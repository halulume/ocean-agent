"""Walk-forward validation, measure how well the bot's predicted win rate
actually matches reality, without waiting for live trades to mature.

The problem this solves: a live observation on a 1h signal takes 24h to grade,
a 1d signal takes 24 days. Judging "does this bot have an edge" that way needs
weeks. But the same question can be answered right now against 9 years of price
history, provided the test is done honestly.

Honestly means: at each point in time T, only information available at T may be
used to form the prediction. The bot at time T knows the outcome of a signal
firing only if that firing is at least FWD bars old, anything more recent has
not resolved yet. So the training set is strictly `fired_at + FWD <= T`. Getting
this wrong turns the whole exercise into the model grading its own homework.

What comes out:

  · calibration curve, when the bot says 60%, what actually happens? The gap is
    overconfidence, and it is measurable per timeframe, per signal, per regime.
  · sample-size reliability, how far off is a prediction built on 30 firings vs
    300? This is the empirical basis for the shrinkage constants in
    signal_scanner, which are currently reasoned estimates, not measurements.

Nothing here touches the trading path. It reads price history and writes a
report. Run it, read it, then decide what to change.

Usage:
  python -m ocean_agent.walkforward              # measure + save + summary
  python -m ocean_agent.walkforward --show       # summary of last run
  python -m ocean_agent.walkforward --symbols BTC,ETH --tf 1h,4h
"""
import json
import os
import sys
import time

RESULT_FILE = os.path.join(os.path.expanduser("~"), ".ocean_agent_walkforward.json")

# 예측값 히스토그램 폭. 기록을 전부 들고 있지 않고 이 격자에 세면서 흘려보낸다.
# 원본을 모으면 5분봉까지 포함할 때 2,600만 건(약 3.7GB)이 되고, 메모리에 올리는
# 순간 10GB를 넘겨 그냥 죽는다. 필요한 건 원본이 아니라 '예측 X일 때 실제 승률'
# 이라는 집계뿐이므로, 세면서 지나가면 파일이 수십 KB로 끝난다.
PRED_BIN = 0.002

FWD = 24                 # prediction horizon in bars, same as the scanner
MIN_TRAIN = 30           # below this many resolved firings, no prediction
STEP_BARS = 250          # re-predict every N bars (mirrors periodic re-measure)
WARMUP = 220             # bars needed before indicators are valid


def _firings(closes, fwd=FWD, highs=None, lows=None):
    """{signal: (side, [(index, won), ...])} over the whole series.

    Computed once per series. Each firing carries the outcome of the trade the
    bot would have taken, so the walk-forward pass is pure bookkeeping after
    this, no re-running indicators per checkpoint.

    highs/lows are the fractal definition. This table calibrates live
    win rates, so measuring a different definition than the one that fires
    would miscalibrate every signal that uses a wick."""
    from .signal_scanner import _series, _signals
    n = len(closes)
    if n < WARMUP + fwd + MIN_TRAIN:
        return {}
    s = _series(closes, highs, lows)
    out = {}
    for name, (side, cond) in _signals(s).items():
        hits = []
        for i in range(WARMUP, n - fwd):
            try:
                if not cond(i):
                    continue
            except Exception:
                continue
            move = closes[i + fwd] / closes[i] - 1.0
            signed = move if side == "long" else -move
            hits.append((i, 1 if signed > 0 else 0))
        # Firings that overlap inside one forward horizon are one observation
        # seen repeatedly, not several. The house standard requires thinning and
        # this file never did it, so a signal that stays true for twenty bars
        # contributed twenty resolved outcomes of the same move to the
        # calibration curve. That matters more here than almost anywhere:
        # measured_winrate replaces pwin outright and sets calibrated_ok,
        # which switches the per-symbol baseline to a flat 0.5 and skips
        # evidence shrinkage, so an inflated number arrives with two safety
        # mechanisms turned off behind it. Same rule as the scanner's _ni.
        thin, last = [], None
        for idx, won in hits:
            if last is None or idx - last >= fwd:
                thin.append((idx, won))
                last = idx
        if len(thin) >= MIN_TRAIN:
            out[name] = (side, thin)
    return out


