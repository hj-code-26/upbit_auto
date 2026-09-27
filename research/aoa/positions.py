"""aoa XBTUSD 포지션 재구성: 체결 누적합 → 포지션 이력, 펀딩 행(그 시각 보유 수량)으로 검증.

XBTUSD 는 인버스 계약: 수량 = USD 명목. Buy +, Sell −.
저장: data_cache/aoa_xbt_pos.parquet (체결 단위 시각·가격·수량·누적 포지션)
"""
import pathlib

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "data_cache" / "aoa_xbt_pos.parquet"


def build():
    d = pd.read_parquet(ROOT / "data_cache" / "aoa_exec.parquet",
                        columns=["symbol", "side", "lastqty", "lastpx", "exectype", "ordtype", "execinst",
                                 "lastliquidityind", "foreignnotional", "transacttime", "orderid"])
    x = d[d.symbol == "XBTUSD"]
    t = x[x.exectype == "Trade"].copy()
    t["q"] = np.where(t.side == "Buy", 1, -1) * t.lastqty
    t["pos"] = t.q.cumsum()
    f = x[x.exectype == "Funding"].copy()
    return t.reset_index(drop=True), f.reset_index(drop=True)


if __name__ == "__main__":
    t, f = build()
    # 펀딩 행 foreignnotional 부호: 롱이면 음수(인버스 관례) 인지 확인
    f["recon"] = pd.merge_asof(f[["transacttime"]], t[["transacttime", "pos"]], on="transacttime").pos.values
    f["mag_err"] = f.lastqty - f.recon.abs()
    f["sign_prod"] = np.sign(f.foreignnotional) * np.sign(f.recon)
    print("펀딩 시점 |포지션| 오차:", f.mag_err.abs().describe().to_dict())
    print("부호 일치(foreignnotional×recon):", f.sign_prod.value_counts().to_dict())
    print(f[["transacttime", "lastqty", "foreignnotional", "recon"]].iloc[::400].to_string())
    print(t.groupby(t.transacttime.dt.year).agg(n=("q", "size"), vol_musd=("lastqty", lambda v: v.sum() / 1e6)))
    print(t.ordtype.value_counts().to_dict(), t.lastliquidityind.value_counts().to_dict())
    t.to_parquet(OUT)
