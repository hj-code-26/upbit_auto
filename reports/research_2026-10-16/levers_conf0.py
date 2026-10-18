"""신호 품질 레버리지 이웃값: SMA 확정 신호 L_hi, ADX 보강 신호 L_lo. → levers_conf0.csv"""
import sys, pathlib, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path[:0] = [str(HERE.parents[1]), str(HERE)]
import pandas as pd, rls, backtest as bt
from multiprocessing import Pool
def run(a):
    inst, hi, lo = a
    h = bt.cached_hourly(inst); s0 = "2021-09-01" if inst[:3] == "SOL" else "2020-06-01"
    f = lambda i, s: hi if s.get("conf0", s.conf) != 0 else lo  # noqa: E731
    out = {"inst": inst[:3], "hi": hi, "lo": lo}
    with bt.lever(30, 0.04):
        for tag, a0, b0 in (("is", s0, "2023-06-30"), ("oos", "2023-07-01", "2026-10-16")):
            eq, tr = rls.simulate_tranches({inst: h}, (0, 8, 16), a0, b0, lever_fn=f)
            s = bt.stats(eq); out |= {f"{tag}_sh": round(s["sharpe"], 2), f"{tag}_cagr": round(s["cagr%"], 1), f"{tag}_mdd": round(s["mdd%"], 1)}
    return out
if __name__ == "__main__":
    jobs = [(i, hi, lo) for i in ("ETH-USDT-SWAP", "BTC-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP") for hi in (20, 25, 30) for lo in (10, 15, 20, 25, 30)]
    with Pool(9) as p: df = pd.DataFrame(p.map(run, jobs))
    df.to_csv(HERE / "levers_conf0.csv", index=False)
    pd.set_option("display.width", 250)
    df["avg8"] = 0.0
    w = df.pivot_table(index=["hi", "lo"], columns="inst", values=["is_sh", "oos_sh"])
    w["평균(4코인·2기간)"] = w.mean(axis=1); w["최저"] = w.drop(columns="평균(4코인·2기간)").min(axis=1)
    print(w.round(2).to_string())
