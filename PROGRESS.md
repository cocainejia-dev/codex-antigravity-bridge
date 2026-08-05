# Codex 调用 Antigravity — 项目进度

## 目标

让 Codex（OpenAI 开源 agent）能够调用 Google Antigravity agent 完成编码任务。

## 已完成

### 调研阶段
- ✅ 调研 Antigravity 官方接口：CLI headless（`agy -p`）、Python SDK（`google-antigravity`）、MCP 支持
- ✅ 调研 GitHub 生态：CLIProxyAPI（46k★）、agy-bridge、agy-headless-bridge 等社区项目
- ✅ 确认 CLI headless 模式（`agy -p "prompt" --output-format json`）已正式稳定
- ✅ 产出调研报告 → `research/codex-antigravity-cases.md`（含官方接口现状、案例清单、架构建议）
- ✅ 下载并分析官方文档 HTML（CLI overview、headless、MCP、SDK overview）

### 实现阶段
- ✅ 项目 `mcp-antigravity-bridge/`（完整版）— MCP Server 桥接 Codex → Antigravity CLI
  - `agy_runner.py` — headless runner，支持 ConPTY/POSIX pty 回退（解决 issue #76 空输出）
  - `server.py` — FastMCP server，暴露 `agy_ask` / `agy_ask_json` 工具
  - `__main__.py` — 入口 `python -m codex_agy_bridge`
  - `pyproject.toml` — 依赖管理（mcp>=1.0,<2.0、pywinpty 可选）
  - `README.md` — 安装和接入 Codex 的完整说明
  - `examples/codex-config.toml` — Codex MCP 配置示例
  - `tests/test_smoke.py` — 单元测试
- ✅ 项目 `mcp-server/`（早期版本）— 基于 fastmcp + CLI 的简化实现
- ✅ `mcp-antigravity-bridge` 语法检查通过（`python -m compileall`）
- ✅ 依赖安装验证（mcp 1.29.0 + FastMCP import 成功）

## 未完成 / 待做

### 高优先级
- ⬜ 真机端到端测试（需安装 Antigravity CLI `agy`，本机尚未安装）
- ⬜ Windows ConPTY 空输出 bug 的真机验证
- ⬜ 在 Codex 中注册 MCP Server 并实际调用验证

### 中优先级
- ⬜ 流式响应支持（当前仅同步调用）
- ⬜ 多轮对话状态管理（当前仅单轮）
- ⬜ 使用 `fastmcp>=3.4`（独立包）替代 `mcp` 自带 FastMCP 的方案对比
- ⬜ 错误处理增强（认证失败、超时重试）

### 低优先级
- ⬜ Python SDK 深度集成（`google-antigravity`，多轮对话、工具拦截）
- ⬜ CI/CD（GitHub Actions）
- ⬜ 发布到 PyPI
- ⬜ npm/Go 实现版本

## 技术栈

- Python 3.10+
- MCP（Model Context Protocol）via `mcp` Python SDK（1.29.0）
- FastMCP（`mcp.server.fastmcp`）
- Antigravity CLI（`agy -p` headless 模式）
- 可选：`google-antigravity` SDK、`pywinpty`（Windows ConPTY）

## 关键参考

| 项目 | Stars | 说明 |
|------|-------|------|
| [router-for-me/CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) | 46k+ | 把 Antigravity/Codex/Claude Code 包装为 OpenAI 兼容 API |
| [sshahzaiib/agy-bridge](https://github.com/sshahzaiib/agy-bridge) | 39★ | MCP bridge，Claude Code 调 agy headless |
| [rhishi99/agy-headless-bridge](https://github.com/rhishi99/agy-headless-bridge) | 13★ | Windows ConPTY 空输出修复 |
| [yyu0310/cc-to-antigravity-cli-bridge](https://github.com/yyu0310/cc-to-antigravity-cli-bridge) | 27★ | Claude Code → agy 桥接 |
| [google-antigravity/antigravity-sdk-python](https://github.com/google-antigravity/antigravity-sdk-python) | 2.8k★ | 官方 Python SDK |
| [google-antigravity/antigravity-cli](https://github.com/google-antigravity/antigravity-cli) | 1.8k★ | 官方 CLI |
| [su-kaka/gcli2api](https://github.com/su-kaka/gcli2api) | 5k★ | Antigravity 转 OpenAI API |
| [aannoo/hcom](https://github.com/aannoo/hcom) | 419★ | 跨终端多代理编排 |