def walk_series(closes, symbol, tf, fwd=FWD, step=STEP_BARS,
                highs=None, lows=None):
    """Replay history forward, predicting then checking.

    Yields (signal, pred, n_train, won) one at a time instead of returning a
    list. A 5m series alone produces over a million of these per symbol; holding
    them costs gigabytes, and nothing downstream needs an individual record,
    only the counts. Streaming keeps memory flat regardless of history length."""
    n = len(closes)
    for name, (side, hits) in _firings(closes, fwd, highs, lows).items():
        # hits is ordered by index; walk a cursor for the training set so the
        # whole pass stays linear instead of re-scanning per checkpoint.
        train_w = train_n = 0
        ti = 0                      # next hit not yet folded into training
        hi = 0                      # next hit not yet tested
        for t in range(WARMUP + step, n - fwd, step):
            # Fold in every firing whose outcome is known by time t.
            while ti < len(hits) and hits[ti][0] + fwd <= t:
                train_w += hits[ti][1]
                train_n += 1
                ti += 1
            # Test window: firings that happen from t up to the next checkpoint.
            while hi < len(hits) and hits[hi][0] < t:
                hi += 1
            j = hi
            if train_n >= MIN_TRAIN:
                pred = train_w / train_n
                while j < len(hits) and hits[j][0] < t + step:
                    yield name, pred, train_n, hits[j][1]
                    j += 1


class Agg:
    """Streaming counters, everything the report and the curve need.

    Each slot holds [wins, total, pred_sum] so both the actual rate and the
    average prediction can be recovered without keeping raw records."""

    __slots__ = ("hist", "sig_tf", "by_tf", "by_sig", "by_ntrain", "n")

    NTRAIN_EDGES = ((30, 60), (60, 120), (120, 300), (300, 800), (800, 10 ** 9))

    def __init__(self):
        self.hist = {}        # pred bin index -> [wins, total, pred_sum]
        self.sig_tf = {}      # "sig|tf" -> [wins, total]
        self.by_tf = {}
        self.by_sig = {}
        self.by_ntrain = {}
        self.n = 0

    @staticmethod
    def _bump(d, key, won, pred=None):
        a = d.get(key)
        if a is None:
            a = d[key] = [0, 0, 0.0]
        a[0] += won
        a[1] += 1
        if pred is not None:
            a[2] += pred

    def add(self, sig, tf, pred, n_train, won):
        self.n += 1
        self._bump(self.hist, int(pred / PRED_BIN), won, pred)
        a = self.sig_tf.get(f"{sig}|{tf}")
        if a is None:
            a = self.sig_tf[f"{sig}|{tf}"] = [0, 0]
        a[0] += won
        a[1] += 1
        self._bump(self.by_tf, tf, won, pred)
        self._bump(self.by_sig, sig, won, pred)
        for lo, hi in self.NTRAIN_EDGES:
            if lo <= n_train < hi:
                self._bump(self.by_ntrain, f"{lo}", won, pred)
                break

    def to_dict(self):
        return {"n": self.n,
                "hist": {str(k): v for k, v in self.hist.items()},
                "sig_tf_raw": self.sig_tf,
                "by_tf": self.by_tf, "by_sig": self.by_sig,
                "by_ntrain": self.by_ntrain}

    @classmethod
    def from_dict(cls, d):
        a = cls()
        a.n = d.get("n", 0)
        a.hist = {int(k): v for k, v in (d.get("hist") or {}).items()}
        a.sig_tf = d.get("sig_tf_raw") or {}
        a.by_tf = d.get("by_tf") or {}
        a.by_sig = d.get("by_sig") or {}
        a.by_ntrain = d.get("by_ntrain") or {}
        return a


