"""바이낸스 현물 5분봉 (BTCUSDT 기본, 인자로 ETHUSDT 등) 2018-02~2022-01 (공개 API) — BitMEX 괴리율·현물 주문흐름(테이커 매수 비중) 용."""
import json
import sys
import pathlib
import time
import urllib.request

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
SYM = (sys.argv[1:] or ["BTCUSDT"])[0]
START, END = (sys.argv[2:4] if len(sys.argv) >= 4 else ["2018-02-01", "2022-01-02"])   # 기간 지정 시 파일명에 시작연도
OUT = ROOT / "data_cache" / ("binance_spot_5m.pkl" if SYM == "BTCUSDT" and len(sys.argv) < 4 else
                             f"binance_{SYM.lower()}_5m.pkl" if len(sys.argv) < 4 else f"binance_{SYM.lower()}_5m_{START[:4]}.pkl")
URL = "https://data-api.binance.vision/api/v3/klines?symbol=" + SYM + "&interval=5m&limit=1000&startTime={}"

if __name__ == "__main__":
    t, end, rows = int(pd.Timestamp(START, tz="UTC").value // 10**6), int(pd.Timestamp(END, tz="UTC").value // 10**6), []
    while t < end:
        try:
            r = json.load(urllib.request.urlopen(URL.format(t), timeout=30))
        except Exception as e:
            print("retry", e); time.sleep(10); continue
        if not r:
            break
        rows += r
        t = r[-1][0] + 300_000
        time.sleep(0.2)
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "ct", "qv", "n", "tbv", "tbq", "x"])
    df.index = pd.to_datetime(df.ts, unit="ms", utc=True); df.index.name = "ts"
    df = df[["open", "high", "low", "close", "volume", "tbv"]].astype(float)
    df[~df.index.duplicated()].to_pickle(OUT)
    print(df.shape, df.index[0], df.index[-1])
