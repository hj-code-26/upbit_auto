"""과거 리플레이 (검증 체크리스트 1). 2020-04-01 ~ 2026-10-09, 한 계좌, 일봉 근사 → rls.simulate.
명세 수치: CAGR 45.2% · MDD −21.4% · Sharpe 1.44 · 거래 329 (주식 211, 코인 118) · 승률 27.4% · PF 2.30 · 강제청산 0.
데이터 출처·PIT 구성종목·체결 시각 차이로 수치는 달라질 수 있다.
  코인: OKX 1Dutc 확정 일봉 · 주식: yfinance 조정 일봉 · 구성종목: ndx_members.csv (PIT) · OKX 상장: 현재 주식 무기한 목록
  (ponytail: 과거 시점의 OKX 상장 목록은 없어서 현재 목록을 쓴다 — 명세 백테스트와 같은 가정인지 확인 필요)
캐시: data_cache/ (다시 받으려면 지운다)
사용: python backtest.py [시작일] [종료일]
      python backtest.py --coin [시작일] [종료일]   코인 전용 실운용 구성 검증 (ndx_members.csv 불필요, 네트워크 없이 캐시만)
        → reports/rlsc_runs/<run>/ manifest.json · equity.csv · trades.csv · rolling.csv, reports/rlsc_runs/summary.json
"""
import contextlib
import hashlib
import json
import pathlib
import sys
import time

import numpy as np
import pandas as pd

import okx
import rls
import stocks

CACHE = pathlib.Path(__file__).resolve().parent / "data_cache"
START, END = "2020-04-01", "2026-10-09"
WARMUP = pd.Timedelta(days=500)


def coin_daily(inst, since):
    f = CACHE / f"{inst}_1D.pkl"
    if f.exists():
        return pd.read_pickle(f)
    d = okx.Client().candles(inst, int(pd.Timestamp(since).timestamp() * 1000), bar="1Dutc")
    d = d[d.confirm == 1]
    d = d.set_index(pd.to_datetime(d.ts, unit="ms"))[["open", "high", "low", "close"]]
    d.to_pickle(f)
    return d


def coin_hourly(inst, since):
    """1H 봉 캐시 (시각 분할 리플레이용). 이어 받는다. 확정 봉만 저장한다 (미확정 봉이 남으면 이어 붙일 때 그날이 빠진다)."""
    f = CACHE / f"{inst}_1H.pkl"
    old = confirmed(pd.read_pickle(f)) if f.exists() else pd.DataFrame(columns=["ts", "confirm"])
    s = int(old.ts.max()) + 1 if len(old) else int(pd.Timestamp(since).timestamp() * 1000)
    df = confirmed(pd.concat([old, okx.Client().candles(inst, s)]).drop_duplicates("ts", keep="last").sort_values("ts"))
    df.to_pickle(f)
    return df


def confirmed(df):
    """확정 봉만, ts·confirm 정수형 (캐시 파일의 object 형이 섞이면 중복 제거가 어긋난다)."""
    df = df.astype({"ts": "int64", "confirm": "int64"})
    return df[df.confirm == 1].reset_index(drop=True)


def stock_daily(tickers, since, end):
    f = CACHE / f"stocks_1D_{hashlib.md5(','.join(sorted(tickers)).encode()).hexdigest()[:8]}.pkl"
    if f.exists():
        return pd.read_pickle(f)
    d = stocks.daily_bars(tickers, since, end)
    pd.to_pickle(d, f)
    return d


def listed():
    f = CACHE / "okx_stock_perps.json"
    if not f.exists():
        f.write_text(json.dumps(okx.Client().stock_perps()))
    return set(json.loads(f.read_text()))


