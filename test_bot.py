"""검증 체크리스트 2·3·5 (네트워크 없음).
  2 룩어헤드  3 CONF3 · 레짐 d→d+1 · edge 재진입 · 손절 cap · 본전 후퇴 금지 · DROP1 부호 · 갭 체결 · 동시 10% 상한과 우선순위
    · 주말을 낀 계좌 일손실 · 주식 후보 교체 청산
  5 락 · clOrdId 중복 · 타임아웃 재시도 · 손절 누락 복구 · 레버리지/마진 모드 불일치 · 기업 이벤트 차단
사용: python test_bot.py
"""
import datetime as dt
import os
import sys
import pathlib
import tempfile

import numpy as np
import pandas as pd
import requests

os.environ["MODE"] = "dry"
import autotrade as A  # noqa: E402
import okx  # noqa: E402
import rls  # noqa: E402
import stocks  # noqa: E402

for _k in ("OKX_API_KEY", "OKX_API_SECRET", "OKX_API_PASSPHRASE"):
    os.environ.pop(_k, None)                         # 테스트는 실제 잔액을 조회하지 않는다
for _h in [h for h in __import__("logging").getLogger().handlers if isinstance(h, __import__("logging").FileHandler)]:
    __import__("logging").getLogger().removeHandler(_h)   # 가짜 주문이 운용 로그(autotrade.log)에 섞이지 않게

BTC, ETH = rls.COINS
T0 = pd.Timestamp("2026-01-01")                      # 목요일


# ---------- 합성 리플레이: 신호를 직접 지정 ----------
def frame(days, conf, atr_v=2.0, px=100.0, bars=None, index=None):
    """conf[k] = 봉 k 종가 때 아는 확정 레짐. bars = {k: (o, h, l, c)} 로 특정 봉 지정."""
    idx = index if index is not None else pd.date_range(T0, periods=days, freq="D")
    x = pd.DataFrame({"open": px, "high": px + 0.5, "low": px - 0.5, "close": px}, index=idx[:days], dtype=float)
    for k, b in (bars or {}).items():
        x.iloc[k, :4] = b
    x["conf"] = conf if np.ndim(conf) else [conf] * days
    x["reg"], x["atr"], x["prev"] = x.conf, atr_v, x.close.shift()
    return x


def sim(coins=None, stocks_=None, cands=None, **kw):
    """rls.prep 을 항등으로, rls.m5 를 cands(날짜 → 목록) 로 대체해 리플레이."""
    coins, stocks_ = coins or {}, stocks_ or {}
    old = rls.prep, rls.m5, rls.LEVER
    rls.prep, rls.LEVER = (lambda d: d), 20                 # 규칙 테스트의 수치(손절 3% 등)는 명세 20x 기준
    rls.m5 = lambda closes, members, listed, n=5: (cands or (lambda d: list(stocks_)))(closes.index[-1])
    try:
        allx = [*coins.values(), *stocks_.values()]
        return rls.simulate(coins, stocks_, lambda d: set(stocks_), set(stocks_), min(x.index[0] for x in allx)
                            + pd.Timedelta(days=1), funding_8h=0, slip=0, **kw)
    finally:
        rls.prep, rls.m5, rls.LEVER = old


def test_conf3_regime_d_to_d1():
    reg = pd.Series([1, 1, 1, 0, 1, 1, -1, -1, -1, -1])
    assert list(rls.confirm(reg)) == [0, 0, 1, 0, 0, 0, 0, 0, -1, -1]
    eq, t, _ = sim({BTC: frame(10, [0, 0, 1, 1, 1, 1, 0, 0, 0, 0])})
    assert list(t.entry_ts) == [T0 + pd.Timedelta(days=3)], "확정 레짐_d → d+1 UTC 00:00 시가 진입"
    assert t.exit_ts.iloc[0] == T0 + pd.Timedelta(days=7) and t.why.iloc[0] == "regime", "NEUTRAL → 다음 시가 청산"
    eq, t, _ = sim({BTC: frame(10, [0, -1, -1, -1, -1, 1, 1, 1, 0, 0])})
    assert list(t.side) == [-1, 1] and list(t.why) == ["regime", "regime"] and t.exit_ts[0] == t.entry_ts[1], "코인 숏 → 반대 전환은 청산 후 같은 시가에 진입"


def test_stop_cap_be_no_retreat():
    assert abs(rls.liq_dist(20, 0.02) - 0.0295) < 1e-12 and abs(rls.liq_dist(20, 0.004) - 0.0455) < 1e-12
    assert abs(rls.stop_dist(2, 100, 20, 0.004) - 0.03) < 1e-12, "1.5×ATR/신호 봉 종가"
    assert abs(rls.stop_dist(10, 100, 20, 0.02) - 0.0195) < 1e-12, "청산 거리 − 1%p 로 cap"
    for side in (1, -1):
        for mmr in (0.004, 0.02):
            stop = rls.stop_px(side, 100, rls.stop_dist(10, 100, 20, mmr))
            assert (stop - 100 * (1 - side * rls.liq_dist(20, mmr))) * side > 0, "손절은 항상 청산가 안쪽"
    assert abs(rls.inside_liq(1, 100, 96) - 97) < 1e-9 and abs(rls.inside_liq(-1, 100, 104) - 103) < 1e-9
    assert rls.tighten(1, 100.15, 97) == 100.15 and rls.tighten(-1, 99.85, 103) == 99.85, "본전 후 후퇴 금지"
    assert rls.be_hit(1, 102, 100, 2) and not rls.be_hit(1, 101.9, 100, 2) and rls.be_hit(-1, 98, 100, 2)
    # 리플레이: 봉 2 종가 +1 ATR → 봉 3 부터 손절 = 진입가 × 1.0015, 봉 3 저가가 원래 손절 위라도 본전에서 체결
    eq, t, _ = sim({BTC: frame(6, 1, bars={2: (100, 102.6, 99.8, 102.5), 3: (102, 102, 100, 100.5)})})
    assert t.why.iloc[0] == "sl" and t.be.iloc[0] and abs(t.exit.iloc[0] - 100.15) < 1e-9
    assert t.exit_ts.iloc[0] == T0 + pd.Timedelta(days=3)


def test_drop1_sign_and_gap_fill():
    assert rls.drop1(1, 97.9, 100, 2) and not rls.drop1(1, 98.1, 100, 2), "롱: 전일 대비 하락 ≥ 1 ATR"
    assert rls.drop1(-1, 102.1, 100, 2) and not rls.drop1(-1, 97, 100, 2), "숏: 전일 대비 상승 ≥ 1 ATR"
    eq, t, _ = sim({BTC: frame(6, 1, bars={2: (100, 100.5, 97.7, 97.8), 3: (98, 98.5, 97.5, 98)})})
    assert list(t.why) == ["drop1"] and t.exit_ts.iloc[0] == T0 + pd.Timedelta(days=3) and t.exit.iloc[0] == 98
    eq, t, _ = sim({BTC: frame(6, -1, bars={2: (100, 102.3, 99.9, 102.2)})})
    assert list(t.why) == ["drop1"] and t.side.iloc[0] == -1
    # 갭: 시가 96 이 손절 97 너머 → 시가 체결 (청산가 95.45 보다는 안쪽 → sl)
    eq, t, _ = sim({BTC: frame(5, 1, bars={3: (96, 96.5, 95.5, 96)})})
    assert t.why.iloc[0] == "sl" and t.exit.iloc[0] == 96
    eq, t, _ = sim({BTC: frame(5, 1, bars={3: (90, 90.5, 89.5, 90)})})
    assert t.why.iloc[0] == "liq" and abs(t.pnl.iloc[0] + t.margin.iloc[0] + t.fee.iloc[0]) < 1e-9, "격리: 손실 = 증거금"


def test_edge_reentry():
    # 진입(봉 2) → 봉 3 손절 → BULL 유지 동안 재진입 없음 → 봉 5 NEUTRAL → 봉 6 BULL → 봉 7 재진입
    eq, t, _ = sim({BTC: frame(10, [1, 1, 1, 1, 1, 0, 1, 1, 0, 0], bars={3: (100, 100, 96, 99)})})
    assert list(t.entry_ts) == [T0 + pd.Timedelta(days=1), T0 + pd.Timedelta(days=7)], list(t.entry_ts)
    # 반대 방향으로 바뀌어도 해제 (BULL 손절 → BEAR → BULL)
    eq, t, _ = sim({BTC: frame(10, [1, 1, 1, -1, 1, 1, 1, 1, 0, 0], bars={2: (100, 100, 96, 99)})})
    assert list(t.side) == [1, -1, 1]


