"""주식 슬리브 데이터: 미국 정규장 캘린더(XNYS), 기초 주식 조정 일봉(yfinance), NDX 구성종목(CSV), 기업 이벤트.

  ndx_members.csv     date,ticker  — 그 날짜의 구성종목 스냅샷. 다음 스냅샷 전까지 유효 (PIT). 운용은 마지막 스냅샷.
  corporate_events.csv date,ticker,type — 분할·병합·특별배당·상장폐지·지수 편출 일정 (사용자 관리).
                       자동 보충: 구성종목 편출(스냅샷 차이), OKX 공지 제목, yfinance 분할 이력.
"""
import bisect
import pathlib
import re

import pandas as pd
import pandas_market_calendars as mcal

import rls

ROOT = pathlib.Path(__file__).resolve().parent
MEMBERS_CSV = ROOT / "ndx_members.csv"
EVENTS_CSV = ROOT / "corporate_events.csv"
NYSE = mcal.get_calendar("XNYS")
EVENT_WORDS = re.compile(r"split|consolidat|dividend|delist|corporate action|adjust", re.I)


def schedule(start, end):
    """정규장 일정 DataFrame [market_open, market_close] (UTC tz-aware, 조기 폐장 반영). index = 세션 날짜."""
    return NYSE.schedule(start_date=pd.Timestamp(start).date(), end_date=pd.Timestamp(end).date())


def members_loader(path=MEMBERS_CSV):
    """→ (at(day) → 그날 유효한 구성종목 집합, 스냅샷 dict)."""
    m = pd.read_csv(path, parse_dates=["date"])
    snaps = {d: set(g.ticker.str.strip()) for d, g in m.groupby("date")}
    keys = sorted(snaps)

    def at(day):
        k = bisect.bisect_right(keys, pd.Timestamp(day)) - 1
        return snaps[keys[k]] if k >= 0 else set()
    return at, snaps


def daily_bars(tickers, start, last_session):
    """yfinance 조정 OHLC (auto_adjust). last_session(정규장 종가 확정된 마지막 세션) 이후 행은 버린다 (진행 중 봉 금지)."""
    import yfinance as yf
    tickers = sorted(set(tickers))
    if not tickers:
        return {}
    df = yf.download(tickers, start=str(pd.Timestamp(start).date()), auto_adjust=True, progress=False,
                     group_by="ticker", threads=True)
    out = {}
    for t in tickers:
        try:
            d = df[t] if isinstance(df.columns, pd.MultiIndex) else df
        except KeyError:
            continue
        d = d.rename(columns=str.lower)[["open", "high", "low", "close"]].dropna()
        d.index = pd.to_datetime(d.index).tz_localize(None).normalize()
        d = d[d.index <= pd.Timestamp(last_session)]
        if len(d):
            out[t] = d.astype(float)
    return out


def events(day, sessions, snaps=None, announcements=(), splits=None, path=EVENTS_CSV):
    """day 세션에 신규 진입을 막을 종목 {티커: [사유]}. 이벤트일의 전·당·후 영업일(세션)을 막는다.
    announcements: [(pTime ms, title)] OKX 공지. splits: {티커: [분할 날짜]} (yfinance 이력)."""
    ev = []
    if path.exists():
        e = pd.read_csv(path, parse_dates=["date"])
        ev += [(r.date, r.ticker.strip(), r.type) for r in e.itertuples()]
    if snaps:                                                     # 지수 편출 = 스냅샷에서 빠진 날
        keys = sorted(snaps)
        ev += [(b, t, "index_removal") for a, b in zip(keys, keys[1:]) for t in snaps[a] - snaps[b]]
    for t, ds in (splits or {}).items():
        ev += [(pd.Timestamp(d), t, "split") for d in ds]
    sess = pd.DatetimeIndex(sessions)
    k = sess.searchsorted(pd.Timestamp(day))
    near = set(sess[max(0, k - 1):k + 2])                         # 전·당·후 세션
    out = {}
    for d, t, typ in ev:
        j = sess.searchsorted(pd.Timestamp(d).normalize())
        if j < len(sess) and sess[j] in near:
            out.setdefault(t, []).append(typ)
    for p_ms, title in announcements:                             # OKX 공지: 제목에 티커 + 이벤트 단어 → 공지일을 이벤트일로
        d = pd.Timestamp(p_ms, unit="ms").normalize()
        j = sess.searchsorted(d)
        if j < len(sess) and sess[j] in near and EVENT_WORDS.search(title):
            for w in re.findall(r"\b[A-Z]{1,6}\b", title):
                out.setdefault(w, []).append(f"okx:{title[:60]}")
    return out


def blocked(events_map, ticker):
    """기초 티커와 OKX 기초명 둘 다로 조회."""
    return events_map.get(ticker) or events_map.get(rls.okx_sym(ticker))
