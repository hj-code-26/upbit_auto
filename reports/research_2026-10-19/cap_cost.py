"""동시 증거금 한도(MAX_TOTAL, 지금 10%) 를 바꾸면 수익이 얼마나 달라지나 — 한도 때문에 잃는 것. 고정 크기 6몫 30x, 리플레이 한도는 몫(계좌)마다.
막힌 진입 수 = 몫별 simulate 일별 기록의 blocked 합. → cap_cost.csv"""
import pathlib, sys, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path.insert(0, str(HERE.parents[1]))
import pandas as pd, rls, backtest as bt
from multiprocessing import Pool

E, Z, B = "ETH-USDT-SWAP", "ZEC-USDT-SWAP", "BTC-USDT-SWAP"
P9 = [("9개월", "2026-01-20", "2026-10-18")]
PL = [("전체", "2020-04-01", None), ("20-22", "2020-04-01", "2022-12-31"), ("23-24", "2023-01-01", "2024-12-31"), ("25~", "2025-01-01", None)]
CASES = {"BEZ 4% ETH→ZEC→BTC": ((E, Z, B), 0.04, P9), "BEZ 4% BTC→ETH→ZEC": ((B, E, Z), 0.04, P9),
         "BE 4%": ((E, B), 0.04, PL), "BE 6%": ((E, B), 0.06, PL)}
CAPS = (0.08, 0.10, 0.12, 0.15, 1.0)


def blocked(H, a, b, cap):
    n = 0
    for off in rls.TRANCHES:
        X = {i: rls.offset_daily(h, off, 1) for i, h in H.items()}
        _, _, d = rls.simulate(X, {}, lambda _: set(), set(), pd.Timestamp(a) + pd.Timedelta(hours=off),
                               None if b is None else pd.Timestamp(b) + pd.Timedelta(hours=off), sleeves=("coin",), kill=1.0, vol_target=0, max_total=cap)
        n += int(d.blocked.sum())
    return n


def run(job):
    case, cap = job; ins, m, per = CASES[case]; H = {i: bt.cached_hourly(i) for i in ins}
    out = {"구성": case, "한도": cap}
    with bt.lever(30, m):
        for p, a, b in per:
            eq, t = rls.simulate_tranches(H, rls.TRANCHES, a, b, vol_target=0, max_total=cap); s = bt.stats(eq, t)
            out |= {f"{p} 수익": round((eq.iloc[-1] - 1) * 100 if p == "9개월" else s["cagr%"], 1), f"{p} MDD": round(s["mdd%"], 1), f"{p} Sharpe": round(s["sharpe"], 2)}
        out["막힌 진입"] = blocked(H, per[0][1], per[0][2], cap)
    return out


if __name__ == "__main__":
    with Pool(9) as p:
        df = pd.DataFrame(p.map(run, [(c, k) for c in CASES for k in CAPS]))
    df.to_csv(HERE / "cap_cost.csv", index=False)
    pd.set_option("display.width", 250)
    for c in CASES:
        print(f"\n== {c}"); print(df[df.구성 == c].drop(columns="구성").dropna(axis=1, how="all").to_string(index=False))
