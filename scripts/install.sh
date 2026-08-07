#!/usr/bin/env sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
bridge_root="$repo_root/mcp-antigravity-bridge"
skill_source="$repo_root/skills/agy-supervisor"
codex_home="${CODEX_HOME:-${HOME}/.codex}"
skill_destination="$codex_home/skills/agy-supervisor"

command -v python3 >/dev/null 2>&1 || {
    printf '%s\n' "Required command 'python3' was not found. Install Python 3.10 or newer and rerun this script." >&2
    exit 1
}
command -v codex >/dev/null 2>&1 || {
    printf '%s\n' "Required command 'codex' was not found. Install Codex CLI and rerun this script." >&2
    exit 1
}

python_executable=$(python3 -c 'import sys; print(sys.executable)') || {
    printf '%s\n' "Could not resolve a working Python interpreter." >&2
    exit 1
}
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' || {
    printf '%s\n' "Python 3.10 or newer is required." >&2
    exit 1
}

proxy_url="${PROXY_URL:-${HTTPS_PROXY:-${HTTP_PROXY:-${ALL_PROXY:-}}}}"
if [ -n "$proxy_url" ]; then
    printf '%s\n' "Using proxy from the environment: $proxy_url"
else
    printf '%s\n' "No proxy was detected. Set PROXY_URL=http://127.0.0.1:PORT if agy needs a local proxy." >&2
fi

[ -d "$skill_source" ] || {
    printf '%s\n' "Skill source was not found at $skill_source. Run this script from a complete repository checkout." >&2
    exit 1
}

printf '%s\n' "Installing the local MCP bridge..."
"$python_executable" -m pip install -e "$bridge_root"

printf '%s\n' "Installing the agy-supervisor skill at $skill_destination..."
mkdir -p "$(dirname -- "$skill_destination")"
rm -rf -- "$skill_destination"
cp -R -- "$skill_source" "$skill_destination"

mcp_list=$(codex mcp list 2>&1) || {
    printf '%s\n' "Could not inspect Codex MCP configuration. Run 'codex mcp list' manually." >&2
    exit 1
}

case "$mcp_list" in
    *codex-agy-bridge*)
        printf '%s\n' "Codex MCP server 'codex-agy-bridge' is already registered."
        ;;
    *)
        printf '%s\n' "Registering codex-agy-bridge with Codex..."
        codex mcp add codex-agy-bridge -- "$python_executable" -m codex_agy_bridge
        ;;
esac

if [ -n "$proxy_url" ]; then
    config_path="$codex_home/config.toml"
    "$python_executable" - "$config_path" "$proxy_url" "$python_executable" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

config_path = Path(sys.argv[1])
proxy_url = sys.argv[2]
python_executable = sys.argv[3]
lines = config_path.read_text(encoding="utf-8").splitlines()
server_header = "[mcp_servers.codex-agy-bridge]"
env_header = "[mcp_servers.codex-agy-bridge.env]"
if server_header not in lines:
    raise SystemExit(f"MCP server section not found in {config_path}")

server_start = lines.index(server_header)
server_end = next((i for i in range(server_start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
for i in range(server_start + 1, server_end):
    if lines[i].lstrip().startswith("command"):
        lines[i] = f"command = {json.dumps(python_executable)}"
        break

managed = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")
if env_header in lines:
    env_start = lines.index(env_header)
    env_end = next((i for i in range(env_start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
    lines = [line for i, line in enumerate(lines) if not (env_start < i < env_end and line.split("=", 1)[0].strip() in managed)]
    env_start = lines.index(env_header)
    insert_at = env_start + 1
else:
    insert_at = server_end
    lines[insert_at:insert_at] = ["", env_header]
    insert_at += 2

entries = [
    f"HTTP_PROXY = {json.dumps(proxy_url)}",
    f"HTTPS_PROXY = {json.dumps(proxy_url)}",
    f"ALL_PROXY = {json.dumps(proxy_url)}",
    'NO_PROXY = "localhost,127.0.0.1"',
]
lines[insert_at:insert_at] = entries
config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
    printf '%s\n' "Configured codex-agy-bridge to pass the proxy to agy."
fi

if command -v agy >/dev/null 2>&1; then
    agy --version
else
    printf '%s\n' "Warning: agy was not found. Install it from https://antigravity.google/docs/cli/overview" >&2
fi

printf '%s\n' "Installation complete. Run 'agy' interactively once to complete login."
printf '%s\n' 'Verify the setup with: agy -p "Reply exactly AGY_OK"'
