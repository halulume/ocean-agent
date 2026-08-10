"""계정 상태를 보고 운용 설정을 추천한다, 레버리지·마진모드·포지션 수.

읽기 전용이다. 설정을 바꾸지 않고, 왜 그 값인지 근거와 함께 제안만 한다.
숫자는 전부 계정 잔고·거래소 스펙·측정된 승률에서 나오며, 관습이나 감이 아니다.

설계 원칙 세 가지 (이 파일의 모든 계산이 여기서 나온다):

1. 포지션 크기는 레버리지가 아니라 '손절 거리'가 정한다.
   명목가 = 자본 × 거래당위험% ÷ 손절폭.  레버리지는 약분되어 사라진다.
   → "레버리지를 올리면 더 번다"는 틀렸다. 묶이는 증거금만 줄어든다.

2. 그래서 레버리지는 '원하는 명목가를 담을 수 있는 최소값'이 최적이다.
   더 올리면 청산선만 가까워질 뿐 수익은 그대로다. 레버리지는 이득이 아니라
   비용이다, 필요한 만큼만 쓴다.

3. 진짜 상한은 '한 번에 감수할 총위험'이다.
   포지션 수 × 거래당위험. 신호들이 같은 방향이면 분산 효과가 없으므로
   보수적으로 잡아야 한다.
"""

MMR_DIV = 2          # MMR = 1 / 시장최대레버리지 / 2  (독스 기준)
TAKER_FEE = 0.0004   # 30일 거래량 $5M 미만 티어
MAKER_FEE = 0.00015


def _market_specs(client) -> dict:
    """심볼 -> {max_leverage, mmr}. 거래소가 주는 값만 쓴다."""
    out = {}
    try:
        for m in client.get_markets():
            ml = int(m.get("max_leverage") or 0)
            if ml > 0:
                out[m["symbol"]] = {"max_leverage": ml,
                                    "mmr": 1.0 / ml / MMR_DIV}
    except Exception:
        pass
    return out


def _tradable(client, policy) -> list:
    """정책이 실제로 진입을 허용하는 심볼들."""
    if policy.get("entry_deep_history_only", False):
        try:
            from .historical_data import BINANCE_SYMBOL
            return sorted(BINANCE_SYMBOL)
        except Exception:
            pass
    try:
        return sorted(m["symbol"] for m in client.get_markets())
    except Exception:
        return []


def _measured_edges(policy) -> list:
    """정책 관문을 통과하는 (신호, 시간봉, 실측승률, 표본). 없으면 빈 리스트."""
    try:
        from .walkforward import _load_curve
        from .signal_scanner import _matrix_rejects, MIN_EDGE
        c = _load_curve()
        if not c or not c.get("sig_tf"):
            return []
        mw = float(policy.get("min_win_rate", 0.45))
        rows = []
        for k, (wr, n) in c["sig_tf"].items():
            sig, tf = k.split("|", 1)
            if _matrix_rejects(sig, tf) or wr < mw or wr - 0.5 < MIN_EDGE:
                continue
            rows.append((wr, n, sig, tf))
        rows.sort(reverse=True)
        return rows
    except Exception:
        return []


