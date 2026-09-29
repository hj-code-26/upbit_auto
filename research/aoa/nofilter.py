"""필터 없음 · 익절 4% · 손절 6.5% — 사전등록 확인 (2026-09-29, 결과 보기 전 작성·커밋). 운영 코드는 건드리지 않는다.

출처: OKX 21-26 익절×손절×보유 격자(1,653조합, 사후 탐색)에서 '필터 없음' 최고 조합(1배 연 +24.6% · MDD −26%).
      OKX 는 고를 때 쓴 데이터라 판정에 쓰지 않는다 — 보고만.
비교: 후보 X = 50·200일선 필터 없음 · TP4 · SL6.5 · 3일
      기준 B = 현행 봇 (필터 있음 · TP4 · SL6 · 3일)
공통: 진입 = 5분 종가 1h EMA −2% 이하 → 다음 봉 시가 롱. 체결 exec_model B2(현행 실봇) · 비용 편도 0.07% · 펀딩 0.01%/8h.
      2배 = 수익·평가손 ×2 근사 (손절 6.5% 라 2배 청산선 −50% 에 닿지 않는다).
판정 데이터: BTC 2종 — BitMEX 2018-03~2021-12 · 바이낸스 2020-03~2026-09 (일봉 이평 예열은 비트스탬프)
            알트 6종 — 바이낸스 현물 2020-01~2026-09 ETH·SOL·XRP·BNB·DOGE·ADA (첫 200일은 B 가 거래 없음 — 두 쪽 같은 구간으로 비교)
사용자 목표 = 하이리스크 하이리턴 (2배 실계좌). 그래서 기준은 '수익이 더 큰가' + '2배로 살아남는가' 로 둔다.
채택 기준 (넷 다 통과해야 X 채택 후보, 반영은 사용자 결정):
  ① BTC 2종 모두 1배 연복리 X > B
  ② BTC 2종 모두 2배 MDD(X) ≥ −60%            (보유 중 저가 평가손 포함 — 이보다 깊으면 2배 운용 생존이 어렵다고 본다)
  ③ BTC 2종 모두 거래당 순수익 X > 0
  ④ 알트 6종 중 4종 이상 1배 연복리 X > B
보고만: 건수·승률·거래당·연도별 · 1배/2배 연복리·MDD · 분해(필터 없음+SL6 · 필터 있음+SL6.5) · OKX 21-26 (선택에 쓴 데이터).
사전 예측 (틀려도 기록): ② 가 BitMEX 에서 떨어질 가능성이 높다 — 옛 tpsl.py IS(2018-21, 필터 없음 TP4/SL6)가 1배 MDD −68% 였다.
  ① 은 바이낸스에서 통과할 것(2020-21 급등기 거래 수 효과), BitMEX 는 2018·19 하락장 때문에 불확실.
사용: python research/aoa/nofilter.py   (결과 → research/aoa/nofilter_result.txt)
"""
import pathlib
import sys

import numpy as np
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[1]))
import exec_model as M  # noqa: E402
import features as F  # noqa: E402
import strategy as S  # noqa: E402
from clone import okx_5m  # noqa: E402

T = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731
ALTS = ["ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT", "ADAUSDT"]
ORIG, SL0 = S.indicators, S.SL
VARIANTS = [("X 필터없음·SL6.5", False, 6.5), ("B 현행(필터·SL6)", True, 6.0), ("분해 필터없음·SL6", False, 6.0), ("분해 필터·SL6.5", True, 6.5)]


def run(c, d1, lo, hi, filt, sl):
    S.indicators = (lambda c5, dd: ORIG(c5, dd)) if filt else (lambda c5, dd: (ORIG(c5, dd)[0], pd.Series(True, index=c5.index)))
    S.SL = sl
    try:
        tr = M.trades(c, d1, lo, hi, skip=1, tp=4.0)
    finally:
        S.indicators, S.SL = ORIG, SL0
    yrs = (min(hi, c.index[-1]) - lo).days / 365.25
    if len(tr) == 0:
        return dict(n=0, win=np.nan, ev=np.nan, c1=0.0, m1=0.0, c2=0.0, m2=0.0, ys="")
    r = pd.Series(np.asarray(M.net(tr, "B", 0.0, "lo", 0.0007, 0.0001), float))
    p1, m1, _, _ = M.curve(tr, r)
    p2, m2, _, _ = M.curve(tr.assign(mae=tr.mae * 2), r * 2)
    yr = pd.Series(r.values, index=tr.t0.dt.year.values).groupby(level=0)
    return dict(n=len(tr), win=(r > 0).mean(), ev=r.mean(), c1=(p1[-1] ** (1 / yrs) - 1), m1=m1,
                c2=(max(p2[-1], 1e-9) ** (1 / yrs) - 1), m2=m2,
                ys=" ".join(f"{y % 100:02d}:{len(v)}건{((1 + v).prod() - 1) * 100:+.0f}%" for y, v in yr))


