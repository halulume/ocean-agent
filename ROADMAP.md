# ocean_agent, Long-Term Hardening & Growth Roadmap

You are a senior quant engineer working on **ocean_agent**, a production MCP trading
server for Pacifica. This document is your standing plan. Follow it across sessions.

## Read this first, every time

This is a **long-term plan (12+ months)**, not a one-shot task. The goal is to take an
already-strong system to top-tier by **fixing correctness, tightening risk, and then
growing capability ONLY as live track record justifies it**, never by piling on
features against backtest assumptions.

The strategy logic is already strong and must be **preserved, not rebuilt**:
- baseline-subtracted edge (signal_scanner.py: `base_up`, `pwin - base < MIN_EDGE`)
- look-ahead prevention (fetch_closes drops the unclosed candle)
- liquidity gate (evaluate_setups: position size vs 24h volume, HYPE lesson)
- EV with fees, self-remeasurement (rematrix), prediction logging + review
- safety: Ed25519 agent-key signing, confirmation gates,
  testnet/mainnet separation, policy.yaml enforcement

**Core principle:** every new layer is *additive* and *measured against the raw layer*.
If a new layer can't be shown to beat the existing logic on live data, it doesn't ship.

## How to run this plan

1. On each session, first **detect the current phase**. Read the codebase and any
   progress markers (a `ROADMAP_STATUS.md` you maintain, plus live data files:
   predictions.json, equity log, regime history). State which phase we're in and why.
2. Do **not** skip ahead. Each phase has an entry gate. If the gate isn't met
   (e.g. not enough live samples), say so and either stay in the current phase or
   recommend waiting, do not start the next phase to look productive.
3. **Before writing any code, map the codebase.** List every module, its public
   functions/classes, and how they connect (api_client, signing, position, scanner,
   signal_scanner, backtest, rematrix, observer, autonomous, mcp_server, etc.). Identify
   the existing names, data shapes, and conventions you must match. Do this once per
   session before touching anything.
4. **One change at a time.** Make a single fix, show the diff, and stop for me to test
   before the next one. Never batch multiple edits blindly.
5. For every change: show a diff, explain it, confirm it doesn't break imports, function
   signatures, or the autonomous loop, and preserve all existing safety. Prefer additive
   changes (new file/function) over modifying working code. When you must modify existing
   code, touch the minimum lines and keep the existing signature/return shape.
6. **If a change conflicts with existing code, an import breaks, a signature no longer
   matches a caller, a data shape differs, STOP. Do not force it or refactor around it.
   Report the conflict, show both sides, and ask how to proceed.** A rejected edit or an
   unexpected structure is information, not a blocker to code around.
7. After each phase, update `ROADMAP_STATUS.md` with what shipped, current live sample
   counts, and the gate status for the next phase.

---

## PHASE 0, Correctness & risk hardening (do first, ~1 week)

Entry gate: always valid. This is the starting point.

Fix these, in order, diff-before-apply:

**BUG 1, position.py `close_delta_neutral` (~line 97-106)**
The open path rolls back on a failed second leg; close does not. If perp close succeeds
and spot sell fails, it raises with no handling, leaving an unhedged (naked) position.
Add symmetry: on spot-sell failure, retry once, then alert loudly and record the naked
position to state, never silently raise and leave exposure untracked.

**BUG 2, api_client.py (~line 84 and ~line 177)**
`body.get("success", True)` treats a missing `success` key as success. A malformed or
unexpected response could be read as a filled order. Default to `False` and handle the
ambiguous case explicitly (treat unknown as failure, log the raw body).

**RISK 1, autonomous.py (~line 156) + policy.yaml**
`fatal_loss_pct` is 0.50 (too deep for a top-tier <15% drawdown target).
- Lower full-stop to **0.20**.
- Add an intermediate **soft-halt at -8%** from day-start equity: pause NEW entries for
  the rest of the UTC day, but keep managing open positions. Full stop stays at -20%.

**RISK 2, signal_scanner.py (~line 331)**
Live winrate blend `w = min(ln/40, 0.6)` weights 40 live samples up to 60%, too thin,
overfits during regime shifts. Change denominator to `ln/100` and cap at **0.4** until
live track record is proven.

