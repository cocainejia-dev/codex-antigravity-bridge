<div align="center">

# 项目进度 · Codex × Antigravity / Project Progress

### 当前状态：命令行桥接器已可用 · Current status: CLI bridge ready

<p>
  <img src="https://img.shields.io/badge/runtime-ready-16a34a?style=for-the-badge" alt="Runtime ready">
  <img src="https://img.shields.io/badge/tests-57%20passed-2563EB?style=for-the-badge" alt="57 tests passed">
  <img src="https://img.shields.io/badge/CI-GitHub%20Actions-0ea5e9?style=for-the-badge&logo=githubactions&logoColor=white" alt="GitHub Actions CI">
  <img src="https://img.shields.io/badge/license-Apache--2.0-111827?style=for-the-badge" alt="Apache 2.0 license">
</p>

<p>
  <a href="README.md">中文项目首页</a> ·
  <a href="README.en.md">English project overview</a> ·
  <a href="PROGRESS.en.md">English progress</a> ·
  <a href="docs/README.md">文档索引</a>
</p>

</div>

> 这份文档记录当前仓库已经交付的能力、验证证据和下一步路线。运行时只有本地 MCP 桥接器，不包含 Antigravity 桌面应用集成。
>
> This document records the delivered capabilities, verification evidence, and next steps. The runtime is a local MCP bridge; the Antigravity desktop application is out of scope.

## 🧭 当前架构 · Architecture

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

## 🧭 运行模式 · Runtime Modes

当前共有 **4 种运行模式**。`headless` 与 `terminal` 是显示方式，监督模式
是 Codex 的安全与验收规则，不分别计作新的运行模式。

| # | 中文模式 | English mode | 入口 / Entry point | 并发与行为 / Concurrency and behavior |
| :---: | --- | --- | --- | --- |
| 1 | Codex 普通开发 | Normal Codex development | 不调用 agy / No agy tool | Codex 独立完成，不会自动委派。 |
| 2 | 单次同步委派 | Synchronous delegation | `agy_ask`、`agy_ask_json` | 一次处理一个任务，Codex 等待返回。 |
| 3 | 异步独立任务 | Async isolated task | `agy_start`、`agy_status` | 一个 job 对应一个 agy 进程，Codex 可继续开发。 |
| 4 | 协同开发 MVP | Collaboration MVP | `agy_collab_start`、`agy_collab_status` | 默认 1 个任务，最多 4 个；每个任务独立分支和 worktree。 |

### 显示方式 · Display Options

| 显示方式 / Display mode | 默认 / Default | 说明 / Behavior | 平台 / Platform |
| --- | :---: | --- | --- |
| `headless` | ✅ | 不弹窗，通过 MCP 返回状态和最终结果。 / No window; status and final output return through MCP. | Windows、macOS、Linux |
| `terminal` | 关闭 / Off | 每个运行中的任务打开一个可见终端并显示 agy 实时输出。 / One visible console per task with live agy output. | Windows |

### 协同生命周期 · Collaboration Lifecycle

```text
用户确认显示方式和任务数量
        ↓
Codex 拆分任务，定义共享契约和互斥文件范围
        ↓
创建独立分支与 worktree，启动 agy 进程
        ↓
Codex 在自己的工作区继续开发，轮询协同状态
        ↓
检查 diff、未提交改动、测试和验收标准
        ↓
人工确认后合并；桥接器不会自动合并
```

`ready_for_review` 只表示 agy 进程成功退出，不表示功能验收已经通过。
终端显示不会改变权限、worktree 隔离、分支和人工验收规则。

## 📦 交付状态 · Delivery Status

| 中文区域 | English area | 状态 / Status | 说明 / Notes |
| --- | --- | :---: | --- |
| 命令行桥接运行时 | CLI bridge runtime | ✅ 已完成 / Done | `mcp-antigravity-bridge/` 提供本地 MCP 运行时。 |
| 同步 MCP 工具 | Synchronous MCP tools | ✅ 已完成 / Done | `agy_ask`、`agy_ask_json`。 |
| 异步工作区工具 | Async worktree tools | ✅ 已完成 / Done | `agy_start`、`agy_status`。 |
| 协同开发 MVP | Collaboration MVP | ✅ 已完成 / Done | `agy_collab_start`、`agy_collab_status` 自动创建隔离 worktree 并汇总状态，不自动合并。 |
| 实时终端模式 | Live terminal mode | ✅ 已完成 / Done | 可选 Windows 可见终端、每任务一个窗口；默认仍为无界面模式。 |
| Windows 路径与终端 | Windows paths and PTY | ✅ 已完成 / Done | 非 ASCII 工作目录与 ConPTY 回退。 |
| 监督技能 | Supervisor skill | ✅ 已完成 / Done | 授权、权限、状态机和纠正规则。 |
| 安装与代理配置 | Install and proxy setup | ✅ 已完成 / Done | Python 路径修复、代理探测与 MCP 配置写入。 |
| 文档 | Documentation | ✅ 已完成 / Done | 中文首页、英文手册、中英双语进度。 |
| 持续集成 | Continuous integration | ✅ 已完成 / Done | GitHub Actions 覆盖测试、技能校验和编译检查。 |

