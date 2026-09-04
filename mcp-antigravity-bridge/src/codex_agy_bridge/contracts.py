"""Typed contracts and state machines for VNext task execution and supervision."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
import hashlib
import math
from pathlib import Path, PurePosixPath
import re
from typing import Any


def _utc_now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


def _format_timestamp(val: str | datetime | float | int | None) -> str | None:
    """Normalize a timestamp to UTC ISO 8601 string."""
    if val is None:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=timezone.utc)
        return val.astimezone(timezone.utc).isoformat()
    if isinstance(val, bool):
        raise ValueError("Invalid timestamp type: bool")
    if isinstance(val, (int, float)):
        if isinstance(val, float) and not math.isfinite(val):
            raise ValueError(f"Invalid non-finite timestamp float: {val!r}")
        return datetime.fromtimestamp(val, tz=timezone.utc).isoformat()
    if isinstance(val, str):
        val_str = val.strip()
        if not val_str:
            return None
        return val_str
    raise ValueError(f"Invalid timestamp type: {type(val).__name__}")


class RiskClass(str, Enum):
    """Task risk classification."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    READ_ONLY = "READ_ONLY"
    CODE_CHANGES = "CODE_CHANGES"
    DESTRUCTIVE = "DESTRUCTIVE"
    PRODUCTION = "PRODUCTION"

    @classmethod
    def from_value(cls, val: str | RiskClass) -> RiskClass:
        if isinstance(val, cls):
            return val
        if not isinstance(val, str):
            raise ValueError(f"Invalid risk class type: {type(val).__name__}")
        norm = val.strip().upper().replace("-", "_").replace(" ", "_")
        for member in cls:
            if member.value == norm or member.name == norm:
                return member
        raise ValueError(f"Unknown risk class: {val!r}")


class AcceptanceState(str, Enum):
    """Explicit distinction between worker lifecycle and supervisor acceptance."""

    WORKER_RUNNING = "WORKER_RUNNING"
    WORKER_TERMINAL = "WORKER_TERMINAL"
    CANDIDATE_PENDING_REVIEW = "CANDIDATE_PENDING_REVIEW"
    CANDIDATE_REJECTED = "CANDIDATE_REJECTED"
    CANDIDATE_REPAIR_REQUIRED = "CANDIDATE_REPAIR_REQUIRED"
    ACCEPTED = "ACCEPTED"
    FAILED = "FAILED"
    HUMAN_DECISION_REQUIRED = "HUMAN_DECISION_REQUIRED"

    @classmethod
    def from_value(cls, val: str | "AcceptanceState") -> "AcceptanceState":
        if isinstance(val, cls):
            return val
        if not isinstance(val, str):
            raise ValueError(f"Invalid acceptance state type: {type(val).__name__}")
        norm = val.strip().upper().replace("-", "_").replace(" ", "_")
        for member in cls:
            if member.value == norm or member.name == norm:
                return member
        raise ValueError(f"Unknown acceptance state: {val!r}")


class AutoCommitPolicy(str, Enum):
    """Policy for automated git commits upon completion."""

    NEVER = "NEVER"
    VERIFIED_ONLY = "VERIFIED_ONLY"
    ALWAYS = "ALWAYS"

    @classmethod
    def from_value(cls, val: str | AutoCommitPolicy) -> AutoCommitPolicy:
        if isinstance(val, cls):
            return val
        if not isinstance(val, str):
            raise ValueError(f"Invalid auto commit policy type: {type(val).__name__}")
        norm = val.strip().upper().replace("-", "_").replace(" ", "_")
        for member in cls:
            if member.value == norm or member.name == norm:
                return member
        raise ValueError(f"Unknown auto commit policy: {val!r}")


