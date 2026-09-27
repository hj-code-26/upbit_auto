"""모의 운용 2배 전환 판정 — 사전등록 (2026-09-27, 결과 보기 전 작성).

대상: dipbuy.py 현재 규칙(50·200일선 필터 추가됨). dipbuy.indicators / on_bar 를 그대로 재생.
일봉: 200일선 예열을 위해 데이터 시작 전은 비트스탬프 BTC 일봉으로 채운다 (실시간은 OKX 일봉 260개).
BTC 3종: BitMEX 2018-03~2021-12 · 바이낸스 2020-03~2026-09 · OKX 2021-05~2026-09. 비교용으로 50일선만(옛 규칙)도 같이 낸다.

2배 채택 기준 (50·200일선 규칙, 사전 고정):
  ① 3종 모두 2배 MDD(보유 중 평가손 포함) ≥ −40%
  ② 3종 모두 2배 연복리 > 1배 연복리
  ③ 가장 나쁜 데이터의 켈리 최적 배율(거래당 순수익 평균 / 분산) ≥ 2
  셋 다 → dipbuy.LEV = 2. 아니면 1배 유지.
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
from dip_report import curve  # noqa: E402


def replay(c, d1, lo, hi, only50=False):
    if only50:
        dev = (c.close / c.close.ewm(span=12, adjust=False).mean() - 1) * 100
        ok = d1 > d1.rolling(50).mean()
        bull = pd.Series(ok.reindex(c.index.normalize() - pd.Timedelta("1D")).fillna(False).values.astype(bool), index=c.index)
    else:
        dev, bull = DB.indicators(c, d1)
    st, out = dict(pending=False, entry_t=None, entry_px=None, last_exit=None), []
    for t, o, h, l, dv, b in zip(c.index, c.open.values, c.high.values, c.low.values, dev.values, bull.values):
        if t < lo:
            continue
        if t >= hi and not st["entry_t"] and not st["pending"]:
            break
        r = DB.on_bar(st, t, o, h, l, dv if t < hi - DB.BAR else 0, b)
        if r:
            t0 = pd.Timestamp(r[0])
            mae = min(c.low.loc[t0:t].min() / r[1] - 1, r[2] / r[1] - 1)
            out.append(dict(t0=t0, t1=t, net=DB.net_ret(r[1], r[2], r[4]), why=r[3], mae=mae))
    return pd.DataFrame(out)


if __name__ == "__main__":
    T = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731
    bs = pd.read_pickle(F.ROOT / "data_cache" / "bitstamp_1d_2016.pkl").close

    def daily(c):
        own = c.close.resample("1D").last()
        return pd.concat([bs[bs.index < own.index[0]], own])

    sets = [("BitMEX 18-21", F.candles().loc["2018-01-01":"2022-01-10"], "2018-03-05", "2022-01-01"),
            ("바이낸스 20-26", pd.read_pickle(F.ROOT / "data_cache" / "binance_btcusdt_5m_2020.pkl").asfreq("5min").ffill(),
             "2020-03-01", "2026-10-01"),
            ("OKX 21-26", okx_5m(), "2021-05-01", "2026-10-01")]
    res = {}
    for name, c, lo, hi in sets:
        d1 = daily(c)
        yrs = (min(T(hi), c.index[-1]) - T(lo)).days / 365.25
        for rule, only50 in (("50일선(옛)", True), ("50·200일선", False)):
            tr = replay(c, d1, T(lo), T(hi), only50)
            kelly = tr.net.mean() / tr.net.var()
            line = f"[{name} · {rule}] {len(tr)}건 승률 {(tr.net > 0).mean():.1%} 거래당 {tr.net.mean() * 100:+.2f}% 켈리 {kelly:.1f}배"
            for L in (1, 2):
                p, mdd = curve(tr, L)
                cagr = (p[-1] ** (1 / yrs) - 1) * 100
                s = pd.Series(p, index=pd.DatetimeIndex(tr.t1)); yr = s.groupby(s.index.year).last()
                prev = np.r_[1.0, yr.values[:-1]]
                ys = " ".join(f"{y % 100:02d}:{(v / pv - 1) * 100:+.0f}" for (y, v), pv in zip(yr.items(), prev))
                line += f"\n    {L}배 누적 {(p[-1] - 1) * 100:+,.0f}% 연복리 {cagr:+.1f}% MDD {mdd * 100:.0f}% | {ys}"
                res[(name, rule, L)] = (cagr, mdd * 100)
            res[(name, rule, "kelly")] = kelly
            print(line)
    names = [s[0] for s in sets]; R = "50·200일선"
    c1 = all(res[(n, R, 2)][1] >= -40 for n in names)
    c2 = all(res[(n, R, 2)][0] > res[(n, R, 1)][0] for n in names)
    k = min(res[(n, R, "kelly")] for n in names); c3 = k >= 2
    print(f"\n판정: ① 2배 MDD ≥ −40% {'통과' if c1 else '불합격'} ({', '.join(f'{res[(n, R, 2)][1]:.0f}%' for n in names)})"
          f" · ② 2배 > 1배 {'통과' if c2 else '불합격'} · ③ 최소 켈리 {k:.1f}배 {'통과' if c3 else '불합격'}")
    print("  →", "2배 적용" if c1 and c2 and c3 else "1배 유지")
