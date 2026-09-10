"""하락 구간을 어떻게 먹을 것인가 — 숏이 안 된 뒤의 후속 검증 (research_both_sides.txt 의 다음 단계).

research_both_sides.txt 에서 나온 것: 숏은 어느 파라미터로도 음수이고, 손실의 76% 가 '하락 국면 숏' 한 칸에서
나왔다. 원인 가설은 '하락은 짧고 급하고 되돌림이 빠르다'. 그 가설이 맞다면 세 가지가 따라온다 —

  ① 숏 청산선을 훨씬 짧게 잡으면(6봉 → 1~3봉) 되돌림에 털리기 전에 나올 수 있나
  ② 하락 국면에서 **롱**(반등 매수)이 오히려 낫다는 신호가 이미 있다 (하락 국면 롱 승률 41%, 여섯 칸 중 1위).
     지금 규칙은 일봉이 강세일 때만 롱을 허용한다. 약세 국면의 4h 돌파 롱을 열면 어떻게 되나
  ③ 펀딩을 숏에 유리하게(받는 쪽) 가정했는데 실제로는 어땠나

한계
· 펀딩: OKX 공개 API 가 최근 3개월(약 93일)만 준다. 5.5년 백테스트에 실제 펀딩을 넣을 수는 없다.
  여기서는 그 3개월을 실측해 **가정이 어느 방향으로 틀렸는지만** 본다. 캐리(펀딩 차익)의 상한도 같은 구간 기준이다.
· 변동성 매도(옵션)는 여기서 못 잰다 — 이 데이터 경로에 옵션 히스토리가 없다. 재려면 다른 소스가 필요하다.

체결·비용·평가는 backtest_quant.py 와 동일. 사용: python backtest_downside.py [편도비용]
"""
import sys
import time

import ccxt
import numpy as np
import pandas as pd

import backtest_both as BO
import backtest_okx as B
import backtest_quant as Q
import model as M

FUND_PKL = M.CACHE / "okx_funding.pkl"


# ---------- ③ 실제 펀딩 ----------
def funding():
    """OKX 실제 펀딩률 (8시간마다). 공개 API 가 최근 3개월만 주므로 그만큼만 받는다."""
    if FUND_PKL.exists():
        return pd.read_pickle(FUND_PKL)
    ex = ccxt.okx({"enableRateLimit": True, "options": {"defaultType": "swap"}})
    rows, cur = [], ex.milliseconds()
    for _ in range(40):
        r = ex.fetch_funding_rate_history("BTC/USDT:USDT", limit=100, params={"after": cur})
        if not r:
            break
        rows += r
        cur = min(x["timestamp"] for x in r)
        time.sleep(0.1)
    s = pd.Series({pd.Timestamp(x["timestamp"], unit="ms", tz="UTC"): float(x["fundingRate"]) for x in rows}).sort_index()
    s.to_pickle(FUND_PKL)
    return s


def funding_report():
    f = funding()
    d1 = B.fetch("1d")
    print(f"── ③ 실제 펀딩 (OKX 공개 API 가 주는 전부: {f.index[0]:%Y-%m-%d} ~ {f.index[-1]:%Y-%m-%d}, {len(f)}회) ──")
    ann = f.mean() * 3 * 365 * 100
    print(f"  8시간 평균 {f.mean() * 100:+.4f}%  중앙값 {f.median() * 100:+.4f}%  →  연율 {ann:+.1f}%")
    print(f"  양수(롱이 내는 쪽) 비율 {(f > 0).mean() * 100:.0f}%   백테스트 가정값 +0.0100%/8h")
    day = f.resample("D").sum()
    chg = d1.close.pct_change().reindex(day.index)
    dn, up = day[chg < 0], day[chg > 0]
    print(f"  하락한 날의 하루 펀딩 평균 {dn.mean() * 100:+.4f}%  (상승한 날 {up.mean() * 100:+.4f}%)")
    print(f"  → 숏이 실제로 '받은' 금액은 하루 {dn.mean() * 100:+.4f}% 수준. 가정(+0.0300%/일)보다 "
          f"{'작다 → 앞선 숏 성적은 낙관 쪽으로 치우쳤다' if dn.mean() < 0.0003 else '크다'}")
    print(f"  캐리(무기한 숏 + 현물 롱, 델타 중립) 상한: 이 구간 연율 {ann:+.1f}% — 여기서 거래소 수수료·현물 조달·")
    print("     리밸런싱 비용이 빠진다. 방향성 전략이 아니라 별개 사업이고, 이 봇 구조(선물 단독)로는 못 한다.\n")
    return f


