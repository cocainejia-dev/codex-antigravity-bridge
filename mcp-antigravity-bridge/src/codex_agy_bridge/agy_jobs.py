"""Asynchronous job management for parallel worktree delegation with durable journal."""

from __future__ import annotations

import atexit
import concurrent.futures
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
import subprocess
import threading
from threading import RLock
from time import monotonic
from typing import Any
from uuid import uuid4

from .agy_runner import AgyResult, classify_agy_error, describe_agy_failure, run_agy, run_agy_visible
from .durable_jobs import (
    DEFAULT_TERMINAL_RETENTION_SECONDS,
    DurableJobStore,
    compute_prompt_hash,
    truncate_result_text,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _observe_worktree(workdir: str | None) -> str | None:
    """Return lightweight git status fingerprint if workdir is a valid git repository."""
    if not workdir:
        return None
    try:
        path = Path(workdir).expanduser()
        if not path.is_dir():
            return None
        extra_kwargs: dict[str, Any] = {}
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            extra_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        res = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(path),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
            **extra_kwargs,
        )
        if res.returncode == 0:
            return hashlib.sha256(res.stdout.encode("utf-8")).hexdigest()
    except Exception:
        return None
    return None


@dataclass
class _JobRecord:
    future: Future[AgyResult]
    task_key: str | None = None
    workdir: str | None = None
    prompt_hash: str = ""
    submitted_at: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    submitted_mono: float = 0.0
    started_mono: float | None = None
    completed_mono: float | None = None
    heartbeat_at: str | None = None
    heartbeat_mono: float | None = None
    last_worktree_activity_at: str | None = None
    last_worktree_activity_mono: float | None = None
    worktree_fingerprint: str | None = None
    persistence_degraded: bool = False
    persistence_error: str | None = None
    result: AgyResult | None = None
    exc: Exception | None = None


