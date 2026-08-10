"""과거 펀딩비 데이터로 전략 시뮬레이션 (백테스트).

Pacifica의 시간당 펀딩 히스토리(오라클 가격 포함)를 받아서
봇과 동일한 진입/청산 규칙을 과거에 적용했으면 어땠는지 계산한다.

실행 예:
  python -m ocean_agent.backtest SOL --mode hedged
  python -m ocean_agent.backtest SOL HYPE --mode directional --entry 0.20
"""

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime

from .api_client import PacificaClient

HOURS_PER_YEAR = 8760


def fetch_funding_history(client: PacificaClient, symbol: str,
                          max_records: int = 4000) -> list[dict]:
    """시간당 펀딩 기록을 과거→현재 순으로 반환."""
    records, cursor = [], None
    while len(records) < max_records:
        params = {"symbol": symbol, "limit": min(4000, max_records - len(records))}
        if cursor:
            params["cursor"] = cursor
        r = client.session.get(f"{client.base}/funding_rate/history",
                               params=params, timeout=20).json()
        if not r.get("success"):
            raise RuntimeError(f"{symbol} 펀딩 히스토리 실패: {r.get('error')}")
        batch = r["data"]
        if not batch:
            break
        records.extend(batch)
        cursor = r.get("next_cursor")
        if not r.get("has_more") or not cursor:
            break
    records.sort(key=lambda x: x["created_at"])
    return records


@dataclass
class Result:
    symbol: str
    mode: str
    hours_total: int = 0
    hours_in_position: int = 0
    round_trips: int = 0
    stop_losses: int = 0
    funding_pnl: float = 0.0   # notional 대비 비율
    price_pnl: float = 0.0
    fee_cost: float = 0.0

    @property
    def net(self) -> float:
        return self.funding_pnl + self.price_pnl - self.fee_cost

    @property
    def days(self) -> float:
        return self.hours_total / 24


def simulate(history: list[dict], mode: str, entry_apr: float, exit_apr: float,
             taker_fee: float, stop_loss_pct: float, symbol: str,
             smooth: int = 1) -> Result:
    """smooth: 진입/청산 판단에 쓸 펀딩 이동평균 시간 수 (1 = 즉시 반응).
    펀딩 수취 자체는 항상 실제 시간당 값으로 계산한다."""
    res = Result(symbol=symbol, mode=mode, hours_total=len(history))
    legs = 4 if mode == "hedged" else 2          # 왕복 주문 다리 수
    round_trip_fee = legs * taker_fee

    in_pos = False
    side = "short"
    entry_price = 0.0
    window: list[float] = []

    for row in history:
        rate = float(row["funding_rate"])
        price = float(row["oracle_price"])
        window.append(rate)
        if len(window) > max(1, smooth):
            window.pop(0)
        apr = (sum(window) / len(window)) * HOURS_PER_YEAR

        if not in_pos:
            signal = apr if mode == "hedged" else abs(apr)
            if signal >= entry_apr:
                in_pos = True
                side = "short" if apr >= 0 else "long"
                if mode == "hedged":
                    side = "short"
                entry_price = price
                res.fee_cost += round_trip_fee / 2   # 진입 다리 수수료
            continue

        # 보유 중: 이 시간의 펀딩 수취 (숏이면 +rate, 롱이면 -rate)
        res.hours_in_position += 1
        res.funding_pnl += rate if side == "short" else -rate

        # 방향성 모드 손절
        if mode == "directional" and entry_price > 0:
            move = (price - entry_price) / entry_price
            adverse = move if side == "short" else -move
            if adverse >= stop_loss_pct:
                res.price_pnl += -adverse
                res.fee_cost += round_trip_fee / 2
                res.round_trips += 1
                res.stop_losses += 1
                in_pos = False
                continue

        # 청산 조건: 수취 기준 APR이 exit 미만
        favorable = apr if side == "short" else -apr
        if favorable < exit_apr:
            if mode == "directional" and entry_price > 0:
                move = (price - entry_price) / entry_price
                res.price_pnl += -move if side == "short" else move
            res.fee_cost += round_trip_fee / 2
            res.round_trips += 1
            in_pos = False

    # 기간 종료 시 보유 중이면 마지막 가격으로 청산 처리
    if in_pos and history:
        price = float(history[-1]["oracle_price"])
        if mode == "directional" and entry_price > 0:
            move = (price - entry_price) / entry_price
            res.price_pnl += -move if side == "short" else move
        res.fee_cost += round_trip_fee / 2
        res.round_trips += 1
    return res


def print_result(r: Result, entry_apr: float, exit_apr: float):
    util = r.hours_in_position / r.hours_total if r.hours_total else 0
    annualized = r.net / r.days * 365 if r.days else 0
    print(f"\n[{r.symbol} · {r.mode}] 기간 {r.days:.0f}일 "
          f"(진입 APR≥{entry_apr:.0%}, 청산<{exit_apr:.0%})")
    print(f"  포지션 보유율     : {util:.1%} ({r.hours_in_position}시간)")
    print(f"  왕복 매매 횟수    : {r.round_trips}회"
          + (f" (손절 {r.stop_losses}회)" if r.mode == "directional" else ""))
    print(f"  펀딩 수익         : {r.funding_pnl:+.3%}")
    if r.mode == "directional":
        print(f"  가격 손익         : {r.price_pnl:+.3%}")
    print(f"  수수료 비용       : {r.fee_cost:-.3%}")
    print(f"  순수익 (기간)     : {r.net:+.3%}")
    print(f"  연환산 순수익     : {annualized:+.2%}")


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("symbols", nargs="*", default=["SOL"])
    p.add_argument("--mode", choices=["hedged", "directional"], default="hedged")
    p.add_argument("--entry", type=float, default=0.20, help="진입 기준 APR")
    p.add_argument("--exit", dest="exit_", type=float, default=0.0, help="청산 기준 APR")
    p.add_argument("--fee", type=float, default=0.0004, help="테이커 수수료")
    p.add_argument("--stop", type=float, default=0.05, help="손절 폭 (방향성)")
    p.add_argument("--smooth", type=int, default=1, help="판단용 펀딩 이동평균(시간)")
    p.add_argument("--url", default="https://api.pacifica.fi", help="메인넷 기본")
    args = p.parse_args()

    client = PacificaClient(args.url)
    symbols = args.symbols or ["SOL"]
    print(f"데이터: {args.url} · 시간당 펀딩 최대 4000개(≈166일)")
    for sym in symbols:
        history = fetch_funding_history(client, sym)
        if not history:
            print(f"[{sym}] 데이터 없음")
            continue
        start = datetime.fromtimestamp(history[0]["created_at"] / 1000)
        print(f"\n{'='*52}\n{sym}: {len(history)}시간 ({start:%Y-%m-%d}부터)")
        r = simulate(history, args.mode, args.entry, args.exit_,
                     args.fee, args.stop, sym, smooth=args.smooth)
        print_result(r, args.entry, args.exit_)


if __name__ == "__main__":
    main()
