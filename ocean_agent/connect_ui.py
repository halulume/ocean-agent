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


# brand identity of the AI client that opened the form, mirrored from
# the homepage hero (same svg paths, same colors)
BRANDS = {
    "claude": {"name": "Claude", "color": "#D97757", "btn": "", "svg": "<path d=\"M4.709 15.955l4.72-2.647.08-.23-.08-.128H9.2l-.79-.048-2.698-.073-2.339-.097-2.266-.122-.571-.121L0 11.784l.055-.352.48-.321.686.06 1.52.103 2.278.158 1.652.097 2.449.255h.389l.055-.157-.134-.098-.103-.097-2.358-1.596-2.552-1.688-1.336-.972-.724-.491-.364-.462-.158-1.008.656-.722.881.06.225.061.893.686 1.908 1.476 2.491 1.833.365.304.145-.103.019-.073-.164-.274-1.355-2.446-1.446-2.49-.644-1.032-.17-.619a2.97 2.97 0 01-.104-.729L6.283.134 6.696 0l.996.134.42.364.62 1.414 1.002 2.229 1.555 3.03.456.898.243.832.091.255h.158V9.01l.128-1.706.237-2.095.23-2.695.08-.76.376-.91.747-.492.584.28.48.685-.067.444-.286 1.851-.559 2.903-.364 1.942h.212l.243-.242.985-1.306 1.652-2.064.73-.82.85-.904.547-.431h1.033l.76 1.129-.34 1.166-1.064 1.347-.881 1.142-1.264 1.7-.79 1.36.073.11.188-.02 2.856-.606 1.543-.28 1.841-.315.833.388.091.395-.328.807-1.969.486-2.309.462-3.439.813-.042.03.049.061 1.549.146.662.036h1.622l3.02.225.79.522.474.638-.079.485-1.215.62-1.64-.389-3.829-.91-1.312-.329h-.182v.11l1.093 1.068 2.006 1.81 2.509 2.33.127.578-.322.455-.34-.049-2.205-1.657-.851-.747-1.926-1.62h-.128v.17l.444.649 2.345 3.521.122 1.08-.17.353-.608.213-.668-.122-1.374-1.925-1.415-2.167-1.143-1.943-.14.08-.674 7.254-.316.37-.729.28-.607-.461-.322-.747.322-1.476.389-1.924.315-1.53.286-1.9.17-.632-.012-.042-.14.018-1.434 1.967-2.18 2.945-1.726 1.845-.414.164-.717-.37.067-.662.401-.589 2.388-3.036 1.44-1.882.93-1.086-.006-.158h-.055L4.132 18.56l-1.13.146-.487-.456.061-.746.231-.243 1.908-1.312-.006.006z\"/>"},
    "gpt": {"name": "ChatGPT", "color": "#10A37F", "btn": "#3B82F6", "svg": "<path d=\"M9.205 8.658v-2.26c0-.19.072-.333.238-.428l4.543-2.616c.619-.357 1.356-.523 2.117-.523 2.854 0 4.662 2.212 4.662 4.566 0 .167 0 .357-.024.547l-4.71-2.759a.797.797 0 00-.856 0l-5.97 3.473zm10.609 8.8V12.06c0-.333-.143-.57-.429-.737l-5.97-3.473 1.95-1.118a.433.433 0 01.476 0l4.543 2.617c1.309.76 2.189 2.378 2.189 3.948 0 1.808-1.07 3.473-2.76 4.163zM7.802 12.703l-1.95-1.142c-.167-.095-.239-.238-.239-.428V5.899c0-2.545 1.95-4.472 4.591-4.472 1 0 1.927.333 2.712.928L8.23 5.067c-.285.166-.428.404-.428.737v6.898zM12 15.128l-2.795-1.57v-3.33L12 8.658l2.795 1.57v3.33L12 15.128zm1.796 7.23c-1 0-1.927-.332-2.712-.927l4.686-2.712c.285-.166.428-.404.428-.737v-6.898l1.974 1.142c.167.095.238.238.238.428v5.233c0 2.545-1.974 4.472-4.614 4.472zm-5.637-5.303l-4.544-2.617c-1.308-.761-2.188-2.378-2.188-3.948A4.482 4.482 0 014.21 6.327v5.423c0 .333.143.571.428.738l5.947 3.449-1.95 1.118a.432.432 0 01-.476 0zm-.262 3.9c-2.688 0-4.662-2.021-4.662-4.519 0-.19.024-.38.047-.57l4.686 2.71c.286.167.571.167.856 0l5.97-3.448v2.26c0 .19-.07.333-.237.428l-4.543 2.616c-.619.357-1.356.523-2.117.523zm5.899 2.83a5.947 5.947 0 005.827-4.756C22.287 18.339 24 15.84 24 13.296c0-1.665-.713-3.282-1.998-4.448.119-.5.19-.999.19-1.498 0-3.401-2.759-5.947-5.946-5.947-.642 0-1.26.095-1.88.31A5.962 5.962 0 0010.205 0a5.947 5.947 0 00-5.827 4.757C1.713 5.447 0 7.945 0 10.49c0 1.666.713 3.283 1.998 4.448-.119.5-.19 1-.19 1.499 0 3.401 2.759 5.946 5.946 5.946.642 0 1.26-.095 1.88-.309a5.96 5.96 0 004.162 1.713z\"/>"},
    "gemini": {"name": "Gemini", "color": "#4E8DF7", "btn": "#4285F4", "svg": "<path d=\"M20.616 10.835a14.147 14.147 0 01-4.45-3.001 14.111 14.111 0 01-3.678-6.452.503.503 0 00-.975 0 14.134 14.134 0 01-3.679 6.452 14.155 14.155 0 01-4.45 3.001c-.65.28-1.318.505-2.002.678a.502.502 0 000 .975c.684.172 1.35.397 2.002.677a14.147 14.147 0 014.45 3.001 14.112 14.112 0 013.679 6.453.502.502 0 00.975 0c.172-.685.397-1.351.677-2.003a14.145 14.145 0 013.001-4.45 14.113 14.113 0 016.453-3.678.503.503 0 000-.975 13.245 13.245 0 01-2.003-.678z\"/>"},
    "grok": {"name": "Grok", "color": "#000000", "btn": "#000000", "svg": "<path d=\"M9.27 15.29l7.978-5.897c.391-.29.95-.177 1.137.272.98 2.369.542 5.215-1.41 7.169-1.951 1.954-4.667 2.382-7.149 1.406l-2.711 1.257c3.889 2.661 8.611 2.003 11.562-.953 2.341-2.344 3.066-5.539 2.388-8.42l.006.007c-.983-4.232.242-5.924 2.75-9.383.06-.082.12-.164.179-.248l-3.301 3.305v-.01L9.267 15.292M7.623 16.723c-2.792-2.67-2.31-6.801.071-9.184 1.761-1.763 4.647-2.483 7.166-1.425l2.705-1.25a7.808 7.808 0 00-1.829-1A8.975 8.975 0 005.984 5.83c-2.533 2.536-3.33 6.436-1.962 9.764 1.022 2.487-.653 4.246-2.34 6.022-.599.63-1.199 1.259-1.682 1.925l7.62-6.815\"/>"},
}

