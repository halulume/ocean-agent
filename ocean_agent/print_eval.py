"""Print 공정가 평가기.

Pacifica Print: 목표가를 걸어두면 24시간 사이클마다 APY를 받고,
사이클 종료 시점에 가격이 목표를 넘어 있으면 목표가로 체결된다.

= "24시간 뒤 가격이 목표를 넘으면 목표가에 사주기로 약속하고 프리미엄을 받는 것".
체결되는 순간 시장가는 목표가보다 더 나가 있을 수 있으므로(오버슛),
받는 APY가 그 기대 손실보다 커야 남는 장사다.

이 모듈은 과거 시간당 봉(바이낸스 선물 2019-09~ + 파시피카 최근, 약 7년)으로
- 체결 확률: 24시간 뒤 가격이 목표 거리 이상 움직였던 비율
- 평균 오버슛: 체결됐을 때 시장가가 목표를 얼마나 지나쳤는지
- 손익분기 APY: 그 기대 손실을 상쇄하는 데 필요한 최소 APY
를 계산한다. 웹 UI에 표시된 APY가 손익분기보다 높으면 통계적으로 유리.

실행: python -m ocean_agent.print_eval BTC --distance 1.0 --side long [--apy 25]
"""

import argparse
import math
import os
import sys

from .api_client import PacificaClient
from .backtest import fetch_funding_history

# ⚠️ 이 모듈은 Print 전용이다. 자동매매 경로(autonomous·signal_scanner의 판단
#    로직)를 바꾸지 않는다. 아래 임포트는 전부 '읽기'다, 지표 계산 함수와
#    캐시된 가격 히스토리를 빌려 쓸 뿐, 그쪽 동작에 영향을 주지 않는다.
#    Print 수정이 매매에 번지지 않게 하는 것이 이 파일의 규칙.

CYCLE_HOURS = 24         # Print 사이클(예치 후 잠기는 시간)
# Closing cost, charged on NOTIONAL, paid only when the target is hit.
# Pacifica: "Once filled, a Print position follows standard perp rules ...
# fees apply to the position from that point on." A fill leaves you holding
# a perp you have to close, so one leg of fees is unavoidable. It was
# missing entirely (breakeven was fill rate x overshoot and nothing else).
# Taker is the default because too high a bar is the safe direction for a
# tool that tells people whether to take a trade; a limit close would pay
# the maker 0.00015 instead. Measured effect is small, +1.1% to +1.9% on
# the breakeven, and on the only recorded favorable set (08-25, twelve
# rows) it removes exactly one, the thinnest at 1.1% margin. Small is not
# the same as zero. (2026-08-28)
CLOSE_FEE = 0.00040


def deep_prices(client: PacificaClient, symbol: str, log=lambda *a: None):
    """시간당 종가, 바이낸스 선물 2019-09~ + 파시피카 최근을 이어붙인 것.

    기존 경로(펀딩 히스토리)는 약 167일뿐이라 2018 하락장·2021 상승장·2022 붕괴
    같은 구간이 통째로 빠진다. Print는 '꼬리에서 체결되는' 상품이라 그 구간이
    빠지면 체결 확률과 오버슛을 실제보다 낙관하게 된다. 매매 쪽에서 이미 받아
    캐시해 둔 9년치를 그대로 재사용한다(추가 다운로드 없음).

    Closes only, by design. Fractals moved onto the wick and every other
    _series() caller now passes highs and lows, but this module cannot: it
    scores Print fills against an hourly close series, and the three
    _series(...) calls below have no OHLC to hand over. That is not an
    oversight to be fixed by threading bars in here. Fractal signals read from
    this path are the close approximation, and Print only uses them to decide
    which side is safer to quote, never to size or direct a trade.

    Binance FUTURES, not spot (2026-08-27 user decision: "선물로 봐야지 왜
    현물로 봐, 프린트 자체가 선물인데"). A filled Print order leaves you
    holding a Pacifica perp, and the overshoot is eaten at the perp mark, so
    scoring fills against spot bars measured a different instrument than the
    one that actually fills. Until today nothing under ocean_agent/ ever set
    BINANCE_FUTURES, so this path silently took the spot branch and wrote a
    spot cache, against the 08-13 rule. The switch costs history: futures
    start 2019-09 against spot's 2017-08, so the 2018 bear market this
    docstring asks for is no longer in the series. That is a real loss and
    it was accepted with the reason above. The env var is set only around
    the fetch and restored, so nothing else in the process changes.
    """
    return [b["c"] for b in deep_bars(client, symbol, log=log)]


# Binance USDT-M futures begin here; _fetch_ohlc's default start boundary
# is 2021-01-01, which would silently drop 1.3 years, so it is passed
# explicitly. With it the OHLC path returns the same span as the closes
# path did (61,071 bars against 61,070).
FUT_START_MS = 1567296000000        # 2019-09-01T00:00:00Z


def deep_bars(client, symbol: str, log=lambda *a: None):
    """Hourly OHLC, Binance futures joined to Pacifica. One source of truth.

    deep_prices() is now the close column of this, so anything that reads
    both a close series and its highs and lows is aligned by construction
    rather than by two fetches happening to agree. They did not quite: the
    closes path returned 61,070 bars and the OHLC path 61,071, and an
    off-by-one between a price series and its own lows would misprice
    liquidations without erroring.

    Futures, not spot (2026-08-27 user decision: "선물로 봐야지 왜 현물로
    봐, 프린트 자체가 선물인데"). A filled Print order leaves you holding a
    Pacifica perp and the overshoot is eaten at the perp mark, so scoring
    against spot bars measured a different instrument. Nothing under
    ocean_agent/ had ever set BINANCE_FUTURES, so this path silently took
    the spot branch, against the 08-13 rule. The switch costs history:
    futures start 2019-09 against spot's 2017-08, so the 2018 bear market
    is no longer in the series. That was accepted for the reason above.
    The env var is set only around the fetch and restored after.
    """
    from .rematrix import _fetch_ohlc
    prev = os.environ.get("BINANCE_FUTURES")
    os.environ["BINANCE_FUTURES"] = "1"
    try:
        return _fetch_ohlc(client, symbol, "1h", use_extended=True, log=log,
                           start_ms=FUT_START_MS) or []
    finally:
        if prev is None:
            os.environ.pop("BINANCE_FUTURES", None)
        else:
            os.environ["BINANCE_FUTURES"] = prev


