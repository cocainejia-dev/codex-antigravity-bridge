from __future__ import annotations

import json
from pathlib import Path
import sys

# Ensure package import from mcp-antigravity-bridge/src
SRC = Path(__file__).resolve().parents[1] / "mcp-antigravity-bridge" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

from codex_agy_bridge.contracts import (
    ALLOWED_TRANSITIONS,
    AutoCommitPolicy,
    InvalidStateTransitionError,
    MAX_TASK_RUNTIME_SECONDS,
    RiskClass,
    RunRecord,
    RunState,
    TaskContract,
)


def test_all_14_exact_run_states_exist() -> None:
    expected = {
        "CREATED",
        "QUEUED",
        "RUNNING",
        "VERIFYING",
        "REPAIRING",
        "COMMITTING",
        "COMPLETE",
        "FAILED",
        "BLOCKED",
        "DECISION_REQUIRED",
        "ACCOUNT_SWITCH_REQUIRED",
        "INTERRUPTED",
        "RECOVERY_READY",
        "CANCELLED",
    }
    actual = {state.value for state in RunState}
    assert actual == expected
    assert len(RunState) == 14

    for name in expected:
        assert RunState.from_value(name).value == name
        assert RunState.from_value(name.lower()).value == name


def test_risk_classes_and_auto_commit_policies() -> None:
    expected_risks = {"READ_ONLY", "CODE_CHANGES", "DESTRUCTIVE", "PRODUCTION"}
    assert {r.value for r in RiskClass} == expected_risks

    assert RiskClass.from_value("read_only") == RiskClass.READ_ONLY
    assert RiskClass.from_value("code-changes") == RiskClass.CODE_CHANGES

    expected_policies = {"NEVER", "VERIFIED_ONLY", "ALWAYS"}
    assert {p.value for p in AutoCommitPolicy} == expected_policies

    assert AutoCommitPolicy.from_value("never") == AutoCommitPolicy.NEVER
    assert AutoCommitPolicy.from_value("verified-only") == AutoCommitPolicy.VERIFIED_ONLY


def test_task_contract_valid_construction() -> None:
    contract = TaskContract(
        task_id="task-001",
        objective="Implement bounded contract",
        base_head="abcdef0123456789",
        workdir="C:/Users/test/workspace" if Path("C:/").exists() else "/tmp/workspace",
        allowed_paths=["src/a.py", "tests/test_a.py"],
        forbidden_paths=["config/secrets.json"],
        acceptance_criteria=["pytest passes", "no leaks"],
        verification_commands=["pytest -q"],
        dependencies=["task-000"],
        risk_class=RiskClass.CODE_CHANGES,
        max_runtime=600,
        max_repair_rounds=3,
        auto_commit_policy=AutoCommitPolicy.VERIFIED_ONLY,
    )
    assert contract.task_id == "task-001"
    assert contract.max_runtime == 600
    assert contract.max_repair_rounds == 3
    assert contract.risk_class == RiskClass.CODE_CHANGES
    assert contract.auto_commit_policy == AutoCommitPolicy.VERIFIED_ONLY


def test_task_contract_rejects_relative_or_invalid_workdir() -> None:
    with pytest.raises(ValueError, match="must be an absolute path"):
        TaskContract(
            task_id="task-001",
            objective="Relative path test",
            base_head="abc",
            workdir="relative/path/dir",
        )

    with pytest.raises(ValueError, match="workdir must be a non-empty string or Path"):
        TaskContract(
            task_id="task-001",
            objective="Empty path test",
            base_head="abc",
            workdir="",
        )


def test_task_contract_path_normalization() -> None:
    workdir = "C:/tmp/repo" if Path("C:/").exists() else "/tmp/repo"
    contract = TaskContract(
        task_id="task-002",
        objective="Test normalization",
        base_head="head123",
        workdir=workdir,
        allowed_paths=["src\\a.py", "src/a.py", "src/b.py"],
        forbidden_paths=["secrets\\keys.txt"],
    )
    # Check normalized forward slashes and deduplication
    assert "src/a.py" in contract.allowed_paths
    assert len(contract.allowed_paths) == 2
    assert "secrets/keys.txt" in contract.forbidden_paths


