"""OKX 데모(모의투자)에서 보호주문 설계(protect.py)의 가정을 확인하는 절차. **데모 서버 전용 — 실계좌로는 돌지 않는다.**

사용:
  python demo_protect.py            계획과 준비 상태만 출력 (네트워크 없음)
  python demo_protect.py run        데모 계좌에서 실행 — 0.01계약(≈10 USDT) 롱을 두 번 열고 닫는다. 5~25분.
  python demo_protect.py run 40     발동 대기 시간을 40분으로 (기본 20분)
준비: .env 에 OKX_DEMO_API_KEY · OKX_DEMO_SECRET · OKX_DEMO_PASSPHRASE (okx.com > 모의투자 > API 에서 발급, 거래 권한 포함).
      데모 계좌 USDT 50 이상. 실계좌 키·.env 의 MODE/OKX_DEMO 값은 쓰지도 바꾸지도 않는다.
안전장치:
  · 데모 헤더(x-simulated-trading=1)와 sandboxMode 를 확인하기 전에는 어떤 비공개 호출도 하지 않는다.
  · 시작할 때 데모 계좌에 BTC-USDT-SWAP 포지션이나 봇 보호주문('p' 접두)이 있으면 손대지 않고 멈춘다.
  · 끝날 때(실패·Ctrl+C 포함) 봇 보호주문 취소 → 포지션 reduceOnly 시장가 청산 → flat 확인.
  · trading.db · autotrade 를 부르지 않는다. 결과는 화면 + demo_protect_result.txt(덧붙이기).
  · 데모 계좌의 BTC-USDT-SWAP 을 격리·1배·net 으로 설정한다 (okx.setup — 봇과 같은 설정).

확인할 것 (각 줄 = 관찰 → protect.py 설계에 주는 의미):
  Q1 closeFraction=1 · oco · 시장가(−1) · reduceOnly 를 net·격리에서 받는가. 거부면 오류 코드 → 설계 수정 필요.
  Q2 같은 포지션에 두 번째 전량 TP/SL 을 거부하는가. 받으면 OKX 는 중복을 안 막는다 → ensure 의 개수 검사가 유일한 방어.
  Q3 발동된 알고의 state · actualSide · ordIdList, 자식 주문을 fetch_order 로 읽는가(outcome), 발동가 대비 체결 미끄러짐.
  Q4 봇이 시장가로 청산한 뒤 남은 전량 TP/SL 을 OKX 가 자동 취소하는가. 아니면 ensure 의 flat 정리가 필수.
  Q5 포지션이 없을 때 reduceOnly 청산의 반응(예외 코드 / 체결 0 상태) — 감사 수정 ② 'rejected' 판정의 전제. 숏이 안 생기는지.
  Q6 fetch_balance total(eq) 에 격리 포지션 미실현손익이 들어가는가 — 사고 차단기(day_loss_hit) 분모.
  Q7 clOrdId 로 주문 재조회(okx.find_order)가 되는가.
한계: 발동·미끄러짐은 표본 1건이다. 부분 체결은 만들 수 없다(시장가 0.01계약). 데모 호가 깊이는 실계좌와 다를 수 있다.
"""
import datetime as dt
import os
import pathlib
import sys
import time
import uuid

import okx as X
import protect as P
import strategy as S

OUT = pathlib.Path(__file__).resolve().parent / "demo_protect_result.txt"
LINES = []


def log(tag, msg):
    s = f"[{tag}] {msg}"
    print(s, flush=True)
    LINES.append(s)


def cid(prefix="d"):
    return prefix + uuid.uuid4().hex[:20]


def guard(ex):
    if ex.headers.get("x-simulated-trading") != "1" or not ex.options.get("sandboxMode"):
        sys.exit("데모 서버가 아닙니다 — 아무것도 하지 않고 중단합니다")


