# 双语言 README 重设计说明

**日期：** 2026-08-07

## 目标

将仓库的两份 README 重新设计为清晰、可公开发布、编码正常的双语言文档：根目录使用简体中文，`mcp-antigravity-bridge/` 使用 English。整体采用活泼但克制的开发者工具风格，使用少量 emoji 增强扫描体验，不改变运行时代码。

## 文档分工

- 根目录 `README.md`：中文项目入口，帮助 GitHub 访客快速理解用途、架构、推荐安装路径和安全边界。
- `mcp-antigravity-bridge/README.md`：English 技术文档，面向需要安装、配置、调试和验证 CLI bridge 的开发者。
- 两份文档都只描述当前支持的 CLI-only 路径：Codex Desktop 或 Codex CLI 通过本地 MCP stdio 调用 `agy`。

## 视觉与写作方向

- 顶部保留项目标题、价值主张、技术徽章和醒目的支持路径。
- 使用少量章节 emoji，例如 🚀、🧭、🛠️、🔐、🧪；不让 emoji 替代技术名称或命令。
- 采用短段落、表格、可直接复制的代码块和 Mermaid 流程图。
- 避免个人电脑路径、代理地址、OAuth 凭据、历史测试日志和未验证的环境细节。
- 保留 English 命令、包名、API 标识符和官方链接，确保文档可执行。

## 章节结构

两份 README 按各自语言覆盖以下内容：

1. 项目简介与支持边界。
2. MCP 调用流程图。
3. 为什么使用这个 bridge。
4. 前置条件与安装。
5. Codex MCP 注册。
6. `agy_ask` 和 `agy_ask_json` 工具说明。
7. Windows 路径、ConPTY/pty fallback 和 `AGY_PATH` 配置。
8. 安全边界与 `dangerously_skip_permissions` 注意事项。
9. 分层验证、测试和常见故障排查。
10. 项目结构、参考资料和 Apache-2.0 License。

根目录版本强调“先跑起来”，子项目版本强调“理解实现并排障”。两份文档的信息保持一致，但不机械逐句翻译。

## 验收标准

- 两份 README 都是有效 UTF-8，正文分别以简体中文和 English 为主。
- 不再出现乱码、个人机器路径、代理配置或历史运行结果。
- 不声称支持 Antigravity 桌面 GUI，也不引入已移除的 SDK 路径。
- `git diff --check` 通过。
- `python -m pytest -q` 和 `python -m compileall -q src` 在 bridge 目录中通过。
- 仅提交本次文档和必要的设计记录，不提交 `.codegraph/`。
- 通过 `origin/main` 推送最终提交。