def test_task_contract_non_negative_bounds() -> None:
    workdir = "C:/tmp/repo" if Path("C:/").exists() else "/tmp/repo"
    with pytest.raises(ValueError, match="max_runtime must be a non-negative number"):
        TaskContract(
            task_id="task-003",
            objective="Invalid runtime",
            base_head="head123",
            workdir=workdir,
            max_runtime=-10,
        )

    with pytest.raises(ValueError, match="max_repair_rounds must be a non-negative integer"):
        TaskContract(
            task_id="task-003",
            objective="Invalid repairs",
            base_head="head123",
            workdir=workdir,
            max_repair_rounds=-1,
        )


def test_task_contract_max_runtime_upper_bound() -> None:
    workdir = "C:/tmp/repo" if Path("C:/").exists() else "/tmp/repo"
    assert MAX_TASK_RUNTIME_SECONDS == 86400

    # Valid boundary value is accepted
    contract = TaskContract(
        task_id="task-valid-bound",
        objective="Valid runtime bound",
        base_head="head123",
        workdir=workdir,
        max_runtime=86400,
    )
    assert contract.max_runtime == 86400

    # Value exceeding upper bound is rejected
    with pytest.raises(ValueError, match=rf"max_runtime must be a non-negative number <= {MAX_TASK_RUNTIME_SECONDS}"):
        TaskContract(
            task_id="task-invalid-bound",
            objective="Over runtime bound",
            base_head="head123",
            workdir=workdir,
            max_runtime=86401,
        )


def test_task_contract_rejects_credential_like_fields() -> None:
    workdir = "C:/tmp/repo" if Path("C:/").exists() else "/tmp/repo"

    with pytest.raises(ValueError, match="Credential-like content detected"):
        TaskContract(
            task_id="task-004",
            objective="Use bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.token",
            base_head="head123",
            workdir=workdir,
        )

    with pytest.raises(ValueError, match="Credential-like content detected"):
        TaskContract(
            task_id="task-004",
            objective="Fix bugs",
            base_head="head123",
            workdir=workdir,
            verification_commands=["ghp_123456789012345678901234567890123456"],
        )

    with pytest.raises(ValueError, match="Credential-like content detected"):
        TaskContract(
            task_id="task-004",
            objective="Fix bugs",
            base_head="head123",
            workdir=workdir,
            acceptance_criteria=["sk-12345678901234567890123456789012"],
        )


def test_task_contract_json_round_trip() -> None:
    workdir = "C:/tmp/repo" if Path("C:/").exists() else "/tmp/repo"
    contract = TaskContract(
        task_id="task-roundtrip",
        objective="Verify serialization",
        base_head="sha123456",
        workdir=workdir,
        allowed_paths=["src/module.py"],
        forbidden_paths=["config/deploy.env"],
        acceptance_criteria=["unit tests pass"],
        verification_commands=["pytest -q tests/test_module.py"],
        dependencies=["task-init"],
        risk_class=RiskClass.DESTRUCTIVE,
        max_runtime=120,
        max_repair_rounds=1,
        auto_commit_policy=AutoCommitPolicy.ALWAYS,
    )

    d = contract.to_dict()
    assert isinstance(d, dict)
    assert d["risk_class"] == "DESTRUCTIVE"
    assert d["auto_commit_policy"] == "ALWAYS"

    json_str = contract.to_json()
    assert json.loads(json_str) == d

    restored = TaskContract.from_json(json_str)
    assert restored.task_id == contract.task_id
    assert restored.objective == contract.objective
    assert restored.risk_class == contract.risk_class
    assert restored.auto_commit_policy == contract.auto_commit_policy
    assert restored.allowed_paths == contract.allowed_paths
    assert restored.to_dict() == contract.to_dict()


