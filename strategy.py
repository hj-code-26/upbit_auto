"""급락 매수 전략 — 봇(autotrade.py) · 대시보드 · 재생 점검이 같이 쓰는 규칙 한 곳 (2026-09-27 전략 교체).

근거: research_aoa.txt (워뇨띠 BitMEX 체결 분석 → tpsl.py → sma50.py → defend.py V3 → dip_lev2.py).
  진입 : 완성된 5분봉 종가가 1h EMA(5분봉 12개) 대비 −2% 이하
         & 그 봉 날짜의 '전날' 일봉 종가 > 전날까지 50일 이평 & 200일 이평   → 롱 (숏 없음 — short.py: 216조합 0개 통과)
  청산 : 진입가 +4% 익절 / −6% 손절 (봉이 손절가 아래서 열리면 그 시가) / 864봉(3일) 지나면 청산
         한 봉에서 둘 다 닿으면 손절로 본다. 청산한 봉에서는 새 신호를 보지 않는다.
백테스트 (1배, 비용 0.07%·펀딩 포함, research/aoa/dip_lev2_result.txt):
  BitMEX 2018-21 연 +27.9% / MDD −37% · 바이낸스 2020-26 +20.0% / −16% · OKX 2021-26 +8.7% / −13%
  승률 66~75% · 거래당 +0.85~1.49% · 연 6~33건. 2배는 BitMEX MDD −63% 로 사전등록 불합격 → 1배.
사용: python strategy.py    재생 점검 (OKX 2022~25 → 독립 백테스트 루프 research/aoa/sma50.run 과 거래 단위 대조)
"""
import pathlib
import sys

import pandas as pd

D, TP, SL, MAXB = 2.0, 4.0, 6.0, 864        # 괴리 문턱 % · 익절 % · 손절 % · 최대 보유 5분봉 수(3일)
BAR = pd.Timedelta("5min")
ROOT = pathlib.Path(__file__).resolve().parent


def indicators(c5, d1):
    """c5: 완성된 5분봉(UTC) · d1: 완성된 일봉 종가(UTC 날짜, 200개 이상) → (dev %, bull bool), 5분봉 인덱스.
    bull = 그 봉 날짜의 전날 종가가 50일선 & 200일선 둘 다 위 (당일 미완성 일봉은 안 쓴다)."""
    dev = (c5.close / c5.close.ewm(span=12, adjust=False).mean() - 1) * 100
    ok = (d1 > d1.rolling(50).mean()) & (d1 > d1.rolling(200).mean())
    prev = c5.index.normalize() - pd.Timedelta("1D")
    bull = pd.Series(ok.reindex(prev).fillna(False).values.astype(bool), index=c5.index)
    return dev, bull


def signal(dev, bull):
    return bool(dev <= -D and bull)


def levels(entry):
    return entry * (1 + TP / 100), entry * (1 - SL / 100)


def exit_check(entry, t0, t, o, h, l):
    """진입 봉 t0 (진입가 = 그 봉 시가) 이후 완성 봉 t 하나 → (청산가, 사유) | None."""
    up, dn = levels(entry)
    if int((t - t0) / BAR) >= MAXB:
        return o, "만기"
    if l <= dn:
        return min(dn, o), "손절"
    if h >= up:
        return up, "익절"
    return None


def on_bar(st, t, o, h, l, dev, bull):
    """재생용 상태기계: 신호 봉 다음 봉 시가 진입. → 청산 시 (진입봉, 진입가, 청산가, 사유)."""
    if st["pending"]:
        st.update(pending=False, t0=t, entry=o)
    if st["t0"] is not None:
        r = exit_check(st["entry"], st["t0"], t, o, h, l)
        if r:
            out = (st["t0"], st["entry"], *r)
            st.update(t0=None, entry=None)
            return out
    elif signal(dev, bull):
        st["pending"] = True
    return None


def selftest():
    """OKX 2022~25 5분봉을 on_bar 로 재생 → 독립 루프 research/aoa/sma50.run 과 진입 시각·수익 대조.
    200일선 예열: OKX 데이터 시작 전은 비트스탬프 일봉으로 채운다."""
    import numpy as np
    sys.path.insert(0, str(ROOT / "research" / "aoa"))
    from clone import okx_5m, prep
    from sma50 import run
    c = okx_5m().loc[:"2026-01-10"]
    own = c.close.resample("1D").last()
    bs = pd.read_pickle(ROOT / "data_cache" / "bitstamp_1d_2016.pkl").close
    dev, bull = indicators(c, pd.concat([bs[bs.index < own.index[0]], own]))
    lo, hi = pd.Timestamp("2022-01-01", tz="UTC"), pd.Timestamp("2026-01-01", tz="UTC")
    st, mine = dict(pending=False, t0=None, entry=None), []
    for t, o, h, l, dv, b in zip(c.index, c.open.values, c.high.values, c.low.values, dev.values, bull.values):
        if t < lo:
            continue
        if t >= hi and st["t0"] is None and not st["pending"]:
            break
        r = on_bar(st, t, o, h, l, dv if t < hi - BAR else 0, b)
        if r:
            mine.append((r[0], (r[2] / r[1] - 1) * 100 - 0.14))
    ref = run(prep(c), bull.values, lo, hi)
    assert [t for t, _ in mine] == [t for t, _ in ref], (len(mine), len(ref))
    diff = np.abs(np.array([x for _, x in mine]) - np.array([x for _, x in ref]))
    assert (diff > 1e-6).sum() <= max(2, len(mine) // 20), "갭 손절 외의 불일치"
    # exit_check 경계: 같은 봉에 익절·손절 → 손절, 갭 → 시가, 만기
    t0 = pd.Timestamp("2024-01-01", tz="UTC")
    assert exit_check(100, t0, t0, 100, 105, 93) == (94, "손절")
    assert exit_check(100, t0, t0, 90, 91, 89) == (90, "손절")
    assert exit_check(100, t0, t0, 100, 104, 99) == (104, "익절")
    assert exit_check(100, t0, t0 + MAXB * BAR, 101, 110, 90) == (101, "만기")
    print(f"selftest ok — 재생 {len(mine)}건 = 독립 루프 {len(ref)}건, 거래당 {np.mean([x for _, x in mine]):+.3f}%")


if __name__ == "__main__":
    selftest()