def test_c_rules_replay():
    """C①: 본전(BE1) 뒤 손절은 edge 없음 → 같은 방향 유지 중 다음 시가 재진입 (C1 이면 막힘).
    C②: 신호 봉 ATR/종가 > 8% 면 진입 대기."""
    be = {2: (100, 102.6, 99.8, 102.5), 3: (102, 102, 100, 100.5)}         # 봉 2 BE → 봉 3 본전 손절
    d4 = T0 + pd.Timedelta(days=4)
    _, t, d = sim({BTC: frame(8, 1, bars=be)})
    assert t.be.iloc[0] and t.why.iloc[0] == "sl" and d.n_pos[d4] == 1, "본전 손절 다음 시가 재진입"
    _, t, d = sim({BTC: frame(8, 1, bars=be)}, be_edge=True)
    assert d.n_pos[d4:].sum() == 0, "C1: 본전 손절도 edge"
    assert rls.edge_after("sl", False) and rls.edge_after("drop1", True) and not rls.edge_after("sl", True)
    _, t, d = sim({BTC: frame(8, 1, atr_v=9.0)})
    assert len(t) == 0 and d.n_pos.sum() == 0, "ATR 9% > 8% → 진입 없음"
    _, t, d = sim({BTC: frame(8, 1, atr_v=9.0)}, atr_max=None)
    assert d.n_pos.iloc[-1] == 1


def test_margin_cap_priority():
    """BTC·ETH 진입(4%) 뒤 주식 후보 5개 → 동시 10% 한도로 M5 상위 3개만 진입, 막힌 2건 기록."""
    sess = pd.bdate_range(T0, periods=10)
    order = ["S3", "S1", "S4", "S0", "S2"]
    st = {k: frame(10, [1] * 7 + [0] * 3, index=sess) for k in order}          # 마지막에 꺼서 거래 기록으로 확인
    _, t, d = sim({BTC: frame(14, [1] * 12 + [0, 0]), ETH: frame(14, [1] * 12 + [0, 0])}, st, cands=lambda x: order)
    assert set(t[t.sleeve == "stock"].inst) == {"S3", "S1", "S4"}, "M5 순위 상위부터 채운다"
    assert d.blocked.sum() >= 2 and d.n_pos.max() == 5 and d.margin_pct.max() < rls.MAX_TOTAL + 1e-3


def test_daily_loss_over_weekend():
    """금(1/2) 주식·코인 보유 → 토(1/3) 코인 급락 (손절 전, 장중 저가 유지) → 일(1/4) 00:00 기록 −5% 이하
    → 코인은 일요일 시가 청산, 주식은 다음 정규장(월 1/5) 시가 청산, 그 시각 신규 진입 없음."""
    sess = pd.bdate_range(T0, periods=8)                      # 1/1 목 ~
    days = 10
    # BTC·ETH 숏 보유 중 토요일 +3.4% → 자산 ≈ −2.7% (한도 1% 로 낮춰 확인)
    up = {2: (100, 103.5, 99.9, 103.4)}                       # 토요일 (손절 103.55 직전)
    c = {BTC: frame(days, -1, atr_v=3.0, bars=up), ETH: frame(days, -1, atr_v=3.0, bars=up)}
    s = {"S0": frame(8, 1, atr_v=4.0, index=sess, bars={1: (100, 100.4, 99.6, 100)})}
    eq, t, d = sim(c, s, cands=lambda x: ["S0"], daily_loss=0.01)
    dl = d[d.daily_loss]
    assert len(dl) and dl.index[0] == pd.Timestamp("2026-01-04"), dl.index
    coin_x = t[(t.sleeve == "coin") & (t.why == "daily_loss")]
    assert set(coin_x.exit_ts) == {pd.Timestamp("2026-01-04")}, "코인: 일요일 00:00 시가 청산"
    st_x = t[(t.sleeve == "stock") & (t.why == "daily_loss")]
    assert list(st_x.exit_ts) == [pd.Timestamp("2026-01-05")], "주식: 다음 정규장(월) 시가 청산"
    st_in = t[(t.sleeve == "stock")].entry_ts
    assert pd.Timestamp("2026-01-05") not in set(st_in), "그 체결 시각 신규 진입 없음"


def test_candidate_swap_exit():
    sess = pd.bdate_range(T0, periods=40)
    st = {"A": frame(40, 1, index=sess), "B": frame(40, [1] * 37 + [0] * 3, index=sess)}
    cands = lambda d: ["A"] if d < sess[21] else ["B"]  # noqa: E731
    eq, t, _ = sim({}, st, cands=cands)
    a = t[t.inst == "A"]
    k0 = sess.searchsorted(T0 + pd.Timedelta(days=1)) - 1
    refresh2 = sess[max(k0, 0) + rls.M5_EVERY]
    nxt = sess[sess.get_loc(refresh2) + 1]
    assert list(a.why) == ["candidate"] and a.exit_ts.iloc[0] == nxt, (a, nxt)
    assert t[t.inst == "B"].entry_ts.min() == nxt, "새 후보는 갱신 다음 거래일 시가부터"


def test_lookahead():
    rng = np.random.default_rng(1)
    n = 500
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    c = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.03, n)))
    d = pd.DataFrame({"open": c * (1 + rng.normal(0, 0.005, n)), "high": c * 1.02, "low": c * 0.98, "close": c}, index=idx)
    sess = pd.bdate_range("2024-01-01", periods=n)
    S = {f"T{k}": pd.DataFrame({"open": v, "high": v * 1.01, "low": v * 0.99, "close": v}, index=sess)
         for k in range(8) for v in [100 * np.exp(np.cumsum(rng.normal(0.0008 * (k - 3), 0.02, n)))]}
    cut = pd.Timestamp("2025-03-01")
    d2, S2 = d.copy(), {k: v.copy() for k, v in S.items()}
    d2.loc[d2.index >= cut] *= 0.5
    for v in S2.values():
        v.loc[v.index >= cut] *= 1.7
    x, x2 = rls.prep(d), rls.prep(d2)
    k = x.index < cut
    assert x[k].equals(x2[k]), "미래 봉을 바꿔도 과거 레짐·CONF3·ATR 불변"
    cl, cl2 = (pd.DataFrame({t: v.close for t, v in s.items()}) for s in (S, S2))
    u = cl.index[cl.index < cut][-1]
    assert rls.m5(cl.loc[:u], set(S), set(S)) == rls.m5(cl2.loc[:u], set(S), set(S)), "과거 후보 불변"
    assert rls.m5(cl.loc[:u], set(S), set(S)), "이력 ≥ 260 이면 후보가 있어야 의미 있는 테스트"
    r = [rls.simulate({BTC: dd}, ss, lambda _: set(S), set(S), "2024-12-01") for dd, ss in ((d, S), (d2, S2))]
    (eq, t, _), (eq2, t2, _) = r
    assert len(t) > 0
    done = t[t.exit_ts < cut - pd.Timedelta(days=1)].reset_index(drop=True)
    assert done.equals(t2[t2.exit_ts < cut - pd.Timedelta(days=1)].reset_index(drop=True)), "과거 거래·DROP1 판정 불변"
    assert np.allclose(eq[eq.index < cut], eq2[eq2.index < cut])


def test_m5_rank():
    sess = pd.bdate_range("2024-01-01", periods=300)
    up = lambda r: pd.Series(100 * (1 + r) ** np.arange(300), index=sess)  # noqa: E731
    cl = pd.DataFrame({"A": up(0.003), "B": up(0.001), "C": up(0.002), "D": up(0.004), "GOOG": up(-0.001)})
    cl["NEW"] = cl.A.where(np.arange(300) >= 100)                          # 이력 200 일 < 260 → 후보 제외
    assert rls.m5(cl, set(cl.columns), {"A", "B", "C", "GOOG", "NEW"}, n=2) == ["A", "C"], "OKX 상장만, 점수 순"
    assert rls.okx_sym("BRK-B") == "BRKB"


