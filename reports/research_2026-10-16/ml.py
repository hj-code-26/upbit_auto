import sys, warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))
import pandas as pd, numpy as np, rls, backtest as bt
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
INSTS = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP"]
HZ = int(sys.argv[1]) if len(sys.argv) > 1 else 10
def feats(d):
    c = d.close; x = rls.prep(d); f = pd.DataFrame(index=d.index)
    for n in (1, 3, 5, 10, 20, 60): f[f"r{n}"] = np.log(c / c.shift(n))
    for n in (20, 50, 100, 200): f[f"d{n}"] = c / c.rolling(n).mean() - 1
    s50 = c.rolling(50).mean(); f["slope"] = s50 / s50.shift(20) - 1
    f["atr"] = x.atr / c; f["vol20"] = np.log(c).diff().rolling(20).std()
    dn = c.diff(); up = dn.clip(lower=0).ewm(alpha=1/14).mean(); lo = (-dn.clip(upper=0)).ewm(alpha=1/14).mean(); f["rsi"] = up / (up + lo)
    f["rng20"] = (c - d.low.rolling(20).min()) / (d.high.rolling(20).max() - d.low.rolling(20).min())
    f["conf"] = x.conf
    f["y"] = np.log(c.shift(-HZ) / c)      # forward HZ-day log return
    f["r_next"] = np.log(c.shift(-1) / c)
    return f
D = {i: rls.offset_daily(bt.cached_hourly(i), 0, 1) for i in INSTS}
F = {i: feats(d).dropna(subset=["d200", "slope", "atr", "r60"]) for i, d in D.items()}
X = [c for c in next(iter(F.values())).columns if c not in ("y", "r_next")]
def sharpe(r): r = r.dropna(); return r.mean() / r.std() * np.sqrt(365) if r.std() else 0
rows = []
preds = {i: pd.Series(np.nan, F[i].index) for i in INSTS}
for Y in range(2022, 2027):
    t0 = pd.Timestamp(f"{Y}-01-01")
    tr = pd.concat([f[(f.index < t0 - pd.Timedelta(days=HZ))] for f in F.values()]).dropna(subset=["y"])
    for name, m in (("gbm", HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=200, min_samples_leaf=100, l2_regularization=1.0)),):
        m.fit(tr[X], tr.y > 0)
        for i, f in F.items():
            te = f[(f.index >= t0) & (f.index < pd.Timestamp(f"{Y+1}-01-01"))]
            if len(te): preds[i].loc[te.index] = m.predict_proba(te[X])[:, 1]
cost = 0.0012  # round trip-ish per position change (taker+slip both sides /2 per change)
for i, f in F.items():
    p = preds[i].dropna(); g = f.loc[p.index]
    sig_reg = g.conf
    sig_ml = pd.Series(np.where(p > 0.55, 1, np.where(p < 0.45, -1, 0)), p.index)
    sig_mix = sig_reg.where(np.sign(sig_reg) == np.sign(p - 0.5), 0)
    for nm, s in (("regime", sig_reg), ("ml", sig_ml), ("regime&ml", sig_mix), ("hold", pd.Series(1, p.index))):
        r = s * g.r_next - cost * s.diff().abs().fillna(0)
        rows.append({"inst": i[:3], "sig": nm, "sharpe": round(sharpe(r), 2), "ann%": round(r.mean() * 365 * 100, 1), "expo%": round((s != 0).mean() * 100)})
    auc = __import__("sklearn.metrics", fromlist=["x"]).roc_auc_score(g.y.dropna() > 0, p.loc[g.y.dropna().index])
    rows.append({"inst": i[:3], "sig": "AUC", "sharpe": round(auc, 3)})
print(f"HZ={HZ}, walk-forward 2022-2026 (pooled 4 coins, retrain yearly), 1x close-to-close")
print(pd.DataFrame(rows).pivot(index="sig", columns="inst", values=["sharpe", "ann%", "expo%"]).to_string())
