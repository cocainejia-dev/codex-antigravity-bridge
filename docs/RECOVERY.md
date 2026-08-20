# Recovery Protocol

For a new client with no chat history:

1. Run `git rev-parse HEAD`.
2. Run `git branch --show-current`.
3. Run `git status --short`.
4. Read `git log -10 --oneline`.
5. Read `AGENTS.md`.
6. Read `docs/CURRENT_STATE.md`.
7. Read `.recovery/current-round.json`.
8. Run `scripts/handoff-status.ps1` to discover machine-local runtime.
9. Query durable AGY jobs if the bridge is available.
10. Classify every job as `LIVE`, `TERMINAL`, or `AMBIGUOUS`.
11. Inspect current diff and worktree ownership.
12. Select the next safe action only after reconciliation.

Never blindly redispatch the current task. A timeout requires inspection of the
durable record, process/PID, heartbeat, worktree activity, output, and diff.
Preserve ambiguous or partial work until independently reviewed.