# ---------- 기업 이벤트 ----------
def test_corporate_event_block(tmp=None):
    tmp = pathlib.Path(tempfile.mkdtemp())
    f = tmp / "ev.csv"
    f.write_text("date,ticker,type\n2026-01-07,AAPL,split\n")
    sess = pd.bdate_range("2026-01-01", periods=10)
    ev = lambda day, **k: stocks.events(pd.Timestamp(day), sess, path=f, **k)  # noqa: E731
    assert stocks.blocked(ev("2026-01-06"), "AAPL") and stocks.blocked(ev("2026-01-08"), "AAPL"), "전·후 영업일 차단"
    assert not stocks.blocked(ev("2026-01-05"), "AAPL") and not stocks.blocked(ev("2026-01-09"), "AAPL")
    snaps = {pd.Timestamp("2026-01-01"): {"AAPL", "XYZ"}, pd.Timestamp("2026-01-12"): {"AAPL"}}
    assert stocks.blocked(ev("2026-01-09", snaps=snaps), "XYZ") == ["index_removal"]
    ann = [(A.ms("2026-01-06"), "OKX to adjust TSLA-USDT-SWAP for stock split")]
    assert stocks.blocked(ev("2026-01-07", announcements=ann), "TSLA")
    assert not stocks.blocked(ev("2026-01-07", announcements=[(A.ms("2026-01-06"), "TSLA listing")]), "TSLA")


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
        self.orders, self.posts = {}, 0

    def request(self, method, url, data=None, headers=None, timeout=None):
        if method == "POST" and "/trade/order" in url:
            self.posts += 1
            body = __import__("json").loads(data)
            self.last_body = data
            self.orders[body["clOrdId"]] = {"state": "filled", "avgPx": "100", "accFillSz": body["sz"], "fee": "-0.05"}
            if self.posts == 1:
                raise requests.Timeout("응답 유실")
            return Resp([{"ordId": "1", "sCode": "0"}])
        if "/trade/order?" in url:
            cid = url.split("clOrdId=")[1]
            return Resp([{**self.orders[cid]}]) if cid in self.orders else Resp([{"sCode": "51603", "sMsg": "x"}], code="1")
        if "/account/leverage-info" in url:
            return Resp([{"lever": "10", "mgnMode": "isolated", "instId": BTC, "posSide": "long"},
                         {"lever": "20", "mgnMode": "isolated", "instId": BTC, "posSide": "short"}])
        if "/public/position-tiers" in url:
            assert "tdMode=isolated" in url and "instFamily=BTC-USDT" in url
            return Resp([{"tier": "2", "mmr": "0.005"}, {"tier": "1", "mmr": "0.004"}])
        if "/public/instruments" in url:
            return Resp([{"instId": "AAPL-USDT-SWAP", "instCategory": "3", "state": "live"},
                         {"instId": "XX-USDT-SWAP", "instCategory": "3", "state": "suspend"},
                         {"instId": "BTC-USDT-SWAP", "instCategory": "1", "state": "live"}])
        raise AssertionError(url)


def test_clordid_no_duplicate_after_timeout():
    s = FakeSession()
    c = okx.Client("k", "s", "p", session=s, sleep=lambda x: None)
    o = c.order(BTC, 1, 0.5, "r260101cBTCopen")
    assert s.posts == 1, "타임아웃 후 조회로 접수를 확인했으면 다시 보내지 않는다"
    assert o["state"] == "filled" and o["sz"] == 0.5
    c.order(BTC, 1, 0.5, "r260101cBTCopen")
    assert s.posts == 1, "같은 날 같은 작업·동작(재실행)은 주문을 다시 보내지 않는다"
    assert c.leverage_info(BTC) == {"mgnMode": "isolated", 1: 10.0, -1: 20.0}, "헤지 모드: 방향별 레버리지"
    assert __import__("json").loads(s.last_body)["posSide"] == "long", "헤지 모드 주문에는 posSide"
    assert c.tier1_mmr(BTC) == 0.004 and c.stock_perps() == {"AAPL": "AAPL-USDT-SWAP"}
    assert "secret" not in repr(c) and "k" not in repr(c).replace("Client", "")


def test_okx_balance_readonly():
    assert A.okx_balance(okx.Paper(Market(), {}, 10_000)) is None, "키 없으면 조회 안 함"

    class Bal:
        def request(self, method, url, **k):
            assert method == "GET" and "/account/balance" in url, "잔액 조회 외 호출 금지"
            return Resp([{"totalEq": "7.12", "details": [{"ccy": "USDT", "availBal": "7.0", "eq": "7.12", "upl": "0.1"}]}])
    old = okx.Client.__init__
    okx.Client.__init__ = lambda self, *a, **k: old(self, *a, **{**k, "session": Bal()})
    os.environ.update(OKX_API_KEY="k", OKX_API_SECRET="s", OKX_API_PASSPHRASE="p")
    try:
        b = A.okx_balance(okx.Paper(Market(), {}, 10_000))
        assert b["eq"] == 7.12 and b["avail"] == 7.0 and abs(b["realized"] - 7.02) < 1e-9 and not b["demo"]
    finally:
        okx.Client.__init__ = old
        for k in ("OKX_API_KEY", "OKX_API_SECRET", "OKX_API_PASSPHRASE"):
            os.environ.pop(k)


def test_preflight_readonly():
    class Ex(Market):
        cfg = {"posMode": "long_short_mode", "perm": "read_only,trade", "acctLv": "2"}
        bal, pos = (50.0, 50.0, 50.0), {}

        def config(self):
            return self.cfg

        def balance(self):
            return self.bal

        def positions(self, inst):
            return self.pos.get(inst, {})

        def stops(self, inst):
            return []

        def __getattr__(self, k):
            raise AssertionError(f"점검은 조회만: {k} 호출 금지")
    tmp = pathlib.Path(tempfile.mkdtemp())
    A.DB_PATH = tmp / "p.db"
    e = Ex()
    e.now = A.ms("2026-01-01")
    e.d.loc[:, ["open", "high", "low", "close"]] = 80_000.0
    bad, info = A.preflight(e)
    need = A.min_capital(e)[BTC]
    assert abs(need - 0.01 * 0.01 * 80_000 / (0.02 / 3 * 30)) < 1e-6 and not bad, (bad, info)
    e.cfg = {"posMode": "net_mode", "perm": "read_only", "acctLv": "1"}
    e.bal, e.pos = (7.0, 7.0, 7.0), {BTC: {1: {"sz": 1}}}
    bad, _ = A.preflight(e)
    assert len(bad) == 5, bad                                 # 모드·권한·계정모드·최소 자금·모르는 포지션


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

    c = okx.Client(session=Down(), sleep=lambda x: None)
    try:
        c.get("/api/v5/public/time")
        raise AssertionError("상한 후에는 예외로 안전 종료")
    except requests.HTTPError:
        pass


# ---------- 5. 모의 시장 + Paper 로 코인 작업 ----------
class Market:
    """일봉(d)을 4H 봉 6개로 펼쳐 준다 (각 4H 봉 = 그날 OHLC → 어느 경계로 묶어도 일봉 OHLC 가 나온다)."""
    INS = {"ctVal": 0.01, "lotSz": 0.01, "minSz": 0.01, "tickSz": 0.01, "lever": 100.0}

    def __init__(self, n=120):
        self.days = pd.date_range("2025-09-01", periods=n, freq="D")
        self.d = pd.DataFrame({"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0}, index=self.days)
        self.now = 0

    def instrument(self, inst):
        return self.INS

    def tier1_mmr(self, inst):
        return 0.004

    def candles(self, inst, since, bar="1H"):
        if bar != "4H":
            return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "confirm"])
        rows = [(A.ms(t) + h * 4 * 3_600_000, *r) for t, r in zip(self.d.index, self.d.itertuples(index=False))
                for h in range(6)]
        out = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close"])
        out["confirm"] = 1
        return out[(out.ts >= since) & (out.ts + 4 * 3_600_000 <= self.now)]

    def ticker(self, inst):
        return {"last": float(self.d[self.d.index < pd.Timestamp(self.now, unit="ms") - pd.Timedelta(hours=1)].close.iloc[-1])}

    def funding_history(self, inst, since):
        return []


