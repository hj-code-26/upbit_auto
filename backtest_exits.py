"""봇 규칙(일봉 국면 + 4h 12봉 돌파 진입)에 익절·손절 방식을 붙여 1h 봉으로 비교. 결과: research_okx_short.txt
진입은 4h 봉 마감 직후(다음 1h 봉 시가). 청산 방식:
  struct    4h 종가 < 직전 6봉 저가 or 국면 붕괴 → 다음 봉 시가          (현재 봇: 4h 마감 뒤에만 인지)
  stop_lo6  직전 6봉 저가를 거래소 스톱 주문으로 걸어 둠 → 봉 안에서 그 가격에 체결 (4h 마다 갱신)
  sl=x      진입가 −x% 스톱 (봉 안 체결)      trail=x  진입 후 1h 고가 대비 −x% 추적 스톱     tp=x  진입가 +x% 지정가 익절
비용 편도 0.07% + 스톱 체결 슬리피지 0.05% 추가 · 펀딩 0.01%/8h · 갭이면 시가 체결. 격리 청산 = 저가 ≤ 진입가×(1−1/lev).
사용: python backtest_exits.py   (data_cache/okx_*.pkl 은 backtest_okx.py 가 만든다)
"""
import numpy as np
import pandas as pd

import backtest_okx as B
import model as M

COST, SLIP = 0.0007, 0.0005
FUND_H = 0.0001 * 3 / 24


def frame():
    """1h 봉 + 각 봉 시가 시점에 알 수 있는 최신 완성 4h 봉의 돌파·이탈·이탈선·국면."""
    D = {tf: B.fetch(tf) for tf in ("1d", "4h", "1h")}
    d1 = D["1d"]; bd = B.bull(d1); rg = (bd >= M.CONF); rg.index = rg.index + pd.Timedelta(days=1)
    h4 = D["4h"].copy()
    h4["hi"], h4["lo"] = h4.high.rolling(M.H4_N).max().shift(1), h4.low.rolling(M.H4_M).min().shift(1)
    h4["brk"], h4["bdn"] = h4.close > h4.hi, h4.close < h4.lo
    h4["regime"] = rg.reindex(h4.index, method="ffill").fillna(False).astype(bool).values
    h4["end"] = (h4.index + pd.Timedelta(hours=4)).astype("datetime64[ns, UTC]")
    h1 = D["1h"][D["1h"].index >= B.START].copy(); h1.index = h1.index.astype("datetime64[ns, UTC]")
    m = pd.merge_asof(h1.rename_axis("t").reset_index(), h4[["end", "brk", "bdn", "lo", "regime"]].reset_index(drop=True),
                      left_on="t", right_on="end")
    m["fresh"] = m.end == m.t                                # 이 1h 봉 시가가 4h 마감 직후
    return m.dropna(subset=["lo"]).reset_index(drop=True)


def run(m, lev, name, struct=True, stop_lo6=False, sl=None, trail=None, tp=None):
    o, h, l, c = m.open.values, m.high.values, m.low.values, m.close.values
    fresh, brk, bdn, lo, rg = m.fresh.values, m.brk.values, m.bdn.values, m.lo.values, m.regime.values
    eq, pos, trades, curve, liq = 1.0, None, [], np.ones(len(m)), 0
    for i in range(1, len(m)):
        if pos:
            e, hi_, k = pos; k += 1
            stop = max(x for x in (e * (1 - sl / 100) if sl else 0, hi_ * (1 - trail / 100) if trail else 0, lo[i] if stop_lo6 else 0))
            px, slip = None, 0
            if l[i] <= e * (1 - 1 / lev):
                eq, liq, px = 0, liq + 1, None; trades.append(-1.0); pos = None
            elif struct and fresh[i] and (bdn[i] or not rg[i]):
                px = o[i]
            elif stop and o[i] <= stop:
                px, slip = o[i], SLIP
            elif stop and l[i] <= stop:
                px, slip = stop, SLIP
            elif tp and h[i] >= e * (1 + tp / 100):
                px = max(o[i], e * (1 + tp / 100))
            if px:
                r = lev * (px / e - 1) - lev * (2 * COST + slip) - k * FUND_H * lev
                eq *= 1 + r; trades.append(r); pos = None
            elif pos:
                pos = (e, max(hi_, h[i]), k)
        if not pos and eq > 0 and fresh[i] and brk[i] and rg[i]:
            pos = (o[i], o[i], 0)
        curve[i] = eq * (1 + lev * (c[i] / pos[0] - 1)) if pos else eq
        if eq <= 0:
            curve[i:] = 0; break
    cv = pd.Series(curve, index=m.t)
    yr = cv.resample("YE").last(); yr = (yr / yr.shift(1).fillna(1) - 1) * 100
    tr = np.array(trades) if trades else np.zeros(1)
    print(f"{name:40s} x{lev}  누적 {cv.iloc[-1] * 100 - 100:+7.0f}%  MDD {(cv / cv.cummax() - 1).min() * 100:6.1f}%  "
          f"거래 {len(trades):4d} 승률 {(tr > 0).mean() * 100:3.0f}% 최악 {tr.min() * 100:6.1f}% 청산{liq} | "
          + " ".join(f"{y:+.0f}" for y in yr.values))


VARIANTS = [
    ("[현재] struct (4h 마감 뒤 인지)", {}),
    ("stop_lo6 (이탈선 스톱 주문, 봉 안 체결)", dict(stop_lo6=True)),
    ("struct + sl 2%", dict(sl=2)), ("struct + sl 3%", dict(sl=3)), ("struct + sl 5%", dict(sl=5)),
    ("struct + trail 3%", dict(trail=3)), ("struct + trail 5%", dict(trail=5)), ("struct + trail 8%", dict(trail=8)),
    ("struct + tp 5%", dict(tp=5)), ("struct + tp 10%", dict(tp=10)), ("struct + tp 20%", dict(tp=20)),
    ("stop_lo6 + sl 3%", dict(stop_lo6=True, sl=3)),
    ("stop_lo6 + trail 5%", dict(stop_lo6=True, trail=5)),
    ("stop_lo6 + sl 3% + tp 10%", dict(stop_lo6=True, sl=3, tp=10)),
    ("sl 3% + trail 5% (구조 청산 없음)", dict(struct=False, sl=3, trail=5)),
]

if __name__ == "__main__":
    m = frame()
    print(f"1h 봉 {m.t.min():%Y-%m-%d}~{m.t.max():%Y-%m-%d} · 비용 편도 {COST * 100:.2f}% (+스톱 슬리피지 {SLIP * 100:.2f}%) | 연도별: 2021 2022 2023 2024 2025 2026")
    for name, kw in VARIANTS:
        for lev in (1, 2, 3):
            run(m, lev, name, **kw)
        print()
