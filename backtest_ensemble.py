"""다중 룩백 앙상블 — 돌파/이탈 기간을 하나 고르는 대신 여러 개를 동시에 돌린다 (2026-09-21).

동기 (research_okx_short.txt 가 이미 절반을 적어놨다)
  지금 봇은 돌파 12봉 / 이탈 6봉 **한 조합**이다. 그런데 그 리서치는 "이웃 파라미터 30/15·6/3 도
  전부 양수, MDD −25~−34%" 라고 적고 지나갔다. 셋 다 양수인데 하나만 쓰는 건 파라미터 운에 거는 것이다.
  TSMOM 문헌의 표준 처방은 룩백을 맞히지 말고 **여러 시간축을 합산**해 그 운을 지우는 것이다
  (Rohrbach·Suremann·Osterrieder 2017, Moskowitz·Ooi·Pedersen 2012 계열; arXiv 2602.11708).
  코인에서도 TS 모멘텀이 CS 모멘텀을 이기고 변동성 스케일링과 붙을 때 Sharpe 가 오른다고 보고돼 있다.

  주의 — 저쪽 문헌의 분산 효과는 **여러 자산**에서 나온다. 여기는 BTC 한 종목이라 슬리브 상관이 높다.
  그래서 기대하는 것은 수익 증가가 아니라 (ㄱ) 파라미터 민감도 감소 (ㄴ) MDD 감소다. 그 둘로 판정한다.

검증할 두 가지 (둘 다 사전 등록. 나중에 하나만 골라 보고하지 않는다)
  A) 슬리브 분할   (6,3)·(12,6)·(30,15) 를 각각 증거금 1/3 로 **독립 포지션**으로 굴린다.
                   격리 마진이므로 한 슬리브가 청산돼도 잃는 것은 그 1/3 이다 (engine.run pos_pct).
  B) 신호 합산     단일 포지션. 켜진 슬리브 수 k(0~3) 에 비례해 레버리지를 LEV×k/3 로 건다.
                   청산은 k=0 일 때. 거래소에 포지션이 하나라 실제 운용이 단순하다.

고정하는 것 (결과를 보고 바꾸지 않는다)
  · 룩백 3세트 = (6,3) · (12,6) · (30,15). research_okx_short.txt 가 **이미 양수라고 적어둔 바로 그 셋**이다.
    여기서 고른 것이 아니라 거기서 물려받은 것이다 — 그래서 선택 자유도가 없다.
  · 일봉 국면 필터(CONF), 비용 편도 0.07%, 펀딩, 격리 청산, 체결(신호봉 종가 → 다음 봉 시가) 전부 지금 봇과 같다.
  · 진입 경로는 1차 전부 **4h 시가 시장가**로 비교한다. 분봉 하한 재확보(research_entry_timing.txt)는
    돌파선 hi 에 묶여 있어 슬리브마다 다시 계산해야 하므로, **ADOPT 후보가 나온 뒤에만** 그 경로로 재측정한다.
    (즉 1차 표의 절대 수익은 실봇보다 낮게 나온다. 기준선도 같은 조건이라 비교는 공정하다.)
  · 레버리지는 1·3·5 를 모두 보고, 판정은 **라이브 설정인 5배**로 한다 (.env LEVERAGE=5).

기준선 — 결과 보기 전에 박아둔다 (research_multi.txt 2026-09-15, 같은 비용·같은 'now' 진입)
                     탐색 CAGR/MDD/Sh        검증 CAGR/MDD/Sh        전체 누적/MDD/Sh     거래  승률
  BTC 단일 12/6 1배   +21% / -17.6% / 0.88   +13% / -22.6% / 0.57   +140% / -22.6% / 0.73  120  33%
  BTC 단일 12/6 3배   +49% / -45.4% / 0.73   +28% / -50.3% / 0.44   +503% / -50.3% / 0.58  120  33%
  BTC 단일 12/6 5배   +54% / -65.1% / 0.50   +27% / -67.1% / 0.27   +553% / -67.1% / 0.39  120  33%
  이 표는 온전성 확인용이다. **판정은 같은 실행 안에서 다시 잰 단일 12/6 과 비교한다** (데이터 종료일이 다르다).

═══ 사전 등록 판정 (ADOPT = ①~⑤ 모두 충족. 하나라도 미달이면 REJECT) ═══
  ① 검증 구간(2024-01~) Sharpe > 단일 12/6 검증 Sharpe          — 과적합이 아니어야 한다
  ② 탐색 구간(~2024-01) Sharpe ≥ 단일 12/6 탐색 Sharpe          — 이 리포 규율: 두 구간 모두 이겨야 채택
  ③ 전체 MDD 가 단일 12/6 보다 **개선**                          — 앙상블의 주장이 분산이므로 여기서 못 이기면 할 말이 없다
  ④ 이웃 룩백 격자 2개에서 ①~③ 의 부호 유지                      — 격자: (5,2)·(10,5)·(24,12) 와 (8,4)·(16,8)·(40,20)
  ⑤ 강제청산 횟수 ≤ 단일 12/6                                   — 5배 운용에서 청산 1회 = 증거금 전액. 늘면 무조건 탈락
  ⑥ **대체 여부는 따로.** ①~⑤ 를 넘어도 검증 Sharpe 가 단일보다 크지 않으면 '대체 불가, 후보로 보관'.

  시도 수 N = 2(A·B) × 3(레버리지 1/3/5) + 2(이웃 격자, 5배만) + 1(B의 k 임계 대조) = 15. 다중검정 할인용으로 적어둔다.
  결과를 보고 룩백 세트·비중·판정 기준을 바꾸지 않는다. 바꾸려면 그 사실을 research_ensemble.txt 에 남기고 N 을 올린다.

불변식 (시뮬 안 assert — 통과 못 하면 표를 믿지 않는다)
  (a) 슬리브 3개를 **전부 (12,6) 으로** 두면 단일 12/6 과 자본곡선이 일치한다 (1/3 증거금 × 3 = 1)
  (b) B 에서 k 임계를 1 로 두고 레버리지를 고정하면 '셋 중 하나라도 켜지면 진입'(OR) 과 같다
  (c) 슬리브 하나가 청산돼도 자산 감소는 1/3 을 넘지 않는다

사용: python backtest_ensemble.py   (make ensemble)
결과: research_ensemble.txt
"""
import sys

