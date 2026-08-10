"""실시간 셋업 스캐너 — 확률·기대값 기준 상위 셋업 추천 + 예측 검증 루프.

원칙 (2026-07 실측 플레이북):
1. 승률은 지어내지 않는다 — 해당 코인·시간봉에서 그 신호의 과거 적중률(발생 n회)만 쓴다
2. 표본 30회 미만, 기준선 대비 엣지 +2%p 미만, 수수료 차감 기대값 ≤ 0 → 추천 금지
3. 신호는 자기 시간봉 스케일로만 평가 (단타 1h→24시간, 스윙 12h→12일, 장타 1d→24일)
4. 모든 추천을 predictions.json에 기록 → --review로 실제 결과와 대조 (승률 개선 루프)

실행:
  python -m ocean_agent.signal_scanner            # 톱3 셋업
  python -m ocean_agent.signal_scanner --review    # 과거 예측 채점
"""

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass

from .api_client import PacificaClient
from .indicators import INTERVAL_MS, ema_series, rsi_series

FWD = 24                 # 평가 지평: 24캔들 (자기 스케일)
FEE_RT = 0.0008          # 시장가 왕복 수수료 (0.04% × 2, 명목 기준)
MIN_N = 30               # 최소 표본 — 이 미만이면 진입하지 않는다
# 2026-08-06에 이 값을 5까지 낮춰 '전 종목 9년 합산을 물려받게' 해봤다.
# 표본이 부족한 종목을 버리는 게 아까웠기 때문인데, 하네스로 재보니 낮출수록
# 나빠졌다 (30건 건당 +1.870 · 20건 +1.825 · 15건 +1.725 · 10건 +1.697).
# 승률은 물려받을 수 있어도 '이 종목에서 이 신호가 어떻게 움직이는가'는
# 물려받을 수 없다 — 표본이 얇으면 그걸 모르는 채로 진입하는 것이다.
#
# 표본 부족의 올바른 해법은 관문을 낮추는 게 아니라 데이터를 더 가져오는
# 것이었다. historical_data.BINANCE_SYMBOL 에 6종목(PAXG·PUMP·ZEC·UNI·
# JUP·ENA)을 추가해 2017년부터 이어붙이니 표본이 4~9배가 됐다
# (UNI 8시간봉 1,068 → 6,447봉). 그러면 30건 기준을 그냥 통과한다.

# ── 진짜 관문은 유동성이다 ──
# 표본이 적다고 버리는 건 근거가 없다(9년 합산이 있으니까). 정작 위험한 건
# 거래량 하한 — 2026-08-07 사용자 지시로 제거(0). 전 종목을 후보로 본다.
# 이전값 100_000 의 근거(참고용): 얇은 호가에서 손절이 계획가에 안 나가고
# 미끄러진다. 2026-08-06 실측 중앙값 $44k, 하위 10%는 $3k 미만
# (CHIP $403 · ZK $491 · PIPPIN $789). 슬리피지 위험은 이제 필터가 아니라
# 사용자 판단에 맡긴다.
MIN_VOLUME_24H = 0
# 기준선 대비 최소 엣지. 예전 값 0.05는 백테 승률이 60~77%로 부풀려져 있던
# 시절 기준이었다. 승률을 워크포워드 실측값으로 바꾸면 그 숫자는 50~58%가 되고,
# 5%p를 요구하면 통과 조합이 1개로 줄어 사실상 거래가 멈춘다.
# 홀드아웃 측정(학습 7종목 / 검증 7종목, 기준선 50.46%):
#   0%p → 36조합 51.20%  ·  1%p → 14조합 52.34%  ·  2%p → 7조합 54.97%
#   3%p →  4조합 55.30%  ·  5%p →  1조합 54.12%(기회 1,251건뿐)
# 2%p 채택 — 3%p가 0.33%p 높지만 4조합에 몰려 한 신호가 무너지면 타격이 크다.
MIN_EDGE = 0.02
# 증거 수축 — 표본이 이만큼일 때 초과분을 절반으로 줄인다(신뢰 50%).
# 표본이 많을수록 1에 수렴해 원래 승률을 그대로 쓴다.
EVIDENCE_PRIOR = 60
LIVE_SAMPLE_WEIGHT = 3   # 실전 관측 1건 = 백테스트 캔들 3건어치 (관문 통과분만)
# 합산 실측이 수축 목표점을 이만큼의 표본에서 절반쯤 끌어간다 (기간 요건도 함께 곱해짐).
POOLED_PRIOR_N = 200
# 1회 손절 시 자본의 몇 %를 잃을 것인가. policy.yaml 의 risk_per_trade_pct 가
# 있으면 그 값이 우선한다 — 2026-08-06 이전엔 이 상수만 쓰여서 정책을 바꿔도
# 매매에 반영되지 않았다(advisor 만 정책을 읽고 있었다).
RISK_PCT = 0.02
# 증거금이 이보다 작으면 진입하지 않는다. 위험을 자본의 %로 고정하면 손절이
# 넓은 자리는 수량이 자동으로 작아지는데, 그러다 보면 증거금 $10~16 짜리
# 포지션이 나온다. 이겨도 몇 달러라 손이 많이 가는 데 비해 남는 게 없다.
# (2026-08-06 사용자 지시: "그 이하는 넣지 마 손해야")
#
# 값을 고를 때 실측 (자본 $333 · 위험 2% · 지금 뜨는 셋업 4개 기준):
#   $30 → 손절 4.0% 까지만 통과. 지금 뜨는 셋업이 전부 걸려 0개가 된다
#   $25 → 손절 5.3% 까지 통과
#   $20 → 손절 6.7% 까지 통과
# 자본이 커지면 저절로 느슨해진다 — $500 이면 손절 8%, $1,000 이면 16%
# 까지 통과한다. 비율이 아니라 절대금액인 이유는 '이겨도 몇 달러'라는
# 기준 자체가 절대금액이기 때문이다.
MIN_MARGIN_USD = 25.0

