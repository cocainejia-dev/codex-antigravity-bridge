# AGY Supervisor Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a reusable Codex skill and one-command installer that lets Codex supervise bounded `agy` implementation tasks, including sequential multi-page development.

**Architecture:** Keep `mcp-antigravity-bridge` as the runtime MCP server. Add a concise `skills/agy-supervisor/SKILL.md` containing the delegation contract, and add platform installers that install the local bridge, copy the skill into the user's Codex skills directory, and idempotently register the MCP server. Update the root README with GitHub quick start, invocation rules, supervisor loop, and safety limits.

**Tech Stack:** Markdown skill metadata, Python 3.10+, PowerShell, POSIX shell, Codex MCP CLI, pytest.

## Post-plan extension: parallel worktree mode

The release also adds asynchronous `agy_start` and `agy_status` tools, a
reusable `skills/agy-supervisor/references/agy-development-plan.md` template,
and parallel worktree handoff rules. Codex writes and commits a plan under
`docs/agy-plans/`, creates `.worktrees/agy/<slug>` on branch
`codex/agy-<slug>`, starts AGY there, and continues in its own worktree. The
tracks must have disjoint file ownership; Codex reviews and merges the AGY
branch only after its tests and acceptance criteria pass.

## Global Constraints

- Normal coding requests must not trigger `agy` unless the user explicitly requests Antigravity collaboration or enables supervisor mode.
- `dangerously_skip_permissions` remains `false` unless explicitly authorized for a trusted directory and task.
- Multi-page tasks run sequentially in one worktree; concurrent writes require separate worktrees and are out of scope.
- The bridge does not store OAuth credentials or persistent Antigravity conversation state.
- Preserve the existing untracked `.codegraph/` directory and do not add it to commits.

---

### Task 1: Add the reusable supervisor skill

**Files:**
- Create: `skills/agy-supervisor/SKILL.md`
- Create: `skills/agy-supervisor/agents/openai.yaml`

**Interfaces:**
- Consumes: Codex's `agy_ask(prompt, workdir, timeout, dangerously_skip_permissions)` MCP tool.
- Produces: A procedural skill that Codex can apply to explicit Antigravity collaboration requests.

- [ ] **Step 1: Write the skill frontmatter and mode policy**

  Use this exact frontmatter shape:

  ```yaml
  ---
  name: agy-supervisor
  description: Use when the user explicitly asks Codex to involve Antigravity, agy, or another coding agent, or enables supervisor mode for bounded implementation and multi-page development tasks. Keep Codex as the reviewer and orchestrator while agy performs scoped implementation.
  ---
  ```

  The body must state that ordinary development does not call `agy`; explicit delegation or explicit supervisor-mode opt-in is required.

- [ ] **Step 2: Add the supervisor loop**

  Document the exact sequence: inspect repository, define workdir and ownership, call `agy_ask`, inspect diff/status/tests, send at most two corrective calls after the initial call, then accept or stop with a blocker. Require each prompt to include task scope, forbidden files, acceptance criteria, and verification commands.

- [ ] **Step 3: Add multi-page rules and safety gates**

  Document that delegation requires at least two independently implementable pages, known shared contracts, exclusive file boundaries, and sequential execution. Explicitly prohibit delegation for unresolved shared state, routing/auth/database changes, production operations, secrets, irreversible actions, concurrent writes in one worktree, and unknown workdirs.

- [ ] **Step 4: Add the UI metadata**

  Create `agents/openai.yaml` with:

  ```yaml
  interface:
    display_name: AGY Supervisor
    short_description: Let Codex supervise bounded Antigravity coding tasks.
    default_prompt: Use AGY Supervisor mode for this explicitly delegated coding task.
  ```

- [ ] **Step 5: Validate the skill text**

  Run a static check that the file has valid frontmatter, the required tool name, both modes, multi-page rules, permission defaults, and the three-attempt limit:

  ```powershell
  python scripts/validate_skill.py
  ```

  Expected: `skill validation passed`.

- [ ] **Step 6: Commit**

  ```powershell
  git add skills/agy-supervisor
  git commit -m "feat: add agy supervisor skill"
  ```

### Task 2: Add idempotent platform installers

**Files:**
- Create: `scripts/install.ps1`
- Create: `scripts/install.sh`
- Create: `scripts/validate_skill.py`

**Interfaces:**
- Consumes: The repository root, `mcp-antigravity-bridge/pyproject.toml`, and `skills/agy-supervisor/SKILL.md`.
- Produces: A local bridge installation, a copied Codex skill, and one `codex-agy-bridge` MCP registration.

