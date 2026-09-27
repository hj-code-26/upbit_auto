"""사후 감사 분석 (post-hoc audit analysis) — 기준선 대비 paired 검정 + 다중검정 보정.

★ 이 파일의 모든 검정은 **사후 감사 분석**이다. 사전등록(deb82b8)의 관문 A~F 는 p값을 정의하지 않았다.

사전 고정 (결과를 보기 전에 이 docstring 에 적고 실행한다)
  주 통계량   ΔSh = Sh(후보) − Sh(기준선), Sh = CAGR/연율변동성 (backtest_quant.metrics 정의 = 관문 D·E·F 의 지표)
  주 비교     OKX 2021-03~2026-09 전체 · 고정 3배 · 재확보 체결 · BUF=2 vs BUF=0
  재표집      circular block bootstrap(Politis–Romano 1992), 일별 수익률, **두 전략에 같은 인덱스**
              주 블록 20일(원 backtest_lev.boot 와 같음) · 민감도 5/10/40/60일 (좋은 값을 고르지 않는다)
  반복        B = 20,000. 경계(|p − α/N| < 3·MC-SE) 이면 B = 200,000 으로 한 번 재실행하고 그 값을 보고한다.
              결과가 좋을 때 멈추는 규칙은 없다.
  p값         귀무 중심화 단측: p = (1 + #{Δ* − Δ̂ ≥ Δ̂}) / (B+1)   (H0: Δ ≤ 0, H1: Δ > 0)
              = 부트스트랩 분포를 Δ̂ 만큼 옮겨 귀무 아래 분포로 쓰는 'basic' 방식. 피벗 근사이며 정확 검정이 아니다.
  CI          95% percentile 과 basic 을 둘 다 적는다.
  보조        Δ평균 로그수익(연율) — 학생화 block bootstrap p (블록 평균 분산으로 SE) · Δ표준 Sharpe(평균/표준편차)
  다중검정    주 방법 = 학생화 max-통계량 (Romano–Wolf 단일단계, White RC 형 보수적 중심화).
              같은 OKX 일별 인덱스 위에 9/23 탐색 사슬의 후보 전부(각자 자기 기준선 대비 Δ)를 올리고
              같은 재표집 인덱스로 max_k (Δ*_k − Δ̂_k)/σ̂_k 분포를 만든다. σ̂_k = Δ*_k 의 부트스트랩 표준편차.
              Bitstamp(기간 다름)는 이 행렬에 넣지 않는다 → 별도 Holm.
  한계        일별 수익률 재표집은 미관측 청산 사건을 만들지 못한다. 블록 경계에서 보유 경로가 끊긴다.

python stats_paired.py → out/paired.csv · out/maxstat.csv · out/bitstamp.csv · out/lev_interaction.csv · out/bootC_blocks.csv
"""
import sys
import time

import numpy as np
import pandas as pd

import common as C

sys.stdout.reconfigure(encoding="utf-8")
SEED, B0, B_BIG, MAIN_BLOCK = 20260923, 20_000, 200_000, 20
BLOCKS = (5, 10, 20, 40, 60)
ALPHA = 0.05


