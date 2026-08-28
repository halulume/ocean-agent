# -*- coding: utf-8 -*-
"""Hourly pick-seal generator for the bracket trader.

Produces the seal file (outputs/내일예측_*.json) that bracket_trader
consumes. One pass does four things:

    score      every non-spot symbol gets an expected 24h move from its own
               recent bars, weighted by a per-class trust factor
    gate       only symbols whose live order book can absorb a $300 market
               order within the slippage cap are eligible
    direction  each pick goes long when its own 24h move sits at or above
               the cross-sectional median of all scored symbols, short below
    specials   up to two extra seats for funding-extreme symbols, taking the
               side that receives the funding payment

Bars come from public market-data endpoints only and each symbol's bars all
come from a single source: Binance USDT-margined futures klines when the
symbol has a futures listing there, Pacifica's own klines otherwise.
Sources are never mixed within one symbol. Market-hours underlyings are
represented only by their around-the-clock token bars; a token without
enough bar history is excluded rather than guessed at.

Nothing here places orders. The generator reads markets, prices, bars and
order books, then writes one JSON file.

Run standalone:  python -m ocean_agent.seal_maker [--out DIR]
"""
import gzip
import json
import os
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

from . import historical_data as hd

KST = timezone(timedelta(hours=9))
TF = "1h"
PER = 24                 # bars per 24h horizon
WIN = 720                # scoring window, bars
SLOTS = 10               # regular seats, filled by class score rank
# Ten, not the eight the engine can hold: symbols already open and the
# operator's skip list both eat seats, and a seal with no bench leaves
# them empty until the next hour. On 08-19 three of six were skipped
# and three were already held, so nothing could be entered at all.
SEATS = 6                # regular picks that rank ahead of the specials
SPECIAL = 2              # extra seats for funding-extreme symbols
SPECIAL_APR = 1.00       # minimum |funding| APR for a special seat
SPECIAL_MOVE = 0.03      # specials also need this much expected move
SIZE = 300               # USD walked against the live book for the gate
MAX_SLIP = 0.005         # slippage cap for the gate
MIN_ROWS = 25            # refuse to seal on a thinner cross-section
# Classes that ride the cross-sectional side rather than the touch side.
#
# Stocks were moved here on 08-19 after two picks (SAMSUNG, SKHYNIX) fell
# hard on a day the cross-section had called short. On 08-20 the same
# question was asked of 71 stock picks where the two sides actually
# disagreed: the touch side beat the cross-section by a wide margin on both
# average and hit rate, and it held in both halves of the period (figures in
# the measurement ledger, kept out of the shipped package). Two picks in
# one falling session were a regime, not a rule, so stocks are back on the
# touch side. RWA stays: it is the one class where the cross-section wins
# over the whole period, and its split picks are too thin and too mixed to
# move it either way.
XSEC_DIR_CLASSES = {"기타·RWA"}
# Which touch horizon calls the side. "touch2h" asks the same 2h +-1.0% rate
# the trader asks at entry; "touch24" is the original 24h +-3% rate.
#
# The trader has overridden the seal with the 2h rule since 2026-08-26, so the
# seal was publishing a side nothing traded, and the trader's fallback for a
# missing cache returned to that unused rule. Replaying the seals themselves
# on both horizons put the short one ahead in each half of the period.
#
# Only the side moves. Ranking and size still come from the 24h expected move,
# because shortening the ranking horizon leaves most of the same symbols in
# the seats and measured no better.
#
# Set SEAL_SIDE_SOURCE=touch24 in the environment to put it back.
SIDE_SOURCE = os.environ.get("SEAL_SIDE_SOURCE", "touch2h")
PER2, LEVEL2 = 2, 0.010          # the short horizon: 2 bars, +-1.0%
# Ranking window, in bars. Size still comes from the 30 day median (WIN):
# a take profit line set off one hot day would never be reached. Ranking
# does not have that problem, and 08-28 measured eight window lengths with
# size held fixed. 90일 was ahead of 30일 in both windows (5.5년
# +0.119±0.213p, 8월 +1.197±1.699p, ledger 156). Both are inside their own
# error bars and no measurement can close that gap (ledger 157 puts the
# daily spread at 10.3p, so two sigma would take 82 years); the operator
# decided on the four-out-of-four positive sign. The ATR term is left OFF
# here: with it, 90일 lost to the current rule (-0.640p against -0.592p).
RANK_WIN = 2160                  # 90일
RANK_MIN = 1440                  # 이만큼도 없으면 순위는 30일 값으로
# Whether the classes above still ride the cross-sectional side. Turned off
# on 2026-08-27 by operator decision so every class asks the same question:
# the trader already overrode all of them with the 2h rule at entry, so the
# seal saying something different for one class only split the record.
# xsec_dir keeps being written either way, so the arms stay comparable.
# Set SEAL_XSEC_DIR=1 to bring it back.
XSEC_DIR_ENABLED = os.environ.get("SEAL_XSEC_DIR", "0") == "1"
# Individual symbols that ride it regardless of class. CHIP was here for
# the same one-day reason as stocks and left with them; it is on the skip
# list anyway (one tick is 0.392%, wider than any stop can hold).
XSEC_DIR_SYMS: set[str] = set()

