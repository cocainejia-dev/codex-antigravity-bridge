"""Controller-owned pre-execution task shaping and plan validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from .contracts import AutoCommitPolicy, RiskClass, TaskContract, normalize_path


TASK_BASE_HEAD_RESOLUTION = "AT_EXECUTION_TIME_FROM_LAST_ACCEPTED_STATE"
VALID_COST_CLASSES = {"CHEAP", "MODERATE", "EXPENSIVE"}
VALID_SIZE_CLASSES = {"SMALL", "MEDIUM", "LARGE"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ids(values: list[Any], prefix: str) -> list[str]:
    return [str(item.get("id") or f"{prefix}{index}") if isinstance(item, dict) else f"{prefix}{index}" for index, item in enumerate(values, 1)]


def _risk(value: Any) -> RiskClass:
    return RiskClass.from_value(value or RiskClass.CODE_CHANGES)


def _risk_level(value: RiskClass | str) -> int:
    risk = _risk(value)
    return {RiskClass.LOW: 1, RiskClass.READ_ONLY: 1, RiskClass.MEDIUM: 2, RiskClass.CODE_CHANGES: 2, RiskClass.HIGH: 3, RiskClass.DESTRUCTIVE: 3, RiskClass.PRODUCTION: 3}[risk]


@dataclass
class ShapedTask:
    task_id: str
    objective: str
    acceptance_criteria: list[str]
    allowed_paths: list[str]
    forbidden_paths: list[str]
    verification_commands: list[str]
    risk_class: RiskClass
    dependencies: list[str] = field(default_factory=list)
    covers_objectives: list[str] = field(default_factory=list)
    covers_acceptance: list[str] = field(default_factory=list)
    path_ownership_reason: str = ""
    shared_path: bool = False
    dependency_order_required: bool = False
    verification_cost_class: str = "MODERATE"
    size_class: str = "SMALL"
    shaping_warning: bool = False
    base_head_resolution: str = "INITIAL_BASE_HEAD"
    isolated_worktree: bool = False
    max_runtime: int | float = 1800
    max_repair_rounds: int = 2

    def to_dict(self, *, base_head: str, workdir: str) -> dict[str, Any]:
        preview_head = base_head if self.base_head_resolution == "INITIAL_BASE_HEAD" else base_head
        contract = TaskContract(
            task_id=self.task_id,
            objective=self.objective,
            base_head=preview_head,
            workdir=workdir,
            allowed_paths=list(self.allowed_paths),
            forbidden_paths=list(self.forbidden_paths),
            acceptance_criteria=list(self.acceptance_criteria),
            verification_commands=list(self.verification_commands),
            dependencies=list(self.dependencies),
            risk_class=self.risk_class,
            isolated_worktree=self.isolated_worktree,
            max_runtime=self.max_runtime,
            max_repair_rounds=self.max_repair_rounds,
            auto_commit_policy=AutoCommitPolicy.VERIFIED_ONLY,
        ).freeze()
        payload = {
            "task_id": self.task_id,
            "objective": self.objective,
            "acceptance_criteria": list(self.acceptance_criteria),
            "allowed_paths": list(self.allowed_paths),
            "forbidden_paths": list(self.forbidden_paths),
            "verification_commands": list(self.verification_commands),
            "risk_class": self.risk_class.value,
            "dependencies": list(self.dependencies),
            "covers_objectives": list(self.covers_objectives),
            "covers_acceptance": list(self.covers_acceptance),
            "path_ownership_reason": self.path_ownership_reason,
            "shared_path": self.shared_path,
            "dependency_order_required": self.dependency_order_required,
            "verification_cost_class": self.verification_cost_class,
            "size_class": self.size_class,
            "shaping_warning": self.shaping_warning,
            "base_head_resolution": self.base_head_resolution,
            "isolated_worktree": self.isolated_worktree,
            "task_contract": contract.to_dict(),
            "task_contract_digest": contract._frozen_digest,
            "task_contract_frozen": contract.is_frozen,
        }
        return payload


@dataclass
class TaskPlanValidation:
    valid: bool
    reasons: list[str] = field(default_factory=list)
    objective_coverage: float = 0.0
    acceptance_coverage: float = 0.0
    dag_valid: bool = False
    risk_partition: bool = False
    path_ownership: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_valid": self.valid,
            "reasons": list(self.reasons),
            "objective_coverage": self.objective_coverage,
            "acceptance_coverage": self.acceptance_coverage,
            "dag_valid": self.dag_valid,
            "risk_partition": self.risk_partition,
            "path_ownership": self.path_ownership,
        }


@dataclass
class TaskPlan:
    plan_id: str
    parent_objective: str
    parent_acceptance: list[str]
    base_head: str
    workdir: str
    created_at: str
    shaping_reason: str
    shaping_required: bool
    tasks: list[ShapedTask]
    dependency_edges: list[dict[str, str]]
    objective_coverage: dict[str, list[str]]
    acceptance_coverage: dict[str, list[str]]
    path_ownership: list[dict[str, Any]]
    risk_summary: dict[str, Any]
    verification_summary: dict[str, Any]
    final_verification: list[str]
    _plan_digest: str | None = field(default=None, init=False, repr=False)

    def _payload(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "parent_objective": self.parent_objective,
            "parent_acceptance": list(self.parent_acceptance),
            "base_head": self.base_head,
            "created_at": self.created_at,
            "shaping_reason": self.shaping_reason,
            "shaping_required": self.shaping_required,
            "workdir": self.workdir,
            "tasks": [task.to_dict(base_head=self.base_head, workdir=self.workdir) for task in self.tasks],
            "dependency_edges": list(self.dependency_edges),
            "objective_coverage": dict(self.objective_coverage),
            "acceptance_coverage": dict(self.acceptance_coverage),
            "path_ownership": list(self.path_ownership),
            "risk_summary": dict(self.risk_summary),
            "verification_summary": dict(self.verification_summary),
            "final_verification": list(self.final_verification),
            "execution_mode": "SERIAL",
            "parallel_eligible": self.parallel_eligible,
        }

    @property
    def parallel_eligible(self) -> bool:
        return not any(task.dependencies or task.shared_path for task in self.tasks)

    @property
    def plan_digest(self) -> str | None:
        return self._plan_digest

    def freeze(self) -> "TaskPlan":
        validation = validate_task_plan(self, allow_unfrozen=True)
        if not validation.valid:
            raise ValueError("Invalid TaskPlan: " + "; ".join(validation.reasons))
        encoded = json.dumps(self._payload(), sort_keys=True, separators=(",", ":"))
        self._plan_digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        return self

    @property
    def is_frozen(self) -> bool:
        return self._plan_digest is not None

    def assert_immutable(self) -> None:
        if self._plan_digest is None:
            return
        encoded = json.dumps(self._payload(), sort_keys=True, separators=(",", ":"))
        if hashlib.sha256(encoded.encode("utf-8")).hexdigest() != self._plan_digest:
            raise ValueError("TaskPlan was mutated after it was frozen")

    def to_dict(self) -> dict[str, Any]:
        self.assert_immutable()
        payload = self._payload()
        payload["plan_digest"] = self._plan_digest
        payload["validation"] = validate_task_plan(self).to_dict()
        return payload


def validate_task_plan(plan: TaskPlan, *, allow_unfrozen: bool = False) -> TaskPlanValidation:
    reasons: list[str] = []
    task_ids = [task.task_id for task in plan.tasks]
    if not plan.tasks:
        reasons.append("TaskPlan must contain at least one task")
    if len(task_ids) != len(set(task_ids)):
        reasons.append("Task IDs must be unique")
    known = set(task_ids)
    for task in plan.tasks:
        if not task.objective.strip():
            reasons.append(f"{task.task_id} has no objective")
        if not task.acceptance_criteria:
            reasons.append(f"{task.task_id} has no acceptance criteria")
        if not task.allowed_paths and task.verification_commands:
            reasons.append(f"{task.task_id} has verification but no allowed_paths")
        for dependency in task.dependencies:
            if dependency not in known:
                reasons.append(f"{task.task_id} depends on unknown task {dependency}")
        if task.verification_cost_class not in VALID_COST_CLASSES:
            reasons.append(f"{task.task_id} has invalid verification cost class")
        if task.size_class not in VALID_SIZE_CLASSES:
            reasons.append(f"{task.task_id} has invalid size class")

    visiting: set[str] = set()
    visited: set[str] = set()
    dag_valid = True

    def visit(task_id: str) -> None:
        nonlocal dag_valid
        if task_id in visiting:
            dag_valid = False
            return
        if task_id in visited:
            return
        visiting.add(task_id)
        task = next((item for item in plan.tasks if item.task_id == task_id), None)
        if task:
            for dependency in task.dependencies:
                visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in task_ids:
        visit(task_id)
    if not dag_valid:
        reasons.append("TaskPlan dependency graph contains a cycle")

    objective_ids = set(plan.objective_coverage)
    covered_objectives = {objective for task in plan.tasks for objective in task.covers_objectives}
    acceptance_ids = set(plan.acceptance_coverage)
    covered_acceptance = {criterion for task in plan.tasks for criterion in task.covers_acceptance}
    objective_coverage = 100.0 if not objective_ids else 100.0 * len(objective_ids & covered_objectives) / len(objective_ids)
    acceptance_coverage = 100.0 if not acceptance_ids else 100.0 * len(acceptance_ids & covered_acceptance) / len(acceptance_ids)
    if objective_ids - covered_objectives:
        reasons.append("Parent objective coverage is incomplete")
    if acceptance_ids - covered_acceptance:
        reasons.append("Parent acceptance coverage is incomplete")

    path_ownership = True
    for left_index, left in enumerate(plan.tasks):
        left_paths = {normalize_path(path).rstrip("/") for path in left.allowed_paths}
        for right in plan.tasks[left_index + 1 :]:
            right_paths = {normalize_path(path).rstrip("/") for path in right.allowed_paths}
            overlap = bool(left_paths & right_paths)
            declared = left.shared_path or right.shared_path or bool(set(left.dependencies) & {right.task_id}) or bool(set(right.dependencies) & {left.task_id})
            if overlap and not declared:
                path_ownership = False
                reasons.append(f"Shared path ownership is undeclared: {left.task_id}/{right.task_id}")
    if not path_ownership:
        reasons.append("Path ownership validation failed")
    if plan.final_verification == [] and len(plan.tasks) > 1:
        reasons.append("Multi-task plan must define final integration verification")
    if not allow_unfrozen and not plan.is_frozen:
        reasons.append("TaskPlan must be frozen before execution")
    risk_partition = all(isinstance(task.risk_class, RiskClass) for task in plan.tasks)
    return TaskPlanValidation(
        valid=not reasons,
        reasons=list(dict.fromkeys(reasons)),
        objective_coverage=objective_coverage,
        acceptance_coverage=acceptance_coverage,
        dag_valid=dag_valid,
        risk_partition=risk_partition,
        path_ownership=path_ownership,
    )


def shape_task(parent: dict[str, Any]) -> TaskPlan:
    """Normalize a structured parent goal into a frozen, validated TaskPlan."""
    if not isinstance(parent, dict):
        raise ValueError("parent must be a dictionary")
    parent_objective = str(parent.get("parent_objective") or parent.get("objective") or "").strip()
    if not parent_objective:
        raise ValueError("parent_objective is required")
    base_head = str(parent.get("base_head") or "").strip()
    if not base_head:
        raise ValueError("base_head is required")
    workdir = str(parent.get("workdir") or "").strip()
    if not Path(workdir).is_absolute():
        raise ValueError("workdir must be an absolute path")
    parent_acceptance = [str(value).strip() for value in parent.get("parent_acceptance", parent.get("acceptance_criteria", [])) if str(value).strip()]
    if not parent_acceptance:
        raise ValueError("parent_acceptance or acceptance_criteria is required")

    raw_objectives = parent.get("objectives")
    if not raw_objectives:
        raw_objectives = [{"id": "O1", "objective": parent_objective, "acceptance_criteria": parent_acceptance}]
    objective_ids = _ids(raw_objectives, "O")
    acceptance_ids = [f"A{index}" for index in range(1, len(parent_acceptance) + 1)]
    tasks: list[ShapedTask] = []
    for index, raw in enumerate(raw_objectives, 1):
        if not isinstance(raw, dict):
            raise ValueError("each objective must be a dictionary")
        objective_id = str(raw.get("id") or objective_ids[index - 1])
        objective = str(raw.get("objective") or raw.get("description") or "").strip()
        criteria = [str(value).strip() for value in raw.get("acceptance_criteria", []) if str(value).strip()]
        if not criteria and len(raw_objectives) == 1:
            criteria = list(parent_acceptance)
        if not criteria:
            raise ValueError(f"objective {objective_id} has no acceptance_criteria")
        covered_acceptance = [str(value) for value in raw.get("covers_acceptance", [])]
        if not covered_acceptance:
            covered_acceptance = acceptance_ids if len(raw_objectives) == 1 else [acceptance_ids[min(index - 1, len(acceptance_ids) - 1)]]
        paths = [normalize_path(str(value)) for value in raw.get("allowed_paths", parent.get("allowed_paths", [])) if str(value).strip()]
        forbidden = [normalize_path(str(value)) for value in raw.get("forbidden_paths", parent.get("forbidden_paths", [])) if str(value).strip()]
        verification = [str(value).strip() for value in raw.get("verification_commands", parent.get("verification_commands", [])) if str(value).strip()]
        dependencies = [str(value) for value in raw.get("dependencies", [])]
        task = ShapedTask(
            task_id=str(raw.get("task_id") or f"task-{index}"),
            objective=objective or parent_objective,
            acceptance_criteria=criteria,
            allowed_paths=paths,
            forbidden_paths=forbidden,
            verification_commands=verification,
            risk_class=_risk(raw.get("risk_class", parent.get("risk_class"))),
            dependencies=dependencies,
            covers_objectives=[objective_id],
            covers_acceptance=covered_acceptance,
            verification_cost_class=str(raw.get("verification_cost_class", parent.get("verification_cost_class", "MODERATE"))).upper(),
            size_class=str(raw.get("size_class", "SMALL" if len(raw_objectives) <= 3 else "MEDIUM")).upper(),
            base_head_resolution="INITIAL_BASE_HEAD" if not dependencies else TASK_BASE_HEAD_RESOLUTION,
            isolated_worktree=bool(parent.get("isolated_worktree", False)),
            max_runtime=raw.get("max_runtime", parent.get("max_runtime", 1800)),
            max_repair_rounds=int(raw.get("max_repair_rounds", parent.get("max_repair_rounds", 2))),
        )
        tasks.append(task)

    shaping_required = len(tasks) > 1 or bool(parent.get("force_shaping"))
    if not shaping_required and len(tasks) == 1:
        tasks[0].task_id = str(parent.get("task_id") or tasks[0].task_id)
        tasks[0].shaping_warning = False
    edges = [{"from": dependency, "to": task.task_id} for task in tasks for dependency in task.dependencies]
    path_ownership: list[dict[str, Any]] = []
    for task in tasks:
        path_ownership.append({"task_id": task.task_id, "allowed_paths": list(task.allowed_paths), "reason": task.path_ownership_reason or "Explicit task ownership"})
    for left_index, left in enumerate(tasks):
        for right in tasks[left_index + 1 :]:
            overlap = sorted(set(left.allowed_paths) & set(right.allowed_paths))
            if overlap:
                left.shared_path = right.shared_path = True
                left.dependency_order_required = right.dependency_order_required = True
                path_ownership.append({"tasks": [left.task_id, right.task_id], "shared_paths": overlap, "shared_path": True, "dependency_order_required": True})
    risk_values = [task.risk_class.value for task in tasks]
    plan = TaskPlan(
        plan_id=str(parent.get("plan_id") or f"plan-{hashlib.sha256((parent_objective + base_head).encode()).hexdigest()[:12]}"),
        parent_objective=parent_objective,
        parent_acceptance=parent_acceptance,
        base_head=base_head,
        workdir=workdir,
        created_at=_now(),
        shaping_reason="Multiple independently verifiable objectives or explicit shaping" if shaping_required else "Single focused objective passes through unchanged",
        shaping_required=shaping_required,
        tasks=tasks,
        dependency_edges=edges,
        objective_coverage={objective_id: [] for objective_id in objective_ids},
        acceptance_coverage={acceptance_id: [] for acceptance_id in acceptance_ids},
        path_ownership=path_ownership,
        risk_summary={"task_risks": risk_values, "plan_max_risk": max(risk_values, key=_risk_level, default=RiskClass.LOW.value)},
        verification_summary={"task_cost_classes": [task.verification_cost_class for task in tasks], "execution_mode": "SERIAL"},
        final_verification=[str(value).strip() for value in parent.get("final_verification", []) if str(value).strip()],
    )
    for task in tasks:
        for objective_id in task.covers_objectives:
            plan.objective_coverage.setdefault(objective_id, []).append(task.task_id)
        for acceptance_id in task.covers_acceptance:
            plan.acceptance_coverage.setdefault(acceptance_id, []).append(task.task_id)
    if shaping_required and not plan.final_verification:
        plan.final_verification = [str(value).strip() for value in parent.get("verification_commands", []) if str(value).strip()]
    plan.freeze()
    return plan
