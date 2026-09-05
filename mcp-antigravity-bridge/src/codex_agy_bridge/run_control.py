"""Standalone Windows-compatible durable run control core for VNext execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time
from typing import Any, Callable, Optional
import uuid

from .agy_runner import classify_agy_error
from .contracts import (
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
from .timeout_diagnostics import diagnose_timeout

TERMINAL_STATES: set[RunState] = {
    RunState.COMPLETE,
    RunState.FAILED,
    RunState.CANCELLED,
}

ACTIVE_STATES: set[RunState] = {
    RunState.CREATED,
    RunState.QUEUED,
    RunState.RUNNING,
    RunState.VERIFYING,
    RunState.REPAIRING,
    RunState.COMMITTING,
    RunState.BLOCKED,
    RunState.DECISION_REQUIRED,
    RunState.ACCOUNT_SWITCH_REQUIRED,
}


class RunControlError(Exception):
    """Base exception for run control operations."""

    pass


class DuplicateRunError(RunControlError):
    """Raised when an active run already exists and cannot be duplicate-spawned."""

    pass


class RunNotFoundError(RunControlError):
    """Raised when a specified run_id is not found in the journal."""

    pass


class RunNotTerminalError(RunControlError):
    """Raised when terminal result is requested for a non-terminal run."""

    pass


class ConcurrentModificationError(RunControlError):
    """Raised when state_version optimistic concurrency check fails."""

    pass


class CredentialSecurityError(RunControlError):
    """Raised when sensitive credential patterns are detected in inputs."""

    pass


def is_pid_alive(pid: int | None) -> bool:
    """Check whether a process with the given PID is currently alive."""
    if pid is None or pid <= 0:
        return False

    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32
            process_query_limited_information = 0x1000
            still_active = 259

            handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
            if not handle:
                error_access_denied = 5
                if kernel32.GetLastError() == error_access_denied:
                    return True
                return False

            try:
                exit_code = wintypes.DWORD()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return exit_code.value == still_active
                return False
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except Exception:
            return False


def _is_in_process_worker(record: RunRecord, worker_identity: dict[str, Any] | None) -> bool:
    """Determine whether a run's worker execution is an in-process thread or external process."""
    if worker_identity:
        wtype = str(worker_identity.get("worker_type") or worker_identity.get("type") or "").strip().lower()
        if wtype in ("process", "subprocess", "external", "supervised_process"):
            return False
        if wtype == "queued":
            return False
        if wtype in ("in_process", "thread", "callback", "in_process_callback", "in_process_thread"):
            return True
    if record.pid is not None and record.pid != os.getpid():
        return False
    return True


@dataclass
class WorkerResult:
    """Standard result structure returned by a worker execution."""

    success: bool
    output: str | None = None
    verification_result: Any | None = None
    result_summary: str | None = None
    commit_sha: str | None = None
    current_head: str | None = None
    last_error: str | None = None
    suspended_reason: str | None = None
    target_state: RunState | None = None
    # A result from an implementation worker is only a candidate until the
    # supervisor independently audits scope and verification evidence.
    candidate: bool = False
    terminal_reason: str | None = None
    acceptance_result: dict[str, Any] | None = None


@dataclass
class WorkerContext:
    """Context passed to injectable worker callbacks."""

    run_id: str
    task_contract: TaskContract
    cancel_event: threading.Event
    heartbeat_callback: Callable[[], None]
    record: RunRecord
    worktree: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def is_cancelled(self) -> bool:
        """Check if cooperative cancellation was requested."""
        return self.cancel_event.is_set()

    def heartbeat(self) -> None:
        """Send a heartbeat signal to durable storage."""
        self.heartbeat_callback()


WorkerCallback = Callable[[WorkerContext], Optional[WorkerResult]]


@dataclass
class RunObservation:
    """Observation outcome for a run, detecting liveness and recovery state."""

    run_id: str
    state: RunState
    state_version: int
    is_terminal: bool
    is_alive: bool
    is_stale: bool
    pid: int | None
    heartbeat: str | None
    recovery_state: RunState | None
    reason: str | None
    record: RunRecord
    timeout_diagnostic: dict[str, Any] | None = None