def test_run_record_defaults_and_validation() -> None:
    record = RunRecord(
        run_id="run-001",
        task_id="task-001",
    )
    assert record.state == RunState.CREATED
    assert record.state_version == 1
    assert record.attempt == 0
    assert record.repair_round == 0
    assert record.created_at is not None
    assert record.updated_at is not None
    assert record.started_at is None
    assert record.pid is None


def test_run_record_rejects_invalid_bounds_and_credentials() -> None:
    with pytest.raises(ValueError, match="state_version must be an integer >= 1"):
        RunRecord(run_id="run-1", task_id="task-1", state_version=0)

    with pytest.raises(ValueError, match="attempt must be a non-negative integer"):
        RunRecord(run_id="run-1", task_id="task-1", attempt=-1)

    with pytest.raises(ValueError, match="repair_round must be a non-negative integer"):
        RunRecord(run_id="run-1", task_id="task-1", repair_round=-1)

    with pytest.raises(ValueError, match="Credential-like content detected"):
        RunRecord(
            run_id="run-1",
            task_id="task-1",
            last_error="Failed with password=SuperSecretPassword123",
        )


def test_run_record_guarded_state_transitions() -> None:
    record = RunRecord(run_id="run-002", task_id="task-002")
    assert record.state == RunState.CREATED
    assert record.state_version == 1

    # CREATED -> QUEUED
    record.transition_to(RunState.QUEUED)
    assert record.state == RunState.QUEUED
    assert record.state_version == 2

    # QUEUED -> RUNNING
    record.transition_to(RunState.RUNNING, pid=12345)
    assert record.state == RunState.RUNNING
    assert record.state_version == 3
    assert record.started_at is not None
    assert record.pid == 12345

    # RUNNING cannot go directly to COMPLETE
    with pytest.raises(InvalidStateTransitionError):
        record.transition_to(RunState.COMPLETE)

    # RUNNING -> VERIFYING
    record.transition_to(RunState.VERIFYING)
    assert record.state == RunState.VERIFYING
    assert record.state_version == 4

    # VERIFYING -> REPAIRING
    record.transition_to(RunState.REPAIRING, repair_round=1)
    assert record.state == RunState.REPAIRING
    assert record.state_version == 5
    assert record.repair_round == 1

    # REPAIRING -> RUNNING -> VERIFYING
    record.transition_to(RunState.RUNNING)
    assert record.state == RunState.RUNNING
    assert record.state_version == 6

    record.transition_to(RunState.VERIFYING)
    assert record.state == RunState.VERIFYING
    assert record.state_version == 7

    # VERIFYING -> COMPLETE without verification result must fail
    with pytest.raises(InvalidStateTransitionError, match="requires a successful verification_result"):
        record.transition_to(RunState.COMPLETE)

    # VERIFYING -> COMPLETE with failed verification must fail
    with pytest.raises(InvalidStateTransitionError, match="verification_result indicated failure"):
        record.transition_to(RunState.COMPLETE, verification_result={"passed": False, "returncode": 1})

    # VERIFYING -> COMMITTING -> COMPLETE with valid verification
    record.transition_to(RunState.COMMITTING, verification_result={"passed": True, "returncode": 0})
    assert record.state == RunState.COMMITTING
    assert record.state_version == 8

    record.transition_to(RunState.COMPLETE, commit_sha="1a2b3c4d5e")
    assert record.state == RunState.COMPLETE
    assert record.state_version == 9
    assert record.commit_sha == "1a2b3c4d5e"

    # Terminal state cannot transition further
    with pytest.raises(InvalidStateTransitionError):
        record.transition_to(RunState.RUNNING)


def test_complete_cannot_be_reached_from_created_or_queued() -> None:
    rec1 = RunRecord(run_id="r1", task_id="t1")
    with pytest.raises(InvalidStateTransitionError):
        rec1.transition_to(RunState.COMPLETE, verification_result={"passed": True})

    rec2 = RunRecord(run_id="r2", task_id="t2")
    rec2.transition_to(RunState.QUEUED)
    with pytest.raises(InvalidStateTransitionError):
        rec2.transition_to(RunState.COMPLETE, verification_result={"passed": True})


