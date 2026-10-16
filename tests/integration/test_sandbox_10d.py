"""Restricted-beta controls remain local-only and cannot mint protected effects."""

from hashlib import sha256
import sqlite3

from fastapi.testclient import TestClient

from scoperelay.sandbox.app import create_sandbox_app


BASE = "http://127.0.0.1:8770"
INVITE = "synthetic-test-invite"


def test_invite_gate_and_client_session_limit_ignore_forwarded_headers(tmp_path):
    root = tmp_path / "sandbox"
    app = create_sandbox_app(
        root, enabled=True, invite_digest=sha256(INVITE.encode()).hexdigest()
    )
    with TestClient(app, base_url=BASE) as web:
        assert web.get("/api/sandbox/capacity").json()["invite_required"] is True
        assert web.post("/api/sandbox/session", headers={"Origin": BASE}).json() == {
            "code": "INVITE_REQUIRED"
        }
        assert (
            web.post(
                "/api/sandbox/session",
                headers={"Origin": BASE, "X-ScopeRelay-Invite": "wrong"},
            ).status_code
            == 403
        )
        for index in range(3):
            response = web.post(
                "/api/sandbox/session",
                headers={
                    "Origin": BASE,
                    "X-ScopeRelay-Invite": INVITE,
                    "X-Forwarded-For": f"198.51.100.{index}",
                },
            )
            assert response.status_code == 200
        fourth = web.post(
            "/api/sandbox/session",
            headers={
                "Origin": BASE,
                "X-ScopeRelay-Invite": INVITE,
                "X-Forwarded-For": "198.51.100.99",
            },
        )
        assert fourth.status_code == 429
        assert fourth.json()["code"] == "CLIENT_SESSION_RATE_LIMIT"
    with sqlite3.connect(root / "sandbox-state.db") as con:
        assert con.execute("SELECT count(*) FROM sessions").fetchone()[0] == 3
        assert con.execute("SELECT created_count FROM day_sessions").fetchone()[0] == 3


def test_run_rate_limit_is_atomic_and_does_not_charge_budget(tmp_path):
    root = tmp_path / "sandbox"
    app = create_sandbox_app(
        root, enabled=True, invite_digest=sha256(INVITE.encode()).hexdigest()
    )
    with TestClient(app, base_url=BASE) as web:
        session = web.post(
            "/api/sandbox/session",
            headers={"Origin": BASE, "X-ScopeRelay-Invite": INVITE},
        )
        assert session.status_code == 200
        csrf = session.json()["csrf"]
        run_ids = []
        for index in range(4):
            response = web.post(
                "/api/sandbox/runs",
                headers={
                    "Origin": BASE,
                    "X-ScopeRelay-CSRF": csrf,
                    "Content-Type": "application/json",
                    "X-Forwarded-For": f"203.0.113.{index}",
                },
                json={
                    "mode": "GUIDED",
                    "scenario_id": "SC-01",
                    "idempotency_key": f"beta-run-{index:04d}",
                },
            )
            if index < 3:
                assert response.status_code == 201, response.text
                run_ids.append(response.json()["run_id"])
            else:
                assert response.status_code == 429
                assert response.json()["code"] == "CLIENT_RUN_RATE_LIMIT"
        assert (
            web.get("/api/sandbox/capacity").json()["offline_turn_slots_remaining"]
            == 28
        )
        assert web.get(f"/api/sandbox/runs/{run_ids[0]}/evidence").status_code == 200
    with sqlite3.connect(root / "sandbox-state.db") as con:
        assert con.execute("SELECT count(*) FROM runs").fetchone()[0] == 3
        assert (
            con.execute(
                "SELECT count(*) FROM client_windows WHERE kind='run'"
            ).fetchone()[0]
            == 1
        )


def test_invalid_invite_configuration_fails_closed(tmp_path):
    try:
        create_sandbox_app(tmp_path, enabled=True, invite_digest="plaintext")
    except ValueError as exc:
        assert "digest" in str(exc)
    else:
        raise AssertionError("invalid invite digest accepted")
