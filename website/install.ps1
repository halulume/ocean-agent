# Ocean Agent one-command installer (Windows)
# Installs uv, writes .env, and registers the MCP server in Claude Desktop.
$ErrorActionPreference = 'Stop'
$oaPkg = "ocean-agent@0.4.36"

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

# Glyphs are built from code points rather than typed literally: this file
# travels over the wire and is executed straight from the response, so a
# stray encoding guess would turn every mark into mojibake. Square corners,
# not rounded ones, because Consolas carries these and not those.
# Consolas carries no check mark, and a missing glyph draws as a hollow box,
# so the three states are told apart by fill and colour instead: a filled dot
# for done, a teal ring for the step running now, a grey ring for waiting.
$CHK = [string][char]0x25CF      # done
$DOT = [string][char]0x25CB      # in progress
$RING = [string][char]0x25CB     # waiting
$H = [string][char]0x2500        # horizontal
$V = [string][char]0x2502        # vertical
$TL = [string][char]0x250C; $TR = [string][char]0x2510
$BL = [string][char]0x2514; $BR = [string][char]0x2518
$W = 64                          # inner width of the card
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

# Whether this host can redraw in place. An AI client running the install for
# the user captures the output instead of showing a console, and there the
# card would append a fresh copy on every update: thirteen lines per step,
# unreadable. Setting the cursor is not the test, because it succeeds under
# capture and simply has no effect; whether stdout is redirected is.
$script:quiet = $false
try { $script:quiet = [Console]::IsOutputRedirected } catch { }
if ($env:OA_QUIET) { $script:quiet = $true }

# The console cannot draw a rounded card with a shadow, but it can draw the
# frame around one, which is what makes the window read as Claude's rather
# than as a DOS box. In quiet mode none of it is drawn: the steps speak for
# themselves, one line each.
function Edge($left, $right) {
    if ($script:quiet) { return }
    Write-Host ("  " + $left + ($H * $W) + $right) -ForegroundColor DarkGray
}
function Line($text, $color) {
    if ($script:quiet) { return }
    Write-Host ("  " + $V + " ") -ForegroundColor DarkGray -NoNewline
    if ($color) { Write-Host $text.PadRight($W - 2) -ForegroundColor $color -NoNewline }
    else { Write-Host $text.PadRight($W - 2) -NoNewline }
    Write-Host (" " + $V) -ForegroundColor DarkGray
}
function Blank { Line "" $null }

if (-not $script:quiet) {
Write-Host ""
Edge $TL $TR
Write-Host ("  " + $V + " ") -ForegroundColor DarkGray -NoNewline
Write-Host "* " -ForegroundColor DarkRed -NoNewline
Write-Host "Claude" -NoNewline
Write-Host ("Ocean Agent".PadLeft($W - 10)) -ForegroundColor DarkGray -NoNewline
Write-Host (" " + $V) -ForegroundColor DarkGray
Line ($H * ($W - 2)) DarkGray
Blank
Line "Installing" $null
Line "This usually takes a minute or two." DarkGray
Blank
}
if ($script:quiet) { Write-Host "Installing Ocean Agent." }

# The three rows mirror the browser window exactly, and they are redrawn in
# place as each step lands. Where the cursor cannot be moved (redirected
# output, an odd host), every draw simply appends instead.
$LABELS = @(@("python", "Preparing Python"),
            @("package", "Installing Ocean Agent"),
            @("register", "Connecting to Claude"))
$script:stepState = @{ python = "wait"; package = "wait"; register = "wait" }
# quiet mode is decided on the first draw; said[] keeps a step from being
# announced twice, since Report and Status both redraw
$script:said = @{ python = $false; package = $false; register = $false }
$script:lastSaid = ""
$script:rowsTop = $null
$script:parked = $null
$script:statusText = ""

function Row($mark, $markColor, $label, $labelColor) {
    Write-Host ("  " + $V + " ") -ForegroundColor DarkGray -NoNewline
    Write-Host "$mark  " -ForegroundColor $markColor -NoNewline
    if ($labelColor) { Write-Host $label.PadRight($W - 5) -ForegroundColor $labelColor -NoNewline }
    else { Write-Host $label.PadRight($W - 5) -NoNewline }
    Write-Host (" " + $V) -ForegroundColor DarkGray
}

