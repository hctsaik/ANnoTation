"""Repeatable E2E acceptance scenarios for the standalone Annotation App.

Run with an already-started app:
    python tests/e2e_annotation_app_scenarios.py
"""

from __future__ import annotations

import json
import os
import re

from playwright.sync_api import Page, sync_playwright


URL = os.environ.get("ANNOTATION_APP_URL", "http://127.0.0.1:64950")


def settle(page: Page, milliseconds: int = 2500) -> None:
    page.wait_for_timeout(milliseconds)


def open_app(browser, viewport: dict[str, int] | None = None) -> Page:
    page = browser.new_page(viewport=viewport or {"width": 1440, "height": 900})
    page.goto(URL, wait_until="domcontentloaded")
    settle(page, 9000)
    return page


def text(page: Page) -> str:
    return page.locator("body").inner_text()


def go(page: Page, label: str) -> None:
    page.get_by_text(label, exact=True).first.click()
    settle(page, 3500)


def record(results: list[dict], scenario: str, checks: dict[str, bool]) -> None:
    passed = sum(bool(value) for value in checks.values())
    results.append(
        {"scenario": scenario, "score": round(100 * passed / len(checks)), "checks": checks}
    )


def main() -> None:
    results: list[dict] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)

        page = open_app(browser)
        page_text = text(page)
        checks = {
            "app_sheet": all(
                item in page_text
                for item in ("總覽", "資料來源", "標注工作台", "標籤管理", "審核", "匯出 / 回傳")
            ),
            "pending_cta": "標注最後 1 張" in page_text,
            "detail_above_fold": "frame_00000.jpg" in page_text,
        }
        page.get_by_role("button", name="標注最後 1 張").click()
        page.get_by_text("顯示 1 張", exact=True).wait_for(timeout=60_000)
        checks["filter_applied"] = "顯示 1 張" in text(page) and "清除全部篩選" in text(page)
        page.get_by_text("清除全部篩選", exact=True).click()
        settle(page)
        checks["filter_reversible"] = "顯示 1382 張" in text(page)
        nav = page.get_by_text("標注工作台", exact=True).bounding_box()
        heading = page.get_by_text("🏷️ car_1", exact=True).bounding_box()
        checks["compact_layout"] = bool(nav and heading and heading["y"] - nav["y"] < 180)
        checks["standalone_language"] = not any(
            legacy in page_text for legacy in ("Follow Input", "Follow Output", "Process")
        )
        record(results, "中斷後續作最後一張", checks)
        page.close()

        page = open_app(browser)
        go(page, "資料來源")
        page_text = text(page)
        checks = {
            "source_choices": all(item in page_text for item in ("資料夾", "資料庫", "API")),
            "clear_action": "建立資料集並開始標注" in page_text,
            "standalone_language": not any(
                legacy in page_text for legacy in ("Follow Input", "Follow Output", "Process")
            ),
        }
        path_input = page.get_by_label("資料夾路徑")
        path_input.fill(r"Z:\definitely_missing")
        path_input.press("Enter")
        page.get_by_text(re.compile("可讀取的資料夾|找不到|不存在|無效")).first.wait_for(
            timeout=60_000
        )
        page_text = text(page)
        checks["invalid_feedback"] = any(
            item in page_text for item in ("找不到", "不存在", "無效", "可讀取的資料夾")
        )
        action = page.get_by_role("button", name=re.compile("建立資料集並開始標注")).first
        checks["invalid_blocked"] = action.is_disabled()
        checks["action_visible"] = action.is_visible()
        record(results, "建立資料集與來源預檢", checks)
        page.close()

        page = open_app(browser)
        go(page, "標籤管理")
        settle(page, 5000)
        page_text = text(page)
        checks = {
            "module_loaded": "ModuleNotFoundError" not in page_text,
            "statistics": "統計總覽" in page_text,
            "management_tab": "標籤管理" in page_text,
        }
        page.get_by_role("tab", name=re.compile("標籤管理")).click()
        settle(page)
        page_text = text(page)
        checks["governance_controls"] = any(
            item in page_text for item in ("新增標籤", "重新命名", "合併", "刪除標籤")
        )
        checks["impact_language"] = any(
            item in page_text for item in ("確認", "影響", "使用次數", "標注數")
        )
        checks["tabs_visible"] = page.get_by_role("tab").count() >= 2
        record(results, "標籤治理與影響辨識", checks)
        page.close()

        page = open_app(browser)
        go(page, "審核")
        settle(page, 5000)
        page_text = text(page)
        checks = {
            "queue_counts": all(item in page_text for item in ("待審", "核准", "退回")),
            "review_actions": "核准" in page_text and "退回" in page_text,
            "reviewer_identity": any(item in page_text for item in ("審核者", "審查人", "Reviewer")),
            "qa_filter": "QA 狀態" in page_text,
        }
        qa_filter = page.get_by_label("QA 狀態")
        qa_filter.click()
        page.get_by_text("已退回", exact=True).last.click()
        settle(page, 4000)
        page_text = text(page)
        checks["filter_applied"] = "已退回" in page_text
        checks["rejected_queue_visible"] = "已退回" in page_text and "待審" in page_text
        record(results, "QA 審核者處理退回佇列", checks)
        page.close()

        page = open_app(browser)
        go(page, "匯出 / 回傳")
        page_text = text(page)
        checks = {
            "completion_summary": all(item in page_text for item in ("標注完整性", "待標注")),
            "pending_gate": any(item in page_text for item in ("尚有", "部分資料")),
            "review_gate": "審核" in page_text,
        }
        go(page, "審核")
        confirmation = page.locator("label").filter(has_text=re.compile("確認.*審核|審核.*完成"))
        if confirmation.count():
            confirmation.first.click()
            settle(page)
        go(page, "匯出 / 回傳")
        page_text = text(page)
        checks["review_persists"] = any(
            item in page_text for item in ("審核已確認", "已完成審核", "審核狀態")
        )
        partial_export = page.get_by_text("我了解並要匯出部分資料", exact=True)
        if partial_export.count():
            partial_export.click()
            settle(page, 3500)
        page_text = text(page)
        checks["preflight_pass"] = "Preflight 通過" in page_text
        checks["export_form"] = any(
            item in page_text for item in ("輸出格式", "匯出格式", "建立匯出")
        )
        checks["clear_status"] = "Preflight 通過" in page_text
        page.screenshot(path=os.path.join(os.environ.get("TEMP", "."), "annotation-final-export.png"), full_page=True)
        record(results, "未完成資料的受控部分匯出", checks)
        page.close()

        page = open_app(browser, {"width": 1440, "height": 1000})
        previous = page.get_by_role("button", name="←", exact=True).first.bounding_box()
        next_page = page.get_by_role("button", name="→", exact=True).first.bounding_box()
        desktop = {
            "full_width_padding": page.evaluate(
                "getComputedStyle(document.querySelector('[data-testid=stMainBlockContainer]')).paddingLeft === '24px'"
            ),
            "pagination_single_line": bool(
                previous and next_page and abs(previous["y"] - next_page["y"]) < 2
                and previous["height"] < 56 and next_page["height"] < 56
            ),
            "no_horizontal_overflow": page.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 2"
            ),
            "desktop_master_detail": "大圖（1:3）" in text(page),
        }
        page.screenshot(path=os.path.join(os.environ.get("TEMP", "."), "annotation-final-desktop.png"))
        page.close()
        browser.close()

    print(json.dumps({"results": results, "desktop": desktop}, ensure_ascii=False, indent=2))
    assert all(result["score"] > 90 for result in results), results
    assert all(desktop.values()), desktop


if __name__ == "__main__":
    main()
