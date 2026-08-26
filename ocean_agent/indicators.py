"""차트 지표 분석, 파시피카 캔들 데이터로 주요 기술 지표를 계산한다.

외부 라이브러리 없이 순수 파이썬으로 구현 (의존성 최소 원칙).
지표: MA(20/50/200), RSI(14), 스토캐스틱(14,3), 스토캐스틱 RSI(14,14,3,3),
     MACD(12,26,9), 볼린저밴드(20,2σ), ATR(14), VWAP(24h)

실행: python -m ocean_agent.indicators BTC --interval 1h
"""

import argparse
import sys
import time

from .api_client import PacificaClient

INTERVAL_MS = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
               "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000,
               "4h": 14_400_000, "8h": 28_800_000, "12h": 43_200_000,
               "1d": 86_400_000, "1w": 604_800_000}


def fetch_klines(client: PacificaClient, symbol: str, interval: str = "1h",
                 count: int = 300) -> list[dict]:
    """최근 count개 캔들 (과거→현재)."""
    step = INTERVAL_MS.get(interval, 3_600_000)
    end = int(time.time() * 1000)
    start = end - step * (count + 5)
    r = client.session.get(f"{client.base}/kline", params={
        "symbol": symbol, "interval": interval,
        "start_time": start, "end_time": end}, timeout=20).json()
    if not r.get("success"):
        raise RuntimeError(f"{symbol} 캔들 조회 실패: {r.get('error')}")
    data = r["data"]
    # 마지막 미완성 캔들 제거, 진행중 봉의 종가로 지표를 계산하면 값이 계속 변한다
    now = int(time.time() * 1000)
    while data and int(data[-1]["t"]) + step > now:
        data.pop()
    return data


# ---------- 지표 계산 (순수 파이썬) ----------

def sma(vals: list[float], n: int) -> float | None:
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def ema_series(vals: list[float], n: int) -> list[float]:
    if len(vals) < n:
        return []
    k = 2 / (n + 1)
    out = [sum(vals[:n]) / n]
    for v in vals[n:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi_series(closes: list[float], n: int = 14) -> list[float]:
    if len(closes) <= n:
        return []
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_g = sum(gains[:n]) / n
    avg_l = sum(losses[:n]) / n
    out = []
    for i in range(n, len(gains) + 1):
        rs = avg_g / avg_l if avg_l > 0 else float("inf")
        out.append(100 - 100 / (1 + rs) if avg_l > 0 else 100.0)
        if i < len(gains):
            avg_g = (avg_g * (n - 1) + gains[i]) / n
            avg_l = (avg_l * (n - 1) + losses[i]) / n
    return out


def stochastic(highs, lows, closes, n: int = 14, d: int = 3):
    if len(closes) < n + d:
        return None, None
    ks = []
    for i in range(n - 1, len(closes)):
        hh = max(highs[i - n + 1:i + 1])
        ll = min(lows[i - n + 1:i + 1])
        ks.append((closes[i] - ll) / (hh - ll) * 100 if hh > ll else 50.0)
    return ks[-1], sum(ks[-d:]) / d


def stoch_rsi(closes, rsi_n: int = 14, stoch_n: int = 14, k_n: int = 3, d_n: int = 3):
    rsis = rsi_series(closes, rsi_n)
    if len(rsis) < stoch_n + k_n + d_n:
        return None, None
    raw = []
    for i in range(stoch_n - 1, len(rsis)):
        window = rsis[i - stoch_n + 1:i + 1]
        hi, lo = max(window), min(window)
        raw.append((rsis[i] - lo) / (hi - lo) * 100 if hi > lo else 50.0)
    ks = [sum(raw[i - k_n + 1:i + 1]) / k_n for i in range(k_n - 1, len(raw))]
    if len(ks) < d_n:
        return None, None
    return ks[-1], sum(ks[-d_n:]) / d_n


def macd(closes, fast: int = 12, slow: int = 26, signal: int = 9):
    if len(closes) < slow + signal:
        return None, None, None
    ef = ema_series(closes, fast)
    es = ema_series(closes, slow)
    line = [f - s for f, s in zip(ef[-len(es):], es)]
    sig = ema_series(line, signal)
    if not sig:
        return None, None, None
    return line[-1], sig[-1], line[-1] - sig[-1]


def bollinger(closes, n: int = 20, mult: float = 2.0):
    if len(closes) < n:
        return None, None, None, None
    window = closes[-n:]
    mid = sum(window) / n
    var = sum((c - mid) ** 2 for c in window) / n
    sd = var ** 0.5
    price = closes[-1]
    pos = (price - mid) / sd if sd > 0 else 0.0
    return mid + mult * sd, mid, mid - mult * sd, pos


def atr(highs, lows, closes, n: int = 14) -> float | None:
    if len(closes) < n + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(highs[i] - lows[i],
                       abs(highs[i] - closes[i - 1]),
                       abs(lows[i] - closes[i - 1])))
    a = sum(trs[:n]) / n
    for t in trs[n:]:
        a = (a * (n - 1) + t) / n
    return a


