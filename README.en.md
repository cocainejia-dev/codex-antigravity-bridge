<div align="center">

# Codex × Antigravity

### A local MCP bridge that connects Codex planning with the headless `agy` CLI

<p>
  <a href="https://github.com/crazyzhang277/codex-antigravity-bridge"><img src="https://img.shields.io/badge/status-active-16a34a?style=for-the-badge" alt="Active"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.10+"></a>
  <a href="https://modelcontextprotocol.io/"><img src="https://img.shields.io/badge/MCP-local%20stdio-111827?style=for-the-badge" alt="Local MCP stdio"></a>
  <a href="README.md">中文项目首页</a>
</p>

<p>
  <a href="PROGRESS.en.md">English progress</a> ·
  <a href="docs/README.en.md">Documentation index</a> ·
  <a href="mcp-antigravity-bridge/README.md">Runtime technical manual</a>
</p>

</div>

> [!IMPORTANT]
> **CLI-only.** This project invokes Antigravity's headless `agy` CLI. It does
> not launch, embed, or control the Antigravity desktop GUI.

## At A Glance

Codex remains responsible for planning, task boundaries, review, testing, and
merging. The bridge exposes six local MCP tools and starts bounded `agy -p`
processes. Collaboration tasks use separate branches and Git worktrees.

```text
Codex Desktop / CLI
        |
        | MCP over local stdio
        v
codex-agy-bridge
        |
        | subprocess / ConPTY / visible console
        v
agy -p "..."
```

## Runtime Modes

The project has four runtime modes:

| Mode | Entry point | Tasks | Codex behavior | Best for |
| --- | --- | :---: | --- | --- |
| Normal Codex development | No agy tool | 0 | Codex works alone | Ordinary development |
| Synchronous delegation | `agy_ask` / `agy_ask_json` | 1 | Codex waits for the result | One-shot analysis or structured output |
| Async isolated task | `agy_start` / `agy_status` | 1 per job | Codex continues in another worktree | One independent delegated task |
| Collaboration MVP | `agy_collab_start` / `agy_collab_status` | 1 by default, 4 maximum | Codex and agy work in parallel | Frontend/backend or other disjoint tracks |

`headless` and `terminal` are display options, not additional runtime modes.
Headless is the default. On Windows, the opt-in terminal mode opens one visible
console per running task and shows live agy output.

## Quick Start

Install the bridge and configure the per-user MCP entry from the repository root:

```powershell
git clone https://github.com/crazyzhang277/codex-antigravity-bridge.git
cd codex-antigravity-bridge
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

Install and log in to Antigravity separately:

```powershell
irm https://antigravity.google/cli/install.ps1 | iex
agy --version
agy
```

For the complete installation, proxy, tool, Windows, collaboration, and
troubleshooting instructions, read the [runtime technical manual](mcp-antigravity-bridge/README.md).

## Collaboration Rules

Before starting collaboration, Codex asks whether the user wants live terminal
output and how many tasks to dispatch. The default is one headless task; the
hard limit is four tasks per session.

Every collaboration task must declare:

- `id`
- `prompt`
- `owned_paths`
- `acceptance`

Owned paths must not overlap. The bridge creates one temporary branch, worktree,
and agy process per task. It never auto-merges, deletes worktrees, or runs
arbitrary verification commands. `ready_for_review` means that agy exited
successfully; Codex must still inspect the diff and run acceptance checks.

## Documentation Map

| Need | Start here |
| --- | --- |
| Chinese project overview | [README.md](README.md) |
| English project overview | [README.en.md](README.en.md) |
| Chinese project progress | [PROGRESS.md](PROGRESS.md) |
| English project progress | [PROGRESS.en.md](PROGRESS.en.md) |
| Runtime installation and troubleshooting | [mcp-antigravity-bridge/README.md](mcp-antigravity-bridge/README.md) |
| All documentation links | [docs/README.en.md](docs/README.en.md) |
| AGY behavior and safety rules | [skills/agy-supervisor/SKILL.md](skills/agy-supervisor/SKILL.md) |
| Design history and implementation plans | [docs/superpowers/](docs/superpowers/) |
| Research notes | [research/codex-antigravity-cases.md](research/codex-antigravity-cases.md) |

## Safety Boundary

- Communication stays on local MCP stdio.
- `dangerously_skip_permissions` defaults to `false`.
- Production operations, secrets, irreversible actions, unclear workdirs, and
  concurrent writes to one worktree remain out of scope.
- OAuth credentials, proxy credentials, and private Codex configuration are not
  stored or forwarded by the bridge.

## Verification

```powershell
python -m pytest -q
python scripts/validate_skill.py
python -m compileall -q .\mcp-antigravity-bridge\src
git diff --check
```

## License

Apache-2.0
