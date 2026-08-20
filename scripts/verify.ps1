$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$bridgeSrc = Join-Path $repo 'mcp-antigravity-bridge\src'
$python = Join-Path $repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw "Canonical virtualenv interpreter not found: $python"
}
$env:PYTHONPATH = $bridgeSrc
Write-Host "SOURCE_PROVENANCE_GATE=REQUIRED_AND_ENFORCED"
& $python -m pytest -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python -m compileall -q (Join-Path $repo 'mcp-antigravity-bridge\src')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
git -C $repo diff --check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host 'RELEASE_VERIFICATION=PASS'