# ── 스윙 고정 손절/익절 — 2026-08-05 채택했다가 08-06 철회 ──
# ⚠️ 현재 코드에서 쓰이지 않는다. 종가 기준으로 골랐던 값인데, 고가/저가로
# 다시 재니 마이너스였다(낙관 가정으로도 -0.013%). 되살릴 일이 있을 때
# 근거를 함께 보라고 상수와 주석만 남긴다. 지금은 각 신호가 자기 실측값을 쓴다.
# 손절 1.5% / 익절 5.0% 가 1.5~3.0% × 3~12% 격자에서 최적이었고, 기간을
# 3등분해도 순위가 유지됐다. 손익비 3.33 이라 손익분기 승률이 24.3%뿐이다.
SWING_SL = 0.015
SWING_TP = 0.05
# 손절 폭 배수 (2026-08-09 실측 채택): 24h급 픽은 진입 후 평균 3% 역행 후
# 방향대로 가는데, 실측 손실폭 그대로 걸면 잡음 안이라 이길 거래가 잘린다.
# 하네스 7년(스윙 블록 패리티 수정 후): 1.0×=$8,048 → 1.5×=$9,134 →
# 2.0×=$9,709(+20.6%, MDD 5.9→5.2%) → 2.5×=$8,894 → 3.0×=$8,606.
# 2.0에서 정점을 찍고 꺾여 과적합 아닌 실측 최적점. 위험 계산이 손절폭
# 기준이라 포지션은 자동으로 절반 크기가 된다 (위험 총량 불변).
SL_WIDTH_MULT = 2.0
# ⚠️ 아래도 현재 미사용 (위 스윙 블록과 함께 철회).
# 변동성 관문: 직전 VOL_LOOKBACK 시간의 변동폭이 익절의 VOL_GATE_MULT 배
# 미만이면 진입하지 않는다. 되돌아보기 24h/3일/7일/14일을 비교해 3일이
# 가장 좋았고(상위40% 기준 +3.046%), 배수 2.0 은 실측 60분위(10.3%)다.
VOL_LOOKBACK = 72
VOL_GATE_MULT = 2.0
# 선별 편향 보정 강도 — 순위 점수에 n/(n+이 값)을 곱한다.
# 실측 부풀림에서 역산: 60일창(표본 약 60건)이 2.34배 → prior 약 80,
# 3년창(표본 약 1500건)이 1.24배 → prior 약 350. 그 범위의 아래쪽을 잡았다.
# 위쪽(300)으로 잡아봤더니 표본 50~80건짜리가 0.15~0.22배로 눌려 순위에서
# 사실상 사라졌다 — 신규 상장을 '진입 금지'한 것과 같아져 B안의 취지에 어긋난다.
EV_SHRINK_PRIOR_N = 100
PREDICTIONS_FILE = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "predictions.json")

# 전 시간봉 매트릭스(2026-07-21, 633조합) 실측 기본값:
#   1m~15m = 평균 EV 음수 (수수료가 다 먹음), 1d/1w = 표본 부족(신호 발생 거의 없음)
#   8h가 최고(+1.08%), 12h(+0.96%), 4h(+0.08%) — 기준선 대비 순수 엣지도 8h가 +2.05%p
# 자가 재측정(rematrix)이 돌면 그 결과가 이 기본값을 대체한다 — 국면이 바뀌면
# 최적 시간봉도 바뀌기 때문 (7월엔 8h가 '전부 무효'였다가 지금은 최고가 됨).
_DEFAULT_HORIZONS = {"단타": "4h", "스윙": "8h", "장타": "12h"}


def current_horizons() -> dict:
    """지금 매트릭스가 고른 시간봉. 호출할 때마다 다시 읽는다.

    예전엔 모듈 전역 HORIZONS 로 import 시점에 한 번만 계산했다. 그러면
    ensure_fresh() 가 7일마다 매트릭스를 다시 재도 봇이 재시작될 때까지 옛
    시간봉으로 계속 거래한다 — 자가개선 루프의 마지막 화살표가 끊긴 상태였다.
    load_matrix() 가 mtime 캐시라 매번 불러도 파일을 다시 파싱하지 않는다."""
    try:
        from .rematrix import best_horizons
        return best_horizons(_DEFAULT_HORIZONS)
    except Exception:
        return _DEFAULT_HORIZONS


_SIDE_CACHE = {"src": None, "map": {}}


def _signal_side(sig: str):
    """이 신호가 롱 신호인가 숏 신호인가. 매트릭스 행에 기록된 값을 쓴다
    (신호 정의에 종속되지 않게 — 정의가 바뀌면 매트릭스도 함께 갱신된다).

    매트릭스 객체가 바뀌면 다시 만든다 — 한 번 채우고 끝내면 백그라운드
    재측정이 파일을 갈아끼워도 옛 매핑을 계속 쓴다."""
    try:
        from .rematrix import load_matrix
        m = load_matrix()
    except Exception:
        return None
    if m is None:
        return None
    c = _SIDE_CACHE
    if c["src"] is not m:
        c["map"] = {}
        for r in m.get("rows", []):
            c["map"].setdefault(r["sig"], r.get("side"))
        c["src"] = m
    return c["map"].get(sig)


def _matrix_rejects(sig: str, tf: str) -> bool:
    """자가 재측정 결과가 이 신호·시간봉을 쓸 수 없다고 판정했는가.
    측정 기록이 없거나 표본이 부족하면 막지 않는다(모르면 통과).

    두 가지를 **모두** 요구한다:
      ① 절대 EV > 0     — 실제로 돈이 되는가 (수수료 차감 후)
      ② 순수 엣지 > 0    — 신호가 없을 때(무조건 진입)보다 나은가 = 실력인가

    ②가 없으면 '시장이 올랐다'를 '신호가 좋다'로 착각한다. 2017~2026 측정에서
    1일봉 기준선은 롱 +5.03% / 숏 -5.19%였다. 절대 EV만 보면 롱 신호는 실력과
    무관하게 전부 통과하고 숏은 전부 차단된다 — 실제로 82조합 중 78개가 롱이었고,
    그중엔 순수 엣지가 마이너스인 것(sRSI<20 과매도 -0.01%, 프랙탈 저점 -0.06%)도
    통과하고 있었다. 반대로 엣지가 있는 숏(MA 데드크로스 +1.34%)은 막혔다.
    ①이 없으면 반대로 엣지는 있지만 돈은 안 되는 조합이 통과한다.
    측정 비교(표본 200+ 193조합): ①만 82조합 순수엣지 +0.15% · ②만 97조합
    절대EV +0.40% · 둘 다 54조합 절대EV +1.32% 순수엣지 +0.51% — 둘 다가 우세.
    """
    try:
        from .rematrix import signal_ev, baseline_ev
        r = signal_ev(sig, tf)
    except Exception:
        return False
    if not r:
        return False
    ev, n = r
    if n < 200:
        return False              # 표본 부족 — 모르면 통과
    if ev <= 0:
        return True               # ① 돈이 안 됨
    try:
        side = _signal_side(sig)
        base = baseline_ev(tf, side) if side else None
    except Exception:
        base = None
    if base is None:
        return False              # 기준선 불명 — ①만 통과했으면 통과
    return ev - base <= 0         # ② 기준선을 못 이김 = 실력 아님


_BAR_CACHE = {}      # (base, symbol, interval) -> [(t, close), ...] 확정봉만
# 관측이 파시피카 전체 perp(100종목 안팎)를 순회하므로 상한이 없으면 계속 쌓인다.
# 넘치면 가장 오래 안 쓴 항목부터 버린다 — 버려도 다음 호출에서 다시 받을 뿐이다.
_BAR_CACHE_MAX = 400


