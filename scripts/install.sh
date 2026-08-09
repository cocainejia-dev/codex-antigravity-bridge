#!/usr/bin/env sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
bridge_root="$repo_root/mcp-antigravity-bridge"
python_executable=$(command -v python3 || true)
if [ -z "$python_executable" ]; then
    printf '%s\n' "Required command 'python3' was not found. Install Python 3.10 or newer and rerun this script." >&2
    exit 1
fi

if [ "${WHAT_IF:-0}" != "1" ]; then
    "$python_executable" -m pip install -e "$bridge_root"
fi

setup_args=""
if [ "${WHAT_IF:-0}" = "1" ]; then
    setup_args="--what-if"
    PYTHONPATH="$bridge_root/src${PYTHONPATH:+:$PYTHONPATH}" \
        "$python_executable" -m codex_agy_bridge.setup $setup_args
else
    "$python_executable" -m codex_agy_bridge.setup
fi