def summarize(agg) -> str:
    """Calibration curve + sample-size reliability, from streamed counters."""
    if isinstance(agg, dict):
        agg = Agg.from_dict(agg)
    if not agg or not agg.n:
        return "기록이 없습니다."

    def line(slot):
        w, t, ps = slot
        return t, ps / t, w / t

    out = [f"검증 표본 {agg.n:,}건 "
           f"({len(agg.by_tf)}시간봉 · {len(agg.by_sig)}신호)", ""]
    out.append("=== 보정 곡선, 봇이 X%라 할 때 실제로는? ===")
    out.append("  예측구간      건수    예측평균   실제      차이")
    edges = [(0.0, .45), (.45, .50), (.50, .55), (.55, .60), (.60, .65),
             (.65, .70), (.70, .80), (.80, 1.01)]
    for lo, hi in edges:
        w = t = 0
        ps = 0.0
        for b, (bw, bt, bps) in agg.hist.items():
            if lo <= b * PRED_BIN < hi:
                w += bw
                t += bt
                ps += bps
        if t < 20:
            continue
        out.append(f"  {lo:.0%}~{hi:.0%}   {t:8,}   {ps / t:7.1%}  "
                   f"{w / t:7.1%}   {w / t - ps / t:+.1%}")
    out.append("")
    out.append("=== 표본 크기별 신뢰도 ===")
    out.append("  표본구간      건수    예측평균   실제      차이")
    for lo, hi in Agg.NTRAIN_EDGES:
        slot = agg.by_ntrain.get(str(lo))
        if not slot or slot[1] < 20:
            continue
        t, pm, am = line(slot)
        hi_s = "이상" if hi > 10 ** 8 else f"~{hi}"
        out.append(f"  {lo}{hi_s:>6}   {t:8,}   {pm:7.1%}  "
                   f"{am:7.1%}   {am - pm:+.1%}")
    out.append("")
    out.append("=== 시간봉별 ===")
    for tf in sorted(agg.by_tf):
        t, pm, am = line(agg.by_tf[tf])
        out.append(f"  {tf:5} {t:8,}건   예측 {pm:.1%}  실제 {am:.1%}  "
                   f"차이 {am - pm:+.1%}")
    out.append("")
    out.append("=== 신호별 (과신이 큰 순) ===")
    rows = []
    for sig, slot in agg.by_sig.items():
        if slot[1] < 50:
            continue
        t, pm, am = line(slot)
        rows.append((am - pm, sig, t, pm, am))
    for d, sig, t, pm, am in sorted(rows)[:12]:
        out.append(f"  {sig:22} {t:7,}건  예측 {pm:.1%} → 실제 {am:.1%}  {d:+.1%}")
    return "\n".join(out)


def run(symbols=None, tfs=None, fwd=FWD, log=print) -> "Agg":
    """Measure across the deep (Binance + Pacifica) series, counting as we go.

    Saves counters, not records. The previous version kept every prediction in a
    list: 5 timeframes already came to 1.67M entries / 236MB, and adding the
    lower timeframes would have made it ~26M / 3.7GB, enough to run the process
    out of memory before it could write anything. The counters answer every
    question the raw records did and fit in tens of KB."""
    from .api_client import PacificaClient
    from .rematrix import COINS, TFS, _fetch_ohlc, _fwd_for
    symbols = symbols or COINS
    tfs = tfs or TFS
    # 항상 메인넷 가격으로 잰다(테스트넷 설정으로 돌려도 마찬가지). 이 표는
    # 시장이 실제로 어떻게 움직였는지를 담아야 하고, 테스트넷 가격은 실제 시장이
    # 아니다. 결과 파일도 매트릭스처럼 네트워크 구분 없이 하나를 공유한다,
    # 계좌 기록(상태·관측·손익)만 data_file()로 분리하는 것이 이 프로젝트의 규칙.
    client = PacificaClient("https://api.pacifica.fi")
    agg = Agg()
    for sym in symbols:
        for tf in tfs:
            try:
                # _fetch returns closes only; the fractal definition needs the
                # wicks, so pull whole bars.
                bars = _fetch_ohlc(client, sym, tf, use_extended=True, log=log)
                closes = [x["c"] for x in bars]
            except Exception as e:
                log(f"  {sym} {tf}: 조회 실패 {e}")
                continue
            # 24 hours on every timeframe, not 24 bars. The calibration this
            # file builds is read by a scanner whose gate, observation and
            # bracket all end at 24h, so measuring a daily signal 576 hours
            # out calibrated the wrong thing.
            tf_fwd = _fwd_for(tf) if fwd == FWD else fwd
            if len(closes) < WARMUP + tf_fwd + MIN_TRAIN:
                log(f"  {sym} {tf}: 데이터 부족 ({len(closes)}봉), 건너뜀")
                continue
            before = agg.n
            for sig, pred, n_train, won in walk_series(
                    closes, sym, tf, fwd=tf_fwd,
                    highs=[x["h"] for x in bars],
                    lows=[x["l"] for x in bars]):
                agg.add(sig, tf, pred, n_train, won)
            log(f"  {sym} {tf}: {len(closes):,}봉 → 검증표본 {agg.n - before:,}건")
    from .signal_scanner import DEFS_VERSION
    # fwd here is the requested horizon in HOURS, not the bar count any one
    # timeframe used; each timeframe derives its own via _fwd_for. Recording
    # the bar count would name one timeframe's number for all of them.
    payload = {"measured_at": int(time.time() * 1000), "fwd_hours": fwd,
               "step": STEP_BARS, "min_train": MIN_TRAIN,
               "defs_version": DEFS_VERSION, **agg.to_dict()}
    tmp = RESULT_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    os.replace(tmp, RESULT_FILE)
    return agg


