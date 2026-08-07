"""Small, explicit orchestration layer for parallel Codex/agy work."""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Mapping, Sequence
from uuid import uuid4

from .agy_jobs import AgyJobRegistry, agy_jobs


_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_BASE_REF = re.compile(r"^[A-Za-z0-9_./:-]+$")
_MAX_TASKS = 4


@dataclass(frozen=True)
class CollaborationTask:
    task_id: str
    role: str
    prompt: str
    owned_paths: tuple[str, ...]
    forbidden_paths: tuple[str, ...]
    acceptance: tuple[str, ...]
    verification: tuple[str, ...]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "CollaborationTask":
        task_id = _required_string(raw, "id")
        if not _TASK_ID.fullmatch(task_id):
            raise ValueError(
                "task id must start with a letter or number and contain only "
                "letters, numbers, '.', '_' or '-'")

        prompt = _required_string(raw, "prompt")
        owned_paths = _path_list(raw, "owned_paths", required=True)
        forbidden_paths = _path_list(raw, "forbidden_paths", required=False)
        acceptance = _string_list(raw, "acceptance", required=True)
        verification = _string_list(raw, "verification", required=False)

        return cls(
            task_id=task_id,
            role=str(raw.get("role") or task_id),
            prompt=prompt,
            owned_paths=owned_paths,
            forbidden_paths=forbidden_paths,
            acceptance=acceptance,
            verification=verification,
        )


@dataclass
class _TaskRecord:
    spec: CollaborationTask
    workdir: Path
    branch: str
    job_id: str | None = None


@dataclass
class _SessionRecord:
    session_id: str
    project_dir: Path
    worktree_root: Path
    base_ref: str
    target_branch: str
    shared_contract: str
    display_mode: str
    max_tasks: int
    tasks: list[_TaskRecord]


