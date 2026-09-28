"""BTC 단일 · 급락 매수 자동매매 봇 (OKX USDT 무기한 선물, 격리 마진, 롱 전용). 규칙은 strategy.py 한 곳에 있다.

전략 (2026-09-27 교체 — 옛 '일봉 국면 + 4h 돌파' 는 커밋 d29f29f 에 보존):
  진입 : 완성된 5분봉 종가가 1h EMA 대비 −2% 이하 & 전날 일봉 종가가 50·200일선 위 → 롱
  청산 : 진입가 +4% 익절 / −6% 손절 / 3일 만기
  근거 : research_aoa.txt (워뇨띠 BitMEX 체결 분석 → 급락 매수 → 50·200일선 → 1배 판정)
매 사이클(INTERVAL_MIN=5, 5분봉 마감 15초 뒤)에 지난 사이클 이후 완성된 5분봉을 **전부** 훑는다:
  포지션이 있으면 그 봉들의 고가·저가로 익절·손절·만기를 확인하고(백테스트와 같은 판정) 닿았으면 청산,
  없으면 **마지막 완성 봉** 하나만 신호로 본다 — 봇이 꺼져 있던 동안의 지난 신호로 뒤늦게 사지 않는다.
  모의 장부는 익절·손절가에 체결한 것으로 적는다(백테스트와 같다). 실주문은 확인 즉시 시장가로 청산한다.
  ponytail: 실주문 청산은 봉 마감 뒤 확인이라 최대 5분 늦다 — 실계좌 전환 전에 거래소 TP/SL 주문 부착을 검토할 것.
사이징: 명목 = 자산 × POSITION_PCT × LEVERAGE × (1 − CASH_RESERVE). 레버리지 1배 (2배는 사전등록 불합격, 코드 상한 2).

모의 3단계 (MODE):
  paper : 모의 장부만. 키가 있으면 실계좌 잔고는 읽어서 기록·표시만 한다 (주문 없음)   ← 교체 직후 기본
  live  : 매 주문을 OKX 실계좌에 보냄 (OKX 데모는 2026-09-28 제외)
  auto  : 모의 장부로 시작 → 모의 거래 LIVE_AFTER_PAPER_TRADES 회 완료 뒤 거래소 주문
사고 차단기 (MAX_DAY_LOSS_PCT, 기본 15%): 24시간 고점 대비 그만큼 빠지면 청산하고 자동실행을 끈다.
  1배 롱이 손절 −6% 를 두고 하루에 −15% 를 잃는다면 손절이 작동하지 않은 것 — 버그·급변을 잡는 값이다. 재개는 make on.
Claude 검토 (CLAUDE_BASE_URL): 주문 직전에만 호출. 알고리즘 값을 캔들과 대조하고 사건·급변이면 거부 (instructions.md).
자동실행 온오프: autorun.off 가 있으면 신규 진입을 멈춘다. 포지션이 남아 있으면 청산 확인은 계속 돈다 (PROTECT_WHEN_OFF).

사용:  python autotrade.py --once     1회 실행
       python autotrade.py            5분마다 실행
       python dashboard.py            대시보드 http://localhost:8000
"""
import contextlib
import datetime as dt
import json
import logging
import os
import pathlib
import socket
import sqlite3
import sys
import threading
import time
import uuid
import zoneinfo

import anthropic
import pandas as pd
from dotenv import load_dotenv

import engine as E
import okx as X
import strategy as S

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parent
DB_PATH = ROOT / "trading.db"
KST = zoneinfo.ZoneInfo("Asia/Seoul")
AUTORUN_OFF = ROOT / "autorun.off"                       # 있으면 신규 진입 안 함 (보호·대사는 계속)
ENTRY_OFF = ROOT / "entry.off"                           # 신규 진입만 막는 별도 스위치 (사고 차단기가 켠다)
PROTECT_WHEN_OFF = os.environ.get("PROTECT_WHEN_OFF", "1") != "0"
                          # 자동실행을 껐어도 포지션이 남아 있으면 청산·대사 사이클은 계속 돈다.
                          # 2026-09-10 감사: '신규 진입 정지' 와 '기존 포지션 보호' 는 다른 스위치다.
MODE = os.environ.get("MODE", "paper")                   # paper | live | auto
LIVE_AFTER = int(os.environ.get("LIVE_AFTER_PAPER_TRADES", 2))
HAVE_KEYS = all(os.environ.get(k) for k in ("OKX_API_KEY", "OKX_SECRET", "OKX_PASSPHRASE"))
PAPER_CASH = float(os.environ.get("PAPER_CASH", 1000))   # 모의 장부 시작 자산 (USDT)
INTERVAL_MIN = int(os.environ.get("INTERVAL_MIN", 5))    # 5분봉 전략이라 5분. 늘리면 그 사이 봉의 신호를 놓친다(청산 확인은 따라잡는다)
POSITION_PCT = float(os.environ.get("POSITION_PCT", 100)) / 100
CASH_RESERVE = float(os.environ.get("CASH_RESERVE_PCT", 1)) / 100   # 명목에서 떼어 두는 여유 (수수료·슬리피지·펀딩)
LEVERAGE = float(os.environ.get("LEVERAGE", 1))           # 모의 장부·거래소 공통 (okx.setup 이 진입 직전 설정)
MAX_LEVERAGE = 2                    # 코드가 막는 상한. research/aoa/dip_lev2.py: 2배도 BitMEX 2018-21 MDD −63% 로
                                    # 사전등록 기준(−40%) 불합격이라 1배로 운용한다. 2 초과는 근거가 전혀 없다.
