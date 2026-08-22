# Codex AGY Supervisor

Codex plans and reviews. Antigravity implements bounded tasks in isolated Git worktrees.

A local MCP bridge that lets Codex delegate independent coding tasks to the
`agy` CLI, inspect the results, and keep the final merge decision with a human.

[Quick Start](#quick-start) | [Demo](docs/demo.md) | [Security](SECURITY.md) | [中文说明](README.zh-CN.md)

## Why this project

Most bridges stop at starting `agy`. This project focuses on the supervision
boundary around a coding task:

- each task declares its owned paths;
- each task can run in its own Git worktree;
- shared contracts, acceptance criteria, and verification commands are explicit;
- status includes changed files and scope-audit results;
- the bridge never auto-merges or silently expands task scope.

## Is this for you?

Use it when you already use Codex and the Antigravity CLI and want to delegate
independent coding tasks without letting workers share the same worktree.

This is not a hosted agent platform, a replacement for Codex, an Antigravity
desktop controller, or an automatic merge bot.

## Quick Start

Prerequisites:

- Python 3.10 or newer;
- Git;
- Codex Desktop or Codex CLI with MCP support;
- Antigravity CLI installed as `agy`;
- one interactive `agy` login before the first real task.

### Source install

```powershell
git clone https://github.com/cocainejia-dev/codex-antigravity-bridge.git
cd codex-antigravity-bridge
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -WhatIf
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

On macOS or Linux:

```bash
git clone https://github.com/cocainejia-dev/codex-antigravity-bridge.git
cd codex-antigravity-bridge
WHAT_IF=1 sh scripts/install.sh
sh scripts/install.sh
```

The setup command installs the packaged `agy-supervisor` skill, registers the
MCP server with the current Python interpreter, and updates only the managed
proxy variables for this server. It does not read, save, or print OAuth tokens.

### Package install

These are the intended release paths once `codex-agy-bridge` is available from
the package index:

```powershell
pipx install codex-agy-bridge
codex-agy-bridge-setup

uv tool install codex-agy-bridge
codex-agy-bridge-setup
```

For a local editable install while developing the bridge:

```powershell
python -m pip install -e ".\mcp-antigravity-bridge[dev,winpty]"
codex-agy-bridge-setup --what-if
```

### Interactive login and Doctor check

```powershell
agy --version
agy
agy -p "Reply exactly AGY_OK"
codex mcp list
codex-agy-bridge doctor
```

Once `AGY_OK` is returned and `codex-agy-bridge` appears in your MCP list, the bridge is ready.

### Codex trigger and delegation

Instruct Codex clearly to delegate tasks or initiate collaboration:

- **Bounded task delegation**: "Enable supervisor mode. Delegate bounded implementation of module X to Antigravity with owned_paths=[...]."
- **Parallel multi-track collaboration**: Ask Codex to run collaboration mode; it will partition frontend/backend tasks and invoke `agy_collab_start`.

### Quota switch and account resumption

When hitting rate limits or quota exhaustion:

1. The durable supervisor transitions the run to `ACCOUNT_SWITCH_REQUIRED`, **preserving the entire worktree and uncommitted progress**.
2. Run `agy` in your terminal to perform an interactive login/account switch.
3. Call `run_resume(db_path, run_id, account_switched=True)` to resume the run in-place on the same worktree without losing work.

### Proxy configuration

Use `AGY_PROXY_URL`, `PROXY_URL`, or `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`
for a proxy. An explicit setup proxy can be passed as
`codex-agy-bridge-setup --proxy-url http://127.0.0.1:7890`; use
`--no-proxy` to remove the managed proxy entries. URLs containing credentials
are rejected so they cannot be copied into Codex configuration.

### Runtime state, recovery, and diagnostics

Runtime databases, logs, PIDs, heartbeats, coverage caches, and temporary
worktrees are machine-local and excluded from Git. They may contain prompts or
workspace metadata and must not be published. Use these read-only diagnostics
before retrying an interrupted task:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\handoff-status.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\runtime-provenance.ps1
```

The repository identity is portable; its recorded local path is only a hint.
Provenance still fails closed unless the repository-local interpreter is used
and bridge modules resolve beneath the current source tree. Unknown runtime
state, missing `agy`, authentication failures, rate limits, network/proxy
errors, and Windows ConPTY problems are reported without silent legacy fallback.

## Runtime Modes

| Mode | Entry point | Worktree | Best for |
| --- | --- | --- | --- |
| Normal Codex development | No agy tool | Current worktree | Ordinary local development |
| Synchronous delegation | `agy_ask` / `agy_ask_json` | Caller-selected | One bounded analysis task |
| Async isolated task | `agy_start` / `agy_status` | Caller-created | One independent implementation task |
| Collaboration MVP | `agy_collab_start` / `agy_collab_status` | Bridge-created | Independent frontend, backend, or test tracks |

`headless` is the default display mode. `terminal` opens one visible Windows
console per collaboration task. A session is capped at four tasks.

## Thirty-second workflow

Start with `dry_run=true` to validate the repository, base ref, task contract,
owned-path overlap, branches, worktree paths, and acceptance metadata without
starting `agy` or creating a worktree:

```json
{
  "project_dir": "C:/work/my-app",
  "base_ref": "HEAD",
  "dry_run": true,
  "shared_contract": "Frontend consumes GET /api/items with id and name.",
  "display_mode": "headless",
  "tasks": [
    {
      "id": "backend",
      "prompt": "Implement GET /api/items and its tests.",
      "owned_paths": ["backend/"],
      "acceptance": ["API tests pass"],
      "verification": ["python -m pytest backend"]
    },
    {
      "id": "frontend",
      "prompt": "Implement the items page against the shared contract.",
      "owned_paths": ["frontend/"],
      "acceptance": ["Frontend tests pass"]
    }
  ]
}
```

Run the same request with `dry_run=false` only after the plan is clear. Poll
`agy_collab_status(session_id)` and review each task's branch, changed files,
uncommitted files, `scope_status`, and `scope_violations`. `ready_for_review`
means only that the process exited successfully; it is not acceptance proof.

## Tools

The bridge provides a layered MCP tool architecture separating basic agy delegation tools from durable supervisor/recovery tools:

### Basic Delegation Tools (`agy_*`)

| Tool | Purpose |
| --- | --- |
| `agy_ask` | One bounded synchronous CLI task (`agy -p`) |
| `agy_ask_json` | One task with validated JSON output |
| `agy_start` | Async work in a caller-created isolated worktree |
| `agy_status` | Poll an async job |
| `agy_wait` | Bounded wait for async task completion without cancelling |
| `agy_jobs_recent` | Inspect recent async task history |
| `agy_collab_start` | Validate a contract and start up to four isolated tasks |
| `agy_collab_status` | Aggregate task results and changed-file scope audits |

### Durable Supervisor & Recovery Tools (`run_*`)

| Tool | Purpose |
| --- | --- |
| `run_start` | Start a durable run tracking a `TaskContract` (persists `CREATED` record in SQLite `db_path`, auto-spawns bounded worker) |
| `run_status` | Inspect durable `RunRecord` JSON from SQLite |
| `run_observe` | Check process/heartbeat liveness and expose recovery state (`is_alive`, `is_stale`, `recovery_state`) |
| `run_wait` | Wait for a run to reach a terminal state within a bounded timeout without cancelling |
| `run_result` | Retrieve terminal result evidence (raises error if non-terminal) |
| `run_cancel` | Cooperatively request cancellation for a run |
| `run_resume` | Resume a suspended durable run on its existing task and worktree after an account switch or credential refresh |

## Security and limits

- communication stays on local MCP stdio;
- `dangerously_skip_permissions` defaults to `false`;
- the bridge does not store or forward OAuth credentials;
- the bridge does not auto-merge, delete worktrees, or execute arbitrary verification commands;
- scope violations are reported, not silently reverted;
- `ready_for_review` does not mean that acceptance criteria passed;
- production, irreversible, cross-project, and unclear tasks remain out of scope.

See [SECURITY.md](SECURITY.md) for reporting guidance and [docs/demo.md](docs/demo.md)
for a reproducible dry-run and live-demo checklist.

## Release status

Version `0.1.0` has passed OPERATIONAL_MVP verification and technical hardening, backed by 354 automated tests and deterministic CI. The authoritative version lives in `mcp-antigravity-bridge/pyproject.toml`; package imports read installed metadata and do not maintain a second release constant. Deterministic CI does not require an Antigravity account, Google AI Pro, or any API key. See [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md).

## Development

```powershell
python -m pip install -e ".\mcp-antigravity-bridge[dev,winpty]"
python -m pytest -q
python scripts/validate_skill.py
python -m compileall -q mcp-antigravity-bridge/src
python -m build mcp-antigravity-bridge
git diff --check
```

Tests mock the `agy` process boundary and do not require a live login.

## Documentation

- [中文项目首页](README.zh-CN.md)
- [Demo and verification checklist](docs/demo.md)
- [Documentation index](docs/README.en.md)
- [Supervisor skill](skills/agy-supervisor/SKILL.md)
- [Contributing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)

## License

Apache-2.0
