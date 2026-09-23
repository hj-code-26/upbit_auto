"""quant_nasq100 2차 이식 — **장기 추세 필터** + 국면 히스테리시스 (2026-09-23).

1차 이식(research_quant_transfer.txt, 2026-09-10)은 변동성 타겟팅·고정 만기·하락 국면 전환을 옮겼고,
그중 VT 만 채택됐다가 2026-09-21 재검증에서 **기각**됐다(research_vt.txt). SOXL 규칙도 기각(research_soxl.txt).
이번에는 저쪽 목록을 우리 기각 이력과 한 줄씩 대조해 **아직 안 겹친 것 하나**를 골랐다.

### 왜 이것만 남았나 (저쪽 README '채택한 규칙' / '넣지 않기로 한 것' 대조)
  저쪽 채택 5개 중
    · 변동성 타겟 30%          -> 우리는 2026-09-21 두 번 기각 (research_vt.txt)
    · 고정 20거래일 만기 청산   -> backtest_quant ③ 에서 이미 측정
    · 하락 국면 전환(지수 60일) -> backtest_quant ④ 에서 이미 측정
    · 동시 보유 10종목          -> 다종목은 2026-09-15 기각 (research_multi.txt)
    · 선별 = 20일 수익률 상위   -> 횡단면 규칙. 단일 종목에 뜻이 없다
  저쪽 기각 목록(손절선·비중 트림·하락장 매매 중단·모멘텀 기울기·가속도)은 우리 결론과 방향이 같다. 다시 재지 않는다.
  **남은 것: 저쪽 research/ts_momentum.md 의 장기 추세 신호.** 우리는 한 번도 안 봤다.

### 무엇이 새로운가 — 우리 국면 필터는 전부 단기다
  model.RULES 8개의 최장 창이 **50일 이평 · 20일 수익률**이다. 그보다 긴 시간축이 없다.
  저쪽에서 위상(재판정 시작일)을 0/5/10/15 로 흔들어도 안정적이었던 신호는 둘이다:
    P2 12-1개월 모멘텀 > 0   Sharpe 0.744 · 위상별 0.72~0.80 · 연회전율 10.8 -> 1.1
    P3 종가 > 200일선        Sharpe 0.735 · 위상별 0.64~0.78 · MDD -18.4%
  (P1 20일 모멘텀은 위상별 0.38~0.76 으로 달력 운에 기댄다고 저쪽이 적었다 — 우리 20일 규칙과 같은 창이다.)

  **출처의 판정을 숨기지 않는다: 저쪽에서 P2·P3 는 ADOPT 가 아니라 CANDIDATE 다.** QQQ 매수보유(0.898)를
  못 이겨서다. 우리는 기준선이 다르다(우리 봇은 BTC 매수보유를 이긴다). 그래서 이식할 값어치는 있지만,
  "저쪽에서 채택된 규칙" 이라고 말하면 거짓이다.

### 후보 (전부 **진입 게이트로만**. 청산·국면 규칙·사이징은 건드리지 않는다)
  L1  12-1개월 모멘텀 > 0   (252일 전 -> 21일 전 수익률. 최근 한 달을 빼는 고전적 정의)
  L2  종가 > 200일 이평
  L3  L1 AND L2
  H1  국면 히스테리시스 — 진입은 강세 CONF(6)개, **유지는 CONF-2(4)개**. 켜고 끄는 문턱을 벌려 잦은 전환을 막는다.
      audit/AUDIT.md §7 '후보 3 (ATR 재난손절 · 국면 hysteresis)' 의 뒷부분이다. 아직 성적이 없던 항목.
  이웃: L1 창 (6-1, 12-1, 18-1개월) · L2 (100, 200, 300일) · H1 버퍼 (1, 2, 3)
  장기 필터의 가격은 **바이낸스 USDT-M 일봉 2019-09~** 을 쓴다 (OKX 는 2021-01 시작이라 252일 사전이력이 없다).
  가격 수준이 아니라 부호만 쓰므로 거래소 차이는 문제되지 않는다. 판단 시각 정렬은 model.align_daily 와 같다.

### 여기서 "위에 있는 내용" 을 합친다 — 기준선과 판정 기준
  기준선 = **고정 3배 · 분봉 하한 재확보 진입 · 고친 엔진** (2026-09-21 레버리지 다이얼이 권고한 지점).
  판정은 누적이 아니라 **그때 세운 꼬리위험 관문**을 그대로 쓴다. 1배·5배는 맥락으로만 찍는다.

=== 사전 등록 판정 (ADOPT = (A)~(E) 모두 충족) ===
  (A) 강제청산 0회
  (B) 상위 5개 거래 제외 누적 > 기준선
  (C) block bootstrap 하위 5분위 CAGR > 기준선
  (D) 검증(2024-01~) Sharpe > 기준선
  (E) 탐색(~2024-01) Sharpe >= 기준선
  이웃 격자에서 (D)(E) 의 부호가 유지돼야 한다. 하나라도 미달이면 REJECT.
  시도 수 N = 4(주 설정) + 9(이웃) = 13.

**판정보다 먼저 세는 것 — 사건 수 (research_funding.txt 에서 배운 순서)**
  BTC 2021-03~2026-09 에서 '12-1개월 모멘텀 < 0' 은 사실상 2022 하락장 한 구간일 수 있다.
  그러면 이건 통계가 아니라 **사건 하나에 건 베팅**이고, Sharpe 가 아무리 올라도 의미가 없다.
  그래서 각 필터가 **켜고 끈 구간(episode) 수와 차단한 진입 건수**를 판정 전에 센다.
  구간이 3개 미만이면 격자 결과와 무관하게 **INCONCLUSIVE** 로 적는다.

불변식 (assert)
  (a) 게이트를 항상 열면 기준선과 자본곡선이 일치한다
  (b) 게이트는 진입만 막는다 — 거래 수가 기준선보다 늘지 않는다
  (c) 장기 필터가 미래를 보지 않는다 (표본을 t 에서 잘라도 t 이전 게이트 값이 동일)

사용: python backtest_longtrend.py   (make longtrend)
결과: research_longtrend.txt
"""
import sys

