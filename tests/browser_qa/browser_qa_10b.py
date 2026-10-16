"""Real installed Edge checks for Task 10B limit and expired-session states."""

import json
from pathlib import Path
import socket
import sqlite3
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic, sleep

from playwright.sync_api import sync_playwright
import uvicorn

from scoperelay.sandbox.app import create_sandbox_app


ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "docs/reports/artifacts/10b"


def main() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="scoperelay-10b-edge-") as temporary:
        state_root = Path(temporary) / "sandbox"
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        app = create_sandbox_app(state_root, enabled=True, daily_turn_budget=4)
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                proxy_headers=False,
                access_log=False,
                log_level="error",
            )
        )
        thread = Thread(target=server.run, daemon=True)
        thread.start()
        deadline = monotonic() + 10
        while not server.started and monotonic() < deadline:
            sleep(0.05)
        assert server.started
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="msedge", headless=True)
                try:
                    page = browser.new_page(viewport={"width": 390, "height": 844})
                    assert page.goto(base + "/sandbox").status == 200
                    page.get_by_text("Local offline capacity").wait_for()
                    page.get_by_role("button", name="Run offline scenario").click()
                    page.get_by_text("CANDIDATE VALIDATED").wait_for()
                    run_id = page.evaluate("sessionStorage.getItem('sr_last_run')")
                    assert run_id
                    page.get_by_role("button", name="Run offline scenario").click()
                    page.get_by_text("OFFLINE_CAPACITY_EXHAUSTED").wait_for()
                    assert (
                        "No new run was started"
                        in page.locator("#form-message").inner_text()
                    )
                    page.screenshot(
                        path=str(ARTIFACTS / "capacity-390.png"), full_page=True
                    )
                    con = sqlite3.connect(
                        state_root / "sandbox-state.db", autocommit=True
                    )
                    try:
                        con.execute("UPDATE sessions SET expires_at=0")
                    finally:
                        con.close()
                    page.reload()
                    page.get_by_text("Local offline capacity").wait_for()
                    # Refresh must not replay a run, and an expired owner loses evidence.
                    assert (
                        page.locator("#timeline")
                        .get_by_text("CANDIDATE VALIDATED")
                        .count()
                        == 0
                    )
                    # Page attempts a fresh session after expiry; the old run
                    # is then a different visitor's record (404, not leaked).
                    assert (
                        page.context.request.get(
                            base + f"/api/sandbox/runs/{run_id}"
                        ).status
                        == 404
                    )
                    page.screenshot(
                        path=str(ARTIFACTS / "expired-390.png"), full_page=True
                    )
                    page.close()
                finally:
                    browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive()
        print(
            json.dumps(
                {
                    "result": "LOCAL_EDGE_10B_PASS",
                    "screenshots": 2,
                    "model_generations": 0,
                    "protected_effects": 0,
                }
            )
        )


if __name__ == "__main__":
    main()
