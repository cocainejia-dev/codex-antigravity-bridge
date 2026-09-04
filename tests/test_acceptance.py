from __future__ import annotations

import subprocess
from pathlib import Path

from codex_agy_bridge.acceptance import (
    NoProgressGuard,
    WorkerTerminalReason,
    audit_candidate_scope,
    capture_baseline_snapshot,
    evaluate_candidate,
    restore_safe_out_of_scope_files,
    should_stop_blind_retry,
)
from codex_agy_bridge.contracts import AcceptanceState, RiskClass, TaskContract


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Acceptance Tests")
    (repo / "package.json").write_text('{"scripts":{"test":"pytest"}}\n', encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "ui.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "baseline")
    return repo


def _contract(repo: Path, baseline, *, risk: RiskClass = RiskClass.LOW) -> TaskContract:
    return TaskContract(
        task_id="acceptance-test",
        objective="bounded candidate",
        base_head=baseline.head,
        workdir=str(repo),
        allowed_paths=["src/ui.py"],
        forbidden_paths=["package.json"],
        acceptance_criteria=["independent test passes"],
        verification_commands=["python -c pass"],
        risk_class=risk,
        baseline_branch=baseline.branch,
        baseline_worktree_status=list(baseline.worktree_status),
        baseline_tracked_diff=list(baseline.tracked_diff),
        baseline_file_hashes=dict(baseline.file_hashes),
    )


def test_allowed_candidate_requires_independent_verification(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    baseline = capture_baseline_snapshot(repo, isolated_worktree=True)
    contract = _contract(repo, baseline)
    (repo / "src" / "ui.py").write_text("VALUE = 2\n", encoding="utf-8")

    audit = audit_candidate_scope(contract, repo, baseline)
    accepted = evaluate_candidate(
        worker_result=WorkerTerminalReason.COMPLETED,
        scope_audit=audit,
        independently_verified=True,
        risk_class=RiskClass.LOW,
    )
    assert audit.passed
    assert accepted.task_accepted
    assert accepted.acceptance == AcceptanceState.ACCEPTED


def test_package_json_regression_is_rejected_and_evidence_retained(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    baseline = capture_baseline_snapshot(repo, isolated_worktree=True)
    contract = _contract(repo, baseline)
    (repo / "src" / "ui.py").write_text("VALUE = 2\n", encoding="utf-8")
    (repo / "package.json").write_text('{"scripts":{}}\n', encoding="utf-8")

    audit = audit_candidate_scope(contract, repo, baseline)
    result = evaluate_candidate(
        worker_result="COMPLETED", scope_audit=audit, independently_verified=True, risk_class=RiskClass.LOW
    )
    assert "package.json" in audit.out_of_scope_files
    assert "package.json" in audit.forbidden_files
    assert not result.task_accepted
    assert result.acceptance == AcceptanceState.CANDIDATE_REJECTED


def test_dirty_baseline_overlap_is_never_auto_restored(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "package.json").write_text('{"scripts":{"test":"pytest","local":"keep"}}\n', encoding="utf-8")
    baseline = capture_baseline_snapshot(repo, isolated_worktree=True)
    contract = _contract(repo, baseline)
    (repo / "package.json").write_text('{"scripts":{"test":"pytest","local":"worker"}}\n', encoding="utf-8")

    audit = audit_candidate_scope(contract, repo, baseline)
    assert audit.baseline_clean is False
    assert "package.json" in audit.baseline_overlap_files
    assert restore_safe_out_of_scope_files(contract, repo, baseline, audit) == ()
    assert "local\":\"worker" in (repo / "package.json").read_text(encoding="utf-8")


def test_isolated_clean_forbidden_file_can_be_exactly_restored_but_violation_remains(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    baseline = capture_baseline_snapshot(repo, isolated_worktree=True)
    contract = _contract(repo, baseline)
    (repo / "package.json").write_text('{"scripts":{"test":"changed"}}\n', encoding="utf-8")

    audit = audit_candidate_scope(contract, repo, baseline)
    restored = restore_safe_out_of_scope_files(contract, repo, baseline, audit)
    assert restored == ("package.json",)
    assert "package.json" in audit.out_of_scope_files
    assert (repo / "package.json").read_text(encoding="utf-8") == '{"scripts":{"test":"pytest"}}\n'


def test_timeout_policy_is_risk_aware_and_bounded_wait_is_not_final(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    baseline = capture_baseline_snapshot(repo, isolated_worktree=True)
    (repo / "src" / "ui.py").write_text("VALUE = 2\n", encoding="utf-8")
    low = evaluate_candidate(
        worker_result=WorkerTerminalReason.HARD_TIMEOUT,
        scope_audit=audit_candidate_scope(_contract(repo, baseline), repo, baseline),
        independently_verified=True,
        risk_class=RiskClass.LOW,
    )
    high = evaluate_candidate(
        worker_result="HARD_TIMEOUT",
        scope_audit=audit_candidate_scope(_contract(repo, baseline, risk=RiskClass.HIGH), repo, baseline),
        independently_verified=True,
        risk_class=RiskClass.HIGH,
    )
    legacy_medium = evaluate_candidate(
        worker_result="HARD_TIMEOUT",
        scope_audit=audit_candidate_scope(_contract(repo, baseline), repo, baseline),
        independently_verified=True,
        risk_class=RiskClass.CODE_CHANGES,
    )
    waiting = evaluate_candidate(
        worker_result="COMPLETED",
        scope_audit=audit_candidate_scope(_contract(repo, baseline), repo, baseline),
        independently_verified=False,
        risk_class=RiskClass.LOW,
        worker_alive=True,
    )
    assert low.task_accepted
    assert low.worker_result == WorkerTerminalReason.HARD_TIMEOUT
    assert high.acceptance == AcceptanceState.CANDIDATE_REJECTED
    assert legacy_medium.risk_class == RiskClass.MEDIUM.value
    assert legacy_medium.acceptance == AcceptanceState.CANDIDATE_REJECTED
    assert not high.task_accepted
    assert waiting.acceptance == AcceptanceState.CANDIDATE_PENDING_REVIEW


def test_worker_reported_pass_does_not_bypass_independent_failure() -> None:
    from codex_agy_bridge.acceptance import ScopeAudit

    result = evaluate_candidate(
        worker_result="COMPLETED",
        scope_audit=ScopeAudit(),
        independently_verified=False,
        risk_class=RiskClass.LOW,
    )
    assert not result.task_accepted
    assert result.acceptance == AcceptanceState.CANDIDATE_PENDING_REVIEW


def test_identical_no_progress_stops_blind_retry() -> None:
    observation = ("test failed", "same diff", "same blocker")
    assert not should_stop_blind_retry([observation])
    assert should_stop_blind_retry([observation, observation])
    guard = NoProgressGuard()
    assert not guard.observe(failure=observation[0], diff=observation[1], blocker=observation[2])
    assert guard.observe(failure=observation[0], diff=observation[1], blocker=observation[2])
    assert guard.identical_no_progress_failure_count == 2
