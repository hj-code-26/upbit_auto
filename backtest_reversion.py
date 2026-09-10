"""평균회귀(고점 숏 · 저점 롱) 모델 검증 — reversion.py 의 신호를 10년 표본에서 잰다.

표본
  · 10년 가설 검증용: Bitstamp BTC/USD 4h·1d (2016-01~). 현물이라 숏은 못 하지만 '되돌림이 존재하는가' 를
    긴 표본에서 보는 데 쓴다. 2017 이전은 거래가 얇아 지표가 거칠다는 점을 감안할 것.
  · 실거래 검증용: OKX BTC-USDT-SWAP 4h (2021-03~). 비용·숏·격리청산이 실제와 같은 유일한 표본이다.
  · 나스닥: yfinance QQQ 일봉. 선택 필터로만 쓴다.

순서가 중요하다 — **먼저 신호의 예측력을 재고, 통과할 때만 거래로 옮긴다.**
고점 신호 뒤 수익률이 평균보다 낮지 않다면 어떤 청산 규칙도 그걸 되돌리지 못한다.

사용: python backtest_reversion.py            예측력 + 거래 시뮬
      python backtest_reversion.py --ablation 지표 하나씩 빼 보기
"""
import sys
import time

import ccxt
import numpy as np
import pandas as pd

import backtest_quant as Q
import reversion as R

SPLIT10 = "2021-01-01"          # 10년 표본의 탐색/검증 경계 (탐색 2016~2020 / 검증 2021~2026)
HORIZONS = (1, 3, 6, 12, 24)    # 4h 봉 기준 앞으로 몇 봉


# ---------- 데이터 ----------
def fetch(venue, tf, start="2016-01-01"):
    f = R.CACHE / f"{venue}_{tf}_{start[:4]}.pkl"
    if f.exists():
        return pd.read_pickle(f)
    R.CACHE.mkdir(exist_ok=True)
    ex = getattr(ccxt, venue)({"enableRateLimit": True})
    sym = "BTC/USD" if venue == "bitstamp" else "BTC/USDT"
    ms = {"1d": 86400, "4h": 14400}[tf] * 1000
    rows, since = [], ex.parse8601(f"{start}T00:00:00Z")
    while since < ex.milliseconds():
        try:
            b = ex.fetch_ohlcv(sym, tf, since=since, limit=1000)
        except ccxt.BaseError as e:
            print(venue, e, file=sys.stderr); time.sleep(2); continue
        if not b:
            break
        rows += b
        since = b[-1][0] + ms
        if len(b) < 2:
            break
    d = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).drop_duplicates("ts")
    d.index = pd.to_datetime(d.ts, unit="ms", utc=True)
    d = d.drop(columns="ts").astype(float).sort_index().iloc[:-1]
    d.to_pickle(f)
    return d


def nasdaq():
    """QQQ 일봉 (yfinance). 거래일만 있으므로 4h 로 붙일 때 ffill 한다."""
    f = R.CACHE / "qqq_1d.pkl"
    if f.exists():
        return pd.read_pickle(f)
    import yfinance as yf
    d = yf.download("QQQ", start="2015-06-01", auto_adjust=True, progress=False)
    d.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in d.columns]
    d.index = pd.DatetimeIndex(d.index).tz_localize("UTC")
    d = d[["open", "high", "low", "close", "volume"]].astype(float)
    d.to_pickle(f)
    return d


# ---------- ① 신호 예측력 ----------
def edge(d, name, conf=None, split=None):
    """고점·저점 신호 뒤 앞으로 n봉 수익률이 기준(전체 평균)과 다른가. 거래 규칙 없이 신호만 본다."""
    top, bot = R.signals(d, conf)
    c = d.close
    print(f"\n[{name}] 봉 {len(d):,}개 · 고점 후보 {top.sum():,} ({top.mean() * 100:.1f}%) · "
          f"저점 후보 {bot.sum():,} ({bot.mean() * 100:.1f}%)  (REV_CONF={conf or R.REV_CONF})")
    print(f"  {'앞으로':>6}{'전체평균':>10}{'고점 뒤':>10}{'차이':>9}{'승률':>7} |{'저점 뒤':>10}{'차이':>9}{'승률':>7}")
    for n in HORIZONS:
        fwd = (c.shift(-n) / c - 1) * 100
        base = fwd.mean()
        a, b = fwd[top.values], fwd[bot.values]
        print(f"  {n:4d}봉{base:+9.2f}%{a.mean():+9.2f}%{a.mean() - base:+8.2f}%{(a < 0).mean() * 100:6.0f}% |"
              f"{b.mean():+9.2f}%{b.mean() - base:+8.2f}%{(b > 0).mean() * 100:6.0f}%")
    if split:
        for lo, hi, lab in ((None, split, "탐색"), (split, None, "검증")):
            s = d[lo:hi]
            t2, b2 = R.signals(s, conf)
            c2 = s.close
            f6 = (c2.shift(-6) / c2 - 1) * 100
            print(f"  [{lab} {s.index[0]:%Y-%m}~{s.index[-1]:%Y-%m}] 6봉: 전체 {f6.mean():+.2f}% · "
                  f"고점 뒤 {f6[t2.values].mean():+.2f}% · 저점 뒤 {f6[b2.values].mean():+.2f}%")