# Default seal directory: <project root>/outputs, next to the package.
# Same expression bracket_trader uses, so the bot finds what this writes.
OUTPUTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outputs")

# Asset classes. Pacifica lists stocks, RWA, FX and commodities beside
# crypto; the trust weights and touch calibration are kept per class.
STOCK = {"NVDA", "SAMSUNG", "SKHYNIX", "MU", "SNDK", "DRAM", "CRCL", "HOOD",
         "MSTR", "GOOGL", "SPCX", "CHIP", "AAPL", "TSLA", "META", "AMZN",
         "MSFT", "COIN", "AMD", "PLTR"}
MACRO = {"EURUSD", "USDJPY", "XAU", "XAG", "COPPER", "NATGAS", "CL", "PAXG"}

# Per-class trust factor applied to the predicted move when ranking.
TRUST = {"주식": 0.520, "기타·RWA": 0.450, "FX·원자재": 0.450, "코인": 0.386}

# Perp-only symbol name differences on Binance futures (1000-bundle names).
_FUT_OVERRIDE = {"kBONK": "1000BONKUSDT", "kPEPE": "1000PEPEUSDT",
                 "kSHIB": "1000SHIBUSDT"}

# Calibration offsets for raw touch rates, shipped as package data.
# Rows are [lo, hi, offset, sample_count] per "class|level" key.
_CALIB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "data", "calib_offsets.json")
_calib_table: dict | None = None


def klass(sym: str) -> str:
    """Asset class label for a Pacifica symbol."""
    if sym in STOCK:
        return "주식"
    if sym in MACRO:
        return "FX·원자재"
    if hd.BINANCE_SYMBOL.get(sym):
        return "코인"
    return "기타·RWA"


def futures_symbol(sym: str) -> str | None:
    """Binance USDT-margined futures symbol for a Pacifica symbol, if any."""
    return _FUT_OVERRIDE.get(sym) or hd.BINANCE_SYMBOL.get(sym)


def calib(cls: str, lv: float, p: float) -> float:
    """Apply the shipped calibration offset to a raw touch rate.

    Returns p unchanged when no offset row covers it or the table cannot
    be read. Only rows backed by at least 2000 samples are applied.
    """
    global _calib_table
    if _calib_table is None:
        try:
            with open(_CALIB_PATH, encoding="utf-8") as f:
                _calib_table = json.load(f)
        except Exception:
            _calib_table = {}
    key = min((0.02, 0.03, 0.05), key=lambda x: abs(x - lv))
    for a, b, o, n in _calib_table.get(f"{cls}|{key}", []):
        if a <= p < b and n >= 2000:
            return max(0.0, min(1.0, p + o))
    return p


# ── Pacifica request pacing ──────────────────────────────────────────────
# The generator runs inside the trader's own process while the trader keeps
# hitting the same API for entries and aftercare. Spacing this module's
# Pacifica requests out keeps the generator from bursting into the rate
# limit that the trader's own calls depend on.
_PAC_GAP_SEC = 0.5
_pac_lock = threading.Lock()
_pac_next = 0.0


