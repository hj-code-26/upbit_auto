"""과거 리플레이 (명세 검증 체크리스트 1). OKX 1H 봉 2020-01-01~ → rls.simulate (종목별 독립 계좌) → 일수익 합산.
명세 수치(증거금 4% 격리 20x, BTC+ETH 합산): CAGR 30.9% / MDD 19.0% / Sharpe 1.15 / 최악 주 −7.8% / 강제청산 0,
거래 119 (BTC 60, ETH 59), 승률 38.7% (BTC 41.7%, ETH 35.6%). 연구 엔진은 SMA14 ATR 이라 'ATR=sma' 행이 이에 가깝다.
1H 봉은 data_cache/ 에 저장하고 다음 실행 때 이어 받는다.
사용: python backtest.py               2020-01-01 부터
      python backtest.py 2023-01-01    그날부터 거래 (이전 봉은 워밍업에만, 진행 중 레짐은 건너뜀)
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
INSTS = ("BTC-USDT-SWAP", "ETH-USDT-SWAP")


def history(inst):
    CACHE.mkdir(exist_ok=True)
    f = CACHE / f"{inst}_1H.pkl"
    old = pd.read_pickle(f) if f.exists() else pd.DataFrame(columns=["ts", "confirm"])
    since = int(old.ts[old.confirm == 1].max()) + 1 if len(old) else START
    new = okx.Client().candles(inst, since)
    df = pd.concat([old[old.ts < since], new]).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    df.to_pickle(f)
    return df


def stats(eq, trades=None):
    """eq: 일별 자산. 일별 Sharpe(√365), 최악 주, 연도별, 거래·승률·강제청산·최장 연패."""
    r = eq.pct_change().dropna()
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    ye = eq.groupby(eq.index.year).last()
    out = {"cagr%": ((eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1) * 100, "mdd%": (1 - eq / eq.cummax()).max() * 100,
           "sharpe": r.mean() / r.std() * np.sqrt(365) if r.std() else 0.0,
           "worst_wk%": eq.resample("W").last().pct_change().min() * 100,
           "yearly": ye / ye.shift(fill_value=eq.iloc[0]) - 1}
    if trades is not None and len(trades):
        loss = trades.pnl <= 0
        out.update(trades=len(trades), win=(~loss).mean() * 100, liq=int((trades.why == "liq").sum()),
                   streak=int(loss.groupby((~loss).cumsum()).sum().max()))
    return out


def line(name, s):
    t = (f"  거래 {s['trades']}  승률 {s['win']:.1f}%  청산 {s['liq']}  최장연패 {s['streak']}" if "trades" in s else "")
    return (f"  {name:18s} CAGR {s['cagr%']:+5.1f}%  MDD {s['mdd%']:4.1f}%  Sharpe {s['sharpe']:.2f}  "
            f"최악 주 {s['worst_wk%']:+.1f}%{t}\n{'':22s}" + " ".join(f"{y}:{v * 100:+.1f}" for y, v in s["yearly"].items()))


if __name__ == "__main__":
    start = pd.Timestamp(sys.argv[1]) if len(sys.argv) > 1 else None
    t = time.time()
    H = {i: history(i) for i in INSTS}
    print(f"1H 봉 {', '.join(f'{i[:3]} {len(h)}개' for i, h in H.items())} ~{pd.Timestamp(max(h.ts.max() for h in H.values()), unit='ms')} "
          f"({time.time() - t:.0f}s)")
    for name, kw in [("20x (명세, Wilder)", {}), ("20x ATR=sma", {"atr_kind": "sma"}), ("10x", {"lever": 10}),
                     ("7x", {"lever": 7}), ("20x 펀딩0", {"funding_8h": 0})]:
        R = {i: rls.simulate(h, start=start, **kw) for i, h in H.items()}
        print(line(name, stats(rls.portfolio([e for e, _ in R.values()]), pd.concat([tr for _, tr in R.values()]))))
        if not kw:
            for i, (e, tr) in R.items():
                s = stats(e.groupby(e.index.normalize()).last(), tr)
                print(f"      {i[:3]} 거래 {s['trades']}  승률 {s['win']:.1f}%  청산 {s['liq']}  최장연패 {s['streak']}  "
                      f"사유 {tr.why.value_counts().to_dict()}  손실 중앙 {tr.pnl_pct[tr.pnl <= 0].median() * 100:+.1f}%  "
                      f"이익 중앙 {tr.pnl_pct[tr.pnl > 0].median() * 100:+.1f}%")
    D = {i: rls.daily(h) for i, h in H.items()}
    print(line("RLS 5x 2% (FWD-v2)", stats(rls.portfolio([rls.simulate_rls5(d, start=start) for d in D.values()]))))
    for i, d in D.items():
        c = d.close[d.index >= start] if start is not None else d.close
        print(line(f"1x 매수보유 {i[:3]}", stats(c / c.iloc[0])))
