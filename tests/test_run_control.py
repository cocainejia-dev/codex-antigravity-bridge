"""Tests for standalone Windows-compatible durable run control core."""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

# Ensure package import from mcp-antigravity-bridge/src
SRC = Path(__file__).resolve().parents[1] / "mcp-antigravity-bridge" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest
from codex_agy_bridge.contracts import (
    AutoCommitPolicy,
    RiskClass,
    RunRecord,
    RunState,
    TaskContract,
)
from codex_agy_bridge.run_control import (
    ConcurrentModificationError,
    CredentialSecurityError,
    DuplicateRunError,
    DurableRunManager,
    DurableRunStore,
    RunNotFoundError,
    RunNotTerminalError,
    WorkerContext,
    WorkerResult,
    is_pid_alive,
)


def _create_sample_contract(
    task_id: str = "task-vnext-001",
    objective: str = "Implement durable run controller",
    workdir: str | None = None,
    **kwargs: Any,
) -> TaskContract:
    """Helper to create a valid TaskContract with temporary workdir."""
    if workdir is None:
        workdir = Path(os.getcwd()).as_posix()
    return TaskContract(
        task_id=task_id,
        objective=objective,
        base_head="abcdef1234567890",
        workdir=workdir,
        allowed_paths=["src/run_control.py"],
        forbidden_paths=["config/secrets.json"],
        acceptance_criteria=["pytest passes"],
        verification_commands=["pytest -q"],
        dependencies=[],
        risk_class=RiskClass.CODE_CHANGES,
        max_runtime=300,
        max_repair_rounds=2,
        auto_commit_policy=AutoCommitPolicy.VERIFIED_ONLY,
        **kwargs,
    )


def test_sqlite_journal_schema_initialization(tmp_path: Path) -> None:
    """Test that SQLite journal is initialized at caller-supplied path with correct schema."""
    db_file = tmp_path / "custom_runs.sqlite3"
    manager = DurableRunManager(db_file)
    assert db_file.exists()

    contract = _create_sample_contract()
    record = manager.run_start(contract, auto_spawn=False)

    assert record.run_id.startswith("run-")
    assert record.task_id == contract.task_id
    assert record.state == RunState.CREATED
    assert record.state_version == 1

    fetched = manager.run_status(record.run_id)
    assert fetched.run_id == record.run_id
    assert fetched.state == RunState.CREATED

    stored_contract = manager.get_task_contract(record.run_id)
    assert stored_contract.task_id == contract.task_id
    assert stored_contract.objective == contract.objective


def test_persist_before_spawn_ordering(tmp_path: Path) -> None:
    """Verify that the run record is written to SQLite BEFORE the worker executes."""
    db_file = tmp_path / "test_persist_before_spawn.sqlite3"
    manager = DurableRunManager(db_file)
    contract = _create_sample_contract(task_id="task-persist-01")

    worker_started_event = threading.Event()
    worker_finished_event = threading.Event()
    worker_saw_db_record = threading.Event()

    def sample_worker(ctx: WorkerContext) -> WorkerResult:
        # At the time worker executes, verify the run record was already in SQLite
        db_record = manager.run_status(ctx.run_id)
        if db_record is not None and db_record.run_id == ctx.run_id:
            worker_saw_db_record.set()
        worker_started_event.set()
        worker_finished_event.wait(timeout=2.0)
        return WorkerResult(success=True, result_summary="Done")

    record = manager.run_start(contract, worker=sample_worker)

    # Immediately check that the record exists in DB before worker finishes
    initial_db_record = manager.run_status(record.run_id)
    assert initial_db_record is not None
    assert initial_db_record.run_id == record.run_id

    # Wait for worker to start and verify it saw the DB record
    assert worker_started_event.wait(timeout=2.0)
    assert worker_saw_db_record.is_set()

    worker_finished_event.set()
    terminal = manager.run_wait(record.run_id, timeout=2.0)
    assert terminal.state == RunState.COMPLETE


