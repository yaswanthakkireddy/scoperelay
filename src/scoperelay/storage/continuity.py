"""Conservative out-of-DB commit-intent witness for local restore detection.

The sidecar is never proof of a commit. A matching intent with absent DB effect
blocks a negative conclusion, including after an older DB snapshot is restored.
Local filesystem administrators and in-place replacement of all provenance are
outside this mechanism's threat boundary.
"""

from pathlib import Path
import json
import os
import sqlite3


class ContinuityError(RuntimeError):
    pass


def _path(database_path: Path) -> Path:
    return database_path.with_name(database_path.name + ".continuity")


def _header(database_path: Path, database_uuid: str) -> dict[str, object]:
    stat = database_path.stat()
    return {
        "type": "HEADER",
        "database_uuid": database_uuid,
        "file_device": stat.st_dev,
        "file_inode": stat.st_ino,
    }


def _load(database_path: Path, database_uuid: str) -> list[dict[str, object]]:
    path = _path(database_path)
    try:
        raw = path.read_bytes()
        if not raw.endswith(b"\n"):
            raise ContinuityError("incomplete continuity record")
        records = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
        if not records or not all(isinstance(r, dict) for r in records):
            raise ContinuityError("continuity record shape invalid")
        if records[0] != _header(database_path, database_uuid):
            raise ContinuityError(
                "continuity header does not identify original DB file"
            )
        if any(
            set(r) != {"type", "action_id", "business_key", "material_sha256"}
            or r.get("type") != "COMMIT_INTENT"
            or any(
                type(r.get(k)) is not str or not r[k]
                for k in ("action_id", "business_key", "material_sha256")
            )
            for r in records[1:]
        ):
            raise ContinuityError("unknown continuity event")
        return records
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise ContinuityError("continuity unavailable or invalid") from exc


def prepare_continuity(
    con: sqlite3.Connection, database_path: Path, database_uuid: str
) -> None:
    """Create only for fresh, effect-free history; never repair a missing sidecar."""
    path = _path(database_path)
    if path.exists():
        _load(database_path, database_uuid)
        return
    if (
        con.execute("SELECT 1 FROM original_actions LIMIT 1").fetchone() is not None
        or con.execute("SELECT 1 FROM queue_effects LIMIT 1").fetchone() is not None
    ):
        raise ContinuityError("existing operation history has no continuity witness")
    try:
        with path.open("xb") as handle:
            handle.write(
                (
                    json.dumps(_header(database_path, database_uuid), sort_keys=True)
                    + "\n"
                ).encode()
            )
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        _load(database_path, database_uuid)


def record_commit_intent(
    database_path: Path,
    database_uuid: str,
    *,
    action_id: str,
    business_key: str,
    material_sha256: str,
) -> None:
    _load(database_path, database_uuid)
    event = {
        "type": "COMMIT_INTENT",
        "action_id": action_id,
        "business_key": business_key,
        "material_sha256": material_sha256,
    }
    with _path(database_path).open("ab") as handle:
        handle.write((json.dumps(event, sort_keys=True) + "\n").encode())
        handle.flush()
        os.fsync(handle.fileno())


def assert_no_commit_intent(
    database_path: Path, database_uuid: str, *, action_id: str, business_key: str
) -> None:
    for event in _load(database_path, database_uuid)[1:]:
        if event["action_id"] == action_id or event["business_key"] == business_key:
            raise ContinuityError(
                "possible committed effect missing from current database"
            )