def cbb(n, b, reps, rng):
    nb = -(-n // b)
    st = rng.integers(0, n, (reps, nb))
    return ((st[:, :, None] + np.arange(b)) % n).reshape(reps, nb * b)[:, :n]


def boot_stats(M, stat, b, reps, seed=SEED, chunk=1000):
    """M: (S, n) 수익률 행렬. → (S, reps) 재표집 통계량 (모든 행에 같은 인덱스)."""
    rng = np.random.default_rng(seed)
    out = []
    for s in range(0, reps, chunk):
        idx = cbb(M.shape[1], b, min(chunk, reps - s), rng)
        out.append(stat(M[:, idx]))
    return np.concatenate(out, axis=1)


def pval(dstar, dhat):
    return (1 + np.sum(dstar - dhat >= dhat)) / (len(dstar) + 1)


def mcse(p, reps):
    return float(np.sqrt(p * (1 - p) / reps))


def paired(rb, rc, b, reps, seed=SEED):
    """기준선·후보 일별 수익률 → 주/보조 통계 dict."""
    Mx = np.vstack([rb, rc])
    res = {}
    for nm, st in (("dSh_cv", C.sharpe_cv), ("dSh_std", C.sharpe_std), ("dMlog", C.mlog)):
        bs = boot_stats(Mx, st, b, reps, seed)
        d = bs[1] - bs[0]
        dh = float(st(Mx[1]) - st(Mx[0]))
        p = pval(d, dh)
        lo, hi = np.percentile(d, [2.5, 97.5])
        res[nm] = {"est": dh, "p_centered": p, "mcse": mcse(p, reps), "ci_pct": (lo, hi), "ci_basic": (2 * dh - hi, 2 * dh - lo),
                   "sd": float(d.std())}
    # 학생화: Δ 로그수익 평균, SE = 블록 평균들의 표준편차/√k
    dl = np.log1p(rc) - np.log1p(rb)
    n, k = len(dl), len(dl) // b
    se0 = dl[: k * b].reshape(k, b).mean(1).std(ddof=1) / np.sqrt(k)
    t0 = dl.mean() / se0
    rng = np.random.default_rng(seed + 1)
    ts = []
    for s in range(0, reps, 2000):
        m = min(2000, reps - s)
        nb = k
        st = rng.integers(0, n, (m, nb))
        x = dl[(st[:, :, None] + np.arange(b)) % n]          # (m, k, b)
        bm = x.mean(2)
        ts.append((bm.mean(1) - dl.mean()) / (bm.std(1, ddof=1) / np.sqrt(k)))
    ts = np.concatenate(ts)
    p = (1 + np.sum(ts >= t0)) / (len(ts) + 1)
    res["dMlog_student"] = {"est": float(dl.mean() * C.YEAR), "t": float(t0), "p_centered": p, "mcse": mcse(p, reps)}
    return res


def flat(tag, r, extra):
    rows = []
    for nm, v in r.items():
        row = {"comparison": tag, **extra, "stat": nm, "est": v["est"], "p": v["p_centered"], "mcse": v["mcse"]}
        if "ci_pct" in v:
            row.update(ci_pct_lo=v["ci_pct"][0], ci_pct_hi=v["ci_pct"][1], ci_basic_lo=v["ci_basic"][0],
                       ci_basic_hi=v["ci_basic"][1], boot_sd=v["sd"])
        if "t" in v:
            row["t"] = v["t"]
        rows.append(row)
    return rows


def main():
    t0 = time.time()
    base, cand = C.run_okx(0, 3), C.run_okx(2, 3)
    rows = []
    # ── 1. 주 비교 + 블록 민감도 + 구간 ──
    for lo, hi, seg in ((C.START, None, "full"), (C.START, C.SPLIT, "explore"), (C.SPLIT, None, "valid")):
        rb, rc = C.daily(base[0], lo, hi).values, C.daily(cand[0], lo, hi).values
        for b in (BLOCKS if seg == "full" else (MAIN_BLOCK,)):
            r = paired(rb, rc, b, B0)
            rows += flat("OKX 3x BUF2 vs BUF0", r, {"segment": seg, "block": b, "B": B0, "n_days": len(rb)})
            print(f"{seg:8s} b={b:2d}  ΔSh {r['dSh_cv']['est']:+.4f} p={r['dSh_cv']['p_centered']:.4f} "
                  f"CI[{r['dSh_cv']['ci_pct'][0]:+.3f},{r['dSh_cv']['ci_pct'][1]:+.3f}]  "
                  f"Δmlog p={r['dMlog']['p_centered']:.4f} stud p={r['dMlog_student']['p_centered']:.4f}", flush=True)
    P = pd.DataFrame(rows)
    main_p = P.query("segment=='full' and block==@MAIN_BLOCK and stat=='dSh_cv'").p.iloc[0]

    # ── 2. 다중검정: 9/23 탐색 사슬 max-통계량 (OKX 공통 인덱스) ──
    sys.path.insert(0, str(C.ROOT))
    import backtest_longtrend as LT
    h4f, bull, k, h4, fl = C.okx()
    S = LT.long_signals(h4.index)
    z = np.zeros(len(h4), bool)
    e0, x0 = C.X.signals(h4f, bull, 0)

    def gated(g, lev=3):
        return C.E.run(h4, e0[k], x0[k], z, z, lambda i, c: lev, allow=("long",), fills=fl, gate=(g, None))

    def hyst(buf, lev=3, conf=None):
        conf = C.M.CONF if conf is None else conf
        keep = C.M.CONF
        C.M.CONF = conf
        try:
            hf, he, hx = LT.hyst_signals(conf, conf - buf)
        finally:
            C.M.CONF = keep
        kk = hf.index >= pd.Timestamp(C.START, tz="UTC")
        return C.E.run(hf[kk], he[kk], hx[kk], z, z, lambda i, c: lev, allow=("long",), fills=fl)

    def conf_base(conf, lev=3):
        en, ex = C.X.signals(h4f, bull, 0, conf=conf)
        return C.E.run(h4, en[k], ex[k], z, z, lambda i, c: lev, allow=("long",), fills=fl)

    rg_b = (C.M.align_daily(bull, h4f.index).ffill().fillna(0).values >= C.M.CONF)
    rg_h = LT.hysteresis(h4f.index, C.M.CONF, C.M.CONF - 2)
    brk = (h4f.close > h4f.high.rolling(C.M.H4_N).max().shift(1)).values
    bdn = (h4f.close < h4f.low.rolling(C.M.H4_M).min().shift(1)).values

    def decomp(ri, rx):
        return C.E.run(h4, (brk & ri)[k], (bdn | ~rx)[k], z, z, lambda i, c: 3, allow=("long",), fills=fl)

    B3, B1, B5 = C.run_okx(0, 3), C.run_okx(0, 1), C.run_okx(0, 5)
    fam = [  # (이름, 출처, 후보, 기준선)
        ("L1 mom252-21 gate", "longtrend prereg", gated(S["mom252-21"]), B3),
        ("L2 sma200 gate", "longtrend prereg", gated(S["sma200"]), B3),
        ("L3 L1&L2 gate", "longtrend prereg", gated(S["mom252-21"] & S["sma200"]), B3),
        ("H1 hyst 6/4 (both)", "longtrend prereg", hyst(2), B3),
        ("nb mom126-21", "longtrend prereg nb", gated(S["mom126-21"]), B3),
        ("nb mom378-21", "longtrend prereg nb", gated(S["mom378-21"]), B3),
        ("nb sma100", "longtrend prereg nb", gated(S["sma100"]), B3),
        ("nb sma300", "longtrend prereg nb", gated(S["sma300"]), B3),
        ("nb hyst 6/5", "longtrend prereg nb", hyst(1), B3),
        ("nb hyst 6/3", "longtrend prereg nb", hyst(3), B3),
        ("post hyst6/4 @1x", "longtrend post-hoc", hyst(2, 1), B1),
        ("post hyst6/4 @5x", "longtrend post-hoc", hyst(2, 5), B5),
        ("post CONF5 hyst5/3", "longtrend post-hoc", hyst(2, 3, 5), conf_base(5)),
        ("post CONF7 hyst7/5", "longtrend post-hoc", hyst(2, 3, 7), conf_base(7)),
        ("post CONF8 hyst8/6", "longtrend post-hoc", hyst(2, 3, 8), conf_base(8)),
        ("decomp entry-only", "decomp (unregistered)", decomp(rg_h, rg_b), B3),
        ("decomp exit-only", "decomp (unregistered)", decomp(rg_b, rg_h), B3),
        ("EXIT BUF1", "exitconf prereg", C.run_okx(1, 3), B3),
        ("EXIT BUF2 (main)", "exitconf prereg", C.run_okx(2, 3), B3),
        ("EXIT BUF3", "exitconf prereg", C.run_okx(3, 3), B3),
    ]
    # 앞의 두 줄(mom252·sma200 이웃) 은 L1·L2 와 같은 곡선 · CONF6 축은 H1 과 같은 곡선 · 레버리지 3x 축도 H1 → 중복 제외
    same = np.allclose(fam[16][2][0].values, fam[18][2][0].values)
    print(f"decomp exit-only == EXIT BUF2 : {same}", flush=True)
    series, meta = [], []
    for nm, src, c, b in fam:
        rc, rb = C.daily(c[0]).values, C.daily(b[0]).values
        series += [rb, rc]
        meta.append((nm, src, float(C.sharpe_cv(rc) - C.sharpe_cv(rb))))
    Mx = np.vstack(series)
    D = {}
    for bl in (MAIN_BLOCK, 10, 40):
        bs = boot_stats(Mx, C.sharpe_cv, bl, B0)
        D[bl] = bs[1::2] - bs[0::2]                     # (K, B)
    rows2 = []
    for bl, d in D.items():
        dh = np.array([m[2] for m in meta])[:, None]
        sd = d.std(1, keepdims=True)
        sd = np.where(sd > 0, sd, np.inf)               # Δ 가 항상 0 인 후보(동일 곡선)는 max 에 기여하지 않는다
        tmax = ((d - dh) / sd).max(0)
        tobs = (dh / sd)[:, 0]
        for j, (nm, src, est) in enumerate(meta):
            p_ind = pval(d[j], est)
            p_adj = (1 + np.sum(tmax >= tobs[j])) / (len(tmax) + 1)
            rows2.append({"block": bl, "candidate": nm, "source": src, "dSh": est, "t": tobs[j], "p_indiv": p_ind,
                          "p_maxT_adj": p_adj, "mcse_adj": mcse(p_adj, B0)})
    MS = pd.DataFrame(rows2)
    MS.to_csv(C.OUT / "maxstat.csv", index=False, encoding="utf-8-sig")
    print(MS[MS.block == MAIN_BLOCK].to_string(index=False), flush=True)

    # ── 3. Bitstamp: 중첩/비중첩 · 비용·펀딩 민감도 ──
    rows3 = []
    ov = C.START
    for cost in (0.0007, 0.001, 0.002, 0.003, 0.005):
        for fund in (C.E.FUND, 0.0):
            keep = C.E.FUND
            C.E.FUND = fund
            try:
                rb_, rc_ = C.run_bs(0, cost), C.run_bs(2, cost)
                others = {bf: C.run_bs(bf, cost) for bf in (1, 3)} if (cost == 0.0007 and fund == keep) else {}
            finally:
                C.E.FUND = keep
            for seg, lo, hi in (("full", None, None), ("pre-2021-03 (OKX 비중첩)", None, ov), ("2021-03~ (OKX 중첩)", ov, None)):
                a, b = C.daily(rb_[0], lo, hi).values, C.daily(rc_[0], lo, hi).values
                main_cfg = cost == 0.0007 and fund == keep
                reps = B0 if main_cfg else 5000
                r = paired(a, b, MAIN_BLOCK, reps)
                rows3 += flat("Bitstamp 1x BUF2 vs BUF0", r, {"segment": seg, "cost": cost, "fund_per4h": fund, "block": MAIN_BLOCK,
                                                            "B": reps, "n_days": len(a),
                                                            "trades_base": len(rb_[1]), "trades_cand": len(rc_[1])})
            for bf, rr in others.items():
                a, b = C.daily(rb_[0]).values, C.daily(rr[0]).values
                r = paired(a, b, MAIN_BLOCK, B0)
                rows3 += flat(f"Bitstamp 1x BUF{bf} vs BUF0", r, {"segment": "full", "cost": cost, "fund_per4h": fund,
                                                                "block": MAIN_BLOCK, "B": B0, "n_days": len(a)})
        print(f"bitstamp cost {cost} done", flush=True)
    BS = pd.DataFrame(rows3)
    BS.to_csv(C.OUT / "bitstamp.csv", index=False, encoding="utf-8-sig")

    # ── 4. 사전등록 6개 비교(BUF1·2·3 × OKX·Bitstamp) Holm ──
    six = []
    ms = MS[MS.block == MAIN_BLOCK].set_index("candidate").p_indiv
    bsm = BS[(BS.segment == "full") & (BS.cost == 0.0007) & (BS.stat == "dSh_cv") & (BS.fund_per4h > 0)].set_index("comparison").p
    for bf in (1, 2, 3):
        six.append((f"OKX BUF{bf}", ms["EXIT BUF2 (main)" if bf == 2 else f"EXIT BUF{bf}"]))
        six.append((f"Bitstamp BUF{bf}", bsm[f"Bitstamp 1x BUF{bf} vs BUF0"]))
    six.sort(key=lambda x: x[1])
    holm, run = [], 0.0
    for i, (nm, p) in enumerate(six):
        run = max(run, min(1.0, (len(six) - i) * p))
        holm.append({"comparison": nm, "p": p, "holm_adj": run})
    H = pd.DataFrame(holm)
    H.to_csv(C.OUT / "holm_prereg6.csv", index=False, encoding="utf-8-sig")
    print(H.to_string(index=False), flush=True)

    # ── 5. 레버리지 상호작용 (진단. 배율을 다시 고르지 않는다) ──
    rows4 = []
    for lev in (1, 2, 3, 5):
        a, b = C.daily(C.run_okx(0, lev)[0]).values, C.daily(C.run_okx(2, lev)[0]).values
        r = paired(a, b, MAIN_BLOCK, B0)
        rows4 += flat(f"OKX {lev}x BUF2 vs BUF0", r, {"lev": lev, "segment": "full", "block": MAIN_BLOCK, "B": B0})
    pd.DataFrame(rows4).to_csv(C.OUT / "lev_interaction.csv", index=False, encoding="utf-8-sig")

    # ── 6. 원 관문 C (backtest_lev.boot 5분위 CAGR) 의 블록 길이 민감도 — 원 구현 그대로(비원형·절단·seed 0·2000회) ──
    import backtest_lev as L
    keepb = L.BLOCK
    rows5 = []
    try:
        for bl in BLOCKS:
            L.BLOCK = bl
            qb, nb_ = L.boot(base[0])
            qc, nc_ = L.boot(cand[0])
            rows5.append({"block": bl, "base_q5": qb[0], "cand_q5": qc[0], "gateC_pass": bool(qc[0] > qb[0]),
                          "base_neg%": nb_, "cand_neg%": nc_, "draws": L.DRAWS})
    finally:
        L.BLOCK = keepb
    pd.DataFrame(rows5).to_csv(C.OUT / "bootC_blocks.csv", index=False, encoding="utf-8-sig")
    print(pd.DataFrame(rows5).to_string(index=False))

    # ── 경계 규칙: 주 p 가 α/27 근처면 B_BIG 로 재실행 ──
    thr = ALPHA / 27
    if abs(main_p - thr) < 3 * mcse(max(main_p, thr), B0):
        rb, rc = C.daily(base[0]).values, C.daily(cand[0]).values
        r = paired(rb, rc, MAIN_BLOCK, B_BIG, seed=SEED + 7)
        rows += flat("OKX 3x BUF2 vs BUF0 [boundary rerun]", r, {"segment": "full", "block": MAIN_BLOCK, "B": B_BIG,
                                                                "n_days": len(rb)})
        print("boundary rerun", r["dSh_cv"])
    pd.DataFrame(rows).to_csv(C.OUT / "paired.csv", index=False, encoding="utf-8-sig")
    print(f"done {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