# 파일명 주의: 두뇌(brain)의 메타학습도 "calibration"이라는 말을 쓰고
# ~/.ocean_agent_{net}_calibration.json 에 저장한다. 둘은 완전히 다른 것이다,
# 이쪽은 9년 가격사에서 잰 보정표(신호별 실측 승률), 저쪽은 이 봇이 낸 예측이
# 실제로 맞았는지의 기록. 이름이 비슷하면 나중에 반드시 헷갈리므로 구분한다.
CURVE_FILE = os.path.join(os.path.expanduser("~"),
                          ".ocean_agent_walkforward_curve.json")
CURVE_MIN_BIN = 2000     # bin must hold this many samples to be trusted
# Per-signal sample floor. Out-of-sample checks showed thin combinations
# can top the ranking on luck that does not transfer to other symbols,
# so the floor is set generously to keep them out of first place.
SIG_MIN_N = 1000


def build_curve(agg, save: bool = True) -> dict:
    """Turn the streamed counters into a predicted -> actual lookup.

    Bins are merged to equal COUNT, not equal width, so every point rests on the
    same amount of evidence; the interesting region (high predictions) is thin
    and equal-width bins would leave it on a handful of samples. The histogram
    is already width-binned at PRED_BIN, which is fine to merge upward from.

    Deliberately global, not per (symbol, timeframe, signal): the measurement
    says the per-signal error is small (worst -1.2%) while the error from a
    prediction simply being extreme is large (-26% at the top). Slicing finer
    would rebuild the very overfitting this curve exists to undo."""
    if isinstance(agg, dict):
        agg = Agg.from_dict(agg)
    if not agg or agg.n < CURVE_MIN_BIN * 3:
        return {}
    target = max(CURVE_MIN_BIN, agg.n // 40)
    curve = []
    w = t = 0
    ps = 0.0
    for b in sorted(agg.hist):
        bw, bt, bps = agg.hist[b]
        w += bw
        t += bt
        ps += bps
        if t >= target:
            curve.append([round(ps / t, 4), round(w / t, 4), t])
            w = t = 0
            ps = 0.0
    # 남은 꼬리 처리. 근거가 충분하면 제 구간으로 세우고, 얇을 때만 앞 구간에
    # 합친다. 무조건 합치면 마지막 구간이 다른 구간의 몇 배 크기로 부풀어 예측
    # 평균이 위로 끌려간다(검증에서 0.505가 0.588로 밀렸다), 곡선의 오른쪽 끝은
    # 고승률 구간이라 하필 제일 중요한 자리다.
    if t >= CURVE_MIN_BIN or (t and not curve):
        curve.append([round(ps / t, 4), round(w / t, 4), t])
    elif t:
        p0, a0, n0 = curve[-1]
        tot = n0 + t
        curve[-1] = [round((p0 * n0 + ps) / tot, 4),
                     round((a0 * n0 + w) / tot, 4), tot]
    # Per-signal measured win rate is the real ranking information:
    # validated out of sample, it separates candidates far better than raw
    # backtest figures. A symbol's own backtest number carries that
    # symbol's luck; a signal's skill transfers across symbols.
    sig_tf = {k: [round(v[0] / v[1], 4), v[1]]
              for k, v in agg.sig_tf.items() if v[1] >= SIG_MIN_N}
    # The stamp goes on this file too. _load_curve reads CURVE_FILE and
    # rejects anything whose defs_version does not match, but only run()
    # stamped RESULT_FILE, so the curve was born stale and could never be
    # used again: calibrated() and measured_winrate() returned None forever,
    # calibrated_ok stayed False, and every prediction took evidence
    # shrinkage permanently rather than only while definitions were behind.
    from .signal_scanner import DEFS_VERSION
    out = {"measured_at": int(time.time() * 1000), "n": agg.n,
           "defs_version": DEFS_VERSION, "curve": curve, "sig_tf": sig_tf}
    if save:
        tmp = CURVE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        os.replace(tmp, CURVE_FILE)
    return out


_CURVE_CACHE = {"mtime": None, "pts": None, "sig_tf": None}


# Seed shipped in the package. A fresh install has measured nothing, so without
# this the calibration is simply absent and raw backtest win rates go unshrunk.
# The user's own measurement replaces it once it lands in the home directory.
_SEED_CURVE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "curve_seed.json")


