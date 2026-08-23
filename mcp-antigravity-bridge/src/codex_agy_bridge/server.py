"""MCP server exposing Google Antigravity (`agy`) as tools for Codex."""

from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path
import threading
import time
from typing import Any

from mcp.server.fastmcp import FastMCP

from .agy_collaboration import agy_collaborations
from .agy_jobs import agy_jobs
from .agy_runner import AgyResult, classify_agy_error, describe_agy_failure, run_agy
from .contracts import (
    AutoCommitPolicy,
    InvalidStateTransitionError,
    RiskClass,
    RunRecord,
    RunState,
    TASK_WALL_CLOCK_BUDGET,
    TaskContract,
)
from .run_control import (
    ConcurrentModificationError,
    CredentialSecurityError,
    DurableRunManager,
    DurableRunStore,
    DuplicateRunError,
    RunControlError,
    RunNotFoundError,
    RunNotTerminalError,
    WorkerResult,
)
from .timeout_diagnostics import evaluate_timeout_diagnostics
from .recovery import RecoveryOrchestrator
from .worker_binding import build_worker_callback

_run_resume_lock = threading.Lock()

mcp = FastMCP(
    "codex-agy-bridge",
    instructions=(
        "Bridge from Codex to the Google Antigravity agent and VNext durable run control. "
        "Use agy_ask for a one-shot headless call (`agy -p`); "
        "use agy_ask_json when you want structured JSON output; "
        "use agy_start, agy_status, and agy_wait for explicit asynchronous worktree collaboration "
        "with a caller-created isolated workdir; "
        "use agy_jobs_recent to inspect durable task history; "
        "use agy_collab_start and agy_collab_status for the MVP collaboration mode: "
        "the bridge creates separate Git worktrees and starts bounded tasks, but "
        "Codex reviews and merges branches manually; "
        "use run_start, run_status, run_observe, run_wait, run_result, and run_cancel for VNext durable runs. "
        "Only pass dangerously_skip_permissions=true after the user explicitly "
        "authorizes that exact trusted worktree and task."
    ),
)


def _require_success(result: AgyResult) -> AgyResult:
    if result.exit_code != 0:
        raise RuntimeError(describe_agy_failure(result))
    return result


def _validate_timeout(timeout: float) -> float:
    """Reject invalid MCP timeouts before starting a subprocess or job."""
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(float(timeout))
        or timeout <= 0
    ):
        raise ValueError("timeout must be a positive finite number")
    return float(timeout)


def _validate_wait_seconds(wait_seconds: float) -> float:
    """Reject invalid wait_seconds before waiting on an async task."""
    if (
        isinstance(wait_seconds, bool)
        or not isinstance(wait_seconds, (int, float))
        or not math.isfinite(float(wait_seconds))
        or wait_seconds <= 0
    ):
        raise ValueError("wait_seconds must be a positive finite number")
    return float(wait_seconds)


def _validate_limit(limit: int) -> int:
    """Reject invalid limit before querying recent task history."""
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or limit < 1
        or limit > 100
    ):
        raise ValueError("limit must be an integer between 1 and 100")
    return limit


def _validate_positive_finite(val: float, name: str = "value") -> float:
    """Reject invalid numeric values (bool, NaN, Infinity, non-positive)."""
    if (
        isinstance(val, bool)
        or not isinstance(val, (int, float))
        or not math.isfinite(float(val))
        or val <= 0
    ):
        raise ValueError(f"{name} must be a positive finite number")
    return float(val)


