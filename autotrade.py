"""BTC 단일 · 일봉 국면 + 4h 돌파 자동매매 봇 (OKX USDT 무기한 선물, 격리 마진).

용어: **롱 = 상승에 거는 것, 숏 = 하락에 거는 것.** 청산은 방향이 아니라 행동이다 (예전 zone="short" 는 청산이었다).
자리 (model.signal(): 마지막 완성 일봉 + 마지막 완성 4h 봉):
  long  = 일봉 강세 CONF개↑ & 4h 종가가 직전 12봉 고가 돌파   → 롱 진입
  short = 일봉 약세 CONF개↑ & 4h 종가가 직전 12봉 저가 이탈   → 숏 진입 (ALLOW_SHORT=1 일 때만)
  wait  = 그 외.  청산은 들고 있는 쪽의 exit_long / exit_short 로 본다
숏은 기본 꺼짐(ALLOW_SHORT=0)이다 — research_both_sides.txt: 숏만 1배 −72%, 롱+숏 1배 −34% (롱만 +135%).
매 사이클(INTERVAL_MIN, 기본 10분) 판단은 open / close / hold 셋 중 하나이고, open·close 는 그 사이클 안에서 바로 주문한다.
포지션 상태기계 (flat / long / short) — 한 번에 한 방향만. 방향 전환은 청산 후 다음 사이클부터:
  flat : long·short 자리 → Claude 검토 → 승인되면 '진입 대기' (같은 4h 자리로 Claude 를 부르는 것은 한 번뿐)
         진입 대기 중에는 매 사이클 1분봉으로 하한(=돌파당한 고가)이 지켜지는지 보고, 지켜지면 그때 매수.
         다음 4h 봉이 닫힐 때까지 못 지키면 자리 포기 (ENTRY_FLOOR=0 이면 예전처럼 신호 즉시 매수)
         근거: research_entry_timing.txt — 3배 즉시매수 +492%/MDD −38.9% → 하한 확인 +1,156%/−35.2% (2026-09-09 재측정)
  long/short : 그 방향의 청산 조건(이탈선 or 국면 붕괴) → 즉시 청산, 그 외 → 보유
사이징: 명목 = 자산 × POSITION_PCT × lev. lev 는 변동성 타겟팅이 정한다 (VOL_TARGET_PCT, 상한 LEVERAGE):
  계좌 일별 실현변동성이 목표를 넘으면 그만큼 깎고, 올리지는 않는다. 이력이 VOL_WINDOW/2 일보다 짧으면 대기.
  근거: research_quant_transfer.txt — 하한 재확보 진입 기준 MDD −42.5%→−22.7%, Sharpe(검증) 0.80→1.11.
  진입 직전 okx.setup() 이 격리 마진·레버리지(정수로 올림)를 거래소에 설정한다.
  모의 장부는 손실이 증거금(명목/LEVERAGE)에 닿으면 강제청산으로 흉내 낸다. 최소 주문 10 USDT.

모의 3단계:
  1) 모의 장부(paper)  : 이 파일 안의 state 표. 거래소 호출 없음. 어느 모드에서든 항상 기록된다
  2) OKX 데모(OKX_DEMO=1, 기본) : '실주문' 이 OKX 모의투자 서버로 나간다. 가짜 돈, 진짜 체결·청산·수수료·레버리지
  3) 실계좌(OKX_DEMO=0) : 진짜 돈. 시작할 때 '실주문' 을 타이핑해야만 뜬다
모의·실주문 연계 (MODE):
  paper : 모의 장부만. 키가 있으면 실계좌 잔고는 읽어서 기록·표시만 한다 (주문 없음)
  live  : 매 주문을 거래소(데모 또는 실계좌)에 보냄
  auto  : 모의 장부로 시작 → 모의 거래(진입→청산) LIVE_AFTER_PAPER_TRADES 회 완료되면 그 다음 주문부터 거래소 주문   (기본)
사고 차단기 (MAX_DAY_LOSS_PCT, 기본 40%): 24시간 고점 대비 그만큼 빠지면 신호와 무관하게 청산하고 자동실행을 끈다.
  전략 MDD(3배 −50%) 를 잡는 값이 아니라 버그·급변을 잡는 값이다. 재개는 사람이 make on.
레버리지 상한: MAX_LEVERAGE(5배). .env 가 넘기면 시작하지 않는다. 10배부터 백테스트에서 전액 청산이다 (2026-09-10 재측정).
Claude 검토 (CLAUDE_BASE_URL, OmniRoute 경유): **주문을 내기 직전**에만 호출한다 — 자리가 났을 때가 아니라
  분봉이 진입선을 지켜 체결이 확정된 순간이다 (2026-09-10 이동. 그전에는 최대 4시간 전에 물어봤다).
  알고리즘 값을 캔들과 대조하고 사건·급변을 본다. 거부·실패면 그 자리를 접는다. 한 자리에 호출은 한 번뿐.
자동실행 온오프: autorun.off 가 있으면 **신규 진입을 멈춘다** (make off / make on, 대시보드 버튼). 실행 중에도 바로 반영.
  포지션이 남아 있으면 청산·대사 사이클은 계속 돈다 (PROTECT_WHEN_OFF=0 으로 끌 수 있다) — 2026-09-10 감사:
  '신규 진입 정지' 와 '기존 포지션 방치' 는 다른 문제다. entry.off 는 진입만 막는 별도 스위치(사고 차단기가 켠다).

사용:  python autotrade.py --once     1회 실행
       python autotrade.py            INTERVAL_MIN 분마다 실행 (기본 60)
       python dashboard.py            대시보드 http://localhost:8000
"""
import contextlib
import datetime as dt
import json
import logging
import math
import os
import pathlib
import socket
import sqlite3
import statistics
import sys
import threading
import time
import uuid
import zoneinfo

