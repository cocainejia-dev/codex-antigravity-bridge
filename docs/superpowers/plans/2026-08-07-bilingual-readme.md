# 双语言 README 重设计实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 将仓库的两份 README 重写为中文项目入口和 English 技术指南，并以活泼开发者风格发布到 `origin/main`。

**架构：** 根目录 `README.md` 面向 GitHub 访客，突出 CLI-only 架构、快速开始和安全边界；`mcp-antigravity-bridge/README.md` 面向安装和排障，详细说明 FastMCP 工具、`agy` runner、Windows 兼容和验证方式。只修改 Markdown 文档，不改变 Python 运行时代码。

**技术栈：** Markdown、Mermaid、Python 3.10+、FastMCP、Antigravity `agy` CLI、pytest。

## 全局约束

- 根目录 README 使用简体中文；bridge README 使用 English。
- 保留 `agy_ask` 和 `agy_ask_json` 的准确参数签名。
- 只描述 Codex Desktop/CLI → MCP stdio → `agy` CLI 的 CLI-only 路径。
- 不写入个人路径、代理地址、OAuth 凭据或历史运行日志。
- 使用少量章节 emoji，不让 emoji 替代命令、API 或技术名词。
- 不提交 `.codegraph/`、缓存文件或生成物。

---

### Task 1: Rewrite the Chinese project homepage

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: `PROGRESS.md`, `mcp-antigravity-bridge/pyproject.toml`, current bridge entry points.
- Produces: A concise Chinese GitHub homepage with a runnable quick start and links to the English bridge guide.

- [ ] **Step 1:** Add the title, value proposition, badges, language link, and supported-path warning.
- [ ] **Step 2:** Add the Mermaid architecture, project advantages, and three-step installation flow.
- [ ] **Step 3:** Document both tools, configuration, security, Windows notes, verification, structure, references, and license.
- [ ] **Step 4:** Remove stale SDK/GUI claims and any machine-specific details.

### Task 2: Rewrite the English bridge guide

**Files:**
- Modify: `mcp-antigravity-bridge/README.md`

**Interfaces:**
- Consumes: `src/codex_agy_bridge/server.py` and `src/codex_agy_bridge/agy_runner.py`.
- Produces: UTF-8 English installation and troubleshooting documentation for the supported bridge.

- [ ] **Step 1:** Replace the corrupted document with a clear title, scope, architecture, prerequisites, and installation sections.
- [ ] **Step 2:** Document exact `agy_ask`/`agy_ask_json` signatures and `run_agy` behavior, including `AGY_PATH`, PATH lookup, output cleanup, and PTY fallback.
- [ ] **Step 3:** Add Codex registration, Windows non-ASCII workdir notes, security guidance, verification commands, troubleshooting, structure, references, and license.

### Task 3: Verify and publish

**Files:**
- Verify: `README.md`
- Verify: `mcp-antigravity-bridge/README.md`
- Verify: `mcp-antigravity-bridge/tests/test_smoke.py`
- Verify: `mcp-antigravity-bridge/src/codex_agy_bridge`

- [ ] **Step 1:** Scan both README files for mojibake, stale SDK/GUI wording, private paths, and placeholder text.
- [ ] **Step 2:** Run `git diff --check`.
- [ ] **Step 3:** Run `python -m pytest -q` and `python -m compileall -q src` from `mcp-antigravity-bridge`.
- [ ] **Step 4:** Review the diff and status, stage only the two READMEs and this plan, commit, and push `main` to `origin`.
