# -*- coding: utf-8 -*-
"""The bracket trader: the measured operating plan, and nothing else.

Born 2026-08-11 from 430 measured variants, of which three survived and are
wired here verbatim:
  · trade the top three picks (by trade_rank) of the daily seal
  · exit at TP 0.3x / SL 1.0x of the pick's expected move, or the 24h expiry
  · leverage capped by policy (week one runs reduced), isolated margin

Everything else that was measured and rejected is deliberately absent: no
intraday refills (ranks 7-12 net negative), no swaps, no reserve slots, no
12h re-look (dilutes per-trade), no trailing, no partial TP.

Safety posture:
  · TP/SL ride on the exchange with the entry order, so a dead bot or PC
    cannot orphan a position without its lines.
  · The pre-registered breakers (liquidation, a bad running average, stops
    filling past their line) warn loudly instead of halting: 08-19 showed
    a halt is a silence, not a brake, because it waits for a console
    nobody reads while the account's real protection sits on the exchange.
  · Dry runs touch NOTHING on disk (review 5, BR1/BR4: a dry run once burned
    the day's seal and could even flip the halt flag; now it only logs).
  · Grading of closed trades reads the exchange's own realized pnl and cause,
    not a mark-price guess (BR3/BR11); the guess remains only as a fallback
    and errs to the stop, never the target.
  · The old EV bot (autonomous.py) is untouched; never run both at once. On
    entry, any symbol that already has a live exchange position - whoever
    opened it - is skipped (BR2).

Modes (2026-08-12): one account, two measured plans.
  · base - the validated plan (~80% TP-first-touch, ~60% profitable days)
  · hard - fixed ~3% target, wider swings (~56% win rate, larger total),
    NOT forward-validated yet; its settings live commented in
    policy_default.yaml
Each mode keeps its own state/record file so results never mix, and a live
run publishes a heartbeat so the other mode refuses to start next to it -
the same reasoning as the X1 guard against the old EV bot.

Run:  python -m ocean_agent.bracket_trader [--once] [--dry] [--status]
                                           [--resume] [--close-all]
"""
import argparse
import datetime as dt
import glob
import json
import os
import sys
import time

from .api_client import PacificaError
from .autonomous import load_policy, make_client, log
from .position import _round_down_to_lot, _round_to_tick
from . import data_file, notify

KST = dt.timezone(dt.timedelta(hours=9))
# All safety-critical files live under the project root next to the package,
# never under whatever folder the process happened to start in. A cwd-relative
# path here silently disabled the cross-bot guards when the bot was launched
# from another directory. (review N1)
OUTPUTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outputs")
# Mode-dependent paths. Base keeps the historical file name so a run already
# in progress is never orphaned; use_mode() repoints both at startup.
MODE = "base"
MODES = ("base", "hard")
STATE_PATH = os.path.join(OUTPUTS_DIR, "bracket_state.json")
HEARTBEAT_PATH = os.path.join(OUTPUTS_DIR, "bracket_alive_base.json")
HEARTBEAT_FRESH_MIN = 10            # same window as the X1 guard (old EV bot)
LOOP_MIN = 30

# ── pre-registered circuit breakers (round-3 review, 2026-08-11) ──
HALT_ON_LIQUIDATION = True          # exchange-confirmed liquidation = warn
HALT_AVG_AFTER = 30                 # trades before the average is judged
# Floor for the 1.5x TP geometry (same derivation as before, re-evaluated
# 2026-08-19 when the target widened): wins near +4.4% and stops near
# -3.0% put the per-trade std around 3.7%, so a 30-trade average two
# sigmas below a roughly breakeven expectation is 0 - 2 * 3.7 / sqrt(30)
# ~= -1.4. Re-derive from the live sample once 30 real trades are booked.
HALT_AVG_FLOOR = -1.4               # % per trade; -2 sigma of the expectation
DEMOTE_SLIP_EVENTS = 2              # stop fills worse than line by ...
DEMOTE_SLIP_PCT = 0.5               # ... this many %p, twice -> warn.
# Not "lower the leverage" any more: with a fixed notional per pick that
# moves the margin posted, not the loss a slipped stop takes. Thin books
# are the cause, so the answer is a smaller notional or bracket_skip_syms.

# Seals a dry run has already logged, per process. Deliberately NOT persisted:
# dry must leave no trace in the state file (BR1), this only stops log spam.
_dry_logged: set[str] = set()

# When an unconsumed seal stays open after a failed entry pass (M16), the
# 30-second seal poll must not become a hot retry loop: one 429 burst would
# re-run the whole entry pass every poll, thousands of requests an hour,
# each failure feeding the next. Entry attempts that leave the seal open
# arm this cooldown; the seal wakeup honors it. (review P4)
_retry_not_before: float = 0.0
ENTRY_RETRY_COOLDOWN_SEC = 300
# How long a warning key stays quiet after ringing (2026-08-21 user choice).
# The two ends were both wrong: comparing message text re-sent every cycle
# because the text carries a running average, and suppressing on the key
# alone silenced it forever, since nothing clears `warned`.
WARN_COOLDOWN_SEC = 6 * 3600
# Taker round trip, in percentage points of notional. Measured on the venue's
# own history 2026-08-21: an opening row and a closing row each carry about
# 0.04% of notional as a negative pnl. 작업규칙 §11 records 0.07%, which is
# one side short of what the exchange actually charges.
FEE_RT_PCT = 0.08


def _now():
    return dt.datetime.now(KST)


def bracket_mode(policy: dict) -> str:
    """Which measured plan this process runs: 'base' or 'hard'.

    An unknown name falls back to base rather than inventing a third set of
    files that nothing else would ever read again.
    """
    m = str(policy.get("bracket_mode", "base")).strip().lower()
    if m not in MODES:
        log(f"알 수 없는 모드 '{m}', 베이스 모드로 실행합니다")
        return "base"
    return m


def use_mode(mode: str, dry: bool = False) -> None:
    """Point the state and heartbeat files at this mode's own files.

    Base and hard trade different plans, so their positions and closed
    records must never share one file. Base keeps the original path.

    A dry run is given its own names on top of that. Today it writes nothing
    at all (BR1), so the separation costs nothing; the day someone lets a dry
    run keep a ledger, it will land beside the live one instead of on top of
    it.
    """
    global MODE, STATE_PATH, HEARTBEAT_PATH
    MODE = mode
    suffix = "" if mode == "base" else f"_{mode}"
    if dry:
        suffix += "_dry"
    STATE_PATH = os.path.join(OUTPUTS_DIR, f"bracket_state{suffix}.json")
    HEARTBEAT_PATH = heartbeat_path(mode, dry)


def heartbeat_path(mode: str, dry: bool = False) -> str:
    return os.path.join(OUTPUTS_DIR,
                        f"bracket_alive_{mode}{'_dry' if dry else ''}.json")


def write_heartbeat() -> None:
    """Publish "this mode is alive" for the mutual lock.

    Live runs only: a dry run must touch nothing on disk (BR1), and it holds
    no margin, so it needs no claim on the account either.
    """
    os.makedirs(OUTPUTS_DIR, exist_ok=True)
    tmp = HEARTBEAT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"mode": MODE, "pid": os.getpid(),
                   "at": _now().isoformat()}, f, ensure_ascii=False)
    os.replace(tmp, HEARTBEAT_PATH)


def clear_heartbeat() -> None:
    """Release the claim on a clean exit; a crash lets it expire instead."""
    try:
        os.remove(HEARTBEAT_PATH)
    except OSError:
        pass


def live_bracket() -> tuple[str, float] | None:
    """A bracket instance still holding the account: (mode, minutes ago).

    One account means one margin pool, so two bracket instances must never
    run at once - the same reason the X1 guard refuses to start beside the
    old EV bot. Own-mode files count too: a second copy of the same mode is
    just as bad. Freshness (not a pid) is the test, so a killed process
    releases the account by itself.
    """
    for other in MODES:
        path = heartbeat_path(other)
        if not os.path.exists(path):
            continue
        age_min = (time.time() - os.path.getmtime(path)) / 60
        if age_min < HEARTBEAT_FRESH_MIN:
            return other, age_min
    return None


def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"positions": {}, "closed": [], "entered_seals": [],
            "halted": False, "halt_reason": "", "slip_events": 0}


def save_state(st: dict) -> None:
    """Write the ledger, keeping any closes another process booked.

    The whole file is rewritten from memory, so two bots (or a bot and a
    repair) overwrite each other's closed trades: on 08-19 a bot started
    at 21:59 saved at 22:33 and erased three closes written in between.
    Closed records only ever get appended, so the union of what is on
    disk and what is in memory is the truth.
    """
    os.makedirs(OUTPUTS_DIR, exist_ok=True)
    try:
        with open(STATE_PATH, encoding='utf-8') as f:
            on_disk = json.load(f).get('closed', [])
    except (OSError, ValueError):
        on_disk = []
    # keyed on the entry, not on the writer's clock: two processes grading
    # the same close stamp different closed_at values and would each keep a row
    def key_of(c):
        return (c.get('sym'), c.get('opened_at') or c.get('closed_at'))

    if on_disk:
        seen = {key_of(c) for c in st['closed']}
        extra = [c for c in on_disk if key_of(c) not in seen]
        if extra:
            st['closed'] = sorted(st['closed'] + extra,
                                  key=lambda c: c.get('closed_at', ''))
            log(f'다른 프로세스가 기록한 청산 {len(extra)}건을 합칩니다')
    # The same key twice inside one list is the same trade booked twice: a
    # flatten that wrote no sym, or a repair run against a live bot. The
    # merge above already treats the key as the identity of an entry, so
    # apply it here too rather than letting the average count it twice.
    once, dedup = set(), []
    for c in st['closed']:
        k = key_of(c)
        if k in once:
            log(f'같은 청산이 두 번 기록돼 하나로 합칩니다: {k[0] or "?"}')
            continue
        once.add(k)
        dedup.append(c)
    st['closed'] = dedup
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_PATH)