class Bot:
    """몫별 확정 레짐을 conf[(몫 경계 시각)] 로 지정하고 (rls.prep 대체, ATR = 2 고정) run_job 을 돌린다."""
    def __init__(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        A.DB_PATH, A.LOCK_PATH = tmp / "t.db", tmp / "t.lock"
        self.alerts, self.conf, self.atr = [], {}, 2.0
        A.alert = self.alerts.append
        self.m = Market()
        self.st = {}
        self.px = okx.Paper(self.m, self.st, 10_000, now=lambda: self.m.now)
        self.old = rls.prep

        def prep(d):
            x = self.old(d)
            x["conf"] = [self.conf.get(t, 0) for t in x.index]
            x["atr"] = self.atr
            return x
        rls.prep = prep

    def close(self):
        rls.prep = self.old

    def at(self, n, hour=0, minute=2):
        return A.ms(self.m.days[n]) + hour * 3_600_000 + minute * 60_000

    def run(self, job, n, hour=None, minute=None):
        hour = A.TRANCHES[int(job[1])] if hour is None else hour
        minute = minute if minute is not None else {"guard": A.GUARD_MINUTE, "risk": 3}.get(job, A.RUN_MINUTE)
        self.m.now = self.at(n, hour, minute)
        A.run_job(job, self.px, dt.datetime.fromtimestamp(self.m.now / 1000, dt.UTC))
        with A.db() as c:
            r = c.execute("SELECT flags, equity, blocked FROM daily WHERE date=?", (str(self.m.days[n].date()),)).fetchone()
        return dict(zip(("flags", "equity", "blocked"), r or ("", None, 0)))

    def sig(self, k, n, v):
        """몫 k 가 n 일차 작업에서 보는 신호 봉 (= 그 경계 시각 하루 전 시작) 의 확정 레짐."""
        self.conf[self.m.days[n] + pd.Timedelta(hours=A.TRANCHES[k]) - pd.Timedelta(days=1)] = v

    def pos(self, inst=BTC):
        return self.px.positions(inst)

    def book(self, k, inst=BTC):
        return A.ledger(inst, k)

    def trades(self):
        with A.db() as c:
            return [tuple(r) for r in c.execute("SELECT inst, why FROM trades")]


def test_tranche_jobs_paper():
    b = Bot()
    try:
        # 몫 0: 확정 BULL → 00:02 진입. 몫당 명목 = 실현 잔고 2%/3 × 30
        b.sig(0, 100, 1)
        b.run("t0", 100)
        p = b.pos()[1]
        assert 0 <= 10_000 * 0.02 / 3 * 30 - p["sz"] * 0.01 * 100 < 3.5 and b.book(0)["side"] == 1   # 수량 여유 10bp·lot 내림
        assert b.px.leverage_info(BTC)[1] == 30.0
        st = [x for x in b.px.stops(BTC) if x["cid"] == b.book(0)["stop_cid"]]
        assert len(st) == 1 and st[0]["sz"] == b.book(0)["sz"] and st[0]["pos_side"] == 1
        cap = rls.liq_dist(30, 0.004) - rls.LIQ_BUF                         # 30x: 1.5×ATR(3%) 대신 상한 1.88%
        assert abs(cap - 0.018833) < 1e-5 and abs(st[0]["trigger"] - b.book(0)["entry"] * (1 - cap)) < 0.011
        assert st[0]["trigger"] > p["liqPx"]
        with A.lock():                                       # 락: 중복 실행 금지
            try:
                A.run_job("t0", b.px)
                raise AssertionError("락이 잡혀 있으면 실행되면 안 된다")
            except RuntimeError:
                pass
        # 몫 1: 같은 날 08:02 확정 BEAR → 숏 (헤지 모드라 몫 0 롱과 상쇄되지 않는다), 몫마다 자기 손절
        b.sig(1, 100, -1)
        b.run("t1", 100)
        assert set(b.pos()) == {1, -1} and b.book(1)["side"] == -1
        assert {x["pos_side"] for x in b.px.stops(BTC)} == {1, -1} and len(b.px.stops(BTC)) == 2
        # 몫 2: 신호 없음 → 진입 없음
        b.run("t2", 100)
        assert b.book(2)["side"] == 0
        # 점검: 몫 1 손절 주문 누락 → 복구 + 경보, 자산 기록은 덮지 않음
        eq = b.run("guard", 100, 17)["equity"]
        b.st["stops"][BTC] = [x for x in b.st["stops"][BTC] if x["pos_side"] == 1]
        r = b.run("guard", 100, 18)
        assert "stop_reset" in r["flags"] and len(b.px.stops(BTC)) == 2 and r["equity"] == eq
        # 몫 1 손절 체결 (거래소) → 점검이 대사해서 기록, 몫 1 edge, 몫 0 은 그대로
        s1 = b.book(1)
        b.px._reduce(BTC, -1, s1["sz"], s1["stop"], b.at(100, 19))
        b.px.cancel_stops(BTC, [s1["stop_cid"]])
        b.st["algos"][s1["stop_cid"]] = {"state": "effective", "px": s1["stop"]}
        r = b.run("guard", 100, 20)
        assert "stop_hit:BTC#1" in r["flags"] and b.book(1)["blk"] == -1 and b.book(0)["side"] == 1
        assert (BTC + "#1", "sl") in b.trades() and -1 not in b.pos()
        # 몫 0 BE1: 신호 봉 종가 102.5 (+1 ATR) → 몫 0 손절 = 진입가 × 1.0015 (경보 없음)
        b.m.d.loc[b.m.days[100], ["high", "close"]] = [102.6, 102.5]
        b.sig(0, 101, 1)
        r = b.run("t0", 101)
        s0 = b.book(0)
        assert "be:BTC#0" in r["flags"] and abs(s0["stop"] - s0["entry"] * 1.0015) < 1e-9 and "stop_reset" not in r["flags"]
        # 몫 0 DROP1: 종가 − 전일 종가 = −2.5 ≤ −1 ATR → 청산, 같은 방향 지속 동안 재진입 없음
        b.m.d.loc[b.m.days[101], ["low", "close"]] = [99.9, 100.0]
        b.sig(0, 102, 1)
        b.run("t0", 102)
        assert b.book(0)["side"] == 0 and b.book(0)["blk"] == 1 and (BTC + "#0", "drop1") in b.trades()
        b.sig(0, 103, 1)
        b.run("t0", 103)
        assert b.book(0)["side"] == 0, "DROP1 뒤 원하는 방향이 꺼지기 전엔 재진입 없음"
        # 레버리지 조회 불일치 → 주문 안 함 (몫 2 진입 시도)
        b.px.set_leverage = lambda inst, lv, side: None
        b.st["lever"].setdefault(ETH, {})["1"] = 10
        b.sig(2, 103, 1)
        r = b.run("t2", 103)
        assert "order_check_fail" in r["flags"] and b.book(2, ETH)["side"] == 0
        del b.px.set_leverage
        # 계좌 일손실 → 열린 몫 전부 즉시 청산, 그날 진입 없음
        b.sig(2, 103, 1)
        b.run("t2", 103)
        assert b.book(2)["side"] == 1
        A.risk_update(lambda r: r["day"].update(eq=1e9))
        b.sig(0, 104, 1)
        r = b.run("t0", 104)
        assert "daily_loss" in r["flags"] and b.book(2)["side"] == 0 and (BTC + "#2", "daily_loss") in b.trades()
        assert not b.pos() and not b.pos(ETH)
        b.sig(1, 104, 1)
        b.run("t1", 104)
        assert b.book(1)["side"] == 0, "일손실 날(UTC)엔 다른 몫도 진입 없음"
        # 강제청산 1건 → 진입 중단
        b.st["hist"].append({"inst": BTC, "uTime": b.at(104, 9), "side": 1, "closeAvgPx": 95.5, "realizedPnl": -20.0,
                             "fee": 0.0, "fundingFee": 0.0, "type": "3"})
        r = b.run("guard", 104, 10)
        assert "liquidated" in r["flags"] and A.kv_get("acct")["hold"]
        A.resume()
        # 킬 스위치
        A.risk_update(lambda r: r.update(hwm=20_000))
        b.run("guard", 104, 11)
        assert "kill" in A.risk_get()["flatten"] and A.risk_get()["state"] == "HALTED" and any("킬 스위치" in m for m in b.alerts)
        # 스케줄: 몫 작업 HH:02 (00·08·16) + 매시 07분 점검
        assert A.next_job(pd.Timestamp("2026-10-12 07:59", tz="UTC"))[1] == "t1"
        assert A.next_job(pd.Timestamp("2026-10-12 08:03", tz="UTC"))[1] == "guard"
        assert A.next_job(pd.Timestamp("2026-10-12 23:58", tz="UTC"))[1] == "t0"
    finally:
        b.close()


def test_c_rules_paper():
    """운용 봇: 본전 손절 체결 → edge 없음, ATR 9% 신호는 진입 대기(vol_wait), 변동성이 내려오면 진입."""
    b = Bot()
    try:
        b.sig(0, 100, 1)
        b.run("t0", 100)
        b.m.d.loc[b.m.days[100], ["high", "close"]] = [102.6, 102.5]
        b.sig(0, 101, 1)
        assert "be:BTC#0" in b.run("t0", 101)["flags"]
        s0 = b.book(0)
        b.px._reduce(BTC, 1, s0["sz"], s0["stop"], b.at(101, 3))
        b.px.cancel_stops(BTC, [s0["stop_cid"]])
        b.st["algos"][s0["stop_cid"]] = {"state": "effective", "px": s0["stop"]}
        assert "stop_hit:BTC#0" in b.run("guard", 101, 4)["flags"]
        assert b.book(0)["side"] == 0 and b.book(0)["blk"] is None, "본전 뒤 손절은 edge 를 걸지 않는다"
        b.atr = 9.0
        b.sig(0, 102, 1)
        assert "vol_wait:BTC#0" in b.run("t0", 102)["flags"] and b.book(0)["side"] == 0
        b.atr = 2.0
        b.sig(0, 103, 1)
        b.run("t0", 103)
        assert b.book(0)["side"] == 1, "변동성이 내려오면 같은 방향 재진입"
    finally:
        b.close()


def test_one_tranche_env():
    """RLSC_TRANCHES=0 → 1몫: 몫 증거금 = 실현 잔고 2% 전부, 작업은 t0 + guard 만."""
    import importlib
    import subprocess
    out = subprocess.run([sys.executable, "-c", "import autotrade as A; print(A.N, A.TRANCHES, A.next_job(__import__('pandas').Timestamp('2026-10-12 07:59', tz='UTC'))[1])"],
                         env={**os.environ, "RLSC_TRANCHES": "0", "MODE": "dry"}, capture_output=True, text=True,
                         cwd=pathlib.Path(A.__file__).parent)
    assert out.stdout.split()[-3:] == ["1", "(0,)", "guard"], out.stdout + out.stderr
    assert importlib.import_module("autotrade").N == 3
    run = lambda coins: subprocess.run([sys.executable, "-c", "import autotrade as A; print(A.COINS, A.CFG['insts'])"],  # noqa: E731
                                       env={**os.environ, "RLSC_COINS": coins, "MODE": "dry"}, capture_output=True, text=True,
                                       cwd=pathlib.Path(A.__file__).parent)
    out = run("ETH-USDT-SWAP")
    assert out.stdout.split("\n")[-2] == "('ETH-USDT-SWAP',) ['ETH-USDT-SWAP']", out.stdout + out.stderr
    assert run("DOGE-USDT-SWAP").returncode != 0 and A.COINS == rls.COINS


def test_tranche_replay():
    """시각 분할 리플레이: 몫 0 = 단일 리플레이, 몫별 경계 일봉은 4H/1H 어느 쪽으로 만들어도 같다."""
    rng = np.random.default_rng(3)
    n = 24 * 400
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.006, n)))
    h1 = pd.DataFrame({"ts": A.ms("2024-01-01") + np.arange(n) * 3_600_000, "open": c, "high": c * 1.003,
                       "low": c * 0.997, "close": c, "confirm": 1})
    h4 = h1.groupby(np.arange(n) // 4).agg(ts=("ts", "first"), open=("open", "first"), high=("high", "max"),
                                            low=("low", "min"), close=("close", "last"), confirm=("confirm", "min"))
    for off in rls.TRANCHES:
        assert rls.offset_daily(h1, off, 1).equals(rls.offset_daily(h4, off, 4))
    assert rls.offset_daily(h1, 8, 1).index[0] == pd.Timestamp("2024-01-01 08:00")
    eq1, _, _ = rls.simulate({BTC: rls.offset_daily(h1, 0, 1)}, {}, lambda _: set(), set(), "2024-05-01", sleeves=("coin",))
    eq, t = rls.simulate_tranches({BTC: h1}, (0,), "2024-05-01")
    r1 = eq1.groupby(eq1.index.normalize()).last().pct_change().dropna()
    assert np.allclose(eq.pct_change().dropna().to_numpy(), r1.to_numpy()), "1몫이면 단일 리플레이와 같다"


