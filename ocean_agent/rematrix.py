"""자가 재측정 엔진 (Self-Remeasurement).

봇의 진짜 자기개선 장치. 주기적으로 '전 코인 × 전 시간봉 × 전 신호' 매트릭스를
다시 측정해서, 지금 국면에서 어느 시간봉·어느 신호가 실제로 돈이 되는지 갱신한다.

왜 이게 관측 DB보다 중요한가:
- 관측 DB(실시간 채점)는 신호 하나당 30건 모으는 데 몇 달이 걸린다.
- 매트릭스는 20분 만에 수만 건의 표본을 다시 만든다.
- 실제로 2026-07-09 측정에선 8시간봉이 '전부 무효'였는데 07-21 재측정에선
  최고 구간이 됐다. 국면은 바뀌고, 고정된 파라미터는 낡는다.

관측 DB의 역할은 '백테스트가 틀리기 시작했는지' 감지하는 알람이고,
이 모듈이 '무엇이 맞는지'를 다시 계산하는 본체다.

실행:
  python -m ocean_agent.rematrix            # 재측정 (파시피카만, 약 20분)
  python -m ocean_agent.rematrix --extended # + 바이낸스 과거 종가로 표본 증강
  python -m ocean_agent.rematrix --show     # 마지막 측정 결과 요약
"""

import json
import os
import subprocess
import sys
import time

MATRIX_FILE = os.path.join(os.path.expanduser("~"), ".ocean_agent_matrix.json")
LOCK_FILE = MATRIX_FILE + ".running"
STALE_LOCK_SEC = 10800   # a lock older than 3h means that run died mid-way


def _acquire_lock(log=print) -> bool:
    """Create the lock file atomically (O_CREAT|O_EXCL, no check-then-write
    race). Returns False while another measurement holds a live lock. A stale
    lock is taken over, loudly, because the previous run most likely died
    before its release (e.g. hard kill)."""
    for _ in range(2):
        try:
            fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w") as f:
                f.write(str(time.time()))
            return True
        except FileExistsError:
            try:
                age = time.time() - os.path.getmtime(LOCK_FILE)
            except OSError:
                continue        # lock vanished between open and stat, retry
            if age < STALE_LOCK_SEC:
                return False    # a measurement is still running
            log(f"재측정 락이 {age / 3600:.1f}시간째 방치됨, "
                f"죽은 실행으로 보고 인수")
            try:
                os.remove(LOCK_FILE)
            except OSError:
                return False
        except OSError:
            return False        # cannot create the lock: fail closed
    return False


def _release_lock() -> None:
    try:
        os.remove(LOCK_FILE)
    except OSError:
        pass

# 측정 대상 = 바이낸스 현물 매핑이 있는 파시피카 종목 전부.
#
# 목록을 손으로 적어두는 방식이었고, 그래서 매트릭스가 19종목으로 돌았다.
# 봇은 75개 시장을 거래하는데 표본의 71%를 상위 10종목이 냈고, 요즘 실제로
# 뽑히는 종목(KAITO·MEGA·LDO·kBONK·WLFI 등)은 매트릭스에 아예 없어서
# 그 신호들이 BTC·ETH·LTC 에서 통한 값으로 판정되고 있었다.
# 이제 historical_data.BINANCE_SYMBOL(2026-08-12에 exchangeInfo 전수 대조로
# 43종까지 넓힘)에서 직접 끌어온다, 표를 한 군데만 고치면 된다.
#
# 매핑이 없는 종목(HYPE·주식·원자재·SP500 등)은 넣지 않는다. 파시피카 보관분
# 3,000봉만으로는 8시간봉·일봉에서 표본이 한 자릿수라, 매트릭스에 들어와도
# 근거가 되기보다 잡음이 된다. run_measurement 가 제외 목록과 사유를 찍는다.
from .historical_data import BINANCE_SYMBOL

COINS = sorted(BINANCE_SYMBOL)
# 5분봉은 안 쓴다 (사용자 지시 2026-08-12·08-13). 라이브 채점 경로가 읽지
# 않는데 캐시만 298MB 차지하고 매트릭스 계산 시간도 먹었다.
TFS = ["15m", "30m", "1h", "2h", "4h", "8h", "12h", "1d"]
FEE_RT = 0.0008
MIN_N = 20              # 이 미만 표본은 판단 근거로 쓰지 않음
DEFAULT_HORIZONS = {"단타": "4h", "스윙": "8h", "장타": "12h"}

# ---------- 표본 구간의 앞 경계 ----------
# 2021-01-01 부터 잰다. 2017~2020 국면은 이 봇이 거래하는 시장이 아니고
# (상장 종목도, 유동성도, 참여자도 다르다), 그것 없이도 표본은 이미 충분하다.
# 9년치를 끌어오는 건 비용만 크고 판단을 낡은 국면 쪽으로 끌어당긴다.
MATRIX_START_MS = 1_609_459_200_000        # 2021-01-01 00:00 UTC

