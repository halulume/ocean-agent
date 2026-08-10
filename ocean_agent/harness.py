"""풀 백테스트 하네스, 봇의 실제 매매 파이프라인을 과거 OHLC 위에 재생한다.

무엇을 재는가:
  신호 → 지표분석 → 승률/EV 관문 → 확신도 → 진입 → TP/SL·본전스탑·트레일링·
  부분익절·시간만료 → 청산 → 원인분석. 이 전 과정을 봉 단위로 되돌려
  '순수 지표매매'의 수익곡선과 패배 원인을 뽑는다.

왜 별도 파일인가:
  walkforward 는 '신호가 방향을 맞히나'만 잰다(청산 규칙 없음). rematrix 는
  신호×시간봉의 평균 EV를 잰다. 둘 다 "그래서 계좌가 어떻게 되나"는 답하지
  못한다. 손절·트레일링·동시보유·예산이 결과를 크게 바꾸기 때문이다.

재사용 원칙, 새 판단 로직을 만들지 않는다:
  신호/지표  signal_scanner._series, ._signals
  관문       signal_scanner 의 MIN_N / MIN_EDGE / FEE_RT / 스윙·변동성 상수
  확신도     brain.conviction
  사이징     autonomous.run_cycle 과 같은 식 (EV비례·확신도배율·레버리지)
  사후관리   autonomous.manage_positions 와 같은 규칙 (policy.yaml 값 그대로)
  귀인       postmortem.analyze_close

⚠️ 한계 (리포트 맨 위에도 적힌다):
  완벽체결·슬리피지 0 가정, 펀딩 제외, 과거 국면 실측치이며 미래 보장 아님.
  유동성/호가 데이터가 없어 유동성 관문은 생략한다.

실행:
  python -m ocean_agent.harness --coins BTC ETH SOL --start 2019
  python -m ocean_agent.harness --coins BTC --intervals 1d --smoke
  백테스트.bat            (백그라운드 실행)
"""

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

from . import signal_scanner as ss
from .indicators import INTERVAL_MS

MIN_SAMPLES = ss.MIN_N     # 스윕용, 기본은 스캐너와 동일
SIZE_SCALE = 1.0           # 스윕용, 목표·손절 크기 보정 배수
# 증거금 하한: 0이면 실거래와 동일(signal_scanner.MIN_MARGIN_USD=25 관문 적용),
# 양수면 그 값으로 스윕, 음수면 관문 없음 (2026-08-07 실거래 일치화, 이전엔
# 선언만 있고 미사용이라 하네스가 실거래보다 관대했다)
MIN_MARGIN = 0.0
# 스윕용, 사이징 기준 자본을 고정한다(복리 끔). 백테스트는 8년간 자본이
# 수천 달러로 불어나므로, '지금 $333 계좌에서 어떤가'를 물으면 뒤쪽 구간이
# 답을 지배해버린다. 이 값을 켜면 항상 그 자본으로 크기를 잡는다.
FIXED_CAPITAL = 0.0

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "outputs")
LOG_FILE = os.path.join(OUT_DIR, "harness.log")
LOCK_FILE = os.path.join(OUT_DIR, ".harness.lock")

DISCLAIMER = (
    "⚠️ 완벽체결·슬리피지 0 가정, 펀딩 제외, 과거 국면 실측치이며 미래 보장 아님.\n"
    "   유동성/호가 데이터가 없어 유동성 관문은 생략했다.\n"
    "   한 봉 안에서 익절·손절이 모두 닿으면 손절을 먼저 적용한다(보수적).\n")


def _log(msg: str, quiet: bool = False) -> None:
    line = f"[{datetime.now():%m-%d %H:%M:%S}] {msg}"
    if not quiet:
        print(line, flush=True)
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ─────────────────────────── 데이터 ───────────────────────────

_FNG: dict | None = None


