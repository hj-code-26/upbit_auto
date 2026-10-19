"""채널(기울어진 박스) 매매: 매일 직전 N일 일봉 고가·저가에 각각 직선 회귀 → 상한선·하한선을 오늘로 연장.
채널 폭 ≥ 1 ATR 일 때만. 상한선 닿으면 숏 / 하한선 닿으면 롱 (지정가 maker). mode="both" 양방향, "trend" 채널 기울기 방향만
(상승 채널은 하한선 롱만, 하락 채널은 상한선 숏만). 손절 = 선 바깥 buf×ATR (레버리지 = 그 손절이 청산 안쪽인 최대, ≤30x),
목표 = 중앙선 또는 반대 선, 최대 5일, 손절 뒤 그날 쉼. 같은 봉 손절·목표는 손절 먼저. 증거금 4%. → channel_trade.csv"""
import sys, pathlib, itertools, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path[:0] = [str(HERE.parents[1]), str(HERE)]
import numpy as np, pandas as pd, rls, backtest as bt
from multiprocessing import Pool
MAKER, MMR = 0.0002, rls.SIM_MMR["coin"]

def lines(d, N):
    """직전 N일(오늘 제외) 회귀선을 오늘(x=N)로 연장 → (상한, 하한, 기울기/일)."""
    x = np.arange(N); xm = x.mean(); sxx = ((x - xm) ** 2).sum()
    def proj(s):
        a = np.lib.stride_tricks.sliding_window_view(s.to_numpy(), N)
        b = ((a - a.mean(axis=1, keepdims=True)) * (x - xm)).sum(axis=1) / sxx
        v = a.mean(axis=1) + b * (N - xm)
        out = pd.Series(np.nan, s.index); out.iloc[N:] = v[:-1]; sl = pd.Series(np.nan, s.index); sl.iloc[N:] = b[:-1]
        return out, sl
    up, bu = proj(d.high); lo, bl = proj(d.low)
    return up, lo, (bu + bl) / 2

def run(h, N, mode, target, buf, margin=0.04, start=None, end=None, max_d=5):
    d = rls.offset_daily(h, 0, 1); atr = rls.atr(d)
    up, lo, slope = lines(d, N)
    b = h.assign(t=pd.to_datetime(h.ts, unit="ms")).set_index("t")[["open", "high", "low", "close"]].astype(float)
    b = b[(b.index >= pd.Timestamp(start)) & (b.index <= pd.Timestamp(end))]
    day = b.index.floor("D")
    U, Lo, S, A = (s.reindex(day).to_numpy() for s in (up, lo, slope, atr.shift(1)))
    O, H, L_, C, T = b.open.to_numpy(), b.high.to_numpy(), b.low.to_numpy(), b.close.to_numpy(), b.index
    cash, pos, cool, eqs, trades = 1.0, None, None, {}, []
    def close(px, why, i, fr):
        nonlocal cash, pos
        q = pos; pos = None
        pnl = q["side"] * q["qty"] * (px - q["entry"]) - fr * q["qty"] * px
        cash += pnl
        trades.append({"entry_ts": T[q["i0"]], "exit_ts": T[i], "side": q["side"], "why": why, "L": q["L"], "pnl_pct": (pnl - q["fee"]) / q["eq0"]})
    for i in range(len(b)):
        u, l, a, s = U[i], Lo[i], A[i], S[i]
        if pos is None and not np.isnan(u) and not np.isnan(a) and u - l >= a and (cool is None or T[i] >= cool):
            sides = ((-1, u), (1, l)) if mode == "both" else (((1, l),) if s > 0 else ((-1, u),))
            for side, lvl in sides:
                if (H[i] >= lvl) if side < 0 else (L_[i] <= lvl):
                    entry = max(O[i], lvl) if side < 0 else min(O[i], lvl)
                    stop = lvl + buf * a if side < 0 else lvl - buf * a
                    dist = abs(stop / entry - 1)
                    if (stop - entry) * side >= 0:
                        break
                    Lv = int(min(30, np.floor(1 / (dist + MMR + rls.TAKER + rls.LIQ_BUF))))
                    if Lv < 1:
                        break
                    mg = cash * margin; qty = mg * Lv / entry; fee = MAKER * mg * Lv; cash -= fee
                    tgt = (u + l) / 2 if target == "mid" else (l if side < 0 else u)
                    pos = {"side": side, "entry": entry, "qty": qty, "stop": stop, "tgt": tgt, "i0": i, "L": Lv, "fee": fee, "eq0": cash + fee}
                    break
        if pos is not None:
            if (H[i] >= pos["stop"]) if pos["side"] < 0 else (L_[i] <= pos["stop"]):
                close(pos["stop"] * (1 - pos["side"] * rls.SLIP), "sl", i, rls.TAKER); cool = T[i].floor("D") + pd.Timedelta(days=1)
            elif i > pos["i0"] and ((L_[i] <= pos["tgt"]) if pos["side"] < 0 else (H[i] >= pos["tgt"])):
                close(pos["tgt"], "target", i, MAKER)
            elif i - pos["i0"] >= 24 * max_d:
                close(C[i] * (1 - pos["side"] * rls.SLIP), "time", i, rls.TAKER)
        if pos is not None:
            cash += -pos["side"] * rls.FUNDING_8H / 8 * pos["qty"] * C[i]
        eqs[T[i]] = cash + (pos["side"] * pos["qty"] * (C[i] - pos["entry"]) if pos else 0.0)
    eq = pd.Series(eqs); return eq.groupby(eq.index.normalize()).last(), pd.DataFrame(trades)

