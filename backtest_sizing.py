"""사이징 후보 비교 · 지표 중복 제거 · 불확실성 · 스트레스 (2026-09-10 감사 요청 §3~§4).

**전부 미검증 가설이다.** 기준선은 지금 봇 규칙(롱 전용, 분봉 하한 재확보 진입) 그대로 두고,
'명목을 얼마로 할 것인가' 만 바꿔 비교한다. 숏은 검증 통과 전까지 OFF (research_both_sides.txt: 누적 −100%).

후보 1  BTC 기초자산 변동성 노출:  L = min(Lmax, target_vol / asset_vol)
        계좌 변동성 방식(지금 .env 의 VOL_TARGET_PCT)과 따로 비교한다.
        이력이 없거나 거래가 없을 때 **최대 레버리지로 복귀하지 않는다** — 보수적 cap(WARMUP_CAP)으로 간다.
        진입 시점에 정하고 보유 중에는 바꾸지 않는다 (거래소가 포지션 단위로 레버리지를 걸어서다 → 회전비용 0).
후보 2  거래별 손실예산:  notional ≤ equity × risk_budget / (stop_distance% + cost_stress%)
        stop_distance = 진입가에서 청산선(직전 6봉 저가)까지의 거리. 여기에 레버리지 cap 을 또 씌운다.
        연구 범위 risk_budget 0.25~0.5% · 유효 레버리지 1~2배는 **검증된 최적값이 아니라 출발점**이다.
후보 3·4 (ATR 재난손절 · 국면 hysteresis · 독립 숏 가설) 는 이 파일에 없다 — audit/AUDIT.md '남은 일' 참고.

§5  지표 중복: close>SMA20 과 볼린저 %B>0.5 는 표준편차가 양수면 **같은 조건**이다. 실제로 같은지 세고,
    중복을 뺀 7규칙에서 CONF 를 바꿔 가며 ablation 한다 (CONF 를 그대로 두지 않는다).
§4  불확실성: 일별 수익률 block bootstrap. 소표본에서 파산확률을 정밀 추정하지는 않는다.
    스트레스: 비용 1/2/3배 · 체결 지연 1/10/30분(4h 봉 근사) · 펀딩 3배.

사용: python backtest_sizing.py            후보 비교 + ablation + 스트레스
      python backtest_sizing.py --boot     block bootstrap 까지 (느리다)
"""
import sys

import numpy as np
import pandas as pd

import backtest_both as BO
import backtest_okx as B
import backtest_quant as Q
import engine as E
import model as M

LMAX = 5                  # .env 의 LEVERAGE 상한과 같은 값
WARMUP_CAP = 1.0          # 이력이 모자랄 때 쓰는 보수적 레버리지 (예전엔 최대치로 복귀했다)
YEAR = 365


def setup():
    """(4h 봉, 신호 4종, 국면라벨, 분봉 체결, BTC 실현변동성, 손절거리%) — 전부 과거 정보만."""
    h4, l_en, l_ex, s_en, s_ex, lab = BO.frames()
    d1 = B.fetch("1d")
    btc_vol = (d1.close.pct_change().rolling(60).std() * np.sqrt(YEAR) * 100).shift(1)
    btc_vol = btc_vol.reindex(h4.index, method="ffill").values
    lo_m = h4.low.rolling(M.H4_M).min().shift(1)                    # 진입 시점의 청산선
    stop = ((h4.open - lo_m) / h4.open * 100).clip(lower=0.5).values  # 시가 기준 손절 거리 %
    m = h4.index >= pd.Timestamp(Q.START, tz="UTC")
    fills = Q.reclaim_fills()
    return (h4[m], (l_en[m], l_ex[m], s_en[m], s_ex[m]), lab[m], fills, btc_vol[m], stop[m])


def run(h4, sig, lab, lev_of, fills, **kw):
    return E.run(h4, *sig, lev_of, lab=lab, allow=("long",), fills=fills, **kw)


def line(name, r):
    Q.report(name, r[0], r[1].ret.values if len(r[1]) else np.zeros(1), r[2])


def header():
    print(f"\n{'':24s}{'──── 탐색 ────':>22} |{'──── 검증 ────':>22} |{'────── 전체 ──────':>28}"
          f"{'거래':>5}{'승률':>6}{'최악':>7}{'청산':>4}")
    print(f"{'':24s}{'CAGR':>7}{'MDD':>7}{'Sh':>6} |{'CAGR':>7}{'MDD':>7}{'Sh':>6} |"
          f"{'누적':>9}{'MDD':>7}{'Sh':>6}")


def sharpe_std(curve, lo=None, hi=None):
    """표준 Sharpe = mean(일별 초과수익) / std × √365. Q.metrics 의 CAGR/변동성 과는 다른 값이다."""
    d = curve[lo:hi].resample("D").last().dropna()
    r = d.pct_change().dropna()
    r = r[np.isfinite(r)]
    return float(r.mean() / r.std() * np.sqrt(YEAR)) if len(r) > 2 and r.std() > 0 else 0.0