function Draw-Rows {
    $canMove = $false
    try {
        if ($null -eq $script:rowsTop) {
            $script:rowsTop = $Host.UI.RawUI.CursorPosition
        } else {
            $Host.UI.RawUI.CursorPosition = $script:rowsTop
        }
        $canMove = $true
    } catch { }
    # A host that cannot move the cursor cannot redraw the card either, so
    # every update would append a whole new copy. That is what an AI client
    # sees when it runs this for you: pages of near-identical boxes. There,
    # say one line per step and nothing else.
    if (-not $canMove -and $null -eq $script:rowsTop) { $script:quiet = $true }
    if ($script:quiet) {
        foreach ($pair in $LABELS) {
            $st = $script:stepState[$pair[0]]
            if ($st -eq "done" -and -not $script:said[$pair[0]]) {
                $script:said[$pair[0]] = $true
                Write-Host ("  [done] " + $pair[1])
            }
        }
        # the status line carries the two things the user must act on (the
        # terms question and the window asking for keys), so it is said once
        if ($script:statusText -and $script:statusText -ne $script:lastSaid) {
            $script:lastSaid = $script:statusText
            Write-Host ("  " + $script:statusText)
        }
        return
    }
    foreach ($pair in $LABELS) {
        $st = $script:stepState[$pair[0]]
        if ($st -eq "done") { Row $CHK DarkCyan $pair[1] $null }
        elseif ($st -eq "run") { Row $DOT DarkCyan $pair[1] $null }
        else { Row $RING DarkGray $pair[1] DarkGray }
    }
    Blank
    Line $script:statusText DarkGray
    # the frame closes under the status line, and the cursor parks below it so
    # anything printed later lands outside the card instead of eating its edge
    if ($null -eq $script:parked) {
        Edge $BL $BR
        try { $script:parked = $Host.UI.RawUI.CursorPosition } catch { }
    } elseif ($canMove) {
        try { $Host.UI.RawUI.CursorPosition = $script:parked } catch { }
    } else {
        Write-Host ""
    }
}

function Status($text) {
    $script:statusText = $text
    Draw-Rows
}

