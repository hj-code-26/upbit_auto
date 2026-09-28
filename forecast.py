"""BTC 방향·변동폭 예측기 — 매수·매도 세팅은 사용자가 한다 (2026-09-29). 주문 코드 없음.

출력 (1시간 · 4시간 · 24시간 뒤): 상승 확률 P(상승) · 수익률 분포 하위 10% / 중앙 / 상위 10% (그 가격)
학습: 바이낸스 BTCUSDT 5분봉 2020-03~ (일봉 이평 예열은 비트스탬프 일봉). 매시 정각 표본.
특징 16개: 수익률 1h·4h·24h·7d · 1h EMA 괴리 · 24h 박스 위치·폭 · 실현변동성 1h·24h·7d·비율 · RSI 5m·느린 RSI
           · 거래량 급증 · 50·200일선 거리(전날 기준). 모델: sklearn HistGradientBoosting (방향 분류 + 분위 회귀 3개).
신뢰도: python forecast.py eval — 연도별 워크포워드(그해 이전만 학습, 예측 기간만큼 간격을 두고) 2022~2026.
        결과 → forecast_eval.txt. 실시간 출력 끝에 이 파일의 요약을 같이 보여준다.
사용: python forecast.py        (OKX 최근 8일 5분봉 + 일봉 260개로 지금 예측)
"""
import pathlib
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier as HGC
from sklearn.ensemble import HistGradientBoostingRegressor as HGR
from sklearn.metrics import roc_auc_score

ROOT = pathlib.Path(__file__).resolve().parent
DC = ROOT / "data_cache"
HZ = {"1시간": 12, "4시간": 48, "24시간": 288}
QS = (0.1, 0.5, 0.9)
COST = 0.0014                                   # 왕복 (편도 0.07%)
KW = dict(max_iter=200, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=200, random_state=0)


def features(c, d1):
    cl = c.close; lr = np.log(cl).diff()
    f = pd.DataFrame(index=c.index)
    for k, n in (("r1h", 12), ("r4h", 48), ("r24h", 288), ("r7d", 2016)):
        f[k] = np.log(cl / cl.shift(n))
    f["dev1h"] = cl / cl.ewm(span=12, adjust=False).mean() - 1
    hi, lo = c.high.rolling(288).max(), c.low.rolling(288).min()
    f["pos24"], f["w24"] = (cl - lo) / (hi - lo), (hi - lo) / lo
    f["rv1h"], f["rv24h"], f["rv7d"] = lr.rolling(12).std(), lr.rolling(288).std(), lr.rolling(2016).std()
    f["rvr"] = f.rv1h / f.rv24h
    d = cl.diff()
    for k, a in (("rsi5m", 1 / 14), ("rsi_slow", 1 / 168)):
        up = d.clip(lower=0).ewm(alpha=a, adjust=False).mean(); dn = (-d).clip(lower=0).ewm(alpha=a, adjust=False).mean()
        f[k] = 100 - 100 / (1 + up / dn)
    f["vz"] = np.log1p(c.volume.rolling(12).sum()) - np.log1p(c.volume.rolling(288).sum() / 24)
    prev = c.index.normalize() - pd.Timedelta("1D")
    for k, w in (("d50", 50), ("d200", 200)):
        f[k] = cl.values / d1.rolling(w).mean().reindex(prev).values - 1
    return f.replace([np.inf, -np.inf], np.nan)


def heat(c):
    """과열 지표 (바이낸스 캐시, 2019-09~): 펀딩 수준·3일·30일·급등 z · 현물 체결 매수 비율 1h·24h
    · 선물 4h 프리미엄(선물 종가/현물 종가−1)·30일 z · 선물 체결 매수 비율 4h. 전부 그 시각에 확정된 값만 (봉 끝 시각으로 옮겨 ffill)."""
    idx = c.index
    fu = pd.read_pickle(DC / "binance_funding.pkl").sort_index()
    g = pd.DataFrame({"f_now": fu, "f_3d": fu.rolling(9).mean(), "f_30d": fu.rolling(90).mean()})
    g["f_z"] = (g.f_3d - g.f_30d) / fu.rolling(90).std()
    out = g.reindex(idx, method="ffill")
    for k, n in (("tbr1h", 12), ("tbr24h", 288)):
        out[k] = c.tbv.rolling(n).sum() / c.volume.rolling(n).sum() - 0.5
    fut = pd.read_pickle(DC / "binance_taker_4h.pkl").sort_index()
    end = fut.index + pd.Timedelta("4h")
    b = pd.DataFrame({"basis": fut.close.values / c.close.reindex(end - pd.Timedelta("5min")).values - 1,
                      "ftbr4h": fut.tbuy.values / fut.volume.values - 0.5}, index=end)
    b["basis_z"] = (b.basis - b.basis.rolling(180).mean()) / b.basis.rolling(180).std()
    return out.join(b.reindex(idx, method="ffill")).replace([np.inf, -np.inf], np.nan)


