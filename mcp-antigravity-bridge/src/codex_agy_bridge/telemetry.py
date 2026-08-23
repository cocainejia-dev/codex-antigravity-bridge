"""Usage Telemetry Core for Codex <-> Antigravity Bridge.

This module provides stdlib-only usage telemetry tracking, aggregation,
and persistence with:
- Structured UsageEvent and UsageLedger
- Explicit measurement sources and confidence levels
- Mixed-unit safety (never summing incompatible units)
- Secret-safe metadata redaction (tokens, cookies, prompts, passwords, etc.)
- Per-run, per-project, and time-range aggregation
- Separate lazy SQLite telemetry schema backward-compatible with existing databases
- Idempotent event recording and concurrency safety
- Robust Windows path normalization
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import logging
import math
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import posixpath
import re
import sqlite3
import threading
from typing import Any, Iterable, Sequence

logger = logging.getLogger(__name__)

TELEMETRY_SCHEMA_VERSION = 1


def _utc_now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


def _format_timestamp(val: str | datetime | float | int | None) -> str:
    """Normalize a timestamp to UTC ISO 8601 string."""
    if val is None:
        return _utc_now_iso()
    if isinstance(val, bool):
        raise ValueError("Invalid timestamp type: bool")
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=timezone.utc)
        return val.astimezone(timezone.utc).isoformat()
    if isinstance(val, (int, float)):
        if isinstance(val, float) and not math.isfinite(val):
            raise ValueError(f"Invalid non-finite timestamp: {val!r}")
        return datetime.fromtimestamp(val, tz=timezone.utc).isoformat()
    if isinstance(val, str):
        val_str = val.strip()
        if not val_str:
            return _utc_now_iso()
        # Parse ISO string to ensure valid format and normalize to UTC
        try:
            dt = datetime.fromisoformat(val_str.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
        except Exception:
            # If not standard ISO, return stripped string
            return val_str
    raise ValueError(f"Invalid timestamp type: {type(val).__name__}")


def normalize_project_path(path: str | Path | None) -> str | None:
    """Normalize a project directory path for robust cross-platform comparison.

    Converts Windows and POSIX path variants into a canonical normalized POSIX string.
    On Windows, drive letters and paths are lowercased for case-insensitive matching.
    """
    if path is None:
        return None
    p_str = str(path).strip()
    if not p_str:
        return None

    # Strip Windows extended-length prefix (e.g. \\?\ or //?/)
    if p_str.startswith(("\\\\?\\", "//?/")):
        p_str = p_str[4:]

    # If path is Windows-style (drive letter, backslash, or UNC)
    if "\\" in p_str or (len(p_str) >= 2 and p_str[1] == ":") or p_str.startswith(("\\\\", "//")):
        norm = PureWindowsPath(p_str).as_posix()
        norm = posixpath.normpath(norm).lower()
    elif p_str.startswith("/"):
        norm = PurePosixPath(p_str).as_posix()
        norm = posixpath.normpath(norm)
    else:
        try:
            resolved = Path(p_str).resolve()
            norm = resolved.as_posix()
        except Exception:
            norm = p_str.replace("\\", "/")
        if os.name == "nt" or (len(norm) >= 2 and norm[1] == ":"):
            norm = norm.lower()
        norm = posixpath.normpath(norm)

    return norm.rstrip("/") if len(norm) > 1 and norm != "/" else norm


def paths_equal(p1: str | Path | None, p2: str | Path | None) -> bool:
    """Compare two file/directory paths for equality after normalization."""
    norm1 = normalize_project_path(p1)
    norm2 = normalize_project_path(p2)
    if norm1 is None or norm2 is None:
        return norm1 == norm2
    return norm1 == norm2


class MeasurementSource(str, Enum):
    """Explicit measurement source classification."""

    PROVIDER_EXACT = "PROVIDER_EXACT"
    CLI_EXACT = "CLI_EXACT"
    QUOTA_DELTA = "QUOTA_DELTA"
    TEXT_ESTIMATE = "TEXT_ESTIMATE"
    DERIVED = "DERIVED"
    UNAVAILABLE = "UNAVAILABLE"

    @classmethod
    def from_value(cls, val: str | MeasurementSource) -> MeasurementSource:
        """Parse source with case and character normalization."""
        if isinstance(val, cls):
            return val
        if not isinstance(val, str):
            raise ValueError(f"Invalid measurement source type: {type(val).__name__}")
        norm = val.strip().upper().replace("-", "_").replace(" ", "_")
        for member in cls:
            if member.value == norm or member.name == norm:
                return member
        raise ValueError(f"Unknown measurement source: {val!r}")


DEFAULT_SOURCE_CONFIDENCE: dict[MeasurementSource, float] = {
    MeasurementSource.PROVIDER_EXACT: 1.0,
    MeasurementSource.CLI_EXACT: 1.0,
    MeasurementSource.DERIVED: 0.9,
    MeasurementSource.QUOTA_DELTA: 0.8,
    MeasurementSource.TEXT_ESTIMATE: 0.6,
    MeasurementSource.UNAVAILABLE: 0.0,
}


_SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)(password|passwd|pwd|secret|token|cookie|auth|authorization|"
    r"credential|bearer|api_key|apikey|private_key|privkey|access_token|"
    r"refresh_token|session|jwt|prompt|raw_prompt|user_prompt|system_prompt|"
    r"body|headers|signature|client_secret)"
)

_SENSITIVE_VALUE_PATTERNS = [
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9\-\._~\+\/]+=*"), "Bearer [REDACTED]"),
    (re.compile(r"\b(sk-[a-zA-Z0-9_\-]{20,})\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"\b(AIza[0-9A-Za-z\-_]{35})\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"\b(gh[pousr]_[0-9a-zA-Z]{36})\b"), "[REDACTED_TOKEN]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_\-]+\b"), "[REDACTED_JWT]"),
    (re.compile(r"https?://[^:\s]+:[^@\s]+@"), "https://[REDACTED_AUTH]@"),
    (re.compile(r"(?i)(password|passwd|pwd)\s*=\s*[^\s;&]+"), r"\1=[REDACTED]"),
    (re.compile(r"(?i)(session_id|PHPSESSID|connect\.sid|JSESSIONID|auth_token)=[^\s;]+"), r"\1=[REDACTED]"),
]


def _redact_value_string(val: str) -> str:
    """Scrub known sensitive patterns from free-form text."""
    res = val
    for pat, repl in _SENSITIVE_VALUE_PATTERNS:
        res = pat.sub(repl, res)
    return res


def redact_metadata(data: Any, max_depth: int = 10) -> Any:
    """Recursively scrub secrets, tokens, credentials, and prompts from metadata."""
    if max_depth <= 0:
        return "[MAX_DEPTH_EXCEEDED]"

    if isinstance(data, dict):
        redacted_dict: dict[str, Any] = {}
        for k, v in data.items():
            k_str = str(k)
            if _SENSITIVE_KEY_PATTERN.search(k_str):
                if "prompt" in k_str.lower() and isinstance(v, str):
                    prompt_hash = hashlib.sha256(v.encode("utf-8", errors="ignore")).hexdigest()[:16]
                    redacted_dict[k_str] = f"[REDACTED_PROMPT_HASH:{prompt_hash}]"
                else:
                    redacted_dict[k_str] = "[REDACTED]"
            else:
                redacted_dict[k_str] = redact_metadata(v, max_depth - 1)
        return redacted_dict

    if isinstance(data, (list, tuple, set)):
        return [redact_metadata(elem, max_depth - 1) for elem in data]

    if isinstance(data, str):
        return _redact_value_string(data)

    if isinstance(data, (int, float, bool)) or data is None:
        return data

    if isinstance(data, (datetime, Path, Enum)):
        return str(data)

    # Fallback for arbitrary non-primitive objects
    try:
        return _redact_value_string(str(data))
    except Exception:
        return "[UNSERIALIZABLE]"


def _json_fallback(o: Any) -> Any:
    if isinstance(o, datetime):
        return _format_timestamp(o)
    if isinstance(o, Path):
        return str(o)
    if isinstance(o, Enum):
        return o.value
    if isinstance(o, (set, tuple)):
        return list(o)
    return str(o)


def deterministic_json_dumps(obj: Any) -> str:
    """Serialize object to deterministic canonical JSON with sorted keys."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=_json_fallback,
    )


