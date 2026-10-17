"""RLS-C v1 자동매매 — 코인 슬리브 (BTC-USDT-SWAP · ETH-USDT-SWAP), OKX 한 계좌, 격리 30x, 헤지(long/short) 모드.
규칙: rls.py (명세를 바꾸지 않는다). 거래소: okx.py. 과거 백테스트의 사후 탐색 결과이며 수익을 보장하지 않는다.

시각 분할 (기본 6몫, 환경변수 RLSC_TRANCHES): 자본을 몫 수로 나눠 몫마다 다른 일봉 경계(기본 UTC 00·04·08·12·16·20 = KST 09·13·17·21·01·05)로
  같은 규칙을 하루 1번 돌린다. RLSC_TRANCHES=0 이면 1몫 (UTC 00, 자본이 작아 여러 몫이 최소 주문에 못 미칠 때).
  몫 구성을 바꾸면 열린 몫이 없을 때만 시작한다 (장부가 순번 #k 로 저장됨 → tranche_guard).
  몫마다 장부·진입가·손절·본전·DROP1·edge 가 따로 있고, 거래소에서는 같은 상품·방향의 몫이 한 포지션으로 합쳐진다.
  손절은 몫마다 자기 수량만큼 따로 건다. 헤지 모드라 몫끼리 롱·숏이 달라도 상쇄되지 않는다.
작업 (1 락 → 2 확정봉·신호 → 3 거래소 대사 → 4 위험 → 레짐 청산 → DROP1 → 진입 → 본전 → 5 주문 → 6 기록):
  t0 UTC 00:02   자산 기록(00:05 대용)·일손실·킬 → 몫 0 판단·체결
  t1~t5 UTC 04·08·12·16·20:02   몫 1~5 판단·체결
  C 규칙 (rls.edge_after · rls.vol_ok): 본전 뒤 손절은 edge 없음, 신호 봉 ATR14/종가 > 8% 면 진입 대기.
  guard 매시 07분  점검만: 대사(손절 체결·강제청산 감지)·몫별 손절 주문 복구. 진입·청산 판단 없음
  risk  RLSC_RISK_POLL_SEC(기본 5초, dry 60초)마다  위험 감시: 자산·입출금 조회 → 일 기준(UTC 00:00 직후 첫 기록)·고점·킬
        → 청산 사유가 있으면 그 자리에서 계좌 단위 비상 청산 (RUNNING → HALTING → FLATTENING → HALTED, 실패 = DEGRADED)
        RLSC_PROTECT_SEC(기본 60초)마다 guard 와 같은 대사·손절 점검.
위험(R2): 몫당 증거금 = 실현 잔고 × RLSC_MARGIN(기본 4%) / 몫 수, 동시 합 ≤ 일 기준 자산 10%. 일 기준 대비 −5% (UTC 날 경계 판정) → 전 몫 즉시 청산,
      그 UTC 날 진입 없음. 킬: 일별 고점 대비 −35% (상시 판정) → 즉시 전량 청산·정지. 강제청산 1건·몫별 20연패 → 진입 중단.
      거래 손실 > 자산 3% 경보. 조회 실패·수량 불일치·미보호 몫·봇 외 주문·미확정 주문 → 신규 진입 차단 (자동 해제).
      입출금(거래 계정 이체)은 일 기준·고점을 같은 금액만큼 옮긴다 (입금 = 수익, 출금 = 손실로 보지 않음).
MODE (명시적 설정으로만 전환): dry = 주문 없이 장부만 (기본) | demo = OKX 데모 | live = 실거래
OKX 계정은 포지션 모드를 long/short(헤지)로 둬야 한다 (포지션이 없을 때만 바꿀 수 있다).

사용:  python autotrade.py --job t0|t1|t2|guard   1회 실행
       python autotrade.py                       스케줄 루프
       python autotrade.py --resume              정지·진입 중단 해제 (수동 확인 후)
       python autotrade.py --preflight           실거래 전 점검 (조회만: 권한·포지션 모드·최소 자금·남은 포지션)
"""
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import logging
import math
import os
import pathlib
import re
import sqlite3
import subprocess
import sys
import time

import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv

import okx
import rls

load_dotenv()
ROOT = pathlib.Path(__file__).resolve().parent
MODE = os.environ.get("MODE", "dry")
DB_PATH = ROOT / f"rlsc_{MODE}.db"
LOCK_PATH = ROOT / f"rlsc_{MODE}.lock"
KILL_DD, ANOMALY, STREAK_HOLD = rls.KILL_DD, rls.ANOMALY, rls.STREAK_HOLD     # 운용·리플레이 공통 (rls.strategy_config)
assert KILL_DD <= 0.35, "킬 스위치 완화 금지"
LIQ_TYPES = ("3", "4")                 # positions-history type: 강제청산 / 일부 강제청산
RUN_MINUTE, GUARD_MINUTE = 2, 7        # 몫 작업 HH:02 (HH = 몫 경계), 점검 매시 07분
TRANCHES = tuple(int(x) for x in os.environ.get("RLSC_TRANCHES", ",".join(map(str, rls.TRANCHES))).split(","))
assert TRANCHES and all(0 <= h < 24 and h % 4 == 0 for h in TRANCHES), "몫 경계는 4H 봉 경계(0·4·…·20 시)"
N = len(TRANCHES)                      # RLSC_TRANCHES=0 → 1몫 (자본이 작을 때), 기본 0,8,16 → 3몫
COINS = tuple(os.environ.get("RLSC_COINS", ",".join(rls.COINS)).split(","))   # 운용 상품 (순서 = 진입 우선). 예: ETH-USDT-SWAP
assert COINS and set(COINS) <= set(rls.COINS), f"RLSC_COINS 는 {rls.COINS} 중에서"
SEED_DAYS, COIN_WARMUP = 500, 150      # 시작 시 edge 복원용 / 신호 계산용 과거 일수
RISK_POLL = float(os.environ.get("RLSC_RISK_POLL_SEC", 60 if MODE == "dry" else 5))   # 위험 감시 간격 (목표, API 제한 안에서)
PROTECT_SEC = float(os.environ.get("RLSC_PROTECT_SEC", 60))   # 위험 감시 중 대사·손절 점검 간격
DAY_REF_LATE = 15 * 60                 # 일 기준 기록이 UTC 00:00 보다 이만큼(초) 늦으면 그날 신규 진입 보류 (기준 신뢰 불가)
BOT_CID = re.compile(r"^r\d{6}")       # 봇 주문 clOrdId / algoClOrdId (r + yymmdd …). 이것만 봇 소유로 보고 취소한다
FINAL = ("filled", "canceled", "mmp_canceled")
SIZE_BUF = 0.001                       # 수량 산정 가격 여유 10bp: 시장가 체결이 이만큼 불리해도 몫 증거금 ≤ 실현 잔고 2%/N
MARGIN = float(os.environ.get("RLSC_MARGIN", rls.MARGIN))   # 종목당 증거금 비율 (실현 잔고 대비). 몫당 = MARGIN / N
assert 0 < MARGIN and 2 * MARGIN <= rls.MAX_TOTAL + 1e-12, f"RLSC_MARGIN={MARGIN}: 0 초과, 두 종목 합이 동시 한도 {rls.MAX_TOTAL:.0%} 이하(≤ 5%)"
rls.VOL_TARGET = float(os.environ.get("RLSC_VOL_TARGET", rls.VOL_TARGET or 0)) or None   # 동적 크기 목표 (0 = 끔: BTC+ETH 다몫은 끄는 편이 낫다)
CFG = rls.strategy_config(TRANCHES, rls.LEVER, insts=COINS, margin=MARGIN)

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
    CREATE TABLE IF NOT EXISTS daily (date TEXT PRIMARY KEY, equity REAL, dd REAL, margin_pct REAL, blocked INTEGER,
        regimes TEXT, cands TEXT, streak INTEGER, flags TEXT, stock_only REAL, coin_only REAL, btc_hold REAL,
        note TEXT, ts TEXT);
    CREATE TABLE IF NOT EXISTS trades (sleeve TEXT, inst TEXT, side INTEGER, entry_ts TEXT, entry_px REAL,
        exit_ts TEXT, exit_px REAL, lever REAL, margin REAL, notional REAL, stop_dist REAL, liq_px REAL, be INTEGER,
        be_ts TEXT, why TEXT, fee REAL, funding REAL, pnl REAL, pnl_pct REAL, basis REAL);
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
        c.execute("INSERT OR REPLACE INTO kv VALUES (?, ?)", (k, json.dumps(v, default=str)))


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
            requests.post(url, json={"text": f"[RLS-C {MODE}] {msg}", "content": f"[RLS-C {MODE}] {msg}"}, timeout=10)
        except Exception as e:  # noqa: BLE001
            log.error("경보 전송 실패: %s", e)


