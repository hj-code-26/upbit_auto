"""backtest_entry 재배선 검수 — (A) fallback='end' 체결 봉 불일치 수정판 대사 · (B) 분봉 내부 순서 낙관/비관 경계 · (C) 결측.

(A) 원 구현 backtest_entry.simulate: 창 안에서 못 채우면 p=(o[i+1],)*3 을 **봉 i 의 fills 에** 넣는다.
    engine 은 봉 i 진입으로 기록 → 펀딩 1봉 과다 · 봉 i 종가로 평가손익 반영(체결 전) ·
    청산 신호가 체결 전에 이미 선 자리(j ≤ i+1)에서도 같은 가격에 사고파는 거래가 생긴다.
    수정판: 체결 봉을 i+1 로 옮기고(en[i]=True, fills 에 키 없음 → engine 이 o[i+1] 시가 체결),
            j ≤ i+1 이면 거래하지 않는다 (autotrade.act: 대기 중 청산 조건이 켜지면 자리 취소).
(B) 분봉 OHLC 로는 분 안의 순서를 모른다. 신호(분 k 종가 확인) → 주문은 빨라야 분 k+1.
    now     명목 o=분0 시가 · 비관 분0 고가 · 낙관 분0 저가
    reclaim 명목 분k 종가 · 비관 분k+1 고가 · 낙관 분k+1 저가 (체결 후 극단은 분 k+1 부터)
    '재확보 우위' 가 [reclaim 비관 vs now 낙관] 에서도 남는지 본다.
(C) 결측: 120개 이벤트 창 전부 240분 완전 (실측). 결측 처리 규칙만 기록한다.

python entry_audit.py → out/entry_fallback.csv · out/entry_bounds.csv
"""
import sys

import common as C
from common import E, np, pd

import backtest_entry as BE  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")


def simulate_fixed(evs, h4, px_of, lev, fallback):
    o, idx, n = h4.open, h4.index, len(h4)
    en, ex = np.zeros(n, bool), np.zeros(n, bool)
    fl, phantom_avoided = {}, 0
    for ev in evs:
        t, i, j, hi, _ = ev
        if i >= n:
            continue
        p = px_of(ev)
        if p is None:
            if fallback == "skip":
                fl[idx[i]] = None
                en[i - 1] = True
                ex[min(j, n) - 1] = True
                continue
            if i + 1 >= n or j <= i + 1:          # 체결 시각에 이미 청산 조건 → 자리 취소
                phantom_avoided += 1
                continue
            en[i] = True                            # 봉 i+1 시가 체결 (fills 키 없음 → engine 기본 o[i+1])
            ex[min(j, n) - 1] = True
            continue
        if not isinstance(p, tuple):
            p = (p, float(h4.low.iloc[i]), float(h4.high.iloc[i]))
        fl[idx[i]] = p
        en[i - 1] = True
        ex[min(j, n) - 1] = True
    z = np.zeros(n, bool)
    curve, t, st = E.run(h4, en, ex, z, z, lambda i, c: lev, allow=("long",), fills=fl)
    return (curve.iloc[-1] * 100 - 100, (curve / curve.cummax() - 1).min() * 100, len(t), st["liq"], phantom_avoided,
            C.sharpe_cv(C.daily(curve).values))


evs, h4 = BE.events()
mins = BE.minutes(evs)
evs = [e for e in evs if len(mins[e[0]][1]) >= 10]
F = {t: BE.fills(*mins[t], hi) for t, _, _, hi, _ in evs}
names = ["now", "dip0.3", "dip0.6", "dip1.0", "dip1.5", "retest", "wick", "vwap", "floor2", "hold_hi", "hold_hi15", "reclaim"]
rows = []
for lev in (1, 3, 5):
    for nm in names:
        a = BE.simulate(evs, h4, lambda e, nm=nm: F[e[0]].get(nm), lev, "end")
        b = simulate_fixed(evs, h4, lambda e, nm=nm: F[e[0]].get(nm), lev, "end")
        miss = sum(F[e[0]].get(nm) is None for e in evs)
        rows.append({"lev": lev, "policy": f"{nm} (end)", "unfilled_events": miss, "orig_cum%": a[0], "fixed_cum%": b[0],
                     "orig_trades": a[2], "fixed_trades": b[2], "phantom_removed": b[4], "orig_liq": a[5], "fixed_liq": b[3]})
FB = pd.DataFrame(rows)
FB.to_csv(C.OUT / "entry_fallback.csv", index=False, encoding="utf-8-sig")
pd.set_option("display.width", 250)
print(FB.round(1).to_string(index=False))


# ── (B) 낙관/비관 경계 ──
def bound(w, hi, pol, mode):
    if pol == "now":
        px = {"nom": w.open.iloc[0], "pess": w.high.iloc[0], "opt": w.low.iloc[0]}[mode]
        return BE.after(w, float(px), 0)
    r = BE.reclaim_event(w, hi)
    if r is None:
        return None
    px, ts = r
    k = int(w.index.get_indexer([ts])[0])
    if mode == "nom":
        return BE.after(w, px, k)
    k1 = min(k + 1, len(w) - 1)
    return BE.after(w, float(w.high.iloc[k1] if mode == "pess" else w.low.iloc[k1]), k1)


G = {(pol, mode): {t: bound(mins[t][1], hi, pol, mode) for t, _, _, hi, _ in evs}
     for pol in ("now", "reclaim") for mode in ("nom", "pess", "opt")}
out = []
for lev in (1, 3):
    R = {}
    for (pol, mode), g in G.items():
        r = BE.simulate(evs, h4, lambda e, g=g: g[e[0]], lev, "skip" if pol == "reclaim" else "end")
        R[(pol, mode)] = r
        out.append({"lev": lev, "policy": pol, "fill": mode, "cum%": r[0], "mdd%": r[1], "trades": r[2], "liq": r[5], "avg_improve_bp": r[6]})
    for a, b in (("nom", "nom"), ("pess", "opt"), ("opt", "pess"), ("pess", "pess")):
        out.append({"lev": lev, "policy": f"reclaim[{a}] − now[{b}]", "fill": "Δ",
                    "cum%": R[("reclaim", a)][0] - R[("now", b)][0]})
BD = pd.DataFrame(out)
BD.to_csv(C.OUT / "entry_bounds.csv", index=False, encoding="utf-8-sig")
print(BD.round(1).to_string(index=False))

# ── 체결 시각표 (문서용): 정책별 신호 확정 → 주문 가능 → 체결 ──
print("\n정책별 체결 시각 (분 인덱스, 창 시작 = 신호봉 종료 = 다음 4h 시가):")
print("  now       신호 확정 t0 · 주문 t0 · 체결 분0 시가 (지연 0 가정) · 극단 시작 분0")
print("  reclaim   분k 종가 확인 · 주문 ≥ 분k 종가 · 체결 분k 종가(명목, 지연 0) · 극단 시작 분k(체결 전 분k 저가 포함=보수적)")
print("  hold_hi15 분0~15 저가 확인 · 주문 ≥ 분15 종가 · 체결 분15 종가 · 극단 시작 분15 — 체결가가 관찰 창보다 앞서지 않는다")
print("  dipX      지정가 lim · 분k 저가 ≤ lim 이면 lim 체결(대기열 무시=낙관) · 극단 시작 분k")
print("  Q.reclaim_fills 는 체결 후 극단을 분k **이후**(w.index > ts) 로 잡는다 — backtest_entry.after(분k 포함) 와 다르다")