import numpy as np
import pandas as pd

from isolation import guard

guard()

import backtest_flow as FL                                          # noqa: E402
import backtest_lev as L                                            # noqa: E402
import backtest_okx as B                                            # noqa: E402
import backtest_quant as Q                                          # noqa: E402
import engine as E                                                  # noqa: E402
import model as M                                                   # noqa: E402

NL = chr(10)
BASE_LEV = 3                     # 2026-09-21 레버리지 다이얼이 권고한 지점
CTX_LEV = (1, 5)                 # 맥락용
MOM_WINS = ((252, 21), (126, 21), (378, 21))      # 12-1 · 6-1 · 18-1 개월
SMA_WINS = (100, 200, 300)
BUFFERS = (1, 2, 3)
START, SPLIT = Q.START, Q.SPLIT


def long_signals(h4_index):
    """바이낸스 일봉으로 만든 장기 필터들을 4h 봉에 붙인다. 정렬은 model.align_daily 와 같다."""
    d = FL.taker("1d").close
    out = {}
    for lb, skip in MOM_WINS:
        s = (d.shift(skip) / d.shift(lb) - 1) > 0                   # t-lb -> t-skip 수익률 > 0
        out[f"mom{lb}-{skip}"] = M.align_daily(s, h4_index).fillna(False).astype(bool).values
    for n in SMA_WINS:
        out[f"sma{n}"] = M.align_daily(d > d.rolling(n).mean(), h4_index).fillna(False).astype(bool).values
    return out


def hysteresis(h4_index, enter=None, exit_at=None):
    """국면 히스테리시스: 강세 규칙 enter개 이상이면 켜지고, exit_at개 미만으로 떨어져야 꺼진다."""
    enter = M.CONF if enter is None else enter
    bd = M.align_daily(B.bull(B.fetch("1d")), h4_index).ffill().fillna(0).values
    out, on = np.zeros(len(h4_index), bool), False
    for i, v in enumerate(bd):
        on = (v >= enter) if not on else (v >= exit_at)
        out[i] = on
    return out


