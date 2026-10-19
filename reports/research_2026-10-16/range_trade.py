"""횡보장 전고점·전저점 매매: 확정 레짐(지금 코드 conf, ADX 보강 포함)이 NEUTRAL 인 날에만, 직전 N일 고점(상한선)에 닿으면 숏,
N일 저점(하한선)에 닿으면 롱 (지정가 = maker). 손절 = 진입가 ± stop×ATR (레버리지 L 의 청산 안쪽으로 cap), 목표 = 범위 중간 또는 반대편,
최대 보유 max_d 일, 레짐이 NEUTRAL 을 벗어나면 다음 시가 청산. 1H 봉, 같은 봉 손절·목표는 손절 먼저 (보수적). 손절 뒤 그날은 재진입 없음.
증거금 4%, 한 포지션. → range_trade.csv"""
import sys, pathlib, itertools, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path[:0] = [str(HERE.parents[1]), str(HERE)]
import numpy as np, pandas as pd, rls, backtest as bt
from multiprocessing import Pool
MAKER = 0.0002

def run(h, N, stop_atr, L, target, max_d=5, margin=0.04, start=None, end=None):
    D = rls.prep(rls.offset_daily(h, 0, 1))
    D["hi"], D["lo"] = D.high.rolling(N).max(), D.low.rolling(N).min()
    b = h.assign(t=pd.to_datetime(h.ts, unit="ms")).set_index("t")[["open", "high", "low", "close"]].astype(float)
    b = b[(b.index >= pd.Timestamp(start)) & (b.index <= pd.Timestamp(end))]
    day = b.index.floor("D") - pd.Timedelta(days=1)                  # 그 시각에 확정된 마지막 일봉
    X = D.reindex(day)[["conf", "hi", "lo", "atr", "close"]].set_index(b.index)
    mmr = rls.SIM_MMR["coin"]; cap = rls.liq_dist(L, mmr) - rls.LIQ_BUF
    cash, pos, cool, eqs, trades = 1.0, None, None, {}, []
    def close(px, why, t, fee_rate):
        nonlocal cash, pos
        p, pos = pos, None
        pnl = p["side"] * p["qty"] * (px - p["entry"]) - fee_rate * p["qty"] * px
        cash += pnl
        trades.append({"entry_ts": p["t0"], "exit_ts": t, "side": p["side"], "why": why, "pnl_pct": (pnl - p["fee"]) / p["eq0"]})
    for (t, r), x in zip(b.iterrows(), X.itertuples(index=False)):
        if pos and x.conf != 0:                                       # 레짐이 횡보를 벗어남 → 시가 청산
            close(r.open * (1 - pos["side"] * rls.SLIP), "regime", t, rls.TAKER)
        if pos is None and x.conf == 0 and not np.isnan(x.hi) and not np.isnan(x.atr) and (cool is None or t >= cool):
            for side, lvl in ((-1, x.hi), (1, x.lo)):
                if (r.high >= lvl) if side < 0 else (r.low <= lvl):
                    entry = max(r.open, lvl) if side < 0 else min(r.open, lvl)
                    dist = min(stop_atr * x.atr / x.close, cap)
                    m = cash * margin; qty = m * L / entry; fee = MAKER * m * L; cash -= fee
                    tgt = (x.hi + x.lo) / 2 if target == "mid" else (x.lo if side < 0 else x.hi)
                    pos = {"side": side, "entry": entry, "qty": qty, "stop": entry * (1 - side * dist), "tgt": tgt, "t0": t,
                           "fee": fee, "eq0": cash + fee}
                    break
        if pos:
            if (r.high >= pos["stop"]) if pos["side"] < 0 else (r.low <= pos["stop"]):
                close(pos["stop"] * (1 - pos["side"] * rls.SLIP), "sl", t, rls.TAKER); cool = t.floor("D") + pd.Timedelta(days=1)
            elif t > pos["t0"] and ((r.low <= pos["tgt"]) if pos["side"] < 0 else (r.high >= pos["tgt"])):
                close(pos["tgt"], "target", t, MAKER)
            elif t - pos["t0"] >= pd.Timedelta(days=max_d):
                close(r.close * (1 - pos["side"] * rls.SLIP), "time", t, rls.TAKER)
        if pos:
            f = -pos["side"] * rls.FUNDING_8H / 8 * pos["qty"] * r.close; cash += f
        eqs[t] = cash + (pos["side"] * pos["qty"] * (r.close - pos["entry"]) if pos else 0.0)
    eq = pd.Series(eqs); return eq.groupby(eq.index.normalize()).last(), pd.DataFrame(trades)

CFGS = list(itertools.product((10, 20, 30), (0.5, 1.0), (10, 30), ("mid", "opp")))
def job(a):
    inst, (N, st, L, tg) = a
    h = bt.cached_hourly(inst); s0 = "2021-09-01" if inst[:3] == "SOL" else "2020-06-01"
    eq, tr = run(h, N, st, L, tg, start=s0, end="2026-10-16")
    out = {"inst": inst[:3], "N": N, "stop_atr": st, "L": L, "target": tg, "trades": len(tr),
           "win%": round((tr.pnl_pct > 0).mean() * 100) if len(tr) else 0, "avg%": round(tr.pnl_pct.mean() * 100, 3) if len(tr) else 0,
           "why": tr.why.value_counts().to_dict() if len(tr) else {}}
    for tag, a0, b0 in (("all", s0, "2026-10-16"), ("is", s0, "2023-06-30"), ("oos", "2023-07-01", "2026-10-16"), ("y26", "2025-12-31", "2026-10-16")):
        e = eq[(eq.index >= a0) & (eq.index <= b0)]; e = e / e.iloc[0]; s = bt.stats(e)
        out |= {f"{tag}_sh": round(s["sharpe"], 2), f"{tag}_ret": round(s["cagr%"], 1) if tag != "y26" else round((e.iloc[-1] - 1) * 100, 1), f"{tag}_mdd": round(s["mdd%"], 1)}
    return out
if __name__ == "__main__":
    jobs = [(i, c) for i in ("ETH-USDT-SWAP", "BTC-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP") for c in CFGS]
    with Pool(9) as p: df = pd.DataFrame(p.map(job, jobs, chunksize=2))
    df.to_csv(HERE / "range_trade.csv", index=False)
    pd.set_option("display.width", 260); pd.set_option("display.max_rows", 200)
    k = ["N", "stop_atr", "L", "target"]
    g = df.groupby(k).agg(Sharpe=("all_sh", "mean"), 최저=("all_sh", "min"), IS=("is_sh", "mean"), OOS=("oos_sh", "mean"), 연수익=("all_ret", "mean"),
                          MDD=("all_mdd", "mean"), y26=("y26_ret", "mean"), 거래=("trades", "mean"), 승률=("win%", "mean")).round(2)
    print("== 4코인 평균 (횡보장 전고점·전저점 매매만, 증거금 4%)"); print(g.sort_values("IS", ascending=False).to_string())
    print("\n== ETH"); print(df[df.inst == "ETH"].drop(columns=["inst"]).sort_values("is_sh", ascending=False).head(10).to_string(index=False))
