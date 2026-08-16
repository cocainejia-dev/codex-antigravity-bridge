"""Durable SQLite3 journal and recovery for asynchronous agy jobs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any

SCHEMA_VERSION = 1
MAX_RESULT_BYTES = 512 * 1024  # 512 KiB
DEFAULT_TERMINAL_RETENTION_DAYS = 30
DEFAULT_TERMINAL_RETENTION_SECONDS = DEFAULT_TERMINAL_RETENTION_DAYS * 86400.0


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_default_db_path() -> Path:
    """Return default durable database path outside repositories."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base_dir = Path(local_app_data) / "codex-agy-bridge"
    else:
        base_dir = Path.home() / ".local" / "share" / "codex-agy-bridge"
    return base_dir / "jobs.sqlite3"


def compute_prompt_hash(prompt: str) -> str:
    """Compute sha256 hex digest for prompt without persisting sensitive prompt text."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def truncate_result_text(text: str) -> tuple[str, bool]:
    """Cap result UTF-8 bytes at 512 KiB, returning truncated text and truncation flag."""
    encoded = text.encode("utf-8")
    if len(encoded) <= MAX_RESULT_BYTES:
        return text, False
    truncated_bytes = encoded[:MAX_RESULT_BYTES]
    truncated_text = truncated_bytes.decode("utf-8", errors="ignore")
    return truncated_text, True


class DurableJobStore:
    """SQLite-backed journal for job persistence, state reconciliation, and recovery."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else get_default_db_path()
        self._lock = threading.RLock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = None
        try:
            conn = sqlite3.connect(
                str(self.db_path),
                timeout=30.0,
                check_same_thread=False,
                isolation_level=None,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            return conn
        except Exception as err:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            if "DURABLE_SCHEMA_UNSUPPORTED" in str(err):
                raise
            raise RuntimeError(f"DURABLE_STORE_ERROR: {err}") from err

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_meta';"
                )
                if cur.fetchone() is not None:
                    cur.execute(
                        "SELECT value FROM schema_meta WHERE key='schema_version';"
                    )
                    row = cur.fetchone()
                    if row is None:
                        raise RuntimeError(
                            "DURABLE_SCHEMA_UNSUPPORTED: schema_meta table exists but schema_version key is missing"
                        )
                    try:
                        version = int(row["value"])
                    except (ValueError, TypeError):
                        version = -1
                    if version != SCHEMA_VERSION:
                        raise RuntimeError(
                            f"DURABLE_SCHEMA_UNSUPPORTED: durable schema version '{row['value']}' is unsupported (expected {SCHEMA_VERSION})"
                        )
                else:
                    conn.execute("BEGIN IMMEDIATE;")
                    try:
                        conn.execute(
                            """
                            CREATE TABLE IF NOT EXISTS schema_meta (
                                key TEXT PRIMARY KEY,
                                value TEXT NOT NULL
                            );
                            """
                        )
                        conn.execute(
                            "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', ?);",
                            (str(SCHEMA_VERSION),),
                        )
                        conn.execute(
                            """
                            CREATE TABLE IF NOT EXISTS durable_jobs (
                                job_id TEXT PRIMARY KEY,
                                task_key TEXT,
                                state TEXT NOT NULL,
                                health TEXT NOT NULL,
                                recovery_state TEXT,
                                workdir TEXT,
                                submitted_at TEXT NOT NULL,
                                started_at TEXT,
                                completed_at TEXT,
                                elapsed_seconds REAL NOT NULL DEFAULT 0.0,
                                owner_session_id TEXT NOT NULL,
                                heartbeat_at TEXT,
                                last_worktree_activity_at TEXT,
                                exit_code INTEGER,
                                error TEXT,
                                result_text TEXT,
                                result_truncated INTEGER NOT NULL DEFAULT 0,
                                used_pty INTEGER NOT NULL DEFAULT 0,
                                prompt_hash TEXT NOT NULL,
                                created_at TEXT NOT NULL,
                                updated_at TEXT NOT NULL
                            );
                            """
                        )
                        conn.execute(
                            "CREATE INDEX IF NOT EXISTS idx_durable_jobs_task_key ON durable_jobs(task_key);"
                        )
                        conn.execute(
                            "CREATE INDEX IF NOT EXISTS idx_durable_jobs_state ON durable_jobs(state);"
                        )
                        conn.execute(
                            "CREATE INDEX IF NOT EXISTS idx_durable_jobs_updated_at ON durable_jobs(updated_at DESC);"
                        )
                        conn.execute("COMMIT;")
                    except Exception:
                        try:
                            conn.execute("ROLLBACK;")
                        except Exception:
                            pass
                        raise
            except Exception as err:
                if "DURABLE_SCHEMA_UNSUPPORTED" in str(err):
                    raise
                raise RuntimeError(f"DURABLE_STORE_ERROR: {err}") from err
            finally:
                conn.close()

    def reconcile_other_sessions(self, current_session_id: str, now_iso: str) -> int:
        """Mark active jobs from previous/other sessions as unknown/INTERRUPTED."""
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE;")
                try:
                    cur = conn.execute(
                        """
                        UPDATE durable_jobs
                        SET state = 'unknown',
                            health = 'INTERRUPTED',
                            recovery_state = 'interrupted',
                            updated_at = ?
                        WHERE state IN ('submitted', 'queued', 'running')
                          AND owner_session_id != ?;
                        """,
                        (now_iso, current_session_id),
                    )
                    count = cur.rowcount
                    conn.execute("COMMIT;")
                    return count
                except Exception:
                    try:
                        conn.execute("ROLLBACK;")
                    except Exception:
                        pass
                    raise
            except Exception as err:
                if "DURABLE_SCHEMA_UNSUPPORTED" in str(err):
                    raise
                raise RuntimeError(f"DURABLE_STORE_ERROR: {err}") from err
            finally:
                conn.close()

    def reserve_and_create(
        self,
        job_id: str,
        task_key: str | None,
        workdir: str | None,
        prompt_hash: str,
        owner_session_id: str,
        now_iso: str,
    ) -> None:
        """Atomically check task_key duplicate rules and record new submitted job."""
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE;")
                try:
                    if task_key is not None:
                        cur = conn.execute(
                            """
                            SELECT job_id, state, health, recovery_state, owner_session_id
                            FROM durable_jobs
                            WHERE task_key = ?
                            ORDER BY created_at DESC;
                            """,
                            (task_key,),
                        )
                        rows = cur.fetchall()
                        for row in rows:
                            row_job_id = row["job_id"]
                            row_state = row["state"]
                            row_health = row["health"]
                            row_rec = row["recovery_state"]

                            if row_state in ("submitted", "queued", "running"):
                                raise RuntimeError(
                                    f"DUPLICATE_ACTIVE_TASK: task_key '{task_key}' is already active on job {row_job_id}"
                                )
                            if row_rec == "interrupted" or (row_health == "INTERRUPTED" and row_state == "unknown"):
                                raise RuntimeError(
                                    f"RECOVERY_REQUIRED: task_key '{task_key}' is in interrupted state from previous session on job {row_job_id}"
                                )

                    conn.execute(
                        """
                        INSERT INTO durable_jobs (
                            job_id, task_key, state, health, recovery_state, workdir,
                            submitted_at, started_at, completed_at, elapsed_seconds,
                            owner_session_id, heartbeat_at, last_worktree_activity_at,
                            exit_code, error, result_text, result_truncated, used_pty,
                            prompt_hash, created_at, updated_at
                        ) VALUES (
                            ?, ?, 'queued', 'QUEUED', NULL, ?,
                            ?, NULL, NULL, 0.0,
                            ?, ?, NULL,
                            NULL, NULL, NULL, 0, 0,
                            ?, ?, ?
                        );
                        """,
                        (
                            job_id,
                            task_key,
                            workdir,
                            now_iso,
                            owner_session_id,
                            now_iso,
                            prompt_hash,
                            now_iso,
                            now_iso,
                        ),
                    )
                    conn.execute("COMMIT;")
                except Exception:
                    try:
                        conn.execute("ROLLBACK;")
                    except Exception:
                        pass
                    raise
            except Exception as err:
                if any(x in str(err) for x in ("DUPLICATE_ACTIVE_TASK", "RECOVERY_REQUIRED", "DURABLE_SCHEMA_UNSUPPORTED")):
                    raise
                raise RuntimeError(f"DURABLE_STORE_ERROR: {err}") from err
            finally:
                conn.close()

    def mark_started(self, job_id: str, started_at: str, now_iso: str) -> None:
        """Update job to running state in journal."""
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE;")
                try:
                    conn.execute(
                        """
                        UPDATE durable_jobs
                        SET state = 'running',
                            health = 'HEALTHY',
                            started_at = ?,
                            updated_at = ?
                        WHERE job_id = ?;
                        """,
                        (started_at, now_iso, job_id),
                    )
                    conn.execute("COMMIT;")
                except Exception:
                    try:
                        conn.execute("ROLLBACK;")
                    except Exception:
                        pass
                    raise
            except Exception as err:
                if "DURABLE_SCHEMA_UNSUPPORTED" in str(err):
                    raise
                raise RuntimeError(f"DURABLE_STORE_ERROR: {err}") from err
            finally:
                conn.close()

    def update_heartbeat(
        self,
        job_id: str,
        heartbeat_at: str,
        health: str,
        elapsed_seconds: float,
        last_worktree_activity_at: str | None,
        now_iso: str,
    ) -> None:
        """Update heartbeat and watchdog health for an active job."""
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE;")
                try:
                    if last_worktree_activity_at is not None:
                        conn.execute(
                            """
                            UPDATE durable_jobs
                            SET heartbeat_at = ?,
                                health = ?,
                                elapsed_seconds = ?,
                                last_worktree_activity_at = ?,
                                updated_at = ?
                            WHERE job_id = ? AND state IN ('submitted', 'queued', 'running');
                            """,
                            (
                                heartbeat_at,
                                health,
                                float(elapsed_seconds),
                                last_worktree_activity_at,
                                now_iso,
                                job_id,
                            ),
                        )
                    else:
                        conn.execute(
                            """
                            UPDATE durable_jobs
                            SET heartbeat_at = ?,
                                health = ?,
                                elapsed_seconds = ?,
                                updated_at = ?
                            WHERE job_id = ? AND state IN ('submitted', 'queued', 'running');
                            """,
                            (
                                heartbeat_at,
                                health,
                                float(elapsed_seconds),
                                now_iso,
                                job_id,
                            ),
                        )
                    conn.execute("COMMIT;")
                except Exception:
                    try:
                        conn.execute("ROLLBACK;")
                    except Exception:
                        pass
                    raise
            except Exception as err:
                if "DURABLE_SCHEMA_UNSUPPORTED" in str(err):
                    raise
                raise RuntimeError(f"DURABLE_STORE_ERROR: {err}") from err
            finally:
                conn.close()

    def mark_terminal(
        self,
        job_id: str,
        state: str,
        health: str,
        exit_code: int | None,
        error: str | None,
        result_text: str | None,
        result_truncated: bool,
        used_pty: bool,
        started_at: str | None,
        completed_at: str,
        elapsed_seconds: float,
        now_iso: str,
    ) -> None:
        """Record terminal completion or failure in the durable journal."""
        persisted_text = None
        truncated_flag = 0
        if result_text is not None:
            persisted_text, truncated_flag_bool = truncate_result_text(result_text)
            truncated_flag = 1 if (result_truncated or truncated_flag_bool) else 0

        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE;")
                try:
                    conn.execute(
                        """
                        UPDATE durable_jobs
                        SET state = ?,
                            health = ?,
                            exit_code = ?,
                            error = ?,
                            result_text = ?,
                            result_truncated = ?,
                            used_pty = ?,
                            started_at = COALESCE(started_at, ?),
                            completed_at = ?,
                            elapsed_seconds = ?,
                            heartbeat_at = ?,
                            updated_at = ?
                        WHERE job_id = ?;
                        """,
                        (
                            state,
                            health,
                            exit_code,
                            error,
                            persisted_text,
                            truncated_flag,
                            1 if used_pty else 0,
                            started_at,
                            completed_at,
                            float(elapsed_seconds),
                            completed_at,
                            now_iso,
                            job_id,
                        ),
                    )
                    conn.execute("COMMIT;")
                except Exception:
                    try:
                        conn.execute("ROLLBACK;")
                    except Exception:
                        pass
                    raise
            except Exception as err:
                if "DURABLE_SCHEMA_UNSUPPORTED" in str(err):
                    raise
                raise RuntimeError(f"DURABLE_STORE_ERROR: {err}") from err
            finally:
                conn.close()

    def prune_terminal(
        self,
        older_than_seconds: float = DEFAULT_TERMINAL_RETENTION_SECONDS,
        now_iso: str | None = None,
    ) -> int:
        """Prune expired terminal completed/failed jobs older than retention policy.

        Never prunes active (submitted/queued/running) or interrupted/recovery jobs.
        """
        if older_than_seconds < 0:
            raise ValueError("older_than_seconds must be non-negative")

        now_dt = datetime.fromisoformat(now_iso) if now_iso else datetime.now(timezone.utc)
        cutoff_dt = now_dt - timedelta(seconds=older_than_seconds)
        cutoff_iso = cutoff_dt.isoformat()

        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE;")
                try:
                    cur = conn.execute(
                        """
                        DELETE FROM durable_jobs
                        WHERE state IN ('completed', 'failed')
                          AND (recovery_state IS NULL OR recovery_state != 'interrupted')
                          AND health != 'INTERRUPTED'
                          AND COALESCE(completed_at, updated_at, created_at) <= ?;
                        """,
                        (cutoff_iso,),
                    )
                    count = cur.rowcount
                    conn.execute("COMMIT;")
                    return count
                except Exception:
                    try:
                        conn.execute("ROLLBACK;")
                    except Exception:
                        pass
                    raise
            except Exception as err:
                if "DURABLE_SCHEMA_UNSUPPORTED" in str(err):
                    raise
                raise RuntimeError(f"DURABLE_STORE_ERROR: {err}") from err
            finally:
                conn.close()

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        """Retrieve full durable job record by job_id."""
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.execute(
                    """
                    SELECT job_id, task_key, state, health, recovery_state, workdir,
                           submitted_at, started_at, completed_at, elapsed_seconds,
                           owner_session_id, heartbeat_at, last_worktree_activity_at,
                           exit_code, error, result_text, result_truncated, used_pty,
                           prompt_hash, created_at, updated_at
                    FROM durable_jobs
                    WHERE job_id = ?;
                    """,
                    (job_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                return dict(row)
            except Exception as err:
                if "DURABLE_SCHEMA_UNSUPPORTED" in str(err):
                    raise
                raise RuntimeError(f"DURABLE_STORE_ERROR: {err}") from err
            finally:
                conn.close()

    def get_recent(
        self,
        limit: int = 20,
        task_key: str | None = None,
        state: str | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve newest-first summary of recent jobs without full prompt or result."""
        with self._lock:
            conn = self._get_connection()
            try:
                query = """
                    SELECT job_id, task_key, state, health, recovery_state, workdir,
                           submitted_at, started_at, completed_at, elapsed_seconds,
                           owner_session_id, heartbeat_at, last_worktree_activity_at,
                           exit_code, result_truncated, used_pty, prompt_hash,
                           created_at, updated_at
                    FROM durable_jobs
                """
                clauses = []
                params: list[Any] = []
                if task_key is not None:
                    clauses.append("task_key = ?")
                    params.append(task_key)
                if state is not None:
                    clauses.append("LOWER(state) = ?")
                    params.append(state.lower())
                if clauses:
                    query += " WHERE " + " AND ".join(clauses)
                query += " ORDER BY created_at DESC LIMIT ?"
                params.append(max(1, min(100, int(limit))))

                cur = conn.execute(query, tuple(params))
                rows = cur.fetchall()
                results = []
                for row in rows:
                    item = dict(row)
                    item["result_truncated"] = bool(item["result_truncated"])
                    item["used_pty"] = bool(item["used_pty"])
                    results.append(item)
                return results
            except Exception as err:
                if "DURABLE_SCHEMA_UNSUPPORTED" in str(err):
                    raise
                raise RuntimeError(f"DURABLE_STORE_ERROR: {err}") from err
            finally:
                conn.close()
