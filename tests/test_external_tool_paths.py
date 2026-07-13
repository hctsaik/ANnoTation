from pathlib import Path

from modules.module_012._external_tools import (
    choose_executable,
    missing_tool_message,
    relative_tool_path,
    resolve_tool,
    validate_executable_path,
)


def test_missing_tool_returns_expected_app_relative_path(tmp_path, monkeypatch):
    monkeypatch.delenv("ANNOTATION_EXTERNAL_TOOLS_DIR", raising=False)
    monkeypatch.setattr("modules.module_012._external_tools.shutil.which", lambda _cmd: None)
    executable = resolve_tool("x-anylabeling", tmp_path, tmp_path / "modules" / "module_012")

    assert executable == str(tmp_path / relative_tool_path("x-anylabeling"))
    message = missing_tool_message("x-anylabeling", "X-AnyLabeling", executable)
    assert "external-tools/x-anylabeling/.venv/Scripts/xanylabeling.exe" in message
    assert executable in message


def test_resolver_prefers_bundled_tool(tmp_path, monkeypatch):
    monkeypatch.delenv("ANNOTATION_EXTERNAL_TOOLS_DIR", raising=False)
    monkeypatch.setattr("modules.module_012._external_tools.shutil.which", lambda _cmd: None)
    expected = tmp_path / relative_tool_path("labelme")
    expected.parent.mkdir(parents=True)
    expected.write_bytes(b"launcher")

    assert resolve_tool("labelme", tmp_path, tmp_path / "modules" / "module_012") == str(expected)


def test_resolver_prefers_saved_explicit_executable(tmp_path):
    selected = tmp_path / "custom" / "xanylabeling.exe"
    selected.parent.mkdir()
    selected.write_bytes(b"launcher")

    assert resolve_tool(
        "x-anylabeling", tmp_path, tmp_path / "modules" / "module_012", str(selected)
    ) == str(selected)


def test_file_picker_accepts_only_expected_executable(tmp_path, monkeypatch):
    selected = tmp_path / "xanylabeling.exe"
    selected.write_bytes(b"launcher")

    class Result:
        returncode = 0
        stdout = str(selected)

    monkeypatch.setattr(
        "modules.module_012._external_tools.subprocess.run", lambda *_args, **_kwargs: Result()
    )
    assert choose_executable("x-anylabeling") == str(selected)

    wrong = tmp_path / "wrong.exe"
    wrong.write_bytes(b"launcher")
    Result.stdout = str(wrong)
    assert choose_executable("x-anylabeling") == ""


def test_manual_path_validation_accepts_quotes_and_rejects_wrong_name(tmp_path):
    selected = tmp_path / "xanylabeling.exe"
    selected.write_bytes(b"launcher")
    path, error = validate_executable_path("x-anylabeling", f'"{selected}"')
    assert path == str(selected)
    assert error == ""

    wrong = tmp_path / "wrong.exe"
    wrong.write_bytes(b"launcher")
    path, error = validate_executable_path("x-anylabeling", str(wrong))
    assert path == ""
    assert "xanylabeling.exe" in error
