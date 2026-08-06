"""FastMCP server exposing the Antigravity Python SDK to MCP clients."""

from __future__ import annotations

from fastmcp import FastMCP

from antigravity_mcp.agy_runner import AgyError, AgyRunner

mcp = FastMCP("antigravity")


@mcp.tool()
def run_agy(
    prompt: str,
    cwd: str = "",
    api_key: str = "",
    model: str = "",
) -> str:
    """Run a prompt through the Google Antigravity Python SDK.

    Args:
        prompt: The user prompt to send to Antigravity.
        cwd: Optional SDK workspace root. Empty means use the SDK default.
        api_key: Optional Gemini API key override.
        model: Optional model identifier. Empty means use the SDK default.

    Returns:
        The Antigravity response text, or a readable SDK error.
    """
    runner = AgyRunner(api_key=api_key or None, model=model or None)
    try:
        result = runner.run_prompt(prompt, cwd=cwd or None)
    except AgyError as exc:
        return f"agy error: {exc}"
    return result.text


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
