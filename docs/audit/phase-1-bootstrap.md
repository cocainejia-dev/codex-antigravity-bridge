# Phase 1 Isolation Bootstrap

Date: 2026-08-18

## Immutable production baseline

- Production repository: `D:\软件开发\codex-antigravity-bridge`
- Production HEAD: `79e17cb77a4924afcdf14b926fab6365747d5570`
- Production branch: `main`
- Production status at bootstrap: clean
- Production MCP: `codex-agy-bridge`, version `0.1.0`
- Production durable state: `C:\Users\28760\AppData\Local\codex-agy-bridge\jobs.sqlite3`
- Global Codex config: `C:\Users\28760\.codex\config.toml`
- Antigravity global data: `C:\Users\28760\AppData\Roaming\Antigravity`
- Legacy HTTP bridge: `127.0.0.1:8766`

## VNext isolation

- Independent clone: `D:\软件开发\codex-antigravity-vnext`
- Clone mode: `git clone --no-hardlinks`
- Runtime root: `C:\Users\28760\AppData\Local\codex-agy-vnext`
- State: `C:\Users\28760\AppData\Local\codex-agy-vnext\state`
- Logs: `C:\Users\28760\AppData\Local\codex-agy-vnext\logs`
- Runs: `C:\Users\28760\AppData\Local\codex-agy-vnext\runs`
- Worktrees: `C:\Users\28760\AppData\Local\codex-agy-vnext\worktrees`
- Private virtual environment: `D:\软件开发\codex-antigravity-vnext\.venv`
- VNext MCP name: `codex-agy-vnext` (not registered)
- VNext port: none required for Phase 1
- Maximum Antigravity parallelism: `1`

## Bootstrap evidence

- VNext HEAD equals production baseline: `79e17cb77a4924afcdf14b926fab6365747d5570`
- VNext package install: `codex-agy-bridge 0.1.0` editable install succeeded
- Installed test/runtime dependencies: `mcp 1.29.0`, `pytest 9.1.1`, `pywinpty 3.0.5`
- Baseline command: `.venv\Scripts\python.exe -m pytest -q`
- Baseline result: `144 passed, 1 warning`

## Safety gate

- `PRODUCTION_UNCHANGED`: PASS
- `VNEXT_GIT_ISOLATED`: PASS
- `BASELINE_TESTS_PASS`: PASS
- `GLOBAL_CONFIG_UNCHANGED`: PASS
- `LEGACY_BRIDGE_UNTOUCHED`: PASS; read-only health probe still returns `401`
- `ASHAREADVISOR_UNTOUCHED`: PASS; no commands or writes were issued in that repository

No production MCP registration, process restart, authentication action, or
AshareAdvisor operation was performed.
