# Codex AGY Supervisor

PROJECT = Codex <-> Antigravity supervised autonomous bridge
CANONICAL_REPO = D:\CODEX项目\agy-bridge
AUTHORITATIVE_REPO = canonical repository root
ACTIVE_MCP_IDENTITY = codex-agy-vnext

## Startup Protocol

1. Confirm the current root is `CANONICAL_REPO` and read `.recovery/repository-identity.json`.
2. Inspect Git status, HEAD, branch, and worktrees.
3. Read `docs/CURRENT_STATE.md` and `.recovery/current-round.json`.
4. Reconcile machine-local runtime and active AGY jobs.
5. Read the relevant recovery and architecture documents.
6. Only then modify files or dispatch AGY.

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
- All source, test, documentation, release, safe-commit, and AGY worktree
  changes must be based on `CANONICAL_REPO`.
- `D:\软件开发\codex-antigravity-vnext` is `PRE_MIGRATION_REFERENCE_ONLY`.
- `D:\软件开发\codex-antigravity-bridge` is `LEGACY_REFERENCE_ONLY`.
- If the identity marker is absent, invalid, or the current root is not
  canonical, set `WRITE_ALLOWED=NO` and `AGY_DISPATCH_ALLOWED=NO`.
- The canonical MCP must use its repository-local `.venv` interpreter and
  fresh resolved-path provenance; global Python editable packages are not an
  accepted runtime source.
- After each development round, stop at the state recorded in
  `docs/CURRENT_STATE.md`; do not enter R3 automatically.
