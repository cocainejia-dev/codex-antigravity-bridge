"""Comprehensive unit and integration tests for Phase 6 VNext persistent Task DAG scheduler."""

from __future__ import annotations

import concurrent.futures
import json
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any

# Ensure package import from mcp-antigravity-bridge/src
SRC = Path(__file__).resolve().parents[1] / "mcp-antigravity-bridge" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

from codex_agy_bridge.contracts import (
    AutoCommitPolicy,
    RiskClass,
    RunState,
    TaskContract,
)
from codex_agy_bridge.scheduler import (
    FIXED_MAX_PARALLELISM,
    CyclicDependencyError,
    CredentialSecurityError,
    DAGSchedulerError,
    DAGTaskRecord,
    DAGTaskSpec,
    DAGTaskState,
    DependencyNotFoundError,
    DuplicateTaskError,
    InvalidDAGStateTransitionError,
    MaxParallelismViolationError,
    TaskDAGScheduler,
    TaskExecutionResult,
    TaskNotFoundError,
)


def _create_sample_contract(task_id: str, objective: str = "Test objective") -> TaskContract:
    """Helper to create a valid TaskContract."""
    return TaskContract(
        task_id=task_id,
        objective=objective,
        base_head="abcdef1234567890",
        workdir=Path(os.getcwd()).as_posix(),
        allowed_paths=["src/test.py"],
        forbidden_paths=[],
        acceptance_criteria=["tests pass"],
        verification_commands=["pytest -q"],
        dependencies=[],
        risk_class=RiskClass.CODE_CHANGES,
        max_runtime=300,
        max_repair_rounds=2,
        auto_commit_policy=AutoCommitPolicy.VERIFIED_ONLY,
    )


def test_max_parallelism_invariant(tmp_path: Path) -> None:
    """Scheduler must enforce fixed max_parallelism = 1."""
    db_file = tmp_path / "scheduler_max_par.sqlite3"
    sched = TaskDAGScheduler(db_file)
    assert sched.max_parallelism == 1

    with pytest.raises(MaxParallelismViolationError):
        TaskDAGScheduler(db_file, max_parallelism=2)

    with pytest.raises(MaxParallelismViolationError):
        TaskDAGScheduler(db_file, max_parallelism=0)


def test_linear_chain_execution(tmp_path: Path) -> None:
    """Linear dependency chain: task-A -> task-B -> task-C."""
    db_file = tmp_path / "linear_dag.sqlite3"
    execution_order: list[str] = []

    def mock_runner(task: DAGTaskRecord) -> TaskExecutionResult:
        execution_order.append(task.task_id)
        return TaskExecutionResult(
            success=True,
            output=f"Output from {task.task_id}",
            result_summary=f"Finished {task.task_id}",
        )

    sched = TaskDAGScheduler(db_file, runner=mock_runner)

    sched.add_task("task-A")
    sched.add_task("task-B", dependencies=["task-A"])
    sched.add_task("task-C", dependencies=["task-B"])
    sched.validate_dag()

    # Initial states
    assert sched.get_task("task-A").state == DAGTaskState.READY
    assert sched.get_task("task-B").state == DAGTaskState.BLOCKED_BY_DEPENDENCY
    assert sched.get_task("task-C").state == DAGTaskState.BLOCKED_BY_DEPENDENCY

    # Step 1: executes task-A
    t1 = sched.step()
    assert t1 is not None
    assert t1.task_id == "task-A"
    assert t1.state == DAGTaskState.COMPLETE
    assert sched.get_task("task-B").state == DAGTaskState.READY
    assert sched.get_task("task-C").state == DAGTaskState.BLOCKED_BY_DEPENDENCY

    # Step 2: executes task-B
    t2 = sched.step()
    assert t2 is not None
    assert t2.task_id == "task-B"
    assert t2.state == DAGTaskState.COMPLETE
    assert sched.get_task("task-C").state == DAGTaskState.READY

    # Step 3: executes task-C
    t3 = sched.step()
    assert t3 is not None
    assert t3.task_id == "task-C"
    assert t3.state == DAGTaskState.COMPLETE

    # Step 4: nothing left to run
    assert sched.step() is None
    assert execution_order == ["task-A", "task-B", "task-C"]

    snap = sched.snapshot()
    assert snap["is_complete"] is True
    assert snap["total_tasks"] == 3
    assert len(snap["completed_tasks"]) == 3