def code_version():
    """원본 코드 식별: git HEAD + 작업 트리 변경 여부 + 실행 파일 내용 해시 (서버 복사본처럼 git 이 없으면 HEAD = unknown)."""
    files = ("rls.py", "okx.py", "autotrade.py")
    out = {"files_sha": hashlib.sha256(b"".join((ROOT / f).read_bytes() for f in files)).hexdigest()[:12]}
    try:
        g = lambda *a: subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, timeout=5, check=True).stdout.strip()  # noqa: E731
        out.update(git=g("rev-parse", "--short", "HEAD"), dirty=bool(g("status", "--porcelain", "--", *files)))
    except Exception:  # noqa: BLE001
        out.update(git="unknown", dirty=None)
    return out


def effective_config():
    """비밀정보 없는 유효 설정. strategy = 리플레이와 같은 전략 설정 (해시 비교 대상). 거래소 적용값은 조회 전까지 unverified."""
    return {"mode": MODE, "strategy": CFG, "strategy_hash": rls.config_hash(CFG), "code": code_version(),
            "source": {"RLSC_TRANCHES": "env" if "RLSC_TRANCHES" in os.environ else "default",
                       "RLSC_MARGIN": "env" if "RLSC_MARGIN" in os.environ else "default", "MODE": "env" if "MODE" in os.environ else "default"},
            "runtime": {"risk_poll_sec": RISK_POLL, "protect_sec": PROTECT_SEC, "lever_applied": "min(lever, exchange max) — 거래소 조회값은 leverage-info"}}


# ---------- 위험 상태 (kv "risk") ----------
# state: RUNNING | HALTING (진입 금지 기록) | FLATTENING (거래소 잔여 수량 청산 중) | HALTED (봇 수량 0 확인) | DEGRADED (조회·주문 실패, 재시도)
# flatten: 청산이 필요한 사유 {kill: 메시지, daily_loss: UTC 날짜}. daily_loss 는 그 날짜가 지나면 그 사유만 풀린다. kill 은 --resume.
# block: 신규 진입만 막는 사유 (조회 실패·수량 불일치·미보호·봇 외 주문·미확정 주문·일 기준 지연). 원인이 사라지면 자동 해제.
# day: 일손실 기준 {date, eq, ts, lag_s}, hwm: 일별 고점 (리플레이와 같은 정의), t: 내부 지표 시각.
def _risk_norm(r):
    if r is None:                                            # 예전 acct 필드에서 한 번 옮긴다 (상태 보존)
        a = kv_get("acct", {})
        r = {"hwm": a.get("peak"), "flatten": {}, "block": {}}
        if a.get("rec_eq"):
            r["day"] = {"date": a.get("rec_day"), "eq": a["rec_eq"], "ts": None, "lag_s": None}
        if a.get("halted"):
            r["flatten"]["kill"] = "acct.halted (이전 버전)"
        if a.get("dl_day"):
            r["flatten"]["daily_loss"] = a["dl_day"]
    for k, v in (("state", "RUNNING"), ("flatten", {}), ("block", {}), ("t", {})):
        r.setdefault(k, v)
    return r


def risk_get():
    return _risk_norm(kv_get("risk"))


def risk_update(fn):
    """kv "risk" 를 한 트랜잭션으로 읽고 고쳐 쓴다 (다른 프로세스와 갱신이 섞이지 않게)."""
    raw = kv_get("risk")
    r = _risk_norm(raw) if raw is None else None
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        if r is None:
            row = c.execute("SELECT v FROM kv WHERE k='risk'").fetchone()
            r = _risk_norm(json.loads(row[0]))
        fn(r)
        c.execute("INSERT OR REPLACE INTO kv VALUES ('risk', ?)", (json.dumps(r, default=str),))
    return r


def set_blocks(prefix, found):
    """prefix 로 시작하는 진입 차단 사유를 이번 점검 결과(found dict)로 바꾼다."""
    def f(r):
        r["block"] = {k: v for k, v in r["block"].items() if not k.startswith(prefix)} | found
    return risk_update(f)


def flatten_reasons(r, now):
    return [k for k, v in r["flatten"].items() if k != "daily_loss" or v == str(now.date())]


def entry_blocks(r, now, a=None):
    """신규 진입을 막는 사유 목록. 비었으면 진입 가능."""
    out = flatten_reasons(r, now) + list(r["block"])
    if r["state"] in ("HALTING", "FLATTENING", "DEGRADED"):
        out.append(r["state"])
    if a and a.get("hold"):
        out.append(a["hold"])
    return out


def risk_eval(r, eq, now, flow=0.0):
    """위험 판정 (거래소 호출 없음): r 을 고치고 새로 걸린 청산 사유 [(사유, 메시지)] 를 돌려준다.
    eq = 미실현 손익 포함 USDT 자산, flow = 지난 판정 이후 순이체 (+입금 / −출금).
    일손실은 UTC 날 경계에서만 판정한다 (이전 기간 판정을 끝낸 뒤 새 기준을 만든다 — 리플레이의 00:00 기록과 같은 규칙)."""
    day, new = str(now.date()), []
    if flow:
        for d in [r.get("day")] if r.get("day") else []:
            d["eq"] += flow
        if r.get("hwm"):
            r["hwm"] += flow
    d = r.get("day")
    if not d or d.get("date") != day:
        if d and d.get("eq") and eq <= d["eq"] * (1 - rls.DAILY_LOSS):
            r["flatten"]["daily_loss"] = day
            new.append(("daily_loss", f"계좌 일손실: 일 기준 {d['eq']:,.2f} → {eq:,.2f} ({eq / d['eq'] - 1:+.2%} ≤ −{rls.DAILY_LOSS:.0%})"
                       f" → 전 몫 즉시 청산, 오늘(UTC) 신규 진입 없음"))
        lag = (pd.Timestamp(now).tz_localize(None) - pd.Timestamp(day)).total_seconds()
        r["day"] = {"date": day, "eq": eq, "ts": pd.Timestamp(now).isoformat(timespec="seconds"), "lag_s": round(lag)}
        r["hwm"] = max(r.get("hwm") or eq, eq)
        if lag > DAY_REF_LATE:
            r["block"]["day_ref_late"] = f"{day} 일 기준이 UTC 00:00 + {lag / 60:.0f}분에 기록됨 → 오늘 신규 진입 보류"
    if r["flatten"].get("daily_loss") and r["flatten"]["daily_loss"] != day:
        del r["flatten"]["daily_loss"]                       # 일손실 사유만 해제 (킬·다른 차단 사유는 그대로)
    if not r["block"].get("day_ref_late", day).startswith(day):
        del r["block"]["day_ref_late"]                       # 지난 날의 기준 지연 사유
    r["hwm"] = r.get("hwm") or eq
    if "kill" not in r["flatten"] and 1 - eq / r["hwm"] >= KILL_DD:
        r["flatten"]["kill"] = f"{now:%Y-%m-%d %H:%M:%S} 고점 {r['hwm']:,.2f} 대비 {1 - eq / r['hwm']:.1%}"
        new.append(("kill", f"킬 스위치: 고점 {r['hwm']:,.2f} 대비 {1 - eq / r['hwm']:.1%} → 즉시 전량 청산·정지. 확인 후 --resume"))
    return new


def risk_step(ex, ctx):
    """위험 감시 1회 (락 안): 입출금 반영 → 판정 → 청산 사유가 있으면 지금 계좌 단위로 청산하고 잔여 수량을 확인한다."""
    now, stamp = ctx["now"], pd.Timestamp(ctx["now"]).isoformat(timespec="seconds")
    try:
        r0 = risk_get()
        bills = ex.transfers(r0.get("bill_ts") or ms(now))
    except Exception as e:  # noqa: BLE001
        def f(r):
            r["block"]["stale"] = f"{stamp} 입출금 조회 실패: {e}"
            r["state"], r["err"] = "DEGRADED", str(e)
        risk_update(f)
        raise
    flow, alerts = sum(x[2] for x in bills), []

    def judge(r):
        r["t"].update(last_check=stamp, last_ok=stamp)
        r["block"].pop("stale", None)
        if bills:
            r["bill_ts"] = max(x[0] for x in bills)
            r.setdefault("flows", []).extend([[iso(x[0]), x[2]] for x in bills])
        r.setdefault("bill_ts", ms(now))
        for why, m in risk_eval(r, ctx["equity"], now, flow):
            alerts.append(m)
            ctx["flags"].append(why)
            r["t"]["detected"] = stamp
        if not flatten_reasons(r, now):
            r["state"], r["err"] = "RUNNING", None           # 이번 조회가 성공했으므로 조회 실패 DEGRADED 도 복구
        elif r["state"] == "RUNNING" or (r["state"] == "HALTED" and (alerts or open_books())):
            r["state"] = "HALTING"                           # 신규 진입 금지를 먼저 기록한다 (청산보다 앞)
            r["t"].pop("flat_ok", None)
    r = risk_update(judge)
    for m in alerts:
        alert(m)
    if bills:
        ctx["notes"].append(f"입출금 {flow:+,.2f} USDT → 일 기준·고점 조정")
    why = flatten_reasons(r, now)
    if not why or r["state"] == "HALTED":
        return r
    risk_update(lambda x: (x.update(state="FLATTENING"), x["t"].setdefault("flatten_req", stamp)))
    try:
        left = flatten(ex, ctx, "halt" if "kill" in why else "daily_loss")
    except Exception as e:  # noqa: BLE001
        log.exception("비상 청산 실패")
        repeat = r.get("err") == f"비상 청산 실패: {e}"
        r = risk_update(lambda x: x.update(state="DEGRADED", err=f"비상 청산 실패: {e}"))
        if not repeat:                                       # 같은 실패는 5초마다 다시 경보하지 않는다
            alert(f"비상 청산 실패 → DEGRADED, 다음 감시에서 거래소 잔여 수량 재조회 후 재시도: {e}")
        return r
    prev_left = r.get("left")

    def done(x):
        x["state"], x["left"] = ("HALTED" if left <= 1e-12 else "FLATTENING"), left
        if left <= 1e-12 and not x["t"].get("flat_ok"):
            x["t"]["flat_ok"] = pd.Timestamp(dt.datetime.now(dt.UTC)).isoformat(timespec="seconds")
            ref = (x.get("day") or {}).get("eq")
            x["flat_eq"] = {"eq": ctx["equity"], "vs_day": ctx["equity"] / ref - 1 if ref else None,
                            "vs_hwm": ctx["equity"] / x["hwm"] - 1 if x.get("hwm") else None}
    r = risk_update(done)
    if r["state"] == "HALTED":
        alert(f"비상 청산 완료 ({', '.join(why)}): 봇 수량 0 확인. 판정 시 자산 대비 일 기준 {r['flat_eq']['vs_day'] or 0:+.2%} "
              f"(−{rls.DAILY_LOSS:.0%} 는 발동 기준이지 손실 상한이 아니다)")
    elif left != prev_left:
        alert(f"비상 청산 미완료: 봇 수량 {left:g} 남음 → 다음 감시에서 재시도")
    return r