def hyst_signals(enter, exit_at):
    """히스테리시스 국면으로 **진입·청산 신호를 다시 만든다.**

    2026-09-23: 처음에는 engine 의 gate 로 넣었는데 그건 틀린 배선이었다 — 히스테리시스는 국면을
    **더 오래 켜두는** 규칙이고 gate 는 조이기만 하므로 0건 차단, 즉 아무 일도 하지 않았다.
    국면은 진입 조건(국면 & 돌파)과 청산 조건(이탈 | 국면 붕괴) 양쪽에 들어가므로 여기서 둘 다 다시 만든다."""
    d1, h4 = B.fetch("1d"), B.fetch("4h")
    rg = pd.Series(hysteresis(h4.index, enter, exit_at), index=h4.index)
    hi = h4.high.rolling(M.H4_N).max().shift(1)
    lo = h4.low.rolling(M.H4_M).min().shift(1)
    return h4, ((h4.close > hi) & rg).values, ((h4.close < lo) | ~rg).values


def episodes(g):
    """게이트가 닫혔다 열린 구간의 수. 사건 하나에 건 베팅인지 보는 지표."""
    d = np.diff(np.concatenate([[True], g.astype(bool)]).astype(int))
    return int((d < 0).sum())


def run(h4, en, ex, fills, lev, gate=None):
    z = np.zeros(len(h4), bool)
    return E.run(h4, en, ex, z, z, lambda i, c: lev, allow=("long",), fills=fills, gate=(gate, None) if gate is not None else None)


