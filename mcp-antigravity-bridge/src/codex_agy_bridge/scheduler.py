"""Persistent Task DAG Scheduler with fixed single-worker parallelism for VNext."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any, Callable, Optional, Sequence
import uuid

from .contracts import (
    AutoCommitPolicy,
    CREDENTIAL_PATTERNS,
    RiskClass,
    RunState,
    TaskContract,
    _format_timestamp,
    _utc_now_iso,
    validate_no_credentials,
)

# Documented fixed max parallelism for Phase 6 scheduler
FIXED_MAX_PARALLELISM: int = 1


class DAGSchedulerError(Exception):
    """Base exception for all DAG scheduler errors."""

    pass


class TaskNotFoundError(DAGSchedulerError):
    """Raised when a referenced task_id does not exist in the DAG store."""

    pass


class DuplicateTaskError(DAGSchedulerError):
    """Raised when attempting to add a task with an existing task_id."""

    pass


class DependencyNotFoundError(DAGSchedulerError):
    """Raised when a task dependency refers to a non-existent task_id."""

    pass


class CyclicDependencyError(DAGSchedulerError):
    """Raised when a cycle is detected in the Task DAG."""

    pass


class CredentialSecurityError(DAGSchedulerError):
    """Raised when sensitive credential patterns are detected in task data."""

    pass


class InvalidDAGStateTransitionError(DAGSchedulerError):
    """Raised when an illegal state transition is attempted on a DAG task."""

    pass


class MaxParallelismViolationError(DAGSchedulerError):
    """Raised when max_parallelism configuration violates the fixed single-worker invariant."""

    pass


class DAGTaskState(str, Enum):
    """Exact lifecycle states for a persistent DAG task."""

    READY = "READY"
    BLOCKED_BY_DEPENDENCY = "BLOCKED_BY_DEPENDENCY"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    DECISION_REQUIRED = "DECISION_REQUIRED"
    ACCOUNT_SWITCH_REQUIRED = "ACCOUNT_SWITCH_REQUIRED"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"

    @classmethod
    def from_value(cls, val: str | DAGTaskState | RunState) -> DAGTaskState:
        if isinstance(val, cls):
            return val
        if isinstance(val, RunState):
            val = val.value
        if not isinstance(val, str):
            raise ValueError(f"Invalid DAG task state type: {type(val).__name__}")
        norm = val.strip().upper().replace("-", "_").replace(" ", "_")
        for member in cls:
            if member.value == norm or member.name == norm:
                return member
        raise ValueError(f"Unknown DAG task state: {val!r}")


# Guarded transitions for DAG tasks
DAG_ALLOWED_TRANSITIONS: dict[DAGTaskState, set[DAGTaskState]] = {
    DAGTaskState.BLOCKED_BY_DEPENDENCY: {
        DAGTaskState.READY,
        DAGTaskState.CANCELLED,
        DAGTaskState.SKIPPED,
        DAGTaskState.FAILED,
    },
    DAGTaskState.READY: {
        DAGTaskState.RUNNING,
        DAGTaskState.BLOCKED_BY_DEPENDENCY,
        DAGTaskState.CANCELLED,
        DAGTaskState.SKIPPED,
    },
    DAGTaskState.RUNNING: {
        DAGTaskState.COMPLETE,
        DAGTaskState.FAILED,
        DAGTaskState.DECISION_REQUIRED,
        DAGTaskState.ACCOUNT_SWITCH_REQUIRED,
        DAGTaskState.CANCELLED,
        DAGTaskState.READY,  # for recovery or retry
    },
    DAGTaskState.DECISION_REQUIRED: {
        DAGTaskState.READY,
        DAGTaskState.RUNNING,
        DAGTaskState.CANCELLED,
        DAGTaskState.FAILED,
    },
    DAGTaskState.ACCOUNT_SWITCH_REQUIRED: {
        DAGTaskState.READY,
        DAGTaskState.RUNNING,
        DAGTaskState.CANCELLED,
        DAGTaskState.FAILED,
    },
    DAGTaskState.FAILED: {
        DAGTaskState.READY,  # for manual retry
        DAGTaskState.BLOCKED_BY_DEPENDENCY,
    },
    DAGTaskState.COMPLETE: set(),
    DAGTaskState.CANCELLED: {DAGTaskState.READY},
    DAGTaskState.SKIPPED: {DAGTaskState.READY},
}

TERMINAL_DAG_STATES: set[DAGTaskState] = {
    DAGTaskState.COMPLETE,
    DAGTaskState.FAILED,
    DAGTaskState.CANCELLED,
    DAGTaskState.SKIPPED,
}


@dataclass
class DAGTaskSpec:
    """Specification for declaring a task within a DAG."""

    task_id: str
    dependencies: list[str] = field(default_factory=list)
    contract: TaskContract | dict[str, Any] | None = None
    objective: str | None = None
    workdir: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    priority: int = 0

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Validate DAG task specification fields and check credential safety."""
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise ValueError("task_id must be a non-empty string")

        if not isinstance(self.dependencies, (list, tuple, set)):
            raise ValueError(f"dependencies must be a sequence of strings, got {type(self.dependencies).__name__}")

        norm_deps: list[str] = []
        for d in self.dependencies:
            if not isinstance(d, str) or not d.strip():
                raise ValueError(f"Each dependency must be a non-empty string, got {d!r}")
            d_clean = d.strip()
            if d_clean == self.task_id.strip():
                raise ValueError(f"Task {self.task_id!r} cannot depend on itself")
            if d_clean not in norm_deps:
                norm_deps.append(d_clean)
        self.dependencies = norm_deps

        if self.contract is not None:
            if isinstance(self.contract, dict):
                # Validate as TaskContract
                TaskContract.from_dict(self.contract)
            elif isinstance(self.contract, TaskContract):
                self.contract.validate()
            else:
                raise ValueError(f"contract must be a TaskContract or dict, got {type(self.contract).__name__}")

        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise ValueError(f"priority must be an integer: {self.priority!r}")

        # Validate no credentials in all spec fields
        try:
            validate_no_credentials(self.task_id, "task_id")
            validate_no_credentials(self.dependencies, "dependencies")
            if self.objective is not None:
                validate_no_credentials(self.objective, "objective")
            if self.workdir is not None:
                validate_no_credentials(self.workdir, "workdir")
            validate_no_credentials(self.metadata, "metadata")
            if self.contract is not None:
                if isinstance(self.contract, TaskContract):
                    validate_no_credentials(self.contract.to_dict(), "contract")
                else:
                    validate_no_credentials(self.contract, "contract")
        except ValueError as exc:
            raise CredentialSecurityError(str(exc)) from exc


