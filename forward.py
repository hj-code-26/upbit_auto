"""실거래 검증: 운용 기록을 같은 기간 계좌 단위 리플레이(backtest.account_replay)와 비교한다.
주문·설정 변경 없음. 운용 DB 는 읽기 전용으로 연다. 결과 → forward.json (대시보드·Hub 가 읽는다), 새 문제만 경보.

판단 기준 (미리 정해 둔다 — 결과를 보고 바꾸지 않는다. 바꾸면 설정 해시처럼 기록을 남긴다):
  구현  운용 거래·보유마다 같은 몫·상품·방향·진입 시각(±1시간)의 리플레이 거래가 있고, 청산 사유가 같고, 체결가 차이 ≤ 50bp.
        리플레이에만 있는 진입은 그때 운용 잔고로 최소 수량 미달이면 '예상된 건너뜀' (불일치 아님).
  위험  거래 1건 손실 < 기준 손절 손실(리플레이 p5) × 1.5, 검증 기간 낙폭 < 리플레이 최대 낙폭.
  성과  30·90일 수익률이 리플레이 같은 길이 구간의 하위 5% 밖이면 '재검토' (자동 조치 없음. 킬 −35% 는 봇이 따로 한다).
검증 기간은 봇이 기록한 설정 해시가 바뀐 시각부터 다시 센다 (규칙을 바꾸면 처음부터).
리플레이는 시작 150일 전부터 돌려 edge 상태를 만든다 (운용의 seed_edge 와 같은 구실). 시작 시점에 리플레이만 보유 중인
포지션은 비교에서 뺀다 (운용은 무포지션으로 시작).
사용 (봇 폴더, 봇과 같은 사용자·MODE): python forward.py"""
import datetime as dt
import json
import sqlite3

import pandas as pd

import autotrade as A
import backtest as B
import okx

OUT = A.ROOT / "forward.json"
BANDS = A.ROOT / "reports" / "rlsc_runs" / "forward_bands.json"   # 배포 스크립트가 rlsc_runs 를 통째로 복사한다
PRICE_BP, LOSS_X, WARM_DAYS, TOL = 50, 1.5, 150, pd.Timedelta(hours=1)


def ro(sql, args=()):
    """운용 DB 읽기 전용 조회."""
    with sqlite3.connect(f"file:{A.DB_PATH}?mode=ro", uri=True) as c:
        return c.execute(sql, args).fetchall()


def kv(k, default=None):
    r = ro("SELECT v FROM kv WHERE k=?", (k,))
    return json.loads(r[0][0]) if r else default


def since_for(h, prev, now):
    """검증 시작 = 이 설정 해시를 처음 본 시각 (이전 결과와 해시가 같으면 이어 간다)."""
    return pd.Timestamp(prev["since"]) if prev.get("hash") == h else pd.Timestamp(now).floor("min")


def ts(x):
    return pd.Timestamp(x).tz_localize(None) if pd.Timestamp(x).tzinfo else pd.Timestamp(x)


def match(live, rep, since, eq_at, cfg, price):
    """운용(live) ↔ 리플레이(rep) 거래·보유 맞추기 → (일치 수, 불일치 목록)."""
    used, out, ok = set(), [], 0
    for L in live:
        cand = [j for j, R in enumerate(rep) if j not in used and (R["inst"], R["tranche"], R["side"]) == (L["inst"], L["tranche"], L["side"])
                and abs(ts(R["entry_ts"]) - ts(L["entry_ts"])) <= TOL]
        if not cand:
            out.append({"kind": "운용만 진입", **L})
            continue
        j = min(cand, key=lambda j: abs(ts(rep[j]["entry_ts"]) - ts(L["entry_ts"])))
        used.add(j)
        R, notes = rep[j], []
        if abs(L["entry"] / R["entry"] - 1) * 1e4 > PRICE_BP:
            notes.append(f"진입가 차이 {abs(L['entry'] / R['entry'] - 1) * 1e4:.0f}bp")
        if (L.get("why") or "open") != (R.get("why") or "open"):
            notes.append(f"청산 사유 운용 {L.get('why') or '보유 중'} ≠ 리플레이 {R.get('why') or '보유 중'}")
        elif L.get("exit") and R.get("exit") and abs(L["exit"] / R["exit"] - 1) * 1e4 > PRICE_BP:
            notes.append(f"청산가 차이 {abs(L['exit'] / R['exit'] - 1) * 1e4:.0f}bp")
        if notes:
            out.append({"kind": "내용 불일치", **L, "note": " · ".join(notes)})
        else:
            ok += 1
    for j, R in enumerate(rep):
        if j in used or ts(R["entry_ts"]) < since.floor("h"):
            continue
        ct, lot, mn = B.INST_META[R["inst"]]
        eq = eq_at(ts(R["entry_ts"]))
        sz = okx.floor_to(eq * cfg["margin_per_symbol"] / len(cfg["tranches_utc"]) * cfg["lever"] / (ct * price(R) * 1.001), lot) if eq else 0
        out.append({"kind": "예상된 건너뜀 (최소 수량 미달)" if sz < mn else "리플레이만 진입", **R})
    return ok, out