_ADDR_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
SERVER_LIFETIME_SEC = 600

_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ocean Agent - Connect</title><style>
:root { --ink:#0b2239; --sub:#5a7184; --line:#dbe6ee; --acc:#0e7f8a;
        --bg:#eef4f8; --card:#ffffff; --ok:#0d8a4f; --bad:#c0392b; }
* { box-sizing:border-box; margin:0; }
body { background:var(--bg); color:var(--ink);
       font:16px/1.6 -apple-system,'Segoe UI',Roboto,sans-serif;
       display:flex; justify-content:center; padding:40px 16px; }
.card { background:var(--card); border:1px solid var(--line);
        border-radius:18px; max-width:560px; width:100%;
        padding:28px 26px; box-shadow:0 10px 40px rgba(11,34,57,.08); }
h1 { font-size:20px; margin-bottom:4px; }
.brand { display:flex; align-items:center; gap:9px; padding-bottom:14px;
         margin-bottom:16px; border-bottom:1px solid var(--line); }
.brand svg { width:26px; height:26px; }
.brand b { font-size:16px; }
.brand .bsub { color:var(--sub); font-size:13px; margin-left:auto; }
.sub { color:var(--sub); font-size:14px; margin-bottom:18px; }
.msg { background:#f2f7fa; border:1px solid var(--line);
       border-radius:12px; padding:12px 14px; font-size:14px;
       margin-bottom:10px; }
.msg b { color:var(--acc); }
.msg a { color:var(--acc); font-weight:600; }
label { display:block; font-size:13px; font-weight:600; margin:16px 0 6px; }
input { width:100%; padding:12px 14px; border:1.5px solid var(--line);
        border-radius:10px; font-size:14px; font-family:ui-monospace,monospace; }
input:focus { outline:none; border-color:var(--acc); }
button { width:100%; margin-top:20px; padding:13px; border:0;
         border-radius:10px; background:var(--acc); color:#fff;
         font-size:15px; font-weight:700; cursor:pointer; }
button:hover { filter:brightness(1.08); }
.note { color:var(--sub); font-size:12.5px; margin-top:12px; }
.ko { color:var(--ink); font-weight:600; }
.ok { color:var(--ok); } .bad { color:var(--bad); }
.big { font-size:38px; text-align:center; margin:10px 0 2px; }
</style></head><body><div class="card">{body}</div></body></html>"""

_FORM = """
<div class="brand"><svg viewBox="0 0 24 24" fill="currentColor"
 fill-rule="evenodd" style="color:{bcolor}">{bsvg}</svg>
 <b>{bname}</b><span class="bsub">Ocean Agent</span></div>
<h1>Connect your Pacifica account</h1>
<div class="sub">Two values, then you are done.</div>
<div class="msg"><b>1.</b> Log in at
 <a href="https://app.pacifica.fi" target="_blank">app.pacifica.fi</a>
 (connect wallet)</div>
<div class="msg"><b>2.</b> Open
 <a href="https://app.pacifica.fi/apikey" target="_blank">app.pacifica.fi/apikey</a>
 and make a key<br>
 <span class="ko">Generate &rarr; copy the key on the left &rarr; Create</span>
 <br>Ocean Agent only trades, never withdraws. Revocable any time.</div>
<div class="msg"><b>3.</b> Paste the copied key below</div>
<form method="post" action="/submit?n={nonce}">
<label>Wallet PUBLIC address (Solana)</label>
<input name="address" placeholder="e.g. 7Ncb...abcd" required
 autocomplete="off" value="{addr}">
<label>Pacifica API key</label>
<input name="api_key" placeholder="from app.pacifica.fi/apikey" required
 autocomplete="off" type="password">
{err}
<button style="{bbtn}">Connect</button>
</form>
<div class="note">Saved only to the .env file on THIS computer and never
shown in the chat.</div>
"""

_DONE_OK = """
<div class="big">✅</div>
<h1 style="text-align:center">Connected</h1>
<div class="sub" style="text-align:center">balance {bal} USDC</div>
<div class="msg">Keys saved. Go back to your AI chat and continue - try
asking to <b>start auto trading</b>.</div>
<div class="note">You can close this window.</div>
"""

_DONE_WARN = """
<div class="big">⚠️</div>
<h1 style="text-align:center">Saved, but test failed</h1>
<div class="sub" style="text-align:center">{msg}</div>
<div class="msg">The values were saved. Check that the address has
deposited on Pacifica, or open this page again from the chat.</div>
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
        b = BRANDS.get(self.server.brand, BRANDS["claude"])
        self._page(200, _FORM.replace("{nonce}", self.server.nonce)
                   .replace("{err}", err).replace("{addr}", addr)
                   .replace("{bname}", b["name"])
                   .replace("{bcolor}", b["color"])
                   .replace("{bbtn}", ("background:" + b["btn"])
                            if b.get("btn") else "")
                   .replace("{bsvg}", b["svg"]))

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
                       'address (base58, 32-44 chars).</div>')
            return
        if len(key) < 20:
            self._form('<div class="note bad">The API key looks too short. '
                       'That key looks too short.</div>', addr=addr)
            return
        self.server.used = True
        result = self.server.on_submit(addr, key)
        if result.get("ok"):
            self._page(200, _DONE_OK.replace("{bal}", str(result.get("bal"))))
        else:
            self._page(200, _DONE_WARN.replace(
                "{msg}", str(result.get("msg", ""))[:160]))
        threading.Thread(target=self.server.shutdown, daemon=True).start()


