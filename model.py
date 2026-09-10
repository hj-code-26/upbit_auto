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
ALIGN_LEGACY = False         # True 면 수정 전 일봉→4h 정렬을 쓴다 (audit/compare.py 비교 전용)
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


def align_daily(s, h4_index):
    """일봉에서 만든 값(국면 등)을 4h 봉에 붙인다. **기준은 그 4h 봉이 닫히는 시각**이다 (2026-09-10 감사).

    봇은 4h 봉 i 가 닫힌 직후에 판단하므로, 그때 완성돼 있는 마지막 일봉을 쓴다.
    4h 봉 20:00 은 00:00 에 닫히고 일봉도 바로 그 순간 닫힌다 → **그날 일봉을 쓸 수 있다.**
    예전 백테스트는 `일봉 index + 1일` 을 4h 봉의 **시작** 시각에 ffill 해서, 20:00 봉만 하루 묵은
    일봉을 봤다. 실봇보다 하루 늦은 판단이라 성적이 봇의 것이 아니었다 (test_signal.py 2번이 잡는다)."""
    x = s.copy()
    x.index = x.index + pd.Timedelta(days=1)                 # 일봉이 닫히는 시각
    if ALIGN_LEGACY:                                          # 감사 비교용 — 수정 전 동작 (20:00 봉이 하루 묵은 일봉을 본다)
        return x.reindex(h4_index, method="ffill")
    out = x.reindex(h4_index + pd.Timedelta(hours=4), method="ffill")   # 4h 봉이 닫히는 시각에 아는 값
    out.index = h4_index
    return out


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
