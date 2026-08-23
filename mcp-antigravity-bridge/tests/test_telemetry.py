"""Comprehensive deterministic tests for Usage Telemetry Core."""

from __future__ import annotations

import concurrent.futures
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import codex_agy_bridge  # noqa: E402
_local_pkg_path = str(SRC_DIR / "codex_agy_bridge")
if hasattr(codex_agy_bridge, "__path__") and _local_pkg_path not in codex_agy_bridge.__path__:
    codex_agy_bridge.__path__.insert(0, _local_pkg_path)



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

from codex_agy_bridge.durable_jobs import DurableJobStore, SCHEMA_VERSION  # noqa: E402
from codex_agy_bridge.telemetry import (  # noqa: E402
    DEFAULT_SOURCE_CONFIDENCE,
    MeasurementSource,
    UsageEvent,
    UsageLedger,
    UsageSummary,
    aggregate_events,
    compute_event_id,
    deterministic_json_dumps,
    normalize_project_path,
    paths_equal,
    redact_metadata,
)


def test_measurement_source_enum_parsing():
    """Verify measurement source values and robust normalization."""
    assert MeasurementSource.PROVIDER_EXACT.value == "PROVIDER_EXACT"
    assert MeasurementSource.CLI_EXACT.value == "CLI_EXACT"
    assert MeasurementSource.QUOTA_DELTA.value == "QUOTA_DELTA"
    assert MeasurementSource.TEXT_ESTIMATE.value == "TEXT_ESTIMATE"
    assert MeasurementSource.DERIVED.value == "DERIVED"
    assert MeasurementSource.UNAVAILABLE.value == "UNAVAILABLE"

    # from_value normalization
    assert MeasurementSource.from_value("provider_exact") == MeasurementSource.PROVIDER_EXACT
    assert MeasurementSource.from_value("cli-exact") == MeasurementSource.CLI_EXACT
    assert MeasurementSource.from_value("quota_delta") == MeasurementSource.QUOTA_DELTA
    assert MeasurementSource.from_value("Text-Estimate") == MeasurementSource.TEXT_ESTIMATE
    assert MeasurementSource.from_value(MeasurementSource.DERIVED) == MeasurementSource.DERIVED

    with pytest.raises(ValueError, match="Unknown measurement source"):
        MeasurementSource.from_value("invalid_source_123")


def test_usage_event_creation_and_deterministic_id():
    """Verify UsageEvent fields, defaults, and deterministic hashing."""
    ev1 = UsageEvent(
        actor="codex",
        event_type="model_call",
        measurement_type="tokens",
        value=1500,
        unit="tokens",
        measurement_source=MeasurementSource.PROVIDER_EXACT,
        confidence=1.0,
        run_id="run-001",
        project_dir="D:/repos/bridge",
        timestamp="2026-08-23T12:00:00+00:00",
    )
    assert ev1.actor == "codex"
    assert ev1.value == 1500.0
    assert ev1.confidence == 1.0
    assert len(ev1.event_id) == 64  # SHA256 hex digest

    # Creating identical event yields identical event_id
    ev2 = UsageEvent(
        actor="codex",
        event_type="model_call",
        measurement_type="tokens",
        value=1500,
        unit="tokens",
        measurement_source=MeasurementSource.PROVIDER_EXACT,
        confidence=1.0,
        run_id="run-001",
        project_dir="D:/repos/bridge",
        timestamp="2026-08-23T12:00:00+00:00",
    )
    assert ev1.event_id == ev2.event_id