def open_connect_page(on_submit, brand="claude") -> str:
    """Serve the form on a random loopback port and open the browser.

    on_submit(address, api_key) -> {"ok": bool, "bal": .., "msg": ..} runs
    in the server thread: it writes .env and tests the connection. Returns
    the URL (also shown to the user in case the browser did not open).
    """
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.nonce = secrets.token_urlsafe(16)
    srv.brand = brand
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
    # webbrowser.open can block for minutes on some Windows setups, and the
    # caller cannot print the URL until this returns, so the parent process
    # reading our stdout saw nothing and gave up (fresh-install simulation,
    # 08-24: 0.44s with the call suppressed, 300s+ without). Fire it from a
    # thread; the URL is the contract, the auto-open is best effort.
    def _open():
        try:
            webbrowser.open(url)
        except Exception:
            pass
    threading.Thread(target=_open, daemon=True).start()
    return url


def write_env(path: str, addr: str, key: str, extra: dict = None) -> None:
    """Put the two credentials into an env file, keeping other lines.

    Written through a temp file and moved into place so a crash cannot
    leave a half-written file, then locked to the current user where the
    platform allows it.
    """
    lines = []
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()

    def upsert(ls, k, v):
        out, done = [], False
        for ln in ls:
            if ln.strip().startswith(k + "="):
                out.append(f"{k}={v}")
                done = True
            else:
                out.append(ln)
        if not done:
            out.append(f"{k}={v}")
        return out

    # An empty credential means "I do not have it here", never "erase the one
    # on disk". A caller that only wants to add a setting (the budget tools)
    # reads the keys out of its own environment, which is empty when the keys
    # were written by a different process after this one started.
    def keep(k, v):
        if v:
            return upsert(lines, k, v)
        return lines

    lines = keep("ADDRESS", addr)
    lines = keep("PACIFICA_API_KEY", key)
    # anything else the setup asked for rides along, so an installed
    # user has one file to edit instead of a policy inside site-packages
    for k, v in (extra or {}).items():
        lines = upsert(lines, k, v)
    if not any(ln.strip().startswith("PACIFICA_BASE_URL=") for ln in lines):
        lines.append("PACIFICA_BASE_URL=https://api.pacifica.fi")
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)
    try:
        if os.name == "nt":
            import subprocess
            user = os.environ.get("USERNAME", "")
            if user:
                subprocess.run(["icacls", path, "/inheritance:r",
                                "/grant:r", f"{user}:(R,W)"],
                               capture_output=True, check=False)
        else:
            os.chmod(path, 0o600)
    except Exception:
        pass


