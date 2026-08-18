"""텔레그램 알림. 토큰/챗ID가 없으면 콘솔 출력만 한다."""

import os

import requests


def send(text: str) -> None:
    print(f"[알림] {text}")
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