def fetch_bars(client, symbol, interval, max_bars=1500):
    """확정(닫힌) 캔들 리스트 — (t, o, h, l, c) 튜플.

    예전엔 종가만 돌려줬다. 그러면 '봉 안에서 손절가를 스치고 회복한' 경우를
    볼 수 없어 손절 도달률이 과소평가된다 — 실측(2026-08-05, 1시간봉 4.4만건):
    종가 기준 손절율 55% vs 고저 기준 68%, EV 는 +0.298% vs -0.148% 로
    부호까지 뒤집혔다. 파시피카 kline 이 이미 o/h/l/c 를 다 주는데 버리고
    있었을 뿐이다.

    마지막 미완성 캔들은 버린다.

    아직 진행 중인 봉의 종가는 봉이 닫힐 때까지 계속 변한다. 그걸로 신호를
    판정하면 ① 실시간 진입이 봉 안에서 켜졌다 꺼졌다 하고 ② 백테스트가
    미완성 종가와 비교돼 미래참조(look-ahead)가 된다. 확정봉만 쓴다.

    닫힌 봉은 두 번 다시 바뀌지 않으므로 캐시하고, 사이클마다 새로 생긴
    구간만 이어받는다. 예전에는 매 사이클 1,500봉을 통째로 다시 받았는데,
    실측상 한 사이클의 진입 판단 115초 중 계산은 1초 미만이고 나머지가 전부
    이 조회였다. 15분 주기에 1시간봉이면 새 봉은 0~1개뿐이다.
    부수 효과로 API 호출이 크게 줄어 429(레이트리밋)도 함께 완화된다.
    """
    step = INTERVAL_MS[interval]
    now = int(time.time() * 1000)
    key = (getattr(client, "base", ""), symbol, interval)
    bars = _BAR_CACHE.get(key) or []
    want_from = now - step * max_bars
    # 캐시가 요청 구간을 못 덮거나(더 긴 구간 요청) 너무 낡았으면 통째로 다시.
    if bars and (bars[0][0] > want_from + step or bars[-1][0] < want_from):
        bars = []
    cur = bars[-1][0] + step if bars else want_from
    tries = 0
    fetched = []
    while cur < now and tries < 30:
        tries += 1
        resp = client.session.get(f"{client.base}/kline", params={
            "symbol": symbol, "interval": interval,
            "start_time": cur, "end_time": now}, timeout=25)
        if resp.status_code == 429 or not resp.headers.get(
                "content-type", "").startswith("application/json"):
            time.sleep(12)
            continue
        r = resp.json()
        time.sleep(0.3)
        d = r.get("data") or []
        if not d:
            break
        fetched.extend(
            (int(c["t"]), float(c.get("o") or c["c"]), float(c.get("h") or c["c"]),
             float(c.get("l") or c["c"]), float(c["c"])) for c in d)
        last = d[-1]["t"]
        if last <= cur:
            break
        cur = last + step
    if fetched:
        # 겹치는 시각은 새로 받은 값으로 덮는다(마지막 캐시봉이 다시 올 수 있음)
        merged = {b[0]: b for b in bars}
        merged.update({b[0]: b for b in fetched})
        bars = [merged[k] for k in sorted(merged)]
    # 진행 중인 봉은 캐시에 넣지 않는다 — 종가가 아직 변한다
    while bars and bars[-1][0] + step > now:
        bars.pop()
    if len(bars) > max_bars:
        bars = bars[-max_bars:]
    _BAR_CACHE.pop(key, None)      # 재삽입해 '최근 사용' 순서를 유지
    _BAR_CACHE[key] = bars
    while len(_BAR_CACHE) > _BAR_CACHE_MAX:
        _BAR_CACHE.pop(next(iter(_BAR_CACHE)))
    return bars


def fetch_closes(client, symbol, interval, max_bars=1500):
    """종가만 필요한 호출부(지표 계산 등)를 위한 얇은 래퍼."""
    return [b[4] for b in fetch_bars(client, symbol, interval, max_bars)]


def _series(closes):
    """신호 판정에 필요한 시계열 일괄 계산."""
    n = len(closes)
    rs = rsi_series(closes, 14)
    rsi = [None] * (n - len(rs)) + list(rs)
    ma20 = [None] * n
    ma50 = [None] * n
    for i in range(n):
        if i >= 19:
            ma20[i] = sum(closes[i-19:i+1]) / 20
        if i >= 49:
            ma50[i] = sum(closes[i-49:i+1]) / 50
    e12 = [None] * (n - len(ema_series(closes, 12))) + ema_series(closes, 12)
    e26 = [None] * (n - len(ema_series(closes, 26))) + ema_series(closes, 26)
    macd = [None if (a is None or b is None) else a - b for a, b in zip(e12, e26)]
    ml = [v for v in macd if v is not None]
    sg = ([None] * (n - len(ema_series(ml, 9))) + ema_series(ml, 9)) if len(ml) > 9 else [None] * n
    hist = [None if (m is None or s is None) else m - s for m, s in zip(macd, sg)]
    bbpos = [None] * n
    bbw = [None] * n          # 밴드 폭(변동성) — 스퀴즈 판정용
    for i in range(19, n):
        w = closes[i-19:i+1]
        mid = sum(w) / 20
        sd = (sum((c - mid) ** 2 for c in w) / 20) ** 0.5
        bbpos[i] = (closes[i] - mid) / sd if sd > 0 else 0.0
        bbw[i] = (4 * sd / mid) if mid > 0 else None
    # 200기간 이동평균 (장기 추세)
    ma200 = [None] * n
    run = 0.0
    for i in range(n):
        run += closes[i]
        if i >= 200:
            run -= closes[i-200]
        if i >= 199:
            ma200[i] = run / 200
    # 스토캐스틱 RSI — 대중적으로 가장 인기 있는 보조지표.
    # 과거 측정에선 성적이 최하였으나, 국면이 바뀌므로 후보로 넣고 매트릭스가 판정한다.
    srsi_k = [None] * n
    for i in range(n):
        if rsi[i] is None or i < 14:
            continue
        w = [r for r in rsi[i-13:i+1] if r is not None]
        if len(w) < 14:
            continue
        lo, hi = min(w), max(w)
        srsi_k[i] = (rsi[i] - lo) / (hi - lo) * 100 if hi > lo else 50.0
    srsi_d = [None] * n       # %K의 3기간 평균
    for i in range(2, n):
        w = [v for v in srsi_k[i-2:i+1] if v is not None]
        if len(w) == 3:
            srsi_d[i] = sum(w) / 3
    # 윌리엄스 프랙탈 (5봉) — 종가 기반 근사. 가운데 봉(i-2)이 좌우 2봉보다
    # 모두 높으면 up-fractal(고점 반전→하락 신호), 모두 낮으면 down-fractal
    # (저점 반전→상승 신호). 확정은 i에서만 가능(우측 2봉 필요) = look-ahead 없음.
    frac_up = [False] * n     # i에서 '방금 확정된' 고점 프랙탈
    frac_dn = [False] * n
    for i in range(4, n):
        c2 = closes[i-2]      # 가운데
        if c2 > closes[i-4] and c2 > closes[i-3] and c2 > closes[i-1] and c2 > closes[i]:
            frac_up[i] = True
        if c2 < closes[i-4] and c2 < closes[i-3] and c2 < closes[i-1] and c2 < closes[i]:
            frac_dn[i] = True
    return {"rsi": rsi, "ma20": ma20, "ma50": ma50, "ma200": ma200,
            "hist": hist, "bbpos": bbpos, "bbw": bbw,
            "srsi_k": srsi_k, "srsi_d": srsi_d,
            "frac_up": frac_up, "frac_dn": frac_dn}


