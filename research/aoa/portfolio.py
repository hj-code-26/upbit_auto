"""급락 매수 포트폴리오 — 사전등록 (2026-09-26, BTC 2020~26 바이낸스 데이터 받기 전 작성).

유니버스 7종: BTC · ETH · SOL · XRP · BNB · DOGE · ADA (바이낸스 현물 5분봉 2020-01~2026-09, 선물로 체결한다고 가정).
코인별 규칙 (tpsl.py / sma50.py 와 동일, 코인별 조정 없음):
  5분 종가가 1h EMA(12봉) 대비 −2% 이하 → 다음 봉 시가 롱 · 익절 +4% / 손절 −6% (갭이면 시가) / 시간손절 3일 · 코인당 동시 1포지션
  비용 편도 0.07% · 펀딩 0.01%/8h (롱이 낸다)
변형: A = 필터 없음, B = 그 코인의 일봉 50일선 필터 (전날 종가 > 전날까지 50일 이평)
사이징: 진입 순간 '실현 자본 / 7' × 레버리지 L 을 명목으로. 여러 코인 동시 보유 가능(최대 7개).
평가: 1시간 그리드에서 보유 포지션을 그 시간 '저가'로 평가(롱에 보수적) → MDD. 1배 기준 판정, 2·3배는 참고.

비교 대상: ① BTC 단독(같은 규칙·같은 변형, 자본 전액) ② 7종 동일비중 보유(월초 리밸런싱)
   ③ 무작위 진입 포트폴리오: 코인별 같은 거래 수를 같은 변형 조건의 날짜 안 무작위 시각에 (같은 TP/SL, 10회 평균)

채택 기준 (변형 B 포트폴리오, 1배, 사전 고정):
  ① 누적 수익 > 0 이고 전반(2020-01~2022-12)·후반(2023-01~2026-09) 둘 다 > 0
  ② 달력연도 7개(2020~2026) 중 5개 이상 양수
  ③ 수익/위험(연복리 ÷ |MDD|) 이 BTC 단독 B 보다 높고, MDD 가 BTC 단독 B 보다 얕다
  ④ 무작위 진입 포트폴리오보다 누적 수익이 높다
  넷 다 통과 → '포트폴리오 채택 후보'. A 는 참고로만 같이 보고한다.
"""
import heapq
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import features as F  # noqa: E402
from clone import prep  # noqa: E402
from sma50 import bull_mask  # noqa: E402
from tpsl import MAXB, path_exit  # noqa: E402

COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT", "ADAUSDT"]
D, TP, SL, COST, FUND = 2.0, 4.0, 6.0, 0.0007, 0.0001
LO, HI = pd.Timestamp("2020-01-01", tz="UTC"), pd.Timestamp("2026-10-01", tz="UTC")


def load(sym):
    c = pd.read_pickle(F.ROOT / "data_cache" / f"binance_{sym.lower()}_5m_2020.pkl")
    return c.asfreq("5min").ffill()


def coin_trades(c, use_filter, rand_n=None, seed=0):
    """→ [(진입시각, 청산시각, 진입가, 순수익률 소수)]. rand_n 이 있으면 조건 날짜 안 무작위 시각 rand_n 개."""
    p = prep(c); o, h, l, dev, idx = p["o"], p["h"], p["l"], p["dev"], p["idx"]
    mask = bull_mask(c) if use_filter else np.ones(len(o), bool)
    out = []
    net = lambda r, bars: r / 100 - 2 * COST - FUND * bars / 96  # noqa: E731
    if rand_n is not None:
        ok = np.where(mask[:len(o) - MAXB - 2])[0]; ok = ok[ok > 300]
        for i in np.sort(np.random.default_rng(seed).choice(ok, rand_n, replace=False)) if rand_n and len(ok) >= rand_n else []:
            j, r = path_exit(o, h, l, i, 1, TP, SL)
            out.append((idx[i], idx[j], o[i], net(r, j - i + 1)))
        return out
    i = max(idx.searchsorted(LO), 300); i1 = idx.searchsorted(HI)
    while i < i1 - 1:
        if dev[i] > -D or not mask[i]:
            i += 1; continue
        j, r = path_exit(o, h, l, i + 1, 1, TP, SL)
        out.append((idx[i + 1], idx[j], o[i + 1], net(r, j - i)))
        i = j + 1
    return out


def simulate(trades_by_coin, lows, L=1.0, n_slots=None, weights=None):
    """trades_by_coin: {coin: [...]} · lows: {coin: 1h 저가 Series} → (1h 자본곡선, 거래 표).
    weights: {coin: 비중} 이면 진입 명목 = 실현자본 × 비중 × L (없으면 1/n 균등)."""
    n = n_slots or len(trades_by_coin)
    ev = sorted((t0, t1, px, r, cn) for cn, tr in trades_by_coin.items() for t0, t1, px, r in tr)
    cash, openq, rows = 1.0, [], []
    for t0, t1, px, r, cn in ev:
        while openq and openq[0][0] <= t0:
            cash += heapq.heappop(openq)[1]
        notional = cash * (weights[cn] if weights else 1 / n) * L
        pnl = notional * r
        heapq.heappush(openq, (t1, pnl))
        rows.append((t0, t1, cn, px, notional, pnl))
    tb = pd.DataFrame(rows, columns=["t0", "t1", "coin", "px", "notional", "pnl"])
    grid = pd.date_range(LO, min(HI, max(s.index[-1] for s in lows.values())), freq="1h")
    realized = tb.groupby("t1").pnl.sum().sort_index().cumsum().reindex(grid, method="ffill").fillna(0).values
    unreal = np.zeros(len(grid))
    for cn, g in tb.groupby("coin"):
        lo = lows[cn].reindex(grid).ffill().values
        for t0, t1, px, notional in zip(g.t0, g.t1, g.px, g.notional):
            a, b = grid.searchsorted(t0), grid.searchsorted(t1)
            if b > a:
                unreal[a:b] += notional * (lo[a:b] / px - 1)
    return pd.Series(1.0 + realized + unreal, index=grid), tb


