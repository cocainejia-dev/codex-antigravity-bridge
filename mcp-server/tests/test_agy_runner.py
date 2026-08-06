import asyncio
from unittest import mock

import pytest

from antigravity_mcp import agy_runner
from antigravity_mcp.agy_runner import AgyError, AgyResult, AgyRunner, AgyTimeoutError


class FakeResponse:
    async def text(self):
        return "hello antigravity"


class FakeAgent:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def chat(self, prompt):
        assert prompt == "Say hi"
        return FakeResponse()


def test_run_prompt_returns_sdk_text():
    with mock.patch.object(agy_runner, "LocalAgentConfig") as config_cls:
        with mock.patch.object(agy_runner, "Agent", return_value=FakeAgent()):
            result = AgyRunner().run_prompt("Say hi")

    assert isinstance(result, AgyResult)
    assert result.text == "hello antigravity"
    assert result.exit_code == 0
    config_cls.assert_called_once_with()


def test_run_prompt_passes_workspace_api_key_and_model():
    with mock.patch.object(agy_runner, "LocalAgentConfig") as config_cls:
        with mock.patch.object(agy_runner, "Agent", return_value=FakeAgent()):
            AgyRunner(api_key="secret", model="gemini-test").run_prompt(
                "Say hi", cwd=r"C:\work"
            )

    assert config_cls.call_args.kwargs == {
        "workspaces": [r"C:\work"],
        "api_key": "secret",
        "model": "gemini-test",
    }


def test_run_prompt_rejects_empty_prompt():
    with pytest.raises(AgyError, match="prompt must not be empty"):
        AgyRunner().run_prompt("  ")


def test_run_prompt_raises_on_empty_response():
    class EmptyResponse(FakeResponse):
        async def text(self):
            return ""

    class EmptyAgent(FakeAgent):
        async def chat(self, prompt):
            return EmptyResponse()

    with mock.patch.object(agy_runner, "Agent", return_value=EmptyAgent()):
        with pytest.raises(AgyError, match="empty response"):
            AgyRunner().run_prompt("Q")


def test_run_prompt_raises_on_timeout():
    async def slow_call(prompt, cwd, api_key, model):
        await asyncio.sleep(0.05)
        return "too late"

    with mock.patch.object(agy_runner, "_run_sdk", side_effect=slow_call):
        with pytest.raises(AgyTimeoutError):
            AgyRunner(timeout_seconds=0.001).run_prompt("Q")


def test_run_prompt_wraps_sdk_errors():
    async def failing_call(prompt, cwd, api_key, model):
        raise RuntimeError("connection failed")

    with mock.patch.object(agy_runner, "_run_sdk", side_effect=failing_call):
        with pytest.raises(AgyError, match="connection failed"):
            AgyRunner().run_prompt("Q")


def test_run_prompt_works_inside_an_existing_event_loop():
    async def fake_call(prompt, cwd, api_key, model):
        return "nested loop response"

    async def invoke_from_loop():
        with mock.patch.object(agy_runner, "_run_sdk", side_effect=fake_call):
            return AgyRunner().run_prompt("Q").text

    assert asyncio.run(invoke_from_loop()) == "nested loop response"
