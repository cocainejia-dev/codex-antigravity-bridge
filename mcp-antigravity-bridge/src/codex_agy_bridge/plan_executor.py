"""Durable serial execution of a frozen TaskPlan.

The executor is deliberately an orchestration layer. Child execution and
candidate acceptance remain owned by :mod:`run_control`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path
import sqlite3
import subprocess
import threading
import time
from typing import Any, Callable
import uuid

from .contracts import TaskContract
from .run_control import DurableRunManager, RunState, WorkerCallback
from .task_shaping import ShapedTask, TaskPlan, validate_task_plan
from .contracts import RiskClass
from .worker_binding import build_worker_callback


class PlanExecutionState(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    FINAL_VERIFYING = "FINAL_VERIFYING"
    COMPLETE = "COMPLETE"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"


class PlanTaskState(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    ACCEPTED = "ACCEPTED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


TERMINAL_PLAN_STATES = {
    PlanExecutionState.COMPLETE,
    PlanExecutionState.BLOCKED,
    PlanExecutionState.FAILED,
    PlanExecutionState.CANCELLED,
}

_EXECUTION_LOCKS: dict[str, threading.Lock] = {}
_EXECUTION_LOCKS_GUARD = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git(worktree: str, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", worktree, *args], capture_output=True, text=True, timeout=15
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _plan_from_dict(raw: dict[str, Any]) -> TaskPlan:
    """Reconstruct a frozen TaskPlan without trusting stored child contracts."""
    tasks = []
    for raw_task in raw.get("tasks", []):
        tasks.append(
            ShapedTask(
                task_id=raw_task["task_id"],
                objective=raw_task["objective"],
                acceptance_criteria=list(raw_task.get("acceptance_criteria", [])),
                allowed_paths=list(raw_task.get("allowed_paths", [])),
                forbidden_paths=list(raw_task.get("forbidden_paths", [])),
                verification_commands=list(raw_task.get("verification_commands", [])),
                risk_class=RiskClass.from_value(raw_task.get("risk_class")),
                dependencies=list(raw_task.get("dependencies", [])),
                covers_objectives=list(raw_task.get("covers_objectives", [])),
                covers_acceptance=list(raw_task.get("covers_acceptance", [])),
                path_ownership_reason=raw_task.get("path_ownership_reason", ""),
                shared_path=bool(raw_task.get("shared_path")),
                dependency_order_required=bool(
                    raw_task.get("dependency_order_required")
                ),
                verification_cost_class=raw_task.get(
                    "verification_cost_class", "MODERATE"
                ),
                size_class=raw_task.get("size_class", "SMALL"),
                shaping_warning=bool(raw_task.get("shaping_warning")),
                base_head_resolution=raw_task.get(
                    "base_head_resolution", "INITIAL_BASE_HEAD"
                ),
                isolated_worktree=bool(raw_task.get("isolated_worktree")),
                max_runtime=raw_task.get("task_contract", {}).get("max_runtime", 1800),
                max_repair_rounds=raw_task.get("task_contract", {}).get(
                    "max_repair_rounds", 2
                ),
            )
        )
    plan = TaskPlan(
        plan_id=raw["plan_id"],
        parent_objective=raw["parent_objective"],
        parent_acceptance=list(raw.get("parent_acceptance", [])),
        base_head=raw["base_head"],
        workdir=raw["workdir"],
        created_at=raw["created_at"],
        shaping_reason=raw.get("shaping_reason", ""),
        shaping_required=bool(raw.get("shaping_required")),
        tasks=tasks,
        dependency_edges=list(raw.get("dependency_edges", [])),
        objective_coverage=dict(raw.get("objective_coverage", {})),
        acceptance_coverage=dict(raw.get("acceptance_coverage", {})),
        path_ownership=list(raw.get("path_ownership", [])),
        risk_summary=dict(raw.get("risk_summary", {})),
        verification_summary=dict(raw.get("verification_summary", {})),
        final_verification=list(raw.get("final_verification", [])),
    )
    validation = validate_task_plan(plan, allow_unfrozen=True)
    if not validation.valid:
        raise ValueError("Invalid TaskPlan: " + "; ".join(validation.reasons))
    plan._plan_digest = raw.get("plan_digest")
    plan.assert_immutable()
    return plan


@dataclass
class PlanExecutionRecord:
    plan_execution_id: str
    plan_id: str
    plan_digest: str
    state: PlanExecutionState
    state_version: int
    initial_base_head: str
    current_accepted_head: str
    integration_worktree: str
    active_task_id: str | None
    active_run_id: str | None
    tasks: dict[str, dict[str, Any]]
    plan_snapshot: dict[str, Any]
    final_verification_result: dict[str, Any] | None = None
    last_error: str | None = None
    terminal_reason: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_execution_id": self.plan_execution_id,
            "plan_id": self.plan_id,
            "plan_digest": self.plan_digest,
            "state": self.state.value,
            "state_version": self.state_version,
            "initial_base_head": self.initial_base_head,
            "current_accepted_head": self.current_accepted_head,
            "integration_worktree": self.integration_worktree,
            "active_task_id": self.active_task_id,
            "active_run_id": self.active_run_id,
            "tasks": self.tasks,
            "plan_snapshot": self.plan_snapshot,
            "final_verification_result": self.final_verification_result,
            "last_error": self.last_error,
            "terminal_reason": self.terminal_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class PlanExecutionStore:
    """Additive SQLite journal; existing ``runs`` rows remain untouched."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS plan_executions (plan_execution_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, plan_digest TEXT NOT NULL, state TEXT NOT NULL, state_version INTEGER NOT NULL, payload_json TEXT NOT NULL, idempotency_key TEXT UNIQUE, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_plan_execution_state ON plan_executions(state)"
            )

    def create(
        self, record: PlanExecutionRecord, idempotency_key: str | None = None
    ) -> PlanExecutionRecord:
        payload = json.dumps(record.to_dict(), sort_keys=True)
        with self._lock, sqlite3.connect(self.db_path) as conn:
            if idempotency_key:
                row = conn.execute(
                    "SELECT payload_json FROM plan_executions WHERE idempotency_key=?",
                    (idempotency_key,),
                ).fetchone()
                if row:
                    return self._decode(row[0])
            conn.execute(
                "INSERT INTO plan_executions VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    record.plan_execution_id,
                    record.plan_id,
                    record.plan_digest,
                    record.state.value,
                    record.state_version,
                    payload,
                    idempotency_key,
                    record.created_at,
                    record.updated_at,
                ),
            )
        return record

    def get(self, execution_id: str) -> PlanExecutionRecord | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT payload_json FROM plan_executions WHERE plan_execution_id=?",
                (execution_id,),
            ).fetchone()
        return self._decode(row[0]) if row else None

    def save(self, record: PlanExecutionRecord) -> PlanExecutionRecord:
        record.state_version += 1
        record.updated_at = _now()
        payload = json.dumps(record.to_dict(), sort_keys=True)
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE plan_executions SET state=?, state_version=?, payload_json=?, updated_at=? WHERE plan_execution_id=?",
                (
                    record.state.value,
                    record.state_version,
                    payload,
                    record.updated_at,
                    record.plan_execution_id,
                ),
            )
        return record

    @staticmethod
    def _decode(payload: str) -> PlanExecutionRecord:
        raw = json.loads(payload)
        raw["state"] = PlanExecutionState(raw["state"])
        return PlanExecutionRecord(**raw)


