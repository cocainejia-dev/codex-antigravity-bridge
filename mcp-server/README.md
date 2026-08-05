# antigravity-mcp

MCP server that bridges Codex (or any MCP client) to **Google Antigravity** via the official Python SDK.

## Architecture

```
Codex (MCP Client)
   └─> antigravity-mcp (this server, stdin/stdout)
         └─> google-antigravity SDK (Agent.chat, in-process)
               └─> Google Antigravity / Gemini API
```

No CLI binary (`agy`) needed — the server calls the SDK directly in-process.

## Prerequisites

- Python 3.10+
- A Gemini API key: set `GEMINI_API_KEY` in the environment, or pass `api_key` per tool call.

## Setup

```bash
cd mcp-server
pip install -e ".[dev]"
```

## Run Tests

```bash
pip install -e ".[dev]"
pytest -q
```

## Register with Codex

Create or edit `%USERPROFILE%\.codex\mcp.json` (Windows) or `~/.codex/mcp.json` (macOS/Linux):

```json
{
  "servers": {
    "antigravity": {
      "command": "python",
      "args": ["-m", "antigravity_mcp.server"],
      "cwd": "C:\\path\\to\\codex调用antigravity\\mcp-server",
      "env": {
        "GEMINI_API_KEY": "your-gemini-api-key"
      }
    }
  }
}
```

Then restart Codex. The `run_agy` tool will appear in your available tools.

## Tool: `run_agy`

| Parameter | Required | Description |
|---|---|---|
| `prompt` | yes | The prompt to send to Antigravity. |
| `cwd` | no | Restrict file tools to this directory. Empty = no restriction. |
| `api_key` | no | Gemini API key override. Empty = `GEMINI_API_KEY` env. |
| `model` | no | Model name (e.g. `gemini-2.5-flash`). Empty = SDK default. |

## Manual Smoke Test

```bash
cd mcp-server
python -m antigravity_mcp.server
# In another terminal, send an MCP JSON-RPC message:
# echo '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"run_agy","arguments":{"prompt":"What is 2+2?"}}}' | python -m antigravity_mcp.server
```

## Files

- `mcp-server/antigravity_mcp/agy_runner.py` — SDK bridge (`LocalAgentConfig` + `Agent.chat`)
- `mcp-server/antigravity_mcp/server.py` — FastMCP server exposing the `run_agy` tool
- `mcp-server/tests/` — unit tests (pytest)
- `mcp-server/pyproject.toml` — project metadata and dependencies
