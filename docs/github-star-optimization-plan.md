# GitHub Star 优化实施方案

## 文档目的

这是一份交给另一个 Codex 对话执行的实施文档。

目标不是继续扩展 `codex-antigravity-bridge` 的底层功能，而是提高陌生访客从“看到仓库”到“理解项目、成功安装、完成第一次协作、愿意 Star”的转化率。

本方案基于 2026-08-09 的仓库快照编写，执行前必须重新读取当前文件和 Git 状态，不要假设工作区仍然与本方案创建时完全一致。

## 当前仓库

项目路径：`<repo-root>`

项目地址：

```text
https://github.com/crazyzhang277/codex-antigravity-bridge
```

当前实现的核心能力：

- 通过本地 MCP stdio 让 Codex 调用 `agy` CLI
- `agy_ask`：单次同步委派
- `agy_ask_json`：结构化 JSON 输出
- `agy_start` / `agy_status`：异步任务
- `agy_collab_start` / `agy_collab_status`：多任务、独立 Git worktree 和协作状态
- `agy-supervisor` skill：范围确认、任务边界、权限、安全和验收规则
- Windows ConPTY 回退和代理探测
- Windows、Ubuntu、多版本 Python CI

研究快照结果（执行前重新验证）：

- 仓库测试：57 passed
- 当前仓库没有 Git tag
- 当前仓库没有 GitHub Release
- GitHub 页面检查时为 0 Star
- 代码与文档已经有较多内容，但入口信息密度过高

## 核心定位决定

不要把项目定位成“另一个 Antigravity bridge”。同类项目已经存在，单纯调用 `agy` 不足以构成差异。

项目应该突出以下核心价值：

> **让 Codex 在保持监督权的前提下，把前端、后端和测试等互不冲突的任务分配给 Antigravity，并在独立 Git worktree 中并行执行，最后由 Codex 检查并由用户决定是否合并。**

英文一句话：

> **Let Codex supervise parallel Antigravity coding tasks in isolated Git worktrees.**

面向第一次访问者的短描述：

> A local MCP bridge that lets Codex delegate bounded coding tasks to Antigravity, run them in isolated worktrees, and review the results before merge.

中文短描述：

> 一个本地 MCP 桥接器，让 Codex 将受限的编码任务委派给 Antigravity，在独立 worktree 中并行执行，并在合并前审查结果。

## 目标用户

优先服务以下人群，不要把受众写成“所有 AI 用户”：

1. 同时使用 Codex 和 Antigravity CLI 的开发者
2. 希望把前端、后端、测试分开交给不同 Agent 的个人开发者
3. 使用 Git worktree、重视 diff 审查和可回滚的开发者
4. 想尝试多 Agent 编程，但不希望 Agent 直接改当前工作区的开发者
5. 使用 Windows、需要处理中文路径、代理或非 TTY 输出的开发者

明确不作为首要目标：

- 不使用 Codex 的用户
- 不使用 Antigravity CLI 的用户
- 只想让 AI 聊天或生成代码片段的用户
- 需要完整云端 Agent 编排平台的团队

## 竞品处理原则

首页不需要放竞品差异表，也不要声称“市场上没有类似项目”。竞品只用于执行前的内部核对：确认一句话定位没有夸大，确认项目没有把普通的 `agy` 调用包装成新功能。

README 只需要回答一个问题：

> 用户为什么要在自己的 Codex + agy 工作流中增加这一层 worktree 协作和人工验收？

如果无法用真实 Demo 回答这个问题，应先调整产品定位，不要继续堆文档或功能。

## 总体执行原则

### 原则 1：先卖一个工作流，不卖一堆 API

README 第一屏只展示一个最容易理解的故事：

```text
需求：给应用增加设置页
Codex：拆成前端、后端、测试三个任务
Bridge：给每个任务创建独立 worktree
Antigravity：分别实现任务
Codex：查看状态、diff 和测试结果
用户：确认后手动合并
```

### 原则 2：不把 Codex 和 agy 的内部机制写成用户必须先学会的知识

内部机制可以放到技术文档。首页先回答：

- 它解决什么问题
- 谁应该使用
- 我需要安装什么
- 第一次调用怎么做
- 和普通 bridge 有什么不同