def test_seed_edge():
    """시작 시 과거 리플레이: 손절 뒤 확정 레짐이 안 꺼졌으면 막고, 한 번이라도 꺼졌으면 풀린다."""
    old = rls.prep
    rls.prep = lambda d: d
    try:
        sl = {93: (100, 100, 96, 99)}
        assert A.seed_edge(BTC, frame(100, [0] * 90 + [1] * 10, bars=sl)) == 1
        assert A.seed_edge(BTC, frame(100, [0] * 90 + [1] * 5 + [0] + [1] * 4, bars=sl)) is None
        assert A.seed_edge(BTC, frame(100, [0] * 90 + [1] * 10)) is None, "손절·DROP1 없으면 막지 않는다"
    finally:
        rls.prep = old


# ---------- 위험 실패 재현 (지시서 9장 T01~T17): 모의 거래소(Paper) + 제어 가능한 시계 ----------
import contextlib  # noqa: E402
import copy  # noqa: E402
import json  # noqa: E402


@contextlib.contextmanager
def tranches(*offs):
    """몫 수를 바꿔 실행 (1몫 = RLSC_TRANCHES=0 과 같음)."""
    old = A.TRANCHES, A.N
    A.TRANCHES, A.N = tuple(offs), len(offs)
    try:
        yield
    finally:
        A.TRANCHES, A.N = old


def bot_open(b, n=100, ks=(0,), side=1):
    """몫 ks 를 n 일차 작업에서 진입시킨다 (BTC·ETH 둘 다)."""
    for k in ks:
        b.sig(k, n, side)
        b.run(f"t{k}", n)
    return b


def flat(b):
    return not b.pos(BTC) and not b.pos(ETH) and not A.open_books()


def test_t01_risk_flatten_without_next_job():
    """T01: 몫 작업 직후 킬·일손실 → 다음 몫 작업(1몫이면 24시간 뒤) 없이 위험 감시가 즉시 전량 청산·잔여 0 확인."""
    for offs in ((0,), (0, 8, 16)):
        with tranches(*offs):
            b = Bot()
            try:
                bot_open(b, 100, ks=range(len(offs)))
                assert A.open_books()
                A.risk_update(lambda r: r.update(hwm=1e6))             # 킬: 고점 대비 −35% 이하
                b.run("risk", 100, 16, 3)
                r = A.risk_get()
                assert flat(b) and r["state"] == "HALTED" and "kill" in r["flatten"], (offs, r)
                assert r["t"]["detected"] and r["t"]["flatten_req"] and r["t"]["flat_ok"]
                assert {w for _, w in b.trades()} == {"halt"}
                assert not b.px.stops(BTC), "닫힌 몫의 봇 손절은 정리"
            finally:
                b.close()
    with tranches(0):                                                  # 일손실: 날 경계 직후 첫 감시(00:00:05)에서 판정·청산
        b = Bot()
        try:
            bot_open(b, 100)
            A.risk_update(lambda r: r["day"].update(eq=1e9))
            b.run("risk", 101, 0, 5 / 60)
            r = A.risk_get()
            assert flat(b) and r["flatten"]["daily_loss"] == str(b.m.days[101].date()) and r["state"] == "HALTED"
            assert r["day"]["date"] == str(b.m.days[101].date()) and r["day"]["lag_s"] == 5
        finally:
            b.close()


def test_t02_risk_during_entry():
    """T02: 몫 작업이 BTC 진입 뒤 ETH 주문 직전에 다른 프로세스가 킬을 기록 → ETH 주문 없음, 이미 접수된 BTC 는 다음 감시가 정리."""
    b = Bot()
    try:
        real = b.px.set_leverage

        def lev(inst, lv, side):
            real(inst, lv, side)
            if inst == ETH:
                A.risk_update(lambda r: r["flatten"].update(kill="외부 감시가 기록"))
        b.px.set_leverage = lev
        b.sig(0, 100, 1)
        r = b.run("t0", 100)
        assert b.book(0)["side"] == 1 and b.book(0, ETH)["side"] == 0 and "entry_hold" in r["flags"]
        assert not b.pos(ETH)
        del b.px.set_leverage
        b.run("risk", 100, 0, 3)
        assert flat(b) and A.risk_get()["state"] == "HALTED"
    finally:
        b.close()


