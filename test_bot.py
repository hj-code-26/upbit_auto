"""명세 검증 체크리스트 2~5 (네트워크 없음).
  2 룩어헤드  3 레짐 d→d+1 · 롱↔숏 전환 · 손절 우선 · 갭 체결 · 손절 후 에지 재진입  4 숏 펀딩 부호
  5 락 · clOrdId 중복 · 타임아웃 재시도 · 손절 주문 누락 복구 (모의 HTTP / 모의 시장 + Paper)
사용: python test_bot.py
"""
import datetime as dt
import os
import pathlib
import tempfile

import numpy as np
import pandas as pd
import requests

os.environ["MODE"] = "dry"
import autotrade as A  # noqa: E402
import okx  # noqa: E402
import rls  # noqa: E402

H, DAY = 3_600_000, rls.DAY_MS
T0 = pd.Timestamp("2026-01-01")


def hourly(days, px=lambda i: 100.0, spread=1.0):
    ts = np.arange(days * 24) * H + A.ms(T0)
    c = np.array([px(i) for i in range(days * 24)], float)
    return pd.DataFrame({"ts": ts, "open": c, "high": c + spread, "low": c - spread, "close": c, "confirm": 1})


def fixed_regime(values):
    """rls.regime 대체: 일자 → 레짐 dict (없으면 0)."""
    return lambda d: pd.Series([values.get(t, 0) for t in d.index], index=d.index, dtype=int)


# ---------- 2. 룩어헤드 ----------
def test_lookahead():
    h = hourly(150, px=lambda i: 100 + 10 * np.sin(i / 200) + i / 50)
    d = rls.daily(h)
    reg, atr = rls.regime(d), rls.atr(d)
    h2 = h.copy()
    h2.loc[h2.ts >= A.ms(T0 + pd.Timedelta(days=120)), ["open", "high", "low", "close"]] *= 3   # 미래 봉만 변경
    d2 = rls.daily(h2)
    k = 120
    assert d.iloc[:k].equals(d2.iloc[:k])
    assert rls.regime(d2).iloc[:k].equals(reg.iloc[:k]) and np.allclose(rls.atr(d2).iloc[:k], atr.iloc[:k], equal_nan=True)
    assert (reg != 0).any(), "합성 데이터에서 레짐이 한 번은 켜져야 의미 있는 테스트"


def test_daily_requires_24_confirmed():
    h = hourly(3)
    h.loc[30, "confirm"] = 0                                 # 2일차에 미확정 봉
    h = h.drop(index=60)                                      # 3일차 봉 하나 결측
    assert list(rls.daily(h).index) == [T0]


# ---------- 3. 시뮬레이션 규칙 ----------
def sim(prices, regimes, atr_v=2.0):
    """일봉 직접 지정: prices = [(open, high, low, close)...], regimes = 일 index → 값."""
    idx = pd.date_range(T0, periods=len(prices))
    d = pd.DataFrame(prices, columns=["open", "high", "low", "close"], index=idx)
    old = rls.regime, rls.atr
    rls.regime = fixed_regime({idx[i]: v for i, v in regimes.items()})
    rls.atr = lambda d: pd.Series(atr_v, index=d.index)
    try:
        return rls.simulate(d, funding_8h=0)
    finally:
        rls.regime, rls.atr = old


def test_regime_applies_next_day_and_flip():
    flat = (100, 101, 99, 100)
    eq, t = sim([flat] * 7, {1: 1, 2: 1, 3: -1, 4: -1})
    assert list(t.entry_day) == [pd.Timestamp("2026-01-03"), pd.Timestamp("2026-01-05")], "d 레짐은 d+1 시가에 체결"
    assert list(t.side) == [1, -1] and t.why.iloc[0] == "regime"
    assert t.exit_day.iloc[0] == t.entry_day.iloc[1], "롱 청산과 숏 진입이 같은 시가"
    assert abs(eq.iloc[-1] - (1 - 4 * rls.COST * rls.MARGIN * rls.LEVER)) < 1e-6, "같은 가격 왕복 2회 = 비용만"


