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

## Authorization checkpoint

Ask the user whether to enable AGY collaboration when the task is substantial,
has at least two independently implementable pages or modules, or would benefit
from a second implementation track, but the user has not explicitly requested
Antigravity. Ask at most once for the task. Do not ask for trivial fixes,
read-only analysis, or tasks involving secrets, production operations, or
irreversible actions.

If the user declines, continue in Normal mode. If the user agrees, create the
plan before starting AGY.

## Parallel worktree mode

Use this mode when Codex and AGY should develop the same project at the same
time. Never let them write to the same worktree or the same file.

1. Create `docs/agy-plans/YYYY-MM-DD-<slug>.md` from the bundled plan template.
2. Fill in the goal, Codex-owned files, AGY-owned files, worktree path,
   forbidden files, acceptance criteria, test commands, permission setting,
   and stop conditions. Set `Status: READY_FOR_AGY`.
3. Commit the plan on the current branch so the AGY worktree can read it.
4. Create an isolated worktree and branch:

   ```text
   git worktree add .worktrees/agy/<slug> -b codex/agy-<slug> HEAD
   ```

5. Start AGY asynchronously with `agy_start`, passing the AGY worktree as
   `workdir` and instructing it to read the plan and modify only its owned
   files. Poll the returned job id with `agy_status` while Codex continues in
   its own worktree.
6. When AGY finishes, inspect its worktree diff and tests. Merge its branch
   only after the plan's acceptance criteria pass and no forbidden files were
   changed.

Use `agy_ask` instead of `agy_start` only when asynchronous tools are
unavailable; that is a synchronous fallback and does not provide simultaneous
development.

The handoff prompt must be concise and point to the committed plan:

```text
Read <absolute path to docs/agy-plans/...md>. Implement only the AGY-owned tasks
in that plan from the assigned worktree. Do not edit the plan, Codex-owned files,
or forbidden files. Run the listed verification commands and report changed
files, tests, and blockers.
```

Parallel delegation requires at least two independently implementable pages or
modules, known shared contracts, exclusive file boundaries, and a clean
worktree relationship. Do not parallelize unresolved shared state, routing,
authentication, database changes, or shared infrastructure.

Do not delegate work with unresolved shared state, routing changes, authentication changes, database changes, production operations, secrets, irreversible actions, concurrent writes in one worktree, or unknown workdirs.

Set `dangerously_skip_permissions=false` by default. Enable it only when the user gives explicit authorization for a trusted task.

Stop when acceptance criteria pass, tests pass, there is no meaningful progress, the work is out of scope, an authentication or permission blocker appears, a timeout occurs, or a user decision is required.

Never store OAuth tokens or private machine configuration.

Limit each delegated task to three total AGY calls: one initial call and at
most two corrective calls. For parallel mode, treat each worktree task as one
delegated task and stop polling when the plan is complete, a blocker occurs,
or the worker changes scope.