Exit gate to Phase 1: all four fixed and verified, AND the bot is running small live on
mainnet accumulating a real track record. Do not add features in Phase 0.

---

## PHASE 1, Regime awareness + live accumulation (~1-2 months)

Entry gate: Phase 0 complete, bot live on mainnet (small size).

Create `ocean_agent/core/regime_detector.py`, exposed as MCP tool
`detect_market_regime`. Reuse existing data (scanner, market_context.fear_greed,
indicators), no duplicate fetching.

- Classify: Bull, Bear, Choppy, High_Vol, Low_Vol, Trending, Mean_Reverting
- Inputs: funding, price vs MA200, realized vol, volume, Fear & Greed
- Output: regime, confidence, top factors, recommended posture
- Persist regime history to JSON for later meta-learning
- Integrate as an **additive flag, not a hard block**: alongside `_matrix_rejects`, add
  an optional regime layer that flags signals whose historical EV flips sign in the
  current regime. Log BOTH raw and regime-adjusted recommendations so we can later
  measure whether the regime layer actually helped.

Do not touch existing EV/baseline/liquidity logic. Keep collecting live data.

Exit gate to Phase 2: **100+ closed live trades** AND meaningful regime history logged.
If not met, stay here and keep accumulating.

---

## PHASE 2, Self-improvement, validated on live data (~3-6 months)

Entry gate: 100+ closed live trades + regime history. **State the actual counts back to
me before starting. If short, recommend waiting, do not proceed.**

**meta_learner.py**
- Cluster losing *live* trades by signal, regime, timeframe, symbol
- Surface patterns ("signal X in regime Y loses") as SUGGESTIONS for policy.yaml,
  never auto-applied. Reject any suggestion citing < 50 live samples.

**conviction fusion**
- Fuse backtest EV + live winrate + regime-conditioned winrate into one score
- Weight live data higher as it grows, but cap backtest influence, goal is
  live-proven edge. If a fused score relies on < 50 live samples, mark low-confidence.

Everything additive; existing signal logic stays intact and measurable.

Exit gate to Phase 3: **6+ months live track record** and Phase 2 layers shown to beat
the raw layer on live data. If they don't beat it, keep the raw layer and report that
honestly.


---

### PHASE 2b, Remote MCP so any LLM can run it (parallel track)

Goal, stated by the operator on 2026-08-21: **the exact setup used today, usable from
ChatGPT, Gemini, Grok and Claude on the web, with no install.** One URL, log in, done.
PayBox (`https://api.paybox.sh/mcp` + OAuth) is the shape to copy.

The code is closer to this than it looks. Measured 2026-08-21:

```
mcp_server.py:244   FastMCP(...)            transport is one argument away
mcp_server.py:247   def _client()           ONE factory, 24 of the 30 tools call it
mcp_server.py:1820  mcp.run()               stdio today, streamable-http supported
16 home-directory singletons + 2 outputs dirs
bracket_trader       one process == one account
```

Because every account-touching tool goes through `_client()`, injecting a per-request
user there carries all 24 along. The tool bodies do not change.

**Step 1, read-only remote (no gate, do whenever)**

Switch transport, expose only the tools that need no key: `analyze_chart`,
`market_context`, `top_setups`, `daily_picks`, `learned_winrates`, `scan_funding`,
`evaluate_print`. No accounts, no OAuth, no custody, no licensing surface. The matrix,
the seed, the walk-forward curve and the 13 GB Binance cache are *shared* resources, so
multi-tenancy is free here. This is also where people meet the product.

**Step 2, per-user context (gate: the two verifications below)**

Session-scoped credentials injected into `_client()`. Split the 16 singletons.

**Shared, and this is the real saving.** Matrix, seed, walk-forward curve and the Binance
cache. Every install today runs its own `rematrix` (43 symbols x 8 timeframes, ~1.2 hours
even with a warm cache) and keeps its own 13 GB of bars. A hundred installs means that
computation a hundred times and that download a hundred times, for a result that is
supposed to be identical. Hosted, it happens once.

**Per user.** `.env`, `bracket_state.json`, heartbeat, lock, `warned`, the consent marker.