def stats_block(name, curve, t):
    """§4 가 요구한 보고 항목. Sharpe 는 표준 정의로 따로 낸다."""
    d = curve.resample("D").last().dropna()
    r = t.ret.values if len(t) else np.zeros(1)
    win, loss = r[r > 0], r[r <= 0]
    top5 = np.sort(r)[::-1][:5]
    ex_top5 = np.prod(1 + np.sort(r)[::-1][5:]) - 1 if len(r) > 5 else 0.0
    streak = mx = 0
    for x in r:
        streak = streak + 1 if x <= 0 else 0
        mx = max(mx, streak)
    print(f"\n[{name}]")
    print(f"  순수익 {curve.iloc[-1] * 100 - 100:+.0f}%  ·  MDD(일별) {(d / d.cummax() - 1).min() * 100:.1f}%"
          f"  ·  MDD(4h) {(curve / curve.cummax() - 1).min() * 100:.1f}%")
    print(f"  표준 Sharpe {sharpe_std(curve):.2f} (탐색 {sharpe_std(curve, Q.START, Q.SPLIT):.2f} / "
          f"검증 {sharpe_std(curve, Q.SPLIT, None):.2f})  ·  Calmar {Q.metrics(curve, r, 0)['cagr'] / abs((d / d.cummax() - 1).min() * 100):.2f}")
    print(f"  거래 {len(r)}  승률 {(r > 0).mean() * 100:.0f}%  평균 순이익 {r.mean() * 100:+.2f}%  "
          f"평균이익 {win.mean() * 100 if len(win) else 0:+.2f}% / 평균손실 {loss.mean() * 100 if len(loss) else 0:+.2f}%")
    print(f"  profit factor {win.sum() / abs(loss.sum()) if len(loss) and loss.sum() else float('inf'):.2f}"
          f"  ·  최대 연속손실 {mx}회  ·  상위 5개 거래 {', '.join(f'{x * 100:+.0f}%' for x in top5)}")
    print(f"  상위 5개 거래를 빼면 누적 {ex_top5 * 100:+.0f}%   ← 몇 건에 얼마나 기대고 있는지")
    print(f"  회전율 {len(r) / ((curve.index[-1] - curve.index[0]).days / 365):.1f}회/년"
          f"  ·  롱 {(t.side == '롱').sum() if len(t) else 0}건 / 숏 {(t.side == '숏').sum() if len(t) else 0}건")