def open_books():
    return any(s["side"] for i in COINS for s in books(i).values())


def make_client():
    if MODE == "dry":
        return okx.Paper(okx.Client(), kv_get("paper", {}), float(os.environ.get("PAPER_EQUITY_USDT", 10_000)))
    if MODE not in ("demo", "live"):
        raise ValueError(f"MODE={MODE} (dry | demo | live)")
    k, s, p = (os.environ.get(x) for x in ("OKX_API_KEY", "OKX_API_SECRET", "OKX_API_PASSPHRASE"))
    if not (k and s and p):
        raise ValueError("OKX_API_KEY / OKX_API_SECRET / OKX_API_PASSPHRASE 가 없습니다")
    return okx.Client(k, s, p, demo=MODE == "demo")


def okx_balance(ex):
    """실제 OKX 계좌 잔액 (USDT). dry 에서도 키가 있으면 잔액만 조회한다 (주문 없음). 키가 없거나 실패하면 None."""
    try:
        if isinstance(ex, okx.Paper):
            k, s, p = (os.environ.get(x) for x in ("OKX_API_KEY", "OKX_API_SECRET", "OKX_API_PASSPHRASE"))
            if not (k and s and p):
                return None
            ex = okx.Client(k, s, p, demo=os.environ.get("OKX_BALANCE_DEMO") == "1")
        eq, avail, realized = ex.balance()
        return {"eq": eq, "avail": avail, "realized": realized, "demo": ex.demo,
                "ts": dt.datetime.now(dt.UTC).isoformat(timespec="seconds")}
    except Exception as e:  # noqa: BLE001
        log.warning("OKX 잔액 조회 실패: %s", e)
        return None


def ms(t):
    return int(pd.Timestamp(t).timestamp() * 1000)


def iso(t_ms):
    return pd.Timestamp(t_ms, unit="ms").isoformat(timespec="minutes")


def sym(inst):
    return inst.split("-")[0]


def log_order(inst, o, action, side):
    with db() as c:
        c.execute("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), inst, o["cid"], action, side, o["sz"],
                   o["avgPx"], o["fee"], o["state"]))
    log.info("[%s] %s %s %s %s @ %s", MODE, o["cid"], action, side, o["sz"], o["avgPx"])


def record_day(day, ctx, snapshot=True, **cols):
    """일별 행 upsert. flags·note 는 누적, blocked 는 합산. snapshot=False(점검)면 이미 있는 자산·낙폭은 덮지 않는다."""
    a, eq = ctx["a"], ctx["equity"]
    row = {"date": str(day), "equity": eq, "dd": 1 - eq / (ctx.get("hwm") or eq), "streak": a.get("streak", 0),
           "margin_pct": ctx["used"] / eq if eq else None, "ts": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
           **{k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in cols.items()}}
    with db() as c:
        old = c.execute("SELECT flags, note, blocked FROM daily WHERE date=?", (row["date"],)).fetchone() or ("", "", 0)
        row["flags"] = ",".join(dict.fromkeys([f for f in (old[0] or "").split(",") if f] + ctx["flags"]))
        row["note"] = " · ".join(x for x in (old[1], " · ".join(ctx["notes"])) if x)
        row["blocked"] = (old[2] or 0) + ctx["blocked"]
        keep = () if snapshot else ("equity", "dd", "margin_pct")
        upd = ", ".join(f"{k}=excluded.{k}" for k in row if k != "date" and k not in keep)
        c.execute(f"INSERT INTO daily ({', '.join(row)}) VALUES ({', '.join('?' * len(row))}) "
                  f"ON CONFLICT(date) DO UPDATE SET {upd}", list(row.values()))


# ---------- 상품 메타 (매일 캐시) ----------
def meta(ex, inst, a):
    """{ctVal, lotSz, minSz, tickSz, lever(거래소 최대), mmr(격리 티어1), L = min(rls.LEVER, 최대)}."""
    day = str(dt.datetime.now(dt.UTC).date())
    c = a.setdefault("meta", {})
    if c.get(inst, {}).get("day") != day:
        m = ex.instrument(inst)
        c[inst] = {**m, "mmr": ex.tier1_mmr(inst), "L": rls.lever_for(m["lever"]), "day": day}
    return c[inst]


def tkey(inst, k):
    return f"{inst}#{k}"


def ledger(inst, k):
    return kv_get(tkey(inst, k)) or {"side": 0, "sz": 0.0, "stop_n": 0, "blk": None}


def books(inst):
    """{몫: 장부} 전 몫."""
    return {k: ledger(inst, k) for k in range(N)}


def bars(ex, inst, now, off, days=COIN_WARMUP):
    """off 시(UTC) 경계 확정 일봉 (OKX 4H 확정봉으로 만든다. off=0 이면 1Dutc 와 같다)."""
    since = ms(pd.Timestamp(now).tz_localize(None).normalize() - pd.Timedelta(days=days))
    return rls.offset_daily(ex.candles(inst, since, bar="4H"), off, 4)


def seed_edge(inst, d):
    """처음 실행할 때 과거 일봉 리플레이로 edge 상태를 복원한다: 마지막 손절·DROP1 뒤로 원하는 방향(확정 레짐)이
    한 번도 꺼지지 않았으면 그 방향을 막는다. → 막을 방향 | None"""
    if len(d) <= rls.SMA + rls.SLOPE + rls.ATR_N:
        return None
    _, t, _ = rls.simulate({inst: d}, {}, lambda _: set(), set(), d.index[rls.SMA + rls.SLOPE + rls.ATR_N],
                           sleeves=("coin",))
    t = t[[rls.edge_after(w, b) for w, b in zip(t.why, t.be)]] if len(t) else t      # C: 본전 뒤 손절은 제외
    if not len(t):
        return None
    last = t.iloc[-1]
    conf = rls.prep(d).conf
    return int(last.side) if (conf[conf.index >= last.exit_ts] == last.side).all() else None


# ---------- 몫 단위 동작 ----------
def add_hold(a, msg):
    """수동 해제(--resume)가 필요한 진입 중단 사유를 덧붙인다 (기존 사유를 덮지 않는다)."""
    a["hold"] = " · ".join(x for x in (a.get("hold"), msg) if x)