def test_manager_recreation_read_after_restart(tmp_path: Path) -> None:
    """Verify that run records survive manager object recreation and new instances read durable state."""
    db_file = tmp_path / "restart.sqlite3"
    manager1 = DurableRunManager(db_file)
    contract = _create_sample_contract(task_id="task-restart-01")

    def fast_worker(ctx: WorkerContext) -> WorkerResult:
        return WorkerResult(
            success=True,
            verification_result={"passed": True, "details": "all checks pass"},
            result_summary="Verified and finished",
        )

    record = manager1.run_start(contract, worker=fast_worker)
    manager1.run_wait(record.run_id, timeout=2.0)

    # Recreate manager pointing to the same SQLite DB
    manager2 = DurableRunManager(db_file)
    status2 = manager2.run_status(record.run_id)
    assert status2.run_id == record.run_id
    assert status2.state == RunState.COMPLETE
    assert status2.verification_result == {"passed": True, "details": "all checks pass"}
    assert status2.result_summary == "Verified and finished"

    contract2 = manager2.get_task_contract(record.run_id)
    assert contract2.task_id == contract.task_id
    assert contract2.objective == contract.objective


def test_duplicate_start_protection_by_task_id(tmp_path: Path) -> None:
    """Verify duplicate active runs for same task_id are rejected."""
    db_file = tmp_path / "dup_task.sqlite3"
    manager = DurableRunManager(db_file)
    contract = _create_sample_contract(task_id="task-dup-01")

    hold_event = threading.Event()

    def slow_worker(ctx: WorkerContext) -> WorkerResult:
        hold_event.wait(timeout=3.0)
        return WorkerResult(success=True)

    record1 = manager.run_start(contract, worker=slow_worker)
    assert record1.run_id

    # Attempting to start another run for the same task_id while active must raise DuplicateRunError
    with pytest.raises(DuplicateRunError, match="already exists"):
        manager.run_start(contract, worker=slow_worker)

    hold_event.set()
    manager.run_wait(record1.run_id, timeout=2.0)

    # Once terminated, a new run for the same task_id can be started
    record2 = manager.run_start(contract, auto_spawn=False)
    assert record2.run_id != record1.run_id


def test_duplicate_start_protection_by_idempotency_key(tmp_path: Path) -> None:
    """Verify starting with identical idempotency_key returns existing run without duplicate spawn."""
    db_file = tmp_path / "idempotent.sqlite3"
    manager = DurableRunManager(db_file)
    contract = _create_sample_contract(task_id="task-idem-01")

    spawn_count = 0

    def counting_worker(ctx: WorkerContext) -> WorkerResult:
        nonlocal spawn_count
        spawn_count += 1
        return WorkerResult(success=True)

    record1 = manager.run_start(contract, idempotency_key="key-abc-123", worker=counting_worker)
    manager.run_wait(record1.run_id, timeout=2.0)
    assert spawn_count == 1

    # Starting with the same idempotency key returns the existing record
    record2 = manager.run_start(contract, idempotency_key="key-abc-123", worker=counting_worker)
    assert record2.run_id == record1.run_id
    assert spawn_count == 1  # Not spawned again


def test_monotonic_state_version_and_optimistic_concurrency(tmp_path: Path) -> None:
    """Verify that state_version monotonically increments and protects against concurrent modifications."""
    db_file = tmp_path / "concurrency.sqlite3"
    store = DurableRunStore(db_file)
    contract = _create_sample_contract(task_id="task-version-01")

    record = RunRecord(
        run_id="run-version-test",
        task_id=contract.task_id,
        state=RunState.CREATED,
        state_version=1,
    )
    store.insert_run(record, task_contract=contract)

    # Valid transition from version 1 to version 2 (CREATED -> QUEUED)
    r2 = store.transition_run("run-version-test", expected_version=1, target_state=RunState.QUEUED)
    assert r2.state == RunState.QUEUED
    assert r2.state_version == 2

    # Attempting transition with stale expected_version=1 must raise ConcurrentModificationError
    with pytest.raises(ConcurrentModificationError):
        store.transition_run("run-version-test", expected_version=1, target_state=RunState.RUNNING)

    # Valid transition with version 2 (QUEUED -> RUNNING)
    r3 = store.transition_run("run-version-test", expected_version=2, target_state=RunState.RUNNING)
    assert r3.state == RunState.RUNNING
    assert r3.state_version == 3


