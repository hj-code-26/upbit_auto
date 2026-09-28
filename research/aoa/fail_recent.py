"""최근 6개월(2026-03-28~) 에 fail_rules.py 의 기준(V0)·H1~H4 를 그대로 적용한 결과 — 거래 단위로 보여준다 (2026-09-28).
캐시(okx_1m.pkl) 뒤로 빈 구간은 OKX 공개 5분봉으로 채운다. 규칙·문턱은 fail_rules.py 그대로(여기서 아무것도 고르지 않음)."""
import pathlib
import sys

import ccxt
import numpy as np
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[1]))
import features as F  # noqa: E402
import okx as X  # noqa: E402
import strategy as S  # noqa: E402
from clone import okx_5m  # noqa: E402
from fail_rules import ZL, ZS, exit_at, prep  # noqa: E402

LO = pd.Timestamp("2026-03-28", tz="UTC")


def data():
    c = okx_5m()
    new = X.bars_since(ccxt.okx(), c.index[-1] + S.BAR, "5m")[["open", "high", "low", "close", "volume"]]
    now = pd.Timestamp.now(tz="UTC").floor(S.BAR)
    c = pd.concat([c, new[new.index < now]])
    return c[~c.index.duplicated(keep="last")].asfreq(S.BAR).ffill()


def trades(p, trig, side):
    idx, out, i = p["idx"], [], max(p["idx"].searchsorted(LO), 300)
    while i < len(idx) - 1:
        if not trig[i]:
            i += 1; continue
        j, r = exit_at(p, i + 1, side)
        out.append(dict(신호=idx[i].strftime("%m-%d %H:%M"), z=round(p["z"][i], 2), 청산=idx[j].strftime("%m-%d %H:%M"),
                        수익=round(r, 2), 상태="보유중" if j >= len(idx) - 2 else ("익절" if r > S.TP - 1 else "손절" if r < -S.SL else "만기")))
        i = j + 1
    return pd.DataFrame(out)


if __name__ == "__main__":
    bs = pd.read_pickle(F.ROOT / "data_cache" / "bitstamp_1d_2016.pkl").close
    c = data()
    p = prep(c, bs)
    dev, bull, z = p["dev"], p["bull"], p["z"]
    m = p["idx"] >= LO
    print(f"데이터 {c.index[0]:%Y-%m-%d} ~ {c.index[-1]:%Y-%m-%d %H:%M} UTC · 최근 6개월 BTC {c.close[m].iloc[0]:,.0f} → {c.close.iloc[-1]:,.0f}")
    print(f"50·200일선 위(롱 허용) 비율 {bull[m].mean():.0%} · 급락(dev≤−2%) 봉 {(dev[m] <= -S.D).sum()} · 그중 롱 허용 {((dev <= -S.D) & bull)[m].sum()}"
          f" · 급등(dev≥+2%) 봉 {(dev[m] >= S.D).sum()}")
    d = c.close.resample("1D").last(); dd = pd.concat([bs[bs.index < d.index[0]], d])
    mo = pd.DataFrame({"종가": dd, "50일선": dd.rolling(50).mean(), "200일선": dd.rolling(200).mean()}).loc["2026-03-28":].resample("MS").last()
    print(mo.round(0).to_string())
    pd.set_option("display.width", 200)
    up = dev >= S.D
    sig24 = pd.Series(up.astype(float)).rolling(288).sum().shift(1).values
    for name, trig, side in [("V0 지금 봇 (H1 은 z≥2.66 제외 · H2 는 z≥2.66 절반)", (dev <= -S.D) & bull, 1),
                             ("급락 전부 (필터 없음, 참고)", dev <= -S.D, 1),
                             ("H3 조용한 장 급락 숏", (dev <= -S.D) & (z >= ZL), -1),
                             ("H4 출렁이는 장 급등 숏", up & (z < ZS) & (sig24 >= 1), -1)]:
        t = trades(p, trig, side)
        print(f"\n=== {name}: {len(t)}건", end="")
        if len(t):
            hot = t.z >= ZL
            print(f" · 거래당 {t.수익.mean():+.2f}% · 승률 {np.mean(t.수익 > 0):.0%} · 합 {t.수익.sum():+.2f}%p", end="")
            if side == 1:
                print(f" | H1 합 {t.수익[~hot].sum():+.2f}%p ({(~hot).sum()}건) · H2 합 {(t.수익 * np.where(hot, .5, 1)).sum():+.2f}%p", end="")
            print(" ===")
            print(t.to_string(index=False))
        else:
            print(" ===")
