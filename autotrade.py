"""BTC 단일 · 관망형 자동매매 봇 (업비트 KRW 현물).

구역 (model.py 가 매일 마지막 완성 일봉(09:00 KST 경계)으로 계산):
  long  = 강세 지표 합류(8개 중 7개↑) & 상승확률 p ≥ 0.55 & 약세 합류 아님
  short = 약세 지표 합류 & p ≤ 0.40   (0.40 인 근거는 model.py 주석)
  wait  = 그 외
포지션 상태기계 (flat / long) — 현물이라 숏 진입은 없고, 약세 구역은 '청산 · 진입 금지' 로만 쓴다:
  flat : long 구역 → 매수, 그 외 → 관망
  long : short 구역 → 즉시 청산
         HOLD_DAYS 지났고 long 구역 아님 → 청산 후 관망
사이징: 명목 = 자산 × POSITION_PCT (현물이라 레버리지 없음). 업비트 최소 주문 5,000원.

모의·실주문 연계 (MODE):
  paper : 모의 장부만 (키 없어도 됨)
  live  : 매 주문을 거래소에 보냄
  auto  : 모의 장부로 시작 → 모의 거래(진입→청산) LIVE_AFTER_PAPER_TRADES 회 완료되면 그 다음 주문부터 실주문   (기본)
  모의 장부(state 표)는 어느 모드에서든 항상 같은 판단으로 기록되어 대시보드에서 모의 vs 실계좌 곡선을 나란히 본다.
Claude 는 거부권만: 진입을 뉴스·거래소 사건·시장 급락으로 거부하고, 보유 중 사고면 강제 청산. 키 없으면 생략.

사용:  python autotrade.py --once     1회 실행
       python autotrade.py            매일 TRADE_TIME(KST) 실행
       python dashboard.py            대시보드 http://localhost:8000
"""
import contextlib
import datetime as dt
import json
import logging
import os
import pathlib
import sqlite3
import sys
import time
import zoneinfo

import anthropic
import requests
from dotenv import load_dotenv

import upbit as X
import model as M

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parent
DB_PATH = ROOT / "trading.db"
KST = zoneinfo.ZoneInfo("Asia/Seoul")
MODE = os.environ.get("MODE", "auto")                    # paper | live | auto
LIVE_AFTER = int(os.environ.get("LIVE_AFTER_PAPER_TRADES", 2))
HAVE_KEYS = bool(os.environ.get("UPBIT_ACCESS_KEY") and os.environ.get("UPBIT_SECRET_KEY"))
PAPER_CASH_KRW = float(os.environ.get("PAPER_CASH_KRW", 1000000))
TRADE_TIME = os.environ.get("TRADE_TIME", "09:05")        # 업비트 일봉 경계(09:00 KST) 직후 = 백테스트의 '다음날 시가' 체결
POSITION_PCT = float(os.environ.get("POSITION_PCT", 100)) / 100
CANDLES = 100
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5")
BASE_URL = os.environ.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com"
GATEWAY = "api.anthropic.com" not in BASE_URL
USE_CLAUDE = bool(os.environ.get("ANTHROPIC_API_KEY") or GATEWAY)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout),
                              logging.FileHandler(ROOT / "autotrade.log", encoding="utf-8")])
log = logging.getLogger("autotrade")

SCHEMA = {
    "type": "object",
    "properties": {
        "approve": {"type": "boolean"},
        "force_exit": {"type": "boolean"},
        "reason": {"type": "string"}},
    "required": ["approve", "force_exit", "reason"], "additionalProperties": False}


