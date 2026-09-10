# BTC 자동매매 봇 알고리즘 검증 요청

당신은 **퀀트 트레이딩 시스템 감사자**다. 아래는 실제로 **실계좌 자금이 들어가 있는** BTC 자동매매 봇의
전체 알고리즘과 소스다. 이 시스템이 **깨져 있거나, 백테스트가 거짓말을 하고 있거나, 계좌를 날릴 구멍이
있는지** 찾아 달라. 칭찬은 필요 없다. 틀린 곳만 말해 달라.

## 답해 주었으면 하는 것 (우선순위 순)

1. **파산 위험** — 이 설정(고정 5배, 격리 마진, 자산 100% 명목 투입)으로 장기 생존 가능한가?
   백테스트가 놓친 꼬리 위험(갭·급락·거래소 장애·펀딩·슬리피지)은 무엇인가?
2. **백테스트 무결성** — 미래 정보 누수(lookahead), 생존 편향, 과최적화가 있는가?
   특히 `backtest_quant.py`/`backtest_both.py` 의 신호 생성(`frames`)과 체결 모델(`simulate`)을 봐 달라.
   봉 i 종가 신호 → i+1 시가 체결, 일봉 국면은 하루 shift — 이게 실제 봇 동작과 일치하는가?
3. **코드 버그** — 백테스트와 실봇의 로직 불일치, 상태기계 결함, 재시작·중복실행·부분체결 시 깨지는 곳.
4. **통계적 유효성** — 5.5년 표본 108거래 · 승률 34% 로 "우위가 있다"고 말할 수 있는가?
   탐색/검증 분할(2021-03~2024 / 2024~)이 충분한가? 파라미터(CONF=6, 12봉/6봉)는 과최적화인가?
5. **LLM 게이트의 타당성** — 주문 직전 Claude 에게 거부권을 준 설계(`instructions.md`)가
   기대값을 올리는가 낮추는가? 비결정적 요소를 매매 경로에 넣은 것 자체가 문제인가?

각 지적은 **어느 파일 어느 부분인지**와 **왜 손실로 이어지는지**를 함께 적어 달라.
문제가 없다고 판단되는 항목은 "문제 없음" 한 줄로 끝내도 좋다.

---

## 1. 전략 요약

- 시장: OKX BTC-USDT 무기한 선물, 격리 마진, 단방향(net), 테이커 0.05%
- 데이터: 일봉(UTC) + 4시간봉. **진행 중인 봉은 항상 버리고 완성봉만 사용**
- 국면 판정: 마지막 완성 일봉에 8개 규칙 적용 → 강세 `CONF`(=6)개 이상이면 롱 국면,
  약세 6개 이상이면 숏 국면 (동시 성립 불가)
- 진입: 롱 국면 & 4h 종가 > 직전 12봉 고가 → 롱 자리
        숏 국면 & 4h 종가 < 직전 12봉 저가 → 숏 자리 (**현재 ALLOW_SHORT=0 으로 꺼짐**)
- 청산: 롱은 4h 종가 < 직전 6봉 저가 또는 롱 국면 붕괴 / 숏은 거울
- 진입 타이밍: 신호 후 바로 사지 않는다. 돌파당한 고가를 하한으로 두고 **1분봉이 그 선을 지키거나
  되찾을 때** 매수. 다음 4h 봉 마감까지 못 지키면 자리 포기
- 사이징: 명목 = 자산 × POSITION_PCT(100%) × 레버리지. 현재 **고정 5배** (변동성 타겟팅 꺼짐)
- 안전장치: 레버리지 코드 상한 5배, 24h 고점 대비 −40% 면 청산 후 자동실행 정지
- 실행 주기: 10분마다 1회 판단 (open / close / hold)
- 주문 직전 LLM(Claude)에게 거부권 부여. 호출 실패 = 거부

## 2. 주장하고 있는 백테스트 성적 (검증해 달라)

- 표본: OKX BTC 4h, 2021-03-01 ~ 2026-09-09 (5.5년), 편도 비용 0.07%, 펀딩 0.01%/8h
- 탐색 2021-03~2024-01 / 검증 2024-01~
- CONF=6, 1배: 누적 +135% / MDD −23% / 거래 108회 / 승률 34% / 최악 거래 −5.8%
- 3배: +492% / MDD −52%. 분봉 하한 재확보 진입 적용 시 +1,156% / −35.2%
- 고정 5배: +2,359% / MDD −59%, 104건 전부 청산 없이 생존. **7배부터 12번째 거래에서 전액 청산**
- 진입 후 최악 역행 −14.7% → 이론적 청산 상한 6.8배 (여유 1.4배뿐)

## 3. 현재 운영 설정 (.env, 비밀키 제거)

```
MODE=live            # 실주문
OKX_DEMO=0           # 실계좌
SYMBOL=BTC/USDT:USDT
POSITION_PCT=100
LEVERAGE=5           # 고정 5배 (코드 상한도 5)
VOL_TARGET_PCT=0     # 변동성 타겟팅 꺼짐
VOL_WINDOW=40
CONF=6
MAX_DAY_LOSS_PCT=40
ALLOW_SHORT=0
EXTREME_MIN=0
ENTRY_FLOOR=1        # (기본값, .env 에 없음)
INTERVAL_MIN=10
CLAUDE_MODEL=claude/claude-opus-5
```

> 참고: 문서(README)는 아직 "3배 상한 + 변동성 타겟 40%" 로 적혀 있으나 실제 .env 는 **고정 5배 · 타겟 꺼짐**이다.
> 이 선택(누적 +1,027%/MDD −22%/Sharpe 1.36 → +2,359%/MDD −59%/Sharpe 0.79)이 합리적인지도 평가해 달라.

---

# 소스 코드

## `model.py`

```python
"""신호 모델: 일봉 강세 국면 + 4h 돌파 (2026-09-07, backtest_okx.py · research_okx_short.txt).

봇이 쓰는 것은 signal() 뿐이다:
용어 (2026-09-10 정리): **롱 = 상승에 거는 것, 숏 = 하락에 거는 것.** 청산은 zone 이 아니라 exit_long/exit_short 다.
  (예전에는 zone="short" 가 '청산·진입 금지' 를 뜻해 숏 포지션과 이름이 겹쳤다. 그 값은 이제 안 쓴다.)
  · 롱 국면 = 마지막 완성 일봉 8개 규칙 중 CONF 개 이상 강세  · 숏 국면 = CONF 개 이상 약세 (둘은 동시에 참일 수 없다)
  · 롱 진입 = 롱 국면 & 4h 종가 > 직전 H4_N(12)봉 고가   · 롱 청산 = 4h 종가 < 직전 H4_M(6)봉 저가 or 롱 국면 붕괴
  · 숏 진입 = 숏 국면 & 4h 종가 < 직전 H4_N(12)봉 저가   · 숏 청산 = 4h 종가 > 직전 H4_M(6)봉 고가 or 숏 국면 붕괴
  OKX 2021-03~2026-09: 1배 +117% / MDD −21% (일봉 국면만 보유 +100% / −41%, 상시 보유 +62% / −77%).
  단기 지표 단독(변동성 돌파·1h/4h 급등·EMA 교차)은 비용 편도 0.07% 에서 전부 음수 → research_okx_short.txt.

옛 분류기(확률 p)와 그 특징표는 봇이 쓴 적이 없고(research_bot_rules.txt: p 문턱은 노이즈)
유일한 사용처인 backtest_upbit.py 로 옮겼다. 그래서 여기엔 sklearn·pyupbit·model.pkl 이 없다.
사용: python model.py     자체 점검
"""
import os
import pathlib

import pandas as pd
from dotenv import load_dotenv

import reversion as R
from indicators import add_indicators

load_dotenv()

ROOT = pathlib.Path(__file__).resolve().parent
CACHE = ROOT / "data_cache"             # backtest_okx.py 가 캔들을 캐시하는 곳
CONF = int(os.environ.get("CONF", 7))   # 8개 지표 중 CONF 개 이상 겹치면 합류. .env 의 CONF 로 진입 엄격도를 조절한다.
                             # OKX BTC 2021-03~2026-09, 3배, 편도 0.07%: 7 → 누적 +445% / MDD −50% / 거래 85회 · 승률 36%
                             #                                          8 → 누적 +117% / MDD −43% / 거래 43회 · 승률 33%
                             # 8 은 '확실할 때만' 사는 대신 기회와 수익이 줄어든다 (backtest_okx.py 로 재측정 가능)
H4_N, H4_M = 12, 6           # 4h 돌파(직전 12봉 고가) / 이탈(직전 6봉 저가). 이웃값 30/15·6/3 도 양수 (research_okx_short.txt)


# 합류 규칙 8개: (이름, 강세 조건, 약세 조건, 표시값). rules_at() 과 대시보드 explain() 이 같은 목록을 쓴다.
RULES = [
    ("종가 vs 20일 이평", lambda d, f: d.close > d.SMA_20, lambda d, f: d.close < d.SMA_20,
     lambda d, f: f"{d.close:,.0f} / {d.SMA_20:,.0f}"),
    ("20일 이평 vs 50일 이평", lambda d, f: d.SMA_20 > d.SMA_50, lambda d, f: d.SMA_20 < d.SMA_50,
     lambda d, f: f"{d.SMA_20:,.0f} / {d.SMA_50:,.0f}"),
    ("종가 vs 10일 지수이평", lambda d, f: d.close > d.EMA_10, lambda d, f: d.close < d.EMA_10,
     lambda d, f: f"{d.close:,.0f} / {d.EMA_10:,.0f}"),
    ("MACD 히스토그램", lambda d, f: d["MACDh_12_26_9"] > 0, lambda d, f: d["MACDh_12_26_9"] < 0,
     lambda d, f: f"{d['MACDh_12_26_9']:,.0f}"),
    ("RSI(14) >55 / <45", lambda d, f: d.RSI_14 > 55, lambda d, f: d.RSI_14 < 45, lambda d, f: f"{d.RSI_14:.1f}"),
    ("볼린저 위치 >0.5 / <0.5", lambda d, f: d["BBP_20_2.0_2.0"] > 0.5, lambda d, f: d["BBP_20_2.0_2.0"] < 0.5,
     lambda d, f: f"{d['BBP_20_2.0_2.0']:.2f}"),
    ("20일 수익률", lambda d, f: f.ret_20d > 0, lambda d, f: f.ret_20d < 0, lambda d, f: f"{f.ret_20d:+.1f}%"),
    ("5일 수익률", lambda d, f: f.ret_5d > 0, lambda d, f: f.ret_5d < 0, lambda d, f: f"{f.ret_5d:+.1f}%"),
]


def rules_at(df):
    """완성봉 df 의 마지막 봉에 규칙 8개 적용 → (rows[{name, bull, bear, value}], bull, bear)."""
    d = add_indicators(df.copy()).iloc[-1]
    c = df.close
    f = pd.Series({"ret_5d": c.iloc[-1] / c.iloc[-6] * 100 - 100, "ret_20d": c.iloc[-1] / c.iloc[-21] * 100 - 100})
    rows = [{"name": n, "bull": bool(b(d, f)), "bear": bool(s(d, f)), "value": v(d, f)} for n, b, s, v in RULES]
    return rows, sum(r["bull"] for r in rows), sum(r["bear"] for r in rows), f


def signal(daily, h4):
    """daily·h4 = 완성봉만 (진행 중 봉 제외). → dict

    zone  = 이번 봉이 가리키는 **진입 방향**: long(상승 베팅) · short(하락 베팅) · wait(자리 없음).
    청산   = zone 이 아니라 exit_long / exit_short 를 본다. 들고 있는 쪽 값만 보면 된다.
    진입선 = 롱은 hi(직전 12봉 고가), 숏은 lo_n(직전 12봉 저가). 이탈선 = 롱은 lo(6봉 저가), 숏은 hi_m(6봉 고가).
    롱 국면과 숏 국면은 동시에 참일 수 없다 (bull+bear ≤ 8 이고 CONF ≥ 5 이므로)."""
    rules, bull, bear, f = rules_at(daily)
    bull_regime, bear_regime = bull >= CONF, bear >= CONF
    hi_s, lo_s = R.scores(h4)                       # 4h 극단 점수 (reversion.py) — 진입 필터용, 판정 자체는 안 바꾼다
    last, prev = h4.iloc[-1], h4.iloc[-H4_N - 1:-1]
    hi, lo_n = float(prev.high.max()), float(prev.low.min())
    lo, hi_m = float(prev.low.tail(H4_M).min()), float(prev.high.tail(H4_M).max())
    breakout, breakdown = last.close > hi, last.close < lo_n
    zone = "long" if bull_regime and breakout else "short" if bear_regime and breakdown else "wait"
    return {"date": daily.index[-1].strftime("%Y-%m-%d"), "bar": h4.index[-1].strftime("%Y-%m-%d %H:%M"),
            "bull": bull, "bear": bear, "regime": bull_regime, "bull_regime": bull_regime, "bear_regime": bear_regime,
            "breakout": bool(breakout), "breakdown": bool(breakdown), "zone": zone,
            "exit_long": bool(last.close < lo or not bull_regime), "exit_short": bool(last.close > hi_m or not bear_regime),
            "close": float(last.close), "hi": hi, "lo": lo, "lo_n": lo_n, "hi_m": hi_m,
            "extreme_hi": int(hi_s.iloc[-1]), "extreme_lo": int(lo_s.iloc[-1]), "extreme_rules": len(R.RULES),
            "conf": CONF, "h4_n": H4_N, "h4_m": H4_M,
            "ret_5d": round(float(f.ret_5d), 1), "ret_20d": round(float(f.ret_20d), 1), "rules": rules}


def entry_level(sig):
    """진입 대기 중 분봉으로 지키는지 볼 선. 롱은 돌파당한 고가, 숏은 이탈당한 저가."""
    return sig["hi"] if sig["zone"] == "long" else sig["lo_n"]


if __name__ == "__main__":
    import numpy as np                                      # 자체 점검: 합성 봉으로 돌파·이탈·국면 판정
    idx = pd.date_range("2024-01-01", periods=80, freq="D", tz="UTC")
    up = pd.DataFrame({"close": np.linspace(100, 180, 80)}, index=idx)
    up["open"], up["high"], up["low"], up["volume"] = up.close, up.close + 1, up.close - 1, 1.0
    # 4h 봉 14개: 앞쪽(0~6)을 넓게 잡아 12봉 선(105/95)과 6봉 선(101/99)이 서로 다르게 만든다
    h = pd.DataFrame({"open": [100.0] * 14, "high": [105.0] * 7 + [101.0] * 7,
                      "low": [95.0] * 7 + [99.0] * 7, "close": [100.0] * 14, "volume": 1.0},
                     index=pd.date_range("2024-03-20", periods=14, freq="4h", tz="UTC"))
    dn = up.assign(close=up.close[::-1].values)                       # 계속 내리는 일봉 → 숏 국면
    dn["open"], dn["high"], dn["low"] = dn.close, dn.close + 1, dn.close - 1
    at = lambda df, c: signal(df, h.assign(close=[*h.close[:-1], float(c)]))
    s = at(up, 100)
    assert (s["hi"], s["lo_n"], s["lo"], s["hi_m"]) == (105, 95, 99, 101), "진입선·이탈선이 12봉/6봉으로 갈린다"
    assert s["zone"] == "wait" and s["bull_regime"] and not s["bear_regime"], "롱 국면인데 돌파 없음 → 자리 없음"
    assert not s["exit_long"], "롱 국면 유지 & 이탈선 위 → 롱 청산 아님"
    assert at(up, 106)["zone"] == "long", "롱 국면 & 12봉 고가(105) 돌파 → 롱"
    assert at(up, 104)["zone"] == "wait", "12봉 고가를 못 넘으면 롱 자리가 아니다"
    assert at(up, 98)["zone"] == "wait" and at(up, 98)["exit_long"], "롱 국면의 하방 이탈은 숏 자리가 아니라 롱 청산"
    assert not at(up, 98)["breakdown"], "6봉 저가(99)는 깼지만 12봉 저가(95)는 아직"
    assert at(dn, 94)["zone"] == "short", "숏 국면 & 12봉 저가(95) 이탈 → 숏"
    assert at(dn, 96)["zone"] == "wait", "12봉 저가를 못 깨면 숏 자리가 아니다"
    assert at(dn, 100)["exit_long"] and not at(dn, 100)["exit_short"], "숏 국면이면 롱은 청산, 숏은 유지"
    assert at(dn, 102)["exit_short"], "숏 보유 중 6봉 고가(101) 돌파 → 숏 청산"
    assert at(up, 100)["exit_short"], "롱 국면이면 숏은 청산"
    assert not (at(up, 100)["bull_regime"] and at(up, 100)["bear_regime"]), "롱 국면과 숏 국면은 동시에 참일 수 없다"
    assert entry_level(at(up, 106)) == 105 and entry_level(at(dn, 94)) == 95, "진입 대기선은 돌파당한 그 선"
    print("ok")
```

## `indicators.py`

