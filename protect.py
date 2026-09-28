"""거래소 보호주문(TP/SL) — 설계 + 초안 (2026-09-28 감사). **autotrade 에 아직 연결하지 않았다 (사용자 승인 대기).**
시험: python test_protect.py (가짜 거래소, 네트워크 없음).

왜: 봇은 5분봉이 닫힌 15초 뒤에야 익절·손절을 보고 시장가로 판다. research/aoa/exec_model.py 에서 평균 손실은 작지만
    (거래당 −0.06~−0.25%p) 꼬리가 −12% (2021-09-07), 진입 봉 건너뛰기(2b) 뒤로는 진입 직후 최대 ~10분 무방비다.

무엇을 거는가: OKX 알고 주문 한 개 — ordType=oco · closeFraction=1 · reduceOnly · 발동 기준 last(백테스트 OHLC 와 같은 체결가 계열)
    익절 = 진입가 × 1.04 발동 → 시장가 · 손절 = 진입가 × 0.94 발동 → 시장가 (strategy.levels 그대로).
  · 진입가는 **실제 체결 평단** (find_order 의 avg). 주문 전 시세로 걸면 미끄러진 만큼 문턱이 어긋난다.
  · closeFraction=1 은 '그때의 포지션 전량' 을 닫는다 → 부분 체결·수동 추가에도 수량이 맞고, reduceOnly 라 숏으로 뒤집히지 않는다.
    OKX 는 한 포지션에 이런 전량 TP/SL 을 하나만 허용한다 → 바꿀 때는 '취소 → 다시 걸기' (그 사이 수백 ms 무방비).
  · 봇이 건 것만 algoClOrdId 접두 'p' 로 구분해 건드린다. 사람이 건 알고 주문은 세기만 하고 취소하지 않는다.

불변식 (매 실주문 사이클 ensure() 가 강제한다):
  롱이 있으면 → '지금 진입가 기준' 보호주문 정확히 1개.  없거나 문턱이 다르거나 2개 이상이면 전부 취소하고 새로 건다.
  롱이 없으면 → 봇 보호주문 0개.  남은 것은 취소하고, 취소가 확인 안 되면 **신규 진입 금지**
                (closeFraction 주문이 살아 있으면 다음 포지션을 옛 문턱으로 닫는다).

연결 계획 (승인 뒤 적용할 diff — 지금은 적용 안 함):
  1. autotrade.execute(open) 이 체결을 확인한 직후 place(ex, 평단). 실패하면 CRITICAL 만 — 다음 사이클 ensure 가 다시 건다.
     건 보호주문은 orders 표에 action='protect' · client_id=algoClOrdId · price=진입가 행으로 남긴다 (스키마 변경 없음).
  2. autotrade._run_cycle (live): account() 뒤 ensure(ex, acc['pos']).  flat 인데 ok=False → act 에 신규 진입 차단을 넘긴다.
  3. 봇이 스스로 청산할 때(만기 · 봉 마감 판정 · 사고 차단기 · 수동 매도): **청산 먼저, 취소 나중** (취소 먼저면 청산 실패 시 무방비).
     남은 보호주문은 다음 ensure 가 치운다. 동시에 거래소 TP/SL 이 먼저 나가도 봇의 청산은 reduceOnly 라 0 이 되고
     (감사 수정 ②: 체결 0 = rejected → 재시도 → 대사로 flat 확인) 숏이 생기지 않는다.
  4. account() 에서 '거래소 flat · 장부 롱' 이면 outcome(ex, 마지막 protect 행의 algoClOrdId) 로 거래소 청산을 확인하고
     실제 체결 평단으로 close 행을 남긴다 (지금은 기록 없이 장부만 지운다).
  5. 봇의 봉 마감 청산 판정(exit_check)은 **그대로 둔다** — 보호주문이 안 걸렸거나 거래소가 발동을 놓친 경우의 예비선.
  모의(paper) 모드는 ex=None 이라 이 모듈을 전혀 부르지 않는다.

실계좌에서 확인해야 하는 것 (mock 으로는 알 수 없다 · OKX 데모는 2026-09-28 제외, 확인 도구 없음):
  · closeFraction=1 + oco + 시장가(−1) 조합이 net·격리에서 받아지는지 (51327~51330 오류 계열)
  · 포지션이 봇 시장가로 닫힌 뒤 남은 closeFraction 주문을 OKX 가 자동 취소하는지 (자동이어도 ensure 는 그대로 둔다)
  · 발동된 알고의 자식 주문(ordIdList)을 fetch_order 로 읽을 수 있는지, actualSide 값('tp'/'sl')
  · 발동 → 체결까지의 실제 미끄러짐 (exec_model 의 s 시나리오 0~0.3% 와 비교)
"""
import uuid

