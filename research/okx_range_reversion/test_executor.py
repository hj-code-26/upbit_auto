"""executor 검증 — 가짜 OKX (네트워크 없음). 사용: python research/okx_range_reversion/test_executor.py
주문 장애 · 타임아웃 · 취소 경합 · 부분 체결 · 부분 익절 뒤 보호 수량 · 체결 중복 · 재시작 복구 · 설정 불일치 · 일손실 · 펀딩 · 비밀값."""
import os
import pathlib
import sys
import tempfile

import ccxt

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import executor as E  # noqa: E402

H8 = 8 * 3600 * 1000


class Fake:
    """OKX v5 흉내: Net 모드 부호 포지션(계약), 시장가는 즉시(또는 partial 비율만) 체결, 지정가는 fill() 로만 체결,
    reduceOnly 가 포지션을 늘리면 거부, 조건부 손절은 trigger() 로 발동. timeout[메서드] = 'before'(도착 전 끊김) | 'after'(처리 후 끊김)."""
    fake = True

    def __init__(self):
        self.pos, self.px, self.orders, self.algos, self.fills, self.calls = 0.0, 80_000.0, {}, {}, [], []
        self.cfg = {"acctLv": "2", "posMode": "net_mode", "perm": "read_only,trade"}
        self.lever, self.partial, self.timeout = "3", 1.0, {}
        self.amend_fail = False
        self.foreign_orders, self.foreign_algos, self.bills = [], [], []
        self.liq = 55_000.0
        self.n = 0

    def __getattr__(self, k):
        if k.startswith("set_"):
            raise AssertionError(f"설정 변경 호출 금지: {k}")
        raise AttributeError(k)

    def _net(self, name, when):
        if self.timeout.get(name) == when:
            del self.timeout[name]
            raise ccxt.RequestTimeout("timeout")

    def _fill(self, cid, sz, px):
        o = self.orders[cid]
        sgn = 1 if o["side"] == "buy" else -1
        if o["reduceOnly"]:
            sz = min(sz, abs(self.pos))
        self.pos = round(self.pos + sgn * sz, 8)
        o["accFillSz"] = round(o["accFillSz"] + sz, 8)
        o["avgPx"] = px
        o["state"] = "filled" if o["accFillSz"] >= o["sz"] - 1e-9 else "partially_filled"
        self.n += 1
        self.fills.append({"tradeId": f"T{self.n}", "clOrdId": cid, "ordId": o["ordId"], "fillSz": str(sz), "fillPx": str(px), "ts": str(self.n)})

    def fill(self, cid, sz=None):
        o = self.orders[cid]
        self._fill(cid, sz if sz is not None else o["sz"] - o["accFillSz"], o.get("px") or self.px)

    def trigger(self, px):
        self.px = px
        for aid, a in list(self.algos.items()):
            if (a["side"] == "sell" and px <= float(a["slTriggerPx"])) or (a["side"] == "buy" and px >= float(a["slTriggerPx"])):
                del self.algos[aid]
                cid = f"algo{aid}"
                self.orders[cid] = {"side": a["side"], "sz": float(a["sz"]), "reduceOnly": True, "accFillSz": 0.0, "state": "live", "ordId": cid, "ordType": "market"}
                self._fill(cid, float(a["sz"]), px)

    # ── 공개 ──
    def public_get_public_instruments(self, p):
        return {"data": [{"ctVal": "0.01", "ctMult": "1", "ctType": "linear", "ctValCcy": "BTC", "settleCcy": "USDT", "lotSz": "0.01",
                          "minSz": "0.01", "tickSz": "0.1", "maxMktSz": "35000", "state": "live"}]}

    def public_get_public_funding_rate(self, p):
        return {"data": [{"fundingRate": "0.0001", "fundingTime": str(10 * H8), "nextFundingTime": str(11 * H8)}]}

    # ── 계정 ──
    def private_get_account_config(self, p):
        return {"data": [self.cfg]}

    def private_get_account_leverage_info(self, p):
        return {"data": [{"lever": self.lever, "mgnMode": "isolated", "instId": E.INST}]}

    def private_get_account_positions(self, p):
        if not self.pos:
            return {"data": []}
        return {"data": [{"pos": str(self.pos), "avgPx": "80000", "markPx": str(self.px), "liqPx": str(self.liq), "mgnRatio": "30",
                          "mmr": "3", "lever": self.lever, "mgnMode": "isolated"}]}

    def private_get_account_bills(self, p):
        return {"data": self.bills}

    # ── 주문 ──
    def private_post_trade_order(self, b):
        self.calls.append(("order", dict(b)))
        self._net("order", "before")
        red = b.get("reduceOnly") == "true"
        sgn = 1 if b["side"] == "buy" else -1
        if red and (self.pos == 0 or sgn * self.pos > 0):
            raise ccxt.InvalidOrder("51169 reduce-only would increase position")
        self.orders[b["clOrdId"]] = {"side": b["side"], "sz": float(b["sz"]), "reduceOnly": red, "accFillSz": 0.0, "state": "live",
                                     "ordId": f"O{len(self.orders)}", "ordType": b["ordType"], "px": float(b["px"]) if "px" in b else None}
        if b["ordType"] == "market":
            self._fill(b["clOrdId"], round(float(b["sz"]) * self.partial, 8), self.px)
        self._net("order", "after")
        return {"data": [{"ordId": self.orders[b["clOrdId"]]["ordId"], "clOrdId": b["clOrdId"], "sCode": "0"}]}

    def private_get_trade_order(self, p):
        o = self.orders.get(p["clOrdId"])
        if not o:
            raise ccxt.OrderNotFound("51603 Order does not exist")
        return {"data": [{"state": o["state"], "accFillSz": str(o["accFillSz"]), "avgPx": str(o.get("avgPx") or ""), "ordId": o["ordId"]}]}

    def private_post_trade_cancel_order(self, p):
        self.calls.append(("cancel", dict(p)))
        o = self.orders[p["clOrdId"]]
        if getattr(self, "fill_before_cancel", False):                  # 취소 요청이 도착하기 직전에 체결
            self.fill(p["clOrdId"]); self.fill_before_cancel = False
        if o["state"] in ("filled", "canceled"):
            raise ccxt.ExchangeError("51400 Cancellation failed as the order has been filled, canceled or does not exist")
        o["state"] = "canceled"
        return {"data": [{"sCode": "0"}]}

    def private_get_trade_orders_pending(self, p):
        return {"data": [{"clOrdId": c, **o} for c, o in self.orders.items() if o["state"] in ("live", "partially_filled")] + self.foreign_orders}

    def private_get_trade_fills(self, p):
        return {"data": self.fills[-100:]}

    # ── 알고 ──
    def private_post_trade_order_algo(self, b):
        self.calls.append(("algo", dict(b)))
        assert b["reduceOnly"] == "true" and b["ordType"] == "conditional"
        aid = f"A{len(self.calls)}"
        self.algos[aid] = {**b, "algoId": aid}
        return {"data": [{"algoId": aid, "sCode": "0"}]}

    def private_post_trade_amend_algos(self, b):
        self.calls.append(("amend", dict(b)))
        if self.amend_fail:
            raise ccxt.ExchangeError("51000 amend failed")
        a = self.algos[b["algoId"]]
        a["sz"], a["slTriggerPx"] = b["newSz"], b["newSlTriggerPx"]
        return {"data": [{"sCode": "0"}]}

    def private_post_trade_cancel_algos(self, lst):
        for x in lst:
            self.algos.pop(x["algoId"], None)
        return {"data": [{"sCode": "0"}]}

    def private_get_trade_orders_algo_pending(self, p):
        return {"data": [a for a in self.algos.values() if a["ordType"] == p.get("ordType", "conditional")] +
                        [a for a in self.foreign_algos if a.get("ordType") == p.get("ordType")]}