def stats(eq, tr=None):
    r = eq.pct_change().dropna()
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    ye = eq.groupby(eq.index.year).last()
    mo = eq.resample("ME").last().pct_change(fill_method=None).dropna()
    out = {"cagr%": ((eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1) * 100, "mdd%": (1 - eq / eq.cummax()).max() * 100,
           "sharpe": r.mean() / r.std() * np.sqrt(365) if r.std() else 0.0,
           "worst_wk%": eq.resample("W").last().pct_change().min() * 100, "worst_mo%": mo.min() * 100,
           "pos_mo%": (mo > 0).mean() * 100, "yearly": ye / ye.shift(fill_value=eq.iloc[0]) - 1}
    if tr is not None and len(tr):
        tr = tr.sort_values("exit_ts")
        loss = tr.pnl <= 0
        out.update(trades=len(tr), by=tr.sleeve.value_counts().to_dict(), win=(~loss).mean() * 100,
                   pf=tr.pnl[~loss].sum() / -tr.pnl[loss].sum(), liq=int((tr.why == "liq").sum()),
                   streak=int(loss.groupby((~loss).cumsum()).sum().max()),
                   loss_med=tr.pnl_pct[loss].median() * 100, loss_min=tr.pnl_pct.min() * 100,
                   ex_top10=tr.pnl.sum() - tr.pnl.nlargest(10).sum(), why=tr.why.value_counts().to_dict())
    return out


def line(name, s):
    t = (f"\n{'':4s}거래 {s['trades']} {s['by']}  승률 {s['win']:.1f}%  PF {s['pf']:.2f}  강제청산 {s['liq']}  최장연패 {s['streak']}"
         f"  손실 중앙 {s['loss_med']:+.2f}% 최악 {s['loss_min']:+.2f}%  상위10 제외 손익 {s['ex_top10']:+.3f}\n{'':4s}사유 {s['why']}"
         if "trades" in s else "")
    return (f"  {name:22s} CAGR {s['cagr%']:+5.1f}%  MDD {s['mdd%']:4.1f}%  Sharpe {s['sharpe']:.2f}  최악 주 {s['worst_wk%']:+.1f}%"
            f"  최악 달 {s['worst_mo%']:+.1f}%  수익 달 {s['pos_mo%']:.0f}%{t}\n{'':4s}"
            + " ".join(f"{y}:{v * 100:+.1f}" for y, v in s["yearly"].items()))


# ---------- 코인 전용 실운용 구성 검증 (지시서 P1) ----------
RUNS = pathlib.Path(__file__).resolve().parent / "reports" / "rlsc_runs"
SPLIT = "2023-07-01"                                   # 기존 문서의 IS/OOS 경계 (설정 선택 때 이미 본 구간 → 사후 검토 구간)


def cached_hourly(inst):
    """캐시된 1H 봉만 (네트워크 없음). 없으면 받는다."""
    f = CACHE / f"{inst}_1H.pkl"
    return confirmed(pd.read_pickle(f)) if f.exists() else coin_hourly(inst, "2020-01-01")


def sha(f):
    return hashlib.sha256(pathlib.Path(f).read_bytes()).hexdigest()[:16]


def data_manifest(H, start, end):
    out = {}
    for i, h in H.items():
        t = pd.to_datetime(h.ts, unit="ms")
        gaps = t.diff().dt.total_seconds().gt(3600).sum()
        out[i] = {"source": "OKX /market/history-candles 1H (last 가격), data_cache 캐시", "tz": "UTC", "first": str(t.min()),
                  "last": str(t.max()), "rows": len(h), "unconfirmed_rows": int((h.confirm == 0).sum()),
                  "gaps_over_1h": int(gaps), "sha256_16": sha(CACHE / f"{i}_1H.pkl"),
                  "eval": [str(start), str(end)], "warmup": "평가 시작 전 봉은 신호 계산에만 (SMA50+기울기20+CONF3 ≥ 73일, ATR14)"}
    return out


def roll(eq, w):
    """완전한 w 일 창 (시작일마다, 겹침) → 창 수익률, 창 내부 최대낙폭."""
    v = eq.to_numpy()
    n = len(v) - w
    ret = v[w:] / v[:n] - 1
    mdd = np.array([1 - (v[i:i + w + 1] / np.maximum.accumulate(v[i:i + w + 1])).min() for i in range(n)])
    return pd.DataFrame({"ret": ret, "mdd": mdd}, index=eq.index[:n])


def window_stats(eq, tr, w):
    r = roll(eq, w)
    entries = pd.DatetimeIndex(tr.entry_ts).normalize() if len(tr) else pd.DatetimeIndex([])
    no_trade = np.mean([not ((entries >= a) & (entries < a + pd.Timedelta(days=w))).any() for a in r.index])
    q = r.ret.quantile([0.05, 0.25, 0.5, 0.75, 0.95])
    return {"windows": len(r), "first_start": str(r.index[0].date()), "last_start": str(r.index[-1].date()),
            "median%": q[0.5] * 100, "p5%": q[0.05] * 100, "p25%": q[0.25] * 100, "p75%": q[0.75] * 100,
            "p95%": q[0.95] * 100, "min%": r.ret.min() * 100, "max%": r.ret.max() * 100,
            "loss_frac%": (r.ret < 0).mean() * 100, "mdd_in_window_med%": r.mdd.median() * 100,
            "mdd_in_window_max%": r.mdd.max() * 100, "no_new_entry_frac%": no_trade * 100,
            "note": "겹치는 창 — 독립 표본이 아니다 (신뢰구간 계산 안 함)"}, r


def underwater(eq):
    peak = eq.cummax()
    uw = eq < peak
    runs = (uw != uw.shift()).cumsum()[uw]
    longest = int(runs.value_counts().max()) if len(runs) else 0
    cur = int(uw[::-1].cumprod().sum())
    return longest, cur


def trade_stats(tr, n_tr, cap):
    """코인 전용 구성의 거래 통계. pnl 은 몫 자본 단위 → 계좌 기여 = pnl / 몫 수."""
    tr = tr.sort_values("exit_ts").reset_index(drop=True)
    pnl = tr.pnl / n_tr
    win, loss = pnl[pnl > 0], pnl[pnl <= 0]
    net = pnl.sum()
    top = pnl.sort_values(ascending=False)
    lose = (pnl <= 0).astype(int)
    streak = int(lose.groupby((lose == 0).cumsum()).sum().max())
    ov = 0.0
    if n_tr > 1:                                       # 같은 상품·방향을 동시에 든 몫 수 (일 단위)
        days = pd.date_range(tr.entry_ts.min().normalize(), tr.exit_ts.max().normalize())
        cnt = pd.DataFrame(0, index=days, columns=pd.MultiIndex.from_product([sorted(tr.inst.unique()), (1, -1)]))
        for r in tr.itertuples():
            cnt.loc[r.entry_ts.normalize():r.exit_ts.normalize() - pd.Timedelta(days=1), (r.inst, r.side)] += 1
        held = cnt.to_numpy()[cnt.to_numpy() > 0]
        ov = float((held > 1).mean() * 100) if len(held) else 0.0
    return {"trades": len(tr), "by_inst": tr.inst.value_counts().to_dict(), "win%": len(win) / len(tr) * 100,
            "pf": (win.sum() / -loss.sum()) if loss.sum() < 0 else None, "net_pnl_acct": net,
            "max_loss_streak": streak, "why": tr.why.value_counts().to_dict(),
            "cap_bound%": float((tr.stop_dist >= cap - 1e-12).mean() * 100),
            "be_moved%": float(tr.be.mean() * 100),
            "top1_share%": top.iloc[:1].sum() / net * 100 if net > 0 else None,
            "top3_share%": top.iloc[:3].sum() / net * 100 if net > 0 else None,
            "top5_share%": top.iloc[:5].sum() / net * 100 if net > 0 else None,
            "top_abs": [float(x) for x in top.iloc[:5]],
            "by_year": pnl.groupby(tr.exit_ts.dt.year).sum().round(5).to_dict(),
            "by_inst_pnl": pnl.groupby(tr.inst).sum().round(5).to_dict(),
            "by_side_pnl": pnl.groupby(tr.side).sum().round(5).to_dict(),
            "same_side_overlap_days%": ov,
            "avg_hold_days": float((tr.exit_ts - tr.entry_ts).dt.total_seconds().mean() / 86400)}


def stop_fill_check(tr, H):
    """일봉 리플레이의 손절 체결가(손절가 또는 일봉 시가 갭)를 1H 경로로 다시 본다: 손절가를 처음 넘은 시간 봉의 시가가
    이미 넘어 있었으면(시간 봉 사이 갭) 실제는 그 시가 근처 체결 → 일봉 근사가 낙관적인 금액. 1H 도 봉 안 순서는 모른다."""
    out = {"sl_trades": 0, "hourly_gap_through": 0, "extra_loss_sum_pct_of_notional": 0.0, "liq_reached_same_hour": 0}
    for r in tr[tr.why == "sl"].itertuples():
        h = H[r.inst]
        t0 = A_ms(r.exit_ts)
        bars = h[(h.ts >= t0) & (h.ts < t0 + 86_400_000)]
        side, fill = r.side, r.exit
        hit = bars[(bars.low <= fill) if side > 0 else (bars.high >= fill)]
        out["sl_trades"] += 1
        if not len(hit):
            continue
        b = hit.iloc[0]
        if b.name != bars.index[0] and side * (b.open - fill) < 0:
            out["hourly_gap_through"] += 1
            out["extra_loss_sum_pct_of_notional"] += abs(b.open - fill) / r.entry * 100
        if (b.low <= r.liq) if side > 0 else (b.high >= r.liq):
            out["liq_reached_same_hour"] += 1                  # 같은 시간 봉에 청산가까지 감: mark 경로에 따라 강제청산이 먼저일 수 있음
    return out


def A_ms(t):
    return int(pd.Timestamp(t).timestamp() * 1000)


@contextlib.contextmanager
def lever(L, margin=None):
    old = rls.LEVER, rls.MARGIN
    rls.LEVER, rls.MARGIN = L, rls.MARGIN if margin is None else margin
    try:
        yield
    finally:
        rls.LEVER, rls.MARGIN = old


def coin_run(name, H, offs, L, start, end, cost=None, extra=None, margin=0.02, **kw):
    """한 구성 리플레이 → reports/rlsc_runs/<name>/ 에 manifest·equity·trades·rolling 저장 → 요약 dict."""
    with lever(L, margin):
        eq, tr = rls.simulate_tranches(H, offs, start, end, **kw)
        cfg = rls.strategy_config(offs, L, insts=tuple(H))
        cap = rls.liq_dist(L, rls.SIM_MMR["coin"]) - rls.LIQ_BUF
        exposure = {"margin_per_symbol": rls.MARGIN, "margin_per_tranche": rls.MARGIN / len(offs),
                    "notional_per_symbol_full": rls.MARGIN * L, "notional_gross_full_2coins": 2 * rls.MARGIN * L}
    if cost:
        cfg["cost"].update(cost)
    if "daily_loss" in kw or "kill" in kw:
        cfg["risk"].update({k: kw[k] for k in ("daily_loss", "kill") if k in kw})
    s = stats(eq, tr)
    lw, cur = underwater(eq)
    w30, r30 = window_stats(eq, tr, 30)
    w90, r90 = window_stats(eq, tr, 90)
    d = RUNS / name
    d.mkdir(parents=True, exist_ok=True)
    eq.rename("equity").to_csv(d / "equity.csv")
    tr.to_csv(d / "trades.csv", index=False)
    pd.concat({"30d": r30, "90d": r90}, axis=1).to_csv(d / "rolling.csv")
    n = len(offs)
    m = {"run": name, "period": f"{start} ~ {end}", "strategy": cfg, "strategy_hash": rls.config_hash(cfg),
         "kw": {k: v for k, v in kw.items()}, "lever": L, "tranches_utc": list(offs),
         "stats": {k: (float(v) if isinstance(v, (int, float, np.floating)) else v) for k, v in s.items() if k != "yearly"} | {
             "yearly%": {int(y): round(float(v) * 100, 2) for y, v in s["yearly"].items()},
             "longest_underwater_days": lw, "current_underwater_days": cur},
         "trades_stats": trade_stats(tr, n, cap) if len(tr) else {},
         "rolling_30d": w30, "rolling_90d": w90,
         "stop_fill_1h": stop_fill_check(tr, H) if len(tr) else {},
         "exposure": exposure | {"stop_cap": cap, "atr_over_px_cap_binds_above": cap / rls.SL_ATR},
         "assumptions": ["일봉 근사: 몫 경계 시각 시가 체결 (운용은 HH:02 시장가 — 2분 차이 미반영, 슬리피지 스트레스로 대신)",
                         "손절: 일봉 고저가 손절가를 넘으면 손절가 체결, 시가 갭이면 시가 (1H 갭은 stop_fill_1h 진단)",
                         "강제청산: last 가격 = mark 가정, 티어1 MMR 0.4% 고정 — 청산 안전성 검증 완료 아님",
                         "펀딩: 8시간 0.01% 고정 (과거 실제 펀딩 아님), 부호대로 지급/수취",
                         "몫마다 독립 자본·독립 일손실·독립 킬 (운용은 계좌 합산) — 3몫 결과는 일손실 발동을 과대 반영할 수 있음",
                         "20연패 진입 중단·강제청산 진입 중단·사람 재개는 리플레이에 없음",
                         "데이터: OKX 1H last 봉 (2020-01-01~), 누락 시간 없음 확인, 보간 없음"],
         "data": data_manifest(H, start, end), "code": code_ver(),
         "experiment_note": "설정(10x→20x→30x, 4%→2%, 1몫→3몫, CONF3·BE1·DROP1)은 2020-04~2026-10 전 구간을 본 뒤 고름 "
                            "(git: 4737945 10-06 10x, e981f63 10-09 20x DROP1, 작업 트리 10-11 30x 3몫). "
                            f"{SPLIT} 이후도 사후 검토된 구간이지 독립 검증이 아니다."}
    if extra:
        m.update(extra)
    (d / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=1, default=str))
    return m


