import sys, warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2])); sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import numpy as np, pandas as pd, rls, backtest as bt, regime as R
from multiprocessing import Pool
ORIG = rls.prep
SETUPS = {"BTC+ETH 6몫": (("BTC-USDT-SWAP", "ETH-USDT-SWAP"), rls.TRANCHES, "2020-06-01"),
          "ETH 3몫(서버)": (("ETH-USDT-SWAP",), (0, 8, 16), "2020-06-01"),
          "SOL+XRP 6몫": (("SOL-USDT-SWAP", "XRP-USDT-SWAP"), rls.TRANCHES, "2021-08-01")}
def adx_prep(th, n):
    def prep(d):
        x = ORIG(d)
        up, dn = d.high.diff(), -d.low.diff()
        pdm = up.where((up > dn) & (up > 0), 0.0); ndm = dn.where((dn > up) & (dn > 0), 0.0)
        w = lambda s: s.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()  # noqa: E731
        raw = np.sign(w(pdm) - w(ndm)).where(R.adx(d) > th, 0).fillna(0).astype(int)
        same = pd.concat([raw == raw.shift(j) for j in range(n)], axis=1).all(axis=1)
        x["conf"] = x.conf.where(x.conf != 0, raw.where(same, 0))
        return x
    return prep
def run(a):
    setup, th, n = a
    insts, offs, s0 = SETUPS[setup]; H = {i: bt.cached_hourly(i) for i in insts}
    rls.prep = adx_prep(th, n) if th else ORIG
    out = {"setup": setup, "th": th, "n": n}
    with bt.lever(30, 0.04):
        for tag, a0, b0 in (("is", s0, "2023-06-30"), ("oos", "2023-07-01", "2026-10-12")):
            eq, tr = rls.simulate_tranches(H, offs, a0, b0, max_total=0.10)
            s = bt.stats(eq, tr); out |= {f"{tag}_sh": round(s["sharpe"], 2), f"{tag}_mdd": round(s["mdd%"], 1)}
    return out
if __name__ == "__main__":
    pd.set_option("display.width", 250)
    jobs = [(s, 0, 3) for s in SETUPS] + [(s, th, n) for s in SETUPS for th in (26, 28, 30, 32, 34) for n in (2, 3, 5)]
    with Pool(9) as p: df = pd.DataFrame(p.map(run, jobs))
    df.to_csv("neighbor.csv", index=False)
    for s in SETUPS:
        x = df[df.setup == s]; b = x[x.th == 0].iloc[0]
        print(f"\n== {s}  지금 규칙: IS {b.is_sh} / OOS {b.oos_sh}")
        print("IS Sharpe (행 ADX, 열 확정일)\n", x[x.th > 0].pivot(index="th", columns="n", values="is_sh"))
        print("OOS Sharpe\n", x[x.th > 0].pivot(index="th", columns="n", values="oos_sh"))
    h = bt.coin_hourly("ETH-USDT-SWAP", "2020-01-01")
    for off in (0, 8, 16):
        d = rls.offset_daily(h, off, 1)
        print(f"ETH UTC{off:02d}: ADX 최근5", R.adx(d).tail(5).round(1).tolist(), "보강 conf", adx_prep(30, 3)(d).conf.tail(5).tolist(), "원래", ORIG(d).conf.tail(5).tolist())
