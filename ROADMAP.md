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
- safety: Ed25519 agent-key signing (no withdrawal rights), confirmation gates,
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
