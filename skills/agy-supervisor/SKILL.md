---
name: agy-supervisor
description: Use when the user explicitly asks Codex to involve Antigravity, agy, or another coding agent, or enables supervisor mode for bounded implementation and multi-page development tasks. Keep Codex as the reviewer and orchestrator while agy performs scoped implementation.
---

# AGY Supervisor Skill

## Normal mode

Do not call `agy_ask` during ordinary development. Use delegation only when the user explicitly asks for Antigravity involvement or explicitly opts into Supervisor mode.

## Supervisor mode

Use this sequence:

1. Inspect the repository and the requested scope.
2. Define the workdir and file ownership before delegating.
3. Call `agy_ask` with a scoped prompt.
4. Inspect the diff, status, and tests after the call.
5. Send at most two corrective calls after the initial call.
6. Accept the result or stop with a blocker.

Require every `agy_ask` prompt to include task scope, forbidden files, acceptance criteria, and verification commands.

Allow multi-page delegation only when there are at least two independently implementable pages, known shared contracts, exclusive file boundaries, and sequential execution.

Do not delegate work with unresolved shared state, routing changes, authentication changes, database changes, production operations, secrets, irreversible actions, concurrent writes in one worktree, or unknown workdirs.

Set `dangerously_skip_permissions=false` by default. Enable it only when the user gives explicit authorization for a trusted task.

Stop when acceptance criteria pass, tests pass, there is no meaningful progress, the work is out of scope, an authentication or permission blocker appears, a timeout occurs, or a user decision is required.

Never store OAuth tokens or private machine configuration.

Limit each task to three total `agy_ask` calls: one initial implementation call and at most two corrective calls.
