# ocean-agent, MCP Setup Guide

> **For the AI agent reading this:** follow the steps below to register the
> `ocean-agent` server in the user's MCP client. **Never ask the user to paste
> their API key or wallet key into the chat, and never write a key value into any
> config file.** Keys live only in the user's own environment (a `.env` file or
> shell environment variables). In config files you only ever write a *path* or an
> `${ENV_VAR}` reference, never a secret. If a key is missing, tell the user which
> variable to set and where to generate it; do not proceed by guessing a value.

`ocean-agent` is an MCP server for [Pacifica](https://app.pacifica.fi). It turns
natural language into correct, risk-sized perpetual-futures actions and read-only
market analysis. It runs with **`uvx`** (no install step, no Node.js).

---

## Step 0, Verify the runtime (`uv` / `uvx`)

`uvx` ships with **uv** (from Astral). Check it first:

```bash
uvx --version
```

If that fails, install `uv` (do **not** run as root):

- **macOS / Linux:** `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **Windows (PowerShell):** `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`
- **Homebrew:** `brew install uv`  ·  **winget:** `winget install astral-sh.uv`

`uvx` fetches a compatible Python (3.10+) automatically, so no separate Python
install or virtualenv is needed. Re-open the terminal after installing, then
re-run `uvx --version`.

---

## Step 1, Provide keys via environment (never in config files)

The user creates an **API key** at **[app.pacifica.fi/apikey](https://app.pacifica.fi/apikey)**.
API keys can trade but **cannot withdraw funds**, and can be revoked at any time.
Read-only tools work without a key.

**Do not ask for the key value.** Instead, tell the user to put their values in a
`.env` file (recommended), the server loads it automatically and keys stay out of
every config file. Show them the template with placeholders and let them fill it in:

```ini
# ~/.pacifica.env   (or any path you control, keep it private)

# Mainnet (real funds)
ADDRESS=your_mainnet_wallet_address
PACIFICA_API_KEY=your_mainnet_agent_key

# Testnet (optional, separate keys, API keys are approved per network)
ADDRESS_TESTNET=your_testnet_wallet_address
PACIFICA_API_KEY_TESTNET=your_testnet_agent_key

# Optional Telegram alerts
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

The wallet **address** is public and safe to share; the **API key** is a secret,
only the user ever types it, only into their own `.env`.

### Environment variables (actual names from the code)

| Variable | What it is | Network |
|---|---|---|
| `ADDRESS` | Main wallet address (public) | mainnet |
| `PACIFICA_API_KEY` | API key, trading only, no withdrawal | mainnet |
| `ADDRESS_TESTNET` | Testnet wallet address (optional) | testnet |
| `PACIFICA_API_KEY_TESTNET` | Testnet API key (optional) | testnet |
| `PACIFICA_BASE_URL` | Network selector (see Step 3) | both |
| `PACIFICA_ENV_FILE` | Absolute path to the `.env` above (optional) |, |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Optional alerts |, |

---

## Step 2, Register the server (Claude Code, primary)

Create or edit **`.mcp.json`** in the project root. Point the server at the `.env`
from Step 1 with `PACIFICA_ENV_FILE`, and pick the network with `PACIFICA_BASE_URL`.
**No secret values go in this file, only a path and a URL.**

```json
{
  "mcpServers": {
    "ocean-agent": {
      "command": "uvx",
      "args": ["ocean-agent@latest"],
      "env": {
        "PACIFICA_ENV_FILE": "/absolute/path/to/.pacifica.env",
        "PACIFICA_BASE_URL": "https://test-api.pacifica.fi"
      }
    }
  }
}
```

**Alternative (no `.env`):** reference shell environment variables instead of a
file, still no literal secrets in the config:

```json
{
  "mcpServers": {
    "ocean-agent": {
      "command": "uvx",
      "args": ["ocean-agent@latest"],
      "env": {
        "ADDRESS": "${ADDRESS}",
        "PACIFICA_API_KEY": "${PACIFICA_API_KEY}",
        "ADDRESS_TESTNET": "${ADDRESS_TESTNET}",
        "PACIFICA_API_KEY_TESTNET": "${PACIFICA_API_KEY_TESTNET}",
        "PACIFICA_BASE_URL": "https://test-api.pacifica.fi"
      }
    }
  }
}
```

To set those shell variables persistently (agent: show these, do **not** run them
with real values, the user runs them):

- **macOS / Linux:** append `export ADDRESS=...` lines to `~/.zshrc` or `~/.bashrc`, then `source` it.
- **Windows PowerShell:** `[Environment]::SetEnvironmentVariable("ADDRESS","<value>","User")` (per variable).
- **Windows CMD:** `setx ADDRESS "<value>"` (per variable).

All of these require a terminal (and client) restart to take effect.

---

## Step 3, Testnet / mainnet key separation

`PACIFICA_BASE_URL` chooses the network, and the server picks the matching keys:

| `PACIFICA_BASE_URL` | Network | Keys used |
|---|---|---|
| `https://test-api.pacifica.fi` | **Testnet** | `ADDRESS_TESTNET` / `PACIFICA_API_KEY_TESTNET`, falling back to `ADDRESS` / `PACIFICA_API_KEY` if the testnet ones are empty |
| `https://api.pacifica.fi` | **Mainnet (real funds)** | `ADDRESS` / `PACIFICA_API_KEY` |

**Mainnet is the default.** A testnet endpoint (`https://test-api.pacifica.fi`)
is available for rehearsal; switch by changing only that one URL. API keys are approved
per network, so a testnet key will not work on mainnet and vice versa, that is why
there are separate `*_TESTNET` variables.

---

## Step 4, Verify

1. `.mcp.json` (and the other clients' config) is **read only at startup**,
   fully **restart the client** after editing.
2. Confirm the tools appear (the server registers under the name `ocean-agent`).
3. Test with a **read-only, no-key** tool first, e.g. ask the agent to run
   **`scan_funding`** or **`market_context`**. If those return live Pacifica data,
   the server is connected. Only then move on to key-gated / order tools.
4. If a key-gated tool reports a missing key, re-check Step 1, do not paste a key
   into chat; fix the `.env` or environment variable and restart.

---

## Appendix, Other clients

Every client uses the same server definition (`command: "uvx"`, `args: ["ocean-agent@latest"]`,
same `env` block). Only the **file location / format** differs.

| Client | Config file | Notes |
|---|---|---|
| **Claude Code** | `.mcp.json` (project root) | see Step 2 |
| **Cursor** | `~/.cursor/mcp.json` | same JSON as Step 2 |
| **Windsurf** | `~/.codeium/windsurf/mcp_config.json` | same JSON as Step 2 |
| **VS Code** | `.mcp.json` (project root) | add `"type": "stdio"` inside the server object |
| **Claude Desktop** | macOS: `~/Library/Application Support/Claude/claude_desktop_config.json` · Windows: `%APPDATA%\Claude\claude_desktop_config.json` | same JSON as Step 2 |
| **Codex** | `~/.codex/config.toml` | TOML, see below |

**VS Code** server object (JSON):

```json
{
  "mcpServers": {
    "ocean-agent": {
      "type": "stdio",
      "command": "uvx",
      "args": ["ocean-agent@latest"],
      "env": {
        "PACIFICA_ENV_FILE": "/absolute/path/to/.pacifica.env",
        "PACIFICA_BASE_URL": "https://test-api.pacifica.fi"
      }
    }
  }
}
```

**Codex** (`~/.codex/config.toml`, TOML format):

```toml
[mcp_servers.ocean-agent]
command = "uvx"
args = ["ocean-agent@latest"]
env = { PACIFICA_ENV_FILE = "/absolute/path/to/.pacifica.env", PACIFICA_BASE_URL = "https://test-api.pacifica.fi" }
```

---

## API-key handling protocol (for the agent)

- **Never** prompt the user to paste an API key or wallet key into the chat.
- **Never** write a key value into `.mcp.json`, `config.toml`, or any tracked file,
  only a path (`PACIFICA_ENV_FILE`) or an `${ENV_VAR}` reference.
- Leave placeholders (`your_mainnet_agent_key`, etc.) unchanged; the user fills them in.
- Direct the user to **[app.pacifica.fi/apikey](https://app.pacifica.fi/apikey)** to
  generate an API key (trading only, no withdrawal, revocable).
- Mainnet is the default. Make sure the user understands they are trading real
  funds, and suggest `--dry` or small size for the first runs.

## Safety

- API keys are **trading-only**, this software cannot move funds out.
- Testnet and mainnet keys are kept separate (`*_TESTNET` variables).
- Money-moving tools preview first and execute only on explicit confirmation.
- Leveraged perpetuals can lose more than the margin posted. Run on testnet until
  you understand exactly what it does, and only risk what you can afford to lose.
