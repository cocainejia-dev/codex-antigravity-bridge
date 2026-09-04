# Repository and Runtime Inventory

`D:\CODEX项目\agy-bridge` is the sole authoritative repository,
development worktree, verification source, release source, and Codex handoff
entry point.

`D:\软件开发\codex-antigravity-vnext` is `PRE_MIGRATION_REFERENCE_ONLY`.
It preserves the pre-migration history and must not receive new source, test,
documentation, release, or AGY worktree changes.

`D:\软件开发\codex-antigravity-bridge` is `LEGACY_REFERENCE_ONLY`.
It is a historical/production reference clone and must not be used as the
canonical verification or MCP source. Its global editable installation may
remain machine-local; do not delete or modify it during normal consolidation.

AppData durable databases, old worktrees, reviewer relays, chat transcripts,
Antigravity caches, OAuth/token state, and user Codex configuration are runtime
or private state. They are not portable project inputs and must not be added to
Git.
