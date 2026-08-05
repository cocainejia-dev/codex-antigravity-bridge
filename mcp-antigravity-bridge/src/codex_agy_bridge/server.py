"""MCP server exposing Google Antigravity (`agy`) as tools for Codex."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .agy_runner import run_agy

mcp = FastMCP(
    "codex-agy-bridge",
    instructions=(
        "Bridge from Codex to the Google Antigravity agent. "
        "Use agy_ask for a one-shot headless call (`agy -p`); "
        "use agy_ask_json when you want structured JSON output."
    ),
)


@mcp.tool()
def agy_ask(prompt: str, workdir: str = "", timeout: float = 300.0) -> str:
    """Ask the Google Antigravity agent headlessly and return its text answer.

    Args:
        prompt: The task/instruction for the Antigravity agent.
        workdir: Optional working directory for the agy process ("" = inherit).
        timeout: Hard wall-clock timeout in seconds.
    """
    result = run_agy(prompt, workdir=workdir or None, timeout=timeout)
    return result.text


@mcp.tool()
def agy_ask_json(prompt: str, workdir: str = "", timeout: float = 300.0) -> str:
    """Ask the Google Antigravity agent and return structured JSON.

    Uses `agy -p <prompt> --output-format json`.
    """
    result = run_agy(
        prompt,
        workdir=workdir or None,
        timeout=timeout,
        output_format="json",
    )
    return result.text