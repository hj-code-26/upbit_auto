"""OKX 주문 실행 — 연구용. **실거래 차단 (LIVE_ALLOWED=False)**: 진짜 ccxt 객체를 넘기면 생성자에서 거부한다. 가짜 거래소(테스트)만 쓴다.
운영 봇(autotrade.py) 과 무관하다. 매매 판단에 LLM 을 쓰지 않는다.

  python research/okx_range_reversion/executor.py check    읽기 전용 점검 — 계약 규격·계정 모드·레버리지·수수료·기존 포지션/주문 (set_* 호출 없음)

규약
  · 수량: 거래소로 가는 sz 는 항상 '계약 수' (BTC-USDT-SWAP 1계약 = ctVal × ctMult BTC). BTC 수량 변환은 btc()/cts() 두 곳뿐.
  · 모든 주문은 clOrdId(TAG 접두) 를 '보내기 전에' 상태 파일에 적는다. 네트워크 오류·타임아웃 = 'unknown' — 실패가 아니다.
    unknown 인 동안 새 주문 금지, 같은 주문 재전송 금지. clOrdId 로 조회해 결말을 확인한 뒤에만 다음 행동 (새 clOrdId).
  · 청산·익절·손절은 전부 reduceOnly (Net 모드에서 포지션 감소만 — 반대 포지션으로 뒤집히지 않는다).
  · 손절 = 조건부 알고 주문 1개, 수량 = 거래소가 보고한 실제 포지션 수량. 부분 체결·부분 익절 뒤 sync() 가 수량을 맞추고 다시 읽어 검증한다.
  · 체결 중복 처리 방지: tradeId 를 본 것만 기록 (fills). 포지션 크기는 체결 합산이 아니라 거래소 positions 가 진실.
  · 재시작: recover() — 미확인 주문 조회 → 거래소 포지션·일반/조건부 주문 확인 → 손절 복구. 주인 모를 포지션·주문이 있으면 정지.
  · 설정(계정 모드·포지션 모드·레버리지·마진 모드)이 기대와 다르면 바꾸지 않고 정지·보고한다.
  · API 키는 출금 권한 없이. 로그·상태 파일에 비밀값을 남기지 않는다 (scrub).
"""
import json
import math
import os
import pathlib
import sys
import time
import uuid

import ccxt

INST = "BTC-USDT-SWAP"
TAG = "rr"                        # 이 전략이 낸 주문 식별 접두 (clOrdId·algoClOrdId)
LIVE_ALLOWED = False              # 실거래 스위치. 바꾸려면 사용자 승인 + 코드 수정 (환경변수로는 못 켠다)
ABSENT_AFTER = 30.0               # 초. 조회해서 '없음' 이 이 시간 넘게 계속되면 미도달로 본다
KEYS = ("OKX_API_KEY", "OKX_SECRET", "OKX_PASSPHRASE")


class Halt(RuntimeError):
    """신규 진입을 멈추고 사람이 봐야 하는 상태."""


def scrub(s):
    s = str(s)
    for k in KEYS:
        v = os.environ.get(k)
        if v and len(v) >= 4:
            s = s.replace(v, "***")
    return s


def spec(ex):
    """계약 규격을 거래소에서 읽고 전제(USDT 선형·BTC 표시·거래 가능)를 검사한다."""
    d = ex.public_get_public_instruments({"instType": "SWAP", "instId": INST})["data"][0]
    s = dict(ct_val=float(d["ctVal"]), ct_mult=float(d["ctMult"]), ct_type=d["ctType"], ct_ccy=d["ctValCcy"],
             settle=d["settleCcy"], lot=float(d["lotSz"]), min_sz=float(d["minSz"]), tick=float(d["tickSz"]),
             max_mkt=float(d["maxMktSz"]), state=d["state"])
    bad = [k for k, want in (("ct_type", "linear"), ("settle", "USDT"), ("ct_ccy", "BTC"), ("state", "live")) if s[k] != want]
    if bad:
        raise Halt(f"{INST} 규격이 전제와 다르다: " + ", ".join(f"{k}={s[k]}" for k in bad))
    return s


def btc(ct, s):
    return ct * s["ct_val"] * s["ct_mult"]


def cts(qty_btc, s):
    """BTC → 계약 수, lot 단위 내림."""
    return round(math.floor(qty_btc / (s["ct_val"] * s["ct_mult"]) / s["lot"] + 1e-9) * s["lot"], 8)


def fsz(x):
    return f"{x:.8f}".rstrip("0").rstrip(".")


