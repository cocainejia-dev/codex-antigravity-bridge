# 文档索引

这里是项目文档的统一入口。项目首页、进度记录、运行时技术手册和设计历史分别放在不同位置，避免把使用说明和内部设计记录混在一起。

## 从这里开始

| 你想了解什么 | 从这里开始 |
| --- | --- |
| 中文项目介绍、安装和模式选择 | [项目首页](../README.md) |
| English project overview | [README.en.md](../README.en.md) |
| 中文项目进度 | [PROGRESS.md](../PROGRESS.md) |
| English project progress | [PROGRESS.en.md](../PROGRESS.en.md) |
| 安装、代理、MCP 工具和排错 | [运行时技术手册](../mcp-antigravity-bridge/README.md) |
| AGY 协同规则和安全边界 | [agy-supervisor 技能](../skills/agy-supervisor/SKILL.md) |
| 研究资料 | [research/codex-antigravity-cases.md](../research/codex-antigravity-cases.md) |

## 技术文档分层

### 运行时文档

`mcp-antigravity-bridge/README.md` 是 Python MCP 包的技术手册，和
`pyproject.toml`、`src/`、`tests/` 放在同一目录，方便安装包的开发者直接查看。
它不是项目首页；项目首页统一从根目录的 `README.md` 和 `README.en.md` 开始。

### 设计与执行记录

- [`docs/superpowers/specs/`](superpowers/specs/)：设计决策和规格说明。
- [`docs/superpowers/plans/`](superpowers/plans/)：实现计划和执行记录。

这些文件保留历史上下文，不作为普通用户的第一阅读入口。

### 技能与协议

- [`skills/agy-supervisor/SKILL.md`](../skills/agy-supervisor/SKILL.md)：Codex 何时以及如何调用 agy。
- [`skills/agy-supervisor/references/`](../skills/agy-supervisor/references/)：任务契约、状态机、worktree 和纠正协议。

## 文档命名约定

- `README.md`：中文项目入口。
- `README.en.md`：英文项目入口。
- `PROGRESS.md`：中文项目进度。
- `PROGRESS.en.md`：英文项目进度。
- `mcp-antigravity-bridge/README.md`：包级运行时技术手册。
- `docs/`：文档索引、设计历史和长期参考资料。