class TimeoutClassification(str, Enum):
    """Explicit deterministic classification of timeout failures."""

    CONNECT_TIMEOUT = "CONNECT_TIMEOUT"
    REMOTE_EXECUTION_TIMEOUT = "REMOTE_EXECUTION_TIMEOUT"
    LOCAL_SUPERVISION_TIMEOUT = "LOCAL_SUPERVISION_TIMEOUT"
    AGY_PRINT_TIMEOUT = "AGY_PRINT_TIMEOUT"

    @classmethod
    def from_value(cls, val: str | TimeoutClassification) -> TimeoutClassification:
        if isinstance(val, cls):
            return val
        if not isinstance(val, str):
            raise ValueError(f"Invalid timeout classification type: {type(val).__name__}")
        norm = val.strip().upper().replace("-", "_").replace(" ", "_")
        for member in cls:
            if member.value == norm or member.name == norm:
                return member
        raise ValueError(f"Unknown timeout classification: {val!r}")


class RunState(str, Enum):
    """Exact lifecycle states for a VNext run."""

    CREATED = "CREATED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    REPAIRING = "REPAIRING"
    COMMITTING = "COMMITTING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    DECISION_REQUIRED = "DECISION_REQUIRED"
    ACCOUNT_SWITCH_REQUIRED = "ACCOUNT_SWITCH_REQUIRED"
    INTERRUPTED = "INTERRUPTED"
    RECOVERY_READY = "RECOVERY_READY"
    CANCELLED = "CANCELLED"

    @classmethod
    def from_value(cls, val: str | RunState) -> RunState:
        if isinstance(val, cls):
            return val
        if not isinstance(val, str):
            raise ValueError(f"Invalid run state type: {type(val).__name__}")
        norm = val.strip().upper().replace("-", "_").replace(" ", "_")
        for member in cls:
            if member.value == norm or member.name == norm:
                return member
        raise ValueError(f"Unknown run state: {val!r}")


class InvalidStateTransitionError(ValueError):
    """Raised when an invalid state transition is attempted."""

    pass


# Transition graph for guarded state progression
ALLOWED_TRANSITIONS: dict[RunState, set[RunState]] = {
    RunState.CREATED: {
        RunState.QUEUED,
        RunState.CANCELLED,
        RunState.FAILED,
        RunState.BLOCKED,
    },
    RunState.QUEUED: {
        RunState.RUNNING,
        RunState.CANCELLED,
        RunState.BLOCKED,
        RunState.FAILED,
        RunState.INTERRUPTED,
    },
    RunState.RUNNING: {
        RunState.VERIFYING,
        RunState.INTERRUPTED,
        RunState.BLOCKED,
        RunState.DECISION_REQUIRED,
        RunState.ACCOUNT_SWITCH_REQUIRED,
        RunState.FAILED,
        RunState.CANCELLED,
    },
    RunState.VERIFYING: {
        RunState.COMMITTING,
        RunState.REPAIRING,
        RunState.COMPLETE,
        RunState.FAILED,
        RunState.BLOCKED,
        RunState.DECISION_REQUIRED,
        RunState.INTERRUPTED,
        RunState.CANCELLED,
    },
    RunState.REPAIRING: {
        RunState.RUNNING,
        RunState.VERIFYING,
        RunState.FAILED,
        RunState.BLOCKED,
        RunState.INTERRUPTED,
        RunState.CANCELLED,
    },
    RunState.COMMITTING: {
        RunState.COMPLETE,
        RunState.FAILED,
        RunState.BLOCKED,
        RunState.INTERRUPTED,
    },
    RunState.DECISION_REQUIRED: {
        RunState.RUNNING,
        RunState.QUEUED,
        RunState.REPAIRING,
        RunState.CANCELLED,
        RunState.FAILED,
        RunState.BLOCKED,
    },
    RunState.ACCOUNT_SWITCH_REQUIRED: {
        RunState.RUNNING,
        RunState.QUEUED,
        RunState.CANCELLED,
        RunState.FAILED,
        RunState.BLOCKED,
    },
    RunState.INTERRUPTED: {
        RunState.RECOVERY_READY,
        RunState.CANCELLED,
        RunState.FAILED,
    },
    RunState.RECOVERY_READY: {
        RunState.QUEUED,
        RunState.RUNNING,
        RunState.CANCELLED,
        RunState.FAILED,
    },
    RunState.BLOCKED: {
        RunState.QUEUED,
        RunState.RUNNING,
        RunState.CANCELLED,
        RunState.FAILED,
    },
    RunState.COMPLETE: set(),
    RunState.FAILED: set(),
    RunState.CANCELLED: set(),
}

