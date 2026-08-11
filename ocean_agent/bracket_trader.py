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
  · The old EV bot (autonomous.py) is untouched; never run both at once, they
    would fight over the same margin.

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
HALT_ON_LIQUIDATION = True          # any liquidation = full stop
HALT_AVG_AFTER = 30                 # trades before the average is judged
HALT_AVG_FLOOR = -0.55              # % per trade; -2 sigma of the expectation
DEMOTE_SLIP_EVENTS = 2              # stop fills worse than line by ...
DEMOTE_SLIP_PCT = 0.5               # ... this many %p, twice -> leverage down


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


def enter_positions(client, policy, st, cfg, dry: bool) -> None:
    rec = latest_seal()
    if not rec:
        return
    made = dt.datetime.fromisoformat(rec["made_at"])
    seal_id = rec["_path"]
    if seal_id in st["entered_seals"]:
        return
    age_h = (_now() - made).total_seconds() / 3600
    if age_h > 6:
        # a stale seal is not an entry signal; wait for tonight's fresh one
        log(f"봉인이 {age_h:.1f}시간 지나 진입 생략 (다음 봉인 대기)")
        st["entered_seals"].append(seal_id)
        return

    acct = client.get_account()
    equity = float(acct.get("account_equity") or acct.get("balance") or 0)
    if equity <= 0:
        notify.send("브래킷: 계좌 잔고를 읽지 못해 진입 중단")
        return
    picks = select_picks(rec, cfg)
    if not picks:
        return
    margin_per = equity * cfg["deploy_pct"] / cfg["slots"]
    mkts = {m["symbol"]: m for m in client.get_markets()}
    prices = {p["symbol"]: p for p in client.get_prices()}

    opened = []
    for p in picks:
        sym, direction = p["sym"], p["dir"]
        if sym in st["positions"]:
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
        tp_px = px * (1 + cfg["tp_mult"] * mv) if long_ else px * (1 - cfg["tp_mult"] * mv)
        sl_px = px * (1 - cfg["sl_mult"] * mv) if long_ else px * (1 + cfg["sl_mult"] * mv)
        tp_s, sl_s = _round_to_tick(tp_px, tick), _round_to_tick(sl_px, tick)

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
            # read back the fill so the record holds reality, not intent
            time.sleep(2)
            fill = px
            for pos in client.get_positions():
                if pos.get("symbol") == sym:
                    fill = float(pos.get("entry_price") or px)
                    break
            st["positions"][sym] = {
                "dir": direction, "amount": amount, "entry_intent": px,
                "entry_fill": fill, "tp": float(tp_s), "sl": float(sl_s),
                "exp_move_pct": p["exp_move_pct"], "leverage": lev,
                "opened_at": _now().isoformat(), "seal": seal_id,
                "trade_rank": p.get("trade_rank"),
            }
            slip = (fill - px) / px * 100 * (1 if long_ else -1)
            opened.append(f"{sym} {direction} {lev}배 체결 {fill} "
                          f"(슬리피지 {slip:+.3f}%)")
        except PacificaError as e:
            notify.send(f"브래킷 진입 실패 {sym}: {str(e)[:120]}")
    st["entered_seals"].append(seal_id)
    if opened:
        notify.send("브래킷 진입:\n" + "\n".join(opened))


def close_market(client, policy, sym: str, pos: dict) -> None:
    side = "ask" if pos["dir"] == "long" else "bid"
    client.create_market_order(sym, side, str(pos["amount"]), "0.5",
                              reduce_only=True,
                              builder_code=policy.get("builder_code", ""))


def watch_positions(client, policy, st, cfg, dry: bool) -> None:
    """Expiries, stuck exits, and disappeared positions, every loop."""
    live = {p.get("symbol"): p for p in client.get_positions()}
    prices = {p["symbol"]: p for p in client.get_prices()}

    for sym in list(st["positions"]):
        pos = st["positions"][sym]
        opened = dt.datetime.fromisoformat(pos["opened_at"])
        mark = float(prices.get(sym, {}).get("mark")
                     or prices.get(sym, {}).get("mid") or 0)
        long_ = pos["dir"] == "long"

        if sym not in live:
            # the exchange closed it: TP, SL, or something worse. classify by
            # where the mark sits; flag anything far beyond the stop.
            entry = pos["entry_fill"]
            if mark <= 0 or entry <= 0:
                est = 0.0
            else:
                move = (mark / entry - 1) * 100 * (1 if long_ else -1)
                tp_d = cfg["tp_mult"] * pos["exp_move_pct"]
                sl_d = cfg["sl_mult"] * pos["exp_move_pct"]
                est = tp_d if move >= 0 else max(move, -sl_d)
                if move < -sl_d - DEMOTE_SLIP_PCT:
                    st["slip_events"] = st.get("slip_events", 0) + 1
                    est = move
                    notify.send(f"브래킷 경고: {sym} 손절 이탈 추정 "
                                f"({move:+.2f}% vs 손절선 -{sl_d:.2f}%) "
                                f"[{st['slip_events']}회]")
                if move < -sl_d * 2.2 and HALT_ON_LIQUIDATION:
                    st["halted"] = True
                    st["halt_reason"] = (f"청산 의심: {sym} {move:+.2f}% "
                                         f"(손절선의 2.2배 초과)")
            st["closed"].append({"sym": sym, "dir": pos["dir"],
                                 "pnl_pct_est": round(est, 3),
                                 "opened_at": pos["opened_at"],
                                 "closed_at": _now().isoformat(),
                                 "trade_rank": pos.get("trade_rank")})
            del st["positions"][sym]
            notify.send(f"브래킷 청산 감지: {sym} 추정 {est:+.2f}%")
            continue

        held_h = (_now() - opened).total_seconds() / 3600
        stuck = mark > 0 and ((long_ and mark >= pos["tp"])
                              or (not long_ and mark <= pos["tp"]))
        if held_h >= cfg["horizon_h"] or stuck:
            why = "만기" if held_h >= cfg["horizon_h"] else "익절 트리거 잔류"
            if dry:
                log(f"[DRY] {sym} {why} 청산")
                continue
            try:
                close_market(client, policy, sym, pos)
                entry = pos["entry_fill"]
                move = (mark / entry - 1) * 100 * (1 if long_ else -1) \
                    if entry > 0 and mark > 0 else 0.0
                st["closed"].append({"sym": sym, "dir": pos["dir"],
                                     "pnl_pct_est": round(move, 3),
                                     "opened_at": pos["opened_at"],
                                     "closed_at": _now().isoformat(),
                                     "reason": why,
                                     "trade_rank": pos.get("trade_rank")})
                del st["positions"][sym]
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

    client = make_client(policy)

    if args.close_all:
        for sym, pos in list(st["positions"].items()):
            try:
                close_market(client, policy, sym, pos)
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
        except Exception as e:                     # noqa: BLE001
            notify.send(f"브래킷 예상 밖 오류: {type(e).__name__}: {str(e)[:150]}")
        if args.once:
            break
        print(status(st))
        time.sleep(LOOP_MIN * 60)


if __name__ == "__main__":
    main()
