"""델타뉴트럴 포지션 오픈/클로즈.

오픈: 스팟 매수 → perp 숏 (같은 수량). perp 숏 실패 시 스팟 즉시 되팔아 롤백.
클로즈: perp 숏 청산(reduce_only) → 스팟 매도.
모든 주문에 builder_code가 설정돼 있으면 첨부한다.
"""

import math
import time
from dataclasses import dataclass

from .api_client import PacificaClient, PacificaError
from .scanner import Candidate


@dataclass
class OpenResult:
    symbol: str
    amount: float          # perp 레그 수량 (헤지 모드에선 수수료 보정 후)
    spot_amount: float     # 스팟 매수 주문 수량 (방향성 모드는 0)
    notional_usd: float


def _round_down_to_lot(amount: float, lot_size: float) -> float:
    steps = math.floor(amount / lot_size + 1e-9)
    # lot_size의 소수 자릿수에 맞춰 부동소수점 잔여 오차 제거
    decimals = max(0, -math.floor(math.log10(lot_size) + 1e-9))
    return round(steps * lot_size, decimals)


def _round_to_tick(price: float, tick_size: float) -> str:
    """가격을 틱 단위 배수로 반올림해 문자열로 (아니면 400 거부).
    예: 68.7285, tick 0.01 → '68.73'"""
    steps = round(price / tick_size)
    decimals = max(0, -math.floor(math.log10(tick_size) + 1e-9))
    return f"{steps * tick_size:.{decimals}f}"


def compute_amount(candidate: Candidate, budget_usd: float) -> float:
    """예산으로 살 수 있는 수량을 두 마켓의 lot size 중 큰 쪽에 맞춰 내림."""
    lot = max(candidate.perp_lot_size, candidate.spot_lot_size)
    amount = _round_down_to_lot(budget_usd / candidate.mid_price, lot)
    min_usd = max(candidate.perp_min_order, candidate.spot_min_order)
    if amount * candidate.mid_price < min_usd:
        return 0.0
    return amount


def open_delta_neutral(client: PacificaClient, candidate: Candidate,
                       amount: float, slippage: str, builder_code: str,
                       taker_fee: float = 0.0004) -> OpenResult:
    """스팟 매수 + perp 숏. 두 번째 레그 실패 시 첫 레그 롤백 후 예외를 다시 던진다.

    스팟 매수 수수료는 받는 코인에서 차감되므로(체결량 × (1-fee)),
    perp 숏은 실제 받은 수량에 맞춰 잡아야 정확한 델타뉴트럴이 된다.
    """
    hedge_amount = _round_down_to_lot(amount * (1 - taker_fee),
                                      candidate.perp_lot_size)
    if hedge_amount <= 0:
        raise PacificaError("수수료 보정 후 헤지 수량이 0 — 주문 크기를 키우세요")
    client.create_market_order(candidate.spot_symbol, "bid", str(amount),
                               slippage, builder_code=builder_code)
    try:
        client.create_market_order(candidate.symbol, "ask", str(hedge_amount),
                                   slippage, builder_code=builder_code)
    except PacificaError:
        # perp 숏 실패 → 스팟을 되팔아 델타 노출 제거 (보유량 = 매수량 - 수수료)
        time.sleep(1)
        sell_back = _round_down_to_lot(amount * (1 - taker_fee),
                                       candidate.spot_lot_size)
        client.create_market_order(candidate.spot_symbol, "ask", str(sell_back),
                                   slippage, builder_code=builder_code)
        raise
    return OpenResult(symbol=candidate.symbol, amount=hedge_amount,
                      spot_amount=amount,
                      notional_usd=hedge_amount * candidate.mid_price)


def open_directional(client: PacificaClient, candidate: Candidate,
                     amount: float, slippage: str, builder_code: str) -> OpenResult:
    """펀딩 수취 방향으로 perp 단독 진입 (short='ask', long='bid')."""
    side = "ask" if candidate.farm_side == "short" else "bid"
    client.create_market_order(candidate.symbol, side, str(amount),
                               slippage, builder_code=builder_code)
    return OpenResult(symbol=candidate.symbol, amount=amount, spot_amount=0.0,
                      notional_usd=amount * candidate.mid_price)


def close_directional(client: PacificaClient, symbol: str, farm_side: str,
                      amount: float, slippage: str, builder_code: str) -> None:
    """perp 포지션 청산: short였으면 bid로, long이었으면 ask로 reduce_only."""
    side = "bid" if farm_side == "short" else "ask"
    client.create_market_order(symbol, side, str(amount), slippage,
                               reduce_only=True, builder_code=builder_code)


def close_delta_neutral(client: PacificaClient, symbol: str, spot_symbol: str,
                        amount: float, slippage: str, builder_code: str) -> dict:
    """델타뉴트럴 청산 — perp 숏 청산 후 스팟 매도.

    ⚠️ 비대칭 위험: perp 청산은 성공했는데 스팟 매도가 실패하면 스팟 롱만
    남아 '네이키드 롱 노출'이 생긴다. 조용히 raise하면 그 노출을 아무도
    추적하지 못한다. 그래서 스팟 매도 실패 시 1회 재시도하고, 그래도 안 되면
    크게 알리고 네이키드 상태를 반환한다(호출자가 상태에 기록·수동청산 가능).

    반환: 성공 시 {"ok": True, "naked": None}.
    스팟 매도 최종 실패 시 PacificaError 를 던지며, 예외 객체에
    .naked = {"symbol","side","amount"} 가 붙는다 (호출자가 상태에 기록).
    """
    amt = str(amount)
    # 1) perp 숏 청산
    client.create_market_order(symbol, "bid", amt, slippage,
                               reduce_only=True, builder_code=builder_code)
    time.sleep(1)
    # 2) 스팟 매도 — 실패하면 재시도, 그래도 실패면 네이키드 노출 보고
    try:
        client.create_market_order(spot_symbol, "ask", amt,
                                   slippage, builder_code=builder_code)
        return {"ok": True, "naked": None}
    except PacificaError:
        time.sleep(3)
        try:
            client.create_market_order(spot_symbol, "ask", amt,
                                       slippage, builder_code=builder_code)
            return {"ok": True, "naked": None}
        except PacificaError as e2:
            # perp은 청산됐고 스팟만 남음 = 네이키드 롱. 손실을 막으려면
            # 이 스팟을 즉시 수동 매도해야 한다. 예외에 .naked 를 붙여
            # 호출자가 상태 파일에 구조적으로 기록할 수 있게 한다.
            naked = {"symbol": spot_symbol, "side": "long", "amount": amount}
            err = PacificaError(
                f"🚨 네이키드 노출! {spot_symbol} 롱 {amount}개가 헤지 없이 남음 "
                f"(perp은 청산됨). 즉시 수동 매도 필요. 원인: {e2}")
            err.naked = naked
            raise err from e2