### 原则 3：不以“求 Star”作为发布内容

发布内容应该请求真实反馈：

> 我在解决多个 Coding Agent 同时修改项目时的文件冲突和审查问题，做了一个本地 worktree 协作桥接器。欢迎用这个示例尝试一次真实任务。

用户实际安装并获得结果后，Star 才有意义。

### 原则 4：本轮完成一个可验证、可分发的完整闭环

本轮不是只做营销包装，也不是把关键能力推迟到“以后”。首次正式发布前必须同时完成：

- 定位、文档和真实 Demo
- `dry-run` 计划校验
- 实际 changed-files 越界审计
- PyPI 打包
- `pipx` / `uv tool` 安装
- `codex-agy-bridge-setup` 完整安装入口
- Windows、macOS/Linux 安装路径验证
- Release 和 GitHub 仓库元数据

允许为上述闭环修改核心运行时、MCP 工具参数、打包配置和安装逻辑，但每个改动都必须有测试和明确的用户可见行为。

不要在本轮新增：

- Web UI
- 云端服务
- 账号系统
- 自动合并
- 自动执行任意命令
- Agent 记忆系统
- 新的多 Agent 协议
- 模型路由平台

## Phase 0：验证真实用户，同时推进完整交付

真实用户验证、核心实现、分发和发布属于同一个版本，不能以“先验证、以后再做 PyPI 或 dry-run”为结论。执行顺序可以先验证需求，再实现和打包，但所有阶段都必须在首次正式 Release 前完成。

项目要求用户同时拥有 Codex、Antigravity CLI、Git、Python 和可用登录环境，受众天然较窄，不能只凭概念判断需求。

寻找 3～5 个已经使用 Codex 和 `agy` 的开发者，让他们完成一次真实任务：

```text
后端：增加 GET /api/items
前端：增加 items 页面
测试：增加接口测试
```

任务必须使用互不重叠的 `owned_paths`。记录安装时间、卡住的位置、是否理解 worktree、是否能看懂状态结果，以及用户是否愿意继续使用。

继续执行实现、分发和发布的最低标准：

```text
至少 3 个陌生用户完成安装
至少 2 个陌生用户完成一次协同任务
至少 1 个用户提交具体反馈或继续使用
```

如果用户只觉得概念有趣但没有运行，应先改定位，不要用 README、Release 或社区发帖掩盖需求不足。

## Phase 1：重构 GitHub 首页 README

### 目标

让陌生开发者在 30 秒内理解项目，并知道第一次使用的最短路径。

### 文件

- `README.md`
- `README.en.md`
- `docs/README.md`
- `docs/README.en.md`

### README 第一屏结构

第一屏只保留以下内容，顺序不要改变：

```text
# Codex AGY Supervisor

一句英文主描述
一句中文辅助描述（如果英文作为默认首页）

演示 GIF 或静态架构图

Install / Quick Start / Demo / Security 链接

Supported platforms and prerequisites
```

推荐标题候选，执行时选一个并保持全仓库一致：

- `Codex AGY Supervisor`
- `Codex x Antigravity Worktree Bridge`
- `AGY Supervisor for Codex`

建议采用 `Codex AGY Supervisor` 作为产品名称，`codex-agy-bridge` 继续作为 Python 包和 MCP server 名称，避免破坏已有配置。

### 第一屏必须包含的真实句子

```text
Codex plans and reviews. Antigravity implements bounded tasks in isolated Git worktrees.
```

中文：

```text
Codex 负责规划和审查，Antigravity 在独立 Git worktree 中实现受限任务。
```

### 移动 CC Switch 警告

当前 README 在项目价值之前放置了很长的 CC Switch 配置警告。应调整为：

- 首页前 100 行内不出现详细 CC Switch 故障排查
- 在 Quick Start 后增加简短提示：`Using CC Switch? See troubleshooting.`
- 详细恢复步骤移动到 README 后部或独立文档
- 保留现有 issue 链接和技术事实，不删除内容

注意：CC Switch 是部分用户遇到的环境问题，不是本项目的核心卖点。不要让首页看起来像“CC Switch 配置故障修复仓库”。

