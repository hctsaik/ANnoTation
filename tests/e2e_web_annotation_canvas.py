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
        editor_frame = None
        for frame in page.frames:
            state = frame.evaluate(
                "window.__m012canvas ? window.__m012canvas.state() : null"
            )
            if state is not None:
                probes.append(state)
                editor_frame = frame

        assert "Traceback" not in body
        assert "ModuleNotFoundError" not in body
        assert len(probes) == 1
        assert probes[0]["cssW"] > 500
        assert probes[0]["cssH"] > 300
        assert probes[0]["curClass"]

        canvas = editor_frame.locator("#cv")
        box = canvas.bounding_box()
        assert box
        canvas.evaluate(
            """el => {
                const r = el.getBoundingClientRect();
                el.dispatchEvent(new WheelEvent('wheel', {
                    deltaY: -120,
                    clientX: r.left + r.width / 2,
                    clientY: r.top + r.height / 2,
                    bubbles: true,
                    cancelable: true,
                }));
            }"""
        )
        page.wait_for_timeout(500)
        zoomed = editor_frame.evaluate("window.__m012canvas.state()")
        assert zoomed["scale"] > probes[0]["scale"]

        editor_frame.locator("#panBtn").click()
        before_pan = editor_frame.evaluate("window.__m012canvas.state()")
        box = canvas.bounding_box()
        assert box
        start = {"x": box["width"] / 2, "y": box["height"] / 2}
        end = {"x": start["x"] + 90, "y": start["y"] + 55}
        canvas.hover(position=start)
        page.mouse.down()
        canvas.hover(position=end)
        page.mouse.up()
        after_pan = editor_frame.evaluate("window.__m012canvas.state()")
        assert after_pan["panMode"] is True
        assert after_pan["ox"] != before_pan["ox"]
        assert after_pan["oy"] != before_pan["oy"]

        editor_frame.locator("#panBtn").click()
        before_middle_pan = editor_frame.evaluate("window.__m012canvas.state()")
        canvas.hover(position=end)
        page.mouse.down(button="middle")
        canvas.hover(position=start)
        page.mouse.up(button="middle")
        after_middle_pan = editor_frame.evaluate("window.__m012canvas.state()")
        assert after_middle_pan["panMode"] is False
        assert after_middle_pan["ox"] != before_middle_pan["ox"]
        assert after_middle_pan["oy"] != before_middle_pan["oy"]
        assert after_middle_pan["dirty"] == before_middle_pan["dirty"]
        browser.close()


if __name__ == "__main__":
    main()
