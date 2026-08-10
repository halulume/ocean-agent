"""메인 루프: 스캔 → 판단 → 실행 → 알림 → 대기.

실행:  python -m ocean_agent.main          (무한 루프)
       python -m ocean_agent.main --once   (1사이클만, 테스트용)
"""

import argparse
import sys
import time
from datetime import datetime

import yaml
from dotenv import load_dotenv

from . import notify, state
from .api_client import PacificaClient, PacificaError
from .position import (close_delta_neutral, close_directional, compute_amount,
                       open_delta_neutral, open_directional)
from .scanner import price_and_funding, scan


def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}")


def check_borrow_interest(client, st: dict) -> None:
    """USDC를 넘겨 쓰면 자동 대출이 시작돼 이자가 붙는다, 감지 시 1회 경고."""
    try:
        acct = client.get_account()
    except Exception:
        return
    pending = float(acct.get("pending_interest") or 0)
    if pending > 0 and not st.get("warned_interest"):
        notify.send(f"⚠️ 자동 대출 이자 발생 중 (미결제 이자 {pending} USDC). "
                    f"포지션이 USDC 잔고를 초과했다는 뜻, allocation_pct를 낮추면 "
                    f"펀딩 수익이 이자로 새는 걸 막을 수 있어요.")
        st["warned_interest"] = True
        state.save(st)
    elif pending == 0 and st.get("warned_interest"):
        st["warned_interest"] = False
        state.save(st)


def available_usd(client: PacificaClient) -> float | None:
    """계정의 가용 USDC. 조회 실패(키 없음 등) 시 None."""
    try:
        acct = client.get_account()
    except Exception:
        return None
    for key in ("available_to_spend", "available_balance", "available", "balance"):
        if acct.get(key) is not None:
            return float(acct[key])
    return None


