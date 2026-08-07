[CmdletBinding()]
param(
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$bridgeRoot = Join-Path $repoRoot "mcp-antigravity-bridge"
$skillSource = Join-Path $repoRoot "skills\agy-supervisor"
$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE ".codex" }
$skillDestination = Join-Path $codexHome "skills\agy-supervisor"

function Invoke-InstallStep {
    param(
        [string]$Description,
        [scriptblock]$Action
    )

    if ($WhatIf) {
        Write-Host "[WhatIf] $Description"
        return
    }

    Write-Host $Description
    & $Action
}

function Require-Command {
    param([string]$Name)

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "Required command '$Name' was not found. Install it and rerun this script."
    }
    return $command
}

$python = Require-Command "python"
$codex = Require-Command "codex"

$pythonVersionText = & $python.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
$pythonVersion = [Version]$pythonVersionText.Trim()
if ($pythonVersion -lt [Version]"3.10") {
    throw "Python 3.10 or newer is required; found $pythonVersionText."
}

if (-not (Test-Path $skillSource -PathType Container)) {
    throw "Skill source was not found at '$skillSource'. Run this script from a complete repository checkout."
}

$installSpec = "${bridgeRoot}[winpty]"
Invoke-InstallStep "Install the local MCP bridge from $installSpec" {
    & $python.Source -m pip install -e $installSpec
    if ($LASTEXITCODE -ne 0) {
        throw "Python dependency installation failed with exit code $LASTEXITCODE."
    }
}

Invoke-InstallStep "Install the agy-supervisor skill at $skillDestination" {
    $skillParent = Split-Path $skillDestination -Parent
    New-Item -ItemType Directory -Force -Path $skillParent | Out-Null
    if (Test-Path $skillDestination) {
        Remove-Item -LiteralPath $skillDestination -Recurse -Force
    }
    Copy-Item -LiteralPath $skillSource -Destination $skillDestination -Recurse
}

if ($WhatIf) {
    Write-Host "[WhatIf] Inspect Codex MCP configuration for codex-agy-bridge"
    Write-Host "[WhatIf] Register codex-agy-bridge with Codex if it is absent"
} else {
    $mcpList = & $codex.Source mcp list 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect Codex MCP configuration. Run 'codex mcp list' manually and fix the CLI installation."
    }

    if ($mcpList -match "codex-agy-bridge") {
        Write-Host "Codex MCP server 'codex-agy-bridge' is already registered."
    } else {
        Invoke-InstallStep "Register codex-agy-bridge with Codex" {
            & $codex.Source mcp add codex-agy-bridge -- python -m codex_agy_bridge
            if ($LASTEXITCODE -ne 0) {
                throw "Codex MCP registration failed with exit code $LASTEXITCODE."
            }
        }
    }
}

if ($WhatIf) {
    Write-Host "[WhatIf] Check whether agy is installed"
} elseif ($null -eq (Get-Command agy -ErrorAction SilentlyContinue)) {
    Write-Warning "The agy command was not found. Install Antigravity CLI with: irm https://antigravity.google/cli/install.ps1 | iex"
} else {
    & (Get-Command agy).Source --version
}

Write-Host "Installation complete. Run 'agy' interactively once to complete login."
Write-Host 'Verify the setup with: agy -p "Reply exactly AGY_OK"'