def new(fx=None, path=None):
    fx = fx or Fake()
    path = path or pathlib.Path(tempfile.mkdtemp()) / "state.json"
    return E.Exec(fx, E.spec(fx), path), fx, path


def algo(fx):
    assert len(fx.algos) == 1, fx.algos
    a = next(iter(fx.algos.values()))
    return float(a["sz"]), float(a["slTriggerPx"])


def t_live_gate():
    try:
        E.Exec(ccxt.okx(), E.spec(Fake()), pathlib.Path(tempfile.mkdtemp()) / "s.json")
    except E.Halt:
        return
    raise AssertionError("진짜 거래소 객체가 통과했다")


def t_spec_units():
    s = E.spec(Fake())
    assert E.cts(0.0123, s) == 1.23 and abs(E.btc(1.23, s) - 0.0123) < 1e-12        # 계약 ≠ BTC
    assert E.cts(0.00009, s) == 0.0                                                  # 0.009계약 → lot 0.01 내림
    fx = Fake()
    fx.public_get_public_instruments = lambda p: {"data": [{**Fake().public_get_public_instruments(p)["data"][0], "ctType": "inverse"}]}
    try:
        E.spec(fx)
    except E.Halt:
        return
    raise AssertionError("inverse 계약이 통과했다")


def t_preflight():
    fx = Fake()
    s = E.spec(fx)
    assert E.preflight(fx, s, 3) == []
    fx.cfg["acctLv"] = "1"; fx.lever = "5"; fx.cfg["perm"] = "read_only,trade,withdraw"
    fx.pos = 0.02; fx.foreign_algos = [{"ordType": "oco", "algoClOrdId": "p123"}]
    bad = E.preflight(fx, s, 3)
    assert len(bad) == 5 and any("acctLv=1" in b for b in bad) and any("자동 변경하지 않음" in b for b in bad)
    # set_* 를 부르면 Fake 가 AssertionError — 여기까지 왔으면 안 불렀다


