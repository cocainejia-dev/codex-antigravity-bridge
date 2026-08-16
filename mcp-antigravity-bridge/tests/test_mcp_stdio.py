"""Real MCP stdio smoke test; no Antigravity login is required."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys

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
        "agy_wait",
        "agy_collab_start",
        "agy_collab_status",
        "agy_jobs_recent",
    }


async def _call_status_stdio() -> dict:
    import json
    import time

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
            start_t = time.monotonic()
            res_status = await asyncio.wait_for(
                session.call_tool("agy_status", {"job_id": "non-existent-job-id"}),
                timeout=10,
            )
            elapsed = time.monotonic() - start_t
            status_text = "\n".join(getattr(item, "text", "") for item in res_status.content)
            data = json.loads(status_text)
            assert data.get("state") == "unknown"
            assert elapsed < 2.0
            return data


async def _call_wait_stdio() -> dict:
    import json
    import time

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
            start_t = time.monotonic()
            res_unknown = await asyncio.wait_for(
                session.call_tool("agy_wait", {"job_id": "non-existent-job-id", "wait_seconds": 2.0}),
                timeout=10,
            )
            elapsed = time.monotonic() - start_t
            text_unknown = "\n".join(getattr(item, "text", "") for item in res_unknown.content)
            data_unknown = json.loads(text_unknown)
            assert data_unknown.get("state") == "unknown"
            assert elapsed < 5.0
            return data_unknown


def test_mcp_stdio_agy_status_responsive() -> None:
    result = asyncio.run(_call_status_stdio())
    assert result["state"] == "unknown"


def test_mcp_stdio_agy_wait_responsive() -> None:
    result = asyncio.run(_call_wait_stdio())
    assert result["state"] == "unknown"


async def _call_session_sequence_stdio() -> None:
    import json
    import time

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
            # Call status
            t0 = time.monotonic()
            res_status = await asyncio.wait_for(
                session.call_tool("agy_status", {"job_id": "job-probe-1"}),
                timeout=10,
            )
            assert time.monotonic() - t0 < 2.0
            data_status = json.loads("\n".join(getattr(i, "text", "") for i in res_status.content))
            assert data_status.get("state") == "unknown"

            # Call recent
            t1 = time.monotonic()
            res_recent = await asyncio.wait_for(
                session.call_tool("agy_jobs_recent", {"limit": 5}),
                timeout=10,
            )
            assert time.monotonic() - t1 < 2.0
            data_recent = json.loads("\n".join(getattr(i, "text", "") for i in res_recent.content))
            assert isinstance(data_recent, list)


def test_mcp_stdio_session_sequence_responsive() -> None:
    asyncio.run(_call_session_sequence_stdio())
