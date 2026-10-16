import pandas as pd, numpy as np
pd.set_option("display.width", 250); pd.set_option("display.max_rows", 100)
d = pd.read_csv("search.csv").fillna({"amax": 0, "trail": 0})
k = ["sma", "slope", "conf", "be", "drop", "amax", "trail"]
cur = d[(d.sma == 50) & (d.slope == 20) & (d.conf == 3) & (d.be == 1) & (d["drop"] == 1) & (d.amax == 0.08) & (d.trail == 0)].iloc[0]
c = k + ["is_cagr", "is_mdd", "is_sh", "is_n", "oos_cagr", "oos_mdd", "oos_sh", "sx_cagr", "sx_sh"]
print("CURRENT", cur[c].to_dict())
print("IS-vs-OOS Spearman:", round(d.is_sh.corr(d.oos_sh, method="spearman"), 2), " IS-vs-SX:", round(d.is_sh.corr(d.sx_sh, method="spearman"), 2))
print("share beating current Sharpe: IS", round((d.is_sh > cur.is_sh).mean() * 100, 1), "% OOS", round((d.oos_sh > cur.oos_sh).mean() * 100, 1),
      "% SX", round((d.sx_sh > cur.sx_sh).mean() * 100, 1), "% all three", round(((d.is_sh > cur.is_sh) & (d.oos_sh > cur.oos_sh) & (d.sx_sh > cur.sx_sh)).mean() * 100, 1), "%")
top = d.sort_values("is_sh", ascending=False).head(20)
print("TOP20 by IS Sharpe (selection uses IS only)"); print(top[c].to_string(index=False))
print("TOP20 median OOS sh", top.oos_sh.median(), "SX sh", top.sx_sh.median())
for p in k:
    print(d.groupby(p)[["is_sh", "oos_sh", "sx_sh"]].median().round(2).T.to_string()); print()
both = d[(d.is_sh > cur.is_sh) & (d.oos_sh > cur.oos_sh) & (d.sx_sh > cur.sx_sh)]
print("BEAT CURRENT ON ALL THREE"); print(both.sort_values("is_sh", ascending=False)[c].head(25).to_string(index=False))