def vwap_24h(kl: list[dict], interval: str) -> float | None:
    step = INTERVAL_MS.get(interval, 3_600_000)
    bars = max(1, int(86_400_000 / step))
    recent = kl[-bars:]
    pv = v = 0.0
    for c in recent:
        typ = (float(c["h"]) + float(c["l"]) + float(c["c"])) / 3
        vol = float(c["v"])
        pv += typ * vol
        v += vol
    return pv / v if v > 0 else None


# ---------- 종합 분석 ----------

def zone(x: float, low: float, high: float, low_txt: str, high_txt: str) -> str:
    if x >= high:
        return high_txt
    if x <= low:
        return low_txt
    return "중립"


def analyze(client: PacificaClient, symbol: str, interval: str = "1h") -> str:
    kl = fetch_klines(client, symbol, interval)
    if len(kl) < 60:
        return f"{symbol} 캔들이 부족합니다 ({len(kl)}개), 상장 초기 코인일 수 있음"
    closes = [float(c["c"]) for c in kl]
    highs = [float(c["h"]) for c in kl]
    lows = [float(c["l"]) for c in kl]
    price = closes[-1]

    ma20, ma50, ma200 = sma(closes, 20), sma(closes, 50), sma(closes, 200)
    r = rsi_series(closes)
    rsi_v = r[-1] if r else None
    st_k, st_d = stochastic(highs, lows, closes)
    srsi_k, srsi_d = stoch_rsi(closes)
    m_line, m_sig, m_hist = macd(closes)
    bb_up, bb_mid, bb_lo, bb_pos = bollinger(closes)
    atr_v = atr(highs, lows, closes)
    vw = vwap_24h(kl, interval)

    lines = [f"{symbol} · {interval} 차트 분석 (현재가 {price:,.6g})"]

    if ma20 and ma50:
        if ma200:
            if ma20 > ma50 > ma200:
                trend = "정배열 (상승 추세)"
            elif ma20 < ma50 < ma200:
                trend = "역배열 (하락 추세)"
            else:
                trend = "혼조 (추세 전환 구간 가능)"
            lines.append(f"추세: MA20 {ma20:,.6g} / MA50 {ma50:,.6g} / "
                         f"MA200 {ma200:,.6g} → {trend}")
        else:
            lines.append(f"추세: MA20 {ma20:,.6g} / MA50 {ma50:,.6g} "
                         f"({'상승' if ma20 > ma50 else '하락'} 우위, MA200은 데이터 부족)")
    if rsi_v is not None:
        lines.append(f"RSI(14): {rsi_v:.1f} → {zone(rsi_v, 30, 70, '과매도', '과매수')}")
    if srsi_k is not None:
        lines.append(f"스토캐스틱 RSI: K {srsi_k:.1f} / D {srsi_d:.1f} → "
                     f"{zone(srsi_k, 20, 80, '과매도 (반등 주시)', '과매수 (조정 주시)')}")
    if st_k is not None:
        lines.append(f"스토캐스틱(14,3): K {st_k:.1f} / D {st_d:.1f} → "
                     f"{zone(st_k, 20, 80, '과매도', '과매수')}")
    if m_hist is not None:
        cross = "골든크로스 상태" if m_hist > 0 else "데드크로스 상태"
        lines.append(f"MACD: 히스토그램 {m_hist:+.6g} → {cross}")
    if bb_pos is not None:
        lines.append(f"볼린저(20,2σ): 현재가는 중심선 대비 {bb_pos:+.2f}σ "
                     f"(상단 {bb_up:,.6g} / 하단 {bb_lo:,.6g})")
    if atr_v is not None:
        lines.append(f"ATR(14): {atr_v:,.6g} (캔들당 평균 변동 {atr_v / price:.2%})")
    if vw is not None:
        lines.append(f"VWAP(24h): {vw:,.6g} → 현재가는 VWAP 대비 {price / vw - 1:+.2%}")

    lines.append("주의: 지표는 과거 가격의 요약일 뿐 예측을 보장하지 않음. "
                 "판단 재료로만 사용할 것.")
    return "\n".join(lines)


