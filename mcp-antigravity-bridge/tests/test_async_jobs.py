from __future__ import annotations

import threading
import time

import pytest

from codex_agy_bridge.agy_jobs import AgyJobRegistry
from codex_agy_bridge.agy_runner import AgyResult


def test_start_and_status_reports_completed_job(monkeypatch):
    def fake_run_agy(*args, **kwargs):
        return AgyResult(text="DONE", exit_code=0, used_pty=False)

    monkeypatch.setattr("codex_agy_bridge.agy_jobs.run_agy", fake_run_agy)
    registry = AgyJobRegistry()

    job_id = registry.start("Implement page", workdir="C:\\work")
    for _ in range(20):
        status = registry.status(job_id)
        if status["state"] == "completed":
            break
        time.sleep(0.01)

    assert status["job_id"] == job_id
    assert status["state"] == "completed"
    assert status["text"] == "DONE"
    assert status["exit_code"] == 0
    assert status["used_pty"] is False
    assert status["submitted_at"]
    assert status["started_at"]
    assert status["completed_at"]
    assert isinstance(status["elapsed_seconds"], float)
    assert status["elapsed_seconds"] >= 0.0


def test_status_reports_running_job(monkeypatch):
    def slow_run_agy(*args, **kwargs):
        time.sleep(0.1)
        return AgyResult(text="DONE", exit_code=0, used_pty=False)

    monkeypatch.setattr("codex_agy_bridge.agy_jobs.run_agy", slow_run_agy)
    registry = AgyJobRegistry()

    job_id = registry.start("Implement page")
    status = registry.status(job_id)

    assert status["job_id"] == job_id
    assert status["state"] in {"queued", "running"}
    assert status["submitted_at"]
    assert isinstance(status["elapsed_seconds"], float)
    assert status["elapsed_seconds"] >= 0.0


def test_status_reports_unknown_job():
    registry = AgyJobRegistry()

    assert registry.status("missing") == {
        "job_id": "missing",
        "state": "unknown",
        "error": "job not found",
    }


def test_status_reports_nonzero_exit_as_failed(monkeypatch):
    def failed_run_agy(*args, **kwargs):
        return AgyResult(text="agy failed", exit_code=1, used_pty=False)

    monkeypatch.setattr("codex_agy_bridge.agy_jobs.run_agy", failed_run_agy)
    registry = AgyJobRegistry()

    job_id = registry.start("Fail this task")
    for _ in range(20):
        status = registry.status(job_id)
        if status["state"] == "failed":
            break
        time.sleep(0.01)

    assert status["job_id"] == job_id
    assert status["state"] == "failed"
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


def test_status_reports_login_required_for_authentication_failure(monkeypatch):
    def failed_run_agy(*args, **kwargs):
        return AgyResult(text="authentication required", exit_code=1, used_pty=False)

    monkeypatch.setattr("codex_agy_bridge.agy_jobs.run_agy", failed_run_agy)
    registry = AgyJobRegistry()
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


def test_completed_jobs_are_pruned_after_retention(monkeypatch):
    monkeypatch.setattr(
        "codex_agy_bridge.agy_jobs.run_agy",
        lambda *args, **kwargs: AgyResult(text="DONE", exit_code=0, used_pty=False),
    )
    registry = AgyJobRegistry(retention_seconds=0.05)
    try:
        job_id = registry.start("Implement page")
        for _ in range(20):
            if registry.status(job_id)["state"] == "completed":
                break
            time.sleep(0.01)
        time.sleep(0.1)
        assert registry.cleanup() == 1
        assert registry.status(job_id)["state"] == "unknown"
    finally:
        registry.close()


def test_closed_registry_rejects_new_jobs(monkeypatch):
    monkeypatch.setattr(
        "codex_agy_bridge.agy_jobs.run_agy",
        lambda *args, **kwargs: AgyResult(text="DONE", exit_code=0, used_pty=False),
    )
    registry = AgyJobRegistry()
    registry.close()

    with pytest.raises(RuntimeError, match="closed"):
        registry.start("Implement page")


