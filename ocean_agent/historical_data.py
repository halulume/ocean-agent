"""과거 데이터 증강 (Historical Data Augmentation), 백테스트 표본 두껍게.

파시피카는 데이터가 ~13개월(2025-06~)뿐이라 한 국면(하락장)만 담긴다.
바이낸스는 2017년부터 있어 상승/하락/횡보 여러 국면을 담는다. 이 모듈은
'가격 기반 신호'의 백테스트 사전(prior)을 두껍게 하려고, 파시피카 데이터가
시작되기 전 구간을 바이낸스 종가로 채운다.

━━━ 엄격한 경계 (반드시 지킴) ━━━
- 가격(OHLCV의 종가)만 가져온다. 바이낸스의 펀딩·호가·OI·유동성은 절대 안 씀.
  그것들은 파시피카 고유라 이식되지 않는다. 펀딩/캐리/OI 로직은 파시피카 전용.
- 기존 지표(indicators.py, _series)를 그대로 쓴다. 재작성하지 않는다.
- 모든 캔들에 source 태그(binance/pacifica). 라이브 판단은 파시피카만.
- 백테스트 '사전 강화'일 뿐, 자기학습이 아니다. 진짜 자기개선은 라이브
  파시피카 데이터에서 온다(나중에).
- 겹치는 구간은 파시피카가 authoritative, 바이낸스로 덮어쓰지 않는다.
"""

import gzip
import json
import os
import time
import urllib.request

# 파시피카 심볼 → 바이낸스 심볼. 양쪽에 다 있는 것만. RWA/파시피카전용 제외.
# 무기한 선물에서만 이름이 다른 것들. 파시피카가 1,000개 묶음으로 부르는
# 종목을 선물도 같은 묶음으로 부르므로, 현물에서 배율 1,000으로 거부되던
# kPEPE·kBONK·kSHIB 가 선물에서는 그냥 붙는다.
BINANCE_FUT_OVERRIDE = {
    "kBONK": "1000BONKUSDT",
    "kPEPE": "1000PEPEUSDT",
    "kSHIB": "1000SHIBUSDT",
}


def binance_symbol(sym: str) -> str | None:
    """이 종목의 바이낸스 심볼. 선물 모드면 선물 이름을 준다."""
    if os.environ.get("BINANCE_FUTURES") == "1" and sym in BINANCE_FUT_OVERRIDE:
        return BINANCE_FUT_OVERRIDE[sym]
    return BINANCE_SYMBOL.get(sym)


BINANCE_SYMBOL = {
    "BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT",
    "XRP": "XRPUSDT", "DOGE": "DOGEUSDT", "BNB": "BNBUSDT",
    "AVAX": "AVAXUSDT", "LINK": "LINKUSDT", "LTC": "LTCUSDT",
    "ADA": "ADAUSDT", "SUI": "SUIUSDT", "AAVE": "AAVEUSDT",
    # 2026-08-06 추가, 바이낸스 exchangeInfo 전수 대조로 확인했다.
    # PUMP 는 예전 주석에 "매칭 없음"으로 적혀 있었는데 사실이 아니었다.
    # 이 종목들은 파시피카 보관분(약 3,000봉)만으로 판단되고 있었고, 그래서
    # 8시간봉·일봉에서 표본이 6~30건에 그쳤다. 2017년부터 이어붙이면 수백 건이
    # 되어 '표본 부족'이라는 문제 자체가 사라진다.
    "PAXG": "PAXGUSDT", "PUMP": "PUMPUSDT", "ZEC": "ZECUSDT",
    "UNI": "UNIUSDT", "JUP": "JUPUSDT", "ENA": "ENAUSDT",
    # 2026-08-12 추가 25종. 표에 18종뿐이라 매트릭스가 19종목으로 돌고 있었고,
    # 봇이 실제로 뽑는 종목(KAITO·LDO·kBONK·WLFI 등)이 거의 다 빠져 있었다.
    # 그날 exchangeInfo(TRADING) 전수 대조로 확인했다, 파시피카 75종 중 43종이
    # 바이낸스 현물에 있다. 후보는 SYM·기초자산·1000기초자산 + USDT 순으로 보고
    # 처음 맞는 것을 쓴다(파시피카의 앞 소문자 k = 1000단위, 기초자산은 나머지).
    "2Z": "2ZUSDT", "ARB": "ARBUSDT", "ASTER": "ASTERUSDT",
    "BCH": "BCHUSDT", "CHIP": "CHIPUSDT", "CRV": "CRVUSDT",
    "ICP": "ICPUSDT", "KAITO": "KAITOUSDT", "LDO": "LDOUSDT",
    "MEGA": "MEGAUSDT", "NEAR": "NEARUSDT", "PENGU": "PENGUUSDT",
    "STRK": "STRKUSDT", "TAO": "TAOUSDT", "TRUMP": "TRUMPUSDT",
    "VIRTUAL": "VIRTUALUSDT", "WIF": "WIFUSDT", "WLD": "WLDUSDT",
    "WLFI": "WLFIUSDT", "XPL": "XPLUSDT", "ZK": "ZKUSDT",
    "ZRO": "ZROUSDT",
    # ⚠️ k 종목은 호가 단위가 1000배다(kBONK 0.002282 vs BONKUSDT 0.00000228).
    # 그래서 겹침 검증이 반드시 실패하고, 이어붙이기는 자동으로 취소된다
    # (파시피카 보관분만 쓴다). 스케일을 맞춰 붙이는 건 별도 작업이다,
    # 지금 억지로 붙이면 이음매에 -99.9% 짜리 가짜 봉이 생긴다.
    "kBONK": "BONKUSDT", "kPEPE": "PEPEUSDT", "kSHIB": "SHIBUSDT",
    # 아직 없는 것: HYPE(바이낸스 현물 없음) · 주식(SKHYNIX·NVDA·SAMSUNG 등)
    # · 원자재(XAU·XAG·CL) · SP500. 억지 매핑은 오염이므로 넣지 않는다.
}