def main(argv=None) -> int:
    """Collect credentials in the browser form; used by the installer.

    Prints the URL so a headless or locked-down machine can still be
    finished by hand, waits for the submission, and exits non-zero when
    nothing arrives inside the window so the caller can fall back to
    asking in the terminal.
    """
    import argparse
    import time as _t
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", required=True)
    ap.add_argument("--brand", default="claude")
    ap.add_argument("--timeout", type=int, default=600)
    a = ap.parse_args(argv)
    state = {"done": False}

    def on_submit(addr: str, key: str) -> dict:
        write_env(a.env_file, addr, key)
        state["done"] = True
        try:
            os.environ["ADDRESS"] = addr
            os.environ["PACIFICA_API_KEY"] = key
            from .api_client import PacificaClient
            cl = PacificaClient(os.environ.get("PACIFICA_BASE_URL",
                                               "https://api.pacifica.fi"),
                                address=addr)
            return {"ok": True, "bal": cl.get_account().get("balance")}
        except Exception as e:
            return {"ok": False, "msg": str(e)[:150]}

    url = open_connect_page(on_submit, brand=a.brand)
    # first line is machine readable: the caller may be a parent process
    # that needs the address to hand back to the user
    print(f"URL {url}", flush=True)
    print(f"  Finish in the browser window. If it did not open: {url}",
          flush=True)
    end = _t.time() + a.timeout
    while _t.time() < end and not state["done"]:
        _t.sleep(1)
    if state["done"]:
        print(f"  Saved to {a.env_file}", flush=True)
        return 0
    print("  Nothing was entered; skipping.", flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
