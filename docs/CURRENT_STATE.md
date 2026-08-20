# Current State

## Canonical Authority

- `CANONICAL_REPO`: `D:\软件开发\git仓库\桥接git仓库`.
- `REPOSITORY_IDENTITY`: `codex-agy-supervised-bridge`; role `authoritative`.
- `RUNTIME_RESOLVED_HEAD`: discover at startup; do not write the containing
  commit hash into the recovery anchor.
- `CURRENT_BRANCH`: `main`.
- `CURRENT_PHASE`: `PHASE 11.2R / CANONICAL RUNTIME CUTOVER`.
- `CURRENT_TASK`: `WAITING_FOR_USER_DIRECTION`.
- `STATE`: `WAITING_FOR_USER_DIRECTION`.
- `LAST_VERIFIED_COMMIT`: `3cded09512ade486387d6eb251f31c2d8f393491` (R2).
- R1 effective progress: PASS.
- W1 timeout/liveness semantics: PASS.
- R2 source provenance: PASS.
- `CANONICAL_REPOSITORY_STATUS`: PASS.
- `CANONICAL_RUNTIME_PROVENANCE_STATIC`: PASS; the repository-local `.venv`
  resolves all four bridge modules under the canonical source root.
- `FRESH_CLIENT_RUNTIME_ACCEPTANCE`: PASS.
- `MCP_RUNTIME_CUTOVER`: `PASS`; target identity is
  `codex-agy-vnext`.
- `READY_FOR_R3`: NO.

## Repository Roles

- `D:\软件开发\codex-antigravity-vnext`: `PRE_MIGRATION_REFERENCE_ONLY`.
- `D:\软件开发\codex-antigravity-bridge`: `LEGACY_REFERENCE_ONLY`.
- New source, tests, docs, commits, AGY worktrees, and release operations must
  use `CANONICAL_REPO` only.

## Release Blockers and Deferred Work

- Ruff remains `DEFERRED_EXISTING_ENVIRONMENT_BLOCKER`.
- Fresh-client runtime acceptance: PASS (fresh controller smoke and one
  disposable durable async acceptance).
- MCP runtime cutover: PASS.
- R3 reproducible verification tooling remains frozen pending user direction.
- `NEXT_SAFE_ACTION`: `WAIT_FOR_USER_DIRECTION`.

## Verification

From the canonical repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\handoff-status.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\runtime-provenance.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\verify.ps1
```

The historical VNext/canonical baseline was `281 passed` with one known
`pydantic_settings` warning; that result is not a current-run claim.
Canonical acceptance additionally requires a fresh `.venv` provenance probe
for `codex_agy_bridge.__file__`, `server.__file__`, `agy_jobs.__file__`, and
`agy_runner.__file__`, followed by the configured verification commands.

## Machine-Local Runtime Facts

- AGY executable: `C:\Users\28760\AppData\Local\agy\bin\agy.EXE`.
- Active MCP registration and Codex config live outside Git.
- The global editable legacy `.pth` may remain installed, but canonical MCP
  must use `D:\软件开发\git仓库\桥接git仓库\.venv\Scripts\python.exe` and
  must not import it.
- Durable job databases, processes, heartbeats, and temporary worktrees are
  machine-local evidence, not portable project state.
- Run `scripts/handoff-status.ps1`, then query the durable job store read-only
  and classify every record as `LIVE`, `TERMINAL`, or `AMBIGUOUS` before any
  AGY dispatch.