# 바이낸스 kline interval 매핑 (파시피카와 표기 다름)
BINANCE_INTERVAL = {
    "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "2h": "2h", "4h": "4h", "8h": "8h", "12h": "12h",
    "1d": "1d", "1w": "1w",
}

EARLIEST_MS = 1_483_228_800_000   # 2017-01-01. 이전은 얇고 구조가 달라 잡음.

# 받아온 [2017 ~ 파시피카 시작] 구간을 디스크에 캐시한다.
# 이 구간은 이미 지나간 과거라 값이 절대 안 바뀐다, 7일마다 재측정할 때마다
# 수천 요청을 다시 보낼 이유가 없다. 최초 1회만 받고(5분봉 포함 전 구간),
# 이후 재측정은 캐시에서 즉시 읽는다. 봉 수를 잘라 표본을 줄이는 것보다
# 이 방식이 낫다, 승률·EV의 근거가 되는 구간을 온전히 유지하면서 비용만 없앤다.
CACHE_DIR = os.path.join(os.path.expanduser("~"), ".ocean_agent_bincache")
OVERLAP_TOLERANCE = 0.02          # 겹침 구간 종가 괴리 허용치(2%). 넘으면 경고.


def _cache_path(bsym: str, binterval: str) -> str:
    """캐시 파일 경로, (심볼, 시간봉)만으로 정한다.

    예전엔 파일명에 파시피카 상장 시각(end_ms)을 넣었는데, 저시간봉은 파시피카가
    짧게만 보관해서(5분봉 ≈ 10일) 그 값이 실행할 때마다 밀린다. 결과적으로 매번
    캐시가 빗나가 같은 2017~현재 구간을 통째로 다시 받고, 파일만 쌓였다
    (BTC 5분봉 3MB짜리가 실행 횟수만큼). 캐시의 존재 이유가 사라진 셈이다.
    이제 구간 끝은 파일 안에 기록하고, 모자란 부분만 이어받는다.

    현물과 선물은 자리를 나눈다. 이 경로에는 접미사가 없어서, 선물 모드로 받은
    종가가 현물 이름 파일에 들어갈 수 있었다 — 43종목 중 40개가 심볼까지 같아서
    (BTCUSDT) 어느 쪽인지 구분이 안 된다. 08-13 점검에서 실제 오염은 없었지만
    (현물 파일 140개 전부 전환 전 기록), 구멍은 열려 있었다.
    현물은 `spot/` 아래로, 선물은 `_fut` 접미사로 간다."""
    if os.environ.get("BINANCE_FUTURES") == "1":
        return os.path.join(CACHE_DIR, f"{bsym}_{binterval}_fut.json.gz")
    return os.path.join(CACHE_DIR, "spot", f"{bsym}_{binterval}.json.gz")


def _cached_closes(bsym: str, binterval: str, end_ms: int):
    """캐시에서 [시작 ~ end_ms) 구간 종가. 부족하면 (부분데이터, 이어받을시작).

    반환: (closes, resume_from)
      · resume_from is None  → 캐시만으로 충분
      · resume_from is int   → 그 시각부터 더 받아 뒤에 붙이면 됨
      · closes is None       → 캐시 없음(전부 받아야 함)"""
    try:
        path = _cache_path(bsym, binterval)
        if not os.path.exists(path):
            return None, None
        with gzip.open(path, "rt", encoding="utf-8") as f:
            d = json.load(f)
        closes, covered = d.get("closes"), float(d.get("end_ms") or 0)
        if not isinstance(closes, list) or not closes:
            return None, None
        if covered >= end_ms:
            return closes, None          # 이미 충분히 덮는다
        return closes, int(covered)      # 뒤에 이어받기
    except Exception:
        return None, None                # 깨진 캐시는 그냥 다시 받는다


def _store_closes(bsym: str, binterval: str, end_ms: int,
                  closes: list) -> None:
    """원자적으로 캐시 저장(임시파일 → 교체). 실패해도 측정은 계속된다."""
    if not closes:
        return
    try:
        path = _cache_path(bsym, binterval)
        # 현물은 spot/ 아래라 CACHE_DIR 만 만들면 안 된다.
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump({"end_ms": int(end_ms), "closes": closes}, f)
        os.replace(tmp, path)
    except Exception:
        pass


