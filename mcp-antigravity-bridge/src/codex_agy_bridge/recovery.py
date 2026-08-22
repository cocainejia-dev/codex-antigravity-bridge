"""Phase 7 VNext crash, interruption, and auth recovery orchestration.

Provides typed recovery evidence/reports, deterministic failure classification,
guarded state recovery transitions, and same-run resumption using durable state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from typing import Any, Callable, Optional, Sequence

from .contracts import (
    CREDENTIAL_PATTERNS,
    AutoCommitPolicy,
    InvalidStateTransitionError,
    RiskClass,
    RunRecord,
    RunState,
    TaskContract,
    _format_timestamp,
    _utc_now_iso,
    normalize_path,
    validate_no_credentials,
)
from .run_control import (
    ACTIVE_STATES,
    TERMINAL_STATES,
    ConcurrentModificationError,
    CredentialSecurityError,
    DurableRunManager,
    DurableRunStore,
    DuplicateRunError,
    RunControlError,
    RunNotFoundError,
    RunObservation,
    WorkerCallback,
    WorkerContext,
    WorkerResult,
    is_pid_alive,
)

# Maximum allowed size in characters for captured recovery error / output snippets
MAX_BOUNDED_STRING_LENGTH: int = 4096
MAX_BOUNDED_LOG_LINES: int = 100

# Error regex patterns for auth and quota classification
QUOTA_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(?:quota|resource[_\s]exhausted|rate[_\s]capacity|credit[_\s]limit|insufficient[_\s]quota)\b"),
    re.compile(r"(?i)\b(?:429|too[_\s]many[_\s]requests)\b"),
    re.compile(r"(?i)\b(?:daily|monthly|hourly)[_\s]limit[_\s]reached\b"),
)

AUTH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(?:auth(?:entication)?|token|session|credentials?)[_\s](?:expired|invalid|revoked|missing|required)\b"),
    re.compile(r"(?i)\b(?:unauthorized|401|403|access[_\s]denied|permission[_\s]denied|forbidden|re-?authenticate)\b"),
    re.compile(r"(?i)\b(?:account[_\s]switch[_\s]required|login[_\s]required|jwt[_\s]expired)\b"),
)


class RecoveryError(Exception):
    """Base exception for recovery operations."""

    pass


class InconsistentEvidenceError(RecoveryError):
    """Raised when durable state or recovery evidence is corrupted or contradictory."""

    pass


class UnrecoverableRunError(RecoveryError):
    """Raised when a run cannot be safely recovered."""

    pass


class FailureClass(str, Enum):
    """Deterministic taxonomy of task execution failures and interruption causes."""

    WORKER_CRASH = "WORKER_CRASH"
    MCP_RESTART = "MCP_RESTART"
    DEAD_PID = "DEAD_PID"
    STALE_HEARTBEAT = "STALE_HEARTBEAT"
    DIRTY_WORKTREE = "DIRTY_WORKTREE"
    VERIFICATION_INTERRUPTION = "VERIFICATION_INTERRUPTION"
    PRE_COMMIT_INTERRUPTION = "PRE_COMMIT_INTERRUPTION"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    RATE_LIMIT = "RATE_LIMIT"
    AUTH_EXPIRED = "AUTH_EXPIRED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    INCONSISTENT_EVIDENCE = "INCONSISTENT_EVIDENCE"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def from_value(cls, val: str | FailureClass) -> FailureClass:
        if isinstance(val, cls):
            return val
        if not isinstance(val, str):
            raise ValueError(f"Invalid FailureClass type: {type(val).__name__}")
        norm = val.strip().upper().replace("-", "_").replace(" ", "_")
        for member in cls:
            if member.value == norm or member.name == norm:
                return member
        return cls.UNKNOWN


class RecoveryStatus(str, Enum):
    """Lifecycle status of recovery assessment."""

    RECOVERY_READY = "RECOVERY_READY"
    ACCOUNT_SWITCH_REQUIRED = "ACCOUNT_SWITCH_REQUIRED"
    INTERRUPTED = "INTERRUPTED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    HEALTHY = "HEALTHY"
    TERMINAL = "TERMINAL"

    @classmethod
    def from_value(cls, val: str | RecoveryStatus) -> RecoveryStatus:
        if isinstance(val, cls):
            return val
        if not isinstance(val, str):
            raise ValueError(f"Invalid RecoveryStatus type: {type(val).__name__}")
        norm = val.strip().upper().replace("-", "_").replace(" ", "_")
        for member in cls:
            if member.value == norm or member.name == norm:
                return member
        raise ValueError(f"Unknown recovery status: {val!r}")


class RecoveryAction(str, Enum):
    """Recommended recovery action for supervisor or operator."""

    RESUME_SAME_RUN = "RESUME_SAME_RUN"
    SWITCH_ACCOUNT_AND_RESUME = "SWITCH_ACCOUNT_AND_RESUME"
    REPAIR_OR_RETRY = "REPAIR_OR_RETRY"
    CLEAN_WORKTREE_AND_RESUME = "CLEAN_WORKTREE_AND_RESUME"
    FAIL_RUN = "FAIL_RUN"
    BLOCK_RUN = "BLOCK_RUN"
    NOOP = "NOOP"

    @classmethod
    def from_value(cls, val: str | RecoveryAction) -> RecoveryAction:
        if isinstance(val, cls):
            return val
        if not isinstance(val, str):
            raise ValueError(f"Invalid RecoveryAction type: {type(val).__name__}")
        norm = val.strip().upper().replace("-", "_").replace(" ", "_")
        for member in cls:
            if member.value == norm or member.name == norm:
                return member
        raise ValueError(f"Unknown recovery action: {val!r}")


def _truncate_string(s: str | None, max_len: int = MAX_BOUNDED_STRING_LENGTH) -> str | None:
    """Safely bound log or error string length to prevent memory amplification."""
    if s is None:
        return None
    if len(s) <= max_len:
        return s
    return s[:max_len] + "... [TRUNCATED]"


def classify_error_message(msg: str | None) -> FailureClass | None:
    """Deterministically classify an error message into an auth/quota/failure class."""
    if not msg or not isinstance(msg, str):
        return None

    msg_norm = msg.strip()

    # Check quota / rate limit first
    for pat in QUOTA_PATTERNS:
        if pat.search(msg_norm):
            if "rate" in msg_norm.lower() or "429" in msg_norm:
                return FailureClass.RATE_LIMIT
            return FailureClass.QUOTA_EXHAUSTED

    # Check auth / credentials
    for pat in AUTH_PATTERNS:
        if pat.search(msg_norm):
            if "expire" in msg_norm.lower():
                return FailureClass.AUTH_EXPIRED
            return FailureClass.AUTH_REQUIRED

    return None


@dataclass
class RecoveryEvidence:
    """Typed evidence captured from durable journal, OS process table, and worktree filesystem."""

    run_id: str
    task_id: str
    state: RunState
    state_version: int
    failure_class: FailureClass | None = None
    pid: int | None = None
    pid_alive: bool = False
    heartbeat: str | None = None
    heartbeat_age_seconds: float | None = None
    is_stale_heartbeat: bool = False
    manager_restarted: bool = False
    worktree: str | None = None
    worktree_exists: bool = False
    worktree_dirty: bool = False
    dirty_files: list[str] = field(default_factory=list)
    base_head: str | None = None
    current_head: str | None = None
    repair_round: int = 0
    attempt: int = 0
    last_error: str | None = None
    suspended_reason: str | None = None
    verification_result: Any | None = None
    raw_details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Validate integrity and credential-safety of evidence."""
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise InconsistentEvidenceError("run_id must be a non-empty string")
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise InconsistentEvidenceError("task_id must be a non-empty string")

        self.state = RunState.from_value(self.state)
        if self.failure_class is not None:
            self.failure_class = FailureClass.from_value(self.failure_class)

        if self.last_error is not None:
            self.last_error = _truncate_string(self.last_error)
        if self.suspended_reason is not None:
            self.suspended_reason = _truncate_string(self.suspended_reason)

        # Enforce strict secret scanning
        validate_no_credentials(self.run_id, "evidence.run_id")
        validate_no_credentials(self.task_id, "evidence.task_id")
        validate_no_credentials(self.last_error, "evidence.last_error")
        validate_no_credentials(self.suspended_reason, "evidence.suspended_reason")
        validate_no_credentials(self.raw_details, "evidence.raw_details")

    def to_dict(self) -> dict[str, Any]:
        """Convert evidence to JSON-safe dictionary."""
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "state": self.state.value,
            "state_version": self.state_version,
            "failure_class": self.failure_class.value if self.failure_class else None,
            "pid": self.pid,
            "pid_alive": self.pid_alive,
            "heartbeat": self.heartbeat,
            "heartbeat_age_seconds": self.heartbeat_age_seconds,
            "is_stale_heartbeat": self.is_stale_heartbeat,
            "manager_restarted": self.manager_restarted,
            "worktree": self.worktree,
            "worktree_exists": self.worktree_exists,
            "worktree_dirty": self.worktree_dirty,
            "dirty_files": list(self.dirty_files),
            "base_head": self.base_head,
            "current_head": self.current_head,
            "repair_round": self.repair_round,
            "attempt": self.attempt,
            "last_error": self.last_error,
            "suspended_reason": self.suspended_reason,
            "verification_result": self.verification_result,
            "raw_details": dict(self.raw_details),
        }


