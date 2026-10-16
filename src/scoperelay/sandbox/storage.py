"""Disposable sandbox state store; never opens the protected application database.

This SQLite implementation requires one persistent local filesystem. It is not a
distributed/serverless state service. All quota mutations use BEGIN IMMEDIATE.
"""

from pathlib import Path
import sqlite3

from scoperelay.storage.transaction import connect


class LocalSandboxState:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def connect(self) -> sqlite3.Connection:
        con = connect(self.root / "sandbox-state.db", timeout=5)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=FULL")
        con.row_factory = sqlite3.Row
        return con

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "runs").mkdir(exist_ok=True)
        con = self.connect()
        try:
            con.execute(
                "CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, "
                "csrf_hash TEXT NOT NULL, expires_at REAL NOT NULL)"
            )
            con.execute(
                "CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, "
                "token_hash TEXT NOT NULL REFERENCES sessions(token_hash), "
                "idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL, "
                "scenario_id TEXT NOT NULL, mode TEXT NOT NULL, status TEXT NOT NULL, "
                "failure_code TEXT, decision TEXT, b_status TEXT, execution_state TEXT, "
                "db_name TEXT NOT NULL, created_at REAL NOT NULL, "
                "expires_at REAL NOT NULL, UNIQUE(token_hash,idempotency_key))"
            )
            con.execute(
                "CREATE TABLE IF NOT EXISTS run_inference ("
                "run_id TEXT PRIMARY KEY REFERENCES runs(run_id) ON DELETE CASCADE, "
                "run_kind TEXT NOT NULL CHECK(run_kind='LIVE'), "
                "model_requests INTEGER NOT NULL DEFAULT 0 CHECK(model_requests BETWEEN 0 AND 4))"
            )
            con.execute(
                "CREATE TABLE IF NOT EXISTS day_budget (day TEXT PRIMARY KEY, "
                "reserved_turns INTEGER NOT NULL CHECK(reserved_turns>=0))"
            )
            con.execute(
                "CREATE TABLE IF NOT EXISTS day_sessions (day TEXT PRIMARY KEY, "
                "created_count INTEGER NOT NULL CHECK(created_count>=0))"
            )
            con.execute(
                "CREATE TABLE IF NOT EXISTS client_windows (client_key TEXT NOT NULL, "
                "kind TEXT NOT NULL, minute INTEGER NOT NULL, count INTEGER NOT NULL "
                "CHECK(count>=1), PRIMARY KEY(client_key,kind,minute))"
            )
            con.execute(
                "CREATE TABLE IF NOT EXISTS control (id INTEGER PRIMARY KEY CHECK(id=1), "
                "accept_new_runs INTEGER NOT NULL CHECK(accept_new_runs IN (0,1)))"
            )
            con.execute("INSERT OR IGNORE INTO control VALUES (1,1)")
        finally:
            con.close()

    @staticmethod
    def charge_client(
        con: sqlite3.Connection, client_key: str, kind: str, minute: int, limit: int
    ) -> bool:
        """Caller holds BEGIN IMMEDIATE; false leaves the window unchanged."""
        row = con.execute(
            "SELECT count FROM client_windows WHERE client_key=? AND kind=? AND minute=?",
            (client_key, kind, minute),
        ).fetchone()
        if row and row[0] >= limit:
            return False
        con.execute(
            "INSERT INTO client_windows VALUES (?,?,?,1) "
            "ON CONFLICT(client_key,kind,minute) DO UPDATE SET count=count+1",
            (client_key, kind, minute),
        )
        return True
