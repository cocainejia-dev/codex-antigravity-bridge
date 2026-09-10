from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

from codex_agy_bridge.plan_executor import (
    PlanExecutionState,
    PlanExecutor,
    PlanTaskState,
    _git,
    _git_read,
)
from codex_agy_bridge.run_control import WorkerResult
from codex_agy_bridge.task_shaping import shape_task


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "integration"
    repo.mkdir()
    (repo / "README.md").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=repo, check=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    return repo, head


def _plan(repo: Path, head: str, *, failure: bool = False) -> dict:
    return shape_task({
        "plan_id": "plan-test-executor",
        "parent_objective": "serial plan",
        "base_head": head,
        "workdir": str(repo),
        "parent_acceptance": ["a", "b", "c"],
        "objectives": [
            {"id": "O1", "task_id": "t1", "objective": "write a", "acceptance_criteria": ["a"], "covers_acceptance": ["A1"], "allowed_paths": ["a.txt"], "verification_commands": ["python -c \"print('ok')\""]},
            {"id": "O2", "task_id": "t2", "objective": "write b", "acceptance_criteria": ["b"], "covers_acceptance": ["A2"], "allowed_paths": ["b.txt"], "dependencies": ["t1"], "verification_commands": ["python -c \"print('ok')\""]},
            {"id": "O3", "task_id": "t3", "objective": "write c", "acceptance_criteria": ["c"], "covers_acceptance": ["A3"], "allowed_paths": ["c.txt"], "dependencies": ["t2"], "verification_commands": ["python -c \"print('ok')\""]},
        ],
        "final_verification": ["python -c \"import pathlib; assert all(pathlib.Path(x).exists() for x in ['a.txt','b.txt','c.txt'])\""],
        "risk_class": "LOW",
    }).to_dict()


def test_serial_execution_checkpoints_and_rebaseline(tmp_path: Path) -> None:
    repo, head = _repo(tmp_path)
    db = tmp_path / "plan.sqlite3"
    starts: list[str] = []

    def factory(contract):
        def worker(_ctx):
            starts.append(contract.task_id)
            (repo / {"t1": "a.txt", "t2": "b.txt", "t3": "c.txt"}[contract.task_id]).write_text(contract.task_id, encoding="utf-8")
            return WorkerResult(success=True, candidate=True, verification_result={"passed": True}, result_summary=contract.task_id, terminal_reason="COMPLETED")
        return worker

    executor = PlanExecutor(db, worker_factory=factory)
    record = executor.start(_plan(repo, head), integration_worktree=str(repo), initial_base_head=head, execution_id="exec-serial")
    assert record.state == PlanExecutionState.CREATED
    result = executor.wait("exec-serial", timeout=15)
    assert result.state == PlanExecutionState.COMPLETE
    assert starts == ["t1", "t2", "t3"]
    assert result.tasks["t1"]["execution_state"] == PlanTaskState.ACCEPTED.value
    assert result.tasks["t2"]["resolved_base_head"] == result.tasks["t1"]["accepted_checkpoint_sha"]
    assert result.tasks["t3"]["resolved_base_head"] == result.tasks["t2"]["accepted_checkpoint_sha"]
    assert len({task["child_run_id"] for task in result.tasks.values()}) == 3
    assert result.final_verification_result["passed"] is True


def test_failure_blocks_dependents_without_replay(tmp_path: Path) -> None:
    repo, head = _repo(tmp_path)
    db = tmp_path / "plan.sqlite3"
    starts: list[str] = []

    def factory(contract):
        def worker(_ctx):
            starts.append(contract.task_id)
            if contract.task_id == "t2":
                return WorkerResult(success=False, last_error="deliberate failure", terminal_reason="FAILED")
            (repo / {"t1": "a.txt", "t2": "b.txt", "t3": "c.txt"}[contract.task_id]).write_text(contract.task_id, encoding="utf-8")
            return WorkerResult(success=True, candidate=True, verification_result={"passed": True}, result_summary=contract.task_id, terminal_reason="COMPLETED")
        return worker

    executor = PlanExecutor(db, worker_factory=factory)
    executor.start(_plan(repo, head), integration_worktree=str(repo), initial_base_head=head, execution_id="exec-failure")
    result = executor.wait("exec-failure", timeout=15)
    assert result.state == PlanExecutionState.BLOCKED
    assert starts == ["t1", "t2"]
    assert result.tasks["t1"]["execution_state"] == PlanTaskState.ACCEPTED.value
    assert result.tasks["t2"]["execution_state"] == PlanTaskState.FAILED.value
    assert result.tasks["t3"]["execution_state"] == PlanTaskState.PENDING.value
    assert result.current_accepted_head == result.tasks["t1"]["accepted_checkpoint_sha"]


