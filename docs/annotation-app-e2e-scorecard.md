# Annotation App E2E 驗收記錄

日期：2026-07-13  
測試入口：`http://127.0.0.1:64950`  
瀏覽器：Playwright Chromium，desktop 1600×1000 與 1440×900

## 停止條件

五個相異使用情境必須連續取得嚴格大於 90 分。最終獨立複評結果：

| 順序 | 使用情境 | 評分 | 結果 |
|---:|---|---:|---|
| 1 | 中斷後恢復資料集並續作最後一張 | 94 | 通過 |
| 2 | 建立資料集並執行來源預檢 | 92 | 通過 |
| 3 | 待標注篩選、清除與恢復分頁 | 97 | 通過 |
| 4 | 標籤治理、影響辨識與取消危險操作 | 92 | 通過 |
| 5 | 受控部分匯出與 Preflight 品質閘門 | 94 | 通過 |

QA 退回佇列另取得 92 分。最新可重跑驗收對上述五類流程皆取得 100%。

另新增零容忍 critical path，共 15 個連續檢查點，涵蓋：啟動、資料來源錯誤防護、
待標注篩選與清除、桌面標註工具啟動、網頁 canvas 掛載、標籤管理、審核、匯出與總覽。
每次互動後都檢查主內容非空、六個 Sheet 導覽仍存在、無水平溢位，且不得出現
browser `pageerror`、console error、Traceback、IndexError、ModuleNotFoundError 或功能載入失敗。

## 主要改善

- 將平台 Input / Process / Output 拆頁整合為 App 內的總覽、資料來源、工作台、標籤、審核、匯出 Sheet。
- 移除首屏過量留白；桌面首屏可直接看到工作項目，mobile KPI 改為 2×2。
- 待標注 CTA、active filter、清除全部與 `aria-live` 結果數形成完整可逆流程。
- standalone 模式採明確重新掃描，避免週期 rerun 與篩選互動競態；每頁由 50 降至 20 張。
- 資料來源先驗證再允許建立；成功後直接進入工作台。
- 標籤治理顯示影響檔案數，改名／刪除提供取消與二次確認。
- 審核提供 QA 狀態、佇列統計、審核者與時間責任資訊。
- 匯出在完整性與審核未通過時預設阻擋；部分匯出需要明確 opt-in。
- fail-fast runner 逐支執行 critical path、canvas 專項與五情境驗收；任一子測試失敗即回傳非零狀態。

## 重跑

```powershell
$env:PYTHONIOENCODING='utf-8'
python tests/run_annotation_app_e2e.py
```

App 必須已啟動；如非預設位置，可先設定 `ANNOTATION_APP_URL`。
