"""Independent candidate acceptance, baseline snapshots, and scope policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
from pathlib import Path
import subprocess
from typing import Any, Sequence

from .contracts import AcceptanceState, RiskClass, TaskContract, normalize_path


class WorkerTerminalReason(str, Enum):
    """Worker completion reasons used by the timeout-aware acceptance gate."""

    COMPLETED = "COMPLETED"
    HARD_TIMEOUT = "HARD_TIMEOUT"
    FAILED = "FAILED"


def effective_risk_class(value: RiskClass | str) -> RiskClass:
    """Map legacy contract labels onto the explicit LOW/MEDIUM/HIGH model."""
    risk = RiskClass.from_value(value)
    return {
        RiskClass.READ_ONLY: RiskClass.LOW,
        RiskClass.CODE_CHANGES: RiskClass.MEDIUM,
        RiskClass.DESTRUCTIVE: RiskClass.HIGH,
        RiskClass.PRODUCTION: RiskClass.HIGH,
    }.get(risk, risk)


@dataclass
class NoProgressGuard:
    """Track repeated identical failures so continuation cannot become blind retry."""

    identical_no_progress_failure_count: int = 0
    _last_signature: tuple[str, str, str] | None = field(default=None, init=False, repr=False)

    def observe(self, *, failure: str, diff: str, blocker: str) -> bool:
        """Return True after two consecutive observations with no new evidence."""
        signature = (str(failure), str(diff), str(blocker))
        if signature == self._last_signature:
            self.identical_no_progress_failure_count += 1
        else:
            self.identical_no_progress_failure_count = 1
            self._last_signature = signature
        return self.identical_no_progress_failure_count >= 2


def should_stop_blind_retry(
    observations: Sequence[tuple[str, str, str]],
) -> bool:
    """Check the policy using the final two failure/diff/blocker observations."""
    if len(observations) < 2:
        return False
    return observations[-1] == observations[-2]


@dataclass(frozen=True)
class BaselineSnapshot:
    """Immutable evidence captured before a candidate worker is started."""

    head: str
    branch: str | None
    worktree_status: tuple[str, ...] = ()
    tracked_diff: tuple[str, ...] = ()
    file_hashes: dict[str, str] = field(default_factory=dict)
    isolated_worktree: bool = False
    captured_at: str = ""

    @property
    def clean(self) -> bool:
        return not self.worktree_status and not self.tracked_diff

    @property
    def changed_files(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self.worktree_status, *self.tracked_diff)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "head": self.head,
            "branch": self.branch,
            "worktree_status": list(self.worktree_status),
            "tracked_diff": list(self.tracked_diff),
            "file_hashes": dict(self.file_hashes),
            "isolated_worktree": self.isolated_worktree,
            "captured_at": self.captured_at,
        }


@dataclass(frozen=True)
class ScopeAudit:
    """Post-worker diff audit independent of the worker's report."""

    changed_files: tuple[str, ...] = ()
    out_of_scope_files: tuple[str, ...] = ()
    forbidden_files: tuple[str, ...] = ()
    baseline_overlap_files: tuple[str, ...] = ()
    diff_check_passed: bool = True
    baseline_clean: bool = True
    isolated_worktree: bool = False
    auto_restorable_files: tuple[str, ...] = ()
    violations: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.violations and self.diff_check_passed

    @property
    def scope_violation(self) -> bool:
        return bool(self.out_of_scope_files or self.forbidden_files or self.baseline_overlap_files)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "changed_files": list(self.changed_files),
            "out_of_scope_files": list(self.out_of_scope_files),
            "forbidden_files": list(self.forbidden_files),
            "baseline_overlap_files": list(self.baseline_overlap_files),
            "diff_check_passed": self.diff_check_passed,
            "baseline_clean": self.baseline_clean,
            "isolated_worktree": self.isolated_worktree,
            "auto_restorable_files": list(self.auto_restorable_files),
            "violations": list(self.violations),
        }


@dataclass(frozen=True)
class CandidateAcceptance:
    """Result of independent supervisor acceptance of a worker candidate."""

    worker_result: WorkerTerminalReason
    acceptance: AcceptanceState
    accepted: bool
    risk_class: str
    scope_audit: ScopeAudit
    independently_verified: bool
    reasons: tuple[str, ...] = ()

    @property
    def task_accepted(self) -> bool:
        return self.accepted and self.acceptance == AcceptanceState.ACCEPTED

    def to_dict(self) -> dict[str, Any]:
        return {
            "worker_result": self.worker_result.value,
            "acceptance": self.acceptance.value,
            "accepted": self.accepted,
            "task_accepted": self.task_accepted,
            "risk_class": self.risk_class,
            "scope_audit": self.scope_audit.to_dict(),
            "independently_verified": self.independently_verified,
            "reasons": list(self.reasons),
        }


