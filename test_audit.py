"""실행 안전성 감사 테스트 (2026-09-28) — 임시 DB · ccxt 를 흉내 낸 가짜 거래소만 쓴다.
네트워크(ccxt.okx · anthropic)는 막아 두고, 운영 DB(trading.db)·운영 로그(autotrade.log)·autorun.off 를 건드리지 않는다.
okx.py 의 실제 함수(open_position · close_position · find_order · bars_since · snapshot)가 가짜 거래소 위에서 돈다.

OPEN = 재현만 하고 고치지 않은 결함 (주문 생명주기·청산 의미 변경이라 승인 대기). 그 밖의 실패가 있으면 exit 1.
사용: python test_audit.py
"""
import builtins
import contextlib
import logging
import os
import pathlib
import sys
import tempfile
import time

import ccxt
import numpy as np
import pandas as pd
from ccxt.base.decimal_to_precision import TICK_SIZE, TRUNCATE, decimal_to_precision

TMP =pathlib.Path(tempfile.mkdtemp(prefix="coin_audit_"))
os.environ.update(MODE="paper", PAPER_CASH="1000", SYMBOL="BTC/USDT:USDT", LEVERAGE="1", MAX_DAY_LOSS_PCT="15")

import autotrade as A                                     # noqa: E402
import okx as X                                           # noqa: E402
import strategy as S                                      # noqa: E402

for h in logging.getLogger().handlers[:]:                 # 운영 로그 파일에 쓰지 않는다
    if isinstance(h, logging.FileHandler):
        logging.getLogger().removeHandler(h)
        h.close()


def _no_net(*a, **k):
    raise RuntimeError("테스트에서 네트워크 호출 금지")


X.ccxt.okx = _no_net
A.anthropic.Anthropic = _no_net
A.DB_PATH = TMP / "audit.db"
A.AUTORUN_OFF, A.ENTRY_OFF = TMP / "autorun.off", TMP / "entry.off"
A.USE_CLAUDE, A.HAVE_KEYS, A._LOCK = False, False, object()
OPEN = set()
PX, CS = 80_000.0, 0.01                                   # BTC-USDT-SWAP 1계약 = 0.01 BTC
T0 = pd.Timestamp("2026-06-01 12:00", tz="UTC")
UP = pd.Series(np.linspace(50_000, 90_000, 280), index=pd.date_range(T0.normalize() - pd.Timedelta("260D"), periods=280, freq="D"))


