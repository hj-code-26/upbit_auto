"""몫 수 × 증거금으로 수익 극대화 (ETH 단독, 30x, 지금 코드). 동시 한도 = 증거금 × 2 (동적 크기 배수 최대 2 를 막지 않게).
전체 기간 연속(정지 포함) + 기간별. → maxret.csv"""
import sys, pathlib, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path[:0] = [str(HERE.parents[1]), str(HERE)]
import pandas as pd, rls, backtest as bt
from multiprocessing import Pool
SETS = {3: (0, 8, 16), 6: (0, 4, 8, 12, 16, 20)}
def run(a):
    n, m = a; H = {"ETH-USDT-SWAP": bt.cached_hourly("ETH-USDT-SWAP")}; out = {"몫": n, "증거금%": m * 100}
    with bt.lever(30, m):
        for tag, a0, b0 in (("전체", "2020-06-01", "2026-10-16"), ("20~23", "2020-06-01", "2023-06-30"), ("23~26", "2023-07-01", "2026-10-16")):
            eq, tr = rls.simulate_tranches(H, SETS[n], a0, b0, max_total=2 * m); s = bt.stats(eq, tr)
            out |= {f"{tag} Sharpe": round(s["sharpe"], 2), f"{tag} 연수익": round(s["cagr%"], 1), f"{tag} MDD": round(s["mdd%"], 1)}
            if tag == "전체":
                k = eq.index[(1 - eq / eq.cummax()) >= rls.KILL_DD]
                out |= {"최악달": round(s["worst_mo%"], 1), "최악연도": round(s["yearly"].min() * 100, 1), "정지": str(k[0].date()) if len(k) else "-"}
        eq, _ = rls.simulate_tranches(H, SETS[n], "2025-06-01", "2026-10-16", max_total=2 * m); y = eq[eq.index >= "2025-12-31"]
    out |= {"2026": round((y.iloc[-1] / y.iloc[0] - 1) * 100, 1), "2026 MDD": round((1 - y / y.cummax()).max() * 100, 1),
            "필요잔고(배수1)": round(2.42 * 1.001 / (m / n * 30), 1)}
    return out
if __name__ == "__main__":
    with Pool(9) as p: df = pd.DataFrame(p.map(run, [(n, m) for n in (3, 6) for m in (0.04, 0.05, 0.06, 0.07, 0.08)]))
    df.to_csv(HERE / "maxret.csv", index=False)
    pd.set_option("display.width", 300); pd.set_option("display.max_columns", 30); print(df.to_string(index=False))
