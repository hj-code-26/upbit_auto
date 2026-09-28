"""실패 탐색 2단계 — fail.py 가 가리킨 '조용한 장이 깨질 때' 를 IS(BitMEX 2018-03~2021-12) 안에서만 좁힌다 (2026-09-28).

fail.py 에서 롱·숏 합쳐 4년 모두 같은 방향이던 특징 3개: rv_1d 낮음 · rv_ratio 높음 · 직전 24h 신호 없음 → 실패↑.
여기서 볼 것 (전부 탐색, 채택 아님):
  A. 두 특징 교차표 — 실패율·거래당 수익이 한쪽 구석에 몰리나
  B. 척도 없는 판정값 z = |dev| ÷ (rv_1d/√24)  (급락폭이 평소 1시간 변동의 몇 배인가) — 알트에 옮길 수 있게
  C. 대응 후보를 IS 에서 재 본다: ① 건너뛰기 ② 반대로 타기(추세 추종) ③ 확인 후 진입(1h EMA 되찾기)
"""
import pathlib
import sys

import numpy as np
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import features as F  # noqa: E402
from fail import COST, MAXB, feats  # noqa: E402


def exit_at(o, h, l, k, side, tp, sl):
    e = o[k]
    up, dn = (e * (1 + tp / 100), e * (1 - sl / 100)) if side == 1 else (e * (1 - tp / 100), e * (1 + sl / 100))
    end = min(k + MAXB, len(o) - 1)
    for j in range(k, end):
        if (l[j] <= dn) if side == 1 else (h[j] >= dn):
            px = min(dn, o[j]) if side == 1 else max(dn, o[j]); return j, side * (px / e - 1) * 100 - 2 * COST
        if (h[j] >= up) if side == 1 else (l[j] <= up):
            return j, side * (up / e - 1) * 100 - 2 * COST
    return end, side * (o[end] / e - 1) * 100 - 2 * COST


def events(c, f, sig, lo, hi, D=2.0):
    """sig=+1 급락(dev ≤ −D) / −1 급등(dev ≥ +D) 신호 봉 목록 — 겹침 없이 3일 창마다 첫 신호가 아니라,
    fail.py 와 같게 '청산 뒤 다음 신호' 로 뽑는다(기준 전략 TP4/SL6 기준)."""
    o, h, l, dev, idx = c.open.values, c.high.values, c.low.values, f.dev.values, c.index
    trig = (dev <= -D) if sig == 1 else (dev >= D)
    sig24 = pd.Series(trig.astype(float), index=idx).rolling(288).sum().shift(1).values
    z = np.abs(dev) / (f.rv_1d.values / np.sqrt(24))
    i, i1, out = max(idx.searchsorted(lo), 300), idx.searchsorted(hi), []
    while i < i1 - 1:
        if not trig[i]:
            i += 1; continue
        j, r = exit_at(o, h, l, i + 1, sig, 4, 6)
        out.append(dict(i=i, t=idx[i], base=r, rv_1d=f.rv_1d.values[i], rv_ratio=f.rv_ratio.values[i], sig_24h=sig24[i], z=z[i]))
        i = j + 1
    return pd.DataFrame(out)


def responses(c, f, E, sig):
    """각 신호에서 대응별 결과 (%). flip = 반대 방향 TP4/SL6 · flip2 = 반대 방향 TP6/SL4 (추세 추종은 이익을 길게) ·
    confirm = 신호 뒤 12봉 안에 5분 종가가 1h EMA 를 되찾으면(롱: dev ≥ 0) 그다음 봉 진입, 못 되찾으면 거래 없음(0)."""
    o, h, l, dev = c.open.values, c.high.values, c.low.values, f.dev.values
    fl, fl2, cf = [], [], []
    for i in E.i:
        fl.append(exit_at(o, h, l, i + 1, -sig, 4, 6)[1])
        fl2.append(exit_at(o, h, l, i + 1, -sig, 6, 4)[1])
        w = np.where(sig * dev[i + 1:i + 13] >= 0)[0]
        cf.append(exit_at(o, h, l, i + 2 + w[0], sig, 4, 6)[1] if len(w) else np.nan)
    return E.assign(flip=fl, flip2=fl2, confirm=cf)


def show(E, name):
    print(f"\n##### {name}: {len(E)}건, 기준 거래당 {E.base.mean():+.2f}% 실패율 {np.mean(E.base < 0):.0%}")
    E = E.assign(rvq=pd.qcut(E.rv_1d.rank(method="first"), 3, labels=["rv낮", "rv중", "rv높"]),
                 first=np.where(E.sig_24h == 0, "24h첫신호", "24h재신호"),
                 zq=pd.qcut(E.z.rank(method="first"), 3, labels=["z낮", "z중", "z높"]))
    agg = lambda g: pd.Series({"n": len(g), "실패율": np.mean(g.base < 0), "기준": g.base.mean(),  # noqa: E731
                               "반대4/6": g.flip.mean(), "반대6/4": g.flip2.mean(),
                               "확인진입": g.confirm.mean(), "확인률": g.confirm.notna().mean()})
    for keys in (["rvq"], ["first"], ["zq"], ["rvq", "first"]):
        print(E.groupby(keys, observed=True).apply(agg, include_groups=False).round(2).to_string())
    for Y, g in E.groupby(E.t.dt.year):
        hi = g[g.z >= E.z.quantile(2 / 3)]
        print(f"  {Y}: 전체 {g.base.mean():+.2f}% ({len(g)}) · z상위3분 기준 {hi.base.mean():+.2f}% 반대6/4 {hi.flip2.mean():+.2f}% ({len(hi)})")


if __name__ == "__main__":
    T = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731
    bs = pd.read_pickle(F.ROOT / "data_cache" / "bitstamp_1d_2016.pkl").close
    c = F.candles().loc["2018-01-01":"2022-01-10"]
    f = feats(c, bs)
    pd.set_option("display.width", 250)
    for sig, name in ((1, "급락 → 롱 신호"), (-1, "급등 → 숏 신호")):
        E = responses(c, f, events(c, f, sig, T("2018-03-05"), T("2022-01-01")), sig)
        show(E, name)
        if sig == 1:
            b = E[(f.d50.values[E.i] > 0) & (f.d200.values[E.i] > 0)]
            show(b.reset_index(drop=True), "급락 롱 · 50·200일선 위 (지금 봇)")