def record_trade(inst, k, s, exit_px, why, exit_fee, ctx):
    """몫 하나의 거래를 기록하고 계좌 연패·이상 손실을 본다. 손익 = 가격 손익 + 진입·청산 수수료 + 배분된 펀딩.
    연패: 비용 포함 순손익 ≤ 0 이면 패 (본전도 패). 부분 청산은 각 부분을 거래 1건으로 센다."""
    a, ct = ctx["a"], meta(ctx["ex"], inst, ctx["a"])["ctVal"]
    t = s.get("trade", {})
    fee = s.get("fee_in", 0.0) + exit_fee
    pnl = s["side"] * s["sz"] * ct * (exit_px - s["entry"]) + fee + s.get("fund", 0.0)
    with db() as c:
        c.execute("INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  ("coin", tkey(inst, k), s["side"], t.get("entry_ts"), s["entry"], iso(ms(ctx["now"])), exit_px,
                   s.get("lever"), s.get("margin"), s["sz"] * ct * s["entry"], s.get("dist"), t.get("liq_px"),
                   int(bool(s.get("be_ts"))), s.get("be_ts"), why, fee, s.get("fund", 0.0), pnl,
                   pnl / t.get("eq0", ctx["equity"]), None))
    n = count_streak(a, k, pnl <= 0)
    if n >= STREAK_HOLD and "연패" not in (a.get("hold") or ""):
        add_hold(a, f"몫{k} {n}연패")
        alert(f"몫 {k} {n}연패 (리플레이 몫별 최장 15, 미래 상한 아님) → 신규 진입 중단. 확인 후 --resume")
    if pnl < -ANOMALY * ctx["equity"]:
        ctx["flags"].append("anomaly")
        alert(f"{tkey(inst, k)} 거래 손실 {pnl:,.4f} > 자산의 {ANOMALY:.0%}")
    if s.get("margin") and -pnl > s["margin"] + abs(fee) + abs(s.get("fund", 0.0)) + 1e-9:
        ctx["flags"].append("anomaly")
        alert(f"{tkey(inst, k)} 손실 {pnl:,.4f} 이 격리 증거금 {s['margin']:,.4f} + 비용을 넘음")
    if rls.edge_after(why, bool(s.get("be_ts"))):           # C①: 본전(BE1) 뒤 손절은 edge 를 걸지 않는다
        s["blk"] = s["side"]
    ctx["used"] -= s.get("margin", 0.0)
    keep = {x: s.get(x) for x in ("blk", "stop_n", "seeded")}
    s.clear()
    s.update(side=0, sz=0.0, **keep)


def split(s, q):
    """몫 장부 s 에서 q 계약을 떼어 낸 장부 (진입 수수료·증거금·펀딩은 수량 비율). 부분 청산용."""
    f = q / s["sz"]
    piece = {**s, "sz": q}
    for x in ("fee_in", "margin", "fund"):
        if s.get(x):
            piece[x] = s[x] * f
            s[x] -= piece[x]
    s["sz"] = round(s["sz"] - q, 10)
    return piece


def commit(inst, B, done=None):
    """몫 장부 저장 + 미확정 주문 done 해제·반영 기록을 한 트랜잭션으로 (중간에 죽어도 같은 체결을 두 번 반영하지 않는다)."""
    with db() as c:
        for k, s in B.items():
            c.execute("INSERT OR REPLACE INTO kv VALUES (?, ?)", (tkey(inst, k), json.dumps(s, default=str)))
        if done:
            get = lambda k, d: json.loads(r[0]) if (r := c.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()) else d  # noqa: E731
            p, applied = get("pending", {}), get("applied", [])
            p.pop(done, None)
            c.execute("INSERT OR REPLACE INTO kv VALUES ('pending', ?)", (json.dumps(p),))
            c.execute("INSERT OR REPLACE INTO kv VALUES ('applied', ?)", (json.dumps((applied + [done])[-1000:]),))


def fresh_cid(base):
    """반영됐거나 미확정인 clOrdId 와 겹치지 않는 새 clOrdId (같은 날 재시도가 옛 체결을 다시 반영하지 않게)."""
    used = set(kv_get("applied", [])) | set(kv_get("pending", {}))
    n = 0
    while (cid := f"{base[:29]}{n or ''}") in used:
        n += 1
    return cid


def apply_fill(ex, inst, B, cid, info, o, ctx):
    """확정된 주문 o 의 체결 수량(accFillSz, 접수량 아님)만 몫 장부에 반영하고 저장한다. → 닫힌 몫의 손절 clOrdId 목록."""
    log_order(tkey(inst, info["ks"][0]) if len(info["ks"]) == 1 else inst, o, info["why"],
              info["side"] if info["kind"] == "open" else -info["side"])
    stops = []
    if o["sz"] > 0 and info["kind"] == "open":
        s, m = B[info["ks"][0]], meta(ex, inst, ctx["a"])
        used = o["sz"] * m["ctVal"] * o["avgPx"] / info["lever"]
        s.update(side=info["side"], sz=o["sz"], entry=o["avgPx"], dist=info["dist"],
                 stop=rls.stop_px(info["side"], o["avgPx"], info["dist"]), atr_e=info["atr"], ref=o["avgPx"], be_ts=None,
                 lever=info["lever"], margin=used, fee_in=o["fee"], fund=0.0, stop_cid=None,
                 trade={"entry_ts": info["ts"], "eq0": info["eq0"], "liq_px": None})
        ctx["used"] += used
        if o["sz"] < info["sz"] - 1e-12:
            ctx["notes"].append(f"{tkey(inst, info['ks'][0])} 진입 부분 체결 {o['sz']:g}/{info['sz']:g} → 체결 수량만 장부")
    elif o["sz"] > 0:
        q = o["sz"]
        for k in info["ks"]:
            s = B[k]
            if q <= 1e-12 or s["side"] != info["side"]:
                continue
            part = min(q, s["sz"])
            fee = o["fee"] * part / o["sz"]
            if part < s["sz"] - 1e-12:
                record_trade(inst, k, split(s, part), o["avgPx"], info["why"], fee, ctx)
                ctx["notes"].append(f"{tkey(inst, k)} 부분 청산 {part:g} → 남은 {s['sz']:g}")
            else:
                stops += [x for x in (s.get("stop_cid"), s.get("stop_old")) if x]
                record_trade(inst, k, s, o["avgPx"], info["why"], fee, ctx)
            q -= part
        if q > 1e-9:
            alert(f"{inst} 청산 체결 {o['sz']:g} 중 {q:g} 는 봇 몫에 배분되지 않음 → 확인 필요")
    commit(inst, B, cid)
    return stops


def resolve(ex, inst, B, cid, info, o, ctx):
    stops = apply_fill(ex, inst, B, cid, info, o, ctx)
    if stops:                                                # 닫힌 몫의 손절은 체결 뒤에 취소 (실패해도 다음 점검이 봇 소유 고아로 취소)
        try:
            ids = [x["algoId"] for x in ex.stops(inst) if x["cid"] in stops]
            if ids:
                ex.cancel_stops(inst, ids)
        except Exception as e:  # noqa: BLE001
            log.warning("%s 닫힌 몫 손절 취소 실패 (다음 점검에서 정리): %s", inst, e)
    return o


def submit(ex, inst, B, cid, info, ctx):
    """주문 1건: 미확정 기록(저장) → 시장가 → 체결 수량만 장부 반영 → 장부 저장과 미확정 해제를 함께.
    응답 유실·체결 확인 실패면 미확정으로 남고, 다음 대사(reconcile)가 clOrdId 로 결과를 확정한다 (그 전엔 같은 상품 새 주문 없음)."""
    p = kv_get("pending", {})
    p[cid] = {"inst": inst, **info}
    kv_set("pending", p)
    return resolve(ex, inst, B, cid, p[cid], ex.order(inst, info["side"], info["sz"], cid, close=info["kind"] == "close"), ctx)


def pending_for(inst):
    return {c: i for c, i in kv_get("pending", {}).items() if i["inst"] == inst}


def count_streak(a, k, loss):
    """몫별 연패 → 이번 몫의 연패 수. 몫마다 독립 일정이라 1몫으로 정한 20연패 기준을 그대로 쓴다
    (계좌 전체로 세면 몫 수만큼 길어진다: 리플레이 계좌 최장 ETH 3몫 17 · BTC+ETH 3몫 26 · 6몫 36, 몫별 최장 9~15).
    a["streak"] = 지금 몫들 중 가장 긴 연패 (표시용)."""
    ks = a.setdefault("streaks", {})
    ks[str(k)] = ks.get(str(k), 0) + 1 if loss else 0
    a["streak"] = max(ks.get(str(j), 0) for j in range(N))
    return ks[str(k)]


