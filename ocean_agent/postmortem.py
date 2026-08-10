"""사후 원인분석 (Post-Mortem), 자가진화 루프의 심장.

원칙 (사용자 지시 2026-07-22):
  결과가 실제 데이터다. 수익이 나면 '왜 났는지', 손실이 나면 '왜 났는지'를
  과거 데이터로 분석해 기록한다. 그 축적이 다음 판단을 정확하게 만들고,
  계좌 금액을 계속 늘려가는 방향으로 진화시킨다.

무엇을 분석하나 (포지션이 청산될 때마다):
  1. 실제 손익 (얼마 벌었나/잃었나), 이론이 아니라 체결된 결과
  2. 진입 근거였던 신호의 그 시점 매트릭스 EV, 근거가 맞았나
  3. 국면(공포/탐욕), 이 국면에서 이 신호가 통하는가
  4. 시장 방향 기여도, 순수 신호 실력인가 그냥 시장 덕/탓인가
  5. 청산 사유 (익절/손절/시간만료/수동), 어떻게 끝났나

결과는 ~/.ocean_agent_<net>_postmortem.jsonl 에 append (net=testnet/mainnet 분리).
집계는 win_reasons()/loss_reasons() 로 조회 → 리포트·적응이 참조.
"""

import json
import os
import time
from datetime import datetime

from . import data_file

_HOME = os.path.expanduser("~")

def _pm_file() -> str:  # 네트워크별 분리 (테스트넷/메인넷)
    return data_file("postmortem.jsonl")


def _load() -> list[dict]:
    if not os.path.exists(_pm_file()):
        return []
    out = []
    with open(_pm_file(), encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


def _append(rec: dict) -> None:
    with open(_pm_file(), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def analyze_close(rec: dict, exit_price: float, pnl_usd: float,
                  reason: str, regime_now: str = "") -> dict:
    """청산된 거래 하나를 원인분석해 기록.

    rec: st["opened"]의 진입 기록 (symbol, side, signal, entry_price,
         interval, regime, win_rate, n_samples, at)
    exit_price: 실제 청산가
    pnl_usd: 실제 실현 손익 (달러)
    reason: 청산 사유 ('take_profit'|'stop_loss'|'expire'|'manual'|'flip')
    """
    entry = float(rec.get("entry_price") or 0)
    side = rec.get("side", "?")
    move = (exit_price / entry - 1) if entry > 0 else 0.0
    signal_move = move if side == "long" else -move   # +면 신호방향으로 맞음
    won = pnl_usd > 0

    # 진입 근거였던 매트릭스 EV를 지금 다시 조회, 근거가 유효했나
    matrix_ev = None
    try:
        from .rematrix import signal_ev, baseline_ev
        r = signal_ev(rec.get("signal", ""), rec.get("interval", ""))
        if r:
            matrix_ev = r[0]
        base = baseline_ev(rec.get("interval", ""), side)
    except Exception:
        base = None

    # 시장 방향이 도왔나 vs 신호 실력이었나
    #  base(무조건 그 방향 진입 EV)가 양수인데 이겼으면 '시장 덕'이 큼
    attribution = "미상"
    if base is not None:
        if won and base > 0.005:
            attribution = "시장순풍"      # 그냥 그 방향이 유리한 국면이었음
        elif won and base <= 0.005:
            attribution = "신호실력"      # 시장 덕 없이 이김 = 진짜 엣지
        elif not won and base > 0.005:
            attribution = "역행패배"      # 유리한 국면인데도 짐 = 신호가 나빴음
        else:
            attribution = "시장역풍"      # 국면 자체가 불리했음

    rec_out = {
        "ts": int(time.time() * 1000),
        "symbol": rec.get("symbol"),
        "signal": rec.get("signal"),
        "interval": rec.get("interval"),
        "side": side,
        "entry_regime": rec.get("regime"),
        "exit_regime": regime_now,
        "entry_price": entry,
        "exit_price": exit_price,
        "signal_move_pct": round(signal_move * 100, 3),
        "pnl_usd": round(pnl_usd, 2),
        "won": won,
        "reason": reason,
        "entry_winrate": rec.get("win_rate"),
        "matrix_ev_now": matrix_ev,
        "baseline_ev": base,
        "attribution": attribution,
        "held_hours": _held_hours(rec.get("at")),
    }
    # 예측봉 대조 결과 (있으면): 진입 때 그린 경로 체크포인트의 적중/오차.
    # 방향(won)만이 아니라 '그린 경로대로 갔나'를 학습 데이터로 남긴다.
    if rec.get("pred"):
        rec_out["pred_target_move"] = rec["pred"].get("target_move")
    if rec.get("pred_check"):
        rec_out["pred_check"] = rec["pred_check"]
    _append(rec_out)
    return rec_out


def _held_hours(at_iso) -> float:
    try:
        return round((datetime.now()
                      - datetime.fromisoformat(at_iso)).total_seconds() / 3600, 1)
    except (ValueError, TypeError):
        return 0.0


def summarize(days: int = 30) -> dict:
    """최근 N일 원인분석 집계, 리포트·적응이 참조하는 요약."""
    cutoff = time.time() * 1000 - days * 86_400_000
    rows = [r for r in _load() if r.get("ts", 0) >= cutoff]
    if not rows:
        return {"n": 0}
    wins = [r for r in rows if r.get("won")]
    losses = [r for r in rows if not r.get("won")]
    total_pnl = sum(float(r.get("pnl_usd") or 0) for r in rows)

    def _group(items, key):
        g = {}
        for r in items:
            k = r.get(key) or "?"
            g[k] = g.get(k, 0) + 1
        return dict(sorted(g.items(), key=lambda kv: -kv[1]))

    return {
        "n": len(rows),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(rows),
        "total_pnl": round(total_pnl, 2),
        # 수익이 왜 났나 / 손실이 왜 났나 (신호별·귀인별)
        "win_by_signal": _group(wins, "signal"),
        "loss_by_signal": _group(losses, "signal"),
        "win_by_attribution": _group(wins, "attribution"),
        "loss_by_attribution": _group(losses, "attribution"),
        "loss_by_reason": _group(losses, "reason"),
        # 이 국면에서 뭐가 통했나
        "win_by_regime": _group(wins, "entry_regime"),
        "loss_by_regime": _group(losses, "entry_regime"),
    }


def signal_verdict(signal: str, interval: str = "") -> dict | None:
    """특정 신호의 실전 성적표, 몇 번 이기고 졌나, 순수 실력이었나."""
    rows = [r for r in _load()
            if r.get("signal") == signal
            and (not interval or r.get("interval") == interval)]
    if not rows:
        return None
    wins = [r for r in rows if r.get("won")]
    real_edge = [r for r in wins if r.get("attribution") == "신호실력"]
    return {
        "signal": signal,
        "n": len(rows),
        "win_rate": len(wins) / len(rows),
        "total_pnl": round(sum(float(r.get("pnl_usd") or 0) for r in rows), 2),
        "real_edge_wins": len(real_edge),   # 시장 덕 없이 이긴 횟수
        "market_luck_wins": len(wins) - len(real_edge),
    }