```python
"""pandas_ta 대체 (pandas 3 / py3.11 에 설치 불가). 열 이름은 원본(pandas_ta)과 같게 맞춘다."""
import pandas as pd


def add_indicators(df):
    c = df["close"]
    for n in (10, 20, 50):
        df[f"SMA_{n}"] = c.rolling(n).mean()
    df["EMA_10"] = c.ewm(span=10, adjust=False).mean()
    d = c.diff()
    up, dn = d.clip(lower=0), -d.clip(upper=0)
    rs = up.ewm(alpha=1 / 14, adjust=False).mean() / dn.ewm(alpha=1 / 14, adjust=False).mean()
    df["RSI_14"] = 100 - 100 / (1 + rs)
    lo, hi = df["low"].rolling(14).min(), df["high"].rolling(14).max()
    k = ((c - lo) / (hi - lo) * 100).rolling(3).mean()
    df["STOCHk_14_3_3"], df["STOCHd_14_3_3"] = k, k.rolling(3).mean()
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    sig = macd.ewm(span=9, adjust=False).mean()
    df["MACD_12_26_9"], df["MACDs_12_26_9"], df["MACDh_12_26_9"] = macd, sig, macd - sig
    m, s = c.rolling(20).mean(), c.rolling(20).std(ddof=0)
    df["BBL_20_2.0_2.0"], df["BBM_20_2.0_2.0"], df["BBU_20_2.0_2.0"] = m - 2 * s, m, m + 2 * s
    df["BBP_20_2.0_2.0"] = (c - (m - 2 * s)) / (4 * s)
    return df


if __name__ == "__main__":
    import numpy as np
    x = pd.DataFrame({"close": np.arange(1, 61, dtype=float)})
    x["high"], x["low"] = x.close + 1, x.close - 1
    r = add_indicators(x).iloc[-1]
    assert abs(r["SMA_20"] - 50.5) < 1e-9 and r["RSI_14"] > 99 and 0 < r["BBP_20_2.0_2.0"] <= 1.01
    print("ok")
```

## `autotrade.py`