def _load_curve():
    """Cached read of the calibration file, keyed on (path, mtime)."""
    path = (CURVE_FILE if os.path.exists(CURVE_FILE)
            else _SEED_CURVE if os.path.exists(_SEED_CURVE) else None)
    if path is None:
        return None
    mt = os.path.getmtime(path)
    c = _CURVE_CACHE
    if c.get("path") != path or c["mtime"] != mt:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        # A table measured under older signal definitions is not a calibration
        # of the current ones. The 08-05 table scored the lower Bollinger band
        # as a short and fractals off closes; feeding those win rates to
        # today's definitions would also set calibrated_ok, which switches the
        # per-symbol baseline to a flat 0.5 and skips evidence shrinkage. Empty
        # is the safe answer: the caller falls back to shrinkage.
        from .signal_scanner import DEFS_VERSION
        stale = d.get("defs_version") != DEFS_VERSION
        c["pts"] = [] if stale else [(p, a) for p, a, _ in d.get("curve", [])]
        c["sig_tf"] = {} if stale else (d.get("sig_tf") or {})
        c["stale"] = stale
        c["mtime"] = mt
        c["path"] = path
    return c


def measured_winrate(sig: str, tf: str):
    """This signal's measured win rate on this timeframe, pooled over symbols.

    Returns (win_rate, n) or None when the signal has not been measured with
    enough samples. This is the number that actually ranks well out of sample,
    see the holdout figures in build_curve."""
    try:
        c = _load_curve()
        if not c or not c["sig_tf"]:
            return None
        v = c["sig_tf"].get(f"{sig}|{tf}")
        return (v[0], v[1]) if v else None
    except Exception:
        return None


def calibrated(pwin: float):
    """Measured win rate for a backtest that claims `pwin`. None if unmeasured.

    Linear interpolation between bin centres; outside the measured range the
    nearest end is held flat rather than extrapolated, beyond the data the
    honest answer is "no better than the last thing we saw"."""
    try:
        c = _load_curve()
        pts = c["pts"] if c else None
        if not pts:
            return None
        if pwin <= pts[0][0]:
            return pts[0][1]
        if pwin >= pts[-1][0]:
            return pts[-1][1]
        for i in range(1, len(pts)):
            p0, a0 = pts[i - 1]
            p1, a1 = pts[i]
            if pwin <= p1:
                w = (pwin - p0) / (p1 - p0) if p1 > p0 else 0.0
                return a0 + (a1 - a0) * w
    except Exception:
        return None
    return None


