from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "mcp-antigravity-bridge" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from codex_agy_bridge.agy_runner import AgyResult
from codex_agy_bridge.contracts import TaskContract
from codex_agy_bridge.run_control import WorkerContext
from codex_agy_bridge.worker_binding import build_worker_callback


def _contract(**overrides):
    data = {
        "task_id": "binding-001",
        "objective": "Inspect the requested repository",
        "base_head": "abc123",
        "workdir": Path.cwd().as_posix(),
        "allowed_paths": ["src/server.py"],
        "forbidden_paths": ["secrets.json"],
        "acceptance_criteria": ["tests pass"],
        "verification_commands": ["pytest -q"],
        "dependencies": [],
        "risk_class": "READ_ONLY",
        "max_runtime": 17,
        "max_repair_rounds": 2,
        "auto_commit_policy": "NEVER",
    }
    data.update(overrides)
    return TaskContract.from_dict(data)


def _context(contract: TaskContract) -> WorkerContext:
    import threading
    from codex_agy_bridge.contracts import RunRecord

    return WorkerContext(
        run_id="run-binding",
        task_contract=contract,
        cancel_event=threading.Event(),
        heartbeat_callback=lambda: None,
        record=RunRecord(run_id="run-binding", task_id=contract.task_id),
        worktree=contract.workdir,
    )


def test_callback_maps_contract_to_existing_runner(monkeypatch):
    calls = []

    def fake_runner(prompt, workdir, timeout, **kwargs):
        calls.append((prompt, workdir, timeout, kwargs))
        return AgyResult(text="verified", exit_code=0, used_pty=False)

    contract = _contract()
    result = build_worker_callback(contract, runner=fake_runner)(_context(contract))

    assert result.success is True
    assert result.result_summary == "verified"
    assert len(calls) == 1
    prompt, workdir, timeout, kwargs = calls[0]
    assert contract.objective in prompt
    assert contract.task_id in prompt
    assert workdir == contract.workdir
    assert timeout == 17
    assert kwargs == {}


def test_callback_forwards_explicit_permission_bypass_only_when_authorized():
    calls = []

    def fake_runner(prompt, workdir, timeout, **kwargs):
        calls.append(kwargs)
        return AgyResult(text="ok", exit_code=0, used_pty=False)

    contract = _contract()
    build_worker_callback(
        contract,
        runner=fake_runner,
        dangerously_skip_permissions=True,
    )(_context(contract))

    assert calls == [{"dangerously_skip_permissions": True}]


def test_callback_factory_accepts_injected_runner_without_global_patch():
    calls = []

    def fake_runner(prompt, workdir, timeout, **kwargs):
        calls.append((prompt, workdir, timeout))
        return AgyResult(text="isolated", exit_code=0, used_pty=False)

    contract = _contract()
    result = build_worker_callback(contract, runner=fake_runner)(_context(contract))

    assert result.success is True
    assert len(calls) == 1


def test_callback_maps_runner_failure(monkeypatch):
    fake_runner = lambda *args, **kwargs: AgyResult(text="provider failed", exit_code=7, used_pty=False)
    contract = _contract()
    result = build_worker_callback(contract, runner=fake_runner)(_context(contract))

    assert result.success is False
    assert result.last_error == "provider failed"


def test_callback_maps_explicit_quota_exhaustion_to_account_switch():
    fake_runner = lambda *args, **kwargs: AgyResult(
        text="429: Account daily quota reached",
        exit_code=1,
        used_pty=False,
    )
    contract = _contract()
    result = build_worker_callback(contract, runner=fake_runner)(_context(contract))

    assert result.success is False
    assert result.target_state.value == "ACCOUNT_SWITCH_REQUIRED"


def test_callback_does_not_map_transient_429_to_account_switch():
    fake_runner = lambda *args, **kwargs: AgyResult(
        text="429: server high traffic, retry later",
        exit_code=1,
        used_pty=False,
    )
    contract = _contract()
    result = build_worker_callback(contract, runner=fake_runner)(_context(contract))

    assert result.success is False
    assert result.target_state is None


def test_factory_rejects_invalid_contract_without_worker():
    with pytest.raises(ValueError):
        build_worker_callback(None)  # type: ignore[arg-type]
