"""Comprehensive deterministic tests for SUPERVISOR V3 Option C durable jobs and watchdog."""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
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
from codex_agy_bridge.durable_jobs import (
    DurableJobStore,
    SCHEMA_VERSION,
    compute_prompt_hash,
    get_default_db_path,
    truncate_result_text,
)


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


def _wait_until(predicate, *, timeout: float = 1.0, interval: float = 0.01) -> None:
    """Wait for an asynchronous test condition without unbounded polling."""
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError("condition was not satisfied before timeout")
        time.sleep(min(interval, remaining))


def test_default_db_path_outside_repository():
    path = get_default_db_path()
    assert "codex-agy-bridge" in str(path)
    assert path.name == "jobs.sqlite3"


def test_schema_unsupported_version_rejected():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "future_jobs.sqlite3"
        conn = sqlite3.connect(str(db_file))
        conn.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);")
        conn.execute("INSERT INTO schema_meta VALUES ('schema_version', '999');")
        conn.commit()
        conn.close()

        with pytest.raises(RuntimeError, match="DURABLE_SCHEMA_UNSUPPORTED"):
            DurableJobStore(db_path=db_file)


def test_secret_nonleakage_and_prompt_hash():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "test_jobs.sqlite3"
        secret_prompt = "SuperSecretAPIKey=sk-123456789 and Bearer token=xyz"
        expected_hash = compute_prompt_hash(secret_prompt)

        store = DurableJobStore(db_path=db_file)
        store.reserve_and_create(
            job_id="job-sec-1",
            task_key="sec-key",
            workdir="C:\\tmp",
            prompt_hash=expected_hash,
            owner_session_id="sess-1",
            now_iso="2026-08-16T00:00:00Z",
        )

        job = store.get_job("job-sec-1")
        assert job is not None
        assert job["prompt_hash"] == expected_hash

        # Verify DB directly: full prompt text and raw keys must NEVER be stored
        conn = sqlite3.connect(str(db_file))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM durable_jobs WHERE job_id='job-sec-1'")
        row = dict(cur.fetchone())
        conn.close()

        for col_val in row.values():
            assert "sk-123456789" not in str(col_val)
            assert "Bearer token" not in str(col_val)


def test_result_truncation_caps_at_512_kib():
    text_small = "A" * 1000
    res_text, truncated = truncate_result_text(text_small)
    assert res_text == text_small
    assert truncated is False

    text_huge = "B" * (600 * 1024)
    res_text2, truncated2 = truncate_result_text(text_huge)
    assert truncated2 is True
    assert len(res_text2.encode("utf-8")) == 512 * 1024


def test_old_session_reconciliation_marks_interrupted():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "test_jobs.sqlite3"

        # Session 1 runs a job and crashes/exits
        store1 = DurableJobStore(db_path=db_file)
        store1.reserve_and_create(
            job_id="job-crash-1",
            task_key="task-crash",
            workdir="C:\\tmp",
            prompt_hash=compute_prompt_hash("prompt 1"),
            owner_session_id="session-old",
            now_iso="2026-08-16T00:00:00Z",
        )
        store1.mark_started("job-crash-1", "2026-08-16T00:00:01Z", "2026-08-16T00:00:01Z")

        # Session 2 starts fresh
        registry2 = AgyJobRegistry(db_path=db_file)
        try:
            status = registry2.status("job-crash-1")
            assert status["job_id"] == "job-crash-1"
            assert status["state"] == "unknown"
            assert status["health"] == "INTERRUPTED"
            assert status["recovery_state"] == "interrupted"
            assert "interrupted across session boundary" in status["error"]

            # Wait on old session interrupted job returns immediately without inventing a Future
            wait_res = registry2.wait("job-crash-1", wait_seconds=5.0)
            assert wait_res["state"] == "unknown"
            assert wait_res["health"] == "INTERRUPTED"

            # Attempting to start the same task_key raises RECOVERY_REQUIRED
            with pytest.raises(RuntimeError, match="RECOVERY_REQUIRED"):
                registry2.start("Restart crash task", task_key="task-crash")
        finally:
            registry2.close()


