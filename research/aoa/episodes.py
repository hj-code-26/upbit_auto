"""aoa 포지션 → 에피소드(방향이 유지된 구간). 자기자본 대비 레버리지 |lev| >= FLAT 이면 방향 보유로 본다.

에피소드 = 방향 상태(+1/−1)가 시작돼서 0 으로 돌아가거나 반대로 뒤집힐 때까지.
저장: data_cache/aoa_episodes.parquet
"""
import pathlib

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "data_cache" / "aoa_episodes.parquet"
FLAT = 0.1   # |포지션 / 자기자본| 이 이 미만이면 무포지션(먼지·잔량)으로 본다


def trades_with_lev():
    t = pd.read_parquet(ROOT / "data_cache" / "aoa_xbt_pos.parquet")
    w = pd.read_csv(ROOT / "COIN_CHANGE" / "aoa-wallet-2018-03-01-2021-12-31.csv", encoding="utf-8-sig")
    w["d"] = pd.to_datetime(w.date)
    bal = (w.groupby("d").walletbalance.last() / 1e8).rename("bal").reset_index()
    t["d"] = t.transacttime.dt.normalize()
    t["bal"] = pd.merge_asof(t[["d"]], bal, on="d").bal.values
    t["lev"] = t.pos / (t.bal * t.lastpx).where(lambda s: s > 0)
    t["state"] = np.where(t.lev >= FLAT, 1, np.where(t.lev <= -FLAT, -1, 0))
    return t


def episodes(t):
    # 같은 시각 체결 묶음(한 주문 다수 체결)은 마지막 상태만 본다
    g = t.groupby("transacttime", sort=True).agg(state=("state", "last"), px=("lastpx", "last"),
                                                  lev=("lev", "last"), pos=("pos", "last")).reset_index()
    chg = g.state.ne(g.state.shift()).cumsum()
    seg = g.groupby(chg).agg(state=("state", "first"), start=("transacttime", "first"), px0=("px", "first"),
                             maxlev=("lev", lambda s: s.abs().max()))
    seg["end"] = seg.start.shift(-1)
    seg["px1"] = seg.px0.shift(-1)          # 다음 상태로 넘어간 첫 체결가 ≈ 청산가
    seg = seg[seg.state != 0].dropna(subset=["end"]).reset_index(drop=True)
    seg["hours"] = (seg.end - seg.start).dt.total_seconds() / 3600
    seg["ret"] = seg.state * (seg.px1 / seg.px0 - 1) * 100
    return seg


if __name__ == "__main__":
    ep = episodes(trades_with_lev())
    ep.to_parquet(OUT)
    ep["y"] = ep.start.dt.year
    print("에피소드", len(ep), "롱 비율", round((ep.state == 1).mean(), 3))
    print(ep.hours.describe(percentiles=[.1, .25, .5, .75, .9]).round(2).to_dict())
    print(ep.groupby(["y", "state"]).agg(n=("ret", "size"), win=("ret", lambda r: (r > 0).mean()),
                                          avg=("ret", "mean"), med_h=("hours", "median"),
                                          maxlev=("maxlev", "median")).round(3))
