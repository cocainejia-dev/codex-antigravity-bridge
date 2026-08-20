$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$identityPath = Join-Path $repo '.recovery\repository-identity.json'
if (-not (Test-Path -LiteralPath $identityPath)) { throw "CANONICAL_IDENTITY_MISSING=$identityPath" }
$identity = Get-Content -LiteralPath $identityPath -Encoding UTF8 -Raw | ConvertFrom-Json
if ($identity.canonical_repository -ne $true -or $identity.repository_role -ne 'authoritative') {
    throw 'CANONICAL_IDENTITY_INVALID'
}
Write-Output "REPO_ROOT=$repo"
Write-Output "CANONICAL_REPO=$repo"
Write-Output "CANONICAL_REPOSITORY_IDENTITY=$($identity.project_id)"
Write-Output "THIS_IS_CANONICAL_REPO=YES"
Write-Output "HEAD=$(git -C $repo rev-parse HEAD)"
Write-Output "BRANCH=$(git -C $repo branch --show-current)"
Write-Output "WORKTREE_STATUS=$(git -C $repo status --short)"
Write-Output "PYTHON=$((Get-Command python).Source)"
Write-Output "CANONICAL_PYTHON=$(Join-Path $repo '.venv\Scripts\python.exe')"
Write-Output "PACKAGE_SOURCE_PATH=$(Join-Path $repo 'mcp-antigravity-bridge\src')"
Write-Output "MCP_SOURCE_PATH=$(Join-Path $repo 'mcp-antigravity-bridge\src\codex_agy_bridge')"
$agy = Get-Command agy -ErrorAction SilentlyContinue
if ($agy) { Write-Output "AGY_CLI=$($agy.Source)" } else { Write-Output 'AGY_CLI=NOT_FOUND' }
Write-Output "ACTIVE_RUNTIME_ROOTS=$env:LOCALAPPDATA\codex-agy-vnext"
Write-Output "DURABLE_JOB_DB=$env:LOCALAPPDATA\codex-agy-bridge\jobs.sqlite3"
Write-Output "ACTIVE_MCP_IDENTITY=$($identity.active_mcp_identity)"
Write-Output "RECOVERY_ANCHOR=$(Join-Path $repo '.recovery\current-round.json')"
Write-Output 'KNOWN_ACTIVE_JOB_IF_DISCOVERABLE=USE_BRIDGE_AGY_JOBS_RECENT_READ_ONLY'