def compute_event_id(
    run_id: str | None,
    actor: str,
    event_type: str,
    measurement_type: str,
    unit: str,
    value: float | int | None,
    timestamp: str,
    metadata_json: str,
    task_id: str | None = None,
) -> str:
    """Generate a deterministic sha256 digest event ID for idempotency."""
    val_repr = "null" if value is None else f"{float(value):.6f}"
    payload = f"{run_id or ''}:{task_id or ''}:{actor}:{event_type}:{measurement_type}:{unit}:{val_repr}:{timestamp}:{metadata_json}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class UsageEvent:
    """A single observational usage telemetry event."""

    actor: str
    event_type: str
    measurement_type: str
    value: float | int | None
    unit: str
    measurement_source: MeasurementSource = MeasurementSource.PROVIDER_EXACT
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    event_id: str = ""
    run_id: str | None = None
    task_id: str | None = None
    project_dir: str | None = None
    timestamp: str = ""
    created_at: str = ""

    def __post_init__(self) -> None:
        # Validate / normalize measurement_source
        self.measurement_source = MeasurementSource.from_value(self.measurement_source)

        # Validate value and measurement_source consistency
        if self.measurement_source == MeasurementSource.UNAVAILABLE or self.value is None:
            self.value = None
            self.measurement_source = MeasurementSource.UNAVAILABLE
        else:
            if not isinstance(self.value, (int, float)) or not math.isfinite(float(self.value)):
                raise ValueError(f"Invalid numeric value for UsageEvent: {self.value!r}")
            self.value = float(self.value)

        # Validate and clamp confidence
        if self.measurement_source == MeasurementSource.UNAVAILABLE:
            self.confidence = 0.0
        else:
            if self.confidence is None:
                self.confidence = DEFAULT_SOURCE_CONFIDENCE.get(self.measurement_source, 1.0)
            elif not isinstance(self.confidence, (int, float)) or not math.isfinite(float(self.confidence)):
                self.confidence = DEFAULT_SOURCE_CONFIDENCE.get(self.measurement_source, 1.0)
            self.confidence = max(0.0, min(1.0, float(self.confidence)))


        # Clean string fields
        self.actor = str(self.actor).strip()
        self.event_type = str(self.event_type).strip()
        self.measurement_type = str(self.measurement_type).strip()
        self.unit = str(self.unit).strip()
        if self.run_id is not None:
            self.run_id = str(self.run_id).strip() or None
        if self.task_id is not None:
            self.task_id = str(self.task_id).strip() or None
        if self.project_dir is not None:
            self.project_dir = normalize_project_path(self.project_dir)

        # Timestamps
        self.timestamp = _format_timestamp(self.timestamp)
        if not self.created_at:
            self.created_at = _utc_now_iso()
        else:
            self.created_at = _format_timestamp(self.created_at)

        # Secret-safe metadata redaction
        if isinstance(self.metadata, dict):
            self.metadata = redact_metadata(self.metadata)
        else:
            self.metadata = {"value": redact_metadata(self.metadata)}

        # Deterministic Event ID
        if not self.event_id:
            meta_json = deterministic_json_dumps(self.metadata)
            self.event_id = compute_event_id(
                self.run_id,
                self.actor,
                self.event_type,
                self.measurement_type,
                self.unit,
                self.value,
                self.timestamp,
                meta_json,
                task_id=self.task_id,
            )
        else:
            self.event_id = str(self.event_id).strip()

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dictionary."""
        return {
            "event_id": self.event_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "project_dir": self.project_dir,
            "actor": self.actor,
            "event_type": self.event_type,
            "measurement_type": self.measurement_type,
            "value": self.value,
            "unit": self.unit,
            "measurement_source": self.measurement_source.value,
            "confidence": self.confidence,
            "metadata": self.metadata,
            "timestamp": self.timestamp,
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        """Serialize to deterministic JSON."""
        return deterministic_json_dumps(self.to_dict())

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> UsageEvent:
        """Construct UsageEvent from a dictionary."""
        return cls(
            actor=d.get("actor", "unknown"),
            event_type=d.get("event_type", "unknown"),
            measurement_type=d.get("measurement_type", "unknown"),
            value=d.get("value"),
            unit=d.get("unit", "unknown"),
            measurement_source=d.get("measurement_source", MeasurementSource.PROVIDER_EXACT),
            confidence=float(d.get("confidence", 1.0)),
            metadata=d.get("metadata") if isinstance(d.get("metadata"), dict) else {},
            event_id=d.get("event_id", ""),
            run_id=d.get("run_id"),
            task_id=d.get("task_id"),
            project_dir=d.get("project_dir"),
            timestamp=d.get("timestamp", ""),
            created_at=d.get("created_at", ""),
        )

    @classmethod
    def from_json(cls, s: str) -> UsageEvent:
        """Construct UsageEvent from JSON string."""
        return cls.from_dict(json.loads(s))


@dataclass
class UsageSummary:
    """Safe aggregated summary of usage events without mixing incompatible units."""

    event_count: int = 0
    unavailable_count: int = 0
    totals_by_unit: dict[str, float] = field(default_factory=dict)
    totals_by_measurement_type: dict[str, dict[str, float]] = field(default_factory=dict)
    totals_by_actor: dict[str, dict[str, float]] = field(default_factory=dict)
    events_by_source: dict[str, int] = field(default_factory=dict)
    events_by_type: dict[str, int] = field(default_factory=dict)
    mean_confidence: float = 1.0
    weighted_confidence_by_unit: dict[str, float] = field(default_factory=dict)
    earliest_timestamp: str | None = None
    latest_timestamp: str | None = None

    def total_for(self, unit: str) -> float:
        """Get the safe sum total for a specific unit (e.g. 'tokens', 'seconds')."""
        return self.totals_by_unit.get(unit, 0.0)

    def total_for_measurement(self, measurement_type: str, unit: str | None = None) -> float:
        """Get total for a measurement_type, failing closed on ambiguous mixed units.

        If unit is specified, returns sum for that (measurement_type, unit).
        If unit is None:
          - Returns total if exactly one unit is used for this measurement_type.
          - Returns 0.0 if no events exist for this measurement_type.
          - Raises ValueError if multiple incompatible units are present!
        """
        unit_map = self.totals_by_measurement_type.get(measurement_type, {})
        if unit is not None:
            return unit_map.get(unit, 0.0)
        if not unit_map:
            return 0.0
        if len(unit_map) == 1:
            return next(iter(unit_map.values()))
        raise ValueError(
            f"Cannot safely aggregate mixed incompatible units for measurement_type '{measurement_type}': "
            f"found {list(unit_map.keys())}. Specify explicit unit filter."
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert summary to deterministic dictionary."""
        return {
            "event_count": self.event_count,
            "unavailable_count": self.unavailable_count,
            "totals_by_unit": self.totals_by_unit,
            "totals_by_measurement_type": self.totals_by_measurement_type,
            "totals_by_actor": self.totals_by_actor,
            "events_by_source": self.events_by_source,
            "events_by_type": self.events_by_type,
            "mean_confidence": self.mean_confidence,
            "weighted_confidence_by_unit": self.weighted_confidence_by_unit,
            "earliest_timestamp": self.earliest_timestamp,
            "latest_timestamp": self.latest_timestamp,
        }

    def to_json(self) -> str:
        """Serialize summary to deterministic JSON."""
        return deterministic_json_dumps(self.to_dict())


