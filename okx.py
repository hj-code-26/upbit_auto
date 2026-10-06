"""OKX v5 REST — USDT 무기한 선물, net 포지션 모드, 격리(isolated) 전용.
키는 환경변수에서만 받고 출력·로그하지 않는다.

  Client : 실거래 / 데모 거래(x-simulated-trading: 1)
  Paper  : dry-run. 시세는 공개 API, 주문·포지션·손절은 장부에서. sync() 가 지난 실행 이후의
           확정 1H 봉으로 펀딩과 손절·강제청산(가까운 쪽 먼저) 체결을 재생한다. Client 와 같은 메서드를 가진다.
재시도: GET 은 지수 백오프. 주문 POST 는 재전송 전에 반드시 clOrdId/algoClOrdId 로 이미 접수됐는지 조회한다.
"""
import base64
import datetime as dt
import hashlib
import hmac
import json
import logging
import math
import time
from urllib.parse import urlencode

import pandas as pd
import requests

import rls

BASE = "https://www.okx.com"
RETRIES = 5
TRANSIENT_CODES = {"50001", "50004", "50011", "50013", "50026"}    # 일시 장애·타임아웃·호출 제한
NOT_FOUND = "51603"                                                  # Order does not exist
log = logging.getLogger("okx")


class OKXError(RuntimeError):
    def __init__(self, code, msg):
        super().__init__(f"OKX {code}: {msg}")
        self.code = code


def transient(e):
    return isinstance(e, requests.RequestException) or (isinstance(e, OKXError) and e.code in TRANSIENT_CODES)


def fmt(x):
    return f"{x:.8f}".rstrip("0").rstrip(".")


def floor_to(x, step):
    return math.floor(x / step + 1e-9) * step