import numpy as np
import pandas as pd

from isolation import guard

guard()

import backtest_okx as B                                            # noqa: E402
import backtest_quant as Q                                          # noqa: E402
import engine as E                                                  # noqa: E402
import model as M                                                   # noqa: E402

SETS = [(6, 3), (12, 6), (30, 15)]                  # 사전등록한 룩백 3세트 (research_okx_short.txt 에서 물려받음)
GRIDS = [[(5, 2), (10, 5), (24, 12)], [(8, 4), (16, 8), (40, 20)]]   # ④ 이웃 격자
SINGLE = (M.H4_N, M.H4_M)                           # 기준선 = 지금 봇 (12, 6)
START, SPLIT = Q.START, Q.SPLIT


def frames():
    """4h 봉 + 일봉 국면. 룩백마다 다른 것은 돌파선/이탈선뿐이라 여기서는 국면만 만든다."""
    d1, h4 = B.fetch("1d"), B.fetch("4h")
    rg = M.align_daily(B.bull(d1) >= M.CONF, h4.index).fillna(False).astype(bool)
    m = h4.index >= pd.Timestamp(START, tz="UTC")
    return h4[m], rg.values[m]


def sig(h4, rg, n, m):
    """룩백 (n, m) 의 (진입, 청산) 신호. 지금 봇 규칙을 기간만 바꾼 것이다."""
    hi = h4.high.rolling(n).max().shift(1)
    lo = h4.low.rolling(m).min().shift(1)
    return ((h4.close > hi) & rg).values, ((h4.close < lo) | ~rg).values


def hold_state(en, ex):
    """진입 신호 뒤 청산 신호까지 '보유 중' 인 구간. engine 과 같은 순서(청산 먼저)로 재현한다."""
    out = np.zeros(len(en), bool)
    on = False
    for i in range(len(en)):
        if on and ex[i]:
            on = False
        elif not on and en[i]:
            on = True
        out[i] = on
    return out


