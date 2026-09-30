"""OKX BTC-USDT-SWAP 횡보 박스 복귀 — 백테스트 (설계·판정 기준은 DESIGN.md, 결과 보기 전에 커밋할 것).

데이터: data_cache/okx_1m.pkl (BTC-USDT-SWAP 체결가 1분봉, 2021-03~) — 첫 결측 앞까지만 쓴다.
        펀딩: OKX 실측(okx_funding.pkl, 2026-06~ 뿐) + 그 앞은 바이낸스 BTCUSDT 무기한 실측(대용, 8h 정산 같음).
체결 모델 (모두 보수적으로):
  · 진입   = 신호 5분봉 마감 직후 1분봉 시가 + 슬리피지, 테이커.
  · 손절   = 스톱 시장가. 그 분봉 저가(숏은 고가)가 닿으면 체결, 갭이면 시가. + 슬리피지, 테이커.
  · 익절   = 지정가(메이커). 가격이 목표를 '넘어야' 체결 (닿기만 한 것은 미체결 — 지정가 체결 착시 방지). 진입 분봉에선 안 본다.
  · 한 분봉에서 손절·익절 둘 다 가능하면 손절.
  · 시간 청산·조건 불충족 = 판정한 분봉 마감 다음 1분봉 시가 시장가.
  · 펀딩 = 정산 시각에 들고 있던 비율만큼, 실측률 부호대로 (롱이 +률이면 낸다).
사용: python research/okx_range_reversion/backtest.py          전체 → backtest_result.txt
      python research/okx_range_reversion/backtest.py count    신호·진입 건수만 (성과 안 봄 — 데이터 충분성 확인용)
"""
import pathlib
import sys

import numpy as np
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import rules as R  # noqa: E402

DC = HERE.parents[1] / "data_cache"
IS_END = pd.Timestamp("2024-01-01", tz="UTC")
MIN = 60_000_000_000


def ns(idx):
    """pandas 3 는 인덱스 해상도를 추론한다 (s·ms·us·ns) — 정수 시각은 항상 ns 로."""
    return idx.as_unit("ns").asi8


def load():
    m = pd.read_pickle(DC / "okx_1m.pkl").astype(float).sort_index()
    m = m[~m.index.duplicated()]
    gap = m.index.to_series().diff() > pd.Timedelta("1min")
    if gap.any():
        m = m[m.index < gap[gap].index[0]]
    assert (np.diff(ns(m.index)) == MIN).all(), "1분봉이 연속이 아니다"
    return m


def funding():
    okx = pd.read_pickle(DC / "okx_funding.pkl").sort_index()
    bn = pd.read_pickle(DC / "binance_funding.pkl").sort_index()
    return pd.concat([bn[bn.index < okx.index[0]], okx])


def arrays(m, f, p):
    t0 = ns(m.index)[0]
    fi = (ns(f.index) - t0) // MIN
    k = (fi >= 0) & (fi < len(m))
    return dict(o=m.open.values, h=m.high.values, l=m.low.values, c=m.close.values,
                ema=m.close.ewm(span=p.c_ema, adjust=False).mean().values, t0=t0, idx=m.index,
                fi=fi[k], fr=f.values[k], fall_i=fi, fall_r=f.values)


def cond(M, b, s, mid):
    """C 의 잔여분 유지 조건 — 마감된 1분봉 b 에서 중앙 유지 & EMA 가 포지션 방향."""
    c, ema = M["c"], M["ema"]
    return bool(c[b] >= mid and ema[b] > ema[b - 1]) if s > 0 else bool(c[b] <= mid and ema[b] < ema[b - 1])


