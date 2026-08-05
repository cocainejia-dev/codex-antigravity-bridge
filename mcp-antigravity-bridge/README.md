# codex-agy-bridge

让 **Codex**（或任何 MCP 客户端）通过 MCP 调用 **Google Antigravity** agent 的桥接工具。

```
Codex (MCP Client)
   │  MCP (stdio)
   ▼
codex-agy-bridge (MCP Server, Python)
   │  subprocess / pty
   ▼
agy -p "<prompt>" --output-format json   (Antigravity CLI headless)
```

## 为什么需要它

- Antigravity CLI（`agy`）默认是交互式 TUI；`agy -p` 提供非交互 headless 模式。
- 部分构建版本 `agy -p` 会因 stdout 不是 TTY 而输出为空（上游 issue #76），
  本项目先走普通 subprocess，输出为空时自动回退到伪终端
  （Windows 用 ConPTY / `pywinpty`，macOS/Linux 用 stdlib `pty`），并清理 ANSI/TUI 噪音。
- Codex 原生支持 MCP，无需插件即可注册本 server。

## 安装

1. 安装 Antigravity CLI（Windows PowerShell）：

   ```powershell
   irm https://antigravity.google/cli/install.ps1 | iex
   ```

   其他平台见 https://antigravity.google/docs/cli/overview

2. 安装本桥（Windows 建议带 `winpty` 依赖）：

   ```powershell
   cd mcp-antigravity-bridge
   pip install -e ".[winpty]"
   ```

## 接入 Codex

方式一（推荐）：

```powershell
codex mcp add codex-agy-bridge -- python -m codex_agy_bridge
```

方式二：手动在 `~/.codex/config.toml` 加入（参考 `examples/codex-config.toml`）：

```toml
[mcp_servers.codex-agy-bridge]
command = "python"
args = ["-m", "codex_agy_bridge"]
```

然后在 Codex 里直接说：*"用 agy_ask 让 Antigravity 帮我审查这个仓库的 TODO"*。

## 工具

| 工具 | 说明 |
|------|------|
| `agy_ask(prompt, workdir?, timeout?)` | 单轮 headless 调用，返回清洗后的文本 |
| `agy_ask_json(prompt, workdir?, timeout?)` | 同上，`--output-format json` 结构化输出 |

环境变量：`AGY_PATH` 可显式指定 `agy` 二进制路径。

## 深度控制（可选）

需要多轮对话、流式响应、工具调用拦截时，把底层换成官方 SDK：

```bash
pip install -e ".[sdk]"
```

然后参考 https://github.com/google-antigravity/antigravity-sdk-python 的
`Agent` / `Conversation` API 扩展 `server.py`。

## 参考项目

- https://github.com/rhishi99/agy-headless-bridge — Windows ConPTY 空输出修复（本项目的 pty 方案来源）
- https://github.com/sshahzaiib/agy-bridge — Claude Code → agy 的 MCP bridge（TypeScript）
- https://github.com/yyu0310/cc-to-antigravity-cli-bridge — Claude Code → agy
- https://github.com/google-antigravity/antigravity-sdk-python — 官方 Python SDK

## 测试

```bash
pip install -e ".[dev]"
pytest
```