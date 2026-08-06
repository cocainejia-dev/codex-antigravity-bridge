# codex-agy-bridge

让 Codex 通过 MCP 调用 Google Antigravity CLI `agy`，把 Antigravity 当作一个可复用的子智能体。

> **一句话结论**
>
> 本项目不是让 Codex 直接嵌入 Antigravity，而是启动一个本地 MCP bridge。Codex 调用 `agy_ask`，bridge 再启动 `agy -p`，最后把 Antigravity 的回答返回给 Codex。

## 先回答一个关键问题

### 这次到底是桌面版 Codex 调用，还是 Codex CLI 调用？

**已经完成并有完整事件证据的真机测试，是 Codex CLI 调用 `agy`。**

使用的 CLI 路径是：

```text
C:\Users\EDY\AppData\Local\OpenAI\Codex\bin\68de26ad08be95cd\codex.exe
```

测试事件明确显示：

```text
server: codex-agy-bridge
tool: agy_ask
status: completed
result: PARENT_CODEX_AGY_OK
```

Codex 桌面应用和 Codex CLI 都可以作为 MCP 客户端。只要桌面应用重新加载同一份 Codex MCP 配置，它也可以发现并调用 `codex-agy-bridge`。不过，**本次已经跑通的“父 Codex -> MCP -> agy”证明来自 CLI，而不是桌面窗口本身。**

无论客户端是桌面版还是 CLI，后半段都一样：

```text
Codex desktop / Codex CLI
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

## 完整工作流程

### 1. Codex 启动 MCP server

Codex 读取：

```text
C:\Users\EDY\.codex\config.toml
```

其中的 MCP 注册项会启动：

```toml
[mcp_servers.codex-agy-bridge]
command = "C:\\Users\\EDY\\AppData\\Local\\Programs\\Python\\Python312\\python.exe"
args = ["-m", "codex_agy_bridge"]
startup_timeout_sec = 120
```

bridge 是一个本地 stdio MCP server，不监听 HTTP 端口，也不需要单独启动 Web 服务。

### 2. Codex 发现工具

bridge 使用 FastMCP 注册两个工具：

```text
agy_ask
agy_ask_json
```

其中：

- `agy_ask`：返回普通文本。
- `agy_ask_json`：给 `agy` 增加 `--output-format json`，返回结构化输出文本。

### 3. Codex 发起工具调用

典型调用参数如下：

```text
agy_ask(
  prompt="Inspect README.md and reply exactly FINAL_OK",
  workdir="",
  timeout=120,
  dangerously_skip_permissions=true
)
```

这里有两个不同层级的权限开关，不要混淆：

| 参数 | 属于谁 | 作用 |
| --- | --- | --- |
| `--dangerously-bypass-approvals-and-sandbox` | 父 Codex CLI | 让非交互式 `codex exec` 自动批准工具调用，并跳过 Codex 沙箱确认 |
| `dangerously_skip_permissions=true` | Antigravity `agy` | 让 headless 的 `agy` 不等待交互式工具授权 |

只有在可信 prompt 和可信工作目录下才使用这两个选项。

### 4. bridge 进入 `server.py`

`server.py` 中的 `agy_ask` 接收到请求后，会把参数转给：

```text
run_agy(prompt, workdir, timeout, output_format, dangerously_skip_permissions)
```

### 5. `agy_runner.py` 定位 CLI

`run_agy()` 按以下顺序寻找 `agy`：

1. 环境变量 `AGY_PATH`。
2. 当前 `PATH` 中的 `agy` 或 `agy.exe`。
3. Windows、macOS、Linux 的常见安装目录。

找不到时会返回 `agy binary not found`。

### 6. bridge 构造命令

普通文本调用大致会构造为：

```text
agy -p "你的 prompt" --dangerously-skip-permissions
```

JSON 调用则会增加：

```text
--output-format json
```

### 7. Windows 中文路径兼容

如果 `workdir` 包含中文，Windows 的伪终端库可能报：

```text
[WinError 267] 目录名称无效
```

bridge 会尝试通过 `GetShortPathNameW` 把中文工作目录转换成 ASCII 8.3 路径，再传给 subprocess 和 ConPTY。这样可以继续在真实中文项目目录中工作。

### 8. 先普通 subprocess，再回退伪终端

`run_agy()` 的执行顺序是：

1. 先用普通 subprocess 启动 `agy`。
2. 如果进程成功但 stdout 为空，Windows 使用 ConPTY 重试。
3. macOS/Linux 使用标准库 `pty` 重试。
4. 清理 ANSI 控制字符和 TUI 装饰。
5. 将干净文本返回给 MCP server。

### 9. 返回 Codex

bridge 把文本放进 MCP `CallToolResult`，Codex 收到工具结果后继续完成当前回答。

## 安装

### 安装 Antigravity CLI

Windows PowerShell：

```powershell
irm https://antigravity.google/cli/install.ps1 | iex
```

确认版本：

```powershell
agy --version
```

本次验证版本：

```text
agy 1.1.10
```

### 登录 Antigravity

首次使用时，在交互式终端直接运行 `agy`，按 CLI 提示完成 OAuth 登录。授权码是一次性的：

- 不要复用旧授权码。
- 不要把授权码发到聊天中。
- 如果后续测试出现新的 `Authentication required`，先停止自动测试。

bridge 不保存 OAuth 凭据，登录状态由 `agy` 自己管理。

### 安装 bridge

```powershell
cd "C:\Users\EDY\Documents\codex调用antigravity\mcp-antigravity-bridge"
python -m pip install -e ".[winpty]"
python -m pip install -e ".[dev]"
```

其中：

- `[winpty]` 提供 Windows ConPTY 回退。
- `[dev]` 安装 pytest 测试依赖。

## 代理配置

如果浏览器能访问 Google，但 `agy` 报网络连接失败，通常是因为 `agy` 没有继承 Windows 系统代理。

本机代理示例：

```powershell
$proxy = "http://127.0.0.1:7897"
[Environment]::SetEnvironmentVariable("HTTP_PROXY", $proxy, "User")
[Environment]::SetEnvironmentVariable("HTTPS_PROXY", $proxy, "User")
[Environment]::SetEnvironmentVariable("ALL_PROXY", $proxy, "User")
```

当前 bridge MCP 配置也显式传递这些变量：

```toml
[mcp_servers.codex-agy-bridge.env]
HTTP_PROXY = "http://127.0.0.1:7897"
HTTPS_PROXY = "http://127.0.0.1:7897"
ALL_PROXY = "http://127.0.0.1:7897"
```

修改用户环境变量后要完全重启 Codex，让 MCP 子进程继承新环境。

## 接入 Codex

### 推荐：命令注册

```powershell
codex mcp add codex-agy-bridge -- python -m codex_agy_bridge
```

Windows 上如果 `python` 不是稳定路径，建议使用绝对路径：

```powershell
codex mcp add codex-agy-bridge -- `
  "C:\Users\EDY\AppData\Local\Programs\Python\Python312\python.exe" `
  -m codex_agy_bridge
