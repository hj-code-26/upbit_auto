"""연구: 거래소 레버리지만 낮추고 손절 폭·노출은 지금과 같게 (또는 손절 폭만 바꿔서). 리플레이 전용.
지금 30x 의 손절 = min(1.5 ATR, 청산 거리 − 1%p ≈ 1.88%) 은 레버리지가 정한 cap. 낮은 레버리지에서도 같은 cap 을 '규칙' 으로 두면
명목(증거금 × L)이 같을 때 손익은 같고, 청산가만 훨씬 멀어진다 (갭·급락 꼬리 위험 감소). cap 을 바꾸면 손절 폭 자체를 바꾸는 실험.
명목 = 4% × 30 = 종목당 몫 자본의 120% (동적 크기 배수 그대로). 증거금 = 120% / L, 동시 한도 = 증거금이 막지 않게. → low_lever_cap.csv"""
import pathlib, sys, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path.insert(0, str(HERE.parents[1]))
import pandas as pd, rls, backtest as bt
from multiprocessing import Pool
from low_lever import CFG, P

ORIG = rls.stop_dist


def run(job):
    cfg, L, cap = job
    ins, offs = CFG[cfg]; H = {i: bt.cached_hourly(i) for i in ins}
    rls.stop_dist = lambda a, c, lv, m: min(ORIG(a, c, lv, m), cap)          # 규칙 cap (청산 cap 보다 작을 때만 묶임)
    out = {"구성": cfg, "L": L, "cap%": round(cap * 100, 2)}
    with bt.lever(L, 1.2 / L):
        for p, a, b in P:
            eq, tr = rls.simulate_tranches(H, offs, a, b, max_total=max(0.10, 2.5 * 1.2 / L * 2)); s = bt.stats(eq, tr)
            out |= {f"{p} 연수익": round(s["cagr%"], 1), f"{p} MDD": round(s["mdd%"], 1), f"{p} Sharpe": round(s["sharpe"], 2)}
            if p == "전체":
                out |= {"승률": round(s["win"], 0), "거래": int(s["trades"]), "강제청산": int((tr.why == "liq").sum())}
    return out


if __name__ == "__main__":
    c30 = rls.liq_dist(30, rls.SIM_MMR["coin"]) - rls.LIQ_BUF
    jobs = [(c, L, c30) for c in CFG for L in (30, 20, 10, 5)]
    jobs += [(c, 10, cap) for c in CFG for cap in (0.015, 0.0225, 0.025, 0.03, 0.04)]
    with Pool(9) as pool:
        rows = pool.map(run, jobs)
    df = pd.DataFrame(rows); df.to_csv(HERE / "low_lever_cap.csv", index=False)
    pd.set_option("display.width", 250)
    print(df[["구성", "L", "cap%", "전체 연수익", "전체 MDD", "전체 Sharpe", "20-22 Sharpe", "23-24 Sharpe", "25~ Sharpe", "승률", "거래", "강제청산"]].to_string(index=False))
