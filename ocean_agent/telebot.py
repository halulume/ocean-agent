# -*- coding: utf-8 -*-
"""Telegram interface with zero LLM calls, for users without Claude credits.

The product's functions are plain Python already - the MCP server merely wraps
them for Claude, and Claude is a conversational skin, not a dependency. What a
credit-less user lacks is an entrance. This is that entrance: fixed commands
long-polling the Telegram API with nothing but the standard library and the
same functions the MCP tools call.

Free-form sentences route through scored keyword intents (still no model:
every phrase must be in the table), an optional LLM tier answers in
conversation when a key is configured, and /commands stay fixed.

Orders: the OPERATOR's own chat can start and stop the bracket bot, always
behind an exact yes/no confirmation (08-24 user decision). Member chats
remain read-only - a member's text can never start a trade or reach the
operator's machine as a prompt.

Setup for a user: make a bot with @BotFather, put TELEGRAM_BOT_TOKEN and
TELEGRAM_CHAT_ID in .env (the chat id gates who the bot answers), run
`python -m ocean_agent.telebot`. Alerts raised by the hourly watchers are
forwarded through the existing notify channel, which reads the same token.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

API = "https://api.telegram.org/bot{token}/{method}"

# All user-facing text lives in telebot_i18n.T (11 languages, static
# tables, no LLM). Personal mode keeps today's Korean via lang="ko"
# defaults; central mode passes each member's stored language down.


def _env_file(root):
    """The .env this bot reads AND writes, first match wins: the repo
    checkout layout, an explicit PACIFICA_ENV_FILE, then the installer's
    default (~/.ocean-agent/.env, created by install.ps1/install.sh).
    Before 2026-08-25 only the repo layout was searched, so a pip user who
    followed the mobile guide got 'no token' from a perfectly good .env.
    When none exists yet, new values go to the installer default."""
    cands = [os.path.join(root, ".env"),
             os.environ.get("PACIFICA_ENV_FILE") or "",
             os.path.join(os.path.expanduser("~"), ".ocean-agent", ".env")]
    for c in cands:
        if c and os.path.exists(c):
            return c
    return cands[2]


def _env():
    out = dict(os.environ)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = _env_file(root)
    if os.path.exists(p):
        for ln in open(p, encoding="utf-8", errors="replace"):
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                out.setdefault(k.strip(), v.strip())
    return out


def _call(token, method, **params):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(API.format(token=token, method=method),
                                 data=data)
    with urllib.request.urlopen(req, timeout=35) as r:
        return json.loads(r.read())


def _send(token, chat, text, kb=None):
    for i in range(0, len(text), 3800):
        if kb and i == 0:
            _call(token, "sendMessage", chat_id=chat,
                  text=text[:3800], reply_markup=kb)
        else:
            _call(token, "sendMessage", chat_id=chat, text=text[i:i + 3800])


def _read_tail(root, rel, lines=25, lang="ko"):
    from .telebot_i18n import tr
    p = os.path.join(root, rel)
    if not os.path.exists(p):
        return tr("file_missing", lang).format(rel)
    with open(p, encoding="utf-8", errors="replace") as f:
        rows = f.read().splitlines()
    return "\n".join(rows[:lines]) if rel.endswith(".md") \
        else "\n".join(rows[-lines:])


# Read-only twins of mcp_server.scan_funding / funding_alerts. The telebot
# must NOT import mcp_server: that module repoints state.STATE_FILE and the
# predictions file at the live trading ledgers at import time, and a chat
# frontend has no business touching trading state files. These helpers read
# the same public API and log files with zero side effects.

def _funding_table(cl, top=10):
    from .scanner import scan
    candidates = scan(cl, 8760, require_spot=False)
    if not candidates:
        return "No candidate markets found."
    lines = [f"{'symbol':<10}{'funding/hr':>12}{'APR':>10}"
             f"{'collect side':>14}{'mid price':>14}"]
    for c in candidates[:max(1, top)]:
        lines.append(f"{c.symbol:<10}{c.funding_hourly:>12.7f}{c.apr:>9.1%}"
                     f"{c.farm_side:>14}{c.mid_price:>14,.4f}")
    return "\n".join(lines)


def _carry_alerts(root, hours=24):
    out = []
    log_p = os.path.join(root, "outputs", "alarm.log")
    if os.path.exists(log_p):
        cut = time.time() - hours * 3600
        rows = []
        with open(log_p, encoding="utf-8", errors="replace") as f:
            for ln in f:
                parts = [x.strip() for x in ln.split("|")]
                if len(parts) < 4:
                    continue
                try:
                    ts = time.mktime(time.strptime(parts[0], "%Y-%m-%d %H:%M"))
                except ValueError:
                    continue
                if ts >= cut:
                    rows.append(parts)
        if rows:
            out.append(f"# Alerts in the last {hours}h ({len(rows)})")
            out += [f"- {p[0]} · {p[2]} · {p[3]}" for p in reversed(rows[-20:])]
        else:
            out.append(f"# No alerts in the last {hours}h")
    else:
        out.append("# The watcher has not raised anything yet")
    rank = os.path.join(root, "outputs", "arb_rank.md")
    if os.path.exists(rank):
        with open(rank, encoding="utf-8", errors="replace") as f:
            txt = f.read()
        for blk in txt.split("## ")[1:]:
            if blk.lstrip().startswith("통과"):
                out += ["", "## " + blk.strip()[:900]]
                break
    return "\n".join(out) if out else "No data."


_PRINT_BUSY = {"on": False, "token": "", "cid": ""}


def _print_async(env, lang):
    """recommend_now grinds through years of price history and can take
    minutes; run sync it froze the whole poll loop and every later message
    queued silently (08-24, seen live). The verdict is computed in a
    worker thread and delivered when ready; the caller gets an instant
    notice, and a second /print while one is running just says so."""
    from .telebot_i18n import tr
    import threading
    if _PRINT_BUSY["on"]:
        return tr("print_wait", lang)
    tok, cid = _PRINT_BUSY["token"], _PRINT_BUSY["cid"]
    if not tok or not cid:
        return _print_answer(env, lang)      # no send channel: stay sync
    _PRINT_BUSY["on"] = True

    def work():
        try:
            out = _print_answer(env, lang)
        except Exception as e:
            out = f"Print 판정 실패: {type(e).__name__}"
        finally:
            _PRINT_BUSY["on"] = False
        try:
            _send(tok, cid, out)
        except Exception:
            pass
    threading.Thread(target=work, daemon=True).start()
    return tr("print_wait", lang)


def _env_set(root, key, value):
    """Update or append KEY=value in the project's .env (utf-8).

    The personal onboarding writes what the owner pastes in chat, so a
    non technical user never edits a file by hand (2026-08-25 user order:
    language first, then wallet address, then API key)."""
    p = _env_file(root)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    lines = []
    if os.path.exists(p):
        with open(p, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    for i, ln in enumerate(lines):
        if ln.strip().startswith(key + "="):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _setup_stage(root):
    p = os.path.join(root, "outputs", "telebot_setup.json")
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f).get("stage", "")
    except (OSError, ValueError):
        return ""


def _setup_set(root, stage):
    p = os.path.join(root, "outputs", "telebot_setup.json")
    if not stage:
        try:
            os.remove(p)
        except OSError:
            pass
        return
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"stage": stage}, f)


_B58 = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")


def _setup_route(text, token, cid, env, root, lang, msg_id=""):
    """Consume one onboarding answer while a setup stage is pending.

    Returns None when no setup is pending (normal chat continues), or a
    reply string ('' when everything was already sent from here). The 'no'
    words skip the rest; both values land in .env AND in the live env dict
    so they work without a restart. The API key message is deleted from
    the chat history right after saving."""
    from .telebot_i18n import tr, intent_words, tier_kb
    stage = _setup_stage(root)
    if not stage:
        return None
    t = text.strip()
    if t.strip(" !.?~,").lower() in {k.strip() for k in
                                     intent_words("no", lang)}:
        _setup_set(root, "")
        _send(token, cid, tr("tier_pick", lang), kb=tier_kb(lang))
        return ""
    if stage == "addr":
        if not (32 <= len(t) <= 44 and set(t) <= _B58):
            return tr("setup_bad_addr", lang)
        _env_set(root, "ADDRESS", t)
        env["ADDRESS"] = t
        _send(token, cid, tr("setup_saved_addr", lang))
        if (env.get("PACIFICA_API_KEY") or "").strip():
            _setup_set(root, "")
            _send(token, cid, tr("tier_pick", lang), kb=tier_kb(lang))
        else:
            _setup_set(root, "key")
            _send(token, cid, tr("ask_apikey", lang))
        return ""
    # stage == "key"
    try:
        from .signing import keypair_from_base58
        keypair_from_base58(t)
    except Exception:
        return tr("setup_bad_key", lang)
    _env_set(root, "PACIFICA_API_KEY", t)
    env["PACIFICA_API_KEY"] = t
    if msg_id:                    # wipe the pasted key from the chat
        try:
            _call(token, "deleteMessage", chat_id=cid, message_id=msg_id)
        except Exception:
            pass
    _setup_set(root, "")
    _send(token, cid, tr("setup_done_key", lang))
    _send(token, cid, tr("tier_pick", lang), kb=tier_kb(lang))
    return ""


def _print_watch_loop(token, cid, env, root):
    """Hourly Print watch for every shipped bot, not just the operator.

    The operator's research watcher alarms only the operator's Telegram; a
    pip user got /print on demand but no push (2026-08-25 user order:
    "신규자도 알아서 알림받고 해야돼"). This thread reprices every live
    Print each hour and pushes an alert when a combination clears its
    breakeven. Dedup: only when the ✅ set changes, so the same list never
    rings twice. The opportunity file it writes is the same one the
    'print execute' flow reads, so alert -> reply -> amount -> order works
    end to end. Never places an order by itself. Opt out:
    TELEBOT_PRINT_WATCH=0.
    """
    from .telebot_i18n import tr
    from .api_client import PacificaClient
    from .print_eval import recommend_now
    watch_on = env.get("TELEBOT_PRINT_WATCH", "1") != "0"
    time.sleep(180)                       # let startup traffic settle
    while True:
        try:
            # Cycle settlement runs every hour regardless of the alert
            # opt-out: leaving an ended deposit unwithdrawn earns nothing.
            _print_cycle_check(token, cid, env, root)
        except Exception:
            pass
        try:
            if watch_on and not _PRINT_BUSY["on"]:
                _print_watch_once(token, cid, env, root)
        except Exception:
            pass                          # a watcher must outlive one bad hour
        time.sleep(3600)


def _print_cycle_check(token, cid, env, root):
    """Settle finished Print cycles: withdraw, report, offer re-entry.

    2026-08-25 user order: the deposit is there to collect APR only, so
    when the 24h cycle ends the funds must come back automatically and the
    owner is asked whether to go again. Settlement of an ENDED game only
    recovers the owner's own deposit plus premium and opens no position,
    so it runs without a per-event confirmation (의도적결정 §23 추가).
    Re-entry is never automatic: it goes through the same yes -> judge ->
    confirm -> amount flow as every money action.
    """
    from .telebot_i18n import tr
    from .api_client import PacificaClient
    addr = env.get("ADDRESS", "")
    key = env.get("PACIFICA_API_KEY", "")
    if not addr or not key:
        return
    state_p = os.path.join(root, "outputs", "print_cycle_state.json")
    try:
        with open(state_p, encoding="utf-8") as f:
            handled = json.load(f).get("done", [])
    except (OSError, ValueError):
        handled = []
    cl = PacificaClient(env.get("PACIFICA_BASE_URL",
                                "https://api.pacifica.fi"),
                        address=addr, private_key=key)
    try:
        accts = cl.print_positions().get("game_accounts", [])
    except Exception:
        return
    lang = _pers_lang(root) or "ko"
    changed = False
    for a in accts:
        ga = a.get("address", "")
        if not ga or ga in handled:
            continue
        started = float(a.get("game_started_at_ms") or 0)
        age_h = (time.time() * 1000 - started) / 3.6e6 if started else 0.0
        ended = bool(a.get("game_ended_at_ms"))
        if not ended and age_h < 24.05:
            continue                      # cycle still running
        side = "long" if int(a.get("direction", 0)) == 0 else "short"
        dep = float(a.get("initial_deposit") or 0)
        prem = float(a.get("premium_paid") or 0)
        try:
            if not ended:
                cl.print_end(ga)
            cl.print_withdraw(ga)
        except Exception as e:
            _send(token, cid, tr("print_cycle_fail", lang)
                  .format(f"{type(e).__name__}: {str(e)[:80]}"))
            continue                      # not handled: retry next hour
        handled.append(ga)
        changed = True
        _PPRINT[cid] = ("recycle", None, time.time() + 6 * 3600)
        _send(token, cid, tr("print_cycle_done", lang).format(
            a.get("game", "?"), side, f"{dep:g}", f"{prem:.2f}"))
    if changed:
        os.makedirs(os.path.dirname(state_p), exist_ok=True)
        with open(state_p, "w", encoding="utf-8") as f:
            json.dump({"done": handled[-200:]}, f)


def _print_watch_once(token, cid, env, root):
    """One watch pass: judge, dedup, write the opportunity file, push."""
    from .telebot_i18n import tr
    from .api_client import PacificaClient
    from .print_eval import recommend_now
    state_p = os.path.join(root, "outputs", "print_watch_state.json")
    opp_p = os.path.join(root, "outputs", "print_opportunity.json")
    _PRINT_BUSY["on"] = True
    try:
        cl = PacificaClient(env.get("PACIFICA_BASE_URL",
                                    "https://api.pacifica.fi"))
        txt = recommend_now(cl)
    finally:
        _PRINT_BUSY["on"] = False
    game, hits = "", []
    for ln in txt.splitlines():
        s = ln.strip()
        if s.startswith("=== "):
            game = s.split()[1]
        elif (s.startswith("✅") and not s.startswith("✅ =")
                and "%" in s):
            t = s.split()
            try:
                hits.append({"game": game, "side": t[1],
                             "dist": float(t[2].rstrip("%")),
                             "lev": float(t[3].rstrip("x")),
                             "apy": float(t[4].rstrip("%")),
                             "breakeven": float(t[5].rstrip("%")),
                             "row": s})
            except (IndexError, ValueError):
                continue
    oks = [h["row"] for h in hits]
    try:
        with open(state_p, encoding="utf-8") as f:
            prev = json.load(f).get("ok", [])
    except (OSError, ValueError):
        prev = []
    if oks and oks != prev:
        best = max(hits, key=lambda h: h["apy"] - h["breakeven"])
        os.makedirs(os.path.dirname(opp_p), exist_ok=True)
        with open(opp_p, "w", encoding="utf-8") as f:
            json.dump({"at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                       "best": best, "rows": hits}, f,
                      ensure_ascii=False, indent=1)
        lang = _pers_lang(root) or "ko"
        combos = {}
        for h in hits:
            key = (h["game"], h["side"], h["dist"])
            combos.setdefault(key, {"levs": [], "apy": h["apy"],
                                    "be": h["breakeven"]})
            combos[key]["levs"].append(f"{h['lev']:g}")
        lines = [tr("print_yes", lang), ""]
        for (g, side, dist), v in combos.items():
            lines.append(tr("print_combo", lang).format(
                g, side, f"{dist}%", "·".join(v["levs"]) + "x",
                f"{v['apy']:.0f}%", f"{v['be']:.0f}%"))
        lines += ["", tr("print_alert_hint", lang)]
        _send(token, cid, "\n".join(lines))
    os.makedirs(os.path.dirname(state_p), exist_ok=True)
    with open(state_p, "w", encoding="utf-8") as f:
        json.dump({"ok": oks}, f, ensure_ascii=False)


def _print_answer(env, lang="ko"):
    """Pacifica Print verdict, compact (08-24 user order: no table dump).

    The shipped judge (print_eval.recommend_now) reprices every live print
    against 9y of history and the lit signals; the reply is ONLY the
    verdict, and when something passes, one line per enterable combination
    with the leverages that clear breakeven."""
    from .telebot_i18n import tr
    from .api_client import PacificaClient
    from .print_eval import recommend_now
    cl = PacificaClient(env.get("PACIFICA_BASE_URL",
                                "https://api.pacifica.fi"))
    txt = recommend_now(cl)
    game, combos = "", {}
    for ln in txt.splitlines():
        s = ln.strip()
        if s.startswith("=== "):
            game = s.split()[1]
        elif s.startswith("✅") and not s.startswith("✅ =") and "%" in s:
            t = s.split()
            try:
                key = (game, t[1], t[2])            # (game, side, dist)
                combos.setdefault(key, {"levs": [], "apy": t[4],
                                        "be": t[5]})
                combos[key]["levs"].append(t[3].rstrip("x"))
            except IndexError:
                continue
    if not combos:
        return tr("print_no", lang)
    lines = [tr("print_yes", lang), ""]
    for (g, side, dist), v in combos.items():
        levs = "·".join(v["levs"]) + "x"
        lines.append(tr("print_combo", lang).format(
            g, side, dist, levs, v["apy"], v["be"]))
    return "\n".join(lines)


def _pick_table(root, lang="ko", cls=None):
    """Render the newest seal: exactly the picks the bot trades.

    Reads the seal file the bracket bot consumes, so this can never drift
    from what actually gets ordered. The seal is rewritten every hour on
    the machine that runs the bot, operator and user alike.
    """
    import glob as _glob
    import json as _json
    from .telebot_i18n import tr
    paths = sorted(_glob.glob(os.path.join(root, "outputs",
                                           "내일예측_*.json")))
    rec = None
    for p in reversed(paths):
        try:
            with open(p, encoding="utf-8") as f:
                r = _json.load(f)
        except (OSError, ValueError):
            continue
        if str(r.get("rule", "")).startswith("자산군"):
            rec = r
            break
    if rec is None:
        return tr("pick_none", lang)
    picks = rec.get("picks", [])
    if cls in ("stock", "coin"):
        # the seal maker's own classifier, so this can never disagree with
        # how the pick itself was classed (LIT is a coin without a Binance
        # twin; a Binance-based test called it a stock)
        from .seal_maker import klass
        want = (lambda k: k == "주식") if cls == "stock" \
            else (lambda k: k != "주식")
        picks = [p for p in picks if want(klass(p.get("sym", "")))]
    out = [tr("pick_header", lang).format(str(rec.get("made_at", ""))[:16])]
    for p in picks:
        side = tr("side_long" if p.get("dir") == "long" else "side_short",
                  lang)
        out.append(tr("pick_row", lang).format(
            p.get("trade_rank", "-"), p.get("sym", "?"), side,
            p.get("exp_move_pct", 0), p.get("touch_up_pct", "-"),
            p.get("touch_dn_pct", "-"), p.get("entry", 0)))
    out.append("")
    out.append(tr("pick_note", lang))
    return "\n".join(out)


def handle(cmd, env, root, lang="ko"):
    from .api_client import PacificaClient
    from .telebot_i18n import tr
    cl = PacificaClient(env.get("PACIFICA_BASE_URL",
                                "https://api.pacifica.fi"),
                        address=env.get("ADDRESS", ""))
    if cmd == "/pick":
        return _pick_table(root, lang)
    if cmd == "/print":
        return _print_async(env, lang)
    if cmd == "/funding":
        return _funding_table(cl, top=10)
    if cmd == "/carry":
        return _carry_alerts(root, hours=24)
    if cmd == "/bot":
        # the LIVE log; this used to point at the 08-13 dry-run file and
        # served week-old numbers as if they were current (caught 08-24)
        tail = _read_tail(root,
                          os.path.join("outputs", "bracket_live.log"),
                          12, lang)
        return tr("bot_header", lang) + "\n" + tail
    if cmd == "/balance":
        a = cl.get_account()
        return tr("balance", lang).format(
            f"{float(a.get('balance') or 0):,.2f}",
            f"{float(a.get('account_equity') or 0):,.2f}",
            f"{float(a.get('available_to_spend') or 0):,.2f}")
    if cmd == "/trades":
        d = cl._get("positions/history",
                    {"account": env.get("ADDRESS", ""), "limit": 10})
        rows = []
        for x in d if isinstance(d, list) else []:
            rows.append(tr("trade_row", lang).format(
                x["symbol"], x.get("side", ""),
                f"{float(x['amount']):,.2f}",
                f"{float(x['price']):,.4f}",
                f"{float(x.get('pnl') or 0):+.4f}"))
        return (tr("trades_header", lang) + "\n"
                + ("\n".join(rows) or tr("trades_none", lang)))
    return tr("menu", lang)


# ── 규칙 기반 대화 (LLM 없음) ──────────────────────────────────────────
# 문장을 이해하는 것이 아니다. 종목명과 의도 단어를 잡아 실데이터로 답을
# 조립한다. 유저 질문의 대부분은 조회라 이것으로 충분하고, 못 알아들으면
# 메뉴를 보여준다. 새로운 표현을 배우지는 못한다 - 그것이 LLM 과의 경계다.
ALIAS = {"카이토": "KAITO", "하이닉스": "SKHYNIX", "삼성": "SAMSUNG",
         "비트": "BTC", "비트코인": "BTC", "이더": "ETH", "이더리움": "ETH",
         "솔라나": "SOL", "도지": "DOGE", "테슬라": "TSLA",
         "엔비디아": "NVDA", "구글": "GOOGL", "마이크론": "MU",
         "샌디스크": "SNDK", "펌프": "PUMP", "펭구": "PENGU"}


def _sym_in(text, symbols, lang="ko"):
    from .telebot_i18n import alias_map
    up = text.upper()
    low = text.lower()
    for kr, s in ALIAS.items():
        if kr in text:
            return s
    for name, s in alias_map(lang).items():
        if name in low:
            return s
    hits = [s for s in symbols if s in up]
    return max(hits, key=len) if hits else None


def _fmt_apr(f):
    return f"{f * 24 * 365 * 100:+.0f}%"


def _sym_info(cl, sym, lang="ko"):
    from .telebot_i18n import tr
    q = next((p for p in cl.get_prices() if p["symbol"] == sym), None)
    if not q:
        return tr("sym_none", lang).format(sym)
    m = float(q.get("mark") or 0)
    y = float(q.get("yesterday_price") or 0)
    chg = (m / y - 1) * 100 if y > 0 else 0.0
    f = float(q.get("funding") or 0)
    side = tr("fund_long" if f < 0 else "fund_short", lang)
    return tr("sym_info", lang).format(
        sym, f"{m:,.4f}", f"{chg:+.2f}", _fmt_apr(f), side,
        f"{float(q.get('open_interest') or 0):,.0f}",
        f"{float(q.get('volume_24h') or 0):,.0f}")


def _why(cl, env, sym, lang="ko"):
    from .telebot_i18n import tr
    d = cl._get("positions/history",
                {"account": env.get("ADDRESS", ""), "limit": 30})
    rows = [x for x in (d if isinstance(d, list) else [])
            if x.get("symbol") == sym]
    if not rows:
        return tr("why_none", lang).format(sym)
    close = next((x for x in rows if str(x.get("side", ""))
                  .startswith("close")), None)
    if not close:
        return tr("why_noclose", lang).format(sym)
    op = next((x for x in rows if str(x.get("side", "")).startswith("open")),
              None)
    e = float(op["price"]) if op else float(close.get("entry_price") or 0)
    x = float(close["price"])
    pnl = float(close.get("pnl") or 0)
    pct = (x / e - 1) * 100 if e > 0 else 0
    line = tr("why_line", lang).format(
        sym, f"{e:,.4f}", f"{x:,.4f}", f"{pct:+.2f}", f"{pnl:+.4f}")
    verdict = tr("why_won" if pnl > 0 else "why_lost", lang)
    return f"{line}\n{verdict}. {tr('why_coin', lang)}"


def _cli_answer(question, data):
    """Paid tier, the simple way: mirror the user's own Claude through
    Telegram. If Claude Code is installed and logged in on this machine,
    `claude -p` answers on the user's existing subscription - no API key,
    no extra bill. The telebot is then just a remote screen for the Claude
    the user already pays for."""
    import shutil
    import subprocess
    exe = shutil.which("claude")
    if not exe:
        return None
    try:
        r = subprocess.run(
            [exe, "-p",
             "아래 실데이터만 근거로 한국어로 짧게 답해라. 데이터에 없는 것은 "
             "모른다고 하고, 숫자를 지어내지 말고, 투자 조언은 하지 마라.\n"
             f"[실데이터]\n{data}\n\n[질문]\n{question}"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120,
            creationflags=0x08000000 if os.name == "nt" else 0)
        out = (r.stdout or "").strip()
        return out if r.returncode == 0 and out else None
    except Exception:
        return None


_MODEL_CACHE = {}


def _pick_model(client, env):
    """Auto-match the model to the user's key. Lists the models the key can
    actually reach and takes the most capable one, so a paid user only puts
    the key in .env and gets the best of what they pay for. Setting
    ANTHROPIC_MODEL by hand still wins for anyone who wants to choose.
    Cached per key so the lookup happens once per bot process."""
    manual = env.get("ANTHROPIC_MODEL", "")
    if manual:
        return manual
    key = env.get("ANTHROPIC_API_KEY", "")
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key]
    pick = "claude-opus-5"
    try:
        have = {m.id for m in client.models.list()}
        for want in ("claude-opus-5", "claude-sonnet-5", "claude-sonnet-4-6",
                     "claude-haiku-4-5"):
            if want in have:
                pick = want
                break
    except Exception:
        pass                     # 조회 실패 시 기본값으로 그냥 간다
    _MODEL_CACHE[key] = pick
    return pick


# Tone matters as much as truth: the first prompt only said "answer from
# the data, say you don't know otherwise", and the model obeyed into
# stiffness ("알 수 없음?"). This one asks for a warm, natural reply in
# the user's language while keeping the same hard rules on numbers.
_SYS_PROMPT = (
    "You are Ocean Agent, a friendly trading assistant for Pacifica. "
    "Reply in the user's language (code: {lang}), in a warm, natural, "
    "conversational tone, like a helpful colleague. Keep it short: one to "
    "three sentences unless listing data. Use ONLY the numbers in the data "
    "below; never invent figures. If the data does not cover the question, "
    "say so naturally and suggest what you CAN show (picks, funding, "
    "balance, bot status). Never give investment advice or tell the user "
    "to buy or sell. Do not repeat the data verbatim; summarize what "
    "matters for the question. Write correct, natural {lang} with no "
    "mixed-language fragments.")


def _llm_http(url, headers, payload):
    import json as _json
    import urllib.request
    req = urllib.request.Request(
        url, data=_json.dumps(payload).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=45) as r:
        return _json.loads(r.read())


def _llm_answer(env, question, data, lang="ko", provider="claude",
                token=None):
    """Paid tier: what the member types goes straight to their chosen AI
    (Claude, ChatGPT, Gemini or Grok) together with the live data tables,
    and the AI answers in conversation. The AI never invents numbers
    because the only facts it sees are `data`. Runs on the member's own
    API key; any failure quietly falls back to the free-tier answer."""
    key = token or env.get("ANTHROPIC_API_KEY", "")
    if not key:
        return None
    sys_p = _SYS_PROMPT.format(lang=lang)
    user_p = f"[실데이터]\n{data}\n\n[질문]\n{question}"
    try:
        if provider == "claude":
            try:
                import anthropic
            except ImportError:
                return None
            client = anthropic.Anthropic(api_key=key)
            r = client.messages.create(
                model=_pick_model(client, env), max_tokens=1000,
                system=sys_p,
                messages=[{"role": "user", "content": user_p}])
            out = "".join(b.text for b in r.content
                          if b.type == "text").strip()
            return out or None
        if provider in ("gpt", "grok"):
            base = ("https://api.openai.com/v1" if provider == "gpt"
                    else "https://api.x.ai/v1")
            model = "gpt-4o-mini" if provider == "gpt" else "grok-3-mini"
            d = _llm_http(base + "/chat/completions",
                          {"Authorization": "Bearer " + key,
                           "Content-Type": "application/json"},
                          {"model": model, "max_tokens": 1000,
                           "messages": [{"role": "system", "content": sys_p},
                                        {"role": "user", "content": user_p}]})
            out = (d.get("choices") or [{}])[0].get(
                "message", {}).get("content", "").strip()
            return out or None
        if provider == "gemini":
            d = _llm_http(
                "https://generativelanguage.googleapis.com/v1beta/models/"
                "gemini-2.0-flash:generateContent?key=" + key,
                {"Content-Type": "application/json"},
                {"system_instruction": {"parts": [{"text": sys_p}]},
                 "contents": [{"parts": [{"text": user_p}]}],
                 "generationConfig": {"maxOutputTokens": 1000}})
            cands = d.get("candidates") or [{}]
            parts = (cands[0].get("content") or {}).get("parts") or []
            out = "".join(p.get("text", "") for p in parts).strip()
            return out or None
    except Exception:
        return None          # 어떤 실패든 무료 동작으로 조용히 복귀
    return None


# ── 판올림 알림·자가 업데이트 ───────────────────────────────────────────
# The bot checks PyPI at startup and every six hours; when a newer version
# exists it tells its owner ONCE per version, in their language, and the
# owner replies with the update word to have the bot pip-upgrade itself
# and restart on the new code. Members are never pinged: the package lives
# on the owner's machine, so only the owner can update it.
_UPD_STATE = "telebot_update.json"
_UPD = {"latest": "", "checked": 0.0}


def _pkg_version():
    try:
        from importlib.metadata import version
        return version("ocean-agent")
    except Exception:
        return ""


def _ver_tuple(v):
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return ()


def _update_check(root):
    """Newer PyPI version not yet announced, or ''. Never raises."""
    try:
        import urllib.request as _rq
        with _rq.urlopen("https://pypi.org/pypi/ocean-agent/json",
                         timeout=15) as r:
            latest = json.loads(r.read())["info"]["version"]
        cur = _pkg_version()
        if not cur or _ver_tuple(latest) <= _ver_tuple(cur):
            return ""
        p = os.path.join(root, "outputs", _UPD_STATE)
        try:
            with open(p, encoding="utf-8") as f:
                if json.load(f).get("notified") == latest:
                    _UPD["latest"] = latest
                    return ""              # already announced this one
        except (OSError, ValueError):
            pass
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"notified": latest}, f)
        _UPD["latest"] = latest
        return latest
    except Exception:
        return ""


def _do_update(root, lang, token, cid):
    """pip -U then replace this process with a fresh bot on the new code."""
    from .telebot_i18n import tr
    import subprocess
    _send(token, cid, tr("update_running", lang))
    r = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "--upgrade",
         "ocean-agent"],
        capture_output=True, text=True, timeout=900,
        creationflags=0x08000000 if os.name == "nt" else 0)
    if r.returncode != 0:
        _send(token, cid, tr("update_failed", lang))
        return
    _send(token, cid, tr("update_done", lang))
    subprocess.Popen([sys.executable, "-m", "ocean_agent.telebot"],
                     cwd=root, stdin=subprocess.DEVNULL,
                     creationflags=0x08000008 if os.name == "nt" else 0)
    os._exit(0)                            # the fresh process takes over


# ── 로컬 무제한 대화 모드 (chat_local) ──────────────────────────────────
# The free tier talks like an LLM because a small one runs right here: on
# first use the bot installs llama-cpp-python and pulls Qwen2.5-1.5B-
# Instruct (about 1GB, official Qwen repo, Apache-2.0) onto THIS machine,
# then answers any sentence from the live data, unlimited and offline.
# Each installation carries only its own load: member chats in central
# mode never route here, so nobody's traffic lands on the operator's CPU.
# The model only writes text - order execution stays with the exact-match
# confirmation flow above and is never wired to a model.
_MODEL_URL = ("https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/"
              "resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf")
_MODEL_DIR = os.path.expanduser("~/.ocean_agent_models")
_MODEL_PATH = os.path.join(_MODEL_DIR, "qwen2.5-1.5b-instruct-q4_k_m.gguf")
_MODEL_SIZE = 1_117_320_736          # exact size on the official repo
_LOCAL = {"state": "", "llm": None}  # "" -> preparing -> ready | error
_POLICY_CHAT_LOCAL = None


def _chat_local_on():
    """policy.yaml chat_local, read once per process (restart to change)."""
    global _POLICY_CHAT_LOCAL
    if _POLICY_CHAT_LOCAL is None:
        try:
            from .autonomous import load_policy
            _POLICY_CHAT_LOCAL = bool(load_policy().get("chat_local", True))
        except Exception:
            _POLICY_CHAT_LOCAL = False
    return _POLICY_CHAT_LOCAL


def _prepare_local():
    """Background one-time setup: pip install the runtime, download the
    model with a .part rename so a killed download never half-counts."""
    import subprocess
    import urllib.request as _rq
    try:
        try:
            import llama_cpp                              # noqa: F401
        except ImportError:
            print("[대화모드] llama-cpp-python 설치 중...", flush=True)
            # Prebuilt CPU wheel index: without it pip tries a source build
            # that needs a C++ toolchain, fails on most machines, and its
            # compiler subprocesses flash console windows (08-24, seen live)
            r = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--quiet",
                 "llama-cpp-python", "--extra-index-url",
                 "https://abetlen.github.io/llama-cpp-python/whl/cpu"],
                capture_output=True, text=True, timeout=1800,
                creationflags=0x08000000 if os.name == "nt" else 0)
            if r.returncode != 0:
                raise RuntimeError("pip install 실패")
            import llama_cpp                              # noqa: F401
        if not (os.path.exists(_MODEL_PATH)
                and os.path.getsize(_MODEL_PATH) == _MODEL_SIZE):
            os.makedirs(_MODEL_DIR, exist_ok=True)
            part = _MODEL_PATH + ".part"
            print(f"[대화모드] 모델 내려받는 중 (약 1GB) → {_MODEL_PATH}",
                  flush=True)
            _rq.urlretrieve(_MODEL_URL, part)
            if os.path.getsize(part) != _MODEL_SIZE:
                os.remove(part)
                raise RuntimeError("다운로드 크기 불일치")
            os.replace(part, _MODEL_PATH)
        try:
            from llama_cpp import Llama
            _LOCAL["llm"] = Llama(model_path=_MODEL_PATH, n_ctx=4096,
                                  n_threads=max(6, (os.cpu_count() or 8) - 4),
                                  verbose=False)
        except Exception:
            pass                     # lazy load will retry on first use
        _LOCAL["state"] = "ready"
        print("[대화모드] 준비 완료 (모델 프리로딩 포함)", flush=True)
    except Exception as ex:
        _LOCAL["state"] = "error"
        print(f"[대화모드] 준비 실패({type(ex).__name__}), 규칙 답변으로 "
              f"계속합니다", flush=True)


def _local_answer(question, data, lang):
    from llama_cpp import Llama
    if _LOCAL["llm"] is None:
        _LOCAL["llm"] = Llama(model_path=_MODEL_PATH, n_ctx=4096,
                              n_threads=max(6, (os.cpu_count() or 8) - 4),
                              verbose=False)
    r = _LOCAL["llm"].create_chat_completion(
        messages=[{"role": "system",
                   "content": _SYS_PROMPT.format(lang=lang)},
                  {"role": "user",
                   "content": f"[실데이터]\n{data}\n\n[질문]\n{question}"}],
        max_tokens=320, temperature=0.4)
    out = (r.get("choices") or [{}])[0].get("message", {}) \
        .get("content", "").strip()
    return out or None


def _local_llm(env, root, question, data, lang="ko"):
    """Unlimited local tier. None hands over to the next tier (rules)."""
    if not _chat_local_on():
        return None
    if _LOCAL["state"] == "ready":
        try:
            return _local_answer(question, data, lang)
        except Exception:
            return None
    if _LOCAL["state"] == "":
        _LOCAL["state"] = "preparing"
        import threading
        threading.Thread(target=_prepare_local, daemon=True).start()
        from .telebot_i18n import tr
        return tr("chat_local_preparing", lang) + "\n\n" + data
    return None                      # preparing or error: rules answer


def _free_llm(env, root, question, data, lang="ko"):
    """Free-tier LLM: one operator key with a free quota (Gemini's free
    tier fits) answers EVERY member in conversation, so the free mode talks
    like an LLM because it is one. FREE_LLM_KEY + FREE_LLM_PROVIDER in
    .env turn it on; a daily counter caps usage so the free quota is never
    exceeded, and past the cap (or on any failure) the rule answers take
    over exactly as before."""
    key = env.get("FREE_LLM_KEY", "")
    if not key:
        return None
    provider = (env.get("FREE_LLM_PROVIDER", "gemini") or "gemini").lower()
    try:
        cap = int(env.get("FREE_LLM_DAILY", "1200") or 1200)
    except ValueError:
        cap = 1200
    qp = os.path.join(root, "outputs", "free_llm_quota.json")
    today = time.strftime("%Y-%m-%d")
    st = {"day": today, "n": 0}
    try:
        with open(qp, encoding="utf-8") as f:
            old = json.load(f)
        if old.get("day") == today:
            st = old
    except (OSError, ValueError):
        pass
    if st["n"] >= cap:
        return None
    out = _llm_answer(env, question, data, lang, provider=provider,
                      token=key)
    if out:
        st["n"] = int(st.get("n", 0)) + 1
        try:
            with open(qp, "w", encoding="utf-8") as f:
                json.dump(st, f)
        except OSError:
            pass
    return out


def chat(text, env, root, lang="ko", cid="solo"):
    """자유 문장 → 의도 추정 → 실데이터 답. 모르면 메뉴.
    ANTHROPIC_API_KEY 가 있으면(유료 버전) 같은 데이터를 하이쿠가 문장으로
    풀어서 답한다. 입출력 인터페이스는 무료 버전과 동일하다.
    이 함수에 도달하는 채팅은 운영자의 것뿐이라(개인 모드 게이트, 중앙
    모드는 관리자만) 자동매매 켜고 끄기도 여기서 받는다."""
    if text.startswith("/"):
        return handle(text, env, root, lang)
    pf = _print_flow(text, text.lower(), cid, env, root, lang)
    if pf:
        return pf
    of = _order_flow(text, text.lower(), cid, env, root, lang)
    if of:
        return of
    ex = _exec_step(text.lower(), cid, root, lang)
    if ex:
        return ex
    base, kind = _rule_answer(text, env, root, lang)
    # A matched greeting/thanks/help reads better than anything a small
    # model rewrites it into, and tables are wanted verbatim (08-24: the
    # LLM used to overwrite '고마워' with a garbled data report). The LLM
    # tiers only take the questions no rule understood, and they get a
    # live snapshot to stand on instead of the menu text.
    if kind in ("talk", "data"):
        return base
    try:
        data = (_pick_table(root, lang) + "\n\n"
                + _read_tail(root, os.path.join(
                    "outputs", "bracket_live.log"), 8, lang))
    except Exception:
        data = base
    cli_ok = env.get("TELEBOT_CLI_MIRROR", "") == "1"   # 옵트인 전엔 봉인
    return ((cli_ok and _cli_answer(text, data))
            or _llm_answer(env, text, data, lang)  # 2순위: API 키 있으면
            or _local_llm(env, root, text, data, lang)  # 3순위: 로컬 무제한
            or _free_llm(env, root, text, data, lang)  # 4순위: 무료쿼터 LLM
            or base)                         # 5순위: 무료 규칙 답변


def _rule_answer(text, env, root, lang="ko"):
    from .api_client import PacificaClient
    from .telebot_i18n import tr, intent_words
    cl = PacificaClient(env.get("PACIFICA_BASE_URL",
                                "https://api.pacifica.fi"),
                        address=env.get("ADDRESS", ""))
    syms = [m["symbol"] for m in cl.get_markets()]
    sym = _sym_in(text, syms, lang)
    low = text.lower()
    if sym and _score(low, "why", lang):
        return _why(cl, env, sym, lang), "data"
    # "펀딩이 뭐야?" is a definition, not a data lookup: glossary first
    # when the sentence carries a what-is marker and a known term.
    g = _gloss_hit(low, lang)
    if g:
        return g, "talk"
    if sym:
        return _sym_info(cl, sym, lang), "data"
    # Best-scoring intent wins, so word combinations resolve to the intent
    # they share the most (and longest) keywords with, instead of whichever
    # if-branch happened to come first.
    DATA = {"pick": "/pick", "carry": "/carry", "funding": "/funding",
            "balance": "/balance", "trades": "/trades", "bot": "/bot",
            "print_q": "/print"}
    TALK = {"help": "help_reply", "alerts": "alerts_info",
            "greet": "greet_reply", "thanks": "thanks_reply"}
    best, best_s = None, 0
    for it in list(DATA) + list(TALK) + ["pick_stock", "pick_coin"]:
        s = _score(low, it, lang)
        if s > best_s:
            best, best_s = it, s
    if best == "pick_stock":
        return _pick_table(root, lang, cls="stock"), "data"
    if best == "pick_coin":
        return _pick_table(root, lang, cls="coin"), "data"
    if best in DATA:
        return handle(DATA[best], env, root, lang), "data"
    if best in TALK:
        return tr(TALK[best], lang), "talk"
    return tr("not_understood", lang) + "\n" + tr("menu", lang), "none"


def _score(low, intent, lang):
    """Sum of matched keyword lengths: longer, more specific words weigh
    more, so '펀딩 캐리 자리' lands on carry, not funding."""
    from .telebot_i18n import intent_words
    return sum(len(k) for k in intent_words(intent, lang) if k in low)


def _gloss_hit(low, lang):
    from .telebot_i18n import GLOSS, gloss, intent_words
    if not any(k in low for k in intent_words("whatis", lang)):
        return None
    hit = None
    for term, (key,) in GLOSS.items():
        if term in low and (hit is None or len(term) > len(hit[0])):
            hit = (term, key)
    return gloss(hit[1], lang) if hit else None


# ── 실행 (운영자 전용): 자동매매 켜고 끄기 ─────────────────────────────
# Execution over Telegram is limited to the operator's own chat: members
# registered an address only, and the bot's promise is "no orders leave
# through me" for them. Every action asks for a yes/no first (the same
# preview-then-confirm rule the MCP tools follow), and the pending request
# expires after two minutes.
_PENDING = {}                    # chat_id -> (action, expires_epoch)


def _exec_intent(low, lang):
    off = _score(low, "auto_off", lang)
    on = _score(low, "auto_on", lang)
    if off and off >= on:
        return "off"
    return "on" if on else None


def _bot_pids():
    """PIDs of running bracket_trader python processes, via CIM (works for
    python.exe and pythonw.exe alike, any install path). None on platforms
    where this lookup does not exist, so callers can tell "not running"
    apart from "cannot look" (review M2)."""
    import subprocess
    if os.name != "nt":
        return None
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process | Where-Object { "
             "$_.CommandLine -match 'ocean_agent.bracket_trader' -and "
             "$_.Name -match 'python' }).ProcessId"],
            capture_output=True, text=True, timeout=30,
            creationflags=0x08000000)     # no console flash from pythonw
        return [int(x) for x in r.stdout.split() if x.strip().isdigit()]
    except Exception:
        return []


def _exec_apply(action, root, lang):
    from .telebot_i18n import tr
    import subprocess
    pids = _bot_pids()                       # None: platform can't look
    if action == "on":
        if pids:
            return tr("exec_already_on", lang)
        os.makedirs(os.path.join(root, "outputs"), exist_ok=True)
        logf = open(os.path.join(root, "outputs", "bracket_live.log"), "a",
                    encoding="utf-8")
        # DETACHED_PROCESS | CREATE_NO_WINDOW: survives the telebot, shows
        # nothing. stdin=DEVNULL is the MCP zombie lesson (08-22). A
        # duplicate start on platforms where pids is None is caught by the
        # trader's own heartbeat guard.
        subprocess.Popen(
            [sys.executable, "-u", "-m", "ocean_agent.bracket_trader"],
            cwd=root, stdout=logf, stderr=logf,
            stdin=subprocess.DEVNULL,
            creationflags=0x08000008 if os.name == "nt" else 0)
        logf.close()                         # the child holds its own copy
        return tr("exec_done_on", lang)
    if pids is None:
        # Non-Windows: no honest way to find the process from here yet,
        # and a silent "not running" while it trades would be a lie
        # (review M2). Say so instead.
        return tr("exec_no_stop_platform", lang)
    if not pids:
        return tr("exec_already_off", lang)
    for pid in pids:
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            f"Stop-Process -Id {pid} -Force"],
                           capture_output=True, timeout=30,
                           creationflags=0x08000000)
        except Exception:
            pass
    return tr("exec_done_off", lang)


# ── 수동 주문 흐름 (운영자 전용, 08-24 사용자 지시 "주문도 넣을 수 있게") ──
# "BTC 롱 100불 5배" -> preview -> exact yes -> isolated margin + leverage
# set -> market order WITH native TP/SL attached (the safety invariant no
# order may skip). Defaults: 5x, TP +2% / SL -1%; every stage expires,
# ordinary sentences pass through, members can never reach this.
_PORDER = {}                    # cid -> (stage, spec, expires)
_SIDE_WORDS = {
    "long": ("롱", "long", "做多", "ロング", "лонг", "compra", "alış"),
    "short": ("숏", "short", "做空", "ショート", "шорт", "venta", "satış"),
}


def _parse_order(text, low, env, lang):
    import re
    side = None
    for sd, words in _SIDE_WORDS.items():
        if any(w in low for w in words):
            side = sd if side is None else side
    if not side:
        return None
    try:
        from .api_client import PacificaClient
        cl = PacificaClient(env.get("PACIFICA_BASE_URL",
                                    "https://api.pacifica.fi"))
        syms = [m["symbol"] for m in cl.get_markets()]
    except Exception:
        return None
    sym = _sym_in(text, syms, lang)
    if not sym:
        return None
    spec = {"sym": sym, "side": side, "lev": 5.0, "tp": 2.0, "sl": 1.0,
            "usd": None}
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:배|x|X|倍)", text)
    if m:
        spec["lev"] = float(m.group(1))
    m = re.search(r"[\$]?\s*(\d+(?:\.\d+)?)\s*(?:불|달러|usd|USD|\$)", text) \
        or re.search(r"\$\s*(\d+(?:\.\d+)?)", text)
    if m:
        spec["usd"] = float(m.group(1))
    m = re.search(r"(?:익절|tp|TP)\s*\+?(\d+(?:\.\d+)?)\s*%", text)
    if m:
        spec["tp"] = float(m.group(1))
    m = re.search(r"(?:손절|sl|SL)\s*-?(\d+(?:\.\d+)?)\s*%", text)
    if m:
        spec["sl"] = float(m.group(1))
    return spec


def _order_flow(text, low, cid, env, root, lang):
    from .telebot_i18n import tr, intent_words
    p = _PORDER.get(cid)
    if p and time.time() >= p[2]:
        _PORDER.pop(cid, None)
        p = None
    if p:
        stage, spec = p[0], p[1]
        t = low.strip(" !.?~,$")
        if t in {k.strip() for k in intent_words("no", lang)}:
            _PORDER.pop(cid, None)
            return tr("exec_cancel", lang)
        if stage == "amount":
            try:
                spec["usd"] = float(t.replace(",", ""))
                assert spec["usd"] > 0
            except (ValueError, AssertionError):
                return None            # not an amount: let it pass through
            _PORDER[cid] = ("confirm", spec, time.time() + 180)
            return tr("order_confirm", lang).format(
                spec["sym"], spec["side"], f"{spec['usd']:g}",
                f"{spec['lev']:g}", f"{spec['tp']:g}", f"{spec['sl']:g}")
        if stage == "confirm":
            if t in {k.strip() for k in intent_words("yes", lang)}:
                _PORDER.pop(cid, None)
                return _order_place(env, spec, lang)
            return None
    spec = None
    if any(w in low for words in _SIDE_WORDS.values() for w in words):
        # "SOL 숏 왜 잃었어" is a why-question, not an order (0.4.46 audit)
        if _score(low, "why", lang):
            return None
        spec = _parse_order(text, low, env, lang)
    if not spec:
        return None
    if spec["usd"] is None:
        _PORDER[cid] = ("amount", spec, time.time() + 180)
        return tr("order_ask_amount", lang).format(
            spec["sym"], spec["side"], f"{spec['lev']:g}")
    _PORDER[cid] = ("confirm", spec, time.time() + 180)
    return tr("order_confirm", lang).format(
        spec["sym"], spec["side"], f"{spec['usd']:g}",
        f"{spec['lev']:g}", f"{spec['tp']:g}", f"{spec['sl']:g}")


def _order_place(env, spec, lang):
    from .telebot_i18n import tr
    from .api_client import PacificaClient, PacificaError
    from .position import _round_to_tick, _round_down_to_lot
    try:
        cl = PacificaClient(env.get("PACIFICA_BASE_URL",
                                    "https://api.pacifica.fi"),
                            address=env.get("ADDRESS", ""),
                            private_key=env.get("PACIFICA_API_KEY", ""))
        sym, side = spec["sym"], spec["side"]
        m = next(x for x in cl.get_markets() if x["symbol"] == sym)
        px = next(float(p.get("mark") or p.get("mid") or 0)
                  for p in cl.get_prices() if p["symbol"] == sym)
        lot = float(m.get("lot_size") or 0.0001)
        tick = float(m.get("tick_size") or 0.01)
        lev = min(spec["lev"], float(m.get("max_leverage") or spec["lev"]))
        amount = _round_down_to_lot(spec["usd"] * lev / px, lot)
        if float(amount) <= 0:
            return tr("order_fail", lang).format("amount=0")
        ok = True
        try:
            cl.update_margin_mode(sym, True)
            cl.update_leverage(sym, int(lev))
        except PacificaError:
            ok = False
        long_ = side == "long"
        tp = _round_to_tick(px * (1 + spec["tp"] / 100 * (1 if long_ else -1)),
                            tick)
        sl = _round_to_tick(px * (1 - spec["sl"] / 100 * (1 if long_ else -1)),
                            tick)
        res = cl.create_market_order(
            sym, "bid" if long_ else "ask", str(amount), "0.5",
            take_profit_price=tp, stop_loss_price=sl,
            take_profit_limit=True)
        note = "" if ok else "\n⚠️ margin/leverage setting unconfirmed"
        return tr("order_done", lang).format(
            sym, side, f"{spec['usd']:g}", f"{lev:g}", f"{px:,.6g}",
            tp, sl) + note
    except Exception as e:
        return tr("order_fail", lang).format(
            f"{type(e).__name__}: {str(e)[:120]}")


# ── 프린트 실행 흐름 (운영자 전용, 08-24 사용자 설계) ────────────────────
# alarm -> owner: "프린트 실행" -> bot shows the best combo and asks ->
# exact 예 -> bot recommends an amount off the live balance and asks ->
# a number -> the ORDER IS PLACED (print_open). Every stage expires, only
# the operator's chat reaches this, and any parse failure cancels cleanly.
_PPRINT = {}                 # cid -> (stage, combo, expires)


def _print_flow(text, low, cid, env, root, lang):
    from .telebot_i18n import tr, intent_words
    p = _PPRINT.get(cid)
    if p and time.time() >= p[2]:
        _PPRINT.pop(cid, None)
        p = None
    if p:
        stage, combo = p[0], p[1]
        t = low.strip(" !.?~,$")
        if t in {k.strip() for k in intent_words("no", lang)}:
            _PPRINT.pop(cid, None)
            return tr("exec_cancel", lang)
        if stage == "recycle":
            # After a settled 24h cycle: yes = re-enter ONLY through the
            # judge (fresh ✅ needed), no = stop here. Any other text falls
            # through to normal chat.
            if t in {k.strip() for k in intent_words("yes", lang)}:
                opp = _print_opp(root)
                if not opp or not opp.get("best"):
                    _PPRINT.pop(cid, None)
                    return tr("print_no", lang)
                b = opp["best"]
                _PPRINT[cid] = ("confirm", b, time.time() + 300)
                return (f"프린트 기회: {b['game']} {b['side']} · 거리 "
                        f"{b['dist']}% · {b['lev']:g}배 · 실제 APY "
                        f"{b['apy']:.0f}% vs 손익분기 {b['breakeven']:.0f}%\n"
                        f"프린트를 실행할까요? (예/아니)")
            return None
        if stage == "confirm":
            if t in {k.strip() for k in intent_words("yes", lang)}:
                try:
                    from .api_client import PacificaClient
                    cl = PacificaClient(env.get("PACIFICA_BASE_URL",
                                                "https://api.pacifica.fi"),
                                        address=env.get("ADDRESS", ""))
                    a = cl.get_account()
                    avail = float(a.get("available_to_spend") or 0)
                except Exception:
                    avail = 0.0
                rec = max(10, min(200, round(avail * 0.10)))
                _PPRINT[cid] = ("amount", combo, time.time() + 300)
                return (f"얼마를 넣을까요? 추천: ${rec} "
                        f"(가용 ${avail:,.0f}의 10%, 추천일 뿐 자유입니다)\n"
                        f"숫자로 답해주세요. 취소는 '아니'.")
            return None
        if stage == "amount":
            try:
                usd = float(t.replace(",", ""))
                assert usd > 0
            except (ValueError, AssertionError):
                return "숫자로만 답해주세요 (예: 50). 취소는 '아니'."
            _PPRINT.pop(cid, None)
            return _print_place(env, combo, usd)
    # entry word: only meaningful while a fresh opportunity file exists
    if "프린트 실행" in text or "print execute" in low:
        opp = _print_opp(root)
        if not opp or not opp.get("best"):
            return tr("print_no", lang)
        b = opp["best"]
        _PPRINT[cid] = ("confirm", b, time.time() + 300)
        return (f"프린트 기회: {b['game']} {b['side']} · 거리 {b['dist']}% · "
                f"{b['lev']:g}배 · 실제 APY {b['apy']:.0f}% vs 손익분기 "
                f"{b['breakeven']:.0f}%\n프린트를 실행할까요? (예/아니)")
    return None


def _print_opp(root):
    p = os.path.join(root, "outputs", "print_opportunity.json")
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        at = time.mktime(time.strptime(d.get("at", ""),
                                       "%Y-%m-%dT%H:%M:%S"))
        return d if time.time() - at < 2 * 3600 else None
    except Exception:
        return None


def _print_place(env, b, usd):
    """Place the confirmed print via the same client path the MCP tool
    uses, with the same absolute notional guard."""
    from .api_client import PacificaClient
    try:
        cl = PacificaClient(env.get("PACIFICA_BASE_URL",
                                    "https://api.pacifica.fi"),
                            address=env.get("ADDRESS", ""),
                            private_key=env.get("PACIFICA_API_KEY", ""))
        if usd * max(b["lev"], 1.0) > cl.MAX_ORDER_NOTIONAL_USD:
            return (f"거절: ${usd:,.0f} x {b['lev']:g}배 명목이 상한 "
                    f"${cl.MAX_ORDER_NOTIONAL_USD:,.0f} 를 넘습니다.")
        games = {g["game"]: g for g in cl.print_games()}
        asset = games[b["game"]]["target_asset"]
        mark = next(float(p.get("mark") or p.get("mid") or 0)
                    for p in cl.get_prices() if p["symbol"] == asset)
        strike = round(mark * (1 - b["dist"] / 100) if b["side"] == "long"
                       else mark * (1 + b["dist"] / 100), 2)
        direction = 0 if b["side"] == "long" else 1
        res = cl.print_open(b["game"], str(usd), direction, str(strike),
                            str(b["lev"]))
        return (f"✅ 프린트 주문 완료: {b['game']} {b['side']} ${usd:g} · "
                f"목표가 {strike:,.6g} (현재 {mark:,.6g}) · {b['lev']:g}배\n"
                f"계정: {res.get('game_account_address', '?')}")
    except Exception as e:
        return f"프린트 주문 실패: {type(e).__name__}: {str(e)[:150]}"


def _exec_step(low, cid, root, lang):
    """Reply if this message belongs to the execution flow, else None."""
    from .telebot_i18n import tr, intent_words
    p = _PENDING.get(cid)
    if p and time.time() >= p[1]:
        _PENDING.pop(cid, None)              # expired: forget it (review M6)
        p = None
    if p:
        # While a confirmation is pending, ONLY an exact yes/no counts.
        # Substring scoring here approved real orders off the "예" inside
        # "예측" and the "확인" inside "잔고 확인해줘" (review S1), and the
        # en keyword "no " with its trailing space meant a literal "no"
        # did not cancel (S2). Any other sentence falls through unanswered
        # and the request simply keeps waiting or expires.
        t = low.strip(" !.?~‼️,")
        if t in {k.strip() for k in intent_words("yes", lang)}:
            _PENDING.pop(cid, None)
            return _exec_apply(p[0], root, lang)
        if t in {k.strip() for k in intent_words("no", lang)}:
            _PENDING.pop(cid, None)
            return tr("exec_cancel", lang)
    # "자동매매란 뭐야?" is a definition question, not an order (review M1)
    if any(k in low for k in intent_words("whatis", lang)):
        return None
    act = _exec_intent(low, lang)
    if act:
        _PENDING[cid] = (act, time.time() + 120)
        return tr("exec_confirm_on" if act == "on" else "exec_confirm_off",
                  lang)
    return None


# ── 중앙 운영 모드 ────────────────────────────────────────────────────
# One bot for everyone, run by the operator, like the Claude app: a user
# adds the bot on Telegram, registers a wallet address (Pacifica reads are
# address-only, no key), and gets the free tier. Paying users are flagged by
# the operator and get Claude answers on the operator's API key - they never
# touch a key themselves. Single-user mode (TELEGRAM_CHAT_ID set, no user
# file) still works exactly as before.
USERS = "telebot_users.json"


def _users_path(root):
    return os.path.join(root, "outputs", USERS)


def _load_users(root):
    p = _users_path(root)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_users(root, users):
    p = _users_path(root)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=1)


def _handle_member(text, chat_id, env, root, admin):
    """Route one message in central mode: registration, admin approval,
    then the normal free/paid answer with the member's own address."""
    users = _load_users(root)
    u = users.get(chat_id)
    if admin and chat_id == admin and text.startswith("/승인 "):
        target = text.split()[1]
        if target in users:
            users[target]["paid"] = True
            _save_users(root, users)
            return f"{target} 유료 전환 완료"
        return f"{target} 는 등록된 사용자가 아니다"
    from .telebot_i18n import tr, lang_kb
    if u is None or not u.get("lang"):
        # 국기 키보드부터 (사용자 확정 순서). 언어가 정해질 때까지는
        # 어떤 텍스트가 와도 같은 화면을 다시 보여준다.
        if u is None:
            users[chat_id] = {"lang": "", "address": "", "paid": False}
            _save_users(root, users)
        return ("KB", tr("pick_lang", "any"))
    lang0 = u.get("lang") or "en"
    if chat_id != admin and env.get("TELEBOT_MEMBER_OPEN", "") != "1":
        # 2026-08-25 user order: even window shopping requires installing.
        # The central bot answers members with the install pitch, in their
        # language, and nothing else. TELEBOT_MEMBER_OPEN=1 restores the
        # old open behavior if it is ever wanted again.
        return tr("install_pitch", lang0)
    if text == "/mode":
        return ("KB_TIER", tr("tier_pick", lang0))
    if text == "/menu":
        return handle("/menu", env, root, lang0) + "\n" + tr("menu_extra",
                                                             lang0)
    if u.get("await_token"):
        from .telebot_i18n import PROVIDERS
        p = PROVIDERS.get(u.get("provider") or "claude",
                          PROVIDERS["claude"])
        tok = text.strip()
        if tok.startswith(p["prefix"]) and len(tok) > 20:
            u["token"] = tok
            u["paid"] = True
            u["await_token"] = False
            _save_users(root, users)
            return tr("token_saved", lang0).format(p["name"]) \
                + ("\n\n" + tr("ask_addr", lang0)
                                               if not u.get("address") else "")
        return tr("token_bad", lang0).format(p["name"], p["prefix"])
    if not u.get("address"):
        b58 = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZ"
                  "abcdefghijkmnopqrstuvwxyz")
        if 32 <= len(text) <= 44 and all(ch in b58 for ch in text):
            u["address"] = text
            _save_users(root, users)
            return tr("done", u["lang"])
        return tr("bad_addr", u["lang"])
    e2 = dict(env)
    e2["ADDRESS"] = u["address"]
    lang = u.get("lang") or "en"
    # Central mode never touches the operator's Claude CLI for MEMBER text:
    # a member's sentence must not become a local prompt (secret-exfiltration
    # surface). The admin is the operator, so their own text may mirror
    # through their own `claude -p` (subscription, no API bill) when
    # TELEBOT_CLI_MIRROR=1. Free members get rule answers; paid members get
    # their own key, or the operator's key if sponsored via /승인.
    if text.startswith("/"):
        return handle(text, e2, root, lang)
    if admin and chat_id == admin:
        return chat(text, e2, root, lang, cid=chat_id)
    if _exec_intent(text.lower(), lang):
        return tr("exec_member_no", lang)
    base, kind2 = _rule_answer(text, e2, root, lang)
    if kind2 in ("talk", "data"):
        return base
    if u.get("paid"):
        # paid members bring their own AI key (Claude/GPT/Gemini/Grok);
        # the operator's Claude key is only the fallback for members the
        # admin sponsored via /승인
        return _llm_answer(env, text, base, u.get("lang") or "en",
                           provider=u.get("provider") or "claude",
                           token=u.get("token") or None) or base
    # free members: the operator's free-quota LLM key answers in
    # conversation; rules are the fallback when the key is absent, the
    # daily cap is hit, or the call fails
    return _free_llm(env, root, text, base, lang) or base