def _pace() -> None:
    """Block until this module's next Pacifica request slot."""
    global _pac_next
    while True:
        with _pac_lock:
            now = time.monotonic()
            if now >= _pac_next:
                _pac_next = now + _PAC_GAP_SEC
                return
            wait = _pac_next - now
        time.sleep(wait)


# ── order book gate ──────────────────────────────────────────────────────

def pac_book(cl, sym):
    """Pacifica order book as ((bids, asks), note); levels are (price, qty).

    The endpoint name has varied across API revisions, so a few candidates
    are tried in order; (None, reason) when none answers.
    """
    for path, params in (("book", {"symbol": sym}),
                         ("orderbook", {"symbol": sym}),
                         ("info/book", {"symbol": sym}),
                         ("book/level2", {"symbol": sym})):
        try:
            _pace()
            d = cl._get(path, params)
        except Exception:
            continue
        if not d:
            continue
        lv = d.get("l") or d.get("levels") or d
        try:
            if isinstance(lv, list) and len(lv) == 2:
                bids = [(float(x["p"]), float(x["a"])) for x in lv[0]]
                asks = [(float(x["p"]), float(x["a"])) for x in lv[1]]
                return (bids, asks), path
        except Exception:
            continue
    return None, "book endpoint not found"


def slip_cost(book, usd: float, side: str) -> float | None:
    """Cost versus mid of a market order of `usd`, walking the book.

    None when the book cannot fill the size (too thin to trade).
    """
    if not book:
        return None
    bids, asks = book
    if not bids or not asks:
        return None
    mid = (bids[0][0] + asks[0][0]) / 2.0
    lv = asks if side == "buy" else bids
    need, paid, qty = usd, 0.0, 0.0
    for px, am in lv:
        take = min(need, px * am)
        if take <= 0:
            continue
        paid += take
        qty += take / px
        need -= take
        if need <= 0:
            break
    if need > 0 or qty <= 0:
        return None
    return abs(paid / qty / mid - 1.0)


# ── bar loading (one source per symbol, cache first) ─────────────────────
#
# The 90 day ranking window needs 2,184 bars and a Binance kline request
# caps at 1,000. Asking three times an hour for 74 symbols would be 222
# requests where 74 used to do, and 2,183 of every 2,184 bars asked for are
# ones we already have on disk. The operator has said twice that the past
# belongs in the cache and only the new day should be fetched.
#
# So: read the cache, ask only for what comes after its last bar, write the
# merged series back. First run on a machine with the project cache tops up
# about a day; after that it is one or two bars an hour. A machine with no
# cache (a new install) falls through to the plain fetch and builds one.
#
# Source discipline (데이터목록 1조) is unchanged and is the reason this
# does not simply read the Pacifica cache for everything even though that
# file is fresher and covers all 75 symbols: a coin's size and side come
# from Binance bars, so its ranking must too. Mixing the two inside one
# symbol measured 4.2% disagreement on 08-13.

def _cache_path(sym: str, bsym: str) -> str:
    """이 종목의 봉이 사는 곳. 바이낸스면 선물 파일, 아니면 파시피카 파일."""
    if bsym:
        return os.path.join(hd.CACHE_DIR, f"{bsym}_1h_fut_ohlc.json.gz")
    return os.path.join(hd.CACHE_DIR, f"pac_{sym}_1h_ohlc.json.gz")


def _read_cache(path: str) -> list[dict]:
    """캐시의 1시간봉. 없거나 깨졌으면 []."""
    if not os.path.exists(path):
        return []
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            bars = json.load(f).get("bars") or []
    except (OSError, ValueError, KeyError, AttributeError):
        return []
    out = []
    for b in bars:
        try:
            c = float(b["c"])
            out.append({"t": int(b["t"]), "c": c,
                        "h": float(b.get("h") or c),
                        "l": float(b.get("l") or c)})
        except (TypeError, ValueError, KeyError):
            continue
    out.sort(key=lambda x: x["t"])
    return out


