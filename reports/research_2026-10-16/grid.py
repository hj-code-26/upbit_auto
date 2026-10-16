import sys, itertools, json, time
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))
import pandas as pd, numpy as np, rls, backtest as bt
from multiprocessing import Pool
INSTS = sys.argv[1].split(",") if len(sys.argv) > 1 else list(rls.COINS)
H = {i: bt.cached_hourly(i) for i in INSTS}
PER = {"is": ("2020-04-01", "2023-06-30"), "oos": ("2023-07-01", "2026-10-15")}
if "SOL-USDT-SWAP" in INSTS: PER["is"] = ("2021-06-01", "2023-06-30")

def run(cfg):
    L, size, drop, edge, amax = cfg
    kw = dict(atr_max=amax, max_total=0.6)
    if size != "fix": kw["risk"] = size
    if edge != "base": kw[edge[0]] = edge[1]
    rls.DROP_ATR = drop
    out = {}
    with bt.lever(L):
        for tag, (a, b) in PER.items():
            eq, tr = rls.simulate_tranches(H, rls.TRANCHES, a, b, **kw)
            s = bt.stats(eq, tr)
            out[tag] = {k: round(float(s[k]), 2) for k in ("cagr%", "mdd%", "sharpe")} | {"n": int(s.get("trades", 0)), "win": round(s.get("win", 0), 1)}
    return {"L": L, "size": size, "drop": drop, "edge": str(edge), "amax": amax, **{f"{t}_{k}": v for t, o in out.items() for k, v in o.items()}}

if __name__ == "__main__":
    grid = list(itertools.product([30, 20, 10, 5], ["fix", 0.005, 0.01, 0.015], [1.0, 2.0, 99.0],
                                  ["base", ("edge_atr", 1.0), ("edge_days", 10), ("be_edge", False)], [0.08, None]))
    grid = [g for g in grid if not (g[3] != "base" and g[3][0] == "be_edge")]   # be_edge False already default
    t = time.time()
    with Pool(8) as p:
        res = p.map(run, grid, chunksize=2)
    df = pd.DataFrame(res)
    df.to_csv(f"{sys.argv[2] if len(sys.argv) > 2 else 'grid'}.csv", index=False)
    print(f"{len(grid)} cfgs {time.time()-t:.0f}s")
    pd.set_option("display.width", 250)
    base = df[(df.L == 30) & (df["size"] == "fix") & (df.drop == 1.0) & (df.edge == "base") & (df.amax == 0.08)]
    print("BASELINE\n", base.to_string(index=False))
    print("TOP by IS sharpe\n", df.sort_values("is_sharpe", ascending=False).head(20).to_string(index=False))
