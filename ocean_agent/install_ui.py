# -*- coding: utf-8 -*-
"""The installer's face: a chat-shaped page instead of a black console.

A terminal is where people hesitate, so the console is left behind and
this serves the rest of the install in the browser, in the same visual
language as the connect form. It opens on the progress rows, hands over
to the credential fields, and ends on the finish card, all in one window.
The page never asks for the command: by the time it exists, the command
has already run.

Progress arrives over the loopback socket from the install script, which
posts one short status per step. Nothing here executes anything: it
displays state and collects the two credentials, exactly like the connect
form it shares its writer with.
"""
import json
import os
import re
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .connect_ui import BRANDS, write_env

SERVER_LIFETIME_SEC = 1800
_ADDR_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

STEPS = [
    ("python", "Preparing Python"),
    ("package", "Installing Ocean Agent"),
    ("register", "Connecting to Claude"),
]

_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ocean Agent</title><style>
:root { --ink:#0b2239; --sub:#5a7184; --line:#dbe6ee; --acc:#0e7f8a;
        --bg:#eef4f8; --card:#fff; --ok:#0d8a4f; --bad:#c0392b; }
* { box-sizing:border-box; margin:0; }
body { background:var(--bg); color:var(--ink);
  font:16px/1.6 -apple-system,'Segoe UI',Roboto,sans-serif;
  display:flex; justify-content:center; padding:40px 16px; }
.card { background:var(--card); border:1px solid var(--line);
  border-radius:18px; max-width:600px; width:100%; padding:28px 26px;
  box-shadow:0 10px 40px rgba(11,34,57,.08); }
.brand { display:flex; align-items:center; gap:9px; padding-bottom:14px;
  margin-bottom:18px; border-bottom:1px solid var(--line); }
.brand svg { width:26px; height:26px; }
.brand b { font-size:16px; }
.brand .bsub { color:var(--sub); font-size:13px; margin-left:auto; }
h1 { font-size:21px; margin-bottom:6px; }
.sub { color:var(--sub); font-size:14px; margin-bottom:18px; }
.hint { color:var(--sub); font-size:13px; margin-top:10px; }
.row { display:flex; align-items:center; gap:11px; padding:11px 0;
  border-bottom:1px solid var(--line); font-size:15px; }
.row:last-child { border-bottom:0; }
.dot { width:20px; height:20px; border-radius:50%; flex:none;
  border:2px solid var(--line); display:flex; align-items:center;
  justify-content:center; font-size:12px; color:#fff; }
.dot.on { border-color:var(--acc); background:var(--acc); }
.dot.run { border-color:var(--acc); border-right-color:transparent;
  animation:spin .8s linear infinite; }
@keyframes spin { to { transform:rotate(360deg); } }
.row.wait { color:var(--sub); }
label { display:block; font-size:13px; font-weight:600; margin:16px 0 6px; }
input { width:100%; padding:12px 14px; border:1.5px solid var(--line);
  border-radius:10px; font-size:14px; font-family:ui-monospace,monospace; }
input:focus { outline:none; border-color:var(--acc); }
button.go { width:100%; margin-top:20px; padding:13px; border:0;
  border-radius:10px; background:var(--acc); color:#fff; font-size:15px;
  font-weight:700; cursor:pointer; }
.msg { background:#f2f7fa; border:1px solid var(--line); border-radius:12px;
  padding:12px 14px; font-size:14px; margin-bottom:10px; }
.msg b { color:var(--acc); } .msg a { color:var(--acc); font-weight:600; }
.note { color:var(--sub); font-size:12.5px; margin-top:12px; }
.bad { color:var(--bad); } .big { font-size:38px; text-align:center; }
</style></head><body><div class="card">{body}</div>
</body></html>"""


def _rows(state):
    out = []
    for key, label in STEPS:
        st = state.get(key, "wait")
        if st == "done":
            dot, cls = '<div class="dot on">&#10003;</div>', ""
        elif st == "run":
            dot, cls = '<div class="dot run"></div>', ""
        else:
            dot, cls = '<div class="dot"></div>', " wait"
        out.append(f'<div class="row{cls}">{dot}{label}</div>')
    return "".join(out)


class _H(BaseHTTPRequestHandler):
    server_version = "OceanAgentInstall/1"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        raw = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _page(self, body):
        b = BRANDS.get(self.server.brand, BRANDS["claude"])
        head = (f'<div class="brand"><svg viewBox="0 0 24 24" '
                f'fill="currentColor" fill-rule="evenodd" '
                f'style="color:{b["color"]}">{b["svg"]}</svg>'
                f'<b>{b["name"]}</b>'
                f'<span class="bsub">Ocean Agent</span></div>')
        self._send(200, _PAGE.replace("{body}", head + body))

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if q.get("n", [""])[0] != self.server.nonce:
            self._send(404, "<h1>Not found</h1>")
            return
        s = self.server.state
        if u.path == "/state":
            self._send(200, json.dumps({"stage": s["stage"]}),
                       "application/json")
            return
        if u.path != "/":
            self._send(404, "<h1>Not found</h1>")
            return
        if s["stage"] == "install":
            self._page(
                "<h1>Installing</h1>"
                "<div class='sub'>This usually takes a minute or two.</div>" + _rows(s["steps"]) +
                "<script>setInterval(function(){fetch('/state?n="
                + self.server.nonce + "').then(r=>r.json()).then(function(d){"
                "location.reload();});},2000)</script>")
            return
        if s["stage"] == "keys":
            self._page(
                "<h1>Connect your account</h1>"
                "<div class='sub'>Last step.</div>"
                "<div class='msg'><b>1.</b> Log in at "
                "<a href='https://app.pacifica.fi' target='_blank'>"
                "app.pacifica.fi</a></div>"
                "<div class='msg'><b>2.</b> Open "
                "<a href='https://app.pacifica.fi/apikey' target='_blank'>"
                "app.pacifica.fi/apikey</a> and make a key<br>"
                "<b>Generate &rarr; copy the key on the left &rarr; Create"
                "</b><br>trade-only, cannot withdraw, revocable anytime</div>"
                f"<form method='post' action='/submit?n={self.server.nonce}'>"
                "<label>Wallet PUBLIC address (Solana)</label>"
                "<input name='address' placeholder='e.g. 7Ncb...abcd' "
                "required autocomplete='off'>"
                "<label>Pacifica API key</label>"
                "<input name='api_key' type='password' required "
                "autocomplete='off' placeholder='from app.pacifica.fi/apikey'>"
                f"{s.get('err', '')}"
                "<button class='go'>Connect</button></form>"
                "<div class='note'>Saved only to a file on THIS computer and never "
                "shown in the chat.</div>")
            return
        if s["stage"] == "done":
            self._page(
                "<div class='big'>&#9989;</div>"
                "<h1 style='text-align:center'>All set</h1>"
                f"<div class='sub' style='text-align:center'>"
                f"{s.get('bal', '')}</div>"
                "<div class='msg'>Open Claude and just talk to it.<br>"
                "<b>show me today's picks</b> &middot; <b>start auto trading</b></div>"
                "<div class='note'>You can close this window.</div>")
            return
        self._page("<h1>One moment</h1>")

    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if q.get("n", [""])[0] != self.server.nonce:
            self._send(403, "<h1>Expired</h1>")
            return
        s = self.server.state
        ln = int(self.headers.get("Content-Length") or 0)
        form = parse_qs(self.rfile.read(min(ln, 65536)).decode("utf-8"))
        if u.path == "/progress":
            # the install script reports one step at a time
            step = (form.get("step") or [""])[0]
            status = (form.get("status") or ["run"])[0]
            if step in dict(STEPS):
                s["steps"][step] = status
                s["stage"] = "install"
            if step == "keys":
                s["stage"] = "keys"
            self._send(200, "ok", "text/plain")
            return
        if u.path == "/submit":
            addr = (form.get("address") or [""])[0].strip()
            key = (form.get("api_key") or [""])[0].strip()
            if not _ADDR_RE.fullmatch(addr):
                s["err"] = ("<div class='note bad'>That is not a Solana "
                            "address (base58, 32-44 chars).</div>")
                self.send_response(303)
                self.send_header("Location", f"/?n={self.server.nonce}")
                self.end_headers()
                return
            if len(key) < 20:
                s["err"] = ("<div class='note bad'>That key looks too "
                            "short.</div>")
                self.send_response(303)
                self.send_header("Location", f"/?n={self.server.nonce}")
                self.end_headers()
                return
            s["err"] = ""
            write_env(self.server.env_file, addr, key)
            os.environ["ADDRESS"] = addr
            os.environ["PACIFICA_API_KEY"] = key
            try:
                from .api_client import PacificaClient
                cl = PacificaClient(os.environ.get(
                    "PACIFICA_BASE_URL", "https://api.pacifica.fi"),
                    address=addr)
                s["bal"] = f"balance {cl.get_account().get('balance')} USDC"
            except Exception:
                s["bal"] = "saved"
            s["stage"] = "done"
            s["finished"] = True
            self.send_response(303)
            self.send_header("Location", f"/?n={self.server.nonce}")
            self.end_headers()
            return
        self._send(404, "<h1>Not found</h1>")


def serve(env_file: str, brand: str = "claude") -> tuple:
    """Start the install page. Returns (url, state, shutdown)."""
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    srv.nonce = secrets.token_urlsafe(16)
    srv.brand = brand
    srv.env_file = env_file
    srv.state = {"stage": "install", "steps": {}, "err": "",
                 "finished": False}
    url = f"http://127.0.0.1:{srv.server_address[1]}/?n={srv.nonce}"

    def run():
        import time
        srv.timeout = 1
        end = time.time() + SERVER_LIFETIME_SEC
        while time.time() < end and not srv.state.get("closed"):
            srv.handle_request()
        try:
            srv.server_close()
        except Exception:
            pass

    threading.Thread(target=run, daemon=True).start()
    return url, srv.state, lambda: srv.state.update(closed=True)


def main(argv=None) -> int:
    """Open the install page and wait for the user to finish."""
    import argparse
    import time
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", required=True)
    ap.add_argument("--brand", default="claude")
    ap.add_argument("--stage", default="install",
                    choices=["install", "keys"])
    ap.add_argument("--timeout", type=int, default=1800)
    a = ap.parse_args(argv)
    url, state, stop = serve(a.env_file, a.brand)
    state["stage"] = a.stage
    print(f"URL {url}", flush=True)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    end = time.time() + a.timeout
    while time.time() < end and not state.get("finished"):
        time.sleep(1)
    return 0 if state.get("finished") else 1


if __name__ == "__main__":
    raise SystemExit(main())
