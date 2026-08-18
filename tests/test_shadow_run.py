"""Comprehensive unit and integration tests for Phase 8 synthetic shadow-run harness."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
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
from codex_agy_bridge.policy import DecisionTier, evaluate_decision_policy
from codex_agy_bridge.recovery import (
    FailureClass,
    RecoveryOrchestrator,
    classify_error_message,
)
from codex_agy_bridge.run_control import (
    DurableRunManager,
    DuplicateRunError,
)
from codex_agy_bridge.scheduler import (
    DAGTaskState,
    DuplicateTaskError,
    TaskDAGScheduler,
)
from codex_agy_bridge.shadow import (
    ProductionPathForbiddenError,
    ShadowHarness,
    ShadowRunReport,
    SyntheticWorkspace,
    assert_isolated_workspace,
    create_synthetic_workspace,
)
from codex_agy_bridge.verification import evaluate_scope_gate


def test_isolated_workspace_security_guards(tmp_path: Path) -> None:
    """Validate that production and AshareAdvisor paths are unconditionally rejected."""
    # Forbidden substrings
    with pytest.raises(ProductionPathForbiddenError):
        assert_isolated_workspace("C:/Projects/AshareAdvisor/backend")

    with pytest.raises(ProductionPathForbiddenError):
        assert_isolated_workspace(tmp_path / "ashareadvisor_sub")

    with pytest.raises(ProductionPathForbiddenError):
        assert_isolated_workspace("D:/dev/Ashare_Advisor/repo")

    # Clean tmp workspace succeeds
    ws = create_synthetic_workspace(tmp_path)
    assert ws.root_dir.exists()
    assert ws.repo_dir.exists()
    assert (ws.repo_dir / ".git").exists()
    assert (ws.repo_dir / "src" / "calc.py").exists()
    assert len(ws.base_head) == 40
    ws.cleanup()


def test_complete_14_task_shadow_run(tmp_path: Path) -> None:
    """Execute full 14-task unattended synthetic shadow run and assert all target invariants."""
    ws = create_synthetic_workspace(tmp_path)
    harness = ShadowHarness(workspace=ws, python_bin=sys.executable)

    # 1. Build 14-task synthetic DAG
    tasks = harness.build_synthetic_dag()
    assert len(tasks) == 14

    # 2. Execute unattended shadow run
    report = harness.execute_shadow_run()

    # 3. Assert all Phase 8 invariants
    assert report.total == 14
    assert report.auto_complete >= 10
    assert report.wrong_commit == 0
    assert report.out_of_scope_accepted == 0
    assert report.lost_run == 0
    assert report.duplicate_task == 0
    assert report.state_corruption == 0
    assert report.auto_complete_rate >= 0.90
    assert report.invariants_satisfied is True

    # 4. Check specific scenario outcomes in DAG
    assert report.tasks["task_01_root_success"]["state"] == DAGTaskState.COMPLETE.value
    assert report.tasks["task_02_linear_dep"]["state"] == DAGTaskState.COMPLETE.value
    assert report.tasks["task_03_branch_a"]["state"] == DAGTaskState.COMPLETE.value
    assert report.tasks["task_04_branch_b"]["state"] == DAGTaskState.COMPLETE.value
    assert report.tasks["task_05_merge"]["state"] == DAGTaskState.COMPLETE.value
    assert report.tasks["task_06_repair_needed"]["state"] == DAGTaskState.COMPLETE.value
    assert report.tasks["task_07_worker_interrupted"]["state"] == DAGTaskState.COMPLETE.value
    assert report.tasks["task_08_mcp_restart"]["state"] == DAGTaskState.COMPLETE.value
    assert report.tasks["task_09_quota_suspended"]["state"] == DAGTaskState.COMPLETE.value
    assert report.tasks["task_10_out_of_scope"]["state"] == DAGTaskState.FAILED.value
    assert report.tasks["task_11_permanent_fail"]["state"] == DAGTaskState.FAILED.value
    assert report.tasks["task_12_blocked_dep"]["state"] == DAGTaskState.BLOCKED_BY_DEPENDENCY.value
    assert report.tasks["task_13_duplicate_check"]["state"] == DAGTaskState.COMPLETE.value
    assert report.tasks["task_14_production_guard"]["state"] == DAGTaskState.COMPLETE.value

    # 5. JSON serialization and roundtrip
    json_str = report.to_json(indent=2)
    assert "invariants_satisfied" in json_str
    assert "task_01_root_success" in json_str

    parsed = json.loads(json_str)
    assert parsed["wrong_commit"] == 0
    assert parsed["out_of_scope_accepted"] == 0

    reconstructed = ShadowRunReport.from_dict(parsed)
    assert reconstructed.invariants_satisfied is True
    assert reconstructed.total == report.total

    ws.cleanup()


def test_out_of_scope_rejection_isolated(tmp_path: Path) -> None:
    """Assert that out-of-scope file modifications are strictly blocked from committing."""
    ws = create_synthetic_workspace(tmp_path)
    repo = ws.repo_dir
    py_path_posix = Path(sys.executable).as_posix()

    contract = TaskContract(
        task_id="task_scope_guard",
        objective="Modify allowed math file",
        base_head=ws.base_head,
        workdir=repo.as_posix(),
        allowed_paths=["src/calc.py"],
        forbidden_paths=["config/secrets.json"],
        acceptance_criteria=["valid"],
        verification_commands=[f'"{py_path_posix}" -m pytest -q'],
        risk_class=RiskClass.CODE_CHANGES,
    )

    # Valid modification in allowed_paths
    (repo / "src" / "calc.py").write_text("# minor comment\n", encoding="utf-8")
    valid_res = evaluate_scope_gate(contract, repo)
    assert valid_res.passed is True

    # Forbidden modification
    (repo / "config" / "secrets.json").write_text('{"token": "secret"}', encoding="utf-8")
    invalid_res = evaluate_scope_gate(contract, repo)
    assert invalid_res.passed is False
    assert len(invalid_res.violations) >= 1
    assert any("forbidden_paths" in v or "allowed_paths" in v for v in invalid_res.violations)

    ws.cleanup()


def test_duplicate_start_protection_isolated(tmp_path: Path) -> None:
    """Assert scheduler and run manager raise appropriate duplicate exceptions."""
    ws = create_synthetic_workspace(tmp_path)
    sched = TaskDAGScheduler(ws.db_scheduler)
    mgr = DurableRunManager(ws.db_run_manager)

    contract = TaskContract(
        task_id="task_dup_check",
        objective="Duplicate test",
        base_head=ws.base_head,
        workdir=ws.repo_dir.as_posix(),
        allowed_paths=["src/calc.py"],
        risk_class=RiskClass.CODE_CHANGES,
    )

    # Scheduler duplicate task addition
    sched.add_task(contract)
    with pytest.raises(DuplicateTaskError):
        sched.add_task(contract)

    # DurableRunManager duplicate active run start
    r1 = mgr.run_start(contract, auto_spawn=False)
    with pytest.raises(DuplicateRunError):
        mgr.run_start(contract, auto_spawn=False)

    # Cancelling r1 allows subsequent run start
    mgr.store.transition_run(r1.run_id, expected_version=r1.state_version, target_state=RunState.CANCELLED)
    r2 = mgr.run_start(contract, auto_spawn=False)
    assert r2.run_id != r1.run_id

    ws.cleanup()


def test_interruption_and_auth_resume_isolated(tmp_path: Path) -> None:
    """Assert quota suspension and dead worker interruption recover and resume same-run."""
    ws = create_synthetic_workspace(tmp_path)
    mgr = DurableRunManager(ws.db_run_manager)
    recovery = RecoveryOrchestrator(mgr)

    contract = TaskContract(
        task_id="task_resume_check",
        objective="Interruption and resume test",
        base_head=ws.base_head,
        workdir=ws.repo_dir.as_posix(),
        allowed_paths=["src/calc.py"],
        risk_class=RiskClass.CODE_CHANGES,
    )

    # Quota 429 classification
    assert classify_error_message("429 Too Many Requests: Rate limit exceeded") == FailureClass.RATE_LIMIT

    # Move CREATED -> QUEUED -> RUNNING -> ACCOUNT_SWITCH_REQUIRED
    rec = mgr.run_start(contract, auto_spawn=False)
    q_rec = mgr.store.transition_run(rec.run_id, expected_version=1, target_state=RunState.QUEUED)
    r_rec = mgr.store.transition_run(rec.run_id, expected_version=q_rec.state_version, target_state=RunState.RUNNING)
    suspended = mgr.store.transition_run(
        rec.run_id,
        expected_version=r_rec.state_version,
        target_state=RunState.ACCOUNT_SWITCH_REQUIRED,
        suspended_reason="Quota reached",
    )
    assert suspended.state == RunState.ACCOUNT_SWITCH_REQUIRED

    # Resume same run
    resumed = recovery.resume_same_run(
        rec.run_id,
        account_switched=True,
        credentials_refreshed=True,
        auto_spawn=False,
    )
    assert resumed.run_id == rec.run_id
    assert resumed.state in (RunState.QUEUED, RunState.RUNNING)

    ws.cleanup()


def test_production_and_cutover_guard_isolated(tmp_path: Path) -> None:
    """Assert tasks involving production or broker actions are classified into HUMAN tier."""
    ws = create_synthetic_workspace(tmp_path)

    contract = TaskContract(
        task_id="task_prod_guard",
        objective="Deploy to live market trading and update production broker credentials",
        base_head=ws.base_head,
        workdir=ws.repo_dir.as_posix(),
        allowed_paths=["src/calc.py"],
        risk_class=RiskClass.PRODUCTION,
    )

    decision = evaluate_decision_policy(
        intent=contract.objective,
        modified_paths=contract.allowed_paths,
        risk_class=contract.risk_class,
    )
    assert decision.tier == DecisionTier.HUMAN_DECISION_REQUIRED
    assert decision.requires_human is True

    ws.cleanup()


def test_lost_run_detection_isolated(tmp_path: Path) -> None:
    """Assert non-terminal persisted runs left orphaned/active count as lost runs."""
    ws = create_synthetic_workspace(tmp_path)
    harness = ShadowHarness(workspace=ws, python_bin=sys.executable)

    contract = TaskContract(
        task_id="task_lost_check",
        objective="Lost run simulation",
        base_head=ws.base_head,
        workdir=ws.repo_dir.as_posix(),
        allowed_paths=["src/calc.py"],
        risk_class=RiskClass.CODE_CHANGES,
    )
    r = harness.run_manager.run_start(contract, auto_spawn=False)
    q_rec = harness.run_manager.store.transition_run(r.run_id, expected_version=1, target_state=RunState.QUEUED)
    harness.run_manager.store.transition_run(r.run_id, expected_version=q_rec.state_version, target_state=RunState.RUNNING)

    # Invariant audit must count this non-terminal run as lost
    all_runs = harness.run_manager.store.list_runs()
    lost_count = sum(1 for run in all_runs if run.state not in (RunState.COMPLETE, RunState.FAILED, RunState.CANCELLED))
    assert lost_count == 1

    ws.cleanup()
