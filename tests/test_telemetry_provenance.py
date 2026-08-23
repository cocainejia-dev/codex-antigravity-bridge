"""Deterministic tests for Usage Telemetry Provenance and Origin tracking."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from contextlib import suppress
from pathlib import Path

import pytest

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

from codex_agy_bridge.telemetry import (
    EventOrigin,
    UsageEvent,
    UsageLedger,
    classify_telemetry_db,
    deterministic_json_dumps,
    evaluate_event_provenance,
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
)
from codex_agy_bridge.usage_cli import build_usage_report_data
from codex_agy_bridge.usage_reports import (
    validate_final_response_report_link,
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


def test_classify_telemetry_db_matrix():
    """Verify DB classification across in-memory, test, CI, and production ledger paths."""
    # 1. In-memory / empty paths
    assert classify_telemetry_db(None, origin=EventOrigin.TEST) == "TEST_LEDGER"
    assert classify_telemetry_db(":memory:", origin=EventOrigin.TEST) == "TEST_LEDGER"
    assert classify_telemetry_db(":memory:", origin=EventOrigin.CI) == "CI_LEDGER"
    assert classify_telemetry_db(":memory:", origin=EventOrigin.PRODUCTION) == "UNKNOWN_LEDGER"

    # 2. Test indicators in path
    assert classify_telemetry_db("d:/tmp/pytest-123/telemetry.db", origin=EventOrigin.PRODUCTION) == "TEST_LEDGER"
    assert classify_telemetry_db("c:/data/isolated_telemetry/run.db", origin=EventOrigin.PRODUCTION) == "TEST_LEDGER"
    assert classify_telemetry_db("test_isolated_telemetry.sqlite3", origin=EventOrigin.PRODUCTION) == "TEST_LEDGER"

    # 3. CI indicators in path or origin
    assert classify_telemetry_db("d:/repos/ci_telemetry.sqlite3", origin=EventOrigin.PRODUCTION) == "CI_LEDGER"
    assert classify_telemetry_db("d:/repos/app.sqlite3", origin=EventOrigin.CI) == "CI_LEDGER"

    # 4. Standard default production path
    prod_path = get_default_telemetry_db_path()
    assert classify_telemetry_db(prod_path, origin=EventOrigin.PRODUCTION) == "PRODUCTION_LEDGER"


def test_evaluate_event_provenance_matrix():
    """Verify provenance evaluation across pure production, mixed origins, and empty event sets."""
    # 1. Empty events -> NO_EVENTS
    eval_empty = evaluate_event_provenance([], expected_run_id="run-1", expected_origin=EventOrigin.PRODUCTION)
    assert eval_empty["confirmed"] is False
    assert eval_empty["classification"] == "NO_EVENTS"
    assert eval_empty["is_pure_production"] is False

    # 2. Pure production events matching expected run_id -> CONFIRMED_PRODUCTION
    ev1 = UsageEvent(
        actor="codex", event_type="call", measurement_type="tokens", value=100.0,
        unit="tokens", run_id="run-prod-1", origin=EventOrigin.PRODUCTION
    )
    ev2 = UsageEvent(
        actor="agy", event_type="completion", measurement_type="seconds", value=5.0,
        unit="seconds", run_id="run-prod-1", origin=EventOrigin.PRODUCTION
    )
    eval_prod = evaluate_event_provenance([ev1, ev2], expected_run_id="run-prod-1")
    assert eval_prod["confirmed"] is True
    assert eval_prod["classification"] == "CONFIRMED_PRODUCTION"
    assert eval_prod["is_pure_production"] is True
    assert eval_prod["exact_run_matched"] is True

    # 3. Pure production events with mismatched expected run_id
    eval_mismatch = evaluate_event_provenance([ev1, ev2], expected_run_id="other-run")
    assert eval_mismatch["confirmed"] is False
    assert eval_mismatch["exact_run_matched"] is False

    # 4. Mixed production and test events -> TEST_PROVENANCE
    ev_test = UsageEvent(
        actor="bridge", event_type="retry", measurement_type="retries", value=1.0,
        unit="count", run_id="run-prod-1", origin=EventOrigin.TEST
    )
    eval_mixed = evaluate_event_provenance([ev1, ev_test], expected_run_id="run-prod-1")
    assert eval_mixed["confirmed"] is False
    assert eval_mixed["classification"] == "TEST_PROVENANCE"
    assert eval_mixed["has_test_events"] is True

    # 5. CI events -> CI_PROVENANCE
    ev_ci = UsageEvent(
        actor="codex", event_type="call", measurement_type="tokens", value=50.0,
        unit="tokens", run_id="run-ci-1", origin=EventOrigin.CI
    )
    eval_ci = evaluate_event_provenance([ev_ci], expected_run_id="run-ci-1")
    assert eval_ci["confirmed"] is False
    assert eval_ci["classification"] == "CI_PROVENANCE"
    assert eval_ci["has_ci_events"] is True

    # 6. UNKNOWN origin events -> UNKNOWN_PROVENANCE
    ev_unk = UsageEvent(
        actor="codex", event_type="call", measurement_type="tokens", value=50.0,
        unit="tokens", run_id="run-unk-1", origin=EventOrigin.UNKNOWN
    )
    eval_unk = evaluate_event_provenance([ev_unk], expected_run_id="run-unk-1")
    assert eval_unk["confirmed"] is False
    assert eval_unk["classification"] == "UNKNOWN_PROVENANCE"


def test_build_usage_report_data_provenance_metadata():
    """Verify build_usage_report_data populates usage_report_* provenance metadata fields."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        ledger = None
        try:
            db_path = Path(tmp_dir) / "build_report.sqlite3"
            ledger = UsageLedger(db_path=db_path)

            ledger.record_event(
                actor="codex",
                event_type="call",
                measurement_type="tokens",
                value=150.0,
                unit="tokens",
                run_id="run-exact-001",
                origin=EventOrigin.PRODUCTION,
            )

            report_data = build_usage_report_data(
                ledger=ledger,
                run_id="run-exact-001",
                origin=EventOrigin.PRODUCTION,
            )

            assert report_data["usage_report_origin"] == "PRODUCTION"
            assert report_data["usage_report_run_id"] == "run-exact-001"
            assert "usage_report_db_classification" in report_data
            assert report_data["usage_report_event_provenance"] == "CONFIRMED_PRODUCTION"
            assert report_data["event_provenance_details"]["confirmed"] is True

            filters = report_data["filters"]
            assert filters["usage_report_origin"] == "PRODUCTION"
            assert filters["usage_report_run_id"] == "run-exact-001"
            assert filters["usage_report_event_provenance"] == "CONFIRMED_PRODUCTION"
        finally:
            if ledger is not None:
                ledger.close()
            reset_telemetry_ledgers()


