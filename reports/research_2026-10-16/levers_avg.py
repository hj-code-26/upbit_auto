"""레버리지 방식 전체 평균: 6개 구성(ETH·BTC·SOL·XRP 단독, BTC+ETH, ETH 서버와 같은 3몫) × 전체 기간 + 기간별(IS·OOS·2026).
증거금 4%, 3몫, 지금 코드(C3 + 동적 크기), 동시 한도 10%. → levers_avg.csv"""
import sys, pathlib, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path[:0] = [str(HERE.parents[1]), str(HERE)]
import numpy as np, pandas as pd, rls, backtest as bt, regime as R
from multiprocessing import Pool
OFFS = (0, 8, 16)
SETUPS = {"ETH": ("ETH-USDT-SWAP",), "BTC": ("BTC-USDT-SWAP",), "SOL": ("SOL-USDT-SWAP",), "XRP": ("XRP-USDT-SWAP",),
          "BTC+ETH": ("BTC-USDT-SWAP", "ETH-USDT-SWAP")}
clipL = lambda v: int(np.clip(round(v), 10, 50)) if np.isfinite(v) else 30  # noqa: E731
METHODS = [f"fixed{L}" for L in (10, 15, 20, 25, 30, 35, 40, 45, 50)] + ["vol1.0", "vol1.2", "adx", "conf0"]
def method(name, H):
    if name.startswith("fixed"):
        L = int(name[5:]); return lambda i, s: L
    if name.startswith("vol"):
        c = float(name[3:]); return lambda i, s: clipL(c / (s.atr / s.close))
    if name == "adx":
        A = {i: pd.concat([R.adx(rls.offset_daily(h, o, 1)) for o in OFFS]) for i, h in H.items()}
        return lambda i, s: clipL(A[i].get(s.name, 20) + 10)
    return lambda i, s: 30 if s.get("conf0", s.conf) != 0 else 15
def run(a):
    setup, name = a
    H = {i: bt.cached_hourly(i) for i in SETUPS[setup]}; f = method(name, H)
    s0 = "2021-09-01" if setup == "SOL" else "2020-06-01"
    out = {"setup": setup, "method": name}
    with bt.lever(30, 0.04):
        for tag, a0, b0 in (("all", s0, "2026-10-16"), ("is", s0, "2023-06-30"), ("oos", "2023-07-01", "2026-10-16"), ("y26", "2025-06-01", "2026-10-16")):
            eq, tr = rls.simulate_tranches(H, OFFS, a0, b0, lever_fn=f, max_total=0.10)
            if tag == "y26":
                eq = eq[eq.index >= "2025-12-31"]
                out |= {"y26_ret": (eq.iloc[-1] / eq.iloc[0] - 1) * 100, "y26_mdd": (1 - eq / eq.cummax()).max() * 100}
                continue
            s = bt.stats(eq, tr)
            out |= {f"{tag}_sh": s["sharpe"], f"{tag}_cagr": s["cagr%"], f"{tag}_mdd": s["mdd%"]}
            if tag == "all":
                out |= {"all_trades": len(tr), "all_win": (tr.pnl > 0).mean() * 100, "all_worst_yr": s["yearly"].min() * 100}
    return out
if __name__ == "__main__":
    with Pool(9) as p: df = pd.DataFrame(p.map(run, [(s, m) for s in SETUPS for m in METHODS], chunksize=2))
    df.to_csv(HERE / "levers_avg.csv", index=False)
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)
    g = df.groupby("method").agg(전체Sharpe=("all_sh", "mean"), 전체연수익=("all_cagr", "mean"), 전체MDD=("all_mdd", "mean"),
                                최악MDD=("all_mdd", "max"), 최악연도=("all_worst_yr", "min"), 승률=("all_win", "mean"),
                                IS_Sharpe=("is_sh", "mean"), OOS_Sharpe=("oos_sh", "mean"), 최저Sharpe=("all_sh", "min"),
                                y26수익=("y26_ret", "mean"), y26MDD=("y26_mdd", "mean")).loc[METHODS]
    g["기간차(OOS−IS)"] = g.OOS_Sharpe - g.IS_Sharpe
    g["수익/MDD"] = g.전체연수익 / g.전체MDD
    print(g.round(2).to_string())
    print("\n구성별 전체 기간 Sharpe"); print(df.pivot(index="method", columns="setup", values="all_sh").loc[METHODS].round(2).to_string())
    print("\n구성별 전체 기간 연수익 %"); print(df.pivot(index="method", columns="setup", values="all_cagr").loc[METHODS].round(1).to_string())
    print("\n구성별 전체 기간 MDD %"); print(df.pivot(index="method", columns="setup", values="all_mdd").loc[METHODS].round(1).to_string())
