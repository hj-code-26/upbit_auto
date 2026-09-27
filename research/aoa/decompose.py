"""우위 분해: 그의 실제 진입(시각·체결가·방향)은 그대로, 청산만 기계적 TP/SL 로 바꾼다. 비용 0 (순수 판단력 비교).

진입 우위가 있으면 기계적 청산으로도 양수가 나와야 하고, 없으면 그의 수익은 재량 청산·물타기에서 나온 것이다.
대조군: 같은 해·같은 방향 비율로 무작위 시각에 진입(현재가 기준)한 것.
"""
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import features as F  # noqa: E402

GRID = [(0.25, 0.75), (0.5, 1.0), (1.0, 2.0), (2.0, 4.0), (0.5, 0.5), (1.0, 1.0)]   # (TP%, SL%)


def mech(c, times, px, side, tp, sl, max_bars=288):
    h, l, o = c.high.values, c.low.values, c.open.values
    i0 = c.index.get_indexer(pd.DatetimeIndex(times).tz_localize("UTC").floor("5min")) + 1   # 체결 다음 봉부터 경로 판정
    out = np.full(len(i0), np.nan)
    for k, (i, p, s) in enumerate(zip(i0, px, side)):
        if i <= 0 or i + max_bars >= len(o):
            continue
        up, dn = p * (1 + s * tp / 100), p * (1 - s * sl / 100)
        for j in range(i, i + max_bars):
            hs = l[j] <= dn if s == 1 else h[j] >= dn
            ht = h[j] >= up if s == 1 else l[j] <= up
            if hs:
                out[k] = -sl; break
            if ht:
                out[k] = tp; break
        else:
            out[k] = s * (o[i + max_bars] / p - 1) * 100
    return out


if __name__ == "__main__":
    c = F.candles()
    ep = pd.read_parquet(F.ROOT / "data_cache" / "aoa_episodes.parquet")
    ep["y"] = ep.start.dt.year
    rng = np.random.default_rng(0)
    # 대조군: 같은 해 안 무작위 시각, 같은 방향 구성
    rt = []
    for y, g in ep.groupby("y"):
        lo, hi = g.start.min().value, g.start.max().value
        tt = pd.to_datetime(rng.integers(lo, hi, len(g)))
        rt.append(pd.DataFrame({"start": tt, "state": g.state.values, "y": y}))
    rnd = pd.concat(rt, ignore_index=True)
    rnd["px0"] = c.open.reindex(pd.DatetimeIndex(rnd.start).tz_localize("UTC").floor("5min")).values
    rows = []
    for tp, sl in GRID:
        a = mech(c, ep.start, ep.px0.values, ep.state.values, tp, sl)
        b = mech(c, rnd.start, rnd.px0.values, rnd.state.values, tp, sl)
        for y in sorted(ep.y.unique()):
            m, n = (ep.y == y).values, (rnd.y == y).values
            rows.append(dict(tp=tp, sl=sl, y=y, his_entry=np.nanmean(a[m]), his_win=np.nanmean(a[m] > 0),
                             random=np.nanmean(b[n]), rnd_win=np.nanmean(b[n] > 0), actual=ep.ret[m].mean()))
    r = pd.DataFrame(rows)
    pd.set_option("display.width", 200)
    print("평균 수익 %/거래 (비용 0). actual = 그의 실제 청산 결과")
    print(r.round(3).to_string())
