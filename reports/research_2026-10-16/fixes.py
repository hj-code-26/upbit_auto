"""오답 분석 후보 수정 점검 (ETH·BTC·SOL·XRP 3몫 4%, 지금 버전 C3 + 동적 크기).
A) 과열 진입 금지: 진입 방향으로 |종가 − SMA50| > X × ATR 이면 건너뜀.  B) 추적 손절 trail_atr.  → fixes.csv"""
import sys, pathlib, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path[:0] = [str(HERE.parents[1]), str(HERE)]
import numpy as np, pandas as pd, rls, backtest as bt
from multiprocessing import Pool
OFFS = (0, 8, 16)
def ext_ok(inst, X, cache={}):
    if inst not in cache:
        h = bt.cached_hourly(inst); parts = []
        for o in OFFS:
            d = rls.offset_daily(h, o, 1); x = rls.prep(d)
            parts.append((x.close - d.close.rolling(50).mean()) / x.atr)
        cache[inst] = pd.concat(parts)
    e = cache[inst]
    return lambda i, day: not (abs(e.get(day, 0)) > X)        # 방향: 롱은 위로, 숏은 아래로 벌어졌을 때만 → 부호로 판단
def run(a):
    inst, kind, v = a
    h = bt.cached_hourly(inst); s0 = "2021-09-01" if inst[:3] == "SOL" else "2020-06-01"
    kw = {}
    if kind == "ext": kw["entry_ok"] = ext_ok(inst, v)
    if kind == "trail": kw["trail_atr"] = v
    out = {"inst": inst[:3], "fix": kind, "v": v}
    with bt.lever(30, 0.04):
        for tag, a0, b0 in (("is", s0, "2023-06-30"), ("oos", "2023-07-01", "2026-10-16"), ("ytd", "2025-06-01", "2026-10-16")):
            eq, tr = rls.simulate_tranches({inst: h}, OFFS, a0, b0, **kw)
            if tag == "ytd":
                eq = eq[eq.index >= "2025-12-31"]
            s = bt.stats(eq); out |= {f"{tag}_cagr": round(s["cagr%"], 1), f"{tag}_mdd": round(s["mdd%"], 1), f"{tag}_sh": round(s["sharpe"], 2)}
            if tag == "ytd":
                m = eq.resample("ME").last(); out["jan%"] = round((m.iloc[1] / eq.iloc[0] - 1) * 100, 1) if len(m) > 1 else np.nan
                out["ytd%"] = round((eq.iloc[-1] / eq.iloc[0] - 1) * 100, 1)
    return out
if __name__ == "__main__":
    jobs = [(i, k, v) for i in ("ETH-USDT-SWAP", "BTC-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP")
            for k, v in [("base", 0)] + [("ext", x) for x in (1.5, 2.0, 2.5, 3.0)] + [("trail", t) for t in (2.0, 3.0, 4.0, 5.0)]]
    with Pool(9) as p: df = pd.DataFrame(p.map(run, jobs))
    df.to_csv(HERE / "fixes.csv", index=False)
    pd.set_option("display.width", 250); print(df.to_string(index=False))
