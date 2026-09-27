"""레버리지 권고 × 실제 주문 제약 — autotrade.act → execute → okx.open_position 경로를 **순수 함수로** 재현한다.
주문·레버리지 설정 API 는 부르지 않는다. 네트워크는 OKX 공개 상품 명세(GET, 인증 없음) 한 번뿐이다.

추적한 실제 코드 (HEAD):
  autotrade.py:539/585  notional = equity × POSITION_PCT × lev × (1 − CASH_RESERVE)       (자동 경로)
  autotrade.py:419      X.setup(ex, E.exchange_lev(lev))  → 거래소 레버리지 = ceil(lev)
  okx.py:159-160        notional < MIN_ORDER(10 USDT) → ValueError  (자동 경로의 최소 주문 가드)
  okx.py:153-155        계약 수 = amount_to_precision(qty_btc / contractSize)  → ccxt 기본 TRUNCATE (lotSz 단위 내림)
  okx.py:161-163        계약 수 0 → ValueError
  autotrade.py:430-438  위 예외는 execute 의 except 로 잡혀 status='unknown' · 장부 불변 · False 반환
  autotrade.py:755-756  **대시보드 수동 매수 전용** 가드 (cash × POSITION_PCT × LEVERAGE < MIN_ORDER). 자동 경로 아님.
  minSz 검사는 코드에 없다 → 0 < n < minSz 이면 거래소가 거절한다 (여기서는 lotSz=minSz 라 truncate 결과가 0 또는 ≥minSz).

python sizing.py [--equity=7.28] [--price=80996] → out/sizing.csv · out/okx_instrument.json · out/sizing_backtest.csv
"""
import datetime as dt
import json
import math
import sys
import urllib.request
from decimal import ROUND_DOWN, Decimal

import common as C
from common import E, np

sys.stdout.reconfigure(encoding="utf-8")
EQ = float(next((a.split("=")[1] for a in sys.argv if a.startswith("--equity=")), 7.28))
PX = float(next((a.split("=")[1] for a in sys.argv if a.startswith("--price=")), 80996))
POSITION_PCT, CASH_RESERVE, MIN_ORDER, TAKER = 1.0, 0.01, 10.0, 0.0005   # .env POSITION_PCT=100 · CASH_RESERVE_PCT 미설정(기본 1)


def spec():
    url = "https://www.okx.com/api/v5/public/instruments?instType=SWAP&instId=BTC-USDT-SWAP"
    at = dt.datetime.now(dt.timezone.utc).isoformat()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (audit read-only)"}), timeout=15) as r:
            d = json.load(r)["data"][0]
        keep = {k: d[k] for k in ("instId", "ctVal", "ctValCcy", "ctType", "lotSz", "minSz", "settleCcy", "lever", "state")}
        return {"source": url, "fetched_at_utc": at, **keep}, float(d["ctVal"]), float(d["lotSz"]), float(d["minSz"])
    except Exception as e:   # noqa: BLE001 — 오프라인이면 research_lev 의 가정값으로 계산하고 '미확인' 으로 적는다
        return {"source": url, "fetched_at_utc": at, "error": str(e), "assumed": "ctVal=0.01 lotSz=0.01 minSz=0.01"}, 0.01, 0.01, 0.01


def order(eq, px, lev, ct, lot, mn):
    """→ 한 줄 dict. 실패하면 fail 에 코드 위치."""
    notional = eq * POSITION_PCT * lev * (1 - CASH_RESERVE)
    raw = notional / px / ct
    n = float(Decimal(str(raw)).quantize(Decimal(str(lot)), rounding=ROUND_DOWN))
    xlev = E.exchange_lev(lev)
    act = n * ct * px
    fail = ""
    if notional < MIN_ORDER:
        fail = "okx.py:159 notional < MIN_ORDER(10)"
    elif n <= 0:
        fail = "okx.py:162 계약 수 0"
    elif n < mn:
        fail = "거래소 거절 (minSz, 코드 검사 없음)"
    margin = act / xlev if not fail else 0.0
    return {"set_lev": lev, "exchange_lev": xlev, "target_notional": notional, "raw_contracts": raw, "contracts": n if not fail else 0.0,
            "notional": act if not fail else 0.0, "margin": margin, "margin_%eq": margin / eq * 100,
            "fee_open": act * TAKER if not fail else 0.0, "eff_lev": (act / eq) if not fail else 0.0,
            "liq_move%": -(1 / xlev - E.MMR) * 100, "pass": not fail, "fail": fail,
            "pos_pct_engine": (act / (eq * lev)) if not fail else 0.0}


meta, CT, LOT, MN = spec()
meta["note"] = "현재 명세다. 과거 시점(백테스트 2021~) 의 명세를 입증하지 않는다."
(C.OUT / "okx_instrument.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(meta, ensure_ascii=False))
rows = [order(EQ, PX, lev, CT, LOT, MN) for lev in (1, 2, 3, 4, 5)]
import pandas as pd  # noqa: E402

S = pd.DataFrame(rows)
S.to_csv(C.OUT / "sizing.csv", index=False, encoding="utf-8-sig")
pd.set_option("display.width", 250)
print(f"자산 {EQ} USDT · BTC {PX:,.0f}")
print(S.round(4).to_string(index=False))

# 1배가 가능해지는 최소 자산 (lotSz 한 칸 = ct×lot×px 를 넘고 MIN_ORDER 도 넘어야 한다)
unit = CT * LOT * PX
need1 = max(MIN_ORDER, unit) / (POSITION_PCT * (1 - CASH_RESERVE))
print(f"1배 주문이 가능한 최소 자산 ≈ {need1:.2f} USDT (한 칸 {unit:.2f} USDT)")

# ── 이산 계약 근사 백테스트: 실제 걸리는 명목/증거금을 pos_pct 로 고정 (자산 경로에 따른 반올림 변화는 무시 = 근사) ──
bt = []
for r in rows:
    if not r["pass"]:
        continue
    for buf in (0, 2):
        res = C.run_okx(buf, r["set_lev"], pos_pct=r["pos_pct_engine"])
        d = C.daily(res[0]).values
        bt.append({"set_lev": r["set_lev"], "pos_pct": r["pos_pct_engine"], "eff_lev": r["eff_lev"], "buf": buf,
                   "sharpe_cv": C.sharpe_cv(d), "cum%": (res[0].iloc[-1] - 1) * 100,
                   "mdd%": (res[0] / res[0].cummax() - 1).min() * 100,
                   "valid_sh": C.Q.metrics(res[0], C.rets(res), res[2], C.SPLIT, None)["sharpe"]})
BT = pd.DataFrame(bt)
BT.to_csv(C.OUT / "sizing_backtest.csv", index=False, encoding="utf-8-sig")
print(BT.round(4).to_string(index=False))
assert rows[0]["pass"] is False and "okx.py" in rows[0]["fail"], "1배는 이 자산에서 자동 경로 가드에 걸려야 한다"