def preflight(ex, s, lever):
    """읽기 전용 점검 → 문제 목록 (비어 있어야 진행). 어떤 설정도 바꾸지 않는다."""
    bad = []
    c = ex.private_get_account_config({})["data"][0]
    if str(c.get("acctLv")) not in ("2", "3", "4"):
        bad.append(f"계정 모드 acctLv={c.get('acctLv')} (1=현물 모드: 무기한 선물 거래 불가) — OKX 웹에서 사용자가 바꿔야 함")
    if c.get("posMode") != "net_mode":
        bad.append(f"포지션 모드 {c.get('posMode')} ≠ net_mode")
    if "withdraw" in str(c.get("perm", "")):
        bad.append("API 키에 출금 권한이 있다 — 출금 권한 없는 키로 바꿀 것")
    li = ex.private_get_account_leverage_info({"instId": INST, "mgnMode": "isolated"})["data"]
    lv = {str(x.get("lever")) for x in li}
    if lv != {str(lever)}:
        bad.append(f"격리 레버리지 설정 {sorted(lv)} ≠ {lever} — 자동 변경하지 않음")
    for p in ex.private_get_account_positions({"instType": "SWAP", "instId": INST})["data"]:
        if float(p.get("pos") or 0):
            bad.append(f"기존 포지션 {p.get('pos')}계약 ({p.get('mgnMode')}, {p.get('lever')}배) — Net 모드에선 같은 상품에 두 전략을 못 돌린다")
    foreign = [o for o in ex.private_get_trade_orders_pending({"instType": "SWAP", "instId": INST})["data"]
               if not str(o.get("clOrdId", "")).startswith(TAG)]
    for t in ("conditional", "oco", "trigger", "move_order_stop"):
        foreign += [a for a in ex.private_get_trade_orders_algo_pending({"instType": "SWAP", "instId": INST, "ordType": t})["data"]
                    if not str(a.get("algoClOrdId", "")).startswith(TAG)]
    if foreign:
        bad.append(f"이 전략이 내지 않은 대기 주문 {len(foreign)}개")
    return bad


