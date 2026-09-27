"""BitMEX 5분봉 (XBTUSD 및 지수 .BXBT 현물지수 · .XBTUSDPI 프리미엄지수) 2018-02~2022-01 (공개 API, 캐시). 버킷 timestamp 는 봉 '종료' 시각 → 시작 시각으로 당겨 저장."""
import json, pathlib, sys, time, urllib.request
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
URL = "https://www.bitmex.com/api/v1/trade/bucketed?binSize=5m&symbol={}&count=1000&startTime={}"
NAMES = {"XBTUSD": "bitmex_xbt_5m.pkl", ".BXBT": "bitmex_bxbt_5m.pkl", ".XBTUSDPI": "bitmex_pi_5m.pkl"}

def fetch(symbol="XBTUSD", start="2018-02-01", end="2022-01-02"):
    OUT = ROOT / "data_cache" / NAMES[symbol]
    rows, t = [], pd.Timestamp(start, tz="UTC")
    if OUT.exists():
        old = pd.read_pickle(OUT); rows = [old]; t = old.index[-1] + pd.Timedelta("10min")
    buf = []
    while t < pd.Timestamp(end, tz="UTC"):
        try:
            r = json.load(urllib.request.urlopen(URL.format(symbol, t.strftime("%Y-%m-%dT%H:%M:%SZ")), timeout=30))
        except Exception as e:
            print("retry", e); time.sleep(20); continue
        if not r: t = pd.Timestamp(end, tz="UTC")
        else:
            buf += r
            t = pd.Timestamp(r[-1]["timestamp"])
        if len(buf) >= 20000:
            print(t, flush=True)
        time.sleep(2.1)
        if buf and (len(buf) >= 20000 or t >= pd.Timestamp(end, tz="UTC")):
            df = pd.DataFrame(buf); buf = []
            df.index = pd.to_datetime(df.timestamp) - pd.Timedelta("5min"); df.index.name = "ts"
            rows.append(df[["open", "high", "low", "close", "volume"]].astype(float))
            pd.concat(rows).pipe(lambda x: x[~x.index.duplicated()]).to_pickle(OUT)
    return pd.read_pickle(OUT)

if __name__ == "__main__":
    for sym in sys.argv[1:] or ["XBTUSD"]:
        print(sym, fetch(sym).tail())
