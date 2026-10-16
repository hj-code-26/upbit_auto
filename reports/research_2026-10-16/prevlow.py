"""전일 저가 지정가 롱: 날 D(UTC 00) 에 전일 저가 L 에 지정가 매수 → 1H 봉으로 체결·청산 순서 판정. 1x 거래당 순수익.
비용: 진입 maker 0.02%, 청산 taker 0.05% + 슬리피지 0.02% (손절·종가 청산), 익절 지정가는 maker."""
import sys, warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))
import numpy as np, pandas as pd, rls, backtest as bt
MAKER, TAKER, SLIP = 0.0002, 0.0005, 0.0002
def trades(h, exit_rule, sl=None, hold=1):
    h = h.copy(); h["t"] = pd.to_datetime(h.ts, unit="ms"); h["day"] = h.t.dt.floor("D")
    g = {d: x.reset_index(drop=True) for d, x in h.groupby("day")}
    days = sorted(g); D = rls.offset_daily(h, 0, 1); x = rls.prep(D)
    out = []
    for i in range(1, len(days) - hold):
        d, p = days[i], days[i - 1]
        if p not in D.index or len(g[d]) < 24: continue
        L, Hp = D.at[p, "low"], D.at[p, "high"]
        b = pd.concat([g[days[i + k]] for k in range(hold)], ignore_index=True)
        hit = np.flatnonzero(b.low.values <= L)
        if not len(hit): continue
        j = hit[0]; e = min(b.open[j], L)
        stop = e * (1 - sl) if sl else None
        tp = Hp if exit_rule == "tp_prevhigh" else None
        px, why = b.close.iloc[-1], "close"
        for k in range(j, len(b)):
            if stop and b.low[k] <= stop:                       # 같은 봉이면 손절 먼저 (보수적)
                px, why = min(b.open[k], stop) if k > j else stop, "sl"; break
            if tp and k > j and b.high[k] >= tp:
                px, why = max(b.open[k], tp), "tp"; break
        cost = MAKER + (MAKER if why == "tp" else TAKER + SLIP)
        out.append({"day": d, "ret": px / e - 1 - cost, "why": why, "conf": x.conf.get(p, 0), "reg": x.reg.get(p, 0),
                    "gap": (D.at[p, "close"] - L) / L})
    return pd.DataFrame(out)
def summ(t, a, b, label):
    t = t[(t.day >= a) & (t.day <= b)]
    if not len(t): return None
    r = t.ret; yrs = (pd.Timestamp(b) - pd.Timestamp(a)).days / 365.25
    daily = r.groupby(t.day).sum().reindex(pd.date_range(a, b), fill_value=0)
    return {"rule": label, "n": len(t), "win%": round((r > 0).mean() * 100), "avg%": round(r.mean() * 100, 3),
            "sum%": round(r.sum() * 100), "cagr1x%": round(((1 + daily).prod() ** (1 / yrs) - 1) * 100, 1),
            "sharpe": round(daily.mean() / daily.std() * np.sqrt(365), 2) if daily.std() else 0,
            "worst%": round(r.min() * 100, 1), "mdd1x%": round((1 - (1 + daily).cumprod() / (1 + daily).cumprod().cummax()).max() * 100, 1)}
if __name__ == "__main__":
    pd.set_option("display.width", 250)
    coins = ["ETH-USDT-SWAP", "BTC-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP"]
    rules = [("close", None, 1), ("close", 0.02, 1), ("close", None, 3), ("tp_prevhigh", 0.03, 1), ("tp_prevhigh", 0.03, 3)]
    rows = []
    for c in coins:
        h = bt.cached_hourly(c)
        for er, sl, hold in rules:
            t = trades(h, er, sl, hold)
            lab = f"{er} sl{sl or '-'} hold{hold}d"
            for flt, tt in (("all", t), ("BULL확정", t[t.conf == 1]), ("BEAR아님", t[t.conf != -1])):
                for per, a, b in (("전체", "2020-04-01", "2026-10-12"), ("3년", "2023-10-16", "2026-10-12")):
                    s = summ(tt, a, b, lab)
                    if s: rows.append({"coin": c[:3], "filter": flt, "per": per, **s})
    df = pd.DataFrame(rows); df.to_csv("prevlow.csv", index=False)
    print(df[df.per == "전체"].to_string(index=False))
    print(df[df.per == "3년"].to_string(index=False))
