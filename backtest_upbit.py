"""관망형 다종목 현물 백테스트 (업비트 KRW). 봇에 넣을 파라미터를 여기서 확정한다.

확정 대상 (research_backtest_5y.txt 의 +677% 는 종목수·거래대금·실행가 가정이 기록돼 있지 않음):
  · MAX_POS   동시 보유 종목 수 (자산의 1/MAX_POS 씩 균등)
  · MIN_TV    20일 평균 거래대금 하한 — 소형 알트 슬리피지 회피
  · 실행가    close(신호 당일 종가) vs open(다음날 시가). 봇은 09:05 실행이라 open 이 진짜.
규칙: 매일 종가 신호 → 강세합류(bull≥7, bear<7) & p≥0.55 인 코인을 p 높은 순으로 빈 슬롯에 매수,
      5일 이상 보유하고 long 구역 아니면 매도. 현물이라 숏 없음(약세는 그냥 현금).
비용: 업비트 현물 0.05% + 슬리피지 0.1% (편도).
사용: python backtest_upbit.py fetch   업비트 전 종목 일봉 내려받기 (data_cache)
      python backtest_upbit.py         백테스트

분류기(p)·특징표는 원래 model.py 에 있었지만 봇이 쓰지 않아 여기로 옮겼다 (연구 전용).
"""
import itertools
import pathlib
import sys

import numpy as np
import pandas as pd
import pyupbit
from sklearn.ensemble import HistGradientBoostingClassifier

import model as M
from indicators import add_indicators

CACHE = pathlib.Path(__file__).resolve().parent / "data_cache"
TRAIN_DAYS, MIN_TRAIN_DAYS = 1900, 1000
HOLD_DAYS = 5                # 학습 목표 = 5일 뒤 상승 여부 (백테스트 최적)
L = 0.55                     # 롱 문턱
FEATS = ["ret_1d", "ret_5d", "ret_20d", "ret_60d", "rsi", "bbp", "macdh", "vol_ratio", "range_pct",
         "vol_5d", "from_hi20", "from_lo20", "stoch", "bull", "bear",
         "btc_ret_5d", "btc_ret_20d", "btc_bull", "btc_bear"]
COST = 0.0005 + 0.001        # 편도 수수료 + 슬리피지
RETRAIN = 182
WARMUP = 500                 # 첫 학습 확보용 (검증은 이 뒤부터)


def fetch():
    """업비트 KRW 전 종목 일봉을 data_cache 에 내려받는다 (walkforward 학습용 표본)."""
    CACHE.mkdir(exist_ok=True)
    frames = {}
    syms = pyupbit.get_tickers(fiat="KRW")
    for i, s in enumerate(syms):
        try:
            df = pyupbit.get_ohlcv(s, interval="day", count=TRAIN_DAYS)
            df = df[["open", "high", "low", "close", "volume"]].astype(float).iloc[:-1]   # 진행 중인 봉 제외
            df.to_pickle(CACHE / f"{s}_5y.pkl")
            if len(df) >= MIN_TRAIN_DAYS:
                frames[s] = df
        except Exception as e:  # noqa: BLE001
            print(f"{s} 실패: {e}", file=sys.stderr)
        if (i + 1) % 25 == 0:
            print(f"  {i + 1}/{len(syms)}", file=sys.stderr)
    print(f"내려받기 완료: 코인 {len(frames)}개 → {CACHE.name}/", file=sys.stderr)


def load_frames():
    frames = {}
    for f in sorted(CACHE.glob("*_5y.pkl")):
        d = pd.read_pickle(f)
        if len(d) >= MIN_TRAIN_DAYS:
            frames[f.name[:-7]] = d[["open", "high", "low", "close", "volume"]].astype(float)
    return frames


