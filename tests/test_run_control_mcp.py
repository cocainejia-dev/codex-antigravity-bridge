"""Tests for Phase 3B VNext FastMCP durable run wrappers."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest

# Ensure package import from mcp-antigravity-bridge/src
SRC = Path(__file__).resolve().parents[1] / "mcp-antigravity-bridge" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from codex_agy_bridge import server as server_module
from codex_agy_bridge.contracts import RunState
from codex_agy_bridge.run_control import (
    CredentialSecurityError,
    DuplicateRunError,
    DurableRunManager,
    RunNotTerminalError,
    WorkerResult,
)
from codex_agy_bridge.server import (
    mcp,
    run_cancel,
    run_observe,
    run_result,
    run_resume,
    run_start,
    run_status,
    run_wait,
)


@pytest.fixture(autouse=True)
def isolate_start_job_from_real_agy(monkeypatch):
    calls = []

    def fake_factory(contract, **kwargs):
        def fake_worker(context):
            calls.append(context.run_id)
            time.sleep(0.2)
            return WorkerResult(
                success=True,
                result_summary="fake durable execution",
                verification_result={"passed": True, "status": "passed", "returncode": 0},
            )

        return fake_worker

    monkeypatch.setattr(server_module, "build_worker_callback", fake_factory)
    yield calls


def _sample_task_dict(
    task_id: str = "task-mcp-001",
    objective: str = "Test MCP durable run wrapper",
    workdir: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Helper to create a valid TaskContract dict."""
    if workdir is None:
        workdir = Path(os.getcwd()).as_posix()
    return {
        "task_id": task_id,
        "objective": objective,
        "base_head": "0123456789abcdef",
        "workdir": workdir,
        "allowed_paths": ["src/server.py"],
        "forbidden_paths": ["secrets.json"],
        "acceptance_criteria": ["All MCP wrapper tests pass"],
        "verification_commands": ["pytest -q"],
        "dependencies": [],
        "risk_class": "CODE_CHANGES",
        "max_runtime": 300,
        "max_repair_rounds": 2,
        "auto_commit_policy": "VERIFIED_ONLY",
        **kwargs,
    }


def test_tool_registration_preserves_existing_and_adds_vnext() -> None:
    """Verify that all existing agy_* tools and new run_* tools are registered in FastMCP."""
    tools = asyncio.run(mcp.list_tools())
    registered_names = {t.name for t in tools}

    # Verify existing agy_* tools remain registered
    expected_agy_tools = {
        "agy_ask",
        "agy_ask_json",
        "agy_start",
        "agy_status",
        "agy_wait",
        "agy_jobs_recent",
        "agy_collab_start",
        "agy_collab_status",
    }
    for tool_name in expected_agy_tools:
        assert tool_name in registered_names, f"Expected existing tool {tool_name} to be registered"

    # Verify new VNext run_* tools are registered
    expected_run_tools = {
        "run_start",
        "run_status",
        "run_observe",
        "run_wait",
        "run_result",
        "run_cancel",
        "run_resume",
    }
    for tool_name in expected_run_tools:
        assert tool_name in registered_names, f"Expected VNext tool {tool_name} to be registered"


def test_db_path_validation_and_no_production_fallback(tmp_path: Path) -> None:
    """Verify that empty, whitespace, non-string, bool, or directory db_path is rejected without fallback."""
    invalid_paths = [
        "",
        "   ",
        None,
        True,
        False,
        str(tmp_path),  # Directory path
    ]
    task = _sample_task_dict()

    for bad_path in invalid_paths:
        with pytest.raises(ValueError):
            run_start(db_path=bad_path, task=task)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            run_status(db_path=bad_path, run_id="run-123")  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            run_observe(db_path=bad_path, run_id="run-123")  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            run_wait(db_path=bad_path, run_id="run-123")  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            run_result(db_path=bad_path, run_id="run-123")  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            run_cancel(db_path=bad_path, run_id="run-123")  # type: ignore[arg-type]

    # Verify no fallback files (like jobs.sqlite3 or default.sqlite3) were created in CWD or temp
    assert not Path("jobs.sqlite3").exists()
    assert not Path("default.sqlite3").exists()


