"""단기 박스 매매: 직전 W 시간 폭(최고−최저) ≤ k × 일봉 ATR 이면 박스. 상한선 = 시간봉 고가의 p 분위, 하한선 = 저가의 (1−p) 분위
(극단이 아닌 '평균적' 경계). 상한선 닿으면 숏, 하한선 닿으면 롱 (지정가 maker). 손절 = 박스 극단 + 0.25 ATR 바깥,
레버리지 = 그 손절이 청산 안쪽에 들어오는 최대(≤30x), 목표 = 반대편 경계 (지정가), 최대 보유 W 시간. 손절 뒤 W/2 시간 쉼.
neutral=True 면 확정 레짐이 NEUTRAL 인 날만. 같은 봉 손절·목표는 손절 먼저 (보수적). 증거금 4%. → box_trade.csv"""
import sys, pathlib, itertools, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path[:0] = [str(HERE.parents[1]), str(HERE)]
import numpy as np, pandas as pd, rls, backtest as bt
from multiprocessing import Pool
MAKER, MMR = 0.0002, rls.SIM_MMR["coin"]

def run(h, W, k, p, neutral, margin=0.04, start=None, end=None, L_fix=None):
    """L_fix: 고정 레버리지 (손절이 청산 거리보다 멀면 청산 안쪽으로 당김). None = 거래마다 최대(≤30x)."""
    b = h.assign(t=pd.to_datetime(h.ts, unit="ms")).set_index("t")[["open", "high", "low", "close"]].astype(float)
    D = rls.prep(rls.offset_daily(h, 0, 1))
    day = b.index.floor("D") - pd.Timedelta(days=1)
    dd = D.reindex(day); atr, conf = dd.atr.to_numpy(), dd.conf.to_numpy()
    hi_w, lo_w = b.high.rolling(W).max().shift(1), b.low.rolling(W).min().shift(1)        # 직전 W 시간 (이 봉 제외)
    up, dn = b.high.rolling(W).quantile(p).shift(1), b.low.rolling(W).quantile(1 - p).shift(1)
    m = (b.index >= pd.Timestamp(start)) & (b.index <= pd.Timestamp(end))
    O, H, L_, C = (b[c].to_numpy() for c in ("open", "high", "low", "close"))
    HI, LO, UP, DN, T = hi_w.to_numpy(), lo_w.to_numpy(), up.to_numpy(), dn.to_numpy(), b.index
    cash, pos, cool, eqs, trades = 1.0, None, -1, {}, []
    def close(px, why, i, fee_rate):
        nonlocal cash, pos
        q = pos; pos = None
        pnl = q["side"] * q["qty"] * (px - q["entry"]) - fee_rate * q["qty"] * px
        cash += pnl
        trades.append({"entry_ts": T[q["i0"]], "exit_ts": T[i], "side": q["side"], "why": why, "L": q["L"], "pnl_pct": (pnl - q["fee"]) / q["eq0"]})
    for i in np.flatnonzero(m):
        if pos is None and i >= cool and not np.isnan(UP[i]) and not np.isnan(atr[i]) and (not neutral or conf[i] == 0):
            if (HI[i] - LO[i]) <= k * atr[i] and UP[i] > DN[i]:
                for side, lvl, ext in ((-1, UP[i], HI[i]), (1, DN[i], LO[i])):
                    if (H[i] >= lvl) if side < 0 else (L_[i] <= lvl):
                        entry = max(O[i], lvl) if side < 0 else min(O[i], lvl)
                        stop = ext + 0.25 * atr[i] if side < 0 else ext - 0.25 * atr[i]
                        dist = abs(stop / entry - 1)
                        if L_fix:
                            Lv = L_fix; cap = rls.liq_dist(Lv, MMR) - rls.LIQ_BUF
                            if dist > cap:
                                dist = cap; stop = entry * (1 - side * dist)
                        else:
                            Lv = int(min(30, np.floor(1 / (dist + MMR + rls.TAKER + rls.LIQ_BUF))))
                        if Lv < 1 or (side < 0 and stop <= entry) or (side > 0 and stop >= entry):
                            break
                        mg = cash * margin; qty = mg * Lv / entry; fee = MAKER * mg * Lv; cash -= fee
                        pos = {"side": side, "entry": entry, "qty": qty, "stop": stop, "tgt": DN[i] if side < 0 else UP[i],
                               "i0": i, "L": Lv, "fee": fee, "eq0": cash + fee}
                        break
        if pos is not None:
            if (H[i] >= pos["stop"]) if pos["side"] < 0 else (L_[i] <= pos["stop"]):
                close(pos["stop"] * (1 - pos["side"] * rls.SLIP), "sl", i, rls.TAKER); cool = i + W // 2
            elif i > pos["i0"] and ((L_[i] <= pos["tgt"]) if pos["side"] < 0 else (H[i] >= pos["tgt"])):
                close(pos["tgt"], "target", i, MAKER)
            elif i - pos["i0"] >= W:
                close(C[i] * (1 - pos["side"] * rls.SLIP), "time", i, rls.TAKER)
        if pos is not None:
            cash += -pos["side"] * rls.FUNDING_8H / 8 * pos["qty"] * C[i]
        eqs[T[i]] = cash + (pos["side"] * pos["qty"] * (C[i] - pos["entry"]) if pos else 0.0)
    eq = pd.Series(eqs); return eq.groupby(eq.index.normalize()).last(), pd.DataFrame(trades)

