"""상태기계 자체 점검 (모의 장부만 사용, 거래소·모델 호출 없음).
현물 전환으로 바뀐 부분만 본다: 숏 진입이 없어졌는지, 약세 구역이 청산으로만 쓰이는지, 5일 규칙.
사용: python test_bot.py
"""
import datetime as dt
import os
import pathlib
import sys

os.environ.update(MODE="paper", PAPER_CASH_KRW="1000000", SYMBOL="KRW-BTC")
ROOT = pathlib.Path(__file__).resolve().parent
DB = ROOT / "test_bot.db"
DB.unlink(missing_ok=True)

import autotrade as A                                     # noqa: E402

A.DB_PATH = DB
A.USE_CLAUDE = False
PX = 100_000_000


def cycle(zone, days_held):
    """run_cycle 의 상태기계 부분만 그대로 재현한다 (거래소·모델은 빼고)."""
    acc = A.account(None, PX)
    pos = acc["pos"]
    if pos:
        pos["held_days"] = days_held
    action = "hold"
    if pos:
        if zone == "short":
            action = "close"
        elif pos["held_days"] >= A.M.HOLD_DAYS and zone != "long":
            action = "close"
    elif zone == "long":
        action = "open"
    if action == "close":
        A.execute(None, 0, "close", pos["side"], pos["qty"], pos["qty"] * PX, PX, "test")
    elif action == "open":
        A.execute(None, 0, "open", "long", None, acc["equity"] * A.POSITION_PCT, PX, "test")
    return action


def backdate(days):
    A.set_state(entered_at=(dt.datetime.now(A.KST) - dt.timedelta(days=days)).isoformat(timespec="seconds"))


try:
    assert cycle("wait", 0) == "hold", "포지션 없고 wait 면 아무것도 안 한다"
    assert cycle("short", 0) == "hold", "현물이라 약세 구역에서 숏 진입하지 않는다"
    assert cycle("long", 0) == "open", "롱 구역이면 매수"
    assert A.state()["side"] == "long"

    assert cycle("long", 9) == "hold", "롱 구역이 유지되면 5일이 지나도 계속 보유"
    assert cycle("wait", 3) == "hold", "5일 전에는 구역이 빠져도 보유"
    assert cycle("wait", 5) == "close", "5일 지나고 롱 구역 아니면 청산"
    assert A.state()["side"] is None

    cycle("long", 0)
    assert cycle("short", 1) == "close", "약세 합류는 보유일과 무관하게 즉시 청산"
    assert A.state()["side"] is None

    cash = A.state()["cash"]
    assert cash < 1_000_000, f"같은 가격에 사고팔면 수수료만큼 줄어야 한다: {cash:,.0f}"
    assert cash > 1_000_000 * (1 - 6 * A.X.TAKER_FEE), f"수수료가 과다 차감됐다: {cash:,.0f}"
    assert A.paper_trades_done() == 2, "완료된 모의 거래 2회"
    print(f"ok  모의 잔고 {cash:,.0f}원 (수수료 {1_000_000 - cash:,.0f}원), 모의 거래 {A.paper_trades_done()}회")
finally:
    DB.unlink(missing_ok=True)
    sys.stdout.flush()
