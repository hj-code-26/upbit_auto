"""OKX USDT 무기한 선물 클라이언트 (ccxt). 격리 마진 · 단방향(net) 포지션. upbit.py 를 대체한다.

  · OKX_DEMO=1 이면 모의투자(Demo Trading) 서버로 주문이 나간다. 키도 데모 전용 키여야 한다
    (okx.com > 모의투자 > API 에서 발급. 실계좌 키와 다르다).
  · 레버리지: setup(ex, LEVERAGE) 가 격리 마진 + 레버리지를 심볼에 설정한다. 명목 = 자산 × POSITION_PCT × LEVERAGE.
  · OKX 주문 단위는 '계약' (BTC-USDT-SWAP 은 1계약 = 0.01 BTC, 최소 0.01 계약) 이지만 봇 안의 qty 는 항상 BTC 수량이다.
    변환은 이 파일의 contracts() 한 곳에서만 한다.
일봉 경계는 UTC 00:00 (KST 09:00) 라서 업비트와 같다.
"""
import os
import re
import sys

import ccxt
import pandas as pd
from dotenv import load_dotenv

load_dotenv()                # autotrade 보다 먼저 import 되므로 여기서 .env 를 읽어야 SYMBOL·DEMO 가 맞는다
SYMBOL = os.environ.get("SYMBOL", "BTC/USDT:USDT")
COIN = SYMBOL.split("/")[0]
CCY = "USDT"
DEMO = os.environ.get("OKX_DEMO", "1") != "0"          # 기본 대상(자동 매매). 대시보드는 주문마다 demo 를 골라 보낸다
DEMO_KEYS = ("OKX_DEMO_API_KEY", "OKX_DEMO_SECRET", "OKX_DEMO_PASSPHRASE")   # 데모 서버는 모의투자 전용 키를 쓴다
TAKER_FEE = 0.0005          # 0.05% (일반 등급)
MIN_ORDER = 10              # 최소 명목 (USDT). 0.01계약 × BTC 가격보다 조금 넉넉히
_MARKET = None


def explain(e):
    """OKX 인증 에러를 사람이 읽을 한 줄로. 원문 JSON 을 그대로 화면에 뿌리면 무슨 일인지 알 수가 없다."""
    s = str(e)
    if "50110" in s:                       # 집 인터넷은 공인 IP 가 바뀌므로 이건 주기적으로 재발한다
        ip = re.search(r"Your IP ([\d.]+)", s)
        return (f"OKX API 키에 현재 IP {ip.group(1) if ip else '(불명)'} 가 등록돼 있지 않습니다 (50110). "
                "okx.com > 우측 상단 프로필 > API > 해당 키 편집 > IP 주소에 추가하세요. "
                "공유기 재부팅·ISP 재할당으로 IP 가 바뀌면 다시 등록해야 합니다.")
    if "50101" in s:
        return ("키가 환경과 맞지 않습니다 (50101) — 실계좌 키를 데모 서버에 쓰거나 그 반대입니다. "
                ".env 의 OKX_DEMO 와 어떤 키를 넣었는지 확인하세요.")
    if any(c in s for c in ("50102", "50111", "50113")):
        return ("OKX 인증 실패 — 키·시크릿·패스프레이즈가 틀렸거나 PC 시각이 어긋났습니다 (50102는 타임스탬프). "
                ".env 값과 윈도우 시계 동기화를 확인하세요.")
    return f"{type(e).__name__}: {s}"[:200]


def have_keys(demo=False):
    return all(os.environ.get(k) for k in (DEMO_KEYS if demo else ("OKX_API_KEY", "OKX_SECRET", "OKX_PASSPHRASE")))


def _ex(keys, demo=None):
    demo = DEMO if demo is None else demo
    cfg = {"enableRateLimit": True, "options": {"defaultType": "swap"}}
    if keys:
        if not have_keys(demo):
            raise ValueError(f".env 에 {', '.join(DEMO_KEYS)} 가 없습니다" if demo else ".env 에 OKX 키가 없습니다")
        k = DEMO_KEYS if demo else ("OKX_API_KEY", "OKX_SECRET", "OKX_PASSPHRASE")
        cfg.update(apiKey=os.environ[k[0]], secret=os.environ[k[1]], password=os.environ[k[2]])
    ex = ccxt.okx(cfg)
    if demo:
        ex.set_sandbox_mode(True)
    return ex


def public():
    return _ex(False)


def client(demo=None):
    """demo=None 이면 .env 의 OKX_DEMO 를 따른다. 대시보드는 True/False 를 직접 준다."""
    return _ex(True, demo)


