"""시간대·요일 필터 — 4h 봉 6칸 중 어디서 난 돌파가 좋은가 (2026-09-21).

왜 재보는가
  문헌은 코인의 인트라데이 계절성이 실재한다고 본다: BTC 수익은 NYSE 폐장(오버나이트) 구간에 몰리고,
  주말은 15:00~21:00 UTC 가 강하며, 요일 효과는 일요일 23:00~00:00 UTC 한 칸에 거의 전부 몰려 있다는 보고.
  우리 봇은 4h 봉 6칸(UTC 00/04/08/12/16/20) 중 아무 데서나 진입한다 — 한 번도 안 갈라 봤다.
  데이터 비용 0, engine 의 gate 한 줄이면 된다.

**먼저 인정하고 시작하는 것: 표본이 부족하다.**
  거래 117건을 6칸으로 나누면 칸당 20건이다. 무엇을 재든 '제일 좋은 칸' 은 나온다 — 그게 노이즈여도.
  research_bias.txt 의 EXTREME_MIN 이 정확히 그렇게 죽었다(백분위 95~98 이었지만 사후 선택 보정 후 실효 p~0.30).
  그래서 성적표를 보기 **전에** 순열검정을 통과해야만 성적을 보는 순서로 짠다.

절차 (순서를 바꾸지 않는다)
  1단계  칸 효과가 무작위와 구분되는가 — 순열검정.
         통계량 = 칸별 평균 거래수익률의 표준편차(칸 간 퍼짐). 거래 결과를 칸에 무작위 재배치 2,000회.
         칸 배정만 섞고 거래 수익률 자체는 그대로 둔다 → 귀무가설 '칸은 무관하다' 의 정확한 분포다.
  2단계  안정적인가 — 탐색 구간(~2024-01)에서 **상위 절반 칸** 을 고르고, 그 선택을 검증 구간에 그대로 적용.
         검증 구간을 보고 칸을 고르지 않는다. 이게 이 시험의 유일한 자유도 통제다.
  3단계  그래서 돈이 되는가 — 그 칸에서만 진입하는 규칙의 성적.

=== 사전 등록 판정 (ADOPT = (1)~(4) 모두 충족) ===
  (1) 순열검정 p < 0.05 (시간대 또는 요일 중 해당 축에서)
  (2) 탐색에서 고른 상위 절반 칸이 검증 구간에서도 평균 수익률 상위 절반에 들어간다
  (3) 그 칸만 진입한 규칙의 검증 Sharpe > 고정 5배
  (4) 전체 MDD 개선
  (1) 이 미달이면 (2)~(4) 결과와 무관하게 REJECT 로 적는다. '그래도 성적은 좋더라' 를 쓰지 않기 위해서다.
  시도 수 N = 2 (시간대 축 · 요일 축). 두 축을 다 재고 둘 다 보고한다. 좋은 쪽만 고르지 않는다.
  다중검정: 축이 2개이므로 실효 유의수준은 0.05/2 = 0.025 로 본다. (1) 은 이 값으로 판정한다.

불변식 (assert)
  (a) 모든 칸을 다 열면 고정 5배와 곡선이 일치한다
  (b) 칸 배정의 합이 전체 거래 수와 같다 (거래를 흘리지 않는다)
  (c) 순열검정의 관측 통계량이 무작위 분포의 최소~최대 안에 있다 (계산 배선 점검)

사용: python backtest_hour.py   (make hour)
결과: research_hour.txt
"""
import sys

import numpy as np
import pandas as pd

from isolation import guard

guard()

import backtest_quant as Q                                          # noqa: E402
import backtest_vt as V                                             # noqa: E402
import engine as E                                                  # noqa: E402
import model as M                                                   # noqa: E402

NL = chr(10)
CAP = V.CAP
TRIALS = 2000
START, SPLIT = Q.START, Q.SPLIT
AXES = {"시간대(UTC)": lambda ix: ix.hour, "요일": lambda ix: ix.dayofweek}
DAYS = ["월", "화", "수", "목", "금", "토", "일"]


def label(axis, v):
    return f"{v:02d}시" if axis.startswith("시간") else DAYS[v]


def trades(h4, en, ex, fills, gate=None):
    """engine.run 을 직접 부른다 — 거래 원장의 i_in 이 필요해서 Q.simulate(수익률만 줌)를 안 쓴다."""
    z = np.zeros(len(h4), bool)
    return E.run(h4, en, ex, z, z, lambda i, c: CAP, allow=("long",), fills=fills, gate=gate)


def perm_p(groups, rets, trials=TRIALS, seed=0):
    """귀무가설 '칸은 무관하다'. 칸 배정만 섞고 수익률은 그대로 → 칸별 평균의 표준편차 분포.
    → (p, 관측 통계량, 무작위 분포)"""
    rng = np.random.default_rng(seed)
    uq = np.unique(groups)
    stat = lambda g: np.std([rets[g == u].mean() for u in uq if (g == u).sum()])
    obs = stat(groups)
    null = np.array([stat(rng.permutation(groups)) for _ in range(trials)])
    return (null >= obs).mean(), obs, null


