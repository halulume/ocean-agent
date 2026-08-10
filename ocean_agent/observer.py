"""관측 로거 + 학습, 진입 여부와 무관하게 전 코인 신호를 기록·채점한다.

지금까지는 '진입한 거래'만 채점했다. 관측 로거는 거래량 상위 코인의
'켜진 모든 신호'를 진입 안 했어도 기록하고, 만기가 지나면 실제 결과로
채점한다. 그 결과 신호별 '실전 승률 DB'가 24시간 자동으로 커지고,
셋업 스캐너·자율 개체가 이 DB를 참조해 점점 똑똑해진다.

파일:
- observations.jsonl : 채점 대기 중인 관측 (만기 지나면 채점 후 삭제)
- signal_stats.json  : 신호별 누적 실전 승률 (요약, 영구 축적)

크기: 관측 1건 ~200B. 상위 15코인 × 하루 48사이클 ≈ 하루 최대 130KB,
채점 후 원본 삭제되므로 대기 파일은 항상 작게 유지된다.
"""

import json
import os
import time

from . import data_file
from .api_client import PacificaClient
from .indicators import INTERVAL_MS
from .signal_scanner import FWD, _series, _signals, current_horizons, fetch_closes

_HOME = os.path.expanduser("~")

# 라이브 관측/승률은 네트워크별로 분리 (테스트넷/메인넷)
def _obs_file() -> str:
    return data_file("observations.jsonl")

def _stats_file() -> str:
    return data_file("signal_stats.json")


_STATS_CACHE = {"path": None, "mtime": None, "data": None, "pooled": None}


def _load_stats() -> dict:
    """Cached by (path, mtime), the scanner asks per symbol x timeframe x signal,
    which meant re-parsing the whole stats file thousands of times per cycle.
    Keyed on mtime so a fresh grading pass is still picked up without a restart."""
    path = _stats_file()
    if not os.path.exists(path):
        return {}
    mt = os.path.getmtime(path)
    c = _STATS_CACHE
    if c["path"] == path and c["mtime"] == mt and c["data"] is not None:
        return c["data"]
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    c.update(path=path, mtime=mt, data=data, pooled=None)
    return data


def _save_stats(stats: dict) -> None:
    path = _stats_file()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _regime_tag(client: PacificaClient) -> str:
    """현재 국면을 짧은 태그로 (공포/중립/탐욕). 조합 학습의 맥락 축이 된다."""
    try:
        from .market_context import fear_greed
        fg = fear_greed()
        if not fg:
            return "?"
        v = fg[0]
        return "fear" if v <= 25 else ("greed" if v >= 75 else "neutral")
    except Exception:
        return "?"


def _rot_file() -> str:  # 네트워크별 분리
    return data_file("observe_rotation.txt")

# 빠른 국면 감지용 시간봉. 매매하지 않는 1h도 관측만 한다,
# 채점까지 24시간이면 끝나므로, 며칠~2주 걸리는 매매 시간봉보다 훨씬 빨리
# "백테스트가 틀리기 시작했는지"를 알려주는 조기 경보가 된다.
FAST_TF = "1h"

# 관측 대상 최소 거래대금. 거래가 없는 마켓의 신호는 채점해도 잡음일 뿐이다.
MIN_OBSERVE_VOLUME = 50_000


def _observe_tfs() -> list[str]:
    tfs = list(dict.fromkeys(list(current_horizons().values()) + [FAST_TF]))
    return tfs


def _rotation_offset(total: int, batch: int) -> int:
    """사이클마다 다른 묶음을 보도록 오프셋을 순회시킨다 (전체 커버 + 부담 분산)."""
    rot = _rot_file()
    try:
        off = int(open(rot).read().strip())
    except (OSError, ValueError):
        off = 0
    nxt = (off + batch) % max(total, 1)
    try:
        open(rot, "w").write(str(nxt))
    except OSError:
        pass
    return off


