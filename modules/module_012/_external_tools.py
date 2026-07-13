from __future__ import annotations

import os
import shutil
from pathlib import Path


TOOL_PATHS = {
    "x-anylabeling": Path("x-anylabeling/.venv/Scripts/xanylabeling.exe"),
    "labelme": Path("labelme/.venv/Scripts/labelme.exe"),
    "isat": Path("isat/.venv/Scripts/isat-sam.exe"),
}
TOOL_COMMANDS = {
    "x-anylabeling": "xanylabeling",
    "labelme": "labelme",
    "isat": "isat-sam",
}


def relative_tool_path(tool_id: str) -> Path:
    return Path("external-tools") / TOOL_PATHS[tool_id]


def resolve_tool(tool_id: str, project_root: Path, module_dir: Path) -> str:
    """Resolve a bundled tool first, then PATH; return the expected local path if missing."""
    override_root = os.environ.get("ANNOTATION_EXTERNAL_TOOLS_DIR", "").strip()
    roots = []
    if override_root:
        roots.append(Path(override_root).expanduser())
    plugin_root = module_dir.parents[1] if module_dir.parent.name == "modules" else module_dir.parent
    roots.append(plugin_root / "external-tools")
    roots.append(Path(project_root) / "external-tools")

    seen: set[str] = set()
    for root in roots:
        candidate = root / TOOL_PATHS[tool_id]
        key = str(candidate).lower()
        if key not in seen and candidate.is_file():
            return str(candidate)
        seen.add(key)

    command = shutil.which(TOOL_COMMANDS[tool_id])
    if command:
        return command
    return str(roots[0] / TOOL_PATHS[tool_id])


def missing_tool_message(tool_id: str, display_name: str, executable: str) -> str | None:
    if Path(executable).is_file() or shutil.which(executable):
        return None
    relative = str(relative_tool_path(tool_id))
    return (
        f"找不到 {display_name} 執行檔。\n\n"
        f"請將完整工具環境放到 App 相對目錄：\n  {relative}\n\n"
        f"目前檢查的位置：\n  {executable}"
    )