def aggregate_events(events: Iterable[UsageEvent]) -> UsageSummary:
    """Safely aggregate an iterable of UsageEvents without summing incompatible units."""
    event_list = list(events)
    if not event_list:
        return UsageSummary()

    event_count = len(event_list)
    unavailable_count = 0
    totals_by_unit: dict[str, float] = {}
    totals_by_measurement_type: dict[str, dict[str, float]] = {}
    totals_by_actor: dict[str, dict[str, float]] = {}
    events_by_source: dict[str, int] = {}
    events_by_type: dict[str, int] = {}

    confidence_sum = 0.0
    unit_val_sum: dict[str, float] = {}
    unit_conf_weighted_sum: dict[str, float] = {}
    unit_conf_count: dict[str, int] = {}

    timestamps: list[str] = []

    for ev in event_list:
        # Sources and types
        src_val = ev.measurement_source.value
        events_by_source[src_val] = events_by_source.get(src_val, 0) + 1
        events_by_type[ev.event_type] = events_by_type.get(ev.event_type, 0) + 1

        confidence_sum += ev.confidence
        if ev.timestamp:
            timestamps.append(ev.timestamp)

        # Track confidence by unit
        unit_conf_count[ev.unit] = unit_conf_count.get(ev.unit, 0) + 1

        if ev.value is None or ev.measurement_source == MeasurementSource.UNAVAILABLE:
            unavailable_count += 1
            continue

        val = float(ev.value)

        # Unit totals
        totals_by_unit[ev.unit] = totals_by_unit.get(ev.unit, 0.0) + val

        # Measurement type + unit
        if ev.measurement_type not in totals_by_measurement_type:
            totals_by_measurement_type[ev.measurement_type] = {}
        totals_by_measurement_type[ev.measurement_type][ev.unit] = (
            totals_by_measurement_type[ev.measurement_type].get(ev.unit, 0.0) + val
        )

        # Actor + unit
        if ev.actor not in totals_by_actor:
            totals_by_actor[ev.actor] = {}
        totals_by_actor[ev.actor][ev.unit] = (
            totals_by_actor[ev.actor].get(ev.unit, 0.0) + val
        )

        # Weighted confidence calculation
        unit_val_sum[ev.unit] = unit_val_sum.get(ev.unit, 0.0) + val
        unit_conf_weighted_sum[ev.unit] = unit_conf_weighted_sum.get(ev.unit, 0.0) + (val * ev.confidence)

    # Compute mean and weighted confidences
    mean_confidence = round(confidence_sum / event_count, 6) if event_count > 0 else 1.0
    weighted_confidence_by_unit: dict[str, float] = {}
    for u, count in unit_conf_count.items():
        v_sum = unit_val_sum.get(u, 0.0)
        if v_sum > 0:
            weighted_confidence_by_unit[u] = round(unit_conf_weighted_sum[u] / v_sum, 6)
        else:
            # Fallback to simple average confidence for unit if sum of values is 0
            u_events = [e for e in event_list if e.unit == u]
            u_conf_avg = sum(e.confidence for e in u_events) / len(u_events) if u_events else 1.0
            weighted_confidence_by_unit[u] = round(u_conf_avg, 6)

    earliest_timestamp = min(timestamps) if timestamps else None
    latest_timestamp = max(timestamps) if timestamps else None

    return UsageSummary(
        event_count=event_count,
        unavailable_count=unavailable_count,
        totals_by_unit=totals_by_unit,
        totals_by_measurement_type=totals_by_measurement_type,
        totals_by_actor=totals_by_actor,
        events_by_source=events_by_source,
        events_by_type=events_by_type,
        mean_confidence=mean_confidence,
        weighted_confidence_by_unit=weighted_confidence_by_unit,
        earliest_timestamp=earliest_timestamp,
        latest_timestamp=latest_timestamp,
    )