def t_timeout_after_no_resend():
    ex, fx, _ = new()
    fx.timeout["order"] = "after"                                     # 거래소는 받아 체결했는데 응답이 끊김
    cid = ex.enter(1, 1.0, 79_000.0, 80_500.0)
    assert ex.st["orders"][cid]["status"] == "unknown" and fx.pos == 1.0
    for f in (lambda: ex.enter(1, 1.0, 79_000.0, 80_500.0), lambda: ex.close("x")):
        try:
            f(); raise AssertionError("미확인 중 새 주문이 나갔다")
        except E.Halt:
            pass
    assert sum(1 for c in fx.calls if c[0] == "order") == 1           # 재전송 없음
    assert ex.resolve(cid) == "filled"
    ex.poll_fills()
    assert ex.sync() and algo(fx) == (1.0, 79_000.0)


def t_timeout_before_then_absent():
    ex, fx, _ = new()
    fx.timeout["order"] = "before"                                    # 도착 못 함
    cid = ex.enter(1, 1.0, 79_000.0, 80_500.0)
    assert ex.resolve(cid) == "unknown"                               # 방금 보낸 건 '없음' 이어도 아직 모른다
    ex.st["orders"][cid]["ts"] -= E.ABSENT_AFTER + 1
    assert ex.resolve(cid) == "absent" and fx.pos == 0
    ex.st["trade"] = None                                             # 호출자(러너)가 거래를 접는다
    cid2 = ex.enter(1, 1.0, 79_000.0, 80_500.0)
    assert cid2 != cid and fx.pos == 1.0


def t_rejected():
    ex, fx, _ = new()
    fx.orders_reject = True
    fx.private_post_trade_order = lambda b: (_ for _ in ()).throw(ccxt.InsufficientFunds("51008 insufficient"))
    cid = ex.enter(1, 1.0, 79_000.0, 80_500.0)
    assert ex.st["orders"][cid]["status"] == "rejected" and ex.st["trade"] is None and not ex.unresolved()