LOCK_FILE = CURVE_FILE + ".running"
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
            log(f"보정표 재측정 락이 {age / 3600:.1f}시간째 방치됨, "
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


def curve_age_days() -> float:
    """보정표가 측정된 지 며칠 됐나. 없으면 아주 큰 값."""
    try:
        if not os.path.exists(CURVE_FILE):
            return 1e9
        with open(CURVE_FILE, encoding="utf-8") as f:
            return (time.time() * 1000 - float(
                json.load(f).get("measured_at", 0))) / 86_400_000
    except Exception:
        return 1e9


def live_vs_measured(min_live: int = 20):
    """실거래·실관측 결과가 보정표와 얼마나 어긋나는가, 루프의 ④→⑤ 구간.

    보정표는 과거 9년에서 '이 신호는 이만큼 이긴다'를 잰 값이다. 지금 시장에서
    실제로 그만큼 이기고 있는지는 별개 문제이고, 어긋난다면 국면이 바뀌었거나
    측정이 낡았다는 뜻이다. 여기서 그 격차를 신호별로 드러낸다.

    관측 표본은 반드시 기간 관문을 통과한 것만 센다, 한나절에 몰린 20건은
    20개의 표본이 아니라 같은 시장 1건을 20번 센 것이다(2026-08-04 교훈).

    반환: (rows, overall), rows는 (격차, 신호, 시간봉, 실측, 실전, 표본),
    overall은 전체 합산 (실측평균, 실전승률, 표본) 또는 None."""
    try:
        from .observer import _load_stats, _span_days, MIN_SPAN_DAYS
        stats = _load_stats()
        c = _load_curve()
    except Exception:
        return [], None
    if not c or not c.get("sig_tf"):
        return [], None
    live = {}
    for k, e in stats.items():
        if not isinstance(e, dict) or k.startswith("*|"):
            continue
        if _span_days(e) < MIN_SPAN_DAYS:
            continue                      # 뭉친 표본은 세지 않는다
        key = f"{e.get('sig')}|{e.get('tf')}"
        a = live.setdefault(key, [0, 0])
        a[0] += e.get("win", 0)
        a[1] += e.get("total", 0)
    rows = []
    tot_w = tot_n = 0
    exp_sum = 0.0
    for key, (w, n) in live.items():
        m = c["sig_tf"].get(key)
        if not m or n < min_live:
            continue
        sig, tf = key.split("|", 1)
        rows.append((w / n - m[0], sig, tf, m[0], w / n, n))
        tot_w += w
        tot_n += n
        exp_sum += m[0] * n
    rows.sort()
    overall = (exp_sum / tot_n, tot_w / tot_n, tot_n) if tot_n else None
    return rows, overall


def divergence_alarm(gap: float = 0.06, min_n: int = 60):
    """실전이 보정표보다 이만큼 못하면 경보 문자열, 아니면 None.

    한쪽 방향만 본다, 실전이 더 좋은 건 재측정을 서두를 이유가 아니다.

    메인넷에서만 판정한다. 보정표는 실제 가격사(바이낸스+파시피카 메인넷)에서
    잰 값인데, 실전 성적은 네트워크별로 분리된 파일에서 온다. 테스트넷은 가격이
    실제 시장이 아니므로, 그 성적으로 메인넷 보정표를 '틀렸다'고 판정하면
    멀쩡한 표를 버리고 재측정을 반복하게 된다."""
    from . import data_tag
    if data_tag() != "mainnet":
        return None
    _, overall = live_vs_measured()
    if not overall:
        return None
    exp, act, n = overall
    if n >= min_n and exp - act >= gap:
        return (f"실전이 실측표보다 낮음 (실측 {exp:.1%} vs 실전 {act:.1%}, "
                f"표본 {n})")
    return None


def ensure_fresh(policy: dict, log=print) -> bool:
    """보정표가 낡았으면(또는 실전이 어긋나면) 백그라운드로 재측정, 루프의 ⑥→②.

    이 연결이 없으면 보정표는 만든 날에 멈춘 사진이고, 자가개선이 아니라
    일회성 보정에 그친다. 매트릭스(rematrix)와 같은 방식으로 돈다."""
    import subprocess
    every = float(policy.get("walkforward_every_days", 0) or 0)
    if every <= 0:
        return False
    alarm = divergence_alarm()
    if alarm and curve_age_days() >= 1.0:
        log(alarm + " → 보정표 재측정 앞당김")
    elif curve_age_days() < every:
        return False
    # On success the lock is handed to the child process, which releases it
    # at the end of main(); the parent only releases when the spawn fails.
    if not _acquire_lock(log):
        return False
    try:
        subprocess.Popen(
            [sys.executable, "-m", "ocean_agent.walkforward",
             "--tf", ",".join(policy.get("walkforward_tfs",
                                         ["1h", "4h", "8h", "12h", "1d"]))],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log(f"보정표 재측정 시작 (마지막 측정 {curve_age_days():.1f}일 전), "
            f"백그라운드, 완료되면 다음 사이클부터 반영")
        return True
    except Exception as e:
        log(f"보정표 재측정 실행 실패: {e}")
        _release_lock()   # child never started, so it cannot release it
        return False


def load():
    """마지막 측정의 집계(Agg). 없으면 None."""
    if not os.path.exists(RESULT_FILE):
        return None
    try:
        with open(RESULT_FILE, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError, MemoryError):
        return None      # 옛 형식(원본 기록 236MB)일 수 있다, 다시 측정하면 된다
    if "hist" not in d:
        return None      # 구버전 파일: 집계가 없으므로 요약 불가
    return Agg.from_dict(d)


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    args = sys.argv[1:]
    if "--show" in args:
        a = load()
        print(summarize(a) if a else "측정 기록이 없습니다, python -m ocean_agent.walkforward 로 측정하세요.")
        return
    if "--live" in args:
        rows, overall = live_vs_measured()
        if not overall:
            print("실전 표본이 아직 부족합니다 (기간 관문을 통과한 관측 기준).")
            return
        exp, act, n = overall
        print(f"전체: 실측표 {exp:.1%} vs 실전 {act:.1%} "
              f"(표본 {n:,}, 격차 {act - exp:+.1%})")
        print()
        print("  신호                  시간봉   실측표    실전     격차   표본")
        for d, sig, tf, m, a, cnt in rows:
            print(f"  {sig:20} {tf:5}  {m:6.1%}  {a:6.1%}  {d:+6.1%}  {cnt}")
        alarm = divergence_alarm()
        print()
        print("  " + (alarm + " → 재측정 대상" if alarm else "경보 없음"))
        return
    syms = tfs = None
    for a in args:
        if a.startswith("--symbols"):
            syms = a.split("=", 1)[-1].split(",") if "=" in a else None
        if a.startswith("--tf"):
            tfs = a.split("=", 1)[-1].split(",") if "=" in a else None
    if "--symbols" in args:
        syms = args[args.index("--symbols") + 1].split(",")
    if "--tf" in args:
        tfs = args[args.index("--tf") + 1].split(",")
    print("워크포워드 검증 시작, 과거 시점에서 예측하고 결과를 대조합니다...")
    try:
        agg = run(symbols=syms, tfs=tfs)
        # 보정표까지 만들어야 봇이 쓸 수 있다. 이게 빠지면 백그라운드 재측정이
        # 기록만 갱신하고 정작 판단에 쓰이는 표는 낡은 채로 남는다.
        c = build_curve(agg)
        if c:
            print(f"보정표 갱신, 곡선 {len(c['curve'])}구간 · "
                  f"신호별 {len(c.get('sig_tf', {}))}조합 (표본 {c['n']:,})")
        else:
            print("보정표 생성 실패, 표본 부족")
        print()
        print(summarize(agg))
    finally:
        # Release even when the measurement raises, otherwise the lock file
        # lingers and blocks re-measurement for the whole stale window.
        _release_lock()


if __name__ == "__main__":
    main()
