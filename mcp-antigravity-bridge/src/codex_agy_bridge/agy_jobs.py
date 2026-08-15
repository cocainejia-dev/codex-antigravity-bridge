"""Asynchronous job management for parallel worktree delegation."""

from __future__ import annotations

import atexit
import concurrent.futures
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from threading import RLock
from time import monotonic
from typing import Any
from uuid import uuid4

from .agy_runner import AgyResult, classify_agy_error, describe_agy_failure, run_agy, run_agy_visible


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class _JobRecord:
    future: Future[AgyResult]
    task_key: str | None = None
    submitted_at: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    submitted_mono: float = 0.0
    started_mono: float | None = None
    completed_mono: float | None = None


class AgyJobRegistry:
    """Run bounded agy calls and retain completed results for a finite period."""

    def __init__(self, max_workers: int = 4, retention_seconds: float = 900.0) -> None:
        if retention_seconds < 0:
            raise ValueError("retention_seconds must be non-negative")
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="codex-agy",
        )
        self._retention_seconds = retention_seconds
        self._jobs: dict[str, _JobRecord] = {}
        self._lock = RLock()
        self._closed = False

    def _prune_locked(self) -> int:
        cutoff = monotonic() - self._retention_seconds
        expired = [
            job_id
            for job_id, record in self._jobs.items()
            if record.completed_mono is not None and record.completed_mono <= cutoff
        ]
        for job_id in expired:
            del self._jobs[job_id]
        return len(expired)

    def _mark_started(self, job_id: str) -> None:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is not None and record.started_at is None:
                record.started_at = _utc_now_iso()
                record.started_mono = monotonic()

    def _mark_completed(self, job_id: str) -> None:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is not None:
                now_iso = _utc_now_iso()
                now_mono = monotonic()
                if record.started_at is None:
                    record.started_at = record.submitted_at
                    record.started_mono = record.submitted_mono
                if record.completed_at is None:
                    record.completed_at = now_iso
                    record.completed_mono = now_mono

    def _compute_elapsed_seconds(self, record: _JobRecord) -> float:
        start = (
            record.started_mono
            if record.started_mono is not None
            else record.submitted_mono
        )
        end = (
            record.completed_mono
            if record.completed_mono is not None
            else monotonic()
        )
        return max(0.0, float(end - start))

    def start(
        self,
        prompt: str,
        workdir: str | None = None,
        timeout: float = 300.0,
        output_format: str | None = None,
        dangerously_skip_permissions: bool = False,
        display_mode: str = "headless",
        task_key: str | None = None,
    ) -> str:
        if display_mode not in {"headless", "terminal"}:
            raise ValueError("display_mode must be 'headless' or 'terminal'")
        job_id = uuid4().hex
        with self._lock:
            if self._closed:
                raise RuntimeError("agy job registry is closed")
            self._prune_locked()
            if task_key is not None:
                for existing_id, existing_record in self._jobs.items():
                    if existing_record.task_key == task_key and not existing_record.future.done():
                        raise RuntimeError(
                            f"DUPLICATE_ACTIVE_TASK: task_key '{task_key}' is already active on job {existing_id}"
                        )
            runner = run_agy_visible if display_mode == "terminal" else run_agy

            def _wrapped_runner() -> AgyResult:
                self._mark_started(job_id)
                try:
                    return runner(
                        prompt,
                        workdir,
                        timeout,
                        output_format,
                        dangerously_skip_permissions,
                    )
                finally:
                    self._mark_completed(job_id)

            future = self._executor.submit(_wrapped_runner)
            record = _JobRecord(
                future=future,
                task_key=task_key,
                submitted_at=_utc_now_iso(),
                submitted_mono=monotonic(),
            )
            self._jobs[job_id] = record
        return job_id

    def status(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            self._prune_locked()
            record = self._jobs.get(job_id)
            if record is None:
                return {"job_id": job_id, "state": "unknown", "error": "job not found"}
            future = record.future

        elapsed = self._compute_elapsed_seconds(record)

        if not future.done():
            status: dict[str, Any] = {
                "job_id": job_id,
                "state": "running" if future.running() else "queued",
                "submitted_at": record.submitted_at,
                "started_at": record.started_at,
                "completed_at": None,
                "elapsed_seconds": elapsed,
            }
            if record.task_key is not None:
                status["task_key"] = record.task_key
            return status

        try:
            result = future.result()
        except Exception as exc:  # noqa: BLE001 - report worker failures to MCP.
            status = {
                "job_id": job_id,
                "state": "failed",
                "error": str(exc),
                "submitted_at": record.submitted_at,
                "started_at": record.started_at,
                "completed_at": record.completed_at,
                "elapsed_seconds": elapsed,
            }
            if record.task_key is not None:
                status["task_key"] = record.task_key
            return status

        state = "completed" if result.exit_code == 0 else "failed"
        status = {
            "job_id": job_id,
            "state": state,
            "text": result.text,
            "exit_code": result.exit_code,
            "used_pty": result.used_pty,
            "submitted_at": record.submitted_at,
            "started_at": record.started_at,
            "completed_at": record.completed_at,
            "elapsed_seconds": elapsed,
        }
        if state == "failed":
            status["error_kind"] = classify_agy_error(result.text, result.stderr)
            status["error"] = describe_agy_failure(result)
        if record.task_key is not None:
            status["task_key"] = record.task_key
        return status

    def wait(self, job_id: str, wait_seconds: float = 120.0) -> dict[str, Any]:
        if (
            isinstance(wait_seconds, bool)
            or not isinstance(wait_seconds, (int, float))
            or not math.isfinite(float(wait_seconds))
            or wait_seconds <= 0
        ):
            raise ValueError("wait_seconds must be a positive finite number")

        with self._lock:
            self._prune_locked()
            record = self._jobs.get(job_id)
            if record is None:
                return {"job_id": job_id, "state": "unknown", "error": "job not found"}
            future = record.future
            if future.done():
                return self.status(job_id)

        concurrent.futures.wait([future], timeout=float(wait_seconds))
        return self.status(job_id)

    def cleanup(self) -> int:
        """Remove completed jobs older than the configured retention period."""
        with self._lock:
            return self._prune_locked()

    def close(self, wait: bool = True, cancel_futures: bool = True) -> None:
        """Stop accepting jobs and release the worker pool."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=wait, cancel_futures=cancel_futures)

    def __enter__(self) -> "AgyJobRegistry":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


agy_jobs = AgyJobRegistry()
atexit.register(agy_jobs.close)
