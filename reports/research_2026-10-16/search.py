"""규칙 수치 무작위 탐색. 선택은 IS(2020-06~2023-06, BTC+ETH)만. OOS(2023-07~)·SOL+XRP 는 보고용."""
import sys, itertools, random, warnings, time; warnings.filterwarnings("ignore")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))
import pandas as pd, numpy as np, rls, backtest as bt
from multiprocessing import Pool
BE = {i: bt.cached_hourly(i) for i in rls.COINS}
SX = {i: bt.cached_hourly(i) for i in ("SOL-USDT-SWAP", "XRP-USDT-SWAP")}
PER = [("is", BE, "2020-06-01", "2023-06-30"), ("oos", BE, "2023-07-01", "2026-10-15"), ("sx", SX, "2021-08-01", "2026-10-12")]
SPACE = {"sma": [20, 30, 40, 50, 70, 100], "slope": [5, 10, 20, 30], "conf": [1, 2, 3, 5], "be": [0.5, 1.0, 1.5, 2.0, 99.0],
         "drop": [0.75, 1.0, 1.5, 2.0, 99.0], "amax": [0.05, 0.06, 0.08, 0.10, None], "trail": [None, 2.0, 3.0, 4.0]}
ORIG_CONFIRM = rls.confirm
def set_rules(c):
    rls.SMA, rls.SLOPE, rls.BE_ATR, rls.DROP_ATR = c["sma"], c["slope"], c["be"], c["drop"]
    n = c["conf"]
    rls.confirm = lambda reg: reg.where(pd.concat([reg == reg.shift(j) for j in range(n)], axis=1).all(axis=1), 0).astype(int)
def run(c, L=30, m=0.02, offs=rls.TRANCHES, pers=PER):
    set_rules(c)
    out = dict(c)
    with bt.lever(L, m):
        for tag, H, a, b in pers:
            eq, tr = rls.simulate_tranches(H, offs, a, b, atr_max=c["amax"], trail_atr=c["trail"], max_total=max(0.10, 2 * m))
            s = bt.stats(eq, tr)
            out |= {f"{tag}_cagr": round(s["cagr%"], 1), f"{tag}_mdd": round(s["mdd%"], 1), f"{tag}_sh": round(s["sharpe"], 2),
                    f"{tag}_n": s.get("trades", 0)}
    return out
CUR = {"sma": 50, "slope": 20, "conf": 3, "be": 1.0, "drop": 1.0, "amax": 0.08, "trail": None}
if __name__ == "__main__":
    keys = list(SPACE)
    allc = [dict(zip(keys, v)) for v in itertools.product(*SPACE.values())]
    random.seed(7); sample = random.sample(allc, int(sys.argv[1])) + [CUR]
    t = time.time()
    with Pool(9) as p: df = pd.DataFrame(p.map(run, sample, chunksize=4))
    df.to_csv("search.csv", index=False); print(len(df), f"{time.time()-t:.0f}s")
