"""aoa 패턴을 OKX BTC 2022-01~2025-12 차트에 적용했을 때의 '정확도' (그의 거래 기록은 2021 에서 끝나 일치율은 못 잰다).

① 규칙 방향 적중률: 1h 이평 괴리 ≥ D% 인 5분봉에서 역방향 예측 → 다음 봉 시가 대비 h 후 종가가 예측 방향이면 적중.
② 학습 모델 방향 적중률: 그의 2018~21 에피소드로 학습한 M1(단기 가격 8개 로지스틱)을
   그가 주로 진입하던 급변동 봉(rv_ratio>1.5)에 적용 → 같은 방식으로 적중 판정. 확신 구간(p≥0.7 / ≤0.3) 따로.
③ 복제 전략(clone.run) 승률·수익률 — 연도별, 비용 편도 0.07% / 0%.
적중률 기준선 = 같은 구간에서 '항상 롱' 이었을 때의 적중률 (BTC 드리프트 때문에 50% 가 아니다).
"""
import pathlib
import sys

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import features as F  # noqa: E402
from clone import okx_5m, prep, run  # noqa: E402
from compare import load  # noqa: E402

BASE = ["pos_15m", "pos_1h", "pos_4h", "ema_1h", "rsi_5m", "ret_5m", "ret_15m", "ret_1h"]
YEARS = [2022, 2023, 2024, 2025]
H = {"1h": 12, "4h": 48}


def fwd_sign(c, h):
    return np.sign(c.close.shift(-h) / c.open.shift(-1) - 1)   # 신호 봉 다음 봉 시가 진입 → h 봉 뒤 종가


def acc_table(name, pred, c, f):
    rows = []
    for k, h in H.items():
        up = fwd_sign(c, h)
        ok = (pred == up)[pred != 0]
        base = (up == 1)[pred != 0]
        yr = ok.index.year
        rows.append({"규칙": name, "보유": k, **{y: f"{ok[yr == y].mean():.1%}/{base[yr == y].mean():.1%}" for y in YEARS},
                     "전체": f"{ok.mean():.1%}", "신호봉수": int(ok.size)})
    return rows


if __name__ == "__main__":
    c = okx_5m().loc["2021-11-01":"2025-12-31"]
    f = F.build(c)
    pc = [x for x in f.columns if x.startswith("pos_")]
    f[pc] = f[pc].fillna(0.5)
    c, f = c.loc["2022-01-01":], f.loc["2022-01-01":]
    rows = []
    for D in (0.25, 0.5, 1.0, 2.0):
        pred = pd.Series(np.where(f.ema_1h <= -D, 1, np.where(f.ema_1h >= D, -1, 0)), index=f.index)
        rows += acc_table(f"① 1h 이평 괴리 ≥{D}% 역방향", pred, c, f)
    x = load()
    m = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000)).fit(x[BASE], x.long)
    p = pd.Series(m.predict_proba(f[BASE].fillna(0))[:, 1], index=f.index)
    spike = f.rv_ratio > 1.5
    rows += acc_table("② 학습모델 · 급변동 봉 전체", pd.Series(np.where(spike, np.where(p >= 0.5, 1, -1), 0), index=f.index), c, f)
    rows += acc_table("② 학습모델 · 급변동+확신(p≥.7/≤.3)",
                      pd.Series(np.where(spike & (p >= 0.7), 1, np.where(spike & (p <= 0.3), -1, 0)), index=f.index), c, f)
    pd.set_option("display.width", 250)
    print("방향 적중률 (각 칸: 적중률 / 같은 봉에서 '항상 롱' 기준선)")
    print(pd.DataFrame(rows).to_string(index=False))

    P = prep(okx_5m().loc["2021-11-01":"2025-12-31"])
    print("\n③ 복제 전략 (연도별 승률 · 수익률)")
    for name, k in [("승률형 D=1 a 물타기 양방향", dict(D=1.0, spike=False, exit_="a", add=True, long_only=False)),
                    ("D=2 b 양방향", dict(D=2.0, spike=False, exit_="b", add=False, long_only=False)),
                    ("D=2 b 롱만", dict(D=2.0, spike=False, exit_="b", add=False, long_only=True))]:
        for cost in (0.0007, 0.0):
            tr = run(P, **k, cost=cost)
            line = f"  {name:24s} 비용 {cost * 100:.2f}% |"
            allr = []
            for y in YEARS:
                r = np.array([v for i, s, v in tr if P["idx"][i].year == y]) / 100
                allr += list(r)
                line += f" {y} 승률 {(r > 0).mean():.0%} {(np.prod(1 + r) - 1) * 100:+.0f}% ({len(r)}건) |"
            r = np.array(allr)
            line += f" 4년 승률 {(r > 0).mean():.0%} 누적 {(np.prod(1 + r) - 1) * 100:+.0f}%"
            print(line)
