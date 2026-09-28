"""거래소 보호주문(protect.py) mock 테스트 — test_audit 의 가짜 거래소에 OKX 알고 주문 흉내를 얹는다. 네트워크·운영 DB 없음.
흉내 낸 OKX 규칙: 한 포지션에 전량(closeFraction=1) TP/SL 은 하나만 · last 가격이 문턱을 넘으면 그때 포지션 전량을 시장가로 닫음
                  (reduceOnly — 포지션이 없으면 아무것도 안 함) · 봇이 시장가로 닫아도 알고 주문은 자동 취소하지 않음(보수적 가정).
사용: python test_protect.py
"""
import sys

import test_audit as TA                                   # 네트워크 차단 · 임시 DB · FakeEx 를 그대로 쓴다
import okx as X
import protect as P
import strategy as S

PX, CS = TA.PX, TA.CS
X._MARKET = None


class AlgoEx(TA.FakeEx):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.algos, self.children = {}, {}
        self.place_err = self.cancel_fail = None

    def load_markets(self):
        return {X.SYMBOL: {"id": "BTC-USDT-SWAP", "contractSize": CS}}

    def price_to_precision(self, s, p):
        return f"{round(p, 1)}"

    def private_post_trade_order_algo(self, p):
        self.calls.append(("order_algo", dict(p)))
        if self.place_err:
            return {"code": "1", "data": [{"sCode": self.place_err, "sMsg": "rejected"}]}
        if any(a["state"] == "live" for a in self.algos.values()):
            return {"code": "1", "data": [{"sCode": "51088", "sMsg": "only one TP/SL order for fully closing a position"}]}
        aid = f"A{len(self.algos)}"
        self.algos[aid] = {**p, "algoId": aid, "state": "live", "actualSide": ""}
        return {"code": "0", "data": [{"algoId": aid, "algoClOrdId": p["algoClOrdId"], "sCode": "0"}]}

    def private_get_trade_orders_algo_pending(self, p):
        if getattr(self, "blind", False):                   # 접수 응답은 성공인데 목록에 안 잡힌다 (지연·유실)
            return {"code": "0", "data": []}
        return {"code": "0", "data": [a for a in self.algos.values() if a["state"] == "live" and a["instId"] == p["instId"]]}

    def private_post_trade_cancel_algos(self, lst):
        if self.cancel_fail:
            return {"code": "1", "data": [{"sCode": "51000", "sMsg": "cancel failed"}]}
        for x in lst:
            self.algos[x["algoId"]]["state"] = "canceled"
        return {"code": "0", "data": [{"sCode": "0"}]}

    def private_get_trade_order_algo(self, p):
        return {"code": "0", "data": [next(a for a in self.algos.values() if a["algoClOrdId"] == p["algoClOrdId"])]}

    def move(self, px, slip=0.0):
        """last 가격이 px 로 움직인다 (갭 포함). 발동한 알고는 그 순간 포지션 전량을 시장가로 닫는다."""
        self.px = px
        for a in self.algos.values():
            if a["state"] != "live":
                continue
            side = "sl" if px <= float(a["slTriggerPx"]) else "tp" if px >= float(a["tpTriggerPx"]) else None
            if not side:
                continue
            if self.pos <= 0:                               # 닫을 포지션이 없다 — reduceOnly 라 아무것도 안 한다
                a["state"] = "canceled"
                continue
            fill = px * (1 - slip) if side == "sl" else px
            oid = f"c{len(self.children)}"
            self.children[oid] = {"id": oid, "average": fill, "filled": self.pos, "status": "closed"}
            self.pos = 0.0
            self.min_pos = min(self.min_pos, self.pos)
            a.update(state="effective", actualSide=side, ordIdList=[oid])

    def fetch_order(self, oid, sym, params=None):
        return self.children[oid] if oid in self.children else super().fetch_order(oid, sym, params)

    def live(self):
        return [a for a in self.algos.values() if a["state"] == "live"]


