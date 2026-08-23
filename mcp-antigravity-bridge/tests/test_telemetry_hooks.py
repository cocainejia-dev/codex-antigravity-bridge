"""Comprehensive deterministic tests for Usage Telemetry Hooks."""

from __future__ import annotations

import concurrent.futures
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import codex_agy_bridge  # noqa: E402
_local_pkg_path = str(SRC_DIR / "codex_agy_bridge")
if hasattr(codex_agy_bridge, "__path__") and _local_pkg_path not in codex_agy_bridge.__path__:
    codex_agy_bridge.__path__.insert(0, _local_pkg_path)

import pytest  # noqa: E402

from codex_agy_bridge.agy_jobs import AgyJobRegistry  # noqa: E402
from codex_agy_bridge.agy_runner import AgyResult, LOCAL_SUPERVISION_TIMEOUT, CONNECT_TIMEOUT, REMOTE_EXECUTION_TIMEOUT  # noqa: E402
from codex_agy_bridge.contracts import RunState, TaskContract  # noqa: E402
from codex_agy_bridge.run_control import DurableRunManager, DurableRunStore, WorkerContext, WorkerResult  # noqa: E402
from codex_agy_bridge.telemetry import (  # noqa: E402
    MeasurementSource,
    UsageEvent,
    UsageLedger,
    UsageSummary,
    normalize_project_path,
)
from codex_agy_bridge.telemetry_hooks import (  # noqa: E402
    get_telemetry_ledger,
    record_account_switch_event,
    record_agy_job_completion_event,
    record_agy_job_start_event,
    record_oneshot_call_event,
    record_reconciliation_event,
    record_retry_event,
    record_run_resume_event,
    record_run_start_event,
    record_timeout_event,
    record_worker_completion_event,
    record_worker_launch_event,
    reset_telemetry_ledgers,
    safe_inspect_worktree_diff,
)


@pytest.fixture(autouse=True)
def _auto_reset_ledgers():
    """Ensure all ledger SQLite connections are cleanly closed before and after each test."""
    reset_telemetry_ledgers()
    yield
    reset_telemetry_ledgers()


@contextmanager
def temporary_db_dir():
    """Create a temporary directory and guarantee all SQLite handles are closed before deletion."""
    with tempfile.TemporaryDirectory() as td:
        try:
            yield Path(td)
        finally:
            reset_telemetry_ledgers()


