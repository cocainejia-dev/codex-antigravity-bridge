import json
import subprocess
from types import SimpleNamespace
from unittest import mock

import pytest

from antigravity_mcp.agy_runner import AgyError, AgyResult, AgyRunner, AgyTimeoutError


def _completed(stdout, stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def _ok_json(text="hello antigravity"):
    return json.dumps({"status": "OK", "response": text, "num_turns": 1})


def test_run_prompt_returns_text():
    with mock.patch("antigravity_mcp.agy_runner.subprocess.run", return_value=_completed(_ok_json())) as run:
        result = AgyRunner(agy_binary="agy").run_prompt("Say hi")
    assert result.text == "hello antigravity"
    assert result.exit_code == 0
    cmd = run.call_args.args[0]
    assert cmd[:2] == ["agy", "-p"]


def test_run_prompt_passes_cwd():
    with mock.patch("antigravity_mcp.agy_runner.subprocess.run", return_value=_completed(_ok_json("ok"))) as run:
        AgyRunner(agy_binary="agy").run_prompt("Q", cwd=r"C:\work")
    assert run.call_args.kwargs["cwd"] == r"C:\work"


def test_run_prompt_no_cwd_means_none():
    with mock.patch("antigravity_mcp.agy_runner.subprocess.run", return_value=_completed(_ok_json("ok"))) as run:
        AgyRunner(agy_binary="agy").run_prompt("Q")
    assert run.call_args.kwargs["cwd"] is None


def test_run_prompt_exposes_raw_json():
    raw = {"status": "OK", "response": "done", "usage": {"total_tokens": 42}}
    with mock.patch("antigravity_mcp.agy_runner.subprocess.run", return_value=_completed(json.dumps(raw))):
        result = AgyRunner(agy_binary="agy").run_prompt("Q")
    assert result.raw == raw


def test_run_prompt_raises_on_error_status():
    bad = json.dumps({"status": "ERROR", "error": "something broke"})
    with mock.patch("antigravity_mcp.agy_runner.subprocess.run", return_value=_completed(bad)):
        with pytest.raises(AgyError) as exc:
            AgyRunner(agy_binary="agy").run_prompt("Q")
    assert "something broke" in str(exc.value)


def test_run_prompt_raises_on_auth_error():
    bad = json.dumps({"status": "ERROR", "error": "authentication failed or timed out"})
    with mock.patch("antigravity_mcp.agy_runner.subprocess.run", return_value=_completed(bad)):
        with pytest.raises(AgyError) as exc:
            AgyRunner(agy_binary="agy").run_prompt("Q")
    assert "authentication required" in str(exc.value)


def test_run_prompt_raises_on_empty_response():
    with mock.patch("antigravity_mcp.agy_runner.subprocess.run", return_value=_completed("")):
        with pytest.raises(AgyError) as exc:
            AgyRunner(agy_binary="agy").run_prompt("Q")
    assert "empty" in str(exc.value).lower()


def test_run_prompt_raises_on_timeout():
    with mock.patch("antigravity_mcp.agy_runner.subprocess.run", side_effect=subprocess.TimeoutExpired("agy", 1)):
        with pytest.raises(AgyTimeoutError):
            AgyRunner(timeout_seconds=1, agy_binary="agy").run_prompt("Q")


def test_run_prompt_raises_when_binary_missing():
    with mock.patch("antigravity_mcp.agy_runner.subprocess.run", side_effect=OSError("no such file")):
        with pytest.raises(AgyError) as exc:
            AgyRunner(agy_binary="no-such-agy").run_prompt("Q")
    assert "failed to start" in str(exc.value)


def test_find_agy_prefers_explicit_binary():
    from antigravity_mcp.agy_runner import find_agy
    assert find_agy("custom-agy") == "custom-agy"