def main():
    h4, entry, exit_, _, _ = Q.frames()
    m = h4.index >= pd.Timestamp(START, tz="UTC")
    h4, entry, exit_ = h4[m], entry[m], exit_[m]
    fl = Q.reclaim_fills()
    S = long_signals(h4.index)
    print(f"OKX BTC 4h {h4.index[0]:%Y-%m-%d}~{h4.index[-1]:%Y-%m-%d} · CONF={M.CONF} · 편도 {Q.COST*100:.2f}%"
          f" · 진입 분봉 하한 재확보 · 기준선 고정 {BASE_LEV}배")
    print("장기 필터 가격 = 바이낸스 USDT-M 일봉 2019-09~ (OKX 는 252일 사전이력이 없다). 부호만 쓴다.")

    base = run(h4, entry, exit_, fl, BASE_LEV)
    G = {"L1 12-1개월>0": S["mom252-21"], "L2 종가>200일선": S["sma200"],
         "L3 L1 & L2": S["mom252-21"] & S["sma200"]}

    def hyst_run(buf, lev=BASE_LEV):
        """히스테리시스는 게이트가 아니라 국면 자체를 바꾼다 → 신호를 다시 만들어 돌린다."""
        hf, he, hx = hyst_signals(M.CONF, M.CONF - buf)
        k = hf.index >= pd.Timestamp(START, tz="UTC")
        return run(hf[k], he[k], hx[k], fl, lev)

    allopen = run(h4, entry, exit_, fl, BASE_LEV, np.ones(len(h4), bool))
    assert np.allclose(allopen[0].values, base[0].values), "(a) 게이트를 다 열었는데 기준선과 다르다"
    d = FL.taker("1d").close
    half = len(d) // 2
    s_full = M.align_daily((d.shift(21) / d.shift(252) - 1) > 0, h4.index).fillna(False).astype(bool).values
    s_cut = M.align_daily((d[:half].shift(21) / d[:half].shift(252) - 1) > 0, h4.index).fillna(False).astype(bool).values
    upto = h4.index <= d.index[half - 1]
    assert (s_full[upto] == s_cut[upto]).all(), "(c) 표본을 자르면 과거 게이트 값이 달라진다 = 미래 참조"
    # (c) 히스테리시스도 미래를 보지 않는가 — 인과 루프인지 잘라서 확인한다
    hy_full = hysteresis(h4.index, M.CONF, M.CONF - 2)
    k2 = len(h4) // 2
    hy_cut = hysteresis(h4.index[:k2], M.CONF, M.CONF - 2)
    assert (hy_full[:k2] == hy_cut).all(), "(c) 히스테리시스가 미래를 본다 — 표본을 자르면 과거 값이 달라진다"
    print("ok  불변식 (a) 게이트 전부 열면 = 기준선 · (c) 장기필터·히스테리시스 모두 표본 절단에 불변")

    # ── 사건 수를 판정보다 먼저 센다 ──
    print(f"{NL}── 사건 수 (판정 전에 먼저 본다) ──")
    print("   구간이 3개 미만이면 통계가 아니라 사건 한두 개에 건 베팅이다 → INCONCLUSIVE")
    ent_i = np.array([i for i in range(1, len(h4)) if entry[i - 1]])
    fires = {}
    for buf in BUFFERS:
        hf, he, hx = hyst_signals(M.CONF, M.CONF - buf)
        k = hf.index >= pd.Timestamp(START, tz="UTC")
        fires[f"H1 히스테리시스 {M.CONF}/{M.CONF-buf}"] = episodes(hysteresis(h4.index, M.CONF, M.CONF - buf))
        print(f"   H1 버퍼 {buf} ({M.CONF}/{M.CONF-buf}): 국면 켜진 비율 "
              f"{hysteresis(h4.index, M.CONF, M.CONF-buf).mean()*100:4.1f}% (기준 국면 "
              f"{M.align_daily(B.bull(B.fetch('1d')) >= M.CONF, h4.index).fillna(False).mean()*100:4.1f}%)"
              f" · 진입 신호 {int(he[k].sum()):3d}건 (기준 {int(entry.sum()):3d}건)")
    for n, g in G.items():
        blocked = int((~g[ent_i - 1]).sum())
        ep = episodes(g)
        fires[n] = ep
        print(f"   {n:22s} 닫힌 구간 {ep:2d}개 · 게이트가 막은 진입 {blocked:3d}/{len(ent_i)}건 "
              f"({blocked/len(ent_i)*100:4.1f}%) · 전체 봉 중 열린 비율 {g.mean()*100:4.1f}%")

    rep = lambda nm, r: Q.report(nm, r[0], r[1].ret.values if len(r[1]) else np.zeros(1), r[2])
    f = lambda r, lo, hi: Q.metrics(r[0], r[1].ret.values if len(r[1]) else np.zeros(1), r[2], lo, hi)
    print(f"{NL}{'':24s}{'──── 탐색 ────':>22} |{'──── 검증 ────':>22} |{'────── 전체 ──────':>28}"
          f"{'거래':>5}{'승률':>6}{'최악':>7}{'청산':>4}")
    print(f"{'':24s}{'CAGR':>7}{'MDD':>7}{'Sh':>6} |{'CAGR':>7}{'MDD':>7}{'Sh':>6} |"
          f"{'누적':>9}{'MDD':>7}{'Sh':>6}")
    print(f"{NL}── 기준선 ──")
    rep(f"고정 {BASE_LEV}배 (권고 지점)", base)
    for lv in CTX_LEV:
        rep(f"  참고: 고정 {lv}배", run(h4, entry, exit_, fl, lv))

    print(f"{NL}── 후보 (전부 {BASE_LEV}배) ──")
    R = {}
    for n, g in G.items():
        R[n] = run(h4, entry, exit_, fl, BASE_LEV, g)
        assert R[n][1].shape[0] <= base[1].shape[0], f"(b) {n}: 게이트가 거래를 늘렸다"
        rep(n, R[n])
    R[f"H1 히스테리시스 {M.CONF}/{M.CONF-2}"] = hyst_run(2)
    rep(f"H1 히스테리시스 {M.CONF}/{M.CONF-2}", R[f"H1 히스테리시스 {M.CONF}/{M.CONF-2}"])

    print(f"{NL}── 이웃 격자 ──")
    NB = {}
    for lb, sk in MOM_WINS:
        NB[f"모멘텀 {lb}-{sk}"] = S[f"mom{lb}-{sk}"]
    for n in SMA_WINS:
        NB[f"이평 {n}일"] = S[f"sma{n}"]
    for n, g in NB.items():
        rep(n, run(h4, entry, exit_, fl, BASE_LEV, g))
    for b in BUFFERS:
        rep(f"히스테리시스 {M.CONF}/{M.CONF-b}", hyst_run(b))

    bt, bv, bf = f(base, START, SPLIT), f(base, SPLIT, None), f(base, START, None)
    bB, (bq, _) = L.ex_top(base[1].ret.values, 5), L.boot(base[0])
    print(f"{NL}═══ 사전 등록 판정 (기준선 = 고정 {BASE_LEV}배: 검증 Sh {bv['sharpe']:.2f} · "
          f"상위5제외 {bB:+.0f}% · 부트5분위 {bq[0]:+.2f}%) ═══")
    for n, r in R.items():
        ct, cv, cf = f(r, START, SPLIT), f(r, SPLIT, None), f(r, START, None)
        rr = r[1].ret.values if len(r[1]) else np.zeros(1)
        cB, (cq, _) = L.ex_top(rr, 5), L.boot(r[0])
        ch = [("A 청산0", r[2]["liq"] == 0, f"{r[2]['liq']}회"),
              ("B 상위5제외", cB > bB, f"{cB:+.0f}% vs {bB:+.0f}%"),
              ("C 부트5분위", cq[0] > bq[0], f"{cq[0]:+.2f}% vs {bq[0]:+.2f}%"),
              ("D 검증Sh", cv["sharpe"] > bv["sharpe"], f"{cv['sharpe']:.2f} vs {bv['sharpe']:.2f}"),
              ("E 탐색Sh", ct["sharpe"] >= bt["sharpe"], f"{ct['sharpe']:.2f} vs {bt['sharpe']:.2f}")]
        ok = all(v for _, v, _ in ch)
        bad = fires[n] < 3
        print(f"  {n:22s} " + " · ".join(f"{a} {'O' if v else 'X'}({d})" for a, v, d in ch))
        print(f"  {'':22s}   → {'INCONCLUSIVE (사건 %d개)' % fires[n] if bad else ('ADOPT' if ok else 'REJECT')}")
    print(f"{NL}  시도 수 N = 13 (사전등록과 같음).")

    # ── 사전등록 밖의 독립 확인. ADOPT 가 나왔을 때만 의미가 있다 ──
    print(f"{NL}{'='*78}{NL}── [사전등록 밖] 히스테리시스 독립 확인 — N 이 늘어난다 ──")
    print("   채택 후보가 나왔으므로 다른 축에서도 버티는지 본다. 이 표로 버퍼를 다시 고르지는 않는다.")
    print(f"{NL}   (1) 레버리지 축 — 6/4 버퍼 고정")
    for lv in (1, 3, 5):
        rep(f"  기준 {lv}배", run(h4, entry, exit_, fl, lv))
        rep(f"  +히스 6/4 {lv}배", hyst_run(2, lv))
    print(f"{NL}   (2) CONF 축 — 버퍼 2 고정 (문턱과 버퍼를 같이 옮긴다)")
    keep = M.CONF
    for conf in (5, 6, 7, 8):
        M.CONF = conf
        d1, hh = B.fetch("1d"), B.fetch("4h")
        rg = M.align_daily(B.bull(d1) >= conf, hh.index).fillna(False).astype(bool)
        hi, lo = hh.high.rolling(M.H4_N).max().shift(1), hh.low.rolling(M.H4_M).min().shift(1)
        kk = hh.index >= pd.Timestamp(START, tz="UTC")
        b = run(hh[kk], ((hh.close > hi) & rg).values[kk], ((hh.close < lo) | ~rg).values[kk], fl, BASE_LEV)
        rep(f"  기준 CONF={conf}", b)
        rep(f"  +히스 {conf}/{conf-2}", hyst_run(2))
    M.CONF = keep

    # ── 독립 표본: Bitstamp BTC/USD 2016~ (우리 표본은 2021-03~. 2016~2021 은 완전히 새 데이터다) ──
    print(f"{NL}   (3) 독립 표본 — Bitstamp BTC/USD 4h 2016~ · 1배 · 편도 {E.cost()*100:.2f}%"
          f" (engine.cost() 하나를 기준선·후보에 같이 적용한다)")
    print("       이게 갈라준다. CONF 축에서 뒤집힌 것이 표본 운인지 진짜 한계인지.")
    import backtest_reversion as RV
    b4, b1 = RV.fetch("bitstamp", "4h"), RV.fetch("bitstamp", "1d")
    bull_b = B.bull(M.add_indicators(b1.copy()))
    hi_b = b4.high.rolling(M.H4_N).max().shift(1)
    lo_b = b4.low.rolling(M.H4_M).min().shift(1)

    def bs_run(buf=None, conf=6, lev=1):
        rg_raw = M.align_daily(bull_b, b4.index).ffill().fillna(0).values
        if buf is None:
            rg = rg_raw >= conf
        else:
            rg, on = np.zeros(len(b4), bool), False
            for i, v in enumerate(rg_raw):
                on = (v >= conf) if not on else (v >= conf - buf)
                rg[i] = on
        rg = pd.Series(rg, index=b4.index)
        en, ex2 = ((b4.close > hi_b) & rg).values, ((b4.close < lo_b) | ~rg).values
        z = np.zeros(len(b4), bool)
        return E.run(b4, en, ex2, z, z, lambda i, c: lev, allow=("long",))

    print(f"       {'':22s}{'전체 누적':>12}{'MDD':>9}{'Sh':>7}{'거래':>6}{'승률':>6}")
    for conf in (5, 6, 7):
        for buf in (None, 1, 2, 3):
            r = bs_run(buf, conf)
            mt = Q.metrics(r[0], r[1].ret.values if len(r[1]) else np.zeros(1), r[2], None, None)
            nm = f"CONF={conf} 기준" if buf is None else f"CONF={conf} +히스 버퍼{buf}"
            print(f"       {nm:22s}{mt['누적']:+11.0f}%{mt['mdd']:8.1f}%{mt['sharpe']:7.2f}"
                  f"{mt['거래']:6d}{mt['승률']:5.0f}%")
        print()


