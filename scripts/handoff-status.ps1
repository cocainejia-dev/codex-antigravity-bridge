$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Write-Output "REPO_ROOT=$repo"
Write-Output "HEAD=$(git -C $repo rev-parse HEAD)"
Write-Output "BRANCH=$(git -C $repo branch --show-current)"
Write-Output "WORKTREE_STATUS=$(git -C $repo status --short)"
Write-Output "PYTHON=$((Get-Command python).Source)"
Write-Output "PACKAGE_SOURCE_PATH=$(Join-Path $repo 'mcp-antigravity-bridge\src')"
Write-Output "MCP_SOURCE_PATH=$(Join-Path $repo 'mcp-antigravity-bridge\src\codex_agy_bridge')"
$agy = Get-Command agy -ErrorAction SilentlyContinue
if ($agy) { Write-Output "AGY_CLI=$($agy.Source)" } else { Write-Output 'AGY_CLI=NOT_FOUND' }
Write-Output "ACTIVE_RUNTIME_ROOTS=$env:LOCALAPPDATA\codex-agy-vnext"
Write-Output "RECOVERY_ANCHOR=$(Join-Path $repo '.recovery\current-round.json')"
Write-Output 'KNOWN_ACTIVE_JOB_IF_DISCOVERABLE=USE_BRIDGE_AGY_JOBS_RECENT_READ_ONLY'
