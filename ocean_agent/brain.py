"""중앙 신경계 (Brain), 모든 학습원을 하나로 잇는 허브.

지금까지 학습 조각들이 따로 존재했다:
  · 매트릭스(rematrix), 과거 13개월 기대값
  · 관측DB(observer), 실시간 신호 승률
  · 원인분석(postmortem), 청산별 '왜 이겼나/졌나' 귀인
  · 적응(graded), 신호@시간봉 실전 손익
  · 메타학습(이 모듈), '내 예측이 얼마나 정확한가'

Brain은 이것들을 한 셋업에 대해 종합해 '최종 확신도(conviction)'를 낸다.
그 하나의 숫자가 진입 여부·사이즈·Print 회피까지 관통한다.
= 조각들이 각자 놀지 않고 하나의 유기체로 성장한다.

메타학습 원칙 (사용자 지시 2026-07-22):
  봇이 "승률 70%"라 예측한 셋업들이 실제로 몇 % 이겼나를 추적한다.
  실제가 예측보다 낮으면 → 내 추정이 뻥튀기됐다 → 다음 추정을 하향 보정.
  단, 표본이 충분히 쌓이기 전엔 '기록만' 하고 보정하지 않는다(잡음 방지).
"""

import json
import os
import time

from . import data_file

_HOME = os.path.expanduser("~")

def _calib_file() -> str:  # 네트워크별 분리 (테스트넷/메인넷)
    return data_file("calibration.json")

MIN_CALIB_SAMPLES = 40      # 이만큼 쌓이기 전엔 보정 안 함(기록만)


# ---------- 메타학습: 예측 정확도 캘리브레이션 ----------

