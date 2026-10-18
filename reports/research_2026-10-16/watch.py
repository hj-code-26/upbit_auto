"""진입 타이밍: 같은 규칙(지금 코드 신호·손절·본전·DROP1·edge·동적 크기, 30x, 4%)을 한 포지션으로 돌리되
'판단 가능한 시각'만 바꾼다 — 하루 1번(UTC 00) / 3번(00·08·16) / 매시간(신호가 뜨는 즉시).
신호 = 그 시각에 막 끝난 24시간 봉(그 시각 경계)으로 계산한 확정 레짐. 포지션은 자기 진입 시각 경계의 일봉으로 본전·DROP1·레짐 청산.
손절은 1H 봉 고저로 매시간. ponytail: 일손실·킬·펀딩 시각 차이는 단순화 (펀딩 = 시간당 1/8). → watch.csv"""
import sys, pathlib, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path[:0] = [str(HERE.parents[1]), str(HERE)]
import numpy as np, pandas as pd, rls, backtest as bt
from multiprocessing import Pool

def signals(h):
    """시각 t → (그 시각 경계 24h 봉이 막 끝났을 때의 conf, atr, close, prev) — 24개 경계 모두."""
    rows = []
    for off in range(24):
        x = rls.prep(rls.offset_daily(h, off, 1))
        x = x.assign(t=x.index + pd.Timedelta(hours=24))           # 봉이 끝나는 시각
        rows.append(x[["t", "conf", "atr", "close", "prev"]])
    return pd.concat(rows).set_index("t").sort_index()

def run(h, hours, start, end, L=30, margin=0.04):
    b = h.assign(t=pd.to_datetime(h.ts, unit="ms")).set_index("t")[["open", "high", "low", "close"]].astype(float)
    b = b[(b.index >= pd.Timestamp(start)) & (b.index <= pd.Timestamp(end))]
    S = signals(h)
    mmr, cash, pos, blk, pend = rls.SIM_MMR["coin"], 1.0, None, None, None
    eqs, trades = {}, []

    def close(px, why, t):
        nonlocal cash, pos, blk, pend
        p, pos, pend = pos, None, None
        fill = px * (1 - p["side"] * rls.SLIP)
        pnl = p["side"] * p["qty"] * (fill - p["entry"]) - rls.TAKER * p["qty"] * fill
        cash += pnl
        if rls.edge_after(why, p["be"]):
            blk = p["side"]
        trades.append({"entry_ts": p["t0"], "exit_ts": t, "side": p["side"], "why": why, "pnl_pct": (pnl - p["fee"] + p["fund"]) / p["eq0"]})

    for t, r in b.iterrows():
        sig = S.loc[t] if t in S.index else None                       # 이 시각에 막 끝난 24h 봉 (경계 = t.hour)
        own = pos is not None and t.hour == pos["h0"] and sig is not None
        if pos and pend:                                               # 자기 경계에서 정한 청산 → 이 시각 시가
            close(r.open, pend, t)
        if own and pos:                                                # 자기 경계 일봉: 레짐 청산 판정은 다음 시가
            if sig.conf != pos["side"]:
                close(r.open, "regime", t)
        if sig is not None and blk is not None and sig.conf != blk and t.hour in hours:
            blk = None
        if pos is None and sig is not None and t.hour in hours and sig.conf and sig.conf != blk \
                and not np.isnan(sig.atr) and rls.vol_ok(sig.atr, sig.close):
            side = int(sig.conf); dist = rls.stop_dist(sig.atr, sig.close, L, mmr)
            m = cash * margin * rls.vol_k(sig.atr, sig.close)
            fill = r.open * (1 + side * rls.SLIP); fee = rls.TAKER * m * L; cash -= fee
            pos = {"side": side, "entry": fill, "qty": m * L / fill, "stop": rls.stop_px(side, fill, dist), "atr_e": sig.atr,
                   "be": False, "h0": t.hour, "t0": t, "fee": fee, "fund": 0.0, "eq0": cash + fee}
        if pos and (r.low <= pos["stop"] if pos["side"] > 0 else r.high >= pos["stop"]):
            close(min(r.open, pos["stop"]) if pos["side"] > 0 else max(r.open, pos["stop"]), "sl", t)
        if pos:
            f = -pos["side"] * rls.FUNDING_8H / 8 * pos["qty"] * r.close
            cash += f; pos["fund"] += f
            nxt = t + pd.Timedelta(hours=1)                            # 자기 경계 봉이 이 봉으로 끝나면 종가 판정
            if nxt.hour == pos["h0"] and nxt in S.index and nxt - pos["t0"] >= pd.Timedelta(hours=24):
                s2 = S.loc[nxt]
                if rls.drop1(pos["side"], s2.close, s2.prev, s2.atr):
                    pend = "drop1"; blk = pos["side"]
                elif not pos["be"] and rls.be_hit(pos["side"], s2.close, pos["entry"], pos["atr_e"]):
                    pos["stop"] = rls.tighten(pos["side"], pos["stop"], rls.be_px(pos["side"], pos["entry"])); pos["be"] = True
        eqs[t] = cash + (pos["side"] * pos["qty"] * (r.close - pos["entry"]) if pos else 0.0)
    eq = pd.Series(eqs)
    return eq.groupby(eq.index.normalize()).last(), pd.DataFrame(trades)

MODES = {"하루 1번 (00)": {0}, "하루 3번 (00·08·16)": {0, 8, 16}, "6번 (4시간마다)": {0, 4, 8, 12, 16, 20}, "매시간 (즉시)": set(range(24))}
def job(a):
    inst, mode = a
    h = bt.cached_hourly(inst); s0 = "2021-09-01" if inst[:3] == "SOL" else "2020-06-01"
    out = {"inst": inst[:3], "mode": mode}
    for tag, a0, b0 in (("all", s0, "2026-10-16"), ("is", s0, "2023-06-30"), ("oos", "2023-07-01", "2026-10-16"), ("y26", "2026-01-01", "2026-10-16")):
        eq, tr = run(h, MODES[mode], a0, b0)
        s = bt.stats(eq)
        out |= {f"{tag}_ret" if tag == "y26" else f"{tag}_cagr": round((eq.iloc[-1] / eq.iloc[0] - 1) * 100 if tag == "y26" else s["cagr%"], 1),
                f"{tag}_mdd": round(s["mdd%"], 1), f"{tag}_sh": round(s["sharpe"], 2)}
        if tag == "all":
            out |= {"trades": len(tr), "win": round((tr.pnl_pct > 0).mean() * 100), "d1_sl%": round(((tr.why == "sl") & ((tr.exit_ts - tr.entry_ts) < pd.Timedelta(hours=24))).mean() * 100)}
    return out
if __name__ == "__main__":
    with Pool(9) as p:
        df = pd.DataFrame(p.map(job, [(i, m) for i in ("ETH-USDT-SWAP", "BTC-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP") for m in MODES]))
    df.to_csv(HERE / "watch.csv", index=False)
    pd.set_option("display.width", 250); print(df.to_string(index=False))
    print("\n평균 (4코인)"); print(df.groupby("mode")[["all_sh", "all_cagr", "all_mdd", "is_sh", "oos_sh", "y26_ret", "trades", "win", "d1_sl%"]].mean().loc[list(MODES)].round(2))