def history():
    c = pd.read_pickle(DC / "binance_btcusdt_5m_2020.pkl").asfreq("5min").ffill()
    own = c.close.resample("1D").last()
    bs = pd.read_pickle(DC / "bitstamp_1d_2016.pkl").close
    return c, pd.concat([bs[bs.index < own.index[0]], own])


def dataset(c, d1, n, extra=None):
    f = features(c, d1) if extra is None else features(c, d1).join(extra)
    y = np.log(c.close.shift(-n) / c.close)
    s = (f.index.minute == 55) & (f.index >= pd.Timestamp("2020-03-01", tz="UTC"))
    X, y = f[s], y[s]
    ok = X.notna().all(axis=1) & y.notna()
    return X[ok], y[ok], f


def fit(X, y):
    clf = HGC(**KW).fit(X, (y > 0).astype(int))
    qs = {q: HGR(loss="quantile", quantile=q, **KW).fit(X, y) for q in QS}
    return clf, qs


def walk(X, y, f, n):
    """연도별 워크포워드 예측 → DataFrame(y · p · q0.1/0.5/0.9 · naive)."""
    out = []
    for yr in range(2022, 2027):
        t0 = pd.Timestamp(f"{yr}-01-01", tz="UTC")
        tr = X.index < t0 - pd.Timedelta(minutes=5 * n)
        te = X.index.year == yr
        if te.sum() == 0:
            continue
        clf, qs = fit(X[tr], y[tr])
        q = {f"q{k}": m.predict(X[te]) for k, m in qs.items()}
        out.append(pd.DataFrame({"y": y[te].values, "p": clf.predict_proba(X[te])[:, 1], **q,
                                 "naive": f.rv24h.reindex(X.index[te]).values * np.sqrt(n) * 1.2816}, index=X.index[te]))
    return pd.concat(out)