def bracket_cfg(policy: dict) -> dict:
    """Bracket settings, read from policy.yaml with the measured defaults.

    Week one deliberately runs leverage 3 (execution shakedown); raise
    bracket_leverage to 8 in policy.yaml only after the forward gates pass.
    """
    return {
        "slots": int(policy.get("bracket_slots", 3)),
        "leverage": int(policy.get("bracket_leverage", 3)),
        "deploy_pct": float(policy.get("bracket_deploy_pct", 0.97)),
        # share of spendable margin the user lets the bot use; 100 = all of
        # it, exactly as before this key existed
        "capital_pct": max(0.0, min(100.0,
                                    float(policy.get("bracket_capital_pct", 100)))),
        "tp_mult": float(policy.get("bracket_tp_mult", 0.3)),
        # fixed TP distance in % (hard mode); 0 keeps the measured multiple
        "tp_pct": max(0.0, float(policy.get("bracket_tp_pct", 0))),
        "sl_mult": float(policy.get("bracket_sl_mult", 1.0)),
        "vol_floor_pct": float(policy.get("bracket_vol_floor_pct", 0.8)),
        # What the operator said they wanted working, in dollars. Slots and
        # per-pick size come out of it: $300 at $50 a pick is six positions.
        # Percent-of-account is what a spreadsheet understands; a person
        # answering "how much do you want to trade with" says a number.
        "budget_usd": max(0.0, float(policy.get("bracket_budget_usd", 0))),
        # symbols the operator wants left alone, whatever the seal says.
        # Empty by default: this is a manual veto, not a measured rule.
        "skip_syms": {str(s).upper()
                      for s in (policy.get("bracket_skip_syms") or [])},
        "horizon_h": int(policy.get("bracket_horizon_h", 24)),
        # fixed per-pick notional in USD; 0 keeps proportional sizing.
        # Added 2026-08-14 (user): their own account trades a fixed $30 a
        # pick while the shipped default stays proportional to the account.
        "notional_usd": max(0.0, float(policy.get("bracket_notional_usd", 0))),
    }


def tp_distance_pct(cfg: dict, exp_move_pct: float) -> float:
    """Target distance in %, the one place both modes agree on.

    Base multiplies the pick's expected move (0.3x); hard fixes the target
    near 3% regardless of the pick. Grading's fallback estimate must use the
    same number as the order, so both call this.
    """
    if cfg["tp_pct"] > 0:
        return cfg["tp_pct"]
    return cfg["tp_mult"] * exp_move_pct


def latest_seal() -> dict | None:
    """The newest seal from the CURRENT pick pipeline only.

    The legacy forecast process also writes 내일예측 files with trade ranks;
    its picks come from a different, rejected selection rule, so the bot
    must never consume them. The current sealer stamps a rule string that
    starts with the class-score marker, and every pick carries slip_pct.
    """
    files = sorted(glob.glob(os.path.join(OUTPUTS_DIR, "내일예측_*.json")))
    for path in reversed(files):
        try:
            with open(path, encoding="utf-8") as f:
                rec = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if (str(rec.get("rule", "")).startswith("자산군")
                and any("trade_rank" in p for p in rec.get("picks", []))):
            rec["_path"] = path
            return rec
    return None


def seal_key(rec: dict) -> str:
    """Content-based identity (BR10): renaming or moving the file must not
    make a seal enterable twice."""
    return str(rec.get("made_at", ""))


def select_picks(rec: dict, cfg: dict) -> list[dict]:
    """Top slots by trade_rank; a pick under the vol floor is skipped and the
    next rank takes its seat (the floor is cost-derived: TP must clear 3x the
    round-trip fee)."""
    picks = sorted(rec.get("picks", []), key=lambda p: p.get("trade_rank", 9))
    out = []
    for p in picks:
        if p["sym"].upper() in cfg.get("skip_syms", ()):
            log(f"제외 목록이라 건너뜀: {p['sym']} (bracket_skip_syms)")
            continue
        if p.get("exp_move_pct", 0) < cfg["vol_floor_pct"]:
            log(f"변동폭 하한 미달로 건너뜀: {p['sym']} "
                f"({p.get('exp_move_pct')}% < {cfg['vol_floor_pct']}%)")
            continue
        out.append(p)
        if len(out) == cfg["slots"]:
            break
    return out


def bracket_prices(px: float, direction: str, mv: float, cfg: dict,
                   tick: float) -> tuple[str, str] | None:
    """Tick-rounded TP/SL, validated to sit on the correct sides of entry.

    With a tight target (0.3x of a small expected move) and a coarse tick,
    rounding can collapse the TP onto the entry price or past it (BR8). A
    bracket that fails validation is a reason to skip the pick, not to enter
    it naked.

    Deliberate: the bracket is anchored to the LIVE price at entry (px), not
    to the seal's own entry price. The fill happens minutes to hours after
    the seal was made; anchoring to the stale seal price could place the TP
    on a level the market has already passed (instant fee-only exit) or push
    the SL further than its measured distance. The seal contributes the
    direction and expected move only; the geometry is recomputed from what
    was actually paid. Do not "fix" this by copying seal prices.
    """
    long_ = direction == "long"
    tp_d = tp_distance_pct(cfg, mv * 100) / 100
    tp = px * (1 + tp_d) if long_ else px * (1 - tp_d)
    sl = px * (1 - cfg["sl_mult"] * mv) if long_ else px * (1 + cfg["sl_mult"] * mv)
    tp_s, sl_s = _round_to_tick(tp, tick), _round_to_tick(sl, tick)
    tp_f, sl_f = float(tp_s), float(sl_s)
    ok = (sl_f < px < tp_f) if long_ else (tp_f < px < sl_f)
    if not ok:
        return None
    return tp_s, sl_s


def _recorded_distances(pos: dict, cfg: dict) -> tuple[float, float]:
    """This position's own take-profit and stop distances, in percent.

    Read off the prices the orders were actually placed at, not recomputed
    from today's multipliers. §100 made the same point about the ledger: on a
    day the geometry changes, the book holds two generations and reading one
    with the other's ruler is wrong. The code had the same bug. It matters for
    the slippage breaker, which asks whether a stop filled worse than its own
    distance, and for the estimated-PnL cap.

    Tick rounding alone already moves these: SKHYNIX opened at 1.5x/1.0x with
    an expected move of 3.98% and sits at 6.002% and 3.955%, not 5.970% and
    3.980%. Across a geometry change the gap is the whole change.

    Falls back to the config when a position predates the recorded prices or
    carries an unusable entry.
    """
    entry = pos.get("entry_fill") or pos.get("entry_intent") or 0
    tp, sl = pos.get("tp"), pos.get("sl")
    try:
        if entry > 0 and tp and sl:
            tp_d = abs(float(tp) / entry - 1.0) * 100
            sl_d = abs(float(sl) / entry - 1.0) * 100
            if tp_d > 0 and sl_d > 0:
                return tp_d, sl_d
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    exp = pos.get("exp_move_pct") or 0
    if exp > 0:
        return tp_distance_pct(cfg, exp), cfg["sl_mult"] * exp
    # Nothing to measure against, so say so instead of answering (0, 0). The
    # caller caps its estimate at min(move, tp_d) and max(move, -sl_d), so
    # zeros would book every close, win or loss, as exactly 0.00%p and make
    # the slippage counter read every stop as slipped. No position on file
    # lacks these fields; this is a guard, not a path.
    return None, None


def _close_row(sym: str, pos: dict, est, cause: str) -> dict:
    """One shape for every booked close, carrying its own geometry.

    The book used to keep eight fields, none of them the bracket the trade
    actually wore, so a row could not be re-read later: -3.155% might be a
    stop hit cleanly or a stop slipped through, and nothing on the row said
    which. §100 made this point about reading a book that spans a geometry
    change, and the analysis side was corrected while the writing side was
    not. Additive, so nothing that reads the old eight fields changes.
    """
    row = {"sym": sym, "dir": pos.get("dir", ""),
           "pnl_pct_est": est, "cause": cause,
           "opened_at": pos.get("opened_at"),
           "closed_at": _now().isoformat(),
           "trade_rank": pos.get("trade_rank"),
           "basis": pos.get("basis")}
    for k in ("tp", "sl", "entry_fill", "amount", "exp_move_pct", "leverage"):
        v = pos.get(k)
        if v is not None:
            row[k] = v
    return row