def reconcile(ex, inst, B, ctx):
    """거래소와 몫 장부 맞추기: 미확정 주문 확정 → 몫 손절 체결 기록 → 강제청산 감지 → 방향별 수량 대조 → 펀딩 배분."""
    a = ctx["a"]
    wait = {}
    for cid, info in pending_for(inst).items():              # 0) 응답 유실·확인 실패 주문을 clOrdId 로 확정
        o = ex.get_order(inst, cid)
        if o is None:
            commit(inst, B, cid)
            ctx["notes"].append(f"미확정 주문 {cid} 거래소 기록 없음 → 해제")
        elif o["state"] in FINAL:
            ctx["flags"].append("pending_resolved")
            resolve(ex, inst, B, cid, info, o, ctx)
        else:
            wait[f"pending:{inst}:{cid}"] = f"주문 {cid} 상태 {o['state']}"
    set_blocks(f"pending:{inst}", wait)
    for k, s in B.items():                                   # 1) 몫 손절이 체결됐나 (교체 중이던 옛 손절 포함)
        for c in (s.get("stop_cid"), s.get("stop_old")):
            st = ex.algo(inst, c) if c and s["side"] else None
            if st and st["state"] == "effective":
                ctx["flags"].append(f"stop_hit:{sym(inst)}#{k}")
                record_trade(inst, k, s, st["px"], "sl", -rls.TAKER * s["sz"] * meta(ex, inst, a)["ctVal"] * st["px"], ctx)
    hs = a.setdefault("hist_ts", {}).get(inst, ms(ctx["now"]))
    liq = False
    for h in ex.positions_history(inst, hs):                 # 2) 강제청산
        a["hist_ts"][inst] = max(a["hist_ts"].get(inst, hs), h["uTime"])
        if h["type"] in LIQ_TYPES:
            liq = True
            ctx["flags"].append("liquidated")
            add_hold(a, f"{inst} 강제청산 {iso(h['uTime'])}")
            alert(f"{inst} 강제청산 발생 (설계상 0건) → 신규 진입 중단. 확인 후 --resume")
    a["hist_ts"].setdefault(inst, hs)
    pos = ex.positions(inst)
    mism = {}
    for side in (1, -1):                                     # 3) 방향별 수량: 거래소 = 몫 합 (확정 체결 수량 기준)
        mine = {k: s for k, s in B.items() if s["side"] == side}
        want = sum(s["sz"] for s in mine.values())
        have = pos.get(side, {}).get("sz", 0.0)
        p = pos.get(side)
        if p and p.get("mgnMode") != "isolated":
            ctx["flags"].append("margin_mode")
            mism[f"mismatch:{inst}:{okx.PS[side]}:mode"] = f"포지션이 격리가 아님 ({p.get('mgnMode')})"
        if want and not have and not wait:
            why = "liq" if liq else "external"
            alert(f"{inst} {okx.PS[side]} 포지션이 거래소에서 사라짐 ({why}) → 몫 {list(mine)} 종료 처리")
            for k, s in mine.items():
                record_trade(inst, k, s, s.get("stop") or s["entry"], why, 0.0, ctx)
                s["blk"] = side
        elif abs(have - want) > 1e-9:
            ctx["flags"].append("size_mismatch")
            mism[f"mismatch:{inst}:{okx.PS[side]}"] = (f"거래소 수량 {have:g} ≠ 몫 합 {want:g} — 귀속 불명 수량은 몫에 배분하지 않음, "
                                                       f"신규 진입 차단 (봇 외 수량은 청산하지 않음)")
        if p and want:                                       # 4) 펀딩: 지난 조회 이후 증가분을 몫 수량대로 배분
            seen = a.setdefault("fund_seen", {})
            key = f"{inst}{side}"
            d = p["fundingFee"] - seen.get(key, 0.0) if p["fundingFee"] >= seen.get(key, 0.0) else p["fundingFee"]
            seen[key] = p["fundingFee"]
            for s in mine.values():
                if s["side"]:
                    s["fund"] = s.get("fund", 0.0) + d * s["sz"] / want
        elif not p:
            a.setdefault("fund_seen", {}).pop(f"{inst}{side}", None)
    for k in new_blocks(f"mismatch:{inst}", mism):
        alert(f"{inst} {mism[k]} — 수동 확인 필요 (자동 주문 없음)")
    return ex.positions(inst)


def new_blocks(prefix, found):
    """set_blocks 후 이번에 새로 생긴 사유만 (경보를 매 점검마다 반복하지 않게)."""
    before = set(risk_get()["block"])
    set_blocks(prefix, found)
    return [k for k in found if k not in before]


def close_tranche(ex, inst, B, k, why, key, ctx):
    """몫 하나 시장가 청산 (자기 수량만, 거래소 현재 수량 한도). 자기 손절은 체결 뒤 취소한다 (청산 전에 보호를 없애지 않음)."""
    s = B[k]
    have = ex.positions(inst).get(s["side"], {}).get("sz", 0.0)
    sz = min(s["sz"], have)
    if sz <= 0:
        raise RuntimeError(f"{tkey(inst, k)} 청산할 거래소 수량 없음")
    o = submit(ex, inst, B, fresh_cid(f"{key}{k}{sym(inst)}{why.replace('_', '')}"),
               {"kind": "close", "side": s["side"], "sz": sz, "ks": [k], "why": why}, ctx)
    if o["state"] != "filled" or o["sz"] < sz - 1e-12:
        raise RuntimeError(f"{tkey(inst, k)} 청산 일부만 체결 {o['sz']:g}/{sz:g} ({o['state']}) → 남은 수량은 다음 대사·점검에서")


def flatten(ex, ctx, why):
    """계좌 단위 비상 청산 → 남은 봇 수량 합 (0 = flat 확인). (상품, 방향)마다 봇 몫 합산 수량을 거래소 현재 수량 한도로
    주문 1건 (몫 단위 주문을 따로 내지 않음). 보호 손절은 먼저 취소하지 않는다 (헤지 모드 청산 주문은 반대 포지션을 만들지 않음).
    봇 장부보다 많은 거래소 수량(수동·다른 봇)은 건드리지 않는다. 미확정 주문이 있는 상품은 확정될 때까지 새 주문을 내지 않는다."""
    left, errs = 0.0, []
    for inst in COINS:
        B = books(inst)
        try:
            pos = reconcile(ex, inst, B, ctx)                # 손절 체결·강제청산·미확정 주문을 먼저 반영
            for _ in range(3):                               # 부분 체결이면 남은 수량만 다시 (그래도 남으면 다음 감시)
                sent = False
                for side in (1, -1) if not pending_for(inst) else ():
                    ks = [k for k, s in B.items() if s["side"] == side]
                    sz = min(sum(B[k]["sz"] for k in ks), pos.get(side, {}).get("sz", 0.0))
                    if sz > 0:
                        sent = True
                        submit(ex, inst, B, fresh_cid(f"r{ctx['now']:%y%m%d%H%M}f{sym(inst)}{'L' if side > 0 else 'S'}"),
                               {"kind": "close", "side": side, "sz": sz, "ks": ks, "why": why}, ctx)
                pos = reconcile(ex, inst, B, ctx)            # 체결 뒤 거래소 잔여 수량 재조회
                if not sent:
                    break
        except Exception as e:  # noqa: BLE001
            log.exception("%s 비상 청산 실패", inst)
            errs.append(f"{inst}: {e}")
        finally:
            save(inst, B)
        left += sum(s["sz"] for s in B.values() if s["side"])
    if errs:
        raise RuntimeError(" / ".join(errs))
    return left


def open_tranche(ex, inst, B, k, side, sig, key, ctx):
    """주문 직전 검증 → set-leverage(방향별) 확인 → 위험 상태 재확인 → 시장가 진입 → 체결 수량만 몫 장부. 진입하면 True."""
    a, s = ctx["a"], B[k]
    m = meta(ex, inst, a)
    L, last = m["L"], ex.ticker(inst)["last"]
    vk = rls.vol_k(sig.atr, sig.close)                       # 동적 크기: 변동성 클 때 작게 (rls.VOL_TARGET)
    margin = ctx["realized"] * MARGIN / N * vk               # 종목당 비율을 몫 수로 나눈다 (몫마다 다시 쓰지 않음)
    sz = okx.floor_to(margin * L / (m["ctVal"] * last * (1 + SIZE_BUF)), m["lotSz"])   # 체결 슬리피지로 2%/N 을 넘지 않게
    dist = rls.stop_dist(sig.atr, sig.close, L, m["mmr"])
    if ctx["used"] + margin > rls.MAX_TOTAL * ctx["ref_eq"] + 1e-9:
        ctx["blocked"] += 1
        ctx["notes"].append(f"{sym(inst)}#{k} 동시 증거금 한도 {rls.MAX_TOTAL:.0%} → 진입 건너뜀")
        return False
    if sz < m["minSz"]:
        ctx["flags"].append("below_min")
        ctx["notes"].append(f"{sym(inst)}#{k} 수량 {sz:g} < 최소 {m['minSz']:g} (몫 증거금 {margin:.4f}, 크기 배수 {vk:.2f}) → 건너뜀")
        return False
    why = ("레버리지 > 거래소 최대" if L > m["lever"] else
           "손절 거리 ≤ 0 (청산 거리 부족)" if dist <= 0 else
           f"증거금 > 실현 잔고 {MARGIN:.2%}/{N}×{vk:.2f}" if sz * m["ctVal"] * last / L > margin + 1e-9 else
           "가용 증거금 부족" if margin > ctx["avail"] else None)
    if not why:
        ex.set_leverage(inst, L, side)
        li = ex.leverage_info(inst)                          # 실제 적용 여부를 조회로 확인
        if li.get(side) != L or li.get("mgnMode") != "isolated":
            why = f"set-leverage 미적용 (조회 {li})"
    if why:
        ctx["flags"].append("order_check_fail")
        alert(f"{tkey(inst, k)} 주문 검증 실패: {why} → 주문 안 함")
        return False
    blocks = entry_blocks(risk_get(), ctx["now"], a)        # 주문 직전 다시 확인 (위험 감시가 기록한 상태 포함)
    if blocks:
        ctx["flags"].append("entry_hold")
        ctx["notes"].append(f"{sym(inst)}#{k} 주문 직전 진입 차단: {', '.join(blocks)[:200]}")
        return False
    o = submit(ex, inst, B, fresh_cid(f"{key}{k}{sym(inst)}open"),
               {"kind": "open", "side": side, "sz": sz, "ks": [k], "why": "open", "dist": dist, "atr": float(sig.atr),
                "lever": L, "eq0": ctx["equity"], "ts": iso(ms(ctx["now"]))}, ctx)
    if not o["sz"]:
        raise RuntimeError(f"{tkey(inst, k)} 진입 체결 수량 0 ({o['state']})")
    s["trade"]["liq_px"] = ex.positions(inst).get(side, {}).get("liqPx")
    return True


