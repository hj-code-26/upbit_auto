"""과거 리플레이 (명세 검증 체크리스트 1번). OKX 1H 봉 2020-01-01~ → UTC 일봉 → rls.simulate.
연구 수치(증거금 2% 격리, 펀딩 0.01%/8h 가정, 수익 / MDD / Sharpe / 거래 / 강제청산 / 손절):
  BTC 10x +50.5% / 12.8% / 0.65 / 88 / 10 / 2     5x +37.1% / 7.6% / 0.75 / 88 / 1 / 7
  ETH 10x +99.1% / 23.9% / 0.80 / 76 / 13 / 0     5x +41.9% / 12.8% / 0.69 / 75 / 2 / 1
청산 건수는 ATR 방식 차이로 ±1~2건, 2024-10 이전은 last price 근사(OKX 실제는 mark price).
"10x B안" 은 검증 안 된 변형(손절 = min(3×ATR, 청산 거리 − 1.5%p)) — A안과 따로 본다.
1H 봉은 data_cache/ 에 저장하고 다음 실행 때 이어 받는다.
사용: python backtest.py               2020-01-01 부터
      python backtest.py 2023-01-01    그날부터 거래 (이전 일봉은 레짐 워밍업에만)
"""
import pathlib
import sys
import time

import numpy as np
import pandas as pd

import okx
import rls

CACHE = pathlib.Path(__file__).resolve().parent / "data_cache"
START = int(pd.Timestamp("2020-01-01").timestamp() * 1000)


def history(inst):
    CACHE.mkdir(exist_ok=True)
    f = CACHE / f"{inst}_1H.pkl"
    old = pd.read_pickle(f) if f.exists() else pd.DataFrame(columns=["ts", "confirm"])
    since = int(old.ts[old.confirm == 1].max()) + 1 if len(old) else START
    new = okx.Client().candles(inst, since)
    df = pd.concat([old[old.ts < since], new]).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    df.to_pickle(f)
    return df


def stats(eq, trades):
    r = eq.pct_change().dropna()
    ye = eq.groupby(eq.index.year).last()
    yearly = ye / ye.shift(fill_value=1.0) - 1
    return {"ret%": (eq.iloc[-1] - 1) * 100, "mdd%": (1 - eq / eq.cummax()).max() * 100,
            "sharpe": r.mean() / r.std() * np.sqrt(365) if r.std() else 0.0, "trades": len(trades),
            "win%": (trades.ret > 0).mean() * 100 if len(trades) else 0.0, "yearly": yearly,
            "liq": int((trades.why == "liq").sum()) if len(trades) else 0,
            "stop": int((trades.why == "stop").sum()) if len(trades) else 0}


if __name__ == "__main__":
    start = pd.Timestamp(sys.argv[1]) if len(sys.argv) > 1 else None
    for inst in ("BTC-USDT-SWAP", "ETH-USDT-SWAP"):
        t = time.time()
        d = rls.daily(history(inst))
        print(f"\n== {inst}  일봉 {len(d)}개 {d.index[0].date()}~{d.index[-1].date()}  ({time.time() - t:.0f}s)")
        for name, kw in [("10x A안", {}), ("10x B안", {"stop_mode": "B"}), ("10x 펀딩0", {"funding_8h": 0}),
                         ("5x", {"lever": 5}), ("R4 롱만", {"allow": (1,)})]:
            s = stats(*rls.simulate(d, start=start, **kw))
            print(f"  {name:9s} 수익 {s['ret%']:+6.1f}%  MDD {s['mdd%']:4.1f}%  Sharpe {s['sharpe']:.2f}  "
                  f"거래 {s['trades']}  청산 {s['liq']}  손절 {s['stop']}  승률 {s['win%']:.0f}%  연도별 "
                  + " ".join(f"{y}:{v * 100:+.1f}" for y, v in s["yearly"].items()))
        c = d.close[d.index >= start] if start is not None else d.close
        bh = c / c.iloc[0]
        print(f"  1x 매수보유 수익 {(bh.iloc[-1] - 1) * 100:+.0f}%  Sharpe {stats(bh, pd.DataFrame())['sharpe']:.2f}")