def market(ex):
    global _MARKET
    if _MARKET is None:
        _MARKET = ex.load_markets()[SYMBOL]
    return _MARKET


def candles(ex, count=100, tf="1d"):
    """봉 (1d = UTC 00:00 경계, 4h = UTC 00/04/08/... 경계). 마지막 행은 진행 중인 봉."""
    rows = ex.fetch_ohlcv(SYMBOL, tf, limit=count)
    if not rows:
        raise ValueError(f"{SYMBOL}: 캔들 없음")
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df.timestamp, unit="ms", utc=True).dt.tz_convert("Asia/Seoul")
    return df.set_index("timestamp").astype(float)


def bars_since(ex, since, tf="5m"):
    """since(UTC) 이후 봉 전부 — UTC 인덱스, 마지막 행은 진행 중일 수 있다. OKX 는 since 조회를 100개씩 준다.
    봇이 꺼져 있던 동안의 봉까지 따라잡을 때 쓴다 (candles() 는 최근 N개뿐).
    ccxt 는 since 조회를 [since, since+100봉) 창 하나로 보낸다. 창 안에 결측이 있으면 100개가 안 와도 뒤에 봉이 있으므로
    '100개 미만이면 끝' 으로 멈추지 않고 창 단위로 지금까지 넘긴다 (2026-09-28 감사: 결측 창에서 조회가 끊겨 낡은 봉을 최신으로 봤다)."""
    step = int(pd.Timedelta(tf).total_seconds() * 1000) * 100
    rows, t, end = [], int(since.timestamp() * 1000), int(pd.Timestamp.now(tz="UTC").timestamp() * 1000)
    while t <= end:
        rows += ex.fetch_ohlcv(SYMBOL, tf, since=t, limit=100)
        t += step
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"]).drop_duplicates("timestamp")
    df.index = pd.to_datetime(df.timestamp, unit="ms", utc=True)
    return df.drop(columns="timestamp").astype(float).sort_index()


def price(ex):
    return float(ex.fetch_ticker(SYMBOL)["last"])


def position(ex):
    """열린 포지션 {side: long|short, qty(BTC), entry, pnl, liq} 또는 None."""
    for p in ex.fetch_positions([SYMBOL]):
        if float(p.get("contracts") or 0) > 0:
            return {"side": p["side"], "qty": float(p["contracts"]) * float(market(ex)["contractSize"]), "entry": float(p["entryPrice"]),
                    "pnl": float(p.get("unrealizedPnl") or 0), "liq": float(p.get("liquidationPrice") or 0)}
    return None


def snapshot(ex):
    """{price, cash, coin_qty(BTC), coin_value, equity, position}. 봇과 대시보드가 같은 값을 본다."""
    px = price(ex)
    bal = ex.fetch_balance().get(CCY) or {}          # USDT 가 한 번도 안 들어온 계좌는 키 자체가 없다 → 0
    pos = position(ex)
    qty = pos["qty"] if pos else 0.0
    return {"price": px, "cash": float(bal.get("free") or 0), "coin_qty": qty, "coin_value": qty * px,
            "equity": float(bal.get("total") or 0), "position": pos}


def equity(ex):
    return snapshot(ex)["equity"]


def setup(ex, leverage):
    """단방향 포지션 · 격리 마진 · 레버리지 → 거래소가 실제로 들고 있는 레버리지를 돌려준다.

    포지션 모드 설정은 '이미 그 모드' 일 때 OKX 가 에러를 준다. 그렇다고 모든 에러를 삼키면
    **양방향(hedge) 모드인 채로 주문이 나가는 것**을 못 잡는다 (2026-09-10 감사).
    그래서 실패해도 무시하지 않고, 마지막에 fetch_positions 로 최종 상태를 확인한다."""
    mode_err = None
    try:
        ex.set_position_mode(False, SYMBOL)
    except ccxt.BaseError as e:
        mode_err = e                                       # 대개 '이미 net 모드' — 아래 검증으로 가른다
    ex.set_leverage(int(leverage), SYMBOL, params={"mgnMode": "isolated"})
    got = verify_setup(ex, int(leverage))
    if got.get("posMode") not in (None, "net_mode") :
        raise ValueError(f"OKX 포지션 모드가 net 이 아닙니다 ({got.get('posMode')}) — 주문을 내지 않습니다 ({mode_err})")
    return int(leverage)


