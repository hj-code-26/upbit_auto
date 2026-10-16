import sys, warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2])); sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import numpy as np, pandas as pd, rls, backtest as bt, regime as R
from multiprocessing import Pool
ALL = {c: bt.cached_hourly(c) for c in ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP")}
SC = {c: pd.concat([R.scores(rls.offset_daily(h, off, 1)) for off in range(0, 24, 4)]) for c, h in ALL.items()}
SC = {c: s[~s.index.duplicated()] for c, s in SC.items()}
ORIG_PREP = rls.prep
SETUPS = {"BTC+ETH 6몫": (("BTC-USDT-SWAP", "ETH-USDT-SWAP"), rls.TRANCHES),
          "ETH 3몫(서버)": (("ETH-USDT-SWAP",), (0, 8, 16)),
          "SOL+XRP 6몫": (("SOL-USDT-SWAP", "XRP-USDT-SWAP"), rls.TRANCHES)}
PER = {"BTC+ETH 6몫": [("is", "2020-06-01", "2023-06-30"), ("oos", "2023-07-01", "2026-10-15"), ("3y", "2023-10-16", "2026-10-15")],
       "ETH 3몫(서버)": [("is", "2020-06-01", "2023-06-30"), ("oos", "2023-07-01", "2026-10-15"), ("3y", "2023-10-16", "2026-10-15")],
       "SOL+XRP 6몫": [("is", "2021-08-01", "2023-06-30"), ("oos", "2023-07-01", "2026-10-12"), ("3y", "2023-10-16", "2026-10-12")]}

def filt(kind, th):
    if kind == "none": return None
    def ok(inst, day):
        v = SC[inst][kind].get(day, np.nan)
        return bool(v > th) if kind in ("er", "adx") else bool(v < th) if kind == "chop" else bool(v < th)
    return ok

def adx_prep(th, conf_n=3, only_neutral=True):
    """확정 레짐이 0 일 때 ADX>th 이면 DI 방향을 레짐으로 (conf_n 일 연속이면 확정)."""
    def prep(d):
        x = ORIG_PREP(d)
        up, dn = d.high.diff(), -d.low.diff()
        pdm = up.where((up > dn) & (up > 0), 0.0); ndm = dn.where((dn > up) & (dn > 0), 0.0)
        w = lambda s: s.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()  # noqa: E731
        di = np.sign(w(pdm) - w(ndm))
        a = R.adx(d)
        raw = di.where(a > th, 0).fillna(0).astype(int)
        same = pd.concat([raw == raw.shift(j) for j in range(conf_n)], axis=1).all(axis=1)
        adir = raw.where(same, 0)
        x["conf"] = x.conf.where(x.conf != 0, adir) if only_neutral else adir
        return x
    return prep

def run(args):
    setup, kind, th = args
    insts, offs = SETUPS[setup]; H = {i: ALL[i] for i in insts}
    out = {"setup": setup, "kind": kind, "th": th}
    rls.prep = adx_prep(th) if kind == "adx_dir" else adx_prep(th, only_neutral=False) if kind == "adx_only" else ORIG_PREP
    try:
        with bt.lever(30, 0.04):
            for tag, a, b in PER[setup]:
                eq, tr = rls.simulate_tranches(H, offs, a, b, max_total=0.10,
                                               entry_ok=filt(kind, th) if kind in ("er", "adx", "chop", "volp") else None)
                s = bt.stats(eq, tr)
                out |= {f"{tag}_cagr": round(s["cagr%"], 1), f"{tag}_mdd": round(s["mdd%"], 1), f"{tag}_sh": round(s["sharpe"], 2), f"{tag}_n": s.get("trades", 0)}
    finally:
        rls.prep = ORIG_PREP
    return out

if __name__ == "__main__":
    grid = [("none", 0)] + [("er", t) for t in (0.1, 0.15, 0.2, 0.3)] + [("adx", t) for t in (15, 20, 25, 30)] \
           + [("chop", t) for t in (50, 55, 61.8)] + [("volp", t) for t in (0.6, 0.8, 0.9)] \
           + [("adx_dir", t) for t in (20, 25, 30, 35)] + [("adx_only", t) for t in (20, 25, 30)]
    jobs = [(s, k, t) for s in SETUPS for k, t in grid]
    with Pool(9) as p: df = pd.DataFrame(p.map(run, jobs))
    df.to_csv("regtest.csv", index=False)
    pd.set_option("display.width", 250); pd.set_option("display.max_rows", 200)
    for s in SETUPS:
        print(f"\n===== {s} =====")
        print(df[df.setup == s].drop(columns="setup").to_string(index=False))
