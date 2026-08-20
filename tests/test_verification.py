"""Tests for Phase 4 VNext verification gate, deterministic failure package, bounded auto-repair orchestration, and safe auto-commit policy."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
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
    validate_no_credentials,
)
from codex_agy_bridge.run_control import DurableRunManager
from codex_agy_bridge.verification import (
    SOURCE_PROVENANCE_MISMATCH,
    AutoCommitDecision,
    AutoCommitResult,
    CommandResult,
    FailurePackage,
    RepairLoopResult,
    ScopeGateResult,
    VerificationEvidence,
    attest_source_provenance,
    create_failure_package,
    evaluate_auto_commit_policy,
    evaluate_scope_gate,
    execute_repair_loop,
    execute_verification_command,
    extract_bounded_traceback,
    extract_failed_tests,
    run_verification,
)


def _init_git_repo(repo_dir: Path) -> str:
    """Initialize a git repository in repo_dir and create an initial commit."""
    subprocess.run(["git", "init"], cwd=str(repo_dir), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=str(repo_dir), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo_dir), capture_output=True, check=True)

    init_file = repo_dir / "README.md"
    init_file.write_text("# Test Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=str(repo_dir), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(repo_dir), capture_output=True, check=True)

    res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo_dir), capture_output=True, text=True, check=True)
    return res.stdout.strip()


def test_command_result_serialization() -> None:
    """Test CommandResult serialization and deserialization."""
    res = CommandResult(
        command="python -m pytest",
        exit_code=0,
        stdout="1 passed",
        stderr="",
        duration_seconds=0.45,
        timed_out=False,
    )
    d = res.to_dict()
    assert d["command"] == "python -m pytest"
    assert d["exit_code"] == 0
    assert d["duration_seconds"] == 0.45

    res2 = CommandResult.from_dict(d)
    assert res2.command == res.command
    assert res2.exit_code == res.exit_code
    assert res2.duration_seconds == res.duration_seconds


def test_verification_evidence_serialization() -> None:
    """Test VerificationEvidence dataclass serialization and JSON safety."""
    evidence = VerificationEvidence(
        task_id="task-01",
        run_id="run-01",
        passed=True,
        commands=[CommandResult(command="pytest -q", exit_code=0, stdout="OK")],
        exit_codes=[0],
        failed_tests=[],
        bounded_traceback=None,
        changed_files=["src/main.py"],
        diff_summary={"insertions": 10, "deletions": 2, "files_changed": 1, "diff_bytes": 120},
        current_state=RunState.VERIFYING,
        scope_passed=True,
        scope_violations=[],
        repair_round=0,
    )
    d = evidence.to_dict()
    assert d["task_id"] == "task-01"
    assert d["passed"] is True
    assert d["current_state"] == "VERIFYING"
    assert len(d["commands"]) == 1

    json_str = evidence.to_json()
    loaded = VerificationEvidence.from_json(json_str)
    assert loaded.task_id == evidence.task_id
    assert loaded.passed == evidence.passed
    assert loaded.changed_files == evidence.changed_files


def test_failure_package_serialization_and_credentials_check() -> None:
    """Test FailurePackage creation, serialization, and credential validation."""
    pkg = FailurePackage(
        task_id="task-fail-01",
        run_id="run-fail-01",
        repair_round=1,
        failed_tests=["tests/test_mod.py::test_failing_case"],
        bounded_traceback="Traceback (most recent call last):\n  File 'test.py', line 5\nAssertionError",
        commands=[CommandResult(command="pytest", exit_code=1, stdout="FAILED test.py::test_failing_case")],
        exit_codes=[1],
        changed_files=["src/mod.py"],
        diff_summary={"insertions": 5, "deletions": 1, "files_changed": 1, "diff_bytes": 50},
        current_state=RunState.REPAIRING,
        scope_violations=[],
        error_message="Test assertion failed",
    )
    d = pkg.to_dict()
    assert d["repair_round"] == 1
    assert d["failed_tests"] == ["tests/test_mod.py::test_failing_case"]

    json_str = pkg.to_json()
    loaded = FailurePackage.from_json(json_str)
    assert loaded.task_id == pkg.task_id
    assert loaded.failed_tests == pkg.failed_tests

    # Verify that credential in failure package is rejected by validation
    with pytest.raises(ValueError, match="Credential-like content detected"):
        FailurePackage(
            task_id="task-fail-01",
            run_id="run-fail-01",
            repair_round=1,
            error_message="Leaked secret: " + "g" + "hp_" + "123456789012345678901234",
        )


def test_extract_failed_tests_pytest_and_unittest() -> None:
    """Test extracting test identifiers from pytest and unittest failure outputs."""
    pytest_output = """
