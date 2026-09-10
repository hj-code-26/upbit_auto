"""업비트 KRW 현물 클라이언트 (pyupbit). exchange.py(바이낸스 선물) 를 대체한다.

현물이라 바이낸스판과 다른 점 두 가지:
  · 레버리지 없음 (명목 = 자산 × POSITION_PCT)
  · 숏 진입 불가 → 약세 구역은 '청산 · 진입 금지' 로만 쓴다 (research_backtest_5y.txt 채택안 그대로)
일봉 경계는 09:00 KST(UTC 00:00) 라서 봇은 INTERVAL_MIN 분마다 마지막 완성 일봉으로 판단한다.
"""
import os

import pandas as pd
import pyupbit

SYMBOL = os.environ.get("SYMBOL", "KRW-BTC")
COIN = SYMBOL.split("-")[1]
TAKER_FEE = 0.0005          # 업비트 원화마켓 0.05%
MIN_ORDER_KRW = 5000        # 업비트 최소 주문 금액


def public():
    return None             # 시세 조회는 키가 필요 없다


def client():
    return pyupbit.Upbit(os.environ["UPBIT_ACCESS_KEY"], os.environ["UPBIT_SECRET_KEY"])


def candles(ex, count=100):
    """일봉(09:00 KST 경계). 마지막 행은 진행 중인 봉."""
    df = pyupbit.get_ohlcv(SYMBOL, interval="day", count=count)
    if df is None or df.empty:
        raise ValueError(f"{SYMBOL}: 캔들 없음")
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def price(ex):
    return float(pyupbit.get_current_price(SYMBOL))


def _balances(ex):
    return {b["currency"]: b for b in ex.get_balances()}


def snapshot(ex):
    """계좌 현황을 조회 두 번(잔고·시세)으로 한꺼번에. 봇과 대시보드가 같은 값을 본다.
    {price, krw, coin_qty, coin_value, equity, position} — 주문 잠금분도 자산에 포함한다."""
    b = _balances(ex)
    px = price(ex)
    krw = float(b.get("KRW", {}).get("balance", 0)) + float(b.get("KRW", {}).get("locked", 0))
    c = b.get(COIN)
    qty = (float(c["balance"]) + float(c["locked"])) if c else 0.0
    entry = float(c["avg_buy_price"]) if c else 0.0
    value = qty * px
    pos = None
    if value >= MIN_ORDER_KRW:              # 먼지 잔고는 포지션으로 보지 않는다
        pos = {"side": "long", "qty": qty, "entry": entry, "pnl": (px - entry) * qty}
    return {"price": px, "krw": krw, "coin_qty": qty, "coin_value": value, "equity": krw + value, "position": pos}


def equity(ex):
    return snapshot(ex)["equity"]


def position(ex):
    """보유 코인 {side:'long', qty, entry, pnl} 또는 None. 현물이라 side 는 항상 long."""
    return snapshot(ex)["position"]


def setup(ex, leverage=1):
    return None                              # 현물은 마진·레버리지 설정이 없다


def open_position(ex, side, notional_krw, px):
    if side != "long":
        raise ValueError("현물은 숏 진입 불가")
    krw = float(_balances(ex).get("KRW", {}).get("balance", 0))
    amount = min(notional_krw, krw) * (1 - TAKER_FEE)      # 수수료가 원화에서 따로 나가므로 여유를 둔다
    if amount < MIN_ORDER_KRW:
        raise ValueError(f"주문 금액 {amount:,.0f}원 < 최소 {MIN_ORDER_KRW:,}원")
    o = ex.buy_market_order(SYMBOL, amount)
    if not isinstance(o, dict) or "uuid" not in o:
        raise RuntimeError(f"매수 실패: {o}")
    return o["uuid"], amount / px                            # 체결 수량은 다음 사이클에 거래소 잔고로 보정된다


def close_position(ex, pos):
    qty = float(_balances(ex).get(COIN, {}).get("balance", 0))    # 잠긴 수량은 못 판다 → 실제 가용분으로
    o = ex.sell_market_order(SYMBOL, qty)
    if not isinstance(o, dict) or "uuid" not in o:
        raise RuntimeError(f"매도 실패: {o}")
    return o["uuid"]


if __name__ == "__main__":
    df = candles(None, 30)
    assert len(df) == 30 and df.close.iloc[-1] > 0 and df.index[-1] > df.index[0]
    assert abs(price(None) / df.close.iloc[-1] - 1) < 0.5      # 현재가와 마지막 종가가 같은 자릿수인지
    print("ok", SYMBOL, df.index[-1], f"{df.close.iloc[-1]:,.0f}", f"{price(None):,.0f}")
