# README Refresh Design

## Goal

Present the Codex to Antigravity bridge as a polished GitHub project while
keeping enough technical detail for developers to install, configure, and
debug it.

## Scope

- Refresh the root `README.md` as the project landing document.
- Rewrite `mcp-antigravity-bridge/README.md` to remove encoding corruption and
  document the recommended CLI/ConPTY implementation.
- Keep `mcp-server/README.md` focused on the SDK prototype and its limitations.
- Do not change runtime behavior in this README task.
- Commit the documentation and the already verified local implementation
  changes together, then push the current `main` branch through the local Git
  remote.

## Information Architecture

1. Project title and one-sentence value proposition.
2. Architecture diagram and implementation choices.
3. Comparison table for the CLI bridge and SDK prototype.
4. Recommended quick start for the CLI bridge.
5. Codex MCP registration examples.
6. Tool reference and configuration options.
7. Testing, troubleshooting, roadmap, references, and license.

## Writing and Visual Direction

- Chinese-first prose with English names, commands, and API identifiers kept
  intact.
- Short sections, descriptive headings, tables, and copyable code blocks.
- Use only verified project facts; avoid unsupported popularity claims or fake
  status badges.
- Make the recommended path obvious without hiding the SDK prototype.

## Acceptance Criteria

- The root README explains what the project does within the first screen.
- A new user can install and register the recommended bridge by following the
  documented commands.
- The two implementations have distinct, accurate positioning.
- The two subproject READMEs render as readable UTF-8 Markdown.
- Existing tests remain green and no runtime files are changed by the README
  refresh.
- The final changes are committed locally and pushed to `origin/main`.