def _setup_test_git_repo(tmp_dir: Path) -> Path:
    """Initialize a small git repository for testing diff inspection."""
    repo = tmp_dir / "test_repo"
    repo.mkdir(parents=True, exist_ok=True)
    extra_kwargs = {}
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        extra_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True, **extra_kwargs)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), check=True, capture_output=True, **extra_kwargs)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo), check=True, capture_output=True, **extra_kwargs)
    f = repo / "file.txt"
    f.write_text("line 1\nline 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "file.txt"], cwd=str(repo), check=True, capture_output=True, **extra_kwargs)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), check=True, capture_output=True, **extra_kwargs)
    return repo


def test_run_start_and_worker_launch_hooks():
    """Verify run_start and worker_launch record exact metrics, turns, and metadata."""
    with temporary_db_dir() as td:
        db_path = td / "telemetry.sqlite3"

        ev_start = record_run_start_event(
            run_id="run-101",
            task_id="task-101",
            project_dir="D:/Projects/App",
            db_path=db_path,
            metadata={"source": "pytest"},
        )
        assert ev_start is not None
        assert ev_start.run_id == "run-101"
        assert ev_start.task_id == "task-101"
        assert ev_start.actor == "codex"
        assert ev_start.event_type == "run_start"
        assert ev_start.measurement_type == "call_count"
        assert ev_start.value == 1.0

        ev_launch = record_worker_launch_event(
            run_id="run-101",
            task_id="task-101",
            project_dir="D:/Projects/App",
            attempt=1,
            repair_round=0,
            worker_identity={"runtime": "agy-vnext"},
            db_path=db_path,
        )
        assert ev_launch is not None
        assert ev_launch.actor == "agy"
        assert ev_launch.event_type == "worker_launch"
        assert ev_launch.value == 1.0
        assert ev_launch.metadata["attempt"] == 1
        assert ev_launch.metadata["worker_identity"] == {"runtime": "agy-vnext"}

        ledger = get_telemetry_ledger(db_path)
        events = ledger.query(run_id="run-101")
        assert len(events) == 3  # run_start call_count, run_start monitoring_turns=0.0, worker_launch call_count

        turn_events = [e for e in events if e.measurement_type == "monitoring_turns"]
        assert len(turn_events) == 1
        assert turn_events[0].value == 0.0
        assert turn_events[0].unit == "turns"
        assert turn_events[0].measurement_source == MeasurementSource.DERIVED
        assert turn_events[0].confidence == 1.0


def test_worker_completion_success_and_metrics():
    """Verify worker_completion records duration, outcomes, unavailable tokens, and 0-turn preservation."""
    with temporary_db_dir() as td:
        db_path = td / "telemetry.sqlite3"
        repo = _setup_test_git_repo(td)

        # Modify repo to test diff capture
        (repo / "file.txt").write_text("line 1\nmodified line 2\nadded line 3\n", encoding="utf-8")

        events = record_worker_completion_event(
            run_id="run-202",
            task_id="task-202",
            project_dir=repo,
            duration_seconds=42.5,
            success=True,
            target_state="COMPLETE",
            verification_result={"passed": True, "status": "all_passed"},
            db_path=db_path,
        )
        assert len(events) >= 5

        types = {e.measurement_type: e for e in events}
        assert "call_count" in types
        assert types["call_count"].value == 1.0
        assert types["call_count"].actor == "agy"

        assert "success_count" in types
        assert types["success_count"].value == 1.0

        assert "duration_seconds" in types
        assert types["duration_seconds"].value == 42.5
        assert types["duration_seconds"].unit == "seconds"

        assert "tokens" in types
        assert types["tokens"].value is None
        assert types["tokens"].measurement_source == MeasurementSource.UNAVAILABLE
        assert types["tokens"].confidence == 0.0

        assert "monitoring_turns" in types
        assert types["monitoring_turns"].value == 0.0
        assert types["monitoring_turns"].actor == "codex"

        assert "changed_files" in types
        assert types["changed_files"].value >= 1.0
        assert "lines_of_code" in types
        assert types["lines_of_code"].value >= 1.0


def test_worker_completion_failure_and_timeout_classification():
    """Verify worker completion with timeout error records failure_count and timeout_classified event."""
    with temporary_db_dir() as td:
        db_path = td / "telemetry.sqlite3"

        events = record_worker_completion_event(
            run_id="run-303",
            task_id="task-303",
            project_dir="D:/Projects/App",
            duration_seconds=120.0,
            success=False,
            last_error="Local supervision timeout: agy timed out after 120s",
            db_path=db_path,
        )
        types = {e.measurement_type: e for e in events}
        assert "failure_count" in types
        assert types["failure_count"].value == 1.0
        assert "timeout_count" in types
        assert types["timeout_count"].value == 1.0
        assert types["timeout_count"].metadata["timeout_class"] == "LOCAL_SUPERVISION_TIMEOUT"


def test_account_switch_and_run_resume_hooks():
    """Verify account switch and run resume hooks record correct metrics."""
    with temporary_db_dir() as td:
        db_path = td / "telemetry.sqlite3"

        ev_switch = record_account_switch_event(
            run_id="run-404",
            task_id="task-404",
            project_dir="D:/Projects/App",
            reason="daily quota exceeded on primary account",
            db_path=db_path,
        )
        assert ev_switch is not None
        assert ev_switch.actor == "bridge"
        assert ev_switch.event_type == "account_switch_required"
        assert ev_switch.measurement_type == "account_switches"
        assert ev_switch.value == 1.0

        ev_resume = record_run_resume_event(
            run_id="run-404",
            task_id="task-404",
            project_dir="D:/Projects/App",
            attempt=2,
            account_switched=True,
            credentials_refreshed=False,
            db_path=db_path,
        )
        assert ev_resume is not None
        assert ev_resume.actor == "codex"
        assert ev_resume.event_type == "run_resumed"
        assert ev_resume.measurement_type == "resumptions"
        assert ev_resume.value == 1.0
        assert ev_resume.metadata["account_switched"] is True
        assert ev_resume.metadata["attempt"] == 2


def test_agy_job_lifecycle_hooks():
    """Verify asynchronous AGY job start and completion hooks."""
    with temporary_db_dir() as td:
        db_path = td / "telemetry.sqlite3"

        ev_start = record_agy_job_start_event(
            job_id="job-505",
            task_key="task-parallel-1",
            workdir="D:/Projects/App",
            db_path=db_path,
        )
        assert ev_start is not None
        assert ev_start.run_id == "job-505"
        assert ev_start.event_type == "job_start"
        assert ev_start.value == 1.0

        events_done = record_agy_job_completion_event(
            job_id="job-505",
            task_key="task-parallel-1",
            workdir="D:/Projects/App",
            elapsed_seconds=15.2,
            exit_code=0,
            result_text="All tasks finished successfully",
            db_path=db_path,
        )
        assert len(events_done) >= 4
        m_types = {e.measurement_type: e for e in events_done}
        assert "call_count" in m_types
        assert "success_count" in m_types
        assert "duration_seconds" in m_types
        assert "tokens" in m_types
        assert "monitoring_turns" in m_types


def test_agy_job_timeout_completion_hooks():
    """Verify asynchronous AGY job timeout failure records timeout event."""
    with temporary_db_dir() as td:
        db_path = td / "telemetry.sqlite3"

        events_done = record_agy_job_completion_event(
            job_id="job-606",
            task_key="task-timeout-1",
            workdir="D:/Projects/App",
            elapsed_seconds=300.0,
            exit_code=1,
            error_kind="CONNECT_TIMEOUT",
            error_text="dial tcp 127.0.0.1:7890: connectex: connection timed out",
            db_path=db_path,
        )
        m_types = {e.measurement_type: e for e in events_done}
        assert "failure_count" in m_types
        assert "timeout_count" in m_types
        assert m_types["timeout_count"].metadata["timeout_class"] == "CONNECT_TIMEOUT"


def test_oneshot_call_hooks():
    """Verify synchronous oneshot agy_ask / agy_ask_json hook recording and prompt hashing."""
    with temporary_db_dir() as td:
        db_path = td / "telemetry.sqlite3"

        events_success = record_oneshot_call_event(
            prompt="Refactor the authentication module with sk-secret-key-12345",
            workdir="D:/Projects/App",
            duration_seconds=5.4,
            exit_code=0,
            db_path=db_path,
        )
        assert len(events_success) >= 4
        m_types = {e.measurement_type: e for e in events_success}
        assert "call_count" in m_types
        assert "success_count" in m_types
        assert "duration_seconds" in m_types
        assert "tokens" in m_types

        # Verify prompt is NEVER stored in plaintext and is safely hashed
        call_ev = m_types["call_count"]
        assert "prompt_hash" in call_ev.metadata
        assert "sk-secret-key" not in json.dumps(call_ev.metadata)
        assert "Refactor" not in json.dumps(call_ev.metadata)

        # Timeout oneshot
        events_to = record_oneshot_call_event(
            prompt="Long running query",
            workdir="D:/Projects/App",
            duration_seconds=30.0,
            exit_code=1,
            error_kind="REMOTE_EXECUTION_TIMEOUT",
            db_path=db_path,
        )
        to_types = {e.measurement_type: e for e in events_to}
        assert "failure_count" in to_types
        assert "timeout_count" in to_types
        assert to_types["timeout_count"].metadata["timeout_class"] == "REMOTE_EXECUTION_TIMEOUT"


def test_secret_redaction_in_all_hooks():
    """Verify complete redaction of passwords, bearer tokens, api keys, jwt tokens, cookies."""
    with temporary_db_dir() as td:
        db_path = td / "telemetry.sqlite3"

        secret_meta = {
            "api_key": "sk-123456789012345678901234567890",
            "bearer_header": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.doNotLeakThisSignature",
            "password": "SuperSecretPassword123!",
            "cookie": "session_id=abcdef123456; PHPSESSID=xyz789",
            "raw_prompt": "Please do not leak this prompt content",
            "nested": {
                "token": "ghp_123456789012345678901234567890123456",
                "safe_field": "public_data",
            },
        }

        ev = record_run_start_event(
            run_id="run-secret-1",
            task_id="task-secret-1",
            project_dir="D:/Projects/App",
            metadata=secret_meta,
            db_path=db_path,
        )
        assert ev is not None

        raw_json = json.dumps(ev.metadata)
        assert "sk-1234567890" not in raw_json
        assert "eyJhbGciOi" not in raw_json
        assert "SuperSecretPassword" not in raw_json
        assert "abcdef123456" not in raw_json
        assert "ghp_123456" not in raw_json
        assert "Please do not leak" not in raw_json
        assert "[REDACTED" in raw_json
        assert ev.metadata["nested"]["safe_field"] == "public_data"


def test_durable_run_manager_telemetry_integration_and_no_observation_duplicates():
    """Verify DurableRunManager lifecycle records telemetry to the specified DB and observation never duplicates."""
    with temporary_db_dir() as td:
        db_path = td / "durable_runs.sqlite3"
        mgr = DurableRunManager(db_path)

        task = TaskContract(
            task_id="task-dm-1",
            objective="Implement feature X",
            base_head="base_head_dm",
            workdir=str(td),
        )

        def worker_success(ctx: WorkerContext) -> WorkerResult:
            time.sleep(0.05)
            return WorkerResult(success=True, result_summary="Done")

        # 1. Run start and execution
        record = mgr.run_start(task, worker=worker_success)
        result = mgr.run_wait(record.run_id, timeout=5.0)
        assert result.state == RunState.COMPLETE

        # Verify telemetry in ledger
        ledger = get_telemetry_ledger(db_path)
        events = ledger.query(run_id=record.run_id)
        assert len(events) >= 4  # run_start, worker_launch, worker_completion (calls, success, dur, tokens, turns)

        # 2. Observe run multiple times: MUST NOT duplicate telemetry events!
        count_before = len(ledger.query(run_id=record.run_id))
        for _ in range(10):
            obs = mgr.run_observe(record.run_id)
            assert obs.is_terminal is True

        count_after = len(ledger.query(run_id=record.run_id))
        assert count_before == count_after, f"Observation poll duplicated telemetry events! {count_before} != {count_after}"


def test_durable_run_manager_timeout_and_interrupted_telemetry():
    """Verify heartbeat interruption records timeout and reconciliation telemetry without duplicate polling."""
    with temporary_db_dir() as td:
        db_path = td / "durable_runs.sqlite3"
        mgr = DurableRunManager(db_path)

        task = TaskContract(
            task_id="task-dm-timeout",
            objective="Hanging task",
            base_head="base_head_dm",
            workdir=str(td),
        )

        # Insert a run manually in RUNNING state with stale heartbeat and alive PID
        stale_hb = datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc).isoformat()
        r = mgr.run_start(
            task,
            auto_spawn=False,
            worker_identity={"worker_type": "process", "pid": os.getpid()},
        )
        r = mgr.store.transition_run(
            r.run_id,
            expected_version=r.state_version,
            target_state=RunState.QUEUED,
        )
        r = mgr.store.transition_run(
            r.run_id,
            expected_version=r.state_version,
            target_state=RunState.RUNNING,
            pid=os.getpid(),
        )
        mgr.store.update_heartbeat(r.run_id, timestamp=stale_hb)

        # First observation marks interrupted and records timeout & reconciliation
        obs1 = mgr.run_observe(r.run_id, stale_heartbeat_threshold_seconds=1.0)
        assert obs1.state == RunState.INTERRUPTED

        ledger = get_telemetry_ledger(db_path)
        to_events_1 = ledger.query(run_id=r.run_id, measurement_type="timeout_count")
        assert len(to_events_1) == 1

        # Subsequent observation checks MUST NOT add more timeout events!
        for _ in range(5):
            mgr.run_observe(r.run_id, stale_heartbeat_threshold_seconds=1.0)

        to_events_2 = ledger.query(run_id=r.run_id, measurement_type="timeout_count")
        assert len(to_events_1) == len(to_events_2)


