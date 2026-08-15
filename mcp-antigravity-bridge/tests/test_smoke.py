"""Smoke tests that do not require an installed agy binary."""

import subprocess
import sys

import pytest

from codex_agy_bridge import agy_runner
from codex_agy_bridge.agy_runner import (
    classify_agy_error,
    clean_agy_output,
    find_agy,
    resolve_agy_environment,
    run_agy,
    run_agy_visible,
)


def test_clean_agy_output_removes_ansi():
    raw = "\x1b[32mOK\x1b[0m\r\n\x1b[?25l"
    assert clean_agy_output(raw) == "OK"


@pytest.mark.parametrize("raw", [None, "", b"", b"OK"])
def test_clean_agy_output_normalizes_empty_and_bytes(raw):
    expected = "OK" if raw == b"OK" else ""
    assert clean_agy_output(raw) == expected


def test_clean_agy_output_decodes_invalid_bytes_without_raising():
    assert clean_agy_output(b"OK\xff") == "OK\ufffd"


def test_clean_agy_output_drops_pure_chrome_lines():
    raw = "┌────────┐\nhello\n└────────┘"
    assert clean_agy_output(raw) == "hello"


def test_clean_agy_output_keeps_code_indentation():
    raw = "def f():\n    return 1"
    assert clean_agy_output(raw) == "def f():\n    return 1"


def test_find_agy_does_not_raise():
    # Should return a path or None, never raise.
    find_agy()


def test_resolve_agy_environment_prefers_explicit_proxy(monkeypatch):
    for name in (
        "AGY_PROXY_URL",
        "PROXY_URL",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "ALL_PROXY",
        "https_proxy",
        "http_proxy",
        "all_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGY_PROXY_URL", "http://127.0.0.1:7892")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    monkeypatch.setattr(agy_runner, "_probe_local_proxy_port", lambda port: None)
    monkeypatch.setattr(agy_runner, "_cached_runtime_proxy", lambda force=False: None)

    env = resolve_agy_environment(force=True)

    assert env["HTTP_PROXY"] == "http://127.0.0.1:7892"
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:7892"
    assert env["ALL_PROXY"] == "http://127.0.0.1:7892"


