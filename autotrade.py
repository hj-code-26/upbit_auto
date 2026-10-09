"""RLS 20x DROP1 자동매매 — OKX USDT 무기한 선물 BTC-USDT-SWAP · ETH-USDT-SWAP, 종목별 독립 신호, 한 계좌.
규칙: rls.py (명세를 바꾸지 않는다). 거래소: okx.py. 과거 백테스트의 사후 탐색 결과이며 수익을 보장하지 않는다.

매시 HH:02 UTC (1H 확정봉 반영 후):
  1 락 → 2 확정봉·일봉·레짐·ATR → 3 거래소 포지션·레버리지·마진 모드·손절·청산가 조회
  → 4 위험 한도 → 레짐 청산/진입 → DROP1 → 본전 이동 → 5 필요한 주문만 → 6 기록
리스크: 격리 20x, 증거금 = 자산의 4% (명목 80%, 종목당), 동시 증거금 합 ≤ 10%.
       당일 UTC 시작 자산 대비 −5% → 전 포지션 청산 + 그날 신규 진입 중단.
       고점 대비 낙폭 35% → 전량 청산·정지 / 강제청산 1건 → 신규 진입 중단 / 같은 종목 10연패 → 그 종목 신규 진입 중단
       (모두 --resume 으로만 재개). 7연패 경보, 거래 손실 > 자산 4% 이상 경보.
MODE (명시적 설정으로만 전환): dry = 주문 없이 장부만 (기본) | demo = OKX 데모 거래 | live = 실거래

사용:  python autotrade.py --once     1회 실행 (cron 권장:  2 * * * *  UTC)
       python autotrade.py            매시 HH:02 UTC 실행
       python autotrade.py --resume   정지·진입 중단 해제 (수동 확인 후)
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
DB_PATH = ROOT / f"rls20_{MODE}.db"
LOCK_PATH = ROOT / f"rls20_{MODE}.lock"
RUN_MINUTE = 2                        # 매시 HH:02 UTC
MAX_TOTAL_MARGIN, KILL_DD, ANOMALY, STREAK_ALERT, STREAK_HOLD = 0.10, 0.35, 0.04, 7, 10
LIQ_TYPES = ("3", "4")                # positions-history type: 강제청산 / 일부 강제청산
EDGE_WHY = ("sl", "liq", "drop1", "daily_loss", "halt")   # 이 사유로 정리되면 새 레짐 에지까지 재진입 금지
WARMUP_DAYS = 100                     # SMA50 + 기울기 20 + CONF3 + ATR 여유
BASELINES = {"bt20_ret": {}, "bt10_ret": {"lever": 10}, "bt7_ret": {"lever": 7}}   # 같은 규칙 다른 레버리지

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
    CREATE TABLE IF NOT EXISTS daily (date TEXT, inst TEXT, regime INTEGER, conf INTEGER, actual INTEGER,
        equity REAL, dd REAL, loss_streak INTEGER, stop_px REAL, liq_px REAL, be INTEGER, fee_cum REAL,
        funding_cum REAL, bh_ret REAL, rls5_ret REAL, bt20_ret REAL, bt10_ret REAL, bt7_ret REAL,
        flags TEXT, note TEXT, ts TEXT, PRIMARY KEY (date, inst));
    CREATE TABLE IF NOT EXISTS trades (inst TEXT, side INTEGER, entry_ts TEXT, entry_px REAL, slip_bps REAL,
        exit_ts TEXT, exit_px REAL, stop_dist REAL, liq_px REAL, be INTEGER, be_ts TEXT, why TEXT, fee REAL,
        funding REAL, pnl REAL, pnl_pct REAL);
    CREATE TABLE IF NOT EXISTS orders (ts TEXT, inst TEXT, cid TEXT, action TEXT, side INTEGER, sz REAL,
        avg_px REAL, fee REAL, state TEXT);""")
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
            requests.post(url, json={"text": f"[RLS20 {MODE}] {msg}", "content": f"[RLS20 {MODE}] {msg}"}, timeout=10)
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


def iso(t_ms):
    return pd.Timestamp(t_ms, unit="ms").isoformat(timespec="minutes")


def log_order(inst, o, action, side):
    with db() as c:
        c.execute("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), inst, o["cid"], action, side, o["sz"],
                   o["avgPx"], o["fee"], o["state"]))
    log.info("[%s] %s %s %s %s @ %s", MODE, o["cid"], action, side, o["sz"], o["avgPx"])