def main():
    h4, sig, lab, fills, btc_vol, stop = setup()
    print(f"OKX BTC 4h {h4.index[0]:%Y-%m-%d}~{h4.index[-1]:%Y-%m-%d} · CONF={M.CONF} · 편도 {Q.COST * 100:.2f}%"
          f" · 롱 전용 · 분봉 하한 재확보 진입")
    print("탐색/검증 분할은 이미 여러 번 열람했다 — **최종 미사용 검증셋이 아니다** (audit/AUDIT.md §4)")
    header()

    print("\n── 기준선 (지금 .env: 고정 5배) ──")
    base = run(h4, sig, lab, lambda i, c: LMAX, fills)
    line("고정 5배 (현재)", base)
    for L in (1, 2, 3):
        line(f"고정 {L}배", run(h4, sig, lab, lambda i, c, L=L: L, fills))

    print("\n── 후보 1a: 계좌 실현변동성 타겟 (워밍업은 보수적 cap) ──")
    for tg in (20, 30, 40, 60):
        def lev_acct(i, c, tg=tg):
            v = Q.acct_vol(i, c, 40)
            return WARMUP_CAP if v is None else min(LMAX, tg / v)
        line(f"VT(계좌) {tg}%", run(h4, sig, lab, lev_acct, fills))

    print("\n── 후보 1b: BTC 기초자산 변동성 타겟 ──")
    for tg in (20, 30, 40, 60, 80):
        def lev_btc(i, c, tg=tg):
            v = btc_vol[i]
            return WARMUP_CAP if not np.isfinite(v) or v <= 0 else min(LMAX, tg / v)
        line(f"VT(BTC) {tg}%", run(h4, sig, lab, lev_btc, fills))

    print("\n── 후보 2: 거래별 손실예산 (stop = 진입가→직전 6봉 저가, 비용 스트레스 0.5% 가산) ──")
    print("   유효 레버리지 = risk_budget / (손절거리% + 0.5%). 레버리지 cap 을 또 씌운다.")
    for rb in (0.25, 0.5, 1.0, 2.0, 4.0):
        for cap in (2, 5):
            def lev_risk(i, c, rb=rb, cap=cap):
                return min(cap, LMAX, (rb / 100) / (stop[i] / 100 + 0.005))
            line(f"위험예산 {rb:.2f}% · cap {cap}배", run(h4, sig, lab, lev_risk, fills))
    print("   → 연구 범위로 제시된 risk_budget 0.25~0.5% 는 이 전략의 손절거리(중앙 1.6%)에서")
    print("      유효 레버리지 0.1~0.3배가 된다. '최대 유효 레버리지 1~2배' 와 앞뒤가 맞지 않는다.")
    print("      1~2배를 원하면 위험예산은 2~4% 대여야 한다. 이 값은 검증된 최적값이 아니다.")
    print("   (손절거리 분포: "
          + " · ".join(f"{q}분위 {np.nanpercentile(stop[np.isfinite(stop)], q):.1f}%" for q in (10, 50, 90)) + ")")

    print("\n\n═══ §5 지표 중복: close>SMA20 vs 볼린저 %B>0.5 ═══")
    d1 = B.fetch("1d")
    x = M.add_indicators(d1.copy())
    a, b = (x.close > x.SMA_20), (x["BBP_20_2.0_2.0"] > 0.5)
    both = a.notna() & b.notna()
    print(f"  일봉 {both.sum()}개 중 두 규칙이 다른 날: {int((a[both] != b[both]).sum())}일 "
          f"→ 표준편차가 0 이 아닌 한 **같은 조건**이다. 8규칙이 아니라 사실상 7규칙이다.")
    print("  중복을 빼면 같은 CONF 가 더 엄격해진다. CONF 를 그대로 두면 안 된다 → ablation:")
    header()
    keep = M.RULES
    try:
        for conf in (5, 6, 7):
            for tag, rules in (("8규칙(현행)", keep), ("7규칙(중복제거)", [r for r in keep if "볼린저" not in r[0]])):
                M.RULES = rules
                M.CONF, old = conf, M.CONF
                h2, s2, l2, f2, _, _ = setup()
                line(f"{tag} CONF={conf}", run(h2, s2, l2, lambda i, c: LMAX, f2))
                M.CONF = old
    finally:
        M.RULES, M.CONF = keep, M.CONF

    print("\n\n═══ §4 스트레스 (기준선 = 고정 5배) ═══")
    header()
    real = B.COST
    try:
        for k in (1, 2, 3):
            B.COST = real * k
            line(f"비용 {k}배 (편도 {B.COST * 100:.2f}%)", run(h4, sig, lab, lambda i, c: LMAX, fills))
    finally:
        B.COST = real
    real_f = E.FUND
    try:
        for k in (1, 3, 10):
            E.FUND = real_f * k
            line(f"펀딩 {k}배", run(h4, sig, lab, lambda i, c: LMAX, fills))
    finally:
        E.FUND = real_f
    print("   체결 지연: 분봉 체결을 끄면(=다음 4h 시가 시장가) 최대 4시간 지연과 같다")
    line("지연 최대(4h 시가)", run(h4, sig, lab, lambda i, c: LMAX, None))
    print("   ※ 1/10/30분 지연은 분봉 창을 그만큼 밀어야 정확하다 — 미측정 (audit/AUDIT.md '남은 일')")

    print("\n\n═══ §4 상세 통계 ═══")
    stats_block("고정 5배 (현재 설정)", base[0], base[1])
    vt40 = run(h4, sig, lab, lambda i, c: (lambda v: WARMUP_CAP if v is None else min(LMAX, 40 / v))(Q.acct_vol(i, c, 40)), fills)
    stats_block("VT(계좌) 40% · 상한 5배", vt40[0], vt40[1])

    if "--boot" in sys.argv:
        print("\n\n═══ §4 block bootstrap (일별 수익률, 블록 20일, 2000회) ═══")
        print("   자기상관·거래 군집을 살린 채 표본을 다시 뽑는다. 소표본 파산확률은 추정하지 않는다.")
        for name, r in (("고정 5배", base), ("VT(계좌) 40%", vt40)):
            d = r[0].resample("D").last().dropna().pct_change().dropna().values
            rng, out = np.random.default_rng(0), []
            nb = len(d) // 20
            for _ in range(2000):
                st = rng.integers(0, len(d) - 20, nb)
                x = np.concatenate([d[j:j + 20] for j in st])
                out.append((np.prod(1 + x) ** (YEAR / len(x)) - 1) * 100)
            q = np.percentile(out, [5, 25, 50, 75, 95])
            print(f"  {name:16s} CAGR 5% {q[0]:+7.0f}% · 25% {q[1]:+7.0f}% · 중앙 {q[2]:+7.0f}% · "
                  f"75% {q[3]:+7.0f}% · 95% {q[4]:+7.0f}%   (음수 비율 {np.mean(np.array(out) < 0) * 100:.0f}%)")

    # 자체 점검
    assert abs(base[0].iloc[-1] - run(h4, sig, lab, lambda i, c: LMAX, fills)[0].iloc[-1]) < 1e-12, "같은 입력은 같은 결과"
    assert run(h4, sig, lab, lambda i, c: 0.0, fills)[0].iloc[-1] == 1.0, "레버리지 0 이면 무거래"
    print("\nok  자체 점검 통과 (재현성 · 레버리지 0 = 무거래)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