# Credential patterns to reject in contract or run data
CREDENTIAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(bearer\s+[a-z0-9_\-\.]{12,})"),
    re.compile(r"\b(ghp_[a-zA-Z0-9]{20,})"),
    re.compile(r"\b(gho_[a-zA-Z0-9]{20,})"),
    re.compile(r"\b(glpat-[a-zA-Z0-9\-]{20,})"),
    re.compile(r"\b(sk-[a-zA-Z0-9]{20,})"),
    re.compile(r"\b(AKIA[0-9A-Z]{16})\b"),
    re.compile(r"(?i)\b(password|passwd|api_key|apikey|secret_key|private_key)\s*[:=]\s*['\"]?[^\s'\"]{6,}['\"]?"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
)

# Documented deterministic upper bound for task runtime in seconds (24 hours)
MAX_TASK_RUNTIME_SECONDS: int = 86400

# Default task wall-clock budget in seconds (30 minutes)
TASK_WALL_CLOCK_BUDGET: int = 1800
DEFAULT_TASK_WALL_CLOCK_BUDGET: int = 1800


def validate_no_credentials(val: Any, field_name: str = "") -> None:
    """Recursively check that val does not contain credential-like secrets."""
    if isinstance(val, str):
        for pat in CREDENTIAL_PATTERNS:
            if pat.search(val):
                raise ValueError(
                    f"Credential-like content detected in field '{field_name}': "
                    f"matches security pattern {pat.pattern}"
                )
    elif isinstance(val, (list, tuple, set)):
        for idx, item in enumerate(val):
            validate_no_credentials(item, f"{field_name}[{idx}]")
    elif isinstance(val, dict):
        for k, v in val.items():
            validate_no_credentials(k, f"{field_name}.key({k})")
            validate_no_credentials(v, f"{field_name}.{k}")


def normalize_path(p: str | Path) -> str:
    """Normalize a path to a clean POSIX representation."""
    raw = p.as_posix() if isinstance(p, Path) else str(p)
    return PurePosixPath(raw.replace("\\", "/")).as_posix()


def normalize_paths(paths: list[str | Path] | tuple[str | Path, ...] | None) -> list[str]:
    """Normalize a collection of paths, removing duplicates while preserving order."""
    if not paths:
        return []
    normalized: list[str] = []
    for p in paths:
        if not isinstance(p, (str, Path)):
            raise ValueError(f"Expected str or Path, got {type(p).__name__}")
        norm = normalize_path(p).strip()
        if norm and norm not in normalized:
            normalized.append(norm)
    return normalized