def _binance_klines(bsym: str, binterval: str, start_ms: int,
                    end_ms: int, log=print) -> list[dict]:
    """바이낸스 공개 klines(키 불필요)을 1000개 한도로 페이징. 종가만 태깅해 반환.
    반환: [{"t": openTime, "c": close, "source": "binance"}, ...] 과거→현재."""
    out = []
    cur = start_ms
    guard = 0
    fails = 0
    base = ("https://fapi.binance.com/fapi/v1/klines"
            if os.environ.get("BINANCE_FUTURES") == "1"
            else "https://api.binance.com/api/v3/klines")
    # 5분봉 2017~현재는 약 1,000페이지다. 500에서 끊으면 앞부분만 받고 최근이
    # 비어, 파시피카 종가와 이어붙일 때 가격이 순간이동하는 시계열이 된다
    # (가짜 신호의 원인). 구간을 온전히 받도록 상한을 넉넉히 둔다, 어차피
    # 결과는 캐시되므로 이 비용은 심볼당 1회뿐이다.
    while cur < end_ms and guard < 3000:
        guard += 1
        url = (f"{base}?symbol={bsym}&interval={binterval}"
               f"&startTime={cur}&endTime={end_ms}&limit=1000")
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                d = json.loads(r.read())
        except Exception as e:
            # 실패해도 cur가 안 움직이므로 같은 페이지를 계속 다시 친다. 예전엔
            # 이 재시도가 guard(페이지 예산)를 소진해 구간이 잘린 채 끝났다,
            # 조용히 반쪽짜리 시계열이 나오는 경로다. 연속 실패는 끊는다.
            fails += 1
            if fails >= 5:
                log(f"  바이낸스 중단({bsym} {binterval}): {e}, 연속 5회 실패")
                return []
            time.sleep(3 * fails)
            continue
        fails = 0
        if not d:
            break
        for k in d:
            # 바이낸스 형식: [openTime, O, H, L, C, V, closeTime, ...]
            out.append({"t": int(k[0]), "c": float(k[4]), "source": "binance"})
        last_open = int(d[-1][0])
        if last_open <= cur:
            break
        # 다음 페이지: 마지막 캔들 다음부터
        step = int(d[-1][6]) - int(d[-1][0]) + 1 if len(d[-1]) > 6 else 1
        cur = last_open + step
        time.sleep(0.2)   # 바이낸스 레이트리밋 예의
    return out


def _pac_kline(client, symbol: str, interval: str, start_ms: int,
               end_ms: int, tries: int = 5, log=print):
    """파시피카 캔들 조회, 일시적 실패는 재시도, 진짜 '없음'만 []로 돌려준다.

    재측정은 봇이 거래하는 동안 돌기 때문에 같은 API를 두 프로세스가 두드린다.
    그때 돌아오는 429/HTML 응답을 JSON으로 파싱하면 예외가 나는데, 예전 코드는
    그걸 그대로 '데이터 없음'으로 삼켰다. 결과가 조용해서 더 나쁘다, 겹침 검증이
    False가 되고, 바이낸스 증강이 통째로 건너뛰어져, 2017년 데이터를 붙이려고
    돌린 측정이 파시피카 구간만 재고 끝난다(실패 로그도 안 남는다).
    None(포기)과 []( 진짜 빈 구간)을 구분해서 돌려준다."""
    for i in range(tries):
        try:
            resp = client.session.get(f"{client.base}/kline", params={
                "symbol": symbol, "interval": interval,
                "start_time": start_ms, "end_time": end_ms}, timeout=25)
            if resp.status_code == 429 or not resp.headers.get(
                    "content-type", "").startswith("application/json"):
                time.sleep(5 * (i + 1))
                continue
            return resp.json().get("data") or []
        except Exception as e:
            if i == tries - 1:
                log(f"  파시피카 조회 포기({symbol} {interval}): {e}")
                return None
            time.sleep(5 * (i + 1))
    log(f"  파시피카 조회 포기({symbol} {interval}): 재시도 {tries}회 초과")
    return None


def _pacifica_start_ms(client, symbol: str, interval: str) -> int | None:
    """이 심볼·시간봉에서 파시피카 데이터가 시작되는 시각(가장 오래된 캔들)."""
    from .indicators import INTERVAL_MS
    step = INTERVAL_MS[interval]
    end = int(time.time() * 1000)
    start = end - step * 3000
    d = _pac_kline(client, symbol, interval, start, end)
    return int(d[0]["t"]) if d else None


