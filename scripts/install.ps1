[CmdletBinding()]
param(
    [switch]$WhatIf,
    [string]$ProxyUrl,
    [switch]$NoProxy
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

function Resolve-PythonExecutable {
    $candidates = @(Get-Command python -All -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandType -eq "Application" })
    foreach ($candidate in $candidates) {
        try {
            $executable = (& $candidate.Source -c "import sys; print(sys.executable)" 2>$null).Trim()
            if ($LASTEXITCODE -eq 0 -and $executable -and (Test-Path -LiteralPath $executable -PathType Leaf)) {
                return $executable
            }
        } catch {
            # Try the next python command, including when WindowsApps is only a shim.
        }
    }
    throw "A working Python 3.10+ interpreter was not found. Install Python from python.org and disable the Windows Store python aliases if needed."
}

function ConvertTo-ProxyUrl {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $null
    }

    try {
        $uri = [Uri]$Value.Trim()
        if (-not $uri.IsAbsoluteUri -or $uri.Port -lt 1) {
            return $null
        }
        if ($uri.Scheme -notin @("http", "https", "socks5", "socks5h")) {
            return $null
        }
        return $uri.AbsoluteUri.TrimEnd("/")
    } catch {
        return $null
    }
}

function Find-ProxyFromEnvironment {
    foreach ($name in @("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy")) {
        $value = [Environment]::GetEnvironmentVariable($name)
        $proxy = ConvertTo-ProxyUrl $value
        if ($proxy) {
            return $proxy
        }
    }
    return $null
}

function Find-ProxyFromWindowsSettings {
    $settingsPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    $settings = Get-ItemProperty -Path $settingsPath -ErrorAction SilentlyContinue
    if (-not $settings -or $settings.ProxyEnable -ne 1 -or [string]::IsNullOrWhiteSpace($settings.ProxyServer)) {
        return $null
    }

    $server = [string]$settings.ProxyServer
    $match = [regex]::Match($server, "(?i)(?:https|http|all)=(?<proxy>[^;]+)")
    $value = if ($match.Success) { $match.Groups["proxy"].Value } else { $server }
    if ($value -notmatch "://") {
        $value = "http://$value"
    }
    return ConvertTo-ProxyUrl $value
}

function Test-LocalProxyPort {
    param([int]$Port)

    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $connect = $client.ConnectAsync("127.0.0.1", $Port)
        if (-not $connect.Wait(500) -or -not $client.Connected) {
            return $null
        }

        $stream = $client.GetStream()
        $stream.ReadTimeout = 1000
        $request = [Text.Encoding]::ASCII.GetBytes("CONNECT oauth2.googleapis.com:443 HTTP/1.1`r`nHost: oauth2.googleapis.com:443`r`n`r`n")
        $stream.Write($request, 0, $request.Length)
        $buffer = New-Object byte[] 128
        $read = $stream.Read($buffer, 0, $buffer.Length)
        if ($read -gt 0) {
            $response = [Text.Encoding]::ASCII.GetString($buffer, 0, $read)
            if ($response -match "(?m)^HTTP/\d(?:\.\d)?\s+200\b") {
                return "http://127.0.0.1:$Port"
            }
        }
    } catch {
        # Try the SOCKS5 handshake below on the same candidate port.
    } finally {
        $client.Dispose()
    }

    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $connect = $client.ConnectAsync("127.0.0.1", $Port)
        if (-not $connect.Wait(500) -or -not $client.Connected) {
            return $null
        }
        $stream = $client.GetStream()
        $stream.ReadTimeout = 1000
        $hello = [byte[]](0x05, 0x01, 0x00)
        $stream.Write($hello, 0, $hello.Length)
        $reply = New-Object byte[] 2
        $read = $stream.Read($reply, 0, 2)
        if ($read -eq 2 -and $reply[0] -eq 0x05 -and $reply[1] -eq 0x00) {
            return "socks5://127.0.0.1:$Port"
        }
    } catch {
        return $null
    } finally {
        $client.Dispose()
    }
    return $null
}

function Find-ProxyFromLocalPorts {
    $candidatePorts = [System.Collections.Generic.HashSet[int]]::new()
    foreach ($port in @(7890, 7891, 7892, 7897, 1080, 10808, 10809, 8080, 8888, 3128)) {
        $candidatePorts.Add($port) | Out-Null
    }

    $proxyProcessIds = @(Get-Process -ErrorAction SilentlyContinue |
        Where-Object { $_.ProcessName -match "(?i)clash|mihomo|sing.?box|shadowsocks|xray|v2ray|nekoray|shan|proxy" } |
        Select-Object -ExpandProperty Id)
    if ($proxyProcessIds.Count -gt 0) {
        Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
            Where-Object { $_.LocalAddress -in @("127.0.0.1", "::1") -and $proxyProcessIds -contains $_.OwningProcess } |
            ForEach-Object { $candidatePorts.Add([int]$_.LocalPort) | Out-Null }
    }

    foreach ($port in $candidatePorts) {
        $proxy = Test-LocalProxyPort $port
        if ($proxy) {
            return $proxy
        }
    }
    return $null
}

