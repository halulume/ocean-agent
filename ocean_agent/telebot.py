# -*- coding: utf-8 -*-
"""Telegram interface with zero LLM calls, for users without Claude credits.

The product's functions are plain Python already - the MCP server merely wraps
them for Claude, and Claude is a conversational skin, not a dependency. What a
credit-less user lacks is an entrance. This is that entrance: fixed commands
long-polling the Telegram API with nothing but the standard library and the
same functions the MCP tools call.

Commands are deliberately fixed rather than free-form. No model means no
interpretation, so each command maps to exactly one function and its output
is the function's own text. That also makes the surface auditable - there is
nothing this bot can be talked into.

No orders. Reading balances, positions, funding tables and alerts is safe to
expose to a chat app; placing trades is not, and stays with the local
launcher scripts where the user's own hands are on it.

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


def _env():
    out = dict(os.environ)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = os.path.join(root, ".env")
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
            errors="replace", timeout=120)
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


_SYS_PROMPT = (
    "너는 오션 에이전트(파시피카 트레이딩 도구)의 안내원이다. "
    "Answer briefly, ONLY from the data below, in the user's language "
    "(code: {lang}). 데이터에 없는 것은 모른다고 말한다. 숫자를 지어내지 "
    "않는다. 투자 조언·매수 권유는 하지 않는다.")


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
    ex = _exec_step(text.lower(), cid, root, lang)
    if ex:
        return ex
    base = _rule_answer(text, env, root, lang)
    cli_ok = env.get("TELEBOT_CLI_MIRROR", "") == "1"   # 옵트인 전엔 봉인
    return ((cli_ok and _cli_answer(text, base))
            or _llm_answer(env, text, base, lang)  # 2순위: API 키 있으면
            or _free_llm(env, root, text, base, lang)  # 3순위: 무료쿼터 LLM
            or base)                         # 4순위: 무료 규칙 답변


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
        return _why(cl, env, sym, lang)
    # "펀딩이 뭐야?" is a definition, not a data lookup: glossary first
    # when the sentence carries a what-is marker and a known term.
    g = _gloss_hit(low, lang)
    if g:
        return g
    if sym:
        return _sym_info(cl, sym, lang)
    # Best-scoring intent wins, so word combinations resolve to the intent
    # they share the most (and longest) keywords with, instead of whichever
    # if-branch happened to come first.
    DATA = {"pick": "/pick", "carry": "/carry", "funding": "/funding",
            "balance": "/balance", "trades": "/trades", "bot": "/bot"}
    TALK = {"help": "help_reply", "alerts": "alerts_info",
            "greet": "greet_reply", "thanks": "thanks_reply"}
    best, best_s = None, 0
    for it in list(DATA) + list(TALK) + ["pick_stock", "pick_coin"]:
        s = _score(low, it, lang)
        if s > best_s:
            best, best_s = it, s
    if best == "pick_stock":
        return _pick_table(root, lang, cls="stock")
    if best == "pick_coin":
        return _pick_table(root, lang, cls="coin")
    if best in DATA:
        return handle(DATA[best], env, root, lang)
    if best in TALK:
        return tr(TALK[best], lang)
    return tr("not_understood", lang) + "\n" + tr("menu", lang)


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
    python.exe and pythonw.exe alike, any install path)."""
    import subprocess
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process | Where-Object { "
             "$_.CommandLine -match 'ocean_agent.bracket_trader' -and "
             "$_.Name -match 'python' }).ProcessId"],
            capture_output=True, text=True, timeout=30)
        return [int(x) for x in r.stdout.split() if x.strip().isdigit()]
    except Exception:
        return []


def _exec_apply(action, root, lang):
    from .telebot_i18n import tr
    import subprocess
    pids = _bot_pids()
    if action == "on":
        if pids:
            return tr("exec_already_on", lang)
        logf = open(os.path.join(root, "outputs", "bracket_live.log"), "a",
                    encoding="utf-8")
        # DETACHED_PROCESS | CREATE_NO_WINDOW: survives the telebot, shows
        # nothing. stdin=DEVNULL is the MCP zombie lesson (08-22).
        subprocess.Popen(
            [sys.executable, "-u", "-m", "ocean_agent.bracket_trader"],
            cwd=root, stdout=logf, stderr=logf,
            stdin=subprocess.DEVNULL,
            creationflags=0x08000008 if os.name == "nt" else 0)
        return tr("exec_done_on", lang)
    if not pids:
        return tr("exec_already_off", lang)
    for pid in pids:
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            f"Stop-Process -Id {pid} -Force"],
                           capture_output=True, timeout=30)
        except Exception:
            pass
    return tr("exec_done_off", lang)