def code_ver():
    root = pathlib.Path(__file__).resolve().parent
    out = {"files_sha": hashlib.sha256(b"".join((root / f).read_bytes() for f in ("rls.py", "backtest.py"))).hexdigest()[:12]}
    try:
        import subprocess
        g = lambda *a: subprocess.run(["git", *a], cwd=root, capture_output=True, text=True, check=True).stdout.strip()  # noqa: E731
        out.update(git=g("rev-parse", "--short", "HEAD"), dirty=bool(g("status", "--porcelain", "--", "rls.py", "backtest.py")))
    except Exception:  # noqa: BLE001
        out.update(git="unknown", dirty=None)
    return out


def coin_validation(start=START, end=END):
    H = {i: cached_hourly(i) for i in rls.COINS}
    H = {i: h[pd.to_datetime(h.ts, unit="ms") < pd.Timestamp(end) + pd.Timedelta(days=1, hours=16)] for i, h in H.items()}
    one, three = (0,), (0, 8, 16)                    # 기존 검증 런 = 명세 2%·3몫 (coin_run 기본 margin)
    jobs = [("c1-l30", one, 30, {}, None), ("c3-l30", three, 30, {}, None), ("c1-l20", one, 20, {}, None),
            ("c3-l20", three, 20, {}, None), ("c1-l10", one, 10, {}, None), ("c3-l10", three, 10, {}, None)]
    for base, offs in (("c1-l30", one), ("c3-l30", three)):
        for bp in (5, 10, 20):
            jobs.append((f"{base}-slip+{bp}bp", offs, 30, {"slip": rls.SLIP + bp / 1e4}, {"slip_one_way": rls.SLIP + bp / 1e4}))
        jobs.append((f"{base}-fund2x", offs, 30, {"funding_8h": 2 * rls.FUNDING_8H}, {"funding_8h": 2 * rls.FUNDING_8H}))
        jobs.append((f"{base}-fundadv", offs, 30, {"fund_adverse": True}, {"funding_model": "adverse: both sides pay |rate|"}))
        jobs.append((f"{base}-old-stress", offs, 30, {"slip": 0.001, "funding_8h": 2 * rls.FUNDING_8H},
                     {"slip_one_way": 0.001, "funding_8h": 2 * rls.FUNDING_8H}))
    jobs.append(("c3-l30-no-tranche-dl", three, 30, {"daily_loss": 1.0}, None))
    jobs.append(("c3-l30-kill40", three, 30, {"kill": 0.40}, None))
    out = {}
    for name, offs, L, kw, cost in jobs:
        m = coin_run(name, H, offs, L, start, end, cost=cost, **kw)
        out[name] = {"hash": m["strategy_hash"], **{k: m["stats"].get(k) for k in ("cagr%", "mdd%", "sharpe", "worst_mo%")},
                     "trades": m["trades_stats"].get("trades"), "win%": m["trades_stats"].get("win%"),
                     "pf": m["trades_stats"].get("pf"), "why": m["trades_stats"].get("why"),
                     "r30_median%": m["rolling_30d"]["median%"], "r30_p5%": m["rolling_30d"]["p5%"],
                     "r30_loss_frac%": m["rolling_30d"]["loss_frac%"], "r90_median%": m["rolling_90d"]["median%"],
                     "r90_p5%": m["rolling_90d"]["p5%"], "r90_loss_frac%": m["rolling_90d"]["loss_frac%"]}
        print(f"{name:22s} CAGR {m['stats']['cagr%']:+6.1f}%  MDD {m['stats']['mdd%']:5.1f}%  Sharpe {m['stats']['sharpe']:.2f}  "
              f"거래 {out[name]['trades']}  30일 중앙 {out[name]['r30_median%']:+.1f}% 손실창 {out[name]['r30_loss_frac%']:.0f}%  "
              f"90일 중앙 {out[name]['r90_median%']:+.1f}% 손실창 {out[name]['r90_loss_frac%']:.0f}%")
    for pct in (2.5, 3.0, 3.5, 4.0, 4.5, 5.0):            # 소액 계좌: 최소 주문을 맞추려고 종목당 증거금 % 를 올린 3몫·30x
        m = coin_run(f"c3-l30-m{pct:.1f}", H, three, 30, start, end, margin=pct / 100)
        t = m["trades_stats"]
        out[m["run"]] = {"hash": m["strategy_hash"], "margin%": pct, **{k: m["stats"].get(k) for k in ("cagr%", "mdd%", "sharpe", "worst_mo%")},
                         "trades": t.get("trades"), "why": t.get("why"), "r30_p5%": m["rolling_30d"]["p5%"],
                         "r90_median%": m["rolling_90d"]["median%"], "r90_p5%": m["rolling_90d"]["p5%"],
                         "r90_loss_frac%": m["rolling_90d"]["loss_frac%"], "worst_wk%": m["stats"].get("worst_wk%")}
        print(f"{m['run']:22s} CAGR {m['stats']['cagr%']:+6.1f}%  MDD {m['stats']['mdd%']:5.1f}%  Sharpe {m['stats']['sharpe']:.2f}  "
              f"최악 주 {m['stats']['worst_wk%']:+.1f}%  최악 달 {m['stats']['worst_mo%']:+.1f}%  사유 {t.get('why')}")
    for base, offs in (("c1-l30", one), ("c1-l20", one), ("c3-l30", three), ("c3-l20", three)):   # 사후 검토 구간 분할
        L = int(base[-2:])
        for tag, a, b in (("is", start, "2023-06-30"), ("oos", SPLIT, end)):
            m = coin_run(f"{base}-{tag}", H, offs, L, a, b)
            out[f"{base}-{tag}"] = {k: m["stats"].get(k) for k in ("cagr%", "mdd%", "sharpe")}
            print(f"{base}-{tag:3s}             CAGR {m['stats']['cagr%']:+6.1f}%  MDD {m['stats']['mdd%']:5.1f}%  Sharpe {m['stats']['sharpe']:.2f}")
    (RUNS / "summary.json").write_text(json.dumps({"period": f"{start} ~ {end}", "runs": out,
                                                   "actual": {"status": "unverified",
                                                              "note": "서버 RLSC_TRANCHES·MODE 미확인. 패치 문서상 배포 유닛 RLSC_TRANCHES=0 + 30x → c1-l30 이 해당"}},
                                                  ensure_ascii=False, indent=1, default=str))
    return out