class GitWorktreeManager:
    """Git adapter for the small set of worktree operations this module needs."""

    def _git(self, cwd: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
        return result.stdout.strip()

    def _git_succeeds(self, cwd: Path, *args: str) -> bool:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0

    def validate_project(self, project_dir: str) -> tuple[Path, str]:
        requested = Path(project_dir).expanduser().resolve()
        if not requested.is_dir():
            raise ValueError(f"project_dir is not an existing directory: {project_dir}")

        root = Path(self._git(requested, "rev-parse", "--show-toplevel")).resolve()
        if root != requested:
            raise ValueError(f"project_dir must be the Git repository root: {root}")
        target_branch = self._git(root, "branch", "--show-current") or " detached HEAD"
        return root, target_branch.strip()

    def validate_base_ref(self, project_dir: Path, base_ref: str) -> None:
        if not base_ref or not _BASE_REF.fullmatch(base_ref):
            raise ValueError("base_ref must be a simple Git ref such as HEAD or main")
        self._git(project_dir, "rev-parse", "--verify", f"{base_ref}^{{commit}}")

    def create(
        self,
        project_dir: Path,
        workdir: Path,
        branch: str,
        base_ref: str,
    ) -> None:
        if workdir.exists():
            raise ValueError(f"worktree path already exists: {workdir}")
        workdir.parent.mkdir(parents=True, exist_ok=True)
        self._git(project_dir, "worktree", "add", "-b", branch, str(workdir), base_ref)

    def inspect(self, project_dir: Path, workdir: Path, base_ref: str) -> dict[str, Any]:
        if not workdir.is_dir():
            return {"available": False, "error": "worktree directory is missing"}

        try:
            status = self._git(workdir, "status", "--porcelain")
            changed = self._git(workdir, "diff", "--name-only", f"{base_ref}...HEAD")
            uncommitted = self._git(workdir, "status", "--short")
        except RuntimeError as exc:
            return {"available": False, "error": str(exc)}

        return {
            "available": True,
            "dirty": bool(status),
            "diff_check": (
                "passed"
                if self._git_succeeds(workdir, "diff", "--check", f"{base_ref}...HEAD")
                and self._git_succeeds(workdir, "diff", "--check")
                else "failed"
            ),
            "changed_files": [line for line in changed.splitlines() if line],
            "uncommitted": [line for line in uncommitted.splitlines() if line],
        }


class CollaborationRegistry:
    """Track bounded collaboration sessions in the current bridge process."""

    def __init__(
        self,
        jobs: AgyJobRegistry = agy_jobs,
        worktrees: GitWorktreeManager | None = None,
    ) -> None:
        self._jobs = jobs
        self._worktrees = worktrees or GitWorktreeManager()
        self._sessions: dict[str, _SessionRecord] = {}
        self._lock = RLock()

    def start(
        self,
        project_dir: str,
        tasks: Sequence[Mapping[str, Any]],
        shared_contract: str = "",
        base_ref: str = "HEAD",
        worktree_root: str = "",
        timeout: float = 900.0,
        dangerously_skip_permissions: bool = False,
        display_mode: str = "headless",
        max_tasks: int = _MAX_TASKS,
    ) -> dict[str, Any]:
        if not tasks:
            raise ValueError("tasks must contain at least one task")
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        if not isinstance(shared_contract, str):
            raise ValueError("shared_contract must be a string")
        if display_mode not in {"headless", "terminal"}:
            raise ValueError("display_mode must be 'headless' or 'terminal'")
        if display_mode == "terminal" and sys.platform != "win32":
            raise ValueError("display_mode='terminal' is currently supported on Windows only")
        if isinstance(max_tasks, bool) or not isinstance(max_tasks, int):
            raise ValueError("max_tasks must be an integer between 1 and 4")
        if not 1 <= max_tasks <= _MAX_TASKS:
            raise ValueError("max_tasks must be between 1 and 4")

        if not isinstance(tasks, Sequence) or isinstance(tasks, (str, bytes)):
            raise ValueError("tasks must be a list of task objects")
        if any(not isinstance(task, Mapping) for task in tasks):
            raise ValueError("tasks must be a list of task objects")
        if len(tasks) > max_tasks:
            raise ValueError(
                f"received {len(tasks)} tasks, but max_tasks is {max_tasks}; "
                f"the hard limit is {_MAX_TASKS}"
            )
        specs = [CollaborationTask.from_mapping(task) for task in tasks]
        _validate_task_contract(specs)
        root, target_branch = self._worktrees.validate_project(project_dir)
        self._worktrees.validate_base_ref(root, base_ref)

        root_parent = root.parent
        session_root_base = (
            Path(worktree_root).expanduser().resolve()
            if worktree_root.strip()
            else root_parent / ".codex-agy-worktrees"
        )
        if _is_relative_to(session_root_base, root):
            raise ValueError("worktree_root must be outside the project directory")

        session_id = uuid4().hex[:12]
        session_root = session_root_base / session_id
        session_root.mkdir(parents=True, exist_ok=False)
        records: list[_TaskRecord] = []

        for spec in specs:
            branch = f"codex-agy/{session_id}/{spec.task_id}"
            workdir = session_root / spec.task_id
            self._worktrees.create(root, workdir, branch, base_ref)
            records.append(_TaskRecord(spec=spec, workdir=workdir, branch=branch))

        session = _SessionRecord(
            session_id=session_id,
            project_dir=root,
            worktree_root=session_root,
            base_ref=base_ref,
            target_branch=target_branch,
            shared_contract=shared_contract.strip(),
            display_mode=display_mode,
            max_tasks=max_tasks,
            tasks=records,
        )
        with self._lock:
            self._sessions[session_id] = session

        try:
            for record in records:
                record.job_id = self._jobs.start(
                    _build_prompt(session, record),
                    workdir=str(record.workdir),
                    timeout=timeout,
                    dangerously_skip_permissions=dangerously_skip_permissions,
                    display_mode=display_mode,
                )
        except Exception as exc:  # noqa: BLE001 - preserve the session for inspection.
            raise RuntimeError(
                f"collaboration session {session_id} was created but a task could not start: {exc}"
            ) from exc

        return self._snapshot(session, include_job_output=False)

    def status(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            return {
                "session_id": session_id,
                "state": "unknown",
                "error": "collaboration session not found",
            }
        return self._snapshot(session, include_job_output=True)

    def _snapshot(self, session: _SessionRecord, include_job_output: bool) -> dict[str, Any]:
        task_snapshots: list[dict[str, Any]] = []
        states: list[str] = []
        for record in session.tasks:
            if record.job_id is None:
                job_status: dict[str, Any] = {
                    "job_id": None,
                    "state": "failed",
                    "error": "task was not started",
                }
            else:
                job_status = self._jobs.status(record.job_id)
            state = str(job_status.get("state", "unknown"))
            states.append(state)
            task_snapshot: dict[str, Any] = {
                "id": record.spec.task_id,
                "role": record.spec.role,
                "state": state,
                "job_id": record.job_id,
                "branch": record.branch,
                "workdir": str(record.workdir),
                "owned_paths": list(record.spec.owned_paths),
                "acceptance": list(record.spec.acceptance),
                "verification": list(record.spec.verification),
                "acceptance_status": "manual",
                "worktree": self._worktrees.inspect(
                    session.project_dir, record.workdir, session.base_ref
                ),
            }
            if record.spec.forbidden_paths:
                task_snapshot["forbidden_paths"] = list(record.spec.forbidden_paths)
            if include_job_output:
                for key in ("text", "error", "exit_code", "used_pty"):
                    if key in job_status:
                        task_snapshot[key] = job_status[key]
            task_snapshots.append(task_snapshot)

        if any(state in {"failed", "unknown"} for state in states):
            state = "failed"
        elif states and all(state == "completed" for state in states):
            state = "ready_for_review"
        elif any(state == "running" for state in states):
            state = "running"
        else:
            state = "queued"

        return {
            "session_id": session.session_id,
            "state": state,
            "project_dir": str(session.project_dir),
            "base_ref": session.base_ref,
            "target_branch": session.target_branch,
            "worktree_root": str(session.worktree_root),
            "merge_policy": "manual",
            "shared_contract": session.shared_contract,
            "display_mode": session.display_mode,
            "max_tasks": session.max_tasks,
            "task_count": len(session.tasks),
            "next_step": (
                "Review each worktree, run acceptance checks, then merge branches manually."
            ),
            "tasks": task_snapshots,
        }


def _required_string(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"task {key} must be a non-empty string")
    return value.strip()


def _string_list(raw: Mapping[str, Any], key: str, required: bool) -> tuple[str, ...]:
    value = raw.get(key)
    if value is None and not required:
        return ()
    if not isinstance(value, list) or (required and not value):
        requirement = "a non-empty list" if required else "a list"
        raise ValueError(f"task {key} must be {requirement} of strings")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"task {key} must be a list of non-empty strings")
    return tuple(item.strip() for item in value)


def _path_list(raw: Mapping[str, Any], key: str, required: bool) -> tuple[str, ...]:
    values = _string_list(raw, key, required)
    normalized: list[str] = []
    for value in values:
        path = value.replace("\\", "/").strip("/")
        parts = path.split("/")
        if not path or path == "." or ".." in parts:
            raise ValueError(f"task {key} must contain relative paths without '..'")
        if re.match(r"^[A-Za-z]:", path):
            raise ValueError(f"task {key} must contain relative paths")
        normalized.append(path)
    return tuple(normalized)


def _validate_task_contract(specs: Sequence[CollaborationTask]) -> None:
    ids = [spec.task_id for spec in specs]
    if len(ids) != len(set(ids)):
        raise ValueError("task ids must be unique")
    for index, left in enumerate(specs):
        for right in specs[index + 1 :]:
            for left_path in left.owned_paths:
                for right_path in right.owned_paths:
                    if _paths_overlap(left_path, right_path):
                        raise ValueError(
                            f"owned path overlap between {left.task_id} and {right.task_id}: "
                            f"{left_path} / {right_path}"
                        )


def _paths_overlap(left: str, right: str) -> bool:
    return left == right or left.startswith(f"{right}/") or right.startswith(f"{left}/")


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _build_prompt(session: _SessionRecord, record: _TaskRecord) -> str:
    spec = record.spec
    forbidden = list(spec.forbidden_paths) + [
        path
        for other in session.tasks
        if other.spec.task_id != spec.task_id
        for path in other.spec.owned_paths
    ]
    lines = [
        "You are the AGY implementation agent in an explicit collaboration session.",
        f"Role: {spec.role}",
        f"Session: {session.session_id}",
        f"Worktree: {record.workdir}",
        "Task scope:",
        spec.prompt,
        f"Owned files: {', '.join(spec.owned_paths)}",
        f"Forbidden files: {', '.join(forbidden) if forbidden else 'all files outside the owned paths'}",
        "Shared contract:",
        session.shared_contract or "No additional shared contract was supplied.",
        "Acceptance criteria:",
        *[f"- {item}" for item in spec.acceptance],
        "Verification commands:",
        *[
            f"- {item}"
            for item in (spec.verification or ("Report the checks you ran.",))
        ],
        "Do not broaden the scope, edit forbidden files, or perform production or irreversible operations.",
        "Report changed files, tests, final status, and blockers when finished.",
    ]
    return "\n".join(lines)


agy_collaborations = CollaborationRegistry()
