"""봇 자체 점검 (모의 장부만 사용, 거래소·Claude 호출 없음).
본다: 급락 매수 신호 → 진입, 익절·손절·갭·동시 도달·만기 청산, 꺼진 동안의 봉 따라잡기, 지난 신호로 뒤늦게 사지 않기,
      진입 차단, Claude 게이트, 봇 밖 숏 포지션 + 2026-09-10 감사 회귀 테스트(주문/장부 순서 · 재조회 · 청산선 · 락).
사용: python test_bot.py
"""
import os
import pathlib
import sys

import numpy as np
import pandas as pd

os.environ.update(MODE="paper", PAPER_CASH="1000", SYMBOL="BTC/USDT:USDT", LEVERAGE="1", MAX_DAY_LOSS_PCT="15")
ROOT = pathlib.Path(__file__).resolve().parent
DB = ROOT / "test_bot.db"
DB.unlink(missing_ok=True)

import autotrade as A                                     # noqa: E402
import strategy as S                                      # noqa: E402

A.DB_PATH = DB
A.USE_CLAUDE = False
A.AUTORUN_OFF = ROOT / "test_autorun.off"
A.ENTRY_OFF = ROOT / "test_entry.off"
REAL_ASK = A.ask_claude          # 아래 점검들이 ask_claude 를 갈아 끼우므로 원본을 먼저 잡아 둔다
A._LOCK = object()               # 프로세스간 락은 이미 잡은 것으로 (실제 봇이 돌고 있어도 테스트는 돌아야 한다)
PX = 80_000.0
T0 = pd.Timestamp("2026-06-01 12:00", tz="UTC")
UP = pd.Series(np.linspace(50_000, 90_000, 280), index=pd.date_range(T0.normalize() - pd.Timedelta("260D"), periods=280, freq="D"))   # 시험 날짜들 전날까지 있다
DOWN = UP[::-1].set_axis(UP.index)                        # 전날 종가가 50·200일선 아래


def bars(n=60, start=T0, dip=None):
    """평평한 5분봉 n개 (마지막 봉 시작 = start + (n−1)·5분). dip 이면 마지막 봉 종가를 그만큼(%) 떨어뜨린다."""
    idx = pd.date_range(start - (n - 1) * S.BAR, periods=n, freq="5min")
    c = pd.DataFrame({"open": PX, "high": PX * 1.001, "low": PX * 0.999, "close": PX}, index=idx)
    if dip:
        c.iloc[-1] = [PX, PX, PX * (1 - dip / 100), PX * (1 - dip / 100)]
    return c


def after(c, *rows):
    """c 뒤에 (open, high, low, close) 봉들을 붙인다."""
    idx = pd.date_range(c.index[-1] + S.BAR, periods=len(rows), freq="5min")
    return pd.concat([c, pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)])


def cycle(c, d1=UP, px=PX):
    """run_cycle 의 판단·주문 부분(act)을 그대로 탄다. 거래소·Claude 만 뺀다."""
    action, reason, _, _, _ = A.act(None, None, 0, c, d1, px, A.account(None, px))
    assert action in ("open", "close", "hold"), action
    return action, reason


def enter_at(c):
    """방금 진입한 포지션의 진입 봉을 c 의 다음 봉으로 맞춘다 (execute 는 벽시계 시각을 적는다)."""
    A.set_state(entered_at=(c.index[-1] + S.BAR).tz_convert(A.KST).isoformat())


def flat():
    A.set_state(side=None, qty=0, entry=0, entered_at=None, seen_bar=None)


