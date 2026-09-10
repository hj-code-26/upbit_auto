"""지금 .env 설정 그대로 백테스트 — "현재 알고리즘의 수익률·승률은 얼마인가" 에 답하는 파일.

설정을 손으로 옮겨 적지 않고 `.env` 를 직접 읽는다. 그래야 봇과 백테스트가 어긋날 수 없다.
반영하는 것: CONF · LEVERAGE · VOL_TARGET_PCT/VOL_WINDOW · ENTRY_FLOOR · ALLOW_SHORT · EXTREME_MIN
표본: OKX BTC-USDT-SWAP 4h 2021-03-01~ · 편도 0.07% · 펀딩 0.01%/8h · 격리 청산 · 탐색/검증 분할

숏이 꺼져 있으면(ALLOW_SHORT=0) 아래 '롱/숏 비율' 은 자동으로 롱 100% 가 된다. 그래도 숏 자리가
얼마나 자주 났는지, 켰다면 어떻게 됐을지는 같이 보여 준다 — 무엇을 포기하고 있는지 알아야 하니까.

사용: python backtest_current.py          현재 설정의 수익률·승률·롱숏 비율
      python backtest_current.py --lev    레버리지를 올리면 어디서 청산되는지 (2026-09-10 엔진 수정 후: 고정 10배부터 전액 손실)
"""
import sys

import numpy as np
import pandas as pd
from dotenv import dotenv_values

import backtest_both as BO
import backtest_quant as Q
import model as M
import reversion as R

ENV = {**dotenv_values(Q.M.ROOT / ".env")}


def cfg(k, d):
    v = (ENV.get(k) or "").split("#")[0].strip()
    return v if v else d


LEV = float(cfg("LEVERAGE", "3"))
VT_PCT = float(cfg("VOL_TARGET_PCT", "40"))
VT_WIN = int(cfg("VOL_WINDOW", "40"))
FLOOR = cfg("ENTRY_FLOOR", "1") != "0"
SHORT = cfg("ALLOW_SHORT", "0") != "0"
EXT = int(cfg("EXTREME_MIN", "0"))


def by_side(t, label):
    """방향별 거래수·비중·승률·평균·합산기여."""
    if not len(t):
        print(f"  {label}: 거래 없음")
        return
    print(f"\n  [{label}]  {'방향':<6}{'거래':>5}{'비중':>7}{'승률':>7}{'평균':>9}{'합산(단리)':>12}{'최악':>9}")
    for side in ("롱", "숏"):
        x = t[t.side == side]
        if not len(x):
            print(f"  {'':<10}{side:<8}{0:>3}{0:>7.0f}%{'':>7}{'':>9}{'':>12}")
            continue
        print(f"  {'':<10}{side:<8}{len(x):>3}{len(x) / len(t) * 100:>6.0f}%{(x.ret > 0).mean() * 100:>6.0f}%"
              f"{x.ret.mean() * 100:>+9.2f}%{x.ret.sum() * 100:>+11.0f}%{x.ret.min() * 100:>+9.1f}%")
    print(f"  {'':<10}{'전체':<8}{len(t):>3}{100:>6.0f}%{(t.ret > 0).mean() * 100:>6.0f}%"
          f"{t.ret.mean() * 100:>+9.2f}%{t.ret.sum() * 100:>+11.0f}%{t.ret.min() * 100:>+9.1f}%")