@dataclass
class TaskContract:
    """Typed and validated specification for a delegated VNext task."""

    task_id: str
    objective: str
    base_head: str
    workdir: str
    allowed_paths: list[str] = field(default_factory=list)
    forbidden_paths: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    verification_commands: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    risk_class: RiskClass = RiskClass.CODE_CHANGES
    max_runtime: int | float = 1800
    max_repair_rounds: int = 2
    auto_commit_policy: AutoCommitPolicy = AutoCommitPolicy.VERIFIED_ONLY
    baseline_branch: str | None = None
    baseline_worktree_status: list[str] = field(default_factory=list)
    baseline_tracked_diff: list[str] = field(default_factory=list)
    baseline_file_hashes: dict[str, str] = field(default_factory=dict)
    per_wait_window: float | None = None
    task_total_timeout: float | None = None
    isolated_worktree: bool = False
    _frozen_digest: str | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Deterministically validate all TaskContract fields."""
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise ValueError("task_id must be a non-empty string")
        if not isinstance(self.objective, str) or not self.objective.strip():
            raise ValueError("objective must be a non-empty string")
        if not isinstance(self.base_head, str) or not self.base_head.strip():
            raise ValueError("base_head must be a non-empty string")

        if not isinstance(self.workdir, (str, Path)) or not str(self.workdir).strip():
            raise ValueError("workdir must be a non-empty string or Path")
        workdir_path = Path(self.workdir)
        if not workdir_path.is_absolute():
            raise ValueError(f"workdir must be an absolute path: {self.workdir!r}")
        self.workdir = workdir_path.as_posix()

        self.allowed_paths = normalize_paths(self.allowed_paths)
        self.forbidden_paths = normalize_paths(self.forbidden_paths)
        self.acceptance_criteria = [str(x).strip() for x in self.acceptance_criteria if str(x).strip()]
        self.verification_commands = [str(x).strip() for x in self.verification_commands if str(x).strip()]
        self.dependencies = [str(x).strip() for x in self.dependencies if str(x).strip()]

        self.risk_class = RiskClass.from_value(self.risk_class)
        self.auto_commit_policy = AutoCommitPolicy.from_value(self.auto_commit_policy)

        if self.baseline_branch is not None:
            if not isinstance(self.baseline_branch, str):
                raise ValueError("baseline_branch must be a string or None")
            self.baseline_branch = self.baseline_branch.strip() or None
        self.baseline_worktree_status = [str(x) for x in self.baseline_worktree_status]
        self.baseline_tracked_diff = normalize_paths(self.baseline_tracked_diff)
        if not isinstance(self.baseline_file_hashes, dict):
            raise ValueError("baseline_file_hashes must be a dictionary")
        self.baseline_file_hashes = {
            normalize_path(str(path)): str(value)
            for path, value in self.baseline_file_hashes.items()
        }
        for field_name, value in (
            ("per_wait_window", self.per_wait_window),
            ("task_total_timeout", self.task_total_timeout),
        ):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{field_name} must be a positive finite number or None: {value!r}")
        if not isinstance(self.isolated_worktree, bool):
            raise ValueError("isolated_worktree must be a boolean")

        if (
            isinstance(self.max_runtime, bool)
            or not isinstance(self.max_runtime, (int, float))
            or not math.isfinite(self.max_runtime)
            or self.max_runtime < 0
            or self.max_runtime > MAX_TASK_RUNTIME_SECONDS
        ):
            raise ValueError(
                f"max_runtime must be a non-negative number <= {MAX_TASK_RUNTIME_SECONDS}: {self.max_runtime!r}"
            )

        if isinstance(self.max_repair_rounds, bool) or not isinstance(self.max_repair_rounds, int) or self.max_repair_rounds < 0:
            raise ValueError(f"max_repair_rounds must be a non-negative integer: {self.max_repair_rounds!r}")

        # Security validation against credential leaks
        validate_no_credentials(self.task_id, "task_id")
        validate_no_credentials(self.objective, "objective")
        validate_no_credentials(self.base_head, "base_head")
        validate_no_credentials(self.workdir, "workdir")
        validate_no_credentials(self.allowed_paths, "allowed_paths")
        validate_no_credentials(self.forbidden_paths, "forbidden_paths")
        validate_no_credentials(self.acceptance_criteria, "acceptance_criteria")
        validate_no_credentials(self.verification_commands, "verification_commands")
        validate_no_credentials(self.dependencies, "dependencies")
        validate_no_credentials(self.baseline_branch, "baseline_branch")
        validate_no_credentials(self.baseline_worktree_status, "baseline_worktree_status")
        validate_no_credentials(self.baseline_tracked_diff, "baseline_tracked_diff")
        validate_no_credentials(self.baseline_file_hashes, "baseline_file_hashes")

    def _canonical_payload(self) -> dict[str, Any]:
        payload = self.to_dict()
        return payload

    def freeze(self) -> "TaskContract":
        """Freeze the validated contract and record a tamper-evident digest."""
        self.validate()
        encoded = json.dumps(self._canonical_payload(), sort_keys=True, separators=(",", ":"))
        self._frozen_digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        return self

    @property
    def is_frozen(self) -> bool:
        return self._frozen_digest is not None

    def assert_immutable(self) -> None:
        """Raise if a frozen contract was mutated after worker start."""
        if self._frozen_digest is None:
            return
        encoded = json.dumps(self._canonical_payload(), sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        if digest != self._frozen_digest:
            raise ValueError("TaskContract was mutated after it was frozen")

    def to_dict(self) -> dict[str, Any]:
        """Convert TaskContract to a JSON-safe dictionary."""
        return {
            "task_id": self.task_id,
            "objective": self.objective,
            "base_head": self.base_head,
            "workdir": self.workdir,
            "allowed_paths": list(self.allowed_paths),
            "forbidden_paths": list(self.forbidden_paths),
            "acceptance_criteria": list(self.acceptance_criteria),
            "verification_commands": list(self.verification_commands),
            "dependencies": list(self.dependencies),
            "risk_class": self.risk_class.value,
            "max_runtime": self.max_runtime,
            "max_repair_rounds": self.max_repair_rounds,
            "auto_commit_policy": self.auto_commit_policy.value,
            "baseline_branch": self.baseline_branch,
            "baseline_worktree_status": list(self.baseline_worktree_status),
            "baseline_tracked_diff": list(self.baseline_tracked_diff),
            "baseline_file_hashes": dict(self.baseline_file_hashes),
            "per_wait_window": self.per_wait_window,
            "task_total_timeout": self.task_total_timeout,
            "isolated_worktree": self.isolated_worktree,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskContract:
        """Create TaskContract from a dictionary."""
        if not isinstance(data, dict):
            raise ValueError(f"Expected dict, got {type(data).__name__}")
        return cls(
            task_id=data.get("task_id", ""),
            objective=data.get("objective", ""),
            base_head=data.get("base_head", ""),
            workdir=data.get("workdir", ""),
            allowed_paths=data.get("allowed_paths", []),
            forbidden_paths=data.get("forbidden_paths", []),
            acceptance_criteria=data.get("acceptance_criteria", []),
            verification_commands=data.get("verification_commands", []),
            dependencies=data.get("dependencies", []),
            risk_class=RiskClass.from_value(data.get("risk_class", RiskClass.CODE_CHANGES)),
            max_runtime=data.get("max_runtime", 1800),
            max_repair_rounds=data.get("max_repair_rounds", 2),
            auto_commit_policy=AutoCommitPolicy.from_value(
                data.get("auto_commit_policy", AutoCommitPolicy.VERIFIED_ONLY)
            ),
            baseline_branch=data.get("baseline_branch"),
            baseline_worktree_status=data.get("baseline_worktree_status", []),
            baseline_tracked_diff=data.get("baseline_tracked_diff", []),
            baseline_file_hashes=data.get("baseline_file_hashes", {}),
            per_wait_window=data.get("per_wait_window"),
            task_total_timeout=data.get("task_total_timeout"),
            isolated_worktree=bool(data.get("isolated_worktree", False)),
        )

    def to_json(self, **kwargs: Any) -> str:
        """Serialize TaskContract to JSON string."""
        return json.dumps(self.to_dict(), **kwargs)

    @classmethod
    def from_json(cls, json_str: str) -> TaskContract:
        """Deserialize TaskContract from JSON string."""
        return cls.from_dict(json.loads(json_str))


@dataclass
class RunRecord:
    """Typed execution record tracking state and lifecycle transitions for a run."""

    run_id: str
    task_id: str
    state: RunState = RunState.CREATED
    state_version: int = 1
    pid: int | None = None
    heartbeat: str | None = None
    created_at: str = field(default_factory=_utc_now_iso)
    started_at: str | None = None
    updated_at: str = field(default_factory=_utc_now_iso)
    worktree: str | None = None
    repo: str | None = None
    base_head: str | None = None
    current_head: str | None = None
    attempt: int = 0
    repair_round: int = 0
    verification_result: Any | None = None
    result_summary: str | None = None
    commit_sha: str | None = None
    last_error: str | None = None
    suspended_reason: str | None = None

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Deterministically validate RunRecord fields."""
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise ValueError("run_id must be a non-empty string")
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise ValueError("task_id must be a non-empty string")

        self.state = RunState.from_value(self.state)

        if isinstance(self.state_version, bool) or not isinstance(self.state_version, int) or self.state_version < 1:
            raise ValueError(f"state_version must be an integer >= 1: {self.state_version!r}")

        if self.pid is not None and (isinstance(self.pid, bool) or not isinstance(self.pid, int) or self.pid < 0):
            raise ValueError(f"pid must be a non-negative integer or None: {self.pid!r}")

        if isinstance(self.attempt, bool) or not isinstance(self.attempt, int) or self.attempt < 0:
            raise ValueError(f"attempt must be a non-negative integer: {self.attempt!r}")

        if isinstance(self.repair_round, bool) or not isinstance(self.repair_round, int) or self.repair_round < 0:
            raise ValueError(f"repair_round must be a non-negative integer: {self.repair_round!r}")

        self.heartbeat = _format_timestamp(self.heartbeat)
        self.created_at = _format_timestamp(self.created_at) or _utc_now_iso()
        self.started_at = _format_timestamp(self.started_at)
        self.updated_at = _format_timestamp(self.updated_at) or _utc_now_iso()

        if self.worktree is not None:
            self.worktree = normalize_path(self.worktree)
        if self.repo is not None:
            self.repo = normalize_path(self.repo)

        validate_no_credentials(self.run_id, "run_id")
        validate_no_credentials(self.task_id, "task_id")
        validate_no_credentials(self.worktree, "worktree")
        validate_no_credentials(self.repo, "repo")
        validate_no_credentials(self.base_head, "base_head")
        validate_no_credentials(self.current_head, "current_head")
        validate_no_credentials(self.commit_sha, "commit_sha")
        validate_no_credentials(self.verification_result, "verification_result")
        validate_no_credentials(self.result_summary, "result_summary")
        validate_no_credentials(self.last_error, "last_error")
        validate_no_credentials(self.suspended_reason, "suspended_reason")
        validate_no_credentials(self.heartbeat, "heartbeat")
        validate_no_credentials(self.created_at, "created_at")
        validate_no_credentials(self.started_at, "started_at")
        validate_no_credentials(self.updated_at, "updated_at")

    def transition_to(
        self,
        new_state: RunState | str,
        *,
        verification_result: Any = None,
        result_summary: str | None = None,
        commit_sha: str | None = None,
        last_error: str | None = None,
        suspended_reason: str | None = None,
        current_head: str | None = None,
        repair_round: int | None = None,
        attempt: int | None = None,
        pid: int | None = None,
        timestamp: str | datetime | None = None,
    ) -> RunRecord:
        """Monotonically transition to a new valid state, incrementing state_version."""
        target_state = RunState.from_value(new_state)
        current_state = self.state

        allowed = ALLOWED_TRANSITIONS.get(current_state, set())
        if target_state not in allowed:
            raise InvalidStateTransitionError(
                f"Invalid state transition from {current_state.value} to {target_state.value}. "
                f"Allowed transitions from {current_state.value}: {[s.value for s in allowed]}"
            )

        # Enforce guarded verification check for COMPLETE state
        if target_state == RunState.COMPLETE:
            if current_state in (RunState.CREATED, RunState.QUEUED, RunState.RUNNING):
                raise InvalidStateTransitionError(
                    f"COMPLETE cannot be reached directly from {current_state.value}; "
                    f"must go through VERIFYING or COMMITTING."
                )

            effective_verification = (
                verification_result if verification_result is not None else self.verification_result
            )
            if effective_verification is None:
                raise InvalidStateTransitionError(
                    "Transition to COMPLETE requires a successful verification_result, but none was provided."
                )

            # Check verification result validity
            if isinstance(effective_verification, dict):
                if effective_verification.get("passed") is False or effective_verification.get("success") is False:
                    raise InvalidStateTransitionError(
                        "Transition to COMPLETE failed: verification_result indicated failure (passed/success is False)."
                    )
                if effective_verification.get("status") in ("failed", "error", "blocked"):
                    raise InvalidStateTransitionError(
                        f"Transition to COMPLETE failed: verification_result status is {effective_verification.get('status')}."
                    )
                if effective_verification.get("returncode", 0) != 0:
                    raise InvalidStateTransitionError(
                        f"Transition to COMPLETE failed: verification returncode is {effective_verification.get('returncode')}."
                    )
            elif isinstance(effective_verification, bool) and not effective_verification:
                raise InvalidStateTransitionError(
                    "Transition to COMPLETE failed: verification_result is False."
                )

        # Apply state transition
        self.state = target_state
        self.state_version += 1
        now_ts = _format_timestamp(timestamp) or _utc_now_iso()
        self.updated_at = now_ts

        if target_state == RunState.RUNNING and self.started_at is None:
            self.started_at = now_ts

        if verification_result is not None:
            self.verification_result = verification_result
        if result_summary is not None:
            self.result_summary = result_summary
        if commit_sha is not None:
            self.commit_sha = commit_sha
        if last_error is not None:
            self.last_error = last_error
        if suspended_reason is not None:
            self.suspended_reason = suspended_reason
        if current_head is not None:
            self.current_head = current_head
        if repair_round is not None:
            self.repair_round = repair_round
        if attempt is not None:
            self.attempt = attempt
        if pid is not None:
            self.pid = pid

        self.validate()
        return self

    def to_dict(self) -> dict[str, Any]:
        """Convert RunRecord to a JSON-safe dictionary."""
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "state": self.state.value,
            "state_version": self.state_version,
            "pid": self.pid,
            "heartbeat": self.heartbeat,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "worktree": self.worktree,
            "repo": self.repo,
            "base_head": self.base_head,
            "current_head": self.current_head,
            "attempt": self.attempt,
            "repair_round": self.repair_round,
            "verification_result": self.verification_result,
            "result_summary": self.result_summary,
            "commit_sha": self.commit_sha,
            "last_error": self.last_error,
            "suspended_reason": self.suspended_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunRecord:
        """Create RunRecord from a dictionary."""
        if not isinstance(data, dict):
            raise ValueError(f"Expected dict, got {type(data).__name__}")
        return cls(
            run_id=data.get("run_id", ""),
            task_id=data.get("task_id", ""),
            state=RunState.from_value(data.get("state", RunState.CREATED)),
            state_version=data.get("state_version", 1),
            pid=data.get("pid"),
            heartbeat=data.get("heartbeat"),
            created_at=data.get("created_at", _utc_now_iso()),
            started_at=data.get("started_at"),
            updated_at=data.get("updated_at", _utc_now_iso()),
            worktree=data.get("worktree"),
            repo=data.get("repo"),
            base_head=data.get("base_head"),
            current_head=data.get("current_head"),
            attempt=data.get("attempt", 0),
            repair_round=data.get("repair_round", 0),
            verification_result=data.get("verification_result"),
            result_summary=data.get("result_summary"),
            commit_sha=data.get("commit_sha"),
            last_error=data.get("last_error"),
            suspended_reason=data.get("suspended_reason"),
        )

    def to_json(self, **kwargs: Any) -> str:
        """Serialize RunRecord to JSON string."""
        return json.dumps(self.to_dict(), **kwargs)

    @classmethod
    def from_json(cls, json_str: str) -> RunRecord:
        """Deserialize RunRecord from JSON string."""
        return cls.from_dict(json.loads(json_str))
