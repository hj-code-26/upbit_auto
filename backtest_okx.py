"""OKX BTC 무기한 · 봇 규칙(일봉 국면 + 4h 돌파) 과 단기 전략 비교 백테스트 (롱 전용). 결과: research_okx_short.txt
체결 = 신호 봉 종가 → 다음 봉 시가. 비용 편도 0.07% (테이커 0.05 + 슬리피지 0.02), 펀딩 0.01%/8h.
격리 청산 = 봉 저가가 진입가 × (1 − 1/lev) 이하면 자산 0.
사용: python backtest_okx.py [편도비용]     첫 실행은 OKX 공개 API 로 2021~ 봉을 받아 data_cache/okx_*.pkl 에 저장 (수 분)
"""
import sys
import time

import ccxt
import numpy as np
import pandas as pd

import model as M

def arg_cost(module_file, default=0.0007):
    """명령줄의 숫자 인자를 편도 비용으로 읽되, **그 파일을 직접 실행할 때만**.

    예전에는 import 만 해도 argv 를 읽었다. 그래서 `python backtest_bias.py 300` 의 300(시행 횟수)이
    편도 비용 30,000% 로 들어가 결과가 조용히 -100% 가 됐다 (2026-09-10 발견). 조용히 틀리는 것이 최악이라
    호출한 파일이 __main__ 일 때만 인자를 본다."""
    import __main__
    import os
    main_file = getattr(__main__, "__file__", "") or ""
    if os.path.basename(main_file) != os.path.basename(module_file):
        return default
    c = float(next((a for a in sys.argv[1:] if a.replace(".", "").isdigit()), default))
    assert 0 <= c < 0.05, f"편도 비용으로 읽은 값 {c} 가 상식 밖이다 (인자를 잘못 넘겼는가)"
    return c


COST = arg_cost(__file__)                              # 편도 비용 (이 파일을 직접 실행할 때 인자로 덮어쓰기)
START = "2021-03-01"
BPD = {"1d": 1, "4h": 6, "1h": 24}


def fetch(tf):
    f = M.CACHE / f"okx_{tf}.pkl"
    if f.exists():
        return pd.read_pickle(f)
    M.CACHE.mkdir(exist_ok=True)
    ex = ccxt.okx({"enableRateLimit": True, "options": {"defaultType": "swap"}})
    ms = {"1d": 86400, "4h": 14400, "1h": 3600}[tf] * 1000
    rows, since = [], ex.parse8601("2021-01-01T00:00:00Z")
    while since < ex.milliseconds():
        try:
            b = ex.fetch_ohlcv("BTC/USDT:USDT", tf, since=since, limit=100)
        except ccxt.BaseError as e:
            print(tf, e, file=sys.stderr); time.sleep(2); continue
        if not b:
            break
        rows += b; since = b[-1][0] + ms
    d = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).drop_duplicates("ts")
    d.index = pd.to_datetime(d.ts, unit="ms", utc=True)
    d = d.drop(columns="ts").astype(float).iloc[:-1]
    d.to_pickle(f)
    return d


def run(d, tf, entry, exit_, lev, name, entry_px=None):
    """entry/exit: bool 배열 (봉 i 종가 신호 → i+1 시가 체결). entry_px: 봉 안 체결가(변동성 돌파), None 이면 다음 시가."""
    assert 0 <= COST < 0.05, f"편도 비용 {COST} 가 상식 밖이다 (arg_cost 주석 참고)"
    o, h, l, c = d.open.values, d.high.values, d.low.values, d.close.values
    fund = 0.0001 * 3 / BPD[tf]
    eq, held, trades, curve, liq = 1.0, None, [], np.ones(len(d)), 0
    for i in range(1, len(d)):
        if held:
            e, k = held; k += 1
            if l[i] <= e * (1 - 1 / lev):
                eq, held, liq = 0, None, liq + 1; trades.append(-1.0)
            elif exit_[i - 1]:
                r = lev * (o[i] / e - 1) - 2 * lev * COST - k * fund * lev
                eq *= 1 + r; trades.append(r); held = None
            else:
                held = (e, k)
        if not held and eq > 0:
            if entry[i - 1]:
                held = (o[i], 0)
            elif entry_px is not None and not np.isnan(entry_px[i]) and h[i] >= entry_px[i]:
                held = (max(entry_px[i], o[i]), 0)
        curve[i] = eq * (1 + lev * (c[i] / held[0] - 1)) if held else eq
        if eq <= 0:
            curve[i:] = 0; break
    cv = pd.Series(curve, index=d.index)
    yr = cv.resample("YE").last(); yr = (yr / yr.shift(1).fillna(1) - 1) * 100
    tr = np.array(trades) if trades else np.zeros(1)
    print(f"{name:36s} x{lev}  누적 {cv.iloc[-1] * 100 - 100:+7.0f}%  MDD {(cv / cv.cummax() - 1).min() * 100:6.1f}%  "
          f"거래 {len(trades):4d} 승률 {(tr > 0).mean() * 100:3.0f}% 최악 {tr.min() * 100:6.1f}% 청산{liq} | "
          + " ".join(f"{y:+.0f}" for y in yr.values))


