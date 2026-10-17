"""regime5 단계 성향 탐색: 선택은 IS(2020-08~2023-06, ETH·BTC)만. OOS(2023-07~)·SOL·XRP 는 확인용. → regime5_grid.csv"""
import sys, pathlib, itertools, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path[:0] = [str(HERE.parents[1]), str(HERE)]
import numpy as np, pandas as pd, rls, backtest as bt, regime5 as R5
from multiprocessing import Pool
H = {i: bt.cached_hourly(i) for i in ("ETH-USDT-SWAP", "BTC-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP")}
PER = {"is": ("2020-08-01", "2023-06-30"), "oos": ("2023-07-01", "2026-10-12")}
def table(s1, s2, r5, r4, tr5, st5, ex):
    t = {5: dict(side=1, risk=r5, stop=st5, trail=tr5, drop1=False, be=None, expo=ex),
         4: dict(side=1, risk=r4, stop=1.5, trail=None, drop1=True, be=1.0, expo=ex), 3: dict(side=0)}
    for lv, sd in ((1, s1), (2, s2)):
        t[lv] = dict(side=-1, risk=0.015, stop=1.5, trail=2.5, drop1=True, be=1.0, expo=ex) if sd else dict(side=0)
    return t
def run(c):
    t = table(*c); out = dict(zip(("short1", "short2", "risk5", "risk4", "trail5", "stop5", "expo"), c))
    for inst, h in H.items():
        for tag, (a, b) in PER.items():
            if inst[:3] == "SOL" and tag == "is": a = "2021-09-01"
            eq, _ = R5.run_tranches(h, start=a, end=b, table=t)
            s = bt.stats(eq); out |= {f"{inst[:3]}_{tag}_sh": round(s["sharpe"], 2), f"{inst[:3]}_{tag}_cagr": round(s["cagr%"], 1), f"{inst[:3]}_{tag}_mdd": round(s["mdd%"], 1)}
    return out
if __name__ == "__main__":
    grid = list(itertools.product((0, 1), (0, 1), (0.03, 0.06, 0.10), (0.015, 0.03), (2.0, 3.0, 4.0), (1.5, 2.0), (3.0, 5.0)))
    with Pool(9) as p: df = pd.DataFrame(p.map(run, grid, chunksize=4))
    df["is_score"] = df[["ETH_is_sh", "BTC_is_sh"]].mean(axis=1)
    df.to_csv(HERE / "regime5_grid.csv", index=False)
    pd.set_option("display.width", 300); pd.set_option("display.max_columns", 40)
    cols = ["short1", "short2", "risk5", "risk4", "trail5", "stop5", "expo", "is_score"] + [f"{c}_{t}_{m}" for c in ("ETH", "BTC") for t in ("is", "oos") for m in ("cagr", "mdd", "sh")] + ["SOL_oos_sh", "XRP_oos_sh", "SOL_is_sh", "XRP_is_sh"]
    print(df.sort_values("is_score", ascending=False)[cols].head(12).to_string(index=False))
    print("\n요인별 IS 점수 중앙값"); [print(k, df.groupby(k).is_score.median().round(2).to_dict()) for k in ("short1", "short2", "risk5", "risk4", "trail5", "stop5", "expo")]