def test_usage_event_serialization_roundtrip():
    """Verify serialization to/from dict and JSON."""
    ev = UsageEvent(
        actor="agy",
        event_type="tool_execution",
        measurement_type="duration_seconds",
        value=3.456,
        unit="seconds",
        measurement_source=MeasurementSource.CLI_EXACT,
        confidence=0.95,
        metadata={"tool": "run_command", "exit_code": 0},
        run_id="run-42",
        project_dir="C:\\workspace\\proj",
        timestamp="2026-08-23T12:30:00+00:00",
    )

    d = ev.to_dict()
    assert d["actor"] == "agy"
    assert d["measurement_source"] == "CLI_EXACT"
    assert d["value"] == 3.456
    assert d["metadata"]["tool"] == "run_command"

    json_str = ev.to_json()
    assert isinstance(json_str, str)

    ev_from_json = UsageEvent.from_json(json_str)
    assert ev_from_json.event_id == ev.event_id
    assert ev_from_json.actor == ev.actor
    assert ev_from_json.value == ev.value
    assert ev_from_json.metadata == ev.metadata


def test_secret_safe_metadata_redaction():
    """Verify thorough redaction of passwords, tokens, API keys, cookies, and prompts."""
    raw_meta = {
        "user_id": "alice",
        "api_key": "sk-1234567890123456789012345",
        "password": "supersecretpassword",
        "nested": {
            "token": "ghp_1234567890abcdefghijklmnopqrstuvwx",
            "cookie": "session_id=xyz789;",
            "safe_counter": 42,
            "secret_note": "hidden",
            "authorization_header": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozG429n4_fZG8e1",
        },
        "user_prompt": "Please summarize this document containing confidential data.",
        "list_items": [
            {"access_token": "token_abc"},
            "https://admin:pass123@api.internal.com/v1",
            "Safe string value",
        ],
    }

    redacted = redact_metadata(raw_meta)

    assert redacted["user_id"] == "alice"
    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["password"] == "[REDACTED]"
    assert redacted["nested"]["token"] == "[REDACTED]"
    assert redacted["nested"]["cookie"] == "[REDACTED]"
    assert redacted["nested"]["safe_counter"] == 42
    assert redacted["nested"]["secret_note"] == "[REDACTED]"
    assert redacted["nested"]["authorization_header"] == "[REDACTED]"

    # Prompt is hashed for correlation without exposing content
    assert redacted["user_prompt"].startswith("[REDACTED_PROMPT_HASH:")
    assert len(redacted["user_prompt"]) > 25

    # Nested list checks
    assert redacted["list_items"][0]["access_token"] == "[REDACTED]"
    assert "pass123" not in redacted["list_items"][1]
    assert "[REDACTED_AUTH]" in redacted["list_items"][1]
    assert redacted["list_items"][2] == "Safe string value"


def test_unavailable_and_estimated_measurements():
    """Verify handling of UNAVAILABLE measurements and default confidences."""
    # When source is UNAVAILABLE, value becomes None and confidence becomes 0.0
    ev_unavail = UsageEvent(
        actor="system",
        event_type="quota_check",
        measurement_type="quota_remaining",
        value=50.0,
        unit="percent",
        measurement_source=MeasurementSource.UNAVAILABLE,
    )
    assert ev_unavail.value is None
    assert ev_unavail.confidence == 0.0
    assert ev_unavail.measurement_source == MeasurementSource.UNAVAILABLE

    # When value is None, measurement_source becomes UNAVAILABLE
    ev_none_val = UsageEvent(
        actor="system",
        event_type="quota_check",
        measurement_type="quota_remaining",
        value=None,
        unit="percent",
        measurement_source=MeasurementSource.PROVIDER_EXACT,
    )
    assert ev_none_val.measurement_source == MeasurementSource.UNAVAILABLE
    assert ev_none_val.confidence == 0.0

    # TEXT_ESTIMATE confidence default
    ev_est = UsageEvent(
        actor="codex",
        event_type="prompt_estimate",
        measurement_type="tokens",
        value=300,
        unit="tokens",
        measurement_source=MeasurementSource.TEXT_ESTIMATE,
    )
    assert ev_est.confidence == DEFAULT_SOURCE_CONFIDENCE[MeasurementSource.TEXT_ESTIMATE]
    assert ev_est.confidence == 0.6


