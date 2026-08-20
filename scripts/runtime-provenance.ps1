$ErrorActionPreference = 'Stop'

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$identityPath = Join-Path $repo '.recovery\repository-identity.json'
$expectedPython = Join-Path $repo '.venv\Scripts\python.exe'
$sourceRoot = Join-Path $repo 'mcp-antigravity-bridge\src'
$identity = $null
$identityValid = $false

if (Test-Path -LiteralPath $identityPath) {
    try {
        $identity = Get-Content -LiteralPath $identityPath -Encoding UTF8 -Raw | ConvertFrom-Json
        $identityValid = (
            $identity.canonical_repository -eq $true -and
            $identity.repository_role -eq 'authoritative' -and
            [string]::Equals(
                $repo,
                [string]$identity.machine_local_canonical_path,
                [System.StringComparison]::OrdinalIgnoreCase
            )
        )
    } catch {
        $identityValid = $false
    }
}

Write-Output "REPO_ROOT=$repo"
Write-Output "IDENTITY_PATH=$identityPath"
$identityResult = if ($identityValid) { 'YES' } else { 'NO' }
Write-Output "THIS_IS_CANONICAL_REPO=$identityResult"
Write-Output "EXPECTED_INTERPRETER=$expectedPython"
Write-Output "EXPECTED_SOURCE_ROOT=$sourceRoot"

if (-not $identityValid) {
    Write-Output 'PROVENANCE_STATUS=FAIL'
    Write-Output 'PROVENANCE_ERROR=CANONICAL_IDENTITY_INVALID'
    exit 1
}

if (-not (Test-Path -LiteralPath $expectedPython)) {
    Write-Output 'PROVENANCE_STATUS=FAIL'
    Write-Output "PROVENANCE_ERROR=INTERPRETER_MISSING:$expectedPython"
    exit 1
}

$probe = "import importlib,json,os,sys;from pathlib import Path;mods={'bridge':'codex_agy_bridge','server':'codex_agy_bridge.server','agy_jobs':'codex_agy_bridge.agy_jobs','agy_runner':'codex_agy_bridge.agy_runner'};ei=Path(os.environ['EXPECTED_INTERPRETER']).resolve();er=Path(os.environ['EXPECTED_SOURCE_ROOT']).resolve();paths={k:Path(importlib.import_module(n).__file__).resolve() for k,n in mods.items()};result={'resolved_interpreter':str(Path(sys.executable).resolve()).encode('unicode_escape').decode('ascii'),'interpreter_matches_expected':Path(sys.executable).resolve()==ei};result.update({k+'_path':str(p).encode('unicode_escape').decode('ascii') for k,p in paths.items()});result.update({k+'_matches':p.is_relative_to(er) for k,p in paths.items()});print(json.dumps(result))"

$oldPythonPath = $env:PYTHONPATH
$oldPythonNoUserSite = $env:PYTHONNOUSERSITE
$oldExpectedInterpreter = $env:EXPECTED_INTERPRETER
$oldExpectedSourceRoot = $env:EXPECTED_SOURCE_ROOT
$oldErrorActionPreference = $ErrorActionPreference
try {
    $env:PYTHONPATH = $sourceRoot
    $env:PYTHONNOUSERSITE = '1'
    $env:EXPECTED_INTERPRETER = $expectedPython
    $env:EXPECTED_SOURCE_ROOT = $sourceRoot
    $ErrorActionPreference = 'Continue'
    $probeOutput = (& $expectedPython -B -c $probe 2>$null | Out-String).Trim()
    $probeExitCode = $LASTEXITCODE
} finally {
    $env:PYTHONPATH = $oldPythonPath
    $env:PYTHONNOUSERSITE = $oldPythonNoUserSite
    $env:EXPECTED_INTERPRETER = $oldExpectedInterpreter
    $env:EXPECTED_SOURCE_ROOT = $oldExpectedSourceRoot
    $ErrorActionPreference = $oldErrorActionPreference
}

if ($probeExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($probeOutput)) {
    Write-Output 'PROVENANCE_STATUS=FAIL'
    Write-Output "PROVENANCE_ERROR=CHILD_PROBE_FAILED:$probeExitCode"
    if (-not [string]::IsNullOrWhiteSpace($probeOutput)) { Write-Output "PROBE_OUTPUT=$probeOutput" }
    exit 1
}

try {
    $resolved = $probeOutput | ConvertFrom-Json
} catch {
    Write-Output 'PROVENANCE_STATUS=FAIL'
    Write-Output 'PROVENANCE_ERROR=CHILD_OUTPUT_NOT_JSON'
    exit 1
}

Write-Output "RESOLVED_INTERPRETER=$($resolved.resolved_interpreter)"
$allMatch = $true
foreach ($name in @('bridge', 'server', 'agy_jobs', 'agy_runner')) {
    $upperName = $name.ToUpperInvariant()
    $modulePath = [string]$resolved."${name}_path"
    $matches = [bool]$resolved."${name}_matches"
    if (-not $matches) { $allMatch = $false }
    Write-Output "RESOLVED_$upperName=$modulePath"
    $matchResult = if ($matches) { 'YES' } else { 'NO' }
    Write-Output "${upperName}_MATCHES_CANONICAL=$matchResult"
}

$interpreterMatches = [bool]$resolved.interpreter_matches_expected
$interpreterResult = if ($interpreterMatches) { 'YES' } else { 'NO' }
Write-Output "INTERPRETER_MATCHES_EXPECTED=$interpreterResult"
$allMatch = $allMatch -and $interpreterMatches

if ($allMatch) {
    Write-Output 'PROVENANCE_STATUS=PASS'
    exit 0
}

Write-Output 'PROVENANCE_STATUS=FAIL'
exit 1
