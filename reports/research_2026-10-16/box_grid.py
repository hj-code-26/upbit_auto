"""박스 매매 × 레버리지(5·10·20·30·50·거래마다 최대) × 몫(박스 길이가 다른 하위 계좌 1~4개, 자본 균등) × 증거금 2·4·8% × 폭 1·1.5 ATR.
중립 레짐만, 경계 80% 분위. 몫 = 하위 계좌 일수익 평균. → box_grid.csv"""
import sys, pathlib, itertools, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path[:0] = [str(HERE.parents[1]), str(HERE)]
import numpy as np, pandas as pd, rls, backtest as bt, box_trade as B
from multiprocessing import Pool
TR = {1: (72,), 2: (48, 120), 3: (48, 72, 120), 4: (24, 48, 72, 120)}
CFGS = list(itertools.product(TR, (1.0, 1.5), (None, 5, 10, 20, 30, 50), (0.02, 0.04, 0.08)))
def job(a):
    inst, (n, k, L, m) = a
    h = bt.cached_hourly(inst); s0 = "2021-09-01" if inst[:3] == "SOL" else "2020-06-01"
    rets, ntr = [], 0
    for W in TR[n]:
        eq, tr = B.run(h, W, k, 0.8, True, margin=m, start=s0, end="2026-10-16", L_fix=L)
        rets.append(eq.pct_change()); ntr += len(tr)
    eq = (1 + pd.concat(rets, axis=1).fillna(0).mean(axis=1)).cumprod()
    out = {"inst": inst[:3], "몫": n, "k": k, "L": L or "최대", "증거금%": m * 100, "거래": ntr}
    for tag, a0, b0 in (("all", s0, "2026-10-16"), ("is", s0, "2023-06-30"), ("oos", "2023-07-01", "2026-10-16")):
        e = eq[(eq.index >= a0) & (eq.index <= b0)]; e = e / e.iloc[0]; s = bt.stats(e)
        out |= {f"{tag}_sh": round(s["sharpe"], 2), f"{tag}_ret": round(s["cagr%"], 1), f"{tag}_mdd": round(s["mdd%"], 1)}
    out["정지"] = bool(out["all_mdd"] >= 35)
    return out
if __name__ == "__main__":
    jobs = [(i, c) for i in ("ETH-USDT-SWAP", "BTC-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP") for c in CFGS]
    with Pool(9) as p: df = pd.DataFrame(p.map(job, jobs, chunksize=4))
    df.to_csv(HERE / "box_grid.csv", index=False)
    pd.set_option("display.width", 260); pd.set_option("display.max_rows", 300)
    g = df.groupby(["몫", "k", "L", "증거금%"]).agg(Sharpe=("all_sh", "mean"), 최저=("all_sh", "min"), IS=("is_sh", "mean"), OOS=("oos_sh", "mean"),
            연수익=("all_ret", "mean"), MDD=("all_mdd", "mean"), 정지=("정지", "sum"), 거래=("거래", "mean")).round(2)
    print("두 기간·4코인 평균이 모두 플러스:", int(((g.IS > 0) & (g.OOS > 0)).sum()), "/", len(g), " · 모든 코인 전체기간 플러스:", int((g.최저 > 0).sum()))
    print("\n== IS 평균 상위 15"); print(g.sort_values("IS", ascending=False).head(15).to_string())
    print("\n== 요인별 평균 Sharpe"); [print(c, g.groupby(level=c).Sharpe.mean().round(2).to_dict()) for c in ("몫", "k", "L", "증거금%")]
    print("\n== ETH 상위 8"); print(df[df.inst == "ETH"].sort_values("is_sh", ascending=False).head(8).to_string(index=False))
