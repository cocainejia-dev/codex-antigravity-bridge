from __future__ import annotations

import time

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

    assert status == {
        "job_id": job_id,
        "state": "completed",
        "text": "DONE",
        "exit_code": 0,
        "used_pty": False,
    }


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

    assert status == {
        "job_id": job_id,
        "state": "failed",
        "text": "agy failed",
        "exit_code": 1,
        "used_pty": False,
    }