def _merge_bars(old: list[dict], new: list[dict]) -> list[dict]:
    """시각으로 합친다. 같은 시각은 새 쪽이 이긴다(먼저 봤을 때 미완성일 수
    있다). 정렬은 여기서 한 번만 한다."""
    m = {b["t"]: b for b in old}
    m.update({b["t"]: b for b in new})
    return [m[k] for k in sorted(m)]


def _write_cache(path: str, bars: list[dict]) -> None:
    """원자적으로 쓴다. 실패해도 조용히 넘어간다(캐시는 편의이지 정답이 아니다)."""
    if not bars:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".seal.tmp"
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump({"start_ms": int(bars[0]["t"]),
                       "end_ms": int(bars[-1]["t"]) + 3_600_000,
                       "bars": bars}, f)
        os.replace(tmp, path)
    except OSError:
        pass


def _binance_fut_bars(bsym: str, limit: int = 1000) -> list[dict]:
    """Most recent closed 1h bars from Binance USDT-margined futures.

    The still-forming bar is dropped so every bar is final. Returns [] on
    persistent network failure.
    """
    url = (f"https://fapi.binance.com/fapi/v1/klines?symbol={bsym}"
           f"&interval=1h&limit={min(limit, 1000)}")
    d = None
    for i in range(3):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "ocean-agent"})
            with urllib.request.urlopen(req, timeout=20) as r:
                d = json.loads(r.read())
            break
        except Exception:
            if i == 2:
                return []
            time.sleep(2 * (i + 1))
    out = []
    for k in d or []:
        try:
            out.append({"t": int(k[0]), "h": float(k[2]),
                        "l": float(k[3]), "c": float(k[4])})
        except (TypeError, ValueError, IndexError):
            continue
    return _drop_open_bar(out)


def _pacifica_bars(cl, sym: str, need: int = WIN + PER + 30) -> list[dict]:
    """Most recent closed 1h bars from Pacifica's kline endpoint."""
    step = 3_600_000
    now = int(time.time() * 1000)
    try:
        _pace()
        d = cl._get("kline", {"symbol": sym, "interval": TF,
                              "start_time": now - step * (need + 2),
                              "end_time": now})
    except Exception:
        return []
    out = []
    for c in d or []:
        try:
            out.append({"t": int(c["t"]),
                        "h": float(c.get("h") or c["c"]),
                        "l": float(c.get("l") or c["c"]),
                        "c": float(c["c"])})
        except (KeyError, TypeError, ValueError):
            continue
    out.sort(key=lambda b: b["t"])
    return _drop_open_bar(out)


def _drop_open_bar(bars: list[dict]) -> list[dict]:
    """Remove the still-forming last bar; closed bars never change."""
    if not bars:
        return bars
    open_start = int(time.time() * 1000) // 3_600_000 * 3_600_000
    return [b for b in bars if b["t"] < open_start]


def load_bars(sym: str, base_url: str) -> tuple[list[dict], str]:
    """1h bars for one symbol from a single source, cache first.

    Binance futures when the symbol is listed there, Pacifica otherwise.
    A non-empty note means the symbol must be skipped, not guessed at.

    The cache holds the history and the fetch covers only what came after
    it, so the ranking window can be 90 days without asking for 90 days of
    bars every hour. When there is no cache the fetch is what it always
    was and the cache is written for next time.
    """
    bsym = futures_symbol(sym)
    path = _cache_path(sym, bsym)
    old = _read_cache(path)
    if bsym:
        fresh = _binance_fut_bars(bsym)
    else:
        from .api_client import PacificaClient
        fresh = _pacifica_bars(PacificaClient(base_url), sym)
    if not fresh and not old:
        return [], "bars 0"
    b = _merge_bars(old, fresh) if old else fresh
    # Only write when the fetch actually added something, so a failed fetch
    # cannot rewrite the file and a run with nothing new costs no disk.
    if fresh and old and b[-1]["t"] > old[-1]["t"]:
        _write_cache(path, b)
    elif fresh and not old:
        _write_cache(path, b)
    if len(b) < WIN + PER:
        return [], f"bars {len(b)} < {WIN + PER}"
    return b, ""