```python
"""BTC 단일 · 일봉 국면 + 4h 돌파 자동매매 봇 (OKX USDT 무기한 선물, 격리 마진).

용어: **롱 = 상승에 거는 것, 숏 = 하락에 거는 것.** 청산은 방향이 아니라 행동이다 (예전 zone="short" 는 청산이었다).
자리 (model.signal(): 마지막 완성 일봉 + 마지막 완성 4h 봉):
  long  = 일봉 강세 CONF개↑ & 4h 종가가 직전 12봉 고가 돌파   → 롱 진입
  short = 일봉 약세 CONF개↑ & 4h 종가가 직전 12봉 저가 이탈   → 숏 진입 (ALLOW_SHORT=1 일 때만)
  wait  = 그 외.  청산은 들고 있는 쪽의 exit_long / exit_short 로 본다
숏은 기본 꺼짐(ALLOW_SHORT=0)이다 — research_both_sides.txt: 숏만 1배 −72%, 롱+숏 1배 −34% (롱만 +135%).
매 사이클(INTERVAL_MIN, 기본 10분) 판단은 open / close / hold 셋 중 하나이고, open·close 는 그 사이클 안에서 바로 주문한다.
포지션 상태기계 (flat / long / short) — 한 번에 한 방향만. 방향 전환은 청산 후 다음 사이클부터:
  flat : long·short 자리 → Claude 검토 → 승인되면 '진입 대기' (같은 4h 자리로 Claude 를 부르는 것은 한 번뿐)
         진입 대기 중에는 매 사이클 1분봉으로 하한(=돌파당한 고가)이 지켜지는지 보고, 지켜지면 그때 매수.
         다음 4h 봉이 닫힐 때까지 못 지키면 자리 포기 (ENTRY_FLOOR=0 이면 예전처럼 신호 즉시 매수)
         근거: research_entry_timing.txt — 3배 즉시매수 +492%/MDD −38.9% → 하한 확인 +1,156%/−35.2% (2026-09-09 재측정)
  long/short : 그 방향의 청산 조건(이탈선 or 국면 붕괴) → 즉시 청산, 그 외 → 보유
사이징: 명목 = 자산 × POSITION_PCT × lev. lev 는 변동성 타겟팅이 정한다 (VOL_TARGET_PCT, 상한 LEVERAGE):
  계좌 일별 실현변동성이 목표를 넘으면 그만큼 깎고, 올리지는 않는다. 이력이 VOL_WINDOW/2 일보다 짧으면 대기.
  근거: research_quant_transfer.txt — 하한 재확보 진입 기준 MDD −42.5%→−22.7%, Sharpe(검증) 0.80→1.11.
  진입 직전 okx.setup() 이 격리 마진·레버리지(정수로 올림)를 거래소에 설정한다.
  모의 장부는 손실이 증거금(명목/LEVERAGE)에 닿으면 강제청산으로 흉내 낸다. 최소 주문 10 USDT.

모의 3단계:
  1) 모의 장부(paper)  : 이 파일 안의 state 표. 거래소 호출 없음. 어느 모드에서든 항상 기록된다
  2) OKX 데모(OKX_DEMO=1, 기본) : '실주문' 이 OKX 모의투자 서버로 나간다. 가짜 돈, 진짜 체결·청산·수수료·레버리지
  3) 실계좌(OKX_DEMO=0) : 진짜 돈. 시작할 때 '실주문' 을 타이핑해야만 뜬다
모의·실주문 연계 (MODE):
  paper : 모의 장부만. 키가 있으면 실계좌 잔고는 읽어서 기록·표시만 한다 (주문 없음)
  live  : 매 주문을 거래소(데모 또는 실계좌)에 보냄
  auto  : 모의 장부로 시작 → 모의 거래(진입→청산) LIVE_AFTER_PAPER_TRADES 회 완료되면 그 다음 주문부터 거래소 주문   (기본)
사고 차단기 (MAX_DAY_LOSS_PCT, 기본 40%): 24시간 고점 대비 그만큼 빠지면 신호와 무관하게 청산하고 자동실행을 끈다.
  전략 MDD(3배 −50%) 를 잡는 값이 아니라 버그·급변을 잡는 값이다. 재개는 사람이 make on.
레버리지 상한: MAX_LEVERAGE(5배). .env 가 넘기면 시작하지 않는다. 7배부터는 백테스트에서 전액 청산이다.
Claude 검토 (CLAUDE_BASE_URL, OmniRoute 경유): **주문을 내기 직전**에만 호출한다 — 자리가 났을 때가 아니라
  분봉이 진입선을 지켜 체결이 확정된 순간이다 (2026-09-10 이동. 그전에는 최대 4시간 전에 물어봤다).
  알고리즘 값을 캔들과 대조하고 사건·급변을 본다. 거부·실패면 그 자리를 접는다. 한 자리에 호출은 한 번뿐.
자동실행 온오프: autorun.off 파일이 있으면 스케줄러가 판단을 건너뛴다 (make off / make on, 대시보드 버튼). 실행 중에도 바로 반영.

사용:  python autotrade.py --once     1회 실행
       python autotrade.py            INTERVAL_MIN 분마다 실행 (기본 60)
       python dashboard.py            대시보드 http://localhost:8000
"""
import contextlib
import datetime as dt
import json
import logging
import math
import os
import pathlib
import socket
import sqlite3
import statistics
import sys
import time
import zoneinfo

import anthropic
from dotenv import load_dotenv

import okx as X
import model as M

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parent
DB_PATH = ROOT / "trading.db"
KST = zoneinfo.ZoneInfo("Asia/Seoul")
AUTORUN_OFF = ROOT / "autorun.off"                       # 있으면 자동실행 꺼짐
MODE = os.environ.get("MODE", "auto")                    # paper | live | auto
LIVE_AFTER = int(os.environ.get("LIVE_AFTER_PAPER_TRADES", 2))
HAVE_KEYS = all(os.environ.get(k) for k in ("OKX_API_KEY", "OKX_SECRET", "OKX_PASSPHRASE"))
PAPER_CASH = float(os.environ.get("PAPER_CASH", 1000))   # 모의 장부 시작 자산 (USDT)
INTERVAL_MIN = int(os.environ.get("INTERVAL_MIN", 10))    # 사이클 간격(분). 4h 봉이 닫히면 늦어도 이 안에 잡는다.
                          # 10분이 적정 — backtest_latency.py: 1~30분은 차이가 노이즈 폭(검증 Sharpe 1.19~1.29)이고
                          # 60분부터 뚜렷하게 나빠진다(1.11). 놓치는 자리가 4→8건으로 늘어서다. 청산 지연 비용은 ±1bp 로 무시 가능
POSITION_PCT = float(os.environ.get("POSITION_PCT", 100)) / 100
LEVERAGE = float(os.environ.get("LEVERAGE", 1))           # 모의 장부·거래소 공통 (okx.setup 이 진입 직전 설정)
MAX_LEVERAGE = 5                    # 코드가 막는 상한 (2026-09-10 3→5, 사용자 결정).
                          # backtest_current.py --lev: 고정 5배까지 104건 전부 생존(+2,359%/MDD −59%),
                          # **7배부터 12번째 거래에서 전액 청산**. 진입 후 최악 역행 −14.7% → 이론 상한 6.8배.
                          # 여유가 1.4배뿐이다. 표본에 없던 폭락일(−20%↑)이 한 번 오면 5배도 끝난다
if LEVERAGE > MAX_LEVERAGE:                               # LLM 검토를 마지막 방어선으로 두지 않는다
    sys.exit(f".env 의 LEVERAGE={LEVERAGE:g} 가 상한 {MAX_LEVERAGE}배를 넘습니다. 낮추고 다시 실행하세요.")
VOL_TARGET_PCT = float(os.environ.get("VOL_TARGET_PCT", 40))   # 변동성 타겟. 0 = 끔 (레버리지를 LEVERAGE 로 고정)
VOL_WINDOW = int(os.environ.get("VOL_WINDOW", 40))             # 실현 변동성 창 (일). 절반(20일)이 쌓여야 켜진다
MAX_DAY_LOSS = float(os.environ.get("MAX_DAY_LOSS_PCT", 40))   # 24h 고점 대비 이만큼 빠지면 청산 후 자동실행 정지. 0 = 끄기
                          # 2026-09-10 30→40: 고정 5배의 정상 24h 낙폭이 −29.9% 라 30 이면 평범한 손실 거래에 걸린다.
                          # 걸리면 최악의 자리에서 청산하고 사람이 make on 할 때까지 회복 구간을 통째로 놓친다
ALLOW_SHORT = os.environ.get("ALLOW_SHORT", "0") != "0"   # 숏(하락 베팅) 진입 허용. 기본 꺼짐 — 백테스트가 음수다
EXTREME_MIN = int(os.environ.get("EXTREME_MIN", 0))      # 진입 시 요구하는 4h 극단 점수 하한 (0 = 끔).
                                                          # research_reversion.txt: OKX 표본에선 2 가 낫고, 10년 Bitstamp 표본에선 차이 없음
ENTRY_FLOOR = os.environ.get("ENTRY_FLOOR", "1") != "0"   # 1: 하한(돌파선) 방어를 분봉으로 확인한 뒤 매수. 0: 예전처럼 신호 즉시 매수
CANDLES, H4_CANDLES = 100, 60                             # 일봉(50일선) · 4h 봉(직전 12봉 고가 + 극단 지표 20봉 여유)
CLAUDE_URL = os.environ.get("CLAUDE_BASE_URL")             # Anthropic 호환 게이트웨이(OmniRoute). 비우면 Claude 검토 생략
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "auto/claude-opus")
USE_CLAUDE = bool(CLAUDE_URL)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout),
                              logging.FileHandler(ROOT / "autotrade.log", encoding="utf-8")])
log = logging.getLogger("autotrade")

# ---------- DB ----------
@contextlib.contextmanager
def db():
    """호출마다 커넥션을 열고 반드시 닫는다 (닫지 않으면 매 사이클 핸들이 쌓인다)."""
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, mode TEXT,
        equity REAL, paper_equity REAL, price REAL, zone TEXT, p REAL, bull INTEGER, bear INTEGER, position TEXT,
        action TEXT, reason TEXT, status TEXT, model TEXT, input_tokens INTEGER, output_tokens INTEGER);
    CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, timestamp TEXT, mode TEXT,
        action TEXT, side TEXT, qty REAL, notional REAL, price REAL, order_id TEXT, status TEXT, reason TEXT);
    CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK (id = 1), side TEXT, qty REAL, entry REAL,
        entered_at TEXT, cash REAL);""")
    for sql in ("ALTER TABLE runs ADD COLUMN real_equity REAL", "ALTER TABLE state ADD COLUMN seen_bar TEXT",
                "ALTER TABLE state ADD COLUMN watch_bar TEXT", "ALTER TABLE state ADD COLUMN watch_hi REAL",
                "ALTER TABLE state ADD COLUMN watch_since TEXT", "ALTER TABLE state ADD COLUMN lev REAL", "ALTER TABLE state ADD COLUMN watch_side TEXT"):
        with contextlib.suppress(sqlite3.OperationalError):    # 기존 DB 에 열 추가 (이미 있으면 에러 → 무시)
            conn.execute(sql)
    conn.execute("INSERT OR IGNORE INTO state (id, side, qty, entry, entered_at, cash) VALUES (1, NULL, 0, 0, NULL, ?)", (PAPER_CASH,))
    conn.commit()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def now():
    return dt.datetime.now(KST).isoformat(timespec="seconds")


def state():
    with db() as c:
        cols = "side, qty, entry, entered_at, cash, seen_bar, watch_bar, watch_hi, watch_since, lev, watch_side"
        row = c.execute(f"SELECT {cols} FROM state").fetchone()
    return dict(zip(cols.split(", "), row))


def set_state(**kw):
    with db() as c:
        c.execute(f"UPDATE state SET {', '.join(f'{k}=?' for k in kw)} WHERE id=1", list(kw.values()))


def paper_trades_done():
    """완료된 모의 거래(청산) 횟수 — auto 모드의 실주문 전환 기준."""
    with db() as c:
        return c.execute("SELECT COUNT(*) FROM orders WHERE action='close' AND mode='paper' AND status='paper'").fetchone()[0]


def live_now():
    return HAVE_KEYS and (MODE == "live" or (MODE == "auto" and paper_trades_done() >= LIVE_AFTER))


def autorun():
    return not AUTORUN_OFF.exists()


def set_autorun(on):
    AUTORUN_OFF.unlink(missing_ok=True) if on else AUTORUN_OFF.touch()
    log.info("자동실행 %s", "ON" if on else "OFF")
    return autorun()


# ---------- 계좌 (모의 장부는 항상, 실계좌는 live 일 때) ----------
def account(ex, px):
    """{equity, pos, paper_equity, paper_pos}. pos = {side, qty, entry, pnl, entered_at, held_days, pnl_pct} | None"""
    st = state()
    paper_pos = None
    if st["side"]:
        sgn = 1 if st["side"] == "long" else -1
        paper_pos = {"side": st["side"], "qty": st["qty"], "entry": st["entry"], "lev": st["lev"],
                     "pnl": sgn * (px - st["entry"]) * st["qty"]}
    paper_eq = st["cash"] + (paper_pos["pnl"] if paper_pos else 0)
    if ex:
        snap = X.snapshot(ex)                                          # 잔고·시세를 한 번에 (폴링 비용 절감)
        eq, pos = snap["equity"], snap["position"]
        if pos and (pos["side"] != st["side"] or not st["entered_at"]):   # 봇 밖에서 연 포지션 → 모의 장부도 맞추고 오늘 진입으로
            log.warning("봇 밖에서 연 포지션을 장부에 흡수합니다 (%s %.6f @%s) — 이후 4h 이탈·국면 붕괴 때 봇이 청산합니다",
                        pos["side"], pos["qty"], f"{pos['entry']:,.1f}")
            set_state(side=pos["side"], qty=pos["qty"], entry=pos["entry"], entered_at=now())
            st = state()
        elif pos:                                                          # 시장가 체결 수량·평단을 거래소 값으로 보정
            set_state(qty=pos["qty"], entry=pos["entry"])
        if not pos and st["side"]:                                         # 거래소는 비었는데 장부에 포지션 → 장부 정리
            set_state(side=None, qty=0, entry=0, entered_at=None)
            st, paper_pos = state(), None
    else:
        eq, pos = paper_eq, paper_pos
    if pos:
        pos["entered_at"] = st["entered_at"]
        pos["held_days"] = (dt.datetime.now(KST) - dt.datetime.fromisoformat(st["entered_at"])).days
        pos["pnl_pct"] = pos["pnl"] / eq * 100 if eq else 0.0
    return {"equity": eq, "pos": pos, "paper_equity": paper_eq, "paper_pos": paper_pos}


def liquidated(pos):
    """격리 증거금 = 진입 명목 / 진입 당시 레버리지. 손실이 그만큼이면 강제청산."""
    # ponytail: 모의 장부 전용 근사(유지증거금·펀딩비 무시). 거래소 포지션은 OKX 가 직접 청산한다.
    # 거래소는 X.setup 이 건 정수 레버리지로 판정하므로 실제 청산선은 이보다 멀다 — 모의 쪽이 보수적이다
    lev = pos.get("lev") or LEVERAGE
    return lev > 1 and pos["pnl"] <= -pos["entry"] * pos["qty"] / lev


def day_loss_hit(eq, live=False):
    """24시간 고점 대비 MAX_DAY_LOSS% 아래면 True. 전략 MDD(5배 −62%)를 잡으려는 값이 아니라,
    버그·급변으로 하루 만에 무너지는 경우를 잡는 값이다. 걸리면 청산하고 자동실행을 끈다 (재개는 사람이 make on).

    고점은 지금 재는 것과 같은 계열에서만 찾는다. runs.equity 는 모의면 장부(≈1,000), 실주문이면 실계좌(≈7)라
    한 열에 두 축척이 섞여 있어서, 모드가 바뀐 직후 고점이 −99% 로 읽히고 차단기가 영영 걸린다
    (2026-09-08 실제 발생: 모의 998 → 실계좌 7.28). paper_equity·real_equity 는 축척이 하나뿐이다."""
    if not MAX_DAY_LOSS or not eq:
        return False
    col = "real_equity" if live else "paper_equity"
    since = (dt.datetime.now(KST) - dt.timedelta(days=1)).isoformat(timespec="seconds")
    with db() as c:
        peak = c.execute(f"SELECT MAX({col}) FROM runs WHERE {col} IS NOT NULL AND timestamp > ?", (since,)).fetchone()[0]
    return bool(peak) and eq <= peak * (1 - MAX_DAY_LOSS / 100)


def target_leverage(live):
    """변동성 타겟팅 → (이번 진입에 쓸 레버리지, 사유). 실현 변동성이 목표를 넘는 만큼 깎는다. 올리지는 않는다.

    lev = min(LEVERAGE, VOL_TARGET_PCT / 계좌 일별 실현변동성(연율))
    quant_nasq100 의 exposure_cap() 을 레버리지 쪽으로 옮긴 것. 자산 축척이 섞이지 않게 day_loss_hit 과
    같은 열을 본다. 이력이 VOL_WINDOW/2 일보다 짧거나 변동성이 0(계속 쉬었다)이면 기능 대기 — LEVERAGE 그대로.
    검증 (research_quant_transfer.txt, 실전 경로=하한 재확보 진입, 타겟 40%·창 40일, 2021-03~2026-09):
      누적 +1,156%→+1,027%, MDD −42.5%→−22.4%, Sharpe 탐색 1.05→1.43 · 검증 0.80→1.29.
      타겟 20~60% × 창 30~120일 25조합 전부에서 MDD 개선 · 검증 Sharpe 개선 (기준선 0.80).
      창 40일이 30·60·90·120 보다 낫고, 절반인 20일만 쌓이면 켜져서 실전에서 더 빨리 붙는다."""
    if VOL_TARGET_PCT <= 0:
        return LEVERAGE, ""
    col = "real_equity" if live else "paper_equity"
    since = (dt.datetime.now(KST) - dt.timedelta(days=VOL_WINDOW)).isoformat(timespec="seconds")
    with db() as c:                                  # 날짜별 마지막 값 = 그 날 종료 자산 (SQLite 의 MAX + bare column)
        rows = c.execute(f"SELECT MAX(timestamp), {col} FROM runs WHERE {col} > 0 AND timestamp > ? "
                         f"GROUP BY substr(timestamp, 1, 10) ORDER BY 1", (since,)).fetchall()
    need = max(2, VOL_WINDOW // 2)
    if len(rows) < need:
        return LEVERAGE, f"변동성 타겟 대기 (자산 이력 {len(rows)}/{need}일) → {LEVERAGE:g}배"
    v = [r[1] for r in rows]
    rets = [b / a - 1 for a, b in zip(v, v[1:]) if a]
    vol = statistics.stdev(rets) * (365 ** 0.5) * 100 if len(rets) > 1 else 0     # 코인은 24/7 → 365
    if vol <= 0:
        return LEVERAGE, f"변동성 타겟 대기 (실현 변동성 0) → {LEVERAGE:g}배"
    lev = min(LEVERAGE, VOL_TARGET_PCT / vol)
    return lev, f"변동성 타겟 실현 {vol:.0f}% / 목표 {VOL_TARGET_PCT:.0f}% → {lev:.2f}배"


def real_equity(ex):
    """거래소 실제 자산 (USDT). 모의 모드여도 키가 있으면 읽기만 한다. 조회 실패는 None."""
    try:
        return X.snapshot(ex or X.client())["equity"] if HAVE_KEYS else None
    except Exception as e:  # noqa: BLE001
        log.warning("실계좌 조회 실패: %s", X.explain(e))
        return None


# ---------- 하한 방어 (진입 타이밍) ----------
def floor_check(pub, level, since, side="long"):
    """돌파당한 선이 분봉에서 지켜지는가 → (진입해도 되는가, 사유).
    롱은 돌파당한 고가를 **하한**으로 지켜야 하고, 숏은 이탈당한 저가를 **상한**으로 눌러야 한다 (거울).

    backtest_entry.py (2026-09-08, OKX 2021-03~2026-09, 이벤트 107건):
      · 하한을 한 번도 잃지 않았거나 잃었다가 되찾았을 때만 사면 3배 +1,286% / MDD −34.8% (즉시 매수 +558% / −38.9%)
      · 창 안에 되찾지 못해 건너뛴 4건은 전부 손실 거래였다
      · CONF 6/7/8 × 파라미터 6종 × 전후반 = 54개 조합 전부에서 즉시 매수보다 좋았다 (backtest_entry.py sweep)
    '싸게 사려고 기다리는' 규칙이 아니다 — 지정가·되돌림 매수는 전부 즉시 매수보다 나빴다. 이건 자리가 무너졌는지 보는 규칙이다."""
    m = X.candles(pub, 300, "1m").iloc[:-1]                     # 진행 중인 분봉 제외
    m = m[m.index >= since]
    if m.empty:
        return False, "분봉 없음 → 다음 사이클에 다시 확인"
    last = float(m.close.iloc[-1])
    if side == "long":
        if float(m.low.min()) > level:
            return True, f"{len(m)}분간 하한 {level:,.0f} 유지 (최저 {m.low.min():,.0f})"
        if last > level:
            return True, f"하한 {level:,.0f} 이탈 후 재확보 (분봉 종가 {last:,.0f})"
        return False, f"하한 {level:,.0f} 아래 (분봉 종가 {last:,.0f}, 최저 {m.low.min():,.0f}) → 대기"
    if float(m.high.max()) < level:
        return True, f"{len(m)}분간 상한 {level:,.0f} 유지 (최고 {m.high.max():,.0f})"
    if last < level:
        return True, f"상한 {level:,.0f} 회복 후 재이탈 (분봉 종가 {last:,.0f})"
    return False, f"상한 {level:,.0f} 위 (분봉 종가 {last:,.0f}, 최고 {m.high.max():,.0f}) → 대기"


# ---------- Claude 검토 (매수 직전) ----------
def review_payload(sig, daily, h4, px, acc, notional, pub=None, lev=None, side="long"):
    """알고리즘이 계산한 값 + 그 근거 캔들. Claude 는 둘이 맞아떨어지는지, 사건·급변이 없는지 본다 (instructions.md).
    m1 = 신호 이후 분봉. 봇은 이 분봉으로 하한(돌파선) 방어를 확인하고 사므로 Claude 도 같은 것을 본다."""
    d = M.add_indicators(daily.copy()).tail(15)
    m1 = []
    if pub is not None and ENTRY_FLOOR:
        with contextlib.suppress(Exception):                     # 분봉 조회 실패가 검토 자체를 막지는 않는다
            m = X.candles(pub, 90, "1m").iloc[:-1].tail(60)
            m1 = [{"time": t.strftime("%H:%M"), "open": r.open, "high": r.high, "low": r.low, "close": r.close}
                  for t, r in m.iterrows()]
    return {"analysis": {k: v for k, v in sig.items() if k not in ("ret_5d", "ret_20d")},
            "proposal": {"action": "open", "side": side, "notional_usdt": round(notional, 2), "leverage": LEVERAGE if lev is None else round(lev, 2),
                         "equity_usdt": round(acc["equity"], 2), "price": px},
            "market": {"today_pct": round(px / daily.close.iloc[-1] * 100 - 100, 2), "ret_5d": sig["ret_5d"], "ret_20d": sig["ret_20d"]},
            "daily": [{"date": t.strftime("%m-%d"), "open": r.open, "high": r.high, "low": r.low, "close": r.close,
                       "chg_pct": round(r.close / r.open * 100 - 100, 1), "vol_btc": round(r.volume), "rsi": round(r.RSI_14),
                       "sma20": round(r.SMA_20), "sma50": round(r.SMA_50)} for t, r in d.iterrows()],
            "h4": [{"time": t.strftime("%m-%d %H:%M"), "open": r.open, "high": r.high, "low": r.low, "close": r.close}
                   for t, r in h4.tail(M.H4_N + 1).iterrows()],
            "entry": {"floor": M.entry_level(sig), "side": side,
                      "rule": ("롱: 돌파당한 고가(floor)를 분봉이 하한으로 지키거나 되찾을 때만 진입" if side == "long" else
                               "숏: 이탈당한 저가(floor)를 분봉이 상한으로 누르거나 다시 잃을 때만 진입")
                      + ", 다음 4h 봉 마감까지 못 지키면 자리 포기",
                      "m1": m1}}


def ask_claude(payload):
    client = anthropic.Anthropic(base_url=CLAUDE_URL, api_key=os.environ.get("ANTHROPIC_API_KEY", "omniroute"),
                                 timeout=90, max_retries=1)
    user = "\n\n".join(f"## {k}\n{json.dumps(v, ensure_ascii=False)}" for k, v in payload.items())
    resp = client.messages.create(model=CLAUDE_MODEL, max_tokens=1000, system=(ROOT / "instructions.md").read_text(encoding="utf-8"),
                                  messages=[{"role": "user", "content": user}])
    text = "".join(b.text for b in resp.content if b.type == "text")
    out, _ = json.JSONDecoder().raw_decode(text[text.find("{"):])
    return {"approve": bool(out["approve"]), "reason": str(out.get("reason", ""))[:300], "model": resp.model,
            "input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}


def claude_gate(payload):
    """매수 직전 Claude 검토. 호출이 실패하면 거부로 본다 — 확인 못 한 매수는 내지 않는다."""
    try:
        v = ask_claude(payload)
    except Exception as e:  # noqa: BLE001
        log.error("Claude 검토 실패: %s", e)
        v = {"approve": False, "reason": f"검토 실패 ({e})"[:200], "model": CLAUDE_MODEL, "input_tokens": 0, "output_tokens": 0}
    log.info("Claude(%s): approve=%s — %s", v["model"], v["approve"], v["reason"])
    return v


# ---------- 주문 ----------
def execute(ex, run_id, action, side, qty, notional, px, reason, demo=False, lev=None):
    """action: open|close. 모의 장부는 항상 정산하고, ex 가 있으면 실주문도 보낸다.
    demo=True (대시보드 데모 주문) 는 별도 계좌라서 모의 장부를 건드리지 않는다."""
    mode = "demo" if demo else "live" if ex else "paper"
    row = {"run_id": run_id, "timestamp": now(), "mode": mode, "action": action, "side": side, "qty": qty,
           "notional": notional, "price": px, "reason": reason}
    try:
        st, fee = state(), notional * X.TAKER_FEE
        lev = LEVERAGE if lev is None else lev
        if demo:                     # 데모 계좌는 모의 장부와 별개 — 정산하지 않는다
            pass
        elif action == "open":
            row["qty"] = row["qty"] or notional / px
            set_state(side=side, qty=row["qty"], entry=px, cash=st["cash"] - fee, entered_at=now(), lev=lev)
        elif st["side"]:
            sgn = 1 if st["side"] == "long" else -1
            set_state(side=None, qty=0, entry=0, entered_at=None, cash=st["cash"] + sgn * (px - st["entry"]) * st["qty"] - fee)
        if ex:
            if action == "open":
                X.setup(ex, max(1, math.ceil(lev)))   # 거래소 레버리지는 정수. 명목으로 lev 를 맞추고 증거금은 넉넉히 둔다
                row["order_id"], row["qty"] = X.open_position(ex, side, notional, px)
                if not demo:
                    set_state(qty=row["qty"])
            else:
                row["order_id"] = X.close_position(ex, {"side": side, "qty": qty})
            row["status"] = "submitted"
        else:
            row["status"] = "paper"
        log.info("[%s] %s %s %.6f %s (%s USDT, %.2f배) @%s — %s", row["status"], action, side, row["qty"], X.COIN,
                 f"{notional:,.2f}", lev, f"{px:,.1f}", reason)
    except Exception as e:  # noqa: BLE001
        row["status"] = f"error: {e}"[:200]
        log.error("주문 실패 %s %s: %s", action, side, e)
    with db() as c:
        c.execute(f"INSERT INTO orders ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
    return row["status"] in ("submitted", "paper")


# ---------- 판단 · 주문 ----------
def decide(pos, sig, seen_bar, paper):
    """상태기계 → (open | close | hold, 방향, 사유). 방향은 open·close 일 때만 의미가 있다.
    롱 = 상승 베팅, 숏 = 하락 베팅. 청산은 zone 이 아니라 들고 있는 쪽의 exit_long / exit_short 로 본다.
    paper=True 면 모의 장부의 강제청산도 흉내 낸다."""
    if pos:
        if paper and liquidated(pos):
            return "close", pos["side"], f"손실 {pos['pnl']:,.2f} USDT ≥ 증거금 → 강제청산 (모의 {pos.get('lev') or LEVERAGE:g}배)"
        if pos["side"] == "long" and sig["exit_long"]:
            why = (f"4h 종가 {sig['close']:,.0f} < 직전 {sig['h4_m']}봉 저가 {sig['lo']:,.0f} 이탈" if sig["bull_regime"]
                   else f"일봉 롱 {sig['bull']}/8 < {sig['conf']} 롱 국면 붕괴")
            return "close", "long", why + " → 청산"
        if pos["side"] == "short" and sig["exit_short"]:
            why = (f"4h 종가 {sig['close']:,.0f} > 직전 {sig['h4_m']}봉 고가 {sig['hi_m']:,.0f} 돌파" if sig["bear_regime"]
                   else f"일봉 숏 {sig['bear']}/8 < {sig['conf']} 숏 국면 붕괴")
            return "close", "short", why + " → 청산"
        line = sig["lo"] if pos["side"] == "long" else sig["hi_m"]
        return "hold", None, (f"{'롱' if pos['side'] == 'long' else '숏'} 보유 유지 (롱 {sig['bull']}/8 · 숏 {sig['bear']}/8 · "
                              f"4h {sig['bar']} 종가 {sig['close']:,.0f}, 청산선 {line:,.0f})")
    z = sig["zone"]
    if z in ("long", "short"):
        score = sig["extreme_hi"] if z == "long" else sig["extreme_lo"]
        if EXTREME_MIN and score < EXTREME_MIN:
            return "hold", None, (f"{'롱' if z == 'long' else '숏'} 자리지만 4h 극단 점수 {score}/{sig['extreme_rules']} "
                                  f"< {EXTREME_MIN} (EXTREME_MIN) → 관망. 과열되지 않은 돌파는 성적이 나빴다")
        if z == "short" and not ALLOW_SHORT:
            return "hold", None, (f"숏 자리지만 ALLOW_SHORT=0 이라 진입하지 않는다 (일봉 숏 {sig['bear']}/8 & 4h 종가 "
                                  f"{sig['close']:,.0f} < 직전 {sig['h4_n']}봉 저가 {sig['lo_n']:,.0f} 이탈) → 관망")
        if seen_bar == sig["bar"]:
            return "hold", None, f"4h {'롱' if z == 'long' else '숏'} 자리 {sig['bar']} 은 이미 판단함 → 다음 봉까지 관망"
        if z == "long":
            return "open", "long", (f"일봉 롱 {sig['bull']}/8 & 4h 종가 {sig['close']:,.0f} > 직전 {sig['h4_n']}봉 "
                                    f"고가 {sig['hi']:,.0f} 돌파")
        return "open", "short", (f"일봉 숏 {sig['bear']}/8 & 4h 종가 {sig['close']:,.0f} < 직전 {sig['h4_n']}봉 "
                                 f"저가 {sig['lo_n']:,.0f} 이탈")
    if sig["bull_regime"]:
        why = f"일봉 롱 {sig['bull']}/8 롱 국면이지만 4h 돌파 없음 (종가 {sig['close']:,.0f} ≤ {sig['hi']:,.0f})"
    elif sig["bear_regime"]:
        why = f"일봉 숏 {sig['bear']}/8 숏 국면이지만 4h 이탈 없음 (종가 {sig['close']:,.0f} ≥ {sig['lo_n']:,.0f})"
    else:
        why = f"국면 없음 (롱 {sig['bull']}/8 · 숏 {sig['bear']}/8, 둘 다 {sig['conf']} 미만)"
    return "hold", None, why + " → 관망"


def watch_start(sig):
    """Claude 승인이 난 자리를 '진입 대기' 로 걸어 둔다.
    지킬 선 = 돌파당한 선 (롱은 직전 12봉 고가, 숏은 직전 12봉 저가), 기한 = 다음 4h 봉 마감."""
    since = dt.datetime.strptime(sig["bar"], "%Y-%m-%d %H:%M").replace(tzinfo=KST) + dt.timedelta(hours=4)
    set_state(watch_bar=sig["bar"], watch_hi=M.entry_level(sig), watch_side=sig["zone"],
              watch_since=since.isoformat(timespec="seconds"))


def watch_stop():
    set_state(watch_bar=None, watch_hi=None, watch_since=None, watch_side=None)


def act(ex, pub, run_id, sig, frames, px, acc):
    """decide → 진입 대기(분봉 방어) → **체결 직전 Claude 검토** → 주문. → (action, reason, claude 결과|None)

    Claude 는 '자리가 났을 때' 가 아니라 **정말 주문을 내기 직전에** 부른다 (2026-09-10 이동).
    예전에는 4h 신호가 나자마자 물어보고 승인되면 최대 4시간을 기다렸다 — 검토와 체결이 그만큼 벌어져
    판단이 낡았다. 지금은 분봉이 선을 지켜 '산다' 가 확정된 그 순간에 물어보므로 Claude 가 보는 분봉·현재가가
    실제 체결 조건과 같다. 늦어지는 비용은 측정했다 (backtest_latency.py):
      호출 지연 1~10분 구간의 성적 차이는 노이즈 폭 안이고(검증 Sharpe 1.18~1.29), Claude 타임아웃은 90초다.
    거부하면 그 자리는 접는다 (watch_stop + seen_bar) — 한 자리에 호출은 한 번뿐이다."""
    pos = acc["pos"]
    st = state()
    action, side, reason = decide(pos, sig, st["seen_bar"], paper=not ex)
    lev, lev_why = target_leverage(bool(ex))            # 변동성 타겟팅 — 이번 진입에 쓸 레버리지
    notional = acc["equity"] * POSITION_PCT * lev
    v = None
    if action == "close":
        watch_stop()
        if execute(ex, run_id, "close", pos["side"], pos["qty"], pos["qty"] * px, px, reason):
            acc = account(ex, px)
    fresh = action == "open"
    if fresh and ENTRY_FLOOR:
        watch_start(sig)                                        # 자리가 났다 → 대기로 넘기고 아래 공통 경로에서 선을 본다
        st, action = state(), "hold"
    if not pos and st["watch_bar"] and ENTRY_FLOOR:
        w = st["watch_side"] or "long"                          # 옛 DB 행에는 방향이 없다 (그때는 롱뿐이었다)
        since = dt.datetime.fromisoformat(st["watch_since"])
        if sig["exit_long"] if w == "long" else sig["exit_short"]:
            watch_stop()                                        # 기다리는 사이 그 방향의 청산 조건이 켜졌다 → 자리 무효
            reason = f"진입 대기 취소 ({w} · {st['watch_bar']}): {reason}"
        elif dt.datetime.now(KST) >= since + dt.timedelta(hours=4):
            watch_stop()
            reason = (f"진입 대기 만료 ({w} · 4h 자리 {st['watch_bar']} · "
                      f"{'하한' if w == 'long' else '상한'} {st['watch_hi']:,.0f} 미회복) → 자리 포기")
            log.info(reason)
        else:
            ok, why = floor_check(pub, st["watch_hi"], since, w)
            reason = (reason if fresh else f"진입 대기 ({w} · {st['watch_bar']})") + " · " + why
            action, side = ("open", w) if ok else ("hold", None)
            if ok:
                watch_stop()
    if action == "open" and USE_CLAUDE:                          # ← 체결 직전. 여기서 거부되면 그 자리는 끝
        v = claude_gate(review_payload(sig, *frames, px, acc, notional, pub, lev, side))
        if v["approve"]:
            reason += " · Claude 승인: " + v["reason"]
        else:
            action, reason = "hold", "Claude 거부: " + v["reason"]
            watch_stop()
    if action == "open":
        execute(ex, run_id, "open", side, None, notional, px, reason + (" · " + lev_why if lev_why else ""), lev=lev)
    if sig["zone"] in ("long", "short") and not pos:
        set_state(seen_bar=sig["bar"])
    return action, reason, v


# ---------- 한 사이클 ----------
NEXT_RUN = None   # 스케줄러의 다음 실행 시각 (대시보드 표시용)


def run_cycle(source="자동"):
    live = live_now()
    tag = "데모" if X.DEMO else "실주문"
    mode = tag if live else f"모의 ({paper_trades_done()}/{LIVE_AFTER} 완료 후 {tag})" if MODE == "auto" and HAVE_KEYS else "모의"
    mode += f" · {source}"
    with db() as c:
        run_id = c.execute("INSERT INTO runs (timestamp, mode, status) VALUES (?, ?, 'running')", (now(), mode)).lastrowid
    log.info("=== run %d 시작 (%s%s) ===", run_id, mode, f" · Claude {CLAUDE_MODEL}" if USE_CLAUDE else "")
    try:
        ex = X.client() if live else None
        pub = ex or X.public()
        daily = X.candles(pub, CANDLES).iloc[:-1]                 # 진행 중인 봉 제외
        h4 = X.candles(pub, H4_CANDLES, "4h").iloc[:-1]
        sig = M.signal(daily, h4)
        px = X.price(pub)
        acc = account(ex, px)
        pos, z = acc["pos"], sig["zone"]
        log.info("%s %s USDT · 자리 %s (일봉 %s 롱 %d/8 · 숏 %d/8 · 4h %s 종가 %s · 롱선 %s / 숏선 %s) · 자산 %s USDT (모의 %s) · 포지션 %s",
                 X.COIN, f"{px:,.1f}", z, sig["date"], sig["bull"], sig["bear"], sig["bar"], f"{sig['close']:,.0f}",
                 f"{sig['hi']:,.0f}", f"{sig['lo_n']:,.0f}",
                 f"{acc['equity']:,.2f}", f"{acc['paper_equity']:,.2f}",
                 f"{pos['side']} {pos['held_days']}일 손익 {pos['pnl_pct']:+.1f}%" if pos else "없음")
        if day_loss_hit(acc["equity"], live):                             # 사고 차단기 — 신호와 무관하게 손 떼고 사람을 부른다
            reason = f"24시간 고점 대비 −{MAX_DAY_LOSS:g}% 이상 손실 → 청산 후 자동실행 정지 (재개: make on)"
            log.critical(reason)
            action, v = "hold", None
            if pos and execute(ex, run_id, "close", pos["side"], pos["qty"], pos["qty"] * px, px, reason):
                action = "close"
            set_autorun(False)
        else:
            action, reason, v = act(ex, pub, run_id, sig, (daily, h4), px, acc)   # 판단 → Claude 검토 → 하한 방어 → 주문
        if action != "hold":
            acc = account(ex, px)
        with db() as c:
            c.execute("UPDATE runs SET status='done', equity=?, paper_equity=?, real_equity=?, price=?, zone=?, p=?, bull=?, bear=?, "
                      "position=?, action=?, reason=?, model=?, input_tokens=?, output_tokens=? WHERE id=?",
                      (acc["equity"], acc["paper_equity"], acc["equity"] if ex else real_equity(None), px, z, None,
                       int(sig["bull"]), int(sig["bear"]), pos["side"] if pos else None, action, reason,
                       *((v["model"], v["input_tokens"], v["output_tokens"]) if v else (None, None, None)), run_id))
        log.info("=== run %d 완료: %s — %s ===", run_id, action, reason)
    except Exception as e:  # noqa: BLE001
        log.error("run %d 실패: %s", run_id, X.explain(e))
        log.debug("스택", exc_info=True)
        with db() as c:
            c.execute("UPDATE runs SET status=? WHERE id=?", (f"error: {X.explain(e)}"[:300], run_id))


def confirm_live():
    """진짜 돈이 나갈 수 있는 설정이면 사람이 직접 '실주문' 을 치게 한다. 데모(OKX_DEMO=1)는 묻지 않는다. autotrade·dashboard 공용."""
    if not (HAVE_KEYS and MODE != "paper" and not X.DEMO):
        return
    msg = f"OKX 실계좌 주문 모드입니다 (OKX_DEMO=0, MODE={MODE}{', 모의 %d회 완료 후 자동 전환' % LIVE_AFTER if MODE == 'auto' else ''}, {LEVERAGE:g}배). '실주문' 을 입력하면 계속합니다: "
    if input(msg).strip() != "실주문":
        sys.exit("취소")


_LOCK = None


def single_instance(port=8765):
    """스케줄러는 한 프로세스만. 두 개가 돌면 같은 돌파봉으로 주문이 두 번 나간다
    (2026-09-08 실제로 dashboard 와 autotrade 가 같이 떠서 CONF 7·8 두 판단이 3분 간격으로 trading.db 에 섞였다).
    포트를 하나 잡아 두는 것으로 막는다 — 프로세스가 죽으면 OS 가 알아서 놓아 준다."""
    global _LOCK
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
    except OSError:
        return False
    _LOCK = s                                                # 참조를 살려 둬야 닫히지 않는다
    return True


def schedule_forever():
    """시작 즉시 한 사이클, 이후 INTERVAL_MIN 분마다. 대시보드는 이걸 백그라운드 스레드로 돌린다.
    벽시계 기준으로 다음 시각을 정하고 30초씩 자므로, 재시작·PC 절전 뒤에도 늦은 사이클을 바로 따라잡는다."""
    global NEXT_RUN
    if not single_instance():
        log.critical("스케줄러가 이미 다른 프로세스에서 돌고 있습니다 — 이 프로세스는 자동 판단을 하지 않습니다 (make stop 후 하나만 띄우세요)")
        return
    while True:
        NEXT_RUN = dt.datetime.now(KST) + dt.timedelta(minutes=INTERVAL_MIN)
        if not autorun():
            log.info("자동실행 OFF (autorun.off) — 이번 판단 건너뜀")
        else:
            try:
                run_cycle("자동")
            except Exception:
                log.exception("자동 사이클 실패")
        log.info("다음 실행 %s", NEXT_RUN.strftime("%H:%M"))
        while dt.datetime.now(KST) < NEXT_RUN:
            time.sleep(30)


def manual_order(action, demo=False):
    """대시보드에서 사람이 직접 누른 실주문. auto 모드의 '모의 N회' 게이트와 무관하게 바로 나간다.
    자동 매매와 같은 execute() 를 타므로 주문 기록·장부가 그대로 이어진다."""
    if not X.have_keys(demo):
        raise ValueError(f"OKX {'데모' if demo else '실계좌'} API 키가 없습니다")
    if not demo:
        watch_stop()          # 사람이 직접 주문했으면 대기 중이던 자리는 무효 (남겨 두면 나중에 혼자 진입한다)
    if MODE == "paper" and not demo:
        raise ValueError("MODE=paper 에서는 실주문을 보내지 않습니다 (.env 의 MODE 를 auto 나 live 로). 데모 주문은 그대로 됩니다")
    ex = X.client(demo)
    snap = X.snapshot(ex)
    px, pos = snap["price"], snap["position"]
    if action == "buy" and pos:
        raise ValueError(f"이미 {snap['coin_qty']:.6f} {X.COIN} 포지션이 있습니다")
    if action == "sell" and not pos:
        raise ValueError("열린 포지션이 없습니다")
    if action == "buy" and snap["cash"] * POSITION_PCT * LEVERAGE < X.MIN_ORDER:
        raise ValueError(f"주문 가능 {snap['cash']:,.2f} USDT × {LEVERAGE:g}배 < 최소 {X.MIN_ORDER} USDT")
    with db() as c:
        run_id = c.execute("INSERT INTO runs (timestamp, mode, status, price, position, action, reason) "
                           "VALUES (?, '수동', 'done', ?, ?, ?, ?)",
                           (now(), px, pos["side"] if pos else None, "open" if action == "buy" else "close",
                            f"대시보드 수동 {'데모' if demo else '실'}주문")).lastrowid
    if action == "buy":
        ok = execute(ex, run_id, "open", "long", None, snap["cash"] * POSITION_PCT * LEVERAGE, px,
                     f"대시보드 수동 매수{' (데모)' if demo else ''}", demo)
    else:
        ok = execute(ex, run_id, "close", pos["side"], pos["qty"], pos["qty"] * px, px,
                     f"대시보드 수동 매도{' (데모)' if demo else ''}", demo)
    if not ok:
        with db() as c:
            raise RuntimeError(c.execute("SELECT status FROM orders WHERE run_id=? ORDER BY id DESC LIMIT 1",
                                         (run_id,)).fetchone()[0])
    return {"action": action, "price": px, "run_id": run_id}


if __name__ == "__main__":
    confirm_live()
    run_cycle()
    if "--once" not in sys.argv:
        schedule_forever()
```

