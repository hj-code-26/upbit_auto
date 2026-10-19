"""chart_cnn 검산: 그림 없이 '종가 → 고가/저가(박스 끝) 거리' 숫자 한 개로 문턱만 정한 규칙의 시험 정확도.
CNN 이 이 규칙과 비슷하면, CNN 이 배운 것은 패턴이 아니라 '가까운 쪽이 먼저 닿는다' 는 기하. → chart_rule.csv"""
import pathlib, sys
HERE = pathlib.Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
import numpy as np, pandas as pd
import chart_cnn as cc

D = pd.concat([cc.samples(c)[1] for c in cc.COINS], ignore_index=True)
D["d_hl"], D["d_box"] = D.d_lo - D.d_hi, D.d_blo - D.d_bhi        # 클수록 종가가 위쪽 끝에 가까움 (방향은 학습에서 고름)
D["d_rng"] = D.f_p                                                 # 전일 폭 (좁을수록 다음 날 넓어짐 = 평균 회귀)
rule = {"box_hold": "f_w", "box_dir": "d_box", "hh": "d_hi", "ll": "d_lo", "hl_first": "d_hl", "expand": "d_rng", "up": "f_r1"}
tr = (D.t < "2024-01-01") & (D.coin != "ZEC"); te = D.t >= "2025-01-01"
out = []
for task, f in rule.items():
    ok = D[task].notna(); a, c = D[ok & tr], D[ok & te]
    best = max(((s, th) for s in (1, -1) for th in np.quantile(a[f], np.linspace(.02, .98, 49))),
               key=lambda st: ((st[0] * a[f] >= st[0] * st[1]) == a[task]).mean())   # 학습에서 문턱·방향
    acc = ((best[0] * c[f] >= best[0] * best[1]) == c[task]).mean()
    out.append(dict(task=task, feature=f, rule_test=round(acc, 4))); print(out[-1])
pd.DataFrame(out).to_csv(HERE / "chart_rule.csv", index=False)