def _register_commands(token):
    """setMyCommands: typing / in the chat pops the command list. English
    is the default scope; Korean gets its own localized set."""
    cmds = {
        "en": [("pick", "latest pick ranking"),
               ("funding", "funding ranking"),
               ("print", "is any Print worth taking now?"),
               ("carry", "funding-carry seats"),
               ("bot", "bot status and recent fills"),
               ("balance", "account balance"),
               ("trades", "recent fills"),
               ("menu", "everything I can do"),
               ("mode", "free / paid tier")],
        "ko": [("pick", "추천픽 순위 (최근 계산본)"),
               ("funding", "펀딩 순위"),
               ("print", "지금 잡을 만한 프린트 있나 판정"),
               ("carry", "펀딩캐리 자리·알람"),
               ("bot", "봇 상태·최근 체결"),
               ("balance", "계좌 잔고"),
               ("trades", "최근 체결 이력"),
               ("menu", "전체 기능 보기"),
               ("mode", "무료/유료 모드 전환")],
        "zh": [("pick", "最新推荐排行"), ("funding", "资金费排行"),
               ("print", "现在有值得参与的 Print 吗"),
               ("carry", "资金费套利机会"), ("bot", "机器人状态·最近成交"),
               ("balance", "账户余额"), ("trades", "最近成交记录"),
               ("menu", "查看全部功能"), ("mode", "免费/付费模式")],
        "ja": [("pick", "最新ピックランキング"), ("funding", "ファンディング順位"),
               ("print", "今取る価値のある Print はあるか"),
               ("carry", "ファンディングキャリー候補"),
               ("bot", "ボット状態・最近の約定"), ("balance", "口座残高"),
               ("trades", "最近の約定履歴"), ("menu", "全機能を見る"),
               ("mode", "無料/有料モード")],
        "vi": [("pick", "xếp hạng pick mới nhất"),
               ("funding", "xếp hạng funding"),
               ("print", "có Print đáng tham gia không?"),
               ("carry", "cơ hội funding carry"),
               ("bot", "trạng thái bot, khớp lệnh gần đây"),
               ("balance", "số dư tài khoản"),
               ("trades", "lịch sử khớp lệnh"),
               ("menu", "xem tất cả chức năng"),
               ("mode", "chế độ miễn phí/trả phí")],
        "hi": [("pick", "नवीनतम पिक रैंकिंग"), ("funding", "फंडिंग रैंकिंग"),
               ("print", "क्या अभी कोई Print लेने लायक है?"),
               ("carry", "फंडिंग कैरी अवसर"), ("bot", "बॉट स्थिति·हाल के फिल"),
               ("balance", "खाता बैलेंस"), ("trades", "हाल की ट्रेड"),
               ("menu", "सभी सुविधाएँ"), ("mode", "मुफ्त/सशुल्क मोड")],
        "id": [("pick", "peringkat pick terbaru"),
               ("funding", "peringkat funding"),
               ("print", "adakah Print yang layak sekarang?"),
               ("carry", "peluang funding carry"),
               ("bot", "status bot, eksekusi terbaru"),
               ("balance", "saldo akun"), ("trades", "riwayat eksekusi"),
               ("menu", "semua fitur"), ("mode", "mode gratis/berbayar")],
        "ru": [("pick", "свежий рейтинг пиков"),
               ("funding", "рейтинг фандинга"),
               ("print", "стоит ли сейчас брать Print?"),
               ("carry", "возможности кэрри"),
               ("bot", "статус бота, последние сделки"),
               ("balance", "баланс счёта"), ("trades", "история сделок"),
               ("menu", "все функции"), ("mode", "бесплатный/платный режим")],
        "pt": [("pick", "ranking de picks mais recente"),
               ("funding", "ranking de funding"),
               ("print", "algum Print vale a pena agora?"),
               ("carry", "oportunidades de carry"),
               ("bot", "status do bot, execuções recentes"),
               ("balance", "saldo da conta"), ("trades", "execuções recentes"),
               ("menu", "todas as funções"), ("mode", "modo grátis/pago")],
        "tr": [("pick", "en yeni pick sıralaması"),
               ("funding", "funding sıralaması"),
               ("print", "şu an değer bir Print var mı?"),
               ("carry", "funding carry fırsatları"),
               ("bot", "bot durumu, son işlemler"),
               ("balance", "hesap bakiyesi"), ("trades", "son işlem geçmişi"),
               ("menu", "tüm özellikler"), ("mode", "ücretsiz/ücretli mod")],
        "es": [("pick", "ranking de picks más reciente"),
               ("funding", "ranking de funding"),
               ("print", "¿algún Print vale la pena ahora?"),
               ("carry", "oportunidades de carry"),
               ("bot", "estado del bot, ejecuciones recientes"),
               ("balance", "saldo de la cuenta"),
               ("trades", "ejecuciones recientes"),
               ("menu", "todas las funciones"), ("mode", "modo gratis/pago")],
    }
    for code, rows in cmds.items():
        body = json.dumps([{"command": c, "description": d}
                           for c, d in rows])
        try:
            if code == "en":
                _call(token, "setMyCommands", commands=body)
            else:
                _call(token, "setMyCommands", commands=body,
                      language_code=code)
        except Exception:
            pass                 # cosmetic; the bot works without it


