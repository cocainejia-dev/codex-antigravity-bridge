from __future__ import annotations

from concurrent.futures import Future
import os
from pathlib import Path
import subprocess
import tempfile
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

from codex_agy_bridge.agy_jobs import AgyJobRegistry
from codex_agy_bridge.agy_runner import AgyResult
from codex_agy_bridge.durable_jobs import DurableJobStore, _utc_now_iso


class _SimpleMonkeyPatch:
    def __init__(self):
        self._undos = []

    def setattr(self, target, name, value=None):
        if value is None:
            # target is string path
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


def _wait_for_status(registry: AgyJobRegistry, job_id: str, predicate, timeout: float = 2.0):
    """Poll a durable status condition with a bounded deadline across slow CI hosts."""
    deadline = time.monotonic() + timeout
    status = registry.status(job_id)
    while not predicate(status) and time.monotonic() < deadline:
        time.sleep(0.01)
        status = registry.status(job_id)
    return status


def test_start_and_status_reports_completed_job(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    def fake_run_agy(*args, **kwargs):
        return AgyResult(text="DONE", exit_code=0, used_pty=False)

    mp.setattr("codex_agy_bridge.agy_jobs.run_agy", fake_run_agy)
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "jobs.sqlite3"
        registry = AgyJobRegistry(db_path=db_file)
        try:
            job_id = registry.start("Implement page", workdir="C:\\work")
            status = _wait_for_status(
                registry,
                job_id,
                lambda current: current["state"] == "completed",
            )

            assert status["job_id"] == job_id
            assert status["state"] == "completed"
            assert status["health"] == "COMPLETED"
            assert status["text"] == "DONE"
            assert status["exit_code"] == 0
            assert status["used_pty"] is False
            assert status["submitted_at"]
            assert status["started_at"]
            assert status["completed_at"]
            assert isinstance(status["elapsed_seconds"], float)
            assert status["elapsed_seconds"] >= 0.0
        finally:
            registry.close()
            if monkeypatch is None:
                mp.undo()


def test_status_reports_running_job(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    def slow_run_agy(*args, **kwargs):
        time.sleep(0.1)
        return AgyResult(text="DONE", exit_code=0, used_pty=False)

    mp.setattr("codex_agy_bridge.agy_jobs.run_agy", slow_run_agy)
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "jobs.sqlite3"
        registry = AgyJobRegistry(db_path=db_file)
        try:
            job_id = registry.start("Implement page")
            status = registry.status(job_id)

            assert status["job_id"] == job_id
            assert status["state"] in {"queued", "running"}
            assert status["health"] in {"QUEUED", "HEALTHY"}
            assert status["submitted_at"]
            assert isinstance(status["elapsed_seconds"], float)
            assert status["elapsed_seconds"] >= 0.0
        finally:
            registry.close()
            if monkeypatch is None:
                mp.undo()


def test_soft_budget_exceeded_with_fresh_heartbeat_remains_live(monkeypatch=None):
    """W1 RED: a hard runner timeout is currently recorded as terminal failure."""
    mp = monkeypatch or _SimpleMonkeyPatch()
    unblock = threading.Event()

    def liveness_aware_runner(*args, **kwargs):
        if kwargs.get("liveness_probe") is None:
            raise TimeoutError("agy timed out after 0.05s")
        assert unblock.wait(timeout=2.0)
        return AgyResult(text="DONE", exit_code=0, used_pty=False)

    mp.setattr("codex_agy_bridge.agy_jobs.run_agy", liveness_aware_runner)
    with tempfile.TemporaryDirectory() as tmp_dir:
        registry = AgyJobRegistry(
            db_path=Path(tmp_dir) / "jobs.sqlite3",
            watchdog_interval=0.02,
            stale_heartbeat_threshold=0.2,
        )
        try:
            job_id = registry.start("Long live task", timeout=0.05)
            status = _wait_for_status(
                registry,
                job_id,
                lambda current: current["state"] == "running"
                and current["health"] in {"HEALTHY", "QUEUED"},
            )
            assert status["state"] == "running"
            assert status["health"] in {"HEALTHY", "QUEUED"}
            unblock.set()
            assert registry.wait(job_id, wait_seconds=1.0)["state"] == "completed"
        finally:
            unblock.set()
            registry.close()
            if monkeypatch is None:
                mp.undo()


def test_soft_budget_exceeded_with_recent_worktree_activity_remains_live(monkeypatch=None):
    """W1 RED: worktree activity is observed but cannot prevent hard timeout failure."""
    mp = monkeypatch or _SimpleMonkeyPatch()
    unblock = threading.Event()

    def liveness_aware_runner(*args, **kwargs):
        if kwargs.get("liveness_probe") is None:
            raise TimeoutError("agy timed out after 0.05s")
        assert unblock.wait(timeout=2.0)
        return AgyResult(text="DONE", exit_code=0, used_pty=False)

    mp.setattr("codex_agy_bridge.agy_jobs.run_agy", liveness_aware_runner)
    with tempfile.TemporaryDirectory() as git_dir, tempfile.TemporaryDirectory() as tmp_dir:
        subprocess.run(["git", "init"], cwd=git_dir, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=git_dir, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=git_dir, capture_output=True, check=True)
        work_file = Path(git_dir) / "progress.txt"
        work_file.write_text("initial", encoding="utf-8")
        subprocess.run(["git", "add", "progress.txt"], cwd=git_dir, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=git_dir, capture_output=True, check=True)

        registry = AgyJobRegistry(
            db_path=Path(tmp_dir) / "jobs.sqlite3",
            watchdog_interval=0.02,
            stale_heartbeat_threshold=0.2,
            idle_worktree_threshold=0.2,
        )
        try:
            job_id = registry.start("Long live worktree task", workdir=git_dir, timeout=0.05)
            time.sleep(0.06)
            work_file.write_text("progress", encoding="utf-8")
            time.sleep(0.10)
            status = registry.status(job_id)
            assert status["state"] == "running"
            assert status["health"] == "HEALTHY"
            assert status["last_worktree_activity_at"] is not None
            unblock.set()
            assert registry.wait(job_id, wait_seconds=1.0)["state"] == "completed"
        finally:
            unblock.set()
            registry.close()
            if monkeypatch is None:
                mp.undo()


def test_status_reports_unknown_job():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "jobs.sqlite3"
        registry = AgyJobRegistry(db_path=db_file)
        try:
            assert registry.status("missing") == {
                "job_id": "missing",
                "state": "unknown",
                "health": "UNKNOWN",
                "error": "job not found",
            }
        finally:
            registry.close()


def test_status_reports_nonzero_exit_as_failed(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    def failed_run_agy(*args, **kwargs):
        return AgyResult(text="agy failed", exit_code=1, used_pty=False)

    mp.setattr("codex_agy_bridge.agy_jobs.run_agy", failed_run_agy)
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "jobs.sqlite3"
        registry = AgyJobRegistry(db_path=db_file)
        try:
            job_id = registry.start("Fail this task")
            for _ in range(20):
                status = registry.status(job_id)
                if status["state"] == "failed":
                    break
                time.sleep(0.01)

            assert status["job_id"] == job_id
            assert status["state"] == "failed"
            assert status["health"] == "FAILED"
            assert status["text"] == "agy failed"
            assert status["exit_code"] == 1
            assert status["used_pty"] is False
            assert status["error_kind"] == "unknown"
            assert status["error"] == "AGY_FAILED: agy exited with code 1: agy failed"
            assert status["submitted_at"]
            assert status["started_at"]
            assert status["completed_at"]
            assert isinstance(status["elapsed_seconds"], float)
            assert status["elapsed_seconds"] >= 0.0
        finally:
            registry.close()
            if monkeypatch is None:
                mp.undo()


def test_status_reports_login_required_for_authentication_failure(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    def failed_run_agy(*args, **kwargs):
        return AgyResult(text="authentication required", exit_code=1, used_pty=False)

    mp.setattr("codex_agy_bridge.agy_jobs.run_agy", failed_run_agy)
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "jobs.sqlite3"
        registry = AgyJobRegistry(db_path=db_file)
        try:
            job_id = registry.start("Login test")
            for _ in range(20):
                status = registry.status(job_id)
                if status["state"] == "failed":
                    break
                time.sleep(0.01)

            assert status["error_kind"] == "authentication"
            assert status["error"].startswith("AGY_LOGIN_REQUIRED:")
        finally:
            registry.close()
            if monkeypatch is None:
                mp.undo()


def test_completed_jobs_are_pruned_after_retention(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    mp.setattr(
        "codex_agy_bridge.agy_jobs.run_agy",
        lambda *args, **kwargs: AgyResult(text="DONE", exit_code=0, used_pty=False),
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "jobs.sqlite3"
        registry = AgyJobRegistry(retention_seconds=0.05, db_path=db_file)
        try:
            job_id = registry.start("Implement page")
            for _ in range(20):
                if registry.status(job_id)["state"] == "completed":
                    break
                time.sleep(0.01)
            time.sleep(0.1)
            assert registry.cleanup() == 1
            # In Option C, memory is pruned but durable journal retains completed job
            durable_status = registry.status(job_id)
            assert durable_status["state"] == "completed"
            assert durable_status["health"] == "COMPLETED"
        finally:
            registry.close()
            if monkeypatch is None:
                mp.undo()


def test_closed_registry_rejects_new_jobs(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    mp.setattr(
        "codex_agy_bridge.agy_jobs.run_agy",
        lambda *args, **kwargs: AgyResult(text="DONE", exit_code=0, used_pty=False),
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "jobs.sqlite3"
        registry = AgyJobRegistry(db_path=db_file)
        registry.close()

        with pytest.raises(RuntimeError, match="closed"):
            registry.start("Implement page")
        if monkeypatch is None:
            mp.undo()


def test_terminal_display_selects_visible_runner(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    calls = []

    def fake_visible_run(*args, **kwargs):
        calls.append((args, kwargs))
        return AgyResult(text="shown", exit_code=0, used_pty=False)

    mp.setattr("codex_agy_bridge.agy_jobs.run_agy_visible", fake_visible_run)
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "jobs.sqlite3"
        registry = AgyJobRegistry(db_path=db_file)
        try:
            job_id = registry.start("Show task", display_mode="terminal")
            for _ in range(20):
                status = registry.status(job_id)
                if status["state"] == "completed":
                    break
                time.sleep(0.01)
            assert status["text"] == "shown"
            assert calls
        finally:
            registry.close()
            if monkeypatch is None:
                mp.undo()


def test_wait_reports_unknown_job_immediately():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "jobs.sqlite3"
        registry = AgyJobRegistry(db_path=db_file)
        try:
            res = registry.wait("missing", wait_seconds=10.0)
            assert res == {
                "job_id": "missing",
                "state": "unknown",
                "health": "UNKNOWN",
                "error": "job not found",
            }
        finally:
            registry.close()


def test_wait_returns_immediately_for_completed_or_failed_job(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    mp.setattr(
        "codex_agy_bridge.agy_jobs.run_agy",
        lambda *args, **kwargs: AgyResult(text="DONE", exit_code=0, used_pty=False),
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "jobs.sqlite3"
        registry = AgyJobRegistry(db_path=db_file)
        try:
            job_id = registry.start("Quick task")
            status = registry.wait(job_id, wait_seconds=5.0)
            assert status["state"] == "completed"
            assert status["health"] == "COMPLETED"

            # Subsequent wait returns immediately
            status2 = registry.wait(job_id, wait_seconds=60.0)
            assert status2["state"] == "completed"
        finally:
            registry.close()
            if monkeypatch is None:
                mp.undo()


def test_wait_completes_within_window_returns_terminal(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    def quick_run(*args, **kwargs):
        time.sleep(0.02)
        return AgyResult(text="FINISHED", exit_code=0, used_pty=False)

    mp.setattr("codex_agy_bridge.agy_jobs.run_agy", quick_run)
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "jobs.sqlite3"
        registry = AgyJobRegistry(db_path=db_file)
        try:
            start_t = time.monotonic()
            job_id = registry.start("Wait task")
            res = registry.wait(job_id, wait_seconds=10.0)
            duration = time.monotonic() - start_t
            assert res["state"] == "completed"
            assert res["text"] == "FINISHED"
            assert duration < 2.0
        finally:
            registry.close()
            if monkeypatch is None:
                mp.undo()


def test_wait_fails_within_window_returns_terminal_failure(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    def failing_run(*args, **kwargs):
        time.sleep(0.02)
        return AgyResult(text="CRASH", exit_code=2, used_pty=False)

    mp.setattr("codex_agy_bridge.agy_jobs.run_agy", failing_run)
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "jobs.sqlite3"
        registry = AgyJobRegistry(db_path=db_file)
        try:
            start_t = time.monotonic()
            job_id = registry.start("Failing wait task")
            res = registry.wait(job_id, wait_seconds=10.0)
            duration = time.monotonic() - start_t
            assert res["state"] == "failed"
            assert res["exit_code"] == 2
            assert duration < 2.0
        finally:
            registry.close()
            if monkeypatch is None:
                mp.undo()


def test_wait_timeout_preserves_active_job(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    unblock = threading.Event()

    def hanging_run(*args, **kwargs):
        unblock.wait(timeout=5.0)
        return AgyResult(text="LATE", exit_code=0, used_pty=False)

    mp.setattr("codex_agy_bridge.agy_jobs.run_agy", hanging_run)
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "jobs.sqlite3"
        registry = AgyJobRegistry(db_path=db_file)
        try:
            job_id = registry.start("Hanging task")
            res = registry.wait(job_id, wait_seconds=0.05)
            assert res["job_id"] == job_id
            assert res["state"] in {"queued", "running"}
            status = registry.status(job_id)
            assert status["state"] in {"queued", "running"}
            unblock.set()
            final_res = registry.wait(job_id, wait_seconds=5.0)
            assert final_res["state"] == "completed"
            assert final_res["text"] == "LATE"
        finally:
            unblock.set()
            registry.close()
            if monkeypatch is None:
                mp.undo()


def test_wait_rejects_invalid_wait_seconds():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "jobs.sqlite3"
        registry = AgyJobRegistry(db_path=db_file)
        try:
            with pytest.raises(ValueError, match="wait_seconds"):
                registry.wait("any", wait_seconds=0)
            with pytest.raises(ValueError, match="wait_seconds"):
                registry.wait("any", wait_seconds=-1.0)
            with pytest.raises(ValueError, match="wait_seconds"):
                registry.wait("any", wait_seconds=float("nan"))
            with pytest.raises(ValueError, match="wait_seconds"):
                registry.wait("any", wait_seconds=float("inf"))
            with pytest.raises(ValueError, match="wait_seconds"):
                registry.wait("any", wait_seconds=True)
        finally:
            registry.close()


def test_timestamps_and_elapsed_progression_and_freeze(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    started_evt = threading.Event()
    finish_evt = threading.Event()

    def controlled_run(*args, **kwargs):
        started_evt.set()
        finish_evt.wait(timeout=5.0)
        return AgyResult(text="DONE", exit_code=0, used_pty=False)

    mp.setattr("codex_agy_bridge.agy_jobs.run_agy", controlled_run)
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "jobs.sqlite3"
        registry = AgyJobRegistry(db_path=db_file)
        try:
            job_id = registry.start("Controlled timing")
            assert started_evt.wait(timeout=2.0)
            running_status = registry.status(job_id)
            assert running_status["state"] == "running"
            assert running_status["submitted_at"] is not None
            assert running_status["started_at"] is not None
            assert running_status["completed_at"] is None
            assert running_status["elapsed_seconds"] >= 0.0

            finish_evt.set()
            completed_status = registry.wait(job_id, wait_seconds=2.0)
            assert completed_status["state"] == "completed"
            assert completed_status["completed_at"] is not None
            frozen_elapsed = completed_status["elapsed_seconds"]
            frozen_completed_at = completed_status["completed_at"]

            time.sleep(0.05)
            later_status = registry.status(job_id)
            assert later_status["elapsed_seconds"] == frozen_elapsed
            assert later_status["completed_at"] == frozen_completed_at
        finally:
            finish_evt.set()
            registry.close()
            if monkeypatch is None:
                mp.undo()


def test_task_key_semantics(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    blocker = threading.Event()

    def blocking_run(*args, **kwargs):
        blocker.wait(timeout=5.0)
        return AgyResult(text="DONE", exit_code=0, used_pty=False)

    mp.setattr("codex_agy_bridge.agy_jobs.run_agy", blocking_run)
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "jobs.sqlite3"
        registry = AgyJobRegistry(db_path=db_file)
        try:
            j1 = registry.start("Task 1", task_key=None)
            j2 = registry.start("Task 2", task_key=None)
            assert j1 != j2

            j_a = registry.start("Task A", task_key="key-A")
            status_a = registry.status(j_a)
            assert status_a["task_key"] == "key-A"

            with pytest.raises(RuntimeError) as exc_info:
                registry.start("Task A duplicate", task_key="key-A")
            assert "DUPLICATE_ACTIVE_TASK" in str(exc_info.value)
            assert j_a in str(exc_info.value)

            j_b = registry.start("Task B", task_key="key-B")
            assert j_b != j_a

            blocker.set()
            res_a = registry.wait(j_a, wait_seconds=2.0)
            assert res_a["state"] == "completed"
            assert res_a["task_key"] == "key-A"

            j_a2 = registry.start("Task A restart", task_key="key-A")
            assert j_a2 != j_a
        finally:
            blocker.set()
            registry.close()
            if monkeypatch is None:
                mp.undo()


def test_status_remains_active_until_completion_recording_finishes(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    runner_finished = threading.Event()
    unblock_completion = threading.Event()

    def fake_run(*args, **kwargs):
        runner_finished.set()
        return AgyResult(text="DONE", exit_code=0, used_pty=False)

    mp.setattr("codex_agy_bridge.agy_jobs.run_agy", fake_run)
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "jobs.sqlite3"
        registry = AgyJobRegistry(db_path=db_file)
        orig_mark_completed = registry._mark_completed

        def delayed_mark_completed(job_id, result=None, exc=None):
            unblock_completion.wait(timeout=5.0)
            orig_mark_completed(job_id, result=result, exc=exc)

        registry._mark_completed = delayed_mark_completed  # type: ignore[method-assign]

        try:
            job_id = registry.start("Race ordering test")
            assert runner_finished.wait(timeout=2.0)

            active_status = registry.status(job_id)
            assert active_status["state"] in {"queued", "running"}
            assert active_status["completed_at"] is None
            assert isinstance(active_status["elapsed_seconds"], float)
            assert active_status["elapsed_seconds"] >= 0.0

            unblock_completion.set()

            completed_status = registry.wait(job_id, wait_seconds=2.0)
            assert completed_status["state"] == "completed"
            assert completed_status["text"] == "DONE"
            assert completed_status["completed_at"] is not None
            assert isinstance(completed_status["elapsed_seconds"], float)
            assert completed_status["elapsed_seconds"] >= 0.0

            frozen_elapsed = completed_status["elapsed_seconds"]
            frozen_completed_at = completed_status["completed_at"]

            time.sleep(0.02)
            later_status = registry.status(job_id)
            assert later_status["elapsed_seconds"] == frozen_elapsed
            assert later_status["completed_at"] == frozen_completed_at
        finally:
            unblock_completion.set()
            registry.close()
            if monkeypatch is None:
                mp.undo()


def test_runner_exception_records_completion_before_terminal_failure(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    runner_raised = threading.Event()
    unblock_completion = threading.Event()

    def crashing_run(*args, **kwargs):
        runner_raised.set()
        raise RuntimeError("runner crashed")

    mp.setattr("codex_agy_bridge.agy_jobs.run_agy", crashing_run)
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "jobs.sqlite3"
        registry = AgyJobRegistry(db_path=db_file)
        orig_mark_completed = registry._mark_completed

        def delayed_mark_completed(job_id, result=None, exc=None):
            unblock_completion.wait(timeout=5.0)
            orig_mark_completed(job_id, result=result, exc=exc)

        registry._mark_completed = delayed_mark_completed  # type: ignore[method-assign]

        try:
            job_id = registry.start("Crashing race test")
            assert runner_raised.wait(timeout=2.0)

            active_status = registry.status(job_id)
            assert active_status["state"] in {"queued", "running"}
            assert active_status["completed_at"] is None

            unblock_completion.set()

            final_status = registry.wait(job_id, wait_seconds=2.0)
            assert final_status["state"] == "failed"
            assert "runner crashed" in final_status["error"]
            assert final_status["completed_at"] is not None
            assert isinstance(final_status["elapsed_seconds"], float)
            assert final_status["elapsed_seconds"] >= 0.0

            frozen_elapsed = final_status["elapsed_seconds"]
            frozen_completed_at = final_status["completed_at"]

            time.sleep(0.02)
            later_status = registry.status(job_id)
            assert later_status["elapsed_seconds"] == frozen_elapsed
            assert later_status["completed_at"] == frozen_completed_at
        finally:
            unblock_completion.set()
            registry.close()
            if monkeypatch is None:
                mp.undo()


def test_status_does_not_block_while_worktree_observation_is_slow(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    obs_entered = threading.Event()
    obs_unblock = threading.Event()
    runner_started = threading.Event()
    runner_unblock = threading.Event()

    def slow_observe(workdir):
        obs_entered.set()
        obs_unblock.wait(timeout=5.0)
        return "fake_fingerprint"

    def fast_runner(*args, **kwargs):
        runner_started.set()
        runner_unblock.wait(timeout=5.0)
        return AgyResult(text="DONE", exit_code=0, used_pty=False)

    mp.setattr("codex_agy_bridge.agy_jobs._observe_worktree", slow_observe)
    mp.setattr("codex_agy_bridge.agy_jobs.run_agy", fast_runner)

    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "jobs.sqlite3"
        registry = AgyJobRegistry(db_path=db_file, watchdog_interval=0.05)
        try:
            job_id = registry.start("Slow observation test", workdir="C:\\dummy_workdir")
            # 1. Runner must start immediately without waiting for observation
            assert runner_started.wait(timeout=1.0), "Worker runner start was delayed by observation"

            # 2. Watchdog enters observation
            assert obs_entered.wait(timeout=2.0), "Watchdog did not enter _observe_worktree"

            # 3. While watchdog is stuck in observation, status must return immediately
            t0 = time.perf_counter()
            status = registry.status(job_id)
            query_duration = time.perf_counter() - t0

            assert query_duration < 0.1, f"registry.status blocked for {query_duration:.3f}s during observation"
            assert status["job_id"] == job_id
            assert status["state"] in {"queued", "running"}

            # Status for another/missing job must also not block
            t0_other = time.perf_counter()
            other_status = registry.status("non_existent_id")
            other_duration = time.perf_counter() - t0_other

            assert other_duration < 0.1, f"registry.status for other job blocked for {other_duration:.3f}s"
            assert other_status["state"] == "unknown"

            # Now release observation and runner and verify clean completion
            obs_unblock.set()
            runner_unblock.set()

            terminal_status = registry.wait(job_id, wait_seconds=2.0)
            assert terminal_status["state"] == "completed"
            assert terminal_status["text"] == "DONE"
        finally:
            obs_unblock.set()
            runner_unblock.set()
            registry.close()
            if monkeypatch is None:
                mp.undo()


def test_status_does_not_block_while_watchdog_observation_is_slow(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    runner_unblock = threading.Event()
    watchdog_obs_entered = threading.Event()
    watchdog_obs_unblock = threading.Event()

    def slow_observe(workdir):
        watchdog_obs_entered.set()
        watchdog_obs_unblock.wait(timeout=5.0)
        return "fake_fingerprint"

    def blocking_runner(*args, **kwargs):
        runner_unblock.wait(timeout=5.0)
        return AgyResult(text="DONE", exit_code=0, used_pty=False)

    mp.setattr("codex_agy_bridge.agy_jobs._observe_worktree", slow_observe)
    mp.setattr("codex_agy_bridge.agy_jobs.run_agy", blocking_runner)

    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "jobs.sqlite3"
        registry = AgyJobRegistry(db_path=db_file, watchdog_interval=0.05)
        try:
            job_id = registry.start("Watchdog slow observation test", workdir="C:\\dummy_workdir")
            assert watchdog_obs_entered.wait(timeout=2.0), "Watchdog did not enter _observe_worktree"

            # While watchdog is stuck in _observe_worktree, status must return immediately
            t0 = time.perf_counter()
            status = registry.status(job_id)
            query_duration = time.perf_counter() - t0

            assert query_duration < 0.1, f"registry.status blocked for {query_duration:.3f}s during watchdog observation"
            assert status["job_id"] == job_id
            assert status["state"] in {"queued", "running"}

            watchdog_obs_unblock.set()
            runner_unblock.set()

            terminal_status = registry.wait(job_id, wait_seconds=2.0)
            assert terminal_status["state"] == "completed"
        finally:
            watchdog_obs_unblock.set()
            runner_unblock.set()
            registry.close()
            if monkeypatch is None:
                mp.undo()


def test_wait_unknown_job_does_not_hold_registry_lock_during_durable_lookup(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    lookup_entered = threading.Event()
    lookup_unblock = threading.Event()

    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "jobs.sqlite3"
        registry = AgyJobRegistry(db_path=db_file)

        # Start a live job
        mp.setattr(
            "codex_agy_bridge.agy_jobs.run_agy",
            lambda *args, **kwargs: time.sleep(0.5) or AgyResult(text="LIVE_DONE", exit_code=0, used_pty=False),
        )
        live_id = registry.start("Live job")

        orig_get_job = registry._store.get_job

        def hooked_get_job(job_id: str):
            if job_id == "slow_unknown_job":
                lookup_entered.set()
                lookup_unblock.wait(timeout=5.0)
            return orig_get_job(job_id)

        mp.setattr(registry._store, "get_job", hooked_get_job)

        wait_thread_res: dict = {}

        def wait_worker():
            wait_thread_res["res"] = registry.wait("slow_unknown_job", wait_seconds=5.0)

        t = threading.Thread(target=wait_worker)
        t.start()

        try:
            assert lookup_entered.wait(timeout=2.0), "registry.wait did not enter durable lookup"

            # While durable lookup for unknown job is blocked in get_job,
            # live status query must NOT block on registry lock
            t0 = time.perf_counter()
            status = registry.status(live_id)
            status_duration = time.perf_counter() - t0
            assert status_duration < 0.1, f"registry.status for live job blocked ({status_duration:.3f}s)"
            assert status["state"] in {"queued", "running"}

            # Other status query must also not block on registry lock
            t1 = time.perf_counter()
            other_status = registry.status("immediate_missing")
            assert other_status["state"] == "unknown"

            # Live job wait with short timeout returns without deadlocking or blocking on durable lookup
            t2 = time.perf_counter()
            live_wait = registry.wait(live_id, wait_seconds=0.01)
            wait_duration = time.perf_counter() - t2
            assert wait_duration < 0.1, f"registry.wait for live job blocked ({wait_duration:.3f}s)"
            assert live_wait["state"] in {"queued", "running"}
        finally:
            lookup_unblock.set()
            t.join(timeout=2.0)
            registry.close()
            if monkeypatch is None:
                mp.undo()

        assert wait_thread_res.get("res", {}).get("state") == "unknown"
        assert wait_thread_res.get("res", {}).get("error") == "job not found"


def test_wait_unknown_durable_completed_returns_terminal_state():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "jobs.sqlite3"
        store = DurableJobStore(db_path=db_file)
        now_iso = _utc_now_iso()
        store.reserve_and_create(
            job_id="persisted_completed_123",
            task_key="task-comp",
            workdir="C:\\test",
            prompt_hash="abc",
            owner_session_id="session_xyz",
            now_iso=now_iso,
        )
        store.mark_started("persisted_completed_123", now_iso, now_iso)
        store.mark_terminal(
            job_id="persisted_completed_123",
            state="completed",
            health="COMPLETED",
            exit_code=0,
            error=None,
            result_text="HISTORICAL_SUCCESS",
            result_truncated=False,
            used_pty=False,
            started_at=now_iso,
            completed_at=now_iso,
            elapsed_seconds=1.23,
            now_iso=now_iso,
        )

        registry = AgyJobRegistry(db_path=db_file)
        try:
            assert "persisted_completed_123" not in registry._jobs
            t0 = time.perf_counter()
            res = registry.wait("persisted_completed_123", wait_seconds=10.0)
            duration = time.perf_counter() - t0

            assert duration < 0.2, f"wait on durable completed job took too long: {duration:.3f}s"
            assert res["job_id"] == "persisted_completed_123"
            assert res["state"] == "completed"
            assert res["health"] == "COMPLETED"
            assert res["text"] == "HISTORICAL_SUCCESS"
            assert res["exit_code"] == 0
            assert res["task_key"] == "task-comp"
        finally:
            registry.close()


def test_wait_unknown_durable_interrupted_returns_recovery_state():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "jobs.sqlite3"
        store = DurableJobStore(db_path=db_file)
        now_iso = _utc_now_iso()
        store.reserve_and_create(
            job_id="interrupted_job_456",
            task_key="task-interrupted",
            workdir="C:\\test",
            prompt_hash="abc",
            owner_session_id="old_crashed_session",
            now_iso=now_iso,
        )
        store.mark_started("interrupted_job_456", now_iso, now_iso)

        registry = AgyJobRegistry(db_path=db_file)
        try:
            assert "interrupted_job_456" not in registry._jobs
            t0 = time.perf_counter()
            res = registry.wait("interrupted_job_456", wait_seconds=10.0)
            duration = time.perf_counter() - t0

            assert duration < 0.2, f"wait on durable interrupted job took too long: {duration:.3f}s"
            assert res["job_id"] == "interrupted_job_456"
            assert res["state"] == "unknown"
            assert res["health"] == "INTERRUPTED"
            assert res["recovery_state"] == "interrupted"
            assert "INTERRUPTED: job interrupted across session boundary" in res["error"]
            assert res["task_key"] == "task-interrupted"
        finally:
            registry.close()


def test_wait_real_future_waiting_remains_outside_registry_lock(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    runner_started = threading.Event()
    runner_unblock = threading.Event()

    def slow_runner(*args, **kwargs):
        runner_started.set()
        runner_unblock.wait(timeout=5.0)
        return AgyResult(text="ASYNC_DONE", exit_code=0, used_pty=False)

    mp.setattr("codex_agy_bridge.agy_jobs.run_agy", slow_runner)

    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "jobs.sqlite3"
        registry = AgyJobRegistry(db_path=db_file)
        try:
            job_id = registry.start("Slow future task")
            assert runner_started.wait(timeout=2.0)

            wait_res: dict = {}

            def wait_worker():
                wait_res["res"] = registry.wait(job_id, wait_seconds=5.0)

            wait_thread = threading.Thread(target=wait_worker)
            wait_thread.start()

            time.sleep(0.05)
            t0 = time.perf_counter()
            with registry._lock:
                lock_acquired = True
            lock_duration = time.perf_counter() - t0
            assert lock_acquired is True
            assert lock_duration < 0.05, f"Lock was held by wait thread ({lock_duration:.3f}s)"

            status = registry.status(job_id)
            assert status["state"] in {"queued", "running"}

            other_id = registry.start("Another quick task")
            other_status = registry.status(other_id)
            assert other_status["job_id"] == other_id

            t1 = time.perf_counter()
            timeout_res = registry.wait(job_id, wait_seconds=0.05)
            timeout_duration = time.perf_counter() - t1
            assert timeout_duration < 0.2
            assert timeout_res["state"] in {"queued", "running"}

            rec = registry._jobs.get(job_id)
            assert rec is not None
            assert not rec.future.done()
            assert not rec.future.cancelled()

            runner_unblock.set()
            wait_thread.join(timeout=2.0)
            assert wait_res.get("res", {}).get("state") == "completed"
            assert wait_res.get("res", {}).get("text") == "ASYNC_DONE"
        finally:
            runner_unblock.set()
            registry.close()
            if monkeypatch is None:
                mp.undo()


def test_instant_successful_worker_sets_completed_at_and_freezes_elapsed(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    mp.setattr(
        "codex_agy_bridge.agy_jobs.run_agy",
        lambda *args, **kwargs: AgyResult(text="FAST_DONE", exit_code=0, used_pty=False),
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "jobs.sqlite3"
        registry = AgyJobRegistry(db_path=db_file)
        try:
            job_id = registry.start("Instant success test")
            for _ in range(20):
                res = registry.status(job_id)
                if res["state"] == "completed":
                    break
                time.sleep(0.005)

            assert res["state"] == "completed"
            assert res["health"] == "COMPLETED"
            assert res["text"] == "FAST_DONE"
            assert res["submitted_at"] is not None
            assert res["started_at"] is not None
            assert res["completed_at"] is not None
            assert isinstance(res["elapsed_seconds"], float)
            assert res["elapsed_seconds"] >= 0.0

            frozen_completed_at = res["completed_at"]
            frozen_elapsed = res["elapsed_seconds"]

            time.sleep(0.05)
            status_later = registry.status(job_id)
            assert status_later["completed_at"] == frozen_completed_at
            assert status_later["elapsed_seconds"] == frozen_elapsed
        finally:
            registry.close()
            if monkeypatch is None:
                mp.undo()


def test_instant_failed_worker_terminal_metadata_complete(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    def instant_fail(*args, **kwargs):
        return AgyResult(text="FAST_FAIL", exit_code=1, stderr="fail err", used_pty=False)

    mp.setattr("codex_agy_bridge.agy_jobs.run_agy", instant_fail)
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "jobs.sqlite3"
        registry = AgyJobRegistry(db_path=db_file)
        try:
            job_id = registry.start("Instant fail test", task_key="instant-fail-key")
            for _ in range(20):
                res = registry.status(job_id)
                if res["state"] == "failed":
                    break
                time.sleep(0.005)

            assert res["state"] == "failed"
            assert res["health"] == "FAILED"
            assert res["exit_code"] == 1
            assert res["task_key"] == "instant-fail-key"
            assert res["submitted_at"] is not None
            assert res["started_at"] is not None
            assert res["completed_at"] is not None
            assert isinstance(res["elapsed_seconds"], float)
            assert res["elapsed_seconds"] >= 0.0

            frozen_completed_at = res["completed_at"]
            frozen_elapsed = res["elapsed_seconds"]

            time.sleep(0.05)
            status_later = registry.status(job_id)
            assert status_later["completed_at"] == frozen_completed_at
            assert status_later["elapsed_seconds"] == frozen_elapsed
        finally:
            registry.close()
            if monkeypatch is None:
                mp.undo()


def test_submit_exception_reconciles_reservation_and_allows_reuse(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "jobs.sqlite3"
        registry = AgyJobRegistry(db_path=db_file)

        def failing_submit(*args, **kwargs):
            raise RuntimeError("simulated executor failure")

        mp.setattr(registry._executor, "submit", failing_submit)

        try:
            with pytest.raises(RuntimeError, match="simulated executor failure"):
                registry.start("Failing submit task", task_key="reusable-key")

            # 1. No active orphan in memory
            assert len(registry._jobs) == 0

            # 2. Reconciled terminal failure in durable store
            recent = registry.recent(limit=10, task_key="reusable-key")
            assert len(recent) == 1
            assert recent[0]["state"] == "failed"
            assert recent[0]["health"] == "FAILED"

            job_rec = registry._store.get_job(recent[0]["job_id"])
            assert job_rec is not None
            assert job_rec["state"] == "failed"
            assert "SUBMIT_FAILED" in (job_rec.get("error") or "")
            assert job_rec["completed_at"] is not None

            # 3. Same task_key can be successfully submitted now without DUPLICATE_ACTIVE_TASK
            mp.undo()
            mp.setattr(
                "codex_agy_bridge.agy_jobs.run_agy",
                lambda *args, **kwargs: AgyResult(text="REUSE_SUCCESS", exit_code=0, used_pty=False),
            )

            new_job_id = registry.start("Retry task", task_key="reusable-key")
            res = registry.wait(new_job_id, wait_seconds=2.0)
            assert res["state"] == "completed"
            assert res["text"] == "REUSE_SUCCESS"
            assert res["task_key"] == "reusable-key"
        finally:
            registry.close()
            if monkeypatch is None:
                mp.undo()


def test_deterministic_close_start_race_no_orphan_active_durable_job(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "jobs.sqlite3"
        registry = AgyJobRegistry(db_path=db_file)

        orig_reserve = registry._store.reserve_and_create

        def hooked_reserve(*args, **kwargs):
            orig_reserve(*args, **kwargs)
            # Concurrent close occurs right after durable reservation
            registry.close(wait=False, cancel_futures=True)

        mp.setattr(registry._store, "reserve_and_create", hooked_reserve)

        try:
            with pytest.raises(RuntimeError, match="closed"):
                registry.start("Race start", task_key="race-key")

            # Verify no orphan active job in durable store
            recent = registry.recent(limit=10)
            assert len(recent) == 1
            assert recent[0]["state"] in {"failed", "unknown"}
            assert recent[0]["health"] in {"FAILED", "INTERRUPTED"}
            assert recent[0]["state"] not in {"submitted", "queued", "running"}

            job_rec = registry._store.get_job(recent[0]["job_id"])
            assert job_rec is not None
            assert job_rec["state"] == "failed"
            assert job_rec["completed_at"] is not None
        finally:
            registry.close()
            if monkeypatch is None:
                mp.undo()


def test_deterministic_close_after_record_registration_before_gate_release(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    runner_called = threading.Event()

    def fake_runner(*args, **kwargs):
        runner_called.set()
        return AgyResult(text="SHOULD_NOT_RUN", exit_code=0, used_pty=False)

    mp.setattr("codex_agy_bridge.agy_jobs.run_agy", fake_runner)

    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "jobs.sqlite3"
        registry = AgyJobRegistry(db_path=db_file)

        orig_event_init = threading.Event

        def hooked_event_factory():
            evt = orig_event_init()
            orig_set = evt.set

            def hooked_set():
                # Force close precisely after record registration and before gate release
                with registry._lock:
                    in_jobs = len(registry._jobs) > 0
                if in_jobs and not registry._closed:
                    registry.close(wait=False, cancel_futures=True)
                orig_set()

            evt.set = hooked_set
            return evt

        mp.setattr(threading, "Event", hooked_event_factory)

        try:
            with pytest.raises(RuntimeError, match="closed"):
                registry.start("Test close after record registration", task_key="gate-close-key")

            # Assert runner was never executed
            assert not runner_called.is_set()

            # Verify durable store has no active row and was reconciled
            recent = registry.recent(limit=10)
            assert len(recent) == 1
            assert recent[0]["state"] == "failed"
            assert recent[0]["health"] == "FAILED"
            assert recent[0]["state"] not in {"submitted", "queued", "running"}

            job_rec = registry._store.get_job(recent[0]["job_id"])
            assert job_rec is not None
            assert job_rec["state"] == "failed"
            assert job_rec["completed_at"] is not None
            assert "REGISTRY_CLOSED" in str(job_rec["error"])
        finally:
            registry.close()
            mp.undo()





def test_duplicate_task_key_remains_atomic(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    start_barrier = threading.Barrier(5)
    results = []
    errors = []

    def blocking_run(*args, **kwargs):
        time.sleep(0.1)
        return AgyResult(text="ATOMIC_DONE", exit_code=0, used_pty=False)

    mp.setattr("codex_agy_bridge.agy_jobs.run_agy", blocking_run)

    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "jobs.sqlite3"
        registry = AgyJobRegistry(db_path=db_file)

        def worker():
            start_barrier.wait()
            try:
                jid = registry.start("Atomic test", task_key="shared-atomic-key")
                results.append(jid)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        try:
            # Exactly 1 thread succeeds, 4 raise DUPLICATE_ACTIVE_TASK
            assert len(results) == 1, f"Expected 1 winner, got {len(results)}"
            assert len(errors) == 4, f"Expected 4 errors, got {len(errors)}"
            for err in errors:
                assert "DUPLICATE_ACTIVE_TASK" in str(err)

            winner_id = results[0]
            winner_res = registry.wait(winner_id, wait_seconds=2.0)
            assert winner_res["state"] == "completed"
            assert winner_res["text"] == "ATOMIC_DONE"
        finally:
            registry.close()
            if monkeypatch is None:
                mp.undo()


def test_concurrent_status_and_wait_never_observes_unusable_or_none_future(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "jobs.sqlite3"
        registry = AgyJobRegistry(db_path=db_file)

        submit_entered = threading.Event()
        submit_proceed = threading.Event()
        concurrent_errors = []
        observed_states = []

        orig_submit = registry._executor.submit

        def hooked_submit(fn, *args, **kwargs):
            submit_entered.set()
            # Block submit execution until concurrent status and wait have executed
            if not submit_proceed.wait(timeout=3.0):
                concurrent_errors.append(TimeoutError("submit_proceed timed out"))
            return orig_submit(fn, *args, **kwargs)

        mp.setattr(registry._executor, "submit", hooked_submit)

        def slow_run(*args, **kwargs):
            time.sleep(0.05)
            return AgyResult(text="RACE_SAFE_DONE", exit_code=0, used_pty=False)

        mp.setattr("codex_agy_bridge.agy_jobs.run_agy", slow_run)

        try:
            started_job_id = []

            def starter():
                jid = registry.start("Test race safety", task_key="race-safe-key")
                started_job_id.append(jid)

            starter_thread = threading.Thread(target=starter)
            starter_thread.start()

            # 1. Wait until executor.submit is in flight
            assert submit_entered.wait(timeout=3.0)

            # 2. While submit is in flight, check memory invariants:
            # No record in registry._jobs may ever have future=None or unusable Future
            with registry._lock:
                for rec in registry._jobs.values():
                    assert rec.future is not None
                    assert isinstance(rec.future, Future)

            # Query durable store to find the in-flight job_id
            recent = registry.recent(limit=10, task_key="race-safe-key")
            assert len(recent) == 1
            in_flight_job_id = recent[0]["job_id"]

            # Concurrent status() must not crash (AttributeError / NoneType)
            st = registry.status(in_flight_job_id)
            observed_states.append(st["state"])
            assert st["state"] in {"submitted", "queued", "running"}
            assert st["health"] in {"SUBMITTED", "QUEUED", "HEALTHY"}

            # Concurrent wait() with small timeout must not crash
            wt = registry.wait(in_flight_job_id, wait_seconds=0.01)
            observed_states.append(wt["state"])

            # 3. Release submit and let start() complete
            submit_proceed.set()
            starter_thread.join(timeout=3.0)
            assert not starter_thread.is_alive()
            assert len(started_job_id) == 1
            job_id = started_job_id[0]

            # In-memory record must now exist and have a valid, usable Future
            with registry._lock:
                record = registry._jobs.get(job_id)
                assert record is not None
                assert record.future is not None
                assert isinstance(record.future, Future)

            # 4. Wait for completion and verify success
            final_status = registry.wait(job_id, wait_seconds=3.0)
            assert final_status["state"] == "completed"
            assert final_status["text"] == "RACE_SAFE_DONE"
            assert len(concurrent_errors) == 0
        finally:
            submit_proceed.set()
            registry.close()
            if monkeypatch is None:
                mp.undo()


def test_high_concurrency_hammer_status_wait_and_watchdog_never_crashes(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "jobs.sqlite3"
        registry = AgyJobRegistry(db_path=db_file, watchdog_interval=0.02)

        def fast_run(*args, **kwargs):
            time.sleep(0.01)
            return AgyResult(text="HAMMER_DONE", exit_code=0, used_pty=False)

        mp.setattr("codex_agy_bridge.agy_jobs.run_agy", fast_run)

        stop_hammer = threading.Event()
        hammer_errors = []
        job_ids = []

        def hammer_status_wait():
            while not stop_hammer.is_set():
                with registry._lock:
                    current_jobs = list(registry._jobs.keys())
                    for rec in registry._jobs.values():
                        if rec.future is None or not isinstance(rec.future, Future):
                            hammer_errors.append(AssertionError(f"Found invalid record future: {rec.future}"))
                for jid in current_jobs:
                    try:
                        st = registry.status(jid)
                        assert "state" in st
                        assert "health" in st
                        wt = registry.wait(jid, wait_seconds=0.001)
                        assert "state" in wt
                    except Exception as ex:
                        hammer_errors.append(ex)
                time.sleep(0.001)

        hammer_threads = [threading.Thread(target=hammer_status_wait) for _ in range(4)]
        for ht in hammer_threads:
            ht.start()

        try:
            for i in range(10):
                jid = registry.start(f"Hammer prompt {i}", task_key=f"hammer-key-{i}")
                job_ids.append(jid)
                time.sleep(0.005)

            for jid in job_ids:
                res = registry.wait(jid, wait_seconds=3.0)
                assert res["state"] == "completed"
                assert res["text"] == "HAMMER_DONE"
        finally:
            stop_hammer.set()
            for ht in hammer_threads:
                ht.join(timeout=2.0)
            registry.close()
            if monkeypatch is None:
                mp.undo()

        assert len(hammer_errors) == 0, f"Encountered concurrent hammer errors: {hammer_errors}"
