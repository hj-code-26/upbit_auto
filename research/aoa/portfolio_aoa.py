"""aoa 비중 포트폴리오 — 사전등록 (2026-09-26). 비중은 그의 2018~21 거래금액 비중에서 정했다(수익률과 무관).

주: BTC 85% · ETH 15% (전체 기간 BTC 84.7 · ETH 14.1 · 나머지 1.2% 는 버림)   참고: BTC 75 · ETH 25 (2021 비중)
규칙·기간·평가는 portfolio.py 와 동일 (롱만 2% 급락 · TP4/SL6 · 3일 · 비용 0.07% · 펀딩 · 바이낸스 5분봉 2020-01~2026-09, 1시간 저가 MDD).
진입 명목 = 실현자본 × 비중 × L. BTC 포지션 중엔 BTC 몫만 쓰고 나머지는 현금으로 대기(그의 운용과 같다).
채택 기준 (주 비중 · 50일선 필터 B · 1배): portfolio.py 의 ①~④ 와 동일 — 특히 ③ BTC 단독 B(수익/위험 1.01, MDD −17%)보다 우위.
"""
import pathlib
import sys

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from portfolio import HI, LO, coin_trades, load, simulate, stats, yearly  # noqa: E402

MAIN, ALT = {"BTCUSDT": 0.85, "ETHUSDT": 0.15}, {"BTCUSDT": 0.75, "ETHUSDT": 0.25}

if __name__ == "__main__":
    data = {s: load(s) for s in MAIN}
    lows = {s: c.low.resample("1h").min() for s, c in data.items()}
    T = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731
    res = {}
    for var, filt in (("A 필터없음", False), ("B 50일선", True)):
        tr = {s: coin_trades(c, filt) for s, c in data.items()}
        for wn, w in (("BTC85·ETH15", MAIN), ("BTC75·ETH25", ALT)):
            for L in (1, 2, 3):
                res[(var, wn, L)] = simulate(tr, lows, L, weights=w)[0]
        res[(var, "BTC 단독", 1)] = simulate({"BTCUSDT": tr["BTCUSDT"]}, lows, 1, n_slots=1)[0]
        res[(var, "ETH 단독", 1)] = simulate({"ETHUSDT": tr["ETHUSDT"]}, lows, 1, n_slots=1)[0]
        rnd = [simulate({s: coin_trades(c, filt, rand_n=len(tr[s]), seed=k) for s, c in data.items()}, lows, 1, weights=MAIN)[0]
               for k in range(10)]
        res[(var, "무작위(85·15)", 1)] = pd.concat(rnd, axis=1).mean(axis=1)
    print(f"{'':30s}{'누적':>9}{'연복리':>8}{'MDD':>7}{'수익/위험':>9} | 전반(20-22) 후반(23-26) | 연도별")
    for k, eq in res.items():
        tot, cagr, mdd = stats(eq)
        a, b = stats(eq, LO, T("2023-01-01"))[0], stats(eq, T("2023-01-01"), HI)[0]
        print(f"{k[0]:9s}{k[1]:14s}{k[2]}배 {tot:>+9,.0f}%{cagr:>+7.0f}%{mdd:>6.0f}%{cagr / abs(mdd):>9.2f} | {a:>+8.0f}% {b:>+9.0f}% | {yearly(eq).to_dict()}")
    eq = res[("B 50일선", "BTC85·ETH15", 1)]
    tot, cagr, mdd = stats(eq); a, b = stats(eq, LO, T("2023-01-01"))[0], stats(eq, T("2023-01-01"), HI)[0]
    yr = yearly(eq); bt, bc, bm = stats(res[("B 50일선", "BTC 단독", 1)]); rt = stats(res[("B 50일선", "무작위(85·15)", 1)])[0]
    c = [tot > 0 and a > 0 and b > 0, (yr > 0).sum() >= 5, cagr / abs(mdd) > bc / abs(bm) and mdd > bm, tot > rt]
    print(f"\n판정(B · BTC85·ETH15 · 1배): ① {c[0]} ② 양수연도 {(yr > 0).sum()}/{len(yr)} {c[1]} ③ BTC단독보다 수익/위험·MDD 우위 {c[2]} ④ 무작위 우위 {c[3]}")
    print("  →", "채택 후보" if all(c) else "기각")
