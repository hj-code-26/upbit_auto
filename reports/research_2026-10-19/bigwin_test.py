"""bigwin.py 에서 IS·OOS 모두 같은 방향이던 조건을 크기 규칙으로 바꿔 리플레이 (진입은 그대로, size_k 만). 리플레이 전용.
조건: BTC 레짐 같은 방향(BTC 아닌 상품) · 새 신호(age ≤ 4) · 20일 돌파 · 롱 · 기울기 ≤ 8%.
구성: BTC+ETH 6몫 / ETH 3몫(서버) / SOL+XRP 3몫(규칙 고를 때 안 본 코인) / ETH+ZEC 3몫(2026-01~). IS ~2023-06 · OOS 2023-07~. → bigwin_test.csv"""
import pathlib, sys, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path.insert(0, str(HERE.parents[1]))
import numpy as np, pandas as pd
import backtest as bt, rls
from multiprocessing import Pool

B, E, S, X, Z = (f"{c}-USDT-SWAP" for c in ("BTC", "ETH", "SOL", "XRP", "ZEC"))
CFG = {"BTC+ETH 6몫": ((B, E), rls.TRANCHES, "2020-04-01"), "ETH 3몫": ((E,), (0, 8, 16), "2020-04-01"),
       "SOL+XRP 3몫": ((S, X), (0, 8, 16), "2021-09-01"), "ETH+ZEC 3몫": ((E, Z), (0, 8, 16), "2026-01-20")}
F = {}                                         # 상품별, 모든 몫 시각을 합친 신호 봉 특징 (몫마다 시각이 달라 겹치지 않음)


def feat_table():
    btc = {o: rls.prep(rls.offset_daily(bt.cached_hourly(B), o, 1)).conf for o in rls.TRANCHES}
    for i in (B, E, S, X, Z):
        rows = []
        for o in rls.TRANCHES:
            x = rls.prep(rls.offset_daily(bt.cached_hourly(i), o, 1)); s50 = x.close.rolling(rls.SMA).mean()
            run = (x.conf != x.conf.shift()).cumsum()
            rows.append(pd.DataFrame({"btc": btc[o].reindex(x.index), "age": x.groupby(run).cumcount() + 1,
                                      "hi20": x.close >= x.high.rolling(20).max().shift(), "lo20": x.close <= x.low.rolling(20).min().shift(),
                                      "slope": 100 * (s50 / s50.shift(20) - 1)}, index=x.index))
        F[i] = pd.concat(rows).sort_index()


def cond(name, i, sig):
    f, s = F[i].loc[sig.name], int(sig.conf)
    return {"btc": i == B or f.btc == s, "fresh": f.age <= 4, "brk": bool(f.hi20 if s > 0 else f.lo20), "long": s > 0,
            "calm": s * f.slope <= 8}[name]


RULES = {"현행": None}
for c in ("btc", "fresh", "long", "calm"):
    RULES[f"{c} 아니면 ×0.5"] = (c, 1.0, 0.5)
RULES["brk 면 ×1.5"] = ("brk", 1.5, 1.0)
RULES["btc 면 ×1.5 아니면 ×0.5"] = ("btc", 1.5, 0.5)
RULES["btc&fresh 아니면 ×0.5"] = ("btc&fresh", 1.0, 0.5)


def k_fn(rule):
    c, yes, no = rule
    def k(i, sig):
        ok = all(cond(n, i, sig) for n in c.split("&"))
        return yes if ok else no
    return k


def run(job):
    feat_table()
    cfg, name = job; ins, offs, s0 = CFG[cfg]; H = {i: bt.cached_hourly(i) for i in ins}
    rule = RULES[name]; kw = {"size_k": k_fn(rule)} if rule else {}
    out = {"구성": cfg, "규칙": name}
    per = [("전체", s0, None)] + ([("IS", s0, "2023-06-30"), ("OOS", "2023-07-01", None)] if s0 < "2023" else [])
    for p, a, b in per:
        eq, tr = rls.simulate_tranches(H, offs, a, b, max_total=0.15, **kw); s = bt.stats(eq, tr)
        out |= {f"{p} 연수익": round(s["cagr%"], 1), f"{p} MDD": round(s["mdd%"], 1), f"{p} Sharpe": round(s["sharpe"], 2)}
    return out


if __name__ == "__main__":
    with Pool(9) as pool:
        rows = pool.map(run, [(c, r) for c in CFG for r in RULES])
    df = pd.DataFrame(rows); df.to_csv(HERE / "bigwin_test.csv", index=False)
    pd.set_option("display.width", 250)
    for c in CFG:
        print(f"\n== {c}"); print(df[df.구성 == c].drop(columns="구성").to_string(index=False))
