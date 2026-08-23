r"""Report directory and run resolution helpers for Usage Telemetry.

Provides:
- Stable report directory helper under %LOCALAPPDATA%\codex-agy-bridge\reports
  (or POSIX / platform equivalent)
- Path resolution and URI conversion using Path.resolve and Path.as_uri
- Safe atomic report writing preserving existing reports
- Default latest run selection of newest confirmed PRODUCTION run/task,
  excluding TEST and CI and not letting UNKNOWN override production
- Explicit opt-in filters for TEST, CI, and UNKNOWN origins
"""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Sequence

from .telemetry import (
    EventOrigin,
    UsageEvent,
    UsageLedger,
    normalize_project_path,
    paths_equal,
)


def get_default_reports_dir() -> Path:
    r"""Return the stable default directory for usage telemetry HTML reports.

    Uses CODEX_AGY_REPORTS_DIR environment variable if configured.
    Otherwise defaults to %LOCALAPPDATA%\codex-agy-bridge\reports on Windows,
    or ~/.local/share/codex-agy-bridge/reports on other platforms.
    """
    env_dir = os.environ.get("CODEX_AGY_REPORTS_DIR")
    if env_dir and env_dir.strip():
        return Path(env_dir.strip()).resolve()

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base_dir = Path(local_app_data) / "codex-agy-bridge" / "reports"
    else:
        base_dir = Path.home() / ".local" / "share" / "codex-agy-bridge" / "reports"
    return base_dir.resolve()


def sanitize_filename_component(name: str) -> str:
    """Sanitize arbitrary string for safe cross-platform filename usage."""
    if not name:
        return "report"
    # Replace illegal filename characters across Windows/POSIX with underscore
    sanitized = re.sub(r'[\\/*?:"<>|\r\n\t\x00]', "_", str(name).strip())
    sanitized = sanitized.strip(" .")
    return sanitized or "report"


def find_latest_run(
    ledger: UsageLedger,
    project_dir: str | Path | None = None,
    include_test: bool = False,
    include_ci: bool = False,
    include_unknown: bool = False,
    since: str | datetime | float | None = None,
    until: str | datetime | float | None = None,
) -> dict[str, Any] | None:
    """Find the newest run or task according to production safety rules.

    Rules:
    - Default: selects the newest confirmed PRODUCTION run/task.
    - Excludes TEST and CI runs/events by default.
    - Does NOT let UNKNOWN origin runs override or replace PRODUCTION runs.
    - If include_test=True, TEST runs become eligible.
    - If include_ci=True, CI runs become eligible.
    - If include_unknown=True, UNKNOWN runs become eligible.
    - Collapses listing at run/task level; does not alter raw event granularity.

    Returns a dictionary with run metadata if found, or None if no matching run exists.
    """
    all_events = ledger.query(
        project_dir=project_dir,
        start_time=since,
        end_time=until,
    )
    if not all_events:
        return None

    # Group events by durable run_id or task_id
    groups: dict[tuple[str, str], list[UsageEvent]] = {}
    for ev in all_events:
        if ev.run_id:
            key = ("run", ev.run_id)
        elif ev.task_id:
            key = ("task", ev.task_id)
        else:
            key = ("event", ev.event_id)
        groups.setdefault(key, []).append(ev)

    allowed_origins: set[EventOrigin] = {EventOrigin.PRODUCTION}
    if include_test:
        allowed_origins.add(EventOrigin.TEST)
    if include_ci:
        allowed_origins.add(EventOrigin.CI)
    if include_unknown:
        allowed_origins.add(EventOrigin.UNKNOWN)

    candidate_runs: list[dict[str, Any]] = []

    for (key_type, key_val), ev_list in groups.items():
        origins = {ev.origin for ev in ev_list}

        has_production = EventOrigin.PRODUCTION in origins
        has_test = EventOrigin.TEST in origins
        has_ci = EventOrigin.CI in origins
        has_unknown = (EventOrigin.UNKNOWN in origins) or (None in origins)

        if has_production and not (has_test or has_ci):
            primary_origin = EventOrigin.PRODUCTION
        elif has_test:
            primary_origin = EventOrigin.TEST
        elif has_ci:
            primary_origin = EventOrigin.CI
        else:
            primary_origin = EventOrigin.UNKNOWN

        if primary_origin not in allowed_origins:
            continue

        latest_ts = max((e.timestamp for e in ev_list if e.timestamp), default="")
        latest_created = max((e.created_at for e in ev_list if e.created_at), default="")

        run_id_val = ev_list[0].run_id if key_type == "run" else None
        task_id_val = ev_list[0].task_id if (key_type == "task" or not run_id_val) else None

        candidate_runs.append({
            "key_type": key_type,
            "key_value": key_val,
            "run_id": run_id_val or (key_val if key_type == "run" else None),
            "task_id": task_id_val or (key_val if key_type == "task" else None),
            "primary_origin": primary_origin,
            "origins": [o.value if isinstance(o, EventOrigin) else str(o) for o in origins],
            "latest_timestamp": latest_ts,
            "latest_created": latest_created,
            "event_count": len(ev_list),
            "events": ev_list,
        })

    if not candidate_runs:
        return None

    # If only UNKNOWN and PRODUCTION are eligible (neither include_test nor include_ci was requested),
    # ensure UNKNOWN does not override confirmed PRODUCTION runs.
    if not (include_test or include_ci):
        prod_candidates = [c for c in candidate_runs if c["primary_origin"] == EventOrigin.PRODUCTION]
        if prod_candidates:
            prod_candidates.sort(key=lambda c: (c["latest_timestamp"], c["latest_created"]), reverse=True)
            return prod_candidates[0]

    # When explicit opt-in flags (include_test / include_ci) are requested, sort all eligible runs by time
    candidate_runs.sort(key=lambda c: (c["latest_timestamp"], c["latest_created"]), reverse=True)
    return candidate_runs[0]


