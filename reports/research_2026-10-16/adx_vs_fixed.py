"""ADX 레버리지(L = ADX+10, 10~50x) vs 고정 25x vs 고정 30x — 구성별·연도별 (전체 기간 연속, 3몫, 4%, 지금 코드). → adx_vs_fixed.csv"""
import sys, pathlib, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path[:0] = [str(HERE.parents[1]), str(HERE)]
import numpy as np, pandas as pd, rls, backtest as bt, regime as R
from multiprocessing import Pool
OFFS = (0, 8, 16)
SETUPS = {"ETH": ("ETH-USDT-SWAP",), "BTC": ("BTC-USDT-SWAP",), "SOL": ("SOL-USDT-SWAP",), "XRP": ("XRP-USDT-SWAP",), "BTC+ETH": ("BTC-USDT-SWAP", "ETH-USDT-SWAP")}
def run(a):
    setup, name = a
    H = {i: bt.cached_hourly(i) for i in SETUPS[setup]}
    if name == "ADX":
        A = {i: pd.concat([R.adx(rls.offset_daily(h, o, 1)) for o in OFFS]) for i, h in H.items()}
        f = lambda i, s: int(np.clip(round(A[i].get(s.name, 20) + 10), 10, 50))  # noqa: E731
    else:
        L = int(name[:2]); f = lambda i, s: L  # noqa: E731
    s0 = "2021-09-01" if setup == "SOL" else "2020-06-01"
    with bt.lever(30, 0.04):
        eq, tr = rls.simulate_tranches(H, OFFS, s0, "2026-10-16", lever_fn=f, max_total=0.10)
    s = bt.stats(eq, tr)
    yr = {f"y{y}": round(v * 100, 1) for y, v in s["yearly"].items()}
    kill = eq.index[(1 - eq / eq.cummax()) >= rls.KILL_DD]
    return {"setup": setup, "method": name, "cagr": round(s["cagr%"], 1), "mdd": round(s["mdd%"], 1), "sharpe": round(s["sharpe"], 2),
            "worst_mo": round(s["worst_mo%"], 1), "trades": len(tr), "win": round((tr.pnl > 0).mean() * 100), "pf": round(s["pf"], 2),
            "L_mean": round(tr.lever.mean(), 1), "L_p10": int(tr.lever.quantile(0.1)), "L_p90": int(tr.lever.quantile(0.9)),
            "kill_at": str(kill[0].date()) if len(kill) else "", **yr}
if __name__ == "__main__":
    with Pool(9) as p: df = pd.DataFrame(p.map(run, [(s, m) for s in SETUPS for m in ("ADX", "25x", "30x")]))
    df.to_csv(HERE / "adx_vs_fixed.csv", index=False)
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)
    print(df.to_string(index=False))