function Resolve-ProxyUrl {
    param([string]$RequestedProxyUrl)

    $explicit = ConvertTo-ProxyUrl $RequestedProxyUrl
    if ($RequestedProxyUrl -and -not $explicit) {
        throw "Invalid -ProxyUrl '$RequestedProxyUrl'. Use http://host:port or socks5://host:port."
    }
    if ($explicit) {
        return $explicit
    }

    $fromEnvironment = Find-ProxyFromEnvironment
    if ($fromEnvironment) {
        return $fromEnvironment
    }

    $fromWindows = Find-ProxyFromWindowsSettings
    if ($fromWindows) {
        return $fromWindows
    }

    return Find-ProxyFromLocalPorts
}

function Set-CodexMcpProxy {
    param(
        [string]$ConfigPath,
        [string]$Value,
        [string]$PythonExecutable
    )

    if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
        throw "Codex config was not found at '$ConfigPath' after MCP registration."
    }

    $lines = [System.Collections.Generic.List[string]]::new()
    foreach ($line in (Get-Content -LiteralPath $ConfigPath)) {
        $lines.Add([string]$line)
    }

    $envHeader = "[mcp_servers.codex-agy-bridge.env]"
    $managedNames = "HTTP_PROXY|HTTPS_PROXY|ALL_PROXY|NO_PROXY"
    $serverHeader = "[mcp_servers.codex-agy-bridge]"
    $serverIndex = $lines.IndexOf($serverHeader)
    if ($serverIndex -lt 0) {
        throw "MCP server 'codex-agy-bridge' was not found in '$ConfigPath'."
    }
    $serverEnd = $serverIndex + 1
    while ($serverEnd -lt $lines.Count -and $lines[$serverEnd] -notmatch '^\s*\[') {
        $serverEnd++
    }
    for ($index = $serverIndex + 1; $index -lt $serverEnd; $index++) {
        if ($lines[$index] -match '^\s*command\s*=') {
            $tomlPython = $PythonExecutable.Replace("\", "\\").Replace('"', '\"')
            $lines[$index] = "command = `"$tomlPython`""
            break
        }
    }

    if (-not $Value) {
        $encoding = New-Object System.Text.UTF8Encoding($false)
        [IO.File]::WriteAllLines($ConfigPath, $lines, $encoding)
        return
    }

    $envIndex = $lines.IndexOf($envHeader)
    if ($envIndex -ge 0) {
        $nextIndex = $envIndex + 1
        while ($nextIndex -lt $lines.Count -and $lines[$nextIndex] -notmatch '^\s*\[') {
            $nextIndex++
        }
        for ($index = $nextIndex - 1; $index -gt $envIndex; $index--) {
            if ($lines[$index] -match "^\s*(?:$managedNames)\s*=") {
                $lines.RemoveAt($index)
            }
        }
        $insertAt = $envIndex + 1
    } else {
        $insertAt = $serverIndex + 1
        while ($insertAt -lt $lines.Count -and $lines[$insertAt] -notmatch '^\s*\[') {
            $insertAt++
        }
        $lines.InsertRange($insertAt, [string[]]@("", $envHeader))
        $insertAt += 2
    }

    $entries = [string[]]@(
        "HTTP_PROXY = `"$Value`"",
        "HTTPS_PROXY = `"$Value`"",
        "ALL_PROXY = `"$Value`"",
        "NO_PROXY = `"localhost,127.0.0.1`""
    )
    $lines.InsertRange($insertAt, $entries)
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllLines($ConfigPath, $lines, $encoding)
}

$pythonExecutable = Resolve-PythonExecutable
$codex = Require-Command "codex"

$pythonVersionText = & $pythonExecutable -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
$pythonVersion = [Version]$pythonVersionText.Trim()
if ($pythonVersion -lt [Version]"3.10") {
    throw "Python 3.10 or newer is required; found $pythonVersionText."
}

if (-not (Test-Path $skillSource -PathType Container)) {
    throw "Skill source was not found at '$skillSource'. Run this script from a complete repository checkout."
}

$installSpec = "${bridgeRoot}[winpty]"
$proxyUrl = if ($NoProxy) { $null } else { Resolve-ProxyUrl $ProxyUrl }
if ($proxyUrl) {
    Write-Host "Detected proxy: $proxyUrl"
} else {
    Write-Warning "No usable proxy was detected. AGY may require direct network access or an explicit -ProxyUrl."
}

Invoke-InstallStep "Install the local MCP bridge from $installSpec" {
    & $pythonExecutable -m pip install -e $installSpec
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
    if ($proxyUrl) {
        Write-Host "[WhatIf] Write the detected proxy to the codex-agy-bridge MCP environment"
    }
} else {
    $mcpList = & $codex.Source mcp list 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect Codex MCP configuration. Run 'codex mcp list' manually and fix the CLI installation."
    }

    if ($mcpList -match "codex-agy-bridge") {
        Write-Host "Codex MCP server 'codex-agy-bridge' is already registered."
    } else {
        Invoke-InstallStep "Register codex-agy-bridge with Codex" {
            & $codex.Source mcp add codex-agy-bridge -- $pythonExecutable -m codex_agy_bridge
            if ($LASTEXITCODE -ne 0) {
                throw "Codex MCP registration failed with exit code $LASTEXITCODE."
            }
        }
    }

    Set-CodexMcpProxy (Join-Path $codexHome "config.toml") $proxyUrl $pythonExecutable
    if ($proxyUrl) {
        Write-Host "Configured codex-agy-bridge to pass the proxy to agy."
    } else {
        Write-Host "Configured codex-agy-bridge to use the resolved Python executable."
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
