# 文档索引

这里是项目文档的统一入口。用户说明、进度记录、验证 Demo 和设计历史分别放在不同位置，避免把使用指南和内部记录混在一起。

> [!WARNING]
> 如果通过 CC Switch 接管 Codex 代理，重启或恢复接管时可能覆盖 `%USERPROFILE%\.codex\config.toml`。先运行 `codex mcp list` 检查 MCP 是否仍然存在；需要恢复时，按[中文首页的配置说明](../README.zh-CN.md#cc-switch)重新注册，并在新建对话前再次确认。

## 从这里开始

| 你想了解什么 | 从这里开始 |
| --- | --- |
| 中文项目介绍、安装和模式选择 | [项目首页](../README.zh-CN.md) |
| English project overview | [README.md](../README.md) |
| 中文项目进度 | [PROGRESS.md](../PROGRESS.md) |
| English project progress | [PROGRESS.en.md](../PROGRESS.en.md) |
| 安装、代理、MCP 工具和排错 | [中文首页快速开始](../README.zh-CN.md#quick-start) · [English quick start](../README.md#quick-start) |
| AGY 协同规则和安全边界 | [agy-supervisor 技能](../skills/agy-supervisor/SKILL.md) |
| 协同 Demo、dry-run 和验收步骤 | [Demo](demo.md) |
| 安全问题报告 | [SECURITY.md](../SECURITY.md) |
| 研究资料 | [research/codex-antigravity-cases.md](../research/codex-antigravity-cases.md) |

## 技术文档分层

### 用户说明

根目录的 `README.md` 和 `README.zh-CN.md` 是安装、代理、MCP 工具和安全边界的唯一用户入口。`docs/demo.md` 提供真实 MCP 冒烟测试、协同 dry-run 和人工验收步骤。

`mcp-antigravity-bridge/README.md` 仅保留包级安装和开发命令，服务于源码开发与包元数据，不再重复用户指南。

### 设计与执行记录

- [`docs/superpowers/specs/`](superpowers/specs/)：设计决策和规格说明。
- [`docs/superpowers/plans/`](superpowers/plans/)：实现计划和执行记录。

这些文件保留历史上下文，不作为普通用户的第一阅读入口。

### 技能与协议

- [`skills/agy-supervisor/SKILL.md`](../skills/agy-supervisor/SKILL.md)：Codex 何时以及如何调用 agy。
- [`skills/agy-supervisor/references/`](../skills/agy-supervisor/references/)：任务契约、状态机、worktree 和纠正协议。

## 文档命名约定

- `README.md`：英文项目入口。
- `README.en.md`：英文项目入口兼兼容链接。
- `README.zh-CN.md`：中文项目入口。
- `PROGRESS.md`：中文项目进度。
- `PROGRESS.en.md`：英文项目进度。
- `mcp-antigravity-bridge/README.md`：包级安装与开发说明。
- `mcp-antigravity-bridge/examples/codex-config.toml`：手动 MCP 配置示例，路径需按本机 Python 环境替换。
- `docs/`：文档索引、设计历史和长期参考资料。
