# CLI-Only Bridge Cleanup and README Design

**Date:** 2026-08-07

## Goal

Make the repository explicitly CLI-only for Codex integrations: keep the working Codex MCP bridge that launches `agy`, remove the separate Python SDK prototype, and present the project with a polished GitHub homepage README.

## Scope

- Delete the standalone `mcp-server/` SDK prototype, including its MCP tool, implementation, tests, package metadata, and README.
- Remove the unused `sdk` optional dependency from `mcp-antigravity-bridge/pyproject.toml`.
- Keep the existing `mcp-antigravity-bridge` runtime and public tools unchanged: `agy_ask` and `agy_ask_json`.
- Rewrite the root `README.md` as the GitHub homepage.
- Update `PROGRESS.md` so it describes the CLI-only architecture and current verification state.
- Keep `research/` and historical design documents as archival material; they are not supported runtime entry points.

## Architecture

The supported path is:

```text
Codex Desktop or Codex CLI
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

The bridge remains a local stdio MCP server. It locates `agy` through `AGY_PATH`, `PATH`, or platform defaults, handles non-ASCII Windows workdirs, retries through a pseudo-terminal when direct stdout is empty, and returns cleaned text to Codex.

This repository does not launch or control the Antigravity desktop GUI. The supported integration invokes the Antigravity CLI headlessly.

## README Information Architecture

The new homepage README will use a compact developer-tool layout:

1. Centered title, value proposition, and badges.
2. One architecture diagram that makes the process boundary explicit.
3. Three-step quick start for CLI installation, bridge installation, and Codex registration.
4. Tool reference for `agy_ask` and `agy_ask_json`.
5. Configuration, Windows compatibility, security, and troubleshooting sections.
6. Verification commands and a concise project tree.

The README will use clean UTF-8 Markdown and avoid presenting the removed SDK as an available path.

## Acceptance Criteria

- `mcp-server/` is absent from the committed tree.
- `mcp-antigravity-bridge/` still exposes `agy_ask` and `agy_ask_json`.
- The bridge package has no `google-antigravity` SDK extra.
- Root documentation describes only the supported CLI bridge.
- Bridge unit tests and compile checks pass.
- The final commit is pushed to `origin/main`.
