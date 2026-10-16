import sys, warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))
import pandas as pd, numpy as np, rls, backtest as bt
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40)
H = {i: bt.cached_hourly(i) for i in rls.COINS}
cfgs = {"cur": {}, "risk1.5": {"risk": 0.015, "max_total": 0.6}}
for nm, kw in cfgs.items():
    eq, tr = rls.simulate_tranches(H, rls.TRANCHES, "2020-04-01", "2026-10-15", **kw)
    tr = tr.copy(); tr["yr"] = tr.exit_ts.dt.year; tr["c"] = tr.pnl_pct / 3 * 100   # approx account contribution %
    tr["d1"] = (tr.why == "sl") & (tr.exit_ts.dt.normalize() == tr.entry_ts.dt.normalize())
    ye = eq.groupby(eq.index.year).last(); ret = (ye / ye.shift(fill_value=1.0) - 1) * 100
    mdd = eq.groupby(eq.index.year).apply(lambda e: (1 - e / e.cummax()).max() * 100)
    rows = []
    for y, g in tr.groupby("yr"):
        w = g.pnl > 0
        rows.append({"yr": y, "ret%": round(ret[y], 1), "mdd%": round(mdd[y], 1), "n": len(g), "win%": round(w.mean() * 100),
                     "pf": round(g.pnl[w].sum() / -g.pnl[~w].sum(), 2) if (~w).any() else np.inf,
                     "long%": round(g.c[g.side > 0].sum(), 1), "short%": round(g.c[g.side < 0].sum(), 1),
                     "btc%": round(g.c[g.inst.str[:3] == "BTC"].sum(), 1), "eth%": round(g.c[g.inst.str[:3] == "ETH"].sum(), 1),
                     "top3%": round(g.c.nlargest(3).sum(), 1), "loss_sum%": round(g.c[~w].sum(), 1),
                     "d1_sl": int(g.d1.sum()), "liq": int((g.why == "liq").sum()),
                     "why": g.why.value_counts().to_dict()})
    print(f"===== {nm} ====="); print(pd.DataFrame(rows).to_string(index=False))
    if nm == "cur":
        best = tr.nlargest(12, "c")[["inst", "side", "entry_ts", "exit_ts", "entry", "exit", "why", "c", "tranche"]]
        print("TOP trades"); print(best.round(2).to_string(index=False))
        tr.to_csv("tr_cur.csv", index=False)
# market context
D = {i: rls.prep(rls.offset_daily(H[i], 0, 1)) for i in rls.COINS}
rows = []
for y in range(2020, 2027):
    r = {"yr": y}
    for i, x in D.items():
        s = x[(x.index >= max(pd.Timestamp(f"{y}-01-01"), pd.Timestamp("2020-04-01"))) & (x.index <= pd.Timestamp(f"{y}-12-31"))]
        a = i[:3]
        r[a + "_hold%"] = round((s.close.iloc[-1] / s.open.iloc[0] - 1) * 100)
        r[a + "_bull%"] = round((s.conf == 1).mean() * 100); r[a + "_bear%"] = round((s.conf == -1).mean() * 100)
        r[a + "_atr%"] = round((s.atr / s.close).median() * 100, 1)
        r[a + "_flips"] = int((s.conf != s.conf.shift()).sum())
        r[a + "_maxDD%"] = round((1 - s.close / s.close.cummax()).max() * 100)
    rows.append(r)
print("MARKET"); print(pd.DataFrame(rows).to_string(index=False))
