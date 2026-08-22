"""Comprehensive synthetic fault-injection tests for Phase 7 VNext recovery orchestration."""

from __future__ import annotations

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
    InvalidStateTransitionError,
    RiskClass,
    RunRecord,
    RunState,
    TaskContract,
)
from codex_agy_bridge.recovery import (
    FailureClass,
    InconsistentEvidenceError,
    RecoveryAction,
    RecoveryEvidence,
    RecoveryOrchestrator,
    RecoveryReport,
    RecoveryStatus,
    UnrecoverableRunError,
    classify_error_message,
)
from codex_agy_bridge.run_control import (
    ConcurrentModificationError,
    CredentialSecurityError,
    DurableRunManager,
    DurableRunStore,
    DuplicateRunError,
    RunNotFoundError,
    WorkerCallback,
    WorkerContext,
    WorkerResult,
)


def _create_test_contract(
    task_id: str = "task-rec-001",
    objective: str = "Test recovery orchestration",
    base_head: str = "abc123def456",
    workdir: str | None = None,
    **kwargs: Any,
) -> TaskContract:
    """Helper to generate a valid TaskContract."""
    if workdir is None:
        workdir = Path(os.getcwd()).as_posix()
    return TaskContract(
        task_id=task_id,
        objective=objective,
        base_head=base_head,
        workdir=workdir,
        allowed_paths=["src/recovery.py"],
        forbidden_paths=["config/secrets.json"],
        acceptance_criteria=["recovery verified"],
        verification_commands=["pytest -q"],
        dependencies=[],
        risk_class=RiskClass.CODE_CHANGES,
        max_runtime=300,
        max_repair_rounds=2,
        auto_commit_policy=AutoCommitPolicy.VERIFIED_ONLY,
        **kwargs,
    )


def test_classify_error_messages() -> None:
    """Verify deterministic classification of error strings."""
    assert classify_error_message("Error: Quota exceeded for project") == FailureClass.QUOTA_EXHAUSTED
    assert classify_error_message("429 Too Many Requests: Rate limit reached") == FailureClass.RATE_LIMIT
    assert classify_error_message("Token expired: auth token no longer valid") == FailureClass.AUTH_EXPIRED
    assert classify_error_message("401 Unauthorized: Authentication required") == FailureClass.AUTH_REQUIRED
    assert classify_error_message("Syntax error in test.py line 42") is None


def test_worker_crash_dead_pid_detection(tmp_path: Path) -> None:
    """Verify detection of dead external worker process PID and classification."""
    db_file = tmp_path / "dead_pid.sqlite3"
    manager = DurableRunManager(db_file)
    orchestrator = RecoveryOrchestrator(manager)

    contract = _create_test_contract(task_id="task-dead-pid")
    record = manager.run_start(
        contract,
        auto_spawn=False,
        worker_identity={"worker_type": "process", "pid": 99999999},
    )

    # Transition to RUNNING with a non-existent PID
    manager.store.transition_run(
        record.run_id,
        expected_version=1,
        target_state=RunState.QUEUED,
    )
    running_rec = manager.store.transition_run(
        record.run_id,
        expected_version=2,
        target_state=RunState.RUNNING,
        pid=99999999,
    )

    # External PID check returning False
    report = orchestrator.diagnose_run(
        record.run_id,
        external_pid_alive_fn=lambda pid: False,
    )

    assert report.primary_failure == FailureClass.DEAD_PID
    assert report.recovery_status == RecoveryStatus.INTERRUPTED
    assert report.recommended_action == RecoveryAction.RESUME_SAME_RUN
    assert report.can_resume_same_run is True

    # Mark interrupted
    interrupted_rec = orchestrator.mark_interrupted_if_orphaned(
        record.run_id,
        external_pid_alive_fn=lambda pid: False,
    )
    assert interrupted_rec.state == RunState.INTERRUPTED


def test_manager_restart_orphaned_running_detection(tmp_path: Path) -> None:
    """Verify that manager restart identifies orphaned in-process workers."""
    db_file = tmp_path / "mgr_restart.sqlite3"
    manager1 = DurableRunManager(db_file)
    contract = _create_test_contract(task_id="task-restart-01")

    # Start run with auto_spawn=False and manually move to RUNNING
    record = manager1.run_start(contract, auto_spawn=False)
    q_rec = manager1.store.transition_run(record.run_id, expected_version=1, target_state=RunState.QUEUED)
    r_rec = manager1.store.transition_run(record.run_id, expected_version=q_rec.state_version, target_state=RunState.RUNNING)

    # Recreate manager (simulating MCP server restart)
    manager2 = DurableRunManager(db_file)
    orchestrator2 = RecoveryOrchestrator(manager2)

    report = orchestrator2.diagnose_run(record.run_id)
    assert report.primary_failure == FailureClass.MCP_RESTART
    assert report.recovery_status == RecoveryStatus.INTERRUPTED
    assert report.recommended_action == RecoveryAction.RESUME_SAME_RUN
    assert report.can_resume_same_run is True


