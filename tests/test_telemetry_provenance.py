"""Deterministic tests for Usage Telemetry Provenance and Origin tracking."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from typing import Any

# Ensure package import from mcp-antigravity-bridge/src
SRC = Path(__file__).resolve().parents[1] / "mcp-antigravity-bridge" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:
    import codex_agy_bridge
    pkg_dir = str(SRC / "codex_agy_bridge")
    if pkg_dir not in codex_agy_bridge.__path__:
        codex_agy_bridge.__path__.insert(0, pkg_dir)
except ImportError:
    pass

import pytest

from codex_agy_bridge.telemetry import (
    DEFAULT_SOURCE_CONFIDENCE,
    EventOrigin,
    MeasurementSource,
    UsageEvent,
    UsageLedger,
    UsageSummary,
    aggregate_events,
    compute_event_id,
    deterministic_json_dumps,
    get_default_telemetry_db_path,
    resolve_default_origin,
)
from codex_agy_bridge.telemetry_hooks import (
    get_telemetry_ledger,
    record_account_switch_event,
    record_agy_job_completion_event,
    record_agy_job_start_event,
    record_avoided_duplicate_retry_event,
    record_duplicate_quota_risk_event,
    record_oneshot_call_event,
    record_reconciliation_event,
    record_retry_event,
    record_run_resume_event,
    record_run_start_event,
    record_timeout_event,
    record_worker_completion_event,
    record_worker_launch_event,
    reset_telemetry_ledgers,
    telemetry_path_for,
)


@pytest.fixture(autouse=True)
def _reset_ledgers_per_test():
    """Ensure all ledger SQLite connections are cleanly closed before and after each test."""
    reset_telemetry_ledgers()
    yield
    reset_telemetry_ledgers()


def test_event_origin_enum_and_normalization():
    """Verify EventOrigin enum values and robust from_value parsing."""
    assert EventOrigin.PRODUCTION.value == "PRODUCTION"
    assert EventOrigin.TEST.value == "TEST"
    assert EventOrigin.CI.value == "CI"
    assert EventOrigin.UNKNOWN.value == "UNKNOWN"

    # Case-insensitive normalization
    assert EventOrigin.from_value("production") == EventOrigin.PRODUCTION
    assert EventOrigin.from_value("Production") == EventOrigin.PRODUCTION
    assert EventOrigin.from_value("PRODUCTION") == EventOrigin.PRODUCTION
    assert EventOrigin.from_value("test") == EventOrigin.TEST
    assert EventOrigin.from_value("Test") == EventOrigin.TEST
    assert EventOrigin.from_value("ci") == EventOrigin.CI
    assert EventOrigin.from_value("CI") == EventOrigin.CI
    assert EventOrigin.from_value("unknown") == EventOrigin.UNKNOWN
    assert EventOrigin.from_value(EventOrigin.CI) == EventOrigin.CI

    # Fallback to UNKNOWN for unrecognized/invalid values
    assert EventOrigin.from_value(None) == EventOrigin.UNKNOWN
    assert EventOrigin.from_value("") == EventOrigin.UNKNOWN
    assert EventOrigin.from_value("invalid_origin") == EventOrigin.UNKNOWN
    assert EventOrigin.from_value(12345) == EventOrigin.UNKNOWN


def test_resolve_default_origin_resolution_order(monkeypatch: pytest.MonkeyPatch):
    """Verify origin resolution hierarchy: explicit arg > CODEX_AGY_TELEMETRY_ORIGIN > pytest > CI > PRODUCTION."""
    # 1. Explicit argument always wins
    assert resolve_default_origin(EventOrigin.PRODUCTION) == EventOrigin.PRODUCTION
    assert resolve_default_origin("ci") == EventOrigin.CI
    assert resolve_default_origin("test") == EventOrigin.TEST

    # 2. CODEX_AGY_TELEMETRY_ORIGIN environment variable takes precedence over ambient env
    monkeypatch.setenv("CODEX_AGY_TELEMETRY_ORIGIN", "PRODUCTION")
    assert resolve_default_origin() == EventOrigin.PRODUCTION
    monkeypatch.setenv("CODEX_AGY_TELEMETRY_ORIGIN", "CI")
    assert resolve_default_origin() == EventOrigin.CI
    monkeypatch.setenv("CODEX_AGY_TELEMETRY_ORIGIN", "TEST")
    assert resolve_default_origin() == EventOrigin.TEST

    # 3. Ambient pytest environment detection when env var is unset
    monkeypatch.delenv("CODEX_AGY_TELEMETRY_ORIGIN", raising=False)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "test_foo")
    assert resolve_default_origin() == EventOrigin.TEST

    # 4. Ambient CI environment detection when not running under pytest
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("PYTEST_VERSION", raising=False)
    monkeypatch.setenv("CI", "true")
    assert resolve_default_origin() == EventOrigin.CI

    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert resolve_default_origin() == EventOrigin.CI

    # 5. Default fallback to PRODUCTION when all test and CI markers are cleared
    for k in ("GITHUB_ACTIONS", "GITLAB_CI", "TRAVIS", "CIRCLECI", "BITBUCKET_COMMIT", "TF_BUILD", "BUILD_BUILDID"):
        monkeypatch.delenv(k, raising=False)
    assert resolve_default_origin() == EventOrigin.PRODUCTION


def test_usage_event_deterministic_event_id_with_origin():
    """Verify event ID hashing incorporates non-UNKNOWN origins while isolating cross-origin events."""
    ev_prod = UsageEvent(
        actor="codex",
        event_type="call",
        measurement_type="tokens",
        value=100.0,
        unit="tokens",
        timestamp="2026-08-23T12:00:00Z",
        origin=EventOrigin.PRODUCTION,
    )
    ev_test = UsageEvent(
        actor="codex",
        event_type="call",
        measurement_type="tokens",
        value=100.0,
        unit="tokens",
        timestamp="2026-08-23T12:00:00Z",
        origin=EventOrigin.TEST,
    )
    ev_ci = UsageEvent(
        actor="codex",
        event_type="call",
        measurement_type="tokens",
        value=100.0,
        unit="tokens",
        timestamp="2026-08-23T12:00:00Z",
        origin=EventOrigin.CI,
    )
    ev_unk = UsageEvent(
        actor="codex",
        event_type="call",
        measurement_type="tokens",
        value=100.0,
        unit="tokens",
        timestamp="2026-08-23T12:00:00Z",
        origin=EventOrigin.UNKNOWN,
    )

    # Different origins produce different hashes
    assert ev_prod.event_id != ev_test.event_id
    assert ev_prod.event_id != ev_ci.event_id
    assert ev_test.event_id != ev_ci.event_id
    assert ev_unk.event_id != ev_prod.event_id

    # Identical origin reproduces exact same event_id
    ev_prod2 = UsageEvent(
        actor="codex",
        event_type="call",
        measurement_type="tokens",
        value=100.0,
        unit="tokens",
        timestamp="2026-08-23T12:00:00Z",
        origin=EventOrigin.PRODUCTION,
    )
    assert ev_prod.event_id == ev_prod2.event_id


def test_usage_event_dict_and_json_serialization_provenance():
    """Verify origin serialization roundtrip and defaults for dictionary/JSON representations."""
    ev = UsageEvent(
        actor="agy",
        event_type="completion",
        measurement_type="duration_seconds",
        value=2.5,
        unit="seconds",
        origin=EventOrigin.PRODUCTION,
    )
    d = ev.to_dict()
    assert d["origin"] == "PRODUCTION"

    j = ev.to_json()
    assert '"origin":"PRODUCTION"' in j

    # Reconstruct from dict
    restored = UsageEvent.from_dict(d)
    assert restored.origin == EventOrigin.PRODUCTION
    assert restored.event_id == ev.event_id

    # Missing origin in dict defaults to UNKNOWN
    d_no_origin = dict(d)
    del d_no_origin["origin"]
    restored_legacy = UsageEvent.from_dict(d_no_origin)
    assert restored_legacy.origin == EventOrigin.UNKNOWN

    # None origin in dict defaults to UNKNOWN
    d_null_origin = dict(d)
    d_null_origin["origin"] = None
    restored_null = UsageEvent.from_dict(d_null_origin)
    assert restored_null.origin == EventOrigin.UNKNOWN


def test_legacy_sqlite_row_migration_reads_unknown_origin():
    """Verify legacy SQLite tables without 'origin' column migrate safely and read old rows as UNKNOWN."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        ledger = None
        try:
            db_path = Path(tmp_dir) / "legacy_provenance.sqlite3"

            # 1. Create legacy schema without origin column
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
                    'legacy-row-001', 'run-old-1', 'd:/repo', 'codex', 'model_call',
                    'tokens', 500.0, 'tokens', 'PROVIDER_EXACT', 1.0,
                    '2026-08-23T10:00:00+00:00', '{}', '2026-08-23T10:00:00+00:00'
                );
                """
            )
            conn.commit()
            conn.close()

            # 2. Open ledger to trigger automatic migration
            ledger = UsageLedger(db_path=db_path)

            # 3. Query existing legacy row -> origin must be EventOrigin.UNKNOWN
            events = ledger.query(run_id="run-old-1")
            assert len(events) == 1
            assert events[0].event_id == "legacy-row-001"
            assert events[0].origin == EventOrigin.UNKNOWN
            assert events[0].value == 500.0

            # 4. Insert another row with NULL origin via raw SQL
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                """
                INSERT INTO telemetry_events (
                    event_id, run_id, project_dir, actor, event_type,
                    measurement_type, value, unit, measurement_source,
                    confidence, origin, timestamp, metadata_json, created_at
                ) VALUES (
                    'legacy-row-002', 'run-old-2', 'd:/repo', 'agy', 'exec',
                    'seconds', 10.0, 'seconds', 'CLI_EXACT',
                    1.0, NULL, '2026-08-23T10:05:00+00:00', '{}', '2026-08-23T10:05:00+00:00'
                );
                """
            )
            conn.execute(
                """
                INSERT INTO telemetry_events (
                    event_id, run_id, project_dir, actor, event_type,
                    measurement_type, value, unit, measurement_source,
                    confidence, origin, timestamp, metadata_json, created_at
                ) VALUES (
                    'legacy-row-003', 'run-old-3', 'd:/repo', 'codex', 'edit',
                    'lines', 15.0, 'lines', 'CLI_EXACT',
                    1.0, '', '2026-08-23T10:06:00+00:00', '{}', '2026-08-23T10:06:00+00:00'
                );
                """
            )
            conn.commit()
            conn.close()

            events2 = ledger.query(run_id="run-old-2")
            assert len(events2) == 1
            assert events2[0].origin == EventOrigin.UNKNOWN

            events3 = ledger.query(run_id="run-old-3")
            assert len(events3) == 1
            assert events3[0].origin == EventOrigin.UNKNOWN

            # 5. Record new event with explicit origin
            new_ev = ledger.record_event(
                actor="codex",
                event_type="call",
                measurement_type="tokens",
                value=300.0,
                unit="tokens",
                run_id="run-new",
                origin=EventOrigin.PRODUCTION,
            )
            assert new_ev is not None
            assert new_ev.origin == EventOrigin.PRODUCTION

            # 6. Filter by origin
            unknown_events = ledger.query(origin=EventOrigin.UNKNOWN)
            assert len(unknown_events) == 3
            assert {e.event_id for e in unknown_events} == {"legacy-row-001", "legacy-row-002", "legacy-row-003"}

            prod_events = ledger.query(origin=EventOrigin.PRODUCTION)
            assert len(prod_events) == 1
            assert prod_events[0].event_id == new_ev.event_id

            multi_events = ledger.query(origin=[EventOrigin.UNKNOWN, EventOrigin.PRODUCTION])
            assert len(multi_events) == 4
        finally:
            if ledger is not None:
                ledger.close()
            reset_telemetry_ledgers()


def test_ledger_query_and_aggregation_by_origin():
    """Verify origin-based filtering and aggregation in both in-memory and SQLite ledgers."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_ledger = None
        try:
            db_path = Path(tmp_dir) / "provenance_filters.sqlite3"
            db_ledger = UsageLedger(db_path=db_path)
            mem_ledger = UsageLedger(in_memory=True)

            for ledger in (db_ledger, mem_ledger):
                # Production events
                ledger.record_event(
                    actor="codex", event_type="call", measurement_type="tokens", value=100.0,
                    unit="tokens", origin=EventOrigin.PRODUCTION, timestamp="2026-08-23T12:00:00Z"
                )
                ledger.record_event(
                    actor="agy", event_type="call", measurement_type="tokens", value=200.0,
                    unit="tokens", origin=EventOrigin.PRODUCTION, timestamp="2026-08-23T12:01:00Z"
                )
                # Test events
                ledger.record_event(
                    actor="codex", event_type="call", measurement_type="tokens", value=50.0,
                    unit="tokens", origin=EventOrigin.TEST, timestamp="2026-08-23T12:02:00Z"
                )
                ledger.record_event(
                    actor="agy", event_type="call", measurement_type="tokens", value=60.0,
                    unit="tokens", origin=EventOrigin.TEST, timestamp="2026-08-23T12:03:00Z"
                )
                ledger.record_event(
                    actor="bridge", event_type="retry", measurement_type="retries", value=1.0,
                    unit="count", origin=EventOrigin.TEST, timestamp="2026-08-23T12:04:00Z"
                )
                # CI event
                ledger.record_event(
                    actor="codex", event_type="call", measurement_type="tokens", value=75.0,
                    unit="tokens", origin=EventOrigin.CI, timestamp="2026-08-23T12:05:00Z"
                )
                # Unknown event
                ledger.record_event(
                    actor="codex", event_type="call", measurement_type="tokens", value=25.0,
                    unit="tokens", origin=EventOrigin.UNKNOWN, timestamp="2026-08-23T12:06:00Z"
                )

            for ledger in (db_ledger, mem_ledger):
                # Query by single origin
                prod_events = ledger.query(origin=EventOrigin.PRODUCTION)
                assert len(prod_events) == 2
                assert sum(e.value for e in prod_events) == 300.0

                test_events = ledger.query(origin=EventOrigin.TEST)
                assert len(test_events) == 3

                ci_events = ledger.query(origin=EventOrigin.CI)
                assert len(ci_events) == 1
                assert ci_events[0].value == 75.0

                unk_events = ledger.query(origin=EventOrigin.UNKNOWN)
                assert len(unk_events) == 1
                assert unk_events[0].value == 25.0

                # Query by multiple origins
                prod_or_ci = ledger.query(origin=[EventOrigin.PRODUCTION, EventOrigin.CI])
                assert len(prod_or_ci) == 3

                # Aggregate by origin
                summary_prod = ledger.aggregate(origin=EventOrigin.PRODUCTION)
                assert summary_prod.event_count == 2
                assert summary_prod.total_for("tokens") == 300.0
                assert summary_prod.events_by_origin == {"PRODUCTION": 2}

                # Overall aggregate
                summary_all = ledger.aggregate()
                assert summary_all.event_count == 7
                assert summary_all.events_by_origin == {
                    "PRODUCTION": 2,
                    "TEST": 3,
                    "CI": 1,
                    "UNKNOWN": 1,
                }

                # Export events filtered by origin
                exported_ci = ledger.export_events(origin=EventOrigin.CI)
                assert len(exported_ci) == 1
                assert exported_ci[0]["origin"] == "CI"
        finally:
            if db_ledger is not None:
                db_ledger.close()
            reset_telemetry_ledgers()