def test_safe_mixed_unit_aggregation():
    """Verify that different units (tokens, seconds, usd, requests) are NEVER summed together."""
    events = [
        UsageEvent(actor="codex", event_type="call", measurement_type="tokens", value=1000, unit="tokens"),
        UsageEvent(actor="codex", event_type="call", measurement_type="tokens", value=500, unit="tokens"),
        UsageEvent(actor="codex", event_type="call", measurement_type="latency", value=2.5, unit="seconds"),
        UsageEvent(actor="codex", event_type="call", measurement_type="latency", value=1.5, unit="seconds"),
        UsageEvent(actor="agy", event_type="call", measurement_type="cost", value=0.04, unit="usd"),
        UsageEvent(actor="agy", event_type="call", measurement_type="cost", value=0.01, unit="usd"),
        UsageEvent(actor="agy", event_type="call", measurement_type="quota", value=None, unit="unknown", measurement_source=MeasurementSource.UNAVAILABLE),
    ]

    summary = aggregate_events(events)

    assert summary.event_count == 7
    assert summary.unavailable_count == 1

    # Totals by unit are strictly separated
    assert summary.total_for("tokens") == 1500.0
    assert summary.total_for("seconds") == 4.0
    assert summary.total_for("usd") == 0.05
    assert summary.total_for("requests") == 0.0

    assert summary.totals_by_unit == {
        "tokens": 1500.0,
        "seconds": 4.0,
        "usd": 0.05,
    }

    # Measurement type safe access
    assert summary.total_for_measurement("tokens") == 1500.0
    assert summary.total_for_measurement("latency") == 4.0
    assert summary.total_for_measurement("cost") == 0.05

    # Totals by actor
    assert summary.totals_by_actor["codex"]["tokens"] == 1500.0
    assert summary.totals_by_actor["codex"]["seconds"] == 4.0
    assert summary.totals_by_actor["agy"]["usd"] == 0.05


def test_mixed_incompatible_units_for_same_measurement_type():
    """Verify that attempting to sum incompatible units for a single measurement type fails closed."""
    events = [
        UsageEvent(actor="a", event_type="e", measurement_type="compute", value=100, unit="tokens"),
        UsageEvent(actor="a", event_type="e", measurement_type="compute", value=5, unit="seconds"),
    ]

    summary = aggregate_events(events)

    # Calling total_for_measurement without specifying unit must raise ValueError
    with pytest.raises(ValueError, match="Cannot safely aggregate mixed incompatible units"):
        summary.total_for_measurement("compute")

    # Explicit unit queries must succeed
    assert summary.total_for_measurement("compute", unit="tokens") == 100.0
    assert summary.total_for_measurement("compute", unit="seconds") == 5.0


def test_windows_path_normalization_and_comparison():
    """Verify Windows drive letter, case folding, and slash normalization."""
    p_win = "D:\\Software\\Repos\\bridge"
    p_posix = "d:/software/repos/bridge"
    p_trailing = "D:/Software/Repos/bridge/"
    p_extended = "\\\\?\\D:\\Software\\Repos\\bridge"
    p_dots = "D:\\Software\\.\\Repos\\..\\Repos\\bridge"
    p_unc = "\\\\server\\share\\folder"
    p_unc_posix = "//server/share/folder"

    norm_win = normalize_project_path(p_win)
    norm_posix = normalize_project_path(p_posix)
    norm_trailing = normalize_project_path(p_trailing)
    norm_extended = normalize_project_path(p_extended)
    norm_dots = normalize_project_path(p_dots)
    norm_unc = normalize_project_path(p_unc)
    norm_unc_posix = normalize_project_path(p_unc_posix)

    assert norm_win == norm_posix
    assert norm_win == norm_trailing
    assert norm_win == norm_extended
    assert norm_win == norm_dots
    assert norm_unc == norm_unc_posix
    assert paths_equal(p_win, p_posix)
    assert paths_equal(p_win, p_trailing)
    assert paths_equal(p_win, p_extended)
    assert paths_equal(p_win, p_dots)
    assert paths_equal(p_unc, p_unc_posix)
    assert not paths_equal(p_win, "D:/Other/Path")


