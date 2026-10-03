"""워뇨띠 거래를 예측기(forecast.py) 눈으로 보기 (2026-09-29, 사후 분석 — 판정 아님).

모델: 바이낸스 BTC 2022-01~2026-09 로만 학습 (그의 거래 기간 2018~2021 은 학습에 안 들어간다 → 그 기간 예측은 표본 밖).
  A = 기본 16개 · BitMEX 5분봉으로 특징 계산 → 그의 에피소드 3,063개 전부 (2018-03~2021-12)
  B = 12묶음 전부(과열·시간·ETH 포함) · 바이낸스 5분봉으로 특징 계산 → 2020-03 이후 에피소드만
진입 시점 = 그의 진입 직전에 완성된 5분봉. ret = 방향 반영 가격 변화 %(수수료 전), 에피소드 = |포지션/자기자본| ≥ 0.1 인 방향 유지 구간.
질문: ① 그의 방향이 모델 방향과 맞나 ② 모델과 같은 방향일 때 그의 성적이 더 좋은가 ③ 그가 들어간 순간 모델이 본 변동폭은 평소 대비 얼마인가
      ④ 모델과 반대로 간 거래를 빼면 그의 성적이 나아지나
한계: 특징 일부(RSI·1h 괴리)는 원래 그의 거래 분석에서 나온 것이라 '독립적인 눈' 은 아니다.
"""
import pathlib
import sys

import numpy as np
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research")); sys.path.insert(0, str(HERE))
import forecast as FC  # noqa: E402
import features as F  # noqa: E402
from forecast_ablation import extra  # noqa: E402

T = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731


def models(X, y):
    tr = X.index >= T("2022-01-01")
    clf = FC.HGC(**FC.KW).fit(X[tr], (y[tr] > 0).astype(int))
    q = {a: FC.HGR(loss="quantile", quantile=a, **FC.KW).fit(X[tr], y[tr]) for a in (0.1, 0.9)}
    return clf, q


def at_entry(f, ep):
    k = f.index.searchsorted(ep.start.floor("min") - pd.Timedelta("5min"), side="right") - 1   # 분 단위로 내림(인덱스 단위 맞춤)
    return f.iloc[k]


def report(P, lab, ep, fe, hourly):
    """fe: 에피소드별 예측 (p_h, w_h) · hourly: 같은 기간 매시 예측 (비교 기준)."""
    P(f"\n[{lab}] 에피소드 {len(ep):,}개 ({ep.start.min():%Y-%m} ~ {ep.start.max():%Y-%m})")
    for h in FC.HZ:
        p, w = fe[f"p_{h}"], fe[f"w_{h}"]
        agree = np.where(ep.state == 1, p > 0.5, p < 0.5)
        strong_a = np.where(ep.state == 1, p >= 0.6, p <= 0.4)
        strong_d = np.where(ep.state == 1, p <= 0.4, p >= 0.6)
        r = ep.ret.values
        P(f"  {h:4s} ① 방향 일치 {agree.mean():.1%} (롱 {agree[ep.state == 1].mean():.1%} · 숏 {agree[ep.state == -1].mean():.1%})"
          f" | ② 일치 {r[agree].mean():+.3f}% 승률 {(r[agree] > 0).mean():.0%} · 반대 {r[~agree].mean():+.3f}% 승률 {(r[~agree] > 0).mean():.0%}"
          f" · 강한 일치({strong_a.sum()}) {r[strong_a].mean() if strong_a.sum() else np.nan:+.3f}% · 강한 반대({strong_d.sum()}) {r[strong_d].mean() if strong_d.sum() else np.nan:+.3f}%")
        P(f"       ③ 변동폭 예측(하위10~상위10% 폭) 진입 시 중앙 {np.median(w) * 100:.2f}% vs 평소 {hourly[f'w_{h}'].median() * 100:.2f}%"
          f" ({np.median(w) / hourly[f'w_{h}'].median():.2f}배) · 평소 대비 상위 20% 변동 순간에 들어간 비율 {(w >= hourly[f'w_{h}'].quantile(0.8)).mean():.0%}"
          f" | ④ 반대 거래 빼면 거래당 {r.mean():+.3f}% → {r[agree].mean():+.3f}%, 합계 {r.sum():+.0f}% → {r[agree].sum():+.0f}%")
    y = ep.start.dt.year
    P("  연도별 4h 일치율·일치/반대 거래당: " + " · ".join(
        f"{k}: {np.where(g.state == 1, fe.loc[g.index, 'p_4시간'] > 0.5, fe.loc[g.index, 'p_4시간'] < 0.5).mean():.0%} "
        f"{g.ret[np.where(g.state == 1, fe.loc[g.index, 'p_4시간'] > 0.5, fe.loc[g.index, 'p_4시간'] < 0.5)].mean():+.2f}/"
        f"{g.ret[~np.where(g.state == 1, fe.loc[g.index, 'p_4시간'] > 0.5, fe.loc[g.index, 'p_4시간'] < 0.5)].mean():+.2f}"
        for k, g in ep.groupby(y)))


if __name__ == "__main__":
    lines = []
    P = lambda *a: (print(*a, flush=True), lines.append(" ".join(map(str, a))))  # noqa: E731
    P(__doc__.split("\n")[0])
    ep = pd.read_parquet(FC.DC / "aoa_episodes.parquet")
    ep = ep[ep.state != 0].copy()
    ep["start"] = pd.to_datetime(ep.start).dt.tz_localize("UTC")
    ep = ep.reset_index(drop=True)
    c, d1 = FC.history()
    ext = extra(c)
    bs = pd.read_pickle(FC.DC / "bitstamp_1d_2016.pkl").close
    bmx = F.candles().loc["2017-12-01":"2022-01-10"]
    own = bmx.close.resample("1D").last()
    fb = FC.features(bmx, pd.concat([bs[bs.index < own.index[0]], own]))
    fn = FC.features(c, d1).join(ext)
    for lab, fr, cols_all, sel in (("A 기본 16개 · BitMEX 18-21", fb, False, ep.index),
                                   ("B 12묶음 전부 · 바이낸스 20-21", fn, True, ep.index[ep.start >= T("2020-03-15")])):
        e = ep.loc[sel]
        fe, hourly = pd.DataFrame(index=e.index), {}
        span = (fr.index >= e.start.min()) & (fr.index <= e.start.max()) & (fr.index.minute == 55)
        for h, n in FC.HZ.items():
            X, y, _ = FC.dataset(c, d1, n, ext)
            cols = list(X.columns) if cols_all else list(FC.features(c.iloc[:3000], d1).columns)
            clf, q = models(X[cols], y)
            xe = pd.DataFrame([at_entry(fr[cols], r) for r in e.itertuples()], index=e.index)
            ok = xe.notna().all(axis=1)
            fe.loc[ok, f"p_{h}"] = clf.predict_proba(xe[ok])[:, 1]
            fe.loc[ok, f"w_{h}"] = q[0.9].predict(xe[ok]) - q[0.1].predict(xe[ok])
            xh = fr.loc[span, cols].dropna()
            hourly[f"w_{h}"] = pd.Series(q[0.9].predict(xh) - q[0.1].predict(xh))
        keep = fe.notna().all(axis=1)
        report(P, lab, e[keep], fe[keep], hourly)
    (HERE / "aoa_forecast_result.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