def tiny_open(ex, wait):
    """최소 수량 롱 → find_order 결과 (Q7). 명목은 최소 주문 금액의 1.3배 → 계약 수 내림으로 0.01~0.02계약."""
    px, c = X.price(ex), cid()
    X.open_position(ex, "long", max(X.MIN_ORDER, px * 0.0001) * 1.3, px, c)
    got = None
    for _ in range(5):
        got = X.find_order(ex, c)
        if got and got["filled"] > 0:
            break
        wait(1)
    return got, c


def flat(ex, wait):
    """정리: 봇 보호주문 취소 → 포지션 청산 → flat 확인."""
    try:
        P.cancel(ex, P.pending(ex)[0])
    except Exception as e:                                 # noqa: BLE001
        log("정리", f"보호주문 취소 실패: {X.explain(e)}")
    pos = X.position(ex)
    if pos:
        X.close_position(ex, pos, cid())
        wait(2)
    pos, left = X.position(ex), P.pending(ex)[0]
    log("정리", "flat · 봇 보호주문 0개" if not pos and not left else f"**정리 안 됨** 포지션 {pos} · 보호주문 {len(left)}개 — 데모 화면에서 직접 확인")


def usdt_detail(ex):
    try:
        for d in ex.fetch_balance()["info"]["data"][0]["details"]:
            if d.get("ccy") == "USDT":
                return {k: d.get(k) for k in ("eq", "cashBal", "upl", "isoEq", "availBal", "frozenBal")}
    except Exception:                                      # noqa: BLE001
        pass
    return None


