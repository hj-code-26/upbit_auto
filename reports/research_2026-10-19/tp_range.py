"""연구: 몫끼리 신호가 엇갈릴 때 익절 — 보유 몫이 이익(≥ kR)인데 다른 몫 경계는 같은 방향 신호가 n 개 이하(다음 몫이 못 들어감 = 횡보 의심)면
자기 경계 종가 판정 때 다음 시가에 익절. 1H 리플레이 watch.run(tp_range=), 3몫(00·08·16) 평균, 일손실·킬 없음. → tp_range.csv"""
import pathlib, sys, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path[:0] = [str(HERE.parents[1]), str(HERE.parent / "research_2026-10-16")]
import pandas as pd, backtest as bt
import watch as W
from multiprocessing import Pool

COIN = {"ETH": "ETH-USDT-SWAP", "BTC": "BTC-USDT-SWAP", "SOL": "SOL-USDT-SWAP", "XRP": "XRP-USDT-SWAP"}
OFF = (0, 8, 16)
VAR = {"현행": None, "이익>0 · 같은 쪽 0개": (0.0, 0, OFF), "이익>0 · 같은 쪽 ≤1개": (0.0, 1, OFF), "≥1R · 0개": (1.0, 0, OFF),
       "≥1R · ≤1개": (1.0, 1, OFF), "≥2R · 0개": (2.0, 0, OFF), "≥2R · ≤1개": (2.0, 1, OFF), "≥4R · 0개": (4.0, 0, OFF)}


def job(a):
    c, name = a; h = bt.cached_hourly(COIN[c]); S = W.signals(h)
    s0 = "2021-09-01" if c == "SOL" else "2020-06-01"
    out = {"coin": c, "규칙": name}
    for tag, a0, b0 in (("전체", s0, "2026-10-16"), ("IS", s0, "2023-06-30"), ("OOS", "2023-07-01", "2026-10-16")):
        rs, trs = [], []
        for o in OFF:
            eq, tr = W.run(h, {o}, a0, b0, S=S, tp_range=VAR[name])
            rs.append(eq.pct_change()); trs.append(tr)
        r = pd.concat(rs, axis=1).dropna().mean(axis=1); s = bt.stats((1 + r).cumprod()); tr = pd.concat(trs)
        out |= {f"{tag} 연수익": round(s["cagr%"], 1), f"{tag} MDD": round(s["mdd%"], 1), f"{tag} Sharpe": round(s["sharpe"], 2)}
        if tag == "전체":
            out |= {"거래": len(tr), "익절": int((tr.why == "tp_range").sum())}
    return out


if __name__ == "__main__":
    with Pool(9) as p:
        df = pd.DataFrame(p.map(job, [(c, v) for c in COIN for v in VAR]))
    df.to_csv(HERE / "tp_range.csv", index=False)
    pd.set_option("display.width", 250)
    for c in COIN:
        print(f"\n== {c}"); print(df[df.coin == c].drop(columns="coin").to_string(index=False))
