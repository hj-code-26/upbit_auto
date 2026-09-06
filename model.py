"""관망형 신호 모델.

근거 (research_backtest_5y.txt, 업비트 KRW 102코인 × 5년, 6개월마다 재학습 워크포워드):
  · 지표 8개가 7개 이상 같은 방향을 가리키는 '합류' 날만 이후 수익률이 양수 (bull≥7: 5일 뒤 +0.75~+1.29%, 나머지 전부 음수)
  · 합류만으로 사면 −98%. 합류 표본으로 학습한 확률 p 로 거른 것만 살아남음 (p≥0.65: 5일 뒤 상승 64.6%, 평균 +3.8%)
  · 관망형(강세 합류 & p≥0.55 만 롱, 아니면 현금): 5일 보유 +677% / MDD −27%, 반기 블록 8개 모두 ≥ 0
  · 숏 레그 수익은 소형 알트 숏이라 현물에서 실행 불가 → 약세 합류는 '청산·진입 금지' 로만 쓴다

  · BTC 단일로 돌리면 (같은 모델·같은 규칙, 롱/숏 모두 실행): 1배 +92% / MDD −23%, 롱 자리 5일 뒤 적중 62.5%,
    5일 거래 최악 −20% → 레버리지 5배면 그 한 번에 청산. 3배 MDD −58%, 2배 −42%.

역할: 업비트 전 종목 5년 일봉으로 학습(표본 확보) → 대상 코인의 p(다음 HOLD_DAYS 일 상승 확률), bull/bear 합류 점수, 구역.
      학습·매매 모두 업비트 KRW 일봉이므로 봉을 그대로 쓴다.
사용: python model.py          (학습: 전 종목 5년 일봉 재수집 후 model.pkl 저장, 10분 안팎)
"""
import datetime as dt
import pathlib
import pickle
import sys

import pandas as pd
import pyupbit
from sklearn.ensemble import HistGradientBoostingClassifier

from indicators import add_indicators

ROOT = pathlib.Path(__file__).resolve().parent
CACHE = ROOT / "data_cache"
MODEL_PATH = ROOT / "model.pkl"
TRAIN_DAYS, MIN_TRAIN_DAYS = 1900, 1000
RETRAIN_DAYS = 182
HOLD_DAYS = 5                # 학습 목표 = 5일 뒤 상승 여부 (백테스트 최적)
CONF = 7                     # 8개 지표 중 7개 이상 겹치면 합류
L, S = 0.55, 0.40            # 롱 문턱 / 숏 문턱. 숏만 0.40 인 이유: 손실 거래 분석(2026-09-06)에서
                             # 최악 12건 중 11건이 숏이었고 p 0.40~0.45 숏은 적중 43%·평균 −0.5%. p≤0.40 으로 제한하면
                             # 앞 절반 MDD −14%→−6%, 최악 −10.5%→−5.6%, 뒤 절반 MDD 동일(−6%)·수익 −8%p.
                             # 손절선(−5%)은 저가 스파이크에 걸려 손실을 키웠음 → 진입 필터만 쓴다.
FEATS = ["ret_1d", "ret_5d", "ret_20d", "ret_60d", "rsi", "bbp", "macdh", "vol_ratio", "range_pct",
         "vol_5d", "from_hi20", "from_lo20", "stoch", "bull", "bear",
         "btc_ret_5d", "btc_ret_20d", "btc_bull", "btc_bear"]


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
    bull = [c > d.SMA_20, d.SMA_20 > d.SMA_50, c > d.EMA_10, d["MACDh_12_26_9"] > 0,
            d.RSI_14 > 55, d["BBP_20_2.0_2.0"] > 0.5, f.ret_20d > 0, f.ret_5d > 0]
    bear = [c < d.SMA_20, d.SMA_20 < d.SMA_50, c < d.EMA_10, d["MACDh_12_26_9"] < 0,
            d.RSI_14 < 45, d["BBP_20_2.0_2.0"] < 0.5, f.ret_20d < 0, f.ret_5d < 0]
    f["bull"] = sum(x.astype(int) for x in bull)
    f["bear"] = sum(x.astype(int) for x in bear)
    f["trade_value_20d"] = (c * d.volume).rolling(20).mean()
    f["fwd"] = (c.shift(-HOLD_DAYS) / c - 1) * 100
    return f


def table(frames, btc="KRW-BTC"):
    """{symbol: ohlcv} → 특징 표 (BTC 국면 열 병합). btc = frames 안에서 BTC 를 가리키는 키."""
    a = pd.concat([feats(d).assign(symbol=s) for s, d in frames.items()]).rename_axis("date").reset_index()
    btc = a[a.symbol == btc][["date", "ret_5d", "ret_20d", "bull", "bear"]]
    btc.columns = ["date", "btc_ret_5d", "btc_ret_20d", "btc_bull", "btc_bear"]
    return a.merge(btc, on="date", how="left").dropna(subset=FEATS).sort_values("date")


def zone(row):
    if row.bull >= CONF and row.bear < CONF and row.p >= L:
        return "long"
    if row.bear >= CONF and row.p <= S:
        return "short"
    return "wait"


def train():
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
    a = table(frames).dropna(subset=["fwd"])
    a = a[(a.bull >= CONF) | (a.bear >= CONF)]                 # 합류 표본만 학습 (백테스트 채택안)
    m = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.03, max_iter=300,
                                       min_samples_leaf=200, l2_regularization=1.0)
    m.fit(a[FEATS], (a.fwd > 0).astype(int))
    pickle.dump({"model": m, "trained_at": dt.date.today(), "coins": len(frames), "rows": len(a)},
                MODEL_PATH.open("wb"))
    print(f"학습 완료: 코인 {len(frames)}개, 합류 표본 {len(a):,}개 → {MODEL_PATH.name}")
    return m


def load():
    """model.pkl 이 없거나 RETRAIN_DAYS 지났으면 재학습."""
    if MODEL_PATH.exists():
        obj = pickle.load(MODEL_PATH.open("rb"))
        if (dt.date.today() - obj["trained_at"]).days < RETRAIN_DAYS:
            return obj["model"]
    return train()


def predict(m, df):
    """대상 코인 일봉(진행 중 봉 제외) → 마지막 완성봉의 {date, p, bull, bear, zone, ret_5d, ret_20d, rsi}."""
    last = table({"BTC": df}, btc="BTC").iloc[-1]
    last["p"] = float(m.predict_proba(last[FEATS].to_frame().T)[0, 1])
    last["zone"] = zone(last)
    return last[["date", "p", "bull", "bear", "zone", "ret_5d", "ret_20d", "rsi"]]


if __name__ == "__main__":
    train()