def _git(workdir: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=str(workdir), capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or f"git {args[0]} failed")
    return result.stdout


def _paths_from_status(output: str) -> list[str]:
    paths: list[str] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        value = line[3:].strip() if len(line) >= 3 else line.strip()
        if " -> " in value:
            value = value.split(" -> ")[-1].strip()
        value = value.strip('"')
        path = normalize_path(value)
        if path and path not in paths:
            paths.append(path)
    return paths


def _path_list(output: str) -> list[str]:
    return [normalize_path(line.strip()) for line in output.splitlines() if line.strip()]


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def capture_baseline_snapshot(
    workdir: str | Path,
    *,
    isolated_worktree: bool = False,
    include_hashes: bool = True,
) -> BaselineSnapshot:
    """Capture HEAD, status, tracked diff, and optional file hashes before spawning."""
    root = Path(workdir).expanduser().resolve()
    head = _git(root, "rev-parse", "HEAD").strip()
    branch = _git(root, "branch", "--show-current").strip() or None
    status_raw = _git(root, "status", "--porcelain", "-uall")
    tracked_raw = _git(root, "diff", "--name-only", "HEAD")
    status_paths = _paths_from_status(status_raw)
    tracked_paths = _path_list(tracked_raw)
    hashes: dict[str, str] = {}
    if include_hashes:
        # Only pre-existing dirty paths need content ownership evidence.  Clean
        # tracked files can be restored exactly from baseline.head, so hashing
        # every file would add an unacceptable start-up delay on large repos.
        for path in status_paths:
            candidate = root / path
            if candidate.is_file():
                try:
                    hashes[path] = _hash_file(candidate)
                except OSError:
                    continue
    return BaselineSnapshot(
        head=head,
        branch=branch,
        worktree_status=tuple(status_paths),
        tracked_diff=tuple(tracked_paths),
        file_hashes=hashes,
        isolated_worktree=isolated_worktree,
        captured_at=datetime.now(timezone.utc).isoformat(),
    )


def _current_changed_files(root: Path, baseline: BaselineSnapshot) -> list[str]:
    paths: list[str] = []
    try:
        paths.extend(_path_list(_git(root, "diff", "--name-only", baseline.head)))
        paths.extend(_path_list(_git(root, "diff", "--cached", "--name-only")))
        paths.extend(_paths_from_status(_git(root, "status", "--porcelain", "-uall")))
    except RuntimeError:
        return []
    return list(dict.fromkeys(path for path in paths if path))


def _matches(path: str, patterns: Sequence[str]) -> bool:
    normalized = normalize_path(path).lstrip("./")
    for pattern in patterns:
        candidate = normalize_path(pattern).lstrip("./").rstrip("/")
        if normalized == candidate or normalized.startswith(candidate + "/"):
            return True
        from fnmatch import fnmatch

        if fnmatch(normalized, candidate):
            return True
    return False


