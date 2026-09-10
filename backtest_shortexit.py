"""숏 청산을 '고가 돌파' 가 아닌 다른 축으로 다시 설계해 본다.

research_both_sides.txt '후속 2' 에서 원인이 좁혀졌다 — 숏은 자리를 못 맞추는 게 아니라 **나오는 데서 진다**:
  · 이긴 거래 평균은 롱과 같다 (+5.56% vs +5.55%)
  · 그런데 최대 유리 이동(MFE +4.69%) 중 44% 만 실현한다 (롱 56%)
  · 구조 청산(직전 6봉 고가 돌파)이 급반등 한 번에 털리기 때문
그리고 backtest_downside ① 에서 그 청산선을 1~3봉으로 당겨 봐도 12조합 전부 음수였다 —
당기면 이번엔 이긴 거래가 잘린다. 그래서 **같은 축(고가 돌파)에서는 답이 없다**.

여기서는 축을 바꾼다: 시간 청산 · ATR 추적 · 고정 익절 · 그 조합.
진입은 두 가지로 잰다 — 4h 시가 시장가, 그리고 롱과 같은 대우(분봉 상한 재확보).
롱도 같은 청산으로 같이 재서 대조군으로 둔다. 비용·펀딩·격리청산은 backtest_quant 와 같다.

사용: python backtest_shortexit.py
"""
import sys

import numpy as np
import pandas as pd

import backtest_both as BO
import backtest_quant as Q
import minute_data as MD

SLIP = 0.0005          # 스톱·추적 체결은 슬리피지가 더 붙는다 (backtest_exits.py 와 같은 가정)
ATR_N = 14
WIN_H = 4


