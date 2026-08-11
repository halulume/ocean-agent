"""봇이 연 델타뉴트럴 포지션 상태를 state.json에 기록/복원.

state.json이 포지션의 source of truth다. 봇이 연 것만 봇이 닫는다.
"""

import json
import os

# Legacy default. mcp_server overrides this with an absolute, network-tagged
# path at import time; only the funding CLI (main.py) uses the default.
STATE_FILE = "state.json"


def _path() -> str:
    """Resolve where the state file lives.

    Priority:
      1. An explicit override (STATE_FILE reassigned to another path) wins.
      2. Backward compatibility: if the legacy cwd-relative "state.json"
         already exists, keep using it so a live bot's state is not orphaned.
         No silent migration.
      3. Otherwise anchor new state to the repo outputs/ dir next to the
         package, tagged per network, instead of depending on the cwd.
         If there is no outputs/ dir (pip install), fall back to the
         network-tagged home data file.
    """
    if STATE_FILE != "state.json":
        return STATE_FILE
    if os.path.exists(STATE_FILE):
        return STATE_FILE
    from . import data_file, data_tag
    out_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outputs")
    if os.path.isdir(out_dir):
        return os.path.join(out_dir, f"state_{data_tag()}.json")
    return data_file("state.json")


def load() -> dict:
    path = _path()
    if not os.path.exists(path):
        return {"position": None}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(state: dict) -> None:
    path = _path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
