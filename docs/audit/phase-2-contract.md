# Phase 2 Task and Run Contract

Date: 2026-08-18

## Scope

Implemented only in the VNext repository. Production Bridge, global Codex
configuration, Antigravity global state, runtime databases, and AshareAdvisor
were not modified.

## Delivered

- `mcp-antigravity-bridge/src/codex_agy_bridge/contracts.py`
  - `TaskContract`
  - `RunRecord`
  - `RunState` with the required 14 states
  - guarded transition graph and verification gate
  - JSON-safe round trips
  - normalized paths and bounded runtime/repair counters
  - recursive credential-like value rejection
- `tests/test_vnext_contracts.py`
  - schema and enum coverage
  - invalid input coverage
  - state transition and COMPLETE gate coverage
  - credential, NaN/Infinity, boolean, and runtime-bound coverage

## Acceptance evidence

- Focused contract tests: pass
- Full VNext suite with the task worktree source on `PYTHONPATH`: `163 passed, 1 warning`
- `git diff --check`: pass
- Changed paths: exactly the two owned paths before integration
- Integrated commit: `73d3af8 feat(vnext): add task and run contracts`

## Gate

- `CONTRACT_SCHEMA_TESTS`: PASS
- `STATE_TRANSITION_TESTS`: PASS
- `LEGACY_BEHAVIOR_REGRESSION`: PASS
- `PHASE_2`: PASS

The first headless attempt was correctly classified as permission-blocked with
no changes. A later repair attempt reported a transient AGY proxy failure, but
the final bounded hardening task completed and was independently verified.