def test_special_operational_states_transitions() -> None:
    # Test DECISION_REQUIRED and ACCOUNT_SWITCH_REQUIRED
    rec = RunRecord(run_id="r-ops", task_id="t-ops")
    rec.transition_to(RunState.QUEUED)
    rec.transition_to(RunState.RUNNING)
    rec.transition_to(RunState.DECISION_REQUIRED, suspended_reason="Waiting for user input")
    assert rec.state == RunState.DECISION_REQUIRED
    assert rec.suspended_reason == "Waiting for user input"

    rec.transition_to(RunState.RUNNING)
    assert rec.state == RunState.RUNNING

    rec.transition_to(RunState.ACCOUNT_SWITCH_REQUIRED, suspended_reason="Quota exceeded on account A")
    assert rec.state == RunState.ACCOUNT_SWITCH_REQUIRED

    rec.transition_to(RunState.RUNNING)
    assert rec.state == RunState.RUNNING

    # INTERRUPTED -> RECOVERY_READY -> QUEUED -> RUNNING
    rec.transition_to(RunState.INTERRUPTED, last_error="Process killed unexpectedly")
    assert rec.state == RunState.INTERRUPTED

    rec.transition_to(RunState.RECOVERY_READY)
    assert rec.state == RunState.RECOVERY_READY

    rec.transition_to(RunState.QUEUED)
    assert rec.state == RunState.QUEUED


def test_run_record_json_round_trip() -> None:
    record = RunRecord(
        run_id="run-json-1",
        task_id="task-json-1",
        state=RunState.VERIFYING,
        state_version=4,
        pid=9876,
        heartbeat="2026-08-18T04:00:00+00:00",
        created_at="2026-08-18T03:55:00+00:00",
        started_at="2026-08-18T03:56:00+00:00",
        updated_at="2026-08-18T04:00:00+00:00",
        worktree="C:/repos/wt1" if Path("C:/").exists() else "/tmp/wt1",
        repo="C:/repos/main" if Path("C:/").exists() else "/tmp/main",
        base_head="commit001",
        current_head="commit002",
        attempt=1,
        repair_round=0,
        verification_result={"passed": True, "returncode": 0, "stdout": "All tests passed"},
        result_summary="Execution succeeded",
        commit_sha="c123456",
        last_error=None,
        suspended_reason=None,
    )

    d = record.to_dict()
    assert d["state"] == "VERIFYING"
    assert d["pid"] == 9876
    assert d["verification_result"]["passed"] is True

    json_str = record.to_json()
    assert json.loads(json_str) == d

    restored = RunRecord.from_json(json_str)
    assert restored.run_id == record.run_id
    assert restored.task_id == record.task_id
    assert restored.state == RunState.VERIFYING
    assert restored.state_version == 4
    assert restored.verification_result == record.verification_result
    assert restored.to_dict() == record.to_dict()


def test_task_contract_max_runtime_rejects_bool_nan_inf() -> None:
    workdir = "C:/tmp/repo" if Path("C:/").exists() else "/tmp/repo"

    for invalid in [True, False, float("nan"), float("inf"), float("-inf")]:
        with pytest.raises(ValueError, match="max_runtime must be a non-negative number"):
            TaskContract(
                task_id="t-invalid-rt",
                objective="Test invalid runtime",
                base_head="head123",
                workdir=workdir,
                max_runtime=invalid,
            )


def test_task_contract_max_repair_rounds_rejects_bool() -> None:
    workdir = "C:/tmp/repo" if Path("C:/").exists() else "/tmp/repo"

    for invalid in [True, False]:
        with pytest.raises(ValueError, match="max_repair_rounds must be a non-negative integer"):
            TaskContract(
                task_id="t-invalid-rr",
                objective="Test invalid repair rounds",
                base_head="head123",
                workdir=workdir,
                max_repair_rounds=invalid,
            )