## `okx.py`

```python
"""OKX USDT 무기한 선물 클라이언트 (ccxt). 격리 마진 · 단방향(net) 포지션. upbit.py 를 대체한다.

  · OKX_DEMO=1 이면 모의투자(Demo Trading) 서버로 주문이 나간다. 키도 데모 전용 키여야 한다
    (okx.com > 모의투자 > API 에서 발급. 실계좌 키와 다르다).
  · 레버리지: setup(ex, LEVERAGE) 가 격리 마진 + 레버리지를 심볼에 설정한다. 명목 = 자산 × POSITION_PCT × LEVERAGE.
  · OKX 주문 단위는 '계약' (BTC-USDT-SWAP 은 1계약 = 0.01 BTC, 최소 0.01 계약) 이지만 봇 안의 qty 는 항상 BTC 수량이다.
    변환은 이 파일의 contracts() 한 곳에서만 한다.
일봉 경계는 UTC 00:00 (KST 09:00) 라서 업비트와 같다.
"""
import os
import re
import sys

import ccxt
import pandas as pd
from dotenv import load_dotenv

load_dotenv()                # autotrade 보다 먼저 import 되므로 여기서 .env 를 읽어야 SYMBOL·DEMO 가 맞는다
SYMBOL = os.environ.get("SYMBOL", "BTC/USDT:USDT")
COIN = SYMBOL.split("/")[0]
CCY = "USDT"
DEMO = os.environ.get("OKX_DEMO", "1") != "0"          # 기본 대상(자동 매매). 대시보드는 주문마다 demo 를 골라 보낸다
DEMO_KEYS = ("OKX_DEMO_API_KEY", "OKX_DEMO_SECRET", "OKX_DEMO_PASSPHRASE")   # 데모 서버는 모의투자 전용 키를 쓴다
TAKER_FEE = 0.0005          # 0.05% (일반 등급)
MIN_ORDER = 10              # 최소 명목 (USDT). 0.01계약 × BTC 가격보다 조금 넉넉히
_MARKET = None


def explain(e):
    """OKX 인증 에러를 사람이 읽을 한 줄로. 원문 JSON 을 그대로 화면에 뿌리면 무슨 일인지 알 수가 없다."""
    s = str(e)
    if "50110" in s:                       # 집 인터넷은 공인 IP 가 바뀌므로 이건 주기적으로 재발한다
        ip = re.search(r"Your IP ([\d.]+)", s)
        return (f"OKX API 키에 현재 IP {ip.group(1) if ip else '(불명)'} 가 등록돼 있지 않습니다 (50110). "
                "okx.com > 우측 상단 프로필 > API > 해당 키 편집 > IP 주소에 추가하세요. "
                "공유기 재부팅·ISP 재할당으로 IP 가 바뀌면 다시 등록해야 합니다.")
    if "50101" in s:
        return ("키가 환경과 맞지 않습니다 (50101) — 실계좌 키를 데모 서버에 쓰거나 그 반대입니다. "
                ".env 의 OKX_DEMO 와 어떤 키를 넣었는지 확인하세요.")
    if any(c in s for c in ("50102", "50111", "50113")):
        return ("OKX 인증 실패 — 키·시크릿·패스프레이즈가 틀렸거나 PC 시각이 어긋났습니다 (50102는 타임스탬프). "
                ".env 값과 윈도우 시계 동기화를 확인하세요.")
    return f"{type(e).__name__}: {s}"[:200]


def have_keys(demo=False):
    return all(os.environ.get(k) for k in (DEMO_KEYS if demo else ("OKX_API_KEY", "OKX_SECRET", "OKX_PASSPHRASE")))


def _ex(keys, demo=None):
    demo = DEMO if demo is None else demo
    cfg = {"enableRateLimit": True, "options": {"defaultType": "swap"}}
    if keys:
        if not have_keys(demo):
            raise ValueError(f".env 에 {', '.join(DEMO_KEYS)} 가 없습니다" if demo else ".env 에 OKX 키가 없습니다")
        k = DEMO_KEYS if demo else ("OKX_API_KEY", "OKX_SECRET", "OKX_PASSPHRASE")
        cfg.update(apiKey=os.environ[k[0]], secret=os.environ[k[1]], password=os.environ[k[2]])
    ex = ccxt.okx(cfg)
    if demo:
        ex.set_sandbox_mode(True)
    return ex


def public():
    return _ex(False)


def client(demo=None):
    """demo=None 이면 .env 의 OKX_DEMO 를 따른다. 대시보드는 True/False 를 직접 준다."""
    return _ex(True, demo)


def market(ex):
    global _MARKET
    if _MARKET is None:
        _MARKET = ex.load_markets()[SYMBOL]
    return _MARKET


def candles(ex, count=100, tf="1d"):
    """봉 (1d = UTC 00:00 경계, 4h = UTC 00/04/08/... 경계). 마지막 행은 진행 중인 봉."""
    rows = ex.fetch_ohlcv(SYMBOL, tf, limit=count)
    if not rows:
        raise ValueError(f"{SYMBOL}: 캔들 없음")
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df.timestamp, unit="ms", utc=True).dt.tz_convert("Asia/Seoul")
    return df.set_index("timestamp").astype(float)


def price(ex):
    return float(ex.fetch_ticker(SYMBOL)["last"])


def position(ex):
    """열린 포지션 {side: long|short, qty(BTC), entry, pnl, liq} 또는 None."""
    for p in ex.fetch_positions([SYMBOL]):
        if float(p.get("contracts") or 0) > 0:
            return {"side": p["side"], "qty": float(p["contracts"]) * float(market(ex)["contractSize"]), "entry": float(p["entryPrice"]),
                    "pnl": float(p.get("unrealizedPnl") or 0), "liq": float(p.get("liquidationPrice") or 0)}
    return None


def snapshot(ex):
    """{price, cash, coin_qty(BTC), coin_value, equity, position}. 봇과 대시보드가 같은 값을 본다."""
    px = price(ex)
    bal = ex.fetch_balance().get(CCY) or {}          # USDT 가 한 번도 안 들어온 계좌는 키 자체가 없다 → 0
    pos = position(ex)
    qty = pos["qty"] if pos else 0.0
    return {"price": px, "cash": float(bal.get("free") or 0), "coin_qty": qty, "coin_value": qty * px,
            "equity": float(bal.get("total") or 0), "position": pos}


def equity(ex):
    return snapshot(ex)["equity"]


def setup(ex, leverage):
    """단방향 포지션 · 격리 마진 · 레버리지. 포지션 모드는 이미 맞으면 OKX 가 에러를 주므로 무시한다."""
    try:
        ex.set_position_mode(False, SYMBOL)
    except ccxt.BaseError:
        pass
    ex.set_leverage(int(leverage), SYMBOL, params={"mgnMode": "isolated"})
    return int(leverage)


def contracts(ex, qty_btc):
    """BTC 수량 → 계약 수 (거래소 정밀도로 내림)."""
    return float(ex.amount_to_precision(SYMBOL, qty_btc / float(market(ex)["contractSize"])))


def open_position(ex, side, notional, px):
    if notional < MIN_ORDER:
        raise ValueError(f"주문 금액 {notional:,.2f} USDT < 최소 {MIN_ORDER} USDT")
    n = contracts(ex, notional / px)
    if n <= 0:
        raise ValueError(f"계약 수 0 (명목 {notional:,.2f} USDT)")
    o = ex.create_order(SYMBOL, "market", "buy" if side == "long" else "sell", n, params={"tdMode": "isolated"})
    return o["id"], n * float(market(ex)["contractSize"])


def close_position(ex, pos):
    o = ex.create_order(SYMBOL, "market", "sell" if pos["side"] == "long" else "buy", contracts(ex, pos["qty"]),
                        params={"tdMode": "isolated", "reduceOnly": True})
    return o["id"]


if __name__ == "__main__":
    if "keys" in sys.argv:                       # python okx.py keys — 키가 맞는지 잔고 조회로 확인 (주문 안 함)
        missing = [k for k in ("OKX_API_KEY", "OKX_SECRET", "OKX_PASSPHRASE") if not os.environ.get(k)]
        if missing:
            sys.exit(f".env 에 {', '.join(missing)} 가 비어 있습니다")
        try:
            snap = snapshot(client())
        except Exception as e:                   # noqa: BLE001 — 스택 대신 무엇을 고쳐야 하는지 한 줄로
            sys.exit(explain(e))
        print(f"ok  {'데모' if DEMO else '실계좌'} 잔고 {snap['equity']:,.2f} USDT (가용 {snap['cash']:,.2f}) · "
              f"{COIN} {snap['price']:,.1f} · 포지션 {snap['position'] or '없음'}")
        sys.exit()
    assert "121.0.0.1" in explain(Exception('{"msg":"Your IP 121.0.0.1 is not in your API key IP whitelist.","code":"50110"}'))
    assert "OKX_DEMO" in explain(Exception('{"msg":"APIKey does not match current environment.","code":"50101"}'))
    assert explain(ValueError("boom")).startswith("ValueError")
    ex = public()
    df = candles(ex, 30)
    assert len(df) == 30 and df.close.iloc[-1] > 0 and df.index[-1] > df.index[0]
    px = price(ex)
    assert abs(px / df.close.iloc[-1] - 1) < 0.5
    n = contracts(ex, 100 / px)
    assert 0 < n * float(market(ex)["contractSize"]) * px <= 100
    print("ok", SYMBOL, "demo" if DEMO else "LIVE", df.index[-1], f"{px:,.1f}", "100USDT =", n, "계약")
```