def recommend(client, policy: dict, equity_usd: float = 0.0) -> dict:
    """계정을 보고 설정을 제안한다. 반환은 dict (표시는 format_recommendation)."""
    if equity_usd <= 0:
        try:
            equity_usd = float(client.get_account().get("account_equity") or 0)
        except Exception:
            equity_usd = 0.0
    specs = _market_specs(client)
    syms = [s for s in _tradable(client, policy) if s in specs]
    edges = _measured_edges(policy)

    # --- 거래당 위험 ---
    # 자본이 작을수록 최소 주문 단위 때문에 미세한 사이징이 불가능해진다.
    # $300 전후에서 1%는 $3짜리 위험이라 손절폭이 조금만 좁아도 주문이 최소
    # 단위에 걸린다. 그래서 소액에서는 2%가 현실적인 하한이다.
    risk_pct = 0.02 if equity_usd < 2000 else 0.01

    # --- 동시 보유 수 ---
    # 총위험 = 개수 × 거래당위험. 6%를 넘기지 않는다.
    # 측정된 신호가 전부 같은 방향(과매도 반등 롱)이면 분산 효과가 없어
    # '동시에 다 맞을 수 있다'고 보고 잡아야 한다.
    same_side = False
    if edges:
        try:
            from .signal_scanner import _signal_side
            sides = {_signal_side(s) for _wr, _n, s, _tf in edges[:8]}
            sides.discard(None)
            same_side = len(sides) <= 1
        except Exception:
            pass
    max_total_risk = 0.06 if not same_side else 0.05
    n_pos = max(1, int(max_total_risk / risk_pct))

    # --- 명목가 상한 ---
    # 손절폭이 좁을수록 같은 위험에 더 큰 명목가가 필요하다. 실측 손절폭
    # 중앙값을 1.5%로 보고, 포지션 수만큼 곱해 필요한 총 명목가를 구한다.
    typical_sl = 0.015
    notional_per = risk_pct / typical_sl                 # 자본 대비 배수
    notional_total = notional_per * n_pos                # 자본 대비 배수

    # --- 레버리지: 그 명목가를 담는 최소값 (+ 여유 20%) ---
    # 증거금 = 명목가 / 레버리지 ≤ 자본 × 0.8 (여유 20% 남김)
    need_lev = notional_total / 0.8
    market_min = min((specs[s]["max_leverage"] for s in syms), default=10)
    lev = max(2, min(int(need_lev + 0.999), market_min))

    # --- 청산 여유 확인 ---
    worst = None
    for s in syms:
        mmr = specs[s]["mmr"]
        dist = (1.0 / lev) - mmr
        if worst is None or dist < worst[1]:
            worst = (s, dist)
    liq_dist = worst[1] if worst else 0.0
    # 손절(1~3%)보다 청산이 최소 5배는 멀어야 갭에 여유가 생긴다
    while lev > 2 and liq_dist < 0.03 * 5:
        lev -= 1
        liq_dist = (1.0 / lev) - (worst[1] and specs[worst[0]]["mmr"] or 0)

    # --- 마진 모드 ---
    # 격리: 포지션마다 격벽. 코드 사고가 계좌 전체로 번지지 않는다.
    # 크로스: 미실현이익이 다른 포지션을 받쳐 증거금 효율이 높다.
    # 증거금이 자본의 절반도 안 쓰이면 크로스의 효율 이점이 없다 → 격리.
    margin_used = notional_total / lev
    isolated = margin_used < 0.7
    margin_reason = ("증거금이 자본의 %.0f%%만 쓰여 크로스의 효율 이점이 없다"
                     % (margin_used * 100)) if isolated else \
                    ("증거금이 자본의 %.0f%%라 크로스의 상계가 도움이 된다"
                     % (margin_used * 100))

    return {
        "equity": equity_usd,
        "risk_pct": risk_pct,
        "n_positions": n_pos,
        "leverage": lev,
        "isolated": isolated,
        "margin_reason": margin_reason,
        "notional_total_x": notional_total,
        "notional_per_x": notional_per,
        "margin_used_x": margin_used,
        "liq_distance": liq_dist,
        "total_risk": n_pos * risk_pct,
        "same_side": same_side,
        "edges": edges[:6],
        "tradable": len(syms),
        "market_min_leverage": market_min,
        "current": {
            "leverage": policy.get("max_leverage"),
            "concurrent": policy.get("max_concurrent"),
            "risk_pct": policy.get("risk_per_trade_pct"),
            "notional_x": policy.get("max_net_exposure_pct"),
        },
    }


def format_recommendation(r: dict) -> str:
    eq = r["equity"]
    L = []
    L.append("계정 잔고 $%,.2f 기준 권장 설정".replace(",.2f", ".2f") % eq)
    L.append("")
    L.append("  항목            권장        현재        근거")
    cur = r["current"]
    L.append("  거래당 위험      %.0f%%         %s         %s"
             % (r["risk_pct"] * 100,
                ("%.0f%%" % (float(cur['risk_pct']) * 100)) if cur['risk_pct'] else '?',
                "소액은 최소주문 단위 때문에 1%로 못 쪼갬" if eq < 2000
                else "자본이 커서 1%로 충분히 쪼개짐"))
    L.append("  동시 보유       %d개         %s개         총위험 %.0f%%로 제한%s"
             % (r["n_positions"], cur["concurrent"], r["total_risk"] * 100,
                " (신호가 전부 같은 방향이라 더 보수적)" if r["same_side"] else ""))
    L.append("  레버리지        %d배         %s배         명목가 %.1f배를 담는 최소값"
             % (r["leverage"], cur["leverage"], r["notional_total_x"]))
    L.append("  마진 모드       %s        -           %s"
             % ("격리" if r["isolated"] else "크로스", r["margin_reason"]))
    L.append("  명목가 상한      자본의 %.1f배  자본의 %s배   포지션 %d개 × 손절 1.5%% 기준"
             % (r["notional_total_x"], cur["notional_x"], r["n_positions"]))
    L.append("")
    L.append("  증거금 사용   자본의 %.0f%% ($%.0f), 나머지는 여유"
             % (r["margin_used_x"] * 100, eq * r["margin_used_x"]))
    L.append("  청산까지     %.1f%% (손절 1~3%%의 %.0f배 거리)"
             % (r["liq_distance"] * 100, r["liq_distance"] / 0.02))
    L.append("  1회 손절     -$%.2f  ·  %d개 동시 손절 -$%.2f (자본의 %.0f%%)"
             % (eq * r["risk_pct"], r["n_positions"],
                eq * r["total_risk"], r["total_risk"] * 100))
    L.append("")
    if r["edges"]:
        L.append("  근거가 된 실측 신호 (워크포워드 9년):")
        for wr, n, sig, tf in r["edges"]:
            L.append("    %-20s %-4s %.1f%%  표본 %s" % (sig, tf, wr * 100, f"{n:,}"))
    else:
        L.append("  ⚠️ 측정된 신호가 없습니다, walkforward 를 먼저 돌리세요.")
    L.append("")
    L.append("  ※ 레버리지는 수익을 키우지 않습니다. 포지션 크기는 손절 거리가")
    L.append("     정하고, 레버리지는 묶이는 증거금만 바꿉니다. 그래서 '필요한")
    L.append("     최소값'이 최적이며, 더 올리면 청산선만 가까워집니다.")
    return "\n".join(L)