def test_run_start_persists_created_run(tmp_path: Path) -> None:
    """Verify run_start persists a CREATED run in the exact db_path and returns JSON."""
    db_file = tmp_path / "vnext_runs.sqlite3"
    task = _sample_task_dict(task_id="task-start-01")

    res_json = run_start(db_path=str(db_file), task=task, run_id="run-custom-01")
    assert db_file.exists()

    data = json.loads(res_json)
    assert data["run_id"] == "run-custom-01"
    assert data["task_id"] == "task-start-01"
    assert data["state"] == "CREATED"
    assert data["state_version"] == 1
    assert data["created_at"] is not None
    assert data["updated_at"] is not None

    # Check that another manager reading the exact same DB sees the record
    manager = DurableRunManager(db_file)
    record = manager.run_status("run-custom-01")
    assert record.run_id == "run-custom-01"
    assert record.state in (RunState.CREATED, RunState.QUEUED, RunState.RUNNING, RunState.COMPLETE)


def test_run_start_with_callback_claims_in_process_worker(tmp_path: Path) -> None:
    """MCP-created runs claim ownership only after a real callback is bound."""
    db_file = tmp_path / "vnext_no_callback.sqlite3"
    task = _sample_task_dict(task_id="task-no-callback-ownership")

    start_data = json.loads(run_start(db_path=str(db_file), task=task, run_id="run-no-callback-ownership"))
    assert start_data["state"] == "CREATED"

    manager = DurableRunManager(db_file)
    identity = manager.store.get_worker_identity("run-no-callback-ownership")
    assert identity is not None
    assert identity["worker_type"] == "in_process"
    assert identity["type"] == "in_process"

    observed = json.loads(run_observe(db_path=str(db_file), run_id="run-no-callback-ownership"))
    assert observed["state"] in {"CREATED", "QUEUED", "RUNNING", "COMPLETE"}
    assert observed["recovery_state"] is None


def test_run_start_accepts_json_string_task(tmp_path: Path) -> None:
    """Verify run_start accepts task as a serialized JSON string."""
    db_file = tmp_path / "vnext_json_str.sqlite3"
    task = _sample_task_dict(task_id="task-json-str-01")
    task_json = json.dumps(task)

    res_json = run_start(db_path=str(db_file), task=task_json)  # type: ignore[arg-type]
    data = json.loads(res_json)
    assert data["task_id"] == "task-json-str-01"
    assert data["state"] == "CREATED"


def test_run_start_idempotency_and_duplicate_handling(tmp_path: Path) -> None:
    """Verify idempotency key re-returns existing record, while duplicate task_id raises DuplicateRunError."""
    db_file = tmp_path / "vnext_idempotency.sqlite3"
    task = _sample_task_dict(task_id="task-idem-01")

    # 1. Start with idempotency_key
    res1 = run_start(db_path=str(db_file), task=task, idempotency_key="idem-key-abc")
    data1 = json.loads(res1)
    run_id1 = data1["run_id"]

    # 2. Call again with same idempotency_key -> returns identical run
    res2 = run_start(db_path=str(db_file), task=task, idempotency_key="idem-key-abc")
    data2 = json.loads(res2)
    assert data2["run_id"] == run_id1
    assert data2["state"] in {"CREATED", "QUEUED", "RUNNING", "COMPLETE"}

    # 3. Call with same task_id but different/no idempotency key -> DuplicateRunError
    with pytest.raises(DuplicateRunError):
        run_start(db_path=str(db_file), task=task, idempotency_key="different-key")


def test_run_resume_preserves_same_run_and_rejects_duplicate(tmp_path: Path) -> None:
    db_file = tmp_path / "resume.sqlite3"
    task = _sample_task_dict(task_id="task-resume-public")
    manager = DurableRunManager(db_file)
    contract = server_module.TaskContract.from_dict(task)
    initial = manager.run_start(contract, run_id="run-resume-public", auto_spawn=False)
    start = initial.to_dict()
    manager.store.transition_run(start["run_id"], expected_version=1, target_state=RunState.QUEUED)
    manager.store.transition_run(
        start["run_id"],
        expected_version=2,
        target_state=RunState.RUNNING,
    )
    manager.store.transition_run(
        start["run_id"],
        expected_version=3,
        target_state=RunState.ACCOUNT_SWITCH_REQUIRED,
        suspended_reason="Account daily quota reached",
    )

    resumed = json.loads(run_resume(str(db_file), start["run_id"], account_switched=True))
    assert resumed["run_id"] == start["run_id"]
    assert resumed["task_id"] == start["task_id"]
    final = manager.run_wait(start["run_id"], timeout=2.0)
    assert final.state == RunState.COMPLETE
    with pytest.raises(Exception, match="ACCOUNT_SWITCH_REQUIRED"):
        run_resume(str(db_file), start["run_id"], account_switched=True)