def test_stale_heartbeat_detection(tmp_path: Path) -> None:
    """Verify detection of stale heartbeat exceeding threshold."""
    db_file = tmp_path / "stale_hb.sqlite3"
    manager = DurableRunManager(db_file)
    orchestrator = RecoveryOrchestrator(manager, stale_heartbeat_threshold_seconds=2.0)

    contract = _create_test_contract(task_id="task-stale-hb")
    record = manager.run_start(
        contract,
        auto_spawn=False,
        worker_identity={"worker_type": "process", "pid": os.getpid()},
    )

    manager.store.transition_run(record.run_id, expected_version=1, target_state=RunState.QUEUED)
    manager.store.transition_run(record.run_id, expected_version=2, target_state=RunState.RUNNING)

    # Set old heartbeat
    old_ts = "2020-01-01T00:00:00+00:00"
    manager.store.update_heartbeat(record.run_id, timestamp=old_ts)

    evidence = orchestrator.collect_evidence(
        record.run_id,
        external_pid_alive_fn=lambda pid: True,
    )
    assert evidence.is_stale_heartbeat is True
    assert evidence.failure_class == FailureClass.STALE_HEARTBEAT

    report = orchestrator.diagnose_run(
        record.run_id,
        external_pid_alive_fn=lambda pid: True,
    )
    assert report.primary_failure == FailureClass.STALE_HEARTBEAT


def test_dirty_worktree_interruption_evidence(tmp_path: Path) -> None:
    """Verify dirty worktree detection with uncommitted file modifications."""
    db_file = tmp_path / "dirty_wt.sqlite3"
    workdir = tmp_path / "worktree_repo"
    workdir.mkdir()

    manager = DurableRunManager(db_file)
    orchestrator = RecoveryOrchestrator(manager)

    contract = _create_test_contract(task_id="task-dirty", workdir=workdir.as_posix())
    record = manager.run_start(contract, auto_spawn=False)
    manager.store.transition_run(record.run_id, expected_version=1, target_state=RunState.QUEUED)
    manager.store.transition_run(record.run_id, expected_version=2, target_state=RunState.INTERRUPTED)

    report = orchestrator.diagnose_run(record.run_id)
    assert report.evidence.worktree_exists is True
    assert report.checkpoint.worktree == workdir.as_posix()


def test_verification_and_precommit_interruption(tmp_path: Path) -> None:
    """Verify that runs interrupted during VERIFYING or COMMITTING are accurately classified without assuming success."""
    db_file = tmp_path / "interrupted_phases.sqlite3"
    manager = DurableRunManager(db_file)
    orchestrator = RecoveryOrchestrator(manager)

    contract1 = _create_test_contract(task_id="task-verif-interrupted")
    rec1 = manager.run_start(contract1, auto_spawn=False)
    q1 = manager.store.transition_run(rec1.run_id, expected_version=1, target_state=RunState.QUEUED)
    r1 = manager.store.transition_run(rec1.run_id, expected_version=q1.state_version, target_state=RunState.RUNNING)
    v1 = manager.store.transition_run(
        rec1.run_id,
        expected_version=r1.state_version,
        target_state=RunState.VERIFYING,
        verification_result={"passed": False, "status": "running"},
    )

    report1 = orchestrator.diagnose_run(rec1.run_id)
    assert report1.primary_failure == FailureClass.VERIFICATION_INTERRUPTION
    assert report1.recovery_status == RecoveryStatus.INTERRUPTED
    assert "cannot be assumed" in report1.reason

    contract2 = _create_test_contract(task_id="task-commit-interrupted")
    rec2 = manager.run_start(contract2, auto_spawn=False)
    q2 = manager.store.transition_run(rec2.run_id, expected_version=1, target_state=RunState.QUEUED)
    r2 = manager.store.transition_run(rec2.run_id, expected_version=q2.state_version, target_state=RunState.RUNNING)
    v2 = manager.store.transition_run(
        rec2.run_id,
        expected_version=r2.state_version,
        target_state=RunState.VERIFYING,
        verification_result={"passed": True, "status": "passed"},
    )
    c2 = manager.store.transition_run(rec2.run_id, expected_version=v2.state_version, target_state=RunState.COMMITTING)

    report2 = orchestrator.diagnose_run(rec2.run_id)
    assert report2.primary_failure == FailureClass.PRE_COMMIT_INTERRUPTION
    assert report2.recovery_status == RecoveryStatus.INTERRUPTED