def ensure_stops(ex, inst, B, key, ctx, expected=()):
    """몫마다 자기 수량의 stop-market 1개 (slTriggerPxType=last 명시). 같은 방향 몫이 추가·청산돼 거래소 평단·청산가가 바뀌면
    남은 몫 전부를 그 청산가로 다시 검사한다. 손절가는 tick 을 진입가 쪽으로 반올림한 뒤 청산가 안쪽·현재가 반대편인지 본다.
    현재가가 이미 넘었거나 청산가와 여유가 없는 손절은 보호로 보지 않고 그 몫을 청산한다.
    새 손절을 먼저 걸고 거래소 조회(ID·방향·수량·가격)로 확인한 뒤 옛 손절을 취소한다. 취소 실패 = 임시 중복(stop_old)으로 추적.
    봇 소유가 아닌 조건부 주문은 취소하지 않고 진입 차단 + 경보. 손절·청산 모두 실패한 몫은 미보호로 진입 차단."""
    m = meta(ex, inst, ctx["a"])
    pos, stops = ex.positions(inst), ex.stops(inst)
    last, tick = ex.ticker(inst)["last"], m["tickSz"]
    mine, bad = set(), {}
    for k, s in B.items():
        if not s["side"] or s["side"] not in pos:
            continue
        side, p = s["side"], pos[s["side"]]
        if s.get("stop_old"):                                # 지난번 교체 때 못 지운 옛 손절
            try:
                old = [x["algoId"] for x in stops if x["cid"] == s["stop_old"]]
                if old:
                    ex.cancel_stops(inst, old)
                s["stop_old"] = None
            except Exception as e:  # noqa: BLE001
                mine.add(s["stop_old"])
                bad[f"unprotected:{inst}#{k}:dup"] = f"임시 중복 손절 취소 실패: {e}"
        if not s.get("stop"):
            s["stop"] = rls.stop_px(side, s["entry"], rls.liq_dist(m["L"], m["mmr"]) - rls.LIQ_BUF)
            alert(f"{tkey(inst, k)} 손절가 기록 없음 → 최대 거리로 설정 {s['stop']:,.4f}")
        if p.get("liqPx") and (s["stop"] - p["liqPx"]) * side <= 0:
            s["stop"] = rls.tighten(side, s["stop"], rls.inside_liq(side, p["avgPx"], p["liqPx"]))
            ctx["flags"].append("stop_inside_reset")
            alert(f"{tkey(inst, k)} 손절가가 거래소 청산가({p['liqPx']:,.4f}) 바깥 → 안쪽으로 재설정 {s['stop']:,.4f}")
        want = round((math.ceil(s["stop"] / tick - 1e-9) if side > 0 else math.floor(s["stop"] / tick + 1e-9)) * tick, 10)
        liq_px = p.get("liqPx")
        if side * (last - want) <= 0 or (liq_px and side * (want - liq_px) <= 0):
            ctx["flags"].append("stop_invalid")
            alert(f"{tkey(inst, k)} 손절 {want:,.4f} 이 유효하지 않음 (현재가 {last:,.4f}, 청산가 {liq_px}) → 보호로 보지 않고 그 몫 청산")
            try:
                close_tranche(ex, inst, B, k, "sl", key + "v", ctx)
            except Exception as e:  # noqa: BLE001
                bad[f"unprotected:{inst}#{k}"] = f"유효 손절 없음 + 청산 실패: {e}"
                mine.update(x for x in (s.get("stop_cid"), s.get("stop_old")) if x)
            continue
        cur = [x for x in stops if x["cid"] == s.get("stop_cid")]
        if cur and abs(cur[0]["sz"] - s["sz"]) < 1e-9 and abs(cur[0]["trigger"] - want) < tick / 2:
            mine.add(s["stop_cid"])
            continue
        if k not in expected:
            ctx["flags"].append("stop_reset")
            alert(f"{tkey(inst, k)} 손절 주문 없음/불일치 → 재설정 {want:,.4f}")
        s["stop_n"] = s.get("stop_n", 0) + 1
        cid = f"{key}{k}{sym(inst)}sl{s['stop_n']}"[:32]
        try:
            ex.place_stop(inst, side, s["sz"], want, cid)
            if not [x for x in ex.stops(inst) if x["cid"] == cid and x["pos_side"] == side
                    and abs(x["sz"] - s["sz"]) < 1e-9 and abs(x["trigger"] - want) < tick / 2]:
                raise RuntimeError("접수 조회에서 손절이 확인되지 않음 (ID·방향·수량·가격)")
        except Exception as e:  # noqa: BLE001
            if cur:
                mine.add(s["stop_cid"])
                alert(f"{tkey(inst, k)} 손절 재설정 실패({e}) → 기존 손절 유지")
                continue
            alert(f"{tkey(inst, k)} 손절 주문 실패({e}) → 손절 없이 보유하지 않도록 즉시 청산")
            ctx["flags"].append("stop_fail")
            try:
                close_tranche(ex, inst, B, k, "sl", key + "x", ctx)
            except Exception as e2:  # noqa: BLE001
                bad[f"unprotected:{inst}#{k}"] = f"손절 실패({e}) + 청산 실패({e2})"
                alert(f"{tkey(inst, k)} 청산도 실패({e2}) → 미보호 상태 유지 표시, 진입 차단, 위험 감시에서 재시도")
            continue
        s["stop_cid"] = cid
        mine.add(cid)
        if cur:
            try:
                ex.cancel_stops(inst, [cur[0]["algoId"]])
            except Exception as e:  # noqa: BLE001
                s["stop_old"] = cur[0]["cid"]
                mine.add(cur[0]["cid"])
                bad[f"unprotected:{inst}#{k}:dup"] = f"새 손절 확인 뒤 옛 손절 취소 실패 (임시 중복): {e}"
    orphan = [x for x in ex.stops(inst) if x["cid"] not in mine]
    own = [x["algoId"] for x in orphan if BOT_CID.match(x["cid"] or "")]
    if own:
        ex.cancel_stops(inst, own)
    foreign = {f"foreign:{inst}": f"봇 소유가 아닌 조건부 주문 {len(orphan) - len(own)}건 (자동 취소하지 않음)"} if len(orphan) > len(own) else {}
    for b in new_blocks(f"unprotected:{inst}", bad) + new_blocks(f"foreign:{inst}", foreign):
        alert(f"진입 차단: {b} — {(bad | foreign)[b]}")


def save(inst, B):
    commit(inst, B)


def used_margin():
    return sum(s.get("margin", 0.0) for i in COINS for s in books(i).values() if s["side"])