def fmt(s):
    return (f"{s['n']:>4}건 승률 {s['win']:.0%} 거래당 {s['ev'] * 100:+.2f}% | 1배 연 {s['c1'] * 100:+.1f}% MDD {s['m1'] * 100:.0f}%"
            f" | 2배 연 {s['c2'] * 100:+.1f}% MDD {s['m2'] * 100:.0f}% | {s['ys']}") if s["n"] else "   0건"


if __name__ == "__main__":
    lines = []
    P = lambda *a: (print(*a, flush=True), lines.append(" ".join(map(str, a))))  # noqa: E731
    P(__doc__.split("\n")[0])
    bs = pd.read_pickle(F.ROOT / "data_cache" / "bitstamp_1d_2016.pkl").close
    sets = [("BitMEX 18-21", F.candles().loc["2017-12-01":"2022-01-10"], "2018-03-05", "2022-01-01", "BTC"),
            ("바이낸스 20-26", pd.read_pickle(F.ROOT / "data_cache" / "binance_btcusdt_5m_2020.pkl").asfreq("5min").ffill(),
             "2020-03-01", "2026-10-01", "BTC")]
    for sym in ALTS:
        c = pd.read_pickle(F.ROOT / "data_cache" / f"binance_{sym.lower()}_5m_2020.pkl").asfreq("5min").ffill()
        sets.append((sym, c, str((c.index[0] + pd.Timedelta("201D")).date()), "2026-10-01", "ALT"))
    sets.append(("OKX 21-26 (선택에 쓴 데이터·보고만)", okx_5m(), "2021-05-01", "2026-10-01", "OKX"))
    res = {}
    for name, c, d0, d9, kind in sets:
        own = c.close.resample("1D").last()
        d1 = pd.concat([bs[bs.index < own.index[0]], own]) if kind != "ALT" else own
        lo, hi = max(T(d0), c.index[0]), T(d9)
        P(f"\n[{name}] {lo:%Y-%m} ~")
        for lab, filt, sl in VARIANTS:
            res[(name, lab)] = s = run(c, d1, lo, hi, filt, sl)
            P(f"  {lab:14s} {fmt(s)}")
    btc = ["BitMEX 18-21", "바이낸스 20-26"]
    X, B = VARIANTS[0][0], VARIANTS[1][0]
    c1 = all(res[(n, X)]["c1"] > res[(n, B)]["c1"] for n in btc)
    c2 = all(res[(n, X)]["m2"] >= -0.60 for n in btc)
    c3 = all(res[(n, X)]["ev"] > 0 for n in btc)
    k4 = sum(res[(s, X)]["c1"] > res[(s, B)]["c1"] for s in ALTS); c4 = k4 >= 4
    ok = lambda b: "통과" if b else "불합격"  # noqa: E731
    t1 = ", ".join("%+.1f vs %+.1f" % (res[(n, X)]["c1"] * 100, res[(n, B)]["c1"] * 100) for n in btc)
    t2 = ", ".join("%.0f%%" % (res[(n, X)]["m2"] * 100) for n in btc)
    P(f"\n판정: ① BTC 1배 연복리 X>B {ok(c1)} ({t1}) · ② BTC 2배 MDD ≥ −60% {ok(c2)} ({t2})"
      f" · ③ BTC 거래당 > 0 {ok(c3)} · ④ 알트 {k4}/6 {ok(c4)}")
    P("  →", "X 채택 후보 (반영은 사용자 결정)" if all([c1, c2, c3, c4]) else "기각 — 현행 유지")
    (HERE / "nofilter_result.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
