"""자율 거래 개체 (Autonomous Trading Entity).

policy.yaml이라는 '위임장'을 받아, 그 울타리 안에서 스스로:
  인지(시장·셋업) → 정책 필터 → 진입/청산(TP/SL 자동) → 킬스위치 → 기록 → 보고
를 반복한다. 사용자는 성과 보고만 받는다.

핵심 원칙 (프로젝트 전체 교훈의 집약):
- 수익 목표를 명령으로 받지 않는다. 검증된 엣지만, 리스크 한도 안에서 운용한다.
- 완전 자율은 '정책 위임'이다. 정책 밖 행동은 구조적으로 불가능.
- 멈추지 않고 '적응'한다: 지는 신호를 스스로 찾아 정지시키고, 연패 시 사이즈를
  줄이며, 왜 졌는지 분석해 정책을 조정한다. 24시간 계속 돌면서 스스로 개선.
- 단 하나의 최후 방어선: 자본이 치명적으로 줄면(기본 -50%) 완전 정지(수동 재개).

실행:
  python -m ocean_agent.autonomous --once     # 1사이클(검증용)
  python -m ocean_agent.autonomous            # 상시 루프
  python -m ocean_agent.autonomous --report   # 현재 성과만 출력
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import yaml

from . import (address_from_env, agent_name, api_key_from_env, data_file,
               migrate_brand_rename, migrate_legacy_data, notify)
from .api_client import PacificaClient, PacificaError
from .signal_scanner import evaluate_setups

# 라이브 파일은 네트워크별로 분리(테스트넷/메인넷), data_file()이 접두어를 붙인다.
def _state_file() -> str:
    return data_file("autonomous.json")

def _equity_file() -> str:
    return data_file("equity.jsonl")

def _log_file() -> str:
    return data_file("bot.log")


def record_equity(eq: float) -> None:
    """사이클마다 자본을 기록, 성과 추적과 수익 곡선 그래프의 재료."""
    if eq <= 0:
        return
    try:
        with open(_equity_file(), "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": int(time.time() * 1000),
                                "eq": round(eq, 2)}) + "\n")
    except OSError:
        pass


def keep_awake_on():
    """봇이 도는 동안 Windows 절전/최대절전을 막는다 (화면은 꺼져도 됨).
    ES_CONTINUOUS | ES_SYSTEM_REQUIRED. 봇 종료 시 자동 해제.
    다른 OS에선 조용히 통과."""
    try:
        import ctypes
        ES_CONTINUOUS = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        return True
    except Exception:
        return False


def keep_awake_off():
    """절전 방지 해제 (원래 전원 정책으로 복귀)."""
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
    except Exception:
        pass


def log(msg: str) -> None:
    line = f"[{datetime.now():%m-%d %H:%M:%S}] {msg}"
    # flush 필수, 콘솔이 아닌 곳(파이프·리다이렉트)으로 나가면 버퍼에 갇혀
    # 화면에 아무것도 안 뜬다. 사용자가 .bat 을 켰는데 빈 창만 보이는 원인이었다.
    print(line, flush=True)
    # 파일에도 남긴다, 일일 브리핑 루틴이 에러·경고를 스스로 읽고 진단하도록.
    # 최근 것만 유지(대략 5000줄 넘으면 뒤쪽 절반만 보존)해 무한 증가 방지.
    log_path = _log_file()
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        if os.path.getsize(log_path) > 2_000_000:   # ~2MB 넘으면 절반 트림
            with open(log_path, encoding="utf-8") as f:
                lines = f.readlines()
            with open(log_path, "w", encoding="utf-8") as f:
                f.writelines(lines[len(lines) // 2:])
    except OSError:
        pass


def load_state() -> dict:
    path = _state_file()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"halted": False, "day": "", "day_start_equity": None,
            "peak_equity": None, "cycles": 0, "opened": [], "log": []}


def save_state(st: dict) -> None:
    path = _state_file()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def exchange_equity(client: PacificaClient) -> float:
    """Exchange account equity only, no Print face value.

    This is the basis for position sizing and the fatal/soft defense lines
    (review M5): Print deposits are carried at face value and can already be
    partly lost before settlement, so counting them would inflate sizing and
    hide Print losses from the stops until settlement. User-facing profit
    reporting keeps using equity() (balance + Print) below."""
    try:
        return float(client.get_account().get("account_equity") or 0)
    except Exception:
        return 0.0


def _live_print_face(client: PacificaClient):
    """Sum of face-value deposits of Print games still running.

    Returns None when the lookup fails (private API), so callers can tell
    "no Print money" apart from "could not check" and skip baseline
    adjustments instead of applying a phantom change."""
    try:
        total = 0.0
        for a in client.print_positions().get("game_accounts", []):
            if not a.get("game_ended_at_ms"):
                total += float(a.get("initial_deposit") or 0)
        return total
    except Exception:
        return None


def equity(client: PacificaClient) -> float:
    """Total capital = exchange equity + live Print deposits at face value.

    Reporting only: the user judges results by balance + Print combined, so
    reports and the equity curve keep using this. Sizing and defense lines
    use exchange_equity() instead (review M5)."""
    try:
        eq = float(client.get_account().get("account_equity") or 0)
    except Exception:
        return 0.0
    face = _live_print_face(client)
    # Print lookup failure -> exchange equity only (conservative fallback).
    return eq + (face or 0.0)


def _deep_history_symbols(policy: dict):
    """Symbols allowed to be ENTERED when policy.entry_deep_history_only is on.

    Observation is deliberately NOT restricted, the bot keeps watching and
    grading every market, because collecting data is the point. This gates only
    where real money goes.

    "Deep" means the symbol has a Binance spot mapping, so its backtest can reach
    back to 2017 across bull, bear and chop. Everything else is judged on the
    ~1500 Pacifica bars available (62 days on 1h), which is one market mood.
    (2026-08-04: gold and silver were entered at a claimed 77%/68% win rate built
    on that shallow window alone; neither has any long history at all.)"""
    if not policy.get("entry_deep_history_only", False):
        return None
    try:
        from .historical_data import BINANCE_SYMBOL
        return set(BINANCE_SYMBOL)
    except Exception:
        return None      # cannot tell -> do not block anything


def _fixed_capital(policy: dict):
    """policy.capital_usd 가 고정 숫자면 그 값(float)을, 'live'/'auto'/0/미설정이면
    None 을 돌려준다. None 이면 사이징을 실계좌 잔고에 연동한다는 뜻."""
    raw = policy.get("capital_usd", "live")
    if isinstance(raw, str):
        if raw.strip().lower() in ("live", "auto", "wallet", ""):
            return None
        try:
            raw = float(raw)
        except ValueError:
            return None
    val = float(raw or 0)
    return val if val > 0 else None


def operating_capital(policy: dict, eq: float) -> float:
    """사이징 기준 자본. capital_usd 가 고정 숫자면 그 값, 아니면 실계좌 잔고(eq).

    메인넷은 policy 에 capital_usd: live 로 두어 지갑 USDC 잔고를 그대로 따라가게
    한다, 입금/출금·수익에 맞춰 포지션 크기가 자동으로 커지고 줄어든다.
    고정 숫자(예: 100)면 '그만큼만 굴린다', 지갑에 더 있어도 $100 기준으로만
    사이징한다. 단 지갑 잔고보다 크게는 잡지 않는다(min).
    (치명적 손실 방어선은 이 값이 아니라 '시작 잔고 + 배정액' 기준을 쓴다 ,
    낙폭이 항상 0이 되어 방어선이 무력화되는 것을 막기 위해.)"""
    fx = _fixed_capital(policy)
    if fx is None:
        return max(float(eq or 0), 0.0)
    return min(fx, max(float(eq or 0), 0.0))   # 지갑보다 크게 잡지 않음


# Absolute outputs dir anchored to the package location, shared with the
# bracket bot. The cross-bot guards used to glob a cwd-relative "outputs"
# and silently found nothing when either process started from another
# folder, voiding both guards at once. (review N1)
_PKG_OUTPUTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outputs")


def _bracket_owned_syms() -> set:
    """Symbols the bracket bot currently holds, per its own state files.

    Reads bracket_state*.json (base and hard modes) under the package's
    outputs dir. Unreadable files are skipped: better to close one position
    too many during a real emergency than to crash the defense line on a
    corrupt ledger."""
    import glob as _glob
    syms: set = set()
    for path in _glob.glob(os.path.join(_PKG_OUTPUTS, "bracket_state*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                syms |= set((json.load(f).get("positions") or {}).keys())
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
    return syms


def close_all(client: PacificaClient, builder: str, reason: str,
              dry: bool = False, failed: list | None = None) -> int:
    """모든 perp 포지션을 시장가 reduce_only로 청산. 청산 건수 반환.

    dry=True 면 주문을 보내지 않고 대상만 센다. --dry 는 '판단만 하고 주문은
    내지 않는다'는 약속이라, 방어선 발동 같은 비상 경로도 예외가 아니다
    (2026-08-10 검토 B1).

    failed: if given, symbols whose close order failed are appended, so the
    caller can tell whether positions remain open (review H8).

    Positions owned by the bracket bot (listed in its own state files) are
    never touched: closing them here would realize their losses at the worst
    moment while that bot grades the wound as its own and re-enters (review
    H5)."""
    n = 0
    bracket_syms = _bracket_owned_syms()
    for p in client.get_positions():
        try:
            if p.get("symbol") in bracket_syms:
                log(f"청산 건너뜀 {p.get('symbol')}: 브래킷 봇 소유 포지션 "
                    f"(bracket_state 장부에 있음)")
                continue
            opp = "ask" if p.get("side") == "bid" else "bid"
            if dry:
                log(f"[DRY] 청산 생략: {p['symbol']} {p.get('amount')}")
                n += 1
                continue
            client.create_market_order(p["symbol"], opp, str(p["amount"]),
                                       "0.5", reduce_only=True,
                                       builder_code=builder)
            n += 1
        except PacificaError as e:
            log(f"청산 실패 {p.get('symbol')}: {e}")
            if failed is not None:
                failed.append(str(p.get("symbol") or "?"))
    if n:
        log(f"전량 청산 {n}건 ({reason})")
    return n


def _reconcile_balance_change(client: PacificaClient, st: dict,
                              eq: float) -> bool:
    """Shift the stop baselines by whatever the user deposited or withdrew, so the
    fatal/soft stops measure *trading* P&L only.

    Reads the exchange's own transfer ledger (`account/balance/history`,
    event_type deposit/withdraw) instead of guessing from equity movement.
    Guessing forced a choice between two failures: infer a transfer from any
    equity jump and unrealized PnL gets mistaken for a deposit (masking a real
    loss), or only reconcile when flat and a deposit made while holding positions
    is missed entirely, which silently disables the kill switch, because
    `start_equity` stays low and the drawdown never reaches the threshold.
    The ledger has no such ambiguity and works with positions open.

    Withdrawals carry a negative `amount`, so the net sum shifts the baselines in
    the right direction either way.
    (2026-08-04: a $50 withdrawal dropped the account to ~$3; the fatal stop read
    it as a -94% loss and jammed the bot. Withdrawal != loss.)"""
    st["last_equity"] = eq
    try:
        rows = client._get("account/balance/history",
                           {"account": client.address})
        rows = rows if isinstance(rows, list) else (rows.get("data") or [])
    except Exception:
        return False                 # 조회 실패, 이번 사이클은 그냥 넘어간다
    xfers = [r for r in rows
             if r.get("event_type") in ("deposit", "withdraw")]
    if not xfers:
        return False
    newest = max(float(r.get("created_at") or 0) for r in xfers)
    seen = st.get("last_transfer_ms")
    if seen is None:
        # 첫 실행: 과거 입출금 전체를 소급 적용하면 기준이 엉뚱해진다.
        # 지금까지의 이력은 이미 현재 잔고에 반영돼 있으므로 표시만 남긴다.
        st["last_transfer_ms"] = newest
        return False
    fresh = [r for r in xfers if float(r.get("created_at") or 0) > float(seen)]
    if not fresh:
        return False
    st["last_transfer_ms"] = newest
    net = sum(float(r.get("amount") or 0) for r in fresh)
    if abs(net) < 0.01:
        return False
    for k in ("start_equity", "day_start_equity", "peak_equity"):
        v = st.get(k)
        if v is not None:
            st[k] = float(v) + net
    kind = "입금" if net > 0 else "출금"
    base = float(st.get("start_equity") or eq)
    fatal = 0.20
    log(f"{kind} ${abs(net):,.2f} 감지({len(fresh)}건), 방어선 기준 재조정. "
        f"새 시작기준 ${base:,.2f} · 완전정지 ${base * (1 - fatal):,.2f}")
    try:
        notify.send(f"💱 {kind} ${abs(net):,.2f} 감지, 방어선 기준을 "
                    f"${base:,.2f}로 재조정했습니다. 손익이 아니라 자금 이동입니다.")
    except Exception:
        pass
    return True


def final_stop_check(client: PacificaClient, policy: dict, st: dict,
                     dry: bool = False) -> bool:
    """최후 방어선만 판정. True면 거래 금지.
    킬스위치(멈춤) 대신 적응(review_and_adapt)이 주 방어이고,
    여기서는 '적응이 실패했다는 증거'인 치명적 손실만 잡는다."""
    # Defense lines run on exchange equity only (review M5): Print deposits
    # sit at face value and their losses only surface at settlement.
    eq = exchange_equity(client)
    # Guard against transient/partial balance reads. You cannot lose most of the
    # account in one cycle (fatal stop is -20%, every position carries native
    # TP/SL), so a value implausibly far below the running peak is almost always
    # a bad API read, not a real loss. Re-read once and keep the higher; only a
    # *confirmed* low then flows into the drawdown checks. Prevents a glitchy read
    # from false-triggering the fatal stop (2026-08-04: read $3 vs a $338 account,
    # which set halted=True and jammed the bot).
    _peak = st.get("peak_equity") or 0
    if _peak > 20 and 0 < eq < _peak * 0.5:
        import time as _t
        _t.sleep(1)
        eq = max(eq, exchange_equity(client))
    if eq <= 0:
        log("잔고 조회 실패(일시적), 이번 사이클 쉼")
        return True
    # Bracket-owned positions are excluded from the drawdown basis: close_all
    # deliberately skips them (review H5), so their unrealized pnl must not
    # be able to trigger a liquidation of THIS bot's own book while the
    # actual cause stays open (review N7). Unrealized pnl is computed from
    # entry vs mark, the same way aftercare does. If any read fails the raw
    # equity stands, which can only fire the stop earlier, never later.
    _bsyms = _bracket_owned_syms()
    if _bsyms:
        _bpnl = 0.0
        try:
            _prices = {p["symbol"]: p for p in client.get_prices()}
            for _p in client.get_positions():
                _s = _p.get("symbol")
                if _s not in _bsyms:
                    continue
                _amt = abs(float(_p.get("amount") or 0))
                _ent = float(_p.get("entry_price") or 0)
                _mk = float(_prices.get(_s, {}).get("mark")
                            or _prices.get(_s, {}).get("mid") or 0)
                if _amt > 0 and _ent > 0 and _mk > 0:
                    _bpnl += (_mk - _ent) * _amt \
                        * (1 if _p.get("side") == "bid" else -1)
        except (PacificaError, TypeError, ValueError, KeyError):
            _bpnl = 0.0
        if abs(_bpnl) >= 0.01:
            eq -= _bpnl
            log(f"방어선 판정: 브래킷 소유 포지션 미실현 손익 ${_bpnl:+,.2f} "
                f"제외 (판정 자본 ${eq:,.2f})")
    # Deposits/withdrawals are not trading results, shift the baselines before
    # any drawdown is judged (flat-only, see the helper).
    _xfer = _reconcile_balance_change(client, st, eq)
    # Print deposits/settlements move money between the exchange account and
    # the Print game without touching the deposit/withdraw ledger. Since the
    # defense lines now run on exchange equity only (review M5), shift the
    # baselines by the change in live Print face value; otherwise a Print
    # deposit would read as a trading loss and could false-trigger the stops.
    # At settlement only the Print P&L (payout minus face) flows into the
    # drawdown, which is exactly when the loss becomes real.
    # First run after the basis change (no stored face): stored baselines were
    # built with Print face included, so subtracting the full current face
    # converts them to the exchange-only basis once.
    _face = _live_print_face(client)
    if _face is not None:
        _delta = _face - float(st.get("print_face_live") or 0)
        if abs(_delta) >= 0.01:
            _shifted = False
            for _k in ("start_equity", "day_start_equity", "peak_equity"):
                _v = st.get(_k)
                if _v is not None:
                    st[_k] = float(_v) - _delta
                    _shifted = True
            if _shifted:
                log(f"Print 예치 변화 ${_delta:+,.2f} 반영, 방어선 기준 조정 "
                    f"(현재 Print 액면 ${_face:,.2f})")
        st["print_face_live"] = _face
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if st.get("day") != today:
        # 날짜가 바뀌면 전날 결산을 먼저 보내고 새 기준을 세운다
        prev_day = st.get("day")
        day0 = float(st.get("day_start_equity") or 0)
        if prev_day and day0 > 0:
            opened_today = [o for o in st.get("opened", [])
                            if str(o.get("at", "")).startswith(prev_day)]
            notify.send(
                f"📅 일일 결산 {prev_day}\n"
                f"  자본 {day0:,.0f} → {eq:,.0f} ({eq/day0-1:+.2%})\n"
                f"  신규 진입 {len(opened_today)}건 · 누적 사이클 {st.get('cycles',0)}"
                + (f"\n  자가정지 신호: {', '.join(st['banned_signals'])}"
                   if st.get("banned_signals") else ""))
        st["day"] = today
        st["day_start_equity"] = eq
    if st.get("peak_equity") is None or eq > st["peak_equity"]:
        st["peak_equity"] = eq
    if st.get("halted"):
        return True

    # 최후 방어선: '맡긴 돈(배정액)' 대비 치명적 손실 → 완전 정지 (수동 재개)
    # 낙폭을 절대 금액으로 잰다: (시작 잔고 − 현재) ≥ 배정액 × fatal.
    # 이래야 capital_usd: 100 이 큰 지갑의 일부여도 '$100의 20%=$20'에서 걸린다.
    # (지갑 전체의 20%가 아니라 맡긴 돈의 20%. live면 배정액=계좌 전체라 동일.)
    if st.get("start_equity") is None and eq > 0:
        st["start_equity"] = eq
    start_eq = float(st.get("start_equity") or eq)
    # 래칫 재앵커 (2026-08-07 사용자 승인): 자본이 앵커를 넘어서면 앵커를 따라
    # 올린다, 방어선이 '시작 대비 -20%'가 아니라 '고점 대비 -20%'가 되어
    # 번 수익도 보호한다. 앵커는 올라가기만 하고 내려가지 않는다 (입출금은
    # 위의 원장 재앵커가 별도 처리).
    # 글리치 방어: 한 번에 +10% 넘는 점프는 1초 뒤 재읽기해 낮은 쪽을 쓴다,
    # 일시적 과대 응답으로 앵커가 튀면 이후 정상 자본이 낙폭으로 오인된다.
    if not _xfer and eq > start_eq > 0:
        new_anchor = eq
        if eq > start_eq * 1.10:
            try:
                import time as _t2
                _t2.sleep(1)
                re_eq = exchange_equity(client)   # same basis as eq (M5)
                new_anchor = min(eq, re_eq) if re_eq > 0 else start_eq
            except Exception:
                new_anchor = start_eq      # 확인 못 하면 올리지 않는다
        if new_anchor > start_eq:
            st["start_equity"] = new_anchor
            start_eq = float(new_anchor)
    if _xfer:
        # 자금 이동을 방금 반영했다. 거래소 원장은 즉시 바뀌지만 잔고 조회는
        # 한 박자 늦게 따라오므로, 이 사이클에 낙폭을 재면 입출금이 손실로
        # 보인다. 입금은 즉시, 출금은 다음 사이클에 전량청산+영구정지로
        # 이어지는 것을 재현 확인했다 (검토 B7, 08-04 사고와 같은 경로).
        # 한 사이클 쉬면 다음 조회에서 잔고와 기준이 함께 맞는다.
        log("자금 이동 반영 직후, 이번 사이클은 낙폭 판정을 건너뜁니다.")
        return False
    fixed = _fixed_capital(policy)
    allocation = min(fixed, start_eq) if fixed is not None else start_eq
    fatal = float(policy.get("fatal_loss_pct", 0.20))
    drawdown_usd = start_eq - eq                    # 양수면 손실
    if allocation > 0 and drawdown_usd >= fatal * allocation:
        dd_pct = drawdown_usd / allocation
        failed_closes: list = []
        close_all(client, policy.get("builder_code", ""), "치명적 손실 방어선",
                  dry=dry, failed=failed_closes)
        if dry:
            # 드라이런은 판단만 남긴다. 알림·정지 래치까지 실행하면 실운영
            # 상태 파일이 오염된다 (dry/live가 같은 파일을 쓴다).
            log(f"[DRY] 방어선 발동 판정: 배정액 대비 {-dd_pct:.1%} "
                f"(실제 청산·정지 없음)")
            return True
        notify.send(f"🚨 {agent_name()} 완전 정지: 배정액 대비 {-dd_pct:.1%} "
                    f"(손실 ${drawdown_usd:,.0f} / 배정 ${allocation:,.0f}, "
                    f"방어선 -{fatal:.0%}). 전량 청산·정지. "
                    f"원인 검토 후 --resume 로 재개하세요.")
        # Halt regardless (safety latch), but if some closes failed the
        # operator must know positions are still open (review H8).
        if failed_closes:
            notify.send(f"⚠️ 방어선 청산 일부 실패: {', '.join(failed_closes)} "
                        f"포지션이 아직 열려 있습니다. 거래소에서 직접 확인 후 "
                        f"수동 청산하세요.")
        st["halted"] = True
        return True

    # 소프트 정지: 당일 '배정액' 대비 -8% → 그날은 신규 진입만 멈춤(보유는 계속
    # 관리). 완전정지(-20%)까지 안 가도, 나쁜 날 손실을 하루 단위로 끊는다.
    # 방어선과 같은 원리, 당일 손실을 절대금액으로 재서 배정액과 비교한다.
    soft = float(policy.get("soft_halt_pct", 0.08))
    day0 = float(st.get("day_start_equity") or eq)
    soft_ref = min(fixed, day0) if fixed is not None else day0
    day_loss_usd = day0 - eq
    if soft > 0 and soft_ref > 0 and day_loss_usd >= soft * soft_ref:
        if st.get("soft_halt_day") != today:
            st["soft_halt_day"] = today
            sd_pct = day_loss_usd / soft_ref
            notify.send(f"⏸️ 소프트 정지: 오늘 배정액 대비 {-sd_pct:.1%} "
                        f"(손실 ${day_loss_usd:,.0f} / 배정 ${soft_ref:,.0f}, "
                        f"기준 -{soft:.0%}). 오늘 신규 진입 중단, 보유는 계속 관리.")
            log(f"소프트 정지 발동, 당일 배정액 대비 {-sd_pct:.1%}, 신규 진입 중단")
    return False


def is_soft_halted(st: dict) -> bool:
    """오늘 소프트 정지 상태인가 (신규 진입만 막고 보유는 관리)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return st.get("soft_halt_day") == today


