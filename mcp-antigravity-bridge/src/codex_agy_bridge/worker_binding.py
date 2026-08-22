"""Production binding from durable worker contexts to the existing AGY runner."""

from __future__ import annotations

import inspect
import json
from typing import Any, Callable

from .agy_runner import is_quota_exhaustion, run_agy
from .contracts import RunState, TaskContract
from .run_control import WorkerCallback, WorkerResult


def _build_prompt(contract: TaskContract) -> str:
    """Preserve the validated contract as the single AGY execution payload."""
    payload: dict[str, Any] = {
        "task_id": contract.task_id,
        "objective": contract.objective,
        "base_head": contract.base_head,
        "workdir": contract.workdir,
        "allowed_paths": contract.allowed_paths,
        "forbidden_paths": contract.forbidden_paths,
        "acceptance_criteria": contract.acceptance_criteria,
        "verification_commands": contract.verification_commands,
        "dependencies": contract.dependencies,
        "risk_class": contract.risk_class.value,
        "max_repair_rounds": contract.max_repair_rounds,
        "auto_commit_policy": contract.auto_commit_policy.value,
    }
    return (
        "Execute this VNext TaskContract in the assigned worktree. "
        "Honor its mutation and path constraints.\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )


def _accepts_liveness_probe(runner: Callable[..., Any]) -> bool:
    """Check if runner accepts liveness_probe without breaking injected test doubles."""
    try:
        sig = inspect.signature(runner)
    except (ValueError, TypeError):
        return True
    params = sig.parameters.values()
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params):
        return True
    return "liveness_probe" in sig.parameters


def build_worker_callback(
    contract: TaskContract,
    *,
    runner: Callable[..., Any] = run_agy,
    dangerously_skip_permissions: bool = False,
) -> WorkerCallback:
    """Build one durable worker callback backed by the existing AGY primitive."""
    if not isinstance(contract, TaskContract):
        raise ValueError("contract must be a validated TaskContract")

    prompt = _build_prompt(contract)

    def _worker(context) -> WorkerResult:
        def _liveness_probe() -> bool:
            try:
                context.heartbeat()
            except Exception:
                pass
            try:
                return not context.is_cancelled()
            except Exception:
                return True

        try:
            runner_kwargs: dict[str, Any] = {
                "workdir": context.worktree or contract.workdir,
                "timeout": float(contract.max_runtime),
            }
            if dangerously_skip_permissions:
                runner_kwargs["dangerously_skip_permissions"] = True
            if _accepts_liveness_probe(runner):
                runner_kwargs["liveness_probe"] = _liveness_probe

            result = runner(prompt, **runner_kwargs)
        except Exception as exc:  # Worker lifecycle records the failure durably.
            return WorkerResult(success=False, last_error=str(exc))

        if result.exit_code == 0:
            return WorkerResult(
                success=True,
                output=result.text,
                result_summary=result.text,
                verification_result={"passed": True, "status": "passed", "returncode": 0},
            )
        if is_quota_exhaustion(result.text, result.stderr):
            return WorkerResult(
                success=False,
                target_state=RunState.ACCOUNT_SWITCH_REQUIRED,
                output=result.text,
                suspended_reason=result.text or result.stderr,
                last_error=result.text or "AGY quota exhausted",
                verification_result={"passed": False, "status": "quota_exhausted", "returncode": result.exit_code},
            )
        return WorkerResult(
            success=False,
            output=result.text,
            last_error=result.text or f"agy exited with code {result.exit_code}",
            verification_result={"passed": False, "status": "failed", "returncode": result.exit_code},
        )

    return _worker