def compare_heat():
    """기본 16개 vs +과열 11개 — 같은 표본(과열 지표가 있는 시각)·같은 분할. 결과 → forecast_heat.txt"""
    c, d1 = history()
    ext = heat(c)
    lines = []
    P = lambda *a: (print(*a, flush=True), lines.append(" ".join(map(str, a))))  # noqa: E731
    P("기본 vs +과열 (펀딩·프리미엄·체결 매수 비율) — 워크포워드 2022~2026, 같은 표본")
    for name, n in HZ.items():
        X, y, f = dataset(c, d1, n, ext)
        P(f"\n[{name} 뒤] 표본 {len(X):,}")
        for lab, cols in (("기본", [k for k in X.columns if k not in ext.columns]), ("+과열", list(X.columns))):
            o = walk(X[cols], y, f, n)
            up = o.y > 0
            conf = (o.p - 0.5).abs() >= 0.1
            yr = " ".join(f"{k % 100:02d}:{roc_auc_score(g.y > 0, g.p):.3f}" for k, g in o.groupby(o.index.year))
            nb = o.iloc[:: max(n // 12, 1)]; m = (nb.p >= 0.6) | (nb.p <= 0.4)
            r = np.where(nb.p[m] >= 0.6, nb.y[m], -nb.y[m]) - COST
            P(f"  {lab:4s} 적중 {((o.p > 0.5) == up).mean():.1%} · AUC {roc_auc_score(up, o.p):.3f} (연도별 {yr})"
              f" · 확신 {conf.mean():.0%} 적중 {((o.p[conf] > 0.5) == up[conf]).mean():.1%}"
              f" · 구간 적중 {((o.y >= o['q0.1']) & (o.y <= o['q0.9'])).mean():.1%} 폭 {(o['q0.9'] - o['q0.1']).median() * 100:.2f}%"
              f" · 참고 매매 {m.sum()}회 {np.mean(r) * 100 if m.sum() else float('nan'):+.3f}%")
    (ROOT / "forecast_heat.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def evaluate():
    c, d1 = history()
    lines = []
    P = lambda *a: (print(*a, flush=True), lines.append(" ".join(map(str, a))))  # noqa: E731
    P("워크포워드 2022~2026 (그해 1월 1일 이전만 학습, 예측 기간만큼 간격). 기준선 = 그 기간 실제 상승 비율(항상 상승에 걸 때 적중률)")
    for name, n in HZ.items():
        X, y, f = dataset(c, d1, n)
        o = walk(X, y, f, n)
        up = o.y > 0
        acc, base = ((o.p > 0.5) == up).mean(), max(up.mean(), 1 - up.mean())
        conf = (o.p - 0.5).abs() >= 0.1
        cov = ((o.y >= o["q0.1"]) & (o.y <= o["q0.9"])).mean()
        ncov = (o.y.abs() <= o.naive).mean()
        P(f"\n[{name} 뒤] 표본 {len(o):,}개(시간당 1개, {'겹침 있음 — 실효 표본은 훨씬 적다' if n > 12 else '겹침 없음'})")
        P(f"  방향 적중 {acc:.1%} (기준선 {base:.1%}) · AUC {roc_auc_score(up, o.p):.3f} · 확신(P≤40% 또는 ≥60%) {conf.mean():.0%} 구간 적중 {((o.p[conf] > 0.5) == up[conf]).mean():.1%}")
        P("  연도별 적중/기준선: " + " · ".join(f"{k % 100:02d}:{((g.p > 0.5) == (g.y > 0)).mean():.0%}/{max((g.y > 0).mean(), (g.y <= 0).mean()):.0%}"
                                          for k, g in o.groupby(o.index.year)))
        P(f"  변동폭: 하위10~상위10% 구간에 실제가 들어온 비율 {cov:.1%} (목표 80%) · 구간 폭 중앙 {(o['q0.9'] - o['q0.1']).median() * 100:.2f}%"
          f" | 단순 변동성 구간(24h 변동성 기준) {ncov:.1%} · 폭 {(2 * o.naive).median() * 100:.2f}%")
        P(f"  중앙 예측 부호 적중 {((o['q0.5'] > 0) == up).mean():.1%} · 예측 중앙값 크기 중앙 {o['q0.5'].abs().median() * 100:.3f}% vs 실제 |수익률| 중앙 {o.y.abs().median() * 100:.3f}%")
        nb = o.iloc[:: max(n // 12, 1)]                            # 겹치지 않게 n 시간마다 한 번
        for lab, m in (("P≥60% 롱·≤40% 숏", (nb.p >= 0.6) | (nb.p <= 0.4)), ("P≥60% 롱만", nb.p >= 0.6)):
            r = np.where(nb.p[m] >= 0.6, nb.y[m], -nb.y[m]) - COST
            P(f"  참고 매매({lab}, {name} 보유, 왕복 0.14%): {m.sum()}회 거래당 {np.mean(r) * 100 if m.sum() else float('nan'):+.3f}% 승률 {np.mean(r > 0) if m.sum() else float('nan'):.0%}")
    (ROOT / "forecast_eval.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def live():
    import okx
    ex = okx.public()
    now = pd.Timestamp.now(tz="UTC")
    c = okx.bars_since(ex, now - pd.Timedelta("8D"), "5m")
    c = c[c.index + pd.Timedelta("5min") <= now]                  # 진행 중인 봉 제외
    dd = okx.candles(ex, 260, "1d").close.iloc[:-1]
    dd.index = dd.index.tz_convert("UTC")
    hc, hd = history()
    px = c.close.iloc[-1]
    print(f"OKX BTC-USDT-SWAP {px:,.1f}  (마지막 완성 5분봉 {c.index[-1] + pd.Timedelta('5min'):%Y-%m-%d %H:%M} UTC)")
    for name, n in HZ.items():
        X, y, _ = dataset(hc, hd, n)
        clf, qs = fit(X, y)
        x = features(c, dd).iloc[[-1]]
        if x.isna().any(axis=None):
            print(f"  {name}: 특징 결측 {list(x.columns[x.isna().iloc[0]])} — 예측 불가"); continue
        p = clf.predict_proba(x)[0, 1]
        q = {k: float(m.predict(x)[0]) for k, m in qs.items()}
        lv = " / ".join(f"{np.expm1(q[k]) * 100:+.2f}% ({px * np.exp(q[k]):,.0f})" for k in QS)
        print(f"  {name:4s} 뒤: P(상승) {p:.0%} · 하위10% / 중앙 / 상위10%: {lv}")
    ev = ROOT / "forecast_eval.txt"
    print("\n신뢰도 (" + ("forecast_eval.txt" if ev.exists() else "없음 — python forecast.py eval 먼저") + ")")
    if ev.exists():
        for ln in ev.read_text(encoding="utf-8").splitlines():
            if ln.startswith("[") or "방향 적중" in ln or "변동폭:" in ln:
                print(ln)


if __name__ == "__main__":
    {"eval": evaluate, "heat": compare_heat}.get(sys.argv[1] if len(sys.argv) > 1 else "", live)()
