"""Opt-in real Edge QA for static historical replay; no model or protected calls.

Run: uv run --locked --with playwright python tests/browser_qa/browser_qa_interactive_harness.py
"""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from threading import Thread

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / "showcase/public"
ARTIFACTS = ROOT / "docs/reports/artifacts/interactive-harness"
VIEWPORTS = ((1440, 900), (1024, 768), (768, 1024), (390, 844), (320, 700))


def main():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    handler = partial(SimpleHTTPRequestHandler, directory=str(PUBLIC))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    local = f"http://127.0.0.1:{server.server_port}/"
    results = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge", headless=True)
            try:
                # Read-only before capture of the currently deployed static site.
                before = browser.new_context(viewport={"width": 1440, "height": 900})
                try:
                    page = before.new_page()
                    response = page.goto(
                        "https://scoperelay-showcase.vercel.app/",
                        wait_until="domcontentloaded",
                    )
                    if response and response.status == 200:
                        page.screenshot(
                            path=str(ARTIFACTS / "before-public-1440.png"),
                            full_page=True,
                        )
                        before_status = "HTTP 200 screenshot captured"
                    else:
                        before_status = f"HTTP {response.status if response else 'no response'}; no screenshot"
                except Exception as error:
                    before_status = f"unavailable: {type(error).__name__}"
                finally:
                    before.close()

                ffmpeg = (
                    Path(os.environ.get("LOCALAPPDATA", ""))
                    / "ms-playwright/ffmpeg-1013/ffmpeg-win64.exe"
                )
                video_available = ffmpeg.is_file()
                options = {
                    "viewport": {"width": 1440, "height": 900},
                    "reduced_motion": "reduce",
                }
                if video_available:
                    options["record_video_dir"] = str(ARTIFACTS)
                context = browser.new_context(**options)
                page = context.new_page()
                errors, bad_responses, off_origin = [], [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on(
                    "console",
                    lambda message: (
                        errors.append(message.text) if message.type == "error" else None
                    ),
                )
                page.on(
                    "response",
                    lambda response: (
                        bad_responses.append((response.status, response.url))
                        if response.status >= 400
                        else None
                    ),
                )
                page.on(
                    "request",
                    lambda request: (
                        off_origin.append(request.url)
                        if not request.url.startswith(local)
                        else None
                    ),
                )
                assert page.goto(local).status == 200
                page.locator("#demo-stages button").first.wait_for()
                assert page.get_by_role("link", name="Run Verified Demo").count() == 1
                assert (
                    page.locator("#demo-progress")
                    .inner_text()
                    .startswith("Stage 1 of 10")
                )
                assert (
                    page.locator("#demo-source mark").inner_text()
                    == "Hello, could you tell me the current status of my order ORD-101? Thanks!"
                )
                page.get_by_role("link", name="Run Verified Demo").click()
                assert page.locator("#lab").is_visible()
                page.locator("#demo-speed").select_option("800")
                page.locator("#demo-play").click()
                expect(page.locator("#demo-progress")).to_contain_text("Stage 2 of")
                page.locator("#demo-play").click()
                paused = page.locator("#demo-progress").inner_text()
                page.wait_for_timeout(1100)
                assert page.locator("#demo-progress").inner_text() == paused
                page.locator("#demo-next").click()
                assert page.locator("#demo-progress").inner_text() != paused
                page.locator("#demo-previous").click()
                page.locator("#demo-restart").click()
                assert (
                    page.locator("#demo-progress")
                    .inner_text()
                    .startswith("Stage 1 of 10")
                )
                page.locator("#demo-stages button").nth(3).click()
                assert "Quotation bound" in page.locator("#demo-workflow").inner_text()
                assert "Task 07G report" in page.locator("#demo-evidence").inner_text()
                page.locator("#demo-stages button").nth(4).focus()
                page.keyboard.press("Enter")
                assert (
                    "Business facts verified"
                    in page.locator("#demo-workflow").inner_text()
                )
                page.locator("#demo-stages button").nth(3).click()
                page.reload()
                expect(page.locator("#demo-workflow")).to_contain_text(
                    "Quotation bound"
                )
                page.locator("#demo-speed").select_option("800")
                page.locator("#demo-play").click()
                expect(page.locator("#demo-progress")).to_contain_text(
                    "Stage 10 of 10", timeout=12000
                )
                assert "NOT_ATTEMPTED" in page.locator("#lab").inner_text()
                page.locator("#demo-restart").click()
                for width, height in VIEWPORTS:
                    page.set_viewport_size({"width": width, "height": height})
                    metrics = page.evaluate(
                        "() => ({viewport: innerWidth, document: document.documentElement.scrollWidth})"
                    )
                    assert metrics["document"] <= metrics["viewport"] + 1, metrics
                    page.screenshot(
                        path=str(ARTIFACTS / f"after-local-{width}.png"), full_page=True
                    )
                    results.append({"width": width, "overflow": False})
                page.get_by_role("button", name="SC-06", exact=False).click()
                assert "REQUEST APPROVAL" in page.locator("#case-detail").inner_text()
                assert (
                    "no approved frozen material"
                    in page.locator("#case-detail").inner_text()
                )
                page.locator(".case-controls button").nth(1).click()
                assert "Step 2 of 8" in page.locator(".case-controls").inner_text()
                page.locator(".case-controls button").nth(2).click()
                assert "Step 1 of 8" in page.locator(".case-controls").inner_text()
                page.get_by_role("button", name="SC-08", exact=False).click()
                assert "current intent" in page.locator("#case-detail").inner_text()
                assert (
                    "OFFLINE DETERMINISTIC EVALUATION"
                    in page.locator("#case-detail").inner_text()
                )
                page.reload()
                expect(page.locator("#case-detail .detail-top")).to_contain_text(
                    "SC-08"
                )
                page.keyboard.press("Tab")
                assert page.evaluate("document.activeElement.className") == "skip"
                assert page.evaluate(
                    "matchMedia('(prefers-reduced-motion: reduce)').matches"
                )
                assert errors == [], errors
                assert bad_responses == [], bad_responses
                assert off_origin == [], off_origin
                missing = context.new_page()
                missing.route(
                    "**/trace.json", lambda route: route.fulfill(status=404, body="")
                )
                assert missing.goto(local).status == 200
                expect(missing.locator("#demo-progress")).to_contain_text(
                    "Verified record unavailable"
                )
                assert missing.locator("#demo-play").is_disabled()
                missing.close()
                context.close()
                print(
                    json.dumps(
                        {
                            "result": "LOCAL_EDGE_PASS",
                            "edge": browser.version,
                            "before_public": before_status,
                            "viewports": results,
                            "screenshots": len(results)
                            + int(before_status.startswith("HTTP 200")),
                            "video": "recorded"
                            if video_available
                            else "unavailable: ffmpeg not installed",
                            "model_generations": 0,
                            "protected_requests": 0,
                        },
                        sort_keys=True,
                    )
                )
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()


if __name__ == "__main__":
    main()
