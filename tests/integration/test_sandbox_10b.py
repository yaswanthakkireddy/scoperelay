"""Task 10B: local shared-file state and fail-closed sandbox controls."""

from concurrent.futures import ProcessPoolExecutor
import ast
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from scoperelay.sandbox.app import create_sandbox_app
from scoperelay.sandbox.service import SandboxService
from scoperelay.sandbox.storage import LocalSandboxState
from scoperelay.inference.adapter import ProviderAdapter
from scoperelay.agents.contracts import agent_b_response_schema


BASE = "http://127.0.0.1:8770"
ORIGIN = {"Origin": BASE}


def _other_process_run(root: str, token: str, csrf: str, key: str) -> tuple[int, dict]:
    """Separate Python process, separate API app, same local SQLite state file."""
    with TestClient(
        create_sandbox_app(Path(root), enabled=True, daily_turn_budget=4), base_url=BASE
    ) as web:
        web.cookies.set("sr_sandbox", token)
        response = web.post(
            "/api/sandbox/runs",
            headers={**ORIGIN, "X-ScopeRelay-CSRF": csrf},
            json={"mode": "GUIDED", "scenario_id": "SC-01", "idempotency_key": key},
        )
        return response.status_code, response.json()


def _other_process_read(root: str, token: str, run_id: str) -> int:
    with TestClient(create_sandbox_app(Path(root), enabled=True), base_url=BASE) as web:
        web.cookies.set("sr_sandbox", token)
        return web.get(f"/api/sandbox/runs/{run_id}/evidence").status_code


def _session(web: TestClient) -> str:
    response = web.post("/api/sandbox/session", headers=ORIGIN)
    assert response.status_code == 200
    return response.json()["csrf"]


def test_two_api_processes_share_atomic_global_budget(tmp_path):
    root = tmp_path / "sandbox"
    with TestClient(
        create_sandbox_app(root, enabled=True, daily_turn_budget=4), base_url=BASE
    ) as web:
        csrf = _session(web)
        token = web.cookies.get("sr_sandbox")
        with ProcessPoolExecutor(max_workers=2) as pool:
            first = pool.submit(
                _other_process_run, str(root), token, csrf, "separate-worker-0001"
            )
            second = pool.submit(
                _other_process_run, str(root), token, csrf, "separate-worker-0002"
            )
            results = [first.result(timeout=35), second.result(timeout=35)]
        assert sorted(code for code, _ in results) == [201, 429]
        assert (
            next(body for code, body in results if code == 429)["code"]
            == "OFFLINE_CAPACITY_EXHAUSTED"
        )
        assert (
            web.get("/api/sandbox/capacity").json()["offline_turn_slots_remaining"] == 0
        )
        con = sqlite3.connect(root / "sandbox-state.db")
        try:
            assert con.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
            assert (
                con.execute("SELECT reserved_turns FROM day_budget").fetchone()[0] == 4
            )
        finally:
            con.close()


def test_two_api_processes_enforce_evidence_owner(tmp_path):
    root = tmp_path / "sandbox"
    with TestClient(create_sandbox_app(root, enabled=True), base_url=BASE) as alice:
        csrf = _session(alice)
        token = alice.cookies.get("sr_sandbox")
        created = alice.post(
            "/api/sandbox/runs",
            headers={**ORIGIN, "X-ScopeRelay-CSRF": csrf},
            json={
                "mode": "GUIDED",
                "scenario_id": "SC-01",
                "idempotency_key": "alice-run-000001",
            },
        )
        run_id = created.json()["run_id"]
        with TestClient(create_sandbox_app(root, enabled=True), base_url=BASE) as bob:
            _session(bob)
            other_token = bob.cookies.get("sr_sandbox")
        with ProcessPoolExecutor(max_workers=2) as pool:
            own = pool.submit(_other_process_read, str(root), token, run_id)
            other = pool.submit(_other_process_read, str(root), other_token, run_id)
            assert own.result(timeout=35) == 200
            assert other.result(timeout=35) == 404


def test_csrf_refresh_after_browser_reload_without_browser_secret_storage(tmp_path):
    root = tmp_path / "sandbox"
    with TestClient(create_sandbox_app(root, enabled=True), base_url=BASE) as web:
        old = _session(web)
        fresh = web.get("/api/sandbox/session").json()["csrf"]
        assert fresh != old
        data = {
            "mode": "GUIDED",
            "scenario_id": "SC-01",
            "idempotency_key": "refresh-key-0001",
        }
        assert (
            web.post(
                "/api/sandbox/runs",
                headers={**ORIGIN, "X-ScopeRelay-CSRF": old},
                json=data,
            ).status_code
            == 403
        )
        created = web.post(
            "/api/sandbox/runs",
            headers={**ORIGIN, "X-ScopeRelay-CSRF": fresh},
            json=data,
        )
        assert created.status_code == 201
        assert (
            web.get(
                f"/api/sandbox/runs/{created.json()['run_id']}/evidence"
            ).status_code
            == 200
        )
        assert (
            web.get(f"/api/sandbox/runs/{created.json()['run_id']}/evidence").headers[
                "cache-control"
            ]
            == "no-store"
        )


