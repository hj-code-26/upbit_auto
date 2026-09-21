"""레버리지 다이얼 — 고정 1/2/3/5배를 고친 엔진 + 재확보 경로에서 나란히 잰다 (2026-09-21).

이건 새 전략이 아니다. 이미 구현돼 있고 지금 .env 에서 5로 놓인 손잡이 하나를,
**지금 믿는 숫자로** 다시 보는 것이다. 그래서 '가설 검증' 이 아니라 '선택' 이다.

왜 지금인가
  · 2026-09-10 에 3 -> 5 로 올렸다. 그 결정의 근거 수치는 **감사 전 엔진** 것이다.
  · 감사가 "5배는 상위 5개 거래를 빼면 누적 -26%, 부트스트랩 하위 5분위 CAGR -14%(음수 10%)" 를 남겼다.
  · VT(변동성 타겟팅)는 그 꼬리위험을 줄이는 기계장치였는데 두 번의 사전등록 검증에서 전부 기각됐다
    (research_vt.txt). 기계장치가 없으면 **손잡이 자체를 다시 보는 것** 말고 남는 수가 없다.
  · 그리고 2026-09-21 에 backtest_entry 의 체결 루프를 engine.run 으로 갈아끼워, 이제 세 파일이
    같은 숫자를 내는지 대사가 된다 (research_entry_timing.txt). 비로소 재측정할 값어치가 생겼다.

조건 (전부 지금 봇과 같게)
  진입 = 분봉 하한 재확보 · 편도 0.07% · 펀딩 0.01%/8h · 격리 청산(정수 올림 + MMR) · CONF=6 · 롱 전용.
  후보 = 고정 1 · 2 · 3 · 5배 (autotrade.MAX_LEVERAGE = 5 가 코드 상한이다).
  참고행 = 4 · 7 · 10배. **후보가 아니다.** 상한을 넘기므로 표에 맥락으로만 찍고 선택 대상에서 제외한다.

=== 선택 규칙 (결과 보기 전에 박는다. 사전순 관문이다) ===
  관문 A  강제청산 0회                      — 5배 격리에서 청산 1회 = 증거금 전액. 협상 대상이 아니다.
  관문 B  상위 5개 거래 제외 누적 > 0        — 대박 몇 번을 빼도 살아남는가. 감사가 남긴 결정적 통계다.
  관문 C  block bootstrap 하위 5분위 CAGR > 0 — 운이 나쁜 경로에서도 원금을 지키는가.
  세 관문을 **모두** 통과한 후보 중 **검증 구간(2024-01~) Sharpe 최대**. 동률이면 **낮은 쪽**.

  누적 수익률로 고르지 않는다. 그렇게 고르면 언제나 가장 높은 배율이 이기고, 그건 이미 알고 있다.
  **아무 후보도 세 관문을 통과하지 못하면 '권고 없음' 으로 적는다** — 그 경우 결론은 레버리지가 아니라
  "이 전략 자체가 소수 거래에 의존한다" 이고, 그건 배율을 바꿔서 풀 문제가 아니다.
  결과를 보고 관문·후보·가중치를 바꾸지 않는다.

같이 찍는 것 (선택에는 안 쓰고 해석에만 쓴다)
  · 상위 N개 제외 누적을 N = 0/1/3/5/10 으로 — 의존도가 배율에 따라 어떻게 꺾이는지 곡선으로 본다.
  · 최대 연속손실 횟수 · profit factor · 최악 거래 — 사람이 실제로 견딜 수 있는가에 붙는 숫자들.
  · 부트스트랩 전 분위(5/25/50/75/95)와 음수 비율.

불변식 (assert)
  (a) 레버리지 0 이면 무거래 · 자본 1.0
  (b) 배율이 오르면 최악 거래 손실과 MDD 가 단조로 나빠진다 (아니면 배선 오류다)
  (c) 1배 누적이 backtest_entry 의 reclaim 1배와 일치한다 (파일 간 대사)

사용: python backtest_lev.py   (make lev)
결과: research_lev.txt
"""
import sys

import numpy as np
import pandas as pd

from isolation import guard

guard()

import backtest_quant as Q                                          # noqa: E402
import engine as E                                                  # noqa: E402
import model as M                                                   # noqa: E402

NL = chr(10)
CANDS = (1, 2, 3, 5)             # autotrade.MAX_LEVERAGE = 5 가 코드 상한
CONTEXT = (4, 7, 10)             # 표에만 찍는다. 선택 대상 아님
BLOCK, DRAWS = 20, 2000          # block bootstrap (backtest_sizing §4 와 같은 설정)
START, SPLIT, YEAR = Q.START, Q.SPLIT, Q.YEAR


def sim(h4, en, ex, fills, lev):
    z = np.zeros(len(h4), bool)
    return E.run(h4, en, ex, z, z, lambda i, c: lev, allow=("long",), fills=fills)