def bull(d):
    x = M.add_indicators(d.copy()); f = pd.DataFrame(index=d.index)
    f["ret_5d"], f["ret_20d"] = d.close.pct_change(5) * 100, d.close.pct_change(20) * 100
    return sum(b(x, f).astype(int) for _, b, _, _ in M.RULES)


def strategies(D):
    d = D["1d"]; o, h, l, c = d.open, d.high, d.low, d.close
    ones = np.ones(len(d), bool)
    yield "1d", d, "변동성돌파 k=0.5 (익일시가 청산)", None, ones, (o + 0.5 * (h - l).shift(1)).values
    yield "1d", d, "전일 +2% 이상이면 1일 보유", (c.pct_change() > 0.02).values, ones, None
    yield "1d", d, "20일 고점돌파 → 10일 저점이탈", (c > h.rolling(20).max().shift(1)).values, (c < l.rolling(10).min().shift(1)).values, None
    bd = bull(d)
    yield "1d", d, "[이전 봇] 일봉 bull>=7 동안 보유", (bd >= M.CONF).values, (bd < M.CONF).values, None
    regime = (bd >= M.CONF)                                     # align_daily 로 4h 에 붙인다 (아래)
    for tf in ("4h", "1h"):
        d = D[tf]; h, l, c = d.high, d.low, d.close; n = BPD[tf] * 2
        b = bull(d)
        yield tf, d, f"{tf} bull>=7 동안 보유", (b >= M.CONF).values, (b < M.CONF).values, None
        yield tf, d, f"{tf} {n}봉 고점돌파 → {n // 2}봉 저점이탈", (c > h.rolling(n).max().shift(1)).values, (c < l.rolling(n // 2).min().shift(1)).values, None
        k = BPD[tf] // 6 or 1
        burst = (c.pct_change(k) > 0.02)
        yield tf, d, f"{tf} {k}봉 +2% 급등 → {BPD[tf]}봉 보유", burst.values, ~burst.rolling(BPD[tf]).max().fillna(0).astype(bool).values, None
    d = D["4h"]; h, l, c = d.high, d.low, d.close
    rg = M.align_daily(regime, d.index).fillna(False).astype(bool).values   # 판단 시각 = 4h 봉 종료 (2026-09-10)
    for n, m in ((M.H4_N, M.H4_M), (30, 15), (6, 3)):
        tag = "[봇] " if n == M.H4_N else ""
        yield "4h", d, f"{tag}일봉bull>=7 & 4h {n}봉 돌파 → {m}봉 이탈", (c > h.rolling(n).max().shift(1)).values & rg, (c < l.rolling(m).min().shift(1)).values | ~rg, None


if __name__ == "__main__":
    D = {tf: fetch(tf) for tf in ("1d", "4h", "1h")}
    c = D["1d"][D["1d"].index >= START].close
    print(f"비용 편도 {COST * 100:.2f}%  |  연도별: 2021 2022 2023 2024 2025 2026")
    print(f"[기준] BTC 상시보유  누적 {(c.iloc[-1] / c.iloc[0] - 1) * 100:+.0f}%  MDD {(c / c.cummax() - 1).min() * 100:.1f}%\n")
    for tf, d, name, en, ex, px in strategies(D):
        d = d[d.index >= START]; n = len(d)
        en = np.zeros(n, bool) if en is None else en[-n:]; ex = ex[-n:]; px = None if px is None else px[-n:]
        for lev in (1, 2, 3):
            run(d, tf, en, ex, lev, name, px)
        print()