# Progress goes to the browser window too (OA_UI). Reporting is best effort:
# if the page is not there, the install just runs on without it.
function Report($step, $status) {
    if ($script:stepState.ContainsKey($step)) {
        $script:stepState[$step] = $status
        Draw-Rows
    }
    if (-not $env:OA_UI) { return }
    try {
        Invoke-RestMethod -Method Post -Uri $env:OA_UI `
            -Body @{ step = $step; status = $status } -TimeoutSec 3 | Out-Null
    } catch { }
}
Draw-Rows

# 1) uv (installs its own Python automatically)
Report "python" "run"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    # uv's own installer prints a page of PATH advice, which would scroll the
    # rows away; it runs in a child process with every stream discarded
    $uvCmd = "iex (irm https://astral.sh/uv/install.ps1)"
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'      # same stderr trap as below
    $null = & powershell -NoProfile -ExecutionPolicy ByPass -Command $uvCmd 2>&1
    $ErrorActionPreference = $prevEAP
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
$uvx = Join-Path $env:USERPROFILE ".local\bin\uvx.exe"
if (-not (Test-Path $uvx)) {
    $cmd = Get-Command uvx -ErrorAction SilentlyContinue
    if ($cmd) { $uvx = $cmd.Source } else { $uvx = "" }
}
# Falling through with the bare string "uvx" made every later call raise
# CommandNotFoundException, which does not touch $LASTEXITCODE, so the
# install walked past its own failure and still printed "Install complete".
if (-not $uvx) {
    Status ""
    Bad "uv could not be installed, so nothing else can run."
    Write-Host ""
    Say "Check your internet connection and run the line again."
    Dim "If it keeps failing, install uv yourself and retry:"
    Dim "    irm https://astral.sh/uv/install.ps1 | iex"
    Write-Host ""
    exit 1
}
# Let uv bring its own Python. A machine with the Microsoft Store Python on
# PATH otherwise hands uv an interpreter it cannot launch, and the install
# dies on "Unable to create process using ...WindowsApps\python.exe".
$env:UV_PYTHON_PREFERENCE = "only-managed"

$envDir  = Join-Path $env:USERPROFILE ".ocean-agent"
New-Item -ItemType Directory -Force $envDir | Out-Null
$envFile = Join-Path $envDir ".env"
Report "python" "done"

# 2) the package, downloaded BEFORE the window is asked for. On a machine
# with nothing on it this pulls a Python and forty wheels, minutes on a slow
# line, and the window cannot exist until it lands. Starting the window
# first and waiting a fixed spell is how an install ends up looking finished
# while the browser never opened.
Report "package" "run"
Status "Downloading Ocean Agent and its Python..."
# uv writes its download progress to stderr, and under ErrorActionPreference
# Stop PowerShell turns every one of those lines into a terminating error. The
# preference is lowered around the call and put back straight after, so the
# exit code decides the outcome rather than the noise.
$prepLog = Join-Path $env:TEMP "ocean_agent_install.log"
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $uvx --from $oaPkg python -c "import ocean_agent" *> $prepLog
$prepCode = $LASTEXITCODE
$ErrorActionPreference = $prevEAP
if ($prepCode -ne 0) {
    Status ""
    Bad "Could not install Ocean Agent."
    Write-Host ""
    Dim ((Get-Content $prepLog -Tail 12 -ErrorAction SilentlyContinue) -join "`r`n")
    Dim "Full log: $prepLog"
    Write-Host ""
    Say "Nothing was saved. Check your connection and run the line again."
    exit 1
}
Report "package" "done"

# Bring up the install page and let it carry the rest: progress, the two
# credentials, and the finish card all happen there, so this console has
# nothing left worth reading. The package is already local by now, so the
# window comes up in a second or two; the wait below is only a safety net.
$uiOut = Join-Path $env:TEMP "ocean_agent_ui.txt"
$uiProc = $global:oaUiProc
try {
    # the launcher already put the window up in this same console session,
    # so reuse it: a second one would leave the first stuck on its own page
    if ($env:OA_UI) { throw "reuse" }
    # The page writes its own address to $uiOut. Reading it out of a
    # redirected stdout looked equivalent and was not: started hidden, that
    # file sometimes never reaches disk, and the install then walks past the
    # window it had just opened.
    Remove-Item $uiOut, "$uiOut.log" -ErrorAction SilentlyContinue
    $uiProc = Start-Process -PassThru -WindowStyle Hidden -FilePath $uvx `
        -ArgumentList @("--from", $oaPkg, "python", "-m",
            "ocean_agent.install_ui", "--env-file", "$envFile",
            "--stage", "install", "--timeout", "1800",
            "--url-file", "$uiOut") `
        -RedirectStandardOutput "$uiOut.log"
    for ($i = 0; $i -lt 240; $i++) {
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
if ($env:OA_UI) { Status "A window just opened; it follows along." }

# 3) terms of use (declining aborts the install; details: oceanagent.fi)
$env:PACIFICA_ENV_FILE = $envFile
$marker = Join-Path $env:USERPROFILE ".ocean_agent_builder_consent"
if ($env:OA_UI) {
    # the window asks, so this console never blocks on a question nobody
    # is looking at; the answer lands in the same marker file either way
    Remove-Item $marker -ErrorAction SilentlyContinue
    Report "terms" "run"
    Status "Terms of use: please answer in the window."
    $answer = ""
    for ($i = 0; $i -lt 1200; $i++) {
        if (Test-Path $marker) {
            $answer = (Get-Content $marker -Raw -ErrorAction SilentlyContinue).Trim()
            if ($answer) { break }
        }
        Start-Sleep -Seconds 1
    }
    if ($answer -eq "declined") {
        Status ""
        Bad "Terms declined. Installation cancelled."
        exit 1
    }
    # No answer is not a yes. The loop above can also end because the window
    # was closed or never seen, and continuing would install a thing that
    # places real orders under terms nobody agreed to.
    if ($answer -ne "approved") {
        Status ""
        Bad "The terms were never answered, so the install stopped here."
        Say "Nothing was connected. Run the line again when you are ready."
        exit 1
    }
} else {
    & $uvx --from $oaPkg python -m ocean_agent.builder_consent
    if ($LASTEXITCODE -eq 3) { exit 1 }
}

# 4) register in Claude Desktop config
Report "register" "run"
Status "Writing the Claude config."
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
    Status "Claude config updated."
} else {
    Status "Claude config updated."
}

Report "register" "done"

# 5) credentials, last, on the page that is already open
Status "Your account"
$write = $true
if (Test-Path $envFile) {
    # same reason as below: with no keyboard attached, take the safe answer
    if ($script:quiet) {
        $write = $false
        Status "Keys already saved, so they were left alone."
    } else {
        $ans = Read-Host "  .env already exists. Overwrite? (y/N)"
        if ($ans -notmatch '^[yY]') { $write = $false; Status "Keeping the keys already saved." }
    }
}
if ($write) {
    $done = $false
    if ($env:OA_UI -and $uiProc -and -not $uiProc.HasExited) {
        Status "Waiting for the window: wallet address and API key."
        Report "keys" "run"
        $uiProc.WaitForExit()
        if (Test-Path $envFile) { $done = $true }
    }
    if (-not $done) {
        Status "Opening a secure form..."
        try {
            & $uvx --from "$oaPkg" python -m ocean_agent.connect_ui `
                --env-file "$envFile" --timeout 600
            if ($LASTEXITCODE -eq 0) { $done = $true }
        } catch { }
    }
    # Asking here needs a keyboard on the other end of stdin. Under an AI
    # client there is none: the question would either read end-of-file or
    # sit waiting forever. Point at the command that opens the form instead.
    if (-not $done -and $script:quiet) {
        Write-Host ""
        Say "Ocean Agent is installed, but no keys were entered."
        Say "Run this to add them, in a terminal you can type into:"
        Dim "    uvx --from $oaPkg python -m ocean_agent.connect_ui --env-file `"$envFile`""
        Write-Host ""
        $write = $false
        $done = $true
    }
    if (-not $done) {
        Status ""
        Dim "The form could not open, so this window asks instead."
        $addr = Read-Host "  Wallet public address (ADDRESS)"
        try { Start-Process "https://app.pacifica.fi/apikey" } catch {}
        $keySec = Read-Host "  Agent API key (input hidden)" -AsSecureString
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($keySec)
        $key  = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        # An empty answer used to be written out as an empty .env, and the
        # install then reported success while the tools had nothing to sign
        # with. Say so instead, and leave the file alone.
        if ([string]::IsNullOrWhiteSpace($addr) -or [string]::IsNullOrWhiteSpace($key)) {
            Bad "No wallet address or key was entered, so nothing was saved."
            Write-Host ""
            Say "Ocean Agent is installed. To add your keys later, run:"
            Dim "    uvx --from $oaPkg python -m ocean_agent.connect_ui --env-file `"$envFile`""
            Write-Host ""
            $write = $false
        } else {
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
    }
    if ($write) { Status "Keys saved. Only you can read them." }
}
$env:PACIFICA_ENV_FILE = $envFile

# Claude reads its config once at startup, so the tools only appear after
# a restart. Doing it here saves the user a step they cannot skip; if
# Claude was not running, it is simply started.
Write-Host ""
# Never kill the app that is running this. When Claude itself was asked to
# install (the Code tab route the site now offers first), closing "Claude"
# closes the session mid-install: the user watches their own assistant
# disappear and never learns whether it finished. Ask instead.
if ($script:quiet) {
    Status ""
    Say "Restart Claude yourself so the trading tools load."
} else {
Status "Restarting Claude so the tools load..."
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
        Status "Claude restarted."
    } else {
        Status "Could not find Claude. Open it yourself once."
    }
} catch {
    Status "Could not restart Claude. Open it yourself once."
}
}

Status ""
Write-Host ""
Write-Host "  $CHK  " -ForegroundColor DarkCyan -NoNewline
Write-Host "Install complete."
Write-Host ""
Say "Go to Claude and just talk to it:"
Dim "    show me today's picks"
Dim "    start auto trading"
Write-Host ""
Dim "Any language works, and your keys are already saved."
Dim "Nothing else to type here. You can close this window."
Write-Host ""
