"""운영 패리티 — **봇 코드 자체**를 과거에 재생해 백테스트와 같은 거래를 내는가 (2026-09-19).
(quant_nasq100 research/soxl_ops_parity.py 이식)

왜 test_signal.py 로 모자라나. 저건 **신호**만 대조한다. 그런데 2026-09-10 감사의 큰 결함 둘은
신호가 아니라 **체결·청산 계층**에 있었다 (일봉 정렬, 청산 우선순위). 리플레이가 못 잡았다.
여기서는 autotrade._run_cycle() 을 그대로 — 상태기계·진입 대기·분봉 하한 방어·사이징·모의 장부까지 —
과거 위에서 10분 주기로 돌리고, 나온 거래를 backtest_current 의 거래 원장과 한 줄씩 맞춘다.

바꿔 끼우는 것은 넷뿐이다: 시계(가짜) · 캔들/시세(캐시) · DB(임시 파일) · Claude(끔).
거래소·운영 DB·.env 는 isolation.guard() 가 물리적으로 막는다.

재생과 백테스트에 **남는 차이** (동등성 검사가 잡지 못하는 것)
  · 봇은 10분마다 돈다. 분봉이 하한을 되찾은 그 분이 아니라 **그 다음 사이클**에 산다 → 체결가가 다르다.
    이 파일이 그 비용을 bp 로 잰다 (backtest_latency.py 가 '노이즈 폭' 이라고 한 것의 실측치다).
  · 청산 체결가: 백테스트는 다음 4h 봉 시가, 봇은 봉 마감 직후 사이클의 시장가. 분봉 캐시가 진입 창에만
    있어 재생도 시가를 쓴다 → 이 항목은 검사되지 않는다 (미검증으로 남긴다).
  · 모의 장부(cash·TAKER_FEE)는 engine 의 비용 모델(편도 0.07%+펀딩)과 다르다. 그래서 **자산 곡선이 아니라
    거래(진입봉·체결가·청산봉)** 를 대조하고, 재생이 낸 체결을 engine 에 다시 먹여 성적을 비교한다.
  · 사고 차단기(MAX_DAY_LOSS)는 백테스트에 아예 없다. 재생은 발동 횟수를 세고 즉시 재개(make on)를 가정한다.

사용: python test_parity.py            현재 .env 설정으로 재생 + 대조 (4~6분)
      python test_parity.py --quick    최근 1년만
"""
import contextlib
import datetime as dt
import logging
import pathlib
import sqlite3
import sys
import types

import numpy as np
import pandas as pd

from isolation import guard

guard()
logging.FileHandler = lambda *a, **kw: logging.NullHandler()      # 운영 로그를 재생으로 더럽히지 않는다

import autotrade as A                                              # noqa: E402
import backtest_both as BO                                         # noqa: E402
import backtest_okx as B                                           # noqa: E402
import backtest_quant as Q                                         # noqa: E402
import minute_data as MD                                           # noqa: E402
import model as M                                                  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent
KST = A.KST
CLOCK = [pd.Timestamp("2021-03-01", tz="UTC")]                     # 재생 시계 (UTC)
PUB = object()                                                     # 거래소 핸들 자리표시자