def sleeves(h4, rg, sets, lev):
    """A) 슬리브 분할 — 각 룩백을 자본 1/3 의 **독립 계좌**로 굴리고 곡선을 더한다.

    슬리브끼리 리밸런싱하지 않는다 (각자 자기 몫만 복리). 따라서 한 슬리브가 청산돼도
    잃는 것은 전체의 1/3 이다 — 불변식 (c). 전부 같은 룩백이면 단일과 완전히 같다 — 불변식 (a)."""
    w = 1.0 / len(sets)
    curves, trades, liq, amb, open_end = [], [], 0, 0, False
    for n, m in sets:
        en, ex = sig(h4, rg, n, m)
        c, t, st = E.run(h4, en, ex, np.zeros(len(h4), bool), np.zeros(len(h4), bool),
                         lambda i, cv: lev, allow=("long",))
        curves.append(c * w)
        trades.append(t)
        liq += st["liq"]; amb += st["liq_ambiguous"]; open_end |= st["open_at_end"]
    tr = pd.concat(trades, ignore_index=True) if any(len(t) for t in trades) else trades[0]
    return sum(curves), tr, {"liq": liq, "liq_ambiguous": amb, "open_at_end": open_end}


def combo(h4, rg, sets, lev, kmin=1, scale=True):
    """B) 신호 합산 — 단일 포지션. 켜진 슬리브 수 k 에 비례해 레버리지 lev×k/len(sets).

    레버리지는 **진입 시점 k 로 정하고 그 거래 동안 고정**한다 (거래소가 포지션 단위로 건다).
    kmin=1 · scale=False 면 '셋 중 하나라도 켜지면 진입' 과 같다 — 불변식 (b)."""
    k = sum(hold_state(*sig(h4, rg, n, m)).astype(int) for n, m in sets)
    en, ex = k >= kmin, k == 0
    lv = (lambda i, cv: lev * k[i - 1] / len(sets)) if scale else (lambda i, cv: lev)
    c, t, st = E.run(h4, en, ex, np.zeros(len(h4), bool), np.zeros(len(h4), bool), lv, allow=("long",))
    return c, t, st


def single(h4, rg, lev):
    en, ex = sig(h4, rg, *SINGLE)
    return E.run(h4, en, ex, np.zeros(len(h4), bool), np.zeros(len(h4), bool),
                 lambda i, cv: lev, allow=("long",))


def judge(name, base, cand):
    """사전등록 ①~⑤. base/cand = (곡선, 거래, stats). → (통과여부, 사유 문자열)"""
    f = lambda r, lo, hi: Q.metrics(r[0], r[1].ret.values if len(r[1]) else np.zeros(1), r[2], lo, hi)
    bt, bv, bf = f(base, START, SPLIT), f(base, SPLIT, None), f(base, START, None)
    ct, cv, cf = f(cand, START, SPLIT), f(cand, SPLIT, None), f(cand, START, None)
    checks = [("① 검증 Sharpe >", cv["sharpe"] > bv["sharpe"], f"{cv['sharpe']:.2f} vs {bv['sharpe']:.2f}"),
              ("② 탐색 Sharpe ≥", ct["sharpe"] >= bt["sharpe"], f"{ct['sharpe']:.2f} vs {bt['sharpe']:.2f}"),
              ("③ 전체 MDD 개선", cf["mdd"] > bf["mdd"], f"{cf['mdd']:.1f}% vs {bf['mdd']:.1f}%"),
              ("⑤ 청산 ≤", cand[2]["liq"] <= base[2]["liq"], f"{cand[2]['liq']} vs {base[2]['liq']}")]
    ok = all(c[1] for c in checks)
    print(f"  {name:28s} " + " · ".join(f"{n} {'O' if v else 'X'}({d})" for n, v, d in checks)
          + f"  → {'PASS' if ok else 'FAIL'}")
    return ok


def _selfcheck(h4, rg):
    """사전등록 불변식 (a)(b)(c). 통과 못 하면 아래 표를 믿지 않는다."""
    a = sleeves(h4, rg, [SINGLE] * 3, 3)
    b = single(h4, rg, 3)
    assert np.allclose(a[0].values, b[0].values), "(a) 같은 룩백 3슬리브 ≠ 단일"
    c1 = combo(h4, rg, SETS, 3, kmin=1, scale=False)
    k = sum(hold_state(*sig(h4, rg, n, m)).astype(int) for n, m in SETS)
    o_en, o_ex = k >= 1, k == 0
    c2 = E.run(h4, o_en, o_ex, np.zeros(len(h4), bool), np.zeros(len(h4), bool),
               lambda i, cv: 3, allow=("long",))
    assert np.allclose(c1[0].values, c2[0].values), "(b) k≥1 고정배율 ≠ OR 진입"
    d = sleeves(h4, rg, SETS, 5)
    step = (d[0] / d[0].shift(1)).dropna().min()
    assert step > 1 / 3 - 1e-9, f"(c) 한 봉에 1/3 초과 손실: {step}"
    print("ok  불변식 (a) 같은룩백=단일 · (b) k≥1=OR · (c) 슬리브 손실 ≤ 1/3")


