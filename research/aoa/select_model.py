"""aoa 진입 '선택' 모방 모델 — 사전등록 (2026-09-26, clone.py 전 조합 불합격 후 새로 세팅).

동기: decompose.py — 그의 실제 진입을 5분 늦게 따라가도 TP2/SL4 기계 청산으로 4개 연도 전부 +0.15~0.57%/거래(비용 전).
     반면 일반 역추세 규칙(clone.py)은 0/48. → 그가 '어느 순간을 고르는지'를 학습해 본다.

라벨: 5분봉 t 의 특징으로 '다음 봉(t+1) 안에 그가 롱(숏) 에피소드를 시작하는가'. 특징 = features.build (완성봉만).
모델: HistGradientBoosting 이진 분류 2개(롱·숏). 학습 2018-03~2020-12, 그의 데이터 안 검증 2021.
신호: 학습 구간 점수 분포의 상위 q (q ∈ {0.5%, 1%, 2%}) 문턱을 학습 구간에서 고정 → 그대로 OKX 에 적용.
체결: 신호 봉 다음 봉 시가 진입, 청산 TP/SL ∈ {(1,2), (2,4)}%, 시간손절 1일, 동시 1포지션, 편도 비용 0.07%.

판정 (사전 고정):
  ① 모방 정확도: 2021 에서 AUC ≥ 0.60 (0.5=무작위), 상위 신호의 방향 정확도(그가 그 방향으로 ±30분 안에 진입) > 40%.
     — 사용자 규칙: 정확도 40% 이하면 즉시 폐기.
  ② 수익: OKX OOS 전반(2022-01~2024-04)·후반(2024-05~) 둘 다 > 0 인 조합이 12개(q 3 × 청산 2 × 롱만/양방향 2) 중 과반(7+).
  둘 다 통과해야 '봇 이식 후보'. 아니면 폐기.
"""
import pathlib
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import features as F  # noqa: E402
from clone import okx_5m  # noqa: E402

ROOT = F.ROOT
COST = 0.0007
COLS = [c for c in pd.read_pickle(ROOT / "data_cache" / "aoa_feat_5m.pkl").columns]


def labels(f):
    ep = pd.read_parquet(ROOT / "data_cache" / "aoa_episodes.parquet")
    bar = pd.DatetimeIndex(ep.start).tz_localize("UTC").floor("5min") - pd.Timedelta("5min")   # 특징 봉 = 진입 봉 직전
    L = pd.Series(0, index=f.index); S = pd.Series(0, index=f.index)
    L.loc[bar[(ep.state == 1).values].intersection(f.index)] = 1
    S.loc[bar[(ep.state == -1).values].intersection(f.index)] = 1
    return L, S


def fill_pos(f):
    f = f.copy()
    pc = [c for c in f.columns if c.startswith("pos_")]
    f[pc] = f[pc].fillna(0.5)
    return f


def sim(c, sl_long, sl_short, tp, sl, max_bars=288, cost=COST):
    """신호 배열(bool) → 거래 [(시각, 방향, 순수익%)]. 롱·숏 동시 신호면 건너뛴다."""
    o, h, l = c.open.values, c.high.values, c.low.values
    out, i, n = [], 0, len(o)
    while i < n - max_bars - 2:
        s = 1 if sl_long[i] and not sl_short[i] else (-1 if sl_short[i] and not sl_long[i] else 0)
        if s == 0:
            i += 1; continue
        p = o[i + 1]; up, dn = p * (1 + s * tp / 100), p * (1 - s * sl / 100)
        for j in range(i + 1, i + 1 + max_bars):
            if (l[j] <= dn) if s == 1 else (h[j] >= dn):
                r = -sl; break
            if (h[j] >= up) if s == 1 else (l[j] <= up):
                r = tp; break
        else:
            r = s * (o[j + 1] / p - 1) * 100
        out.append((c.index[i], s, r - 2 * cost * 100))
        i = j + 1
    return out