def _exec_step(low, cid, root, lang):
    """Reply if this message belongs to the execution flow, else None."""
    from .telebot_i18n import tr
    p = _PENDING.get(cid)
    if p and time.time() < p[1]:
        if _score(low, "yes", lang):
            _PENDING.pop(cid, None)
            return _exec_apply(p[0], root, lang)
        if _score(low, "no", lang):
            _PENDING.pop(cid, None)
            return tr("exec_cancel", lang)
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
    base = _rule_answer(text, e2, root, lang)
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
               ("carry", "funding-carry seats"),
               ("bot", "bot status and recent fills"),
               ("balance", "account balance"),
               ("trades", "recent fills"),
               ("menu", "everything I can do"),
               ("mode", "free / paid tier")],
        "ko": [("pick", "추천픽 순위 (최근 계산본)"),
               ("funding", "펀딩 순위"),
               ("carry", "펀딩캐리 자리·알람"),
               ("bot", "봇 상태·최근 체결"),
               ("balance", "계좌 잔고"),
               ("trades", "최근 체결 이력"),
               ("menu", "전체 기능 보기"),
               ("mode", "무료/유료 모드 전환")],
        "zh": [("pick", "最新推荐排行"), ("funding", "资金费排行"),
               ("carry", "资金费套利机会"), ("bot", "机器人状态·最近成交"),
               ("balance", "账户余额"), ("trades", "最近成交记录"),
               ("menu", "查看全部功能"), ("mode", "免费/付费模式")],
        "ja": [("pick", "最新ピックランキング"), ("funding", "ファンディング順位"),
               ("carry", "ファンディングキャリー候補"),
               ("bot", "ボット状態・最近の約定"), ("balance", "口座残高"),
               ("trades", "最近の約定履歴"), ("menu", "全機能を見る"),
               ("mode", "無料/有料モード")],
        "vi": [("pick", "xếp hạng pick mới nhất"),
               ("funding", "xếp hạng funding"),
               ("carry", "cơ hội funding carry"),
               ("bot", "trạng thái bot, khớp lệnh gần đây"),
               ("balance", "số dư tài khoản"),
               ("trades", "lịch sử khớp lệnh"),
               ("menu", "xem tất cả chức năng"),
               ("mode", "chế độ miễn phí/trả phí")],
        "hi": [("pick", "नवीनतम पिक रैंकिंग"), ("funding", "फंडिंग रैंकिंग"),
               ("carry", "फंडिंग कैरी अवसर"), ("bot", "बॉट स्थिति·हाल के फिल"),
               ("balance", "खाता बैलेंस"), ("trades", "हाल की ट्रेड"),
               ("menu", "सभी सुविधाएँ"), ("mode", "मुफ्त/सशुल्क मोड")],
        "id": [("pick", "peringkat pick terbaru"),
               ("funding", "peringkat funding"),
               ("carry", "peluang funding carry"),
               ("bot", "status bot, eksekusi terbaru"),
               ("balance", "saldo akun"), ("trades", "riwayat eksekusi"),
               ("menu", "semua fitur"), ("mode", "mode gratis/berbayar")],
        "ru": [("pick", "свежий рейтинг пиков"),
               ("funding", "рейтинг фандинга"),
               ("carry", "возможности кэрри"),
               ("bot", "статус бота, последние сделки"),
               ("balance", "баланс счёта"), ("trades", "история сделок"),
               ("menu", "все функции"), ("mode", "бесплатный/платный режим")],
        "pt": [("pick", "ranking de picks mais recente"),
               ("funding", "ranking de funding"),
               ("carry", "oportunidades de carry"),
               ("bot", "status do bot, execuções recentes"),
               ("balance", "saldo da conta"), ("trades", "execuções recentes"),
               ("menu", "todas as funções"), ("mode", "modo grátis/pago")],
        "tr": [("pick", "en yeni pick sıralaması"),
               ("funding", "funding sıralaması"),
               ("carry", "funding carry fırsatları"),
               ("bot", "bot durumu, son işlemler"),
               ("balance", "hesap bakiyesi"), ("trades", "son işlem geçmişi"),
               ("menu", "tüm özellikler"), ("mode", "ücretsiz/ücretli mod")],
        "es": [("pick", "ranking de picks más reciente"),
               ("funding", "ranking de funding"),
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
    offset = 0
    while True:
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


def _process_update(u, env, root, gate, central, token):
    cq = u.get("callback_query")
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
        if data.startswith("lang:") and cid:
            users = _load_users(root)
            rec = users.get(cid) or {"address": "", "paid": False}
            rec["lang"] = data.split(":", 1)[1]
            users[cid] = rec
            _save_users(root, users)
            # language chosen -> tier choice next, in that language
            from .telebot_i18n import tier_kb
            _send(token, cid, tr("tier_pick", rec["lang"]),
                  kb=tier_kb(rec["lang"]))
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
                _send(token, cid, tr("mode_now_free", lang) + "\n\n"
                      + (tr("ask_addr", lang) if not rec.get("address")
                         else tr("menu_extra", lang)))
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
    try:
        if central:
            out = _handle_member(text, cid, env, root, gate)
        else:
            if gate and cid != gate:
                return        # 개인 모드: 등록된 챗만
            out = chat(text, env, root, cid=cid)
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
