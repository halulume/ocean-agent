# Ocean Agent one-command installer (Windows)
# Installs uv, writes .env, and registers the MCP server in Claude Desktop.
$ErrorActionPreference = 'Stop'
$oaPkg = "ocean-agent@0.4.29"

# The console a new user lands in is the first impression, so it is dressed
# as Claude instead of left as a black DOS box: light ground, dark text, the
# amber mark. Colour goes through Write-Host and never ANSI, because an ANSI
# reset returns the line to the console's ORIGINAL dark-blue attributes and
# repaints half of every styled line in the old colours. DarkYellow and Gray
# are avoided too: PowerShell's palette draws them near white, so anything
# in them would vanish on this ground.
$ui = $Host.UI.RawUI
try {
    $ui.WindowTitle = "Claude - Ocean Agent"
    $ui.BackgroundColor = 'White'
    $ui.ForegroundColor = 'Black'
    Clear-Host
} catch { }
function Say($text)  { Write-Host "  $text" }
function Dim($text)  { Write-Host "  $text" -ForegroundColor DarkGray }
function Good($text) { Write-Host "  $text" -ForegroundColor DarkGreen }
function Bad($text)  { Write-Host "  $text" -ForegroundColor Red }
function Rule { Write-Host ("  " + ("-" * 66)) -ForegroundColor DarkGray }

Write-Host ""
Write-Host "  * " -ForegroundColor DarkRed -NoNewline
Write-Host "Claude" -NoNewline
Write-Host ("".PadLeft(46) + "Ocean Agent") -ForegroundColor DarkGray
Rule
Write-Host ""
Say "Installing Ocean Agent. This takes a minute or two."
Dim "Nothing to type here: a window opens and asks for what it needs."
Write-Host ""

# The install page (OA_UI, set by the launcher) shows progress in the
# browser so nobody has to read a console. Reporting is best effort: if
# the page is not there, the install just runs on without it.
function Report($step, $status) {
    if (-not $env:OA_UI) { return }
    try {
        Invoke-RestMethod -Method Post -Uri $env:OA_UI `
            -Body @{ step = $step; status = $status } -TimeoutSec 3 | Out-Null
    } catch { }
}

# 1) uv (installs its own Python automatically)
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Say "Preparing Python..."
    Report "python" "run"
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
} else {
    Say "Python is ready."
}
$uvx = Join-Path $env:USERPROFILE ".local\bin\uvx.exe"
if (-not (Test-Path $uvx)) {
    $cmd = Get-Command uvx -ErrorAction SilentlyContinue
    if ($cmd) { $uvx = $cmd.Source } else { $uvx = "uvx" }
}

# Bring up the install page and let it carry the rest: progress, the two
# credentials, and the finish card all happen there, so this console has
# nothing left worth reading.
$envDir  = Join-Path $env:USERPROFILE ".ocean-agent"
New-Item -ItemType Directory -Force $envDir | Out-Null
$envFile = Join-Path $envDir ".env"
$uiOut = Join-Path $env:TEMP "ocean_agent_ui.txt"
$uiProc = $global:oaUiProc
try {
    # the launcher already put the window up in this same console session,
    # so reuse it: a second one would leave the first stuck on its own page
    if ($env:OA_UI) { throw "reuse" }
    Remove-Item $uiOut -ErrorAction SilentlyContinue
    $uiProc = Start-Process -PassThru -WindowStyle Hidden -FilePath $uvx `
        -ArgumentList @("--from", "ocean-agent", "python", "-m",
            "ocean_agent.install_ui", "--env-file", "$envFile",
            "--stage", "install", "--timeout", "1800") `
        -RedirectStandardOutput $uiOut
    for ($i = 0; $i -lt 90; $i++) {
        if (Test-Path $uiOut) {
            $line = (Get-Content $uiOut -ErrorAction SilentlyContinue |
                     Where-Object { $_ -like "URL *" } | Select-Object -First 1)
            if ($line) {
                $uiUrl = $line.Substring(4).Trim()
                $env:OA_UI = ($uiUrl -replace "/\?n=", "/progress?n=")
                Start-Process $uiUrl
                break
            }
        }
        Start-Sleep -Milliseconds 500
    }
} catch { }
if ($env:OA_UI) {
    Dim "Follow along in the window that just opened."
}
Report "python" "done"

# 2) terms of use (declining aborts the install; details: oceanagent.fi)
Report "package" "run"
Say "Terms of use"
$env:PACIFICA_ENV_FILE = $envFile
$marker = Join-Path $env:USERPROFILE ".ocean_agent_builder_consent"
if ($env:OA_UI) {
    # the window asks, so this console never blocks on a question nobody
    # is looking at; the answer lands in the same marker file either way
    Remove-Item $marker -ErrorAction SilentlyContinue
    Report "terms" "run"
    Dim "Answer in the window, please."
    $answer = ""
    for ($i = 0; $i -lt 1200; $i++) {
        if (Test-Path $marker) {
            $answer = (Get-Content $marker -Raw -ErrorAction SilentlyContinue).Trim()
            if ($answer) { break }
        }
        Start-Sleep -Seconds 1
    }
    if ($answer -eq "declined") {
        Bad "Terms declined. Installation cancelled."
        exit 1
    }
} else {
    & $uvx --from $oaPkg python -m ocean_agent.builder_consent
    if ($LASTEXITCODE -eq 3) { exit 1 }
}

# 3) register in Claude Desktop config
Report "package" "done"
Report "register" "run"
Say "Connecting to Claude..."
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
        Bad "The existing Claude config was not valid JSON; it was rewritten (original saved as .bak)."
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
    Dim "Claude config updated."
} else {
    Dim "Claude config updated."
}

Report "register" "done"

# 4) credentials, last, on the page that is already open
Say "Your account"
$write = $true
if (Test-Path $envFile) {
    $ans = Read-Host "  .env already exists. Overwrite? (y/N)"
    if ($ans -notmatch '^[yY]') { $write = $false; Dim "Keeping the keys already saved." }
}
if ($write) {
    $done = $false
    if ($env:OA_UI -and $uiProc -and -not $uiProc.HasExited) {
        Dim "Waiting for the window: wallet address and API key."
        Report "keys" "run"
        $uiProc.WaitForExit()
        if (Test-Path $envFile) { $done = $true }
    }
    if (-not $done) {
        Dim "Opening a secure form..."
        try {
            & $uvx --from "ocean-agent" python -m ocean_agent.connect_ui `
                --env-file "$envFile" --timeout 600
            if ($LASTEXITCODE -eq 0) { $done = $true }
        } catch { }
    }
    if (-not $done) {
        Dim "The form could not open, so this window asks instead."
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
            Bad "Could not restrict permissions on $envFile."
        }
    }
    Good "Saved. Only you can read it."
}
$env:PACIFICA_ENV_FILE = $envFile

# Claude reads its config once at startup, so the tools only appear after
# a restart. Doing it here saves the user a step they cannot skip; if
# Claude was not running, it is simply started.
Write-Host ""
Say "Restarting Claude so the tools load..."
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
        Good "Claude restarted."
    } else {
        Dim "Could not find Claude. Open it yourself once."
    }
} catch {
    Dim "Could not restart Claude. Open it yourself once."
}

Write-Host ""
Rule
Write-Host ""
Good "Install complete."
Write-Host ""
Say "Go to Claude and just talk to it:"
Dim "    show me today's picks"
Dim "    start auto trading"
Write-Host ""
Dim "Any language works, and your keys are already saved."
Dim "Nothing else to type here. You can close this window."
Write-Host ""