import anthropic
from dotenv import load_dotenv

import engine as E
import okx as X
import model as M

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parent
DB_PATH = ROOT / "trading.db"
KST = zoneinfo.ZoneInfo("Asia/Seoul")
AUTORUN_OFF = ROOT / "autorun.off"                       # 있으면 신규 진입 안 함 (보호·대사는 계속)
ENTRY_OFF = ROOT / "entry.off"                           # 신규 진입만 막는 별도 스위치 (사고 차단기가 켠다)
PROTECT_WHEN_OFF = os.environ.get("PROTECT_WHEN_OFF", "1") != "0"
                          # 자동실행을 껐어도 포지션이 남아 있으면 청산·대사 사이클은 계속 돈다.
                          # 2026-09-10 감사: 비상청산이 실패한 채 set_autorun(False) 가 걸리면 포지션이
                          # 사람이 올 때까지 방치됐다. '신규 진입 정지' 와 '기존 포지션 보호' 는 다른 스위치다.
MODE = os.environ.get("MODE", "auto")                    # paper | live | auto
LIVE_AFTER = int(os.environ.get("LIVE_AFTER_PAPER_TRADES", 2))
HAVE_KEYS = all(os.environ.get(k) for k in ("OKX_API_KEY", "OKX_SECRET", "OKX_PASSPHRASE"))
PAPER_CASH = float(os.environ.get("PAPER_CASH", 1000))   # 모의 장부 시작 자산 (USDT)
INTERVAL_MIN = int(os.environ.get("INTERVAL_MIN", 10))    # 사이클 간격(분). 4h 봉이 닫히면 늦어도 이 안에 잡는다.
                          # 10분이 적정 — backtest_latency.py: 1~30분은 차이가 노이즈 폭(검증 Sharpe 1.19~1.29)이고
                          # 60분부터 뚜렷하게 나빠진다(1.11). 놓치는 자리가 4→8건으로 늘어서다. 청산 지연 비용은 ±1bp 로 무시 가능
POSITION_PCT = float(os.environ.get("POSITION_PCT", 100)) / 100
CASH_RESERVE = float(os.environ.get("CASH_RESERVE_PCT", 1)) / 100   # 명목에서 떼어 두는 여유 (수수료·슬리피지·펀딩)
                          # 100% 증거금 투입은 왕복 수수료(5배에서 자산의 0.5%)와 펀딩을 낼 현금조차 남기지 않는다
MAX_SLIP = float(os.environ.get("MAX_SLIP_PCT", 0.5)) / 100        # 검토 시점 대비 이만큼 벌어지면 진입을 접는다
LEVERAGE = float(os.environ.get("LEVERAGE", 1))           # 모의 장부·거래소 공통 (okx.setup 이 진입 직전 설정)
MAX_LEVERAGE = 5                    # 코드가 막는 상한 (2026-09-10 3→5, 사용자 결정).
                          # backtest_current.py --lev (2026-09-10 엔진 수정 후 재측정, audit/stages.csv):
                          # 고정 5배·7배 모두 104건 생존, **10배부터 전액 청산**. 진입 후 최악 역행 −10.1%
                          # (청산되는 봉의 저가는 빼고 잰 값) → 이론 상한 9.9배. 5배의 여유는 약 2배다.
                          # 예전 주석의 '7배부터 청산 · 상한 6.8배' 는 **수정 전 엔진의 허위 청산**이었다.
                          # 그래도 5배를 권하지 않는다 — 상위 5개 거래를 빼면 고정 5배의 누적은 **−15%** 이고
                          # (backtest_sizing.py), 표본에 없던 폭락일(−20%↑)이 한 번 오면 5배도 끝난다.
if LEVERAGE > MAX_LEVERAGE:                               # LLM 검토를 마지막 방어선으로 두지 않는다
    sys.exit(f".env 의 LEVERAGE={LEVERAGE:g} 가 상한 {MAX_LEVERAGE}배를 넘습니다. 낮추고 다시 실행하세요.")
VOL_TARGET_PCT = float(os.environ.get("VOL_TARGET_PCT", 40))   # 변동성 타겟. 0 = 끔 (레버리지를 LEVERAGE 로 고정)
VOL_WINDOW = int(os.environ.get("VOL_WINDOW", 40))             # 실현 변동성 창 (일). 절반(20일)이 쌓여야 켜진다
MAX_DAY_LOSS = float(os.environ.get("MAX_DAY_LOSS_PCT", 40))   # 24h 고점 대비 이만큼 빠지면 청산 후 자동실행 정지. 0 = 끄기
                          # 2026-09-10 30→40: 고정 5배의 정상 24h 낙폭이 −29.9% 라 30 이면 평범한 손실 거래에 걸린다.
                          # 걸리면 최악의 자리에서 청산하고 사람이 make on 할 때까지 회복 구간을 통째로 놓친다
