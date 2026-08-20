[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$Python
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$bridgeRoot = Join-Path $repo 'mcp-antigravity-bridge'
$bridgeSrc = Join-Path $bridgeRoot 'src'
$bridgeTests = Join-Path $bridgeRoot 'tests'
$pyproject = Join-Path $bridgeRoot 'pyproject.toml'

if ([string]::IsNullOrWhiteSpace($Python)) {
    $python = Join-Path $repo '.venv\Scripts\python.exe'
} else {
    $resolvedPython = (Resolve-Path -LiteralPath $Python -ErrorAction SilentlyContinue).Path
    if ($resolvedPython) {
        $python = $resolvedPython
    } else {
        $python = $Python
    }
}

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Python interpreter not found: $python"
}

Remove-Item env:PYTHONPATH -ErrorAction SilentlyContinue
$env:PYTHONPATH = $null

Write-Host "SOURCE_PROVENANCE_GATE=REQUIRED_AND_ENFORCED"

& $python -m pytest -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "PYTEST=PASS"

& $python -m ruff check --config $pyproject $bridgeSrc $bridgeTests
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "RUFF=PASS"

& $python -m compileall -q $bridgeSrc
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "COMPILEALL=PASS"

$provenanceProbe = @'
import importlib, sys
from pathlib import Path

expected_root = Path(sys.argv[1]).resolve()
modules = [
    "codex_agy_bridge",
    "codex_agy_bridge.server",
    "codex_agy_bridge.agy_jobs",
    "codex_agy_bridge.agy_runner",
]
for mod_name in modules:
    mod = importlib.import_module(mod_name)
    mod_file = getattr(mod, "__file__", None)
    if not mod_file:
        raise RuntimeError(f"Module {mod_name} has no __file__")
    resolved = Path(mod_file).resolve()
    try:
        resolved.relative_to(expected_root)
    except ValueError:
        raise RuntimeError(f"Module {mod_name} resolved to {resolved}, outside expected root {expected_root}")
'@

$provenanceProbe | & $python -B - $bridgeSrc
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "SOURCE_PROVENANCE=PASS"

git -C $repo diff --check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "DIFF_CHECK=PASS"

Write-Host 'RELEASE_VERIFICATION=PASS'