def test_validate_final_response_report_link_rejects_test_and_ci(tmp_path: Path):
    """Verify fail-closed gating rejects TEST, CI, and test-isolated report origins."""
    report_file = tmp_path / "run-test-001.html"
    report_file.write_text("<html><body>Test Report</body></html>", encoding="utf-8")

    # 1. Reject TEST origin
    payload_test = {
        "run_id": "run-test-001",
        "usage_report_run_id": "run-test-001",
        "usage_report_status": "READY",
        "usage_report_origin": "TEST",
        "usage_report_db_classification": "TEST_LEDGER",
        "usage_report_event_provenance": "TEST_PROVENANCE",
        "usage_report_path": str(report_file),
        "usage_report_uri": report_file.resolve().as_uri(),
        "usage_report_reason": None,
    }
    res_test = validate_final_response_report_link(payload_test, supervisor_run_id="run-test-001")
    assert res_test.is_valid is False
    assert res_test.markdown_link is None
    assert "Rejected non-production usage report origin: 'TEST'" in (res_test.fail_closed_reason or "")

    # 2. Reject CI origin
    payload_ci = {
        "run_id": "run-ci-001",
        "usage_report_run_id": "run-ci-001",
        "usage_report_status": "READY",
        "usage_report_origin": "CI",
        "usage_report_db_classification": "CI_LEDGER",
        "usage_report_event_provenance": "CI_PROVENANCE",
        "usage_report_path": str(report_file),
        "usage_report_uri": report_file.resolve().as_uri(),
        "usage_report_reason": None,
    }
    res_ci = validate_final_response_report_link(payload_ci, supervisor_run_id="run-ci-001")
    assert res_ci.is_valid is False
    assert res_ci.markdown_link is None
    assert "Rejected non-production usage report origin: 'CI'" in (res_ci.fail_closed_reason or "")

    # 3. Reject non-production DB classification
    payload_bad_db = dict(payload_test)
    payload_bad_db["usage_report_origin"] = "PRODUCTION"
    payload_bad_db["usage_report_db_classification"] = "TEST_LEDGER"
    res_bad_db = validate_final_response_report_link(payload_bad_db, supervisor_run_id="run-test-001")
    assert res_bad_db.is_valid is False
    assert "Rejected non-production DB classification: 'TEST_LEDGER'" in (res_bad_db.fail_closed_reason or "")

    # 4. Reject unconfirmed event provenance
    payload_bad_prov = dict(payload_test)
    payload_bad_prov["usage_report_origin"] = "PRODUCTION"
    payload_bad_prov["usage_report_db_classification"] = "PRODUCTION_LEDGER"
    payload_bad_prov["usage_report_event_provenance"] = "TEST_PROVENANCE"
    res_bad_prov = validate_final_response_report_link(payload_bad_prov, supervisor_run_id="run-test-001")
    assert res_bad_prov.is_valid is False
    assert "Unconfirmed event provenance: 'TEST_PROVENANCE'" in (res_bad_prov.fail_closed_reason or "")

    # 5. Reject forbidden report paths containing test indicators
    forbidden_file = tmp_path / "isolated_telemetry_report.html"
    forbidden_file.write_text("<html></html>", encoding="utf-8")
    payload_forbidden_path = {
        "run_id": "run-prod-001",
        "usage_report_run_id": "run-prod-001",
        "usage_report_status": "READY",
        "usage_report_origin": "PRODUCTION",
        "usage_report_db_classification": "PRODUCTION_LEDGER",
        "usage_report_event_provenance": "CONFIRMED_PRODUCTION",
        "usage_report_path": str(forbidden_file),
        "usage_report_uri": forbidden_file.resolve().as_uri(),
        "usage_report_reason": None,
    }
    res_forbidden = validate_final_response_report_link(payload_forbidden_path, supervisor_run_id="run-prod-001")
    assert res_forbidden.is_valid is False
    assert "Rejected forbidden report path" in (res_forbidden.fail_closed_reason or "")