============================= test session starts =============================
FAILED tests/test_core.py::test_one - AssertionError: expected 1 got 2
ERROR tests/test_db.py::TestDatabase::test_connect - ConnectionError
PASSED tests/test_core.py::test_two
=========================== 1 failed, 1 error, 1 passed ===========================
"""
    failed = extract_failed_tests(pytest_output)
    assert "tests/test_core.py::test_one" in failed
    assert "tests/test_db.py::TestDatabase::test_connect" in failed
    assert len(failed) == 2

    unittest_output = """
test_add (test_math.TestMath) ... ok
test_div (test_math.TestMath) ... FAIL
test_err (test_math.TestMath) ... ERROR

======================================================================
FAIL: test_div (test_math.TestMath)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "test_math.py", line 12, in test_div
    self.assertEqual(div(4, 2), 3)
AssertionError: 2 != 3
"""
    u_failed = extract_failed_tests(unittest_output)
    assert "test_math.TestMath.test_div" in u_failed


def test_extract_bounded_traceback() -> None:
    """Test extracting and bounding tracebacks."""
    output = """
Some log message
Traceback (most recent call last):
  File "run.py", line 10, in <module>
    main()
  File "run.py", line 5, in main
    raise ValueError("Something went wrong")
ValueError: Something went wrong
End of log
"""
    tb = extract_bounded_traceback(output, max_chars=500)
    assert tb is not None
    assert "Traceback (most recent call last):" in tb
    assert "ValueError: Something went wrong" in tb

    # Bounded truncation test
    large_tb = "Traceback (most recent call last):\n" + ("  line\n" * 200) + "ValueError: boom"
    bounded = extract_bounded_traceback(large_tb, max_chars=120)
    assert bounded is not None
    assert len(bounded) <= 150
    assert "boom" in bounded
    assert "[truncated traceback]" in bounded


def test_normal_verification_pass(tmp_path: Path) -> None:
    """Test normal verification pass when commands succeed and scope is clean."""
    head_sha = _init_git_repo(tmp_path)
    test_file = tmp_path / "test_sample.py"
    test_file.write_text("def test_ok(): assert 1 == 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "test_sample.py"], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "Add sample test"], cwd=str(tmp_path), capture_output=True, check=True)

    contract = TaskContract(
        task_id="task-normal-pass",
        objective="Run passing tests",
        base_head=head_sha,
        workdir=tmp_path.as_posix(),
        allowed_paths=["test_sample.py", "README.md"],
        verification_commands=[f'"{sys.executable}" -B -m pytest -q test_sample.py'],
        risk_class=RiskClass.CODE_CHANGES,
        auto_commit_policy=AutoCommitPolicy.VERIFIED_ONLY,
    )

    evidence = run_verification(contract, tmp_path)
    assert evidence.passed is True
    assert evidence.scope_passed is True
    assert len(evidence.exit_codes) == 1
    assert evidence.exit_codes[0] == 0
    assert len(evidence.failed_tests) == 0


def test_scope_gate_out_of_scope_rejection(tmp_path: Path) -> None:
    """Test scope gate rejects changed files outside allowed_paths."""
    head_sha = _init_git_repo(tmp_path)

    # Modify an unallowed file
    unallowed = tmp_path / "secret_unallowed.py"
    unallowed.write_text("print('not allowed')\n", encoding="utf-8")

    contract = TaskContract(
        task_id="task-scope-reject",
        objective="Modify allowed file only",
        base_head=head_sha,
        workdir=tmp_path.as_posix(),
        allowed_paths=["src/allowed.py"],
        verification_commands=[],
    )

    scope = evaluate_scope_gate(contract, tmp_path)
    assert scope.passed is False
    assert any("not in contract.allowed_paths" in v for v in scope.violations)

    evidence = run_verification(contract, tmp_path)
    assert evidence.passed is False
    assert evidence.scope_passed is False
    assert len(evidence.scope_violations) > 0


def test_scope_gate_forbidden_paths_rejection(tmp_path: Path) -> None:
    """Test scope gate rejects changed files matching forbidden_paths."""
    head_sha = _init_git_repo(tmp_path)

    forbidden = tmp_path / "credentials.json"
    forbidden.write_text('{"key": "val"}', encoding="utf-8")

    contract = TaskContract(
        task_id="task-forbidden-reject",
        objective="Avoid forbidden files",
        base_head=head_sha,
        workdir=tmp_path.as_posix(),
        allowed_paths=[],
        forbidden_paths=["credentials.json", "config/secrets/*"],
        verification_commands=[],
    )

    scope = evaluate_scope_gate(contract, tmp_path)
    assert scope.passed is False
    assert any("matches contract.forbidden_paths" in v for v in scope.violations)


def test_scope_gate_base_head_mismatch(tmp_path: Path) -> None:
    """Test scope gate rejects when base_head does not match repository HEAD/ancestry."""
    head_sha = _init_git_repo(tmp_path)

    contract = TaskContract(
        task_id="task-base-mismatch",
        objective="Base mismatch test",
        base_head="0000000000000000000000000000000000000000",
        workdir=tmp_path.as_posix(),
        allowed_paths=[],
        verification_commands=[],
    )

    scope = evaluate_scope_gate(contract, tmp_path)
    assert scope.passed is False
    assert scope.base_head_matched is False
    assert any("Base head mismatch" in v for v in scope.violations)


def test_scope_gate_diff_size_guard(tmp_path: Path) -> None:
    """Test scope gate rejects diffs exceeding configured size thresholds."""
    head_sha = _init_git_repo(tmp_path)

    large_file = tmp_path / "large.txt"
    large_file.write_text("\n".join(f"line {i}" for i in range(500)), encoding="utf-8")
    subprocess.run(["git", "add", "large.txt"], cwd=str(tmp_path), capture_output=True, check=True)

    contract = TaskContract(
        task_id="task-diff-size",
        objective="Diff size check",
        base_head=head_sha,
        workdir=tmp_path.as_posix(),
        allowed_paths=["large.txt"],
        verification_commands=[],
    )

    # Max diff lines set to 50
    scope = evaluate_scope_gate(contract, tmp_path, max_diff_lines=50)
    assert scope.passed is False
    assert any("exceeded max_diff_lines guard" in v for v in scope.violations)


def test_scope_gate_detects_credentials_in_diff(tmp_path: Path) -> None:
    """Test scope gate detects credential pattern in git diff and rejects it."""
    head_sha = _init_git_repo(tmp_path)

    code_file = tmp_path / "app.py"
    code_file.write_text('api_key = "sk-123456789012345678901234567890"\n', encoding="utf-8")

    contract = TaskContract(
        task_id="task-secret-diff",
        objective="Secret diff check",
        base_head=head_sha,
        workdir=tmp_path.as_posix(),
        allowed_paths=["app.py"],
        verification_commands=[],
    )

    scope = evaluate_scope_gate(contract, tmp_path)
    assert scope.passed is False
    assert scope.security_clean is False
    assert any("Security violation: Credential-like secret detected" in v for v in scope.violations)


def test_auto_commit_decision_gate(tmp_path: Path) -> None:
    """Test safe auto-commit policy gate evaluations for safe and unsafe conditions."""
    head_sha = _init_git_repo(tmp_path)

    # 1. Normal safe case -> PASS
    contract_safe = TaskContract(
        task_id="task-safe-commit",
        objective="Safe code change",
        base_head=head_sha,
        workdir=tmp_path.as_posix(),
        allowed_paths=["src/feature.py"],
        risk_class=RiskClass.CODE_CHANGES,
        auto_commit_policy=AutoCommitPolicy.VERIFIED_ONLY,
    )
    safe_evidence = VerificationEvidence(
        task_id="task-safe-commit",
        run_id="run-safe-01",
        passed=True,
        scope_passed=True,
        changed_files=["src/feature.py"],
    )
    decision = evaluate_auto_commit_policy(contract_safe, safe_evidence, tmp_path)
    assert decision.decision == AutoCommitDecision.PASS
    assert decision.allowed is True

    # 2. AutoCommitPolicy.NEVER -> CODEX_REVIEW_REQUIRED
    contract_never = TaskContract(
        task_id="task-never-commit",
        objective="Never commit policy",
        base_head=head_sha,
        workdir=tmp_path.as_posix(),
        risk_class=RiskClass.CODE_CHANGES,
        auto_commit_policy=AutoCommitPolicy.NEVER,
    )
    decision_never = evaluate_auto_commit_policy(contract_never, safe_evidence, tmp_path)
    assert decision_never.decision == AutoCommitDecision.CODEX_REVIEW_REQUIRED
    assert decision_never.allowed is False
    assert any("AutoCommitPolicy is NEVER" in r for r in decision_never.reasons)

    # 3. RiskClass.PRODUCTION -> CODEX_REVIEW_REQUIRED
    contract_prod = TaskContract(
        task_id="task-prod-risk",
        objective="Production risk",
        base_head=head_sha,
        workdir=tmp_path.as_posix(),
        risk_class=RiskClass.PRODUCTION,
        auto_commit_policy=AutoCommitPolicy.VERIFIED_ONLY,
    )
    decision_prod = evaluate_auto_commit_policy(contract_prod, safe_evidence, tmp_path)
    assert decision_prod.decision == AutoCommitDecision.CODEX_REVIEW_REQUIRED
    assert decision_prod.allowed is False
    assert any("PRODUCTION" in r for r in decision_prod.reasons)

    # 4. Verification failed evidence -> CODEX_REVIEW_REQUIRED
    failed_evidence = VerificationEvidence(
        task_id="task-safe-commit",
        run_id="run-safe-01",
        passed=False,
        scope_passed=True,
        error_message="Test failed",
    )
    decision_fail = evaluate_auto_commit_policy(contract_safe, failed_evidence, tmp_path)
    assert decision_fail.decision == AutoCommitDecision.CODEX_REVIEW_REQUIRED
    assert decision_fail.allowed is False

    # 5. Migration file detected -> CODEX_REVIEW_REQUIRED
    migration_evidence = VerificationEvidence(
        task_id="task-safe-commit",
        run_id="run-safe-01",
        passed=True,
        scope_passed=True,
        changed_files=["migrations/0001_initial.py"],
    )
    decision_mig = evaluate_auto_commit_policy(contract_safe, migration_evidence, tmp_path)
    assert decision_mig.decision == AutoCommitDecision.CODEX_REVIEW_REQUIRED
    assert any("Migration file detected" in r for r in decision_mig.reasons)


def test_auto_commit_rejects_manually_crafted_evidence_on_base_head_mismatch(tmp_path: Path) -> None:
    """Test safe auto-commit policy gate rejects manually-crafted passing evidence when base_head mismatches repository HEAD."""
    head_sha = _init_git_repo(tmp_path)

    # Contract with a foreign / invalid base_head
    contract = TaskContract(
        task_id="task-fake-evidence-base-mismatch",
        objective="Reject forged evidence with bad base_head",
        base_head="1111111111111111111111111111111111111111",
        workdir=tmp_path.as_posix(),
        allowed_paths=["src/feature.py"],
        risk_class=RiskClass.CODE_CHANGES,
        auto_commit_policy=AutoCommitPolicy.VERIFIED_ONLY,
    )

    # Forged passing evidence claiming all verified and scope passed
    forged_evidence = VerificationEvidence(
        task_id="task-fake-evidence-base-mismatch",
        run_id="run-fake-01",
        passed=True,
        scope_passed=True,
        changed_files=["src/feature.py"],
    )

    decision = evaluate_auto_commit_policy(contract, forged_evidence, tmp_path)
    assert decision.decision == AutoCommitDecision.CODEX_REVIEW_REQUIRED
    assert decision.allowed is False
    assert any("Base head mismatch" in r or "base_head" in r for r in decision.reasons)


def test_auto_commit_rejects_manually_crafted_evidence_on_diff_check_failure(tmp_path: Path) -> None:
    """Test safe auto-commit gate rejects manually-crafted passing evidence when git diff --check fails (conflict markers)."""
    head_sha = _init_git_repo(tmp_path)

    # Create a file with merge conflict markers
    conflict_file = tmp_path / "conflict.py"
    conflict_file.write_text(
        "<<<<<<< HEAD\ndef func(): return 1\n=======\ndef func(): return 2\n>>>>>>> feature\n",
        encoding="utf-8",
    )

    contract = TaskContract(
        task_id="task-fake-diff-check",
        objective="Reject forged evidence when diff --check detects conflict markers",
        base_head=head_sha,
        workdir=tmp_path.as_posix(),
        allowed_paths=["conflict.py"],
        risk_class=RiskClass.CODE_CHANGES,
        auto_commit_policy=AutoCommitPolicy.VERIFIED_ONLY,
    )

    forged_evidence = VerificationEvidence(
        task_id="task-fake-diff-check",
        run_id="run-fake-02",
        passed=True,
        scope_passed=True,
        changed_files=["conflict.py"],
    )

    decision = evaluate_auto_commit_policy(contract, forged_evidence, tmp_path)
    assert decision.decision == AutoCommitDecision.CODEX_REVIEW_REQUIRED
    assert decision.allowed is False
    assert any("diff --check" in r or "conflict markers" in r for r in decision.reasons)


def test_auto_commit_rejects_manually_crafted_evidence_on_unallowed_scope(tmp_path: Path) -> None:
    """Test safe auto-commit gate rejects manually-crafted passing evidence when unallowed files are modified in workdir."""
    head_sha = _init_git_repo(tmp_path)

    # Create an unallowed modified file in the worktree
    rogue_file = tmp_path / "rogue.py"
    rogue_file.write_text("print('rogue change')\n", encoding="utf-8")

    contract = TaskContract(
        task_id="task-fake-scope-unallowed",
        objective="Reject forged evidence when unallowed file modified",
        base_head=head_sha,
        workdir=tmp_path.as_posix(),
        allowed_paths=["src/safe.py"],
        risk_class=RiskClass.CODE_CHANGES,
        auto_commit_policy=AutoCommitPolicy.VERIFIED_ONLY,
    )

    forged_evidence = VerificationEvidence(
        task_id="task-fake-scope-unallowed",
        run_id="run-fake-03",
        passed=True,
        scope_passed=True,
        changed_files=["src/safe.py"],
    )

    decision = evaluate_auto_commit_policy(contract, forged_evidence, tmp_path)
    assert decision.decision == AutoCommitDecision.CODEX_REVIEW_REQUIRED
    assert decision.allowed is False
    assert any("rogue.py" in r and "not in contract.allowed_paths" in r for r in decision.reasons)


def test_auto_commit_rejects_manually_crafted_evidence_on_forbidden_path(tmp_path: Path) -> None:
    """Test safe auto-commit gate rejects manually-crafted passing evidence when forbidden files are present."""
    head_sha = _init_git_repo(tmp_path)

    # Create a forbidden file in worktree
    secret_file = tmp_path / "credentials.json"
    secret_file.write_text('{"token": "xyz"}', encoding="utf-8")

    contract = TaskContract(
        task_id="task-fake-scope-forbidden",
        objective="Reject forged evidence when forbidden path modified",
        base_head=head_sha,
        workdir=tmp_path.as_posix(),
        allowed_paths=[],
        forbidden_paths=["credentials.json"],
        risk_class=RiskClass.CODE_CHANGES,
        auto_commit_policy=AutoCommitPolicy.VERIFIED_ONLY,
    )

    forged_evidence = VerificationEvidence(
        task_id="task-fake-scope-forbidden",
        run_id="run-fake-04",
        passed=True,
        scope_passed=True,
        changed_files=["README.md"],
    )

    decision = evaluate_auto_commit_policy(contract, forged_evidence, tmp_path)
    assert decision.decision == AutoCommitDecision.CODEX_REVIEW_REQUIRED
    assert decision.allowed is False
    assert any("matches contract.forbidden_paths" in r for r in decision.reasons)


def test_auto_commit_rejects_manually_crafted_evidence_on_secret_in_diff(tmp_path: Path) -> None:
    """Test safe auto-commit gate rejects manually-crafted passing evidence when secrets exist in worktree diff."""
    head_sha = _init_git_repo(tmp_path)

    # Write a secret into allowed file
    code_file = tmp_path / "app.py"
    code_file.write_text('token = "' + "g" + "hp_" + '123456789012345678901234567890123456"\n', encoding="utf-8")

    contract = TaskContract(
        task_id="task-fake-secret-diff",
        objective="Reject forged evidence when secret in diff",
        base_head=head_sha,
        workdir=tmp_path.as_posix(),
        allowed_paths=["app.py"],
        risk_class=RiskClass.CODE_CHANGES,
        auto_commit_policy=AutoCommitPolicy.VERIFIED_ONLY,
    )

    forged_evidence = VerificationEvidence(
        task_id="task-fake-secret-diff",
        run_id="run-fake-05",
        passed=True,
        scope_passed=True,
        changed_files=["app.py"],
    )

    decision = evaluate_auto_commit_policy(contract, forged_evidence, tmp_path)
    assert decision.decision == AutoCommitDecision.CODEX_REVIEW_REQUIRED
    assert decision.allowed is False
    assert any("Security violation" in r or "Credential-like secret" in r for r in decision.reasons)


def test_bounded_repair_loop_fixes_failure_on_second_round(tmp_path: Path) -> None:
    """Test auto-repair loop: first round fails, injectable repair callback fixes code, second round passes."""
    head_sha = _init_git_repo(tmp_path)
    code_file = tmp_path / "calc.py"
    # Initially broken function
    code_file.write_text("def add(a, b): return a - b\n", encoding="utf-8")

    test_file = tmp_path / "test_calc.py"
    test_file.write_text("import calc\ndef test_add(): assert calc.add(2, 3) == 5\n", encoding="utf-8")

    contract = TaskContract(
        task_id="task-repair-success",
        objective="Fix add function in calc.py",
        base_head=head_sha,
        workdir=tmp_path.as_posix(),
        allowed_paths=["calc.py", "test_calc.py"],
        verification_commands=[f'"{sys.executable}" -B -m pytest -q test_calc.py'],
        max_repair_rounds=3,
        auto_commit_policy=AutoCommitPolicy.VERIFIED_ONLY,
    )

    repair_attempts = 0

    def repair_callback(pkg: FailurePackage, ctx: Any) -> bool:
        nonlocal repair_attempts
        repair_attempts += 1
        assert pkg.repair_round == 0
        assert len(pkg.failed_tests) > 0 or len(pkg.exit_codes) > 0
        # Fix the bug
        code_file.write_text("def add(a, b): return a + b\n", encoding="utf-8")
        return True

    # Execute repair loop
    result: RepairLoopResult = execute_repair_loop(
        contract,
        workdir=tmp_path,
        repair_callback=repair_callback,
        max_repair_rounds=3,
    )

    assert result.success is True
    assert result.final_state == RunState.COMPLETE
    assert result.repair_rounds_completed == 1
    assert repair_attempts == 1
    assert result.final_evidence.passed is True
    assert result.auto_commit_result is not None
    assert result.auto_commit_result.decision == AutoCommitDecision.PASS


def test_bounded_repair_loop_exhaustion(tmp_path: Path) -> None:
    """Test repair loop terminates and returns FAILED when max_repair_rounds is reached without fix."""
    head_sha = _init_git_repo(tmp_path)
    broken_test = tmp_path / "test_broken.py"
    broken_test.write_text("def test_never_passes(): assert False\n", encoding="utf-8")

    contract = TaskContract(
        task_id="task-exhaust-repair",
        objective="Exhaust repair rounds",
        base_head=head_sha,
        workdir=tmp_path.as_posix(),
        allowed_paths=["test_broken.py"],
        verification_commands=[f'"{sys.executable}" -B -m pytest -q test_broken.py'],
        max_repair_rounds=2,
    )

    repair_calls = 0

    def repair_callback(pkg: FailurePackage, ctx: Any) -> bool:
        nonlocal repair_calls
        repair_calls += 1
        # No fix made
        return True

    result = execute_repair_loop(
        contract,
        workdir=tmp_path,
        repair_callback=repair_callback,
        max_repair_rounds=2,
    )

    assert result.success is False
    assert result.final_state == RunState.FAILED
    assert result.repair_rounds_completed == 2
    assert repair_calls == 2
    assert result.final_evidence.passed is False
    assert result.failure_package is not None
    assert "Verification failed after 2 repair rounds" in (result.error_message or "")


def test_bounded_repair_loop_callback_failure(tmp_path: Path) -> None:
    """Test repair loop handles repair callback returning False or raising exception."""
    head_sha = _init_git_repo(tmp_path)
    broken_test = tmp_path / "test_err.py"
    broken_test.write_text("def test_fail(): assert False\n", encoding="utf-8")

    contract = TaskContract(
        task_id="task-callback-fail",
        objective="Callback failure test",
        base_head=head_sha,
        workdir=tmp_path.as_posix(),
        allowed_paths=["test_err.py"],
        verification_commands=[f'"{sys.executable}" -B -m pytest -q test_err.py'],
        max_repair_rounds=3,
    )

    def failing_callback(pkg: FailurePackage, ctx: Any) -> bool:
        return False

    result = execute_repair_loop(
        contract,
        workdir=tmp_path,
        repair_callback=failing_callback,
    )

    assert result.success is False
    assert result.final_state == RunState.FAILED
    assert result.repair_rounds_completed == 0
    assert "Repair callback returned failure" in (result.error_message or "")


def test_repair_loop_with_durable_run_manager(tmp_path: Path) -> None:
    """Test repair loop integrated with DurableRunManager recording states and transitions into SQLite."""
    head_sha = _init_git_repo(tmp_path)
    # Put SQLite journal in a dedicated directory outside tracked paths
    db_file = tmp_path.parent / "db_journal" / "runs.sqlite3"
    manager = DurableRunManager(db_file)

    code_file = tmp_path / "module.py"
    code_file.write_text("def answer(): return 41\n", encoding="utf-8")

    test_file = tmp_path / "test_module.py"
    test_file.write_text("import module\ndef test_answer(): assert module.answer() == 42\n", encoding="utf-8")

    contract = TaskContract(
        task_id="task-durable-repair",
        objective="Durable repair integration test",
        base_head=head_sha,
        workdir=tmp_path.as_posix(),
        allowed_paths=["module.py", "test_module.py"],
        verification_commands=[f'"{sys.executable}" -B -m pytest -q test_module.py'],
        max_repair_rounds=2,
        auto_commit_policy=AutoCommitPolicy.VERIFIED_ONLY,
    )

    record = manager.run_start(contract, auto_spawn=False)
    # Transition to RUNNING
    manager.store.transition_run(record.run_id, expected_version=1, target_state=RunState.QUEUED)
    manager.store.transition_run(record.run_id, expected_version=2, target_state=RunState.RUNNING)

    def repair_callback(pkg: FailurePackage, ctx: Any) -> bool:
        # Fix code
        code_file.write_text("def answer(): return 42\n", encoding="utf-8")
        return True

    result = execute_repair_loop(
        contract,
        workdir=tmp_path,
        run_id=record.run_id,
        repair_callback=repair_callback,
        max_repair_rounds=2,
        run_manager=manager,
    )

    assert result.success is True
    assert result.final_state == RunState.COMPLETE
    assert result.repair_rounds_completed == 1

    # Check journal persistence
    final_record = manager.run_status(record.run_id)
    assert final_record.state == RunState.COMPLETE
    assert final_record.repair_round == 1
    assert final_record.verification_result is not None
    assert final_record.verification_result.get("passed") is True


def test_execute_verification_command_rejects_foreign_provenance(tmp_path: Path) -> None:
    """Test execute_verification_command fails closed or reports mismatch when foreign PYTHONPATH shadows target."""
    foreign_dir = tmp_path / "foreign_lib"
    foreign_pkg = foreign_dir / "codex_agy_bridge"
    foreign_pkg.mkdir(parents=True, exist_ok=True)
    (foreign_pkg / "__init__.py").write_text("__version__ = '0.0.0-foreign'\n", encoding="utf-8")
    (foreign_pkg / "server.py").write_text("# foreign server implementation\n", encoding="utf-8")

    target_src = SRC.resolve()
    # Deliberately foreign PYTHONPATH before target source
    env_override = {"PYTHONPATH": f"{foreign_dir}{os.pathsep}{target_src}"}

    cmd = (
        f'"{sys.executable}" -c '
        f'"import codex_agy_bridge, codex_agy_bridge.server; '
        f'print(codex_agy_bridge.__file__); print(codex_agy_bridge.server.__file__)"'
    )

    res = execute_verification_command(cmd, cwd=tmp_path, env=env_override)

    # Verify foreign module was indeed loaded by cold subprocess
    lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
    assert len(lines) >= 2, f"Subprocess output missing expected module file lines: {res.stdout!r}, stderr: {res.stderr!r}"
    assert str(foreign_pkg) in lines[0] or foreign_dir.name in lines[0]
    assert str(foreign_pkg) in lines[1] or foreign_dir.name in lines[1]

    # RED assertion: API must fail closed or report provenance mismatch against foreign resolution
    assert res.exit_code != 0, (
        f"Expected execute_verification_command to fail closed on foreign provenance shadow, "
        f"but command exited 0 with resolved modules: {lines}"
    )
    assert "provenance" in res.stderr.lower(), (
        f"Expected provenance error in stderr, got stderr: {res.stderr!r}"
    )


def test_run_verification_fails_closed_on_foreign_provenance_shadow(tmp_path: Path) -> None:
    """Detect raw mismatch, then prove controller binding overrides the shadow."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    head_sha = _init_git_repo(repo_dir)

    foreign_dir = tmp_path / "foreign_lib"
    foreign_pkg = foreign_dir / "codex_agy_bridge"
    foreign_pkg.mkdir(parents=True, exist_ok=True)
    (foreign_pkg / "__init__.py").write_text("__version__ = '0.0.0-foreign'\n", encoding="utf-8")
    (foreign_pkg / "server.py").write_text("# foreign server implementation\n", encoding="utf-8")

    target_src = SRC.resolve()
    env_override = {"PYTHONPATH": f"{foreign_dir}{os.pathsep}{target_src}"}

    direct_attestation = attest_source_provenance(target_src, env_override, cwd=repo_dir)
    assert direct_attestation["status"] == SOURCE_PROVENANCE_MISMATCH
    assert direct_attestation["verified"] is False
    assert direct_attestation["resolved_package_file"] is not None
    assert foreign_dir.name in direct_attestation["resolved_package_file"]
    assert "outside expected source root" in (direct_attestation.get("error") or "")

    cmd = (
        f'"{sys.executable}" -c '
        f'"import codex_agy_bridge, codex_agy_bridge.server; '
        f'print(codex_agy_bridge.__file__); print(codex_agy_bridge.server.__file__)"'
    )

    contract = TaskContract(
        task_id="task-provenance-foreign-gate",
        objective="Verify controller binding overrides foreign provenance shadow",
        base_head=head_sha,
        workdir=repo_dir.as_posix(),
        allowed_paths=["README.md"],
        verification_commands=[cmd],
    )

    evidence = run_verification(contract, workdir=repo_dir, env=env_override)

    assert evidence.passed is True
    assert evidence.provenance_status == "PASS"
    assert evidence.provenance_verified is True
    assert len(evidence.commands) == 1
    assert evidence.commands[0].exit_code == 0
    lines = [line.strip() for line in evidence.commands[0].stdout.splitlines() if line.strip()]
    assert len(lines) >= 2
    assert Path(lines[0]).resolve().is_relative_to(target_src)
    assert Path(lines[1]).resolve().is_relative_to(target_src)
    assert foreign_dir.name not in lines[0]
    assert foreign_dir.name not in lines[1]


