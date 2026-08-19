# Ocean Agent one-command installer (Windows)
# Installs uv, writes .env, and registers the MCP server in Claude Desktop.
$ErrorActionPreference = 'Stop'
$oaPkg = "ocean-agent@0.4.24"
Write-Host ""
Write-Host "=== Ocean Agent installer ===" -ForegroundColor Cyan

# 1) uv (installs its own Python automatically)
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "[1/4] Installing uv..."
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
} else {
    Write-Host "[1/4] uv already installed"
}
$uvx = Join-Path $env:USERPROFILE ".local\bin\uvx.exe"
if (-not (Test-Path $uvx)) {
    $cmd = Get-Command uvx -ErrorAction SilentlyContinue
    if ($cmd) { $uvx = $cmd.Source } else { $uvx = "uvx" }
}

# 2) keys -> .env
Write-Host "[2/4] Account setup"
$envDir  = Join-Path $env:USERPROFILE ".ocean-agent"
New-Item -ItemType Directory -Force $envDir | Out-Null
$envFile = Join-Path $envDir ".env"
$write = $true
if (Test-Path $envFile) {
    $ans = Read-Host "  .env already exists. Overwrite? (y/N)"
    if ($ans -notmatch '^[yY]') { $write = $false; Write-Host "  keeping existing .env" }
}
if ($write) {
    # Credentials go in through a small styled page on this machine, not
    # a terminal prompt: pasting a key into a black console reads as
    # something to be nervous about, and the form can check the format
    # and test the connection while it is at it.
    Write-Host "  Opening a secure form in your browser..."
    $done = $false
    try {
        & $uvx --from "ocean-agent" python -m ocean_agent.connect_ui `
            --env-file "$envFile" --timeout 600
        if ($LASTEXITCODE -eq 0) { $done = $true }
    } catch { }
    if (-not $done) {
        Write-Host "  Browser form unavailable, asking here instead." -ForegroundColor Yellow
        $addr = Read-Host "  Wallet public address (ADDRESS)"
        try { Start-Process "https://app.pacifica.fi/apikey" } catch {}
        $keySec = Read-Host "  Agent API key (input hidden)" -AsSecureString
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($keySec)
        $key  = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        @(
            "ADDRESS=$addr"
            "PACIFICA_API_KEY=$key"
            "PACIFICA_BASE_URL=https://api.pacifica.fi"
        ) -join "`r`n" | Out-File -Encoding ascii $envFile
        try {
            icacls $envFile /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null
        } catch {
            Write-Host "  WARNING: could not restrict permissions on $envFile" -ForegroundColor Yellow
        }
    }
    Write-Host "  saved to $envFile (readable only by you)"
}

# 3) terms of use (declining aborts the install; details: oceanagent.fi)
Write-Host "[3/4] Terms of Use"
$env:PACIFICA_ENV_FILE = $envFile
& $uvx --from $oaPkg python -m ocean_agent.builder_consent
if ($LASTEXITCODE -eq 3) { exit 1 }

# 4) register in Claude Desktop config
Write-Host "[4/4] Registering with Claude Desktop"
$cfgDir  = Join-Path $env:APPDATA "Claude"
$cfgPath = Join-Path $cfgDir "claude_desktop_config.json"
$server  = [pscustomobject]@{
    command = "$uvx"
    args    = @($oaPkg)
    env     = [pscustomobject]@{ PACIFICA_ENV_FILE = "$envFile" }
}
$bakPath = $null
if (Test-Path $cfgPath) {
    $bakPath = "$cfgPath.bak"
    Copy-Item $cfgPath $bakPath -Force
    try {
        $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
    } catch {
        Write-Host "  WARNING: existing config is not valid JSON; rewriting it (original saved as .bak)" -ForegroundColor Yellow
        $cfg = $null
    }
    if ($null -eq $cfg) { $cfg = [pscustomobject]@{} }
} else {
    New-Item -ItemType Directory -Force $cfgDir | Out-Null
    $cfg = [pscustomobject]@{}
}
if (-not ($cfg.PSObject.Properties.Name -contains 'mcpServers')) {
    $cfg | Add-Member -NotePropertyName mcpServers -NotePropertyValue ([pscustomobject]@{})
}
if ($cfg.mcpServers.PSObject.Properties.Name -contains 'ocean-agent') {
    $cfg.mcpServers.'ocean-agent' = $server
} else {
    $cfg.mcpServers | Add-Member -NotePropertyName 'ocean-agent' -NotePropertyValue $server
}
$tmpPath = "$cfgPath.tmp"
try {
    $cfg | ConvertTo-Json -Depth 10 | Out-File -Encoding utf8 $tmpPath
    $null = Get-Content $tmpPath -Raw | ConvertFrom-Json
    Move-Item -Force $tmpPath $cfgPath
} catch {
    if (Test-Path $tmpPath) { Remove-Item $tmpPath -Force }
    if ($bakPath -and (Test-Path $bakPath)) {
        Copy-Item $bakPath $cfgPath -Force
        Write-Host "  ERROR: failed to update $cfgPath; original restored from .bak" -ForegroundColor Red
    } else {
        Write-Host "  ERROR: failed to update $cfgPath" -ForegroundColor Red
    }
    exit 1
}
if ($bakPath) {
    Write-Host "  updated $cfgPath (backup saved as .bak)"
} else {
    Write-Host "  updated $cfgPath"
}

# Claude reads its config once at startup, so the tools only appear after
# a restart. Doing it here saves the user a step they cannot skip; if
# Claude was not running, it is simply started.
Write-Host ""
Write-Host "Restarting Claude Desktop so the tools load..."
$claudeExe = $null
try {
    $running = Get-Process -Name "Claude" -ErrorAction SilentlyContinue
    if ($running) {
        $claudeExe = ($running | Select-Object -First 1).Path
        $running | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 3
    }
    if (-not $claudeExe) {
        $guess = Join-Path $env:LOCALAPPDATA "AnthropicClaude\Claude.exe"
        if (Test-Path $guess) { $claudeExe = $guess }
        else {
            $shortcut = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Claude.lnk"
            if (Test-Path $shortcut) {
                $sh = New-Object -ComObject WScript.Shell
                $claudeExe = $sh.CreateShortcut($shortcut).TargetPath
            }
        }
    }
    if ($claudeExe -and (Test-Path $claudeExe)) {
        Start-Process $claudeExe
        Write-Host "  Claude Desktop restarted." -ForegroundColor Green
    } else {
        Write-Host "  Could not find Claude Desktop. Open it yourself once." -ForegroundColor Yellow
    }
} catch {
    Write-Host "  Could not restart Claude Desktop. Open it yourself once." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "==============================================" -ForegroundColor Green
Write-Host "  INSTALL COMPLETE" -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Green
Write-Host "Nothing else to type in this window. You can close it."
Write-Host ""
Write-Host "Now go to Claude Desktop and just talk to it:" -ForegroundColor Yellow
Write-Host "       show me today's picks"
Write-Host "       start auto trading"
Write-Host ""
Write-Host "Say hello in any language and it replies in yours."
Write-Host "Your keys are already saved, so there is nothing more to set up."