class AgyJobRegistry:
    """Run bounded agy calls with durable SQLite journal and health watchdog."""

    def __init__(
        self,
        max_workers: int = 4,
        retention_seconds: float = 900.0,
        db_path: str | Path | None = None,
        watchdog_interval: float = 20.0,
        stale_heartbeat_threshold: float = 60.0,
        idle_worktree_threshold: float = 60.0,
        stall_grace_seconds: float = 60.0,
        store_prune_interval: float = 300.0,
        store_retention_seconds: float = DEFAULT_TERMINAL_RETENTION_SECONDS,
    ) -> None:
        if retention_seconds < 0:
            raise ValueError("retention_seconds must be non-negative")
        if watchdog_interval <= 0:
            raise ValueError("watchdog_interval must be positive")
        if stall_grace_seconds <= 0:
            raise ValueError("stall_grace_seconds must be positive")
        if store_prune_interval < 0:
            raise ValueError("store_prune_interval must be non-negative")
        if store_retention_seconds < 0:
            raise ValueError("store_retention_seconds must be non-negative")

        self.bridge_session_id = uuid4().hex
        self._retention_seconds = retention_seconds
        self._watchdog_interval = watchdog_interval
        self._stale_heartbeat_threshold = stale_heartbeat_threshold
        self._idle_worktree_threshold = idle_worktree_threshold
        self._stall_grace_seconds = stall_grace_seconds
        self._store_prune_interval = store_prune_interval
        self._store_retention_seconds = store_retention_seconds
        self._last_store_prune_mono = monotonic()

        self._store = DurableJobStore(db_path=db_path)
        self._store.reconcile_other_sessions(self.bridge_session_id, _utc_now_iso())
        try:
            self._store.prune_terminal(older_than_seconds=self._store_retention_seconds)
        except Exception:
            pass

        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="codex-agy",
        )
        self._jobs: dict[str, _JobRecord] = {}
        self._lock = RLock()
        self._closed = False

        self._watchdog_stop_event = threading.Event()
        self._watchdog_wake_event = threading.Event()
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            name="codex-agy-watchdog",
            daemon=True,
        )
        self._watchdog_thread.start()

    def _watchdog_loop(self) -> None:
        """Daemon watchdog thread updating heartbeats and health for live active jobs."""
        next_tick = monotonic() + self._watchdog_interval
        while not self._watchdog_stop_event.is_set():
            sleep_duration = max(0.0, next_tick - monotonic())
            self._watchdog_wake_event.wait(timeout=sleep_duration)
            self._watchdog_wake_event.clear()
            if self._watchdog_stop_event.is_set():
                break
            next_tick = monotonic() + self._watchdog_interval
            with self._lock:
                if self._closed:
                    break
                active_jobs = [
                    (job_id, record.workdir)
                    for job_id, record in self._jobs.items()
                    if record.completed_at is None and not record.future.done()
                ]

            now_iso = _utc_now_iso()
            now_mono = monotonic()

            for job_id, workdir in active_jobs:
                with self._lock:
                    if self._closed:
                        break
                    record = self._jobs.get(job_id)
                    if record is None or record.completed_at is not None or record.future.done():
                        continue
                    record.heartbeat_at = now_iso
                    record.heartbeat_mono = now_mono

                fp = _observe_worktree(workdir) if workdir else None
                obs_iso = _utc_now_iso()
                obs_mono = monotonic()

                with self._lock:
                    if self._closed:
                        break
                    record = self._jobs.get(job_id)
                    if record is None or record.completed_at is not None or record.future.done():
                        continue
                    if fp is not None:
                        if record.worktree_fingerprint is None:
                            record.worktree_fingerprint = fp
                            record.last_worktree_activity_at = obs_iso
                            record.last_worktree_activity_mono = obs_mono
                        elif record.worktree_fingerprint != fp:
                            record.worktree_fingerprint = fp
                            record.last_worktree_activity_at = obs_iso
                            record.last_worktree_activity_mono = obs_mono

                    health = self._compute_live_health(record)
                    elapsed = self._compute_elapsed_seconds(record)
                    last_activity = record.last_worktree_activity_at

                try:
                    self._store.update_heartbeat(
                        job_id=job_id,
                        heartbeat_at=now_iso,
                        health=health,
                        elapsed_seconds=elapsed,
                        last_worktree_activity_at=last_activity,
                        now_iso=now_iso,
                    )
                    with self._lock:
                        rec = self._jobs.get(job_id)
                        if rec is not None and rec.persistence_degraded:
                            rec.persistence_degraded = False
                            rec.persistence_error = None
                except Exception as err:
                    with self._lock:
                        rec = self._jobs.get(job_id)
                        if rec is not None:
                            rec.persistence_degraded = True
                            rec.persistence_error = f"DURABLE_STORE_ERROR: {err}"

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

    def _maybe_prune_store_terminal(self) -> int:
        """Throttled maintenance to prune expired terminal jobs in durable store."""
        now = monotonic()
        with self._lock:
            if now - self._last_store_prune_mono < self._store_prune_interval:
                return 0
            self._last_store_prune_mono = now
        try:
            return self._store.prune_terminal(older_than_seconds=self._store_retention_seconds)
        except Exception:
            return 0

    def _mark_started(self, job_id: str) -> None:
        now_iso = _utc_now_iso()
        now_mono = monotonic()
        with self._lock:
            record = self._jobs.get(job_id)
            if record is not None and record.started_at is None:
                record.started_at = now_iso
                record.started_mono = now_mono
                record.heartbeat_at = now_iso
                record.heartbeat_mono = now_mono

        try:
            self._store.mark_started(job_id, now_iso, now_iso)
        except Exception as err:
            with self._lock:
                rec = self._jobs.get(job_id)
                if rec is not None:
                    rec.persistence_degraded = True
                    rec.persistence_error = f"DURABLE_STORE_ERROR: {err}"

    def _mark_completed(
        self,
        job_id: str,
        result: AgyResult | None = None,
        exc: Exception | None = None,
    ) -> None:
        now_iso = _utc_now_iso()
        now_mono = monotonic()
        with self._lock:
            record = self._jobs.get(job_id)
            if record is not None:
                if record.completed_at is not None:
                    return
                if record.started_at is None:
                    record.started_at = record.submitted_at
                    record.started_mono = record.submitted_mono
                record.completed_at = now_iso
                record.completed_mono = now_mono
                record.result = result
                record.exc = exc
                elapsed = self._compute_elapsed_seconds(record)
                started_at = record.started_at
            else:
                elapsed = 0.0
                started_at = now_iso

        if exc is not None:
            state = "failed"
            health = "FAILED"
            exit_code = 1
            error = str(exc)
            text = None
            used_pty = False
            result_truncated = False
        elif result is not None:
            state = "completed" if result.exit_code == 0 else "failed"
            health = "COMPLETED" if result.exit_code == 0 else "FAILED"
            exit_code = result.exit_code
            text = result.text
            used_pty = result.used_pty
            result_truncated = False
            error = describe_agy_failure(result) if state == "failed" else None
        else:
            state = "failed"
            health = "FAILED"
            exit_code = 1
            error = "unknown execution termination"
            text = None
            used_pty = False
            result_truncated = False

        try:
            self._store.mark_terminal(
                job_id=job_id,
                state=state,
                health=health,
                exit_code=exit_code,
                error=error,
                result_text=text,
                result_truncated=result_truncated,
                used_pty=used_pty,
                started_at=started_at,
                completed_at=now_iso,
                elapsed_seconds=elapsed,
                now_iso=now_iso,
            )
        except Exception as err:
            with self._lock:
                rec = self._jobs.get(job_id)
                if rec is not None:
                    rec.persistence_degraded = True
                    rec.persistence_error = f"DURABLE_STORE_ERROR: {err}"

        self._maybe_prune_store_terminal()

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

    def _compute_live_health(self, record: _JobRecord) -> str:
        if not record.future.running():
            return "QUEUED"
        now_mono = monotonic()
        hb_mono = record.heartbeat_mono or record.started_mono or record.submitted_mono
        hb_age = max(0.0, now_mono - hb_mono)
        if hb_age > self._stale_heartbeat_threshold:
            return "POSSIBLY_STALLED"
        if (
            record.last_worktree_activity_mono is not None
            and (now_mono - record.last_worktree_activity_mono) > self._idle_worktree_threshold
        ):
            return "IDLE"
        return "HEALTHY"

    def _liveness_probe(self, job_id: str) -> bool:
        """Return whether supervisory evidence still proves useful worker progress."""
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None or record.completed_at is not None:
                return False
            now = monotonic()
            heartbeat = record.heartbeat_mono or record.started_mono or record.submitted_mono
            heartbeat_fresh = now - heartbeat <= self._stale_heartbeat_threshold
            activity_fresh = (
                record.last_worktree_activity_mono is not None
                and now - record.last_worktree_activity_mono <= self._idle_worktree_threshold
            )
            return heartbeat_fresh or activity_fresh

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
        now_iso = _utc_now_iso()
        now_mono = monotonic()
        prompt_hash = compute_prompt_hash(prompt)

        with self._lock:
            if self._closed:
                raise RuntimeError("agy job registry is closed")
            self._prune_locked()

        # Atomically reserve and record in durable journal
        self._store.reserve_and_create(
            job_id=job_id,
            task_key=task_key,
            workdir=workdir,
            prompt_hash=prompt_hash,
            owner_session_id=self.bridge_session_id,
            now_iso=now_iso,
        )

        with self._lock:
            closed_early = self._closed

        if closed_early:
            try:
                self._store.mark_terminal(
                    job_id=job_id,
                    state="failed",
                    health="FAILED",
                    exit_code=1,
                    error="REGISTRY_CLOSED: agy job registry is closed",
                    result_text=None,
                    result_truncated=False,
                    used_pty=False,
                    started_at=now_iso,
                    completed_at=now_iso,
                    elapsed_seconds=0.0,
                    now_iso=now_iso,
                )
            except Exception:
                pass
            raise RuntimeError("agy job registry is closed")

        runner = run_agy_visible if display_mode == "terminal" else run_agy
        start_gate = threading.Event()

        def _wrapped_runner() -> AgyResult:
            start_gate.wait()
            res: AgyResult | None = None
            exc: Exception | None = None
            try:
                with self._lock:
                    if self._closed or job_id not in self._jobs:
                        raise RuntimeError("REGISTRY_CLOSED: agy job registry is closed")
                self._mark_started(job_id)
                runner_args = (
                    prompt,
                    workdir,
                    timeout,
                    output_format,
                    dangerously_skip_permissions,
                )
                if display_mode == "terminal":
                    res = runner(*runner_args)
                else:
                    res = runner(
                        *runner_args,
                        liveness_probe=lambda: self._liveness_probe(job_id),
                        stall_grace_seconds=self._stall_grace_seconds,
                    )
                return res
            except Exception as err:
                exc = err
                raise
            finally:
                self._mark_completed(job_id, result=res, exc=exc)

        try:
            future = self._executor.submit(_wrapped_runner)
        except Exception as submit_err:
            start_gate.set()
            try:
                self._store.mark_terminal(
                    job_id=job_id,
                    state="failed",
                    health="FAILED",
                    exit_code=1,
                    error=f"SUBMIT_FAILED: {submit_err}",
                    result_text=None,
                    result_truncated=False,
                    used_pty=False,
                    started_at=now_iso,
                    completed_at=now_iso,
                    elapsed_seconds=0.0,
                    now_iso=now_iso,
                )
            except Exception:
                pass
            raise

        closed = False
        try:
            with self._lock:
                if self._closed:
                    closed = True
                    try:
                        future.cancel()
                    except Exception:
                        pass
                else:
                    record = _JobRecord(
                        future=future,
                        task_key=task_key,
                        workdir=workdir,
                        prompt_hash=prompt_hash,
                        submitted_at=now_iso,
                        submitted_mono=now_mono,
                        heartbeat_at=now_iso,
                        heartbeat_mono=now_mono,
                    )
                    self._jobs[job_id] = record
        finally:
            start_gate.set()

        with self._lock:
            if self._closed:
                closed = True

        if closed:
            self._mark_completed(
                job_id,
                exc=RuntimeError("REGISTRY_CLOSED: agy job registry is closed"),
            )
            raise RuntimeError("agy job registry is closed")

        self._watchdog_wake_event.set()
        return job_id

    def status(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            self._prune_locked()
            record = self._jobs.get(job_id)

        # 1. Live memory status
        if record is not None:
            future = record.future
            elapsed = self._compute_elapsed_seconds(record)

            if record.completed_at is None and not future.done():
                now_mono = monotonic()
                hb_mono = record.heartbeat_mono or record.started_mono or record.submitted_mono
                hb_age = max(0.0, now_mono - hb_mono)
                health = self._compute_live_health(record)
                status: dict[str, Any] = {
                    "job_id": job_id,
                    "state": "running" if (record.started_at is not None or future.running()) else "queued",
                    "health": health,
                    "submitted_at": record.submitted_at,
                    "started_at": record.started_at,
                    "completed_at": None,
                    "elapsed_seconds": elapsed,
                    "heartbeat_at": record.heartbeat_at or record.submitted_at,
                    "heartbeat_age_seconds": round(hb_age, 3),
                    "last_worktree_activity_at": record.last_worktree_activity_at,
                }
                if record.task_key is not None:
                    status["task_key"] = record.task_key
                if record.workdir is not None:
                    status["workdir"] = record.workdir
                if record.persistence_degraded:
                    status["supervision_persistence"] = "degraded"
                    status["supervision_persistence_error"] = record.persistence_error
                return status

            if record.completed_at is None:
                with self._lock:
                    if record.completed_at is None:
                        record.completed_at = _utc_now_iso()
                        record.completed_mono = monotonic()
                elapsed = self._compute_elapsed_seconds(record)

            exc = record.exc
            result = record.result

            if exc is None and result is None and future.done():
                try:
                    result = future.result()
                    record.result = result
                except Exception as ex:
                    exc = ex
                    record.exc = ex

            if exc is not None:
                status = {
                    "job_id": job_id,
                    "state": "failed",
                    "health": "FAILED",
                    "error": str(exc),
                    "submitted_at": record.submitted_at,
                    "started_at": record.started_at,
                    "completed_at": record.completed_at,
                    "elapsed_seconds": elapsed,
                    "heartbeat_at": record.heartbeat_at or record.completed_at,
                    "last_worktree_activity_at": record.last_worktree_activity_at,
                }
                if record.task_key is not None:
                    status["task_key"] = record.task_key
                if record.workdir is not None:
                    status["workdir"] = record.workdir
                if record.persistence_degraded:
                    status["supervision_persistence"] = "degraded"
                    status["supervision_persistence_error"] = record.persistence_error
                return status

            if result is not None:
                state = "completed" if result.exit_code == 0 else "failed"
                status = {
                    "job_id": job_id,
                    "state": state,
                    "health": "COMPLETED" if state == "completed" else "FAILED",
                    "text": result.text,
                    "exit_code": result.exit_code,
                    "used_pty": result.used_pty,
                    "submitted_at": record.submitted_at,
                    "started_at": record.started_at,
                    "completed_at": record.completed_at,
                    "elapsed_seconds": elapsed,
                    "heartbeat_at": record.heartbeat_at or record.completed_at,
                    "last_worktree_activity_at": record.last_worktree_activity_at,
                }
                if state == "failed":
                    status["error_kind"] = classify_agy_error(result.text, result.stderr)
                    status["error"] = describe_agy_failure(result)
                if record.task_key is not None:
                    status["task_key"] = record.task_key
                if record.workdir is not None:
                    status["workdir"] = record.workdir
                if record.persistence_degraded:
                    status["supervision_persistence"] = "degraded"
                    status["supervision_persistence_error"] = record.persistence_error
                return status

            status = {
                "job_id": job_id,
                "state": "failed",
                "health": "FAILED",
                "error": "unknown execution termination",
                "submitted_at": record.submitted_at,
                "started_at": record.started_at,
                "completed_at": record.completed_at,
                "elapsed_seconds": elapsed,
                "heartbeat_at": record.heartbeat_at or record.completed_at,
                "last_worktree_activity_at": record.last_worktree_activity_at,
            }
            if record.task_key is not None:
                status["task_key"] = record.task_key
            if record.workdir is not None:
                status["workdir"] = record.workdir
            if record.persistence_degraded:
                status["supervision_persistence"] = "degraded"
                status["supervision_persistence_error"] = record.persistence_error
            return status

        # 2. Durable journal fallback
        try:
            durable = self._store.get_job(job_id)
        except Exception as err:
            return {
                "job_id": job_id,
                "state": "unknown",
                "health": "UNKNOWN",
                "supervision_persistence": "degraded",
                "supervision_persistence_error": f"DURABLE_STORE_ERROR: {err}",
                "error": f"DURABLE_STORE_ERROR: {err}",
            }

        if durable is not None:
            state = durable["state"]
            health = durable["health"]
            rec_state = durable["recovery_state"]

            if state not in ("completed", "failed") and (
                rec_state == "interrupted" or (health == "INTERRUPTED" and state == "unknown")
            ):
                status = {
                    "job_id": durable["job_id"],
                    "state": "unknown",
                    "health": "INTERRUPTED",
                    "recovery_state": "interrupted",
                    "error": "INTERRUPTED: job interrupted across session boundary",
                    "submitted_at": durable["submitted_at"],
                    "started_at": durable["started_at"],
                    "completed_at": durable["completed_at"],
                    "elapsed_seconds": durable["elapsed_seconds"],
                    "heartbeat_at": durable["heartbeat_at"],
                    "last_worktree_activity_at": durable["last_worktree_activity_at"],
                }
            elif state == "completed":
                status = {
                    "job_id": durable["job_id"],
                    "state": "completed",
                    "health": "COMPLETED",
                    "text": durable["result_text"] or "",
                    "result_truncated": bool(durable["result_truncated"]),
                    "exit_code": durable["exit_code"] or 0,
                    "used_pty": bool(durable["used_pty"]),
                    "submitted_at": durable["submitted_at"],
                    "started_at": durable["started_at"],
                    "completed_at": durable["completed_at"],
                    "elapsed_seconds": durable["elapsed_seconds"],
                    "heartbeat_at": durable["heartbeat_at"],
                    "last_worktree_activity_at": durable["last_worktree_activity_at"],
                }
            elif state == "failed":
                status = {
                    "job_id": durable["job_id"],
                    "state": "failed",
                    "health": "FAILED",
                    "text": durable["result_text"] or "",
                    "result_truncated": bool(durable["result_truncated"]),
                    "exit_code": durable["exit_code"] if durable["exit_code"] is not None else 1,
                    "error": durable["error"] or "unknown error",
                    "submitted_at": durable["submitted_at"],
                    "started_at": durable["started_at"],
                    "completed_at": durable["completed_at"],
                    "elapsed_seconds": durable["elapsed_seconds"],
                    "heartbeat_at": durable["heartbeat_at"],
                    "last_worktree_activity_at": durable["last_worktree_activity_at"],
                }
            else:
                status = {
                    "job_id": durable["job_id"],
                    "state": state,
                    "health": health,
                    "submitted_at": durable["submitted_at"],
                    "started_at": durable["started_at"],
                    "completed_at": durable["completed_at"],
                    "elapsed_seconds": durable["elapsed_seconds"],
                    "heartbeat_at": durable["heartbeat_at"],
                    "last_worktree_activity_at": durable["last_worktree_activity_at"],
                }

            if durable.get("task_key"):
                status["task_key"] = durable["task_key"]
            if durable.get("workdir"):
                status["workdir"] = durable["workdir"]
            return status

        # 3. Missing/unknown
        return {
            "job_id": job_id,
            "state": "unknown",
            "health": "UNKNOWN",
            "error": "job not found",
        }

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
            future = record.future if record is not None else None
            completed = (record is not None and record.completed_at is not None)

        if completed or future is None or future.done():
            return self.status(job_id)

        concurrent.futures.wait([future], timeout=float(wait_seconds))
        return self.status(job_id)

    def recent(
        self,
        limit: int = 20,
        task_key: str = "",
        state: str = "",
    ) -> list[dict[str, Any]]:
        """Return newest-first summary of recent jobs from durable store."""
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit < 1
            or limit > 100
        ):
            raise ValueError("limit must be an integer between 1 and 100")
        key_filter = task_key.strip() or None
        state_filter = state.strip().lower() or None
        return self._store.get_recent(limit=limit, task_key=key_filter, state=state_filter)

    def cleanup(self) -> int:
        """Remove completed jobs older than the configured retention period."""
        with self._lock:
            mem_count = self._prune_locked()
        try:
            store_count = self._store.prune_terminal(older_than_seconds=self._store_retention_seconds)
        except Exception:
            store_count = 0
        return mem_count + store_count

    def close(self, wait: bool = True, cancel_futures: bool = True) -> None:
        """Stop accepting jobs, stop watchdog, and release the worker pool."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            uncompleted_jobs = [
                (job_id, record)
                for job_id, record in self._jobs.items()
                if record.completed_at is None
            ]
        self._watchdog_stop_event.set()
        self._watchdog_wake_event.set()
        self._watchdog_thread.join(timeout=1.0)
        self._executor.shutdown(wait=wait, cancel_futures=cancel_futures)

        for job_id, record in uncompleted_jobs:
            with self._lock:
                if record.completed_at is not None:
                    continue
            self._mark_completed(
                job_id,
                exc=RuntimeError("REGISTRY_CLOSED: job cancelled during registry shutdown"),
            )

    def __enter__(self) -> "AgyJobRegistry":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


agy_jobs = AgyJobRegistry()
atexit.register(agy_jobs.close)
