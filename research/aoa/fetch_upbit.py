"""업비트 KRW-BTC 5분봉 2018-02~2022-01 (공개 API, 요청당 200개·뒤로 걷기) + 원/달러 일별 환율(FRED DEXKOUS).

김프 = 업비트 원화가 / (바이낸스 USDT가 × 환율) − 1. 환율은 일별이라 김프 '수준'은 거칠다 → 분석은 하루 중앙값 대비 편차 위주.
"""
import io
import json
import pathlib
import time
import urllib.request

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "data_cache" / "upbit_btc_5m.pkl"
FX = ROOT / "data_cache" / "fx_usdkrw.pkl"
URL = "https://api.upbit.com/v1/candles/minutes/5?market=KRW-BTC&count=200&to={}"


def fetch(start="2018-02-01", end="2022-01-02"):
    rows, t, lo = [], pd.Timestamp(end, tz="UTC"), pd.Timestamp(start, tz="UTC")
    while t > lo:
        try:
            r = json.load(urllib.request.urlopen(URL.format(t.strftime("%Y-%m-%dT%H:%M:%SZ")), timeout=30))
        except Exception as e:
            print("retry", e, flush=True); time.sleep(5); continue
        if not r:
            break
        rows += r
        t = pd.Timestamp(r[-1]["candle_date_time_utc"], tz="UTC")
        if len(rows) % 20000 < 200:
            print(t, flush=True)
        time.sleep(0.12)
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df.candle_date_time_utc, utc=True); df.index.name = "ts"
    df = df.rename(columns={"opening_price": "open", "high_price": "high", "low_price": "low", "trade_price": "close",
                            "candle_acc_trade_volume": "volume"})[["open", "high", "low", "close", "volume"]].astype(float)
    df = df[~df.index.duplicated()].sort_index()
    df.to_pickle(OUT)
    return df


def fx():
    csv = urllib.request.urlopen("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DEXKOUS", timeout=30).read().decode()
    s = pd.read_csv(io.StringIO(csv), index_col=0, parse_dates=True).iloc[:, 0]
    s = pd.to_numeric(s, errors="coerce").dropna()
    s.index = s.index.tz_localize("UTC"); s.to_pickle(FX)
    return s


if __name__ == "__main__":
    print(fx().loc["2018":"2021"].describe())
    print(fetch().shape)