def test_branch_and_merge_dag(tmp_path: Path) -> None:
    """Diamond DAG: task-root -> [task-left, task-right] -> task-merge."""
    db_file = tmp_path / "diamond_dag.sqlite3"
    execution_order: list[str] = []

    def mock_runner(task: DAGTaskRecord) -> TaskExecutionResult:
        execution_order.append(task.task_id)
        return TaskExecutionResult(success=True)

    sched = TaskDAGScheduler(db_file, runner=mock_runner)

    sched.add_tasks([
        DAGTaskSpec(task_id="task-root", dependencies=[]),
        DAGTaskSpec(task_id="task-left", dependencies=["task-root"], priority=10),
        DAGTaskSpec(task_id="task-right", dependencies=["task-root"], priority=5),
        DAGTaskSpec(task_id="task-merge", dependencies=["task-left", "task-right"]),
    ])

    results = sched.run_all()
    assert len(results) == 4

    # Priority ensures task-left runs before task-right
    assert execution_order == ["task-root", "task-left", "task-right", "task-merge"]

    merge_task = sched.get_task("task-merge")
    assert merge_task.state == DAGTaskState.COMPLETE
    assert merge_task.completed_at is not None


def test_failed_dependency_blocking(tmp_path: Path) -> None:
    """When an upstream task fails, downstream tasks must remain BLOCKED_BY_DEPENDENCY."""
    db_file = tmp_path / "failure_dag.sqlite3"
    executed: list[str] = []

    def mock_runner(task: DAGTaskRecord) -> TaskExecutionResult:
        executed.append(task.task_id)
        if task.task_id == "task-failing":
            return TaskExecutionResult(
                success=False,
                last_error="Simulated build failure",
            )
        return TaskExecutionResult(success=True)

    sched = TaskDAGScheduler(db_file, runner=mock_runner)

    sched.add_task("task-failing")
    sched.add_task("task-downstream-1", dependencies=["task-failing"])
    sched.add_task("task-downstream-2", dependencies=["task-downstream-1"])

    results = sched.run_all()
    assert len(results) == 1
    assert executed == ["task-failing"]

    fail_task = sched.get_task("task-failing")
    assert fail_task.state == DAGTaskState.FAILED
    assert fail_task.last_error == "Simulated build failure"

    d1 = sched.get_task("task-downstream-1")
    d2 = sched.get_task("task-downstream-2")
    assert d1.state == DAGTaskState.BLOCKED_BY_DEPENDENCY
    assert d2.state == DAGTaskState.BLOCKED_BY_DEPENDENCY

    snap = sched.snapshot()
    assert snap["is_complete"] is False
    assert snap["has_failures"] is True
    assert "task-failing" in snap["failed_tasks"]
    assert "task-downstream-1" in snap["blocked_tasks"]


def test_retry_failed_task(tmp_path: Path) -> None:
    """Retrying a failed task allows downstream tasks to proceed once successful."""
    db_file = tmp_path / "retry_dag.sqlite3"
    fail_count = 0

    def mock_runner(task: DAGTaskRecord) -> TaskExecutionResult:
        nonlocal fail_count
        if task.task_id == "task-flaky":
            fail_count += 1
            if fail_count == 1:
                return TaskExecutionResult(success=False, last_error="Transient error")
        return TaskExecutionResult(success=True)

    sched = TaskDAGScheduler(db_file, runner=mock_runner)
    sched.add_task("task-flaky")
    sched.add_task("task-subsequent", dependencies=["task-flaky"])

    # Step 1: task-flaky fails
    res1 = sched.step()
    assert res1 is not None and res1.state == DAGTaskState.FAILED
    assert sched.step() is None  # downstream is blocked

    # Retry task-flaky
    retried = sched.retry_task("task-flaky")
    assert retried.state == DAGTaskState.READY

    # Step 2: task-flaky succeeds on second attempt
    res2 = sched.step()
    assert res2 is not None and res2.state == DAGTaskState.COMPLETE
    assert res2.attempt == 2

    # Step 3: downstream now runs
    res3 = sched.step()
    assert res3 is not None and res3.task_id == "task-subsequent"
    assert res3.state == DAGTaskState.COMPLETE


