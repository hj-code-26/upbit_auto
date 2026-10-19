"""두 크기 규칙을 합친 백테스트: 이전(좁은 봉 없음) / 좁은 봉 ×0.5 (운용 코드 반영분) / + 과열 ×0.5 (SMA50 20일 기울기 > 6·8%).
운용과 같은 동시 한도 10%. 구성: 서버 ETH 3몫 · BTC+ETH 6몫 · SOL+XRP 3몫(안 봄) · ETH+ZEC / BTC+ETH+ZEC 3몫(2026-01-20~). → combo.csv"""
import pathlib, sys, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path[:0] = [str(HERE.parents[1]), str(HERE)]
import pandas as pd, rls, backtest as bt
import bigwin_test as T
from multiprocessing import Pool

B, E, S, X, Z = T.B, T.E, T.S, T.X, T.Z
CFG = {"ETH 3몫 (서버)": ((E,), (0, 8, 16), "2020-04-01"), "BTC+ETH 6몫": ((B, E), rls.TRANCHES, "2020-04-01"),
       "SOL+XRP 3몫": ((S, X), (0, 8, 16), "2021-09-01"), "ETH+ZEC 3몫": ((E, Z), (0, 8, 16), "2026-01-20"),
       "BTC+ETH+ZEC 3몫": ((B, E, Z), (0, 8, 16), "2026-01-20")}
RULES = {"이전 (좁은 봉 없음)": (0, None), "좁은 봉 (반영분)": (None, None), "좁은 봉 + 과열 8%": (None, 8), "좁은 봉 + 과열 6%": (None, 6)}


def run(job):
    T.feat_table(); cfg, name = job; ins, offs, s0 = CFG[cfg]; nw, th = RULES[name]
    H = {i: bt.cached_hourly(i) for i in ins}
    kw = {"narrow_w": nw}
    if th:
        kw["size_k"] = lambda i, sig: 1.0 if int(sig.conf) * T.F[i].at[sig.name, "slope"] <= th else 0.5
    per = [("전체", s0, None)] + ([("20-22", s0, "2022-12-31"), ("23-24", "2023-01-01", "2024-12-31"), ("25~", "2025-01-01", None)] if s0 < "2023" else [])
    out = {"구성": cfg, "규칙": name}
    for p, a, b in per:
        eq, tr = rls.simulate_tranches(H, offs, a, b, **kw); s = bt.stats(eq, tr)
        out |= {f"{p} 연수익": round(s["cagr%"], 1), f"{p} MDD": round(s["mdd%"], 1), f"{p} Sharpe": round(s["sharpe"], 2)}
        if p == "전체":
            out |= {"기간수익": round((eq.iloc[-1] / eq.iloc[0] - 1) * 100, 1), "거래": int(s["trades"]), "최악월": round(s["worst_mo%"], 1)}
    return out


if __name__ == "__main__":
    with Pool(9) as p:
        df = pd.DataFrame(p.map(run, [(c, r) for c in CFG for r in RULES]))
    df.to_csv(HERE / "combo.csv", index=False)
    pd.set_option("display.width", 260)
    for c in CFG:
        print(f"\n== {c}"); print(df[df.구성 == c].drop(columns="구성").dropna(axis=1).to_string(index=False))
