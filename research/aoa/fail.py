"""실패 사례 탐색 — '급락 롱이 더 빠진 경우' 와 '급등 숏이 더 오른 경우' 의 공통점 (2026-09-28).

이 스크립트는 '탐색' 이다. 규칙을 채택하지 않는다. 여기서 나온 대응책은 별도 스크립트(fail_rules.py)에
결과 보기 전 사전등록하고, 여기서 한 번도 안 본 데이터(OKX 2022-01~2026-09, 알트 6종)로만 판정한다.

데이터: BitMEX XBTUSD 5분봉 2018-03~2021-12 (IS) 만.
사건: 5분 종가가 1h EMA(12봉) 대비 −2% 이하 → 롱 / +2% 이상 → 숏. 다음 봉 시가 진입, 익절 4% / 손절 6% / 3일,
      롱·숏 따로 겹치지 않게(청산 뒤 다음 신호). 일봉 필터 없음(표본 확보) — 필터 켠 부분집합은 따로 보여준다.
실패 = 손절(−6%) 또는 만기 손실. 특징은 전부 신호 봉 종가 시점에 알 수 있는 값.
방향 특징(ret·ema·pos·rsi·일봉선 괴리)은 거래 방향을 곱해 '거래 방향 쪽이면 +' 로 바꿔서 롱·숏을 한 표에서 비교한다.
  예) 롱에서 ret_3d = −8% 이면 s_ret_3d = −8 → '신호 방향(아래)으로 이미 3일간 8% 달려온' 상태가 아니라,
      롱 방향 기준 −8 = 거래 반대쪽으로 8% 움직여 온 상태. 숏에서 ret_3d = +8% 도 s_ret_3d = −8 로 같은 값이 된다.
출력: 특징별 AUC(실패를 가려내는 힘, 0.5 = 무관), 연도별 방향 일관성, 실패 거래의 경로(반등 없이 직행했나).
"""
import pathlib
import sys

import numpy as np
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import features as F  # noqa: E402

COST, D, TP, SL, MAXB = 0.07, 2.0, 4.0, 6.0, 864
DIRECTIONAL = ["ret_5m", "ret_15m", "ret_1h", "ret_4h", "ret_12h", "ret_1d", "ret_3d", "ret_7d",
               "pos_1h", "pos_4h", "pos_1d", "pos_3d", "pos_7d", "ema_4h", "ema_1d", "ema_7d",
               "rsi_5m", "rsi_1h", "rsi_4h", "d50", "d200", "slope50", "ret_30d", "dev"]
PLAIN = ["rv_1h", "rv_1d", "rv_ratio", "vol_z_1h", "sig_24h", "hour"]


def daily_feats(c, bs):
    """전날 확정 일봉 기준: 50·200일선 괴리 %, 50일선 10일 기울기 %, 30일 수익률 %. 200일선 예열은 비트스탬프."""
    own = c.close.resample("1D").last()
    d = pd.concat([bs[bs.index < own.index[0]], own])
    s50, s200 = d.rolling(50).mean(), d.rolling(200).mean()
    t = pd.DataFrame({"d50": (d / s50 - 1) * 100, "d200": (d / s200 - 1) * 100,
                      "slope50": (s50 / s50.shift(10) - 1) * 100, "ret_30d": (d / d.shift(30) - 1) * 100})
    prev = c.index.normalize() - pd.Timedelta("1D")
    return t.reindex(prev).set_index(c.index)


def feats(c, bs):
    f = F.build(c)
    f = f.join(daily_feats(c, bs))
    f["dev"] = (c.close / c.close.ewm(span=12, adjust=False).mean() - 1) * 100
    for k in ["pos_1h", "pos_4h", "pos_1d", "pos_3d", "pos_7d"]:
        f[k] = f[k] - 0.5
    for k in ["rsi_5m", "rsi_1h", "rsi_4h"]:
        f[k] = f[k] - 50
    return f


def trades(c, f, side, lo, hi):
    """side=+1 롱(dev ≤ −D) / −1 숏(dev ≥ +D). → 거래표 (특징 + 결과 + 경로)."""
    o, h, l, dev, idx = c.open.values, c.high.values, c.low.values, f.dev.values, c.index
    trig = (dev <= -D) if side == 1 else (dev >= D)
    sig24 = pd.Series(trig.astype(float), index=idx).rolling(288).sum().shift(1).values   # 직전 24h 신호 봉 수
    i, i1, rows = max(idx.searchsorted(lo), 300), idx.searchsorted(hi), []
    while i < i1 - 1:
        if not trig[i]:
            i += 1; continue
        k = i + 1; e = o[k]
        up, dn = (e * (1 + TP / 100), e * (1 - SL / 100)) if side == 1 else (e * (1 - TP / 100), e * (1 + SL / 100))
        end, j, why, px = min(k + MAXB, len(o) - 1), k, None, None
        while j < end:
            if (l[j] <= dn) if side == 1 else (h[j] >= dn):
                why, px = "손절", (min(dn, o[j]) if side == 1 else max(dn, o[j])); break
            if (h[j] >= up) if side == 1 else (l[j] <= up):
                why, px = "익절", up; break
            j += 1
        if why is None:
            j, why, px = end, "만기", o[end]
        seg_h, seg_l = h[k:j + 1], l[k:j + 1]
        fav = (seg_h.max() / e - 1) * 100 if side == 1 else (1 - seg_l.min() / e) * 100      # 최대 유리 이동
        adv = (1 - seg_l.min() / e) * 100 if side == 1 else (seg_h.max() / e - 1) * 100      # 최대 불리 이동
        r = side * (px / e - 1) * 100 - 2 * COST
        row = {"t": idx[k], "side": side, "why": why, "ret": r, "bad": r < 0, "bars": j - k + 1, "mfe": fav, "mae": adv,
               "sig_24h": sig24[i]}
        for col in DIRECTIONAL:
            row["s_" + col] = side * f[col].values[i]
        for col in PLAIN:
            if col != "sig_24h":
                row[col] = f[col].values[i]
        rows.append(row)
        i = j + 1
    return pd.DataFrame(rows)


