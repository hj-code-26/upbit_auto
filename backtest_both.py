"""양방향(롱·숏) 매매 검증 — 국면별로 나눠 승률·수익률을 잰다.

용어는 사용자 기준으로 통일: **롱 = 상승에 거는 것, 숏 = 하락에 거는 것.** 청산은 방향이 아니라 행동이다.
(예전 코드의 zone="short" 는 '청산·진입 금지' 였다. model.py 에서 exit_long/exit_short 로 갈라냈다.)

규칙 (model.signal() 과 같다. 롱 규칙을 그대로 뒤집은 것이 숏이다)
  롱  국면 = 일봉 강세 규칙 CONF개↑ · 진입 = 4h 종가 > 직전 12봉 고가 · 청산 = 4h 종가 < 직전 6봉 저가 or 국면 붕괴
  숏  국면 = 일봉 약세 규칙 CONF개↑ · 진입 = 4h 종가 < 직전 12봉 저가 · 청산 = 4h 종가 > 직전 6봉 고가 or 국면 붕괴
  롱 국면과 숏 국면은 동시에 참일 수 없으므로 한 번에 한 포지션만 잡힌다. 방향 전환은 청산 후 다음 봉부터.

국면 분류 (quant_nasq100 backtest_volume.DIR_BAND=3.0 과 같은 정의를 BTC 자신에게 적용)
  BTC 60일 수익률 > +3% 상승 · < −3% 하락 · 그 사이 횡보. 전일까지의 정보만 쓴다.

체결·비용·평가는 backtest_quant.py 와 동일 (편도 0.07%, 격리 청산, 탐색/검증 분할, Sharpe = CAGR/연율변동성).
펀딩은 **숏에 유리하게** 잡았다 — 롱이 내는 0.01%/8h 를 숏이 받는 것으로 본다. 하락장에서는 펀딩이
음전(숏이 내는 쪽)하는 일이 잦으므로 아래 숏 성적은 낙관 쪽으로 치우친 값이다.

사용: python backtest_both.py [편도비용]
"""
import sys

import numpy as np
import pandas as pd

import backtest_okx as B
import backtest_quant as Q
import engine as E
import model as M

DIR_BAND = 3.0            # 60일 수익률 ±3% 를 횡보로 본다 (quant_nasq100 과 같은 값)


def bear(d):
    """일봉 약세 규칙 개수 — backtest_okx.bull() 의 거울."""
    x = M.add_indicators(d.copy())
    f = pd.DataFrame(index=d.index)
    f["ret_5d"], f["ret_20d"] = d.close.pct_change(5) * 100, d.close.pct_change(20) * 100
    return sum(s(x, f).astype(int) for _, _, s, _ in M.RULES)


def frames(n=None, m=None):
    """(4h 봉, 롱진입, 롱청산, 숏진입, 숏청산, 국면라벨). n·m 은 돌파·이탈 봉수 (기본 = 봇 값 12/6)."""
    n, m = n or M.H4_N, m or M.H4_M
    d1, h4 = B.fetch("1d"), B.fetch("4h")

    def regime(cnt):
        return M.align_daily(cnt >= M.CONF, h4.index).fillna(False).astype(bool)   # 판단 시각 = 4h 봉 종료

    up, dn = regime(B.bull(d1)), regime(bear(d1))
    hi_n, lo_n = h4.high.rolling(n).max().shift(1), h4.low.rolling(n).min().shift(1)
    hi_m, lo_m = h4.high.rolling(m).max().shift(1), h4.low.rolling(m).min().shift(1)

    ret60 = ((d1.close / d1.close.shift(60) - 1) * 100).shift(1)   # 전일까지의 정보
    lab = pd.Series(np.where(ret60 > DIR_BAND, "상승", np.where(ret60 < -DIR_BAND, "하락", "횡보")), index=d1.index)
    lab = M.align_daily(lab, h4.index)
    return (h4,
            ((h4.close > hi_n) & up).values, ((h4.close < lo_m) | ~up).values,
            ((h4.close < lo_n) & dn).values, ((h4.close > hi_m) | ~dn).values,
            lab.values)


def simulate(h4, sig, lev_of, lab, allow=("long", "short"), fills=None, gate=None, pos_pct=1.0):
    """engine.run 의 얇은 껍데기 — 체결·청산 판정은 engine.py 한 곳에만 있다 (2026-09-10 감사).
    fills 는 **롱에만** 적용된다 (숏 분봉 진입은 미구현 — research_both_sides.txt '후속 2').
    gate 는 방향별 튜플 (롱, 숏) 을 받는다. 하나짜리 배열을 주면 양방향 공통으로 쓴다.
    → (자본곡선, 거래 DataFrame, stats)"""
    return E.run(h4, *sig, lev_of, lab=lab, allow=allow, fills=fills, gate=gate, pos_pct=pos_pct)


def line(name, curve, t, stats):
    Q.report(name, curve, t.ret.values if len(t) else np.zeros(1), stats)


