"""손실 방어 장치 검증 — 사전등록 (2026-09-27, 결과 보기 전 작성).

기준 V0 = dipbuy.py 그대로 (BTC · 50일선 · 롱 2% 급락 · TP4/SL6/3일 · 비용 0.07% · 펀딩 · 갭 손절).
방어 후보 (하나씩 V0 에 더한다):
  V1 본전 손절   : 고가가 진입가 +2% 에 닿은 '다음 봉부터' 손절선을 진입가(+왕복 비용)로 올린다
  V2 손절 후 휴식: 손절 청산 후 288봉(24h) 동안 신규 신호 무시
  V3 50·200일선  : 전날 종가가 50일선 & 200일선 둘 다 위 (200일선은 BTC=비트스탬프 일봉 2016~, 알트=자기 데이터)
  V4 50일선 상승 : 전날 50일선 > 10일 전 50일선 (50일선 필터에 추가)
  V5 손절 후 절반: 손절 다음 거래 1건은 0.5배 (aoa: 큰 손실 뒤 사이즈 0.5~0.8배)
  V6 변동성 맞춤 : 크기 = min(1, 3% / 직전 20일 일간 수익률 표준편차)
  V7 낙폭 브레이크: 진입 시 계좌가 고점 대비 −15% 아래면 0.5배 (새 고점까지)
  V8 RSI 극단 회피: 신호 봉 RSI(5m) ≤ 20 이면 진입 안 함 (aoa: 극단 RSI 는 덜 받아쳤다)
평가: 1배, 거래마다 자본 × (1 + 크기 × 순수익), MDD 는 보유 중 5분봉 저가 평가손 포함. 수익/위험 = 연복리 ÷ |MDD|.
데이터: BTC 3종 (BitMEX 2018-03~2021-12 · 바이낸스 2020-03~2026-09 · OKX 2021-05~2026-09) + 알트 6종(바이낸스 2020-03~2026-09).
채택 (후보별): ① BTC 3종 모두에서 수익/위험 > V0  ② BTC 3종 중 가장 나쁜 MDD 가 V0 보다 얕다
              ③ 알트 6종 중 4종 이상에서 수익/위험 > V0.  셋 다 → 채택. 여러 개 채택되면 조합은 별도 사전등록.
"""
import pathlib
import sys

import numpy as np
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[1]))
import dipbuy as DB  # noqa: E402
import features as F  # noqa: E402
from clone import okx_5m  # noqa: E402
from sma50 import ALTS  # noqa: E402

VARS = ["V0 기준", "V1 본전손절", "V2 손절후휴식", "V3 50·200일선", "V4 50일선상승", "V5 손절후절반", "V6 변동성맞춤",
        "V7 낙폭브레이크", "V8 RSI극단회피"]


def daily_ctx(c, long_daily=None):
    d = c.close.resample("1D").last()
    dl = long_daily if long_daily is not None else d
    s50 = d.rolling(50).mean()
    ctx = pd.DataFrame({"s200": (dl > dl.rolling(200).mean()).reindex(d.index).fillna(False),
                        "rise": s50 > s50.shift(10),
                        "rv": np.log(d).diff().rolling(20).std() * 100})
    prev = c.index.normalize() - pd.Timedelta("1D")          # 전날 확정값
    return ctx.reindex(prev).set_index(c.index)