def baselines(h1, start_day):
    """시작일 이후 같은 기간의 가상 성과: 같은 규칙 20x/10x/7x, RLS 5x 2%(FWD-v2), 1x 매수보유."""
    d = rls.daily(h1)
    if not len(d[d.index >= start_day]) or not len(d[d.index < start_day]):
        return {}
    out = {k: float(rls.simulate(h1, start=start_day, **kw)[0].iloc[-1] - 1) for k, kw in BASELINES.items()}
    out["rls5_ret"] = float(rls.simulate_rls5(d, start=start_day).iloc[-1] - 1)
    out["bh_ret"] = float(d.close.iloc[-1] / d.close[d.index < start_day].iloc[-1] - 1)
    return out


# ---------- 한 종목 ----------
def trade(ex, inst, now, equity, avail, used_margin, risk, entry_ok, a):
    """한 종목을 목표 상태로 맞춘다. risk = 'halt' | 'daily_loss' | None (계좌 단위). 반환: 이 종목이 쓰는 증거금."""
    coin, today = inst.split("-")[0], str(now.date())
    hk = now.strftime("%Y%m%d%H")
    s = kv_get(inst) or {"start_day": today, "hist_ts": ms(now), "side": 0, "streak": 0, "stop_n": 0,
                         "fee": 0.0, "funding": 0.0, "blk": None, "new": True}
    flags, notes, opened, moved = [], [], False, False

    def absorb():                                           # 닫힌 포지션 → 거래 기록·연패·강제청산·이상 손실
        for h in ex.positions_history(inst, s["hist_ts"]):
            s["hist_ts"] = max(s["hist_ts"], h["uTime"])
            s["fee"] += h["fee"]
            s["funding"] += h["fundingFee"]
            liq = h["type"] in LIQ_TYPES
            why = "liq" if liq else s.pop("exit_why", None) or "sl"
            t = s.pop("trade", None) or {}
            pnl = h["realizedPnl"]
            with db() as c:
                c.execute("INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                          (inst, h["side"], t.get("entry_ts"), t.get("entry_px"), t.get("slip_bps"), iso(h["uTime"]),
                           h["closeAvgPx"], t.get("stop_dist"), t.get("liq_px"), int(bool(t.get("be_ts"))),
                           t.get("be_ts"), why, h["fee"], h["fundingFee"], pnl, pnl / t.get("eq0", equity)))
            s["streak"] = s["streak"] + 1 if pnl <= 0 else 0
            if s["streak"] == STREAK_ALERT:
                alert(f"{inst} {STREAK_ALERT}연패 (과거 최장 수준) — 전략 변경 없이 기록")
            if s["streak"] >= STREAK_HOLD and not s.get("streak_hold"):
                s["streak_hold"] = True
                alert(f"{inst} {s['streak']}연패 → 이 종목 신규 진입 중단. 확인 후 --resume")
            if pnl < -ANOMALY * equity:
                flags.append("anomaly")
                alert(f"{inst} 거래 손실 {pnl:,.2f} > 자산의 {ANOMALY:.0%}")
            if liq:
                flags.append("liquidated")
                a["hold"] = f"{inst} 강제청산 {iso(h['uTime'])}"
                alert(f"{inst} 강제청산 발생 (설계상 0건이어야 함) → 전 종목 신규 진입 중단. 확인 후 --resume")

    # 2) 확정봉 → 일봉·레짐·ATR (그날 첫 회차는 기준선 계산용으로 시작일 이전부터)
    first_today = s.get("base_day") != today
    since = (min(pd.Timestamp(today), pd.Timestamp(s["start_day"])) if first_today else pd.Timestamp(now))
    h1 = ex.candles(inst, ms(since - pd.Timedelta(days=WARMUP_DAYS)))
    h1 = h1[h1.confirm == 1]
    last_bar = ms(pd.Timestamp(now).floor("h")) - rls.H_MS
    x = rls.signals(h1) if len(h1) else None
    row = x.loc[last_bar] if x is not None and last_bar in x.index else None
    gap = (row is None or pd.isna(row.want) or pd.isna(row.atr) or pd.isna(row.c24)
           or len(rls.daily(h1)) < rls.MIN_DAYS)
    conf = None if gap else int(row.want)
    if gap:
        flags.append("data_gap")
        notes.append("확정봉/일봉 공백 → 신호 갱신 없이 기존 포지션·손절만 유지")
    elif s.pop("new", False) and conf:
        s["blk"] = conf                                       # 시작 시점에 이미 켜진 레짐은 건너뛰고 다음 에지 대기
        notes.append("시작: 진행 중인 레짐은 건너뛰고 새 에지 대기")
    if not gap and s.get("blk") and conf != s["blk"]:
        s["blk"] = None                                       # 확정 레짐이 바뀜 → 재진입 허용

    # 3) 거래소 실제 상태
    absorb()
    pos = ex.position(inst)
    if s["side"] and not pos:                               # 봇이 닫지 않았는데 사라짐 = 거래소 손절 또는 강제청산
        flags.append("stop_hit")
        s["blk"] = s["side"]
        alert(f"{inst} {'롱' if s['side'] > 0 else '숏'} 포지션이 거래소에서 종료됨 (손절/청산) — 새 레짐 에지까지 재진입 금지")
        s.update(side=0, stop=None)
    if pos and pos["side"] != s["side"]:
        alert(f"{inst} 장부({s['side']})와 거래소 포지션({pos['side']}) 불일치 → 거래소 기준으로 맞춤")
        s.update(side=pos["side"], entry=pos["avgPx"], stop=None, atr_e=None, be_ts=None)
    if pos and pos.get("mgnMode") != "isolated":
        alert(f"{inst} 포지션이 격리가 아님({pos.get('mgnMode')})")
    if pos and pos.get("lever") and pos["lever"] != rls.LEVER:
        alert(f"{inst} 포지션 레버리지 {pos['lever']:g}x ≠ {rls.LEVER}x")

    # 4) 위험 한도 → 레짐 청산/진입 → DROP1 → 본전 이동
    cur, exit_why, enter = (pos["side"] if pos else 0), None, 0
    if cur and risk:
        exit_why = risk
    elif cur and not gap:
        if conf != cur:
            exit_why = "regime"
        elif rls.drop1(cur, row.close, row.c24, row.atr):
            exit_why = "drop1"
        elif not s.get("be_ts") and s.get("atr_e") and rls.be_hit(cur, row.close, s["entry"], s["atr_e"]):
            s["stop"] = rls.tighten(cur, s["stop"], rls.be_px(cur, s["entry"]))
            s["be_ts"] = iso(last_bar + rls.H_MS)
            if s.get("trade"):
                s["trade"]["be_ts"] = s["be_ts"]
            flags.append("be")
            moved = True
    if not gap and conf and (not cur or exit_why == "regime") and not risk:
        if conf == s.get("blk"):
            if not notes:
                notes.append("손절/조기청산/일손실 후 같은 확정 레짐 지속 → 새 에지 대기")
        elif not entry_ok or s.get("streak_hold"):
            flags.append("entry_hold")
            notes.append("신규 진입 중단 중 (일손실/강제청산/연패) → 진입 안 함")
        else:
            enter = conf

    # 5) 필요한 주문만
    if cur and exit_why:
        s["exit_why"] = exit_why
        if ex.stops(inst):
            ex.cancel_stops(inst, [x["algoId"] for x in ex.stops(inst)])
        fill = ex.order(inst, -cur, pos["sz"], f"{hk}{coin}{exit_why}", reduce=True)
        log_order(inst, fill, exit_why, -cur)
        if ex.position(inst):
            raise RuntimeError(f"{inst} 청산 미확인 — 진입하지 않음")
        if exit_why in EDGE_WHY and conf == cur:
            s["blk"] = cur
        s.update(side=0, stop=None)
        pos, cur = None, 0
        absorb()
    if enter and not cur:
        ins, tk = ex.instrument(inst), ex.ticker(inst)
        margin = equity * rls.MARGIN
        sz = okx.floor_to(margin * rls.LEVER / (ins["ctVal"] * tk["last"]), ins["lotSz"])
        why = ("레버리지 > 거래소 최대" if rls.LEVER > ins["lever"] else
               f"수량 {sz} < 최소 {ins['minSz']}" if sz < ins["minSz"] else
               f"증거금 > 자산 {rls.MARGIN:.0%}" if sz * ins["ctVal"] * tk["last"] / rls.LEVER > margin + 1e-9 else
               f"동시 증거금 합 > 자산 {MAX_TOTAL_MARGIN:.0%}" if used_margin + margin > MAX_TOTAL_MARGIN * equity + 1e-9 else
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
            fill = ex.order(inst, enter, sz, f"{hk}{coin}open")
            log_order(inst, fill, "open", enter)
            pos = ex.position(inst)
            if not pos:
                raise RuntimeError(f"{inst} 진입 체결 확인 실패")
            dist = rls.stop_dist(row.atr, row.close)            # 진입 직전 종가 = 마지막 확정 1H 종가
            s.update(side=enter, entry=pos["avgPx"], stop=rls.stop_px(enter, pos["avgPx"], dist), atr_e=float(row.atr),
                     be_ts=None, trade={"entry_ts": iso(ms(now)), "entry_px": pos["avgPx"], "eq0": equity,
                                        "stop_dist": dist, "liq_px": pos.get("liqPx"),
                                        "slip_bps": enter * (fill["avgPx"] / row.close - 1) * 1e4})
            cur, opened = enter, True

    # 손절 보장: 청산가 안쪽 확인 → 거래소 조건부 주문 1개 (없거나 수량·가격이 다르면 재설정 + 경보)
    if pos:
        if not s.get("stop"):
            s["stop"] = rls.stop_px(pos["side"], pos["avgPx"], rls.liq_dist() - rls.LIQ_BUF)
            alert(f"{inst} 손절가 기록 없음 → 평단 기준 최대 거리로 설정 {s['stop']:,.2f}")
        liq = pos.get("liqPx")
        if liq and (s["stop"] - liq) * pos["side"] <= 0:
            s["stop"] = rls.inside_liq(pos["side"], pos["avgPx"], liq)
            flags.append("stop_inside_reset")
            alert(f"{inst} 손절가가 거래소 청산가({liq:,.2f}) 바깥 → 안쪽으로 재설정 {s['stop']:,.2f}")
        tick = ex.instrument(inst)["tickSz"]
        want = round(round(s["stop"] / tick) * tick, 8)
        stops = ex.stops(inst)
        good = [x for x in stops if abs(x["sz"] - pos["sz"]) < 1e-9 and x["side"] == -pos["side"]
                and abs(x["trigger"] - want) < tick / 2]
        if not (len(stops) == 1 and good):
            if not (opened or moved):
                flags.append("stop_reset")
                alert(f"{inst} 손절 주문 없음/불일치 ({len(stops)}개) → 재설정 {want:,.2f}")
            s["stop_n"] = s.get("stop_n", 0) + 1
            old = [x["algoId"] for x in stops]
            try:                                             # 새 손절을 먼저 건 뒤 옛 손절을 취소 (무방비 구간 없음)
                ex.place_stop(inst, pos["side"], pos["sz"], want, f"{hk}{coin}sl{s['stop_n']}")
                if old:
                    ex.cancel_stops(inst, old)
            except Exception as e:  # noqa: BLE001
                if stops:
                    alert(f"{inst} 손절 재설정 실패({e}) → 기존 손절 유지")
                else:
                    alert(f"{inst} 손절 주문 실패({e}) → 손절 없이 보유하지 않도록 즉시 청산")
                    s["exit_why"] = "sl"
                    fill = ex.order(inst, -pos["side"], pos["sz"], f"{hk}{coin}nostop", reduce=True)
                    log_order(inst, fill, "close", -pos["side"])
                    s["blk"] = pos["side"]
                    s.update(side=0, stop=None)
                    pos, cur = None, 0
                    flags.append("stop_fail")
    elif ex.stops(inst):                                     # 포지션 없는데 남은 손절 = 정리
        ex.cancel_stops(inst, [x["algoId"] for x in ex.stops(inst)])

    # 6) 기록 (일별 행은 그날 마지막 회차 상태, 플래그는 누적)
    absorb()
    pos = ex.position(inst)
    row_ = {"date": today, "inst": inst, "regime": None if row is None or pd.isna(row.reg) else int(row.reg),
            "conf": conf, "actual": cur, "equity": equity, "dd": 1 - equity / a["peak"], "loss_streak": s["streak"],
            "stop_px": s.get("stop") if pos else None, "liq_px": pos.get("liqPx") if pos else None,
            "be": int(bool(s.get("be_ts"))) if pos else None, "fee_cum": s["fee"] + (pos["fee"] if pos else 0),
            "funding_cum": s["funding"] + (pos["fundingFee"] if pos else 0), "note": " · ".join(notes),
            "ts": dt.datetime.now(dt.UTC).isoformat(timespec="seconds")}
    if first_today:
        row_.update(baselines(h1, pd.Timestamp(s["start_day"])))
        s["base_day"] = today
    with db() as c:
        old = c.execute("SELECT flags FROM daily WHERE date=? AND inst=?", (today, inst)).fetchone()
        prev = [f for f in ((old and old[0]) or "").split(",") if f]
        row_["flags"] = ",".join(dict.fromkeys(prev + flags))
        upd = ", ".join(f"{k}=excluded.{k}" for k in row_ if k not in ("date", "inst"))
        c.execute(f"INSERT INTO daily ({', '.join(row_)}) VALUES ({', '.join('?' * len(row_))}) "
                  f"ON CONFLICT(date, inst) DO UPDATE SET {upd}", list(row_.values()))
    kv_set(inst, s)
    log.info("%s 확정레짐 %s · 실제 %s · 손절 %s · 청산가 %s · 연패 %d %s %s", inst, conf, cur,
             f"{s['stop']:,.2f}" if pos and s.get("stop") else "-",
             f"{pos['liqPx']:,.2f}" if pos and pos.get("liqPx") else "-", s["streak"], ",".join(flags), " · ".join(notes))
    return pos["margin"] if pos else 0.0


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
            a, today = kv_get("acct", {}), str(now.date())
            if a.get("day") != today:                           # 당일 UTC 시작 자산 = 그날 첫 회차 자산
                a.update(day=today, day_eq0=equity)
            a["peak"] = max(a.get("peak") or equity, equity)
            dd, risk = 1 - equity / a["peak"], None
            if not a.get("halted") and dd >= KILL_DD:
                a["halted"] = True
                alert(f"계좌 킬 스위치: 고점 대비 낙폭 {dd:.1%} ≥ {KILL_DD:.0%} → 전량 청산·정지. 확인 후 --resume")
            if a.get("halted"):
                risk = "halt"
            elif a.get("no_entry_day") == today:                # 그날은 계속 청산 유지 (앞선 청산 실패 대비)
                risk = "daily_loss"
            elif equity <= a["day_eq0"] * (1 - rls.DAILY_LOSS):
                a["no_entry_day"] = today
                risk = "daily_loss"
                alert(f"일일 손실 한도: 당일 시작 {a['day_eq0']:,.2f} → {equity:,.2f} (−{rls.DAILY_LOSS:.0%}) → 전 포지션 청산, 오늘 신규 진입 중단")
            entry_ok = not a.get("halted") and a.get("no_entry_day") != today and not a.get("hold")
            margins = {i: (p or {}).get("margin", 0.0) for i in INSTS for p in [ex.position(i)]}
            log.info("=== %s · 자산 %.2f USDT (가용 %.2f) · 낙폭 %.1f%%%s ===", MODE, equity, avail, dd * 100,
                     "" if entry_ok else " · 신규 진입 중단")
            for inst in INSTS:
                try:
                    others = sum(v for k, v in margins.items() if k != inst)
                    margins[inst] = trade(ex, inst, now, equity, avail, others, risk, entry_ok, a)
                except Exception as e:  # noqa: BLE001
                    log.exception("%s 실패", inst)
                    alert(f"{inst} 실행 실패, 주문 없이 종료 (거래소 손절은 유지): {e}")
            kv_set("acct", a)
        finally:
            if isinstance(ex, okx.Paper):
                kv_set("paper", ex.st)


def schedule_forever():
    while True:
        n = dt.datetime.now(dt.UTC)
        nxt = n.replace(minute=RUN_MINUTE, second=0, microsecond=0)
        if nxt <= n:
            nxt += dt.timedelta(hours=1)
        time.sleep((nxt - n).total_seconds())
        try:
            run_cycle()
        except Exception as e:  # noqa: BLE001
            alert(f"사이클 실패: {e}")


def resume():
    a = kv_get("acct", {})
    if a.get("halted"):
        a["peak"] = None                                     # 재개 시점 자산을 새 고점으로
    a.update(halted=False, hold=None)
    kv_set("acct", a)
    for inst in INSTS:
        s = kv_get(inst)
        if s:
            s.update(streak_hold=False, streak=0)
            kv_set(inst, s)


if __name__ == "__main__":
    if "--resume" in sys.argv:
        resume()
        sys.exit("정지·진입 중단 해제")
    run_cycle()
    if "--once" not in sys.argv:
        schedule_forever()
