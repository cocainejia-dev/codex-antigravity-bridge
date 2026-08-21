# Durable Run Manager Lifecycle Fix

## Goal

Prevent the MCP `run_start` wrapper from persisting an in-process worker identity when no worker callback is available. A later per-request manager must not reinterpret that run as an orphaned callback worker.

## Scope

- Change the `server.py` `run_start` wiring only, plus focused tests.
- Preserve `DurableRunManager.run_start(worker=...)` and its exactly-once worker lifecycle.
- Use the existing created/queued semantics and worker identity fields; add no state or schema.
- Do not modify provider, Gemini, MCP configuration, `agy_runner`, or Phase 11.5 code.

## Design

The MCP wrapper has no callback binding. It will call `DurableRunManager.run_start` with `auto_spawn=False` and an explicit non-worker `queued` identity. This prevents the manager's default inference from recording `in_process` ownership. A real callback caller continues to pass `worker` and uses the existing spawn path unchanged.

## Ordering Invariant

For the MCP wrapper, the durable row is created as a non-running created/queued run with no in-process ownership. For callback callers, ownership and running transitions remain inside `_spawn_worker` after the callback thread is created. Recovery detection remains active for genuinely started in-process workers whose manager loses the active thread.

## Verification

Add a regression covering the no-callback MCP path and retain tests for valid callback execution, manager recreation recovery, duplicate protection, and read-only contracts. Run focused tests, full pytest, Ruff, compileall, diff checks, and source provenance.