def test_stop_priority_gap_and_edge_reentry():
    flat = (100, 101, 99, 100)
    # 1일 BULL → 2일 진입(손절 94) → 3일 시가 90 갭 → 시가 체결. 레짐은 3·4일 BULL 유지 → 재진입 금지,
    # 5일 NEUTRAL 한 번 → 7일 재진입(6일 레짐 BULL)
    eq, t = sim([flat, flat, flat, (90, 91, 89, 90), flat, flat, flat, flat, flat],
                {1: 1, 2: 1, 3: 1, 4: 1, 5: 0, 6: 1, 7: 0})
    assert t.why.iloc[0] == "stop" and t.exit.iloc[0] == 90, "갭은 손절가(94)가 아니라 시가(90)에 체결"
    assert t.entry_day.iloc[1] == pd.Timestamp("2026-01-08"), f"새 에지에서만 재진입: {list(t.entry_day)}"
    # 장중 손절: 시가 100, 저가 93 → 손절가 94 에 체결. 같은 날 레짐 청산 신호가 와도 손절이 먼저
    eq, t = sim([flat, flat, (100, 101, 93, 99), flat], {1: 1, 2: 0})
    assert t.why.iloc[0] == "stop" and t.exit.iloc[0] == 94


def test_blocked_matches_simulation_rule():
    idx = pd.date_range(T0, periods=6)
    reg = pd.Series([1, 1, 1, 0, 1, 1], index=idx)
    assert rls.blocked(reg.iloc[:3], 1, idx[1]), "손절 이후 계속 BULL → 금지"
    assert rls.blocked(reg.iloc[:1], 1, idx[1]), "손절 이후 판정된 레짐이 아직 없으면 금지"
    assert not rls.blocked(reg, 1, idx[1]), "중간에 NEUTRAL 이 있었으면 해제"


def test_stop_modes_and_liq_first_at_10x():
    assert rls.LEVER == 10
    liq = 100 * (1 - (1 / 10 - rls.MMR))                     # 롱 청산가 ≈ 90.4
    a = rls.stop_price(1, 100, 4.0)                          # A안: 3×ATR = 12 → 88 (청산보다 멂)
    assert a == 88 and rls.beyond_liq(1, a, liq)
    b = rls.stop_price(1, 100, 4.0, liq)                     # B안: 청산 거리 9.6 − 1.5 = 8.1 → 91.9
    assert abs(b - 91.9) < 1e-9 and not rls.beyond_liq(1, b, liq)
    assert rls.stop_price(-1, 100, 1.0, 100 * (1 + 0.096)) == 103, "B안도 3×ATR 이 더 가까우면 그대로"
    flat = (100, 101, 99, 100)
    _, t = sim([flat, flat, (100, 101, 89, 95), flat], {1: 1, 2: 1}, atr_v=4.0)
    assert t.why.iloc[0] == "liq" and abs(t.exit.iloc[0] - liq) < 1e-9, "10x A안: 손절(88)보다 청산(90.4)이 먼저"


# ---------- 4. 펀딩 부호 ----------
def test_funding_sign():
    assert rls.funding_pnl(1, 0.0001, 10_000) == -1.0, "양(+) 펀딩: 롱 지불"
    assert rls.funding_pnl(-1, 0.0001, 10_000) == 1.0, "양(+) 펀딩: 숏 수취"
    assert rls.funding_pnl(-1, -0.0001, 10_000) == -1.0, "음(-) 펀딩: 숏 지불"


# ---------- 5. 모의 HTTP ----------
class Resp:
    def __init__(self, data, code="0", status=200):
        self.status_code, self._j = status, {"code": code, "msg": "", "data": data}

    def json(self):
        return self._j

    def raise_for_status(self):
        raise requests.HTTPError(f"{self.status_code}")