# ---------- 작업: 몫 k (UTC TRANCHES[k]:02) ----------
def job_tranche(ex, now, ctx, k):
    a, off = ctx["a"], TRANCHES[k]
    day = pd.Timestamp(now.date())
    key = f"r{now:%y%m%d}{off:02d}"                         # 일손실·킬 판정과 비상 청산은 run_job 의 risk_step 이 이미 했다
    sig_day = day + pd.Timedelta(hours=off) - pd.Timedelta(days=1)       # 방금 확정된 off 경계 일봉의 시작
    regimes = kv_get("regimes") or {}
    for inst in COINS:                                   # BTC → ETH (RLSC_COINS 순서)
        B = books(inst)
        moved = set()
        try:
            reconcile(ex, inst, B, ctx)
            s = B[k]
            d = bars(ex, inst, now, off)
            x = rls.prep(d)
            gap = sig_day not in x.index or np.isnan(x.atr.get(sig_day, np.nan)) or len(x) < rls.SMA + rls.SLOPE + 2
            if not s.get("seeded") and not gap:
                s["blk"], s["seeded"] = seed_edge(inst, bars(ex, inst, now, off, SEED_DAYS)), True
                if s["blk"]:
                    ctx["notes"].append(f"{sym(inst)}#{k} 시작: 과거 손절/DROP1 뒤 같은 방향 지속 → 꺼졌다 켜질 때까지 대기")
            if gap:
                ctx["flags"].append(f"data_gap:{sym(inst)}#{k}")
                ctx["notes"].append(f"{sym(inst)}#{k} 확정 일봉 공백 → 신호 갱신 없이 보유·손절만 유지")
            else:
                sig = x.loc[sig_day]
                regimes[tkey(inst, k)] = [int(sig.reg), int(sig.conf)]
                if s["side"]:
                    why = ("regime" if sig.conf != s["side"] else
                           "drop1" if rls.drop1(s["side"], sig.close, sig.prev, sig.atr) else None)
                    if why:
                        close_tranche(ex, inst, B, k, why, key, ctx)
                if s.get("blk") and sig.conf != s["blk"]:
                    s["blk"] = None                          # 원하는 방향이 꺼짐/반대 → edge 해제
                if not s["side"] and sig.conf and sig.conf != s.get("blk"):
                    if not rls.vol_ok(sig.atr, sig.close):           # C②: 변동성 과대 → 진입 대기 (다음 작업에서 다시 본다)
                        ctx["flags"].append(f"vol_wait:{sym(inst)}#{k}")
                        ctx["notes"].append(f"{sym(inst)}#{k} ATR {sig.atr / sig.close:.1%} > {rls.ATR_MAX:.0%} → 진입 대기")
                    elif blocks := entry_blocks(risk_get(), now, a):
                        ctx["flags"].append("entry_hold")
                        ctx["notes"].append(f"{sym(inst)}#{k} 진입 차단: {', '.join(blocks)[:200]}")
                    elif open_tranche(ex, inst, B, k, int(sig.conf), sig, key, ctx):
                        moved.add(k)
                elif s["side"] and not s.get("be_ts") and rls.be_hit(s["side"], sig.close, s["ref"], s["atr_e"]):
                    s["stop"] = rls.tighten(s["side"], s["stop"], rls.be_px(s["side"], s["entry"]))
                    s["be_ts"] = iso(ms(now))
                    ctx["flags"].append(f"be:{sym(inst)}#{k}")
                    moved.add(k)
            ensure_stops(ex, inst, B, key, ctx, expected=moved)
        except Exception as e:  # noqa: BLE001
            log.exception("%s 실패", tkey(inst, k))
            alert(f"{tkey(inst, k)} 실행 실패, 주문 없이 종료 (거래소 손절은 유지): {e}")
        finally:
            save(inst, B)
    kv_set("regimes", regimes)
    base = baselines(ex, pd.Timestamp(a.setdefault("start_day", str(day.date())))) if k == 0 else {}
    record_day(day.date(), ctx, snapshot=k == 0, regimes=regimes, **base)


def baselines(ex, start):
    """같은 기간 기준선 (시작일 대비): 코인 롱·숏 RLS 1몫(UTC 00, 같은 레버리지), BTC 1x 보유."""
    try:
        coins = {i: bars(ex, i, dt.datetime.now(dt.UTC), 0, (pd.Timestamp.now() - start).days + COIN_WARMUP)
                 for i in rls.COINS}
        if max(c.index[-1] for c in coins.values()) <= start:
            return {}
        eq = rls.simulate(coins, {}, lambda _: set(), set(), start, sleeves=("coin",))[0]
        btc = coins["BTC-USDT-SWAP"].close
        return {"coin_only": float(eq.iloc[-1] / eq.iloc[0] - 1),
                "btc_hold": float(btc.iloc[-1] / btc[btc.index < start].iloc[-1] - 1)}
    except Exception as e:  # noqa: BLE001
        log.warning("기준선 계산 실패: %s", e)
        return {}


# ---------- 작업: 점검 (매시 07분) ----------
def job_guard(ex, now, ctx):
    """진입·청산 판단 없이 거래소 상태만 맞춘다: 대사(미확정 주문·몫 손절 체결·강제청산), 몫별 손절 복구.
    (킬·일손실과 비상 청산은 run_job 의 risk_step 이 먼저 처리한다.)"""
    for inst in COINS:
        B = books(inst)
        try:
            reconcile(ex, inst, B, ctx)
            ensure_stops(ex, inst, B, f"r{now:%y%m%d%H}g", ctx)
        except Exception as e:  # noqa: BLE001
            log.exception("%s 점검 실패", inst)
            alert(f"{inst} 점검 실패 (거래소 손절은 유지): {e}")
        finally:
            save(inst, B)
    if ctx["flags"] or ctx["notes"]:
        record_day(now.date(), ctx, snapshot=False)


# ---------- 실거래 전 점검 (조회만) ----------
def min_capital(ex):
    """상품별 최소 주문을 맞추는 데 필요한 실현 잔고 (현재가 기준): 최소 명목 / (몫 증거금 비율 × L)."""
    out = {}
    for inst in COINS:
        m = ex.instrument(inst)
        out[inst] = m["minSz"] * m["ctVal"] * ex.ticker(inst)["last"] / (MARGIN / N * rls.lever_for(m["lever"]))
    return out


def preflight(ex):
    """demo/live 시작 전 점검. 주문·설정 변경 없이 조회만 한다. → (막는 문제 목록, 참고 목록)"""
    bad, info = [], []
    cfg = ex.config()
    perm = cfg.get("perm", "")
    if cfg.get("posMode") != "long_short_mode":
        bad.append(f"포지션 모드 {cfg.get('posMode')} → long/short(헤지) 모드로 바꿔야 함 (OKX 앱: 설정 > 포지션 모드)")
    if "trade" not in perm:
        bad.append(f"API 키 권한 '{perm}' 에 거래(trade) 없음")
    if "withdraw" in perm:
        info.append("API 키에 출금 권한이 있음 → 출금 권한 없는 키 권장")
    if str(cfg.get("acctLv")) == "1":
        bad.append("계정 모드가 '현물(Spot)' → 선물 거래 가능한 모드(Futures/Spot and futures)로 바꿔야 함")
    eq, avail, realized = ex.balance()
    info.append(f"USDT 자산 {eq:.2f} · 실현 {realized:.2f} · 가용 {avail:.2f}")
    need = min_capital(ex)
    for inst, v in need.items():
        info.append(f"{sym(inst)} 최소 주문에 필요한 실현 잔고 ≈ {v:.1f} USDT ({'충족' if realized >= v else '부족 → 이 상품은 건너뜀'}) · "
                    f"지금 잔고로 최소 주문을 맞추는 종목당 증거금 ≈ {MARGIN * v / realized * (1 + SIZE_BUF):.2%} (현재 {MARGIN:.2%})"
                    if realized > 0 else f"{sym(inst)} 실현 잔고 0")
    if realized < min(need.values()):
        bad.append(f"실현 잔고 {realized:.2f} USDT < 최소 {min(need.values()):.1f} USDT → 어떤 주문도 최소 단위 미달")
    for inst in COINS:
        m = ex.instrument(inst)
        if m["lever"] < rls.LEVER:
            info.append(f"{sym(inst)} 거래소 최대 레버리지 {m['lever']:g}x < {rls.LEVER}x → {m['lever']:g}x 로 운용")
        if any(s["side"] for s in books(inst).values()):
            continue                                         # 봇이 연 포지션은 정상
        if ex.positions(inst) or ex.stops(inst):
            bad.append(f"{inst} 에 봇이 모르는 포지션/조건부 주문이 있음 → 정리 후 시작 (봇 장부와 어긋남)")
    for inst in set(rls.COINS) - set(COINS):
        if any(s["side"] for s in books(inst).values()):
            bad.append(f"{inst} 에 봇 포지션이 남아 있는데 RLSC_COINS 에서 빠짐 → 관리·손절 점검이 끊긴다. 청산 후 제외")
    prev, busy = tranche_busy()
    if busy:
        bad.append(f"몫 구성 {prev} → {list(TRANCHES)} 변경인데 열린 몫·미확정 주문 {busy} → 청산 후 바꾼다 (장부가 순번으로 저장돼 다른 시각 봉으로 관리됨)")
    elif prev is not None:
        info.append(f"몫 구성 {prev} → {list(TRANCHES)} 변경: 시작 때 몫 장부 초기화 (열린 몫 없음)")
    return bad, info


def tranche_busy():
    """지난 실행(kv config)의 몫 구성과 지금 RLSC_TRANCHES 가 다르면 → (이전 구성, 열린 몫·미확정 주문). 같거나 처음이면 (None, [])."""
    prev = (kv_get("config") or {}).get("strategy", {}).get("tranches_utc")
    if prev is None or list(prev) == list(TRANCHES):
        return None, []
    ks = range(max(len(prev), N))
    return prev, [tkey(i, k) for i in sorted(set(COINS) | set(rls.COINS)) for k in ks if ledger(i, k).get("side")] + list(kv_get("pending", {}))