def validate_overlap(client, symbol: str, interval: str,
                     pac_start_ms: int, log=print) -> bool:
    """겹침 검증, 파시피카 시작 직후 ~1개월 구간에서 바이낸스와 종가를 대조.
    괴리가 허용치를 넘으면 경고하고 False(이어붙이지 말 것). 같으면 True.

    같은 자산이라도 거래소가 다르면 미세하게 다를 수 있다. 크게 벌어지면
    두 시계열을 잇는 것 자체가 오염이므로, blind stitch 대신 보고한다.
    """
    # binance_symbol() (not the raw table) so futures mode picks up the
    # 1000-bundle overrides (kBONK -> 1000BONKUSDT); the raw spot name is
    # scale-mismatched 1000x for the k symbols and always fails this check.
    bsym = binance_symbol(symbol)
    binterval = BINANCE_INTERVAL.get(interval)
    if not bsym or not binterval:
        return False
    # 시작 +1개월. 단 '지금'을 넘지 않게 자른다, 파시피카는 저시간봉을 짧게만
    # 보관해서(5분봉 ≈ 10일) 시작+30일이 미래가 되고, 미래 구간을 조회하면 빈
    # 응답이 온다. 그러면 겹침 검증이 실패로 떨어져 5분봉만 증강이 통째로
    # 건너뛰어졌다, 실패 로그도 없이. 15분봉 이상은 31일치가 있어 우연히 통과했다.
    ov_end = min(pac_start_ms + 30 * 86_400_000, int(time.time() * 1000))
    # 파시피카 겹침 구간
    d = _pac_kline(client, symbol, interval, pac_start_ms, ov_end, log=log)
    if d is None:
        log(f"  겹침 검증 보류({symbol} {interval}), 조회 실패, 증강 건너뜀")
        return False
    pac = {int(c["t"]): float(c["c"]) for c in d}
    bin_ov = _binance_klines(bsym, binterval, pac_start_ms, ov_end, log=log)
    # 조용한 False가 이 모듈의 사고 원인이었다, 증강이 통째로 빠져도 로그가
    # 없으니, 측정이 '성공'한 것처럼 끝나고 매트릭스만 얕아진다. 전부 말하게 한다.
    if not pac or not bin_ov:
        log(f"  겹침 검증 불가({symbol} {interval}), "
            f"파시피카 {len(pac)}봉 / 바이낸스 {len(bin_ov)}봉, 증강 건너뜀")
        return False
    # 같은 openTime 캔들끼리 종가 비교
    diffs = []
    for b in bin_ov:
        if b["t"] in pac and pac[b["t"]] > 0:
            diffs.append(abs(b["c"] / pac[b["t"]] - 1))
    if not diffs:
        log(f"  겹침 검증 불가({symbol} {interval}), 같은 시각 캔들이 없음"
            f"(파시피카 {len(pac)}봉 / 바이낸스 {len(bin_ov)}봉), 증강 건너뜀")
        return False
    avg_diff = sum(diffs) / len(diffs)
    ok = avg_diff <= OVERLAP_TOLERANCE
    if not ok:
        log(f"  ⚠️ {symbol} {interval}: 바이낸스-파시피카 종가 괴리 "
            f"{avg_diff:.1%} > 허용 {OVERLAP_TOLERANCE:.0%}, 증강 스킵(이어붙이지 않음)")
    else:
        log(f"  ✓ {symbol} {interval}: 겹침 괴리 {avg_diff:.2%}, 증강 가능")
    return ok


def extended_closes(client, symbol: str, interval: str,
                    pacifica_closes: list[float], log=print) -> list[float]:
    """[바이낸스 과거] + [파시피카 최근] 종가를 이어붙여 반환.

    - 파시피카 시작 이전 구간만 바이낸스로 채운다(겹침은 파시피카 우선).
    - 겹침 검증 실패 시 파시피카 종가를 그대로 반환(증강 안 함, 안전).
    - 바이낸스에 없는 심볼(HYPE 등)도 그대로 파시피카만 반환.
    """
    bsym = binance_symbol(symbol)    # futures-aware mapping (same as overlap)
    binterval = BINANCE_INTERVAL.get(interval)
    if not bsym or not binterval:
        return pacifica_closes   # 매칭 없음 → 파시피카만
    pac_start = _pacifica_start_ms(client, symbol, interval)
    if not pac_start:
        log(f"  {symbol} {interval}: 파시피카 시작 시각 불명, 증강 건너뜀")
        return pacifica_closes
    if not validate_overlap(client, symbol, interval, pac_start, log=log):
        return pacifica_closes   # 괴리 크면 증강 안 함
    # 2017 ~ 파시피카시작 직전 구간. 캐시에 있는 만큼 쓰고 모자란 뒤쪽만 이어받는다.
    # A fetch that stops short of pac_start must NOT be stitched to the
    # Pacifica segment: that splices two series across a silent hole, and
    # stamping end_ms=pac_start would freeze the hole into the cache as
    # "complete" forever. Instead the real coverage is stored (so the next run
    # resumes where this one stopped) and the caller gets Pacifica-only data,
    # the module's normal safe fallback.
    from .indicators import INTERVAL_MS
    step = INTERVAL_MS[interval]
    binance_closes, resume = _cached_closes(bsym, binterval, pac_start)
    if binance_closes is None:
        hist = _binance_klines(bsym, binterval, EARLIEST_MS,
                               pac_start - 1, log=log)
        if not hist:
            return pacifica_closes
        binance_closes = [h["c"] for h in hist if h["t"] < pac_start]
        covered = hist[-1]["t"] + step        # end of what was actually fetched
        if covered < pac_start - step:        # more than one bar missing
            _store_closes(bsym, binterval, covered, binance_closes)
            log(f"  {symbol} {interval}: 바이낸스 구간 미완성"
                f"(빈틈 {(pac_start - covered) / 3_600_000:.1f}시간), 증강 건너뜀")
            return pacifica_closes
        _store_closes(bsym, binterval, pac_start, binance_closes)
    elif resume is not None:
        add = _binance_klines(bsym, binterval, resume, pac_start - 1, log=log)
        # The cached closes carry no timestamps, so overlap cannot be deduped
        # after the fact: keep only bars at/after the resume point, which is
        # exactly where the cached coverage ends. Anything earlier would be
        # appended a second time.
        binance_closes = binance_closes + [h["c"] for h in add
                                           if resume <= h["t"] < pac_start]
        covered = (add[-1]["t"] + step) if add else resume
        if covered < pac_start - step:        # top-up failed or stopped short
            if add:
                _store_closes(bsym, binterval, covered, binance_closes)
            log(f"  {symbol} {interval}: 바이낸스 이어받기 미완성"
                f"(빈틈 {(pac_start - covered) / 3_600_000:.1f}시간), 증강 건너뜀")
            return pacifica_closes
        _store_closes(bsym, binterval, pac_start, binance_closes)
    log(f"  {symbol} {interval}: 바이낸스 {len(binance_closes)}봉 + "
        f"파시피카 {len(pacifica_closes)}봉 = {len(binance_closes)+len(pacifica_closes)}봉")
    return binance_closes + pacifica_closes


