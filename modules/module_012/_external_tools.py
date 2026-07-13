from __future__ import annotations

import os
import shutil
import subprocess
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


def resolve_tool(
    tool_id: str, project_root: Path, module_dir: Path, explicit_path: str = ""
) -> str:
    """Resolve a bundled tool first, then PATH; return the expected local path if missing."""
    if explicit_path and Path(explicit_path).expanduser().is_file():
        return str(Path(explicit_path).expanduser())
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


def choose_executable(tool_id: str) -> str:
    """Open the local Windows file picker and return a validated executable path."""
    expected_name = TOOL_PATHS[tool_id].name.lower()
    title = f"選擇 {expected_name}"
    script = (
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$d=New-Object System.Windows.Forms.OpenFileDialog;"
        f"$d.Title='{title}';"
        "$d.Filter='Executable (*.exe)|*.exe';"
        "$d.CheckFileExists=$true;"
        "$o=New-Object System.Windows.Forms.Form;"
        "$o.TopMost=$true;$o.ShowInTaskbar=$false;$o.Width=1;$o.Height=1;$o.Opacity=0;"
        "$o.StartPosition='CenterScreen';$o.Show();$o.Activate();"
        "$r=$d.ShowDialog($o);$o.Close();$o.Dispose();"
        "if($r -eq 'OK'){[Console]::Write($d.FileName)}"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-STA", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except Exception:
        return ""
    selected = result.stdout.strip()
    if result.returncode != 0 or not selected:
        return ""
    path = Path(selected)
    if not path.is_file() or path.name.lower() != expected_name:
        return ""
    return str(path)


def validate_executable_path(tool_id: str, raw_path: str) -> tuple[str, str]:
    value = raw_path.strip().strip('"').strip("'")
    if not value:
        return "", "請輸入執行檔完整路徑。"
    path = Path(value).expanduser()
    if not path.is_file():
        return "", f"找不到檔案：{path}"
    expected_name = TOOL_PATHS[tool_id].name.lower()
    if path.name.lower() != expected_name:
        return "", f"請選擇 {expected_name}，目前選到的是 {path.name}。"
    return str(path), ""


def missing_tool_message(tool_id: str, display_name: str, executable: str) -> str | None:
    if Path(executable).is_file() or shutil.which(executable):
        return None
    relative = relative_tool_path(tool_id).as_posix()
    return (
        f"找不到 {display_name} 執行檔。\n\n"
        f"請將完整工具環境放到 App 相對目錄：\n\n`{relative}`\n\n"
        f"目前檢查的位置：\n\n`{executable}`"
    )