def _fng_map(log=_log) -> dict:
    """일자(UTC) → 공포탐욕지수. 실거래 국면 필터의 과거 재생용.
    alternative.me 전체 히스토리(2018-02~)를 7일 캐시. 실패하면 빈 dict ,
    그 구간은 필터 없이 재생된다(지수가 없던 2018 이전과 동일 취급)."""
    global _FNG
    if _FNG is not None:
        return _FNG
    cache = os.path.join(OUT_DIR, "fng_history.json")
    data = None
    try:
        if (os.path.exists(cache)
                and time.time() - os.path.getmtime(cache) < 7 * 86400):
            with open(cache, encoding="utf-8") as f:
                data = json.load(f)
    except Exception:
        data = None
    if data is None:
        try:
            import requests
            r = requests.get("https://api.alternative.me/fng/?limit=0",
                             timeout=20)
            data = r.json().get("data", [])
            os.makedirs(OUT_DIR, exist_ok=True)
            with open(cache, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception as e:
            log(f"공포탐욕 히스토리 로드 실패, 해당 구간 필터 없이 재생: {e}")
            data = []
    m = {}
    for row in data or []:
        try:
            day = datetime.fromtimestamp(
                int(row["timestamp"]), tz=timezone.utc).strftime("%Y-%m-%d")
            m[day] = int(row["value"])
        except Exception:
            continue
    _FNG = m
    return m


def load_bars(client, symbol: str, interval: str, start_year: int, log=_log):
    """OHLC 로드 + 시작연도 이후로 자르기."""
    from .historical_data import extended_ohlc
    bars = extended_ohlc(client, symbol, interval, log=lambda m: log(m, True))
    if not bars:
        return []
    cut = int(datetime(start_year, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    return [b for b in bars if b["t"] >= cut]


# ─────────────────────── 신호 사전계산 ───────────────────────

def precompute(bars: list[dict]):
    """이 심볼·시간봉의 신호별 발생 지점과 지평 결과를 미리 계산.

    승률은 '그 시점까지 알 수 있던 것'만으로 재야 하므로(미래참조 금지),
    발생 목록만 만들어 두고 백테스트 진행에 맞춰 포인터를 밀며 누적한다.
    """
    closes = [b["c"] for b in bars]
    sigs = ss._signals(ss._series(closes))
    n = len(closes)
    out = {}
    for name, (side, cond) in sigs.items():
        fires = []          # (발생봉, 지평결과), 지평결과는 채점용
        live = []           # 그 봉에 켜졌는지 (진입 판정용)
        for i in range(n):
            try:
                on = bool(cond(i))
            except (TypeError, IndexError):
                on = False
            live.append(on)
            if on and 220 <= i < n - ss.FWD and closes[i] > 0:
                m = closes[i + ss.FWD] / closes[i] - 1.0
                fires.append((i, m if side == "long" else -m))
        if fires:
            out[name] = {"side": side, "fires": fires, "live": live, "ptr": 0,
                         "closes": closes,
                         "n": 0, "wins": [], "losses": [],
                         # 경로 EV 누적, 발생분 하나를 딱 한 번만 평가한다
                         "path_ptr": 0, "path_sum": 0.0, "path_n": 0}
    return out


def _advance(st: dict, i: int) -> None:
    """봉 i 시점에 '이미 채점이 끝난' 발생분까지만 누적한다.
    fire + FWD <= i 조건이 미래참조를 막는 핵심이다."""
    f = st["fires"]
    p = st["ptr"]
    while p < len(f) and f[p][0] + ss.FWD <= i:
        m = f[p][1]
        (st["wins"] if m > 0 else st["losses"]).append(abs(m))
        st["n"] += 1
        p += 1
    st["ptr"] = p


# ─────────────────────── 진입 후보 평가 ───────────────────────

_BASE_CACHE = {}


def _baseline_up(closes: list, i: int) -> float:
    """직전 3000봉에서 '그냥 롱'의 승률. 500봉 단위로만 다시 센다 ,
    이 값은 천천히 변하는데 봉마다 계산하면 그 자체가 O(n²)이다."""
    key = (id(closes), i // 500)
    hit = _BASE_CACHE.get(key)
    if hit is not None:
        return hit
    seg = closes[max(0, i - 3000):i]
    if len(seg) > ss.FWD + 50:
        ups = sum(1 for k in range(len(seg) - ss.FWD)
                  if seg[k + ss.FWD] > seg[k])
        val = ups / (len(seg) - ss.FWD)
    else:
        val = 0.5
    if len(_BASE_CACHE) > 5000:
        _BASE_CACHE.clear()
    _BASE_CACHE[key] = val
    return val


def evaluate(st: dict, name: str, tf: str, bars: list[dict], i: int,
             policy: dict, graded: dict, budget_usd: float, max_lev: int):
    """봉 i에서 이 신호로 진입할 만한가. 되면 셋업 dict, 아니면 None.
    signal_scanner.evaluate_setups 와 같은 순서·같은 관문을 쓴다."""
    if st["n"] < MIN_SAMPLES:
        return None
    wins, losses = st["wins"], st["losses"]
    pwin = len(wins) / st["n"]
    side = st["side"]
    closes = st["closes"]

    # 기준선: 이 구간에서 그냥 롱/숏 했을 때의 승률 (실력인지 시장 덕인지 분리)
    # 봉마다 3000봉을 다시 세면 이것만으로도 O(n²)이라 500봉 단위로 캐시한다.
    base_up = _baseline_up(closes, i)
    base = base_up if side == "long" else 1 - base_up
    if pwin - base < ss.MIN_EDGE:
        return None

    # 증거 수축, 표본이 적으면 기준선 쪽으로 끌어당긴다
    pwin = base + (pwin - base) * (st["n"] / (st["n"] + ss.EVIDENCE_PRIOR))

    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = (sum(losses) / len(losses) if losses else max(avg_win, 0.005))
    sl = max(avg_loss * SIZE_SCALE, 0.005) * ss.SL_WIDTH_MULT  # live parity
    tp = avg_win * SIZE_SCALE
    horizon_h = INTERVAL_MS[tf] * ss.FWD / 3_600_000
    # swing fixed SL/TP + volatility gate removed 2026-08-09: live withdrew
    # them on 2026-08-05 (signal_scanner:641) - keeping them here made the
    # harness replay a config the bot no longer runs (parity bug).
    # measurement-only override (HARNESS_SL_MULT, default 1 = unchanged):
    # widens the stop everywhere it matters - path EV, sizing, leverage
    sl *= float(os.environ.get("HARNESS_SL_MULT", "1") or 1)

    # 기대값은 경로로, 익절·손절 중 먼저 닿는 쪽이 결과다.
    # 새로 채점이 끝난 발생분만 평가해 누적한다. 매번 과거 전체를 다시 훑으면
    # O(n²)이 되어 1시간봉 66,507봉에서는 끝나지 않는다(실측: 첫 시도 중단).
    # 오래된 발생분은 '그때의 sl/tp'로 평가된 채 남는데, 그게 오히려 실전에
    # 가깝다, 그 시점의 판단 기준으로 채점한 것이기 때문이다.
    while st["path_ptr"] < st["ptr"]:
        fi = st["fires"][st["path_ptr"]][0]
        st["path_ptr"] += 1
        e0 = closes[fi]
        if e0 <= 0:
            continue
        r = None
        for k in range(1, ss.FWD + 1):
            if fi + k >= len(bars):
                break
            hi, lo = bars[fi + k]["h"], bars[fi + k]["l"]
            up, dn = hi / e0 - 1.0, lo / e0 - 1.0
            adverse = -dn if side == "long" else up
            favor = up if side == "long" else -dn
            if adverse >= sl:          # 손절 우선 (보수적)
                r = -sl
                break
            if favor >= tp:
                r = tp
                break
        if r is None and fi + ss.FWD < len(closes):
            mv = closes[fi + ss.FWD] / e0 - 1.0
            r = mv if side == "long" else -mv
        if r is not None:
            st["path_sum"] += r
            st["path_n"] += 1
    if st["path_n"] < MIN_SAMPLES:
        return None
    raw_ev = st["path_sum"] / st["path_n"]
    if raw_ev - ss.FEE_RT <= 0:
        return None

    lev = max(1, min(max_lev, int(1 / (2.5 * sl)))) if sl > 0 else 1
    _risk = float(policy.get("risk_per_trade_pct", 0) or 0) or ss.RISK_PCT
    margin = min(budget_usd, budget_usd * _risk / (sl * lev)
                 if sl * lev > 0 else budget_usd)
    margin = max(margin, 10 / lev)
    # 실거래 동일 관문: 너무 잘게 쪼개진 자리는 잡지 않는다 (signal_scanner :730)
    floor_usd = MIN_MARGIN if MIN_MARGIN != 0 else ss.MIN_MARGIN_USD
    if floor_usd > 0 and margin < floor_usd:
        return None

    try:
        from .brain import conviction
        cv = conviction("", name, tf, side, pwin, st["n"], graded)
        conv = float(cv.get("conviction", pwin))
    except Exception:
        conv = pwin
    if conv < float(policy.get("min_conviction", 0.52) or 0):
        return None
    if pwin < float(policy.get("min_win_rate", 0.45) or 0):
        return None

    return {"signal": name, "side": side, "interval": tf, "win_rate": pwin,
            "n_samples": st["n"], "tp_move": tp, "sl_move": sl,
            "leverage": lev, "margin_usd": margin, "raw_ev": raw_ev,
            "conviction": conv, "horizon_hours": horizon_h,
            "depth": st["n"] / (st["n"] + ss.EV_SHRINK_PRIOR_N),
            "score": (raw_ev - ss.FEE_RT) * lev * 24 / max(horizon_h, 1e-9)
                     * (0.5 + 0.5 * pwin) * (st["n"] / (st["n"] + ss.EV_SHRINK_PRIOR_N))}


# ─────────────────────────── 엔진 ───────────────────────────

class Backtest:
    def __init__(self, policy: dict, capital: float, adaptive: bool = False):
        self.p = policy
        self.start_capital = capital
        self.equity = capital
        self.adaptive = adaptive
        self.open = {}          # symbol -> position dict
        self.trades = []
        self.curve = []         # (ts, equity)
        self.graded = {}        # 적응 루프용 (signal@tf -> {win,total,pnl})
        self.banned = set()
        self.ban_at = {}        # signal@tf -> 정지 시각 ms (만료 재개용)
        self.size_scale = 1.0
        self.peak = capital
        self.mdd = 0.0

    # ── 사후관리: autonomous.manage_positions 와 같은 규칙 ──
    def manage(self, sym: str, bar: dict, ts: int):
        pos = self.open.get(sym)
        if not pos:
            return
        p = self.p
        hi, lo, close = bar["h"], bar["l"], bar["c"]
        long = pos["side"] == "long"
        entry = pos["entry"]

        # 시간만료, 지평 × 배수, 또는 절대 상한(max_hold_hours) 중 먼저
        mult = float(p.get("expire_after_horizons", 0) or 0)
        cap_h = float(p.get("max_hold_hours", 0) or 0)
        limits = []
        if mult > 0:
            limits.append(pos["horizon_hours"] * mult)
        if cap_h > 0:
            limits.append(cap_h)
        if limits:
            limit_ms = min(limits) * 3_600_000
            if ts - pos["opened_ts"] > limit_ms:
                self._close(sym, close, "expire", ts)
                return

        # 이 봉의 유리/불리 최대폭
        up, dn = hi / entry - 1.0, lo / entry - 1.0
        favor = up if long else -dn
        adverse = -dn if long else up

        # ★ 손절 우선: 한 봉 안에서 둘 다 닿으면 손절로 본다(과대평가 방지)
        # 손절선은 트레일링·본전스탑으로 이익 쪽까지 올라갈 수 있다(sl_dist<0).
        # 그때 청산되면 손실이 아니라 '이익 확정'이므로 사유를 갈라 적는다 ,
        # 안 그러면 리포트에 '손절인데 +2.50' 같은 줄이 생겨 원인분석이 꼬인다.
        sl_dist = pos["sl_dist"]
        if adverse >= sl_dist:
            px = entry * (1 - sl_dist) if long else entry * (1 + sl_dist)
            self._close(sym, px, "trail_stop" if pos["sl_moved"] else "stop_loss", ts)
            return
        if favor >= pos["tp_dist"]:
            px = entry * (1 + pos["tp_dist"]) if long else entry * (1 - pos["tp_dist"])
            self._close(sym, px, "take_profit", ts)
            return

        # 부분 익절 (포지션당 한 번)
        ptp_at = float(p.get("partial_tp_at_pct", 0) or 0)
        ptp_fr = float(p.get("partial_tp_fraction", 0) or 0)
        if ptp_at > 0 and ptp_fr > 0 and not pos["partial_done"] and favor >= ptp_at:
            px = entry * (1 + ptp_at) if long else entry * (1 - ptp_at)
            cut = pos["notional"] * ptp_fr
            # 자본은 청산 시점에 한 번만 반영한다. 여기서 더하고 _close 에서
            # partial_pnl 을 또 더하면 이중계상이 되어 equity 와 trades.csv 의
            # 합이 어긋난다(스모크런에서 총손익 +0.73 vs 자본 +38.67).
            pos["partial_pnl"] = cut * ptp_at - cut * ss.FEE_RT / 2
            pos["notional"] -= cut
            pos["partial_done"] = True

        # 손절선 이동, 트레일링 우선, 유리한 쪽으로만
        be_at = float(p.get("breakeven_at_pct", 0) or 0)
        tr_at = float(p.get("trail_at_pct", 0) or 0)
        tr_gap = float(p.get("trail_gap_pct", 0) or 0)
        new_dist = None
        if tr_at > 0 and favor >= tr_at:
            new_dist = -(favor - tr_gap)      # 진입가 기준 '이익 쪽' 손절선
        elif be_at > 0 and favor >= be_at:
            new_dist = 0.0
        if new_dist is not None and new_dist < pos["sl_dist"]:
            pos["sl_dist"] = new_dist
            pos["sl_moved"] = True

    def _close(self, sym: str, px: float, reason: str, ts: int):
        pos = self.open.pop(sym)
        entry = pos["entry"]
        move = (px / entry - 1.0) if entry > 0 else 0.0
        signed = move if pos["side"] == "long" else -move
        pnl = pos["notional"] * signed - pos["notional"] * ss.FEE_RT
        pnl += pos.get("partial_pnl", 0.0)
        self.equity += pnl
        self.peak = max(self.peak, self.equity)
        self.mdd = max(self.mdd, (self.peak - self.equity) / self.peak
                       if self.peak > 0 else 0.0)
        held_h = (ts - pos["opened_ts"]) / 3_600_000

        attr = ""
        try:
            from . import postmortem as pm
            rec = {"symbol": sym, "side": pos["side"], "signal": pos["signal"],
                   "entry_price": entry, "interval": pos["interval"],
                   "regime": "", "win_rate": pos["win_rate"],
                   "n_samples": pos["n_samples"],
                   "at": datetime.fromtimestamp(pos["opened_ts"] / 1000).isoformat()}
            saved = pm._append
            pm._append = lambda r: None        # 라이브 파일 오염 금지
            try:
                out = pm.analyze_close(rec, px, pnl, reason, "")
                attr = out.get("attribution", "")
            finally:
                pm._append = saved
        except Exception:
            pass

        key = f"{pos['signal']}@{pos['interval']}"
        g = self.graded.setdefault(key, {"win": 0, "total": 0, "pnl": 0.0})
        g["total"] += 1
        g["win"] += int(pnl > 0)
        g["pnl"] += pnl
        if self.adaptive:
            # 실거래 review_and_adapt 와 동일 규칙 (2026-08-07 일치화):
            # 정지 = 승률<floor 이면서 순손익<0 일 때만 · 21일 만료 후 백지 재평가
            # · 승률 floor+10%p 이고 순이익이면 재개
            floor = float(self.p.get("adapt_win_floor", 0.45) or 0)
            need = int(self.p.get("adapt_min_graded_combo",
                                  max(5, int(self.p.get("adapt_min_graded", 8)) // 2)))
            ban_days = float(self.p.get("adapt_ban_days", 21) or 0)
            if ban_days > 0:
                for k in list(self.banned):
                    since = self.ban_at.get(k, 0)
                    if since and (ts - since) / 86_400_000 >= ban_days:
                        self.banned.discard(k)
                        self.ban_at.pop(k, None)
                        if k != key:      # 지금 채점 중인 조합의 성적은 지키고
                            self.graded.pop(k, None)
            if g["total"] >= need:
                wr = g["win"] / g["total"]
                if wr < floor and g["pnl"] < 0 and key not in self.banned:
                    self.banned.add(key)
                    self.ban_at[key] = ts
                elif wr >= floor + 0.10 and g["pnl"] >= 0 and key in self.banned:
                    self.banned.discard(key)
                    self.ban_at.pop(key, None)

        self.trades.append({
            "ts": ts, "time": datetime.fromtimestamp(ts / 1000).isoformat(),
            "symbol": sym, "signal": pos["signal"], "interval": pos["interval"],
            "side": pos["side"], "entry": round(entry, 6), "exit": round(px, 6),
            "reason": reason, "pnl": round(pnl, 4),
            "pnl_pct_margin": round(signed * pos["leverage"] * 100, 3),
            "notional": round(pos["notional"], 2), "leverage": pos["leverage"],
            "held_hours": round(held_h, 2), "win_rate_at_entry": round(pos["win_rate"], 4),
            "n_samples": pos["n_samples"], "attribution": attr,
            "equity_after": round(self.equity, 2)})
        self.curve.append((ts, self.equity))

    def can_open(self, sym: str, side: str, notional: float,
                 leverage: int = 1) -> bool:
        p = self.p
        if sym in self.open:
            return False
        # 증거금이 실제로 있는가, 거래소는 없는 돈으로 포지션을 열어주지 않는다.
        # 2026-08-06까지 이 확인이 없어서, 포지션 상한을 올리면 자본을 넘는
        # 증거금이 필요한 조합도 통과했다(자본 $333에 상한 $600 → 6개면 $720).
        # 그 설정이 백테스트에서 +$24,380 으로 나왔는데 실제로는 열 수조차 없다.
        used_margin = sum(q["notional"] / max(q["leverage"], 1)
                          for q in self.open.values())
        if used_margin + notional / max(leverage, 1) > self.equity:
            return False
        if len(self.open) >= int(p.get("max_concurrent", 3)):
            return False
        net = sum((q["notional"] if q["side"] == "long" else -q["notional"])
                  for q in self.open.values())
        signed = notional if side == "long" else -notional
        cap = self.equity * float(p.get("max_net_exposure_pct", 2.0) or 0)
        if cap > 0 and abs(net + signed) > cap:
            return False
        alloc = float(p.get("trading_alloc", 0) or 0)
        if alloc > 0:
            used = sum(q["notional"] for q in self.open.values())
            if used + notional > self.equity * alloc:
                return False
        return True

    def open_position(self, sym: str, s: dict, bar: dict, ts: int):
        p = self.p
        # 국면(공포탐욕) 필터, 실거래 regime_allows 와 동일: 극단(≤25/≥75)에서
        # 신규 롱 차단, 숏은 안 막음. use_regime_filter: false 로 끌 수 있다.
        if bool(p.get("use_regime_filter", True)) and s["side"] == "long":
            day = datetime.fromtimestamp(
                ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            v = _fng_map().get(day)
            if v is not None and (v <= 25 or v >= 75):
                return False
        conv_mult = 0.7 + s["conviction"] * 0.6
        # EV 비례 배율, 실거래(autonomous)와 같은 식. 단 실거래는 현재 매트릭스
        # EV를 쓰지만 재생에서는 그 시점까지의 경로 EV(raw_ev)를 쓴다 ,
        # 오늘의 매트릭스를 과거에 주입하면 미래참조가 되기 때문.
        lo = float(p.get("ev_size_floor_pct", 0.0) or 0.0)
        hi = float(p.get("ev_size_cap_pct", 0.05) or 0.05)
        frac = (s.get("raw_ev", 0.0) - lo) / (hi - lo) if hi > lo else 0.5
        ev_mult = max(0.5, min(1.5, 0.5 + frac))
        notional = min(s["margin_usd"] * s["leverage"] * self.size_scale
                       * ev_mult * conv_mult,
                       float(p.get("max_position_usd", 3000) or 3000))
        if notional < 10 or not self.can_open(sym, s["side"], notional,
                                              s["leverage"]):
            return False
        self.open[sym] = {
            "side": s["side"], "signal": s["signal"], "interval": s["interval"],
            "entry": bar["c"], "notional": notional, "leverage": s["leverage"],
            "tp_dist": s["tp_move"], "sl_dist": s["sl_move"],
            "horizon_hours": s["horizon_hours"], "opened_ts": ts,
            "win_rate": s["win_rate"], "n_samples": s["n_samples"],
            "partial_done": False, "partial_pnl": 0.0, "sl_moved": False}
        return True


# ─────────────────────────── 실행 ───────────────────────────

def run(coins, intervals, start_year, capital, adaptive, log=_log,
        regime: bool | None = None):
    from .autonomous import load_policy, make_client
    policy = load_policy()
    if regime is not None:            # A/B용 강제 켬/끔 (기본은 정책값)
        policy = dict(policy)
        policy["use_regime_filter"] = regime
    client = make_client(policy)
    max_lev = int(policy.get("max_leverage", 5) or 5)

    log(f"백테스트 시작, 종목 {','.join(coins)} · 시간봉 {','.join(intervals)} · "
        f"{start_year}년~ · 자본 ${capital:,.0f} · 적응루프 {'ON' if adaptive else 'OFF'}")

    data = {}
    for sym in coins:
        for tf in intervals:
            bars = load_bars(client, sym, tf, start_year, log=log)
            if len(bars) < 400:
                log(f"  {sym} {tf}: 봉 {len(bars)}개, 건너뜀")
                continue
            data[(sym, tf)] = {"bars": bars, "sig": precompute(bars)}
            log(f"  {sym} {tf}: {len(bars)}봉 · 신호 {len(data[(sym,tf)]['sig'])}종")
    if not data:
        log("데이터가 없어 중단")
        return None

    # 전 종목·전 시간봉을 시각순으로 병합, 포트폴리오 한도가 제대로 걸리려면
    # 심볼별로 따로 돌리면 안 되고 하나의 시간축에서 처리해야 한다.
    events = []
    for key, d in data.items():
        for i, b in enumerate(d["bars"]):
            if i >= 220:
                events.append((b["t"], key, i))
    events.sort(key=lambda e: e[0])
    log(f"총 {len(events):,}개 봉 이벤트 처리 시작")

    bt = Backtest(policy, capital, adaptive)
    last_report = time.time()
    for idx, (ts, key, i) in enumerate(events):
        sym, tf = key
        d = data[key]
        bar = d["bars"][i]
        bt.manage(sym, bar, ts)

        cands = []
        for name, st in d["sig"].items():
            _advance(st, i)
            if not st["live"][i]:
                continue
            if f"{name}@{tf}" in bt.banned:
                continue
            s = evaluate(st, name, tf, d["bars"], i, policy, bt.graded,
                         FIXED_CAPITAL or bt.equity, max_lev)
            if s:
                cands.append(s)
        for s in sorted(cands, key=lambda x: -x["score"]):
            if bt.open_position(sym, s, bar, ts):
                break

        if time.time() - last_report > 30:
            last_report = time.time()
            log(f"[진행] {idx:,}/{len(events):,}봉 ({idx/len(events)*100:.0f}%) · "
                f"누적 트레이드 {len(bt.trades)}건 · 자본 ${bt.equity:,.0f}", True)

    # 남은 포지션은 마지막 종가로 정리
    for sym in list(bt.open):
        for key, d in data.items():
            if key[0] == sym:
                bt._close(sym, d["bars"][-1]["c"], "end_of_test",
                          d["bars"][-1]["t"])
                break
    log(f"완료, 트레이드 {len(bt.trades)}건 · 최종 자본 ${bt.equity:,.2f}")
    return bt


# ─────────────────────────── 리포트 ───────────────────────────

def report(bt: Backtest, log=_log):
    os.makedirs(OUT_DIR, exist_ok=True)
    T = bt.trades
    if not T:
        log("트레이드가 0건이라 리포트를 생성하지 않았다.")
        return

    with open(os.path.join(OUT_DIR, "trades.csv"), "w", newline="",
              encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(T[0].keys()))
        w.writeheader()
        w.writerows(T)
    with open(os.path.join(OUT_DIR, "equity_curve.csv"), "w", newline="",
              encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["time", "equity"])
        for ts, eq in bt.curve:
            w.writerow([datetime.fromtimestamp(ts / 1000).isoformat(),
                        round(eq, 2)])

    wins = [t for t in T if t["pnl"] > 0]
    losses = [t for t in T if t["pnl"] <= 0]
    aw = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
    al = -sum(t["pnl"] for t in losses) / len(losses) if losses else 0
    total = sum(t["pnl"] for t in T)

    def group(keyfn, rows=T):
        g = {}
        for t in rows:
            k = keyfn(t)
            e = g.setdefault(k, {"n": 0, "w": 0, "pnl": 0.0})
            e["n"] += 1
            e["w"] += int(t["pnl"] > 0)
            e["pnl"] += t["pnl"]
        return g

    L = []
    L.append(DISCLAIMER)
    L.append("=" * 66)
    L.append("전체 성적")
    L.append("=" * 66)
    L.append(f"  트레이드      {len(T):,}건")
    L.append(f"  승률          {len(wins)/len(T)*100:.1f}%  ({len(wins)}승 {len(losses)}패)")
    L.append(f"  평균 이익     ${aw:+.3f}")
    L.append(f"  평균 손실     ${-al:+.3f}")
    L.append(f"  손익비        {aw/al:.2f}" if al else "  손익비        ,")
    L.append(f"  기대값        ${total/len(T):+.4f} / 트레이드")
    L.append(f"  총손익        ${total:+,.2f}")
    L.append(f"  자본          ${bt.start_capital:,.0f} → ${bt.equity:,.2f} "
             f"({(bt.equity/bt.start_capital-1)*100:+.1f}%)")
    L.append(f"  최대낙폭      {bt.mdd*100:.1f}%")

    for title, fn in (("신호별", lambda t: t["signal"]),
                      ("시간봉별", lambda t: t["interval"]),
                      ("종목별", lambda t: t["symbol"]),
                      ("청산사유별", lambda t: t["reason"])):
        L.append("")
        L.append(f"── {title} " + "─" * (60 - len(title)))
        L.append(f"  {'구분':<22}{'건수':>7}{'승률':>8}{'총손익':>12}{'건당':>10}")
        for k, v in sorted(group(fn).items(), key=lambda x: -x[1]["pnl"]):
            L.append(f"  {str(k)[:22]:<22}{v['n']:>7}{v['w']/v['n']*100:>7.1f}%"
                     f"{v['pnl']:>+12.2f}{v['pnl']/v['n']:>+10.3f}")

    L.append("")
    L.append("── 귀인(왜 이겼나/졌나) " + "─" * 42)
    for k, v in sorted(group(lambda t: t["attribution"] or "(미분류)").items(),
                       key=lambda x: -x[1]["n"]):
        L.append(f"  {str(k)[:22]:<22}{v['n']:>7}{v['w']/v['n']*100:>7.1f}%"
                 f"{v['pnl']:>+12.2f}")

    txt = "\n".join(L)
    with open(os.path.join(OUT_DIR, "summary.txt"), "w", encoding="utf-8") as f:
        f.write(txt + "\n")

    # ── 패배 분석 ──
    LL = [DISCLAIMER, "=" * 66, "패배 분석", "=" * 66, ""]
    ag = group(lambda t: t["attribution"] or "(미분류)", losses)
    adverse = ag.get("역행패배", {"n": 0})["n"]
    headwind = ag.get("시장역풍", {"n": 0})["n"]
    LL.append(f"  역행패배 {adverse}건, 유리한 국면인데 짐 = 신호 자체가 나쁘다")
    LL.append(f"  시장역풍 {headwind}건, 시장이 반대로 간 것 = 신호 탓 아님")
    LL.append("")
    LL.append("  ※ 역행패배가 많은 신호부터 손봐야 한다. 시장역풍은 국면 필터의 몫.")
    LL.append("")
    LL.append("── 신호별 패배 구성 " + "─" * 46)
    LL.append(f"  {'신호':<22}{'패배':>6}{'역행':>7}{'역풍':>7}{'손실합':>11}")
    weak = []
    for sig, rows in sorted(
            {s: [t for t in losses if t["signal"] == s]
             for s in {t["signal"] for t in losses}}.items(),
            key=lambda x: sum(t["pnl"] for t in x[1])):
        adv = sum(1 for t in rows if t["attribution"] == "역행패배")
        hw = sum(1 for t in rows if t["attribution"] == "시장역풍")
        loss = sum(t["pnl"] for t in rows)
        LL.append(f"  {sig[:22]:<22}{len(rows):>6}{adv:>7}{hw:>7}{loss:>+11.2f}")
        weak.append((loss, sig, len(rows), adv))
    with open(os.path.join(OUT_DIR, "losses.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(LL) + "\n")

    log("")
    for line in txt.split("\n")[:22]:
        log(line)
    log("")
    log("── 상위 취약 신호 5개 (총손실 큰 순) " + "─" * 26)
    for loss, sig, n, adv in sorted(weak)[:5]:
        log(f"  {sig[:24]:<24} 손실 {loss:+9.2f} · 패배 {n}건 중 역행 {adv}건")
    log("")
    log(f"저장: {OUT_DIR}")
    log("  summary.txt · trades.csv · equity_curve.csv · losses.txt")


def spawn_background(argv: list[str]) -> bool:
    """백그라운드로 자기 자신을 띄운다. 이미 돌고 있으면 False."""
    os.makedirs(OUT_DIR, exist_ok=True)
    if os.path.exists(LOCK_FILE):
        try:
            if time.time() - os.path.getmtime(LOCK_FILE) < 12 * 3600:
                return False
        except OSError:
            pass
    with open(LOCK_FILE, "w") as f:
        f.write(str(int(time.time())))
    cmd = [sys.executable, "-m", "ocean_agent.harness"] + argv + ["--_child"]
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NEW_CONSOLE  # type: ignore[attr-defined]
    subprocess.Popen(cmd, cwd=os.path.dirname(OUT_DIR), **kw)
    return True


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="풀 백테스트 하네스")
    p.add_argument("--coins", nargs="+", default=["BTC", "ETH", "SOL"])
    p.add_argument("--intervals", nargs="+", default=["1h", "4h", "1d"])
    p.add_argument("--start", type=int, default=2019, help="시작 연도")
    p.add_argument("--capital", type=float, default=1000.0)
    p.add_argument("--adaptive", action="store_true",
                   help="자가정지·사이즈배율 적응 루프까지 재생 (기본 off)")
    p.add_argument("--smoke", action="store_true",
                   help="스모크런, BTC 1d 만 빠르게")
    p.add_argument("--background", action="store_true", help="백그라운드 실행")
    p.add_argument("--no-regime", action="store_true",
                   help="국면(공포탐욕) 필터 끄고 재생 (A/B 대조용)")
    p.add_argument("--_child", action="store_true", help=argparse.SUPPRESS)
    a = p.parse_args()

    if a.smoke:
        a.coins, a.intervals, a.start = ["BTC"], ["1d"], 2019

    if a.background and not a._child:
        argv = ["--coins"] + a.coins + ["--intervals"] + a.intervals + \
               ["--start", str(a.start), "--capital", str(a.capital)]
        if a.adaptive:
            argv.append("--adaptive")
        if a.no_regime:
            argv.append("--no-regime")
        if spawn_background(argv):
            print(f"백그라운드로 시작했습니다. 진행상황: {LOG_FILE}")
        else:
            print("이미 백테스트가 돌고 있습니다.")
        return

    try:
        bt = run(a.coins, a.intervals, a.start, a.capital, a.adaptive,
                 regime=False if a.no_regime else None)
        if bt:
            report(bt)
    finally:
        if a._child:
            try:
                os.remove(LOCK_FILE)
            except OSError:
                pass


if __name__ == "__main__":
    main()
