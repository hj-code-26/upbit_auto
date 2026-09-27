"""급락 매수 모의 운용 — BTC 단독 · 일봉 50·200일선 필터 · 1배. **주문 코드가 없다** (공개 시세만 읽는다).

근거: research_aoa.txt 8~12·17·18절 (워뇨띠 거래내역 분석 → tpsl.py → sma50.py → defend.py V3 → dip_lev2.py).
규칙:
  진입 : 완성된 5분봉 종가가 1h EMA(5분봉 12개) 대비 −2% 이하 & 그 봉 날짜의 '전날' 일봉 종가 > 전날까지 50일 이평
         & 200일 이평 (200일선은 2026-09-27 추가)
         → 다음 5분봉 시가에 롱
  청산 : 진입가 +4% 익절 / −6% 손절 (봉이 손절가 아래서 열리면 그 시가) / 3일(864봉) 지나면 그 봉 시가
         한 봉에서 익절·손절 둘 다 닿으면 손절로 본다. 청산한 봉에서는 새 신호를 보지 않는다.
  비용 : 편도 0.07% · 펀딩 0.01%/8h (롱이 낸다) — 기록되는 순수익에 반영
백테스트 기대치 (BTC · 50·200일선 · 1배, research/aoa/dip_lev2_result.txt): 승률 66~75% · 거래당 +0.85~1.49%
  연복리 BitMEX 18-21 +27.9% (MDD −37%) · 바이낸스 20-26 +20.0% (−16%) · OKX 21-26 +8.7% (−13%) · 연 6~33건
레버리지: 1배 유지. 2배는 사전등록 기준(3종 모두 MDD ≥ −40%)에서 BitMEX 18-21 −63% 로 불합격 (dip_lev2.py).

장부: dipbuy.db (가상 자본 1,000 USDT, 복리). 실시간과 --selftest 재생은 같은 on_bar() 를 탄다.
사용:  python dipbuy.py              5분마다 실행 (봉 마감 15초 뒤)
       python dipbuy.py --once       1회
       python dipbuy.py --status     장부 요약
       python dipbuy.py --selftest   OKX 2022~25 5분봉 재생 → 독립 백테스트 루프(research/aoa/sma50.run)와 진입 시각·수익 대조
"""
import json
import logging
import pathlib
import socket
import sqlite3
import sys
import time

import pandas as pd

import okx

D, TP, SL, MAXB = 2.0, 4.0, 6.0, 864
COST, FUND = 0.0007, 0.0001
LEV = 1.0                   # 가상 장부 배율: 자본 × (1 + LEV × 순수익)
BAR = pd.Timedelta("5min")
HERE = pathlib.Path(__file__).resolve().parent
DB = HERE / "dipbuy.db"
START_EQ = 1000.0

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[logging.FileHandler(HERE / "dipbuy.log", encoding="utf-8"), logging.StreamHandler()])
log = logging.getLogger("dipbuy")


# ---------- 판정 (실시간·재생 공용) ----------
def indicators(c5, d1):
    """c5: 완성된 5분봉(UTC) · d1: 완성된 일봉 종가(UTC 날짜 인덱스, 200개 이상) → (dev %, bull bool) 5분봉 인덱스.
    bull = 전날 종가가 50일선 & 200일선 둘 다 위 (200일선: research_aoa.txt 17절 V3)."""
    dev = (c5.close / c5.close.ewm(span=12, adjust=False).mean() - 1) * 100
    ok = (d1 > d1.rolling(50).mean()) & (d1 > d1.rolling(200).mean())
    prev = c5.index.normalize() - pd.Timedelta("1D")
    bull = pd.Series(ok.reindex(prev).fillna(False).values.astype(bool), index=c5.index)
    return dev, bull


def on_bar(st, t, o, h, l, dev, bull):
    """완성된 5분봉 하나 처리 → st(dict) 갱신, 청산이 있으면 (진입시각, 진입가, 청산가, 사유, 보유봉) 반환."""
    out = None
    if st["pending"]:                                   # 직전 봉 신호 → 이 봉 시가 체결
        st.update(pending=False, entry_t=t.isoformat(), entry_px=o)
    if st["entry_t"]:
        e, t0 = st["entry_px"], pd.Timestamp(st["entry_t"])
        held = int((t - t0) / BAR)
        up, dn = e * (1 + TP / 100), e * (1 - SL / 100)
        if held >= MAXB:
            px, why = o, "시간"
        elif l <= dn:
            px, why = min(dn, o), "손절"
        elif h >= up:
            px, why = up, "익절"
        else:
            px = None
        if px is not None:
            out = (st["entry_t"], e, px, why, held + (0 if why == "시간" else 1))
            st.update(entry_t=None, entry_px=None, last_exit=t.isoformat())
            return out
    elif dev <= -D and bull:
        st["pending"] = True
    return out


