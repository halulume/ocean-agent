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
  · Pre-registered circuit breakers halt the bot loudly (file flag + telegram)
    rather than letting it degrade quietly.
  · Dry runs touch NOTHING on disk (review 5, BR1/BR4: a dry run once burned
    the day's seal and could even flip the halt flag; now it only logs).
  · Grading of closed trades reads the exchange's own realized pnl and cause,
    not a mark-price guess (BR3/BR11); the guess remains only as a fallback
    and errs to the stop, never the target.
  · The old EV bot (autonomous.py) is untouched; never run both at once. On
    entry, any symbol that already has a live exchange position - whoever
    opened it - is skipped (BR2).

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
from . import notify

KST = dt.timezone(dt.timedelta(hours=9))
STATE_PATH = os.path.join("outputs", "bracket_state.json")
LOOP_MIN = 30

# ── pre-registered circuit breakers (round-3 review, 2026-08-11) ──
HALT_ON_LIQUIDATION = True          # exchange-confirmed liquidation = stop
HALT_AVG_AFTER = 30                 # trades before the average is judged
HALT_AVG_FLOOR = -0.55              # % per trade; -2 sigma of the expectation
DEMOTE_SLIP_EVENTS = 2              # stop fills worse than line by ...
DEMOTE_SLIP_PCT = 0.5               # ... this many %p, twice -> leverage down

# Seals a dry run has already logged, per process. Deliberately NOT persisted:
# dry must leave no trace in the state file (BR1), this only stops log spam.
_dry_logged: set[str] = set()


def _now():
    return dt.datetime.now(KST)


def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"positions": {}, "closed": [], "entered_seals": [],
            "halted": False, "halt_reason": "", "slip_events": 0}


def save_state(st: dict) -> None:
    os.makedirs("outputs", exist_ok=True)
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
        "tp_mult": float(policy.get("bracket_tp_mult", 0.3)),
        "sl_mult": float(policy.get("bracket_sl_mult", 1.0)),
        "vol_floor_pct": float(policy.get("bracket_vol_floor_pct", 0.8)),
        "horizon_h": int(policy.get("bracket_horizon_h", 24)),
    }


