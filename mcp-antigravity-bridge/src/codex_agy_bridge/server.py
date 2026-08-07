"""MCP server exposing Google Antigravity (`agy`) as tools for Codex."""

from __future__ import annotations

import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .agy_jobs import agy_jobs
from .agy_runner import AgyResult, run_agy

mcp = FastMCP(
    "codex-agy-bridge",
    instructions=(
        "Bridge from Codex to the Google Antigravity agent. "
        "Use agy_ask for a one-shot headless call (`agy -p`); "
        "use agy_ask_json when you want structured JSON output; "
        "use agy_start and agy_status for explicit asynchronous worktree collaboration "
        "with a caller-created isolated workdir. "
        "Only pass dangerously_skip_permissions=true after the user explicitly "
        "authorizes that exact trusted worktree and task."
    ),
)


def _require_success(result: AgyResult) -> AgyResult:
    if result.exit_code != 0:
        detail = result.text or result.stderr or "agy returned no diagnostic output"
        raise RuntimeError(f"agy exited with code {result.exit_code}: {detail}")
    return result


@mcp.tool()
def agy_ask(
    prompt: str,
    workdir: str = "",
    timeout: float = 300.0,
    dangerously_skip_permissions: bool = False,
) -> str:
    """Ask the Google Antigravity agent headlessly and return its text answer.

    Args:
        prompt: The task/instruction for the Antigravity agent.
        workdir: Optional working directory for the agy process ("" = inherit).
        timeout: Hard wall-clock timeout in seconds.
        dangerously_skip_permissions: Allow agy tools without interactive prompts.
    """
    result = run_agy(
        prompt,
        workdir=workdir or None,
        timeout=timeout,
        dangerously_skip_permissions=dangerously_skip_permissions,
    )
    return _require_success(result).text


@mcp.tool()
def agy_ask_json(
    prompt: str,
    workdir: str = "",
    timeout: float = 300.0,
    dangerously_skip_permissions: bool = False,
) -> str:
    """Ask the Google Antigravity agent and return structured JSON.

    Uses `agy -p <prompt> --output-format json`.
    """
    result = run_agy(
        prompt,
        workdir=workdir or None,
        timeout=timeout,
        output_format="json",
        dangerously_skip_permissions=dangerously_skip_permissions,
    )
    _require_success(result)
    try:
        json.loads(result.text)
    except json.JSONDecodeError as exc:
        raise ValueError("agy_ask_json did not return valid JSON") from exc
    return result.text


@mcp.tool()
def agy_start(
    prompt: str,
    workdir: str = "",
    timeout: float = 300.0,
    dangerously_skip_permissions: bool = False,
) -> str:
    """Start an asynchronous agy task and return its job id.

    Use this for explicit parallel worktree collaboration. The caller must
    provide an existing isolated worktree as workdir; the bridge does not
    create one. Poll the returned id with ``agy_status`` while Codex continues
    work elsewhere.
    """
    if not workdir.strip():
        raise ValueError(
            "agy_start requires an explicit workdir for a caller-created isolated worktree"
        )
    if not Path(workdir).expanduser().is_dir():
        raise ValueError(f"agy_start workdir is not an existing directory: {workdir}")

    return agy_jobs.start(
        prompt,
        workdir=workdir or None,
        timeout=timeout,
        dangerously_skip_permissions=dangerously_skip_permissions,
    )


@mcp.tool()
def agy_status(job_id: str) -> str:
    """Return JSON status for an asynchronous agy task."""
    import json

    return json.dumps(agy_jobs.status(job_id), ensure_ascii=False)
