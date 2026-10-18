"""더 좋은 진입가: 판단 시각에 시장가 대신 지정가 (진입 방향으로 x×ATR 유리하게), W 시간 대기, 못 닿으면 건너뜀/시장가.
3몫(00·08·16 각 1/3) 평균, ETH·BTC·SOL·XRP, 전체 기간 연속. → limit_entry.csv"""
import sys, pathlib, itertools, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path[:0] = [str(HERE.parents[1]), str(HERE)]
import numpy as np, pandas as pd, backtest as bt, watch as W
from multiprocessing import Pool
INSTS = ("ETH-USDT-SWAP", "BTC-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP")
CFG = {"시장가 (지금)": None} | {f"지정가 {x}ATR · {w}h · {'못 닿으면 시장가' if fb else '못 닿으면 건너뜀'}": (x, w, fb)
                                for x, w, fb in itertools.product((0.1, 0.25, 0.5, 0.75), (4, 8, 24), (False, True))}
def job(a):
    inst, name = a
    h = bt.cached_hourly(inst); S = W.signals(h); s0 = "2021-09-01" if inst[:3] == "SOL" else "2020-06-01"
    rets, st, n = [], {"orders": 0, "filled": 0, "fallback": 0, "missed": 0}, 0
    for hr in (0, 8, 16):
        eq, tr = W.run(h, {hr}, s0, "2026-10-16", limit=CFG[name], S=S)
        rets.append(eq.pct_change()); n += len(tr)
        for k in st: st[k] += W.run.stats[k]
    eq = (1 + pd.concat(rets, axis=1).dropna().mean(axis=1)).cumprod()
    out = {"inst": inst[:3], "cfg": name, "trades": n, **st}
    for tag, a0, b0 in (("all", s0, "2026-10-16"), ("is", s0, "2023-06-30"), ("oos", "2023-07-01", "2026-10-16"), ("y26", "2025-12-31", "2026-10-16")):
        e = eq[(eq.index >= a0) & (eq.index <= b0)]; e = e / e.iloc[0]; s = bt.stats(e)
        out |= {f"{tag}_sh": round(s["sharpe"], 2), f"{tag}_cagr": round(s["cagr%"], 1) if tag != "y26" else round((e.iloc[-1] - 1) * 100, 1), f"{tag}_mdd": round(s["mdd%"], 1)}
    return out
if __name__ == "__main__":
    with Pool(9) as p: df = pd.DataFrame(p.map(job, [(i, c) for i in INSTS for c in CFG], chunksize=2))
    df.to_csv(HERE / "limit_entry.csv", index=False)
    pd.set_option("display.width", 260); pd.set_option("display.max_rows", 200)
    df["fill%"] = np.where(df.orders > 0, df.filled / df.orders * 100, np.nan)
    g = df.groupby("cfg").agg(Sharpe=("all_sh", "mean"), 최저=("all_sh", "min"), IS=("is_sh", "mean"), OOS=("oos_sh", "mean"), 연수익=("all_cagr", "mean"),
                              MDD=("all_mdd", "mean"), y26=("y26_cagr", "mean"), 거래=("trades", "mean"), 체결률=("fill%", "mean")).round(2).loc[list(CFG)]
    print("== 4코인 평균"); print(g.to_string())
    print("\n== ETH"); print(df[df.inst == "ETH"].set_index("cfg").loc[list(CFG), ["all_sh", "all_cagr", "all_mdd", "is_sh", "oos_sh", "y26_cagr", "trades", "fill%"]].round(2).to_string())
