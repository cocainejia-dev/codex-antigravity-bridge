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

python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' || {
    printf '%s\n' "Python 3.10 or newer is required." >&2
    exit 1
}

[ -d "$skill_source" ] || {
    printf '%s\n' "Skill source was not found at $skill_source. Run this script from a complete repository checkout." >&2
    exit 1
}

printf '%s\n' "Installing the local MCP bridge..."
python3 -m pip install -e "$bridge_root"

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
        codex mcp add codex-agy-bridge -- python3 -m codex_agy_bridge
        ;;
esac

if command -v agy >/dev/null 2>&1; then
    agy --version
else
    printf '%s\n' "Warning: agy was not found. Install it from https://antigravity.google/docs/cli/overview" >&2
fi

printf '%s\n' "Installation complete. Run 'agy' interactively once to complete login."
printf '%s\n' 'Verify the setup with: agy -p "Reply exactly AGY_OK"'