# 15분·30분봉을 60일로 자르던 규칙은 2026-08-21 에 풀었다.
#
# 자른 근거는 "봇의 실전 채점 경로는 1h·4h·8h·12h 를 쓴다" 였는데, 08-20 재계산
# 에서 best_horizons() 가 단타로 15분봉을 골랐다. 전제가 깨진 것이다. 그 상태
# 에서는 60일짜리 기대값과 5.5년짜리 기대값을 한 표에 놓고 1등을 뽑게 되고,
# 운 좋은 60일이 이긴다. 국면이 하나뿐인 창은 국면을 못 이긴다.
#
# 다운로드 비용이 컸다는 당시 판단도 지금은 안 맞는다. 캐시에 이미 15분봉
# 520만 봉(146MB) · 30분봉 260만 봉(76MB) 이 중앙 1,195일 · 최대 2,050일치로
# 들어와 있다. 매매 쪽이 이미 받아둔 것이고, 여기서 안 쓰고 있었을 뿐이다.
#
# 5분봉은 여전히 안 쓴다 (사용자 지시 2026-08-12·08-13). TFS 에 없다.
CAPPED_TFS: dict[str, int] = {}   # 시간봉 → 최근 며칠만. 지금은 전부 전 구간


def _tf_start_ms(tf: str) -> int:
    """이 시간봉에 쓸 과거 구간의 앞 경계(ms)."""
    days = CAPPED_TFS.get(tf)
    if days:
        return int(time.time() * 1000) - days * 86_400_000
    return MATRIX_START_MS


# ---------- 측정 ----------

def _fetch(client, symbol, interval, max_bars=3000, use_extended=False,
           log=print):
    """파시피카 kline 종가를 과거→현재로 반환.

    use_extended=False(기본): 아래 파시피카 전용 경로만 실행, 기존과 완전 동일.
    use_extended=True: 파시피카 종가 앞에 바이낸스 과거 종가를 이어붙여 표본을
        두껍게 한다(가격 전용. 펀딩·호가·OI는 절대 안 가져옴). 겹침 괴리가 크거나
        바이낸스에 없는 심볼이면 자동으로 파시피카만 반환(안전 폴백)."""
    from .indicators import INTERVAL_MS
    step = INTERVAL_MS[interval]
    end = int(time.time() * 1000)
    cur = end - step * max_bars
    out = []
    guard = 0
    while cur < end and guard < 40:
        guard += 1
        try:
            r = client.session.get(f"{client.base}/kline", params={
                "symbol": symbol, "interval": interval,
                "start_time": cur, "end_time": end}, timeout=25).json()
        except Exception:
            time.sleep(8)
            continue
        d = r.get("data") or []
        if not d:
            if "too large" in str(r.get("error", "")).lower():
                cur += (end - cur) // 2
                continue
            break
        out.extend(d)
        last = d[-1]["t"]
        if last <= cur:
            break
        cur = last + step
    # 마지막 미완성 캔들 제거, 백테스트 look-ahead 방지 (진행중 종가는 변함)
    now = int(time.time() * 1000)
    while out and int(out[-1]["t"]) + step > now:
        out.pop()
    pacifica_closes = [float(c["c"]) for c in out]
    if not use_extended:
        return pacifica_closes   # ← 기존 경로, 바이트 단위 동일
    # 증강 경로 (가격 종가만, 실패 시 파시피카만 반환)
    try:
        from .historical_data import extended_closes
        return extended_closes(client, symbol, interval, pacifica_closes,
                               log=log)
    except Exception as e:
        log(f"  증강 폴백({symbol} {interval}): {e}")
        return pacifica_closes


def _fetch_ohlc(client, symbol, interval, use_extended=False, log=print,
                start_ms=None, use_underlying=False):
    """봉 전체(o/h/l/c)를 과거→현재로. 손절 도달 판정에 고가·저가가 필요하다.

    종가만으로는 '봉 안에서 손절을 스치고 회복한' 경우를 못 봐 손절률이
    과소평가된다(실측: 55% vs 실제 68%). extended_ohlc 가 겹침 검증·안전
    폴백을 모두 처리하므로 여기서는 호출만 한다.

    start_ms=None 이면 이 시간봉의 기본 경계(_tf_start_ms)를 쓴다.

    use_underlying=True: 주식·RWA 토큰만 해당. 접합본(ub_ 캐시)이 있으면 원본
        시장 과거를 앞에 붙인 시계열을 쓴다. 접합본이 없으면 아래 기존 경로로
        그대로 떨어진다. 바이낸스 증강과 겹치는 종목은 없다(주식·원자재는
        바이낸스 매핑 자체가 없다). 기본값 False 라 이 인자를 안 주면 동작은
        예전과 한 바이트도 다르지 않다."""
    from .historical_data import extended_ohlc, _pacifica_ohlc
    try:
        st = _tf_start_ms(interval) if start_ms is None else start_ms
        if use_underlying:
            from .historical_data import (has_underlying_cache,
                                          underlying_ohlc)
            if has_underlying_cache(symbol, interval):
                return underlying_ohlc(client, symbol, interval, log=log,
                                       start_ms=st)
        if use_extended:
            return extended_ohlc(client, symbol, interval, log=log,
                                 start_ms=st)
        return _pacifica_ohlc(client, symbol, interval, log=log)
    except Exception as e:
        log(f"  OHLC 조회 실패({symbol} {interval}): {e}")
        return []