def main():
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    from .api_client import PacificaClient
    c = PacificaClient("https://api.pacifica.fi")
    sym = sys.argv[1] if len(sys.argv) > 1 else "BTC"
    tf = sys.argv[2] if len(sys.argv) > 2 else "1d"
    from .rematrix import _fetch
    pac = _fetch(c, sym, tf)
    print(f"파시피카만: {len(pac)}봉")
    ext = extended_closes(c, sym, tf, pac)
    print(f"증강 후: {len(ext)}봉 (추가 {len(ext)-len(pac)}봉)")


if __name__ == "__main__":
    main()


# ─────────────────────────────────────────────────────────────────────────
# OHLC (고가/저가 포함), 백테스트 하네스 전용. 위쪽 종가 전용 경로는
# 그대로 두고 여기에 새로 붙인다. 종가만으로는 "한 봉 안에서 손절가에
# 닿았는가"를 알 수 없어 TP/SL·트레일링 재생이 불가능하기 때문이다.
# ─────────────────────────────────────────────────────────────────────────

def _ohlc_cache_path(bsym: str, binterval: str) -> str:
    """OHLC 캐시는 종가 캐시와 파일을 분리한다, 같은 이름을 쓰면 서로
    덮어써서 한쪽이 상대 형식을 읽고 조용히 깨진다.

    선물도 같은 이유로 파일을 나눈다. 43종목 중 40개는 현물과 선물의 심볼이
    글자까지 같아서(BTCUSDT), 접미사가 없으면 선물 데이터가 현물 캐시를
    덮어쓰고 어느 쪽인지 알 수 없게 된다. 가격은 비슷해도 거래량과 델타는
    다른 시장이라 섞이면 조용히 틀린 값이 된다.
    """
    if os.environ.get("BINANCE_FUTURES") == "1":
        return os.path.join(CACHE_DIR, f"{bsym}_{binterval}_fut_ohlc.json.gz")
    return os.path.join(CACHE_DIR, "spot", f"{bsym}_{binterval}_ohlc.json.gz")


def _cached_ohlc(bsym: str, binterval: str, end_ms: int,
                 start_ms: int = EARLIEST_MS):
    """(bars, resume_from, covered_start). _cached_closes 와 같은 규약에
    '앞쪽 경계'가 하나 붙었다.

    캐시는 [covered_start, end_ms) 를 덮는다. 예전 파일에는 start_ms 가 없는데,
    그때는 언제나 EARLIEST_MS 부터 받았으므로 그 값으로 본다(기존 캐시 그대로 유효).
    요청한 start_ms 가 캐시 시작보다 앞이면 앞부분이 비어 있다는 뜻이라 캐시를
    쓰지 않는다(조용한 빈틈 금지). 반대로 요청이 캐시 안쪽이면 그대로 쓰고,
    자르는 일은 호출부가 반환 직전에 한다. 여기서 잘라 돌려주면 그 잘린 결과가
    다시 캐시에 저장돼 이미 받아둔 과거가 사라진다."""
    try:
        path = _ohlc_cache_path(bsym, binterval)
        if not os.path.exists(path):
            return None, None, None
        with gzip.open(path, "rt", encoding="utf-8") as f:
            d = json.load(f)
        bars, covered = d.get("bars"), float(d.get("end_ms") or 0)
        cov_start = int(d.get("start_ms") or EARLIEST_MS)
        if not isinstance(bars, list) or not bars:
            return None, None, None
        # 2026-08-06에 거래량(v)을 추가했다. 그 이전 캐시는 v가 없어 거래량
        # 분석에 쓸 수 없으므로 통째로 다시 받는다(부분 이어받기로는 앞부분에
        # v 없는 봉이 남아 조용히 반쪽짜리가 된다).
        if "v" not in bars[0]:
            return None, None, None
        if cov_start > start_ms:
            return None, None, None      # 요청 구간의 앞이 캐시에 없다
        if covered >= end_ms:
            return bars, None, cov_start
        return bars, int(covered), cov_start
    except Exception:
        return None, None, None


def _store_ohlc(bsym: str, binterval: str, end_ms: int, bars: list,
                start_ms: int = EARLIEST_MS) -> None:
    if not bars:
        return
    try:
        path = _ohlc_cache_path(bsym, binterval)
        # 현물은 spot/ 아래라 CACHE_DIR 만 만들면 안 된다.
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump({"start_ms": int(start_ms), "end_ms": int(end_ms),
                       "bars": bars}, f)
        os.replace(tmp, path)
    except Exception:
        pass