def test_run_status_and_observe_shape(tmp_path: Path) -> None:
    """Verify run_status and run_observe return expected JSON structures."""
    db_file = tmp_path / "vnext_status_observe.sqlite3"
    task = _sample_task_dict(task_id="task-observe-01")

    start_json = run_start(db_path=str(db_file), task=task, run_id="run-obs-01")
    start_data = json.loads(start_json)
    run_id = start_data["run_id"]

    # run_status
    status_json = run_status(db_path=str(db_file), run_id=run_id)
    status_data = json.loads(status_json)
    assert status_data["run_id"] == run_id
    assert status_data["state"] in {"CREATED", "QUEUED", "RUNNING", "COMPLETE"}
    assert status_data["task_id"] == "task-observe-01"

    # run_observe on a completed MCP-created run preserves the observation schema.
    observe_json = run_observe(db_path=str(db_file), run_id=run_id)
    obs_data = json.loads(observe_json)
    assert obs_data["run_id"] == run_id
    assert obs_data["state"] in {"CREATED", "QUEUED", "RUNNING", "COMPLETE"}
    assert obs_data["recovery_state"] is None
    assert obs_data["is_terminal"] is (obs_data["state"] == "COMPLETE")
    assert obs_data["is_alive"] is (obs_data["state"] != "COMPLETE")
    assert obs_data["is_stale"] is False
    assert "record" in obs_data
    assert obs_data["record"]["run_id"] == run_id

    obs_terminal_json = run_observe(db_path=str(db_file), run_id=run_id)
    obs_term_data = json.loads(obs_terminal_json)
    assert obs_term_data["state"] == obs_data["state"]
    assert obs_term_data["is_terminal"] is obs_data["is_terminal"]
    assert obs_term_data["recovery_state"] is None


def test_run_wait_bounded_and_does_not_cancel(tmp_path: Path) -> None:
    """Verify run_wait returns current state on timeout without cancelling or terminating the run."""
    db_file = tmp_path / "vnext_wait.sqlite3"
    task = _sample_task_dict(task_id="task-wait-01")

    start_json = run_start(db_path=str(db_file), task=task, run_id="run-wait-01")
    start_data = json.loads(start_json)
    run_id = start_data["run_id"]

    # Wait with a short timeout on a running callback.
    wait_json = run_wait(db_path=str(db_file), run_id=run_id, timeout=0.01, poll_interval=0.002)
    wait_data = json.loads(wait_json)
    assert wait_data["run_id"] == run_id
    assert wait_data["state"] in {"CREATED", "QUEUED", "RUNNING"}

    # Verify the run remains active and was NOT cancelled.
    status_json = run_status(db_path=str(db_file), run_id=run_id)
    status_data = json.loads(status_json)
    assert status_data["state"] in {"CREATED", "QUEUED", "RUNNING", "COMPLETE"}


def test_run_result_rejects_non_terminal_and_returns_terminal(tmp_path: Path) -> None:
    """Verify run_result raises RunNotTerminalError on active runs and returns result on terminal runs."""
    db_file = tmp_path / "vnext_result.sqlite3"
    task = _sample_task_dict(task_id="task-result-01")

    start_json = run_start(db_path=str(db_file), task=task, run_id="run-res-01")
    run_id = json.loads(start_json)["run_id"]

    # 1. Non-terminal: should raise RunNotTerminalError
    with pytest.raises(RunNotTerminalError):
        run_result(db_path=str(db_file), run_id=run_id)

    # 2. Cooperatively cancel run to make it terminal
    cancel_json = run_cancel(db_path=str(db_file), run_id=run_id, reason="Testing run_result")
    cancel_data = json.loads(cancel_json)
    assert cancel_data["state"] == "CANCELLED"

    # 3. Now run_result should return the terminal record JSON
    result_json = run_result(db_path=str(db_file), run_id=run_id)
    result_data = json.loads(result_json)
    assert result_data["run_id"] == run_id
    assert result_data["state"] == "CANCELLED"
    assert result_data["last_error"] == "Testing run_result"