def auc(x, y):
    """y(실패=1) 를 x 가 클수록 실패로 가려내는 정도. 0.5 = 무관, >0.5 = 값이 클수록 실패."""
    m = ~np.isnan(x)
    x, y = x[m], y[m]
    n1, n0 = y.sum(), (~y).sum()
    if n1 == 0 or n0 == 0:
        return np.nan
    r = pd.Series(x).rank().values
    return (r[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)


def table(t, cols):
    y = t.bad.values.astype(bool)
    yr = t.t.dt.year.values
    out = []
    for col in cols:
        x = t[col].values.astype(float)
        a = auc(x, y)
        per = [auc(x[yr == Y], y[yr == Y]) for Y in sorted(set(yr))]
        same = sum((p - 0.5) * (a - 0.5) > 0 for p in per if not np.isnan(p))
        q = pd.qcut(pd.Series(x).rank(method="first"), 3, labels=["하", "중", "상"])
        ev = t.groupby(q.values, observed=True).ret.mean()
        out.append(dict(특징=col, AUC=a, 연도일관=f"{same}/{len(per)}", 연도AUC=" ".join(f"{p:.2f}" for p in per),
                        하위3분=ev.get("하"), 중위=ev.get("중"), 상위3분=ev.get("상")))
    return pd.DataFrame(out).assign(강도=lambda d: (d.AUC - 0.5).abs()).sort_values("강도", ascending=False).drop(columns="강도")


def path_summary(t, name):
    b, g = t[t.bad], t[~t.bad]
    print(f"\n[{name}] {len(t)}건 · 실패 {len(b)} ({len(b) / len(t):.0%}) · 거래당 {t.ret.mean():+.2f}%")
    if len(b):
        print(f"  실패: 손절까지 중앙 {b.bars.median() / 12:.1f}h · 그 전 최대 반등 중앙 {b.mfe.median():.2f}% "
              f"(+1% 이상 반등 {np.mean(b.mfe >= 1):.0%}, +2% 이상 {np.mean(b.mfe >= 2):.0%})")
    if len(g):
        print(f"  성공: 익절까지 중앙 {g.bars.median() / 12:.1f}h · 그 전 최대 역행 중앙 {g.mae.median():.2f}% "
              f"(−3% 이상 역행 {np.mean(g.mae >= 3):.0%}, −4% 이상 {np.mean(g.mae >= 4):.0%})")
    print(f"  실패 중 1h 안에 손절: {np.mean(b.bars <= 12):.0%}") if len(b) else None


if __name__ == "__main__":
    T = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731
    bs = pd.read_pickle(F.ROOT / "data_cache" / "bitstamp_1d_2016.pkl").close
    c = F.candles().loc["2018-01-01":"2022-01-10"]
    f = feats(c, bs)
    L = trades(c, f, 1, T("2018-03-05"), T("2022-01-01"))
    S = trades(c, f, -1, T("2018-03-05"), T("2022-01-01"))
    bull = (L.s_d50 > 0) & (L.s_d200 > 0)
    pd.set_option("display.width", 250); pd.set_option("display.max_rows", 200)
    path_summary(L, "롱 · 필터 없음"); path_summary(L[bull], "롱 · 50·200일선 위 (지금 봇)"); path_summary(S, "숏 · 필터 없음")
    cols = ["s_" + c_ for c_ in DIRECTIONAL] + PLAIN
    for name, t in [("롱 실패", L), ("숏 실패", S), ("롱+숏 합쳐서", pd.concat([L, S], ignore_index=True))]:
        print(f"\n=== {name}: AUC>0.5 = 값이 클수록 실패 (s_ 접두 = 거래 방향 기준) · 3분위별 거래당 수익 % ===")
        print(table(t, cols).round(3).head(18).to_string(index=False))
    print("\n=== 롱 · 50·200일선 위 부분집합 (지금 봇, 표본 작음) ===")
    print(table(L[bull].reset_index(drop=True), cols).round(3).head(12).to_string(index=False))
    L.to_pickle(F.ROOT / "data_cache" / "fail_long_is.pkl"); S.to_pickle(F.ROOT / "data_cache" / "fail_short_is.pkl")
