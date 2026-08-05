# 🤖 Codex → Antigravity 桥接工具

让 **OpenAI Codex**（或任何 MCP 客户端）调用 **Google Antigravity** agent 的完整方案。

> 从调研 → 原型 → 完整实现的落地仓库。

---

## 🎯 目标

在 Codex 会话中，直接把任务委托给 Google Antigravity 的 agent 引擎，复用两家的模型与工具链：

```
┌─────────────┐      MCP (stdio)      ┌────────────────────┐      subprocess / pty      ┌─────────────────┐
│    Codex     │ ───────────────────▶ │  antigravity-mcp   │ ────────────────────────▶ │  agy (CLI)      │
│ (MCP Client) │                      │  (MCP Server)      │                            │ (Antigravity)   │
└─────────────┘                      └────────────────────┘                            └─────────────────┘
```

---

## 📦 仓库结构

| 目录 | 说明 | 状态 |
|------|------|------|
| [`mcp-antigravity-bridge/`](mcp-antigravity-bridge/) | **完整版** MCP Server（FastMCP + pty 回退），支持 Windows ConPTY | ✅ 推荐 |
| [`mcp-server/`](mcp-server/) | 早期简化版 MCP Server（SDK 直连） | 🧪 原型 |
| [`research/`](research/) | 调研报告 + 官方文档快照 | ✅ 完成 |
| [`PROGRESS.md`](PROGRESS.md) | 已完成 / 未完成任务清单 | 📋 持续更新 |

---

## ✨ 特性

- 🚀 **原生 MCP 集成** — Codex 无需插件，注册即可用
- 🖥️ **跨平台** — Windows ConPTY / macOS / Linux pty 回退，解决 `agy` 空输出 bug（#76）
- 🧹 **输出清洗** — 自动剥离 ANSI / TUI 噪音，返回干净文本或 JSON
- 🔌 **工具化** — `agy_ask`（文本）、`agy_ask_json`（结构化输出）
- 🧩 **可升级** — 底层可切换为官方 Python SDK（多轮对话、流式响应、工具拦截）

---

## 🚀 快速开始

```bash
# 1. 安装 Antigravity CLI（Windows PowerShell）
irm https://antigravity.google/cli/install.ps1 | iex

# 2. 安装 MCP 桥
cd mcp-antigravity-bridge
pip install -e ".[winpty]"

# 3. 注册到 Codex
codex mcp add codex-agy-bridge -- python -m codex_agy_bridge
```

然后在 Codex 中直接说：

> *"用 agy_ask 让 Antigravity 帮我审查这个仓库的 TODO"*

---

## 🔍 调研结论

| 接口 | 形态 | 适用场景 |
|------|------|----------|
| **CLI headless** | `agy -p "prompt" --output-format json` | 快速、单轮、无依赖 |
| **Python SDK** | `google-antigravity`（官方） | 多轮、流式、深度控制 |
| **MCP** | 官方全线支持 | Codex 等 MCP 客户端直接对接 |

GitHub 生态现状：暂无「Codex → Antigravity」专用库，社区方案集中在
**CLI 桥接**（`agy-bridge`）、**API 网关**（`CLIProxyAPI` 46k★）和**多代理编排**（`hcom`、`jinn`）。
详见 [`research/codex-antigravity-cases.md`](research/codex-antigravity-cases.md)。

---

## 📚 参考项目

- [router-for-me/CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) — 46k★，Antigravity/Codex 统一 API 网关
- [sshahzaiib/agy-bridge](https://github.com/sshahzaiib/agy-bridge) — Claude Code → agy MCP 桥
- [rhishi99/agy-headless-bridge](https://github.com/rhishi99/agy-headless-bridge) — Windows ConPTY 空输出修复
- [google-antigravity/antigravity-sdk-python](https://github.com/google-antigravity/antigravity-sdk-python) — 官方 SDK（2.8k★）
- [google-antigravity/antigravity-cli](https://github.com/google-antigravity/antigravity-cli) — 官方 CLI（1.8k★）

---

## 📄 License

Apache-2.0
