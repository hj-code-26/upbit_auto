"""정세 5단계 프로토타입 (리플레이 전용 — autotrade 는 쓰지 않는다). 과거 데이터 실험이며 수익을 보장하지 않는다.

정세 (일봉 종가에 아는 값, 2일 연속 같으면 확정):
  표 4개 = sign(종가 − SMA50) + sign(SMA50 − 20일 전 SMA50) + ADX14>25 의 DI 방향 + sign(종가 − SMA200)
  합 ≥ 3 → 5 강한 상승 · 2 → 4 상승 · −1~1 → 3 중립 · −2 → 2 하락 · ≤ −3 → 1 강한 하락
단계별 성향 (LEVELS): 5 = 롱·넓은 손절·추적 손절(급등을 탄다) · 4 = 롱·본전·DROP1 · 3 = 현금 · 2 = 숏 · 1 = 현금 (반등 잦음).
레버리지 (거래마다, 당일 변동폭에 맞춤): 손절 거리 d = stop_atr × ATR14/종가 →
  L = min(L_CAP, ⌊1 / (d + MMR + taker + BUF)⌋) — 청산가가 항상 손절가 바깥. 손실 크기는 레버리지가 아니라
  명목 = 잔고 × risk / d 로 정한다 (손절에 닿으면 잔고 risk 만큼 잃는다). 증거금 = 명목 / L.
python regime5.py → 코인 4개 · 통과 조건 3개 점검 표.
"""
import numpy as np
import pandas as pd

import rls

LEVELS = {5: dict(side=1, risk=0.03, stop=2.0, trail=2.0, drop1=False, be=None),          # IS(2020-08~2023-06 ETH·BTC)로 고른 값
          4: dict(side=1, risk=0.03, stop=1.5, trail=None, drop1=True, be=1.0),           # (reports/research_2026-10-16/regime5_grid.py)
          3: dict(side=0),
          2: dict(side=-1, risk=0.015, stop=1.5, trail=2.5, drop1=True, be=1.0),           # 하락이 이어지는 단계 → 숏
          1: dict(side=0)}                                                                 # 강한 하락 = 반등이 잦음 → 현금
L_CAP, EXPO_CAP, MARGIN_CAP, MMR, BUF, COOL = 50, 3.0, 0.5, 0.004, 0.01, 3   # ponytail: MMR 티어1 고정 가정
COST = rls.TAKER + rls.SLIP


def levels(d):
    """일봉 → 확정 정세 1~5 (NaN = 계산 전)."""
    c = d.close
    s50, s200 = c.rolling(50).mean(), c.rolling(200).mean()
    v = np.sign(c - s50) + np.sign(s50 - s50.shift(20)) + rls.adx_dir(d, 25) + np.sign(c - s200)
    raw = pd.Series(np.select([v >= 3, v == 2, v >= -1, v == -2], [5, 4, 3, 2], 1), d.index, dtype=float)
    raw[s200.isna() | s50.shift(20).isna()] = np.nan
    return raw.where(raw == raw.shift(1)).ffill()


def lever(d_stop):
    """손절 거리 → 그날 레버리지 (청산이 손절보다 BUF 만큼 바깥)."""
    return int(min(L_CAP, np.floor(1 / (d_stop + MMR + rls.TAKER + BUF))))


