"""변동성 타겟팅 — 창·타겟을 **워크포워드로** 고른다 (2026-09-21). backtest_vt.py 의 후속.

왜 필요한가
  backtest_vt.py 는 사전등록 좌표(타겟40%·창40일)로 REJECT 났다. 그런데 격자를 보니 그 좌표가
  상한 5배에서 나쁜 쪽 축이었고, 창60일은 탐색·검증 두 구간 모두 Sharpe 1.07 로 기준선(0.74/0.69)을 압도했다.
  **여기서 창60 으로 갈아타면 사후 선택이다** (research_bias.txt 의 EXTREME_MIN 과 똑같은 실수).
  그래서 "어느 칸이 좋았나" 를 묻지 않고, **그때그때 과거만 보고 고르면 어땠나** 를 묻는다.
  이건 좌표를 고르는 문제가 아니라 규칙을 하나 더 얹는 문제이고, 사후 선택 없이 판정할 수 있다.

규칙 (전부 미리 고정)
  후보 = backtest_vt.py 의 격자 20칸 그대로 (타겟 20/30/40/50/60 x 창 30/40/60/90). 여기서 더하거나 빼지 않는다.
  선택 = 재선택 시점 t 까지의 **자기 자본곡선 Sharpe 최대**. engine.run 은 인과적이라 t 시점 곡선값은
         t 이전 데이터만 쓴다 → 후보 20개를 전 구간 한 번씩 돌려 놓고 [START, t] 로 잘라 재도 누설이 없다.
  워밍업 = 첫 365일은 선택 근거가 없다 → **가장 보수적인 칸(타겟20%·창90일)** 을 쓴다. 유리한 쪽이 아니라 불리한 쪽으로 고정.
  재선택 주기 = 180일 (주 설정). 90일·365일은 견고성 확인용.
  평가는 워밍업을 **포함한** 전 구간으로 한다. 워밍업을 빼면 성적이 좋아지므로 포함이 불리한 쪽이다.

=== 사전 등록 판정 (ADOPT = (1)~(6) 모두 충족) ===
  (1) 검증(2024-01~) Sharpe > 고정 5배
  (2) 탐색(~2024-01) Sharpe >= 고정 5배
  (3) 전체 MDD 개선
  (4) 재선택 주기 90 · 180 · 365일 **셋 다** 에서 (1)(3) 유지
  (5) 강제청산 <= 고정 5배
  (6) 상위 5개 거래 제외 누적이 고정 5배보다 개선
  미달이면 REJECT = 고정 5배 유지. 결과를 보고 후보·주기·워밍업 규칙을 바꾸지 않는다.
  시도 수 N = 3(주기) + 1(대조: 사후 최적 고정칸) = 4. (후보 20칸은 backtest_vt.py 의 N=22 에 이미 셈했다.)

  ※ 대조군으로 '사후 최적 고정칸' 도 같이 찍는다. 워크포워드가 그것보다 나쁜 것은 당연하다 —
    그건 미래를 본 값이다. 판정에는 **쓰지 않는다**. 워크포워드가 이겨야 하는 상대는 고정 5배뿐이다.

불변식 (assert)
  (a) 후보가 1칸뿐이면 워크포워드 = 그 칸 고정과 일치한다
  (b) 선택 이력의 모든 (타겟, 창) 이 후보 집합 안에 있다
  (c) 재선택 시점 이전 구간의 선택은 재선택 주기를 바꿔도 워밍업 동안 동일하다

사용: python backtest_vt_wf.py   (make vtwf)
결과: research_vt.txt 에 이어 붙인다
"""
import sys

import numpy as np
import pandas as pd

from isolation import guard

guard()

import backtest_quant as Q                                          # noqa: E402
import backtest_vt as V                                             # noqa: E402
import model as M                                                   # noqa: E402