def test_durable_run_manager_account_switch_telemetry():
    """Verify worker returning ACCOUNT_SWITCH_REQUIRED records account_switch event."""
    with temporary_db_dir() as td:
        db_path = td / "durable_runs.sqlite3"
        mgr = DurableRunManager(db_path)

        task = TaskContract(
            task_id="task-dm-quota",
            objective="Quota exhausting task",
            base_head="base_head_dm",
            workdir=str(td),
        )

        def worker_quota(ctx: WorkerContext) -> WorkerResult:
            return WorkerResult(
                success=False,
                target_state=RunState.ACCOUNT_SWITCH_REQUIRED,
                suspended_reason="Rate limit exceeded (429)",
                last_error="Resource quota exhausted",
            )

        record = mgr.run_start(task, worker=worker_quota)
        result = mgr.run_wait(record.run_id, timeout=5.0)
        assert result.state == RunState.ACCOUNT_SWITCH_REQUIRED

        ledger = get_telemetry_ledger(db_path)
        switch_events = ledger.query(run_id=record.run_id, measurement_type="account_switches")
        assert len(switch_events) == 1
        assert switch_events[0].actor == "bridge"


def test_proof_hook_and_storage_failures_never_alter_execution():
    """Proof that telemetry hook failures and storage crashes NEVER change execution outcomes."""
    with temporary_db_dir() as td:
        db_path = td / "durable_runs.sqlite3"
        mgr = DurableRunManager(db_path)

        task = TaskContract(
            task_id="task-failsafe",
            objective="Must complete even if telemetry is completely broken",
            base_head="base_head_dm",
            workdir=str(td),
        )

        def worker_fn(ctx: WorkerContext) -> WorkerResult:
            return WorkerResult(success=True, result_summary="100% SUCCESS")

        # Mock every telemetry hook to raise unexpected exceptions
        with patch("codex_agy_bridge.telemetry_hooks.record_run_start_event", side_effect=RuntimeError("TELEMETRY CRASH 1")), \
             patch("codex_agy_bridge.telemetry_hooks.record_worker_launch_event", side_effect=OSError("TELEMETRY DISK FULL")), \
             patch("codex_agy_bridge.telemetry_hooks.record_worker_completion_event", side_effect=sqlite3.DatabaseError("DB CORRUPT")), \
             patch("codex_agy_bridge.telemetry_hooks.record_account_switch_event", side_effect=Exception("UNEXPECTED")), \
             patch("codex_agy_bridge.telemetry_hooks.record_timeout_event", side_effect=Exception("UNEXPECTED")):

            # 1. run_start succeeds
            record = mgr.run_start(task, worker=worker_fn)
            assert record.run_id is not None

            # 2. worker execution completes and transitions to COMPLETE
            final_record = mgr.run_wait(record.run_id, timeout=5.0)
            assert final_record.state == RunState.COMPLETE
            assert final_record.result_summary == "100% SUCCESS"

            # 3. run_result returns expected result
            res = mgr.run_result(record.run_id)
            assert res.state == RunState.COMPLETE
            assert res.result_summary == "100% SUCCESS"

            # 4. run_observe returns valid observation
            obs = mgr.run_observe(record.run_id)
            assert obs.is_terminal is True


