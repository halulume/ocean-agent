"""Pacifica MCP 서버(@pacifica-fi/mcp-server)를 stdio로 띄워서 도구를 호출하는 클라이언트.

MCP stdio 프로토콜은 한 줄에 하나의 JSON-RPC 메시지를 주고받는다.
외부 의존성 없이 subprocess 파이프로 직접 구현했다.
"""

import json
import os
import shutil
import subprocess
import threading


class MCPError(Exception):
    pass


class MCPStdioClient:
    """범용 MCP stdio 클라이언트 (initialize → tools/list → tools/call)."""

    def __init__(self, command: list[str], env: dict | None = None):
        full_env = {**os.environ, **(env or {})}
        self.proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=full_env,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self._id = 0
        self._lock = threading.Lock()
        self._initialize()

    def _send(self, method: str, params: dict | None = None,
              notification: bool = False) -> dict | None:
        with self._lock:
            msg = {"jsonrpc": "2.0", "method": method}
            if params is not None:
                msg["params"] = params
            if not notification:
                self._id += 1
                msg["id"] = self._id
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()
            if notification:
                return None
            # 내 요청 id에 대한 응답이 올 때까지 읽는다 (서버발 알림은 무시)
            while True:
                line = self.proc.stdout.readline()
                if not line:
                    raise MCPError("MCP 서버가 종료됨 (Node.js 설치와 네트워크를 확인하세요)")
                try:
                    resp = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if resp.get("id") == self._id:
                    if "error" in resp:
                        raise MCPError(f"{method} 실패: {resp['error']}")
                    return resp.get("result", {})

    def _initialize(self):
        self._send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pacifica-funding-bot", "version": "1.0"},
        })
        self._send("notifications/initialized", {}, notification=True)

    def list_tools(self) -> list[dict]:
        tools = []
        cursor = None
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = self._send("tools/list", params)
            tools.extend(result.get("tools", []))
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name: str, arguments: dict) -> dict | list | str:
        result = self._send("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise MCPError(f"도구 {name} 오류: {result.get('content')}")
        texts = [c.get("text", "") for c in result.get("content", [])
                 if c.get("type") == "text"]
        raw = "\n".join(texts)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    def close(self):
        try:
            self.proc.terminate()
        except OSError:
            pass


def spawn_pacifica_mcp(base_url: str, address: str = "",
                       private_key: str = "") -> MCPStdioClient:
    npx = shutil.which("npx")
    if not npx:
        raise MCPError("npx를 찾을 수 없습니다. Node.js 18+를 설치하세요.")
    env = {"PACIFICA_BASE_URL": base_url}
    if address:
        env["ADDRESS"] = address
    if private_key:
        env["AGENT_PRIVATE_KEY"] = private_key
    return MCPStdioClient([npx, "-y", "@pacifica-fi/mcp-server"], env)


class PacificaMCPClient:
    """api_client.PacificaClient와 동일한 인터페이스 — 뒤에서 MCP 도구를 호출한다.

    전략 코드(scanner/position/main)는 이 클래스와 REST 클라이언트를
    구분하지 않고 그대로 쓸 수 있다.
    """

    def __init__(self, base_url: str, address: str = "", private_key: str = ""):
        self.address = address
        self.mcp = spawn_pacifica_mcp(base_url, address, private_key)

    def _call(self, tool: str, args: dict | None = None):
        res = self.mcp.call_tool(tool, args or {})
        # 도구 응답은 REST와 동일한 {success, data, error, code} 봉투
        if isinstance(res, dict) and "success" in res:
            if not res["success"]:
                raise MCPError(f"{tool} 실패: {res.get('error')} (code={res.get('code')})")
            return res["data"]
        return res

    def get_markets(self) -> list[dict]:
        return self._call("getInfo")

    def get_prices(self) -> list[dict]:
        return self._call("getPrices")

    def get_account(self) -> dict:
        return self._call("getAccountInfo")

    def get_positions(self) -> list[dict]:
        return self._call("getCurrentPositions")

    def create_market_order(self, symbol: str, side: str, amount: str,
                            slippage_percent: str, reduce_only: bool = False,
                            builder_code: str = "") -> dict:
        import uuid
        args = {
            "symbol": symbol,
            "side": side,
            "amount": str(amount),
            "slippage_percent": str(slippage_percent),
            "reduce_only": reduce_only,
            "client_order_id": str(uuid.uuid4()),
        }
        if builder_code:
            args["builder_code"] = builder_code
        return self._call("createMarketOrder", args)

    def close(self):
        self.mcp.close()