def run(d, start=None, end=None, levels_fn=levels, table=LEVELS):
    """한 일봉 열 → (일별 자산, 거래). 시가 진입·청산, 장중 손절, 종가에 추적·본전·DROP1·정세 판정."""
    x = d[["open", "high", "low", "close"]].astype(float).copy()
    x["lvl"], x["atr"], x["prev"] = levels_fn(d), rls.atr(x), x.close.shift()
    cash, pos, pend, cool, eqs, trades = 1.0, None, None, {}, {}, []

    def mark(px):
        return cash + (pos["side"] * pos["qty"] * (px - pos["entry"]) if pos else 0.0)

    def close(px, why, t):
        nonlocal cash, pos, pend
        p, pos, pend = pos, None, None
        if why == "liq":
            pnl = -p["margin"]
        else:
            fill = px * (1 - p["side"] * rls.SLIP)
            pnl = p["side"] * p["qty"] * (fill - p["entry"]) - rls.TAKER * p["qty"] * fill
        cash += pnl
        if why in ("sl", "drop1", "liq"):
            cool[p["side"]] = t + pd.Timedelta(days=COOL)
        trades.append({"entry_ts": p["t0"], "exit_ts": t, "side": p["side"], "lvl": p["lvl0"], "L": p["L"], "why": why,
                       "pnl_pct": (pnl - p["fee"] + p["fund"]) / p["eq0"], "expo": p["qty"] * p["entry"] / p["eq0"]})

    for k in range(1, len(x)):
        t, r, s = x.index[k], x.iloc[k], x.iloc[k - 1]
        if (start and t < pd.Timestamp(start)) or (end and t > pd.Timestamp(end)):
            continue
        lv = table.get(int(s.lvl), {"side": 0}) if not np.isnan(s.lvl) else {"side": 0}
        if pos and (r.open - pos["stop"]) * pos["side"] <= 0:                     # 시가 갭이 손절 너머
            close(r.open, "liq" if (r.open - pos["liq"]) * pos["side"] <= 0 else "sl", t)
        if pos and (pend or lv["side"] != pos["side"]):
            close(r.open, pend or "level", t)
        if not pos and lv["side"] and t >= cool.get(lv["side"], t) and not np.isnan(s.atr):
            dist = lv["stop"] * s.atr / s.close
            L, eq0 = lever(dist), cash
            notional = min(lv["risk"] * eq0 / dist, lv.get("expo", EXPO_CAP) * eq0, MARGIN_CAP * eq0 * L)
            fill = r.open * (1 + lv["side"] * rls.SLIP)
            fee = rls.TAKER * notional
            cash -= fee
            pos = {"side": lv["side"], "entry": fill, "qty": notional / fill, "margin": notional / L, "L": L, "t0": t,
                   "stop": fill * (1 - lv["side"] * dist), "liq": fill * (1 - lv["side"] * (1 / L - MMR)), "atr_e": s.atr,
                   "peak": fill, "lvl0": int(s.lvl), "eq0": eq0, "fee": fee, "fund": 0.0}
        if pos and (r.low <= pos["stop"] if pos["side"] > 0 else r.high >= pos["stop"]):
            close(pos["stop"], "sl", t)
        if pos:
            f = -pos["side"] * 3 * rls.FUNDING_8H * pos["qty"] * r.close
            cash, pos["fund"] = cash + f, pos["fund"] + f
            lp = table.get(int(r.lvl), {}) if not np.isnan(r.lvl) else {}
            p = {**table[pos["lvl0"]], **(lp if lp.get("side") == pos["side"] else {})}   # 같은 방향이면 오늘 단계 성향
            pos["peak"] = max(pos["peak"], r.close) if pos["side"] > 0 else min(pos["peak"], r.close)
            new = pos["stop"]
            if p.get("trail"):
                new = pos["peak"] - pos["side"] * p["trail"] * r.atr
            if p.get("be") and pos["side"] * (r.close - pos["entry"]) >= p["be"] * pos["atr_e"]:
                new = rls.tighten(pos["side"], new, rls.be_px(pos["side"], pos["entry"]))
            pos["stop"] = rls.tighten(pos["side"], pos["stop"], new)
            if p.get("drop1") and rls.drop1(pos["side"], r.close, r.prev, r.atr):
                pend = "drop1"
        eqs[t] = mark(r.close)
    return pd.Series(eqs), pd.DataFrame(trades)


def run_tranches(h1, offsets=(0, 8, 16), start=None, end=None, **kw):
    """시각 분할 평균 (rls.simulate_tranches 와 같은 방식: 몫마다 독립, 일수익 평균)."""
    rets, trs = [], []
    for k, off in enumerate(offsets):
        eq, tr = run(rls.offset_daily(h1, off, 1), start and pd.Timestamp(start) + pd.Timedelta(hours=off),
                     end and pd.Timestamp(end) + pd.Timedelta(hours=off), **kw)
        rets.append(eq.groupby(eq.index.normalize()).last().pct_change())
        trs.append(tr.assign(tranche=k))
    r = pd.concat(rets, axis=1).dropna().mean(axis=1)
    return (1 + r).cumprod(), pd.concat(trs, ignore_index=True)


def legs(c, th):
    """지그재그: 종가가 th 이상 반대로 움직이면 전환 → [(시작, 끝, 수익률)]."""
    out, piv_t, piv_v, up = [], c.index[0], c.iloc[0], None
    ext_t, ext_v = piv_t, piv_v
    for t, v in c.items():
        if up is None:
            if v >= piv_v * (1 + th) or v <= piv_v * (1 - th):
                up, ext_t, ext_v = v > piv_v, t, v
            continue
        if (v > ext_v) if up else (v < ext_v):
            ext_t, ext_v = t, v
        elif (v <= ext_v * (1 - th)) if up else (v >= ext_v * (1 + th)):
            out.append((piv_t, ext_t, ext_v / piv_v - 1))
            piv_t, piv_v, up, ext_t, ext_v = ext_t, ext_v, not up, t, v
    if up is not None:
        out.append((piv_t, ext_t, ext_v / piv_v - 1))                  # 마지막 구간 (아직 미확정)
    return out


def window(eq, a, b):
    s = eq[(eq.index >= a.normalize()) & (eq.index <= b.normalize())]
    return s.iloc[-1] / s.iloc[0] - 1 if len(s) > 1 else np.nan


