"""코인별 비중 (종목당 증거금) — 동시 한도 10% 안에서 어떻게 나눌까. 고정 크기 6몫 30x, 순위 ETH→ZEC→BTC.
size_k = 코인 비중 / 4% (rls.simulate 기본 증거금 4% 에 곱함). ① BTC+ETH+ZEC 9개월 ② BTC+ETH 2020~ (긴 구간, 비율 확인). → weights.csv"""
import pathlib, sys, itertools, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path.insert(0, str(HERE.parents[1]))
import pandas as pd, rls, backtest as bt
from multiprocessing import Pool

E, Z, B = "ETH-USDT-SWAP", "ZEC-USDT-SWAP", "BTC-USDT-SWAP"
P9 = [("9개월", "2026-01-20", "2026-10-18")]
PL = [("전체", "2020-04-01", None), ("20-22", "2020-04-01", "2022-12-31"), ("23-24", "2023-01-01", "2024-12-31"), ("25~", "2025-01-01", None)]


def run(job):
    w, per = job; ins = tuple(i for i in (E, Z, B) if w.get(i)); H = {i: bt.cached_hourly(i) for i in ins}
    out = {"ETH%": w.get(E, 0) * 100, "ZEC%": w.get(Z, 0) * 100, "BTC%": w.get(B, 0) * 100, "합%": round(sum(w.values()) * 100, 1)}
    with bt.lever(30, 0.04):
        for p, a, b in per:
            eq, t = rls.simulate_tranches(H, rls.TRANCHES, a, b, vol_target=0, size_k=lambda i, s: w[i] / 0.04)
            s = bt.stats(eq, t)
            out |= {f"{p} 수익": round((eq.iloc[-1] - 1) * 100 if p == "9개월" else s["cagr%"], 1), f"{p} MDD": round(s["mdd%"], 1),
                    f"{p} Sharpe": round(s["sharpe"], 2)}
            if p in ("9개월", "전체"):
                out["정지"] = bool(((1 - eq / eq.cummax()) >= rls.KILL_DD).any())
    return out


if __name__ == "__main__":
    j3 = [({E: e, Z: z, B: b}, P9) for e, z, b in itertools.product((0.03, 0.04, 0.05, 0.06), (0.02, 0.03, 0.04), (0, 0.02, 0.03, 0.04))]
    j2 = [({E: e, B: b}, PL) for e, b in itertools.product((0.04, 0.05, 0.06), (0.02, 0.03, 0.04))]
    with Pool(9) as p:
        r3 = pd.DataFrame(p.map(run, j3)); r2 = pd.DataFrame(p.map(run, j2))
    r3.to_csv(HERE / "weights.csv", index=False); r2.to_csv(HERE / "weights_be.csv", index=False)
    pd.set_option("display.width", 250)
    print("== BTC+ETH+ZEC 9개월 (수익 상위 15)"); print(r3.sort_values("9개월 수익", ascending=False).head(15).to_string(index=False))
    print("\n== Sharpe 상위 10"); print(r3.sort_values("9개월 Sharpe", ascending=False).head(10).to_string(index=False))
    print("\n== 기준 ETH4 ZEC4 BTC4"); print(r3[(r3["ETH%"] == 4) & (r3["ZEC%"] == 4) & (r3["BTC%"] == 4)].to_string(index=False))
    print("\n== BTC+ETH 2020~"); print(r2.sort_values("전체 Sharpe", ascending=False).to_string(index=False))
