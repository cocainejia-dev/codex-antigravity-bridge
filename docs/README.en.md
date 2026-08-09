# Documentation Index

This is the unified documentation entry point. Project overviews, progress
records, runtime instructions, and internal design history have separate homes
so that user guidance is not mixed with implementation records.

> [!WARNING]
> If CC Switch takes over the Codex proxy, a restart or takeover recovery may
> overwrite `%USERPROFILE%\.codex\config.toml`, removing MCP entries and other
> Codex settings. An MCP marked enabled in CC Switch's management database does
> not prove that it exists in Codex's live configuration. Run `codex mcp list`
> and `Get-Content "$env:USERPROFILE\.codex\config.toml"`, then follow the
> [runtime recovery instructions](../mcp-antigravity-bridge/README.md#cc-switch-configuration-ownership-and-recovery).
> Track the upstream bug in [CC Switch issue #6265](https://github.com/farion1231/cc-switch/issues/6265).

## Start Here

| Need | Start here |
| --- | --- |
| Chinese overview, installation, and mode selection | [README.zh-CN.md](../README.zh-CN.md) |
| English overview, installation, and mode selection | [Project overview](../README.md) |
| Chinese project progress | [PROGRESS.md](../PROGRESS.md) |
| English project progress | [PROGRESS.en.md](../PROGRESS.en.md) |
| Installation, proxy, MCP tools, and troubleshooting | [Runtime technical manual](../mcp-antigravity-bridge/README.md) |
| AGY collaboration rules and safety boundaries | [agy-supervisor skill](../skills/agy-supervisor/SKILL.md) |
| Collaboration demo and verification | [Demo](demo.md) |
| Security reporting | [SECURITY.md](../SECURITY.md) |
| Research notes | [research/codex-antigravity-cases.md](../research/codex-antigravity-cases.md) |

## Technical Documentation Layout

### Runtime documentation

`mcp-antigravity-bridge/README.md` is the technical manual for the Python MCP
package. It stays beside `pyproject.toml`, `src/`, and `tests/` so package
developers can find installation and runtime details in one place. It is not the
project landing page; start with the root `README.md` or `README.zh-CN.md` instead.
The package manual is maintained in English; the root project overviews and
progress records are available in both Chinese and English.

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
- `mcp-antigravity-bridge/README.md`: package-level runtime technical manual.
- `mcp-antigravity-bridge/examples/codex-config.toml`: manual MCP configuration example.
- `docs/`: documentation indexes, design history, and long-lived reference material.
