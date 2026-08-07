---
name: agy-supervisor
description: Use when the user explicitly asks Codex to involve Antigravity, agy, or another coding agent, or enables supervisor mode for bounded implementation and multi-page development tasks. Keep Codex as the reviewer and orchestrator while agy performs scoped implementation.
---

# AGY Supervisor Skill

## Normal mode

Do not call `agy_ask`, `agy_ask_json`, or `agy_start` during ordinary
development. Use delegation only when the user explicitly asks for Antigravity
involvement or explicitly opts into Supervisor mode.

## Supervisor mode

Use this sequence:

1. Inspect the repository and requested scope.
2. Define the workdir, risk class, authorization, and exclusive file ownership.
3. Build the required delegation prompt and choose the permission mode.
4. Call `agy_ask`, `agy_ask_json`, or `agy_start`.
5. Inspect status, diff, worktree boundaries, output, and tests.
6. Accept only a verified success; otherwise make at most two evidence-based
   corrective calls, then stop with the exact blocker.

Every delegation prompt must include task scope, owned files, forbidden files,
acceptance criteria, verification commands, permission mode, authorization, and
the requested report fields. The copyable template, risk matrix, result state
machine, lifecycle checklist, correction protocol, and pressure cases are in
[`references/agy-supervisor-protocol.md`](references/agy-supervisor-protocol.md).
Parallel worktree plans live under `docs/agy-plans/` and start with
`Status: READY_FOR_AGY`; create and validate the caller-owned AGY worktree,
pass it as `workdir` to `agy_start`, and inspect `git worktree list` before and
after delegation. The bridge does not create worktrees for `agy_start`.

### Tool and output rules

- Apply the same scope and permission contract to `agy_ask`, `agy_ask_json`, and
  `agy_start`; `agy_ask_json` additionally requires parseable JSON matching the
  requested output schema.
- `agy_start` requires an existing caller-created isolated worktree as `workdir`;
  an empty or non-directory workdir is rejected.
- `dangerously_skip_permissions=false` is the default. Enable it only after
  explicit authorization for the exact trusted worktree and task.
- `agy_status` may report `queued`, `running`, `completed`, `failed`, or
  `unknown`. An `unknown` job is not a success: preserve the worktree, inspect
  it manually, and do not restart blindly.
- A result is `succeeded` only with usable output, acceptable exit status, no
  permission/authentication error, and passing acceptance criteria.
- There are three total AGY calls per delegated task: one initial call and at
  most two corrective calls. Never issue a fourth call or widen ownership.

### Hard boundaries

Do not delegate unresolved shared state, routing, authentication, database,
production, secrets, irreversible operations, concurrent writes in one
worktree, or unknown workdirs. Destructive and production work is blocked by
default; production operations remain forbidden even with full access.

## Authorization checkpoint

Ask once whether to enable AGY collaboration when the task is substantial,
needs at least two independently implementable pages/modules, or benefits from
a second implementation track and the user has not already opted in. Do not
ask for trivial fixes, read-only analysis, secrets, production operations, or
irreversible actions. If the user declines, continue in Normal mode.

## Parallel worktree gate

Use parallel mode only when shared contracts are known, file boundaries are
exclusive, and Codex and AGY can work in different worktrees. Create and commit
the plan under `docs/agy-plans/`, create the AGY branch/worktree, start with
`agy_start`, poll with `agy_status`, and merge only after the post-delegation
audit and acceptance criteria pass. Use `agy_ask` only as a synchronous fallback
when asynchronous tools are unavailable.

Stop when acceptance criteria and tests pass, there is no meaningful progress,
scope changes, a permission/authentication blocker appears, a timeout occurs,
or a user decision is required.

Never store OAuth tokens or private machine configuration. See the reference
protocol before every delegated implementation and the plan template before
parallel worktree collaboration.