def t_partial_entry_protection():
    ex, fx, _ = new()
    fx.partial = 0.4
    cid = ex.enter(1, 1.0, 79_000.0, 80_500.0)
    ex.poll_fills()
    assert ex.st["orders"][cid]["status"] == "partial" and ex.sync() and algo(fx) == (0.4, 79_000.0)   # 부분 체결 직후 보호 활성
    fx.fill(cid)
    ex.poll_fills()
    assert ex.sync() and algo(fx) == (1.0, 79_000.0) and any(c[0] == "amend" for c in fx.calls)


def t_tp_partial_dedupe():
    ex, fx, _ = new()
    ex.enter(1, 1.0, 79_000.0, 80_500.0); ex.poll_fills(); ex.sync()
    tp = ex.take_profit(0.5, 80_500.04)
    b = [c[1] for c in fx.calls if c[0] == "order"][-1]
    assert b["reduceOnly"] == "true" and b["sz"] == "0.5" and b["px"] == "80500.1"   # 롱 익절가는 올림 (목표보다 불리하게 안 건다)
    fx.fill(tp, 0.3)
    n1 = len(ex.poll_fills()); n2 = len(ex.poll_fills())
    assert n1 == 1 and n2 == 0 and ex.st["orders"][tp]["filled"] == 0.3            # 같은 체결을 두 번 세지 않는다
    assert ex.sync() and algo(fx) == (0.7, 79_000.0)                                # 보호 수량 = 실제 잔여
    fx.fill(tp); ex.poll_fills()
    assert ex.sync() and algo(fx) == (0.5, 79_000.0)


def t_tp_min_size_goes_full():
    ex, fx, _ = new()
    ex.enter(1, 0.01, 79_000.0, 80_500.0); ex.poll_fills(); ex.sync()
    ex.take_profit(0.5, 80_500.0)
    assert [c[1] for c in fx.calls if c[0] == "order"][-1]["sz"] == "0.01"          # 0.005 는 최소 미만 → 전량


def t_cancel_race():
    ex, fx, _ = new()
    ex.enter(1, 1.0, 79_000.0, 80_500.0); ex.poll_fills(); ex.sync()
    tp = ex.take_profit(0.5, 80_500.0)
    fx.fill_before_cancel = True                                      # 취소가 도착하기 직전에 체결 → 51400
    assert ex.cancel(tp) == "filled"
    ex.poll_fills()
    assert fx.pos == 0.5 and ex.sync() and algo(fx) == (0.5, 79_000.0)
    ex.close("시간")
    assert fx.pos == 0 and [c[1] for c in fx.calls if c[0] == "order"][-1]["reduceOnly"] == "true"
    ex.poll_fills()
    assert ex.sync() and not fx.algos and ex.st["trade"] is None


def t_amend_fail_fallback():
    ex, fx, _ = new()
    ex.enter(1, 1.0, 79_000.0, 80_500.0); ex.poll_fills(); ex.sync()
    fx.amend_fail = True
    assert ex.move_stop(79_500.0) and algo(fx) == (1.0, 79_500.0)
    assert not ex.move_stop(79_200.0) and ex.st["trade"]["stop"] == 79_500.0        # 손절선 확대 거부


def t_stop_trigger_cleanup():
    ex, fx, _ = new()
    ex.enter(-1, 1.0, 81_000.0, 79_500.0); ex.poll_fills(); ex.sync()
    assert algo(fx) == (1.0, 81_000.0) and fx.pos == -1.0
    tp = ex.take_profit(0.5, 79_500.0)
    fx.trigger(81_050.0)                                              # 숏 손절 발동 → flat
    ex.poll_fills()
    assert fx.pos == 0 and ex.sync() and ex.st["orders"][tp]["status"] == "canceled" and ex.st["trade"] is None


def t_reduce_only_cannot_flip():
    fx = Fake()
    try:
        fx.private_post_trade_order({"side": "sell", "sz": "1", "ordType": "market", "clOrdId": "x", "reduceOnly": "true"})
    except ccxt.InvalidOrder:
        return
    raise AssertionError


