"""Usage Telemetry Reporting CLI for Codex <-> Antigravity Bridge.

Provides the `codex-agy-bridge usage` CLI command for querying, aggregating,
and reporting observational telemetry metrics:
- Filter by run_id, task_id, project directory, and time range
- Default human report clearly labeling RUN, CODEX, ANTIGRAVITY, MEASUREMENTS,
  ATTRIBUTION, RETRIES, TIMEOUTS, ACCOUNT_SWITCHES, CONFIDENCE, SOURCE
- Deterministic JSON output preserving separate units (never summing incompatible units)
- Explicit DERIVED/ESTIMATED attribution based solely on recorded measurable workload
  (no provider-token savings or synthetic discount claims)
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from .telemetry import (
    MeasurementSource,
    UsageEvent,
    UsageLedger,
    UsageSummary,
    aggregate_events,
    deterministic_json_dumps,
    get_default_telemetry_db_path,
    normalize_project_path,
    paths_equal,
)
from .telemetry_hooks import get_telemetry_ledger


def build_parser() -> argparse.ArgumentParser:
    """Build argparse parser for `codex-agy-bridge usage`."""
    parser = argparse.ArgumentParser(
        prog="codex-agy-bridge usage",
        description="Query and aggregate Codex <-> Antigravity usage telemetry.",
    )
    parser.add_argument(
        "--db",
        dest="db_path",
        default=None,
        help="path to telemetry SQLite database (default: system standard location)",
    )
    parser.add_argument(
        "--run",
        dest="run_id",
        default=None,
        help="filter events by durable run_id or job_id",
    )
    parser.add_argument(
        "--task",
        dest="task_id",
        default=None,
        help="filter events by task_id",
    )
    parser.add_argument(
        "--project",
        dest="project_dir",
        default=None,
        help="filter events by project directory path (cross-platform normalized)",
    )
    parser.add_argument(
        "--since",
        dest="since",
        default=None,
        help="filter events starting at or after timestamp (ISO 8601 or unix epoch)",
    )
    parser.add_argument(
        "--until",
        dest="until",
        default=None,
        help="filter events ending at or before timestamp (ISO 8601 or unix epoch)",
    )
    parser.add_argument(
        "--json",
        "-j",
        action="store_true",
        help="output deterministic JSON instead of human-formatted report",
    )
    return parser


def build_usage_report_data(
    ledger: UsageLedger,
    run_id: str | None = None,
    task_id: str | None = None,
    project_dir: str | Path | None = None,
    since: str | datetime | float | None = None,
    until: str | datetime | float | None = None,
    db_path_str: str | None = None,
) -> dict[str, Any]:
    """Query ledger and construct structured telemetry report data dictionary."""
    events = ledger.query(
        run_id=run_id,
        task_id=task_id,
        project_dir=project_dir,
        start_time=since,
        end_time=until,
    )
    summary = aggregate_events(events)

    # Actor totals
    totals_codex = summary.totals_by_actor.get("codex", {})
    totals_agy = summary.totals_by_actor.get("agy", {})
    totals_bridge = summary.totals_by_actor.get("bridge", {})

    # Specific measurement totals
    codex_calls = totals_codex.get("calls", 0.0)
    codex_turns = totals_codex.get("turns", 0.0)
    codex_resumptions = summary.totals_by_measurement_type.get("resumptions", {}).get("count", 0.0)

    agy_calls = totals_agy.get("calls", 0.0)
    agy_seconds = totals_agy.get("seconds", 0.0)
    agy_successes = summary.totals_by_measurement_type.get("success_count", {}).get("count", 0.0)
    agy_failures = summary.totals_by_measurement_type.get("failure_count", {}).get("count", 0.0)
    agy_files = summary.totals_by_measurement_type.get("changed_files", {}).get("files", 0.0)
    agy_lines = summary.totals_by_measurement_type.get("lines_of_code", {}).get("lines", 0.0)

    # Retries
    retry_events = [e for e in events if e.measurement_type == "retries" or e.event_type == "retry"]
    retries_count = int(summary.totals_by_measurement_type.get("retries", {}).get("count", len(retry_events)))

    # Timeouts
    timeout_events = [e for e in events if e.measurement_type == "timeout_count" or e.event_type == "timeout_classified"]
    timeouts_count = int(summary.totals_by_measurement_type.get("timeout_count", {}).get("count", len(timeout_events)))
    timeout_classes: dict[str, int] = {}
    for te in timeout_events:
        cls_name = str(te.metadata.get("timeout_class", "UNKNOWN"))
        timeout_classes[cls_name] = timeout_classes.get(cls_name, 0) + 1

    # Account switches
    switch_events = [
        e for e in events if e.measurement_type == "account_switches" or e.event_type == "account_switch_required"
    ]
    switches_count = int(summary.totals_by_measurement_type.get("account_switches", {}).get("count", len(switch_events)))

    # Attribution: DERIVED/ESTIMATED strictly on recorded measurable workload
    attribution_workload = {
        "antigravity_duration_seconds": agy_seconds,
        "antigravity_calls": int(agy_calls),
        "antigravity_successes": int(agy_successes),
        "antigravity_failures": int(agy_failures),
        "codex_calls": int(codex_calls),
        "codex_monitoring_turns": codex_turns,
        "codex_resumptions": int(codex_resumptions),
        "changed_files": int(agy_files),
        "lines_of_code": int(agy_lines),
    }

    norm_proj = normalize_project_path(project_dir)

    return {
        "filters": {
            "db_path": db_path_str or str(ledger.db_path) if ledger.db_path else ":memory:",
            "run_id": run_id,
            "task_id": task_id,
            "project_dir": norm_proj,
            "since": str(since) if since is not None else None,
            "until": str(until) if until is not None else None,
        },
        "summary": summary.to_dict(),
        "codex": {
            "calls": int(codex_calls),
            "monitoring_turns": codex_turns,
            "resumptions": int(codex_resumptions),
        },
        "antigravity": {
            "calls": int(agy_calls),
            "duration_seconds": agy_seconds,
            "successes": int(agy_successes),
            "failures": int(agy_failures),
            "changed_files": int(agy_files),
            "lines_of_code": int(agy_lines),
        },
        "attribution": {
            "classification": "DERIVED/ESTIMATED",
            "basis": "recorded_measurable_workload",
            "statement": (
                "Workload attribution is DERIVED/ESTIMATED solely from recorded measurable workload "
                "(execution duration, calls, monitoring turns, diffs). "
                "No provider-token savings or synthetic cost discount claims are made."
            ),
            "measurable_workload": attribution_workload,
        },
        "retries": {
            "total_count": retries_count,
            "events": [e.to_dict() for e in retry_events],
        },
        "timeouts": {
            "total_count": timeouts_count,
            "classes": timeout_classes,
            "events": [e.to_dict() for e in timeout_events],
        },
        "account_switches": {
            "total_count": switches_count,
            "events": [e.to_dict() for e in switch_events],
        },
        "confidence": {
            "mean_confidence": summary.mean_confidence,
            "weighted_confidence_by_unit": summary.weighted_confidence_by_unit,
        },
        "sources": {
            "events_by_source": summary.events_by_source,
        },
        "events": [e.to_dict() for e in events],
    }


def format_human_report(report_data: dict[str, Any]) -> str:
    """Format report data dictionary into human-readable text output.

    Clearly labels:
    - RUN
    - CODEX
    - ANTIGRAVITY
    - MEASUREMENTS
    - ATTRIBUTION
    - RETRIES
    - TIMEOUTS
    - ACCOUNT_SWITCHES
    - CONFIDENCE
    - SOURCE
    """
    filters = report_data.get("filters", {})
    summary = report_data.get("summary", {})
    codex = report_data.get("codex", {})
    agy = report_data.get("antigravity", {})
    attribution = report_data.get("attribution", {})
    retries = report_data.get("retries", {})
    timeouts = report_data.get("timeouts", {})
    switches = report_data.get("account_switches", {})
    confidence = report_data.get("confidence", {})
    sources = report_data.get("sources", {})

    run_id = filters.get("run_id")
    task_id = filters.get("task_id")
    proj = filters.get("project_dir")
    since = filters.get("since")
    until = filters.get("until")

    lines: list[str] = [
        "=" * 70,
        "Codex <-> Antigravity Bridge Usage Telemetry Report",
        "=" * 70,
    ]

    # 1. RUN
    lines.append("RUN:")
    run_label = run_id if run_id else "ALL (unfiltered)"
    lines.append(f"  Run ID:              {run_label}")
    if task_id:
        lines.append(f"  Task ID:             {task_id}")
    if proj:
        lines.append(f"  Project:             {proj}")
    if since or until:
        time_span = f"{since or 'start'} -> {until or 'now'}"
        lines.append(f"  Time Window:         {time_span}")
    ev_count = summary.get("event_count", 0)
    unavail_count = summary.get("unavailable_count", 0)
    lines.append(f"  Total Events:        {ev_count} (Unavailable data points: {unavail_count})")
    lines.append("")

    # 2. CODEX
    lines.append("CODEX:")
    c_calls = codex.get("calls", 0)
    c_turns = codex.get("monitoring_turns", 0.0)
    c_res = codex.get("resumptions", 0)
    lines.append(f"  Calls / Launches:    {c_calls} calls")
    lines.append(f"  Monitoring Turns:    {c_turns:.2f} turns (Derived baseline)")
    lines.append(f"  Resumptions:         {c_res} count")
    lines.append("")

    # 3. ANTIGRAVITY
    lines.append("ANTIGRAVITY:")
    a_calls = agy.get("calls", 0)
    a_secs = agy.get("duration_seconds", 0.0)
    a_succ = agy.get("successes", 0)
    a_fail = agy.get("failures", 0)
    a_files = agy.get("changed_files", 0)
    a_lines = agy.get("lines_of_code", 0)
    lines.append(f"  Calls / Launches:    {a_calls} calls")
    lines.append(f"  Execution Duration:  {a_secs:.2f} seconds")
    lines.append(f"  Outcomes:            {a_succ} successes, {a_fail} failures")
    lines.append(f"  Worktree Diffs:      {a_files} changed files, {a_lines} lines of code")
    lines.append("")

    # 4. MEASUREMENTS
    lines.append("MEASUREMENTS:")
    totals_unit = summary.get("totals_by_unit", {})
    if totals_unit:
        for unit_name in sorted(totals_unit.keys()):
            val = totals_unit[unit_name]
            lines.append(f"  - {unit_name:<16} {val:.2f} {unit_name}")
    else:
        lines.append("  (No measurable unit totals recorded)")
    lines.append("  [Tokens / Quotas:    UNAVAILABLE (provider metrics not directly observable)]")
    lines.append("")

    # 5. ATTRIBUTION
    lines.append("ATTRIBUTION:           [DERIVED/ESTIMATED]")
    lines.append(
        f"  Workload Breakdown:  Antigravity executed {a_secs:.2f}s across {a_calls} calls; "
        f"Codex monitored with {c_turns:.2f} turns."
    )
    lines.append("  Workload Basis:      Derived strictly from recorded measurable workload (duration, calls, turns, LOC).")
    lines.append("  Disclaimer:          No provider-token savings or synthetic cost discount claims are made.")
    lines.append("")

    # 6. RETRIES
    lines.append("RETRIES:")
    r_count = retries.get("total_count", 0)
    lines.append(f"  Total Retries:       {r_count} count")
    lines.append("")

    # 7. TIMEOUTS
    lines.append("TIMEOUTS:")
    to_count = timeouts.get("total_count", 0)
    lines.append(f"  Total Timeouts:      {to_count} count")
    to_classes = timeouts.get("classes", {})
    if to_classes:
        for cls_name, cnt in sorted(to_classes.items()):
            lines.append(f"    - {cls_name}: {cnt}")
    lines.append("")

    # 8. ACCOUNT_SWITCHES
    lines.append("ACCOUNT_SWITCHES:")
    as_count = switches.get("total_count", 0)
    lines.append(f"  Total Switches:      {as_count} count")
    lines.append("")

    # 9. CONFIDENCE
    lines.append("CONFIDENCE:")
    mean_conf = confidence.get("mean_confidence", 1.0)
    lines.append(f"  Mean Confidence:     {mean_conf:.6f}")
    w_conf = confidence.get("weighted_confidence_by_unit", {})
    if w_conf:
        lines.append("  Weighted Confidence by Unit:")
        for u_name in sorted(w_conf.keys()):
            lines.append(f"    - {u_name:<14} {w_conf[u_name]:.6f}")
    lines.append("")

    # 10. SOURCE
    lines.append("SOURCE:")
    src_map = sources.get("events_by_source", {})
    if src_map:
        for src_name in sorted(src_map.keys()):
            cnt = src_map[src_name]
            pct = (cnt / ev_count * 100) if ev_count > 0 else 0.0
            lines.append(f"  - {src_name:<16} {cnt} events ({pct:.1f}%)")
    else:
        lines.append("  (No sources recorded)")

    lines.extend([
        "=" * 70,
    ])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Entry point for `codex-agy-bridge usage` CLI command."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        ledger = get_telemetry_ledger(args.db_path)
        report_data = build_usage_report_data(
            ledger=ledger,
            run_id=args.run_id,
            task_id=args.task_id,
            project_dir=args.project_dir,
            since=args.since,
            until=args.until,
            db_path_str=args.db_path,
        )

        if args.json:
            print(deterministic_json_dumps(report_data))
        else:
            print(format_human_report(report_data))
        return 0
    except Exception as exc:
        print(f"Error generating usage report: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