### 首页只展示一个主流程

在 README 前半部分只展示协同开发 MVP：

```text
Codex backend + AGY frontend + tests
```

使用一个最小的 JSON 调用示例，不能让读者同时阅读六个工具的完整参数。

示例应包含：

- `project_dir`
- `shared_contract`
- `tasks`
- `owned_paths`
- `acceptance`
- `verification`
- `display_mode`

解释重点：

- `owned_paths` 用于声明任务边界，并检查不同任务的声明是否重叠
- `shared_contract` 作为任务提示中的共享约定，防止前后端各自猜接口
- `acceptance` 和 `verification` 让结果可检查
- bridge 不自动合并，用户保留最终决定权

不要把 `owned_paths` 描述成强制沙箱。当前实现会校验任务声明并把边界传给 Agent，但不会在任务完成后强制阻止 Agent 修改声明之外的文件。

### 把六个工具放到后面

保留工具表，但移动到主流程之后。工具表改成面向场景：

| 我想做什么 | 调用 |
| --- | --- |
| 让 agy 直接完成一次只读分析 | `agy_ask` |
| 获取机器可读结果 | `agy_ask_json` |
| 让 Codex 继续工作，同时运行一个独立任务 | `agy_start` |
| 查询异步任务 | `agy_status` |
| 并行执行多个有边界的任务 | `agy_collab_start` |
| 查看协同任务和差异 | `agy_collab_status` |

### 增加“为什么不是普通 bridge”部分

放在首页前半部分，内容应明确、克制：

```markdown
## Why this project

Most bridges stop at starting `agy`. This project focuses on the supervision
boundary around a coding task:

- each task declares its owned paths;
- each task can run in its own worktree;
- the shared contract is passed explicitly to the task;
- results expose status, changed files, and verification metadata;
- the bridge never auto-merges or silently expands the task scope.
```

不要声称项目比所有竞品都安全或更强，只描述当前代码真正提供的行为。

### 增加“适合与不适合”

```markdown
## Is this for you?

Use it when you already use Codex and the Antigravity CLI and want to delegate
independent coding tasks without letting workers share the same worktree.

This is not a hosted agent platform, a replacement for Codex, an Antigravity
desktop controller, or an automatic merge bot.
```

这会减少错误用户安装后因为预期不符而产生的负面反馈。

## Phase 2：制作可理解的 Demo

### 目标

让用户看到“为什么需要这个 bridge”，而不是看到一张抽象架构图。

### 前置条件必须说清楚

真实协同 Demo 需要 Codex、Antigravity CLI、`agy` 登录、Git 和一个可运行的示例项目。README 和视频中必须明确这些前置条件，不能让用户以为下载仓库即可完成真实运行。

### 新增文件

建议新增：

```text
docs/demo.md
docs/assets/collaboration-flow.gif
examples/collaboration-demo/README.md
examples/collaboration-demo/shared-contract.md
```

如果暂时不能制作 GIF，先放一张脱敏的真实终端截图和完整日志；不要放假的成功输出。

### Demo 场景

使用一个极小的示例项目，不要用当前仓库自身作为唯一示例。示例项目可以包含：

```text
demo-app/
├── backend/
├── frontend/
└── tests/
```

Demo 任务：

```text
backend：增加 GET /api/items
frontend：增加 items 页面
tests：增加接口测试
```

每个任务的 `owned_paths` 必须互不重叠。共享契约应明确接口、字段和验证命令。

### Demo 必须展示的过程

1. Codex 或调用方提交任务契约
2. bridge 创建独立 worktree
3. 每个任务返回自己的分支和状态
4. `agy_collab_status` 显示运行中、完成、失败、改动文件和 diff 检查
5. Codex/用户查看变更
6. 用户手动合并

Demo 必须同时展示真实执行和 `dry-run` 预检：真实执行证明产品能完成协同工作流，`dry-run` 证明用户可以在不启动 `agy`、不消耗登录状态的情况下先发现配置错误。`dry-run` 不能伪装成真实 Agent 执行，也不能被描述为完整安全沙箱。

### `dry-run` 必须在本版本完成

