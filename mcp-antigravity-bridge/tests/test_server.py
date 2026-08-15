from __future__ import annotations

import pytest

from codex_agy_bridge import server
from codex_agy_bridge.agy_runner import AgyResult


def test_agy_ask_json_rejects_unparseable_output(monkeypatch):
    monkeypatch.setattr(
        server,
        "run_agy",
        lambda *args, **kwargs: AgyResult(text="not json", exit_code=0),
    )

    with pytest.raises(ValueError, match="valid JSON"):
        server.agy_ask_json("Return JSON")


def test_agy_ask_rejects_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        server,
        "run_agy",
        lambda *args, **kwargs: AgyResult(
            text="authentication failed", exit_code=7, stderr="authentication failed"
        ),
    )

    with pytest.raises(RuntimeError, match="AGY_LOGIN_REQUIRED.*authentication failed"):
        server.agy_ask("Say hi")


def test_agy_ask_propagates_runner_timeout(monkeypatch):
    def timed_out(*args, **kwargs):
        raise TimeoutError("agy timed out after 1s")

    monkeypatch.setattr(server, "run_agy", timed_out)

    with pytest.raises(TimeoutError, match="agy timed out"):
        server.agy_ask("Say hi", timeout=1)


def test_agy_ask_reports_proxy_failure_without_requesting_login(monkeypatch):
    monkeypatch.setattr(
        server,
        "run_agy",
        lambda *args, **kwargs: AgyResult(
            text="token exchange failed: dial tcp 74.125.195.95:443",
            exit_code=7,
        ),
    )

    with pytest.raises(RuntimeError, match="AGY_PROXY_ERROR") as exc_info:
        server.agy_ask("Say hi")

    assert "login" not in str(exc_info.value).lower()


def test_agy_ask_reports_authentication_failure_as_login_required(monkeypatch):
    monkeypatch.setattr(
        server,
        "run_agy",
        lambda *args, **kwargs: AgyResult(
            text="OAuth token invalid; authentication required",
            exit_code=7,
        ),
    )

    with pytest.raises(RuntimeError, match="AGY_LOGIN_REQUIRED"):
        server.agy_ask("Say hi")


def test_agy_ask_json_returns_parseable_json(monkeypatch):
    monkeypatch.setattr(
        server,
        "run_agy",
        lambda *args, **kwargs: AgyResult(text='{"ok": true}', exit_code=0),
    )

    assert server.agy_ask_json("Return JSON") == '{"ok": true}'


def test_agy_start_requires_explicit_workdir():
    with pytest.raises(ValueError, match="workdir"):
        server.agy_start("Implement the task")


def test_public_tools_reject_nonpositive_timeouts(tmp_path):
    with pytest.raises(ValueError, match="positive finite number"):
        server.agy_ask("Say hi", timeout=0)

    with pytest.raises(ValueError, match="positive finite number"):
        server.agy_ask_json("Return JSON", timeout=-1)

    with pytest.raises(ValueError, match="positive finite number"):
        server.agy_start("Implement the task", workdir=str(tmp_path), timeout=0)

    with pytest.raises(ValueError, match="positive finite number"):
        server.agy_ask("Say hi", timeout=float("nan"))