class FakeSession:
    """주문 POST 첫 회는 거래소가 접수했지만 응답이 타임아웃 난 상황."""
    def __init__(self):
        self.orders, self.posts, self.gets = {}, 0, 0

    def request(self, method, url, data=None, headers=None, timeout=None):
        if method == "POST" and "/trade/order" in url:
            self.posts += 1
            body = __import__("json").loads(data)
            self.orders[body["clOrdId"]] = {"state": "filled", "avgPx": "100", "accFillSz": body["sz"], "fee": "-0.05"}
            if self.posts == 1:
                raise requests.Timeout("응답 유실")
            return Resp([{"ordId": "1", "sCode": "0"}])
        if "/trade/order?" in url:
            self.gets += 1
            cid = url.split("clOrdId=")[1]
            return Resp([{**self.orders[cid]}]) if cid in self.orders else Resp([{"sCode": "51603", "sMsg": "x"}], code="1")
        raise AssertionError(url)


def test_clordid_no_duplicate_after_timeout():
    s = FakeSession()
    c = okx.Client("k", "s", "p", session=s, sleep=lambda x: None)
    o = c.order("BTC-USDT-SWAP", 1, 0.5, "20260101BTCopen")
    assert s.posts == 1, "타임아웃 후 조회로 접수를 확인했으면 다시 보내지 않는다"
    assert o["state"] == "filled" and o["sz"] == 0.5
    c.order("BTC-USDT-SWAP", 1, 0.5, "20260101BTCopen")
    assert s.posts == 1, "같은 날 같은 동작(재실행)은 주문을 다시 보내지 않는다"


def test_retry_backoff_then_give_up():
    calls, sleeps = [], []

    class Flaky:
        def request(self, *a, **k):
            calls.append(1)
            if len(calls) <= 2:
                raise requests.ConnectionError("x")
            return Resp([{"ts": "1"}])

    c = okx.Client(session=Flaky(), sleep=sleeps.append)
    assert c.get("/api/v5/public/time") == [{"ts": "1"}] and sleeps == [1, 2]

    class Down:
        def request(self, *a, **k):
            return Resp([], status=503)

    c, sleeps = okx.Client(session=Down(), sleep=sleeps.append), []
    try:
        c.get("/api/v5/public/time")
        raise AssertionError("상한 후에는 예외로 안전 종료")
    except requests.HTTPError:
        pass
    c2 = okx.Client(session=type("Bad", (), {"request": lambda *a, **k: Resp([], code="51000")})(), sleep=sleeps.append)
    try:
        c2.get("/x")
        raise AssertionError
    except okx.OKXError as e:
        assert e.code == "51000" and not sleeps, "일시 장애가 아닌 오류는 재시도하지 않는다"


# ---------- 5. 모의 시장 + Paper 로 run_cycle 전체 ----------
class Market:
    INS = {"ctVal": 0.01, "lotSz": 0.01, "minSz": 0.01, "tickSz": 0.1, "lever": 100.0}

    def __init__(self, h1):
        self.h1, self.now, self.fund = h1, 0, []

    def instrument(self, inst):
        return self.INS

    def candles(self, inst, since):
        h = self.h1[(self.h1.ts >= since) & (self.h1.ts < self.now)].copy()
        h["confirm"] = (h.ts + H <= self.now).astype(int)
        return h

    def ticker(self, inst):
        h = self.h1[self.h1.ts < self.now]
        return {"last": float(h.close.iloc[-1]), "open_utc0": float(h[h.ts >= self.now - self.now % DAY].open.iloc[0])}

    def funding_history(self, inst, since):
        return [f for f in self.fund if since < f[0] <= self.now]


