<div align="center">

# Codex <sup>×</sup> Antigravity

### 一个把 Codex 的判断力与 `agy` 的执行力接起来的本地 MCP 桥接器

<p>
  <a href="https://github.com/crazyzhang277/codex-antigravity-bridge"><img src="https://img.shields.io/badge/status-active-16a34a?style=for-the-badge" alt="项目状态：可用"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.10 及以上"></a>
  <a href="https://modelcontextprotocol.io/"><img src="https://img.shields.io/badge/MCP-local%20stdio-111827?style=for-the-badge" alt="本地 MCP stdio"></a>
  <a href="https://github.com/google-antigravity/antigravity-cli"><img src="https://img.shields.io/badge/Antigravity-agy%20CLI-4285F4?style=for-the-badge&logo=google&logoColor=white" alt="Antigravity agy 命令行"></a>
</p>

<p><strong>规划清晰 · 授权明确 · 验收完整</strong></p>

<p>
  <a href="#快速开始">快速开始</a> ·
  <a href="#四个工具">工具</a> ·
  <a href="#监督模式">监督模式</a> ·
  <a href="mcp-antigravity-bridge/README.md">英文技术手册</a>
</p>

</div>

> [!IMPORTANT]
> **仅使用命令行。** 本项目只调用 Antigravity 的无界面 `agy` 命令行，不启动、嵌入或控制 Antigravity 桌面应用。

## 🧭 一眼看懂

| Codex | 本地桥接器 | Antigravity |
| :---: | :---: | :---: |
| 规划 · 拆分 · 验收 | MCP · 权限边界 · 独立工作区 | 实现 · 测试 · 返回结果 |

```mermaid
flowchart LR
    C[Codex] -->|本地 MCP stdio| B[codex-agy-bridge]
    B -->|子进程 / ConPTY| A[agy -p]
    A --> G[Antigravity 执行器]
```

这个项目的核心取舍很简单：**让 Codex 保持监督权，让 `agy` 只做清晰、可回滚、可验收的子任务。**

## ✨ 为什么值得用

<table>
<tr>
<td width="25%"><strong>原生接入</strong><br><sub>注册为 MCP 服务器，Codex 桌面版和命令行都能直接调用。</sub></td>
<td width="25%"><strong>命令行优先</strong><br><sub>复用 `agy` 自己的登录、工作区和权限流程。</sub></td>
<td width="25%"><strong>Windows 友好</strong><br><sub>支持中文路径，并在空输出时尝试 ConPTY 回退。</sub></td>
<td width="25%"><strong>小而清楚</strong><br><sub>本地 stdio、四个工具，没有网页服务器和数据库。</sub></td>
</tr>
</table>

## 🚀 快速开始

### 01 · 安装仓库

**Windows PowerShell**

```powershell
git clone https://github.com/crazyzhang277/codex-antigravity-bridge.git
cd codex-antigravity-bridge
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

安装器会自动读取当前环境、Windows 系统代理和常见本地代理端口，并且只写入当前用户的 `codex-agy-bridge` MCP 配置。使用不同代理软件时，可以显式指定地址：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -ProxyUrl "http://127.0.0.1:7897"
```

不同代理软件没有统一端口。自动检测不到时，请使用 `-ProxyUrl`；项目不会假设所有用户都使用同一个端口。

**macOS / Linux**

```bash
git clone https://github.com/crazyzhang277/codex-antigravity-bridge.git
cd codex-antigravity-bridge
sh scripts/install.sh
```

安装脚本会安装桥接器、复制 `agy-supervisor` 技能，并幂等注册 `codex-agy-bridge`。它不会安装或保存 Antigravity OAuth 凭据。

### 02 · 安装并登录 `agy`

Windows PowerShell：

```powershell
irm https://antigravity.google/cli/install.ps1 | iex
agy --version
agy
```

