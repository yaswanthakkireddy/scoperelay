"""Local SQLite lifecycle; initialization is explicit and ordinary reads never create a DB."""

from pathlib import Path
import sqlite3

from .transaction import connect


class DatabaseError(RuntimeError):
    pass


def open_existing(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise DatabaseError(f"database does not exist: {path}")
    con = connect(path, timeout=5.0)
    try:
        if con.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
            raise DatabaseError("database is not in WAL mode")
        con.execute("PRAGMA synchronous=FULL")
        con.execute("PRAGMA busy_timeout=5000")
        if con.execute("PRAGMA synchronous").fetchone()[0] != 2:
            raise DatabaseError("FULL synchronization unavailable")
        return con
    except BaseException:
        con.close()
        raise


def check_integrity(con: sqlite3.Connection) -> None:
    if con.execute("PRAGMA integrity_check").fetchone() != ("ok",):
        raise DatabaseError("SQLite integrity_check failed")
    if con.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise DatabaseError("SQLite foreign_key_check failed")