def test_t03_partial_fill_and_timeout():
    """T03: 청산 일부 체결 → 남은 수량만 / 응답 유실(체결은 됨) → clOrdId 로 확정, 같은 수량을 다시 보내지 않음."""
    b = Bot()
    try:
        bot_open(b, 100)
        sz = b.book(0)["sz"]
        real, sent = b.px.order, []

        def half(inst, side, q, cid, close=False):                     # 첫 청산은 절반만 체결되고 나머지 취소
            sent.append((inst, q, close))
            if close and len([x for x in sent if x[2]]) == 1:
                o = real(inst, side, round(q / 2, 2), cid, close)
                o = {**o, "state": "canceled"}
                b.st["orders"][cid] = o
                return o
            return real(inst, side, q, cid, close)
        b.px.order = half
        A.risk_update(lambda r: r.update(hwm=1e6))
        b.run("risk", 100, 1, 0)
        closes = [x for x in sent if x[2] and x[0] == BTC]
        assert [q for _, q, _ in closes] == [sz, round(sz - round(sz / 2, 2), 2)], closes
        assert flat(b) and A.risk_get()["state"] == "HALTED"
        assert [w for i, w in b.trades() if i == BTC + "#0"] == ["halt", "halt"], "부분 청산 = 거래 2건"
    finally:
        b.close()
    b = Bot()
    try:
        bot_open(b, 100)
        real, sent = b.px.order, []

        def lost(inst, side, q, cid, close=False):                    # 거래소는 체결했지만 응답 확인 실패
            sent.append(cid)
            real(inst, side, q, cid, close)
            raise RuntimeError(f"{cid} 체결 확인 실패")
        b.px.order = lost
        A.risk_update(lambda r: r.update(hwm=1e6))
        try:
            b.run("risk", 100, 1, 0)
        except Exception:  # noqa: BLE001
            pass
        r = A.risk_get()
        assert r["state"] == "DEGRADED" and "flat_ok" not in r["t"], "확정 전엔 flat 선언 없음"
        assert not A.kv_get("pending") and len(sent) == 2, "같은 회차 점검이 clOrdId 로 확정 (재전송 없음)"
        del b.px.order
        b.run("risk", 100, 1, 1)
        assert flat(b) and A.risk_get()["state"] == "HALTED" and not A.kv_get("pending")
        assert len(sent) == 2 and [w for _, w in b.trades()].count("halt") == 2, "BTC·ETH 1번씩, 재전송 없음"
    finally:
        b.close()


def test_t04_stop_and_emergency_close_together():
    """T04: 비상 청산 주문 직전에 한 몫 손절이 체결 → 반대 포지션·과청산 없음, 잔여 수량 재조회 후 flat."""
    b = Bot()
    try:
        bot_open(b, 100, ks=(0, 1))
        real = b.px.order
        hit = []

        def race(inst, side, q, cid, close=False):
            if close and inst == BTC and not hit:
                s0 = b.book(0)
                b.px._reduce(BTC, 1, s0["sz"], s0["stop"], b.m.now)
                b.st["algos"][s0["stop_cid"]] = {"state": "effective", "px": s0["stop"]}
                hit.append(1)
            return real(inst, side, q, cid, close)
        b.px.order = race
        A.risk_update(lambda r: r.update(hwm=1e6))
        b.run("risk", 100, 9, 0)
        assert hit and flat(b) and -1 not in b.pos(BTC) and A.risk_get()["state"] == "HALTED"
        assert all(h["side"] == 1 for h in b.st["hist"]), "숏을 만든 체결 없음"
    finally:
        b.close()


def test_t05_restart_during_flatten():
    """T05: 청산 주문 전송 직전에 프로세스가 죽음 → 재시작 뒤 사유·상태 복구, 미접수 주문 해제 후 청산, 재진입 없음."""
    b = Bot()
    try:
        bot_open(b, 100)
        b.px.order = lambda *a, **k: (_ for _ in ()).throw(SystemExit("죽음"))
        A.risk_update(lambda r: r.update(hwm=1e6))
        try:
            b.run("risk", 100, 2, 0)
        except SystemExit:
            pass
        assert A.kv_get("pending") and A.risk_get()["state"] == "FLATTENING" and "kill" in A.risk_get()["flatten"]
        b.px = okx.Paper(b.m, copy.deepcopy(A.kv_get("paper")), 10_000, now=lambda: b.m.now)   # 재시작: 저장된 장부에서
        b.st = b.px.st
        b.run("risk", 100, 2, 1)
        assert flat(b) and A.risk_get()["state"] == "HALTED" and not A.kv_get("pending")
        b.sig(0, 101, 1)
        b.run("t0", 101)
        assert b.book(0)["side"] == 0 and not b.pos(), "킬 사유가 남아 재진입 없음"
    finally:
        b.close()


def test_t06_t07_day_boundary_and_reasons():
    """T06: 이전 기간 판정을 끝낸 뒤 새 기준 (00:02 기록을 분모로 써서 손실 0 이 되는 문제 없음), 기준은 날마다 1번.
    T07: 날짜가 바뀌면 일손실 사유만 풀리고 킬·다른 차단 사유는 남는다."""
    t = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731
    r = A._risk_norm({"day": {"date": "2026-01-01", "eq": 100.0}, "hwm": 100.0})
    assert A.risk_eval(r, 94.0, t("2026-01-01 23:59:59")) == [], "경계 전에는 일손실 판정 없음 (리플레이와 같은 규칙)"
    new = A.risk_eval(r, 94.0, t("2026-01-02 00:00:03"))
    assert [w for w, _ in new] == ["daily_loss"] and r["flatten"]["daily_loss"] == "2026-01-02"
    assert r["day"] == {"date": "2026-01-02", "eq": 94.0, "ts": "2026-01-02T00:00:03+00:00", "lag_s": 3}
    assert A.risk_eval(r, 90.0, t("2026-01-02 05:00")) == [] and r["day"]["eq"] == 94.0, "같은 날 기준 재생성 없음"
    r["flatten"]["kill"] = "x"
    r["block"]["mismatch:BTC"] = "y"
    A.risk_eval(r, 90.0, t("2026-01-03 00:00:01"))
    assert "daily_loss" not in r["flatten"] and r["flatten"]["kill"] == "x" and r["block"] == {"mismatch:BTC": "y"}
    r = A._risk_norm({"day": {"date": "2026-01-01", "eq": 100.0}, "hwm": 100.0})
    A.risk_eval(r, 100.0, t("2026-01-02 03:00"))
    assert "day_ref_late" in r["block"], "경계 누락(3시간 늦은 기준) → 그날 진입 보류"
    A.risk_eval(r, 100.0, t("2026-01-03 00:00:02"))
    assert "day_ref_late" not in r["block"]


def test_t08_cashflow_and_equity_scope():
    """T08: 출금은 손실이 아니고 입금은 수익이 아니다 (기준·고점 같이 이동), 자산은 USDT eq 한 번만 (이중 집계 없음)."""
    b = Bot()
    try:
        bot_open(b, 100)
        b.run("risk", 100, 0, 3)
        eq0 = A.risk_get()["day"]["eq"]
        b.st["cash"] -= 4_000                                          # 40% 출금
        b.st["transfers"] = [[b.at(100, 1), "b1", -4_000.0]]
        b.run("risk", 100, 1, 5)
        r = A.risk_get()
        assert "kill" not in r["flatten"] and r["state"] == "RUNNING" and abs(r["day"]["eq"] - (eq0 - 4_000)) < 1e-6
        b.st["cash"] += 9_000
        b.st["transfers"].append([b.at(100, 2), "b2", 9_000.0])
        b.run("risk", 101, 0, 3)
        r = A.risk_get()
        assert "daily_loss" not in r["flatten"] and abs(r["hwm"] - (eq0 + 5_000)) < 50, r["hwm"]
    finally:
        b.close()

    class Bal:
        def request(self, method, url, **k):
            return Resp([{"totalEq": "999", "details": [{"ccy": "USDT", "availBal": "60", "eq": "100", "upl": "5"}]}])
    assert okx.Client(session=Bal()).balance() == (100.0, 60.0, 95.0), "총자산(USD 전 통화) 대신 USDT eq, 실현 = eq − upl"