按照交互式提示完成登录。macOS / Linux 请参考 [Antigravity CLI 文档](https://antigravity.google/docs/cli/overview)。

### 03 · 验证连接

```powershell
agy -p "Reply exactly AGY_OK"
codex mcp list
```

看到 `AGY_OK`，并在 MCP 列表中看到 `codex-agy-bridge`，就可以在 Codex 中使用它。

## 🧰 四个工具

| 工具 | 作用 | 返回 |
| --- | --- | --- |
| `agy_ask` | 同步执行一次受控 CLI 任务 | 清理后的文本 |
| `agy_ask_json` | 请求结构化 CLI 输出，并拒绝非法 JSON | JSON 输出文本 |
| `agy_start` | 在调用方提供的独立 worktree 中异步启动任务 | `job_id` |
| `agy_status` | 查询异步任务 | 状态与结果 JSON |

常用的只读请求：

```text
只调用一次 agy_ask。检查 README.md，返回三个具体改进建议。
任务必须只读，使用仓库根目录作为 workdir，不要修改文件。
```

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `prompt` | 必填 | 交给 Antigravity 的任务说明 |
| `workdir` | `""` | 空字符串表示继承当前目录 |
| `timeout` | `300.0` | 硬超时时间，单位为秒 |
| `dangerously_skip_permissions` | `false` | 仅在明确授权的可信任务中启用 |

## 🛡️ 监督模式

安装 `agy-supervisor` 技能后，Codex 仍然是监督者，`agy` 只是受控实现者。

```mermaid
stateDiagram-v2
    [*] --> Scope
    state "范围确认" as Scope
    state "委派执行" as Delegate
    state "结果审查" as Review
    state "纠正处理" as Correct
    state "合并结果" as Merge
    state "停止任务" as Stop
    [*] --> Scope
    Scope --> Delegate: 用户明确授权
    Delegate --> Review: agy 返回
    Review --> Correct: 测试或范围问题
    Correct --> Review: 最多两次纠正
    Review --> Merge: 验收通过
    Review --> Stop: 越界 / 超时 / 无进展
    Merge --> [*]
    Stop --> [*]
```

- 普通开发请求不会自动调用 `agy`。
- 只有用户明确要求 Antigravity 协作，或明确开启本次 supervisor mode，才会委派。
- Codex 负责拆分任务、文件边界、权限检查、diff 审查和测试验收。
- 每个子任务最多三次调用：首次实现加两次纠正。
- 测试通过、越界、无进展、超时或需要用户决定时立即停止。

明确授权示例：

```text
开启 supervisor mode。让 Antigravity 在当前项目实现用户设置页。
只允许修改 settings 页面及其专属组件，完成后运行相关测试。
```

## 🧩 多页面协同

适合异步 worktree 协同的任务必须满足：页面可独立实现、共享契约明确、文件边界互斥，并且没有其他进程同时修改相关文件。

```text
页面 1：dashboard/，只允许修改 dashboard 页面和专属组件
页面 2：settings/，只允许修改 settings 页面和专属组件
页面 3：reports/，只允许修改 reports 页面和专属组件
```

Codex 会先把计划写入 `docs/agy-plans/`，创建并验证独立工作区，再把该目录作为 `workdir` 传给 `agy_start`。桥接器不会自动创建 Git 工作区，也不会替代监督者做边界审查。任务结束后检查 `agy_status`、差异和测试结果，再决定是否合并。

## ⚙️ 配置与 Windows 支持

推荐使用 Codex CLI 注册：

```powershell
codex mcp add codex-agy-bridge -- python -m codex_agy_bridge
```

<details>
<summary>手动 MCP 配置</summary>

```toml
[mcp_servers.codex-agy-bridge]
command = "python"
args = ["-m", "codex_agy_bridge"]
startup_timeout_sec = 120
```

</details>

<details>
<summary>Windows 路径与 ConPTY</summary>

如果 `agy` 不在 `PATH`：

```powershell
$env:AGY_PATH = "C:\path\to\agy.exe"
```

建议安装 Windows 终端回退依赖：

```powershell
python -m pip install -e ".\mcp-antigravity-bridge[winpty]"
```

把真实项目目录传给工具的 `workdir`，不要把机器专属路径硬编码进 MCP 配置。

</details>

## 🔒 安全边界

- 通信只经过本地 MCP stdio。
- `dangerously_skip_permissions` 默认关闭。
- 不自动委托生产操作、不可逆操作、跨项目写入或范围不明的任务。
- 不把 OAuth 材料、密钥、代理凭据或私有 Codex 配置传给 `agy`。
- `agy` 修改了禁止文件、输出为空或超时时，监督流程会停止并报告，不会无限重试。

## ✅ 验证

```powershell
# 桥接器测试
cd mcp-antigravity-bridge
python -m pytest -q
python -m compileall -q src

# 仓库检查
cd ..
python -m pytest -q
python scripts/validate_skill.py
git diff --check
```

单元测试会 mock 进程边界，不要求真实 Antigravity 登录。

## 🗂️ 项目结构

```text
.
├── mcp-antigravity-bridge/       # 本地 MCP runtime
├── skills/agy-supervisor/        # Codex supervisor skill
├── scripts/                      # 安装与验证脚本
├── tests/                        # skill 与分发回归测试
├── docs/superpowers/             # 设计与执行记录
├── research/                     # 研究资料与历史比较
├── PROGRESS.md
├── LICENSE
└── README.md
```

## 📚 继续阅读

| 想了解 | 从这里开始 |
| --- | --- |
| 安装、工具、运行机制和排错 | [英文桥接器技术手册](mcp-antigravity-bridge/README.md) |
| 委派规则和验收协议 | [Supervisor skill](skills/agy-supervisor/SKILL.md) |
| 分发与 skill 验证 | [validate_skill.py](scripts/validate_skill.py) |
| 项目进度 | [PROGRESS.md](PROGRESS.md) |

## 🔗 参考资料

- [Antigravity CLI](https://github.com/google-antigravity/antigravity-cli)
- [Antigravity CLI 文档](https://antigravity.google/docs/cli/overview)
- [Model Context Protocol](https://modelcontextprotocol.io/)

## 📄 许可证

Apache-2.0