def test_validate_final_response_report_link_accepts_exact_production():
    """Verify exact production run report produces valid URI and clickable Markdown link."""
    # Create valid report file path in non-test mock reports directory
    mock_dir = Path(tempfile.gettempdir()) / f"codex_agy_mock_prod_{os.getpid()}"
    mock_dir.mkdir(parents=True, exist_ok=True)
    report_file = mock_dir / "run_prod_999.html"
    try:
        report_file.write_text("<!DOCTYPE html><html><body>Production Report</body></html>", encoding="utf-8")
        expected_uri = report_file.resolve().as_uri()

        payload = {
            "run_id": "run_prod_999",
            "usage_report_run_id": "run_prod_999",
            "usage_report_status": "READY",
            "usage_report_origin": "PRODUCTION",
            "usage_report_db_classification": "PRODUCTION_LEDGER",
            "usage_report_event_provenance": "CONFIRMED_PRODUCTION",
            "usage_report_path": str(report_file),
            "usage_report_uri": expected_uri,
            "usage_report_reason": None,
        }

        # Pass as dict
        res = validate_final_response_report_link(payload, supervisor_run_id="run_prod_999", label="Usage Report")
        assert res.is_valid is True
        assert res.report_path == str(report_file.resolve())
        assert res.report_uri == expected_uri
        assert res.markdown_link == f"[Usage Report]({expected_uri})"
        assert res.fail_closed_reason is None
        assert res.run_id == "run_prod_999"
        assert res.origin == "PRODUCTION"
        assert res.db_classification == "PRODUCTION_LEDGER"
        assert res.event_provenance == "CONFIRMED_PRODUCTION"

        # Pass as JSON string (MCP protocol output)
        json_str = deterministic_json_dumps(payload)
        res_from_json = validate_final_response_report_link(json_str, supervisor_run_id="run_prod_999")
        assert res_from_json.is_valid is True
        assert res_from_json.report_uri == expected_uri
        assert res_from_json.markdown_link == f"[Usage Report]({expected_uri})"

        # Verify URI matches Path.as_uri()
        assert res.report_uri == Path(res.report_path).resolve().as_uri()
        assert res.report_uri.startswith("file:///")
    finally:
        if report_file.exists():
            with suppress(OSError):
                report_file.unlink()
        if mock_dir.exists():
            with suppress(OSError):
                mock_dir.rmdir()