# (이름, 방향, 판정함수) — 매트릭스 실측에서 살아남은 계열만
def _signals(s):
    rsi, ma20, ma50, hist = s["rsi"], s["ma20"], s["ma50"], s["hist"]
    def x(i, arr):
        return arr[i] is not None and arr[i-1] is not None
    bb = s.get("bbpos")
    n = len(rsi)
    m200 = s.get("ma200") or [None] * n
    bw = s.get("bbw") or [None] * n
    sk = s.get("srsi_k") or [None] * n
    sd = s.get("srsi_d") or [None] * n
    fu = s.get("frac_up") or [False] * n
    fd = s.get("frac_dn") or [False] * n
    return {
        # 모멘텀 극단 (실측: 시간봉별로 유효 구간 다름 — 승률로 자동 필터됨)
        "RSI>70 과열": ("short", lambda i: rsi[i] is not None and rsi[i] > 70),
        "RSI>80 극과열": ("short", lambda i: rsi[i] is not None and rsi[i] > 80),
        "RSI<30 과매도": ("long", lambda i: rsi[i] is not None and rsi[i] < 30),
        "RSI<20 극과매도": ("long", lambda i: rsi[i] is not None and rsi[i] < 20),
        # 추세 전환 이벤트 (실측: '상태'보다 '발생 순간'이 유효)
        "MA 골든크로스": ("long", lambda i: i > 0 and x(i, ma20) and x(i, ma50)
                       and ma20[i] > ma50[i] and ma20[i-1] <= ma50[i-1]),
        "MA 데드크로스": ("short", lambda i: i > 0 and x(i, ma20) and x(i, ma50)
                       and ma20[i] < ma50[i] and ma20[i-1] >= ma50[i-1]),
        "MACD 골든크로스": ("long", lambda i: i > 0 and x(i, hist)
                         and hist[i] > 0 >= hist[i-1]),
        "MACD 데드크로스": ("short", lambda i: i > 0 and x(i, hist)
                         and hist[i] < 0 <= hist[i-1]),
        # 변동성 밴드 이탈
        "볼린저 상단이탈": ("short", lambda i: bb[i] is not None and bb[i] > 2),
        "볼린저 하단이탈": ("long", lambda i: bb[i] is not None and bb[i] < -2),
        # ── 아래는 매트릭스 검증 대기 중인 후보 계열 ──
        # 승률이 아니라 실측 EV가 마이너스면 _matrix_rejects가 자동 차단한다.
        "sRSI>80 과열": ("short", lambda i: sk[i] is not None and sk[i] > 80),
        "sRSI<20 과매도": ("long", lambda i: sk[i] is not None and sk[i] < 20),
        "sRSI 골든크로스": ("long", lambda i: i > 0 and x(i, sk) and x(i, sd)
                        and sk[i] > sd[i] and sk[i-1] <= sd[i-1] and sk[i] < 40),
        "sRSI 데드크로스": ("short", lambda i: i > 0 and x(i, sk) and x(i, sd)
                        and sk[i] < sd[i] and sk[i-1] >= sd[i-1] and sk[i] > 60),
        # 장기 추세 전환 (MA20/50보다 느리지만 신호가 강함)
        "MA200 골든크로스": ("long", lambda i: i > 0 and x(i, ma50) and x(i, m200)
                         and ma50[i] > m200[i] and ma50[i-1] <= m200[i-1]),
        "MA200 데드크로스": ("short", lambda i: i > 0 and x(i, ma50) and x(i, m200)
                         and ma50[i] < m200[i] and ma50[i-1] >= m200[i-1]),
        # 변동성 수축 후 이탈 — '상태'가 아닌 '발생' 신호라 실측 기대가 있는 계열
        "볼린저 스퀴즈 상방": ("long", lambda i: _squeeze_break(bw, bb, i, +1)),
        "볼린저 스퀴즈 하방": ("short", lambda i: _squeeze_break(bw, bb, i, -1)),
        # 극단에서 '되돌아오는 순간' — 극단 상태 자체보다 유효할 수 있음
        "RSI 과열이탈": ("short", lambda i: i > 0 and x(i, rsi)
                      and rsi[i] < 70 <= rsi[i-1]),
        "RSI 과매도탈출": ("long", lambda i: i > 0 and x(i, rsi)
                       and rsi[i] > 30 >= rsi[i-1]),
        # 윌리엄스 프랙탈 (5봉 반전) — 고점 프랙탈=하락, 저점 프랙탈=상승 신호.
        # 매트릭스가 국면별로 유효한지 판정한다.
        "프랙탈 고점": ("short", lambda i: fu[i]),
        "프랙탈 저점": ("long", lambda i: fd[i]),
    }


def _squeeze_break(bw, bb, i, direction):
    """밴드 폭이 최근 50봉 중 하위 20%로 수축(스퀴즈)한 뒤 밴드를 이탈하는 순간."""
    if i < 50 or bw[i] is None or bb[i] is None:
        return False
    w = [v for v in bw[i-50:i] if v is not None]
    if len(w) < 30:
        return False
    thresh = sorted(w)[int(len(w) * 0.2)]
    prev_squeezed = any(v is not None and v <= thresh for v in bw[i-5:i])
    if not prev_squeezed:
        return False
    return bb[i] > 1.5 if direction > 0 else bb[i] < -1.5


@dataclass
class Setup:
    symbol: str
    horizon: str          # 단타/스윙/장타
    interval: str
    signal: str
    side: str             # long/short
    entry: float
    win_rate: float
    n_samples: int
    tp_move: float        # 가격 기준 목표 이동폭 (비율)
    sl_move: float        # 가격 기준 손절 이동폭
    leverage: int
    margin_usd: float
    ev_margin: float      # 수수료 반영, 원금 대비 기대수익률
    horizon_hours: float
    live: bool = False    # 지금 이 순간 신호가 켜져 있는가
    ts: int = 0


