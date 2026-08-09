[CmdletBinding()]
param(
    [switch]$WhatIf,
    [string]$ProxyUrl,
    [switch]$NoProxy
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$bridgeRoot = Join-Path $repoRoot "mcp-antigravity-bridge"
$pythonExecutable = $null
foreach ($candidate in @(Get-Command python -All -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandType -eq "Application" })) {
    try {
        $resolved = (& $candidate.Source -c "import sys; print(sys.executable)" 2>$null).Trim()
        if ($LASTEXITCODE -eq 0 -and $resolved -and (Test-Path -LiteralPath $resolved -PathType Leaf)) {
            $pythonExecutable = $resolved
            break
        }
    } catch {
        # Ignore Windows Store shims and try the next interpreter.
    }
}
if ($null -eq $pythonExecutable) {
    throw "Required command 'python' was not found. Install Python 3.10 or newer and rerun this script."
}

$setupArgs = @("-m", "codex_agy_bridge.setup")
if ($WhatIf) { $setupArgs += "--what-if" }
if ($ProxyUrl) { $setupArgs += @("--proxy-url", $ProxyUrl) }
if ($NoProxy) { $setupArgs += "--no-proxy" }

if (-not $WhatIf) {
    & $pythonExecutable -m pip install -e "${bridgeRoot}[winpty]"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} else {
    $previousPythonPath = $env:PYTHONPATH
    $env:PYTHONPATH = Join-Path $bridgeRoot "src"
}

& $pythonExecutable @setupArgs
$exitCode = $LASTEXITCODE
if ($WhatIf) { $env:PYTHONPATH = $previousPythonPath }
exit $exitCode
