---
title: "Codex → Antigravity 集成案例调研"
date: 2026-08-05
---
# Codex → Antigravity 集成案例调研

## 执行摘要

截至 2026 年 8 月，Google Antigravity 已形成完整的产品矩阵：IDE（v2.5.0）、CLI（`agy`，v1.1.10）、SDK（Python `google-antigravity`，v0.1.9）和统一 MCP 支持。官方已正式提供 CLI Headless 模式（`agy -p "prompt"`）和 Python SDK，两者均支持非交互式调用，为跨代理集成打下了基础。

GitHub 生态中尚无成熟、维护良好的"Codex → Antigravity"专用集成库，但已出现若干值得关注的路径：

- **CLI 桥接**（最直接）：`sshahzaiib/agy-bridge`（MCP server，将 Antigravity CLI 暴露给其他代理）、`rhishi99/agy-headless-bridge`（修复 headless 空输出问题，含 Windows ConPTY）
- **API 网关**：`router-for-me/CLIProxyAPI`（46k+★，将 Antigravity/Codex/Claude Code 包装为统一 API）、`funny-vibes/agent-vibes`（协议翻译网关）
- **多代理协调**：`aannoo/hcom`（跨终端代理通信）、`hristo2612/jinn`（多代理编排守护进程）

**推荐路径**：优先选择 **MCP Server** 架构——在 Codex 中注册一个自定义 MCP Server，该 server 内部通过 `agy -p` headless 调用 Antigravity CLI 并返回结构化结果。理由：Codex 原生支持 MCP，CLI headless 模式已正式稳定，且社区已有多个可参考实现（`agy-bridge`、`agy-headless-bridge`）。若需更深度的 agent loop 控制（多轮对话、流式响应、工具调用），可选用 **Python SDK**（`google-antigravity`）作为底层。

---

## 官方接口现状

### 产品矩阵（2026-08）

