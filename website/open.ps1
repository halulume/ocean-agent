# Ocean Agent launcher (Windows)
# The console a new user lands in is the first impression, so this one is
# dressed as Claude rather than left as a black DOS box: light ground,
# dark text, the amber mark, and a chat-shaped prompt. It asks for the
# install line right here and runs it in the same window.
#
# Colour goes through Write-Host, not ANSI: an ANSI reset returns the line
# to the console's ORIGINAL attributes (dark blue), which repaints half of
# every styled line in the old DOS colours. DarkYellow and Gray are avoided
# on purpose: PowerShell's own palette paints them near white, so the mark
# and the prompt would simply vanish on this ground.
$ErrorActionPreference = 'Stop'
$installCmd = 'irm https://oceanagent.fi/install.ps1 | iex'

$ui = $Host.UI.RawUI
try {
    $ui.WindowTitle = "Claude - Ocean Agent"
    $ui.BackgroundColor = 'White'
    $ui.ForegroundColor = 'Black'
    Clear-Host
} catch { }

function Seg($color, $text) { Write-Host $text -ForegroundColor $color -NoNewline }
function Head {
    Write-Host ""
    Seg DarkRed "  * "
    Seg Black "Claude"
    Seg DarkGray ("".PadLeft(46) + "Ocean Agent")
    Write-Host ""
    Write-Host ("  " + ("-" * 66)) -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  Ocean Agent installs itself from here."
    Write-Host "  One line to go. It takes a minute or two." -ForegroundColor DarkGray
    Write-Host ""
}
Head

# --- uv, which brings its own Python ----------------------------------
# in a child process with every stream discarded: its own installer prints
# a page of PATH advice that would wreck the window a new user is reading
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "  Preparing... (about a minute)" -ForegroundColor DarkGray
    $null = & powershell -NoProfile -ExecutionPolicy ByPass -Command `
        "irm https://astral.sh/uv/install.ps1 | iex" 2>&1
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    try { Clear-Host } catch { }
    Head
}

# --- the line, typed here ---------------------------------------------
Write-Host "  Copy this line:"
Write-Host ""
Write-Host "    $installCmd" -ForegroundColor DarkCyan
Write-Host ""
Write-Host "  Right click pastes into this window." -ForegroundColor DarkGray
Write-Host ""
$line = ""
for ($try = 0; $try -lt 5; $try++) {
    Seg DarkRed "  > "
    $line = (Read-Host).Trim()
    if ($line -like "*install.ps1*") { break }
    if ($line -eq "") { $line = $installCmd; break }
    Write-Host "  That is not the line above. Try again." -ForegroundColor DarkGray
    $line = ""
}
if (-not $line) { $line = $installCmd }
Write-Host ""
Write-Host ("  " + ("-" * 66)) -ForegroundColor DarkGray
Write-Host ""
Invoke-Expression $line
