from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SRC = Path(__file__).resolve().parents[1] / "mcp-antigravity-bridge" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from codex_agy_bridge import agy_runner
from codex_agy_bridge.agy_runner import (
    AGY_PRINT_TIMEOUT,
    CONNECT_TIMEOUT,
    LOCAL_SUPERVISION_TIMEOUT,
    REMOTE_EXECUTION_TIMEOUT,
    TASK_WALL_CLOCK_BUDGET,
    AgyResult,
    LocalSupervisionTimeoutError,
    _format_duration_flag,
    classify_agy_error,
    derive_agy_print_timeout,
    describe_agy_failure,
    run_agy,
    run_agy_visible,
)


def test_classify_agy_error_network_and_connect_timeout_markers():
    connect_samples = [
        "connection timed out",
        "connect timeout after 5000ms",
        "tls handshake timeout",
        "socket timeout while writing request",
    ]
    for sample in connect_samples:
        assert classify_agy_error(sample) == CONNECT_TIMEOUT, f"Expected CONNECT_TIMEOUT for: {sample}"

    network_samples = [
        "dial tcp 127.0.0.1:7890: connectex: connection refused",
        "proxyconnect tcp: dial tcp 127.0.0.1:7890: connect: connection refused",
        "network is unreachable",
        "no such host: api.antigravity.google",
        "failed to connect to host",
        "could not connect to server",
        "network error: eof while connecting",
        "failed to fetch response from proxy",
    ]
    for sample in network_samples:
        assert classify_agy_error(sample) == "network", f"Expected network for: {sample}"


def test_classify_agy_error_authentication_markers():
    auth_samples = [
        "authentication required",
        "authentication failed: invalid token",
        "login required to continue",
        "oauth token has expired; please run `agy` to log in",
        "unauthenticated request",
        "invalid_grant: bad credentials",
    ]
    for sample in auth_samples:
        assert classify_agy_error(sample) == "authentication", f"Expected auth for: {sample}"


def test_classify_agy_error_timeout_granularity():
    # 1. Local supervision TimeoutError markers
    local_samples = [
        "TimeoutError: agy timed out after 300.0s",
        "LOCAL_SUPERVISION_TIMEOUT: agy timed out after 17.0s",
        "agy timed out after 17.0s",
    ]
    for sample in local_samples:
        kind = classify_agy_error(sample)
        assert kind == LOCAL_SUPERVISION_TIMEOUT, f"Expected LOCAL_SUPERVISION_TIMEOUT for {sample}, got {kind}"

    # 2. Remote execution timeout text
    remote_samples = [
        "command timed out after 60 seconds",
        "pytest execution timed out",
        "test execution timed out after 120s",
        "deadline exceeded while waiting for task completion",
        "time limit exceeded during build step",
    ]
    for sample in remote_samples:
        kind = classify_agy_error(sample)
        assert kind == REMOTE_EXECUTION_TIMEOUT, f"Expected REMOTE_EXECUTION_TIMEOUT for {sample}, got {kind}"

    # 3. Connect timeout markers
    connect_samples = [
        "connect timeout after 5000ms",
        "connection timed out",
        "tls handshake timeout",
        "socket timeout while writing request",
    ]
    for sample in connect_samples:
        kind = classify_agy_error(sample)
        assert kind == CONNECT_TIMEOUT, f"Expected CONNECT_TIMEOUT for {sample}, got {kind}"


def test_classify_agy_error_unknown_for_other_errors():
    unknown_samples = [
        "SyntaxError: invalid syntax in file.py",
        "TypeError: unsupported operand type",
        "AssertionError: expected True but got False",
        "Process exited with code 1",
    ]
    for sample in unknown_samples:
        assert classify_agy_error(sample) == "unknown", f"Expected unknown for: {sample}"


def test_describe_agy_failure_formatting():
    # 1. Network error -> AGY_PROXY_ERROR
    net_res = AgyResult(text="dial tcp 127.0.0.1:7890: connection refused", exit_code=1)
    net_desc = describe_agy_failure(net_res)
    assert net_desc.startswith("AGY_PROXY_ERROR:")
    assert "dial tcp" in net_desc

    # 2. Auth error -> AGY_LOGIN_REQUIRED
    auth_res = AgyResult(text="authentication required: please run `agy`", exit_code=1)
    auth_desc = describe_agy_failure(auth_res)
    assert auth_desc.startswith("AGY_LOGIN_REQUIRED:")
    assert "authentication required" in auth_desc

    # 3. Timeout error -> AGY_TIMEOUT
    timeout_res = AgyResult(text="agy timed out after 300.0s", exit_code=1)
    timeout_desc = describe_agy_failure(timeout_res)
    assert timeout_desc.startswith("AGY_TIMEOUT:")
    assert "timed out" in timeout_desc

    # 4. Generic failure -> AGY_FAILED
    generic_res = AgyResult(text="file not found: config.yaml", exit_code=2)
    generic_desc = describe_agy_failure(generic_res)
    assert generic_desc.startswith("AGY_FAILED:")
    assert "code 2" in generic_desc