## `reversion.py`

```python
"""당일 고점·저점 판별 모델 (평균회귀). 고점 후보에서 숏, 저점 후보에서 롱.

기존 봇(model.py)은 추세 추종이다 — 고가를 넘으면 사고 저가를 깨면 판다. 이 파일은 정반대 가설을 다룬다:
research_both_sides.txt 에서 '하락은 짧고 급하고 되돌림이 빠르다' 가 반복해서 나왔고, 그 되돌림을
추세 규칙으로 먹으려던 시도(숏, 반등 롱)는 전부 실패했다. 되돌림이 진짜라면 **극단에서 반대로 걸어야** 한다.

고점/저점은 지나고 나서야 확정되므로, 여기서는 '지금 봉이 극단처럼 보이는가' 를 보조지표로 채점한다.
미래 정보는 쓰지 않는다 — 판정은 **마지막 완성 봉까지의 값**만 본다.

고점 규칙 7개 (저점은 전부 거울):
  ① RSI(14) ≥ 70              과매수
  ② 볼린저 %B ≥ 1.0            상단 밴드 이탈
  ③ 스토캐스틱 %K ≥ 80         과매수
  ④ 도치안 위치 ≥ 0.95         직전 20봉 고저 범위의 상단
  ⑤ 윗꼬리 ≥ 몸통              위에서 거부당함
  ⑥ 거래량 ≥ 1.5 × 20봉 평균   극단에 물량이 실림
  ⑦ 3봉 상승폭 ≥ 2 × ATR(14)   과도한 급등
  N개 이상 켜지면 고점 후보 (REV_CONF).

**이 7개를 더해 '점수' 로 쓰는 것에는 검증된 근거가 없다** (research_bias.txt, 2026-09-10).
돌파봉만 따로 놓고 규칙별로 재보니 ② 볼린저 이탈이 사실상 혼자 일하고(무작위 대비 백분위 99),
④ 도치안은 오히려 해로웠다(백분위 1). 나머지는 희석한다. 합계 점수는 백분위 95~98 인데
후보 ~18개 중 사후에 고른 것이라 실효 p ≈ 0.30 이다.
그렇다고 '볼린저 단독' 으로 갈아타서도 안 된다 — 7개 중 사후 최고를 고르는 더 심한 선택 편향이다.
지금 이 파일의 쓰임새는 **관찰과 연구**다. 매매 필터(autotrade.EXTREME_MIN)는 0(꺼짐)이 기본이다.

나스닥(QQQ) 은 선택 필터다 — 켜면 '주식이 같은 방향일 때만' 진입한다 (research_reversion.txt 참고).
사용: python reversion.py     자체 점검
"""
import os
import pathlib

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent
CACHE = ROOT / "data_cache"
REV_CONF = int(os.environ.get("REV_CONF", 4))     # 7개 중 몇 개 이상이면 극단 후보인가
DON_N, VOL_N, ATR_N = 20, 20, 14


def add_features(d):
    """4h(또는 1d) 봉 → 판정에 쓰는 지표. 전부 그 봉까지의 정보만 쓴다 (shift 없이 당봉 종가 기준)."""
    from indicators import add_indicators
    x = add_indicators(d.copy())
    c, h, l, o, v = x.close, x.high, x.low, x.open, x.volume

    body = (c - o).abs().clip(lower=1e-9)
    x["wick_up"] = (h - c.combine(o, max)) / body            # 윗꼬리 / 몸통
    x["wick_dn"] = (c.combine(o, min) - l) / body            # 아랫꼬리 / 몸통

    hh, ll = h.rolling(DON_N).max(), l.rolling(DON_N).min()
    x["don"] = (c - ll) / (hh - ll).replace(0, np.nan)       # 0=구간 최저, 1=구간 최고

    x["vol_ratio"] = v / v.rolling(VOL_N).mean().replace(0, np.nan)

    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    x["ATR"] = tr.ewm(alpha=1 / ATR_N, adjust=False).mean()
    x["push"] = (c - c.shift(3)) / x["ATR"].replace(0, np.nan)   # 3봉 이동 / ATR
    return x


# (이름, 고점 조건, 저점 조건) — 저점은 고점의 거울이다
RULES = [
    ("RSI 과열", lambda x: x.RSI_14 >= 70, lambda x: x.RSI_14 <= 30),
    ("볼린저 밴드 이탈", lambda x: x["BBP_20_2.0_2.0"] >= 1.0, lambda x: x["BBP_20_2.0_2.0"] <= 0.0),
    ("스토캐스틱", lambda x: x["STOCHk_14_3_3"] >= 80, lambda x: x["STOCHk_14_3_3"] <= 20),
    ("도치안 상·하단", lambda x: x.don >= 0.95, lambda x: x.don <= 0.05),
    ("꼬리 거부", lambda x: x.wick_up >= 1.0, lambda x: x.wick_dn >= 1.0),
    ("거래량 급증", lambda x: x.vol_ratio >= 1.5, lambda x: x.vol_ratio >= 1.5),
    ("ATR 대비 급등락", lambda x: x.push >= 2.0, lambda x: x.push <= -2.0),
]


def scores(d):
    """→ (고점 점수 0~7, 저점 점수 0~7) 시리즈. 각 봉의 종가 시점 판정."""
    x = add_features(d)
    hi = sum(f(x).fillna(False).astype(int) for _, f, _ in RULES)
    lo = sum(g(x).fillna(False).astype(int) for _, _, g in RULES)
    return hi, lo


def signals(d, conf=None):
    """→ (고점 후보 bool, 저점 후보 bool). 둘 다 참인 봉은 판정 불가로 보고 둘 다 끈다."""
    conf = REV_CONF if conf is None else conf
    hi, lo = scores(d)
    top, bot = hi >= conf, lo >= conf
    both = top & bot
    return top & ~both, bot & ~both


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    n = 300
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    rng = np.random.default_rng(0)
    # 톱니파 + 잡음 → 극단이 주기적으로 생기는 합성 시계열
    base = 100 + 20 * np.sin(np.arange(n) / 12) + rng.normal(0, 0.5, n)
    d = pd.DataFrame({"close": base}, index=idx)
    d["open"] = d.close.shift(1).fillna(base[0])
    d["high"] = d[["open", "close"]].max(axis=1) + 0.6
    d["low"] = d[["open", "close"]].min(axis=1) - 0.6
    d["volume"] = 1.0 + rng.random(n)

    hi, lo = scores(d)
    assert hi.max() >= 3 and lo.max() >= 3, "합성 톱니파에서 극단 점수가 전혀 안 오른다"
    top, bot = signals(d, conf=3)
    assert top.any() and bot.any(), "고점·저점 후보가 하나도 안 잡힌다"
    assert not (top & bot).any(), "같은 봉이 고점이자 저점일 수는 없다"
    # 고점 후보는 저점 후보보다 평균적으로 높은 자리에 있어야 한다
    assert d.close[top].mean() > d.close[bot].mean(), "고점 후보가 저점 후보보다 낮은 자리에 잡혔다"
    # 미래 참조 없음: 앞쪽 절반만 줘도 그 구간 판정이 같아야 한다
    half = len(d) // 2
    h2, _ = scores(d.iloc[:half])
    assert (h2.iloc[-20:].values == hi.iloc[half - 20:half].values).all(), "뒤쪽 데이터가 앞쪽 판정을 바꿨다 (미래 참조)"
    print(f"ok  고점 후보 {top.sum()}봉 · 저점 후보 {bot.sum()}봉 (합성 {n}봉, conf=3) · 미래 참조 없음")
```

## `backtest_quant.py`

