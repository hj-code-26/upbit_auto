"""OKX BTC-USDT-SWAP 1분봉 수집·캐시 (연구 전용, 봇은 쓰지 않는다).

OKX 과거 캔들 API 는 한 번에 100개라서 5.5년 전량(약 290만 봉)이면 요청이 2.9만 건이다.
요청 하나가 왕복 0.3초라 순차로는 2시간이 넘어, 스레드 3개로 나눠 보낸다 (엔드포인트 상한 20건/2초 안쪽).
그래서 기본은 '필요한 구간만' 받는다 — 진입 이벤트 앞뒤 몇 시간이면 진입 타이밍 연구에는 충분하다.
받은 구간은 data_cache/okx_1m.pkl 에 쌓이고, 어디까지 받았는지는 okx_1m_ranges.json 이 기억한다 (중간에 끊겨도 이어받음).

사용: python minute_data.py            자체 점검 (최근 3시간)
      python minute_data.py full       2021-03~ 전량 (약 1시간, 300MB 안팎). 7일씩 저장하므로 끊겨도 이어받는다
"""
import json
import pathlib
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import ccxt
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent
CACHE = ROOT / "data_cache"
PKL = CACHE / "okx_1m.pkl"
RANGES = CACHE / "okx_1m_ranges.json"
SYMBOL = "BTC/USDT:USDT"
MIN_MS = 60_000
LIMIT = 100                     # OKX 과거 캔들 한 번에 100개
WORKERS = 3                     # 왕복 지연이 병목이라 병렬로 보낸다
CHUNK_MS = 7 * 86400 * 1000     # 한 번에 받는 구간 = 7일
SAVE_EVERY = 20                 # 20구간(=약 20주)마다 저장. 중간에 끊겨도 여기까지는 남는다
_LOCAL = threading.local()


def _ex():
    """스레드마다 제 인스턴스를 쓴다 (ccxt 객체는 스레드 안전하지 않다).
    ccxt 의 요청 간격을 WORKERS 배로 늘려 합계가 엔드포인트 상한을 넘지 않게 한다."""
    if not hasattr(_LOCAL, "ex"):
        _LOCAL.ex = ccxt.okx({"enableRateLimit": True, "options": {"defaultType": "swap"}})
        _LOCAL.ex.rateLimit *= WORKERS
    return _LOCAL.ex


def _page(a):
    """[a, a+100분) 한 페이지. 429 는 물러섰다 다시 시도한다."""
    for wait in (0, 1, 2, 5, 10):
        time.sleep(wait)
        try:
            return _ex().fetch_ohlcv(SYMBOL, "1m", since=a, limit=LIMIT)
        except ccxt.BaseError as e:
            last = e
    print(f"  포기 {a}: {last}", file=sys.stderr)
    return []


def _load():
    df = pd.read_pickle(PKL) if PKL.exists() else pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    got = [tuple(r) for r in json.loads(RANGES.read_text())] if RANGES.exists() else []
    return df, got


def _missing(want, got):
    """want=(a,b) 에서 이미 받은 구간 got 을 뺀 나머지 [(a,b), ...]. 같은 구간을 두 번 받지 않는다."""
    out = [want]
    for ga, gb in sorted(got):
        nxt = []
        for a, b in out:
            if gb <= a or ga >= b:
                nxt.append((a, b))
                continue
            if a < ga:
                nxt.append((a, ga))
            if gb < b:
                nxt.append((gb, b))
        out = nxt
    return [(a, b) for a, b in out if b - a >= MIN_MS]


def fetch(ranges, quiet=False):
    """ranges: [(pd.Timestamp, pd.Timestamp)] (UTC). 캐시에 없는 구간만 받아 합치고 전체를 돌려준다."""
    CACHE.mkdir(exist_ok=True)
    df, got = _load()
    want = [(int(a.timestamp() * 1000), int(b.timestamp() * 1000)) for a, b in ranges]
    todo = [m for w in want for m in _missing(w, got)]
    todo = [(a, min(a + CHUNK_MS, b)) for x, y in todo for a in range(x, y, CHUNK_MS) for b in (y,)]
    if not todo:
        return df
    calls = sum(-(-(b - a) // (LIMIT * MIN_MS)) for a, b in todo)
    if not quiet:
        print(f"1분봉 {len(todo)}구간 · 요청 약 {calls:,}건 ({calls / 9 / 60:.0f}분 예상)", file=sys.stderr)
    def save(buf):
        """버퍼를 표에 합치고 저장. 구간마다 하면 표 전체를 300번 다시 정렬하게 되어 갈수록 느려진다."""
        nonlocal df
        if buf:
            new = pd.DataFrame(buf, columns=["ts", "open", "high", "low", "close", "volume"])
            new.index = pd.to_datetime(new.ts, unit="ms", utc=True)
            df = pd.concat([df, new.drop(columns="ts").astype(float)])
            df = df[~df.index.duplicated()].sort_index()
        df.to_pickle(PKL)
        RANGES.write_text(json.dumps(sorted(got)))

    buf = []
    with ThreadPoolExecutor(WORKERS) as pool:
        for k, (a, b) in enumerate(todo, 1):
            pages = pool.map(_page, range(a, b, LIMIT * MIN_MS))
            buf += [r for pg in pages for r in pg if a <= r[0] < b]
            got.append((a, b))
            if k % SAVE_EVERY == 0:                             # 끊겨도 여기까지는 남는다
                save(buf)
                buf = []
                if not quiet:
                    print(f"  {k}/{len(todo)}구간  누적 {len(df):,}봉  ~{pd.Timestamp(b, unit='ms', tz='UTC'):%Y-%m-%d}", file=sys.stderr)
    save(buf)
    return df


def window(df, start, end):
    """받아 둔 표에서 [start, end) 만. 거래소 공백(체결 없는 분)은 그대로 비어 있다."""
    return df[(df.index >= start) & (df.index < end)]


if __name__ == "__main__":
    if "full" in sys.argv:
        d = fetch([(pd.Timestamp("2021-03-01", tz="UTC"), pd.Timestamp.now("UTC").floor("min"))])
        print(f"전량 {len(d):,}봉  {d.index[0]} ~ {d.index[-1]}")
    else:
        end = pd.Timestamp.now("UTC").floor("min") - pd.Timedelta(minutes=2)
        d = window(fetch([(end - pd.Timedelta(hours=3), end)], quiet=True), end - pd.Timedelta(hours=3), end)
        assert len(d) > 150, f"3시간이면 180봉 안팎이어야 한다: {len(d)}"
        assert (d.high >= d.low).all() and (d.close > 0).all()
        assert _missing((0, 1000 * MIN_MS), [(0, 400 * MIN_MS), (600 * MIN_MS, 1000 * MIN_MS)]) == [(400 * MIN_MS, 600 * MIN_MS)]
        print(f"ok  {len(d)}봉  {d.index[0]:%m-%d %H:%M} ~ {d.index[-1]:%H:%M}  마지막 종가 {d.close.iloc[-1]:,.1f}")
