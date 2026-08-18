# Phase 7 Crash, Interruption, and Auth Recovery Audit

Date: 2026-08-18

## Scope

Added VNext-only recovery orchestration based on durable run records, process
liveness, heartbeat age, and optional Git worktree evidence. No real account
switch, credential handling, production task, or external investment action
was performed.

## Acceptance Evidence

- Worker crash and dead PID classification: PASS
- MCP restart/orphan `RUNNING` detection: PASS
- Stale heartbeat detection: PASS
- Dirty worktree interruption evidence: PASS
- Verification and pre-commit interruption classification: PASS
- Quota/rate/auth classification to `ACCOUNT_SWITCH_REQUIRED`: PASS
- Same run/task checkpoint preservation: PASS
- Recovery readiness requires explicit evidence: PASS
- Same-run resume without new IDs: PASS
- Inconsistent evidence maps to failure: PASS
- Credential-safe bounded evidence: PASS

## Verification

Focused recovery result: `11 passed, 1 warning`.

Combined recovery/run/scheduler result: `44 passed, 1 warning`.

Full result: `265 passed, 1 warning`.

The warning is the existing `pydantic_settings` incomplete forward-reference
warning from the environment.

## Commit

`f937d5b feat(vnext): add crash and auth recovery orchestration`

## Process Note

The Antigravity job ended with an `AGY_PROXY_ERROR` after producing the
in-scope implementation. Codex independently verified all tests before
accepting it.

## Known Boundary

Phase 8 must exercise these capabilities in a dedicated synthetic shadow
repository with at least 12 tasks. Production cutover remains forbidden.
