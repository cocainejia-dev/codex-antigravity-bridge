# README Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the project documentation into a polished, Chinese-first GitHub README set and push the verified local work to `origin/main`.

**Architecture:** The root README is the project landing page and explains the two implementations. The CLI bridge README documents the recommended production path, while the SDK README documents the experimental in-process path. Runtime code remains unchanged in this documentation pass.

**Tech Stack:** Markdown, Python 3.10+, FastMCP, `google-antigravity`, local Git, GitHub remote `origin`.

## Global Constraints

- Keep Chinese-first prose while preserving English commands, package names, and API identifiers.
- Use only facts already present in the repository and verified local implementation.
- Keep `mcp-antigravity-bridge/` as the recommended CLI/ConPTY path.
- Keep `mcp-server/` as the SDK prototype and document its authentication limitation.
- Do not stage `.codegraph/` or generated cache files.
- Push only through the local Git remote after fresh verification.

---

### Task 1: Rewrite the Root Project README

**Files:**
- Modify: `C:\Users\EDY\Documents\codex调用antigravity\README.md`

**Interfaces:**
- Consumes: `PROGRESS.md`, both subproject READMEs, and the current project layout.
- Produces: A GitHub landing page with quick-start commands and accurate implementation comparison.

- [ ] **Step 1:** Replace the opening with a clear title, one-sentence value proposition, status line, and links to both bridge implementations.
- [ ] **Step 2:** Add the MCP architecture and a CLI-versus-SDK comparison table.
- [ ] **Step 3:** Add the Windows CLI install, editable install, and Codex registration commands.
- [ ] **Step 4:** Add exact tool names, testing instructions, roadmap, research links, and Apache-2.0 license information.

### Task 2: Rewrite the CLI Bridge README

**Files:**
- Modify: `C:\Users\EDY\Documents\codex调用antigravity\mcp-antigravity-bridge\README.md`

**Interfaces:**
- Consumes: `mcp-antigravity-bridge/pyproject.toml`, `src/codex_agy_bridge/server.py`, and `src/codex_agy_bridge/agy_runner.py`.
- Produces: Readable UTF-8 documentation for the recommended CLI/ConPTY implementation.

- [ ] **Step 1:** Replace the corrupted text with Chinese-first sections for purpose, architecture, prerequisites, installation, registration, tools, environment variables, fallback behavior, tests, and references.
- [ ] **Step 2:** Document the exact signatures `agy_ask(prompt, workdir="", timeout=300.0) -> str` and `agy_ask_json(prompt, workdir="", timeout=300.0) -> str`.
- [ ] **Step 3:** Document remedies for missing `agy`, authentication failures, empty non-TTY output, and timeout errors.

### Task 3: Polish the SDK Prototype README

**Files:**
- Modify: `C:\Users\EDY\Documents\codex调用antigravity\mcp-server\README.md`

**Interfaces:**
- Consumes: `mcp-server/pyproject.toml`, `antigravity_mcp/agy_runner.py`, and `antigravity_mcp/server.py`.
- Produces: Accurate SDK setup, authentication, registration, and API documentation.

- [ ] **Step 1:** State that this implementation calls `Agent(LocalAgentConfig(...))` in-process and does not launch `agy`.
- [ ] **Step 2:** Document `cwd`, `api_key`, `model`, SDK environment authentication, and `python -m antigravity_mcp` registration.
- [ ] **Step 3:** Explain that tests mock the SDK and do not require a live authenticated model session.

### Task 4: Verify Documentation and Runtime

**Files:**
- Check: `README.md`, `mcp-antigravity-bridge/README.md`, `mcp-server/README.md`, both test directories.

- [ ] **Step 1:** Search the three READMEs for placeholders and known mojibake markers; expected result is no matches.
- [ ] **Step 2:** Run both existing test suites with the bundled Python runtime plus the repository's installed site-packages; confirm `10 passed` and `4 passed`.
- [ ] **Step 3:** Run `git diff --check` and inspect status; do not stage `.codegraph/` or cache files.

### Task 5: Commit and Push Through Local Git

**Files:**
- Stage the three READMEs, `PROGRESS.md`, the already verified SDK implementation/tests, and the committed design/plan docs.

- [ ] **Step 1:** Stage only intended files and inspect `git status --short`.
- [ ] **Step 2:** Commit with `docs: polish project README and bridge guides`.
- [ ] **Step 3:** Push the current branch with `git push origin main`.
- [ ] **Step 4:** Verify local `main` tracks `origin/main` and the new commit is at the remote tip.
