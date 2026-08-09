<div align="center">

# Project Progress · Codex × Antigravity

### Current status: CLI bridge ready

<p>
  <img src="https://img.shields.io/badge/runtime-ready-16a34a?style=for-the-badge" alt="Runtime ready">
  <img src="https://img.shields.io/badge/tests-114%20passed-2563EB?style=for-the-badge" alt="114 tests passed">
  <img src="https://img.shields.io/badge/CI-GitHub%20Actions-0ea5e9?style=for-the-badge&logo=githubactions&logoColor=white" alt="GitHub Actions CI">
  <img src="https://img.shields.io/badge/license-Apache--2.0-111827?style=for-the-badge" alt="Apache 2.0 license">
</p>

<p>
  <a href="PROGRESS.md">中文进度</a> ·
  <a href="README.md">English project overview</a> ·
  <a href="docs/README.en.md">Documentation index</a>
</p>

</div>

This document records delivered capabilities, verification evidence, boundaries,
and the next roadmap. The runtime is a local MCP bridge; the Antigravity
desktop application is out of scope.

## Architecture

```text
Codex Desktop / CLI
        |
        | MCP over local stdio
        v
codex-agy-bridge
        |
        | subprocess / ConPTY / pty / visible console
        v
agy -p "..."
        |
        v
Antigravity agent
```

## Runtime Modes

The project has four runtime modes. `headless` and `terminal` are display
options, not separate runtime modes. Supervisor mode is Codex's safety and
acceptance workflow, not another agy process mode.

| # | Mode | Entry point | Behavior |
| :---: | --- | --- | --- |
| 1 | Normal Codex development | No agy tool | Codex works alone; agy is not invoked automatically. |
| 2 | Synchronous delegation | `agy_ask`, `agy_ask_json` | One bounded task; Codex waits for the result. |
| 3 | Async isolated task | `agy_start`, `agy_status` | One agy process in a caller-created worktree; Codex can continue. |
| 4 | Collaboration MVP | `agy_collab_start`, `agy_collab_status` | One task by default, four maximum; each task gets its own branch and worktree. |

### Display Options

| Display mode | Default | Behavior | Platform |
| --- | :---: | --- | --- |
| `headless` | Yes | No window; status and final output return through MCP. | Windows, macOS, Linux |
| `terminal` | No | One visible console per running task with live agy output. | Windows |

### Collaboration Lifecycle

```text
Ask for display preference and task count
        -> define the shared contract and exclusive file ownership
        -> create branches and worktrees
        -> start one agy process per task
        -> Codex continues in its own worktree
        -> inspect status, diff, tests, and acceptance
        -> manually merge verified branches
```

`ready_for_review` only means that agy exited successfully. It is not proof that
the acceptance criteria passed.

## Delivery Status

| Area | Status | Notes |
| --- | :---: | --- |
| CLI bridge runtime | Done | `mcp-antigravity-bridge/` provides the local MCP server. |
| Synchronous MCP tools | Done | `agy_ask` and `agy_ask_json`. |
| Async worktree tools | Done | `agy_start` and `agy_status`. |
| Collaboration MVP | Done | `agy_collab_start` and `agy_collab_status` create isolated worktrees and aggregate status without auto-merging. |
| Live terminal mode | Done | Optional visible Windows console, one per task; headless remains the default. |
| Windows paths and PTY | Done | Non-ASCII workdirs and ConPTY fallback. |
| Supervisor skill | Done | Authorization, permissions, lifecycle, and correction rules. |
| Installation and proxy setup | Done | Python path recovery, proxy detection, and per-user MCP configuration. |
| Documentation | Done | Chinese and English project overviews, progress files, and docs indexes. |
| Continuous integration | Done | Tests, skill validation, and compilation checks. |

## Recent Fixes

- Nonzero agy exits are reported as `failed`, not `completed`.
- Empty direct output triggers PTY fallback and preserves actionable diagnostics.
- `agy_ask_json` rejects unparseable JSON.
- `agy_start` requires an existing isolated worktree.
- Collaboration validates unique task ids, relative owned paths, and non-overlapping ownership.
- Collaboration sessions enforce a maximum of four tasks.
- Terminal mode is opt-in and Windows-only; headless mode remains the default.
- The Codex skill asks for display preference and task count before collaboration.
- Public synchronous and async tools reject non-positive or non-finite timeouts before starting work.

## Verification Evidence

Run from the repository root:

```powershell
python -m pytest -q
python scripts/validate_skill.py
git diff --check
```

Latest verification:

- Repository tests: 114 passed in total.
- Root tests: 67 passed.
- Bridge tests: 47 passed, including the real MCP stdio tool-list smoke test.
- Skill validation passed.
- Compileall passed.
- README, progress, and documentation-index links are covered by tests.

## Scope and Boundaries

- No Antigravity desktop GUI integration.
- No storage or forwarding of OAuth credentials, proxy credentials, or private Codex configuration.
- No automatic delegation of production, irreversible, cross-project, or unclear work.
- `dangerously_skip_permissions` defaults to `false`.
- Collaboration is capped at four tasks, with one agy process and worktree per task.
- `terminal` display mode is currently Windows-only and opens one visible console per running task.
- Async job and collaboration session state is kept in the current bridge process; old ids become unavailable after restart.
- No versioned PyPI release workflow yet.

## Roadmap

### High priority

- Publish a versioned Python package when the command and configuration interfaces stabilize.
- Expand authentication, timeout, and empty-output diagnostics.

### Medium priority

- Explore optional MCP streaming output without changing the one-shot tool contract.
- Add more complete real-machine smoke-check documentation.

### Low priority

- Evaluate a TypeScript or Go implementation if distribution needs justify it.

## Key Files

| File | Purpose |
| --- | --- |
| `README.zh-CN.md` | Chinese project overview |
| `README.md` | English project overview |
| `PROGRESS.md` | Chinese project progress |
| `PROGRESS.en.md` | English project progress |
| `docs/README.md` | Chinese documentation index |
| `docs/README.en.md` | English documentation index |
| `mcp-antigravity-bridge/README.md` | Package installation note |
| `skills/agy-supervisor/SKILL.md` | AGY behavior rules |
| `skills/agy-supervisor/references/` | Protocol and plan references |
| `tests/` | Distribution, skill, and documentation tests |

## References

- [Antigravity CLI](https://github.com/google-antigravity/antigravity-cli)
- [Antigravity CLI documentation](https://antigravity.google/docs/cli/overview)
- [Model Context Protocol](https://modelcontextprotocol.io/)

## License

Apache-2.0