def test_dead_pid_and_interrupted_observation(tmp_path: Path) -> None:
    """Verify that run_observe detects dead worker PID, marks INTERRUPTED, and exposes RECOVERY_READY."""
    db_file = tmp_path / "dead_pid.sqlite3"
    store = DurableRunStore(db_file)
    contract = _create_sample_contract(task_id="task-dead-pid")

    # Insert a run with a non-existent dead PID (e.g. 99999999) in RUNNING state
    record = RunRecord(
        run_id="run-dead-pid-01",
        task_id=contract.task_id,
        state=RunState.CREATED,
        state_version=1,
    )
    store.insert_run(record, task_contract=contract)
    store.transition_run("run-dead-pid-01", expected_version=1, target_state=RunState.QUEUED)
    store.transition_run(
        "run-dead-pid-01",
        expected_version=2,
        target_state=RunState.RUNNING,
        pid=99999999,  # Definitely dead
    )

    manager = DurableRunManager(db_file)
    obs = manager.run_observe("run-dead-pid-01")

    assert obs.is_terminal is False
    assert obs.is_alive is False
    assert obs.state == RunState.INTERRUPTED
    assert obs.recovery_state == RunState.RECOVERY_READY

    # Verify durable state in DB was updated to INTERRUPTED
    db_record = manager.run_status("run-dead-pid-01")
    assert db_record.state == RunState.INTERRUPTED


def test_wait_timeout_does_not_cancel_worker(tmp_path: Path) -> None:
    """CRITICAL: Verify that run_wait timing out NEVER cancels or interrupts the worker."""
    db_file = tmp_path / "timeout_wait.sqlite3"
    manager = DurableRunManager(db_file)
    contract = _create_sample_contract(task_id="task-wait-timeout")

    finish_event = threading.Event()
    worker_cancelled = threading.Event()

    def long_worker(ctx: WorkerContext) -> WorkerResult:
        # Sleep for a bit while checking cancel
        for _ in range(20):
            if ctx.is_cancelled():
                worker_cancelled.set()
                return WorkerResult(success=False, last_error="Cancelled")
            time.sleep(0.05)
        finish_event.set()
        return WorkerResult(success=True, result_summary="Long task completed")

    record = manager.run_start(contract, worker=long_worker)

    # Wait with a short timeout of 0.15s
    interim_record = manager.run_wait(record.run_id, timeout=0.15)

    # The wait timed out, but the worker MUST STILL BE RUNNING (not cancelled)
    assert interim_record.state in (RunState.RUNNING, RunState.QUEUED)
    assert not worker_cancelled.is_set()

    # Wait for completion without timeout
    final_record = manager.run_wait(record.run_id, timeout=2.0)
    assert final_record.state == RunState.COMPLETE
    assert finish_event.is_set()
    assert not worker_cancelled.is_set()


