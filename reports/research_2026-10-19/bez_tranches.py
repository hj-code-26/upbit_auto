"""BTC+ETH+ZEC 몫 구성 고르기 (운용 규칙 = 좁은 봉·과열 크기 조절 없음). 30x, 동시 한도 10%.
몫 {1, 2, 3, 3(04·12·20), 6} × 증거금 {4%, 3.33%} × 동적 크기 {켬, 끔}. ZEC 데이터 때문에 2026-01-20~10-18.
같은 몫·크기 설정을 BTC+ETH 2020-04~ 에도 돌려 짧은 구간 선택이 긴 구간에서도 괜찮은지 본다. → bez_tranches.csv"""
import pathlib, sys, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path.insert(0, str(HERE.parents[1]))
import pandas as pd, rls, backtest as bt
from multiprocessing import Pool

B, E, Z = "BTC-USDT-SWAP", "ETH-USDT-SWAP", "ZEC-USDT-SWAP"
TR = {"1몫 (00)": (0,), "2몫 (00·12)": (0, 12), "3몫 (00·08·16)": (0, 8, 16), "3몫 (04·12·20)": (4, 12, 20), "6몫": rls.TRANCHES}


def run(job):
    tr, m, vol = job; out = {"몫": tr, "증거금": m, "동적크기": vol}
    for tag, ins, a, b in (("BEZ 26", (B, E, Z), "2026-01-20", "2026-10-18"), ("BE 20~", (B, E), "2020-04-01", None),
                           ("BE 25~", (B, E), "2025-01-01", None)):
        H = {i: bt.cached_hourly(i) for i in ins}
        with bt.lever(30, m):
            eq, t = rls.simulate_tranches(H, TR[tr], a, b, vol_target=None if vol else 0)
        s = bt.stats(eq, t)
        out |= {f"{tag} 수익": round((eq.iloc[-1] / eq.iloc[0] - 1) * 100, 1) if tag == "BEZ 26" else round(s["cagr%"], 1),
                f"{tag} MDD": round(s["mdd%"], 1), f"{tag} Sharpe": round(s["sharpe"], 2)}
        if tag == "BEZ 26":
            out |= {"BEZ 거래": int(s["trades"]), "BEZ 최악월": round(s["worst_mo%"], 1)}
    return out


if __name__ == "__main__":
    jobs = [(t, m, v) for t in TR for m in (0.04, 0.0333) for v in (True, False)]
    with Pool(9) as p:
        df = pd.DataFrame(p.map(run, jobs))
    df.to_csv(HERE / "bez_tranches.csv", index=False)
    pd.set_option("display.width", 260)
    print(df.sort_values("BEZ 26 수익", ascending=False).to_string(index=False))