def test_expiry_background_cleanup_and_restart(tmp_path):
    root = tmp_path / "sandbox"
    with TestClient(
        create_sandbox_app(root, enabled=True, session_ttl=60, cleanup_interval=0.05),
        base_url=BASE,
    ) as web:
        csrf = _session(web)
        created = web.post(
            "/api/sandbox/runs",
            headers={**ORIGIN, "X-ScopeRelay-CSRF": csrf},
            json={
                "mode": "GUIDED",
                "scenario_id": "SC-01",
                "idempotency_key": "cleanup-key-0001",
            },
        )
        assert created.status_code == 201
        run_id = created.json()["run_id"]
        path = root / "runs" / f"run-{run_id}.db"
        assert path.exists()
        con = sqlite3.connect(root / "sandbox-state.db", autocommit=True)
        try:
            con.execute("UPDATE runs SET expires_at=0 WHERE run_id=?", (run_id,))
            con.execute("UPDATE sessions SET expires_at=0")
        finally:
            con.close()
        for _ in range(40):
            if not path.exists():
                break
            time.sleep(0.05)
        assert not path.exists()  # cleanup ran without an API request
        assert web.get(f"/api/sandbox/runs/{run_id}").status_code == 401
    with TestClient(create_sandbox_app(root, enabled=True), base_url=BASE) as restarted:
        assert restarted.get("/api/sandbox/capacity").status_code == 200
        assert restarted.get(f"/api/sandbox/runs/{run_id}").status_code == 401


def test_cleanup_failure_fails_closed_and_does_not_drop_metadata(tmp_path, monkeypatch):
    root = tmp_path / "sandbox"
    service = SandboxService(root, enabled=True)
    token, csrf = service.new_session()
    run_id, _ = service.start(
        token,
        csrf,
        scenario_id="SC-01",
        mode="GUIDED",
        ticket_text=None,
        idempotency_key="failure-key-0001",
    )
    state = sqlite3.connect(root / "sandbox-state.db", autocommit=True)
    try:
        state.execute("UPDATE runs SET expires_at=0 WHERE run_id=?", (run_id,))
        state.execute("UPDATE sessions SET expires_at=0")
    finally:
        state.close()
    original = Path.unlink

    def cannot_delete(self, *args, **kwargs):
        if self.name == f"run-{run_id}.db":
            raise OSError("simulated access denied")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", cannot_delete)
    with pytest.raises(OSError):
        service.sweep_expired()
    state = sqlite3.connect(root / "sandbox-state.db")
    try:
        assert (
            state.execute(
                "SELECT count(*) FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            == 1
        )
    finally:
        state.close()


def test_session_creation_global_cap_and_custom_quota(tmp_path, monkeypatch):
    monkeypatch.setattr("scoperelay.sandbox.service.MAX_SESSIONS_PER_DAY", 1)
    root = tmp_path / "sandbox"
    with TestClient(create_sandbox_app(root, enabled=True), base_url=BASE) as web:
        csrf = _session(web)
        assert (
            web.post("/api/sandbox/session", headers=ORIGIN).json()["code"]
            == "SESSION_CAPACITY_EXHAUSTED"
        )
        for index in range(2):
            response = web.post(
                "/api/sandbox/runs",
                headers={**ORIGIN, "X-ScopeRelay-CSRF": csrf},
                json={
                    "mode": "CUSTOM",
                    "scenario_id": "SC-01",
                    "ticket_text": "Please tell me the status of ORD-101.",
                    "idempotency_key": f"custom-quota-{index:04d}",
                },
            )
            assert response.status_code == 201
        response = web.post(
            "/api/sandbox/runs",
            headers={**ORIGIN, "X-ScopeRelay-CSRF": csrf},
            json={
                "mode": "CUSTOM",
                "scenario_id": "SC-01",
                "ticket_text": "Please tell me the status of ORD-101.",
                "idempotency_key": "custom-quota-0002",
            },
        )
        assert (
            response.status_code == 429
            and response.json()["code"] == "CUSTOM_RUN_LIMIT"
        )


def test_storage_outage_is_sanitized_and_fails_closed(tmp_path, monkeypatch):
    root = tmp_path / "sandbox"
    with TestClient(create_sandbox_app(root, enabled=True), base_url=BASE) as web:

        def unavailable(_self):
            raise sqlite3.OperationalError("PRIVATE_DATABASE_PATH_AND_SECRET")

        monkeypatch.setattr(LocalSandboxState, "connect", unavailable)
        response = web.post("/api/sandbox/session", headers=ORIGIN)
        assert response.status_code == 503
        assert response.json() == {"code": "SANDBOX_STORAGE_UNAVAILABLE"}
        assert "PRIVATE_DATABASE_PATH" not in response.text


def test_crashed_worker_retains_uncertain_reservation(tmp_path):
    root = tmp_path / "sandbox"
    with TestClient(
        create_sandbox_app(root, enabled=True, daily_turn_budget=4), base_url=BASE
    ) as web:
        csrf = _session(web)
        token = web.cookies.get("sr_sandbox")
        state = sqlite3.connect(root / "sandbox-state.db", autocommit=True)
        try:
            state.execute(
                "INSERT INTO day_budget VALUES (?,4)",
                (datetime.now(timezone.utc).date().isoformat(),),
            )
            state.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "a" * 32,
                    sha256(token.encode()).hexdigest(),
                    "interrupted-key",
                    "original-request-hash",
                    "SC-01",
                    "GUIDED",
                    "RUNNING",
                    None,
                    None,
                    None,
                    None,
                    "run-" + "a" * 32 + ".db",
                    time.time(),
                    time.time() - 1,
                ),
            )
        finally:
            state.close()
        with TestClient(
            create_sandbox_app(root, enabled=True, daily_turn_budget=4), base_url=BASE
        ) as restarted:
            restarted.cookies.set("sr_sandbox", token)
            assert (
                restarted.get("/api/sandbox/capacity").json()[
                    "offline_turn_slots_remaining"
                ]
                == 0
            )
            response = restarted.post(
                "/api/sandbox/runs",
                headers={**ORIGIN, "X-ScopeRelay-CSRF": csrf},
                json={
                    "mode": "GUIDED",
                    "scenario_id": "SC-01",
                    "idempotency_key": "after-crash-0001",
                },
            )
            assert response.status_code == 429
            assert response.json()["code"] in {
                "OFFLINE_CAPACITY_EXHAUSTED",
                "SANDBOX_BUSY",
            }


