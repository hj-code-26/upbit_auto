"""연구: 레버리지를 30x 보다 크게 낮추면 어떻게 세팅해야 30x 보다 나은가. 리플레이 전용.
낮은 레버리지 = 청산 거리가 멀어져 손절이 1.88% cap 대신 진짜 1.5 ATR 에 놓인다 (rls.stop_dist). 크기는 위험 기준
(rls.simulate(risk=r): 명목 = 잔고 × r / 손절 거리 → 손절에 닿으면 몫 잔고 r 손실). 동시 한도 = 증거금이 막지 않게 넉넉히.
vol: 동적 크기(ATR 배수)를 같이 쓸지. 좁은 봉 규칙은 그대로 (기본). → low_lever.csv"""
import pathlib, sys, itertools, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path.insert(0, str(HERE.parents[1]))
import pandas as pd, rls, backtest as bt
from multiprocessing import Pool

B, E = "BTC-USDT-SWAP", "ETH-USDT-SWAP"
CFG = {"ETH 3몫": ((E,), (0, 8, 16)), "BTC+ETH 6몫": ((B, E), rls.TRANCHES)}
P = [("전체", "2020-04-01", None), ("20-22", "2020-04-01", "2022-12-31"), ("23-24", "2023-01-01", "2024-12-31"), ("25~", "2025-01-01", None)]


def run(job):
    cfg, L, r, vol = job
    ins, offs = CFG[cfg]; H = {i: bt.cached_hourly(i) for i in ins}
    out = {"구성": cfg, "L": L, "risk%": r * 100 if r else None, "vol": vol}
    with bt.lever(L, 0.04):
        for p, a, b in P:
            kw = {"vol_target": None if vol else 0}
            if r:
                kw |= {"risk": r, "max_total": 1.0}
            eq, tr = rls.simulate_tranches(H, offs, a, b, **kw); s = bt.stats(eq, tr)
            out |= {f"{p} 연수익": round(s["cagr%"], 1), f"{p} MDD": round(s["mdd%"], 1), f"{p} Sharpe": round(s["sharpe"], 2)}
            if p == "전체":
                out |= {"승률": round(s["win"], 0), "거래": int(s["trades"]), "정지": bool(((1 - eq / eq.cummax()) >= rls.KILL_DD).any())}
    return out


if __name__ == "__main__":
    jobs = [(c, 30, None, True) for c in CFG]                                   # 현행 (30x, 4%, 동적 크기)
    jobs += [(c, L, r, v) for c in CFG for L in (5, 10, 15) for r in (0.015, 0.02, 0.03, 0.04) for v in (False, True)]
    with Pool(9) as pool:
        rows = pool.map(run, jobs)
    df = pd.DataFrame(rows); df.to_csv(HERE / "low_lever.csv", index=False)
    pd.set_option("display.width", 250)
    print(df[["구성", "L", "risk%", "vol", "전체 연수익", "전체 MDD", "전체 Sharpe", "20-22 Sharpe", "23-24 Sharpe", "25~ Sharpe", "25~ 연수익", "승률", "거래", "정지"]].to_string(index=False))