def net_ret(entry, exit_, bars):
    return exit_ / entry - 1 - 2 * COST - FUND * bars / 96


# ---------- 장부 ----------
def db():
    con = sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
    con.execute("""CREATE TABLE IF NOT EXISTS trades (entry_t TEXT, entry_px REAL, exit_t TEXT, exit_px REAL,
                   reason TEXT, bars INT, net REAL, equity REAL)""")
    return con


def load_state(con):
    r = con.execute("SELECT v FROM kv WHERE k='state'").fetchone()
    return json.loads(r[0]) if r else dict(pending=False, entry_t=None, entry_px=None, last_bar=None, last_exit=None,
                                           equity=START_EQ)


def save_state(con, st):
    con.execute("INSERT OR REPLACE INTO kv VALUES ('state', ?)", (json.dumps(st),))
    con.commit()


# ---------- 시세 (공개 API) ----------
def fetch(ex, since):
    rows, t = [], int(since.timestamp() * 1000)
    while True:
        r = ex.fetch_ohlcv(okx.SYMBOL, "5m", since=t, limit=100)
        if not r:
            break
        rows += r
        if len(r) < 100:
            break
        t = r[-1][0] + 1
    c = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).drop_duplicates("ts")
    c.index = pd.to_datetime(c.ts, unit="ms", utc=True)
    c = c[c.index + BAR <= pd.Timestamp.now(tz="UTC")]           # 진행 중인 봉 제외
    d = ex.fetch_ohlcv(okx.SYMBOL, "1d", limit=260)                # 200일선 + 여유
    d1 = pd.Series([x[4] for x in d], index=pd.to_datetime([x[0] for x in d], unit="ms", utc=True))
    d1 = d1[d1.index + pd.Timedelta("1D") <= pd.Timestamp.now(tz="UTC")]
    return c[["open", "high", "low", "close"]].astype(float), d1.astype(float)


def cycle(ex=None):
    ex = ex or okx.public()
    con = db(); st = load_state(con)
    now = pd.Timestamp.now(tz="UTC")
    last = pd.Timestamp(st["last_bar"]) if st["last_bar"] else now
    c, d1 = fetch(ex, min(last, now - pd.Timedelta("30h")) - BAR)   # EMA 예열용으로 30시간 이상
    dev, bull = indicators(c, d1)
    if not st["last_bar"]:                                         # 첫 실행: 과거 신호로 소급 진입하지 않는다
        last = c.index[-1]
    new = c.index[c.index > last]
    for t in new:
        r = c.loc[t]
        ex_ = on_bar(st, t, r.open, r.high, r.low, dev[t], bull[t])
        if ex_:
            et, e, px, why, bars = ex_
            n = net_ret(e, px, bars); st["equity"] *= 1 + LEV * n
            con.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?)", (et, e, t.isoformat(), px, why, bars, n, st["equity"]))
            log.info(f"청산 [{why}] 진입 {e:,.1f} → {px:,.1f} · 순수익 {n * 100:+.2f}% ×{LEV:g}배 · 가상자본 {st['equity']:,.1f}")
        if st["pending"]:
            log.info(f"신호 {t:%m-%d %H:%M} 종가 {r.close:,.1f} (1h EMA 대비 {dev[t]:+.2f}%, 50·200일선 위) → 다음 봉 시가 진입")
        elif st["entry_t"] == t.isoformat():
            log.info(f"진입 {r.open:,.1f} · 익절 {r.open * 1.04:,.1f} · 손절 {r.open * 0.94:,.1f}")
    st["last_bar"] = max(last, c.index[-1]).isoformat()
    save_state(con, st)
    t = c.index[-1]
    pos = f"보유 중 진입 {st['entry_px']:,.1f} (평가 {(c.close.iloc[-1] / st['entry_px'] - 1) * 100:+.2f}%)" if st["entry_t"] else "무포지션"
    log.info(f"사이클 {t:%m-%d %H:%M} 종가 {c.close.iloc[-1]:,.1f} · 괴리 {dev.iloc[-1]:+.2f}% · 50·200일선 {'위' if bull.iloc[-1] else '아래(진입 안 함)'}"
             f" · {pos} · 가상자본 {st['equity']:,.1f}")


