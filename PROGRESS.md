# Codex to Antigravity Bridge: Progress

## Goal

Let Codex Desktop and Codex CLI delegate bounded coding tasks to Google's Antigravity agent through the `agy` CLI and a local MCP server.

## Current Architecture

```text
Codex Desktop or CLI
        |
        | MCP over stdio
        v
codex-agy-bridge
        |
        | subprocess / ConPTY / pty
        v
agy -p "..."
        |
        v
Antigravity agent
```

## Completed

### Research

- Reviewed the official Antigravity CLI headless mode and MCP documentation.
- Compared community bridges and Windows terminal workarounds.
- Captured source notes under `research/`.

### Implementation

- Built `mcp-antigravity-bridge/` as the supported runtime.
- Exposed `agy_ask` for normal text responses.
- Exposed `agy_ask_json` for structured CLI output.
- Added `AGY_PATH`, `PATH`, and platform-default binary discovery.
- Added Windows non-ASCII workdir handling.
- Added ConPTY and POSIX pty fallback when direct stdout is empty.
- Added ANSI and TUI output cleanup.
- Added local unit tests and compile checks.
- Verified a Codex tool call can delegate a small task to Antigravity through the bridge.
- Removed the unused in-process integration prototype so the repository has one supported runtime path.

## Remaining Work

### High Priority

- Add CI for the bridge test suite.
- Publish a versioned package when the command and configuration surface stabilize.

### Medium Priority

- Add optional streaming output without changing the one-shot tool contract.
- Add clearer authentication and timeout diagnostics.

### Low Priority

- Explore a TypeScript or Go implementation if distribution requirements justify it.

## Technology

- Python 3.10+
- MCP over local stdio
- FastMCP from the `mcp` package
- Antigravity `agy` CLI in headless mode
- Optional `pywinpty` for Windows ConPTY fallback
- pytest for local verification

## References

- [Antigravity CLI](https://github.com/google-antigravity/antigravity-cli)
- [Antigravity CLI documentation](https://antigravity.google/docs/cli/overview)
- [Model Context Protocol](https://modelcontextprotocol.io/)
- [Project research notes](research/codex-antigravity-cases.md)
