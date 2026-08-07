<div align="center">

# 项目进度 · Codex × Antigravity / Project Progress

### 当前状态：命令行桥接器已可用 · Current status: CLI bridge ready

<p>
  <img src="https://img.shields.io/badge/runtime-ready-16a34a?style=for-the-badge" alt="Runtime ready">
  <img src="https://img.shields.io/badge/tests-37%20passed-2563EB?style=for-the-badge" alt="37 tests passed">
  <img src="https://img.shields.io/badge/CI-GitHub%20Actions-0ea5e9?style=for-the-badge&logo=githubactions&logoColor=white" alt="GitHub Actions CI">
  <img src="https://img.shields.io/badge/license-Apache--2.0-111827?style=for-the-badge" alt="Apache 2.0 license">
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

## 📦 交付状态 · Delivery Status

| 中文区域 | English area | 状态 / Status | 说明 / Notes |
| --- | --- | :---: | --- |
| 命令行桥接运行时 | CLI bridge runtime | ✅ 已完成 / Done | `mcp-antigravity-bridge/` 提供本地 MCP 运行时。 |
| 同步 MCP 工具 | Synchronous MCP tools | ✅ 已完成 / Done | `agy_ask`、`agy_ask_json`。 |
| 异步工作区工具 | Async worktree tools | ✅ 已完成 / Done | `agy_start`、`agy_status`。 |
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

Codex plans and reviews through local MCP stdio. The bridge starts `agy -p` as a bounded subprocess, cleans terminal output, and supports both synchronous calls and explicit asynchronous worktree jobs.

### 📦 Delivered

- Four MCP tools: `agy_ask`, `agy_ask_json`, `agy_start`, and `agy_status`.
- Windows non-ASCII workdir support with ConPTY fallback.
- Direct stderr, nonzero exits, PTY failures, and empty-output diagnostics are preserved.
- Completed async jobs are retained for a finite period and the worker pool has an explicit shutdown path.
- Windows installation resolves a real Python executable, detects common proxy configurations, and writes per-user MCP environment values.
- POSIX installation accepts `PROXY_URL` and mirrors the per-user MCP configuration behavior.
- CI runs repository tests, bridge tests, the real MCP stdio smoke test, skill validation, and compilation checks.

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

Latest verification: root tests 17 passed; bridge tests 19 passed, including the real MCP stdio tool-list smoke test; skill validation passed; compileall passed.

当前结果：

- 根测试：17 passed。
- bridge 测试：19 passed，包含真实 MCP stdio 工具清单测试。
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
| `skills/agy-supervisor/SKILL.md` | Supervisor 行为规则 |
| `skills/agy-supervisor/references/` | 状态机、计划和协议参考 |
| `scripts/install.ps1` | Windows 安装器 |
| `scripts/install.sh` | POSIX 安装器 |
| `scripts/validate_skill.py` | skill package 验证器 |
| `tests/` | skill、分发与压力场景测试 |
| `PROGRESS.md` | 当前中文进度 |

## 🔗 参考资料 · References

- [Antigravity CLI](https://github.com/google-antigravity/antigravity-cli)
- [Antigravity CLI 文档](https://antigravity.google/docs/cli/overview)
- [Model Context Protocol](https://modelcontextprotocol.io/)

## 📄 许可证 · License

Apache-2.0
