"""몫 정리 순위: 동시 한도로 높은 순위 코인 진입이 막히면 낮은 순위 포지션을 정리하고 들어간다 (rls.simulate(preempt=)).
A = 무조건 가장 낮은 순위 정리, B = 본전 이동 안 된 것만 (큰 승리 보호). 고정 크기, 30x, 동시 한도 10%.
① BTC+ETH+ZEC 6몫 4% (순위 ETH>ZEC>BTC), 2026-01-20~ ② 한도가 실제로 묶이는 긴 구간: BTC+ETH 6몫 6% (12% > 10%), 순위 ETH>BTC. → preempt.csv"""
import pathlib, sys, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path.insert(0, str(HERE.parents[1]))
import pandas as pd, rls, backtest as bt
from multiprocessing import Pool

E, Z, B = "ETH-USDT-SWAP", "ZEC-USDT-SWAP", "BTC-USDT-SWAP"
CASES = {"BEZ 4%": ((E, Z, B), 0.04, [("9개월", "2026-01-20", "2026-10-18")]),
         "BE 6%": ((E, B), 0.06, [("전체", "2020-04-01", None), ("20-22", "2020-04-01", "2022-12-31"), ("23-24", "2023-01-01", "2024-12-31"), ("25~", "2025-01-01", None)])}
RULES = {"정리 없음": None, "A 무조건": False, "B 본전 전만": True}


def run(job):
    case, rule = job; ins, m, per = CASES[case]; H = {i: bt.cached_hourly(i) for i in ins}
    kw = {} if RULES[rule] is None else {"preempt": (list(ins), RULES[rule])}
    out = {"구성": case, "규칙": rule}
    for p, a, b in per:
        with bt.lever(30, m):
            eq, t = rls.simulate_tranches(H, rls.TRANCHES, a, b, vol_target=0, **kw)
        s = bt.stats(eq, t)
        out |= {f"{p} 수익": round((eq.iloc[-1] - 1) * 100 if p == "9개월" else s["cagr%"], 1), f"{p} MDD": round(s["mdd%"], 1), f"{p} Sharpe": round(s["sharpe"], 2)}
        if p in ("9개월", "전체"):
            pe = t[t.why == "preempt"]
            out |= {"정리 횟수": len(pe), "정리된 거래 평균%": round(pe.pnl_pct.mean() * 100, 2) if len(pe) else None}
    return out


if __name__ == "__main__":
    with Pool(6) as p:
        df = pd.DataFrame(p.map(run, [(c, r) for c in CASES for r in RULES]))
    df.to_csv(HERE / "preempt.csv", index=False)
    pd.set_option("display.width", 250)
    for c in CASES:
        print(f"\n== {c}"); print(df[df.구성 == c].drop(columns="구성").dropna(axis=1, how="all").to_string(index=False))