def _binance_ohlc(bsym: str, binterval: str, start_ms: int,
                  end_ms: int, log=print) -> list[dict]:
    """_binance_klines 와 같은 페이징이되 O/H/L/C 를 모두 태깅한다.
    반환: [{"t","o","h","l","c","source":"binance"}, ...] 과거→현재.

    BINANCE_FUTURES=1 이면 무기한 선물(fapi)에서 받는다. 우리가 파시피카에서
    거래하는 것이 무기한 선물인데 5년치를 현물에서 받아왔던 것을 08-13에
    바로잡았다. 가격은 겹침 검증을 통과할 만큼 비슷하지만 거래량·델타·청산은
    다른 시장이고, 델타를 쓰려면 그 차이가 곧바로 결과에 들어온다.
    """
    out = []
    cur = start_ms
    guard = 0
    fails = 0
    base = ("https://fapi.binance.com/fapi/v1/klines"
            if os.environ.get("BINANCE_FUTURES") == "1"
            else "https://api.binance.com/api/v3/klines")
    while cur < end_ms and guard < 3000:
        guard += 1
        url = (f"{base}?symbol={bsym}&interval={binterval}"
               f"&startTime={cur}&endTime={end_ms}&limit=1000")
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                d = json.loads(r.read())
        except Exception as e:
            fails += 1
            if fails >= 5:
                log(f"  바이낸스 OHLC 중단({bsym} {binterval}): {e}, 연속 5회 실패")
                return []
            time.sleep(3 * fails)
            continue
        fails = 0
        if not d:
            break
        for k in d:
            # [openTime, O, H, L, C, V, closeTime, quoteV, trades,
            #  takerBuyBaseV, takerBuyQuoteV, ignore]
            #
            # tb is the taker-buy volume of the bar. Subtracting it from the
            # total volume gives the taker-sell side; the difference between
            # the two is the volume delta, i.e. which side was pressing.
            # Volume alone only carries size, so the delta is kept as well.
            out.append({"t": int(k[0]), "o": float(k[1]), "h": float(k[2]),
                        "l": float(k[3]), "c": float(k[4]), "v": float(k[5]),
                        "tb": float(k[9]) if len(k) > 9 else None,
                        "n": int(k[8]) if len(k) > 8 else None,
                        "source": "binance"})
        last_open = int(d[-1][0])
        if last_open <= cur:
            break
        step = int(d[-1][6]) - int(d[-1][0]) + 1 if len(d[-1]) > 6 else 1
        cur = last_open + step
        time.sleep(0.2)
    return out


def _pac_cache_path(symbol: str, interval: str) -> str:
    return os.path.join(CACHE_DIR, f"pac_{symbol}_{interval}_ohlc.json.gz")


def _load_pac_cache(symbol: str, interval: str) -> list[dict]:
    try:
        path = _pac_cache_path(symbol, interval)
        if not os.path.exists(path):
            return []
        with gzip.open(path, "rt", encoding="utf-8") as f:
            bars = json.load(f).get("bars") or []
        return bars if isinstance(bars, list) else []
    except Exception:
        return []


def _store_pac_cache(symbol: str, interval: str, bars: list) -> None:
    if not bars:
        return
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        path = _pac_cache_path(symbol, interval)
        tmp = path + ".tmp"
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump({"bars": bars}, f)
        os.replace(tmp, path)
    except Exception:
        pass


def _pacifica_ohlc(client, symbol: str, interval: str, log=print) -> list[dict]:
    """파시피카 캔들을 같은 포맷으로. 보관 한도(약 3000봉)만큼만 나온다.

    닫힌 봉은 두 번 다시 바뀌지 않으므로 디스크에 쌓아두고 새로 생긴 구간만
    이어받는다. signal_scanner.fetch_bars 가 메모리로 하던 것과 같은 규약인데,
    그쪽은 프로세스가 끝나면 사라져서 측정 스크립트처럼 매번 새로 뜨는 쪽은
    혜택을 못 받았다. 실측(2026-08-10): 75종목×4시간봉 수집이 매번 30분 넘게
    걸렸고 그 사이 레이트리밋에 걸려 수집이 여러 번 중단됐다.

    ⚠️ 진행 중인 마지막 봉은 저장하지 않는다. 그 봉의 종가는 닫힐 때까지 계속
    바뀌므로 캐시하면 낡은 값이 굳는다. 매 호출마다 마지막 확정봉 이후 구간은
    반드시 새로 받으므로 최신성은 유지된다.

    주문 가격은 이 경로를 쓰지 않는다(client.get_prices). 여기는 백테스트·
    재측정·예측 계산용이다.
    """
    from .indicators import INTERVAL_MS
    step = INTERVAL_MS[interval]
    now = int(time.time() * 1000)
    end = now
    start = end - step * 3000

    cached = [b for b in _load_pac_cache(symbol, interval) if b.get("t", 0) >= start]
    fetch_from = (cached[-1]["t"] + step) if cached else start

    d = _pac_kline(client, symbol, interval, fetch_from, end, log=log) if fetch_from <= end else []
    fresh = []
    for c in d or []:
        try:
            fresh.append({"t": int(c["t"]), "o": float(c.get("o") or c["c"]),
                          "h": float(c.get("h") or c["c"]),
                          "l": float(c.get("l") or c["c"]),
                          "c": float(c["c"]), "v": float(c.get("v") or 0),
                          "source": "pacifica"})
        except (KeyError, TypeError, ValueError):
            continue

    merged = {b["t"]: b for b in cached}
    merged.update({b["t"]: b for b in fresh})
    out = [merged[k] for k in sorted(merged)]
    if not out:
        return []

    # 최신성 자체 점검. 캐시가 굳어 옛 봉만 돌려주는 사고를 조용히 넘기지 않는다.
    # 마지막 봉이 두 칸 이상 뒤처져 있으면 캐시를 버리고 통째로 다시 받는다.
    if out[-1]["t"] < now - step * 2:
        log(f"  {symbol} {interval}: 캐시가 낡았다(마지막 봉 "
            f"{(now - out[-1]['t']) // 60000}분 전), 통째로 다시 받는다")
        # Fetch first, replace only on success. Deleting the cache before the
        # refetch turned one transient API failure into a permanent loss of
        # everything already collected; now a failed refetch returns nothing
        # for this call but keeps the cache on disk for the next attempt
        # (the successful path overwrites it via _store_pac_cache below).
        d = _pac_kline(client, symbol, interval, start, end, log=log)
        refreshed = []
        for c in d or []:
            try:
                refreshed.append(
                    {"t": int(c["t"]), "o": float(c.get("o") or c["c"]),
                     "h": float(c.get("h") or c["c"]),
                     "l": float(c.get("l") or c["c"]),
                     "c": float(c["c"]), "v": float(c.get("v") or 0),
                     "source": "pacifica"})
            except (KeyError, TypeError, ValueError):
                continue
        refreshed.sort(key=lambda b: b["t"])
        if not refreshed:
            log(f"  {symbol} {interval}: 재수집 실패, 낡은 캐시는 보존하고 "
                f"이번 호출은 빈 결과")
            return []
        out = refreshed

    # 현재 진행 중인 봉은 캐시에서 뺀다. 반환값에는 남겨 둔다 (호출부가
    # 예전과 같은 데이터를 보도록; 미완성 봉 처리는 호출부 규약이다).
    open_start = now - (now % step)
    _store_pac_cache(symbol, interval, [b for b in out if b["t"] < open_start])
    return out


