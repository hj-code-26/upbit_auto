"""RSI 조건을 급락 매수(롱만 · TP4/SL6 · 3일)에 더하면 나아지나 — 사전등록 (2026-09-26, rsi_explore.py 직후 작성).

기준 V0: 5분 종가가 1h EMA 대비 −2% 이하.  변형(신호 봉에서 추가 조건):
  V1 과매도 확인     : + RSI(5m) ≤ 30
  V2 교과서 다이버전스: + 강세 다이버전스(5분봉 창 36 또는 144)
  V3 투매(그의 2018~20 선호): + 신저점(창 36)인데 강세 다이버전스 없음 = RSI 도 같이 신저점
  V4 합치 구간(그의 롱 72% 칸): V0 대신 RSI(1h) ≤ 30 & 하루 박스 위치 ≤ 0.1 만으로 진입
  V5 극단 회피(그는 RSI<20 을 덜 받아쳤다): + 20 < RSI(5m) ≤ 40
각 변형을 필터 none / 50일선(B) 두 가지로. 체결·비용·청산은 tpsl.py 와 동일, 1배.

채택 기준 (변형별, 같은 필터의 V0 대비, 전부 통과해야):
  ① BTC IS(BitMEX 2018-03~2021-12) 기대값 > V0 이고 거래 ≥ 30
  ② BTC OOS(OKX) 2022-23 · 2024-25 둘 다 기대값 > 0, 그리고 OOS 전체 기대값 > V0
  ③ 처음 보는 알트 6종(바이낸스 2020-01~2026-09)에서 V0 보다 기대값 높은 코인 ≥ 4/6, 합산 기대값도 V0 보다 높음
"""
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import features as F  # noqa: E402
import rsi as R  # noqa: E402
from clone import okx_5m, prep  # noqa: E402
from sma50 import ALTS, bull_mask  # noqa: E402
from tpsl import COST, path_exit  # noqa: E402

TP, SL = 4.0, 6.0


def signals(c):
    f, p = R.build(c), prep(c)
    dev = pd.Series(p["dev"], index=c.index)
    v0 = (dev <= -2).values
    r5 = f.rsi_5m.values
    return p, {
        "V0 급락 매수": v0,
        "V1 +RSI5m≤30": v0 & (r5 <= 30),
        "V2 +강세다이버전스": v0 & (f.bull_36 | f.bull_144).values,
        "V3 +투매(다이버전스 없는 신저점)": v0 & (f.newlow_36 & ~f.bull_36).values,
        "V4 RSI1h≤30 & 하루바닥": ((f.rsi_1h <= 30) & (f.pos_1d <= 0.1)).values,
        "V5 +20<RSI5m≤40": v0 & (r5 > 20) & (r5 <= 40),
    }


def run(p, sig, lo, hi):
    o, h, l, idx = p["o"], p["h"], p["l"], p["idx"]
    i, i1, out = max(idx.searchsorted(lo), 300), idx.searchsorted(hi), []
    while i < i1 - 1:
        if not sig[i]:
            i += 1; continue
        j, r = path_exit(o, h, l, i + 1, 1, TP, SL)
        out.append(r - 2 * COST * 100)
        i = j + 1
    return np.array(out)


ev = lambda r: r.mean() if len(r) else np.nan  # noqa: E731

if __name__ == "__main__":
    T = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731
    ci, co = F.candles().loc["2018-02-01":"2021-12-31"], okx_5m().loc["2021-11-01":]
    (PI, SI), (PO, SO) = signals(ci), signals(co)
    FI, FO = bull_mask(ci), bull_mask(co)
    alts = {}
    for s in ALTS:
        c = pd.read_pickle(F.ROOT / "data_cache" / f"binance_{s.lower()}_5m_2020.pkl").asfreq("5min").ffill()
        alts[s] = (*signals(c), bull_mask(c))
    pd.set_option("display.width", 250)
    for fl in ("none", "B 50일선"):
        rows = []
        for v in SI:
            mi = SI[v] & (FI if fl != "none" else True); mo = SO[v] & (FO if fl != "none" else True)
            a = run(PI, mi, T("2018-03-05"), T("2022-01-01"))
            b1, b2 = run(PO, mo, T("2022-01-01"), T("2024-01-01")), run(PO, mo, T("2024-01-01"), T("2026-01-01"))
            al = {s[:-4]: run(p, sg[v] & (m if fl != "none" else True), T("2020-01-01"), T("2026-10-01")) for s, (p, sg, m) in alts.items()}
            rows.append(dict(변형=v, IS_n=len(a), IS_win=(a > 0).mean() if len(a) else np.nan, IS=ev(a),
                             OOS1_n=len(b1), OOS1=ev(b1), OOS2_n=len(b2), OOS2=ev(b2), OOS=ev(np.r_[b1, b2]),
                             **{k: ev(r) for k, r in al.items()}, 알트합=ev(np.concatenate(list(al.values()))),
                             알트n=sum(len(r) for r in al.values())))
        g = pd.DataFrame(rows).set_index("변형")
        base = g.iloc[0]
        coins = [s[:-4] for s in ALTS]
        g["판정"] = ["기준"] + [
            "채택" if (r.IS > base.IS and r.IS_n >= 30 and r.OOS1 > 0 and r.OOS2 > 0 and r.OOS > base.OOS
                      and sum(r[k] > base[k] for k in coins) >= 4 and r.알트합 > base.알트합)
            else "기각(" + ",".join(n for n, ok in (("①", r.IS > base.IS and r.IS_n >= 30),
                                                    ("②", r.OOS1 > 0 and r.OOS2 > 0 and r.OOS > base.OOS),
                                                    ("③", sum(r[k] > base[k] for k in coins) >= 4 and r.알트합 > base.알트합)) if not ok) + ")"
            for _, r in g.iloc[1:].iterrows()]
        print(f"\n=== 필터 {fl} === (기대값 = 거래당 %, 비용 포함)")
        print(g.round(3).to_string())
