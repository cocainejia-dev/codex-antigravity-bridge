"""Low-token local watcher for one AGY job.

The watcher polls the durable job store locally. It prints only state changes,
heartbeat loss, and terminal results, so normal heartbeats do not wake a model.
"""
from __future__ import annotations

import argparse
import json
import time

from codex_agy_bridge.agy_jobs import agy_jobs


TERMINAL = {"completed", "failed", "cancelled", "lost"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    parser.add_argument("--interval", type=float, default=15.0)
    parser.add_argument("--stale-after", type=float, default=90.0)
    args = parser.parse_args()
    if args.interval <= 0 or args.stale_after <= 0:
        parser.error("interval and stale-after must be positive")

    previous = None
    while True:
        status = agy_jobs.status(args.job_id)
        state = status.get("state", "unknown")
        heartbeat_age = status.get("heartbeat_age_seconds")
        if state in {"queued", "running"} and heartbeat_age is not None and heartbeat_age > args.stale_after:
            event = {"event": "LOST", "job_id": args.job_id, "heartbeat_age_seconds": heartbeat_age}
            print(json.dumps(event, ensure_ascii=False), flush=True)
            return 2
        fingerprint = (state, status.get("health"), status.get("error"), status.get("text"))
        if fingerprint != previous:
            print(json.dumps({"event": state.upper(), **status}, ensure_ascii=False), flush=True)
            previous = fingerprint
        if state in TERMINAL:
            return 0 if state == "completed" else 1
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
