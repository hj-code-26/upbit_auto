"""몫 3·6·8·12·24 × 증거금 4~12% (30x, 지금 코드, 동시 한도 = 증거금 × 2). ETH 전체 기간 연속 + 기간별, BTC·SOL 정지 여부. → tranches24.csv"""
import sys, pathlib, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path[:0] = [str(HERE.parents[1]), str(HERE)]
import pandas as pd, rls, backtest as bt
from multiprocessing import Pool
SETS = {n: tuple(range(0, 24, 24 // n)) for n in (3, 6, 8, 12, 24)}
def run(a):
    inst, n, m = a; H = {inst: bt.cached_hourly(inst)}; s0 = "2021-09-01" if inst[:3] == "SOL" else "2020-06-01"
    out = {"코인": inst[:3], "몫": n, "증거금%": m * 100}
    pers = (("전체", s0, "2026-10-16"), ("20~23", s0, "2023-06-30"), ("23~26", "2023-07-01", "2026-10-16")) if inst[:3] == "ETH" else (("전체", s0, "2026-10-16"),)
    with bt.lever(30, m):
        for tag, a0, b0 in pers:
            eq, tr = rls.simulate_tranches(H, SETS[n], a0, b0, max_total=2 * m); s = bt.stats(eq, tr)
            out |= {f"{tag} Sharpe": round(s["sharpe"], 2), f"{tag} 연수익": round(s["cagr%"], 1), f"{tag} MDD": round(s["mdd%"], 1)}
            if tag == "전체":
                k = eq.index[(1 - eq / eq.cummax()) >= rls.KILL_DD]
                out |= {"최악달": round(s["worst_mo%"], 1), "최악연도": round(s["yearly"].min() * 100, 1), "정지": str(k[0].date()) if len(k) else "-", "거래": len(tr)}
        if inst[:3] == "ETH":
            eq, _ = rls.simulate_tranches(H, SETS[n], "2025-06-01", "2026-10-16", max_total=2 * m); y = eq[eq.index >= "2025-12-31"]
            out |= {"2026": round((y.iloc[-1] / y.iloc[0] - 1) * 100, 1), "2026 MDD": round((1 - y / y.cummax()).max() * 100, 1)}
    px = {"ETH": 2.42, "BTC": 8.24, "SOL": None}[inst[:3]]
    if px: out["필요잔고(배수1)"] = round(px * 1.001 / (m / n * 30), 1)
    return out
if __name__ == "__main__":
    jobs = [("ETH-USDT-SWAP", n, m) for n in SETS for m in (0.04, 0.06, 0.08, 0.10, 0.12)] + \
           [(i, n, m) for i in ("BTC-USDT-SWAP", "SOL-USDT-SWAP") for n in (6, 12, 24) for m in (0.06, 0.08, 0.10)]
    with Pool(9) as p: df = pd.DataFrame(p.map(run, jobs, chunksize=1))
    df.to_csv(HERE / "tranches24.csv", index=False)
    pd.set_option("display.width", 320); pd.set_option("display.max_columns", 30)
    print(df[df.코인 == "ETH"].dropna(axis=1, how="all").to_string(index=False))
    print(); print(df[df.코인 != "ETH"].dropna(axis=1, how="all").to_string(index=False))