def run_cycle(cfg: dict, client: PacificaClient) -> None:
    st = state.load()
    ppy = cfg.get("funding_periods_per_year", 8760)
    dry = cfg.get("dry_run", True)
    tag = "[DRY-RUN] " if dry else ""
    slippage = str(cfg.get("slippage_percent", "0.5"))
    builder_code = cfg.get("builder_code", "") or ""
    mode = cfg.get("strategy_mode", "hedged")

    check_borrow_interest(client, st)

    pos = st.get("position")
    if pos:
        # ---- 손절/청산 판단 ----
        mid, apr = price_and_funding(client, pos["symbol"], ppy)
        pos_mode = pos.get("mode", "hedged")
        side = pos.get("side", "short")
        # 수취 관점의 APR: 숏이면 펀딩 그대로, 롱이면 부호 반전
        favorable = apr if side == "short" else -apr
        log(f"보유 중[{pos_mode}/{side}]: {pos['symbol']} {pos['amount']} "
            f"(펀딩 APR {apr:+.1%}, 수취 기준 {favorable:+.1%})")

        # 방향성 모드 손절: 가격이 반대로 stop_loss_pct 이상 가면 펀딩 불문 청산
        entry_price = pos.get("entry_price")
        if pos_mode == "directional" and entry_price and mid > 0:
            move = (mid - float(entry_price)) / float(entry_price)
            adverse = move if side == "short" else -move
            if adverse >= float(cfg.get("stop_loss_pct", 0.05)):
                log(f"손절 발동: 진입가 대비 {adverse:+.1%} 역행")
                if not dry:
                    close_directional(client, pos["symbol"], side,
                                      pos["amount"], slippage, builder_code)
                    st["position"] = None
                    state.save(st)
                notify.send(f"🛑 {tag}손절: {pos['symbol']} {side} {pos['amount']} "
                            f", 가격 {adverse:+.1%} 역행 (펀딩 수익보다 손실 방어 우선)")
                return

        if favorable < cfg["exit_threshold_apr"]:
            flipped = favorable < 0
            reason = "펀딩 방향 반전" if flipped else "펀딩 약화"
            log(f"청산 조건 충족 ({reason}: {favorable:+.1%} < {cfg['exit_threshold_apr']:.1%})")
            if not dry:
                if pos_mode == "hedged":
                    try:
                        close_delta_neutral(client, pos["symbol"],
                                            pos["spot_symbol"], pos["amount"],
                                            slippage, builder_code)
                    except PacificaError as e:
                        naked = getattr(e, "naked", None)
                        if naked:
                            st["naked_exposure"] = naked
                            state.save(st)
                        raise
                else:
                    close_directional(client, pos["symbol"], side,
                                      pos["amount"], slippage, builder_code)
                st["position"] = None
                state.save(st)
            notify.send(f"{tag}포지션 청산: {pos['symbol']} {side} {pos['amount']} "
                        f", {reason} (펀딩 APR {apr:+.1%})")
        return

    # ---- 진입 판단 ----
    candidates = scan(client, ppy, require_spot=(mode == "hedged"))
    if not candidates:
        log("후보 코인이 없음")
        return

    best = candidates[0]
    signal_apr = best.apr if mode == "hedged" else best.abs_apr
    log(f"최고 펀딩: {best.symbol} APR {best.apr:+.1%} → 수취 방향 {best.farm_side} "
        f"(기준 {cfg['entry_threshold_apr']:.1%})")
    if signal_apr < cfg["entry_threshold_apr"]:
        return

    budget = float(cfg["max_notional_usd"])
    avail = available_usd(client)
    if avail is not None:
        budget = min(avail * float(cfg["allocation_pct"]), budget)
    amount = compute_amount(best, budget)
    if amount <= 0:
        log(f"예산 {budget:.2f} USD로는 최소 주문 크기 미달, 스킵")
        return

    if mode == "hedged":
        desc = "스팟 매수 + perp 숏 (델타뉴트럴)"
        side = "short"
    else:
        side = best.farm_side
        desc = f"perp {side} 단독 (방향성 ⚠️)"
    log(f"진입: {best.symbol} {amount} (~{amount * best.mid_price:.2f} USD) {desc}")
    if not dry:
        if mode == "hedged":
            result = open_delta_neutral(client, best, amount, slippage,
                                        builder_code,
                                        taker_fee=float(cfg.get("taker_fee", 0.0004)))
        else:
            result = open_directional(client, best, amount, slippage, builder_code)
        st["position"] = {
            "mode": mode,
            "side": side,
            "symbol": best.symbol,
            "spot_symbol": best.spot_symbol,
            "amount": result.amount,
            "spot_amount": result.spot_amount,
            "notional_usd": result.notional_usd,
            "entry_apr": best.apr,
            "entry_price": best.mid_price,
            "opened_at": datetime.now().isoformat(),
        }
        state.save(st)
    extreme = ("\n⚠️ 시간당 펀딩 1%+, 급변동 중인 코인일 수 있음, 주의"
               if abs(best.funding_hourly) >= 0.01 else "")
    notify.send(f"{tag}진입[{mode}]: {best.symbol} {side} {amount}개 "
                f"(~{amount * best.mid_price:,.2f} USD, 펀딩 APR {best.apr:+.1%})"
                f"{extreme}")


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="1사이클만 실행")
    args = parser.parse_args()

    load_dotenv()
    with open("config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    from . import address_from_env, api_key_from_env
    api_key = api_key_from_env(cfg["base_url"])
    address = address_from_env(cfg["base_url"])
    api_mode = cfg.get("api_mode", "rest")
    if api_mode == "mcp":
        from .mcp_client import PacificaMCPClient
        client = PacificaMCPClient(cfg["base_url"],
                                   address=address, private_key=api_key)
    else:
        client = PacificaClient(cfg["base_url"],
                                address=address, private_key=api_key)

    run_mode = "DRY-RUN" if cfg.get("dry_run", True) else "실거래"
    net = "테스트넷" if "test-api" in cfg["base_url"] else "메인넷"
    log(f"봇 시작, {net} / {run_mode} / 전략={cfg.get('strategy_mode', 'hedged')} "
        f"/ API={api_mode.upper()} / 스캔 주기 {cfg['loop_interval_sec']}초")
    if not cfg.get("dry_run", True) and not api_key:
        log("경고: 실거래 모드인데 API 키가 없습니다. .env의 PACIFICA_API_KEY를 확인하세요.")

    while True:
        try:
            run_cycle(cfg, client)
        except PacificaError as e:
            log(f"API 오류: {e}")
            notify.send(f"⚠️ 봇 API 오류: {e}")
        except Exception as e:
            log(f"예기치 못한 오류: {e}")
            notify.send(f"🚨 봇 오류: {e}")
        if args.once:
            break
        time.sleep(cfg["loop_interval_sec"])


if __name__ == "__main__":
    main()
