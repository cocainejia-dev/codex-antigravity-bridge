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

from dataclasses import dataclass
from datetime import datetime
import json
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


@dataclass
class FinalReportLinkResult:
    """Result of final-response usage report provenance validation."""

    is_valid: bool
    markdown_link: str | None
    report_path: str | None
    report_uri: str | None
    fail_closed_reason: str | None
    run_id: str | None = None
    origin: str | None = None
    db_classification: str | None = None
    event_provenance: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "markdown_link": self.markdown_link,
            "report_path": self.report_path,
            "report_uri": self.report_uri,
            "fail_closed_reason": self.fail_closed_reason,
            "run_id": self.run_id,
            "origin": self.origin,
            "db_classification": self.db_classification,
            "event_provenance": self.event_provenance,
        }


def validate_final_response_report_link(
    run_result_payload: dict[str, Any] | str,
    supervisor_run_id: str,
    label: str = "Usage Report",
) -> FinalReportLinkResult:
    """Validate and gate final-response report linking based on strict provenance contracts.

    Fail-closed gate requirements:
    1. Status must be READY
    2. File must exist on disk
    3. Exact run_id match between payload, report metadata, and supervisor_run_id
    4. Origin must be PRODUCTION
    5. DB classification must be PRODUCTION_LEDGER
    6. Confirmed PRODUCTION event provenance
    7. Rejects TEST/CI, pytest temp/pytest-of/isolated_telemetry paths, latest alias, .codex/visualizations, mismatched run.

    Returns FinalReportLinkResult with either a clickable Markdown link and resolved path/URI,
    or no link (None) and a fail-closed reason.
    """
    if isinstance(run_result_payload, str):
        try:
            payload = json.loads(run_result_payload)
        except Exception as exc:
            return FinalReportLinkResult(
                is_valid=False,
                markdown_link=None,
                report_path=None,
                report_uri=None,
                fail_closed_reason=f"Invalid run_result payload: JSON decode error: {exc}",
            )
    elif isinstance(run_result_payload, dict):
        payload = run_result_payload
    else:
        return FinalReportLinkResult(
            is_valid=False,
            markdown_link=None,
            report_path=None,
            report_uri=None,
            fail_closed_reason=f"Invalid run_result payload type: {type(run_result_payload).__name__}",
        )

    if not supervisor_run_id or not str(supervisor_run_id).strip():
        return FinalReportLinkResult(
            is_valid=False,
            markdown_link=None,
            report_path=None,
            report_uri=None,
            fail_closed_reason="Missing or empty supervisor_run_id",
        )
    sup_run_id = str(supervisor_run_id).strip()

    # 1. Status check
    status = payload.get("usage_report_status")
    if status != "READY":
        reason = payload.get("usage_report_reason") or f"usage report status is '{status}' (expected 'READY')"
        return FinalReportLinkResult(
            is_valid=False,
            markdown_link=None,
            report_path=None,
            report_uri=None,
            fail_closed_reason=f"Report status not READY: {reason}",
            run_id=sup_run_id,
        )

    # 2. Exact run_id check
    payload_run_id = payload.get("run_id")
    report_run_id = payload.get("usage_report_run_id")

    if payload_run_id != sup_run_id:
        return FinalReportLinkResult(
            is_valid=False,
            markdown_link=None,
            report_path=None,
            report_uri=None,
            fail_closed_reason=(
                f"Mismatched run_id: payload run_id '{payload_run_id}' does not match supervisor run_id '{sup_run_id}'"
            ),
            run_id=sup_run_id,
        )

    if report_run_id != sup_run_id:
        return FinalReportLinkResult(
            is_valid=False,
            markdown_link=None,
            report_path=None,
            report_uri=None,
            fail_closed_reason=(
                f"Mismatched report run_id: usage_report_run_id '{report_run_id}' does not match supervisor run_id '{sup_run_id}'"
            ),
            run_id=sup_run_id,
        )

    # 3. Origin check
    origin = payload.get("usage_report_origin")
    if origin != "PRODUCTION":
        return FinalReportLinkResult(
            is_valid=False,
            markdown_link=None,
            report_path=None,
            report_uri=None,
            fail_closed_reason=(
                f"Rejected non-production usage report origin: '{origin}' (only confirmed PRODUCTION origin accepted)"
            ),
            run_id=sup_run_id,
            origin=origin,
        )

    # 4. DB Classification check
    db_class = payload.get("usage_report_db_classification")
    if db_class != "PRODUCTION_LEDGER":
        return FinalReportLinkResult(
            is_valid=False,
            markdown_link=None,
            report_path=None,
            report_uri=None,
            fail_closed_reason=(
                f"Rejected non-production DB classification: '{db_class}' (only PRODUCTION_LEDGER accepted)"
            ),
            run_id=sup_run_id,
            origin=origin,
            db_classification=db_class,
        )

    # 5. Confirmed event provenance check
    event_prov = payload.get("usage_report_event_provenance")
    if event_prov != "CONFIRMED_PRODUCTION":
        return FinalReportLinkResult(
            is_valid=False,
            markdown_link=None,
            report_path=None,
            report_uri=None,
            fail_closed_reason=(
                f"Unconfirmed event provenance: '{event_prov}' (requires confirmed PRODUCTION events)"
            ),
            run_id=sup_run_id,
            origin=origin,
            db_classification=db_class,
            event_provenance=str(event_prov) if event_prov is not None else None,
        )

    # 6. Path and File existence checks
    path_str = payload.get("usage_report_path")
    if not path_str or not str(path_str).strip():
        return FinalReportLinkResult(
            is_valid=False,
            markdown_link=None,
            report_path=None,
            report_uri=None,
            fail_closed_reason="Missing usage_report_path in payload",
            run_id=sup_run_id,
            origin=origin,
            db_classification=db_class,
            event_provenance=event_prov,
        )

    target_path = Path(path_str).resolve()
    if not target_path.is_file():
        return FinalReportLinkResult(
            is_valid=False,
            markdown_link=None,
            report_path=str(target_path),
            report_uri=None,
            fail_closed_reason=f"Usage report file does not exist on disk: {target_path}",
            run_id=sup_run_id,
            origin=origin,
            db_classification=db_class,
            event_provenance=event_prov,
        )

    # 7. Reject forbidden / alias paths
    if target_path.name.lower() == "latest.html" or target_path.stem.lower() == "latest":
        return FinalReportLinkResult(
            is_valid=False,
            markdown_link=None,
            report_path=str(target_path),
            report_uri=None,
            fail_closed_reason=f"Rejected latest alias path: {target_path} (must be exact run report)",
            run_id=sup_run_id,
            origin=origin,
            db_classification=db_class,
            event_provenance=event_prov,
        )

    norm_path_lower = str(target_path).lower().replace("\\", "/")
    forbidden_patterns = [
        "pytest",
        "pytest-of",
        "isolated_telemetry",
        "test_isolated",
        ".codex/visualizations",
        "visualizations/",
    ]
    for pat in forbidden_patterns:
        if pat in norm_path_lower:
            return FinalReportLinkResult(
                is_valid=False,
                markdown_link=None,
                report_path=str(target_path),
                report_uri=None,
                fail_closed_reason=f"Rejected forbidden report path containing '{pat}': {target_path}",
                run_id=sup_run_id,
                origin=origin,
                db_classification=db_class,
                event_provenance=event_prov,
            )

    expected_name = f"{sanitize_filename_component(sup_run_id)}.html".lower()
    if target_path.name.lower() != expected_name:
        return FinalReportLinkResult(
            is_valid=False,
            markdown_link=None,
            report_path=str(target_path),
            report_uri=None,
            fail_closed_reason=(
                f"Report filename is not bound to exact run_id '{sup_run_id}': {target_path.name}"
            ),
            run_id=sup_run_id,
            origin=origin,
            db_classification=db_class,
            event_provenance=event_prov,
        )

    # 8. All checks passed -> compute URI and Markdown link
    resolved_uri = target_path.as_uri()
    markdown_link = f"[{label}]({resolved_uri})"

    return FinalReportLinkResult(
        is_valid=True,
        markdown_link=markdown_link,
        report_path=str(target_path),
        report_uri=resolved_uri,
        fail_closed_reason=None,
        run_id=sup_run_id,
        origin=origin,
        db_classification=db_class,
        event_provenance=event_prov,
    )


resolve_final_response_report_link = validate_final_response_report_link


__all__ = [
    "get_default_reports_dir",
    "sanitize_filename_component",
    "find_latest_run",
    "resolve_report_path",
    "write_stable_report",
    "FinalReportLinkResult",
    "validate_final_response_report_link",
    "resolve_final_response_report_link",
]