def test_in_memory_usage_ledger_query_and_aggregation():
    """Verify in-memory UsageLedger operations, queries, and filters."""
    ledger = UsageLedger(in_memory=True)

    ev1 = ledger.record_event(
        actor="codex",
        event_type="model_call",
        measurement_type="tokens",
        value=500,
        unit="tokens",
        run_id="run-1",
        project_dir="D:\\project-alpha",
        timestamp="2026-08-23T10:00:00Z",
    )
    ev2 = ledger.record_event(
        actor="codex",
        event_type="model_call",
        measurement_type="tokens",
        value=700,
        unit="tokens",
        run_id="run-1",
        project_dir="D:\\project-alpha",
        timestamp="2026-08-23T11:00:00Z",
    )
    ev3 = ledger.record_event(
        actor="agy",
        event_type="tool_execution",
        measurement_type="tokens",
        value=300,
        unit="tokens",
        run_id="run-2",
        project_dir="d:/project-alpha/",
        timestamp="2026-08-23T12:00:00Z",
    )
    ev4 = ledger.record_event(
        actor="agy",
        event_type="tool_execution",
        measurement_type="tokens",
        value=400,
        unit="tokens",
        run_id="run-2",
        project_dir="C:\\project-beta",
        timestamp="2026-08-23T13:00:00Z",
    )

    # Per-run query
    run1_events = ledger.query(run_id="run-1")
    assert len(run1_events) == 2
    assert sum(e.value for e in run1_events) == 1200.0

    # Per-project aggregation (cross-platform path match)
    alpha_summary = ledger.aggregate(project_dir="d:/project-alpha")
    assert alpha_summary.event_count == 3
    assert alpha_summary.total_for("tokens") == 1500.0

    # Time-range query
    time_events = ledger.query(start_time="2026-08-23T10:30:00Z", end_time="2026-08-23T12:30:00Z")
    assert len(time_events) == 2
    assert [e.event_id for e in time_events] == [ev2.event_id, ev3.event_id]


def test_sqlite_persistence_lazy_schema_and_idempotency():
    """Verify SQLite persistence, lazy schema creation, and duplicate deduplication."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "telemetry.sqlite3"
        ledger = UsageLedger(db_path=db_path)

        # File should not be created immediately before any operation
        assert not db_path.exists()

        ev = UsageEvent(
            actor="codex",
            event_type="llm_call",
            measurement_type="tokens",
            value=250,
            unit="tokens",
            run_id="run-sqlite",
            project_dir=Path(tmp_dir) / "repo",
            timestamp="2026-08-23T14:00:00Z",
            metadata={"secret_token": "sk-1234567890123456789012345", "step": 1},
        )

        recorded1 = ledger.record(ev)
        assert db_path.exists()

        # Idempotent re-record with identical event_id
        recorded2 = ledger.record(ev)
        assert recorded1.event_id == recorded2.event_id

        # Query back from SQLite
        queried = ledger.query(run_id="run-sqlite")
        assert len(queried) == 1
        assert queried[0].event_id == ev.event_id
        assert queried[0].value == 250.0
        assert queried[0].metadata["secret_token"] == "[REDACTED]"
        assert queried[0].metadata["step"] == 1

        ledger.close()

        # Reopen with fresh ledger instance on same DB file
        fresh_ledger = UsageLedger(db_path=db_path)
        fresh_events = fresh_ledger.query(run_id="run-sqlite")
        assert len(fresh_events) == 1
        assert fresh_events[0].event_id == ev.event_id
        fresh_ledger.close()


def test_backward_compatibility_with_existing_durable_jobs_database():
    """Verify that UsageLedger coexists with existing DurableJobStore databases without corrupting schema_meta."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        shared_db_path = Path(tmp_dir) / "shared_jobs.sqlite3"

        # 1. Initialize DurableJobStore first
        job_store = DurableJobStore(db_path=shared_db_path)
        job_id = "job-telemetry-compat-001"
        now_iso = datetime.now(timezone.utc).isoformat()
        job_store.reserve_and_create(
            job_id=job_id,
            task_key=None,
            workdir=None,
            prompt_hash="dummy_prompt_hash",
            owner_session_id="session-001",
            now_iso=now_iso,
        )

        # 2. Attach UsageLedger to the SAME database file
        ledger = UsageLedger(db_path=shared_db_path)
        ev = ledger.record_event(
            actor="agy",
            event_type="job_completion",
            measurement_type="duration_seconds",
            value=12.5,
            unit="seconds",
            run_id=job_id,
        )
        assert ev is not None


        # 3. Verify DurableJobStore still works and schema_meta is intact
        raw_conn = sqlite3.connect(str(shared_db_path))
        cur = raw_conn.cursor()
        meta_row = cur.execute("SELECT value FROM schema_meta WHERE key='schema_version';").fetchone()
        assert meta_row is not None
        assert int(meta_row[0]) == SCHEMA_VERSION

        # Durable job read succeeds
        stored_job = job_store.get_job(job_id)
        assert stored_job is not None
        assert stored_job["job_id"] == job_id

        # UsageLedger read succeeds
        tel_events = ledger.query(run_id=job_id)
        assert len(tel_events) == 1
        assert tel_events[0].value == 12.5
        assert tel_events[0].unit == "seconds"

        raw_conn.close()
        ledger.close()


