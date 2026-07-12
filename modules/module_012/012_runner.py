"""Standalone Annotation Workspace for module_012.

The process and output modules keep their existing responsibilities, while this
runner turns them into one continuous application instead of exposing the
platform's Input/Output split to operators.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st


_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_cfg = _load("_012_workspace_config", "_config.py")
_process = _load("_012_workspace_process", "012_process.py")
_output = _load("_012_workspace_output", "012_output.py")

# _manifest_db lives outside this module folder. Support both the packaged
# plugins/labeling/modules layout and the legacy scripts/module_012 runtime.
_shared_candidates = (
    _HERE.parent / "shared",
    _HERE.parents[3] / "scripts" / "shared",
)
_shared_dir = next((path for path in _shared_candidates if path.exists()), _shared_candidates[-1])
_mdb_spec = importlib.util.spec_from_file_location(
    "_012_workspace_manifest_db",
    _shared_dir / "_manifest_db.py",
)
_mdb = importlib.util.module_from_spec(_mdb_spec)
_mdb_spec.loader.exec_module(_mdb)


_TOOLS = {
    "X-AnyLabeling（建議）": "x-anylabeling",
    "LabelMe": "labelme",
    "ISAT-SAM": "isat",
}

_STAGES = (
    ("overview", "總覽", "015", "更新總覽"),
    ("data", "資料來源", "026|010", "建立資料集並開始標注"),
    ("workspace", "標注工作台", "012", ""),
    ("labels", "標籤管理", "017", "掃描標籤與影響"),
    ("review", "審核", "018", "更新審核清單"),
    ("export", "匯出 / 回傳", "014", "建立匯出"),
)


def _parse_lines(raw: str) -> list[str]:
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _settings_key(params: dict) -> str:
    stable = {
        key: params[key]
        for key in (
            "manifest_id",
            "annotation_tool",
            "labels",
            "classification_labels",
            "autorefresh_enabled",
            "autorefresh_seconds",
        )
    }
    return json.dumps(stable, ensure_ascii=False, sort_keys=True)


def _persist_review_confirmation() -> None:
    st.session_state["m012_review_confirmed"] = bool(
        st.session_state.get("m012_review_complete_widget", False)
    )


def _render_stage_navigation() -> str:
    if next_stage := st.session_state.pop("_m012_next_stage", None):
        st.session_state["m012_app_stage"] = next_stage
        st.session_state["m012_stage_nav"] = next_stage
    if "m012_app_stage" not in st.session_state:
        st.session_state["m012_app_stage"] = "workspace"
    labels = {stage: label for stage, label, _module_id, _action in _STAGES}
    selected = st.pills(
        "Annotation workflow",
        list(labels),
        default=st.session_state["m012_app_stage"],
        format_func=labels.get,
        key="m012_stage_nav",
        label_visibility="collapsed",
    )
    if selected:
        st.session_state["m012_app_stage"] = selected
    return st.session_state["m012_app_stage"]


def _render_composed_feature(stage: str, module_id: str, action_label: str) -> None:
    """Compose a legacy split module into one named product stage."""
    module_ids = module_id.split("|")
    module_id = next(
        (candidate for candidate in module_ids if (_HERE.parent / f"module_{candidate}").exists()),
        module_ids[0],
    )
    module_dir = _HERE.parent / f"module_{module_id}"
    try:
        input_module = _load_from(module_dir, f"_annotation_app_{module_id}_input", f"{module_id}_input.py")
        process_module = _load_from(module_dir, f"_annotation_app_{module_id}_process", f"{module_id}_process.py")
        output_module = _load_from(module_dir, f"_annotation_app_{module_id}_output", f"{module_id}_output.py")
    except Exception as exc:
        st.error(f"無法載入此功能：{exc}")
        return

    guidance = {
        "overview": "自動彙整進度、品質異常與最近活動。",
        "data": "建立前先確認來源與掃描選項；成功後會自動進入標注工作台。",
        "labels": "先掃描標籤使用量與近似衝突，再決定改名、合併或刪除。",
        "review": "自動載入審核清單，可依標注狀態與信心度集中處理異常。",
        "export": "匯出前請確認格式、範圍與輸出位置；驗證失敗不會產生不完整包。",
    }
    st.caption(guidance.get(stage, ""))

    if stage == "export":
        workspace = st.session_state.get("m012_workspace_result", {})
        total = int(workspace.get("total", 0))
        annotated = int(workspace.get("annotated", 0))
        pending = max(0, total - annotated)
        reviewed = bool(st.session_state.get("m012_review_confirmed", False))
        st.markdown("#### 匯出前檢查")
        check_cols = st.columns(3)
        check_cols[0].metric("標注完整性", f"{annotated}/{total}" if total else "待檢查")
        check_cols[1].metric("待標注", pending if total else "—")
        check_cols[2].metric("審核狀態", "已確認" if reviewed else "未確認")
        allow_partial = False
        if not total:
            st.warning("尚未完成資料集完整性檢查，請先開啟一次標注工作台。")
            if st.button("前往標注工作台", key="m012_export_go_workspace"):
                st.session_state["_m012_next_stage"] = "workspace"
                st.rerun()
            return
        if pending:
            st.warning(f"仍有 {pending} 張圖片未標注，預設阻擋匯出。")
            allow_partial = st.checkbox(
                "我了解並要匯出部分資料",
                key="m012_export_allow_partial",
            )
        if not reviewed:
            st.warning("尚未確認審核完成。請先完成審核，避免輸出未驗證資料。")
            if st.button("前往審核", key="m012_export_go_review"):
                st.session_state["_m012_next_stage"] = "review"
                st.rerun()
        if (pending and not allow_partial) or not reviewed:
            return
        st.success("Preflight 通過：可以建立匯出。")

    params = input_module.render_input()
    action_disabled = False
    if stage == "data":
        source_type = params.get("source_type")
        if source_type == "folder":
            folder = Path(params.get("folder_path", "")).expanduser()
            action_disabled = not folder.is_dir()
            if action_disabled:
                st.warning("請選擇可讀取的資料夾後再建立資料集。")
            else:
                extensions = {
                    str(ext).lower() for ext in params.get(
                        "extensions", [".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tiff"]
                    )
                }
                iterator = folder.rglob("*") if params.get("recursive", True) else folder.glob("*")
                found = sum(1 for path in iterator if path.is_file() and path.suffix.lower() in extensions)
                st.success(f"預掃描完成：找到 {found} 張支援的圖片。")
                action_disabled = found == 0
        elif source_type == "db":
            db_path = Path(params.get("db_path", "")).expanduser()
            action_disabled = not db_path.is_file() or not params.get("db_sql", "").strip()
            if action_disabled:
                st.warning("請提供存在的 SQLite 檔案與查詢語句。")
        elif source_type == "api":
            action_disabled = not str(params.get("api_url", "")).startswith(("http://", "https://"))
            if action_disabled:
                st.warning("請輸入有效的 http(s) API URL。")
    result_key = f"m012_app_stage_result_{stage}"
    auto_stage = stage in {"overview", "labels", "review"}
    params_key = json.dumps(params, ensure_ascii=False, sort_keys=True, default=str)
    params_state_key = f"{result_key}_params"
    if auto_stage and st.session_state.get(params_state_key) != params_key:
        with st.spinner("正在更新資料…"):
            try:
                st.session_state[result_key] = process_module.execute_logic(params)
                st.session_state[params_state_key] = params_key
            except Exception as exc:
                st.error(f"自動載入失敗：{exc}")
    if not auto_stage and st.button(
        action_label, type="primary", key=f"m012_app_action_{stage}", disabled=action_disabled,
    ):
        with st.spinner(f"{action_label}中…"):
            try:
                st.session_state[result_key] = process_module.execute_logic(params)
                if stage == "data":
                    st.session_state["_m012_next_stage"] = "workspace"
                    st.toast("資料集已建立，正在開啟標注工作台。", icon="✅")
                    st.rerun()
            except Exception as exc:
                st.error(f"{action_label}失敗：{exc}")
                return
    if result_key in st.session_state:
        st.divider()
        if stage == "review":
            review_items = st.session_state[result_key].get("items", [])
            review_counts = {"pending": 0, "approved": 0, "rejected": 0}
            for item in review_items:
                status = item.get("review_status", "pending")
                review_counts[status if status in review_counts else "pending"] += 1
            st.caption(
                f"審核佇列：待審 {review_counts['pending']} · "
                f"核准 {review_counts['approved']} · 退回 {review_counts['rejected']}"
            )
            if "m012_review_complete_widget" not in st.session_state:
                st.session_state["m012_review_complete_widget"] = bool(
                    st.session_state.get("m012_review_confirmed", False)
                )
            st.checkbox(
                "我已依本批審核規則完成檢查，可進入匯出",
                key="m012_review_complete_widget",
                on_change=_persist_review_confirmation,
                help="確認前請完成必要的抽查、旗標處理與異常修正。",
            )
        output_module.render_output(st.session_state[result_key])


def _load_from(directory: Path, name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, directory / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _render_sidebar(manifests: list[dict], cfg: dict) -> dict:
    ids = [manifest["manifest_id"] for manifest in manifests]
    preferred = cfg.get("last_manifest_id") or _cfg.get_shared_manifest_id()
    default_index = ids.index(preferred) if preferred in ids else 0

    with st.sidebar:
        st.markdown("## 工作區設定")
        st.caption("設定會立即保存，不需要另外執行。")

        manifest_id = st.selectbox(
            "資料集",
            ids,
            index=default_index,
            format_func=lambda mid: next(
                (
                    f"{item['name']} · {item.get('item_count', 0)} 張"
                    for item in manifests
                    if item["manifest_id"] == mid
                ),
                mid,
            ),
            key="m012_workspace_manifest",
        )

        if "m012_workspace_labels" not in st.session_state:
            st.session_state["m012_workspace_labels"] = "\n".join(
                cfg.get("annotation_labels", [])
            )
        labels = _parse_lines(
            st.text_area(
                "框選類別",
                key="m012_workspace_labels",
                height=120,
                placeholder="scratch\ndent\nstain",
            )
        )
        if labels:
            st.caption(f"已設定 {len(labels)} 個類別")
        else:
            st.warning("尚未設定框選類別；仍可瀏覽與分類圖片。")

        saved_tool = cfg.get("annotation_tool", "x-anylabeling")
        tool_names = list(_TOOLS)
        tool_index = next(
            (idx for idx, name in enumerate(tool_names) if _TOOLS[name] == saved_tool),
            0,
        )
        tool_name = st.selectbox("標注工具", tool_names, index=tool_index)

        with st.expander("分類與同步", expanded=False):
            if "m012_workspace_classifications" not in st.session_state:
                st.session_state["m012_workspace_classifications"] = "\n".join(
                    cfg.get("classification_labels", [])
                )
            classification_labels = _parse_lines(
                st.text_area(
                    "整張圖片快速分類",
                    key="m012_workspace_classifications",
                    height=90,
                    placeholder="OK\nNG\n待確認",
                )
            )
            autorefresh_enabled = st.toggle(
                "自動掃描標注變更",
                value=bool(cfg.get("autorefresh_enabled", True)),
                key="m012_workspace_autorefresh",
            )
            autorefresh_seconds = int(
                st.number_input(
                    "掃描間隔（秒）",
                    min_value=5,
                    max_value=300,
                    step=5,
                    value=int(cfg.get("autorefresh_seconds", 10)),
                    disabled=not autorefresh_enabled,
                    key="m012_workspace_autorefresh_seconds",
                )
            )

        st.divider()
        force_reload = st.button(
            "重新載入資料集",
            use_container_width=True,
            key="m012_workspace_reload",
        )

    return {
        "manifest_id": manifest_id,
        "annotation_tool": _TOOLS[tool_name],
        "labels": labels,
        "classification_labels": classification_labels,
        "autorefresh_enabled": autorefresh_enabled,
        "autorefresh_seconds": autorefresh_seconds,
        "_force_reload": force_reload,
    }


def main() -> None:
    st.set_page_config(
        page_title="標注工作台",
        page_icon="🏷️",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    st.markdown(
        """<style>
        header[data-testid="stHeader"] { background: transparent; }
        [data-testid="stMainBlockContainer"] {
            padding: 1rem 1.5rem 2rem !important;
            max-width: 100% !important;
        }
        [data-testid="stToolbar"], [data-testid="stStatusWidget"], #MainMenu { display: none !important; }
        [data-testid="stSidebar"][aria-expanded="false"] [data-testid="stSidebarUserContent"] {
            visibility: hidden !important; pointer-events: none !important;
        }
        [data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] { gap: .65rem; }
        [data-testid="stMetric"] { padding: 0 !important; }
        [data-testid="stMetricValue"] { font-size: 1.75rem !important; }
        hr { margin: .25rem 0 !important; }
        button[kind="pillsActive"] {
            background: #2563eb !important;
            border-color: #2563eb !important;
            color: white !important;
        }
        button[kind="pillsActive"] p { color: white !important; }
        button[kind="pills"], button[kind="pillsActive"] { min-height: 44px; }
        [data-testid="stButton"] button[kind="primary"] {
            background: #2563eb !important; border-color: #2563eb !important;
        }
        .m012-skip {
            position: fixed; left: 1rem; top: -4rem; z-index: 999999;
            background: #fff; color: #1d4ed8; border: 2px solid #1d4ed8;
            border-radius: 6px; padding: .5rem .75rem;
        }
        .m012-skip:focus { top: 1rem; }
        @media (max-width: 640px) {
            [data-testid="stHorizontalBlock"]:has([data-testid="stMetric"]) {
                display: grid !important; grid-template-columns: 1fr 1fr !important;
                gap: .75rem !important;
            }
            [data-testid="stHorizontalBlock"]:has([data-testid="stMetric"]) > div {
                width: auto !important; min-width: 0 !important;
            }
        }
        </style>""",
        unsafe_allow_html=True,
    )
    st.markdown(
        '<a class="m012-skip" href="#annotation-main">跳到主要工作區</a>',
        unsafe_allow_html=True,
    )

    title_c, state_c = st.columns([5, 1], vertical_alignment="center")
    title_c.markdown("# Annotation App")
    try:
        saved_at = datetime.fromtimestamp(_cfg._config_path().stat().st_mtime).strftime("%H:%M")
        state_c.caption(f"● 已儲存 {saved_at}")
    except Exception:
        state_c.caption("● 本機自動儲存")

    try:
        manifests = _mdb.list_manifests(_cfg.get_manifest_db_path())
    except Exception as exc:
        st.error(f"無法讀取資料集：{exc}")
        if st.button("重新嘗試"):
            st.rerun()
        return

    if "m012_app_stage" not in st.session_state:
        st.session_state["m012_app_stage"] = "workspace" if manifests else "data"
    stage = _render_stage_navigation()

    if stage != "workspace":
        _selected = next(item for item in _STAGES if item[0] == stage)
        _render_composed_feature(stage, _selected[2], _selected[3])
        return

    if not manifests:
        st.info(
            "目前沒有可標注的資料集。請先到「資料來源」建立資料集，"
            "再回到標注工作台；工作台會自動接續。"
        )
        if st.button("重新檢查資料集", type="primary"):
            st.rerun()
        return

    cfg = _cfg.load_config()
    params = _render_sidebar(manifests, cfg)
    force_reload = params.pop("_force_reload")
    key = _settings_key(params)

    if force_reload or st.session_state.get("m012_workspace_result_key") != key:
        with st.spinner("正在準備標注工作區…"):
            result = _process.execute_logic(params)
        st.session_state["m012_workspace_result"] = result
        st.session_state["m012_workspace_result_key"] = key
    else:
        result = st.session_state.get("m012_workspace_result", {})

    manifest = next(
        (item for item in manifests if item["manifest_id"] == params["manifest_id"]),
        manifests[0],
    )
    st.caption(
        f"**{manifest['name']}** · {manifest.get('item_count', 0)} 張　｜　"
        f"{params['annotation_tool']}　｜　{len(params['labels'])} 個框選類別　｜　"
        "快捷鍵 J/K 切換、A 開啟標注"
    )
    st.markdown('<span id="annotation-main" tabindex="-1"></span>', unsafe_allow_html=True)

    if result.get("mode") != "ready":
        st.error(result.get("error") or "標注工作區初始化失敗。")
        if st.button("重新初始化", type="primary"):
            st.session_state.pop("m012_workspace_result_key", None)
            st.rerun()
        return

    # In the standalone shell refresh is explicit. Periodic full-page reruns can
    # race a filter interaction on large datasets and replace its visible result.
    standalone_result = {**result, "autorefresh_enabled": False}
    _output.render_output(standalone_result, show_help=False)


main()
