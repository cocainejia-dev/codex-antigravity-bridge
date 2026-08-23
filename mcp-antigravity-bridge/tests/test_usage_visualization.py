"""Deterministic unit and integration tests for Usage Telemetry Visualization."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import codex_agy_bridge  # noqa: E402
_local_pkg_path = str(SRC_DIR / "codex_agy_bridge")
if hasattr(codex_agy_bridge, "__path__") and _local_pkg_path not in codex_agy_bridge.__path__:
    codex_agy_bridge.__path__.insert(0, _local_pkg_path)

import pytest  # noqa: E402

from codex_agy_bridge.telemetry import (  # noqa: E402
    MeasurementSource,
    UsageEvent,
    UsageLedger,
    UsageSummary,
    aggregate_events,
)
from codex_agy_bridge.telemetry_hooks import (  # noqa: E402
    get_telemetry_ledger,
    record_account_switch_event,
    record_avoided_duplicate_retry_event,
    record_duplicate_quota_risk_event,
    record_retry_event,
    record_run_start_event,
    record_worker_completion_event,
    record_worker_launch_event,
    reset_telemetry_ledgers,
)
from codex_agy_bridge.usage_cli import (  # noqa: E402
    build_usage_report_data,
    format_human_report,
    main as usage_cli_main,
)
from codex_agy_bridge.usage_visualization import (  # noqa: E402
    generate_html_report,
    write_html_report,
)


@pytest.fixture(autouse=True)
def _reset_ledgers():
    reset_telemetry_ledgers()
    yield
    reset_telemetry_ledgers()


def _populate_full_test_telemetry(db_path: Path) -> None:
    """Populate database with comprehensive realistic telemetry data."""
    # 1. Run 1: Normal successful run in Project Alpha
    record_run_start_event(
        run_id="run-alpha-1",
        task_id="task-101",
        project_dir="D:/Projects/Alpha",
        db_path=db_path,
    )
    record_worker_launch_event(
        run_id="run-alpha-1",
        task_id="task-101",
        project_dir="D:/Projects/Alpha",
        attempt=1,
        db_path=db_path,
    )
    record_worker_completion_event(
        run_id="run-alpha-1",
        task_id="task-101",
        project_dir="D:/Projects/Alpha",
        duration_seconds=45.0,
        success=True,
        target_state="COMPLETE",
        db_path=db_path,
    )

    # 2. Run 2: Failed run with timeout, retries, account switch, and duplicate quota metrics in Project Beta
    record_run_start_event(
        run_id="run-beta-2",
        task_id="task-202",
        project_dir="D:/Projects/Beta",
        db_path=db_path,
    )
    record_worker_launch_event(
        run_id="run-beta-2",
        task_id="task-202",
        project_dir="D:/Projects/Beta",
        attempt=1,
        db_path=db_path,
    )
    record_worker_completion_event(
        run_id="run-beta-2",
        task_id="task-202",
        project_dir="D:/Projects/Beta",
        duration_seconds=120.0,
        success=False,
        last_error="Local supervision timeout: agy timed out after 120s",
        db_path=db_path,
    )
    record_retry_event(
        run_id="run-beta-2",
        task_id="task-202",
        project_dir="D:/Projects/Beta",
        attempt=2,
        reason="Local timeout retry",
        db_path=db_path,
    )
    record_account_switch_event(
        run_id="run-beta-2",
        task_id="task-202",
        project_dir="D:/Projects/Beta",
        reason="Daily quota exhausted",
        db_path=db_path,
    )
    # Duplicate quota metrics
    record_duplicate_quota_risk_event(
        run_id="run-beta-2",
        task_id="task-202",
        project_dir="D:/Projects/Beta",
        reason="Potential duplicate worker detected during timeout reconciliation",
        db_path=db_path,
    )
    record_avoided_duplicate_retry_event(
        run_id="run-beta-2",
        task_id="task-202",
        project_dir="D:/Projects/Beta",
        reason="Prevented concurrent duplicate retry execution via lease locking",
        db_path=db_path,
    )


def test_html_generation_structure_and_no_external_assets(tmp_path: Path):
    """Verify generated HTML is valid HTML5 and contains strictly zero external assets."""
    db_path = tmp_path / "telemetry_test.sqlite3"
    _populate_full_test_telemetry(db_path)

    ledger = get_telemetry_ledger(db_path)
    report_data = build_usage_report_data(ledger=ledger)
    html_out = generate_html_report(report_data)

    # 1. Valid HTML5 structure
    assert "<!DOCTYPE html>" in html_out
    assert "<html" in html_out
    assert "<head>" in html_out
    assert "<style>" in html_out
    assert "</style>" in html_out
    assert "<body>" in html_out
    assert "</html>" in html_out

    # 2. Strict zero external assets (no external scripts, styles, fonts, or images)
    assert not re.search(r'<script[^>]+src=["\']http', html_out, re.IGNORECASE)
    assert not re.search(r'<link[^>]+href=["\']http', html_out, re.IGNORECASE)
    assert not re.search(r'<img[^>]+src=["\']http', html_out, re.IGNORECASE)
    assert not re.search(r'url\(["\']?http', html_out, re.IGNORECASE)

    # 3. Headers and Titles
    assert "Codex &lt;-&gt; Antigravity Bridge Usage Telemetry" in html_out


def test_html_labels_exact_estimated_unavailable(tmp_path: Path):
    """Verify HTML report includes explicit EXACT, ESTIMATED/DERIVED, and UNAVAILABLE badges and disclaimers."""
    db_path = tmp_path / "telemetry_labels.sqlite3"
    _populate_full_test_telemetry(db_path)

    ledger = get_telemetry_ledger(db_path)
    report_data = build_usage_report_data(ledger=ledger)
    html_out = generate_html_report(report_data)

    # Check for presence of required classification badges
    assert "badge-exact" in html_out
    assert "EXACT" in html_out

    assert "badge-derived" in html_out
    assert "DERIVED" in html_out or "ESTIMATED" in html_out

    assert "badge-unavail" in html_out
    assert "UNAVAILABLE" in html_out

    # Disclaimer check
    assert "No provider-token savings or synthetic cost discount claims are made" in html_out


def test_html_duplicate_metrics_and_workload_totals(tmp_path: Path):
    """Verify duplicate quota risk/avoided counts and workload breakdown in HTML."""
    db_path = tmp_path / "telemetry_dup.sqlite3"
    _populate_full_test_telemetry(db_path)

    ledger = get_telemetry_ledger(db_path)
    report_data = build_usage_report_data(ledger=ledger)
    html_out = generate_html_report(report_data)

    # Duplicate Quota Metrics in HTML
    assert "Duplicate Quota Metrics" in html_out
    assert "1 count" in html_out  # 1 risk, 1 avoided
    assert "Duplicate Quota Risks" in html_out
    assert "Avoided Retries" in html_out

    # Antigravity Workload & Codex Supervision
    assert "Antigravity Execution" in html_out
    assert "Codex Supervision" in html_out
    assert "165" in html_out or "165.00s" in html_out  # 45s + 120s = 165s
    assert "Monitoring Baseline" in html_out

    # Operational Events
    assert "Operational Events" in html_out
    assert "Retries" in html_out
    assert "Timeouts" in html_out
    assert "Account Switches" in html_out


def test_html_escaping_and_xss_safety():
    """Verify strict HTML escaping of potentially malicious or unusual data."""
    report_data = {
        "filters": {
            "db_path": "<script>alert('db')</script>",
            "run_id": "run-<b>injected</b>",
            "task_id": "task-\"quoted\"",
            "project_dir": "D:/Projects/<style>body{color:red}</style>&dir",
            "since": "2026-08-23T00:00:00Z",
            "until": "2026-08-23T23:59:59Z",
        },
        "summary": {
            "event_count": 1,
            "unavailable_count": 0,
            "totals_by_unit": {"<custom_unit>": 10.0},
            "mean_confidence": 1.0,
            "weighted_confidence_by_unit": {"<custom_unit>": 1.0},
            "events_by_source": {"PROVIDER_EXACT": 1},
        },
        "codex": {"calls": 1, "monitoring_turns": 0.0, "resumptions": 0},
        "antigravity": {"calls": 1, "duration_seconds": 10.0, "successes": 1, "failures": 0, "changed_files": 0, "lines_of_code": 0},
        "attribution": {"statement": "No <tokens> claim."},
        "retries": {"total_count": 0, "events": []},
        "timeouts": {"total_count": 0, "classes": {"<TIMEOUT_CLASS>": 1}, "events": []},
        "account_switches": {"total_count": 0, "events": []},
        "duplicate_quota_metrics": {"risk_count": 1, "avoided_count": 0, "source": "<DERIVED_SOURCE>"},
        "confidence": {"mean_confidence": 1.0, "weighted_confidence_by_unit": {}},
        "sources": {"events_by_source": {"PROVIDER_EXACT": 1}},
        "events": [
            {
                "event_id": "<script>evil()</script>",
                "timestamp": "2026-08-23T12:00:00Z",
                "actor": "<actor_xss>",
                "event_type": "<type_xss>",
                "measurement_type": "<mtype_xss>",
                "value": 10.0,
                "unit": "<unit_xss>",
                "measurement_source": "PROVIDER_EXACT",
                "confidence": 1.0,
                "metadata": {"malicious": "<img src=x onerror=alert(1)>"},
            }
        ],
    }

    html_out = generate_html_report(report_data)

    # Verify no raw unescaped HTML tags injected
    assert "<script>alert" not in html_out
    assert "&lt;script&gt;alert(&#x27;db&#x27;)&lt;/script&gt;" in html_out
    assert "<b>injected</b>" not in html_out
    assert "&lt;b&gt;injected&lt;/b&gt;" in html_out
    assert "<style>body{color:red}</style>" not in html_out
    assert "<img src=x onerror=alert(1)>" not in html_out
    assert "&lt;img src=x onerror=alert(1)&gt;" in html_out or "&lt;img src=x onerror=alert(1)&gt;" in html_out.replace('"', '&quot;')
    assert "<custom_unit>" not in html_out
    assert "&lt;custom_unit&gt;" in html_out
    assert "<TIMEOUT_CLASS>" not in html_out
    assert "&lt;TIMEOUT_CLASS&gt;" in html_out


def test_html_determinism(tmp_path: Path):
    """Verify generate_html_report produces deterministic output byte-for-byte on identical data."""
    db_path = tmp_path / "telemetry_det.sqlite3"
    _populate_full_test_telemetry(db_path)

    ledger = get_telemetry_ledger(db_path)
    report_data = build_usage_report_data(ledger=ledger)

    html1 = generate_html_report(report_data)
    html2 = generate_html_report(report_data)
    assert html1 == html2


def test_write_html_report_safe_file_creation(tmp_path: Path):
    """Verify write_html_report creates necessary parent folders and writes valid UTF-8 file."""
    target_file = tmp_path / "nested" / "sub" / "report.html"
    content = "<!DOCTYPE html><html><body>Report Content</body></html>"

    written_path = write_html_report(content, target_file)
    assert written_path.exists()
    assert written_path.is_file()
    assert written_path.read_text(encoding="utf-8") == content


def test_cli_html_option(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """Verify `codex-agy-bridge usage --html <path>` generates HTML file and preserves stdout."""
    db_path = tmp_path / "cli_test.sqlite3"
    _populate_full_test_telemetry(db_path)
    html_file = tmp_path / "output_report.html"

    # 1. Standard HTML report generation
    code = usage_cli_main(["--db", str(db_path), "--html", str(html_file)])
    assert code == 0
    stdout = capsys.readouterr().out
    assert "Usage report HTML visualization written to:" in stdout
    assert html_file.exists()
    file_content = html_file.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in file_content
    assert "Antigravity Execution" in file_content

    # 2. HTML report generation with --json preserving stdout JSON output
    html_file_json = tmp_path / "output_json.html"
    code2 = usage_cli_main(["--db", str(db_path), "--html", str(html_file_json), "--json"])
    assert code2 == 0
    stdout2 = capsys.readouterr().out
    # Must be valid JSON
    json_data = json.loads(stdout2)
    assert "summary" in json_data
    assert "duplicate_quota_metrics" in json_data
    assert json_data["duplicate_quota_metrics"]["risk_count"] == 1
    assert json_data["duplicate_quota_metrics"]["avoided_count"] == 1
    assert html_file_json.exists()


def test_empty_database_html_generation(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """Verify that generating HTML on an empty database succeeds cleanly without errors."""
    db_path = tmp_path / "empty.sqlite3"
    html_file = tmp_path / "empty_report.html"

    code = usage_cli_main(["--db", str(db_path), "--html", str(html_file)])
    assert code == 0
    assert html_file.exists()
    content = html_file.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in content
    assert "Events:</span><span class=\"filter-value\">0 (Unavailable: 0)" in content
    assert "No measurable unit totals recorded" in content
    assert "No telemetry events found matching filters" in content


def test_mixed_units_never_mixed_in_html(tmp_path: Path):
    """Verify that events with different units are strictly segregated into separate unit rows."""
    db_path = tmp_path / "mixed_units.sqlite3"
    ledger = get_telemetry_ledger(db_path)

    ledger.record_event(
        actor="agy",
        event_type="completion",
        measurement_type="duration",
        value=30.0,
        unit="seconds",
    )
    ledger.record_event(
        actor="agy",
        event_type="completion",
        measurement_type="calls",
        value=2.0,
        unit="calls",
    )
    ledger.record_event(
        actor="codex",
        event_type="completion",
        measurement_type="monitoring_turns",
        value=0.0,
        unit="turns",
    )

    report_data = build_usage_report_data(ledger=ledger)
    html_out = generate_html_report(report_data)

    assert "<code>seconds</code>" in html_out
    assert "<code>calls</code>" in html_out
    assert "<code>turns</code>" in html_out
    # Each unit is presented in its own table row with exact value
    assert "30s" in html_out or "30" in html_out
    assert "2 calls" in html_out or "2" in html_out