def test_validate_final_response_report_link_rejects_mismatched_run():
    """Verify mismatched run_id or report_run_id fails closed immediately."""
    mock_dir = Path(tempfile.gettempdir()) / f"codex_agy_mock_prod_{os.getpid()}"
    mock_dir.mkdir(parents=True, exist_ok=True)
    report_file = mock_dir / "run_001.html"
    try:
        report_file.write_text("<html></html>", encoding="utf-8")

        # Mismatch between payload run_id and supervisor_run_id
        payload1 = {
            "run_id": "run-A",
            "usage_report_run_id": "run-A",
            "usage_report_status": "READY",
            "usage_report_origin": "PRODUCTION",
            "usage_report_db_classification": "PRODUCTION_LEDGER",
            "usage_report_event_provenance": "CONFIRMED_PRODUCTION",
            "usage_report_path": str(report_file),
        }
        res1 = validate_final_response_report_link(payload1, supervisor_run_id="run-B")
        assert res1.is_valid is False
        assert "Mismatched run_id" in (res1.fail_closed_reason or "")

        # Mismatch between payload usage_report_run_id and supervisor_run_id
        payload2 = {
            "run_id": "run-B",
            "usage_report_run_id": "run-A",
            "usage_report_status": "READY",
            "usage_report_origin": "PRODUCTION",
            "usage_report_db_classification": "PRODUCTION_LEDGER",
            "usage_report_event_provenance": "CONFIRMED_PRODUCTION",
            "usage_report_path": str(report_file),
        }
        res2 = validate_final_response_report_link(payload2, supervisor_run_id="run-B")
        assert res2.is_valid is False
        assert "Mismatched report run_id" in (res2.fail_closed_reason or "")
    finally:
        if report_file.exists():
            with suppress(OSError):
                report_file.unlink()
        if mock_dir.exists():
            with suppress(OSError):
                mock_dir.rmdir()


def test_validate_final_response_report_link_rejects_missing_file_and_latest_alias():
    """Verify non-existent files and latest alias files are rejected."""
    mock_dir = Path(tempfile.gettempdir()) / f"codex_agy_mock_prod_{os.getpid()}"
    mock_dir.mkdir(parents=True, exist_ok=True)
    try:
        # 1. Missing file on disk
        missing_file = mock_dir / "does_not_exist.html"
        payload_missing = {
            "run_id": "run-100",
            "usage_report_run_id": "run-100",
            "usage_report_status": "READY",
            "usage_report_origin": "PRODUCTION",
            "usage_report_db_classification": "PRODUCTION_LEDGER",
            "usage_report_event_provenance": "CONFIRMED_PRODUCTION",
            "usage_report_path": str(missing_file),
        }
        res_missing = validate_final_response_report_link(payload_missing, supervisor_run_id="run-100")
        assert res_missing.is_valid is False
        assert "Usage report file does not exist on disk" in (res_missing.fail_closed_reason or "")

        # 2. Empty usage_report_path
        payload_empty_path = dict(payload_missing)
        payload_empty_path["usage_report_path"] = ""
        res_empty = validate_final_response_report_link(payload_empty_path, supervisor_run_id="run-100")
        assert res_empty.is_valid is False
        assert "Missing usage_report_path in payload" in (res_empty.fail_closed_reason or "")

        # 3. Latest alias path
        latest_file = mock_dir / "latest.html"
        latest_file.write_text("<html></html>", encoding="utf-8")
        payload_latest = dict(payload_missing)
        payload_latest["usage_report_path"] = str(latest_file)
        res_latest = validate_final_response_report_link(payload_latest, supervisor_run_id="run-100")
        assert res_latest.is_valid is False
        assert "Rejected latest alias path" in (res_latest.fail_closed_reason or "")

        # 4. Forbidden visualization path
        viz_file = mock_dir / ".codex" / "visualizations" / "run-100.html"
        viz_file.parent.mkdir(parents=True, exist_ok=True)
        viz_file.write_text("<html></html>", encoding="utf-8")
        payload_viz = dict(payload_missing)
        payload_viz["usage_report_path"] = str(viz_file)
        res_viz = validate_final_response_report_link(payload_viz, supervisor_run_id="run-100")
        assert res_viz.is_valid is False
        assert "Rejected forbidden report path containing '.codex/visualizations'" in (res_viz.fail_closed_reason or "")
    finally:
        if mock_dir.exists():
            import shutil
            shutil.rmtree(mock_dir, ignore_errors=True)


