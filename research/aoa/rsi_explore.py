"""aoa 진입을 RSI 로 해부 (탐색 — 규칙 채택용 아님).
1) 롱/숏 진입 시 시간대별 RSI 분포와 판별력(AUC)
2) RSI(1h) × 가격 위치(하루 박스) 지도: 롱 비율 · 진입 빈도
3) 다이버전스가 '선택'을 설명하나: 같은 신저점(신고점) 봉 중 다이버전스 유무별 그의 진입률
"""
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import features as F  # noqa: E402
from compare import auc  # noqa: E402

RS = ["rsi_5m", "rsi_15m", "rsi_1h", "rsi_4h", "rsi_1d"]

if __name__ == "__main__":
    f = pd.read_pickle(F.ROOT / "data_cache" / "aoa_rsi_5m.pkl").loc["2018-03-05":"2021-12-31"]
    ep = pd.read_parquet(F.ROOT / "data_cache" / "aoa_episodes.parquet")
    x = pd.concat([ep, F.at(f, ep.start)], axis=1).dropna(subset=["rsi_1d"])
    x["y"] = x.start.dt.year; x["long"] = (x.state == 1).astype(int)
    pd.set_option("display.width", 220)

    print("1) 진입 시 RSI 중앙값 (롱 / 숏) 과 AUC (0.5=무관, <0.5 = RSI 낮을수록 롱)")
    for r in RS:
        a = {y: round(auc(g[r], g.long), 3) for y, g in x.groupby("y")}
        print(f"  {r:8s} 롱 {x.loc[x.long == 1, r].median():5.1f}  숏 {x.loc[x.long == 0, r].median():5.1f}  AUC {auc(x[r], x.long):.3f} {a}")
    for r in ("rsi_5m", "rsi_1h", "rsi_4h"):
        b = pd.cut(x[r], [0, 20, 30, 40, 50, 60, 70, 80, 100])
        t = x.groupby(b, observed=True).long.agg(["size", "mean"]).T.round(2)
        print(f"\n  {r} 구간별 [건수 / 롱 비율]"); print(t.to_string())

    print("\n2) RSI(1h) × 하루 박스 위치 — 롱 비율 (표본 20 미만은 빈칸)")
    x["rb"] = pd.cut(x.rsi_1h, [0, 30, 40, 50, 60, 70, 100]); x["pb"] = pd.cut(x.pos_1d, [-.01, .1, .3, .5, .7, .9, 1.01])
    pv = x.pivot_table(index="rb", columns="pb", values="long", aggfunc=["mean", "size"], observed=True)
    print(pv["mean"].where(pv["size"] >= 20).round(2).to_string())
    print("   건수"); print(pv["size"].to_string())
    # 진입 빈도: 같은 칸의 전체 5분봉 대비
    lab = pd.Series(0, index=f.index)
    bar = pd.DatetimeIndex(ep.start).tz_localize("UTC").floor("5min") - pd.Timedelta("5min")
    lab.loc[bar.intersection(f.index)] = 1
    g = f.assign(e=lab, rb=pd.cut(f.rsi_1h, [0, 30, 40, 50, 60, 70, 100]), pb=pd.cut(f.pos_1d, [-.01, .1, .3, .5, .7, .9, 1.01]))
    print("   진입 빈도 (그 칸 5분봉 1,000개당 진입 수)")
    print(g.pivot_table(index="rb", columns="pb", values="e", aggfunc="mean", observed=True).mul(1000).round(1).to_string())

    print("\n3) 다이버전스가 '선택'을 설명하나 — 신저점 봉에서 다음 봉 롱 진입률 / 신고점 봉에서 숏 진입률 (1,000봉당)")
    L = pd.Series(0, index=f.index); S = pd.Series(0, index=f.index)
    L.loc[bar[(ep.state == 1).values].intersection(f.index)] = 1
    S.loc[bar[(ep.state == -1).values].intersection(f.index)] = 1
    g = f.assign(L=L, S=S, y=f.index.year)
    for W in ("36", "144"):
        for side, cond, d, lab_ in (("롱", f"newlow_{W}", f"bull_{W}", "L"), ("숏", f"newhigh_{W}", f"bear_{W}", "S")):
            s = g[g[cond]]
            rows = {y: (round(v[v[d]][lab_].mean() * 1000, 1), round(v[~v[d]][lab_].mean() * 1000, 1)) for y, v in s.groupby("y")}
            lift = s[s[d]][lab_].mean() / s[~s[d]][lab_].mean()
            print(f"  창 {W:>3}봉 {side}: 다이버전스 있음/없음 {rows} | 배율 {lift:.2f} | 신저(고)점 중 다이버전스 비율 {s[d].mean():.0%}")
    for side, cond, d, lab_ in (("롱", "h_newlow", "h_bull", "L"), ("숏", "h_newhigh", "h_bear", "S")):
        s = g[g[cond]]
        rows = {y: (round(v[v[d]][lab_].mean() * 1000, 1), round(v[~v[d]][lab_].mean() * 1000, 1)) for y, v in s.groupby("y")}
        print(f"  1h 봉 {side}: 다이버전스 있음/없음 {rows} | 배율 {s[s[d]][lab_].mean() / s[~s[d]][lab_].mean():.2f}")
    # 진입 방향: 다이버전스 종류가 방향을 가르나
    print("\n  진입 시 다이버전스 유형별 롱 비율:")
    for k in ("bull_36", "bear_36", "bull_144", "bear_144", "h_bull", "h_bear"):
        m = x[k].astype(bool)
        print(f"    {k:9s} 있음 {m.sum():4d}건 롱 {x.long[m].mean():.2f} | 없음 롱 {x.long[~m].mean():.2f}")
