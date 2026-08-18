# -*- coding: utf-8 -*-
"""Browser credential form: the same clean window on every AI client.

MCP input forms (elicitation) only exist in some clients, and a raw
terminal scares people. This serves a small styled page on 127.0.0.1
with two fields (public wallet address, API key), a step guide with the
key-issue link, format checks, and a live connection test. The submitted
values go straight into the local .env; they never pass through the
conversation. One-shot nonce in the URL, loopback only, the server dies
after success or timeout.
"""
import json
import os
import re
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

_ADDR_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
SERVER_LIFETIME_SEC = 600

_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ocean Agent - Connect</title><style>
:root {{ --ink:#0b2239; --sub:#5a7184; --line:#dbe6ee; --acc:#0e7f8a;
        --bg:#eef4f8; --card:#ffffff; --ok:#0d8a4f; --bad:#c0392b; }}
* {{ box-sizing:border-box; margin:0; }}
body {{ background:var(--bg); color:var(--ink);
       font:16px/1.6 -apple-system,'Segoe UI',Roboto,sans-serif;
       display:flex; justify-content:center; padding:40px 16px; }}
.card {{ background:var(--card); border:1px solid var(--line);
        border-radius:18px; max-width:560px; width:100%;
        padding:28px 26px; box-shadow:0 10px 40px rgba(11,34,57,.08); }}
h1 {{ font-size:20px; margin-bottom:4px; }}
.sub {{ color:var(--sub); font-size:14px; margin-bottom:18px; }}
.msg {{ background:#f2f7fa; border:1px solid var(--line);
       border-radius:12px; padding:12px 14px; font-size:14px;
       margin-bottom:10px; }}
.msg b {{ color:var(--acc); }}
.msg a {{ color:var(--acc); font-weight:600; }}
label {{ display:block; font-size:13px; font-weight:600; margin:16px 0 6px; }}
input {{ width:100%; padding:12px 14px; border:1.5px solid var(--line);
        border-radius:10px; font-size:14px; font-family:ui-monospace,monospace; }}
input:focus {{ outline:none; border-color:var(--acc); }}
button {{ width:100%; margin-top:20px; padding:13px; border:0;
         border-radius:10px; background:var(--acc); color:#fff;
         font-size:15px; font-weight:700; cursor:pointer; }}
button:hover {{ filter:brightness(1.08); }}
.note {{ color:var(--sub); font-size:12.5px; margin-top:12px; }}
.ok {{ color:var(--ok); }} .bad {{ color:var(--bad); }}
.big {{ font-size:38px; text-align:center; margin:10px 0 2px; }}
</style></head><body><div class="card">{body}</div></body></html>"""

_FORM = """
<h1>🌊 Ocean Agent</h1>
<div class="sub">Connect your Pacifica account &middot; 파시피카 계정 연결</div>
<div class="msg"><b>1.</b> Log in at
 <a href="https://app.pacifica.fi" target="_blank">app.pacifica.fi</a>
 (connect wallet)</div>
<div class="msg"><b>2.</b> Create an API key at
 <a href="https://app.pacifica.fi/apikey" target="_blank">app.pacifica.fi/apikey</a>
 &middot; trade-only, cannot withdraw, revocable anytime</div>
<div class="msg"><b>3.</b> Paste both below &middot; 아래에 붙여넣기</div>
<form method="post" action="/submit?n={nonce}">
<label>Wallet PUBLIC address (Solana) &middot; 지갑 공개주소</label>
<input name="address" placeholder="e.g. 7Ncb...abcd" required
 autocomplete="off" value="{addr}">
<label>Pacifica API key &middot; API 키</label>
<input name="api_key" placeholder="from app.pacifica.fi/apikey" required
 autocomplete="off" type="password">
{err}
<button>Connect &middot; 연결</button>
</form>
<div class="note">Saved only to the .env file on THIS computer and never
shown in the chat. 이 값은 이 컴퓨터의 .env 에만 저장되며 대화에 표시되지
않습니다.</div>
"""

_DONE_OK = """
<div class="big">✅</div>
<h1 style="text-align:center">Connected &middot; 연결 완료</h1>
<div class="sub" style="text-align:center">balance {bal} USDC</div>
<div class="msg">Keys saved to .env. Go back to your AI chat and continue -
try asking to <b>start auto trading</b>.<br>
.env 저장 완료. 이제 AI 대화로 돌아가 "자동매매 시작"이라고 해보세요.</div>
<div class="note">You can close this window. 이 창은 닫으셔도 됩니다.</div>
"""

_DONE_WARN = """
<div class="big">⚠️</div>
<h1 style="text-align:center">Saved, but test failed</h1>
<div class="sub" style="text-align:center">{msg}</div>
<div class="msg">The values were saved to .env. Check that the address has
deposited on Pacifica, or open this page again from the chat.<br>
저장은 됐지만 연결 테스트에 실패했습니다. 주소가 파시피카에 입금 이력이 있는
계정인지 확인하세요.</div>
"""


class _Handler(BaseHTTPRequestHandler):
    server_version = "OceanAgentConnect/1"

    def log_message(self, *a):            # keys must never reach a log line
        pass

    def _page(self, code, body):
        raw = _PAGE.replace("{body}", body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _form(self, err="", addr=""):
        self._page(200, _FORM.replace("{nonce}", self.server.nonce)
                   .replace("{err}", err).replace("{addr}", addr))

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/" and parse_qs(u.query).get("n", [""])[0] == \
                self.server.nonce:
            self._form()
        else:
            self._page(404, "<h1>Not found</h1>")

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/submit" or parse_qs(u.query).get("n", [""])[0] != \
                self.server.nonce or self.server.used:
            self._page(403, "<h1>Expired</h1>")
            return
        ln = int(self.headers.get("Content-Length") or 0)
        form = parse_qs(self.rfile.read(min(ln, 65536)).decode("utf-8"))
        addr = (form.get("address") or [""])[0].strip()
        key = (form.get("api_key") or [""])[0].strip()
        if not _ADDR_RE.fullmatch(addr):
            self._form('<div class="note bad">That is not a Solana public '
                       'address (base58, 32-44 chars). 공개주소 형식이 '
                       '아닙니다.</div>')
            return
        if len(key) < 20:
            self._form('<div class="note bad">The API key looks too short. '
                       'API 키가 너무 짧습니다.</div>', addr=addr)
            return
        self.server.used = True
        result = self.server.on_submit(addr, key)
        if result.get("ok"):
            self._page(200, _DONE_OK.replace("{bal}", str(result.get("bal"))))
        else:
            self._page(200, _DONE_WARN.replace(
                "{msg}", str(result.get("msg", ""))[:160]))
        threading.Thread(target=self.server.shutdown, daemon=True).start()


def open_connect_page(on_submit) -> str:
    """Serve the form on a random loopback port and open the browser.

    on_submit(address, api_key) -> {"ok": bool, "bal": .., "msg": ..} runs
    in the server thread: it writes .env and tests the connection. Returns
    the URL (also shown to the user in case the browser did not open).
    """
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.nonce = secrets.token_urlsafe(16)
    srv.used = False
    srv.on_submit = on_submit
    port = srv.server_address[1]
    url = f"http://127.0.0.1:{port}/?n={srv.nonce}"

    def run():
        srv.timeout = 1
        import time
        end = time.time() + SERVER_LIFETIME_SEC
        while time.time() < end and not srv.used:
            srv.handle_request()
        # allow the final response to flush, then close
        try:
            srv.server_close()
        except Exception:
            pass

    threading.Thread(target=run, daemon=True).start()
    try:
        webbrowser.open(url)
    except Exception:
        pass
    return url