def evaluate(inst, h1, start, end, **kw):
    """통과 조건 1·2·3 + 지금 운용 전략·보유 비교."""
    import backtest as bt
    eq, tr = run_tranches(h1, start=start, end=end, **kw)
    with bt.lever(30, 0.04):
        eq0, _ = rls.simulate_tranches({inst: h1}, (0, 8, 16), start, end)
    eq0 = eq0.groupby(eq0.index.normalize()).last()
    d = rls.offset_daily(h1, 0, 1)
    c = d.close[(d.index >= start) & (d.index <= end)]
    lv = levels(d)
    fwd = (d.close.shift(-10) / d.close - 1)[(d.index >= start) & (d.index <= end)]
    s, s0 = bt.stats(eq), bt.stats(eq0)
    out = {"inst": inst[:3], "cagr%": s["cagr%"], "mdd%": s["mdd%"], "sharpe": s["sharpe"], "trades": len(tr),
           "cur_cagr%": s0["cagr%"], "cur_mdd%": s0["mdd%"], "cur_sharpe": s0["sharpe"],
           "hold_cagr%": ((c.iloc[-1] / c.iloc[0]) ** (365.25 / (c.index[-1] - c.index[0]).days) - 1) * 100}
    out["fwd10_by_lvl%"] = (fwd.groupby(lv.reindex(fwd.index)).mean() * 100).round(2).to_dict()       # 조건 1: 정세가 미래를 정렬하나
    out["ev_by_lvl%"] = (tr.groupby("lvl").pnl_pct.agg(["count", "mean", "sum"]) * [1, 100, 100]).round(2).to_dict("index")
    out["L_by_lvl"] = tr.groupby("lvl").L.median().to_dict()
    bear = [(a, b, r) for a, b, r in legs(c, 0.25) if r < 0]                                          # 조건 2: 하락 구간
    bull = [(a, b, r) for a, b, r in legs(c, 0.25) if r >= 0.5]                                       # 조건 3: 급등 구간
    def seg(ls, e):
        return [window(e, a, b) for a, b, _ in ls]
    bearL = [x for x in legs(c, 0.25) if x[2] < 0]; bullL = [x for x in legs(c, 0.25) if x[2] >= 0.5]
    for tag, e in (("proto", eq), ("cur", eq0)):
        br, bu = np.array(seg(bearL, e)), np.array(seg(bullL, e))
        out[f"{tag}_bear_nonneg%"], out[f"{tag}_bear_mean%"] = (br >= 0).mean() * 100, np.nanmean(br) * 100
        out[f"{tag}_bull_capture%"] = np.nanmean(bu / np.array([r for _, _, r in bullL])) * 100
        out[f"{tag}_bull_mean%"] = np.nanmean(bu) * 100
    out["bear"] = [(str(a.date()), str(b.date()), round(r * 100), round(window(eq, a, b) * 100, 1), round(window(eq0, a, b) * 100, 1)) for a, b, r in bear]
    out["bull"] = [(str(a.date()), str(b.date()), round(r * 100), round(window(eq, a, b) * 100, 1), round(window(eq0, a, b) * 100, 1)) for a, b, r in bull]
    return out


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    import backtest as bt
    for inst, a in (("ETH-USDT-SWAP", "2023-07-01"), ("BTC-USDT-SWAP", "2023-07-01"), ("ETH-USDT-SWAP", "2020-08-01"), ("BTC-USDT-SWAP", "2020-08-01"), ("SOL-USDT-SWAP", "2021-09-01"), ("XRP-USDT-SWAP", "2020-08-01")):
        r = evaluate(inst, bt.cached_hourly(inst), a, "2026-10-12")
        print(f"\n== {r['inst']}  프로토 CAGR {r['cagr%']:.1f}% MDD {r['mdd%']:.1f}% Sharpe {r['sharpe']:.2f} 거래 {r['trades']}"
              f"  | 지금 전략 {r['cur_cagr%']:.1f}% · {r['cur_mdd%']:.1f}% · {r['cur_sharpe']:.2f} | 보유 {r['hold_cagr%']:.1f}%")
        print("  [1] 단계별 10일 뒤 보유 수익 평균 %:", r["fwd10_by_lvl%"])
        print("  [1] 진입 단계별 거래 (수, 평균 %, 합 %):", r["ev_by_lvl%"], " 레버리지 중앙값:", r["L_by_lvl"])
        print(f"  [2] 하락 구간 {len(r['bear'])}개: 손실 없음 비율 프로토 {r['proto_bear_nonneg%']:.0f}% (평균 {r['proto_bear_mean%']:+.1f}%)"
              f" · 지금 {r['cur_bear_nonneg%']:.0f}% ({r['cur_bear_mean%']:+.1f}%)")
        print(f"  [3] 급등 구간 {len(r['bull'])}개: 보유 대비 포착 프로토 {r['proto_bull_capture%']:.0f}% (평균 {r['proto_bull_mean%']:+.1f}%)"
              f" · 지금 {r['cur_bull_capture%']:.0f}% ({r['cur_bull_mean%']:+.1f}%)")
