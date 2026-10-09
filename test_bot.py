"""명세 검증 체크리스트 2·3·5 (네트워크 없음).
  2 룩어헤드  3 CONF3 · 레짐 d→d+1 · edge 재진입 · 손절 cap(청산가 안쪽) · 본전 이동 후퇴 금지 · DROP1 부호 · 갭 체결
  5 락 · clOrdId 중복 · 타임아웃 재시도 · 손절 누락 복구 · 레버리지/마진 모드 불일치 (모의 HTTP / 모의 시장 + Paper)
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

H, DAY = rls.H_MS, rls.DAY_MS
T0 = pd.Timestamp("2026-01-01")
INST = "BTC-USDT-SWAP"


def hourly(days, px=lambda i: 100.0, spread=0.5):
    ts = np.arange(days * 24) * H + A.ms(T0)
    c = np.array([px(i) for i in range(days * 24)], float)
    return pd.DataFrame({"ts": ts, "open": c, "high": c + spread, "low": c - spread, "close": c, "confirm": 1})


# ---------- 2. 룩어헤드 ----------
def test_lookahead():
    h = hourly(200, px=lambda i: 100 + 15 * np.sin(i / 300) + i / 80, spread=1.5)
    x, (eq, tr) = rls.signals(h), rls.simulate(h)
    cut = A.ms(T0 + pd.Timedelta(days=150))
    h2 = h.copy()
    h2.loc[h2.ts >= cut, ["open", "high", "low", "close"]] *= 0.5            # 미래 봉만 변경
    x2, (eq2, tr2) = rls.signals(h2), rls.simulate(h2)
    k = x.index < cut - H                                                   # 봉 i 신호는 봉 i 종가까지만 사용
    assert x[k].drop(columns=["open", "high", "low", "close"]).equals(x2[k].drop(columns=["open", "high", "low", "close"]))
    assert (x.want != 0).any() and len(tr) > 1, "합성 데이터에서 레짐·거래가 있어야 의미 있는 테스트"
    done = tr[tr.exit_ts < cut]
    assert done.reset_index(drop=True).equals(tr2[tr2.exit_ts < cut].reset_index(drop=True)), "과거 거래·DROP1 판정 불변"
    assert np.allclose(eq[eq.index < pd.Timestamp(cut, unit="ms")], eq2[eq2.index < pd.Timestamp(cut, unit="ms")])


def test_daily_requires_24_confirmed():
    h = hourly(3)
    h.loc[30, "confirm"] = 0                                 # 2일차에 미확정 봉
    h = h.drop(index=60)                                      # 3일차 봉 하나 결측
    assert list(rls.daily(h).index) == [T0]


# ---------- 3. 규칙 ----------
def test_conf3_and_regime_d_to_d1():
    reg = pd.Series([1, 1, 1, 0, 1, 1, -1, -1, -1, -1])
    assert list(rls.confirm(reg)) == [0, 0, 1, 0, 0, 0, 0, 0, -1, -1]
    assert list(rls.edges(rls.confirm(reg))) == [False, False, True, False, False, False, False, False, True, False]
    h = hourly(200, px=lambda i: 100 + 15 * np.sin(i / 300) + i / 80, spread=1.5)
    x, conf = rls.signals(h), rls.confirm(rls.regime(rls.daily(h)))
    for day in conf.index[100:110]:
        d23, d22 = A.ms(day) + 23 * H, A.ms(day) + 22 * H
        assert x.loc[d23, "want"] == conf[day], "d 23시 봉 종가(= d+1 00:00)에 확정 레짐_d → d+1 00시 봉 시가에 적용"
        assert x.loc[d22, "want"] == conf[day - pd.Timedelta(days=1)], "d 장중에는 아직 확정 레짐_(d-1)"


def test_stop_cap_be_drop1_units():
    assert abs(rls.liq_dist() - 0.0455) < 1e-12
    assert abs(rls.stop_dist(2, 100) - 0.03) < 1e-12 and abs(rls.stop_dist(10, 100) - 0.0355) < 1e-12, "1.5×ATR, 청산 거리 − 1%p 로 cap"
    for side in (1, -1):
        stop, liq = rls.stop_px(side, 100, rls.stop_dist(10, 100)), 100 * (1 - side * rls.liq_dist())
        assert (stop - liq) * side > 0, "손절은 항상 청산가보다 진입가 쪽"
    assert abs(rls.inside_liq(1, 100, 96) - 97) < 1e-9 and abs(rls.inside_liq(-1, 100, 104) - 103) < 1e-9
    assert abs(rls.be_px(1, 100) - 100.15) < 1e-9 and abs(rls.be_px(-1, 100) - 99.85) < 1e-9
    assert rls.tighten(1, 100.15, 97) == 100.15 and rls.tighten(-1, 99.85, 103) == 99.85, "본전 후 후퇴 금지"
    assert rls.drop1(1, 97.9, 100, 2) and not rls.drop1(1, 98.1, 100, 2), "롱: 24h 역행 ≥ 1 ATR"
    assert rls.drop1(-1, 102.1, 100, 2) and not rls.drop1(-1, 97, 100, 2), "숏: 24h 상승 ≥ 1 ATR"


def sim(bars, want, atr_v=2.0, c24=None):
    """봉 직접 지정: bars = [(open, high, low, close)...], want[i] = 봉 i 종가 때 아는 확정 레짐."""
    ts = np.arange(len(bars)) * H + A.ms(T0)
    x = pd.DataFrame(bars, columns=["open", "high", "low", "close"], index=ts)
    x["reg"], x["want"], x["atr"] = want, want, atr_v
    x["c24"] = c24 if c24 is not None else x.close
    old = rls.signals
    rls.signals = lambda h1, kind=None: x
    try:
        return rls.simulate(None, funding_8h=0)
    finally:
        rls.signals = old


def test_sim_gap_edge_reentry():
    f = (100, 100.5, 99.5, 100)
    # 봉 0 확정 BULL → 봉 1 시가 진입(손절 97) → 봉 3 시가 96 갭 → 시가 체결. BULL 유지 동안 재진입 없음,
    # 봉 5 NEUTRAL → 봉 6 BULL → 봉 7 시가 재진입
    eq, t = sim([f, f, f, (96, 96.5, 95.5, 96), f, f, f, f, f, f], [1, 1, 1, 1, 1, 0, 1, 1, 0, 0])
    assert t.why.iloc[0] == "sl" and abs(t.exit.iloc[0] - 96 * (1 - rls.SLIP)) < 1e-9, "갭은 손절가(97)가 아니라 시가(불리)"
    assert abs(t.stop_dist.iloc[0] - 0.03) < 1e-12 and t.entry_ts.iloc[0] == A.ms(T0) + H
    assert list(t.entry_ts) == [A.ms(T0) + H, A.ms(T0) + 7 * H], f"새 에지에서만 재진입: {list(t.entry_ts)}"
    assert t.why.iloc[1] == "regime" and t.exit_ts.iloc[1] == A.ms(T0) + 9 * H, "확정 레짐 NEUTRAL → 다음 1H 시가 청산"
    loss = t.pnl_pct.iloc[0]
    assert -0.04 < loss < -0.03, f"20x · 증거금 4% · 4% 역행 ≈ 자산 −3.2% − 비용: {loss}"


def test_sim_be_no_retreat_and_drop1():
    f = (100, 100.5, 99.5, 100)
    # 진입 → 봉 2 종가 102.5 (+1 ATR) → 봉 3 부터 손절 = 진입가 × 1.0015 → 봉 4 저가 100 에서 본전 손절
    eq, t = sim([f, f, (100, 102.6, 99.8, 102.5), (102.5, 103, 101, 101.5), (101, 101.2, 100, 100.5), f],
                [1] * 6)
    entry = 100 * (1 + rls.SLIP)
    assert t.why.iloc[0] == "sl" and t.be.iloc[0] and abs(t.exit.iloc[0] - rls.be_px(1, entry) * (1 - rls.SLIP)) < 1e-9
    assert t.exit_ts.iloc[0] == A.ms(T0) + 4 * H, "봉 3 저가 101 은 본전(100.17) 위 → 후퇴 없이 봉 4 에서 체결"
    # DROP1 롱: 봉 2 종가 − 24h 전 종가 = −2.2 ≤ −1 ATR → 봉 3 시가 청산, 같은 레짐 동안 재진입 없음
    eq, t = sim([f, f, (100, 100.5, 97.7, 97.8), (98, 98.5, 97.5, 98), f, f], [1] * 6,
                c24=[100, 100, 100, 100, 100, 100])
    assert list(t.why) == ["drop1"] and t.exit_ts.iloc[0] == A.ms(T0) + 3 * H and abs(t.exit.iloc[0] - 98 * (1 - rls.SLIP)) < 1e-9
    # DROP1 숏: 24h 상승 ≥ 1 ATR
    eq, t = sim([f, f, (100, 102.3, 99.9, 102.2), f, f], [-1] * 5, c24=[100] * 5)
    assert list(t.why) == ["drop1"] and t.side.iloc[0] == -1


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
        if "/account/leverage-info" in url:
            return Resp([{"lever": "10", "mgnMode": "isolated", "instId": INST}])
        raise AssertionError(url)


def test_clordid_no_duplicate_after_timeout():
    s = FakeSession()
    c = okx.Client("k", "s", "p", session=s, sleep=lambda x: None)
    o = c.order(INST, 1, 0.5, "2026010100BTCopen")
    assert s.posts == 1, "타임아웃 후 조회로 접수를 확인했으면 다시 보내지 않는다"
    assert o["state"] == "filled" and o["sz"] == 0.5
    c.order(INST, 1, 0.5, "2026010100BTCopen")
    assert s.posts == 1, "같은 시각 같은 동작(재실행)은 주문을 다시 보내지 않는다"
    assert c.leverage_info(INST) == {"lever": 10.0, "mgnMode": "isolated"}, "레버리지 조회 파싱"


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
    INS = {"ctVal": 0.01, "lotSz": 0.01, "minSz": 0.01, "tickSz": 0.01, "lever": 100.0}

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


class Bot:
    """확정 레짐을 일자별로 지정하고 (rls.confirm 대체), ATR = 2 고정으로 run_cycle 을 돌린다."""
    def __init__(self, days=130):
        tmp = pathlib.Path(tempfile.mkdtemp())
        A.DB_PATH, A.LOCK_PATH, A.INSTS = tmp / "t.db", tmp / "t.lock", [INST]
        A.alert = lambda m: self.alerts.append(m)
        self.alerts, self.conf = [], {}
        self.m = Market(hourly(days))
        self.st = {}
        self.px = okx.Paper(self.m, self.st, 10_000, now=lambda: self.m.now)
        self.old = rls.confirm, rls.atr
        rls.confirm = lambda reg: pd.Series([self.conf.get(t, 0) for t in reg.index], index=reg.index, dtype=int)
        rls.atr = lambda d, kind=None: pd.Series(2.0, index=d.index)

    def close(self):
        rls.confirm, rls.atr = self.old

    def at(self, n, hour=0):
        return A.ms(T0 + pd.Timedelta(days=n)) + hour * H

    def set_bar(self, n, hour, o, h, l, c):
        self.m.h1.loc[self.m.h1.ts == self.at(n, hour), ["open", "high", "low", "close"]] = [o, h, l, c]

    def run(self, n, hour=0):
        self.m.now = self.at(n, hour) + 2 * 60_000
        A.run_cycle(self.px, dt.datetime.fromtimestamp(self.m.now / 1000, dt.UTC))
        with A.db() as c:
            r = c.execute("SELECT conf, actual, flags, stop_px, liq_px, be, note FROM daily WHERE date=?",
                          (str((T0 + pd.Timedelta(days=n)).date()),)).fetchone()
        return dict(zip(("conf", "actual", "flags", "stop", "liq", "be", "note"), r))

    def pos(self):
        return self.px.position(INST)

    def trades(self):
        with A.db() as c:
            return [r[0] for r in c.execute("SELECT why FROM trades")]


def test_cycle_paper_end_to_end():
    b = Bot()
    day = lambda n: T0 + pd.Timedelta(days=n)  # noqa: E731
    try:
        b.conf[day(99)] = 1
        r = b.run(100)
        assert r["actual"] == 0 and "새 에지 대기" in r["note"], "시작 시점에 이미 켜진 레짐에는 진입하지 않는다"
        b.conf[day(100)] = 0
        b.run(101)
        b.conf[day(101)] = 1
        r = b.run(102)                                                    # 새 에지 → 00:02 진입
        p = b.pos()
        assert r["actual"] == 1 and p["side"] == 1 and abs(p["sz"] * 0.01 * 100 - 10_000 * 0.80) < 1, "명목 = 자산 80%"
        assert b.px.leverage_info(INST) == {"lever": 20.0, "mgnMode": "isolated"}
        stop = b.px.stops(INST)[0]
        assert stop["side"] == -1 and stop["sz"] == p["sz"] and abs(stop["trigger"] - p["avgPx"] * 0.97) < 0.011
        assert stop["trigger"] > p["liqPx"] and r["liq"] == p["liqPx"], "손절은 거래소 청산가 안쪽"
        with A.lock():                                                    # 락: 중복 실행 금지
            try:
                A.run_cycle(b.px)
                raise AssertionError("락이 잡혀 있으면 실행되면 안 된다")
            except RuntimeError:
                pass

        # DROP1: 02시 봉 종가 97.6 (24h 전 100 대비 −2.4 ≥ 1 ATR, 저가 97.1 > 손절 97) → 03시 시가 청산
        b.set_bar(102, 2, 100, 100.2, 97.1, 97.6)
        assert b.run(102, 1)["actual"] == 1
        assert b.run(102, 2)["actual"] == 1
        b.m.h1.loc[b.m.h1.ts >= b.at(102, 3), ["open", "high", "low", "close"]] = [97.6, 98.1, 97.1, 97.6]
        r = b.run(102, 3)
        assert r["actual"] == 0 and b.trades() == ["drop1"]
        assert b.run(102, 4)["actual"] == 0 and b.run(103)["actual"] == 0, "같은 확정 레짐 동안 재진입 없음 (edge)"
        b.conf[day(103)] = 0
        b.run(104)
        b.conf[day(104)] = 1
        assert b.run(105)["actual"] == 1, "NEUTRAL 을 거친 새 에지에서 재진입"
        entry = b.pos()["avgPx"]

        # BE1: 00시 봉 종가가 진입가 + 1 ATR 이상 → 손절 = 진입가 × 1.0015, 이후 후퇴 없음
        b.m.h1.loc[b.m.h1.ts >= b.at(105, 0), ["open", "high", "low", "close"]] = [100, 100.5, 99.5, 100]
        r = b.run(105, 1)
        assert r["be"] == 1 and "be" in r["flags"] and abs(b.px.stops(INST)[0]["trigger"] - entry * 1.0015) < 0.011
        assert "stop_reset" not in r["flags"], "본전 이동은 정상 재설정 (경보 아님)"
        r = b.run(105, 2)
        assert abs(r["stop"] - entry * 1.0015) < 1e-9 and len(b.px.stops(INST)) == 1

        # 손절 주문 누락 → 재설정 + 경보
        b.st["stops"][INST] = []
        assert "stop_reset" in b.run(105, 3)["flags"] and len(b.px.stops(INST)) == 1

        # 일일 손실 한도: 당일 시작 자산 대비 −5% → 전 포지션 청산, 그날 신규 진입 중단
        a = A.kv_get("acct")
        a["day_eq0"] = 1e9
        A.kv_set("acct", a)
        r = b.run(105, 4)
        assert r["actual"] == 0 and b.trades()[-1] == "daily_loss"
        b.conf[day(105)] = 0
        b.run(106)
        b.conf[day(106)] = 1
        r = b.run(107)
        assert r["actual"] == 1, "다음 날 새 에지에서 진입 재개"

        # 롱 보유 중 양(+) 펀딩 → 지불 (숏 수취 부호는 test_funding_sign)
        b.m.fund = [(b.at(107, 8), 0.0001)]
        b.run(107, 9)
        assert b.pos()["fundingFee"] < 0, "롱은 양(+) 펀딩 지불"

        # 데이터 공백: 직전 1H 봉 결측 → 신호 갱신 없이 포지션·손절 유지
        b.m.h1 = b.m.h1[b.m.h1.ts != b.at(107, 9)]
        b.conf[day(107)] = 0
        r = b.run(107, 10)
        assert "data_gap" in r["flags"] and r["actual"] == 1 and len(b.px.stops(INST)) == 1

        # 거래소 손절 체결 (Paper.sync 가 1H 봉으로 재생) → stop_hit 기록, 같은 레짐 재진입 금지
        b.conf[day(107)] = 1
        b.set_bar(107, 10, 100, 100.5, 90, 99)
        r = b.run(107, 11)
        assert "stop_hit" in r["flags"] and r["actual"] == 0 and b.trades()[-1] == "sl"
        assert b.run(107, 12)["actual"] == 0, "손절 뒤 같은 확정 레짐 동안 재진입 없음"
    finally:
        b.close()


def test_liquidation_streak_leverage_and_kill():
    b = Bot()
    day = lambda n: T0 + pd.Timedelta(days=n)  # noqa: E731
    try:
        b.run(100)
        # 거래소 강제청산 1건 + 10연패 → 전 종목 진입 중단 / 종목 진입 중단
        for k in range(10):
            b.st["hist"].append({"inst": INST, "uTime": b.at(100, 1) + k, "side": 1, "closeAvgPx": 95.5,
                                 "realizedPnl": -50.0, "fee": -1.0, "fundingFee": 0.0, "type": "3" if k == 9 else "2"})
        b.conf[day(100)] = 1
        r = b.run(101)
        assert "liquidated" in r["flags"] and "entry_hold" in r["flags"] and r["actual"] == 0
        assert A.kv_get("acct")["hold"] and A.kv_get(INST)["streak_hold"] and A.kv_get(INST)["streak"] == 10
        assert any("7연패" in m for m in b.alerts) and any("강제청산" in m for m in b.alerts)
        A.resume()
        # 레버리지 조회 불일치 → 주문 안 함
        b.px.set_leverage = lambda inst, lv: None
        b.st["lever"][INST] = 10
        r = b.run(101, 1)
        assert "order_check_fail" in r["flags"] and r["actual"] == 0, "set-leverage 가 20x 로 적용되지 않으면 주문 안 함"
        # 킬 스위치: 고점 대비 35% → 정지 (수동 확인 전까지)
        a = A.kv_get("acct")
        a["peak"] = 20_000
        A.kv_set("acct", a)
        b.run(101, 2)
        assert A.kv_get("acct")["halted"] and any("킬 스위치" in m for m in b.alerts)
    finally:
        b.close()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