def feats(d):
    d = add_indicators(d.copy())
    c = d.close
    f = pd.DataFrame(index=d.index)
    for n in (1, 5, 20, 60):
        f[f"ret_{n}d"] = c.pct_change(n) * 100
    f["rsi"] = d["RSI_14"]
    f["bbp"] = d["BBP_20_2.0_2.0"]
    f["macdh"] = d["MACDh_12_26_9"] / c * 100
    f["vol_ratio"] = d.volume / d.volume.rolling(20).mean()
    f["range_pct"] = (d.high - d.low) / c * 100
    f["vol_5d"] = c.pct_change().rolling(5).std() * 100
    f["from_hi20"] = c / c.rolling(20).max() * 100 - 100
    f["from_lo20"] = c / c.rolling(20).min() * 100 - 100
    f["stoch"] = d["STOCHk_14_3_3"]
    f["bull"] = sum(b(d, f).astype(int) for _, b, _, _ in RULES)
    f["bear"] = sum(s(d, f).astype(int) for _, _, s, _ in RULES)
    f["trade_value_20d"] = (c * d.volume).rolling(20).mean()
    f["fwd"] = (c.shift(-HOLD_DAYS) / c - 1) * 100
    return f


def table(frames, btc="KRW-BTC"):
    """{symbol: ohlcv} → 특징 표 (BTC 국면 열 병합). btc = frames 안에서 BTC 를 가리키는 키."""
    a = pd.concat([feats(d).assign(symbol=s) for s, d in frames.items()]).rename_axis("date").reset_index()
    btc = a[a.symbol == btc][["date", "ret_5d", "ret_20d", "bull", "bear"]]
    btc.columns = ["date", "btc_ret_5d", "btc_ret_20d", "btc_bull", "btc_bear"]
    return a.merge(btc, on="date", how="left").dropna(subset=FEATS).sort_values("date")

def walkforward(a, seed=0):
    """합류 표본으로 6개월마다 재학습하며 검증 구간의 p 를 채운다 (미래 정보 차단)."""
    dates = np.array(sorted(a.date.unique()))
    a["p"] = np.nan
    for i in range(WARMUP, len(dates), RETRAIN):
        t0, t1 = dates[i], dates[min(i + RETRAIN, len(dates) - 1)]
        tr = a[(a.date < t0 - np.timedelta64(HOLD_DAYS, "D")) & a.fwd.notna()]
        tr = tr[(tr.bull >= M.CONF) | (tr.bear >= M.CONF)]
        if len(tr) < 2000:
            continue
        m = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.03, max_iter=300,
                                           min_samples_leaf=200, l2_regularization=1.0, random_state=seed)
        m.fit(tr[FEATS], (tr.fwd > 0).astype(int))
        te = a.index[(a.date >= t0) & (a.date < t1)]
        if len(te):
            a.loc[te, "p"] = m.predict_proba(a.loc[te, FEATS])[:, 1]
        print(f"  학습 {str(t0)[:10]} 표본 {len(tr):>6,} → 검증 {len(te):>6,}", file=sys.stderr)
    return a[a.date >= dates[WARMUP]]


def simulate(sig, px, max_pos):
    """sig: date×symbol 롱신호 p (아니면 NaN), px: date×symbol 체결가. → (자산곡선, 거래목록)"""
    cash, held, curve, trades = 1.0, {}, [], []
    for d in sig.index:
        pr = px.loc[d]
        eq = cash + sum(w * pr[s] / e for s, (e, _, w) in held.items())
        curve.append(eq)
        for s, v in list(held.items()):                                  # 보유일 +1, 매도 판정
            v[1] += 1
            if v[1] >= HOLD_DAYS and not sig.at[d, s] == sig.at[d, s]:
                cash += v[2] * pr[s] / v[0] * (1 - COST)
                trades.append(pr[s] / v[0] - 1 - 2 * COST)
                del held[s]
        cand = [s for s in sig.loc[d].dropna().sort_values(ascending=False).index if s not in held]
        for s in cand[:max_pos - len(held)]:
            size = min(cash, eq / max_pos)
            if size < eq * 0.01:
                break
            held[s] = [pr[s], 0, size * (1 - COST)]
            cash -= size
    return pd.Series(curve, index=sig.index), trades