def test_run_verification_with_caller_env_preserves_controller_binding_and_sentinel(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    head_sha = _init_git_repo(repo_dir)

    foreign_dir = tmp_path / "foreign_lib"
    foreign_pkg = foreign_dir / "codex_agy_bridge"
    foreign_pkg.mkdir(parents=True, exist_ok=True)
    (foreign_pkg / "__init__.py").write_text("__version__ = 'foreign'\n", encoding="utf-8")
    (foreign_pkg / "server.py").write_text("# foreign server\n", encoding="utf-8")

    target_src = SRC.resolve()
    caller_env = {
        "R2_SENTINEL": "1",
        "PYTHONPATH": f"{foreign_dir}{os.pathsep}{target_src}",
    }
    cmd = (
        f'"{sys.executable}" -c '
        f'"import os, codex_agy_bridge, codex_agy_bridge.server; '
        f"assert os.environ.get('R2_SENTINEL') == '1'; "
        f'print(codex_agy_bridge.__file__); print(codex_agy_bridge.server.__file__)"'
    )
    contract = TaskContract(
        task_id="task-caller-env-sentinel-binding",
        objective="Preserve caller env while enforcing controller source binding",
        base_head=head_sha,
        workdir=repo_dir.as_posix(),
        allowed_paths=["README.md"],
        verification_commands=[cmd],
    )

    evidence = run_verification(contract, workdir=repo_dir, env=caller_env)
    assert evidence.passed is True
    assert evidence.provenance_status == "PASS"
    assert evidence.provenance_verified is True
    lines = [line.strip() for line in evidence.commands[0].stdout.splitlines() if line.strip()]
    assert Path(lines[0]).resolve().is_relative_to(target_src)
    assert Path(lines[1]).resolve().is_relative_to(target_src)
    assert foreign_dir.name not in lines[0]
    assert foreign_dir.name not in lines[1]


def test_controller_env_target_provenance_contract(tmp_path: Path) -> None:
    """Positive contract: explicit controller env with target mcp-antigravity-bridge/src resolves under target root, but fails RED if API cannot attest provenance."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    head_sha = _init_git_repo(repo_dir)

    target_src = SRC.resolve()
    env_target = {"PYTHONPATH": str(target_src)}

    cmd = (
        f'"{sys.executable}" -c '
        f'"import codex_agy_bridge, codex_agy_bridge.server; '
        f'print(codex_agy_bridge.__file__); print(codex_agy_bridge.server.__file__)"'
    )

    # 1. Direct cold child subprocess execution resolves both files under target root
    res = execute_verification_command(cmd, cwd=repo_dir, env=env_target)
    assert res.exit_code == 0, f"Child execution failed with stderr: {res.stderr}"

    lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
    assert len(lines) >= 2, f"Expected 2 module lines in stdout, got: {res.stdout!r}"
    pkg_file = Path(lines[0]).resolve()
    server_file = Path(lines[1]).resolve()

    assert pkg_file.is_relative_to(target_src), f"Expected package {pkg_file} to resolve under target root {target_src}"
    assert server_file.is_relative_to(target_src), f"Expected server {server_file} to resolve under target root {target_src}"

    # 2. Verification API attestation contract
    contract = TaskContract(
        task_id="task-provenance-positive-contract",
        objective="Attest target module provenance under clean controller env",
        base_head=head_sha,
        workdir=repo_dir.as_posix(),
        allowed_paths=["README.md"],
        verification_commands=[cmd],
    )

    evidence = run_verification(contract, workdir=repo_dir, env=env_target)
    assert evidence.passed is True

    # RED expectation: current verification API lacks explicit provenance attestation in evidence
    assert "provenance" in evidence.diff_summary or getattr(evidence, "provenance_verified", False) is True, (
        "Current verification API does not attest that child subprocesses resolved modules from target root"
    )
