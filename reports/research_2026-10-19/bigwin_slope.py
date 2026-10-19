"""bigwin_test 이웃값: 신호 봉 SMA50 20일 기울기(진입 방향) > th% 이면 크기 ×k. → bigwin_slope.csv"""
import pathlib, sys, time, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path[:0] = [str(HERE.parents[1]), str(HERE)]
import pandas as pd, rls, backtest as bt
import bigwin_test as T
from multiprocessing import Pool


def run(job):
    T.feat_table(); cfg, th, k = job; ins, offs, s0 = T.CFG[cfg]; H = {i: bt.cached_hourly(i) for i in ins}
    f = lambda i, sig: 1.0 if int(sig.conf) * T.F[i].at[sig.name, "slope"] <= th else k  # noqa: E731
    out = {"구성": cfg, "기울기>": th, "배수": k}
    for p, a, b in [("전체", s0, None), ("IS", s0, "2023-06-30"), ("OOS", "2023-07-01", None)]:
        eq, tr = rls.simulate_tranches(H, offs, a, b, max_total=0.15, size_k=f); s = bt.stats(eq, tr)
        out |= {f"{p} 연수익": round(s["cagr%"], 1), f"{p} MDD": round(s["mdd%"], 1), f"{p} Sharpe": round(s["sharpe"], 2)}
    print(out, flush=True)
    return out


if __name__ == "__main__":
    t0 = time.time()
    cfgs = ("BTC+ETH 6몫", "ETH 3몫", "SOL+XRP 3몫")
    jobs = [(c, th, 0.5) for c in cfgs for th in (6, 10, 12)] + [(c, 8, 0.75) for c in cfgs]
    with Pool(9) as p:
        rows = p.map(run, jobs)
    pd.DataFrame(rows).to_csv(HERE / "bigwin_slope.csv", index=False)
    print(f"done {time.time() - t0:.0f}s")