def _is_underlying_augmented(bars) -> bool:
    """이 시계열에 원본시장 유래 봉이 실제로 섞였는가.

    캐시 파일이 있는지가 아니라 '무엇이 돌아왔는지'로 판단한다. 파시피카가 이미
    그 구간을 덮으면 접합본이 있어도 붙는 원본 봉은 0개인데, 그런 행까지
    원본 유래로 태깅하면 태그가 거짓말을 하게 된다."""
    return any(b.get("source") not in ("pacifica", "binance") for b in bars)


def _path_result(bars, closes, i, side, sl, tp, fwd):
    """발생 i 에서 손절·익절 중 먼저 닿는 쪽으로 판정. 둘 다 안 닿으면 만기 종가.

    한 봉에서 양쪽 다 닿으면 손절을 먼저 적용한다.

    실전에는 이런 모호함이 없다, 익절·손절을 둘 다 거래소에 걸어두므로 먼저
    닿는 쪽이 체결되고 나머지는 취소된다. 봉 하나에 고가·저가만 남는 백테스트
    에서만 순서를 알 수 없다.

    어느 쪽으로 칠지는 2026-08-06에 실측으로 정했다. 8h/12h/1d 봉을 1시간봉으로
    쪼개 실제 순서를 확인해 비교했더니:
        8h   손절우선 -5.579% · 익절우선 -5.262% · 실제순서 -5.528%
        12h  손절우선 -7.322% · 익절우선 -7.055% · 실제순서 -7.264%
    한 봉에 둘 다 닿는 경우가 0.1~0.3%뿐이라(익절과 손절이 서로 멀다) 실제
    순서와의 차이가 0.05%p 에 그친다. 하위 봉을 들여다보는 복잡도를 더할
    이유가 없어 손절 우선으로 둔다."""
    e = closes[i]
    if e <= 0:
        return None
    long = side == "long"
    for k in range(1, fwd + 1):
        if i + k >= len(bars):
            return None
        hi = bars[i + k]["h"] / e - 1.0
        lo = bars[i + k]["l"] / e - 1.0
        adverse = -lo if long else hi
        favor = hi if long else -lo
        if adverse >= sl:
            return -sl
        if favor >= tp:
            return tp
    m = closes[i + fwd] / e - 1.0
    return m if long else -m


def _measure_series(closes, fwd, bars=None):
    """이 시계열에서 각 신호의 EV·승률을 측정하고, 기준선(무조건 진입)도 함께."""
    from .signal_scanner import _series, _signals
    n = len(closes)
    if n < 240 + fwd:
        return {}, {}
    moves_all = [closes[i + fwd] / closes[i] - 1.0 for i in range(220, n - fwd)]
    base = {}
    for side in ("long", "short"):
        mv = moves_all if side == "long" else [-m for m in moves_all]
        w = [m for m in mv if m > 0]
        l = [-m for m in mv if m <= 0]
        wr = len(w) / len(mv) if mv else 0
        aw = sum(w)/len(w) if w else 0
        al = sum(l)/len(l) if l else 0
        ev_end = wr * aw - (1 - wr) * al - FEE_RT
        ev = ev_end
        if bars:
            # 기준선도 같은 방식으로 재야 신호와 공정하게 비교된다.
            # The signal side widens its stop by SL_WIDTH_MULT to match the
            # live order; the baseline must use the exact same line or the
            # ev-vs-base gate compares two different games and zero-edge
            # signals pass it (review P5).
            from .signal_scanner import SL_WIDTH_MULT as _slw_b
            sl_b = max(al, 0.005) * _slw_b
            tot = cnt = 0
            for i in range(220, n - fwd, 3):      # 3봉 간격 표본 (속도)
                r = _path_result(bars, closes, i, side, sl_b, aw, fwd)
                if r is not None:
                    tot += r - FEE_RT
                    cnt += 1
            if cnt >= 50:
                ev = tot / cnt
        base[side] = {"wr": wr, "n": len(mv), "ev": ev, "ev_end": ev_end}
    # Fractals need the wick, not the close. §108 fixed that in the scanner
    # but this call kept passing closes only, so the gate that decides which
    # signals may trade was built from a different definition than the one
    # that fires. On 1000BONKUSDT 1h over 6,000 bars the two definitions
    # overlap on 329 of 819 close-based and 788 wick-based highs, so three
    # quarters of the events were not the same events.
    if bars and len(bars) == len(closes):
        s = _series(closes, [b["h"] for b in bars], [b["l"] for b in bars])
    else:
        s = _series(closes)
    res = {}
    for name, (side, cond) in _signals(s).items():
        moves, fired = [], []
        for i in range(220, n - fwd):
            try:
                if not cond(i):
                    continue
            except Exception:
                continue
            m = closes[i + fwd] / closes[i] - 1.0
            moves.append(m if side == "long" else -m)
            fired.append(i)
        if not moves:
            continue
        wins = [m for m in moves if m > 0]
        losses = [-m for m in moves if m <= 0]
        wr = len(wins) / len(moves)
        aw = sum(wins) / len(wins) if wins else 0.0
        al = sum(losses) / len(losses) if losses else 0.0
        ev_end = wr * aw - (1 - wr) * al - FEE_RT
        # ── 봇이 실제로 하는 방식으로 다시 잰다 ──
        # 손절 max(avg_loss, 0.5%) · 익절 avg_win 을 걸고 고가/저가로 도달 판정.
        # 이 값이 통과/차단을 정한다. ev_end 는 예전 방식(손절 없음)으로,
        # 두 판정이 어긋나는지 확인할 때만 쓴다.
        ev = ev_end
        if bars:
            # stop width must match the live order exactly: the scanner
            # sends max(avg_loss, 0.5%) x SL_WIDTH_MULT, so the gate is
            # measured with the same line or it approves a different trade
            # than the one that goes out (review S5)
            from .signal_scanner import SL_WIDTH_MULT as _slw
            sl = max(al, 0.005) * _slw
            tot = cnt = 0
            for i in fired:
                r = _path_result(bars, closes, i, side, sl, aw, fwd)
                if r is not None:
                    tot += r - FEE_RT
                    cnt += 1
            if cnt >= 30:
                ev = tot / cnt
        res[name] = {"side": side, "n": len(moves), "wr": wr, "aw": aw,
                     "al": al, "ev": ev, "ev_end": ev_end}
    return res, base


