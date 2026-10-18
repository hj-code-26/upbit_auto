"""maxret 확인: 같은 몫·증거금 조합을 BTC·SOL·XRP 단독으로 (안 본 코인 포함). → maxret_coins.csv"""
import sys, pathlib, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path[:0] = [str(HERE.parents[1]), str(HERE)]
import pandas as pd, rls, backtest as bt
from multiprocessing import Pool
SETS = {3: (0, 8, 16), 6: (0, 4, 8, 12, 16, 20)}
def run(a):
    inst, n, m = a; H = {inst: bt.cached_hourly(inst)}; s0 = "2021-09-01" if inst[:3] == "SOL" else "2020-06-01"
    with bt.lever(30, m):
        eq, tr = rls.simulate_tranches(H, SETS[n], s0, "2026-10-16", max_total=2 * m)
    s = bt.stats(eq, tr); k = eq.index[(1 - eq / eq.cummax()) >= rls.KILL_DD]
    return {"코인": inst[:3], "몫": n, "증거금%": m * 100, "Sharpe": round(s["sharpe"], 2), "연수익": round(s["cagr%"], 1),
            "MDD": round(s["mdd%"], 1), "최악연도": round(s["yearly"].min() * 100, 1), "정지": str(k[0].date()) if len(k) else "-"}
if __name__ == "__main__":
    jobs = [(i, n, m) for i in ("BTC-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP") for n, m in ((3, .04), (3, .06), (3, .07), (6, .06), (6, .07), (6, .08))]
    with Pool(9) as p: df = pd.DataFrame(p.map(run, jobs))
    df.to_csv(HERE / "maxret_coins.csv", index=False)
    pd.set_option("display.width", 250); print(df.to_string(index=False))