| 产品 | 版本 | 类型 | 官方仓库 | 文档入口 |
|------|------|------|----------|----------|
| Antigravity IDE | v2.5.0 | GUI（桌面/浏览器） | 未开源（仅文档） | [antigravity.google/docs](https://antigravity.google/docs) |
| Antigravity CLI | v1.1.10 | 终端 TUI + Headless | [google-antigravity/antigravity-cli](https://github.com/google-antigravity/antigravity-cli) (1,835★) | [docs/cli/overview](https://antigravity.google/docs/cli/overview) |
| Antigravity SDK | v0.1.9 | Python library | [google-antigravity/antigravity-sdk-python](https://github.com/google-antigravity/antigravity-sdk-python) (2,828★) | [docs/sdk/overview](https://antigravity.google/docs/sdk/overview) |

> 官方 GitHub Org：[github.com/google-antigravity](https://github.com/google-antigravity)（目前仅上述两个公开仓库）

---

### 接口 1：Antigravity CLI（`agy`）

**安装**：

```bash
# macOS / Linux
curl -fsSL https://antigravity.google/cli/install.sh | bash

# Windows PowerShell
irm https://antigravity.google/cli/install.ps1 | iex
```

**交互模式**：

```bash
agy
```

**Headless 模式（非交互）**：

```bash
# 基本用法：-p / --print / --prompt
agy -p "In one sentence, what is a git rebase?"

# 机器可读输出
agy -p "List all TODO comments in src/" --output-format json
```

**关键特性**：
- 共享 Core Agent Engine（与 IDE 2.0 相同）
- 支持 MCP Server 配置（通过 `/config` 命令）
- 支持 Plugins & Skills（通过 `/plugins` 和 slash 命令）
- 会话可导出到 IDE GUI 继续工作
- 认证：系统 keyring + Google Sign-In（SSH 场景自动生成授权 URL）

> **引用**：[antigravity.google/docs/cli/overview](https://antigravity.google/docs/cli/overview)、[antigravity.google/docs/cli/headless](https://antigravity.google/docs/cli/headless)

---

### 接口 2：Antigravity Python SDK（`google-antigravity`）

```python
# 安装
pip install google-antigravity

# 简单调用
from google.antigravity import Agent, LocalAgentConfig

async with Agent(LocalAgentConfig()) as agent:
    response = await agent.chat("Hello!")
    print(await response.text())
```

**关键特性**：
- 异步上下文管理器，管理完整生命周期
- 流式响应（`async for token in response`）
- 工具调用拦截（`response.tool_calls`）
- 思维链流式（`response.thoughts`）
- 多模态输入（图像、视频、音频、文档）
- 对话状态管理（`Conversation` + `ConnectionStrategy`）
- Vertex AI / GCP 企业支持

> **引用**：[github.com/google-antigravity/antigravity-sdk-python](https://github.com/google-antigravity/antigravity-sdk-python)、[pypi.org/project/google-antigravity](https://pypi.org/project/google-antigravity/)

---

### 接口 3：MCP 支持

Antigravity 在所有产品线（IDE 2.0、CLI、SDK）中均支持 Model Context Protocol（MCP）：

- **IDE 2.0**：Settings → MCP 页面配置
- **CLI**：通过 `/config` 或 `/mcp` 命令配置
- **SDK**：通过 `tools.tool_runner` 自定义

> **引用**：[antigravity.google/docs/mcp](https://antigravity.google/docs/mcp)、[antigravity.google/docs/cli/mcp](https://antigravity.google/docs/cli/mcp)

---

### 接口 4：Hooks & Plugins & Skills

- **Hooks**：在代理动作前后触发自定义脚本
- **Plugins**：扩展代理能力（如浏览器录制、外部工具集成）
- **Skills**：Agent Skills 开放标准，兼容 Codex、Claude Code 等

> **引用**：[antigravity.google/docs/plugins](https://antigravity.google/docs/plugins)、[antigravity.google/docs/skills](https://antigravity.google/docs/skills)、[antigravity.google/docs/hooks](https://antigravity.google/docs/hooks)

---

### 接口 5：Antigravity2.0 API / HTTP

Antigravity 2.0（IDE）本身不直接暴露公共 REST API。但社区通过以下方式桥接：
- **CDP（Chrome DevTools Protocol）**：注入 Antigravity WebView 进行远程控制
- **Bridge Extension**：官方浏览器扩展提供部分 API
- **代理网关**：将 `agy` headless 输出包装为 HTTP API

---

## 现有案例清单

### 核心参考项目

| # | 仓库 | ★ | 语言 | 最近更新 | 相关度 | 说明 |
|---|------|---|------|----------|--------|------|
| 1 | [sshahzaiib/agy-bridge](https://github.com/sshahzaiib/agy-bridge) | 39 | TypeScript | 2026-07-04 | ★★★★★ | MCP bridge，让 Claude Code 调用 `agy` CLI，含模型路由、fallback、会话连续性 |
| 2 | [rhishi99/agy-headless-bridge](https://github.com/rhishi99/agy-headless-bridge) | 13 | Python | 2026-07-17 | ★★★★★ | 修复 `agy -p` 在 Windows ConPTY / POSIX pty 下的空输出 bug（#76），含 MCP server |
| 3 | [router-for-me/CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) | 46,218 | Go | 2026-08-04 | ★★★★☆ | 将 Antigravity/Codex/Claude Code 包装为 OpenAI 兼容 API，通用代理→API 网关 |
| 4 | [funny-vibes/agent-vibes](https://github.com/funny-vibes/agent-vibes) | 347 | TypeScript | 2026-07-16 | ★★★★☆ | Unified Agent Gateway，协议翻译层，支持 Antigravity ↔ Claude Code/Cursor |
| 5 | [professional-ALFIE/antigravity-ide-cli](https://github.com/professional-ALFIE/antigravity-ide-cli) | 71 | TypeScript | 2026-05-21 | ★★★★☆ | 通过 Bridge Extension + `antigravity-sdk` 控制 Antigravity IDE 的 headless CLI |
| 6 | [aannoo/hcom](https://github.com/aannoo/hcom) | 419 | Rust | 2026-07-30 | ★★★★☆ | 跨终端代理通信框架，支持 Claude Code、Codex、Antigravity CLI 等 |
| 7 | [hristo2612/jinn](https://github.com/hristo2612/jinn) | 299 | TypeScript | 2026-08-03 | ★★★★☆ | 轻量 AI gateway daemon，编排 Claude Code、Codex、Hermes、Grok 和 Antigravity CLI |
| 8 | [SeemSeam/claude_codex_bridge](https://github.com/SeemSeam/claude_codex_bridge) | 3,365 | Python | 2026-08-05 | ★★★☆☆ | 多代理 CLI workspace，混合 Codex/Claude/Gemini 等（含 Antigravity） |
| 9 | [contains-studio/fable-delegator](https://github.com/contains-studio/fable-delegator) | 8 | — | 2026-07-25 | ★★★★☆ | Plan with Claude → 实施通过 Codex/Grok/Cursor/Antigravity CLI headless，含 quota failover |
| 10 | [yyu0310/cc-to-antigravity-cli-bridge](https://github.com/yyu0310/cc-to-antigravity-cli-bridge) | 27 | Shell | 2026-07-18 | ★★★★★ | 从 Claude Code 驱动 `agy`，共享 system prompt + research method，CLI 版 IDE bridge |

### API 网关类

| # | 仓库 | ★ | 说明 |
|---|------|---|------|
| 11 | [su-kaka/gcli2api](https://github.com/su-kaka/gcli2api) | 5,027 | GeminiCLI + Antigravity → OpenAI/Gemini/Claude API |
| 12 | [justlovemaki/AIClient2API](https://github.com/justlovemaki/AIClient2API) | 8,586 | 多协议 AI API 代理，支持 Antigravity/Codex/Grok |
| 13 | [ink1ing/anti-api](https://github.com/ink1ing/anti-api) | 483 | 将 Antigravity/Codex/Copilot 转换为 Anthropic & OpenAI API |
| 14 | [jackwener/open-antigravity](https://github.com/jackwener/open-antigravity) | 200 | 将 Antigravity 暴露为 OpenAI & Anthropic 兼容 API |
| 15 | [ythx-101/antigravity-bridge](https://github.com/ythx-101/antigravity-bridge) | 58 | 通过 CDP 将 Antigravity 桌面应用桥接到 REST API |

### 多代理协调/基础设施

| # | 仓库 | ★ | 说明 |
|---|------|---|------|
| 16 | [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills) | 81,710 | 生产级 Agent Skills 库，兼容 Codex/Antigravity/Claude Code 等 |
| 17 | [google-labs-code/stitch-skills](https://github.com/google-labs-code/stitch-skills) | 7,919 | Google 官方 Skills 库，配合 Stitch MCP server，兼容 Antigravity/Codex |
| 18 | [amtiYo/agents](https://github.com/amtiYo/agents) | 85 | 一个 `.agents` 配置同步 MCP servers/skills 到 Codex、Claude Code、Antigravity 等 |
| 19 | [hacktivist123/agent-session-resume](https://github.com/hacktivist123/agent-session-resume) | 235 | 跨代理会话恢复 skill（Claude Code/Codex/Antigravity/OpenCode） |
| 20 | [CaviraOSS/OpenMemory](https://github.com/CaviraOSS/OpenMemory) | 4,406 | 本地持久化记忆存储，支持 Claude Desktop/Copilot/Codex/Antigravity |

### 官方 / 生态工具

| # | 仓库 | ★ | 说明 |
|---|------|---|------|
| 21 | [rominirani/antigravity-skills](https://github.com/rominirani/antigravity-skills) | 554 | Google Antigravity Skills 示例集合 |
| 22 | [rmyndharis/antigravity-skills](https://github.com/rmyndharis/antigravity-skills) | 1,241 | Agent Skills for Google Antigravity |
| 23 | [jlcodes99/vscode-antigravity-cockpit](https://github.com/jlcodes99/vscode-antigravity-cockpit) | 4,813 | VS Code 扩展，监控 Antigravity AI 配额 |
| 24 | [wusimpl/AntigravityQuotaWatcher](https://github.com/wusimpl/AntigravityQuotaWatcher) | 2,264 | Antigravity 模型配额监控插件 |
| 25 | [gemini-cli-extensions/conductor](https://github.com/gemini-cli-extensions/conductor) | 3,684 | Spec-Driven Development 插件，兼容 Antigravity/Claude Code |

---

## 相关模式

### 模式 A：MCP Server 桥接（推荐）

**原理**：编写一个 MCP Server，暴露 Antigravity 能力为 MCP 工具，Codex 通过 MCP 协议调用。

**代表项目**：
- `sshahzaiib/agy-bridge`：让 Claude Code 通过 MCP 调用 `agy` headless
- `rhishi99/agy-headless-bridge`：修复 `agy -p` 的兼容性问题
- `amtiYo/agents`：统一 `.agents` 配置同步 MCP servers

**优势**：Codex 原生支持 MCP，部署简单，社区已有成熟参考
**劣势**：每次调用是独立的 CLI 进程，无多轮对话状态

---

### 模式 B：CLI Headless 直接调用

**原理**：Codex 通过 shell 工具直接执行 `agy -p "prompt"`，解析输出。

**代表项目**：
- `yyu0310/cc-to-antigravity-cli-bridge`：从 Claude Code 驱动 `agy`
- `contains-studio/fable-delegator`：多 CLI 代理委托执行

**优势**：零依赖，最简单直接
**劣势**：无状态，输出解析需处理，Windows 兼容性问题（`agy-headless-bridge` 修复的 #76）

---

### 模式 C：Python SDK 集成

**原理**：在 Codex skill 或 MCP server 中使用 `google-antigravity` SDK，以编程方式控制 agent loop。

**代表项目**：
- `google-antigravity/antigravity-sdk-python`（官方 SDK）
- `professional-ALFIE/antigravity-ide-cli`（基于 SDK 的 headless 控制）

**优势**：支持多轮对话、流式响应、工具调用拦截、会话管理
**劣势**：Python 依赖，需要 API key（Gemini API Key 或 Vertex AI）

---

### 模式 D：API 网关 / 协议翻译

**原理**：将 Antigravity CLI 输出包装为标准 API（OpenAI 兼容等），其他代理通过 HTTP 调用。

**代表项目**：
- `router-for-me/CLIProxyAPI`（46k+★，Go 实现）
- `funny-vibes/agent-vibes`（TypeScript，协议翻译网关）
- `su-kaka/gcli2api`（Python）

**优势**：解耦代理实现，支持多代理访问同一 Antigravity 实例
**劣势**：额外基础设施，认证和配额管理复杂

---

### 模式 E：多代理编排框架

**原理**：专用框架管理多个代理（Codex/Antigravity/Claude Code）的协作。

**代表项目**：
- `aannoo/hcom`（Rust，跨终端代理通信）
- `hristo2612/jinn`（TypeScript，AI gateway daemon）
- `SeemSeam/claude_codex_bridge`（Python，多代理 CLI workspace）

**优势**：完整的编排能力，支持代理间通信、任务分发
**劣势**：框架复杂度高，对简单场景过度设计

---

## 差距与建议

### 现有差距

1. **无"Codex → Antigravity"专用集成**：目前没有一个成熟、维护良好、专门为 Codex 设计的 Antigravity 集成库。最接近的是通用桥接器（`agy-bridge` 为 Claude Code 设计）。
2. **CLI Headless 兼容性问题**：Windows 下 `agy -p` 存在空输出 bug（#76），`agy-headless-bridge` 已修复但仅限特定场景。
3. **缺乏结构化输出标准**：`agy -p` 的输出格式（纯文本 vs JSON）需要社区约定，目前各项目自行解析。
4. **认证流程未文档化**：SDK 需要 Gemini API Key 或 Vertex AI，CLI 需要 Google Sign-In，但 Codex 集成场景下的自动化认证未有成熟方案。

### 推荐架构

**首选：MCP Server + CLI Headless**

```
Codex (MCP Client)
    ↓ MCP 协议
Antigravity MCP Server (Node.js/Python)
    ↓ subprocess / Python SDK
agy -p "prompt" (headless mode)
    ↓ stdout
解析并返回 MCP 工具结果
```

**理由**：
- Codex 原生支持 MCP，无需额外插件
- `agy -p` 已稳定（v1.1.10），社区已有 bridge 参考（`agy-bridge`、`agy-headless-bridge`）
- 可逐步升级：先用 CLI headless，后续切换到 Python SDK（支持多轮对话）
- MCP server 可复用给其他代理（Claude Code、Cursor 等）

**备选：Python SDK 集成**（需更深度控制时）

若需要：
- 多轮对话状态
- 流式响应
- 工具调用拦截
- 多模态输入

则使用 `google-antigravity` Python SDK 替代 CLI headless。

---

## 参考资源

### 官方文档
- [antigravity.google/docs](https://antigravity.google/docs) — 产品文档入口
- [antigravity.google/docs/cli/overview](https://antigravity.google/docs/cli/overview) — CLI 概览
- [antigravity.google/docs/cli/headless](https://antigravity.google/docs/cli/headless) — Headless 模式
- [antigravity.google/docs/sdk/overview](https://antigravity.google/docs/sdk/overview) — SDK 概览
- [antigravity.google/docs/mcp](https://antigravity.google/docs/mcp) — MCP 支持
- [antigravity.google/docs/plugins](https://antigravity.google/docs/plugins) — Plugins
- [antigravity.google/docs/skills](https://antigravity.google/docs/skills) — Skills
- [antigravity.google/docs/hooks](https://antigravity.google/docs/hooks) — Hooks

### 官方仓库
- [github.com/google-antigravity/antigravity-sdk-python](https://github.com/google-antigravity/antigravity-sdk-python) — Python SDK (2,828★)
- [github.com/google-antigravity/antigravity-cli](https://github.com/google-antigravity/antigravity-cli) — CLI (1,835★)
- [pypi.org/project/google-antigravity](https://pypi.org/project/google-antigravity/) — PyPI 包

### 关键社区项目
- [sshahzaiib/agy-bridge](https://github.com/sshahzaiib/agy-bridge) — MCP bridge for agy
- [rhishi99/agy-headless-bridge](https://github.com/rhishi99/agy-headless-bridge) — Headless bridge 修复
- [yyu0310/cc-to-antigravity-cli-bridge](https://github.com/yyu0310/cc-to-antigravity-cli-bridge) — Claude Code → agy
- [contains-studio/fable-delegator](https://github.com/contains-studio/fable-delegator) — 多 CLI 代理委托
- [aannoo/hcom](https://github.com/aannoo/hcom) — 跨终端代理通信
- [hristo2612/jinn](https://github.com/hristo2612/jinn) — AI gateway daemon
- [router-for-me/CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) — 统一 API 网关 (46k+★)
- [funny-vibes/agent-vibes](https://github.com/funny-vibes/agent-vibes) — 协议翻译网关