def extended_ohlc(client, symbol: str, interval: str, log=print,
                  start_ms: int | None = None) -> list[dict]:
    """[바이낸스 과거] + [파시피카 최근] OHLC 를 이어붙여 반환.

    extended_closes 와 같은 안전장치를 그대로 따른다:
      · 파시피카 시작 이전 구간만 바이낸스로 채운다(겹침은 파시피카 우선)
      · 겹침 검증(validate_overlap) 실패 → 파시피카 OHLC 만 (증강 안 함)
      · 바이낸스에 매칭 없는 심볼(HYPE 등) → 파시피카 OHLC 만

    start_ms: 과거 구간의 앞 경계. 기본값(None)은 EARLIEST_MS(2017-01-01)이라
        기존 호출부(harness 등)의 동작은 한 바이트도 바뀌지 않는다. 재측정처럼
        '이 봇이 거래하는 국면만' 필요한 쪽이 2021 같은 값을 넘겨 쓴다.
        캐시는 요청 구간을 좁혀도 줄어들지 않는다, 이미 받아둔 과거는 그대로
        두고 반환값만 자른다.

    반환: 과거→현재 정렬된 [{"t","o","h","l","c","source"}, ...]
    """
    start_ms = EARLIEST_MS if start_ms is None else int(start_ms)
    pac = _pacifica_ohlc(client, symbol, interval, log=log)
    bsym = binance_symbol(symbol)    # futures-aware mapping (same as overlap)
    binterval = BINANCE_INTERVAL.get(interval)
    if not bsym or not binterval:
        log(f"  {symbol} {interval}: 바이낸스 매칭 없음, 파시피카 {len(pac)}봉만")
        return pac
    pac_start = pac[0]["t"] if pac else _pacifica_start_ms(client, symbol, interval)
    if not pac_start:
        log(f"  {symbol} {interval}: 파시피카 시작 시각 불명, 증강 건너뜀")
        return pac
    if start_ms >= pac_start:
        # 요청 경계가 파시피카 시작보다 뒤다, 바이낸스로 채울 앞 구간이 없다.
        cut = [b for b in pac if b["t"] >= start_ms]
        log(f"  {symbol} {interval}: 파시피카가 요청 구간을 이미 덮는다, "
            f"{len(cut)}봉")
        return cut if cut else pac
    if not validate_overlap(client, symbol, interval, pac_start, log=log):
        log(f"  {symbol} {interval}: 겹침 검증 실패, 파시피카 {len(pac)}봉만")
        return pac

    # Same no-holes rule as extended_closes: a fetch that stops short of
    # pac_start is stored with its real coverage and NOT stitched, otherwise
    # the cache would be stamped "complete" over a silent gap.
    from .indicators import INTERVAL_MS
    step = INTERVAL_MS[interval]
    bars, resume, cov_start = _cached_ohlc(bsym, binterval, pac_start, start_ms)
    if bars is None:
        hist = _binance_ohlc(bsym, binterval, start_ms, pac_start - 1, log=log)
        if not hist:
            return pac
        bars = [b for b in hist if b["t"] < pac_start]
        cov_start = start_ms
        covered = hist[-1]["t"] + step
        if covered < pac_start - step:        # more than one bar missing
            _store_ohlc(bsym, binterval, covered, bars, cov_start)
            log(f"  {symbol} {interval}: 바이낸스 OHLC 구간 미완성"
                f"(빈틈 {(pac_start - covered) / 3_600_000:.1f}시간), 증강 건너뜀")
            return pac
        _store_ohlc(bsym, binterval, pac_start, bars, cov_start)
    elif resume is not None:
        add = _binance_ohlc(bsym, binterval, resume, pac_start - 1, log=log)
        # Merge by timestamp instead of appending: a bar already in the cache
        # that comes back in the top-up would otherwise be stored twice, and
        # a doubled bar silently skews every indicator computed over it.
        merged = {b["t"]: b for b in bars}
        merged.update({b["t"]: b for b in add if b["t"] < pac_start})
        bars = [merged[k] for k in sorted(merged)]
        covered = (add[-1]["t"] + step) if add else resume
        if covered < pac_start - step:        # top-up failed or stopped short
            if add:
                _store_ohlc(bsym, binterval, covered, bars, cov_start)
            log(f"  {symbol} {interval}: 바이낸스 OHLC 이어받기 미완성"
                f"(빈틈 {(pac_start - covered) / 3_600_000:.1f}시간), 증강 건너뜀")
            return pac
        _store_ohlc(bsym, binterval, pac_start, bars, cov_start)
    # 캐시에는 받아둔 전 구간을 남기고, 반환값만 요청 경계로 자른다.
    bars = [b for b in bars if start_ms <= b["t"] < pac_start]
    log(f"  {symbol} {interval}: 바이낸스 {len(bars)}봉 + 파시피카 {len(pac)}봉 "
        f"= {len(bars) + len(pac)}봉")
    return bars + pac