**The seal is neither, and it is a decision, not a saving.** Seals are stamped at the
moment the loop wakes (04:14, 05:17, 06:20 on 08-21, roughly 63 minutes apart), not on an
hour boundary, so two installs already produce different picks from different market
snapshots. Serving one seal to everyone is a *change in behaviour*: it makes every user
trade the same symbols in the same second, which is exactly the aggregate-slippage
problem below. Cheaper, and worse, unless that problem is solved first.

**Step 3, hosted auto-trading (gate: PHASE 2 entry gate + the two verifications)**

`bracket_trader` is rewritten as one worker iterating over N users. Whether the seal is
computed once for everyone or per user on their own clock is the decision above, and it
has to be settled before this step, not during it. Watch out for the exchange rate limit,
which today is sized for one account's worth of calls.

**Two verifications that must land before Step 2. Neither is code.**

1. **Can an API agent key withdraw?** We assert it cannot, in `DISCLAIMER.md:51`,
   `DISCLAIMER.ko.md:45`, `README.md:162`, this file, and, worst of all, in
   `connect_ui.py:85` and `install_ui.py:196`, which are the screens shown at the moment
   the user hands the key over. **That claim is ours alone.** Checked on 2026-08-21:
   Pacifica's docs state no such restriction, the withdrawal endpoint
   (`request-withdrawal`) accepts an optional `agent_wallet` field, and the official SDK
   `rest/api_agent_keys.py` documents only binding and order placement. The claim traces
   back to at least the 07-27 snapshot with no cited source.
   **Verify on testnet or ask Pacifica. Until then treat the claim as unproven, and do
   not build a custody story on it.** If it turns out an agent key can withdraw, the
   six places above are misstatements at the point of consent and must be corrected
   before anything else here proceeds.

2. **Does subaccount isolation work?** Pacifica has `create-subaccount` and
   `subaccount-fund-transfer`. If an agent key can be scoped to a subaccount, a user can
   fund only their trading budget there and the blast radius stops at that balance. That
   is PayBox's "amount-capped" property, implemented with a native exchange feature
   instead of MPC. Check whether keys bind per subaccount or to the whole account.

**Aggregate slippage, a problem this track creates**

Today every user runs their own bot on their own clock, so entries scatter by minutes and
by market snapshot. Hosted with a shared seal, N users hit the same symbol in the same
second on a small venue. The slippage gate
(`seal_maker`) measures **one order's** impact; nothing measures the sum. Before Step 3,
measure Pacifica book depth against total notional across the expected user count, then
add per-symbol notional caps and entry jitter.

**Legal**

Discretionary trading on someone else's account. This cannot be resolved by engineering.
Get an opinion before Step 3, not after, or the whole build may be unusable.

**What this track deletes**

Distribution stops being a source of bugs. The 2026-08-21 review found the running bot
was on 08-20 code, the forecast loop on 08-18 code, and the published wheel on a third
version, all at once. Hosted, that class of failure cannot occur. Version pins in eight
places, MCPB, the two install scripts, and half of `release/preflight.py` all go away.

---

## PHASE 3, Institutional-grade validation (~6-12 months)

Entry gate: 6-12 months live track record.

- **Monte Carlo stress test:** resample live trade history for drawdown distribution,
  ruin probability, Sharpe confidence intervals. Report worst-case tails honestly.
- **Cross-asset correlation & portfolio hedging:** stop stacking correlated directional
  bets (BTC long + ETH long = one bet). Portfolio-level exposure cap.
- **Independent validation:** a separate script recomputing reported performance from
  raw equity/trade logs, so claimed metrics can't drift from reality.

The bar: prove the edge held up live, out-of-sample, across regimes. If the data shows
the edge decayed, report it honestly rather than tuning to hide it.

---

## What NOT to do (all phases)

- Don't rebuild the proven strategy core to "improve" it.
- Don't add meta-learner / conviction fusion / Monte Carlo before the live-data gates.
- Don't auto-apply learned suggestions, always human-in-the-loop for policy changes.
- Don't bypass agent-key signing, confirmation gates, or policy.yaml.
- Don't present backtest performance as if it were live performance.
- Don't skip a phase gate to appear productive. Waiting for live data IS the work.

## The honest truth to keep restating

Code gets this system to roughly top 5-10%. The jump to top ~1% is **live track record
holding up across regimes**, that's time, not more code. This plan front-loads
correctness and risk, then grows capability only as live evidence earns it.