def evaluate(prices: list[float], distance_pct: float, side: str,
             hours: int = CYCLE_HOURS, mask=None,
             fee: float = CLOSE_FEE) -> dict:
    """시간당 가격 시계열에서 `hours` 뒤 이동 기준으로 Print 통계 계산.

    mask: None이면 전 구간. 리스트를 주면 mask[i]가 참인 시점만 센다
          (특정 신호가 켜져 있던 순간만 골라 조건부 확률을 재는 용도)."""
    d = distance_pct / 100.0
    fills = 0
    total = 0
    overshoots = []
    for i in range(len(prices) - hours):
        if mask is not None and not mask[i]:
            continue
        move = prices[i + hours] / prices[i] - 1.0
        total += 1
        # long: 목표가는 현재가보다 -d 아래. 24h 뒤 가격이 목표 이하면 체결
        # short: 목표가는 +d 위. 24h 뒤 가격이 목표 이상이면 체결
        if side == "long" and move <= -d:
            fills += 1
            overshoots.append(-move - d)   # 시장이 목표보다 더 내려간 폭 = 즉시 평가손
        elif side == "short" and move >= d:
            fills += 1
            overshoots.append(move - d)
    if total == 0:
        raise ValueError("데이터 부족")
    p_fill = fills / total
    avg_overshoot = sum(overshoots) / len(overshoots) if overshoots else 0.0
    # 사이클당 기대 손실 = 체결확률 × (평균 오버슛 + 청산 수수료).
    # 수수료는 체결됐을 때만, 명목가에 붙는다. 1배 기준이므로 배수는 호출부가
    # 곱한다(오버슛과 같은 자리에 붙으므로 배수 비례가 자동으로 맞는다).
    breakeven_cycle = p_fill * (avg_overshoot + fee)
    # 연율화는 사이클 길이로 나눈다. 25시간 사이클을 24로 나누면 손익분기를
    # 4% 과대평가한다, 유·불리가 갈리는 경계에서 판단이 뒤집힐 수 있다.
    cycles_per_year = 8760.0 / hours
    return {
        "windows": total,
        "hours": hours,
        "p_fill": p_fill,
        "avg_overshoot": avg_overshoot,
        "breakeven_cycle": breakeven_cycle,
        "breakeven_daily": breakeven_cycle * (24.0 / hours),
        "breakeven_apy": breakeven_cycle * cycles_per_year,
    }


def breakeven_cliff(prices, distance_pct: float, side: str, lev: float,
                    liq_dist_pct: float, hours: int = CYCLE_HOURS,
                    mask=None, lows=None, highs=None,
                    fee: float = CLOSE_FEE) -> float:
    """Breakeven APY with the liquidation cliff priced in, in percent.

    Loss per window: the whole deposit when the hourly path crosses the
    liquidation distance beyond the strike (the deposit is gone even if
    price recovers), else lev x end overshoot capped at 100%, else zero.
    Measured against a long BTC history:
    identical to the old linear model up to 5x, honest above it. The
    exchange premium stays proportional to leverage until about 20x and
    decays after, so the grid in recommend_now now runs to 20x and the
    ranking finds the optimal leverage by itself (user order: "5배
    상한정하지마 검토해보고 배수 최적인거면 기회 포착해").

    The liquidation test reads LOWS and HIGHS when they are supplied, and
    falls back to closes when they are not (2026-08-27). A liquidation is
    an intrabar event: price that dips through the liquidation level and
    recovers within the hour still takes the deposit, and a close-only
    series cannot see it. This is the same hole the 08-05 stop-detection
    fix closed on the trading side, where close-based judging read a 55%
    stop rate against a real 68%. It does not matter at 1x, where the
    liquidation distance is far outside anything a 24h window reaches, and
    it matters more the higher the leverage, which is exactly the range
    recommend_now now ranks over.

    The FILL test deliberately stays on closes. Print only settles at the
    24h checkpoint (Pacifica docs: "it's checked once at the end of each
    24-hour block"), so a target the market touched intraday and left does
    NOT fill. Using highs and lows there would invent fills that the
    product does not make.
    """
    d = distance_pct / 100.0
    liq = liq_dist_pct / 100.0
    total = 0
    loss = 0.0
    for i in range(len(prices) - hours):
        if mask is not None and not mask[i]:
            continue
        p0 = prices[i]
        w = prices[i:i + hours + 1]
        end = w[-1] / p0 - 1.0
        total += 1
        if side == "long":
            # p0 is bar i's CLOSE, so bar i's own low happened BEFORE the
            # deposit existed and cannot liquidate it. The window starts at
            # i+1. Identical at 1x and 5x, worth 4 to 7 points of breakeven
            # at 20x, and in the safe direction (it was overstating the
            # cost). Same class of off-by-one as the 61,070 against 61,071
            # bar mismatch. (2026-08-28 review P3)
            worst = (min(lows[i + 1:i + hours + 1])
                     if lows is not None else min(w))
            if worst / p0 - 1.0 <= -(d + liq):
                loss += 1.0
            elif end <= -d:
                # the closing fee rides on the same notional as the
                # overshoot, so it scales with leverage the same way
                loss += min(1.0, lev * (-end - d + fee))
        else:
            worst = (max(highs[i + 1:i + hours + 1])
                     if highs is not None else max(w))
            if worst / p0 - 1.0 >= d + liq:
                loss += 1.0
            elif end >= d:
                loss += min(1.0, lev * (end - d + fee))
    if total == 0:
        raise ValueError("데이터 부족")
    return loss / total * (8760.0 / hours) * 100


