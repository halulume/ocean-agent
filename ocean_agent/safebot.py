# -*- coding: utf-8 -*-
"""Ocean Safe Bot: group-chat guard against bot accounts and promo spam.

A second, separate Telegram bot (its own BotFather token) that sits in the
community group as an ADMIN and does three things:

  1. Gate every new member: mute on join, show an "I'm human" button, and
     kick whoever does not press it within GATE_SEC (bots cannot press
     inline buttons on their own). Kicked accounts are unbanned right away
     so a real person can simply rejoin and try again.
  2. Filter probation messages: while a member is on probation (first
     PROBATION_MSGS messages after verifying), anything that smells like
     promotion is deleted on sight: invite links, URLs, @mentions of other
     channels, messages forwarded from channels, and a small list of scam
     phrases. Strikes escalate: delete -> mute a day -> ban.
  3. Flood control: FLOOD_N messages inside FLOOD_SEC gets a short mute.

Every action is reported to the operator chat (SAFEBOT_ADMIN, falls back
to TELEGRAM_CHAT_ID). Trusted members (past probation) chat freely; admins
are never touched.

Setup: create the bot at @BotFather, put SAFEBOT_TOKEN=... in .env, add
the bot to the group and promote it to admin with "delete messages",
"ban users" and "restrict members" rights, then run:

    python -m ocean_agent.safebot

State lives in outputs/safebot_state.json (strikes, verified members).
"""
import json
import os
import re
import time
import urllib.parse
import urllib.request

API = "https://api.telegram.org/bot{token}/{method}"
GATE_SEC = 120               # seconds to press the human button
PROBATION_MSGS = 5           # messages watched closely after verifying
# Duplicate-spam rule (08-24 operator spec): the SAME text may appear up
# to DUP_ALLOW times; from the next one the message is deleted, the
# sender is muted DUP_MUTE seconds and warned in chat, and the third
# warning is a kick. Counted per user inside a rolling DUP_WINDOW.
DUP_ALLOW = 5
DUP_MUTE = 10
DUP_WINDOW = 600
DUP_KICK_WARNS = 3
MUTE_DAY = 24 * 3600

# Promotion smells. Kept deliberately blunt: probation members have no
# business posting links at all; regulars are not filtered.
RE_LINK = re.compile(r"(?:t\.me/|telegram\.me/|https?://|www\.)", re.I)
RE_MENTION = re.compile(r"@[A-Za-z]\w{4,}")
SCAM_WORDS = ("리딩방", "수익보장", "수익 보장", "원금보장", "코인추천방",
              "guaranteed profit", "pump group", "vip signal", "vip signals",
              "airdrop claim", "dm me", "dm for", "먼저 연락", "부업",
              "재테크방", "혼자 알기 아까운")


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
    for k, v in list(params.items()):
        if isinstance(v, (dict, list)):
            params[k] = json.dumps(v)
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(API.format(token=token, method=method),
                                 data=data)
    with urllib.request.urlopen(req, timeout=35) as r:
        return json.loads(r.read())


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "outputs", "safebot_state.json")


def _load():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"verified": {}, "strikes": {}, "pending": {}}


def _save(st):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)


def log(msg):
    line = f"[{time.strftime('%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)


def _report(token, admin, msg):
    if not admin:
        return
    try:
        _call(token, "sendMessage", chat_id=admin, text="🛡️ " + msg)
    except Exception:
        pass


def _mute(token, chat, uid, until=None):
    perms = {"can_send_messages": False}
    kw = {"chat_id": chat, "user_id": uid, "permissions": perms}
    if until:
        kw["until_date"] = int(time.time() + until)
    _call(token, "restrictChatMember", **kw)


def _unmute(token, chat, uid):
    perms = {"can_send_messages": True, "can_send_audios": True,
             "can_send_documents": True, "can_send_photos": True,
             "can_send_videos": True, "can_send_other_messages": True,
             "can_add_web_page_previews": True}
    _call(token, "restrictChatMember", chat_id=chat, user_id=uid,
          permissions=perms)


def _kick(token, chat, uid):
    _call(token, "banChatMember", chat_id=chat, user_id=uid)
    try:
        _call(token, "unbanChatMember", chat_id=chat, user_id=uid,
              only_if_banned=True)      # kicked, not permanently banned
    except Exception:
        pass