if LEVERAGE > MAX_LEVERAGE:                               # LLM 검토를 마지막 방어선으로 두지 않는다
    sys.exit(f".env 의 LEVERAGE={LEVERAGE:g} 가 상한 {MAX_LEVERAGE}배를 넘습니다. 낮추고 다시 실행하세요.")
MAX_DAY_LOSS = float(os.environ.get("MAX_DAY_LOSS_PCT", 15))   # 24h 고점 대비 이만큼 빠지면 청산 후 자동실행 정지. 0 = 끄기
WARMUP = pd.Timedelta("30h")                              # 1h EMA(5분봉 12개) 예열 — 360봉이면 초기값 영향이 사라진다
DAILY = 260                                               # 일봉 개수 (200일선 + 여유)
CLAUDE_URL = os.environ.get("CLAUDE_BASE_URL")             # Anthropic 호환 게이트웨이(OmniRoute). 비우면 Claude 검토 생략
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "auto/claude-opus")
USE_CLAUDE = bool(CLAUDE_URL)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout),
                              logging.FileHandler(ROOT / "autotrade.log", encoding="utf-8")])
log = logging.getLogger("autotrade")

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
        action TEXT, side TEXT, qty REAL, notional REAL, price REAL, order_id TEXT, status TEXT, reason TEXT);
    CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK (id = 1), side TEXT, qty REAL, entry REAL,
        entered_at TEXT, cash REAL);""")
    for sql in ("ALTER TABLE runs ADD COLUMN real_equity REAL", "ALTER TABLE state ADD COLUMN seen_bar TEXT",
                "ALTER TABLE state ADD COLUMN lev REAL",
                "ALTER TABLE orders ADD COLUMN client_id TEXT",     # 우리가 만든 clOrdId — 타임아웃 뒤 대사의 열쇠
                "ALTER TABLE orders ADD COLUMN filled_qty REAL", "ALTER TABLE orders ADD COLUMN fill_price REAL"):
        with contextlib.suppress(sqlite3.OperationalError):    # 기존 DB 에 열 추가 (이미 있으면 에러 → 무시)
            conn.execute(sql)
    conn.execute("INSERT OR IGNORE INTO state (id, side, qty, entry, entered_at, cash) VALUES (1, NULL, 0, 0, NULL, ?)", (PAPER_CASH,))
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
        cols = "side, qty, entry, entered_at, cash, seen_bar, lev"   # seen_bar = 마지막으로 처리한 5분봉 (UTC ISO)
        row = c.execute(f"SELECT {cols} FROM state").fetchone()
    return dict(zip(cols.split(", "), row))


def set_state(**kw):
    with db() as c:
        c.execute(f"UPDATE state SET {', '.join(f'{k}=?' for k in kw)} WHERE id=1", list(kw.values()))


def paper_trades_done():
    """완료된 모의 거래(청산) 횟수 — auto 모드의 실주문 전환 기준."""
    with db() as c:
        return c.execute("SELECT COUNT(*) FROM orders WHERE action='close' AND mode='paper' AND status='paper'").fetchone()[0]


def live_now():
    return HAVE_KEYS and (MODE == "live" or (MODE == "auto" and paper_trades_done() >= LIVE_AFTER))


def autorun():
    return not AUTORUN_OFF.exists()


def entry_blocked():
    """신규 진입만 막혔는가. 기존 포지션 보호·대사는 이것과 무관하게 계속 돈다 (2026-09-10 감사)."""
    return ENTRY_OFF.exists() or not autorun()


def set_entry_block(on, why=""):
    ENTRY_OFF.write_text(f"{now()} {why}", encoding="utf-8") if on else ENTRY_OFF.unlink(missing_ok=True)
    log.warning("신규 진입 %s%s", "차단" if on else "허용", f" — {why}" if why else "")


def set_autorun(on):
    AUTORUN_OFF.unlink(missing_ok=True) if on else AUTORUN_OFF.touch()
    log.info("자동실행 %s", "ON" if on else "OFF")
    return autorun()


# ---------- 계좌 (모의 장부는 항상, 실계좌는 live 일 때) ----------
def account(ex, px):
    """{equity, pos, paper_equity, paper_pos}. pos = {side, qty, entry, pnl, entered_at, held_days, pnl_pct} | None"""
    st = state()
    paper_pos = None
    if st["side"]:
        sgn = 1 if st["side"] == "long" else -1
        paper_pos = {"side": st["side"], "qty": st["qty"], "entry": st["entry"], "lev": st["lev"],
                     "pnl": sgn * (px - st["entry"]) * st["qty"]}
    paper_eq = st["cash"] + (paper_pos["pnl"] if paper_pos else 0)
    if ex:
        snap = X.snapshot(ex)                                          # 잔고·시세를 한 번에 (폴링 비용 절감)
        eq, pos = snap["equity"], snap["position"]
        if pos and (pos["side"] != st["side"] or not st["entered_at"]):   # 봇 밖에서 연 포지션 → 모의 장부도 맞추고 오늘 진입으로
            log.warning("봇 밖에서 연 포지션을 장부에 흡수합니다 (%s %.6f @%s) — 지금부터 익절·손절·만기 규칙으로 봇이 관리합니다",
                        pos["side"], pos["qty"], f"{pos['entry']:,.1f}")
            set_state(side=pos["side"], qty=pos["qty"], entry=pos["entry"], entered_at=now())
            st = state()
        elif pos:                                                          # 시장가 체결 수량·평단을 거래소 값으로 보정
            set_state(qty=pos["qty"], entry=pos["entry"])
        if not pos and st["side"]:                                         # 거래소는 비었는데 장부에 포지션 → 장부 정리
            set_state(side=None, qty=0, entry=0, entered_at=None)
            st, paper_pos = state(), None
    else:
        eq, pos, snap = paper_eq, paper_pos, {"cash": st["cash"]}
    if pos:
        pos["entered_at"] = st["entered_at"]
        pos["held_days"] = (dt.datetime.now(KST) - dt.datetime.fromisoformat(st["entered_at"])).days
        pos["pnl_pct"] = pos["pnl"] / eq * 100 if eq else 0.0
    return {"equity": eq, "pos": pos, "paper_equity": paper_eq, "paper_pos": paper_pos, "cash": snap["cash"]}


def liquidated(pos):
    """모의 장부의 강제청산 판정 — 백테스트(engine.liq_level)와 **같은 식**을 쓴다.

    2026-09-10 정정: 예전 주석은 "거래소는 정수 레버리지로 판정하므로 실제 청산선은 이보다 멀다" 였는데
    반대다. X.setup 이 ceil 로 올려 걸면 같은 명목에 증거금이 **덜** 들어가고 청산선은 진입가에 **가까워진다**
    (2.1배 → 3배: −48% 가 아니라 −33%). 게다가 유지증거금이 남아 있는 동안 청산되므로 그보다도 앞이다.
    모의 장부가 낙관적이면 실계좌에서만 터진다 — 그래서 여기서도 같은 함수를 쓴다."""
    lev = pos.get("lev") or LEVERAGE
    if lev <= 1:
        return False
    sgn = 1 if pos["side"] == "long" else -1
    px = pos["entry"] + pos["pnl"] / (pos["qty"] * sgn) if pos["qty"] else pos["entry"]
    lvl = E.liq_level(pos["entry"], lev, sgn)
    return px <= lvl if sgn > 0 else px >= lvl


def day_loss_hit(eq, live=False):
    """24시간 고점 대비 MAX_DAY_LOSS% 아래면 True. 전략 MDD(5배 −62%)를 잡으려는 값이 아니라,
    버그·급변으로 하루 만에 무너지는 경우를 잡는 값이다. 걸리면 청산하고 자동실행을 끈다 (재개는 사람이 make on).

    고점은 지금 재는 것과 같은 계열에서만 찾는다. runs.equity 는 모의면 장부(≈1,000), 실주문이면 실계좌(≈7)라
    한 열에 두 축척이 섞여 있어서, 모드가 바뀐 직후 고점이 −99% 로 읽히고 차단기가 영영 걸린다
    (2026-09-08 실제 발생: 모의 998 → 실계좌 7.28). paper_equity·real_equity 는 축척이 하나뿐이다."""
    if not MAX_DAY_LOSS or not eq:
        return False
    col = "real_equity" if live else "paper_equity"
    since = (dt.datetime.now(KST) - dt.timedelta(days=1)).isoformat(timespec="seconds")
    with db() as c:
        peak = c.execute(f"SELECT MAX({col}) FROM runs WHERE {col} IS NOT NULL AND timestamp > ?", (since,)).fetchone()[0]
    return bool(peak) and eq <= peak * (1 - MAX_DAY_LOSS / 100)


def real_equity(ex):
    """거래소 실제 자산 (USDT). 모의 모드여도 키가 있으면 읽기만 한다. 조회 실패는 None."""
    try:
        return X.snapshot(ex or X.client())["equity"] if HAVE_KEYS else None
    except Exception as e:  # noqa: BLE001
        log.warning("실계좌 조회 실패: %s", X.explain(e))
        return None


# ---------- Claude 검토 (주문 직전) ----------
def review_payload(c5, d1, dev, bull, px, acc, notional):
    """알고리즘이 계산한 값 + 그 근거 캔들. Claude 는 둘이 맞아떨어지는지, 사건·급변이 없는지 본다 (instructions.md)."""
    t = c5.index[-1]
    ema = c5.close.ewm(span=12, adjust=False).mean()
    s50, s200 = d1.rolling(50).mean(), d1.rolling(200).mean()
    up, dn = S.levels(px)
    return {"analysis": {"bar_kst": t.tz_convert(KST).strftime("%m-%d %H:%M"), "close": c5.close.iloc[-1],
                         "ema_1h": round(ema.iloc[-1], 1), "dev_pct": round(dev, 3), "trigger_pct": -S.D,
                         "prev_day": d1.index[-1].strftime("%Y-%m-%d"), "prev_close": d1.iloc[-1],
                         "sma50": round(s50.iloc[-1], 1), "sma200": round(s200.iloc[-1], 1), "trend_ok": bool(bull)},
            "proposal": {"action": "open", "side": "long", "notional_usdt": round(notional, 2), "leverage": LEVERAGE,
                         "equity_usdt": round(acc["equity"], 2), "price": px, "take_profit": round(up, 1),
                         "stop_loss": round(dn, 1), "max_hold_hours": S.MAXB * 5 / 60},
            "m5": [{"time": i.tz_convert(KST).strftime("%H:%M"), "open": r.open, "high": r.high, "low": r.low, "close": r.close}
                   for i, r in c5.tail(36).iterrows()],
            "daily": [{"date": i.strftime("%m-%d"), "close": c, "sma50": round(a, 1), "sma200": round(b, 1)}
                      for i, c, a, b in zip(d1.index[-15:], d1.values[-15:], s50.values[-15:], s200.values[-15:])]}


def ask_claude(payload):
    client = anthropic.Anthropic(base_url=CLAUDE_URL, api_key=os.environ.get("ANTHROPIC_API_KEY", "omniroute"),
                                 timeout=90, max_retries=1)
    user = "\n\n".join(f"## {k}\n{json.dumps(v, ensure_ascii=False)}" for k, v in payload.items())
    resp = client.messages.create(model=CLAUDE_MODEL, max_tokens=1000, system=(ROOT / "instructions.md").read_text(encoding="utf-8"),
                                  messages=[{"role": "user", "content": user}])
    text = "".join(b.text for b in resp.content if b.type == "text")
    out, _ = json.JSONDecoder().raw_decode(text[text.find("{"):])
    if not isinstance(out.get("approve"), bool):
        # bool("false") 는 True 다. 문자열이 오면 거부가 승인으로 뒤집힌다 (2026-09-10 감사).
        raise ValueError(f"approve 가 JSON Boolean 이 아닙니다: {out.get('approve')!r}")
    return {"approve": out["approve"], "reason": str(out.get("reason", ""))[:300], "model": resp.model,
            "input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}


def claude_gate(payload):
    """매수 직전 Claude 검토. 호출이 실패하면 거부로 본다 — 확인 못 한 매수는 내지 않는다."""
    try:
        v = ask_claude(payload)
    except Exception as e:  # noqa: BLE001
        log.error("Claude 검토 실패: %s", e)
        v = {"approve": False, "reason": f"검토 실패 ({e})"[:200], "model": CLAUDE_MODEL, "input_tokens": 0, "output_tokens": 0}
    log.info("Claude(%s): approve=%s — %s", v["model"], v["approve"], v["reason"])
    return v


# ---------- 주문 ----------
def _order_row(**kw):
    """orders 표에 한 줄 넣고 id 를 돌려준다."""
    with db() as c:
        return c.execute(f"INSERT INTO orders ({', '.join(kw)}) VALUES ({', '.join('?' * len(kw))})",
                         list(kw.values())).lastrowid


def _order_done(oid, **kw):
    with db() as c:
        c.execute(f"UPDATE orders SET {', '.join(f'{k}=?' for k in kw)} WHERE id=?", [*kw.values(), oid])


PENDING_TTL = pd.Timedelta("30min")   # ponytail: 시장가는 몇 초면 결론이 난다. 이만큼 지나도 거래소가 모르면 '안 나간 주문' 으로 본다
                                      # (조회 자체가 30분 내내 실패하는 경우와는 못 가른다 — 그때도 account() 대사가 2차 방어선)


def unresolved_orders(ex):
    """결론이 안 난 실주문(intent · submitted · unknown)을 clOrdId 로 다시 조회해 기록을 확정하고, 아직 모르는 건수를 돌려준다.
    0 이 아니면 신규 진입을 막는다 — 접수만 되고 안 보이던 주문이 뒤늦게 체결되면 포지션이 2배가 된다 (2026-09-28 감사 7b).
    장부는 건드리지 않는다: 체결된 포지션은 다음 account() 대사가 거래소 값으로 흡수한다."""
    since = (dt.datetime.now(KST) - dt.timedelta(days=1)).isoformat(timespec="seconds")   # 옛 기록은 다시 쓰지 않는다
    with db() as c:
        rows = c.execute("SELECT id, client_id, timestamp FROM orders WHERE mode='live' AND client_id IS NOT NULL AND timestamp > ? "
                         "AND (status IN ('intent', 'submitted') OR status LIKE 'unknown%')", (since,)).fetchall()
    left = 0
    for oid, cid, ts in rows:
        got = X.find_order(ex, cid)
        if got and got["filled"] > 0:
            _order_done(oid, status="filled(대사)" if got["status"] == "closed" else "partial(대사)",
                        order_id=got["id"], filled_qty=got["filled"], fill_price=got["avg"] or None)
        elif got and got["status"] == "canceled":
            _order_done(oid, status="rejected(대사)", order_id=got["id"])
        elif pd.Timestamp.now(tz=KST) - pd.Timestamp(ts) > PENDING_TTL:
            _order_done(oid, status="notfound(대사)")
            log.critical("주문 %s 이 %s 지나도 거래소에 없습니다 — 안 나간 주문으로 보고 진입 차단을 풉니다", cid, PENDING_TTL)
        else:
            left += 1
    return left


def execute(ex, run_id, action, side, qty, notional, px, reason, lev=None):
    """action: open|close. → 성공 여부 (True 면 장부·거래소가 같은 상태라고 확인된 것).

    실행 순서 (2026-09-10 감사로 뒤집었다). **주문이 먼저, 장부는 나중이다**:
      1) 의도(intent)를 clOrdId 와 함께 DB 에 먼저 적는다 — 여기서 죽어도 흔적이 남는다
      2) 거래소에 제출한다
      3) 그 clOrdId 로 **주문을 다시 조회해** 실제 체결 수량·평단을 읽는다
      4) 그 값으로 모의 장부를 확정한다
    예전에는 4)→2) 순서였다. 주문이 실패하면 장부에는 포지션이 있고 거래소에는 없었다(또는 반대).
    **타임아웃은 주문 실패가 아니다** — 예외가 나면 다시 조회하고, 그래도 모르면 status='unknown' 으로
    남긴 채 장부를 건드리지 않는다. 다음 사이클의 account() 대사가 거래소 쪽 진실로 맞춘다."""
    mode = "live" if ex else "paper"
    lev = LEVERAGE if lev is None else lev
    cid = "a" + uuid.uuid4().hex[:20]                      # OKX clOrdId: 영숫자 1~32자
    oid = _order_row(run_id=run_id, timestamp=now(), mode=mode, action=action, side=side, qty=qty or 0,
                     notional=notional, price=px, order_id=None, client_id=cid, status="intent", reason=reason)
    fill_qty, fill_px, status, ex_id = qty, px, "paper", None
    if ex:
        try:
            if action == "open":
                # 거래소 레버리지는 정수다. ceil 로 올리면 같은 명목에 증거금이 **덜** 들어가고
                # 청산선이 진입가에 더 가까워진다 (2.1배→3배: −48% 가 아니라 −33%). engine.liq_level 과 같은 계산.
                X.setup(ex, E.exchange_lev(lev))
                ex_id, fill_qty = X.open_position(ex, side, notional, px, cid)
            else:
                ex_id = X.close_position(ex, {"side": side, "qty": qty}, cid)
            got = X.find_order(ex, cid)
            if got and got["filled"] > 0:
                fill_qty, fill_px = got["filled"], got["avg"] or px
                status = "filled" if got["status"] == "closed" else "partial"
            elif got and got["status"] == "canceled":
                status = "rejected"                        # 거래소가 체결 0 으로 취소·거부했다 — 성공이 아니다 (2026-09-28 감사)
            else:
                status = "submitted"                       # 조회가 아직 안 잡힌다 → 다음 사이클 대사에 맡긴다
        except Exception as e:                             # noqa: BLE001
            got = X.find_order(ex, cid)                    # 타임아웃/끊김 뒤 '정말 안 나갔는지' 확인
            if got and got["filled"] > 0:
                fill_qty, fill_px, status = got["filled"], got["avg"] or px, "filled(재조회)"
                log.warning("주문 예외 뒤 재조회에서 체결 확인: %s %.6f @%s", action, fill_qty, f"{fill_px:,.1f}")
            else:
                status = f"unknown: {X.explain(e)}"[:200]
                _order_done(oid, status=status, order_id=ex_id)
                log.critical("주문 상태 불명 (%s %s) — 장부를 바꾸지 않습니다. 다음 사이클에서 거래소 상태로 대사합니다: %s",
                             action, side, X.explain(e))
                return False
    if status == "rejected" or (action == "close" and status in ("partial", "submitted")):
        # 청산은 거래소가 전량 체결을 확인해 줄 때만 장부에서 지운다. 남은 포지션은 다음 사이클 account() 가
        # 거래소 수량으로 맞추고 act() 가 같은 봉부터 다시 청산한다 (2026-09-28 감사: 부분·미확인 청산을 완료로 적었다).
        _order_done(oid, status=status, order_id=ex_id, filled_qty=fill_qty if status == "partial" else 0, fill_price=fill_px)
        log.critical("주문 미완료 (%s %s · %s) — 장부를 바꾸지 않고 다음 사이클에 거래소 상태로 대사합니다", action, side, status)
        return False
    # 체결을 확인한 **뒤에** 장부를 확정한다
    st, fee = state(), (fill_qty or 0) * fill_px * X.TAKER_FEE
    if action == "open":
        fill_qty = fill_qty or notional / fill_px
        set_state(side=side, qty=fill_qty, entry=fill_px, cash=st["cash"] - fee, entered_at=now(), lev=lev)
    elif st["side"]:
        sgn = 1 if st["side"] == "long" else -1
        set_state(side=None, qty=0, entry=0, entered_at=None,
                  cash=st["cash"] + sgn * (fill_px - st["entry"]) * st["qty"] - fee)
    _order_done(oid, status=status, order_id=ex_id, filled_qty=fill_qty, fill_price=fill_px, qty=fill_qty or 0)
    log.info("[%s] %s %s %.6f %s (%s USDT, %.2f배) @%s — %s", status, action, side, fill_qty or 0, X.COIN,
             f"{notional:,.2f}", lev, f"{fill_px:,.1f}", reason)
    return status.startswith(("filled", "paper", "partial", "submitted"))


# ---------- 판단 · 주문 ----------
def entry_bar(entered_at):
    """진입 시각(KST ISO) → 진입 뒤 첫 **온전한** 5분봉 (UTC). 익절·손절·만기는 이 봉부터 센다.
    봇은 봉 마감 15초(+Claude 검토) 뒤에 사므로, 진입한 봉의 고가·저가에는 진입 전 가격이 섞여 있어
    그 봉으로 판정하면 가짜 익절·손절이 나온다 (2026-09-28 감사 2b, (a)안). 백테스트('진입 = 그 봉 시가')보다
    판정이 최대 한 봉 늦고 만기도 그만큼 늦다 — 진입 직후 5분 안의 터치는 다음 봉부터 본다."""
    return pd.Timestamp(entered_at).tz_convert("UTC").ceil(S.BAR)


def act(ex, pub, run_id, c5, d1, px, acc, fresh=True):
    """지난 사이클 이후 완성 봉으로 청산 확인 → 무포지션이면 마지막 봉 신호 → Claude 검토 → 주문.
    fresh=False (마지막 완성 봉이 지금 기대하는 봉이 아니다 = 데이터 지연) 면 신규 진입만 막는다.
    → (action, reason, claude 결과|None, 마지막 봉 괴리 %, 추세 통과 여부)"""
    dev, bull = S.indicators(c5, d1)
    t, st, pos = c5.index[-1], state(), acc["pos"]
    seen = pd.Timestamp(st["seen_bar"]) if st["seen_bar"] else t - S.BAR      # 첫 실행: 마지막 봉만 본다
    action, v, retry = "hold", None, False
    pending = unresolved_orders(ex) if ex else 0                  # 실주문이면 매 사이클 미확정 주문 기록을 거래소로 확정
    notional = acc["equity"] * POSITION_PCT * LEVERAGE * (1 - CASH_RESERVE)
    line = (f"5분봉 {t.tz_convert(KST):%H:%M} 종가 {c5.close.iloc[-1]:,.0f} · 1h EMA 대비 {dev.iloc[-1]:+.2f}% "
            f"(진입 ≤ −{S.D:g}%) · 전날 종가 50·200일선 {'위' if bull.iloc[-1] else '아래'}")
    if pos and pos["side"] != "long":
        reason = f"롱 전용 봇인데 봇 밖에서 연 {pos['side']} 포지션이 있다 → 손대지 않는다 (직접 정리하세요)"
        log.critical(reason)
    elif pos:
        t0, hit = entry_bar(st["entered_at"]), None
        for bt, r in c5[(c5.index > seen) & (c5.index >= t0)].iterrows():
            hit = S.exit_check(pos["entry"], t0, bt, r.open, r.high, r.low)
            if hit:
                break
        if not hit and not ex and liquidated(pos):
            hit, bt = (px, "강제청산(모의)"), t
        if hit:
            fill = px if ex else hit[0]          # 모의: 백테스트처럼 익절·손절가 체결 / 실주문: 시장가 (최대 5분 늦다)
            reason = f"{hit[1]} — 진입 {pos['entry']:,.1f} → {hit[0]:,.1f} ({bt.tz_convert(KST):%m-%d %H:%M} 봉) → 청산"
            if execute(ex, run_id, "close", "long", pos["qty"], pos["qty"] * fill, fill, reason):
                action = "close"
            else:                                # seen_bar 를 넘기지 않는다 → 다음 사이클이 같은 봉부터 다시 청산 (2026-09-28 감사)
                retry, reason = True, reason + " · 청산 미확인 — 다음 사이클에 다시 시도"
        else:
            up, dn = S.levels(pos["entry"])
            reason = (f"롱 보유 · 익절 {up:,.0f} / 손절 {dn:,.0f} / 만기 "
                      f"{(t0 + S.MAXB * S.BAR).tz_convert(KST):%m-%d %H:%M} · {line}")
    elif t > seen and S.signal(dev.iloc[-1], bull.iloc[-1]):
        action, reason = ("open", line + " → 급락 매수") if fresh else ("hold", line + " → 급락이지만 마지막 봉이 늦게 왔다(데이터 지연) → 사지 않는다")
    else:
        reason = line + " → 관망"
    if action == "open" and entry_blocked():                     # 신규 진입만 막힌 상태 (사고 차단기 · autorun off)
        action, reason = "hold", "신규 진입 차단 중 (entry.off / 자동실행 OFF) — 보호·대사만 한다 · " + reason
    if action == "open" and pending:                              # 결론 안 난 주문이 있으면 새로 내지 않는다
        action, reason = "hold", f"결론 안 난 주문 {pending}건 — 거래소에서 확인될 때까지 신규 진입 안 함 · " + reason
    if action == "open" and ex and notional * (1 / LEVERAGE + X.TAKER_FEE) > acc["cash"]:   # 제출 전 가용 잔고 확인
        action, reason = "hold", f"가용 {acc['cash']:,.2f} USDT < 증거금+수수료 {notional * (1 / LEVERAGE + X.TAKER_FEE):,.2f} → 제출 안 함 · " + reason
    if action == "open" and USE_CLAUDE:                          # ← 주문 직전. 거부되면 이번 신호는 끝
        v = claude_gate(review_payload(c5, d1, dev.iloc[-1], bull.iloc[-1], px, acc, notional))
        if v["approve"]:
            reason += " · Claude 승인: " + v["reason"]
        else:
            action, reason = "hold", "Claude 거부: " + v["reason"]
    if action == "open":
        if USE_CLAUDE:                                           # 검토(최대 90초) 사이 움직인 만큼 지금 가격으로 수량을 정한다
            px = X.price(pub)
        execute(ex, run_id, "open", "long", None, notional, px, reason)
    if not retry:
        set_state(seen_bar=t.isoformat())
    return action, reason, v, float(dev.iloc[-1]), bool(bull.iloc[-1])


# ---------- 한 사이클 ----------
NEXT_RUN = None   # 스케줄러의 다음 실행 시각 (대시보드 표시용)
_CYCLE = threading.Lock()   # 같은 프로세스 안에서 사이클이 겹치지 않게 (대시보드 수동 주문 + 스케줄러)


def run_cycle(source="자동"):
    """사이클은 한 번에 하나만. 겹치면 같은 돌파봉으로 주문이 두 번 나간다."""
    if not single_instance():          # 프로세스간 락 — 주문 경로는 전부 이 뒤에 있다
        log.critical("주문을 낼 수 있는 다른 프로세스가 이미 돌고 있습니다 — 이번 호출(%s)은 하지 않습니다", source)
        return
    if not _CYCLE.acquire(blocking=False):
        log.warning("이전 사이클이 아직 돌고 있습니다 — 이번 호출(%s)은 건너뜁니다", source)
        return
    try:
        _run_cycle(source)
    finally:
        _CYCLE.release()


def _run_cycle(source="자동"):
    live = live_now()
    tag = "실주문"
    mode = tag if live else f"모의 ({paper_trades_done()}/{LIVE_AFTER} 완료 후 {tag})" if MODE == "auto" and HAVE_KEYS else "모의"
    mode += f" · {source}"
    with db() as c:
        run_id = c.execute("INSERT INTO runs (timestamp, mode, status) VALUES (?, ?, 'running')", (now(), mode)).lastrowid
    log.info("=== run %d 시작 (%s%s) ===", run_id, mode, f" · Claude {CLAUDE_MODEL}" if USE_CLAUDE else "")
    try:
        ex = X.client() if live else None
        pub = ex or X.public()
        utc_now = pd.Timestamp.now(tz="UTC")
        seen = state()["seen_bar"]
        since = min(pd.Timestamp(seen), utc_now - WARMUP) if seen else utc_now - WARMUP
        raw = X.bars_since(pub, since - S.BAR)
        c5 = raw[raw.index + S.BAR <= utc_now]                   # 진행 중인 봉 제외
        d1 = X.candles(pub, DAILY, "1d").close.iloc[:-1]         # 진행 중인 오늘 봉 제외
        d1.index = d1.index.tz_convert("UTC")
        px = X.price(pub)
        acc = account(ex, px)
        pos = acc["pos"]
        log.info("%s %s USDT · 마지막 5분봉 %s · 자산 %s USDT (모의 %s) · 포지션 %s", X.COIN, f"{px:,.1f}",
                 c5.index[-1].tz_convert(KST).strftime("%m-%d %H:%M"), f"{acc['equity']:,.2f}", f"{acc['paper_equity']:,.2f}",
                 f"{pos['side']} {pos['held_days']}일 손익 {pos['pnl_pct']:+.1f}%" if pos else "없음")
        dev = trend = None
        if day_loss_hit(acc["equity"], live):                             # 사고 차단기 — 신호와 무관하게 손 떼고 사람을 부른다
            reason = f"24시간 고점 대비 −{MAX_DAY_LOSS:g}% 이상 손실 → 청산 후 신규 진입 정지 (재개: make on)"
            log.critical(reason)
            action, v = "hold", None
            set_entry_block(True, "사고 차단기")                            # 신규 진입은 지금 즉시 막는다
            closed = bool(pos) and execute(ex, run_id, "close", pos["side"], pos["qty"], pos["qty"] * px, px, reason)
            if closed:
                action = "close"
            if pos and not closed:
                # 2026-09-10 감사: 청산 실패한 포지션을 방치하지 않도록 진입만 막고 보호 루프는 계속 돌린다.
                reason += " · **비상 청산 실패 — 자동실행은 켜 둔 채 다음 사이클에 다시 시도합니다**"
                log.critical("비상 청산에 실패했습니다. 포지션이 남아 있으므로 자동실행을 끄지 않습니다. 즉시 확인하세요.")
            else:
                set_autorun(False)
        else:
            fresh = c5.index[-1] >= utc_now.floor(S.BAR) - S.BAR           # 마지막 완성 봉이 직전 봉인가 (지연 데이터로 진입 금지)
            action, reason, v, dev, trend = act(ex, pub, run_id, c5, d1, px, acc, fresh)   # 청산 확인 → 신호 → Claude 검토 → 주문
        if action != "hold":
            acc = account(ex, px)
        with db() as c:
            c.execute("UPDATE runs SET status='done', equity=?, paper_equity=?, real_equity=?, price=?, zone=?, p=?, bull=?, bear=?, "
                      "position=?, action=?, reason=?, model=?, input_tokens=?, output_tokens=? WHERE id=?",
                      (acc["equity"], acc["paper_equity"], acc["equity"] if ex else real_equity(None), px,
                       "long" if action == "open" else "wait", dev, None if trend is None else int(trend), None,
                       pos["side"] if pos else None, action, reason,
                       *((v["model"], v["input_tokens"], v["output_tokens"]) if v else (None, None, None)), run_id))
        log.info("=== run %d 완료: %s — %s ===", run_id, action, reason)
    except Exception as e:  # noqa: BLE001
        log.error("run %d 실패: %s", run_id, X.explain(e))
        log.debug("스택", exc_info=True)
        with db() as c:
            c.execute("UPDATE runs SET status=? WHERE id=?", (f"error: {X.explain(e)}"[:300], run_id))


def confirm_live():
    """진짜 돈이 나갈 수 있는 설정이면 사람이 직접 '실주문' 을 치게 한다. autotrade·dashboard 공용."""
    if not (HAVE_KEYS and MODE != "paper"):
        return
    msg = f"OKX 실계좌 주문 모드입니다 (MODE={MODE}{', 모의 %d회 완료 후 자동 전환' % LIVE_AFTER if MODE == 'auto' else ''}, {LEVERAGE:g}배). '실주문' 을 입력하면 계속합니다: "
    if input(msg).strip() != "실주문":
        sys.exit("취소")


_LOCK = None


def single_instance(port=8765):
    """**주문을 낼 수 있는 프로세스는 하나만.** 두 개가 돌면 같은 돌파봉으로 주문이 두 번 나간다
    (2026-09-08 실제로 dashboard 와 autotrade 가 같이 떠서 CONF 7·8 두 판단이 3분 간격으로 trading.db 에 섞였다).
    포트를 하나 잡아 두는 것으로 막는다 — 프로세스가 죽으면 OS 가 알아서 놓아 준다.

    2026-09-10 감사: 예전에는 schedule_forever() 안에서만 불렀다. 그래서 시작 직후의 첫 run_cycle 과
    `--once` 는 락 없이 주문을 냈다 — 다른 프로세스가 이미 돌고 있어도 그대로 나갔다.
    지금은 주문 경로의 입구(__main__ · dashboard)에서 먼저 잡는다."""
    global _LOCK
    if _LOCK is not None:                       # 이 프로세스가 이미 잡았다 (__main__ → schedule_forever)
        return True
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
    except OSError:
        return False
    _LOCK = s                                                # 참조를 살려 둬야 닫히지 않는다
    return True


def next_slot():
    """다음 INTERVAL_MIN 경계 + 15초 (KST). 5분봉이 닫히고 거래소에 반영될 시간을 준다."""
    step = INTERVAL_MIN * 60
    return dt.datetime.fromtimestamp((time.time() // step + 1) * step + 15, KST)


def schedule_forever():
    """시작 즉시 한 사이클, 이후 5분봉 마감 15초 뒤마다. 대시보드는 이걸 백그라운드 스레드로 돌린다.
    벽시계 기준으로 다음 시각을 정하고 짧게 자므로, 재시작·PC 절전 뒤에도 늦은 사이클을 바로 따라잡는다
    (놓친 봉의 청산 확인은 act() 가 seen_bar 부터 훑어서 따라잡는다)."""
    global NEXT_RUN
    if not single_instance():
        log.critical("스케줄러가 이미 다른 프로세스에서 돌고 있습니다 — 이 프로세스는 자동 판단을 하지 않습니다 (make stop 후 하나만 띄우세요)")
        return
    while True:
        NEXT_RUN = next_slot()
        holding = bool(state()["side"])
        if autorun():
            src = "자동"
        elif PROTECT_WHEN_OFF and holding:
            src = "보호"
            log.warning("자동실행 OFF 이지만 포지션이 남아 있습니다 — 보호·대사 사이클만 돌립니다 (신규 진입 없음)")
        else:
            log.info("자동실행 OFF (autorun.off) — 이번 판단 건너뜀")
            src = None
        if src:
            try:
                run_cycle(src)
            except Exception:
                log.exception("자동 사이클 실패")
        log.info("다음 실행 %s", NEXT_RUN.strftime("%H:%M:%S"))
        while dt.datetime.now(KST) < NEXT_RUN:
            time.sleep(5)


def manual_order(action):
    """대시보드에서 사람이 직접 누른 실주문. auto 모드의 '모의 N회' 게이트와 무관하게 바로 나간다.
    자동 매매와 같은 execute() 를 타므로 주문 기록·장부가 그대로 이어진다."""
    if not X.have_keys():
        raise ValueError("OKX 실계좌 API 키가 없습니다")
    if not single_instance():
        raise RuntimeError("주문을 낼 수 있는 다른 프로세스가 이미 돌고 있습니다 (make stop 후 하나만 띄우세요)")
    if not _CYCLE.acquire(timeout=30):        # 자동 사이클과 겹치면 같은 포지션에 주문이 두 번 나간다
        raise RuntimeError("자동 사이클이 돌고 있습니다 — 잠시 뒤 다시 누르세요")
    try:
        return _manual_order(action)
    finally:
        _CYCLE.release()


def _manual_order(action):
    if MODE == "paper":
        raise ValueError("MODE=paper 에서는 실주문을 보내지 않습니다 (.env 의 MODE 를 auto 나 live 로)")
    ex = X.client()
    snap = X.snapshot(ex)
    px, pos = snap["price"], snap["position"]
    if action == "buy" and pos:
        raise ValueError(f"이미 {snap['coin_qty']:.6f} {X.COIN} 포지션이 있습니다")
    if action == "sell" and not pos:
        raise ValueError("열린 포지션이 없습니다")
    if action == "buy" and snap["cash"] * POSITION_PCT * LEVERAGE < X.MIN_ORDER:
        raise ValueError(f"주문 가능 {snap['cash']:,.2f} USDT × {LEVERAGE:g}배 < 최소 {X.MIN_ORDER} USDT")
    with db() as c:
        run_id = c.execute("INSERT INTO runs (timestamp, mode, status, price, position, action, reason) "
                           "VALUES (?, '수동', 'done', ?, ?, ?, ?)",
                           (now(), px, pos["side"] if pos else None, "open" if action == "buy" else "close",
                            "대시보드 수동 실주문")).lastrowid
    if action == "buy":
        ok = execute(ex, run_id, "open", "long", None, snap["cash"] * POSITION_PCT * LEVERAGE, px,
                     "대시보드 수동 매수")
    else:
        ok = execute(ex, run_id, "close", pos["side"], pos["qty"], pos["qty"] * px, px,
                     "대시보드 수동 매도")
    if not ok:
        with db() as c:
            raise RuntimeError(c.execute("SELECT status FROM orders WHERE run_id=? ORDER BY id DESC LIMIT 1",
                                         (run_id,)).fetchone()[0])
    return {"action": action, "price": px, "run_id": run_id}


if __name__ == "__main__":
    confirm_live()
    if not single_instance():                 # --once 와 시작 직후 사이클도 같은 락 뒤에 둔다 (2026-09-10 감사)
        sys.exit("주문을 낼 수 있는 프로세스가 이미 돌고 있습니다 (make stop 후 하나만 띄우세요)")
    run_cycle("시작")
    if "--once" not in sys.argv:
        schedule_forever()
