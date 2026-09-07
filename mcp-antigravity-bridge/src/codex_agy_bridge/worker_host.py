"""Private durable process boundary for production AGY runs.

The MCP process starts this module and then has no ownership of the worker's
lifetime.  The host reads the frozen contract from SQLite and uses the normal
run-control lifecycle, so a fresh MCP process can observe the same run and
PID without replaying it.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from .run_control import DurableRunManager, RunState
from .worker_binding import build_worker_callback


def spawn_worker_host(db_path: str | Path, run_id: str, interpreter: str) -> subprocess.Popen:
    """Start a detached host with stdio disconnected from the MCP process."""
    args = [interpreter, "-m", "codex_agy_bridge.worker_host", "--db-path", str(Path(db_path).resolve()), "--run-id", run_id]
    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        # A detached process group prevents console/pipe teardown from being
        # inherited as a lifetime boundary.  No ConPTY handles are involved.
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(args, **kwargs)  # type: ignore[arg-type]


def _run(db_path: str, run_id: str) -> int:
    manager = DurableRunManager(db_path)
    deadline = time.monotonic() + 30.0
    identity = None
    while time.monotonic() < deadline:
        identity = manager.store.get_worker_identity(run_id)
        if identity and identity.get("launch_state") == "SPAWNED":
            reservation_pid = identity.get("reservation_pid", identity.get("pid"))
            if manager.store.claim_worker_host(run_id, reservation_pid, os.getpid()):
                identity = manager.store.get_worker_identity(run_id)
                break
        time.sleep(0.02)
    else:
        record = manager.store.get_run(run_id)
        if record and record.state not in {RunState.COMPLETE, RunState.FAILED, RunState.CANCELLED}:
            manager._mark_interrupted(record, "Durable worker host reservation was not finalized")
        return 2

    record = manager.store.get_run(run_id)
    contract = manager.store.get_task_contract(run_id)
    if record is None or contract is None:
        return 2
    worker = build_worker_callback(
        contract,
        dangerously_skip_permissions=bool((identity or {}).get("dangerously_skip_permissions")),
    )
    manager._spawn_worker(record, contract, worker, worktree=record.worktree or contract.workdir)
    with manager._active_lock:
        execution = manager._active_executions.get(run_id)
        thread = execution.thread if execution else None
    stop_heartbeat = threading.Event()

    def heartbeat_loop() -> None:
        while not stop_heartbeat.wait(5.0):
            try:
                manager.store.update_heartbeat(run_id)
            except Exception:
                return

    heartbeat_thread = threading.Thread(target=heartbeat_loop, name=f"Heartbeat-{run_id}", daemon=True)
    heartbeat_thread.start()
    if thread is not None:
        thread.join()
    stop_heartbeat.set()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="codex-agy durable worker host")
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)
    return _run(args.db_path, args.run_id)


if __name__ == "__main__":
    raise SystemExit(main())
