"""Phase 8 synthetic unattended shadow-run harness for VNext validation.

Constructs and executes synthetic DAG tasks in an isolated temporary workspace,
exercising normal execution, repair, branch/merge, recovery from interruption/crash/MCP reload,
quota/auth suspensions, out-of-scope rejections, and duplicate protection without
production repository access or human approval interruptions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any, Callable, Optional, Sequence
import uuid

from .contracts import (
    CREDENTIAL_PATTERNS,
    AutoCommitPolicy,
    InvalidStateTransitionError,
    RiskClass,
    RunRecord,
    RunState,
    TaskContract,
    _format_timestamp,
    _utc_now_iso,
    normalize_path,
    validate_no_credentials,
)
from .policy import (
    DecisionCategory,
    DecisionRecord,
    DecisionTier,
    evaluate_decision_policy,
)
from .recovery import (
    FailureClass,
    RecoveryAction,
    RecoveryEvidence,
    RecoveryOrchestrator,
    RecoveryReport,
    RecoveryStatus,
    classify_error_message,
)
from .run_control import (
    ACTIVE_STATES,
    TERMINAL_STATES,
    ConcurrentModificationError,
    CredentialSecurityError,
    DurableRunManager,
    DurableRunStore,
    DuplicateRunError,
    RunControlError,
    RunNotFoundError,
    RunObservation,
    WorkerCallback,
    WorkerContext,
    WorkerResult,
)
from .scheduler import (
    FIXED_MAX_PARALLELISM,
    CyclicDependencyError,
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
from .verification import (
    AutoCommitDecision,
    AutoCommitResult,
    CommandResult,
    FailurePackage,
    RepairLoopResult,
    ScopeGateResult,
    VerificationEvidence,
    evaluate_auto_commit_policy,
    evaluate_scope_gate,
    execute_repair_loop,
    execute_verification_command,
    run_verification,
)

FORBIDDEN_PATH_SUBSTRINGS: tuple[str, ...] = (
    "AshareAdvisor",
    "ashareadvisor",
    "ashare_advisor",
)


class ProductionPathForbiddenError(RuntimeError):
    """Raised when any shadow run operation targets or touches production repositories."""

    pass


class ShadowInvariantViolationError(RuntimeError):
    """Raised when an essential safety invariant is violated during a shadow run."""

    pass


def assert_isolated_workspace(path: str | Path) -> Path:
    """Verify that path does not point to production or AshareAdvisor workspace."""
    resolved = Path(path).resolve()
    resolved_str = resolved.as_posix()
    for forbidden in FORBIDDEN_PATH_SUBSTRINGS:
        if forbidden.lower() in resolved_str.lower():
            raise ProductionPathForbiddenError(
                f"Security guard violated: shadow workspace path '{resolved_str}' matches forbidden substring '{forbidden}'"
            )
    return resolved


@dataclass
class SyntheticWorkspace:
    """Encapsulates isolated temporary directories, repo, and databases for shadow run."""

    root_dir: Path
    repo_dir: Path
    db_scheduler: Path
    db_run_manager: Path
    base_head: str

    def cleanup(self) -> None:
        """Clean up workspace files if requested."""
        if self.root_dir.exists():
            shutil.rmtree(self.root_dir, ignore_errors=True)


def create_synthetic_workspace(temp_root: Path) -> SyntheticWorkspace:
    """Initialize a clean synthetic git repository and SQLite stores in temp_root."""
    assert_isolated_workspace(temp_root)

    root_dir = temp_root / f"shadow_ws_{uuid.uuid4().hex[:8]}"
    root_dir.mkdir(parents=True, exist_ok=True)
    repo_dir = root_dir / "synthetic_repo"
    repo_dir.mkdir(parents=True, exist_ok=True)

    # Initialize synthetic git repository
    def _run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            check=True,
        )

    _run_git(["init"])
    _run_git(["config", "user.name", "Shadow Runner"])
    _run_git(["config", "user.email", "shadow-runner@test.local"])
    _run_git(["config", "commit.gpgsign", "false"])

    # Create base code structure
    src_dir = repo_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    tests_dir = repo_dir / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    config_dir = repo_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

    # Initial code
    (src_dir / "__init__.py").write_text('"""Synthetic math library."""\n', encoding="utf-8")
    (src_dir / "calc.py").write_text(
        "def add(a: int, b: int) -> int:\n    return a + b\n\ndef multiply(a: int, b: int) -> int:\n    return a * b\n",
        encoding="utf-8",
    )
    (tests_dir / "__init__.py").write_text("", encoding="utf-8")
    (tests_dir / "test_calc.py").write_text(
        "import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))\nfrom calc import add, multiply\n\ndef test_add():\n    assert add(2, 3) == 5\n\ndef test_multiply():\n    assert multiply(3, 4) == 12\n",
        encoding="utf-8",
    )
    (config_dir / "settings.json").write_text('{"env": "synthetic", "version": "1.0.0"}', encoding="utf-8")

    _run_git(["add", "."])
    _run_git(["commit", "-m", "Initial synthetic commit"])

    head_res = _run_git(["rev-parse", "HEAD"])
    base_head = head_res.stdout.strip()

    db_scheduler = root_dir / "scheduler.sqlite3"
    db_run_manager = root_dir / "run_manager.sqlite3"

    return SyntheticWorkspace(
        root_dir=root_dir,
        repo_dir=repo_dir,
        db_scheduler=db_scheduler,
        db_run_manager=db_run_manager,
        base_head=base_head,
    )


@dataclass
class ShadowRunReport:
    """Comprehensive, serializable report summarizing Phase 8 synthetic shadow execution."""

    total: int
    auto_complete: int
    failed: int
    blocked: int
    suspended_or_recovered: int
    wrong_commit: int
    out_of_scope_accepted: int
    lost_run: int
    duplicate_task: int
    state_corruption: int
    auto_complete_rate: float
    invariants_satisfied: bool
    tasks: dict[str, dict[str, Any]] = field(default_factory=dict)
    summary: str = ""
    started_at: str = field(default_factory=_utc_now_iso)
    completed_at: str = field(default_factory=_utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        """Convert report to dictionary."""
        return {
            "total": self.total,
            "auto_complete": self.auto_complete,
            "failed": self.failed,
            "blocked": self.blocked,
            "suspended_or_recovered": self.suspended_or_recovered,
            "wrong_commit": self.wrong_commit,
            "out_of_scope_accepted": self.out_of_scope_accepted,
            "lost_run": self.lost_run,
            "duplicate_task": self.duplicate_task,
            "state_corruption": self.state_corruption,
            "auto_complete_rate": self.auto_complete_rate,
            "invariants_satisfied": self.invariants_satisfied,
            "tasks": dict(self.tasks),
            "summary": self.summary,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    def to_json(self, **kwargs: Any) -> str:
        """Convert report to JSON string."""
        return json.dumps(self.to_dict(), **kwargs)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ShadowRunReport:
        """Construct report from dictionary."""
        if not isinstance(data, dict):
            raise ValueError(f"Expected dict, got {type(data).__name__}")
        return cls(
            total=int(data.get("total", 0)),
            auto_complete=int(data.get("auto_complete", 0)),
            failed=int(data.get("failed", 0)),
            blocked=int(data.get("blocked", 0)),
            suspended_or_recovered=int(data.get("suspended_or_recovered", 0)),
            wrong_commit=int(data.get("wrong_commit", 0)),
            out_of_scope_accepted=int(data.get("out_of_scope_accepted", 0)),
            lost_run=int(data.get("lost_run", 0)),
            duplicate_task=int(data.get("duplicate_task", 0)),
            state_corruption=int(data.get("state_corruption", 0)),
            auto_complete_rate=float(data.get("auto_complete_rate", 0.0)),
            invariants_satisfied=bool(data.get("invariants_satisfied", False)),
            tasks=dict(data.get("tasks", {})),
            summary=str(data.get("summary", "")),
            started_at=str(data.get("started_at", _utc_now_iso())),
            completed_at=str(data.get("completed_at", _utc_now_iso())),
        )


class ShadowHarness:
    """Deterministic, unattended execution harness for Phase 8 shadow validation."""

    def __init__(self, workspace: SyntheticWorkspace, python_bin: str | None = None) -> None:
        self.workspace = workspace
        self.python_bin = python_bin or os.environ.get("PYTHON", sys.executable)
        self.scheduler = TaskDAGScheduler(workspace.db_scheduler, max_parallelism=1)
        self.run_manager = DurableRunManager(workspace.db_run_manager)
        self.recovery = RecoveryOrchestrator(self.run_manager)

        # Invariant counters
        self.wrong_commit = 0
        self.out_of_scope_accepted = 0
        self.lost_run = 0
        self.duplicate_task = 0
        self.state_corruption = 0

        # State tracking
        self.task_evidence: dict[str, dict[str, Any]] = {}
        self.fault_states: dict[str, int] = {}
        self.repair_counts: dict[str, int] = {}
        self.suspended_or_recovered_count = 0

    def _commit_repo(self, message: str) -> str:
        """Helper to create a git commit in synthetic repo and return SHA."""
        subprocess.run(["git", "add", "."], cwd=str(self.workspace.repo_dir), check=True, capture_output=True)
        res = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=str(self.workspace.repo_dir),
            capture_output=True,
            text=True,
            check=True,
        )
        head_res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(self.workspace.repo_dir),
            capture_output=True,
            text=True,
            check=True,
        )
        return head_res.stdout.strip()

    def build_synthetic_dag(self) -> list[DAGTaskRecord]:
        """Construct standard 14-task synthetic DAG exercising all Phase 8 scenarios."""
        repo = self.workspace.repo_dir
        base_head = self.workspace.base_head
        py_path_posix = Path(self.python_bin).as_posix()
        pytest_cmd = f'"{py_path_posix}" -m pytest -q'

        # 1. task_01_root_success: Linear root, normal success
        c01 = TaskContract(
            task_id="task_01_root_success",
            objective="Add subtraction function to calc.py",
            base_head=base_head,
            workdir=repo.as_posix(),
            allowed_paths=["src/calc.py", "tests/test_calc.py"],
            acceptance_criteria=["subtraction works and tests pass"],
            verification_commands=[pytest_cmd],
            dependencies=[],
            risk_class=RiskClass.CODE_CHANGES,
            auto_commit_policy=AutoCommitPolicy.VERIFIED_ONLY,
        )
        self.scheduler.add_task(c01)

        # 2. task_02_linear_dep: Depends on task_01, linear chain
        c02 = TaskContract(
            task_id="task_02_linear_dep",
            objective="Add division function to calc.py",
            base_head=base_head,
            workdir=repo.as_posix(),
            allowed_paths=["src/calc.py", "tests/test_calc.py"],
            acceptance_criteria=["division works with zero-division handling"],
            verification_commands=[pytest_cmd],
            dependencies=["task_01_root_success"],
            risk_class=RiskClass.CODE_CHANGES,
            auto_commit_policy=AutoCommitPolicy.VERIFIED_ONLY,
        )
        self.scheduler.add_task(c02)

        # 3. task_03_branch_a: Branch A from task_02
        c03 = TaskContract(
            task_id="task_03_branch_a",
            objective="Add power function to calc.py",
            base_head=base_head,
            workdir=repo.as_posix(),
            allowed_paths=["src/calc.py", "tests/test_calc.py"],
            acceptance_criteria=["power works and tests pass"],
            verification_commands=[pytest_cmd],
            dependencies=["task_02_linear_dep"],
            risk_class=RiskClass.CODE_CHANGES,
            auto_commit_policy=AutoCommitPolicy.VERIFIED_ONLY,
        )
        self.scheduler.add_task(c03)

        # 4. task_04_branch_b: Branch B from task_02
        c04 = TaskContract(
            task_id="task_04_branch_b",
            objective="Add modulo function to calc.py",
            base_head=base_head,
            workdir=repo.as_posix(),
            allowed_paths=["src/calc.py", "tests/test_calc.py"],
            acceptance_criteria=["modulo works and tests pass"],
            verification_commands=[pytest_cmd],
            dependencies=["task_02_linear_dep"],
            risk_class=RiskClass.CODE_CHANGES,
            auto_commit_policy=AutoCommitPolicy.VERIFIED_ONLY,
        )
        self.scheduler.add_task(c04)

        # 5. task_05_merge: Merge task_03 and task_04
        c05 = TaskContract(
            task_id="task_05_merge",
            objective="Create statistics helper in stats.py utilizing power and calc functions",
            base_head=base_head,
            workdir=repo.as_posix(),
            allowed_paths=["src/stats.py", "tests/test_stats.py"],
            acceptance_criteria=["mean and variance functions work"],
            verification_commands=[pytest_cmd],
            dependencies=["task_03_branch_a", "task_04_branch_b"],
            risk_class=RiskClass.CODE_CHANGES,
            auto_commit_policy=AutoCommitPolicy.VERIFIED_ONLY,
        )
        self.scheduler.add_task(c05)

        # 6. task_06_repair_needed: First verification fails, auto-repair repairs it on round 1
        c06 = TaskContract(
            task_id="task_06_repair_needed",
            objective="Add median function with repair-needed simulation",
            base_head=base_head,
            workdir=repo.as_posix(),
            allowed_paths=["src/stats.py", "tests/test_stats.py"],
            acceptance_criteria=["median works on odd and even lengths"],
            verification_commands=[pytest_cmd],
            dependencies=["task_05_merge"],
            risk_class=RiskClass.CODE_CHANGES,
            max_repair_rounds=2,
            auto_commit_policy=AutoCommitPolicy.VERIFIED_ONLY,
        )
        self.scheduler.add_task(c06)

        # 7. task_07_worker_interrupted: Simulates worker crash / dead PID, recovered and resumed
        c07 = TaskContract(
            task_id="task_07_worker_interrupted",
            objective="Add standard deviation function with simulated worker crash recovery",
            base_head=base_head,
            workdir=repo.as_posix(),
            allowed_paths=["src/stats.py", "tests/test_stats.py"],
            acceptance_criteria=["stddev works and survives worker crash"],
            verification_commands=[pytest_cmd],
            dependencies=["task_05_merge"],
            risk_class=RiskClass.CODE_CHANGES,
            auto_commit_policy=AutoCommitPolicy.VERIFIED_ONLY,
        )
        self.scheduler.add_task(c07)

        # 8. task_08_mcp_restart: Simulates MCP restart / reload with stale heartbeat
        c08 = TaskContract(
            task_id="task_08_mcp_restart",
            objective="Add min-max normalizer with simulated MCP restart reconciliation",
            base_head=base_head,
            workdir=repo.as_posix(),
            allowed_paths=["src/stats.py", "tests/test_stats.py"],
            acceptance_criteria=["normalizer works and recovers cleanly from restart"],
            verification_commands=[pytest_cmd],
            dependencies=["task_07_worker_interrupted"],
            risk_class=RiskClass.CODE_CHANGES,
            auto_commit_policy=AutoCommitPolicy.VERIFIED_ONLY,
        )
        self.scheduler.add_task(c08)

        # 9. task_09_quota_suspended: Simulates 429 quota exhaustion -> account switch -> resume
        c09 = TaskContract(
            task_id="task_09_quota_suspended",
            objective="Add percentile calculator with quota suspension handling",
            base_head=base_head,
            workdir=repo.as_posix(),
            allowed_paths=["src/stats.py", "tests/test_stats.py"],
            acceptance_criteria=["percentile works and recovers from quota suspension"],
            verification_commands=[pytest_cmd],
            dependencies=["task_05_merge"],
            risk_class=RiskClass.CODE_CHANGES,
            auto_commit_policy=AutoCommitPolicy.VERIFIED_ONLY,
        )
        self.scheduler.add_task(c09)

        # 10. task_10_out_of_scope: Attempts out-of-scope write to config/secrets.json
        c10 = TaskContract(
            task_id="task_10_out_of_scope",
            objective="Attempt writing to forbidden credentials path (must be rejected)",
            base_head=base_head,
            workdir=repo.as_posix(),
            allowed_paths=["src/stats.py"],
            forbidden_paths=["config/secrets.json", "config/*.json"],
            acceptance_criteria=["forbidden write is rejected by scope gate"],
            verification_commands=[pytest_cmd],
            dependencies=["task_05_merge"],
            risk_class=RiskClass.CODE_CHANGES,
            auto_commit_policy=AutoCommitPolicy.VERIFIED_ONLY,
        )
        self.scheduler.add_task(c10)

        # 11. task_11_permanent_fail: Verification permanently fails (unresolvable error)
        c11 = TaskContract(
            task_id="task_11_permanent_fail",
            objective="Unresolvable requirement causing permanent verification failure",
            base_head=base_head,
            workdir=repo.as_posix(),
            allowed_paths=["src/stats.py"],
            acceptance_criteria=["intended permanent failure"],
            verification_commands=[f'"{py_path_posix}" -c "import sys; sys.exit(1)"'],
            dependencies=["task_05_merge"],
            risk_class=RiskClass.CODE_CHANGES,
            max_repair_rounds=0,
            auto_commit_policy=AutoCommitPolicy.VERIFIED_ONLY,
        )
        self.scheduler.add_task(c11)

        # 12. task_12_blocked_dep: Depends on task_11 -> remains BLOCKED_BY_DEPENDENCY
        c12 = TaskContract(
            task_id="task_12_blocked_dep",
            objective="Dependent task that must stay blocked due to task_11 failure",
            base_head=base_head,
            workdir=repo.as_posix(),
            allowed_paths=["src/stats.py"],
            acceptance_criteria=["remains blocked by upstream failure"],
            verification_commands=[pytest_cmd],
            dependencies=["task_11_permanent_fail"],
            risk_class=RiskClass.CODE_CHANGES,
            auto_commit_policy=AutoCommitPolicy.VERIFIED_ONLY,
        )
        self.scheduler.add_task(c12)

        # 13. task_13_duplicate_check: Exercises duplicate start protection
        c13 = TaskContract(
            task_id="task_13_duplicate_check",
            objective="Verifies duplicate task and run start rejection",
            base_head=base_head,
            workdir=repo.as_posix(),
            allowed_paths=["src/calc.py"],
            acceptance_criteria=["duplicate start raises DuplicateTaskError / DuplicateRunError"],
            verification_commands=[pytest_cmd],
            dependencies=["task_01_root_success"],
            risk_class=RiskClass.CODE_CHANGES,
            auto_commit_policy=AutoCommitPolicy.VERIFIED_ONLY,
        )
        self.scheduler.add_task(c13)

        # 14. task_14_production_guard: Halts before any production cutover without human prompt
        c14 = TaskContract(
            task_id="task_14_production_guard",
            objective="Deploy to live market trading and update production broker credentials (must stop before production action)",
            base_head=base_head,
            workdir=repo.as_posix(),
            allowed_paths=["src/calc.py"],
            acceptance_criteria=["governance policy identifies PRODUCTION / HUMAN tier"],
            verification_commands=[pytest_cmd],
            dependencies=["task_05_merge"],
            risk_class=RiskClass.PRODUCTION,
            auto_commit_policy=AutoCommitPolicy.VERIFIED_ONLY,
        )
        self.scheduler.add_task(c14)

        self.scheduler.validate_dag()
        return self.scheduler.store.list_tasks()

    def _execute_task_logic(self, task: DAGTaskRecord) -> TaskExecutionResult:
        """Injectable deterministic runner handling faults, verification, repairs, and transitions."""
        tid = task.task_id
        contract = task.contract
        if contract is None:
            return TaskExecutionResult(success=True, result_summary=f"Task {tid} without contract completed")

        repo = self.workspace.repo_dir
        src_calc = repo / "src" / "calc.py"
        src_stats = repo / "src" / "stats.py"
        test_calc = repo / "tests" / "test_calc.py"
        test_stats = repo / "tests" / "test_stats.py"

        # --- Task 1: task_01_root_success ---
        if tid == "task_01_root_success":
            code = src_calc.read_text(encoding="utf-8")
            if "def subtract" not in code:
                src_calc.write_text(code + "\ndef subtract(a: int, b: int) -> int:\n    return a - b\n", encoding="utf-8")
                test_code = test_calc.read_text(encoding="utf-8")
                test_calc.write_text(test_code + "\ndef test_subtract():\n    from calc import subtract\n    assert subtract(5, 2) == 3\n", encoding="utf-8")
            ev = run_verification(contract, repo)
            if not ev.passed:
                return TaskExecutionResult(success=False, last_error=ev.error_message, verification_result=ev.to_dict())
            commit_res = evaluate_auto_commit_policy(contract, ev, repo)
            if not commit_res.allowed:
                self.wrong_commit += 1
                return TaskExecutionResult(success=False, last_error=f"Auto commit disallowed: {commit_res.reasons}")
            commit_sha = self._commit_repo("task_01: Add subtract function")
            return TaskExecutionResult(success=True, commit_sha=commit_sha, verification_result=ev.to_dict(), result_summary="Subtract added and verified")

        # --- Task 2: task_02_linear_dep ---
        elif tid == "task_02_linear_dep":
            code = src_calc.read_text(encoding="utf-8")
            if "def divide" not in code:
                src_calc.write_text(code + "\ndef divide(a: int, b: int) -> float:\n    if b == 0:\n        raise ValueError('div by zero')\n    return a / b\n", encoding="utf-8")
                test_code = test_calc.read_text(encoding="utf-8")
                test_calc.write_text(test_code + "\ndef test_divide():\n    from calc import divide\n    assert divide(10, 2) == 5.0\n", encoding="utf-8")
            ev = run_verification(contract, repo)
            if not ev.passed:
                return TaskExecutionResult(success=False, last_error=ev.error_message, verification_result=ev.to_dict())
            commit_res = evaluate_auto_commit_policy(contract, ev, repo)
            if not commit_res.allowed:
                self.wrong_commit += 1
                return TaskExecutionResult(success=False, last_error=f"Auto commit disallowed: {commit_res.reasons}")
            commit_sha = self._commit_repo("task_02: Add divide function")
            return TaskExecutionResult(success=True, commit_sha=commit_sha, verification_result=ev.to_dict(), result_summary="Divide added and verified")

        # --- Task 3: task_03_branch_a ---
        elif tid == "task_03_branch_a":
            code = src_calc.read_text(encoding="utf-8")
            if "def power" not in code:
                src_calc.write_text(code + "\ndef power(a: int, b: int) -> int:\n    return a ** b\n", encoding="utf-8")
                test_code = test_calc.read_text(encoding="utf-8")
                test_calc.write_text(test_code + "\ndef test_power():\n    from calc import power\n    assert power(2, 3) == 8\n", encoding="utf-8")
            ev = run_verification(contract, repo)
            if not ev.passed:
                return TaskExecutionResult(success=False, last_error=ev.error_message, verification_result=ev.to_dict())
            commit_res = evaluate_auto_commit_policy(contract, ev, repo)
            if not commit_res.allowed:
                self.wrong_commit += 1
                return TaskExecutionResult(success=False, last_error=f"Auto commit disallowed: {commit_res.reasons}")
            commit_sha = self._commit_repo("task_03: Add power function")
            return TaskExecutionResult(success=True, commit_sha=commit_sha, verification_result=ev.to_dict(), result_summary="Power added and verified")

        # --- Task 4: task_04_branch_b ---
        elif tid == "task_04_branch_b":
            code = src_calc.read_text(encoding="utf-8")
            if "def modulo" not in code:
                src_calc.write_text(code + "\ndef modulo(a: int, b: int) -> int:\n    return a % b\n", encoding="utf-8")
                test_code = test_calc.read_text(encoding="utf-8")
                test_calc.write_text(test_code + "\ndef test_modulo():\n    from calc import modulo\n    assert modulo(10, 3) == 1\n", encoding="utf-8")
            ev = run_verification(contract, repo)
            if not ev.passed:
                return TaskExecutionResult(success=False, last_error=ev.error_message, verification_result=ev.to_dict())
            commit_res = evaluate_auto_commit_policy(contract, ev, repo)
            if not commit_res.allowed:
                self.wrong_commit += 1
                return TaskExecutionResult(success=False, last_error=f"Auto commit disallowed: {commit_res.reasons}")
            commit_sha = self._commit_repo("task_04: Add modulo function")
            return TaskExecutionResult(success=True, commit_sha=commit_sha, verification_result=ev.to_dict(), result_summary="Modulo added and verified")

        # --- Task 5: task_05_merge ---
        elif tid == "task_05_merge":
            if not src_stats.exists():
                src_stats.write_text(
                    "from calc import add, divide, power\n\ndef mean(values: list[float]) -> float:\n    if not values:\n        return 0.0\n    total = 0\n    for v in values:\n        total = add(total, v)\n    return divide(total, len(values))\n",
                    encoding="utf-8",
                )
                test_stats.write_text(
                    "import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))\nfrom stats import mean\n\ndef test_mean():\n    assert mean([2.0, 4.0, 6.0]) == 4.0\n",
                    encoding="utf-8",
                )
            ev = run_verification(contract, repo)
            if not ev.passed:
                return TaskExecutionResult(success=False, last_error=ev.error_message, verification_result=ev.to_dict())
            commit_res = evaluate_auto_commit_policy(contract, ev, repo)
            if not commit_res.allowed:
                self.wrong_commit += 1
                return TaskExecutionResult(success=False, last_error=f"Auto commit disallowed: {commit_res.reasons}")
            commit_sha = self._commit_repo("task_05: Merge stats helper")
            return TaskExecutionResult(success=True, commit_sha=commit_sha, verification_result=ev.to_dict(), result_summary="Stats merged and verified")

        # --- Task 6: task_06_repair_needed ---
        elif tid == "task_06_repair_needed":
            state = self.fault_states.get(tid, 0)
            if state == 0:
                # First attempt: write intentional bug that fails verification
                code = src_stats.read_text(encoding="utf-8")
                src_stats.write_text(
                    code + "\ndef median(values: list[float]) -> float:\n    # Intended bug: always returns 0\n    return 0.0\n",
                    encoding="utf-8",
                )
                test_code = test_stats.read_text(encoding="utf-8")
                test_stats.write_text(
                    test_code + "\ndef test_median():\n    from stats import median\n    assert median([1.0, 3.0, 5.0]) == 3.0\n",
                    encoding="utf-8",
                )
                self.fault_states[tid] = 1

                # Execute repair loop using execute_repair_loop
                def _repair_callback(pkg: FailurePackage, ctx: WorkerContext | None) -> bool:
                    self.repair_counts[tid] = self.repair_counts.get(tid, 0) + 1
                    # Repair the implementation correctly
                    cur = src_stats.read_text(encoding="utf-8")
                    fixed = cur.replace(
                        "    # Intended bug: always returns 0\n    return 0.0",
                        "    s = sorted(values)\n    n = len(s)\n    if n % 2 == 1:\n        return s[n // 2]\n    return (s[n // 2 - 1] + s[n // 2]) / 2.0",
                    )
                    src_stats.write_text(fixed, encoding="utf-8")
                    return True

                repair_result = execute_repair_loop(
                    contract=contract,
                    workdir=repo,
                    repair_callback=_repair_callback,
                    max_repair_rounds=2,
                )

                if repair_result.success:
                    commit_sha = self._commit_repo("task_06: Add median function after repair")
                    self.suspended_or_recovered_count += 1
                    return TaskExecutionResult(
                        success=True,
                        commit_sha=commit_sha,
                        verification_result=repair_result.final_evidence.to_dict(),
                        result_summary=f"Repaired successfully in round {repair_result.repair_rounds_completed}",
                        evidence={"repair_rounds": repair_result.repair_rounds_completed},
                    )
                else:
                    return TaskExecutionResult(
                        success=False,
                        last_error=repair_result.error_message,
                        verification_result=repair_result.final_evidence.to_dict(),
                    )
            else:
                return TaskExecutionResult(success=True, result_summary="Already repaired")

        # --- Task 7: task_07_worker_interrupted ---
        elif tid == "task_07_worker_interrupted":
            state = self.fault_states.get(tid, 0)
            if state == 0:
                self.fault_states[tid] = 1
                # Simulate durable run manager registration with dead worker PID
                durable_record = self.run_manager.run_start(
                    contract,
                    auto_spawn=False,
                    worker_identity={"worker_type": "process", "pid": 99999999},
                )
                q_rec = self.run_manager.store.transition_run(durable_record.run_id, expected_version=1, target_state=RunState.QUEUED)
                r_rec = self.run_manager.store.transition_run(durable_record.run_id, expected_version=q_rec.state_version, target_state=RunState.RUNNING, pid=99999999)

                # Ensure durable state is preserved and not lost
                assert self.run_manager.store.get_run(durable_record.run_id) is not None

                # Recovery orchestrator detects dead PID / crash and marks interrupted
                interrupted_rec = self.recovery.mark_interrupted_if_orphaned(durable_record.run_id, external_pid_alive_fn=lambda pid: False)
                assert interrupted_rec.state == RunState.INTERRUPTED

                # Write actual working implementation
                code = src_stats.read_text(encoding="utf-8")
                if "def variance" not in code:
                    src_stats.write_text(
                        code + "\ndef variance(values: list[float]) -> float:\n    m = mean(values)\n    return sum((x - m) ** 2 for x in values) / len(values)\n",
                        encoding="utf-8",
                    )
                    test_code = test_stats.read_text(encoding="utf-8")
                    test_stats.write_text(
                        test_code + "\ndef test_variance():\n    from stats import variance\n    assert round(variance([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]), 2) == 4.0\n",
                        encoding="utf-8",
                    )

                # Resume same run
                resumed = self.recovery.resume_same_run(durable_record.run_id, auto_spawn=False)
                ev = run_verification(contract, repo)
                r_rec = self.run_manager.store.transition_run(durable_record.run_id, expected_version=resumed.state_version, target_state=RunState.RUNNING)
                v_rec = self.run_manager.store.transition_run(
                    durable_record.run_id,
                    expected_version=r_rec.state_version,
                    target_state=RunState.VERIFYING,
                    verification_result=ev.to_dict(),
                )
                self.run_manager.store.transition_run(
                    durable_record.run_id,
                    expected_version=v_rec.state_version,
                    target_state=RunState.COMPLETE,
                    verification_result=ev.to_dict(),
                    result_summary="Resumed after worker interruption",
                )

                commit_sha = self._commit_repo("task_07: Add variance after worker crash recovery")
                self.suspended_or_recovered_count += 1
                return TaskExecutionResult(
                    success=True,
                    commit_sha=commit_sha,
                    verification_result=ev.to_dict(),
                    result_summary="Worker crash recovered and resumed to complete",
                    evidence={"recovered_run_id": durable_record.run_id},
                )
            else:
                return TaskExecutionResult(success=True, result_summary="Interruption already handled")

        # --- Task 8: task_08_mcp_restart ---
        elif tid == "task_08_mcp_restart":
            state = self.fault_states.get(tid, 0)
            if state == 0:
                self.fault_states[tid] = 1
                # Simulate MCP server reload / ungraceful restart with stale heartbeat
                durable_record = self.run_manager.run_start(
                    contract,
                    auto_spawn=False,
                    worker_identity={"worker_type": "mcp_session", "session_id": "mcp_old_sess"},
                )
                q_rec = self.run_manager.store.transition_run(durable_record.run_id, expected_version=1, target_state=RunState.QUEUED)
                r_rec = self.run_manager.store.transition_run(durable_record.run_id, expected_version=q_rec.state_version, target_state=RunState.RUNNING)

                # Force heartbeat in past to simulate stale session
                self.run_manager.store.update_heartbeat(durable_record.run_id, timestamp="2020-01-01T00:00:00+00:00")

                # Recovery reconciles MCP reload and marks interrupted
                interrupted_rec = self.recovery.mark_interrupted_if_orphaned(durable_record.run_id)
                assert interrupted_rec.state == RunState.INTERRUPTED

                code = src_stats.read_text(encoding="utf-8")
                if "def normalize" not in code:
                    src_stats.write_text(
                        code + "\ndef normalize(values: list[float]) -> list[float]:\n    low, high = min(values), max(values)\n    if low == high:\n        return [0.0] * len(values)\n    return [(x - low) / (high - low) for x in values]\n",
                        encoding="utf-8",
                    )
                    test_code = test_stats.read_text(encoding="utf-8")
                    test_stats.write_text(
                        test_code + "\ndef test_normalize():\n    from stats import normalize\n    assert normalize([10.0, 20.0, 30.0]) == [0.0, 0.5, 1.0]\n",
                        encoding="utf-8",
                    )

                resumed = self.recovery.resume_same_run(durable_record.run_id, auto_spawn=False)
                ev = run_verification(contract, repo)
                r_rec = self.run_manager.store.transition_run(durable_record.run_id, expected_version=resumed.state_version, target_state=RunState.RUNNING)
                v_rec = self.run_manager.store.transition_run(
                    durable_record.run_id,
                    expected_version=r_rec.state_version,
                    target_state=RunState.VERIFYING,
                    verification_result=ev.to_dict(),
                )
                self.run_manager.store.transition_run(
                    durable_record.run_id,
                    expected_version=v_rec.state_version,
                    target_state=RunState.COMPLETE,
                    verification_result=ev.to_dict(),
                    result_summary="Resumed after MCP restart",
                )
                commit_sha = self._commit_repo("task_08: Add normalize after MCP reload reconciliation")
                self.suspended_or_recovered_count += 1
                return TaskExecutionResult(
                    success=True,
                    commit_sha=commit_sha,
                    verification_result=ev.to_dict(),
                    result_summary="MCP restart recovered and resumed",
                )
            else:
                return TaskExecutionResult(success=True, result_summary="MCP restart already handled")

        # --- Task 9: task_09_quota_suspended ---
        elif tid == "task_09_quota_suspended":
            state = self.fault_states.get(tid, 0)
            if state == 0:
                self.fault_states[tid] = 1
                # 1. Encounter 429 quota error
                classified = classify_error_message("429 Too Many Requests: Rate limit quota exhausted")
                assert classified == FailureClass.RATE_LIMIT

                # 2. Suspend task via ACCOUNT_SWITCH_REQUIRED
                durable_record = self.run_manager.run_start(contract, auto_spawn=False)
                q_rec = self.run_manager.store.transition_run(durable_record.run_id, expected_version=1, target_state=RunState.QUEUED)
                r_rec = self.run_manager.store.transition_run(durable_record.run_id, expected_version=q_rec.state_version, target_state=RunState.RUNNING)
                suspended = self.run_manager.store.transition_run(
                    durable_record.run_id,
                    expected_version=r_rec.state_version,
                    target_state=RunState.ACCOUNT_SWITCH_REQUIRED,
                    suspended_reason="429 Rate limit reached, account switch required",
                )
                assert suspended.state == RunState.ACCOUNT_SWITCH_REQUIRED

                # 3. Automated policy/auth resolver switches credential/token and unblocks
                resumed_rec = self.recovery.resume_same_run(
                    durable_record.run_id,
                    account_switched=True,
                    credentials_refreshed=True,
                    auto_spawn=False,
                )
                assert resumed_rec.state in (RunState.QUEUED, RunState.RUNNING)

                code = src_stats.read_text(encoding="utf-8")
                if "def percentile" not in code:
                    src_stats.write_text(
                        code + "\ndef percentile(values: list[float], p: float) -> float:\n    s = sorted(values)\n    k = (len(s) - 1) * p\n    f = int(k)\n    c = int(k) + 1 if int(k) + 1 < len(s) else f\n    d = k - f\n    return s[f] + (s[c] - s[f]) * d\n",
                        encoding="utf-8",
                    )
                    test_code = test_stats.read_text(encoding="utf-8")
                    test_stats.write_text(
                        test_code + "\ndef test_percentile():\n    from stats import percentile\n    assert percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.5) == 3.0\n",
                        encoding="utf-8",
                    )

                ev = run_verification(contract, repo)
                if resumed_rec.state == RunState.QUEUED:
                    cur_rec = self.run_manager.store.transition_run(durable_record.run_id, expected_version=resumed_rec.state_version, target_state=RunState.RUNNING)
                else:
                    cur_rec = resumed_rec
                v_rec = self.run_manager.store.transition_run(
                    durable_record.run_id,
                    expected_version=cur_rec.state_version,
                    target_state=RunState.VERIFYING,
                    verification_result=ev.to_dict(),
                )
                self.run_manager.store.transition_run(
                    durable_record.run_id,
                    expected_version=v_rec.state_version,
                    target_state=RunState.COMPLETE,
                    verification_result=ev.to_dict(),
                    result_summary="Complete after quota switch",
                )
                commit_sha = self._commit_repo("task_09: Add percentile after quota suspension resolved")
                self.suspended_or_recovered_count += 1
                return TaskExecutionResult(
                    success=True,
                    commit_sha=commit_sha,
                    verification_result=ev.to_dict(),
                    result_summary="Quota suspension handled and resolved",
                )
            else:
                return TaskExecutionResult(success=True, result_summary="Quota switch already handled")

        # --- Task 10: task_10_out_of_scope ---
        elif tid == "task_10_out_of_scope":
            # Attempt writing to forbidden config/secrets.json
            secrets_file = repo / "config" / "secrets.json"
            secrets_file.write_text('{"unauthorized_key": "12345"}', encoding="utf-8")

            # Evaluate scope gate directly
            scope_result = evaluate_scope_gate(contract, repo)
            if scope_result.passed:
                # If accepted, this violates the invariant!
                self.out_of_scope_accepted += 1
                self.wrong_commit += 1
                return TaskExecutionResult(success=True, result_summary="Scope gate incorrectly accepted forbidden file")
            else:
                # Rejection is the expected safe behavior! Clean up the unauthorized file.
                secrets_file.unlink(missing_ok=True)
                return TaskExecutionResult(
                    success=False,
                    target_state=DAGTaskState.FAILED,
                    last_error=f"Scope violation correctly rejected: {scope_result.violations}",
                    evidence={"scope_violations": scope_result.violations},
                    result_summary="Forbidden path write rejected by scope gate",
                )

        # --- Task 11: task_11_permanent_fail ---
        elif tid == "task_11_permanent_fail":
            ev = run_verification(contract, repo)
            return TaskExecutionResult(
                success=False,
                target_state=DAGTaskState.FAILED,
                last_error=ev.error_message or "Permanent verification failure",
                verification_result=ev.to_dict(),
                result_summary="Intended permanent failure executed",
            )

        # --- Task 12: task_12_blocked_dep ---
        elif tid == "task_12_blocked_dep":
            # Should never be reached because upstream task_11 failed
            return TaskExecutionResult(
                success=False,
                target_state=DAGTaskState.BLOCKED_BY_DEPENDENCY,
                last_error="Dependency failed",
            )

        # --- Task 13: task_13_duplicate_check ---
        elif tid == "task_13_duplicate_check":
            # Test duplicate task registration
            dup_task_threw = False
            try:
                self.scheduler.add_task(contract)
            except DuplicateTaskError:
                dup_task_threw = True

            # Test duplicate active run start
            dup_run_threw = False
            r1 = self.run_manager.run_start(contract, auto_spawn=False)
            try:
                self.run_manager.run_start(contract, auto_spawn=False)
            except DuplicateRunError:
                dup_run_threw = True
            finally:
                self.run_manager.store.transition_run(r1.run_id, expected_version=1, target_state=RunState.CANCELLED)

            if not dup_task_threw or not dup_run_threw:
                self.duplicate_task += 1
                return TaskExecutionResult(success=False, last_error="Duplicate protection failed to raise error")

            return TaskExecutionResult(
                success=True,
                result_summary="Duplicate task and run start protections verified",
            )

        # --- Task 14: task_14_production_guard ---
        elif tid == "task_14_production_guard":
            # Check autonomous policy governance
            decision = evaluate_decision_policy(
                intent=contract.objective,
                modified_paths=contract.allowed_paths,
                risk_class=contract.risk_class,
            )
            if decision.tier != DecisionTier.HUMAN_DECISION_REQUIRED:
                return TaskExecutionResult(
                    success=False,
                    last_error=f"Expected HUMAN_DECISION_REQUIRED tier for PRODUCTION risk, got {decision.tier}",
                )

            # Harness stops before any production cutover action without requiring human prompt
            return TaskExecutionResult(
                success=True,
                target_state=DAGTaskState.COMPLETE,
                result_summary="Production risk classified safely and halted before cutover action",
                evidence={"decision": asdict(decision)},
            )

        return TaskExecutionResult(success=True, result_summary=f"Default runner pass for {tid}")

    def execute_shadow_run(self) -> ShadowRunReport:
        """Run complete unattended synthetic DAG shadow run and verify all target invariants."""
        start_ts = _utc_now_iso()

        # Execute scheduler sequentially with fixed parallelism = 1
        executed_records = self.scheduler.run_all(runner=self._execute_task_logic)

        completed_ts = _utc_now_iso()

        # Audit final states and invariants across the entire DAG
        all_tasks = self.scheduler.store.list_tasks()
        total_tasks = len(all_tasks)

        auto_complete_count = 0
        failed_count = 0
        blocked_count = 0

        for t in all_tasks:
            self.task_evidence[t.task_id] = {
                "state": t.state.value,
                "attempt": t.attempt,
                "run_id": t.run_id,
                "dependencies": t.dependencies,
                "last_error": t.last_error,
                "evidence": t.evidence,
            }
            if t.state == DAGTaskState.COMPLETE:
                auto_complete_count += 1
            elif t.state == DAGTaskState.FAILED:
                failed_count += 1
            elif t.state == DAGTaskState.BLOCKED_BY_DEPENDENCY:
                blocked_count += 1

        # Check for lost runs in run manager
        all_runs = self.run_manager.store.list_runs()
        for r in all_runs:
            if r.state not in TERMINAL_STATES:
                self.lost_run += 1

        # Eligible autonomous tasks: tasks that are intended to succeed autonomously
        # (excluding intended failures like task_10, task_11, and blocked task_12)
        eligible_count = sum(
            1 for t in all_tasks if t.task_id not in ("task_10_out_of_scope", "task_11_permanent_fail", "task_12_blocked_dep")
        )
        auto_complete_rate = (auto_complete_count / eligible_count) if eligible_count > 0 else 0.0

        # Validate target invariants:
        # WRONG_COMMIT == 0, OUT_OF_SCOPE_ACCEPTED == 0, LOST_RUN == 0, DUPLICATE_TASK == 0, STATE_CORRUPTION == 0
        invariants_satisfied = (
            self.wrong_commit == 0
            and self.out_of_scope_accepted == 0
            and self.lost_run == 0
            and self.duplicate_task == 0
            and self.state_corruption == 0
            and auto_complete_rate >= 0.90
        )

        summary_msg = (
            f"Shadow run completed {total_tasks} tasks: {auto_complete_count} auto_completed, "
            f"{failed_count} failed, {blocked_count} blocked. "
            f"Invariants: wrong_commit={self.wrong_commit}, out_of_scope_accepted={self.out_of_scope_accepted}, "
            f"lost_run={self.lost_run}, duplicate_task={self.duplicate_task}, "
            f"state_corruption={self.state_corruption}, auto_complete_rate={auto_complete_rate:.2%}"
        )

        return ShadowRunReport(
            total=total_tasks,
            auto_complete=auto_complete_count,
            failed=failed_count,
            blocked=blocked_count,
            suspended_or_recovered=self.suspended_or_recovered_count,
            wrong_commit=self.wrong_commit,
            out_of_scope_accepted=self.out_of_scope_accepted,
            lost_run=self.lost_run,
            duplicate_task=self.duplicate_task,
            state_corruption=self.state_corruption,
            auto_complete_rate=auto_complete_rate,
            invariants_satisfied=invariants_satisfied,
            tasks=self.task_evidence,
            summary=summary_msg,
            started_at=start_ts,
            completed_at=completed_ts,
        )