def evaluate_setups(client, budget_usd=100.0, universe=15,
                    min_volume_24h=0.0, position_usd=0.0,
                    max_position_pct_of_volume=0.0, max_leverage=0,
                    risk_pct=0.0):
    """유동성 관문 — 호가창이 얇으면 ① 주문이 일부만 체결되고 ② 손절이 걸려도
    그 가격에 못 팔아 계획보다 크게 잃는다. 변동성보다 실질적인 위험.

    핵심은 '거래대금이 큰가'가 아니라 '내 주문이 그 시장에 비해 큰가'다.
    그래서 기준은 (내 포지션 크기 ÷ 24시간 거래대금).

    실측 근거 — 우리 주문 $3,000 기준:
        BTC 테스트넷  거래대금의 0.3%  → 정상 체결 ✅
        SOL 테스트넷  거래대금의 4.6%  → 정상 체결 ✅
        HYPE 테스트넷 거래대금의 12.8% → $13만 체결 ❌
        PUMP 테스트넷 거래대금의 108%  → 부분 체결 ❌
    이 기준은 네트워크와 무관하게 작동한다. 메인넷 HYPE는 같은 $3,000이
    거래대금의 0.11%에 불과하므로 정상 통과한다(= 주식·금·원자재도 살아남음).

    min_volume_24h: 절대 금액 하한(선택). 0이면 끔.
    """
    prices = client.get_prices()
    perps = sorted([p for p in prices if p.get("mid")],
                   key=lambda p: -float(p.get("volume_24h") or 0))[:universe]
    floor = max(min_volume_24h, MIN_VOLUME_24H)
    perps = [p for p in perps if float(p.get("volume_24h") or 0) >= floor]
    if max_position_pct_of_volume > 0 and position_usd > 0:
        need = position_usd / max_position_pct_of_volume
        perps = [p for p in perps
                 if float(p.get("volume_24h") or 0) >= need]
    markets = {m["symbol"]: m for m in client.get_markets()}
    # 사이클마다 다시 읽는다 — 백그라운드 재측정이 최적 시간봉을 바꾸면
    # 다음 스캔부터 바로 반영된다(봇 재시작 불필요).
    horizons = current_horizons()
    setups = []

    for p in perps:
        sym = p["symbol"]
        max_lev = int(markets.get(sym, {}).get("max_leverage", 10))
        for hz, tf in horizons.items():
            ohlc = fetch_bars(client, sym, tf)
            closes = [b[4] for b in ohlc]
            n = len(closes)
            if n < 220 + FWD:   # 채점 시작(220) + 전방지평 이후가 있어야 표본 생김
                continue
            s = _series(closes)
            sigs = _signals(s)
            ups = sum(1 for i in range(n - FWD) if closes[i + FWD] > closes[i])
            base_up = ups / (n - FWD)

            # 지금 동시에 켜진 신호 집합 (조합 학습 참조용)
            co_active = []
            for _nm, (_sd, _cd) in sigs.items():
                try:
                    if _cd(n - 1):
                        co_active.append(_nm)
                except (TypeError, IndexError):
                    pass

            for name, (side, cond) in sigs.items():
                # 자가 재측정 관문: 이 신호가 이 시간봉에서 전 코인 합산으로도
                # 마이너스면 건너뛴다. 한 코인의 우연한 성적에 속지 않기 위함
                # (실측: 롱 계열 신호는 현 국면에서 전부 음수 EV였다).
                if _matrix_rejects(name, tf):
                    continue
                # 과거 발생 전수 → 방향 부호 반영 수익률 분포.
                # 시작 220 = 매트릭스(rematrix)와 동일. 지표 성숙(sRSI 220봉,
                # ma200 200봉) 이전 구간을 포함하면 실전 승률과 매트릭스 EV가
                # 서로 다른 표본으로 계산돼 어긋난다.
                moves = []
                fires = []
                for i in range(220, n - FWD):
                    try:
                        if not cond(i):
                            continue
                    except (TypeError, IndexError):
                        continue
                    m = closes[i + FWD] / closes[i] - 1
                    moves.append(m if side == "long" else -m)
                    fires.append(i)
                if len(moves) < MIN_N:
                    continue
                wins = [m for m in moves if m > 0]
                losses = [-m for m in moves if m <= 0]
                pwin = len(wins) / len(moves)
                # 보정 — 백테스트 승률을 '그 값이 실제로 뜻하는 승률'로 바꾼다.
                # 워크포워드 실측(14종목·5시간봉·22신호, 167만건)에서, 백테스트가
                # 55%를 넘기면 실제는 51~52%로 무너졌다: 예측 61.8%→실제 50.8%,
                # 73.4%→51.0%, 84.3%→58.1%. 반대로 낮은 예측은 올라온다(40.9%→48.4%).
                # 즉 55% 위 숫자는 정보가 아니라 과적합이다. 봇은 승률 높은 순으로
                # 고르기 때문에, 보정 없이는 하필 그 구간만 골라 진입하게 된다
                # (2026-08-04 금·은 77% 사건이 정확히 이것).
                # 보정표가 없으면(미측정) 아래 증거 수축으로 넘어간다.
                # 순서: ① 신호 실측이 있으면 그것을 쓴다 ② 없으면 보정 곡선
                # ①이 우선인 이유 — 보정 곡선은 "이 숫자 믿지 마라"까지만 말하고
                # 55% 위를 전부 51%로 눌러 순위 정보를 없앤다. 실측(홀드아웃 검증)
                # 결과가 그걸 확인해줬다: 상위 5% 선택 시 백테 52.60% · 보정 53.14%로
                # 거의 차이가 없었고, 신호 실측으로 정렬하니 55.16%였다.
                # 종목별 백테 숫자는 그 종목의 우연이고, 신호의 성적은 다른 종목으로
                # 넘어간다. 종목별 차이는 avg_win/avg_loss(→EV)에 그대로 남아
                # 같은 신호 안에서의 미세 순위를 만든다.
                calibrated_ok = False
                try:
                    from .walkforward import calibrated, measured_winrate
                    _m = measured_winrate(name, tf)
                    if _m is not None:
                        pwin = _m[0]
                        calibrated_ok = True
                    else:
                        _c = calibrated(pwin)
                        if _c is not None:
                            pwin = _c
                            calibrated_ok = True
                except Exception:
                    pass
                # 실전 관측이 쌓였으면 그 승률을 섞는다 (과거 백테스트 + 실전 학습)
                combo_note = ""
                live_n = 0
                try:
                    from .observer import combo_winrate, signal_winrate
                    live_wr = signal_winrate(sym, tf, name)
                    if live_wr:
                        lw, ln = live_wr
                        live_n = ln
                        # 실전 표본 가중 — 40건에 60%는 너무 얇아 국면전환 때
                        # 과적합된다. 100건에 최대 40%로 낮춰, 실전 track record가
                        # 충분히 쌓이기 전엔 백테스트를 더 신뢰한다.
                        w = min(ln / 100, 0.4)
                        pwin = pwin * (1 - w) + lw * w
                    # 조합 부스트: 지금 이 신호가 다른 신호들과 동시 점등이고,
                    # 그 조합이 이 신호와 방향이 같고, 학습된 조합 승률이 더 높으면 상향
                    if len(co_active) >= 2:
                        from .market_context import fear_greed
                        fg = fear_greed()
                        reg = ("fear" if fg and fg[0] <= 25 else
                               "greed" if fg and fg[0] >= 75 else "neutral")
                        cw = combo_winrate(co_active, tf, reg)
                        if cw and cw[2] == side and cw[0] > pwin:
                            cwr, cn, _ = cw
                            cwgt = min(cn / 40, 0.5)
                            pwin = pwin * (1 - cwgt) + cwr * cwgt
                            combo_note = f" +조합({'+'.join(co_active[:3])}@{reg} {cwr:.0%})"
                except Exception:
                    pass
                base = base_up if side == "long" else 1 - base_up
                # 승률이 실측표에서 온 경우 비교 기준은 0.5다. 실측값은 전 종목·
                # 전 기간을 통합해 잰 값이라 이미 드리프트를 품고 있는데, 그걸
                # 이 종목의 드리프트(base_up)와 다시 비교하면 기준이 어긋난다
                # (강한 추세 종목일수록 좋은 신호가 부당하게 탈락한다).
                if calibrated_ok:
                    base = 0.5
                # 수축의 목표점 — 기본은 기준선(base)이지만, 이 신호가 실제로 내는
                # 성적(전 심볼 합산 실측)이 있으면 그쪽으로 끌어당긴다. 신호마다
                # 다르게 대접하게 되는 지점이다: 실측이 나쁜 신호는 더 깎이고,
                # 좋은 신호는 덜 깎인다.
                # 영향력은 두 겹으로 자동으로 커진다 — (1) 표본이 쌓일수록 (2) 수집
                # 기간이 길어질수록. 지금은 데이터가 짧은 한 구간이라 절반쯤만
                # 반영되고, 여러 국면에 걸치면 저절로 제 몫을 한다. 설정 변경 불필요.
                target = base
                try:
                    from .observer import pooled_winrate, MIN_SPAN_DAYS
                    pw = pooled_winrate(tf, name)
                    if pw:
                        p_wr, p_n, p_span = pw
                        w = (p_n / (p_n + POOLED_PRIOR_N)) * min(
                            p_span / MIN_SPAN_DAYS, 1.0)
                        target = base * (1 - w) + p_wr * w
                except Exception:
                    pass
                # 증거 수축(shrinkage) — 근거가 얇을수록 승률을 목표점 쪽으로 끌어내린다.
                # 관문에 걸려 실전 확인이 없으면 백테스트 하나만 남는데, 예전 코드는
                # 그걸 100% 믿었다. 백테스트 60% = "확실히 60%"가 아니라 "표본이
                # 말하는 60%"이고, 표본이 적을수록 우연일 확률이 크다. 그래서
                # 실효표본 n_eff 로 신뢰폭 k 를 정해 초과분(pwin-base)만 줄인다.
                # 실전 관측은 실제 체결·국면을 겪은 값이라 백테스트 캔들보다
                # 무겁게(3배) 센다. 관문 통과분만 들어오므로 뭉친 데이터는 애초에 0.
                # (2026-08-04: 실전 근거 0인 상태에서 금·은 sRSI 과열 숏을 승률
                #  77%로 믿고 진입. 실측은 18~22%였다.)
                # 보정표가 있으면 수축은 건너뛴다 — 같은 문제를 두 번 깎게 된다.
                # 수축은 표본 크기로 깎는 추정 방식이었는데, 워크포워드 실측은
                # 표본 크기가 주범이 아니라고 말한다(30~60건 오차 -0.5%, 800건+
                # 오차 0.0%). 진짜 위험 신호는 표본이 아니라 예측의 극단성이고,
                # 그건 보정표가 직접 잰다. 수축은 보정표가 없을 때의 대비책으로 남긴다.
                if not calibrated_ok:
                    n_eff = len(moves) + live_n * LIVE_SAMPLE_WEIGHT
                    pwin = target + (pwin - target) * (n_eff / (n_eff + EVIDENCE_PRIOR))
                if pwin - base < MIN_EDGE:
                    continue
                avg_win = sum(wins) / len(wins) if wins else 0
                # 전승(losses 없음)이면 avg_loss를 0.001로 두던 옛 코드는 EV를
                # 폭발시켜 표본 적은 신호를 과대평가했다. 손실 표본이 없으면
                # 보수적으로 평균이익만큼을 잠재손실로 가정(손익비 1로 하향).
                avg_loss = (sum(losses) / len(losses) if losses
                            else max(avg_win, 0.005))
                # 손절선을 먼저 확정하고 그 값으로 EV를 검사한다.
                # 예전엔 avg_loss 로 통과 판정을 하고 실제로는 max(avg_loss, 0.005)
                # 를 걸었다. avg_loss 가 0.5% 미만이면 관문이 본 손실보다 실제
                # 손실이 커서, 통과 당시 플러스였던 EV가 실전에선 마이너스가 됐다.
                # (2026-08-05 실측: BTC 5m 볼린저 하단이탈 — 목표 +0.392% 인데
                #  손절이 바닥값 0.5%로 올라가 실제 EV -0.088%. 그 상태로 진입했다.)
                sl = max(avg_loss, 0.005) * SL_WIDTH_MULT
                tp = avg_win
                horizon_h = INTERVAL_MS[tf] * FWD / 3_600_000
                # ── 24시간 이상 지평은 '스윙'으로 다르게 잡는다 ──
                # 실측(2026-08-05, 1시간봉 9년 10종목, 겹침 제거 59,750건):
                # 손절을 좁히고 익절을 넓히면 승률은 55%→39%로 떨어지지만
                # 손익비가 3.3배가 되어 기대값이 크게 는다. 기간을 3등분해도
                # 모든 구간에서 유지됐다(앞 +1.83% / 가운데 +1.68% / 뒤 +1.16%).
                #
                # 왜 되는지: 방향 예측력은 24시간 52.9%, 1주면 50%(무작위)라
                # 사실상 기여가 없다. 이 구조의 손익분기 승률이 24.3%인데
                # 실측이 38.9%라서 남는 것이다 — 방향이 아니라 '출렁임'이
                # 수익원이다. 그래서 아래 변동성 관문이 핵심이 된다.
                # ── 스윙 고정 손절/익절은 철회했다 (2026-08-05) ──
                # 손절1.5%/익절5.0%을 1시간봉에 강제하던 블록이 여기 있었다.
                # 채택 근거가 종가 기준 측정이었는데, 종가만 보면 봉 안에서
                # 손절을 스치고 회복한 경우를 못 봐 EV가 크게 부풀려진다.
                # 같은 설정을 고가/저가로 다시 재니:
                #   종가만        +2.549%   ← 이걸 보고 채택했다
                #   고저·익절우선  -0.013%   (가장 낙관적인 가정)
                #   고저·손절우선  -0.647%
                # 낙관 가정으로도 0 이하라 판정 방식과 무관하게 마이너스다.
                # 풀 백테스트도 독립적으로 같은 답을 냈다 — 1시간봉 517건
                # 승률 36.9% 건당 -$0.315 (수익은 전부 4h/1d에서 나왔다).
                # 그래서 모든 시간봉이 자기 측정값(avg_win/avg_loss)을 쓴다.
                # 변동성 관문도 같은 종가 기준으로 고른 것이라 함께 뺐다.
                # ── 기대값은 '경로'로 계산한다 (단타·스윙 공통) ──
                # ⚠️ pwin × avg_win − (1−pwin) × sl 은 틀린 식이다. pwin 은
                # '지평 끝에 방향이 맞나'인데(실측 55%), 실제 청산은 익절·손절 중
                # 먼저 닿는 쪽이라 확률이 다르다(익절 5%를 먼저 칠 확률은 39%).
                # 섞어 쓰면 스윙 EV가 실제(+0.32%)의 6배(+2.1%)로 나왔고,
                # 단타는 하루 +18% 같은 값이 찍혔다(실측은 마이너스).
                # 그래서 과거 발생 지점마다 경로를 따라가 먼저 닿는 쪽으로 판정한다.
                # 어느 쪽도 안 닿으면 만기 종가로 청산한다(스윙 실측 26%).
                # 도달 판정은 봉의 고가/저가로 한다. 종가만 보면 '봉 안에서
                # 손절을 스치고 회복한' 경우를 놓쳐 손절율이 과소평가된다 —
                # 실측(1시간봉 4.4만건): 손절율 55%(종가) vs 68%(고저),
                # EV +0.298% vs -0.148% 로 부호까지 뒤집혔다.
                # 한 봉에서 양쪽 다 닿으면 손절을 먼저 적용한다(보수적).
                path = []
                for fi in fires:
                    e0 = closes[fi]
                    if e0 <= 0:
                        continue
                    r = None
                    for k in range(1, FWD + 1):
                        hi = ohlc[fi + k][2] / e0 - 1.0
                        lo = ohlc[fi + k][3] / e0 - 1.0
                        adverse = -lo if side == "long" else hi
                        favor = hi if side == "long" else -lo
                        if adverse >= sl:
                            r = -sl
                            break
                        if favor >= tp:
                            r = tp
                            break
                    if r is None:
                        mv = closes[fi + FWD] / e0 - 1.0
                        r = mv if side == "long" else -mv
                    path.append(r)
                # 경로 평가가 가능한 표본이 위 관문과 같아야 한다 — 위에서
                # 통과시킨 것을 여기서 다른 기준으로 또 자르면 어긋난다.
                if len(path) < MIN_N:
                    continue
                raw_ev = sum(path) / len(path)
                # ── 선별 편향(승자의 저주) 보정 ──
                # 표본이 적은 셋업은 EV가 틀리는 게 아니라 '흔들린다'. 그런데
                # 우리는 상위만 골라 진입하므로, 운 좋게 높게 나온 쪽이 계통적으로
                # 뽑힌다. 2026-08-05 실측(메이저 10종목, 상위3 선별):
                #   추정창 60일  → 예측 +5.04% / 실현 +2.15%  (2.34배 부풀림)
                #   추정창 3년   → 예측 +3.58% / 실현 +2.89%  (1.24배)
                # 추정 자체는 부풀지 않았다(고르지 않고 재면 0.88~0.94배로 오히려
                # 낮게 나온다). 부풀림은 '고르는 행위'에서만 생기고, 표본이 적을수록
                # 커진다. 그래서 표본 깊이에 비례해 EV를 끌어내린다 — 신규 상장이
                # 짧은 호황 구간만으로 상위를 독식하는 것을 막는다(진입 금지가
                # 아니라 순위·크기에서 제 몫만 갖게 하는 것).
                #
                # ⚠️ 보정은 '순위'에만 건다(아래 score). 관문에까지 걸면 표본이
                # 적다는 이유로 셋업 자체가 사라진다 — 실제로 17개가 4개로
                # 줄었다. 부풀림은 추정값이 틀려서가 아니라 '제일 좋아 보이는
                # 것을 고르는 행위'에서 생기므로, 고르는 단계에서만 눌러야 한다.
                if raw_ev - FEE_RT <= 0:
                    continue
                # 지금 이 순간 신호가 켜져 있는가 (마지막 캔들) — 필터가 아니라 표시용
                try:
                    live = bool(cond(n - 1))
                except (TypeError, IndexError):
                    live = False
                # 레버리지: 손절가가 청산가의 절반 이내 거리에 오도록.
                # 정책 상한(max_leverage)을 여기서 함께 적용해야 한다 — 예전엔
                # 거래소 상한(40배)으로 레버리지를 잡아 증거금을 계산해놓고,
                # 실행 단계에서 정책 상한(3배)으로 낮춰 주문했다. 증거금은
                # 36배 기준으로 산출된 작은 값인데 3배로 곱하니 포지션이 12배
                # 작아졌고, risk_per_trade 2%가 실제로는 0.17%로 작동했다.
                # (2026-08-05 실측: BTC 손절시 손실이 자본의 0.17%였다.)
                # sl 은 위 EV 관문에서 이미 확정했다.
                cap_lev = min(max_lev, int(max_leverage)) if max_leverage else max_lev
                lev = max(1, min(cap_lev, int(1 / (2.5 * sl))))
                _risk = risk_pct if risk_pct > 0 else RISK_PCT
                margin = min(budget_usd, budget_usd * _risk / (sl * lev)
                             if sl * lev > 0 else budget_usd)
                margin = max(margin, 10 / lev)  # 최소 주문 $10 충족
                if margin < MIN_MARGIN_USD:
                    continue        # 너무 잘게 쪼개진 자리는 건너뛴다
                # 증거금 하한 — 너무 잘게 쪼개진 자리는 아예 잡지 않는다.
                if margin < MIN_MARGIN_USD:
                    continue
                setups.append(Setup(
                    symbol=sym, horizon=hz, interval=tf,
                    signal=name + combo_note, side=side,
                    entry=float(p["mid"]), win_rate=pwin, n_samples=len(moves),
                    tp_move=tp, sl_move=sl, leverage=lev,
                    margin_usd=round(margin, 2),
                    ev_margin=(raw_ev - FEE_RT) * lev,
                    horizon_hours=horizon_h,
                    live=live, ts=int(time.time() * 1000)))
    # 종합 점수: '하루당' 기대수익 기준. 표본이 많고 승률이 기준선을 크게
    # 이긴 신호에 가중한다. "승률만" 또는 "수익만"이 아니라 둘 다 본다.
    #
    # 하루당으로 나누는 이유: 보유 시간이 다른 셋업을 1회 EV로 줄 세우면
    # 불공평하다. 2시간짜리 EV +0.5%는 하루 12회전이라 +6%/일이고,
    # 24시간짜리 EV +1.5%는 +1.5%/일이다. 1회 EV로 보면 후자가 이기지만
    # 실제로는 전자가 4배 낫다. 슬롯이 희소 자원이므로 회전율을 반영해야
    # 단타·스윙이 같은 자로 경쟁한다. (국면이 바뀌어 단타가 유리해지면
    # 시간봉을 지워두지 않았으므로 자동으로 되돌아온다)
    def score(x):
        # 표본 깊이 보정 = 승자의 저주 대책. n/(n+prior) 로 표본이 얕은 셋업의
        # 순위를 끌어내린다. 예전엔 min(n/100, 1.5) 였는데 n>=150 이면 전부
        # 1.5로 같아져 깊이 차이를 못 봤다 — 표본 60건과 3000건이 동점이었다.
        depth = x.n_samples / (x.n_samples + EV_SHRINK_PRIOR_N)
        per_day = x.ev_margin * 24 / max(x.horizon_hours, 1e-9)
        return per_day * (0.5 + 0.5 * x.win_rate) * depth
    setups.sort(key=score, reverse=True)
    return setups


