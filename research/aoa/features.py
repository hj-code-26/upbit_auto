"""5분봉 위 특징표. 모든 값은 해당 5분봉 '종가 시점'에 이미 알 수 있는 것만 쓴다.

진입 시각 t 에 붙일 때는 t 이전에 닫힌 마지막 봉(= floor(t, 5min) − 5min 봉)의 행을 쓴다 → 미래 누설 없음.
"""
import pathlib

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
BAR = pd.Timedelta("5min")
# 창 이름 → 5분봉 개수
WIN = {"5m": 1, "15m": 3, "1h": 12, "4h": 48, "12h": 144, "1d": 288, "3d": 864, "7d": 2016}


def candles():
    c = pd.read_pickle(ROOT / "data_cache" / "bitmex_xbt_5m.pkl")
    c = c[~c.index.duplicated()].sort_index()
    return c.asfreq(BAR).ffill()          # 빈 봉(거래 없음/점검)은 직전 값으로


def rsi(s, n):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def build(c=None):
    c = candles() if c is None else c
    f = pd.DataFrame(index=c.index)
    cl = c.close
    for k, n in WIN.items():
        f[f"ret_{k}"] = (cl / cl.shift(n) - 1) * 100
        hi, lo = c.high.rolling(n).max(), c.low.rolling(n).min()
        if n > 1:
            f[f"pos_{k}"] = (cl - lo) / (hi - lo).replace(0, np.nan)     # 박스 내 위치 0=저점, 1=고점
    for k, n in {"1h": 12, "4h": 48, "1d": 288, "7d": 2016}.items():
        f[f"ema_{k}"] = (cl / cl.ewm(span=n, adjust=False).mean() - 1) * 100   # 이평 괴리
    f["rsi_5m"] = rsi(cl, 14)
    h1, h4 = cl.resample("1h").last(), cl.resample("4h").last()
    v = c.volume
    f["vol_z_1h"] = np.log1p(v.rolling(12).sum()) - np.log1p(v.rolling(288).sum() / 24)   # 최근 1h 거래량 / 하루 평균 1h
    lr = np.log(cl).diff()
    f["rv_1h"] = lr.rolling(12).std() * np.sqrt(12) * 100
    f["rv_1d"] = lr.rolling(288).std() * np.sqrt(288) * 100
    f["rv_ratio"] = f.rv_1h / (f.rv_1d / np.sqrt(24))
    f["hour"] = f.index.hour
    f["dow"] = f.index.dayofweek
    # resample 기반 rsi 는 1h/4h 봉 '시작' 라벨에 종가를 붙여 미래가 섞인다 → 봉이 닫힌 뒤에만 보이게 밀어준다
    f["rsi_1h"] = rsi(h1, 14).shift(1).reindex(f.index, method="ffill")
    f["rsi_4h"] = rsi(h4, 14).shift(1).reindex(f.index, method="ffill")
    return f


def at(f, times):
    """times(진입 시각) 직전에 닫힌 봉의 특징. 봉 라벨 = 시작 시각이므로 floor(t) − 5분 봉이 마지막 완성봉."""
    idx = pd.DatetimeIndex(times).tz_localize("UTC").floor(BAR) - BAR
    return f.reindex(idx).set_index(pd.RangeIndex(len(idx)))


def fwd(c, times, horizons=("1h", "4h", "1d")):
    """진입 시각 이후 수익률(%) — 그의 '타이밍 우위' 측정용 (특징에는 절대 안 씀)."""
    idx = pd.DatetimeIndex(times).tz_localize("UTC").floor(BAR)
    base = c.open.reindex(idx).values
    out = {}
    for h in horizons:
        n = WIN[h]
        out[f"fwd_{h}"] = (c.close.shift(-n + 1).reindex(idx).values / base - 1) * 100
    return pd.DataFrame(out)


if __name__ == "__main__":
    c = candles()
    f = build(c)
    # 누설 점검: 특징 t 행이 t+1 봉 가격과 무관해야 한다 → ret_5m(t) 는 close(t)/close(t-1)
    assert np.isclose(f.ret_5m.iloc[1000], (c.close.iloc[1000] / c.close.iloc[999] - 1) * 100)
    t = pd.Timestamp("2019-05-01 10:07:30")
    assert at(f, [t]).ret_5m.iloc[0] == f.loc[pd.Timestamp("2019-05-01 10:00", tz="UTC"), "ret_5m"]
    f.to_pickle(ROOT / "data_cache" / "aoa_feat_5m.pkl")
    print(f.shape, f.index[0], f.index[-1], "ok")
