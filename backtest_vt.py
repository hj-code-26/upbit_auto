"""변동성 타겟팅 재측정 — 라이브가 고정 5배인데 VT 가 꺼져 있다 (2026-09-21).

왜 다시 재는가
  research_quant_transfer.txt 는 VT 를 "탐색·검증 두 구간 모두에서 이긴 유일한 기법" 으로 채택했다.
  audit/AUDIT.md 는 더 세게 적었다 — 고정 5배는 **상위 5개 거래를 빼면 누적 -26%** 이고 block bootstrap
  하위 5분위 CAGR -14%(음수 10%) 인데, VT40 은 상위5 제외 +146% · 하위 5분위 +18%(음수 0%) 였다.
  그런데 지금 .env 는 LEVERAGE=5 · **VOL_TARGET_PCT=0** 이다. 채택된 기법이 꺼져 있다.
  그 판단들은 **상한 3배** 기준이었다. 상한이 5배로 바뀌었으니 같은 결론인지 다시 재야 한다.

  ※ 이 파일은 새 기법을 찾지 않는다. 이미 채택됐던 것이 바뀐 설정에서도 유효한지만 본다.

조건 (전부 지금 봇과 같게)
  진입 = 분봉 하한 재확보(research_entry_timing.txt, backtest_quant.reclaim_fills) · 상한 5배 ·
  편도 0.07% · 펀딩 0.01%/8h · 격리 청산 · CONF=6 · 롱 전용.
  레버리지 = min(5, 타겟% / 계좌 실현변동성%). 진입 시점에 정하고 그 거래 동안 고정. 깎기만 하고 올리지 않는다.
  계좌 이력이 창의 절반도 없으면 타겟팅이 쉬어 간다(=상한 그대로). 실계좌는 지금 딱 그 구간이다.

**채택값은 미리 정한다 — 격자에서 고르지 않는다**
  타겟 40% · 창 40일. 2026-09-10 에 이미 그 값으로 확정했었다(창 60→40 수정 포함).
  아래 격자는 그 값이 특이점이 아님을 보이는 용도일 뿐, 격자 최고점으로 갈아타지 않는다.
  갈아타면 그건 사후 선택이고, 이 리포는 EXTREME_MIN 에서 이미 그 대가를 치렀다(research_bias.txt).

=== 사전 등록 판정 (ADOPT = (1)~(6) 모두 충족) ===
  (1) 검증(2024-01~) Sharpe > 고정 5배
  (2) 탐색(~2024-01) Sharpe >= 고정 5배
  (3) 전체 MDD 개선
  (4) 격자 타겟20~60 x 창30~120 (20조합) 중 검증 Sharpe 가 고정 5배를 넘는 비율 >= 80%
  (5) 강제청산 횟수 <= 고정 5배
  (6) 꼬리: 상위 5개 거래를 뺀 누적이 고정 5배보다 개선 (audit 의 결정적 통계)
  하나라도 미달이면 REJECT = 고정 5배 유지. 결과를 보고 타겟/창/판정을 바꾸지 않는다.
  시도 수 N = 1(주 설정) + 20(격자) + 1(꼬리) = 22.

불변식 (assert)
  (a) 타겟을 무한대로 두면 고정 5배와 곡선이 일치한다
  (b) 타겟이 작을수록 평균 레버리지가 단조 감소한다
  (c) 모든 진입의 레버리지가 (0, 5] 안에 있다

사용: python backtest_vt.py   (make vt)
결과: research_vt.txt
"""
import sys

import numpy as np
import pandas as pd

from isolation import guard

guard()

import backtest_quant as Q                                          # noqa: E402
import model as M                                                   # noqa: E402

NL = chr(10)                    # 줄바꿈 (이 파일은 생성 스크립트로 만들어져 이스케이프를 피한다)
CAP = 5.0                       # .env LEVERAGE. 타겟팅은 이 아래로 깎기만 한다
TG, WIN = 40, 40                # 사전등록한 채택 후보값 (격자에서 고르지 않는다)
GRID_TG, GRID_WIN = (20, 30, 40, 50, 60), (30, 40, 60, 90)
START, SPLIT = Q.START, Q.SPLIT


def vt(tg, win):
    """lev_of: min(CAP, 타겟/계좌 실현변동성). 이력 부족하면 상한 그대로(타겟팅 쉬어 감)."""
    def f(i, c):
        v = Q.acct_vol(i, c, win)
        return CAP if v is None else min(CAP, tg / v)
    return f


def levs(h4, en, ex, lev_of, fills):
    """실제로 걸린 레버리지들 — 불변식 (b)(c) 용. engine 과 같은 자리에서 뽑는다."""
    out = []
    cur = Q.simulate(h4, en, ex, lambda i, c: (out.append(lev_of(i, c)) or lev_of(i, c)), fills=fills)
    return np.array(out), cur


def tail(trades, k=5):
    """상위 k개 거래를 빼고 다시 복리 — audit 의 결정적 통계. 몇 번의 대박에 기댄 곡선인지 본다."""
    r = np.sort(trades)[:-k] if len(trades) > k else np.array([0.0])
    return (np.prod(1 + r) - 1) * 100


