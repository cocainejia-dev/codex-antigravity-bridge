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
