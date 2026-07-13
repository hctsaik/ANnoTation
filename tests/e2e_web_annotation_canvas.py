"""Regression: enabling browser annotation must render a live canvas editor."""

from __future__ import annotations

import os

from playwright.sync_api import sync_playwright


URL = os.environ.get("ANNOTATION_APP_URL", "http://127.0.0.1:64950")


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(URL, wait_until="domcontentloaded")
        page.wait_for_timeout(9000)
        page.get_by_text("在網頁直接標注", exact=False).first.click()
        page.wait_for_timeout(9000)

        body = page.locator("body").inner_text()
        probes = []
        for frame in page.frames:
            state = frame.evaluate(
                "window.__m012canvas ? window.__m012canvas.state() : null"
            )
            if state is not None:
                probes.append(state)

        assert "Traceback" not in body
        assert "ModuleNotFoundError" not in body
        assert len(probes) == 1
        assert probes[0]["cssW"] > 500
        assert probes[0]["cssH"] > 300
        assert probes[0]["curClass"]
        browser.close()


if __name__ == "__main__":
    main()
