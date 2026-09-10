"""1분봉 보조지표로 '지금부터 H분 뒤 오를까' 를 학습한다 (연구 전용, 봇은 아직 쓰지 않는다).

목적은 하나 — 진입 타이밍. 4h 돌파 신호가 난 뒤 곧장 사는 대신, 분봉에서 '하한이 방어되는 자리' 를
알아볼 수 있으면 같은 신호를 더 좋은 가격에 잡는다. 그런 자리가 정말 있는지를 데이터로 묻는다.

방식: 2021-03~ 1분봉 전량(minute_data.py full) → 지표 → 6개월마다 재학습하는 워크포워드로
      검증 구간의 확률 p 를 채운다(미래 정보 차단). 평가는 AUC 와 p 십분위별 실제 상승률.
      p 가 쓸모 있으면 backtest_entry.py 의 'learned' 정책이 그 p 로 진입 시각을 고른다.

하한 방어 관련 지표를 일부러 넣었다: 60분 저가까지의 거리·나이·재터치 횟수, 아래꼬리 비율,
연속 음봉 수, VWAP 이격. '떨어지다 멈춘 자리' 가 실제로 반등을 예고하는지 이 지표들이 답한다.

사용: python minute_model.py            학습·평가 (전량 필요, 수 분)
      python minute_model.py quick      최근 1년만 (빠른 점검)
"""
import pathlib
import pickle
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

import minute_data as MD

ROOT = pathlib.Path(__file__).resolve().parent
MODEL_PATH = MD.CACHE / "minute_model.pkl"
P_PATH = MD.CACHE / "minute_p.pkl"     # 워크포워드 p (검증 구간만). backtest_entry.py 가 이걸 읽는다
H = 60                     # 예측 지평 (분). 4h 창 안에서 쓸 값이라 60분
RETRAIN_D = 182            # 6개월마다 재학습
WARMUP_D = 365             # 첫 학습 확보 구간
STRIDE = 5                 # 학습 표본은 5분마다 (인접 분은 거의 같은 정보라 버려도 된다)

FEATS = ["ret_1", "ret_5", "ret_15", "ret_60", "ret_240", "rsi", "bbp", "from_hi60", "from_lo60",
         "from_hi240", "from_lo240", "sma60_d", "sma240_d", "wick_lo", "vol_ratio", "vol_std",
         "down_streak", "vwap_d", "range_pct", "lo60_age", "lo60_touch", "bounce"]


def feats(d):
    """1분봉 → 지표 표. 모든 값은 '지금 봉까지' 만 쓴다 (미래 정보 없음).
    빠진 분은 빈 칸으로 채워 둔다 — 그래야 공백을 사이에 둔 두 시점이 240분 창 안에서 섞이지 않는다
    (빈 칸이 섞인 창은 NaN 이 되고 학습·검증에서 자동으로 빠진다)."""
    d = d.reindex(pd.date_range(d.index[0], d.index[-1], freq="min"))
    c, h, l, v = d.close, d.high, d.low, d.volume
    f = pd.DataFrame(index=d.index)
    for n in (1, 5, 15, 60, 240):
        f[f"ret_{n}"] = c.pct_change(n) * 100
    dif = c.diff()
    up, dn = dif.clip(lower=0), -dif.clip(upper=0)
    f["rsi"] = 100 - 100 / (1 + up.ewm(alpha=1 / 14, adjust=False).mean() / dn.ewm(alpha=1 / 14, adjust=False).mean())
    m, s = c.rolling(60).mean(), c.rolling(60).std(ddof=0)
    f["bbp"] = (c - (m - 2 * s)) / (4 * s)
    hi60, lo60 = h.rolling(60).max(), l.rolling(60).min()
    f["from_hi60"], f["from_lo60"] = c / hi60 * 100 - 100, c / lo60 * 100 - 100
    f["from_hi240"] = c / h.rolling(240).max() * 100 - 100
    f["from_lo240"] = c / l.rolling(240).min() * 100 - 100
    f["sma60_d"] = c / m * 100 - 100
    f["sma240_d"] = c / c.rolling(240).mean() * 100 - 100
    body = (c - d.open).abs().clip(lower=1e-9)
    f["wick_lo"] = ((d[["open", "close"]].min(axis=1) - l) / body).rolling(15).mean()   # 아래꼬리 = 매수 흡수
    f["vol_ratio"] = v.rolling(15).mean() / v.rolling(240).mean()
    f["vol_std"] = c.pct_change().rolling(60).std() * 100
    down = (dif < 0)
    f["down_streak"] = down.groupby((~down).cumsum()).cumsum()                          # 연속 음봉 수
    vwap = (c * v).rolling(240).sum() / v.rolling(240).sum()
    f["vwap_d"] = c / vwap * 100 - 100
    f["range_pct"] = (h - l) / c * 100
    f["lo60_age"] = 60 - l.rolling(60).apply(np.argmin, raw=True) - 1                   # 60분 저가가 몇 분 전인가
    f["lo60_touch"] = (l <= lo60 * 1.0005).rolling(60).sum()                            # 그 바닥을 몇 번 다시 찍었나
    f["bounce"] = c / lo60 * 100 - 100
    f["fwd"] = (c.shift(-H) / c - 1) * 100
    return f