def evaluate_symbol(client: PacificaClient, symbol: str, distance_pct: float,
                    side: str, hours: int = CYCLE_HOURS,
                    deep: bool = True) -> dict:
    """심볼 하나의 Print 통계. deep=True면 9년치, False면 옛 경로(펀딩 167일)."""
    if deep:
        prices = deep_prices(client, symbol)
        if len(prices) >= 200:
            return evaluate(prices, distance_pct, side, hours)
    history = fetch_funding_history(client, symbol)
    prices = [float(r["oracle_price"]) for r in history]
    if len(prices) < 200:
        raise ValueError(f"{symbol} 가격 히스토리가 부족합니다 ({len(prices)}시간)")
    return evaluate(prices, distance_pct, side, hours)


def evaluate_by_signal(prices: list[float], distance_pct: float, side: str,
                       hours: int = CYCLE_HOURS, min_windows: int = 300) -> list:
    """신호가 켜져 있던 순간만 골라 Print 통계를 다시 잰다.

    Print 사이클(24h)과 매매 신호의 평가 지평(1시간봉 × 24봉)이 정확히 같은
    길이라, 매매 쪽에서 이미 측정해 둔 신호들을 그대로 '지금 예치해도 되는가'에
    쓸 수 있다. 예: 롱 Print는 목표가가 현재가 아래라 가격이 내려가면 체결된다
    → 24시간 뒤 상승 확률이 높은 신호가 켜져 있으면 체결 위험이 낮아진다.

    반환: [(손익분기APY, 신호명, 표본, 체결확률, 평균오버슛), ...] 낮은 순.
    표본이 min_windows 미만인 신호는 뺀다, 얇은 표본의 낮은 확률은 우연이다."""
    from .signal_scanner import _series, _signals
    n = len(prices)
    if n < 300 + hours:
        return []
    s = _series(prices)
    base = evaluate(prices, distance_pct, side, hours)
    rows = [(base["breakeven_apy"], "(신호 무관 · 전 구간)", base["windows"],
             base["p_fill"], base["avg_overshoot"])]
    for name, (_sd, cond) in _signals(s).items():
        mask = [False] * n
        lit = 0
        for i in range(220, n - hours):
            try:
                if cond(i):
                    mask[i] = True
                    lit += 1
            except (TypeError, IndexError):
                continue
        if lit < min_windows:
            continue
        try:
            st = evaluate(prices, distance_pct, side, hours, mask=mask)
        except ValueError:
            continue
        rows.append((st["breakeven_apy"], name, st["windows"],
                     st["p_fill"], st["avg_overshoot"]))
    rows.sort()
    return rows


def realized_vol(prices: list[float]) -> float:
    """시간 수익률 기반 연율 실현변동성. Print 유불리를 가르는 핵심 숫자."""
    import math
    rs = [math.log(prices[i + 1] / prices[i])
          for i in range(len(prices) - 1) if prices[i] > 0]
    if len(rs) < 30:
        return 0.0
    m = sum(rs) / len(rs)
    v = sum((r - m) ** 2 for r in rs) / (len(rs) - 1)
    return math.sqrt(v * 8760)


# 2026-08-05 측정으로 확정한 관문. 기간을 반으로 갈라 앞 절반에서만 문턱을
# 잡고 뒤 절반에 적용해도 흑자가 유지되는 조합만 남긴 것이다(6년 시간봉).
#
# 왜 이게 유일하게 통과했나: Print 손익은 방향이 아니라 변동성이 정한다
# (체결확률·오버슛 둘 다 변동성 함수). 변동성은 뭉쳐 오므로 '최근 7일이
# 조용했다'로 '앞으로 24시간도 조용할 것'을 확률적으로 고를 수 있다.
# 방향 신호로는 실패했다, 최고 신호도 손익분기를 159%→139%로 낮췄을 뿐이다.
#
# BTC 는 뺐다. 같은 규칙으로 연 -30%다. 조용하다 한 방에 튀는 성질 때문에
# 사고 1회가 프리미엄 196일치를 지운다(ETH 는 23일치).
# asset: (lookback hours, annualised vol threshold, distance %, side,
#         measured annual return or None)
#
# Refit 2026-08-27 on Binance FUTURES with the
# intrabar liquidation model, the basis this module actually runs on.
# Method is the 08-05 one: choose on the first half of the series only,
# verify on the second, criterion declared before the second half was
# looked at. The gate marks the only condition under which Print turns
# positive, a market quieter than its own normal.
#
# ETH was REMOVED. Its old (168, 0.242) never opened in the first half at
# all (0 of 1,639 windows, the 0.00 percentile) so it could not have been
# chosen by this method on today's longer series, and on futures it goes
# outright negative on the second half. Refitting ETH honestly produced
# (240, 0.434, 3.0, long), which clears drawdown and the year by year
# check but fails the concentration cap: 53% of its quiet windows sit in
# one block against a declared 50% limit. An asset with no measured
# threshold says so in the ranking rather than pretending to pass.
#
# The fifth field is None on purpose. It would be the return actually
# earned in open windows, and that needs historical PREMIUMS, which the
# exchange only quotes live. History prices the cost side only.
VOL_GATE = {
    "BTC": (168, 0.295, 3.0, "long", None),
    "SOL": (168, 0.660, 3.0, "short", None),
}


