# Durable Worker Host V1

Production MCP `run_start` uses `EXTERNAL_DURABLE_WORKER`. The controller
persists the frozen `TaskContract` and a reserved worker identity before it
spawns `python -m codex_agy_bridge.worker_host --db-path ... --run-id ...`.
Only bounded identifiers are passed in argv; the prompt and permission mode
are loaded from the durable record.

The worker host disconnects stdin/stdout/stderr from MCP. On Windows it uses
`DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`; on POSIX it starts a new
session. It owns the existing AGY callback until terminal state and writes
heartbeats/results to the same SQLite ledger. A fresh MCP manager reconciles
the persisted `worker_host_pid` and heartbeat and never creates a replacement
worker for the same run.

Injected `WorkerCallback` values remain available through the explicit
`launch_mode="in_process"` path for deterministic unit tests. Orphaned runs
are marked `INTERRUPTED`, never `COMPLETED`; LOW-risk worktree candidates may
only be accepted after scope attribution and independent verification. MEDIUM
and HIGH interrupted partials are rejected.
