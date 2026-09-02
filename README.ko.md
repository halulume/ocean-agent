# ocean-agent

> **🌊 오픈 베타**: 2026년 8월 26일부터 누구나 쓸 수 있습니다.
> 초대도 대기줄도 없습니다. 설치하고 본인 계정을 연결하면 됩니다.
> 브래킷 엔진은 저희 자금으로도 계속 실전 검증 중입니다.
> 매일 채점은 [텔레그램](https://t.me/+V7wwRr6n4ZtmOGFl)에서 공개합니다.

> [English](README.md) · 한국어

🌊 **홈페이지:** [oceanagent.fi](https://oceanagent.fi) · **PyPI:** [ocean-agent](https://pypi.org/project/ocean-agent/)

**AI에게 매매를 시키세요.** [Pacifica](https://app.pacifica.fi)용 MCP 서버로,
자연어를 정확하고 위험이 계산된 선물 주문으로 바꿉니다. 여기에 사용자가 직접
정한 정책 파일이 통제하는 24시간 자율 매매 개체가 함께 들어 있습니다.

```bash
uvx ocean-agent        # 설치 불필요
```

전부 Pacifica 위에 만들어졌습니다. 공식 도구가 쓰는 것과 같은 Ed25519 에이전트
키 서명으로 Pacifica REST API를 직접 호출합니다, npm 의존성이 없습니다.

> ⚠️ 실제 자금으로 실제 주문을 냅니다. 연결 전에 [면책 고지](DISCLAIMER.ko.md)를
> 반드시 읽으세요.

---

## 왜 API를 직접 쓰지 않고 이걸 쓰나

공식 Pacifica MCP는 API를 그대로 노출합니다. AI가 정확한 가격과 수량을 직접
계산해야 하고, 틱 사이즈의 배수가 아닌 가격은 거래소가 거부합니다.
ocean-agent는 그 위층을 담당합니다.

| | 원본 API / 공식 MCP | ocean-agent |
|---|---|---|
| 주문 가격 | AI가 정확한 값을 계산 | *"손절 3%"* 라고 말하면 틱·랏·최소주문 자동 보정 |
| 포지션 크기 | 수동 | 위험 기반 (거래당 자본의 고정 비율만 위험에 노출) |
| 안전장치 | 없음 | 자금이 움직이는 모든 도구에 2단계 확인 게이트 |
| 통계 | 없음 | 교과서 이론이 아니라 **측정된** 승률과 기대값 |

---

## MCP 도구

**시장 · 분석**
- `analyze_chart`, 여러 시간봉 지표 스냅샷. 그 코인·그 시간봉에서 **실측된**
  신호별 적중률을 함께 보여줍니다. 엣지가 없으면 "없다"고 말합니다.
- `top_setups`, 통계로 검증된 셋업의 실시간 순위 (EV × 승률 × 표본 신뢰도).
  진입가·손절·목표·레버리지 포함
- `recommend_settings`, 잔고를 읽고 거래당 위험·동시 보유 수·레버리지·마진 모드를
  근거와 함께 추천
- `market_context`, 공포탐욕 국면 판독
- `scan_funding`, 전 마켓 펀딩 APR 순위
- `learned_winrates` / `learned_combos`, 실시간 관측으로 쌓은 승률 DB
  (신호 조합 포함)
- `review_predictions`, 과거 예측을 실제 결과로 채점

**매매**
- `open_with_bracket`, 진입과 거래소 네이티브 TP/SL을 한 번에. 손절이 거래소에
  얹히므로 PC가 꺼져 있어도 작동합니다.
- `protect_position`, 이미 열린 포지션에 네이티브 TP/SL 부착
- `open_funding_position` / `close_funding_position`, 델타뉴트럴 펀딩 캐리
  (현물 매수 + 선물 숏)를 배치로 원자적 실행
- `plan_oi_hedge`, OI 파밍 포지션과 교차 거래소 헤지 사이징 (수수료·펀딩 계산 포함)
- `open_pacifica_leg`, `check_position`, `account_status`

**Print** (실험적, Pacifica가 문서화하지 않은 엔드포인트를 씁니다. 예고 없이
바뀔 수 있습니다)
- `print_quote`, 실시간 프리미엄·내재변동성·청산가
- `print_order` / `print_status` / `print_close`
- `evaluate_print`, Print 조건이 값어치 있는지 통계적 판정: 체결 확률, 평균
  오버슛, 그것을 상쇄하는 손익분기 APY

자금이 움직이는 도구 7개에는 MCP 표준 `destructiveHint`가 붙어 있어, 클라이언트가
승인 UI를 띄웁니다.

---

## 자율 매매 개체

`policy.yaml`이 통제하는 자율 트레이더입니다. 이 파일이 위임장이며, 개체는 그
울타리 밖에서 행동할 수 없습니다.

이것은 **별도의 상시 프로세스**이지 MCP 도구가 아닙니다. MCP 서버는 AI 클라이언트가
호출할 때만 돕니다. 포지션을 들고 24시간 손절을 관리해야 하는 트레이더는 자기
프로세스가 필요합니다. 의도적으로 켜야 하고, 켜두면 AI가 붙어 있든 아니든 계속 돕니다.

```bash
python -m ocean_agent.autonomous --init      # 편집용 policy.yaml 생성
python -m ocean_agent.autonomous --dry       # 판단만, 주문은 안 냄
python -m ocean_agent.autonomous             # 계속 실행
python -m ocean_agent.autonomous --once      # 1사이클만
python -m ocean_agent.autonomous --report    # 성과 요약
python -m ocean_agent.autonomous --close-all # 전량 청산 + 방어선 기준 재설정
```

첫 실거래 전에 `policy.yaml`을 읽으세요, 자본·레버리지 상한·거래당 위험·최후
방어선이 전부 거기 있습니다. `--dry`로 시작하세요, 판단만 하고 주문은 내지
않습니다.

매 사이클 시장을 읽고, 배운 것을 채점하고, 보유 포지션을 관리하고, 모든 관문을
통과한 셋업에만 진입합니다.

**포트폴리오 버킷**, 자본을 방향성 매매와 펀딩 캐리로 나누고 매 사이클 재조정.

**포지션 사후관리**, 수익이 나면 손절을 진입가로 옮기고, 더 벌면 추적하고,
목표에서 부분 익절합니다. 손절선은 유리한 방향으로만 움직입니다.

**유동성 관문**, 내 주문이 그 시장 하루 거래대금에서 큰 비중을 차지하는 마켓은
건너뜁니다. 얇은 호가창이 진짜 위험입니다, 일부만 체결되고, 손절이 그 가격에
실행되지 않습니다.

**방향 쏠림 한도**, 한쪽으로 얼마나 몰릴 수 있는지 제한해, 한 번의 시장 반전이
모든 포지션을 동시에 때리지 못하게 합니다.

**자가 재측정**, 이것이 실제 학습 엔진입니다. 주기적으로 코인 × 시간봉 × 신호
전체 매트릭스를 다시 측정하고, 어느 시간봉을 매매할지와 어느 신호를 믿을지를
갱신합니다. 국면은 바뀝니다, 한 측정에서 8시간봉은 엣지가 전혀 없었는데 몇 주 뒤
가장 좋은 구간이 됐습니다. 고정 파라미터는 낡으므로, 고정하지 않습니다.

```bash
python -m ocean_agent.rematrix           # 지금 재측정
python -m ocean_agent.rematrix --show    # 현재 믿고 있는 것
python -m ocean_agent.walkforward        # 예측 정확도 측정 (보정표 갱신)
python -m ocean_agent.walkforward --live # 실전이 실측표와 맞는지 대조
```

**적응**, 실전 채점에서 지는 신호는 정지되고, 손실 구간에선 사이즈가 줄고
회복하면 되돌아옵니다. 파라미터는 정책 범위 안에서 적응하며, 개체가 자기 코드를
고쳐 쓰지는 않습니다.

**최후 방어선**, 치명적 손실에서 한 번의 완전 정지. 그 외에는 멈추지 않고
적응합니다.

---

## 설치

**명령어 하나면 됩니다**, uv·파이썬·의존성 설치와 Claude Desktop 등록까지 전부
자동입니다. 지갑 주소와 에이전트 키, 두 가지만 물어봅니다.

```bash
# 윈도우 (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://oceanagent.fi/install.ps1 | iex"

# macOS / Linux
sh -c "$(curl -LsSf https://oceanagent.fi/install.sh)"
```

직접 설정하려면:

1. uv 설치:

```bash
# 윈도우 (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

2. 에이전트 키를 [app.pacifica.fi/apikey](https://app.pacifica.fi/apikey)에서
발급하세요. 오션에이전트는 이 키로 주문만 서명하며 **출금 기능 자체가 없습니다.**
키 자체의 권한 범위는 파시피카가 정합니다. 키는 언제든 폐기할 수 있습니다. 읽기 전용 도구는 키 없이도 동작합니다.

3. 키는 설정 파일이 아니라 `.env`에 둡니다.

```ini
ADDRESS=지갑_주소
PACIFICA_API_KEY=에이전트_키
PACIFICA_BASE_URL=https://api.pacifica.fi
```

이대로 쓰면 **메인넷 실거래(실제 자금)** 입니다. 테스트넷으로 연습하려면
`PACIFICA_BASE_URL` 줄을 지우세요 (테스트넷 키는 별도: `ADDRESS_TESTNET`,
`PACIFICA_API_KEY_TESTNET`).

4. AI 클라이언트의 MCP 설정에 추가:

```jsonc
{
  "mcpServers": {
    "ocean-agent": {
      "command": "uvx",
      "args": ["ocean-agent@0.4.67"],
      "env": { "PACIFICA_ENV_FILE": "/절대경로/.env" }
    }
  }
}
```

클라이언트를 재시작하면 첫 실행 때 필요한 것이 자동으로 받아집니다.
자세한 단계별 설치(클라이언트 6종)는 [setup-mcp.md](setup-mcp.md)를 보세요.

5. 확인:

```bash
uv run --with ocean-agent python -m ocean_agent.doctor
```

---

## 안전장치

- 자금이 움직이는 모든 도구는 `confirm=true` 없이는 주문을 내지 않습니다
- 이 소프트웨어에는 출금 기능이 없습니다
- 테스트넷과 메인넷의 데이터 파일이 분리되어 섞이지 않습니다
- 모든 진입에 거래소 네이티브 손절이 붙습니다

---

## 위험

레버리지 선물은 맡긴 증거금보다 더 잃을 수 있습니다. 측정된 엣지는 작고
(이 시장 실측 승률 천장은 동전던지기보다 조금 나은 정도) 국면이
바뀌면 사라질 수 있습니다.
무엇을 하는지 정확히 이해할 때까지 `--dry`로 돌리고, 잃어도 되는 돈만
거세요.

전체 내용은 [면책 고지](DISCLAIMER.ko.md)를 읽으세요.

---

## 라이선스

MIT, [LICENSE](LICENSE)
