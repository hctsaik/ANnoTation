"""LV → Labeling 單向交棒收尾（close-out）契約測試。

VisualLatent (LV) 把批次交給 Labeling 後不再追蹤（LV 端沒有交接箱）。當批次走到
匯出（module_014）即代表 LV 交辦完成——`014_process._retire_lv_handoffs()` 會把
共享 registry（<CIM_LOG_DIR>/lv_labeling_handoff/_pending.json）裡仍開著的批次標記為
``read_back``，好讓資料來源頁（module_026）不再重複帶入，並讓輸出頁顯示「不用回 LV」收尾。

這裡只測 module_014 的 process 純邏輯（無 Streamlit、不需 hnswlib），框架無關地讀寫 JSON。
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

_HERE = Path(__file__).parent
_M014 = _HERE.parent / "modules" / "module_014" / "014_process.py"


def _load_process(cim_log_dir: Path):
    """Load 014_process.py fresh so its module-level `_CIM_LOG_DIR` picks up our
    temp CIM_LOG_DIR (the constant is read from env at import time)."""
    os.environ["CIM_LOG_DIR"] = str(cim_log_dir)
    spec = importlib.util.spec_from_file_location("m014_proc_closeout", _M014)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed_registry(cim_log_dir: Path, entries: dict) -> Path:
    reg = cim_log_dir / "lv_labeling_handoff" / "_pending.json"
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    return reg


def test_retire_marks_open_batches_and_returns_newest(tmp_path):
    reg = _seed_registry(tmp_path, {
        "sel_1": {"source": "selection", "task": "relabel", "status": "sent",
                  "n_total": 3, "created_at": "2026-06-14T10:00:00"},
        "cart_2": {"source": "cart", "task": "relabel", "status": "annotating",
                   "n_total": 5, "created_at": "2026-06-14T11:00:00"},
        "old_3": {"source": "diversity", "task": "fresh", "status": "read_back",
                  "n_total": 2, "created_at": "2026-06-13T09:00:00"},
    })
    proc = _load_process(tmp_path)

    newest = proc._retire_lv_handoffs()

    data = json.loads(reg.read_text(encoding="utf-8"))
    # every still-open batch is now delivered → module_026 stops re-suggesting them
    assert all(v["status"] == "read_back" for v in data.values())
    # the close-out message names the newest retired batch
    assert newest is not None and newest["source"] == "cart"


def test_retire_is_idempotent(tmp_path):
    _seed_registry(tmp_path, {
        "sel_1": {"source": "selection", "task": "relabel", "status": "sent",
                  "n_total": 1, "created_at": "2026-06-14T10:00:00"},
    })
    proc = _load_process(tmp_path)
    assert proc._retire_lv_handoffs() is not None   # first export closes it out
    assert proc._retire_lv_handoffs() is None        # nothing open the second time


def test_retire_safe_when_no_registry(tmp_path):
    # No LV hand-off ever happened (or non-LV manifest): nothing to close out.
    proc = _load_process(tmp_path)
    assert proc._retire_lv_handoffs() is None


def test_retire_returns_none_when_all_already_read(tmp_path):
    _seed_registry(tmp_path, {
        "done_1": {"source": "cart", "task": "relabel", "status": "read_back",
                   "n_total": 4, "created_at": "2026-06-14T10:00:00"},
    })
    proc = _load_process(tmp_path)
    assert proc._retire_lv_handoffs() is None