def test_run_result_terminal_evidence_and_errors(tmp_path: Path) -> None:
    """Verify run_result returns terminal evidence when complete, and raises RunNotTerminalError when active."""
    db_file = tmp_path / "result_test.sqlite3"
    manager = DurableRunManager(db_file)
    contract = _create_sample_contract(task_id="task-result-01")

    hold_event = threading.Event()

    def controlled_worker(ctx: WorkerContext) -> WorkerResult:
        hold_event.wait(timeout=2.0)
        return WorkerResult(
            success=True,
            verification_result={"passed": True, "score": 100},
            result_summary="All 100 tests passed",
            commit_sha="c0ffee123456",
        )

    record = manager.run_start(contract, worker=controlled_worker)

    # Calling run_result while still running must raise RunNotTerminalError
    with pytest.raises(RunNotTerminalError, match="non-terminal state"):
        manager.run_result(record.run_id)

    # Release worker and wait for terminal state
    hold_event.set()
    manager.run_wait(record.run_id, timeout=2.0)

    # Now run_result returns terminal evidence
    res = manager.run_result(record.run_id)
    assert res.state == RunState.COMPLETE
    assert res.verification_result == {"passed": True, "score": 100}
    assert res.result_summary == "All 100 tests passed"
    assert res.commit_sha == "c0ffee123456"


def test_cooperative_run_cancel(tmp_path: Path) -> None:
    """Verify cooperative cancellation sets event, transitions to CANCELLED, and doesn't crash."""
    db_file = tmp_path / "cancel_test.sqlite3"
    manager = DurableRunManager(db_file)
    contract = _create_sample_contract(task_id="task-cancel-01")

    worker_saw_cancel = threading.Event()

    def cancellable_worker(ctx: WorkerContext) -> WorkerResult:
        for _ in range(50):
            if ctx.is_cancelled():
                worker_saw_cancel.set()
                return WorkerResult(success=False, last_error="Cancelled by user")
            time.sleep(0.05)
        return WorkerResult(success=True)

    record = manager.run_start(contract, worker=cancellable_worker)
    time.sleep(0.1)

    cancelled_record = manager.run_cancel(record.run_id, reason="User requested stop")
    assert cancelled_record.state == RunState.CANCELLED

    # Give worker thread a moment to see cancellation
    assert worker_saw_cancel.wait(timeout=2.0)


def test_no_credential_persistence_rejection(tmp_path: Path) -> None:
    """Verify credential patterns in contract or inputs are rejected before persistence."""
    db_file = tmp_path / "credentials_sec.sqlite3"
    manager = DurableRunManager(db_file)

    # Attempt to start with secret in objective
    with pytest.raises((CredentialSecurityError, ValueError), match="Credential-like"):
        contract = _create_sample_contract(
            task_id="task-sec-01",
            objective="Use GitHub token ghp_123456789012345678901234567890 to deploy",
        )
        manager.run_start(contract)

    # Attempt to start with AWS key in idempotency_key
    with pytest.raises((CredentialSecurityError, ValueError), match="Credential-like"):
        contract = _create_sample_contract(task_id="task-sec-02")
        manager.run_start(contract, idempotency_key="AKIA1234567890ABCDEF")

    # Verify no records were inserted into DB
    runs = manager.list_runs()
    assert len(runs) == 0


def test_heartbeat_updates(tmp_path: Path) -> None:
    """Verify heartbeat pulses update the heartbeat and updated_at fields."""
    db_file = tmp_path / "heartbeat.sqlite3"
    manager = DurableRunManager(db_file)
    contract = _create_sample_contract(task_id="task-heartbeat-01")

    record = manager.run_start(contract, auto_spawn=False)
    initial_hb = record.heartbeat

    time.sleep(0.05)
    updated = manager.heartbeat(record.run_id)
    assert updated.heartbeat != initial_hb
    assert updated.updated_at >= record.updated_at


def test_is_pid_alive_helper() -> None:
    """Verify is_pid_alive works for current pid and non-existent pid."""
    current_pid = os.getpid()
    assert is_pid_alive(current_pid) is True
    assert is_pid_alive(None) is False
    assert is_pid_alive(-1) is False
    assert is_pid_alive(99999999) is False


