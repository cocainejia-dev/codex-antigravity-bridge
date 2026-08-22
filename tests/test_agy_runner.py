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
    CONNECT_TIMEOUT,
    LOCAL_SUPERVISION_TIMEOUT,
    REMOTE_EXECUTION_TIMEOUT,
    AgyResult,
    LocalSupervisionTimeoutError,
    classify_agy_error,
    describe_agy_failure,
    run_agy,
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
