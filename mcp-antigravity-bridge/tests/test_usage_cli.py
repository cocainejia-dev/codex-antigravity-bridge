"""Comprehensive deterministic tests for Usage Telemetry Reporting CLI."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
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

from codex_agy_bridge import __main__ as bridge_main  # noqa: E402
from codex_agy_bridge import usage_cli  # noqa: E402
from codex_agy_bridge.telemetry import (  # noqa: E402
    EventOrigin,
    MeasurementSource,
    UsageEvent,
    UsageLedger,
    UsageSummary,
    aggregate_events,
    get_default_telemetry_db_path,
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
)
from codex_agy_bridge.usage_cli import (  # noqa: E402
    build_parser,
    build_usage_report_data,
    format_human_report,
    main as usage_cli_main,
)
from codex_agy_bridge.usage_reports import (  # noqa: E402
    find_latest_run,
    get_default_reports_dir,
    resolve_report_path,
    write_stable_report,
)


@pytest.fixture(autouse=True)
def _reset_ledgers():
    reset_telemetry_ledgers()
    yield
    reset_telemetry_ledgers()


def _populate_sample_telemetry(db_path: Path) -> None:
    """Populate database with multi-run, multi-project, multi-actor sample data."""
    # Run 1: Normal successful run in Project Alpha
    record_run_start_event(
        run_id="run-001",
        task_id="task-001",
        project_dir="D:/Projects/Alpha",
        db_path=db_path,
    )
    record_worker_launch_event(
        run_id="run-001",
        task_id="task-001",
        project_dir="D:/Projects/Alpha",
        attempt=1,
        db_path=db_path,
    )
    record_worker_completion_event(
        run_id="run-001",
        task_id="task-001",
        project_dir="D:/Projects/Alpha",
        duration_seconds=30.0,
        success=True,
        target_state="COMPLETE",
        db_path=db_path,
    )

    # Run 2: Failed run with timeout and retry in Project Beta
    record_run_start_event(
        run_id="run-002",
        task_id="task-002",
        project_dir="D:/Projects/Beta",
        db_path=db_path,
    )
    record_worker_launch_event(
        run_id="run-002",
        task_id="task-002",
        project_dir="D:/Projects/Beta",
        attempt=1,
        db_path=db_path,
    )
    record_worker_completion_event(
        run_id="run-002",
        task_id="task-002",
        project_dir="D:/Projects/Beta",
        duration_seconds=120.0,
        success=False,
        last_error="Local supervision timeout: agy timed out after 120s",
        db_path=db_path,
    )
    record_retry_event(
        run_id="run-002",
        task_id="task-002",
        project_dir="D:/Projects/Beta",
        attempt=2,
        reason="Local timeout retry",
        db_path=db_path,
    )
    record_account_switch_event(
        run_id="run-002",
        task_id="task-002",
        project_dir="D:/Projects/Beta",
        reason="Quota reached",
        db_path=db_path,
    )


def test_parser_options():
    """Verify argparse options and flags for `usage` CLI command."""
    parser = build_parser()
    args = parser.parse_args([
        "--db", "custom.sqlite3",
        "--run", "run-123",
        "--task", "task-456",
        "--project", "D:/repos/proj",
        "--since", "2026-08-23T10:00:00Z",
        "--until", "2026-08-23T12:00:00Z",
        "--json",
    ])
    assert args.db_path == "custom.sqlite3"
    assert args.run_id == "run-123"
    assert args.task_id == "task-456"
    assert args.project_dir == "D:/repos/proj"
    assert args.since == "2026-08-23T10:00:00Z"
    assert args.until == "2026-08-23T12:00:00Z"
    assert args.json is True


def test_human_output_labels(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """Verify that default human output clearly includes ALL 10 required labels."""
    db_path = tmp_path / "test_human.sqlite3"
    _populate_sample_telemetry(db_path)

    exit_code = usage_cli_main(["--db", str(db_path)])
    assert exit_code == 0
    captured = capsys.readouterr().out

    required_labels = [
        "RUN:",
        "CODEX:",
        "ANTIGRAVITY:",
        "MEASUREMENTS:",
        "ATTRIBUTION:",
        "RETRIES:",
        "TIMEOUTS:",
        "ACCOUNT_SWITCHES:",
        "CONFIDENCE:",
        "SOURCE:",
    ]
    for label in required_labels:
        assert label in captured, f"Missing required label '{label}' in human report:\n{captured}"

    # Verify content indicators
    assert "DERIVED/ESTIMATED" in captured
    assert "No provider-token savings" in captured
    assert "UNAVAILABLE" in captured


def test_json_output_deterministic_and_separate_units(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """Verify deterministic JSON output and separate unit preservation (never summed)."""
    db_path = tmp_path / "test_json.sqlite3"
    _populate_sample_telemetry(db_path)

    exit_code = usage_cli_main(["--db", str(db_path), "--json"])
    assert exit_code == 0
    captured = capsys.readouterr().out

    data = json.loads(captured)
    assert "filters" in data
    assert "summary" in data
    assert "codex" in data
    assert "antigravity" in data
    assert "attribution" in data
    assert "retries" in data
    assert "timeouts" in data
    assert "account_switches" in data
    assert "confidence" in data
    assert "sources" in data
    assert "events" in data

    # Attribution check
    assert data["attribution"]["classification"] == "DERIVED/ESTIMATED"
    assert "measurable_workload" in data["attribution"]
    assert "No provider-token savings" in data["attribution"]["statement"]

    # Separate unit totals
    totals_unit = data["summary"]["totals_by_unit"]
    assert "calls" in totals_unit
    assert "seconds" in totals_unit
    assert "turns" in totals_unit
    # Units are NOT mixed together
    assert totals_unit["calls"] > 0
    assert totals_unit["seconds"] > 0

    # Determinism: second run produces identical string
    exit_code2 = usage_cli_main(["--db", str(db_path), "--json"])
    assert exit_code2 == 0
    captured2 = capsys.readouterr().out
    assert captured == captured2


def test_filtering_per_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """Verify filtering by --run restricts data to the targeted run_id."""
    db_path = tmp_path / "test_run_filter.sqlite3"
    _populate_sample_telemetry(db_path)

    # Filter Run 1
    code1 = usage_cli_main(["--db", str(db_path), "--run", "run-001", "--json"])
    assert code1 == 0
    data1 = json.loads(capsys.readouterr().out)
    assert data1["filters"]["run_id"] == "run-001"
    assert all(e["run_id"] == "run-001" for e in data1["events"])
    assert data1["timeouts"]["total_count"] == 0
    assert data1["retries"]["total_count"] == 0

    # Filter Run 2
    code2 = usage_cli_main(["--db", str(db_path), "--run", "run-002", "--json"])
    assert code2 == 0
    data2 = json.loads(capsys.readouterr().out)
    assert data2["filters"]["run_id"] == "run-002"
    assert all(e["run_id"] == "run-002" for e in data2["events"])
    assert data2["timeouts"]["total_count"] == 1
    assert data2["retries"]["total_count"] == 1
    assert data2["account_switches"]["total_count"] == 1


def test_filtering_per_task(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """Verify filtering by --task restricts data to the targeted task_id."""
    db_path = tmp_path / "test_task_filter.sqlite3"
    _populate_sample_telemetry(db_path)

    code = usage_cli_main(["--db", str(db_path), "--task", "task-001", "--json"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["filters"]["task_id"] == "task-001"
    assert all(e["task_id"] == "task-001" for e in data["events"])
    assert len(data["events"]) > 0


def test_filtering_per_project_cross_platform(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """Verify filtering by --project normalizes Windows and POSIX paths properly."""
    db_path = tmp_path / "test_project_filter.sqlite3"
    _populate_sample_telemetry(db_path)

    # Query with Windows backslashes and different case
    code = usage_cli_main(["--db", str(db_path), "--project", "d:\\projects\\alpha", "--json"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data["events"]) > 0
    for ev in data["events"]:
        assert ev["run_id"] == "run-001"


def test_filtering_time_range(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """Verify time range filtering via --since and --until."""
    db_path = tmp_path / "test_time_filter.sqlite3"
    ledger = get_telemetry_ledger(db_path)

    ledger.record_event(
        actor="codex",
        event_type="step",
        measurement_type="calls",
        value=1.0,
        unit="calls",
        timestamp="2026-08-23T08:00:00Z",
    )
    ledger.record_event(
        actor="codex",
        event_type="step",
        measurement_type="calls",
        value=1.0,
        unit="calls",
        timestamp="2026-08-23T10:00:00Z",
    )
    ledger.record_event(
        actor="codex",
        event_type="step",
        measurement_type="calls",
        value=1.0,
        unit="calls",
        timestamp="2026-08-23T12:00:00Z",
    )

    code = usage_cli_main([
        "--db", str(db_path),
        "--since", "2026-08-23T09:00:00Z",
        "--until", "2026-08-23T11:00:00Z",
        "--json",
    ])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data["events"]) == 1
    assert data["events"][0]["timestamp"].startswith("2026-08-23T10:00:00")


def test_empty_database_human_and_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """Verify that reporting on an empty database produces valid zeroed output without crashing."""
    db_path = tmp_path / "empty.sqlite3"

    # Human report on empty db
    code1 = usage_cli_main(["--db", str(db_path)])
    assert code1 == 0
    human_out = capsys.readouterr().out
    assert "Total Events:        0" in human_out
    assert "RUN:" in human_out
    assert "CODEX:" in human_out
    assert "ANTIGRAVITY:" in human_out
    assert "MEASUREMENTS:" in human_out
    assert "ATTRIBUTION:" in human_out
    assert "RETRIES:" in human_out
    assert "TIMEOUTS:" in human_out
    assert "ACCOUNT_SWITCHES:" in human_out
    assert "CONFIDENCE:" in human_out
    assert "SOURCE:" in human_out

    # JSON report on empty db
    code2 = usage_cli_main(["--db", str(db_path), "--json"])
    assert code2 == 0
    data = json.loads(capsys.readouterr().out)
    assert data["summary"]["event_count"] == 0
    assert len(data["events"]) == 0
    assert data["attribution"]["classification"] == "DERIVED/ESTIMATED"


def test_bridge_main_usage_delegation(monkeypatch: pytest.MonkeyPatch):
    """Verify that `__main__.main(['usage', ...])` delegates to `usage_cli.main`."""
    called_with: list[list[str]] = []
    monkeypatch.setattr(usage_cli, "main", lambda argv: (called_with.append(argv), 0)[1])

    ret1 = bridge_main.main(["usage", "--json"])
    assert ret1 == 0
    assert called_with == [["--json"]]

    ret2 = bridge_main.main(["--usage", "--run", "run-999"])
    assert ret2 == 0
    assert called_with == [["--json"], ["--run", "run-999"]]


def test_latest_production_selection(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """Verify --latest selects the newest confirmed PRODUCTION run and ignores TEST/CI/UNKNOWN by default."""
    db_path = tmp_path / "latest_test.sqlite3"

    # 1. Older production run
    record_run_start_event(
        run_id="prod-run-old",
        task_id="task-old",
        db_path=db_path,
        origin=EventOrigin.PRODUCTION,
    )
    record_worker_completion_event(
        run_id="prod-run-old",
        task_id="task-old",
        duration_seconds=10.0,
        success=True,
        db_path=db_path,
        origin=EventOrigin.PRODUCTION,
    )

    # 2. Newer confirmed production run
    record_run_start_event(
        run_id="prod-run-new",
        task_id="task-new",
        db_path=db_path,
        origin=EventOrigin.PRODUCTION,
    )
    record_worker_completion_event(
        run_id="prod-run-new",
        task_id="task-new",
        duration_seconds=20.0,
        success=True,
        db_path=db_path,
        origin=EventOrigin.PRODUCTION,
    )

    # 3. Even newer TEST run
    record_run_start_event(
        run_id="test-run-newest",
        task_id="task-test",
        db_path=db_path,
        origin=EventOrigin.TEST,
    )
    record_worker_completion_event(
        run_id="test-run-newest",
        task_id="task-test",
        duration_seconds=5.0,
        success=True,
        db_path=db_path,
        origin=EventOrigin.TEST,
    )

    # 4. Even newer CI run
    record_run_start_event(
        run_id="ci-run-newest",
        task_id="task-ci",
        db_path=db_path,
        origin=EventOrigin.CI,
    )
    record_worker_completion_event(
        run_id="ci-run-newest",
        task_id="task-ci",
        duration_seconds=5.0,
        success=True,
        db_path=db_path,
        origin=EventOrigin.CI,
    )

    # 5. UNKNOWN origin run
    record_run_start_event(
        run_id="unknown-run",
        task_id="task-unk",
        db_path=db_path,
        origin=EventOrigin.UNKNOWN,
    )

    # Test default --latest selection with JSON output
    code = usage_cli_main(["--db", str(db_path), "--latest", "--json"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["filters"]["latest"] is True
    assert data["filters"]["latest_found"] is True
    assert data["filters"]["run_id"] == "prod-run-new"
    assert data["filters"]["latest_origin"] == "PRODUCTION"
    assert all(e["run_id"] == "prod-run-new" for e in data["events"])

    # Test default --latest with human report
    code2 = usage_cli_main(["--db", str(db_path), "--latest"])
    assert code2 == 0
    human_out = capsys.readouterr().out
    assert "prod-run-new" in human_out
    assert "PRODUCTION" in human_out or "production" in human_out


def test_latest_inclusion_flags(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """Verify --include-test, --include-ci, and --include-unknown flags make non-production runs eligible."""
    db_path = tmp_path / "inclusion_test.sqlite3"

    # Production run
    record_run_start_event(
        run_id="prod-run",
        task_id="task-prod",
        db_path=db_path,
        origin=EventOrigin.PRODUCTION,
    )
    record_worker_completion_event(
        run_id="prod-run",
        task_id="task-prod",
        duration_seconds=10.0,
        success=True,
        db_path=db_path,
        origin=EventOrigin.PRODUCTION,
    )

    # Newer TEST run
    record_run_start_event(
        run_id="test-run-newest",
        task_id="task-test",
        db_path=db_path,
        origin=EventOrigin.TEST,
    )
    record_worker_completion_event(
        run_id="test-run-newest",
        task_id="task-test",
        duration_seconds=5.0,
        success=True,
        db_path=db_path,
        origin=EventOrigin.TEST,
    )

    # Newer CI run
    record_run_start_event(
        run_id="ci-run-newest",
        task_id="task-ci",
        db_path=db_path,
        origin=EventOrigin.CI,
    )
    record_worker_completion_event(
        run_id="ci-run-newest",
        task_id="task-ci",
        duration_seconds=5.0,
        success=True,
        db_path=db_path,
        origin=EventOrigin.CI,
    )

    # 1. With --include-test, test run is eligible
    code_test = usage_cli_main(["--db", str(db_path), "--latest", "--include-test", "--json"])
    assert code_test == 0
    data_test = json.loads(capsys.readouterr().out)
    assert data_test["filters"]["latest_found"] is True
    assert data_test["filters"]["run_id"] in ("test-run-newest", "ci-run-newest")

    # 2. With --include-ci, CI run is eligible
    code_ci = usage_cli_main(["--db", str(db_path), "--latest", "--include-ci", "--json"])
    assert code_ci == 0
    data_ci = json.loads(capsys.readouterr().out)
    assert data_ci["filters"]["latest_found"] is True


def test_latest_no_production_found(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """Verify behavior when --latest finds no production runs."""
    db_path = tmp_path / "no_prod.sqlite3"

    # Only record a TEST run
    record_run_start_event(
        run_id="test-only-run",
        task_id="task-test-only",
        db_path=db_path,
        origin=EventOrigin.TEST,
    )

    # 1. Human mode
    code1 = usage_cli_main(["--db", str(db_path), "--latest"])
    assert code1 == 0
    human_out = capsys.readouterr().out
    assert "No confirmed production run found" in human_out
    assert "TEST, CI, and UNKNOWN runs are excluded by default." in human_out

    # 2. JSON mode
    code2 = usage_cli_main(["--db", str(db_path), "--latest", "--json"])
    assert code2 == 0
    data = json.loads(capsys.readouterr().out)
    assert data["filters"]["latest"] is True
    assert data["filters"]["latest_found"] is False
    assert "No confirmed production run found" in data["message"]
    assert data["summary"]["event_count"] == 0

    # 3. HTML mode
    html_file = tmp_path / "no_prod.html"
    code3 = usage_cli_main(["--db", str(db_path), "--latest", "--html", str(html_file)])
    assert code3 == 0
    assert html_file.exists()
    html_content = html_file.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in html_content
    assert '<html lang="zh-CN">' in html_content


def test_stable_report_path_unicode_and_spaces(tmp_path: Path):
    """Verify exact report path resolution and Path.as_uri handling with Unicode and spaces."""
    nested_unicode_dir = tmp_path / "测试 项目 目录 2026" / "子目录 with spaces"
    target_file = nested_unicode_dir / "运行 报告.html"
    alias_file = nested_unicode_dir / "latest.html"
    content = "<!DOCTYPE html><html lang=\"zh-CN\"><body>测试内容</body></html>"

    target, target_uri, alias, alias_uri = write_stable_report(
        html_content=content,
        target_path=target_file,
        alias_path=alias_file,
    )

    assert target.exists()
    assert target.is_file()
    assert target.read_text(encoding="utf-8") == content
    assert target_uri.startswith("file://")
    assert "%20" in target_uri or " " in target_uri
    assert target_uri == target.as_uri()

    assert alias is not None and alias.exists()
    assert alias.read_text(encoding="utf-8") == content
    assert alias_uri is not None and alias_uri.startswith("file://")
    assert alias_uri == alias.as_uri()
