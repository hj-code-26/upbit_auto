"""익절 폭 재측정 — 사전등록 (2026-09-28, 결과 보기 전 작성). 운영 코드·strategy.TP 는 건드리지 않는다.

질문: 지금 규칙(50·200일선 필터 · 진입 봉 건너뛰기 · 봉 마감 시장가 청산)에서 익절을 4% 보다 높이거나 낮추면 나아지나.
      옛 탐색(tpsl.py, 필터 전)은 TP 2·3·4·6 중 4 가 최선이었고 5·8 은 잰 적이 없다.

고정: 진입(5분 종가 1h EMA −2% & 전날 종가 50·200일선 위 → 다음 봉 시가 롱) · 손절 6% · 만기 864봉 · 비용 편도 0.07% · 펀딩 0.01%/8h · 1배.
후보 TP: 3 · 4(기준) · 5 · 6 · 8 %.  이 다섯 개 말고는 보지 않는다.
체결 모델 (exec_model.py 와 같은 코드):
  B2 = 현행 실봇 (진입 봉 건너뛰기 · 판정 봉이 닫힌 뒤 다음 봉 시가 시장가)   ← 주 판정
  A  = 공식 성적표 방식 (익절·손절가 체결 · 진입 봉부터 판정)               ← 체결 모델에 흔들리지 않는지 확인
데이터:
  BTC 3종 (dip_lev2 와 같은 구간·일봉 예열): BitMEX 2018-03~2021-12 · 바이낸스 2020-03~2026-09 · OKX 2021-05~2026-09
    ※ 바이낸스·OKX 는 같은 자산·겹치는 기간이라 독립이 아니다. 그래서 아래 ④ 알트를 따로 둔다.
  알트 6종 (바이낸스 현물 5분봉 2020-01~2026-09, 자체 일봉 — 첫 200일은 필터가 꺼져 거래 없음): ETH · SOL · XRP · BNB · DOGE · ADA
    ※ 알트 결과로 BTC 봇의 TP 를 고르는 게 아니라, BTC 에서 나온 차이가 '급락 매수 일반' 에서도 같은 방향인지만 본다.

채택 기준 (TP≠4 인 후보 X 마다, 넷 다 통과해야 한다):
  ① BTC 3종 모두 B2 거래당 순수익(X) > B2(4)
  ② BTC 3종 모두 B2 MDD(X) 가 B2 MDD(4) 보다 5%p 넘게 깊지 않다 (보유 중 저가 평가손 포함)
  ③ BTC 3종 모두 A 거래당 순수익(X) > A(4)     — 체결 가정을 바꿔도 우위가 남는가
  ④ 알트 6종 중 4종 이상에서 B2 거래당 순수익(X) > B2(4)
  둘 이상 통과하면: BTC 3종 B2 개선폭(X − 4)의 최솟값이 가장 큰 것. 동률이면 4 에 가까운 것.
  하나도 통과 못 하면: TP 4 유지.
  통과해도 '채택 후보' 까지다 — strategy.TP 변경은 사용자 결정. 후보가 5개라 우연히 하나가 통과할 여지가 있다.
보고만(판정 안 씀): 거래 수 · 익절/손절/만기 분해 · 승률 · 1배 연복리 · 상위 3거래 제외 거래당 · 연도별.
한계: 5분 OHLC 기반 (exec_model.py 의 한계 그대로). 알트는 현물 데이터라 펀딩이 실제와 다르다(같은 고정률 적용).
사용: python research/aoa/tp_sweep.py   (결과 → research/aoa/tp_sweep_result.txt)
"""
import pathlib
import sys

import numpy as np
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[1]))
import exec_model as M  # noqa: E402
import features as F  # noqa: E402
from clone import okx_5m  # noqa: E402

TPS = (3, 4, 5, 6, 8)
BASE = 4
ALTS = ["ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT", "ADAUSDT"]
COST, FUND = 0.0007, 0.0001


def measure(c, d1, lo, hi, tp, yrs):
    """→ {모델: (거래당, MDD, 거래 수, 사유, 승률, 연복리, 상위3 제외, 연도별)}"""
    out = {}
    for m, skip in (("A", 0), ("B2", 1)):
        tr = M.trades(c, d1, lo, hi, skip=skip, tp=tp)
        r = pd.Series(np.asarray(M.net(tr, m[0], 0.0, "lo", COST, FUND), float), index=tr.index)
        p, mdd, _, _ = M.curve(tr, r)
        yr = pd.Series(p, index=pd.DatetimeIndex(tr.t1)).groupby(tr.t1.dt.year.values).last()
        ys = " ".join(f"{y % 100:02d}:{(v / pv - 1) * 100:+.0f}" for (y, v), pv in zip(yr.items(), np.r_[1.0, yr.values[:-1]]))
        out[m] = dict(ev=r.mean(), mdd=mdd, n=len(tr), why=tr.why.value_counts().to_dict(), win=(r > 0).mean(),
                      cagr=(p[-1] ** (1 / yrs) - 1) * 100, top=r.sort_values().iloc[:-3].mean() if len(r) > 3 else np.nan, ys=ys)
    return out