# ---------- 계좌 단위 리플레이 (운용과 같은 구조) ----------
INST_META = {"BTC-USDT-SWAP": (0.01, 0.01, 0.01), "ETH-USDT-SWAP": (0.1, 0.01, 0.01), "ZEC-USDT-SWAP": (0.01, 1.0, 1.0)}   # ctVal, lotSz, minSz


def account_replay(H, offs, start, end=None, margin=rls.MARGIN, lever_=rls.LEVER, capital=None, mmr=rls.SIM_MMR["coin"],
                   slip=rls.SLIP, funding_8h=rls.FUNDING_8H, size_buf=0.001):
    """한 계좌 1H 리플레이 — simulate_tranches(몫마다 독립 자본·독립 일손실) 와 달리 autotrade 와 같은 구조:
    몫 증거금 = 실현 잔고 × margin / 몫 수, 동시 증거금 ≤ 일 기준 자산 10%, 일손실 = UTC 날 경계에서 일 기준 대비 −5%
    → 전량 청산·그날 진입 없음, 킬 = 일별 고점 대비 −35% (매시 종가로 판정) → 전량 청산·정지, 몫별 20연패 → 진입 중단.
    몫 작업 = 몫 경계 시각 1H 봉 시가 (운용 HH:02 시장가), 손절 = 1H 고저 (시가가 넘으면 시가, 청산가 넘으면 증거금 손실),
    펀딩 = UTC 00·08·16 고정률. capital 을 주면 그 USDT 로 시작해 수량을 lot 단위로 내림하고 최소 수량 미만이면 건너뛴다.
    → (UTC 00:00 자산 Series [시작 = 1], 거래 DataFrame, {"below_min": 건수, "cap_block": 건수, "open": 끝에 열린 포지션}).
    ponytail: 킬은 매시 종가로만 판정 (운용은 5초) — 시간 내 급락은 다음 판정까지 늦게 잡힌다."""
    N, start = len(offs), pd.Timestamp(start)
    X = {(i, off): rls.prep(rls.offset_daily(h, off, 1)) for i, h in H.items() for off in offs}
    B = {}
    for i, h in H.items():
        b = h[h.confirm == 1]
        B[i] = pd.DataFrame({"o": b.open.to_numpy(), "h": b.high.to_numpy(), "l": b.low.to_numpy(), "c": b.close.to_numpy()},
                            index=pd.to_datetime(b.ts.astype("int64"), unit="ms")).astype(float)
    clock = sorted(set.intersection(*(set(b.index) for b in B.values())))
    clock = [t for t in clock if t >= start and (end is None or t < pd.Timestamp(end) + pd.Timedelta(days=1))]
    cash = float(capital or 1.0)
    pos, blk, streak, trades, eqs = {}, {}, [0] * N, [], {}
    day_ref = hwm = None
    dl_day, halted, hold = None, False, False
    cnt = {"below_min": 0, "cap_block": 0}

    def mark(px):
        return cash + sum(p["side"] * p["qty"] * (px[p["i"]] - p["entry"]) for p in pos.values())

    def close(key, px, why, t):
        nonlocal cash, hold
        p = pos.pop(key)
        i, k = key
        if why == "liq":
            fill, gross, fee = px, -p["margin"], 0.0
        else:
            fill = px * (1 - p["side"] * slip)
            gross, fee = p["side"] * p["qty"] * (fill - p["entry"]), rls.TAKER * p["qty"] * fill
        cash += gross - fee
        pnl = gross - fee - p["fee"] + p["fund"]
        if rls.edge_after(why, p["be"]):
            blk[key] = p["side"]
        streak[k] = streak[k] + 1 if pnl <= 0 else 0
        hold = hold or streak[k] >= rls.STREAK_HOLD
        trades.append({"sleeve": "coin", "tranche": k, "inst": i, "side": p["side"], "entry_ts": p["t0"], "exit_ts": t, "entry": p["entry"],
                       "exit": fill, "be": p["be"], "why": why, "pnl": pnl, "pnl_pct": pnl / p["eq0"]})

    for t in clock:
        o = {i: B[i].at[t, "o"] for i in B}
        for key in list(pos):                                # 1) 시가 갭: 손절·청산가 너머로 열리면 시가 체결
            p = pos[key]
            if (o[key[0]] - p["stop"]) * p["side"] <= 0:
                close(key, o[key[0]], "liq" if (o[key[0]] - p["liq"]) * p["side"] <= 0 else "sl", t)
        if t.hour == 0:                                      # 2) UTC 날 경계: 자산 기록 → 일손실 판정 → 새 일 기준 · 일별 고점
            eq = mark(o)
            eqs[t] = eq
            if day_ref and eq <= day_ref * (1 - rls.DAILY_LOSS):
                dl_day = t.date()
                for key in list(pos):
                    close(key, o[key[0]], "daily_loss", t)
                eq = cash
            day_ref, hwm = eq, max(hwm or eq, eq)
        if t.hour % 8 == 0:                                  # 3) 펀딩 (UTC 00·08·16)
            for p in pos.values():
                f = rls.funding_pnl(p["side"], funding_8h, p["qty"] * o[p["i"]])
                cash += f
                p["fund"] += f
        for k, off in enumerate(offs):                       # 4) 몫 작업: 방금 끝난 off 경계 일봉으로 판단, 이 봉 시가에 체결
            if t.hour != off:
                continue
            for i in H:
                x = X[(i, off)]
                sd = t - pd.Timedelta(days=1)
                if sd not in x.index or np.isnan(x.at[sd, "atr"]):
                    continue
                sig, key = x.loc[sd], (i, k)
                p = pos.get(key)
                if p:
                    why = "regime" if sig.conf != p["side"] else "drop1" if rls.drop1(p["side"], sig.close, sig.prev, sig.atr) else None
                    if why:
                        close(key, o[i], why, t)
                if blk.get(key) and sig.conf != blk[key]:
                    del blk[key]
                p = pos.get(key)
                if not p and sig.conf and sig.conf != blk.get(key) and rls.vol_ok(sig.atr, sig.close) \
                        and not halted and not hold and dl_day != t.date():
                    m_amt = cash * margin / N
                    if sum(q["margin"] for q in pos.values()) + m_amt > rls.MAX_TOTAL * day_ref + 1e-12:
                        cnt["cap_block"] += 1
                        continue
                    side, fill = int(sig.conf), o[i] * (1 + int(sig.conf) * slip)
                    if capital:
                        ct, lot, mn = INST_META[i]
                        sz = okx.floor_to(m_amt * lever_ / (ct * o[i] * (1 + size_buf)), lot)
                        if sz < mn:
                            cnt["below_min"] += 1
                            continue
                        qty = sz * ct
                    else:
                        qty = m_amt * lever_ / fill
                    dist = rls.stop_dist(sig.atr, sig.close, lever_, mmr)
                    fee = rls.TAKER * qty * fill
                    eq0 = mark(o)
                    cash -= fee
                    pos[key] = {"i": i, "side": side, "entry": fill, "qty": qty, "margin": qty * fill / lever_,
                                "stop": rls.stop_px(side, fill, dist), "liq": fill * (1 - side * rls.liq_dist(lever_, mmr)),
                                "atr_e": sig.atr, "be": False, "t0": t, "eq0": eq0, "fee": fee, "fund": 0.0}
                elif p and not p["be"] and rls.be_hit(p["side"], sig.close, p["entry"], p["atr_e"]):
                    p["stop"], p["be"] = rls.tighten(p["side"], p["stop"], rls.be_px(p["side"], p["entry"])), True
        for key in list(pos):                                # 5) 장중 손절 (1H 고저)
            p, b = pos[key], B[key[0]].loc[t]
            if (b.l <= p["stop"]) if p["side"] > 0 else (b.h >= p["stop"]):
                close(key, p["stop"], "sl", t)
        c = {i: B[i].at[t, "c"] for i in B}                  # 6) 킬: 매시 종가 자산 vs 일별 고점
        if hwm and not halted and 1 - mark(c) / hwm >= rls.KILL_DD:
            halted = True
            for key in list(pos):
                close(key, c[key[0]], "halt", t)
    cnt["open"] = [{"tranche": k, "inst": i, "side": p["side"], "entry_ts": p["t0"], "entry": p["entry"], "be": p["be"],
                    "stop": p["stop"]} for (i, k), p in pos.items()]
    e = pd.Series(eqs)
    return e / e.iloc[0], pd.DataFrame(trades), cnt


