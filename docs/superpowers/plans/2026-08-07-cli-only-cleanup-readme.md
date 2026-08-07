# CLI-Only Bridge Cleanup and README Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the unused SDK prototype while preserving Codex Desktop -> MCP -> `agy` CLI, then publish a polished CLI-only GitHub homepage README.

**Architecture:** Keep `mcp-antigravity-bridge` as the sole runtime integration. It exposes `agy_ask` and `agy_ask_json` over local MCP stdio and launches `agy -p` through the existing runner with Windows ConPTY and POSIX pty fallbacks.

**Tech Stack:** Python 3.10+, `mcp` FastMCP, Antigravity `agy` CLI, pytest, Markdown, Mermaid.

## Global Constraints

- Preserve the public bridge tool names and signatures: `agy_ask(prompt, workdir="", timeout=300.0, dangerously_skip_permissions=False)` and `agy_ask_json(prompt, workdir="", timeout=300.0, dangerously_skip_permissions=False)`.
- Do not add a Python SDK runtime dependency.
- Do not commit `.codegraph/` or generated `__pycache__` / editable-install metadata.
- Keep `research/` and historical design documents as archival material.
- Do not claim desktop GUI control; document only headless `agy` CLI execution.

---

### Task 1: Remove SDK Runtime Surface

**Files:**
- Delete: `mcp-server/README.md`
- Delete: `mcp-server/pyproject.toml`
- Delete: `mcp-server/antigravity_mcp/__main__.py`
- Delete: `mcp-server/antigravity_mcp/server.py`
- Delete: `mcp-server/antigravity_mcp/agy_runner.py`
- Delete: `mcp-server/tests/test_server.py`
- Delete: `mcp-server/tests/test_agy_runner.py`
- Modify: `mcp-antigravity-bridge/pyproject.toml`

**Interfaces:**
- Consumes: The existing CLI bridge package metadata.
- Produces: One supported runtime package with no SDK extra.

- [ ] **Step 1: Remove the standalone SDK files**

Delete the seven files listed above. Do not remove the `research/` SDK snapshots or historical docs.

- [ ] **Step 2: Remove the SDK optional dependency**

Delete only this block from `mcp-antigravity-bridge/pyproject.toml`:

```toml
# Deep agent-loop control via the official Antigravity SDK (multi-turn, streaming)
sdk = ["google-antigravity>=0.1"]
```

Keep the `winpty` and `dev` extras unchanged.

- [ ] **Step 3: Verify the runtime surface is CLI-only**

Run:

```powershell
rg -n "google\.antigravity|LocalAgentConfig|Agent\(|sdk =" mcp-antigravity-bridge mcp-server
```

Expected: no matches, with the command allowed to report that `mcp-server` does not exist. The CLI bridge's `def run_agy` is intentionally retained and is not part of this SDK check.

### Task 2: Rewrite the GitHub Homepage README

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: The existing bridge tool signatures and installation flow.
- Produces: A clean GitHub landing page explaining the supported CLI path.

- [ ] **Step 1: Replace the stale two-path overview**

Write a UTF-8 README with:

```markdown
<div align="center">
<h1>Codex &lt;-&gt; Antigravity Bridge</h1>
<p>Give Codex Desktop a local MCP tool that delegates work to Google's Antigravity CLI.</p>
</div>
```

Follow it with badges, the supported Mermaid flow, a direct statement that the project does not control the Antigravity desktop GUI, and the quick-start commands.

- [ ] **Step 2: Document the two supported tools**

Include a table for `agy_ask` and `agy_ask_json`, preserving their exact parameters and explaining that both ultimately invoke `agy -p`.

- [ ] **Step 3: Add operational sections**

Include Codex TOML configuration, Windows `AGY_PATH` and ConPTY notes, security guidance for `dangerously_skip_permissions`, layered verification commands, troubleshooting, project structure, and license.

- [ ] **Step 4: Check the README for stale SDK claims**

Run:

```powershell
rg -n "mcp-server|google-antigravity|LocalAgentConfig|SDK prototype|Python SDK|run_agy" README.md
```

Expected: no matches.

### Task 3: Synchronize Project Progress Documentation

**Files:**
- Modify: `PROGRESS.md`

**Interfaces:**
- Consumes: The CLI bridge verification state.
- Produces: Progress documentation that matches the remaining runtime.

- [ ] **Step 1: Remove SDK implementation entries**

Remove the SDK prototype from the implementation list, low-priority SDK roadmap item, SDK dependency note, and SDK reference table entry.

- [ ] **Step 2: Record the CLI-only decision**

State that `mcp-antigravity-bridge` is the supported integration and that Codex Desktop/CLI can use the same MCP registration.

- [ ] **Step 3: Verify current-document consistency**

Run:

```powershell
rg -n "mcp-server|Python SDK|SDK prototype|run_agy" PROGRESS.md README.md
```

Expected: no matches in either current document.

### Task 4: Run Verification and Publish

**Files:**
- Verify: `mcp-antigravity-bridge/tests/test_smoke.py`
- Verify: `mcp-antigravity-bridge/src/codex_agy_bridge`
- Verify: Git diff and tracked file list.

**Interfaces:**
- Consumes: The cleaned repository.
- Produces: A verified commit on `main` pushed to `origin`.

- [ ] **Step 1: Run bridge tests**

Run from `mcp-antigravity-bridge`:

```powershell
python -m pytest -q
```

Expected: all bridge tests pass.

- [ ] **Step 2: Run the compile check**

Run from `mcp-antigravity-bridge`:

```powershell
python -m compileall -q src
```

Expected: exit code 0.

- [ ] **Step 3: Review the staged scope**

Run from the repository root:

```powershell
git status --short
git diff --check
git diff --stat
```

Expected: only the SDK deletion, CLI-only documentation, plan/spec records, and bridge metadata changes are present; `.codegraph/` remains untracked and unstaged.

- [ ] **Step 4: Commit the change**

Run:

```powershell
git add README.md PROGRESS.md mcp-antigravity-bridge/pyproject.toml docs/superpowers/specs/2026-08-07-cli-only-cleanup-readme-design.md docs/superpowers/plans/2026-08-07-cli-only-cleanup-readme.md mcp-server
git commit -m "refactor: keep Antigravity integration CLI-only"
```

- [ ] **Step 5: Push to GitHub**

Run:

```powershell
git push origin main
```

Expected: `origin/main` advances to the new commit without pushing `.codegraph/`.
