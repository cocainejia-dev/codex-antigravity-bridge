# Recovery Protocol

This repository is the only authoritative development and handoff source:

`CANONICAL_REPO = D:\CODEX项目\agy-bridge`

For a new client with no chat history:

1. Confirm the current root is canonical and read
   `.recovery/repository-identity.json`. If it is missing or says anything
   other than `canonical_repository=true`, fail closed.
2. Run `git rev-parse HEAD`, `git branch --show-current`,
   `git status --short`, `git log -10 --oneline`, and `git worktree list`.
3. Read `AGENTS.md`, `docs/CURRENT_STATE.md`, and
   `.recovery/current-round.json`.
4. Run `scripts/handoff-status.ps1`.
5. Discover the machine-local durable AGY store read-only. Do not copy,
   migrate, delete, or commit its SQLite files.
6. Inspect process liveness, heartbeat, worktree activity, and late diffs.
   Classify every relevant job as `LIVE`, `TERMINAL`, or `AMBIGUOUS`.
7. Before dispatch, run `scripts/runtime-provenance.ps1` and attest a fresh
   canonical `.venv` child process resolves all bridge modules under the
   canonical source root.
8. Only after those checks may the canonical controller dispatch a bounded AGY
   job from a canonical-derived worktree.

The canonical runtime identity is `codex-agy-vnext`. Its MCP command must use
the absolute interpreter:

`D:\CODEX项目\agy-bridge\.venv\Scripts\python.exe -m codex_agy_bridge`

The global Python editable legacy package is evidence only. It must not affect
canonical imports, and it must not be removed or modified as part of recovery.

Never blindly redispatch a task. A timeout, client change, path change, or RPC
disconnect is not worker death. Reconcile the durable record, process/PID,
heartbeat, worktree activity, output, and diff first. Preserve ambiguous or
partial work until independently reviewed.

The old repositories are read-only references:

- `D:\软件开发\codex-antigravity-vnext` = `PRE_MIGRATION_REFERENCE_ONLY`.
- `D:\软件开发\codex-antigravity-bridge` = `LEGACY_REFERENCE_ONLY`.

If the current root is either reference repository, set
`WRITE_ALLOWED=NO` and `AGY_DISPATCH_ALLOWED=NO`.
