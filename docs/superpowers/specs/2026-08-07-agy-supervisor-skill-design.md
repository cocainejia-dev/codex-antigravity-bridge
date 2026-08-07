# AGY Supervisor Skill Design

**Date:** 2026-08-07

## Goal

Publish a reusable Codex skill that lets Codex supervise Google's Antigravity
`agy` CLI on bounded coding tasks, with a first-class workflow for independent
multi-page development and a fast GitHub installation path.

## Scope

The change adds a distributable skill and installation guidance around the
existing local MCP bridge. It does not replace the bridge, persist Antigravity
conversation state, or store OAuth credentials.

## Architecture

The repository remains the source of truth for the MCP bridge. A skill under
`skills/agy-supervisor/` describes the delegation protocol that Codex follows.
The bridge exposes synchronous `agy_ask` tools and asynchronous
`agy_start`/`agy_status` tools for independent worktree execution.
The protocol has two modes:

- **Normal mode:** Codex completes the task itself and does not call `agy`.
- **Supervisor mode:** Codex calls `agy_ask` only after explicit user intent or
  an explicit opt-in for collaboration, then reviews the result and may send a
  bounded correction task.

The install flow installs the Python bridge, verifies `agy`, registers the
bridge with Codex, and installs the skill into the user's Codex skills
directory. Authentication remains owned by `agy`; the installer only checks
that the CLI is available and directs the user through the first interactive
login.

## Supervisor Workflow

1. Inspect the repository and establish the task scope, working directory,
   acceptance criteria, relevant tests, and files that are out of scope.
2. Decide whether the task is eligible for delegation.
3. Send one bounded prompt to `agy_ask` or `agy_start` with the exact
   `workdir`, allowed files, forbidden files, expected changes, and verification
   commands.
4. Inspect `git diff`, status, test output, and whether changes stayed in scope.
5. Accept the result, send a corrective prompt, or stop and report a blocker.
6. For multiple pages, create independent worktrees, run disjoint Codex and
   AGY tracks in parallel, and run integration checks after both tracks complete.

The supervisor must cap correction attempts at three per subtask. It must stop
when tests pass and acceptance criteria are met, when the agent makes no
meaningful progress, when changes escape scope, or when a user decision or
authentication is required.

## Multi-Page Development

Delegation is eligible when at least two pages are independently implementable,
shared routes/components and data contracts are known, each page has a clear
acceptance checklist, and no other process is editing the same files.

Codex must establish shared contracts first. It must then assign each track an
exclusive file ownership boundary, create an AGY worktree and branch, start
AGY asynchronously, and continue in its own worktree. Parallel calls are only
allowed when every writer has a separate worktree and no file ownership
overlaps.

Do not delegate page work when pages share unresolved state, routing, auth, or
database changes; when the architecture is still undecided; or when a page
requires broad changes to shared infrastructure.

## Safety Rules

- Never call `agy` for an ordinary coding request unless the user explicitly
  requests Antigravity collaboration or enables supervisor mode.
- Require a known project directory and bounded task before any write task.
- Keep `dangerously_skip_permissions=false` unless the user explicitly enables
  it for the named trusted directory and task.
- Do not delegate secrets, production operations, irreversible actions, or
  cross-project writes.
- Do not run concurrent write calls against the same worktree.
- Treat unexpected file changes, authentication prompts, timeouts, and empty
  output as blockers to report rather than silently retrying without limit.
- Never place OAuth tokens or private machine configuration in the repository.

## User-Facing Documentation

The root README will provide a short quick start, explicit normal-mode versus
supervisor-mode examples, multi-page example, login persistence explanation,
security limitations, and troubleshooting. Detailed bridge behavior remains in
`mcp-antigravity-bridge/README.md`.

## Verification

The bridge's existing tests and compile check must continue to pass. New tests
will validate installer command construction/check behavior where practical,
and a static validation will verify the skill frontmatter, required sections,
and absence of credential material. Documentation commands must use the
repository's actual package and MCP entry point.