# ---------- ① 숏 청산선을 짧게 ----------
def short_sweep(h4, lab, m):
    print("── ① 숏: 진입선(n봉 저가) × 청산선(m봉 고가) 스윕, 3배 ──")
    for n in (6, 12, 30):
        for k in (1, 2, 3, 6):
            h2, a, b, c, d, lab2 = BO.frames(n, k)
            mm = h2.index >= pd.Timestamp(Q.START, tz="UTC")
            BO.line(f"숏 {n}봉진입 / {k}봉청산", *BO.simulate(h2[mm], (a[mm], b[mm], c[mm], d[mm]),
                                                        lambda i, c_: 3, lab2[mm], ("short",)))
        print()


# ---------- ② 하락 국면 반등 롱 ----------
def rebound(h4, m):
    """롱 진입의 일봉 국면 필터를 바꿔 본다. 지금은 '강세 국면일 때만' 인데,
    약세 국면(=하락)에서의 4h 돌파 롱이 반등 매수다."""
    d1 = B.fetch("1d")
    bull_r, bear_r = B.bull(d1) >= M.CONF, BO.bear(d1) >= M.CONF

    def to_h4(r):
        r = r.copy()
        r.index = r.index + pd.Timedelta(days=1)
        return r.reindex(h4.index, method="ffill").fillna(False).astype(bool)

    up, dn = to_h4(bull_r), to_h4(bear_r)
    hi_n = h4.high.rolling(M.H4_N).max().shift(1)
    lo_m = h4.low.rolling(M.H4_M).min().shift(1)
    brk = (h4.close > hi_n).values
    ex_ = (h4.close < lo_m).values
    lab = BO.frames()[5]

    print("── ② 롱 진입의 일봉 국면 필터를 바꾸면 (4h 12봉 고가 돌파 진입 · 6봉 저가 이탈 청산, 3배) ──")
    for name, gate in (("[현재] 강세 국면에서만", up.values),
                       ("약세 국면에서만 (반등 매수)", dn.values),
                       ("국면 무시 (항상)", np.ones(len(h4), bool)),
                       ("약세 아닐 때 (= 강세+중립)", ~dn.values)):
        en = brk & gate
        exi = ex_ | ~gate
        BO.line(name, *BO.simulate(h4[m], (en[m], exi[m], np.zeros(m.sum(), bool), np.zeros(m.sum(), bool)),
                                   lambda i, c: 3, lab[m], ("long",)))
    print()
    # 반등 롱의 국면별 성적
    en, exi = brk & dn.values, ex_ | ~dn.values
    _, t, _ = BO.simulate(h4[m], (en[m], exi[m], np.zeros(m.sum(), bool), np.zeros(m.sum(), bool)),
                          lambda i, c: 3, lab[m], ("long",))
    BO.by_regime("약세 국면 반등 롱", t)


def main():
    h4, l_en, l_ex, s_en, s_ex, lab = BO.frames()
    m = h4.index >= pd.Timestamp(Q.START, tz="UTC")
    print(f"OKX BTC 4h {h4[m].index[0]:%Y-%m-%d}~{h4[m].index[-1]:%Y-%m-%d} · CONF={M.CONF} · 편도 {Q.COST * 100:.2f}%")
    print(f"탐색 {Q.START}~{Q.SPLIT} / 검증 {Q.SPLIT}~\n")
    funding_report()
    print(f"{'':26s}{'──── 탐색 ────':>22} |{'──── 검증 ────':>22} |{'────── 전체 ──────':>28}"
          f"{'거래':>5}{'승률':>6}{'최악':>7}{'청산':>4}")
    print(f"{'':26s}{'CAGR':>7}{'MDD':>7}{'Sh':>6} |{'CAGR':>7}{'MDD':>7}{'Sh':>6} |"
          f"{'누적':>9}{'MDD':>7}{'Sh':>6}")
    print("\n[기준] 현재 봇 (롱만, 강세 국면)")
    BO.line("롱만 3배", *BO.simulate(h4[m], (l_en[m], l_ex[m], s_en[m], s_ex[m]), lambda i, c: 3, lab[m], ("long",)))
    print()
    short_sweep(h4, lab, m)
    rebound(h4, m)

    # 자체 점검: 국면 필터를 뒤집으면 거래가 겹치지 않아야 한다 (강세 국면과 약세 국면은 배타적)
    d1 = B.fetch("1d")
    assert not ((B.bull(d1) >= M.CONF) & (BO.bear(d1) >= M.CONF)).any(), "강세 국면과 약세 국면은 동시에 성립할 수 없다"
    f = funding()
    assert len(f) > 100 and f.abs().max() < 0.01, "펀딩률이 상식 범위(±1%/8h) 안에 있어야 한다"
    print("\nok  자체 점검 통과 (국면 배타성 · 펀딩률 범위)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