def main():
    h4, entry, exit_, btc_vol, _ = Q.frames()
    m = h4.index >= pd.Timestamp(START, tz="UTC")
    h4, entry, exit_ = h4[m], entry[m], exit_[m]
    fl = Q.reclaim_fills()
    print(f"OKX BTC 4h {h4.index[0]:%Y-%m-%d}~{h4.index[-1]:%Y-%m-%d} · CONF={M.CONF} · 편도 {Q.COST*100:.2f}%"
          f" · 상한 {CAP:.0f}배 · 진입 분봉 하한 재확보")
    print(f"채택 후보 = 타겟 {TG}% · 창 {WIN}일 (사전등록. 격자에서 고르지 않는다)")

    base = Q.simulate(h4, entry, exit_, lambda i, c: CAP, fills=fl)
    cand = Q.simulate(h4, entry, exit_, vt(TG, WIN), fills=fl)

    lv_inf, _ = levs(h4, entry, exit_, vt(10 ** 9, WIN), fl)
    assert abs(Q.simulate(h4, entry, exit_, vt(10 ** 9, WIN), fills=fl)[0].iloc[-1] - base[0].iloc[-1]) < 1e-9,         "(a) 타겟 무한대 != 고정 5배"
    means = []
    for t in (10, 20, 40, 80, 160):
        lv, _ = levs(h4, entry, exit_, vt(t, WIN), fl)
        assert ((lv > 0) & (lv <= CAP + 1e-12)).all(), f"(c) 레버리지가 (0,{CAP}] 를 벗어났다: {lv.min()}~{lv.max()}"
        means.append(lv.mean())
    assert all(a <= b + 1e-12 for a, b in zip(means, means[1:])), f"(b) 평균 레버리지가 단조가 아니다: {means}"
    print(f"ok  불변식 (a) 타겟∞=고정 · (b) 평균레버리지 단조 {['%.2f' % x for x in means]} · (c) 범위 (0,{CAP:.0f}]")

    print(f"{NL}{'':24s}{'──── 탐색 ────':>22} |{'──── 검증 ────':>22} |{'────── 전체 ──────':>28}"
          f"{'거래':>5}{'승률':>6}{'최악':>7}{'청산':>4}")
    print(f"{'':24s}{'CAGR':>7}{'MDD':>7}{'Sh':>6} |{'CAGR':>7}{'MDD':>7}{'Sh':>6} |"
          f"{'누적':>9}{'MDD':>7}{'Sh':>6}")
    rep = lambda n, r: Q.report(n, *r)   # Q.simulate 는 (곡선, 거래수익률 배열, stats) 를 준다
    print(f"{NL}── 기준선 vs 채택 후보 ──")
    rep(f"고정 {CAP:.0f}배 (지금 라이브)", base)
    rep(f"VT {TG}%·창{WIN}일", cand)

    lv, _ = levs(h4, entry, exit_, vt(TG, WIN), fl)
    print(f"   실제 걸린 레버리지: 평균 {lv.mean():.2f}배 · 중앙값 {np.median(lv):.2f} · "
          f"상한 그대로 쓴 진입 {(lv > CAP - 1e-9).mean()*100:.0f}%")

    print(f"{NL}── (4) 격자: 타겟 × 창 ──")
    bv = Q.metrics(base[0], base[1], base[2], SPLIT, None)["sharpe"]
    hits = 0
    for win in GRID_WIN:
        for tg in GRID_TG:
            r = Q.simulate(h4, entry, exit_, vt(tg, win), fills=fl)
            hits += Q.metrics(r[0], r[1], r[2], SPLIT, None)["sharpe"] > bv
            rep(f"VT {tg}%·창{win}일", r)
        print()
    rate = hits / (len(GRID_TG) * len(GRID_WIN)) * 100

    f = lambda r, lo, hi: Q.metrics(r[0], r[1], r[2], lo, hi)
    bt, bvm, bf = f(base, START, SPLIT), f(base, SPLIT, None), f(base, START, None)
    ct, cv, cf = f(cand, START, SPLIT), f(cand, SPLIT, None), f(cand, START, None)
    tb, tc = tail(base[1]), tail(cand[1])
    checks = [("(1) 검증 Sharpe >", cv["sharpe"] > bvm["sharpe"], f"{cv['sharpe']:.2f} vs {bvm['sharpe']:.2f}"),
              ("(2) 탐색 Sharpe >=", ct["sharpe"] >= bt["sharpe"], f"{ct['sharpe']:.2f} vs {bt['sharpe']:.2f}"),
              ("(3) 전체 MDD 개선", cf["mdd"] > bf["mdd"], f"{cf['mdd']:.1f}% vs {bf['mdd']:.1f}%"),
              ("(4) 격자 >=80%", rate >= 80, f"{rate:.0f}%"),
              ("(5) 청산 <=", cand[2]["liq"] <= base[2]["liq"], f"{cand[2]['liq']} vs {base[2]['liq']}"),
              ("(6) 상위5 제외 누적", tc > tb, f"{tc:+.0f}% vs {tb:+.0f}%")]
    print(f"{NL}═══ 사전 등록 판정 ═══")
    for n, v, d in checks:
        print(f"  {n:20s} {'PASS' if v else 'FAIL'}  ({d})")
    ok = all(v for _, v, _ in checks)
    print(f"{NL}  → {'ADOPT — .env VOL_TARGET_PCT=%d · VOL_WINDOW=%d 로 켠다' % (TG, WIN) if ok else 'REJECT — 고정 5배 유지'}")
    print("  시도 수 N = 22 (사전등록과 같음).")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
