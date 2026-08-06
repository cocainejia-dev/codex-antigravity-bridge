"""Smoke tests that do not require an installed agy binary."""

import subprocess
import sys

from codex_agy_bridge.agy_runner import clean_agy_output, find_agy, run_agy


def test_clean_agy_output_removes_ansi():
    raw = "\x1b[32mOK\x1b[0m\r\n\x1b[?25l"
    assert clean_agy_output(raw) == "OK"


def test_clean_agy_output_drops_pure_chrome_lines():
    raw = "┌────────┐\nhello\n└────────┘"
    assert clean_agy_output(raw) == "hello"


def test_clean_agy_output_keeps_code_indentation():
    raw = "def f():\n    return 1"
    assert clean_agy_output(raw) == "def f():\n    return 1"


def test_find_agy_does_not_raise():
    # Should return a path or None, never raise.
    find_agy()


def test_run_agy_can_skip_headless_permission_prompts(monkeypatch):
    captured = {}

    def fake_run(args, workdir, timeout):
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, "OK", "")

    monkeypatch.setattr("codex_agy_bridge.agy_runner.find_agy", lambda: "agy")
    monkeypatch.setattr("codex_agy_bridge.agy_runner._run_subprocess", fake_run)

    result = run_agy("Say hi", dangerously_skip_permissions=True)

    assert result.text == "OK"
    assert captured["args"][-1] == "--dangerously-skip-permissions"


def test_run_agy_uses_ascii_workdir_alias_for_non_ascii_windows_workdir(monkeypatch):
    captured = {}

    def fake_run(args, workdir, timeout):
        return subprocess.CompletedProcess(args, 0, "", "")

    def fake_pty(args, workdir, timeout):
        captured["args"] = args
        captured["workdir"] = workdir
        return "OK", 0

    monkeypatch.setattr("codex_agy_bridge.agy_runner.find_agy", lambda: "agy")
    monkeypatch.setattr("codex_agy_bridge.agy_runner._run_subprocess", fake_run)
    monkeypatch.setattr("codex_agy_bridge.agy_runner._run_with_pty", fake_pty)
    monkeypatch.setattr(
        "codex_agy_bridge.agy_runner._windows_short_path",
        lambda path: "C:\\WORKSP~1\\CODEX~2",
    )
    monkeypatch.setattr(sys, "platform", "win32")

    result = run_agy("Say hi", workdir="C:\\workspace\\中文")

    assert result.text == "OK"
    assert "--add-dir" not in captured["args"]
    assert captured["workdir"] == "C:\\WORKSP~1\\CODEX~2"