class FakeEx:
    """ccxt.okx 흉내 (단방향 · 격리). create_order 반응은 modes 큐로 정한다:
    fill 전량 · partial 절반 체결 후 잔량 취소 · reject 체결 0 취소 · lost 체결됐는데 응답 유실(예외)
    · limbo 접수됐지만 아직 체결·조회 안 됨(예외) · down 거래소에 닿기 전 끊김(예외)
    · refuse 거래소가 응답으로 거절 (ccxt.ExchangeError — 주문이 안 생겼다는 확정 정보)."""

    def __init__(self, px=PX, cash=1000.0, pos=0.0, entry=0.0, locked=0.0, m5=None, d1=None):
        self.px, self.cash, self.pos, self.entry, self.locked = px, cash, pos, entry, locked
        self.m5, self.d1, self.modes, self.orders, self.calls = m5, d1, [], {}, []
        self.lag, self.min_pos = False, pos
        self.cfg = {"acctLv": "2", "posMode": "net_mode", "perm": "read_only,trade"}   # 2026-09-30 실계좌 조회 형식

    def load_markets(self):
        return {X.SYMBOL: {"contractSize": CS}}

    def fetch_ticker(self, s):
        return {"last": self.px}

    def fetch_balance(self):
        upl = self.pos * CS * (self.px - self.entry)
        return {"USDT": {"free": self.cash - self.locked - self.pos * CS * self.entry, "total": self.cash + upl}}

    def fetch_positions(self, syms):
        return [{"contracts": self.pos, "side": "long", "entryPrice": self.entry, "info": {}}] if self.pos > 1e-12 else []

    def amount_to_precision(self, s, n):
        """실제 ccxt(OKX) 와 같은 의미: 문자열 기준 TRUNCATE(0.01), 결과가 0 이면 InvalidOrder (2026-09-30 실측으로 대조).
        예전 흉내는 +1e-9 를 더해 float 오차(0.029999…→0.02)를 가렸다 — 그래서 18번 결함이 안 보였다."""
        out = decimal_to_precision(n, TRUNCATE, 0.01, TICK_SIZE)
        if float(out) == 0:
            raise ccxt.InvalidOrder("amount must be greater than minimum amount precision of 0.01")
        return out

    def set_position_mode(self, *a):
        self.calls.append(("set_position_mode",))

    def set_leverage(self, *a, **k):
        self.calls.append(("set_leverage",))

    def private_get_account_config(self, params=None):
        return {"data": [self.cfg]}

    def create_order(self, sym, typ, side, n, params=None):
        params = dict(params or {})
        self.calls.append(("create_order", side, float(n), params))
        mode = self.modes.pop(0) if self.modes else "fill"
        if mode == "down":
            raise ConnectionError("connection reset")
        if mode == "refuse":
            raise ccxt.InsufficientFunds('okx {"code":"1","data":[{"sCode":"51008","sMsg":"Order failed. Insufficient USDT margin"}]}')
        if side == "sell" and params.get("reduceOnly"):
            n = min(n, self.pos)                                  # reduceOnly 는 포지션을 넘지 못한다
        f = {"fill": n, "lost": n, "partial": np.floor(n * 50) / 100, "reject": 0.0, "limbo": 0.0}[mode]
        self._apply(side, f)
        self.orders[params["clOrdId"]] = {"status": "open" if mode == "limbo" else "closed" if f == n else "canceled",
                                          "filled": f, "average": self.px if f else None, "id": f"o{len(self.orders)}",
                                          "hidden": mode == "limbo", "side": side, "n": n}
        if mode in ("lost", "limbo"):
            raise TimeoutError("gateway timeout")
        return {"id": self.orders[params["clOrdId"]]["id"]}

    def settle_limbo(self):
        """접수만 됐던 주문이 뒤늦게 체결된다."""
        for o in self.orders.values():
            if o["hidden"]:
                o.update(hidden=False, status="closed", filled=o["n"], average=self.px)
                self._apply(o["side"], o["n"])

    def _apply(self, side, f):
        if side == "buy" and f:
            self.entry = (self.entry * self.pos + self.px * f) / (self.pos + f)
        self.pos += f if side == "buy" else -f
        self.min_pos = min(self.min_pos, self.pos)

    def fetch_order(self, oid, sym, params=None):
        o = self.orders.get((params or {}).get("clOrdId"))
        if self.lag or not o or o["hidden"]:
            raise LookupError("order does not exist")
        return o

    def fetch_ohlcv(self, sym, tf, since=None, limit=100):
        """OKX(ccxt) 의 since 조회 흉내: [since, since + limit봉) 창 하나. 1d 는 최근 limit 개."""
        if tf == "1d":
            d = self.d1.tail(limit)
            return [[int(t.timestamp() * 1000), v, v, v, v, 0] for t, v in d.items()]
        now = pd.Timestamp.now(tz="UTC")
        lo = pd.Timestamp(since, unit="ms", tz="UTC")
        w = self.m5[(self.m5.index >= lo) & (self.m5.index < lo + limit * S.BAR) & (self.m5.index <= now)]
        rows = [[int(t.timestamp() * 1000), r.open, r.high, r.low, r.close, 0] for t, r in w.iterrows()]
        return rows[::-1] + rows[:3] if getattr(self, "scramble", False) else rows

    def buys(self):
        return [c for c in self.calls if c[0] == "create_order" and c[1] == "buy"]

    def sells(self):
        return [c for c in self.calls if c[0] == "create_order" and c[1] == "sell"]


def bars(n=60, start=T0, dip=None):
    idx = pd.date_range(start - (n - 1) * S.BAR, periods=n, freq="5min")
    c = pd.DataFrame({"open": PX, "high": PX * 1.001, "low": PX * 0.999, "close": PX}, index=idx)
    if dip:
        c.iloc[-1] = [PX, PX, PX * (1 - dip / 100), PX * (1 - dip / 100)]
    return c


