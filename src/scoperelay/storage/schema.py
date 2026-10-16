"""Forward-only, checksum-verified migrations with explicit SQL transactions.

executescript() is deliberately excluded: it can commit a pending transaction.
"""

from datetime import datetime, timezone
from hashlib import sha256
from importlib.resources import files
from pathlib import Path
import sqlite3
from uuid import uuid4

from .database import DatabaseError, check_integrity, open_existing
from .transaction import connect, immediate


MIGRATIONS = (
    "001_initial.sql",
    "002_frozen_material.sql",
    "003_original_action_lifecycle.sql",
    "004_quote_binding.sql",
)
SCHEMA_VERSION = len(MIGRATIONS)
MIGRATION_NAME = MIGRATIONS[0]  # compatibility for the initial-schema test fixture


def _statements(source: str) -> list[str]:
    statements: list[str] = []
    buffer = ""
    for line in source.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            if any(
                part.strip() and not part.lstrip().startswith("--")
                for part in buffer.splitlines()
            ):
                statements.append(buffer.strip())
            buffer = ""
    if any(
        part.strip() and not part.lstrip().startswith("--")
        for part in buffer.splitlines()
    ):
        raise DatabaseError("incomplete migration statement")
    return statements


def _migration_source(name: str = MIGRATION_NAME) -> str:
    return (
        files("scoperelay.storage.migrations")
        .joinpath(name)
        .read_text(encoding="utf-8")
    )


def _verify(con: sqlite3.Connection) -> None:
    version = con.execute("PRAGMA user_version").fetchone()[0]
    if version != SCHEMA_VERSION:
        raise DatabaseError(f"unsupported schema version: {version}")
    ledger = con.execute("SELECT version,name,sha256 FROM schema_migrations").fetchall()
    expected = [
        (i, name, sha256(_migration_source(name).encode("utf-8")).hexdigest())
        for i, name in enumerate(MIGRATIONS, 1)
    ]
    if ledger != expected:
        raise DatabaseError("migration ledger or checksum mismatch")
    expected_tables = {
        "organizations",
        "principals",
        "operator_sessions",
        "org_policy",
        "manager_grants",
        "customers",
        "accounts",
        "contacts",
        "orders",
        "tickets",
        "ticket_sources",
        "episodes",
        "agent_invocations",
        "tool_events",
        "candidate_requests",
        "verifications",
        "workflow_decisions",
        "handoffs",
        "agent_b_proposals",
        "clarification_drafts",
        "material_actions",
        "escalation_approvals",
        "resource_attempts",
        "queue_effects",
        "journal_events",
        "scenarios",
        "evaluation_truth",
        "evaluation_results",
        "schema_migrations",
        "database_identity",
        "original_actions",
        "reconciliation_events",
        "episode_candidate_protocols",
        "candidate_quote_bindings",
    }
    actual = {
        row[0]
        for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if not expected_tables <= actual:
        raise DatabaseError(
            f"missing schema tables: {sorted(expected_tables - actual)}"
        )
    if con.execute("SELECT count(*) FROM database_identity").fetchone() != (1,):
        raise DatabaseError("database identity missing or ambiguous")
    check_integrity(con)


def initialize(path: Path, *, source_override: str | None = None) -> None:
    """Create or verify the one designated local DB; never replace existing data."""
    if not path.parent.is_dir():
        raise DatabaseError(f"database parent does not exist: {path.parent}")
    exists = path.exists()
    if exists and not path.is_file():
        raise DatabaseError("database path is not a regular file")
    if exists:
        con = open_existing(path)
        try:
            version = con.execute("PRAGMA user_version").fetchone()[0]
            if version not in range(1, SCHEMA_VERSION + 1):
                raise DatabaseError(f"unsupported schema version: {version}")
            expected = [
                (i, name, sha256(_migration_source(name).encode("utf-8")).hexdigest())
                for i, name in enumerate(MIGRATIONS[:version], 1)
            ]
            if (
                con.execute(
                    "SELECT version,name,sha256 FROM schema_migrations"
                ).fetchall()
                != expected
            ):
                raise DatabaseError("migration ledger or checksum mismatch")
            if version < SCHEMA_VERSION:
                check_integrity(con)
                for next_version in range(version + 1, SCHEMA_VERSION + 1):
                    name = MIGRATIONS[next_version - 1]
                    source = _migration_source(name)
                    with immediate(con):
                        for statement in _statements(source):
                            con.execute(statement)
                        if next_version == 2:
                            con.execute(
                                "INSERT INTO database_identity VALUES (1,?,?)",
                                (
                                    uuid4().hex,
                                    datetime.now(timezone.utc)
                                    .isoformat(timespec="microseconds")
                                    .replace("+00:00", "Z"),
                                ),
                            )
                        con.execute(
                            "INSERT INTO schema_migrations VALUES (?,?,?,?)",
                            (
                                next_version,
                                name,
                                sha256(source.encode("utf-8")).hexdigest(),
                                datetime.now(timezone.utc)
                                .isoformat(timespec="microseconds")
                                .replace("+00:00", "Z"),
                            ),
                        )
                        con.execute(f"PRAGMA user_version={next_version}")
            _verify(con)
        finally:
            con.close()
        return
    con = connect(path, timeout=5.0)
    try:
        if con.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() != "wal":
            raise DatabaseError("WAL mode unavailable")
        con.execute("PRAGMA synchronous=FULL")
        con.execute("PRAGMA busy_timeout=5000")
        if con.execute("PRAGMA synchronous").fetchone()[0] != 2:
            raise DatabaseError("FULL synchronization unavailable")
        with immediate(con):
            con.execute(
                "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, sha256 TEXT NOT NULL, applied_at TEXT NOT NULL)"
            )
            source = _migration_source()
            for statement in _statements(
                source_override if source_override is not None else source
            ):
                con.execute(statement)
            con.execute(
                "INSERT INTO schema_migrations VALUES (?,?,?,?)",
                (
                    1,
                    MIGRATION_NAME,
                    sha256(source.encode("utf-8")).hexdigest(),
                    datetime.now(timezone.utc)
                    .isoformat(timespec="microseconds")
                    .replace("+00:00", "Z"),
                ),
            )
            for statement in _statements(_migration_source(MIGRATIONS[1])):
                con.execute(statement)
            con.execute(
                "INSERT INTO database_identity VALUES (1,?,?)",
                (
                    uuid4().hex,
                    datetime.now(timezone.utc)
                    .isoformat(timespec="microseconds")
                    .replace("+00:00", "Z"),
                ),
            )
            con.execute(
                "INSERT INTO schema_migrations VALUES (?,?,?,?)",
                (
                    2,
                    MIGRATIONS[1],
                    sha256(
                        _migration_source(MIGRATIONS[1]).encode("utf-8")
                    ).hexdigest(),
                    datetime.now(timezone.utc)
                    .isoformat(timespec="microseconds")
                    .replace("+00:00", "Z"),
                ),
            )
            for next_version in range(3, SCHEMA_VERSION + 1):
                name = MIGRATIONS[next_version - 1]
                source = _migration_source(name)
                for statement in _statements(source):
                    con.execute(statement)
                con.execute(
                    "INSERT INTO schema_migrations VALUES (?,?,?,?)",
                    (
                        next_version,
                        name,
                        sha256(source.encode("utf-8")).hexdigest(),
                        datetime.now(timezone.utc)
                        .isoformat(timespec="microseconds")
                        .replace("+00:00", "Z"),
                    ),
                )
            con.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        _verify(con)
    finally:
        con.close()