def vol_gate(client: PacificaClient) -> list[dict]:
    """지금 Print 를 넣어도 되는 자산이 있는지, 변동성 관문 판정.

    Print 는 평상시 크게 불리하다(IV 26~38% vs 실현 40~53%). 유일하게
    흑자로 넘어오는 구간이 '최근 7일이 유난히 조용할 때'라서, 그 순간만
    골라내는 것이 이 함수의 일이다. 실측 개방률은 자산과 구간에 따라
    5%에서 36% 사이다(08-27 재측정). 예전 주석의 '약 3%' 는 ETH 하나만
    있던 시절, 그것도 앞 절반에서 한 번도 안 열리던 문턱 기준이었다.

    주문은 넣지 않는다. 판정만 돌려준다."""
    out = []
    for asset, (win, thr, dist, side, apy) in VOL_GATE.items():
        try:
            p = deep_prices(client, asset)
        except Exception as e:
            out.append({"asset": asset, "open": False, "error": str(e)})
            continue
        if len(p) < win + 2:
            # was a silent `continue`: an asset short of bars simply left
            # the list with no row and no error (2026-08-27)
            out.append({"asset": asset, "open": False,
                        "error": f"봉 부족 {len(p)} < {win + 2}"})
            continue
        cur = realized_vol(p[-(win + 1):])
        out.append({"asset": asset, "open": cur < thr, "vol": cur,
                    "threshold": thr, "distance": dist, "side": side,
                    "expected_apy": apy, "lookback_h": win,
                    "margin": (thr - cur) / thr})
    return out


def format_gate(rows: list[dict]) -> str:
    """vol_gate() 결과를 사람이 읽는 형태로."""
    if not rows:
        return "Print 관문: 판정할 자산 없음"
    out = ["Print 진입 관문, 자기 평소보다 조용할 때만 열린다",
           "  (실측 개방률: 앞 절반 5~10%, 뒤 절반 11~36%)"]
    for r in rows:
        if r.get("error"):
            out.append(f"  {r['asset']}: 조회 실패 {r['error']}")
            continue
        state = "✅ 열림" if r["open"] else "⛔ 대기"
        # per asset window: it was hardcoded to "7일" while every asset
        # carries its own lookback, so a 240h gate printed as 7 days.
        _d = r.get("lookback_h", 168) / 24
        out.append(
            f"  {r['asset']}  최근 {_d:g}일 변동성 {r['vol']:.1%}"
            f"  (문턱 {r['threshold']:.1%} 미만)   {state}")
        if r["open"]:
            out.append(f"       → {r['side']} · 거리 {r['distance']:.1f}%")
            out.append(
                "       ⚠️ 관문은 비용을 절반 안팎으로 깎아 줄 뿐이다. "
                "그날 거래소 지급률이 그 낮아진 본전보다 높은지 반드시 "
                "따로 확인할 것. 관문이 열렸다는 것 자체는 넣으라는 뜻이 "
                "아니다.")
    if not any(r.get("open") for r in rows):
        out.append("  → 지금은 넣지 않는 게 맞다. 관문이 열릴 때만 다시 본다.")
    return "\n".join(out)


def live_compare(client: PacificaClient, hours: int = CYCLE_HOURS,
                 usd: float = 100.0, distances=(1.0, 2.0, 3.0),
                 levs=(1, 3, 5)) -> str:
    """지금 파시피카가 주는 APY vs 실측 손익분기, 넣어도 되는지 한눈에.

    주문은 넣지 않는다(시뮬레이터 조회만). Print에서 우리는 '옵션을 파는 쪽'이라,
    파시피카가 매기는 내재변동성(IV)이 실제 변동성보다 높을 때만 유리하다.
    2026-08-05 측정에선 IV 26.2% vs 실현 40~72%로 크게 불리했지만, 변동성이
    가라앉거나 파시피카가 IV를 올리면 뒤집힐 수 있으므로 그때그때 재본다.

    ⚠️ 손실은 명목가(예치금 × 레버리지) 기준이고 프리미엄은 예치금 기준이다.
       레버리지를 곱해 비교하지 않으면 고배율이 유리해 보이는 착시가 생긴다."""
    out = []
    try:
        games = {g["game"]: g for g in client.print_games()}
        prices = {p["symbol"]: float(p.get("mark") or p.get("mid") or 0)
                  for p in client.get_prices()}
    except Exception as e:
        return f"Print 시세 조회 실패: {e}"
    for game, g in games.items():
        asset = g.get("target_asset")
        mark = prices.get(asset, 0)
        if not mark:
            continue
        try:
            hist = deep_prices(client, asset)
        except Exception as e:
            out.append(f"{game}: 가격 히스토리 실패 {e}")
            continue
        recent = hist[-8760 * 2:] if len(hist) > 8760 * 2 else hist
        # 결국 이 한 줄이 전부를 결정한다. Print는 예치가 24시간 잠겨 체크포인트를
        # 반드시 통과하므로 체결 위험을 피할 수 없고, 그러면 우리는 옵션을 파는
        # 쪽이다. 파는 쪽은 파시피카가 매기는 IV가 실제 변동성보다 높을 때만
        # 남는다. 아래 표를 다 보지 않아도 이 비교로 결론이 난다.
        iv = 0.0
        try:
            probe = client.print_sim(game, str(usd), 0,
                                     str(round(mark * 0.99, 2)), "1")
            iv = float(probe.get("iv_pct") or 0)
        except Exception:
            pass
        rv3 = realized_vol(hist[-2190:]) * 100 if len(hist) > 2200 else 0
        rv2y = realized_vol(recent) * 100
        edge = "✅ 팔 만함" if iv > rv3 else "✕ 싸게 파는 셈"
        out.append(f"=== {game} ({asset} {mark:,.2f}) · 예치 ${usd:g} ===")
        out.append(f"  변동성  파시피카 IV {iv:.1f}%  vs  실현 "
                   f"{rv3:.1f}%(3개월) / {rv2y:.1f}%(2년)   → {edge}")
        out.append("  거리 레버   실제APY ┃ 손익분기APY(레버반영)     판정")
        out.append("                     ┃  최근2년    9년전체")
        for d in distances:
            st_r = evaluate(recent, d, "long", hours)
            st_f = evaluate(hist, d, "long", hours)
            for lev in levs:
                strike = round(mark * (1 - d / 100), 2)
                try:
                    sim = client.print_sim(game, str(usd), 0, str(strike),
                                           str(lev))
                    prem = float(sim.get("premium") or 0)
                except Exception:
                    continue
                apy = prem / usd * (8760.0 / hours) * 100
                be_r = st_r["breakeven_apy"] * 100 * lev
                be_f = st_f["breakeven_apy"] * 100 * lev
                verdict = ("✅ 유리" if apy > be_r else
                           "△ 잠잠한 국면에서만" if apy > be_r * 0.8 else "✕")
                out.append(f"  {d:4.1f}% {lev:2d}x  {apy:7.0f}% ┃ "
                           f"{be_r:8.0f}%  {be_f:8.0f}%   {verdict}")
        out.append("")
    out.append("Print에서 우리는 옵션을 파는 쪽, IV가 실제 변동성보다 높아야 남는다.")
    return "\n".join(out)


