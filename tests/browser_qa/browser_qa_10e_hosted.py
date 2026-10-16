"""Read-only hosted static-showcase smoke; no backend or model requests."""

from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


URL = "https://scoperelay-showcase.vercel.app/"
WIDTHS = (1440, 1024, 768, 390, 320)


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        try:
            for width in WIDTHS:
                context = browser.new_context(viewport={"width": width, "height": 900})
                page = context.new_page()
                errors, failed, external = [], [], []
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
                        failed.append(response.url) if response.status >= 400 else None
                    ),
                )
                page.on(
                    "request",
                    lambda request: (
                        external.append(request.url)
                        if urlsplit(request.url).netloc != urlsplit(URL).netloc
                        else None
                    ),
                )
                response = page.goto(URL, wait_until="networkidle")
                assert response is not None and response.status == 200
                page.locator("#case-list").get_by_text("SC-01").first.wait_for()
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= innerWidth + 1"
                )
                assert (
                    "historical" in page.locator("#live-evidence").inner_text().lower()
                )
                assert "OFFLINE" in page.locator("#explorer").inner_text()
                assert "SC-08" in page.locator("#limits").inner_text()
                assert not errors and not failed and not external, (
                    errors,
                    failed,
                    external,
                )
                print(f"HOSTED_EDGE_{width}: PASS; external_requests=0")
                context.close()
        finally:
            browser.close()


if __name__ == "__main__":
    main()