class DurableRunStore:
    """SQLite journal for persisting RunRecord and TaskContract data."""

    def __init__(self, db_path: str | Path) -> None:
        if db_path is None:
            raise ValueError("db_path must be an explicit path")
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,
            check_same_thread=False,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                    """
                )
                cur.execute(
                    """
                    INSERT OR IGNORE INTO schema_meta (key, value) VALUES ('schema_version', '1');
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS runs (
                        run_id TEXT PRIMARY KEY,
                        task_id TEXT NOT NULL,
                        idempotency_key TEXT,
                        task_contract_json TEXT NOT NULL,
                        state TEXT NOT NULL,
                        state_version INTEGER NOT NULL DEFAULT 1,
                        pid INTEGER,
                        heartbeat TEXT,
                        created_at TEXT NOT NULL,
                        started_at TEXT,
                        updated_at TEXT NOT NULL,
                        worktree TEXT,
                        repo TEXT,
                        base_head TEXT,
                        current_head TEXT,
                        attempt INTEGER NOT NULL DEFAULT 0,
                        repair_round INTEGER NOT NULL DEFAULT 0,
                        verification_result TEXT,
                        result_summary TEXT,
                        commit_sha TEXT,
                        last_error TEXT,
                        suspended_reason TEXT,
                        worker_identity_json TEXT
                    );
                    """
                )
                cur.execute("CREATE INDEX IF NOT EXISTS idx_runs_task_id ON runs(task_id);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_runs_idempotency_key ON runs(idempotency_key);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_runs_state ON runs(state);")
            finally:
                conn.close()

    def _row_to_record(self, row: sqlite3.Row) -> RunRecord:
        verification_result_raw = row["verification_result"]
        verification_result = None
        if verification_result_raw is not None:
            try:
                verification_result = json.loads(verification_result_raw)
            except Exception:
                verification_result = verification_result_raw

        return RunRecord(
            run_id=row["run_id"],
            task_id=row["task_id"],
            state=RunState.from_value(row["state"]),
            state_version=int(row["state_version"]),
            pid=int(row["pid"]) if row["pid"] is not None else None,
            heartbeat=row["heartbeat"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            updated_at=row["updated_at"],
            worktree=row["worktree"],
            repo=row["repo"],
            base_head=row["base_head"],
            current_head=row["current_head"],
            attempt=int(row["attempt"]),
            repair_round=int(row["repair_round"]),
            verification_result=verification_result,
            result_summary=row["result_summary"],
            commit_sha=row["commit_sha"],
            last_error=row["last_error"],
            suspended_reason=row["suspended_reason"],
        )

    def insert_run(
        self,
        record: RunRecord,
        task_contract: TaskContract,
        idempotency_key: str | None = None,
        worker_identity: dict[str, Any] | None = None,
    ) -> RunRecord:
        """Insert a newly initialized RunRecord and TaskContract into SQLite."""
        record.validate()
        task_contract.validate()

        # Sanitize against credentials before persisting
        validate_no_credentials(record.to_dict(), "run_record")
        validate_no_credentials(task_contract.to_dict(), "task_contract")
        if idempotency_key is not None:
            validate_no_credentials(idempotency_key, "idempotency_key")
        if worker_identity is not None:
            validate_no_credentials(worker_identity, "worker_identity")

        task_contract_json = task_contract.to_json()
        worker_identity_json = json.dumps(worker_identity) if worker_identity is not None else None
        verification_json = (
            json.dumps(record.verification_result)
            if record.verification_result is not None and not isinstance(record.verification_result, str)
            else (record.verification_result if isinstance(record.verification_result, str) else None)
        )

        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO runs (
                        run_id, task_id, idempotency_key, task_contract_json,
                        state, state_version, pid, heartbeat, created_at, started_at, updated_at,
                        worktree, repo, base_head, current_head, attempt, repair_round,
                        verification_result, result_summary, commit_sha, last_error,
                        suspended_reason, worker_identity_json
                    ) VALUES (
                        :run_id, :task_id, :idempotency_key, :task_contract_json,
                        :state, :state_version, :pid, :heartbeat, :created_at, :started_at, :updated_at,
                        :worktree, :repo, :base_head, :current_head, :attempt, :repair_round,
                        :verification_result, :result_summary, :commit_sha, :last_error,
                        :suspended_reason, :worker_identity_json
                    );
                    """,
                    {
                        "run_id": record.run_id,
                        "task_id": record.task_id,
                        "idempotency_key": idempotency_key,
                        "task_contract_json": task_contract_json,
                        "state": record.state.value,
                        "state_version": record.state_version,
                        "pid": record.pid,
                        "heartbeat": record.heartbeat,
                        "created_at": record.created_at,
                        "started_at": record.started_at,
                        "updated_at": record.updated_at,
                        "worktree": record.worktree,
                        "repo": record.repo,
                        "base_head": record.base_head,
                        "current_head": record.current_head,
                        "attempt": record.attempt,
                        "repair_round": record.repair_round,
                        "verification_result": verification_json,
                        "result_summary": record.result_summary,
                        "commit_sha": record.commit_sha,
                        "last_error": record.last_error,
                        "suspended_reason": record.suspended_reason,
                        "worker_identity_json": worker_identity_json,
                    },
                )
            finally:
                conn.close()

        return record

    def get_run(self, run_id: str) -> RunRecord | None:
        """Fetch a RunRecord by its unique run_id."""
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute("SELECT * FROM runs WHERE run_id = ?;", (run_id,))
                row = cur.fetchone()
                if row is None:
                    return None
                return self._row_to_record(row)
            finally:
                conn.close()

    def get_task_contract(self, run_id: str) -> TaskContract | None:
        """Fetch the TaskContract associated with a run."""
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute("SELECT task_contract_json FROM runs WHERE run_id = ?;", (run_id,))
                row = cur.fetchone()
                if row is None or not row["task_contract_json"]:
                    return None
                return TaskContract.from_json(row["task_contract_json"])
            finally:
                conn.close()

    def get_worker_identity(self, run_id: str) -> dict[str, Any] | None:
        """Fetch the worker identity metadata associated with a run."""
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute("SELECT worker_identity_json FROM runs WHERE run_id = ?;", (run_id,))
                row = cur.fetchone()
                if row is None or not row["worker_identity_json"]:
                    return None
                return json.loads(row["worker_identity_json"])
            finally:
                conn.close()

    def get_active_run_by_task_id(self, task_id: str) -> RunRecord | None:
        """Fetch an active (non-terminal) run for a task_id if one exists."""
        terminal_values = [s.value for s in TERMINAL_STATES]
        placeholders = ",".join("?" for _ in terminal_values)
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute(
                    f"SELECT * FROM runs WHERE task_id = ? AND state NOT IN ({placeholders}) ORDER BY created_at DESC LIMIT 1;",
                    (task_id, *terminal_values),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                return self._row_to_record(row)
            finally:
                conn.close()

    def get_run_by_idempotency_key(self, idempotency_key: str) -> RunRecord | None:
        """Fetch a run by its idempotency key."""
        if not idempotency_key:
            return None
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute("SELECT * FROM runs WHERE idempotency_key = ? ORDER BY created_at DESC LIMIT 1;", (idempotency_key,))
                row = cur.fetchone()
                if row is None:
                    return None
                return self._row_to_record(row)
            finally:
                conn.close()

    def update_heartbeat(self, run_id: str, timestamp: str | None = None) -> RunRecord:
        """Update the heartbeat timestamp for a run."""
        now_ts = _format_timestamp(timestamp) or _utc_now_iso()
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    UPDATE runs
                    SET heartbeat = :heartbeat, updated_at = :updated_at
                    WHERE run_id = :run_id;
                    """,
                    {"heartbeat": now_ts, "updated_at": now_ts, "run_id": run_id},
                )
                if cur.rowcount == 0:
                    raise RunNotFoundError(f"Run {run_id} not found for heartbeat update")
            finally:
                conn.close()

        record = self.get_run(run_id)
        if record is None:
            raise RunNotFoundError(f"Run {run_id} not found after heartbeat update")
        return record

    def transition_run(
        self,
        run_id: str,
        expected_version: int,
        target_state: RunState | str,
        *,
        verification_result: Any = None,
        result_summary: str | None = None,
        commit_sha: str | None = None,
        last_error: str | None = None,
        suspended_reason: str | None = None,
        current_head: str | None = None,
        repair_round: int | None = None,
        attempt: int | None = None,
        pid: int | None = None,
        timestamp: str | None = None,
    ) -> RunRecord:
        """Transition a RunRecord to a new state with optimistic concurrency check."""
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute("SELECT * FROM runs WHERE run_id = ?;", (run_id,))
                row = cur.fetchone()
                if row is None:
                    raise RunNotFoundError(f"Run {run_id} not found")

                current_record = self._row_to_record(row)
                if current_record.state_version != expected_version:
                    raise ConcurrentModificationError(
                        f"State version mismatch for run {run_id}: expected {expected_version}, got {current_record.state_version}"
                    )

                # Monotonically transition in-memory record using contracts state machine
                current_record.transition_to(
                    target_state,
                    verification_result=verification_result,
                    result_summary=result_summary,
                    commit_sha=commit_sha,
                    last_error=last_error,
                    suspended_reason=suspended_reason,
                    current_head=current_head,
                    repair_round=repair_round,
                    attempt=attempt,
                    pid=pid,
                    timestamp=timestamp,
                )

                # Persist updated values with optimistic version check
                verification_json = (
                    json.dumps(current_record.verification_result)
                    if current_record.verification_result is not None and not isinstance(current_record.verification_result, str)
                    else (current_record.verification_result if isinstance(current_record.verification_result, str) else None)
                )

                cur.execute(
                    """
                    UPDATE runs
                    SET state = :state,
                        state_version = :state_version,
                        pid = :pid,
                        heartbeat = :heartbeat,
                        started_at = :started_at,
                        updated_at = :updated_at,
                        current_head = :current_head,
                        attempt = :attempt,
                        repair_round = :repair_round,
                        verification_result = :verification_result,
                        result_summary = :result_summary,
                        commit_sha = :commit_sha,
                        last_error = :last_error,
                        suspended_reason = :suspended_reason
                    WHERE run_id = :run_id AND state_version = :expected_version;
                    """,
                    {
                        "state": current_record.state.value,
                        "state_version": current_record.state_version,
                        "pid": current_record.pid,
                        "heartbeat": current_record.heartbeat,
                        "started_at": current_record.started_at,
                        "updated_at": current_record.updated_at,
                        "current_head": current_record.current_head,
                        "attempt": current_record.attempt,
                        "repair_round": current_record.repair_round,
                        "verification_result": verification_json,
                        "result_summary": current_record.result_summary,
                        "commit_sha": current_record.commit_sha,
                        "last_error": current_record.last_error,
                        "suspended_reason": current_record.suspended_reason,
                        "run_id": run_id,
                        "expected_version": expected_version,
                    },
                )
                if cur.rowcount == 0:
                    raise ConcurrentModificationError(
                        f"Concurrent modification detected when transitioning run {run_id} at version {expected_version}"
                    )
            finally:
                conn.close()

        return current_record

    def list_runs(
        self,
        task_id: str | None = None,
        state: RunState | str | None = None,
    ) -> list[RunRecord]:
        """List all runs matching optional filters."""
        query = "SELECT * FROM runs"
        params: list[Any] = []
        conditions: list[str] = []

        if task_id is not None:
            conditions.append("task_id = ?")
            params.append(task_id)

        if state is not None:
            norm_state = RunState.from_value(state).value
            conditions.append("state = ?")
            params.append(norm_state)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY created_at DESC;"

        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute(query, params)
                return [self._row_to_record(row) for row in cur.fetchall()]
            finally:
                conn.close()


@dataclass
class _ActiveExecution:
    cancel_event: threading.Event
    thread: threading.Thread | None = None
    cancel_reason: str | None = None


def _evaluate_obs_timeout_diagnostic(
    record: RunRecord,
    is_alive: bool,
    reason: str | None,
) -> dict[str, Any] | None:
    candidate_texts = [record.last_error, record.suspended_reason, reason]
    combined = " ".join(c for c in candidate_texts if c)
    if not combined:
        return None
    err_kind = classify_agy_error(combined)
    if err_kind in ("CONNECT_TIMEOUT", "REMOTE_EXECUTION_TIMEOUT", "LOCAL_SUPERVISION_TIMEOUT", "AGY_PRINT_TIMEOUT"):
        return diagnose_timeout(
            err_kind,
            remote_progress_evidence="UNKNOWN",
            worker_alive="YES" if is_alive else "NO",
        )
    if "timed out" in combined.lower() or "timeout" in combined.lower() or "heartbeat" in combined.lower():
        return diagnose_timeout(
            "LOCAL_SUPERVISION_TIMEOUT",
            remote_progress_evidence="UNKNOWN",
            worker_alive="YES" if is_alive else "NO",
        )
    return None


class DurableRunManager:
    """Durable run controller managing persistence, execution, heartbeats, and observation."""

    _process_active_executions: dict[str, dict[str, _ActiveExecution]] = {}
    _process_active_locks: dict[str, threading.Lock] = {}
    _process_registry_lock = threading.Lock()

    def __init__(self, db_path: str | Path) -> None:
        if db_path is None:
            raise ValueError("DurableRunManager requires an explicit caller-supplied db_path")
        self.store = DurableRunStore(db_path)
        self._ownership_key = str(Path(db_path).expanduser().resolve()).casefold()
        with self._process_registry_lock:
            self._active_executions = self._process_active_executions.setdefault(self._ownership_key, {})
            self._active_lock = self._process_active_locks.setdefault(self._ownership_key, threading.Lock())

    def _finalize_observation(self, obs: RunObservation) -> RunObservation:
        diag = _evaluate_obs_timeout_diagnostic(obs.record, obs.is_alive, obs.reason)
        if diag is not None:
            obs.timeout_diagnostic = diag
        return obs

    def run_start(
        self,
        task: TaskContract | dict[str, Any],
        *,
        idempotency_key: str | None = None,
        worker: WorkerCallback | None = None,
        worker_identity: dict[str, Any] | None = None,
        worktree: str | None = None,
        repo: str | None = None,
        base_head: str | None = None,
        attempt: int = 0,
        repair_round: int = 0,
        run_id: str | None = None,
        auto_spawn: bool = True,
    ) -> RunRecord:
        """Start a new durable run with persist-before-spawn ordering and idempotency protection."""
        if isinstance(task, dict):
            contract = TaskContract.from_dict(task)
        elif isinstance(task, TaskContract):
            contract = task
        else:
            raise ValueError(f"Expected TaskContract or dict, got {type(task).__name__}")

        contract.validate()
        # Freeze the exact contract before persistence/spawn.  The worker and
        # acceptance paths re-check this digest to detect in-flight mutation.
        if not contract.is_frozen:
            contract.freeze()
        else:
            contract.assert_immutable()

        if not contract.baseline_file_hashes:
            try:
                from .acceptance import capture_baseline_snapshot

                snapshot = capture_baseline_snapshot(
                    worktree or contract.workdir,
                    isolated_worktree=contract.isolated_worktree,
                )
                contract.baseline_branch = snapshot.branch
                contract.baseline_worktree_status = list(snapshot.worktree_status)
                contract.baseline_tracked_diff = list(snapshot.tracked_diff)
                contract.baseline_file_hashes = dict(snapshot.file_hashes)
                contract.freeze()
            except Exception:
                # A non-git or synthetic workdir can still use the legacy
                # lifecycle; independent verification will fail closed later.
                pass

        # Validate security against secrets
        try:
            validate_no_credentials(contract.to_dict(), "task")
            if idempotency_key is not None:
                validate_no_credentials(idempotency_key, "idempotency_key")
            if worker_identity is not None:
                validate_no_credentials(worker_identity, "worker_identity")
        except ValueError as exc:
            raise CredentialSecurityError(str(exc)) from exc

        # Duplicate protection by idempotency_key
        if idempotency_key:
            existing_by_key = self.store.get_run_by_idempotency_key(idempotency_key)
            if existing_by_key is not None:
                return existing_by_key

        # Duplicate protection by task_id active run
        active_run = self.store.get_active_run_by_task_id(contract.task_id)
        if active_run is not None:
            raise DuplicateRunError(
                f"Active run {active_run.run_id} (state={active_run.state.value}) already exists for task {contract.task_id}"
            )

        # Determine and validate worker execution identity
        resolved_worker_identity = dict(worker_identity) if worker_identity is not None else {}
        if "worker_type" not in resolved_worker_identity and "type" not in resolved_worker_identity:
            if worker is not None or auto_spawn:
                resolved_worker_identity["worker_type"] = "in_process"
                resolved_worker_identity["type"] = "in_process"
            elif resolved_worker_identity.get("pid") is not None and resolved_worker_identity.get("pid") != os.getpid():
                resolved_worker_identity["worker_type"] = "process"
                resolved_worker_identity["type"] = "process"
            else:
                resolved_worker_identity["worker_type"] = "in_process"
                resolved_worker_identity["type"] = "in_process"

        if "pid" not in resolved_worker_identity:
            resolved_worker_identity["pid"] = os.getpid()

        # 1. PERSIST-BEFORE-SPAWN: Insert initial RunRecord into SQLite
        new_run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
        now_ts = _utc_now_iso()
        initial_record = RunRecord(
            run_id=new_run_id,
            task_id=contract.task_id,
            state=RunState.CREATED,
            state_version=1,
            pid=resolved_worker_identity.get("pid", os.getpid()),
            heartbeat=now_ts,
            created_at=now_ts,
            updated_at=now_ts,
            worktree=worktree or contract.workdir,
            repo=repo,
            base_head=base_head or contract.base_head,
            attempt=attempt,
            repair_round=repair_round,
        )

        persisted_record = self.store.insert_run(
            initial_record,
            task_contract=contract,
            idempotency_key=idempotency_key,
            worker_identity=resolved_worker_identity,
        )

        try:
            from .telemetry_hooks import record_run_start_event, telemetry_path_for
            record_run_start_event(
                run_id=persisted_record.run_id,
                task_id=contract.task_id,
                project_dir=worktree or contract.workdir,
                db_path=telemetry_path_for(self.store.db_path),
            )
        except Exception:
            pass

        # 2. SPAWN: Background execution decoupled from API caller
        if auto_spawn and worker is not None:
            self._spawn_worker(persisted_record, contract, worker, worktree=worktree)

        return persisted_record

    def _spawn_worker(
        self,
        record: RunRecord,
        contract: TaskContract,
        worker: WorkerCallback,
        worktree: str | None = None,
    ) -> None:
        """Spawn background worker execution."""
        cancel_event = threading.Event()
        execution = _ActiveExecution(cancel_event=cancel_event)

        with self._active_lock:
            self._active_executions[record.run_id] = execution

        def _worker_runner() -> None:
            current_version = record.state_version
            run_id = record.run_id

            try:
                if cancel_event.is_set():
                    cancel_reason = "Cancelled before execution"
                    with self._active_lock:
                        active = self._active_executions.get(run_id)
                        if active is not None and active.cancel_reason is not None:
                            cancel_reason = active.cancel_reason
                    self.store.transition_run(
                        run_id,
                        expected_version=current_version,
                        target_state=RunState.CANCELLED,
                        last_error=cancel_reason,
                    )
                    return

                # Transition CREATED -> QUEUED
                queued_record = self.store.transition_run(
                    run_id,
                    expected_version=current_version,
                    target_state=RunState.QUEUED,
                )
                current_version = queued_record.state_version

                if cancel_event.is_set():
                    cancel_reason = "Cancelled before execution"
                    with self._active_lock:
                        active = self._active_executions.get(run_id)
                        if active is not None and active.cancel_reason is not None:
                            cancel_reason = active.cancel_reason
                    self.store.transition_run(
                        run_id,
                        expected_version=current_version,
                        target_state=RunState.CANCELLED,
                        last_error=cancel_reason,
                    )
                    return

                # Transition QUEUED -> RUNNING
                running_record = self.store.transition_run(
                    run_id,
                    expected_version=current_version,
                    target_state=RunState.RUNNING,
                    pid=os.getpid(),
                    timestamp=_utc_now_iso(),
                )
                current_version = running_record.state_version
                self.store.update_heartbeat(run_id)

                try:
                    from .telemetry_hooks import record_worker_launch_event, telemetry_path_for
                    record_worker_launch_event(
                        run_id=run_id,
                        task_id=contract.task_id,
                        project_dir=worktree or contract.workdir,
                        attempt=running_record.attempt,
                        repair_round=running_record.repair_round,
                        worker_identity=self.store.get_worker_identity(run_id),
                        db_path=telemetry_path_for(self.store.db_path),
                    )
                except Exception:
                    pass

                # Prepare WorkerContext
                ctx = WorkerContext(
                    run_id=run_id,
                    task_contract=contract,
                    cancel_event=cancel_event,
                    heartbeat_callback=lambda: self.store.update_heartbeat(run_id),
                    record=running_record,
                    worktree=worktree or contract.workdir,
                )

                # Execute injectable worker with duration measurement
                worker_t0 = time.monotonic()
                worker_result = worker(ctx)
                worker_dur = max(0.0, time.monotonic() - worker_t0)
                contract.assert_immutable()

                # Handle cancellation
                if cancel_event.is_set():
                    latest = self.store.get_run(run_id)
                    if latest and latest.state not in TERMINAL_STATES:
                        cancel_reason = "Worker cancelled cooperatively"
                        with self._active_lock:
                            active = self._active_executions.get(run_id)
                            if active is not None and active.cancel_reason is not None:
                                cancel_reason = active.cancel_reason
                        self.store.transition_run(
                            run_id,
                            expected_version=latest.state_version,
                            target_state=RunState.CANCELLED,
                            last_error=cancel_reason,
                        )
                    return

                # Process WorkerResult
                if worker_result is None:
                    # Default empty success
                    worker_result = WorkerResult(success=True, result_summary="Completed successfully")

                try:
                    from .telemetry_hooks import (
                        record_account_switch_event,
                        record_worker_completion_event,
                        telemetry_path_for,
                    )
                    record_worker_completion_event(
                        run_id=run_id,
                        task_id=contract.task_id,
                        project_dir=worktree or contract.workdir,
                        duration_seconds=worker_dur,
                        success=bool(worker_result.success),
                        target_state=worker_result.target_state.value if worker_result.target_state else None,
                        last_error=worker_result.last_error,
                        verification_result=worker_result.verification_result,
                        db_path=telemetry_path_for(self.store.db_path),
                    )
                    if worker_result.target_state == RunState.ACCOUNT_SWITCH_REQUIRED:
                        record_account_switch_event(
                            run_id=run_id,
                            task_id=contract.task_id,
                            project_dir=worktree or contract.workdir,
                            reason=worker_result.suspended_reason or worker_result.last_error,
                            db_path=telemetry_path_for(self.store.db_path),
                        )
                except Exception:
                    pass

                latest = self.store.get_run(run_id)
                if (
                    latest is None
                    or latest.state in TERMINAL_STATES
                    or latest.state in (RunState.INTERRUPTED, RunState.RECOVERY_READY)
                ):
                    return
                current_version = latest.state_version

                if worker_result.success:
                    if worker_result.candidate:
                        acceptance = self._accept_candidate(
                            contract,
                            worker_result,
                            worktree=worktree or contract.workdir,
                        )
                        worker_result.acceptance_result = acceptance.to_dict()
                        if not acceptance.task_accepted:
                            self.store.transition_run(
                                run_id,
                                expected_version=current_version,
                                target_state=RunState.VERIFYING,
                                verification_result=acceptance.to_dict(),
                            )
                            self.store.transition_run(
                                run_id,
                                expected_version=current_version + 1,
                                target_state=RunState.FAILED,
                                result_summary=f"Candidate rejected: {acceptance.acceptance.value}",
                                last_error="; ".join(acceptance.reasons) or "Candidate did not pass independent acceptance",
                                verification_result=acceptance.to_dict(),
                            )
                            return
                        # Preserve the independent acceptance payload below.
                        worker_result.verification_result = acceptance.to_dict()
                    # Guarded transition: RUNNING -> VERIFYING -> COMPLETE (or COMMITTING -> COMPLETE)
                    verif_payload = worker_result.verification_result or {
                        "passed": True,
                        "status": "passed",
                        "returncode": 0,
                    }

                    verifying_record = self.store.transition_run(
                        run_id,
                        expected_version=current_version,
                        target_state=RunState.VERIFYING,
                        verification_result=verif_payload,
                        current_head=worker_result.current_head,
                    )
                    current_version = verifying_record.state_version

                    if worker_result.commit_sha or contract.auto_commit_policy == AutoCommitPolicy.ALWAYS:
                        committing_record = self.store.transition_run(
                            run_id,
                            expected_version=current_version,
                            target_state=RunState.COMMITTING,
                            commit_sha=worker_result.commit_sha or "auto-committed",
                        )
                        current_version = committing_record.state_version

                    self.store.transition_run(
                        run_id,
                        expected_version=current_version,
                        target_state=RunState.COMPLETE,
                        verification_result=verif_payload,
                        result_summary=worker_result.result_summary or "Execution verified and complete",
                        commit_sha=worker_result.commit_sha,
                        current_head=worker_result.current_head,
                    )
                else:
                    # Failure or custom target state
                    target = worker_result.target_state or RunState.FAILED
                    err_msg = worker_result.last_error or "Worker returned failure"
                    if target in (RunState.DECISION_REQUIRED, RunState.BLOCKED, RunState.ACCOUNT_SWITCH_REQUIRED):
                        self.store.transition_run(
                            run_id,
                            expected_version=current_version,
                            target_state=target,
                            suspended_reason=worker_result.suspended_reason or err_msg,
                            last_error=err_msg,
                        )
                    elif target == RunState.REPAIRING:
                        verifying_record = self.store.transition_run(
                            run_id,
                            expected_version=current_version,
                            target_state=RunState.VERIFYING,
                            verification_result={"passed": False, "status": "needs_repair"},
                        )
                        self.store.transition_run(
                            run_id,
                            expected_version=verifying_record.state_version,
                            target_state=RunState.REPAIRING,
                            last_error=err_msg,
                        )
                    else:
                        self.store.transition_run(
                            run_id,
                            expected_version=current_version,
                            target_state=RunState.FAILED,
                            last_error=err_msg,
                            verification_result=worker_result.verification_result,
                        )

            except Exception as exc:
                try:
                    from .telemetry_hooks import record_worker_completion_event, telemetry_path_for
                    record_worker_completion_event(
                        run_id=run_id,
                        task_id=contract.task_id,
                        project_dir=worktree or contract.workdir,
                        duration_seconds=max(0.0, time.monotonic() - worker_t0) if "worker_t0" in locals() else 0.0,
                        success=False,
                        last_error=str(exc),
                        db_path=telemetry_path_for(self.store.db_path),
                    )
                except Exception:
                    pass

                latest = self.store.get_run(run_id)
                if (
                    latest is not None
                    and latest.state not in TERMINAL_STATES
                    and latest.state not in (RunState.INTERRUPTED, RunState.RECOVERY_READY)
                ):
                    try:
                        self.store.transition_run(
                            run_id,
                            expected_version=latest.state_version,
                            target_state=RunState.FAILED,
                            last_error=f"Uncaught worker exception: {exc}",
                        )
                    except Exception:
                        pass
            finally:
                with self._active_lock:
                    self._active_executions.pop(run_id, None)

        thread = threading.Thread(target=_worker_runner, name=f"Worker-{record.run_id}", daemon=True)
        execution.thread = thread
        thread.start()

    def _accept_candidate(
        self,
        contract: TaskContract,
        worker_result: WorkerResult,
        *,
        worktree: str,
    ):
        """Run supervisor-owned verification for a candidate worker result."""
        from .acceptance import (
            BaselineSnapshot,
            ScopeAudit,
            WorkerTerminalReason,
            audit_candidate_scope,
            evaluate_candidate,
        )

        baseline = BaselineSnapshot(
            head=contract.base_head,
            branch=contract.baseline_branch,
            worktree_status=tuple(contract.baseline_worktree_status),
            tracked_diff=tuple(contract.baseline_tracked_diff),
            file_hashes=dict(contract.baseline_file_hashes),
            isolated_worktree=contract.isolated_worktree,
        )
        audit = audit_candidate_scope(contract, worktree, baseline)
        independent = False
        if contract.verification_commands:
            from .verification import run_verification

            evidence = run_verification(contract, workdir=worktree, run_id=f"{worker_result.result_summary or contract.task_id}-acceptance")
            independent = bool(evidence.passed and evidence.scope_passed)
        return evaluate_candidate(
            worker_result=worker_result.terminal_reason or WorkerTerminalReason.COMPLETED,
            scope_audit=audit,
            independently_verified=independent,
            risk_class=contract.risk_class,
        )

    def run_status(self, run_id: str) -> RunRecord:
        """Fetch current durable state of a run."""
        record = self.store.get_run(run_id)
        if record is None:
            raise RunNotFoundError(f"Run {run_id} not found")
        return record

    def run_observe(
        self,
        run_id: str,
        stale_heartbeat_threshold_seconds: float = 60.0,
    ) -> RunObservation:
        """Observe run status, checking process and heartbeat liveness and exposing recovery state."""
        record = self.store.get_run(run_id)
        if record is None:
            raise RunNotFoundError(f"Run {run_id} not found")

        # Terminal runs
        if record.state in TERMINAL_STATES:
            return self._finalize_observation(
                RunObservation(
                    run_id=run_id,
                    state=record.state,
                    state_version=record.state_version,
                    is_terminal=True,
                    is_alive=False,
                    is_stale=False,
                    pid=record.pid,
                    heartbeat=record.heartbeat,
                    recovery_state=None,
                    reason=None,
                    record=record,
                )
            )

        # If already INTERRUPTED or RECOVERY_READY
        if record.state in (RunState.INTERRUPTED, RunState.RECOVERY_READY):
            return self._finalize_observation(
                RunObservation(
                    run_id=run_id,
                    state=record.state,
                    state_version=record.state_version,
                    is_terminal=False,
                    is_alive=False,
                    is_stale=True,
                    pid=record.pid,
                    heartbeat=record.heartbeat,
                    recovery_state=RunState.RECOVERY_READY,
                    reason=record.last_error or "Run previously interrupted",
                    record=record,
                )
            )

        # Check in-memory active execution
        with self._active_lock:
            active_exec = self._active_executions.get(run_id)
            thread_alive = (
                active_exec is not None
                and active_exec.thread is not None
                and active_exec.thread.is_alive()
            )

        worker_identity = self.store.get_worker_identity(run_id)
        is_in_process = _is_in_process_worker(record, worker_identity)

        # In-process callback execution
        if is_in_process:
            if thread_alive:
                return self._finalize_observation(
                    RunObservation(
                        run_id=run_id,
                        state=record.state,
                        state_version=record.state_version,
                        is_terminal=False,
                        is_alive=True,
                        is_stale=False,
                        pid=record.pid,
                        heartbeat=record.heartbeat,
                        recovery_state=None,
                        reason=None,
                        record=record,
                    )
                )
            else:
                # Manager was recreated or worker thread died without updating terminal state
                interrupted_record = self._mark_interrupted(
                    record,
                    reason="In-process callback worker is no longer active (manager recreated or thread terminated)",
                )
                return self._finalize_observation(
                    RunObservation(
                        run_id=run_id,
                        state=interrupted_record.state,
                        state_version=interrupted_record.state_version,
                        is_terminal=False,
                        is_alive=False,
                        is_stale=True,
                        pid=record.pid,
                        heartbeat=record.heartbeat,
                        recovery_state=RunState.RECOVERY_READY,
                        reason=interrupted_record.last_error or "In-process callback worker absent",
                        record=interrupted_record,
                    )
                )

        # External supervised process execution
        pid_alive = is_pid_alive(record.pid)
        if record.pid is not None and not pid_alive:
            interrupted_record = self._mark_interrupted(
                record,
                reason=f"Worker process (PID {record.pid}) is no longer alive",
            )
            return self._finalize_observation(
                RunObservation(
                    run_id=run_id,
                    state=interrupted_record.state,
                    state_version=interrupted_record.state_version,
                    is_terminal=False,
                    is_alive=False,
                    is_stale=True,
                    pid=record.pid,
                    heartbeat=record.heartbeat,
                    recovery_state=RunState.RECOVERY_READY,
                    reason=interrupted_record.last_error or "In-process callback worker absent",
                    record=interrupted_record,
                )
            )

        # External process heartbeat staleness check
        if record.heartbeat:
            try:
                hb_dt = datetime.fromisoformat(record.heartbeat)
                if hb_dt.tzinfo is None:
                    hb_dt = hb_dt.replace(tzinfo=timezone.utc)
                age_seconds = (datetime.now(timezone.utc) - hb_dt).total_seconds()
                if age_seconds > stale_heartbeat_threshold_seconds:
                    interrupted_record = self._mark_interrupted(
                        record,
                        reason=f"Worker heartbeat timed out (age={age_seconds:.1f}s > {stale_heartbeat_threshold_seconds}s)",
                    )
                    return self._finalize_observation(
                        RunObservation(
                            run_id=run_id,
                            state=interrupted_record.state,
                            state_version=interrupted_record.state_version,
                            is_terminal=False,
                            is_alive=False,
                            is_stale=True,
                            pid=record.pid,
                            heartbeat=record.heartbeat,
                            recovery_state=RunState.RECOVERY_READY,
                            reason="Heartbeat timed out",
                            record=interrupted_record,
                        )
                    )
            except Exception:
                pass

        # External process is active and healthy
        return self._finalize_observation(
            RunObservation(
                run_id=run_id,
                state=record.state,
                state_version=record.state_version,
                is_terminal=False,
                is_alive=True,
                is_stale=False,
                pid=record.pid,
                heartbeat=record.heartbeat,
                recovery_state=None,
                reason=None,
                record=record,
            )
        )

    def _mark_interrupted(self, record: RunRecord, reason: str) -> RunRecord:
        """Safely transition an orphaned active run to INTERRUPTED state."""
        if record.state in TERMINAL_STATES or record.state in (RunState.INTERRUPTED, RunState.RECOVERY_READY):
            return record

        curr = record
        if curr.state == RunState.CREATED:
            try:
                curr = self.store.transition_run(
                    curr.run_id,
                    expected_version=curr.state_version,
                    target_state=RunState.QUEUED,
                )
            except (InvalidStateTransitionError, ConcurrentModificationError):
                latest = self.store.get_run(curr.run_id)
                if latest is not None:
                    curr = latest

        try:
            interrupted = self.store.transition_run(
                curr.run_id,
                expected_version=curr.state_version,
                target_state=RunState.INTERRUPTED,
                last_error=reason,
            )
            try:
                from .telemetry_hooks import (
                    record_reconciliation_event,
                    record_timeout_event,
                    telemetry_path_for,
                )
                if "timed out" in reason.lower() or "timeout" in reason.lower():
                    record_timeout_event(
                        run_id=interrupted.run_id,
                        task_id=interrupted.task_id,
                        project_dir=interrupted.worktree,
                        timeout_class="LOCAL_SUPERVISION_TIMEOUT",
                        error_text=reason,
                        db_path=telemetry_path_for(self.store.db_path),
                    )
                record_reconciliation_event(
                    run_id=interrupted.run_id,
                    task_id=interrupted.task_id,
                    project_dir=interrupted.worktree,
                    action="mark_interrupted",
                    reason=reason,
                    db_path=telemetry_path_for(self.store.db_path),
                )
            except Exception:
                pass
            return interrupted
        except (InvalidStateTransitionError, ConcurrentModificationError):
            latest = self.store.get_run(record.run_id)
            return latest if latest is not None else curr

    def run_wait(
        self,
        run_id: str,
        timeout: float | None = None,
        poll_interval: float = 0.05,
    ) -> RunRecord:
        """Wait for a run to reach a terminal state with bounded timeout that NEVER cancels worker."""
        deadline = (time.monotonic() + timeout) if timeout is not None else None

        while True:
            record = self.store.get_run(run_id)
            if record is None:
                raise RunNotFoundError(f"Run {run_id} not found")

            if record.state in TERMINAL_STATES:
                return record

            if deadline is not None and time.monotonic() >= deadline:
                # Timeout elapsed: Return current state without cancelling worker
                return record

            time.sleep(poll_interval)

    def run_result(self, run_id: str) -> RunRecord:
        """Retrieve terminal result evidence for a run; raises RunNotTerminalError if still active."""
        record = self.store.get_run(run_id)
        if record is None:
            raise RunNotFoundError(f"Run {run_id} not found")

        if record.state not in TERMINAL_STATES:
            raise RunNotTerminalError(
                f"Run {run_id} is in non-terminal state '{record.state.value}'. "
                f"Use run_wait() or run_status() to inspect active progress."
            )

        return record

    def run_cancel(
        self,
        run_id: str,
        reason: str = "User requested cancellation",
    ) -> RunRecord:
        """Cooperatively request cancellation and transition to CANCELLED without killing processes."""
        with self._active_lock:
            active = self._active_executions.get(run_id)
            if active is not None:
                active.cancel_reason = reason
                active.cancel_event.set()

        record = self.store.get_run(run_id)
        if record is None:
            raise RunNotFoundError(f"Run {run_id} not found")

        if record.state in TERMINAL_STATES:
            return record

        try:
            return self.store.transition_run(
                run_id,
                expected_version=record.state_version,
                target_state=RunState.CANCELLED,
                last_error=reason,
            )
        except (InvalidStateTransitionError, ConcurrentModificationError):
            # If direct transition is blocked (e.g. from COMMITTING), observe current state
            latest = self.store.get_run(run_id)
            if latest is not None and latest.state not in TERMINAL_STATES:
                try:
                    return self.store.transition_run(
                        run_id,
                        expected_version=latest.state_version,
                        target_state=RunState.CANCELLED,
                        last_error=reason,
                    )
                except (InvalidStateTransitionError, ConcurrentModificationError):
                    latest = self.store.get_run(run_id)
            return latest if latest is not None else record

    def heartbeat(self, run_id: str) -> RunRecord:
        """Explicitly record a heartbeat pulse for a run."""
        return self.store.update_heartbeat(run_id)

    def list_runs(
        self,
        task_id: str | None = None,
        state: RunState | str | None = None,
    ) -> list[RunRecord]:
        """List runs in the journal matching optional filters."""
        return self.store.list_runs(task_id=task_id, state=state)

    def get_task_contract(self, run_id: str) -> TaskContract:
        """Retrieve the TaskContract specification for a run."""
        contract = self.store.get_task_contract(run_id)
        if contract is None:
            raise RunNotFoundError(f"Task contract for run {run_id} not found")
        return contract