if __name__ == "__main__" and "--coin" in sys.argv:
    args = [a for a in sys.argv[1:] if a != "--coin"]
    coin_validation(*(args + [START, END])[:2])
    sys.exit(0)

if __name__ == "__main__":
    start = sys.argv[1] if len(sys.argv) > 1 else START
    end = sys.argv[2] if len(sys.argv) > 2 else END
    if not stocks.MEMBERS_CSV.exists():
        sys.exit(f"{stocks.MEMBERS_CSV.name} 가 없습니다 (date,ticker PIT 구성종목 스냅샷)")
    CACHE.mkdir(exist_ok=True)
    t0 = time.time()
    members_at, snaps = stocks.members_loader()
    since = pd.Timestamp(start) - WARMUP
    C = {i: coin_daily(i, since) for i in rls.COINS}
    S = stock_daily(set().union(*snaps.values()), since, end)
    L = listed()
    print(f"코인 {', '.join(f'{i[:3]} {len(d)}' for i, d in C.items())} · 주식 {len(S)}종목 · "
          f"OKX 주식 무기한 {len(L)} ({time.time() - t0:.0f}s)")
    runs = [("명세 (한 계좌)", {}), ("펀딩 2배 + 슬리피지 10bp", {"funding_8h": 2 * rls.FUNDING_8H, "slip": 0.001}),
            ("주식만 (M5+RLS 롱)", {"sleeves": ("stock",)}), ("코인 롱·숏만 (RLS)", {"sleeves": ("coin",)})]
    for name, kw in runs:
        eq, tr, days = rls.simulate(C, S, members_at, L, start, end, **kw)
        print(line(name, stats(eq, tr)) + f"\n{'':4s}일손실 발동 {int(days.daily_loss.sum())}회 · 동시 한도로 막힌 진입 "
              f"{int(days.blocked.sum())}건 · 평균 증거금 사용 {days.margin_pct.mean():.1%}")
    H = {i: coin_hourly(i, since) for i in rls.COINS}
    eq, tr = rls.simulate_tranches(H, rls.TRANCHES, start, end)
    print(line(f"코인 시각 분할 {len(rls.TRANCHES)}몫 (운용)", stats(eq, tr)))
    btc = C["BTC-USDT-SWAP"].close.loc[start:end]
    print(line("BTC 1x 보유", stats(btc / btc.iloc[0])))
