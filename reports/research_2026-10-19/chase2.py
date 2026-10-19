"""연구: 추격 재진입 보완 — 흔들기(빠른 회복)와 횡보(느린 반복 손절)를 가르는 조건. 리플레이 전용 (1H, watch.run(chase=)).
가설: ① 진짜 흔들기는 손절가를 1~8시간 안에 되찾는다 ② 큰 승리 조건(새 추세·ADX 강함·과열 전)일 때만 ③ 추격은 작게(×0.5).
선택: BTC·ETH IS (2020-06~2023-06) 로만 고르고, OOS (2023-07~) 와 안 본 SOL·XRP 로 확인. 3몫 평균, 일손실·킬 없음. → chase2.csv"""
import pathlib, sys, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path[:0] = [str(HERE.parents[1]), str(HERE.parent / "research_2026-10-16")]
import pandas as pd, backtest as bt
import watch as W
from multiprocessing import Pool

COIN = {"ETH": "ETH-USDT-SWAP", "BTC": "BTC-USDT-SWAP", "SOL": "SOL-USDT-SWAP", "XRP": "XRP-USDT-SWAP"}
OFF = (0, 8, 16)
VAR = {"현행": None, "회복 24h": ("reclaim", 1, 24, {}),
       "회복 1h": ("reclaim", 1, 1, {}), "회복 4h": ("reclaim", 1, 4, {}), "회복 8h": ("reclaim", 1, 8, {}),
       "회복 4h ×0.5": ("reclaim", 1, 4, {"k": 0.5}), "회복 24h ×0.5": ("reclaim", 1, 24, {"k": 0.5}),
       "회복 4h 새추세(≤5일)": ("reclaim", 1, 4, {"age": 5}), "회복 4h ADX≥25": ("reclaim", 1, 4, {"adx": 25}),
       "회복 4h 과열 전(≤8%)": ("reclaim", 1, 4, {"slope": 8}), "회복 24h 새추세+ADX+과열전 ×0.5": ("reclaim", 1, 24, {"age": 5, "adx": 25, "slope": 8, "k": 0.5}),
       "회복 4h 새추세+ADX+과열전": ("reclaim", 1, 4, {"age": 5, "adx": 25, "slope": 8})}


def job(a):
    c, name = a; h = bt.cached_hourly(COIN[c]); S = W.signals(h); ch = VAR[name]
    s0 = "2021-09-01" if c == "SOL" else "2020-06-01"
    out = {"coin": c, "규칙": name}
    for tag, a0, b0 in (("전체", s0, "2026-10-16"), ("IS", s0, "2023-06-30"), ("OOS", "2023-07-01", "2026-10-16")):
        rs, trs = [], []
        for o in OFF:
            eq, tr = W.run(h, {o}, a0, b0, S=S, chase=ch, narrow=True)
            rs.append(eq.pct_change()); trs.append(tr)
        r = pd.concat(rs, axis=1).dropna().mean(axis=1); s = bt.stats((1 + r).cumprod()); tr = pd.concat(trs)
        out |= {f"{tag} 연수익": round(s["cagr%"], 1), f"{tag} MDD": round(s["mdd%"], 1), f"{tag} Sharpe": round(s["sharpe"], 2)}
        if tag == "전체":
            out |= {"거래": len(tr)}
    return out


if __name__ == "__main__":
    with Pool(9) as p:
        df = pd.DataFrame(p.map(job, [(c, v) for c in COIN for v in VAR]))
    df.to_csv(HERE / "chase2.csv", index=False)
    pd.set_option("display.width", 250)
    for c in COIN:
        print(f"\n== {c}"); print(df[df.coin == c].drop(columns="coin").to_string(index=False))
