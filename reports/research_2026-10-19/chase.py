"""연구: 손절 뒤 추격 재진입 — 그 몫의 최신 일봉 신호가 아직 같은 방향이면 다시 들어간다 (신호 없으면 안 들어감). 리플레이 전용.
즉시 = 다음 시간 시가, 회복 = 1H 종가가 손절가를 되찾은 다음 시간 시가. n 번까지 연속, W 시간 안. 본전 손절 뒤는 추격 없음.
1H 해상도 리플레이 = reports/research_2026-10-16/watch.run (현행 신호·손절 cap·일봉 종가 본전·DROP1·edge·동적 크기 + 좁은 봉).
몫 = 경계 시각 하나씩 따로 돌려 일수익 평균 (rls.simulate_tranches 와 같은 방식). 일손실·킬 없음 (A/B 비교용 같은 조건).
비교 대상: 같은 하네스의 현행 (be_r=None). → chase.csv"""
import pathlib, sys, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path[:0] = [str(HERE.parents[1]), str(HERE.parent / "research_2026-10-16")]
import pandas as pd, backtest as bt
import watch as W
from multiprocessing import Pool

COIN = {"ETH": "ETH-USDT-SWAP", "BTC": "BTC-USDT-SWAP", "SOL": "SOL-USDT-SWAP", "XRP": "XRP-USDT-SWAP"}
OFF = (0, 8, 16)
VAR = {"현행": None, "즉시 1번": ("now", 1, 48), "즉시 2번": ("now", 2, 48), "회복 1번 24h": ("reclaim", 1, 24), "회복 1번 72h": ("reclaim", 1, 72), "회복 2번 72h": ("reclaim", 2, 72)}


def job(a):
    c, name = a; h = bt.cached_hourly(COIN[c]); S = W.signals(h); ch = VAR[name]
    s0 = "2021-09-01" if c == "SOL" else "2020-06-01"
    out = {"coin": c, "규칙": name}
    for tag, a0, b0 in (("전체", s0, "2026-10-16"), ("IS", s0, "2023-06-30"), ("OOS", "2023-07-01", "2026-10-16")):
        rs, trs = [], []
        for o in OFF:
            eq, tr = W.run(h, {o}, a0, b0, S=S, chase=ch, narrow=True)
            rs.append(eq.pct_change()); trs.append(tr)
        r = pd.concat(rs, axis=1).dropna().mean(axis=1); eq = (1 + r).cumprod(); s = bt.stats(eq); tr = pd.concat(trs)
        out |= {f"{tag} 연수익": round(s["cagr%"], 1), f"{tag} MDD": round(s["mdd%"], 1), f"{tag} Sharpe": round(s["sharpe"], 2)}
        if tag == "전체":
            out |= {"거래": len(tr), "승률": round((tr.pnl_pct > 0).mean() * 100), "평균%": round(tr.pnl_pct.mean() * 100, 3)}
    return out


if __name__ == "__main__":
    with Pool(9) as p:
        df = pd.DataFrame(p.map(job, [(c, v) for c in COIN for v in VAR]))
    df.to_csv(HERE / "chase.csv", index=False)
    pd.set_option("display.width", 250)
    for c in COIN:
        print(f"\n== {c}"); print(df[df.coin == c].drop(columns="coin").to_string(index=False))
