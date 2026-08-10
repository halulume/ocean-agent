# Ocean Agent one-command installer (Windows)
# Installs uv, writes .env, and registers the MCP server in Claude Desktop.
$ErrorActionPreference = 'Stop'
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
    $addr = Read-Host "  Wallet public address (ADDRESS)"
    Write-Host "  Opening app.pacifica.fi/apikey in your browser (create a key there)..."
    try { Start-Process "https://app.pacifica.fi/apikey" } catch {}
    $key  = Read-Host "  Agent API key (from app.pacifica.fi/apikey)"
    @(
        "ADDRESS=$addr"
        "PACIFICA_API_KEY=$key"
        "PACIFICA_BASE_URL=https://api.pacifica.fi"
    ) -join "`r`n" | Out-File -Encoding ascii $envFile
    # Lock the file to the current user only (remove inherited access).
    try {
        icacls $envFile /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null
    } catch {}
    Write-Host "  saved to $envFile (readable only by you)"
}

# 3) terms of use (declining aborts the install; details: oceanagent.vercel.app)
Write-Host "[3/4] Terms of Use"
$env:PACIFICA_ENV_FILE = $envFile
& $uvx --from ocean-agent@latest python -m ocean_agent.builder_consent
if ($LASTEXITCODE -eq 3) { exit 1 }

# 4) register in Claude Desktop config
Write-Host "[4/4] Registering with Claude Desktop"
$cfgDir  = Join-Path $env:APPDATA "Claude"
$cfgPath = Join-Path $cfgDir "claude_desktop_config.json"
$server  = [pscustomobject]@{
    command = "$uvx"
    args    = @("ocean-agent@latest")
    env     = [pscustomobject]@{ PACIFICA_ENV_FILE = "$envFile" }
}
if (Test-Path $cfgPath) {
    Copy-Item $cfgPath "$cfgPath.bak" -Force
    $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
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
$cfg | ConvertTo-Json -Depth 10 | Out-File -Encoding utf8 $cfgPath
Write-Host "  updated $cfgPath (backup saved as .bak)"

Write-Host ""
Write-Host "Done. Restart Claude Desktop and the Ocean Agent tools appear in chat." -ForegroundColor Green
Write-Host "Optional 24/7 bot:  `"$uvx`" --from ocean-agent python -m ocean_agent.autonomous --dry"