def stats(eq, lo=LO, hi=HI):
    e = eq[(eq.index >= lo) & (eq.index < hi)]
    yrs = (e.index[-1] - e.index[0]).days / 365.25
    tot = e.iloc[-1] / e.iloc[0] - 1
    cagr = (1 + tot) ** (1 / yrs) - 1 if tot > -1 else -1
    mdd = (e / e.cummax() - 1).min()
    return tot * 100, cagr * 100, mdd * 100


def yearly(eq):
    y = eq.resample("YE").last(); y0 = pd.concat([eq.iloc[:1], y]).shift(1).iloc[1:]
    return ((y / y0.values - 1) * 100).round(0).set_axis(y.index.year)


if __name__ == "__main__":
    data = {s: load(s) for s in COINS}
    lows = {s: c.low.resample("1h").min() for s, c in data.items()}
    closes = {s: c.close.resample("1h").last() for s, c in data.items()}
    T = pd.Timestamp
    res = {}
    for var, filt in (("A 필터없음", False), ("B 50일선", True)):
        tr = {s: coin_trades(c, filt) for s, c in data.items()}
        for L in (1, 2, 3):
            eq, tb = simulate(tr, lows, L)
            res[(var, "포트폴리오", L)] = eq
            if L == 1:
                print(f"\n[{var}] 코인별 거래수", {s[:-4]: len(v) for s, v in tr.items()},
                      f"| 동시 보유 최대 {max(((tb.t0 <= t) & (tb.t1 > t)).sum() for t in tb.t0)}")
        eqb, _ = simulate({"BTCUSDT": tr["BTCUSDT"]}, lows, 1, n_slots=1)
        res[(var, "BTC 단독", 1)] = eqb
        rnd = [simulate({s: coin_trades(c, filt, rand_n=len(tr[s]), seed=k) for s, c in data.items()}, lows, 1)[0]
               for k in range(10)]
        res[(var, "무작위 진입", 1)] = pd.concat(rnd, axis=1).mean(axis=1)
    px = pd.DataFrame(closes).loc[LO:]
    mret = px.resample("MS").first()
    monthly = (px.resample("ME").last().values / mret.values - 1)
    bh_m = pd.Series(np.nancumprod(1 + np.nanmean(monthly, axis=1)), index=px.resample("ME").last().index)
    print("\n기간 2020-01~2026-09 · 1시간 저가 평가 MDD")
    print(f"{'':28s}{'누적':>10}{'연복리':>8}{'MDD':>7}{'수익/위험':>9} | 전반(20-22) 후반(23-26) | 연도별")
    for k, eq in res.items():
        tot, cagr, mdd = stats(eq)
        a = stats(eq, LO, T("2023-01-01", tz="UTC"))[0]; b = stats(eq, T("2023-01-01", tz="UTC"), HI)[0]
        print(f"{k[0]:9s}{k[1]:12s}{k[2]}배 {tot:>+10,.0f}%{cagr:>+7.0f}%{mdd:>6.0f}%{cagr / abs(mdd):>9.2f} | {a:>+8.0f}% {b:>+9.0f}% | {yearly(eq).to_dict()}")
    tot, cagr, mdd = (bh_m.iloc[-1] - 1) * 100, (bh_m.iloc[-1] ** (1 / 6.7) - 1) * 100, ((bh_m / bh_m.cummax()) - 1).min() * 100
    print(f"{'7종 동일비중 보유(월 리밸런싱, 월말 기준)':30s} 누적 {tot:+,.0f}% 연복리 {cagr:+.0f}% MDD {mdd:.0f}%")
    # 판정
    eq = res[("B 50일선", "포트폴리오", 1)]
    tot, cagr, mdd = stats(eq)
    a = stats(eq, LO, T("2023-01-01", tz="UTC"))[0]; b = stats(eq, T("2023-01-01", tz="UTC"), HI)[0]
    yr = yearly(eq)
    bt, bc, bm = stats(res[("B 50일선", "BTC 단독", 1)])
    rt = stats(res[("B 50일선", "무작위 진입", 1)])[0]
    c1 = tot > 0 and a > 0 and b > 0
    c2 = (yr > 0).sum() >= 5
    c3 = cagr / abs(mdd) > bc / abs(bm) and mdd > bm
    c4 = tot > rt
    print(f"\n판정(B 포트폴리오 1배): ① 누적·전반·후반 >0 {'통과' if c1 else '불합격'} · ② 양수 연도 {(yr > 0).sum()}/{len(yr)} "
          f"{'통과' if c2 else '불합격'} · ③ 수익/위험·MDD 가 BTC 단독보다 우위 {'통과' if c3 else '불합격'} "
          f"· ④ 무작위 진입보다 우위 {'통과' if c4 else '불합격'}")
    print("  →", "포트폴리오 채택 후보" if c1 and c2 and c3 and c4 else "기각")