def test_sandbox_modules_do_not_import_protected_writer():
    root = Path(__file__).resolve().parents[2] / "src/scoperelay/sandbox"
    imports = []
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports.extend(
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
    assert not any(
        "resource" in name or "approval" in name or "recovery" in name
        for name in imports
    )


def test_groq_b_tool_then_bounded_json_shape_is_offline_only():
    requests = []
    tool = SimpleNamespace(
        type="function",
        id="fake-b-call",
        function=SimpleNamespace(
            name="read_verified_order_status", arguments='{"verification_id":"V-001"}'
        ),
    )

    def create(**options):
        requests.append(options)
        first = len(requests) == 1
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=None if first else '{"status":"UNABLE"}',
                        tool_calls=[tool] if first else [],
                        refusal=None,
                    ),
                    finish_reason="tool_calls" if first else "stop",
                )
            ],
            id="fake-id",
            model="openai/gpt-oss-20b",
            usage=None,
        )

    adapter = object.__new__(ProviderAdapter)
    adapter.provider, adapter.model = "groq", "openai/gpt-oss-20b"
    adapter.max_requests, adapter.request_count, adapter.request_seconds = 4, 0, []
    adapter._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    first = adapter.complete(
        [{"role": "user", "content": "synthetic H2"}],
        tool_name="read_verified_order_status",
        max_tokens=256,
    )
    assert first.tool_calls[0].name == "read_verified_order_status"
    adapter.complete(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "fake-b-call",
                        "type": "function",
                        "function": {
                            "name": "read_verified_order_status",
                            "arguments": '{"verification_id":"V-001"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "fake-b-call",
                "content": "trusted H2 observation",
            },
        ],
        tool_name="read_verified_order_status",
        max_tokens=256,
    )
    assert requests[0]["tools"][0]["function"]["name"] == "read_verified_order_status"
    assert "response_format" not in requests[0]
    assert requests[1]["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "agent_b_proposal_or_nonmaterial",
            "schema": agent_b_response_schema(),
            "strict": False,
        },
    }
    assert "tools" not in requests[1]
    assert all("store" not in item for item in requests)
    assert adapter.request_count == 2
