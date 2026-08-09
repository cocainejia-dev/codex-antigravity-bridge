# Documentation Index

This is the unified documentation entry point. Project overviews, progress
records, the verification demo, and internal design history have separate homes
so that user guidance is not mixed with implementation records.

> [!WARNING]
> If CC Switch takes over the Codex proxy, a restart or takeover recovery may
> overwrite `%USERPROFILE%\.codex\config.toml`. Run `codex mcp list` to verify
> that the bridge is still registered; use the [Chinese setup instructions](../README.zh-CN.md#cc-switch)
> when it needs to be restored before creating a new conversation.

## Start Here

| Need | Start here |
| --- | --- |
| Chinese overview, installation, and mode selection | [README.zh-CN.md](../README.zh-CN.md) |
| English overview, installation, and mode selection | [Project overview](../README.md) |
| Chinese project progress | [PROGRESS.md](../PROGRESS.md) |
| English project progress | [PROGRESS.en.md](../PROGRESS.en.md) |
| Installation, proxy, MCP tools, and troubleshooting | [English quick start](../README.md#quick-start) · [中文快速开始](../README.zh-CN.md#quick-start) |
| AGY collaboration rules and safety boundaries | [agy-supervisor skill](../skills/agy-supervisor/SKILL.md) |
| Collaboration demo, dry-run, and verification | [Demo](demo.md) |
| Security reporting | [SECURITY.md](../SECURITY.md) |
| Research notes | [research/codex-antigravity-cases.md](../research/codex-antigravity-cases.md) |

## Technical Documentation Layout

### User documentation

The root `README.md` and `README.zh-CN.md` are the single user entry points for
installation, proxy setup, MCP tools, and security boundaries. `docs/demo.md`
contains the real MCP smoke test, collaboration dry-run, and human review steps.

`mcp-antigravity-bridge/README.md` only contains package installation and
development commands for source contributors and package metadata.

### Design and execution history

- [`docs/superpowers/specs/`](superpowers/specs/) contains design decisions and specifications.
- [`docs/superpowers/plans/`](superpowers/plans/) contains implementation plans and execution records.

These files preserve project history and are not the first reading path for new
users.

### Skills and protocols

- [`skills/agy-supervisor/SKILL.md`](../skills/agy-supervisor/SKILL.md) defines when and how Codex may call agy.
- [`skills/agy-supervisor/references/`](../skills/agy-supervisor/references/) contains task contracts, state machines, worktree rules, and correction protocols.

## Naming Convention

- `README.md`: English project entry point.
- `README.en.md`: English compatibility entry point.
- `README.zh-CN.md`: Chinese project entry point.
- `PROGRESS.md`: Chinese project progress.
- `PROGRESS.en.md`: English project progress.
- `mcp-antigravity-bridge/README.md`: package-level installation and development note.
- `mcp-antigravity-bridge/examples/codex-config.toml`: manual MCP configuration example; replace its Python path for the local machine.
- `docs/`: documentation indexes, design history, and long-lived reference material.