class PlanExecutor:
    """Single-active-child, restart-safe serial plan orchestrator."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        worker_factory: Callable[[TaskContract], WorkerCallback] | None = None,
    ) -> None:
        self.store = PlanExecutionStore(db_path)
        self.worker_factory = worker_factory or (
            lambda contract: build_worker_callback(contract)
        )
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.RLock()

    def start(
        self,
        plan: TaskPlan | dict[str, Any],
        *,
        integration_worktree: str,
        initial_base_head: str,
        execution_id: str | None = None,
        idempotency_key: str | None = None,
        allow_high_risk_tasks: bool = False,
    ) -> PlanExecutionRecord:
        if isinstance(plan, dict):
            plan = _plan_from_dict(plan)
        plan.assert_immutable()
        if not plan.is_frozen:
            raise ValueError("TaskPlan must be frozen")
        if not Path(integration_worktree).is_absolute():
            raise ValueError("integration_worktree must be absolute")
        if not allow_high_risk_tasks and any(
            task.risk_class.value in {"HIGH", "DESTRUCTIVE", "PRODUCTION"}
            for task in plan.tasks
        ):
            raise ValueError("HIGH_RISK_AUTHORIZATION_REQUIRED")
        tasks = {
            task.task_id: {
                "task_id": task.task_id,
                "sequence": index,
                "dependencies": list(task.dependencies),
                "execution_state": PlanTaskState.PENDING.value,
                "resolved_base_head": None,
                "resolved_worktree": integration_worktree,
                "child_run_id": None,
                "child_run_state": None,
                "accepted_checkpoint_sha": None,
                "attempt": 0,
                "last_error": None,
            }
            for index, task in enumerate(plan.tasks, 1)
        }
        now = _now()
        record = PlanExecutionRecord(
            execution_id or f"plan-exec-{uuid.uuid4().hex[:12]}",
            plan.plan_id,
            plan.plan_digest or "",
            PlanExecutionState.CREATED,
            1,
            initial_base_head,
            initial_base_head,
            integration_worktree,
            None,
            None,
            tasks,
            plan.to_dict(),
            created_at=now,
            updated_at=now,
        )
        record = self.store.create(record, idempotency_key)
        self._launch(record.plan_execution_id)
        return record

    def _launch(self, execution_id: str) -> None:
        with self._lock:
            thread = self._threads.get(execution_id)
            if thread and thread.is_alive():
                return
            thread = threading.Thread(
                target=self._orchestrate,
                args=(execution_id,),
                daemon=True,
                name=f"PlanExecutor-{execution_id}",
            )
            self._threads[execution_id] = thread
            thread.start()

    def resume(self, execution_id: str) -> PlanExecutionRecord:
        record = self.store.get(execution_id)
        if record is None:
            raise KeyError(execution_id)
        if record.state not in TERMINAL_PLAN_STATES:
            self._launch(execution_id)
        return record

    def status(self, execution_id: str) -> PlanExecutionRecord:
        record = self.store.get(execution_id)
        if record is None:
            raise KeyError(execution_id)
        return record

    def cancel(self, execution_id: str) -> PlanExecutionRecord:
        record = self.status(execution_id)
        if record.active_run_id:
            DurableRunManager(self.store.db_path).run_cancel(
                record.active_run_id, "Plan cancellation requested"
            )
        if record.state not in TERMINAL_PLAN_STATES:
            record.state = PlanExecutionState.CANCELLED
            for task in record.tasks.values():
                if task["execution_state"] in {
                    PlanTaskState.PENDING.value,
                    PlanTaskState.READY.value,
                }:
                    task["execution_state"] = PlanTaskState.CANCELLED.value
            self.store.save(record)
        return record

    def wait(
        self, execution_id: str, timeout: float | None = None
    ) -> PlanExecutionRecord:
        deadline = time.monotonic() + timeout if timeout is not None else None
        while True:
            record = self.status(execution_id)
            if record.state in TERMINAL_PLAN_STATES or (
                deadline is not None and time.monotonic() >= deadline
            ):
                return record
            time.sleep(0.05)

    def result(self, execution_id: str) -> PlanExecutionRecord:
        record = self.status(execution_id)
        if record.state not in TERMINAL_PLAN_STATES:
            raise RuntimeError("PLAN_NOT_TERMINAL")
        return record

    def _orchestrate(self, execution_id: str) -> None:
        with _EXECUTION_LOCKS_GUARD:
            execution_lock = _EXECUTION_LOCKS.setdefault(
                str(self.store.db_path.resolve()) + ":" + execution_id, threading.Lock()
            )
        if not execution_lock.acquire(blocking=False):
            return
        record = self.store.get(execution_id)
        try:
            if record is None or record.state in TERMINAL_PLAN_STATES:
                return
            record.state = PlanExecutionState.RUNNING
            self.store.save(record)
            plan = _plan_from_dict(record.plan_snapshot)
            while True:
                record = self.store.get(execution_id)
                if record is None or record.state in TERMINAL_PLAN_STATES:
                    return
                task = self._next_task(record, plan)
                if task is None:
                    if all(
                        item["execution_state"] == PlanTaskState.ACCEPTED.value
                        for item in record.tasks.values()
                    ):
                        self._final_verify(record, plan)
                    return
                self._run_task(record, plan, task)
        except Exception as exc:
            record = self.store.get(execution_id)
            if record:
                record.state = PlanExecutionState.FAILED
                record.last_error = str(exc)
                record.terminal_reason = "PLAN_EXECUTION_ERROR"
                self.store.save(record)
        finally:
            execution_lock.release()

    def _next_task(self, record: PlanExecutionRecord, plan: TaskPlan):
        for shaped in plan.tasks:
            state = record.tasks[shaped.task_id]["execution_state"]
            if state == PlanTaskState.ACCEPTED.value:
                continue
            if state in {
                PlanTaskState.FAILED.value,
                PlanTaskState.BLOCKED.value,
                PlanTaskState.CANCELLED.value,
            }:
                return None
            deps = [record.tasks[d]["execution_state"] for d in shaped.dependencies]
            if any(value != PlanTaskState.ACCEPTED.value for value in deps):
                if any(
                    value
                    in {
                        PlanTaskState.FAILED.value,
                        PlanTaskState.BLOCKED.value,
                        PlanTaskState.CANCELLED.value,
                    }
                    for value in deps
                ):
                    record.tasks[shaped.task_id]["execution_state"] = (
                        PlanTaskState.BLOCKED.value
                    )
                    record.tasks[shaped.task_id]["last_error"] = (
                        "DEPENDENCY_NOT_ACCEPTED"
                    )
                    self.store.save(record)
                continue
            return shaped
        return None

    def _run_task(self, record: PlanExecutionRecord, plan: TaskPlan, shaped) -> None:
        task_state = record.tasks[shaped.task_id]
        task_state["execution_state"] = PlanTaskState.RUNNING.value
        task_state["resolved_base_head"] = record.current_accepted_head
        child_id = (
            f"{record.plan_execution_id}-{shaped.task_id}-{task_state['attempt']}"
        )
        task_state["child_run_id"] = child_id
        record.active_task_id = shaped.task_id
        record.active_run_id = child_id
        self.store.save(record)
        contract = TaskContract(
            task_id=shaped.task_id,
            objective=shaped.objective,
            base_head=record.current_accepted_head,
            workdir=record.integration_worktree,
            allowed_paths=list(shaped.allowed_paths),
            forbidden_paths=list(shaped.forbidden_paths),
            acceptance_criteria=list(shaped.acceptance_criteria),
            verification_commands=list(shaped.verification_commands),
            dependencies=list(shaped.dependencies),
            risk_class=shaped.risk_class,
            isolated_worktree=False,
            max_runtime=shaped.max_runtime,
            max_repair_rounds=shaped.max_repair_rounds,
        ).freeze()
        manager = DurableRunManager(self.store.db_path)
        existing = None
        try:
            existing = manager.run_status(child_id)
        except Exception:
            existing = None
        if existing is None:
            manager.run_start(
                contract,
                run_id=child_id,
                worker=self.worker_factory(contract),
                worktree=record.integration_worktree,
                repo=record.integration_worktree,
                base_head=record.current_accepted_head,
                auto_spawn=True,
            )
        child = manager.run_wait(child_id, timeout=shaped.max_runtime)
        task_state["child_run_state"] = child.state.value
        if child.state != RunState.COMPLETE:
            task_state["execution_state"] = PlanTaskState.FAILED.value
            task_state["last_error"] = child.last_error or child.state.value
            record.state = PlanExecutionState.BLOCKED
            record.active_task_id = None
            record.active_run_id = None
            self.store.save(record)
            return
        verification = (
            child.verification_result
            if isinstance(child.verification_result, dict)
            else {}
        )
        acceptance = (
            verification.get("acceptance", {}) if isinstance(verification, dict) else {}
        )
        accepted = (
            bool(verification.get("task_accepted", False))
            or (isinstance(acceptance, str) and acceptance == "ACCEPTED")
            or (isinstance(acceptance, dict) and acceptance.get("task_accepted", False))
        )
        if not accepted:
            task_state["execution_state"] = PlanTaskState.FAILED.value
            task_state["last_error"] = "CHILD_EXECUTION_REJECTED"
            record.state = PlanExecutionState.BLOCKED
            record.active_task_id = None
            record.active_run_id = None
            self.store.save(record)
            return
        checkpoint = _git(record.integration_worktree, "rev-parse", "HEAD")
        dirty = _git(record.integration_worktree, "status", "--porcelain")
        if dirty:
            _git(record.integration_worktree, "add", "--", *shaped.allowed_paths)
            subprocess.run(
                [
                    "git",
                    "-C",
                    record.integration_worktree,
                    "commit",
                    "-m",
                    f"agy-plan-checkpoint: {record.plan_execution_id} {shaped.task_id}",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            checkpoint = _git(record.integration_worktree, "rev-parse", "HEAD")
        task_state["execution_state"] = PlanTaskState.ACCEPTED.value
        task_state["accepted_checkpoint_sha"] = checkpoint
        record.current_accepted_head = checkpoint
        record.active_task_id = None
        record.active_run_id = None
        self.store.save(record)

    def _final_verify(self, record: PlanExecutionRecord, plan: TaskPlan) -> None:
        record.state = PlanExecutionState.FINAL_VERIFYING
        self.store.save(record)
        results = []
        for command in plan.final_verification:
            proc = subprocess.run(
                command,
                cwd=record.integration_worktree,
                shell=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
            results.append(
                {
                    "command": command,
                    "returncode": proc.returncode,
                    "stdout": proc.stdout[-2000:],
                    "stderr": proc.stderr[-2000:],
                }
            )
            if proc.returncode:
                record.final_verification_result = {"passed": False, "results": results}
                record.state = PlanExecutionState.BLOCKED
                record.last_error = "FINAL_VERIFICATION_ERROR"
                self.store.save(record)
                return
        record.final_verification_result = {"passed": True, "results": results}
        record.state = PlanExecutionState.COMPLETE
        record.terminal_reason = "PLAN_COMPLETE"
        self.store.save(record)
