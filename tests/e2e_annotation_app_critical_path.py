"""Zero-tolerance desktop E2E for the standalone Annotation App.

This suite exercises every product sheet plus the two annotation entry points.
Every interaction is followed by a health check so a Streamlit exception,
blank main area, browser error, or broken desktop layout fails at the exact step.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright


URL = os.environ.get("ANNOTATION_APP_URL", "http://127.0.0.1:64950")
NAV_LABELS = ("資料集", "標注", "審核", "匯出")
FATAL_TEXT = (
    "Traceback:",
    "IndexError:",
    "ModuleNotFoundError",
    "無法載入此功能",
    "Exception in",
)


class AppProbe:
    def __init__(self, page: Page) -> None:
        self.page = page
        self.steps: list[str] = []
        self.page_errors: list[str] = []
        self.console_errors: list[str] = []
        page.on("pageerror", lambda error: self.page_errors.append(str(error)))
        page.on(
            "console",
            lambda message: self.console_errors.append(message.text)
            if message.type == "error"
            else None,
        )

    def settle(self, milliseconds: int = 1800) -> None:
        self.page.wait_for_timeout(milliseconds)

    def healthy(self, step: str) -> None:
        self.settle()
        body = self.page.locator("body").inner_text()
        main = self.page.locator("[data-testid=stMainBlockContainer]")
        assert main.is_visible(), f"{step}: main content is not visible"
        assert len(main.inner_text().strip()) > 80, f"{step}: main content is blank"
        assert all(label in body for label in NAV_LABELS), f"{step}: app navigation disappeared"
        found = [token for token in FATAL_TEXT if token in body]
        assert not found, f"{step}: fatal UI error: {found}"
        assert not self.page_errors, f"{step}: page errors: {self.page_errors}"
        assert not self.console_errors, f"{step}: console errors: {self.console_errors}"
        assert self.page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 2"
        ), f"{step}: horizontal desktop overflow"
        box = main.bounding_box()
        assert box and box["width"] >= 1200, f"{step}: desktop content is unexpectedly narrow"
        self.steps.append(step)

    def navigate(self, label: str) -> None:
        self.page.get_by_role("button", name=label, exact=True).click()
        self.healthy(f"navigate:{label}")


def canvas_state(page: Page) -> dict | None:
    for frame in page.frames:
        try:
            state = frame.evaluate("window.__m012canvas ? window.__m012canvas.state() : null")
        except PlaywrightError:
            # Streamlit replaces component iframes during a normal rerun.
            continue
        if state is not None:
            return state
    return None


def main() -> None:
    report: dict = {"url": URL, "steps": []}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        probe = AppProbe(page)
        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=30_000)
            page.get_by_role("button", name="標注", exact=True).wait_for(
                state="visible", timeout=45_000
            )
            probe.healthy("startup")

            probe.navigate("資料集")
            path_input = page.get_by_label("資料夾路徑")
            path_input.fill(r"Z:\annotation-e2e-definitely-missing")
            path_input.press("Enter")
            page.get_by_text(re.compile("可讀取的資料夾|找不到|不存在|無效")).first.wait_for(
                timeout=30_000
            )
            assert page.get_by_role(
                "button", name=re.compile("建立資料集並開始標注")
            ).first.is_disabled()
            probe.healthy("data-source:invalid-path-guard")

            probe.navigate("標注")
            page.get_by_text(re.compile(r"顯示 \d+ 張"), exact=True).wait_for(timeout=45_000)
            total_match = re.search(r"顯示 (\d+) 張", page.locator("body").inner_text())
            assert total_match, "workspace: total result count is missing"
            total_count = int(total_match.group(1))
            pending = page.get_by_role("button", name=re.compile("標注最後"))
            if pending.count():
                pending.first.click()
                page.get_by_text(re.compile(r"顯示 \d+ 張"), exact=True).wait_for(timeout=30_000)
                probe.healthy("workspace:pending-filter")
                page.get_by_text("清除全部篩選", exact=True).click()
                page.get_by_text(f"顯示 {total_count} 張", exact=True).wait_for(timeout=30_000)
                probe.healthy("workspace:clear-filter")

            page.get_by_role("button", name="開啟標注").first.click()
            probe.healthy("workspace:launch-desktop-annotation")
            body = page.locator("body").inner_text()
            if "external-tools" in body:
                assert "xanylabeling.exe" in body
                picker_button = page.get_by_role(
                    "button", name=re.compile("指定 X-AnyLabeling 位置")
                )
                assert picker_button.is_visible()
                picker_button.click()
                path_input = page.get_by_role(
                    "textbox", name="X-AnyLabeling 執行檔完整路徑"
                )
                path_input.wait_for(state="visible", timeout=15_000)
                path_input.fill(r"Z:\missing\xanylabeling.exe")
                page.get_by_role("button", name="套用完整路徑").click()
                page.get_by_text(re.compile("找不到檔案")).last.wait_for(timeout=15_000)
                probe.healthy("workspace:tool-path-panel-validation")
            else:
                assert "找不到 X-AnyLabeling" not in body

            page.get_by_text("在網頁直接標注", exact=False).first.click()
            state = None
            for _ in range(20):
                probe.settle(500)
                state = canvas_state(page)
                if state is not None:
                    break
            assert state, "workspace:web-canvas did not mount"
            assert state["cssW"] > 500 and state["cssH"] > 300
            assert state["curClass"]
            probe.healthy("workspace:web-canvas-mounted")

            probe.navigate("資料集")
            page.get_by_role("tab", name=re.compile("標籤管理")).first.click()
            page.get_by_role("tab").last.click()
            probe.healthy("labels:management-tab")

            probe.navigate("審核")
            assert page.get_by_label("QA 狀態").is_visible()
            probe.healthy("review:queue-and-filter")

            probe.navigate("匯出")
            assert "標注完整性" in page.locator("body").inner_text()
            probe.healthy("export:preflight-summary")

            probe.navigate("標注")
            report["steps"] = probe.steps
        except Exception:
            screenshot = Path(tempfile.gettempdir()) / "annotation-e2e-critical-failure.png"
            page.screenshot(path=str(screenshot), full_page=True)
            report["failure_screenshot"] = str(screenshot)
            report["steps"] = probe.steps
            print(json.dumps(report, ensure_ascii=False, indent=2))
            raise
        finally:
            browser.close()

    report["passed"] = True
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