def run(c, lo, hi, var, long_daily=None):
    dev, bull = DB.indicators(c, c.close.resample("1D").last())
    ctx = daily_ctx(c, long_daily)
    dev, bull = dev.values, bull.values
    s200, rise, rv = ctx.s200.fillna(False).values.astype(bool), ctx.rise.fillna(False).values.astype(bool), ctx.rv.values
    rsi5 = F.rsi(c.close, 14).values
    o, h, l = c.open.values, c.high.values, c.low.values
    idx = c.index
    i, i1 = idx.searchsorted(lo), idx.searchsorted(hi)
    eq, peak, mdd, trades, cool_until, half_next = 1.0, 1.0, 0.0, [], -1, False
    while i < i1 - 1:
        ok = dev[i] <= -DB.D and bull[i] and i > cool_until
        if ok and var == "V3 50·200일선":
            ok = s200[i]
        if ok and var == "V4 50일선상승":
            ok = rise[i]
        if ok and var == "V8 RSI극단회피":
            ok = rsi5[i] > 20
        if not ok:
            i += 1; continue
        k = i + 1; e = o[k]; up, dn = e * (1 + DB.TP / 100), e * (1 - DB.SL / 100)
        size = 1.0
        if var == "V5 손절후절반" and half_next:
            size = 0.5
        if var == "V6 변동성맞춤" and rv[i] > 0:
            size = min(1.0, 3.0 / rv[i])
        if var == "V7 낙폭브레이크" and eq / peak - 1 < -0.15:
            size = 0.5
        be_on, j, px, why = False, k, None, None
        end = min(k + DB.MAXB, len(o) - 1)
        while j < end:
            if l[j] <= dn:
                px, why = min(dn, o[j]), ("본전" if be_on else "손절"); break
            if h[j] >= up:
                px, why = up, "익절"; break
            if var == "V1 본전손절" and not be_on and h[j] >= e * 1.02:
                be_on, dn = True, e * (1 + 2 * DB.COST)          # 다음 봉부터 적용
            j += 1
        if px is None:
            j, px, why = end, o[end], "시간"
        net = DB.net_ret(e, px, j - k + 1)
        mae = min(l[k:j + 1].min() / e - 1, px / e - 1)
        mdd = min(mdd, eq * (1 + size * mae) / peak - 1)
        eq *= 1 + size * net; peak = max(peak, eq); mdd = min(mdd, eq / peak - 1)
        trades.append(net)
        half_next = why == "손절"
        if var == "V2 손절후휴식" and why == "손절":
            cool_until = j + 288
        i = j + 1
    yrs = (min(hi, idx[-1]) - lo).days / 365.25
    cagr = (eq ** (1 / yrs) - 1) * 100
    t = np.array(trades)
    return dict(n=len(t), win=(t > 0).mean() if len(t) else np.nan, cagr=cagr, mdd=mdd * 100,
                rr=cagr / abs(mdd * 100) if mdd < 0 else np.nan)


if __name__ == "__main__":
    T = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731
    bs = pd.read_pickle(F.ROOT / "data_cache" / "bitstamp_1d_2016.pkl").close
    rd = lambda s: pd.read_pickle(F.ROOT / "data_cache" / f"binance_{s.lower()}_5m_2020.pkl").asfreq("5min").ffill()  # noqa: E731
    btc = [("BitMEX 18-21", F.candles().loc["2018-01-01":"2022-01-10"], "2018-03-05", "2022-01-01", bs),
           ("바이낸스 20-26", rd("BTCUSDT"), "2020-03-01", "2026-10-01", bs),
           ("OKX 21-26", okx_5m(), "2021-05-01", "2026-10-01", bs)]
    alts = [(s[:-4], rd(s), "2020-03-01", "2026-10-01", None) for s in ALTS]
    res = {}
    for name, c, lo, hi, ld in btc + alts:
        for v in VARS:
            res[(name, v)] = run(c, T(lo), T(hi), v, ld)
        print("done", name, flush=True)
    pd.set_option("display.width", 250)
    names = [b[0] for b in btc]; an = [a[0] for a in alts]
    rows = []
    for v in VARS:
        row = {"변형": v}
        for n in names:
            r = res[(n, v)]
            row[n] = f"{r['n']}건 {r['win']:.0%} 연{r['cagr']:+.1f}% MDD{r['mdd']:.0f}% rr{r['rr']:.2f}"
        row["알트 rr 개선"] = sum(res[(a, v)]["rr"] > res[(a, "V0 기준")]["rr"] for a in an) if v != "V0 기준" else "-"
        row["알트 rr 평균"] = round(np.nanmean([res[(a, v)]["rr"] for a in an]), 2)
        row["알트 MDD 평균"] = round(np.mean([res[(a, v)]["mdd"] for a in an]), 1)
        if v != "V0 기준":
            c1 = all(res[(n, v)]["rr"] > res[(n, "V0 기준")]["rr"] for n in names)
            c2 = min(res[(n, v)]["mdd"] for n in names) > min(res[(n, "V0 기준")]["mdd"] for n in names)
            c3 = row["알트 rr 개선"] >= 4
            row["판정"] = "채택" if c1 and c2 and c3 else "기각(" + ",".join(k for k, ok in (("①", c1), ("②", c2), ("③", c3)) if not ok) + ")"
        rows.append(row)
    print(pd.DataFrame(rows).set_index("변형").to_string())
