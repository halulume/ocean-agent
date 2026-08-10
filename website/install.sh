#!/bin/sh
# Ocean Agent one-command installer (macOS / Linux)
# Installs uv, writes .env, and registers the MCP server in Claude Desktop.
set -e
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
    printf "  Agent API key (from app.pacifica.fi/apikey): "
    read KEY </dev/tty
    (umask 177; printf 'ADDRESS=%s\nPACIFICA_API_KEY=%s\nPACIFICA_BASE_URL=https://api.pacifica.fi\n' "$ADDR" "$KEY" > "$ENVF")
    chmod 600 "$ENVF" 2>/dev/null || true
    echo "  saved to $ENVF (readable only by you)"
fi

# 3) terms of use (declining aborts the install; details: oceanagent.vercel.app)
echo "[3/4] Terms of Use"
RC=0
PACIFICA_ENV_FILE="$ENVF" "$UVX" --from ocean-agent@latest python -m ocean_agent.builder_consent </dev/tty || RC=$?
if [ "$RC" = "3" ]; then exit 1; fi

# 4) register in Claude Desktop config (uses uv's Python for a safe JSON merge)
echo "[4/4] Registering with Claude Desktop"
CFG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
[ -d "$HOME/.config/Claude" ] && CFG="$HOME/.config/Claude/claude_desktop_config.json"
"$UV" run --no-project python - "$CFG" "$UVX" "$ENVF" <<'PY'
import json, os, sys, shutil
cfg_path, uvx, envf = sys.argv[1], sys.argv[2], sys.argv[3]
os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
cfg = {}
if os.path.exists(cfg_path):
    shutil.copy2(cfg_path, cfg_path + ".bak")
    with open(cfg_path, encoding="utf-8") as f:
        try: cfg = json.load(f) or {}
        except ValueError: cfg = {}
cfg.setdefault("mcpServers", {})["ocean-agent"] = {
    "command": uvx, "args": ["ocean-agent@latest"],
    "env": {"PACIFICA_ENV_FILE": envf},
}
with open(cfg_path, "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2)
print("  updated " + cfg_path + " (backup saved as .bak)")
PY

echo ""
echo "Done. Restart Claude Desktop and the Ocean Agent tools appear in chat."
echo "Optional 24/7 bot:  \"$UV\" run --with ocean-agent python -m ocean_agent.autonomous --dry"