def t_restart_restores_stop():
    ex, fx, path = new()
    ex.enter(1, 1.0, 79_000.0, 80_500.0); ex.poll_fills(); ex.sync()
    fx.algos.clear()                                                  # 재시작 사이 손절이 사라짐 (수동 취소·유실)
    ex2 = E.Exec(fx, E.spec(fx), path)                                # 같은 상태 파일로 새 프로세스
    assert ex2.recover() and algo(fx) == (1.0, 79_000.0)
    n = len(ex2.st["fills"])
    ex2.poll_fills()
    assert len(ex2.st["fills"]) == n                                  # 재시작 뒤에도 옛 체결을 다시 안 센다


def t_restart_crash_while_sending():
    ex, fx, path = new()
    fx.timeout["order"] = "after"
    cid = ex.enter(1, 1.0, 79_000.0, 80_500.0)
    ex.st["orders"][cid]["status"] = "sending"; ex.save()             # 응답 전에 죽었다
    ex2 = E.Exec(fx, E.spec(fx), path)
    assert ex2.recover() and ex2.st["orders"][cid]["status"] == "filled" and algo(fx) == (1.0, 79_000.0)


def t_restart_unknown_owner_halts():
    fx = Fake()
    fx.pos = 0.3                                                      # 다른 봇·사람의 포지션
    ex, _, _ = new(fx)
    try:
        ex.recover(); raise AssertionError
    except E.Halt as e:
        assert "주인 모를" in str(e) and ex.st["halt"]
    fx2 = Fake()
    fx2.foreign_orders = [{"clOrdId": "manual1", "state": "live"}]
    ex, _, _ = new(fx2)
    try:
        ex.recover(); raise AssertionError
    except E.Halt:
        pass


def t_day_guard():
    ex, fx, _ = new()
    ex.enter(1, 1.0, 79_000.0, 80_500.0); ex.poll_fills(); ex.sync()
    assert not ex.day_guard(9_950.0, 10_000.0)
    assert ex.day_guard(9_899.0, 10_000.0) and fx.pos == 0
    ex.poll_fills()
    assert ex.sync() and not fx.algos
    try:
        ex.enter(1, 1.0, 79_000.0, 80_500.0); raise AssertionError
    except E.Halt:
        pass


def t_risk_check():
    ex, fx, _ = new()
    ex.enter(1, 1.0, 79_000.0, 80_500.0); ex.poll_fills(); ex.sync()
    ok, info = ex.risk_check()
    assert ok and abs(info["buf"] - (80_000 - 55_000) / 80_000) < 1e-12
    fx.liq = 78_000.0
    assert not ex.risk_check()[0]                                     # 청산가까지 2.5% < 5% → 신규 중단
    fx.lever = "5"
    try:
        ex.risk_check(); raise AssertionError
    except E.Halt:
        pass


def t_funding_bills_dedupe():
    ex, fx, _ = new()
    fx.bills = [{"billId": "b1", "instId": E.INST, "balChg": "-0.8"}, {"billId": "b2", "instId": E.INST, "balChg": "0.3"},
                {"billId": "b3", "instId": "ETH-USDT-SWAP", "balChg": "-5"}]
    r1 = ex.funding(); r2 = ex.funding()
    assert r1["interval_h"] == 8 and abs(r2["paid_total"] - (-0.5)) < 1e-12


def t_secret_scrub():
    os.environ["OKX_SECRET"] = "SUPERSECRET123"
    try:
        ex, fx, path = new()
        fx.private_post_trade_order = lambda b: (_ for _ in ()).throw(ccxt.AuthenticationError("bad sign SUPERSECRET123"))
        ex.enter(1, 1.0, 79_000.0, 80_500.0)
        assert "SUPERSECRET123" not in path.read_text(encoding="utf-8")
    finally:
        del os.environ["OKX_SECRET"]


if __name__ == "__main__":
    ts = [v for k, v in dict(globals()).items() if k.startswith("t_")]
    for t in ts:
        t()
        print("ok", t.__name__)
    print(f"{len(ts)}/{len(ts)} 통과")