def test_checkpoint_git_timeout_retries_without_replaying_worker(tmp_path: Path, monkeypatch) -> None:
    repo, head = _repo(tmp_path)
    db = tmp_path / "plan.sqlite3"
    starts: list[str] = []
    real_run = subprocess.run
    timed_out = False

    def flaky_run(command, *args, **kwargs):
        nonlocal timed_out
        if (
            not timed_out
            and command[:2] == ["git", "-C"]
            and command[-2:] == ["rev-parse", "HEAD"]
        ):
            timed_out = True
            raise subprocess.TimeoutExpired(command, timeout=15)
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", flaky_run)

    def factory(contract):
        def worker(_ctx):
            starts.append(contract.task_id)
            target = {"t1": "a.txt", "t2": "b.txt", "t3": "c.txt"}[contract.task_id]
            (repo / target).write_text(contract.task_id, encoding="utf-8")
            return WorkerResult(
                success=True,
                candidate=True,
                verification_result={"passed": True},
                result_summary="timeout-retry",
                terminal_reason="COMPLETED",
            )

        return worker

    executor = PlanExecutor(db, worker_factory=factory)
    executor.start(_plan(repo, head), integration_worktree=str(repo), initial_base_head=head, execution_id="exec-timeout-retry")
    result = executor.wait("exec-timeout-retry", timeout=15)
    assert result.state == PlanExecutionState.COMPLETE
    assert starts == ["t1", "t2", "t3"]


def test_git_read_retries_once_then_succeeds(monkeypatch) -> None:
    calls = 0

    def flaky_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise subprocess.TimeoutExpired("git", timeout=15)
        return subprocess.CompletedProcess("git", 0, stdout="abc\n", stderr="")

    monkeypatch.setattr(subprocess, "run", flaky_run)
    assert _git_read("repo", "rev-parse", "HEAD") == "abc"
    assert calls == 2


def test_git_write_does_not_retry_after_timeout(monkeypatch) -> None:
    calls = 0

    def stalled_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise subprocess.TimeoutExpired("git", timeout=15)

    monkeypatch.setattr(subprocess, "run", stalled_run)
    try:
        _git("repo", "add", "--", "file.txt")
    except subprocess.TimeoutExpired:
        pass
    else:
        raise AssertionError("write timeout must be propagated")
    assert calls == 1


def test_git_read_rejects_write_command() -> None:
    try:
        _git_read("repo", "add", "--", "file.txt")
    except ValueError as exc:
        assert "read-only" in str(exc)
    else:
        raise AssertionError("_git_read must reject write commands")


def test_git_read_fails_after_bounded_retry(monkeypatch) -> None:
    calls = 0

    def stalled_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise subprocess.TimeoutExpired("git", timeout=15)

    monkeypatch.setattr(subprocess, "run", stalled_run)
    try:
        _git_read("repo", "status", "--porcelain")
    except subprocess.TimeoutExpired:
        pass
    else:
        raise AssertionError("repeated read timeout must remain a failure")
    assert calls == 2


def test_synthetic_external_worker_serial_plan(tmp_path: Path) -> None:
    """SYNTHETIC: exercise the serial plan with a real local child process per task."""
    repo, head = _repo(tmp_path)
    db = tmp_path / "synthetic-external.sqlite3"
    starts: list[str] = []
    targets = {"t1": "a.txt", "t2": "b.txt", "t3": "c.txt"}

    def factory(contract):
        def worker(_ctx):
            starts.append(contract.task_id)
            target = repo / targets[contract.task_id]
            code = (
                "from pathlib import Path; "
                f"Path({str(target)!r}).write_text({contract.task_id!r}, encoding='utf-8')"
            )
            subprocess.run([sys.executable, "-c", code], check=True, timeout=5)
            return WorkerResult(
                success=True,
                candidate=True,
                verification_result={"passed": True},
                result_summary="synthetic-external-worker",
                terminal_reason="COMPLETED",
            )

        return worker

    executor = PlanExecutor(db, worker_factory=factory)
    executor.start(
        _plan(repo, head),
        integration_worktree=str(repo),
        initial_base_head=head,
        execution_id="exec-synthetic-external",
    )
    result = executor.wait("exec-synthetic-external", timeout=20)

    assert result.state == PlanExecutionState.COMPLETE
    assert starts == ["t1", "t2", "t3"]
    assert result.tasks["t2"]["resolved_base_head"] == result.tasks["t1"]["accepted_checkpoint_sha"]
    assert all((repo / target).exists() for target in targets.values())