def open_long(ex, fill_px):
    """시세 PX 로 명목을 잡고 시장가 진입 → 실제 체결은 fill_px (미끄러짐). → 거래소가 알려 준 평단."""
    ex.px = fill_px
    cid = "a" + "0" * 20 + str(len(ex.orders))
    X.open_position(ex, "long", 990, PX, cid)
    return X.find_order(ex, cid)["avg"]


def pos_of(ex):
    return X.position(ex)


# ───────────── 시나리오 ─────────────
def t1():
    ex = AlgoEx()
    avg = open_long(ex, PX * 1.005)                        # 시세 80,000 · 체결 80,400
    P.place(ex, avg)
    a = ex.live()[0]
    up, dn = S.levels(avg)
    assert (float(a["tpTriggerPx"]), float(a["slTriggerPx"])) == (round(up, 1), round(dn, 1)), a
    assert a["closeFraction"] == "1" and a["reduceOnly"] == "true" and a["side"] == "sell" and "sz" not in a, a
    assert a["tpTriggerPxType"] == a["slTriggerPxType"] == "last" and a["tpOrdPx"] == a["slOrdPx"] == "-1", a


def t2():
    ex = AlgoEx()
    open_long(ex, PX)
    for _ in range(3):
        ok, why = P.ensure(ex, pos_of(ex))
        assert ok, why
    assert len(ex.live()) == 1 and len([c for c in ex.calls if c[0] == "order_algo"]) == 1, "ensure 가 보호주문을 중복으로 걸었다"


def t3():
    ex = AlgoEx(pos=1.23, entry=79_000.0)                  # 재시작 · 봇 밖 포지션 흡수 — 보호주문 없음
    ok, why = P.ensure(ex, pos_of(ex))
    assert ok and len(ex.live()) == 1 and float(ex.live()[0]["slTriggerPx"]) == round(79_000 * 0.94, 1), why


def t4():
    ex = AlgoEx()
    open_long(ex, PX)
    P.place(ex, PX * 1.10)                                 # 틀린 문턱으로 걸려 있다
    ok, why = P.ensure(ex, pos_of(ex))
    a = ex.live()
    assert ok and len(a) == 1 and float(a[0]["slTriggerPx"]) == round(PX * 0.94, 1), (why, a)


def t5():
    ex = AlgoEx()
    avg = open_long(ex, PX)
    cid = P.place(ex, avg)
    ex.move(PX * 0.90, slip=0.002)                          # 봇이 꺼진 동안 손절선 아래로 갭
    assert ex.pos == 0 and ex.min_pos >= 0
    o = P.outcome(ex, cid)
    assert o["side"] == "sl" and abs(o["px"] - PX * 0.90 * 0.998) < 1e-6, f"실제 체결가 대신 손절가를 가정했다: {o}"
    assert P.ensure(ex, pos_of(ex))[0] and not ex.live()


def t6():
    ex = AlgoEx()
    open_long(ex, PX)
    P.place(ex, PX)
    X.close_position(ex, pos_of(ex), "a" + "9" * 20)       # 봇이 만기로 시장가 청산 — 보호주문은 남아 있다
    assert len(ex.live()) == 1
    ok, why = P.ensure(ex, None)                           # 다음 사이클: flat → 남은 것 정리
    assert ok and not ex.live(), why
    avg = open_long(ex, PX * 1.2)                          # 새 진입 — 옛 문턱(PX 기준)이 새 포지션을 닫으면 안 된다
    P.place(ex, avg)
    ex.move(PX * 1.2 * 0.95)                                # 옛 문턱이면 익절(+4% of PX) 구간, 새 문턱으론 아무 일 없음
    assert ex.pos > 0 and len(ex.live()) == 1 and float(ex.live()[0]["slTriggerPx"]) == round(avg * 0.94, 1)


def t7():
    ex = AlgoEx()
    open_long(ex, PX)
    P.place(ex, PX)
    stale = pos_of(ex)
    ex.move(PX * 0.93)                                      # 거래소 손절이 먼저 나갔다
    X.close_position(ex, stale, "a" + "8" * 20)            # 봇은 낡은 포지션으로 시장가 청산을 낸다 (reduceOnly)
    assert ex.pos == 0 and ex.min_pos >= 0, f"숏이 생겼다: {ex.pos}"
    assert ex.sells()[-1][3].get("reduceOnly") is True