def ex_top(r, n):
    """상위 n개 거래를 빼고 다시 복리 (%)."""
    s = np.sort(r)[::-1]
    return (np.prod(1 + s[n:]) - 1) * 100 if len(s) > n else -100.0


def boot(curve, seed=0):
    """일별 수익률 block bootstrap → (5,25,50,75,95 분위 CAGR%, 음수 비율%)."""
    d = curve.resample("D").last().dropna().pct_change().dropna().values
    rng, out, nb = np.random.default_rng(seed), [], len(d) // BLOCK
    for _ in range(DRAWS):
        st = rng.integers(0, len(d) - BLOCK, nb)
        x = np.concatenate([d[j:j + BLOCK] for j in st])
        g = np.prod(1 + x)
        out.append((g ** (YEAR / len(x)) - 1) * 100 if g > 0 else -100.0)
    out = np.array(out)
    return np.percentile(out, [5, 25, 50, 75, 95]), (out < 0).mean() * 100


def streak(r):
    s = m = 0
    for x in r:
        s = s + 1 if x <= 0 else 0
        m = max(m, s)
    return m


def main():
    h4, entry, exit_, _, _ = Q.frames()
    m = h4.index >= pd.Timestamp(START, tz="UTC")
    h4, entry, exit_ = h4[m], entry[m], exit_[m]
    fl = Q.reclaim_fills()
    print(f"OKX BTC 4h {h4.index[0]:%Y-%m-%d}~{h4.index[-1]:%Y-%m-%d} · CONF={M.CONF} · 편도 {Q.COST*100:.2f}%"
          f" · 진입 분봉 하한 재확보 · 롱 전용")
    print(f"후보 {CANDS} (코드 상한 5) · 참고 {CONTEXT} · bootstrap 블록 {BLOCK}일 x {DRAWS:,}회")

    R = {}
    for lev in sorted(set(CANDS + CONTEXT)):
        c, t, st = sim(h4, entry, exit_, fl, lev)
        r = t.ret.values if len(t) else np.zeros(1)
        q, neg = boot(c)
        R[lev] = {"curve": c, "r": r, "st": st, "q": q, "neg": neg,
                  "탐색": Q.metrics(c, r, st, START, SPLIT), "검증": Q.metrics(c, r, st, SPLIT, None),
                  "전체": Q.metrics(c, r, st, START, None)}

    assert sim(h4, entry, exit_, fl, 0)[0].iloc[-1] == 1.0, "(a) 레버리지 0 인데 자본이 변했다"
    worst = [R[l]["전체"]["최악"] for l in sorted(R)]
    mdds = [R[l]["전체"]["mdd"] for l in sorted(R)]
    assert all(a >= b - 1e-9 for a, b in zip(worst, worst[1:])), f"(b) 최악 거래가 배율에 단조가 아니다: {worst}"
    assert all(a >= b - 1e-9 for a, b in zip(mdds, mdds[1:])), f"(b) MDD 가 배율에 단조가 아니다: {mdds}"
    import backtest_entry as BE
    evs, h4e = BE.events()
    mins = BE.minutes(evs)
    evs = [e for e in evs if len(mins[e[0]][1]) >= 10]
    F = {t: BE.fills(*mins[t], hi) for t, _, _, hi, _ in evs}
    mine = BE.simulate(evs, h4e, lambda e: F[e[0]].get("reclaim"), 1, "skip")[0]
    assert abs(mine - R[1]["전체"]["누적"]) < 0.5, f"(c) 1배 대사 불일치: {mine:+.0f}% vs {R[1]['전체']['누적']:+.0f}%"
    print(f"ok  불변식 (a) 레버 0 = 무거래 · (b) 최악·MDD 가 배율에 단조 · (c) 1배 누적이 backtest_entry 와 일치")

    print(f"{NL}{'':10s}{'──── 탐색 ────':>22} |{'──── 검증 ────':>22} |{'───── 전체 ─────':>26}"
          f"{'거래':>5}{'승률':>6}{'최악':>8}{'청산':>5}")
    print(f"{'':10s}{'CAGR':>7}{'MDD':>7}{'Sh':>6} |{'CAGR':>7}{'MDD':>7}{'Sh':>6} |{'누적':>9}{'MDD':>7}{'Sh':>6}")
    for lev in sorted(R):
        d = R[lev]
        tag = f"{lev}배" + ("" if lev in CANDS else " (참고)")
        print(f"{tag:10s}{d['탐색']['cagr']:+7.0f}%{d['탐색']['mdd']:6.1f}%{d['탐색']['sharpe']:6.2f} |"
              f"{d['검증']['cagr']:+7.0f}%{d['검증']['mdd']:6.1f}%{d['검증']['sharpe']:6.2f} |"
              f"{d['전체']['누적']:+8.0f}%{d['전체']['mdd']:6.1f}%{d['전체']['sharpe']:6.2f}"
              f"{d['전체']['거래']:5d}{d['전체']['승률']:5.0f}%{d['전체']['최악']:7.1f}%{d['st']['liq']:5d}")

    print(f"{NL}── 상위 N개 거래를 빼면 누적이 얼마인가 (의존도 곡선) ──")
    print(f"{'':10s}" + "".join(f"{'상위'+str(n)+'제외':>12}" for n in (0, 1, 3, 5, 10)))
    for lev in sorted(R):
        print(f"{str(lev)+'배':10s}" + "".join(f"{ex_top(R[lev]['r'], n):+11.0f}%" for n in (0, 1, 3, 5, 10)))

    print(f"{NL}── block bootstrap CAGR 분위 · 견딜 수 있는가 ──")
    print(f"{'':10s}{'5%':>8}{'25%':>8}{'중앙':>8}{'75%':>8}{'95%':>8}{'음수비율':>9}"
          f"{'최대연속손실':>13}{'PF':>7}")
    for lev in sorted(R):
        d, r = R[lev], R[lev]["r"]
        w, l = r[r > 0], r[r <= 0]
        pf = w.sum() / abs(l.sum()) if len(l) and l.sum() else float("inf")
        print(f"{str(lev)+'배':10s}" + "".join(f"{x:+7.0f}%" for x in d["q"])
              + f"{d['neg']:8.0f}%{streak(r):12d}회{pf:7.2f}")

    # ── 실계좌 사이징 제약: 백테스트는 이걸 모른다 ──
    eq = float(next((a.split("=")[1] for a in sys.argv if a.startswith("--equity=")), 7.28))
    px = float(next((a.split("=")[1] for a in sys.argv if a.startswith("--price=")), 80996))
    CT, MINC, MINO, RES = 0.01, 0.01, 10.0, 0.01          # 1계약=0.01BTC · 최소 0.01계약 · okx.MIN_ORDER · CASH_RESERVE
    unit = CT * MINC * px                                  # 최소 주문 한 칸의 명목 (USDT)
    print(f"{NL}── 실계좌 사이징 제약 (자산 {eq:.2f} USDT · BTC {px:,.0f} · 최소 주문 한 칸 {unit:.2f} USDT) ──")
    print("   백테스트는 명목을 연속으로 잡지만 거래소는 계약 단위로 내림한다. 잔고가 작으면 이 손실이 크다.")
    print(f"   {'설정':>6}{'목표 명목':>12}{'실제 계약':>10}{'실제 명목':>12}{'실효 배율':>11}{'달성률':>9}  비고")
    for lev in sorted(set(CANDS + CONTEXT)):
        tgt = eq * 1.0 * lev * (1 - RES)
        n = int(tgt // unit)
        act = n * unit
        note = "주문 불가 (최소 명목 미달)" if tgt < MINO or n == 0 else ""
        print(f"   {str(lev)+'배':>6}{tgt:11.2f}U{n * MINC:10.2f}{act:11.2f}U{act / eq:10.2f}배"
              f"{(act / tgt * 100 if tgt else 0):8.0f}%  {note}")

    print(f"{NL}═══ 사전 등록 선택 규칙 적용 ═══")
    ok = []
    for lev in CANDS:
        d = R[lev]
        a, b, c = d["st"]["liq"] == 0, ex_top(d["r"], 5) > 0, d["q"][0] > 0
        print(f"  {lev}배  관문A 청산0 {'O' if a else 'X'}({d['st']['liq']}회) · "
              f"관문B 상위5제외 {'O' if b else 'X'}({ex_top(d['r'],5):+.1f}%) · "
              f"관문C 부트5분위 {'O' if c else 'X'}({d['q'][0]:+.2f}%)"
              f"  → {'통과' if a and b and c else '탈락'}")
        if a and b and c:
            ok.append(lev)
    if not ok:
        print(f"{NL}  → **권고 없음.** 세 관문을 통과한 후보가 없다.")
        print("     사전등록대로: 결론은 레버리지가 아니라 '이 전략이 소수 거래에 의존한다' 이고")
        print("     배율을 바꿔서 풀 문제가 아니다.")
    else:
        best = max(ok, key=lambda l: (R[l]["검증"]["sharpe"], -l))
        print(f"{NL}  통과 {ok} · 검증 Sharpe " + " · ".join(f"{l}배 {R[l]['검증']['sharpe']:.2f}" for l in ok))
        print(f"  → **{best}배** (통과자 중 검증 Sharpe 최대, 동률이면 낮은 쪽)")
        print(f"     지금 .env LEVERAGE=5 → {'변경 불필요' if best == 5 else f'{best} 로 낮추는 것을 권고'}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