### 🧹 最近审计修复 · Recent Audit Fixes

- 异步任务现在会把非零 `agy` 退出码报告为 `failed`，不再误报 `completed`。
- 运行器现在基于清洗后的输出判断 PTY 回退，能处理“只有 ANSI/TUI 装饰”的空结果。
- `agy_ask_json` 现在拒绝不可解析的 JSON；请求结构仍由监督者根据提示词契约验收。
- `agy_start` 现在要求调用方传入已存在的独立工作区，桥接器不会误用当前目录或自动创建工作区。
- MCP 权限提示已明确要求用户对指定工作区和任务进行授权后才能启用权限绕过。
- 安装器现在会自动探测代理；无法识别时支持 `-ProxyUrl`，不会假设统一端口。
- runner、同步工具和异步任务都会保留失败原因，不再把空输出误报为成功。
- 新增协同开发 MVP：校验共享契约与互斥文件边界，创建临时分支和 worktree，并并行启动多个 agy 任务。
- 新增可选实时终端模式：用户确认后按任务打开可见 Windows 终端，默认不弹窗。
- 协同启动前询问显示方式和任务数量，默认 1 个任务，硬上限 4 个。
- 同步和异步公开工具会在启动进程或 job 前拒绝非正数或非有限 `timeout`。

## ✅ 已完成 · Completed

### 🔬 研究与决策 · Research and Decisions

- 阅读 Antigravity CLI 无界面模式、MCP 和相关官方资料。
- 对比社区桥接器与 Windows 终端输出方案。
- 将研究笔记保存在 `research/`。
- 明确当前支持路径为仅命令行，不把已移除的 SDK 原型作为运行入口。

### 🧱 桥接运行时 · Bridge Runtime

- 构建 `mcp-antigravity-bridge/` 本地 MCP 服务器。
- 提供 `agy_ask` 普通文本调用。
- 提供 `agy_ask_json` 结构化输出调用。
- 提供 `agy_start` / `agy_status` 异步工作区调用。
- 提供 `agy_collab_start` / `agy_collab_status` 协同开发调用。
- 协同会话返回任务状态、分支、worktree、改动文件、未提交改动和 `diff_check`，验收与合并仍由 Codex 手动完成。
- 终端模式使用独立可见控制台承载每个 agy 进程，退出码仍由协同状态接口汇总。
- 支持通过 `AGY_PATH`、`PATH` 和平台默认位置发现 `agy`。
- 支持 Windows 非 ASCII 工作目录。
- 直接标准输出为空时，回退到 Windows ConPTY 或 POSIX `pty`。
- 清理 ANSI 转义、回车重绘和 TUI 装饰输出。
- 使用有界线程池管理异步任务。

### 🛡️ 监督技能 · Supervisor Skill

- 普通请求不会自动调用 Antigravity。
- 只有用户明确授权或开启监督模式才会委派。
- 委派前检查风险、权限、工作区状态和文件边界。
- 委派后检查差异、测试、结果状态和越界修改。
- 每个子任务最多三次调用：首次实现加两次纠正。
- 测试通过、超出范围、连续无进展、超时或需要用户决定时停止。
- 多页面协同要求共享契约明确、文件边界互斥，并使用独立工作区。
- 相关协议集中在 `skills/agy-supervisor/references/`。

### 📝 文档与验证 · Documentation and Verification

- 根 README 已完成视觉重设计。
- 英文 bridge README 与根文档保持同一套信息架构。
- 本进度文档改为中文，并记录交付边界。
- 分发测试覆盖 skill、README、入口和 metadata。
- skill validator 会扫描完整 skill package 与 references。

## 🇬🇧 English Summary

### 🧭 Architecture

Codex plans and reviews through local MCP stdio. The bridge starts `agy -p` as a bounded subprocess, cleans terminal output, and supports synchronous calls, explicit asynchronous worktree jobs, and an opt-in collaboration MVP.

### 📦 Delivered

- Six MCP tools: `agy_ask`, `agy_ask_json`, `agy_start`, `agy_status`, `agy_collab_start`, and `agy_collab_status`.
- Collaboration MVP: validate exclusive task paths, create temporary Git worktrees, start parallel jobs, and aggregate review metadata without auto-merging.
- Optional live terminal mode: ask for consent before opening one visible Windows console per task; default to one headless task and cap sessions at four tasks.
- Windows non-ASCII workdir support with ConPTY fallback.
- Direct stderr, nonzero exits, PTY failures, and empty-output diagnostics are preserved.
- Completed async jobs are retained for a finite period and the worker pool has an explicit shutdown path.
- Windows installation resolves a real Python executable, detects common proxy configurations, and writes per-user MCP environment values.
- POSIX installation accepts `PROXY_URL` and mirrors the per-user MCP configuration behavior.
- CI runs repository tests, bridge tests, the real MCP stdio smoke test, skill validation, and compilation checks.