def test_cycle_paper_end_to_end():
    tmp = pathlib.Path(tempfile.mkdtemp())
    A.DB_PATH, A.LOCK_PATH, A.INSTS = tmp / "t.db", tmp / "t.lock", ["BTC-USDT-SWAP"]
    A.alert = lambda m: A.log.warning("ALERT(test) %s", m)
    m = Market(hourly(130))
    st = {}
    px = okx.Paper(m, st, 10_000, now=lambda: m.now)
    regs = {}
    old = rls.regime
    rls.regime = fixed_regime(regs)
    day = lambda n: T0 + pd.Timedelta(days=n)  # noqa: E731

    def run(n, **reg):
        regs.update({day(n - 1): reg.get("r", 0)})
        m.now = A.ms(day(n)) + 5 * 60_000
        A.run_cycle(px, dt.datetime.fromtimestamp(m.now / 1000, dt.UTC))
        with A.db() as c:
            return dict(zip(("regime", "target", "actual", "flags", "stop_px"),
                            c.execute("SELECT regime, target, actual, flags, stop_px FROM daily WHERE date=?",
                                      (str(day(n).date()),)).fetchone()))

    try:
        r = run(100, r=1)
        p = px.position("BTC-USDT-SWAP")
        assert r["actual"] == 1 and p["side"] == 1
        sz = p["sz"]
        assert abs(sz * 0.01 * 100 - 10_000 * 0.20) < 1, f"명목 = 자산의 20% (2% × 10x): {sz}"
        assert px.leverage_info("BTC-USDT-SWAP")["lever"] == 10 and p["liqPx"] < p["avgPx"] - 3 * 2.0
        stop = px.stops("BTC-USDT-SWAP")[0]
        assert stop["side"] == -1 and stop["sz"] == sz and abs(stop["trigger"] - (p["avgPx"] - 3 * 2.0)) < 0.11
        with A.lock():                                                 # 락: 중복 실행 금지
            try:
                A.run_cycle(px)
                raise AssertionError("락이 잡혀 있으면 실행되면 안 된다")
            except RuntimeError:
                pass

        # 손절 주문 누락 → 재설정 + 경보
        st["stops"]["BTC-USDT-SWAP"] = []
        assert "stop_reset" in run(101, r=1)["flags"] and len(px.stops("BTC-USDT-SWAP")) == 1

        # 장중 급락 → Paper.sync 가 손절 체결 → 같은 방향 재진입 금지 (레짐 BULL 유지)
        i = m.h1.index[m.h1.ts == A.ms(day(101)) + 10 * H][0]
        m.h1.loc[i, "low"] = 80
        r = run(102, r=1)
        assert "stop_hit" in r["flags"] and r["actual"] == 0 and r["target"] == 0
        assert st["hist"][-1]["closeAvgPx"] == px.st["hist"][-1]["closeAvgPx"] < 100
        assert run(103, r=1)["actual"] == 0, "레짐이 안 꺼졌으면 계속 대기"
        run(104, r=0)
        assert run(105, r=1)["actual"] == 1, "NEUTRAL 을 거친 새 에지에서 재진입"

        # 롱 → 숏 전환: 청산 확인 후 진입, 숏 손절은 위쪽
        r = run(106, r=-1)
        p = px.position("BTC-USDT-SWAP")
        assert r["actual"] == -1 and p["side"] == -1 and px.stops("BTC-USDT-SWAP")[0]["trigger"] > p["avgPx"]

        # 숏 보유 중 양(+) 펀딩 → 수취
        m.fund = [(A.ms(day(106)) + 8 * H, 0.0001)]
        run(107, r=-1)
        assert px.position("BTC-USDT-SWAP")["fundingFee"] > 0

        # 데이터 공백: 전일 봉 결측 → 신호 갱신 없이 포지션·손절 유지
        m.h1 = m.h1[~((m.h1.ts >= A.ms(day(107))) & (m.h1.ts < A.ms(day(108))))]
        r = run(108, r=1)
        assert "data_gap" in r["flags"] and r["actual"] == -1 and len(px.stops("BTC-USDT-SWAP")) == 1

        # 일일 손실 한도: 전일 대비 −5% 면 신규 진입 없음 (청산은 함)
        A.kv_set("acct", {"date": "2000-01-01", "equity": 1e9})
        r = run(109, r=1)
        assert "daily_loss" in r["flags"] and r["actual"] == 0
    finally:
        rls.regime = old


