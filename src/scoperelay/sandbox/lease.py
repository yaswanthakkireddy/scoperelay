"""OS-held, single-host sandbox process lease for the approved local prototype.

This is not a distributed lock or permission boundary against a filesystem admin.
The CLI holds it for the entire Uvicorn lifetime before recovering stale runs.
"""

from pathlib import Path
import os


class SandboxInstanceBusy(RuntimeError):
    pass


class SingleInstanceLease:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._file = None

    @property
    def held(self) -> bool:
        return self._file is not None

    def __enter__(self):
        self.root.mkdir(parents=True, exist_ok=True)
        handle = (self.root / "sandbox-instance.lock").open("a+b")
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                if handle.read(1) == b"":
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise SandboxInstanceBusy(
                "another local sandbox instance owns this data directory"
            ) from exc
        self._file = handle
        return self

    def __exit__(self, *_):
        if self._file is not None:
            handle = self._file
            self._file = None
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