### 🧭 Runtime Modes

The project has four runtime modes:

1. **Normal Codex development**: Codex works alone and does not invoke agy automatically.
2. **Synchronous delegation**: `agy_ask` or `agy_ask_json` handles one bounded task while Codex waits.
3. **Async isolated task**: `agy_start` and `agy_status` run one agy task in a caller-created worktree while Codex continues.
4. **Collaboration MVP**: `agy_collab_start` and `agy_collab_status` run one to four independently scoped tasks, each with its own branch and worktree.

`headless` is the default display mode. On Windows, the opt-in `terminal` display
mode opens one visible console per running task and shows agy's live output. The
display option does not change the runtime mode, task isolation, or acceptance
responsibility.

### 🔒 Safety Boundary

Normal development does not invoke `agy`. Delegation requires explicit user authorization or supervisor mode. Production operations, secrets, irreversible actions, unclear workdirs, and concurrent writes in one worktree remain out of scope.

### 🛣️ Next Steps

- Publish a versioned Python package when the command and configuration interfaces stabilize.
- Explore optional streaming output without changing the one-shot tool contract.
- Expand real-machine smoke-check documentation.

## 🔍 验证证据 · Verification Evidence

在仓库根目录执行：

```powershell
python -m pytest -q
python scripts/validate_skill.py
git diff --check
```

Latest verification: repository tests 57 passed in total; root tests 20 passed; bridge tests 37 passed, including the real MCP stdio tool-list smoke test; skill validation passed; compileall passed.

当前结果：

- 仓库总测试：57 passed；根测试：20 passed。
- bridge 测试：37 passed，包含真实 MCP stdio 工具清单测试。
- skill validator：skill validation passed。
- README Markdown 代码围栏：已检查为真实反引号，围栏成对闭合。
- CI 配置：已覆盖 Windows 与 Ubuntu、多版本 Python、根测试和 bridge 测试。
- README 本地相对链接：已检查可解析。
- UTF-8 文件编码：无替换字符。

在 `mcp-antigravity-bridge/` 目录执行：

```powershell
python -m pytest -q
python -m compileall -q src
```

bridge 测试与编译检查均通过。单元测试 mock 进程边界，不需要真实 Antigravity 登录。

## 🔒 当前边界 · Scope and Boundaries

- 不启动、嵌入或控制 Antigravity 桌面 GUI。
- 不保存或转发 Antigravity OAuth、代理凭据或私有 Codex 配置。
- 不自动委托生产操作、不可逆操作、跨项目写入或范围不明任务。
- `dangerously_skip_permissions` 默认关闭。
- 异步 job 状态保存在当前 bridge 进程内；进程重启后旧 job id 会变成 `unknown`。
- 协同会话最多 4 个任务；每个任务使用一个独立 agy 进程、分支和 worktree。
- `terminal` 显示方式当前只支持 Windows，并且每个运行任务打开一个可见控制台。
- 当前没有发布到 PyPI 的版本化流程。

## 🛣️ 下一步路线 · Roadmap

### 🔴 优先级高 · High Priority

- 在命令和配置接口稳定后发布版本化 Python 包。
- 补充认证失败、超时和空输出的诊断信息。

### 🟡 优先级中 · Medium Priority

- 在不改变一次性工具契约的前提下探索可选 streaming output。
- 增加更完整的真实机器 smoke check 文档。

### 🟢 优先级低 · Low Priority

- 如果分发需求值得，评估 TypeScript 或 Go 实现。

## 🗂️ 关键文件 · Key Files

| 文件 | 作用 |
| --- | --- |
| `mcp-antigravity-bridge/src/codex_agy_bridge/` | MCP bridge runtime |
| `README.md` | 中文项目入口 |
| `README.en.md` | English project entry point |
| `PROGRESS.md` | 当前中文进度 |
| `PROGRESS.en.md` | English project progress |
| `docs/README.md` | 中文文档索引 |
| `docs/README.en.md` | English documentation index |
| `mcp-antigravity-bridge/README.md` | 运行时技术手册 / Runtime technical manual |
| `skills/agy-supervisor/SKILL.md` | Supervisor 行为规则 |
| `skills/agy-supervisor/references/` | 状态机、计划和协议参考 |
| `scripts/install.ps1` | Windows 安装器 |
| `scripts/install.sh` | POSIX 安装器 |
| `scripts/validate_skill.py` | skill package 验证器 |
| `tests/` | skill、分发与压力场景测试 |

## 🔗 参考资料 · References

- [Antigravity CLI](https://github.com/google-antigravity/antigravity-cli)
- [Antigravity CLI 文档](https://antigravity.google/docs/cli/overview)
- [Model Context Protocol](https://modelcontextprotocol.io/)

## 📄 许可证 · License

Apache-2.0