```python
"""quant_nasq100(나스닥 100 봇)에서 검증돼 채택된 퀀트 기법을 코인 봇 규칙 위에 얹어 본다.

가져온 것 (그쪽 README 의 '채택한 규칙' / '넣지 않기로 한 것' 기준):
  · 변동성 타겟팅   실현 변동성이 목표를 넘으면 노출을 깎는다. cap = min(1, 목표/실현변동성)
                    저쪽: Sharpe 1.32→1.34, MDD −36.9%→−31.2% (검증 구간). 타겟·창 폭넓게 견고했다
  · 고정 만기 청산   보유 N일이 지나면 신호와 무관하게 판다. 저쪽에서 승률 56.4% 로 모멘텀 청산(42.1%)을 이겼다
  · 하락 국면 전환   지수 60일 수익률 < −3% 면 하락 국면 → 저쪽은 저변동성 종목으로 갈아탄다.
                    단일 종목인 여기서는 '레버리지를 1배로 낮춘다' 로 옮긴다
  · 탐색/검증 분할   두 구간 모두에서 기준선을 이겨야 채택. 한쪽만 이기면 과적합으로 본다
  · 지표             CAGR · 연율 변동성 · Sharpe(= CAGR / 변동성) · MDD. 누적%만 보면 위험이 안 보인다
저쪽이 재보고 떨어뜨린 것(손절선·비중 트림·하락장 매매 중단)은 이 코인 백테스트에서도 이미 같은 결론이
나 있다 (research_okx_short.txt 익절·손절 비교). 그래서 다시 재지 않는다.

기준선 = 현재 봇 규칙: 일봉 강세 CONF개 이상 & 4h 종가가 직전 12봉 고가 돌파 → 직전 6봉 저가 이탈/국면 붕괴에 청산.
체결·비용 가정은 backtest_okx.py 와 같다 (신호봉 종가 → 다음 봉 시가, 편도 0.07%, 펀딩 0.01%/8h, 격리 청산).
분봉 하한 방어(reclaim)는 여기 없다 — 4h 단위 비교라 backtest_entry.py 의 'now' 와 같은 자리다.

사용: python backtest_quant.py [편도비용]
"""
import sys

import numpy as np
import pandas as pd

import backtest_okx as B
import model as M

COST = B.arg_cost(__file__)
START, SPLIT = "2021-03-01", "2024-01-01"     # 탐색 2021-03~2023 / 검증 2024~2026 (둘 다 하락 구간 포함)
BPD = 6                                       # 하루 4h 봉 개수
FUND = 0.0001 * 3 / BPD                       # 4h 봉당 펀딩
YEAR = 365                                    # 코인은 24/7 — 주식의 252 자리
MAX_LEV = 3                                   # autotrade.MAX_LEVERAGE. 타겟팅은 깎기만 하고 올리지 않는다
VOL_WIN = 60                                  # 실현 변동성 창 (일). quant_nasq100 기본값과 같다
BEAR_RET60 = -3                               # 60일 수익률이 이 밑이면 하락 국면 (저쪽 BEAR_RET60_PCT)


def frames():
    """4h 봉 + 봉마다 (진입신호, 청산신호, BTC 실현변동성%, 60일 수익률%). 전부 과거 정보만 쓴다."""
    d1, h4 = B.fetch("1d"), B.fetch("4h")
    rg = (B.bull(d1) >= M.CONF)
    rg.index = rg.index + pd.Timedelta(days=1)                    # 일봉이 닫힌 다음 날부터 유효
    rg = rg.reindex(h4.index, method="ffill").fillna(False).astype(bool)
    hi = h4.high.rolling(M.H4_N).max().shift(1)
    lo = h4.low.rolling(M.H4_M).min().shift(1)

    r1 = d1.close.pct_change()
    btc_vol = (r1.rolling(VOL_WIN).std() * np.sqrt(YEAR) * 100).shift(1)   # 어제까지의 정보만
    ret60 = ((d1.close / d1.close.shift(60) - 1) * 100).shift(1)
    ext = lambda s: s.reindex(h4.index, method="ffill")
    return h4, ((h4.close > hi) & rg).values, ((h4.close < lo) | ~rg).values, ext(btc_vol).values, ext(ret60).values


def reclaim_fills():
    """실제 봇이 쓰는 분봉 하한 방어(research_entry_timing.txt)의 체결가를 이벤트별로 구한다.
    → {진입봉 시각: 체결가}. 창 안에 하한을 되찾지 못한 자리는 값이 None (봇도 그 자리를 포기한다).
    분봉이 없는 이벤트는 아예 키를 넣지 않는다 → 시가 시장가로 떨어진다 (지금 표본에선 0건)."""
    import backtest_entry as E
    evs, h4e = E.events()
    mins = E.minutes(evs)
    return {h4e.index[i]: E.reclaim_at(mins[t][1], hi)
            for t, i, _, hi, _ in evs if len(mins[t][1]) >= 10}


def simulate(h4, entry, exit_, lev_of, hold_days=0, fills=None, side=1):
    """봉 i 종가 신호 → i+1 시가 체결. 레버리지는 진입 시점에 lev_of(i, 지금까지의 자본곡선)으로 정하고
    그 거래 동안 고정한다 (거래소가 포지션 단위로 레버리지를 걸어서 중간에 못 바꾼다).
    hold_days>0 이면 그만큼 지나면 신호와 무관하게 판다 (고정 만기 청산).
    side=1 롱 · side=-1 숏 (backtest_short.py). 숏은 펀딩을 내는 게 아니라 받는 쪽으로 잡는다.
    → (자본곡선, 거래수익률, 청산횟수)"""
    assert 0 <= COST < 0.05, f"편도 비용 {COST} 는 상식 밖이다 — 인자를 비용으로 잘못 읽지 않았는지 확인 (arg_cost)"
    o, l, hh, c, idx = h4.open.values, h4.low.values, h4.high.values, h4.close.values, h4.index
    n = len(h4)
    eq, held, trades, liq = 1.0, None, [], 0
    curve = np.ones(n)
    cap = hold_days * BPD if hold_days else 10 ** 9
    for i in range(1, n):
        if held:
            e, k, lev = held
            k += 1
            if (l[i] <= e * (1 - 1 / lev)) if side > 0 else (hh[i] >= e * (1 + 1 / lev)):   # 격리 청산
                eq, held, liq = 0.0, None, liq + 1
                trades.append(-1.0)
            elif exit_[i - 1] or k >= cap:
                r = side * lev * (o[i] / e - 1) - 2 * lev * COST - side * k * FUND * lev
                eq *= 1 + r
                trades.append(r)
                held = None
            else:
                held = (e, k, lev)
        if held is None and eq > 0 and entry[i - 1]:
            px = o[i] if fills is None else fills.get(idx[i], o[i])   # fills 에 None 이 들어 있으면 자리 포기
            lev = lev_of(i, curve)
            if lev > 0 and px is not None:
                held = (px, 0, lev)
        curve[i] = eq * (1 + side * held[2] * (c[i] / held[0] - 1)) if held else eq
        if eq <= 0:
            curve[i:] = 0.0
            break
    return pd.Series(curve, index=h4.index), np.array(trades) if trades else np.zeros(1), liq


def acct_vol(i, curve, win=VOL_WIN):
    """진입 직전까지의 계좌 일별 수익률로 잰 연율 실현 변동성(%). 이력이 모자라면 None.
    quant_nasq100 의 exposure_cap() 과 같은 계산 — 자산 자체가 아니라 계좌의 변동성을 본다."""
    daily = curve[max(0, i - win * BPD):i:BPD]
    if len(daily) < win // 2 or (daily <= 0).any():
        return None
    if len(r := np.diff(daily) / daily[:-1]) < 2:
        return None
    v = float(np.std(r, ddof=1) * np.sqrt(YEAR) * 100)
    return v if v > 0 else None            # 창 내내 쉬었으면 변동성 0 → 잴 것이 없다 (타겟팅 비활성)


def metrics(curve, trades, liq, lo=None, hi=None):
    """일별로 다시 샘플링해 CAGR·연율변동성·Sharpe·MDD. 구간을 잘라도 지표 정의는 같다."""
    c = curve[lo:hi]
    d = c.resample("D").last().dropna()
    d = d / d.iloc[0]
    r = d.pct_change().dropna()
    yrs = len(d) / YEAR
    cagr = (d.iloc[-1] ** (1 / yrs) - 1) * 100 if d.iloc[-1] > 0 and yrs > 0 else -100.0
    vol = r.std() * np.sqrt(YEAR) * 100
    return {"누적": d.iloc[-1] * 100 - 100, "cagr": cagr, "vol": vol, "sharpe": cagr / vol if vol else 0,
            "mdd": (d / d.cummax() - 1).min() * 100, "거래": len(trades), "승률": (trades > 0).mean() * 100,
            "최악": trades.min() * 100, "청산": liq}


def report(name, curve, trades, liq):
    a, b, f = (metrics(curve, trades, liq, *s) for s in ((START, SPLIT), (SPLIT, None), (START, None)))
    print(f"{name:24s}"
          f"{a['cagr']:+7.0f}%{a['mdd']:7.1f}%{a['sharpe']:6.2f} |"
          f"{b['cagr']:+7.0f}%{b['mdd']:7.1f}%{b['sharpe']:6.2f} |"
          f"{f['누적']:+8.0f}%{f['mdd']:7.1f}%{f['sharpe']:6.2f}"
          f"{f['거래']:5d}{f['승률']:5.0f}%{f['최악']:7.1f}%{liq:3d}")


def main():
    h4, entry, exit_, btc_vol, ret60 = frames()
    m = h4.index >= pd.Timestamp(START, tz="UTC")
    h4, entry, exit_, btc_vol, ret60 = h4[m], entry[m], exit_[m], btc_vol[m], ret60[m]
    print(f"OKX BTC 4h {h4.index[0]:%Y-%m-%d}~{h4.index[-1]:%Y-%m-%d} · CONF={M.CONF} · 편도 {COST * 100:.2f}%")
    print(f"탐색 {START}~{SPLIT} / 검증 {SPLIT}~ · Sharpe = CAGR / 연율변동성 (quant_nasq100 정의)")
    print(f"\n{'':24s}{'──── 탐색 ────':>22} |{'──── 검증 ────':>22} |{'────── 전체 ──────':>28}"
          f"{'거래':>5}{'승률':>6}{'최악':>7}{'청산':>4}")
    print(f"{'':24s}{'CAGR':>7}{'MDD':>7}{'Sh':>6} |{'CAGR':>7}{'MDD':>7}{'Sh':>6} |"
          f"{'누적':>9}{'MDD':>7}{'Sh':>6}")

    base = {}
    print("\n── 기준선 (현재 봇 규칙, 고정 레버리지) ──")
    for lev in (1, 2, 3):
        base[lev] = simulate(h4, entry, exit_, lambda i, c, L=lev: L)
        report(f"고정 {lev}배", *base[lev])

    print("\n── ① 변동성 타겟팅 · 계좌 실현변동성 기준 (저쪽 exposure_cap 과 같은 계산) ──")
    for tg in (40, 60, 80, 120):
        def lev_of(i, c, tg=tg):
            v = acct_vol(i, c)
            return MAX_LEV if v is None else min(MAX_LEV, tg / v)   # 이력 부족하면 3배 (저쪽도 기능 비활성)
        report(f"3배 +VT(계좌) {tg}%", *simulate(h4, entry, exit_, lev_of))

    print("\n── ② 변동성 타겟팅 · BTC 실현변동성 기준 (단일 종목이라 이쪽이 자연스럽다) ──")
    for tg in (40, 60, 80, 120):
        def lev_of(i, c, tg=tg):
            v = btc_vol[i]
            return MAX_LEV if not np.isfinite(v) or v <= 0 else min(MAX_LEV, tg / v)
        report(f"3배 +VT(BTC) {tg}%", *simulate(h4, entry, exit_, lev_of))

    print("\n── ③ 고정 만기 청산 (구조 청산은 그대로 두고 상한만 추가) ──")
    for hd in (3, 5, 10, 20, 40):
        report(f"3배 +만기 {hd}일", *simulate(h4, entry, exit_, lambda i, c: 3, hold_days=hd))

    print("\n── ④ 하락 국면 (60일 수익률 < −3%) ──")
    report("3배 → 하락국면 1배", *simulate(h4, entry, exit_,
           lambda i, c: 1.0 if (np.isfinite(ret60[i]) and ret60[i] < BEAR_RET60) else 3.0))
    report("하락국면 진입금지", *simulate(h4, entry, exit_,
           lambda i, c: 0.0 if (np.isfinite(ret60[i]) and ret60[i] < BEAR_RET60) else 3.0))

    print("\n── ⑤ 조합: VT(BTC) + 하락국면 축소 ──")
    for tg in (60, 80):
        def lev_of(i, c, tg=tg):
            v = btc_vol[i]
            lv = MAX_LEV if not np.isfinite(v) or v <= 0 else min(MAX_LEV, tg / v)
            return min(lv, 1.0) if (np.isfinite(ret60[i]) and ret60[i] < BEAR_RET60) else lv
        report(f"VT(BTC){tg}% + 하락1배", *simulate(h4, entry, exit_, lev_of))

    print("\n── ⑥ 실제 봇 진입(분봉 하한 재확보)에 변동성 타겟팅을 얹으면 ──")
    fills = reclaim_fills()
    print(f"   (진입 이벤트 {len(fills)}건 중 하한 미회복으로 포기 {sum(v is None for v in fills.values())}건)")
    rc = {}
    for lev in (1, 3):
        rc[lev] = simulate(h4, entry, exit_, lambda i, c, L=lev: L, fills=fills)
        report(f"reclaim 고정 {lev}배", *rc[lev])
    for tg, win in ((30, 60), (40, 60), (30, 120), (40, 120)):
        def lev_of(i, c, tg=tg, win=win):
            v = acct_vol(i, c, win)
            return MAX_LEV if v is None else min(MAX_LEV, tg / v)
        report(f"reclaim +VT {tg}%·창{win}", *simulate(h4, entry, exit_, lev_of, fills=fills))

    if "--final" in sys.argv:
        # 실전 설정(하한 재확보 진입)에서 변동성 타겟팅을 확정한다. 앞의 --robust 는 'now' 진입 기준이었다.
        fl = reclaim_fills()
        base3 = simulate(h4, entry, exit_, lambda i, c: 3, fills=fl)
        print("\n\n═══ 실전 경로(하한 재확보) 위에서 변동성 타겟팅 확정 ═══")
        print("\n[기준] reclaim 고정 3배")
        report("reclaim 3배", *base3)

        print("\n── 타겟 × 창 (전 조합) ──")
        for win in (30, 40, 60, 90, 120):
            for tg in (20, 30, 40, 50, 60):
                def lev_of(i, c, tg=tg, win=win):
                    v = acct_vol(i, c, win)
                    return MAX_LEV if v is None else min(MAX_LEV, tg / v)
                report(f"reclaim VT {tg}%·창{win}", *simulate(h4, entry, exit_, lev_of, fills=fl))
            print()

        print("── 워밍업(자산 이력이 창의 절반도 없을 때) 대체값 ──")
        print("   실전에서 이 구간은 계좌를 새로 시작할 때마다 반드시 지나간다. 지금 실계좌가 딱 여기다.")
        for name, fb in (("3배 그대로(현재)", lambda i: MAX_LEV),
                         ("BTC 변동성으로", lambda i: MAX_LEV if not np.isfinite(btc_vol[i]) or btc_vol[i] <= 0
                          else min(MAX_LEV, 40 / btc_vol[i])),
                         ("2배로 낮춤", lambda i: 2.0),
                         ("1배로 낮춤", lambda i: 1.0)):
            def lev_of(i, c, fb=fb):
                v = acct_vol(i, c)
                return fb(i) if v is None else min(MAX_LEV, 40 / v)
            report(f"워밍업 → {name}", *simulate(h4, entry, exit_, lev_of, fills=fl))

        n_wait = sum(1 for i in range(1, len(h4)) if entry[i - 1] and acct_vol(i, base3[0].values) is None)
        print(f"\n   (전체 진입 {len(base3[1])}건 중 워밍업·무거래로 타겟팅이 쉬어 간 진입 {n_wait}건)")

        # 하한 재확보의 마지막 미측정 손잡이: 자리를 몇 시간까지 기다릴 것인가 (봇은 지금 다음 4h 봉 마감까지)
        import backtest_entry as E
        print("\n── 하한 재확보: 진입 창 길이 (기다리는 시간) ──")
        print("   창을 넘기면 자리를 포기한다. 길게 기다릴수록 놓치는 자리는 줄지만 늦게 산다.")
        print("   ※ 6·8시간은 다음 4h 봉으로 넘어가므로 체결 시각을 진입봉에 눌러 재는 근사가 섞인다 (4h 이하는 정확).")
        keep = E.WIN_H
        for h in (2, 3, 4, 6, 8):
            E.WIN_H = h
            f2 = reclaim_fills()
            skipped = sum(v is None for v in f2.values())
            def lev_of(i, c):
                v = acct_vol(i, c, 40)
                return MAX_LEV if v is None else min(MAX_LEV, 40 / v)
            report(f"창 {h}h · 포기 {skipped:2d}건 · 3배", *simulate(h4, entry, exit_, lambda i, c: 3, fills=f2))
            report(f"창 {h}h · 포기 {skipped:2d}건 · +VT", *simulate(h4, entry, exit_, lev_of, fills=f2))
            print()
        E.WIN_H = keep

    print("\n── 참고 ──")
    report("BTC 상시보유 1배", h4.close / h4.close.iloc[0], np.zeros(1), 0)

    if "--robust" in sys.argv:
        # 채택 후보는 이웃값에서도 버텨야 한다 (저쪽이 '타겟 25~35%·창 40~120일 모두 견고' 를 확인한 것과 같은 절차)
        print("\n══ 견고성: VT(계좌) 타겟 × 창 ══")
        for win in (40, 60, 90, 120):
            for tg in (15, 20, 30, 40, 50):
                def lev_of(i, c, tg=tg, win=win):
                    v = acct_vol(i, c, win)
                    return MAX_LEV if v is None else min(MAX_LEV, tg / v)
                report(f"VT(계좌) {tg}% · 창{win}일", *simulate(h4, entry, exit_, lev_of))
            print()

        print("══ 견고성: 고정 만기 청산 (이웃값) ══")
        for hd in (1, 2, 3, 4, 6, 8):
            report(f"3배 +만기 {hd}일", *simulate(h4, entry, exit_, lambda i, c: 3, hold_days=hd))

        print("\n══ 조합: VT(계좌) + 만기 청산 ══")
        for tg in (20, 30, 40):
            for hd in (3, 5):
                def lev_of(i, c, tg=tg):
                    v = acct_vol(i, c)
                    return MAX_LEV if v is None else min(MAX_LEV, tg / v)
                report(f"VT {tg}% + 만기 {hd}일", *simulate(h4, entry, exit_, lev_of, hold_days=hd))

    # 자체 점검: 배선이 맞는지. 극단값에서 기준선과 같아져야 한다
    huge = simulate(h4, entry, exit_, lambda i, c: min(MAX_LEV, 1e9 / max(acct_vol(i, c) or 1, 1e-9)))
    assert abs(huge[0].iloc[-1] - base[3][0].iloc[-1]) < 1e-9, "타겟이 무한대면 3배 고정과 같아야 한다"
    forever = simulate(h4, entry, exit_, lambda i, c: 3, hold_days=10_000)
    assert abs(forever[0].iloc[-1] - base[3][0].iloc[-1]) < 1e-9, "만기가 무한대면 구조 청산만 남아야 한다"
    zero = simulate(h4, entry, exit_, lambda i, c: 0.0)
    assert zero[0].iloc[-1] == 1.0 and len(zero[1]) == 1, "레버리지 0 이면 아무것도 안 산다"
    print("\nok  자체 점검 통과 (타겟 무한대 = 기준선 · 만기 무한대 = 기준선 · 레버리지 0 = 무거래)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
```

## `backtest_both.py`