def _is_admin(token, chat, uid, cache={}):
    key = (chat, uid)
    hit = cache.get(key)
    if hit and time.time() - hit[1] < 600:
        return hit[0]
    try:
        r = _call(token, "getChatMember", chat_id=chat, user_id=uid)
        ok = r["result"]["status"] in ("creator", "administrator")
    except Exception:
        ok = False
    cache[key] = (ok, time.time())
    return ok


def _spammy(msg):
    """Why this probation message must go, or '' if it is fine."""
    text = (msg.get("text") or msg.get("caption") or "")
    low = text.lower()
    if msg.get("forward_from_chat"):
        return "채널 전달글"
    if RE_LINK.search(text):
        return "링크"
    if RE_MENTION.search(text):
        return "외부 @멘션"
    for w in SCAM_WORDS:
        if w in low:
            return f"홍보 문구({w})"
    return ""


def _on_join(token, chat, user, st, admin):
    uid = str(user["id"])
    name = (user.get("first_name") or "") + " " + (user.get("last_name") or "")
    name = name.strip() or user.get("username") or uid
    if user.get("is_bot"):
        _kick(token, chat, int(uid))
        _report(token, admin, f"봇 계정 즉시 퇴장: {name}")
        return
    try:
        _mute(token, chat, int(uid))
    except Exception:
        _report(token, admin, f"입장 게이트 실패(권한 확인 필요): {name}")
        return
    # Chat-facing text is English only (08-24: the community group runs
    # in English); operator reports stay Korean for the operator.
    kb = {"inline_keyboard": [[{
        "text": "🙋 I am human",
        "callback_data": f"human:{uid}"}]]}
    try:
        r = _call(token, "sendMessage", chat_id=chat,
                  text=(f"Welcome, {name}! Please press the button below "
                        f"within {GATE_SEC} seconds to verify you are "
                        f"human. Accounts that do not verify are removed "
                        f"(you can rejoin and try again)."),
                  reply_markup=kb)
        mid = r["result"]["message_id"]
    except Exception:
        mid = 0
    st["pending"][uid] = {"chat": chat, "until": time.time() + GATE_SEC,
                          "msg": mid, "name": name}
    _save(st)


def _expire_pending(token, st, admin):
    now = time.time()
    for uid, p in list(st["pending"].items()):
        if now < p["until"]:
            continue
        try:
            _kick(token, p["chat"], int(uid))
            _report(token, admin,
                    f"검증 시간 초과로 퇴장: {p.get('name', uid)} "
                    f"(재입장하면 다시 기회가 있습니다)")
        except Exception:
            pass
        if p.get("msg"):
            try:
                _call(token, "deleteMessage", chat_id=p["chat"],
                      message_id=p["msg"])
            except Exception:
                pass
        st["pending"].pop(uid, None)
    _save(st)


def _strike(token, chat, uid, name, why, st, admin):
    n = st["strikes"].get(uid, 0) + 1
    st["strikes"][uid] = n
    _save(st)
    if n == 1:
        _report(token, admin, f"홍보글 삭제(1회): {name} · {why}")
    elif n == 2:
        try:
            _mute(token, chat, int(uid), until=MUTE_DAY)
        except Exception:
            pass
        _report(token, admin, f"홍보글 반복, 24시간 음소거: {name} · {why}")
    else:
        try:
            _call(token, "banChatMember", chat_id=chat, user_id=int(uid))
        except Exception:
            pass
        _report(token, admin, f"홍보글 3회, 차단: {name} · {why}")


def main():
    env = _env()
    token = env.get("SAFEBOT_TOKEN", "")
    admin = env.get("SAFEBOT_ADMIN", "") or env.get("TELEGRAM_CHAT_ID", "")
    if not token:
        print("SAFEBOT_TOKEN 이 .env 에 없다. @BotFather 로 봇을 만들고 "
              "토큰을 넣은 뒤, 그룹에 관리자(삭제·차단·제한 권한)로 추가할 것.")
        return
    st = _load()
    flood = {}
    log("오션 세이프 봇 시작. 중지: Ctrl+C")
    offset = 0
    while True:
        _expire_pending(token, st, admin)
        try:
            d = _call(token, "getUpdates", offset=offset, timeout=25,
                      allowed_updates=["message", "callback_query",
                                       "chat_member"])
        except Exception:
            time.sleep(5)
            continue
        for u in d.get("result", []):
            offset = u["update_id"] + 1
            try:
                _handle(u, token, st, flood, admin)
            except Exception as ex:
                log(f"처리 오류(건너뜀): {type(ex).__name__}")