class Client:
    def __init__(self, key="", secret="", passphrase="", demo=False, session=None, sleep=time.sleep):
        self.key, self.secret, self.passphrase, self.demo = key, secret, passphrase, demo
        self.s, self.sleep = session or requests.Session(), sleep

    def __repr__(self):                                           # 키가 로그·트레이스백에 찍히지 않게
        return f"Client(demo={self.demo})"

    def _call(self, method, path, params=None, body=None, private=False):
        q = "?" + urlencode(params) if params else ""
        data = json.dumps(body) if body is not None else ""
        h = {"Content-Type": "application/json"}
        if self.demo:
            h["x-simulated-trading"] = "1"
        if private:
            ts = dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            mac = hmac.new(self.secret.encode(), (ts + method + path + q + data).encode(), hashlib.sha256)
            h.update({"OK-ACCESS-KEY": self.key, "OK-ACCESS-SIGN": base64.b64encode(mac.digest()).decode(),
                      "OK-ACCESS-TIMESTAMP": ts, "OK-ACCESS-PASSPHRASE": self.passphrase})
        r = self.s.request(method, BASE + path + q, data=data or None, headers=h, timeout=10)
        if r.status_code == 429 or r.status_code >= 500:
            r.raise_for_status()
        j = r.json()
        if j.get("code") != "0":
            sub = (j.get("data") or [{}])[0] if isinstance(j.get("data"), list) else {}
            raise OKXError(sub.get("sCode") or j.get("code"), sub.get("sMsg") or j.get("msg"))
        return j["data"]

    def _retry(self, fn, what):
        for i in range(RETRIES):
            try:
                return fn()
            except Exception as e:  # noqa: BLE001
                if not transient(e) or i == RETRIES - 1:
                    raise
                log.warning("%s 재시도 %d/%d: %s", what, i + 1, RETRIES, e)
                self.sleep(min(2 ** i, 30))

    def get(self, path, params=None, private=False):
        return self._retry(lambda: self._call("GET", path, params, private=private), path)

    def _submit(self, path, body, lookup):
        """주문성 POST. 응답이 불확실해 재시도할 때는 먼저 lookup() 으로 접수 여부를 확인하고, 있으면 다시 보내지 않는다."""
        for i in range(RETRIES):
            if i:
                self.sleep(min(2 ** i, 30))
                found = lookup()
                if found:
                    return found
            try:
                return self._call("POST", path, body=body, private=True)
            except Exception as e:  # noqa: BLE001
                if not transient(e) or i == RETRIES - 1:
                    raise
                log.warning("%s 응답 불확실, 조회 후 재시도 %d/%d: %s", path, i + 1, RETRIES, e)

    # ---------- 공개 ----------
    def instrument(self, inst):
        d = self.get("/api/v5/public/instruments", {"instType": "SWAP", "instId": inst})[0]
        return {k: float(d[k]) for k in ("ctVal", "lotSz", "minSz", "tickSz", "lever")}

    def ticker(self, inst):
        d = self.get("/api/v5/market/ticker", {"instId": inst})[0]
        return {"last": float(d["last"]), "open_utc0": float(d["sodUtc0"])}

    def candles(self, inst, since_ms):
        """since_ms 이후 1H 봉 오름차순 DataFrame [ts, open, high, low, close, confirm]."""
        rows, after = [], None
        while True:
            p = {"instId": inst, "bar": "1H", "limit": 100}
            if after:
                p["after"] = after
            page = self.get("/api/v5/market/history-candles", p)
            rows += page
            if not page or int(page[-1][0]) <= since_ms:
                break
            after = page[-1][0]
            self.sleep(0.12)                                      # history-candles 호출 제한 (20회/2초)
        df = pd.DataFrame([r[:5] + [r[8]] for r in rows], columns=["ts", "open", "high", "low", "close", "confirm"])
        df = df.astype({"ts": "int64", "open": float, "high": float, "low": float, "close": float, "confirm": int})
        return df[df.ts >= since_ms].drop_duplicates("ts").sort_values("ts").reset_index(drop=True)

    def funding_history(self, inst, since_ms):
        """since_ms 이후 확정 펀딩 [(fundingTime ms, realizedRate)] 오름차순 (최근 3개월까지만 제공)."""
        d = self.get("/api/v5/public/funding-rate-history", {"instId": inst, "limit": 100})
        return sorted((int(x["fundingTime"]), float(x["realizedRate"] or x["fundingRate"]))
                      for x in d if int(x["fundingTime"]) > since_ms)

    # ---------- 계좌 ----------
    def config(self):
        return self.get("/api/v5/account/config", private=True)[0]

    def balance(self):
        """(총자산 USD, 가용 USDT)"""
        d = self.get("/api/v5/account/balance", {"ccy": "USDT"}, private=True)[0]
        u = next((x for x in d.get("details", []) if x["ccy"] == "USDT"), {})
        return float(d["totalEq"]), float(u.get("availBal") or 0)

    def position(self, inst):
        """{side ±1, sz 계약, avgPx, upl, realizedPnl, fee, fundingFee, margin, mgnMode, lever, liqPx} | None"""
        for p in self.get("/api/v5/account/positions", {"instType": "SWAP", "instId": inst}, private=True):
            sz = float(p.get("pos") or 0)
            if sz:
                n = lambda k: float(p.get(k) or 0)  # noqa: E731
                return {"side": 1 if sz > 0 else -1, "sz": abs(sz), "avgPx": n("avgPx"), "upl": n("upl"),
                        "realizedPnl": n("realizedPnl"), "fee": n("fee"), "fundingFee": n("fundingFee"),
                        "margin": n("margin") or n("imr"), "mgnMode": p.get("mgnMode"), "lever": n("lever"),
                        "liqPx": n("liqPx")}
        return None

    def positions_history(self, inst, since_ms):
        """since_ms 이후 닫힌 포지션 오름차순 [{uTime, side, closeAvgPx, realizedPnl, fee, fundingFee, type}]
        type: 1 일부 청산, 2 전량 청산, 3 강제청산, 4 일부 강제청산, 5 ADL"""
        d = self.get("/api/v5/account/positions-history",
                     {"instType": "SWAP", "instId": inst, "before": since_ms, "limit": 100}, private=True)
        out = [{"uTime": int(x["uTime"]), "side": 1 if x.get("direction") == "long" else -1,
                "closeAvgPx": float(x.get("closeAvgPx") or 0), "realizedPnl": float(x.get("realizedPnl") or 0),
                "fee": float(x.get("fee") or 0), "fundingFee": float(x.get("fundingFee") or 0), "type": x.get("type")}
               for x in d]
        return sorted((x for x in out if x["uTime"] > since_ms), key=lambda x: x["uTime"])

    def set_leverage(self, inst, lever):
        body = {"instId": inst, "lever": str(lever), "mgnMode": "isolated"}
        self._retry(lambda: self._call("POST", "/api/v5/account/set-leverage", body=body, private=True), "set-leverage")

    def leverage_info(self, inst):
        """거래소에 실제 적용된 격리 레버리지 (net 모드라 항목 1개)."""
        d = self.get("/api/v5/account/leverage-info", {"instId": inst, "mgnMode": "isolated"}, private=True)[0]
        return {"lever": float(d["lever"]), "mgnMode": d["mgnMode"]}

    # ---------- 주문 ----------
    def get_order(self, inst, cid):
        try:
            d = self.get("/api/v5/trade/order", {"instId": inst, "clOrdId": cid}, private=True)
        except OKXError as e:
            if e.code == NOT_FOUND:
                return None
            raise
        if not d:
            return None
        x = d[0]
        return {"cid": cid, "state": x["state"], "avgPx": float(x.get("avgPx") or 0),
                "sz": float(x.get("accFillSz") or 0), "fee": float(x.get("fee") or 0)}

    def order(self, inst, side, sz, cid, reduce=False):
        """시장가. 같은 cid 가 이미 있으면 다시 보내지 않는다. 체결 정보를 돌려준다."""
        if not self.get_order(inst, cid):
            body = {"instId": inst, "tdMode": "isolated", "side": "buy" if side > 0 else "sell",
                    "ordType": "market", "sz": fmt(sz), "clOrdId": cid}
            if reduce:
                body["reduceOnly"] = True
            self._submit("/api/v5/trade/order", body, lambda: self.get_order(inst, cid))
        for _ in range(10):
            o = self.get_order(inst, cid)
            if o and o["state"] in ("filled", "canceled", "mmp_canceled"):
                return o
            self.sleep(1)
        raise RuntimeError(f"{cid} 체결 확인 실패")

    def stops(self, inst):
        d = self.get("/api/v5/trade/orders-algo-pending",
                     {"ordType": "conditional", "instType": "SWAP", "instId": inst}, private=True)
        return [{"algoId": x["algoId"], "cid": x.get("algoClOrdId", ""), "sz": float(x["sz"]),
                 "side": 1 if x["side"] == "buy" else -1, "trigger": float(x.get("slTriggerPx") or 0)} for x in d]

    def place_stop(self, inst, pos_side, sz, trigger, cid):
        """포지션 반대 방향 reduce-only stop-market (거래소 측 조건부 주문 → 봇이 죽어도 남는다)."""
        body = {"instId": inst, "tdMode": "isolated", "side": "sell" if pos_side > 0 else "buy",
                "ordType": "conditional", "sz": fmt(sz), "reduceOnly": True, "algoClOrdId": cid,
                "slTriggerPx": fmt(trigger), "slOrdPx": "-1", "slTriggerPxType": "last"}
        self._submit("/api/v5/trade/order-algo", body, lambda: [s for s in self.stops(inst) if s["cid"] == cid])

    def cancel_stops(self, inst, ids):
        body = [{"algoId": i, "instId": inst} for i in ids]
        self._submit("/api/v5/trade/cancel-algos", body,
                     lambda: not any(s["algoId"] in ids for s in self.stops(inst)))