在 `agy_collab_start` 增加 `dry_run: bool = False`，保持默认值为 `False`，不破坏现有调用。`dry_run=true` 时必须：

1. 校验 `project_dir` 是现有 Git 仓库根目录
2. 校验 `base_ref` 可解析为 commit
3. 校验任务格式、任务数量、`owned_paths` 和共享契约
4. 拒绝任务之间重叠的 `owned_paths`
5. 计算 session id、分支名、worktree 目录和每个任务的摘要
6. 返回 `state: "dry-run"` 以及完整的计划结果
7. 不调用 `agy`，不创建持久化 worktree，不创建 job，不修改 Git 仓库

返回结果至少包含：

```json
{
  "state": "dry-run",
  "session_id": "planned-session-id",
  "project_dir": "...",
  "base_ref": "HEAD",
  "worktree_root": "...",
  "tasks": [
    {
      "task_id": "backend",
      "branch": "codex-agy/<session>/backend",
      "workdir": "...",
      "owned_paths": ["backend/"],
      "acceptance": ["..."],
      "verification": ["..."]
    }
  ]
}
```

`dry-run` 测试必须覆盖：无效 `base_ref`、重叠路径、非法任务、未知 `project_dir`、不会调用 subprocess `agy`、不会留下 worktree 或 job，以及返回计划字段完整。计划路径可以使用临时目录或明确的占位符，不能把开发者本机绝对路径写进 Demo、快照或文档。

### 实际 changed-files 越界审计必须在本版本完成

当前 `owned_paths` 主要是任务契约校验和 Agent 提示，不能宣传成强制沙箱。真实任务完成后，对每个 worktree 收集并合并以下文件集合：

- 相对 `base_ref` 已提交的 changed files
- 未提交的 modified/deleted files
- untracked files

将结果与该任务的 `owned_paths` 比较，并在 `agy_collab_status` 中返回：

```text
scope_status: passed | violated | unknown
scope_violations: ["path/to/file"]
changed_files: [...]
```

无法可靠判断时返回 `unknown`，不能默认为 `passed`。发现越界只报告、不自动撤销文件、不自动合并；Codex 和用户仍负责审查。测试必须覆盖 committed、uncommitted、untracked、删除文件、路径规范化和越界结果。

### Demo 禁止的表达

不要写：

- “完全自动开发”
- “无需人工审查”
- “保证多个 Agent 不会出错”
- “替代软件工程师”

当前项目的真实价值是边界控制和并行协作，不是自动保证代码正确。

## Phase 3：一次性完成可分发安装

### 目标

首次正式 Release 必须同时支持源码安装、PyPI 安装、`pipx` 安装和 `uv tool` 安装。四条路径最终都要安装同一个 MCP runtime、同一个 `agy-supervisor` skill，并由同一个 setup 命令完成 Codex 注册和代理配置，不能维护四套不同逻辑。

项目依赖仍然需要用户提供：

- Python 3.10+
- Codex CLI
- Antigravity CLI（命令为 `agy`）
- Git
- 可用的 `agy` 登录状态
- 需要时的网络代理

安装器可以检查这些条件，但不能替用户安装 Codex、`agy` 或保存 OAuth 凭据。未登录 `agy` 时应给出清晰提示，允许安装完成，但在验证步骤明确要求用户自行运行 `agy` 完成登录。

### 统一的 Python 包结构

调整 `mcp-antigravity-bridge/pyproject.toml` 和包资源，使 wheel/sdist 中包含：

1. `codex_agy_bridge` MCP runtime
2. `agy-supervisor` skill 的完整目录，包括 `SKILL.md`、`agents/` 和 `references/`
3. `codex-agy-bridge` MCP runtime console script
4. `codex-agy-bridge-setup` 安装和配置 console script

skill 必须从包资源读取，不允许安装后依赖当前 Git checkout 的 `skills/agy-supervisor`。应把 skill 的唯一可发布源放进 Python 包资源目录，并更新校验脚本和测试，避免根目录脚本与 wheel 内容长期分叉。

### `codex-agy-bridge-setup` 的职责

新增独立安装命令，例如：