def after(c, *rows):
    idx = pd.date_range(c.index[-1] + S.BAR, periods=len(rows), freq="5min")
    return pd.concat([c, pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)])


def reset():
    A.DB_PATH.unlink(missing_ok=True)
    A.set_state(side=None, qty=0, entry=0, entered_at=None, seen_bar=None, cash=1000.0)
    A.USE_CLAUDE, A.HAVE_KEYS = False, False


def hold_long(entry=PX, qty=0.0123, bar=T0):
    """bar = 진입 뒤 첫 온전한 봉. 진입은 그 앞 봉 시작 15초 뒤 (봇이 봉 마감 15초 뒤 판단하므로)."""
    A.set_state(side="long", qty=qty, entry=entry, lev=1.0, seen_bar=(bar - S.BAR).isoformat(),
                entered_at=(bar - S.BAR + pd.Timedelta("15s")).tz_convert(A.KST).isoformat())


def act(ex, c, px=None, d1=UP):
    px = px or (ex.px if ex else PX)
    return A.act(ex, ex, 0, c, d1, px, A.account(ex, px))


def last_order():
    with A.db() as db:
        return db.execute("SELECT action, status, fill_price, qty FROM orders ORDER BY id DESC LIMIT 1").fetchone()


def now_series(dip_last=False, dip_prog=False, lag=0):
    """벽시계 기준 5분봉(마지막 완성 봉 + 진행 중 봉)과 일봉(오늘 진행 중 포함). lag>0 이면 거래소 데이터가 그만큼 늦다."""
    now = pd.Timestamp.now(tz="UTC")
    if (now.ceil(S.BAR) - now).total_seconds() < 5:
        time.sleep(6)
        now = pd.Timestamp.now(tz="UTC")
    last = now.floor(S.BAR) - (1 + lag) * S.BAR
    m5 = bars(400, last, 3 if dip_last else None)
    if not lag:
        p = PX * (0.97 if dip_prog else 1)
        m5 = after(m5, (PX, PX, p, p))
    d1 = pd.Series(np.linspace(50_000, 90_000, 280), index=pd.date_range(now.normalize() - pd.Timedelta("279D"), periods=280, freq="D"))
    return m5, d1