def get_default_telemetry_db_path() -> Path:
    """Return default telemetry SQLite database path."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base_dir = Path(local_app_data) / "codex-agy-bridge"
    else:
        base_dir = Path.home() / ".local" / "share" / "codex-agy-bridge"
    return base_dir / "telemetry.sqlite3"


class UsageLedger:
    """Thread-safe observational usage telemetry ledger with lazy SQLite persistence.

    Features:
    - Lazy SQLite table creation that does not interfere with existing schemas/tables
    - In-memory cache & querying
    - Deterministic JSON serialization
    - Fail-safe append mode (never breaking operational workflows)
    - Concurrency protection and idempotent event recording
    - Windows path normalization support
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        in_memory: bool = False,
        fail_safe: bool = True,
    ) -> None:
        self.in_memory = in_memory
        self.fail_safe = fail_safe
        self.db_path = Path(db_path) if db_path is not None else (None if in_memory else get_default_telemetry_db_path())
        self._lock = threading.RLock()
        self._events: dict[str, UsageEvent] = {}
        self._conn: sqlite3.Connection | None = None
        self._db_initialized: bool = False

    def _get_connection(self) -> sqlite3.Connection:
        """Get or initialize lazy SQLite connection."""
        if self._conn is not None:
            return self._conn
        if self.db_path is None:
            raise RuntimeError("Cannot open SQLite connection with no db_path specified.")

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,
            check_same_thread=False,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        self._conn = conn
        self._ensure_schema(conn)
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        """Lazily ensure telemetry table exists without modifying other tables."""
        if self._db_initialized:
            return
        conn.execute("BEGIN IMMEDIATE;")
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS telemetry_events (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT,
                    task_id TEXT,
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
            # Backward-compatible migration check for existing telemetry_events table
            cur = conn.cursor()
            cols = [row[1] for row in cur.execute("PRAGMA table_info(telemetry_events);").fetchall()]
            if "task_id" not in cols:
                conn.execute("ALTER TABLE telemetry_events ADD COLUMN task_id TEXT;")

            conn.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_run_id ON telemetry_events(run_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_task_id ON telemetry_events(task_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_project ON telemetry_events(project_dir);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_timestamp ON telemetry_events(timestamp);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_actor_event ON telemetry_events(actor, event_type);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_unit ON telemetry_events(unit);")
            conn.execute("COMMIT;")
            self._db_initialized = True
        except Exception:
            conn.execute("ROLLBACK;")
            raise

    def record(
        self,
        event: UsageEvent | dict[str, Any],
        fail_safe: bool | None = None,
    ) -> UsageEvent | None:
        """Record a usage event idempotently to in-memory store and/or SQLite.

        If fail_safe is True (or default), exceptions during persistence are suppressed
        and logged without interrupting caller flow.
        """
        fs = self.fail_safe if fail_safe is None else fail_safe
        try:
            if isinstance(event, dict):
                ev = UsageEvent.from_dict(event)
            elif isinstance(event, UsageEvent):
                ev = event
            else:
                raise TypeError(f"Expected UsageEvent or dict, got {type(event).__name__}")

            with self._lock:
                # In-memory store (idempotent deduplication by event_id)
                self._events[ev.event_id] = ev

                # Persistent store
                if not self.in_memory and self.db_path is not None:
                    conn = self._get_connection()
                    meta_json = deterministic_json_dumps(ev.metadata)
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO telemetry_events (
                            event_id, run_id, task_id, project_dir, actor, event_type,
                            measurement_type, value, unit, measurement_source,
                            confidence, timestamp, metadata_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            ev.event_id,
                            ev.run_id,
                            ev.task_id,
                            ev.project_dir,
                            ev.actor,
                            ev.event_type,
                            ev.measurement_type,
                            ev.value,
                            ev.unit,
                            ev.measurement_source.value,
                            ev.confidence,
                            ev.timestamp,
                            meta_json,
                            ev.created_at,
                        ),
                    )
            return ev
        except Exception as err:
            logger.warning("UsageLedger record failed: %s", err)
            if not fs:
                raise
            return None

    def record_event(
        self,
        actor: str,
        event_type: str,
        measurement_type: str,
        value: float | int | None,
        unit: str,
        measurement_source: MeasurementSource | str = MeasurementSource.PROVIDER_EXACT,
        confidence: float | None = None,
        metadata: dict[str, Any] | None = None,
        run_id: str | None = None,
        task_id: str | None = None,
        project_dir: str | Path | None = None,
        event_id: str | None = None,
        timestamp: str | datetime | float | None = None,
        fail_safe: bool | None = None,
    ) -> UsageEvent | None:
        """Helper method to construct and record a UsageEvent."""
        src = MeasurementSource.from_value(measurement_source)
        conf = confidence if confidence is not None else DEFAULT_SOURCE_CONFIDENCE.get(src, 1.0)
        ev = UsageEvent(
            actor=actor,
            event_type=event_type,
            measurement_type=measurement_type,
            value=value,
            unit=unit,
            measurement_source=src,
            confidence=conf,
            metadata=metadata if metadata is not None else {},
            event_id=event_id or "",
            run_id=run_id,
            task_id=task_id,
            project_dir=normalize_project_path(project_dir),
            timestamp=_format_timestamp(timestamp) if timestamp is not None else "",
        )
        return self.record(ev, fail_safe=fail_safe)

    def query(
        self,
        run_id: str | None = None,
        task_id: str | None = None,
        project_dir: str | Path | None = None,
        actor: str | None = None,
        event_type: str | None = None,
        measurement_type: str | None = None,
        unit: str | None = None,
        measurement_source: MeasurementSource | str | None = None,
        start_time: str | datetime | float | None = None,
        end_time: str | datetime | float | None = None,
        limit: int | None = None,
    ) -> list[UsageEvent]:
        """Query usage events filtered by run_id, task_id, project, actor, type, unit, and time range."""
        norm_project = normalize_project_path(project_dir)
        norm_src = MeasurementSource.from_value(measurement_source).value if measurement_source else None
        start_iso = _format_timestamp(start_time) if start_time is not None else None
        end_iso = _format_timestamp(end_time) if end_time is not None else None

        with self._lock:
            if not self.in_memory and self.db_path is not None and self.db_path.exists():
                conn = self._get_connection()
                query_sql = "SELECT * FROM telemetry_events WHERE 1=1"
                params: list[Any] = []

                if run_id is not None:
                    query_sql += " AND run_id = ?"
                    params.append(run_id)
                if task_id is not None:
                    query_sql += " AND task_id = ?"
                    params.append(task_id)
                if actor is not None:
                    query_sql += " AND actor = ?"
                    params.append(actor)
                if event_type is not None:
                    query_sql += " AND event_type = ?"
                    params.append(event_type)
                if measurement_type is not None:
                    query_sql += " AND measurement_type = ?"
                    params.append(measurement_type)
                if unit is not None:
                    query_sql += " AND unit = ?"
                    params.append(unit)
                if norm_src is not None:
                    query_sql += " AND measurement_source = ?"
                    params.append(norm_src)
                if start_iso is not None:
                    query_sql += " AND timestamp >= ?"
                    params.append(start_iso)
                if end_iso is not None:
                    query_sql += " AND timestamp <= ?"
                    params.append(end_iso)

                query_sql += " ORDER BY timestamp ASC, created_at ASC"
                if limit is not None and limit > 0:
                    query_sql += f" LIMIT {int(limit)}"

                cur = conn.cursor()
                rows = cur.execute(query_sql, params).fetchall()
                events: list[UsageEvent] = []
                for row in rows:
                    p_dir = row["project_dir"]
                    if norm_project is not None and not paths_equal(p_dir, norm_project):
                        continue
                    try:
                        meta = json.loads(row["metadata_json"])
                    except Exception:
                        meta = {}
                    ev = UsageEvent(
                        event_id=row["event_id"],
                        run_id=row["run_id"],
                        task_id=row["task_id"] if "task_id" in row.keys() else None,
                        project_dir=p_dir,
                        actor=row["actor"],
                        event_type=row["event_type"],
                        measurement_type=row["measurement_type"],
                        value=row["value"],
                        unit=row["unit"],
                        measurement_source=MeasurementSource.from_value(row["measurement_source"]),
                        confidence=float(row["confidence"]),
                        metadata=meta,
                        timestamp=row["timestamp"],
                        created_at=row["created_at"],
                    )
                    events.append(ev)
                return events

            # In-memory evaluation
            matched: list[UsageEvent] = []
            for ev in self._events.values():
                if run_id is not None and ev.run_id != run_id:
                    continue
                if task_id is not None and ev.task_id != task_id:
                    continue
                if norm_project is not None and not paths_equal(ev.project_dir, norm_project):
                    continue
                if actor is not None and ev.actor != actor:
                    continue
                if event_type is not None and ev.event_type != event_type:
                    continue
                if measurement_type is not None and ev.measurement_type != measurement_type:
                    continue
                if unit is not None and ev.unit != unit:
                    continue
                if norm_src is not None and ev.measurement_source.value != norm_src:
                    continue
                if start_iso is not None and ev.timestamp < start_iso:
                    continue
                if end_iso is not None and ev.timestamp > end_iso:
                    continue
                matched.append(ev)

            matched.sort(key=lambda e: (e.timestamp, e.created_at))
            if limit is not None and limit > 0:
                matched = matched[:limit]
            return matched

    def aggregate(
        self,
        run_id: str | None = None,
        task_id: str | None = None,
        project_dir: str | Path | None = None,
        actor: str | None = None,
        event_type: str | None = None,
        measurement_type: str | None = None,
        unit: str | None = None,
        measurement_source: MeasurementSource | str | None = None,
        start_time: str | datetime | float | None = None,
        end_time: str | datetime | float | None = None,
    ) -> UsageSummary:
        """Aggregate usage events filtered by given constraints."""
        events = self.query(
            run_id=run_id,
            task_id=task_id,
            project_dir=project_dir,
            actor=actor,
            event_type=event_type,
            measurement_type=measurement_type,
            unit=unit,
            measurement_source=measurement_source,
            start_time=start_time,
            end_time=end_time,
        )
        return aggregate_events(events)

    def export_events(
        self,
        run_id: str | None = None,
        task_id: str | None = None,
        project_dir: str | Path | None = None,
    ) -> list[dict[str, Any]]:
        """Export matching events as list of JSON-serializable dicts."""
        events = self.query(run_id=run_id, task_id=task_id, project_dir=project_dir)
        return [e.to_dict() for e in events]

    def clear(self) -> None:
        """Clear all in-memory events and delete from database if active."""
        with self._lock:
            self._events.clear()
            if not self.in_memory and self.db_path is not None and self.db_path.exists():
                conn = self._get_connection()
                conn.execute("DELETE FROM telemetry_events;")

    def close(self) -> None:
        """Close database connection."""
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
                self._db_initialized = False


__all__ = [
    "MeasurementSource",
    "UsageEvent",
    "UsageSummary",
    "UsageLedger",
    "TELEMETRY_SCHEMA_VERSION",
    "aggregate_events",
    "compute_event_id",
    "redact_metadata",
    "normalize_project_path",
    "paths_equal",
    "deterministic_json_dumps",
    "get_default_telemetry_db_path",
]