```text
codex-agy-bridge-setup
codex-agy-bridge-setup --what-if
codex-agy-bridge-setup --proxy-url http://127.0.0.1:7890
codex-agy-bridge-setup --no-proxy
```

该命令必须负责：

1. 检查 Python 版本、`codex` 命令和 Codex 配置目录
2. 将内置 `agy-supervisor` skill 安装到 `${CODEX_HOME}/skills/agy-supervisor`
3. 安装或更新名为 `codex-agy-bridge` 的 Codex MCP 注册
4. 使用当前包所在解释器启动 `codex_agy_bridge`，避免依赖用户当前目录
5. 只为该 MCP server 写入代理环境变量，并保留其他 MCP 配置
6. 检查 `agy` 是否存在；不存在或未登录时输出可执行的下一步
7. 提供幂等行为：重复执行不会复制嵌套目录、重复注册或累积代理配置
8. `--what-if` 只打印计划，不写入 skill、不修改 Codex 配置、不运行 `agy`

安装器必须明确说明：它不读取、不打印、不保存 `agy` 的 OAuth token 或其他认证凭据。代理 URL 可以被写入 Codex 的 MCP 环境配置，但不能出现在测试快照、日志示例或提交内容中。

### 四条官方安装路径

#### Path A：PyPI + pipx

README 首屏给出：

```powershell
pipx install codex-agy-bridge
codex-agy-bridge-setup
```

验证：

```powershell
codex mcp list
agy -p "Reply exactly AGY_OK"
```

#### Path B：PyPI + uv

同时支持：

```powershell
uv tool install codex-agy-bridge
codex-agy-bridge-setup
```

Linux/macOS 使用对应的 `python3`、`codex` 和 shell 命令。不要把 `pipx` 和 `uv` 写成只安装 CLI 的实验性选项，它们必须经过干净环境验证并作为首次 Release 的正式路径。

#### Path C：源码安装

仓库脚本保留给需要代理探测、开发分支或本地修改的用户：