def audit_candidate_scope(
    contract: TaskContract,
    workdir: str | Path | None = None,
    baseline: BaselineSnapshot | None = None,
) -> ScopeAudit:
    """Audit candidate changes against the frozen baseline and contract paths."""
    root = Path(workdir or contract.workdir).expanduser().resolve()
    snapshot = baseline or capture_baseline_snapshot(root)
    changed = _current_changed_files(root, snapshot)
    baseline_changed = set(snapshot.changed_files)
    baseline_overlap: list[str] = []
    out_of_scope: list[str] = []
    forbidden: list[str] = []
    for path in changed:
        current_hash = None
        candidate = root / path
        if candidate.is_file():
            try:
                current_hash = _hash_file(candidate)
            except OSError:
                pass
        worker_changed = path not in snapshot.file_hashes or current_hash != snapshot.file_hashes.get(path)
        if not worker_changed and path in baseline_changed:
            continue
        if path in baseline_changed:
            baseline_overlap.append(path)
        if contract.forbidden_paths and _matches(path, contract.forbidden_paths):
            forbidden.append(path)
        if contract.allowed_paths and not _matches(path, contract.allowed_paths):
            out_of_scope.append(path)

    violations: list[str] = []
    for path in out_of_scope:
        violations.append(f"Changed file '{path}' is not in contract.allowed_paths: {contract.allowed_paths}")
    for path in forbidden:
        violations.append(f"Changed file '{path}' matches contract.forbidden_paths: {contract.forbidden_paths}")
    for path in baseline_overlap:
        violations.append(f"Baseline dirty file '{path}' was modified; ownership is ambiguous")

    diff_check_passed = True
    try:
        if _git(root, "diff", "--check", baseline.head).strip():
            diff_check_passed = False
        if _git(root, "diff", "--cached", "--check").strip():
            diff_check_passed = False
    except RuntimeError:
        diff_check_passed = False
    if not diff_check_passed:
        violations.append("git diff --check detected whitespace or conflict-marker errors")

    auto_restorable: list[str] = []
    if snapshot.isolated_worktree and snapshot.clean:
        for path in out_of_scope:
            tracked = subprocess.run(
                ["git", "cat-file", "-e", f"{snapshot.head}:{path}"],
                cwd=str(root),
                capture_output=True,
                text=True,
                check=False,
            ).returncode == 0
            if tracked and (root / path).is_file():
                auto_restorable.append(path)
    return ScopeAudit(
        changed_files=tuple(changed),
        out_of_scope_files=tuple(dict.fromkeys(out_of_scope)),
        forbidden_files=tuple(dict.fromkeys(forbidden)),
        baseline_overlap_files=tuple(dict.fromkeys(baseline_overlap)),
        diff_check_passed=diff_check_passed,
        baseline_clean=snapshot.clean,
        isolated_worktree=snapshot.isolated_worktree,
        auto_restorable_files=tuple(auto_restorable),
        violations=tuple(dict.fromkeys(violations)),
    )


def evaluate_candidate(
    *,
    worker_result: WorkerTerminalReason | str,
    scope_audit: ScopeAudit,
    independently_verified: bool,
    risk_class: RiskClass | str = RiskClass.MEDIUM,
    worker_alive: bool = False,
) -> CandidateAcceptance:
    """Convert worker evidence into an explicit, independently reviewed acceptance."""
    reason: list[str] = []
    terminal = WorkerTerminalReason(worker_result)
    risk = effective_risk_class(risk_class)
    if worker_alive:
        return CandidateAcceptance(terminal, AcceptanceState.CANDIDATE_PENDING_REVIEW, False, risk.value, scope_audit, False, ("Worker is still live; continue reconciliation",))
    if not scope_audit.passed:
        reason.append("Scope or diff audit failed")
        return CandidateAcceptance(terminal, AcceptanceState.CANDIDATE_REJECTED, False, risk.value, scope_audit, independently_verified, tuple(reason))
    if terminal == WorkerTerminalReason.FAILED:
        return CandidateAcceptance(terminal, AcceptanceState.FAILED, False, risk.value, scope_audit, independently_verified, ("Worker reported failure",))
    if terminal == WorkerTerminalReason.HARD_TIMEOUT:
        if risk in {RiskClass.HIGH, RiskClass.MEDIUM, RiskClass.DESTRUCTIVE, RiskClass.PRODUCTION}:
            return CandidateAcceptance(terminal, AcceptanceState.CANDIDATE_REJECTED, False, risk.value, scope_audit, independently_verified, ("Timed-out partials require rejection for this risk class",))
        if not independently_verified:
            return CandidateAcceptance(terminal, AcceptanceState.CANDIDATE_PENDING_REVIEW, False, risk.value, scope_audit, False, ("Timed-out partial requires independent verification",))
    if not independently_verified:
        return CandidateAcceptance(terminal, AcceptanceState.CANDIDATE_PENDING_REVIEW, False, risk.value, scope_audit, False, ("Worker output is a candidate, not independent acceptance",))
    return CandidateAcceptance(terminal, AcceptanceState.ACCEPTED, True, risk.value, scope_audit, True, ())


def restore_safe_out_of_scope_files(
    contract: TaskContract,
    workdir: str | Path,
    baseline: BaselineSnapshot,
    audit: ScopeAudit,
) -> tuple[str, ...]:
    """Restore only proven worker-only tracked files; never remove ambiguous/untracked files."""
    if not baseline.isolated_worktree or not baseline.clean:
        return ()
    if not audit.auto_restorable_files:
        return ()
    root = Path(workdir).expanduser().resolve()
    subprocess.run(
        ["git", "restore", "--source", baseline.head, "--worktree", "--", *audit.auto_restorable_files],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=True,
    )
    return audit.auto_restorable_files