def _has_any_underlying(symbol: str) -> bool:
    from .historical_data import has_underlying_cache
    return any(has_underlying_cache(symbol, tf) for tf in TFS)


def _universe(client, log=print, use_underlying=False) -> list[str]:
    """실제로 잴 종목 목록. 파시피카 상장 × 바이낸스 매핑의 교집합.

    use_underlying=True 면 원본시장 접합본(ub_)이 있는 주식·RWA 토큰도 넣는다.
    이게 없으면 옵트인 플래그를 켜도 아무 일이 안 일어난다, 그 종목들은 여기서
    먼저 걸러져 _fetch_ohlc 까지 가지도 못하기 때문이다.

    제외된 종목과 사유를 반드시 찍는다. 조용히 빠지면 '왜 이 종목 신호는
    남의 종목 값으로 판정되나'를 다음 사람이 또 처음부터 찾아야 한다."""
    try:
        markets = sorted({m["symbol"] for m in client.get_markets()})
    except Exception as e:
        log(f"파시피카 종목 조회 실패({e}), 내장 목록 {len(COINS)}종으로 진행")
        return list(COINS)

    def ok(s):
        return s in BINANCE_SYMBOL or (use_underlying and _has_any_underlying(s))
    keep = [s for s in markets if ok(s)]
    drop = [s for s in markets if not ok(s)]
    if use_underlying:
        ub = [s for s in keep if s not in BINANCE_SYMBOL]
        log(f"원본시장 접합본으로 추가된 종목 {len(ub)}종: " + ", ".join(ub))
    log(f"측정 대상 {len(keep)}종목 / 파시피카 전체 {len(markets)}종목")
    if drop:
        log(f"제외 {len(drop)}종목 (바이낸스 현물 미상장 → 파시피카 보관분 "
            f"3,000봉뿐이라 상위 시간봉 표본이 한 자릿수, 근거가 되기보다 "
            f"잡음이 된다): " + ", ".join(drop))
    return keep or list(COINS)


TF_HOURS = {"15m": 0.25, "30m": 0.5, "1h": 1, "2h": 2,
            "4h": 4, "8h": 8, "12h": 12, "1d": 24}


def _fwd_for(tf: str, fwd_hours: int = 24) -> int:
    """몇 봉 앞을 보아야 그 시간봉에서 fwd_hours 가 되는가.

    fwd 는 캔들 수였다. 그래서 4시간봉은 96시간, 12시간봉은 288시간 뒤를 보고
    채점했는데 실제 매매는 24시간 만기다. 시간봉마다 지평이 다른 표를 한 장으로
    읽고 있었던 것이고, 22신호가 전부 기대값 마이너스라는 §105 의 판정도 그
    표에서 나왔다. 최소 1봉은 봐야 하므로 짧은 봉만 그대로 캔들 수가 커진다.
    """
    h = TF_HOURS.get(tf)
    if h is None:
        # TF_HOURS lists the timeframes the matrix measures, but analyze_chart
        # is a public tool and takes any interval the venue supports. Falling
        # back to the raw candle count gave 1w a 4,032-hour horizon and 1m a
        # four-minute one. Derive the hours from the interval itself instead,
        # so an unlisted timeframe is still scored at 24 hours.
        from .indicators import INTERVAL_MS
        ms = INTERVAL_MS.get(tf)
        if not ms:
            return fwd_hours
        h = ms / 3_600_000
    return max(1, round(fwd_hours / h))