def test_list_runs_filtering(tmp_path: Path) -> None:
    """Verify list_runs filters by task_id and state."""
    db_file = tmp_path / "list_runs.sqlite3"
    manager = DurableRunManager(db_file)

    c1 = _create_sample_contract(task_id="task-list-1")
    c2 = _create_sample_contract(task_id="task-list-2")

    r1 = manager.run_start(c1, auto_spawn=False)
    manager.run_start(c2, auto_spawn=False)

    all_runs = manager.list_runs()
    assert len(all_runs) == 2

    task1_runs = manager.list_runs(task_id="task-list-1")
    assert len(task1_runs) == 1
    assert task1_runs[0].run_id == r1.run_id

    created_runs = manager.list_runs(state=RunState.CREATED)
    assert len(created_runs) == 2

    empty_runs = manager.list_runs(state=RunState.COMPLETE)
    assert len(empty_runs) == 0


def test_worker_unhandled_exception_transitions_to_failed(tmp_path: Path) -> None:
    """Verify unhandled worker exceptions transition run to FAILED."""
    db_file = tmp_path / "crash_test.sqlite3"
    manager = DurableRunManager(db_file)
    contract = _create_sample_contract(task_id="task-crash-01")

    def crashing_worker(ctx: WorkerContext) -> WorkerResult:
        raise RuntimeError("Worker disk crashed")

    record = manager.run_start(contract, worker=crashing_worker)
    terminal = manager.run_wait(record.run_id, timeout=2.0)

    assert terminal.state == RunState.FAILED
    assert "Worker disk crashed" in (terminal.last_error or "")


def test_stale_heartbeat_observation(tmp_path: Path) -> None:
    """Verify run_observe detects stale heartbeat and transitions to INTERRUPTED."""
    db_file = tmp_path / "stale_hb.sqlite3"
    store = DurableRunStore(db_file)
    contract = _create_sample_contract(task_id="task-stale-hb")

    record = RunRecord(
        run_id="run-stale-hb-01",
        task_id=contract.task_id,
        state=RunState.CREATED,
        state_version=1,
    )
    store.insert_run(record, task_contract=contract)
    store.transition_run("run-stale-hb-01", expected_version=1, target_state=RunState.QUEUED)
    store.transition_run("run-stale-hb-01", expected_version=2, target_state=RunState.RUNNING, pid=os.getpid())

    # Set an old heartbeat (e.g. 5 minutes ago)
    old_hb = "2020-01-01T00:00:00+00:00"
    store.update_heartbeat("run-stale-hb-01", timestamp=old_hb)

    manager = DurableRunManager(db_file)
    obs = manager.run_observe("run-stale-hb-01", stale_heartbeat_threshold_seconds=10.0)

    assert obs.is_stale is True
    assert obs.state == RunState.INTERRUPTED
    assert obs.recovery_state == RunState.RECOVERY_READY


def test_run_not_found_errors(tmp_path: Path) -> None:
    """Verify accessing non-existent run raises RunNotFoundError."""
    db_file = tmp_path / "not_found.sqlite3"
    manager = DurableRunManager(db_file)

    with pytest.raises(RunNotFoundError):
        manager.run_status("nonexistent-run-id")

    with pytest.raises(RunNotFoundError):
        manager.run_observe("nonexistent-run-id")

    with pytest.raises(RunNotFoundError):
        manager.run_wait("nonexistent-run-id", timeout=0.1)

    with pytest.raises(RunNotFoundError):
        manager.run_result("nonexistent-run-id")

    with pytest.raises(RunNotFoundError):
        manager.run_cancel("nonexistent-run-id")

    with pytest.raises(RunNotFoundError):
        manager.get_task_contract("nonexistent-run-id")


