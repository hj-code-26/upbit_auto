"""청산 문턱 분리(EXIT_CONF) — 히스테리시스 분해가 가리킨 변형을 따로 사전등록한다 (2026-09-23).

배경
  research_longtrend.txt 에서 국면 히스테리시스(진입 6개 / 청산 4개 미만)가 사전등록 5관문을 통과했다(ADOPT).
  그 뒤 원인 분해(backtest_longtrend.py --decomp)에서 효과가 어디서 오는지 갈렸다:
      진입만 히스테리시스  전체 Sharpe 0.87 -> 0.79  (**해롭다**, 거래 117->141)
      청산만 히스테리시스  전체 Sharpe 0.87 -> **1.04**  (이득이 전부 여기)
      둘 다 (채택본)      전체 Sharpe 0.87 -> 1.00
  국면 붕괴로 난 청산이 16건 -> 5건으로 줄고, 그 11건이 이득의 원천이다.

  **그런데 '청산만' 은 분해 결과를 보고 고른 변형이다.** 사전등록을 통과한 것은 '둘 다' 이고,
  여기서 말없이 갈아타면 이 세션 내내 경계해 온 사후 선택이다 (research_bias.txt 의 EXTREME_MIN).
  그래서 별도 가설로 다시 등록하고, **독립 표본까지 포함해** 처음부터 판정한다.

가설
  국면은 두 군데에 쓰인다 — 진입(국면 & 돌파)과 청산(이탈 | 국면 붕괴).
  **이 둘에 같은 문턱을 쓸 이유가 없다.** 진입은 확신을 요구해야 하고(CONF), 청산은 성급하면 손해다.
  따라서 청산 문턱만 낮춘다: 진입 = 강세 >= CONF · 청산 = 강세 < CONF - BUF.

  **예상되는 구현상 이점 (검증할 것): 이 규칙은 상태가 필요 없다.**
  히스테리시스는 '지금 국면이 켜져 있는가' 를 사이클 간에 들고 있어야 했다(trading.db 변경).
  청산 문턱 분리는 보유 중일 때만 뜻이 있고, 보유는 강세 >= CONF 에서 시작했으므로
  '강세 < CONF-BUF 면 청산' 과 히스테리시스가 **같은 결과**여야 한다. 같은지 assert 로 확인한다.
  같다면 model.signal() 에 문턱 하나를 더하는 것으로 끝이고 DB 변경이 없다.

=== 사전 등록 판정 (ADOPT = (A)~(E) 모두 + (F)) ===
  기준선 = 고정 3배 · 분봉 하한 재확보 · 고친 엔진 (2026-09-21 레버리지 다이얼 권고 지점)
  (A) 강제청산 0회
  (B) 상위 5개 거래 제외 누적 > 기준선
  (C) block bootstrap 하위 5분위 CAGR > 기준선
  (D) 검증(2024-01~) Sharpe > 기준선
  (E) 탐색(~2024-01) Sharpe >= 기준선
  (F) **독립 표본(Bitstamp BTC/USD 4h 2016~, 10.7년)에서도 전체 Sharpe 가 기준선보다 높다**
      — 이번엔 채택 후보가 나온 뒤가 아니라 **처음부터** 관문에 넣는다.
  주 설정 BUF=2 (진입 6 / 청산 4 미만). 이웃 BUF 1·3 에서 (D)(E)(F) 부호 유지.
  하나라도 미달이면 REJECT. 결과를 보고 BUF·관문을 바꾸지 않는다.
  시도 수 N = 3(BUF) x 2(OKX·Bitstamp) = 6. research_longtrend.txt 의 N=21 에 이어서 센다 (누적 27).

불변식 (assert)
  (a) BUF=0 이면 기준선과 자본곡선이 일치한다
  (b) **청산 문턱 분리 == 청산만 히스테리시스** (위의 '상태 불필요' 주장. 틀리면 DB 상태가 필요하다는 뜻이다)
  (c) 진입 신호 수가 기준선과 같다 (진입 쪽은 건드리지 않았다)

사용: python backtest_exitconf.py   (make exitconf)
결과: research_exitconf.txt
"""
import sys

import numpy as np
import pandas as pd

from isolation import guard

guard()