def by_regime(name, t):
    """국면 × 방향별 거래수·승률·평균수익·합산기여."""
    if not len(t):
        return
    print(f"\n  [{name}]  {'국면':<6}{'방향':<5}{'거래':>5}{'승률':>7}{'평균':>8}{'합산(단리)':>12}{'최악':>8}")
    for reg in ("상승", "횡보", "하락"):
        for side in ("롱", "숏"):
            x = t[(t.regime == reg) & (t.side == side)]
            if not len(x):
                continue
            print(f"  {'':<10}{reg:<8}{side:<7}{len(x):>3}{(x.ret > 0).mean() * 100:>6.0f}%"
                  f"{x.ret.mean() * 100:>+8.2f}%{x.ret.sum() * 100:>+10.0f}%{x.ret.min() * 100:>+8.1f}%")
    x = t
    print(f"  {'':<10}{'전체':<8}{'':<7}{len(x):>3}{(x.ret > 0).mean() * 100:>6.0f}%"
          f"{x.ret.mean() * 100:>+8.2f}%{x.ret.sum() * 100:>+10.0f}%{x.ret.min() * 100:>+8.1f}%")


def main():
    h4, l_en, l_ex, s_en, s_ex, lab = frames()
    m = h4.index >= pd.Timestamp(Q.START, tz="UTC")
    h4, lab = h4[m], lab[m]
    sig = (l_en[m], l_ex[m], s_en[m], s_ex[m])
    days = pd.Series(lab, index=h4.index).resample("D").last().dropna()
    share = days.value_counts(normalize=True) * 100
    print(f"OKX BTC 4h {h4.index[0]:%Y-%m-%d}~{h4.index[-1]:%Y-%m-%d} · CONF={M.CONF} · 편도 {Q.COST * 100:.2f}%")
    print(f"국면 비중 (BTC 60일 수익률 ±{DIR_BAND:.0f}%): "
          + " · ".join(f"{k} {share.get(k, 0):.0f}%" for k in ("상승", "횡보", "하락")))
    print("펀딩은 숏이 받는 쪽으로 가정 — 숏에 유리한 값이다\n")
    print(f"{'':22s}{'──── 탐색 ────':>22} |{'──── 검증 ────':>22} |{'────── 전체 ──────':>28}"
          f"{'거래':>5}{'승률':>6}{'최악':>7}{'청산':>4}")
    print(f"{'':22s}{'CAGR':>7}{'MDD':>7}{'Sh':>6} |{'CAGR':>7}{'MDD':>7}{'Sh':>6} |"
          f"{'누적':>9}{'MDD':>7}{'Sh':>6}")

    runs = {}
    print("\n── 방향별 (고정 3배) ──")
    for name, allow in (("롱만", ("long",)), ("숏만", ("short",)), ("롱+숏", ("long", "short"))):
        runs[name] = simulate(h4, sig, lambda i, c: 3, lab, allow)
        line(f"{name} 3배", *runs[name])

    print("\n── 1배 ──")
    for name, allow in (("롱만", ("long",)), ("숏만", ("short",)), ("롱+숏", ("long", "short"))):
        line(f"{name} 1배", *simulate(h4, sig, lambda i, c: 1, lab, allow))

    print("\n── 변동성 타겟팅(계좌 40%·창60일)을 얹으면 ──")
    def vt(i, c):
        v = Q.acct_vol(i, c)
        return Q.MAX_LEV if v is None else min(Q.MAX_LEV, 40 / v)
    for name, allow in (("롱만", ("long",)), ("롱+숏", ("long", "short"))):
        line(f"{name} +VT", *simulate(h4, sig, vt, lab, allow))

    print("\n\n═══ 국면 × 방향별 거래 성적 (고정 3배) ═══")
    for name in ("롱만", "숏만", "롱+숏"):
        by_regime(name, runs[name][1])

    print("\n\n═══ 이웃 파라미터 (한 조합에만 걸린 결과인지) ═══")
    for n, k in ((30, 15), (6, 3)):
        h2, a, b, c2, d, lab2 = frames(n, k)
        mm = h2.index >= pd.Timestamp(Q.START, tz="UTC")
        for name, allow in (("롱만", ("long",)), ("숏만", ("short",)), ("롱+숏", ("long", "short"))):
            line(f"{name} 3배 · {n}/{k}봉", *simulate(h2[mm], (a[mm], b[mm], c2[mm], d[mm]), lambda i, c: 3, lab2[mm], allow))
        print()

    print("── 참고 ──")
    line("BTC 상시보유 1배", h4.close / h4.close.iloc[0], pd.DataFrame(columns=["side", "ret", "regime"]), 0)

    # 자체 점검: 방향 배선. 계속 오르기만 하는 봉에서 롱은 벌고 숏은 잃어야 한다
    up = pd.DataFrame({"open": np.linspace(100, 200, 60), "close": np.linspace(100, 200, 60)},
                      index=pd.date_range("2024-01-01", periods=60, freq="4h", tz="UTC"))
    up["high"], up["low"] = up.close + 1, up.close - 1
    en = np.zeros(60, bool); en[0] = True
    ex = np.zeros(60, bool); ex[50] = True
    no = np.zeros(60, bool)
    lb = np.array(["상승"] * 60)
    assert simulate(up, (en, ex, no, no), lambda i, c: 1, lb)[1].ret.iloc[0] > 0.4, "상승 구간 롱은 벌어야 한다"
    assert simulate(up, (no, no, en, ex), lambda i, c: 1, lb)[1].ret.iloc[0] < -0.4, "상승 구간 숏은 잃어야 한다"
    both = simulate(up, (en, ex, en, ex), lambda i, c: 1, lb)[1]
    assert len(both) == 1 and both.side.iloc[0] == "롱", "한 번에 한 포지션만 (롱 우선)"
    print("\nok  자체 점검 통과 (상승 구간에서 롱 이익 · 숏 손실 · 동시 보유 없음)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
