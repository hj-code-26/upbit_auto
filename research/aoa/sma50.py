"""급락 매수(롱만 D=2% · TP4 / SL6) + 일봉 50일선 필터 — 사전등록 (2026-09-26, 알트 데이터 받기 전 작성).

필터: 진입 봉이 속한 날의 '전날' 일봉 종가 > 전날까지 50일 단순이평 일 때만 진입 (당일 미완성 일봉은 안 쓴다).
나머지는 tpsl.py 와 동일: 5분 종가가 1h EMA(12봉) 대비 −2% 이하 → 다음 봉 시가 롱, 익절 +4% / 손절 −6% / 시간손절 3일,
동시 1포지션, 비용 편도 0.07%, 1배.

⚠ 이 필터는 BTC 결과(50일선 위 거래가 좋았다)를 본 뒤에 나온 아이디어다. 그래서 BTC 는 '참고'로만 보고,
판정은 한 번도 안 본 알트 6종(바이낸스 현물 5분봉 2020-01~2026-09: ETH·SOL·XRP·BNB·DOGE·ADA)으로만 한다.
파라미터는 BTC 에서 정한 그대로 — 코인별 조정 없음.

채택 기준 (알트 6종, 사전 고정):
  ① 필터 적용 시 거래당 기대값이 필터 없음보다 높은 코인 ≥ 4/6
  ② 필터 적용 기대값 > 0 인 코인 ≥ 4/6
  ③ 6종 전체 거래를 합친 필터 적용 기대값 > 0, 그리고 같은 필터 날짜 안에서 무작위 시각 진입(같은 TP/SL, 10회 평균)보다 높다
     — 50일선 위에서는 아무 때나 사도 벌 수 있으므로, 급락 신호가 보태는 게 있는지 따로 본다.
  셋 다 통과 → '필터 결합 채택 후보'. 하나라도 불합격 → 필터 기각.
"""
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import features as F  # noqa: E402
from clone import okx_5m, prep  # noqa: E402
from tpsl import COST, MAXB, path_exit  # noqa: E402

D, TP, SL = 2.0, 4.0, 6.0
ALTS = ["ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT", "ADAUSDT"]


def bull_mask(c):
    d = c.close.resample("1D").last()
    bull = (d > d.rolling(50).mean()).shift(1)            # 전날 확정값
    return bull.reindex(c.index.normalize()).fillna(False).values.astype(bool)


def run(p, mask, lo, hi):
    o, h, l, dev, idx = p["o"], p["h"], p["l"], p["dev"], p["idx"]
    i, i1, out = max(idx.searchsorted(lo), 300), idx.searchsorted(hi), []
    while i < i1 - 1:
        if dev[i] > -D or not mask[i]:
            i += 1; continue
        j, r = path_exit(o, h, l, i + 1, 1, TP, SL)
        out.append((idx[i + 1], r - 2 * COST * 100))
        i = j + 1
    return out


def rand(p, mask, n, lo, hi, reps=10, seed=0):
    idx = p["idx"]
    ok = np.where(mask[idx.searchsorted(lo):idx.searchsorted(hi) - MAXB])[0] + idx.searchsorted(lo)
    if n == 0 or len(ok) == 0:
        return []
    rng = np.random.default_rng(seed)
    return [path_exit(p["o"], p["h"], p["l"], i, 1, TP, SL)[1] - 2 * COST * 100 for _ in range(reps) for i in rng.choice(ok, n)]


def st(tr):
    r = np.array([x for _, x in tr])
    if not len(r):
        return dict(n=0, ev=np.nan, win=np.nan, cum=0.0, mdd=0.0)
    eq = np.cumprod(1 + r / 100)
    return dict(n=len(r), ev=r.mean(), win=(r > 0).mean(), cum=(eq[-1] - 1) * 100, mdd=(eq / np.maximum.accumulate(eq) - 1).min() * 100)


def fmt(s):
    return f"{s['n']:>4}건 승률 {s['win']:.0%} 기대값 {s['ev']:+.2f}% 누적 {s['cum']:+.0f}% MDD {s['mdd']:.0f}%" if s["n"] else "   0건"


if __name__ == "__main__":
    T = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731
    lo, hi = T("2020-01-01"), T("2026-10-01")
    print("알트 6종 (판정용, 2020-01~2026-09) — [필터 없음] vs [50일선 필터]")
    better = pos = 0; allf, allu, allr = [], [], []
    for sym in ALTS:
        c = pd.read_pickle(F.ROOT / "data_cache" / f"binance_{sym.lower()}_5m_2020.pkl")
        c = c.asfreq("5min").ffill()
        p, m = prep(c), bull_mask(c)
        u, f = run(p, np.ones(len(m), bool), lo, hi), run(p, m, lo, hi)
        su, sf = st(u), st(f)
        better += sf["ev"] > su["ev"]; pos += sf["ev"] > 0
        allu += u; allf += f; allr += rand(p, m, len(f), lo, hi)
        print(f"  {sym:9s} 시작 {c.index[0]:%Y-%m} | 없음 {fmt(su)} | 필터 {fmt(sf)} | 50일선 위 {m.mean():.0%}")
    ru, rf = np.array([x for _, x in allu]), np.array([x for _, x in allf])
    rr = np.array(allr)
    print(f"\n  합산: 없음 {len(ru)}건 {ru.mean():+.3f}% · 필터 {len(rf)}건 {rf.mean():+.3f}% · 같은 날짜 무작위 진입 {rr.mean():+.3f}%")
    c1, c2, c3 = better >= 4, pos >= 4, rf.mean() > 0 and rf.mean() > rr.mean()
    print(f"  ① 필터가 기대값 개선 {better}/6 {'통과' if c1 else '불합격'} · ② 필터 기대값>0 {pos}/6 {'통과' if c2 else '불합격'}"
          f" · ③ 합산>0 & 무작위보다 우위 {'통과' if c3 else '불합격'}")
    print("  판정:", "필터 결합 채택 후보" if c1 and c2 and c3 else "필터 기각")

    print("\nBTC (참고 — 이미 본 데이터)")
    PI, cb = prep(F.candles().loc["2018-02-01":"2021-12-31"]), F.candles().loc["2018-02-01":"2021-12-31"]
    co = okx_5m().loc["2021-11-01":]; PO = prep(co)
    for name, P, cc, a, b in (("2018~21 BitMEX", PI, cb, "2018-03-05", "2022-01-01"),
                              ("2022~25 OKX", PO, co, "2022-01-01", "2026-01-01"),
                              ("2026.1~9 OKX", PO, co, "2026-01-01", "2027-01-01")):
        m = bull_mask(cc)
        print(f"  {name:15s} 없음 {fmt(st(run(P, np.ones(len(m), bool), T(a), T(b))))} | 필터 {fmt(st(run(P, m, T(a), T(b))))}")