def test_fail_safe_append_mode():
    """Verify fail-safe mode suppresses disk/persistence errors while fail_safe=False raises."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "corrupt.sqlite3"
        ledger_failsafe = UsageLedger(db_path=db_path, fail_safe=True)

        # Record once to initialize
        ledger_failsafe.record_event(actor="codex", event_type="e", measurement_type="t", value=10, unit="tokens")

        # Corrupt / close underlying connection to simulate fatal error
        ledger_failsafe._conn.close()
        ledger_failsafe._conn = None
        # Make path invalid by pointing to a directory
        dir_path = Path(tmp_dir) / "directory_as_file"
        dir_path.mkdir()
        ledger_failsafe.db_path = dir_path

        # Fail-safe record should return None and not crash
        res = ledger_failsafe.record_event(actor="codex", event_type="e", measurement_type="t", value=20, unit="tokens")
        assert res is None

        # Fail-safe=False must raise
        ledger_strict = UsageLedger(db_path=dir_path, fail_safe=False)
        with pytest.raises(Exception):
            ledger_strict.record_event(actor="codex", event_type="e", measurement_type="t", value=20, unit="tokens")


def test_concurrency_safety_multi_threaded_writes_and_reads():
    """Verify thread-safety and SQLite WAL concurrency under multi-threaded load."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "concurrent_telemetry.sqlite3"
        ledger = UsageLedger(db_path=db_path)

        num_threads = 8
        events_per_thread = 25
        errors = []

        def worker(thread_idx: int):
            try:
                for i in range(events_per_thread):
                    ledger.record_event(
                        actor=f"worker-{thread_idx}",
                        event_type="concurrent_test",
                        measurement_type="requests",
                        value=1,
                        unit="requests",
                        run_id=f"run-{thread_idx}",
                        metadata={"thread_idx": thread_idx, "iter": i},
                    )
                    # Intermittent query
                    if i % 5 == 0:
                        ledger.query(actor=f"worker-{thread_idx}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Encountered concurrency errors: {errors}"

        total_summary = ledger.aggregate(event_type="concurrent_test")
        expected_total = num_threads * events_per_thread
        assert total_summary.event_count == expected_total
        assert total_summary.total_for("requests") == float(expected_total)

        ledger.close()


def test_invalid_numeric_values_raise():
    """Verify that NaN, Infinity, and non-numeric values are rejected with ValueError."""
    import math

    with pytest.raises(ValueError, match="Invalid numeric value"):
        UsageEvent(
            actor="codex",
            event_type="model_call",
            measurement_type="tokens",
            value=float("nan"),
            unit="tokens",
        )

    with pytest.raises(ValueError, match="Invalid numeric value"):
        UsageEvent(
            actor="codex",
            event_type="model_call",
            measurement_type="tokens",
            value=float("inf"),
            unit="tokens",
        )

    with pytest.raises(ValueError, match="Invalid numeric value"):
        UsageEvent(
            actor="codex",
            event_type="model_call",
            measurement_type="tokens",
            value="not-a-number",  # type: ignore
            unit="tokens",
        )


def test_weighted_confidence_calculation():
    """Verify weighted confidence calculation across different values and confidence levels."""
    events = [
        UsageEvent(
            actor="codex",
            event_type="model_call",
            measurement_type="tokens",
            value=100,
            unit="tokens",
            confidence=1.0,
        ),
        UsageEvent(
            actor="codex",
            event_type="prompt_estimate",
            measurement_type="tokens",
            value=100,
            unit="tokens",
            confidence=0.5,
        ),
    ]

    summary = aggregate_events(events)
    # Total tokens = 200
    # Weighted confidence = (100 * 1.0 + 100 * 0.5) / 200 = 150 / 200 = 0.75
    assert summary.weighted_confidence_by_unit["tokens"] == 0.75
    assert summary.mean_confidence == 0.75


def test_export_and_clear_operations():
    """Verify export_events and clear methods on UsageLedger."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "export_test.sqlite3"
        ledger = UsageLedger(db_path=db_path)

        ledger.record_event(
            actor="codex",
            event_type="step",
            measurement_type="tokens",
            value=50,
            unit="tokens",
            run_id="run-exp-1",
        )
        ledger.record_event(
            actor="agy",
            event_type="step",
            measurement_type="tokens",
            value=75,
            unit="tokens",
            run_id="run-exp-2",
        )

        exported = ledger.export_events(run_id="run-exp-1")
        assert len(exported) == 1
        assert exported[0]["run_id"] == "run-exp-1"
        assert exported[0]["value"] == 50.0

        # Clear
        ledger.clear()
        assert len(ledger.query()) == 0
        ledger.close()


def test_multi_filter_sqlite_and_in_memory_consistency():
    """Verify that querying with all filters returns identical results in memory and in SQLite."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "multi_filter.sqlite3"
        db_ledger = UsageLedger(db_path=db_path)
        mem_ledger = UsageLedger(in_memory=True)

        events_data = [
            ("codex", "call", "tokens", 100, "tokens", "PROVIDER_EXACT", "run-10", "D:/proj", "2026-08-23T10:00:00Z"),
            ("codex", "call", "tokens", 200, "tokens", "PROVIDER_EXACT", "run-10", "D:/proj", "2026-08-23T10:30:00Z"),
            ("codex", "call", "tokens", 300, "tokens", "PROVIDER_EXACT", "run-10", "D:/other", "2026-08-23T10:45:00Z"),
            ("agy", "exec", "tokens", 400, "tokens", "CLI_EXACT", "run-10", "D:/proj", "2026-08-23T11:00:00Z"),
            ("codex", "call", "seconds", 5, "seconds", "PROVIDER_EXACT", "run-10", "D:/proj", "2026-08-23T11:30:00Z"),
        ]

        for actor, ev_type, m_type, val, unit, src, rid, pdir, ts in events_data:
            ev = UsageEvent(
                actor=actor,
                event_type=ev_type,
                measurement_type=m_type,
                value=val,
                unit=unit,
                measurement_source=MeasurementSource.from_value(src),
                run_id=rid,
                project_dir=pdir,
                timestamp=ts,
            )
            db_ledger.record(ev)
            mem_ledger.record(ev)

        # Query with multi filters: actor="codex", unit="tokens", project_dir="d:/proj", time range
        db_res = db_ledger.query(
            actor="codex",
            unit="tokens",
            project_dir="D:\\proj",
            start_time="2026-08-23T09:00:00Z",
            end_time="2026-08-23T10:35:00Z",
        )
        mem_res = mem_ledger.query(
            actor="codex",
            unit="tokens",
            project_dir="D:\\proj",
            start_time="2026-08-23T09:00:00Z",
            end_time="2026-08-23T10:35:00Z",
        )

        assert len(db_res) == 2
        assert len(mem_res) == 2
        assert [e.event_id for e in db_res] == [e.event_id for e in mem_res]
        assert [e.value for e in db_res] == [100.0, 200.0]

        db_ledger.close()

def test_usage_event_task_id_deterministic_identity_and_serialization():
    """Verify task_id handling, deterministic event hashing, and serialization."""
    ev_with_task = UsageEvent(
        actor="codex",
        event_type="model_call",
        measurement_type="tokens",
        value=1200,
        unit="tokens",
        run_id="run-100",
        task_id="task-abc",
        project_dir="D:/repos/bridge",
        timestamp="2026-08-23T15:00:00+00:00",
    )
    assert ev_with_task.task_id == "task-abc"
    assert ev_with_task.to_dict()["task_id"] == "task-abc"

    # Serialization roundtrip
    json_data = ev_with_task.to_json()
    deserialized = UsageEvent.from_json(json_data)
    assert deserialized.task_id == "task-abc"
    assert deserialized.event_id == ev_with_task.event_id

    # Identical task_id produces identical event_id
    ev_identical = UsageEvent(
        actor="codex",
        event_type="model_call",
        measurement_type="tokens",
        value=1200,
        unit="tokens",
        run_id="run-100",
        task_id="task-abc",
        project_dir="D:/repos/bridge",
        timestamp="2026-08-23T15:00:00+00:00",
    )
    assert ev_identical.event_id == ev_with_task.event_id

    # Different task_id produces distinct event_id
    ev_diff_task = UsageEvent(
        actor="codex",
        event_type="model_call",
        measurement_type="tokens",
        value=1200,
        unit="tokens",
        run_id="run-100",
        task_id="task-xyz",
        project_dir="D:/repos/bridge",
        timestamp="2026-08-23T15:00:00+00:00",
    )
    assert ev_diff_task.event_id != ev_with_task.event_id

    # None task_id vs explicit task_id produces distinct event_id
    ev_no_task = UsageEvent(
        actor="codex",
        event_type="model_call",
        measurement_type="tokens",
        value=1200,
        unit="tokens",
        run_id="run-100",
        task_id=None,
        project_dir="D:/repos/bridge",
        timestamp="2026-08-23T15:00:00+00:00",
    )
    assert ev_no_task.task_id is None
    assert ev_no_task.event_id != ev_with_task.event_id

    # Whitespace task_id normalized to None
    ev_empty_task = UsageEvent(
        actor="codex",
        event_type="model_call",
        measurement_type="tokens",
        value=1200,
        unit="tokens",
        run_id="run-100",
        task_id="   ",
        project_dir="D:/repos/bridge",
        timestamp="2026-08-23T15:00:00+00:00",
    )
    assert ev_empty_task.task_id is None
    assert ev_empty_task.event_id == ev_no_task.event_id


def test_sqlite_backward_compatible_migration_missing_task_id():
    """Verify that an existing SQLite table missing task_id is migrated seamlessly without data loss."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "legacy_telemetry.sqlite3"

        # 1. Pre-create legacy table schema without task_id
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            CREATE TABLE telemetry_events (
                event_id TEXT PRIMARY KEY,
                run_id TEXT,
                project_dir TEXT,
                actor TEXT NOT NULL,
                event_type TEXT NOT NULL,
                measurement_type TEXT NOT NULL,
                value REAL,
                unit TEXT NOT NULL,
                measurement_source TEXT NOT NULL,
                confidence REAL NOT NULL,
                timestamp TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO telemetry_events VALUES (
                'legacy-event-001', 'run-old', 'd:/repo', 'codex', 'call',
                'tokens', 500.0, 'tokens', 'PROVIDER_EXACT', 1.0,
                '2026-08-23T10:00:00+00:00', '{}', '2026-08-23T10:00:00+00:00'
            );
            """
        )
        conn.commit()
        conn.close()

        # 2. Open UsageLedger on this legacy DB -> should trigger automatic migration
        ledger = UsageLedger(db_path=db_path)

        # 3. Query existing event
        legacy_events = ledger.query(run_id="run-old")
        assert len(legacy_events) == 1
        assert legacy_events[0].event_id == "legacy-event-001"
        assert legacy_events[0].task_id is None
        assert legacy_events[0].value == 500.0

        # 4. Record new event with task_id
        new_ev = ledger.record_event(
            actor="agy",
            event_type="tool_execution",
            measurement_type="duration_seconds",
            value=4.2,
            unit="seconds",
            run_id="run-new",
            task_id="task-migrated-01",
        )
        assert new_ev is not None
        assert new_ev.task_id == "task-migrated-01"

        # 5. Query by task_id
        task_events = ledger.query(task_id="task-migrated-01")
        assert len(task_events) == 1
        assert task_events[0].event_id == new_ev.event_id
        assert task_events[0].task_id == "task-migrated-01"
        assert task_events[0].value == 4.2

        # 6. Verify table columns and index in SQLite
        raw_conn = sqlite3.connect(str(db_path))
        cols = [r[1] for r in raw_conn.execute("PRAGMA table_info(telemetry_events);").fetchall()]
        assert "task_id" in cols
        indices = [r[1] for r in raw_conn.execute("PRAGMA index_list(telemetry_events);").fetchall()]
        assert "idx_telemetry_task_id" in indices
        raw_conn.close()

        ledger.close()