def _validate_db_path(db_path: str) -> str:
    """Validate that db_path is an explicit non-empty path under caller control."""
    if isinstance(db_path, bool) or not isinstance(db_path, str):
        raise ValueError("db_path must be a non-empty string path")
    cleaned = db_path.strip()
    if not cleaned:
        raise ValueError("db_path must be a non-empty string path")
    path = Path(cleaned).expanduser()
    if path.is_dir():
        raise ValueError(f"db_path cannot be a directory: {cleaned}")
    return cleaned


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
    valid_timeout = _validate_timeout(timeout)
    t0 = time.monotonic()
    result: AgyResult | None = None
    try:
        result = run_agy(
            prompt,
            workdir=workdir or None,
            timeout=valid_timeout,
            dangerously_skip_permissions=dangerously_skip_permissions,
        )
        return _require_success(result).text
    finally:
        try:
            from .telemetry_hooks import record_oneshot_call_event
            record_oneshot_call_event(
                prompt=prompt,
                workdir=workdir or None,
                duration_seconds=max(0.0, time.monotonic() - t0),
                exit_code=result.exit_code if result is not None else 1,
                error_kind=classify_agy_error(result.text, result.stderr) if (result is not None and result.exit_code != 0) else None,
            )
        except Exception:
            pass


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
    valid_timeout = _validate_timeout(timeout)
    t0 = time.monotonic()
    result: AgyResult | None = None
    try:
        result = run_agy(
            prompt,
            workdir=workdir or None,
            timeout=valid_timeout,
            output_format="json",
            dangerously_skip_permissions=dangerously_skip_permissions,
        )
        _require_success(result)
        try:
            json.loads(result.text)
        except json.JSONDecodeError as exc:
            raise ValueError("agy_ask_json did not return valid JSON") from exc
        return result.text
    finally:
        try:
            from .telemetry_hooks import record_oneshot_call_event
            record_oneshot_call_event(
                prompt=prompt,
                workdir=workdir or None,
                duration_seconds=max(0.0, time.monotonic() - t0),
                exit_code=result.exit_code if result is not None else 1,
                error_kind=classify_agy_error(result.text, result.stderr) if (result is not None and result.exit_code != 0) else None,
            )
        except Exception:
            pass


