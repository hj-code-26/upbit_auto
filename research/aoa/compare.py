"""롱 진입 vs 숏 진입 직전 조건 비교 + 진입 후 수익(타이밍 우위).

AUC: 특징 하나로 롱/숏을 가를 때의 판별력. 0.5=무관, >0.5 = 값이 클수록 롱, <0.5 = 값이 클수록 숏.
"""
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import features as F  # noqa: E402

ROOT = F.ROOT


def load(min_lev=0.0):
    ep = pd.read_parquet(ROOT / "data_cache" / "aoa_episodes.parquet")
    ep = ep[ep.maxlev >= min_lev].reset_index(drop=True)
    f = pd.read_pickle(ROOT / "data_cache" / "aoa_feat_5m.pkl")
    c = F.candles()
    x = pd.concat([ep, F.at(f, ep.start), F.fwd(c, ep.start)], axis=1)
    x["y"] = x.start.dt.year
    x["long"] = (x.state == 1).astype(int)
    # '현재가' 특징: 진입 순간 체결가(px0) — 봇도 실시간으로 아는 값. 직전 완성봉 이후 봉 안에서 움직인 만큼을 잡는다.
    prev = F.at(pd.DataFrame({"close": c.close, "hi1h": c.high.rolling(12).max(), "lo1h": c.low.rolling(12).min(),
                              "hi15": c.high.rolling(3).max(), "lo15": c.low.rolling(3).min()}), ep.start)
    x["now_ret"] = (x.px0 / prev.close - 1) * 100
    x["now_pos_1h"] = ((x.px0 - prev.lo1h) / (prev.hi1h - prev.lo1h)).clip(-1, 2)
    x["now_pos_15m"] = ((x.px0 - prev.lo15) / (prev.hi15 - prev.lo15)).clip(-1, 2)
    pos = [c for c in x.columns if c.startswith("pos_")]
    x[pos] = x[pos].fillna(0.5)            # 박스 폭 0(가격 무변동) = 가운데
    return x.dropna(subset=["ret_7d", "rsi_4h", "rv_1d"]).reset_index(drop=True)


def auc(score, label):
    r = score.rank()
    n1 = label.sum(); n0 = len(label) - n1
    return (r[label == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)


FEATS = [c for c in pd.read_pickle(ROOT / "data_cache" / "aoa_feat_5m.pkl").columns if c not in ("hour", "dow")]

if __name__ == "__main__":
    x = load()
    print("표본", len(x), x.groupby("y").long.agg(["size", "mean"]).round(3).to_dict())
    rows = []
    for col in FEATS:
        row = {"feat": col, "all": auc(x[col], x.long)}
        for y, g in x.groupby("y"):
            row[y] = auc(g[col], g.long)
        row["L_med"] = x.loc[x.long == 1, col].median(); row["S_med"] = x.loc[x.long == 0, col].median()
        rows.append(row)
    a = pd.DataFrame(rows).set_index("feat")
    a["dev"] = (a["all"] - 0.5).abs()
    print(a.sort_values("dev", ascending=False).round(3).to_string())
    print("\n진입 후 방향 수익(%, 방향 부호 반영) — 그가 맞힌 정도")
    for h in ("fwd_1h", "fwd_4h", "fwd_1d"):
        x[h + "_s"] = x[h] * x.state
    print(x.groupby(["y", "state"])[["fwd_1h_s", "fwd_4h_s", "fwd_1d_s"]].agg(["mean", lambda s: (s > 0).mean()]).round(3))
    print("\n시간대(UTC)별 롱 비율"); print(x.groupby("hour").long.agg(["size", "mean"]).round(2).T.to_string())