- [ ] **Step 1: Implement the PowerShell installer**

  `scripts/install.ps1` must:

  1. Resolve its repository root from `$PSScriptRoot`.
  2. Fail with an actionable message if `python` or `codex` is missing.
  3. Run `python -m pip install -e "<root>\mcp-antigravity-bridge[winpty]"`.
  4. Copy `skills\agy-supervisor` to `$env:USERPROFILE\.codex\skills\agy-supervisor`, replacing only that managed destination.
  5. Inspect `codex mcp list`; run `codex mcp add codex-agy-bridge -- python -m codex_agy_bridge` only when the name is absent.
  6. Check `agy --version`; if absent, print the official installation command and continue so the user can install the CLI separately.
  7. Print the one-time interactive login command `agy` and the verification command `agy -p "Reply exactly AGY_OK"`.

  The installer must not print, read, or write OAuth tokens and must return a nonzero exit code for missing Python, failed pip installation, or failed MCP registration.

- [ ] **Step 2: Implement the POSIX installer**

  `scripts/install.sh` must mirror the PowerShell behavior using `${HOME}/.codex/skills/agy-supervisor`, `python3`, `command -v`, `cp -R`, and the same idempotent `codex mcp list` check. Use `set -eu` and quote all paths.

- [ ] **Step 3: Implement the static validator**

  `scripts/validate_skill.py` must locate the repository root relative to its own file and assert:

  ```python
  required = (
      "name: agy-supervisor",
      "description: Use when",
      "agy_ask",
      "Normal mode",
      "Supervisor mode",
      "multi-page",
      "dangerously_skip_permissions=false",
      "three",
  )
  ```

  It must also reject `TODO`, `TBD`, `client_secret`, `refresh_token`, and `access_token`, then print `skill validation passed`.

- [ ] **Step 4: Test installer source behavior without changing user configuration**

  Run:

  ```powershell
  python scripts/validate_skill.py
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install.ps1 -WhatIf
  ```

  The installer should support `-WhatIf` by printing planned package/copy/register actions without running pip, copying files, or changing Codex config. Run `sh -n scripts/install.sh` on POSIX or a shell-compatible CI environment.

- [ ] **Step 5: Commit**

  ```powershell
  git add scripts
  git commit -m "feat: add one-command agy installation scripts"
  ```

### Task 3: Update the GitHub quick-start documentation

**Files:**
- Modify: `README.md`
- Modify: `mcp-antigravity-bridge/README.md`
- Modify: `PROGRESS.md`

**Interfaces:**
- Consumes: The exact installer paths and existing bridge tool signatures.
- Produces: Copy-pasteable setup and usage documentation for new users.

- [ ] **Step 1: Replace the root quick start with the actual GitHub command**

  Document:

  ```powershell
  git clone https://github.com/crazyzhang277/codex-antigravity-bridge.git
  cd codex-antigravity-bridge
  powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
  ```

  Add the POSIX equivalent using `sh scripts/install.sh`.

- [ ] **Step 2: Document login persistence accurately**

  State that users run `agy` interactively once per machine/user profile, credentials are managed by `agy`, and reauthentication can occur after expiry, logout, credential cleanup, or changing machines. State that the bridge and skill never store credentials.

- [ ] **Step 3: Document invocation conditions**

  Include one example that invokes supervisor mode and one ordinary coding request that must not invoke `agy`. Explain that Codex performs the review loop and that each `agy` call is a bounded one-shot task.

- [ ] **Step 4: Document multi-page supervision**

  Include a concrete page breakdown example and state that Codex establishes shared contracts first, assigns exclusive file boundaries, delegates pages sequentially, checks each diff/test result, and stops after three attempts or a blocker.

- [ ] **Step 5: Document security and failure behavior**

  Explain the default permission flag, no concurrent writes in one worktree, no secrets/production/irreversible tasks, and troubleshooting for missing `agy`, authentication required, empty output, and timeout.

- [ ] **Step 6: Commit**

  ```powershell
  git add README.md mcp-antigravity-bridge/README.md PROGRESS.md
  git commit -m "docs: explain agy supervisor workflow"
  ```

### Task 4: Add distribution checks and run verification

**Files:**
- Create: `tests/test_distribution.py`

**Interfaces:**
- Consumes: Skill, metadata, installer, and documentation files.
- Produces: Repeatable checks that the GitHub package contains the required release artifacts.

- [ ] **Step 1: Write distribution tests**

  Add tests that assert the skill and both installers exist, `agents/openai.yaml` has the three interface keys, the validator exits successfully, and README contains the repository clone URL, `agy_ask`, supervisor mode, and multi-page wording.

- [ ] **Step 2: Run focused checks**

  ```powershell
  python -m pytest -q tests/test_distribution.py
  python scripts/validate_skill.py
  python -m pytest -q mcp-antigravity-bridge/tests
  python -m compileall -q mcp-antigravity-bridge/src
  ```

  Expected: all tests pass, the validator prints `skill validation passed`, and compileall produces no output.

- [ ] **Step 3: Inspect the final diff**

  ```powershell
  git diff --check HEAD~4..HEAD
  git status --short
  ```

  Expected: no whitespace errors; only intended tracked files are changed; `.codegraph/` remains untracked and untouched.

- [ ] **Step 4: Commit verification test**

  ```powershell
  git add tests/test_distribution.py
  git commit -m "test: validate agy distribution artifacts"
  ```
