"""VNext-only verification gate, deterministic failure package, bounded auto-repair orchestration, and guarded safe auto-commit policy."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import fnmatch
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time
from typing import Any, Callable, Optional, Sequence

from .contracts import (
    CREDENTIAL_PATTERNS,
    AutoCommitPolicy,
    InvalidStateTransitionError,
    RiskClass,
    RunRecord,
    RunState,
    TaskContract,
    _utc_now_iso,
    normalize_path,
    normalize_paths,
    validate_no_credentials,
)
from .run_control import DurableRunManager, WorkerContext, WorkerResult

# Maximum bounds for output capture and diff inspection
DEFAULT_MAX_OUTPUT_BYTES: int = 100_000
DEFAULT_MAX_TRACEBACK_CHARS: int = 8_192
DEFAULT_MAX_DIFF_BYTES: int = 500_000
DEFAULT_MAX_DIFF_LINES: int = 2_000
DEFAULT_MAX_CHANGED_FILES: int = 50
DEFAULT_COMMAND_TIMEOUT_SECONDS: float = 120.0
DEFAULT_MAX_REPAIR_ROUNDS: int = 3

# Patterns for extracting failed tests from command output
PYTEST_FAIL_PATTERN = re.compile(r"(?:FAILED|ERROR)\s+([^\s:]+(?:::[^\s:]+)+)", re.MULTILINE)
UNITTEST_FAIL_PATTERN = re.compile(r"(?:FAIL|ERROR):\s+([^\s\(]+)\s+\(([^\)]+)\)", re.MULTILINE)

# Transient cache/build artifact patterns ignored during scope checking
TRANSIENT_IGNORE_PATTERNS: tuple[str, ...] = (
    "__pycache__",
    "__pycache__/*",
    "*/__pycache__",
    "*/__pycache__/*",
    "*.pyc",
    "*.pyo",
    "*.pyd",
    ".pytest_cache",
    ".pytest_cache/*",
    "*/.pytest_cache",
    "*/.pytest_cache/*",
    ".coverage",
    ".coverage.*",
    "*/.coverage*",
    "*.tmp",
)


class AutoCommitDecision(str, Enum):
    """Decision outcome for the safe auto-commit policy gate."""

    PASS = "PASS"
    CODEX_REVIEW_REQUIRED = "CODEX_REVIEW_REQUIRED"

    @classmethod
    def from_value(cls, val: str | AutoCommitDecision) -> AutoCommitDecision:
        if isinstance(val, cls):
            return val
        if not isinstance(val, str):
            raise ValueError(f"Invalid auto commit decision type: {type(val).__name__}")
        norm = val.strip().upper().replace("-", "_").replace(" ", "_")
        for member in cls:
            if member.value == norm or member.name == norm:
                return member
        raise ValueError(f"Unknown auto commit decision: {val!r}")


def _truncate_output(text: str, max_bytes: int = DEFAULT_MAX_OUTPUT_BYTES) -> str:
    """Safely bound output text to a maximum size in bytes, keeping head and tail if truncated."""
    if not text:
        return ""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    half = max_bytes // 2 - 32
    head = encoded[:half].decode("utf-8", errors="replace")
    tail = encoded[-half:].decode("utf-8", errors="replace")
    return f"{head}\n\n... [TRUNCATED {len(encoded) - 2 * half} BYTES] ...\n\n{tail}"


def _sanitize_output(text: str) -> str:
    """Scrub sensitive credential patterns from log or diff text."""
    if not text:
        return ""
    sanitized = text
    for pat in CREDENTIAL_PATTERNS:
        sanitized = pat.sub("[REDACTED_CREDENTIAL]", sanitized)
    return sanitized


def extract_failed_tests(output: str) -> list[str]:
    """Extract list of failed test identifiers deterministically from command output."""
    if not output:
        return []
    failed: list[str] = []

    # 1. Pytest style: FAILED tests/test_foo.py::test_bar
    for match in PYTEST_FAIL_PATTERN.finditer(output):
        t_id = match.group(1).strip()
        if t_id and t_id not in failed:
            failed.append(t_id)

    # 2. Unittest style: FAIL: test_bar (test_foo.TestClass)
    for match in UNITTEST_FAIL_PATTERN.finditer(output):
        func_name = match.group(1).strip()
        cls_path = match.group(2).strip()
        t_id = f"{cls_path}.{func_name}"
        if t_id and t_id not in failed:
            failed.append(t_id)

    return failed


def extract_bounded_traceback(output: str, max_chars: int = DEFAULT_MAX_TRACEBACK_CHARS) -> str | None:
    """Extract and bound the most recent traceback from command output."""
    if not output:
        return None
    tb_idx = output.rfind("Traceback (most recent call last):")
    if tb_idx == -1:
        return None
    lines = output[tb_idx:].splitlines()
    tb_lines = [lines[0]]
    for line in lines[1:]:
        if line.startswith(" ") or line.startswith("\t"):
            tb_lines.append(line)
        elif line.strip() and not line.startswith("===") and not line.startswith("---"):
            tb_lines.append(line)
            break
        else:
            break
    tb = "\n".join(tb_lines).strip()
    if len(tb) > max_chars:
        half = max_chars // 2 - 20
        head = tb[:half]
        tail = tb[-half:]
        tb = f"{head}\n... [truncated traceback] ...\n{tail}"
    return tb


@dataclass
class CommandResult:
    """Structured result of a single executed verification command."""

    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    timed_out: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_seconds": self.duration_seconds,
            "timed_out": self.timed_out,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CommandResult:
        if not isinstance(data, dict):
            raise ValueError(f"Expected dict, got {type(data).__name__}")
        return cls(
            command=str(data.get("command", "")),
            exit_code=int(data.get("exit_code", 0)),
            stdout=str(data.get("stdout", "")),
            stderr=str(data.get("stderr", "")),
            duration_seconds=float(data.get("duration_seconds", 0.0)),
            timed_out=bool(data.get("timed_out", False)),
        )


@dataclass
class ScopeGateResult:
    """Result of evaluating repository changes against the task contract scope."""

    passed: bool
    violations: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    diff_summary: dict[str, Any] = field(default_factory=dict)
    base_head_matched: bool = True
    diff_check_passed: bool = True
    security_clean: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "violations": list(self.violations),
            "changed_files": list(self.changed_files),
            "diff_summary": dict(self.diff_summary),
            "base_head_matched": self.base_head_matched,
            "diff_check_passed": self.diff_check_passed,
            "security_clean": self.security_clean,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScopeGateResult:
        if not isinstance(data, dict):
            raise ValueError(f"Expected dict, got {type(data).__name__}")
        return cls(
            passed=bool(data.get("passed", False)),
            violations=list(data.get("violations", [])),
            changed_files=list(data.get("changed_files", [])),
            diff_summary=dict(data.get("diff_summary", {})),
            base_head_matched=bool(data.get("base_head_matched", True)),
            diff_check_passed=bool(data.get("diff_check_passed", True)),
            security_clean=bool(data.get("security_clean", True)),
        )


@dataclass
class VerificationEvidence:
    """Structured verification evidence collected during gate execution."""

    task_id: str
    run_id: str
    passed: bool
    commands: list[CommandResult] = field(default_factory=list)
    exit_codes: list[int] = field(default_factory=list)
    failed_tests: list[str] = field(default_factory=list)
    bounded_traceback: str | None = None
    changed_files: list[str] = field(default_factory=list)
    diff_summary: dict[str, Any] = field(default_factory=dict)
    current_state: str = RunState.VERIFYING.value
    scope_passed: bool = True
    scope_violations: list[str] = field(default_factory=list)
    error_message: str | None = None
    timestamp: str = field(default_factory=_utc_now_iso)
    repair_round: int = 0

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Validate fields and ensure no credentials exist."""
        if not self.task_id or not isinstance(self.task_id, str):
            raise ValueError("task_id must be a non-empty string")
        if not self.run_id or not isinstance(self.run_id, str):
            raise ValueError("run_id must be a non-empty string")

        if isinstance(self.current_state, RunState):
            self.current_state = self.current_state.value
        elif isinstance(self.current_state, str):
            self.current_state = RunState.from_value(self.current_state).value
        else:
            raise ValueError(f"Invalid current_state: {self.current_state!r}")

        validate_no_credentials(self.to_dict(), "verification_evidence")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "passed": self.passed,
            "commands": [c.to_dict() if isinstance(c, CommandResult) else c for c in self.commands],
            "exit_codes": list(self.exit_codes),
            "failed_tests": list(self.failed_tests),
            "bounded_traceback": self.bounded_traceback,
            "changed_files": list(self.changed_files),
            "diff_summary": dict(self.diff_summary),
            "current_state": self.current_state,
            "scope_passed": self.scope_passed,
            "scope_violations": list(self.scope_violations),
            "error_message": self.error_message,
            "timestamp": self.timestamp,
            "repair_round": self.repair_round,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VerificationEvidence:
        if not isinstance(data, dict):
            raise ValueError(f"Expected dict, got {type(data).__name__}")
        raw_commands = data.get("commands", [])
        parsed_commands: list[CommandResult] = []
        for cmd in raw_commands:
            if isinstance(cmd, CommandResult):
                parsed_commands.append(cmd)
            elif isinstance(cmd, dict):
                parsed_commands.append(CommandResult.from_dict(cmd))
            else:
                parsed_commands.append(CommandResult(command=str(cmd), exit_code=0))

        return cls(
            task_id=str(data.get("task_id", "")),
            run_id=str(data.get("run_id", "")),
            passed=bool(data.get("passed", False)),
            commands=parsed_commands,
            exit_codes=[int(ec) for ec in data.get("exit_codes", [])],
            failed_tests=[str(t) for t in data.get("failed_tests", [])],
            bounded_traceback=data.get("bounded_traceback"),
            changed_files=[str(f) for f in data.get("changed_files", [])],
            diff_summary=dict(data.get("diff_summary", {})),
            current_state=str(data.get("current_state", RunState.VERIFYING.value)),
            scope_passed=bool(data.get("scope_passed", True)),
            scope_violations=[str(v) for v in data.get("scope_violations", [])],
            error_message=data.get("error_message"),
            timestamp=str(data.get("timestamp", _utc_now_iso())),
            repair_round=int(data.get("repair_round", 0)),
        )

    def to_json(self, **kwargs: Any) -> str:
        return json.dumps(self.to_dict(), **kwargs)

    @classmethod
    def from_json(cls, json_str: str) -> VerificationEvidence:
        return cls.from_dict(json.loads(json_str))