def format_setups(setups, top=3):
    if not setups:
        return ("통계 관문(표본 30+, 순수엣지 +2%p, 수수료 차감 기대값 양수)을 "
                "통과한 셋업이 지금은 없습니다. 없는 게 정직한 답입니다.")
    live = [s for s in setups if s.live]
    watch = [s for s in setups if not s.live]

    def fmt(st, i):
        hz_d = st.horizon_hours / 24
        hz_txt = f"{st.horizon_hours:.0f}시간" if hz_d < 1 else f"{hz_d:.0f}일"
        return (
            f"{i}) {st.symbol} · {st.horizon}({st.interval}) · "
            f"{'롱' if st.side == 'long' else '숏'} — 신호: {st.signal}\n"
            f"   타점: {st.entry:,.6g} 부근 · {st.leverage}배 · 증거금 ${st.margin_usd:,.0f}\n"
            f"   목표: 가격 {st.tp_move:+.2%} = 원금 {st.tp_move * st.leverage:+.1%} / "
            f"손절: 가격 {-st.sl_move:.2%} = 원금 {-st.sl_move * st.leverage:.1%}\n"
            f"   과거 승률: {st.win_rate:.0%} (발생 {st.n_samples}회, 보유 ~{hz_txt}) · "
            f"수수료 반영 기대값: 원금 대비 {st.ev_margin:+.1%}")

    lines = []
    if live:
        lines.append("🔆 지금 진입 조건 충족 (신호 점등 중)\n")
        for i, st in enumerate(live[:top], 1):
            lines.append(fmt(st, i))
    else:
        lines.append("지금 당장 점등된 셋업은 없음. 아래는 통계 최강 감시 목록 —\n"
                     "해당 신호가 켜지면 진입 후보입니다.\n")
    if watch and (not live or len(live) < top):
        lines.append("\n📋 감시 목록 (통계 상위, 조건 대기 중)")
        for i, st in enumerate(watch[:top], 1):
            lines.append(fmt(st, i))
    lines.append("\n⚠️ 승률은 이 코인·시간봉의 과거 국면 실측치이며 미래 보장이 아닙니다. "
                 "점등 셋업은 predictions.json에 기록되어 사후 검증됩니다.")
    return "\n".join(lines)


