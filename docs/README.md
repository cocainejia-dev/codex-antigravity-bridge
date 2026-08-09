# 文档索引

这里是项目文档的统一入口。项目首页、进度记录、运行时技术手册和设计历史分别放在不同位置，避免把使用说明和内部设计记录混在一起。

> [!WARNING]
> 如果通过 CC Switch 接管 Codex 代理，CC Switch 重启或恢复接管时可能覆盖 `%USERPROFILE%\.codex\config.toml`，导致 MCP 和其他 Codex 设置消失。CC Switch MCP 管理页面显示“已启用”不等于 Codex live 配置中存在该服务器。请先运行 `codex mcp list` 和 `Get-Content "$env:USERPROFILE\.codex\config.toml"`，再按[运行时手册中的恢复说明](../mcp-antigravity-bridge/README.md#cc-switch-configuration-ownership-and-recovery)处理。详细跟踪见 [CC Switch issue #6265](https://github.com/farion1231/cc-switch/issues/6265)。

## 从这里开始

| 你想了解什么 | 从这里开始 |
| --- | --- |
| 中文项目介绍、安装和模式选择 | [项目首页](../README.zh-CN.md) |
| English project overview | [README.md](../README.md) |
| 中文项目进度 | [PROGRESS.md](../PROGRESS.md) |
| English project progress | [PROGRESS.en.md](../PROGRESS.en.md) |
| 安装、代理、MCP 工具和排错 | [运行时技术手册](../mcp-antigravity-bridge/README.md) |
| Runtime manual in English | [Runtime technical manual](../mcp-antigravity-bridge/README.md) |
| AGY 协同规则和安全边界 | [agy-supervisor 技能](../skills/agy-supervisor/SKILL.md) |
| 协同 Demo 和验收步骤 | [Demo](demo.md) |
| 安全问题报告 | [SECURITY.md](../SECURITY.md) |
| 研究资料 | [research/codex-antigravity-cases.md](../research/codex-antigravity-cases.md) |

## 技术文档分层

### 运行时文档

`mcp-antigravity-bridge/README.md` 是 Python MCP 包的技术手册，和
`pyproject.toml`、`src/`、`tests/` 放在同一目录，方便安装包的开发者直接查看。
它不是项目首页；项目首页统一从根目录的 `README.md` 和 `README.zh-CN.md` 开始。
运行时手册目前以英文维护，根目录提供完整的中英文项目入口和双语进度记录。

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
- `mcp-antigravity-bridge/README.md`：包级运行时技术手册。
- `mcp-antigravity-bridge/examples/codex-config.toml`：手动 MCP 配置示例。
- `docs/`：文档索引、设计历史和长期参考资料。
