"""Low-token local watcher for one AGY job.

The watcher polls the durable job store locally. It prints only state changes,
heartbeat loss, and terminal results, so normal heartbeats do not wake a model.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Resolve the canonical source tree when the watcher is run directly from a
# checkout, without relying on a globally installed editable package.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp-antigravity-bridge" / "src"))
from codex_agy_bridge.durable_jobs import DurableJobStore, get_default_db_path

TERMINAL = {"completed", "failed", "cancelled", "lost", "unknown"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    parser.add_argument("--interval", type=float, default=15.0)
    parser.add_argument("--stale-after", type=float, default=90.0)
    parser.add_argument("--db-path", default=str(get_default_db_path()), help="durable jobs SQLite path")
    args = parser.parse_args()
    if args.interval <= 0 or args.stale_after <= 0:
        parser.error("interval and stale-after must be positive")

    # Keep the watcher read-only.  Constructing the global AgyJobRegistry here
    # would reconcile the MCP process's active jobs as an unrelated session.
    store = DurableJobStore(args.db_path)
    previous = None
    while True:
        record = store.get_job(args.job_id)
        status = record or {"job_id": args.job_id, "state": "unknown", "health": "UNKNOWN", "error": "job not found"}
        state = status.get("state", "unknown")
        heartbeat_age = status.get("heartbeat_age_seconds")
        if heartbeat_age is None and status.get("heartbeat_at"):
            try:
                heartbeat = datetime.fromisoformat(str(status["heartbeat_at"]).replace("Z", "+00:00"))
                heartbeat_age = max(0.0, (datetime.now(timezone.utc) - heartbeat).total_seconds())
            except (TypeError, ValueError):
                heartbeat_age = None
        if heartbeat_age is not None:
            status["heartbeat_age_seconds"] = round(float(heartbeat_age), 3)
        if state in {"queued", "running"} and heartbeat_age is not None and heartbeat_age > args.stale_after:
            event = {"event": "LOST", "job_id": args.job_id, "heartbeat_age_seconds": heartbeat_age}
            print(json.dumps(event, ensure_ascii=False), flush=True)
            return 2
        fingerprint = (state, status.get("health"), status.get("error"), status.get("text"))
        if fingerprint != previous:
            print(json.dumps({"event": state.upper(), **status}, ensure_ascii=False), flush=True)
            previous = fingerprint
        if state in TERMINAL:
            if state == "unknown" and status.get("recovery_state") == "interrupted":
                return 1
            return 0 if state == "completed" else 1
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