# ─────────────────────────────────────────────────────────────────────────
# 원본시장 증강 (주식·RWA 토큰), 옵트인 전용. 읽기만 한다.
#
# 무엇인가. 파시피카의 주식·상품 토큰은 보관분이 13~125일뿐이라 상위 시간봉
# 표본이 한 자릿수다. 그 토큰이 물고 있는 원본(야후)의 과거로 앞을 채운 시계열이
# ub_ 캐시이고, 이 함수들이 그것을 읽는다. 만드는 쪽은 research/ 이다
# (underlying_splice.py · kr_splice.py). ocean_agent 는 야후를 부르지 않는다.
#
# Why this holds: these tokens print the underlying's value at the same hour,
# not with a lag; pre-listing underlying history therefore stands in for what
# the token would have printed then.
# 여전히 대역이므로 바이낸스 접합과 같은 겹침 관문을 통과해야 하고(관문은 만드는
# 쪽에서 돈다, validate_overlap 과 같은 허용치), 이 경로로 만든 매트릭스 행은
# 호출부가 src="underlying" 으로 태깅한다.
#
# ⚠️ 파일명 규칙은 ub_{TOKEN}_{TF}_ohlc.json.gz 하나뿐이다. 두 규칙을 동시에
# 지원하지 않는다, 그러면 어느 쪽이 읽히는지가 파일 존재 여부에 달리게 된다.
#
# ⚠️ pac_ 캐시는 절대 건드리지 않는다. 덮어썼다면 원본 유래 봉이 재생과 라이브
# 봉인 경로로 조용히 흘러들어간다. 그쪽은 토큰 데이터만 읽어야 한다.
# ─────────────────────────────────────────────────────────────────────────

def _underlying_cache_path(symbol: str, interval: str) -> str:
    return os.path.join(CACHE_DIR, f"ub_{symbol}_{interval}_ohlc.json.gz")


def has_underlying_cache(symbol: str, interval: str) -> bool:
    """이 종목·시간봉에 접합본이 있는가. 있다고 해서 원본 봉이 실제로 붙는다는
    뜻은 아니다(파시피카가 이미 그 구간을 덮으면 앞에 붙을 것이 없다).
    '실제로 증강됐는가'는 반환된 봉의 source 로 판단할 것."""
    return os.path.exists(_underlying_cache_path(symbol, interval))


def _load_underlying(symbol: str, interval: str) -> dict:
    try:
        path = _underlying_cache_path(symbol, interval)
        if not os.path.exists(path):
            return {}
        with gzip.open(path, "rt", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict) or not isinstance(d.get("bars"), list):
            return {}
        # 미완성으로 표시된 시계열은 측정에 쓰지 않는다. 구멍 난 시계열은
        # 에러 없이 그럴듯한 틀린 값을 낸다, 그게 이 규약의 존재 이유다.
        if d.get("incomplete"):
            return {}
        return d
    except Exception:
        return {}


def underlying_ohlc(client, symbol: str, interval: str, log=print,
                    start_ms: int | None = None) -> list[dict]:
    """[원본시장 과거] + [파시피카 현재] OHLC. 접합본이 없으면 파시피카만.

    순서는 이것 하나뿐이다: 파시피카 첫 봉보다 '엄격히 이전'만 원본에서 가져오고,
    거기서부터는 파시피카다. 겹치면 파시피카가 이긴다, 토큰 가격이 진짜다.

    파시피카 구간은 ub_ 파일이 아니라 _pacifica_ohlc 로 매번 새로 읽는다.
    현재는 토큰의 것이고 최신이어야 하기 때문이다. ub_ 파일 안의 파시피카 봉은
    만들 때의 스냅샷이라 쓰지 않는다.

    원본 구간의 봉 태그는 만든 쪽에 따라 다르다(예: 'underlying', 환율 보정된
    한국 종목은 'yahoo_fx'). 그래서 태그 이름으로 고르지 않고 '파시피카가 아닌
    것'으로 고른다. 새 보정 방식이 생겨도 여기를 고칠 필요가 없다.

    start_ms: 원본 구간의 앞 경계(파시피카 구간은 자르지 않는다, extended_ohlc
        와 같은 규약).
    """
    pac = _pacifica_ohlc(client, symbol, interval, log=log)
    blob = _load_underlying(symbol, interval)
    if not blob:
        return pac
    cut = pac[0]["t"] if pac else int(blob.get("pac_start_ms") or 0)
    lo = EARLIEST_MS if start_ms is None else int(start_ms)
    head = [b for b in blob["bars"]
            if b.get("source") != "pacifica" and lo <= b["t"] < cut]
    if not head:
        log(f"  {symbol} {interval}: 접합본에 파시피카 이전 구간이 없다, "
            f"파시피카 {len(pac)}봉만")
        return pac
    log(f"  {symbol} {interval}: 원본({blob.get('ticker') or blob.get('source')})"
        f" {len(head)}봉 + 파시피카 {len(pac)}봉 = {len(head) + len(pac)}봉")
    return head + pac