def test_validate_final_response_report_link_rejects_failed_status_and_invalid_payload():
    """Verify FAILED status, invalid JSON, and missing supervisor_run_id fail closed."""
    # 1. FAILED status
    payload_failed = {
        "run_id": "run-fail",
        "usage_report_run_id": "run-fail",
        "usage_report_status": "FAILED",
        "usage_report_reason": "Failed to generate usage report: timeout",
        "usage_report_path": None,
    }
    res_failed = validate_final_response_report_link(payload_failed, supervisor_run_id="run-fail")
    assert res_failed.is_valid is False
    assert "Report status not READY: Failed to generate usage report: timeout" in (res_failed.fail_closed_reason or "")

    # 2. Malformed JSON string
    res_bad_json = validate_final_response_report_link("{malformed", supervisor_run_id="run-1")
    assert res_bad_json.is_valid is False
    assert "JSON decode error" in (res_bad_json.fail_closed_reason or "")

    # 3. Invalid payload type
    res_bad_type = validate_final_response_report_link(12345, supervisor_run_id="run-1")  # type: ignore
    assert res_bad_type.is_valid is False
    assert "Invalid run_result payload type" in (res_bad_type.fail_closed_reason or "")

    # 4. Missing supervisor_run_id
    res_no_sup = validate_final_response_report_link(payload_failed, supervisor_run_id="")
    assert res_no_sup.is_valid is False
    assert "Missing or empty supervisor_run_id" in (res_no_sup.fail_closed_reason or "")


def test_server_run_result_metadata_wiring_and_failed_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify server.run_result attaches provenance metadata and preserves FAILED isolation."""
    from codex_agy_bridge import server as server_mod
    from codex_agy_bridge.contracts import RunState, TaskContract
    from codex_agy_bridge.run_control import DurableRunManager

    db_file = tmp_path / "server_test.sqlite3"
    manager = DurableRunManager(str(db_file))

    task = TaskContract(
        task_id="task-srv-001",
        objective="Test server run_result wiring",
        base_head="0123456789abcdef",
        workdir=str(tmp_path),
        allowed_paths=["src/test.py"],
        forbidden_paths=[],
        acceptance_criteria=["Passes"],
    )
    initial = manager.run_start(task, run_id="run-srv-001", auto_spawn=False)
    manager.store.transition_run(initial.run_id, expected_version=1, target_state=RunState.QUEUED)
    manager.store.transition_run(initial.run_id, expected_version=2, target_state=RunState.RUNNING)
    manager.store.transition_run(initial.run_id, expected_version=3, target_state=RunState.VERIFYING)
    manager.store.transition_run(
        initial.run_id,
        expected_version=4,
        target_state=RunState.COMPLETE,
        result_summary="Success",
        verification_result={"passed": True, "status": "passed", "returncode": 0},
    )

    # 1. Normal execution of server.run_result
    res_json = server_mod.run_result(str(db_file), "run-srv-001")
    payload = json.loads(res_json)

    assert payload["run_id"] == "run-srv-001"
    assert payload["usage_report_status"] == "READY"
    assert payload["usage_report_run_id"] == "run-srv-001"
    assert "usage_report_path" in payload
    assert "usage_report_uri" in payload
    assert "usage_report_origin" in payload
    assert "usage_report_db_classification" in payload
    assert "usage_report_event_provenance" in payload

    # Since we are running in pytest, the ambient origin is TEST and DB is TEST_LEDGER
    assert payload["usage_report_origin"] == "TEST"
    assert payload["usage_report_db_classification"] == "TEST_LEDGER"

    # Gating check on this test payload must fail closed!
    gate_res = validate_final_response_report_link(payload, supervisor_run_id="run-srv-001")
    assert gate_res.is_valid is False
    assert gate_res.markdown_link is None

    # 2. Simulated failure in report generation -> preserves FAILED isolation
    def failing_write(*args, **kwargs):
        raise OSError("Simulated disk error during report writing")

    monkeypatch.setattr("codex_agy_bridge.usage_reports.write_stable_report", failing_write)

    res_fail_json = server_mod.run_result(str(db_file), "run-srv-001")
    payload_fail = json.loads(res_fail_json)

    assert payload_fail["usage_report_status"] == "FAILED"
    assert payload_fail["usage_report_path"] is None
    assert payload_fail["usage_report_uri"] is None
    assert "Simulated disk error" in payload_fail["usage_report_reason"]
    assert payload_fail["usage_report_origin"] is None
    assert payload_fail["usage_report_run_id"] == "run-srv-001"
    assert payload_fail["usage_report_db_classification"] is None
    assert payload_fail["usage_report_event_provenance"] is None

    gate_fail_res = validate_final_response_report_link(payload_fail, supervisor_run_id="run-srv-001")
    assert gate_fail_res.is_valid is False
    assert gate_fail_res.markdown_link is None
    assert "Report status not READY" in (gate_fail_res.fail_closed_reason or "")
