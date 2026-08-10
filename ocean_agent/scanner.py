"""펀딩비 스캐너.

두 가지 모드를 지원한다:

- hedged: 스팟 마켓({BASE}-USDC)이 상장된 perp만 후보. 펀딩이 양수일 때
  스팟 매수 + perp 숏으로 펀딩을 수취한다 (델타뉴트럴).
- directional: 전체 perp 대상. 펀딩 절대값이 큰 코인을 골라
  펀딩 양수면 숏, 음수면 롱으로 펀딩을 수취한다 (가격 위험 노출).
"""

from dataclasses import dataclass

from .api_client import PacificaClient


@dataclass
class Candidate:
    symbol: str            # perp 심볼 (예: SOL)
    spot_symbol: str       # 스팟 심볼 (예: SOL-USDC), directional 모드에선 ""
    funding_hourly: float  # 시간당 펀딩비 (부호 있음)
    next_funding: float
    apr: float             # 연환산 펀딩 (부호 있음)
    mid_price: float
    perp_lot_size: float
    spot_lot_size: float   # 스팟 없으면 perp와 동일값
    perp_min_order: float  # USD
    spot_min_order: float

    @property
    def abs_apr(self) -> float:
        return abs(self.apr)

    @property
    def farm_side(self) -> str:
        """펀딩을 수취하는 perp 방향: 양수→short, 음수→long."""
        return "short" if self.funding_hourly >= 0 else "long"


def scan(client: PacificaClient, periods_per_year: int = 8760,
         require_spot: bool = True) -> list[Candidate]:
    """require_spot=True면 스팟 상장 perp만(펀딩 양수 기준 정렬),
    False면 전체 perp를 |펀딩| 기준으로 정렬해 반환."""
    markets = client.get_markets()
    prices = {p["symbol"]: p for p in client.get_prices()}

    perps = {m["symbol"]: m for m in markets if m.get("instrument_type") == "perpetual"}
    spots = {m["base_asset"]: m for m in markets if m.get("instrument_type") == "spot"}

    candidates = []
    for symbol, perp in perps.items():
        spot = spots.get(perp.get("base_asset", symbol))
        if require_spot and not spot:
            continue
        price = prices.get(symbol)
        if not price:
            continue
        mid = price.get("mid") or price.get("mark")
        if not mid:
            continue
        funding = float(price.get("funding") or 0)
        perp_lot = float(perp["lot_size"])
        perp_min = float(perp["min_order_size"])
        candidates.append(Candidate(
            symbol=symbol,
            spot_symbol=spot["symbol"] if spot else "",
            funding_hourly=funding,
            next_funding=float(price.get("next_funding") or 0),
            apr=funding * periods_per_year,
            mid_price=float(mid),
            perp_lot_size=perp_lot,
            spot_lot_size=float(spot["lot_size"]) if spot else perp_lot,
            perp_min_order=perp_min,
            spot_min_order=float(spot["min_order_size"]) if spot else perp_min,
        ))

    if require_spot:
        candidates.sort(key=lambda c: c.apr, reverse=True)
    else:
        candidates.sort(key=lambda c: c.abs_apr, reverse=True)
    return candidates


def funding_apr_for(client: PacificaClient, symbol: str,
                    periods_per_year: int = 8760) -> float:
    """특정 perp 심볼의 현재 펀딩비 연환산 (부호 있음)."""
    return price_and_funding(client, symbol, periods_per_year)[1]


def price_and_funding(client: PacificaClient, symbol: str,
                      periods_per_year: int = 8760) -> tuple[float, float]:
    """(중간가, 펀딩 연환산 APR), 손절 판단과 청산 판단에 함께 쓴다."""
    for p in client.get_prices():
        if p["symbol"] == symbol:
            mid = float(p.get("mid") or p.get("mark") or 0)
            return mid, float(p.get("funding") or 0) * periods_per_year
    raise ValueError(f"심볼을 찾을 수 없음: {symbol}")


def main():
    """단독 실행: 펀딩비 테이블 출력 (키 불필요)."""
    import sys

    import yaml

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    with open("config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    mode = cfg.get("strategy_mode", "hedged")
    require_spot = mode == "hedged"
    if cfg.get("api_mode", "rest") == "mcp":
        from .mcp_client import PacificaMCPClient
        client = PacificaMCPClient(cfg["base_url"])
    else:
        client = PacificaClient(cfg["base_url"])
    candidates = scan(client, cfg.get("funding_periods_per_year", 8760), require_spot)

    print(f"[{cfg['base_url']}] 모드={mode}, 후보 {len(candidates)}개 (상위 15)\n")
    print(f"{'심볼':<12}{'시간당펀딩':>12}{'연환산APR':>10}{'수취방향':>8}{'중간가':>14}")
    print("-" * 60)
    for c in candidates[:15]:
        print(f"{c.symbol:<12}{c.funding_hourly:>12.7f}{c.apr:>9.1%}"
              f"{c.farm_side:>8}{c.mid_price:>14,.4f}")

    entry = cfg.get("entry_threshold_apr", 0.20)
    key = (lambda c: c.apr) if require_spot else (lambda c: c.abs_apr)
    hits = [c for c in candidates if key(c) >= entry]
    print(f"\n진입 기준(|APR| ≥ {entry:.0%}) 충족: "
          + (", ".join(f"{c.symbol}({c.apr:+.0%})" for c in hits[:10]) if hits else "없음"))


if __name__ == "__main__":
    main()