NL = chr(10)
CAP, BPD = V.CAP, Q.BPD
CANDS = [(tg, win) for win in V.GRID_WIN for tg in V.GRID_TG]       # 후보 20칸 (vt 격자 그대로)
WARM_D, WARMUP = 365, (20, 90)          # 워밍업 1년 · 그동안 가장 보수적인 칸
PERIODS = (90, 180, 365)                # 주 설정 180일, 나머지는 견고성
START, SPLIT = Q.START, Q.SPLIT


def sharpe_to(curve, i):
    """[처음, i] 구간 자본곡선의 Sharpe. engine.run 이 인과적이라 i 시점 값은 i 이전 데이터만 쓴다."""
    d = curve.iloc[:i].resample("D").last().dropna()
    if len(d) < 60 or (d <= 0).any():
        return -9e9
    r = d.pct_change().dropna()
    yrs = len(d) / Q.YEAR
    if yrs <= 0 or d.iloc[-1] / d.iloc[0] <= 0:
        return -9e9
    cagr = ((d.iloc[-1] / d.iloc[0]) ** (1 / yrs) - 1) * 100
    v = r.std() * np.sqrt(Q.YEAR) * 100
    return cagr / v if v else -9e9


def selections(h4, cand_curves, period_d):
    """봉마다 그 시점에 쓰는 (타겟, 창). 워밍업 동안은 고정, 그 뒤 period_d 마다 과거 Sharpe 최대로 재선택."""
    cands = list(cand_curves)                       # 불변식 (a) 는 후보를 1칸으로 줄여서 부른다
    sel = [WARMUP] * len(h4)
    cur, warm = (WARMUP if WARMUP in cands else cands[0]), WARM_D * BPD
    for i in range(len(h4)):
        if i >= warm and (i - warm) % (period_d * BPD) == 0:
            cur = max(cands, key=lambda c: sharpe_to(cand_curves[c], i))
        sel[i] = cur
    return sel


def walk(h4, en, ex, fills, sel):
    """실제 경로 한 번. 레버리지는 **진행 중인 자기 계좌** 변동성으로 잰다 (선택만 미리 계산된 것)."""
    def lev_of(i, c):
        tg, win = sel[i - 1]
        v = Q.acct_vol(i, c, win)
        return CAP if v is None else min(CAP, tg / v)
    return Q.simulate(h4, en, ex, lev_of, fills=fills)