ALLOW_SHORT = os.environ.get("ALLOW_SHORT", "0") != "0"   # 숏(하락 베팅) 진입 허용. 기본 꺼짐 — 백테스트가 음수다
EXTREME_MIN = int(os.environ.get("EXTREME_MIN", 0))      # 진입 시 요구하는 4h 극단 점수 하한 (0 = 끔).
                                                          # research_reversion.txt: OKX 표본에선 2 가 낫고, 10년 Bitstamp 표본에선 차이 없음
ENTRY_FLOOR = os.environ.get("ENTRY_FLOOR", "1") != "0"   # 1: 하한(돌파선) 방어를 분봉으로 확인한 뒤 매수. 0: 예전처럼 신호 즉시 매수
CANDLES, H4_CANDLES = 100, 60                             # 일봉(50일선) · 4h 봉(직전 12봉 고가 + 극단 지표 20봉 여유)
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
                "ALTER TABLE state ADD COLUMN watch_bar TEXT", "ALTER TABLE state ADD COLUMN watch_hi REAL",
                "ALTER TABLE state ADD COLUMN watch_since TEXT", "ALTER TABLE state ADD COLUMN lev REAL",
                "ALTER TABLE state ADD COLUMN watch_side TEXT",
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
        cols = "side, qty, entry, entered_at, cash, seen_bar, watch_bar, watch_hi, watch_since, lev, watch_side"
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
            log.warning("봇 밖에서 연 포지션을 장부에 흡수합니다 (%s %.6f @%s) — 이후 4h 이탈·국면 붕괴 때 봇이 청산합니다",
                        pos["side"], pos["qty"], f"{pos['entry']:,.1f}")
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


