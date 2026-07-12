"""LV → Labeling 單向交棒收尾（close-out）契約測試。

VisualLatent (LV) 把批次交給 Labeling 後不再追蹤（LV 端沒有交接箱）。當批次走到
匯出（module_014）即代表 LV 交辦完成——`014_process._retire_lv_handoffs()` 會把
共享 registry（<CIM_LOG_DIR>/lv_labeling_handoff/_pending.json）裡**這次匯出來源**的
批次標記為 ``read_back``，好讓資料來源頁（module_026）不再重複帶入。

關鍵契約（2026-07-12 修正）：**只關這次匯出真正來源的那個 handoff**，靠 manifest 的
item 路徑落在哪個 handoff 的 ``images_dir`` 內來辨識——不可一次關掉所有 open batch，
否則會誤把其他仍在標註的 LV 批次標成已交付。

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


def _handoff_dir(tmp_path: Path, hid: str, n: int = 2) -> tuple[str, list[str]]:
    """Create a hand-off's images_dir with n sha-named images; return
    (images_dir, item_paths) exactly as module_026 `_run_local` would scan them."""
    idir = tmp_path / "lv_labeling_handoff" / hid / "images"
    idir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(n):
        p = idir / f"{hid}_{i:02d}.jpg"
        p.write_bytes(b"\xff\xd8\xff")   # not read; only the path/location matters
        paths.append(str(p))
    return str(idir), paths


def test_retire_closes_only_the_source_batch(tmp_path):
    """The batch this export came from is closed; an unrelated open batch is NOT
    (this is the P0 fix — export must not blanket-retire every in-flight batch)."""
    idir_a, items_a = _handoff_dir(tmp_path, "sel_1")
    idir_b, _items_b = _handoff_dir(tmp_path, "cart_2")
    reg = _seed_registry(tmp_path, {
        "sel_1": {"source": "selection", "task": "relabel", "status": "sent",
                  "images_dir": idir_a, "n_total": 2, "created_at": "2026-06-14T10:00:00"},
        "cart_2": {"source": "cart", "task": "relabel", "status": "annotating",
                   "images_dir": idir_b, "n_total": 2, "created_at": "2026-06-14T11:00:00"},
    })
    proc = _load_process(tmp_path)

    retired = proc._retire_lv_handoffs(items_a)   # export scanned handoff A in place

    data = json.loads(reg.read_text(encoding="utf-8"))
    assert data["sel_1"]["status"] == "read_back", "the source batch must be closed"
    assert data["cart_2"]["status"] == "annotating", \
        "an unrelated in-flight batch must stay open"
    assert retired is not None and retired["source"] == "selection"


def test_retire_is_idempotent(tmp_path):
    idir, items = _handoff_dir(tmp_path, "sel_1")
    _seed_registry(tmp_path, {
        "sel_1": {"source": "selection", "task": "relabel", "status": "sent",
                  "images_dir": idir, "n_total": 2, "created_at": "2026-06-14T10:00:00"},
    })
    proc = _load_process(tmp_path)
    assert proc._retire_lv_handoffs(items) is not None   # first export closes it out
    assert proc._retire_lv_handoffs(items) is None        # nothing open the second time


def test_retire_noop_when_items_match_no_handoff(tmp_path):
    """A non-LV manifest (items live outside any hand-off images_dir) closes nothing."""
    idir, _items = _handoff_dir(tmp_path, "sel_1")
    _seed_registry(tmp_path, {
        "sel_1": {"source": "selection", "task": "relabel", "status": "sent",
                  "images_dir": idir, "n_total": 2, "created_at": "2026-06-14T10:00:00"},
    })
    proc = _load_process(tmp_path)
    outside = [str(tmp_path / "some_other_dataset" / "a.jpg")]
    assert proc._retire_lv_handoffs(outside) is None
    reg = tmp_path / "lv_labeling_handoff" / "_pending.json"
    assert json.loads(reg.read_text(encoding="utf-8"))["sel_1"]["status"] == "sent"


def test_retire_safe_when_no_registry(tmp_path):
    # No LV hand-off ever happened (or non-LV manifest): nothing to close out.
    proc = _load_process(tmp_path)
    assert proc._retire_lv_handoffs(["/whatever/a.jpg"]) is None


def test_retire_returns_none_when_all_already_read(tmp_path):
    idir, items = _handoff_dir(tmp_path, "done_1")
    _seed_registry(tmp_path, {
        "done_1": {"source": "cart", "task": "relabel", "status": "read_back",
                   "images_dir": idir, "n_total": 2, "created_at": "2026-06-14T10:00:00"},
    })
    proc = _load_process(tmp_path)
    assert proc._retire_lv_handoffs(items) is None