import okx as X
import strategy as S

TAG = "p"


def _inst(ex):
    return X.market(ex)["id"]


def _ok(resp):
    d = (resp.get("data") or [{}])[0]
    if str(d.get("sCode", resp.get("code"))) != "0":
        raise RuntimeError(f"OKX 보호주문 거부 {d.get('sCode') or resp.get('code')}: {d.get('sMsg') or resp.get('msg')}")
    return d


def _near(a, b):
    return abs(float(a) / b - 1) < 1e-5                    # 가격 정밀도(0.1) 반올림 오차만 허용


def place(ex, entry):
    """진입가(실제 평단) 기준 익절·손절 OCO 를 건다 → algoClOrdId. 거래소가 거부하면 예외."""
    up, dn = S.levels(entry)
    cid = TAG + uuid.uuid4().hex[:20]
    _ok(ex.private_post_trade_order_algo({
        "instId": _inst(ex), "tdMode": "isolated", "side": "sell", "ordType": "oco",
        "closeFraction": "1", "reduceOnly": "true", "algoClOrdId": cid,
        "tpTriggerPx": ex.price_to_precision(X.SYMBOL, up), "tpOrdPx": "-1", "tpTriggerPxType": "last",
        "slTriggerPx": ex.price_to_precision(X.SYMBOL, dn), "slOrdPx": "-1", "slTriggerPxType": "last"}))
    return cid


def pending(ex):
    """이 심볼에 살아 있는 OCO 알고 주문 → (봇 것, 남의 것)."""
    rows = ex.private_get_trade_orders_algo_pending({"instType": "SWAP", "instId": _inst(ex), "ordType": "oco"}).get("data") or []
    mine = [a for a in rows if str(a.get("algoClOrdId") or "").startswith(TAG)]
    return mine, len(rows) - len(mine)


def cancel(ex, algos):
    if algos:
        ex.private_post_trade_cancel_algos([{"algoId": a["algoId"], "instId": _inst(ex)} for a in algos])


def ensure(ex, pos):
    """불변식 강제 → (ok, 설명). ok=False 이고 pos 가 없으면 호출자는 신규 진입을 막는다.
    pos 가 있는데 ok=False 면 봇의 봉 마감 청산 판정이 예비선이다 (다음 사이클에 다시 시도)."""
    note = ""
    try:
        mine, foreign = pending(ex)
        note = f" · 사람이 건 알고 {foreign}개(건드리지 않음)" if foreign else ""
        if pos:
            up, dn = S.levels(pos["entry"])
            if len(mine) == 1 and _near(mine[0]["tpTriggerPx"], up) and _near(mine[0]["slTriggerPx"], dn):
                return True, "보호주문 유지" + note
            cancel(ex, mine)                               # 없음 · 문턱 틀림 · 중복 → 전부 치우고 하나만
            place(ex, pos["entry"])
            mine, _ = pending(ex)
            return len(mine) == 1, f"보호주문 {'재설정' if len(mine) == 1 else '확인 실패'} (익절 {up:,.1f} / 손절 {dn:,.1f})" + note
        if mine:
            cancel(ex, mine)
            left, _ = pending(ex)
            return not left, ("남은 보호주문 정리" if not left else f"남은 보호주문 {len(left)}개 취소 실패 — 신규 진입 금지") + note
        return True, "보호주문 없음" + note
    except Exception as e:                                 # noqa: BLE001 — 조회·주문 실패는 ok=False 로 돌려준다
        return False, f"보호주문 처리 실패: {X.explain(e)}" + note


def outcome(ex, cid):
    """봇이 건 보호주문의 결말 → {state, side('tp'|'sl'|None), px(실제 체결 평단|None)}.
    가격을 '손절가였을 것' 으로 가정하지 않는다 — 갭이면 실제 체결가가 손절가보다 한참 아래다."""
    d = ex.private_get_trade_order_algo({"algoClOrdId": cid})["data"][0]
    px = None
    for oid in d.get("ordIdList") or []:
        px = float(ex.fetch_order(oid, X.SYMBOL).get("average") or 0) or None
    return {"state": d.get("state"), "side": d.get("actualSide") or None, "px": px}