def summ(tr, lo, hi):
    r = np.array([x for t, s, x in tr if lo <= t < hi])
    if len(r) == 0:
        return 0, 0.0, np.nan
    eq = np.cumprod(1 + r / 100)
    return len(r), (eq[-1] - 1) * 100, (r > 0).mean()


if __name__ == "__main__":
    T = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731
    f = fill_pos(pd.read_pickle(ROOT / "data_cache" / "aoa_feat_5m.pkl")).loc["2018-03-12":"2021-12-31"]
    f = f.dropna()
    L, S = labels(f)
    tr, te = f.index < T("2021-01-01"), f.index >= T("2021-01-01")
    mk = lambda: HistGradientBoostingClassifier(max_depth=4, max_iter=300, learning_rate=0.05,  # noqa: E731
                                               class_weight="balanced", random_state=0)
    mL = mk().fit(f[tr], L[tr]); mS = mk().fit(f[tr], S[tr])
    pL_tr, pS_tr = mL.predict_proba(f[tr])[:, 1], mS.predict_proba(f[tr])[:, 1]
    pL, pS = mL.predict_proba(f[te])[:, 1], mS.predict_proba(f[te])[:, 1]
    print("① 2021 AUC 롱", round(roc_auc_score(L[te], pL), 3), "숏", round(roc_auc_score(S[te], pS), 3))
    # 방향 정확도: 상위 신호 중 그가 ±30분(±6봉) 안에 같은 방향 진입
    near = lambda s: s.rolling(13, center=True, min_periods=1).max()  # noqa: E731
    Ln, Sn = near(L[te]).values, near(S[te]).values
    th = {}
    for q in (0.005, 0.01, 0.02):
        th[q] = (np.quantile(pL_tr, 1 - q), np.quantile(pS_tr, 1 - q))
        gl, gs = pL >= th[q][0], pS >= th[q][1]
        both = gl & gs
        gl, gs = gl & ~both, gs & ~both
        hitL = Ln[gl].mean() if gl.any() else np.nan; hitS = Sn[gs].mean() if gs.any() else np.nan
        wrongL = Sn[gl].mean() if gl.any() else np.nan; wrongS = Ln[gs].mean() if gs.any() else np.nan
        dir_acc = (Ln[gl].sum() + Sn[gs].sum()) / max(Ln[gl].sum() + Sn[gs].sum() + Sn[gl].sum() + Ln[gs].sum(), 1)
        print(f"  상위 {q:.1%}: 신호 롱 {gl.sum()} 숏 {gs.sum()} | 그가 ±30분 내 같은 방향 진입: 롱 {hitL:.2f} 숏 {hitS:.2f}"
              f" | 반대 방향 진입: 롱 {wrongL:.2f} 숏 {wrongS:.2f} | 방향 정확도(진입 있던 신호 중) {dir_acc:.3f}")
    # ② OKX OOS 수익
    c = okx_5m().loc["2021-12-01":]
    g = fill_pos(F.build(c))[COLS]
    ok = g.notna().all(axis=1).values
    qL, qS = mL.predict_proba(g.fillna(0))[:, 1], mS.predict_proba(g.fillna(0))[:, 1]
    rows = []
    for q in th:
        for tp, sl in ((1, 2), (2, 4)):
            for long_only in (False, True):
                a = (qL >= th[q][0]) & ok
                b = (qS >= th[q][1]) & ok & (not long_only)
                t = sim(c, a, b, tp, sl)
                n1, r1, w1 = summ(t, T("2022-01-01"), T("2024-05-01"))
                n2, r2, w2 = summ(t, T("2024-05-01"), T("2027-01-01"))
                rows.append(dict(q=q, tp=tp, sl=sl, side="롱만" if long_only else "양방향",
                                 n1=n1, OOS1=r1, win1=w1, n2=n2, OOS2=r2, win2=w2, ok=r1 > 0 and r2 > 0))
    df = pd.DataFrame(rows)
    print(df.round(3).to_string())
    print("② 통과", int(df.ok.sum()), "/ 12 →", "봇 이식 후보" if df.ok.sum() >= 7 else "폐기")
