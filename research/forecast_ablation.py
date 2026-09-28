"""예측기 조건 넣고 빼기 (2026-09-29, 실행 전 커밋) — forecast.py 와 같은 모델·표본·워크포워드.

조건 묶음 12개 (지금까지 연구에서 다룬 것 중 2020~2026 데이터가 있는 것 전부. 김프는 업비트가 2022 에서 끝나고, OI 는 30일치뿐이라 제외)
  수익률 r1h·r4h·r24h·r7d | 1h괴리 dev1h (급락 매수 신호) | 박스 24h 위치·폭 (횡보 연구) | 변동성 rv1h·24h·7d·비율
  RSI 5m·느린 | 거래량 급증 | 이평거리 50·200일선 (국면 필터) | 펀딩 수준·3일·30일·z | 프리미엄 선물/현물·z
  체결매수 현물 1h·24h·선물 4h | 시간 시·요일 (시간대 연구) | ETH ETH 1h·24h 수익률·ETH−BTC 24h (교차자산 연구)
고르기 (2022·2023·2024 워크포워드 — 그해 이전만 학습):
  방향 = 연도별 AUC 평균(높을수록) · 변동폭 = 하위10%·상위10% 분위 손실(pinball) 평균(낮을수록) — 둘을 따로 고른다.
  후진 제거: 12개 전부에서 시작, 빼서 점수가 떨어지지 않는 묶음 중 가장 좋아지는 것을 하나씩 뺀다. 더 뺄 게 없으면 멈춘다.
  보고: 첫 단계(하나씩 빼기) 변화량 · 묶음 하나만 쓸 때 점수.
확인 (2025·2026 — 고를 때 안 본 구간): 기본 16개(forecast.py 현행) · 12묶음 전부 · 고른 조합을 같은 방식으로 비교.
  고른 조합이 기본보다 확인 구간에서 방향 AUC·변동폭 둘 중 해당 목표에서 나을 때만 forecast.py 반영 후보. 반영은 사용자 결정.
한계: 24h 는 표본이 겹쳐 실효 표본이 작다. 조합 탐색 자체가 다중 비교라 확인 구간 차이가 작으면 우연으로 본다.
사용: python research/forecast_ablation.py   (결과 → research/forecast_ablation_result.txt)
"""
import pathlib
import sys

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import forecast as FC  # noqa: E402

G = {"수익률": ["r1h", "r4h", "r24h", "r7d"], "1h괴리": ["dev1h"], "박스": ["pos24", "w24"],
     "변동성": ["rv1h", "rv24h", "rv7d", "rvr"], "RSI": ["rsi5m", "rsi_slow"], "거래량": ["vz"], "이평거리": ["d50", "d200"],
     "펀딩": ["f_now", "f_3d", "f_30d", "f_z"], "프리미엄": ["basis", "basis_z"], "체결매수": ["tbr1h", "tbr24h", "ftbr4h"],
     "시간": ["hour", "dow"], "ETH": ["eth_r1h", "eth_r24h", "ethbtc24h"]}
BASE = ["수익률", "1h괴리", "박스", "변동성", "RSI", "거래량", "이평거리"]
VAL, HOLD = (2022, 2023, 2024), (2025, 2026)


def extra(c):
    e = FC.heat(c)
    e["hour"], e["dow"] = c.index.hour, c.index.dayofweek
    eth = pd.read_pickle(FC.DC / "binance_ethusdt_5m_2020.pkl").asfreq("5min").ffill().close.reindex(c.index).ffill()
    e["eth_r1h"], e["eth_r24h"] = np.log(eth / eth.shift(12)), np.log(eth / eth.shift(288))
    e["ethbtc24h"] = e.eth_r24h - np.log(c.close / c.close.shift(288))
    return e


def pinball(y, q, a):
    d = y - q
    return np.mean(np.maximum(a * d, (a - 1) * d))


