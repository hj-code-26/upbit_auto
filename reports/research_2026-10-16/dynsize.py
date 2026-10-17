"""동적 크기(유효 레버리지) 연구: 거래소 30x 고정, 진입 증거금 = 기본 4% × k(신호 봉). k 규칙별 IS/OOS·안 본 코인 비교.
python dynsize.py → dynsize.csv"""
import sys, pathlib, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path[:0] = [str(HERE.parents[1]), str(HERE)]
import numpy as np, pandas as pd, rls, backtest as bt, regime as R
from multiprocessing import Pool
SETUPS = {"ETH 3몫(서버)": (("ETH-USDT-SWAP",), (0, 8, 16), "2020-06-01"),
          "BTC+ETH 6몫": (("BTC-USDT-SWAP", "ETH-USDT-SWAP"), (0, 4, 8, 12, 16, 20), "2020-06-01"),
          "SOL+XRP 6몫": (("SOL-USDT-SWAP", "XRP-USDT-SWAP"), (0, 4, 8, 12, 16, 20), "2021-08-01")}
PER = lambda s0: (("is", s0, "2023-06-30"), ("oos", "2023-07-01", "2026-10-12"))  # noqa: E731
def feats(insts, offs):
    out = {}
    for i in insts:
        h = bt.cached_hourly(i)
        out[i] = pd.concat([R.scores(rls.offset_daily(h, o, 1)) for o in offs])
    return out
def rule(name, F):
    clip = lambda v: float(np.clip(v, 0.5, 2.0)) if np.isfinite(v) else 1.0  # noqa: E731
    f = lambda i, sig, c: F[i][c].get(sig.name, np.nan)  # noqa: E731
    return {"base": None,
            "vol T3%": lambda i, s: clip(0.03 / (s.atr / s.close)),
            "vol T4%": lambda i, s: clip(0.04 / (s.atr / s.close)),
            "adx": lambda i, s: clip((f(i, s, "adx") - 10) / 20),
            "er": lambda i, s: clip(f(i, s, "er") / 0.25),
            "vol T3%×adx": lambda i, s: clip(0.03 / (s.atr / s.close) * (f(i, s, "adx") - 10) / 20),
            "역(고변동 확대)": lambda i, s: clip((s.atr / s.close) / 0.03)}[name]
RULES = ["base", "vol T3%", "vol T4%", "adx", "er", "vol T3%×adx", "역(고변동 확대)"]
def run(a):
    setup, name = a
    insts, offs, s0 = SETUPS[setup]
    H = {i: bt.cached_hourly(i) for i in insts}; F = feats(insts, offs); k = rule(name, F)
    ks = []
    kk = (lambda i, s: ks.append(k(i, s)) or ks[-1]) if k else None
    out = {"setup": setup, "rule": name}
    with bt.lever(30, 0.04):
        for tag, a0, b0 in PER(s0):
            ks.clear()
            eq, tr = rls.simulate_tranches(H, offs, a0, b0, max_total=0.10, size_k=kk)
            s = bt.stats(eq, tr)
            out |= {f"{tag}_cagr": round(s["cagr%"], 1), f"{tag}_mdd": round(s["mdd%"], 1), f"{tag}_sh": round(s["sharpe"], 2),
                    f"{tag}_k": round(float(np.mean(ks)), 2) if ks else 1.0, f"{tag}_kill": bool((1 - eq / eq.cummax()).max() >= rls.KILL_DD)}
    return out
if __name__ == "__main__":
    with Pool(9) as p: df = pd.DataFrame(p.map(run, [(s, r) for s in SETUPS for r in RULES]))
    df.to_csv(HERE / "dynsize.csv", index=False)
    pd.set_option("display.width", 250)
    for s in SETUPS: print(f"\n== {s}"); print(df[df.setup == s].drop(columns="setup").to_string(index=False))