@contextlib.contextmanager
def patched(mod, **kw):
    old = {k: getattr(mod, k) for k in kw}
    for k, v in kw.items():
        setattr(mod, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(mod, k, v)


def cycle_now(pub, client=None):
    """_run_cycle 전체(데이터 수집 → 확정 봉 → 신호 → 주문)를 가짜 거래소로 돌린다."""
    with patched(X, public=lambda: pub, client=lambda: client or _no_net()):
        A._run_cycle("감사")
    with A.db() as db:
        return db.execute("SELECT action, reason, status FROM runs ORDER BY id DESC LIMIT 1").fetchone()


# ───────────── 시나리오 ─────────────
def s1():
    reset()
    m5, d1 = now_series(dip_prog=True)
    r = cycle_now(FakeEx(m5=m5, d1=d1))
    assert r[2] == "done" and r[0] == "hold", f"진행 중 봉의 급락으로 진입했다: {r}"
    reset()
    m5, d1 = now_series(dip_last=True)
    r = cycle_now(FakeEx(m5=m5, d1=d1))
    assert r[0] == "open", f"완성된 봉 급락이면 진입해야 한다: {r}"


def s2():
    reset()
    c = after(bars(), (PX, PX, PX * 0.90, PX), (PX, PX, PX, PX))   # 진입 전 봉에 −10% 저가 · 그다음이 진입 봉
    hold_long(bar=c.index[-1] + S.BAR)
    A.set_state(seen_bar=T0.isoformat())                   # 마지막 처리 봉이 진입 전 봉보다 앞이어도
    c = after(c, (PX, PX * 1.001, PX * 0.999, PX))
    assert act(None, c)[0] == "hold", "진입 전 봉의 저가로 손절했다"


def s2b():
    reset()
    c = after(bars(), (PX * 1.05, PX * 1.05, PX * 0.999, PX))   # 진입 봉: 시가 +5% 에서 열려 15초 안에 PX 로 떨어진 뒤 진입
    hold_long(bar=c.index[-1] + S.BAR)
    A.set_state(seen_bar=c.index[-2].isoformat())          # 진입 봉은 아직 처리 전
    a, why, *_ = act(None, after(c, (PX, PX * 1.001, PX * 0.999, PX)))
    assert a == "hold", f"진입 전(봉 시가) 가격으로 익절을 장부에 적었다: {why}"
    reset()                                                # 만기는 첫 온전한 봉부터 864봉
    c = bars()
    hold_long(bar=c.index[-1] + S.BAR)
    rows = [(PX, PX * 1.001, PX * 0.999, PX)] * S.MAXB
    assert act(None, after(c, *rows))[0] == "hold", "만기를 진입 봉부터 셌다"
    assert "만기" in act(None, after(c, *rows, rows[0]))[1]


def s3():
    reset()
    c = bars()
    hold_long(bar=c.index[-1] + S.BAR)
    a, why, *_ = act(None, after(c, (PX, PX * 1.05, PX * 0.93, PX)))
    assert a == "close" and "손절" in why and abs(last_order()[2] - PX * 0.94) < 1e-6, why


def s4():
    reset()
    c = bars()
    hold_long(bar=c.index[-1] + S.BAR)
    act(None, after(c, (PX * 0.90, PX * 0.91, PX * 0.89, PX * 0.90)))
    assert abs(last_order()[2] - PX * 0.90) < 1e-6, f"갭 손절은 손절선(−6%)이 아니라 시가(−10%): {last_order()}"
    reset()                                               # 실주문: 시장가(현재가)로 적는다
    ex = FakeEx(px=PX * 0.88, pos=1.23, entry=PX)
    c = bars()
    hold_long(bar=c.index[-1] + S.BAR)
    act(ex, after(c, (PX * 0.90, PX * 0.91, PX * 0.89, PX * 0.90)))
    assert abs(last_order()[2] - PX * 0.88) < 1e-6 and ex.pos == 0, f"실주문 갭 손절은 체결가: {last_order()}"


def s5():
    reset()
    c = after(bars(dip=3), (PX, PX, PX, PX), (PX, PX, PX, PX))
    A.set_state(seen_bar=(c.index[-1] - 10 * S.BAR).isoformat())
    assert act(None, c)[0] == "hold", "놓친 봉의 급락으로 샀다"


def s6():
    reset()
    ex = FakeEx(px=PX * 1.01, pos=1.23, entry=PX)
    ex.lag = True                                          # 체결은 됐지만 조회가 아직 안 잡힌다
    c = bars()
    hold_long(bar=c.index[-1] + S.BAR)
    c = after(c, (PX, PX, PX * 0.93, PX * 0.95), (PX, PX * 1.01, PX, PX * 1.01))   # 다운타임 중 손절 통과 뒤 회복
    act(ex, c)
    o = last_order()
    assert abs((o[2] or 0) - PX * 0.94) > 1, f"거래소 체결 없이 과거 손절가로 적었다: {o}"
    assert A.state()["side"] == "long", f"체결 확인 전에 장부를 확정했다 (status={o[1]})"
    ex.lag = False
    act(ex, c)                                             # 다음 사이클: 거래소는 이미 비었다 → 대사로 정리, 추가 주문 없음
    assert A.state()["side"] is None and len(ex.sells()) == 1 and ex.min_pos >= 0


def s7():
    reset()
    ex = FakeEx()
    ex.modes, ex.lag = ["lost"], True
    c = bars(dip=3)
    assert act(ex, c)[0] == "open" and A.state()["side"] is None, "응답 유실인데 장부에 포지션"
    ex.lag = False
    act(ex, after(c, (PX, PX, PX * 0.97, PX * 0.97)))       # 다음 봉도 급락
    assert len(ex.buys()) == 1 and A.state()["side"] == "long", f"중복 제출 {len(ex.buys())}회"


def s7b():
    reset()
    ex = FakeEx()
    ex.modes = ["limbo"]
    c = bars(dip=3)
    act(ex, c)
    c = after(c, (PX, PX, PX * 0.97, PX * 0.97))            # 첫 주문이 미해결인 채 다음 봉도 급락
    act(ex, c)
    ex.settle_limbo()
    assert len(ex.buys()) == 1, f"미해결 주문이 있는데 신규 진입을 또 냈다 → 포지션 {ex.pos:.2f}계약 (의도 1.23)"
    act(ex, after(c, (PX, PX, PX * 0.97, PX * 0.97)))       # 뒤늦게 체결 → 대사로 흡수, 추가 주문 없음
    with A.db() as db:
        st = db.execute("SELECT status FROM orders WHERE action='open' ORDER BY id LIMIT 1").fetchone()[0]
    assert len(ex.buys()) == 1 and abs(ex.pos - 1.23) < 1e-9 and A.state()["side"] == "long", (ex.buys(), ex.pos)
    reset()                                                 # 30분 넘게 거래소가 모르는 주문 → 안 나간 것으로 보고 차단 해제
    ex = FakeEx()
    A._order_row(run_id=0, timestamp=(pd.Timestamp.now(tz=A.KST) - pd.Timedelta("2h")).isoformat(), mode="live",
                 action="open", side="long", qty=0, notional=990, price=PX, client_id="aold", status="unknown: timeout", reason="t")
    act(ex, bars(dip=3))
    assert st == "filled(대사)", f"뒤늦게 체결된 주문 기록이 확정되지 않았다: {st}"
    assert len(ex.buys()) == 1, f"오래된 미확인 주문이 진입을 영구히 막았다: {ex.buys()}"
    with A.db() as db:
        assert db.execute("SELECT status FROM orders WHERE client_id='aold'").fetchone()[0] == "notfound(대사)"


def s8():
    reset()
    ex = FakeEx(pos=1.23, entry=79_000.0)                   # 체결 뒤 DB 기록 전에 죽었다
    A._order_row(run_id=0, timestamp=A.now(), mode="live", action="open", side="long", qty=0, notional=990,
                 price=PX, client_id="a1", status="intent", reason="t")
    act(ex, bars())
    st = A.state()
    assert st["side"] == "long" and abs(st["qty"] - 0.0123) < 1e-9 and st["entry"] == 79_000.0, st


def s9():
    reset()
    ex = FakeEx()
    ex.modes = ["partial"]
    act(ex, bars(dip=3))
    assert abs(A.state()["qty"] - ex.pos * CS) < 1e-9, f"부분 체결 진입: 장부 {A.state()['qty']} ≠ 거래소 {ex.pos * CS}"
    reset()
    ex = FakeEx(pos=1.23, entry=PX, px=PX * 0.93)
    ex.modes = ["partial"]
    c = bars()
    hold_long(bar=c.index[-1] + S.BAR)
    c = after(c, (PX, PX, PX * 0.93, PX * 0.93))
    act(ex, c)
    assert A.state()["side"] == "long", f"부분 청산인데 장부에서 포지션을 지웠다 (거래소 잔량 {ex.pos:.2f}계약)"
    left = ex.pos
    act(ex, c)                                             # 다음 사이클: 남은 수량만 다시 청산
    assert ex.pos == 0 and ex.min_pos >= 0 and abs(ex.sells()[-1][2] - left) < 1e-9, (ex.pos, ex.sells())


def s10():
    reset()
    ex = FakeEx(pos=50, entry=PX, px=PX * 0.93)            # 거래소 0.5 BTC, 장부는 1.0 BTC 로 낡았다
    c = bars()
    hold_long(qty=1.0, bar=c.index[-1] + S.BAR)
    act(ex, after(c, (PX, PX, PX * 0.93, PX * 0.93)))
    n, p = ex.sells()[0][2], ex.sells()[0][3]
    assert n <= 50 and p.get("reduceOnly") is True and ex.min_pos >= 0, (n, p, ex.min_pos)


def s11():
    D = T0.normalize() + pd.Timedelta("1D")
    c = bars(8, D + 3 * S.BAR)                             # D−1 23:40 ~ D 00:15
    d1 = UP[UP.index < D]                                  # D−1 까지 확정
    dev0, bull0 = S.indicators(c, d1)
    fut = pd.concat([d1, pd.Series([1.0], index=[D])])    # D 의 (미완성·미래) 값이 끼어들어도
    assert (S.indicators(c, fut)[1] == bull0).all(), "당일 일봉 값이 당일 봉 판단에 쓰였다"
    bad = d1.copy(); bad.iloc[-1] = 1.0                    # D−1 종가를 50·200일선 아래로
    b2 = S.indicators(c, bad)[1]
    assert b2[c.index >= D].eq(False).all() and b2[c.index < D].eq(bull0[c.index < D]).all(), "D−1 종가는 D 봉부터 적용"


def s12():
    now = pd.Timestamp.now(tz="UTC")
    m5 = bars(400, now.floor(S.BAR) - S.BAR)
    m5 = m5.drop(m5.index[10:20])                          # 첫 창(100봉)에 결측 10봉
    ex = FakeEx(m5=m5)
    got = X.bars_since(ex, m5.index[0])
    assert got.index[-1] == m5.index[-1], f"결측 창에서 조회를 멈췄다: 마지막 {got.index[-1]} (기대 {m5.index[-1]})"
    ex.scramble = True                                     # 역순 + 중복
    got = X.bars_since(ex, m5.index[0])
    assert got.index.is_monotonic_increasing and got.index.is_unique, "역순·중복 봉을 정리하지 않았다"
    reset()
    m5, d1 = now_series(dip_last=True, lag=6)             # 거래소 데이터 30분 지연, 마지막(=낡은) 봉이 급락
    r = cycle_now(FakeEx(m5=m5, d1=d1))
    assert r[0] == "hold", f"30분 지난 봉의 신호로 진입했다: {r}"


def s13():
    for ex in (FakeEx(cash=5.0), FakeEx(cash=10.5, px=200_000.0)):   # 최소 명목 미달 · 계약 수 0
        reset()
        act(ex, bars(dip=3))
        assert not ex.buys(), f"최소 수량 미달인데 제출했다: {ex.buys()}"
        # 2026-09-30: 제출 전에 알 수 있는 거부는 '제출 전' 에 막아야 한다 — 계좌 설정(레버리지·포지션 모드)을 먼저 바꾸거나
        # 안 나간 주문을 '상태 불명' 으로 적어 30분 진입 차단·CRITICAL 경보를 내면 안 된다
        assert not [c for c in ex.calls if c[0] in ("set_leverage", "set_position_mode")], f"제출 불가 주문 앞에서 계좌 설정을 바꿨다: {ex.calls}"
        with A.db() as db:
            unk = db.execute("SELECT status FROM orders WHERE status LIKE 'unknown%'").fetchall()
        assert not unk, f"제출하지 않은 주문을 상태 불명으로 적었다: {unk}"
    reset()
    ex = FakeEx(locked=900.0)                              # 자산 1000 중 가용 100
    act(ex, bars(dip=3))
    assert not ex.buys(), f"가용 잔고 100 USDT 로 990 USDT 주문을 냈다: {ex.buys()}"


def s14():
    reset()
    A.HAVE_KEYS = True                                     # 키가 있어도 MODE=paper 면 주문 API 0회
    m5, d1 = now_series(dip_last=True)
    pub, cli = FakeEx(m5=m5, d1=d1), FakeEx()
    r = cycle_now(pub, cli)
    assert r[0] == "open" and last_order()[1] == "paper", r
    assert not [c for c in pub.calls + cli.calls if c[0] in ("create_order", "set_leverage", "set_position_mode")], cli.calls
    try:
        A._manual_order("buy")
        raise AssertionError("paper 에서 수동 실주문이 나갔다")
    except ValueError:
        pass


def s15():
    reset()
    A.USE_CLAUDE = True
    with patched(A, ask_claude=_no_net):
        c = bars()
        hold_long(bar=c.index[-1] + S.BAR)
        assert act(None, after(c, (PX, PX, PX * 0.93, PX * 0.93)))[0] == "close", "Claude 장애가 청산을 막았다"


def s16():
    reset()
    ex = FakeEx(pos=1.23, entry=PX, px=PX * 0.95)
    ex.modes = ["down"]
    c = bars()
    hold_long(bar=c.index[-1] + S.BAR)
    c = after(c, (PX, PX, PX * 0.93, PX * 0.95))
    act(ex, c)
    act(ex, after(c, (PX * 0.95, PX * 0.96, PX * 0.95, PX * 0.95)))   # 다음 봉은 손절선에 안 닿는다
    assert ex.pos == 0, f"손절 청산이 실패한 뒤 다시 시도하지 않았다 (포지션 {ex.pos:.2f}계약 남음)"


def s17():
    reset()
    ex = FakeEx()
    ex.modes = ["reject"]
    act(ex, bars(dip=3))
    assert A.state()["side"] is None, f"거래소가 거부(체결 0)했는데 장부에 롱: {last_order()}"


def s18():
    """거래소가 보고한 포지션 전량을 청산 주문 수량으로 보내는가 (계약 → BTC → 계약 왕복의 float 오차)."""
    for ct in (0.03, 0.06, 0.41, 1.23):                    # 0.03계약 × 0.01 = 0.00030000000000000003 BTC 등
        reset()
        ex = FakeEx(pos=ct, entry=PX, px=PX * 0.93)
        c = bars()
        hold_long(qty=ct * CS, bar=c.index[-1] + S.BAR)
        act(ex, after(c, (PX, PX, PX * 0.93, PX * 0.93)))   # 손절
        assert ex.pos == 0 and ex.min_pos >= 0, f"{ct}계약 청산 주문이 {ex.sells()[-1][2]}계약만 보냈다 → 잔량 {ex.pos:.2f}계약 방치"
        assert A.state()["side"] is None


def s19():
    """체결 뒤 DB 기록 전에 죽고 한참 뒤 재시작 — 다운타임 중 손절 통과를 흡수 뒤에도 판정하는가."""
    reset()
    ex = FakeEx(pos=1.23, entry=PX, px=PX * 0.95)
    c = bars()
    t_fill = c.index[-1] + pd.Timedelta("15s")            # 신호 봉 마감 15초 뒤 체결 (마지막 봉 = 진입 봉)
    A._order_row(run_id=0, timestamp=t_fill.tz_convert(A.KST).isoformat(), mode="live", action="open", side="long", qty=0,
                 notional=984, price=PX, client_id="acrash", status="intent", reason="t")
    ex.orders["acrash"] = {"status": "closed", "filled": 1.23, "average": PX, "id": "o0", "hidden": False, "side": "buy", "n": 1.23}
    A.set_state(seen_bar=c.index[-2].isoformat())
    c = after(c, (PX, PX, PX, PX), (PX, PX, PX * 0.93, PX * 0.95), (PX * 0.95, PX * 0.96, PX * 0.95, PX * 0.95))   # 다운타임 중 손절 통과
    act(ex, c)
    assert ex.pos == 0, "흡수한 포지션의 진입 시각을 재시작 시각으로 적어 다운타임 중 손절 통과를 못 봤다 (만기도 그만큼 밀린다)"
    # 반대 경우: 봇의 마지막 진입은 이미 청산 기록까지 끝났고, 지금 포지션은 사람이 연 것 → 옛 주문 시각으로 거슬러 가면 안 된다
    reset()
    ex = FakeEx(pos=1.23, entry=PX)
    old = (T0 - pd.Timedelta("2D")).tz_convert(A.KST).isoformat()
    for cid, action, st_ in (("aold1", "open", "filled"), ("aold2", "close", "filled")):
        A._order_row(run_id=0, timestamp=old, mode="live", action=action, side="long", qty=1.23, notional=984,
                     price=PX, client_id=cid, status=st_, reason="t")
        ex.orders[cid] = {"status": "closed", "filled": 1.23, "average": PX, "id": cid, "hidden": False, "side": "buy", "n": 1.23}
    A.account(ex, PX)
    got = pd.Timestamp(A.state()["entered_at"])
    assert got > pd.Timestamp.now(tz=A.KST) - pd.Timedelta("1min"), f"사람이 연 포지션을 봇의 옛 진입 시각({got})으로 흡수했다"

def s20():
    """실주문 모드 시작 시 읽기 전용 계정 점검 — 선물 불가 계정(acctLv=1)·출금 권한 키면 주문 경로에 들어가기 전에 멈춘다."""
    good, bad1, bad2 = FakeEx(), FakeEx(), FakeEx()
    bad1.cfg = {**bad1.cfg, "acctLv": "1"}                  # 2026-09-30 실계좌 상태 그대로
    bad2.cfg = {**bad2.cfg, "perm": "read_only,trade,withdraw"}
    with patched(A, MODE="live", HAVE_KEYS=True), patched(builtins, input=lambda msg: "실주문"):
        for ex, should_stop in ((good, False), (bad1, True), (bad2, True)):
            with patched(X, client=lambda ex=ex: ex):
                try:
                    A.confirm_live()
                    stopped = False
                except SystemExit:
                    stopped = True
            assert stopped == should_stop, f"계정 {ex.cfg} 에서 시작 {'계속' if not stopped else '중단'}"
            assert not [c for c in ex.calls if c[0] in ("create_order", "set_leverage", "set_position_mode")], ex.calls


def s21():
    """거래소가 응답으로 거절한 주문은 '상태 불명' 이 아니라 거절이다 — 30분 진입 차단·CRITICAL 대상이 아니다."""
    reset()
    ex = FakeEx()
    ex.modes = ["refuse"]
    c = bars(dip=3)
    act(ex, c)
    with A.db() as db:
        st = db.execute("SELECT status FROM orders WHERE action='open' ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert st.startswith("rejected"), f"거래소 거절을 '{st}' 로 적었다"
    assert A.state()["side"] is None and ex.pos == 0
    act(ex, after(c, (PX, PX, PX * 0.97, PX * 0.97)))       # 다음 봉도 급락 → 미해결 주문이 없으니 바로 다시 낼 수 있다
    assert len(ex.buys()) == 2 and A.state()["side"] == "long", f"거절된 주문이 진입을 막았다: {ex.buys()}"


CASES = [("1", "미완성 신호 봉으로 주문하지 않음", s1), ("2", "진입 전 봉 저가로 손절하지 않음", s2),
         ("2b", "진입 봉의 진입 전 가격으로 익절 기록 안 함", s2b), ("3", "같은 봉 TP/SL → 손절", s3),
         ("4", "갭 손절은 손절선 체결 보장 안 함", s4), ("5", "다운타임 뒤 지난 신호로 진입 안 함", s5),
         ("6", "실계좌 체결 없으면 과거 TP/SL 가로 장부 확정 안 함", s6), ("7", "접수 후 응답 유실 → 중복 제출 없음", s7),
         ("7b", "미해결 주문 중 신규 진입 차단", s7b), ("8", "체결 후 DB 기록 전 종료 → 재시작 대사 복구", s8),
         ("9", "부분 체결 → 남은 수량 기준 복구", s9), ("10", "잔여 포지션 초과 청산·숏 전환 없음", s10),
         ("11", "일봉 경계 미래정보 없음", s11), ("12", "결측·역순·지연 데이터 처리", s12),
         ("13", "최소 수량·가용 잔고 미달 → 제출 전 차단", s13), ("14", "paper 모드 주문 API 0회", s14),
         ("15", "Claude 장애가 청산을 막지 않음", s15), ("16", "청산 실패 → 다음 사이클 재시도", s16),
         ("17", "거래소 거부(체결 0)를 성공으로 보지 않음", s17), ("18", "청산 수량 = 거래소 포지션 전량 (계약 반올림)", s18),
         ("19", "재시작 흡수 포지션도 다운타임 봉을 판정", s19), ("20", "실주문 시작 전 계정 점검 (acctLv·출금 권한)", s20),
         ("21", "거래소 거절 ≠ 상태 불명", s21)]

if __name__ == "__main__":
    bad = []
    for sid, name, fn in CASES:
        try:
            fn()
            res = "통과"
        except AssertionError as e:
            res = f"{'재현됨(OPEN)' if sid in OPEN else '실패'} — {e}"
            bad += [] if sid in OPEN else [sid]
        print(f"[{sid:>3}] {name}: {res}", flush=True)
    print(f"\n{len(CASES) - len(bad)}/{len(CASES)} (OPEN {len(OPEN)}건 제외 실패 {bad or '없음'}) · 임시 폴더 {TMP}")
    sys.exit(1 if bad else 0)