def test_terminal_display_selects_visible_runner(monkeypatch):
    calls = []

    def fake_visible_run(*args, **kwargs):
        calls.append((args, kwargs))
        return AgyResult(text="shown", exit_code=0, used_pty=False)

    monkeypatch.setattr("codex_agy_bridge.agy_jobs.run_agy_visible", fake_visible_run)
    registry = AgyJobRegistry()
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


def test_wait_reports_unknown_job_immediately():
    registry = AgyJobRegistry()
    res = registry.wait("missing", wait_seconds=10.0)
    assert res == {
        "job_id": "missing",
        "state": "unknown",
        "error": "job not found",
    }


def test_wait_returns_immediately_for_completed_or_failed_job(monkeypatch):
    monkeypatch.setattr(
        "codex_agy_bridge.agy_jobs.run_agy",
        lambda *args, **kwargs: AgyResult(text="DONE", exit_code=0, used_pty=False),
    )
    registry = AgyJobRegistry()
    job_id = registry.start("Quick task")
    status = registry.wait(job_id, wait_seconds=5.0)
    assert status["state"] == "completed"

    # Subsequent wait returns immediately
    status2 = registry.wait(job_id, wait_seconds=60.0)
    assert status2["state"] == "completed"


def test_wait_completes_within_window_returns_terminal(monkeypatch):
    def quick_run(*args, **kwargs):
        time.sleep(0.02)
        return AgyResult(text="FINISHED", exit_code=0, used_pty=False)

    monkeypatch.setattr("codex_agy_bridge.agy_jobs.run_agy", quick_run)
    registry = AgyJobRegistry()
    start_t = time.monotonic()
    job_id = registry.start("Wait task")
    res = registry.wait(job_id, wait_seconds=10.0)
    duration = time.monotonic() - start_t
    assert res["state"] == "completed"
    assert res["text"] == "FINISHED"
    assert duration < 2.0


def test_wait_fails_within_window_returns_terminal_failure(monkeypatch):
    def failing_run(*args, **kwargs):
        time.sleep(0.02)
        return AgyResult(text="CRASH", exit_code=2, used_pty=False)

    monkeypatch.setattr("codex_agy_bridge.agy_jobs.run_agy", failing_run)
    registry = AgyJobRegistry()
    start_t = time.monotonic()
    job_id = registry.start("Failing wait task")
    res = registry.wait(job_id, wait_seconds=10.0)
    duration = time.monotonic() - start_t
    assert res["state"] == "failed"
    assert res["exit_code"] == 2
    assert duration < 2.0


def test_wait_timeout_preserves_active_job(monkeypatch):
    unblock = threading.Event()

    def hanging_run(*args, **kwargs):
        unblock.wait(timeout=5.0)
        return AgyResult(text="LATE", exit_code=0, used_pty=False)

    monkeypatch.setattr("codex_agy_bridge.agy_jobs.run_agy", hanging_run)
    registry = AgyJobRegistry()
    try:
        job_id = registry.start("Hanging task")
        res = registry.wait(job_id, wait_seconds=0.05)
        assert res["job_id"] == job_id
        assert res["state"] in {"queued", "running"}
        # Verify job is still active and not marked failed
        status = registry.status(job_id)
        assert status["state"] in {"queued", "running"}
        # Now unblock so worker completes
        unblock.set()
        final_res = registry.wait(job_id, wait_seconds=5.0)
        assert final_res["state"] == "completed"
        assert final_res["text"] == "LATE"
    finally:
        unblock.set()
        registry.close()


def test_wait_rejects_invalid_wait_seconds():
    registry = AgyJobRegistry()
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


def test_timestamps_and_elapsed_progression_and_freeze(monkeypatch):
    started_evt = threading.Event()
    finish_evt = threading.Event()

    def controlled_run(*args, **kwargs):
        started_evt.set()
        finish_evt.wait(timeout=5.0)
        return AgyResult(text="DONE", exit_code=0, used_pty=False)

    monkeypatch.setattr("codex_agy_bridge.agy_jobs.run_agy", controlled_run)
    registry = AgyJobRegistry()
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