def simulate_cohort(sig, px, hold=HOLD_DAYS):
    """원본 백테스트 방식: 신호가 뜬 코인을 전부 균등 보유, 정확히 hold 일 뒤 청산 (슬롯 제한 없음)."""
    w = pd.DataFrame(0.0, index=sig.index, columns=sig.columns)
    ent = sig.notna().astype(float)
    for k in range(hold):                       # d 에 열린 트레이드는 d..d+hold-1 동안 보유
        w += ent.shift(k).fillna(0)
    w = (w > 0).astype(float)
    n = w.sum(axis=1)
    r = px.pct_change().reindex_like(w).fillna(0).clip(-0.9, 3)
    port = (w.shift(1).fillna(0) * r).sum(axis=1) / w.shift(1).sum(axis=1).replace(0, np.nan)
    turn = (w - w.shift(1).fillna(0)).abs().sum(axis=1) / n.replace(0, np.nan)
    daily = (port.fillna(0) - (turn.fillna(0) * COST))
    trades = list((w.diff() < 0).sum(axis=1)[lambda x: x > 0].index)
    return (1 + daily).cumprod(), [0] * int(ent.sum().sum())


def report(name, c, trades):
    half = c.resample("2QE").last().pct_change().dropna() * 100
    print(f"{name:30s} {c.iloc[-1] / c.iloc[0] * 100 - 100:+8.0f}%  MDD {(c / c.cummax() - 1).min() * 100:6.1f}%  "
          f"거래 {len(trades):5d}회  승률 {np.mean([t > 0 for t in trades]) * 100 if trades else 0:4.1f}%  "
          f"최악반기 {half.min():+6.1f}%  음수반기 {(half < 0).sum()}/{len(half)}")


if __name__ == "__main__":
    if "fetch" in sys.argv:
        fetch()
        sys.exit()
    frames = load_frames()
    print(f"코인 {len(frames)}개", file=sys.stderr)
    a = table(frames).reset_index(drop=True)
    a = walkforward(a).copy()
    a["date"] = pd.to_datetime(a.date)
    keep = sorted(a.symbol.unique())
    px_c = pd.DataFrame({s: frames[s].close for s in keep})
    px_o = pd.DataFrame({s: frames[s].open for s in keep}).shift(-1)     # 신호 당일 종가 → 다음날 시가 체결
    tv = a.pivot_table(index="date", columns="symbol", values="trade_value_20d")
    days = pd.DatetimeIndex(sorted(a.date.unique()))                     # 신호 없는 날도 반드시 포함 (보유일 계산)
    sig0 = a[(a.bull >= M.CONF) & (a.bear < M.CONF) & (a.p >= L)].pivot_table(index="date", columns="symbol", values="p")
    sig0 = sig0.reindex(index=days, columns=keep)
    print(f"\n검증 {a.date.min():%Y-%m-%d}~{a.date.max():%Y-%m-%d}  롱신호 {int(sig0.notna().sum().sum()):,}건\n")
    for name, px in (("종가", px_c), ("다음시가", px_o)):
        px = px.reindex(index=sig0.index, columns=sig0.columns).ffill()
        for max_pos, min_tv in itertools.product((5, 10, 15, 20, 30), (3e9,)):
            s = sig0.where(tv.reindex_like(sig0) >= min_tv) if min_tv else sig0
            s = s.where(px.notna())
            report(f"{name} 슬롯{max_pos:2d} 거래대금≥{min_tv / 1e8:.0f}억", *simulate(s, px.ffill().bfill(), max_pos))
        report(f"{name} [기준] BTC 매수보유", pd.Series(px["KRW-BTC"].ffill().values, index=px.index).dropna(), [])
        for min_tv in (3e9,):
            s = sig0.where(tv.reindex_like(sig0) >= min_tv) if min_tv else sig0
            s = s.where(px.notna())
            report(f"{name} [원본]5일고정·전체 ≥{min_tv / 1e8:.0f}억", *simulate_cohort(s, px.ffill().bfill()))
