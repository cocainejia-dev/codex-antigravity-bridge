from __future__ import annotations

from pathlib import Path

import pytest

from codex_agy_bridge.contracts import RiskClass
from codex_agy_bridge.task_shaping import TASK_BASE_HEAD_RESOLUTION, shape_task, validate_task_plan


def _parent(**overrides):
    parent = {
        "parent_objective": "bounded feature goal",
        "parent_acceptance": ["behavior is verified"],
        "base_head": "abcdef1234567890",
        "workdir": str(Path.cwd()),
        "allowed_paths": ["src/feature.py"],
        "forbidden_paths": ["secrets.json"],
        "verification_commands": ["python -c pass"],
        "risk_class": "LOW",
    }
    parent.update(overrides)
    return parent


def test_small_task_passes_through_as_one_frozen_contract():
    plan = shape_task(_parent())
    assert plan.shaping_required is False
    assert len(plan.tasks) == 1
    assert plan.is_frozen
    assert plan.to_dict()["validation"]["plan_valid"] is True
    assert plan.tasks[0].to_dict(base_head=plan.base_head, workdir=plan.workdir)["task_contract_frozen"] is True


def test_three_boundaries_split_with_full_coverage():
    plan = shape_task(
        _parent(
            parent_objective="upload parse judge feedback",
            parent_acceptance=["upload works", "judge works", "feedback is recorded"],
            objectives=[
                {"id": "O1", "task_id": "T1", "objective": "upload", "acceptance_criteria": ["upload works"], "allowed_paths": ["src/upload.py"]},
                {"id": "O2", "task_id": "T2", "objective": "judge", "acceptance_criteria": ["judge works"], "allowed_paths": ["src/judge.py"], "dependencies": ["T1"]},
                {"id": "O3", "task_id": "T3", "objective": "feedback", "acceptance_criteria": ["feedback is recorded"], "allowed_paths": ["src/feedback.py"], "dependencies": ["T2"]},
            ],
            final_verification=["pytest -q tests/integration"],
        )
    )
    result = validate_task_plan(plan)
    assert plan.shaping_required is True
    assert len(plan.tasks) == 3
    assert result.valid and result.objective_coverage == 100.0 and result.acceptance_coverage == 100.0
    assert result.dag_valid


def test_mixed_risk_is_partitioned_without_downgrading_high():
    plan = shape_task(
        _parent(
            parent_objective="UI plus auth",
            parent_acceptance=["UI works", "auth is enforced"],
            objectives=[
                {"id": "O1", "objective": "UI", "acceptance_criteria": ["UI works"], "allowed_paths": ["src/ui.py"], "risk_class": "LOW"},
                {"id": "O2", "objective": "auth", "acceptance_criteria": ["auth is enforced"], "allowed_paths": ["src/auth.py"], "risk_class": "HIGH"},
            ],
            final_verification=["pytest -q"],
        )
    )
    assert [task.risk_class for task in plan.tasks] == [RiskClass.LOW, RiskClass.HIGH]
    assert plan.risk_summary["plan_max_risk"] == "HIGH"


def test_atomic_shared_path_requires_declared_dependency_order():
    plan = shape_task(
        _parent(
            parent_objective="compatible producer consumer change",
            parent_acceptance=["producer and consumer remain compatible"],
            objectives=[
                {"id": "O1", "task_id": "T1", "objective": "producer", "acceptance_criteria": ["producer and consumer remain compatible"], "allowed_paths": ["src/parser.py"]},
                {"id": "O2", "task_id": "T2", "objective": "consumer", "acceptance_criteria": ["producer and consumer remain compatible"], "allowed_paths": ["src/parser.py"], "dependencies": ["T1"]},
            ],
            final_verification=["pytest -q"],
        )
    )
    assert plan.tasks[0].shared_path and plan.tasks[1].dependency_order_required
    assert validate_task_plan(plan).valid


def test_cycle_is_rejected():
    with pytest.raises(ValueError, match="dependency graph"):
        shape_task(_parent(objectives=[
            {"id": "O1", "task_id": "T1", "objective": "one", "acceptance_criteria": ["behavior is verified"], "allowed_paths": ["src/a.py"], "dependencies": ["T2"]},
            {"id": "O2", "task_id": "T2", "objective": "two", "acceptance_criteria": ["behavior is verified"], "allowed_paths": ["src/b.py"], "dependencies": ["T1"]},
        ], final_verification=["pytest -q"]))


def test_missing_coverage_is_rejected():
    with pytest.raises(ValueError, match="coverage"):
        shape_task(_parent(
            parent_acceptance=["first", "second"],
            objectives=[{"id": "O1", "objective": "only first", "acceptance_criteria": ["first"], "covers_acceptance": ["A1"], "allowed_paths": ["src/a.py"]}],
            final_verification=["pytest -q"],
        ))


def test_dependent_task_resolves_base_at_execution_time():
    plan = shape_task(_parent(objectives=[
        {"id": "O1", "task_id": "T1", "objective": "foundation", "acceptance_criteria": ["behavior is verified"], "allowed_paths": ["src/a.py"]},
        {"id": "O2", "task_id": "T2", "objective": "dependent", "acceptance_criteria": ["behavior is verified"], "allowed_paths": ["src/b.py"], "dependencies": ["T1"]},
    ], final_verification=["pytest -q"]))
    assert plan.tasks[1].base_head_resolution == TASK_BASE_HEAD_RESOLUTION


def test_no_execution_or_parallelism_is_encoded():
    plan = shape_task(_parent(objectives=[
        {"id": "O1", "objective": "one", "acceptance_criteria": ["behavior is verified"], "allowed_paths": ["src/a.py"]},
        {"id": "O2", "objective": "two", "acceptance_criteria": ["behavior is verified"], "allowed_paths": ["src/b.py"]},
    ], final_verification=["pytest -q"]))
    payload = plan.to_dict()
    assert payload["execution_mode"] == "SERIAL"
    assert payload["parallel_eligible"] is True