# ---------- DB ----------
@contextlib.contextmanager
def db():
    """호출마다 커넥션을 열고 반드시 닫는다 (닫지 않으면 매 사이클 핸들이 쌓인다)."""
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, mode TEXT,
        equity REAL, paper_equity REAL, price REAL, zone TEXT, p REAL, bull INTEGER, bear INTEGER, position TEXT,
        action TEXT, reason TEXT, status TEXT, model TEXT, input_tokens INTEGER, output_tokens INTEGER);
    CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, timestamp TEXT, mode TEXT,
        action TEXT, side TEXT, qty REAL, notional_krw REAL, price REAL, order_id TEXT, status TEXT, reason TEXT);
    CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK (id = 1), side TEXT, qty REAL, entry REAL,
        entered_at TEXT, cash REAL);""")
    conn.execute("INSERT OR IGNORE INTO state VALUES (1, NULL, 0, 0, NULL, ?)", (PAPER_CASH_KRW,))
    conn.commit()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def now():
    return dt.datetime.now(KST).isoformat(timespec="seconds")


def state():
    with db() as c:
        side, qty, entry, entered_at, cash = c.execute("SELECT side, qty, entry, entered_at, cash FROM state").fetchone()
    return {"side": side, "qty": qty, "entry": entry, "entered_at": entered_at, "cash": cash}


def set_state(**kw):
    with db() as c:
        c.execute(f"UPDATE state SET {', '.join(f'{k}=?' for k in kw)} WHERE id=1", list(kw.values()))


def paper_trades_done():
    """완료된 모의 거래(청산) 횟수 — auto 모드의 실주문 전환 기준."""
    with db() as c:
        return c.execute("SELECT COUNT(*) FROM orders WHERE action='close' AND mode='paper' AND status='paper'").fetchone()[0]


def live_now():
    return HAVE_KEYS and (MODE == "live" or (MODE == "auto" and paper_trades_done() >= LIVE_AFTER))


# ---------- 계좌 (모의 장부는 항상, 실계좌는 live 일 때) ----------
def account(ex, px):
    """{equity, pos, paper_equity, paper_pos}. pos = {side, qty, entry, pnl, entered_at, held_days, pnl_pct} | None"""
    st = state()
    paper_pos = None
    if st["side"]:
        sgn = 1 if st["side"] == "long" else -1
        paper_pos = {"side": st["side"], "qty": st["qty"], "entry": st["entry"], "pnl": sgn * (px - st["entry"]) * st["qty"]}
    paper_eq = st["cash"] + (paper_pos["pnl"] if paper_pos else 0)
    if ex:
        snap = X.snapshot(ex)                                          # 잔고·시세를 한 번에 (폴링 비용 절감)
        eq, pos = snap["equity"], snap["position"]
        if pos and (pos["side"] != st["side"] or not st["entered_at"]):   # 봇 밖에서 연 포지션 → 모의 장부도 맞추고 오늘 진입으로
            set_state(side=pos["side"], qty=pos["qty"], entry=pos["entry"], entered_at=now())
            st = state()
        elif pos:                                                          # 시장가 체결 수량·평단을 거래소 값으로 보정
            set_state(qty=pos["qty"], entry=pos["entry"])
        if not pos and st["side"]:                                         # 거래소는 비었는데 장부에 포지션 → 장부 정리
            set_state(side=None, qty=0, entry=0, entered_at=None)
            st, paper_pos = state(), None
    else:
        eq, pos = paper_eq, paper_pos
    if pos:
        pos["entered_at"] = st["entered_at"]
        pos["held_days"] = (dt.datetime.now(KST) - dt.datetime.fromisoformat(st["entered_at"])).days
        pos["pnl_pct"] = pos["pnl"] / eq * 100 if eq else 0.0
    return {"equity": eq, "pos": pos, "paper_equity": paper_eq, "paper_pos": paper_pos}


# ---------- Claude 거부권 ----------
def news():
    key = os.environ.get("SERPAPI_API_KEY")
    if not key:
        return []
    try:
        r = requests.get("https://serpapi.com/search.json", params={
            "engine": "google_news", "q": "bitcoin", "api_key": key}, timeout=15)
        r.raise_for_status()
        return [{"title": n.get("title"), "source": (n.get("source") or {}).get("name"), "date": n.get("date")}
                for n in r.json().get("news_results", [])[:10]]
    except Exception as e:  # noqa: BLE001
        log.warning("뉴스 실패: %s", e)
        return []


def ask_claude(payload):
    client = anthropic.Anthropic(timeout=300, max_retries=2)
    system = (ROOT / "instructions.md").read_text(encoding="utf-8")
    user = "\n\n".join(f"## {k}\n{json.dumps(v, ensure_ascii=False)}" for k, v in payload.items())
    user += "\n\n## 출력 형식\n아래 JSON 스키마를 만족하는 JSON 객체 하나만 출력한다.\n" + json.dumps(SCHEMA, ensure_ascii=False)
    common = dict(model=CLAUDE_MODEL, max_tokens=16000, system=system, messages=[{"role": "user", "content": user}])
    if GATEWAY:
        resp = client.messages.create(**common)
    elif "opus" in CLAUDE_MODEL or "fable" in CLAUDE_MODEL:
        resp = client.beta.messages.create(**common, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                                           output_config={"format": {"type": "json_schema", "schema": SCHEMA}})
    else:
        resp = client.messages.create(**common, output_config={"format": {"type": "json_schema", "schema": SCHEMA}})
    if resp.stop_reason == "refusal":
        raise RuntimeError(f"Claude 응답 거부: {resp.stop_details}")
    text = next(b.text for b in resp.content if b.type == "text")
    out, _ = json.JSONDecoder().raw_decode(text[text.find("{"):])
    return out, resp.usage.input_tokens, resp.usage.output_tokens


# ---------- 주문 ----------
def execute(ex, run_id, action, side, qty, notional, px, reason):
    """action: open|close. 모의 장부는 항상 정산하고, ex 가 있으면 실주문도 보낸다."""
    mode = "live" if ex else "paper"
    row = {"run_id": run_id, "timestamp": now(), "mode": mode, "action": action, "side": side, "qty": qty,
           "notional_krw": notional, "price": px, "reason": reason}
    try:
        st, fee = state(), notional * X.TAKER_FEE
        if action == "open":
            row["qty"] = row["qty"] or notional / px
            set_state(side=side, qty=row["qty"], entry=px, cash=st["cash"] - fee, entered_at=now())
        elif st["side"]:
            sgn = 1 if st["side"] == "long" else -1
            set_state(side=None, qty=0, entry=0, entered_at=None, cash=st["cash"] + sgn * (px - st["entry"]) * st["qty"] - fee)
        if ex:
            if action == "open":
                row["order_id"], row["qty"] = X.open_position(ex, side, notional, px)
                set_state(qty=row["qty"])
            else:
                row["order_id"] = X.close_position(ex, {"side": side, "qty": qty})
            row["status"] = "submitted"
        else:
            row["status"] = "paper"
        log.info("[%s] %s %s %.8f %s (%s원) @%s — %s", row["status"], action, side, row["qty"], X.COIN,
                 f"{notional:,.0f}", f"{px:,.0f}", reason)
    except Exception as e:  # noqa: BLE001
        row["status"] = f"error: {e}"[:200]
        log.error("주문 실패 %s %s: %s", action, side, e)
    with db() as c:
        c.execute(f"INSERT INTO orders ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
    return row["status"] in ("submitted", "paper")


# ---------- 한 사이클 ----------
def run_cycle():
    live = live_now()
    mode = "실주문" if live else f"모의 ({paper_trades_done()}/{LIVE_AFTER} 완료 후 실주문)" if MODE == "auto" and HAVE_KEYS else "모의"
    with db() as c:
        run_id = c.execute("INSERT INTO runs (timestamp, mode, status, model) VALUES (?, ?, 'running', ?)",
                           (now(), mode, CLAUDE_MODEL)).lastrowid
    log.info("=== run %d 시작 (%s · %s) ===", run_id, mode, "Claude" if USE_CLAUDE else "Claude 없음")
    try:
        ex = X.client() if live else None
        pub = ex or X.public()
        m = M.load()
        df = X.candles(pub, CANDLES).iloc[:-1]
        sig = M.predict(m, df)
        px = X.price(pub)
        acc = account(ex, px)
        pos, z = acc["pos"], sig.zone
        log.info("%s %s원 · 구역 %s (p %.2f, bull %d, bear %d, 20일 %+.1f%%) · 자산 %s원 (모의 %s) · 포지션 %s",
                 X.COIN, f"{px:,.0f}", z, sig.p, sig.bull, sig.bear, sig.ret_20d, f"{acc['equity']:,.0f}", f"{acc['paper_equity']:,.0f}",
                 f"{pos['side']} {pos['held_days']}일 손익 {pos['pnl_pct']:+.1f}%" if pos else "없음")

        # 1) 상태기계
        action, reason = "hold", ""
        if pos:
            if z == "short":
                action, reason = "close", f"약세 합류 (p {sig.p:.2f}) → 청산 (현물이라 숏 진입은 없음)"
            elif pos["held_days"] >= M.HOLD_DAYS and z != "long":
                action, reason = "close", f"{pos['held_days']}일 보유 후 long 구역 아님({z}) → 관망"
            else:
                reason = f"보유 유지 ({pos['held_days']}일째, 구역 {z})"
        elif z == "long":
            action, reason = "open", f"long 구역 p {sig.p:.2f}, bull {sig.bull}/8, bear {sig.bear}/8"
        else:
            reason = f"{z} 구역 (p {sig.p:.2f}) → 관망"

        # 2) Claude 거부권
        tok = (0, 0)
        if USE_CLAUDE and (action == "open" or pos):
            d = M.add_indicators(df.copy()).tail(15)
            payload = {"signal": {"zone": z, "p": round(float(sig.p), 3), "bull": int(sig.bull), "bear": int(sig.bear),
                                  "ret_5d": round(float(sig.ret_5d), 1), "ret_20d": round(float(sig.ret_20d), 1),
                                  "proposed_action": action, "target_side": "long" if action == "open" else None},
                       "position": {"side": pos["side"], "held_days": pos["held_days"], "pnl_pct": round(pos["pnl_pct"], 1)} if pos else None,
                       "market": {"today_pct": round(px / df.close.iloc[-1] * 100 - 100, 1)},
                       "daily": [{"date": t.strftime("%m-%d"), "close": round(r.close), "chg_pct": round(r.close / r.open * 100 - 100, 1),
                                  "vol_krw_억": round(r.close * r.volume / 1e8), "rsi": round(r.RSI_14)} for t, r in d.iterrows()],
                       "news": news()}
            out, *tok = ask_claude(payload)
            log.info("Claude: approve=%s force_exit=%s — %s", out["approve"], out["force_exit"], out["reason"])
            if pos and out["force_exit"]:
                action, reason = "close", "Claude 강제 청산: " + out["reason"]
            elif action == "open" and not out["approve"]:
                action, reason = "hold", "Claude 거부: " + out["reason"]

        # 3) 주문: 청산 먼저, 진입 나중
        if action == "close":
            if execute(ex, run_id, "close", pos["side"], pos["qty"], pos["qty"] * px, px, reason):
                acc = account(ex, px)
        if action == "open":
            execute(ex, run_id, "open", "long", None, acc["equity"] * POSITION_PCT, px, reason)
            acc = account(ex, px)
        with db() as c:
            c.execute("UPDATE runs SET status='done', equity=?, paper_equity=?, price=?, zone=?, p=?, bull=?, bear=?, position=?, "
                      "action=?, reason=?, input_tokens=?, output_tokens=? WHERE id=?",
                      (acc["equity"], acc["paper_equity"], px, z, float(sig.p), int(sig.bull), int(sig.bear),
                       pos["side"] if pos else None, action, reason, tok[0], tok[1], run_id))
        log.info("=== run %d 완료: %s — %s ===", run_id, action, reason)
    except Exception as e:  # noqa: BLE001
        log.exception("run %d 실패: %s", run_id, e)
        with db() as c:
            c.execute("UPDATE runs SET status=? WHERE id=?", (f"error: {e}"[:300], run_id))


def confirm_live():
    """실주문이 나갈 수 있는 설정이면 사람이 직접 '실주문' 을 치게 한다. autotrade·dashboard 공용."""
    if not (HAVE_KEYS and MODE != "paper"):
        return
    msg = f"실주문 가능 모드입니다 (MODE={MODE}{', 모의 %d회 완료 후 자동 전환' % LIVE_AFTER if MODE == 'auto' else ''}). '실주문' 을 입력하면 계속합니다: "
    if input(msg).strip() != "실주문":
        sys.exit("취소")


def schedule_forever():
    """매일 TRADE_TIME(KST) 에 한 사이클. 대시보드는 이걸 백그라운드 스레드로 돌린다."""
    h, mn = map(int, TRADE_TIME.split(":"))
    while True:
        n = dt.datetime.now(KST)
        nxt = n.replace(hour=h, minute=mn, second=0, microsecond=0)
        if nxt <= n:
            nxt += dt.timedelta(days=1)
        log.info("다음 실행 %s", nxt.isoformat(timespec="minutes"))
        time.sleep((nxt - n).total_seconds())
        run_cycle()


def manual_order(action):
    """대시보드에서 사람이 직접 누른 실주문. auto 모드의 '모의 N회' 게이트와 무관하게 바로 나간다.
    자동 매매와 같은 execute() 를 타므로 주문 기록·장부가 그대로 이어진다."""
    if not HAVE_KEYS:
        raise ValueError("업비트 API 키가 없습니다")
    if MODE == "paper":
        raise ValueError("MODE=paper 에서는 실주문을 보내지 않습니다 (.env 의 MODE 를 auto 나 live 로)")
    ex = X.client()
    snap = X.snapshot(ex)
    px, pos = snap["price"], snap["position"]
    if action == "buy" and pos:
        raise ValueError(f"이미 {snap['coin_qty']:.8f} {X.COIN} 보유 중입니다")
    if action == "sell" and not pos:
        raise ValueError("보유 중인 코인이 없습니다")
    if action == "buy" and snap["krw"] * POSITION_PCT < X.MIN_ORDER_KRW:
        raise ValueError(f"주문 가능 원화 {snap['krw']:,.0f}원 < 최소 {X.MIN_ORDER_KRW:,}원")
    with db() as c:
        run_id = c.execute("INSERT INTO runs (timestamp, mode, status, price, position, action, reason) "
                           "VALUES (?, '수동', 'done', ?, ?, ?, ?)",
                           (now(), px, pos["side"] if pos else None, "open" if action == "buy" else "close",
                            "대시보드 수동 주문")).lastrowid
    if action == "buy":
        ok = execute(ex, run_id, "open", "long", None, snap["krw"] * POSITION_PCT, px, "대시보드 수동 매수")
    else:
        ok = execute(ex, run_id, "close", pos["side"], pos["qty"], pos["qty"] * px, px, "대시보드 수동 매도")
    if not ok:
        with db() as c:
            raise RuntimeError(c.execute("SELECT status FROM orders WHERE run_id=? ORDER BY id DESC LIMIT 1",
                                         (run_id,)).fetchone()[0])
    return {"action": action, "price": px, "run_id": run_id}


if __name__ == "__main__":
    confirm_live()
    run_cycle()
    if "--once" not in sys.argv:
        schedule_forever()