def main():
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    prev = json.loads(OUT.read_text()) if OUT.exists() else {}
    conf = kv("config")
    if not conf:
        raise SystemExit("봇이 기록한 설정(kv config)이 없음 — 봇을 먼저 시작")
    cfg, h = conf["strategy"], conf["strategy_hash"]
    since = since_for(h, prev, now)
    offs, N = tuple(cfg["tranches_utc"]), len(cfg["tranches_utc"])
    start = since.floor("D") - pd.Timedelta(days=WARM_DAYS)
    H = {i: B.confirmed(okx.Client().candles(i, int(start.timestamp() * 1000) - 3_600_000)) for i in cfg["insts"]}
    e, t, cnt = B.account_replay(H, offs, start, margin=cfg["margin_per_symbol"], lever_=cfg["lever"])
    o1 = {i: pd.Series(H[i].open.to_numpy(), index=pd.to_datetime(H[i].ts, unit="ms")) for i in H}

    daily = pd.Series({pd.Timestamp(d): q for d, q in ro("SELECT date, equity FROM daily WHERE equity IS NOT NULL ORDER BY date")})
    daily = daily[daily.index >= since.floor("D")]
    eq_at = lambda t_: float(daily[daily.index <= t_].iloc[-1]) if (daily.index <= t_).any() else (kv("risk", {}).get("day") or {}).get("eq")  # noqa: E731

    live = []
    for inst, side, e0, e1, px0, px1, why, pnl, pp in ro("SELECT inst, side, entry_ts, exit_ts, entry_px, exit_px, why, pnl, pnl_pct FROM trades"):
        if e0 and ts(e0) >= since.floor("h"):
            i, k = inst.split("#")
            live.append({"inst": i, "tranche": int(k), "side": side, "entry_ts": e0, "exit_ts": e1, "entry": px0, "exit": px1,
                         "why": why, "pnl_pct": pp})
    for i in cfg["insts"]:
        for k in range(N):
            s = kv(f"{i}#{k}") or {}
            if s.get("side") and (s.get("trade") or {}).get("entry_ts") and ts(s["trade"]["entry_ts"]) >= since.floor("h"):
                live.append({"inst": i, "tranche": k, "side": s["side"], "entry_ts": s["trade"]["entry_ts"], "entry": s["entry"],
                             "why": None, "stop": s.get("stop"), "be": bool(s.get("be_ts"))})
    rep = [r for r in t.to_dict("records") if ts(r["entry_ts"]) >= since.floor("h")]
    rep += [{**r, "why": None, "exit": None} for r in cnt["open"] if ts(r["entry_ts"]) >= since.floor("h")]
    pre = [r for r in cnt["open"] if ts(r["entry_ts"]) < since.floor("h")]
    price = lambda R: float(o1[R["inst"]].get(ts(R["entry_ts"]), R["entry"]))  # noqa: E731
    ok, mis = match(live, rep, since, eq_at, cfg, price)

    band = json.loads(BANDS.read_text()).get(h) if BANDS.exists() else None
    flags = [f"구현 불일치: {m['kind']} {m['inst'].split('-')[0]}#{m['tranche']} {m['entry_ts']}" + (f" ({m['note']})" if m.get("note") else "")
             for m in mis if not m["kind"].startswith("예상된")]
    risk, perf = {}, {}
    if band:
        lim = band["sl_loss_pct_of_equity"]["p5"] * LOSS_X
        big = [m for m in live if m.get("pnl_pct") is not None and m["pnl_pct"] * 100 < lim]
        flags += [f"위험: {m['inst'].split('-')[0]}#{m['tranche']} {m['entry_ts']} 손실 {m['pnl_pct'] * 100:.2f}% < 기준 {lim:.2f}%" for m in big]
        dd = float((1 - daily / daily.cummax()).max() * 100) if len(daily) else 0.0
        risk = {"dd%": round(dd, 1), "dd_limit%": band["mdd%"], "loss_limit%": round(lim, 2)}
        if dd > band["mdd%"]:
            flags.append(f"위험: 검증 기간 낙폭 {dd:.1f}% > 리플레이 최대 낙폭 {band['mdd%']}%")
        for w in (30, 90):
            if len(daily) > w:
                r = (daily.iloc[-1] / daily.iloc[-1 - w] - 1) * 100
                p5 = band[f"ret{w}%"]["p5"]
                perf[f"ret{w}%"] = {"live": round(r, 2), "replay_p5": p5, "replay_p50": band[f"ret{w}%"]["p50"]}
                if r < p5:
                    flags.append(f"성과 재검토: 최근 {w}일 {r:+.1f}% < 리플레이 하위 5% {p5:+.1f}%")
    if len(daily) > 1:
        rs = e[e.index >= daily.index[0]]
        perf["since%"] = {"live": round((daily.iloc[-1] / daily.iloc[0] - 1) * 100, 2),
                          "replay": round((rs.iloc[-1] / rs.iloc[0] - 1) * 100, 2) if len(rs) > 1 else None}
    days = (now - since).total_seconds() / 86400
    res = {"ts": now.isoformat(timespec="seconds"), "hash": h, "since": since.isoformat(), "days": round(days, 1),
           "matched": ok, "live_n": len(live), "replay_n": len(rep), "mismatch": mis, "replay_open_at_start": pre,
           "risk": risk, "perf": perf, "band": band is not None, "flags": flags,
           "summary": f"{days:.0f}일 · 일치 {ok}/{len(live)} · 불일치 {len(flags)}" + (f" · 낙폭 {risk['dd%']}%" if risk else "")}
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    for f in set(flags) - set(prev.get("flags", [])):
        A.alert(f"[검증] {f}")
    print(res["summary"])


if __name__ == "__main__":
    main()
