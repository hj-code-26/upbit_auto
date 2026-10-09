"""RLS 20x DROP1 규칙 — 순수 함수, 거래소 호출 없음. 규칙을 바꾸지 않는다.
과거 백테스트(2020-01~2026-10)의 사후 탐색 결과이며 수익을 보장하지 않는다.

  레짐(일 d 종가): BULL = close_d > SMA50_d & SMA50_d > SMA50_(d-20), BEAR = 거울상, 그 외 NEUTRAL.
  CONF3: d-2, d-1, d 레짐이 모두 같을 때만 확정 레짐_d = 레짐_d, 아니면 NEUTRAL. d+1 00:00 UTC 부터 적용.
  진입: 확정 레짐이 새로 켜진 날(전날 확정 레짐과 다름)의 다음 1H 시가. 같은 레짐 동안 재진입 없음(edge).
  손절: 거리 = min(1.5×ATR14/진입 직전 종가, 청산 거리 − 1%p), 청산 거리 = 1/20 − MMR − taker ≈ 4.55%.
  BE1: 1H 종가가 포지션 방향으로 1×ATR14(진입 시 고정) 이상 → 손절을 진입가 ± 0.15% 로 (다음 봉부터, 후퇴 없음).
  DROP1: 1H 종가 기준 롱 c_t − c_(t−24) ≤ −1×ATR14(현재), 숏 ≥ +1×ATR14 → 다음 1H 시가 청산.
  ATR14 = 완결 일봉 TR 의 Wilder 평활(alpha=1/14). 시점 t 에서는 t 이전에 완결된 일봉만 쓴다.
  주의: 명세 수치(CAGR 30.9%, MDD 19.0%)는 연구 엔진의 SMA14 ATR 로 나왔다. Wilder 면 CAGR ≈ 24%, MDD ≈ 21% (ATR_KIND).
"""
import numpy as np
import pandas as pd

SMA, SLOPE, ATR_N, CONF = 50, 20, 14, 3
MIN_DAYS = SMA + SLOPE                 # SMA50 + 기울기 20일 = 완결 일봉 70개 이상
MARGIN, LEVER = 0.04, 20               # 증거금 = 자산의 4%, 20x 격리 → 명목 80% (종목당)
SL_ATR, LIQ_BUF, BE_ATR, BE_OFF, DROP_ATR = 1.5, 0.01, 1.0, 0.0015, 1.0
TAKER, SLIP = 0.0005, 0.0002
COST = TAKER + SLIP                    # 편도 (왕복 약 0.14%)
FUNDING_8H = 0.0001                    # 백테스트 가정 0.01%/8h
MMR = 0.004                            # ponytail: 유지증거금률 1구간 근사. 실제 티어는 데모에서 확인 (체크리스트 4)
KILL_SIM, DAILY_LOSS = 0.40, 0.05      # 백테스트 엔진 킬 스위치 40% (운용은 35%), 일손실 −5%
DAY_MS, H_MS = 86_400_000, 3_600_000
ATR_KIND = "wilder"                    # 명세 문구. "sma" = 연구 엔진(명세 수치 재현)