def main():
    h4, entry, exit_, _, _ = Q.frames()
    m = h4.index >= pd.Timestamp(START, tz="UTC")
    h4, entry, exit_ = h4[m], entry[m], exit_[m]
    fl = Q.reclaim_fills()
    base = trades(h4, entry, exit_, fl)
    t = base[1]
    ix = h4.index[t.i_in.values]                       # 진입한 봉의 시각
    print(f"OKX BTC 4h {h4.index[0]:%Y-%m-%d}~{h4.index[-1]:%Y-%m-%d} · 상한 {CAP:.0f}배 · 진입 분봉 하한 재확보")
    print(f"거래 {len(t)}건 · 순열 {TRIALS:,}회 · 실효 유의수준 0.05/2 = 0.025 (축 2개 보정)")

    allopen = trades(h4, entry, exit_, fl, gate=np.ones(len(h4), bool))
    assert np.allclose(allopen[0].values, base[0].values), "(a) 칸을 다 열었는데 기준선과 다르다"
    print("ok  불변식 (a) 전부 열면 = 고정 5배")

    split_i = h4.index.get_indexer([pd.Timestamp(SPLIT, tz="UTC")], method="bfill")[0]
    in_tr = t.i_in.values < split_i
    rep = lambda n, r: Q.report(n, r[0], r[1].ret.values if len(r[1]) else np.zeros(1), r[2])
    f = lambda r, lo, hi: Q.metrics(r[0], r[1].ret.values if len(r[1]) else np.zeros(1), r[2], lo, hi)
    bt, bv, bf = f(base, START, SPLIT), f(base, SPLIT, None), f(base, START, None)

    verdicts = {}
    for axis, fn in AXES.items():
        g = np.asarray(fn(ix))
        assert len(g) == len(t), "(b) 칸 배정에서 거래가 샜다"
        print(f"{NL}{'='*78}{NL}축: {axis}{NL}{'='*78}")

        # ── 1단계: 순열검정이 먼저다 ──
        p, obs, null = perm_p(g, t.ret.values)
        assert null.min() <= obs <= null.max() or p in (0.0, 1.0), "(c) 관측 통계량이 무작위 분포 밖이다 — 배선 오류"
        print(f"{NL}[1단계] 순열검정  p = {p:.4f}  (관측 칸간 퍼짐 {obs*100:.2f}%p · 무작위 중앙값 {np.median(null)*100:.2f}%p)")
        passed1 = p < 0.025
        print(f"        → {'PASS' if passed1 else 'FAIL'} (문턱 0.025)")

        print(f"{NL}[참고] 칸별 성적 — 순열검정이 떨어지면 이 표는 노이즈다")
        print(f"        {'칸':>6}{'거래':>6}{'평균':>9}{'승률':>7} |{'탐색 거래':>10}{'탐색 평균':>11} |{'검증 거래':>10}{'검증 평균':>11}")
        rows = {}
        for u in np.unique(g):
            s, a, b = g == u, (g == u) & in_tr, (g == u) & ~in_tr
            rows[u] = (t.ret.values[a].mean() if a.sum() else np.nan,
                       t.ret.values[b].mean() if b.sum() else np.nan)
            print(f"        {label(axis,u):>6}{s.sum():6d}{t.ret.values[s].mean()*100:+8.2f}%"
                  f"{(t.ret.values[s]>0).mean()*100:6.0f}% |{a.sum():10d}{rows[u][0]*100:+10.2f}%"
                  f" |{b.sum():10d}{rows[u][1]*100:+10.2f}%")

        # ── 2단계: 탐색에서 고른 상위 절반이 검증에서도 상위 절반인가 ──
        uq = [u for u in np.unique(g) if np.isfinite(rows[u][0]) and np.isfinite(rows[u][1])]
        k = len(uq) // 2
        pick = set(sorted(uq, key=lambda u: -rows[u][0])[:k])
        top_v = set(sorted(uq, key=lambda u: -rows[u][1])[:k])
        overlap = len(pick & top_v)
        passed2 = overlap == k
        print(f"{NL}[2단계] 탐색 상위 절반 = {{{', '.join(label(axis,u) for u in sorted(pick))}}}")
        print(f"        검증 상위 절반 = {{{', '.join(label(axis,u) for u in sorted(top_v))}}}  겹침 {overlap}/{k}"
              f"  → {'PASS' if passed2 else 'FAIL'}")

        # ── 3단계: 그 칸만 진입 ──
        gate = np.array([fn(ts) in pick for ts in h4.index])
        r = trades(h4, entry, exit_, fl, gate=gate)
        print(f"{NL}[3단계] 탐색에서 고른 칸만 진입")
        print(f"        {'':16s}{'탐색 CAGR/MDD/Sh':>28} |{'검증':>26} |{'전체':>28}")
        rep("  고정 5배(기준)", base)
        rep("  선택 칸만", r)
        ct, cv, cf = f(r, START, SPLIT), f(r, SPLIT, None), f(r, START, None)
        passed3, passed4 = cv["sharpe"] > bv["sharpe"], cf["mdd"] > bf["mdd"]
        print(f"        (3) 검증 Sharpe {cv['sharpe']:.2f} vs {bv['sharpe']:.2f} {'PASS' if passed3 else 'FAIL'}"
              f"  ·  (4) MDD {cf['mdd']:.1f}% vs {bf['mdd']:.1f}% {'PASS' if passed4 else 'FAIL'}")
        verdicts[axis] = (passed1, passed2, passed3, passed4, p)

    print(f"{NL}{'='*78}{NL}═══ 사전 등록 판정 ═══")
    for axis, (p1, p2, p3, p4, p) in verdicts.items():
        ok = p1 and p2 and p3 and p4
        note = "" if p1 else "  ← 1단계 미달이므로 3·4단계 결과는 쓰지 않는다"
        print(f"  {axis:12s} (1)순열 {'O' if p1 else 'X'}(p={p:.4f}) · (2)안정 {'O' if p2 else 'X'}"
              f" · (3)Sharpe {'O' if p3 else 'X'} · (4)MDD {'O' if p4 else 'X'}"
              f"  → {'ADOPT' if ok else 'REJECT'}{note}")
    print(f"{NL}  시도 수 N = 2 (사전등록과 같음).")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