def test_task_key_semantics(monkeypatch):
    blocker = threading.Event()

    def blocking_run(*args, **kwargs):
        blocker.wait(timeout=5.0)
        return AgyResult(text="DONE", exit_code=0, used_pty=False)

    monkeypatch.setattr("codex_agy_bridge.agy_jobs.run_agy", blocking_run)
    registry = AgyJobRegistry()
    try:
        # None permits multiple concurrent
        j1 = registry.start("Task 1", task_key=None)
        j2 = registry.start("Task 2", task_key=None)
        assert j1 != j2

        # task_key="key-A"
        j_a = registry.start("Task A", task_key="key-A")
        status_a = registry.status(j_a)
        assert status_a["task_key"] == "key-A"

        # Duplicate active task_key="key-A" is rejected
        with pytest.raises(RuntimeError) as exc_info:
            registry.start("Task A duplicate", task_key="key-A")
        assert "DUPLICATE_ACTIVE_TASK" in str(exc_info.value)
        assert j_a in str(exc_info.value)

        # Different task_key="key-B" is allowed
        j_b = registry.start("Task B", task_key="key-B")
        assert j_b != j_a

        # Complete active tasks
        blocker.set()
        res_a = registry.wait(j_a, wait_seconds=2.0)
        assert res_a["state"] == "completed"
        assert res_a["task_key"] == "key-A"

        # Completed key can be restarted
        j_a2 = registry.start("Task A restart", task_key="key-A")
        assert j_a2 != j_a
    finally:
        blocker.set()
        registry.close()


def test_status_remains_active_until_completion_recording_finishes(monkeypatch):
    runner_finished = threading.Event()
    unblock_completion = threading.Event()

    def fake_run(*args, **kwargs):
        runner_finished.set()
        return AgyResult(text="DONE", exit_code=0, used_pty=False)

    monkeypatch.setattr("codex_agy_bridge.agy_jobs.run_agy", fake_run)
    registry = AgyJobRegistry()
    orig_mark_completed = registry._mark_completed

    def delayed_mark_completed(job_id: str):
        unblock_completion.wait(timeout=5.0)
        orig_mark_completed(job_id)

    registry._mark_completed = delayed_mark_completed  # type: ignore[method-assign]

    try:
        job_id = registry.start("Race ordering test")
        assert runner_finished.wait(timeout=2.0)

        # After runner has returned, but while completion recording is deliberately blocked:
        # status must remain active rather than terminal with missing timestamps.
        active_status = registry.status(job_id)
        assert active_status["state"] in {"queued", "running"}
        assert active_status["completed_at"] is None
        assert isinstance(active_status["elapsed_seconds"], float)
        assert active_status["elapsed_seconds"] >= 0.0

        # Unblock completion recording
        unblock_completion.set()

        # Once release occurs, terminal status must include non-null completed_at and frozen elapsed
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


def test_runner_exception_records_completion_before_terminal_failure(monkeypatch):
    runner_raised = threading.Event()
    unblock_completion = threading.Event()

    def crashing_run(*args, **kwargs):
        runner_raised.set()
        raise RuntimeError("runner crashed")

    monkeypatch.setattr("codex_agy_bridge.agy_jobs.run_agy", crashing_run)
    registry = AgyJobRegistry()
    orig_mark_completed = registry._mark_completed

    def delayed_mark_completed(job_id: str):
        unblock_completion.wait(timeout=5.0)
        orig_mark_completed(job_id)

    registry._mark_completed = delayed_mark_completed  # type: ignore[method-assign]

    try:
        job_id = registry.start("Crashing race test")
        assert runner_raised.wait(timeout=2.0)

        # Status must remain active while completion recording is blocked
        active_status = registry.status(job_id)
        assert active_status["state"] in {"queued", "running"}
        assert active_status["completed_at"] is None

        # Release completion recording
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
