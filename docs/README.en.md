# Documentation Index

This is the unified documentation entry point. Project overviews, progress
records, release hardening, the verification demo, and internal design history have separate homes
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
| Release-hardening checklist | [RELEASE_HARDENING.md](RELEASE_HARDENING.md) |
| Architecture and recovery | [ARCHITECTURE.md](ARCHITECTURE.md) · [RECOVERY.md](RECOVERY.md) |
| Release plan and phase status | [RELEASE_PLAN.md](RELEASE_PLAN.md) |
| Chinese project progress | [PROGRESS.md](../PROGRESS.md) |
| English project progress | [PROGRESS.en.md](../PROGRESS.en.md) |
| Installation, proxy, MCP tools, and troubleshooting | [English quick start](../README.md#quick-start) · [中文快速开始](../README.zh-CN.md#quick-start) |
| AGY collaboration rules and safety boundaries | [agy-supervisor skill](../skills/agy-supervisor/SKILL.md) |
| Collaboration demo, dry-run, and verification | [Demo](demo.md) |
| Security reporting and trust boundaries | [SECURITY.md](../SECURITY.md) |
| Research notes | [research/codex-antigravity-cases.md](../research/codex-antigravity-cases.md) |

## Technical Documentation Layout

### User documentation and release hardening

The root `README.md` and `README.zh-CN.md` are the single user entry points for
installation, proxy setup, MCP tools, and security boundaries. `docs/demo.md`
contains the real MCP smoke test, collaboration dry-run, and human review steps.

`docs/RELEASE_HARDENING.md` records runtime-state hygiene, source-provenance
checks, provider error boundaries, and the explicit Phase 11.5 boundary.

`mcp-antigravity-bridge/README.md` only contains package installation and
development commands for source contributors and package metadata.

### Architecture and recovery

- [`ARCHITECTURE.md`](ARCHITECTURE.md) defines controller-owned contracts,
  verification, and commits around isolated `agy` worktrees.
- [`RECOVERY.md`](RECOVERY.md) defines read-only runtime-state discovery and
  fail-closed handling for unknown or incompatible state.
- Provider authentication, rate limits, network failures, and ConPTY support
  are external infrastructure boundaries, not silent bridge fallbacks.

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