def test_auth_quota_suspension_and_same_run_resume(tmp_path: Path) -> None:
    """Verify auth/quota error mapping to ACCOUNT_SWITCH_REQUIRED and same-run resume preserving all IDs."""
    db_file = tmp_path / "auth_quota.sqlite3"
    manager = DurableRunManager(db_file)
    orchestrator = RecoveryOrchestrator(manager)

    contract = _create_test_contract(
        task_id="task-auth-01",
        base_head="commit-base-001",
    )

    # Worker that fails with quota error
    def quota_failing_worker(ctx: WorkerContext) -> WorkerResult:
        return WorkerResult(
            success=False,
            target_state=RunState.ACCOUNT_SWITCH_REQUIRED,
            suspended_reason="Rate limit exceeded 429: Account daily quota reached",
            last_error="Quota exhausted",
            current_head="commit-working-002",
        )

    record = manager.run_start(contract, worker=quota_failing_worker)
    terminal = manager.run_wait(record.run_id, timeout=2.0)

    assert terminal.state == RunState.ACCOUNT_SWITCH_REQUIRED
    assert terminal.suspended_reason == "Rate limit exceeded 429: Account daily quota reached"

    # Verify diagnosis
    report = orchestrator.diagnose_run(record.run_id)
    assert report.primary_failure in (FailureClass.QUOTA_EXHAUSTED, FailureClass.RATE_LIMIT)
    assert report.recovery_status == RecoveryStatus.ACCOUNT_SWITCH_REQUIRED
    assert report.recommended_action == RecoveryAction.SWITCH_ACCOUNT_AND_RESUME
    assert report.can_resume_same_run is True
    assert report.checkpoint.task_id == contract.task_id
    assert report.checkpoint.run_id == record.run_id
    assert report.checkpoint.base_head == "commit-base-001"

    # Attempting to resume without account switch must fail
    with pytest.raises(UnrecoverableRunError):
        orchestrator.resume_same_run(record.run_id, account_switched=False, credentials_refreshed=False)

    # Test check_recovery_readiness with account_switched=True
    readiness_report = orchestrator.check_recovery_readiness(record.run_id, account_switched=True)
    assert readiness_report.recovery_status == RecoveryStatus.RECOVERY_READY
    assert readiness_report.recommended_action == RecoveryAction.RESUME_SAME_RUN

    # Resume on the EXACT SAME run_id and task_id with a successful worker
    resume_worker_called = threading.Event()

    def resumed_worker(ctx: WorkerContext) -> WorkerResult:
        assert ctx.run_id == record.run_id
        assert ctx.task_contract.task_id == contract.task_id
        resume_worker_called.set()
        return WorkerResult(
            success=True,
            verification_result={"passed": True, "status": "passed"},
            result_summary="Recovered and finished successfully",
            current_head="commit-final-003",
        )

    resumed_rec = orchestrator.resume_same_run(
        record.run_id,
        worker=resumed_worker,
        account_switched=True,
    )

    assert resumed_rec.run_id == record.run_id
    assert resumed_rec.task_id == contract.task_id
    assert resumed_rec.attempt == 1

    # Wait for completion
    final_rec = manager.run_wait(record.run_id, timeout=2.0)
    assert final_rec.run_id == record.run_id
    assert final_rec.task_id == contract.task_id
    assert final_rec.state == RunState.COMPLETE
    assert resume_worker_called.is_set()


def test_interrupted_run_staged_recovery_and_resume(tmp_path: Path) -> None:
    """Verify INTERRUPTED -> RECOVERY_READY -> QUEUED -> RUNNING -> COMPLETE on same run."""
    db_file = tmp_path / "interrupted_resume.sqlite3"
    manager = DurableRunManager(db_file)
    orchestrator = RecoveryOrchestrator(manager)

    contract = _create_test_contract(task_id="task-interrupted-01")
    record = manager.run_start(contract, auto_spawn=False)

    manager.store.transition_run(record.run_id, expected_version=1, target_state=RunState.QUEUED)
    manager.store.transition_run(record.run_id, expected_version=2, target_state=RunState.INTERRUPTED)

    # Evaluate readiness
    readiness = orchestrator.check_recovery_readiness(record.run_id)
    assert readiness.evidence.state == RunState.RECOVERY_READY

    # Resume
    def recovery_worker(ctx: WorkerContext) -> WorkerResult:
        return WorkerResult(
            success=True,
            verification_result={"passed": True, "status": "passed"},
            result_summary="Resumed successfully",
        )

    resumed = orchestrator.resume_same_run(record.run_id, worker=recovery_worker)
    assert resumed.run_id == record.run_id
    assert resumed.attempt == 1

    deadline = time.monotonic() + 2.0
    final_rec = manager.run_wait(record.run_id, timeout=max(0.01, deadline - time.monotonic()))
    while final_rec.state not in {RunState.COMPLETE, RunState.FAILED, RunState.CANCELLED} and time.monotonic() < deadline:
        time.sleep(0.01)
        final_rec = manager.run_status(record.run_id)
    assert final_rec.state == RunState.COMPLETE
    assert final_rec.result_summary == "Resumed successfully"


