from unittest import mock

from antigravity_mcp.agy_runner import AgyError, AgyResult
from antigravity_mcp.server import run_agy


def test_run_agy_tool_returns_text():
    with mock.patch("antigravity_mcp.server.AgyRunner") as runner_cls:
        runner_cls.return_value.run_prompt.return_value = AgyResult(text="hello antigravity")
        assert run_agy("Say hi") == "hello antigravity"
    assert runner_cls.call_args.kwargs == {"api_key": None, "model": None}
    assert runner_cls.return_value.run_prompt.call_args.kwargs["cwd"] is None


def test_run_agy_tool_passes_sdk_options_and_cwd():
    with mock.patch("antigravity_mcp.server.AgyRunner") as runner_cls:
        runner_cls.return_value.run_prompt.return_value = AgyResult(text="ok")
        run_agy("List files", cwd=r"C:\work", api_key="k", model="gemini-test")

    assert runner_cls.call_args.kwargs == {"api_key": "k", "model": "gemini-test"}
    assert runner_cls.return_value.run_prompt.call_args.kwargs["cwd"] == r"C:\work"


def test_run_agy_tool_surfaces_sdk_errors():
    with mock.patch("antigravity_mcp.server.AgyRunner") as runner_cls:
        runner_cls.return_value.run_prompt.side_effect = AgyError("sdk failed")
        assert "sdk failed" in run_agy("Q")
