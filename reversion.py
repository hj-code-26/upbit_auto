"""당일 고점·저점 판별 모델 (평균회귀). 고점 후보에서 숏, 저점 후보에서 롱.

기존 봇(model.py)은 추세 추종이다 — 고가를 넘으면 사고 저가를 깨면 판다. 이 파일은 정반대 가설을 다룬다:
research_both_sides.txt 에서 '하락은 짧고 급하고 되돌림이 빠르다' 가 반복해서 나왔고, 그 되돌림을
추세 규칙으로 먹으려던 시도(숏, 반등 롱)는 전부 실패했다. 되돌림이 진짜라면 **극단에서 반대로 걸어야** 한다.

고점/저점은 지나고 나서야 확정되므로, 여기서는 '지금 봉이 극단처럼 보이는가' 를 보조지표로 채점한다.
미래 정보는 쓰지 않는다 — 판정은 **마지막 완성 봉까지의 값**만 본다.

고점 규칙 7개 (저점은 전부 거울):
  ① RSI(14) ≥ 70              과매수
  ② 볼린저 %B ≥ 1.0            상단 밴드 이탈
  ③ 스토캐스틱 %K ≥ 80         과매수
  ④ 도치안 위치 ≥ 0.95         직전 20봉 고저 범위의 상단
  ⑤ 윗꼬리 ≥ 몸통              위에서 거부당함
  ⑥ 거래량 ≥ 1.5 × 20봉 평균   극단에 물량이 실림
  ⑦ 3봉 상승폭 ≥ 2 × ATR(14)   과도한 급등
  N개 이상 켜지면 고점 후보 (REV_CONF).

**이 7개를 더해 '점수' 로 쓰는 것에는 검증된 근거가 없다** (research_bias.txt, 2026-09-10).
돌파봉만 따로 놓고 규칙별로 재보니 ② 볼린저 이탈이 사실상 혼자 일하고(무작위 대비 백분위 99),
④ 도치안은 오히려 해로웠다(백분위 1). 나머지는 희석한다. 합계 점수는 백분위 95~98 인데
후보 ~18개 중 사후에 고른 것이라 실효 p ≈ 0.30 이다.
그렇다고 '볼린저 단독' 으로 갈아타서도 안 된다 — 7개 중 사후 최고를 고르는 더 심한 선택 편향이다.
지금 이 파일의 쓰임새는 **관찰과 연구**다. 매매 필터(autotrade.EXTREME_MIN)는 0(꺼짐)이 기본이다.

나스닥(QQQ) 은 선택 필터다 — 켜면 '주식이 같은 방향일 때만' 진입한다 (research_reversion.txt 참고).
사용: python reversion.py     자체 점검
"""
import os
import pathlib

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent
CACHE = ROOT / "data_cache"
REV_CONF = int(os.environ.get("REV_CONF", 4))     # 7개 중 몇 개 이상이면 극단 후보인가
DON_N, VOL_N, ATR_N = 20, 20, 14


def add_features(d):
    """4h(또는 1d) 봉 → 판정에 쓰는 지표. 전부 그 봉까지의 정보만 쓴다 (shift 없이 당봉 종가 기준)."""
    from indicators import add_indicators
    x = add_indicators(d.copy())
    c, h, l, o, v = x.close, x.high, x.low, x.open, x.volume

    body = (c - o).abs().clip(lower=1e-9)
    x["wick_up"] = (h - c.combine(o, max)) / body            # 윗꼬리 / 몸통
    x["wick_dn"] = (c.combine(o, min) - l) / body            # 아랫꼬리 / 몸통

    hh, ll = h.rolling(DON_N).max(), l.rolling(DON_N).min()
    x["don"] = (c - ll) / (hh - ll).replace(0, np.nan)       # 0=구간 최저, 1=구간 최고

    x["vol_ratio"] = v / v.rolling(VOL_N).mean().replace(0, np.nan)

    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    x["ATR"] = tr.ewm(alpha=1 / ATR_N, adjust=False).mean()
    x["push"] = (c - c.shift(3)) / x["ATR"].replace(0, np.nan)   # 3봉 이동 / ATR
    return x


# (이름, 고점 조건, 저점 조건) — 저점은 고점의 거울이다
RULES = [
    ("RSI 과열", lambda x: x.RSI_14 >= 70, lambda x: x.RSI_14 <= 30),
    ("볼린저 밴드 이탈", lambda x: x["BBP_20_2.0_2.0"] >= 1.0, lambda x: x["BBP_20_2.0_2.0"] <= 0.0),
    ("스토캐스틱", lambda x: x["STOCHk_14_3_3"] >= 80, lambda x: x["STOCHk_14_3_3"] <= 20),
    ("도치안 상·하단", lambda x: x.don >= 0.95, lambda x: x.don <= 0.05),
    ("꼬리 거부", lambda x: x.wick_up >= 1.0, lambda x: x.wick_dn >= 1.0),
    ("거래량 급증", lambda x: x.vol_ratio >= 1.5, lambda x: x.vol_ratio >= 1.5),
    ("ATR 대비 급등락", lambda x: x.push >= 2.0, lambda x: x.push <= -2.0),
]


def scores(d):
    """→ (고점 점수 0~7, 저점 점수 0~7) 시리즈. 각 봉의 종가 시점 판정."""
    x = add_features(d)
    hi = sum(f(x).fillna(False).astype(int) for _, f, _ in RULES)
    lo = sum(g(x).fillna(False).astype(int) for _, _, g in RULES)
    return hi, lo


def signals(d, conf=None):
    """→ (고점 후보 bool, 저점 후보 bool). 둘 다 참인 봉은 판정 불가로 보고 둘 다 끈다."""
    conf = REV_CONF if conf is None else conf
    hi, lo = scores(d)
    top, bot = hi >= conf, lo >= conf
    both = top & bot
    return top & ~both, bot & ~both


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    n = 300
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    rng = np.random.default_rng(0)
    # 톱니파 + 잡음 → 극단이 주기적으로 생기는 합성 시계열
    base = 100 + 20 * np.sin(np.arange(n) / 12) + rng.normal(0, 0.5, n)
    d = pd.DataFrame({"close": base}, index=idx)
    d["open"] = d.close.shift(1).fillna(base[0])
    d["high"] = d[["open", "close"]].max(axis=1) + 0.6
    d["low"] = d[["open", "close"]].min(axis=1) - 0.6
    d["volume"] = 1.0 + rng.random(n)

    hi, lo = scores(d)
    assert hi.max() >= 3 and lo.max() >= 3, "합성 톱니파에서 극단 점수가 전혀 안 오른다"
    top, bot = signals(d, conf=3)
    assert top.any() and bot.any(), "고점·저점 후보가 하나도 안 잡힌다"
    assert not (top & bot).any(), "같은 봉이 고점이자 저점일 수는 없다"
    # 고점 후보는 저점 후보보다 평균적으로 높은 자리에 있어야 한다
    assert d.close[top].mean() > d.close[bot].mean(), "고점 후보가 저점 후보보다 낮은 자리에 잡혔다"
    # 미래 참조 없음: 앞쪽 절반만 줘도 그 구간 판정이 같아야 한다
    half = len(d) // 2
    h2, _ = scores(d.iloc[:half])
    assert (h2.iloc[-20:].values == hi.iloc[half - 20:half].values).all(), "뒤쪽 데이터가 앞쪽 판정을 바꿨다 (미래 참조)"
    print(f"ok  고점 후보 {top.sum()}봉 · 저점 후보 {bot.sum()}봉 (합성 {n}봉, conf=3) · 미래 참조 없음")