def forecast(client: PacificaClient, asset: str) -> dict:
    """매매 쪽 측정치로 '24시간 뒤 오를 확률'을 낸다, Print 방향 결정의 근거.

    Print 사이클(24h)과 매매 신호의 평가 지평(1시간봉 × 24봉)이 같은 길이라,
    워크포워드로 잰 신호별 실측 승률을 그대로 쓸 수 있다.
      · 롱 신호가 승률 w → 24h 뒤 상승 확률 w
      · 숏 신호가 승률 w → 하락 확률 w  (= 상승 확률 1-w)
    여러 신호가 켜져 있으면 표본 수로 가중 평균한다(많이 잰 신호에 더 무게).

    Print 방향은 여기서 갈린다:
      · 오를 것 같다 → 롱 Print(목표가 아래)가 안전. 내려가야 체결되므로.
      · 내릴 것 같다 → 숏 Print(목표가 위)가 안전.
    체결을 피하는 게 목적이지, 방향을 맞혀서 돈을 버는 게 아니다."""
    from .signal_scanner import _series, _signals, _matrix_rejects
    from .walkforward import measured_winrate
    prices = deep_prices(client, asset)
    tail = prices[-3000:] if len(prices) > 3000 else prices
    s = _series(tail)
    n = len(tail)
    lit = []
    for name, (side, cond) in _signals(s).items():
        try:
            if not cond(n - 1):
                continue
        except (TypeError, IndexError):
            continue
        m = measured_winrate(name, "1h")
        if m is None:
            lit.append({"signal": name, "side": side, "wr": None,
                        "n": 0, "p_up": None, "blocked": _matrix_rejects(name, "1h")})
            continue
        wr, cnt = m
        p_up = wr if side == "long" else 1.0 - wr
        lit.append({"signal": name, "side": side, "wr": wr, "n": cnt,
                    "p_up": p_up, "blocked": _matrix_rejects(name, "1h")})
    usable = [x for x in lit if x["p_up"] is not None and not x["blocked"]]
    if usable:
        w = sum(x["n"] for x in usable)
        p_up = sum(x["p_up"] * x["n"] for x in usable) / w
    else:
        p_up, w = 0.5, 0
    return {"asset": asset, "signals": lit, "usable": usable,
            "p_up": p_up, "weight": w,
            "safer_side": "long" if p_up >= 0.5 else "short"}


