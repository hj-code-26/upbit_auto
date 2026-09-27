"""롱만 D=2% · TP4 / SL6 조합의 레버리지별 성적 (tpsl.py 후보 1위).

거래 수익률(1배, 비용·펀딩 포함) r 에 대해 L배 계좌 수익 = L·r. 추가 현실 조건:
  · 펀딩 0.01%/8h 를 보유 시간만큼 명목에 부과 (롱이 낸다)
  · 격리 강제청산: 가격이 진입가 대비 (1/L − MMR 0.4%) 이상 불리해지면 증거금 전액 손실 → 계좌의 pos_pct(=1) 전부
    손절선 6% 보다 청산선이 가까우면(L ≥ 16) 손절 전에 청산된다
  · 손절 체결: 봉이 손절가 아래에서 열리면(갭) 그 봉 시가로 체결 (1배 표보다 보수적)
동시 1포지션 · 계좌 전액 증거금 · 복리.
"""
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import features as F  # noqa: E402
from clone import okx_5m, prep  # noqa: E402

D, TP, SL, MAXB, COST, FUND, MMR = 2.0, 4.0, 6.0, 864, 0.0007, 0.0001, 0.004


def trades(p, lo, hi):
    """→ [(시각, 1배 가격수익률 %, 최대 불리폭 %, 보유 봉수)] 비용 전."""
    o, h, l, dev, idx = p["o"], p["h"], p["l"], p["dev"], p["idx"]
    i, i1, out = max(idx.searchsorted(lo), 300), idx.searchsorted(hi), []
    while i < i1 - 1:
        if dev[i] > -D:
            i += 1; continue
        k = i + 1; e = o[k]; up, dn = e * (1 + TP / 100), e * (1 - SL / 100)
        end = min(k + MAXB, len(o) - 1); mae = 0.0; r = None
        for j in range(k, end):
            if l[j] <= dn:
                px = min(dn, o[j]); mae = max(mae, (1 - px / e) * 100); r = (px / e - 1) * 100; break   # 손절 체결 뒤 저가는 무관
            mae = max(mae, (1 - l[j] / e) * 100)
            if h[j] >= up:
                r = TP; break
        if r is None:
            j = end; r = (o[end] / e - 1) * 100
        out.append((idx[k], r, mae, j - k + 1))
        i = j + 1
    return out


def lev_curve(tr, L):
    eq, peak, mdd, liq = 1.0, 1.0, 0.0, 0
    for t, r, mae, bars in tr:
        liq_dist = (1 / L - MMR) * 100
        if mae >= liq_dist:                      # 손절/익절 전에 청산선 도달
            ret = -1.0; liq += 1
        else:
            ret = L * (r / 100 - 2 * COST - FUND * bars / 96)
        # 거래 중 평가손(최대 불리폭) 반영한 MDD
        low = eq * (1 - min(1.0, L * mae / 100))
        mdd = min(mdd, low / peak - 1)
        eq *= max(1 + ret, 0.0)
        peak = max(peak, eq); mdd = min(mdd, eq / peak - 1)
        if eq <= 0:
            break
    return eq, mdd, liq


if __name__ == "__main__":
    T = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731
    PI = prep(F.candles().loc["2018-02-01":"2021-12-31"])
    PO = prep(okx_5m().loc["2021-11-01":])
    per = {"2018~21 (IS, BitMEX)": trades(PI, T("2018-03-05"), T("2022-01-01")),
           "2022~25 (검증, OKX)": trades(PO, T("2022-01-01"), T("2026-01-01")),
           "2026.1~9 (최종, OKX)": trades(PO, T("2026-01-01"), T("2027-01-01"))}
    for name, tr in per.items():
        r = np.array([x[1] / 100 - 2 * COST - FUND * x[3] / 96 for x in tr])
        yrs = (tr[-1][0] - tr[0][0]).days / 365.25 if len(tr) > 1 else 1
        kelly = r.mean() / r.var()
        print(f"\n[{name}] {len(tr)}건 · 1배 거래당 {r.mean() * 100:+.2f}% · 최대 불리폭 중앙 {np.median([x[2] for x in tr]):.1f}% "
              f"최대 {max(x[2] for x in tr):.1f}% · 켈리 최적 배율 ≈ {kelly:.1f}배 (절반 켈리 {kelly / 2:.1f}배)")
        for L in (1, 2, 3, 5, 7, 10, 15, 20):
            eq, mdd, liq = lev_curve(tr, L)
            cagr = (eq ** (1 / yrs) - 1) * 100 if eq > 0 and yrs >= 1 else float("nan")
            print(f"  {L:>2}배: 누적 {(eq - 1) * 100:>+10,.0f}%  연복리 {cagr:>+6.0f}%  MDD {mdd * 100:>5.0f}%  강제청산 {liq}회")