def target_leverage(live):
    """변동성 타겟팅 → (이번 진입에 쓸 레버리지, 사유). 실현 변동성이 목표를 넘는 만큼 깎는다. 올리지는 않는다.

    lev = min(LEVERAGE, VOL_TARGET_PCT / 계좌 일별 실현변동성(연율))
    quant_nasq100 의 exposure_cap() 을 레버리지 쪽으로 옮긴 것. 자산 축척이 섞이지 않게 day_loss_hit 과
    같은 열을 본다. 이력이 VOL_WINDOW/2 일보다 짧거나 변동성이 0(계속 쉬었다)이면 기능 대기 — LEVERAGE 그대로.
    검증 (research_quant_transfer.txt, 실전 경로=하한 재확보 진입, 타겟 40%·창 40일, 2021-03~2026-09):
      누적 +1,156%→+1,027%, MDD −42.5%→−22.4%, Sharpe 탐색 1.05→1.43 · 검증 0.80→1.29.
      타겟 20~60% × 창 30~120일 25조합 전부에서 MDD 개선 · 검증 Sharpe 개선 (기준선 0.80).
      창 40일이 30·60·90·120 보다 낫고, 절반인 20일만 쌓이면 켜져서 실전에서 더 빨리 붙는다."""
    if VOL_TARGET_PCT <= 0:
        return LEVERAGE, ""
    col = "real_equity" if live else "paper_equity"
    since = (dt.datetime.now(KST) - dt.timedelta(days=VOL_WINDOW)).isoformat(timespec="seconds")
    with db() as c:                                  # 날짜별 마지막 값 = 그 날 종료 자산 (SQLite 의 MAX + bare column)
        rows = c.execute(f"SELECT MAX(timestamp), {col} FROM runs WHERE {col} > 0 AND timestamp > ? "
                         f"GROUP BY substr(timestamp, 1, 10) ORDER BY 1", (since,)).fetchall()
    need = max(2, VOL_WINDOW // 2)
    if len(rows) < need:
        return LEVERAGE, f"변동성 타겟 대기 (자산 이력 {len(rows)}/{need}일) → {LEVERAGE:g}배"
    v = [r[1] for r in rows]
    rets = [b / a - 1 for a, b in zip(v, v[1:]) if a]
    vol = statistics.stdev(rets) * (365 ** 0.5) * 100 if len(rets) > 1 else 0     # 코인은 24/7 → 365
    if vol <= 0:
        return LEVERAGE, f"변동성 타겟 대기 (실현 변동성 0) → {LEVERAGE:g}배"
    lev = min(LEVERAGE, VOL_TARGET_PCT / vol)
    return lev, f"변동성 타겟 실현 {vol:.0f}% / 목표 {VOL_TARGET_PCT:.0f}% → {lev:.2f}배"


def real_equity(ex):
    """거래소 실제 자산 (USDT). 모의 모드여도 키가 있으면 읽기만 한다. 조회 실패는 None."""
    try:
        return X.snapshot(ex or X.client())["equity"] if HAVE_KEYS else None
    except Exception as e:  # noqa: BLE001
        log.warning("실계좌 조회 실패: %s", X.explain(e))
        return None


# ---------- 하한 방어 (진입 타이밍) ----------
def floor_check(pub, level, since, side="long"):
    """돌파당한 선이 분봉에서 지켜지는가 → (진입해도 되는가, 사유).
    롱은 돌파당한 고가를 **하한**으로 지켜야 하고, 숏은 이탈당한 저가를 **상한**으로 눌러야 한다 (거울).

    backtest_entry.py (2026-09-08, OKX 2021-03~2026-09, 이벤트 107건):
      · 하한을 한 번도 잃지 않았거나 잃었다가 되찾았을 때만 사면 3배 +1,286% / MDD −34.8% (즉시 매수 +558% / −38.9%)
      · 창 안에 되찾지 못해 건너뛴 4건은 전부 손실 거래였다
      · CONF 6/7/8 × 파라미터 6종 × 전후반 = 54개 조합 전부에서 즉시 매수보다 좋았다 (backtest_entry.py sweep)
    '싸게 사려고 기다리는' 규칙이 아니다 — 지정가·되돌림 매수는 전부 즉시 매수보다 나빴다. 이건 자리가 무너졌는지 보는 규칙이다."""
    m = X.candles(pub, 300, "1m").iloc[:-1]                     # 진행 중인 분봉 제외
    m = m[m.index >= since]
    if m.empty:
        return False, "분봉 없음 → 다음 사이클에 다시 확인"
    last = float(m.close.iloc[-1])
    if side == "long":
        if float(m.low.min()) > level:
            return True, f"{len(m)}분간 하한 {level:,.0f} 유지 (최저 {m.low.min():,.0f})"
        if last > level:
            return True, f"하한 {level:,.0f} 이탈 후 재확보 (분봉 종가 {last:,.0f})"
        return False, f"하한 {level:,.0f} 아래 (분봉 종가 {last:,.0f}, 최저 {m.low.min():,.0f}) → 대기"
    if float(m.high.max()) < level:
        return True, f"{len(m)}분간 상한 {level:,.0f} 유지 (최고 {m.high.max():,.0f})"
    if last < level:
        return True, f"상한 {level:,.0f} 회복 후 재이탈 (분봉 종가 {last:,.0f})"
    return False, f"상한 {level:,.0f} 위 (분봉 종가 {last:,.0f}, 최고 {m.high.max():,.0f}) → 대기"


# ---------- Claude 검토 (매수 직전) ----------
def review_payload(sig, daily, h4, px, acc, notional, pub=None, lev=None, side="long"):
    """알고리즘이 계산한 값 + 그 근거 캔들. Claude 는 둘이 맞아떨어지는지, 사건·급변이 없는지 본다 (instructions.md).
    m1 = 신호 이후 분봉. 봇은 이 분봉으로 하한(돌파선) 방어를 확인하고 사므로 Claude 도 같은 것을 본다."""
    d = M.add_indicators(daily.copy()).tail(15)
    m1 = []
    if pub is not None and ENTRY_FLOOR:
        with contextlib.suppress(Exception):                     # 분봉 조회 실패가 검토 자체를 막지는 않는다
            m = X.candles(pub, 90, "1m").iloc[:-1].tail(60)
            m1 = [{"time": t.strftime("%H:%M"), "open": r.open, "high": r.high, "low": r.low, "close": r.close}
                  for t, r in m.iterrows()]
    return {"analysis": {k: v for k, v in sig.items() if k not in ("ret_5d", "ret_20d")},
            "proposal": {"action": "open", "side": side, "notional_usdt": round(notional, 2), "leverage": LEVERAGE if lev is None else round(lev, 2),
                         "equity_usdt": round(acc["equity"], 2), "price": px},
            "market": {"today_pct": round(px / daily.close.iloc[-1] * 100 - 100, 2), "ret_5d": sig["ret_5d"], "ret_20d": sig["ret_20d"]},
            "daily": [{"date": t.strftime("%m-%d"), "open": r.open, "high": r.high, "low": r.low, "close": r.close,
                       "chg_pct": round(r.close / r.open * 100 - 100, 1), "vol_btc": round(r.volume), "rsi": round(r.RSI_14),
                       "sma20": round(r.SMA_20), "sma50": round(r.SMA_50)} for t, r in d.iterrows()],
            "h4": [{"time": t.strftime("%m-%d %H:%M"), "open": r.open, "high": r.high, "low": r.low, "close": r.close}
                   for t, r in h4.tail(M.H4_N + 1).iterrows()],
            "entry": {"floor": M.entry_level(sig), "side": side,
                      "rule": ("롱: 돌파당한 고가(floor)를 분봉이 하한으로 지키거나 되찾을 때만 진입" if side == "long" else
                               "숏: 이탈당한 저가(floor)를 분봉이 상한으로 누르거나 다시 잃을 때만 진입")
                      + ", 다음 4h 봉 마감까지 못 지키면 자리 포기",
                      "m1": m1}}


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


def execute(ex, run_id, action, side, qty, notional, px, reason, demo=False, lev=None):
    """action: open|close. → 성공 여부 (True 면 장부·거래소가 같은 상태라고 확인된 것).

    실행 순서 (2026-09-10 감사로 뒤집었다). **주문이 먼저, 장부는 나중이다**:
      1) 의도(intent)를 clOrdId 와 함께 DB 에 먼저 적는다 — 여기서 죽어도 흔적이 남는다
      2) 거래소에 제출한다
      3) 그 clOrdId 로 **주문을 다시 조회해** 실제 체결 수량·평단을 읽는다
      4) 그 값으로 모의 장부를 확정한다
    예전에는 4)→2) 순서였다. 주문이 실패하면 장부에는 포지션이 있고 거래소에는 없었다(또는 반대).
    **타임아웃은 주문 실패가 아니다** — 예외가 나면 다시 조회하고, 그래도 모르면 status='unknown' 으로
    남긴 채 장부를 건드리지 않는다. 다음 사이클의 account() 대사가 거래소 쪽 진실로 맞춘다.
    demo=True (대시보드 데모 주문) 는 별도 계좌라 모의 장부를 건드리지 않는다."""
    mode = "demo" if demo else "live" if ex else "paper"
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
    if not demo:                                           # 체결을 확인한 **뒤에** 장부를 확정한다
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
def decide(pos, sig, seen_bar, paper):
    """상태기계 → (open | close | hold, 방향, 사유). 방향은 open·close 일 때만 의미가 있다.
    롱 = 상승 베팅, 숏 = 하락 베팅. 청산은 zone 이 아니라 들고 있는 쪽의 exit_long / exit_short 로 본다.
    paper=True 면 모의 장부의 강제청산도 흉내 낸다."""
    if pos:
        if paper and liquidated(pos):
            return "close", pos["side"], f"손실 {pos['pnl']:,.2f} USDT ≥ 증거금 → 강제청산 (모의 {pos.get('lev') or LEVERAGE:g}배)"
        if pos["side"] == "long" and sig["exit_long"]:
            why = (f"4h 종가 {sig['close']:,.0f} < 직전 {sig['h4_m']}봉 저가 {sig['lo']:,.0f} 이탈" if sig["bull_regime"]
                   else f"일봉 롱 {sig['bull']}/8 < {sig['conf']} 롱 국면 붕괴")
            return "close", "long", why + " → 청산"
        if pos["side"] == "short" and sig["exit_short"]:
            why = (f"4h 종가 {sig['close']:,.0f} > 직전 {sig['h4_m']}봉 고가 {sig['hi_m']:,.0f} 돌파" if sig["bear_regime"]
                   else f"일봉 숏 {sig['bear']}/8 < {sig['conf']} 숏 국면 붕괴")
            return "close", "short", why + " → 청산"
        line = sig["lo"] if pos["side"] == "long" else sig["hi_m"]
        return "hold", None, (f"{'롱' if pos['side'] == 'long' else '숏'} 보유 유지 (롱 {sig['bull']}/8 · 숏 {sig['bear']}/8 · "
                              f"4h {sig['bar']} 종가 {sig['close']:,.0f}, 청산선 {line:,.0f})")
    z = sig["zone"]
    if z in ("long", "short"):
        score = sig["extreme_hi"] if z == "long" else sig["extreme_lo"]
        if EXTREME_MIN and score < EXTREME_MIN:
            return "hold", None, (f"{'롱' if z == 'long' else '숏'} 자리지만 4h 극단 점수 {score}/{sig['extreme_rules']} "
                                  f"< {EXTREME_MIN} (EXTREME_MIN) → 관망. 과열되지 않은 돌파는 성적이 나빴다")
        if z == "short" and not ALLOW_SHORT:
            return "hold", None, (f"숏 자리지만 ALLOW_SHORT=0 이라 진입하지 않는다 (일봉 숏 {sig['bear']}/8 & 4h 종가 "
                                  f"{sig['close']:,.0f} < 직전 {sig['h4_n']}봉 저가 {sig['lo_n']:,.0f} 이탈) → 관망")
        if seen_bar == sig["bar"]:
            return "hold", None, f"4h {'롱' if z == 'long' else '숏'} 자리 {sig['bar']} 은 이미 판단함 → 다음 봉까지 관망"
        if z == "long":
            return "open", "long", (f"일봉 롱 {sig['bull']}/8 & 4h 종가 {sig['close']:,.0f} > 직전 {sig['h4_n']}봉 "
                                    f"고가 {sig['hi']:,.0f} 돌파")
        return "open", "short", (f"일봉 숏 {sig['bear']}/8 & 4h 종가 {sig['close']:,.0f} < 직전 {sig['h4_n']}봉 "
                                 f"저가 {sig['lo_n']:,.0f} 이탈")
    if sig["bull_regime"]:
        why = f"일봉 롱 {sig['bull']}/8 롱 국면이지만 4h 돌파 없음 (종가 {sig['close']:,.0f} ≤ {sig['hi']:,.0f})"
    elif sig["bear_regime"]:
        why = f"일봉 숏 {sig['bear']}/8 숏 국면이지만 4h 이탈 없음 (종가 {sig['close']:,.0f} ≥ {sig['lo_n']:,.0f})"
    else:
        why = f"국면 없음 (롱 {sig['bull']}/8 · 숏 {sig['bear']}/8, 둘 다 {sig['conf']} 미만)"
    return "hold", None, why + " → 관망"


def watch_start(sig):
    """Claude 승인이 난 자리를 '진입 대기' 로 걸어 둔다.
    지킬 선 = 돌파당한 선 (롱은 직전 12봉 고가, 숏은 직전 12봉 저가), 기한 = 다음 4h 봉 마감."""
    since = dt.datetime.strptime(sig["bar"], "%Y-%m-%d %H:%M").replace(tzinfo=KST) + dt.timedelta(hours=4)
    set_state(watch_bar=sig["bar"], watch_hi=M.entry_level(sig), watch_side=sig["zone"],
              watch_since=since.isoformat(timespec="seconds"))


def watch_stop():
    set_state(watch_bar=None, watch_hi=None, watch_since=None, watch_side=None)


def recheck(pub, px_seen, side, level):
    """주문 직전 마지막 확인 → (내도 되는가, 사유, 지금 가격).

    Claude 검토(최대 90초)와 분봉 조회 사이에 가격은 움직인다. 검토 시점에 읽은 값으로 수량을 확정하면
    최악의 경우 이미 진입선이 무너진 자리에 그 가격으로 주문을 낸다 (2026-09-10 감사).
    보는 것은 둘뿐이다 — 미끄러진 폭이 MAX_SLIP 안인가, 지켜야 할 선이 아직 지켜지고 있는가."""
    try:
        px = X.price(pub) if pub is not None else px_seen
    except Exception as e:  # noqa: BLE001
        return False, f"주문 직전 시세 조회 실패 ({X.explain(e)}) → 이번 자리는 접는다", px_seen
    if px_seen and abs(px / px_seen - 1) > MAX_SLIP:
        return False, (f"검토 시점 {px_seen:,.0f} → 현재 {px:,.0f} ({px / px_seen * 100 - 100:+.2f}%) "
                       f"로 {MAX_SLIP * 100:.1f}% 넘게 벌어졌다 → 진입 취소"), px
    if level:
        if side == "long" and px <= level:
            return False, f"주문 직전 현재가 {px:,.0f} 가 하한 {level:,.0f} 아래로 내려갔다 → 진입 취소", px
        if side == "short" and px >= level:
            return False, f"주문 직전 현재가 {px:,.0f} 가 상한 {level:,.0f} 위로 올라갔다 → 진입 취소", px
    return True, "", px


def act(ex, pub, run_id, sig, frames, px, acc):
    """decide → 진입 대기(분봉 방어) → **체결 직전 Claude 검토** → 주문. → (action, reason, claude 결과|None)

    Claude 는 '자리가 났을 때' 가 아니라 **정말 주문을 내기 직전에** 부른다 (2026-09-10 이동).
    예전에는 4h 신호가 나자마자 물어보고 승인되면 최대 4시간을 기다렸다 — 검토와 체결이 그만큼 벌어져
    판단이 낡았다. 지금은 분봉이 선을 지켜 '산다' 가 확정된 그 순간에 물어보므로 Claude 가 보는 분봉·현재가가
    실제 체결 조건과 같다. 늦어지는 비용은 측정했다 (backtest_latency.py):
      호출 지연 1~10분 구간의 성적 차이는 노이즈 폭 안이고(검증 Sharpe 1.18~1.29), Claude 타임아웃은 90초다.
    거부하면 그 자리는 접는다 (watch_stop + seen_bar) — 한 자리에 호출은 한 번뿐이다."""
    pos = acc["pos"]
    st = state()
    action, side, reason = decide(pos, sig, st["seen_bar"], paper=not ex)
    lev, lev_why = target_leverage(bool(ex))            # 변동성 타겟팅 — 이번 진입에 쓸 레버리지
    notional = acc["equity"] * POSITION_PCT * lev * (1 - CASH_RESERVE)   # 수수료·슬리피지·펀딩 낼 현금은 남긴다
    v = None
    if action == "close":
        watch_stop()
        if execute(ex, run_id, "close", pos["side"], pos["qty"], pos["qty"] * px, px, reason):
            acc = account(ex, px)
    fresh = action == "open"
    if fresh and ENTRY_FLOOR:
        watch_start(sig)                                        # 자리가 났다 → 대기로 넘기고 아래 공통 경로에서 선을 본다
        st, action = state(), "hold"
    if not pos and st["watch_bar"] and ENTRY_FLOOR:
        w = st["watch_side"] or "long"                          # 옛 DB 행에는 방향이 없다 (그때는 롱뿐이었다)
        since = dt.datetime.fromisoformat(st["watch_since"])
        if sig["exit_long"] if w == "long" else sig["exit_short"]:
            watch_stop()                                        # 기다리는 사이 그 방향의 청산 조건이 켜졌다 → 자리 무효
            reason = f"진입 대기 취소 ({w} · {st['watch_bar']}): {reason}"
        elif dt.datetime.now(KST) >= since + dt.timedelta(hours=4):
            watch_stop()
            reason = (f"진입 대기 만료 ({w} · 4h 자리 {st['watch_bar']} · "
                      f"{'하한' if w == 'long' else '상한'} {st['watch_hi']:,.0f} 미회복) → 자리 포기")
            log.info(reason)
        else:
            ok, why = floor_check(pub, st["watch_hi"], since, w)
            reason = (reason if fresh else f"진입 대기 ({w} · {st['watch_bar']})") + " · " + why
            action, side = ("open", w) if ok else ("hold", None)
            if ok:
                watch_stop()
    if action == "open" and entry_blocked():                     # 신규 진입만 막힌 상태 (사고 차단기 · autorun off)
        action, reason = "hold", "신규 진입 차단 중 (entry.off / 자동실행 OFF) — 보호·대사만 한다 · " + reason
        watch_stop()
    if action == "open" and USE_CLAUDE:                          # ← 체결 직전. 여기서 거부되면 그 자리는 끝
        v = claude_gate(review_payload(sig, *frames, px, acc, notional, pub, lev, side))
        if v["approve"]:
            reason += " · Claude 승인: " + v["reason"]
        else:
            action, reason = "hold", "Claude 거부: " + v["reason"]
            watch_stop()
    if action == "open":
        # 검토·분봉 확인 사이에 가격이 움직였다. 수량은 **지금** 가격으로 정하고, 자리가 아직 유효한지 다시 본다.
        # (2026-09-10 감사: 예전에는 사이클 맨 앞에서 읽은 px 로 수량을 확정하고 그대로 주문했다)
        ok, why2, px2 = recheck(pub, px, side, st["watch_hi"] if st["watch_hi"] else None)
        if not ok:
            action, reason = "hold", why2 + " · " + reason
            watch_stop()
        else:
            px = px2
            notional = acc["equity"] * POSITION_PCT * lev * (1 - CASH_RESERVE)
            execute(ex, run_id, "open", side, None, notional, px, reason + (" · " + lev_why if lev_why else ""), lev=lev)
    if sig["zone"] in ("long", "short") and not pos:
        set_state(seen_bar=sig["bar"])
    return action, reason, v


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
    tag = "데모" if X.DEMO else "실주문"
    mode = tag if live else f"모의 ({paper_trades_done()}/{LIVE_AFTER} 완료 후 {tag})" if MODE == "auto" and HAVE_KEYS else "모의"
    mode += f" · {source}"
    with db() as c:
        run_id = c.execute("INSERT INTO runs (timestamp, mode, status) VALUES (?, ?, 'running')", (now(), mode)).lastrowid
    log.info("=== run %d 시작 (%s%s) ===", run_id, mode, f" · Claude {CLAUDE_MODEL}" if USE_CLAUDE else "")
    try:
        ex = X.client() if live else None
        pub = ex or X.public()
        daily = X.candles(pub, CANDLES).iloc[:-1]                 # 진행 중인 봉 제외
        h4 = X.candles(pub, H4_CANDLES, "4h").iloc[:-1]
        sig = M.signal(daily, h4)
        px = X.price(pub)
        acc = account(ex, px)
        pos, z = acc["pos"], sig["zone"]
        log.info("%s %s USDT · 자리 %s (일봉 %s 롱 %d/8 · 숏 %d/8 · 4h %s 종가 %s · 롱선 %s / 숏선 %s) · 자산 %s USDT (모의 %s) · 포지션 %s",
                 X.COIN, f"{px:,.1f}", z, sig["date"], sig["bull"], sig["bear"], sig["bar"], f"{sig['close']:,.0f}",
                 f"{sig['hi']:,.0f}", f"{sig['lo_n']:,.0f}",
                 f"{acc['equity']:,.2f}", f"{acc['paper_equity']:,.2f}",
                 f"{pos['side']} {pos['held_days']}일 손익 {pos['pnl_pct']:+.1f}%" if pos else "없음")
        if day_loss_hit(acc["equity"], live):                             # 사고 차단기 — 신호와 무관하게 손 떼고 사람을 부른다
            reason = f"24시간 고점 대비 −{MAX_DAY_LOSS:g}% 이상 손실 → 청산 후 신규 진입 정지 (재개: make on)"
            log.critical(reason)
            action, v = "hold", None
            set_entry_block(True, "사고 차단기")                            # 신규 진입은 지금 즉시 막는다
            closed = bool(pos) and execute(ex, run_id, "close", pos["side"], pos["qty"], pos["qty"] * px, px, reason)
            if closed:
                action = "close"
            if pos and not closed:
                # 2026-09-10 감사: 예전에는 여기서도 set_autorun(False) 를 불러 스케줄러가 멈췄고,
                # 청산 실패한 포지션이 사람이 올 때까지 그대로 방치됐다. 진입만 막고 보호 루프는 계속 돌린다.
                reason += " · **비상 청산 실패 — 자동실행은 켜 둔 채 다음 사이클에 다시 시도합니다**"
                log.critical("비상 청산에 실패했습니다. 포지션이 남아 있으므로 자동실행을 끄지 않습니다. 즉시 확인하세요.")
            else:
                set_autorun(False)
        else:
            action, reason, v = act(ex, pub, run_id, sig, (daily, h4), px, acc)   # 판단 → Claude 검토 → 하한 방어 → 주문
        if action != "hold":
            acc = account(ex, px)
        with db() as c:
            c.execute("UPDATE runs SET status='done', equity=?, paper_equity=?, real_equity=?, price=?, zone=?, p=?, bull=?, bear=?, "
                      "position=?, action=?, reason=?, model=?, input_tokens=?, output_tokens=? WHERE id=?",
                      (acc["equity"], acc["paper_equity"], acc["equity"] if ex else real_equity(None), px, z, None,
                       int(sig["bull"]), int(sig["bear"]), pos["side"] if pos else None, action, reason,
                       *((v["model"], v["input_tokens"], v["output_tokens"]) if v else (None, None, None)), run_id))
        log.info("=== run %d 완료: %s — %s ===", run_id, action, reason)
    except Exception as e:  # noqa: BLE001
        log.error("run %d 실패: %s", run_id, X.explain(e))
        log.debug("스택", exc_info=True)
        with db() as c:
            c.execute("UPDATE runs SET status=? WHERE id=?", (f"error: {X.explain(e)}"[:300], run_id))


def confirm_live():
    """진짜 돈이 나갈 수 있는 설정이면 사람이 직접 '실주문' 을 치게 한다. 데모(OKX_DEMO=1)는 묻지 않는다. autotrade·dashboard 공용."""
    if not (HAVE_KEYS and MODE != "paper" and not X.DEMO):
        return
    msg = f"OKX 실계좌 주문 모드입니다 (OKX_DEMO=0, MODE={MODE}{', 모의 %d회 완료 후 자동 전환' % LIVE_AFTER if MODE == 'auto' else ''}, {LEVERAGE:g}배). '실주문' 을 입력하면 계속합니다: "
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


def schedule_forever():
    """시작 즉시 한 사이클, 이후 INTERVAL_MIN 분마다. 대시보드는 이걸 백그라운드 스레드로 돌린다.
    벽시계 기준으로 다음 시각을 정하고 30초씩 자므로, 재시작·PC 절전 뒤에도 늦은 사이클을 바로 따라잡는다."""
    global NEXT_RUN
    if not single_instance():
        log.critical("스케줄러가 이미 다른 프로세스에서 돌고 있습니다 — 이 프로세스는 자동 판단을 하지 않습니다 (make stop 후 하나만 띄우세요)")
        return
    while True:
        NEXT_RUN = dt.datetime.now(KST) + dt.timedelta(minutes=INTERVAL_MIN)
        holding = bool(state()["side"])
        if autorun():
            src = "자동"
        elif PROTECT_WHEN_OFF and holding:
            # 자동실행 OFF 는 '신규 진입 정지' 다. 포지션이 남아 있으면 청산·대사는 계속해야 한다
            # (act() 의 entry_blocked() 가 신규 진입만 막는다). 2026-09-10 감사.
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
        log.info("다음 실행 %s", NEXT_RUN.strftime("%H:%M"))
        while dt.datetime.now(KST) < NEXT_RUN:
            time.sleep(30)


def manual_order(action, demo=False):
    """대시보드에서 사람이 직접 누른 실주문. auto 모드의 '모의 N회' 게이트와 무관하게 바로 나간다.
    자동 매매와 같은 execute() 를 타므로 주문 기록·장부가 그대로 이어진다."""
    if not X.have_keys(demo):
        raise ValueError(f"OKX {'데모' if demo else '실계좌'} API 키가 없습니다")
    if not single_instance():
        raise RuntimeError("주문을 낼 수 있는 다른 프로세스가 이미 돌고 있습니다 (make stop 후 하나만 띄우세요)")
    if not _CYCLE.acquire(timeout=30):        # 자동 사이클과 겹치면 같은 포지션에 주문이 두 번 나간다
        raise RuntimeError("자동 사이클이 돌고 있습니다 — 잠시 뒤 다시 누르세요")
    try:
        return _manual_order(action, demo)
    finally:
        _CYCLE.release()


def _manual_order(action, demo=False):
    if not demo:
        watch_stop()          # 사람이 직접 주문했으면 대기 중이던 자리는 무효 (남겨 두면 나중에 혼자 진입한다)
    if MODE == "paper" and not demo:
        raise ValueError("MODE=paper 에서는 실주문을 보내지 않습니다 (.env 의 MODE 를 auto 나 live 로). 데모 주문은 그대로 됩니다")
    ex = X.client(demo)
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
                            f"대시보드 수동 {'데모' if demo else '실'}주문")).lastrowid
    if action == "buy":
        ok = execute(ex, run_id, "open", "long", None, snap["cash"] * POSITION_PCT * LEVERAGE, px,
                     f"대시보드 수동 매수{' (데모)' if demo else ''}", demo)
    else:
        ok = execute(ex, run_id, "close", pos["side"], pos["qty"], pos["qty"] * px, px,
                     f"대시보드 수동 매도{' (데모)' if demo else ''}", demo)
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