# ── per-symbol scoring ───────────────────────────────────────────────────

def _touch_side(h2, u: float, d_: float) -> str:
    """The touch side, on whichever horizon SIDE_SOURCE names.

    h2 comes from one(), computed on the bars the seal just fetched, so this
    asks the API's own data rather than a cache that may be hours old. The
    trader has to read a cache because at entry it holds no bars; the seal
    does not, and a rule fed fresher data cannot be the weaker of the two.

    Falls back to the 24h rate only when the window was too short to score a
    single 2h flag, which means the symbol could not be scored at all.
    """
    if SIDE_SOURCE == "touch2h" and h2 in ("long", "short"):
        return h2
    return "long" if u >= d_ else "short"


def one(sym: str, base_url: str) -> dict | None:
    """Score one symbol from its own bars; None when it cannot be scored."""
    b, note = load_bars(sym, base_url)
    if note:
        return None
    c = [x["c"] for x in b]
    h = [x["h"] for x in b]
    lo = [x["l"] for x in b]
    n = len(c)
    i = n - 1
    seg = c[i - WIN:i + 1]
    # median absolute 24h move over the window
    rs = sorted(abs(seg[k] / seg[k - PER] - 1.0)
                for k in range(PER, len(seg)) if seg[k - PER] > 0)
    if not rs or c[i] <= 0:
        return None
    mv = rs[len(rs) // 2]
    # where the current 14-bar ATR sits within its own recent distribution
    tr = 0.0
    for k in range(i - 13, i + 1):
        tr += max(h[k] - lo[k], abs(h[k] - c[k - 1]), abs(lo[k] - c[k - 1]))
    atr = tr / 14 / c[i]
    hist = []
    for j in range(max(14, n - 500), n):
        t2 = 0.0
        for k in range(j - 13, j + 1):
            t2 += max(h[k] - lo[k], abs(h[k] - c[k - 1]),
                      abs(lo[k] - c[k - 1]))
        hist.append(t2 / 14 / c[j] if c[j] > 0 else 0.0)
    hist.sort()
    pct = sum(1 for x in hist if x < atr) / len(hist) if hist else 0.5
    hit3 = sum(1 for x in rs if x >= 0.03) / len(rs)
    # touch rates: share of 24h windows that reached each level up / down
    tou = {}
    for lv in (0.02, 0.03, 0.05):
        u = d_ = cnt = 0
        for k in range(i - WIN, i - PER):
            if k < 1 or c[k] <= 0:
                continue
            cnt += 1
            if max(h[k + 1:k + 1 + PER]) / c[k] - 1.0 >= lv:
                u += 1
            if 1.0 - min(lo[k + 1:k + 1 + PER]) / c[k] >= lv:
                d_ += 1
        tou[lv] = (u / cnt, d_ / cnt) if cnt else (0.0, 0.0)
    # expected size: the window median scaled by how hot the ATR runs now
    pred = mv * (0.7 + 0.6 * pct)
    # Ranking value: the same statistic over a longer window and with no
    # ATR term. Falls back to pred when the symbol has not been listed long
    # enough (SAMSUNG and SKHYNIX were five days short on 08-28), so a new
    # listing keeps its seat rather than vanishing from the board.
    rank_mv = rank_days = None
    if i >= RANK_MIN + PER:
        w = min(RANK_WIN, i - PER)
        rs2 = sorted(abs(c[k] / c[k - PER] - 1.0)
                     for k in range(i - w + 1, i + 1) if c[k - PER] > 0)
        if rs2:
            rank_mv = rs2[len(rs2) // 2]
            # the window ACTUALLY used, not the one asked for: a symbol
            # listed 86 days ago ranks on 85 days, and the seal should say
            # so rather than stamping every row 90일
            rank_days = round(w / 24)
    # Short-horizon touch rate, from the bars already loaded above rather
    # than from a cache file. The trader reads a cache because at entry it
    # has no bars of its own; this function does, and they are fresher and
    # cannot be stale, so there is nothing here to fall back from. Same
    # construction as bracket_trader._touch2h_side: flags look FORWARD PER2
    # bars and the window stops PER2 back, so nothing unclosed is counted.
    up2 = dn2 = cnt2 = 0
    for k in range(max(1, i - WIN), i - PER2 + 1):
        base = c[k]
        if base <= 0:
            continue
        cnt2 += 1
        if max(h[k + 1:k + 1 + PER2]) / base - 1.0 >= LEVEL2:
            up2 += 1
        if 1.0 - min(lo[k + 1:k + 1 + PER2]) / base >= LEVEL2:
            dn2 += 1
    h2 = ("long" if up2 >= dn2 else "short") if cnt2 else None
    return {"mv": mv, "atrq": pct, "pred": pred, "hit3": hit3,
            "rank_mv": rank_mv, "rank_days": rank_days,
            "px": c[i], "tou": tou, "h2": h2,
            "h2_up": (up2 / cnt2) if cnt2 else None,
            "h2_dn": (dn2 / cnt2) if cnt2 else None}


# ── seal assembly ────────────────────────────────────────────────────────

def btc_line(base_url: str = "") -> str:
    """One line on where BTC stands, printed with every seal.

    Asked for on 08-28, after the live book was split by BTC regime: of 191
    real closes, the flat band alone accounts for more loss than the whole
    book has lost, at a 34% hit rate against 48-54% everywhere else. The
    reading is not proven (59 trades, and the long replay says the opposite
    about that band) but it is the one place the money has actually gone,
    so the number goes next to the picks it was chosen under. Nothing acts
    on it: it is here to be watched, and to be checkable later against the
    trades taken that hour.

    Falls back to a plain note rather than raising: a seal must never fail
    to be written because a price series was unavailable.
    """
    try:
        bars, note = load_bars("BTC", base_url or os.environ.get(
            "PACIFICA_BASE_URL", "https://api.pacifica.fi"))
        if note or len(bars) < 25:
            return f"BTC: 봉이 모자라 판정 못 함 ({note or len(bars)})"
        c = [float(b["c"]) for b in bars]
        now = c[-1]
        r1 = (c[-1] / c[-2] - 1) * 100
        r24 = (c[-1] / c[-25] - 1) * 100
        win = c[-25:]
        rng = (max(win) / min(win) - 1) * 100
        band = ("큰 하락" if r24 < -3 else "하락" if r24 < -1
                else "횡보" if r24 < 1 else "상승" if r24 < 3 else "큰 상승")
        return (f"BTC {now:,.0f} · 1h {r1:+.2f}% · 24h {r24:+.2f}% "
                f"({band}) · 24h 고저폭 {rng:.2f}%")
    except Exception as e:                      # noqa: BLE001
        return f"BTC: 확인 실패 ({type(e).__name__})"


def make_seal(out_dir: str | None = None, log=print) -> str | None:
    """Generate one seal file; returns its path, or None when refused.

    Refuses (returns None) when the scored cross-section is too thin to
    trust the direction rule. Raises nothing on individual symbol failures;
    a symbol that cannot be scored or gated is simply skipped.
    """
    from .api_client import PacificaClient
    base = os.environ.get("PACIFICA_BASE_URL", "https://api.pacifica.fi")
    cl = PacificaClient(base)
    _pace()
    mk = {m["symbol"]: m for m in cl.get_markets()
          if m.get("instrument_type") != "spot"}
    _pace()
    pr = {p["symbol"]: p for p in cl.get_prices()}

    rows = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for sym, d in ex.map(lambda s: (s, one(s, base)), sorted(mk)):
            if not d:
                continue
            k = klass(sym)
            base_v = d.get("rank_mv")
            d.update({"sym": sym, "cls": k,
                      "score": (base_v if base_v is not None else d["pred"])
                      * TRUST.get(k, 0.4)})
            rows.append(d)
    rows.sort(key=lambda r: -r["score"])

    # cross-section of 24h moves; the median divides long from short
    r24s = []
    for r in rows:
        q = pr.get(r["sym"]) or {}
        m = float(q.get("mid") or 0)
        y = float(q.get("yesterday_price") or 0)
        if m > 0 and y > 0:
            r24s.append(m / y - 1)
    r24s.sort()
    xsec_median = r24s[len(r24s) // 2] if r24s else 0.0
    if len(rows) < MIN_ROWS or len(r24s) < MIN_ROWS:
        log(f"표본 부족 (점수 {len(rows)} · 24h {len(r24s)}), 봉인 작성 중단")
        return None

    picks = []
    for r in rows:
        if len(picks) >= SLOTS:
            break
        # gate on what the live book can actually absorb
        try:
            pb, _n = pac_book(cl, r["sym"])
            slip = slip_cost(pb, SIZE, "buy") if pb else None
        except Exception:
            slip = None
        if slip is None or slip > MAX_SLIP:
            continue
        px = float((pr.get(r["sym"]) or {}).get("mid") or 0)
        if px <= 0:
            continue
        u, d_ = (calib(r["cls"], 0.03, x) for x in r["tou"][0.03])
        q = pr.get(r["sym"]) or {}
        y = float(q.get("yesterday_price") or 0)
        if y <= 0:
            continue            # no 24h reference: skip, never guess a side
        r24 = px / y - 1
        # 2026-08-19: the RWA/other class rides the cross-sectional side,
        # every other class the calibrated touch side; both arms are sealed alongside
        # so the ledger keeps comparing them
        xsec_dir = "long" if r24 >= xsec_median else "short"
        touch_dir = _touch_side(r.get("h2"), u, d_)
        use_xsec = XSEC_DIR_ENABLED and (r["cls"] in XSEC_DIR_CLASSES
                                         or r["sym"] in XSEC_DIR_SYMS)
        direction = xsec_dir if use_xsec else touch_dir
        picks.append({
            "sym": r["sym"], "dir": direction, "xsec_dir": xsec_dir,
            "entry": px,
            "exp_move_pct": round(r["pred"] * 100, 2),
            "rank_score": round(r["score"], 5),
            # which window ranked this pick. None means the symbol is too
            # new for the long window and fell back to the 30일 size value.
            "rank_days": r.get("rank_days"),
            "touch_up_pct": round(u * 100, 1),
            "touch_dn_pct": round(d_ * 100, 1),
            # The rates the side was actually read off, beside the 24h ones
            # the row has always shown. Without these a reader sees "up 68 /
            # down 54" next to a short and cannot tell why.
            "h2_up_pct": (None if r.get("h2_up") is None
                          else round(r["h2_up"] * 100, 1)),
            "h2_dn_pct": (None if r.get("h2_dn") is None
                          else round(r["h2_dn"] * 100, 1)),
            "side_source": ("touch2h" if (SIDE_SOURCE == "touch2h"
                                          and r.get("h2") and not use_xsec)
                            else "xsec" if use_xsec else "touch24"),
            "slip_pct": round(slip * 100, 3),
            "basis": (f"{r['cls']} 점수 {r['score']*100:.2f} · "
                      f"24h ±3% 도달 위 {u*100:.0f}%/아래 {d_*100:.0f}%"
                      + ("" if r.get("h2_up") is None else
                         f" · 방향근거 2h ±1% 위 {r['h2_up']*100:.0f}%"
                         f"/아래 {r['h2_dn']*100:.0f}%")),
            "trade_rank": len(picks) + 1,
        })

    # special seats: funding extremes, taking the side that receives it
    got = {p["sym"] for p in picks}
    specials = []
    for q in pr.values():
        sym = q["symbol"]
        if sym in got or sym not in mk:
            continue
        try:
            f = float(q.get("funding") or 0)
        except Exception:
            continue
        apr = abs(f) * 24 * 365
        if apr < SPECIAL_APR:
            continue
        try:
            pb, _n = pac_book(cl, sym)
            slip = slip_cost(pb, SIZE, "buy") if pb else None
        except Exception:
            slip = None
        if slip is None or slip > MAX_SLIP:
            continue
        px = float(q.get("mid") or 0)
        if px <= 0:
            continue
        d = next((r for r in rows if r["sym"] == sym), None)
        if d is None or d["pred"] < SPECIAL_MOVE:
            continue        # not volatile enough, or no bars to know
        specials.append({
            "sym": sym, "dir": "short" if f > 0 else "long", "entry": px,
            "exp_move_pct": round(d["pred"] * 100, 2),
            "rank_score": round(apr, 3), "slip_pct": round(slip * 100, 3),
            "funding_apr_pct": round(apr * 100, 0),
            "basis": (f"특별픽: 펀딩 연 {apr*100:+.0f}% 받는 쪽"
                      f"({'숏' if f > 0 else '롱'}) + 예상변동 "
                      f"{d['pred']*100:.1f}% ≥ 3%"),
            "_apr": apr,
        })
    specials.sort(key=lambda x: -x["_apr"])
    chosen = specials[:SPECIAL]
    for sp in chosen:
        sp.pop("_apr", None)
    # Specials sit at ranks 7 and 8, right behind the six regular seats, the
    # way the engine's eight seats were always meant to be split. Appending
    # them after the bench put them at 11 and 12, where the engine only ever
    # reached them if three earlier picks were skipped, which silently
    # retired the funding seat when SLOTS went from six to ten.
    picks = picks[:SEATS] + chosen + picks[SEATS:]
    for i, p in enumerate(picks, 1):
        p["trade_rank"] = i

    now = datetime.now(KST)
    rec = {"made_at": now.isoformat(),
           "horizon_h": 24,
           # Every pick is filed under this sentence, so it has to describe
           # what actually chose the direction. Cross-sectional ranking
           # was cut back to one class while the sentence kept claiming it
           # for all of them, twice now. Built from the
           # live constants rather than typed out, so it cannot drift again.
           # bracket_trader.latest_seal() matches on "자산군" only, so the
           # rest of the line is record, not behaviour.
           "rule": ("자산군별 점수(예상변동×신뢰도) → 호가 관문(슬리피지) → "
                    + ("2시간 ±1% 도달률 방향" if SIDE_SOURCE == "touch2h"
                       else "24시간 ±3% 도달률 방향")
                    + ((f", 단 {'·'.join(sorted(XSEC_DIR_CLASSES))} 는 "
                        "횡단면 24h 상대순위 방향(중앙값 위 롱·아래 숏)"
                        + (f" (예외 종목: {'·'.join(sorted(XSEC_DIR_SYMS))})"
                           if XSEC_DIR_SYMS else ""))
                       if XSEC_DIR_ENABLED and XSEC_DIR_CLASSES else "")
                    + " → 베이스 모드 변동성 브래킷"),
           "picks": picks, "scores": {}}
    out_dir = out_dir or OUTPUTS_DIR
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"내일예측_{now:%Y-%m-%d_%H%M}.json")
    # atomic write: the trader polls this folder and must never read a half
    # file as the day's seal
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    log(f"봉인 작성: {path}")
    log("  " + btc_line(base))
    for p in picks:
        if "funding_apr_pct" in p:
            log(f"  {p['trade_rank']}. {p['sym']:<9} {p['dir']:<5} "
                f"특별(펀딩 연 {p['funding_apr_pct']:+.0f}% · "
                f"예상 {p['exp_move_pct']}%) · 슬리피지 {p['slip_pct']}%")
        else:
            h2u, h2d = p.get("h2_up_pct"), p.get("h2_dn_pct")
            side_txt = ("" if h2u is None else
                        f" · 방향 2h {h2u}/{h2d}")
            log(f"  {p['trade_rank']}. {p['sym']:<9} {p['dir']:<5} "
                f"예상 {p['exp_move_pct']}% · 24h도달 위{p['touch_up_pct']}%"
                f"/아래{p['touch_dn_pct']}%{side_txt} · "
                f"슬리피지 {p['slip_pct']}%")
    nsp = sum(1 for p in picks if "funding_apr_pct" in p)
    log(f"  일반 {len(picks)-nsp} · 특별(펀딩) {nsp} · "
        f"빈 특별석 {SPECIAL-nsp}")
    return path


def main():
    import argparse
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="", help="seal output directory "
                    "(default: the project outputs folder)")
    a = ap.parse_args()
    make_seal(out_dir=a.out or None)


if __name__ == "__main__":
    main()