def _settings_confirmed(client, sym: str, lev: int) -> bool:
    """Does the exchange itself say this market is isolated at `lev`?

    Called only after update_margin_mode/update_leverage failed, to separate
    "already set" (fine) from 429/signature/network errors (must never enter
    on cross margin or at a stale leverage). The settings payload is a dict
    whose per-symbol rows sit under "margin_settings", each row carrying
    "symbol", "isolated" (bool) and "leverage" (int). Anything unreadable
    counts as not confirmed: fail closed, but say WHY, so a silently skipped
    symbol can be diagnosed from the log. (review H2, N2)
    """
    try:
        resp = client.get_account_settings()
    except PacificaError as e:
        log(f"⚠️ {sym}: 계좌 설정 조회 실패({str(e)[:80]}), 확인 불가로 "
            f"진입 거부")
        return False
    rows = resp.get("margin_settings") if isinstance(resp, dict) else resp
    if not isinstance(rows, list):
        log(f"⚠️ {sym}: 계좌 설정 응답에 margin_settings 목록이 없음 "
            f"(응답 형식 {type(resp).__name__}), 확인 불가로 진입 거부")
        return False
    for s in rows:
        if not isinstance(s, dict) or s.get("symbol") != sym:
            continue
        iso, lev_raw = s.get("isolated"), s.get("leverage")
        if iso is None or lev_raw is None:
            log(f"⚠️ {sym}: margin_settings 행에 isolated/leverage 키가 없음 "
                f"(행 키: {sorted(s.keys())}), 확인 불가로 진입 거부")
            return False
        try:
            cur_lev = int(float(lev_raw))
        except (TypeError, ValueError):
            log(f"⚠️ {sym}: leverage 값 해석 불가({lev_raw!r}), 확인 불가로 "
                f"진입 거부")
            return False
        if not (bool(iso) and cur_lev == lev):
            log(f"⚠️ {sym}: 거래소 설정 불일치 (isolated={iso}, "
                f"leverage={cur_lev}, 요구 격리 {lev}배), 진입 거부")
            return False
        return True
    log(f"⚠️ {sym}: margin_settings 에 해당 종목 행이 없어 확인 불가, "
        f"진입 거부")
    return False


# One raw order row is logged per process when open orders exist but none
# matched the order_type values this code expects, so the real field names
# can be read from the log without spamming every loop. (review N2)
_raw_order_logged = False


def _log_raw_order_once(sym: str, orders: list) -> None:
    global _raw_order_logged
    if _raw_order_logged or not orders:
        return
    _raw_order_logged = True
    try:
        log(f"주문 필드 확인용 원본 1건 ({sym}): "
            + json.dumps(orders[0], ensure_ascii=False)[:500])
    except (TypeError, ValueError):
        pass


def _exchange_brackets(client, sym: str) -> tuple[bool, bool, list]:
    """(TP present, SL present, sym's open orders) from the exchange.

    The entry order carries take_profit/stop_loss subfields, but nothing in
    its response proves they were accepted; this asks the order book itself.
    The raw order list is returned so the caller can tell "no orders at all"
    apart from "orders exist but none matched the expected type fields":
    only the first case is safe to re-attach into, the second may already BE
    the bracket under unknown field names. (review H1, N2)
    """
    has_tp = has_sl = False
    orders = [o for o in client.get_open_orders() if o.get("symbol") == sym]
    for o in orders:
        ot = str(o.get("order_type", ""))
        if ot.startswith("take_profit"):
            has_tp = True
        elif ot.startswith("stop"):
            has_sl = True
    return has_tp, has_sl, orders


def _clear_pending(st: dict, sym: str) -> None:
    """Drop the pending-entry intent for a symbol once its outcome is booked
    (either as a position or as a completed round trip). (review H9)"""
    pend = st.get("pending_entries")
    if pend:
        st["pending_entries"] = [q for q in pend if q.get("sym") != sym]


def reconcile_pending(client, st: dict) -> None:
    """Adopt or drop entry intents that never got booked. (review H9)

    A pending record means a market order may have gone out right before a
    crash, so the exchange could hold a position no ledger remembers. If the
    exchange holds it, book it flagged (unprotected + recheck_fill) so the
    watcher re-validates the fill and the user can see it; if the exchange
    does not, the order never filled and the record is dropped. An
    unreadable exchange keeps every record for the next attempt: fail
    closed, never guess.
    """
    pend = st.get("pending_entries") or []
    if not pend:
        return
    try:
        live = {p.get("symbol"): p for p in client.get_positions()}
    except PacificaError as e:
        log(f"⚠️ 미결 진입 기록 {len(pend)}건 확인 실패({str(e)[:80]}), "
            f"다음 회차에 재시도")
        return
    keep = []
    for q in pend:
        sym = q.get("sym")
        if not sym or sym in st["positions"]:
            continue
        try:
            at = dt.datetime.fromisoformat(q.get("at"))
            age_min = (_now() - at).total_seconds() / 60
        except (TypeError, ValueError):
            age_min = 1e9
        lp = live.get(sym)
        if lp is None:
            # Absent from the exchange is ambiguous: never filled, not yet
            # visible, or filled AND already closed by its bracket. Young
            # records get a grace pass; old ones are asked about via the
            # exchange's own close history before being let go, so a trade
            # that lived and died while the bot was down still reaches
            # st["closed"] and the circuit breakers. (review P3, case B)
            if age_min < 5:
                keep.append(q)
                continue
            try:
                since = int(at.timestamp() * 1000) - 60_000
                done = realized_close(client, sym, since)
            except Exception:
                keep.append(q)      # unreadable history: retry next cycle
                continue
            if done:
                notional = float(q.get("amount") or 0) * \
                    float(q.get("entry_intent") or 0)
                _rc = done.get("cause") or ""
                if _rc in ("", "normal"):
                    # The venue labels every close "normal", so the sign of the
                    # result is a guess, and watch_positions was taught not to
                    # state a guess as fact: a hand-closed loss became a
                    # "stop_loss", which fed _stop_streak and blocked that
                    # direction for the rest of the day. This path kept the old
                    # certainty, so the hole reopened every time the bot came
                    # back from a crash. Same names as the other path, so the
                    # streak counter can tell a guess from a stop.
                    _rc = ("추정:이익" if float(done.get("pnl_usd") or 0) >= 0
                           else "추정:손실")
                st["closed"].append({
                    "sym": sym, "dir": q.get("dir"),
                    "cause": _rc,
                    "pnl_usd": done.get("pnl_usd"),
                    "pnl_pct_est": (float(done.get("pnl_usd") or 0) / notional
                                    * 100 if notional > 0 else 0.0),
                    "opened_at": q.get("at"),
                    "closed_at": _now().isoformat(),
                    "basis": q.get("basis"), "seal": q.get("seal"),
                    "recovered": True,
                })
                msg = (f"브래킷 복구: {sym} 은 봇이 꺼진 사이 이미 청산됨 "
                       f"({done.get('cause')}, "
                       f"${float(done.get('pnl_usd') or 0):+.2f}). "
                       f"장부에 기록했습니다.")
                log("⚠️ " + msg)
                notify.send("⚠️ " + msg)
            else:
                log(f"미결 진입 {sym}: 거래소 미보유 + 청산 이력 없음, "
                    f"미체결로 판단하고 기록을 버립니다")
            continue
        # Adopt only a position that matches the recorded intent: same
        # direction, roughly the ordered size. A mismatch means someone
        # else's trade (manual, another tool) and BR2 says never touch it.
        # (review P3, case C)
        want_side = "bid" if q.get("dir") == "long" else "ask"
        lp_side = str(lp.get("side") or "")
        try:
            lp_amt = abs(float(lp.get("amount") or 0))
            q_amt = float(q.get("amount") or 0)
        except (TypeError, ValueError):
            lp_amt, q_amt = 0.0, 0.0
        size_ok = q_amt > 0 and lp_amt > 0 and lp_amt <= q_amt * 1.05
        if lp_side != want_side or not size_ok:
            msg = (f"브래킷 복구 보류: {sym} 거래소 포지션이 기록과 다릅니다 "
                   f"(방향 {lp_side or '?'} vs 기대 {want_side}, 수량 "
                   f"{lp_amt} vs 주문 {q_amt}). 남의 포지션일 수 있어 "
                   f"건드리지 않습니다. 직접 확인해 주세요.")
            log("⚠️ " + msg)
            notify.send("⚠️ " + msg)
            continue
        try:
            entry = float(lp.get("entry_price") or q.get("entry_intent") or 0)
        except (TypeError, ValueError):
            entry = float(q.get("entry_intent") or 0)
        st["positions"][sym] = {
            # book what the exchange actually holds, not what we meant to
            # order: a partial fill must size the close and the pnl math
            "dir": q.get("dir"), "amount": lp_amt or q.get("amount"),
            "entry_intent": q.get("entry_intent"), "entry_fill": entry,
            "fill_confirmed": False, "tp": q.get("tp"), "sl": q.get("sl"),
            "exp_move_pct": q.get("exp_move_pct"),
            "leverage": q.get("leverage"),
            "opened_at": q.get("at") or _now().isoformat(),
            "seal": q.get("seal"), "trade_rank": q.get("trade_rank"),
            "basis": q.get("basis"),
            # bracket presence is unknown after a crash: keep it visibly
            # unprotected and let the watcher re-validate the fill once
            "unprotected": True, "recheck_fill": True,
        }
        msg = (f"브래킷 복구: 중단됐던 진입 {sym} {q.get('dir')} 을 장부에 "
               f"복원했습니다 (거래소 보유 확인). TP/SL 부착 여부는 미확인, "
               f"봇이 매 회차 감시합니다.")
        log("⚠️ " + msg)
        notify.send("⚠️ " + msg)
    st["pending_entries"] = keep
    save_state(st)


def _cool_down() -> None:
    """Hold off the seal poll after a pass that entered nothing."""
    global _retry_not_before
    _retry_not_before = time.time() + ENTRY_RETRY_COOLDOWN_SEC