MULTI_TFS = ("1h", "4h", "12h", "1d")
TF_LABEL = {"1h": "단타", "4h": "단중기", "12h": "스윙", "1d": "장타"}


def analyze_multi(client: PacificaClient, symbol: str,
                  intervals=MULTI_TFS) -> str:
    """한 코인을 여러 시간봉에서 동시 분석 + 신호별 실측 적중률.

    각 시간봉마다:
    - 현재 상태 스냅샷 (추세/RSI/MACD/볼린저)
    - 이 코인·이 시간봉에서 통계 관문(표본30+/엣지+5%p/EV>0)을 통과한
      신호들의 과거 승률, 지금 점등 중이면 🔆 표시
    """
    from .signal_scanner import (FEE_RT, MIN_EDGE, MIN_N, _series,
                                 _signals, fetch_bars)
    # 24 hours per timeframe, matching the bracket and the gate, instead
    # of 24 candles, which showed a daily chart a 576-hour statistic.
    from .rematrix import _fwd_for

    lines = [f"{symbol} 멀티 시간봉 분석, 실측 적중률 포함", ""]
    live_picks = []
    any_calibrated: list[bool] = []

    for tf in intervals:
        fwd = _fwd_for(tf)
        # fetch_bars, not fetch_closes: the wrapper drops the wicks and the
        # fractals shown to the user would then be a different definition
        # from the one the bot trades on.
        ohlc = fetch_bars(client, symbol, tf, max_bars=1500)
        closes = [b[4] for b in ohlc]
        n = len(closes)
        label = f"[{tf} · {TF_LABEL.get(tf, tf)}]"
        if n < 200 + fwd:
            lines.append(f"{label} 캔들 부족 ({n}개), 통계 생략")
            lines.append("")
            continue
        s = _series(closes, [b[2] for b in ohlc], [b[3] for b in ohlc])

        # --- 현재 상태 스냅샷 ---
        rsi_v = s["rsi"][-1]
        ma20, ma50 = s["ma20"][-1], s["ma50"][-1]
        hist_v = s["hist"][-1]
        bb_v = s["bbpos"][-1]
        trend = ("상승 우위" if ma20 and ma50 and ma20 > ma50 else
                 "하락 우위" if ma20 and ma50 else "판단 불가")
        snap = [trend]
        if rsi_v is not None:
            snap.append(f"RSI {rsi_v:.0f}")
        if hist_v is not None:
            snap.append("MACD 골든" if hist_v > 0 else "MACD 데드")
        if bb_v is not None:
            snap.append(f"BB {bb_v:+.1f}σ")
        lines.append(f"{label} " + " · ".join(snap))

        # --- 실측 적중률 (이 코인·이 시간봉) ---
        ups = sum(1 for i in range(n - fwd) if closes[i + fwd] > closes[i])
        base_up = ups / (n - fwd)
        horizon_h = INTERVAL_MS[tf] * fwd / 3_600_000
        hz_txt = (f"{horizon_h:.0f}시간" if horizon_h < 24
                  else f"{horizon_h/24:.0f}일")

        found = []
        for name, (side, cond) in _signals(s).items():
            moves = []
            for i in range(60, n - fwd):
                try:
                    if not cond(i):
                        continue
                except (TypeError, IndexError):
                    continue
                m = closes[i + fwd] / closes[i] - 1
                moves.append(m if side == "long" else -m)
            if len(moves) < MIN_N:
                continue
            wins = [m for m in moves if m > 0]
            pwin = len(wins) / len(moves)
            # 이 화면의 숫자도 봇과 같은 기준이어야 한다. 이 코인·이 시간봉만으로
            # 낸 백테스트 승률은 워크포워드 실측에서 55%를 넘는 순간 신뢰를 잃었다
            # (백테 73% → 실제 51%). 사람이 이 화면을 보고 판단하는데 봇만 보정된
            # 숫자를 쓰면, 둘이 서로 다른 세계를 보게 된다.
            raw_pwin = pwin
            calibrated_here = False
            try:
                from .walkforward import calibrated, measured_winrate
                m = measured_winrate(name, tf)
                if m is not None:
                    pwin = m[0]
                    calibrated_here = True
                else:
                    c = calibrated(pwin)
                    if c is not None:
                        pwin = c
                        calibrated_here = True
            except Exception:
                pass
            # Whether this screen is showing a measured number or a raw
            # backtest one, tracked so the footer can say which. A fresh
            # install has no calibration table at all until its first
            # walk-forward, and the footer used to promise "measured" while
            # showing the backtest figure, which is exactly the 70% the
            # top_setups docstring calls a bug.
            any_calibrated.append(calibrated_here)
            base = base_up if side == "long" else 1 - base_up
            if pwin - base < MIN_EDGE:
                continue
            avg_win = sum(wins) / len(wins) if wins else 0
            losses = [-m for m in moves if m <= 0]
            avg_loss = sum(losses) / len(losses) if losses else 0.001
            if pwin * avg_win - (1 - pwin) * avg_loss - FEE_RT <= 0:
                continue
            try:
                live = bool(cond(n - 1))
            except (TypeError, IndexError):
                live = False
            # 봇이 실제로 진입을 막는 신호인지 함께 표시한다. 이 화면만 보고
            # "통계 우위"라 읽으면, 봇이 장기 측정으로 걸러낸 것을 사람이 다시
            # 집어들게 된다 (예: RSI>70 과열은 매트릭스 EV -3.3%로 차단 대상).
            try:
                from .signal_scanner import _matrix_rejects
                blocked = _matrix_rejects(name, tf)
            except Exception:
                blocked = False
            found.append((live, name, side, pwin, len(moves), raw_pwin, blocked))
            if live and not blocked:
                live_picks.append((tf, name, side, pwin, len(moves), hz_txt))

        if found:
            found.sort(key=lambda x: (-x[0], -x[3]))
            for live, name, side, pwin, cnt, raw, blocked in found:
                mark = "🔆 점등!" if live else "대기"
                gap = (f", 이 코인 백테 {raw:.0%}"
                       if abs(raw - pwin) >= 0.03 else "")
                tag = " ⛔ 봇 차단(장기 측정상 기준선 미달)" if blocked else ""
                lines.append(f"    {mark} {name} → {'롱' if side=='long' else '숏'} "
                             f"(실측 승률 {pwin:.0%}{gap}, n={cnt}, "
                             f"지평 ~{hz_txt}){tag}")
        else:
            lines.append("    통계 우위 신호 없음 (이 시간봉에선 지표 엣지 미검출)")
        lines.append("")

    # --- 종합 ---
    if live_picks:
        best = max(live_picks, key=lambda x: x[3])
        tf, name, side, pwin, cnt, hz = best
        lines.append(f"종합: 지금 점등 중 최강 = {tf} {name} → "
                     f"{'롱' if side=='long' else '숏'} (승률 {pwin:.0%}, n={cnt})")
    else:
        lines.append("종합: 지금 점등된 통계 우위 신호 없음, 위 '대기' 신호들이 "
                     "켜질 때가 진입 후보")
    if any_calibrated and all(any_calibrated):
        lines.append("주의: 승률은 워크포워드 실측값입니다. 과거 시점에서 "
                     "예측하고 그 뒤 실제 결과와 대조한 값입니다. 이 시장의 "
                     "현실적 천장은 백테스트가 보여주는 것보다 훨씬 낮아서, "
                     "이 코인만의 백테스트가 높게 나와도 실전에서는 50%대로 "
                     "내려옵니다. 미래 보장 아님, market_context로 국면 확인 "
                     "병행 권장.")
    else:
        lines.append("⚠️ 주의: 이 화면의 승률은 아직 보정되지 않은 **백테스트 "
                     "값**입니다. 워크포워드 실측표가 이 컴퓨터에 아직 없거나 "
                     "지금 신호 정의보다 낡아서, 과거를 되돌아본 숫자를 그대로 "
                     "보여주고 있습니다. 이 시장의 현실적 천장은 이보다 훨씬 "
                     "낮고, 60%를 넘는 값이 보이면 그것은 기회가 아니라 보정이 "
                     "안 됐다는 뜻입니다. 첫 워크포워드 측정이 끝나면 이 문구가 "
                     "사라집니다.")
    return "\n".join(lines)


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("symbol", nargs="?", default="BTC")
    p.add_argument("--interval", default="1h",
                   choices=list(INTERVAL_MS.keys()) + ["multi"])
    p.add_argument("--url", default="https://api.pacifica.fi")
    args = p.parse_args()
    client = PacificaClient(args.url)
    if args.interval == "multi":
        print(analyze_multi(client, args.symbol))
    else:
        print(analyze(client, args.symbol, args.interval))


if __name__ == "__main__":
    main()
