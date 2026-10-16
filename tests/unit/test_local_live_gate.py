"""Local LIVE plumbing with a fake SDK response. This never invokes a provider."""

from pathlib import Path
import sqlite3
from types import SimpleNamespace

from fastapi.testclient import TestClient

from scoperelay.inference.adapter import ProviderAdapter
from scoperelay.sandbox.app import create_sandbox_app
from scoperelay.sandbox.offline import SandboxOfflineTransport
from scoperelay.storage.database import open_existing


BASE = "http://127.0.0.1:8770"
ORIGIN = {"Origin": BASE}


def _session(web: TestClient) -> dict[str, str]:
    response = web.post("/api/sandbox/session", headers=ORIGIN)
    assert response.status_code == 200
    return {**ORIGIN, "X-ScopeRelay-CSRF": response.json()["csrf"]}


def _submit(web: TestClient, headers: dict[str, str], *, kind="LIVE", scenario="SC-01"):
    return web.post(
        "/api/sandbox/runs",
        headers=headers,
        json={
            "mode": "GUIDED",
            "scenario_id": scenario,
            "ticket_text": None,
            "run_kind": kind,
            "idempotency_key": "local-live-key-0001",
        },
    )


def _no_effects(path: Path) -> None:
    con = open_existing(path)
    try:
        for table in (
            "evaluation_truth",
            "material_actions",
            "escalation_approvals",
            "resource_attempts",
            "queue_effects",
            "journal_events",
        ):
            assert con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    finally:
        con.close()


def test_live_disabled_and_scope_rejected_before_reservation(tmp_path, monkeypatch):
    monkeypatch.setattr(
        ProviderAdapter,
        "__init__",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("provider must not be constructed")
        ),
    )
    with TestClient(
        create_sandbox_app(tmp_path / "state", enabled=True), base_url=BASE
    ) as web:
        headers = _session(web)
        assert _submit(web, headers).json()["code"] == "LIVE_UNAVAILABLE"
        assert (
            web.get("/api/sandbox/capacity").json()["offline_turn_slots_remaining"]
            == 40
        )
        assert web.post("/api/108", headers=ORIGIN).status_code == 404
        assert web.post("/api/109", headers=ORIGIN).status_code == 404
    with TestClient(
        create_sandbox_app(tmp_path / "enabled", enabled=True, local_live_enabled=True),
        base_url=BASE,
    ) as web:
        headers = _session(web)
        assert (
            _submit(web, headers, scenario="SC-02").json()["code"]
            == "LIVE_SYNTHETIC_SC01_ONLY"
        )
        assert (
            web.get("/api/sandbox/capacity").json()["offline_turn_slots_remaining"]
            == 40
        )


def _fake_adapter(monkeypatch, *, fail=False):
    scripted = SandboxOfflineTransport("SC-01")
    actual_requests = []

    def create(**options):
        actual_requests.append(options)
        if fail:
            raise TimeoutError("fake provider timeout")
        messages = options["messages"]
        is_a = "Assigned ticket_id:" in str(messages[:2])
        tool_name = "read_assigned_ticket" if is_a else "read_verified_order_status"
        turn = scripted.complete(
            messages, tool_name=tool_name, max_tokens=options["max_tokens"]
        )
        calls = [
            SimpleNamespace(
                type="function",
                id=call.call_id,
                function=SimpleNamespace(name=call.name, arguments=call.arguments),
            )
            for call in turn.tool_calls
        ]
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=turn.content, tool_calls=calls, refusal=None
                    ),
                    finish_reason="tool_calls" if calls else "stop",
                )
            ],
            id="fake-sdk-response",
            model="fake-sdk-model",
            usage=None,
        )

    def init(self, **kwargs):
        assert kwargs["provider"] == "ollama-local"
        assert kwargs["model"] == "gemma4:e4b"
        assert kwargs["base_url"] == "http://127.0.0.1:11434/v1"
        assert kwargs["max_requests"] == 4
        self.provider, self.model = kwargs["provider"], kwargs["model"]
        self.max_requests, self.request_count, self.request_seconds = 4, 0, []
        self._client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

    monkeypatch.setattr(ProviderAdapter, "__init__", init)
    return actual_requests


def test_mock_sdk_four_turn_a_to_b_and_no_protected_effect(tmp_path, monkeypatch):
    requests = _fake_adapter(monkeypatch)
    root = tmp_path / "state"
    with TestClient(
        create_sandbox_app(root, enabled=True, local_live_enabled=True), base_url=BASE
    ) as web:
        headers = _session(web)
        started = _submit(web, headers)
        assert started.status_code == 201
        run_id = started.json()["run_id"]
        evidence = web.get(f"/api/sandbox/runs/{run_id}/evidence").json()
        assert (
            evidence["mode"],
            evidence["status"],
            evidence["decision"],
            evidence["b_status"],
        ) == ("LIVE", "COMPLETE", "ALLOW", "PROPOSED")
        assert evidence["model_invoked"] is True and evidence["model_requests"] == 4
        assert evidence["protected_effect"] == "DISABLED"
        assert [
            item["tool"]
            for item in evidence["timeline"]
            if item["stage"] == "SCOPED_TOOL"
        ] == ["read_assigned_ticket", "read_verified_order_status"]
        assert len(requests) == 4
        assert _submit(web, headers).json()["created"] is False
        assert len(requests) == 4
    _no_effects(root / "runs" / f"run-{run_id}.db")


def test_mock_provider_failure_keeps_request_count_and_no_effect(tmp_path, monkeypatch):
    requests = _fake_adapter(monkeypatch, fail=True)
    root = tmp_path / "state"
    with TestClient(
        create_sandbox_app(root, enabled=True, local_live_enabled=True), base_url=BASE
    ) as web:
        headers = _session(web)
        started = _submit(web, headers)
        assert started.status_code == 201
        run_id = started.json()["run_id"]
        result = web.get(f"/api/sandbox/runs/{run_id}/evidence").json()
        assert result["mode"] == "LIVE"
        assert result["status"] == "TECHNICAL_ERROR"
        assert result["model_invoked"] is None and result["model_requests"] == 1
        assert result["request_count_note"] == "DISPATCHED_PROCESSING_UNKNOWN"
        assert result["protected_effect"] == "DISABLED"
        assert len(requests) == 1
    _no_effects(root / "runs" / f"run-{run_id}.db")


def test_interrupted_live_request_count_is_not_misreported(tmp_path, monkeypatch):
    _fake_adapter(monkeypatch, fail=True)
    root = tmp_path / "state"
    with TestClient(
        create_sandbox_app(root, enabled=True, local_live_enabled=True), base_url=BASE
    ) as web:
        headers = _session(web)
        run_id = _submit(web, headers).json()["run_id"]
        con = sqlite3.connect(root / "sandbox-state.db", autocommit=True)
        try:
            con.execute(
                "UPDATE runs SET status='INTERRUPTED' WHERE run_id=?", (run_id,)
            )
        finally:
            con.close()
        result = web.get(f"/api/sandbox/runs/{run_id}").json()
        assert result["model_invoked"] is None
        assert result["request_count_note"] == "UNKNOWN_AFTER_INTERRUPTION"