def sim(M, s, j0, e, stop, mid, H, L, A, var, p, kill=None):
    """분봉 j0 시가에 가격 e 로 진입한 한 거래 → dict(pnl=BTC 1개당 USDT, legs, why, x=청산 시각(분 인덱스), mfe)."""
    o, h, l, c = M["o"], M["h"], M["l"], M["c"]
    n = len(o)
    risk0 = abs(e - stop)
    stp, why_stop = stop, "손절"
    if kill is not None and (kill > stop if s > 0 else kill < stop):
        stp, why_stop = kill, "일손실"
    T = H - p.c_target_atr * A if s > 0 else L + p.c_target_atr * A
    legs, rem, phase, fav, best, tpj = [], 1.0, 0, 0.0, mid, -1
    mkt = lambda j: o[j] * (1 - s * p.slip)  # noqa: E731  — 다음 분봉 시가 시장가
    j = j0
    while True:
        if j >= n - 1:
            legs.append((rem, c[n - 1] * (1 - s * p.slip), p.taker, n - 1, "데이터끝")); break
        if (l[j] <= stp) if s > 0 else (h[j] >= stp):                  # 1. 손절이 먼저 (봉 안 순서를 모른다)
            px = (min(stp, o[j]) if s > 0 else max(stp, o[j])) * (1 - s * p.slip)
            legs.append((rem, px, p.taker, j + 0.5, why_stop if phase == 0 else "추적손절")); break
        if phase == 0 and j > j0 and ((h[j] > mid) if s > 0 else (l[j] < mid)):   # 2. 중앙 — 넘어야 체결
            if var != "C":
                legs.append((rem, mid, p.maker, j + 0.5, "중앙익절")); break
            legs.append((p.c_frac, mid, p.maker, j + 0.5, "중앙익절")); rem -= p.c_frac; phase, tpj = 1, j
            if p.c_check == "prev" and not cond(M, j - 1, s, mid):
                legs.append((rem, mid * (1 - s * p.slip), p.taker, j + 0.5, "조건불충족")); break
        elif phase == 1 and j > tpj and ((h[j] > T) if s > 0 else (l[j] < T)):
            legs.append((rem, T, p.maker, j + 0.5, "최종목표")); break
        fav = max(fav, (h[j] - e) if s > 0 else (e - l[j]))              # 3. 분봉 j 마감 뒤 판정 → j+1 시가에 행동
        el = j - j0 + 1
        if phase == 1:
            if j == tpj and p.c_check == "close" and not cond(M, j, s, mid):
                legs.append((rem, mkt(j + 1), p.taker, j + 1, "조건불충족")); break
            best = max(best, c[j]) if s > 0 else min(best, c[j])
            trail = best - s * p.c_trail_atr * A
            stp = max(stp, trail) if s > 0 else min(stp, trail)          # 단조 강화만
            if el >= p.c_max:
                legs.append((rem, mkt(j + 1), p.taker, j + 1, "60분")); break
        else:
            if var in ("B", "C") and el == p.b_check and fav < p.b_mfe * risk0:
                legs.append((rem, mkt(j + 1), p.taker, j + 1, "조기종료")); break
            if el >= p.tp_wait:
                legs.append((rem, mkt(j + 1), p.taker, j + 1, "30분")); break
        j += 1
    x = legs[-1][3]
    pnl = sum(f * (s * (px - e) - px * fee) for f, px, fee, _, _ in legs) - e * p.taker
    k0, k1 = np.searchsorted(M["fi"], j0, "right"), np.searchsorted(M["fi"], x, "right")
    for i in range(k0, k1):                                              # 정산 시각에 남아 있던 비율만큼
        pnl -= s * M["fr"][i] * e * sum(f for f, _, _, t, _ in legs if t >= M["fi"][i])
    return dict(pnl=pnl, legs=legs, why=legs[-1][4], x=x, mfe=fav / risk0 if risk0 else 0.0)


def fund_est(M, j0, p):
    """판단 시점에 아는 펀딩 예상치 — 최대 보유 안에 정산이 있으면 직전 정산률, 없으면 0."""
    k = np.searchsorted(M["fall_i"], j0, "right")
    if k < len(M["fall_i"]) and M["fall_i"][k] <= j0 + p.c_max and k > 0:
        return M["fall_r"][k - 1]
    return 0.0