def test_run_agy_network_error_forces_proxy_refresh(monkeypatch):
    monkeypatch.setattr(agy_runner, "find_agy", lambda: "fake_agy")
    force_calls = []

    def fake_resolve(force=False):
        force_calls.append(force)
        return {}

    monkeypatch.setattr(agy_runner, "resolve_agy_environment", fake_resolve)

    def fake_run_subprocess(args, workdir, timeout, env=None, **kwargs):
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="connection timed out")

    monkeypatch.setattr(agy_runner, "_run_subprocess", fake_run_subprocess)

    result = run_agy("test prompt")
    assert result.exit_code == 1
    assert True in force_calls  # force=True was called for network/connect error


def test_run_agy_timeout_does_not_force_proxy_refresh(monkeypatch):
    monkeypatch.setattr(agy_runner, "find_agy", lambda: "fake_agy")
    force_calls = []

    def fake_resolve(force=False):
        force_calls.append(force)
        return {}

    monkeypatch.setattr(agy_runner, "resolve_agy_environment", fake_resolve)

    def fake_run_subprocess(args, workdir, timeout, env=None, **kwargs):
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="pytest execution timed out")

    monkeypatch.setattr(agy_runner, "_run_subprocess", fake_run_subprocess)

    result = run_agy("test prompt")
    assert result.exit_code == 1
    assert True not in force_calls  # force=True was NOT called for timeout error


def test_run_subprocess_liveness_probe_extends_deadline(monkeypatch):
    mock_proc = MagicMock()
    calls = 0

    def mock_communicate(timeout=None):
        nonlocal calls
        calls += 1
        if calls < 3:
            time.sleep(0.02)
            raise subprocess.TimeoutExpired(cmd=["agy"], timeout=timeout)
        return ("output", "")

    mock_proc.communicate.side_effect = mock_communicate
    mock_proc.returncode = 0
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: mock_proc)

    probe_calls = 0

    def probe():
        nonlocal probe_calls
        probe_calls += 1
        return True

    res = agy_runner._run_subprocess(
        ["agy", "-p", "test"],
        workdir=None,
        timeout=0.01,
        liveness_probe=probe,
        stall_grace_seconds=0.05,
    )
    assert res.returncode == 0
    assert res.stdout == "output"
    assert probe_calls >= 1


def test_run_subprocess_liveness_probe_false_terminates_child(monkeypatch):
    mock_proc = MagicMock()

    def mock_communicate(timeout=None):
        if mock_proc.kill.called:
            return ("", "")
        time.sleep(0.02)
        raise subprocess.TimeoutExpired(cmd=["agy"], timeout=timeout)

    mock_proc.communicate.side_effect = mock_communicate
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: mock_proc)

    def probe():
        return False

    with pytest.raises((TimeoutError, LocalSupervisionTimeoutError), match="agy timed out"):
        agy_runner._run_subprocess(
            ["agy", "-p", "test"],
            workdir=None,
            timeout=0.01,
            liveness_probe=probe,
            stall_grace_seconds=0.05,
        )

    assert mock_proc.kill.called


def test_run_subprocess_repeated_positive_probes_eventually_cause_timeout(monkeypatch):
    mock_proc = MagicMock()

    def mock_communicate(timeout=None):
        if mock_proc.kill.called:
            return ("", "")
        time.sleep(0.01)
        raise subprocess.TimeoutExpired(cmd=["agy"], timeout=timeout)

    mock_proc.communicate.side_effect = mock_communicate
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: mock_proc)

    probe_calls = 0

    def always_alive():
        nonlocal probe_calls
        probe_calls += 1
        return True

    with pytest.raises(LocalSupervisionTimeoutError, match="LOCAL_SUPERVISION_TIMEOUT"):
        agy_runner._run_subprocess(
            ["agy", "-p", "test"],
            workdir=None,
            timeout=0.01,
            liveness_probe=always_alive,
            stall_grace_seconds=0.01,
            max_liveness_extensions=2,
        )

    assert mock_proc.kill.called
    assert probe_calls == 2


