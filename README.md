# ocean-agent

> **🌊 OPEN BETA**: Ocean Agent is in open beta for everyone as of Aug 26, 2026.
> No invitation and no waitlist: install it and connect your own account.
> The bracket engine is still being live-validated with our own funds too.
> Follow the daily scorecard on [Telegram](https://t.me/+V7wwRr6n4ZtmOGFl).

> English · [한국어](README.ko.md)

🌊 **Website:** [oceanagent.fi](https://oceanagent.fi) · **PyPI:** [ocean-agent](https://pypi.org/project/ocean-agent/)

**Tell your AI to trade.** An MCP server for [Pacifica](https://app.pacifica.fi)
that turns natural language into correct, risk-sized perpetual futures orders,
plus a 24/7 autonomous trading entity governed by a policy file you control.

```bash
uvx ocean-agent        # no install needed
```

> ⚠️ This places real orders with real money. Read the
> [disclaimer](DISCLAIMER.md) before connecting an account.

Built entirely on Pacifica. Calls the Pacifica REST API directly with the same
Ed25519 agent-key signing the official tooling uses, no npm dependency.

---

## Why this instead of raw API access

The official Pacifica MCP exposes the API as-is: your AI must compute exact
prices and sizes itself, and a price that isn't a multiple of the market's tick
size is rejected by the exchange. ocean-agent adds the layer above that:

| | Raw API / official MCP | ocean-agent |
|---|---|---|
| Order prices | AI computes exact values | Say *"3% stop"*, tick/lot/min-order corrected automatically |
| Position sizing | Manual | Risk-based (fixed % of capital at risk per trade) |
| Safety | None | Two-step confirm gate on every money-moving tool |
| Statistics | None | Measured win rates and expected value, not textbook theory |

---

## MCP tools

**Market & analysis**
- `analyze_chart`, multi-timeframe indicator snapshot with *measured* hit rates
  per signal on that specific coin and timeframe. Says "no edge detected" when
  there isn't one.
- `top_setups`, live ranking of statistically-proven setups (EV × win rate ×
  sample confidence), with entry, stop, target and leverage
- `market_context`, Fear & Greed regime read
- `scan_funding`, every market ranked by funding APR
- `learned_winrates` / `learned_combos`, win-rate database built from live
  observation, including multi-signal combinations
- `review_predictions`, past calls graded against what actually happened

**Trading**
- `open_with_bracket`, entry plus exchange-native TP/SL in one call. The stops
  live on the exchange, so they fire even with your machine off.
- `protect_position`, retrofit native TP/SL onto any open position
- `open_funding_position` / `close_funding_position`, delta-neutral funding
  carry (spot buy + perp short) executed atomically as a batch
- `plan_oi_hedge`, sizes an OI-farming position with its cross-exchange hedge,
  fee and funding math included
- `open_pacifica_leg`, `check_position`, `account_status`

**Print** (experimental, uses an endpoint Pacifica has not documented; may
change without notice)
- `print_quote`, live premium, implied volatility and liquidation price
- `print_order` / `print_status` / `print_close`
- `evaluate_print`, statistical verdict on whether a Print offer is worth it:
  fill probability, average overshoot, and the breakeven APY that would
  compensate for it

---

## Autonomous trading entity

A self-directed trader governed by `policy.yaml`, a delegation contract. It
cannot act outside those bounds.

This is a **separate always-on process**, not an MCP tool. An MCP server only
runs when your AI client calls it; a trader that must hold positions and manage
stops around the clock needs its own process. Start it deliberately, and it
keeps running whether or not any AI is connected.

```bash
python -m ocean_agent.autonomous --init    # create policy.yaml to edit
python -m ocean_agent.autonomous --dry     # decide, but place no orders
python -m ocean_agent.autonomous           # run continuously
python -m ocean_agent.autonomous --once    # single cycle
python -m ocean_agent.autonomous --report  # performance summary
```

Read `policy.yaml` before the first real run, capital, leverage cap, risk per
trade and the hard-stop threshold all live there. Start with `--dry`.

Each cycle it reads the market, grades what it learned, manages open positions,
and enters only setups that clear every gate.

**Portfolio buckets**, capital split across directional trading, funding carry
and a cash reserve, rebalanced every cycle.

**Position aftercare**, moves the stop to breakeven once a trade is ahead,
trails it as profit grows, and takes partial profit at target. Stops only ever
move in your favour.

**Liquidity gate**, skips markets where your own order would be a large share
of daily volume. Thin books are the real hazard: an order that only partly
fills, and a stop that cannot be executed at its price.

**Net-exposure limit**, caps how one-directional the book can get, so a single
market reversal cannot hit every position at once.

**Self-remeasurement**, this is the actual learning engine. On a schedule the
bot re-measures the full matrix of coins × timeframes × signals and updates
which timeframes it trades and which signals it trusts. Regimes change: in one
measurement the 8h timeframe showed no edge at all; weeks later it was the
best-performing band. Fixed parameters go stale, so they are not fixed.

```bash
python -m ocean_agent.rematrix          # remeasure now
python -m ocean_agent.rematrix --show   # what it currently believes
```

**Adaptation**, signals that lose in live grading are suspended, size is cut
during drawdown and restored on recovery. Parameters adapt within policy bounds;
the bot never rewrites its own code.

**Final stop**, a single hard halt at catastrophic loss. Otherwise it does not
stop, it adapts.

---

## Setup

**One command**, installs everything (uv, Python, dependencies) and registers
the server with Claude Desktop. It asks two questions: wallet address and agent
key.

```bash
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://oceanagent.fi/install.ps1 | iex"

# macOS / Linux
sh -c "$(curl -LsSf https://oceanagent.fi/install.sh)"
```

Or set up manually:

1. Install [uv](https://docs.astral.sh/uv/):

```bash
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

2. Create an **agent wallet key** at [app.pacifica.fi/apikey](https://app.pacifica.fi/apikey).
   Ocean Agent uses the key to sign orders and never withdraws; you can revoke it
   at any time.

3. Put it in `.env`:

```ini
ADDRESS=your_main_wallet_address
PACIFICA_API_KEY=your_agent_wallet_key
PACIFICA_BASE_URL=https://api.pacifica.fi
```

As written this trades **live on mainnet with real funds**. Start with `--dry`,
which decides but places no orders. A testnet endpoint is also available if you
prefer to rehearse there: remove the `PACIFICA_BASE_URL` line (testnet uses
separate keys: `ADDRESS_TESTNET`, `PACIFICA_API_KEY_TESTNET`).

4. Point your MCP client at it:

```json
{
  "mcpServers": {
    "ocean-agent": {
      "command": "uvx",
      "args": ["ocean-agent@0.4.62"],
      "env": { "PACIFICA_ENV_FILE": "/absolute/path/to/.env" }
    }
  }
}
```

Restart your AI client, the first launch downloads everything automatically.

5. Check your setup:

```bash
uv run --with ocean-agent python -m ocean_agent.doctor
```

Any `python -m ocean_agent...` command in this README runs the same way,
prepend `uv run --with ocean-agent`.

---

## Safety

- This software has no withdrawal function; the key's own permissions are set by the exchange
- Every order tool previews first and executes only on explicit confirmation
- Testnet and mainnet keys are kept separate
- The autonomous entity acts only within `policy.yaml`

## Risk

This is trading software. Leveraged perpetual futures can lose more than the
margin you post. Measured win rates come from historical data and are
regime-dependent, an edge that held for months can vanish when the market
changes character. Nothing here is financial advice. Run it with `--dry` until
you understand exactly what it does, and only risk what you can afford to lose.

## License

Business Source License 1.1 (BUSL-1.1): the source is fully visible; trading with your own accounts, research and
non-commercial use are free; offering a competing commercial trading service is not. Converts automatically to MIT
on 2030-08-25.