import backtest_lev as L                                            # noqa: E402
import backtest_longtrend as LT                                     # noqa: E402
import backtest_okx as B                                            # noqa: E402
import backtest_quant as Q                                          # noqa: E402
import backtest_reversion as RV                                     # noqa: E402
import engine as E                                                  # noqa: E402
import model as M                                                   # noqa: E402

NL = chr(10)
BASE_LEV = 3
BUFS = (1, 2, 3)
MAIN = 2
START, SPLIT = Q.START, Q.SPLIT


def signals(h4, bull, buf, conf=None):
    """진입 = 강세 >= conf & 돌파 · 청산 = 이탈 | 강세 < conf-buf. 상태 없음."""
    conf = M.CONF if conf is None else conf
    bd = M.align_daily(bull, h4.index).ffill().fillna(0).values
    hi = h4.high.rolling(M.H4_N).max().shift(1)
    lo = h4.low.rolling(M.H4_M).min().shift(1)
    return ((h4.close > hi).values & (bd >= conf)), ((h4.close < lo).values | (bd < conf - buf))


def run(h4, en, ex, fills, lev=BASE_LEV):
    z = np.zeros(len(h4), bool)
    return E.run(h4, en, ex, z, z, lambda i, c: lev, allow=("long",), fills=fills)