def test_durable_terminal_history_and_memory_pruning(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    mp.setattr(
        "codex_agy_bridge.agy_jobs.run_agy",
        lambda *args, **kwargs: AgyResult(text="TERMINAL_RESULT", exit_code=0, used_pty=False),
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "test_jobs.sqlite3"
        registry = AgyJobRegistry(retention_seconds=0.05, db_path=db_file)
        try:
            job_id = registry.start("Completed task", task_key="t-done")
            res = registry.wait(job_id, wait_seconds=2.0)
            assert res["state"] == "completed"
            assert res["health"] == "COMPLETED"
            assert res["text"] == "TERMINAL_RESULT"

            time.sleep(0.1)
            # Prune memory
            assert registry.cleanup() == 1

            # Fallback to durable SQLite journal
            status = registry.status(job_id)
            assert status["state"] == "completed"
            assert status["health"] == "COMPLETED"
            assert status["text"] == "TERMINAL_RESULT"

            # Wait on pruned terminal job returns immediately
            wait_status = registry.wait(job_id, wait_seconds=5.0)
            assert wait_status["state"] == "completed"
            assert wait_status["text"] == "TERMINAL_RESULT"
        finally:
            registry.close()
            if monkeypatch is None:
                mp.undo()


def test_watchdog_heartbeat_progression_and_stalled_health(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    unblock = threading.Event()

    def hanging_run(*args, **kwargs):
        unblock.wait(timeout=5.0)
        return AgyResult(text="DONE", exit_code=0, used_pty=False)

    mp.setattr("codex_agy_bridge.agy_jobs.run_agy", hanging_run)
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "test_jobs.sqlite3"
        # Watchdog interval 0.05s, stale threshold 0.1s
        registry = AgyJobRegistry(
            db_path=db_file,
            watchdog_interval=0.05,
            stale_heartbeat_threshold=0.1,
        )
        try:
            job_id = registry.start("Hanging job")
            time.sleep(0.08)

            status1 = registry.status(job_id)
            assert status1["state"] == "running"
            assert status1["health"] in {"HEALTHY", "QUEUED"}
            assert status1["heartbeat_age_seconds"] >= 0.0
            hb1 = status1["heartbeat_at"]
            assert hb1 is not None

            # Temporarily pause watchdog to simulate stalled heartbeat
            with registry._lock:
                rec = registry._jobs[job_id]
                rec.heartbeat_mono = time.monotonic() - 0.5  # Simulate stale heartbeat

            status_stalled = registry.status(job_id)
            assert status_stalled["state"] == "running"
            assert status_stalled["health"] == "POSSIBLY_STALLED"
            # POSSIBLY_STALLED must NOT cancel Future or change state to failed
            assert not rec.future.done()

            # Release hanging run
            unblock.set()
            final_status = registry.wait(job_id, wait_seconds=2.0)
            assert final_status["state"] == "completed"
            assert final_status["health"] == "COMPLETED"
        finally:
            unblock.set()
            registry.close()
            if monkeypatch is None:
                mp.undo()


def test_watchdog_worktree_activity_and_idle_health(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    unblock = threading.Event()

    def hanging_run(*args, **kwargs):
        unblock.wait(timeout=5.0)
        return AgyResult(text="DONE", exit_code=0, used_pty=False)

    mp.setattr("codex_agy_bridge.agy_jobs.run_agy", hanging_run)
    with tempfile.TemporaryDirectory() as git_dir, tempfile.TemporaryDirectory() as tmp_dir:
        # Initialize temp git repo and commit initial file
        subprocess.run(["git", "init"], cwd=git_dir, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=git_dir, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=git_dir, capture_output=True, check=True)
        test_file = Path(git_dir) / "file.txt"
        test_file.write_text("initial", encoding="utf-8")
        subprocess.run(["git", "add", "file.txt"], cwd=git_dir, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "initial commit"], cwd=git_dir, capture_output=True, check=True)

        db_file = Path(tmp_dir) / "test_jobs.sqlite3"
        registry = AgyJobRegistry(
            db_path=db_file,
            watchdog_interval=0.05,
            idle_worktree_threshold=0.08,
            stale_heartbeat_threshold=5.0,
        )
        try:
            job_id = registry.start("Worktree task", workdir=git_dir)
            _wait_until(
                lambda: registry.status(job_id)["last_worktree_activity_at"] is not None,
            )

            status = registry.status(job_id)
            assert status["state"] == "running"
            assert status["last_worktree_activity_at"] is not None

            # After idle threshold without changes, health becomes IDLE
            _wait_until(lambda: registry.status(job_id)["health"] == "IDLE")
            idle_status = registry.status(job_id)
            assert idle_status["health"] == "IDLE"

            # Modify worktree file
            test_file.write_text("modified content", encoding="utf-8")

            # Watchdog picks up change and returns health to HEALTHY
            _wait_until(lambda: registry.status(job_id)["health"] == "HEALTHY")
            active_status = registry.status(job_id)
            assert active_status["health"] == "HEALTHY"

            unblock.set()
            registry.wait(job_id, wait_seconds=2.0)
        finally:
            unblock.set()
            registry.close()
            if monkeypatch is None:
                mp.undo()


def test_concurrent_same_key_duplicate_allows_only_one(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    blocker = threading.Event()

    def slow_run(*args, **kwargs):
        blocker.wait(timeout=5.0)
        return AgyResult(text="DONE", exit_code=0, used_pty=False)

    mp.setattr("codex_agy_bridge.agy_jobs.run_agy", slow_run)
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "test_jobs.sqlite3"
        registry = AgyJobRegistry(db_path=db_file)

        results = []
        errors = []

        def worker():
            try:
                jid = registry.start("Concurrent task", task_key="concurrent-key")
                results.append(jid)
            except Exception as e:
                errors.append(e)

        try:
            t1 = threading.Thread(target=worker)
            t2 = threading.Thread(target=worker)
            t1.start()
            t2.start()
            t1.join()
            t2.join()

            # Exactly one worker should succeed, and one should get DUPLICATE_ACTIVE_TASK
            assert len(results) == 1
            assert len(errors) == 1
            assert "DUPLICATE_ACTIVE_TASK" in str(errors[0])
        finally:
            blocker.set()
            registry.close()
            if monkeypatch is None:
                mp.undo()


def test_agy_jobs_recent_query_filtering_and_limits(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    mp.setattr(
        "codex_agy_bridge.agy_jobs.run_agy",
        lambda *args, **kwargs: AgyResult(text="OK", exit_code=0, used_pty=False),
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "test_jobs.sqlite3"
        registry = AgyJobRegistry(db_path=db_file)
        try:
            j1 = registry.start("Task 1", task_key="key-alpha")
            registry.wait(j1, wait_seconds=2.0)

            j2 = registry.start("Task 2", task_key="key-beta")
            registry.wait(j2, wait_seconds=2.0)

            # All recent (newest first)
            recent_all = registry.recent(limit=20)
            assert len(recent_all) == 2
            assert recent_all[0]["job_id"] == j2
            assert recent_all[1]["job_id"] == j1

            # Filter by task_key
            recent_alpha = registry.recent(limit=10, task_key="key-alpha")
            assert len(recent_alpha) == 1
            assert recent_alpha[0]["job_id"] == j1

            # Filter by state
            recent_completed = registry.recent(limit=10, state="completed")
            assert len(recent_completed) == 2

            recent_failed = registry.recent(limit=10, state="failed")
            assert len(recent_failed) == 0

            # Verify no secret / raw prompt in recent summary
            for item in recent_all:
                assert "prompt" not in item
                assert "result_text" not in item
                assert "text" not in item
                assert item["prompt_hash"]
        finally:
            registry.close()
            if monkeypatch is None:
                mp.undo()


def test_cross_registry_atomic_same_key_duplicate_and_restart(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    unblock = threading.Event()

    def slow_run(*args, **kwargs):
        unblock.wait(timeout=5.0)
        return AgyResult(text="DONE_CROSS", exit_code=0, used_pty=False)

    mp.setattr("codex_agy_bridge.agy_jobs.run_agy", slow_run)
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "shared_jobs.sqlite3"
        reg1 = AgyJobRegistry(db_path=db_file)
        reg2 = AgyJobRegistry(db_path=db_file)

        try:
            j1 = reg1.start("Shared Task 1", task_key="cross-key")
            time.sleep(0.05)

            # Concurrent start in registry 2 with same active task_key MUST fail with DUPLICATE_ACTIVE_TASK
            with pytest.raises(RuntimeError, match="DUPLICATE_ACTIVE_TASK"):
                reg2.start("Shared Task 2", task_key="cross-key")

            # Concurrent reserve_and_create across threads in distinct store instances
            store3 = DurableJobStore(db_path=db_file)
            with pytest.raises(RuntimeError, match="DUPLICATE_ACTIVE_TASK"):
                store3.reserve_and_create(
                    job_id="manual-j3",
                    task_key="cross-key",
                    workdir=None,
                    prompt_hash="dummyhash",
                    owner_session_id="session-3",
                    now_iso="2026-08-16T00:00:00Z",
                )

            # Release and complete j1
            unblock.set()
            res1 = reg1.wait(j1, wait_seconds=2.0)
            assert res1["state"] == "completed"
            assert res1["health"] == "COMPLETED"

            # Once j1 is terminal, restarting the same key is permitted
            j2 = reg2.start("Restart shared task after completion", task_key="cross-key")
            res2 = reg2.wait(j2, wait_seconds=2.0)
            assert res2["state"] == "completed"
            assert res2["job_id"] == j2
            assert res2["job_id"] != j1
        finally:
            unblock.set()
            reg1.close()
            reg2.close()
            if monkeypatch is None:
                mp.undo()


def test_durable_terminal_pruning_policy_and_retention():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "prune_jobs.sqlite3"
        store = DurableJobStore(db_path=db_file)

        conn = sqlite3.connect(str(db_file), isolation_level=None)
        # 1. Expired completed job (45 days old) -> MUST be pruned
        conn.execute(
            """
            INSERT INTO durable_jobs (
                job_id, task_key, state, health, recovery_state, workdir,
                submitted_at, started_at, completed_at, elapsed_seconds,
                owner_session_id, heartbeat_at, last_worktree_activity_at,
                exit_code, error, result_text, result_truncated, used_pty,
                prompt_hash, created_at, updated_at
            ) VALUES (
                'job-old-completed', 'key-1', 'completed', 'COMPLETED', NULL, NULL,
                '2026-06-01T00:00:00+00:00', '2026-06-01T00:00:01+00:00', '2026-06-01T00:01:00+00:00', 59.0,
                'sess-old', '2026-06-01T00:01:00+00:00', NULL,
                0, NULL, 'ok', 0, 0,
                'hash1', '2026-06-01T00:00:00+00:00', '2026-06-01T00:01:00+00:00'
            );
            """
        )
        # 2. Expired failed job (40 days old) -> MUST be pruned
        conn.execute(
            """
            INSERT INTO durable_jobs (
                job_id, task_key, state, health, recovery_state, workdir,
                submitted_at, started_at, completed_at, elapsed_seconds,
                owner_session_id, heartbeat_at, last_worktree_activity_at,
                exit_code, error, result_text, result_truncated, used_pty,
                prompt_hash, created_at, updated_at
            ) VALUES (
                'job-old-failed', 'key-2', 'failed', 'FAILED', NULL, NULL,
                '2026-06-10T00:00:00+00:00', '2026-06-10T00:00:01+00:00', '2026-06-10T00:01:00+00:00', 59.0,
                'sess-old', '2026-06-10T00:01:00+00:00', NULL,
                1, 'some error', 'fail', 0, 0,
                'hash2', '2026-06-10T00:00:00+00:00', '2026-06-10T00:01:00+00:00'
            );
            """
        )
        # 3. Recent completed job (5 days old) -> MUST be retained
        conn.execute(
            """
            INSERT INTO durable_jobs (
                job_id, task_key, state, health, recovery_state, workdir,
                submitted_at, started_at, completed_at, elapsed_seconds,
                owner_session_id, heartbeat_at, last_worktree_activity_at,
                exit_code, error, result_text, result_truncated, used_pty,
                prompt_hash, created_at, updated_at
            ) VALUES (
                'job-recent-completed', 'key-3', 'completed', 'COMPLETED', NULL, NULL,
                '2026-08-11T00:00:00+00:00', '2026-08-11T00:00:01+00:00', '2026-08-11T00:01:00+00:00', 59.0,
                'sess-curr', '2026-08-11T00:01:00+00:00', NULL,
                0, NULL, 'ok', 0, 0,
                'hash3', '2026-08-11T00:00:00+00:00', '2026-08-11T00:01:00+00:00'
            );
            """
        )
        # 4. Old running job (45 days old) -> MUST NEVER be pruned automatically
        conn.execute(
            """
            INSERT INTO durable_jobs (
                job_id, task_key, state, health, recovery_state, workdir,
                submitted_at, started_at, completed_at, elapsed_seconds,
                owner_session_id, heartbeat_at, last_worktree_activity_at,
                exit_code, error, result_text, result_truncated, used_pty,
                prompt_hash, created_at, updated_at
            ) VALUES (
                'job-old-running', 'key-4', 'running', 'HEALTHY', NULL, NULL,
                '2026-06-01T00:00:00+00:00', '2026-06-01T00:00:01+00:00', NULL, 59.0,
                'sess-old', '2026-06-01T00:01:00+00:00', NULL,
                NULL, NULL, NULL, 0, 0,
                'hash4', '2026-06-01T00:00:00+00:00', '2026-06-01T00:01:00+00:00'
            );
            """
        )
        # 5. Old queued job (45 days old) -> MUST NEVER be pruned automatically
        conn.execute(
            """
            INSERT INTO durable_jobs (
                job_id, task_key, state, health, recovery_state, workdir,
                submitted_at, started_at, completed_at, elapsed_seconds,
                owner_session_id, heartbeat_at, last_worktree_activity_at,
                exit_code, error, result_text, result_truncated, used_pty,
                prompt_hash, created_at, updated_at
            ) VALUES (
                'job-old-queued', 'key-5', 'queued', 'QUEUED', NULL, NULL,
                '2026-06-01T00:00:00+00:00', NULL, NULL, 0.0,
                'sess-old', '2026-06-01T00:00:00+00:00', NULL,
                NULL, NULL, NULL, 0, 0,
                'hash5', '2026-06-01T00:00:00+00:00', '2026-06-01T00:00:00+00:00'
            );
            """
        )
        # 6. Old interrupted/recovery job (45 days old) -> MUST NEVER be pruned automatically
        conn.execute(
            """
            INSERT INTO durable_jobs (
                job_id, task_key, state, health, recovery_state, workdir,
                submitted_at, started_at, completed_at, elapsed_seconds,
                owner_session_id, heartbeat_at, last_worktree_activity_at,
                exit_code, error, result_text, result_truncated, used_pty,
                prompt_hash, created_at, updated_at
            ) VALUES (
                'job-old-interrupted', 'key-6', 'unknown', 'INTERRUPTED', 'interrupted', NULL,
                '2026-06-01T00:00:00+00:00', '2026-06-01T00:00:01+00:00', NULL, 10.0,
                'sess-old', '2026-06-01T00:00:10+00:00', NULL,
                NULL, 'session ended', NULL, 0, 0,
                'hash6', '2026-06-01T00:00:00+00:00', '2026-06-01T00:00:10+00:00'
            );
            """
        )
        conn.close()

        # Run pruning relative to controlled reference time
        pruned_count = store.prune_terminal(
            older_than_seconds=30 * 86400,
            now_iso="2026-08-16T00:00:00+00:00",
        )
        assert pruned_count == 2

        # Verify exact retention
        assert store.get_job("job-old-completed") is None
        assert store.get_job("job-old-failed") is None
        assert store.get_job("job-recent-completed") is not None
        assert store.get_job("job-old-running") is not None
        assert store.get_job("job-old-queued") is not None
        assert store.get_job("job-old-interrupted") is not None


def test_persistence_degraded_watchdog_survival_and_worker_completion(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    worker_started = threading.Event()
    unblock_worker = threading.Event()

    def controlled_run(*args, **kwargs):
        worker_started.set()
        unblock_worker.wait(timeout=5.0)
        return AgyResult(text="WORKER_SUCCESS", exit_code=0, used_pty=False)

    mp.setattr("codex_agy_bridge.agy_jobs.run_agy", controlled_run)
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "degraded_jobs.sqlite3"
        registry = AgyJobRegistry(
            db_path=db_file,
            watchdog_interval=0.05,
        )
        try:
            job_id = registry.start("Degraded persistence task")
            worker_started.wait(timeout=2.0)

            # Simulate store heartbeat failure
            orig_update = registry._store.update_heartbeat
            def failing_update(*args, **kwargs):
                raise sqlite3.OperationalError("disk I/O error simulation")

            registry._store.update_heartbeat = failing_update

            # Wait for watchdog to tick and encounter the failure
            time.sleep(0.12)

            status = registry.status(job_id)
            assert status["state"] == "running"
            assert status.get("supervision_persistence") == "degraded"
            assert "DURABLE_STORE_ERROR" in str(status.get("supervision_persistence_error"))

            # Watchdog and worker MUST still be alive and not killed/cancelled
            assert not registry._jobs[job_id].future.done()
            assert registry._watchdog_thread.is_alive()

            # Restore store functionality
            registry._store.update_heartbeat = orig_update
            time.sleep(0.1)

            # Next watchdog tick clears degraded signal
            status_recovered = registry.status(job_id)
            assert status_recovered["state"] == "running"
            assert "supervision_persistence" not in status_recovered

            # Worker completes successfully
            unblock_worker.set()
            res = registry.wait(job_id, wait_seconds=2.0)
            assert res["state"] == "completed"
            assert res["health"] == "COMPLETED"
            assert res["text"] == "WORKER_SUCCESS"
        finally:
            unblock_worker.set()
            registry.close()
            if monkeypatch is None:
                mp.undo()


def test_durable_store_error_exposure_and_prelaunch_reservation():
    with tempfile.TemporaryDirectory() as tmp_dir:
        # 1. Corrupted non-sqlite file should raise DURABLE_STORE_ERROR on init
        bad_file = Path(tmp_dir) / "corrupt.sqlite3"
        bad_file.write_text("CORRUPTED_NOT_SQLITE_HEADER", encoding="utf-8")

        with pytest.raises(RuntimeError, match="DURABLE_STORE_ERROR"):
            DurableJobStore(db_path=bad_file)

        # 2. Unsupported schema version should raise DURABLE_SCHEMA_UNSUPPORTED
        schema_file = Path(tmp_dir) / "future_schema.sqlite3"
        conn = sqlite3.connect(str(schema_file), isolation_level=None)
        conn.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);")
        conn.execute("INSERT INTO schema_meta VALUES ('schema_version', '999');")
        conn.close()

        with pytest.raises(RuntimeError, match="DURABLE_SCHEMA_UNSUPPORTED"):
            DurableJobStore(db_path=schema_file)

        # 3. Pre-launch reservation store failure surfaces to registry.start and starts no worker
        db_file = Path(tmp_dir) / "good_jobs.sqlite3"
        registry = AgyJobRegistry(db_path=db_file)
        try:
            def failing_reserve(*args, **kwargs):
                raise RuntimeError("DURABLE_STORE_ERROR: database is locked")

            registry._store.reserve_and_create = failing_reserve

            with pytest.raises(RuntimeError, match="DURABLE_STORE_ERROR"):
                registry.start("Should not launch")

            # Confirm no worker was queued/submitted into jobs map
            assert len(registry._jobs) == 0
        finally:
            registry.close()


def test_schema_meta_missing_schema_version_row_raises_unsupported():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "missing_version.sqlite3"
        conn = sqlite3.connect(str(db_file), isolation_level=None)
        conn.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);")
        conn.execute("INSERT INTO schema_meta VALUES ('other_key', 'some_value');")
        conn.close()

        with pytest.raises(RuntimeError, match="DURABLE_SCHEMA_UNSUPPORTED"):
            DurableJobStore(db_path=db_file)

        # Confirm table was not dropped/overwritten
        conn = sqlite3.connect(str(db_file), isolation_level=None)
        cur = conn.cursor()
        cur.execute("SELECT key, value FROM schema_meta;")
        rows = cur.fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0] == ("other_key", "some_value")


def test_schema_fresh_and_valid_v1_work():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "fresh.sqlite3"
        # 1. Fresh empty DB
        store1 = DurableJobStore(db_path=db_file)
        assert store1.get_recent() == []

        # 2. Existing valid v1 DB
        store2 = DurableJobStore(db_path=db_file)
        assert store2.get_recent() == []


def test_runtime_automatic_terminal_pruning_startup_and_completion(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    mp.setattr(
        "codex_agy_bridge.agy_jobs.run_agy",
        lambda *args, **kwargs: AgyResult(text="DONE_AUTO_PRUNE", exit_code=0, used_pty=False),
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "auto_prune.sqlite3"

        # Pre-populate DB with expired terminal, active, and interrupted jobs
        store = DurableJobStore(db_path=db_file)
        conn = sqlite3.connect(str(db_file), isolation_level=None)
        conn.execute(
            """
            INSERT INTO durable_jobs (
                job_id, task_key, state, health, recovery_state, workdir,
                submitted_at, started_at, completed_at, elapsed_seconds,
                owner_session_id, heartbeat_at, last_worktree_activity_at,
                exit_code, error, result_text, result_truncated, used_pty,
                prompt_hash, created_at, updated_at
            ) VALUES
            ('old-comp', 'k1', 'completed', 'COMPLETED', NULL, NULL,
             '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', '2026-01-01T00:01:00+00:00', 60.0,
             's0', '2026-01-01T00:01:00+00:00', NULL, 0, NULL, 'ok', 0, 0, 'h1', '2026-01-01T00:00:00+00:00', '2026-01-01T00:01:00+00:00'),
            ('old-fail', 'k2', 'failed', 'FAILED', NULL, NULL,
             '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', '2026-01-01T00:01:00+00:00', 60.0,
             's0', '2026-01-01T00:01:00+00:00', NULL, 1, 'err', 'fail', 0, 0, 'h2', '2026-01-01T00:00:00+00:00', '2026-01-01T00:01:00+00:00'),
            ('rec-comp', 'k3', 'completed', 'COMPLETED', NULL, NULL,
             '2026-08-16T00:00:00+00:00', '2026-08-16T00:00:00+00:00', '2026-08-16T00:01:00+00:00', 60.0,
             's0', '2026-08-16T00:01:00+00:00', NULL, 0, NULL, 'ok', 0, 0, 'h3', '2026-08-16T00:00:00+00:00', '2026-08-16T00:01:00+00:00'),
            ('old-run', 'k4', 'running', 'HEALTHY', NULL, NULL,
             '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', NULL, 60.0,
             's0', '2026-01-01T00:01:00+00:00', NULL, NULL, NULL, NULL, 0, 0, 'h4', '2026-01-01T00:00:00+00:00', '2026-01-01T00:01:00+00:00'),
            ('old-intr', 'k5', 'unknown', 'INTERRUPTED', 'interrupted', NULL,
             '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', NULL, 60.0,
             's0', '2026-01-01T00:01:00+00:00', NULL, NULL, 'interrupted', NULL, 0, 0, 'h5', '2026-01-01T00:00:00+00:00', '2026-01-01T00:01:00+00:00');
            """
        )
        conn.close()

        # 1. Startup automatic prune test
        registry = AgyJobRegistry(
            db_path=db_file,
            store_retention_seconds=30 * 86400,
            store_prune_interval=0.0,
        )
        try:
            assert registry._store.get_job("old-comp") is None
            assert registry._store.get_job("old-fail") is None
            assert registry._store.get_job("rec-comp") is not None
            old_run_rec = registry._store.get_job("old-run")
            assert old_run_rec is not None
            assert old_run_rec["recovery_state"] == "interrupted"
            assert registry._store.get_job("old-intr") is not None

            # Insert another expired completed job into store
            conn2 = sqlite3.connect(str(db_file), isolation_level=None)
            conn2.execute(
                """
                INSERT INTO durable_jobs (
                    job_id, task_key, state, health, recovery_state, workdir,
                    submitted_at, started_at, completed_at, elapsed_seconds,
                    owner_session_id, heartbeat_at, last_worktree_activity_at,
                    exit_code, error, result_text, result_truncated, used_pty,
                    prompt_hash, created_at, updated_at
                ) VALUES (
                    'old-comp-2', 'k6', 'completed', 'COMPLETED', NULL, NULL,
                    '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', '2026-01-01T00:01:00+00:00', 60.0,
                    's0', '2026-01-01T00:01:00+00:00', NULL,
                    0, NULL, 'ok', 0, 0,
                    'h6', '2026-01-01T00:00:00+00:00', '2026-01-01T00:01:00+00:00'
                );
                """
            )
            conn2.close()

            # Status and wait should NOT prune store
            dummy_id = registry.start("Dummy quick")
            registry.status(dummy_id)
            assert registry._store.get_job("old-comp-2") is not None

            # 2. Terminal completion maintenance test
            job_id = registry.start("New job to trigger terminal prune maintenance")
            res = registry.wait(job_id, wait_seconds=2.0)
            assert res["state"] == "completed"

            # Verify that old-comp-2 has now been automatically pruned on completion
            assert registry._store.get_job("old-comp-2") is None
        finally:
            registry.close()
            if monkeypatch is None:
                mp.undo()


def test_completed_task_key_reusable_after_restart_and_reconciliation(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    mp.setattr(
        "codex_agy_bridge.agy_jobs.run_agy",
        lambda *args, **kwargs: AgyResult(text="SESSION_A_COMPLETED", exit_code=0, used_pty=False),
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "test_jobs.sqlite3"

        # Session A: start same task_key X, normal completed persisted
        reg_a = AgyJobRegistry(db_path=db_file)
        try:
            job_id_a = reg_a.start("Prompt task X", task_key="task-key-X")
            res_a = reg_a.wait(job_id_a, wait_seconds=2.0)
            assert res_a["state"] == "completed"
            assert res_a["health"] == "COMPLETED"
            assert res_a["text"] == "SESSION_A_COMPLETED"
        finally:
            reg_a.close()

        # Session B: restart/reload -> startup reconciliation
        reg_b = AgyJobRegistry(db_path=db_file)
        try:
            # Status remains completed and recent must not produce false interrupted
            status_a = reg_b.status(job_id_a)
            assert status_a["state"] == "completed"
            assert status_a["health"] == "COMPLETED"
            assert status_a.get("recovery_state") is None

            recent = reg_b.recent(limit=10, task_key="task-key-X")
            assert len(recent) == 1
            assert recent[0]["job_id"] == job_id_a
            assert recent[0]["state"] == "completed"
            assert recent[0].get("recovery_state") is None

            # Session B same task key X start must accept a new job ID
            job_id_b = reg_b.start("Prompt task X second run", task_key="task-key-X")
            assert job_id_b != job_id_a
            res_b = reg_b.wait(job_id_b, wait_seconds=2.0)
            assert res_b["job_id"] == job_id_b
            assert res_b["state"] == "completed"
        finally:
            reg_b.close()
            if monkeypatch is None:
                mp.undo()


def test_terminal_failed_task_key_semantics_and_reuse(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    mp.setattr(
        "codex_agy_bridge.agy_jobs.run_agy",
        lambda *args, **kwargs: AgyResult(text="FAILED_OUTPUT", exit_code=1, stderr="fatal error", used_pty=False),
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "test_jobs.sqlite3"

        # Direct insertion or execution of a real historical failed job
        historical_failed_id = "fcbd5de91c494a6a89f71cfcc880c76b"
        store = DurableJobStore(db_path=db_file)
        now_iso = "2026-08-16T10:00:00Z"
        store.reserve_and_create(
            job_id=historical_failed_id,
            task_key="historical-failed-key",
            workdir="C:\\tmp",
            prompt_hash="hash123",
            owner_session_id="old-session-fail",
            now_iso=now_iso,
        )
        store.mark_started(historical_failed_id, now_iso, now_iso)
        store.mark_terminal(
            job_id=historical_failed_id,
            state="failed",
            health="FAILED",
            exit_code=1,
            error="historical worker failure",
            result_text="FAILED_OUTPUT",
            result_truncated=False,
            used_pty=False,
            started_at=now_iso,
            completed_at=now_iso,
            elapsed_seconds=10.0,
            now_iso=now_iso,
        )

        # New registry starts and reconciles
        reg = AgyJobRegistry(db_path=db_file)
        try:
            status = reg.status(historical_failed_id)
            assert status["state"] == "failed"
            assert status["health"] == "FAILED"
            assert status.get("recovery_state") is None
            assert status["exit_code"] == 1

            recent = reg.recent(limit=10, task_key="historical-failed-key")
            assert len(recent) == 1
            assert recent[0]["job_id"] == historical_failed_id
            assert recent[0]["state"] == "failed"
            assert recent[0].get("recovery_state") is None

            # New job with same task_key succeeds without RECOVERY_REQUIRED or DUPLICATE_ACTIVE_TASK
            new_id = reg.start("Retry failed task", task_key="historical-failed-key")
            assert new_id != historical_failed_id
        finally:
            reg.close()
            if monkeypatch is None:
                mp.undo()


def test_terminal_completion_with_stale_recovery_metadata_permits_reuse():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "test_jobs.sqlite3"
        store = DurableJobStore(db_path=db_file)

        # Reproduce the production bug: a terminal completed row still carries
        # stale interrupted recovery metadata after a session boundary.
        conn = store._get_connection()
        conn.execute(
            """
            INSERT INTO durable_jobs (
                job_id, task_key, state, health, recovery_state, workdir,
                submitted_at, started_at, completed_at, elapsed_seconds,
                owner_session_id, heartbeat_at, last_worktree_activity_at,
                exit_code, error, result_text, result_truncated, used_pty,
                prompt_hash, created_at, updated_at
            ) VALUES
            ('job-completed-stale-recovery', 'key-seq', 'completed', 'COMPLETED', 'interrupted', 'C:\\tmp',
             '2026-08-16T00:05:00Z', '2026-08-16T00:05:01Z', '2026-08-16T00:05:10Z', 9.0,
             'session-2', '2026-08-16T00:05:10Z', NULL, 0, NULL, 'SUCCESS', 0, 0,
             'hash2', '2026-08-16T00:05:00Z', '2026-08-16T00:05:10Z');
            """
        )
        conn.close()

        # Session 3 starts up
        reg3 = AgyJobRegistry(db_path=db_file)
        try:
            status = reg3.status("job-completed-stale-recovery")
            assert status["state"] == "completed"

            # Terminal completed state must override stale recovery metadata.
            new_job_id = reg3.start("New attempt for key-seq", task_key="key-seq")
            assert new_job_id != "job-completed-stale-recovery"
        finally:
            reg3.close()


def test_concurrent_reconciliation_race_terminal_clears_interrupted(monkeypatch=None):
    mp = monkeypatch or _SimpleMonkeyPatch()
    started_event = threading.Event()
    unblock_event = threading.Event()

    def controlled_runner(*args, **kwargs):
        started_event.set()
        assert unblock_event.wait(timeout=5.0), "Timed out waiting for unblock event"
        return AgyResult(text="RACE_WON", exit_code=0, used_pty=False)

    mp.setattr("codex_agy_bridge.agy_jobs.run_agy", controlled_runner)
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_file = Path(tmp_dir) / "test_jobs.sqlite3"

        # Session 1 starts job
        reg1 = AgyJobRegistry(db_path=db_file)
        try:
            job_id = reg1.start("Race task", task_key="race-key")
            assert started_event.wait(timeout=2.0), "Worker did not start in time"

            # Simulate Session 2 reconciling mid-flight before completion
            store2 = DurableJobStore(db_path=db_file)
            store2.reconcile_other_sessions("session-2", "2026-08-16T01:00:00Z")

            # Verify store had marked it interrupted mid-flight
            conn = store2._get_connection()
            cur = conn.execute("SELECT state, health, recovery_state FROM durable_jobs WHERE job_id = ?", (job_id,))
            raw_row = cur.fetchone()
            conn.close()
            assert raw_row["state"] == "unknown"
            assert raw_row["health"] == "INTERRUPTED"
            assert raw_row["recovery_state"] == "interrupted"

            # Now unblock reg1 runner to finish and mark terminal
            unblock_event.set()
            res = reg1.wait(job_id, wait_seconds=2.0)
            assert res["state"] == "completed"
            assert res["health"] == "COMPLETED"

            # Query SQLite directly: get_job() intentionally normalizes legacy
            # terminal rows for callers, but the durable journal itself must
            # clear the stale recovery marker when a worker reaches terminal.
            conn = store2._get_connection()
            raw_after = conn.execute(
                "SELECT state, health, recovery_state FROM durable_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            conn.close()
            assert raw_after["state"] == "completed"
            assert raw_after["health"] == "COMPLETED"
            assert raw_after["recovery_state"] is None

            # New session can start same key without RECOVERY_REQUIRED
            reg3 = AgyJobRegistry(db_path=db_file)
            try:
                j3 = reg3.start("Followup after race", task_key="race-key")
                assert j3 != job_id
            finally:
                reg3.close()
        finally:
            unblock_event.set()
            reg1.close()
            if monkeypatch is None:
                mp.undo()
