"""Exclusive local startup, interrupted runs and bounded request transport."""

from datetime import datetime, timezone
from hashlib import sha256
import sqlite3
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

from fastapi.testclient import TestClient
import pytest

from scoperelay.sandbox.app import create_sandbox_app
from scoperelay.sandbox.lease import SandboxInstanceBusy, SingleInstanceLease
from scoperelay.sandbox.service import SandboxService
from scoperelay.inference.adapter import LiveInferenceBlocked, ProviderAdapter


BASE = "http://127.0.0.1:8770"


def test_exclusive_instance_lease_and_interrupted_retains_reservation(tmp_path):
    root = tmp_path / "sandbox"
    service = SandboxService(root, enabled=True, daily_turn_budget=4)
    token, csrf = service.new_session()
    con = sqlite3.connect(root / "sandbox-state.db", autocommit=True)
    try:
        con.execute(
            "INSERT INTO day_budget VALUES (?,4)",
            (datetime.now(timezone.utc).date().isoformat(),),
        )
        con.execute(
            "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "b" * 32,
                sha256(token.encode()).hexdigest(),
                "crash-key-0001",
                "hash",
                "SC-01",
                "GUIDED",
                "RUNNING",
                None,
                None,
                None,
                None,
                "run-" + "b" * 32 + ".db",
                time.time(),
                time.time() + 60,
            ),
        )
    finally:
        con.close()
    with pytest.raises(ValueError):
        service.recover_interrupted_runs(SingleInstanceLease(root))
    with SingleInstanceLease(root) as lease:
        with pytest.raises(SandboxInstanceBusy):
            with SingleInstanceLease(root):
                pass
        assert service.recover_interrupted_runs(lease) == 1
        assert service.recover_interrupted_runs(lease) == 0
    state = service.status(token, "b" * 32)
    assert state["status"] == "INTERRUPTED"
    assert state["failure_code"] == "WORKER_INTERRUPTED"
    assert service.capacity()["offline_turn_slots_remaining"] == 0
    with pytest.raises(Exception) as rejected:
        service.start(
            token,
            csrf,
            scenario_id="SC-01",
            mode="GUIDED",
            ticket_text=None,
            idempotency_key="later-run-0001",
        )
    assert str(rejected.value) == "OFFLINE_CAPACITY_EXHAUSTED"


def test_expired_interrupted_run_deleted_after_restart_recovery(tmp_path):
    root = tmp_path / "sandbox"
    service = SandboxService(root, enabled=True)
    token, _ = service.new_session()
    con = sqlite3.connect(root / "sandbox-state.db", autocommit=True)
    try:
        con.execute(
            "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "c" * 32,
                sha256(token.encode()).hexdigest(),
                "crash-key-0002",
                "hash",
                "SC-01",
                "GUIDED",
                "RUNNING",
                None,
                None,
                None,
                None,
                "run-" + "c" * 32 + ".db",
                time.time() - 100,
                0,
            ),
        )
        con.execute("UPDATE sessions SET expires_at=0")
    finally:
        con.close()
    path = root / "runs" / ("run-" + "c" * 32 + ".db")
    path.write_bytes(b"synthetic disposable data")
    with SingleInstanceLease(root) as lease:
        restarted = SandboxService(root, enabled=True)
        assert restarted.recover_interrupted_runs(lease) == 1
        assert restarted.sweep_expired() == 1
    assert not path.exists()
    con = sqlite3.connect(root / "sandbox-state.db")
    try:
        assert con.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
    finally:
        con.close()


def test_chunked_body_limit_and_forwarded_identity_not_authority(tmp_path):
    with TestClient(
        create_sandbox_app(tmp_path / "sandbox", enabled=True), base_url=BASE
    ) as web:
        response = web.post(
            "/api/sandbox/session",
            headers={"Origin": BASE, "X-Forwarded-For": "198.51.100.200"},
        )
        assert response.status_code == 200
        csrf = response.json()["csrf"]
        token = web.cookies.get("sr_sandbox")

        def big_body():
            yield b"{" + b"a" * 3000
            yield b"b" * 3000 + b"}"

        response = web.post(
            "/api/sandbox/runs",
            headers={
                "Origin": BASE,
                "X-ScopeRelay-CSRF": csrf,
                "Content-Type": "application/json",
                "X-Forwarded-For": "198.51.100.200",
            },
            content=big_body(),
        )
        assert response.status_code == 413
        assert web.cookies.get("sr_sandbox") == token
        con = sqlite3.connect(tmp_path / "sandbox" / "sandbox-state.db")
        try:
            assert con.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
        finally:
            con.close()


@pytest.mark.parametrize(
    "base_url,model",
    [
        ("https://evil.invalid/openai/v1", "openai/gpt-oss-20b"),
        ("https://api.groq.com/openai/v1", "other-model"),
    ],
)
def test_groq_trusted_endpoint_model_allowlist(base_url, model):
    with pytest.raises(LiveInferenceBlocked):
        ProviderAdapter(
            provider="groq",
            model=model,
            base_url=base_url,
            api_key="fake-not-used",
            authorized=True,
            max_requests=4,
        )


def test_real_cli_rejects_second_instance_then_restarts(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    root = tmp_path / "sandbox"
    command = [
        sys.executable,
        "-m",
        "scoperelay.sandbox",
        "--enable-local-sandbox",
        "--data-dir",
        str(root),
        "--port",
        str(port),
    ]

    def started(process):
        for _ in range(100):
            if process.poll() is not None:
                return False
            try:
                with urlopen(
                    f"http://127.0.0.1:{port}/health", timeout=0.2
                ) as response:
                    return (
                        response.status == 200
                        and b'"live":"DISABLED"' in response.read()
                    )
            except OSError:
                time.sleep(0.05)
        return False

    first = subprocess.Popen(
        command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        assert started(first)
        denied = subprocess.run(
            command[:-1] + [str(port + 1)], capture_output=True, text=True, timeout=10
        )
        assert denied.returncode != 0
        assert "another local sandbox instance" in denied.stderr
    finally:
        first.terminate()
        first.wait(timeout=10)
    second = subprocess.Popen(
        command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        assert started(second)
    finally:
        second.terminate()
        second.wait(timeout=10)