if __name__ == "__main__":
    T = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731
    lines = []
    P = lambda *a: (print(*a, flush=True), lines.append(" ".join(map(str, a))))  # noqa: E731
    P(__doc__.split("\n")[0])
    bs = pd.read_pickle(F.ROOT / "data_cache" / "bitstamp_1d_2016.pkl").close
    btc = [("BitMEX 18-21", F.candles().loc["2018-01-01":"2022-01-10"], "2018-03-05", "2022-01-01"),
           ("바이낸스 20-26", pd.read_pickle(F.ROOT / "data_cache" / "binance_btcusdt_5m_2020.pkl").asfreq("5min").ffill(),
            "2020-03-01", "2026-10-01"),
           ("OKX 21-26", okx_5m(), "2021-05-01", "2026-10-01")]
    res = {}
    for name, c, lo, hi in btc:
        own = c.close.resample("1D").last()
        d1 = pd.concat([bs[bs.index < own.index[0]], own])
        yrs = (min(T(hi), c.index[-1]) - T(lo)).days / 365.25
        n_on = M.replay_check(c, d1, T(lo), T(hi), M.trades(c, d1, T(lo), T(hi), tp=BASE))   # TP4 · A 가 on_bar 재생과 거래 단위 일치
        P(f"\n[{name}] (TP4 · A = strategy.on_bar 재생 {n_on}건 일치)")
        for tp in TPS:
            res[(name, tp)] = m = measure(c, d1, T(lo), T(hi), tp, yrs)
            for k in ("B2", "A"):
                x = m[k]
                P(f"  TP{tp} {k:2s} {x['n']:>3}건 {x['why']} 승률 {x['win']:.0%} 거래당 {x['ev'] * 100:+.2f}% MDD {x['mdd'] * 100:.0f}%"
                  f" 연복리 {x['cagr']:+.1f}% 상위3 제외 {x['top'] * 100:+.2f}% | {x['ys']}")
    P("\n[알트 6종] B2 거래당 (A 거래당)")
    for sym in ALTS:
        c = pd.read_pickle(F.ROOT / "data_cache" / f"binance_{sym.lower()}_5m_2020.pkl").asfreq("5min").ffill()
        d1, lo, hi = c.close.resample("1D").last(), T("2020-01-01"), T("2026-10-01")
        yrs = (min(hi, c.index[-1]) - max(lo, c.index[0])).days / 365.25
        row = []
        for tp in TPS:
            res[(sym, tp)] = m = measure(c, d1, lo, hi, tp, yrs)
            row.append(f"TP{tp} {m['B2']['ev'] * 100:+.2f} ({m['A']['ev'] * 100:+.2f}) {m['B2']['n']}건")
        P(f"  {sym:9s} " + " · ".join(row))
    names = [b[0] for b in btc]
    P("\n판정 (기준 TP4)")
    passed = []
    for tp in (t for t in TPS if t != BASE):
        c1 = all(res[(n, tp)]["B2"]["ev"] > res[(n, BASE)]["B2"]["ev"] for n in names)
        c2 = all(res[(n, tp)]["B2"]["mdd"] >= res[(n, BASE)]["B2"]["mdd"] - 0.05 for n in names)
        c3 = all(res[(n, tp)]["A"]["ev"] > res[(n, BASE)]["A"]["ev"] for n in names)
        k4 = sum(res[(s, tp)]["B2"]["ev"] > res[(s, BASE)]["B2"]["ev"] for s in ALTS)
        c4 = k4 >= 4
        gain = min(res[(n, tp)]["B2"]["ev"] - res[(n, BASE)]["B2"]["ev"] for n in names)
        P(f"  TP{tp}: ① B2 거래당 {'통과' if c1 else '불합격'} · ② MDD {'통과' if c2 else '불합격'} · ③ A 거래당 {'통과' if c3 else '불합격'}"
          f" · ④ 알트 {k4}/6 {'통과' if c4 else '불합격'} · BTC 최소 개선 {gain * 100:+.2f}%p")
        if c1 and c2 and c3 and c4:
            passed.append((gain, -abs(tp - BASE), tp))
    best = max(passed)[2] if passed else None
    P(f"  → {'채택 후보 TP' + str(best) + ' (strategy.TP 변경은 사용자 결정)' if best else '통과 후보 없음 → TP4 유지'}")
    (HERE / "tp_sweep_result.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
