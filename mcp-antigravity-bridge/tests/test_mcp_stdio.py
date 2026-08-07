"""Real MCP stdio smoke test; no Antigravity login is required."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"


async def _list_tools() -> set[str]:
    env = dict(os.environ)
    current_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = os.pathsep.join(
        value for value in (str(SOURCE), current_pythonpath) if value
    )
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "codex_agy_bridge"],
        cwd=ROOT,
        env=env,
    )

    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await asyncio.wait_for(session.initialize(), timeout=15)
            result = await asyncio.wait_for(session.list_tools(), timeout=15)
            return {tool.name for tool in result.tools}


def test_mcp_stdio_server_lists_all_tools() -> None:
    assert asyncio.run(_list_tools()) == {
        "agy_ask",
        "agy_ask_json",
        "agy_start",
        "agy_status",
    }