```python
"""양방향(롱·숏) 매매 검증 — 국면별로 나눠 승률·수익률을 잰다.

용어는 사용자 기준으로 통일: **롱 = 상승에 거는 것, 숏 = 하락에 거는 것.** 청산은 방향이 아니라 행동이다.
(예전 코드의 zone="short" 는 '청산·진입 금지' 였다. model.py 에서 exit_long/exit_short 로 갈라냈다.)

규칙 (model.signal() 과 같다. 롱 규칙을 그대로 뒤집은 것이 숏이다)
  롱  국면 = 일봉 강세 규칙 CONF개↑ · 진입 = 4h 종가 > 직전 12봉 고가 · 청산 = 4h 종가 < 직전 6봉 저가 or 국면 붕괴
  숏  국면 = 일봉 약세 규칙 CONF개↑ · 진입 = 4h 종가 < 직전 12봉 저가 · 청산 = 4h 종가 > 직전 6봉 고가 or 국면 붕괴
  롱 국면과 숏 국면은 동시에 참일 수 없으므로 한 번에 한 포지션만 잡힌다. 방향 전환은 청산 후 다음 봉부터.

국면 분류 (quant_nasq100 backtest_volume.DIR_BAND=3.0 과 같은 정의를 BTC 자신에게 적용)
  BTC 60일 수익률 > +3% 상승 · < −3% 하락 · 그 사이 횡보. 전일까지의 정보만 쓴다.

체결·비용·평가는 backtest_quant.py 와 동일 (편도 0.07%, 격리 청산, 탐색/검증 분할, Sharpe = CAGR/연율변동성).
펀딩은 **숏에 유리하게** 잡았다 — 롱이 내는 0.01%/8h 를 숏이 받는 것으로 본다. 하락장에서는 펀딩이
음전(숏이 내는 쪽)하는 일이 잦으므로 아래 숏 성적은 낙관 쪽으로 치우친 값이다.

사용: python backtest_both.py [편도비용]
"""
import sys

import numpy as np
import pandas as pd

import backtest_okx as B
import backtest_quant as Q
import model as M

DIR_BAND = 3.0            # 60일 수익률 ±3% 를 횡보로 본다 (quant_nasq100 과 같은 값)


def bear(d):
    """일봉 약세 규칙 개수 — backtest_okx.bull() 의 거울."""
    x = M.add_indicators(d.copy())
    f = pd.DataFrame(index=d.index)
    f["ret_5d"], f["ret_20d"] = d.close.pct_change(5) * 100, d.close.pct_change(20) * 100
    return sum(s(x, f).astype(int) for _, _, s, _ in M.RULES)


def frames(n=None, m=None):
    """(4h 봉, 롱진입, 롱청산, 숏진입, 숏청산, 국면라벨). n·m 은 돌파·이탈 봉수 (기본 = 봇 값 12/6)."""
    n, m = n or M.H4_N, m or M.H4_M
    d1, h4 = B.fetch("1d"), B.fetch("4h")

    def regime(cnt):
        r = (cnt >= M.CONF)
        r.index = r.index + pd.Timedelta(days=1)                 # 일봉이 닫힌 다음 날부터 유효
        return r.reindex(h4.index, method="ffill").fillna(False).astype(bool)

    up, dn = regime(B.bull(d1)), regime(bear(d1))
    hi_n, lo_n = h4.high.rolling(n).max().shift(1), h4.low.rolling(n).min().shift(1)
    hi_m, lo_m = h4.high.rolling(m).max().shift(1), h4.low.rolling(m).min().shift(1)

    ret60 = ((d1.close / d1.close.shift(60) - 1) * 100).shift(1)   # 전일까지의 정보
    lab = pd.Series(np.where(ret60 > DIR_BAND, "상승", np.where(ret60 < -DIR_BAND, "하락", "횡보")), index=d1.index)
    lab = lab.reindex(h4.index, method="ffill")
    return (h4,
            ((h4.close > hi_n) & up).values, ((h4.close < lo_m) | ~up).values,
            ((h4.close < lo_n) & dn).values, ((h4.close > hi_m) | ~dn).values,
            lab.values)


def simulate(h4, sig, lev_of, lab, allow=("long", "short"), fills=None, gate=None):
    """한 번에 한 포지션. 봉 i 종가 신호 → i+1 시가 체결.
    fills: {진입봉 시각: 체결가 or None} — 분봉 하한 재확보 진입 (None 이면 그 자리 포기).
           **롱에만 적용된다.** 숏까지 켜고 비교할 때는 숏이 원시 시장가라 불리해진다는 점을 감안할 것
           (거울 규칙으로 숏에도 붙여 재본 결과는 research_both_sides.txt '후속 2': -72%→-63%, 부호는 그대로).
    gate:  진입을 추가로 막는 bool 배열 (EXTREME_MIN 같은 필터).
    → (자본곡선, 거래 DataFrame[side, ret, regime], 청산횟수)"""
    l_en, l_ex, s_en, s_ex = sig
    assert 0 <= Q.COST < 0.05, f"편도 비용 {Q.COST} 가 상식 밖이다 (backtest_okx.arg_cost 주석 참고)"
    o, lo, hi, c, idx = h4.open.values, h4.low.values, h4.high.values, h4.close.values, h4.index
    n = len(h4)
    eq, held, rows, liq = 1.0, None, [], 0
    curve = np.ones(n)
    for i in range(1, n):
        if held:
            e, k, lev, side, reg = held
            k += 1
            blown = (lo[i] <= e * (1 - 1 / lev)) if side > 0 else (hi[i] >= e * (1 + 1 / lev))
            if blown:                                             # 격리 청산 → 증거금 전액 손실
                eq, held, liq = 0.0, None, liq + 1
                rows.append({"side": "롱" if side > 0 else "숏", "ret": -1.0, "regime": reg})
            elif (l_ex if side > 0 else s_ex)[i - 1]:
                r = side * lev * (o[i] / e - 1) - 2 * lev * Q.COST - side * k * Q.FUND * lev
                eq *= 1 + r
                rows.append({"side": "롱" if side > 0 else "숏", "ret": r, "regime": reg})
                held = None
            else:
                held = (e, k, lev, side, reg)
        if held is None and eq > 0 and (gate is None or gate[i - 1]):
            side = 1 if ("long" in allow and l_en[i - 1]) else -1 if ("short" in allow and s_en[i - 1]) else 0
            if side:
                px = o[i] if (fills is None or side < 0) else fills.get(idx[i], o[i])
                lev = lev_of(i, curve)
                if lev > 0 and px is not None:
                    held = (px, 0, lev, side, lab[i - 1])
        curve[i] = eq * (1 + held[3] * held[2] * (c[i] / held[0] - 1)) if held else eq
        if eq <= 0:
            curve[i:] = 0.0
            break
    t = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["side", "ret", "regime"])
    return pd.Series(curve, index=h4.index), t, liq


def line(name, curve, t, liq):
    Q.report(name, curve, t.ret.values if len(t) else np.zeros(1), liq)


def by_regime(name, t):
    """국면 × 방향별 거래수·승률·평균수익·합산기여."""
    if not len(t):
        return
    print(f"\n  [{name}]  {'국면':<6}{'방향':<5}{'거래':>5}{'승률':>7}{'평균':>8}{'합산(단리)':>12}{'최악':>8}")
    for reg in ("상승", "횡보", "하락"):
        for side in ("롱", "숏"):
            x = t[(t.regime == reg) & (t.side == side)]
            if not len(x):
                continue
            print(f"  {'':<10}{reg:<8}{side:<7}{len(x):>3}{(x.ret > 0).mean() * 100:>6.0f}%"
                  f"{x.ret.mean() * 100:>+8.2f}%{x.ret.sum() * 100:>+10.0f}%{x.ret.min() * 100:>+8.1f}%")
    x = t
    print(f"  {'':<10}{'전체':<8}{'':<7}{len(x):>3}{(x.ret > 0).mean() * 100:>6.0f}%"
          f"{x.ret.mean() * 100:>+8.2f}%{x.ret.sum() * 100:>+10.0f}%{x.ret.min() * 100:>+8.1f}%")


def main():
    h4, l_en, l_ex, s_en, s_ex, lab = frames()
    m = h4.index >= pd.Timestamp(Q.START, tz="UTC")
    h4, lab = h4[m], lab[m]
    sig = (l_en[m], l_ex[m], s_en[m], s_ex[m])
    days = pd.Series(lab, index=h4.index).resample("D").last().dropna()
    share = days.value_counts(normalize=True) * 100
    print(f"OKX BTC 4h {h4.index[0]:%Y-%m-%d}~{h4.index[-1]:%Y-%m-%d} · CONF={M.CONF} · 편도 {Q.COST * 100:.2f}%")
    print(f"국면 비중 (BTC 60일 수익률 ±{DIR_BAND:.0f}%): "
          + " · ".join(f"{k} {share.get(k, 0):.0f}%" for k in ("상승", "횡보", "하락")))
    print("펀딩은 숏이 받는 쪽으로 가정 — 숏에 유리한 값이다\n")
    print(f"{'':22s}{'──── 탐색 ────':>22} |{'──── 검증 ────':>22} |{'────── 전체 ──────':>28}"
          f"{'거래':>5}{'승률':>6}{'최악':>7}{'청산':>4}")
    print(f"{'':22s}{'CAGR':>7}{'MDD':>7}{'Sh':>6} |{'CAGR':>7}{'MDD':>7}{'Sh':>6} |"
          f"{'누적':>9}{'MDD':>7}{'Sh':>6}")

    runs = {}
    print("\n── 방향별 (고정 3배) ──")
    for name, allow in (("롱만", ("long",)), ("숏만", ("short",)), ("롱+숏", ("long", "short"))):
        runs[name] = simulate(h4, sig, lambda i, c: 3, lab, allow)
        line(f"{name} 3배", *runs[name])

    print("\n── 1배 ──")
    for name, allow in (("롱만", ("long",)), ("숏만", ("short",)), ("롱+숏", ("long", "short"))):
        line(f"{name} 1배", *simulate(h4, sig, lambda i, c: 1, lab, allow))

    print("\n── 변동성 타겟팅(계좌 40%·창60일)을 얹으면 ──")
    def vt(i, c):
        v = Q.acct_vol(i, c)
        return Q.MAX_LEV if v is None else min(Q.MAX_LEV, 40 / v)
    for name, allow in (("롱만", ("long",)), ("롱+숏", ("long", "short"))):
        line(f"{name} +VT", *simulate(h4, sig, vt, lab, allow))

    print("\n\n═══ 국면 × 방향별 거래 성적 (고정 3배) ═══")
    for name in ("롱만", "숏만", "롱+숏"):
        by_regime(name, runs[name][1])

    print("\n\n═══ 이웃 파라미터 (한 조합에만 걸린 결과인지) ═══")
    for n, k in ((30, 15), (6, 3)):
        h2, a, b, c2, d, lab2 = frames(n, k)
        mm = h2.index >= pd.Timestamp(Q.START, tz="UTC")
        for name, allow in (("롱만", ("long",)), ("숏만", ("short",)), ("롱+숏", ("long", "short"))):
            line(f"{name} 3배 · {n}/{k}봉", *simulate(h2[mm], (a[mm], b[mm], c2[mm], d[mm]), lambda i, c: 3, lab2[mm], allow))
        print()

    print("── 참고 ──")
    line("BTC 상시보유 1배", h4.close / h4.close.iloc[0], pd.DataFrame(columns=["side", "ret", "regime"]), 0)

    # 자체 점검: 방향 배선. 계속 오르기만 하는 봉에서 롱은 벌고 숏은 잃어야 한다
    up = pd.DataFrame({"open": np.linspace(100, 200, 60), "close": np.linspace(100, 200, 60)},
                      index=pd.date_range("2024-01-01", periods=60, freq="4h", tz="UTC"))
    up["high"], up["low"] = up.close + 1, up.close - 1
    en = np.zeros(60, bool); en[0] = True
    ex = np.zeros(60, bool); ex[50] = True
    no = np.zeros(60, bool)
    lb = np.array(["상승"] * 60)
    assert simulate(up, (en, ex, no, no), lambda i, c: 1, lb)[1].ret.iloc[0] > 0.4, "상승 구간 롱은 벌어야 한다"
    assert simulate(up, (no, no, en, ex), lambda i, c: 1, lb)[1].ret.iloc[0] < -0.4, "상승 구간 숏은 잃어야 한다"
    both = simulate(up, (en, ex, en, ex), lambda i, c: 1, lb)[1]
    assert len(both) == 1 and both.side.iloc[0] == "롱", "한 번에 한 포지션만 (롱 우선)"
    print("\nok  자체 점검 통과 (상승 구간에서 롱 이익 · 숏 손실 · 동시 보유 없음)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
```

## `backtest_current.py`

