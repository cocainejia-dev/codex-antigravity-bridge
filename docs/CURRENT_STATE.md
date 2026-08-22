# Current State

## Canonical Authority

- `CANONICAL_REPO`: `D:\软件开发\git仓库\桥接git仓库`.
- `REPOSITORY_IDENTITY`: `codex-agy-supervised-bridge`; role `authoritative`.
- `RUNTIME_RESOLVED_HEAD`: discover at startup; do not write the containing
  commit hash into the recovery anchor.
- `CURRENT_BRANCH`: `main`.
- `CURRENT_PHASE`: `OPERATIONAL_MVP`.
- `CURRENT_TASK`: `WAITING_FOR_USER_DIRECTION`.
- `STATE`: `OPERATIONAL_MVP_VERIFIED`.
- `LAST_VERIFIED_COMMIT`: discover at startup from `RUNTIME_RESOLVED_HEAD`.
- `OPERATIONAL_MVP`: PASS.
- R1 effective progress: PASS.
- W1 timeout/liveness semantics: PASS.
- R2 source provenance: PASS.
- `CANONICAL_REPOSITORY_STATUS`: PASS.
- `CANONICAL_RUNTIME_PROVENANCE_STATIC`: PASS; the repository-local `.venv`
  resolves all four bridge modules under the canonical source root.
- `FRESH_CLIENT_RUNTIME_ACCEPTANCE`: PASS.
- `MCP_RUNTIME_CUTOVER`: `PASS`; target identity is
  `codex-agy-vnext`.
- `R3_REPRODUCIBILITY_DEFECT`: FIXED.
- `PROJECT_DECLARED_DEV_TOOLING`: YES.
- `RUFF_PROJECT_MANAGED`: YES; version range `>=0.16,<0.17`.
- `RUFF_VERSION_DEFINED`: YES.
- `VERIFY_ENTRYPOINT_REPRODUCIBLE`: YES.
- `FRESH_ENV_INSTALL`: PASS.
- `FRESH_ENV_VERIFY`: PASS.
- `MANUAL_PYTHONPATH_REQUIRED`: NO.
- `GLOBAL_TOOLING_REQUIRED`: NO.
- `GLOBAL_PYTHON_ENV_MUTATED`: NO.
- `READY_FOR_R3`: YES.
- `PHASE11_3_CLEANROOM_E2E`: PASS.
- `CLEANROOM_ENV_INSTALL`: PASS.
- `COLD_START_RECOVERY`: PASS.
- `CONVERSATION_CONTEXT_REQUIRED`: NO.
- `DUPLICATE_WORKER`: 0.
- `EXACTLY_ONE_HARVEST_PER_ROUND`: PASS.
- `PHASE11_4_TECHNICAL_HARDENING`: PASS.
- `PHASE11_4_RELEASE_HARDENING`: PASS.
- `VERSION_SOURCE_OF_TRUTH`: `mcp-antigravity-bridge/pyproject.toml`.
- `VERSION_CONFLICT`: `NO`.
- `API_KEY_MODE`: `NO`.
- `ACCOUNT_SWITCH_PRESERVATION`: PASS.
- `LIVE_AGY_CROSS_ACCOUNT`: PASS.
- `FRESH_CLONE`: PASS.
- `FULL_PYTEST`: PASS (`354 passed, 1 warning`).
- `RUFF_CHANGED_SCOPE`: PASS.
- `COMPILEALL`: PASS.
- `WHEEL_BUILD`: PASS.
- `SDIST_BUILD`: PASS.
- `ARTIFACT_INSTALL_IMPORT`: PASS.
- `ARTIFACT_HYGIENE`: PASS.
- `LOCAL_CI_EQUIVALENT`: PASS.
- `HOSTED_CI`: PASS.
- `NEXT_RECOMMENDED_TASK`: `OPERATIONAL_MVP_MAINTENANCE`.

## Repository Roles

- `D:\软件开发\codex-antigravity-vnext`: `PRE_MIGRATION_REFERENCE_ONLY`.
- `D:\软件开发\codex-antigravity-bridge`: `LEGACY_REFERENCE_ONLY`.
- New source, tests, docs, commits, AGY worktrees, and release operations must
  use `CANONICAL_REPO` only.

## Release Blockers and Deferred Work

- R3 reproducible verification tooling: PASS.
- Phase 11.3 clean-room E2E acceptance: PASS.
- Fresh-client runtime acceptance: PASS (fresh controller smoke and one
  disposable durable async acceptance).
- MCP runtime cutover: PASS.
- `OPERATIONAL_MVP`: PASS.
- `API_KEY_MODE`: `NO`.
- `ACCOUNT_SWITCH_PRESERVATION`: PASS.
- `LIVE_AGY_CROSS_ACCOUNT`: PASS.
- `GITHUB_HOSTED_CI`: PASS.
- `LIVE_AGY_RC_ACCEPTANCE`: PASS.
- `NEXT_SAFE_ACTION`: `WAIT_FOR_USER_DIRECTION`.

## Verification

From the canonical repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\handoff-status.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\runtime-provenance.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\verify.ps1
```

Verification suite produces `354 passed, 1 warning`, Ruff
`0.16.4`, compileall PASS, source provenance PASS, and diff-check PASS. The
warning is the existing `pydantic_settings` incomplete forward-reference
warning. The authoritative entrypoint is `scripts/verify.ps1`; it defaults to
the repository `.venv` and accepts `-Python <path>` for a disposable project
environment.

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