def decomp():
    """히스테리시스 효과가 진입 쪽인가 청산 쪽인가 — 2x2 분해 (2026-09-23).

    국면은 두 군데에 들어간다:  진입 = 국면 & 돌파   ·   청산 = 이탈 | 국면 붕괴
    한쪽씩만 히스테리시스로 바꿔 어느 쪽이 이득을 만드는지 가른다.
    이건 새 후보 탐색이 아니라 **이미 채택된 규칙의 원인 분해**다 — 여기서 나온 수치로 버퍼를 다시 고르지 않는다."""
    d1, h4 = B.fetch("1d"), B.fetch("4h")
    bd = M.align_daily(B.bull(d1), h4.index).ffill().fillna(0).values
    rg_b = bd >= M.CONF
    rg_h = hysteresis(h4.index, M.CONF, M.CONF - 2)
    hi = h4.high.rolling(M.H4_N).max().shift(1)
    lo = h4.low.rolling(M.H4_M).min().shift(1)
    brk, bdn = (h4.close > hi).values, (h4.close < lo).values
    k = np.asarray(h4.index >= pd.Timestamp(START, tz="UTC"))
    fl = Q.reclaim_fills()
    hs = h4[k]

    print(f"OKX BTC 4h {hs.index[0]:%Y-%m-%d}~{hs.index[-1]:%Y-%m-%d} · CONF={M.CONF} · 버퍼 2 (6 켜짐 / 4 미만 꺼짐)")
    print(f"고정 {BASE_LEV}배 · 분봉 하한 재확보 · 고친 엔진")

    # ── 기계 자체를 먼저 본다: 국면이 얼마나 자주, 얼마나 짧게 깜빡이는가 ──
    off = ~rg_b
    runs, i, n = [], 0, len(rg_b)
    while i < n:
        if off[i]:
            j = i
            while j < n and off[j]:
                j += 1
            if i > 0 and j < n:                      # 앞뒤가 모두 ON 인 '깜빡임' 만 센다
                runs.append(j - i)
            i = j
        else:
            i += 1
    runs = np.array(runs)
    print(f"{NL}── 국면이 꺼졌다 켜진 구간 {len(runs)}개의 길이 분포 (4h 봉) ──")
    for lim in (1, 2, 3, 6, 12):
        print(f"   {lim:2d}봉 이하 ({lim*4:3d}시간): {int((runs <= lim).sum()):3d}개 "
              f"({(runs <= lim).mean()*100:4.1f}%)")
    print(f"   중앙값 {np.median(runs):.0f}봉 · 평균 {runs.mean():.1f}봉 · 최장 {runs.max()}봉")
    print(f"   → 버퍼 2 는 이 중 짧은 것들을 메운다. 실제로 메운 구간: "
          f"{int((rg_h & ~rg_b).sum())}봉 (전체의 {(rg_h & ~rg_b).mean()*100:.1f}%)")

    # ── 2x2 ──
    combos = {"기준 (둘 다 기본)": (rg_b, rg_b), "진입만 히스테리시스": (rg_h, rg_b),
              "청산만 히스테리시스": (rg_b, rg_h), "둘 다 (채택 후보)": (rg_h, rg_h)}
    print(f"{NL}{'':24s}{'──── 탐색 ────':>22} |{'──── 검증 ────':>22} |{'────── 전체 ──────':>28}"
          f"{'거래':>5}{'승률':>6}{'최악':>7}{'청산':>4}")
    print(f"{'':24s}{'CAGR':>7}{'MDD':>7}{'Sh':>6} |{'CAGR':>7}{'MDD':>7}{'Sh':>6} |"
          f"{'누적':>9}{'MDD':>7}{'Sh':>6}")
    print()
    R = {}
    for nm, (ri, rx) in combos.items():
        en, ex = (brk & ri)[k], (bdn | ~rx)[k]
        z = np.zeros(len(hs), bool)
        R[nm] = E.run(hs, en, ex, z, z, lambda i, c: BASE_LEV, allow=("long",), fills=fl)
        Q.report(nm, R[nm][0], R[nm][1].ret.values if len(R[nm][1]) else np.zeros(1), R[nm][2])

    # ── 청산 사유 분해 ──
    print(f"{NL}── 청산이 무엇 때문에 났나 (청산 신호가 선 봉에서) ──")
    print(f"   {'':24s}{'구조 이탈만':>12}{'국면 붕괴만':>12}{'둘 다':>10}{'만기/미청산':>12}")
    for nm, (ri, rx) in combos.items():
        t = R[nm][1]
        if not len(t):
            continue
        sig = np.where(k)[0][0]
        a = b = c = o = 0
        for j in t.i_out.values:
            g = sig + j - 1                                  # 청산 신호가 선 봉 (engine 은 i-1 을 본다)
            if g >= len(bdn):
                o += 1; continue
            s_, r_ = bool(bdn[g]), not bool(rx[g])
            a += s_ and not r_; b += r_ and not s_; c += s_ and r_; o += not s_ and not r_
        print(f"   {nm:24s}{a:11d}건{b:11d}건{c:9d}건{o:11d}건")

    f = lambda r, lo_, hi_: Q.metrics(r[0], r[1].ret.values if len(r[1]) else np.zeros(1), r[2], lo_, hi_)
    bs = f(R["기준 (둘 다 기본)"], None, None)["sharpe"]
    print(f"{NL}── 기여 분해 (전체 Sharpe) ──")
    for nm in combos:
        v = f(R[nm], None, None)["sharpe"]
        print(f"   {nm:24s} {v:.2f}   ({v - bs:+.2f})")
    print(f"{NL}   ※ 이 표로 버퍼·문턱을 다시 고르지 않는다. 원인 분해 전용이다.")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    (decomp if "--decomp" in sys.argv else main)()