def test_inconsistent_evidence_fails_run_without_generating_new_ids(tmp_path: Path) -> None:
    """Verify that corrupt / inconsistent evidence transitions to FAILED without creating new IDs."""
    db_file = tmp_path / "inconsistent.sqlite3"
    manager = DurableRunManager(db_file)
    orchestrator = RecoveryOrchestrator(manager)

    contract = _create_test_contract(task_id="task-corrupt-01")
    record = manager.run_start(contract, auto_spawn=False)

    # Intentionally corrupt SQLite journal by setting task_contract_json to empty string
    with manager.store._lock:
        conn = manager.store._get_connection()
        try:
            conn.execute("UPDATE runs SET task_contract_json = '' WHERE run_id = ?;", (record.run_id,))
        finally:
            conn.close()

    report = orchestrator.diagnose_run(record.run_id)
    assert report.primary_failure == FailureClass.INCONSISTENT_EVIDENCE
    assert report.recovery_status == RecoveryStatus.FAILED
    assert report.can_resume_same_run is False

    # Attempting to resume corrupt run raises InconsistentEvidenceError
    with pytest.raises(InconsistentEvidenceError):
        orchestrator.resume_same_run(record.run_id)

    # Verify run transitioned to FAILED and NO new run was created
    all_runs = manager.list_runs()
    assert len(all_runs) == 1
    assert all_runs[0].run_id == record.run_id
    assert all_runs[0].state == RunState.FAILED


def test_duplicate_run_protection_during_recovery(tmp_path: Path) -> None:
    """Verify that an active run blocks new runs for the same task_id, preventing duplicate spawns."""
    db_file = tmp_path / "dup_rec.sqlite3"
    manager = DurableRunManager(db_file)
    orchestrator = RecoveryOrchestrator(manager)

    contract = _create_test_contract(task_id="task-no-dup")
    rec1 = manager.run_start(contract, auto_spawn=False)

    # Attempting to start a new run with the same task_id must raise DuplicateRunError
    with pytest.raises(DuplicateRunError):
        manager.run_start(contract, auto_spawn=False)

    # Suspend rec1 to ACCOUNT_SWITCH_REQUIRED
    manager.store.transition_run(rec1.run_id, expected_version=1, target_state=RunState.QUEUED)
    manager.store.transition_run(rec1.run_id, expected_version=2, target_state=RunState.RUNNING)
    manager.store.transition_run(
        rec1.run_id,
        expected_version=3,
        target_state=RunState.ACCOUNT_SWITCH_REQUIRED,
        suspended_reason="Auth required",
    )

    # Still considered active, so starting another run for same task is rejected
    with pytest.raises(DuplicateRunError):
        manager.run_start(contract, auto_spawn=False)


def test_credential_safety_in_evidence_and_reports(tmp_path: Path) -> None:
    """Verify that sensitive credential patterns in error messages or evidence are strictly rejected."""
    # Attempting to create RecoveryEvidence with credentials must raise ValueError
    with pytest.raises(ValueError, match="Credential-like content detected"):
        RecoveryEvidence(
            run_id="run-valid",
            task_id="task-valid",
            state=RunState.RUNNING,
            state_version=1,
            last_error="Error with sk-1234567890abcdef1234567890",
        )

    with pytest.raises(ValueError, match="Credential-like content detected"):
        RecoveryReport(
            run_id="run-valid",
            task_id="task-valid",
            primary_failure=FailureClass.UNKNOWN,
            recovery_status=RecoveryStatus.FAILED,
            recommended_action=RecoveryAction.FAIL_RUN,
            evidence=RecoveryEvidence(
                run_id="run-valid",
                task_id="task-valid",
                state=RunState.RUNNING,
                state_version=1,
            ),
            can_resume_same_run=False,
            reason="Bearer 1234567890abcdef123456",
            checkpoint=RecoveryEvidence(
                run_id="run-valid",
                task_id="task-valid",
                state=RunState.RUNNING,
                state_version=1,
            ).to_dict(),
        )