def _load_predictions() -> list:
    """예측 기록 읽기. 깨진 파일이면 빈 목록으로 시작하되 원본은 남긴다."""
    if not os.path.exists(PREDICTIONS_FILE):
        return []
    try:
        with open(PREDICTIONS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        # 깨진 파일을 그대로 두면 이후 모든 호출이 영구 실패한다. 옆으로
        # 치워 두고(진단용) 새로 시작한다.
        try:
            os.replace(PREDICTIONS_FILE, PREDICTIONS_FILE + ".corrupt")
        except OSError:
            pass
        return []


def _save_predictions(data: list) -> None:
    """임시 파일에 쓰고 교체 — 쓰는 도중 죽어도 원본이 살아 있다.

    다른 모듈(brain·observer·autonomous·historical_data)은 모두 이 방식인데
    여기만 open(...,'w') 로 바로 덮어쓰고 있었다. 그러면 쓰다가 중단될 때
    predictions.json 이 깨지고 top_setups·review_predictions 가 영구 실패한다."""
    tmp = PREDICTIONS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, PREDICTIONS_FILE)


def log_predictions(setups, top=3):
    data = _load_predictions()
    # 실제로 지금 점등된 셋업만 예측으로 기록 (감시 목록은 진입 안 했으므로 제외)
    for st in [s for s in setups if s.live][:top]:
        rec = asdict(st)
        rec["status"] = "open"
        data.append(rec)
    _save_predictions(data)