def run_measurement(base_url="https://api.pacifica.fi", fwd=24,
                    log=print, use_extended=False, symbols=None,
                    use_underlying=False, out_path=None) -> dict:
    """전 조합 재측정 후 결과 저장. 오래 걸린다(약 20분), 백그라운드 권장.

    use_extended=False(기본): 파시피카 데이터만 (기존과 동일).
    use_extended=True: 바이낸스 과거 종가로 백테스트 표본을 두껍게 한다
        (가격 전용). 결과에 그 사실을 기록한다.
    use_underlying=True: 주식·RWA 토큰에 한해 원본시장(야후) 과거로 앞을 채운
        접합본을 쓴다. 접합본이 없는 종목·시간봉은 영향 없음. 이 경로로 잰 행에는
        src="underlying" 이 붙고, 파일 머리에도 어느 종목이 그랬는지 남는다.
        순수 토큰 측정과 섞어 읽는 사고를 막기 위한 것이니 지우지 말 것.
    symbols=None: 파시피카 상장 × 바이낸스 매핑 교집합을 자동으로 쓴다.
    out_path=None: 기본은 라이브 매트릭스(MATRIX_FILE). 실험용 측정은 여기에
        다른 경로를 줘서 라이브를 덮지 않고 따로 받을 수 있다."""
    from .api_client import PacificaClient
    client = PacificaClient(base_url)
    syms = list(symbols) if symbols else _universe(
        client, log=log, use_underlying=use_underlying)
    rows, bases = [], []
    aug_pairs = []
    for sym in syms:
        for tf in TFS:
            try:
                bars = _fetch_ohlc(client, sym, tf, use_extended=use_extended,
                                   log=log, use_underlying=use_underlying)
                closes = [x["c"] for x in bars]
            except Exception as e:
                log(f"  {sym} {tf}: 조회실패 {e}")
                time.sleep(4)
                continue
            r, b = _measure_series(closes, _fwd_for(tf, fwd), bars)
            if not r:
                time.sleep(2)
                continue
            # 파일이 아니라 실제로 돌아온 봉으로 판단한다.
            aug = use_underlying and _is_underlying_augmented(bars)
            if aug:
                aug_pairs.append(f"{sym}|{tf}")
            for name, d in r.items():
                row = {"sym": sym, "tf": tf, "sig": name, **d}
                if aug:
                    row["src"] = "underlying"
                rows.append(row)
            for side, d in b.items():
                bs = {"sym": sym, "tf": tf, "side": side, **d}
                if aug:
                    bs["src"] = "underlying"
                bases.append(bs)
            time.sleep(3)
    from .signal_scanner import SL_WIDTH_MULT as _slw_tag
    from .signal_scanner import DEFS_VERSION as _defs_tag
    out = {"measured_at": int(time.time() * 1000), "fwd": fwd,
           # which stop-width convention measured this file: without the tag
           # a 1x-measured matrix is indistinguishable from a 2x one and the
           # two get mixed silently for up to rematrix_every_days (review P5)
           "sl_width_mult": _slw_tag,
           # same argument for the signal definitions themselves. On 08-20 a
           # matrix measured at 17:25 gated signals whose sign moved at 19:58,
           # so a cell reading "short made money" approved a long entry and
           # nothing in the file said the two disagreed.
           "defs_version": _defs_tag,
           "rows": rows, "bases": bases, "extended": use_extended,
           # 어떤 구간·어떤 종목으로 잰 값인지 파일에 남긴다. 나중에 두 매트릭스를
           # 비교할 때 "표본이 왜 줄었나"를 파일만 보고 알 수 있어야 한다.
           "symbols": syms,
           # 원본시장 유래 구간이 실제로 섞인 곳. 행의 src 태그와 같은 사실을
           # 파일 머리에서도 한 번에 볼 수 있게 남긴다.
           "underlying": sorted({p.split("|")[0] for p in aug_pairs}),
           "underlying_pairs": sorted(aug_pairs),
           "start_ms": {tf: _tf_start_ms(tf) for tf in TFS}}
    path = out_path or MATRIX_FILE
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    os.replace(tmp, path)
    log(f"재측정 완료: {len(rows)}조합 → {path}"
        + (f" (원본증강 {len(aug_pairs)}칸)" if aug_pairs else ""))
    return out


# ---------- 결과 읽기 ----------

_MATRIX_CACHE = {"path": None, "mtime": None, "data": None}

# 패키지에 동봉하는 씨앗 매트릭스. 처음 설치한 사용자는 잰 것이 하나도 없는데,
# _matrix_rejects 가 "모르면 통과"라서 관문이 통째로 열린 채 돈다, 신호가 다
# 통과해 아무거나 진입하게 된다. 씨앗이 있으면 첫날부터 제대로 된 관문을 쓰고,
# 자기 재측정이 끝나면 홈 디렉터리 파일이 이것을 대체한다(씨앗은 안 건드림).
_SEED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "matrix_seed.json")


def _matrix_path() -> str | None:
    """읽을 매트릭스 경로. 사용자가 잰 것이 있으면 그것, 없으면 동봉 씨앗."""
    if os.path.exists(MATRIX_FILE):
        return MATRIX_FILE
    return _SEED_FILE if os.path.exists(_SEED_FILE) else None


