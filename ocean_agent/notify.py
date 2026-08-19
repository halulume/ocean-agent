"""Telegram notifications; console only when no token is configured.

The token lives in .env, and until 2026-08-19 this module read it straight
from os.environ. Anything that fired before the first client was built ran
with an empty environment, so a bot refusing to start, a watchdog reviving
one, and every circuit-breaker warning went to a console nobody was
watching. The .env is loaded here instead, once, on first use.
"""

import os

import requests

_env_loaded = False


def _load_env_once() -> None:
    global _env_loaded
    if _env_loaded:
        return
    _env_loaded = True
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for cand in (os.environ.get("PACIFICA_ENV_FILE"),
                 os.path.join(os.path.expanduser("~"), ".ocean-agent", ".env"),
                 os.path.join(os.getcwd(), ".env"),
                 os.path.join(os.path.dirname(os.path.dirname(
                     os.path.abspath(__file__))), ".env")):
        if cand and os.path.exists(cand):
            load_dotenv(cand, override=False)
            break


def send(text: str) -> None:
    print(f"[알림] {text}")
    _load_env_once()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except requests.RequestException as e:
        # The exception text can embed the request URL, which contains the
        # bot token. Redact it so logs never leak the credential.
        msg = str(e).replace(token, "***") if token else str(e)
        print(f"[알림 실패] {msg}")