class Paper:
    """dry-run 거래소. st 는 호출자가 저장하는 dict (장부). 주문은 현재가 ± 슬리피지 2bps, taker 0.05%."""
    FEE, SLIP = 0.0005, 0.0002

    def __init__(self, market, st, cash=10_000.0, now=lambda: int(time.time() * 1000)):
        self.m, self.now = market, now
        self.st = st
        st.setdefault("cash", cash)
        for k in ("pos", "stops", "orders", "lever"):
            st.setdefault(k, {})
        st.setdefault("hist", [])

    def __getattr__(self, k):                     # instrument / ticker / candles / funding_history 는 공개 API 그대로
        return getattr(self.m, k)

    def config(self):
        return {"posMode": "net_mode"}

    def _upl(self, inst, p):
        return p["side"] * p["sz"] * self.instrument(inst)["ctVal"] * (self.ticker(inst)["last"] - p["avgPx"])

    def position(self, inst):
        p = self.st["pos"].get(inst)
        return {**p, "upl": self._upl(inst, p), "realizedPnl": p["fee"] + p["fundingFee"]} if p else None

    def balance(self):
        pos = self.st["pos"]
        eq = self.st["cash"] + sum(self._upl(i, p) + p["fee"] + p["fundingFee"] for i, p in pos.items())
        return eq, self.st["cash"] - sum(p["margin"] for p in pos.values())

    def positions_history(self, inst, since_ms):
        return [h for h in self.st["hist"] if h["inst"] == inst and h["uTime"] > since_ms]

    def set_leverage(self, inst, lever):
        self.st["lever"][inst] = lever

    def leverage_info(self, inst):
        return {"lever": float(self.st["lever"].get(inst, 0)), "mgnMode": "isolated"}

    def get_order(self, inst, cid):
        return self.st["orders"].get(cid)

    def stops(self, inst):
        return self.st["stops"].get(inst, [])

    def place_stop(self, inst, pos_side, sz, trigger, cid):
        self.st["stops"].setdefault(inst, []).append({"algoId": cid, "cid": cid, "sz": sz, "side": -pos_side,
                                                      "trigger": trigger})

    def cancel_stops(self, inst, ids):
        self.st["stops"][inst] = [s for s in self.stops(inst) if s["algoId"] not in ids]

    def _close(self, inst, px, t_ms, typ="2"):
        p = self.st["pos"].pop(inst)
        qty = p["sz"] * self.instrument(inst)["ctVal"]
        pnl, fee = p["side"] * qty * (px - p["avgPx"]), -self.FEE * qty * px
        realized = pnl + p["fee"] + fee + p["fundingFee"]
        self.st["cash"] += realized
        self.st["stops"].pop(inst, None)
        self.st["hist"].append({"inst": inst, "uTime": t_ms, "side": p["side"], "closeAvgPx": px, "realizedPnl": realized,
                                "fee": p["fee"] + fee, "fundingFee": p["fundingFee"], "type": typ})
        return fee

    def order(self, inst, side, sz, cid, reduce=False):
        if cid in self.st["orders"]:
            return self.st["orders"][cid]
        px = self.ticker(inst)["last"] * (1 + side * self.SLIP)
        now = self.now()
        if reduce:
            fee = self._close(inst, px, now)
        else:
            ct = self.instrument(inst)["ctVal"]
            fee, lv = -self.FEE * sz * ct * px, self.st["lever"].get(inst, rls.LEVER)
            # ponytail: 청산가 = 진입가 ∓ (1/lever − MMR) 단일 구간 근사. 실제는 OKX 유지증거금 티어·수수료 반영
            self.st["pos"][inst] = {"side": side, "sz": sz, "avgPx": px, "fee": fee, "fundingFee": 0.0,
                                    "margin": sz * ct * px / lv, "mgnMode": "isolated", "t": now, "lever": lv,
                                    "liqPx": px * (1 - side * (1 / lv - rls.MMR))}
        o = {"cid": cid, "state": "filled", "avgPx": px, "sz": sz, "fee": fee}
        self.st["orders"][cid] = o
        return o

    def sync(self):
        """보유 포지션마다 마지막 처리 이후의 확정 1H 봉을 순서대로: 펀딩 → 손절/강제청산 중 가까운 쪽
        (시가 갭이면 시가, 아니면 트리거가) 체결. 강제청산은 type 3, 손실은 증거금으로 상한."""
        # ponytail: 진입 봉(00:00~00:05)의 저가도 손절 판정에 쓴다. 3×ATR 거리라 오판 가능성은 무시할 수준.
        for inst, p in list(self.st["pos"].items()):
            ct = self.instrument(inst)["ctVal"]
            bars = self.candles(inst, p["t"] - p["t"] % 3_600_000)
            bars = bars[bars.confirm == 1]
            fund = self.funding_history(inst, p["t"] - 1)
            for b in bars.itertuples():
                for ft, rate in [f for f in fund if f[0] <= b.ts]:
                    p["fundingFee"] += rls.funding_pnl(p["side"], rate, p["sz"] * ct * b.open)
                fund = [f for f in fund if f[0] > b.ts]
                p["t"] = b.ts + 3_600_000
                st = next(iter(self.stops(inst)), None)
                trig, typ = p["liqPx"], "3"
                if st and (st["trigger"] - trig) * p["side"] >= 0:
                    trig, typ = st["trigger"], "2"
                if p["side"] * (b.low if p["side"] > 0 else b.high) <= p["side"] * trig:
                    px = min(b.open, trig) if p["side"] > 0 else max(b.open, trig)
                    if typ == "3":                                # 격리: 갭이어도 손실은 증거금까지
                        px = p["avgPx"] - p["side"] * p["margin"] / (p["sz"] * ct)
                    self._close(inst, px, int(b.ts), typ)
                    break