def test_t09_same_side_tranches_recheck_all_stops():
    """T09: 같은 방향 두 번째 몫 진입으로 평단·청산가가 바뀌면 남은 몫 손절 전부를 새 청산가 안쪽으로 다시 검사."""
    b = Bot()
    try:
        bot_open(b, 100, ks=(0,))
        b.m.d.loc[b.m.days[100], ["high", "close"]] = [104.6, 104.5]   # 08:02 진입가 ≈ 104.5 → 평단 상승 → 청산가 상승
        b.sig(1, 100, 1)
        r = b.run("t1", 100)
        liq = b.pos()[1]["liqPx"]
        st = [x for x in b.px.stops(BTC) if x["pos_side"] == 1]
        assert len(st) == 2 and all(x["trigger"] > liq for x in st), (liq, st)
        assert "stop_inside_reset" in r["flags"]
    finally:
        b.close()


def test_t10_stop_replace_cancel_fails():
    """T10: 손절 교체 중 옛 손절 취소 실패 → 임시 중복을 기록·진입 차단, 다음 점검에서 옛 손절 취소. 급변 때도 반대 포지션 없음."""
    b = Bot()
    try:
        bot_open(b, 100, ks=(0,))
        old = b.book(0)["stop_cid"]
        b.px.cancel_stops = lambda inst, ids: (_ for _ in ()).throw(okx.OKXError("50001", "timeout"))
        b.m.d.loc[b.m.days[100], ["high", "close"]] = [102.6, 102.5]   # BE1 → 손절 교체
        b.sig(0, 101, 1)
        b.run("t0", 101)
        s0 = b.book(0)
        assert s0["stop_old"] == old and s0["stop_cid"] != old and len([x for x in b.px.stops(BTC) if x["pos_side"] == 1]) == 2
        assert any(k.startswith(f"unprotected:{BTC}#0:dup") for k in A.risk_get()["block"]) and A.entry_blocks(A.risk_get(), pd.Timestamp.now(tz="UTC"))
        del b.px.cancel_stops
        b.run("risk", 101, 1, 0)
        assert [x["cid"] for x in b.px.stops(BTC) if x["pos_side"] == 1] == [s0["stop_cid"]] and not b.book(0).get("stop_old")
        assert not [k for k in A.risk_get()["block"] if k.startswith("unprotected")]
    finally:
        b.close()


def test_t11_stop_fail_then_close_fail():
    """T11: 손절 등록 실패 + 청산도 실패 → 보호·flat 으로 표시하지 않음, 진입 차단, 위험 감시에서 재시도해 복구."""
    b = Bot()
    try:
        real = b.px.order
        b.px.place_stop = lambda *a, **k: (_ for _ in ()).throw(okx.OKXError("51000", "rejected"))
        b.px.order = lambda inst, side, q, cid, close=False: (
            (_ for _ in ()).throw(okx.OKXError("50013", "busy")) if close else real(inst, side, q, cid, close))
        b.sig(0, 100, 1)
        r = b.run("t0", 100)
        rk = A.risk_get()
        assert "stop_fail" in r["flags"] and b.book(0)["side"] == 1 and b.pos()[1]
        assert f"unprotected:{BTC}#0" in rk["block"] and rk["state"] != "HALTED" and A.entry_blocks(rk, pd.Timestamp.now(tz="UTC"))
        del b.px.place_stop, b.px.order
        b.run("risk", 100, 0, 4)
        assert not [k for k in A.risk_get()["block"] if k.startswith("unprotected")] and len(b.px.stops(BTC)) == 1
    finally:
        b.close()


def test_t12_foreign_position_and_order():
    """T12: 봇 외 포지션·조건부 주문 → 취소·청산하지 않고 범위 충돌 보고·진입 차단. 비상 청산도 봇 수량만."""
    b = Bot()
    try:
        bot_open(b, 100, ks=(0,))
        bot_sz = b.book(0)["sz"]
        b.st["pos"][BTC]["1"]["sz"] += 0.5                              # 수동 롱 0.5 계약
        b.st["stops"][BTC].append({"algoId": "m1", "cid": "", "sz": 0.5, "pos_side": 1, "trigger": 90.0})
        b.run("guard", 100, 1)
        rk = A.risk_get()
        assert f"foreign:{BTC}" in rk["block"] and f"mismatch:{BTC}:long" in rk["block"]
        assert any(x["algoId"] == "m1" for x in b.px.stops(BTC)), "봇 소유가 아닌 주문은 취소하지 않음"
        A.risk_update(lambda r: r.update(hwm=1e6))
        b.run("risk", 100, 2, 0)
        assert abs(b.pos()[1]["sz"] - 0.5) < 1e-9 and not A.open_books() and A.risk_get()["state"] == "HALTED"
        assert bot_sz > 0
    finally:
        b.close()


def test_t13_stale_api_and_lock():
    """T13: 조회 실패 → DEGRADED·진입 차단·보호 주문 유지 (노출 0 이라 쓰지 않음), 복구되면 해제. 락 경합이면 그 회차 건너뜀."""
    b = Bot()
    try:
        bot_open(b, 100, ks=(0,))
        stops = b.px.stops(BTC)
        b.px.transfers = lambda since: (_ for _ in ()).throw(requests.ConnectionError("down"))
        b.sig(1, 100, 1)
        try:
            b.run("t1", 100)
            raise AssertionError("조회 실패면 작업 중단")
        except requests.ConnectionError:
            pass
        rk = A.risk_get()
        assert rk["state"] == "DEGRADED" and "stale" in rk["block"] and b.book(1)["side"] == 0 and b.px.stops(BTC) == stops
        assert A.open_books(), "기존 노출은 그대로 표시"
        del b.px.transfers
        b.run("risk", 100, 8, 5)
        assert A.risk_get()["state"] == "RUNNING" and "stale" not in A.risk_get()["block"]
        with A.lock():
            try:
                b.run("risk", 100, 8, 6)
                raise AssertionError("락 경합")
            except RuntimeError as e:
                assert "락" in str(e)
    finally:
        b.close()


def test_t14_boundary_bars_no_future():
    """T14: 경계 일봉은 확정 4H 6개만, 미래 봉·미확정 봉이 과거 경계 일봉을 바꾸지 않는다."""
    rng = np.random.default_rng(5)
    n = 6 * 60
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    h4 = pd.DataFrame({"ts": A.ms("2025-01-01") + np.arange(n) * 4 * 3_600_000, "open": c, "high": c * 1.01,
                       "low": c * 0.99, "close": c, "confirm": 1})
    for off in rls.TRANCHES:
        d = rls.offset_daily(h4, off)
        cut = d.index[30]
        h2 = h4.copy()
        later = h2.ts >= A.ms(cut + pd.Timedelta(days=1))
        h2.loc[later, ["open", "high", "low", "close"]] *= 3
        assert rls.offset_daily(h2, off).loc[:cut].equals(d.loc[:cut]), "미래 4H 봉이 과거 경계 일봉에 섞이지 않음"
        h3 = h4.copy()
        h3.loc[h3.index[-1], "confirm"] = 0
        assert len(rls.offset_daily(h3, off)) == len(d) - (off == 0), "미확정 봉이 든 날은 버린다"


def test_t15_live_decisions_match_replay():
    """T15: 같은 일봉으로 운용 작업(t0, Paper)과 리플레이(rls.simulate)의 진입·청산 날짜·방향·사유가 같다
    (체결가 모델 차이 제외: Paper 슬리피지 0, 시가 = 전일 종가)."""
    rng = np.random.default_rng(11)
    n = 330
    with tranches(0):
        b = Bot()
        rls.prep = b.old                                               # 실제 규칙
        try:
            b.m = Market(n)
            b.px = okx.Paper(b.m, b.st, 10_000, now=lambda: b.m.now)
            okx.Paper.SLIP = 0.0
            c = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.02, n)))
            o = np.r_[100, c[:-1]]
            b.m.d = pd.DataFrame({"open": o, "high": np.maximum(o, c) * (1 + rng.uniform(0, 0.01, n)),
                                  "low": np.minimum(o, c) * (1 - rng.uniform(0, 0.01, n)), "close": c}, index=b.m.days)
            hourly = b.m.candles

            def candles(inst, since, bar="1H"):                        # Paper 손절 판정용 1H = 그날 OHLC
                if bar != "1H":
                    return hourly(inst, since, bar)
                x = hourly(inst, since - since % 86_400_000, "4H")
                x = x[(x.ts % 86_400_000 == 0) & (x.ts >= since)]       # 일봉 1개를 그날 00:00 '1H 봉'으로
                return x[x.ts + 86_400_000 <= b.m.now]
            b.m.candles = candles
            start = 120
            for i in rls.COINS:
                A.kv_set(A.tkey(i, 0), {"side": 0, "sz": 0.0, "stop_n": 0, "blk": None, "seeded": True})
            for k in range(start, n):
                b.run("t0", k)
            with A.db() as cn:
                live = [(r[0].split("#")[0], pd.Timestamp(r[1]).normalize(), pd.Timestamp(r[2]).normalize(), r[3], r[4])
                        for r in cn.execute("SELECT inst, entry_ts, exit_ts, side, why FROM trades WHERE inst LIKE 'BTC%'")]
            _, t, _ = rls.simulate({BTC: b.m.d, ETH: b.m.d}, {}, lambda _: set(), set(), b.m.days[start], b.m.days[n - 1],
                                   sleeves=("coin",), funding_8h=0, slip=0)
            t = t[(t.inst == BTC) & (t.exit_ts < b.m.days[n - 1])]
            # 운용은 손절 체결을 다음 대사 때(이 테스트는 다음 날 t0) 기록 → 손절 청산 시각 = 리플레이 손절일 + 1일
            rep = [(BTC, r.entry_ts, r.exit_ts + pd.Timedelta(days=int(r.why == "sl")), r.side, r.why) for r in t.itertuples()]
            live = [x for x in live if x[2] < b.m.days[n - 1]]
            assert len(rep) >= 5 and live == rep, (live, rep)
        finally:
            okx.Paper.SLIP = 0.0002
            b.close()


