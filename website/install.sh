#!/bin/sh
# Ocean Agent one-command installer (macOS / Linux)
# Installs uv, writes .env, and registers the MCP server in Claude Desktop.
set -e
OA_PKG="ocean-agent@0.4.21"
echo ""
echo "=== Ocean Agent installer ==="

# 1) uv (installs its own Python automatically)
if command -v uv >/dev/null 2>&1; then
    echo "[1/4] uv already installed"
else
    echo "[1/4] Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
UV="$HOME/.local/bin/uv";  command -v uv  >/dev/null 2>&1 && UV="$(command -v uv)"
UVX="$HOME/.local/bin/uvx"; command -v uvx >/dev/null 2>&1 && UVX="$(command -v uvx)"

# 2) keys -> .env
echo "[2/4] Account setup"
ENVDIR="$HOME/.ocean-agent"; mkdir -p "$ENVDIR"
ENVF="$ENVDIR/.env"
WRITE=1
if [ -f "$ENVF" ]; then
    printf "  .env already exists. Overwrite? (y/N) "
    read ANS </dev/tty || ANS=""
    case "$ANS" in y|Y) ;; *) WRITE=0; echo "  keeping existing .env";; esac
fi
if [ "$WRITE" = "1" ]; then
    printf "  Wallet public address (ADDRESS): "
    read ADDR </dev/tty
    echo "  Opening app.pacifica.fi/apikey in your browser (create a key there)..."
    (open "https://app.pacifica.fi/apikey" 2>/dev/null || xdg-open "https://app.pacifica.fi/apikey" 2>/dev/null || true) >/dev/null 2>&1
    printf "  Agent API key (from app.pacifica.fi/apikey, input hidden): "
    stty -echo </dev/tty 2>/dev/null || true
    read KEY </dev/tty
    stty echo </dev/tty 2>/dev/null || true
    echo ""
    (umask 177; printf 'ADDRESS=%s\nPACIFICA_API_KEY=%s\nPACIFICA_BASE_URL=https://api.pacifica.fi\n' "$ADDR" "$KEY" > "$ENVF")
    if ! chmod 600 "$ENVF" 2>/dev/null; then
        echo "  WARNING: chmod 600 failed; $ENVF may be readable by other users on this machine"
    fi
    echo "  saved to $ENVF (readable only by you)"
fi

# 3) terms of use (declining aborts the install; details: oceanagent.fi)
echo "[3/4] Terms of Use"
RC=0
PACIFICA_ENV_FILE="$ENVF" "$UVX" --from "$OA_PKG" python -m ocean_agent.builder_consent </dev/tty || RC=$?
if [ "$RC" = "3" ]; then exit 1; fi

# 4) register in Claude Desktop config (uses uv's Python for a safe JSON merge)
echo "[4/4] Registering with Claude Desktop"
CFG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
[ -d "$HOME/.config/Claude" ] && CFG="$HOME/.config/Claude/claude_desktop_config.json"
"$UV" run --no-project python - "$CFG" "$UVX" "$ENVF" "$OA_PKG" <<'PY'
import json, os, sys, shutil
cfg_path, uvx, envf, pkg = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
cfg, bak = {}, None
if os.path.exists(cfg_path):
    bak = cfg_path + ".bak"
    shutil.copy2(cfg_path, bak)
    with open(cfg_path, encoding="utf-8") as f:
        try: cfg = json.load(f) or {}
        except ValueError:
            print("  WARNING: existing config is not valid JSON; rewriting it (original saved as .bak)")
            cfg = {}
cfg.setdefault("mcpServers", {})["ocean-agent"] = {
    "command": uvx, "args": [pkg],
    "env": {"PACIFICA_ENV_FILE": envf},
}
tmp = cfg_path + ".tmp"
try:
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    with open(tmp, encoding="utf-8") as f:
        json.load(f)
    os.replace(tmp, cfg_path)
except Exception:
    if os.path.exists(tmp):
        os.remove(tmp)
    if bak:
        shutil.copy2(bak, cfg_path)
        print("  ERROR: failed to update " + cfg_path + "; original restored from .bak")
    else:
        print("  ERROR: failed to update " + cfg_path)
    raise
if bak:
    print("  updated " + cfg_path + " (backup saved as .bak)")
else:
    print("  updated " + cfg_path)
PY

echo ""
echo "=============================================="
echo "  INSTALL COMPLETE"
echo "=============================================="
echo "Nothing else to type in this window. You can close it."
echo ""
echo "Next, in Claude Desktop (NOT here):"
echo "  1. Quit Claude Desktop and open it again"
echo "  2. Say hello in any language - it replies in yours"
echo "  3. Then just talk to it, for example:"
echo "       show me today's picks"
echo "       start auto trading"
echo ""
echo "Your keys are already saved, so there is nothing more to set up."