def recommend_now(client: PacificaClient, hours: int = CYCLE_HOURS,
                  usd: float = 100.0, distances=(1.0, 1.5, 2.0),
                  levs=(1, 2, 3, 5, 10, 20), top: int = 5) -> str:
    """지금 켜진 1시간봉 신호를 반영해 롱/숏 · 거리 · 배율을 순위 매긴다.

    Print 사이클(24h)과 매매 신호의 평가 지평(1시간봉 × 24봉)이 같은 길이라,
    '24시간 뒤 어디에 있을 것 같은가'를 그대로 체결 확률로 쓸 수 있다.
    롱 Print는 목표가가 아래라 가격이 내려가면 체결되므로, 상승 쪽 신호가
    켜져 있으면 롱이 안전하다(숏은 반대).

    각 후보의 손익분기는 '지금처럼 조용했던 과거 순간들'만 골라 다시 잰
    값이다(변동성 관문이 열려 있을 때만, 08-27 사용자 결정). 관문이 닫혀
    있으면 전 구간으로 잰다. 신호 마스크는 08-27 에 걷어냈다, 시험 구간
    에서 엣지가 음수였다.
    주문은 넣지 않는다, 시뮬레이터 조회만."""
    from .signal_scanner import _series, _signals
    try:
        games = {g["game"]: g for g in client.print_games()}
        prices = {p["symbol"]: float(p.get("mark") or p.get("mid") or 0)
                  for p in client.get_prices()}
    except Exception as e:
        return f"Print 시세 조회 실패: {e}"
    out = []
    gate_open: dict = {}
    for game, g in games.items():
        asset = g.get("target_asset")
        mark = prices.get(asset, 0)
        if not mark:
            continue
        try:
            bars = deep_bars(client, asset)
            hist = [b["c"] for b in bars]
        except Exception as e:
            out.append(f"{game}: 가격 히스토리 실패 {e}")
            continue
        cut = -8760 * 2 if len(hist) > 8760 * 2 else 0
        recent = hist[cut:] if cut else hist
        # Sliced from the same bars, so rlows[i] is the low of the hour whose
        # close is recent[i]. breakeven_cliff needs that guarantee to price
        # an intrabar liquidation. (2026-08-27)
        _rb = bars[cut:] if cut else bars
        rlows = [b["l"] for b in _rb]
        rhighs = [b["h"] for b in _rb]
        n = len(recent)
        s = _series(recent)
        # 지금(마지막 확정봉) 켜져 있는 신호들
        lit = []
        for name, (sd, cond) in _signals(s).items():
            try:
                if cond(n - 1):
                    lit.append((name, sd))
            except (TypeError, IndexError):
                continue
        out.append(f"=== {game} ({asset} {mark:,.2f}) ===")
        # The volatility gate, in the ranking at last. It was measured and
        # then stranded: nothing called it, so the one condition that ever
        # turns Print positive (a market quieter than usual) never reached
        # the table people read. Computed from `recent`, already in hand,
        # so it costs no extra request. Assets with no measured threshold
        # say so rather than pretending to pass. (2026-08-27 user order)
        gate = VOL_GATE.get(asset)
        if gate:
            gwin, gthr = gate[0], gate[1]
            if len(recent) > gwin + 1:
                gvol = realized_vol(recent[-(gwin + 1):])
                gate_open[asset] = gvol < gthr
                gmark = "✅ 열림" if gvol < gthr else "⛔ 대기"
                # asset name was missing here while the "no threshold"
                # branch carried it, so the hourly log printed four gate
                # lines and only two said what they were about (2026-08-28)
                out.append(f"  변동성 관문: {asset} 최근 {gwin//24}일 "
                           f"{gvol:.1%} vs 문턱 {gthr:.1%} → {gmark}")
            else:
                out.append(f"  변동성 관문: {asset} 봉 부족으로 판정 불가")
        else:
            out.append(f"  변동성 관문: {asset} 는 문턱 미측정 "
                       f"(측정된 시장은 {', '.join(VOL_GATE)})")
        # 예측 → 방향. 이게 Print 방향 결정의 근거다.
        try:
            fc = forecast(client, asset)
        except Exception as e:
            fc = None
            out.append(f"  예측 실패: {str(e)[:80]}")
        if fc:
            for x in fc["signals"]:
                if x["wr"] is None:
                    note = "실측 없음"
                else:
                    note = (f"실측 {x['wr']:.1%} (표본 {x['n']:,}) → "
                            f"상승확률 {x['p_up']:.1%}")
                    if x["blocked"]:
                        note += " ⛔ 매트릭스 차단(반영 안 함)"
                out.append(f"  신호 {x['signal']}"
                           f"({'↑' if x['side'] == 'long' else '↓'}) · {note}")
            if fc["weight"]:
                # Display only. The table below scores BOTH sides and
                # ranks on margin alone, so this line is not an input to
                # anything and must not read like one. Ledger 151 measured
                # the signal aggregate behind it and found no edge, which
                # is why the breakeven stopped using it; the sentence
                # outlived the mask. (2026-08-28 review P5)
                out.append(f"  ▶ (참고, 순위에 반영 안 함) 24시간 뒤 "
                           f"상승확률 {fc['p_up']:.1%} "
                           f"(표본 {fc['weight']:,}), 신호만 보면 "
                           f"{fc['safer_side'].upper()} 쪽이 덜 걸린다")
            else:
                out.append("  ▶ 쓸 만한 신호 없음, 방향 근거 없음(전 구간 통계로만 판단)")
        rows = []
        # The breakeven column is conditioned on VOLATILITY, not on lit
        # signals (2026-08-27 user decision: "판정 똑같이
        # 변동률로 해").
        #
        # The signal mask this replaces did two things wrong. It had no
        # edge: measured across three assets and ten cells, restricting to
        # hours when the aligned signals were lit moved the test-half
        # breakeven by -3.9 to -2.2 percent, i.e. slightly WORSE than no
        # condition at all, while cutting the sample to a third. And it
        # picked the best-scoring signal among those lit, live, every run,
        # which is selection on the outcome and exactly why it looked good
        # in sample. Volatility instead cuts the test-half breakeven by 26
        # to 37 percent, and it does so by reducing BOTH terms the
        # breakeven multiplies: fill rate down 13 to 20 percent and
        # overshoot down 16 to 21 percent. Signals moved neither.
        #
        # Only a fitted threshold is used. An asset with no measured gate
        # is scored unconditionally rather than against a made-up number;
        # that is still an improvement, since the mask it lost was worse
        # than nothing.
        # The mask may only be used while the gate is OPEN RIGHT NOW.
        # Conditioning on quiet hours answers "what does this cost when the
        # market is calm"; quoting that number on a noisy day answers a
        # question nobody asked and reads as a recommendation. First run
        # after the switch printed ✅ on BTC 20x with the gate shut at 53.8%
        # against a 29.5% threshold, which is exactly backwards: a cheap
        # breakeven borrowed from calm history while today is anything but.
        # When the gate is shut the honest comparison is unconditional.
        gate = VOL_GATE.get(asset) if gate_open.get(asset) else None
        vmask, vtag = None, "무조건"
        if VOL_GATE.get(asset) and not gate_open.get(asset):
            vtag = "무조건(관문닫힘)"
        if gate and n > gate[0] + 40:
            gwin, gthr = gate[0], gate[1]
            # rolling window over log returns, so this stays one pass
            # instead of recomputing a 168-bar std at every index
            rets = [math.log(recent[i + 1] / recent[i]) if recent[i] > 0
                    else 0.0 for i in range(n - 1)]
            vmask = [False] * n
            cnt = 0
            run = sum(rets[:gwin])
            run2 = sum(r * r for r in rets[:gwin])
            for i in range(gwin, n - hours):
                if i > gwin:
                    out_r = rets[i - gwin - 1]
                    in_r = rets[i - 1]
                    run += in_r - out_r
                    run2 += in_r * in_r - out_r * out_r
                if i < 220:
                    continue
                m = run / gwin
                var = max(0.0, (run2 - gwin * m * m) / (gwin - 1))
                if math.sqrt(var * 8760) < gthr:
                    vmask[i] = True
                    cnt += 1
            if cnt < 200:
                vmask, vtag = None, "조용 표본부족"
            else:
                vtag = f"조용<{gthr:.0%}"
        for side in ("long", "short"):
            for d in distances:
                mask, tag = vmask, vtag
                try:
                    st = evaluate(recent, d, side, hours, mask=mask)
                except ValueError:
                    continue
                strike = round(mark * (1 - d / 100) if side == "long"
                               else mark * (1 + d / 100), 2)
                for lev in levs:
                    try:
                        sim = client.print_sim(game, str(usd),
                                               0 if side == "long" else 1,
                                               str(strike), str(lev))
                        prem = float(sim.get("premium") or 0)
                        liq_px = float(sim.get("liquidation_price") or 0)
                    except Exception:
                        continue
                    apy = prem / usd * (8760.0 / hours) * 100
                    # Breakeven with the liquidation cliff whenever the
                    # exchange quotes a liq price; the linear scaling is
                    # only the fallback. Same series and signal mask as
                    # the fill statistics above.
                    if liq_px > 0:
                        liq_d = abs(strike - liq_px) / strike * 100
                        try:
                            be = breakeven_cliff(recent, d, side, lev,
                                                 liq_d, hours, mask=mask,
                                                 lows=rlows, highs=rhighs)
                        except ValueError:
                            be = st["breakeven_apy"] * 100 * lev
                    else:
                        be = st["breakeven_apy"] * 100 * lev
                    rows.append((apy - be, side, d, lev, apy, be,
                                 st["p_fill"], tag))
        rows.sort(reverse=True)
        # One Print cycle is 24 hours, so the numbers are shown per cycle
        # rather than annualised: 1560% APY reads as 4.27% a day, which is
        # the figure the deposit actually earns before the next checkpoint.
        # The fill-probability column is gone; for Print a "fill" means you
        # took the position at your target with the market already past it,
        # so the word invited exactly the wrong reading, and the breakeven
        # already prices that risk in. (2026-08-27 user order)
        # Top five per market, not the whole grid (2026-08-27 user
        # order: "5위까지만 순위정해서 올려, 다 올리지 말고"). Sixty
        # near duplicate rows buried the one line that mattered.
        out.append("  순위  방향  거리  레버  24h지급  24h본전     여유  조건"
                   f"   (상위 {min(top, len(rows))}/{len(rows)}칸)")
        for rank, (margin, side, d, lev, apy, be, pf, tag) in enumerate(
                rows[:top], 1):
            mk = "✅" if margin > 0 else "✕"
            out.append(f" {rank}{mk}  {side:5} {d:4.1f}% {lev:2d}x "
                       f"{apy/365:7.2f}% {be/365:8.2f}% {margin/365:+8.2f}%p"
                       f"  {tag[:16]}")
        out.append("")
    out.append("✅ = 24시간 지급이 24시간 본전보다 높음(그 조건이면 넣을 만함).")
    out.append("변동성 관문은 '최근이 평소보다 조용한가' 를 본다. 프린트가 "
               "흑자로 넘어오는 구간이 거기뿐이므로, 관문이 닫혀 있으면 "
               "표에 ✅ 가 떠도 한 번 더 의심할 것. 관문이 열렸다는 것도 "
               "비용이 절반쯤으로 내려갔다는 뜻이지 넣으라는 뜻이 아니다.")
    out.append("본전 = 그 조건에서 24시간 동안 기대되는 손실을 상쇄하는 지급률.")
    out.append("조건 열: 조용<X% 는 그만큼 조용했던 과거로만 잰 것, "
               "무조건 은 전 구간. 신호 마스크는 08-27 에 걷어냈다 "
               "(시험 구간에서 엣지가 음수였다).")
    out.append("연 환산이 필요하면 x365.")
    return "\n".join(out)