def t8():
    ex = AlgoEx()
    open_long(ex, PX)
    P.place(ex, PX)
    X.close_position(ex, pos_of(ex), "a" + "7" * 20)
    ex.cancel_fail = True
    ok, why = P.ensure(ex, None)
    assert not ok and "신규 진입 금지" in why, f"남은 보호주문을 못 치웠는데 진입을 허용했다: {why}"


def t9():
    ex = AlgoEx()
    open_long(ex, PX)
    ex.place_err = "51330"                                  # 실계좌 확인 전: closeFraction 조합 거부 가능성
    ok, why = P.ensure(ex, pos_of(ex))
    assert not ok and "51330" in why and ex.pos > 0, why    # 포지션은 남고, 봇 봉 마감 판정이 예비선
    ex.place_err = None
    assert P.ensure(ex, pos_of(ex))[0] and len(ex.live()) == 1, "다음 사이클에 다시 걸어야 한다"


def t10():
    ex = AlgoEx()
    ex.modes = ["partial"]
    avg = open_long(ex, PX)                                 # 0.61 계약만 체결
    P.place(ex, avg)
    ex.move(PX * 0.93)
    assert ex.pos == 0 and ex.min_pos >= 0 and ex.children["c0"]["filled"] == 0.61, ex.children


def t11():
    ex = AlgoEx()
    open_long(ex, PX)
    ex.algos["X"] = {"algoId": "X", "algoClOrdId": "manual1", "instId": "BTC-USDT-SWAP", "state": "live",
                     "tpTriggerPx": "999999", "slTriggerPx": "1"}      # 사람이 건 알고 주문
    ok, why = P.ensure(ex, pos_of(ex))
    assert ex.algos["X"]["state"] == "live" and "사람이 건 알고 1개" in why, why
    # 사람이 건 것이 이미 전량 TP/SL 이면 OKX 가 우리 것을 거부한다 → ok=False 로 드러나야 한다 (조용히 통과 금지)
    assert not ok and not [a for a in ex.live() if a["algoClOrdId"].startswith("p")], why


def t12():
    ex = AlgoEx()
    open_long(ex, PX)
    ex.blind = True
    ok, why = P.ensure(ex, pos_of(ex))
    assert not ok, f"걸었다는 응답만 믿고 확인 없이 보호 중이라고 보고했다: {why}"


CASES = [("P1", "실제 체결 평단으로 문턱 · closeFraction·reduceOnly·last·시장가", t1), ("P2", "ensure 반복해도 보호주문 1개", t2),
         ("P3", "재시작·흡수 포지션에 보호주문 보충", t3), ("P4", "틀린 문턱 → 취소 후 재설정", t4),
         ("P5", "다운타임 중 갭 손절 → 실제 체결가로 결과 확인", t5), ("P6", "봇 청산 뒤 남은 보호주문 정리 · 새 포지션에 옛 문턱 안 씀", t6),
         ("P7", "거래소 손절과 봇 청산 경합 → 숏 없음", t7), ("P8", "flat 인데 취소 실패 → 신규 진입 금지 신호", t8),
         ("P9", "보호주문 거부 → 실패 보고 · 다음 사이클 재시도", t9), ("P10", "부분 체결 포지션 전량만 닫음", t10),
         ("P11", "사람이 건 알고 주문은 안 건드림 · 충돌은 실패로 보고", t11), ("P12", "건 뒤 목록에서 확인 안 되면 실패로 보고", t12)]

if __name__ == "__main__":
    bad = []
    for sid, name, fn in CASES:
        try:
            fn()
            res = "통과"
        except Exception as e:                             # noqa: BLE001 — 예외도 실패로 센다
            res, bad = f"실패 — {type(e).__name__}: {e}", bad + [sid]
        print(f"[{sid:>3}] {name}: {res}", flush=True)
    print(f"\n{len(CASES) - len(bad)}/{len(CASES)} 통과 · 실패 {bad or '없음'}")
    sys.exit(1 if bad else 0)