def test_resolve_agy_environment_discovers_local_proxy(monkeypatch):
    for name in (
        "AGY_PROXY_URL",
        "PROXY_URL",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "ALL_PROXY",
        "https_proxy",
        "http_proxy",
        "all_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(agy_runner.sys, "platform", "win32")
    monkeypatch.setattr(agy_runner, "_windows_system_proxy", lambda: None)
    monkeypatch.setattr(
        agy_runner,
        "_probe_local_proxy_port",
        lambda port: "socks5://127.0.0.1:1080" if port == 1080 else None,
    )

    env = resolve_agy_environment(force=True)

    assert env["ALL_PROXY"] == "socks5://127.0.0.1:1080"


def test_resolve_agy_environment_caches_discovery(monkeypatch):
    calls = []
    monkeypatch.setattr(
        agy_runner,
        "_discover_runtime_proxy",
        lambda: calls.append(True) or "http://127.0.0.1:7892",
    )
    for name in (
        "AGY_PROXY_URL",
        "PROXY_URL",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "ALL_PROXY",
        "https_proxy",
        "http_proxy",
        "all_proxy",
    ):
        monkeypatch.delenv(name, raising=False)

    resolve_agy_environment(force=True)
    resolve_agy_environment()

    assert len(calls) == 1


def test_resolve_agy_environment_refreshes_after_cache_expiry(monkeypatch):
    calls = []
    now = iter((100.0, 100.5, 102.0))
    monkeypatch.setattr(agy_runner.time, "monotonic", lambda: next(now))
    monkeypatch.setattr(agy_runner, "_PROXY_CACHE_TTL", 1.0)
    monkeypatch.setattr(
        agy_runner,
        "_discover_runtime_proxy",
        lambda: calls.append(True) or "http://127.0.0.1:7892",
    )
    for name in (
        "AGY_PROXY_URL",
        "PROXY_URL",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "ALL_PROXY",
        "https_proxy",
        "http_proxy",
        "all_proxy",
    ):
        monkeypatch.delenv(name, raising=False)

    resolve_agy_environment(force=True)
    resolve_agy_environment()
    resolve_agy_environment()

    assert len(calls) == 2



def test_run_agy_can_skip_headless_permission_prompts(monkeypatch):
    captured = {}

    def fake_run(args, workdir, timeout, env=None):
        captured["args"] = args
        captured["env"] = env
        return subprocess.CompletedProcess(args, 0, "OK", "")

    monkeypatch.setattr("codex_agy_bridge.agy_runner.find_agy", lambda: "agy")
    monkeypatch.setattr("codex_agy_bridge.agy_runner._run_subprocess", fake_run)

    result = run_agy("Say hi", dangerously_skip_permissions=True)

    assert result.text == "OK"
    assert captured["args"][-1] == "--dangerously-skip-permissions"


def test_run_agy_uses_ascii_workdir_alias_for_non_ascii_windows_workdir(monkeypatch):
    captured = {}

    def fake_run(args, workdir, timeout, env=None):
        return subprocess.CompletedProcess(args, 0, "", "")

    def fake_pty(args, workdir, timeout, env=None):
        captured["args"] = args
        captured["workdir"] = workdir
        captured["env"] = env
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


def test_run_agy_retries_when_direct_output_is_only_tui_chrome(monkeypatch):
    captured = {"pty_calls": 0}

    def fake_run(args, workdir, timeout, env=None):
        return subprocess.CompletedProcess(args, 0, "\x1b[?25l", "")

    def fake_pty(args, workdir, timeout, env=None):
        captured["pty_calls"] += 1
        return "RECOVERED", 0

    monkeypatch.setattr("codex_agy_bridge.agy_runner.find_agy", lambda: "agy")
    monkeypatch.setattr("codex_agy_bridge.agy_runner._run_subprocess", fake_run)
    monkeypatch.setattr("codex_agy_bridge.agy_runner._run_with_pty", fake_pty)

    result = run_agy("Say hi")

    assert result.text == "RECOVERED"
    assert result.used_pty is True
    assert captured["pty_calls"] == 1


@pytest.mark.parametrize("stdout", [None, "", b"", b"OK"])
def test_run_agy_handles_direct_stdout_variants_and_falls_back(monkeypatch, stdout):
    captured = {"pty_calls": 0}

    def fake_run(args, workdir, timeout, env=None):
        return subprocess.CompletedProcess(args, 0, stdout, None)

    def fake_pty(args, workdir, timeout, env=None):
        captured["pty_calls"] += 1
        return "PTY OK", 0

    monkeypatch.setattr("codex_agy_bridge.agy_runner.find_agy", lambda: "agy")
    monkeypatch.setattr("codex_agy_bridge.agy_runner._run_subprocess", fake_run)
    monkeypatch.setattr("codex_agy_bridge.agy_runner._run_with_pty", fake_pty)

    result = run_agy("Say hi")

    assert result.text == ("OK" if stdout == b"OK" else "PTY OK")
    assert captured["pty_calls"] == (0 if stdout == b"OK" else 1)


@pytest.mark.parametrize("stderr", [None, "", b"", b"failure"])
def test_run_agy_handles_direct_stderr_variants(monkeypatch, stderr):
    def fake_run(args, workdir, timeout, env=None):
        return subprocess.CompletedProcess(args, 7, None, stderr)

    monkeypatch.setattr("codex_agy_bridge.agy_runner.find_agy", lambda: "agy")
    monkeypatch.setattr("codex_agy_bridge.agy_runner._run_subprocess", fake_run)

    result = run_agy("Say hi")

    expected = (
        "failure"
        if stderr == b"failure"
        else "agy returned no diagnostic output"
    )
    assert result.text == expected
    assert result.exit_code == 7


def test_run_agy_preserves_unicode_workdir_on_fallback(monkeypatch):
    captured = {}

    def fake_run(args, workdir, timeout, env=None):
        return subprocess.CompletedProcess(args, 0, None, None)

    def fake_pty(args, workdir, timeout, env=None):
        captured["workdir"] = workdir
        return "中文 OK", 0

    monkeypatch.setattr("codex_agy_bridge.agy_runner.find_agy", lambda: "agy")
    monkeypatch.setattr("codex_agy_bridge.agy_runner._run_subprocess", fake_run)
    monkeypatch.setattr("codex_agy_bridge.agy_runner._run_with_pty", fake_pty)
    monkeypatch.setattr("codex_agy_bridge.agy_runner.sys.platform", "linux")

    result = run_agy("Say hi", workdir="D:\\工作区\\中文")

    assert result.text == "中文 OK"
    assert captured["workdir"] == "D:\\工作区\\中文"


def test_run_agy_propagates_timeout(monkeypatch):
    def fake_run(args, workdir, timeout, env=None):
        raise TimeoutError("agy timed out after 1s")

    monkeypatch.setattr("codex_agy_bridge.agy_runner.find_agy", lambda: "agy")
    monkeypatch.setattr("codex_agy_bridge.agy_runner._run_subprocess", fake_run)

    with pytest.raises(TimeoutError, match="agy timed out"):
        run_agy("Say hi", timeout=1)


def test_run_agy_repeated_empty_direct_output_is_stable(monkeypatch):
    calls = {"pty": 0}

    def fake_run(args, workdir, timeout, env=None):
        return subprocess.CompletedProcess(args, 0, None, None)

    def fake_pty(args, workdir, timeout, env=None):
        calls["pty"] += 1
        return "OK", 0

    monkeypatch.setattr("codex_agy_bridge.agy_runner.find_agy", lambda: "agy")
    monkeypatch.setattr("codex_agy_bridge.agy_runner._run_subprocess", fake_run)
    monkeypatch.setattr("codex_agy_bridge.agy_runner._run_with_pty", fake_pty)

    for _ in range(20):
        result = run_agy("Say hi")
        assert result.text == "OK"
        assert "NoneType" not in result.text

    assert calls["pty"] == 20


def test_run_agy_preserves_direct_stderr_when_pty_has_no_output(monkeypatch):
    def fake_run(args, workdir, timeout, env=None):
        return subprocess.CompletedProcess(args, 7, "", "authentication failed")

    monkeypatch.setattr("codex_agy_bridge.agy_runner.find_agy", lambda: "agy")
    monkeypatch.setattr("codex_agy_bridge.agy_runner._run_subprocess", fake_run)
    monkeypatch.setattr(
        "codex_agy_bridge.agy_runner._run_with_pty",
        lambda args, workdir, timeout, env=None: ("", -1),
    )

    result = run_agy("Say hi")

    assert result.text == "authentication failed"
    assert result.stderr == "authentication failed"
    assert result.exit_code == 7


def test_run_agy_classifies_empty_direct_and_pty_output_as_failure(monkeypatch):
    def fake_run(args, workdir, timeout, env=None):
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("codex_agy_bridge.agy_runner.find_agy", lambda: "agy")
    monkeypatch.setattr("codex_agy_bridge.agy_runner._run_subprocess", fake_run)
    monkeypatch.setattr(
        "codex_agy_bridge.agy_runner._run_with_pty",
        lambda args, workdir, timeout, env=None: ("", 0),
    )

    result = run_agy("Say hi")

    assert result.exit_code == -1
    assert "no output" in result.text


def test_run_agy_visible_uses_a_new_console(monkeypatch):
    captured = {}

    class FakeProcess:
        def wait(self, timeout):
            captured["timeout"] = timeout
            return 0

    def fake_popen(args, cwd, creationflags, env=None):
        captured["args"] = args
        captured["cwd"] = cwd
        captured["creationflags"] = creationflags
        captured["env"] = env
        return FakeProcess()

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("codex_agy_bridge.agy_runner.find_agy", lambda: "agy")
    monkeypatch.setattr(
        agy_runner,
        "resolve_agy_environment",
        lambda force=False: {"HTTPS_PROXY": "http://127.0.0.1:7892"},
    )
    monkeypatch.setattr("codex_agy_bridge.agy_runner.subprocess.Popen", fake_popen)

    result = run_agy_visible(
        "Say hi",
        workdir="C:\\work",
        timeout=12,
        dangerously_skip_permissions=True,
    )

    assert result.exit_code == 0
    assert "terminal" in result.text
    assert captured["args"][-1] == "--dangerously-skip-permissions"
    assert captured["cwd"] == "C:\\work"
    assert captured["creationflags"] == 0x00000010
    assert captured["timeout"] == 12
    assert captured["env"]["HTTPS_PROXY"] == "http://127.0.0.1:7892"


def test_run_agy_passes_one_environment_to_direct_and_pty(monkeypatch):
    captured = {}
    expected_env = {"HTTP_PROXY": "http://127.0.0.1:7892"}

    def fake_run(args, workdir, timeout, env=None):
        captured["direct_env"] = env
        return subprocess.CompletedProcess(args, 0, "", "")

    def fake_pty(args, workdir, timeout, env=None):
        captured["pty_env"] = env
        return "OK", 0

    monkeypatch.setattr(agy_runner, "find_agy", lambda: "agy")
    monkeypatch.setattr(agy_runner, "resolve_agy_environment", lambda force=False: expected_env)
    monkeypatch.setattr(agy_runner, "_run_subprocess", fake_run)
    monkeypatch.setattr(agy_runner, "_run_with_pty", fake_pty)

    assert run_agy("Say hi").text == "OK"
    assert captured["direct_env"] is expected_env
    assert captured["pty_env"] is expected_env


def test_classify_agy_error_distinguishes_network_from_login():
    assert classify_agy_error("token exchange failed: dial tcp 1.2.3.4:443") == "network"
    assert classify_agy_error("OAuth token is invalid; login required") == "authentication"
