"""익절·손절 ≤ 1.5% 단타 탐색 (1H 봉). 진입 = 봉 시가 시장가(taker+슬리피지), 익절 = 지정가(maker), 손절·시간 청산 = taker+슬리피지.
같은 봉에 익절·손절이 둘 다 닿으면 손절 먼저 (보수적). 한 번에 한 포지션 (코인별). 최대 보유 MAXB 봉 뒤 시장가 청산."""
import sys, itertools, warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))
import numpy as np, pandas as pd, rls, backtest as bt
from numpy.lib.stride_tricks import sliding_window_view as swv
from multiprocessing import Pool
TAKER, SLIP, MAKER, MAXB = 0.0005, 0.0002, 0.0002, 24
COINS = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP"]
LV = [0.005, 0.0075, 0.01, 0.0125, 0.015]

def load(c):
    h = bt.cached_hourly(c).sort_values("ts").reset_index(drop=True)
    t = pd.to_datetime(h.ts, unit="ms")
    D = rls.prep(rls.offset_daily(h, 0, 1))
    prevday = t.dt.floor("D") - pd.Timedelta(days=1)                  # 그 봉 시점에 확정된 마지막 일봉
    conf = prevday.map(D.conf).fillna(0).astype(int).values
    ph, pl = prevday.map(D.high).values, prevday.map(D.low).values
    c_ = h.close.values; pc = np.r_[np.nan, c_[:-1]]                  # 직전 1H 종가 (진입 봉 시가 전에 아는 값)
    r24 = np.r_[np.full(25, np.nan), c_[24:-1] / c_[:-25] - 1]        # 직전 24시간 수익률 (직전 봉 종가 기준)
    sig = {"trend_daily00": np.where(t.dt.hour.values == 0, conf, 0),
           "trend_hourly": conf,
           "breakout": np.where(pc > ph, 1, np.where(pc < pl, -1, 0)),
           "breakout&trend": np.where((pc > ph) & (conf == 1), 1, np.where((pc < pl) & (conf == -1), -1, 0)),
           "meanrev": np.where(pc < pl, 1, np.where(pc > ph, -1, 0)),
           "dip&trend": np.where((pc < pl) & (conf == 1), 1, np.where((pc > ph) & (conf == -1), -1, 0)),
           "mom24": np.sign(np.nan_to_num(r24)).astype(int),
           "always_long": np.ones(len(h), int)}
    return {"t": t.values, "o": h.open.values, "h": h.high.values, "l": h.low.values, "c": c_, "sig": sig}

def run(d, name, tp, sl):
    o, H, L, C = d["o"], d["h"], d["l"], d["c"]; n = len(o)
    W = n - MAXB
    hw, lw = swv(H, MAXB)[:W], swv(L, MAXB)[:W]                        # 봉 i..i+MAXB-1
    side = d["sig"][name][:W]
    out = []
    i = 0
    up_tp, up_sl = hw >= (o[:W] * (1 + tp))[:, None], lw <= (o[:W] * (1 - sl))[:, None]   # 롱
    dn_tp, dn_sl = lw <= (o[:W] * (1 - tp))[:, None], hw >= (o[:W] * (1 + sl))[:, None]   # 숏
    while i < W:
        s = side[i]
        if s == 0 or np.isnan(o[i]):
            i += 1; continue
        a, b = (up_tp[i], up_sl[i]) if s > 0 else (dn_tp[i], dn_sl[i])
        ja = a.argmax() if a.any() else MAXB; jb = b.argmax() if b.any() else MAXB
        if jb <= ja and jb < MAXB:  r, j = -sl - TAKER - SLIP - TAKER - SLIP, jb       # 손절 (같은 봉이면 손절)
        elif ja < MAXB:             r, j = tp - TAKER - SLIP - MAKER, ja
        else:                       r, j = s * (C[i + MAXB - 1] / o[i] - 1) - 2 * (TAKER + SLIP), MAXB - 1
        out.append((d["t"][i], r)); i += j + 1
    return pd.DataFrame(out, columns=["t", "r"])

def job(args):
    c, name, tp, sl = args
    d = DATA[c]; tr = run(d, name, tp, sl); res = {"coin": c[:3], "sig": name, "tp%": tp * 100, "sl%": sl * 100}
    for tag, a, b in (("is", "2020-04-01", "2023-06-30"), ("oos", "2023-07-01", "2026-10-16")):
        x = tr[(tr.t >= np.datetime64(a)) & (tr.t <= np.datetime64(b))].r
        res |= {f"{tag}_n": len(x), f"{tag}_win": round((x > 0).mean() * 100, 1) if len(x) else 0,
                f"{tag}_ev": round(x.mean() * 100, 4) if len(x) else 0, f"{tag}_sum": round(x.sum() * 100, 1)}
    return res

DATA = {c: load(c) for c in COINS}
if __name__ == "__main__":
    jobs = [(c, s, tp, sl) for c in COINS for s in DATA[c]["sig"] for tp in LV for sl in LV]
    with Pool(9) as p: df = pd.DataFrame(p.map(job, jobs, chunksize=8))
    df.to_csv("scalp.csv", index=False); print(len(df), "rows")
