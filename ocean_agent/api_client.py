"""Pacifica REST API 클라이언트.

- 공개 데이터: 서명 없이 GET
- 주문/계정 변경: 에이전트 키(또는 메인 키)로 서명해 POST
  에이전트 키의 공개키가 ADDRESS와 다르면 agent_wallet 필드를 자동으로 붙인다.
"""

import uuid

import requests

from .signing import keypair_from_base58, sign_operation


class PacificaError(Exception):
    pass


class PacificaClient:
    def __init__(self, base_url: str, address: str = "", private_key: str = ""):
        # Refuse plaintext HTTP: a downgraded endpoint would let a network
        # attacker read/alter signed requests (MITM). Localhost is exempted
        # only for developer testing against a local mock.
        u = base_url.rstrip("/")
        if not (u.startswith("https://")
                or u.startswith("http://localhost")
                or u.startswith("http://127.0.0.1")):
            raise PacificaError(
                f"거부: 안전하지 않은 주소({u[:40]}). PACIFICA_BASE_URL 은 "
                f"https:// 여야 합니다 (중간자 공격 방어).")
        self.base = u + "/api/v1"
        self.address = address
        # A malformed key (typo, truncated paste) must not kill read-only
        # use: markets, prices, books and bars need no signature. The parse
        # error is kept and surfaced only when a signed call is attempted.
        self.keypair = None
        self.key_error = ""
        if private_key:
            try:
                self.keypair = keypair_from_base58(private_key)
            except Exception:
                self.key_error = (
                    "PACIFICA_API_KEY 형식이 올바르지 않습니다 (오타나 일부 "
                    "누락 가능성). .env 의 키를 다시 확인해 주세요. 시세 조회 "
                    "같은 읽기 기능은 계속 사용할 수 있습니다.")
        self.session = requests.Session()

    # ---------- 공개 GET ----------

    @staticmethod
    def _parse_json(r, ctx: str) -> dict:
        """레이트리밋(429) 등 비JSON 응답을 명확한 오류로 변환."""
        try:
            return r.json()
        except ValueError:
            raise PacificaError(
                f"{ctx} 비정상 응답 (HTTP {r.status_code}), 레이트리밋 가능성. "
                f"본문: {r.text[:80]!r}")

    # 429 재시도 대기(초). 무거운 측정(rematrix·harness)이 같은 API 를 두드리면
    # 10초 한 번으로는 못 넘긴다, 2026-08-06 에 사이클 13개가 이걸로 날아갔다.
    _RETRY_BACKOFF = (5, 20, 45)

    def _get(self, path: str, params: dict | None = None) -> dict:
        import time as _t
        for attempt in range(len(self._RETRY_BACKOFF) + 1):
            # Network-layer failures (timeout, DNS, connection reset) are
            # re-raised as PacificaError so callers' `except PacificaError`
            # safety handling still applies (review H7).
            try:
                r = self.session.get(f"{self.base}/{path}", params=params,
                                     timeout=15)
            except requests.exceptions.RequestException as e:
                raise PacificaError(f"GET /{path} 네트워크 오류: {e}") from e
            if r.status_code == 429 and attempt < len(self._RETRY_BACKOFF):
                _t.sleep(self._RETRY_BACKOFF[attempt])
                continue
            body = self._parse_json(r, f"GET /{path}")
            if not body.get("success", False):
                raise PacificaError(f"GET /{path} 실패: {body.get('error')} (code={body.get('code')})")
            return body["data"]
        raise PacificaError(f"GET /{path} 레이트리밋 지속 (429)")

    def get_markets(self) -> list[dict]:
        """전 마켓 스펙 (perp + spot). funding_rate, lot_size, instrument_type 포함."""
        return self._get("info")

    def get_prices(self) -> list[dict]:
        """전 마켓 실시간 가격/펀딩비 (funding, next_funding, mid, mark 등)."""
        return self._get("info/prices")

    def get_account(self) -> dict:
        return self._get("account", {"account": self.address})

    def get_positions(self) -> list[dict]:
        return self._get("positions", {"account": self.address})

    def get_open_orders(self) -> list[dict]:
        """Open orders for this account, including TP/SL stop orders that
        were attached to an entry. Read-only; used to verify that a bracket
        the entry order asked for actually exists on the exchange."""
        r = self._get("orders", {"account": self.address})
        return r if isinstance(r, list) else []

    # ---------- 빌더 코드 (수수료 동의) ----------

    def get_builder_approvals(self) -> list:
        """이 계정이 승인한 빌더 코드 목록."""
        r = self._get("account/builder_codes/approvals",
                      {"account": self.address})
        return r if isinstance(r, list) else []

    def approve_builder_code(self, builder_code: str,
                             max_fee_rate: str) -> dict:
        """빌더 코드 승인, 반드시 사용자가 동의한 뒤에만 호출할 것.

        max_fee_rate 는 소수 문자열("0.0001" = 1bps). 사용자 보호를 위해
        실제 빌더 수수료율보다 낮으면 거래소가 주문을 거부한다."""
        return self._signed_post("account/builder_codes/approve",
                                 "approve_builder_code",
                                 {"builder_code": builder_code,
                                  "max_fee_rate": str(max_fee_rate)})

    # ---------- 서명 POST ----------

    def _signed_post(self, path: str, op_type: str, payload: dict) -> dict:
        if not self.keypair:
            raise PacificaError(
                self.key_error
                or "API 키가 없어 주문을 보낼 수 없습니다 "
                   "(.env의 PACIFICA_API_KEY에 app.pacifica.fi/apikey 발급 키 입력)")
        import time as _t
        signer_pubkey = str(self.keypair.pubkey())
        for attempt in (1, 2):
            # 재시도 시 서명도 새로 (서명에 타임스탬프가 들어가므로 재사용 불가)
            signed = sign_operation(op_type, payload, self.keypair)
            request = {"account": self.address, **signed, **payload}
            if signer_pubkey != self.address:
                request["agent_wallet"] = signer_pubkey
            # Network-layer failures become PacificaError (review H7).
            try:
                r = self.session.post(f"{self.base}/{path}", json=request,
                                      timeout=15)
            except requests.exceptions.RequestException as e:
                raise PacificaError(f"POST /{path} 네트워크 오류: {e}") from e
            if r.status_code == 429 and attempt == 1:
                _t.sleep(10)          # 레이트리밋, 한 번만 쉬고 재서명·재시도
                continue
            body = self._parse_json(r, f"POST /{path}")
            # success 키가 없으면 실패로 간주(안전). 200인데 스펙 밖 응답이
            # 오면 '주문 성공'으로 오독하지 않는다, 돈이 걸린 경로라 보수적으로.
            if r.status_code != 200 or not body.get("success", False):
                raise PacificaError(f"POST /{path} 실패 ({r.status_code}): {r.text}")
            return body.get("data", body)
        raise PacificaError(f"POST /{path} 레이트리밋 지속 (429)")

    # 하드 사이징 가드, 한 주문의 명목가 절대 상한(USD). 봇 정책($3,000)보다
    # 넉넉히 높게 두되, '폭주'는 확실히 막는 최후 방어선. 봇·스크립트·웹 등
    # 어느 경로로 온 주문이든 이 함수를 통과하므로 여기서 한 번에 막는다.
    # reduce_only(청산)는 예외, 큰 포지션 정리를 막으면 안 됨.
    MAX_ORDER_NOTIONAL_USD = 20_000

    def create_market_order(self, symbol: str, side: str, amount: str,
                            slippage_percent: str, reduce_only: bool = False,
                            builder_code: str = "",
                            take_profit_price: str = "",
                            stop_loss_price: str = "",
                            trigger_price_type: str = "mark_price",
                            take_profit_limit: bool = False,
                            stop_loss_limit_price: str = "") -> dict:
        """시장가 주문. side는 'bid'(매수) 또는 'ask'(매도).

        take_profit_price / stop_loss_price 를 넘기면 거래소에 TP/SL을 함께
        등록한다, 봇/PC가 꺼져도 거래소가 해당 가격에서 포지션을 청산한다.

        builder_code가 계정에서 미승인 상태라 거부되면, 사용자를 막지 않도록
        코드 없이 1회 재시도한다 (승인된 계정에서만 수수료가 붙는 구조).

        하드 가드: 신규 주문의 명목가가 MAX_ORDER_NOTIONAL_USD를 넘으면 거부.
        (2026-07-24 BTC $355k 폭주 재발 방지, 원인 불문 최후 차단)
        """
        # ── 폭주 방어: 신규 주문 명목가 상한 검사 (청산은 제외) ──
        # fail-closed: 가격을 확인할 수 없으면(조회 실패·미상장 심볼) 통과가 아니라
        # 거부한다. 가드를 검증할 수 없는 주문을 통과시키는 것이 진짜 위험이다.
        if not reduce_only:
            amt_f = abs(float(amount))
            px = 0.0
            for p in self.get_prices():
                if p.get("symbol") == symbol:
                    px = float(p.get("mark") or p.get("mid") or 0)
                    break
            if px <= 0:
                raise PacificaError(
                    f"🛑 주문 거부(하드 가드): {symbol} 가격을 확인할 수 없어 "
                    f"명목가 상한 검사가 불가합니다 (미상장이거나 가격 조회 실패). "
                    f"가격이 조회되는 상태에서 다시 시도하세요.")
            if amt_f * px > self.MAX_ORDER_NOTIONAL_USD:
                raise PacificaError(
                    f"🛑 주문 거부(하드 가드): {symbol} {amt_f} × ${px:,.0f} = "
                    f"${amt_f*px:,.0f} > 상한 ${self.MAX_ORDER_NOTIONAL_USD:,.0f}. "
                    f"폭주 방어. 의도된 대형 주문이면 MAX_ORDER_NOTIONAL_USD 조정.")
        payload = {
            "symbol": symbol,
            "side": side,
            "amount": str(amount),
            "slippage_percent": str(slippage_percent),
            "reduce_only": reduce_only,
            "client_order_id": str(uuid.uuid4()),
        }
        if builder_code:
            payload["builder_code"] = builder_code
        if take_profit_price:
            payload["take_profit"] = {"stop_price": str(take_profit_price),
                                      "trigger_price_type": trigger_price_type}
            # Limit execution for the TP leg (user decision 08-24): when
            # triggered the exchange places a LIMIT at the same price instead
            # of a market order, so the fill pays maker fee (0.015% vs 0.04%)
            # and cannot slip past the line. The exchange still manages the
            # TP/SL pair together. Documented risk: a wick that touches the
            # trigger without trading through can leave the limit unfilled,
            # in which case the position runs on to the SL or the 24h expiry.
            # The price is the SAME tp, so the frozen geometry is untouched.
            if take_profit_limit:
                payload["take_profit"]["limit_price"] = str(take_profit_price)
        if stop_loss_price:
            payload["stop_loss"] = {"stop_price": str(stop_loss_price),
                                    "trigger_price_type": trigger_price_type}
            if stop_loss_limit_price:
                payload["stop_loss"]["limit_price"] = str(stop_loss_limit_price)
        try:
            return self._signed_post("orders/create_market",
                                     "create_market_order", payload)
        except PacificaError as e:
            msg = str(e).lower()
            if builder_code and ("builder" in msg or "fee_rate" in msg
                                 or "max_fee" in msg):
                payload.pop("builder_code", None)
                payload["client_order_id"] = str(uuid.uuid4())
                return self._signed_post("orders/create_market",
                                         "create_market_order", payload)
            raise

    def _sign_action_data(self, op_type: str, payload: dict) -> dict:
        """배치 액션 하나의 서명된 data 블록을 만든다 (개별 서명)."""
        signed = sign_operation(op_type, payload, self.keypair)
        data = {"account": self.address, **signed, **payload}
        signer = str(self.keypair.pubkey())
        if signer != self.address:
            data["agent_wallet"] = signer
        return data

    def batch_market_orders(self, orders: list[dict],
                            builder_code: str = "") -> dict:
        """여러 시장가 주문을 원자적 배치(Atomics)로 전송, 한 번에, 순서대로,
        남의 주문에 끼이지 않고 실행. OI 헤지 양다리나 바스켓 진입에 최적.

        orders: [{"symbol","side","amount","slippage_percent"?,"reduce_only"?}, ...]
        최대 10개. 개별 서명되어 actions 배열로 묶인다.
        """
        if not self.keypair:
            raise PacificaError(self.key_error
                                or "API 키가 없어 주문을 보낼 수 없습니다")
        if not 1 <= len(orders) <= 10:
            raise PacificaError("배치는 1~10개 주문만 가능합니다")
        # 하드 가드, 배치 내 각 신규 주문도 명목가 상한 검사 (청산 제외).
        # fail-closed: 가격을 확인할 수 없으면 통과가 아니라 거부한다. 단건
        # create_market_order 와 동일한 정책, 가드를 검증 못 하는 주문을
        # 통과시키는 것이 폭주 우회로가 된다(2026-08-10 감사에서 fail-open 발견).
        try:
            px_map = {p.get("symbol"): float(p.get("mark") or p.get("mid") or 0)
                      for p in self.get_prices()}
        except Exception as e:
            raise PacificaError(
                f"🛑 배치 주문 거부(하드 가드): 시세를 확인할 수 없어 명목가 "
                f"상한 검사가 불가합니다 ({e}).")
        for o in orders:
            if o.get("reduce_only"):
                continue
            px = px_map.get(o["symbol"], 0)
            if px <= 0:
                raise PacificaError(
                    f"🛑 배치 주문 거부(하드 가드): {o['symbol']} 가격을 확인할 "
                    f"수 없어 상한 검사 불가 (미상장이거나 시세 조회 실패).")
            notl = abs(float(o["amount"])) * px
            if notl > self.MAX_ORDER_NOTIONAL_USD:
                raise PacificaError(
                    f"🛑 배치 주문 거부(하드 가드): {o['symbol']} "
                    f"${notl:,.0f} > 상한 ${self.MAX_ORDER_NOTIONAL_USD:,.0f}")
        import time as _t
        # 주문 ID는 재시도 전에 한 번만 발급한다. 루프 안에서 새로 뽑으면
        # 1차가 체결된 뒤 429가 돌아왔을 때 2차가 다른 ID로 또 체결돼
        # 델타뉴트럴 양다리가 2배가 된다. 단건 _signed_post 와 같은 규율
        # (재서명은 하되 ID는 유지 = 멱등). 2026-08-10 검토 B4.
        order_ids = [str(uuid.uuid4()) for _ in orders]
        for attempt in (1, 2):
            # 재시도 시 서명만 새로 (서명에 타임스탬프가 들어가 재사용 불가)
            actions = []
            for o, oid in zip(orders, order_ids):
                payload = {
                    "symbol": o["symbol"],
                    "side": o["side"],
                    "amount": str(o["amount"]),
                    "slippage_percent": str(o.get("slippage_percent", "0.5")),
                    "reduce_only": bool(o.get("reduce_only", False)),
                    "client_order_id": oid,
                }
                if builder_code:
                    payload["builder_code"] = builder_code
                actions.append({"type": "CreateMarket",
                                "data": self._sign_action_data(
                                    "create_market_order", payload)})
            # Network-layer failures become PacificaError (review H7).
            try:
                r = self.session.post(f"{self.base}/orders/batch",
                                      json={"actions": actions}, timeout=20)
            except requests.exceptions.RequestException as e:
                raise PacificaError(
                    f"POST /orders/batch 네트워크 오류: {e}") from e
            if r.status_code == 429 and attempt == 1:
                _t.sleep(10)      # 레이트리밋, 한 번만 쉬고 재서명·재시도
                continue
            body = self._parse_json(r, "POST /orders/batch")
            # success 없으면 실패로 간주(안전), 배치도 돈이 걸린 경로.
            if r.status_code != 200 or not body.get("success", False):
                # 빌더 코드 미승인이면 코드 없이 재시도
                if builder_code and "builder" in r.text.lower():
                    return self.batch_market_orders(orders, builder_code="")
                raise PacificaError(f"배치 주문 실패 ({r.status_code}): {r.text}")
            return body.get("data", body)
        raise PacificaError("POST /orders/batch 레이트리밋 지속 (429)")

    def create_limit_order(self, symbol: str, side: str, amount: str,
                           price: str, tif: str = "GTC",
                           reduce_only: bool = False,
                           builder_code: str = "",
                           take_profit_price: str = "",
                           stop_loss_price: str = "",
                           trigger_price_type: str = "mark_price",
                           take_profit_limit: bool = False,
                           stop_loss_limit_price: str = "") -> dict:
        """지정가 주문 (orders/create, 서명 create_order). side는 bid/ask.

        시장가와 같은 폭주 방어를 적용하고, TP/SL 동봉도 같은 모양으로 받는다.
        stop_loss_limit_price 가 오면 손절도 트리거-지정가가 된다: 트리거는
        stop_loss_price, 지정가는 그보다 버퍼만큼 나쁜 가격. 버퍼가 사실상
        체결을 보장하면서 슬리피지 상한을 만든다. (08-24 사용자 결정)
        """
        if not reduce_only:
            amt_f = abs(float(amount))
            px = float(price)
            if amt_f * px > self.MAX_ORDER_NOTIONAL_USD:
                raise PacificaError(
                    f"🛑 주문 거부(하드 가드): {symbol} {amt_f} × ${px:,.0f} = "
                    f"${amt_f*px:,.0f} > 상한 ${self.MAX_ORDER_NOTIONAL_USD:,.0f}.")
        payload = {
            "symbol": symbol,
            "side": side,
            "amount": str(amount),
            "price": str(price),
            "tif": tif,
            "reduce_only": reduce_only,
            "client_order_id": str(uuid.uuid4()),
        }
        if builder_code:
            payload["builder_code"] = builder_code
        if take_profit_price:
            payload["take_profit"] = {"stop_price": str(take_profit_price),
                                      "trigger_price_type": trigger_price_type}
            if take_profit_limit:
                payload["take_profit"]["limit_price"] = str(take_profit_price)
        if stop_loss_price:
            payload["stop_loss"] = {"stop_price": str(stop_loss_price),
                                    "trigger_price_type": trigger_price_type}
            if stop_loss_limit_price:
                payload["stop_loss"]["limit_price"] = str(stop_loss_limit_price)
        try:
            return self._signed_post("orders/create", "create_order", payload)
        except PacificaError as e:
            msg = str(e).lower()
            if builder_code and ("builder" in msg or "fee_rate" in msg
                                 or "max_fee" in msg):
                payload.pop("builder_code", None)
                payload["client_order_id"] = str(uuid.uuid4())
                return self._signed_post("orders/create", "create_order",
                                         payload)
            raise

    def cancel_order(self, symbol: str, order_id=None,
                     client_order_id: str = "") -> dict:
        """미체결 주문 취소 (orders/cancel, 서명 cancel_order)."""
        if order_id is None and not client_order_id:
            raise PacificaError("order_id 또는 client_order_id 가 필요합니다")
        payload = {"symbol": symbol}
        if order_id is not None:
            payload["order_id"] = int(order_id)
        else:
            payload["client_order_id"] = client_order_id
        return self._signed_post("orders/cancel", "cancel_order", payload)

    def set_position_tpsl(self, symbol: str, side: str,
                          take_profit_price: str = "",
                          stop_loss_price: str = "",
                          trigger_price_type: str = "mark_price",
                          take_profit_limit: bool = False,
                          stop_loss_limit_price: str = "") -> dict:
        """이미 열려 있는 포지션에 TP/SL을 사후 부착한다.

        side는 '포지션의 방향'(bid=롱, ask=숏)을 넘긴다. 파시피카 TP/SL 주문은
        포지션을 청산하는 방향(반대 side)을 요구하므로, 여기서 반대로 변환한다.
        (호출자가 포지션 side를 넘겨도 안전하도록 함수가 책임진다. 포지션 ask →
        스탑주문 bid. 이걸 안 뒤집으면 "Invalid stop order side" 422.)
        TP/SL 중 최소 하나는 필요. 거래소에 등록되어 봇/PC 꺼져도 작동한다.
        """
        if not take_profit_price and not stop_loss_price:
            raise PacificaError("TP/SL 중 최소 하나는 지정해야 합니다")
        close_side = "ask" if side == "bid" else "bid"
        payload = {"symbol": symbol, "side": close_side}
        if take_profit_price:
            payload["take_profit"] = {"stop_price": str(take_profit_price),
                                      "trigger_price_type": trigger_price_type,
                                      "client_order_id": str(uuid.uuid4())}
            if take_profit_limit:
                payload["take_profit"]["limit_price"] = str(take_profit_price)
        if stop_loss_price:
            payload["stop_loss"] = {"stop_price": str(stop_loss_price),
                                    "trigger_price_type": trigger_price_type,
                                    "client_order_id": str(uuid.uuid4())}
            if stop_loss_limit_price:
                payload["stop_loss"]["limit_price"] = str(stop_loss_limit_price)
        return self._signed_post("positions/tpsl", "set_position_tpsl", payload)

    # ---------- Print (⚠️ 비공개 API, 웹앱과 동일 경로, 예고 없이 바뀔 수 있음) ----------
    # Print의 내부 이름은 'game'. 목표가(strike)에 걸어두고 기다리는 동안
    # 프리미엄(이자)을 받는 옵션형 상품. 24시간 체크포인트에 체결 판정.

    # ---------- 계정 설정 (마켓별) ----------

    def get_account_settings(self) -> list[dict]:
        """마켓별 레버리지·마진모드 설정. 봇의 정책 상한과 별개로, 거래소 쪽
        설정이 낮으면 그쪽이 실제 상한이 된다."""
        return self._get("account/settings", {"account": self.address})

    def update_leverage(self, symbol: str, leverage: int) -> dict:
        """마켓 레버리지 변경 (서명: update_leverage).
        정책의 max_leverage 를 올려도 여기가 낮으면 주문이 그 값으로 제한된다."""
        return self._signed_post("account/leverage", "update_leverage",
                                 {"symbol": symbol, "leverage": int(leverage)})

    def update_margin_mode(self, symbol: str, is_isolated: bool) -> dict:
        """마진 모드 변경 (서명: update_margin_mode).
        격리는 포지션마다 증거금을 격벽으로 나눠, 한 포지션 사고가 계좌 전체로
        번지는 것을 막는다. 열린 포지션이 있으면 거부될 수 있다."""
        return self._signed_post("account/margin", "update_margin_mode",
                                 {"symbol": symbol, "is_isolated": bool(is_isolated)})

    def print_games(self) -> list[dict]:
        """Print 마켓 목록 (예: BTC_24H, ETH_24H)과 예치 한도."""
        return self._get("game/config").get("configs", [])

    def print_sim(self, game: str, deposit_usd: str, direction: int,
                  strike_price: str, leverage: str) -> dict:
        """Print 주문 시뮬레이션, 프리미엄(수익), 내재변동성, 청산가 견적.
        direction: 0=롱(아래 목표가에 매수 대기), 1=숏(위 목표가에 매도 대기)."""
        return self._get("game/sim", {
            "game": game, "deposit_amount": str(deposit_usd),
            "direction": int(direction), "strike_price": str(strike_price),
            "leverage": str(leverage)})

    def print_positions(self) -> dict:
        """내 Print 예치 현황."""
        return self._get("game/accounts", {"account": self.address})

    def print_open(self, game: str, deposit_usd: str, direction: int,
                   strike_price: str, leverage: str) -> dict:
        """Print 주문 (서명: deposit_to_game). 웹 UI와 동일한 요청."""
        return self._signed_post("game/deposit", "deposit_to_game", {
            "game": game, "deposit_amount": str(deposit_usd),
            "direction": int(direction), "strike_price": str(strike_price),
            "leverage": str(leverage)})

    def print_end(self, game_account: str) -> dict:
        """Print 조기 종료 (서명: end_game).

        서버가 요구하는 필드는 마켓 이름(game)이 아니라 예치 계정 주소
        (game_account)다. 예전 코드는 {"game": ...}를 보내 항상 400으로
        실패했다, 즉 조기 종료가 한 번도 동작한 적이 없다.
        주소는 print_positions()의 game_accounts[].address 값이다."""
        return self._signed_post("game/end", "end_game",
                                 {"game_account": game_account})

    def print_withdraw(self, game_account: str, amount: str = "") -> dict:
        """Print 예치금 회수 (서명: withdraw_from_game). game_end와 같은 이유로
        마켓 이름이 아니라 예치 계정 주소를 보낸다."""
        payload = {"game_account": game_account}
        if amount:
            payload["amount"] = str(amount)
        return self._signed_post("game/withdraw", "withdraw_from_game", payload)
