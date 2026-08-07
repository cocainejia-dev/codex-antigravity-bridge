<div align="center">

# Codex &lt;-&gt; Antigravity Bridge

让 Codex 通过本地 MCP 调用 Google Antigravity 的 `agy` CLI，把一次性开发任务交给另一个 agent。

<p>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.10+"></a>
  <a href="https://modelcontextprotocol.io/"><img src="https://img.shields.io/badge/MCP-stdio-111827?style=flat-square" alt="MCP stdio"></a>
  <a href="https://github.com/google-antigravity/antigravity-cli"><img src="https://img.shields.io/badge/Antigravity-agy%20CLI-4285F4?style=flat-square&logo=google&logoColor=white" alt="Antigravity agy CLI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-16a34a?style=flat-square" alt="Apache 2.0 license"></a>
</p>

📘 [English technical guide](mcp-antigravity-bridge/README.md)

</div>

## 🚀 一句话说明

这是一个本地 MCP server：Codex 调用 `agy_ask` 或 `agy_ask_json`，bridge 在后台启动 `agy -p`，再把干净的文本结果返回给 Codex。

当前支持的路径只有这一条：

```text
Codex Desktop / Codex CLI
        |
        | MCP over stdio
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

> **边界提醒：** 本项目调用的是 Antigravity 的 headless CLI，不会启动、嵌入或控制 Antigravity 桌面 GUI。

## ✨ 为什么用它？

- **接入方式原生**：注册为 MCP server 后，Codex Desktop 和 Codex CLI 都可以使用。
- **CLI-first**：复用 `agy` 自己的登录、工作区和权限流程。
- **Windows 友好**：处理中文工作目录，并在直接输出为空时尝试 ConPTY。
- **足够小**：本地 stdio、两个工具、没有 Web server，也没有数据库。

## 🛠️ 快速开始

### Windows PowerShell

```powershell
git clone https://github.com/crazyzhang277/codex-antigravity-bridge.git
cd codex-antigravity-bridge
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

### macOS / Linux

```bash
git clone https://github.com/crazyzhang277/codex-antigravity-bridge.git
cd codex-antigravity-bridge
sh scripts/install.sh
```

安装脚本会安装本地 bridge、复制 `agy-supervisor` skill，并在 Codex 中幂等注册
`codex-agy-bridge`。脚本不会替用户安装或保存 Antigravity 凭据。

安装 Antigravity CLI 后，在交互式终端运行一次：

Windows PowerShell 安装命令：

```powershell
irm https://antigravity.google/cli/install.ps1 | iex
```

