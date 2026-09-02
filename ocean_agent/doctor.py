"""환경 진단: 봇을 처음 설치한 사람이 실행해서 준비 상태를 확인한다.

실행: python -m ocean_agent.doctor
"""

import os
import shutil
import sys


def check(name: str, ok: bool, detail: str = "") -> bool:
    mark = "✅" if ok else "❌"
    print(f"{mark} {name}" + (f", {detail}" if detail else ""))
    return ok


def _claude_entry():
    """(설정 파일 경로, ocean-agent 항목). 등록 안 됐으면 항목이 None."""
    import json
    cands = []
    if sys.platform == "win32":
        cands.append(os.path.join(os.environ.get("APPDATA", ""),
                                  "Claude", "claude_desktop_config.json"))
    else:
        cands.append(os.path.expanduser(
            "~/Library/Application Support/Claude/"
            "claude_desktop_config.json"))
    # 설치기(website/install.ps1)가 쓰는 것은 위의 claude_desktop_config.json
    # 이다. 아래 둘은 개발자가 쓰는 배치라 거짓 음성을 막으려고 같이 본다.
    cands.append(os.path.expanduser("~/.claude.json"))
    cands.append(os.path.join(os.getcwd(), ".mcp.json"))
    first = ""
    for p in cands:
        if not p or not os.path.exists(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        servers = d.get("mcpServers") or {}
        for name, ent in servers.items():
            if "ocean" in name.lower() or "ocean_agent" in str(ent):
                return p, ent if isinstance(ent, dict) else {}
        first = first or p          # 파일은 있는데 항목이 없는 첫 곳
    return first, None


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    print("=== Pacifica 펀딩 봇 환경 진단 ===\n")
    all_ok = True

    # 1. Python 버전
    v = sys.version_info
    all_ok &= check("Python 3.10+", v >= (3, 10), f"{v.major}.{v.minor}.{v.micro}")

    # 2. 파이썬 패키지
    missing = []
    for mod in ("requests", "solders", "base58", "yaml", "dotenv"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    all_ok &= check("파이썬 패키지", not missing,
                    "전부 설치됨" if not missing
                    else f"누락: {missing} → pip install -r requirements.txt")

    # 3. Node.js. 파시피카가 만든 MCP 서버를 쓸 때만 필요하다. 우리 도구는
    # 파이썬이라 이것과 무관하다. 09-03 에 한 사용자가 이 ❌ 를 막힌 원인으로
    # 읽었으므로, 없어도 문제가 아니라는 것을 문구에 못 박는다.
    npx = shutil.which("npx")
    node_ok = npx is not None
    check("Node.js/npx", node_ok,
          npx or "없음. 파시피카 MCP 모드에서만 쓰므로 클로드로 쓰는 데는 "
          "문제 없습니다")

    # 4. 클로드에 등록됐나. MCP 로 쓰는 사람에게는 이것이 유일하게 중요하다.
    #
    # 09-03: 한 베타 사용자가 홈 폴더에서 이 진단기를 돌려 ❌ 셋을 받고
    # 막혔다. 셋 다 그 사람 문제가 아니었다. config.yaml 은 독립 실행 봇용이라
    # MCP 사용자에게는 없는 것이 정상이고, .env 는 설치기가 다른 곳에 쓰는데
    # 진단기가 현재 폴더만 봤고, Node.js 는 파시피카 MCP 모드 전용이다.
    # 정작 "클로드가 도구를 보는가" 는 아예 안 물어봤다.
    cfg_path, entry = _claude_entry()
    reg_ok = entry is not None
    check("클로드에 등록", reg_ok,
          f"{os.path.basename(cfg_path)} 에 있음" if reg_ok
          else ("클로드 설정에 ocean-agent 가 없습니다. 설치 명령을 다시 "
                "실행하세요" if cfg_path else "클로드 설정 파일을 못 찾았습니다. "
                "클로드를 한 번 실행한 뒤 다시 시도하세요"))
    all_ok &= reg_ok

    # 5. 설정 파일. MCP 로 등록돼 있으면 config.yaml 은 없는 것이 정상이다.
    cfg_ok = os.path.exists("config.yaml")
    if reg_ok:
        if cfg_ok:
            check("config.yaml", True, "")
    else:
        all_ok &= check("config.yaml", cfg_ok,
                        "" if cfg_ok else "봇 폴더에서 실행했는지 확인")

    import yaml
    from dotenv import load_dotenv
    # 설치기가 쓴 .env 를 MCP 서버와 같은 순서로 찾는다. 현재 폴더만 보면
    # 홈 폴더에서 돌린 사람에게 "키가 없다" 고 거짓말을 하게 된다.
    env_used = None
    for _cand in (os.environ.get("PACIFICA_ENV_FILE"),
                  ((entry or {}).get("env") or {}).get("PACIFICA_ENV_FILE"),
                  os.path.join(os.path.dirname(os.path.dirname(
                      os.path.abspath(__file__))), ".env"),
                  os.path.join(os.getcwd(), ".env")):
        if _cand and os.path.exists(_cand):
            load_dotenv(_cand, override=False)
            env_used = _cand
            break
    if env_used:
        print(f"   (.env 위치: {env_used})")
    cfg = {}
    if cfg_ok:
        with open("config.yaml", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)

    from . import address_from_env, api_key_from_env
    # config.yaml 이 없어도 메인넷으로 본다. 예전에는 빈 문자열이라 지갑
    # 주소를 어느 망에서 찾을지가 정해지지 않았고, MCP 사용자는 키가 있어도
    # "없음" 을 봤다.
    _net_url = cfg.get("base_url", "https://api.pacifica.fi")
    address = address_from_env(_net_url)
    key = api_key_from_env(_net_url)
    net_label = "테스트넷" if "test-api" in _net_url else "메인넷"
    check(f".env, 지갑 주소 ({net_label}용)", bool(address),
          f"{address[:6]}...{address[-4:]}" if address
          else "없음, 계좌 조회/실주문에 필요 (시세 조회만은 가능)")
    check(f".env, API 키 ({net_label}용)", bool(key),
          "설정됨" if key else "없음, dry-run은 가능, 실주문은 불가 "
          "(app.pacifica.fi/apikey 에서 발급)")

    # 6. 실제 연결 테스트. config.yaml 없이도 돈다. MCP 사용자에게는 이것이
    # "키가 진짜 되는가" 를 보여주는 유일한 줄이다.
    if True:
        base_url = cfg.get("base_url", "https://api.pacifica.fi")
        api_mode = cfg.get("api_mode", "rest")
        print(f"\n연결 테스트 ({api_mode.upper()} / {base_url}) ...")
        try:
            if api_mode == "mcp" and node_ok and cfg_ok:
                from .mcp_client import PacificaMCPClient
                client = PacificaMCPClient(base_url, address=address)
            else:
                from .api_client import PacificaClient
                client = PacificaClient(base_url, address=address)
            prices = client.get_prices()
            check("시세 조회", True, f"{len(prices)}개 마켓 수신")
            if address:
                try:
                    acct = client.get_account()
                    check("계좌 조회", True,
                          f"잔고 {acct.get('balance', '?')} USDC, "
                          f"순자산 {acct.get('account_equity', '?')}")
                except Exception as e:
                    check("계좌 조회", False, str(e)[:120])
        except Exception as e:
            all_ok = False
            check("연결", False, str(e)[:150])

    if not reg_ok:
        print("\n⚠️ 클로드에 등록이 안 돼 있습니다. 설치 명령을 다시 실행한 뒤, "
              "클로드를 창만 닫지 말고 완전히 종료했다가 여세요 "
              "(윈도우는 시계 옆 트레이 아이콘 우클릭 후 종료).")
    elif all_ok:
        print("\n🎉 준비 완료. 클로드를 완전히 종료했다가 다시 열고 "
              "\"오늘의 픽 보여줘\" 라고 말해 보세요.")
    else:
        print("\n⚠️ 위의 ❌ 항목을 해결한 뒤 다시 실행하세요.")


if __name__ == "__main__":
    main()
