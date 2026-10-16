"""OS-backed exclusive owner for one existing local SQLite database.

The .owner file is a rendezvous location, never evidence of a live owner by
itself. All protected admissions and terminal reconciliations take this lock.
"""

from contextlib import contextmanager
from pathlib import Path
import os
import sqlite3
import threading
import time
from typing import Iterator


class WriterBusy(RuntimeError):
    pass


class OwnerInvalid(RuntimeError):
    pass


class HistoryQuarantined(OwnerInvalid):
    pass


_GUARD = threading.Lock()
_LOCAL: dict[Path, threading.Lock] = {}
_ACTIVE: dict[Path, "ApplicationWriter"] = {}
_ISSUE_KEY = object()


def quarantine_path(database_path: Path) -> Path:
    return database_path.with_name(database_path.name + ".quarantine")


def _write_quarantine_marker(path: Path, reason: str) -> None:
    marker = quarantine_path(path)
    if marker.exists():
        return
    try:
        with marker.open("xb") as handle:
            handle.write((reason + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        pass


def _history_issue(
    path: Path, con: sqlite3.Connection, database_uuid: str
) -> str | None:
    """Detect surviving contradictory provenance; not coherent all-file rollback."""
    from .continuity import ContinuityError, _load, _path

    history = (
        con.execute("SELECT 1 FROM original_actions LIMIT 1").fetchone() is not None
        or con.execute("SELECT 1 FROM queue_effects LIMIT 1").fetchone() is not None
    )
    if not _path(path).exists():
        return "MISSING_CONTINUITY_WITH_HISTORY" if history else None
    try:
        records = _load(path, database_uuid)
    except ContinuityError:
        return "INVALID_CONTINUITY"
    for event in records[1:]:
        rows = con.execute(
            "SELECT action_id,business_key,material_sha256 FROM queue_effects "
            "WHERE action_id=? OR business_key=?",
            (event["action_id"], event["business_key"]),
        ).fetchall()
        if len(rows) != 1 or tuple(rows[0]) != (
            event["action_id"],
            event["business_key"],
            event["material_sha256"],
        ):
            return "UNMATCHED_COMMIT_INTENT"
    intents = {
        (event["action_id"], event["business_key"], event["material_sha256"])
        for event in records[1:]
    }
    for row in con.execute(
        "SELECT action_id,business_key,material_sha256,effect_id FROM queue_effects"
    ):
        if tuple(row[:3]) not in intents:
            return "ACCEPTED_EFFECT_WITHOUT_INTENT"
        if (
            con.execute(
                "SELECT count(*) FROM resource_attempts a JOIN journal_events j "
                "ON j.attempt_id=a.attempt_id WHERE a.action_id=? AND "
                "a.business_key=? AND a.effect_id=? AND a.outcome='ACCEPTED_COMMITTED' "
                "AND j.effect_id=a.effect_id AND j.event_type='ACCEPTED_COMMITTED'",
                (row[0], row[1], row[3]),
            ).fetchone()[0]
            != 1
        ):
            return "ACCEPTED_EFFECT_EVIDENCE_INCONSISTENT"
    return None


def _local_lock(path: Path) -> threading.Lock:
    with _GUARD:
        return _LOCAL.setdefault(path, threading.Lock())


@contextmanager
def writer_owner(database_path: Path, *, timeout: float = 0.0) -> Iterator[None]:
    """Nonblocking interprocess + in-process exclusion, released by OS on crash."""
    resolved = database_path.resolve(strict=True)
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    local = _local_lock(resolved)
    if not local.acquire(timeout=timeout):
        raise WriterBusy("local protected writer already active")
    handle = None
    locked = False
    try:
        rendezvous = resolved.with_name(resolved.name + ".owner")
        handle = open(rendezvous, "a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        deadline = time.monotonic() + timeout
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise WriterBusy(
                        "another process owns the protected writer"
                    ) from exc
                time.sleep(min(0.01, max(0, deadline - time.monotonic())))
        locked = True
        yield
    finally:
        if handle is not None:
            if locked:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
        local.release()


class ApplicationWriter:
    """Startup-held OS owner plus local admission/recovery/shutdown coordination.

    Call ``start`` once per application instance and ``close`` after draining.
    A failed close timeout leaves the OS lock held in STOPPING state.
    """

    def __init__(
        self,
        path: Path,
        os_guard: object,
        identity: tuple[int, int, str],
        issue_key: object,
    ):
        if issue_key is not _ISSUE_KEY:
            raise OwnerInvalid("application owner must be started by the lock factory")
        self.path = path
        self._os_guard = os_guard
        self._identity = identity
        self._pid = os.getpid()
        self._condition = threading.Condition()
        self._thread = threading.local()
        self._state = "ACTIVE"
        self._active = 0
        self._recovery = False

    @classmethod
    def start(cls, database_path: Path, *, timeout: float = 0.0) -> "ApplicationWriter":
        path = database_path.resolve(strict=True)
        guard = writer_owner(path, timeout=timeout)
        guard.__enter__()
        try:
            stat = path.stat()
            con = sqlite3.connect(
                path.as_uri() + "?mode=rw", uri=True, timeout=0.2, autocommit=True
            )
            try:
                row = con.execute(
                    "SELECT identity_uuid FROM database_identity WHERE singleton=1"
                ).fetchone()
                if row is None:
                    raise OwnerInvalid("database identity missing")
                issue = _history_issue(path, con, row[0])
            finally:
                con.close()
            owner = cls(path, guard, (stat.st_dev, stat.st_ino, row[0]), _ISSUE_KEY)
            if issue is not None:
                _write_quarantine_marker(path, issue)
            if quarantine_path(path).exists() or issue is not None:
                owner._state = "QUARANTINED"
            with _GUARD:
                _ACTIVE[path] = owner
            return owner
        except BaseException:
            guard.__exit__(None, None, None)
            raise

    def _verify(self) -> None:
        if os.getpid() != self._pid or self._os_guard is None:
            raise OwnerInvalid("owner process or OS handle invalid")
        with _GUARD:
            if _ACTIVE.get(self.path) is not self:
                raise OwnerInvalid("application owner is not registered")
        try:
            stat = self.path.stat()
            if (stat.st_dev, stat.st_ino) != self._identity[:2]:
                raise OwnerInvalid("original database file changed")
            con = sqlite3.connect(
                self.path.as_uri() + "?mode=rw", uri=True, timeout=0.2, autocommit=True
            )
            try:
                row = con.execute(
                    "SELECT identity_uuid FROM database_identity WHERE singleton=1"
                ).fetchone()
            finally:
                con.close()
            if row is None or row[0] != self._identity[2]:
                raise OwnerInvalid("original database identity changed")
        except (OSError, sqlite3.Error) as exc:
            raise OwnerInvalid("original database unavailable") from exc

    @contextmanager
    def operation(self, *, recovery: bool = False) -> Iterator[None]:
        with self._condition:
            if self._state == "QUARANTINED" and not recovery:
                raise HistoryQuarantined("protected admission is quarantined")
            if (
                self._state
                not in (("ACTIVE", "QUARANTINED") if recovery else ("ACTIVE",))
                or self._os_guard is None
            ):
                raise OwnerInvalid("application writer is not active")
            if quarantine_path(self.path).exists() and not recovery:
                self._state = "QUARANTINED"
                raise HistoryQuarantined("protected admission is quarantined")
            self._verify()
            if recovery:
                if self._active or self._recovery:
                    raise WriterBusy("local admission or recovery still active")
                self._recovery = True
            else:
                if self._recovery:
                    raise WriterBusy("recovery excludes new admission")
                self._active += 1
        try:
            self._thread.mode = "recovery" if recovery else "admission"
            yield
        finally:
            self._thread.mode = None
            with self._condition:
                if recovery:
                    self._recovery = False
                else:
                    self._active -= 1
                self._condition.notify_all()

    def quarantine(self, reason: str) -> None:
        """Persist a one-way local hold; no implicit unquarantine or activation."""
        if not reason or "\n" in reason:
            raise ValueError("invalid quarantine reason")
        with self._condition:
            if (
                self._state not in ("ACTIVE", "QUARANTINED")
                or self._active
                or self._recovery
            ):
                raise WriterBusy("cannot quarantine while protected work is in flight")
            self._verify()
            _write_quarantine_marker(self.path, reason)
            self._state = "QUARANTINED"

    def assert_operation(self, *, recovery: bool = False) -> None:
        self._verify()
        if getattr(self._thread, "mode", None) != (
            "recovery" if recovery else "admission"
        ):
            raise OwnerInvalid("protected operation lacks active owner registration")

    def verify_connection(self, con: sqlite3.Connection) -> None:
        """Bind a live SQLite transaction to the startup-selected DB incarnation."""
        self._verify()
        row = con.execute(
            "SELECT identity_uuid FROM database_identity WHERE singleton=1"
        ).fetchone()
        if row is None or row[0] != self._identity[2]:
            raise OwnerInvalid("transaction uses a different database identity")

    def close(self, *, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        with self._condition:
            if self._state == "CLOSED":
                return
            if os.getpid() != self._pid:
                raise OwnerInvalid("foreign process cannot close owner")
            self._state = "STOPPING"
            while self._active or self._recovery:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise WriterBusy("shutdown waiting for in-flight protected work")
                self._condition.wait(remaining)
            guard = self._os_guard
            self._os_guard = None
            self._state = "CLOSED"
        with _GUARD:
            if _ACTIVE.get(self.path) is self:
                del _ACTIVE[self.path]
        guard.__exit__(None, None, None)

    def __enter__(self) -> "ApplicationWriter":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