def main():
    env = _env()
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    gate = str(env.get("TELEGRAM_CHAT_ID", ""))
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not token:
        print("TELEGRAM_BOT_TOKEN 이 .env 에 없다. @BotFather 로 봇을 만들고 "
              "토큰·챗ID 를 넣을 것.")
        return
    central = env.get("TELEBOT_CENTRAL", "") == "1"
    if not central and not gate:
        print("개인 모드에는 TELEGRAM_CHAT_ID 가 필수다. 없으면 아무나 "
              "접근할 수 있어 기동을 거부한다.")
        return
    print("텔레봇 시작"
          + (" (중앙 운영 모드)" if central else " (개인 모드)")
          + ". 중지: Ctrl+C")
    _register_commands(token)
    # Warm up conversation mode at startup instead of on the first message
    # (08-24 user request): by the time anyone types, the model is usually
    # already in place and nobody sees the preparing notice.
    if _chat_local_on() and _LOCAL["state"] == "":
        _LOCAL["state"] = "preparing"
        import threading
        threading.Thread(target=_prepare_local, daemon=True).start()
    # Built-in hourly Print thread: opportunity alerts (opt out with
    # TELEBOT_PRINT_WATCH=0) plus 24h cycle settlement, which always runs
    # when signing credentials exist. Needs a chat to push to.
    if gate:
        import threading
        threading.Thread(target=_print_watch_loop,
                         args=(token, gate, env, root), daemon=True).start()
    offset = 0
    while True:
        # version watch: at startup and every six hours, tell the owner
        # once per new PyPI version, in their language
        if time.time() - _UPD["checked"] > 6 * 3600:
            _UPD["checked"] = time.time()
            nv = _update_check(root)
            if nv and gate:
                from .telebot_i18n import tr
                lang = (( _load_users(root).get(gate) or {}).get("lang")
                        if central else _pers_lang(root)) or "ko"
                try:
                    _send(token, gate, tr("update_available", lang)
                          .format(nv, _pkg_version() or "?"))
                except Exception:
                    pass
        try:
            d = _call(token, "getUpdates", offset=offset, timeout=30)
        except Exception:
            time.sleep(5)
            continue
        for u in d.get("result", []):
            offset = u["update_id"] + 1
            try:
                _process_update(u, env, root, gate, central, token)
            except Exception as ex:
                print(f"업데이트 처리 오류(건너뜀): {type(ex).__name__}")