```

### 手动配置

```toml
[mcp_servers.codex-agy-bridge]
command = "C:\\Users\\EDY\\AppData\\Local\\Programs\\Python\\Python312\\python.exe"
args = ["-m", "codex_agy_bridge"]
startup_timeout_sec = 120

[mcp_servers.codex-agy-bridge.env]
PYTHONPATH = "C:\\Users\\EDY\\Documents\\codex调用antigravity\\mcp-antigravity-bridge\\src"
HTTP_PROXY = "http://127.0.0.1:7897"
HTTPS_PROXY = "http://127.0.0.1:7897"
ALL_PROXY = "http://127.0.0.1:7897"
```

不要设置中文 `cwd`。bridge 会自行处理工作目录，避免 Windows MCP 子进程在启动时遇到中文路径问题。

## 分层验证

遇到问题时按层排查，不要一开始就重复 OAuth。

### 第 1 层：直接调用 `agy`

```powershell
agy -p "Reply exactly DIRECT_AGY_OK" --dangerously-skip-permissions
```

预期：

```text
DIRECT_AGY_OK
```

### 第 2 层：Python runner

```powershell
@'
import json
from codex_agy_bridge.agy_runner import run_agy

result = run_agy(
    "Reply exactly RUNNER_OK",
    dangerously_skip_permissions=True,
    timeout=120,
)
print(json.dumps({
    "text": result.text,
    "exit_code": result.exit_code,
    "used_pty": result.used_pty,
}))
'@ | python -
```

预期文本包含：

```text
RUNNER_OK
```

### 第 3 层：MCP 工具发现

```powershell
& "C:\Users\EDY\AppData\Local\OpenAI\Codex\bin\68de26ad08be95cd\codex.exe" mcp list
```

预期能看到：

```text
codex-agy-bridge    enabled
```

### 第 4 层：父 Codex 真调用

非交互式 CLI 测试使用：

```powershell
& "C:\Users\EDY\AppData\Local\OpenAI\Codex\bin\68de26ad08be95cd\codex.exe" `
  exec --json --ephemeral `
  --dangerously-bypass-approvals-and-sandbox `
  -C "C:\Users\EDY\Documents\codex调用antigravity" `
  "Use the MCP server codex-agy-bridge and call agy_ask exactly once. Arguments: prompt = Reply exactly PARENT_CODEX_AGY_OK; dangerously_skip_permissions = true; timeout = 120. Do not attempt login or OAuth. Return the tool result verbatim."