def load_matrix() -> dict | None:
    """매트릭스 읽기. (경로, 수정시각) 기준 캐시.

    스캐너가 신호마다 signal_ev/baseline_ev 를 물어보기 때문에 한 사이클에
    수백 번 호출된다(실측 396회, 5.4초 = 스캔의 17%). 매번 수백 KB JSON을
    다시 파싱하고 있었다. mtime 기준이라 백그라운드 재측정이 파일을 갈아끼우면
    다음 호출에서 자동으로 새 값을 읽는다, 봇 재시작 필요 없음."""
    path = _matrix_path()
    if path is None:
        return None
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return None
    c = _MATRIX_CACHE
    if c["path"] == path and c["mtime"] == mt and c["data"] is not None:
        return c["data"]
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    c["path"] = path
    c["mtime"] = mt
    c["data"] = data
    c["ev_memo"] = {}          # 파일이 바뀌면 파생 메모도 함께 버린다
    c["base_memo"] = {}
    # The gate reads this table without checking the stamp, on purpose: fail
    # closed and every signal is blocked while a rebuild runs. But that window
    # is exactly the 08-20 accident, a table measured under one set of
    # definitions admitting entries under another, so it should not pass in
    # silence. Once per file, because this is called hundreds of times a cycle.
    try:
        from .signal_scanner import DEFS_VERSION
        if data.get("defs_version") != DEFS_VERSION:
            # stderr, not stdout: the MCP server speaks JSON-RPC on stdout
            # and any stray line there breaks the transport.
            print(f"⚠️ 매트릭스가 옛 정의로 잰 표다 "
                  f"(파일 {data.get('defs_version')} vs 코드 {DEFS_VERSION}). "
                  f"재측정이 끝날 때까지 관문은 이 표를 그대로 쓴다.",
                  file=sys.stderr, flush=True)
    except Exception:
        pass
    return data


def age_days(m: dict | None = None) -> float:
    m = m or load_matrix()
    if not m:
        return 1e9
    # A matrix measured under older signal definitions is as good as absent,
    # so report it as infinitely old and let ensure_fresh rebuild it. Reporting
    # it as missing instead would block every signal at the gate; reporting it
    # as fresh is what let the 08-20 sign mismatch stand for two hours.
    from .signal_scanner import DEFS_VERSION
    if m.get("defs_version") != DEFS_VERSION:
        return 1e9
    return (time.time() * 1000 - float(m.get("measured_at", 0))) / 86_400_000


HORIZON_TOP_K = 3        # 시간봉을 평가할 때 쓸 상위 신호 개수


def best_horizons(default: dict | None = None) -> dict:
    """측정된 EV 기준 상위 3개 시간봉을 단타/스윙/장타로 배정.

    ⚠️ 기준은 '평균'이 아니라 '그 시간봉의 상위 신호'다.
    예전엔 시간봉별 전 신호의 평균 EV로 골랐다. 그런데 봇은 관문을 통과한
    상위 신호만 거래하므로, 평균이 나빠도 좋은 신호가 몇 개 있는 시간봉을
    통째로 버리게 된다. 2026-08-06 실측(6종목, 앞 절반으로 고르고 뒤 절반으로
    채점, 미래참조 없음):

        시간봉   평균        상위3
        15m    -0.0965%   -0.0705%
        1h     -0.1194%   -0.1234%
        8h     -0.3595%   +0.4393%
        12h    -0.3063%   +0.8237%
        1d     -1.3252%   +1.0039%

        평균 순위   15m > 1h > 4h > 2h > 12h > 8h > 1d
        상위3 순위  1d > 12h > 8h > 2h > 15m > 1h > 4h    ← 거의 정반대

    일봉은 평균으로 꼴찌인데 상위 기준으론 1위다(-1.33% → +1.00%). 평균으로
    고르면 마이너스 시간봉만 남는다. 풀 백테스트도 같은 답을 냈다,
    1h 건당 -$0.315 / 4h +$0.805 / 1d +$2.703.

    신호 단위로 먼저 묶는 이유: 행이 (종목 × 시간봉 × 신호)라 그냥 상위 3행을
    뽑으면 운 좋은 한 종목이 독차지한다. 종목을 가로질러 신호별로 합친 뒤
    상위를 고른다, 그래야 '이 신호가 이 시간봉에서 통한다'를 재는 것이 된다.
    """
    default = default or DEFAULT_HORIZONS
    m = load_matrix()
    if not m:
        return default
    # (시간봉, 신호) -> 표본가중 EV
    per_sig = {}
    for r in m.get("rows", []):
        if r.get("n", 0) < MIN_N:
            continue
        k = (r["tf"], r["sig"])
        e = per_sig.setdefault(k, [0.0, 0])
        e[0] += r["ev"] * r["n"]
        e[1] += r["n"]
    by_tf = {}
    for (tf, _sig), (tot, n) in per_sig.items():
        if n > 0:
            by_tf.setdefault(tf, []).append(tot / n)
    scored = []
    for tf, evs in by_tf.items():
        if len(evs) < HORIZON_TOP_K:
            continue
        evs.sort(reverse=True)
        scored.append((sum(evs[:HORIZON_TOP_K]) / HORIZON_TOP_K, tf))
    if len(scored) < 3:
        return default
    scored.sort(reverse=True)
    from .indicators import INTERVAL_MS
    top = sorted((tf for _, tf in scored[:3]), key=lambda t: INTERVAL_MS[t])
    return {"단타": top[0], "스윙": top[1], "장타": top[2]}