def test_scheduler_recreation_recovery(tmp_path: Path) -> None:
    """State is preserved across scheduler instances pointing to the same SQLite DB."""
    db_file = tmp_path / "recovery_dag.sqlite3"
    ran: list[str] = []

    def mock_runner(task: DAGTaskRecord) -> TaskExecutionResult:
        ran.append(task.task_id)
        return TaskExecutionResult(success=True)

    # Instance 1: Create DAG and run 1 step
    sched1 = TaskDAGScheduler(db_file, runner=mock_runner)
    sched1.add_task("task-1")
    sched1.add_task("task-2", dependencies=["task-1"])
    sched1.add_task("task-3", dependencies=["task-2"])

    t1 = sched1.step()
    assert t1 is not None and t1.task_id == "task-1"
    assert t1.state == DAGTaskState.COMPLETE

    # Instance 2: Recreate scheduler pointing to same db_file
    sched2 = TaskDAGScheduler(db_file, runner=mock_runner)
    assert sched2.get_task("task-1").state == DAGTaskState.COMPLETE
    assert sched2.get_task("task-2").state == DAGTaskState.READY
    assert sched2.get_task("task-3").state == DAGTaskState.BLOCKED_BY_DEPENDENCY

    # Continue execution from instance 2
    rem = sched2.run_all()
    assert len(rem) == 2
    assert [r.task_id for r in rem] == ["task-2", "task-3"]
    assert ran == ["task-1", "task-2", "task-3"]


def test_interrupted_running_task_recovery(tmp_path: Path) -> None:
    """An orphaned RUNNING task left in DB is recovered to READY upon restart."""
    db_file = tmp_path / "interrupted_dag.sqlite3"

    sched = TaskDAGScheduler(db_file)
    sched.add_task("task-interrupted")
    # Manually transition task to RUNNING to simulate mid-execution crash
    task = sched.get_task("task-interrupted")
    task.transition_to(DAGTaskState.RUNNING)
    sched.store.update_task(task)

    # Recreate scheduler, which triggers recover()
    sched_recovered = TaskDAGScheduler(db_file)
    recovered_task = sched_recovered.get_task("task-interrupted")
    assert recovered_task.state == DAGTaskState.READY
    assert "Recovered from interrupted" in (recovered_task.last_error or "")


def test_duplicate_task_and_dispatch_protection(tmp_path: Path) -> None:
    """Adding existing task_id must raise DuplicateTaskError."""
    db_file = tmp_path / "duplicate_dag.sqlite3"
    sched = TaskDAGScheduler(db_file)

    sched.add_task("task-unique")
    with pytest.raises(DuplicateTaskError):
        sched.add_task("task-unique")

    with pytest.raises(DuplicateTaskError):
        sched.add_tasks([DAGTaskSpec(task_id="task-unique")])