def review(client):
    data = _load_predictions()
    if not data:
        return "기록된 예측이 없습니다."
    now = int(time.time() * 1000)
    checked = wins = 0
    pred_p = []
    lines = []
    for rec in data:
        due = rec["ts"] + rec["horizon_hours"] * 3_600_000
        if rec.get("status") != "open" or now < due:
            continue
        closes = fetch_closes(client, rec["symbol"], rec["interval"],
                              max_bars=FWD + 60)
        if not closes:
            continue
        move = closes[-1] / rec["entry"] - 1
        signed = move if rec["side"] == "long" else -move
        ok = signed > 0
        rec["status"] = "win" if ok else "loss"
        rec["realized_move"] = move
        checked += 1
        wins += ok
        pred_p.append(rec["win_rate"])
        lines.append(f"  {rec['symbol']} {rec['side']} ({rec['signal']}, "
                     f"예측승률 {rec['win_rate']:.0%}) → {'✅ 적중' if ok else '❌ 빗나감'} "
                     f"({signed:+.2%})")
    _save_predictions(data)
    total_done = [r for r in data if r.get("status") in ("win", "loss")]
    if not total_done:
        return "만기 도달한 예측이 아직 없습니다 (보유 지평이 지나야 채점 가능)."
    all_wins = sum(1 for r in total_done if r["status"] == "win")
    avg_pred = sum(r["win_rate"] for r in total_done) / len(total_done)
    out = [f"이번에 채점: {checked}건"] + lines
    out.append(f"\n누적 성적: {all_wins}/{len(total_done)} 적중 "
               f"({all_wins/len(total_done):.0%}) vs 예측 평균 승률 {avg_pred:.0%}")
    gap = all_wins / len(total_done) - avg_pred
    if gap < -0.10:
        out.append("→ 실제가 예측보다 10%p 이상 낮음: 국면 변화 가능성. "
                   "신호 기준 재측정(research/ 스크립트) 권장")
    elif gap > 0.10:
        out.append("→ 실제가 예측을 상회: 현 기준 유지")
    else:
        out.append("→ 예측과 실제가 부합 (캘리브레이션 양호)")
    return "\n".join(out)


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--top", type=int, default=3)
    p.add_argument("--budget", type=float, default=100.0)
    p.add_argument("--universe", type=int, default=15, help="거래량 상위 N개 스캔")
    p.add_argument("--review", action="store_true", help="과거 예측 채점")
    p.add_argument("--url", default="https://api.pacifica.fi")
    args = p.parse_args()

    client = PacificaClient(args.url)
    if args.review:
        print(review(client))
        return
    print(f"거래량 상위 {args.universe}개 코인 × 단타(1h)/스윙(12h)/장타(1d) 스캔 중...\n")
    setups = evaluate_setups(client, args.budget, args.universe)
    print(format_setups(setups, args.top))
    if setups:
        log_predictions(setups, args.top)
        print(f"\n(예측 {min(args.top, len(setups))}건 기록됨 → "
              f"나중에 --review 로 채점)")


if __name__ == "__main__":
    main()
