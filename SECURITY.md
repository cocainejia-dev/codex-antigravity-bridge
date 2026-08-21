# Security Policy

## Trust Boundaries and Security Model

This project is a local Model Context Protocol (MCP) bridge. It starts the
user's local `agy` CLI and orchestrates isolated Git worktrees for explicitly
delegated tasks. It does not provide a hosted agent service, credential store,
or automatic merge service.

All Codex communication uses local MCP stdio. The bridge does not bind a
network listener, send telemetry, or read, save, or forward Antigravity OAuth
tokens. Proxy configuration is limited to the MCP server environment and setup
rejects URLs with embedded credentials.

`dangerously_skip_permissions` is disabled by default. Enable it only for a
trusted, reversible task in a reviewed worktree scope.

### Execution and acceptance boundaries

- `agy` is a managed local child process; upstream authentication, rate limits,
  network failures, and Windows ConPTY support remain external provider or
  environment concerns.
- Delegated tasks declare `owned_paths`; scope violations are reported and are
  never silently reverted.
- Workers do not commit, merge, or delete branches. The controller independently
  reviews diffs, runs verification, and owns acceptance and commits.
- Runtime SQLite databases, logs, PIDs, heartbeats, and temporary worktrees may
  contain prompts and workspace metadata. They are machine-local and must never
  be committed or published.

## Reporting a Vulnerability

Do not include tokens, private configuration, private repository contents, or
proxy credentials in a public issue. Report a suspected security problem
privately to the repository maintainer and include:

- the bridge version or commit;
- operating system and Python version;
- a minimal reproduction without secrets;
- the observed and expected behavior.

The bridge reports scope violations and leaves merge/revert decisions to the
user. It does not silently revert files, merge branches, delete worktrees, or
run arbitrary task verification commands.