def tranche_guard():
    """몫 장부는 순번 #k 로 저장된다. 몫 구성이 바뀌면 같은 #k 가 다른 시각 봉으로 관리되므로
    열린 몫·미확정 주문이 있으면 시작하지 않고, 없으면 몫 장부(edge·연패 포함)를 비워 새 구성으로 다시 복원한다."""
    prev, busy = tranche_busy()
    if busy:
        alert(f"몫 구성 {prev} → {list(TRANCHES)} 변경인데 열린 몫·미확정 주문 {busy} → 시작하지 않음. "
              f"먼저 청산하거나 RLSC_TRANCHES={','.join(map(str, prev))} 로 되돌린다")
        sys.exit(0)                                           # 정상 종료 = systemd 가 재시작하지 않는다
    if prev is not None:
        for i in set(COINS) | set(rls.COINS):
            for k in range(max(len(prev), N)):
                kv_set(tkey(i, k), None)
        a = kv_get("acct", {})
        a.pop("streaks", None), a.pop("streak", None)
        kv_set("acct", a)
        kv_set("config", effective_config())
        log.info("몫 구성 %s → %s: 몫 장부 초기화 (edge 는 다음 몫 작업에서 과거 리플레이로 복원)", prev, list(TRANCHES))


# ---------- 실행 ----------
def run_job(job, ex=None, now=None):
    """job: t0|t1|t2 (몫 작업) · guard (점검) · risk (위험 감시 1회). 모든 작업이 먼저 risk_step 을 거친다."""
    now = now or dt.datetime.now(dt.UTC)
    with lock():
        ex = ex or make_client()
        try:
            try:
                if isinstance(ex, okx.Paper):
                    ex.sync()
                equity, avail, realized = ex.balance()
            except Exception as e:  # noqa: BLE001
                def stale(r):                                # 조회 실패: 신규 진입 차단, 기존 보호 주문은 그대로, 노출 0 이라고 쓰지 않는다
                    r["block"]["stale"] = f"{pd.Timestamp(now).isoformat(timespec='seconds')} 계좌 조회 실패: {e}"
                    r["state"], r["err"] = "DEGRADED", str(e)
                    r["t"]["last_check"] = pd.Timestamp(now).isoformat(timespec="seconds")
                risk_update(stale)
                raise
            a = kv_get("acct", {})
            ctx = {"a": a, "ex": ex, "now": now, "equity": equity, "avail": avail, "realized": realized,
                   "flags": [], "notes": [], "blocked": 0, "used": used_margin()}
            try:
                r = risk_step(ex, ctx)
                ctx.update(ref_eq=r["day"]["eq"], hwm=r["hwm"])
                if job == "risk":
                    last = r["t"].get("protect")
                    due = any(b.startswith(("unprotected", "pending")) for b in r["block"])
                    if due or not last or (pd.Timestamp(now) - pd.Timestamp(last)).total_seconds() >= PROTECT_SEC:
                        risk_update(lambda x: x["t"].update(protect=pd.Timestamp(now).isoformat(timespec="seconds")))
                        job_guard(ex, now, ctx)
                    return r
                if ex.config().get("posMode") != "long_short_mode":
                    raise RuntimeError("OKX 계정을 long/short(헤지) 포지션 모드로 설정해야 합니다 (시각 분할 몫별 손절)")
                ob = okx_balance(ex)
                if ob:
                    kv_set("okx_balance", ob)
                kv_set("config", effective_config())
                log.info("=== %s %s · 설정 %s · 위험 %s · 자산 %.2f (실현 %.2f, 가용 %.2f) · 증거금 %.2f%s%s ===", MODE, job,
                         rls.config_hash(CFG), r["state"], equity, realized, avail, ctx["used"],
                         f" · 차단 {', '.join(entry_blocks(r, now, a))[:200]}" if entry_blocks(r, now, a) else "",
                         f" · OKX{' 데모' if ob['demo'] else ''} 잔액 {ob['eq']:.2f} USDT (가용 {ob['avail']:.2f})" if ob else "")
                if job == "guard":
                    job_guard(ex, now, ctx)
                else:
                    job_tranche(ex, now, ctx, int(job.removeprefix("t")))
            finally:
                kv_set("acct", a)
        finally:
            if isinstance(ex, okx.Paper):
                kv_set("paper", ex.st)


def next_job(now):
    """다음 (시각, 작업)."""
    n = pd.Timestamp(now)
    jobs = []
    for k, off in enumerate(TRANCHES):
        t = n.normalize() + pd.Timedelta(hours=off, minutes=RUN_MINUTE)
        jobs.append((t if t > n else t + pd.Timedelta(days=1), f"t{k}"))
    g = n.floor("h") + pd.Timedelta(minutes=GUARD_MINUTE)
    jobs.append((g if g > n else g + pd.Timedelta(hours=1), "guard"))
    return min(jobs)


def schedule_forever():
    """몫 작업·점검은 정해진 시각에, 그 사이에는 RISK_POLL 초마다 위험 감시 (같은 프로세스에서 차례로 → 작업과 겹치지 않음).
    다른 프로세스(수동 --job)가 락을 쥐고 있으면 그 감시 1회는 건너뛰고, 락 대기가 길어지면 경보한다."""
    with lock():
        tranche_guard()
    cfg = effective_config()
    kv_set("config", cfg)
    log.info("몫 %d개: UTC %s · 위험 감시 %gs · 설정 %s · 코드 %s", N, ", ".join(f"{h:02d}:{RUN_MINUTE:02d}" for h in TRANCHES),
             RISK_POLL, cfg["strategy_hash"], cfg["code"])
    if MODE in ("demo", "live"):
        with lock():
            bad, info = preflight(make_client())
        for x in info:
            log.info("점검: %s", x)
        if bad:
            alert("실거래 시작 점검 실패 → 시작하지 않음: " + " / ".join(bad))
            sys.exit(0)                                       # 정상 종료 = systemd 가 재시작하지 않는다 (고친 뒤 수동 시작)
        alert(f"{MODE} 시작: 점검 통과 — " + " / ".join(info))
    t, job = next_job(dt.datetime.now(dt.UTC))
    log.info("다음 작업 %s %s", job, t)
    last_err, busy_since = None, None
    while True:
        due = pd.Timestamp.now(tz="UTC") >= t
        run = job if due else "risk"
        try:
            run_job(run)
            last_err, busy_since = None, None
        except RuntimeError as e:
            if "락 획득 실패" not in str(e):
                raise_alert = str(e) != last_err
                last_err = str(e)
                if raise_alert or run != "risk":
                    alert(f"{run} 작업 실패 (거래소 손절은 유지): {e}")
            else:
                busy_since = busy_since or time.time()
                if time.time() - busy_since > 120:
                    alert(f"위험 감시가 {time.time() - busy_since:.0f}초째 락을 못 잡음 (다른 프로세스 작업 중?)")
                    busy_since = time.time()
                if due:
                    continue                                  # 몫 작업은 락이 풀릴 때까지 다시 시도
        except Exception as e:  # noqa: BLE001
            if str(e) != last_err or run != "risk":
                alert(f"{run} 작업 실패 (거래소 손절은 유지): {e}")
            last_err = str(e)
        if due:
            t, job = next_job(dt.datetime.now(dt.UTC))
            log.info("다음 작업 %s %s", job, t)
        time.sleep(max(0.0, min(RISK_POLL, (t - pd.Timestamp.now(tz="UTC")).total_seconds())))


def resume():
    """수동 확인 뒤: 킬(정지)·진입 중단·연패 해제. 킬을 풀면 재개 시점 자산이 새 고점. 오늘의 일손실 사유는 날짜가 바뀌어야 풀린다."""
    a = kv_get("acct", {})
    a.update(halted=False, hold=None, streak=0, streaks={})
    kv_set("acct", a)

    def f(r):
        if r["flatten"].pop("kill", None):
            r["hwm"] = None
        if not flatten_reasons(r, dt.datetime.now(dt.UTC)):
            r["state"], r["err"] = "RUNNING", None
    return risk_update(f)


def export_legacy():
    """롤백용: 새 위험 상태를 옛 코드(R1)가 읽는 acct 필드로 되쓴다. 미확정 주문이 있으면 거부 (옛 코드는 확정하지 못함)."""
    if kv_get("pending"):
        raise SystemExit(f"미확정 주문 {list(kv_get('pending'))} — 확정된 뒤 롤백")
    r, a = risk_get(), kv_get("acct", {})
    d = r.get("day") or {}
    a.update(peak=r.get("hwm"), rec_eq=d.get("eq"), rec_day=d.get("date"), halted="kill" in r["flatten"],
             dl_day=r["flatten"].get("daily_loss"))
    kv_set("acct", a)
    return a


if __name__ == "__main__":
    if "--export-legacy" in sys.argv:
        print("acct ←", export_legacy())
        sys.exit(0)
    if "--resume" in sys.argv:
        resume()
        sys.exit("정지·진입 중단 해제")
    if "--preflight" in sys.argv:
        bad, info = preflight(make_client())
        print("\n".join([f"[{MODE}] 참고: {x}" for x in info] + [f"[{MODE}] 문제: {x}" for x in bad]
                        + [f"[{MODE}] 점검 {'실패' if bad else '통과'}"]))
        sys.exit(1 if bad else 0)
    if "--job" in sys.argv:
        with lock():
            tranche_guard()
        run_job(sys.argv[sys.argv.index("--job") + 1])
    else:
        schedule_forever()