def _handle(u, token, st, flood, admin):
    cq = u.get("callback_query")
    if cq:
        data = str(cq.get("data") or "")
        uid = str(cq.get("from", {}).get("id", ""))
        if data == f"human:{uid}" and uid in st["pending"]:
            p = st["pending"].pop(uid)
            st["verified"][uid] = {"at": time.time(), "msgs": 0}
            _save(st)
            try:
                _unmute(token, p["chat"], int(uid))
            except Exception:
                pass
            if p.get("msg"):
                try:
                    _call(token, "deleteMessage", chat_id=p["chat"],
                          message_id=p["msg"])
                except Exception:
                    pass
            _call(token, "answerCallbackQuery",
                  callback_query_id=cq.get("id", ""), text="확인됐습니다 ✅")
        else:
            # pressing someone else's button does nothing
            _call(token, "answerCallbackQuery",
                  callback_query_id=cq.get("id", ""))
        return
    msg = u.get("message")
    if not msg:
        return
    chat = msg.get("chat", {})
    if chat.get("type") not in ("group", "supergroup"):
        return
    cid = chat["id"]
    # joins arrive as service messages
    for nm in msg.get("new_chat_members", []) or []:
        _on_join(token, cid, nm, st, admin)
    user = msg.get("from") or {}
    uid = str(user.get("id") or "")
    if not uid or user.get("is_bot"):
        return
    if _is_admin(token, cid, int(uid), ):
        return
    name = (user.get("first_name") or "") or user.get("username") or uid
    # unverified members should be muted; if a message slips through
    # (e.g. the bot was added after they joined), let it pass silently
    v = st["verified"].get(uid)
    # Duplicate-spam rule: identical text up to DUP_ALLOW times, then
    # delete + short mute + English warning; the third warning is a kick.
    text_now = (msg.get("text") or msg.get("caption") or "").strip()
    if text_now:
        now = time.time()
        q = flood.setdefault(uid, [])
        q.append((now, text_now.lower()))
        while q and now - q[0][0] > DUP_WINDOW:
            q.pop(0)
        dups = sum(1 for _, t in q if t == text_now.lower())
        if dups > DUP_ALLOW:
            try:
                _call(token, "deleteMessage", chat_id=cid,
                      message_id=msg["message_id"])
            except Exception:
                pass
            warns = st.setdefault("dup_warns", {}).get(uid, 0) + 1
            st["dup_warns"][uid] = warns
            _save(st)
            if warns >= DUP_KICK_WARNS:
                _kick(token, cid, int(uid))
                try:
                    _call(token, "sendMessage", chat_id=cid,
                          text=(f"🛡️ {name} was removed after "
                                f"{DUP_KICK_WARNS} spam warnings."))
                except Exception:
                    pass
                _report(token, admin, f"도배 경고 3회, 강퇴: {name}")
            else:
                try:
                    _mute(token, cid, int(uid), until=DUP_MUTE)
                except Exception:
                    pass
                try:
                    _call(token, "sendMessage", chat_id=cid,
                          text=(f"⚠️ {name}, please stop repeating the "
                                f"same message. Muted for {DUP_MUTE} "
                                f"seconds. Warning {warns}/"
                                f"{DUP_KICK_WARNS}; the next ones lead "
                                f"to removal."))
                except Exception:
                    pass
                _report(token, admin,
                        f"도배 경고 {warns}/{DUP_KICK_WARNS}: {name}")
            return
    # probation filter: the member's first PROBATION_MSGS messages
    if v is not None and v.get("msgs", 0) < PROBATION_MSGS:
        why = _spammy(msg)
        if why:
            try:
                _call(token, "deleteMessage", chat_id=cid,
                      message_id=msg["message_id"])
            except Exception:
                pass
            _strike(token, cid, uid, name, why, st, admin)
            return
        v["msgs"] = v.get("msgs", 0) + 1
        _save(st)


if __name__ == "__main__":
    main()