def test_task_id_record_query_aggregate_and_export_filters():
    """Verify record, query, aggregate, and export filtering with task_id across memory and SQLite."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "task_filters.sqlite3"
        db_ledger = UsageLedger(db_path=db_path)
        mem_ledger = UsageLedger(in_memory=True)

        for ledger in (db_ledger, mem_ledger):
            ledger.record_event(
                actor="codex",
                event_type="call",
                measurement_type="tokens",
                value=100,
                unit="tokens",
                run_id="run-1",
                task_id="task-A",
                timestamp="2026-08-23T12:00:00Z",
            )
            ledger.record_event(
                actor="codex",
                event_type="call",
                measurement_type="tokens",
                value=200,
                unit="tokens",
                run_id="run-1",
                task_id="task-A",
                timestamp="2026-08-23T12:05:00Z",
            )
            ledger.record_event(
                actor="agy",
                event_type="call",
                measurement_type="tokens",
                value=300,
                unit="tokens",
                run_id="run-1",
                task_id="task-B",
                timestamp="2026-08-23T12:10:00Z",
            )
            ledger.record_event(
                actor="agy",
                event_type="call",
                measurement_type="tokens",
                value=400,
                unit="tokens",
                run_id="run-2",
                task_id="task-B",
                timestamp="2026-08-23T12:15:00Z",
            )
            ledger.record_event(
                actor="codex",
                event_type="call",
                measurement_type="tokens",
                value=500,
                unit="tokens",
                run_id="run-2",
                task_id=None,
                timestamp="2026-08-23T12:20:00Z",
            )

        for ledger in (db_ledger, mem_ledger):
            # Query by task_id
            events_a = ledger.query(task_id="task-A")
            assert len(events_a) == 2
            assert sum(e.value for e in events_a) == 300.0
            assert all(e.task_id == "task-A" for e in events_a)

            events_b = ledger.query(task_id="task-B")
            assert len(events_b) == 2
            assert sum(e.value for e in events_b) == 700.0
            assert all(e.task_id == "task-B" for e in events_b)

            # Combined run_id and task_id
            events_run1_b = ledger.query(run_id="run-1", task_id="task-B")
            assert len(events_run1_b) == 1
            assert events_run1_b[0].value == 300.0

            # Aggregate by task_id
            summary_a = ledger.aggregate(task_id="task-A")
            assert summary_a.event_count == 2
            assert summary_a.total_for("tokens") == 300.0

            summary_b = ledger.aggregate(task_id="task-B")
            assert summary_b.event_count == 2
            assert summary_b.total_for("tokens") == 700.0

            # Export by task_id
            exported_a = ledger.export_events(task_id="task-A")
            assert len(exported_a) == 2
            assert all(d["task_id"] == "task-A" for d in exported_a)

        db_ledger.close()
