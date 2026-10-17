"""기간별 위험·수익 분포: ETH 3몫(서버) · 거래소 30x · 기본 증거금 m × k(변동성 목표 T=4%: k = clip(4%/ATR%, 0.5, 2)).
겹치는 창(시작일마다)으로 30/90/365일 수익 분포. python riskprofile.py → riskprofile.csv"""
import sys, pathlib, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path[:0] = [str(HERE.parents[1]), str(HERE)]
import numpy as np, pandas as pd, rls, backtest as bt
from multiprocessing import Pool
H = {"ETH-USDT-SWAP": bt.cached_hourly("ETH-USDT-SWAP")}
VOL = lambda i, s: float(np.clip(0.04 / (s.atr / s.close), 0.5, 2.0))  # noqa: E731
def run(a):
    sizing, m = a
    with bt.lever(30, m):
        eq, tr = rls.simulate_tranches(H, (0, 8, 16), "2020-06-01", "2026-10-15", max_total=0.10, size_k=VOL if sizing == "변동성목표" else None)
    eq = eq.groupby(eq.index.normalize()).last()
    out = {"sizing": sizing, "margin%": m * 100, "cagr%": round(bt.stats(eq)["cagr%"], 1), "mdd%": round((1 - eq / eq.cummax()).max() * 100, 1),
           "kill": bool((1 - eq / eq.cummax()).max() >= rls.KILL_DD)}
    for w in (30, 90, 365):
        r = (eq.shift(-w) / eq - 1).dropna() * 100
        out |= {f"{w}d_median%": round(r.median(), 1), f"{w}d_p10%": round(r.quantile(0.10), 1), f"{w}d_p90%": round(r.quantile(0.90), 1),
                f"{w}d_loss%": round((r < 0).mean() * 100)}
    return out
if __name__ == "__main__":
    jobs = [(s, m) for s in ("고정", "변동성목표") for m in (0.02, 0.03, 0.04, 0.05)]
    with Pool(8) as p: df = pd.DataFrame(p.map(run, jobs))
    df.to_csv(HERE / "riskprofile.csv", index=False)
    pd.set_option("display.width", 250); print(df.to_string(index=False))
