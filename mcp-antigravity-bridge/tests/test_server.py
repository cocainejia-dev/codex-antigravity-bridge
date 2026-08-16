from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
import threading
import time

try:
    import pytest
except ImportError:
    class _PytestRaisesContext:
        def __init__(self, expected_exc, match=None):
            self.expected_exc = expected_exc
            self.match = match
            self.value = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            if exc_type is None:
                raise AssertionError(f"Expected {self.expected_exc.__name__} but no exception was raised")
            if not issubclass(exc_type, self.expected_exc):
                return False
            if self.match:
                import re
                if not re.search(self.match, str(exc_val)):
                    raise AssertionError(f"Exception message '{exc_val}' does not match pattern '{self.match}'")
            self.value = exc_val
            return True

    class _PytestStub:
        def raises(self, expected_exc, match=None):
            return _PytestRaisesContext(expected_exc, match=match)

    pytest = _PytestStub()

from codex_agy_bridge import server
from codex_agy_bridge.agy_runner import AgyResult


class _SimpleMonkeyPatch:
    def __init__(self):
        self._undos = []

    def setattr(self, target, name, value=None):
        if value is None:
            parts = target.split(".")
            mod_path = ".".join(parts[:-1])
            attr_name = parts[-1]
            mod = __import__(mod_path, fromlist=[attr_name])
            orig = getattr(mod, attr_name)
            setattr(mod, attr_name, name)
            self._undos.append((mod, attr_name, orig))
        else:
            if isinstance(target, str):
                parts = target.split(".")
                mod_path = ".".join(parts[:-1])
                attr_name = parts[-1]
                mod = __import__(mod_path, fromlist=[attr_name])
                orig = getattr(mod, attr_name)
                setattr(mod, attr_name, name)
                self._undos.append((mod, attr_name, orig))
            else:
                orig = getattr(target, name)
                setattr(target, name, value)
                self._undos.append((target, name, orig))

    def undo(self):
        for target, name, orig in reversed(self._undos):
            setattr(target, name, orig)
        self._undos.clear()