def test_run_agy_no_pty_fallback_when_direct_exit_0_has_stderr_evidence(monkeypatch):
    monkeypatch.setattr(agy_runner, "find_agy", lambda: "fake_agy")
    pty_called = False

    def fake_run_subprocess(args, workdir, timeout, env=None, **kwargs):
        return subprocess.CompletedProcess(
            args,
            0,
            stdout="",
            stderr="[INFO] task execution finished on remote agent",
        )

    def fake_run_with_pty(*args, **kwargs):
        nonlocal pty_called
        pty_called = True
        return ("unexpected duplicate execution", 0)

    monkeypatch.setattr(agy_runner, "_run_subprocess", fake_run_subprocess)
    monkeypatch.setattr(agy_runner, "_run_with_pty", fake_run_with_pty)

    result = run_agy("execute contract")
    assert result.exit_code == 0
    assert result.used_pty is False
    assert "[INFO] task execution finished on remote agent" in result.text
    assert pty_called is False, "PTY fallback must not be called when direct exit 0 has stderr evidence"


def test_run_agy_pty_fallback_when_direct_exit_0_has_empty_stdout_and_empty_stderr(monkeypatch):
    monkeypatch.setattr(agy_runner, "find_agy", lambda: "fake_agy")
    pty_called = False

    def fake_run_subprocess(args, workdir, timeout, env=None, **kwargs):
        # Empty stdout and empty stderr represents the upstream #76 isatty gate
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    def fake_run_with_pty(*args, **kwargs):
        nonlocal pty_called
        pty_called = True
        return ("recovered via pty", 0)

    monkeypatch.setattr(agy_runner, "_run_subprocess", fake_run_subprocess)
    monkeypatch.setattr(agy_runner, "_run_with_pty", fake_run_with_pty)

    result = run_agy("execute contract")
    assert result.exit_code == 0
    assert result.used_pty is True
    assert result.text == "recovered via pty"
    assert pty_called is True


def test_derive_agy_print_timeout_safe_and_deterministic():
    # 1. Default wall budget = 1800 -> 900
    assert derive_agy_print_timeout(1800) == 900
    assert derive_agy_print_timeout(TASK_WALL_CLOCK_BUDGET) == 900

    # 2. Explicit 300 wall budget -> 240 (reserving 60s margin)
    assert derive_agy_print_timeout(300) == 240
    assert derive_agy_print_timeout(300) < 300

    # 3. Explicit 1200 / 3600 wall budgets -> 900 (normal target, well below budget)
    assert derive_agy_print_timeout(1200) == 900
    assert derive_agy_print_timeout(3600) == 900

    # 4. Small budgets preserving supervision margin
    assert derive_agy_print_timeout(60) == 54
    assert derive_agy_print_timeout(10) == 9
    assert derive_agy_print_timeout(2) == 1
    assert derive_agy_print_timeout(1) == 1

    # 5. Invalid / non-positive / NaN / bool budgets safely return minimum
    assert derive_agy_print_timeout(0) == 1
    assert derive_agy_print_timeout(-50) == 1
    assert derive_agy_print_timeout(float("nan")) == 1
    assert derive_agy_print_timeout(float("inf")) == 1
    assert derive_agy_print_timeout(True) == 1
    assert derive_agy_print_timeout(False) == 1


def test_format_duration_flag():
    assert _format_duration_flag(900) == "900s"
    assert _format_duration_flag(240) == "240s"
    assert _format_duration_flag(300.0) == "300s"
    assert _format_duration_flag("900s") == "900s"
    assert _format_duration_flag("15m") == "15m"
    assert _format_duration_flag("2h") == "2h"
    assert _format_duration_flag("900") == "900s"


def test_run_agy_argv_default_print_timeout_go_syntax(monkeypatch):
    monkeypatch.setattr(agy_runner, "find_agy", lambda: "fake_agy")
    captured_args = []

    def fake_run_subprocess(args, workdir, timeout, env=None, **kwargs):
        captured_args.append((args, timeout))
        return subprocess.CompletedProcess(args, 0, stdout="done", stderr="")

    monkeypatch.setattr(agy_runner, "_run_subprocess", fake_run_subprocess)
    monkeypatch.setattr(agy_runner, "resolve_agy_environment", lambda force=False: {})

    res = run_agy("build project")
    assert res.exit_code == 0
    assert len(captured_args) == 1
    args, timeout = captured_args[0]
    assert timeout == 1800.0
    assert args[0] == "fake_agy"
    assert args[1:3] == ["-p", "build project"]
    assert "--print-timeout" in args
    idx = args.index("--print-timeout")
    assert args[idx + 1] == "900s"  # Explicit valid Go duration syntax, not bare '900'