def atr(h4, n=ATR_N):
    tr = pd.concat([h4.high - h4.low, (h4.high - h4.close.shift()).abs(),
                    (h4.low - h4.close.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean().shift(1)     # 직전 봉까지의 정보


def reclaim_fills(h4, idxs, level_of, side, m1):
    """롱과 같은 대우: 돌파당한 선을 분봉이 지키거나 되찾을 때 진입. 못 하면 None(자리 포기)."""
    out = {}
    for i in idxs:
        t0 = h4.index[i]
        w = m1[(m1.index >= t0) & (m1.index < t0 + pd.Timedelta(hours=WIN_H))]
        if len(w) < 10:
            continue
        lvl = level_of(i)
        lost = (w.low < lvl).values if side > 0 else (w.high > lvl).values
        if not lost.any():
            out[t0] = float(w.open.iloc[0])
            continue
        back = w.index[((w.close > lvl) if side > 0 else (w.close < lvl)) & (w.index > w.index[int(lost.argmax())])]
        out[t0] = float(w.close[back[0]]) if len(back) else None
    return out


def events(h4, en, ex, start):
    out, held = [], False
    for i in range(1, len(h4)):
        if held:
            held = not ex[i - 1]
        elif en[i - 1] and h4.index[i] >= start:
            out.append(i)
            held = True
    return out


def run(h4, en, ex, side, lev=1, fills=None, hold=0, trail_atr=0.0, stop_atr=0.0, tp=0.0, struct=True):
    """청산 축을 조합해 돌린다. 봉 안 체결(추적·손절·익절)은 저가/고가로 판정하고 슬리피지를 더한다.
    hold: N봉 뒤 무조건 청산 · trail_atr: 최유리가 대비 k×ATR 되돌림 · stop_atr: 진입가 대비 k×ATR · tp: 진입가 대비 %

    청산 판정 순서: 시가 체결(구조·만기) → 격리청산 → ATR손절 → 추적 → 익절.
    봉 안 경로를 모르므로 불리한 쪽을 먼저 본다. 순서를 뒤집으면(익절 먼저) 성적이 조용히 부풀려진다."""
    o, hi, lo, c, idx = h4.open.values, h4.high.values, h4.low.values, h4.close.values, h4.index
    A = atr(h4).values
    eq, held, rows, liq = 1.0, None, [], 0
    curve = np.ones(len(h4))
    for i in range(1, len(h4)):
        if held:
            e, k, best, a0 = held
            k += 1
            px, why = None, ""
            # ── 순서가 중요하다 ──
            # ① 시가 체결 청산(구조·만기)은 봉이 열리자마자 나간다. 그 뒤 봉 안에서 무슨 일이 있었든 상관없다.
            #    이걸 나중에 보면 '구조 청산으로 나갔어야 할 자리에서 봉 안 고점 익절' 을 챙기게 되어 성적이 부풀려진다.
            # ② 봉 안 체결은 경로를 모르므로 **불리한 것부터** 본다 (청산 → 손절/추적 → 익절). 보수적인 쪽.
            if struct and ex[i - 1]:
                px, why = o[i], "구조"
            elif hold and k >= hold:
                px, why = o[i], "만기"
            if px is None:
                if (lo[i] <= e * (1 - 1 / lev)) if side > 0 else (hi[i] >= e * (1 + 1 / lev)):
                    eq, held, liq = 0.0, None, liq + 1
                    rows.append({"ret": -1.0, "bars": k, "why": "청산"})
                    curve[i] = 0.0
                    continue
                best = max(best, hi[i]) if side > 0 else min(best, lo[i])
                if stop_atr:                                          # 진입가 기준 ATR 손절
                    st = e - side * stop_atr * a0
                    if (lo[i] <= st) if side > 0 else (hi[i] >= st):
                        px, why = st * (1 - side * SLIP), "ATR손절"
                if px is None and trail_atr:                          # 최유리가 대비 되돌림
                    st = best - side * trail_atr * a0
                    if (lo[i] <= st) if side > 0 else (hi[i] >= st):
                        px, why = st * (1 - side * SLIP), "추적"
                if px is None and tp:                                 # 익절은 마지막 (지정가라 슬리피지 없음)
                    lim = e * (1 + side * tp / 100)
                    if (hi[i] >= lim) if side > 0 else (lo[i] <= lim):
                        px, why = lim, "익절"
            if px is not None:
                r = side * lev * (px / e - 1) - 2 * lev * Q.COST - side * k * Q.FUND * lev
                eq *= 1 + r
                rows.append({"ret": r, "bars": k, "why": why})
                held = None
            else:
                held = (e, k, best, a0)
        if held is None and eq > 0 and en[i - 1] and np.isfinite(A[i]):
            px = o[i] if fills is None else fills.get(idx[i], o[i])
            if px is not None:
                held = (px, 0, px, A[i])
        curve[i] = eq * (1 + side * lev * (c[i] / held[0] - 1)) if held else eq
        if eq <= 0:
            curve[i:] = 0.0
            break
    t = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["ret", "bars", "why"])
    return pd.Series(curve, index=idx), t, liq


def line(name, cv, t, liq, start, split):
    cv = cv[start:] / cv[start:].iloc[0]
    r = t.ret.values if len(t) else np.zeros(1)
    a, b, f = (Q.metrics(cv, r, liq, *s) for s in ((None, split), (split, None), (None, None)))
    top = t.why.value_counts().head(2).to_dict() if len(t) else {}
    print(f"{name:26s}{a['cagr']:+7.0f}%{b['cagr']:+7.0f}%{f['누적']:+9.0f}%{f['mdd']:7.1f}%{f['sharpe']:6.2f}"
          f"{f['거래']:5d}{f['승률']:5.0f}%{t.bars.mean() if len(t) else 0:6.1f}"
          f"  {' · '.join(f'{k} {v}' for k, v in top.items())}")


def main():
    h4, l_en, l_ex, s_en, s_ex, lab = BO.frames()
    start = pd.Timestamp(Q.START, tz="UTC")
    m1 = MD._load()[0]
    m1 = m1[~m1.index.duplicated()].sort_index()
    hi_n = h4.high.rolling(12).max().shift(1)
    lo_n = h4.low.rolling(12).min().shift(1)
    sf = reclaim_fills(h4, events(h4, s_en, s_ex, start), lambda i: float(lo_n.iloc[i - 1]), -1, m1)
    lf = reclaim_fills(h4, events(h4, l_en, l_ex, start), lambda i: float(hi_n.iloc[i - 1]), +1, m1)
    print(f"OKX BTC 4h {start:%Y-%m-%d}~{h4.index[-1]:%Y-%m-%d} · 1배 · 편도 {Q.COST * 100:.2f}% (+스톱 슬리피지 0.05%)")
    print(f"숏 진입 이벤트 {len(sf)}건 (분봉 재확보 실패 {sum(v is None for v in sf.values())}건)\n")
    hdr = f"{'':26s}{'탐색':>7}{'검증':>7}{'누적':>9}{'MDD':>7}{'Sh':>6}{'거래':>5}{'승률':>5}{'보유봉':>6}  청산 사유 상위"
    S, L = Q.SPLIT, None

    cases = [
        ("[현재] 구조 (6봉 고가)", dict(struct=True)),
        ("시간 3봉 (0.5일)", dict(struct=False, hold=3)),
        ("시간 6봉 (1일)", dict(struct=False, hold=6)),
        ("시간 12봉 (2일)", dict(struct=False, hold=12)),
        ("시간 24봉 (4일)", dict(struct=False, hold=24)),
        ("ATR 추적 1.0", dict(struct=False, trail_atr=1.0)),
        ("ATR 추적 2.0", dict(struct=False, trail_atr=2.0)),
        ("ATR 추적 3.0", dict(struct=False, trail_atr=3.0)),
        ("ATR 손절 2.0 + 구조", dict(struct=True, stop_atr=2.0)),
        ("익절 3% + 구조", dict(struct=True, tp=3.0)),
        ("익절 5% + 구조", dict(struct=True, tp=5.0)),
        ("익절 5% + 시간 12봉", dict(struct=False, tp=5.0, hold=12)),
        ("익절 5% + ATR추적 2.0", dict(struct=False, tp=5.0, trail_atr=2.0)),
        ("구조 + 시간 12봉 상한", dict(struct=True, hold=12)),
    ]

    print("── 숏 · 진입 = 4h 시가 시장가 ──")
    print(hdr)
    for nm, kw in cases:
        line(nm, *run(h4, s_en, s_ex, -1, fills=None, **kw), start, S)

    print("\n── 숏 · 진입 = 분봉 상한 재확보 (롱과 같은 대우) ──")
    print(hdr)
    for nm, kw in cases:
        line(nm, *run(h4, s_en, s_ex, -1, fills=sf, **kw), start, S)

    print("\n── 대조군: 롱에 같은 청산을 붙이면 (진입 = 분봉 하한 재확보) ──")
    print(hdr)
    for nm, kw in cases:
        line(nm, *run(h4, l_en, l_ex, +1, fills=lf, **kw), start, S)

    # 자체 점검: 구조 청산만 켠 숏이 backtest_both 의 숏만 1배와 같아야 한다
    cv, t, lq = run(h4, s_en, s_ex, -1, fills=None, struct=True)
    ref = BO.simulate(h4, (l_en, l_ex, s_en, s_ex), lambda i, c: 1, lab, ("short",))
    a = cv[start:].iloc[-1] / cv[start:].iloc[0]
    b = ref[0][start:].iloc[-1] / ref[0][start:].iloc[0]
    assert abs(a / b - 1) < 0.02, f"구조 청산 숏이 기존 결과와 다르다: {a:.3f} vs {b:.3f}"
    print(f"\nok  자체 점검: 구조 청산 숏이 backtest_both 결과와 일치 ({a * 100 - 100:+.0f}% vs {b * 100 - 100:+.0f}%)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
