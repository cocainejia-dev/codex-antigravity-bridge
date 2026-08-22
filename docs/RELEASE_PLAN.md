# Release Plan

## Phase 11.2R / R3 Reproducible Verification Tooling

- R1 effective progress: PASS.
- W1 timeout/liveness semantics: PASS.
- R2 verification source provenance: PASS.
- Last verified commit: `414cd43`.
- Canonical repository status: PASS.
- Static canonical runtime provenance: PASS.
- Fresh-client runtime acceptance: PASS.
- MCP runtime cutover: PASS.
- R3 reproducibility defect: FIXED.
- Project-declared dev tooling: YES; Ruff `>=0.16,<0.17`.
- Fresh environment verification: PASS (`281 passed, 1 warning`).
- Current state: WAITING_FOR_USER_DIRECTION.
- Current task: WAITING_FOR_USER_DIRECTION.
- Current release blockers: none for R3.
- Next safe action: WAIT_FOR_USER_DIRECTION.
- READY_FOR_R3: YES.
- READY_FOR_NEXT_RELEASE_STABILIZATION_STEP: YES.

R3 reproducible verification tooling is complete. This plan records stable
project status, not a live runtime process list or durable job database state.

## Phase 11.3 / Clean-Room E2E Acceptance

- Clean-room environment install: PASS.
- Clean-room baseline: PASS (`281 passed, 1 warning`).
- Three consecutive disposable AGY mutation rounds: PASS.
- Cold/recovery boundary: PASS.
- Bounded wait/reconciliation boundary: PASS.
- Exactly-one harvest per round: PASS.
- Duplicate worker count: `0`.
- Final clean-room verification: PASS (`284 passed, 1 warning`).
- Acceptance fixture commits were not merged to `main`.
- Phase state: `WAITING_FOR_USER_DIRECTION`.
- Ready for release hardening: YES.
- Next safe action: `WAIT_FOR_USER_DIRECTION`.

## Phase 11.5 / CI, packaging, and release candidate

- Version source of truth: `mcp-antigravity-bridge/pyproject.toml`.
- Package runtime metadata reads the installed distribution version and keeps
  source-tree imports safe without inventing a release version.
- CI covers Windows/Linux, Python 3.10/3.12, tests, Ruff, compileall, wheel and
  sdist builds, artifact installation/import, and package hygiene.
- Live AGY acceptance is intentionally deferred until deterministic and package
  gates are independently accepted.
- Final account-authenticated live RC acceptance passed exactly one direct
  smoke and one durable no-write job; no retry or replacement worker was used.
- Phase 11.5 Technical RC: PASS. GitHub Hosted CI remains pending Phase 11.6A.
- GitHub publication, tagging, release creation, and package upload remain
  forbidden in this phase.

## Subsequent phases

- Phase 11.4: release hardening: PASS. Public documentation, runtime-state
  hygiene, portable identity diagnostics, fail-closed provenance guidance,
  security boundaries, and release metadata are complete.
- Phase 11.5: CI, packaging, and release candidate (in progress).
- Phase 11.6: GitHub publication.

Phase 11.4 is complete. Do not begin Phase 11.5 without explicit user
direction; the current state is `WAITING_FOR_USER_DIRECTION`.

This file records confirmed status and next steps, not chat history.