def test_strict_single_worker_concurrency(tmp_path: Path) -> None:
    """Multiple threads calling step/run_all concurrently must strictly respect max_parallelism=1."""
    db_file = tmp_path / "concurrency_dag.sqlite3"
    active_count = 0
    max_observed_active = 0
    lock = threading.Lock()
    completed_tasks: list[str] = []

    def mock_concurrent_runner(task: DAGTaskRecord) -> TaskExecutionResult:
        nonlocal active_count, max_observed_active
        with lock:
            active_count += 1
            if active_count > max_observed_active:
                max_observed_active = active_count
        # Sleep briefly to give potential concurrency violations a chance to occur
        time.sleep(0.015)
        with lock:
            active_count -= 1
            completed_tasks.append(task.task_id)
        return TaskExecutionResult(success=True)

    sched = TaskDAGScheduler(db_file, runner=mock_concurrent_runner)

    # Construct wide DAG: 1 root, 8 parallel branches, 1 merge
    branch_ids = [f"branch-{i}" for i in range(8)]
    sched.add_task("root")
    for b in branch_ids:
        sched.add_task(b, dependencies=["root"])
    sched.add_task("merge", dependencies=branch_ids)

    # Spawn 8 threads competing to run steps
    def worker_thread() -> None:
        while True:
            res = sched.step()
            if res is None:
                # Check if all completed
                snap = sched.snapshot()
                if snap["is_complete"] or snap["has_failures"]:
                    break
                time.sleep(0.005)

    threads = [threading.Thread(target=worker_thread) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    assert max_observed_active == 1, f"Expected max parallelism 1, but observed {max_observed_active}"
    assert len(completed_tasks) == 10
    assert sched.snapshot()["is_complete"] is True


def test_decision_required_suspension_preservation(tmp_path: Path) -> None:
    """DECISION_REQUIRED suspension state, checkpoint, and reason are persisted and resolvable."""
    db_file = tmp_path / "decision_dag.sqlite3"

    def decision_runner(task: DAGTaskRecord) -> TaskExecutionResult:
        if task.task_id == "task-needs-input" and task.checkpoint is None:
            return TaskExecutionResult(
                success=False,
                target_state=DAGTaskState.DECISION_REQUIRED,
                suspended_reason="User confirmation required for migration",
                checkpoint={"step": 1, "action": "pending_approval"},
            )
        return TaskExecutionResult(
            success=True,
            result_summary="Completed after approval",
            checkpoint={"step": 2, "action": "approved"},
        )

    sched = TaskDAGScheduler(db_file, runner=decision_runner)
    sched.add_task("task-needs-input")
    sched.add_task("task-after-decision", dependencies=["task-needs-input"])

    # Step 1: suspends on decision
    rec = sched.step()
    assert rec is not None
    assert rec.state == DAGTaskState.DECISION_REQUIRED
    assert rec.suspended_reason == "User confirmation required for migration"
    assert rec.checkpoint == {"step": 1, "action": "pending_approval"}
    assert sched.get_task("task-after-decision").state == DAGTaskState.BLOCKED_BY_DEPENDENCY

    # Recreate scheduler to verify persistence
    sched_reloaded = TaskDAGScheduler(db_file, runner=decision_runner)
    reloaded_task = sched_reloaded.get_task("task-needs-input")
    assert reloaded_task.state == DAGTaskState.DECISION_REQUIRED
    assert reloaded_task.checkpoint == {"step": 1, "action": "pending_approval"}

    # Resolve decision
    sched_reloaded.resolve_decision(
        "task-needs-input",
        checkpoint={"step": 1, "approved": True},
        metadata={"decision_by": "admin"},
    )
    assert sched_reloaded.get_task("task-needs-input").state == DAGTaskState.READY

    # Continue execution
    executed = sched_reloaded.run_all()
    assert len(executed) == 2
    assert sched_reloaded.get_task("task-needs-input").state == DAGTaskState.COMPLETE
    assert sched_reloaded.get_task("task-after-decision").state == DAGTaskState.COMPLETE


def test_account_switch_required_suspension(tmp_path: Path) -> None:
    """ACCOUNT_SWITCH_REQUIRED suspension state is persisted and resolvable."""
    db_file = tmp_path / "switch_dag.sqlite3"

    def switch_runner(task: DAGTaskRecord) -> TaskExecutionResult:
        if task.task_id == "task-cross-account" and not task.metadata.get("switched"):
            return TaskExecutionResult(
                success=False,
                target_state=DAGTaskState.ACCOUNT_SWITCH_REQUIRED,
                suspended_reason="Rate limit exceeded on primary account; switch required",
                checkpoint={"last_page": 42},
            )
        return TaskExecutionResult(success=True, result_summary="Completed on secondary account")

    sched = TaskDAGScheduler(db_file, runner=switch_runner)
    sched.add_task("task-cross-account")
    sched.add_task("task-downstream", dependencies=["task-cross-account"])

    # Step 1: suspends for account switch
    rec = sched.step()
    assert rec is not None
    assert rec.state == DAGTaskState.ACCOUNT_SWITCH_REQUIRED
    assert "Rate limit" in (rec.suspended_reason or "")

    # Resolve account switch
    sched.resolve_account_switch("task-cross-account", metadata={"switched": True})
    assert sched.get_task("task-cross-account").state == DAGTaskState.READY

    # Complete execution
    sched.run_all()
    assert sched.get_task("task-cross-account").state == DAGTaskState.COMPLETE
    assert sched.get_task("task-downstream").state == DAGTaskState.COMPLETE


def test_credential_rejection_security(tmp_path: Path) -> None:
    """Any secret or credential pattern in task specs or results must be rejected."""
    db_file = tmp_path / "security_dag.sqlite3"
    sched = TaskDAGScheduler(db_file)

    # 1. Reject credential in task_id
    with pytest.raises(CredentialSecurityError):
        sched.add_task("task-ghp_123456789012345678901234567890")

    # 2. Reject credential in metadata
    with pytest.raises(CredentialSecurityError):
        sched.add_task("task-meta-sec", metadata={"api_key": "Bearer my_secret_token_123456"})

    # 3. Reject credential in contract
    with pytest.raises((CredentialSecurityError, ValueError)):
        sched.add_task("task-contract-sec", objective="Use sk-1234567890123456789012 to connect")

    # 4. Reject credential returned in runner result
    def leaking_runner(task: DAGTaskRecord) -> TaskExecutionResult:
        return TaskExecutionResult(
            success=True,
            output="Leaked token: ghp_123456789012345678901234567890",
        )

    sched.add_task("task-clean")
    with pytest.raises(CredentialSecurityError):
        sched.step(runner=leaking_runner)


def test_cyclic_and_missing_dependency_validation(tmp_path: Path) -> None:
    """Graph validation must reject missing dependencies and cyclic references."""
    db_file = tmp_path / "cyclic_dag.sqlite3"
    sched = TaskDAGScheduler(db_file)

    # Self dependency rejected at spec level
    with pytest.raises(ValueError):
        sched.add_task("task-self", dependencies=["task-self"])

    # Missing dependency
    sched.add_task("task-orphan", dependencies=["task-nonexistent"])
    with pytest.raises(DependencyNotFoundError):
        sched.validate_dag()

    # Cyclic dependency
    db_file2 = tmp_path / "cyclic_dag2.sqlite3"
    sched2 = TaskDAGScheduler(db_file2)
    sched2.add_task("task-A", dependencies=["task-B"])
    sched2.add_task("task-B", dependencies=["task-C"])
    sched2.add_task("task-C", dependencies=["task-A"])
    with pytest.raises(CyclicDependencyError):
        sched2.validate_dag()


def test_json_safe_snapshot(tmp_path: Path) -> None:
    """Snapshot must be complete, accurately counted, and serializable to JSON."""
    db_file = tmp_path / "snapshot_dag.sqlite3"
    contract = _create_sample_contract("task-contract-1")

    sched = TaskDAGScheduler(db_file, runner=lambda t: TaskExecutionResult(success=True))
    sched.add_task(contract)
    sched.add_task("task-2", dependencies=["task-contract-1"])

    sched.step()

    snap = sched.snapshot()
    serialized = json.dumps(snap)
    assert serialized is not None

    parsed = json.loads(serialized)
    assert parsed["total_tasks"] == 2
    assert parsed["completed_tasks"] == ["task-contract-1"]
    assert parsed["ready_tasks"] == ["task-2"]
    assert parsed["tasks"]["task-contract-1"]["contract"]["task_id"] == "task-contract-1"