def _load_calib() -> dict:
    try:
        with open(_calib_file(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        # bucket: 예측승률 구간(50s/60s/70s/80s+) -> {pred_sum, won, n}
        return {"buckets": {}, "updated": 0}


def _save_calib(c: dict) -> None:
    path = _calib_file()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False)
    os.replace(tmp, path)


def _bucket(win_rate: float) -> str:
    """예측 승률을 2%p 격자로 쪼갠다.

    예전 구간(80+/70s/60s/50s)은 백테 승률이 부풀려져 있던 시절 것이다.
    워크포워드 실측으로 이 시장의 실제 승률 범위가 50~58%임이 밝혀졌고
    (천장 58.0%), 그러면 모든 예측이 "50s" 한 칸에 몰려 52%짜리와 58%짜리를
    구분하지 못한다, 메타학습이 사실상 한 덩어리가 된다.
    운용 범위 안에서 갈리도록 잘게 나눈다."""
    lo = int(max(0.0, min(0.98, win_rate)) * 50) * 2
    return f"{lo}-{lo + 2}"


def record_prediction(predicted_wr: float, won: bool) -> None:
    """청산 시 호출, '이 예측 승률이었고 실제로 이겼나'를 캘리브레이션에 축적.
    이게 메타학습의 입력이다: 내 예측 vs 현실."""
    c = _load_calib()
    b = c["buckets"].setdefault(_bucket(predicted_wr),
                                {"pred_sum": 0.0, "won": 0, "n": 0})
    b["pred_sum"] += float(predicted_wr)
    b["won"] += int(won)
    b["n"] += 1
    c["updated"] = int(time.time() * 1000)
    _save_calib(c)


def calibration_factor(predicted_wr: float) -> float:
    """메타학습 보정계수, 이 예측 승률을 얼마나 신뢰할지(0.5~1.0).

    같은 구간에서 '실제 승률 / 예측 승률'을 반환. 표본 부족이면 1.0(무보정).
    예) 70%로 예측했는데 실제 55%만 이겼으면 → 0.79배로 하향.
    이 값을 win_rate에 곱하면 봇이 자기 낙관을 스스로 교정한다.
    """
    c = _load_calib()
    b = c["buckets"].get(_bucket(predicted_wr))
    if not b or b["n"] < MIN_CALIB_SAMPLES:
        return 1.0   # 표본 부족, 잡음 방지, 무보정
    actual = b["won"] / b["n"]
    predicted = b["pred_sum"] / b["n"]
    if predicted <= 0:
        return 1.0
    return max(0.5, min(1.0, actual / predicted))   # 과신만 낮춤, 부풀리진 않음


def calibration_report() -> dict:
    """구간별 예측 vs 실제, 봇이 얼마나 정확한지 성적표."""
    c = _load_calib()
    out = {}
    for k, b in c.get("buckets", {}).items():
        if b["n"] > 0:
            out[k] = {
                "예측평균": round(b["pred_sum"] / b["n"], 3),
                "실제": round(b["won"] / b["n"], 3),
                "표본": b["n"],
                "보정중": b["n"] >= MIN_CALIB_SAMPLES,
            }
    return out


# ---------- 종합 확신도: 모든 학습원을 하나로 ----------

def conviction(symbol: str, signal: str, interval: str, side: str,
               base_win_rate: float, n_samples: int,
               graded: dict | None = None) -> dict:
    """한 셋업에 대해 모든 학습원을 종합한 최종 확신도.

    입력:
      base_win_rate, scanner가 이미 매트릭스+관측을 섞어 낸 승률
      graded, 이 봇의 실전 채점 {signal@tf: {win,total,pnl}}
    출력:
      {conviction: 0~1, meta_factor, real_pnl, notes}
    이 conviction 하나가 진입·사이즈·Print판단을 관통한다.
    """
    notes = []
    wr = base_win_rate

    # 1) 메타학습 보정, 이 승률대의 과거 예측이 실제로 맞았나
    meta = calibration_factor(base_win_rate)
    if meta < 1.0:
        wr *= meta
        notes.append(f"메타보정 x{meta:.2f}")

    # 2) 이 봇의 실전 손익, 매트릭스가 맞다 해도 실전에서 잃었으면 감점
    real_pnl = 0.0
    key = f"{signal.split(' +조합')[0]}@{interval}"
    if graded and key in graded:
        g = graded[key]
        real_pnl = g.get("pnl", 0.0)
        if g.get("total", 0) >= 5:
            real_wr = g["win"] / g["total"]
            # 실전 승률을 30% 가중으로 섞음 (표본 적으니 과하지 않게)
            wr = wr * 0.7 + real_wr * 0.3
            notes.append(f"실전 {real_wr:.0%}({g['total']}건)")
            if real_pnl < 0:
                wr *= 0.9   # 순손실 조합은 추가 감점
                notes.append(f"순손실 {real_pnl:+.0f}")

    # 3) 원인분석(postmortem) 귀인, '역행패배'(유리한 국면인데 진 것)가
    #    쌓인 신호는 신호 자체가 나쁘다는 증거. '시장순풍'으로만 이긴 신호는
    #    국면이 바뀌면 무너진다 → 둘 다 감점.
    try:
        from .postmortem import signal_verdict
        # 표본 하한 4 → 25. 진짜 승률이 52~58%인 시장에서 4건짜리 성적표로
        # '실전패배多'를 판정하면, 진짜 54%짜리 신호가 25.5% 확률로 부당하게
        # 감점된다(25건이면 5.4%). 자동정지와 같은 종류의 실수이고, 여기선
        # 감점이라 덜 치명적일 뿐 방향은 똑같이 좋은 신호를 죽이는 쪽이다.
        v = signal_verdict(signal.split(" +조합")[0], interval)
        if v and v["n"] >= 25:
            luck_ratio = (v["market_luck_wins"] /
                          max(v["market_luck_wins"] + v["real_edge_wins"], 1))
            if v["win_rate"] < 0.4:
                wr *= 0.85
                notes.append(f"귀인:실전패배多({v['n']}건)")
            elif luck_ratio > 0.8 and v["real_edge_wins"] == 0:
                wr *= 0.92
                notes.append("귀인:운으로만 이김")
    except Exception:
        pass

    # 4) 표본 신뢰도, 표본 적으면 확신을 중앙(0.5)으로 끌어당김
    confidence = min(n_samples / 60, 1.0)
    conv = 0.5 + (wr - 0.5) * confidence

    return {
        "conviction": round(max(0.0, min(1.0, conv)), 3),
        "adjusted_wr": round(wr, 3),
        "meta_factor": round(meta, 3),
        "real_pnl": round(real_pnl, 2),
        "notes": " · ".join(notes) if notes else "기본",
    }