def signal_ev(sig: str, tf: str) -> tuple[float, int] | None:
    """이 신호·시간봉의 측정된 평균 EV와 표본수 (전 코인 합산).

    결과를 메모한다, 행이 수천 개인데 스캐너가 신호마다 물어봐서, 한 사이클에
    전체 행 훑기가 수백 번 반복된다. 메모는 매트릭스가 갈아끼워지면 함께 비워진다."""
    m = load_matrix()
    if not m:
        return None
    memo = _MATRIX_CACHE.setdefault("ev_memo", {})
    key = (sig, tf)
    if key in memo:
        return memo[key]
    g = [r for r in m.get("rows", [])
         if r["sig"] == sig and r["tf"] == tf and r.get("n", 0) >= MIN_N]
    # The EV must be pooled by sample count, not averaged per symbol.
    # An unweighted mean paired with a summed n described two different
    # populations: ten thin symbols (20 firings each) could clear the
    # caller's n >= 200 gate while their noisy EVs outvoted a symbol with
    # 3,000 firings. Weighting by n makes the returned (ev, n) pair one
    # population, the same pooling best_horizons() already uses.
    tot_n = sum(r["n"] for r in g)
    out = (sum(r["ev"] * r["n"] for r in g) / tot_n, tot_n) if tot_n > 0 else None
    memo[key] = out
    return out


def baseline_ev(tf: str, side: str) -> float | None:
    """기준선: 신호 없이 그 방향으로 무조건 진입했을 때의 EV.
    신호의 '순수 엣지' = 신호EV − 기준선EV (하락장 덕분인지 실력인지 구분)."""
    m = load_matrix()
    if not m:
        return None
    memo = _MATRIX_CACHE.setdefault("base_memo", {})
    key = (tf, side)
    if key in memo:
        return memo[key]
    g = [b for b in m.get("bases", []) if b["tf"] == tf and b["side"] == side]
    out = sum(b["ev"] for b in g) / len(g) if g else None
    memo[key] = out
    return out


# ---------- 자동 갱신 ----------

def divergence_alarm() -> str | None:
    """조기경보: 관측DB(1h 실시간 채점)가 매트릭스와 크게 어긋나는가.

    관측은 24시간이면 채점되는 빠른 눈이고, 매트릭스는 13개월 느린 눈이다.
    빠른 눈이 '매트릭스가 좋다는 신호들이 실전에서 자꾸 진다'고 하면
    국면이 바뀌고 있다는 뜻 → 주간 일정을 기다리지 말고 재측정을 앞당긴다.
    """
    try:
        from .observer import _load_stats, _span_days
        stats = _load_stats()
    except Exception:
        return None
    m = load_matrix()
    if not m:
        return None
    # 매트릭스가 EV 양수로 보는 신호들의 '실시간 실전 승률'을 모은다
    good = {}
    for r in m.get("rows", []):
        if r.get("n", 0) >= MIN_N and r["ev"] > 0.005:
            good.setdefault(r["sig"], True)
    wins = total = 0
    for k, e in stats.items():
        if not isinstance(e, dict) or e.get("sig") not in good:
            continue
        if _span_days(e) < 3:      # 뭉친 표본 제외 (독립성)
            continue
        wins += e.get("win", 0)
        total += e.get("total", 0)
    if total < 30:
        return None                # 표본 부족, 경보 판단 불가
    live_wr = wins / total
    if live_wr < 0.40:             # 좋다던 신호들이 실전 40% 미만으로 무너짐
        return (f"관측 조기경보: 매트릭스 우량신호의 실시간 승률 {live_wr:.0%} "
                f"({total}건), 국면 변화 의심")
    return None