def test_run_agy_argv_explicit_wall_budget_propagation(monkeypatch):
    monkeypatch.setattr(agy_runner, "find_agy", lambda: "fake_agy")
    captured_calls = []

    def fake_run_subprocess(args, workdir, timeout, env=None, **kwargs):
        captured_calls.append((args, timeout))
        return subprocess.CompletedProcess(args, 0, stdout="done", stderr="")

    monkeypatch.setattr(agy_runner, "_run_subprocess", fake_run_subprocess)
    monkeypatch.setattr(agy_runner, "resolve_agy_environment", lambda force=False: {})

    # Explicit 300 wall budget
    run_agy("test 300", timeout=300)
    args_300, timeout_300 = captured_calls[-1]
    assert timeout_300 == 300  # Outer budget retains 300
    idx = args_300.index("--print-timeout")
    assert args_300[idx + 1] == "240s"  # Derived print timeout strictly below outer budget

    # Explicit 1200 wall budget
    run_agy("test 1200", timeout=1200)
    args_1200, timeout_1200 = captured_calls[-1]
    assert timeout_1200 == 1200
    idx = args_1200.index("--print-timeout")
    assert args_1200[idx + 1] == "900s"

    # Explicit 3600 wall budget
    run_agy("test 3600", timeout=3600)
    args_3600, timeout_3600 = captured_calls[-1]
    assert timeout_3600 == 3600
    idx = args_3600.index("--print-timeout")
    assert args_3600[idx + 1] == "900s"


def test_run_agy_argv_explicit_print_timeout_override(monkeypatch):
    monkeypatch.setattr(agy_runner, "find_agy", lambda: "fake_agy")
    captured_calls = []

    def fake_run_subprocess(args, workdir, timeout, env=None, **kwargs):
        captured_calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="done", stderr="")

    monkeypatch.setattr(agy_runner, "_run_subprocess", fake_run_subprocess)
    monkeypatch.setattr(agy_runner, "resolve_agy_environment", lambda force=False: {})

    run_agy("test explicit numeric", print_timeout=600)
    args = captured_calls[-1]
    idx = args.index("--print-timeout")
    assert args[idx + 1] == "600s"

    run_agy("test explicit duration string", print_timeout="15m")
    args = captured_calls[-1]
    idx = args.index("--print-timeout")
    assert args[idx + 1] == "15m"


def test_run_agy_visible_argv_deterministic(monkeypatch):
    if sys.platform != "win32":
        monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(agy_runner, "find_agy", lambda: "fake_agy")
    monkeypatch.setattr(agy_runner, "resolve_agy_environment", lambda force=False: {})

    captured_args = []

    class FakePopen:
        def __init__(self, args, **kwargs):
            captured_args.append(args)

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(subprocess, "Popen", FakePopen)

    res = run_agy_visible("visible task")
    assert res.exit_code == 0
    args = captured_args[-1]
    assert "--print-timeout" in args
    idx = args.index("--print-timeout")
    assert args[idx + 1] == "900s"

    run_agy_visible("visible 300", timeout=300)
    args = captured_args[-1]
    idx = args.index("--print-timeout")
    assert args[idx + 1] == "240s"


def test_classify_agy_error_print_timeout_taxonomy():
    print_timeout_samples = [
        "timeout waiting for response",
        "error: timeout waiting for response from agent",
        "print-mode timeout exceeded",
        "print mode timeout after 900s",
        "printmode timeout",
        "print_timeout occurred",
        "agy_print_timeout: backend unresponsive",
    ]
    for sample in print_timeout_samples:
        kind = classify_agy_error(sample)
        assert kind == AGY_PRINT_TIMEOUT, f"Expected AGY_PRINT_TIMEOUT for: {sample!r}, got {kind!r}"

    # Verify real proxy errors still classify as network
    real_proxy_samples = [
        "dial tcp 127.0.0.1:7890: connectex: connection refused",
        "proxyconnect tcp: dial tcp 127.0.0.1:7890: connect: connection refused",
        "failed to fetch response from proxy",
    ]
    for sample in real_proxy_samples:
        kind = classify_agy_error(sample)
        assert kind == "network", f"Expected network for: {sample!r}, got {kind!r}"


def test_agy_print_timeout_does_not_trigger_proxy_refresh_or_proxy_classification(monkeypatch):
    monkeypatch.setattr(agy_runner, "find_agy", lambda: "fake_agy")
    force_calls = []

    def fake_resolve(force=False):
        force_calls.append(force)
        return {}

    monkeypatch.setattr(agy_runner, "resolve_agy_environment", fake_resolve)

    def fake_run_subprocess(args, workdir, timeout, env=None, **kwargs):
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="timeout waiting for response")

    monkeypatch.setattr(agy_runner, "_run_subprocess", fake_run_subprocess)

    result = run_agy("long running task")
    assert result.exit_code == 1
    assert True not in force_calls, "AGY_PRINT_TIMEOUT must NOT trigger proxy rediscovery / force refresh"

    desc = describe_agy_failure(result)
    assert desc.startswith("AGY_TIMEOUT:"), f"Expected AGY_TIMEOUT description, got: {desc}"
    assert "AGY_PROXY_ERROR" not in desc