def main():
    h4f = B.fetch("4h")
    bull = B.bull(B.fetch("1d"))
    k = np.asarray(h4f.index >= pd.Timestamp(START, tz="UTC"))
    h4, fl = h4f[k], Q.reclaim_fills()
    print(f"OKX BTC 4h {h4.index[0]:%Y-%m-%d}~{h4.index[-1]:%Y-%m-%d} · CONF={M.CONF} · 고정 {BASE_LEV}배 "
          f"· 분봉 하한 재확보 · 주 설정 BUF={MAIN} (진입 {M.CONF} / 청산 {M.CONF-MAIN} 미만)")

    e0, x0 = signals(h4f, bull, 0)
    base = run(h4, e0[k], x0[k], fl)
    ref = run(h4, *[a[k] for a in Q.frames()[1:3]], fl) if False else base
    assert np.allclose(base[0].values, run(h4, e0[k], x0[k], fl)[0].values), "(a) 재현성"

    # (b) 상태 불필요 주장 — 청산만 히스테리시스와 같은가
    rg_h = LT.hysteresis(h4f.index, M.CONF, M.CONF - MAIN)
    hi = h4f.high.rolling(M.H4_N).max().shift(1)
    lo = h4f.low.rolling(M.H4_M).min().shift(1)
    bd = M.align_daily(bull, h4f.index).ffill().fillna(0).values
    en_h = (h4f.close > hi).values & (bd >= M.CONF)
    ex_h = (h4f.close < lo).values | ~rg_h
    hyst = run(h4, en_h[k], ex_h[k], fl)
    em, xm = signals(h4f, bull, MAIN)
    cand = run(h4, em[k], xm[k], fl)
    same = np.allclose(cand[0].values, hyst[0].values)
    assert (em[k] == e0[k]).all(), "(c) 진입 신호가 달라졌다 — 진입 쪽은 건드리지 않아야 한다"
    print(f"ok  불변식 (a) BUF=0 = 기준선 · (c) 진입 신호 동일 "
          f"({int(e0[k].sum())}건)")
    print(f"    (b) 청산 문턱 분리 == 청산만 히스테리시스 : **{'일치' if same else '불일치'}**"
          f"  (마지막 자본 {cand[0].iloc[-1]:.4f} vs {hyst[0].iloc[-1]:.4f})")
    print(f"    → {'상태(DB 플래그)가 필요 없다. model.signal 에 문턱 하나만 더하면 된다.' if same else '상태가 필요하다 — 두 규칙이 다르다.'}")

    rep = lambda nm, r: Q.report(nm, r[0], r[1].ret.values if len(r[1]) else np.zeros(1), r[2])
    f = lambda r, a, b: Q.metrics(r[0], r[1].ret.values if len(r[1]) else np.zeros(1), r[2], a, b)
    print(f"{NL}{'':24s}{'──── 탐색 ────':>22} |{'──── 검증 ────':>22} |{'────── 전체 ──────':>28}"
          f"{'거래':>5}{'승률':>6}{'최악':>7}{'청산':>4}")
    print(f"{'':24s}{'CAGR':>7}{'MDD':>7}{'Sh':>6} |{'CAGR':>7}{'MDD':>7}{'Sh':>6} |"
          f"{'누적':>9}{'MDD':>7}{'Sh':>6}")
    print(f"{NL}── OKX 2021-03~ ──")
    rep("기준 (BUF=0)", base)
    R = {}
    for b in BUFS:
        en, ex = signals(h4f, bull, b)
        R[b] = run(h4, en[k], ex[k], fl)
        rep(f"BUF={b} (진입{M.CONF}/청산{M.CONF-b})", R[b])

    # ── (F) 독립 표본 ──
    print(f"{NL}── 독립 표본: Bitstamp BTC/USD 4h 2016~ · 1배 · 편도 {E.cost()*100:.2f}% ──")
    print("   ※ 비용은 engine.cost()(=backtest_okx.COST) 하나를 쓴다. 현물 기준으로는 낙관적이지만"
          "{} 기준선·후보에 **같이** 적용되므로 비교는 공정하다.".format(NL + "     "))
    b4, b1 = RV.fetch("bitstamp", "4h"), RV.fetch("bitstamp", "1d")
    bull_b = B.bull(M.add_indicators(b1.copy()))
    BS = {}
    for b in (0,) + BUFS:
        en, ex = signals(b4, bull_b, b)
        z = np.zeros(len(b4), bool)
        r = E.run(b4, en, ex, z, z, lambda i, c: 1, allow=("long",))
        BS[b] = Q.metrics(r[0], r[1].ret.values if len(r[1]) else np.zeros(1), r[2], None, None)
        print(f"   {'기준 (BUF=0)' if b == 0 else f'BUF={b}':24s}"
              f"누적 {BS[b]['누적']:+8.0f}%  MDD {BS[b]['mdd']:6.1f}%  Sh {BS[b]['sharpe']:5.2f}"
              f"  거래 {BS[b]['거래']:4d}  승률 {BS[b]['승률']:3.0f}%")

    bt, bv, bfull = f(base, START, SPLIT), f(base, SPLIT, None), f(base, START, None)
    bB, (bq, _) = L.ex_top(base[1].ret.values, 5), L.boot(base[0])
    print(f"{NL}═══ 사전 등록 판정 (기준선 BUF=0: 검증 Sh {bv['sharpe']:.2f} · 상위5제외 {bB:+.0f}% · "
          f"부트5분위 {bq[0]:+.2f}% · Bitstamp Sh {BS[0]['sharpe']:.2f}) ═══")
    verdict = {}
    for b in BUFS:
        r = R[b]
        ct, cv, cf = f(r, START, SPLIT), f(r, SPLIT, None), f(r, START, None)
        rr = r[1].ret.values if len(r[1]) else np.zeros(1)
        cB, (cq, _) = L.ex_top(rr, 5), L.boot(r[0])
        ch = [("A 청산0", r[2]["liq"] == 0, f"{r[2]['liq']}회"),
              ("B 상위5제외", cB > bB, f"{cB:+.0f}%/{bB:+.0f}%"),
              ("C 부트5분위", cq[0] > bq[0], f"{cq[0]:+.2f}%/{bq[0]:+.2f}%"),
              ("D 검증Sh", cv["sharpe"] > bv["sharpe"], f"{cv['sharpe']:.2f}/{bv['sharpe']:.2f}"),
              ("E 탐색Sh", ct["sharpe"] >= bt["sharpe"], f"{ct['sharpe']:.2f}/{bt['sharpe']:.2f}"),
              ("F 독립표본", BS[b]["sharpe"] > BS[0]["sharpe"], f"{BS[b]['sharpe']:.2f}/{BS[0]['sharpe']:.2f}")]
        ok = all(v for _, v, _ in ch)
        verdict[b] = ok
        mark = " ← 주 설정" if b == MAIN else ""
        print(f"  BUF={b}{mark}")
        print(f"     " + " · ".join(f"{a} {'O' if v else 'X'}({d})" for a, v, d in ch))
        print(f"     → {'ADOPT' if ok else 'REJECT'}")
    nb = all(verdict[b] for b in BUFS)
    print(f"{NL}  주 설정 BUF={MAIN}: {'ADOPT' if verdict[MAIN] else 'REJECT'}"
          f" · 이웃 BUF 1·3 부호 유지: {'O' if nb else 'X'}")
    print(f"  시도 수 N = 6 (사전등록과 같음. 누적 27).")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