# ---------- ② 거래 시뮬 ----------
def trade(d, exit_bars, conf=None, lev=1, cost=None, qqq_gate=None, side_allow=("long", "short"), invert=False):
    """고점 후보 → 숏, 저점 후보 → 롱. exit_bars 봉 뒤 무조건 청산 (평균회귀는 시간 청산이 자연스럽다).
    invert=True 면 반대로 — 고점 후보 → 롱, 저점 후보 → 숏 (①에서 극단이 '지속' 신호로 나왔으므로 같이 잰다).
    → (자본곡선, 거래 DataFrame, 청산횟수)"""
    cost = Q.COST if cost is None else cost
    assert 0 <= cost < 0.05, f"편도 비용 {cost} 가 상식 밖이다 (backtest_okx.arg_cost 주석 참고)"
    top, bot = R.signals(d, conf)
    o, hi, lo, c = d.open.values, d.high.values, d.low.values, d.close.values
    top, bot = top.values, bot.values
    n = len(d)
    eq, held, rows, liq = 1.0, None, [], 0
    curve = np.ones(n)
    for i in range(1, n):
        if held:
            e, k, side = held
            k += 1
            blown = (lo[i] <= e * (1 - 1 / lev)) if side > 0 else (hi[i] >= e * (1 + 1 / lev))
            if blown:
                eq, held, liq = 0.0, None, liq + 1
                rows.append({"side": "롱" if side > 0 else "숏", "ret": -1.0})
            elif k >= exit_bars:
                r = side * lev * (o[i] / e - 1) - 2 * lev * cost
                eq *= 1 + r
                rows.append({"side": "롱" if side > 0 else "숏", "ret": r})
                held = None
            else:
                held = (e, k, side)
        if held is None and eq > 0:
            g = None if qqq_gate is None else bool(qqq_gate[i - 1])   # None = 필터 없음 (양쪽 허용)
            up_sig, dn_sig = (top[i - 1], bot[i - 1]) if invert else (bot[i - 1], top[i - 1])
            side = 0
            if up_sig and "long" in side_allow and g is not False:         # 롱 자리
                side = 1
            elif dn_sig and "short" in side_allow and g is not True:        # 숏 자리
                side = -1
            if side:
                held = (o[i], 0, side)
        curve[i] = eq * (1 + held[2] * (c[i] / held[0] - 1)) if held else eq
        if eq <= 0:
            curve[i:] = 0.0
            break
    t = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["side", "ret"])
    return pd.Series(curve, index=d.index), t, liq


def line(name, curve, t, liq, split):
    r = t.ret.values if len(t) else np.zeros(1)
    a, b, f = (Q.metrics(curve, r, liq, *s) for s in ((None, split), (split, None), (None, None)))
    print(f"{name:26s}{a['cagr']:+7.0f}%{a['mdd']:7.1f}%{a['sharpe']:6.2f} |"
          f"{b['cagr']:+7.0f}%{b['mdd']:7.1f}%{b['sharpe']:6.2f} |"
          f"{f['누적']:+9.0f}%{f['mdd']:7.1f}%{f['sharpe']:6.2f}{f['거래']:5d}{f['승률']:5.0f}%{f['최악']:7.1f}%{liq:3d}")


def header(a, b):
    print(f"\n{'':26s}{'──── ' + a + ' ────':>22} |{'──── ' + b + ' ────':>22} |"
          f"{'────── 전체 ──────':>29}{'거래':>5}{'승률':>6}{'최악':>7}{'청산':>4}")


