"""Usage Telemetry Observational Instrumentation Hooks for Codex <-> Antigravity Bridge.

This module provides best-effort lifecycle hooks for recording observational usage
telemetry into SQLite journals without altering execution semantics or failure paths:
- Durable run start and worker launch/completion tracking
- Timeout classification and reconciliation events
- Account-switch required and run resumption events
- AGY call counts, duration seconds, success/failure/timeout counts
- Worktree diff / LOC metrics when safely observable
- Explicit UNAVAILABLE marking for provider tokens/quotas (no fake claims)
- Strict 0-turn preservation for Codex monitoring unless real evidence exists
- Complete secret/token/cookie redaction and prompt hashing
- Best-effort fault isolation (all hooks fail safely without raising)
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import logging
import os
from pathlib import Path
import subprocess
import threading
import time
from typing import Any, Callable, Optional, Sequence

from .agy_runner import classify_agy_error
from .telemetry import (
    DEFAULT_SOURCE_CONFIDENCE,
    MeasurementSource,
    UsageEvent,
    UsageLedger,
    UsageSummary,
    aggregate_events,
    get_default_telemetry_db_path,
    normalize_project_path,
    redact_metadata,
)

logger = logging.getLogger(__name__)

_ledgers: dict[str, UsageLedger] = {}
_ledgers_lock = threading.RLock()


def get_telemetry_ledger(db_path: str | Path | None = None) -> UsageLedger:
    """Resolve or construct a thread-safe UsageLedger instance for the given DB path.

    If db_path is omitted, uses the CODEX_AGY_TELEMETRY_DB environment variable if present,
    otherwise defaults to get_default_telemetry_db_path().
    """
    target_path: Path
    if db_path is not None:
        target_path = Path(db_path)
    else:
        env_db = os.environ.get("CODEX_AGY_TELEMETRY_DB")
        if env_db and env_db.strip():
            target_path = Path(env_db.strip())
        else:
            target_path = get_default_telemetry_db_path()

    try:
        resolved = target_path.expanduser().resolve()
        key = str(resolved).casefold() if os.name == "nt" else str(resolved)
    except Exception:
        key = str(target_path)

    with _ledgers_lock:
        ledger = _ledgers.get(key)
        if ledger is None:
            ledger = UsageLedger(db_path=target_path, fail_safe=True)
            _ledgers[key] = ledger
        return ledger


def reset_telemetry_ledgers() -> None:
    """Clear cached ledger instances (used primarily in tests)."""
    with _ledgers_lock:
        for ledger in _ledgers.values():
            try:
                ledger.close()
            except Exception:
                pass
        _ledgers.clear()


def safe_inspect_worktree_diff(workdir: str | Path | None) -> tuple[int, int] | None:
    """Safely inspect worktree git status/diff for changed files and changed lines of code.

    Returns (changed_files_count, total_changed_lines) or None if not applicable / fails.
    Guaranteed to never raise an exception and times out quickly.
    """
    if not workdir:
        return None
    try:
        p = Path(workdir).expanduser()
        if not p.is_dir():
            return None
        extra_kwargs: dict[str, Any] = {}
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            extra_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        res = subprocess.run(
            ["git", "diff", "--numstat", "HEAD"],
            cwd=str(p),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
            **extra_kwargs,
        )
        if res.returncode == 0:
            files_count = 0
            loc_count = 0
            for line in res.stdout.splitlines():
                parts = line.strip().split("\t")
                if len(parts) >= 3:
                    files_count += 1
                    added = int(parts[0]) if parts[0].isdigit() else 0
                    deleted = int(parts[1]) if parts[1].isdigit() else 0
                    loc_count += (added + deleted)
            if files_count == 0:
                # Also check unstaged or untracked changes
                res_status = subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=str(p),
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                    check=False,
                    **extra_kwargs,
                )
                if res_status.returncode == 0:
                    lines = [status_line for status_line in res_status.stdout.splitlines() if status_line.strip()]
                    if lines:
                        files_count = len(lines)
            return (files_count, loc_count)
    except Exception:
        return None
    return None


def record_run_start_event(
    run_id: str,
    task_id: str,
    project_dir: str | Path | None = None,
    db_path: str | Path | None = None,
    metadata: dict[str, Any] | None = None,
) -> UsageEvent | None:
    """Record durable run start lifecycle event and baseline monitoring turn count."""
    try:
        ledger = get_telemetry_ledger(db_path)
        meta = dict(metadata or {})
        meta["lifecycle_phase"] = "run_start"
        ev = ledger.record_event(
            actor="codex",
            event_type="run_start",
            measurement_type="call_count",
            value=1.0,
            unit="calls",
            measurement_source=MeasurementSource.CLI_EXACT,
            confidence=1.0,
            run_id=run_id,
            task_id=task_id,
            project_dir=project_dir,
            metadata=meta,
        )
        # Baseline CODEX monitoring turns = 0.0 unless actual turns exist
        ledger.record_event(
            actor="codex",
            event_type="run_start",
            measurement_type="monitoring_turns",
            value=0.0,
            unit="turns",
            measurement_source=MeasurementSource.DERIVED,
            confidence=1.0,
            run_id=run_id,
            task_id=task_id,
            project_dir=project_dir,
            metadata={"lifecycle_phase": "run_start"},
        )
        return ev
    except Exception as err:
        logger.warning("record_run_start_event failed (best-effort suppressed): %s", err)
        return None


def record_worker_launch_event(
    run_id: str,
    task_id: str | None = None,
    project_dir: str | Path | None = None,
    worker_identity: dict[str, Any] | None = None,
    attempt: int = 0,
    repair_round: int = 0,
    db_path: str | Path | None = None,
) -> UsageEvent | None:
    """Record worker launch event in durable run execution."""
    try:
        ledger = get_telemetry_ledger(db_path)
        meta = {
            "attempt": attempt,
            "repair_round": repair_round,
            "lifecycle_phase": "worker_launch",
        }
        if worker_identity:
            meta["worker_identity"] = worker_identity
        return ledger.record_event(
            actor="agy",
            event_type="worker_launch",
            measurement_type="call_count",
            value=1.0,
            unit="calls",
            measurement_source=MeasurementSource.CLI_EXACT,
            confidence=1.0,
            run_id=run_id,
            task_id=task_id,
            project_dir=project_dir,
            metadata=meta,
        )
    except Exception as err:
        logger.warning("record_worker_launch_event failed (best-effort suppressed): %s", err)
        return None


def record_worker_completion_event(
    run_id: str,
    task_id: str | None = None,
    project_dir: str | Path | None = None,
    duration_seconds: float | None = None,
    success: bool = False,
    target_state: str | None = None,
    last_error: str | None = None,
    verification_result: Any = None,
    db_path: str | Path | None = None,
) -> list[UsageEvent]:
    """Record worker completion lifecycle events (duration, outcomes, diffs, unavailable tokens)."""
    events: list[UsageEvent] = []
    try:
        ledger = get_telemetry_ledger(db_path)
        meta: dict[str, Any] = {
            "success": bool(success),
            "target_state": target_state,
            "lifecycle_phase": "worker_completion",
        }
        if last_error:
            meta["error_summary"] = str(last_error)[:200]
        if verification_result is not None:
            if isinstance(verification_result, dict):
                meta["verification_passed"] = verification_result.get("passed")
                meta["verification_status"] = verification_result.get("status")
            elif isinstance(verification_result, bool):
                meta["verification_passed"] = verification_result

        # 1. Call count
        ev_call = ledger.record_event(
            actor="agy",
            event_type="worker_completion",
            measurement_type="call_count",
            value=1.0,
            unit="calls",
            measurement_source=MeasurementSource.CLI_EXACT,
            confidence=1.0,
            run_id=run_id,
            task_id=task_id,
            project_dir=project_dir,
            metadata=meta,
        )
        if ev_call:
            events.append(ev_call)

        # 2. Outcome count (success / failure)
        outcome_type = "success_count" if success else "failure_count"
        ev_outcome = ledger.record_event(
            actor="agy",
            event_type="worker_completion",
            measurement_type=outcome_type,
            value=1.0,
            unit="count",
            measurement_source=MeasurementSource.CLI_EXACT,
            confidence=1.0,
            run_id=run_id,
            task_id=task_id,
            project_dir=project_dir,
            metadata=meta,
        )
        if ev_outcome:
            events.append(ev_outcome)

        # 3. Duration seconds
        if duration_seconds is not None and isinstance(duration_seconds, (int, float)) and duration_seconds >= 0:
            ev_dur = ledger.record_event(
                actor="agy",
                event_type="worker_completion",
                measurement_type="duration_seconds",
                value=float(duration_seconds),
                unit="seconds",
                measurement_source=MeasurementSource.CLI_EXACT,
                confidence=1.0,
                run_id=run_id,
                task_id=task_id,
                project_dir=project_dir,
                metadata=meta,
            )
            if ev_dur:
                events.append(ev_dur)

        # 4. Changed files & lines of code (when safely available)
        diff_info = safe_inspect_worktree_diff(project_dir)
        if diff_info is not None:
            files_count, loc_count = diff_info
            ev_files = ledger.record_event(
                actor="agy",
                event_type="worker_completion",
                measurement_type="changed_files",
                value=float(files_count),
                unit="files",
                measurement_source=MeasurementSource.CLI_EXACT,
                confidence=1.0,
                run_id=run_id,
                task_id=task_id,
                project_dir=project_dir,
                metadata=meta,
            )
            if ev_files:
                events.append(ev_files)

            ev_loc = ledger.record_event(
                actor="agy",
                event_type="worker_completion",
                measurement_type="lines_of_code",
                value=float(loc_count),
                unit="lines",
                measurement_source=MeasurementSource.CLI_EXACT,
                confidence=1.0,
                run_id=run_id,
                task_id=task_id,
                project_dir=project_dir,
                metadata=meta,
            )
            if ev_loc:
                events.append(ev_loc)

        # 5. Explicitly document UNAVAILABLE provider tokens / quotas without claiming false exactness
        ev_tokens = ledger.record_event(
            actor="agy",
            event_type="worker_completion",
            measurement_type="tokens",
            value=None,
            unit="tokens",
            measurement_source=MeasurementSource.UNAVAILABLE,
            confidence=0.0,
            run_id=run_id,
            task_id=task_id,
            project_dir=project_dir,
            metadata=meta,
        )
        if ev_tokens:
            events.append(ev_tokens)

        # 6. Preserve Codex monitoring turns as 0.0
        ev_turns = ledger.record_event(
            actor="codex",
            event_type="worker_completion",
            measurement_type="monitoring_turns",
            value=0.0,
            unit="turns",
            measurement_source=MeasurementSource.DERIVED,
            confidence=1.0,
            run_id=run_id,
            task_id=task_id,
            project_dir=project_dir,
            metadata=meta,
        )
        if ev_turns:
            events.append(ev_turns)

        # 7. Timeout event if worker failed with timeout
        if last_error:
            err_kind = classify_agy_error(str(last_error))
            if (
                err_kind in ("CONNECT_TIMEOUT", "REMOTE_EXECUTION_TIMEOUT", "LOCAL_SUPERVISION_TIMEOUT")
                or "timed out" in str(last_error).lower()
                or "timeout" in str(last_error).lower()
                or "heartbeat" in str(last_error).lower()
            ):
                to_cls = (
                    err_kind
                    if err_kind in ("CONNECT_TIMEOUT", "REMOTE_EXECUTION_TIMEOUT", "LOCAL_SUPERVISION_TIMEOUT")
                    else "LOCAL_SUPERVISION_TIMEOUT"
                )
                ev_to = record_timeout_event(
                    run_id=run_id,
                    task_id=task_id,
                    project_dir=project_dir,
                    timeout_class=to_cls,
                    error_text=str(last_error),
                    db_path=db_path,
                )
                if ev_to:
                    events.append(ev_to)

    except Exception as err:
        logger.warning("record_worker_completion_event failed (best-effort suppressed): %s", err)

    return events


def record_timeout_event(
    run_id: str | None = None,
    task_id: str | None = None,
    project_dir: str | Path | None = None,
    timeout_class: str | None = None,
    timeout_diagnostic: dict[str, Any] | None = None,
    error_text: str | None = None,
    db_path: str | Path | None = None,
) -> UsageEvent | None:
    """Record timeout classification event with structured diagnostic evidence."""
    try:
        ledger = get_telemetry_ledger(db_path)
        meta: dict[str, Any] = {
            "timeout_class": timeout_class or "UNKNOWN",
            "lifecycle_phase": "timeout_classification",
        }
        if timeout_diagnostic:
            meta["timeout_diagnostic"] = timeout_diagnostic
        if error_text:
            meta["error_summary"] = str(error_text)[:200]

        return ledger.record_event(
            actor="bridge",
            event_type="timeout_classified",
            measurement_type="timeout_count",
            value=1.0,
            unit="count",
            measurement_source=MeasurementSource.CLI_EXACT,
            confidence=1.0,
            run_id=run_id,
            task_id=task_id,
            project_dir=project_dir,
            metadata=meta,
        )
    except Exception as err:
        logger.warning("record_timeout_event failed (best-effort suppressed): %s", err)
        return None


def record_account_switch_event(
    run_id: str,
    task_id: str | None = None,
    project_dir: str | Path | None = None,
    reason: str | None = None,
    db_path: str | Path | None = None,
) -> UsageEvent | None:
    """Record account switch required lifecycle event."""
    try:
        ledger = get_telemetry_ledger(db_path)
        meta = {
            "reason": str(reason)[:200] if reason else "Quota exhausted",
            "lifecycle_phase": "account_switch_required",
        }
        return ledger.record_event(
            actor="bridge",
            event_type="account_switch_required",
            measurement_type="account_switches",
            value=1.0,
            unit="count",
            measurement_source=MeasurementSource.CLI_EXACT,
            confidence=1.0,
            run_id=run_id,
            task_id=task_id,
            project_dir=project_dir,
            metadata=meta,
        )
    except Exception as err:
        logger.warning("record_account_switch_event failed (best-effort suppressed): %s", err)
        return None


def record_run_resume_event(
    run_id: str,
    task_id: str | None = None,
    project_dir: str | Path | None = None,
    attempt: int = 0,
    account_switched: bool = False,
    credentials_refreshed: bool = False,
    db_path: str | Path | None = None,
) -> UsageEvent | None:
    """Record run resumption lifecycle event."""
    try:
        ledger = get_telemetry_ledger(db_path)
        meta = {
            "attempt": attempt,
            "account_switched": bool(account_switched),
            "credentials_refreshed": bool(credentials_refreshed),
            "lifecycle_phase": "run_resume",
        }
        return ledger.record_event(
            actor="codex",
            event_type="run_resumed",
            measurement_type="resumptions",
            value=1.0,
            unit="count",
            measurement_source=MeasurementSource.CLI_EXACT,
            confidence=1.0,
            run_id=run_id,
            task_id=task_id,
            project_dir=project_dir,
            metadata=meta,
        )
    except Exception as err:
        logger.warning("record_run_resume_event failed (best-effort suppressed): %s", err)
        return None


def record_reconciliation_event(
    run_id: str | None = None,
    task_id: str | None = None,
    project_dir: str | Path | None = None,
    action: str | None = None,
    reason: str | None = None,
    db_path: str | Path | None = None,
) -> UsageEvent | None:
    """Record state reconciliation observation event."""
    try:
        ledger = get_telemetry_ledger(db_path)
        meta = {
            "action": action or "reconcile",
            "reason": str(reason)[:200] if reason else None,
            "lifecycle_phase": "reconciliation",
        }
        return ledger.record_event(
            actor="bridge",
            event_type="reconciliation",
            measurement_type="reconciliations",
            value=1.0,
            unit="count",
            measurement_source=MeasurementSource.CLI_EXACT,
            confidence=1.0,
            run_id=run_id,
            task_id=task_id,
            project_dir=project_dir,
            metadata=meta,
        )
    except Exception as err:
        logger.warning("record_reconciliation_event failed (best-effort suppressed): %s", err)
        return None


def record_retry_event(
    run_id: str | None = None,
    task_id: str | None = None,
    project_dir: str | Path | None = None,
    attempt: int = 0,
    reason: str | None = None,
    db_path: str | Path | None = None,
) -> UsageEvent | None:
    """Record execution retry lifecycle event."""
    try:
        ledger = get_telemetry_ledger(db_path)
        meta = {
            "attempt": attempt,
            "reason": str(reason)[:200] if reason else None,
            "lifecycle_phase": "retry",
        }
        return ledger.record_event(
            actor="bridge",
            event_type="retry",
            measurement_type="retries",
            value=1.0,
            unit="count",
            measurement_source=MeasurementSource.CLI_EXACT,
            confidence=1.0,
            run_id=run_id,
            task_id=task_id,
            project_dir=project_dir,
            metadata=meta,
        )
    except Exception as err:
        logger.warning("record_retry_event failed (best-effort suppressed): %s", err)
        return None


def record_agy_job_start_event(
    job_id: str,
    task_key: str | None = None,
    workdir: str | Path | None = None,
    db_path: str | Path | None = None,
) -> UsageEvent | None:
    """Record asynchronous AGY job start event."""
    try:
        ledger = get_telemetry_ledger(db_path)
        meta = {
            "task_key": task_key,
            "lifecycle_phase": "job_start",
        }
        return ledger.record_event(
            actor="agy",
            event_type="job_start",
            measurement_type="call_count",
            value=1.0,
            unit="calls",
            measurement_source=MeasurementSource.CLI_EXACT,
            confidence=1.0,
            run_id=job_id,
            project_dir=workdir,
            metadata=meta,
        )
    except Exception as err:
        logger.warning("record_agy_job_start_event failed (best-effort suppressed): %s", err)
        return None


def record_agy_job_completion_event(
    job_id: str,
    task_key: str | None = None,
    workdir: str | Path | None = None,
    elapsed_seconds: float = 0.0,
    exit_code: int = 0,
    error_kind: str | None = None,
    result_text: str | None = None,
    error_text: str | None = None,
    db_path: str | Path | None = None,
) -> list[UsageEvent]:
    """Record asynchronous AGY job completion metrics."""
    events: list[UsageEvent] = []
    try:
        ledger = get_telemetry_ledger(db_path)
        success = (exit_code == 0)
        meta: dict[str, Any] = {
            "task_key": task_key,
            "exit_code": exit_code,
            "success": success,
            "error_kind": error_kind,
            "lifecycle_phase": "job_completion",
        }
        if error_text:
            meta["error_summary"] = str(error_text)[:200]

        # 1. Call count
        ev_call = ledger.record_event(
            actor="agy",
            event_type="job_completion",
            measurement_type="call_count",
            value=1.0,
            unit="calls",
            measurement_source=MeasurementSource.CLI_EXACT,
            confidence=1.0,
            run_id=job_id,
            project_dir=workdir,
            metadata=meta,
        )
        if ev_call:
            events.append(ev_call)

        # 2. Success / failure count
        outcome_type = "success_count" if success else "failure_count"
        ev_outcome = ledger.record_event(
            actor="agy",
            event_type="job_completion",
            measurement_type=outcome_type,
            value=1.0,
            unit="count",
            measurement_source=MeasurementSource.CLI_EXACT,
            confidence=1.0,
            run_id=job_id,
            project_dir=workdir,
            metadata=meta,
        )
        if ev_outcome:
            events.append(ev_outcome)

        # 3. Duration seconds
        ev_dur = ledger.record_event(
            actor="agy",
            event_type="job_completion",
            measurement_type="duration_seconds",
            value=max(0.0, float(elapsed_seconds)),
            unit="seconds",
            measurement_source=MeasurementSource.CLI_EXACT,
            confidence=1.0,
            run_id=job_id,
            project_dir=workdir,
            metadata=meta,
        )
        if ev_dur:
            events.append(ev_dur)

        # 4. Changed files & LOC if workdir diff exists
        diff_info = safe_inspect_worktree_diff(workdir)
        if diff_info is not None:
            files_count, loc_count = diff_info
            ev_files = ledger.record_event(
                actor="agy",
                event_type="job_completion",
                measurement_type="changed_files",
                value=float(files_count),
                unit="files",
                measurement_source=MeasurementSource.CLI_EXACT,
                confidence=1.0,
                run_id=job_id,
                project_dir=workdir,
                metadata=meta,
            )
            if ev_files:
                events.append(ev_files)

            ev_loc = ledger.record_event(
                actor="agy",
                event_type="job_completion",
                measurement_type="lines_of_code",
                value=float(loc_count),
                unit="lines",
                measurement_source=MeasurementSource.CLI_EXACT,
                confidence=1.0,
                run_id=job_id,
                project_dir=workdir,
                metadata=meta,
            )
            if ev_loc:
                events.append(ev_loc)

        # 5. Timeout event if timeout classified
        if error_kind in ("CONNECT_TIMEOUT", "REMOTE_EXECUTION_TIMEOUT", "LOCAL_SUPERVISION_TIMEOUT") or (
            error_text and "timed out" in error_text.lower()
        ):
            ev_to = record_timeout_event(
                run_id=job_id,
                project_dir=workdir,
                timeout_class=error_kind or "TIMEOUT",
                error_text=error_text,
                db_path=db_path,
            )
            if ev_to:
                events.append(ev_to)

        # 6. Mark tokens as UNAVAILABLE
        ev_tok = ledger.record_event(
            actor="agy",
            event_type="job_completion",
            measurement_type="tokens",
            value=None,
            unit="tokens",
            measurement_source=MeasurementSource.UNAVAILABLE,
            confidence=0.0,
            run_id=job_id,
            project_dir=workdir,
            metadata=meta,
        )
        if ev_tok:
            events.append(ev_tok)

        # 7. Preserve monitoring turns 0.0
        ev_turns = ledger.record_event(
            actor="codex",
            event_type="job_completion",
            measurement_type="monitoring_turns",
            value=0.0,
            unit="turns",
            measurement_source=MeasurementSource.DERIVED,
            confidence=1.0,
            run_id=job_id,
            project_dir=workdir,
            metadata=meta,
        )
        if ev_turns:
            events.append(ev_turns)

    except Exception as err:
        logger.warning("record_agy_job_completion_event failed (best-effort suppressed): %s", err)

    return events


def record_oneshot_call_event(
    prompt: str,
    workdir: str | Path | None = None,
    duration_seconds: float = 0.0,
    exit_code: int = 0,
    error_kind: str | None = None,
    db_path: str | Path | None = None,
) -> list[UsageEvent]:
    """Record metrics for synchronous one-shot agy_ask / agy_ask_json calls."""
    events: list[UsageEvent] = []
    try:
        ledger = get_telemetry_ledger(db_path)
        success = (exit_code == 0)
        prompt_hash = hashlib.sha256(str(prompt).encode("utf-8", errors="ignore")).hexdigest()[:16]
        meta = {
            "prompt_hash": prompt_hash,
            "exit_code": exit_code,
            "success": success,
            "error_kind": error_kind,
            "lifecycle_phase": "oneshot_call",
        }

        # 1. Call count
        ev_call = ledger.record_event(
            actor="agy",
            event_type="oneshot_call",
            measurement_type="call_count",
            value=1.0,
            unit="calls",
            measurement_source=MeasurementSource.CLI_EXACT,
            confidence=1.0,
            project_dir=workdir,
            metadata=meta,
        )
        if ev_call:
            events.append(ev_call)

        # 2. Outcome count
        outcome_type = "success_count" if success else "failure_count"
        ev_outcome = ledger.record_event(
            actor="agy",
            event_type="oneshot_call",
            measurement_type=outcome_type,
            value=1.0,
            unit="count",
            measurement_source=MeasurementSource.CLI_EXACT,
            confidence=1.0,
            project_dir=workdir,
            metadata=meta,
        )
        if ev_outcome:
            events.append(ev_outcome)

        # 3. Duration
        ev_dur = ledger.record_event(
            actor="agy",
            event_type="oneshot_call",
            measurement_type="duration_seconds",
            value=max(0.0, float(duration_seconds)),
            unit="seconds",
            measurement_source=MeasurementSource.CLI_EXACT,
            confidence=1.0,
            project_dir=workdir,
            metadata=meta,
        )
        if ev_dur:
            events.append(ev_dur)

        # 4. UNAVAILABLE tokens
        ev_tok = ledger.record_event(
            actor="agy",
            event_type="oneshot_call",
            measurement_type="tokens",
            value=None,
            unit="tokens",
            measurement_source=MeasurementSource.UNAVAILABLE,
            confidence=0.0,
            project_dir=workdir,
            metadata=meta,
        )
        if ev_tok:
            events.append(ev_tok)

        # 5. Timeout event if error_kind is a timeout
        if error_kind in ("CONNECT_TIMEOUT", "REMOTE_EXECUTION_TIMEOUT", "LOCAL_SUPERVISION_TIMEOUT"):
            ev_to = record_timeout_event(
                project_dir=workdir,
                timeout_class=error_kind,
                error_text=f"Oneshot {error_kind}",
                db_path=db_path,
            )
            if ev_to:
                events.append(ev_to)

    except Exception as err:
        logger.warning("record_oneshot_call_event failed (best-effort suppressed): %s", err)

    return events


__all__ = [
    "get_telemetry_ledger",
    "reset_telemetry_ledgers",
    "safe_inspect_worktree_diff",
    "record_run_start_event",
    "record_worker_launch_event",
    "record_worker_completion_event",
    "record_timeout_event",
    "record_account_switch_event",
    "record_run_resume_event",
    "record_reconciliation_event",
    "record_retry_event",
    "record_agy_job_start_event",
    "record_agy_job_completion_event",
    "record_oneshot_call_event",
]