def test_liquidation_streak_and_leverage_check():
    tmp = pathlib.Path(tempfile.mkdtemp())
    A.DB_PATH, A.LOCK_PATH, A.INSTS = tmp / "q.db", tmp / "q.lock", ["BTC-USDT-SWAP"]
    A.alert = lambda m: A.log.warning("ALERT(test) %s", m)
    m = Market(hourly(130, spread=0.5))
    st = {}
    px = okx.Paper(m, st, 10_000, now=lambda: m.now)
    regs, old = {}, rls.regime
    rls.regime = fixed_regime(regs)
    rls_atr = rls.atr
    rls.atr = lambda d: pd.Series(4.0, index=d.index)        # 손절 12 > 청산 거리 ≈ 9.6 → 청산이 먼저
    day = lambda n: T0 + pd.Timedelta(days=n)  # noqa: E731

    def run(n, r):
        regs[day(n - 1)] = r
        m.now = A.ms(day(n)) + 5 * 60_000
        A.run_cycle(px, dt.datetime.fromtimestamp(m.now / 1000, dt.UTC))
        with A.db() as c:
            return dict(zip(("actual", "flags", "liq_px", "sl"), c.execute(
                "SELECT actual, flags, liq_px, sl_beyond_liq FROM daily WHERE date=?", (str(day(n).date()),)).fetchone()))

    try:
        n = 100
        for k in range(3):                                   # 진입 → 장중 −15% → 강제청산, 3회
            r = run(n, 1)
            assert r["actual"] == 1 and r["sl"] == 1 and "sl_beyond_liq" in r["flags"] and r["liq_px"] < 100
            i = m.h1.index[m.h1.ts == A.ms(day(n)) + 10 * H][0]
            m.h1.loc[i, "low"] = 85
            r = run(n + 1, 1)
            assert "liquidated" in r["flags"] and r["actual"] == 0
            loss = st["hist"][-1]["realizedPnl"]
            assert -0.03 * 10_000 < loss < -0.02 * 10_000 * 0.9, f"손실 ≈ 증거금 2% + 수수료: {loss}"
            run(n + 2, 0)                                    # 새 에지
            n += 3
        assert A.kv_get("BTC-USDT-SWAP")["liq_hold"]
        r = run(n, 1)
        assert "liq_hold" in r["flags"] and r["actual"] == 0, "3회 연속 청산 → 수동 확인 전까지 신규 진입 없음"
        s = A.kv_get("BTC-USDT-SWAP")
        s.update(liq_hold=False, liq_streak=0)
        A.kv_set("BTC-USDT-SWAP", s)
        px.set_leverage = lambda inst, lv: None              # set-leverage 가 적용되지 않은 거래소
        st["lever"]["BTC-USDT-SWAP"] = 5
        r = run(n + 1, 1)
        assert "order_check_fail" in r["flags"] and r["actual"] == 0, "레버리지 조회 불일치 → 주문 안 함"
    finally:
        rls.regime, rls.atr = old, rls_atr


def test_kill_switch():
    tmp = pathlib.Path(tempfile.mkdtemp())
    A.DB_PATH, A.LOCK_PATH, A.INSTS = tmp / "k.db", tmp / "k.lock", ["BTC-USDT-SWAP"]
    m = Market(hourly(130))
    px = okx.Paper(m, {}, 10_000, now=lambda: m.now)
    old = rls.regime
    rls.regime = fixed_regime({})
    try:
        m.now = A.ms(T0 + pd.Timedelta(days=100)) + 300_000
        A.kv_set("BTC-USDT-SWAP", {"start_eq": 10_000, "start_day": "2026-04-01", "peak": 20_000, "realized": 0.0,
                                   "fee": 0.0, "funding": 0.0, "hist_ts": 0, "side": 0})
        A.run_cycle(px, dt.datetime.fromtimestamp(m.now / 1000, dt.UTC))
        assert A.kv_get("BTC-USDT-SWAP")["halted"], "고점 대비 50% 낙폭 → 정지"
    finally:
        rls.regime = old


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