def main():
    print("═══ 현재 .env 설정 ═══")
    print(f"  CONF={M.CONF} · LEVERAGE 상한 {LEV:g}배 · ENTRY_FLOOR={'켜짐(분봉 하한 재확보)' if FLOOR else '꺼짐(신호 즉시)'}")
    print(f"  변동성 타겟 {VT_PCT:g}% / 창 {VT_WIN}일" if VT_PCT else "  변동성 타겟: 꺼짐 (고정 레버리지)")
    print(f"  ALLOW_SHORT={'켜짐' if SHORT else '꺼짐 → 롱 전용'} · EXTREME_MIN={EXT}{' (꺼짐)' if not EXT else ''}")

    h4, l_en, l_ex, s_en, s_ex, lab = BO.frames()
    m = h4.index >= pd.Timestamp(Q.START, tz="UTC")
    h4, lab = h4[m], lab[m]
    sig = (l_en[m], l_ex[m], s_en[m], s_ex[m])
    fills = Q.reclaim_fills() if FLOOR else None
    hi_s, lo_s = R.scores(h4)
    # EXT 필터는 방향별로 따로 본다. 예전엔 (hi>=EXT) | (lo>=EXT) 를 양쪽에 같이 걸어서
    # '숏 극단' 점수만 높아도 롱 진입이 통과했다 (2026-09-10 감사).
    gate = ((hi_s >= EXT).values, (lo_s >= EXT).values) if EXT else None
    allow = ("long", "short") if SHORT else ("long",)

    def lev_of(i, c):
        if not VT_PCT:
            return LEV
        v = Q.acct_vol(i, c, VT_WIN)
        return LEV if v is None else min(LEV, VT_PCT / v)

    print(f"\n표본 {h4.index[0]:%Y-%m-%d}~{h4.index[-1]:%Y-%m-%d} ({(h4.index[-1] - h4.index[0]).days / 365:.1f}년) · "
          f"편도 {Q.COST * 100:.2f}% · 탐색 {Q.START}~{Q.SPLIT} / 검증 {Q.SPLIT}~")
    print(f"\n{'':22s}{'──── 탐색 ────':>22} |{'──── 검증 ────':>22} |{'────── 전체 ──────':>28}"
          f"{'거래':>5}{'승률':>6}{'최악':>7}{'청산':>4}")
    print(f"{'':22s}{'CAGR':>7}{'MDD':>7}{'Sh':>6} |{'CAGR':>7}{'MDD':>7}{'Sh':>6} |"
          f"{'누적':>9}{'MDD':>7}{'Sh':>6}")

    print("\n── 현재 설정 ──")
    cur = BO.simulate(h4, sig, lev_of, lab, allow, fills=fills, gate=gate)
    BO.line("[지금 이 봇]", *cur)

    print("\n── 비교: 무엇이 얼마를 만드는가 (하나씩 켜고 끄면) ──")
    if VT_PCT:
        BO.line(f"VT 끄고 고정 {LEV:g}배", *BO.simulate(h4, sig, lambda i, c: LEV, lab, allow, fills=fills, gate=gate))
    else:
        BO.line(f"VT 켜면 (타겟 40%·창40)", *BO.simulate(
            h4, sig, lambda i, c: (lambda v: LEV if v is None else min(LEV, 40 / v))(Q.acct_vol(i, c, 40)),
            lab, allow, fills=fills, gate=gate))
    BO.line("하한 재확보 끄면", *BO.simulate(h4, sig, lev_of, lab, allow, fills=None, gate=gate))
    BO.line(f"기본 규칙만 ({LEV:g}배)", *BO.simulate(h4, sig, lambda i, c: LEV, lab, allow, fills=None, gate=None))
    for L in (1, 2, 3):
        if L < LEV:
            BO.line(f"{L}배로 낮추면", *BO.simulate(h4, sig, lambda i, c, L=L: L, lab, allow, fills=fills, gate=gate))
    BO.line("BTC 상시보유 1배", h4.close / h4.close.iloc[0], pd.DataFrame(columns=["side", "ret", "regime"]), 0)

    print("\n\n═══ 롱/숏 비율 ═══")
    by_side(cur[1], "지금 이 봇")
    if not SHORT:
        n_s = int((sig[2]).sum())
        print(f"\n  숏은 ALLOW_SHORT=0 이라 한 건도 없다 → **롱 100% / 숏 0%**")
        print(f"  다만 숏 '자리' 자체는 표본 기간에 {n_s}번 났다 (롱 자리 {int(sig[0].sum())}번).")
        print(f"  켰다면 어떻게 됐을지:")
        both = BO.simulate(h4, sig, lev_of, lab, ("long", "short"), fills=fills, gate=gate)
        BO.line("  숏까지 켠 경우", *both)
        by_side(both[1], "숏까지 켠 경우")
        print(f"\n  → 숏을 켜면 누적이 {cur[0].iloc[-1] * 100 - 100:+.0f}% 에서 "
              f"{both[0].iloc[-1] * 100 - 100:+.0f}% 로 바뀐다 (research_both_sides.txt)")

    print("\n\n═══ 국면별 (현재 설정) ═══")
    BO.by_regime("지금 이 봇", cur[1])

    print("\n\n═══ 연도별 수익률 (현재 설정) ═══")
    d = cur[0].resample("YE").last()
    yr = (d / d.shift(1).fillna(1) - 1) * 100
    print("   " + " · ".join(f"{t.year} {v:+.0f}%" for t, v in yr.items()))

    if "--lev" in sys.argv:
        print("\n\n═══ 레버리지 스윕 — 어디까지 살아남나 ═══")
        print("  격리 마진이라 진입가 대비 1/lev 만큼 역행하면 그 포지션은 전액 손실이다.")
        print("  10배면 -10%, 5배면 -20%. 4h 봉 하나 안에서 일어날 수 있는 크기다.\n")
        print(f"  {'상한':>5}{'평균lev':>8}{'누적':>12}{'MDD':>9}{'Sh':>7}{'거래':>6}{'승률':>6}{'최악':>8}{'청산':>6}")
        for L in (1, 2, 3, 5, 7, 10, 15, 20):
            c, t, st = BO.simulate(h4, sig, lambda i, cv, L=L: L, lab, allow, fills=fills, gate=gate)
            f, lq = Q.metrics(c, t.ret.values if len(t) else np.zeros(1), st), Q._liq(st)
            print(f"  {L:>4}배{L:>8.2f}{f['누적']:>+11.0f}%{f['mdd']:>8.1f}%{f['sharpe']:>7.2f}"
                  f"{f['거래']:>6}{f['승률']:>5.0f}%{f['최악']:>7.1f}%{lq:>5}"
                  + (f"   ← 강제청산 {lq}건" if lq else ""))

        print(f"\n  변동성 타겟({VT_PCT:g}%·창{VT_WIN}일)을 얹고 상한만 올리면:")
        print(f"  {'상한':>5}{'평균lev':>8}{'누적':>12}{'MDD':>9}{'Sh':>7}{'거래':>6}{'승률':>6}{'최악':>8}{'청산':>6}")
        for L in (3, 5, 7, 10, 15, 20):
            levs = []

            def vt(i, cv, L=L):
                v = Q.acct_vol(i, cv, VT_WIN)
                x = L if v is None else min(L, (VT_PCT or 40) / v)
                levs.append(x)
                return x

            c, t, st = BO.simulate(h4, sig, vt, lab, allow, fills=fills, gate=gate)
            f, lq = Q.metrics(c, t.ret.values if len(t) else np.zeros(1), st), Q._liq(st)
            print(f"  {L:>4}배{np.mean(levs):>8.2f}{f['누적']:>+11.0f}%{f['mdd']:>8.1f}%{f['sharpe']:>7.2f}"
                  f"{f['거래']:>6}{f['승률']:>5.0f}%{f['최악']:>7.1f}%{lq:>5}"
                  + (f"   ← 강제청산 {lq}건" if lq else ""))

        # 실제로 진입 뒤 얼마나 역행했는지 — 청산선이 어디에 놓이는지 눈으로 본다
        base = BO.simulate(h4, sig, lambda i, c: 1, lab, allow, fills=fills, gate=gate)
        o, lo = h4.open.values, h4.low.values
        l_en2, l_ex2 = sig[0], sig[1]
        worst, held = [], None
        for i in range(1, len(h4)):
            if held is not None:
                e, mn = held
                if l_ex2[i - 1]:                      # 이 봉은 시가에 청산된다 → 이 봉의 저가는 남의 것
                    worst.append(mn * 100)
                    held = None
                else:
                    held = (e, min(mn, lo[i] / e - 1))
            if held is None and l_en2[i - 1]:
                f = o[i] if fills is None else fills.get(h4.index[i], o[i])
                px, lo_a = (f[0], f[1]) if isinstance(f, tuple) else (f, lo[i])
                if px is not None:                    # 진입한 봉은 **체결 후** 저가만 이 포지션의 것이다
                    held = (px, min(0.0, lo_a / px - 1))
        w = np.array(worst)
        print(f"\n  거래 {len(w)}건의 '진입 후 최대 역행' 분포 (저가 기준, 배율 무관):")
        for q in (50, 75, 90, 95, 99, 100):
            print(f"    {q:3d}분위 {np.percentile(w, 100 - q):+7.1f}%"
                  + f"   → {100 / abs(np.percentile(w, 100 - q)):.1f}배 넘으면 이 거래에서 청산" if q >= 90 else "")
        for L in (3, 5, 10, 20):
            print(f"    {L:2d}배 청산선 -{100 / L:.0f}% 를 넘긴 거래: {(w <= -100 / L).sum()}건 / {len(w)}건")

    # 자체 점검: 숏이 꺼져 있으면 숏 거래가 하나도 없어야 한다
    if not SHORT:
        assert (cur[1].side == "숏").sum() == 0, "ALLOW_SHORT=0 인데 숏 거래가 잡혔다"
    assert 0 <= Q.COST < 0.05
    print("\nok  자체 점검 통과 (설정과 거래 방향 일치)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