def test_telemetry_fixture_routes_away_from_production_db():
    """Verify test fixture auto_isolate_telemetry routes to TEST origin and isolates DB from production path."""
    prod_path = get_default_telemetry_db_path()
    configured_db_env = os.environ.get("CODEX_AGY_TELEMETRY_DB")
    configured_origin_env = os.environ.get("CODEX_AGY_TELEMETRY_ORIGIN")

    # Fixture must set CODEX_AGY_TELEMETRY_ORIGIN to TEST
    assert configured_origin_env == "TEST"

    # Fixture must set CODEX_AGY_TELEMETRY_DB to an isolated path
    assert configured_db_env is not None
    assert Path(configured_db_env).resolve() != prod_path.resolve()

    # Telemetry hooks without explicit db_path or origin must record to test DB with origin TEST
    ev_start = record_run_start_event(run_id="test-fixture-run", task_id="task-fixture-01")
    assert ev_start is not None
    assert ev_start.origin == EventOrigin.TEST

    ledger = get_telemetry_ledger()
    assert Path(ledger.db_path).resolve() == Path(configured_db_env).resolve()

    events = ledger.query(run_id="test-fixture-run")
    assert len(events) >= 1
    assert all(e.origin == EventOrigin.TEST for e in events)


def test_telemetry_hooks_explicit_origin_passthrough():
    """Verify all instrumentation hooks accept and record explicit origin parameter."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            db_path = Path(tmp_dir) / "hooks_origin.sqlite3"

            # 1. record_run_start_event
            ev1 = record_run_start_event(
                run_id="r-1", task_id="t-1", db_path=db_path, origin=EventOrigin.PRODUCTION
            )
            assert ev1 is not None and ev1.origin == EventOrigin.PRODUCTION

            # 2. record_worker_launch_event
            ev2 = record_worker_launch_event(
                run_id="r-1", task_id="t-1", db_path=db_path, origin="ci"
            )
            assert ev2 is not None and ev2.origin == EventOrigin.CI

            # 3. record_worker_completion_event
            evs3 = record_worker_completion_event(
                run_id="r-1", task_id="t-1", success=True, duration_seconds=5.0,
                db_path=db_path, origin=EventOrigin.PRODUCTION
            )
            assert len(evs3) >= 1
            assert all(e.origin == EventOrigin.PRODUCTION for e in evs3)

            # 4. record_timeout_event
            ev4 = record_timeout_event(
                run_id="r-1", task_id="t-1", timeout_class="LOCAL_SUPERVISION_TIMEOUT",
                db_path=db_path, origin=EventOrigin.CI
            )
            assert ev4 is not None and ev4.origin == EventOrigin.CI

            # 5. record_account_switch_event
            ev5 = record_account_switch_event(
                run_id="r-1", task_id="t-1", reason="quota", db_path=db_path, origin=EventOrigin.PRODUCTION
            )
            assert ev5 is not None and ev5.origin == EventOrigin.PRODUCTION

            # 6. record_run_resume_event
            ev6 = record_run_resume_event(
                run_id="r-1", task_id="t-1", db_path=db_path, origin=EventOrigin.PRODUCTION
            )
            assert ev6 is not None and ev6.origin == EventOrigin.PRODUCTION

            # 7. record_reconciliation_event
            ev7 = record_reconciliation_event(
                run_id="r-1", task_id="t-1", action="reconcile", db_path=db_path, origin="ci"
            )
            assert ev7 is not None and ev7.origin == EventOrigin.CI

            # 8. record_retry_event
            ev8 = record_retry_event(
                run_id="r-1", task_id="t-1", attempt=1, db_path=db_path, origin=EventOrigin.PRODUCTION
            )
            assert ev8 is not None and ev8.origin == EventOrigin.PRODUCTION

            # 9. record_agy_job_start_event & completion
            ev9 = record_agy_job_start_event(
                job_id="job-1", task_key="tk", db_path=db_path, origin=EventOrigin.PRODUCTION
            )
            assert ev9 is not None and ev9.origin == EventOrigin.PRODUCTION

            evs10 = record_agy_job_completion_event(
                job_id="job-1", exit_code=0, elapsed_seconds=1.2,
                db_path=db_path, origin=EventOrigin.PRODUCTION
            )
            assert len(evs10) >= 1
            assert all(e.origin == EventOrigin.PRODUCTION for e in evs10)

            # 10. record_oneshot_call_event
            evs11 = record_oneshot_call_event(
                prompt="agy_ask", exit_code=0, duration_seconds=0.8,
                db_path=db_path, origin=EventOrigin.PRODUCTION
            )
            assert len(evs11) >= 1
            assert all(e.origin == EventOrigin.PRODUCTION for e in evs11)

            # 11. record_duplicate_quota_risk_event & avoided retry
            ev12 = record_duplicate_quota_risk_event(
                run_id="r-1", task_id="t-1", db_path=db_path, origin=EventOrigin.PRODUCTION
            )
            assert ev12 is not None and ev12.origin == EventOrigin.PRODUCTION

            ev13 = record_avoided_duplicate_retry_event(
                run_id="r-1", task_id="t-1", db_path=db_path, origin=EventOrigin.PRODUCTION
            )
            assert ev13 is not None and ev13.origin == EventOrigin.PRODUCTION
        finally:
            reset_telemetry_ledgers()


def test_existing_telemetry_compatibility():
    """Verify that omitting origin maintains backward-compatible behavior with no breaking changes."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        ledger = None
        try:
            db_path = Path(tmp_dir) / "compat.sqlite3"
            ledger = UsageLedger(db_path=db_path)

            # Record with standard legacy parameters (no origin keyword arg)
            ev = ledger.record_event(
                actor="codex",
                event_type="call",
                measurement_type="tokens",
                value=100.0,
                unit="tokens",
                run_id="legacy-call",
            )
            assert ev is not None
            assert isinstance(ev.origin, EventOrigin)

            summary = ledger.aggregate()
            assert summary.event_count == 1
            assert summary.total_for("tokens") == 100.0

            exported = ledger.export_events()
            assert len(exported) == 1
            assert "origin" in exported[0]
        finally:
            if ledger is not None:
                ledger.close()
            reset_telemetry_ledgers()