def test_run_record_numeric_counters_reject_bool() -> None:
    for invalid in [True, False]:
        with pytest.raises(ValueError, match="state_version must be an integer >= 1"):
            RunRecord(run_id="r1", task_id="t1", state_version=invalid)

        with pytest.raises(ValueError, match="attempt must be a non-negative integer"):
            RunRecord(run_id="r1", task_id="t1", attempt=invalid)

        with pytest.raises(ValueError, match="repair_round must be a non-negative integer"):
            RunRecord(run_id="r1", task_id="t1", repair_round=invalid)

        with pytest.raises(ValueError, match="pid must be a non-negative integer or None"):
            RunRecord(run_id="r1", task_id="t1", pid=invalid)


def test_run_record_credentials_scanning_all_persisted_fields() -> None:
    base_kwargs = {"run_id": "r-sec", "task_id": "t-sec"}

    # worktree
    with pytest.raises(ValueError, match="Credential-like content detected in field 'worktree'"):
        RunRecord(**base_kwargs, worktree="C:/repo/ghp_123456789012345678901234567890123456")

    # repo
    with pytest.raises(ValueError, match="Credential-like content detected in field 'repo'"):
        RunRecord(**base_kwargs, repo="https://glpat-12345678901234567890@gitlab.com/test/repo")

    # base_head
    with pytest.raises(ValueError, match="Credential-like content detected in field 'base_head'"):
        RunRecord(**base_kwargs, base_head="AKIA1234567890ABCDEF")

    # current_head
    with pytest.raises(ValueError, match="Credential-like content detected in field 'current_head'"):
        RunRecord(**base_kwargs, current_head="AKIA1234567890ABCDEF")

    # commit_sha
    with pytest.raises(ValueError, match="Credential-like content detected in field 'commit_sha'"):
        RunRecord(**base_kwargs, commit_sha="bearer 1234567890123456")

    # verification_result recursive dict
    with pytest.raises(ValueError, match="Credential-like content detected in field 'verification_result.token'"):
        RunRecord(**base_kwargs, verification_result={"passed": True, "token": "gho_123456789012345678901234567890123456"})

    # verification_result recursive nested list in dict
    with pytest.raises(ValueError, match=r"Credential-like content detected in field 'verification_result\.items\[0\]\.key'"):
        RunRecord(
            **base_kwargs,
            verification_result={"passed": True, "items": [{"key": "sk-12345678901234567890123456789012"}]},
        )

    # verification_result string
    with pytest.raises(ValueError, match="Credential-like content detected in field 'verification_result'"):
        RunRecord(**base_kwargs, verification_result="bearer 1234567890123456")

    # result_summary
    with pytest.raises(ValueError, match="Credential-like content detected in field 'result_summary'"):
        RunRecord(**base_kwargs, result_summary="Completed with ghp_123456789012345678901234567890123456")

    # last_error
    with pytest.raises(ValueError, match="Credential-like content detected in field 'last_error'"):
        RunRecord(**base_kwargs, last_error="Error: api_key='secret12345'")

    # suspended_reason
    with pytest.raises(ValueError, match="Credential-like content detected in field 'suspended_reason'"):
        RunRecord(**base_kwargs, suspended_reason="Need password: password='super_secret_123'")

    # heartbeat
    with pytest.raises(ValueError, match="Credential-like content detected in field 'heartbeat'"):
        RunRecord(**base_kwargs, heartbeat="ghp_123456789012345678901234567890123456")

    # created_at
    with pytest.raises(ValueError, match="Credential-like content detected in field 'created_at'"):
        RunRecord(**base_kwargs, created_at="bearer 1234567890123456")

    # started_at
    with pytest.raises(ValueError, match="Credential-like content detected in field 'started_at'"):
        RunRecord(**base_kwargs, started_at="sk-12345678901234567890123456789012")

    # updated_at
    with pytest.raises(ValueError, match="Credential-like content detected in field 'updated_at'"):
        RunRecord(**base_kwargs, updated_at="glpat-12345678901234567890")