def main():
    h4, entry, exit_, _, _ = Q.frames()
    m = h4.index >= pd.Timestamp(START, tz="UTC")
    h4, entry, exit_ = h4[m], entry[m], exit_[m]
    fl = Q.reclaim_fills()
    print(f"OKX BTC 4h {h4.index[0]:%Y-%m-%d}~{h4.index[-1]:%Y-%m-%d} · CONF={M.CONF} · 상한 {CAP:.0f}배 · 후보 {len(CANDS)}칸")
    print(f"워밍업 {WARM_D}일은 가장 보수적인 칸 {WARMUP} 고정 · 평가는 워밍업 포함")

    base = Q.simulate(h4, entry, exit_, lambda i, c: CAP, fills=fl)
    cc = {c: Q.simulate(h4, entry, exit_, V.vt(*c), fills=fl)[0] for c in CANDS}   # 선택 점수용 (수익은 여기서 안 쓴다)

    one = selections(h4, {WARMUP: cc[WARMUP]}, 180)
    assert all(s == WARMUP for s in one), "(b) 후보가 1칸인데 다른 칸이 선택됐다"
    w1 = walk(h4, entry, exit_, fl, one)
    fix = Q.simulate(h4, entry, exit_, V.vt(*WARMUP), fills=fl)
    assert abs(w1[0].iloc[-1] - fix[0].iloc[-1]) < 1e-9, "(a) 후보 1칸 워크포워드 != 그 칸 고정"

    sels = {p: selections(h4, cc, p) for p in PERIODS}
    for p, s in sels.items():
        assert all(x in CANDS for x in s), "(b) 후보 밖의 칸이 선택됐다"
    warm = WARM_D * BPD
    assert all(sels[90][i] == sels[365][i] for i in range(warm)), "(c) 워밍업 구간 선택이 주기에 따라 다르다"
    print(f"ok  불변식 (a) 1칸=고정 · (b) 선택은 후보 안 · (c) 워밍업 구간 주기 무관")

    print(f"{NL}{'':24s}{'──── 탐색 ────':>22} |{'──── 검증 ────':>22} |{'────── 전체 ──────':>28}"
          f"{'거래':>5}{'승률':>6}{'최악':>7}{'청산':>4}")
    print(f"{'':24s}{'CAGR':>7}{'MDD':>7}{'Sh':>6} |{'CAGR':>7}{'MDD':>7}{'Sh':>6} |"
          f"{'누적':>9}{'MDD':>7}{'Sh':>6}")
    rep = lambda n, r: Q.report(n, *r)
    f = lambda r, lo, hi: Q.metrics(r[0], r[1], r[2], lo, hi)

    print(f"{NL}── 기준선 ──")
    rep(f"고정 {CAP:.0f}배 (지금 라이브)", base)
    print(f"{NL}── 워크포워드 (재선택 주기별) ──")
    W = {}
    for p in PERIODS:
        W[p] = walk(h4, entry, exit_, fl, sels[p])
        rep(f"WF 주기 {p}일", W[p])
        picks = [s for i, s in enumerate(sels[p]) if i >= warm]
        uniq = sorted(set(picks), key=picks.index)
        print(f"   고른 칸: " + " → ".join(f"{t}%/{w}일" for t, w in uniq))

    print(f"{NL}── [대조군, 판정에 쓰지 않음] 사후 최적 고정칸 ──")
    best = max(CANDS, key=lambda c: f((cc[c], Q.simulate(h4, entry, exit_, V.vt(*c), fills=fl)[1],
                                       Q.simulate(h4, entry, exit_, V.vt(*c), fills=fl)[2]), START, None)["sharpe"])
    rep(f"사후최적 {best[0]}%/{best[1]}일", Q.simulate(h4, entry, exit_, V.vt(*best), fills=fl))

    main_w = W[180]
    bt, bv, bf = f(base, START, SPLIT), f(base, SPLIT, None), f(base, START, None)
    ct, cv, cf = f(main_w, START, SPLIT), f(main_w, SPLIT, None), f(main_w, START, None)
    grid_ok = all(f(W[p], SPLIT, None)["sharpe"] > bv["sharpe"] and f(W[p], START, None)["mdd"] > bf["mdd"]
                  for p in PERIODS)
    tb, tc = V.tail(base[1]), V.tail(main_w[1])
    checks = [("(1) 검증 Sharpe >", cv["sharpe"] > bv["sharpe"], f"{cv['sharpe']:.2f} vs {bv['sharpe']:.2f}"),
              ("(2) 탐색 Sharpe >=", ct["sharpe"] >= bt["sharpe"], f"{ct['sharpe']:.2f} vs {bt['sharpe']:.2f}"),
              ("(3) 전체 MDD 개선", cf["mdd"] > bf["mdd"], f"{cf['mdd']:.1f}% vs {bf['mdd']:.1f}%"),
              ("(4) 주기 3개 유지", grid_ok, " · ".join(f"{p}일 Sh{f(W[p], SPLIT, None)['sharpe']:.2f}" for p in PERIODS)),
              ("(5) 청산 <=", main_w[2]["liq"] <= base[2]["liq"], f"{main_w[2]['liq']} vs {base[2]['liq']}"),
              ("(6) 상위5 제외 누적", tc > tb, f"{tc:+.0f}% vs {tb:+.0f}%")]
    print(f"{NL}═══ 사전 등록 판정 (주 설정 = 재선택 주기 180일) ═══")
    for n, v, d in checks:
        print(f"  {n:20s} {'PASS' if v else 'FAIL'}  ({d})")
    ok = all(v for _, v, _ in checks)
    print(f"{NL}  → {'ADOPT' if ok else 'REJECT — 고정 5배 유지'}")
    print("  시도 수 N = 4 (사전등록과 같음).")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
