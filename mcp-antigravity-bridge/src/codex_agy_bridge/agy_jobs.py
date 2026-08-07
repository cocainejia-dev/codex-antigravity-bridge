"""Asynchronous job management for parallel worktree delegation."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any
from uuid import uuid4

from .agy_runner import AgyResult, run_agy


class AgyJobRegistry:
    """Run bounded agy calls in the background and expose pollable status."""

    def __init__(self, max_workers: int = 4) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="codex-agy",
        )
        self._jobs: dict[str, Future[AgyResult]] = {}

    def start(
        self,
        prompt: str,
        workdir: str | None = None,
        timeout: float = 300.0,
        output_format: str | None = None,
        dangerously_skip_permissions: bool = False,
    ) -> str:
        job_id = uuid4().hex
        future = self._executor.submit(
            run_agy,
            prompt,
            workdir,
            timeout,
            output_format,
            dangerously_skip_permissions,
        )
        self._jobs[job_id] = future
        return job_id

    def status(self, job_id: str) -> dict[str, Any]:
        future = self._jobs.get(job_id)
        if future is None:
            return {"job_id": job_id, "state": "unknown", "error": "job not found"}

        if not future.done():
            return {
                "job_id": job_id,
                "state": "running" if future.running() else "queued",
            }

        try:
            result = future.result()
        except Exception as exc:  # noqa: BLE001 - report worker failures to MCP.
            return {"job_id": job_id, "state": "failed", "error": str(exc)}

        state = "completed" if result.exit_code == 0 else "failed"
        return {
            "job_id": job_id,
            "state": state,
            "text": result.text,
            "exit_code": result.exit_code,
            "used_pty": result.used_pty,
        }


agy_jobs = AgyJobRegistry()
