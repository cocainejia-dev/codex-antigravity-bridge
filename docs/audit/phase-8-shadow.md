# Phase 8 Unattended Shadow Run Audit

Date: 2026-08-18

## Scope

Ran the complete VNext control plane only against a synthetic Git repository
created under a temporary test root. No AshareAdvisor repository, production
Bridge, global Codex configuration, VNext MCP registration, or cutover action
was touched.

## Shadow Result

The unattended synthetic run used 14 DAG tasks at fixed parallelism one.

- Total tasks: 14
- Auto complete: 11
- Intended failed tasks: 2
- Intended dependency-blocked tasks: 1
- Auto completion rate across eligible tasks: 100%
- Wrong commit: 0
- Out-of-scope accepted: 0
- Lost run: 0
- Duplicate task: 0
- State corruption: 0
- Invariants satisfied: YES

Covered scenarios:

- normal success; linear, branch, and merge dependencies
- first verification failure followed by bounded repair success
- worker interruption and MCP restart recovery
- quota/auth synthetic suspension and same-run recovery
- out-of-scope rejection
- permanent failed dependency blocking
- scheduler and durable-run duplicate protection
- production/cutover policy guard

## Verification

Shadow tests: `7 passed, 1 warning`.

Full VNext suite: `272 passed, 1 warning`.

The warning is the existing `pydantic_settings` incomplete forward-reference
warning from the environment.

## Commits

`a297486 test(vnext): add unattended synthetic shadow run`

## Stop Point

Phase 8 passed. No production MCP modification, VNext MCP registration,
global-config modification, cutover, or real project task was performed.
