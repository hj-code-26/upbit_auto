"""RSI × 가격 위치로 본 aoa 의 진입 — 다중 시간대 RSI 와 다이버전스.

RSI(14): 5m · 15m · 1h · 4h (상위 시간대는 봉 확정 후에만 보이게 한 봉 늦춘다 — 누설 없음).
다이버전스 (5분봉 기준, 창 W 봉):
  강세 = 이번 봉 저가가 직전 W 봉 최저가 이하(신저점)인데 RSI 는 직전 W 봉 최저 RSI 보다 M 이상 높다
  약세 = 이번 봉 고가가 직전 W 봉 최고가 이상(신고점)인데 RSI 는 직전 W 봉 최고 RSI 보다 M 이상 낮다
  W ∈ {36(3h), 144(12h)}, M = 3.
1h 봉 다이버전스도 같은 정의(W=24 봉 = 하루)로 계산해 확정 후 5분 격자에 붙인다.
"""
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import features as F  # noqa: E402

M = 3.0


def div(low, high, r, W):
    pl, ph = low.shift(1).rolling(W).min(), high.shift(1).rolling(W).max()
    rl, rh = r.shift(1).rolling(W).min(), r.shift(1).rolling(W).max()
    newlow, newhigh = low <= pl, high >= ph
    return pd.DataFrame({"newlow": newlow, "newhigh": newhigh,
                         "bull": newlow & (r >= rl + M), "bear": newhigh & (r <= rh - M)})


def build(c):
    f = pd.DataFrame(index=c.index)
    f["rsi_5m"] = F.rsi(c.close, 14)
    for tf, name in (("15min", "rsi_15m"), ("1h", "rsi_1h"), ("4h", "rsi_4h"), ("1D", "rsi_1d")):
        f[name] = F.rsi(c.close.resample(tf).last(), 14).shift(1).reindex(f.index, method="ffill")
    for W in (36, 144):
        d = div(c.low, c.high, f.rsi_5m, W)
        for k in d:
            f[f"{k}_{W}"] = d[k]
    h = c.resample("1h").agg({"high": "max", "low": "min", "close": "last"})
    dh = div(h.low, h.high, F.rsi(h.close, 14), 24).shift(1).reindex(f.index, method="ffill").fillna(False)
    for k in dh:
        f[f"h_{k}"] = dh[k].astype(bool)
    for W, k in ((12, "1h"), (288, "1d"), (2016, "7d")):
        hi, lo = c.high.rolling(W).max(), c.low.rolling(W).min()
        f[f"pos_{k}"] = ((c.close - lo) / (hi - lo).replace(0, np.nan)).fillna(0.5)
    return f


if __name__ == "__main__":
    c = F.candles()
    f = build(c)
    f.to_pickle(F.ROOT / "data_cache" / "aoa_rsi_5m.pkl")
    # 누설 점검: rsi_1h 는 그 시간이 끝난 뒤에만 바뀐다 → 정시 이전 5분봉에서는 직전 시간 값
    t = pd.Timestamp("2019-06-01 10:55", tz="UTC")
    assert f.rsi_1h.loc[t] == f.rsi_1h.loc[pd.Timestamp("2019-06-01 10:00", tz="UTC")]
    print(f.shape, "ok")