def latest_seal() -> dict | None:
    """The newest seal that carries trade ranks (older files predate them)."""
    files = sorted(glob.glob(os.path.join("outputs", "내일예측_*.json")))
    for path in reversed(files):
        try:
            with open(path, encoding="utf-8") as f:
                rec = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if any("trade_rank" in p for p in rec.get("picks", [])):
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
    """
    long_ = direction == "long"
    tp = px * (1 + cfg["tp_mult"] * mv) if long_ else px * (1 - cfg["tp_mult"] * mv)
    sl = px * (1 - cfg["sl_mult"] * mv) if long_ else px * (1 + cfg["sl_mult"] * mv)
    tp_s, sl_s = _round_to_tick(tp, tick), _round_to_tick(sl, tick)
    tp_f, sl_f = float(tp_s), float(sl_s)
    ok = (sl_f < px < tp_f) if long_ else (tp_f < px < sl_f)
    if not ok:
        return None
    return tp_s, sl_s


def enter_positions(client, policy, st, cfg, dry: bool) -> None:
    rec = latest_seal()
    if not rec:
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
        notify.send("브래킷: 가용 증거금을 읽지 못해 진입 중단")
        return
    picks = select_picks(rec, cfg)
    if not picks:
        return
    open_slots = max(1, cfg["slots"] - len(st["positions"]))
    margin_per = funds * cfg["deploy_pct"] / open_slots
    mkts = {m["symbol"]: m for m in client.get_markets()}
    prices = {p["symbol"]: p for p in client.get_prices()}
    # any live exchange position blocks that symbol, whoever opened it:
    # the old bot, a manual trade, or ourselves (BR2)
    live_syms = {p.get("symbol") for p in client.get_positions()}

    opened = []
    for p in picks:
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
            continue
        lev = min(cfg["leverage"], int(m.get("max_leverage") or cfg["leverage"]))
        lot = float(m.get("lot_size") or 0.0001)
        tick = float(m.get("tick_size") or 0.01)
        amount = _round_down_to_lot(margin_per * lev / px, lot)
        if amount <= 0 or amount * px < float(m.get("min_order_size") or 10):
            log(f"{sym}: 최소 주문 미달 (명목 ${amount*px:.2f}), 건너뜀")
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
            continue
        try:
            try:
                client.update_margin_mode(sym, True)          # isolated
            except PacificaError:
                pass                                          # already isolated
            try:
                client.update_leverage(sym, lev)
            except PacificaError:
                pass                                          # already set
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
                for pos in client.get_positions():
                    if pos.get("symbol") == sym:
                        fill = float(pos.get("entry_price") or px)
                        confirmed = True
                        break
                if confirmed:
                    break
            st["positions"][sym] = {
                "dir": direction, "amount": amount, "entry_intent": px,
                "entry_fill": fill, "fill_confirmed": confirmed,
                "tp": float(tp_s), "sl": float(sl_s),
                "exp_move_pct": p["exp_move_pct"], "leverage": lev,
                "opened_at": _now().isoformat(), "seal": key,
                "trade_rank": p.get("trade_rank"),
            }
            save_state(st)          # persist each fill immediately (BR7)
            slip = (fill - px) / px * 100 * (1 if long_ else -1)
            opened.append(f"{sym} {direction} {lev}배 체결 {fill} "
                          f"(슬리피지 {slip:+.3f}%"
                          f"{'' if confirmed else ', 체결가 미확인'})")
        except PacificaError as e:
            notify.send(f"브래킷 진입 실패 {sym}: {str(e)[:120]}")
    if dry:
        _dry_logged.add(key)
    else:
        st["entered_seals"].append(key)
        save_state(st)
        if opened:
            notify.send("브래킷 진입:\n" + "\n".join(opened))


def realized_close(client, sym: str, since_ms: int) -> dict | None:
    """The exchange's own record of how a position ended (BR3/BR11).

    positions/history carries pnl in dollars and a cause, including
    "liquidation", so grading and the liquidation halt rest on facts rather
    than a mark-price guess.
    """
    try:
        evs = []
        for h in client._get("positions/history", {"account": client.address}):
            if h.get("symbol") != sym:
                continue
            t = int(h.get("created_at") or 0)
            if t < since_ms:
                continue
            cause = h.get("cause") or ""
            if str(h.get("side", "")).startswith("close") or \
                    cause in ("take_profit", "stop_loss", "liquidation"):
                evs.append((t, float(h.get("pnl") or 0), cause))
        if not evs:
            return None
        evs.sort()
        return {"pnl_usd": sum(e[1] for e in evs), "cause": evs[-1][2]}
    except Exception:
        return None


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
            real = realized_close(client, sym, since)
            tp_d = cfg["tp_mult"] * pos["exp_move_pct"]
            sl_d = cfg["sl_mult"] * pos["exp_move_pct"]
            if real and notional > 0:
                est = real["pnl_usd"] / notional * 100
                cause = real["cause"]
                if cause == "liquidation" and HALT_ON_LIQUIDATION:
                    st["halted"] = True
                    st["halt_reason"] = f"거래소 청산 확인: {sym} ({est:+.2f}%)"
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
            st["closed"].append({"sym": sym, "dir": pos["dir"],
                                 "pnl_pct_est": round(est, 3),
                                 "cause": cause,
                                 "opened_at": pos["opened_at"],
                                 "closed_at": _now().isoformat(),
                                 "trade_rank": pos.get("trade_rank")})
            del st["positions"][sym]
            save_state(st)
            notify.send(f"브래킷 청산: {sym} {est:+.2f}% ({cause})")
            continue

        held_h = (_now() - opened).total_seconds() / 3600
        stuck = mark > 0 and ((long_ and mark >= pos["tp"])
                              or (not long_ and mark <= pos["tp"]))
        if held_h >= cfg["horizon_h"] or stuck:
            why = "만기" if held_h >= cfg["horizon_h"] else "익절 트리거 잔류"
            try:
                close_market(client, policy, sym, pos, live)
                move = (mark / entry - 1) * 100 * (1 if long_ else -1) \
                    if entry > 0 and mark > 0 else 0.0
                st["closed"].append({"sym": sym, "dir": pos["dir"],
                                     "pnl_pct_est": round(move, 3),
                                     "cause": why,
                                     "opened_at": pos["opened_at"],
                                     "closed_at": _now().isoformat(),
                                     "trade_rank": pos.get("trade_rank")})
                del st["positions"][sym]
                save_state(st)
                notify.send(f"브래킷 {why} 청산: {sym} {move:+.2f}%")
            except PacificaError as e:
                notify.send(f"브래킷 청산 실패 {sym}: {str(e)[:120]}")


def circuit_breakers(st, cfg) -> None:
    if st["halted"]:
        return
    closed = st["closed"]
    if len(closed) >= HALT_AVG_AFTER:
        recent = closed[-HALT_AVG_AFTER:]
        avg = sum(c["pnl_pct_est"] for c in recent) / len(recent)
        if avg < HALT_AVG_FLOOR:
            st["halted"] = True
            st["halt_reason"] = (f"누적 성적 미달: 최근 {HALT_AVG_AFTER}건 "
                                 f"건당 {avg:+.3f}% < {HALT_AVG_FLOOR}%")
    if st.get("slip_events", 0) >= DEMOTE_SLIP_EVENTS and cfg["leverage"] > 5:
        st["halted"] = True
        st["halt_reason"] = (f"손절 이탈 {st['slip_events']}회: 레버리지를 "
                             f"5배 이하로 낮춰 재개할 것 (bracket_leverage)")


def status(st) -> str:
    lines = [f"보유 {len(st['positions'])} · 청산 누적 {len(st['closed'])}건"]
    for sym, p in st["positions"].items():
        lines.append(f"  {sym} {p['dir']} {p['leverage']}배 "
                     f"진입 {p['entry_fill']} TP {p['tp']} SL {p['sl']}")
    if st["closed"]:
        wins = sum(1 for c in st["closed"] if c["pnl_pct_est"] > 0)
        tot = sum(c["pnl_pct_est"] for c in st["closed"])
        lines.append(f"  승률 {wins}/{len(st['closed'])} · 합계 {tot:+.2f}%")
    if st["halted"]:
        lines.append(f"  ⛔ 정지됨: {st['halt_reason']}")
    return "\n".join(lines)


def cycle(client, policy, st, cfg, dry: bool) -> None:
    if st["halted"]:
        log(f"정지 상태: {st['halt_reason']} (--resume 으로 해제)")
        return
    watch_positions(client, policy, st, cfg, dry)
    circuit_breakers(st, cfg)
    if not st["halted"]:
        enter_positions(client, policy, st, cfg, dry)
    if not dry:
        save_state(st)


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="1사이클만")
    ap.add_argument("--dry", action="store_true", help="판단만, 주문 안 함")
    ap.add_argument("--status", action="store_true", help="상태만 출력")
    ap.add_argument("--resume", action="store_true", help="정지 해제")
    ap.add_argument("--close-all", action="store_true", help="전량 청산 후 정지")
    args = ap.parse_args()

    policy = load_policy()
    cfg = bracket_cfg(policy)
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
    # bot's state file does.
    old_state = "state.json"
    if os.path.exists(old_state) and not args.close_all:
        age_min = (time.time() - os.path.getmtime(old_state)) / 60
        if age_min < 10:
            msg = (f"기존 EV 봇(autonomous)이 {age_min:.0f}분 전까지 살아있던 "
                   "흔적이 있어 시작을 거부합니다. 같은 계좌 증거금을 두 봇이 "
                   "나눠 쓰면 안 됩니다. EV 봇을 끄고 다시 실행하세요.")
            log(msg)
            notify.send("브래킷: " + msg)
            return

    client = make_client(policy)

    if args.close_all:
        live = {p.get("symbol"): p for p in client.get_positions()}
        for sym, pos in list(st["positions"].items()):
            try:
                close_market(client, policy, sym, pos, live)
                log(f"청산: {sym}")
            except PacificaError as e:
                log(f"청산 실패 {sym}: {e}")
        st["positions"] = {}
        st["halted"], st["halt_reason"] = True, "수동 전량 청산"
        save_state(st)
        return

    log(f"브래킷 트레이더 시작 · 슬롯 {cfg['slots']} · {cfg['leverage']}배 · "
        f"TP {cfg['tp_mult']}x / SL {cfg['sl_mult']}x · "
        f"{'DRY RUN' if args.dry else '실주문'}")
    while True:
        try:
            cycle(client, policy, st, cfg, args.dry)
        except PacificaError as e:
            log(f"사이클 오류(다음 사이클에 재시도): {e}")
            if not args.dry:
                save_state(st)      # keep whatever was booked before the error
        except Exception as e:                     # noqa: BLE001
            notify.send(f"브래킷 예상 밖 오류: {type(e).__name__}: {str(e)[:150]}")
            if not args.dry:
                save_state(st)
        if args.once:
            break
        print(status(st))
        time.sleep(LOOP_MIN * 60)


if __name__ == "__main__":
    main()
