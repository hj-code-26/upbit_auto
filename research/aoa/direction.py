"""진입 방향(롱/숏) 예측 규칙·모델 — 연도 하나를 빼고 학습, 그 연도로 시험(leave-one-year-out).

폐기 기준 (사용자 지정 + 엄격화): 어느 한 해라도 정확도 ≤ 40% 이거나, 시험 연도 평균이 '다수 클래스' 기준선을
넘지 못하면 그 규칙/모델은 폐기(DISCARD). 기준선 = 그 해의 롱/숏 중 많은 쪽 비율.
"""
import pathlib
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeClassifier, export_text

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from compare import FEATS, load  # noqa: E402

YEARS = [2018, 2019, 2020, 2021]


def loyo(x, make, cols):
    out = {}
    for y in YEARS:
        tr, te = x[x.y != y], x[x.y == y]
        m = make().fit(tr[cols], tr.long)
        out[y] = (m.predict(te[cols]) == te.long).mean()
    return out


def rule(pred):
    """고정 규칙(학습 없음): pred(df) → 0/1."""
    return lambda x: {y: (pred(g) == g.long).mean() for y, g in x.groupby("y")}


def verdict(name, acc, base):
    lift = np.mean([acc[y] - base[y] for y in YEARS])
    bad = min(acc.values()) <= 0.40 or lift <= 0
    print(f"{'DISCARD' if bad else 'KEEP   '} {name:42s} " + " ".join(f"{acc[y]:.3f}" for y in YEARS)
          + f" | 평균 {np.mean(list(acc.values())):.3f} 기준선대비 {lift:+.3f}")
    return not bad


if __name__ == "__main__":
    x = load()
    base = {y: max(g.long.mean(), 1 - g.long.mean()) for y, g in x.groupby("y")}
    print("기준선(다수 클래스)", {y: round(b, 3) for y, b in base.items()})
    print(f"{'':50s}" + " ".join(str(y) for y in YEARS))
    # 1) 고정 규칙
    rules = {
        "R1 pos_1h<0.5 → 롱": lambda g: (g.pos_1h < 0.5).astype(int),
        "R2 ema_1h<0 → 롱": lambda g: (g.ema_1h < 0).astype(int),
        "R3 rsi_5m<50 → 롱": lambda g: (g.rsi_5m < 50).astype(int),
        "R4 pos_1h+pos_15m < 1 → 롱": lambda g: (g.pos_1h + g.pos_15m < 1).astype(int),
        "R5 R1 다수결(pos_15m,1h,4h)": lambda g: ((g.pos_15m < .5).astype(int) + (g.pos_1h < .5) + (g.pos_4h < .5) >= 2).astype(int),
        "R6 추세추종 ret_1d>0 → 롱 (대조군)": lambda g: (g.ret_1d > 0).astype(int),
        "R7 추세추종 ema_7d>0 → 롱 (대조군)": lambda g: (g.ema_7d > 0).astype(int),
    }
    for n, p in rules.items():
        verdict(n, rule(p)(x), base)
    # 2) 학습 모델 (leave-one-year-out)
    short = ["pos_15m", "pos_1h", "pos_4h", "ema_1h", "rsi_5m", "ret_5m", "ret_15m", "ret_1h"]
    models = {
        "M1 로지스틱(단기 8개)": (lambda: make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000)), short),
        "M2 로지스틱(전체)": (lambda: make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000)), FEATS),
        "M3 트리 깊이3(전체)": (lambda: DecisionTreeClassifier(max_depth=3, min_samples_leaf=50), FEATS),
        "M4 부스팅(전체)": (lambda: HistGradientBoostingClassifier(max_depth=3, max_iter=200, learning_rate=0.05), FEATS),
        "M5 부스팅(전체+시간대)": (lambda: HistGradientBoostingClassifier(max_depth=3, max_iter=200, learning_rate=0.05),
                              FEATS + ["hour", "dow"]),
    }
    for n, (mk, cols) in models.items():
        verdict(n, loyo(x, mk, cols), base)
    t = DecisionTreeClassifier(max_depth=3, min_samples_leaf=50).fit(x[FEATS], x.long)
    print(export_text(t, feature_names=FEATS, show_weights=True))
