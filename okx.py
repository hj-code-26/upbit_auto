"""OKX v5 REST — USDT 무기한, long/short(헤지) 포지션 모드, 격리(isolated) 전용.
헤지 모드라 같은 상품의 롱·숏이 따로 존재하고, 시각 분할 몫마다 부분 수량 손절을 따로 건다.
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
PS = {1: "long", -1: "short"}
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

    def stock_perps(self):
        """OKX 주식 USDT 무기한 (instCategory=3, state=live, -USDT-SWAP) → {기초명: instId}."""
        return {x["instId"][:-len("-USDT-SWAP")]: x["instId"] for x in self.get("/api/v5/public/instruments", {"instType": "SWAP"})
                if x.get("instCategory") == "3" and x.get("state") == "live" and x["instId"].endswith("-USDT-SWAP")}

    def tier1_mmr(self, inst):
        """격리 유지증거금 티어 1 의 MMR."""
        d = self.get("/api/v5/public/position-tiers", {"instType": "SWAP", "tdMode": "isolated",
                                                       "instFamily": inst.removesuffix("-SWAP")})
        return float(next(x for x in d if x["tier"] == "1")["mmr"])

    def announcements(self):
        """최근 OKX 공지 [(pTime ms, 제목)] (기업 이벤트 확인용, 첫 페이지)."""
        d = self.get("/api/v5/support/announcements", {"page": 1})
        return [(int(x["pTime"]), x["title"]) for page in d for x in page.get("details", [])]

    def ticker(self, inst):
        d = self.get("/api/v5/market/ticker", {"instId": inst})[0]
        return {"last": float(d["last"]), "open_utc0": float(d["sodUtc0"])}

    def candles(self, inst, since_ms, bar="1H"):
        """since_ms 이후 봉 오름차순 DataFrame [ts, open, high, low, close, confirm]. bar = 1H | 1Dutc."""
        rows, after = [], None
        while True:
            p = {"instId": inst, "bar": bar, "limit": 100}
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
        """(USDT 자산 eq [격리 증거금·미실현 손익 포함], 가용 USDT, 실현 잔고 USDT = eq − upl).
        위험 판정·사이징 모두 USDT 범위. totalEq(USD, 전 통화 합)는 다른 자산 가격을 섞으므로 쓰지 않는다."""
        d = self.get("/api/v5/account/balance", {"ccy": "USDT"}, private=True)[0]
        u = next((x for x in d.get("details", []) if x["ccy"] == "USDT"), {})
        n = lambda k: float(u.get(k) or 0)  # noqa: E731
        return n("eq"), n("availBal"), n("eq") - n("upl")

    def transfers(self, since_ms):
        """since_ms 이후 거래 계정 USDT 이체(입출금) [(ts ms, billId, balChg)] (bills type 1 = Transfer, 최근 7일)."""
        d = self.get("/api/v5/account/bills", {"ccy": "USDT", "type": "1", "limit": 100}, private=True)
        return sorted((int(x["ts"]), x["billId"], float(x.get("balChg") or 0)) for x in d if int(x["ts"]) > since_ms)

    def positions(self, inst):
        """{1: 롱, -1: 숏} 각 {sz 계약, avgPx, upl, fee, fundingFee, margin, mgnMode, lever, liqPx}"""
        out = {}
        for p in self.get("/api/v5/account/positions", {"instType": "SWAP", "instId": inst}, private=True):
            sz = float(p.get("pos") or 0)
            if sz:
                n = lambda k: float(p.get(k) or 0)  # noqa: E731
                side = {"long": 1, "short": -1}.get(p.get("posSide"), 1 if sz > 0 else -1)
                out[side] = {"sz": abs(sz), "avgPx": n("avgPx"), "upl": n("upl"), "fee": n("fee"),
                             "fundingFee": n("fundingFee"), "margin": n("margin") or n("imr"),
                             "mgnMode": p.get("mgnMode"), "lever": n("lever"), "liqPx": n("liqPx")}
        return out

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

    def set_leverage(self, inst, lever, side):
        body = {"instId": inst, "lever": str(lever), "mgnMode": "isolated", "posSide": PS[side]}
        self._retry(lambda: self._call("POST", "/api/v5/account/set-leverage", body=body, private=True), "set-leverage")

    def leverage_info(self, inst):
        """거래소에 실제 적용된 격리 레버리지 {1: 롱, -1: 숏, "mgnMode"}."""
        d = self.get("/api/v5/account/leverage-info", {"instId": inst, "mgnMode": "isolated"}, private=True)
        out = {"mgnMode": d[0]["mgnMode"] if d else None}
        for x in d:
            out[{"long": 1, "short": -1}.get(x.get("posSide"), 0)] = float(x["lever"])
        return out

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

    def order(self, inst, pos_side, sz, cid, close=False):
        """시장가. pos_side 포지션(±1)을 열거나(close=False) 줄인다(close=True). 같은 cid 는 다시 보내지 않는다."""
        if not self.get_order(inst, cid):
            buy = (pos_side > 0) != close
            body = {"instId": inst, "tdMode": "isolated", "side": "buy" if buy else "sell", "posSide": PS[pos_side],
                    "ordType": "market", "sz": fmt(sz), "clOrdId": cid}
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
                 "pos_side": {"long": 1, "short": -1}.get(x.get("posSide"), 0),
                 "trigger": float(x.get("slTriggerPx") or 0)} for x in d]

    def place_stop(self, inst, pos_side, sz, trigger, cid):
        """pos_side 포지션의 sz 계약만 닫는 stop-market (거래소 측 조건부 주문 → 봇이 죽어도 남는다)."""
        body = {"instId": inst, "tdMode": "isolated", "side": "sell" if pos_side > 0 else "buy", "posSide": PS[pos_side],
                "ordType": "conditional", "sz": fmt(sz), "algoClOrdId": cid,
                "slTriggerPx": fmt(trigger), "slOrdPx": "-1", "slTriggerPxType": "last"}
        self._submit("/api/v5/trade/order-algo", body, lambda: [s for s in self.stops(inst) if s["cid"] == cid])

    def cancel_stops(self, inst, ids):
        body = [{"algoId": i, "instId": inst} for i in ids]
        self._submit("/api/v5/trade/cancel-algos", body,
                     lambda: not any(s["algoId"] in ids for s in self.stops(inst)))

    def algo(self, inst, cid):
        """손절 주문 상태 {state: live|effective|canceled|order_failed..., px: 체결가(없으면 트리거가)} | None"""
        try:
            d = self.get("/api/v5/trade/order-algo", {"algoClOrdId": cid}, private=True)
        except OKXError:
            return None
        if not d:
            return None
        x = d[0]
        return {"state": x["state"], "px": float(x.get("actualPx") or x.get("slTriggerPx") or 0)}


class Paper:
    """dry-run 거래소 (헤지 모드). st 는 호출자가 저장하는 dict (장부). 시장가 = 현재가 ± 슬리피지 2bps, taker 0.05%.
    수수료·펀딩은 즉시 현금(실현 잔고)에 반영한다."""
    FEE, SLIP = 0.0005, 0.0002

    def __init__(self, market, st, cash=10_000.0, now=lambda: int(time.time() * 1000)):
        self.m, self.now = market, now
        self.st = st
        st.setdefault("cash", cash)
        for k in ("pos", "stops", "orders", "lever", "algos"):
            st.setdefault(k, {})
        st.setdefault("hist", [])

    def __getattr__(self, k):                     # instrument / ticker / candles / tier1_mmr 등 공개 API 그대로
        return getattr(self.m, k)

    def config(self):
        return {"posMode": "long_short_mode"}

    def _ct(self, inst):
        return self.instrument(inst)["ctVal"]

    def _side(self, inst, side):
        return self.st["pos"].get(inst, {}).get(str(side))

    def positions(self, inst):
        last = self.ticker(inst)["last"]
        return {int(k): {**p, "upl": int(k) * p["sz"] * self._ct(inst) * (last - p["avgPx"])}
                for k, p in self.st["pos"].get(inst, {}).items()}

    def balance(self):
        upl = sum(p["upl"] for i in self.st["pos"] for p in self.positions(i).values())
        margin = sum(p["margin"] for ps in self.st["pos"].values() for p in ps.values())
        return self.st["cash"] + upl, self.st["cash"] - margin, self.st["cash"]

    def positions_history(self, inst, since_ms):
        return [h for h in self.st["hist"] if h["inst"] == inst and h["uTime"] > since_ms]

    def transfers(self, since_ms):
        return [tuple(x) for x in self.st.get("transfers", []) if x[0] > since_ms]

    def set_leverage(self, inst, lever, side):
        self.st["lever"].setdefault(inst, {})[str(side)] = lever

    def leverage_info(self, inst):
        return {"mgnMode": "isolated", **{int(k): float(v) for k, v in self.st["lever"].get(inst, {}).items()}}

    def get_order(self, inst, cid):
        return self.st["orders"].get(cid)

    def stops(self, inst):
        return [dict(s) for s in self.st["stops"].get(inst, [])]

    def place_stop(self, inst, pos_side, sz, trigger, cid):
        self.st["stops"].setdefault(inst, []).append({"algoId": cid, "cid": cid, "sz": sz, "pos_side": pos_side,
                                                      "trigger": trigger})
        self.st["algos"][cid] = {"state": "live", "px": trigger}

    def cancel_stops(self, inst, ids):
        for s in self.st["stops"].get(inst, []):
            if s["algoId"] in ids:
                self.st["algos"][s["cid"]] = {"state": "canceled", "px": s["trigger"]}
        self.st["stops"][inst] = [s for s in self.st["stops"].get(inst, []) if s["algoId"] not in ids]

    def algo(self, inst, cid):
        return self.st["algos"].get(cid)

    def _reduce(self, inst, side, sz, px, t_ms, typ="2"):
        """side 포지션 sz 계약을 px 에 닫는다. 수수료는 현금에서, 손익은 현금으로. 남은 수량 없으면 포지션·남은 손절 정리."""
        p = self._side(inst, side)
        sz = min(sz, p["sz"])
        q = sz * self._ct(inst)
        if typ == "3":                                           # 격리 강제청산: 손실 = 그 수량의 증거금
            pnl, fee = -p["margin"] * sz / p["sz"], 0.0
        else:
            pnl, fee = side * q * (px - p["avgPx"]), -self.FEE * q * px
        self.st["cash"] += pnl + fee
        p["margin"] *= 1 - sz / p["sz"]
        p["sz"] = round(p["sz"] - sz, 10)
        self.st["hist"].append({"inst": inst, "uTime": t_ms, "side": side, "closeAvgPx": px, "realizedPnl": pnl + fee,
                                "fee": fee, "fundingFee": 0.0, "type": typ if p["sz"] <= 1e-12 else ("4" if typ == "3" else "1")})
        if p["sz"] <= 1e-12:
            del self.st["pos"][inst][str(side)]
            for s in [s for s in self.st["stops"].get(inst, []) if s["pos_side"] == side]:
                self.cancel_stops(inst, [s["algoId"]])
        return fee

    def order(self, inst, pos_side, sz, cid, close=False):
        if cid in self.st["orders"]:
            return self.st["orders"][cid]
        buy = (pos_side > 0) != close
        px = self.ticker(inst)["last"] * (1 + (1 if buy else -1) * self.SLIP)
        now = self.now()
        if close:
            if not self._side(inst, pos_side):
                raise OKXError("51169", "no position in this direction")     # 헤지 모드: 청산 주문은 반대 포지션을 만들지 않는다
            sz = min(sz, self._side(inst, pos_side)["sz"])
            fee = self._reduce(inst, pos_side, sz, px, now)
        else:
            ct, lv = self._ct(inst), self.st["lever"].get(inst, {}).get(str(pos_side), rls.LEVER)
            fee = -self.FEE * sz * ct * px
            self.st["cash"] += fee
            p = self.st["pos"].setdefault(inst, {}).setdefault(str(pos_side), {
                "sz": 0.0, "avgPx": px, "fee": 0.0, "fundingFee": 0.0, "margin": 0.0, "mgnMode": "isolated",
                "t": now, "lever": lv})
            p["avgPx"] = (p["avgPx"] * p["sz"] + px * sz) / (p["sz"] + sz)
            p["sz"] += sz
            p["margin"] += sz * ct * px / lv
            p["fee"] += fee
            # ponytail: 청산가 = 평단 ∓ (1/lever − 티어1 MMR − taker) 단일 구간 근사. 실제 티어는 데모에서 확인
            p["liqPx"] = p["avgPx"] * (1 - pos_side * rls.liq_dist(lv, self.tier1_mmr(inst)))
        o = {"cid": cid, "state": "filled", "avgPx": px, "sz": sz, "fee": fee}
        self.st["orders"][cid] = o
        return o

    def sync(self):
        """보유 포지션(상품·방향)마다 마지막 처리 이후의 확정 1H 봉을 순서대로: 펀딩 → 손절들(가까운 순) → 강제청산.
        트리거를 넘으면 시가 갭이면 시가, 아니면 트리거가에 그 손절 수량만 체결."""
        # ponytail: 진입 봉(HH:00~HH:02)의 고저도 손절 판정에 쓴다. 2분 구간이라 오판 가능성은 작다. 1분봉이면 정확.
        for inst, sides in list(self.st["pos"].items()):
            for k in list(sides):
                side, p = int(k), sides[k]
                ct = self._ct(inst)
                bars = self.candles(inst, p["t"] - p["t"] % 3_600_000)
                bars = bars[bars.confirm == 1]
                fund = self.funding_history(inst, p["t"] - 1)
                for b in bars.itertuples():
                    if str(side) not in self.st["pos"].get(inst, {}):
                        break
                    for ft, rate in [f for f in fund if f[0] <= b.ts]:
                        f = rls.funding_pnl(side, rate, p["sz"] * ct * b.open)
                        p["fundingFee"] += f
                        self.st["cash"] += f
                    fund = [f for f in fund if f[0] > b.ts]
                    p["t"] = b.ts + 3_600_000
                    adverse = b.low if side > 0 else b.high
                    for s in sorted([s for s in self.stops(inst) if s["pos_side"] == side], key=lambda s: -side * s["trigger"]):
                        if side * adverse <= side * s["trigger"] and str(side) in self.st["pos"].get(inst, {}):
                            px = min(b.open, s["trigger"]) if side > 0 else max(b.open, s["trigger"])
                            self.cancel_stops(inst, [s["algoId"]])
                            self.st["algos"][s["cid"]] = {"state": "effective", "px": px}
                            self._reduce(inst, side, s["sz"], px, int(b.ts))
                    p = self.st["pos"].get(inst, {}).get(str(side))
                    if p and side * adverse <= side * p["liqPx"]:
                        self._reduce(inst, side, p["sz"], p["liqPx"], int(b.ts), "3")