def main():
    h4, rg = frames()
    print(f"OKX BTC 4h {h4.index[0]:%Y-%m-%d}~{h4.index[-1]:%Y-%m-%d} · CONF={M.CONF} · 편도 {Q.COST * 100:.2f}%"
          f" · 진입 4h 시가 시장가(분봉 재확보 없음)")
    print(f"룩백 {SETS} · 탐색 {START}~{SPLIT} / 검증 {SPLIT}~")
    _selfcheck(h4, rg)
    print(f"\n{'':24s}{'──── 탐색 ────':>22} |{'──── 검증 ────':>22} |{'────── 전체 ──────':>28}"
          f"{'거래':>5}{'승률':>6}{'최악':>7}{'청산':>4}")
    print(f"{'':24s}{'CAGR':>7}{'MDD':>7}{'Sh':>6} |{'CAGR':>7}{'MDD':>7}{'Sh':>6} |"
          f"{'누적':>9}{'MDD':>7}{'Sh':>6}")

    rep = lambda name, r: Q.report(name, r[0], r[1].ret.values if len(r[1]) else np.zeros(1), r[2])
    base = {}
    print("\n── 기준선: 단일 12/6 (지금 봇) ──")
    for lev in (1, 3, 5):
        base[lev] = single(h4, rg, lev)
        rep(f"단일 12/6 {lev}배", base[lev])

    print("\n── A) 슬리브 분할 (각 1/3, 독립 계좌, 리밸런싱 없음) ──")
    A = {lev: sleeves(h4, rg, SETS, lev) for lev in (1, 3, 5)}
    for lev in (1, 3, 5):
        rep(f"A 슬리브 {lev}배", A[lev])

    print("\n── B) 신호 합산 (단일 포지션, 레버리지 ×k/3, 진입 시점 고정) ──")
    Bv = {lev: combo(h4, rg, SETS, lev) for lev in (1, 3, 5)}
    for lev in (1, 3, 5):
        rep(f"B 합산 {lev}배", Bv[lev])
    rep("B 대조 k≥1 고정 3배", combo(h4, rg, SETS, 3, kmin=1, scale=False))

    print("\n── ④ 이웃 룩백 격자 (5배) ──")
    G = []
    for g in GRIDS:
        ga, gb = sleeves(h4, rg, g, 5), combo(h4, rg, g, 5)
        rep(f"A {g[0]}..{g[-1]}", ga)
        rep(f"B {g[0]}..{g[-1]}", gb)
        G.append((ga, gb))

    print("\n═══ 사전 등록 판정 (기준선 = 단일 12/6 5배, 라이브 설정) ═══")
    pa = judge("A 슬리브 5배", base[5], A[5])
    pb = judge("B 합산 5배", base[5], Bv[5])
    print("  ④ 이웃 격자:")
    ga = all([judge(f"   A {g[0]}..{g[-1]}", base[5], x[0]) for g, x in zip(GRIDS, G)])
    gb = all([judge(f"   B {g[0]}..{g[-1]}", base[5], x[1]) for g, x in zip(GRIDS, G)])
    print(f"\n  A) {'ADOPT 후보' if pa and ga else 'REJECT'}   B) {'ADOPT 후보' if pb and gb else 'REJECT'}")
    print("\n── 사후분석 (판정 이후. 왜 졌는지만 본다 — 이 표로 새 규칙을 고르지 않는다) ──")
    for n, m in SETS:
        en, ex = sig(h4, rg, n, m)
        for lev in (1, 3, 5):
            rep(f"  ({n},{m}) 단독 {lev}배",
                E.run(h4, en, ex, np.zeros(len(h4), bool), np.zeros(len(h4), bool),
                      lambda i, cv, L=lev: L, allow=("long",)))
    print("  ⑥ 대체 판정은 ADOPT 후보가 나온 뒤 분봉 재확보 경로에서 다시 잰다 (사전등록 참조).")
    print("  참고: 3배·1배 표는 맥락용이다. 판정은 라이브 설정인 5배에서만 한다.")


if __name__ == "__main__":
    main()
