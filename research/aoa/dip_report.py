"""모의 운용 구성(dipbuy.py: BTC 단독 · 50일선 · 롱 2% 급락 · TP4/SL6/3일)의 백테스트 성적표 — 1배 vs 2배.

dipbuy.on_bar 를 그대로 재생한다(실시간과 같은 코드). 순수익 = dipbuy.net_ret (비용 편도 0.07% · 펀딩 0.01%/8h · 갭 손절 시가).
L배 계좌: 거래마다 자본 × (1 + L·순수익). MDD 는 보유 중 5분봉 저가 평가손까지 포함. 2배 강제청산선(−49.6%)은 손절 6% 보다 멀다.
"""
import pathlib
import sys

import numpy as np
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[1]))
import dipbuy as DB  # noqa: E402
import features as F  # noqa: E402
from clone import okx_5m  # noqa: E402


def replay(c, lo, hi):
    dev, bull = DB.indicators(c, c.close.resample("1D").last())
    st, out = dict(pending=False, entry_t=None, entry_px=None, last_exit=None), []
    lows = c.low
    for t, o, h, l, dv, b in zip(c.index, c.open.values, c.high.values, c.low.values, dev.values, bull.values):
        if t < lo:
            continue
        if t >= hi and not st["entry_t"] and not st["pending"]:
            break
        r = DB.on_bar(st, t, o, h, l, dv if t < hi - DB.BAR else 0, b)
        if r:
            t0 = pd.Timestamp(r[0])
            mae = min(lows.loc[t0:t].min() / r[1] - 1, r[2] / r[1] - 1)
            out.append(dict(t0=t0, t1=t, net=DB.net_ret(r[1], r[2], r[4]), why=r[3], mae=mae))
    return pd.DataFrame(out)


def curve(tr, L):
    eq, peak, mdd, path = 1.0, 1.0, 0.0, []
    for r in tr.itertuples():
        mdd = min(mdd, eq * (1 + L * r.mae) / peak - 1)
        eq *= 1 + L * r.net
        peak = max(peak, eq); mdd = min(mdd, eq / peak - 1)
        path.append(eq)
    return np.array(path), mdd


if __name__ == "__main__":
    T = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731
    sets = [("BitMEX 2018-03~2021-12", F.candles().loc["2018-01-01":"2022-01-10"], "2018-03-05", "2022-01-01"),
            ("바이낸스 2020-01~2026-09", pd.read_pickle(F.ROOT / "data_cache" / "binance_btcusdt_5m_2020.pkl").asfreq("5min").ffill(),
             "2020-03-01", "2026-10-01"),
            ("OKX 2021-05~2026-09", okx_5m(), "2021-05-01", "2026-10-01")]
    for name, c, lo, hi in sets:
        tr = replay(c, T(lo), T(hi))
        yrs = (T(hi) if T(hi) < c.index[-1] else c.index[-1]) - T(lo)
        yrs = yrs.days / 365.25
        print(f"\n[{name}] {len(tr)}건 (연 {len(tr) / yrs:.1f}건) · 승률 {(tr.net > 0).mean():.1%} · 거래당 {tr.net.mean() * 100:+.2f}%"
              f" · 평균 이익 {tr.net[tr.net > 0].mean() * 100:+.2f}% / 평균 손실 {tr.net[tr.net <= 0].mean() * 100:+.2f}%"
              f" · 청산 사유 {tr.why.value_counts().to_dict()}")
        for L in (1, 2):
            p, mdd = curve(tr, L)
            cagr = (p[-1] ** (1 / yrs) - 1) * 100
            s = pd.Series(p, index=pd.DatetimeIndex(tr.t1)); yr = s.groupby(s.index.year).last()
            prev = pd.concat([pd.Series([1.0]), yr.iloc[:-1].reset_index(drop=True)]).values
            ys = {y: f"{(v / pv - 1) * 100:+.0f}%" for (y, v), pv in zip(yr.items(), prev)}
            print(f"  {L}배: 누적 {(p[-1] - 1) * 100:+,.0f}% · 연복리 {cagr:+.1f}% · MDD {mdd * 100:.0f}% · 연도별 {ys}")