def apply_budget(cfg: dict) -> dict:
    """Turn a dollar answer into slots and per-pick size.

    Asked once when auto trading starts and changeable any time, so the
    number a person gave is the number that trades: $300 with the standard
    $50 a pick is six positions, not a share of whatever the balance
    happens to be that morning.
    """
    budget = cfg.get("budget_usd", 0)
    if budget <= 0:
        return cfg
    per = cfg.get("notional_usd") or 50.0
    # A budget smaller than one pick used to still open one pick at full size:
    # $30 with a $50 notional traded $50, 167% of what was asked for. Shrink
    # the pick instead, and say so, because silently trading more than the
    # number a person typed is the one thing this function must not do.
    if budget < per:
        log(f"예산 ${budget:,.0f} 가 픽 하나({per:,.0f})보다 작아 "
            f"픽 크기를 ${budget:,.0f} 로 줄입니다")
        per = budget
    cfg["notional_usd"] = per
    cfg["slots"] = max(1, min(int(budget // per), 8))
    # capital_pct is deliberately replaced, not merged: the budget is an
    # absolute limit, so a percentage of the balance on top of it would mean
    # two different answers to "how much". policy.yaml's bracket_capital_pct
    # is therefore ignored whenever a budget is set, which was happening
    # silently before.
    if cfg.get("capital_pct", 100.0) != 100.0:
        log(f"예산이 설정돼 있어 bracket_capital_pct "
            f"{cfg['capital_pct']:.0f}% 는 쓰지 않습니다 "
            f"(예산 ${budget:,.0f} 가 상한)")
    cfg["capital_pct"] = 100.0
    return cfg


def enter_positions(client, policy, st, cfg, dry: bool) -> None:
    rec = latest_seal()
    if not rec:
        log("진입 대기: 봉인(내일예측_*.json) 이 아직 없다. 픽 생성이 만들면 "
            "다음 회차에 자동 진입한다.")
        return
    key = seal_key(rec)
    made = dt.datetime.fromisoformat(rec["made_at"])
    if key in st["entered_seals"] or (dry and key in _dry_logged):
        return
    age_h = (_now() - made).total_seconds() / 3600
    if age_h > 6:
        # a stale seal is not an entry signal; wait for tonight's fresh one.
        # dry leaves no trace (BR1): only a live run marks the seal consumed.
        log(f"봉인이 {age_h:.1f}시간 지나 진입 생략 (다음 봉인 대기)")
        if dry:
            _dry_logged.add(key)
        else:
            st["entered_seals"].append(key)
            save_state(st)
        return

    acct = client.get_account()
    # Spendable margin, not equity: equity counts unrealized pnl and does not
    # subtract margin already locked by other positions (BR2, same as the old
    # bot's S4 fix).
    funds = float(acct.get("available_to_spend") or 0)
    if funds <= 0:
        # Cooling down matters as much as the message: without it the seal
        # stays unconsumed, the 30-second poll wakes on it forever, and the
        # same telegram goes out twice a minute. 08-19 ran 388 cycles in
        # four hours that way.
        _warn_once(st, "funds", "가용 증거금을 읽지 못해 진입을 건너뜁니다.")
        _cool_down()
        return
    # only the share of that spendable margin the user allows (default all of
    # it); deploy_pct and the slot split then divide this share, so $1000 at
    # 50% sizes exactly like $500 at 100%
    funds *= cfg["capital_pct"] / 100
    picks = select_picks(rec, cfg)
    if not picks:
        # every pick filtered out (vol floor, skip list): the seal holds
        # nothing for us, so stop re-reading it every thirty seconds
        _cool_down()
        return
    # Divide by the whole plan, not by what happens to be free. With the
    # old divisor a single empty slot took the entire spendable margin:
    # seven held plus one free put 97% of the account into that one
    # entry. Anyone running the shipped defaults (no fixed notional)
    # met that on their first refill.
    margin_per = funds * cfg["deploy_pct"] / max(1, cfg["slots"])
    if cfg["notional_usd"] > 0:
        # fixed notional wins over the proportional split, but never exceeds
        # what the margin could carry anyway
        margin_per = min(margin_per,
                         cfg["notional_usd"] / max(1, cfg["leverage"]))
    mkts = {m["symbol"]: m for m in client.get_markets()}
    prices = {p["symbol"]: p for p in client.get_prices()}
    # any live exchange position blocks that symbol, whoever opened it:
    # the old bot, a manual trade, or ourselves (BR2)
    live_syms = {p.get("symbol") for p in client.get_positions()}

    opened = []
    entered_n = 0
    # Transient failures (price feed, settings, rejected order) counted so
    # the seal is only consumed below when something real happened; a pick
    # skipped deliberately (streak, slots, invalid bracket) does not count
    # as a failure. Too small to trade does count: the account may be topped
    # up within the seal's window, and burning the seal hides the reason.
    attempt_failed = 0
    too_small = 0
    held_start = len(st["positions"])
    # After two consecutive stops in the same direction, stop chasing that
    # side. A win or a direction flip clears the block (tail-counted).
    def _stop_streak(sym, direction):
        n = 0
        for rec in reversed(st["closed"]):
            if rec.get("sym") != sym:
                continue
            cause = str(rec.get("cause") or "")
            # A guessed loss is not proof the stop was hit: a position the
            # operator closed by hand at a loss used to block that direction
            # for the rest of the day (VVV, 08-19).
            if rec.get("dir") != direction or cause != "stop_loss":
                break
            n += 1
        return n

    for p in picks:
        # Eight picks mean eight order round-trips and their retries; ten
        # minutes of that used to read as a dead bot and invited a second
        # one onto the same margin.
        if not dry:
            write_heartbeat()
        if _stop_streak(p["sym"], p["dir"]) >= 2:
            log(f"{p['sym']} {p['dir']} 연속 손절 2회, 같은 방향 추격 중단")
            continue
        # total-slot cap. Before 2026-08-14 the bot entered one seal per day,
        # so the pick list itself was the cap; with hourly refills a fresh
        # seal of six names must not stack on top of held ones (user: refill
        # emptied slots, never exceed the slot count).
        if held_start + entered_n >= cfg["slots"]:
            break
        sym, direction = p["sym"], p["dir"]
        if sym in st["positions"]:
            continue
        if sym in live_syms:
            log(f"{sym}: 거래소에 이미 포지션이 있어 건너뜀 (타 봇/수동?)")
            continue
        m = mkts.get(sym) or {}
        px = float(prices.get(sym, {}).get("mark")
                   or prices.get(sym, {}).get("mid") or 0)
        if px <= 0:
            log(f"{sym}: 가격 조회 실패, 건너뜀")
            attempt_failed += 1
            continue
        lev = min(cfg["leverage"], int(m.get("max_leverage") or cfg["leverage"]))
        lot = float(m.get("lot_size") or 0.0001)
        tick = float(m.get("tick_size") or 0.01)
        amount = _round_down_to_lot(margin_per * lev / px, lot)
        if amount <= 0 or amount * px < float(m.get("min_order_size") or 10):
            # Counted as a failed attempt, not as a decision: the account is
            # simply too small for this seat size. Without this the seal was
            # marked used and the same silence repeated every hour, with a
            # single log line as the only trace.
            log(f"{sym}: 최소 주문 미달 (명목 ${amount*px:.2f}), 건너뜀")
            attempt_failed += 1
            too_small += 1
            continue
        mv = p["exp_move_pct"] / 100
        long_ = direction == "long"
        pr = bracket_prices(px, direction, mv, cfg, tick)
        if pr is None:
            notify.send(f"브래킷 스킵 {sym}: 익절/손절가가 틱 반올림 후 "
                        f"진입가와 어긋남 (틱 {tick}, 변동폭 {mv:.4f})")
            continue
        tp_s, sl_s = pr

        if dry:
            log(f"[DRY] {sym} {direction} 명목 ${amount*px:.0f} ({lev}배) "
                f"TP {tp_s} SL {sl_s}")
            entered_n += 1
            continue
        try:
            # A swallowed failure here used to mean "assume already set", but
            # a 429 and "already isolated" raise the same PacificaError. On
            # any failure, read the settings back and enter only on confirmed
            # isolated margin at the intended leverage. (review H2)
            mode_ok = lev_ok = True
            try:
                client.update_margin_mode(sym, True)          # isolated
            except PacificaError:
                mode_ok = False
            try:
                client.update_leverage(sym, lev)
            except PacificaError:
                lev_ok = False
            if not (mode_ok and lev_ok) and \
                    not _settings_confirmed(client, sym, lev):
                log(f"⚠️ {sym}: 격리마진/{lev}배 설정을 확인하지 못해 진입 "
                    f"생략 (크로스·엉뚱한 레버리지로 열지 않음)")
                notify.send(f"브래킷 스킵 {sym}: 격리마진/레버리지 설정 실패 "
                            f"후 확인 불가, 진입하지 않음")
                attempt_failed += 1
                continue
            # Persist the intent BEFORE the order goes out: a crash between
            # the order and the fill booking below would otherwise leave a
            # live exchange position that no ledger remembers. Startup
            # reconciles this record against the exchange. (review H9)
            st.setdefault("pending_entries", []).append({
                "sym": sym, "dir": direction, "amount": amount,
                "entry_intent": px, "tp": float(tp_s), "sl": float(sl_s),
                "exp_move_pct": p["exp_move_pct"], "leverage": lev,
                "at": _now().isoformat(), "seal": key,
                "trade_rank": p.get("trade_rank"),
                # why the seal picked this trade; carried into the closed
                # record so grading can be read against the original thesis
                "basis": p.get("basis"),
            })
            save_state(st)
            client.create_market_order(
                sym, "bid" if long_ else "ask", str(amount), "0.5",
                builder_code=policy.get("builder_code", ""),
                take_profit_price=tp_s, stop_loss_price=sl_s)
            # read back the fill so the record holds reality, not intent.
            # a few retries (BR5); if it still cannot be read, record the
            # intent but say so, and let later loops repair it.
            fill, confirmed = px, False
            for _ in range(3):
                time.sleep(2)
                try:
                    _poss = client.get_positions()
                except PacificaError:
                    _poss = []          # order is out; keep recording intent
                for pos in _poss:
                    if pos.get("symbol") == sym:
                        fill = float(pos.get("entry_price") or px)
                        confirmed = True
                        break
                if confirmed:
                    break
            # The order allows 0.5% slippage while the TP can sit less than
            # that away, so a bad fill can land beyond its own bracket. A
            # bracket invalid against the real fill (inverted TP/SL) is
            # closed at market immediately, never held. The check runs even
            # when the fill could not be read back (px is then the best
            # estimate); an unconfirmed fill is additionally flagged so the
            # next watch loop re-reads the real entry and re-validates once.
            # (review H3, N5)
            fill_ok = (float(sl_s) < fill < float(tp_s)) if long_ \
                else (float(tp_s) < fill < float(sl_s))
            if not fill_ok:
                msg = (f"브래킷 역전 {sym}: 체결가 {fill} 가 TP {tp_s} / "
                       f"SL {sl_s} 범위 밖, 즉시 시장가 청산")
                log("⚠️ " + msg)
                try:
                    # Close what the exchange actually holds, exactly like
                    # close_market does; the intended amount is only the
                    # fallback when the read fails. (review N8)
                    close_amt = amount
                    try:
                        for _lp in client.get_positions():
                            if _lp.get("symbol") == sym:
                                close_amt = abs(float(_lp.get("amount")
                                                      or amount))
                                break
                    except (PacificaError, TypeError, ValueError):
                        pass
                    client.create_market_order(
                        sym, "ask" if long_ else "bid", str(close_amt),
                        "0.5", reduce_only=True,
                        builder_code=policy.get("builder_code", ""))
                    notify.send(msg)
                    # Book the round trip so the breaker average sees it:
                    # two taker fills cost about 0.16% of notional, a
                    # deliberately conservative estimate in the same
                    # percent-of-notional units every other closed record
                    # uses. (review N3)
                    st["closed"].append({
                        "sym": sym, "dir": direction,
                        "pnl_pct_est": -0.16,
                        "cause": "역전 즉시청산",
                        "opened_at": _now().isoformat(),
                        "closed_at": _now().isoformat(),
                        "trade_rank": p.get("trade_rank"),
                        "basis": p.get("basis")})
                    _clear_pending(st, sym)     # round trip fully booked (H9)
                    save_state(st)
                    continue        # opened and closed, booked above
                except PacificaError as e:
                    notify.send(msg + f" ...청산 주문도 실패"
                                f"({str(e)[:80]}), 포지션으로 기록해 "
                                f"감시를 계속합니다")
                    # fall through: the record below keeps it watched and
                    # watch_positions force-closes on either line breach
            st["positions"][sym] = {
                "dir": direction, "amount": amount, "entry_intent": px,
                "entry_fill": fill, "fill_confirmed": confirmed,
                "tp": float(tp_s), "sl": float(sl_s),
                "exp_move_pct": p["exp_move_pct"], "leverage": lev,
                "opened_at": _now().isoformat(), "seal": key,
                "trade_rank": p.get("trade_rank"),
                "basis": p.get("basis"),
            }
            if not confirmed:
                # watch_positions re-reads the real entry once and
                # re-validates the bracket against it (review N5)
                st["positions"][sym]["recheck_fill"] = True
            _clear_pending(st, sym)     # intent became a booked position (H9)
            save_state(st)          # persist each fill immediately (BR7)
            # Trust but verify: is the TP/SL really on the exchange? Nothing
            # here changes what watch_positions does: its line checks cover
            # EVERY position, protected or not. The unprotected flag only
            # feeds status() and the alert, so the user can see which
            # positions have no exchange-side lines. Re-attaching is done
            # ONLY when the symbol has no open orders at all: if orders
            # exist but none matched the expected type fields, they may BE
            # the bracket under unknown field names, and a blind re-attach
            # could stack a duplicate stop. (review H1, N2, N6)
            has_tp = has_sl = checked = False
            sym_orders: list = []
            try:
                has_tp, has_sl, sym_orders = _exchange_brackets(client, sym)
                checked = True
            except PacificaError:
                pass
            if checked and not (has_tp and has_sl):
                if not sym_orders:
                    try:
                        client.set_position_tpsl(
                            sym, "bid" if long_ else "ask",
                            take_profit_price="" if has_tp else tp_s,
                            stop_loss_price="" if has_sl else sl_s)
                        has_tp, has_sl, sym_orders = \
                            _exchange_brackets(client, sym)
                    except PacificaError:
                        pass
                else:
                    _log_raw_order_once(sym, sym_orders)
            if not checked or not (has_tp and has_sl):
                st["positions"][sym]["unprotected"] = True
                save_state(st)
                what = "조회 실패로 확인 불가" if not checked else (
                    f"TP {'있음' if has_tp else '없음'} / "
                    f"SL {'있음' if has_sl else '없음'}")
                msg = (f"브래킷 미보호 {sym}: 거래소 TP/SL {what}. "
                       f"봇이 매 회차 감시해 손절선 이탈 시 직접 청산합니다.")
                log("⚠️ " + msg)
                notify.send("⚠️ " + msg)
            entered_n += 1
            slip = (fill - px) / px * 100 * (1 if long_ else -1)
            opened.append(f"{sym} {direction} {lev}배 체결 {fill} "
                          f"(슬리피지 {slip:+.3f}%"
                          f"{'' if confirmed else ', 체결가 미확인'})")
        except PacificaError as e:
            notify.send(f"브래킷 진입 실패 {sym}: {str(e)[:120]}")
            attempt_failed += 1
    if dry:
        _dry_logged.add(key)
    else:
        # Consume the seal only when at least one entry went out, or when
        # every pick was skipped deliberately. If nothing was entered and
        # every attempt failed on transient errors (429, network, feed), the
        # seal stays open so the next loop retries it inside its 6h window;
        # the old unconditional append burned the whole day's picks on one
        # bad minute of API weather.
        if entered_n == 0 and too_small == len(picks) and picks:
            # Say it out loud once per seal: a silent skip every hour is how
            # a small account learns nothing at all about why it never trades
            _warn_once(st, f"too_small:{key}",
                       f"진입 0건: 계좌가 이 자리 크기에 못 미칩니다 "
                       f"(픽 {len(picks)}개 전부 최소 주문 미달). "
                       f"입금하거나 픽당 금액을 낮추세요.")
        if entered_n > 0 or attempt_failed == 0:
            st["entered_seals"].append(key)
        else:
            global _retry_not_before
            _retry_not_before = time.time() + ENTRY_RETRY_COOLDOWN_SEC
            log(f"봉인 미소진: 진입 0건, 일시 실패 {attempt_failed}건, "
                f"{ENTRY_RETRY_COOLDOWN_SEC // 60}분 뒤 재시도")
        save_state(st)
        if opened:
            notify.send("브래킷 진입:\n" + "\n".join(opened))


def realized_close(client, sym: str, since_ms: int) -> dict | None:
    """The exchange's own record of how a position ended (BR3/BR11).

    positions/history carries pnl in dollars and a cause, including
    "liquidation", so grading and the liquidation halt rest on facts rather
    than a mark-price guess.

    Returns None only when the history genuinely holds no close for this
    position. A failed or unparseable query raises PacificaError instead:
    swallowing it here let a 429 on the same cycle as a real liquidation
    silently disable the liquidation halt. (review H7)

    Opening rows count too. The venue reports every row with cause "normal"
    (100 of 100 on 08-21), so the cause branch never fired and only close
    rows were summed. But an open row's pnl IS the opening fee as a negative
    number, and dropping it made every trade look better than it was by one
    side of the round trip: FARTCOIN booked +3.943% where the real result was
    +3.903%. Across the book that is 0.04%p a trade, turning -0.5304%p into
    -0.5704%p. The round trip is 0.08%, not the 0.07% 작업규칙 §11 records.

    The cause and price still come from the last close, because an open row
    describes an entry, not an ending.

    The window ends at this position's own close. since_ms opens it, but the
    same symbol is often re-entered minutes later, and those rows belong to
    the next position: counting them pulled a second opening fee in and made
    one FARTCOIN round trip read -0.081%p instead of its true -0.040%p.
    """
    rows = client._get("positions/history", {"account": client.address})
    try:
        seq = []
        for h in rows:
            if h.get("symbol") != sym:
                continue
            t = int(h.get("created_at") or 0)
            if t < since_ms:
                continue
            side = str(h.get("side", ""))
            cause = h.get("cause") or ""
            ending = (side.startswith("close")
                      or cause in ("take_profit", "stop_loss", "liquidation"))
            if ending or side.startswith("open"):
                seq.append((t, float(h.get("pnl") or 0), ending, cause,
                            float(h.get("price") or 0)))
    except Exception as e:
        raise PacificaError(
            f"positions/history 파싱 실패 ({type(e).__name__}: {e})") from e
    seq.sort()
    total, last = 0.0, None
    for t, pnl, ending, cause, price in seq:
        if last is not None and not ending:
            break                       # a re-entry: the next position's row
        total += pnl
        if ending:
            last = (cause, price)
    if last is None:
        return None
    return {"pnl_usd": total, "cause": last[0], "price": last[1]}


def _flat_pct(client, sym: str, pos: dict, since_ms: int) -> float:
    """Percent for a position closed by --close-all, read from the venue.

    Booking these at 0.0 kept the count honest and the number a lie, and it
    is the same number the running average warns on.
    """
    try:
        real = realized_close(client, sym, since_ms)
        notional = float(pos.get("entry_fill") or 0) * float(pos.get("amount") or 0)
        if real and notional > 0:
            return round(float(real["pnl_usd"]) / notional * 100, 3)
    except Exception:                                       # noqa: BLE001
        pass
    return 0.0


def close_market(client, policy, sym: str, pos: dict, live: dict) -> None:
    # close what the exchange actually holds, not what the ledger remembers
    # (BR6: partial fills or partial closes make the two diverge)
    amt = pos["amount"]
    lp = live.get(sym)
    if lp:
        try:
            amt = abs(float(lp.get("amount") or amt))
        except (TypeError, ValueError):
            pass
    side = "ask" if pos["dir"] == "long" else "bid"
    client.create_market_order(sym, side, str(amt), "0.5", reduce_only=True,
                              builder_code=policy.get("builder_code", ""))


def watch_positions(client, policy, st, cfg, dry: bool) -> None:
    """Expiries, stuck exits, and disappeared positions, every loop."""
    if dry:
        # a dry run observes nothing it did not open, and must never write
        # closed records or flip the halt flag (BR4)
        return
    live = {p.get("symbol"): p for p in client.get_positions()}
    prices = {p["symbol"]: p for p in client.get_prices()}

    for sym in list(st["positions"]):
        pos = st["positions"][sym]
        opened = dt.datetime.fromisoformat(pos["opened_at"])
        mark = float(prices.get(sym, {}).get("mark")
                     or prices.get(sym, {}).get("mid") or 0)
        long_ = pos["dir"] == "long"
        entry = pos["entry_fill"]
        notional = entry * pos["amount"] if entry > 0 else 0

        if sym not in live:
            since = int(opened.timestamp() * 1000)
            # A history query failure defers grading to the next loop rather
            # than falling straight to the estimate, so a real liquidation
            # still reaches the halt check once the query recovers. Only
            # after 3 straight failures does the estimate take over, loudly.
            # (review H7)
            try:
                real = realized_close(client, sym, since)
                pos.pop("grade_fails", None)
            except PacificaError as e:
                fails = int(pos.get("grade_fails", 0)) + 1
                pos["grade_fails"] = fails
                log(f"⚠️ 실현손익 조회 실패 {fails}회 {sym}: {e}")
                if fails < 3:
                    save_state(st)
                    continue
                notify.send(f"브래킷: {sym} 실현손익 조회 {fails}회 연속 실패, "
                            f"추정 채점으로 내려갑니다 (청산 여부 판정 불가)")
                real = None
            tp_d, sl_d = _recorded_distances(pos, cfg)
            if tp_d is None:
                # Leave it on the book rather than grade it at zero. Nothing
                # writes a position without these fields, so this means the
                # record is damaged, and the next cycle can try again.
                log(f"⚠️ {sym}: 브래킷 거리를 못 읽어 채점을 미룹니다 "
                    f"(tp/sl/진입가·예상변동 없음)")
                continue
            if real and notional > 0:
                est = real["pnl_usd"] / notional * 100
                cause = real["cause"]
                if cause in ("", "normal"):
                    # The venue reports every close as "normal", so a label made from
                    # the sign of the result is a guess, and it was being read as fact:
                    # a hand-closed loss became a "stop", which then fed the stop-chase
                    # block and the slippage counter. The price decides when it can,
                    # and the guess is named as one when it cannot.
                    px_close = float(real.get("price") or 0)
                    ref_tp, ref_sl = pos.get("tp"), pos.get("sl")
                    hit = ""
                    if px_close and ref_tp and ref_sl:
                        tp_v, sl_v = float(ref_tp), float(ref_sl)
                        # Past the line counts as that line: a stop that
                        # slipped 1%p is still a stop, and it is precisely
                        # the slipped ones the counter needs to see.
                        if pos["dir"] == "long":
                            hit = ("take_profit" if px_close >= tp_v
                                   else "stop_loss" if px_close <= sl_v else "")
                        else:
                            hit = ("take_profit" if px_close <= tp_v
                                   else "stop_loss" if px_close >= sl_v else "")
                    cause = hit or ("추정:이익" if est >= 0 else "추정:손실")
                if cause == "liquidation" and HALT_ON_LIQUIDATION:
                    # Warned, not halted: every position carries an
                    # exchange-side stop, so a liquidation means that line
                    # was jumped rather than that the bot is unsupervised.
                    _warn_once(st, f"liq:{sym}",
                               f"거래소 청산 확인: {sym} ({est:+.2f}%). "
                               f"손절선을 건너뛴 체결입니다. 매매는 계속합니다.")
                if cause == "stop_loss" and est < -sl_d - DEMOTE_SLIP_PCT:
                    st["slip_events"] = st.get("slip_events", 0) + 1
                    notify.send(f"브래킷 경고: {sym} 손절 이탈 "
                                f"({est:+.2f}% vs -{sl_d:.2f}%) "
                                f"[{st['slip_events']}회]")
            else:
                # history unavailable: fall back to the mark, and err to the
                # stop, never the target (the old guess scored anything a hair
                # above entry as a full TP win and fed that into the breaker)
                move = (mark / entry - 1) * 100 * (1 if long_ else -1) \
                    if mark > 0 and entry > 0 else 0.0
                est = min(move, tp_d) if move > 0 else max(move, -sl_d)
                cause = "추정(이력 조회 실패)"
            st["closed"].append(
                _close_row(sym, pos, round(est, 3), cause))
            del st["positions"][sym]
            save_state(st)
            notify.send(f"브래킷 청산: {sym} {est:+.2f}% ({cause})")
            continue

        # A fill that could not be read back at entry gets one re-validation
        # here: read the real entry price from the live position and, if it
        # inverts the bracket, close at market now instead of holding a
        # broken position to expiry. One shot: the flag is cleared whether
        # or not the read succeeds, the line checks below cover the rest.
        # (review N5)
        if pos.get("recheck_fill"):
            pos.pop("recheck_fill", None)
            try:
                real_entry = float((live.get(sym) or {})
                                   .get("entry_price") or 0)
            except (TypeError, ValueError):
                real_entry = 0.0
            if real_entry > 0:
                pos["entry_fill"] = real_entry
                pos["fill_confirmed"] = True
                entry = real_entry
                ok = (pos["sl"] < entry < pos["tp"]) if long_ \
                    else (pos["tp"] < entry < pos["sl"])
                if not ok:
                    msg = (f"브래킷 역전(재검증) {sym}: 체결가 {entry} 가 "
                           f"TP {pos['tp']} / SL {pos['sl']} 범위 밖, "
                           f"즉시 시장가 청산")
                    log("⚠️ " + msg)
                    try:
                        since_ms = int(time.time() * 1000) - 60_000
                        close_market(client, policy, sym, pos, live)
                        move, tag = _booked_pct(client, sym, pos, mark,
                                                since_ms)
                        st["closed"].append(
                            _close_row(sym, pos, move,
                                       "역전 즉시청산"
                                       + (":" + tag if tag else "")))
                        del st["positions"][sym]
                        save_state(st)
                        notify.send(msg)
                        continue
                    except PacificaError as e:
                        notify.send(msg + f" ...청산 실패({str(e)[:80]}), "
                                    f"감시를 계속합니다")
            save_state(st)

        held_h = (_now() - opened).total_seconds() / 3600
        # A live position past EITHER line means the exchange bracket did not
        # fire (missing or dead). The stop side is the one that bleeds, so it
        # is checked too, not only the TP. (review H1)
        hit_tp = mark > 0 and ((long_ and mark >= pos["tp"])
                               or (not long_ and mark <= pos["tp"]))
        hit_sl = mark > 0 and ((long_ and mark <= pos["sl"])
                               or (not long_ and mark >= pos["sl"]))
        if held_h >= cfg["horizon_h"] or hit_tp or hit_sl:
            why = ("만기" if held_h >= cfg["horizon_h"]
                   else "손절 트리거 잔류" if hit_sl else "익절 트리거 잔류")
            try:
                since_ms = int(time.time() * 1000) - 60_000
                close_market(client, policy, sym, pos, live)
                move, tag = _booked_pct(client, sym, pos, mark, since_ms)
                st["closed"].append(
                    _close_row(sym, pos, move,
                               why + (":" + tag if tag else "")))
                del st["positions"][sym]
                save_state(st)
                notify.send(f"브래킷 {why} 청산: {sym} {move:+.2f}%")
            except PacificaError as e:
                notify.send(f"브래킷 청산 실패 {sym}: {str(e)[:120]}")


def _booked_pct(client, sym: str, pos: dict, mark: float, since_ms: int):
    """What a self-initiated close is worth, preferring the venue to the mark.

    A close the bot observed was read from positions/history; a close the bot
    sent itself was booked straight off the mark with no fees and no slippage
    in it. Same ledger, two accounting standards, and the self-closed rows
    read better by a full round trip. Expiry is about to become the common
    case, so this takes the venue's number once the history has caught up and
    the mark less the round trip when it has not, marking which in the label.
    """
    try:
        real = realized_close(client, sym, since_ms)
    except PacificaError:
        real = None
    entry = pos.get("entry_fill") or 0
    notional = entry * (pos.get("amount") or 0)
    if real and notional > 0:
        return round(real["pnl_usd"] / notional * 100, 3), ""
    if entry > 0 and mark > 0:
        gross = (mark / entry - 1) * 100 * (1 if pos.get("dir") == "long"
                                            else -1)
        return round(gross - FEE_RT_PCT, 3), "추정"
    return 0.0, "추정"


def _warn_once(st, key: str, msg: str) -> None:
    """Say it loudly, once per condition, and carry on trading.

    These three conditions used to stop new entries until a person typed
    --resume. On 08-19 that turned one bad fill into an afternoon of holding
    positions and entering nothing, unnoticed, because a halt announces
    itself to a console window nobody is watching. The account's real
    protection is the exchange-side stop on every position, not this flag,
    so the conditions now warn instead of blocking.

    Suppressed per key on a cooldown, which is the middle these warnings need.
    Comparing message text fired every 30-minute pass, because the average
    warning carries the running average and any close moves it a thousandth;
    the state file held -1.516 while the number had walked to -1.279.
    Suppressing on the key alone went too far the other way: nothing in the
    codebase clears `warned`, --resume included, so a key that rang once went
    silent for the life of the state file. It had already happened, and with
    it the account had neither a halt nor a warning. 의도적결정 13 traded the
    halt away for the warning, so the warning has to keep working.

    Six hours, so a worsening account is told again up to four times a day.
    Old entries were plain strings; those count as "rang, time unknown" and
    are allowed to ring once more.
    """
    seen = st.setdefault("warned", {})
    now = time.time()
    prev = seen.get(key)
    if isinstance(prev, dict) and now - float(prev.get("at") or 0) < WARN_COOLDOWN_SEC:
        return
    seen[key] = {"msg": msg, "at": now}
    log(f"⚠️ {msg}")
    notify.send(f"브래킷 경고: {msg}")


def circuit_breakers(st, cfg) -> None:
    if st["halted"]:
        return
    # Operator closes are not strategy results. --close-all books its exits
    # here like any other, so a breaker meant to ask "is the strategy paying"
    # was averaging in whatever the operator did by hand, and the answer moved
    # with it: on the 08-20 book the same 30 rows read -1.279%p with them and
    # -1.415%p without. Count the last HALT_AVG_AFTER *strategy* closes rather
    # than filtering inside a fixed window, or the sample silently shrinks on
    # a day with many manual exits.
    closed = [c for c in st["closed"] if c.get("cause") != "manual_close"]
    if len(closed) >= HALT_AVG_AFTER:
        recent = closed[-HALT_AVG_AFTER:]
        avg = sum(c["pnl_pct_est"] for c in recent) / len(recent)
        if avg < HALT_AVG_FLOOR:
            _warn_once(st, "avg",
                       f"누적 성적 미달: 최근 {HALT_AVG_AFTER}건 건당 "
                       f"{avg:+.3f}% < {HALT_AVG_FLOOR}%. 매매는 계속합니다.")
    if st.get("slip_events", 0) >= DEMOTE_SLIP_EVENTS:
        # The old remedy said "lower the leverage", which stopped meaning
        # anything on 08-14: notional is fixed per pick now, so leverage
        # moves the margin posted and not the loss a slipped stop takes.
        _warn_once(st, "slip",
                   f"손절 이탈 {st['slip_events']}회. 호가가 얇은 종목이 "
                   f"섞여 있습니다. 명목을 줄이거나 그 종목을 "
                   f"bracket_skip_syms 로 빼는 걸 검토하세요. 매매는 계속합니다.")


def status(st) -> str:
    lines = [f"[{MODE} 모드] 보유 {len(st['positions'])} · "
             f"청산 누적 {len(st['closed'])}건"]
    for sym, p in st["positions"].items():
        lines.append(f"  {sym} {p['dir']} {p['leverage']}배 "
                     f"진입 {p['entry_fill']} TP {p['tp']} SL {p['sl']}"
                     + (" ⚠️ 미보호(거래소 TP/SL 미확인)"
                        if p.get("unprotected") else ""))
    if st["closed"]:
        # Same separation the breaker makes, because this is the line a person
        # actually reads every cycle. Operator exits are not strategy results:
        # on the 08-21 book they moved the score from 19/47 to 20/57 and the
        # total from -23.41%p to -30.24%p, mostly because --close-all books
        # eight fee-only closes. 작업규칙 §11 grades on per-trade return, so
        # that goes first and the sum second.
        strat = [c for c in st["closed"] if c.get("cause") != "manual_close"]
        manual = len(st["closed"]) - len(strat)
        if strat:
            wins = sum(1 for c in strat if c["pnl_pct_est"] > 0)
            tot = sum(c["pnl_pct_est"] for c in strat)
            lines.append(f"  전략 {wins}/{len(strat)} · 건당 "
                         f"{tot / len(strat):+.3f}%p · 합계 {tot:+.2f}%p"
                         + (f" (수동청산 {manual}건 제외)" if manual else ""))
        elif manual:
            lines.append(f"  전략 청산 0건 (수동청산 {manual}건뿐)")
    if st["halted"]:
        lines.append(f"  ⛔ 정지됨: {st['halt_reason']}")
    return "\n".join(lines)


def cycle(client, policy, st, cfg, dry: bool) -> None:
    if not dry:
        # entries that went out right before a crash are re-booked or
        # dropped before anything else runs; dry writes nothing (BR1)
        reconcile_pending(client, st)
    if st["halted"]:
        log(f"정지 상태: {st['halt_reason']} (--resume 으로 해제)")
        # A halt stops NEW entries only. Held positions still need expiry
        # closes, force-closes and grading, or one liquidation halt strands
        # every remaining position until expiry and beyond. Same pattern the
        # EV bot already uses while halted. (review H6)
        try:
            watch_positions(client, policy, st, cfg, dry)
        except Exception as e:
            log(f"정지 중 사후관리 스킵(일시 오류): {e}")
        if not dry:
            save_state(st)
        return
    held_before = len(st["positions"])
    watch_positions(client, policy, st, cfg, dry)
    circuit_breakers(st, cfg)
    if not st["halted"]:
        enter_positions(client, policy, st, cfg, dry)
    # A heartbeat says the process exists; these say it worked. The
    # watchdog and account_status read them, so a bot whose cycle
    # throws on every pass can no longer look healthy.
    st["last_cycle_ok_at"] = _now().isoformat(timespec="seconds")
    if len(st["positions"]) > held_before:
        st["last_entry_at"] = st["last_cycle_ok_at"]
    if not dry:
        save_state(st)


SEAL_POLL_SEC = 30

# ── seal self-generation ─────────────────────────────────────────────────
# The trader consumes seals; seal_maker produces them. When no seal fresh
# enough exists, the loop generates one itself so a standalone install
# needs no external scheduler. At most one attempt per hour, and a failed
# generation only logs: position aftercare must never die with it.
SEAL_FRESH_H = 1.0
SEAL_GEN_MIN_INTERVAL_SEC = 3600
_last_seal_gen: float = 0.0
# An operator who runs an external seal generator sets
# bracket_selfgen_seal: false in policy.yaml so the bot never competes
# with it; the shipped default (no key) keeps self-generation on.
_selfgen_enabled: bool = True


def _newest_seal_age_h() -> float:
    """Age in hours of the newest seal file, by mtime; huge when none."""
    files = glob.glob(os.path.join(OUTPUTS_DIR, "내일예측_*.json"))
    if not files:
        return 1e9
    return (time.time() - max(os.path.getmtime(p) for p in files)) / 3600.0


def maybe_generate_seal() -> None:
    """Generate a fresh seal when the newest one is older than an hour.

    Every failure path logs and returns: the loop's job of watching and
    closing positions continues on the existing seal (or none).
    """
    global _last_seal_gen
    if not _selfgen_enabled:
        return
    try:
        age_h = _newest_seal_age_h()
        if age_h <= SEAL_FRESH_H:
            return
        if time.time() - _last_seal_gen < SEAL_GEN_MIN_INTERVAL_SEC:
            return
        _last_seal_gen = time.time()
        ago = "없음" if age_h > 1e8 else f"{age_h:.1f}시간 지남"
        log(f"봉인 {ago}, 새로 만듭니다 (몇 분 걸릴 수 있음)")
        from . import seal_maker
        path = seal_maker.make_seal(out_dir=OUTPUTS_DIR, log=log)
        if path is None:
            log("봉인 생성 보류(표본 부족), 다음 시간에 재시도합니다")
    except Exception as e:                 # noqa: BLE001
        log(f"봉인 생성 실패({type(e).__name__}: {str(e)[:120]}), "
            f"기존 봉인과 보유 관리로 계속합니다")


def fresh_seal_waiting(st: dict, dry: bool) -> bool:
    """Is there a seal this run has not entered yet, still inside its window?

    Cheap enough to ask every 30 seconds: it reads one small JSON file and
    touches no network. Guards mirror enter_positions exactly, so a True here
    means the next cycle will really act.
    """
    rec = latest_seal()
    if not rec:
        log("진입 대기: 봉인(내일예측_*.json) 이 아직 없다. 픽 생성이 만들면 "
            "다음 회차에 자동 진입한다.")
        return False
    key = seal_key(rec)
    if key in st.get("entered_seals", []) or (dry and key in _dry_logged):
        return False
    if time.time() < _retry_not_before:
        return False        # failed pass cooling down, no hot loop (P4)
    try:
        made = dt.datetime.fromisoformat(rec["made_at"])
    except (KeyError, ValueError):
        return False
    return (_now() - made).total_seconds() / 3600 <= 6


def sleep_alive(seconds: int, dry: bool, st: dict | None = None) -> None:
    """Sleep between cycles, but wake early the moment a new seal appears.

    Two jobs. The lock window (10 min) is deliberately shorter than the loop
    (30 min), so a killed bot releases the account quickly and the heartbeat
    is renewed during the sleep rather than once per cycle.

    And the wait is interruptible. A user who asks for picks now expects the
    bot to act now, not to sit out the rest of a 30-minute timer; with seals
    no longer pinned to one evening hour, a fresh seal can land at any moment.
    Polling the seal file costs one local read, so the bot notices within
    half a minute instead of up to half an hour. Dry writes nothing.
    """
    left = seconds
    hb = 0
    while left > 0:
        nap = min(SEAL_POLL_SEC, left)
        time.sleep(nap)
        left -= nap
        hb += nap
        if not dry and hb >= 300:
            write_heartbeat()
            hb = 0
        if st is not None and fresh_seal_waiting(st, dry):
            log("새 봉인 감지, 대기를 끊고 바로 진입 판단으로 갑니다")
            return


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="1사이클만")
    ap.add_argument("--dry", action="store_true", help="판단만, 주문 안 함")
    ap.add_argument("--status", action="store_true", help="상태만 출력")
    ap.add_argument("--resume", action="store_true",
                    help="정지 해제 (옛 상태 파일용, 이제 멈추는 장치가 없음)")
    ap.add_argument("--close-all", action="store_true", help="전량 청산")
    args = ap.parse_args()

    # Starting is the operator saying "trade": it outranks a stop they asked
    # for earlier, and it is the only thing that clears one. Without this the
    # watchdog and a stop would argue with each other every hour.
    st_early = load_state()
    if st_early.get("stopped_by_user") and not (args.status or args.dry):
        st_early["stopped_by_user"] = False
        st_early["stopped_at"] = ""
        save_state(st_early)
        log("사용자 중지 상태를 시작과 함께 해제합니다")

    policy = load_policy()
    cfg = apply_budget(bracket_cfg(policy))
    global _selfgen_enabled
    _selfgen_enabled = bool(policy.get("bracket_selfgen_seal", True))
    if not _selfgen_enabled:
        log("봉인 자체 생성 꺼짐 (bracket_selfgen_seal: false), 외부 생성기를 기다립니다")
    use_mode(bracket_mode(policy), args.dry)   # before any file is touched
    # Every safety file (state, heartbeat, seals) lives under OUTPUTS_DIR.
    # If it cannot be written, the heartbeat and ledger the cross-bot guards
    # depend on cannot exist either, so refuse to run at all. (review N1)
    try:
        os.makedirs(OUTPUTS_DIR, exist_ok=True)
        _probe = os.path.join(OUTPUTS_DIR, ".write_probe")
        with open(_probe, "w", encoding="utf-8") as _pf:
            _pf.write("ok")
        os.remove(_probe)
    except OSError as e:
        log(f"outputs 폴더({OUTPUTS_DIR})에 쓸 수 없어 시작을 거부합니다: {e}")
        return
    st = load_state()

    if args.status:
        print(status(st))
        return
    if args.resume:
        st["halted"], st["halt_reason"], st["slip_events"] = False, "", 0
        save_state(st)
        print("정지 해제됨")
        return

    # X1: the old EV bot and this one share one account's margin. A docstring
    # warning does not stop a double launch; a recent heartbeat on the old
    # bot's state file does. The EV bot's real state file lives under
    # data_file(), not cwd's state.json, which no longer exists and made this
    # guard never fire. Its mtime is renewed every EV cycle, so freshness is
    # judged against the loop interval. (review H4)
    os.environ["PACIFICA_BASE_URL"] = policy["base_url"]   # data_file() tag
    ev_state = data_file("autonomous.json")
    if os.path.exists(ev_state) and not args.close_all:
        fresh_min = max(10, int(policy.get("loop_interval_min", 30) or 30) * 2)
        age_min = (time.time() - os.path.getmtime(ev_state)) / 60
        if age_min < fresh_min:
            msg = (f"기존 EV 봇(autonomous)이 {age_min:.0f}분 전까지 살아있던 "
                   "흔적이 있어 시작을 거부합니다. 같은 계좌 증거금을 두 봇이 "
                   "나눠 쓰면 안 됩니다. EV 봇을 끄고 다시 실행하세요 "
                   f"(꺼진 봇의 흔적이면 {fresh_min}분 뒤 자동 해제).")
            log(msg)
            notify.send("브래킷: " + msg)
            return

    # The same rule between the two bracket modes: one account's margin, so
    # whichever mode is alive keeps it until it stops.
    running = live_bracket() if not args.close_all else None
    if running:
        other, age_min = running
        msg = (f"{other} 모드가 {age_min:.0f}분 전까지 돌고 있어 시작을 "
               "거부합니다. 한 계좌를 두 모드가 나눠 쓰면 안 됩니다. 그 창에서 "
               "Ctrl+C로 끄고 다시 실행하세요 "
               f"(꺼진 봇의 흔적이면 {HEARTBEAT_FRESH_MIN}분 뒤 자동 해제).")
        log(msg)
        notify.send("브래킷: " + msg)
        return

    # Claim the account the moment the lock check passes. Everything below
    # this line talks to the network first (client, reconcile), which took
    # tens of seconds on 08-20 morning: in that window this bot did not
    # exist yet, so the watchdog read "no heartbeat" and started a second
    # one. The claim has to be the first thing, not the first thing in the
    # loop. (--close-all skips the lock, so it must not claim either.)
    claimed = not args.dry and not args.close_all
    if claimed:
        write_heartbeat()

    try:
        client = make_client(policy)
    except Exception:
        # The claim above is written before the network is touched on purpose,
        # but a claim for a bot that never came up blocks a restart for the
        # whole freshness window. Hand it back on the way out; a crash later
        # still lets the heartbeat expire on its own.
        if claimed:
            clear_heartbeat()
        raise

    # Crash recovery must run before --close-all too, or a position that was
    # opened but never booked would survive a "close everything". (review H9)
    if not args.dry:
        reconcile_pending(client, st)

    if args.close_all:
        # Only a position whose close order actually went out leaves the
        # book. A failed close stays booked, loudly: wiping it would leave a
        # live exchange position that no ledger remembers and no watcher
        # ever force-closes. (review H8)
        live = {p.get("symbol"): p for p in client.get_positions()}
        failed = []
        for sym, pos in list(st["positions"].items()):
            try:
                since_ms = int(time.time() * 1000) - 60_000
                close_market(client, policy, sym, pos, live)
                # Book it the way every other close path does. Without this a
                # flatten erased those trades from the ledger, so the running
                # average that warns on a bad run never saw them, and neither
                # did any later measurement.
                # _close_row carries sym and dir like every other close path:
                # without them the merge key is (None, time), so a live bot's
                # own record of the same trade counts twice and the trade
                # drops out of per-symbol learning.
                row = _close_row(sym, pos,
                                 _flat_pct(client, sym, pos, since_ms),
                                 "manual_close")
                row["basis"] = ("--close-all 전량 청산 "
                                "(손익은 거래소 이력에서 확인)")
                st["closed"].append(row)
                del st["positions"][sym]
                log(f"청산: {sym}")
            except PacificaError as e:
                failed.append(sym)
                log(f"⚠️ 청산 실패 {sym}: {e} (장부에 남김, 재실행으로 재시도)")
        # Flattening is not a reason to stop trading: the operator asked
        # for a clean slate, and the next cycle fills it from the seal.
        st["halted"], st["halt_reason"] = False, ""
        save_state(st)
        if failed:
            msg = ("브래킷 --close-all: 청산 주문 실패로 장부에 남은 포지션: "
                   + ", ".join(failed) + ". 거래소에 살아 있을 수 있으니 "
                   "--close-all 재실행 또는 수동 청산 필요.")
            log("⚠️ " + msg)
            notify.send("⚠️ " + msg)
        return

    tp_txt = (f"TP {cfg['tp_pct']}% 고정" if cfg["tp_pct"] > 0
              else f"TP {cfg['tp_mult']}x")
    log(f"브래킷 트레이더 시작 · {MODE} 모드 · 슬롯 {cfg['slots']} · "
        f"{cfg['leverage']}배 · 자본 {cfg['capital_pct']:.0f}% · "
        f"{tp_txt} / SL {cfg['sl_mult']}x · "
        f"{'DRY RUN' if args.dry else '실주문'}")
    try:
        while True:
            if not args.dry:
                write_heartbeat()      # hold the account for this mode
            maybe_generate_seal()
            if not args.dry:
                write_heartbeat()      # generation can take minutes; renew
            try:
                cycle(client, policy, st, cfg, args.dry)
            except PacificaError as e:
                log(f"사이클 오류(다음 사이클에 재시도): {e}")
                if not args.dry:
                    save_state(st)     # keep whatever was booked before the error
            except Exception as e:                 # noqa: BLE001
                notify.send(f"브래킷 예상 밖 오류: {type(e).__name__}: {str(e)[:150]}")
                if not args.dry:
                    save_state(st)
            if args.once:
                break
            print(status(st))
            sleep_alive(LOOP_MIN * 60, args.dry, st)
    finally:
        if not args.dry:
            clear_heartbeat()


if __name__ == "__main__":
    main()