CFGS = list(itertools.product((24, 48, 72, 120), (1.0, 1.5), (0.8, 0.9), (True, False)))
def job(a):
    inst, (W, k, p, nt) = a
    h = bt.cached_hourly(inst); s0 = "2021-09-01" if inst[:3] == "SOL" else "2020-06-01"
    eq, tr = run(h, W, k, p, nt, start=s0, end="2026-10-16")
    out = {"inst": inst[:3], "W": W, "k": k, "p": p, "neutral": nt, "trades": len(tr),
           "win%": round((tr.pnl_pct > 0).mean() * 100) if len(tr) else 0, "avg%": round(tr.pnl_pct.mean() * 100, 3) if len(tr) else 0,
           "L_med": float(tr.L.median()) if len(tr) else 0, "why": tr.why.value_counts().to_dict() if len(tr) else {}}
    for tag, a0, b0 in (("all", s0, "2026-10-16"), ("is", s0, "2023-06-30"), ("oos", "2023-07-01", "2026-10-16"), ("y26", "2025-12-31", "2026-10-16")):
        e = eq[(eq.index >= a0) & (eq.index <= b0)]
        if len(e) < 30: continue
        e = e / e.iloc[0]; s = bt.stats(e)
        out |= {f"{tag}_sh": round(s["sharpe"], 2), f"{tag}_ret": round(s["cagr%"], 1) if tag != "y26" else round((e.iloc[-1] - 1) * 100, 1), f"{tag}_mdd": round(s["mdd%"], 1)}
    return out
if __name__ == "__main__":
    jobs = [(i, c) for i in ("ETH-USDT-SWAP", "BTC-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP") for c in CFGS]
    with Pool(9) as p: df = pd.DataFrame(p.map(job, jobs, chunksize=2))
    df.to_csv(HERE / "box_trade.csv", index=False)
    pd.set_option("display.width", 260); pd.set_option("display.max_rows", 200)
    g = df.groupby(["W", "k", "p", "neutral"]).agg(Sharpe=("all_sh", "mean"), 최저=("all_sh", "min"), IS=("is_sh", "mean"), OOS=("oos_sh", "mean"),
            연수익=("all_ret", "mean"), MDD=("all_mdd", "mean"), y26=("y26_ret", "mean"), 거래=("trades", "mean"), 승률=("win%", "mean"), L=("L_med", "mean")).round(2)
    print("== 4코인 평균 (박스 매매만, 증거금 4%)"); print(g.sort_values("IS", ascending=False).to_string())
    print("\n== ETH 상위"); print(df[df.inst == "ETH"].drop(columns="inst").sort_values("is_sh", ascending=False).head(8).to_string(index=False))