def test_t16_config_hash_no_secrets():
    """T16: 1몫·3몫 설정 해시가 다르고 차이가 필드로 보이며, 유효 설정에 인증 정보가 없다. 대시보드는 해시가 같은 백테스트만 보인다."""
    c1, c3 = rls.strategy_config((0,), 30), rls.strategy_config((0, 8, 16), 30)
    assert rls.config_hash(c1) != rls.config_hash(c3)
    assert rls.config_diff(c1, c3) == ["margin_per_tranche: 0.02 → 0.006666666666666667", "tranches_utc: [0] → [0, 8, 16]"]
    os.environ.update(OKX_API_KEY="SECRET-KEY-123", OKX_API_SECRET="SECRET-S", OKX_API_PASSPHRASE="SECRET-P")
    try:
        txt = json.dumps(A.effective_config())
        assert "SECRET" not in txt and A.effective_config()["strategy_hash"] == rls.config_hash(A.CFG)
    finally:
        for k in ("OKX_API_KEY", "OKX_API_SECRET", "OKX_API_PASSPHRASE"):
            os.environ.pop(k)
    import dashboard
    tmp = pathlib.Path(tempfile.mkdtemp())
    for name, cfg, cagr in (("c3-l30", c3, 27.3), ("c1-l30", c1, 15.0)):
        (tmp / name).mkdir()
        (tmp / name / "manifest.json").write_text(json.dumps({"run": name, "strategy_hash": rls.config_hash(cfg),
                                                              "stats": {"cagr%": cagr}}))
    assert dashboard.backtest_for(rls.config_hash(c1), tmp)["run"] == "c1-l30"
    assert dashboard.backtest_for("nope", tmp) is None, "같은 구성 결과가 없으면 다른 구성 수치를 보이지 않음"


def test_t17_tick_step_rounding():
    """T17: 수량은 lot 내림 (증거금 ≤ 2%/N), 손절가는 tick 을 진입가 쪽으로 반올림 (청산가 쪽으로 밀리지 않음)."""
    b = Bot()
    try:
        b.m.INS = {**Market.INS, "tickSz": 0.1, "lotSz": 0.1, "minSz": 0.1}
        bot_open(b, 100, ks=(0,))
        bot_open(b, 100, ks=(1,), side=-1)
        for k, side in ((0, 1), (1, -1)):
            s = b.book(k)
            st = [x for x in b.px.stops(BTC) if x["cid"] == s["stop_cid"]][0]
            assert side * (st["trigger"] - s["stop"]) >= -1e-9 and abs(st["trigger"] - s["stop"]) < 0.1 + 1e-9, (st, s["stop"])
            assert abs(round(st["trigger"] / 0.1) * 0.1 - st["trigger"]) < 1e-9
            assert s["margin"] <= 10_000 * rls.MARGIN / A.N + 1e-6 and abs(s["sz"] * 10 - round(s["sz"] * 10)) < 1e-9
    finally:
        b.close()


def test_state_migration_from_acct():
    """배포 전 상태(acct 의 peak·rec_eq·rec_day·halted·dl_day)를 새 위험 상태로 옮긴다 (HWM·일 기준·중단 사유 보존)."""
    tmp = pathlib.Path(tempfile.mkdtemp())
    A.DB_PATH = tmp / "m.db"
    A.kv_set("acct", {"peak": 123.0, "rec_eq": 110.0, "rec_day": "2026-10-11", "halted": True, "dl_day": "2026-10-11",
                      "hold": "BTC 강제청산", "streak": 3})
    r = A.risk_get()
    assert r["hwm"] == 123.0 and r["day"]["eq"] == 110.0 and r["day"]["date"] == "2026-10-11"
    assert "kill" in r["flatten"] and r["flatten"]["daily_loss"] == "2026-10-11"
    assert "BTC 강제청산" in A.entry_blocks(r, pd.Timestamp("2026-10-11 05:00", tz="UTC"), A.kv_get("acct"))
    assert "day_ref_late" not in r["block"], "옮긴 기준(00:02 기록)은 지연으로 보지 않는다"


def test_margin_env():
    """RLSC_MARGIN: 종목당 증거금 비율 → 몫당 = 비율/몫 수, 설정 해시에 반영. 두 종목 합이 동시 한도 10% 를 넘는 값은 시작 거부."""
    import subprocess
    run = lambda m: subprocess.run([sys.executable, "-c", "import autotrade as A; print(A.MARGIN, A.CFG['margin_per_tranche'], A.CFG['margin_per_symbol'])"],  # noqa: E731
                                   env={**os.environ, "RLSC_MARGIN": m, "MODE": "dry"}, capture_output=True, text=True, cwd=pathlib.Path(A.__file__).parent)
    out = run("0.03")
    assert out.stdout.split()[-3:] == ["0.03", "0.01", "0.03"], out.stdout + out.stderr
    assert run("0.06").returncode != 0 and "RLSC_MARGIN" in run("0.06").stderr
    assert rls.config_hash(rls.strategy_config(margin=0.03)) != rls.config_hash(rls.strategy_config())


def test_tranche_replay_account_kill():
    """시각 분할 리플레이의 킬은 계좌(몫 평균) 기준: 한 몫만 −35% 여도 계좌가 −35% 가 아니면 계속, 계좌가 넘으면 그날 이후 고정."""
    n = 24 * 300
    rng = np.random.default_rng(7)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.008, n)))
    h1 = pd.DataFrame({"ts": A.ms("2024-01-01") + np.arange(n) * 3_600_000, "open": c, "high": c * 1.004,
                       "low": c * 0.996, "close": c, "confirm": 1})
    eq, t = rls.simulate_tranches({BTC: h1}, (0, 8, 16), "2024-04-01", kill=0.02)
    hit = eq.index[1 - eq / eq.cummax() >= 0.02]
    assert len(hit) and (eq[eq.index > hit[0]] == eq.loc[hit[0]]).all() and (t.entry_ts < hit[0]).all()
    eq2, _ = rls.simulate_tranches({BTC: h1}, (0, 8, 16), "2024-04-01", kill=1.0)
    assert (eq2[eq2.index > hit[0]] != eq2.loc[hit[0]]).any(), "킬이 없으면 계속 움직인다"


def test_export_legacy_for_rollback():
    """롤백: 새 상태 → 옛 acct 필드 (킬·고점·일 기준), 미확정 주문이 있으면 거부."""
    A.DB_PATH = pathlib.Path(tempfile.mkdtemp()) / "x.db"
    A.risk_update(lambda r: r.update(hwm=150.0, day={"date": "2026-10-12", "eq": 140.0}, flatten={"kill": "x"}))
    a = A.export_legacy()
    assert (a["peak"], a["rec_eq"], a["rec_day"], a["halted"], a["dl_day"]) == (150.0, 140.0, "2026-10-12", True, None)
    A.kv_set("pending", {"c1": {"inst": BTC}})
    try:
        A.export_legacy()
        raise AssertionError("미확정 주문이 있으면 거부")
    except SystemExit:
        pass


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