def test_numeric_timeout_validation(tmp_path: Path) -> None:
    """Verify numeric validation rejects bool, NaN, Infinity, and non-positive numbers."""
    db_file = tmp_path / "vnext_validation.sqlite3"
    task = _sample_task_dict(task_id="task-num-val-01")
    start_json = run_start(db_path=str(db_file), task=task, run_id="run-num-01")
    run_id = json.loads(start_json)["run_id"]

    invalid_numbers = [
        True,
        False,
        float("nan"),
        float("inf"),
        float("-inf"),
        -1.0,
        0,
        0.0,
    ]

    for inv in invalid_numbers:
        with pytest.raises(ValueError):
            run_wait(db_path=str(db_file), run_id=run_id, timeout=inv)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            run_wait(db_path=str(db_file), run_id=run_id, poll_interval=inv)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            run_observe(db_path=str(db_file), run_id=run_id, stale_heartbeat_threshold_seconds=inv)  # type: ignore[arg-type]


def test_credential_security_rejection(tmp_path: Path) -> None:
    """Verify credential patterns in task contract are rejected and not stored."""
    db_file = tmp_path / "vnext_creds.sqlite3"

    bad_task = _sample_task_dict(
        task_id="task-leak-01",
        objective="Deploy using token ghp_123456789012345678901234567890",
    )

    with pytest.raises((CredentialSecurityError, ValueError)):
        run_start(db_path=str(db_file), task=bad_task)

    # Verify no record was inserted
    manager = DurableRunManager(db_file)
    assert manager.list_runs() == []


def test_fastmcp_call_tool_dispatch(tmp_path: Path) -> None:
    """Verify all VNext tools are callable through the FastMCP call_tool protocol."""
    db_file = str(tmp_path / "vnext_fastmcp_dispatch.sqlite3")
    task = _sample_task_dict(task_id="task-dispatch-01")

    async def _run_async_dispatch() -> None:
        # 1. run_start via call_tool
        res_start, _out_start = await mcp.call_tool(
            "run_start",
            {"db_path": db_file, "task": task, "run_id": "run-disp-01"},
        )
        assert res_start[0].text is not None
        start_data = json.loads(res_start[0].text)
        assert start_data["run_id"] == "run-disp-01"
        assert start_data["state"] == "CREATED"

        # 2. run_status via call_tool
        res_status, _ = await mcp.call_tool(
            "run_status",
            {"db_path": db_file, "run_id": "run-disp-01"},
        )
        status_data = json.loads(res_status[0].text)
        assert status_data["run_id"] == "run-disp-01"
        assert status_data["state"] in {"CREATED", "QUEUED", "RUNNING", "COMPLETE"}

        # 3. run_observe via call_tool
        res_obs, _ = await mcp.call_tool(
            "run_observe",
            {"db_path": db_file, "run_id": "run-disp-01"},
        )
        obs_data = json.loads(res_obs[0].text)
        assert obs_data["run_id"] == "run-disp-01"
        assert obs_data["is_terminal"] is (obs_data["state"] == "COMPLETE")

        # 4. run_wait via call_tool
        res_wait, _ = await mcp.call_tool(
            "run_wait",
            {"db_path": db_file, "run_id": "run-disp-01", "timeout": 0.05, "poll_interval": 0.01},
        )
        wait_data = json.loads(res_wait[0].text)
        assert wait_data["run_id"] == "run-disp-01"

        # 5. run_cancel via call_tool
        res_cancel, _ = await mcp.call_tool(
            "run_cancel",
            {"db_path": db_file, "run_id": "run-disp-01", "reason": "Dispatch cancel"},
        )
        cancel_data = json.loads(res_cancel[0].text)
        assert cancel_data["state"] == "CANCELLED"

        # 6. run_result via call_tool
        res_res, _ = await mcp.call_tool(
            "run_result",
            {"db_path": db_file, "run_id": "run-disp-01"},
        )
        res_data = json.loads(res_res[0].text)
        assert res_data["state"] == "CANCELLED"
        assert res_data["last_error"] == "Dispatch cancel"

    asyncio.run(_run_async_dispatch())