@dataclass
class RecoveryCheckpoint:
    """Immutable snapshot of run context preserved across interruptions and account switching."""

    task_id: str
    run_id: str
    worktree: str | None
    base_head: str | None
    current_head: str | None
    repair_round: int
    attempt: int
    state_version: int
    preserved_at: str = field(default_factory=_utc_now_iso)
    dirty_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "worktree": self.worktree,
            "base_head": self.base_head,
            "current_head": self.current_head,
            "repair_round": self.repair_round,
            "attempt": self.attempt,
            "state_version": self.state_version,
            "preserved_at": self.preserved_at,
            "dirty_files": list(self.dirty_files),
        }


@dataclass
class RecoveryReport:
    """Comprehensive diagnosis report containing classified failure, status, and action plan."""

    run_id: str
    task_id: str
    primary_failure: FailureClass | None
    recovery_status: RecoveryStatus
    recommended_action: RecoveryAction
    evidence: RecoveryEvidence
    can_resume_same_run: bool
    reason: str
    checkpoint: RecoveryCheckpoint
    created_at: str = field(default_factory=_utc_now_iso)

    def __post_init__(self) -> None:
        self.recovery_status = RecoveryStatus.from_value(self.recovery_status)
        self.recommended_action = RecoveryAction.from_value(self.recommended_action)
        if self.primary_failure is not None:
            self.primary_failure = FailureClass.from_value(self.primary_failure)
        validate_no_credentials(self.reason, "report.reason")

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "primary_failure": self.primary_failure.value if self.primary_failure else None,
            "recovery_status": self.recovery_status.value,
            "recommended_action": self.recommended_action.value,
            "can_resume_same_run": self.can_resume_same_run,
            "reason": self.reason,
            "checkpoint": self.checkpoint.to_dict(),
            "evidence": self.evidence.to_dict(),
            "created_at": self.created_at,
        }

    def to_json(self, **kwargs: Any) -> str:
        return json.dumps(self.to_dict(), **kwargs)