def run(ex, wait=time.sleep, fire_minutes=20):
    guard(ex)
    log("시작", f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} · {X.SYMBOL} · 데모")
    cfg = ex.private_get_account_config()["data"][0]
    snap = X.snapshot(ex)
    log("D0", f"posMode {cfg.get('posMode')} · USDT {snap['equity']:,.2f} (가용 {snap['cash']:,.2f}) · 포지션 {snap['position']}")
    if snap["position"] or P.pending(ex)[0]:
        sys.exit("데모 계좌에 BTC-USDT-SWAP 포지션이나 봇 보호주문이 이미 있습니다 — 데모 화면에서 정리한 뒤 다시 실행하세요")
    if snap["cash"] < 50:
        sys.exit(f"데모 가용 USDT {snap['cash']:,.2f} < 50")
    X.setup(ex, 1)
    try:
        # ── 진입 · Q7 · Q6 ──
        got, _ = tiny_open(ex, wait)
        log("Q7", f"clOrdId 재조회 {'됨' if got else '안 됨'} · {got}")
        pos = X.position(ex)
        if not pos:
            log("D1", "진입 뒤 포지션이 안 보인다 — 여기서 멈춘다")
            return
        det, snap = usdt_detail(ex), X.snapshot(ex)
        if det:
            f = {k: float(v or 0) for k, v in det.items()}
            log("Q6", f"원본 {det} · total={snap['equity']:.4f} · cashBal+upl={f['cashBal'] + f['upl']:.4f} · 미실현 {pos['pnl']:+.4f}"
                      f" → total 이 미실현을 {'포함' if abs(snap['equity'] - f['cashBal'] - f['upl']) < 1e-6 and f['upl'] else '포함하는지 불명(upl 0 이거나 불일치)'}")
        else:
            log("Q6", "잔고 원본(details)을 못 읽었다")
        # ── Q1 수락 ──
        try:
            pc = P.place(ex, pos["entry"])
            mine = P.pending(ex)[0]
            up, dn = S.levels(pos["entry"])
            log("Q1", f"수락 · 목록 {len(mine)}개 · 익절 {mine[0]['tpTriggerPx'] if mine else '-'} (기대 {up:,.1f}) · "
                      f"손절 {mine[0]['slTriggerPx'] if mine else '-'} (기대 {dn:,.1f})")
        except Exception as e:                             # noqa: BLE001
            log("Q1", f"**거부** {X.explain(e)} → protect.place 설계 수정 필요. 나머지 단계 생략")
            return
        # ── Q2 중복 ──
        try:
            P.place(ex, pos["entry"])
            log("Q2", f"두 번째도 수락됨 (목록 {len(P.pending(ex)[0])}개) → OKX 는 중복을 막지 않는다. ensure 의 개수 검사가 유일한 방어")
        except Exception as e:                             # noqa: BLE001
            log("Q2", f"두 번째 거부 {X.explain(e)} → 설계 가정대로 (바꿀 때는 취소 → 다시 걸기)")
        ok, why = P.ensure(ex, X.position(ex))
        log("D2", f"ensure → {ok} · {why} · 목록 {len(P.pending(ex)[0])}개")
        # ── Q4 봇 청산 뒤 자동 취소 ──
        X.close_position(ex, X.position(ex), cid())
        wait(3)
        left = P.pending(ex)[0]
        log("Q4", f"봇 시장가 청산 3초 뒤 봇 보호주문 {len(left)}개 → "
                  + ("OKX 가 자동 취소한다 (ensure 정리는 이중 안전)" if not left else "자동 취소 안 함 → ensure 의 flat 정리가 필수"))
        ok, why = P.ensure(ex, None)
        log("D3", f"flat ensure → {ok} · {why}")
        # ── Q5 flat 에서 reduceOnly ──
        c5 = cid()
        try:
            X.close_position(ex, {"side": "long", "qty": 0.0001}, c5)
            log("Q5", f"예외 없음 · 재조회 {X.find_order(ex, c5)}")
        except Exception as e:                             # noqa: BLE001
            log("Q5", f"예외 {X.explain(e)} · 재조회 {X.find_order(ex, c5)}")
        log("Q5", f"그 뒤 포지션 {X.position(ex)} (None 이어야 한다 — 숏 없음)")
        # ── Q3 발동 (좁은 문턱 ±0.08%) ──
        got, _ = tiny_open(ex, wait)
        pos, last = X.position(ex), X.price(ex)
        up, dn = last * 1.0008, last * 0.9992
        pc = P.place(ex, pos["entry"], levels=(up, dn))
        log("Q3", f"발동 대기 — 현재 {last:,.1f} · 익절 {up:,.1f} / 손절 {dn:,.1f} · 최대 {fire_minutes}분")
        a = None
        for _ in range(fire_minutes * 6):
            wait(10)
            a = ex.private_get_trade_order_algo({"algoClOrdId": pc})["data"][0]
            if a.get("state") != "live":
                break
        if a and a.get("state") != "live":
            o = P.outcome(ex, pc)
            trig = up if o["side"] == "tp" else dn if o["side"] == "sl" else None
            slip = f"{(o['px'] / trig - 1) * 100:+.3f}%" if o["px"] and trig else "계산 불가"
            log("Q3", f"state {a.get('state')} · actualSide {a.get('actualSide')!r} · ordIdList {a.get('ordIdList')} · 체결 평단 {o['px']}"
                      f" · 발동가 대비 {slip} · 포지션 {X.position(ex)}")
        else:
            log("Q3", f"{fire_minutes}분 동안 발동 안 함 — 'python demo_protect.py run 40' 처럼 늘려서 다시")
    finally:
        flat(ex, wait)
        with OUT.open("a", encoding="utf-8") as f:
            f.write("\n".join(LINES) + "\n\n")
        print(f"\n결과를 {OUT.name} 에 덧붙였습니다")


if __name__ == "__main__":
    print(__doc__)
    missing = [k for k in X.DEMO_KEYS if not os.environ.get(k)]
    print(f"준비 상태: 데모 키 {'없음 — ' + ', '.join(missing) + ' 를 .env 에 넣으세요' if missing else '있음'}")
    if sys.argv[1:2] == ["run"]:
        if missing:
            sys.exit("데모 키가 없어 실행하지 않습니다")
        run(X.client(demo=True), fire_minutes=int(sys.argv[2]) if len(sys.argv) > 2 else 20)