def resolve_report_path(
    html_path: str | Path | None = None,
    run_id: str | None = None,
    task_id: str | None = None,
    is_latest: bool = False,
    reports_dir: str | Path | None = None,
) -> tuple[Path, Path | None]:
    """Resolve target HTML report path and optional latest alias path.

    Returns:
        (target_path, alias_path)
    """
    base_dir = (Path(reports_dir) if reports_dir is not None else get_default_reports_dir()).resolve()

    if html_path is not None and str(html_path).strip() and str(html_path).strip() != "AUTO":
        target = Path(html_path).resolve()
        alias = None
        if is_latest and target.parent == base_dir and target.name != "latest.html":
            alias = (base_dir / "latest.html").resolve()
        return target, alias

    if run_id and run_id.strip():
        safe_name = sanitize_filename_component(run_id)
        target = (base_dir / f"{safe_name}.html").resolve()
    elif task_id and task_id.strip():
        safe_name = sanitize_filename_component(task_id)
        target = (base_dir / f"{safe_name}.html").resolve()
    else:
        target = (base_dir / "usage_report.html").resolve()

    alias = (base_dir / "latest.html").resolve() if is_latest else None
    return target, alias


def write_stable_report(
    html_content: str,
    target_path: str | Path,
    alias_path: str | Path | None = None,
) -> tuple[Path, str, Path | None, str | None]:
    """Safely write HTML report with deterministic UTF-8 encoding.

    Returns:
        (target_resolved_path, target_uri, alias_resolved_path_or_none, alias_uri_or_none)
    """
    target = Path(target_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    tmp_target = target.with_name(f".tmp.{os.getpid()}.{time.time_ns()}.{target.name}")
    try:
        tmp_target.write_text(html_content, encoding="utf-8")
        tmp_target.replace(target)
    except Exception:
        target.write_text(html_content, encoding="utf-8")
        if tmp_target.exists():
            try:
                tmp_target.unlink()
            except Exception:
                pass

    alias_target: Path | None = None
    alias_uri: str | None = None

    if alias_path is not None:
        alias = Path(alias_path).resolve()
        alias.parent.mkdir(parents=True, exist_ok=True)
        tmp_alias = alias.with_name(f".tmp.{os.getpid()}.{time.time_ns()}.{alias.name}")
        try:
            tmp_alias.write_text(html_content, encoding="utf-8")
            tmp_alias.replace(alias)
        except Exception:
            alias.write_text(html_content, encoding="utf-8")
            if tmp_alias.exists():
                try:
                    tmp_alias.unlink()
                except Exception:
                    pass
        alias_target = alias
        alias_uri = alias.as_uri()

    return target, target.as_uri(), alias_target, alias_uri


__all__ = [
    "get_default_reports_dir",
    "sanitize_filename_component",
    "find_latest_run",
    "resolve_report_path",
    "write_stable_report",
]
