# Codex AGY Supervisor

PROJECT = Codex <-> Antigravity supervised autonomous bridge
AUTHORITATIVE_REPO = this repository root

## Startup Protocol

1. Inspect Git status, HEAD, branch, and worktrees.
2. Read `docs/CURRENT_STATE.md` and `.recovery/current-round.json`.
3. Reconcile machine-local runtime and active AGY jobs.
4. Read the relevant recovery and architecture documents.
5. Only then modify files.

Mandatory work mode: read `docs/WORK_MODE.md` first. The original full
work-mode document is machine-local and must not be assumed to be in Git.

## Invariants

- Read before write; diagnose before fixing; keep scope minimal.
- Completion requires real tests, logs, and diff evidence.
- RPC timeout is not worker death; timeout retry requires reconciliation.
- Duplicate workers are forbidden.
- Worker execution success is not implementation progress.
- Verification must use the target worktree source and fail closed on mismatch.
- Antigravity implements bounded code tasks by default; Codex owns planning,
  review, verification, and commits.
- Never reset, clean, revert, or overwrite unknown user changes.
- Do not rely on old chat history.
- After each development round, stop at `WAITING_FOR_USER_DIRECTION`.