def ensure_fresh(policy: dict, log=print) -> bool:
    """오래됐으면(또는 조기경보 시) 백그라운드로 재측정을 띄운다."""
    every = float(policy.get("rematrix_every_days", 0) or 0)
    if every <= 0:
        return False
    alarm = divergence_alarm()
    if alarm and age_days() >= 1.0:     # 경보여도 하루 1회 이상은 안 돌림
        log(alarm + " → 재측정 앞당김")
    elif age_days() < every:
        return False
    # 중복 실행 방지. 측정 시간이 늘었다(심볼 6→14, 바이낸스 과거 구간 추가)
    #, 락이 1시간이면 측정이 아직 도는 중에 두 번째가 떠서 서로 덮어쓴다.
    # On success the lock is handed to the child process, which releases it
    # at the end of main(); the parent only releases when the spawn fails.
    if not _acquire_lock(log):
        return False
    try:
        # --extended: 바이낸스 과거 종가로 표본을 두껍게 한다. 이게 빠져 있어서
        # 2017년 데이터가 자동 측정에 한 번도 쓰인 적이 없었다(매트릭스에
        # extended=False로 기록돼 있었다). 상승·하락·횡보를 다 담자는 게 이
        # 모듈의 존재 이유인데, 파시피카 구간만 재던 셈이다.
        subprocess.Popen(
            [sys.executable, "-m", "ocean_agent.rematrix", "--extended"],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log(f"자가 재측정 시작 (마지막 측정 {age_days():.1f}일 전), "
            f"백그라운드, 완료되면 다음 사이클부터 반영")
        return True
    except Exception as e:
        log(f"재측정 실행 실패: {e}")
        _release_lock()   # child never started, so it cannot release it
        return False


def summary() -> str:
    m = load_matrix()
    if not m:
        return "측정 기록 없음, python -m ocean_agent.rematrix 로 최초 측정"
    rows = [r for r in m["rows"] if r.get("n", 0) >= MIN_N]
    src = "바이낸스 증강" if m.get("extended") else "파시피카만"
    if m.get("underlying"):
        # 원본시장 유래 구간이 섞인 종목이 있으면 요약에서 먼저 말한다.
        # 순수 토큰 측정으로 오해하는 것이 이 태그를 만든 이유다.
        src += f" + 원본증강 {len(m['underlying'])}종목"
    lines = [f"마지막 측정: {age_days(m):.1f}일 전 · {len(m['rows'])}조합 · 데이터 {src}",
             f"현재 선택 시간봉: {best_horizons()}"]
    agg = {}
    for r in rows:
        agg.setdefault(r["tf"], []).append(r["ev"])
    lines.append("시간봉별 평균 EV:")
    for tf, v in sorted(agg.items(), key=lambda kv: -sum(kv[1])/len(kv[1])):
        lines.append(f"  {tf:>4}: {sum(v)/len(v):+.2%} ({len(v)}조합)")
    sig = {}
    for r in rows:
        sig.setdefault(r["sig"], []).append(r["ev"])
    lines.append("신호별 평균 EV:")
    for s, v in sorted(sig.items(), key=lambda kv: -sum(kv[1])/len(kv[1])):
        lines.append(f"  {s[:20]:<20}: {sum(v)/len(v):+.2%} ({len(v)}조합)")
    return "\n".join(lines)


def main():
    if "--show" in sys.argv:
        print(summary())
        return
    # --extended: 바이낸스 과거 종가로 백테스트 표본을 두껍게 (가격 전용).
    # 기본은 파시피카만 (안전). 이 플래그를 줄 때만 다국면 데이터를 섞는다.
    ext = "--extended" in sys.argv
    # --underlying: 주식·RWA 토큰에 한해 원본시장 접합본을 쓴다(옵트인).
    # --out=경로: 라이브 매트릭스 대신 그 파일로 받는다(실험용).
    und = "--underlying" in sys.argv
    out_path = None
    for a in sys.argv[1:]:
        if a.startswith("--out="):
            out_path = a[len("--out="):]
    if ext:
        print("전 조합 재측정 시작, 증강 모드(바이낸스 과거 종가 포함, 가격 전용)...")
    else:
        print("전 코인 × 전 시간봉 × 전 신호 재측정 시작 (약 20분, 파시피카만)...")
    if und:
        print("  + 원본시장 증강: 접합본(ub_)이 있는 주식·RWA 토큰만, "
              "해당 행에 src=underlying 태깅")
    if out_path:
        print(f"  + 출력 경로: {out_path} (라이브 매트릭스는 건드리지 않는다)")
    try:
        run_measurement(use_extended=ext, use_underlying=und,
                        out_path=out_path)
    finally:
        # Release even when the measurement raises, otherwise the lock file
        # lingers and blocks re-measurement for the whole stale window.
        _release_lock()
    if out_path:
        # summary() 는 라이브 매트릭스를 읽는다. --out 으로 다른 파일에 받았는데
        # 여기서 요약을 찍으면 방금 잰 것이 아닌 예전 값을 보여주게 된다.
        print(f"결과는 {out_path} 에 있다 (라이브 매트릭스 미변경)")
    else:
        print(summary())


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()


def pooled_shape(sig: str, tf: str):
    """이 신호·시간봉의 '평균이익 / 평균손실' 비율, 전 종목 합산.

    종목 표본이 부족할 때 손절·익절의 '모양'을 물려받기 위한 것이다.
    크기(절대 %)는 종목마다 변동성이 달라 그대로 못 쓴다, BTC의 평균손실
    10.8%를 변동성 큰 알트에 그대로 걸면 엉뚱해진다. 비율만 가져오고
    크기는 호출부에서 그 종목 변동성으로 맞춘다.

    반환: (aw/al 비율, 표본수) 또는 None
    """
    m = load_matrix()
    if not m:
        return None
    tot_aw = tot_al = n_all = 0.0
    for r in m.get("rows", []):
        if r.get("tf") != tf or r.get("sig") != sig:
            continue
        n = r.get("n", 0)
        if n < MIN_N:
            continue
        tot_aw += r.get("aw", 0) * n
        tot_al += r.get("al", 0) * n
        n_all += n
    if n_all <= 0 or tot_al <= 0:
        return None
    return tot_aw / tot_al, int(n_all)