def daily(h1):
    """1H 봉(ts ms, open, high, low, close, confirm) → UTC 00:00 경계 일봉.
    24개 1H 가 모두 확정(confirm=1)된 날만 남긴다. index = 그날 00:00 UTC (naive Timestamp)."""
    h = h1[h1.confirm == 1].sort_values("ts")
    g = h.groupby(h.ts // DAY_MS)
    d = pd.DataFrame({"open": g.open.first(), "high": g.high.max(), "low": g.low.min(),
                      "close": g.close.last(), "n": g.size()})
    d = d[d.n == 24].drop(columns="n")
    d.index = pd.to_datetime(d.index * DAY_MS, unit="ms")
    return d


def regime(d):
    """{1: BULL, -1: BEAR, 0: NEUTRAL}. SMA50 은 '최근 50개 완결 일봉' (결측일은 daily() 에서 이미 빠져 있다)."""
    c = d.close
    s = c.rolling(SMA).mean()
    sh = s.shift(SLOPE)
    return ((c > s) & (s > sh)).astype(int) - ((c < s) & (s < sh)).astype(int)


def confirm(reg):
    """CONF3: 최근 3개 완결 일봉 레짐이 모두 같으면 그 레짐, 아니면 0."""
    same = (reg == reg.shift(1)) & (reg == reg.shift(2))
    return reg.where(same, 0).astype(int)


def edges(conf):
    """확정 레짐이 새로 켜진 날 (전날 확정 레짐과 다르고 NEUTRAL 이 아님)."""
    return (conf != conf.shift()) & (conf != 0)


def atr(d, kind=None):
    """ATR14 (완결 일봉). wilder = True Range 의 Wilder 평활 (alpha = 1/14), sma = 14일 단순평균. 첫 날 TR = high - low."""
    pc = d.close.shift()
    tr = pd.concat([d.high - d.low, (d.high - pc).abs(), (d.low - pc).abs()], axis=1).max(axis=1)
    if (kind or ATR_KIND) == "sma":
        return tr.rolling(ATR_N).mean()
    return tr.ewm(alpha=1 / ATR_N, adjust=False, min_periods=ATR_N).mean()


def liq_dist(lever=LEVER):
    return 1 / lever - MMR - TAKER


def stop_dist(atr_v, prev_close, lever=LEVER):
    """손절 거리(비율) = min(1.5×ATR/진입 직전 종가, 청산 거리 − 1%p). 항상 청산가보다 진입가 쪽."""
    return min(SL_ATR * atr_v / prev_close, liq_dist(lever) - LIQ_BUF)


def stop_px(side, entry, dist):
    return entry * (1 - side * dist)


def inside_liq(side, entry, liq):
    """거래소 청산가 기준으로 다시 잡은 '안쪽' 손절가 (청산가에서 진입가 쪽으로 1%p)."""
    return stop_px(side, entry, abs(entry - liq) / entry - LIQ_BUF)


def be_px(side, entry):
    return entry * (1 + side * BE_OFF)


def be_hit(side, close, entry, atr_entry):
    return side * (close - entry) >= BE_ATR * atr_entry


def tighten(side, old, new):
    """손절은 손실 방향으로 되돌리지 않는다 (롱은 올리기만, 숏은 내리기만)."""
    return max(old, new) if side > 0 else min(old, new)


def drop1(side, c_now, c_24, atr_v):
    return side * (c_now - c_24) <= -DROP_ATR * atr_v


def funding_pnl(side, rate, notional):
    """펀딩 손익. 양(+) 펀딩이면 롱이 지불(-), 숏이 수취(+)."""
    return -side * rate * notional


def signals(h1, atr_kind=None):
    """1H 봉 → 봉별 신호 DataFrame (index = ts). 봉 i 종가 시점에 알 수 있는 것만 쓴다:
    want = 봉 i 종가 때 완결된 마지막 일봉의 확정 레짐 (봉 i+1 시가에 적용), atr = 같은 일봉의 ATR14,
    c24 = 24봉 전 1H 종가. 일봉 결측이면 want=NaN (신호 갱신 없음)."""
    h = h1[h1.confirm == 1].sort_values("ts").drop_duplicates("ts").set_index("ts")
    d = daily(h1)
    day = pd.to_datetime((h.index + H_MS) // DAY_MS * DAY_MS - DAY_MS, unit="ms")
    out = h[["open", "high", "low", "close"]].copy()
    out["reg"] = regime(d).reindex(day).to_numpy(float)
    out["want"] = confirm(regime(d)).reindex(day).to_numpy(float)
    out["atr"] = atr(d, atr_kind).reindex(day).to_numpy(float)
    out["c24"] = h.close.reindex(h.index - DAY_MS).to_numpy(float)
    return out


def simulate(h1, lever=LEVER, margin=MARGIN, start=None, kill=KILL_SIM, daily_loss=DAILY_LOSS,
             funding_8h=FUNDING_8H, atr_kind=None):
    """1H 리플레이, 한 종목 = 독립 계좌 (연구 엔진 src/backtest/engine.py 와 같은 순서). → (1H 자산 Series, 거래 DataFrame)
    봉 i 시가: 예약 청산(drop1/daily_loss/halt) 또는 레짐 청산 → 진입(확정 레짐 ≠ 0, edge 차단 아님).
    봉 i 장중: 손절(갭이면 시가, 불리) / 청산가(시가에 가까운 쪽 먼저). 봉 i 종가: 펀딩(00/08/16 UTC 봉) → DROP1 → BE1
    (다음 봉부터) → 평가 → 일손실(−5% → 다음 시가 청산, 그날 진입 없음) → 킬(고점 대비 kill → 청산·영구 정지).
    체결: 불리한 방향 슬리피지 2bps + taker 0.05%. 손실은 격리 증거금으로 상한. edge: sl/drop1/daily_loss 뒤에는
    확정 레짐이 한 번 바뀔 때까지 같은 방향 진입 금지 (연구 엔진은 일손실 뒤엔 다음 날 바로 재진입 — 명세 문구를 따른다).
    start 를 주면 그 전 봉은 워밍업에만 쓴다 (그 시점에 이미 켜진 레짐에는 진입하지 않고 다음 에지를 기다린다)."""
    x = signals(h1, atr_kind)
    ts = x.index.to_numpy(np.int64)
    o, h, l, c, want_, av, c24 = (x[k].to_numpy(float) for k in ("open", "high", "low", "close", "want", "atr", "c24"))
    i0 = 1 if start is None else max(1, int(np.searchsorted(ts, int(pd.Timestamp(start).timestamp() * 1000))))
    cash = peak = eq_prev = 1.0
    p, blk, want, halted, flat_next, day, day_eq0, day_blk = None, 0, 0, False, None, None, 1.0, False
    if start is not None:
        blk = int(np.nan_to_num(want_[i0 - 1]))
    eqs, trades = [], []

    def close(i, px, why):
        nonlocal cash, p
        if why == "liq":
            gross, fee, xpx = -p["margin"], 0.0, px
        else:
            xpx = px * (1 - p["side"] * SLIP)
            gross, fee = p["side"] * p["qty"] * (xpx - p["entry"]), TAKER * p["qty"] * xpx
        cash += gross - fee
        pnl = gross - fee - p["fee_in"] + p["fund"]
        trades.append({"entry_ts": ts[p["i"]], "exit_ts": ts[i], "side": p["side"], "entry": p["entry"], "exit": xpx,
                       "stop_dist": p["dist"], "liq": p["liq"], "be": p["be_ts"] is not None, "be_ts": p["be_ts"],
                       "why": why, "fee": p["fee_in"] + fee, "funding": p["fund"], "pnl": pnl, "pnl_pct": pnl / p["eq0"]})
        p = None

    for i in range(i0, len(ts)):
        if ts[i] // DAY_MS != day:
            day, day_eq0, day_blk = ts[i] // DAY_MS, eq_prev, False
        if not np.isnan(want_[i - 1]):                              # 일봉 결측이면 직전 신호 유지 (소급 없음)
            want = int(want_[i - 1])
        if blk and want != blk:
            blk = 0
        if p and flat_next:
            close(i, o[i], flat_next)
        elif p and p.get("drop"):
            close(i, o[i], "drop1")
        elif p and want != p["side"]:
            close(i, o[i], "regime")
        flat_next = None
        if not p and want and want != blk and not halted and not day_blk and not np.isnan(av[i - 1]):
            px = o[i] * (1 + want * SLIP)
            dist = stop_dist(av[i - 1], c[i - 1], lever)
            notional = cash * margin * lever
            p = {"side": want, "entry": px, "qty": notional / px, "margin": notional / lever, "dist": dist,
                 "stop": stop_px(want, px, dist), "liq": px * (1 - want * liq_dist(lever)), "atr_e": av[i - 1],
                 "be_ts": None, "i": i, "eq0": cash, "fee_in": TAKER * notional, "fund": 0.0}
            cash -= p["fee_in"]
        if p:
            lo, hi = (l[i], p["stop"]) if p["side"] > 0 else (p["stop"], h[i])
            if lo <= hi:                                             # 손절이 청산가보다 항상 안쪽 → 손절 먼저
                gap = (o[i] - p["stop"]) * p["side"] <= 0
                px = o[i] if gap else p["stop"]
                d = p["side"]
                close(i, px, "liq" if (px - p["liq"]) * d <= 0 else "sl")
                blk = d
        if p:
            if ts[i] // H_MS % 8 == 0:
                f = funding_pnl(p["side"], funding_8h, p["qty"] * c[i])
                cash += f
                p["fund"] += f
            if not np.isnan(c24[i]) and not np.isnan(av[i]) and drop1(p["side"], c[i], c24[i], av[i]):
                p["drop"] = True
                if want == p["side"]:
                    blk = p["side"]
            elif p["be_ts"] is None and be_hit(p["side"], c[i], p["entry"], p["atr_e"]):
                p["stop"], p["be_ts"] = tighten(p["side"], p["stop"], be_px(p["side"], p["entry"])), ts[i]
        eq_prev = cash + (p["side"] * p["qty"] * (c[i] - p["entry"]) if p else 0.0)
        eqs.append(eq_prev)
        peak = max(peak, eq_prev)
        if not day_blk and eq_prev <= day_eq0 * (1 - daily_loss):
            day_blk, flat_next = True, "daily_loss"
            if p and want == p["side"]:
                blk = p["side"]
        if not halted and 1 - eq_prev / peak >= kill:
            halted, flat_next = True, "halt"
    return pd.Series(eqs, index=pd.to_datetime(ts[i0:], unit="ms")), pd.DataFrame(trades)


def portfolio(eqs):
    """종목별 독립 계좌 결과를 같은 자본에 합산: 일수익 합 (연구 엔진 rls_lev.portfolio 와 같음).
    ponytail: 종목 간 복리·동시성 무시. 실운용은 한 계좌에서 일손실·킬을 합산으로 본다."""
    j = pd.concat([e.groupby(e.index.normalize()).last().pct_change() for e in eqs], axis=1)
    return (1 + j.dropna(how="all").fillna(0.0).sum(axis=1)).cumprod()


def simulate_rls5(d, margin=0.02, lever=5, funding_8h=FUNDING_8H, start=None):
    """기준선: RLS-v0 5x 2% (FWD-v2 동결 설정). 일 d 레짐(확인 없음) → d+1 시가, 손절 3×ATR 고정,
    손절 후 같은 방향은 새 에지까지 재진입 금지. → (일별 자산 Series)."""
    r, av = regime(d).to_numpy(), atr(d).to_numpy()
    o, h, l, c = (d[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    cash, side, qty, entry, trig, blk = 1.0, 0, 0.0, 0.0, 0.0, 0
    eq = [1.0]

    def close(px):
        nonlocal cash, side
        cash += max(side * qty * (px - entry), -qty * entry / lever) - COST * qty * px
        side = 0

    for i in range(1, len(d)):
        want = r[i - 1] if start is None or d.index[i] >= start else 0
        if blk and r[i - 1] != blk:
            blk = 0
        if side and (o[i] - trig) * side <= 0:
            blk = side
            close(o[i])
        if side and want != side:
            close(o[i])
        if not side and want and want != blk and not np.isnan(av[i - 1]):
            side, entry = want, o[i]
            qty = cash * margin * lever / o[i]
            stop, liq = entry - side * 3 * av[i - 1], entry * (1 - side * (1 / lever - MMR))
            trig = stop if (stop - liq) * side >= 0 else liq
            cash -= COST * qty * o[i]
        if side and ((side == 1 and l[i] <= trig) or (side == -1 and h[i] >= trig)):
            blk = side
            close(trig)
        if side:
            cash += funding_pnl(side, 3 * funding_8h, qty * c[i])
        eq.append(cash + (side * qty * (c[i] - entry) if side else 0.0))
    eq = pd.Series(eq, index=d.index)
    return eq if start is None else eq[eq.index >= start] / eq[eq.index >= start].iloc[0]
