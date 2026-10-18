"""BTC+ETH 한 계좌(3몫, C3 + 동적 크기)로 레버리지 방식·레버리지×증거금 조합 비교. 동시 증거금 한도 10% (운용과 같음). → levers_btceth.csv"""
import sys, pathlib, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path[:0] = [str(HERE.parents[1]), str(HERE)]
import numpy as np, pandas as pd, rls, backtest as bt, regime as R
from multiprocessing import Pool
OFFS = (0, 8, 16)
H = {i: bt.cached_hourly(i) for i in rls.COINS}
clipL = lambda v: int(np.clip(round(v), 10, 50)) if np.isfinite(v) else 30  # noqa: E731
def method(name):
    if name.startswith("fixed"):
        L = int(name[5:]); return lambda i, s: L
    if name.startswith("vol"):
        c = float(name[3:]); return lambda i, s: clipL(c / (s.atr / s.close))
    if name == "adx":
        A = {i: pd.concat([R.adx(rls.offset_daily(h, o, 1)) for o in OFFS]) for i, h in H.items()}
        return lambda i, s: clipL(A[i].get(s.name, 20) + 10)
    if name == "conf0":
        return lambda i, s: 30 if s.get("conf0", s.conf) != 0 else 15
def run(a):
    name, m, cap = a
    f = method(name); out = {"method": name, "margin%": m * 100}
    with bt.lever(30, m):
        for tag, a0, b0 in (("is", "2020-06-01", "2023-06-30"), ("oos", "2023-07-01", "2026-10-16"), ("y26", "2025-06-01", "2026-10-16")):
            eq, tr = rls.simulate_tranches(H, OFFS, a0, b0, lever_fn=f, max_total=cap)
            if tag == "y26":
                eq = eq[eq.index >= "2025-12-31"]
                out |= {"y26%": round((eq.iloc[-1] / eq.iloc[0] - 1) * 100, 1), "y26_mdd": round((1 - eq / eq.cummax()).max() * 100, 1)}
                continue
            s = bt.stats(eq, tr)
            out |= {f"{tag}_sh": round(s["sharpe"], 2), f"{tag}_cagr": round(s["cagr%"], 1), f"{tag}_mdd": round(s["mdd%"], 1),
                    f"{tag}_n": len(tr), f"{tag}_liq": int((tr.why == "liq").sum()) if len(tr) else 0,
                    f"{tag}_kill": bool((1 - eq / eq.cummax()).max() >= rls.KILL_DD)}
    return out
if __name__ == "__main__":
    A = [(f"fixed{L}", 0.04, 0.10) for L in (10, 15, 20, 25, 30, 35, 40, 45, 50)] + [(n, 0.04, 0.10) for n in ("vol1.0", "vol1.2", "adx", "conf0")]
    B = [("fixed30", 0.04, 0.10), ("fixed25", 0.04, 0.10), ("fixed25", 0.05, 0.10), ("fixed20", 0.05, 0.10), ("fixed20", 0.06, 0.12)]
    with Pool(9) as p: df = pd.DataFrame(p.map(run, A + B))
    df["set"] = ["A"] * len(A) + ["B"] * len(B)
    df.to_csv(HERE / "levers_btceth.csv", index=False)
    pd.set_option("display.width", 250)
    c = ["method", "margin%", "is_sh", "is_cagr", "is_mdd", "oos_sh", "oos_cagr", "oos_mdd", "y26%", "y26_mdd", "is_n", "oos_n", "is_liq", "oos_liq", "is_kill", "oos_kill"]
    print("== A: 방식 비교 (증거금 4%)"); print(df[df.set == "A"][c].to_string(index=False))
    print("\n== B: 레버리지 × 증거금"); print(df[df.set == "B"][c].to_string(index=False))