macOS/Linux 请按照 [Antigravity CLI 文档](https://antigravity.google/docs/cli/overview) 安装。

然后在交互式终端运行一次：

```powershell
agy
```

按照提示完成登录。登录状态由 `agy` 自己保存，通常同一台机器、同一用户只需登录一次；令牌过期、退出登录、清理系统凭据或更换机器后可能需要重新登录。

验证 CLI：

```powershell
agy -p "Reply exactly AGY_OK"
codex mcp list
```

如果只想手动安装 bridge，请参阅 [bridge 技术文档](mcp-antigravity-bridge/README.md)。

## 🧰 两个工具

| 工具 | 签名 | 适合场景 |
| --- | --- | --- |
| `agy_ask` | `agy_ask(prompt, workdir="", timeout=300.0, dangerously_skip_permissions=false)` | 普通文本任务，例如检查代码、解释文件或执行一个受控的小任务。 |
| `agy_ask_json` | `agy_ask_json(prompt, workdir="", timeout=300.0, dangerously_skip_permissions=false)` | 需要结构化 CLI 输出的任务；内部会增加 `--output-format json`。 |

两个工具最终都会调用 `agy -p "你的 prompt"`。示例请求：

```text
Use agy_ask once. Ask Antigravity to inspect README.md and return three concrete documentation improvements. Use the repository root as workdir and keep the task read-only.
```

参数说明：

- `prompt`：交给 Antigravity 的任务说明。
- `workdir`：可选工作目录；空字符串表示继承当前目录。
- `timeout`：硬超时时间，单位为秒，默认 `300.0`。
- `dangerously_skip_permissions`：默认 `false`；仅在 prompt、目录和操作范围都可信时启用。

## 🧭 Codex 监工模式（supervisor mode）

安装 `agy-supervisor` skill 后，Codex 有两种工作模式：

- **普通模式**：普通的开发、修 bug 或重构请求由 Codex 自己完成，不会自动调用 `agy`。
- **监工模式**：只有用户明确说“让 agy/Antigravity 协作”，或明确开启本次任务的协作模式时，Codex 才会调用 `agy_ask`。

监工模式下，Codex 负责拆分任务、指定项目目录和文件边界、验收 diff 与测试；`agy` 负责实现具体的受控子任务。每次调用都是独立的一次性任务，Codex 通过 diff、测试输出和修正提示维持上下文。

示例：

```text
开启监工模式，让 Antigravity 在当前项目中实现用户设置页。
只允许修改 settings 页面及其专属组件，完成后运行相关测试。
```

下列请求不会自动调用 `agy`：

```text
请修复登录页面的按钮样式。
```

除非用户在请求中明确授权 Antigravity 协作。

## 🧩 多页面协同开发（multi-page）

多页面任务适合交给 `agy` 分页面实现，但 Codex 会先确定共享路由、组件、状态和数据接口，再按页面顺序分配任务。例如：

```text
页面 1：dashboard/，只允许修改 dashboard 页面和专属组件
页面 2：settings/，只允许修改 settings 页面和专属组件
页面 3：reports/，只允许修改 reports 页面和专属组件
```

只有满足以下条件才会进入多页面协同：

- 至少两个页面可以独立实现；
- 共享接口、路由和组件约定已经明确；
- 每个页面有独立的文件边界和验收标准；
- 当前工作区没有其他进程同时修改相关文件。

当前 bridge 默认在同一个 `workdir` 中顺序执行，不会让多个 `agy` 进程并发写入同一工作区。每个子任务最多执行三次调用：首次实现加两次修正；测试通过、超出范围、连续无进展、超时或需要用户决定时立即停止。

## 🔐 协作限制

- 不会把密钥、OAuth 材料或私有 Codex 配置传给 `agy`。
- 不自动委托生产操作、不可逆操作、跨项目写入或范围不明的任务。
- `dangerously_skip_permissions` 默认关闭，只有用户明确授权可信目录和任务时才开启。
- 页面之间存在未解决的共享状态、认证、数据库或路由耦合时，不会自动拆分委托。
- 监工发现 `agy` 修改了禁止文件、需要重新登录、输出为空或超时，会停止并报告问题，不会无限重试。

## ⚙️ 手动配置

推荐使用 `codex mcp add`。如果需要写入机器级 Codex 配置，可以使用：

```toml
[mcp_servers.codex-agy-bridge]
command = "python"
args = ["-m", "codex_agy_bridge"]
startup_timeout_sec = 120
```

如果 `agy` 不在 `PATH` 中，可以显式指定：

```powershell
$env:AGY_PATH = "C:\path\to\agy.exe"
```

如果 Codex Desktop 找不到 Python，请把 `command` 换成 Python 的绝对路径。

## 🪟 Windows 注意事项

- 安装 `[winpty]` extra，启用空输出场景的 ConPTY fallback：`python -m pip install -e ".[winpty]"`。
- 尽量把 Codex MCP 的启动命令放在 ASCII 路径下。
- 把真实项目目录传给工具的 `workdir`；bridge 会处理非 ASCII Windows 路径。
- 如果 `agy` 在交互式终端能运行、在 Codex 中却失败，请检查 `AGY_PATH`、继承的环境变量和 CLI 登录状态。

## 🔐 安全边界

- bridge 只通过本地 stdio MCP 与 Codex 通信。
- `dangerously_skip_permissions` 默认是 `false`。
- headless 权限跳过会让 `agy` 不再等待交互确认，只应对可信 prompt、可信目录和可逆操作使用。
- 不要把 Antigravity OAuth 材料、代理凭据或私有 Codex 配置提交到 Git。

## 🧪 验证

在 `mcp-antigravity-bridge/` 目录运行：

```powershell
python -m pytest -q
python -m compileall -q src
```

这些单元测试会 mock 进程边界，不需要真实的 Antigravity 登录。需要做分层真机检查时，可以依次执行：

```powershell
agy -p "Reply exactly DIRECT_AGY_OK" --dangerously-skip-permissions
codex mcp list
```

然后在 Codex Desktop 中用一个小型、可逆的任务调用 `agy_ask`。

## 🧭 项目结构

```text
.
├── mcp-antigravity-bridge/
│   ├── src/codex_agy_bridge/
│   │   ├── agy_runner.py
│   │   ├── server.py
│   │   └── __main__.py
│   ├── tests/test_smoke.py
│   ├── examples/codex-config.toml
│   └── pyproject.toml
├── research/
├── docs/superpowers/
├── PROGRESS.md
├── LICENSE
└── README.md
```

`research/` 保存资料和历史比较；当前支持的运行时就是 `mcp-antigravity-bridge/`。

## 🔗 参考资料

- [Antigravity CLI](https://github.com/google-antigravity/antigravity-cli)
- [Antigravity CLI documentation](https://antigravity.google/docs/cli/overview)
- [Model Context Protocol](https://modelcontextprotocol.io/)
- [agy-headless-bridge](https://github.com/rhishi99/agy-headless-bridge)
- [agy-bridge](https://github.com/sshahzaiib/agy-bridge)

## License

Apache-2.0