def test_telemetry_db_path_propagation():
    """Verify that custom db_path propagates to the exact targeted SQLite database."""
    with temporary_db_dir() as td:
        custom_db_1 = td / "custom1.sqlite3"
        custom_db_2 = td / "custom2.sqlite3"

        record_run_start_event(run_id="run-c1", task_id="t-1", db_path=custom_db_1)
        record_run_start_event(run_id="run-c2", task_id="t-2", db_path=custom_db_2)

        ledger1 = get_telemetry_ledger(custom_db_1)
        ledger2 = get_telemetry_ledger(custom_db_2)

        assert len(ledger1.query(run_id="run-c1")) > 0
        assert len(ledger1.query(run_id="run-c2")) == 0

        assert len(ledger2.query(run_id="run-c2")) > 0
        assert len(ledger2.query(run_id="run-c1")) == 0


def test_safe_inspect_worktree_diff():
    """Verify safe_inspect_worktree_diff behaves safely on valid, invalid, and non-git dirs."""
    assert safe_inspect_worktree_diff(None) is None
    assert safe_inspect_worktree_diff("/non/existent/path/999") is None

    with temporary_db_dir() as td:
        # Non-git directory
        non_git = td / "not_git"
        non_git.mkdir()
        assert safe_inspect_worktree_diff(non_git) is None

        # Valid git repo
        repo = _setup_test_git_repo(td)
        diff0 = safe_inspect_worktree_diff(repo)
        assert diff0 is not None
        assert diff0[0] == 0  # No modified files initially

        # Modify a file
        (repo / "file.txt").write_text("line 1\nline 2 changed\nline 3 new\n", encoding="utf-8")
        diff1 = safe_inspect_worktree_diff(repo)
        assert diff1 is not None
        assert diff1[0] >= 1  # 1 changed file
        assert diff1[1] >= 1  # changed lines


def test_reconciliation_and_retry_hooks():
    """Verify reconciliation and retry lifecycle hooks record exact values."""
    with temporary_db_dir() as td:
        db_path = td / "telemetry.sqlite3"

        ev_rec = record_reconciliation_event(
            run_id="run-rec-1",
            task_id="t-rec-1",
            project_dir="D:/Projects/App",
            action="reconcile_orphan",
            reason="Worker died unexpectedly",
            db_path=db_path,
        )
        assert ev_rec is not None
        assert ev_rec.actor == "bridge"
        assert ev_rec.event_type == "reconciliation"
        assert ev_rec.measurement_type == "reconciliations"
        assert ev_rec.value == 1.0

        ev_ret = record_retry_event(
            run_id="run-rec-1",
            task_id="t-rec-1",
            project_dir="D:/Projects/App",
            attempt=3,
            reason="Safe connect timeout retry",
            db_path=db_path,
        )
        assert ev_ret is not None
        assert ev_ret.actor == "bridge"
        assert ev_ret.event_type == "retry"
        assert ev_ret.measurement_type == "retries"
        assert ev_ret.value == 1.0
        assert ev_ret.metadata["attempt"] == 3
