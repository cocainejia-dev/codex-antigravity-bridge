# Phase 3 Durable Run Core Audit

Date: 2026-08-18

## Scope

Implemented the VNext-only durable run control core. Production Bridge,
global Codex configuration, Antigravity global state, legacy HTTP bridge, and
AshareAdvisor were not modified.

Owned files:

- `mcp-antigravity-bridge/src/codex_agy_bridge/run_control.py`
- `tests/test_run_control.py`

## Acceptance Evidence

- Explicit caller-supplied SQLite journal path: PASS
- Persist-before-spawn ordering: PASS
- Durable state and monotonic `state_version`: PASS
- Duplicate task and idempotency-key protection: PASS
- Heartbeat and restart-safe observation: PASS
- Dead PID and stale heartbeat detection: PASS
- In-process worker orphan detection after manager recreation: PASS
- Bounded `run_wait` without implicit cancellation: PASS
- Terminal-only `run_result`: PASS
- Cooperative `run_cancel`: PASS
- Credential-safe persisted task and worker metadata: PASS
- `git diff --check`: PASS

## Verification

Command:

```powershell
$env:PYTHONPATH='C:\Users\28760\AppData\Local\codex-agy-vnext\worktrees\phase3-durable-runs\mcp-antigravity-bridge\src'
& 'D:\软件开发\codex-antigravity-vnext\.venv\Scripts\python.exe' -m pytest -q
```

Result: `182 passed, 1 warning`.

The warning is an existing `pydantic_settings` incomplete forward-reference
warning from the environment; no test failed.

## Commit

`01b6a73 feat(vnext): add durable run control core`

## Known Boundary

This phase provides the internal durable run API. MCP exposure, verification
and repair policy, DAG scheduling, recovery orchestration, and the synthetic
shadow run are subsequent phases.