def score(X, y, n, groups, years, what, keep=False):
    cols = [k for g in groups for k in G[g]]
    per, outs = [], []
    for yr in years:
        t0 = pd.Timestamp(f"{yr}-01-01", tz="UTC")
        tr, te = X.index < t0 - pd.Timedelta(minutes=5 * n), X.index.year == yr
        Xt, Xe = X.loc[tr, cols], X.loc[te, cols]
        if what == "방향":
            p = FC.HGC(**FC.KW).fit(Xt, (y[tr] > 0).astype(int)).predict_proba(Xe)[:, 1]
            per.append(roc_auc_score(y[te] > 0, p)); outs.append(pd.DataFrame({"y": y[te], "p": p}))
        else:
            q = {a: FC.HGR(loss="quantile", quantile=a, **FC.KW).fit(Xt, y[tr]).predict(Xe) for a in (0.1, 0.9)}
            per.append(-(pinball(y[te].values, q[0.1], 0.1) + pinball(y[te].values, q[0.9], 0.9)) / 2)
            outs.append(pd.DataFrame({"y": y[te], "q1": q[0.1], "q9": q[0.9]}))
    return (np.mean(per), per, pd.concat(outs)) if keep else np.mean(per)


def fmt(what, s):
    return f"AUC {s:.4f}" if what == "방향" else f"분위손실 {-s * 1e4:.3f}bp"


if __name__ == "__main__":
    lines = []
    P = lambda *a: (print(*a, flush=True), lines.append(" ".join(map(str, a))))  # noqa: E731
    P(__doc__.split("\n")[0])
    c, d1 = FC.history()
    ext = extra(c)
    for name, n in FC.HZ.items():
        X, y, _ = FC.dataset(c, d1, n, ext)
        P(f"\n==== {name} 뒤 (표본 {len(X):,}) ====")
        for what in ("방향", "변동폭"):
            allg = list(G)
            s_all = score(X, y, n, allg, VAL, what)
            P(f"[{what}] 고르기 구간 22~24 · 12묶음 전부 {fmt(what, s_all)} · 기본 {fmt(what, score(X, y, n, BASE, VAL, what))}")
            P("  하나만 쓸 때: " + " · ".join(f"{g} {fmt(what, score(X, y, n, [g], VAL, what)).split()[-1]}" for g in G))
            cur, best, step = allg, s_all, 0
            while len(cur) > 1:
                tr = {g: score(X, y, n, [h for h in cur if h != g], VAL, what) for g in cur}
                if step == 0:
                    P("  하나씩 뺄 때 변화(+ 는 빼면 좋아짐): " + " · ".join(
                        f"{g} {(tr[g] - s_all) * (1 if what == '방향' else 1e4):+.4f}" for g in sorted(tr, key=tr.get, reverse=True)))
                g = max(tr, key=tr.get)
                if tr[g] < best:
                    break
                cur, best, step = [h for h in cur if h != g], tr[g], step + 1
                P(f"  제거 {step}: {g} → {fmt(what, best)} (남은 {len(cur)})")
            P(f"  → 고른 조합: {' · '.join(cur)}")
            P(f"  확인 구간 25~26:")
            for lab, gs in (("기본", BASE), ("전부", allg), ("고른 조합", cur)):
                s, per, o = score(X, y, n, gs, HOLD, what, keep=True)
                extra_txt = ""
                if what == "방향":
                    nb = o.iloc[:: max(n // 12, 1)]; m = (nb.p >= 0.6) | (nb.p <= 0.4)
                    r = np.where(nb.p[m] >= 0.6, nb.y[m], -nb.y[m]) - FC.COST
                    extra_txt = f" · 적중 {((o.p > 0.5) == (o.y > 0)).mean():.1%} · 참고 매매 {m.sum()}회 {np.mean(r) * 100 if m.sum() else float('nan'):+.3f}%"
                else:
                    extra_txt = f" · 구간 적중 {((o.y >= o.q1) & (o.y <= o.q9)).mean():.1%} · 폭 {(o.q9 - o.q1).median() * 100:.2f}%"
                P(f"    {lab:6s} {fmt(what, s)} (25: {per[0]:.4f} · 26: {per[1]:.4f}){extra_txt}" if what == "방향" else
                  f"    {lab:6s} {fmt(what, s)} (25: {-per[0] * 1e4:.3f} · 26: {-per[1] * 1e4:.3f}){extra_txt}")
    (ROOT / "research" / "forecast_ablation_result.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