def _pers_lang(root, set_to=None):
    """Personal-mode language, chosen once with the flag keyboard and kept
    in outputs/telebot_lang.json. Empty string until chosen."""
    p = os.path.join(root, "outputs", "telebot_lang.json")
    if set_to is not None:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"lang": set_to}, f)
        return set_to
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f).get("lang", "")
    except (OSError, ValueError):
        return ""


def _process_update(u, env, root, gate, central, token):
    cq = u.get("callback_query")
    if cq and not central:
        # Personal mode flag keyboard: save the language, then greet in it,
        # with the conversation-mode notice when the model is still warming
        # (08-24: the notice belongs right after the language pick).
        from .telebot_i18n import tr
        cid = str((cq.get("message", {}).get("chat") or {}).get("id", ""))
        data = str(cq.get("data") or "")
        try:
            _call(token, "answerCallbackQuery",
                  callback_query_id=cq.get("id", ""))
        except Exception:
            pass
        if not gate or cid == gate:
            # ordered flow (08-24 user decision): language -> free/paid ->
            # free -> the conversation-mode warm-up notice, in that language
            from .telebot_i18n import tier_kb
            if data.startswith("lang:"):
                lang = _pers_lang(root, set_to=data.split(":", 1)[1])
                # onboarding order (08-25 user decision): language, then
                # wallet address, then API key, then the free/paid pick.
                # Stages are skipped when .env already has the value.
                if not (env.get("ADDRESS") or "").strip():
                    _setup_set(root, "addr")
                    _send(token, cid, tr("ask_wallet", lang))
                elif not (env.get("PACIFICA_API_KEY") or "").strip():
                    _setup_set(root, "key")
                    _send(token, cid, tr("ask_apikey", lang))
                else:
                    _send(token, cid, tr("tier_pick", lang),
                          kb=tier_kb(lang))
            elif data.startswith("tier:"):
                lang = _pers_lang(root) or "en"
                if data.split(":", 1)[1] == "free":
                    msg = tr("greet_reply", lang)
                    if _chat_local_on() and _LOCAL["state"] != "ready":
                        msg += "\n\n" + tr("chat_local_preparing", lang)
                    _send(token, cid, msg)
                else:
                    _send(token, cid, tr("pers_paid_hint", lang))
        return
    if cq and central:
        # 국기 버튼 응답: 언어 저장 후 그 언어로 주소 요청
        from .telebot_i18n import tr
        cid = str((cq.get("message", {}).get("chat") or {})
                  .get("id", ""))
        data = str(cq.get("data") or "")
        try:
            _call(token, "answerCallbackQuery",
                  callback_query_id=cq.get("id", ""))
        except Exception:
            pass
        pitch_only = (cid and cid != gate
                      and env.get("TELEBOT_MEMBER_OPEN", "") != "1")
        if data.startswith("lang:") and cid:
            users = _load_users(root)
            rec = users.get(cid) or {"address": "", "paid": False}
            rec["lang"] = data.split(":", 1)[1]
            users[cid] = rec
            _save_users(root, users)
            if pitch_only:
                # install desk mode: the language pick localizes the pitch,
                # then everything funnels to installing their own bot
                _send(token, cid, tr("install_pitch", rec["lang"]))
                return
            # language chosen -> tier choice next, in that language
            from .telebot_i18n import tier_kb
            _send(token, cid, tr("tier_pick", rec["lang"]),
                  kb=tier_kb(rec["lang"]))
        elif pitch_only:
            # stale tier/provider keyboards from the open era: pitch too
            users = _load_users(root)
            lang = ((users.get(cid) or {}).get("lang")) or "en"
            _send(token, cid, tr("install_pitch", lang))
            return
        elif data.startswith("tier:") and cid:
            users = _load_users(root)
            rec = users.get(cid) or {"lang": "en", "address": "",
                                     "paid": False}
            lang = rec.get("lang") or "en"
            choice = data.split(":", 1)[1]
            if choice == "paid":
                from .telebot_i18n import prov_kb
                users[cid] = rec
                _save_users(root, users)
                _send(token, cid, tr("prov_pick", lang), kb=prov_kb())
            else:
                rec["paid"] = False
                rec["token"] = ""
                rec["await_token"] = False
                users[cid] = rec
                _save_users(root, users)
                msg = tr("mode_now_free", lang) + "\n\n" \
                    + (tr("ask_addr", lang) if not rec.get("address")
                       else tr("menu_extra", lang))
                # free picked -> warm-up notice, operator's chat only (the
                # model runs on this machine for the operator, not members)
                if (cid == gate and _chat_local_on()
                        and _LOCAL["state"] != "ready"):
                    msg += "\n\n" + tr("chat_local_preparing", lang)
                _send(token, cid, msg)
        elif data.startswith("prov:") and cid:
            from .telebot_i18n import PROVIDERS
            users = _load_users(root)
            rec = users.get(cid) or {"lang": "en", "address": "",
                                     "paid": False}
            lang = rec.get("lang") or "en"
            prov = data.split(":", 1)[1]
            if prov in PROVIDERS:
                rec["provider"] = prov
                rec["await_token"] = True
                users[cid] = rec
                _save_users(root, users)
                p = PROVIDERS[prov]
                _send(token, cid, tr("ask_token", lang).format(
                    p["name"], p["prefix"], p["url"]))
        return
    msg = u.get("message") or {}
    cid = str((msg.get("chat") or {}).get("id", ""))
    text = (msg.get("text") or "").strip().split("@")[0]
    if cid and text:
        # instant feedback: the local model takes seconds to write, so the
        # chat shows "typing..." the moment the message lands (08-24)
        try:
            _call(token, "sendChatAction", chat_id=cid, action="typing")
        except Exception:
            pass
        _PRINT_BUSY["token"], _PRINT_BUSY["cid"] = token, cid
    # owner says the update word while a newer version is known -> the bot
    # upgrades itself and restarts; only the owner, never members
    if _UPD["latest"] and cid == gate:
        from .telebot_i18n import intent_words
        lang_u = (((_load_users(root).get(gate) or {}).get("lang")
                   if central else _pers_lang(root)) or "ko")
        if any(k in text.lower() for k in intent_words("update", lang_u)):
            _do_update(root, lang_u, token, cid)
            return
    try:
        if central:
            out = _handle_member(text, cid, env, root, gate)
        else:
            if gate and cid != gate:
                return        # 개인 모드: 등록된 챗만
            lang = _pers_lang(root)
            if not lang:
                from .telebot_i18n import tr
                out = ("KB", tr("pick_lang", "any"))
            else:
                r = _setup_route(text, token, cid, env, root, lang,
                                 msg_id=str(msg.get("message_id", "")))
                if r is not None:
                    out = r or None
                else:
                    out = chat(text, env, root, lang=lang, cid=cid)
    except Exception as ex:
        from .telebot_i18n import tr
        lang = "ko"
        if central:
            lang = ((_load_users(root).get(cid) or {})
                    .get("lang")) or "en"
        out = tr("error", lang).format(type(ex).__name__)
    if isinstance(out, tuple) and out and out[0] == "KB":
        from .telebot_i18n import lang_kb
        _send(token, cid, out[1], kb=lang_kb())
    elif isinstance(out, tuple) and out and out[0] == "KB_TIER":
        from .telebot_i18n import tier_kb
        lang = "en"
        if central:
            lang = ((_load_users(root).get(cid) or {})
                    .get("lang")) or "en"
        _send(token, cid, out[1], kb=tier_kb(lang))
    else:
        _send(token, cid, out)


if __name__ == "__main__":
    main()