@dataclass
class FailurePackage:
    """Deterministic failure package passed to auto-repair planner or orchestrator."""

    task_id: str
    run_id: str
    repair_round: int
    failed_tests: list[str] = field(default_factory=list)
    bounded_traceback: str | None = None
    commands: list[CommandResult] = field(default_factory=list)
    exit_codes: list[int] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    diff_summary: dict[str, Any] = field(default_factory=dict)
    current_state: str = RunState.REPAIRING.value
    scope_violations: list[str] = field(default_factory=list)
    error_message: str | None = None
    timestamp: str = field(default_factory=_utc_now_iso)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Validate fields and sanitize against credentials."""
        if not self.task_id or not isinstance(self.task_id, str):
            raise ValueError("task_id must be a non-empty string")
        if not self.run_id or not isinstance(self.run_id, str):
            raise ValueError("run_id must be a non-empty string")

        if isinstance(self.current_state, RunState):
            self.current_state = self.current_state.value
        elif isinstance(self.current_state, str):
            self.current_state = RunState.from_value(self.current_state).value
        else:
            raise ValueError(f"Invalid current_state: {self.current_state!r}")

        validate_no_credentials(self.to_dict(), "failure_package")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "repair_round": self.repair_round,
            "failed_tests": list(self.failed_tests),
            "bounded_traceback": self.bounded_traceback,
            "commands": [c.to_dict() if isinstance(c, CommandResult) else c for c in self.commands],
            "exit_codes": list(self.exit_codes),
            "changed_files": list(self.changed_files),
            "diff_summary": dict(self.diff_summary),
            "current_state": self.current_state,
            "scope_violations": list(self.scope_violations),
            "error_message": self.error_message,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FailurePackage:
        if not isinstance(data, dict):
            raise ValueError(f"Expected dict, got {type(data).__name__}")
        raw_commands = data.get("commands", [])
        parsed_commands: list[CommandResult] = []
        for cmd in raw_commands:
            if isinstance(cmd, CommandResult):
                parsed_commands.append(cmd)
            elif isinstance(cmd, dict):
                parsed_commands.append(CommandResult.from_dict(cmd))
            else:
                parsed_commands.append(CommandResult(command=str(cmd), exit_code=1))

        return cls(
            task_id=str(data.get("task_id", "")),
            run_id=str(data.get("run_id", "")),
            repair_round=int(data.get("repair_round", 0)),
            failed_tests=[str(t) for t in data.get("failed_tests", [])],
            bounded_traceback=data.get("bounded_traceback"),
            commands=parsed_commands,
            exit_codes=[int(ec) for ec in data.get("exit_codes", [])],
            changed_files=[str(f) for f in data.get("changed_files", [])],
            diff_summary=dict(data.get("diff_summary", {})),
            current_state=str(data.get("current_state", RunState.REPAIRING.value)),
            scope_violations=[str(v) for v in data.get("scope_violations", [])],
            error_message=data.get("error_message"),
            timestamp=str(data.get("timestamp", _utc_now_iso())),
        )

    def to_json(self, **kwargs: Any) -> str:
        return json.dumps(self.to_dict(), **kwargs)

    @classmethod
    def from_json(cls, json_str: str) -> FailurePackage:
        return cls.from_dict(json.loads(json_str))


@dataclass
class AutoCommitResult:
    """Decision result from safe auto-commit policy evaluation."""

    decision: AutoCommitDecision
    allowed: bool
    reasons: list[str] = field(default_factory=list)
    evidence: VerificationEvidence | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "allowed": self.allowed,
            "reasons": list(self.reasons),
            "evidence": self.evidence.to_dict() if self.evidence else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AutoCommitResult:
        if not isinstance(data, dict):
            raise ValueError(f"Expected dict, got {type(data).__name__}")
        evidence_raw = data.get("evidence")
        evidence = VerificationEvidence.from_dict(evidence_raw) if evidence_raw else None
        return cls(
            decision=AutoCommitDecision.from_value(data.get("decision", AutoCommitDecision.CODEX_REVIEW_REQUIRED)),
            allowed=bool(data.get("allowed", False)),
            reasons=list(data.get("reasons", [])),
            evidence=evidence,
        )


def _run_git_command(args: list[str], cwd: str | Path, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    """Execute a git command in cwd without shell composition."""
    cmd = ["git", *args]
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _is_transient_file(path_str: str) -> bool:
    """Check if file matches transient runtime/cache artifact patterns."""
    norm = normalize_path(path_str).rstrip("/")
    for pat in TRANSIENT_IGNORE_PATTERNS:
        clean_pat = pat.rstrip("/")
        if fnmatch.fnmatch(norm, clean_pat) or fnmatch.fnmatch(norm + "/", pat):
            return True
    if norm.startswith(".pytest_cache") or "/.pytest_cache" in norm:
        return True
    if norm.startswith("__pycache__") or "/__pycache__" in norm:
        return True
    return False


def _get_git_changed_files(workdir: Path) -> list[str]:
    """Retrieve changed, staged, and untracked files relative to workdir, filtering transient artifacts."""
    changed_files: list[str] = []
    if not (workdir / ".git").exists() and not (workdir / ".git").is_file():
        try:
            res = _run_git_command(["rev-parse", "--is-inside-work-tree"], workdir)
            if res.returncode != 0:
                return []
        except Exception:
            return []

    try:
        res = _run_git_command(["status", "--porcelain", "-uall"], workdir)
        if res.returncode == 0 and res.stdout:
            for line in res.stdout.splitlines():
                if not line.strip():
                    continue
                path_part = line[3:].strip()
                if " -> " in path_part:
                    path_part = path_part.split(" -> ")[-1].strip()
                if path_part.startswith('"') and path_part.endswith('"'):
                    path_part = path_part[1:-1]
                norm = normalize_path(path_part)
                if norm and not _is_transient_file(norm) and norm not in changed_files:
                    changed_files.append(norm)
    except Exception:
        pass

    return changed_files


def _get_git_diff_summary(workdir: Path) -> dict[str, Any]:
    """Calculate git diff metrics (insertions, deletions, diff_bytes, files_changed)."""
    summary: dict[str, Any] = {
        "insertions": 0,
        "deletions": 0,
        "files_changed": 0,
        "diff_bytes": 0,
    }
    try:
        res = _run_git_command(["diff", "HEAD"], workdir)
        if res.returncode != 0:
            res = _run_git_command(["diff"], workdir)

        if res.returncode == 0:
            raw_diff = res.stdout
            summary["diff_bytes"] = len(raw_diff.encode("utf-8", errors="replace"))
            for line in raw_diff.splitlines():
                if line.startswith("+") and not line.startswith("+++"):
                    summary["insertions"] += 1
                elif line.startswith("-") and not line.startswith("---"):
                    summary["deletions"] += 1

        stat_res = _run_git_command(["diff", "--shortstat"], workdir)
        if stat_res.returncode == 0 and stat_res.stdout:
            match = re.search(r"(\d+)\s+file[s]?\s+changed", stat_res.stdout)
            if match:
                summary["files_changed"] = int(match.group(1))
    except Exception:
        pass

    return summary


def _is_path_allowed(file_path: str, allowed_paths: Sequence[str]) -> bool:
    """Check if file_path is matched by any pattern/prefix in allowed_paths."""
    if not allowed_paths:
        return True
    norm_file = normalize_path(file_path).lstrip("./")
    for allowed in allowed_paths:
        norm_allowed = normalize_path(allowed).lstrip("./")
        if norm_file == norm_allowed or norm_file.startswith(norm_allowed.rstrip("/") + "/"):
            return True
        if fnmatch.fnmatch(norm_file, norm_allowed):
            return True
    return False


def _is_path_forbidden(file_path: str, forbidden_paths: Sequence[str]) -> bool:
    """Check if file_path matches any pattern/prefix in forbidden_paths."""
    if not forbidden_paths:
        return False
    norm_file = normalize_path(file_path).lstrip("./")
    for forbidden in forbidden_paths:
        norm_forbidden = normalize_path(forbidden).lstrip("./")
        if norm_file == norm_forbidden or norm_file.startswith(norm_forbidden.rstrip("/") + "/"):
            return True
        if fnmatch.fnmatch(norm_file, norm_forbidden):
            return True
    return False


def evaluate_scope_gate(
    contract: TaskContract,
    workdir: str | Path | None = None,
    *,
    max_diff_bytes: int = DEFAULT_MAX_DIFF_BYTES,
    max_diff_lines: int = DEFAULT_MAX_DIFF_LINES,
    max_changed_files: int = DEFAULT_MAX_CHANGED_FILES,
    git_bin: str = "git",
) -> ScopeGateResult:
    """Deterministic evaluation of repository changes against TaskContract scope and security rules."""
    resolved_workdir = Path(workdir or contract.workdir).resolve()
    violations: list[str] = []
    base_head_matched = True
    diff_check_passed = True
    security_clean = True

    changed_files = _get_git_changed_files(resolved_workdir)
    diff_summary = _get_git_diff_summary(resolved_workdir)

    # 1. Check allowed_paths & forbidden_paths
    if contract.allowed_paths and changed_files:
        for f in changed_files:
            if not _is_path_allowed(f, contract.allowed_paths):
                violations.append(f"Changed file '{f}' is not in contract.allowed_paths: {contract.allowed_paths}")

    if contract.forbidden_paths and changed_files:
        for f in changed_files:
            if _is_path_forbidden(f, contract.forbidden_paths):
                violations.append(f"Changed file '{f}' matches contract.forbidden_paths: {contract.forbidden_paths}")

    # 2. Check base_head consistency
    if contract.base_head and contract.base_head.strip():
        try:
            head_res = _run_git_command(["rev-parse", "HEAD"], resolved_workdir)
            if head_res.returncode == 0:
                current_head = head_res.stdout.strip()
                if current_head != contract.base_head:
                    ancestor_res = _run_git_command(
                        ["merge-base", "--is-ancestor", contract.base_head, "HEAD"],
                        resolved_workdir,
                    )
                    if ancestor_res.returncode != 0:
                        base_head_matched = False
                        violations.append(
                            f"Base head mismatch: contract.base_head '{contract.base_head}' is not an ancestor of current HEAD '{current_head}'"
                        )
            else:
                base_head_matched = False
                violations.append(
                    f"Base head verification failed: git rev-parse HEAD exited {head_res.returncode}: {head_res.stderr.strip()}"
                )
        except Exception as exc:
            base_head_matched = False
            violations.append(f"Base head verification exception: {exc}")

    # 3. Check git diff --check (whitespace errors, merge conflict markers)
    try:
        check_res = _run_git_command(["diff", "--check"], resolved_workdir)
        if check_res.returncode != 0:
            diff_check_passed = False
            violations.append(f"git diff --check detected conflict markers or whitespace errors:\n{check_res.stdout or check_res.stderr}")
    except Exception as exc:
        diff_check_passed = False
        violations.append(f"git diff --check execution error: {exc}")

    if diff_check_passed and changed_files:
        # Also check untracked and modified changed files directly for merge conflict markers
        for f in changed_files:
            file_path = resolved_workdir / f
            if file_path.is_file() and file_path.stat().st_size < 1_000_000:
                try:
                    content = file_path.read_text(encoding="utf-8", errors="ignore")
                    if re.search(r"^[<>=]{7}(?:\s.*)?$", content, re.MULTILINE):
                        diff_check_passed = False
                        violations.append(f"git diff --check / conflict marker violation: merge conflict markers detected in changed file '{f}'")
                        break
                except Exception:
                    pass

    # 4. Check diff size guards
    total_diff_lines = diff_summary.get("insertions", 0) + diff_summary.get("deletions", 0)
    if total_diff_lines > max_diff_lines:
        violations.append(f"Diff size lines ({total_diff_lines}) exceeded max_diff_lines guard ({max_diff_lines})")

    if diff_summary.get("diff_bytes", 0) > max_diff_bytes:
        violations.append(f"Diff size bytes ({diff_summary.get('diff_bytes')}B) exceeded max_diff_bytes guard ({max_diff_bytes}B)")

    if len(changed_files) > max_changed_files:
        violations.append(f"Total changed files ({len(changed_files)}) exceeded max_changed_files guard ({max_changed_files})")

    # 5. Check credential & security hints in diff and changed files
    try:
        diff_res = _run_git_command(["diff", "HEAD"], resolved_workdir)
        diff_text = diff_res.stdout if diff_res.returncode == 0 else ""
        if not diff_text:
            diff_res = _run_git_command(["diff"], resolved_workdir)
            diff_text = diff_res.stdout if diff_res.returncode == 0 else ""

        for pat in CREDENTIAL_PATTERNS:
            if diff_text and pat.search(diff_text):
                security_clean = False
                violations.append(f"Security violation: Credential-like secret detected in diff matching pattern {pat.pattern}")
                break

        if security_clean:
            for f in changed_files:
                file_path = resolved_workdir / f
                if file_path.is_file() and file_path.stat().st_size < 1_000_000:
                    try:
                        content = file_path.read_text(encoding="utf-8", errors="ignore")
                        for pat in CREDENTIAL_PATTERNS:
                            if pat.search(content):
                                security_clean = False
                                violations.append(f"Security violation: Credential-like secret detected in '{f}' matching pattern {pat.pattern}")
                                break
                    except Exception:
                        pass
                if not security_clean:
                    break
    except Exception:
        pass

    passed = len(violations) == 0 and base_head_matched and diff_check_passed and security_clean

    return ScopeGateResult(
        passed=passed,
        violations=violations,
        changed_files=changed_files,
        diff_summary=diff_summary,
        base_head_matched=base_head_matched,
        diff_check_passed=diff_check_passed,
        security_clean=security_clean,
    )


def _split_command(cmd: str) -> list[str]:
    """Split a command string into arguments safely across Windows and POSIX, stripping surrounding quotes."""
    if not cmd.strip():
        return []
    try:
        if os.name == "nt":
            raw_args = shlex.split(cmd, posix=False)
            args: list[str] = []
            for arg in raw_args:
                if (arg.startswith('"') and arg.endswith('"')) or (arg.startswith("'") and arg.endswith("'")):
                    args.append(arg[1:-1])
                else:
                    args.append(arg)
            return args
        else:
            return shlex.split(cmd)
    except Exception:
        return cmd.strip().split()


def execute_verification_command(
    cmd_str: str,
    cwd: Path,
    timeout: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    env: dict[str, str] | None = None,
) -> CommandResult:
    """Execute a single verification command with bounded output capture, timing, and credential scrubbing."""
    start_time = time.monotonic()
    args = _split_command(cmd_str)
    if not args:
        return CommandResult(command=cmd_str, exit_code=0, duration_seconds=0.0)

    # Prepare environment
    run_env = os.environ.copy()
    if env:
        run_env.update(env)

    try:
        proc = subprocess.run(
            args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=run_env,
            check=False,
        )
        duration = time.monotonic() - start_time
        clean_stdout = _sanitize_output(_truncate_output(proc.stdout, max_output_bytes))
        clean_stderr = _sanitize_output(_truncate_output(proc.stderr, max_output_bytes))

        return CommandResult(
            command=cmd_str,
            exit_code=proc.returncode,
            stdout=clean_stdout,
            stderr=clean_stderr,
            duration_seconds=duration,
            timed_out=False,
        )
    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - start_time
        stdout_str = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else str(exc.stdout or "")
        stderr_str = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else str(exc.stderr or "")
        return CommandResult(
            command=cmd_str,
            exit_code=124,
            stdout=_sanitize_output(_truncate_output(stdout_str, max_output_bytes)),
            stderr=_sanitize_output(_truncate_output(f"Command timed out after {timeout}s: {stderr_str}", max_output_bytes)),
            duration_seconds=duration,
            timed_out=True,
        )
    except Exception as exc:
        duration = time.monotonic() - start_time
        return CommandResult(
            command=cmd_str,
            exit_code=1,
            stdout="",
            stderr=_sanitize_output(f"Execution error: {exc}"),
            duration_seconds=duration,
            timed_out=False,
        )


def run_verification(
    contract: TaskContract,
    workdir: str | Path | None = None,
    *,
    run_id: str | None = None,
    timeout_per_command: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    current_state: RunState | str = RunState.VERIFYING,
    repair_round: int = 0,
    env: dict[str, str] | None = None,
) -> VerificationEvidence:
    """Deterministic verification runner executing TaskContract.verification_commands with scope gate."""
    resolved_workdir = Path(workdir or contract.workdir).resolve()
    resolved_run_id = run_id or f"run-{contract.task_id}-verif"

    command_results: list[CommandResult] = []
    exit_codes: list[int] = []
    all_failed_tests: list[str] = []
    traceback_found: str | None = None
    all_commands_passed = True
    error_messages: list[str] = []

    # 1. Execute verification commands deterministically
    for cmd in contract.verification_commands:
        if not cmd.strip():
            continue
        res = execute_verification_command(
            cmd,
            cwd=resolved_workdir,
            timeout=timeout_per_command,
            max_output_bytes=max_output_bytes,
            env=env,
        )
        command_results.append(res)
        exit_codes.append(res.exit_code)

        combined_output = f"{res.stdout}\n{res.stderr}"
        failed_tests = extract_failed_tests(combined_output)
        for t in failed_tests:
            if t not in all_failed_tests:
                all_failed_tests.append(t)

        tb = extract_bounded_traceback(combined_output)
        if tb and not traceback_found:
            traceback_found = tb

        if res.exit_code != 0 or res.timed_out:
            all_commands_passed = False
            if res.timed_out:
                error_messages.append(f"Command '{cmd}' timed out after {timeout_per_command}s")
            else:
                error_messages.append(f"Command '{cmd}' failed with exit code {res.exit_code}")

    # 2. Evaluate scope gate
    scope_result = evaluate_scope_gate(contract, resolved_workdir)
    if not scope_result.passed:
        error_messages.extend(scope_result.violations)

    # 3. Overall pass requires all verification commands to exit 0 and scope gate to pass
    passed = all_commands_passed and scope_result.passed

    combined_error = "; ".join(error_messages) if error_messages else None

    evidence = VerificationEvidence(
        task_id=contract.task_id,
        run_id=resolved_run_id,
        passed=passed,
        commands=command_results,
        exit_codes=exit_codes,
        failed_tests=all_failed_tests,
        bounded_traceback=traceback_found,
        changed_files=scope_result.changed_files,
        diff_summary=scope_result.diff_summary,
        current_state=current_state,
        scope_passed=scope_result.passed,
        scope_violations=scope_result.violations,
        error_message=combined_error,
        repair_round=repair_round,
    )
    return evidence


def create_failure_package(
    contract: TaskContract,
    evidence: VerificationEvidence,
    repair_round: int = 0,
) -> FailurePackage:
    """Create a clean, bounded, JSON-safe FailurePackage for the auto-repair loop."""
    return FailurePackage(
        task_id=contract.task_id,
        run_id=evidence.run_id,
        repair_round=repair_round,
        failed_tests=list(evidence.failed_tests),
        bounded_traceback=evidence.bounded_traceback,
        commands=list(evidence.commands),
        exit_codes=list(evidence.exit_codes),
        changed_files=list(evidence.changed_files),
        diff_summary=dict(evidence.diff_summary),
        current_state=RunState.REPAIRING.value,
        scope_violations=list(evidence.scope_violations),
        error_message=evidence.error_message,
        timestamp=_utc_now_iso(),
    )


# High-risk keywords in changed files or commit patterns
MIGRATION_PATTERNS = [re.compile(r"migrations?[/\\].*\.py$", re.IGNORECASE), re.compile(r"alembic[/\\].*\.py$", re.IGNORECASE)]


def evaluate_auto_commit_policy(
    contract: TaskContract,
    evidence: VerificationEvidence,
    workdir: str | Path | None = None,
) -> AutoCommitResult:
    """Safe auto-commit decision gate. Evaluates all safety requirements before permitting an auto-commit."""
    reasons: list[str] = []
    resolved_workdir = Path(workdir or contract.workdir).resolve()

    # 1. Policy check
    if contract.auto_commit_policy == AutoCommitPolicy.NEVER:
        reasons.append("AutoCommitPolicy is NEVER")

    # 2. Risk classification check (no production or destructive auto-commit)
    if contract.risk_class == RiskClass.PRODUCTION:
        reasons.append("Task risk class is PRODUCTION; automated commits require human review")
    elif contract.risk_class == RiskClass.DESTRUCTIVE:
        reasons.append("Task risk class is DESTRUCTIVE; automated commits require human review")

    # 3. Verification check
    if not evidence.passed:
        reasons.append("Verification evidence indicated failure (passed is False)")

    # 4. Scope check from verification evidence
    if not evidence.scope_passed:
        reasons.append(f"Scope gate failed with violations: {evidence.scope_violations}")

    # 5. Independent fresh current-worktree scope gate evaluation
    scope_result = evaluate_scope_gate(contract, resolved_workdir)
    if not scope_result.passed:
        for v in scope_result.violations:
            if v not in reasons:
                reasons.append(v)
        if not scope_result.base_head_matched and not any("Base head" in r for r in reasons):
            reasons.append("Current repository HEAD is not compatible with contract.base_head")
        if not scope_result.diff_check_passed and not any("diff --check" in r for r in reasons):
            reasons.append("git diff --check failed (conflict markers or whitespace issues detected)")
        if not scope_result.security_clean and not any("Security violation" in r for r in reasons):
            reasons.append("Security cleanliness check failed in worktree")

    # 6. Database migrations / destructive structural change detection
    all_changed_files = list(dict.fromkeys(list(evidence.changed_files) + list(scope_result.changed_files)))
    for f in all_changed_files:
        for pat in MIGRATION_PATTERNS:
            if pat.search(f):
                msg = f"Migration file detected '{f}'; schema migrations require explicit review"
                if msg not in reasons:
                    reasons.append(msg)
                break

    # 7. Check security in evidence
    if evidence.error_message and any(p.search(evidence.error_message) for p in CREDENTIAL_PATTERNS):
        reasons.append("Sensitive credential patterns found in verification error message")

    if not reasons:
        return AutoCommitResult(
            decision=AutoCommitDecision.PASS,
            allowed=True,
            reasons=["All verification, scope, safety, and risk criteria met."],
            evidence=evidence,
        )
    else:
        return AutoCommitResult(
            decision=AutoCommitDecision.CODEX_REVIEW_REQUIRED,
            allowed=False,
            reasons=reasons,
            evidence=evidence,
        )


RepairCallback = Callable[[FailurePackage, Optional[WorkerContext]], Any]


@dataclass
class RepairLoopResult:
    """Outcome of running the bounded repair loop."""

    success: bool
    final_state: RunState
    repair_rounds_completed: int
    final_evidence: VerificationEvidence
    auto_commit_result: AutoCommitResult | None = None
    failure_package: FailurePackage | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "final_state": self.final_state.value,
            "repair_rounds_completed": self.repair_rounds_completed,
            "final_evidence": self.final_evidence.to_dict(),
            "auto_commit_result": self.auto_commit_result.to_dict() if self.auto_commit_result else None,
            "failure_package": self.failure_package.to_dict() if self.failure_package else None,
            "error_message": self.error_message,
        }


def execute_repair_loop(
    contract: TaskContract,
    workdir: str | Path | None = None,
    *,
    run_id: str | None = None,
    repair_callback: RepairCallback | None = None,
    max_repair_rounds: int | None = None,
    run_manager: DurableRunManager | None = None,
    worker_context: WorkerContext | None = None,
    env: dict[str, str] | None = None,
) -> RepairLoopResult:
    """Bounded auto-repair loop orchestration with deterministic failure packages and guarded commit policy."""
    resolved_workdir = Path(workdir or contract.workdir).resolve()
    resolved_run_id = run_id or (worker_context.run_id if worker_context else f"run-{contract.task_id}-loop")
    limit_rounds = max_repair_rounds if max_repair_rounds is not None else contract.max_repair_rounds
    if limit_rounds < 0:
        limit_rounds = DEFAULT_MAX_REPAIR_ROUNDS

    current_round = 0
    last_evidence: VerificationEvidence | None = None
    last_failure_pkg: FailurePackage | None = None

    while current_round <= limit_rounds:
        # Check cancellation
        if worker_context and worker_context.is_cancelled():
            if run_manager:
                latest = run_manager.store.get_run(resolved_run_id)
                if latest and latest.state not in (RunState.CANCELLED, RunState.FAILED, RunState.COMPLETE):
                    run_manager.store.transition_run(
                        resolved_run_id,
                        expected_version=latest.state_version,
                        target_state=RunState.CANCELLED,
                        last_error="Cancelled during verification/repair loop",
                    )
            ev = last_evidence or VerificationEvidence(
                task_id=contract.task_id,
                run_id=resolved_run_id,
                passed=False,
                current_state=RunState.CANCELLED.value,
                error_message="Cancelled cooperatively",
            )
            return RepairLoopResult(
                success=False,
                final_state=RunState.CANCELLED,
                repair_rounds_completed=current_round,
                final_evidence=ev,
                error_message="Cancelled cooperatively",
            )

        # 1. Update run state to VERIFYING
        if run_manager:
            latest = run_manager.store.get_run(resolved_run_id)
            if latest and latest.state in (RunState.RUNNING, RunState.REPAIRING, RunState.QUEUED):
                try:
                    run_manager.store.transition_run(
                        resolved_run_id,
                        expected_version=latest.state_version,
                        target_state=RunState.VERIFYING,
                        repair_round=current_round,
                    )
                except Exception:
                    pass

        # 2. Run verification & scope checks
        evidence = run_verification(
            contract,
            resolved_workdir,
            run_id=resolved_run_id,
            current_state=RunState.VERIFYING,
            repair_round=current_round,
            env=env,
        )
        last_evidence = evidence

        # Send heartbeat if context available
        if worker_context:
            worker_context.heartbeat()

        # 3. Check verification outcome
        if evidence.passed:
            # Verification passed! Evaluate auto-commit decision gate
            commit_result = evaluate_auto_commit_policy(contract, evidence, resolved_workdir)

            if run_manager:
                latest = run_manager.store.get_run(resolved_run_id)
                if latest and latest.state not in (RunState.COMPLETE, RunState.FAILED, RunState.CANCELLED):
                    curr_ver = latest.state_version
                    if commit_result.allowed:
                        committing = run_manager.store.transition_run(
                            resolved_run_id,
                            expected_version=curr_ver,
                            target_state=RunState.COMMITTING,
                            commit_sha="verified-auto-commit",
                            verification_result=evidence.to_dict(),
                        )
                        curr_ver = committing.state_version

                    run_manager.store.transition_run(
                        resolved_run_id,
                        expected_version=curr_ver,
                        target_state=RunState.COMPLETE,
                        verification_result=evidence.to_dict(),
                        result_summary="Verification passed and policy gate evaluated",
                        repair_round=current_round,
                    )

            return RepairLoopResult(
                success=True,
                final_state=RunState.COMPLETE,
                repair_rounds_completed=current_round,
                final_evidence=evidence,
                auto_commit_result=commit_result,
            )

        # 4. Verification failed: construct deterministic FailurePackage
        last_failure_pkg = create_failure_package(contract, evidence, repair_round=current_round)

        # Check if more repair rounds are available
        if current_round < limit_rounds and repair_callback is not None:
            # Transition state to REPAIRING
            if run_manager:
                latest = run_manager.store.get_run(resolved_run_id)
                if latest and latest.state == RunState.VERIFYING:
                    try:
                        run_manager.store.transition_run(
                            resolved_run_id,
                            expected_version=latest.state_version,
                            target_state=RunState.REPAIRING,
                            repair_round=current_round + 1,
                            last_error=evidence.error_message,
                            verification_result=evidence.to_dict(),
                        )
                    except Exception:
                        pass

            # Execute injectable repair callback
            try:
                repair_ok = repair_callback(last_failure_pkg, worker_context)
            except Exception as repair_exc:
                err = f"Repair callback raised exception: {repair_exc}"
                if run_manager:
                    latest = run_manager.store.get_run(resolved_run_id)
                    if latest and latest.state not in (RunState.COMPLETE, RunState.FAILED, RunState.CANCELLED):
                        try:
                            run_manager.store.transition_run(
                                resolved_run_id,
                                expected_version=latest.state_version,
                                target_state=RunState.FAILED,
                                last_error=err,
                                verification_result=evidence.to_dict(),
                            )
                        except Exception:
                            pass
                return RepairLoopResult(
                    success=False,
                    final_state=RunState.FAILED,
                    repair_rounds_completed=current_round,
                    final_evidence=evidence,
                    failure_package=last_failure_pkg,
                    error_message=err,
                )

            if not repair_ok:
                # Repair callback indicated unable to fix
                err = f"Repair callback returned failure on round {current_round}"
                if run_manager:
                    latest = run_manager.store.get_run(resolved_run_id)
                    if latest and latest.state not in (RunState.COMPLETE, RunState.FAILED, RunState.CANCELLED):
                        try:
                            run_manager.store.transition_run(
                                resolved_run_id,
                                expected_version=latest.state_version,
                                target_state=RunState.FAILED,
                                last_error=err,
                                verification_result=evidence.to_dict(),
                            )
                        except Exception:
                            pass
                return RepairLoopResult(
                    success=False,
                    final_state=RunState.FAILED,
                    repair_rounds_completed=current_round,
                    final_evidence=evidence,
                    failure_package=last_failure_pkg,
                    error_message=err,
                )

            # Repair succeeded, proceed to next verification round
            current_round += 1
            continue
        else:
            # Rounds exhausted or no repair callback
            break

    # If reached here, repair rounds exhausted or failed
    exhausted_err = f"Verification failed after {current_round} repair rounds: {last_evidence.error_message if last_evidence else 'unknown error'}"
    if run_manager:
        latest = run_manager.store.get_run(resolved_run_id)
        if latest and latest.state not in (RunState.COMPLETE, RunState.FAILED, RunState.CANCELLED):
            try:
                run_manager.store.transition_run(
                    resolved_run_id,
                    expected_version=latest.state_version,
                    target_state=RunState.FAILED,
                    last_error=exhausted_err,
                    verification_result=last_evidence.to_dict() if last_evidence else None,
                    repair_round=current_round,
                )
            except Exception:
                pass

    return RepairLoopResult(
        success=False,
        final_state=RunState.FAILED,
        repair_rounds_completed=current_round,
        final_evidence=last_evidence,
        failure_package=last_failure_pkg,
        error_message=exhausted_err,
    )