def format_report(symbol: str, distance_pct: float, side: str, stats: dict,
                  shown_apy: float | None = None, lev: float = 1.0,
                  cliff_apy: float | None = None) -> str:
    days = stats["windows"] / 24
    lines = [
        f"Print 평가, {symbol} {side} / 목표 거리 {distance_pct}% "
        f"(과거 {days:.0f}일 데이터)",
        f"  목표가에 걸릴 확률   : {stats['p_fill']:.1%} (24시간 안)",
        f"  걸렸을 때 평균 오버슛 : {stats['avg_overshoot']:.2%} (즉시 평가손 예상치)",
        f"  24시간 본전 지급률   : {stats['breakeven_daily']:.3%}",
    ]
    if shown_apy is not None:
        # The exchange quotes premium over MARGIN, so shown_apy already
        # carries the leverage: the same offer reads 67% at 1x and 1,347%
        # at 20x. The expected loss carries it too, so the breakeven is
        # scaled by the same factor. Comparing a 20x payout against a 1x
        # breakeven is wrong by exactly 20, and wrong in the direction that
        # calls a losing trade favorable. That is the defect this argument
        # closes; it is the same mistake the ledger records me making by
        # hand.
        #
        # cliff_apy, when the caller can supply it, replaces the linear
        # scaling below. The comment that used to sit here claimed linear
        # was "the conservative side of its cliff model at high leverage"
        # and that was BACKWARDS, which is the worst kind of wrong comment
        # because it is the sentence that kept the weaker model in place.
        # Measured on BTC futures: the cliff sits ABOVE linear once
        # leverage bites (1x identical, 5x 1.03, 10x 1.07, 20x 1.09 to
        # 1.11). A higher breakeven is a higher bar, so the CLIFF is the
        # conservative one and linear is the optimistic one. This tool
        # accepts leverage to 40 and is where the tweet sends people, so
        # it was handing out easier verdicts than the ranking at exactly
        # the sizes people use. (2026-08-28 review P1 and P2)
        if cliff_apy is not None:
            be_apy = cliff_apy
            be_daily = cliff_apy / 365.0
        else:
            be_daily = stats["breakeven_daily"] * lev
            be_apy = stats["breakeven_apy"] * lev
        edge = shown_apy / 100.0 - be_apy
        verdict = ("✅ 지급이 본전보다 높음, 통계적으로 유리한 조건"
                   if edge > 0 else
                   "⚠️ 지급이 본전 미만, 기대값이 마이너스인 조건")
        lines.append(f"  배수 {lev:g}배 기준")
        lines.append(f"  24시간 지급 {shown_apy/365:.3f}% vs 본전 "
                     f"{be_daily:.3%} → {verdict}")
        how = ("청산 절벽 반영" if cliff_apy is not None
               else ("선형 x 배수" if lev != 1 else ""))
        lines.append(f"  (연 환산: 지급 {shown_apy:.0f}% vs 본전 {be_apy:.0%}"
                     + (f" · {how}" if how else "") + ")")
        if cliff_apy is None and lev > 1:
            lines.append("  ⚠️ 거래소 청산가를 못 받아 선형으로 계산했습니다. "
                         "선형은 고배수에서 본전을 7~11% 낮게 봅니다(낙관 쪽). "
                         "프린트 순위(recommend_now)로 한 번 더 확인하세요.")
        if lev == 1:
            lines.append("  거래소 화면을 1배가 아닌 배수로 보고 있다면 그 "
                         "배수를 알려주세요. 화면의 지급률에는 배수가 이미 "
                         "곱해져 있어, 1배 본전과 비교하면 판정이 뒤집힙니다.")
    lines.append("  주의: 과거 분포 기반 추정치. 변동성 급변 구간에서는 빗나갈 수 있음.")
    return "\n".join(lines)


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("symbol", nargs="?", default="BTC")
    p.add_argument("--distance", type=float, default=1.0, help="목표 거리 %% (0.5~5)")
    p.add_argument("--side", choices=["long", "short"], default="long")
    p.add_argument("--apy", type=float, default=None, help="웹에 표시된 APY %%")
    p.add_argument("--url", default="https://api.pacifica.fi")
    p.add_argument("--hours", type=int, default=CYCLE_HOURS,
                   help="사이클 길이(시간). 예치 후 잠기는 시간 (기본 24)")
    p.add_argument("--shallow", action="store_true",
                   help="옛 경로(펀딩 히스토리 약 167일)로 계산")
    p.add_argument("--signals", action="store_true",
                   help="신호별 조건부 통계, 언제 예치하면 유리한가")
    p.add_argument("--live", action="store_true",
                   help="지금 파시피카 APY vs 실측 손익분기 비교 (주문 안 함)")
    p.add_argument("--now", action="store_true",
                   help="지금 켜진 신호 반영, 롱/숏·거리·배율 순위 (주문 안 함)")
    p.add_argument("--gate", action="store_true",
                   help="변동성 관문 판정, 지금 넣어도 되는지만 (주문 안 함)")
    args = p.parse_args()

    client = PacificaClient(args.url)
    if args.gate:
        print(format_gate(vol_gate(client)))
        return
    if args.live:
        print(live_compare(client, hours=args.hours))
        return
    if args.now:
        print(recommend_now(client, hours=args.hours))
        return
    stats = evaluate_symbol(client, args.symbol, args.distance, args.side,
                            hours=args.hours, deep=not args.shallow)
    print(format_report(args.symbol, args.distance, args.side, stats, args.apy))
    if args.signals:
        prices = deep_prices(client, args.symbol)
        rows = evaluate_by_signal(prices, args.distance, args.side, args.hours)
        if not rows:
            print("\n  신호별 통계를 낼 만큼 데이터가 없습니다.")
            return
        print(f"\n신호별, {args.side} Print 를 '그 신호가 켜졌을 때' 넣었다면")
        print("  손익분기APY   체결확률  평균오버슛   표본   신호")
        for be, name, wins, pf, ov in rows:
            mark = ""
            if args.apy is not None:
                mark = " ✅" if args.apy / 100.0 > be else " ⚠️"
            print(f"  {be:9.1%}   {pf:7.1%}  {ov:8.2%}  {wins:7,}   {name}{mark}")
        print("  ※ 위쪽일수록 유리(같은 APY로 더 안전). 표본 300건 미만 신호는 제외.")


if __name__ == "__main__":
    main()
