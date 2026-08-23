---
name: agy-supervisor
description: Use when the user explicitly asks Codex to involve Antigravity, agy, or another coding agent, or enables supervisor mode for bounded implementation and multi-page development tasks. Keep Codex as the reviewer and orchestrator while agy performs scoped implementation.
---

# AGY Supervisor Skill

## Normal mode

Do not call delegation or durable run tools (such as `agy_ask`, `agy_ask_json`, `agy_start`, `agy_collab_start`, or `run_start`) during ordinary
development. Use delegation only when the user explicitly asks for Antigravity
involvement or explicitly opts into Supervisor mode.

## Supervisor mode

Use this sequence:

1. Inspect the repository and requested scope.
2. Define the workdir, risk class, authorization, and exclusive file ownership.
3. Build the required delegation prompt / TaskContract and choose the permission mode.
4. Call `agy_ask`, `agy_ask_json`, `agy_start`, `agy_collab_start`, or `run_start` (for durable runs).
5. Inspect status, diff, worktree boundaries, output, and tests.
6. Accept only a verified success; otherwise make at most two evidence-based
   corrective calls, then stop with the exact blocker.
7. For durable runs, retrieve terminal result evidence via `run_result(db_path, run_id)`
   using the exact `run_id` (never `--latest` guessing), independently verify task execution,
   and append the concise Chinese usage report link and absolute local path fallback to the final response.

Every delegation prompt must include task scope, owned files, forbidden files,
acceptance criteria, verification commands, permission mode, authorization, and
the requested report fields. The copyable template, risk matrix, result state
machine, lifecycle checklist, correction protocol, exact-run usage report contract,
and pressure cases are in
[`references/agy-supervisor-protocol.md`](references/agy-supervisor-protocol.md).
Parallel worktree plans live under `docs/agy-plans/` and start with
`Status: READY_FOR_AGY`; create and validate the caller-owned AGY worktree,
pass it as `workdir` to `agy_start` or `run_start`, and inspect `git worktree list` before and
after delegation. The bridge does not create worktrees for `agy_start`.

### Tool and output rules

- **Basic agy delegation tools**:
  - `agy_ask`: synchronous bounded CLI task (`agy -p`).
  - `agy_ask_json`: structured JSON output with schema validation, parseable JSON, and the requested output schema.
  - `agy_start`: async task in a caller-created isolated worktree (`workdir`); rejected if workdir is missing.
  - `agy_status` / `agy_wait`: poll or bounded-wait for async task completion.
  - `agy_jobs_recent`: inspect recent async task history.
  - `agy_collab_start` / `agy_collab_status`: multi-task collaboration creating separate worktrees for up to 4 tasks.
- **Durable supervisor & recovery tools (`run_*`)**:
  - `run_start`: persists `CREATED` state and `TaskContract` in SQLite `db_path`, auto-spawning a bounded worker.
  - `run_status` / `run_observe` / `run_wait`: inspect durable `RunRecord`, check heartbeat and process liveness, and wait for terminal states.
  - `run_result`: retrieves verified terminal results (rejected if non-terminal) and automatically generates an exact-run Chinese HTML usage report (`<run_id>.html`).
  - `run_cancel`: cooperatively requests run cancellation.
  - `run_resume`: resumes a suspended run (e.g. `ACCOUNT_SWITCH_REQUIRED`) on its existing worktree and contract after account switch or credential refresh.
- **Exact-run usage report final-response contract**:
  - Supervisor must query `run_result` or usage report APIs using the exact durable `run_id` (never rely on `--latest` guessing).
  - `run_result` returns `usage_report_status` (`READY` or `FAILED`), `usage_report_path` (absolute local path), `usage_report_uri` (`file:///...`), and `usage_report_reason`.
  - When `usage_report_status == "READY"`, append a concise Chinese report link to the exact `<run_id>.html` file and provide the absolute local path fallback.
  - When `usage_report_status == "FAILED"`, report generation failure is isolated: it must never alter the verified task result or trigger a worker rerun.
  - Token/quota metrics remain `UNAVAILABLE`; call share is observational and labeled `DERIVED` (`调用占比`); secrets are redacted.
- `dangerously_skip_permissions=false` is the default. Enable it only after
  explicit authorization for the exact trusted worktree and task.
- `agy_status` may report `queued`, `running`, `completed`, `failed`, or
  `unknown`. An `unknown` job is not a success: preserve the worktree, inspect
  it manually, and do not restart blindly.
- A result is `succeeded` only with usable output, acceptable exit status, no
  permission/authentication error, and passing acceptance criteria.
- There are three total AGY calls per delegated task: one initial call and at
  most two corrective calls. Never issue a fourth call or widen ownership.

## Collaboration mode MVP

Use this mode only after the user explicitly requests Codex and AGY to develop
the same project in parallel. It is appropriate for independently implementable
tasks such as a frontend/backend split, not for shared routing, authentication,
database migrations, or concurrent edits to one file.

Call `agy_collab_start` with the repository root, a shared contract, and a task
list. Every task must declare `id`, `prompt`, `owned_paths`, and `acceptance`;
owned paths must be mutually exclusive. The bridge creates a temporary branch
and worktree for each task, then starts bounded AGY jobs. Codex may continue
working in its own worktree while those jobs run.

Before starting a collaboration session, ask the user once:

```text
是否需要打开实时终端显示 agy 的执行过程？默认：否。
本次准备派几个独立任务给 agy？默认：1，最多：4。
```

If the user gives no choice, use `display_mode="headless"` and one task. Do
not start more than four tasks in one session. Use `display_mode="terminal"`
only on Windows and only after the user opts into visible output; it opens one
terminal window per running task and still requires the normal diff and test
review.

Poll `agy_collab_status` until the session is `ready_for_review` or `failed`.
`ready_for_review` means only that AGY exited successfully. Codex must still
inspect each branch, run the acceptance checks, review `diff_check` and
uncommitted changes, and merge manually. The MVP never auto-merges, deletes
worktrees, or executes arbitrary verification commands.

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
the plan under `docs/agy-plans/`. For the MVP, `agy_collab_start` may create the
temporary worktrees; otherwise create the caller-owned worktree and use
`agy_start`. Poll with `agy_collab_status` or `agy_status`, and merge only after
the post-delegation audit and acceptance criteria pass. Use `agy_ask` only as a
synchronous fallback when asynchronous tools are unavailable.

Stop when acceptance criteria and tests pass, there is no meaningful progress,
scope changes, a permission/authentication blocker appears, a timeout occurs,
or a user decision is required.

Never store OAuth tokens or private machine configuration. See the reference
protocol before every delegated implementation and the plan template before
parallel worktree collaboration.