def test_agy_ask_json_rejects_unparseable_output(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    mp.setattr(
        server,
        "run_agy",
        lambda *args, **kwargs: AgyResult(text="not json", exit_code=0),
    )
    try:
        with pytest.raises(ValueError, match="valid JSON"):
            server.agy_ask_json("Return JSON")
    finally:
        if monkeypatch is None:
            mp.undo()


def test_agy_ask_rejects_nonzero_exit(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    mp.setattr(
        server,
        "run_agy",
        lambda *args, **kwargs: AgyResult(
            text="authentication failed", exit_code=7, stderr="authentication failed"
        ),
    )
    try:
        with pytest.raises(RuntimeError, match="AGY_LOGIN_REQUIRED.*authentication failed"):
            server.agy_ask("Say hi")
    finally:
        if monkeypatch is None:
            mp.undo()


def test_agy_ask_propagates_runner_timeout(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    def timed_out(*args, **kwargs):
        raise TimeoutError("agy timed out after 1s")

    mp.setattr(server, "run_agy", timed_out)
    try:
        with pytest.raises(TimeoutError, match="agy timed out"):
            server.agy_ask("Say hi", timeout=1)
    finally:
        if monkeypatch is None:
            mp.undo()


def test_agy_ask_reports_proxy_failure_without_requesting_login(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    mp.setattr(
        server,
        "run_agy",
        lambda *args, **kwargs: AgyResult(
            text="token exchange failed: dial tcp 74.125.195.95:443",
            exit_code=7,
        ),
    )
    try:
        with pytest.raises(RuntimeError, match="AGY_PROXY_ERROR") as exc_info:
            server.agy_ask("Say hi")
        assert "login" not in str(exc_info.value).lower()
    finally:
        if monkeypatch is None:
            mp.undo()


def test_agy_ask_reports_authentication_failure_as_login_required(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    mp.setattr(
        server,
        "run_agy",
        lambda *args, **kwargs: AgyResult(
            text="OAuth token invalid; authentication required",
            exit_code=7,
        ),
    )
    try:
        with pytest.raises(RuntimeError, match="AGY_LOGIN_REQUIRED"):
            server.agy_ask("Say hi")
    finally:
        if monkeypatch is None:
            mp.undo()


def test_agy_ask_json_returns_parseable_json(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    mp.setattr(
        server,
        "run_agy",
        lambda *args, **kwargs: AgyResult(text='{"ok": true}', exit_code=0),
    )
    try:
        assert server.agy_ask_json("Return JSON") == '{"ok": true}'
    finally:
        if monkeypatch is None:
            mp.undo()


def test_agy_start_requires_explicit_workdir():
    with pytest.raises(ValueError, match="workdir"):
        server.agy_start("Implement the task")


def test_public_tools_reject_nonpositive_timeouts(tmp_path=None):
    workdir_str = str(tmp_path) if tmp_path is not None else "."
    with pytest.raises(ValueError, match="positive finite number"):
        server.agy_ask("Say hi", timeout=0)

    with pytest.raises(ValueError, match="positive finite number"):
        server.agy_ask_json("Return JSON", timeout=-1)

    with pytest.raises(ValueError, match="positive finite number"):
        server.agy_start("Implement the task", workdir=workdir_str, timeout=0)

    with pytest.raises(ValueError, match="positive finite number"):
        server.agy_ask("Say hi", timeout=float("nan"))

    with pytest.raises(ValueError, match="positive finite number"):
        asyncio.run(server.agy_wait("job-1", wait_seconds=0))

    with pytest.raises(ValueError, match="positive finite number"):
        asyncio.run(server.agy_wait("job-1", wait_seconds=-1))

    with pytest.raises(ValueError, match="positive finite number"):
        asyncio.run(server.agy_wait("job-1", wait_seconds=float("nan")))

    with pytest.raises(ValueError, match="positive finite number"):
        asyncio.run(server.agy_wait("job-1", wait_seconds=float("inf")))

    with pytest.raises(ValueError, match="positive finite number"):
        asyncio.run(server.agy_wait("job-1", wait_seconds=True))


def test_agy_status_returns_json(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    mp.setattr(
        server.agy_jobs,
        "status",
        lambda job_id: {"job_id": job_id, "state": "running", "health": "HEALTHY"},
    )
    try:
        result_str = asyncio.run(server.agy_status("test-id"))
        data = json.loads(result_str)
        assert data["job_id"] == "test-id"
        assert data["state"] == "running"
        assert data["health"] == "HEALTHY"
    finally:
        if monkeypatch is None:
            mp.undo()


def test_agy_status_is_async_tool_and_unblocks_event_loop(monkeypatch=None):
    assert inspect.iscoroutinefunction(server.agy_status)

    mp = monkeypatch or _SimpleMonkeyPatch()

    def slow_status(job_id):
        time.sleep(0.2)
        return {"job_id": job_id, "state": "running", "health": "HEALTHY"}

    mp.setattr(server.agy_jobs, "status", slow_status)

    async def runner():
        concurrent_ticks = 0

        async def ticker():
            nonlocal concurrent_ticks
            for _ in range(5):
                await asyncio.sleep(0.03)
                concurrent_ticks += 1

        status_task = asyncio.create_task(server.agy_status("test-job"))
        tick_task = asyncio.create_task(ticker())

        res, _ = await asyncio.gather(status_task, tick_task)
        assert json.loads(res)["state"] == "running"
        assert concurrent_ticks >= 3

    try:
        asyncio.run(runner())
    finally:
        if monkeypatch is None:
            mp.undo()


def test_agy_wait_returns_json(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    mp.setattr(
        server.agy_jobs,
        "wait",
        lambda job_id, wait_seconds: {"job_id": job_id, "state": "completed", "text": "DONE"},
    )
    try:
        result_str = asyncio.run(server.agy_wait("test-id", wait_seconds=30.0))
        assert '"state": "completed"' in result_str
        assert '"job_id": "test-id"' in result_str
    finally:
        if monkeypatch is None:
            mp.undo()


def test_agy_wait_is_async_tool_and_unblocks_event_loop(monkeypatch=None):
    assert inspect.iscoroutinefunction(server.agy_wait)

    mp = monkeypatch or _SimpleMonkeyPatch()

    def slow_wait(job_id, wait_seconds):
        time.sleep(0.2)
        return {"job_id": job_id, "state": "running"}

    mp.setattr(server.agy_jobs, "wait", slow_wait)

    async def runner():
        concurrent_ticks = 0

        async def ticker():
            nonlocal concurrent_ticks
            for _ in range(5):
                await asyncio.sleep(0.03)
                concurrent_ticks += 1

        wait_task = asyncio.create_task(server.agy_wait("test-job", wait_seconds=0.2))
        tick_task = asyncio.create_task(ticker())

        res, _ = await asyncio.gather(wait_task, tick_task)
        assert json.loads(res)["state"] == "running"
        assert concurrent_ticks >= 3

    try:
        asyncio.run(runner())
    finally:
        if monkeypatch is None:
            mp.undo()


def test_agy_start_forwards_task_key(monkeypatch=None, tmp_path=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    workdir_str = str(tmp_path) if tmp_path is not None else "."
    captured = {}

    def fake_start(*args, **kwargs):
        captured.update(kwargs)
        return "job-123"

    mp.setattr(server.agy_jobs, "start", fake_start)
    try:
        job_id = server.agy_start("Run task", workdir=workdir_str, task_key="my-key")
        assert job_id == "job-123"
        assert captured.get("task_key") == "my-key"
    finally:
        if monkeypatch is None:
            mp.undo()


def test_agy_jobs_recent_tool_and_validations(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    captured_args = {}

    def fake_recent(limit=20, task_key="", state=""):
        captured_args["limit"] = limit
        captured_args["task_key"] = task_key
        captured_args["state"] = state
        return [
            {
                "job_id": "j-1",
                "task_key": "k-1",
                "state": "completed",
                "health": "COMPLETED",
                "recovery_state": None,
                "workdir": "C:\\work",
                "submitted_at": "2026-08-16T00:00:00Z",
                "started_at": "2026-08-16T00:00:01Z",
                "completed_at": "2026-08-16T00:00:10Z",
                "elapsed_seconds": 9.0,
                "heartbeat_at": "2026-08-16T00:00:10Z",
                "last_worktree_activity_at": None,
                "exit_code": 0,
                "result_truncated": False,
                "used_pty": False,
                "prompt_hash": "abcdef",
                "owner_session_id": "sess-1",
                "created_at": "2026-08-16T00:00:00Z",
                "updated_at": "2026-08-16T00:00:10Z",
            }
        ]

    mp.setattr(server.agy_jobs, "recent", fake_recent)
    try:
        # Valid call
        res_json = server.agy_jobs_recent(limit=10, task_key="k-1", state="completed")
        data = json.loads(res_json)
        assert len(data) == 1
        assert data[0]["job_id"] == "j-1"
        assert data[0]["state"] == "completed"
        assert captured_args["limit"] == 10
        assert captured_args["task_key"] == "k-1"
        assert captured_args["state"] == "completed"

        # Limit validations
        with pytest.raises(ValueError, match="limit"):
            server.agy_jobs_recent(limit=0)
        with pytest.raises(ValueError, match="limit"):
            server.agy_jobs_recent(limit=101)
        with pytest.raises(ValueError, match="limit"):
            server.agy_jobs_recent(limit=-5)
        with pytest.raises(ValueError, match="limit"):
            server.agy_jobs_recent(limit=True)
    finally:
        if monkeypatch is None:
            mp.undo()


def test_agy_status_unblocks_event_loop_while_worktree_observation_is_slow(monkeypatch=None):
    import tempfile
    mp = monkeypatch or _SimpleMonkeyPatch()
    runner_entered = threading.Event()
    runner_unblock = threading.Event()

    def slow_runner(*args, **kwargs):
        runner_entered.set()
        runner_unblock.wait(timeout=5.0)
        return AgyResult(text="DONE", exit_code=0, used_pty=False)

    mp.setattr("codex_agy_bridge.agy_jobs.run_agy", slow_runner)

    async def runner():
        concurrent_ticks = 0

        async def ticker():
            nonlocal concurrent_ticks
            for _ in range(5):
                await asyncio.sleep(0.02)
                concurrent_ticks += 1

        with tempfile.TemporaryDirectory() as tmp_workdir:
            # Start real registry job via server
            job_id = server.agy_start(prompt="Test async unblock", workdir=tmp_workdir)

            assert runner_entered.wait(timeout=2.0), "Worker did not enter runner"

            # agy_status called concurrently with ticker while runner is executing
            status_task = asyncio.create_task(server.agy_status(job_id))
            tick_task = asyncio.create_task(ticker())

            res_str, _ = await asyncio.gather(status_task, tick_task)
            status_data = json.loads(res_str)

            assert status_data["job_id"] == job_id
            assert status_data["state"] in {"queued", "running"}
            assert concurrent_ticks >= 2, f"Event loop was blocked during agy_status, ticks: {concurrent_ticks}"

            runner_unblock.set()
            await server.agy_wait(job_id, wait_seconds=2.0)

    try:
        asyncio.run(runner())
    finally:
        runner_unblock.set()
        if monkeypatch is None:
            mp.undo()
