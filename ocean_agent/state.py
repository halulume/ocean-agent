"""봇이 연 델타뉴트럴 포지션 상태를 state.json에 기록/복원.

state.json이 포지션의 source of truth다. 봇이 연 것만 봇이 닫는다.
"""

import json
import os

STATE_FILE = "state.json"


def load() -> dict:
    if not os.path.exists(STATE_FILE):
        return {"position": None}
    with open(STATE_FILE, encoding="utf-8") as f:
        return json.load(f)


def save(state: dict) -> None:
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)