```

成功时应看到：

```text
PARENT_CODEX_AGY_OK
```

注意：`--dangerously-bypass-approvals-and-sandbox` 只用于这个非交互测试命令。日常桌面使用时，应由 Codex 的正常工具授权流程控制风险。

## 工具参数

### `agy_ask`

```text
agy_ask(
  prompt: str,
  workdir: str = "",
  timeout: float = 300.0,
  dangerously_skip_permissions: bool = False,
) -> str
```

适合普通文本任务，例如让 Antigravity 检查文件、解释代码或执行一个受控的子任务。

### `agy_ask_json`

```text
agy_ask_json(
  prompt: str,
  workdir: str = "",
  timeout: float = 300.0,
  dangerously_skip_permissions: bool = False,
) -> str
```

适合要求 Antigravity 返回结构化结果的任务。prompt 中应明确字段和 JSON 格式要求。

## 常见问题

### `agy binary not found`

```powershell
agy --version
```

如果命令不可用，设置：

```powershell
$env:AGY_PATH = "C:\path\to\agy.exe"
```

### `Authentication required`

这是 Antigravity 登录层的问题，不是 MCP bridge 登录。停止自动测试，在交互式终端完成一次新的登录，然后重新启动 Codex。

### `user cancelled MCP tool call`

这通常表示父 Codex CLI 在非交互模式等待 MCP 工具授权。使用第 4 层测试命令中的：

```text
--dangerously-bypass-approvals-and-sandbox
```

桌面版则应使用其正常的工具授权界面。

### `503`、`502` 或 `invalid peer certificate`

如果错误来自父 Codex 的模型服务，例如：

```text
https://x.ailzd.com/v1/responses
```

说明父 Codex 还没有稳定拿到模型响应，调用可能尚未进入 MCP。先观察事件中是否出现：

```text
type: mcp_tool_call
server: codex-agy-bridge
```

没有这个事件，就不要把问题归因给 `agy`。

### Windows `Access is denied`

某些 WindowsApps 目录下的 `codex.exe` 不能直接从 PowerShell 调用。优先使用用户目录下实际可执行的 CLI，例如：

```text
C:\Users\EDY\AppData\Local\OpenAI\Codex\bin\68de26ad08be95cd\codex.exe
```

### `[WinError 267] 目录名称无效`

通常与中文 `cwd` 和 Windows 伪终端有关。不要把中文 `cwd` 直接写进 MCP server 注册项；当前 bridge 会把运行时工作目录转换为短路径。

### 返回空文本

确认已安装：

```powershell
python -m pip install -e ".[winpty]"
```

然后重新运行相同 prompt。bridge 会自动从普通 subprocess 切换到 ConPTY 或 pty。

## 测试

本地单元测试不要求机器已经安装或登录 `agy`：

```powershell
python -m pytest -q
python -m compileall -q src
```

本次代码测试结果：

```text
6 passed
```

## 项目结构

```text
mcp-antigravity-bridge/
├── src/codex_agy_bridge/
│   ├── agy_runner.py    # 查找 agy、启动 subprocess、PTY 回退、ANSI 清理
│   ├── server.py        # FastMCP 工具注册
│   └── __main__.py      # python -m codex_agy_bridge 入口
├── tests/
│   └── test_smoke.py    # runner 和 Windows 路径兼容性测试
├── pyproject.toml
└── README.md
```

## 安全边界

- bridge 默认只通过本地 stdio 与 MCP 客户端通信。
- `dangerously_skip_permissions` 默认是 `false`。
- 只有在 prompt、工作目录和文件操作范围都可信时，才启用 headless 权限跳过。
- 不要把 `agy` OAuth 授权码、代理凭据或 Codex 配置中的敏感信息提交到 Git。
- 代理变量只影响 MCP 子进程和 `agy` 的网络访问，不会改变 MCP 调用协议。

## 参考

- [Antigravity CLI](https://github.com/google-antigravity/antigravity-cli)
- [Antigravity CLI 文档](https://antigravity.google/docs/cli/overview)
- [agy-headless-bridge](https://github.com/rhishi99/agy-headless-bridge)
- [agy-bridge](https://github.com/sshahzaiib/agy-bridge)

## License

Apache-2.0
