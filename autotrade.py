"""RLS(레짐 롱/숏 보유) 자동매매 — OKX USDT 무기한 선물 BTC-USDT-SWAP · ETH-USDT-SWAP, 종목별 독립.
규칙: rls.py (명세 auto_okx_trading_start/research/RLS_model_prompt_10x.md 를 바꾸지 않는다). 거래소: okx.py.
10x 는 FWD-v2 동결 범위(5x) 밖이고 전진 관찰로 검증되지 않았다. 과거엔 손절보다 강제청산이 먼저 온 경우가 대부분.

하루 1회 00:05 UTC:  1 락 → 2 완결 일봉 → 3 레짐 → 4 거래소 포지션·손절 조회 → 5 목표와 비교 → 6 필요한 주문만 → 7 기록
리스크: 격리 10x, 증거금 = 자산의 2% (명목 20%), 동시 증거금 합 ≤ 10%, 전일 대비 −5% 면 신규 진입 중단,
       종목 자산 고점 낙폭 > 20% 면 그 종목 청산·정지, 같은 종목 강제청산 3회 연속이면 신규 진입 중단
       (둘 다 --resume 으로만 재개), 거래 손실 > 자산 3% 면 이상 경보.
STOP_MODE: A = 진입가 ∓ 3×ATR (백테스트와 동일, 기본) | B = min(3×ATR, 청산 거리 − 1.5%p) (검증 안 됨, 장부 분리)
MODE (명시적 설정으로만 전환): dry = 주문 없이 장부만 (기본) | demo = OKX 데모 거래 | live = 실거래

사용:  python autotrade.py --once            1회 실행 (cron 권장:  5 0 * * *  UTC)
       python autotrade.py                   매일 00:05 UTC 실행
       python autotrade.py --resume BTC-USDT-SWAP   킬 스위치 정지 해제 (수동 확인 후)
"""
import contextlib
import datetime as dt
import fcntl
import json
import logging
import os
import pathlib
import sqlite3
import sys
import time

import pandas as pd
import requests
from dotenv import load_dotenv

import okx
import rls

load_dotenv()
ROOT = pathlib.Path(__file__).resolve().parent
MODE = os.environ.get("MODE", "dry")
INSTS = ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
STOP_MODE = os.environ.get("STOP_MODE", "A")
if STOP_MODE not in ("A", "B"):
    raise ValueError(f"STOP_MODE={STOP_MODE} (A | B)")
TAG = MODE + ("_B" if STOP_MODE == "B" else "")      # B안은 A안 기록과 분리
DB_PATH = ROOT / f"rls_{TAG}.db"
LOCK_PATH = ROOT / f"rls_{MODE}.lock"
RUN_AT = (0, 5)                       # UTC
MAX_TOTAL_MARGIN, DAILY_LOSS, KILL_DD, ANOMALY, LIQ_STREAK = 0.10, 0.05, 0.20, 0.03, 3
LIQ_TYPES = ("3", "4")                # positions-history type: 강제청산 / 일부 강제청산
WARMUP_DAYS = 100                     # SMA50 + 기울기 20 + ATR 여유

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout),
                              logging.FileHandler(ROOT / "autotrade.log", encoding="utf-8")])
log = logging.getLogger("autotrade")