def _hz_ms(interval: str) -> int:
    from .indicators import INTERVAL_MS
    return INTERVAL_MS.get(interval, 3_600_000) * 24  # FWD=24


def review_and_adapt(client: PacificaClient, policy: dict, st: dict) -> None:
    """자가진단·적응, 청산된 거래를 채점해서 스스로 전략을 조정한다.

    1) 만기 지난 진입들을 실제 결과로 채점 (신호별 승률 집계)
    2) 표본이 쌓인 신호 중 실제 승률이 낮으면 → 그 신호 '정지 목록'에 추가
    3) 최근 연패면 → 사이즈 배율 축소, 회복하면 원상복구
    4) 무엇을 왜 바꿨는지 보고
    """
    opened = st.get("opened", [])
    now = datetime.now()
    changed = []

    # --- 1) 청산 감지 + 원인분석 채점 ---
    # 진입 기록에 있던 포지션이 실제 보유목록에서 사라졌으면 = 청산된 것.
    # 청산될 때마다 '왜 이겼나/졌나'를 과거 데이터로 분석해 기록한다.
    graded = st.setdefault("graded", {})   # signal@tf -> {win, total, pnl}
    try:
        live_syms = {p["symbol"] for p in client.get_positions()}
        pr = {p["symbol"]: p for p in client.get_prices()}
    except Exception:
        return   # 조회 실패, 이번 사이클 채점 건너뜀
    # 실제 청산 손익, 거래소가 심볼별 realized pnl과 청산 사유를 준다.
    # 방향 추측이 아니라 실제 지갑 손익으로 채점해야 자가진화 입력이 정직하다.
    # symbol -> [(청산시각ms, pnl, 가격, 사유), ...]
    # ⚠️ 심볼별로 '합계'를 미리 내면 안 된다. 예전 코드는 그 심볼의 평생 청산
    #    손익을 다 더해 이번 거래의 성적으로 썼고, 승패까지 그 값으로 판정했다.
    #    실제로 2026-08-05에 BTC를 +0.21에 이겼는데 7월부터 쌓인 손실 때문에
    #    '패 -7.30'으로 기록됐다, 이긴 신호가 진 것으로 학습되는 상태였다.
    #    진입 시각 이후의 청산만 세도록 시각을 보존한다.
    close_events = {}
    try:
        for h in client._get("positions/history", {"account": client.address}):
            cause = h.get("cause") or ""
            # ⚠️ 봇 매매가 아닌 체결은 학습에서 제외, 오염 방지.
            #  game_settlement(Print 정산)은 봇이 낸 게 아니라 Print 상품 체결이라,
            #  이걸 봇 신호 성적에 넣으면 좋은 신호가 억울하게 죽는다(07-24 교훈).
            #  봇 청산(close_*)과 거래소 TP/SL/청산만 신호 채점에 반영한다.
            if cause == "game_settlement" or cause.startswith("game"):
                continue
            if str(h.get("side", "")).startswith("close") or \
                    cause in ("take_profit", "stop_loss", "liquidation"):
                close_events.setdefault(h.get("symbol"), []).append(
                    (int(h.get("created_at") or 0), float(h.get("pnl") or 0),
                     float(h.get("price") or 0), cause))
    except Exception:
        pass   # 이력 조회 실패 시 방향 추측으로 폴백

    def _closes_since(sym: str, since_ms: int):
        """이 심볼에서 since_ms 이후에 일어난 청산만 합산. 없으면 None."""
        evs = [e for e in close_events.get(sym, []) if e[0] >= since_ms]
        if not evs:
            return None
        evs.sort()
        return {"pnl": sum(e[1] for e in evs),
                "exit": evs[-1][2] or 0.0,
                "cause": evs[-1][3] or ""}
    regime_now = read_regime(client).get("label", "")
    still_open = []
    for rec in opened:
        try:
            sym = rec.get("symbol")
            sig = rec.get("signal", "")
            # ★ 자가개선 화이트리스트: '시그널 매매'만 학습한다 (사용자 지시 2026-07-27).
            #   지표 시그널로 진입한 것(RSI/볼린저/프랙탈 등)만 승률·메타학습에 반영.
            #   펀딩 캐리(방향중립·신호아님)·정체불명 진입은 라이브 메타데이터가 아니므로
            #   기록만 유지하고 채점에서 제외, 오염 원천 차단.
            if not sig or sig == "funding_carry":
                # Funding-carry records are never graded (direction-neutral,
                # horizon 9999h), but they must not outlive their position:
                # a leftover carry record keeps the symbol in carry_syms
                # forever and would permanently exempt later normal positions
                # in that symbol from aftercare (review M3). Drop the record
                # once its perp leg is gone; keep it while the carry is live
                # so the TP/SL aftercare exemption still applies.
                if sig == "funding_carry" and sym not in live_syms:
                    log(f"펀딩 캐리 기록 정리: {sym} (포지션 종료 확인)")
                    continue
                still_open.append(rec)   # 기록은 두되 채점 안 함
                continue
            try:
                at = datetime.fromisoformat(rec["at"])
                # UTC(+00:00)로 기록된 새 형식과 시간대 없는 옛 형식을 모두
                # naive 로컬시각으로 통일, naive now 와 빼도 안 터지게.
                if at.tzinfo is not None:
                    at = at.astimezone().replace(tzinfo=None)
            except (ValueError, KeyError):
                continue
            due_h = rec.get("horizon_hours") or 24
            elapsed = (now - at).total_seconds()
            expired = elapsed >= due_h * 3600
            closed = sym not in live_syms
            # 만기여도 아직 열려 있으면 장부에 남기되 채점은 미룬다.
            #   · 남기는 이유(B2): 빼버리면 만기청산·트레일링·본전스탑이 그 심볼을
            #     건너뛰어 남은 생애 내내 무관리가 된다.
            #   · 미루는 이유(N1): 만기 시점의 미실현 호가로 먼저 채점하면, 나중에
            #     실제 청산될 때 같은 거래가 한 번 더 계상된다(08-10 재현, 2배).
            #     실제 청산 손익이 언제나 더 정확하므로 청산 때 한 번만 센다.
            # 영원히 안 닫히는 기록이 장부에 쌓이지 않도록, 지평의 3배가 지나면
            # 그때는 추정으로 채점하고 정리한다 (만기청산이 1.5배라 여기 도달하면
            # 사후관리가 실패했다는 뜻이다).
            if not closed and elapsed < due_h * 3 * 3600:
                still_open.append(rec)
                continue
            cur = float(pr.get(sym, {}).get("mark")
                        or pr.get(sym, {}).get("mid") or 0)
            entry = float(rec.get("entry_price") or 0)
            if entry <= 0 or cur <= 0:
                # 가격을 못 구했다고 만기 기록을 조용히 버리지 않는다(M7),
                # 만기 후 실패만 세서 재시도 3회를 보장하고, 4번째 만기 실패에
                # 사유를 남기고 정리한다 (만기 전 실패는 카운트하지 않는다,
                # 검토자 지적: 만기 전 누적이 만기 후 재시도 몫을 깎지 않게).
                tries = int(rec.get("_grade_retries", 0)) + (1 if expired else 0)
                if not expired or tries <= 3:
                    rec["_grade_retries"] = tries
                    still_open.append(rec)
                else:
                    log(f"채점 불가로 정리: {sym} {rec.get('signal', '?')} "
                        f"(가격 조회 {tries}회 실패)")
                continue
            # 승패·손익 판정: 실제 청산 손익이 있으면 그걸 쓴다(정직).
            # 없으면(만기지만 아직 보유 등) 방향 추측으로 폴백.
            # 이 진입 이후에 일어난 청산만 센다 (같은 심볼의 옛 거래를 끌어오지 않게).
            real = _closes_since(sym, int(at.timestamp() * 1000))
            base_sig = rec.get("signal", "?").split(" +조합")[0]
            if closed and real is not None:
                pnl_usd = real["pnl"]
                won = pnl_usd > 0
                exit_px = real["exit"] or cur
                reason = real["cause"] or "closed"
            else:
                move = cur / entry - 1
                signal_move = move if rec.get("side") == "long" else -move
                won = signal_move > 0
                # 방향 추측일 땐 명목가 기준 근사 손익 (달러 단위로 통일)
                notional = entry * abs(float(rec.get("amount") or 0)) \
                    or float(rec.get("notional") or 0)
                pnl_usd = signal_move * (notional or entry)
                exit_px = cur
                reason = "expire"
            # 적응 단위 = 신호×시간봉 (같은 신호도 시간봉 따라 EV 부호가 뒤집힘).
            key = f"{base_sig}@{rec.get('interval','?')}"
            g = graded.setdefault(key, {"win": 0, "total": 0, "pnl": 0.0})
            g["win"] += int(won)
            g["total"] += 1
            g["pnl"] = round(g.get("pnl", 0.0) + pnl_usd, 2)
            # R 단위(위험 배수) 손익도 함께 기록한다. 달러 손익은 포지션 크기·
            # 레버리지·자본이 바뀌면 같은 신호라도 값이 달라져, 설정을 조정한
            # 전후 거래를 나란히 비교할 수 없다. 1R = 그 거래가 손절에 걸렸을 때
            # 잃기로 한 금액이므로, 규모가 어떻게 바뀌어도 '몇 배 벌었나'는 그대로다.
            # (2026-08-05 레버리지 3→5배·동시 6→3개 조정 시점부터 기록)
            try:
                risk_usd = abs(float(rec.get("notional") or 0)
                               * float(rec.get("sl_move") or 0))
                if risk_usd > 0:
                    g["pnl_r"] = round(g.get("pnl_r", 0.0) + pnl_usd / risk_usd, 3)
                    g["n_r"] = g.get("n_r", 0) + 1
            except (TypeError, ValueError):
                pass
            # 예측봉 대조 (사용자 루트 2026-08-07): 진입 때 그려둔 체크포인트와
            # 실제 1h 종가를 대조해 경로 적중을 채점한다. 실패해도 채점은 계속.
            try:
                pred = rec.get("pred") or {}
                checks = pred.get("checks") or []
                if checks and entry > 0:
                    from .signal_scanner import fetch_bars
                    t0 = int(at.timestamp() * 1000)
                    bars1h = [b for b in fetch_bars(client, sym, "1h", max_bars=200)
                              if b[0] >= t0]
                    hits = n_checked = 0
                    errs = []
                    for ck in checks:
                        tgt_ms = t0 + int(float(ck["h"]) * 3_600_000)
                        cand = [b for b in bars1h if b[0] <= tgt_ms]
                        if not cand:
                            continue
                        actual_px = float(cand[-1][4])
                        n_checked += 1
                        same_dir = (actual_px - entry) * (float(ck["px"]) - entry) > 0
                        hits += int(same_dir)
                        errs.append(abs(actual_px - float(ck["px"])) / entry)
                    if n_checked:
                        rec["pred_check"] = {
                            "hits": hits, "n": n_checked,
                            "avg_err_pct": round(sum(errs) / len(errs) * 100, 3)}
            except Exception:
                pass
            # 원인분석: 실제 손익 + 청산사유 + 귀인 기록
            try:
                from .postmortem import analyze_close
                pm = analyze_close(rec, exit_px, pnl_usd, reason, regime_now)
                changed.append(
                    f"청산분석 {sym} {base_sig[:10]}: "
                    f"{'승' if won else '패'} {pnl_usd:+.2f} · {pm['attribution']}")
            except Exception as e:
                log(f"원인분석 스킵: {e}")
            # 메타학습: '예측 승률 vs 실제 승패'를 캘리브레이션에 축적.
            # 표본이 쌓이면 봇이 자기 낙관/비관을 스스로 교정하기 시작한다.
            try:
                from .brain import record_prediction
                pred = float(rec.get("win_rate") or 0)
                if pred > 0:
                    record_prediction(pred, won)
            except Exception:
                pass
        except Exception as e:
            # 한 기록의 오류가 루프 전체를 무너뜨리면, 앞서 처리된 건들이
            # postmortem.jsonl·calibration.json 에는 이미 쓰였는데 st 는
            # 통째로 버려진다. 다음 사이클에 같은 거래가 다시 채점돼
            # 학습 파일이 이중 기록된다 (검토 H13). 문제 기록만 남기고
            # 넘어가 나머지 채점과 st 저장이 정상 완료되게 한다.
            log(f"채점 오류로 이 기록만 건너뜀: {rec.get('symbol')} {e}")
            still_open.append(rec)
            continue
    st["opened"] = still_open

    # --- 2) 지는 조합 정지 (실제 손익 우선) ---
    # 신호@시간봉 단위라 표본이 신호당보다 얇다 → min_grade를 조합용으로 낮춤.
    # 정지 기준: 승률이 바닥 미만 '이면서' 실제 순손익도 마이너스일 때만.
    #   (승률 낮아도 손익비가 좋아 순이익이면 살려둔다, 승률≠수익 실측 교훈)
    banned = set(st.get("banned_signals", []))
    min_grade = int(policy.get("adapt_min_graded_combo",
                               max(5, int(policy.get("adapt_min_graded", 8)) // 2)))
    floor_wr = float(policy.get("adapt_win_floor", 0.45))
    # 정지 만료, 정지된 신호는 거래를 안 하니 새 표본이 안 쌓이고, 그래서
    # 해제 조건(승률 floor+10%p)을 영원히 만족할 수 없다. 그대로 두면 한 번의
    # 오탐이 영구 사망이 된다. 기간이 지나면 풀고 성적을 지워 다시 증명하게 한다.
    ban_days = float(policy.get("adapt_ban_days", 21) or 0)
    ban_at = st.setdefault("ban_at", {})
    if ban_days > 0:
        now_ts = time.time()
        for sig in list(banned):
            since = float(ban_at.get(sig) or 0)
            if since and (now_ts - since) / 86400 >= ban_days:
                banned.discard(sig)
                ban_at.pop(sig, None)
                graded.pop(sig, None)      # 성적 초기화, 백지에서 재평가
                changed.append(f"조합 '{sig}' 정지 만료 ({ban_days:.0f}일), 재평가")
    for sig, g in graded.items():
        if g["total"] >= min_grade:
            wr = g["win"] / g["total"]
            pnl = g.get("pnl", 0.0)
            if wr < floor_wr and pnl < 0 and sig not in banned:
                banned.add(sig)
                ban_at[sig] = time.time()
                changed.append(
                    f"조합 '{sig}' 정지 (승률 {wr:.0%}, 손익 {pnl:+.1f}, {g['total']}건)")
            elif wr >= floor_wr + 0.10 and pnl >= 0 and sig in banned:
                banned.discard(sig)
                ban_at.pop(sig, None)
                changed.append(f"조합 '{sig}' 재개 (승률 {wr:.0%}, 손익 {pnl:+.1f})")
    st["banned_signals"] = sorted(banned)
    st["ban_at"] = ban_at

    # --- 3) 실제 손익 기반 사이즈 조절 ---
    # 승률이 아니라 '실제 달러 손익'으로 판단한다. 승률 높아도 크게 잃으면
    # 줄여야 하고(예: 승률 66% PUMP가 -428), 승률 낮아도 순이익이면 유지.
    # 정지 안 된(살아있는) 조합만 집계, 나쁜 조합은 이미 banned로 빠졌으므로
    # 그것까지 합산해 좋은 조합을 처벌하지 않는다(결함 B 수정).
    live_pnl = sum(g.get("pnl", 0.0) for k, g in graded.items()
                   if k not in banned)
    total_n = sum(g["total"] for k, g in graded.items() if k not in banned)
    base_cap = operating_capital(policy, exchange_equity(client))
    scale = float(st.get("size_scale", 1.0))
    if total_n >= min_grade and base_cap > 0:
        pnl_pct = live_pnl / base_cap
        if pnl_pct < -0.05 and scale > 0.5:      # 살아있는 조합들이 -5% 순손실
            scale = round(scale * 0.5, 3)
            changed.append(f"사이즈 축소 → {scale:.0%} (실현 {pnl_pct:+.1%})")
        elif pnl_pct > 0.02 and scale < 1.0:     # +2% 순이익 회복
            scale = min(1.0, round(scale * 2, 3))
            changed.append(f"사이즈 복원 → {scale:.0%} (실현 {pnl_pct:+.1%})")
    st["size_scale"] = scale

    if changed:
        st.setdefault("adapt_log", []).append(
            {"at": now.isoformat(), "changes": changed})
        notify.send(f"🧠 {agent_name()} 자가조정:\n  " + "\n  ".join(changed))
        for c in changed:
            log(f"적응: {c}")


def read_regime(client: PacificaClient) -> dict:
    """국면 인지, 공포탐욕지수 기반. 실측 교훈: 극단 공포에선 '반등 매수'가
    손실 경향, 극단 탐욕에선 '과열 롱'이 위험. 방향 성향을 반환한다."""
    from .market_context import fear_greed
    fg = fear_greed()
    if not fg:
        return {"label": "알수없음", "val": None, "bias": "neutral"}
    val, cls = fg
    if val <= 25:
        bias = "prefer_short"    # 극단 공포, 롱(반등베팅) 자제
    elif val >= 75:
        bias = "prefer_long_caution"  # 극단 탐욕, 신규 롱 신중
    else:
        bias = "neutral"
    return {"label": f"{cls}({val})", "val": val, "bias": bias}


def regime_allows(regime: dict, side: str) -> bool:
    """국면이 이 방향 진입을 허용하는가 (실측 기반 필터)."""
    bias = regime.get("bias", "neutral")
    if bias == "prefer_short" and side == "long":
        return False   # 극단 공포에 롱 금지 (떨어지는 칼 잡기 방지)
    if bias == "prefer_long_caution" and side == "long":
        return False   # 극단 탐욕에 신규 롱 자제
    return True


def try_funding_carry(client, policy, st, held, scale,
                      budget_cap: float = 0.0) -> bool:
    """펀딩 캐리 진입 시도, 스팟+perp 델타뉴트럴을 배치(Atomics)로 한 번에.
    성공 시 True. 스팟 상장 코인(현재 SOL 등)만 대상.
    budget_cap: 포트폴리오 펀딩 버킷의 남은 예산 (이 이상 안 씀)."""
    from .scanner import scan
    ppy = 8760
    cands = scan(client, ppy, require_spot=True)
    if not cands:
        return False
    best = cands[0]
    min_carry = float(policy.get("funding_min_apr", 0.15))
    # 이 함수는 '스팟매수+perp숏'만 한다 = 펀딩이 양수(숏이 받는)일 때만 수익.
    # 음수 펀딩은 롱이 받으므로 숏치면 오히려 펀딩을 낸다. 반드시 양수만.
    if best.apr <= 0 or best.apr < min_carry or best.symbol in held:
        return False
    # 예산 → 수량. 델타뉴트럴의 핵심은 스팟 수량 == perp 수량.
    # 두 다리의 랏 사이즈가 다를 수 있으므로, 둘 다 나누어떨어지는 공통 수량을
    # 구한다(더 큰 랏으로 내림). 수수료를 수량에서 빼면 오히려 델타가 생기므로
    # (덜 숏 → 롱 노출) 빼지 않는다. 수수료는 손익이지 헤지 수량이 아니다.
    from .position import _round_down_to_lot
    bucket = budget_cap if budget_cap > 0 else \
        operating_capital(policy, exchange_equity(client)) \
        * float(policy.get("funding_alloc", 0.3))
    budget = min(bucket * scale, float(policy["max_position_usd"]))
    lot = max(best.perp_lot_size, best.spot_lot_size)   # 공통 배수
    amt = _round_down_to_lot(budget / best.mid_price, lot)
    if amt <= 0 or amt * best.mid_price < max(best.perp_min_order,
                                              best.spot_min_order):
        return False
    hedge = amt   # 동일 수량 = 진짜 델타뉴트럴
    log(f"펀딩 캐리 진입: {best.symbol} 스팟매수+perp숏 {amt}개 "
        f"(델타뉴트럴, 펀딩 APR {best.apr:+.1%})")
    try:
        # Atomics: 스팟 매수 + perp 숏을 원자적 배치로 동시 실행
        client.batch_market_orders([
            {"symbol": best.spot_symbol, "side": "bid", "amount": amt},
            {"symbol": best.symbol, "side": "ask", "amount": hedge},
        ], builder_code=policy.get("builder_code", ""))
        st.setdefault("opened", []).append({
            "symbol": best.symbol, "side": "carry", "signal": "funding_carry",
            "win_rate": 0.5, "entry_price": best.mid_price,
            "horizon_hours": 9999, "at": datetime.now(timezone.utc).isoformat()})
        notify.send(f"🤖 펀딩 캐리 진입: {best.symbol} 델타뉴트럴 {amt}개 "
                    f"(펀딩 APR {best.apr:+.1%}) · Atomics 배치 체결")
        held.add(best.symbol)   # 같은 사이클의 지표 매매가 중복 진입하지 않도록
        return True
    except PacificaError as e:
        log(f"펀딩 캐리 실패: {e}")
        return False


def manage_positions(client: PacificaClient, policy: dict, st: dict) -> None:
    """포지션 사후관리, 이기는 거래를 지키는 3단계.
    ① 부분 익절: 수익이 기준 도달 시 절반 청산(수익 확정), 나머지는 달리게
    ② 본전 스탑: 수익 +1% 도달 시 손절선을 진입가로 (이기던 게 지는 거래로 안 변함)
    ③ 트레일링: 수익 +2%부터 손절선이 가격을 1% 간격으로 따라감 (추세 수익 잠금)
    손절선은 유리한 방향으로만 이동하며 절대 후퇴하지 않는다. 캐리는 제외."""
    from .position import _round_down_to_lot, _round_to_tick
    try:
        positions = client.get_positions()
    except PacificaError:
        return
    care = st.setdefault("aftercare", {})
    if not positions:
        care.clear()
        return
    try:
        prices = {p["symbol"]: p for p in client.get_prices()}
        mkts = {m["symbol"]: m for m in client.get_markets()}
    except PacificaError:
        return
    # 기존 익절 주문 보존용 (손절선만 옮길 때 익절이 지워지지 않도록)
    tp_map = {}
    try:
        for o in client._get("orders", {"account": client.address}):
            # take_profit_market/limit 어느 표기든 잡는다, 정확 일치에 걸면
            # API 표기가 바뀌는 순간 트레일링 갱신마다 익절이 조용히 사라진다(M4)
            if str(o.get("order_type", "")).startswith("take_profit"):
                tp_map[o.get("symbol")] = str(o.get("stop_price", ""))
    except PacificaError:
        pass
    carry_syms = {o["symbol"] for o in st.get("opened", [])
                  if o.get("signal") == "funding_carry"}
    be_at = float(policy.get("breakeven_at_pct", 0.01))
    tr_at = float(policy.get("trail_at_pct", 0.02))
    tr_gap = float(policy.get("trail_gap_pct", 0.01))
    ptp_at = float(policy.get("partial_tp_at_pct", 0.015))
    ptp_frac = float(policy.get("partial_tp_fraction", 0.5))
    # 진입 기록(신호 지평·시각), 시간 만료 판정용
    opened_by = {}
    for o in st.get("opened", []):
        if o.get("signal") != "funding_carry":
            opened_by[o["symbol"]] = o
    expire_mult = float(policy.get("expire_after_horizons", 0) or 0)

    # 봇이 진입한 심볼만 관리 대상 (진입 기록에 있는 것). Print 정산 등으로
    # 봇 밖에서 생긴 포지션은 봇이 손대지 않는다, 07-24 BTC 폭탄(Print 체결)에
    # 봇이 엉뚱한 손절을 걸던 문제 차단. 봇 것만 사후관리한다.
    bot_syms = {o["symbol"] for o in st.get("opened", [])}
    # 고아 감시: 봇 장부에 없는 포지션(Print 정산·수동·기록 실패)을 발견하면
    # 알림만 한다, 자동 편입은 안 함(Print 정산과 구분 불가). 심볼당 1회 알림,
    # 그 포지션이 사라지면 기록을 지워 다음에 다시 생기면 또 알린다.
    alerted = set(st.setdefault("orphan_alerted", []))
    cur_orphans = {p.get("symbol") for p in positions
                   if p.get("symbol") not in bot_syms}
    for sym in sorted(cur_orphans - alerted):
        log(f"⚠️ 고아 포지션 발견: {sym}, 봇 장부에 없음 "
            f"(Print 정산·수동 진입·기록 실패 중 하나). 봇은 손대지 않음. "
            f"트레일링·채점 없이 거래소 손절만 적용되는 상태이니 직접 확인 필요.")
        notify.send(f"⚠️ {agent_name()} 고아 포지션: {sym}, 봇 장부에 없어 "
                    f"관리 대상 아님. 직접 확인하세요.")
    st["orphan_alerted"] = sorted((alerted & cur_orphans) | cur_orphans)
    live = set()
    for p in positions:
        sym = p.get("symbol")
        live.add(sym)
        if sym in carry_syms:
            continue
        if sym not in bot_syms:
            continue   # 봇이 안 연 포지션(Print 등) → 관리 안 함
        # ⓪ 시간 만료 청산: 신호의 예측 지평이 끝나면 통계적 우위도 끝났다.
        # 손절·익절 어느 쪽도 안 닿은 채 슬롯·예산만 묶고 있으므로 정리한다.
        rec = opened_by.get(sym)
        if expire_mult > 0 and rec and rec.get("horizon_hours"):
            try:
                opened_at = datetime.fromisoformat(rec["at"])
                if opened_at.tzinfo is not None:   # UTC 기록 → naive 로컬로 통일
                    opened_at = opened_at.astimezone().replace(tzinfo=None)
                held_h = (datetime.now() - opened_at).total_seconds() / 3600
                limit_h = float(rec["horizon_hours"]) * expire_mult
            except (ValueError, TypeError, KeyError):
                held_h = limit_h = 0
            if limit_h and held_h > limit_h:
                try:
                    amt = abs(float(p["amount"]))
                    close_side = "ask" if p.get("side") == "bid" else "bid"
                    client.create_market_order(
                        sym, close_side, str(amt), "0.5", reduce_only=True,
                        builder_code=policy.get("builder_code", ""))
                    # Confirm the close actually filled instead of trusting
                    # the order response: a rejected/partial close would
                    # otherwise leave the position live while this loop
                    # believes it is gone. Re-read once and retry once;
                    # if it still shows, say so loudly and let the next
                    # cycle try again (the expiry condition re-fires).
                    time.sleep(2)
                    try:
                        still = next(
                            (q for q in client.get_positions()
                             if q.get("symbol") == sym), None)
                    except PacificaError:
                        still = None       # cannot check; next cycle will
                    if still is not None:
                        log(f"시간 만료 청산 미체결 확인: {sym}, 1회 재시도")
                        client.create_market_order(
                            sym, close_side,
                            str(abs(float(still.get("amount") or amt))),
                            "0.5", reduce_only=True,
                            builder_code=policy.get("builder_code", ""))
                        time.sleep(2)
                        try:
                            still = next(
                                (q for q in client.get_positions()
                                 if q.get("symbol") == sym), None)
                        except PacificaError:
                            still = None
                        if still is not None:
                            log(f"⚠️ 시간 만료 청산 재시도에도 포지션 잔존: "
                                f"{sym}, 다음 사이클에 다시 시도")
                            notify.send(f"⚠️ 시간 만료 청산 미완료: {sym}, "
                                        f"포지션이 아직 열려 있습니다")
                            continue
                    log(f"시간 만료 청산: {sym}, 신호 지평 {limit_h:.0f}시간 경과"
                        f"(보유 {held_h:.0f}시간), 우위 소멸로 정리")
                    notify.send(f"⏰ 시간 만료 청산: {sym} (지평 {limit_h:.0f}h 경과)")
                    continue
                except PacificaError as e:
                    log(f"시간 만료 청산 실패: {sym} {e}")
        try:
            amt = abs(float(p["amount"]))
            entry = float(p["entry_price"])
        except (KeyError, TypeError, ValueError):
            continue
        mark = float(prices.get(sym, {}).get("mark")
                     or prices.get(sym, {}).get("mid") or 0)
        if not (amt > 0 and entry > 0 and mark > 0):
            continue
        is_long = p.get("side") == "bid"
        profit = (mark - entry) / entry if is_long else (entry - mark) / entry
        c = care.setdefault(sym, {})
        # Aftercare memory must not outlive the position it was built for.
        # If the symbol closed and re-entered between checks (possibly in the
        # opposite direction), the old SL ratchet would block the new
        # position's stop from ever moving (review M2). Key the memory to the
        # open record's timestamp + current side and reset on mismatch.
        # Entries written before this key existed are adopted as-is once, so
        # an upgrade does not re-lower an already ratcheted stop.
        pos_id = f"{(rec or {}).get('at', '')}|{p.get('side', '')}"
        if c.get("pos_id") != pos_id:
            if "pos_id" in c:
                c.clear()          # different position -> stale memory
            c["pos_id"] = pos_id
        tick = float(mkts.get(sym, {}).get("tick_size") or 0.01)
        lot = float(mkts.get(sym, {}).get("lot_size") or 0.0001)
        min_usd = float(mkts.get(sym, {}).get("min_order_size") or 10)
        # ① 부분 익절 (포지션당 한 번만; 양쪽 다 최소주문 이상 남을 때만)
        if profit >= ptp_at and ptp_frac > 0 and not c.get("partial_done"):
            cut = _round_down_to_lot(amt * ptp_frac, lot)
            if cut * mark >= min_usd and (amt - cut) * mark >= min_usd:
                try:
                    client.create_market_order(
                        sym, "ask" if is_long else "bid", str(cut), "0.5",
                        reduce_only=True,
                        builder_code=policy.get("builder_code", ""))
                    c["partial_done"] = True
                    amt -= cut
                    log(f"부분 익절: {sym} {cut}개 청산 "
                        f"(수익 {profit:+.1%} 확정, 나머지 {amt}개 계속)")
                    notify.send(f"💰 부분 익절: {sym} {cut}개 ({profit:+.1%})")
                except PacificaError as e:
                    log(f"부분 익절 실패: {sym} {e}")
        # ②③ 손절선 이동, 트레일링이 본전보다 우선, 유리한 쪽으로만
        new_sl, tag = None, ""
        if profit >= tr_at:
            new_sl = mark * (1 - tr_gap) if is_long else mark * (1 + tr_gap)
            tag = "트레일링"
        elif profit >= be_at:
            new_sl, tag = entry, "본전 스탑"
        if new_sl is not None:
            prev = c.get("sl")
            if prev is None or (new_sl > prev if is_long else new_sl < prev):
                slp = _round_to_tick(new_sl, tick)
                # 포지션 side를 그대로 넘긴다, set_position_tpsl이 내부에서
                # 청산 방향(반대)으로 뒤집는다. (중복 변환 금지)
                try:
                    client.set_position_tpsl(
                        sym, p.get("side", "bid"),
                        take_profit_price=tp_map.get(sym, ""),
                        stop_loss_price=slp)
                    c["sl"] = float(slp)
                    log(f"{tag}: {sym} 손절선 → {slp} (수익 {profit:+.1%} 잠금)")
                except PacificaError as e:
                    log(f"손절선 이동 실패: {sym} {e}")
    # 청산된 포지션의 관리 기록 정리
    for sym in [s for s in care if s not in live]:
        del care[sym]


def run_cycle(client: PacificaClient, policy: dict, st: dict, dry: bool) -> None:
    st["cycles"] = st.get("cycles", 0) + 1
    # Sizing basis: exchange equity only (review M5). The combined figure
    # (balance + Print) is still what reports and the equity curve show.
    eq = exchange_equity(client)

    # 최후 방어선(치명적 손실)만 확인, 아니면 계속 가동
    if final_stop_check(client, policy, st, dry=dry):
        log("최후 방어선 발동 또는 일시 오류, 이번 사이클 신규 거래 없음")
        # 정지 중에도 보유 포지션 사후관리(트레일링·본전 스탑·만기 청산)는
        # 계속한다, 완전정지 때 청산이 일부 실패하면(429 등) 그 포지션이
        # 네이티브 손절 하나만 남긴 채 무관리로 방치되는 것을 막는다.
        if not dry:
            try:
                manage_positions(client, policy, st)
            except Exception as e:
                log(f"정지 중 사후관리 스킵(일시 오류): {e}")
        save_state(st)
        return

    # 자본 곡선 기록 (성과 추적·수익 그래프 재료)
    # The curve is user-facing performance, so it keeps balance + Print.
    record_equity(equity(client))

    # 자가 재측정: 오래됐으면 백그라운드로 전 시간봉 매트릭스를 다시 돌린다.
    # 국면이 바뀌면 최적 시간봉·유효 신호도 바뀌므로, 고정 파라미터로 두지 않는다.
    try:
        from .rematrix import ensure_fresh
        ensure_fresh(policy, log=log)
    except Exception as e:
        log(f"재측정 확인 스킵: {e}")

    # 보정표(워크포워드)도 같은 이유로 낡는다. 이게 있어야 학습이 판단으로
    # 돌아온다, 없으면 보정표는 만든 날에 멈춘 사진이다.
    # 매트릭스 재측정과 겹치지 않게, 그쪽이 도는 중이면 다음 사이클로 미룬다
    # (둘 다 파시피카·바이낸스를 두드려 서로 레이트리밋에 걸린다).
    try:
        from .rematrix import LOCK_FILE as _MLOCK
        busy = (os.path.exists(_MLOCK)
                and time.time() - os.path.getmtime(_MLOCK) < 10800)
        if not busy:
            from .walkforward import ensure_fresh as wf_fresh
            wf_fresh(policy, log=log)
    except Exception as e:
        log(f"보정표 확인 스킵: {e}")

    # 포지션 사후관리, 부분 익절 / 본전 스탑 / 트레일링 (진입 판단보다 먼저)
    if not dry:
        try:
            manage_positions(client, policy, st)
        except Exception as e:
            log(f"사후관리 스킵(일시 오류): {e}")

    # 데이터 수집·학습: 신호를 진입 무관하게 관측·채점 (매 사이클)
    # observe_universe=0 → 파시피카 전체 perp를 batch개씩 순회 관측
    if policy.get("collect_observations", True):
        try:
            from .observer import grade, observe
            nobs = observe(client, int(policy.get("observe_universe", 0)),
                           batch=int(policy.get("observe_batch", 20)))
            gres = grade(client)
            if nobs or gres:
                log(f"관측 {nobs}건 기록"
                    + (f" · 채점 {gres.get('graded',0)}건 "
                       f"(추적 신호 {gres.get('signals_tracked',0)}종)" if gres else ""))
        except Exception as e:
            log(f"관측 스킵(일시 오류): {e}")

    # 자가진단·적응: 지는 신호 정지, 사이즈 조절
    if not dry:
        review_and_adapt(client, policy, st)
        # 채점 직후 즉시 저장, postmortem·brain 파일에는 이미 기록이 남았으므로,
        # 이 아래에서 예외가 나서 st를 잃으면 다음 사이클이 같은 청산을 다시
        # 채점해 이중 계상된다 (429 폭주 시 실제로 발생하는 경로).
        save_state(st)

    positions = client.get_positions()
    n_open = len(positions)
    scale = float(st.get("size_scale", 1.0))
    banned = set(st.get("banned_signals", []))

    # 국면 인지, 뉴스/공포지수/펀딩 극단값 (진입 성향에 반영)
    regime = read_regime(client)
    log(f"자본 {eq:,.0f} / 보유 {n_open}개 / 사이클 {st['cycles']} "
        f"/ 사이즈배율 {scale:.0%} / 국면 {regime['label']}"
        + (f" / 정지신호 {len(banned)}개" if banned else ""))

    if n_open >= int(policy["max_concurrent"]):
        log("최대 동시 보유 도달, 신규 진입 안 함")
        if st["cycles"] % int(policy.get("report_every_cycles", 8)) == 0:
            report(client, st, policy, push=True)
        save_state(st)
        return

    held = {p["symbol"] for p in positions}
    strategies = policy.get("allowed_strategies", ["stat_setup"])

    # ── 포트폴리오 버킷: 매매(주력) / 펀딩캐리(안정) / 현금예비 ──
    # 최종목표는 지갑 자본 증식, 버킷별 사용량을 재고 비는 쪽을 채운다.
    # capital_usd: live 면 실계좌 잔고(eq)를 그대로 기준 자본으로 쓴다.
    capital = operating_capital(policy, eq)
    carry_syms = {o["symbol"] for o in st.get("opened", [])
                  if o.get("signal") == "funding_carry"}

    def _notional(p):
        try:
            return abs(float(p.get("amount") or 0)) \
                * float(p.get("entry_price") or 0)
        except (TypeError, ValueError):
            return 0.0

    carry_used = sum(_notional(p) for p in positions
                     if p["symbol"] in carry_syms)
    trading_used = sum(_notional(p) for p in positions
                       if p["symbol"] not in carry_syms)
    # trading_alloc 0 = 총액 상한 없음. 개수(max_concurrent)와 크기
    # (max_position_usd)가 이미 총량을 정하므로 이중 규제를 걷어낸 것이다.
    # 0을 그대로 곱하면 예산이 0이 되어 아무것도 못 여니 무한대로 바꾼다.
    _alloc = float(policy.get("trading_alloc", 0.5))
    trading_cap = capital * _alloc if _alloc > 0 else float("inf")
    carry_cap = capital * float(policy.get("funding_alloc", 0.3))
    trading_budget = max(trading_cap - trading_used, 0.0)
    carry_budget = max(carry_cap - carry_used, 0.0)
    # 방향 쏠림(넷 델타): 롱 명목 - 숏 명목. 한 방향 노출이 한도를 넘으면
    # 같은 방향 신규 진입을 막는다 (숏 3개 몰림 → 급등 한 방에 다 맞는 것 방지)
    net_exposure = sum(
        (_notional(p) if p.get("side") == "bid" else -_notional(p))
        for p in positions if p["symbol"] not in carry_syms)
    net_cap = capital * float(policy.get("max_net_exposure_pct", 0.3))
    _cap_txt = "무제한" if trading_cap == float("inf") else f"{trading_cap:,.0f}"
    log(f"포트폴리오, 매매 {trading_used:,.0f}/{_cap_txt} · "
        f"펀딩 {carry_used:,.0f}/{carry_cap:,.0f} · "
        f"예비 {max(capital - trading_used - carry_used, 0):,.0f} · "
        f"방향노출 {net_exposure:+,.0f}/±{net_cap:,.0f}")

    # 소프트 정지 중이면 오늘은 신규 진입 없음 (보유·사후관리는 위에서 이미 실행됨)
    if is_soft_halted(st):
        log("소프트 정지 중, 오늘 신규 진입 없음 (보유 포지션만 관리)")
        if st["cycles"] % int(policy.get("report_every_cycles", 8)) == 0:
            report(client, st, policy, push=True)
        save_state(st)
        return

    # ── 펀딩 캐리 (안정 버킷): 배분이 남아 있을 때만 채움 ──
    # 테스트넷은 스팟 호가가 비어 먼지 체결만 남으므로 캐리 스킵 (메인넷 전용)
    on_testnet = "test-api" in str(policy.get("base_url", ""))
    if ("funding_carry" in strategies and not dry
            and carry_budget >= 10 and not on_testnet):
        if try_funding_carry(client, policy, st, held, scale,
                             budget_cap=carry_budget):
            save_state(st)

    # ── 전략 2: 통계 지표 셋업 (국면 필터 적용) ──
    if "stat_setup" in strategies:
        budget = capital   # 위에서 구한 운용 기준 자본(고정값 또는 실잔고)
        setups = evaluate_setups(
            # 스캔 범위. 예전엔 12로 못 박혀 있어서 거래대금 하한($100k)을
            # 통과한 29종목 중 12개만 봤다, XRP·BNB·ADA·AAVE 같은 주요
            # 종목까지 놓치고 있었다(2026-08-06). 하한이 이미 얇은 시장을
            # 거르므로 여기서 또 자를 이유가 없다.
            client, budget_usd=budget,
            universe=int(policy.get("scan_universe", 40) or 40),
            min_volume_24h=float(policy.get("min_volume_24h", 0) or 0),
            position_usd=float(policy.get("max_position_usd", 0) or 0),
            max_position_pct_of_volume=float(
                policy.get("max_position_pct_of_volume", 0) or 0),
            # 정책 레버리지 상한을 스캐너에도 알려준다. 안 넘기면 스캐너가
            # 거래소 상한으로 증거금을 계산해, 여기서 상한을 낮추는 순간
            # 포지션이 그 배수만큼 작아진다.
            max_leverage=int(policy.get("max_leverage", 0) or 0),
            # 1회 위험도 정책에서 온다, 예전엔 스캐너 상수만 쓰여
            # policy.yaml 을 바꿔도 매매에 반영되지 않았다.
            risk_pct=float(policy.get("risk_per_trade_pct", 0) or 0))
        wl_wr = float(policy.get("watchlist_entry_win_rate", 0) or 0)
        require_live = policy.get("require_live_signal", True)

        def entry_ok(s):
            # 자가정지 대조: banned는 '신호@시간봉' 단위. s.signal에 조합노트가
            # 붙을 수 있으므로 기본 신호명만 추출해 대조한다.
            base_sig = s.signal.split(" +조합")[0]
            adapt_key = f"{base_sig}@{s.interval}"
            # 공통 요건
            if not (s.n_samples >= int(policy["min_samples"])
                    and s.symbol not in held and adapt_key not in banned
                    and base_sig not in banned    # 구버전 이름-only 정지도 존중
                    and (not policy.get("use_regime_filter", True)
                         or regime_allows(regime, s.side))):
                return False
            # 점등이면 min_win_rate만 넘으면 OK
            if s.live and s.win_rate >= float(policy["min_win_rate"]):
                return True
            # 점등 안 됐어도 승률이 watchlist 기준 이상이면 진입 허용
            if not require_live or (wl_wr > 0 and s.win_rate >= wl_wr):
                return s.win_rate >= max(float(policy["min_win_rate"]),
                                         wl_wr if not s.live else 0)
            return False

        picks = [s for s in setups if entry_ok(s)]
        # ── 두뇌(Brain) 종합 확신도: 매트릭스·관측·실전손익·메타학습을 하나로 ──
        # 모든 학습원이 이 숫자 하나로 합쳐져 진입 순서와 사이즈를 결정한다.
        try:
            from .brain import conviction
            graded_now = st.get("graded", {})
            conv_map = {}
            for s in picks:
                cv = conviction(s.symbol, s.signal, s.interval, s.side,
                                s.win_rate, s.n_samples, graded_now)
                conv_map[id(s)] = cv
                if cv["notes"] != "기본":
                    log(f"확신도 {s.symbol} {s.signal[:12]}: "
                        f"{cv['conviction']:.2f} ({cv['notes']})")
            # 확신도 낮은 셋업 제거 + 높은 순 정렬 (좋은 것부터 슬롯 차지)
            min_conv = float(policy.get("min_conviction", 0.52))
            picks = [s for s in picks
                     if conv_map[id(s)]["conviction"] >= min_conv]
            picks.sort(key=lambda s: -conv_map[id(s)]["conviction"])
        except Exception as e:
            # fail-closed (2026-08-07 2회차 검토 M3): 두뇌가 죽으면 확신도
            # 관문 없이 전량 통과시키는 게 아니라, 이번 사이클 신규 진입을
            # 보류한다, 검증 못 한 관문을 열어두는 쪽이 진짜 위험.
            log(f"확신도 계산 실패, 이번 사이클 신규 진입 보류: {e}")
            picks = []
            conv_map = {}
        if not picks:
            log(f"정책 요건 충족 + 점등된 셋업 없음, 대기 (국면 {regime['label']})")
        # 매매는 주력 수익엔진, 버킷 예산과 동시수 한도 안에서 여러 건 진입
        from .position import _round_down_to_lot, _round_to_tick
        try:
            mkts = {m["symbol"]: m for m in client.get_markets()}
        except Exception:
            mkts = {}
        # 가용 증거금 확인, 하네스와 같은 관문을 실거래에도 둔다.
        # 조회 실패 시 fail-closed(신규 진입 보류): 확인 못 한 채 여는 게 진짜 위험.
        try:
            avail_margin = float(client.get_account().get("available_to_spend") or 0)
        except Exception as e:
            avail_margin = -1.0
            log(f"가용 증거금 조회 실패, 이번 사이클 신규 진입 보류: {e}")
        slots = int(policy["max_concurrent"]) - len(held)
        deep = _deep_history_symbols(policy)
        for best in picks:
            if slots <= 0:
                log("동시 보유 한도 도달, 나머지 셋업은 다음 사이클로")
                break
            if trading_budget < 10:
                log("매매 버킷 예산 소진, 나머지 셋업은 다음 사이클로")
                break
            if best.symbol in held:
                continue   # 같은 코인 중복 진입 방지
            if deep is not None and best.symbol not in deep:
                log(f"진입 스킵: {best.symbol}, 과거 데이터 얕음(검증단계 제한)")
                continue
            lev = min(best.leverage, int(policy["max_leverage"]))
            # EV 비례 사이징: 측정된 기대값이 높은 셋업에 더 큰 비중.
            # 실측상 통과 셋업 EV가 0%~16.7%로 편차가 크다, 같은 리스크로
            # 넣으면 강한 기회를 낭비한다. base(0.5)~max(1.5) 사이로 스케일.
            ev_mult = 1.0
            try:
                from .rematrix import signal_ev
                base_sig = best.signal.split(" +조합")[0]
                r = signal_ev(base_sig, best.interval)
                if r:
                    ev = r[0]
                    lo = float(policy.get("ev_size_floor_pct", 0.0))
                    hi = float(policy.get("ev_size_cap_pct", 0.05))
                    frac = (ev - lo) / (hi - lo) if hi > lo else 0.5
                    ev_mult = max(0.5, min(1.5, 0.5 + frac))
            except Exception:
                pass
            # 확신도 배율: 두뇌가 종합한 확신이 높을수록 크게 (0.7~1.3)
            conv_mult = 1.0
            cv = conv_map.get(id(best))
            if cv:
                conv_mult = 0.7 + cv["conviction"] * 0.6   # conv 0.5→1.0, 1.0→1.3
            notional = min(best.margin_usd * lev * scale * ev_mult * conv_mult,
                           float(policy["max_position_usd"]),
                           trading_budget)
            signed = notional if best.side == "long" else -notional
            if abs(net_exposure + signed) > net_cap:
                log(f"진입 스킵: {best.symbol} {best.side}, 방향 쏠림 한도 "
                    f"(현재 {net_exposure:+,.0f}, 한도 ±{net_cap:,.0f})")
                continue
            need_margin = notional / max(lev, 1)
            if avail_margin < 0 or need_margin > avail_margin:
                log(f"진입 스킵: {best.symbol}, 가용 증거금 부족/미확인 "
                    f"(필요 ${need_margin:,.0f}, 가용 ${max(avail_margin, 0):,.0f})")
                continue
            entry_side = "bid" if best.side == "long" else "ask"
            sl_pct = best.sl_move
            tp_pct = best.tp_move
            entry = best.entry
            # 가격은 틱, 수량은 랏 단위로 맞춤 (아니면 400 거부) + 최소 주문 확인
            lot = float(mkts.get(best.symbol, {}).get("lot_size", 0.0001))
            tick = float(mkts.get(best.symbol, {}).get("tick_size", 0.01))
            min_usd = float(mkts.get(best.symbol, {}).get("min_order_size", 10))
            sl_px = _round_to_tick(
                entry*(1-sl_pct) if best.side == "long" else entry*(1+sl_pct), tick)
            tp_px = _round_to_tick(
                entry*(1+tp_pct) if best.side == "long" else entry*(1-tp_pct), tick)
            # Wrong-side stop guard (review H10): after tick rounding the stop
            # must still sit below entry for a long, above entry for a short.
            # A coarse tick can round a tight stop onto/past the entry, which
            # the exchange rejects or triggers instantly. A stop that fails
            # validation is a reason to skip the entry, not to enter naked:
            # the always-attached native stop is a code-fixed invariant
            # (review 6 regression fix; same rule as bracket_prices()).
            sl_f = float(sl_px)
            if not (sl_f < entry if best.side == "long" else sl_f > entry):
                log(f"진입 스킵: {best.symbol} {best.side} 손절가가 반올림 후 "
                    f"{sl_px} (진입가 {entry})로 방향이 어긋남")
                continue
            # The TP leg gets the same post-rounding validation as the stop.
            # A coarse tick can collapse a tight target onto or past the
            # entry; a TP at/below entry (long) triggers immediately, closing
            # the position for a fee-only loss. Same rule as the bracket
            # bot's bracket_prices(): an invalid leg skips the entry.
            if best.tp_move:
                tp_f = float(tp_px)
                if not (tp_f > entry if best.side == "long" else tp_f < entry):
                    log(f"진입 스킵: {best.symbol} {best.side} 익절가가 반올림 "
                        f"후 {tp_px} (진입가 {entry})로 방향이 어긋남")
                    continue
            amount = _round_down_to_lot(max(notional / entry, 0), lot)
            if amount * entry < min_usd:
                log(f"진입 스킵: {best.symbol} 수량이 최소 주문(${min_usd}) 미달")
                continue
            log(f"진입 판단: {best.symbol} {best.side} ~${notional:.0f} "
                f"(승률 {best.win_rate:.0%}, 신호 {best.signal})")
            if not dry:
                # The sizing above assumed margin = notional / lev, but that
                # only holds if the exchange actually applies lev to this
                # market; the account may carry a different stored leverage
                # from an earlier session or the exchange default. Set it,
                # and on failure read it back (an "already set" rejection and
                # a 429 raise the same error). Unconfirmed means the margin
                # math is unknown: skip the entry, never guess. Same pattern
                # as bracket_trader's H2 fix.
                lev_ok = True
                try:
                    client.update_leverage(best.symbol, lev)
                except PacificaError:
                    lev_ok = False
                if not lev_ok:
                    confirmed_lev = False
                    try:
                        resp = client.get_account_settings()
                        rows = resp.get("margin_settings") \
                            if isinstance(resp, dict) else resp
                        for s2 in rows or []:
                            if isinstance(s2, dict) \
                                    and s2.get("symbol") == best.symbol:
                                confirmed_lev = int(float(
                                    s2.get("leverage") or 0)) == lev
                                break
                    except Exception:
                        confirmed_lev = False
                    if not confirmed_lev:
                        log(f"진입 스킵: {best.symbol}, 레버리지 {lev}배 설정 "
                            f"실패 후 확인 불가 (증거금 계산이 어긋난 채로 "
                            f"열지 않음)")
                        continue
                try:
                    client.create_market_order(
                        best.symbol, entry_side, str(amount), "0.5",
                        builder_code=policy.get("builder_code", ""),
                        take_profit_price=tp_px if best.tp_move else "",
                        stop_loss_price=sl_px)
                    # 체결가 반영(M5): 기록·채점 기준가를 스캔 시점 mid가 아니라
                    # 실제 평균 체결가로. 조회 실패 시 스캔가 유지(폴백).
                    fill_entry = entry
                    try:
                        for p2 in client.get_positions():
                            if p2.get("symbol") == best.symbol:
                                fp = float(p2.get("entry_price") or 0)
                                if fp > 0:
                                    fill_entry = fp
                                break
                    except Exception:
                        pass
                    # Fresh position: drop any stale aftercare/ratchet memory
                    # left by a previous position in this symbol (review M2).
                    st.setdefault("aftercare", {}).pop(best.symbol, None)
                    st.setdefault("opened", []).append({
                        "symbol": best.symbol, "side": best.side,
                        "signal": best.signal, "win_rate": best.win_rate,
                        "entry_price": fill_entry,
                        "horizon_hours": best.horizon_hours,
                        # 사후 원인분석용 진입 맥락 (왜 이겼나/졌나 판단 재료)
                        "interval": best.interval,
                        "regime": regime.get("label"),
                        "n_samples": best.n_samples,
                        # 이 거래가 걸었던 위험(명목가 × 손절폭)을 남긴다.
                        # 나중에 손익을 R 단위로 환산해, 레버리지·자본이 달라진
                        # 시점의 거래들과도 같은 잣대로 비교할 수 있게 한다.
                        "notional": round(notional, 4),
                        "sl_move": round(sl_pct, 6),
                        "leverage": lev,
                        # 예측봉 기록 (사용자 루트 2026-08-07): 진입 시점에
                        # "지평의 1/3·2/3·끝에서 가격이 여기까지 간다"를 그려두고,
                        # 채점 때 실제와 대조한다. 앞쪽에 실리는 경로((k)^0.8)는
                        # 예측기(chart_forecast.simulate_candles)와 같은 모양.
                        "pred": {"target_move": round(tp_pct, 6),
                                 "checks": [
                                     {"h": round(best.horizon_hours * f, 1),
                                      "px": round(fill_entry * (1 + (tp_pct if best.side == "long" else -tp_pct) * (f ** 0.8)), 8)}
                                     for f in (1/3, 2/3, 1.0)]},
                        # UTC로 기록, 일일 결산의 날짜 매칭(UTC 기준)과 맞춘다
                        "at": datetime.now(timezone.utc).isoformat()})
                    notify.send(f"🤖 {agent_name()} 진입: {best.symbol} {best.side} "
                                f"~${notional:.0f} · 손절 {sl_px}/익절 {tp_px} · "
                                f"신호 {best.signal}(승률 {best.win_rate:.0%})")
                    held.add(best.symbol)
                    slots -= 1
                    trading_budget -= amount * entry
                    avail_margin -= need_margin
                    net_exposure += (amount * entry
                                     if best.side == "long" else -amount * entry)
                except PacificaError as e:
                    log(f"진입 실패: {e}")
            else:
                log(f"[DRY] 진입 스킵, 손절 {sl_px} / 익절 {tp_px}")
                held.add(best.symbol)
                slots -= 1
                # 실거래와 같은 식(amount×entry)으로 차감, dry와 실거래의
                # 예산 소진 속도가 갈리지 않게 (M8)
                trading_budget -= amount * entry
                avail_margin -= need_margin
                net_exposure += (amount * entry
                                 if best.side == "long" else -amount * entry)

    # 주기적 성과 보고
    if st["cycles"] % int(policy.get("report_every_cycles", 8)) == 0:
        report(client, st, policy, push=True)
    save_state(st)


def report(client: PacificaClient, st: dict, policy: dict, push: bool = False) -> str:
    eq = equity(client)              # user-facing capital: balance + Print
    # peak/day baselines are kept on the exchange-only basis (review M5), so
    # the percentages must compare against exchange equity, not the combined
    # figure, or a live Print game would skew them.
    ex_eq = exchange_equity(client)
    peak = st.get("peak_equity") or ex_eq
    day0 = st.get("day_start_equity") or ex_eq
    positions = client.get_positions()
    day_pct = (ex_eq / day0 - 1) if day0 > 0 else 0.0
    peak_pct = (ex_eq / peak - 1) if peak > 0 else 0.0
    lines = [
        f"📊 {agent_name()} 성과 보고",
        f"  현재 자본: {eq:,.2f}",
        f"  오늘: {day_pct:+.2%} / 고점대비: {peak_pct:+.2%}",
        f"  보유 포지션: {len(positions)}개",
        f"  누적 사이클: {st.get('cycles',0)} · 상태: "
        + ("🛑 완전정지" if st.get("halted") else "가동 중"),
        f"  사이즈 배율: {float(st.get('size_scale',1.0)):.0%}"
        + (f" · 자가정지 신호: {', '.join(st.get('banned_signals',[]))}"
           if st.get("banned_signals") else ""),
    ]
    graded = st.get("graded", {})
    if graded:
        parts = [f"{s.split()[0]} {g['win']}/{g['total']}"
                 for s, g in list(graded.items())[:4]]
        lines.append(f"  신호별 실적: {' · '.join(parts)}")
    # 원인분석 요약, 수익/손실이 왜 났나 (자가진화 루프의 피드백)
    try:
        from .postmortem import summarize
        pm = summarize(days=30)
        if pm.get("n"):
            lines.append(f"  원인분석 {pm['n']}건 · 승률 {pm['win_rate']:.0%}")
            if pm.get("win_by_attribution"):
                wa = pm["win_by_attribution"]
                lines.append("  이긴 이유: " + " · ".join(
                    f"{k} {v}" for k, v in list(wa.items())[:3]))
            if pm.get("loss_by_attribution"):
                la = pm["loss_by_attribution"]
                lines.append("  진 이유: " + " · ".join(
                    f"{k} {v}" for k, v in list(la.items())[:3]))
    except Exception:
        pass
    for p in positions[:5]:
        lines.append(f"    {p.get('symbol')} {p.get('side')} {p.get('amount')} "
                     f"@ {p.get('entry_price')}")
    txt = "\n".join(lines)
    if push:
        notify.send(txt)
    return txt


# 모드 프리셋, mode 값에 따라 정책을 덮어쓴다 (명시된 값은 사용자 우선)
MODE_PRESETS = {
    "aggressive": {   # 테스트넷 학습용: 많이 거래 = 데이터 폭증
        # 0.52 → 0.45 (2026-08-07 실측). 방향 승률의 실측 천장이 낮다. 강한
        # 종합투표)라 52%는 사실상 전員 차단선이었다, 이틀간 진입 0건, LIT
        # 1d 스퀴즈(EV +2.21%·엣지 +1.13%p·승률 49%)까지 차단. 하네스 7년:
        # 낮은 문턱 쪽이 나았다 (+21%).
        # 0.45와 0이 결과 동일 = 순수엣지 +2%p 관문이 바닥을 지킨다.
        "min_win_rate": 0.45,
        # 감시목록 예외 해제(0). 이 예외는 '이 종목의 이 신호만 유독 승률이
        # 높다'(예전 SOL 86%)를 놓치지 않으려던 장치인데, 승률이 워크포워드
        # 실측(신호×시간봉 단위, 전 종목 통합)으로 바뀌면서 같은 신호면 모든
        # 종목이 같은 숫자가 됐다. 그래서 '예외적 기회'를 못 가려내고, 대신
        # 점등 필수를 통째로 우회한다, 가장 높게 나온 RSI<20 극과매도가 지금
        # 켜지지 않았는데도 전 종목에서 진입돼버린다. 점등 요구와 정면충돌.
        "watchlist_entry_win_rate": 0.0,
        # 점등 필수. 예전엔 False였다, "지금 안 켜졌어도 통계 우위면 진입"인데,
        # 그건 이미 지나간 자리에 뒤늦게 타는 것이다. 낮은 승률기준(45%)의 유일한
        # 방어가 승률 숫자뿐이던 시절의 설정이고, 지금은 증거 수축이 품질을 맡는다.
        # 역할을 나눈다: 수축=품질(근거 없는 신호 하향), 점등=타이밍(지금 켜진 자리).
        "require_live_signal": True,
        # 동시 보유 6개. 예전엔 3개였다, 위험 기반 사이징이 거래당 자본의 2%를
        # 걸어서 6개면 한 번에 12%가 물렸기 때문이다. 2026-08-05 스윙 전환으로
        # 포지션 크기를 max_position_usd $150(증거금 $30 × 5배)로 못 박았고
        # 손절이 1.5%라, 이제 1건당 위험이 $2.25 = 자본의 0.67%다. 6개 전부
        # 손절나도 4%뿐이라 옛 제약이 사라졌다. 슬롯을 늘려야 변동성 관문을
        # 통과한 좋은 셋업을 놓치지 않는다.
        "max_concurrent": 6,
        "loop_interval_min": 15,        # 15분마다 (더 자주 판단)
        "observe_universe": 0,          # 0 = 파시피카 전체 perp 순회 관측
        "observe_batch": 25,            # 사이클당 25개씩 돌아가며 (전체 커버)
        "funding_min_apr": 0.10,
    },
    "careful": {      # 메인넷 실전용: 안 잃는 게 최우선
        # 62%·72%는 백테 승률이 부풀려져 있던 시절 값이다. 워크포워드 실측에서
        # 이 시장의 승률 천장은 60% 아래이고, 정책 관문을 다
        # 통과하는 조합은 그 바로 아래 구간에 있다. 62%를 요구하면 통과 조합이
        # 0개가 되어 careful 로 바꾸는 순간 거래가 완전히 멈춘다, '보수적'이
        # 아니라 '고장'이다. 도달 가능한 범위에서 aggressive(52%)보다 높게 잡는다.
        # watchlist는 0, aggressive와 같은 이유(점등 필수를 우회함).
        "min_win_rate": 0.54,
        "require_live_signal": True,
        "watchlist_entry_win_rate": 0.0,
        "max_concurrent": 3,
        "loop_interval_min": 30,
        "observe_universe": 0,          # 관측(무위험 학습)은 실전에서도 전체 순회
        "observe_batch": 15,            # 다만 사이클당 부담은 더 가볍게
        "funding_min_apr": 0.15,
    },
}


def policy_path() -> str:
    """현재 폴더의 policy.yaml을 쓰되, 없으면 패키지 기본 정책으로 폴백.
    (PyPI로 설치한 사용자는 정책 파일이 없으므로 그냥 켜지도록.)"""
    if os.path.exists("policy.yaml"):
        return "policy.yaml"
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "policy_default.yaml")


def init_policy(dest: str = "policy.yaml") -> str:
    """편집용 정책 파일을 현재 폴더에 복사한다 (--init)."""
    import shutil
    if os.path.exists(dest):
        return f"{dest} 이미 있음, 덮어쓰지 않았습니다."
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "policy_default.yaml")
    shutil.copy(src, dest)
    return (f"{dest} 생성 완료. 자본·모드·리스크 한도를 확인·수정한 뒤 "
            f"python -m ocean_agent.autonomous 로 시작하세요.")


def _policy_mtime():
    """policy.yaml 수정시각. 못 읽으면 None (그 사이클은 재적용 안 함)."""
    try:
        return os.path.getmtime(policy_path())
    except OSError:
        return None


def _warn_duplicate_keys(path: str) -> None:
    """YAML silently keeps the LAST duplicate key; that once left the live
    account running on values the file's comments contradicted. Warn loudly
    so a stale block can never lie again.

    Top-level keys only: the pattern matches at column zero, so duplicates
    inside nested blocks are not detected. This warning staying silent does
    not guarantee the file is free of duplicates everywhere."""
    try:
        seen = set()
        for ln in open(path, encoding="utf-8", errors="replace"):
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:", ln)
            if not m:
                continue
            k = m.group(1)
            if k in seen:
                log(f"경고: policy 중복 키 '{k}' — 마지막 값이 적용됩니다")
            seen.add(k)
    except OSError:
        pass


def load_policy() -> dict:
    _warn_duplicate_keys(policy_path())
    with open(policy_path(), encoding="utf-8") as f:
        pol = yaml.safe_load(f)
    mode = pol.get("mode", "careful")
    preset = MODE_PRESETS.get(mode, {})
    # 모드가 튜닝 값(승률기준·점등요구·동시수·주기·관측범위)을 제어한다 → 프리셋 우선.
    # 파일은 그 외(자본·빌더코드·안전한도·적응파라미터)를 그대로 유지.
    # ⚠️ 프리셋에 있는 키는 policy.yaml에 뭘 써도 무시된다. 파일에서 바꾸려면
    #    MODE_PRESETS에서 그 키를 빼거나 프리셋 값을 고쳐야 한다.
    #    (예: policy.yaml에 뭘 적든 aggressive 프리셋의 min_win_rate 0.45,
    #     max_concurrent 6 이 실효값이 된다. 파일만 보면 알 수 없어 혼동을 부른다.)
    merged = {**pol, **preset}
    merged["_mode"] = mode
    return merged


def make_client(policy: dict) -> PacificaClient:
    from dotenv import load_dotenv
    load_dotenv()
    base = policy["base_url"]
    return PacificaClient(base, address=address_from_env(base),
                          private_key=api_key_from_env(base))


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="1사이클만")
    ap.add_argument("--report", action="store_true", help="성과만 출력")
    ap.add_argument("--resume", action="store_true", help="정지 해제")
    ap.add_argument("--dry", action="store_true", help="판단만, 주문 안 함")
    ap.add_argument("--close-all", action="store_true",
                    help="보유 포지션 전량 청산 + 방어선 기준을 현재 잔고로 재설정")
    ap.add_argument("--init", action="store_true",
                    help="편집용 policy.yaml을 현재 폴더에 생성")
    args = ap.parse_args()

    if args.init:
        print(init_policy())
        return

    policy = load_policy()
    # 라이브 데이터 파일이 올바른 네트워크(테스트넷/메인넷)로 분리되도록,
    # policy의 base_url을 data_file()이 읽는 환경변수에 먼저 반영한다.
    os.environ["PACIFICA_BASE_URL"] = policy["base_url"]
    # 옛 이름(.mustache_*) 데이터를 새 이름(.ocean_agent_*)으로 먼저 옮긴다.
    # 순서 중요: 이걸 먼저 해야 아래 태그 이전이 옮겨진 파일을 대상으로 돈다.
    _renamed = migrate_brand_rename()
    if _renamed:
        log(f"데이터 이름 이전({len(_renamed)}건): {', '.join(_renamed[:5])}"
            + (" 외" if len(_renamed) > 5 else ""))
    # 구버전 untagged 라이브 파일이 있으면 testnet 태그본으로 1회 자동 이전.
    _moved = migrate_legacy_data()
    if _moved:
        log(f"라이브 데이터 이전(→testnet): {', '.join(_moved)}")
    client = make_client(policy)
    # 빌더 수수료 동의, 콘솔에서만 1회 질문, 거절해도 매매에 영향 없음
    try:
        from .builder_consent import ensure_consent
        ensure_consent(client, policy.get("builder_code", ""))
    except Exception:
        pass
    st = load_state()

    if args.resume:
        st["halted"] = False
        st.pop("day_trading_stopped", None)
        save_state(st)
        print(f"{agent_name()} 재개됨."); return
    if args.close_all:
        # 보유 포지션을 전부 정리하고 방어선 기준을 현재 잔고로 다시 잡는다.
        # '깨끗하게 다시 시작'용, 옛 기준이 남아 있으면 새 자본에 맞지 않는
        # 손절선으로 돌게 된다(2026-08-04에 실제로 겪은 문제).
        pos = client.get_positions()
        if not pos:
            print("보유 포지션 없음.")
        else:
            print(f"보유 {len(pos)}건 청산합니다: "
                  + ", ".join(f"{p['symbol']}({p.get('side')})" for p in pos))
            n = close_all(client, policy.get("builder_code", ""), "수동 정리")
            print(f"청산 주문 {n}건 전송.")
            time.sleep(3)
        # Baselines are exchange-basis (review M5), reset them from the same.
        eq = exchange_equity(client)
        left = client.get_positions()
        if left:
            print(f"⚠️ 아직 {len(left)}건 남아 있습니다, 다시 실행하거나 "
                  f"거래소에서 직접 확인하세요: "
                  + ", ".join(p["symbol"] for p in left))
        if eq > 0:
            for k in ("start_equity", "day_start_equity", "peak_equity"):
                st[k] = eq
            st["last_equity"] = eq
            # Sync the stored Print face too, so the next cycle's Print
            # reconciliation does not shift the freshly reset baselines.
            face = _live_print_face(client)
            if face is not None:
                st["print_face_live"] = face
            st["halted"] = False
            st.pop("soft_halt_day", None)
            save_state(st)
            fatal = float(policy.get("fatal_loss_pct", 0.20))
            soft = float(policy.get("soft_halt_pct", 0.08))
            print(f"기준 재설정: 시작자본 ${eq:,.2f} · "
                  f"완전정지 ${eq * (1 - fatal):,.0f} · 소프트 ${eq * (1 - soft):,.0f}")
        else:
            print("⚠️ 잔고 조회 실패, 기준 재설정을 건너뜁니다.")
        return
    if args.report:
        print(report(client, st, policy)); return

    # Mirror of the bracket bot's X1 guard (review H4): one account, one
    # margin pool. A fresh bracket heartbeat means that bot holds the
    # account, so this one refuses to start beside it.
    import glob as _glob
    for _hb in _glob.glob(os.path.join(_PKG_OUTPUTS, "bracket_alive_*.json")):
        _age_min = (time.time() - os.path.getmtime(_hb)) / 60
        if _age_min < 10:
            _msg = (f"브래킷 봇이 {_age_min:.0f}분 전까지 살아있어 시작을 "
                    "거부합니다. 같은 계좌 증거금을 두 봇이 나눠 쓰면 안 "
                    "됩니다. 브래킷 봇을 끄고 다시 실행하세요 "
                    "(꺼진 봇의 흔적이면 10분 뒤 자동 해제).")
            log(_msg)
            notify.send(f"{agent_name()}: " + _msg)
            return

    net = "테스트넷" if "test-api" in policy["base_url"] else "메인넷"
    # 자본 표시: 고정값이면 숫자, live면 현재 지갑 잔고 연동임을 표시
    _fx = _fixed_capital(policy)
    cap_disp = f"{_fx:,.0f}" if _fx is not None \
        else f"지갑연동(현재 {exchange_equity(client):,.0f})"
    # 절전 방지, 봇이 도는 동안 PC가 잠들지 않게 (화면은 꺼져도 됨)
    awake = keep_awake_on() if not args.once else False
    log(f"{agent_name()} 시작, {net} / 모드 {policy.get('_mode','?').upper()} / "
        f"자본 {cap_disp} / 승률기준 {policy['min_win_rate']:.0%} / "
        f"동시 {policy['max_concurrent']}개 / 주기 {policy['loop_interval_min']}분 / "
        f"{'DRY' if args.dry else '실거래'}" + (" / 절전방지 ON" if awake else ""))
    try:
        pol_mtime = _policy_mtime()
        rate_limit_streak = 0     # 429 연속 횟수, 알림 스팸 방지에 쓴다
        while True:
            # policy.yaml 를 고쳤으면 재시작 없이 반영한다. 측정 결과(매트릭스·
            # 보정표·승률DB)는 이미 파일 수정시각 기준으로 자동 반영되는데 설정만
            # 재시작을 요구하면, 임계값 하나 바꾸려고 봇을 껐다 켜야 한다,
            # 껐다 켜는 동안 포지션이 방치되고, 실수로 두 개를 띄우는 사고도 났다.
            # 실패하면 옛 설정을 그대로 쓴다(편집 중 잘린 파일에 당하지 않게).
            mt = _policy_mtime()
            if mt and mt != pol_mtime:
                pol_mtime = mt
                try:
                    newpol = load_policy()
                    if newpol.get("base_url") != policy.get("base_url"):
                        # 네트워크가 바뀌면 클라이언트·데이터 경로가 전부 달라진다.
                        # 조용히 갈아타면 메인넷 상태로 테스트넷을 거래할 수 있다.
                        log("⚠️ policy의 base_url이 바뀌었습니다, 네트워크 전환은 "
                            "재시작이 필요합니다. 나머지 설정만 반영합니다.")
                        newpol["base_url"] = policy["base_url"]
                    changed = [k for k in set(newpol) | set(policy)
                               if newpol.get(k) != policy.get(k)]
                    policy = newpol
                    log("설정 반영(재시작 없이): "
                        + ", ".join(f"{k}={policy.get(k)}"
                                    for k in sorted(changed)[:8]))
                except Exception as e:
                    log(f"설정 재적용 실패, 기존 설정 유지: {e}")
            wait_min = int(policy.get("loop_interval_min", 30))
            try:
                keep_awake_on()   # 매 사이클 재확인 (일부 환경은 리셋됨)
                run_cycle(client, policy, st, args.dry)
                rate_limit_streak = 0
            except Exception as e:
                # 레이트리밋은 스스로 풀린다. 30분을 통째로 기다리면 그 사이의
                # 셋업을 놓치므로 5분 뒤 다시 시도하고, 알림도 연속으로 났을
                # 때만 보낸다 (2026-08-06: 자동 복구된 429 로 알림이 울렸다).
                rate_limited = "429" in str(e) or "레이트리밋" in str(e)
                log(f"사이클 오류: {e}")
                if rate_limited:
                    rate_limit_streak += 1
                    wait_min = 5
                    if rate_limit_streak >= 3:
                        notify.send(f"⚠️ {agent_name()} 레이트리밋 {rate_limit_streak}회 연속: {e}")
                else:
                    rate_limit_streak = 0
                    notify.send(f"⚠️ {agent_name()} 오류: {e}")
            if args.once:
                break
            time.sleep(wait_min * 60)
    finally:
        keep_awake_off()   # 봇 종료 시 절전 정책 원상복구


if __name__ == "__main__":
    main()