@mcp.tool()
def agy_start(
    prompt: str,
    workdir: str = "",
    timeout: float = float(TASK_WALL_CLOCK_BUDGET),
    dangerously_skip_permissions: bool = False,
    task_key: str | None = None,
) -> str:
    """Start an asynchronous agy task and return its job id.

    Use this for explicit parallel worktree collaboration. The caller must
    provide an existing isolated worktree as workdir; the bridge does not
    create one. Poll the returned id with ``agy_status`` or wait with ``agy_wait``
    while Codex continues work elsewhere.
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
        timeout=_validate_timeout(timeout),
        dangerously_skip_permissions=dangerously_skip_permissions,
        task_key=task_key,
    )


@mcp.tool()
async def agy_status(job_id: str) -> str:
    """Return JSON status for an asynchronous agy task."""
    result = await asyncio.to_thread(agy_jobs.status, job_id)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
async def agy_wait(
    job_id: str,
    wait_seconds: float = 120.0,
) -> str:
    """Wait for an asynchronous agy task to complete within a bounded duration.

    Returns JSON status. If the job is unknown, completed, or failed, it returns
    immediately. If queued or running, it waits up to `wait_seconds` for completion.
    If the wait expires before completion, the current active status is returned
    without cancelling or killing the background task.
    """
    valid_wait = _validate_wait_seconds(wait_seconds)
    result = await asyncio.to_thread(agy_jobs.wait, job_id, wait_seconds=valid_wait)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def agy_jobs_recent(
    limit: int = 20,
    task_key: str = "",
    state: str = "",
) -> str:
    """Return a newest-first summary of recent asynchronous agy tasks.

    Args:
        limit: Maximum number of recent jobs to return (1..100, default 20).
        task_key: Optional filter by task key.
        state: Optional filter by job state (e.g. 'completed', 'failed', 'running').
    """
    valid_limit = _validate_limit(limit)
    records = agy_jobs.recent(limit=valid_limit, task_key=task_key, state=state)
    return json.dumps(records, ensure_ascii=False)


@mcp.tool()
def agy_collab_start(
    project_dir: str,
    tasks: list[dict[str, Any]],
    shared_contract: str = "",
    base_ref: str = "HEAD",
    worktree_root: str = "",
    timeout: float = 900.0,
    dangerously_skip_permissions: bool = False,
    display_mode: str = "headless",
    max_tasks: int = 4,
    dry_run: bool = False,
) -> str:
    """Start an explicit multi-task collaboration session.

    ``tasks`` must contain objects with ``id``, ``prompt``, ``owned_paths``,
    and ``acceptance`` fields. Each task receives its own Git worktree and
    branch. ``display_mode='terminal'`` opens one visible Windows console per
    task. ``max_tasks`` defaults to four and is a hard upper bound for this
    session. The result is ready for manual review; this tool never merges.
    """
    result = agy_collaborations.start(
        project_dir=project_dir,
        tasks=tasks,
        shared_contract=shared_contract,
        base_ref=base_ref,
        worktree_root=worktree_root,
        timeout=_validate_timeout(timeout),
        dangerously_skip_permissions=dangerously_skip_permissions,
        display_mode=display_mode,
        max_tasks=max_tasks,
        dry_run=dry_run,
    )
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def agy_collab_status(session_id: str) -> str:
    """Return aggregated status for an MVP collaboration session."""
    return json.dumps(agy_collaborations.status(session_id), ensure_ascii=False)


@mcp.tool()
def run_start(
    db_path: str,
    task: dict[str, Any],
    idempotency_key: str | None = None,
    run_id: str | None = None,
    auto_spawn: bool = False,
    worktree: str | None = None,
    repo: str | None = None,
    base_head: str | None = None,
    attempt: int = 0,
    repair_round: int = 0,
    dangerously_skip_permissions: bool = False,
) -> str:
    """Start a durable run tracking a VNext TaskContract specification.

    Persists an initial CREATED RunRecord into the caller-specified db_path.
    Auto_spawn is false by default because MCP JSON cannot carry Python callbacks;
    execution wiring is internal.
    """
    return _run_start_impl(
        db_path=db_path,
        task=task,
        idempotency_key=idempotency_key,
        run_id=run_id,
        auto_spawn=auto_spawn,
        worktree=worktree,
        repo=repo,
        base_head=base_head,
        attempt=attempt,
        repair_round=repair_round,
        dangerously_skip_permissions=dangerously_skip_permissions,
    )


def _run_start_impl(
    db_path: str,
    task: dict[str, Any],
    idempotency_key: str | None = None,
    run_id: str | None = None,
    auto_spawn: bool = False,
    worktree: str | None = None,
    repo: str | None = None,
    base_head: str | None = None,
    attempt: int = 0,
    repair_round: int = 0,
    dangerously_skip_permissions: bool = False,
    *,
    worker_factory=None,
) -> str:
    """Internal run-start seam; callable values never enter the public MCP schema."""
    if worker_factory is None:
        worker_factory = build_worker_callback
    valid_db_path = _validate_db_path(db_path)
    if isinstance(task, str):
        try:
            task = json.loads(task)
        except Exception as exc:
            raise ValueError("task must be a valid dict or JSON object") from exc
    if not isinstance(task, dict):
        raise ValueError("task must be a dictionary representing a TaskContract")

    contract = TaskContract.from_dict(task)
    if dangerously_skip_permissions:
        worker = worker_factory(contract, dangerously_skip_permissions=True)
    else:
        worker = worker_factory(contract)
    worker_identity = {
        "dangerously_skip_permissions": bool(dangerously_skip_permissions),
    }
    manager = DurableRunManager(valid_db_path)
    record = manager.run_start(
        contract,
        idempotency_key=idempotency_key or None,
        worker=worker,
        run_id=run_id or None,
        auto_spawn=True,
        worktree=worktree or None,
        repo=repo or None,
        base_head=base_head or None,
        attempt=attempt,
        repair_round=repair_round,
        worker_identity=worker_identity,
    )
    return json.dumps(record.to_dict(), ensure_ascii=False)


@mcp.tool()
def run_status(
    db_path: str,
    run_id: str,
) -> str:
    """Return durable RunRecord JSON for a run."""
    valid_db_path = _validate_db_path(db_path)
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id must be a non-empty string")
    manager = DurableRunManager(valid_db_path)
    record = manager.run_status(run_id.strip())
    payload = record.to_dict()
    payload["timeout_diagnostic"] = evaluate_timeout_diagnostics(
        error_text=record.last_error or "",
        context={"state": record.state.value, "is_alive": record.state not in {RunState.COMPLETE, RunState.FAILED, RunState.CANCELLED}},
    ).to_dict()
    return json.dumps(payload, ensure_ascii=False)


@mcp.tool()
def run_observe(
    db_path: str,
    run_id: str,
    stale_heartbeat_threshold_seconds: float = 60.0,
) -> str:
    """Observe run status, checking process and heartbeat liveness and exposing recovery state."""
    valid_db_path = _validate_db_path(db_path)
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id must be a non-empty string")
    valid_threshold = _validate_positive_finite(stale_heartbeat_threshold_seconds, "stale_heartbeat_threshold_seconds")
    manager = DurableRunManager(valid_db_path)
    obs = manager.run_observe(run_id.strip(), stale_heartbeat_threshold_seconds=valid_threshold)
    obs_dict = {
        "run_id": obs.run_id,
        "state": obs.state.value,
        "state_version": obs.state_version,
        "is_terminal": obs.is_terminal,
        "is_alive": obs.is_alive,
        "is_stale": obs.is_stale,
        "pid": obs.pid,
        "heartbeat": obs.heartbeat,
        "recovery_state": obs.recovery_state.value if obs.recovery_state else None,
        "reason": obs.reason,
        "record": obs.record.to_dict(),
    }
    if obs.timeout_diagnostic is not None:
        obs_dict["timeout_diagnostic"] = obs.timeout_diagnostic
    else:
        obs_dict["timeout_diagnostic"] = evaluate_timeout_diagnostics(
            error_text=obs.reason or obs.record.last_error or "",
            worker_alive=obs.is_alive,
            context={"state": obs.state.value, "is_alive": obs.is_alive},
        ).to_dict()
    return json.dumps(obs_dict, ensure_ascii=False)


@mcp.tool()
def run_wait(
    db_path: str,
    run_id: str,
    timeout: float = 120.0,
    poll_interval: float = 0.05,
) -> str:
    """Wait for a run to reach a terminal state within a bounded timeout without cancelling."""
    valid_db_path = _validate_db_path(db_path)
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id must be a non-empty string")
    valid_timeout = _validate_timeout(timeout)
    valid_poll = _validate_positive_finite(poll_interval, "poll_interval")
    manager = DurableRunManager(valid_db_path)
    record = manager.run_wait(run_id.strip(), timeout=valid_timeout, poll_interval=valid_poll)
    return json.dumps(record.to_dict(), ensure_ascii=False)


@mcp.tool()
def run_result(
    db_path: str,
    run_id: str,
) -> str:
    """Retrieve terminal result evidence for a run; raises error if non-terminal."""
    valid_db_path = _validate_db_path(db_path)
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id must be a non-empty string")
    manager = DurableRunManager(valid_db_path)
    record = manager.run_result(run_id.strip())
    payload = record.to_dict()

    try:
        from .telemetry_hooks import get_telemetry_ledger, telemetry_path_for
        from .usage_cli import build_usage_report_data
        from .usage_reports import resolve_report_path, write_stable_report
        from .usage_visualization import generate_html_report

        telemetry_db = telemetry_path_for(valid_db_path)
        ledger = get_telemetry_ledger(telemetry_db)

        report_data = build_usage_report_data(
            ledger=ledger,
            run_id=record.run_id,
            db_path_str=str(telemetry_db),
        )
        html_content = generate_html_report(report_data)
        target_path, _alias = resolve_report_path(
            run_id=record.run_id,
            is_latest=False,
        )
        out_file, target_uri, _alias_path, _alias_uri = write_stable_report(
            html_content=html_content,
            target_path=target_path,
        )
        payload["usage_report_status"] = "READY"
        payload["usage_report_path"] = str(out_file.resolve())
        payload["usage_report_uri"] = target_uri
        payload["usage_report_reason"] = None
        payload["usage_report_origin"] = report_data.get("usage_report_origin")
        payload["usage_report_run_id"] = record.run_id
        payload["usage_report_db_classification"] = report_data.get("usage_report_db_classification")
        payload["usage_report_event_provenance"] = report_data.get("usage_report_event_provenance")
    except Exception as exc:
        try:
            from .telemetry import redact_metadata
            safe_reason = redact_metadata({"error": str(exc)})["error"]
        except Exception:
            safe_reason = "Usage report generation failed"
        payload["usage_report_status"] = "FAILED"
        payload["usage_report_path"] = None
        payload["usage_report_uri"] = None
        payload["usage_report_reason"] = f"Failed to generate usage report: {safe_reason}"
        payload["usage_report_origin"] = None
        payload["usage_report_run_id"] = record.run_id
        payload["usage_report_db_classification"] = None
        payload["usage_report_event_provenance"] = None

    return json.dumps(payload, ensure_ascii=False)


@mcp.tool()
def run_cancel(
    db_path: str,
    run_id: str,
    reason: str = "User requested cancellation",
) -> str:
    """Cooperatively request cancellation for a run."""
    valid_db_path = _validate_db_path(db_path)
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id must be a non-empty string")
    if isinstance(reason, bool) or not isinstance(reason, str):
        raise ValueError("reason must be a string")
    manager = DurableRunManager(valid_db_path)
    record = manager.run_cancel(run_id.strip(), reason=reason)
    return json.dumps(record.to_dict(), ensure_ascii=False)


@mcp.tool()
def run_resume(
    db_path: str,
    run_id: str,
    account_switched: bool = False,
    credentials_refreshed: bool = False,
    dangerously_skip_permissions: bool = False,
) -> str:
    """Resume one suspended durable run on its existing task and worktree."""
    valid_db_path = _validate_db_path(db_path)
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id must be a non-empty string")
    if not (account_switched or credentials_refreshed):
        raise ValueError("run_resume requires explicit account_switched or credentials_refreshed confirmation")
    with _run_resume_lock:
        manager = DurableRunManager(valid_db_path)
        record = manager.run_status(run_id.strip())
        if record.state != RunState.ACCOUNT_SWITCH_REQUIRED:
            raise RunControlError(f"run_resume requires ACCOUNT_SWITCH_REQUIRED, got {record.state.value}")
        with manager._active_lock:
            if record.run_id in manager._active_executions:
                raise RunControlError(f"run_resume rejected: worker is still active for {record.run_id}")
        contract = manager.store.get_task_contract(record.run_id)
        if contract is None:
            raise RunControlError(f"run_resume rejected: TaskContract is missing for {record.run_id}")
        worker = build_worker_callback(
            contract,
            dangerously_skip_permissions=dangerously_skip_permissions,
        )
        resumed = RecoveryOrchestrator(manager).resume_same_run(
            record.run_id,
            worker=worker,
            account_switched=account_switched,
            credentials_refreshed=credentials_refreshed,
        )
        try:
            from .telemetry_hooks import record_run_resume_event, telemetry_path_for
            record_run_resume_event(
                run_id=resumed.run_id,
                task_id=resumed.task_id,
                project_dir=resumed.worktree,
                attempt=resumed.attempt,
                account_switched=account_switched,
                credentials_refreshed=credentials_refreshed,
                db_path=telemetry_path_for(valid_db_path),
            )
        except Exception:
            pass
    return json.dumps(resumed.to_dict(), ensure_ascii=False)