def verify_setup(ex, leverage):
    """설정이 실제로 걸렸는지 계정·포지션 쪽에서 되읽는다. 못 읽으면 빈 dict (판단 보류)."""
    out = {}
    try:
        cfg = ex.private_get_account_config()["data"][0]
        out["posMode"] = cfg.get("posMode")
    except Exception:                                      # noqa: BLE001
        pass
    try:
        for p in ex.fetch_positions([SYMBOL]):
            info = p.get("info") or {}
            if float(p.get("contracts") or 0) > 0:
                out["lever"], out["mgnMode"] = info.get("lever"), info.get("mgnMode")
    except Exception:                                      # noqa: BLE001
        pass
    return out


def contracts(ex, qty_btc):
    """BTC 수량 → 계약 수 (거래소 정밀도로 내림)."""
    return float(ex.amount_to_precision(SYMBOL, qty_btc / float(market(ex)["contractSize"])))


def open_position(ex, side, notional, px, cid=None):
    if notional < MIN_ORDER:
        raise ValueError(f"주문 금액 {notional:,.2f} USDT < 최소 {MIN_ORDER} USDT")
    n = contracts(ex, notional / px)
    if n <= 0:
        raise ValueError(f"계약 수 0 (명목 {notional:,.2f} USDT)")
    p = {"tdMode": "isolated", **({"clOrdId": cid} if cid else {})}
    o = ex.create_order(SYMBOL, "market", "buy" if side == "long" else "sell", n, params=p)
    return o["id"], n * float(market(ex)["contractSize"])


def close_position(ex, pos, cid=None):
    p = {"tdMode": "isolated", "reduceOnly": True, **({"clOrdId": cid} if cid else {})}
    o = ex.create_order(SYMBOL, "market", "sell" if pos["side"] == "long" else "buy", contracts(ex, pos["qty"]), params=p)
    return o["id"]


def find_order(ex, cid):
    """clOrdId 로 주문을 다시 찾는다 → {status, filled(BTC), avg, raw} 또는 None.

    타임아웃·연결 끊김은 **주문 실패가 아니다**. 그 주문은 이미 거래소에 도착해 체결됐을 수 있다.
    로컬에서 만든 clOrdId 로 조회해 실제로 무슨 일이 있었는지 확인하는 것이 유일하게 안전한 방법이다
    (2026-09-10 감사: 예전에는 예외가 나면 그냥 '실패' 로 적고 장부를 되돌렸다)."""
    try:
        o = ex.fetch_order(None, SYMBOL, params={"clOrdId": cid})
    except Exception:                                      # noqa: BLE001 — 못 찾는 것도 정보다
        return None
    if not o:
        return None
    cs = float(market(ex)["contractSize"])
    return {"status": o.get("status"), "filled": float(o.get("filled") or 0) * cs,
            "avg": float(o.get("average") or o.get("price") or 0), "id": o.get("id")}


def open_orders(ex):
    """미체결 주문 목록 — 재시작·타임아웃 뒤 대사(reconcile)용."""
    try:
        return ex.fetch_open_orders(SYMBOL)
    except Exception:                                      # noqa: BLE001
        return []


if __name__ == "__main__":
    if "keys" in sys.argv:                       # python okx.py keys — 키가 맞는지 잔고 조회로 확인 (주문 안 함)
        missing = [k for k in ("OKX_API_KEY", "OKX_SECRET", "OKX_PASSPHRASE") if not os.environ.get(k)]
        if missing:
            sys.exit(f".env 에 {', '.join(missing)} 가 비어 있습니다")
        try:
            snap = snapshot(client())
        except Exception as e:                   # noqa: BLE001 — 스택 대신 무엇을 고쳐야 하는지 한 줄로
            sys.exit(explain(e))
        print(f"ok  {'데모' if DEMO else '실계좌'} 잔고 {snap['equity']:,.2f} USDT (가용 {snap['cash']:,.2f}) · "
              f"{COIN} {snap['price']:,.1f} · 포지션 {snap['position'] or '없음'}")
        sys.exit()
    assert "121.0.0.1" in explain(Exception('{"msg":"Your IP 121.0.0.1 is not in your API key IP whitelist.","code":"50110"}'))
    assert "OKX_DEMO" in explain(Exception('{"msg":"APIKey does not match current environment.","code":"50101"}'))
    assert explain(ValueError("boom")).startswith("ValueError")
    ex = public()
    df = candles(ex, 30)
    assert len(df) == 30 and df.close.iloc[-1] > 0 and df.index[-1] > df.index[0]
    px = price(ex)
    assert abs(px / df.close.iloc[-1] - 1) < 0.5
    n = contracts(ex, 100 / px)
    assert 0 < n * float(market(ex)["contractSize"]) * px <= 100
    print("ok", SYMBOL, "demo" if DEMO else "LIVE", df.index[-1], f"{px:,.1f}", "100USDT =", n, "계약")