def main():
    bs4 = fetch("bitstamp", "4h")
    okx4 = Q.B.fetch("4h")
    okx4 = okx4[okx4.index >= pd.Timestamp("2021-03-01", tz="UTC")]
    print(f"Bitstamp BTC/USD 4h {bs4.index[0]:%Y-%m-%d}~{bs4.index[-1]:%Y-%m-%d} ({len(bs4):,}봉, "
          f"{(bs4.index[-1] - bs4.index[0]).days / 365:.1f}년)")
    print(f"OKX BTC-USDT-SWAP 4h {okx4.index[0]:%Y-%m-%d}~{okx4.index[-1]:%Y-%m-%d} ({len(okx4):,}봉)")

    print("\n\n═══ ① 신호 예측력 (거래 규칙 없이 신호 뒤 수익률만) ═══")
    edge(bs4, "Bitstamp 10년", split=SPLIT10)
    edge(okx4, "OKX 실거래 표본", split=Q.SPLIT)

    print("\n\n═══ ② 거래 시뮬 (고점→숏 · 저점→롱, N봉 뒤 시간 청산) ═══")
    header("탐색", "검증")
    print("\n-- Bitstamp 10년, 1배, 비용 편도 0.10% (현물 가정) --")
    for k in (1, 3, 6, 12, 24):
        line(f"청산 {k}봉", *trade(bs4, k, cost=0.001), split=SPLIT10)
    print("\n-- OKX 실거래 표본, 1배, 비용 편도 0.07% --")
    for k in (1, 3, 6, 12, 24):
        line(f"청산 {k}봉", *trade(okx4, k), split=Q.SPLIT)
    print("\n-- OKX, 방향 분리 (청산 6봉) --")
    for allow, nm in ((("long",), "저점 롱만"), (("short",), "고점 숏만"), (("long", "short"), "둘 다")):
        line(nm, *trade(okx4, 6, side_allow=allow), split=Q.SPLIT)
    print("\n-- OKX, 방향 뒤집기: 고점→롱 · 저점→숏 (①이 '극단은 지속' 이라 했으므로) --")
    for k in (3, 6, 12, 24):
        line(f"뒤집기 · 청산 {k}봉", *trade(okx4, k, invert=True), split=Q.SPLIT)
    print("\n-- Bitstamp 10년, 방향 뒤집기 --")
    for k in (6, 12, 24):
        line(f"뒤집기 · 청산 {k}봉", *trade(bs4, k, cost=0.001, invert=True), split=SPLIT10)

    print("\n-- OKX, REV_CONF (문턱) --")
    for cf in (3, 4, 5):
        line(f"REV_CONF={cf} · 6봉", *trade(okx4, 6, conf=cf), split=Q.SPLIT)

    print("\n\n═══ ③ 나스닥(QQQ) 필터 ═══")
    q = nasdaq()
    qret = (q.close / q.close.shift(5) - 1) * 100
    gate = qret.reindex(okx4.index, method="ffill")
    print(f"QQQ {q.index[0]:%Y-%m-%d}~{q.index[-1]:%Y-%m-%d} · BTC 4h 와 겹치는 봉 {gate.notna().sum():,}")
    corr = pd.concat([okx4.close.pct_change(6), gate], axis=1).dropna().corr().iloc[0, 1]
    print(f"BTC 24h 수익률 vs QQQ 5일 수익률 상관 {corr:+.3f}")
    header("탐색", "검증")
    line("필터 없음 (기준)", *trade(okx4, 6), split=Q.SPLIT)
    for thr in (0.0, 1.0):
        g = (gate > thr).values                      # True = 주식 강세 → 롱만 허용, False = 약세 → 숏만 허용
        line(f"QQQ 5일 > {thr:.0f}% 면 롱만", *trade(okx4, 6, qqq_gate=g), split=Q.SPLIT)

    print("\n\n═══ ⑤ ①의 발견을 기존 봇에 써먹을 수 있나 ═══")
    print("   ① 결론: 극단 지표(RSI 과열·밴드 이탈 등)는 이후 수익률을 **높인다**. '과열이라 위험' 은 틀렸다.")
    print("   그렇다면 기존 돌파 롱에 '고점 점수' 를 조건으로 얹으면 더 나은가?")
    import backtest_okx as B
    import model as M
    d1 = B.fetch("1d")
    rg = (B.bull(d1) >= M.CONF)
    rg.index = rg.index + pd.Timedelta(days=1)
    rg = rg.reindex(okx4.index, method="ffill").fillna(False).astype(bool)
    en = (okx4.close > okx4.high.rolling(M.H4_N).max().shift(1)) & rg
    ex = (okx4.close < okx4.low.rolling(M.H4_M).min().shift(1)) | ~rg
    hi_s, lo_s = R.scores(okx4)
    header("탐색", "검증")
    for nm, extra in (("[기준] 봇 롱 규칙 그대로", None),
                      ("+ 고점점수 ≥ 2 일 때만", hi_s >= 2),
                      ("+ 고점점수 ≥ 3 일 때만", hi_s >= 3),
                      ("+ 고점점수 ≥ 4 일 때만", hi_s >= 4),
                      ("+ 고점점수 ≤ 1 일 때만", hi_s <= 1),
                      ("+ 저점점수 = 0 일 때만", lo_s == 0)):
        e2 = en if extra is None else (en & extra)
        c, t, lq = Q.simulate(okx4, e2.values, ex.values, lambda i, cv: 3)
        Q.report(nm, c, t, lq)

    if "--ablation" in sys.argv:
        print("\n\n═══ ④ 지표 하나씩 빼기 (OKX, 청산 6봉, REV_CONF 는 비율 유지) ═══")
        header("탐색", "검증")
        full = R.RULES[:]
        line("전체 7개 (기준)", *trade(okx4, 6), split=Q.SPLIT)
        for j, (nm, _, _) in enumerate(full):
            R.RULES[:] = [r for i, r in enumerate(full) if i != j]
            line(f"− {nm}", *trade(okx4, 6, conf=max(2, round(R.REV_CONF * 6 / 7))), split=Q.SPLIT)
        R.RULES[:] = full


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
