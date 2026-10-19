"""몫 시각 선택: (1) 1몫을 UTC 0~23시 각각, (2) 4시간 경계 6개 중 3개 조합 20개, (3) 균등 간격을 1시간씩 민 3몫(0~7시 시작)·6몫(0~3시 시작).
ETH·BTC 단독, 30x · 4%, 지금 코드. IS 2020-06~2023-06 / OOS 2023-07~2026-10. → tranche_hours.csv"""
import sys, pathlib, itertools, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path[:0] = [str(HERE.parents[1]), str(HERE)]
import pandas as pd, rls, backtest as bt
from multiprocessing import Pool
SETS = [("1몫", (h,)) for h in range(24)] + [("3몫 4H", c) for c in itertools.combinations((0, 4, 8, 12, 16, 20), 3)] + \
       [("3몫 균등", (s, s + 8, s + 16)) for s in range(8)] + [("6몫 균등", tuple(s + 4 * i for i in range(6))) for s in range(4)]
def job(a):
    inst, (kind, offs) = a; H = {inst: bt.cached_hourly(inst)}
    out = {"inst": inst[:3], "kind": kind, "offs": "·".join(f"{h:02d}" for h in offs), "live_ok": all(h % 4 == 0 for h in offs)}
    with bt.lever(30, 0.04):
        for tag, a0, b0 in (("is", "2020-06-01", "2023-06-30"), ("oos", "2023-07-01", "2026-10-16")):
            eq, tr = rls.simulate_tranches(H, offs, a0, b0); s = bt.stats(eq)
            out |= {f"{tag}_sh": round(s["sharpe"], 2), f"{tag}_cagr": round(s["cagr%"], 1), f"{tag}_mdd": round(s["mdd%"], 1)}
    return out
if __name__ == "__main__":
    with Pool(9) as p: df = pd.DataFrame(p.map(job, [(i, s) for i in ("ETH-USDT-SWAP", "BTC-USDT-SWAP") for s in SETS], chunksize=2))
    df.to_csv(HERE / "tranche_hours.csv", index=False)
    pd.set_option("display.width", 250); pd.set_option("display.max_rows", 200)
    for kind in ("1몫", "3몫 4H", "3몫 균등", "6몫 균등"):
        x = df[df.kind == kind]
        w = x.pivot(index="offs", columns="inst", values=["is_sh", "oos_sh", "is_cagr", "oos_cagr", "oos_mdd"])
        w.columns = [f"{a}_{b}" for a, b in w.columns]
        w = w.sort_values("is_sh_ETH", ascending=False)
        print(f"\n===== {kind} (ETH 2020~23 Sharpe 순)"); print(w.to_string())
        for c in ("ETH", "BTC"):
            y = x[x.inst == c]
            print(f"   {c}: IS·OOS Sharpe 순위 상관 {y.is_sh.corr(y.oos_sh, method='spearman'):.2f}")