```powershell
git clone https://github.com/crazyzhang277/codex-antigravity-bridge.git
cd codex-antigravity-bridge
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

`scripts/install.ps1` 和 `scripts/install.sh` 只负责解析源码目录、安装 editable package，然后调用同一个 `codex-agy-bridge-setup`。代理探测、MCP 注册、skill 复制不能在两个脚本中各自维护一份实现。

#### Path D：开发者安装

放入 Development 部分，包含 editable install、测试命令和构建命令；不要让普通使用者先阅读 pytest、compileall 或项目目录结构。

### 构建和干净环境验收

在发布前必须执行：

```powershell
python -m build mcp-antigravity-bridge
python -m twine check mcp-antigravity-bridge\dist\*
```

然后使用全新的环境分别安装 wheel，验证：

```text
pip install <wheel>
pipx install <wheel>
uv tool install <wheel>
```

每种环境都必须验证：

- `codex-agy-bridge` console script 可执行
- `codex-agy-bridge-setup --what-if` 可执行且无副作用
- 包内 skill 资源存在且文件完整
- setup 命令能安装 skill 并注册 MCP
- MCP 注册使用的是当前安装环境的解释器
- 未安装或未登录 `agy` 时提示清晰，不泄露凭据
- Windows `winpty` 作为可选依赖继续工作，基础安装不强制引入无关依赖

增加包构建测试、package-data 测试、console script 测试、clean virtualenv 安装测试、`pipx`/`uv` 本地 wheel 测试，以及 Windows 安装脚本 `-WhatIf` 测试。包内容未通过检查前，不上传正式 PyPI 版本。

### PyPI 发布闸门

`codex-agy-bridge` 包名必须先通过 PyPI 查询和本地构建验证，确认项目拥有发布权限后再上传。包上传属于本版本交付的一部分，不是下一阶段功能；但正式上传、版本号和 Release 内容仍需用户确认。

### GitHub Release

至少创建：

```text
v0.1.0 - Supervised parallel worktree collaboration
```

Release 必须同时给出源码安装、PyPI、`pipx` 和 `uv tool` 安装方式，以及：

- 适用范围和完整前置条件
- 已验证平台
- dry-run 用法
- changed-files 审计限制
- 当前已知限制
- PyPI 包版本
- 测试结果

如果没有编译二进制，不要伪装成桌面应用；发布 Python wheel/sdist 和 GitHub 源码 Release 即可。

## Phase 4：补齐 GitHub 仓库元数据

### 仓库描述

建议改成：

```text
Let Codex supervise parallel Antigravity coding tasks in isolated Git worktrees.
```

### Topics

设置与项目真实能力一致的 topics：

```text
codex
antigravity
agy
mcp
multi-agent
ai-coding
git-worktree
developer-tools
python
```

不要添加与项目无关的 `llm`、`automation` 等宽泛标签来制造搜索曝光。

### 建议新增或确认的仓库文件

```text
CONTRIBUTING.md
CHANGELOG.md
SECURITY.md
.github/ISSUE_TEMPLATE/bug_report.yml
.github/ISSUE_TEMPLATE/feature_request.yml
.github/PULL_REQUEST_TEMPLATE.md
```

这些文件不需要很长。重点是让第一位外部用户知道如何：

- 报告 agy 版本、Codex 版本和平台
- 提交最小复现
- 说明是否使用代理
- 说明任务 worktree 和改动路径
- 贡献代码而不触碰安全边界

### README 语言策略

如果目标是国际 Star，建议让 `README.md` 作为英文首页，把中文移到：

```text
README.zh-CN.md
```

首页保留明显的中文链接。

如果暂时不能改默认语言，至少让英文描述、英文安装路径和英文 Demo 出现在中文内容之前或与中文内容同等明显的位置。

不要只维护两份内容不同的 README。每次功能变化都要同步两份首页的：

- 一句话定位
- 安装步骤
- 工具名称
- 当前限制
- 版本号

## Phase 5：安全和信任文案

当前安全边界是项目的重要优势，但应该从“长规则”改为“用户能理解的承诺”。首页只保留 4 条：

```text
- 通信通过本地 MCP stdio。
- 默认不启用 dangerously_skip_permissions。
- 每个协同任务可以声明独立 owned_paths。
- bridge 不自动合并、不删除 worktree、不替用户决定最终合并。
```

不要把“任务路径声明”和“实际文件修改强制隔离”混为一谈。当前实现能检查声明之间的路径重叠，并在 prompt 中提示禁止路径；如果要强制审计实际 changed files，需要另一个明确的代码任务，不能只通过 README 宣称已经实现。

详细规则继续放在 `skills/agy-supervisor/SKILL.md` 和运行时技术手册。

安全文档必须明确：

- `agy` 仍然拥有它自己的工具权限和登录状态
- bridge 不保存 OAuth 凭据
- `dangerously_skip_permissions=true` 不是普通加速开关
- 用户不应把生产目录作为不受控任务的 workdir
- `ready_for_review` 不等于验收通过

## Phase 6：内容发布计划

### 第一篇发布文章

标题建议：

```text
I built a local MCP bridge that lets Codex supervise parallel Antigravity tasks in isolated Git worktrees
```

文章只讲一个真实问题：

> 让多个 Coding Agent 直接改同一个工作区，会发生文件冲突、边界不清和结果难以审查。这个项目用独立 worktree、owned paths、shared contract 和人工合并解决这条流程问题。

文章结构：

1. 问题：多个 Agent 直接修改同一工作区
2. 目标：并行开发但保持边界和审查
3. 30 秒 Demo
4. 安装命令
5. 设计取舍：为什么不自动合并
6. 当前限制
7. 欢迎反馈具体失败案例

### 发布渠道

针对国际开发者：

- Hacker News `Show HN`
- Reddit：`r/opensource`、`r/SideProject`、与本地 AI 编程相关社区
- GitHub Discussions
- 相关 MCP、Codex、Antigravity 项目 Discussions/Issues，只有在内容直接相关时发布

针对中文开发者：

- V2EX
- 掘金
- 知乎
- B 站短演示
- AI 编程和 MCP 相关群组

不要同一篇内容复制到所有地方；每个平台都用对应语境，避免刷屏。

### 发布后的反馈方式

不要只问“能不能 Star”。应该问：

- 你使用 Codex 和 agy 的什么版本？
- 你想并行处理什么任务？
- 你最担心文件冲突、权限还是审查？
- 你能否完成第一次安装？卡在哪一步？
- 你是否会使用独立 worktree 模式？

## Phase 7：数据指标

Star 是结果，不是唯一指标。每个发布周期记录：

- GitHub Star 增量
- Release 下载量
- README 访问量，如 GitHub Insights 可用
- 安装失败 Issue 数量
- 第一次成功运行反馈数
- 外部用户创建的 Issue 数量
- 外部 PR 数量
- 是否有用户在自己的仓库中使用

第一个阶段目标：

```text
5 个陌生用户成功安装
3 个陌生用户完成协同 Demo
2 个用户提交真实问题或改进建议
1 个外部用户持续使用
```

达到这些目标后再扩展功能，不要为了 Star 数量盲目增加 MCP 工具。

## 建议的执行顺序

下面的 P0/P1/P2 只是同一版本内的工程顺序，不代表功能延期。首次正式 Release 前，三组任务必须全部完成。

### P0：核心行为和真实验证

1. 找到 3～5 个真实目标用户
2. 让至少 2 人完成一次协同任务
3. 确认一句话定位
4. 实现 `dry-run`，并补齐无副作用测试
5. 实现 changed-files 越界审计，并补齐 Git fixture 测试
6. 重写 README 第一屏
7. 移动 CC Switch 长警告
8. 录制一个真实 Demo，同时展示真实执行和 dry-run
9. 明确前置条件、登录要求和当前限制

### P1：完整安装和分发

1. 将 `agy-supervisor` skill 纳入 Python 包资源
2. 实现 `codex-agy-bridge-setup`
3. 让源码安装脚本复用统一 setup 逻辑
4. 完成 `pipx` 和 `uv tool` 的本地 wheel 安装验证
5. 完成干净 Windows、macOS/Linux 环境验证
6. 完成 PyPI metadata、wheel/sdist 构建和 `twine check`
7. 确认 `codex-agy-bridge` 包名可用并准备正式上传

### P2：展示、版本和发布

1. 创建 `v0.1.0` Git tag
2. 创建包含 PyPI 安装说明的 GitHub Release
3. 更新仓库 description 和 topics
4. 添加简短 CHANGELOG 和反馈模板
5. 完成 README 中英版本、Demo、限制和安装命令同步
6. 由用户确认后发布外部文章或社区内容

只有 P0、P1、P2 全部通过，才允许宣布“首次完整发布”。

## 代码变更边界

本次优化允许修改为完成完整闭环所必需的核心代码，但不扩张为新的云端平台或任意 Agent 编排系统。所有核心改动都必须保留现有默认行为，并由单元测试或集成测试覆盖。

允许修改：

- `README.md`
- `README.en.md`
- `README.zh-CN.md`
- `docs/README.md`
- `docs/README.en.md`
- `docs/demo.md`
- `docs/assets/`
- `examples/`
- `CONTRIBUTING.md`
- `CHANGELOG.md`
- `SECURITY.md`
- `.github/ISSUE_TEMPLATE/`
- `.github/PULL_REQUEST_TEMPLATE.md`
- GitHub Release 配置
- `mcp-antigravity-bridge/pyproject.toml`
- `mcp-antigravity-bridge/src/codex_agy_bridge/` 中与 `dry-run`、scope audit、安装器和资源加载有关的代码
- 对应的 `tests/`
- 必要的打包元数据和构建配置

除非为了兼容现有调用必须修改，不要修改：

- 现有 MCP 工具的默认行为
- 现有 MCP 工具名称
- `dangerously_skip_permissions` 的默认行为
- worktree 隔离规则
- 监督模式规则
- 自动合并边界

## 验收标准

### 用户验证验收

- 至少 3 个陌生目标用户完成安装
- 至少 2 个陌生目标用户完成主流程
- 至少 1 个用户提交具体反馈或继续使用

### 文档验收

- 新用户只看 README 第一屏就能回答项目是做什么的
- README 第一屏不再被 CC Switch 警告占据
- 有一个标明前置条件的真实协同开发 Demo
- 有一条清晰的快速安装路径
- 有明确的前置条件和平台支持列表
- 有“不是自动合并机器人”的边界说明
- 没有把 prompt 约束夸大成强制沙箱
- 中英文首页的核心定位、安装和限制一致
- 不出现个人机器绝对路径

### 安装验收

- 已安装 `agy` 和 Codex 的 Windows 用户能按文档完成安装
- macOS/Linux 安装路径至少完成静态验证
- `pipx install codex-agy-bridge` 后可运行 `codex-agy-bridge-setup`
- `uv tool install codex-agy-bridge` 后可运行 `codex-agy-bridge-setup`
- wheel 和 sdist 都包含完整的 `agy-supervisor` skill
- `codex-agy-bridge-setup --what-if` 不写入文件、不修改 Codex 配置、不运行 `agy`
- setup 命令重复执行不会重复注册 MCP 或破坏已有配置
- 安装脚本不打印、读取或写入 OAuth 凭据
- 代理配置只写入当前项目需要的 MCP 环境
- `codex mcp list` 能确认注册结果

### `dry-run` 和 scope audit 验收

- `dry_run=true` 不启动 `agy`
- `dry_run=true` 不创建持久化 worktree、job 或 Git 分支
- `dry_run=true` 返回 session、branch、workdir、任务摘要和校验结果
- 非法 `base_ref`、非法任务和重叠 `owned_paths` 会在启动前失败
- `agy_collab_status` 返回 `scope_status` 和 `scope_violations`
- committed、uncommitted、untracked 和 deleted 文件都参与审计
- 越界结果只报告，不自动删除、回滚或合并
- 无法确定范围时返回 `unknown`，不得默认为 `passed`

### 代码回归验收

执行：

```powershell
python -m pytest -q
python scripts/validate_skill.py
python -m compileall -q mcp-antigravity-bridge/src
python -m build mcp-antigravity-bridge
python -m twine check mcp-antigravity-bridge\dist\*
git diff --check
```

不得因为本次改动导致现有 57 个测试退化，并新增 dry-run、scope audit、资源打包、setup 命令和安装脚本测试。真实 Antigravity 登录不应作为 CI 必需条件。

### 发布验收

- 仓库 description 已更新
- topics 已更新
- 至少有一个 Git tag
- 至少有一个 GitHub Release
- PyPI 上的包名、版本、wheel 和 sdist 与 Release 一致
- 从 PyPI 安装后可以运行 `codex-agy-bridge-setup`
- `pipx` 和 `uv tool` 的正式安装命令已经在 Release notes 中验证过
- Release notes 包含安装、限制和验证结果
- 没有提交用户机器路径、代理地址、OAuth token 或私有项目内容

## 明确不要做

不要在这次优化中：

- 购买 Star
- 互换 Star
- 批量私信陌生开发者
- 伪造下载量、用户反馈或成功案例
- 宣称“自动完成软件开发”
- 宣称“无需人工审查”
- 把 CC Switch 问题说成 bridge 的核心卖点
- 为了显得强大而增加更多 Agent、模型和协议适配
- 把多个已有 bridge 的功能简单复制到本项目
- 未经用户确认向外部社区发帖、提交 Issue 或创建外部账号

## 交给执行对话的最终指令

请先读取本文件和当前仓库状态，然后按 P0 → P1 → P2 执行完整方案。P0、P1、P2 是同一版本的内部顺序，不得把 PyPI、`pipx`、`uv tool`、`dry-run` 或 changed-files 审计留到后续阶段，也不要在只完成文档时创建正式 Release。只有所有代码、测试、安装、打包、Demo、Release 和验收标准都通过后，才算完成本任务。

外部发帖、提交第三方 Issue、创建外部账号或上传 PyPI 正式版本之前，必须把将要执行的外部动作、包名、版本号和目标地址报告给用户并等待明确确认；这不影响先在本地完成构建、安装和全流程验证。

每个阶段完成后：

1. 汇报修改的文件
2. 说明用户看到的变化
3. 运行对应验收命令
4. 检查是否引入机器专属路径或敏感信息
5. 不要覆盖用户未提交的其他改动

如果发现项目定位与当前代码能力不一致，先停止文案扩展，指出具体代码证据和需要用户决定的差异，不要擅自夸大能力。
