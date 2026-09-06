"""pandas_ta 대체 (pandas 3 / py3.11 에 설치 불가). 열 이름은 원본(pandas_ta)과 같게 맞춘다."""
import pandas as pd


def add_indicators(df):
    c = df["close"]
    for n in (10, 20, 50):
        df[f"SMA_{n}"] = c.rolling(n).mean()
    df["EMA_10"] = c.ewm(span=10, adjust=False).mean()
    d = c.diff()
    up, dn = d.clip(lower=0), -d.clip(upper=0)
    rs = up.ewm(alpha=1 / 14, adjust=False).mean() / dn.ewm(alpha=1 / 14, adjust=False).mean()
    df["RSI_14"] = 100 - 100 / (1 + rs)
    lo, hi = df["low"].rolling(14).min(), df["high"].rolling(14).max()
    k = ((c - lo) / (hi - lo) * 100).rolling(3).mean()
    df["STOCHk_14_3_3"], df["STOCHd_14_3_3"] = k, k.rolling(3).mean()
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    sig = macd.ewm(span=9, adjust=False).mean()
    df["MACD_12_26_9"], df["MACDs_12_26_9"], df["MACDh_12_26_9"] = macd, sig, macd - sig
    m, s = c.rolling(20).mean(), c.rolling(20).std(ddof=0)
    df["BBL_20_2.0_2.0"], df["BBM_20_2.0_2.0"], df["BBU_20_2.0_2.0"] = m - 2 * s, m, m + 2 * s
    df["BBP_20_2.0_2.0"] = (c - (m - 2 * s)) / (4 * s)
    return df


if __name__ == "__main__":
    import numpy as np
    x = pd.DataFrame({"close": np.arange(1, 61, dtype=float)})
    x["high"], x["low"] = x.close + 1, x.close - 1
    r = add_indicators(x).iloc[-1]
    assert abs(r["SMA_20"] - 50.5) < 1e-9 and r["RSI_14"] > 99 and 0 < r["BBP_20_2.0_2.0"] <= 1.01
    print("ok")
