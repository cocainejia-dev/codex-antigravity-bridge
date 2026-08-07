<div align="center">

# 项目进度 · Codex × Antigravity

### 当前状态：CLI-only bridge 已可用

<p>
  <img src="https://img.shields.io/badge/runtime-ready-16a34a?style=for-the-badge" alt="Runtime ready">
  <img src="https://img.shields.io/badge/tests-25%20passed-2563EB?style=for-the-badge" alt="25 tests passed">
  <img src="https://img.shields.io/badge/license-Apache--2.0-111827?style=for-the-badge" alt="Apache 2.0 license">
</p>

</div>

> 这份文档记录当前仓库已经交付的能力、验证证据和下一步路线。支持的运行时只有本地 MCP bridge，不包含 Antigravity 桌面 GUI 集成。

## 当前架构

```text
Codex Desktop / CLI
        |
        | MCP over local stdio
        v
codex-agy-bridge
        |
        | subprocess / ConPTY / pty
        v
agy -p "..."
        |
        v
Antigravity agent
```

## 交付状态

| 区域 | 状态 | 说明 |
| --- | :---: | --- |
| CLI bridge runtime | 已完成 | `mcp-antigravity-bridge/` 是当前支持的运行时 |
| 同步 MCP 工具 | 已完成 | `agy_ask`、`agy_ask_json` |
| 异步 worktree 工具 | 已完成 | `agy_start`、`agy_status` |
| Windows 路径与 PTY | 已完成 | 非 ASCII 工作目录、ConPTY fallback |
| Supervisor skill | 已完成 | 明确授权、权限边界、状态机和纠正规则 |
| 安装与分发 | 已完成 | Windows / POSIX 安装脚本与幂等 MCP 注册 |
| README 文档 | 已完成 | 中文项目首页、英文技术手册、中文进度页 |
| CI | 未完成 | 当前依赖本地验证命令 |

### 最近审计修复

- 异步任务现在会把非零 `agy` 退出码报告为 `failed`，不再误报 `completed`。
- runner 现在基于清洗后的输出判断 PTY fallback，能处理“只有 ANSI/TUI 装饰”的空结果。
- `agy_ask_json` 现在拒绝不可解析的 JSON；请求 schema 仍由 supervisor 根据 Prompt 契约验收。
- `agy_start` 现在要求调用方传入已存在的独立 worktree 目录，bridge 不会误用当前目录或自动创建 worktree。
- MCP 权限提示已明确要求用户对 exact worktree/task 进行授权后才能启用权限绕过。

## 已完成

### 研究与决策

- 阅读 Antigravity CLI headless mode、MCP 和相关官方资料。
- 对比社区 bridge 与 Windows 终端输出方案。
- 将研究笔记保存在 `research/`。
- 明确当前支持路径为 CLI-only，不把已移除的 SDK 原型作为运行入口。

### Bridge runtime

- 构建 `mcp-antigravity-bridge/` 本地 MCP server。
- 提供 `agy_ask` 普通文本调用。
- 提供 `agy_ask_json` 结构化输出调用。
- 提供 `agy_start` / `agy_status` 异步 worktree 调用。
- 支持通过 `AGY_PATH`、`PATH` 和平台默认位置发现 `agy`。
- 支持 Windows 非 ASCII 工作目录。
- 直接 stdout 为空时，回退到 Windows ConPTY 或 POSIX `pty`。
- 清理 ANSI escape、回车重绘和 TUI 装饰输出。
- 使用有界线程池管理异步任务。

### Supervisor skill

- 普通请求不会自动调用 Antigravity。
- 只有用户明确授权或开启 supervisor mode 才会委派。
- 委派前检查风险、权限、worktree 状态和文件边界。
- 委派后检查 diff、测试、结果状态和越界修改。
- 每个子任务最多三次调用：首次实现加两次纠正。
- 测试通过、超出范围、连续无进展、超时或需要用户决定时停止。
- 多页面协同要求共享契约明确、文件边界互斥，并使用独立 worktree。
- 相关协议集中在 `skills/agy-supervisor/references/`。

### 文档与验证

- 根 README 已完成视觉重设计。
- 英文 bridge README 与根文档保持同一套信息架构。
- 本进度文档改为中文，并记录交付边界。
- 分发测试覆盖 skill、README、入口和 metadata。
- skill validator 会扫描完整 skill package 与 references。

## 验证证据

在仓库根目录执行：

```powershell
python -m pytest -q
python scripts/validate_skill.py
git diff --check
```

当前结果：

- 根测试：25 passed。
- bridge 测试：9 passed。
- skill validator：skill validation passed。
- README Markdown 代码围栏：已检查为真实反引号，围栏成对闭合。
- README 本地相对链接：已检查可解析。
- UTF-8 文件编码：无替换字符。

在 `mcp-antigravity-bridge/` 目录执行：

```powershell
python -m pytest -q
python -m compileall -q src
```

bridge 测试与编译检查均通过。单元测试 mock 进程边界，不需要真实 Antigravity 登录。

## 当前边界

- 不启动、嵌入或控制 Antigravity 桌面 GUI。
- 不保存或转发 Antigravity OAuth、代理凭据或私有 Codex 配置。
- 不自动委托生产操作、不可逆操作、跨项目写入或范围不明任务。
- `dangerously_skip_permissions` 默认关闭。
- 异步 job 状态保存在当前 bridge 进程内；进程重启后旧 job id 会变成 `unknown`。
- 当前没有 CI，也没有发布到 PyPI 的版本化流程。

## 下一步路线

### 优先级高

- 增加 bridge 与分发测试的 CI。
- 在命令和配置接口稳定后发布版本化 Python 包。
- 补充认证失败、超时和空输出的诊断信息。

### 优先级中

- 在不改变一次性工具契约的前提下探索可选 streaming output。
- 增加更完整的真实机器 smoke check 文档。

### 优先级低

- 如果分发需求值得，评估 TypeScript 或 Go 实现。

## 关键文件

| 文件 | 作用 |
| --- | --- |
| `mcp-antigravity-bridge/src/codex_agy_bridge/` | MCP bridge runtime |
| `skills/agy-supervisor/SKILL.md` | Supervisor 行为规则 |
| `skills/agy-supervisor/references/` | 状态机、计划和协议参考 |
| `scripts/install.ps1` | Windows 安装器 |
| `scripts/install.sh` | POSIX 安装器 |
| `scripts/validate_skill.py` | skill package 验证器 |
| `tests/` | skill、分发与压力场景测试 |
| `PROGRESS.md` | 当前中文进度 |

## 参考资料

- [Antigravity CLI](https://github.com/google-antigravity/antigravity-cli)
- [Antigravity CLI 文档](https://antigravity.google/docs/cli/overview)
- [Model Context Protocol](https://modelcontextprotocol.io/)

## License

Apache-2.0