# ---------- 저장 ----------
@contextlib.contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
    CREATE TABLE IF NOT EXISTS daily (date TEXT, inst TEXT, regime INTEGER, target INTEGER, actual INTEGER,
        fill_px REAL, slip_bps REAL, fee REAL, fee_cum REAL, funding_cum REAL, stop_px REAL, equity REAL,
        sym_equity REAL, dd REAL, ret REAL, bh_ret REAL, r4_ret REAL, bt_ret REAL, flags TEXT, note TEXT, ts TEXT,
        liq_px REAL, sl_beyond_liq INTEGER, bt5_ret REAL, PRIMARY KEY (date, inst));
    CREATE TABLE IF NOT EXISTS orders (ts TEXT, inst TEXT, cid TEXT, action TEXT, side INTEGER, sz REAL,
        avg_px REAL, fee REAL, state TEXT);""")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(daily)")}
    for col, typ in (("liq_px", "REAL"), ("sl_beyond_liq", "INTEGER"), ("bt5_ret", "REAL")):   # 5x 시절 DB
        if col not in cols:
            conn.execute(f"ALTER TABLE daily ADD COLUMN {col} {typ}")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def kv_get(k, default=None):
    with db() as c:
        r = c.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return json.loads(r[0]) if r else default


def kv_set(k, v):
    with db() as c:
        c.execute("INSERT OR REPLACE INTO kv VALUES (?, ?)", (k, json.dumps(v)))


@contextlib.contextmanager
def lock():
    f = open(LOCK_PATH, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        raise RuntimeError("이미 실행 중 (락 획득 실패)") from None
    try:
        yield
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


def alert(msg):
    log.error("ALERT %s", msg)
    url = os.environ.get("ALERT_WEBHOOK_URL")              # Slack(text) / Discord(content) 웹훅
    if url:
        try:
            requests.post(url, json={"text": f"[RLS {TAG} 10x] {msg}", "content": f"[RLS {TAG} 10x] {msg}"}, timeout=10)
        except Exception as e:  # noqa: BLE001
            log.error("경보 전송 실패: %s", e)


def make_client():
    if MODE == "dry":
        return okx.Paper(okx.Client(), kv_get("paper", {}), float(os.environ.get("PAPER_EQUITY_USDT", 10_000)))
    if MODE not in ("demo", "live"):
        raise ValueError(f"MODE={MODE} (dry | demo | live)")
    k, s, p = (os.environ.get(x) for x in ("OKX_API_KEY", "OKX_API_SECRET", "OKX_API_PASSPHRASE"))
    if not (k and s and p):
        raise ValueError("OKX_API_KEY / OKX_API_SECRET / OKX_API_PASSPHRASE 가 없습니다")
    return okx.Client(k, s, p, demo=MODE == "demo")


def ms(t):
    return int(pd.Timestamp(t).timestamp() * 1000)


# ---------- 한 종목 ----------
def trade(ex, inst, now, equity, avail, used_margin, no_entry):
    """한 종목을 목표 상태로 맞춘다. 반환: 이 종목이 지금 쓰는 증거금."""
    coin, today = inst.split("-")[0], pd.Timestamp(now.date())
    day = today.strftime("%Y%m%d")
    s = kv_get(inst) or {"start_eq": equity, "start_day": str(today.date()), "peak": equity, "realized": 0.0,
                         "fee": 0.0, "funding": 0.0, "hist_ts": ms(now), "side": 0, "stop_n": 0}
    flags, notes, fill = [], [], None

    def absorb():                                           # 닫힌 포지션의 손익·수수료·펀딩을 종목 장부에 누적
        for h in ex.positions_history(inst, s["hist_ts"]):
            s["realized"] += h["realizedPnl"]
            s["fee"] += h["fee"]
            s["funding"] += h["fundingFee"]
            s["hist_ts"] = max(s["hist_ts"], h["uTime"])
            s["last_close_ts"] = h["uTime"]
            s["last_close_type"] = h["type"]
            s["liq_streak"] = s.get("liq_streak", 0) + 1 if h["type"] in LIQ_TYPES else 0
            if s["liq_streak"] >= LIQ_STREAK and not s.get("liq_hold"):
                s["liq_hold"] = True
                alert(f"{inst} 강제청산 {s['liq_streak']}회 연속 → 신규 진입 중단. 확인 후 --resume {inst}")
            if h["realizedPnl"] < -ANOMALY * equity:
                flags.append("anomaly")
                alert(f"{inst} 거래 손실 {h['realizedPnl']:,.2f} > 자산의 {ANOMALY:.0%}")

    # 2·3) 완결 일봉 → 레짐 (since 는 시작일 이전 워밍업까지 포함해 기준선 계산에도 쓴다)
    start = min(today, pd.Timestamp(s["start_day"])) - pd.Timedelta(days=WARMUP_DAYS)
    d = rls.daily(ex.candles(inst, ms(start)))
    reg, atr = rls.regime(d), rls.atr(d)
    gap = d.empty or d.index[-1] != today - pd.Timedelta(days=1) or len(d) < rls.MIN_DAYS or pd.isna(atr.iloc[-1])
    r = None if gap else int(reg.iloc[-1])
    if gap:
        flags.append("data_gap")
        notes.append("완결 일봉 없음 → 신호 갱신 없이 기존 포지션·손절만 유지")

    # 4) 거래소 실제 상태
    absorb()
    pos = ex.position(inst)
    if s["side"] and not pos:                               # 봇이 닫지 않았는데 사라짐 = 거래소 손절 또는 강제청산(·수동)
        liq = s.get("last_close_type") in LIQ_TYPES
        flags.append("liquidated" if liq else "stop_hit")
        s["stop_side"] = s["side"]
        s["stop_day"] = str(pd.Timestamp(s.get("last_close_ts", ms(now)), unit="ms").date())
        alert(f"{inst} {'롱' if s['side'] > 0 else '숏'} 포지션이 거래소에서 종료됨 ({'강제청산' if liq else '손절 추정'})"
              " — 같은 방향은 새 레짐 에지까지 재진입 금지")
        s.update(side=0, entry=None, stop=None)
    if pos and pos["side"] != s["side"]:
        alert(f"{inst} 장부({s['side']})와 거래소 포지션({pos['side']}) 불일치 → 거래소 기준으로 맞춤")
        s.update(side=pos["side"], entry=pos["avgPx"], stop=None)
    if pos and pos.get("mgnMode") != "isolated":
        alert(f"{inst} 포지션이 격리가 아님({pos.get('mgnMode')})")
    if pos and pos.get("lever") and pos["lever"] != rls.LEVER:
        alert(f"{inst} 포지션 레버리지 {pos['lever']:g}x ≠ {rls.LEVER}x")

    # 종목 자산·낙폭 → 킬 스위치
    sym_eq = s["start_eq"] + s["realized"] + ((pos["upl"] + pos["realizedPnl"]) if pos else 0.0)
    s["peak"] = max(s.get("peak") or sym_eq, sym_eq)
    dd = 1 - sym_eq / s["peak"]
    if dd > KILL_DD and not s.get("halted"):
        s["halted"] = True
        alert(f"{inst} 킬 스위치: 고점 대비 낙폭 {dd:.1%} > {KILL_DD:.0%} → 청산·정지. 확인 후 --resume {inst}")

    # 5) 목표
    cur = pos["side"] if pos else 0
    if s.get("halted"):
        target = 0
        flags.append("halted")
    elif gap:
        target = cur
    else:
        target = r
        if target and target == s.get("stop_side") and rls.blocked(reg, target, s["stop_day"]):
            target = 0
            notes.append("손절 후 같은 방향 레짐 지속 → 새 에지 대기")
    if target and target != cur and no_entry:
        target = 0
        flags.append("daily_loss")
        notes.append(f"전일 대비 −{DAILY_LOSS:.0%} 이상 → 신규 진입 중단")
    if target and target != cur and s.get("liq_hold"):
        target = 0
        flags.append("liq_hold")
        notes.append(f"강제청산 {LIQ_STREAK}회 연속 → 수동 확인 전까지 신규 진입 없음")

    # 6) 필요한 주문만
    if cur and cur != target:
        if ex.stops(inst):
            ex.cancel_stops(inst, [x["algoId"] for x in ex.stops(inst)])
        fill = ex.order(inst, -cur, pos["sz"], f"{day}{coin}{'kill' if s.get('halted') else 'close'}", reduce=True)
        log_order(inst, fill, "close", -cur)
        pos = ex.position(inst)
        if pos:
            raise RuntimeError(f"{inst} 청산 미확인 — 진입하지 않음")
        s.update(side=0, entry=None, stop=None)
        cur = 0
        absorb()
    opened = False
    if target and not cur:
        ins, tk = ex.instrument(inst), ex.ticker(inst)
        margin = equity * rls.MARGIN
        sz = okx.floor_to(margin * rls.LEVER / (ins["ctVal"] * tk["last"]), ins["lotSz"])
        why = ("레버리지 > 거래소 최대" if rls.LEVER > ins["lever"] else
               f"수량 {sz} < 최소 {ins['minSz']}" if sz < ins["minSz"] else
               "증거금 > 자산 2%" if sz * ins["ctVal"] * tk["last"] / rls.LEVER > rls.MARGIN * equity + 1e-9 else
               "동시 증거금 합 > 자산 10%" if used_margin + margin > MAX_TOTAL_MARGIN * equity + 1e-9 else
               "가용 증거금 부족" if margin > avail else None)
        if not why:
            ex.set_leverage(inst, rls.LEVER)
            li = ex.leverage_info(inst)                         # 실제 적용 여부를 조회로 확인
            if li["lever"] != rls.LEVER or li["mgnMode"] != "isolated":
                why = f"set-leverage 미적용 (조회 {li['mgnMode']} {li['lever']:g}x)"
        if why:
            flags.append("order_check_fail")
            notes.append(f"주문 검증 실패: {why} → 주문 안 함")
            alert(f"{inst} 주문 검증 실패: {why}")
        else:
            fill = ex.order(inst, target, sz, f"{day}{coin}open")
            log_order(inst, fill, "open", target)
            pos = ex.position(inst)
            if not pos:
                raise RuntimeError(f"{inst} 진입 체결 확인 실패")
            stop = rls.stop_price(target, pos["avgPx"], float(atr.iloc[-1]), liq_for_b(pos))
            s.update(side=target, entry=pos["avgPx"], stop=stop, entry_day=str(today.date()))
            fill["slip_bps"] = target * (fill["avgPx"] / tk["open_utc0"] - 1) * 1e4   # 백테스트 체결가(당일 시가) 대비
            cur, opened = target, True

    # 손절 보장 (거래소 조건부 주문). 없거나 수량·가격이 다르면 재설정 + 경보
    if pos:
        if not s.get("stop"):
            s["stop"] = rls.stop_price(pos["side"], pos["avgPx"], float(atr.dropna().iloc[-1]), liq_for_b(pos))
            alert(f"{inst} 손절가 기록 없음 → 평단 기준 {STOP_MODE}안으로 설정 {s['stop']:,.2f}")
        tick = ex.instrument(inst)["tickSz"]
        want = round(round(s["stop"] / tick) * tick, 8)
        stops = ex.stops(inst)
        good = [x for x in stops if abs(x["sz"] - pos["sz"]) < 1e-9 and x["side"] == -pos["side"]
                and abs(x["trigger"] - want) < tick / 2]
        if not (len(stops) == 1 and good):
            if not opened:
                flags.append("stop_reset")
                alert(f"{inst} 손절 주문 없음/불일치 ({len(stops)}개) → 재설정 {want:,.2f}")
            if stops:
                ex.cancel_stops(inst, [x["algoId"] for x in stops])
            s["stop_n"] = s.get("stop_n", 0) + 1
            try:
                ex.place_stop(inst, pos["side"], pos["sz"], want, f"{day}{coin}sl{s['stop_n']}")
            except Exception as e:  # noqa: BLE001
                alert(f"{inst} 손절 주문 실패({e}) → 손절 없이 보유하지 않도록 즉시 청산")
                fill = ex.order(inst, -pos["side"], pos["sz"], f"{day}{coin}nostop", reduce=True)
                log_order(inst, fill, "close", -pos["side"])
                s.update(side=0, entry=None, stop=None)
                pos, cur = None, 0
                flags.append("stop_fail")
    elif ex.stops(inst):                                     # 포지션 없는데 남은 손절 = 정리
        ex.cancel_stops(inst, [x["algoId"] for x in ex.stops(inst)])

    # 7) 기록
    absorb()
    pos = ex.position(inst)
    sym_eq = s["start_eq"] + s["realized"] + ((pos["upl"] + pos["realizedPnl"]) if pos else 0.0)
    liq_px = pos["liqPx"] if pos else None
    sl_beyond = rls.beyond_liq(pos["side"], s["stop"], liq_px) if pos and s.get("stop") else None
    if sl_beyond:
        flags.append("sl_beyond_liq")
    since = d[d.index >= pd.Timestamp(s["start_day"])]
    base = lambda eq: float(eq.iloc[-1] / eq[eq.index < pd.Timestamp(s["start_day"])].iloc[-1] - 1) if len(since) else 0.0  # noqa: E731
    row = {"date": str(today.date()), "inst": inst, "regime": r, "target": target, "actual": cur,
           "fill_px": fill and fill["avgPx"], "slip_bps": fill and fill.get("slip_bps"), "fee": fill and fill["fee"],
           "fee_cum": s["fee"] + (pos["fee"] if pos else 0), "funding_cum": s["funding"] + (pos["fundingFee"] if pos else 0),
           "stop_px": s.get("stop"), "liq_px": liq_px, "sl_beyond_liq": sl_beyond, "equity": equity, "sym_equity": sym_eq, "dd": 1 - sym_eq / s["peak"],
           "ret": sym_eq / s["start_eq"] - 1,
           "bh_ret": float(d.close.iloc[-1] / since.close.iloc[0] - 1) if len(since) else 0.0,   # 1x 매수보유
           "r4_ret": base(rls.simulate(d, allow=(1,))[0]),                                          # BULL 롱만
           "bt_ret": base(rls.simulate(d, stop_mode=STOP_MODE)[0]), "bt5_ret": base(rls.simulate(d, lever=5)[0]),  # 10x / 같은 규칙 5x
           "flags": ",".join(flags), "note": " · ".join(notes), "ts": dt.datetime.now(dt.UTC).isoformat(timespec="seconds")}
    with db() as c:
        keep = ("fill_px", "slip_bps", "fee")                   # 같은 날 재실행이 앞선 체결 기록을 지우지 않게
        upd = ", ".join(f"{k}=COALESCE(excluded.{k}, daily.{k})" if k in keep else f"{k}=excluded.{k}" for k in row)
        c.execute(f"INSERT INTO daily ({', '.join(row)}) VALUES ({', '.join('?' * len(row))}) "
                  f"ON CONFLICT(date, inst) DO UPDATE SET {upd}", list(row.values()))
    kv_set(inst, s)
    log.info("%s 레짐 %s → 목표 %s / 실제 %s · 손절 %s · 청산 %s · 종목자산 %.2f (낙폭 %.1f%%) %s %s", inst, r, target, cur,
             f"{s['stop']:,.2f}" if s.get("stop") else "-", f"{liq_px:,.2f}" if liq_px else "-", sym_eq, row["dd"] * 100, row["flags"], row["note"])
    return pos["margin"] if pos else 0.0


def liq_for_b(pos):
    """B안이면 거래소 청산가, A안이면 None (손절 = 3×ATR 그대로)."""
    if STOP_MODE != "B":
        return None
    if not pos.get("liqPx"):
        raise RuntimeError("B안인데 거래소 청산가(liqPx) 조회 실패")
    return pos["liqPx"]


def log_order(inst, o, action, side):
    with db() as c:
        c.execute("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), inst, o["cid"], action, side, o["sz"],
                   o["avgPx"], o["fee"], o["state"]))
    log.info("[%s] %s %s %s %s @ %s", MODE, o["cid"], action, side, o["sz"], o["avgPx"])


# ---------- 한 사이클 ----------
def run_cycle(ex=None, now=None):
    now = now or dt.datetime.now(dt.UTC)
    with lock():
        ex = ex or make_client()
        try:
            if isinstance(ex, okx.Paper):
                ex.sync()
            if ex.config().get("posMode") != "net_mode":
                raise RuntimeError("OKX 계정을 net(단방향) 포지션 모드로 설정해야 합니다")
            equity, avail = ex.balance()
            a = kv_get("acct", {})
            today = str(now.date())
            if a.get("date") != today:
                a = {"date": today, "prev": a.get("equity")}
            a["equity"] = equity
            kv_set("acct", a)
            # ponytail: 00:05 하루 1회 실행이라 '전일 종가 자산' = 직전 실행일 자산으로 본다
            no_entry = bool(a.get("prev")) and equity < a["prev"] * (1 - DAILY_LOSS)
            margins = {i: (p or {}).get("margin", 0.0) for i in INSTS for p in [ex.position(i)]}
            log.info("=== %s · 10x 손절 %s안 · 자산 %.2f USDT (가용 %.2f)%s ===", MODE, STOP_MODE, equity, avail, " · 신규 진입 중단" if no_entry else "")
            for inst in INSTS:
                try:
                    others = sum(v for k, v in margins.items() if k != inst)
                    margins[inst] = trade(ex, inst, now, equity, avail, others, no_entry)
                except Exception as e:  # noqa: BLE001
                    log.exception("%s 실패", inst)
                    alert(f"{inst} 실행 실패, 주문 없이 종료: {e}")
        finally:
            if isinstance(ex, okx.Paper):
                kv_set("paper", ex.st)


def schedule_forever():
    while True:
        n = dt.datetime.now(dt.UTC)
        nxt = n.replace(hour=RUN_AT[0], minute=RUN_AT[1], second=0, microsecond=0)
        if nxt <= n:
            nxt += dt.timedelta(days=1)
        log.info("다음 실행 %s UTC", nxt.isoformat(timespec="minutes"))
        time.sleep((nxt - n).total_seconds())
        try:
            run_cycle()
        except Exception as e:  # noqa: BLE001
            alert(f"사이클 실패: {e}")


if __name__ == "__main__":
    if "--resume" in sys.argv:
        inst = sys.argv[sys.argv.index("--resume") + 1]
        s = kv_get(inst)
        if s.get("halted"):
            s["peak"] = None                                    # 재개 시점 자산을 새 고점으로
        s.update(halted=False, liq_hold=False, liq_streak=0)
        kv_set(inst, s)
        sys.exit(f"{inst} 정지 해제")
    run_cycle()
    if "--once" not in sys.argv:
        schedule_forever()
