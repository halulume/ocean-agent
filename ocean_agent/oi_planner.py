"""OI 파밍 헤지 플래너.

목표: Pacifica에 perp 포지션을 유지해서 OI(미결제약정)를 쌓는 것.
가격 위험은 다른 거래소에 반대 포지션을 열어 상쇄한다 (크로스 거래소 델타뉴트럴).

이 모듈은 Pacifica의 거래소 집계 펀딩비 API를 분석해서
"이 코인은 Pacifica에서 [롱/숏]을 잡고, [어느 거래소]에서 반대 포지션을 잡아라
 — 그러면 순 펀딩 캐리가 연 X%다"를 코인별로 랭킹한다.

원리: 펀딩이 높은 쪽에서 숏(수취), 낮은 쪽에서 롱(지급 최소/수취).
      두 다리는 반대 방향이라 가격 손익은 상쇄되고 펀딩 차이만 남는다.

실행: python -m ocean_agent.oi_planner [--top 15] [--min-carry 0.0]
"""

import argparse
import sys
from dataclasses import dataclass

from .api_client import PacificaClient

HOURS_PER_YEAR = 8760


@dataclass
class HedgePlan:
    symbol: str
    pacifica_side: str        # Pacifica에서 잡을 방향 (OI가 쌓이는 다리)
    pacifica_apr: float       # Pacifica 펀딩 연환산
    hedge_venue: str          # 반대 포지션을 열 거래소
    hedge_side: str
    hedge_apr: float
    net_carry_apr: float      # 순 펀딩 캐리 (양수 = 받으면서 파밍)
    alternatives: list        # (venue, carry) 차선책들


def fetch_aggregated(client: PacificaClient) -> list[dict]:
    """전 코인의 거래소별 펀딩비 (시간당 기준으로 정규화된 값)."""
    return client._get("funding_rate/aggregated")


def plan_hedges(client: PacificaClient) -> list[HedgePlan]:
    plans = []
    for row in fetch_aggregated(client):
        rates = row.get("rates", {})
        if rates.get("pacifica") is None:
            continue
        rate_p = float(rates["pacifica"])
        options = []
        for venue, rate in rates.items():
            if venue == "pacifica" or rate is None:
                continue
            rate_v = float(rate)
            # 펀딩 높은 쪽에서 숏(수취), 낮은 쪽에서 롱 → 캐리 = |차이|
            if rate_p >= rate_v:
                pac_side, hedge_side = "short", "long"
                carry = rate_p - rate_v
            else:
                pac_side, hedge_side = "long", "short"
                carry = rate_v - rate_p
            options.append((carry, venue, pac_side, hedge_side, rate_v))
        if not options:
            continue
        options.sort(reverse=True)
        best = options[0]
        plans.append(HedgePlan(
            symbol=row["symbol"],
            pacifica_side=best[2],
            pacifica_apr=rate_p * HOURS_PER_YEAR,
            hedge_venue=best[1],
            hedge_side=best[3],
            hedge_apr=best[4] * HOURS_PER_YEAR,
            net_carry_apr=best[0] * HOURS_PER_YEAR,
            alternatives=[(v, c * HOURS_PER_YEAR) for c, v, *_ in options[1:4]],
        ))
    plans.sort(key=lambda p: -p.net_carry_apr)
    return plans


def format_plans(plans, top: int = 15, min_carry: float = 0.0) -> str:
    lines = [
        "Pacifica OI 파밍 헤지 플랜 — 순 캐리 높은 순",
        "(Pacifica 다리가 OI를 쌓고, 헤지 다리가 가격 위험을 상쇄)",
        "",
        f"{'심볼':<10}{'Pacifica':>9}{'펀딩APR':>9} | {'헤지 거래소':<12}{'방향':>6}"
        f"{'펀딩APR':>9} | {'순캐리/년':>9}",
        "-" * 74,
    ]
    shown = 0
    for p in plans:
        if p.net_carry_apr < min_carry or shown >= top:
            continue
        shown += 1
        lines.append(
            f"{p.symbol:<10}{p.pacifica_side:>9}{p.pacifica_apr:>9.1%} | "
            f"{p.hedge_venue:<12}{p.hedge_side:>6}{p.hedge_apr:>9.1%} | "
            f"{p.net_carry_apr:>9.1%}")
    if shown == 0:
        lines.append("조건에 맞는 플랜 없음")
    lines.append("")
    lines.append("주의: 순캐리는 현재 시점 펀딩 기준 추정치이며 시시각각 변함.")
    lines.append("      양쪽 수수료(왕복 ~0.1-0.2%)와 두 거래소 증거금이 별도로 필요.")
    return "\n".join(lines)


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--min-carry", type=float, default=0.0)
    p.add_argument("--url", default="https://api.pacifica.fi")
    args = p.parse_args()

    client = PacificaClient(args.url)
    plans = plan_hedges(client)
    print(format_plans(plans, args.top, args.min_carry))


if __name__ == "__main__":
    main()