@dataclass
class DAGTaskRecord:
    """Persistent database record for a task within a DAG."""

    task_id: str
    dependencies: list[str] = field(default_factory=list)
    state: DAGTaskState = DAGTaskState.READY
    run_id: str | None = None
    checkpoint: dict[str, Any] | None = None
    last_error: str | None = None
    evidence: dict[str, Any] | None = None
    suspended_reason: str | None = None
    contract: TaskContract | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    priority: int = 0
    attempt: int = 0
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = field(default_factory=_utc_now_iso)
    started_at: str | None = None
    completed_at: str | None = None

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Validate DAGTaskRecord invariants and ensure credential safety."""
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise ValueError("task_id must be a non-empty string")

        self.state = DAGTaskState.from_value(self.state)

        if not isinstance(self.dependencies, (list, tuple, set)):
            raise ValueError(f"dependencies must be a list, got {type(self.dependencies).__name__}")
        self.dependencies = [str(d).strip() for d in self.dependencies if str(d).strip()]

        if isinstance(self.attempt, bool) or not isinstance(self.attempt, int) or self.attempt < 0:
            raise ValueError(f"attempt must be a non-negative integer: {self.attempt!r}")

        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise ValueError(f"priority must be an integer: {self.priority!r}")

        self.created_at = _format_timestamp(self.created_at) or _utc_now_iso()
        self.updated_at = _format_timestamp(self.updated_at) or _utc_now_iso()
        self.started_at = _format_timestamp(self.started_at)
        self.completed_at = _format_timestamp(self.completed_at)

        if self.contract is not None:
            if isinstance(self.contract, dict):
                self.contract = TaskContract.from_dict(self.contract)
            elif isinstance(self.contract, TaskContract):
                self.contract.validate()
            else:
                raise ValueError(f"contract must be a TaskContract or dict, got {type(self.contract).__name__}")

        # Credential safety
        try:
            validate_no_credentials(self.task_id, "task_id")
            validate_no_credentials(self.dependencies, "dependencies")
            if self.run_id is not None:
                validate_no_credentials(self.run_id, "run_id")
            if self.checkpoint is not None:
                validate_no_credentials(self.checkpoint, "checkpoint")
            if self.last_error is not None:
                validate_no_credentials(self.last_error, "last_error")
            if self.evidence is not None:
                validate_no_credentials(self.evidence, "evidence")
            if self.suspended_reason is not None:
                validate_no_credentials(self.suspended_reason, "suspended_reason")
            validate_no_credentials(self.metadata, "metadata")
            if self.contract is not None:
                validate_no_credentials(self.contract.to_dict(), "contract")
        except ValueError as exc:
            raise CredentialSecurityError(str(exc)) from exc

    def transition_to(
        self,
        target_state: DAGTaskState | str | RunState,
        *,
        run_id: str | None = None,
        checkpoint: dict[str, Any] | None = None,
        last_error: str | None = None,
        evidence: dict[str, Any] | None = None,
        suspended_reason: str | None = None,
        timestamp: str | None = None,
    ) -> None:
        """Transition task record state with guard check."""
        target = DAGTaskState.from_value(target_state)
        allowed = DAG_ALLOWED_TRANSITIONS.get(self.state, set())
        if target != self.state and target not in allowed:
            raise InvalidDAGStateTransitionError(
                f"Invalid transition for task {self.task_id}: {self.state.value} -> {target.value}"
            )

        now_ts = _format_timestamp(timestamp) or _utc_now_iso()
        self.state = target
        self.updated_at = now_ts

        if run_id is not None:
            self.run_id = run_id
        if checkpoint is not None:
            self.checkpoint = checkpoint
        if last_error is not None:
            self.last_error = last_error
        if evidence is not None:
            self.evidence = evidence
        if suspended_reason is not None:
            self.suspended_reason = suspended_reason

        if target == DAGTaskState.RUNNING and self.started_at is None:
            self.started_at = now_ts
        elif target in (DAGTaskState.COMPLETE, DAGTaskState.FAILED, DAGTaskState.CANCELLED, DAGTaskState.SKIPPED):
            self.completed_at = now_ts

        self.validate()

    def to_dict(self) -> dict[str, Any]:
        """Convert DAGTaskRecord to a JSON-safe dictionary."""
        return {
            "task_id": self.task_id,
            "dependencies": list(self.dependencies),
            "state": self.state.value,
            "run_id": self.run_id,
            "checkpoint": dict(self.checkpoint) if self.checkpoint is not None else None,
            "last_error": self.last_error,
            "evidence": dict(self.evidence) if self.evidence is not None else None,
            "suspended_reason": self.suspended_reason,
            "contract": self.contract.to_dict() if self.contract is not None else None,
            "metadata": dict(self.metadata),
            "priority": self.priority,
            "attempt": self.attempt,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DAGTaskRecord:
        """Construct DAGTaskRecord from dictionary."""
        if not isinstance(data, dict):
            raise ValueError(f"Expected dict, got {type(data).__name__}")

        contract_raw = data.get("contract")
        contract = TaskContract.from_dict(contract_raw) if isinstance(contract_raw, dict) else None

        return cls(
            task_id=data.get("task_id", ""),
            dependencies=data.get("dependencies", []),
            state=DAGTaskState.from_value(data.get("state", DAGTaskState.READY)),
            run_id=data.get("run_id"),
            checkpoint=data.get("checkpoint"),
            last_error=data.get("last_error"),
            evidence=data.get("evidence"),
            suspended_reason=data.get("suspended_reason"),
            contract=contract,
            metadata=data.get("metadata", {}),
            priority=data.get("priority", 0),
            attempt=data.get("attempt", 0),
            created_at=data.get("created_at", _utc_now_iso()),
            updated_at=data.get("updated_at", _utc_now_iso()),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
        )


@dataclass
class TaskExecutionResult:
    """Standardized result structure returned by an injectable DAG task runner."""

    success: bool
    output: str | None = None
    verification_result: Any | None = None
    result_summary: str | None = None
    commit_sha: str | None = None
    last_error: str | None = None
    suspended_reason: str | None = None
    target_state: DAGTaskState | RunState | None = None
    checkpoint: dict[str, Any] | None = None
    evidence: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.target_state is not None:
            self.target_state = DAGTaskState.from_value(self.target_state)

        # Validate against credentials in all result fields
        try:
            if self.output is not None:
                validate_no_credentials(self.output, "output")
            if self.verification_result is not None:
                validate_no_credentials(self.verification_result, "verification_result")
            if self.result_summary is not None:
                validate_no_credentials(self.result_summary, "result_summary")
            if self.commit_sha is not None:
                validate_no_credentials(self.commit_sha, "commit_sha")
            if self.last_error is not None:
                validate_no_credentials(self.last_error, "last_error")
            if self.suspended_reason is not None:
                validate_no_credentials(self.suspended_reason, "suspended_reason")
            if self.checkpoint is not None:
                validate_no_credentials(self.checkpoint, "checkpoint")
            if self.evidence is not None:
                validate_no_credentials(self.evidence, "evidence")
        except ValueError as exc:
            raise CredentialSecurityError(str(exc)) from exc

    def to_dict(self) -> dict[str, Any]:
        """Convert TaskExecutionResult to JSON-safe dictionary."""
        return {
            "success": self.success,
            "output": self.output,
            "verification_result": self.verification_result,
            "result_summary": self.result_summary,
            "commit_sha": self.commit_sha,
            "last_error": self.last_error,
            "suspended_reason": self.suspended_reason,
            "target_state": self.target_state.value if self.target_state else None,
            "checkpoint": dict(self.checkpoint) if self.checkpoint is not None else None,
            "evidence": dict(self.evidence) if self.evidence is not None else None,
        }


# Type alias for injectable runner callable
RunnerCallback = Callable[
    [DAGTaskRecord],
    Optional[TaskExecutionResult | dict[str, Any] | bool],
]


class DurableDAGStore:
    """SQLite-backed persistent store for DAG tasks and execution journal."""

    def __init__(self, db_path: str | Path) -> None:
        if db_path is None:
            raise ValueError("DurableDAGStore requires an explicit caller-supplied db_path")
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
                    CREATE TABLE IF NOT EXISTS dag_tasks (
                        task_id TEXT PRIMARY KEY,
                        dependencies_json TEXT NOT NULL DEFAULT '[]',
                        state TEXT NOT NULL,
                        run_id TEXT,
                        checkpoint_json TEXT,
                        last_error TEXT,
                        evidence_json TEXT,
                        suspended_reason TEXT,
                        task_contract_json TEXT,
                        metadata_json TEXT NOT NULL DEFAULT '{}',
                        priority INTEGER NOT NULL DEFAULT 0,
                        attempt INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        started_at TEXT,
                        completed_at TEXT
                    );
                    """
                )
                cur.execute("CREATE INDEX IF NOT EXISTS idx_dag_tasks_state ON dag_tasks(state);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_dag_tasks_priority ON dag_tasks(priority DESC, created_at ASC);")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS dag_runs (
                        run_id TEXT PRIMARY KEY,
                        task_id TEXT NOT NULL,
                        state TEXT NOT NULL,
                        started_at TEXT NOT NULL,
                        completed_at TEXT,
                        result_json TEXT,
                        error TEXT
                    );
                    """
                )
                cur.execute("CREATE INDEX IF NOT EXISTS idx_dag_runs_task_id ON dag_runs(task_id);")
            finally:
                conn.close()

    def _row_to_record(self, row: sqlite3.Row) -> DAGTaskRecord:
        deps = json.loads(row["dependencies_json"]) if row["dependencies_json"] else []
        checkpoint = json.loads(row["checkpoint_json"]) if row["checkpoint_json"] else None
        evidence = json.loads(row["evidence_json"]) if row["evidence_json"] else None
        metadata = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
        contract = None
        if row["task_contract_json"]:
            contract = TaskContract.from_json(row["task_contract_json"])

        return DAGTaskRecord(
            task_id=row["task_id"],
            dependencies=deps,
            state=DAGTaskState.from_value(row["state"]),
            run_id=row["run_id"],
            checkpoint=checkpoint,
            last_error=row["last_error"],
            evidence=evidence,
            suspended_reason=row["suspended_reason"],
            contract=contract,
            metadata=metadata,
            priority=int(row["priority"]),
            attempt=int(row["attempt"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
        )

    def insert_task(self, record: DAGTaskRecord) -> DAGTaskRecord:
        """Insert a new DAG task record into SQLite."""
        record.validate()
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute("SELECT task_id FROM dag_tasks WHERE task_id = ?;", (record.task_id,))
                if cur.fetchone() is not None:
                    raise DuplicateTaskError(f"Task with task_id {record.task_id!r} already exists in DAG")

                cur.execute(
                    """
                    INSERT INTO dag_tasks (
                        task_id, dependencies_json, state, run_id,
                        checkpoint_json, last_error, evidence_json, suspended_reason,
                        task_contract_json, metadata_json, priority, attempt,
                        created_at, updated_at, started_at, completed_at
                    ) VALUES (
                        :task_id, :dependencies_json, :state, :run_id,
                        :checkpoint_json, :last_error, :evidence_json, :suspended_reason,
                        :task_contract_json, :metadata_json, :priority, :attempt,
                        :created_at, :updated_at, :started_at, :completed_at
                    );
                    """,
                    {
                        "task_id": record.task_id,
                        "dependencies_json": json.dumps(record.dependencies),
                        "state": record.state.value,
                        "run_id": record.run_id,
                        "checkpoint_json": json.dumps(record.checkpoint) if record.checkpoint is not None else None,
                        "last_error": record.last_error,
                        "evidence_json": json.dumps(record.evidence) if record.evidence is not None else None,
                        "suspended_reason": record.suspended_reason,
                        "task_contract_json": record.contract.to_json() if record.contract is not None else None,
                        "metadata_json": json.dumps(record.metadata),
                        "priority": record.priority,
                        "attempt": record.attempt,
                        "created_at": record.created_at,
                        "updated_at": record.updated_at,
                        "started_at": record.started_at,
                        "completed_at": record.completed_at,
                    },
                )
            finally:
                conn.close()

        return record

    def update_task(self, record: DAGTaskRecord) -> DAGTaskRecord:
        """Update an existing DAG task record in SQLite."""
        record.validate()
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    UPDATE dag_tasks
                    SET dependencies_json = :dependencies_json,
                        state = :state,
                        run_id = :run_id,
                        checkpoint_json = :checkpoint_json,
                        last_error = :last_error,
                        evidence_json = :evidence_json,
                        suspended_reason = :suspended_reason,
                        task_contract_json = :task_contract_json,
                        metadata_json = :metadata_json,
                        priority = :priority,
                        attempt = :attempt,
                        updated_at = :updated_at,
                        started_at = :started_at,
                        completed_at = :completed_at
                    WHERE task_id = :task_id;
                    """,
                    {
                        "dependencies_json": json.dumps(record.dependencies),
                        "state": record.state.value,
                        "run_id": record.run_id,
                        "checkpoint_json": json.dumps(record.checkpoint) if record.checkpoint is not None else None,
                        "last_error": record.last_error,
                        "evidence_json": json.dumps(record.evidence) if record.evidence is not None else None,
                        "suspended_reason": record.suspended_reason,
                        "task_contract_json": record.contract.to_json() if record.contract is not None else None,
                        "metadata_json": json.dumps(record.metadata),
                        "priority": record.priority,
                        "attempt": record.attempt,
                        "updated_at": record.updated_at,
                        "started_at": record.started_at,
                        "completed_at": record.completed_at,
                        "task_id": record.task_id,
                    },
                )
                if cur.rowcount == 0:
                    raise TaskNotFoundError(f"Task {record.task_id!r} not found for update")
            finally:
                conn.close()

        return record

    def get_task(self, task_id: str) -> DAGTaskRecord | None:
        """Retrieve a task by its task_id."""
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute("SELECT * FROM dag_tasks WHERE task_id = ?;", (task_id,))
                row = cur.fetchone()
                if row is None:
                    return None
                return self._row_to_record(row)
            finally:
                conn.close()

    def list_tasks(self) -> list[DAGTaskRecord]:
        """List all tasks in the DAG store ordered by priority and creation time."""
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute("SELECT * FROM dag_tasks ORDER BY priority DESC, created_at ASC, task_id ASC;")
                return [self._row_to_record(row) for row in cur.fetchall()]
            finally:
                conn.close()

    def record_run(
        self,
        run_id: str,
        task_id: str,
        state: str,
        started_at: str,
        completed_at: str | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """Record execution run entry in dag_runs."""
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT OR REPLACE INTO dag_runs (
                        run_id, task_id, state, started_at, completed_at, result_json, error
                    ) VALUES (
                        :run_id, :task_id, :state, :started_at, :completed_at, :result_json, :error
                    );
                    """,
                    {
                        "run_id": run_id,
                        "task_id": task_id,
                        "state": state,
                        "started_at": started_at,
                        "completed_at": completed_at,
                        "result_json": json.dumps(result) if result is not None else None,
                        "error": error,
                    },
                )
            finally:
                conn.close()


class TaskDAGScheduler:
    """Deterministic persistent Task DAG scheduler with max parallelism strictly fixed at 1."""

    def __init__(
        self,
        db_path: str | Path,
        runner: RunnerCallback | None = None,
        max_parallelism: int = FIXED_MAX_PARALLELISM,
    ) -> None:
        if db_path is None:
            raise ValueError("TaskDAGScheduler requires an explicit caller-supplied db_path")
        if max_parallelism != FIXED_MAX_PARALLELISM:
            raise MaxParallelismViolationError(
                f"Phase 6 VNext scheduler max_parallelism is strictly fixed at {FIXED_MAX_PARALLELISM}, got {max_parallelism}"
            )

        self.db_path = Path(db_path)
        self.store = DurableDAGStore(self.db_path)
        self.runner = runner
        self.max_parallelism = FIXED_MAX_PARALLELISM
        self._lock = threading.RLock()
        self._running_task_id: str | None = None

        # Re-evaluate and recover any interrupted states upon instantiation
        self.recover()

    def add_task(
        self,
        task: DAGTaskSpec | TaskContract | dict[str, Any] | str,
        *,
        dependencies: Sequence[str] | None = None,
        priority: int = 0,
        metadata: dict[str, Any] | None = None,
        objective: str | None = None,
        workdir: str | None = None,
    ) -> DAGTaskRecord:
        """Add a single task to the DAG, validating credentials, dependencies, and state."""
        with self._lock:
            spec: DAGTaskSpec
            if isinstance(task, DAGTaskSpec):
                spec = task
            elif isinstance(task, TaskContract):
                deps = list(dependencies if dependencies is not None else task.dependencies)
                spec = DAGTaskSpec(
                    task_id=task.task_id,
                    dependencies=deps,
                    contract=task,
                    objective=task.objective,
                    workdir=task.workdir,
                    priority=priority,
                    metadata=metadata or {},
                )
            elif isinstance(task, dict):
                task_id = task.get("task_id", "")
                deps = list(dependencies if dependencies is not None else task.get("dependencies", []))
                contract = task.get("contract")
                prio = task.get("priority", priority)
                meta = task.get("metadata", metadata or {})
                obj = task.get("objective", objective)
                wdir = task.get("workdir", workdir)
                spec = DAGTaskSpec(
                    task_id=task_id,
                    dependencies=deps,
                    contract=contract,
                    objective=obj,
                    workdir=wdir,
                    priority=prio,
                    metadata=meta,
                )
            elif isinstance(task, str):
                spec = DAGTaskSpec(
                    task_id=task,
                    dependencies=list(dependencies or []),
                    priority=priority,
                    metadata=metadata or {},
                    objective=objective,
                    workdir=workdir,
                )
            else:
                raise ValueError(f"Unsupported task type for add_task: {type(task).__name__}")

            spec.validate()

            # Determine initial state: if dependencies are empty -> READY, else BLOCKED_BY_DEPENDENCY
            initial_state = DAGTaskState.READY if len(spec.dependencies) == 0 else DAGTaskState.BLOCKED_BY_DEPENDENCY

            # Convert contract if present
            contract_obj = None
            if spec.contract is not None:
                if isinstance(spec.contract, TaskContract):
                    contract_obj = spec.contract
                elif isinstance(spec.contract, dict):
                    contract_obj = TaskContract.from_dict(spec.contract)

            record = DAGTaskRecord(
                task_id=spec.task_id,
                dependencies=list(spec.dependencies),
                state=initial_state,
                contract=contract_obj,
                metadata=dict(spec.metadata),
                priority=spec.priority,
            )

            inserted = self.store.insert_task(record)
            # Recheck readiness across graph (e.g. if some upstream tasks are already COMPLETE)
            self._reconcile_ready_states()
            return inserted

    def add_tasks(self, tasks: Sequence[DAGTaskSpec | TaskContract | dict[str, Any] | str]) -> list[DAGTaskRecord]:
        """Add multiple tasks and validate DAG acyclicity."""
        with self._lock:
            added: list[DAGTaskRecord] = []
            for t in tasks:
                added.append(self.add_task(t))
            self.validate_dag()
            return added

    def validate_dag(self) -> None:
        """Validate that all referenced dependencies exist and the graph contains no cycles."""
        with self._lock:
            tasks = {t.task_id: t for t in self.store.list_tasks()}

            # 1. Dependency existence check
            for task_id, record in tasks.items():
                for dep in record.dependencies:
                    if dep not in tasks:
                        raise DependencyNotFoundError(
                            f"Task {task_id!r} depends on non-existent task {dep!r}"
                        )

            # 2. Cycle detection via DFS topological traversal
            visited: dict[str, int] = {}  # 0: unvisited, 1: visiting, 2: visited

            def _dfs(node: str, path: list[str]) -> None:
                visited[node] = 1
                for dep in tasks[node].dependencies:
                    if visited.get(dep, 0) == 1:
                        cycle_path = " -> ".join(path + [dep])
                        raise CyclicDependencyError(f"Cycle detected in Task DAG: {cycle_path}")
                    if visited.get(dep, 0) == 0:
                        _dfs(dep, path + [dep])
                visited[node] = 2

            for task_id in tasks:
                if visited.get(task_id, 0) == 0:
                    _dfs(task_id, [task_id])

    def _reconcile_ready_states(self) -> None:
        """Evaluate dependencies for all tasks and unlock downstream tasks when upstream dependencies are complete."""
        tasks = {t.task_id: t for t in self.store.list_tasks()}
        for record in tasks.values():
            if record.state == DAGTaskState.BLOCKED_BY_DEPENDENCY:
                if not record.dependencies:
                    record.transition_to(DAGTaskState.READY)
                    self.store.update_task(record)
                    continue

                # Check if all upstream dependencies are COMPLETE
                all_complete = True
                for dep_id in record.dependencies:
                    dep_task = tasks.get(dep_id)
                    if dep_task is None or dep_task.state != DAGTaskState.COMPLETE:
                        all_complete = False
                        break

                if all_complete:
                    record.transition_to(DAGTaskState.READY)
                    self.store.update_task(record)

    def get_ready_tasks(self) -> list[DAGTaskRecord]:
        """Find and return all currently eligible READY tasks, sorted deterministically."""
        with self._lock:
            self._reconcile_ready_states()
            tasks = self.store.list_tasks()
            ready = [t for t in tasks if t.state == DAGTaskState.READY]
            # Deterministic ordering: (-priority, created_at, task_id)
            ready.sort(key=lambda t: (-t.priority, t.created_at, t.task_id))
            return ready

    def get_task(self, task_id: str) -> DAGTaskRecord:
        """Retrieve task by task_id or raise TaskNotFoundError."""
        with self._lock:
            task = self.store.get_task(task_id)
            if task is None:
                raise TaskNotFoundError(f"Task {task_id!r} not found in DAG store")
            return task

    def step(self, runner: RunnerCallback | None = None) -> DAGTaskRecord | None:
        """Execute the next single ready task deterministically, enforcing max_parallelism=1."""
        with self._lock:
            # Enforce max parallelism strictly at 1
            if self._running_task_id is not None:
                active = self.store.get_task(self._running_task_id)
                if active and active.state == DAGTaskState.RUNNING:
                    return active
                self._running_task_id = None

            ready_tasks = self.get_ready_tasks()
            if not ready_tasks:
                return None

            # Pick the top deterministic ready task
            task_to_run = ready_tasks[0]
            effective_runner = runner or self.runner
            if effective_runner is None:
                raise DAGSchedulerError("No runner provided to step() and no default runner configured on scheduler")

            run_id = f"dag-run-{uuid.uuid4().hex[:12]}"
            now_ts = _utc_now_iso()

            # 1. PERSIST RUNNING STATE BEFORE EXECUTION
            task_to_run.attempt += 1
            task_to_run.transition_to(
                DAGTaskState.RUNNING,
                run_id=run_id,
                timestamp=now_ts,
            )
            self.store.update_task(task_to_run)
            self.store.record_run(run_id, task_to_run.task_id, DAGTaskState.RUNNING.value, started_at=now_ts)
            self._running_task_id = task_to_run.task_id

        # 2. DISPATCH INJECTED RUNNER (OUTSIDE LOCK TO PREVENT DEADLOCK, BUT RUNNING FLAG IS SET)
        exec_result: TaskExecutionResult
        try:
            raw_result = effective_runner(task_to_run)
            if raw_result is None:
                exec_result = TaskExecutionResult(success=True, result_summary="Completed successfully")
            elif isinstance(raw_result, bool):
                exec_result = TaskExecutionResult(
                    success=raw_result,
                    result_summary="Completed successfully" if raw_result else "Execution returned false",
                    last_error=None if raw_result else "Runner returned False",
                )
            elif isinstance(raw_result, dict):
                exec_result = TaskExecutionResult(
                    success=bool(raw_result.get("success", True)),
                    output=raw_result.get("output"),
                    verification_result=raw_result.get("verification_result"),
                    result_summary=raw_result.get("result_summary"),
                    commit_sha=raw_result.get("commit_sha"),
                    last_error=raw_result.get("last_error"),
                    suspended_reason=raw_result.get("suspended_reason"),
                    target_state=raw_result.get("target_state"),
                    checkpoint=raw_result.get("checkpoint"),
                    evidence=raw_result.get("evidence"),
                )
            elif isinstance(raw_result, TaskExecutionResult):
                exec_result = raw_result
            else:
                # Handle WorkerResult or duck-typed result
                exec_result = TaskExecutionResult(
                    success=getattr(raw_result, "success", True),
                    output=getattr(raw_result, "output", None),
                    verification_result=getattr(raw_result, "verification_result", None),
                    result_summary=getattr(raw_result, "result_summary", None),
                    commit_sha=getattr(raw_result, "commit_sha", None),
                    last_error=getattr(raw_result, "last_error", None),
                    suspended_reason=getattr(raw_result, "suspended_reason", None),
                    target_state=getattr(raw_result, "target_state", None),
                    checkpoint=getattr(raw_result, "checkpoint", None),
                    evidence=getattr(raw_result, "evidence", None),
                )
        except CredentialSecurityError:
            self._running_task_id = None
            raise
        except Exception as exc:
            exec_result = TaskExecutionResult(
                success=False,
                last_error=f"Uncaught runner exception: {exc}",
            )

        # 3. RECORD RESULT AND UNLOCK DOWNSTREAM
        with self._lock:
            completed_ts = _utc_now_iso()
            target_state: DAGTaskState

            if exec_result.target_state is not None:
                target_state = DAGTaskState.from_value(exec_result.target_state)
            elif exec_result.success:
                target_state = DAGTaskState.COMPLETE
            else:
                target_state = DAGTaskState.FAILED

            # Extract evidence combining outputs and summaries
            evidence_dict = dict(exec_result.evidence) if exec_result.evidence is not None else {}
            if exec_result.output is not None and "output" not in evidence_dict:
                evidence_dict["output"] = exec_result.output
            if exec_result.result_summary is not None and "result_summary" not in evidence_dict:
                evidence_dict["result_summary"] = exec_result.result_summary
            if exec_result.verification_result is not None and "verification_result" not in evidence_dict:
                evidence_dict["verification_result"] = exec_result.verification_result
            if exec_result.commit_sha is not None and "commit_sha" not in evidence_dict:
                evidence_dict["commit_sha"] = exec_result.commit_sha

            task_to_run.transition_to(
                target_state,
                run_id=run_id,
                checkpoint=exec_result.checkpoint,
                last_error=exec_result.last_error,
                evidence=evidence_dict if evidence_dict else None,
                suspended_reason=exec_result.suspended_reason,
                timestamp=completed_ts,
            )

            self.store.update_task(task_to_run)
            self.store.record_run(
                run_id=run_id,
                task_id=task_to_run.task_id,
                state=target_state.value,
                started_at=now_ts,
                completed_at=completed_ts,
                result=exec_result.to_dict(),
                error=exec_result.last_error,
            )

            self._running_task_id = None

            # Re-evaluate downstream tasks
            if target_state == DAGTaskState.COMPLETE:
                self._reconcile_ready_states()

            return task_to_run

    def run_all(
        self,
        runner: RunnerCallback | None = None,
        max_steps: int | None = None,
    ) -> list[DAGTaskRecord]:
        """Run tasks sequentially until no more tasks are ready or max_steps is reached."""
        executed: list[DAGTaskRecord] = []
        steps = 0
        while True:
            if max_steps is not None and steps >= max_steps:
                break
            record = self.step(runner=runner)
            if record is None:
                break
            executed.append(record)
            steps += 1
        return executed

    def recover(self) -> list[DAGTaskRecord]:
        """Recover DAG state upon scheduler startup/reconnection, resetting any orphaned RUNNING tasks to READY."""
        with self._lock:
            tasks = self.store.list_tasks()
            recovered: list[DAGTaskRecord] = []
            for t in tasks:
                if t.state == DAGTaskState.RUNNING:
                    t.transition_to(
                        DAGTaskState.READY,
                        last_error="Recovered from interrupted execution session",
                        timestamp=_utc_now_iso(),
                    )
                    self.store.update_task(t)
                    recovered.append(t)

            self._reconcile_ready_states()
            self._running_task_id = None
            return recovered

    def resolve_decision(
        self,
        task_id: str,
        *,
        checkpoint: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DAGTaskRecord:
        """Resolve a DECISION_REQUIRED suspension, updating checkpoints and marking task READY."""
        with self._lock:
            task = self.get_task(task_id)
            if task.state != DAGTaskState.DECISION_REQUIRED:
                raise InvalidDAGStateTransitionError(
                    f"Task {task_id!r} is in state {task.state.value}, expected DECISION_REQUIRED"
                )
            if metadata:
                task.metadata.update(metadata)
            task.suspended_reason = None
            task.transition_to(DAGTaskState.READY, checkpoint=checkpoint)
            self.store.update_task(task)
            return task

    def resolve_account_switch(
        self,
        task_id: str,
        *,
        checkpoint: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DAGTaskRecord:
        """Resolve an ACCOUNT_SWITCH_REQUIRED suspension, marking task READY."""
        with self._lock:
            task = self.get_task(task_id)
            if task.state != DAGTaskState.ACCOUNT_SWITCH_REQUIRED:
                raise InvalidDAGStateTransitionError(
                    f"Task {task_id!r} is in state {task.state.value}, expected ACCOUNT_SWITCH_REQUIRED"
                )
            if metadata:
                task.metadata.update(metadata)
            task.suspended_reason = None
            task.transition_to(DAGTaskState.READY, checkpoint=checkpoint)
            self.store.update_task(task)
            return task

    def retry_task(self, task_id: str) -> DAGTaskRecord:
        """Retry a failed or cancelled task by re-evaluating its dependencies and marking it READY or BLOCKED."""
        with self._lock:
            task = self.get_task(task_id)
            task.last_error = None
            task.suspended_reason = None
            task.completed_at = None
            task.transition_to(DAGTaskState.BLOCKED_BY_DEPENDENCY)
            self.store.update_task(task)
            self._reconcile_ready_states()
            return self.get_task(task_id)

    def snapshot(self) -> dict[str, Any]:
        """Generate a complete JSON-safe snapshot of DAG execution state."""
        with self._lock:
            self._reconcile_ready_states()
            tasks = self.store.list_tasks()

            tasks_dict: dict[str, Any] = {}
            ready_ids: list[str] = []
            running_ids: list[str] = []
            completed_ids: list[str] = []
            failed_ids: list[str] = []
            blocked_ids: list[str] = []
            decision_ids: list[str] = []
            switch_ids: list[str] = []

            for t in tasks:
                tasks_dict[t.task_id] = t.to_dict()
                if t.state == DAGTaskState.READY:
                    ready_ids.append(t.task_id)
                elif t.state == DAGTaskState.RUNNING:
                    running_ids.append(t.task_id)
                elif t.state == DAGTaskState.COMPLETE:
                    completed_ids.append(t.task_id)
                elif t.state == DAGTaskState.FAILED:
                    failed_ids.append(t.task_id)
                elif t.state == DAGTaskState.BLOCKED_BY_DEPENDENCY:
                    blocked_ids.append(t.task_id)
                elif t.state == DAGTaskState.DECISION_REQUIRED:
                    decision_ids.append(t.task_id)
                elif t.state == DAGTaskState.ACCOUNT_SWITCH_REQUIRED:
                    switch_ids.append(t.task_id)

            is_complete = len(tasks) > 0 and len(completed_ids) == len(tasks)
            has_failures = len(failed_ids) > 0
            is_suspended = len(decision_ids) > 0 or len(switch_ids) > 0

            return {
                "max_parallelism": self.max_parallelism,
                "total_tasks": len(tasks),
                "ready_tasks": ready_ids,
                "running_tasks": running_ids,
                "completed_tasks": completed_ids,
                "failed_tasks": failed_ids,
                "blocked_tasks": blocked_ids,
                "decision_required_tasks": decision_ids,
                "account_switch_required_tasks": switch_ids,
                "is_complete": is_complete,
                "has_failures": has_failures,
                "is_suspended": is_suspended,
                "tasks": tasks_dict,
            }