def observe(client: PacificaClient, universe: int = 15,
            batch: int = 20) -> int:
    """지금 켜진 신호를 전부 관측 파일에 기록.
    단일 신호뿐 아니라 '그 순간 동시에 켜진 신호 집합 + 국면'을 함께 저장해,
    나중에 조합(예: RSI과열+볼린저상단+공포장)별 승률까지 학습한다.

    universe=0 이면 파시피카 '전체 perp'를 대상으로 하되, 매 사이클 batch개씩
    순회(rotation)하며 관측, 전체를 커버하면서도 사이클당 부담을 낮춘다.
    universe>0 이면 거래량 상위 그만큼만 (기존 방식).
    """
    prices = client.get_prices()
    # 거래가 사실상 없는 마켓은 관측해도 배울 게 없다. 가격이 안 움직이거나
    # 무작위로 튀어서 신호 채점이 순수 잡음이 된다 (테스트넷에는 거래대금 $0인
    # 마켓이 다수 존재했고, 그 잡음이 승률 DB를 오염시켰다).
    ranked = sorted([p for p in prices
                     if p.get("mid") and float(p.get("volume_24h") or 0)
                     >= MIN_OBSERVE_VOLUME],
                    key=lambda p: -float(p.get("volume_24h") or 0))
    if universe and universe > 0:
        perps = ranked[:universe]
    else:
        # 전체 순회: 이번 사이클은 batch개만, 다음 사이클엔 다음 묶음
        off = _rotation_offset(len(ranked), batch)
        perps = (ranked + ranked)[off:off + batch]   # wrap-around
    now_ms = int(time.time() * 1000)
    regime = _regime_tag(client)
    recs = []
    for p in perps:
        sym = p["symbol"]
        for tf in _observe_tfs():
            try:
                closes = fetch_closes(client, sym, tf)
            except Exception:
                continue
            if len(closes) < 60:
                continue
            s = _series(closes)
            n = len(closes)
            # 이 심볼·시간봉에서 지금 동시에 켜진 신호들 수집
            active = []   # (name, side)
            for name, (side, cond) in _signals(s).items():
                try:
                    if cond(n - 1):
                        active.append((name, side))
                except (TypeError, IndexError):
                    continue
            if not active:
                continue
            # 그 조합의 지배 방향 (다수결; 동수면 기록 안 함)
            longs = sum(1 for _, sd in active if sd == "long")
            shorts = len(active) - longs
            combo_side = "long" if longs > shorts else ("short" if shorts > longs else "")
            sig_names = sorted(nm for nm, _ in active)
            base = {"sym": sym, "tf": tf, "entry": closes[-1], "ts": now_ms,
                    "due": now_ms + INTERVAL_MS[tf] * FWD, "regime": regime}
            # 개별 신호 기록 (기존 학습 유지)
            for name, side in active:
                recs.append({**base, "sig": name, "side": side})
            # 조합 기록 (2개 이상 동시 + 방향 일치일 때만)
            if len(active) >= 2 and combo_side:
                recs.append({**base, "kind": "combo",
                             "combo": sig_names, "side": combo_side})
    if recs:
        with open(_obs_file(), "a", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(recs)


def grade(client: PacificaClient) -> dict:
    """만기 지난 관측을 실제 결과로 채점 → signal_stats.json 갱신.
    채점한 관측은 파일에서 제거. 갱신된 통계 요약 반환."""
    obs_path = _obs_file()
    if not os.path.exists(obs_path):
        return {}
    with open(obs_path, encoding="utf-8") as f:
        obs = [json.loads(line) for line in f if line.strip()]
    now_ms = int(time.time() * 1000)
    due = [o for o in obs if o["due"] <= now_ms]
    pending = [o for o in obs if o["due"] > now_ms]
    if not due:
        return {}

    prices = {p["symbol"]: p for p in client.get_prices()}
    stats = _load_stats()
    graded_now = 0
    for o in due:
        pr = prices.get(o["sym"])
        if not pr:
            continue
        cur = float(pr.get("mark") or pr.get("mid") or 0)
        if cur <= 0 or o["entry"] <= 0:
            continue
        move = cur / o["entry"] - 1
        won = (move > 0) if o["side"] == "long" else (move < 0)
        reg = o.get("regime", "?")
        if o.get("kind") == "combo":
            sig_label = "combo:" + "+".join(o["combo"])
            # 조합은 국면축까지 함께 학습 (심볼 무관, 조합의 일반적 엣지)
            key = f"*|{o['tf']}|{sig_label}@{reg}"
        else:
            sig_label = o["sig"]
            key = f"{o['sym']}|{o['tf']}|{sig_label}"
        e = stats.setdefault(key, {"win": 0, "total": 0,
                                   "sig": sig_label, "tf": o["tf"],
                                   "side": o["side"], "regime": reg})
        e["win"] += int(won)
        e["total"] += 1
        # 관측이 언제부터 언제까지 걸쳐 모였는지, 표본 독립성 판정용.
        # (하루에 몰린 20건은 사실상 1건이므로 signal_winrate가 이를 거른다)
        obs_ms = float(o.get("ts") or now_ms)
        e["first_ms"] = min(float(e.get("first_ms") or obs_ms), obs_ms)
        e["last_ms"] = max(float(e.get("last_ms") or obs_ms), obs_ms)
        graded_now += 1
    _save_stats(stats)

    # 남은(미만기) 관측만 다시 기록
    tmp = obs_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for o in pending:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    os.replace(tmp, obs_path)
    return {"graded": graded_now, "pending": len(pending),
            "signals_tracked": len(stats)}


MIN_SPAN_DAYS = 5      # 관측이 이 일수 이상에 걸쳐야 신뢰 (독립성 확보)


def signal_winrate(symbol: str, interval: str, signal: str):
    """축적된 실전 승률 조회 (표본이 부족하거나 시간적으로 뭉쳐 있으면 None).

    ⚠️ 독립성 관문이 핵심이다. 하루 사이에 기록된 관측 20건은 '20개의 표본'이
    아니라 '같은 시장 국면 1건을 20번 센 것'에 가깝다. 실제로 하루치 관측만으로
    볼린저 상단이탈이 23%로 나왔는데, 수천 건 백테스트에서는 +3.3% EV로
    우량 신호였다. 뭉친 표본을 믿으면 좋은 신호를 죽인다.
    """
    stats = _load_stats()
    e = stats.get(f"{symbol}|{interval}|{signal}")
    if not e or e["total"] < 10:
        return None
    if _span_days(e) < MIN_SPAN_DAYS:
        return None
    return e["win"] / e["total"], e["total"]


def pooled_winrate(interval: str, signal: str):
    """Measured win rate for one signal POOLED across every symbol.

    Why pooling is needed: per-symbol stats slice 3.6k observations into ~660
    buckets (~5 each), so `signal_winrate` can never clear its 10-sample gate and
    the bot ends up judging on backtest alone. Pooled by (timeframe, signal) the
    same data gives hundreds of samples per signal, enough to say "this signal
    actually performs like X in live trading", which is the only measured opinion
    available early on.

    Returns (win_rate, n, span_days) or None. Callers must treat this as a soft
    prior, never a hard gate: pooling adds independence across assets (gold,
    stocks and FX do not move with the coins) but crypto majors still move
    together, so a short collection window is still partly one event. Weight it
    by both n and span_days, see EVIDENCE notes in signal_scanner."""
    stats = _load_stats()
    c = _STATS_CACHE
    pooled = c.get("pooled")
    if pooled is None or c.get("data") is not stats:
        pooled = {}
        for k, v in stats.items():
            if k.startswith("*|"):
                continue                      # combos are learned separately
            try:
                _, tf, sig = k.split("|", 2)
            except ValueError:
                continue
            a = pooled.setdefault((tf, sig), [0, 0, None, None])
            a[0] += v.get("win", 0)
            a[1] += v.get("total", 0)
            lo, hi = v.get("first_ms"), v.get("last_ms")
            if lo:
                a[2] = lo if a[2] is None else min(a[2], float(lo))
            if hi:
                a[3] = hi if a[3] is None else max(a[3], float(hi))
        c["pooled"] = pooled
    a = pooled.get((interval, signal))
    if not a or a[1] < POOLED_MIN_N:
        return None
    span = ((a[3] - a[2]) / 86_400_000) if (a[2] and a[3]) else 0.0
    return a[0] / a[1], a[1], span


POOLED_MIN_N = 40        # 합산 표본이 이 미만이면 의견 없음 (심볼 몇 개짜리 우연 방지)


def _span_days(entry: dict) -> float:
    """이 통계가 며칠에 걸쳐 수집됐는가 (첫/마지막 채점 시각 기준)."""
    lo, hi = entry.get("first_ms"), entry.get("last_ms")
    if not lo or not hi:
        return 0.0
    return (float(hi) - float(lo)) / 86_400_000


def top_learned(min_total: int = 15, top: int = 15) -> list:
    """축적 데이터에서 실전 승률 높은 신호 랭킹 (학습 결과).

    반환: [(키, 승률, 표본, 수집일수), ...], 수집일수까지 돌려주는 이유는,
    이 목록이 사람이 보고 판단하는 화면이기 때문이다. 기간 관문을 통과 못 한
    항목은 100%/0% 같은 극단값으로 줄세우기 상단을 차지한다(2026-08-04:
    표본 8건 이상 조합의 80%가 전승 아니면 전패였고, 전부 65시간 한 구간에서
    나왔다). 판정에 쓰는 signal_winrate는 이미 이 관문을 걸고 있는데 화면만
    안 걸려 있으면, 봇은 안 믿는 숫자를 사람은 믿게 된다."""
    stats = _load_stats()
    rows = [(k, v["win"] / v["total"], v["total"], _span_days(v))
            for k, v in stats.items()
            if v["total"] >= min_total and _span_days(v) >= MIN_SPAN_DAYS]
    rows.sort(key=lambda x: -x[1])
    return rows[:top]


def combo_winrate(signals: list, interval: str, regime: str):
    """조합 신호의 학습된 승률 조회. signals=동시 점등된 신호 이름 리스트.
    표본 10건 이상 + 5일 이상에 걸쳐 수집됐을 때만 (승률, 표본, side) 반환.

    ⚠️ 기간 관문은 signal_winrate와 동일한 이유로 필수다. 조합은 신호 여러 개가
    동시에 켜져야 해서 특정 시장 국면에 더 심하게 뭉친다. 실제로 2시간(0.1일)
    사이 16건이 전승 100%로 기록된 조합이 있었고, 관문이 없던 시절 이 값이
    확신도를 최대 50%까지 밀어올렸다, 단일 신호는 막고 조합만 뚫린 구멍이었다.
    """
    stats = _load_stats()
    label = "combo:" + "+".join(sorted(signals))
    e = stats.get(f"*|{interval}|{label}@{regime}")
    if not e or e["total"] < 10:
        return None
    if _span_days(e) < MIN_SPAN_DAYS:
        return None
    return e["win"] / e["total"], e["total"], e.get("side", "")


def top_combos(min_total: int = 12, top: int = 12) -> list:
    """학습된 조합 신호 중 승률 높은 순 (단일 신호 제외, 조합만).

    top_learned와 같은 이유로 기간 관문을 건다, 조합은 신호 여러 개가 동시에
    켜져야 해서 특정 국면에 더 심하게 뭉친다(2시간 사이 16건 전승 사례)."""
    stats = _load_stats()
    rows = [(v["sig"], v.get("regime", "?"), k.split("|")[1],
             v["win"] / v["total"], v["total"], _span_days(v))
            for k, v in stats.items()
            if k.startswith("*|") and v["total"] >= min_total
            and _span_days(v) >= MIN_SPAN_DAYS]
    rows.sort(key=lambda x: -x[3])
    return rows[:top]


def main():
    import sys
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="https://api.pacifica.fi")
    p.add_argument("--learned", action="store_true", help="학습된 승률 랭킹 출력")
    p.add_argument("--combos", action="store_true", help="학습된 조합 신호 랭킹")
    args = p.parse_args()
    if args.combos:
        rows = top_combos()
        if not rows:
            print("아직 채점된 조합 관측이 부족합니다 (동시 점등 조합이 쌓이는 중).")
            return
        print("학습된 조합 신호 승률 랭킹 (동시 점등 + 국면):\n")
        for sig, reg, tf, wr, nn in rows:
            combo = sig.replace("combo:", "").replace("+", " + ")
            print(f"  [{tf}·{reg}] {combo}\n      → {wr:.0%} 승률 (실전 {nn}건)")
        return
    if args.learned:
        rows = top_learned()
        if not rows:
            print("아직 채점된 관측이 부족합니다 (계속 수집 중).")
            return
        print("학습된 실전 승률 랭킹 (진입 무관 전 신호 관측):\n")
        for k, wr, n, sp in rows:
            print(f"  {k:<32} 승률 {wr:.0%} (실전 {n}건, {sp:.0f}일)")
        return
    client = PacificaClient(args.url)
    n = observe(client)
    g = grade(client)
    print(f"관측 {n}건 기록 · {g}")


if __name__ == "__main__":
    main()