def walkforward(f, seed=0):
    """6개월마다 재학습하며 검증 구간 p 를 채운다. 학습 표본은 라벨이 겹치지 않게 H 분 이전까지만 쓴다."""
    f = f.dropna(subset=FEATS).copy()
    f["p"] = np.nan
    days = f.index.normalize().unique()
    for k in range(WARMUP_D, len(days), RETRAIN_D):
        t0, t1 = days[k], days[min(k + RETRAIN_D, len(days) - 1)]
        tr = f[(f.index < t0 - pd.Timedelta(minutes=H)) & f.fwd.notna()].iloc[::STRIDE]
        if len(tr) < 20_000:
            continue
        m = HistGradientBoostingClassifier(max_depth=4, learning_rate=0.05, max_iter=300,
                                           min_samples_leaf=500, l2_regularization=1.0, random_state=seed)
        m.fit(tr[FEATS], (tr.fwd > 0).astype(int))
        te = (f.index >= t0) & (f.index < t1)
        if te.sum():
            f.loc[te, "p"] = m.predict_proba(f.loc[te, FEATS])[:, 1]
        print(f"  학습 ~{t0:%Y-%m-%d} 표본 {len(tr):>8,} → 검증 {te.sum():>8,}", file=sys.stderr)
    pickle.dump({"model": m, "feats": FEATS, "H": H, "trained_at": days[-1]}, MODEL_PATH.open("wb"))
    out = f[f.p.notna()]
    out.p.to_pickle(P_PATH)          # 각 분의 p 는 그 시점 이전 데이터로만 학습한 모델의 값이다 (미래 정보 없음)
    return out


def evaluate(f):
    a = f.dropna(subset=["p", "fwd"])
    print(f"\n검증 {a.index[0]:%Y-%m-%d} ~ {a.index[-1]:%Y-%m-%d}  {len(a):,}분  기본 상승률 {(a.fwd > 0).mean() * 100:.1f}%")
    print(f"AUC {roc_auc_score((a.fwd > 0).astype(int), a.p):.4f}   (0.5 = 무의미)\n")
    q = pd.qcut(a.p, 10, labels=False, duplicates="drop")
    print(f"{'p 십분위':10s} {'p 평균':>8s} {'상승률':>8s} {'평균 %':>8s} {'표본':>10s}")
    for i in sorted(q.unique()):
        g = a[q == i]
        print(f"{i + 1:^10d} {g.p.mean():8.3f} {(g.fwd > 0).mean() * 100:7.1f}% {g.fwd.mean():+8.3f} {len(g):10,}")
    lo, hi = a[q == q.min()], a[q == q.max()]
    print(f"\n최상위−최하위 십분위 {H}분 평균 수익률 차: {hi.fwd.mean() - lo.fwd.mean():+.3f}%p "
          f"(왕복 비용 0.14% 대비 {'유의미' if hi.fwd.mean() - lo.fwd.mean() > 0.14 else '작다'})")
    return a


if __name__ == "__main__":
    d = pd.read_pickle(MD.PKL)
    if "quick" in sys.argv:
        d = d[d.index >= d.index[-1] - pd.Timedelta(days=365)]
    print(f"1분봉 {len(d):,}개  {d.index[0]:%Y-%m-%d} ~ {d.index[-1]:%Y-%m-%d}", file=sys.stderr)
    evaluate(walkforward(feats(d)))
