# Security Policy

## Scope

This project is a local MCP bridge. It starts the user's `agy` CLI and creates
Git worktrees for explicitly delegated tasks. It does not provide a hosted
agent service, credential store, or automatic merge service.

The bridge does not read, save, or forward Antigravity OAuth tokens. Proxy
configuration is limited to the MCP server environment and setup rejects URLs
with embedded credentials.

`dangerously_skip_permissions` is disabled by default. Enable it only for a
trusted, reversible task in a worktree whose scope has been reviewed.

## Reporting

Do not include tokens, private configuration, private repository contents, or
proxy credentials in a public issue. Report a suspected security problem
privately to the repository maintainer and include:

- the bridge version or commit;
- operating system and Python version;
- a minimal reproduction without secrets;
- the observed and expected behavior.

The bridge reports scope violations and leaves the decision to the user. It
does not silently revert files, merge branches, delete worktrees, or run
arbitrary task verification commands.