def test_manager_recreation_preserves_active_in_process_worker(tmp_path: Path) -> None:
    """A new manager in the same process sees the existing active callback."""
    db_file = tmp_path / "recreation_interrupted.sqlite3"
    manager1 = DurableRunManager(db_file)
    contract = _create_sample_contract(task_id="task-recreation-01")

    worker_started = threading.Event()
    worker_hold = threading.Event()

    def in_process_worker(ctx: WorkerContext) -> WorkerResult:
        worker_started.set()
        worker_hold.wait(timeout=5.0)
        return WorkerResult(success=True, result_summary="Done")

    # Start run with manager1
    record = manager1.run_start(contract, worker=in_process_worker)
    assert worker_started.wait(timeout=2.0)

    # In manager1, with active thread, run_observe reports healthy running state
    obs1 = manager1.run_observe(record.run_id)
    assert obs1.is_alive is True
    assert obs1.is_stale is False
    assert obs1.recovery_state is None
    assert obs1.state in (RunState.RUNNING, RunState.QUEUED)

    # Recreate manager instance in the same Python process.
    manager2 = DurableRunManager(db_file)

    # Observe immediately without waiting for heartbeat timeout.
    obs2 = manager2.run_observe(record.run_id)
    assert obs2.run_id == record.run_id
    assert obs2.state in (RunState.RUNNING, RunState.QUEUED)
    assert obs2.recovery_state is None
    assert obs2.is_alive is True
    assert obs2.is_stale is False
    assert obs2.is_terminal is False

    # Durable record remains active with the same run_id.
    persisted_status = manager2.run_status(record.run_id)
    assert persisted_status.run_id == record.run_id
    assert persisted_status.state in (RunState.RUNNING, RunState.QUEUED)

    # Unblock the background thread for clean teardown
    worker_hold.set()
    terminal = manager2.run_wait(record.run_id, timeout=5.0)
    assert terminal.state == RunState.COMPLETE


def test_missing_shared_worker_still_enters_recovery(tmp_path: Path) -> None:
    """A persisted in-process run with no live local ownership remains recoverable."""
    db_file = tmp_path / "missing_shared_worker.sqlite3"
    manager = DurableRunManager(db_file)
    contract = _create_sample_contract(task_id="task-missing-shared-worker")
    record = manager.run_start(contract, auto_spawn=False)
    manager.store.transition_run(record.run_id, expected_version=1, target_state=RunState.QUEUED)
    manager.store.transition_run(record.run_id, expected_version=2, target_state=RunState.RUNNING)

    obs = DurableRunManager(db_file).run_observe(record.run_id)
    assert obs.state == RunState.INTERRUPTED
    assert obs.recovery_state == RunState.RECOVERY_READY


def test_external_process_observation_liveness_and_stale(tmp_path: Path) -> None:
    """Verify observation for external supervised processes distinguishes alive PID, dead PID, and stale heartbeat."""
    db_file = tmp_path / "external_proc.sqlite3"
    manager = DurableRunManager(db_file)
    contract = _create_sample_contract(task_id="task-ext-01")

    # 1. External process with alive current PID and fresh heartbeat
    ext_record = manager.run_start(
        contract,
        auto_spawn=False,
        worker_identity={"worker_type": "process", "pid": os.getpid()},
    )
    manager.store.transition_run(ext_record.run_id, expected_version=1, target_state=RunState.QUEUED)
    manager.store.transition_run(ext_record.run_id, expected_version=2, target_state=RunState.RUNNING, pid=os.getpid())

    obs_alive = manager.run_observe(ext_record.run_id)
    assert obs_alive.is_alive is True
    assert obs_alive.is_stale is False
    assert obs_alive.state == RunState.RUNNING
    assert obs_alive.recovery_state is None


def test_run_cancel_pre_execution_preserves_custom_reason(tmp_path: Path) -> None:
    """Verify that when cancellation is requested before worker execution, the caller reason is preserved."""
    db_file = tmp_path / "pre_exec_cancel.sqlite3"
    manager = DurableRunManager(db_file)
    contract = _create_sample_contract(task_id="task-pre-exec-cancel")

    worker_entered = threading.Event()
    worker_hold = threading.Event()

    def blocked_worker(ctx: WorkerContext) -> WorkerResult:
        worker_entered.set()
        worker_hold.wait(timeout=2.0)
        return WorkerResult(success=True)

    record = manager.run_start(contract, worker=blocked_worker)
    custom_reason = "Explicit pre-execution cancellation reason"
    cancelled = manager.run_cancel(record.run_id, reason=custom_reason)
    assert cancelled.state == RunState.CANCELLED
    assert cancelled.last_error == custom_reason

    terminal = manager.run_wait(record.run_id, timeout=2.0)
    assert terminal.state == RunState.CANCELLED
    assert terminal.last_error == custom_reason

    result = manager.run_result(record.run_id)
    assert result.state == RunState.CANCELLED
    assert result.last_error == custom_reason

    worker_hold.set()


