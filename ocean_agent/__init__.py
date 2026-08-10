import os


def _is_testnet(base_url: str) -> bool:
    return "test-api" in base_url


def data_tag() -> str:
    """라이브 계좌 데이터의 네임스페이스: 'testnet' vs 'mainnet'.

    두 네트워크의 실매매 기록(상태·관측·승률·calibration·postmortem·예측·로그)이
    절대 섞이지 않도록 파일을 분리한다. 판단 기준은 PACIFICA_BASE_URL 환경변수
    (MCP는 .mcp.json이, 자율 개체는 시작 시 policy의 base_url로 설정)이며,
    알 수 없으면 안전하게 'testnet'으로 본다.
    MUSTACHE_DATA_TAG 로 강제 지정하면 그것이 최우선(멀티 인스턴스 격리용)."""
    override = os.environ.get("MUSTACHE_DATA_TAG")
    if override:
        return override
    base = os.environ.get("PACIFICA_BASE_URL", "")
    return "mainnet" if (base and "test-api" not in base) else "testnet"


def data_file(name: str) -> str:
    """네트워크별 라이브 데이터 파일의 홈 경로를 돌려준다.

    예) data_file("autonomous.json") -> ~/.ocean_agent_testnet_autonomous.json
    시장 백테스트 매트릭스는 네트워크와 무관(가격 히스토리)하므로 여기 쓰지 않고
    ~/.ocean_agent_matrix.json 하나로 공유한다."""
    return os.path.join(os.path.expanduser("~"),
                        f".ocean_agent_{data_tag()}_{name}")


# Mainnet-only brand name. Testnet keeps the original generic label so the
# testnet experience is byte-for-byte unchanged. This is DISPLAY ONLY (Telegram
# alerts, logs, MCP handshake) — it never touches data-file names, state keys,
# the PyPI package id, or the .mcp.json tool namespace, so it can't collide with
# the storage/logic layer.
_BRAND_MAINNET = "Ocean Agent"
_BRAND_TESTNET = "자율 개체"


def agent_name(default: str = _BRAND_TESTNET) -> str:
    """User-facing display name. 'Ocean Agent' on mainnet, otherwise `default`.
    Driven by the same PACIFICA_BASE_URL as data_tag(), so name and data
    namespace always agree (mainnet name ↔ mainnet files)."""
    return _BRAND_MAINNET if data_tag() == "mainnet" else default


# 네트워크 분리 이전(구버전)의 라이브 파일 이름들. 매트릭스는 공유라 제외.
_LIVE_FILES = ["autonomous.json", "equity.jsonl", "bot.log", "calibration.json",
               "observations.jsonl", "signal_stats.json", "observe_rotation.txt",
               "postmortem.jsonl", "bot_state.json", "predictions.json"]


def migrate_brand_rename() -> list:
    """옛 이름(~/.mustache_*)의 데이터를 새 이름(~/.ocean_agent_*)으로 1회 옮긴다.

    0.3.0 에서 배포 이름이 mustache-mcp → ocean-agent 로 바뀌면서 홈 데이터
    파일 접두어도 함께 바뀌었다. 이사가 없으면 기존 사용자는 쌓아온 매트릭스·
    관측·손익 기록을 통째로 잃고 빈 상태로 다시 시작하게 된다.

    안전 규칙 두 가지:
      · 새 이름이 이미 있으면 건드리지 않는다 (멱등 — 여러 번 불러도 안전)
      · 복사가 아니라 이름 변경이라 디스크를 두 배로 쓰지 않는다
        (bincache 는 378MB 라 복사하면 느리고, 어차피 재다운로드 가능하다)
    """
    home = os.path.expanduser("~")
    moved = []
    try:
        entries = os.listdir(home)
    except OSError:
        return moved
    for entry in entries:
        if not entry.startswith(".mustache_"):
            continue
        old = os.path.join(home, entry)
        new = os.path.join(home, ".ocean_agent_" + entry[len(".mustache_"):])
        if os.path.exists(new):
            continue                      # 이미 옮겨졌거나 새로 만들어진 것
        try:
            os.rename(old, new)           # 파일·디렉터리 모두 처리
            moved.append(entry)
        except OSError:
            pass                          # 사용 중이면 다음 실행에 다시 시도
    return moved


def migrate_legacy_data() -> list:
    """구버전 untagged 라이브 파일(~/.ocean_agent_X)을 testnet 태그본으로 1회 이전한다.

    지금까지 봇은 테스트넷에서만 돌았으므로 기존 데이터는 전부 테스트넷 것이다 →
    항상 'testnet' 태그로만 옮긴다(현재 네트워크가 메인넷이어도 옛 테스트넷 데이터를
    메인넷에 섞지 않기 위해). 태그본이 이미 있으면 건드리지 않아 멱등하며,
    .bak/.polluted 등 접미사 백업은 정확한 이름이 아니라 옮기지 않는다."""
    home = os.path.expanduser("~")
    moved = []
    for name in _LIVE_FILES:
        legacy = os.path.join(home, f".ocean_agent_{name}")
        tagged = os.path.join(home, f".ocean_agent_testnet_{name}")
        if os.path.exists(legacy) and not os.path.exists(tagged):
            try:
                os.replace(legacy, tagged)
                moved.append(name)
            except OSError:
                pass
    return moved


def api_key_from_env(base_url: str = "") -> str:
    """네트워크에 맞는 API 키를 고른다.

    에이전트 키는 메인넷·테스트넷에서 각각 따로 승인되므로 키가 다를 수 있다.
    테스트넷 대상이고 PACIFICA_API_KEY_TESTNET 이 있으면 그것을, 아니면
    PACIFICA_API_KEY (구 AGENT_PRIVATE_KEY 호환)를 사용한다.
    """
    if _is_testnet(base_url):
        tn = os.environ.get("PACIFICA_API_KEY_TESTNET")
        if tn:
            return tn
    return os.environ.get("PACIFICA_API_KEY") or os.environ.get("AGENT_PRIVATE_KEY", "")


def address_from_env(base_url: str = "") -> str:
    """네트워크에 맞는 계정 주소를 고른다.

    테스트넷은 별도 지갑을 쓸 수 있으므로 ADDRESS_TESTNET 을 우선 사용한다.
    없으면 ADDRESS 로 폴백 (양쪽 같은 지갑을 쓰는 경우).
    """
    if _is_testnet(base_url):
        tn = os.environ.get("ADDRESS_TESTNET")
        if tn:
            return tn
    return os.environ.get("ADDRESS", "")