def status():
    con = db(); st = load_state(con)
    t = pd.read_sql("SELECT * FROM trades", con)
    print(f"가상자본 {st['equity']:,.1f} USDT (시작 {START_EQ:,.0f}, {(st['equity'] / START_EQ - 1) * 100:+.1f}%)")
    print("포지션:", f"롱 진입 {st['entry_px']:,.1f} @ {st['entry_t']}" if st["entry_t"] else ("진입 대기" if st["pending"] else "없음"))
    if len(t):
        print(f"거래 {len(t)}건 · 승률 {(t.net > 0).mean():.0%} · 거래당 {t.net.mean() * 100:+.2f}% "
              f"(백테스트 기대: 승률 66~75%, 거래당 +0.85~1.49%) · 사유 {t.reason.value_counts().to_dict()}")
        print(t.tail(10).to_string(index=False))
    else:
        print("아직 완료된 거래 없음 (백테스트상 연 15건 안팎 — 50·200일선 아래인 동안은 신호가 없다)")


def selftest():
    """OKX 2022~25 5분봉을 on_bar 로 재생 → 독립 백테스트 루프(sma50.run)에 같은 필터를 넣은 결과와 진입 시각·청산가 대조.
    200일선 예열: OKX 데이터 시작 전은 비트스탬프 일봉으로 채운다."""
    sys.path.insert(0, str(HERE / "research" / "aoa"))
    import numpy as np
    from clone import okx_5m, prep
    from sma50 import run
    c = okx_5m().loc[:"2026-01-10"]                    # 끝 무렵 진입한 거래가 끝까지 가도록 여유
    own = c.close.resample("1D").last()
    bs = pd.read_pickle(HERE / "data_cache" / "bitstamp_1d_2016.pkl").close
    d1 = pd.concat([bs[bs.index < own.index[0]], own])
    dev, bull = indicators(c, d1)
    lo, hi = pd.Timestamp("2022-01-01", tz="UTC"), pd.Timestamp("2026-01-01", tz="UTC")
    st = dict(pending=False, entry_t=None, entry_px=None, last_exit=None)
    mine = []
    for t, o, h, l, dv, b in zip(c.index, c.open.values, c.high.values, c.low.values, dev.values, bull.values):
        if t < lo:
            continue
        if t >= hi and not st["entry_t"] and not st["pending"]:
            break
        r = on_bar(st, t, o, h, l, dv if t < hi - BAR else 0, b)   # 백테스트 신호 구간 = [lo, hi−1봉)
        if r:
            mine.append((pd.Timestamp(r[0]), (r[2] / r[1] - 1) * 100 - 2 * COST * 100))
    ref = run(prep(c), bull.values, lo, hi)
    assert [t for t, _ in mine] == [t for t, _ in ref], (len(mine), len(ref))
    diff = np.abs(np.array([x for _, x in mine]) - np.array([x for _, x in ref]))
    print(f"재생 {len(mine)}건 = 백테스트 {len(ref)}건, 진입 시각 전부 일치 · 수익 차이 최대 {diff.max():.3f}%p "
          f"(갭 손절을 시가로 체결한 차이만 허용) · 거래당 {np.mean([x for _, x in mine]):+.3f}% vs {np.mean([x for _, x in ref]):+.3f}%")
    assert (diff > 1e-6).sum() <= max(2, len(mine) // 20), "갭 외의 불일치"
    print("selftest ok")


def lock(port=8766):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
    except OSError:
        sys.exit("dipbuy 가 이미 다른 프로세스에서 돌고 있습니다")
    return s


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--status":
        status()
    elif arg == "--selftest":
        selftest()
    else:
        _l = lock()
        if arg == "--once":
            cycle()
        else:
            log.info("급락 매수 모의 운용 시작 — 주문 없음, 5분마다")
            ex = okx.public()
            while True:
                try:
                    cycle(ex)
                except Exception as e:  # 네트워크 등 — 다음 사이클에 따라잡는다 (last_bar 기준)
                    log.exception(f"사이클 실패: {e}")
                now = time.time()
                time.sleep(300 - now % 300 + 15)