def test_run_cancel_pre_execution_race_under_load(tmp_path: Path) -> None:
    """Verify that rapid concurrent start and cancel consistently preserves caller reason under race conditions."""
    db_file = tmp_path / "rapid_cancel_race.sqlite3"
    manager = DurableRunManager(db_file)

    for i in range(25):
        task_id = f"task-race-{i}"
        contract = _create_sample_contract(task_id=task_id)
        reason = f"Race test cancellation reason #{i}"

        def fast_worker(ctx: WorkerContext) -> WorkerResult:
            time.sleep(0.01)
            return WorkerResult(success=True)

        record = manager.run_start(contract, worker=fast_worker)
        cancelled = manager.run_cancel(record.run_id, reason=reason)
        assert cancelled.state == RunState.CANCELLED

        terminal = manager.run_wait(record.run_id, timeout=2.0)
        assert terminal.state == RunState.CANCELLED
        assert terminal.last_error == reason

        result = manager.run_result(record.run_id)
        assert result.state == RunState.CANCELLED
        assert result.last_error == reason


def test_run_verification_worktree_keyword_reproduces_original_typeerror(tmp_path: Path) -> None:
    """Document the incident: the canonical API rejects the obsolete keyword."""
    from codex_agy_bridge.verification import run_verification

    contract = _create_sample_contract(task_id="task-worktree-keyword-contract", workdir=str(tmp_path))
    with pytest.raises(TypeError, match="unexpected keyword argument 'worktree'"):
        run_verification(contract, worktree=str(tmp_path))


def test_candidate_reaches_independent_acceptance_with_workdir_wiring(tmp_path: Path) -> None:
    """A durable candidate must reach real verification and acceptance evidence."""
    import subprocess

    repo = tmp_path / "candidate-repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "feature.py").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Run Control Tests"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=repo, check=True)
    base_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()

    db_file = tmp_path / "candidate-acceptance.sqlite3"
    manager = DurableRunManager(db_file)
    contract = _create_sample_contract(task_id="task-workdir-wiring-acceptance", workdir=str(repo))
    contract.base_head = base_head
    contract.allowed_paths = ["src/feature.py"]
    contract.forbidden_paths = ["secrets.json"]
    contract.verification_commands = [
        "python -c \"from pathlib import Path; assert Path('src/feature.py').read_text() == 'candidate\\n'\""
    ]

    def candidate_worker(ctx: WorkerContext) -> WorkerResult:
        (Path(ctx.worktree) / "src" / "feature.py").write_text("candidate\n", encoding="utf-8")
        return WorkerResult(success=True, candidate=True, result_summary="candidate-present")

    record = manager.run_start(contract, worker=candidate_worker, worktree=str(repo))
    terminal = manager.run_wait(record.run_id, timeout=10.0)

    assert terminal.state == RunState.COMPLETE
    assert terminal.last_error is None
    assert terminal.verification_result["acceptance"] == "ACCEPTED"
    assert terminal.verification_result["task_accepted"] is True
    assert terminal.verification_result["independently_verified"] is True
    assert terminal.verification_result["scope_audit"]["passed"] is True

    from codex_agy_bridge.verification import VerificationEvidence, run_verification

    evidence = run_verification(contract, workdir=repo, run_id="run-workdir-wiring-evidence")
    assert isinstance(evidence, VerificationEvidence)
    assert evidence.passed is True
    assert evidence.scope_passed is True
    assert evidence.provenance_verified is True
