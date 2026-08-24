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

    # 3. Node.js (MCP 모드용)
    npx = shutil.which("npx")
    node_ok = npx is not None
    check("Node.js/npx (MCP 모드용)", node_ok,
          npx or "없음, MCP 모드를 쓰려면 nodejs.org에서 설치 (rest 모드는 불필요)")

    # 4. 설정 파일
    cfg_ok = os.path.exists("config.yaml")
    all_ok &= check("config.yaml", cfg_ok, "" if cfg_ok else "봇 폴더에서 실행했는지 확인")

    import yaml
    from dotenv import load_dotenv
    load_dotenv()
    cfg = {}
    if cfg_ok:
        with open("config.yaml", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)

    from . import address_from_env, api_key_from_env
    _net_url = cfg.get("base_url", "https://api.pacifica.fi") if cfg_ok else ""
    address = address_from_env(_net_url)
    key = api_key_from_env(_net_url)
    net_label = "테스트넷" if "test-api" in _net_url else "메인넷"
    check(f".env, 지갑 주소 ({net_label}용)", bool(address),
          f"{address[:6]}...{address[-4:]}" if address
          else "없음, 계좌 조회/실주문에 필요 (시세 조회만은 가능)")
    check(f".env, API 키 ({net_label}용)", bool(key),
          "설정됨" if key else "없음, dry-run은 가능, 실주문은 불가 "
          "(app.pacifica.fi/apikey 에서 발급)")

    # 5. 실제 연결 테스트
    if cfg_ok:
        base_url = cfg.get("base_url", "https://api.pacifica.fi")
        api_mode = cfg.get("api_mode", "rest")
        print(f"\n연결 테스트 ({api_mode.upper()} / {base_url}) ...")
        try:
            if api_mode == "mcp" and node_ok:
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

    print("\n" + ("🎉 준비 완료! 다음: python -m ocean_agent.main --once (dry-run 1사이클)"
                  if all_ok else "⚠️ 위의 ❌ 항목을 해결한 뒤 다시 실행하세요."))


if __name__ == "__main__":
    main()