class Exec:
    def __init__(self, ex, s, path, lever=3):
        if not LIVE_ALLOWED and not getattr(ex, "fake", False):
            raise Halt("실거래 차단 (executor.LIVE_ALLOWED=False)")
        self.ex, self.s, self.lever, self.path = ex, s, lever, pathlib.Path(path)
        self.st = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else \
            {"orders": {}, "fills": [], "trade": None, "halt": None, "funding": {}, "log": []}

    # ── 기록 ──
    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.st, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)                                  # 원자적 교체 — 쓰다 죽어도 이전 파일이 남는다

    def log(self, msg):
        self.st["log"] = (self.st["log"] + [f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {scrub(msg)}"])[-500:]

    def halt(self, why):
        self.st["halt"] = scrub(why)
        self.log("정지: " + why)
        self.save()
        raise Halt(why)

    # ── 주문 ──
    def unresolved(self):
        return [c for c, o in self.st["orders"].items() if o["status"] in ("sending", "unknown")]

    def send(self, kind, side, sz, ordType="market", px=None, reduce=False):
        if self.unresolved():
            raise Halt(f"미확인 주문 {self.unresolved()} — 조회·대사 전 새 주문 금지")
        cid = TAG + uuid.uuid4().hex[:24]
        body = {"instId": INST, "tdMode": "isolated", "side": side, "ordType": ordType, "sz": fsz(sz), "clOrdId": cid}
        if px is not None:
            body["px"] = f"{px:.1f}"
        if reduce:
            body["reduceOnly"] = "true"
        o = self.st["orders"][cid] = {"kind": kind, "status": "sending", "ts": time.time(), "sz": sz, "filled": 0.0, "side": side}
        self.save()                                                 # 보내기 전에 의도부터 남긴다
        try:
            d = self.ex.private_post_trade_order(body)["data"][0]
            if str(d.get("sCode", "0")) != "0":
                raise ccxt.InvalidOrder(f"{d.get('sCode')}: {d.get('sMsg')}")
            o.update(status="live", ordId=d.get("ordId"))
        except ccxt.NetworkError as e:                              # 타임아웃 포함 — 도착했을 수도 있다
            o["status"] = "unknown"; self.log(f"{kind} {cid} 응답 없음 → 조회 대기 ({type(e).__name__})")
        except ccxt.ExchangeError as e:
            o["status"] = "rejected"; o["err"] = scrub(e)[:200]; self.log(f"{kind} {cid} 거부 {o['err']}")
        self.save()
        return cid

    def resolve(self, cid):
        """clOrdId 로 거래소 주문 상태를 읽어 기록을 맞춘다. 재전송하지 않는다."""
        o = self.st["orders"][cid]
        try:
            d = self.ex.private_get_trade_order({"instId": INST, "clOrdId": cid})["data"][0]
        except ccxt.OrderNotFound:
            if o["status"] in ("sending", "unknown") and time.time() - o["ts"] > ABSENT_AFTER:
                o["status"] = "absent"; self.log(f"{cid} 거래소에 없음 → 미도달 확정")
            self.save()
            return o["status"]
        except ccxt.NetworkError:
            return o["status"]
        o["filled"] = float(d.get("accFillSz") or 0)
        o["avg"] = float(d.get("avgPx") or 0) or None
        o["ordId"] = d.get("ordId")
        o["status"] = {"live": "live", "partially_filled": "partial", "filled": "filled"}.get(d.get("state"), "canceled")
        self.save()
        return o["status"]

    def cancel(self, cid):
        """취소 경합: 취소 요청의 성패와 상관없이 마지막엔 조회 결과가 진실이다 (그새 체결됐을 수 있다)."""
        try:
            self.ex.private_post_trade_cancel_order({"instId": INST, "clOrdId": cid})
        except (ccxt.ExchangeError, ccxt.NetworkError) as e:
            self.log(f"취소 {cid} 응답 {type(e).__name__}: {scrub(e)[:120]} → 조회로 확인")
        return self.resolve(cid)

    # ── 거래소 상태 ──
    def position(self):
        """부호 있는 계약 수 (+롱 / −숏) 와 원자료."""
        rows = [p for p in self.ex.private_get_account_positions({"instType": "SWAP", "instId": INST})["data"] if float(p.get("pos") or 0)]
        if len(rows) > 1:
            self.halt("Net 모드인데 포지션 행이 여러 개")
        return (float(rows[0]["pos"]), rows[0]) if rows else (0.0, None)

    def my_algos(self):
        rows = self.ex.private_get_trade_orders_algo_pending({"instType": "SWAP", "instId": INST, "ordType": "conditional"})["data"]
        return [a for a in rows if str(a.get("algoClOrdId", "")).startswith(TAG)]

    def poll_fills(self):
        """새 체결만 반영 (tradeId 로 중복 제거). 이 전략 주문이 아닌 체결은 세지 않는다 — 포지션 대사가 잡는다."""
        seen, new = set(self.st["fills"]), []
        for f in sorted(self.ex.private_get_trade_fills({"instType": "SWAP", "instId": INST})["data"], key=lambda f: int(f["ts"])):
            if f["tradeId"] in seen:
                continue
            seen.add(f["tradeId"]); self.st["fills"].append(f["tradeId"])
            new.append(f)
        self.st["fills"] = self.st["fills"][-2000:]
        for cid in {f.get("clOrdId") for f in new} & set(self.st["orders"]):
            self.resolve(cid)
        self.save()
        return new

    # ── 손절 보호 ──
    def sync(self):
        """불변식: 포지션이 있으면 이 전략의 조건부 손절 정확히 1개, 수량 = |포지션|, 발동가 = 기록된 손절가.
        포지션이 없으면 0개. 맞춘 뒤 다시 읽어 검증 → True/False (False 면 호출자는 신규 진입을 막고 청산을 시도)."""
        pos, _ = self.position()
        t, algos = self.st["trade"], self.my_algos()
        if pos == 0:
            if algos:
                self.ex.private_post_trade_cancel_algos([{"instId": INST, "algoId": a["algoId"]} for a in algos])
            for cid, o in self.st["orders"].items():
                if o["status"] in ("live", "partial") and o["kind"] != "entry":
                    self.cancel(cid)
            ok = not self.my_algos()
            if ok and t and not self.unresolved():
                self.log(f"거래 종료 {t['id']}"); self.st["trade"] = None
            self.save()
            return ok
        if not t:
            self.halt(f"주인 모를 포지션 {pos}계약 — 수동 확인")
        if (pos > 0) != (t["side"] > 0):
            self.halt(f"포지션 방향 {pos} 이 기록({t['side']})과 반대")
        want_sz, want_px, side = abs(pos), float(t["stop"]), "sell" if pos > 0 else "buy"
        if len(algos) > 1:
            self.ex.private_post_trade_cancel_algos([{"instId": INST, "algoId": a["algoId"]} for a in algos[1:]])
            algos = algos[:1]
        try:
            if not algos:
                self.ex.private_post_trade_order_algo({
                    "instId": INST, "tdMode": "isolated", "side": side, "ordType": "conditional", "sz": fsz(want_sz),
                    "reduceOnly": "true", "slTriggerPx": f"{want_px:.1f}", "slOrdPx": "-1", "slTriggerPxType": "last",
                    "algoClOrdId": TAG + uuid.uuid4().hex[:24]})
            elif abs(float(algos[0]["sz"]) - want_sz) > 1e-9 or abs(float(algos[0]["slTriggerPx"]) - want_px) > self.s["tick"] / 2:
                try:
                    self.ex.private_post_trade_amend_algos({"instId": INST, "algoId": algos[0]["algoId"], "newSz": fsz(want_sz),
                                                            "newSlTriggerPx": f"{want_px:.1f}", "newSlOrdPx": "-1"})
                except ccxt.BaseError as e:                         # 정정 실패 → 새로 걸고 옛것 취소 (무방비 구간 최소화: 걸기 먼저)
                    self.log(f"손절 정정 실패 {scrub(e)[:120]} → 재설정")
                    self.ex.private_post_trade_order_algo({
                        "instId": INST, "tdMode": "isolated", "side": side, "ordType": "conditional", "sz": fsz(want_sz),
                        "reduceOnly": "true", "slTriggerPx": f"{want_px:.1f}", "slOrdPx": "-1", "slTriggerPxType": "last",
                        "algoClOrdId": TAG + uuid.uuid4().hex[:24]})
                    self.ex.private_post_trade_cancel_algos([{"instId": INST, "algoId": algos[0]["algoId"]}])
        except ccxt.BaseError as e:
            self.log(f"손절 설정 실패 {scrub(e)[:160]}"); self.save()
            return False
        a = self.my_algos()
        ok = len(a) == 1 and abs(float(a[0]["sz"]) - want_sz) < 1e-9 and abs(float(a[0]["slTriggerPx"]) - want_px) <= self.s["tick"] / 2
        self.log(f"손절 {'확인' if ok else '검증 실패'} {want_sz}계약 @ {want_px}")
        self.save()
        return ok

    # ── 거래 단계 ──
    def enter(self, side, ct, stop, mid, meta=None):
        """side +1/−1, ct = 계약 수. 기록 → 시장가 → (호출자가 poll_fills·sync 반복)."""
        if self.st["halt"]:
            raise Halt("정지 상태: " + self.st["halt"])
        if self.st["trade"]:
            raise Halt("진행 중 거래가 있다 — 동시 포지션 1개")
        if self.unresolved():
            raise Halt(f"미확인 주문 {self.unresolved()} — 조회·대사 전 새 주문 금지")
        if ct < self.s["min_sz"] or ct > self.s["max_mkt"]:
            raise Halt(f"계약 수 {ct} 범위 밖 (최소 {self.s['min_sz']}, 시장가 최대 {self.s['max_mkt']})")
        self.st["trade"] = {"id": uuid.uuid4().hex[:8], "side": side, "ct": ct, "stop": stop, "mid": mid, "meta": meta or {}}
        cid = self.send("entry", "buy" if side > 0 else "sell", ct)
        self.st["trade"]["entry"] = cid
        if self.st["orders"][cid]["status"] == "rejected":
            self.st["trade"] = None
        self.save()
        return cid

    def take_profit(self, frac, px):
        """중앙 익절 지정가 (reduceOnly). 수량 = 실제 포지션 × frac, lot 내림. 최소 미만이면 전량."""
        pos, _ = self.position()
        if not pos:
            return None
        sz = cts(btc(abs(pos), self.s) * frac, self.s)
        if sz < self.s["min_sz"] or abs(pos) - sz < self.s["min_sz"]:
            sz = abs(pos)
        tick = self.s["tick"]
        px = math.ceil(px / tick) * tick if pos > 0 else math.floor(px / tick) * tick   # 목표보다 불리하게 걸지 않는다
        cid = self.send("tp", "sell" if pos > 0 else "buy", sz, "limit", px, reduce=True)
        self.st["trade"]["tp"] = cid
        self.save()
        return cid

    def move_stop(self, new_stop):
        """추적 손절 — 단조 강화만 허용 (롱은 올리기만, 숏은 내리기만). 손절선 확대는 거부."""
        t = self.st["trade"]
        if (new_stop - t["stop"]) * t["side"] <= 0:
            return False
        t["stop"] = new_stop
        return self.sync()

    def close(self, reason):
        """잔여 전량 시장가 reduceOnly. 대기 중 익절은 먼저 취소하고 결과를 조회로 확인한 뒤 잔여 수량을 다시 읽는다."""
        for cid, o in list(self.st["orders"].items()):
            if o["status"] in ("live", "partial") and o["kind"] == "tp":
                self.cancel(cid)
        pos, _ = self.position()
        if not pos:
            return None
        self.log(f"청산 {reason} {pos}계약")
        return self.send("close", "sell" if pos > 0 else "buy", abs(pos), reduce=True)

    def day_guard(self, equity, day_start, limit=0.01):
        """일일 손실 한도 정리 절차: ① 신규 진입 정지 ② 대기 익절 취소(조회 확인) ③ 잔여 시장가 reduceOnly
        ④ 손절 주문은 flat 이 확인될 때까지 유지 (sync 가 그 뒤 치운다). 손절선을 넓히거나 증거금을 넣지 않는다."""
        if equity > day_start * (1 - limit):
            return False
        self.st["halt"] = f"일일 손실 한도 {equity:.2f}/{day_start:.2f}"
        self.log(self.st["halt"])
        self.close("일손실")
        self.save()
        return True

    # ── 재시작·감시 ──
    def recover(self):
        """재시작 직후 가장 먼저. 거래소가 진실이다."""
        for cid in [c for c, o in self.st["orders"].items() if o["status"] in ("sending", "unknown", "live", "partial")]:
            self.resolve(cid)
        foreign = [o for o in self.ex.private_get_trade_orders_pending({"instType": "SWAP", "instId": INST})["data"]
                   if not str(o.get("clOrdId", "")).startswith(TAG)]
        if foreign:
            self.halt(f"이 전략이 내지 않은 대기 주문 {len(foreign)}개")
        self.poll_fills()
        return self.sync()

    def risk_check(self, liq_buf_min=0.05):
        """Mark Price·청산가·유지증거금 → 신규 진입 가능 여부와 수치."""
        pos, row = self.position()
        if not pos:
            return True, {}
        mark, liq = float(row.get("markPx") or 0), float(row.get("liqPx") or 0)
        buf = abs(mark - liq) / mark if mark and liq else float("inf")
        info = {"mark": mark, "liq": liq, "buf": buf, "mgnRatio": row.get("mgnRatio"), "mmr": row.get("mmr"), "lever": row.get("lever")}
        if str(row.get("mgnMode")) != "isolated" or str(row.get("lever")) != str(self.lever):
            self.halt(f"포지션 설정이 기대와 다름 {row.get('mgnMode')}/{row.get('lever')}배")
        return buf >= liq_buf_min, info

    def funding(self):
        """다음 정산 시각·주기(추정하지 않고 거래소 값의 차이)·현재 예상률, 그리고 정산 기록(bills type 8) 을 billId 로 중복 없이 적립."""
        d = self.ex.public_get_public_funding_rate({"instId": INST})["data"][0]
        nxt, cur = int(d["nextFundingTime"]), int(d["fundingTime"])
        for b in self.ex.private_get_account_bills({"instType": "SWAP", "type": "8"})["data"]:
            if b.get("instId") == INST:
                self.st["funding"][b["billId"]] = float(b["balChg"])
        self.save()
        return {"rate": float(d["fundingRate"]), "at": cur, "interval_h": (nxt - cur) / 3.6e6, "paid_total": sum(self.st["funding"].values())}


def check():
    """읽기 전용. 주문·설정 변경 없음."""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
    import okx as X                                                   # 키 로딩(.env)만 빌린다
    ex = X.client()
    s = spec(ex)
    print(f"{INST}: 1계약 = {s['ct_val'] * s['ct_mult']} {s['ct_ccy']} · {s['ct_type']} · 정산 {s['settle']} · "
          f"lot {s['lot']} · 최소 {s['min_sz']}계약 · tick {s['tick']} · 시장가 최대 {s['max_mkt']}계약 · {s['state']}")
    fee = ex.private_get_account_trade_fee({"instType": "SWAP", "instFamily": "BTC-USDT"})["data"][0]
    print(f"수수료 (음수 = 낸다): taker {fee.get('takerU')} · maker {fee.get('makerU')}")
    fr = ex.public_get_public_funding_rate({"instId": INST})["data"][0]
    print(f"펀딩: 현재 {float(fr['fundingRate']):+.6f} · 다음 정산 주기 {(int(fr['nextFundingTime']) - int(fr['fundingTime'])) / 3.6e6:.0f}h")
    bad = preflight(ex, s, 3)
    print("점검 통과" if not bad else "진행 불가:\n  - " + "\n  - ".join(bad))


if __name__ == "__main__" and "check" in sys.argv:
    try:
        check()
    except Exception as e:                                            # noqa: BLE001
        sys.exit(scrub(e))