def test_start_is_idempotent_and_high_risk_requires_authorization(tmp_path: Path) -> None:
    repo, head = _repo(tmp_path)
    plan = _plan(repo, head)
    executor = PlanExecutor(tmp_path / "plan.sqlite3", worker_factory=lambda _contract: lambda _ctx: WorkerResult(success=False, terminal_reason="FAILED"))
    first = executor.start(plan, integration_worktree=str(repo), initial_base_head=head, execution_id="exec-idem", idempotency_key="same")
    second = executor.start(plan, integration_worktree=str(repo), initial_base_head=head, execution_id="exec-other", idempotency_key="same")
    assert first.plan_execution_id == second.plan_execution_id == "exec-idem"
    high = shape_task({"parent_objective": "high", "base_head": head, "workdir": str(repo), "parent_acceptance": ["x"], "allowed_paths": ["high.txt"], "risk_class": "HIGH"}).to_dict()
    try:
        executor.start(high, integration_worktree=str(repo), initial_base_head=head, execution_id="exec-high")
    except ValueError as exc:
        assert "HIGH_RISK_AUTHORIZATION_REQUIRED" in str(exc)
    else:
        raise AssertionError("high-risk plan started without authorization")


def test_resume_reuses_exact_active_child(tmp_path: Path) -> None:
    repo, head = _repo(tmp_path)
    db = tmp_path / "plan.sqlite3"
    started = []
    entered = threading.Event()
    release = threading.Event()

    def factory(contract):
        def worker(_ctx):
            started.append(contract.task_id)
            entered.set()
            release.wait(5)
            (repo / "a.txt").write_text("t1", encoding="utf-8")
            return WorkerResult(success=True, candidate=True, verification_result={"passed": True}, result_summary="resume", terminal_reason="COMPLETED")
        return worker

    plan = shape_task({"parent_objective": "resume", "base_head": head, "workdir": str(repo), "parent_acceptance": ["a"], "allowed_paths": ["a.txt"], "verification_commands": ["python -c \"print('ok')\""], "final_verification": ["python -c \"assert __import__('pathlib').Path('a.txt').exists()\""]}).to_dict()
    executor = PlanExecutor(db, worker_factory=factory)
    executor.start(plan, integration_worktree=str(repo), initial_base_head=head, execution_id="exec-resume")
    assert entered.wait(5)
    restarted = PlanExecutor(db, worker_factory=factory)
    restarted.resume("exec-resume")
    release.set()
    result = restarted.wait("exec-resume", timeout=15)
    assert result.state == PlanExecutionState.COMPLETE
    assert started == ["task-1"]


def test_permission_mode_is_persisted_and_bound_to_child(tmp_path: Path) -> None:
    repo, head = _repo(tmp_path)
    db = tmp_path / "plan-permissions.sqlite3"

    def factory(contract):
        def worker(_ctx):
            target = {"t1": "a.txt", "t2": "b.txt", "t3": "c.txt"}[contract.task_id]
            (repo / target).write_text("allowed", encoding="utf-8")
            return WorkerResult(
                success=True,
                candidate=True,
                verification_result={"passed": True},
                result_summary="permission-mode",
                terminal_reason="COMPLETED",
            )

        return worker

    executor = PlanExecutor(
        db,
        worker_factory=factory,
        dangerously_skip_permissions=True,
    )
    executor.start(
        _plan(repo, head),
        integration_worktree=str(repo),
        initial_base_head=head,
        execution_id="exec-permissions",
    )
    result = executor.wait("exec-permissions", timeout=15)
    assert result.dangerously_skip_permissions is True
    assert result.state == PlanExecutionState.COMPLETE

    import sqlite3

    with sqlite3.connect(db) as conn:
        identity = json.loads(
            conn.execute(
                "SELECT worker_identity_json FROM runs WHERE run_id=?",
                ("exec-permissions-t1-0",),
            ).fetchone()[0]
        )
    assert identity["dangerously_skip_permissions"] is True