CFGS = list(itertools.product((10, 20, 30), ("both", "trend"), ("mid", "opp"), (0.25, 0.5)))
def job(a):
    inst, (N, mode, tg, buf) = a
    h = bt.cached_hourly(inst); s0 = "2021-09-01" if inst[:3] == "SOL" else "2020-06-01"
    eq, tr = run(h, N, mode, tg, buf, start=s0, end="2026-10-16")
    w = tr.pnl_pct > 0
    out = {"inst": inst[:3], "N": N, "mode": mode, "target": tg, "buf": buf, "trades": len(tr), "win%": round(w.mean() * 100, 1) if len(tr) else 0,
           "avg_win%": round(tr.pnl_pct[w].mean() * 100, 3) if w.any() else 0, "avg_loss%": round(tr.pnl_pct[~w].mean() * 100, 3) if (~w).any() else 0,
           "avg%": round(tr.pnl_pct.mean() * 100, 3) if len(tr) else 0, "L_med": float(tr.L.median()) if len(tr) else 0}
    for tag, a0, b0 in (("all", s0, "2026-10-16"), ("is", s0, "2023-06-30"), ("oos", "2023-07-01", "2026-10-16"), ("y26", "2025-12-31", "2026-10-16")):
        e = eq[(eq.index >= a0) & (eq.index <= b0)]; e = e / e.iloc[0]; s = bt.stats(e)
        t2 = tr[(tr.entry_ts >= a0) & (tr.entry_ts <= b0)]
        out |= {f"{tag}_sh": round(s["sharpe"], 2), f"{tag}_ret": round(s["cagr%"], 1) if tag != "y26" else round((e.iloc[-1] - 1) * 100, 1),
                f"{tag}_mdd": round(s["mdd%"], 1), f"{tag}_win%": round((t2.pnl_pct > 0).mean() * 100, 1) if len(t2) else 0}
    return out
if __name__ == "__main__":
    jobs = [(i, c) for i in ("ETH-USDT-SWAP", "BTC-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP") for c in CFGS]
    with Pool(9) as p: df = pd.DataFrame(p.map(job, jobs, chunksize=2))
    df.to_csv(HERE / "channel_trade.csv", index=False)
    pd.set_option("display.width", 280); pd.set_option("display.max_rows", 200)
    g = df.groupby(["N", "mode", "target", "buf"]).agg(Sharpe=("all_sh", "mean"), 최저=("all_sh", "min"), IS=("is_sh", "mean"), OOS=("oos_sh", "mean"),
            연수익=("all_ret", "mean"), MDD=("all_mdd", "mean"), 승률=("win%", "mean"), 평균이익=("avg_win%", "mean"), 평균손실=("avg_loss%", "mean"),
            거래당=("avg%", "mean"), 거래=("trades", "mean"), y26=("y26_ret", "mean")).round(2)
    print("두 기간 평균 모두 플러스:", int(((g.IS > 0) & (g.OOS > 0)).sum()), "/", len(g))
    print(g.sort_values("IS", ascending=False).to_string())
    print("\n== ETH"); print(df[df.inst == "ETH"].drop(columns="inst").sort_values("is_sh", ascending=False).head(8).to_string(index=False))