# ---------- 바꿔 끼우는 것 ----------
class _FakeDatetime(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return CLOCK[0].tz_convert(tz or dt.timezone.utc).to_pydatetime()


def _install(d1, h4, m1):
    """autotrade 가 보는 시계·캔들·시세·DB 를 재생용으로 갈아 끼운다.

    DB 만 예외로 **연결을 재사용**한다 (A.db 는 호출마다 열고 스키마를 다시 만든다 — 12,000 사이클이면
    그것만 30분이다). 바꾸는 것은 연결 수명뿐이고 SQL·판단 경로는 원본 그대로다."""
    A.dt = types.SimpleNamespace(datetime=_FakeDatetime, timedelta=dt.timedelta, timezone=dt.timezone)
    A.DB_PATH = ROOT / "parity.db"
    A.DB_PATH.unlink(missing_ok=True)
    A.AUTORUN_OFF, A.ENTRY_OFF = ROOT / "parity_autorun.off", ROOT / "parity_entry.off"
    A.AUTORUN_OFF.unlink(missing_ok=True)
    A.ENTRY_OFF.unlink(missing_ok=True)
    A.USE_CLAUDE = False                     # 검토는 이 파일의 대상이 아니다 (test_bot.py 가 본다)
    A.MODE, A.HAVE_KEYS = "paper", False     # 재생은 **절대** 거래소에 닿지 않는다 (2중 안전장치).
    assert not A.live_now(), "재생이 실주문 모드다 — 멈춘다"      # isolation 이 뚫려도 여기서 멈춘다
    A._LOCK = object()                       # 프로세스간 락은 잡은 것으로

    with A.db():                             # 원본 db() 로 스키마·초기 state 행을 한 번 만든다
        pass
    conn = [None]

    @contextlib.contextmanager
    def fast_db():
        if conn[0] is None:
            conn[0] = sqlite3.connect(A.DB_PATH)
        yield conn[0]
        conn[0].commit()

    A.db = fast_db
    src = {"1d": (d1, pd.Timedelta(days=1)), "4h": (h4, pd.Timedelta(hours=4)),
           "1m": (m1, pd.Timedelta(minutes=1))}
    kst = {k: v.tz_convert(KST) for k, (v, _) in src.items()}      # 봇은 KST 인덱스로 받는다
    ends = {k: (v.index + step).values for k, (v, step) in src.items()}   # 봉이 닫히는 시각

    def candles(ex, count=100, tf="1d"):
        """봇이 받는 것과 같은 모양 — **마지막 행은 진행 중인 봉** (봇이 .iloc[:-1] 로 버린다)."""
        n = int(np.searchsorted(ends[tf], CLOCK[0].to_datetime64(), "right"))    # 닫힌 봉 개수
        if n == 0:
            raise ValueError("캔들 없음")
        df = kst[tf].iloc[max(0, n - count + 1):n + 1]             # 마지막 한 줄이 진행 중인 봉 자리
        return df if len(df) > 1 else kst[tf].iloc[n - 1:n + 1]

    def price(ex):
        """봇이 보는 현재가 = 방금 닫힌 1분봉 종가 (분봉 캐시가 2021-03~ 전 구간 있다)."""
        n = int(np.searchsorted(ends["1m"], CLOCK[0].to_datetime64(), "right"))
        return float(m1.close.iloc[min(max(n, 1), len(m1)) - 1])

    A.X.candles, A.X.price, A.X.public = candles, price, lambda: PUB


# ---------- 재생 ----------
def replay(h4, verbose=False):
    """10분 주기로 _run_cycle 을 돌린다. 4h 봉 마감 직후 + 진입 대기 중에는 10분마다.
    → (거래 [(진입시각, 체결가, lev, 청산시각, 청산가)], 차단기 발동 횟수)"""
    trades, breaker, opened = [], 0, None
    for i, t in enumerate(h4.index):
        close_t = t + pd.Timedelta(hours=4)
        if close_t > h4.index[-1]:
            break
        stops = [close_t + pd.Timedelta(minutes=1)]                # 봉이 닫히면 늦어도 INTERVAL_MIN 안에 돈다
        stops += [close_t + pd.Timedelta(minutes=k) for k in range(A.INTERVAL_MIN, 240, A.INTERVAL_MIN)]
        for ts in stops:
            CLOCK[0] = ts
            if ts != stops[0] and not A.state()["watch_bar"]:
                break                                              # 대기 중이 아니면 다음 4h 봉까지 볼 것이 없다
            A._run_cycle("재생")
            st = A.state()
            if st["side"] and not opened:
                opened = (ts, st["entry"], st["lev"])
            elif opened and not st["side"]:
                trades.append((*opened, ts, A.X.price(PUB)))
                opened = None
            if A.ENTRY_OFF.exists():                               # 사고 차단기 → 사람이 즉시 재개(make on)했다고 본다
                breaker += 1
                A.set_entry_block(False)
                A.set_autorun(True)
        if verbose and i % 500 == 0:
            print(f"  ... {t:%Y-%m-%d} 거래 {len(trades)}건", flush=True)
    return trades, breaker


# ---------- 대조 ----------
def main():
    quick = "--quick" in sys.argv
    d1, h4 = B.fetch("1d"), B.fetch("4h")
    start = pd.Timestamp("2025-09-01" if quick else Q.START, tz="UTC")
    h4r = h4[h4.index >= start]
    m1 = MD._load()[0].sort_index()
    print(f"재생 구간 {h4r.index[0]:%Y-%m-%d}~{h4r.index[-1]:%Y-%m-%d} · 4h 봉 {len(h4r):,} · 분봉 캐시 {len(m1):,}")
    print(f"설정: CONF={M.CONF} · LEVERAGE={A.LEVERAGE:g} · VT={A.VOL_TARGET_PCT:g}% · ENTRY_FLOOR="
          f"{'켜짐' if A.ENTRY_FLOOR else '꺼짐'} · ALLOW_SHORT={'켜짐' if A.ALLOW_SHORT else '꺼짐'} · "
          f"MAX_DAY_LOSS={A.MAX_DAY_LOSS:g}%\n")

    _install(d1, h4, m1)
    ops, breaker = replay(h4r, verbose=True)

    # 백테스트 쪽 (backtest_current 와 같은 구성)
    _, l_en, l_ex, s_en, s_ex, lab = BO.frames()
    m = h4.index >= start
    sig = (l_en[m], l_ex[m], s_en[m], s_ex[m])
    fills = Q.reclaim_fills() if A.ENTRY_FLOOR else None
    lev_of = (lambda i, c: A.LEVERAGE) if not A.VOL_TARGET_PCT else (
        lambda i, c: (lambda v: A.LEVERAGE if v is None else min(A.LEVERAGE, A.VOL_TARGET_PCT / v))(
            Q.acct_vol(i, c, A.VOL_WINDOW)))
    allow = ("long", "short") if A.ALLOW_SHORT else ("long",)
    curve, bt, stats = BO.simulate(h4r, sig, lev_of, lab[m], allow, fills=fills)

    print(f"\n재생(봇 코드) 거래 {len(ops)}건 · 사고 차단기 {breaker}회 | 백테스트 거래 {len(bt)}건")
    ok = len(ops) == len(bt)
    if not ok:
        print(f"  [불일치] 거래 건수가 다르다: 재생 {len(ops)} vs 백테스트 {len(bt)}")
    gaps, tgaps = [], []
    for k in range(min(len(ops), len(bt))):
        (t_in, px_in, lev, t_out, _), row = ops[k], bt.iloc[k]
        bt_in, bt_out = h4r.index[int(row.i_in)], h4r.index[int(row.i_out)]
        dmin = (t_in - bt_in).total_seconds() / 60
        gaps.append((px_in / row.px_in - 1) * 1e4)
        tgaps.append(dmin)
        if dmin < 0 or dmin > 4 * 60 + A.INTERVAL_MIN or abs((t_out - bt_out).total_seconds() / 60) > 4 * 60 + A.INTERVAL_MIN:
            ok = False
            print(f"  [불일치] #{k} 진입 재생 {t_in:%Y-%m-%d %H:%M} vs 백테스트 봉 {bt_in:%Y-%m-%d %H:%M} · "
                  f"청산 {t_out:%m-%d %H:%M} vs {bt_out:%m-%d %H:%M}")
    g = np.array(gaps) if gaps else np.zeros(1)
    print(f"\n체결가 차이 (재생 − 백테스트): 평균 {g.mean():+.1f}bp · 중앙 {np.median(g):+.1f}bp · "
          f"최대 {np.abs(g).max():.0f}bp · |차이|>10bp {int((np.abs(g) > 10).sum())}건")
    print(f"진입 시각 차이: 중앙 {np.median(tgaps or [0]):.0f}분 · 최대 {max(tgaps or [0]):.0f}분 (10분 주기 + 4h 대기창)")

    # 재생이 낸 체결을 그대로 engine 에 먹여 성적을 비교한다 (비용 모델을 한쪽으로 통일)
    ops_fills = {}
    for t_in, px_in, lev, _, _ in ops:
        bar = h4r.index[h4r.index <= t_in][-1]                     # 체결이 일어난 4h 봉
        after = m1[(m1.index >= t_in) & (m1.index < bar + pd.Timedelta(hours=4))]
        ops_fills[bar] = (px_in, min(px_in, float(after.low.min())) if len(after) else px_in,
                          max(px_in, float(after.high.max())) if len(after) else px_in)
    for b in {h4r.index[int(r.i_in)] for _, r in bt.iterrows()} - set(ops_fills):
        ops_fills[b] = None                                        # 재생이 포기한 자리
    c2, t2, s2 = BO.simulate(h4r, sig, lev_of, lab[m], allow, fills=ops_fills)
    print()
    for name, cv, tt, ss in (("백테스트 (분 단위 재확보)", curve, bt, stats), ("재생 (봇 10분 주기)", c2, t2, s2)):
        Q.report(name, cv, tt.ret.values if len(tt) else np.zeros(1), ss)

    print(f"\n→ 10분 주기가 만드는 성적 차이: 누적 {curve.iloc[-1] * 100 - 100:+.0f}% → {c2.iloc[-1] * 100 - 100:+.0f}%")
    assert ok, "재생과 백테스트의 거래가 어긋난다 — 백테스트 성적은 봇의 성적이 아니다"
    print("\nok  패리티 통과 (봇 코드 재생이 백테스트와 같은 자리에서 같은 방향으로 거래한다)")
    A.DB_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