def inspect_worktree_evidence(
    worktree_path: str | Path | None,
) -> tuple[bool, bool, list[str]]:
    """Inspect local filesystem and git state for a worktree safely.

    Returns:
        (exists: bool, is_dirty: bool, dirty_files: list[str])
    """
    if worktree_path is None:
        return False, False, []

    wt = Path(worktree_path)
    if not wt.exists() or not wt.is_dir():
        return False, False, []

    # Check git status if git is available and wt is inside a repo
    try:
        res = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(wt),
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        if res.returncode == 0:
            lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
            dirty_files = [line[3:].strip() for line in lines if len(line) > 3][:MAX_BOUNDED_LOG_LINES]
            is_dirty = len(dirty_files) > 0
            return True, is_dirty, dirty_files
    except Exception:
        # Fallback if git is not installed or worktree is not a git repo
        pass

    return True, False, []


class RecoveryOrchestrator:
    """Orchestrates VNext fault diagnosis, recovery readiness, and same-run resumption."""

    def __init__(
        self,
        manager: DurableRunManager,
        stale_heartbeat_threshold_seconds: float = 60.0,
    ) -> None:
        if manager is None:
            raise ValueError("RecoveryOrchestrator requires a valid DurableRunManager instance")
        self.manager = manager
        self.store = manager.store
        self.stale_heartbeat_threshold_seconds = stale_heartbeat_threshold_seconds
        self._lock = threading.RLock()

    def collect_evidence(
        self,
        run_id: str,
        *,
        external_pid_alive_fn: Callable[[int | None], bool] | None = None,
    ) -> RecoveryEvidence:
        """Collect and cross-correlate all observable evidence for a run."""
        record = self.store.get_run(run_id)
        if record is None:
            raise RunNotFoundError(f"Run {run_id} not found in durable journal")

        task_contract = self.store.get_task_contract(run_id)
        worker_identity = self.store.get_worker_identity(run_id)

        # Check for inconsistent state / corruption
        if task_contract is None:
            return RecoveryEvidence(
                run_id=run_id,
                task_id=record.task_id,
                state=record.state,
                state_version=record.state_version,
                failure_class=FailureClass.INCONSISTENT_EVIDENCE,
                last_error="Corrupt run: associated TaskContract is missing in SQLite journal",
                repair_round=record.repair_round,
                attempt=record.attempt,
            )

        if record.state_version < 1:
            return RecoveryEvidence(
                run_id=run_id,
                task_id=record.task_id,
                state=record.state,
                state_version=record.state_version,
                failure_class=FailureClass.INCONSISTENT_EVIDENCE,
                last_error=f"Corrupt run: invalid state_version {record.state_version}",
                repair_round=record.repair_round,
                attempt=record.attempt,
            )

        # Check process liveness
        pid_alive_check = external_pid_alive_fn or is_pid_alive
        pid_alive = pid_alive_check(record.pid)

        # Check in-memory execution in manager
        with self.manager._active_lock:
            active_exec = self.manager._active_executions.get(run_id)
            thread_alive = (
                active_exec is not None
                and active_exec.thread is not None
                and active_exec.thread.is_alive()
            )

        # Determine worker type
        wtype = ""
        if worker_identity:
            wtype = str(worker_identity.get("worker_type") or worker_identity.get("type") or "").strip().lower()
        is_in_process = wtype in ("in_process", "thread", "callback", "in_process_callback", "in_process_thread")
        if not wtype and (record.pid is None or record.pid == os.getpid()):
            is_in_process = True

        manager_restarted = False
        if is_in_process and not thread_alive:
            manager_restarted = True

        # Check heartbeat age
        heartbeat_age: float | None = None
        is_stale_hb = False
        if record.heartbeat:
            try:
                hb_dt = datetime.fromisoformat(record.heartbeat)
                if hb_dt.tzinfo is None:
                    hb_dt = hb_dt.replace(tzinfo=timezone.utc)
                heartbeat_age = (datetime.now(timezone.utc) - hb_dt).total_seconds()
                if heartbeat_age > self.stale_heartbeat_threshold_seconds:
                    is_stale_hb = True
            except Exception:
                is_stale_hb = True

        # Check worktree
        wt_path = record.worktree or task_contract.workdir
        wt_exists, wt_dirty, dirty_files = inspect_worktree_evidence(wt_path)

        # Classify failure
        failure_class: FailureClass | None = None

        # Check error signatures in last_error or suspended_reason
        err_candidate = record.suspended_reason or record.last_error
        err_class = classify_error_message(err_candidate)
        if err_class is not None:
            failure_class = err_class
        elif record.state == RunState.ACCOUNT_SWITCH_REQUIRED:
            failure_class = FailureClass.AUTH_REQUIRED
        elif record.state in (RunState.RUNNING, RunState.VERIFYING, RunState.REPAIRING, RunState.COMMITTING, RunState.QUEUED):
            if is_in_process and manager_restarted:
                if record.state == RunState.VERIFYING:
                    failure_class = FailureClass.VERIFICATION_INTERRUPTION
                elif record.state == RunState.COMMITTING:
                    failure_class = FailureClass.PRE_COMMIT_INTERRUPTION
                else:
                    failure_class = FailureClass.MCP_RESTART
            elif not is_in_process and record.pid is not None and not pid_alive:
                failure_class = FailureClass.DEAD_PID
            elif is_stale_hb:
                failure_class = FailureClass.STALE_HEARTBEAT
            elif wt_dirty:
                failure_class = FailureClass.DIRTY_WORKTREE
            else:
                failure_class = FailureClass.WORKER_CRASH
        elif record.state == RunState.INTERRUPTED:
            if wt_dirty:
                failure_class = FailureClass.DIRTY_WORKTREE
            elif is_stale_hb:
                failure_class = FailureClass.STALE_HEARTBEAT
            else:
                failure_class = FailureClass.MCP_RESTART

        return RecoveryEvidence(
            run_id=run_id,
            task_id=record.task_id,
            state=record.state,
            state_version=record.state_version,
            failure_class=failure_class,
            pid=record.pid,
            pid_alive=pid_alive,
            heartbeat=record.heartbeat,
            heartbeat_age_seconds=heartbeat_age,
            is_stale_heartbeat=is_stale_hb,
            manager_restarted=manager_restarted,
            worktree=record.worktree,
            worktree_exists=wt_exists,
            worktree_dirty=wt_dirty,
            dirty_files=dirty_files,
            base_head=record.base_head,
            current_head=record.current_head,
            repair_round=record.repair_round,
            attempt=record.attempt,
            last_error=record.last_error,
            suspended_reason=record.suspended_reason,
            verification_result=record.verification_result,
            raw_details={
                "worker_type": wtype,
                "is_in_process": is_in_process,
                "thread_alive": thread_alive,
            },
        )

    def diagnose_run(
        self,
        run_id: str,
        *,
        external_pid_alive_fn: Callable[[int | None], bool] | None = None,
    ) -> RecoveryReport:
        """Perform comprehensive diagnosis of a run and generate a RecoveryReport."""
        record = self.store.get_run(run_id)
        if record is None:
            raise RunNotFoundError(f"Run {run_id} not found")

        evidence = self.collect_evidence(run_id, external_pid_alive_fn=external_pid_alive_fn)

        checkpoint = RecoveryCheckpoint(
            task_id=record.task_id,
            run_id=record.run_id,
            worktree=record.worktree,
            base_head=record.base_head,
            current_head=record.current_head,
            repair_round=record.repair_round,
            attempt=record.attempt,
            state_version=record.state_version,
            dirty_files=evidence.dirty_files,
        )

        # 1. Terminal states
        if record.state in TERMINAL_STATES:
            return RecoveryReport(
                run_id=run_id,
                task_id=record.task_id,
                primary_failure=None,
                recovery_status=RecoveryStatus.TERMINAL,
                recommended_action=RecoveryAction.NOOP,
                evidence=evidence,
                can_resume_same_run=False,
                reason=f"Run is already in terminal state '{record.state.value}'.",
                checkpoint=checkpoint,
            )

        # 2. Inconsistent evidence / corruption
        if evidence.failure_class == FailureClass.INCONSISTENT_EVIDENCE:
            return RecoveryReport(
                run_id=run_id,
                task_id=record.task_id,
                primary_failure=FailureClass.INCONSISTENT_EVIDENCE,
                recovery_status=RecoveryStatus.FAILED,
                recommended_action=RecoveryAction.FAIL_RUN,
                evidence=evidence,
                can_resume_same_run=False,
                reason=evidence.last_error or "Inconsistent or corrupted durable state detected.",
                checkpoint=checkpoint,
            )

        # 3. Only explicit quota exhaustion requires an account switch. Generic
        # rate limits and authentication failures need separate recovery.
        if record.state == RunState.ACCOUNT_SWITCH_REQUIRED or evidence.failure_class == FailureClass.QUOTA_EXHAUSTED:
            return RecoveryReport(
                run_id=run_id,
                task_id=record.task_id,
                primary_failure=evidence.failure_class,
                recovery_status=RecoveryStatus.ACCOUNT_SWITCH_REQUIRED,
                recommended_action=RecoveryAction.SWITCH_ACCOUNT_AND_RESUME,
                evidence=evidence,
                can_resume_same_run=True,
                reason=f"Execution suspended due to {evidence.failure_class.value}. Requires account/credentials switch before same-run resumption.",
                checkpoint=checkpoint,
            )

        # 4. Check active run liveness (still healthy in-process thread or external process)
        if record.state in (RunState.RUNNING, RunState.VERIFYING, RunState.COMMITTING, RunState.REPAIRING):
            raw_details = evidence.raw_details
            if raw_details.get("is_in_process") and raw_details.get("thread_alive"):
                return RecoveryReport(
                    run_id=run_id,
                    task_id=record.task_id,
                    primary_failure=None,
                    recovery_status=RecoveryStatus.HEALTHY,
                    recommended_action=RecoveryAction.NOOP,
                    evidence=evidence,
                    can_resume_same_run=False,
                    reason=f"Worker thread is actively executing in state '{record.state.value}'.",
                    checkpoint=checkpoint,
                )
            if not raw_details.get("is_in_process") and evidence.pid_alive and not evidence.is_stale_heartbeat:
                return RecoveryReport(
                    run_id=run_id,
                    task_id=record.task_id,
                    primary_failure=None,
                    recovery_status=RecoveryStatus.HEALTHY,
                    recommended_action=RecoveryAction.NOOP,
                    evidence=evidence,
                    can_resume_same_run=False,
                    reason=f"Worker process (PID {record.pid}) is actively executing in state '{record.state.value}'.",
                    checkpoint=checkpoint,
                )

        # 5. Interrupted or orphan active states requiring recovery
        if record.state == RunState.RECOVERY_READY:
            return RecoveryReport(
                run_id=run_id,
                task_id=record.task_id,
                primary_failure=evidence.failure_class,
                recovery_status=RecoveryStatus.RECOVERY_READY,
                recommended_action=RecoveryAction.RESUME_SAME_RUN,
                evidence=evidence,
                can_resume_same_run=True,
                reason="Run has verified recovery prerequisites and is ready for same-run resumption.",
                checkpoint=checkpoint,
            )

        if evidence.failure_class == FailureClass.DIRTY_WORKTREE:
            return RecoveryReport(
                run_id=run_id,
                task_id=record.task_id,
                primary_failure=FailureClass.DIRTY_WORKTREE,
                recovery_status=RecoveryStatus.INTERRUPTED,
                recommended_action=RecoveryAction.CLEAN_WORKTREE_AND_RESUME,
                evidence=evidence,
                can_resume_same_run=True,
                reason=f"Interrupted with uncommitted worktree changes ({len(evidence.dirty_files)} modified files).",
                checkpoint=checkpoint,
            )

        if evidence.failure_class == FailureClass.VERIFICATION_INTERRUPTION:
            return RecoveryReport(
                run_id=run_id,
                task_id=record.task_id,
                primary_failure=FailureClass.VERIFICATION_INTERRUPTION,
                recovery_status=RecoveryStatus.INTERRUPTED,
                recommended_action=RecoveryAction.RESUME_SAME_RUN,
                evidence=evidence,
                can_resume_same_run=True,
                reason="Interrupted during verification phase. Success cannot be assumed without complete verification.",
                checkpoint=checkpoint,
            )

        if evidence.failure_class == FailureClass.PRE_COMMIT_INTERRUPTION:
            return RecoveryReport(
                run_id=run_id,
                task_id=record.task_id,
                primary_failure=FailureClass.PRE_COMMIT_INTERRUPTION,
                recovery_status=RecoveryStatus.INTERRUPTED,
                recommended_action=RecoveryAction.RESUME_SAME_RUN,
                evidence=evidence,
                can_resume_same_run=True,
                reason="Interrupted during pre-commit / committing phase.",
                checkpoint=checkpoint,
            )

        # Default interrupted / crash
        primary = evidence.failure_class or FailureClass.WORKER_CRASH
        return RecoveryReport(
            run_id=run_id,
            task_id=record.task_id,
            primary_failure=primary,
            recovery_status=RecoveryStatus.INTERRUPTED,
            recommended_action=RecoveryAction.RESUME_SAME_RUN,
            evidence=evidence,
            can_resume_same_run=True,
            reason=f"Run interrupted ({primary.value}): PID dead, heartbeat timed out, or manager restarted.",
            checkpoint=checkpoint,
        )

    def mark_interrupted_if_orphaned(
        self,
        run_id: str,
        *,
        reason: str | None = None,
        external_pid_alive_fn: Callable[[int | None], bool] | None = None,
    ) -> RunRecord:
        """Mark an orphaned/crashed active run as INTERRUPTED or ACCOUNT_SWITCH_REQUIRED."""
        with self._lock:
            report = self.diagnose_run(run_id, external_pid_alive_fn=external_pid_alive_fn)
            record = self.store.get_run(run_id)
            if record is None:
                raise RunNotFoundError(f"Run {run_id} not found")

            if record.state in TERMINAL_STATES or record.state in (
                RunState.INTERRUPTED,
                RunState.RECOVERY_READY,
            ):
                return record

            # Handle auth/quota suspension mapping
            if report.recovery_status == RecoveryStatus.ACCOUNT_SWITCH_REQUIRED:
                if record.state != RunState.ACCOUNT_SWITCH_REQUIRED:
                    return self.store.transition_run(
                        run_id,
                        expected_version=record.state_version,
                        target_state=RunState.ACCOUNT_SWITCH_REQUIRED,
                        suspended_reason=reason or report.reason,
                        last_error=reason or report.reason,
                    )
                return record

            # If inconsistent evidence, fail run
            if report.recovery_status == RecoveryStatus.FAILED:
                return self.store.transition_run(
                    run_id,
                    expected_version=record.state_version,
                    target_state=RunState.FAILED,
                    last_error=reason or report.reason,
                )

            # Normal crash/interruption: transition to INTERRUPTED
            curr = record
            if curr.state == RunState.CREATED:
                curr = self.store.transition_run(
                    curr.run_id,
                    expected_version=curr.state_version,
                    target_state=RunState.QUEUED,
                )

            return self.store.transition_run(
                curr.run_id,
                expected_version=curr.state_version,
                target_state=RunState.INTERRUPTED,
                last_error=reason or report.reason,
            )

    def check_recovery_readiness(
        self,
        run_id: str,
        *,
        account_switched: bool = False,
        credentials_refreshed: bool = False,
        worktree_cleaned: bool = False,
    ) -> RecoveryReport:
        """Evaluate whether an interrupted or suspended run meets all criteria to transition to RECOVERY_READY."""
        with self._lock:
            report = self.diagnose_run(run_id)
            record = self.store.get_run(run_id)
            if record is None:
                raise RunNotFoundError(f"Run {run_id} not found")

            if record.state in TERMINAL_STATES:
                return report

            # For ACCOUNT_SWITCH_REQUIRED: requires account_switched or credentials_refreshed
            if record.state == RunState.ACCOUNT_SWITCH_REQUIRED:
                if not (account_switched or credentials_refreshed):
                    return report

                return RecoveryReport(
                    run_id=run_id,
                    task_id=record.task_id,
                    primary_failure=report.primary_failure,
                    recovery_status=RecoveryStatus.RECOVERY_READY,
                    recommended_action=RecoveryAction.RESUME_SAME_RUN,
                    evidence=report.evidence,
                    can_resume_same_run=True,
                    reason="Account switched and credentials refreshed. Ready for same-run resumption.",
                    checkpoint=report.checkpoint,
                )

            # For INTERRUPTED state: can transition to RECOVERY_READY
            if record.state == RunState.INTERRUPTED:
                # Transition INTERRUPTED -> RECOVERY_READY
                updated_record = self.store.transition_run(
                    run_id,
                    expected_version=record.state_version,
                    target_state=RunState.RECOVERY_READY,
                    last_error="Recovery prerequisites validated and ready",
                )
                return self.diagnose_run(run_id)

            return report

    def resume_same_run(
        self,
        run_id: str,
        *,
        worker: WorkerCallback | None = None,
        auto_spawn: bool = True,
        account_switched: bool = False,
        credentials_refreshed: bool = False,
    ) -> RunRecord:
        """Resume an interrupted or suspended run on the EXACT SAME run_id and task_id.

        Preserves:
          - task_id
          - run_id
          - worktree
          - base_head
          - current_head
          - task contract specifications
          - history and repair round
        Increments:
          - attempt counter
          - state_version
        """
        with self._lock:
            record = self.store.get_run(run_id)
            if record is None:
                raise RunNotFoundError(f"Run {run_id} not found for resumption")

            contract = self.store.get_task_contract(run_id)
            if contract is None:
                # Corrupted run
                self.store.transition_run(
                    run_id,
                    expected_version=record.state_version,
                    target_state=RunState.FAILED,
                    last_error="Inconsistent evidence: TaskContract missing on resumption",
                )
                raise InconsistentEvidenceError(f"Cannot resume run {run_id}: TaskContract is missing")

            # Check if terminal
            if record.state in TERMINAL_STATES:
                raise UnrecoverableRunError(
                    f"Cannot resume run {run_id}: already in terminal state '{record.state.value}'"
                )

            # Verify and stage state
            curr = record
            if curr.state == RunState.ACCOUNT_SWITCH_REQUIRED:
                if not (account_switched or credentials_refreshed):
                    raise UnrecoverableRunError(
                        f"Cannot resume run {run_id} in ACCOUNT_SWITCH_REQUIRED without account_switched=True or credentials_refreshed=True"
                    )

            # Increment attempt counter atomically in journal
            with self.store._lock:
                conn = self.store._get_connection()
                try:
                    now_ts = _utc_now_iso()
                    conn.execute(
                        """
                        UPDATE runs
                        SET attempt = attempt + 1,
                            last_error = NULL,
                            suspended_reason = NULL,
                            updated_at = :now
                        WHERE run_id = :run_id;
                        """,
                        {"now": now_ts, "run_id": run_id},
                    )
                finally:
                    conn.close()

            curr = self.store.get_run(run_id)
            if curr is None:
                raise RunNotFoundError(f"Run {run_id} not found after attempt increment")

            if curr.state == RunState.INTERRUPTED:
                # INTERRUPTED -> RECOVERY_READY
                curr = self.store.transition_run(
                    run_id,
                    expected_version=curr.state_version,
                    target_state=RunState.RECOVERY_READY,
                    last_error=None,
                    suspended_reason=None,
                )

            if curr.state in (RunState.RUNNING, RunState.VERIFYING, RunState.REPAIRING, RunState.COMMITTING):
                # Active run: mark interrupted first, then stage to RECOVERY_READY
                interrupted = self.mark_interrupted_if_orphaned(run_id, reason="Marked interrupted prior to resume")
                if interrupted.state == RunState.INTERRUPTED:
                    curr = self.store.transition_run(
                        run_id,
                        expected_version=interrupted.state_version,
                        target_state=RunState.RECOVERY_READY,
                    )
                elif interrupted.state == RunState.ACCOUNT_SWITCH_REQUIRED:
                    if not (account_switched or credentials_refreshed):
                        raise UnrecoverableRunError(f"Run {run_id} requires account switch before resume")
                    curr = interrupted

            # If not auto-spawning worker, transition to QUEUED
            if not auto_spawn or worker is None:
                if curr.state in (RunState.RECOVERY_READY, RunState.ACCOUNT_SWITCH_REQUIRED, RunState.BLOCKED, RunState.DECISION_REQUIRED):
                    curr = self.store.transition_run(
                        run_id,
                        expected_version=curr.state_version,
                        target_state=RunState.QUEUED,
                    )

            # Spawn worker on same run if requested
            if auto_spawn and worker is not None:
                # Spawning worker using existing run record on manager
                self.manager._spawn_worker(curr, contract, worker, worktree=curr.worktree)

            return curr

    def handle_inconsistent_evidence(
        self,
        run_id: str,
        reason: str = "Inconsistent or corrupted evidence detected",
    ) -> RunRecord:
        """Safely transition a run with corrupted evidence to FAILED without generating new IDs."""
        with self._lock:
            record = self.store.get_run(run_id)
            if record is None:
                raise RunNotFoundError(f"Run {run_id} not found")

            if record.state in TERMINAL_STATES:
                return record

            # Never generate new IDs! Terminate or block the corrupt run.
            try:
                return self.store.transition_run(
                    run_id,
                    expected_version=record.state_version,
                    target_state=RunState.FAILED,
                    last_error=reason,
                )
            except InvalidStateTransitionError:
                # Fallback to CANCELLED or BLOCKED if FAILED transition is not permitted from current state
                try:
                    return self.store.transition_run(
                        run_id,
                        expected_version=record.state_version,
                        target_state=RunState.BLOCKED,
                        last_error=reason,
                    )
                except Exception:
                    return self.store.get_run(run_id) or record

    def scan_all_runs(self) -> list[RecoveryReport]:
        """Scan and diagnose all runs in the journal."""
        runs = self.store.list_runs()
        return [self.diagnose_run(r.run_id) for r in runs]
