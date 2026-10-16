from pathlib import Path
import sqlite3
from typing import Iterator
from contextlib import contextmanager


def connect(path: Path, *, timeout: float = 0.2) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=timeout, autocommit=True)
    con.execute("PRAGMA foreign_keys=ON")
    if con.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        con.close()
        raise RuntimeError("foreign keys could not be enabled")
    return con


@contextmanager
def immediate(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
    except BaseException:
        con.execute("ROLLBACK")
        raise
    else:
        con.execute("COMMIT")
