"""FastMCP server exposing the Antigravity CLI (agy -p) to MCP clients like Codex."""

from __future__ import annotations

from fastmcp import FastMCP

from antigravity_mcp.agy_runner import AgyError, AgyRunner

mcp = FastMCP("antigravity")


@mcp.tool()
def run_agy(prompt: str, cwd: str = "") -> str:
    """Run a prompt through the Google Antigravity CLI in headless mode (agy -p).

    Args:
        prompt: The user prompt to send to Antigravity.
        cwd: Optional working directory to run the CLI in. Empty string means
            inherit the server's current working directory.

    Returns:
        The Antigravity response text.
    """
    runner = AgyRunner()
    try:
        result = runner.run_prompt(prompt, cwd=cwd or None)
    except AgyError as exc:
        return f"agy error: {exc}"
    return result.text


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