def j_of(M, ts):
    return int((ts.value - M["t0"]) // MIN)


def entries(sig, M, p):
    """고정 진입 목록 — 비용 반영 계획을 통과한 신호, 가장 긴 변형(60분) 동안은 다음 신호를 안 받는다 → A·B·C 가 같은 진입을 쓴다."""
    out, busy, n = [], -1, len(M["o"])
    for r in sig.itertuples():
        j0 = j_of(M, r.t)
        if j0 <= busy or j0 >= n - 1:
            continue
        pl = R.plan(r.side, r.close, r.mid, r.stop, p, fund_est(M, j0, p))
        if not pl["ok"]:
            continue
        out.append(dict(t=r.t, s=r.side, j0=j0, risk=pl["risk"],
                        stop=r.stop, mid=r.mid, H=r.H, L=r.L, A=r.atr))
        busy = j0 + max(p.c_max, p.tp_wait)
    return out


def fixed(ents, M, var, p):
    """p = 실현 비용. 진입 여부·R 의 분모(계획 손절손실)는 entries 에서 가정 비용으로 이미 정해졌다."""
    rows = []
    for x in ents:
        e = M["o"][x["j0"]] * (1 + x["s"] * p.slip)
        r = sim(M, x["s"], x["j0"], e, x["stop"], x["mid"], x["H"], x["L"], x["A"], var, p)
        rows.append(dict(t=x["t"], s=x["s"], R=r["pnl"] / x["risk"], ret=r["pnl"] / e, why=r["why"], mfe=r["mfe"]))
    return pd.DataFrame(rows, columns=["t", "s", "R", "ret", "why", "mfe"])


def portfolio(sig, M, var, p, eq0=10_000.0, plan_p=None):
    """한 번에 한 포지션 · 위험 0.2% · 명목 1배 · 계약 반올림 · UTC 하루 손실 1% 면 그날 신규 중단 + 보유분 정리.
    plan_p = 판단·수량에 쓰는 가정 비용 (기본 p), p = 실현 비용 (스트레스)."""
    plan_p = plan_p or p
    eq, busy, day, day0, blocked, rows = eq0, -1, None, eq0, set(), []
    n = len(M["o"])
    for r in sig.itertuples():
        j0 = j_of(M, r.t)
        if j0 <= busy or j0 >= n - 1:
            continue
        d = r.t.normalize()
        if d != day:
            day, day0 = d, eq
        if d in blocked:
            continue
        pl = R.plan(r.side, r.close, r.mid, r.stop, plan_p, fund_est(M, j0, p))
        ct = R.contracts(eq, pl["e"], pl["risk"], plan_p) if pl["ok"] else 0.0
        if not ct:
            continue
        q, e = ct * p.ct_val, M["o"][j0] * (1 + r.side * p.slip)
        budget = eq - day0 * (1 - p.day_loss)
        # ponytail: 일손실 정리선은 '남은 여유 / 수량 − 왕복 수수료' 근사. 분봉 평가 곡선이 필요해지면 sim 에 평가액 추적 추가
        kill = e - r.side * (budget / q - 2 * e * p.taker)
        res = sim(M, r.side, j0, e, r.stop, r.mid, r.H, r.L, r.atr, var, p, kill)
        eq += res["pnl"] * q
        rows.append(dict(t=r.t, s=r.side, ct=ct, pnl=res["pnl"] * q, equity=eq, why=res["why"]))
        if eq <= day0 * (1 - p.day_loss) + 1e-9:
            blocked.add(d)
        busy = int(np.ceil(res["x"]))
    return pd.DataFrame(rows, columns=["t", "s", "ct", "pnl", "equity", "why"]), len(blocked)


def st(df):
    if df.empty:
        return dict(n=0, R=np.nan, t=np.nan, win=np.nan, pf=np.nan, bp=np.nan)
    r = df.R
    neg = -r[r < 0].sum()
    return dict(n=len(r), R=r.mean(), t=r.mean() / (r.std(ddof=1) / np.sqrt(len(r))) if len(r) > 1 else np.nan,
                win=(r > 0).mean(), pf=r[r > 0].sum() / neg if neg else np.inf, bp=df.ret.mean() * 1e4)


def fmt(s):
    return (f"{s['n']:>5}건 평균 {s['R']:+.3f}R (t {s['t']:+.2f}) 승률 {s['win']:.0%} PF {s['pf']:.2f} "
            f"명목대비 {s['bp']:+.1f}bp") if s["n"] else "    0건"


def pstat(df, eq0, days):
    if df.empty:
        return "    0건"
    eq = np.r_[eq0, df.equity.values]
    mdd = (eq / np.maximum.accumulate(eq) - 1).min()
    cagr = (eq[-1] / eq0) ** (365.25 / days) - 1
    return f"{len(df):>5}건 최종 {eq[-1] / eq0 - 1:+.2%} 연 {cagr:+.2%} MDD {mdd:.2%} 평균 계약 {df.ct.mean():.2f}"


def walk_forward(res, start="2022-01-01", train_m=12, step_m=6):
    """고를 수 있는 것은 청산 변형 하나뿐 → 직전 12개월 평균 R 최고 변형을 다음 6개월에 쓴다."""
    out, picks = [], []
    a = pd.Timestamp(start, tz="UTC")
    end = max(d.t.max() for d in res.values())
    while a < end:
        b = a + pd.DateOffset(months=step_m)
        tr = {k: d[(d.t >= a - pd.DateOffset(months=train_m)) & (d.t < a)].R.mean() for k, d in res.items()}
        k = max(tr, key=lambda v: -np.inf if np.isnan(tr[v]) else tr[v])
        picks.append(f"{a:%y.%m}:{k}")
        out.append(res[k][(res[k].t >= a) & (res[k].t < b)])
        a = b
    return pd.concat(out), picks


VARS = {"A": ("A", {}), "B": ("B", {}), "C": ("C", {"c_check": "prev"}), "C'": ("C", {"c_check": "close"})}


def var_p(p, k):
    return R.P(**{**p.__dict__, **VARS[k][1]})


def main(count_only=False):
    base = R.P()
    m = load()
    f = funding()
    c5, c15 = R.resample(m, "5min"), R.resample(m, "15min")
    lines = []
    out = lambda *a: (print(*a, flush=True), lines.append(" ".join(map(str, a))))  # noqa: E731
    out(__doc__.split("\n")[0])
    out(f"데이터 {m.index[0]:%Y-%m-%d} ~ {m.index[-1]:%Y-%m-%d %H:%M} · 1분봉 {len(m):,} · 펀딩 OKX 실측 {pd.read_pickle(DC / 'okx_funding.pkl').index[0]:%Y-%m-%d}~, 그 앞 바이낸스 대용")
    out(f"설정 {base}")
    sig = R.signals(c5, c15, base)
    M = arrays(m, f, base)
    days = lambda a, b: (min(b, m.index[-1]) - max(a, m.index[0])).total_seconds() / 86400  # noqa: E731
    per = [("IS 21.03-23.12", m.index[0], IS_END), ("OOS 24.01-", IS_END, m.index[-1] + pd.Timedelta("1min"))]
    cut = lambda d, a, b: d[(d.t >= a) & (d.t < b)]  # noqa: E731

    out("\n[0] 신호·진입 건수 (성과 아님). 판단은 항상 가정 비용(×1) — ×1.5·×2 행은 '그 비용을 가정했다면' 참고용")
    for k in (1, 1.5, 2):
        ents = entries(sig, M, R.stressed(base, k))
        e = pd.DataFrame(ents)
        yr = " ".join(f"{y % 100}:{v}" for y, v in e.t.dt.year.value_counts().sort_index().items()) if len(e) else ""
        out(f"  비용×{k}: 신호 {len(sig):,} (롱 {(sig.side > 0).sum():,}/숏 {(sig.side < 0).sum():,}) → 비용 통과·비중첩 진입 "
            f"{len(e):,} (롱 {(e.s > 0).sum() if len(e) else 0}/숏 {(e.s < 0).sum() if len(e) else 0}) | {yr}")
    if count_only:
        return

    res_all = {}
    ents = entries(sig, M, base)                                         # 진입 판단은 가정 비용 — 스트레스는 실현 비용에만
    out("\n[1] 고정 진입 비교 — 같은 진입 목록에 청산 변형만 바꿔 적용 (R = 계획 손절손실 단위, 비용×k = 실현 수수료·슬리피지)")
    for k in (1, 1.5, 2):
        pk = R.stressed(base, k)
        out(f" 비용×{k}")
        for v in VARS:
            d = fixed(ents, M, VARS[v][0], var_p(pk, v))
            res_all[(k, v)] = d
            for name, a, b in per:
                x = cut(d, a, b)
                out(f"  {v:2s} {name:14s} 전체 {fmt(st(x))}")
                out(f"  {'':2s} {'':14s}   롱 {fmt(st(x[x.s > 0]))}")
                out(f"  {'':2s} {'':14s}   숏 {fmt(st(x[x.s < 0]))}")
            out(f"  {'':2s} 청산 사유 " + " · ".join(f"{w} {c}" for w, c in d.why.value_counts().items()))

    out("\n[2] 전체 포트폴리오 — 한 포지션·위험 0.2%·명목 1배·계약 반올림·일손실 1% 정리, 시작 10,000 USDT")
    for k in (1, 1.5, 2):
        pk = R.stressed(base, k)
        for v in VARS:
            for name, a, b in per:
                d, nb = portfolio(sig[(sig.t >= a) & (sig.t < b)], M, VARS[v][0], var_p(pk, v), plan_p=var_p(base, v))
                out(f"  비용×{k} {v:2s} {name:14s} {pstat(d, 10_000.0, days(a, b))} 일손실 정지 {nb}일")

    out("\n[3] 워크포워드 — 직전 12개월 평균 R 최고 청산 변형을 다음 6개월에 (고정 진입, 비용별)")
    for k in (1, 1.5, 2):
        wf, picks = walk_forward({v: res_all[(k, v)] for v in VARS})
        out(f"  비용×{k} {fmt(st(wf))} | " + " ".join(picks))

    out("\n[4] 연도별 평균 R (고정 진입, 비용×1.5)")
    for v in VARS:
        d = res_all[(1.5, v)]
        out(f"  {v:2s} " + " · ".join(f"{y}: {g.R.mean():+.3f}R ({len(g)})" for y, g in d.groupby(d.t.dt.year)))

    out("\n[5] 사전등록 판정 (DESIGN.md §6)")
    for v in VARS:
        o15, o2 = cut(res_all[(1.5, v)], IS_END, m.index[-1] + pd.Timedelta("1min")), cut(res_all[(2, v)], IS_END, m.index[-1] + pd.Timedelta("1min"))
        i15 = cut(res_all[(1.5, v)], m.index[0], IS_END)
        s15 = st(o15)
        pf, _ = portfolio(sig[sig.t >= IS_END], M, VARS[v][0], var_p(R.stressed(base, 1.5), v), plan_p=var_p(base, v))
        eq = np.r_[10_000.0, pf.equity.values]
        mdd = (eq / np.maximum.accumulate(eq) - 1).min()
        chk = [("①n≥100", s15["n"] >= 100), ("②t≥2.5", s15["n"] > 1 and s15["R"] > 0 and s15["t"] >= 2.5),
               ("③롱·숏>0", st(o15[o15.s > 0])["R"] > 0 and st(o15[o15.s < 0])["R"] > 0),
               ("④×2>0", st(o2)["R"] > 0), ("⑤포트 MDD≥−5%·수익>0", mdd >= -0.05 and eq[-1] > eq[0]),
               ("⑥IS>0", st(i15)["R"] > 0)]
        out(f"  {v:2s} " + " · ".join(f"{a} {'통과' if b else '불합격'}" for a, b in chk) +
            f" → {'모의운용 후보' if all(b for _, b in chk) else '기각'}")
    (HERE / "backtest_result.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(count_only="count" in sys.argv)