try:
    # ── 진입 ──
    assert cycle(bars())[0] == "hold", "급락이 없으면 관망"
    flat()
    assert cycle(bars(dip=3), DOWN)[0] == "hold", "50·200일선 아래면 급락이어도 관망"
    flat()
    c = bars(dip=3)
    act, why = cycle(c)
    assert act == "open" and A.state()["side"] == "long", f"급락 + 추세 통과면 롱 진입: {why}"
    assert A.state()["seen_bar"] == c.index[-1].isoformat()
    assert cycle(c)[0] == "hold", "보유 중 같은 봉으로는 아무것도 안 한다"
    e = A.state()["entry"]; up, dn = S.levels(e)

    # ── 익절: 모의 장부는 익절가에 체결 ──
    enter_at(c)
    cash0 = A.state()["cash"]
    c2 = after(c, (e, e * 1.01, e * 0.995, e * 1.005), (e * 1.005, up * 1.001, e, up))
    act, why = cycle(c2)
    assert act == "close" and "익절" in why and A.state()["side"] is None, why
    with A.db() as db:
        last = db.execute("SELECT price FROM orders WHERE action='close' ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert abs(last - up) < 1e-6, f"모의 익절은 익절가 {up:,.1f} 에 체결: {last}"
    assert A.state()["cash"] > cash0, "익절이면 현금이 늘어야 한다"

    # ── 청산한 봉·지난 봉으로는 다시 사지 않는다 ──
    assert cycle(c2)[0] == "hold", "이미 처리한 봉(seen_bar)으로는 진입하지 않는다"

    # ── 손절 · 갭 · 동시 도달 · 만기 ──
    for name, row_fn, want in (("손절", lambda e: (e, e, e * 0.93, e * 0.95), lambda e: e * 0.94),
                               ("갭", lambda e: (e * 0.90, e * 0.91, e * 0.89, e * 0.90), lambda e: e * 0.90),
                               ("동시", lambda e: (e, e * 1.05, e * 0.93, e), lambda e: e * 0.94)):
        flat()
        c = bars(start=T0 + pd.Timedelta("1D"), dip=3)
        assert cycle(c)[0] == "open"
        enter_at(c); e = A.state()["entry"]
        act, why = cycle(after(c, row_fn(e)))
        with A.db() as db:
            fill = db.execute("SELECT price FROM orders WHERE action='close' ORDER BY id DESC LIMIT 1").fetchone()[0]
        assert act == "close" and "손절" in why and abs(fill - want(e)) < 1e-6, f"{name}: {why} · 체결 {fill}"
    flat()
    c = bars(start=T0 + pd.Timedelta("2D"), dip=3)
    assert cycle(c)[0] == "open"
    enter_at(c); e = A.state()["entry"]
    rows = [(e, e * 1.001, e * 0.999, e)] * (S.MAXB + 1)                  # 익절·손절 없이 3일 + 1봉
    act, why = cycle(after(c, *rows))
    assert act == "close" and "만기" in why, f"864봉이 지나면 만기 청산: {why}"

    # ── 꺼져 있던 동안의 봉을 따라잡는다: 두 번째 새 봉이 익절 ──
    flat()
    c = bars(start=T0 + pd.Timedelta("5D"), dip=3)
    assert cycle(c)[0] == "open"
    enter_at(c); e = A.state()["entry"]
    c3 = after(c, (e, e, e * 0.99, e), (e, e * 1.05, e, e * 1.04), (e * 1.04, e * 1.04, e * 0.5, e * 0.5))
    act, why = cycle(c3)
    assert act == "close" and "익절" in why, f"지나간 봉 중 먼저 닿은 쪽(익절)으로 청산: {why}"

    # ── 지난 신호로 뒤늦게 사지 않는다: 급락은 과거 봉, 마지막 봉은 평평 ──
    flat()
    A.set_state(seen_bar=(T0 + pd.Timedelta("6D") - 10 * S.BAR).isoformat())
    c = after(bars(start=T0 + pd.Timedelta("6D") - 5 * S.BAR, dip=3), (PX, PX, PX, PX))
    assert cycle(c)[0] == "hold", "놓친 봉의 급락 신호로는 사지 않는다 (마지막 봉만 본다)"

    # ── 진입 차단: 진입만 막고 청산은 막지 않는다 ──
    flat()
    A.set_entry_block(True, "테스트")
    c = bars(start=T0 + pd.Timedelta("7D"), dip=3)
    assert cycle(c)[0] == "hold" and A.state()["side"] is None, "차단 중에는 진입하지 않는다"
    A.execute(None, 0, "open", "long", 0.01, 0.01 * PX, PX, "강제 보유")
    enter_at(c)
    assert cycle(after(c, (PX, PX, PX * 0.9, PX * 0.9)))[0] == "close", "차단 중에도 손절 청산은 나가야 한다"
    A.set_entry_block(False)

    # ── Claude 게이트: 주문 직전 한 번, 거부면 사지 않는다 ──
    A.USE_CLAUDE = True
    A.review_payload = lambda *a, **k: {}
    A.X.price = lambda pub: PX
    calls = []
    for approve, want in ((False, "hold"), (True, "open")):
        flat(); calls.clear()
        A.ask_claude = lambda p, a=approve: (calls.append(1), {"approve": a, "reason": "t", "model": "t",
                                                               "input_tokens": 1, "output_tokens": 1})[1]
        assert cycle(bars(start=T0 + pd.Timedelta("8D"), dip=3))[0] == want and len(calls) == 1, (approve, calls)
    A.USE_CLAUDE = False
    A.ask_claude = lambda p: (_ for _ in ()).throw(RuntimeError("omniroute down"))
    assert A.claude_gate({})["approve"] is False, "Claude 호출 실패는 거부 (확인 못 한 진입은 내지 않는다)"

    # ── 봇 밖에서 연 숏은 건드리지 않는다 (롱 전용) ──
    flat()
    A.set_state(side="short", qty=0.01, entry=PX, entered_at=T0.tz_convert(A.KST).isoformat())
    assert cycle(bars(start=T0 + pd.Timedelta("9D"), dip=3))[0] == "hold" and A.state()["side"] == "short"
    flat()

    # ── 모의 장부 · 데모 · 사고 차단기 ──
    before = A.state()
    A.execute(None, 0, "open", "long", 0.01, 0.01 * PX, PX, "demo test", demo=True)
    assert A.state() == before, "데모 주문이 모의 장부를 바꿨다"
    with A.db() as db:
        db.execute("INSERT INTO runs (timestamp, equity, paper_equity, real_equity) VALUES (?, 1000, 1000, 7)", (A.now(),))
    assert not A.day_loss_hit(900), "고점 대비 −10% 는 아직 아니다 (15%)"
    assert A.day_loss_hit(850), "고점 1000 대비 −15% 면 걸린다"
    assert not A.day_loss_hit(7, live=True), "모의 1000 과 실계좌 7 을 섞어 재면 안 된다 (2026-09-08 오작동)"
    assert A.day_loss_hit(5, live=True), "실계좌 고점 7 대비 −15% 면 걸린다"
    print(f"ok  전략 점검 통과 · 모의 거래 {A.paper_trades_done()}회 · 모의 현금 {A.state()['cash']:,.2f} USDT")

    # ══════ 2026-09-10 감사 회귀 테스트 ══════
    class FakeResp:                       # anthropic 응답 흉내
        def __init__(self, text):
            self.content = [type("B", (), {"type": "text", "text": text})()]
            self.model, self.usage = "t", type("U", (), {"input_tokens": 1, "output_tokens": 1})()

    def fake_client(text):
        A.anthropic.Anthropic = lambda **kw: type("C", (), {
            "messages": type("M", (), {"create": staticmethod(lambda **k: FakeResp(text))})()})()
    A.ask_claude = REAL_ASK
    fake_client('{"approve": "false", "reason": "안 된다"}')
    assert A.claude_gate({})["approve"] is False, 'approve="false" 가 승인으로 읽혔다 (bool("false") == True)'
    fake_client('{"approve": 1, "reason": "숫자"}')
    assert A.claude_gate({})["approve"] is False, "approve=1 도 Boolean 이 아니므로 거부여야 한다"
    fake_client('{"approve": true, "reason": "진짜 승인"}')
    assert A.claude_gate({})["approve"] is True, "진짜 JSON true 는 승인"

    class FakeEx:
        pass
    A.X.setup = lambda ex, lev: int(lev)
    A.X.explain = lambda e: str(e)[:80]
    flat()
    A.X.open_position = lambda ex, side, notional, px, cid=None: (_ for _ in ()).throw(TimeoutError("gateway timeout"))
    A.X.find_order = lambda ex, cid: None                       # 재조회에도 안 잡힌다 → 상태 불명
    assert A.execute(FakeEx(), 0, "open", "long", None, 1000, PX, "실패 테스트") is False
    assert A.state()["side"] is None, "주문이 실패했는데 장부에 포지션이 생겼다"
    with A.db() as db:
        st_row = db.execute("SELECT status, client_id FROM orders ORDER BY id DESC LIMIT 1").fetchone()
    assert st_row[0].startswith("unknown") and st_row[1], "의도(clOrdId)와 불명 상태가 기록돼야 한다"
    A.X.find_order = lambda ex, cid: {"status": "closed", "filled": 0.02, "avg": 79_000.0, "id": "X1"}
    assert A.execute(FakeEx(), 0, "open", "long", None, 1580, PX, "재조회 테스트") is True
    st = A.state()
    assert st["side"] == "long" and abs(st["qty"] - 0.02) < 1e-9 and st["entry"] == 79_000.0, f"실제 체결로 장부를 맞춰야 한다: {st}"
    A.X.close_position = lambda ex, pos, cid=None: (_ for _ in ()).throw(TimeoutError("gateway timeout"))
    A.X.find_order = lambda ex, cid: None
    assert A.execute(FakeEx(), 0, "close", "long", 0.02, 1580, PX, "청산 실패 테스트") is False
    assert A.state()["side"] == "long", "청산이 확인되지 않았는데 장부에서 포지션을 지웠다"
    A.X.close_position = lambda ex, pos, cid=None: "OK1"
    A.X.find_order = lambda ex, cid: {"status": "closed", "filled": 0.02, "avg": 79_000.0, "id": "OK1"}
    assert A.execute(FakeEx(), 0, "close", "long", 0.02, 1580, 79_000.0, "청산 성공") is True
    assert A.state()["side"] is None

    pos = {"side": "long", "qty": 1.0, "entry": 100.0, "lev": 2.1, "pnl": -35.0}   # 2.1배 → 거래소 3배
    assert A.liquidated(pos) and not A.liquidated({**pos, "pnl": -30.0}), "정수 레버리지 + 유지증거금 청산선"
    assert A.CASH_RESERVE > 0, "왕복 수수료·펀딩을 낼 현금이 남아야 한다"
    assert A.MAX_LEVERAGE == 2, "2배 초과는 근거가 없다 (research/aoa/dip_lev2.py)"

    A._CYCLE.acquire()
    ran, real = [], A._run_cycle
    A._run_cycle = lambda src="자동": ran.append(src)
    A.run_cycle("겹침")
    assert not ran, "사이클이 돌고 있는데 또 들어갔다"
    A._CYCLE.release()
    A.run_cycle("정상")
    assert ran == ["정상"]
    A._run_cycle = real
    print("ok  감사 회귀 테스트 통과 (LLM Boolean · 주문/장부 순서 · 재조회 · 청산선 · 락)")
finally:
    DB.unlink(missing_ok=True)
    A.AUTORUN_OFF.unlink(missing_ok=True)
    A.ENTRY_OFF.unlink(missing_ok=True)
    sys.stdout.flush()
