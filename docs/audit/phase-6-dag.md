# Phase 6 Persistent Task DAG Audit

Date: 2026-08-18

## Scope

Added a VNext-only SQLite-backed Task DAG scheduler. It uses injectable
synthetic runners and enforces `max_parallelism=1`; it does not call
production Bridge or Antigravity directly.

## Acceptance Evidence

- Persistent task records and execution journal: PASS
- READY/BLOCKED_BY_DEPENDENCY/RUNNING/COMPLETE/FAILED states: PASS
- DECISION_REQUIRED and ACCOUNT_SWITCH_REQUIRED suspension preservation: PASS
- Linear dependency chain: PASS
- Branch/merge dependency graph: PASS
- Failed dependency blocks downstream work: PASS
- Scheduler recreation and interrupted RUNNING recovery: PASS
- Duplicate task and duplicate dispatch protection: PASS
- Fixed max parallelism of one: PASS
- Credential-safe task/result persistence: PASS

## Verification

Focused result: `14 passed, 1 warning`.

Full result: `254 passed, 1 warning`.

The warning is the existing `pydantic_settings` incomplete forward-reference
warning from the environment.

## Commit

`a845166 feat(vnext): add persistent task dag scheduler`

## Process Note

The Antigravity job ended with an `AGY_PROXY_ERROR` after leaving the complete
in-scope implementation in the worktree. Codex independently ran the focused
and full test suites before accepting the changes.

## Known Boundary

Crash/auth recovery orchestration and the final synthetic unattended shadow run
remain Phases 7 and 8.
