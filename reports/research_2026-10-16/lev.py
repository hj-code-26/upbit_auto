import sys, itertools, warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))
import pandas as pd, numpy as np, rls, backtest as bt
from multiprocessing import Pool
INSTS = sys.argv[1].split(",")
H = {i: bt.cached_hourly(i) for i in INSTS}
s0 = "2021-06-01" if "SOL-USDT-SWAP" in INSTS else "2020-04-01"
PER = {"all": (s0, "2026-10-15"), "is": (s0, "2023-06-30"), "oos": ("2023-07-01", "2026-10-15")}
TR = {3: (0, 8, 16), 6: (0, 4, 8, 12, 16, 20)}
def run(c):
    L, m, n = c
    out = {"L": L, "margin%": m * 100, "tr": n, "notional%": round(m * L * 100)}
    with bt.lever(L, m):
        for tag, (a, b) in PER.items():
            eq, tr = rls.simulate_tranches(H, TR[n], a, b, max_total=max(0.10, 2 * m))
            s = bt.stats(eq, tr)
            out |= {f"{tag}_cagr": round(s["cagr%"], 1), f"{tag}_mdd": round(s["mdd%"], 1), f"{tag}_sh": round(s["sharpe"], 2)}
            if tag == "all":
                out |= {"n": s.get("trades", 0), "kill": bool((1 - eq / eq.cummax()).max() >= rls.KILL_DD), "worst_yr": round(s["yearly"].min() * 100, 1),
                        "stop%": round((rls.liq_dist(L, 0.004) - rls.LIQ_BUF) * 100, 2)}
    return out
if __name__ == "__main__":
    g = list(itertools.product([10, 15, 20, 25, 30, 40, 50], [0.02, 0.03, 0.04, 0.05, 0.06], [3, 6]))
    with Pool(9) as p: df = pd.DataFrame(p.map(run, g))
    df.to_csv(sys.argv[2], index=False); print("done", len(df))