```python
"""지금 .env 설정 그대로 백테스트 — "현재 알고리즘의 수익률·승률은 얼마인가" 에 답하는 파일.

설정을 손으로 옮겨 적지 않고 `.env` 를 직접 읽는다. 그래야 봇과 백테스트가 어긋날 수 없다.
반영하는 것: CONF · LEVERAGE · VOL_TARGET_PCT/VOL_WINDOW · ENTRY_FLOOR · ALLOW_SHORT · EXTREME_MIN
표본: OKX BTC-USDT-SWAP 4h 2021-03-01~ · 편도 0.07% · 펀딩 0.01%/8h · 격리 청산 · 탐색/검증 분할

숏이 꺼져 있으면(ALLOW_SHORT=0) 아래 '롱/숏 비율' 은 자동으로 롱 100% 가 된다. 그래도 숏 자리가
얼마나 자주 났는지, 켰다면 어떻게 됐을지는 같이 보여 준다 — 무엇을 포기하고 있는지 알아야 하니까.

사용: python backtest_current.py          현재 설정의 수익률·승률·롱숏 비율
      python backtest_current.py --lev    레버리지를 올리면 어디서 청산되는지 (2026-09-10: 고정 7배부터 전액 손실)
"""
import sys

import numpy as np
import pandas as pd
from dotenv import dotenv_values

import backtest_both as BO
import backtest_quant as Q
import model as M
import reversion as R

ENV = {**dotenv_values(Q.M.ROOT / ".env")}


def cfg(k, d):
    v = (ENV.get(k) or "").split("#")[0].strip()
    return v if v else d


LEV = float(cfg("LEVERAGE", "3"))
VT_PCT = float(cfg("VOL_TARGET_PCT", "40"))
VT_WIN = int(cfg("VOL_WINDOW", "40"))
FLOOR = cfg("ENTRY_FLOOR", "1") != "0"
SHORT = cfg("ALLOW_SHORT", "0") != "0"
EXT = int(cfg("EXTREME_MIN", "0"))


def by_side(t, label):
    """방향별 거래수·비중·승률·평균·합산기여."""
    if not len(t):
        print(f"  {label}: 거래 없음")
        return
    print(f"\n  [{label}]  {'방향':<6}{'거래':>5}{'비중':>7}{'승률':>7}{'평균':>9}{'합산(단리)':>12}{'최악':>9}")
    for side in ("롱", "숏"):
        x = t[t.side == side]
        if not len(x):
            print(f"  {'':<10}{side:<8}{0:>3}{0:>7.0f}%{'':>7}{'':>9}{'':>12}")
            continue
        print(f"  {'':<10}{side:<8}{len(x):>3}{len(x) / len(t) * 100:>6.0f}%{(x.ret > 0).mean() * 100:>6.0f}%"
              f"{x.ret.mean() * 100:>+9.2f}%{x.ret.sum() * 100:>+11.0f}%{x.ret.min() * 100:>+9.1f}%")
    print(f"  {'':<10}{'전체':<8}{len(t):>3}{100:>6.0f}%{(t.ret > 0).mean() * 100:>6.0f}%"
          f"{t.ret.mean() * 100:>+9.2f}%{t.ret.sum() * 100:>+11.0f}%{t.ret.min() * 100:>+9.1f}%")


def main():
    print("═══ 현재 .env 설정 ═══")
    print(f"  CONF={M.CONF} · LEVERAGE 상한 {LEV:g}배 · ENTRY_FLOOR={'켜짐(분봉 하한 재확보)' if FLOOR else '꺼짐(신호 즉시)'}")
    print(f"  변동성 타겟 {VT_PCT:g}% / 창 {VT_WIN}일" if VT_PCT else "  변동성 타겟: 꺼짐 (고정 레버리지)")
    print(f"  ALLOW_SHORT={'켜짐' if SHORT else '꺼짐 → 롱 전용'} · EXTREME_MIN={EXT}{' (꺼짐)' if not EXT else ''}")

    h4, l_en, l_ex, s_en, s_ex, lab = BO.frames()
    m = h4.index >= pd.Timestamp(Q.START, tz="UTC")
    h4, lab = h4[m], lab[m]
    sig = (l_en[m], l_ex[m], s_en[m], s_ex[m])
    fills = Q.reclaim_fills() if FLOOR else None
    hi_s, lo_s = R.scores(h4)
    gate = ((hi_s >= EXT) | (lo_s >= EXT)).values if EXT else None
    allow = ("long", "short") if SHORT else ("long",)

    def lev_of(i, c):
        if not VT_PCT:
            return LEV
        v = Q.acct_vol(i, c, VT_WIN)
        return LEV if v is None else min(LEV, VT_PCT / v)

    print(f"\n표본 {h4.index[0]:%Y-%m-%d}~{h4.index[-1]:%Y-%m-%d} ({(h4.index[-1] - h4.index[0]).days / 365:.1f}년) · "
          f"편도 {Q.COST * 100:.2f}% · 탐색 {Q.START}~{Q.SPLIT} / 검증 {Q.SPLIT}~")
    print(f"\n{'':22s}{'──── 탐색 ────':>22} |{'──── 검증 ────':>22} |{'────── 전체 ──────':>28}"
          f"{'거래':>5}{'승률':>6}{'최악':>7}{'청산':>4}")
    print(f"{'':22s}{'CAGR':>7}{'MDD':>7}{'Sh':>6} |{'CAGR':>7}{'MDD':>7}{'Sh':>6} |"
          f"{'누적':>9}{'MDD':>7}{'Sh':>6}")

    print("\n── 현재 설정 ──")
    cur = BO.simulate(h4, sig, lev_of, lab, allow, fills=fills, gate=gate)
    BO.line("[지금 이 봇]", *cur)

    print("\n── 비교: 무엇이 얼마를 만드는가 (하나씩 켜고 끄면) ──")
    if VT_PCT:
        BO.line(f"VT 끄고 고정 {LEV:g}배", *BO.simulate(h4, sig, lambda i, c: LEV, lab, allow, fills=fills, gate=gate))
    else:
        BO.line(f"VT 켜면 (타겟 40%·창40)", *BO.simulate(
            h4, sig, lambda i, c: (lambda v: LEV if v is None else min(LEV, 40 / v))(Q.acct_vol(i, c, 40)),
            lab, allow, fills=fills, gate=gate))
    BO.line("하한 재확보 끄면", *BO.simulate(h4, sig, lev_of, lab, allow, fills=None, gate=gate))
    BO.line(f"기본 규칙만 ({LEV:g}배)", *BO.simulate(h4, sig, lambda i, c: LEV, lab, allow, fills=None, gate=None))
    for L in (1, 2, 3):
        if L < LEV:
            BO.line(f"{L}배로 낮추면", *BO.simulate(h4, sig, lambda i, c, L=L: L, lab, allow, fills=fills, gate=gate))
    BO.line("BTC 상시보유 1배", h4.close / h4.close.iloc[0], pd.DataFrame(columns=["side", "ret", "regime"]), 0)

    print("\n\n═══ 롱/숏 비율 ═══")
    by_side(cur[1], "지금 이 봇")
    if not SHORT:
        n_s = int((sig[2]).sum())
        print(f"\n  숏은 ALLOW_SHORT=0 이라 한 건도 없다 → **롱 100% / 숏 0%**")
        print(f"  다만 숏 '자리' 자체는 표본 기간에 {n_s}번 났다 (롱 자리 {int(sig[0].sum())}번).")
        print(f"  켰다면 어떻게 됐을지:")
        both = BO.simulate(h4, sig, lev_of, lab, ("long", "short"), fills=fills, gate=gate)
        BO.line("  숏까지 켠 경우", *both)
        by_side(both[1], "숏까지 켠 경우")
        print(f"\n  → 숏을 켜면 누적이 {cur[0].iloc[-1] * 100 - 100:+.0f}% 에서 "
              f"{both[0].iloc[-1] * 100 - 100:+.0f}% 로 바뀐다 (research_both_sides.txt)")

    print("\n\n═══ 국면별 (현재 설정) ═══")
    BO.by_regime("지금 이 봇", cur[1])

    print("\n\n═══ 연도별 수익률 (현재 설정) ═══")
    d = cur[0].resample("YE").last()
    yr = (d / d.shift(1).fillna(1) - 1) * 100
    print("   " + " · ".join(f"{t.year} {v:+.0f}%" for t, v in yr.items()))

    if "--lev" in sys.argv:
        print("\n\n═══ 레버리지 스윕 — 어디까지 살아남나 ═══")
        print("  격리 마진이라 진입가 대비 1/lev 만큼 역행하면 그 포지션은 전액 손실이다.")
        print("  10배면 -10%, 5배면 -20%. 4h 봉 하나 안에서 일어날 수 있는 크기다.\n")
        print(f"  {'상한':>5}{'평균lev':>8}{'누적':>12}{'MDD':>9}{'Sh':>7}{'거래':>6}{'승률':>6}{'최악':>8}{'청산':>6}")
        for L in (1, 2, 3, 5, 7, 10, 15, 20):
            c, t, lq = BO.simulate(h4, sig, lambda i, cv, L=L: L, lab, allow, fills=fills, gate=gate)
            f = Q.metrics(c, t.ret.values if len(t) else np.zeros(1), lq)
            print(f"  {L:>4}배{L:>8.2f}{f['누적']:>+11.0f}%{f['mdd']:>8.1f}%{f['sharpe']:>7.2f}"
                  f"{f['거래']:>6}{f['승률']:>5.0f}%{f['최악']:>7.1f}%{lq:>5}"
                  + ("   ← 전액 손실" if lq else ""))

        print(f"\n  변동성 타겟({VT_PCT:g}%·창{VT_WIN}일)을 얹고 상한만 올리면:")
        print(f"  {'상한':>5}{'평균lev':>8}{'누적':>12}{'MDD':>9}{'Sh':>7}{'거래':>6}{'승률':>6}{'최악':>8}{'청산':>6}")
        for L in (3, 5, 7, 10, 15, 20):
            levs = []

            def vt(i, cv, L=L):
                v = Q.acct_vol(i, cv, VT_WIN)
                x = L if v is None else min(L, VT_PCT / v)
                levs.append(x)
                return x

            c, t, lq = BO.simulate(h4, sig, vt, lab, allow, fills=fills, gate=gate)
            f = Q.metrics(c, t.ret.values if len(t) else np.zeros(1), lq)
            print(f"  {L:>4}배{np.mean(levs):>8.2f}{f['누적']:>+11.0f}%{f['mdd']:>8.1f}%{f['sharpe']:>7.2f}"
                  f"{f['거래']:>6}{f['승률']:>5.0f}%{f['최악']:>7.1f}%{lq:>5}"
                  + ("   ← 전액 손실" if lq else ""))

        # 실제로 진입 뒤 얼마나 역행했는지 — 청산선이 어디에 놓이는지 눈으로 본다
        base = BO.simulate(h4, sig, lambda i, c: 1, lab, allow, fills=fills, gate=gate)
        o, lo = h4.open.values, h4.low.values
        l_en2, l_ex2 = sig[0], sig[1]
        worst, held = [], None
        for i in range(1, len(h4)):
            if held is not None:
                e, mn = held
                mn = min(mn, lo[i] / e - 1)
                if l_ex2[i - 1]:
                    worst.append(mn * 100)
                    held = None
                else:
                    held = (e, mn)
            if held is None and l_en2[i - 1]:
                px = o[i] if fills is None else fills.get(h4.index[i], o[i])
                if px is not None:
                    held = (px, 0.0)
        w = np.array(worst)
        print(f"\n  거래 {len(w)}건의 '진입 후 최대 역행' 분포 (저가 기준, 배율 무관):")
        for q in (50, 75, 90, 95, 99, 100):
            print(f"    {q:3d}분위 {np.percentile(w, 100 - q):+7.1f}%"
                  + f"   → {100 / abs(np.percentile(w, 100 - q)):.1f}배 넘으면 이 거래에서 청산" if q >= 90 else "")
        for L in (3, 5, 10, 20):
            print(f"    {L:2d}배 청산선 -{100 / L:.0f}% 를 넘긴 거래: {(w <= -100 / L).sum()}건 / {len(w)}건")

    # 자체 점검: 숏이 꺼져 있으면 숏 거래가 하나도 없어야 한다
    if not SHORT:
        assert (cur[1].side == "숏").sum() == 0, "ALLOW_SHORT=0 인데 숏 거래가 잡혔다"
    assert 0 <= Q.COST < 0.05
    print("\nok  자체 점검 통과 (설정과 거래 방향 일치)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
```

## `instructions.md` — 주문 직전 LLM 검토 프롬프트 (봇이 Claude 에게 보내는 시스템 프롬프트)

```markdown
# 역할

당신은 BTC 자동매매 봇(OKX USDT 무기한 선물, 격리 마진, 레버리지)의 **진입 직전 검토 담당**이다. 봇은 롱(상승 베팅) 또는 숏(하락 베팅) 으로 진입하며, 어느 쪽인지는 `proposal.side` 에 있다. 이 검토는 봇이 **정말 주문을 내기 직전에** 한 번 호출된다 — 자리가 났을 때가 아니라, 분봉이 진입선을 지켜 '지금 산다' 가 확정된 그 순간이다. 그래서 네가 보는 `entry.m1` 과 `proposal.price` 는 실제 체결 조건과 같다. 네가 거부하면 그 자리는 접는다. 두 가지를 보고 `approve` 를 결정한다.

1. **알고리즘 값 대조** — 봇이 계산한 `analysis` 가 근거 데이터 `daily`·`h4` 와 맞아떨어지는가.
2. **사건·급변 거부** — 정량 신호가 모르는 이유로 지금 들어가면 안 되는가.

수량·시점은 코드가 정한다. 당신은 **거부권만** 갖는다.

# 봇이 자리를 고르는 방법 (OKX 2021-03~2026-09 검증)

- **국면**: 마지막 완성 일봉에서 8개 규칙(20일선·50일선 배열, EMA10, MACD 히스토그램, RSI, 볼린저 위치, 5일·20일 수익률) 중 **`conf`개 이상 강세**(현재 6)면 강세 국면(`regime`).
- **롱 진입**: 강세 국면 & 4h 종가 `close` > 직전 `h4_n`(12)봉 고가 `hi` (`breakout`) → zone `long`.
- **숏 진입**: 약세 국면(`bear ≥ conf`) & 4h 종가 < 직전 `h4_n`(12)봉 저가 `lo_n` (`breakdown`) → zone `short`.
  숏은 백테스트가 음수라 기본적으로 꺼져 있다. 호출됐다면 사람이 켠 것이다.
- **청산**: 롱은 4h 종가 < `lo`(6봉 저가) 또는 강세 국면 붕괴 → `exit_long`. 숏은 4h 종가 > `hi_m`(6봉 고가) 또는 약세 국면 붕괴 → `exit_short`.
- zone `wait` = 진입 자리 없음. **zone 은 진입 방향이지 청산 신호가 아니다.**
- **진입 타이밍(하한 방어)**: 네가 승인해도 봇은 곧장 사지 않는다. 돌파당한 고가 `entry.floor`(= `hi`)를
  1분봉이 지키거나(한 번도 밑돌지 않음) 잃었다가 되찾을 때(분봉 종가가 다시 그 위) 산다.
  다음 4h 봉이 닫힐 때까지 못 지키면 그 자리는 포기한다. 검증(backtest_entry.py, 이벤트 107건):
  3배 즉시 매수 +492%/MDD −38.9% → 하한 확인 후 매수 +1,156%/−35.2%, 포기한 4건은 전부 손실 거래였다 (2026-09-09 재측정).
  더 싸게 사려는 규칙(지정가 −0.3~1.5%, 되돌림 매수)은 전부 즉시 매수보다 나빴다.
- 검증(CONF=6, 2021-03~2026-09-09): 1배 +135% / MDD −23%, 거래 108회, 승률 34%, 최악 거래 −5.8%. 승률이 낮고 큰 추세 몇 번이 수익을 만드는 구조다.
- **정량 신호 자체를 재평가하지 마라.** 규칙과 문턱은 검증된 값이다.

# 1. 알고리즘 값 대조 — 거부(approve=false) 사유

- `analysis.rules` 의 판정이 `daily` 마지막 봉과 모순된다 (예: "종가 vs 20일 이평" 강세인데 daily 마지막 close < sma20).
- `analysis.bull`·`analysis.bear` 가 규칙의 판정 개수와 다르다. `bull < conf` 인데 `bull_regime` 이 true 다.
- zone 이 long 인데 `bull_regime` 또는 `breakout` 이 false 다. zone 이 short 인데 `bear_regime` 또는 `breakdown` 이 false 다.
- `proposal.side` 가 `analysis.zone` 과 다르다.
- `analysis.close` 가 `h4` 마지막 봉의 close 와 다르다. `hi` 가 `h4` 의 마지막 봉을 뺀 직전 12봉 고가의 최댓값과 다르다. `close ≤ hi` 인데 breakout 이 true 다.
- `analysis.date` 가 `daily` 마지막 봉 날짜가 아니거나, `analysis.bar` 가 `h4` 마지막 봉 시각이 아니다 (오래된 봉으로 판단).
- `proposal.notional_usdt` 가 `equity_usdt × leverage` 와 크게 다르다, 또는 `leverage` 가 **5** 를 넘는다.
  (봇의 코드 상한이 5배다. 7배부터는 백테스트에서 전액 청산이므로 5 초과는 설정 사고로 보고 거부한다.)
  `leverage` 는 3 고정이 아니다 — 봇이 계좌 실현변동성으로 3배 이하에서 매번 다시 정한다(변동성 타겟팅). 1.4 배 같은 소수여도 정상이고, **낮다는 것 자체는 거부 사유가 아니다.** 볼 것은 명목이 그 leverage 와 맞아떨어지는가뿐이다.
- daily·h4 에 결측·0·비정상 값(고가 < 저가 등)이 있다.

# 2. 사건·급변 — 거부 사유

- **분봉 붕괴**: `entry.m1` 이 있고 그 안에서 가격이 `entry.floor` 를 뚜렷하게(−0.5% 이상) 밑돌아 회복하지 못하고 있다면
  자리가 무너지는 중이다. 거부해도 된다 — 봇의 하한 확인이 어차피 대기시키지만, 회복 가망이 없다고 보면 아예 접는 게 맞다.
  반대로 `m1` 이 하한 위에 있다는 것이 승인 사유가 되지는 않는다. 그건 봇이 이미 보는 조건이다.

- **돌파 뒤 급변**: `market.today_pct`(기준 일봉 종가 대비 현재가)가 −5% 이하 (자리가 이미 무너짐) 또는 +8% 이상 (이미 지나감). 현재가 `proposal.price` 가 4h 이탈선 `lo` 아래면 진입 전에 청산 조건이라 거부.
- **거래소·시장 구조 사건**: OKX 장애·출금 중단, USDT/USDC 디페깅, 대형 거래소 파산, 연쇄 청산 진행. 당신이 아는 사실에 한한다. 뉴스는 제공되지 않는다.
- **레버리지 청산 위험**: 최근 15일 중 하루 변동폭(high/low−1)이 (1/leverage)의 절반을 넘는 날이 여럿이면 격리 증거금이 하루에 날아갈 수 있다. 사유에 명시하고 거부.

# 거부 사유가 아닌 것

- "RSI 가 높다", "볼린저 상단이다", "많이 올랐다", "고점 돌파는 추격매수다". 이 전략은 고점 돌파에만 들어가며, 과열은 이후 수익률을 낮추지 **않는다** — 오히려 높인다.
  2026-09-10 재확인 (BTC 10.7년 + OKX 5.5년): '고점처럼 보이는' 자리 뒤 12봉 수익률이 전체 평균 +0.13% 대비
  +0.93% 다. 추세를 통제해도 +0.79% 이고 순열 검정 p=0.003 이다. (겹치지 않는 표본만 쓰면 t=1.80 으로
  약해지므로 크기는 이보다 작게 볼 것 — research_bias.txt.) 방향은 분명하다:
  `analysis.extreme_hi` 가 높다는 것은 **승인 쪽 근거이지 거부 사유가 아니다.** 낮다고 거부하는 것도 근거가 없다.
- "승률이 낮다", "확신이 안 선다", "변동성이 크다". 비트코인은 원래 그렇다.
- 뉴스가 없다는 것. 뉴스 없음이 기본 상태다.

# 제공 데이터

- `analysis`: `date` 기준 일봉, `bar` 기준 4h 봉 시각, `bull`(롱 쪽 규칙 수)/`bear`(숏 쪽 규칙 수), `regime`, `close`/`hi`/`lo`, `breakout`/`breakdown`, `zone`, `exit_long`/`exit_short`, `bull_regime`/`bear_regime`, `extreme_hi`/`extreme_lo`(4h 극단 점수 0~7), `close`/`hi`/`lo`/`lo_n`/`hi_m`, `conf`, `h4_n`/`h4_m`, `rules[]`(name, bull, bear, value).
- `proposal`: `action`, `side`, `notional_usdt`, `leverage`, `equity_usdt`, `price`(현재가).
- `market`: `today_pct`(기준 일봉 종가 대비 현재가), `ret_5d`, `ret_20d`.
- `daily`: 최근 15일 일봉 (open/high/low/close, chg_pct, vol_btc, rsi, sma20, sma50). 마지막 행이 기준봉.
- `h4`: 최근 13개 4h 봉 (time, open/high/low/close). 마지막 행이 기준봉, 그 앞 12개가 `hi` 의 근거.
- `entry`: `floor`(봇이 지키는지 볼 선 — 롱은 `hi`, 숏은 `lo_n`), `side`, `rule`(위 규칙 요약), `m1`(최근 60개 1분봉 — 비어 있을 수도 있다).

# 출력

JSON 객체 하나만. 다른 텍스트 없이.

```json
{"approve": true, "reason": "규칙 7/8 강세가 daily 와 일치, 4h 종가 81,250 > 직전 12봉 고가 80,900, 기준봉 최신. 당일 +1.2% 로 자리 유효."}
```
```json
{"approve": false, "reason": "analysis.hi 80,900 인데 h4 직전 12봉 고가 최댓값은 81,600. 값 불일치."}
```

- `reason` 은 한국어 1~2문장, 어떤 수치가 근거인지 적는다.
```
