import sqlite3
import subprocess
import sys

import pytest

from scoperelay.storage.transaction import connect, immediate


def schema(con):
    con.executescript("""
    CREATE TABLE records (id INTEGER PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE children (id INTEGER PRIMARY KEY, record_id INTEGER NOT NULL REFERENCES records(id));
    CREATE UNIQUE INDEX records_value ON records(value);
    """)


def test_explicit_commit_and_reopen(tmp_path):
    path = tmp_path / "synthetic.db"
    con = connect(path)
    schema(con)
    with immediate(con):
        con.execute("INSERT INTO records(value) VALUES (?)", ("committed",))
    con.close()
    reopened = connect(path)
    assert reopened.execute("SELECT value FROM records").fetchone() == ("committed",)
    reopened.close()


def test_explicit_rollback_has_no_partial_rows(tmp_path):
    con = connect(tmp_path / "rollback.db")
    schema(con)
    with pytest.raises(RuntimeError):
        with immediate(con):
            con.execute("INSERT INTO records(value) VALUES (?)", ("rolled-back",))
            raise RuntimeError("injected failure")
    assert con.execute("SELECT COUNT(*) FROM records").fetchone() == (0,)
    con.close()


def test_foreign_key_and_unique_constraints(tmp_path):
    con = connect(tmp_path / "constraints.db")
    schema(con)
    with immediate(con):
        con.execute("INSERT INTO records(value) VALUES ('one')")
    with pytest.raises(sqlite3.IntegrityError):
        with immediate(con):
            con.execute("INSERT INTO records(value) VALUES ('one')")
    with pytest.raises(sqlite3.IntegrityError):
        with immediate(con):
            con.execute("INSERT INTO children(record_id) VALUES (999)")
    con.close()


def test_two_writers_lock_and_then_commit(tmp_path):
    path = tmp_path / "writers.db"
    first = connect(path)
    schema(first)
    second = connect(path)
    first.execute("BEGIN IMMEDIATE")
    first.execute("INSERT INTO records(value) VALUES ('first')")
    with pytest.raises(sqlite3.OperationalError):
        second.execute("BEGIN IMMEDIATE")
    first.execute("COMMIT")
    second.execute("BEGIN IMMEDIATE")
    second.execute("INSERT INTO records(value) VALUES ('second')")
    second.execute("COMMIT")
    assert second.execute("SELECT COUNT(*) FROM records").fetchone() == (2,)
    first.close()
    second.close()


def test_rollback_after_child_failure_preserves_parent_only(tmp_path):
    con = connect(tmp_path / "atomic.db")
    schema(con)
    with pytest.raises(sqlite3.IntegrityError):
        with immediate(con):
            con.execute("INSERT INTO records(value) VALUES ('parent')")
            con.execute("INSERT INTO children(record_id) VALUES (404)")
    assert con.execute("SELECT COUNT(*) FROM records").fetchone() == (0,)
    con.close()


def test_process_crash_before_commit_does_not_persist(tmp_path):
    path = tmp_path / "crash.db"
    con = connect(path)
    schema(con)
    con.close()
    code = (
        "import sqlite3, os; "
        f"c=sqlite3.connect(r'{path}', autocommit=True); "
        "c.execute('BEGIN IMMEDIATE'); "
        "c.execute(\"INSERT INTO records(value) VALUES ('crashed')\"); "
        "os._exit(17)"
    )
    result = subprocess.run([sys.executable, "-c", code], check=False)
    assert result.returncode == 17
    reopened = connect(path)
    assert reopened.execute("SELECT COUNT(*) FROM records").fetchone() == (0,)
    reopened.close()


def test_process_commit_survives_close(tmp_path):
    path = tmp_path / "durable.db"
    con = connect(path)
    schema(con)
    con.close()
    code = (
        "import sqlite3; "
        f"c=sqlite3.connect(r'{path}', autocommit=True); "
        "c.execute('BEGIN IMMEDIATE'); "
        "c.execute(\"INSERT INTO records(value) VALUES ('durable')\"); "
        "c.execute('COMMIT'); c.close()"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
    reopened = connect(path)
    assert reopened.execute("SELECT value FROM records").fetchone() == ("durable",)
    reopened.close()
