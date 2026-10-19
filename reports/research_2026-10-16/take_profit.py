"""고정 익절: 진입가 ± k×ATR(진입 시) 지정가 전량 익절 (같은 봉에 손절도 닿으면 손절 먼저). 지금 코드, 3몫, 30x · 4%.
ETH·BTC·SOL·XRP 단독, 전체 기간 연속 + 기간별. → take_profit.csv"""
import sys, pathlib, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path[:0] = [str(HERE.parents[1]), str(HERE)]
import pandas as pd, rls, backtest as bt
from multiprocessing import Pool
KS = [None, 0.5, 0.8, 1.0, 1.5, 2.0, 3.0, 5.0]
def job(a):
    inst, k = a; H = {inst: bt.cached_hourly(inst)}; s0 = "2021-09-01" if inst[:3] == "SOL" else "2020-06-01"
    out = {"inst": inst[:3], "tp_atr": k or "없음"}
    with bt.lever(30, 0.04):
        for tag, a0, b0 in (("all", s0, "2026-10-16"), ("is", s0, "2023-06-30"), ("oos", "2023-07-01", "2026-10-16")):
            eq, tr = rls.simulate_tranches(H, (0, 8, 16), a0, b0, tp_atr=k); s = bt.stats(eq, tr)
            out |= {f"{tag}_sh": round(s["sharpe"], 2), f"{tag}_cagr": round(s["cagr%"], 1), f"{tag}_mdd": round(s["mdd%"], 1)}
            if tag == "all":
                out |= {"win%": round(s.get("win", 0), 1), "pf": round(s.get("pf", 0), 2), "tp%": round((tr.why == "tp").mean() * 100, 1) if len(tr) else 0}
        eq, _ = rls.simulate_tranches(H, (0, 8, 16), "2025-06-01", "2026-10-16", tp_atr=k); y = eq[eq.index >= "2025-12-31"]
    return out | {"2026": round((y.iloc[-1] / y.iloc[0] - 1) * 100, 1)}
if __name__ == "__main__":
    with Pool(9) as p: df = pd.DataFrame(p.map(job, [(i, k) for i in ("ETH-USDT-SWAP", "BTC-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP") for k in KS]))
    df.to_csv(HERE / "take_profit.csv", index=False)
    pd.set_option("display.width", 250)
    print(df[df.inst == "ETH"].drop(columns="inst").to_string(index=False))
    g = df.groupby("tp_atr", sort=False).agg(Sharpe=("all_sh", "mean"), 최저=("all_sh", "min"), IS=("is_sh", "mean"), OOS=("oos_sh", "mean"),
                                           연수익=("all_cagr", "mean"), MDD=("all_mdd", "mean"), 승률=("win%", "mean"), y2026=("2026", "mean")).round(2)
    print("\n== 4코인 평균"); print(g.to_string())
