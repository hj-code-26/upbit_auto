"""업비트 잔여 자산 전량 시장가 매도 (OKX 전환용 1회성 스크립트).

  1) 보유 코인·평가액을 보여준다
  2) '실주문' 을 입력하면 5,000원 이상인 코인을 전부 KRW 로 판다. 5,000원 미만 먼지는 업비트가 주문을 거절하므로 남긴다
  3) 결과 잔고를 다시 보여준다. 남은 KRW 는 업비트 앱에서 출금 → OKX 입금은 직접 한다 (봇은 출금 권한이 없다)
사용: make liquidate  또는  python liquidate_upbit.py
"""
import sys
import time

from dotenv import load_dotenv

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")
import upbit as X  # noqa: E402

MIN = X.MIN_ORDER_KRW


def holdings(ex):
    out = []
    for cur, b in X._balances(ex).items():
        if cur == "KRW":
            continue
        qty = float(b["balance"])
        px = pyupbit_price(cur)
        out.append((cur, qty, px, qty * px))
    return out


def pyupbit_price(cur):
    p = X.pyupbit.get_current_price(f"KRW-{cur}")
    return float(p) if p else 0.0


def show(ex):
    krw = float(X._balances(ex).get("KRW", {}).get("balance", 0))
    print(f"KRW {krw:,.0f}원")
    for cur, qty, px, val in holdings(ex):
        print(f"{cur:6} {qty:.8f} × {px:,.0f} = {val:,.0f}원 {'(매도 대상)' if val >= MIN else '(먼지, 남김)'}")
    return krw


if __name__ == "__main__":
    ex = X.client()
    print("── 현재 업비트 자산 ──")
    show(ex)
    targets = [(c, q, v) for c, q, _, v in holdings(ex) if v >= MIN]
    if not targets:
        sys.exit("매도할 자산이 없습니다 (전부 5,000원 미만 먼지). 업비트 앱에서 남은 KRW 를 출금하세요.")
    if input(f"{len(targets)}개 코인을 전량 시장가 매도합니다. '실주문' 을 입력하면 진행: ").strip() != "실주문":
        sys.exit("취소")
    for cur, qty, val in targets:
        o = ex.sell_market_order(f"KRW-{cur}", qty)
        print(f"{cur} 매도 {qty:.8f} ({val:,.0f}원) → {o.get('uuid') if isinstance(o, dict) else o}")
    time.sleep(2)
    print("── 매도 후 ──")
    show(ex)
    print("남은 KRW 는 업비트 앱에서 출금하세요. 정리가 끝나면 .env 의 UPBIT_* 를 지우고 upbit.com 에서 키를 삭제하세요.")
