"""상황별 익절·손절 — 사전등록 (2026-09-29, 결과 보기 전 작성·커밋). 운영 코드는 건드리지 않는다.

질문: 급락 매수 진입(현행: 5분 종가 1h EMA −2% & 전날 종가 50·200일선 위)은 그대로 두고, 진입 순간의 변동성·RSI 등에 따라
      익절(TP)·손절(SL) 폭을 바꾸면 고정 TP4/SL6 보다 나은가.
체결: exec_model B2 와 같은 규칙을 거래마다 다른 TP/SL 로 재구현 (진입 봉 건너뛰기 · 봉 마감 뒤 다음 봉 시가 · 3일 만기 ·
      한 봉에 둘 다면 손절) · 비용 편도 0.07% · 펀딩 0.01%/8h · 1배. TP4/SL6 고정에서 exec_model.trades 와 거래 단위 일치를 assert.

가족 (각 가족 안의 후보는 아래 목록뿐)
  F0 고정        TP4 / SL6 (현행, 기준)
  F1 변동성(실현) σ = 직전 24h 5분 로그수익 표준편차 × √288 (%) → TP = a·σ, SL = b·σ   a ∈ {0.5,0.75,1,1.25,1.5,2} · b ∈ {1,1.5,2,2.5,3,4}
                  (TP 1~15%, SL 1.5~20% 로 자름)
  F2 변동성(예측) σ = forecast.py 기본 모델의 24h 하위10~상위10% 폭 ÷ 2.563 (그 해를 안 본 모델 — 2018~21 은 2022 이후로, 2022~ 는 그 해 이전으로 학습)
                  → F1 과 같은 a·b 격자
  F3 RSI          5분 RSI(14) < 20 인 급락(투매)이면 TP t / SL s, 아니면 TP4/SL6     t ∈ {2,3,4,5,6} · s ∈ {4,6,8}
  F4 급락 깊이    1h EMA 괴리 ≤ −3% 이면 TP t / SL s, 아니면 TP4/SL6                같은 격자
  F5 거래량 급증  1h 거래량 / 24h 평균 시간당 ≥ e (vz ≥ 1) 이면 TP t / SL s, 아니면 TP4/SL6   같은 격자
  σ 가 없으면(초기 결측) 그 거래는 TP4/SL6.
고르기 (IS = BitMEX 2018-03~2021-12 · 바이낸스 BTC 2020-03~2022-12):
  점수 = 두 IS 데이터의 (1배 연복리 ÷ |MDD|) 중 작은 값. 가족마다 점수 1등 하나. F0 보다 점수가 낮으면 그 가족은 탈락.
확인 (결과 보기 전 고정):
  시간 밖 BTC  : 바이낸스 2023-01~2026-09 · OKX 2023-01~2026-09
  자산 밖      : 알트 6종(바이낸스 현물 ETH·SOL·XRP·BNB·DOGE·ADA, 200일 예열 뒤 ~2026-09)
  통과 = ① 시간 밖 BTC 2종 모두 1배 연복리 ≥ F0 이고 MDD 가 F0 보다 3%p 넘게 깊지 않음
         ② 알트 6종 중 4종 이상 (연복리÷|MDD|) > F0
  가족이 5개라 하나쯤 우연히 통과할 수 있다(다중 비교). 통과해도 '모의 운용 후보' 까지, 반영은 사용자 결정.
보고만: 가족별 IS 상위 5 · 고른 규칙의 평균 TP/SL · OKX 2021-26 전체 · 거래 수·승률·거래당·2배.
한계: 시간 밖 BTC 는 거래가 20건 안팎이라 ① 은 흔들린다. 무게는 알트(수백 건)에 둔다.
      F2 의 2018~21 σ 는 2022~26 으로 학습한 모델이라, IS 고르기에 쓰는 σ 가 확인 구간의 가격 관계를 일부 배운 상태다
      (확인 구간 자체의 σ 는 그 해 이전만 학습 — 확인 성적에는 누수 없음).
사용: python research/aoa/adaptive_exit.py   (결과 → research/aoa/adaptive_exit_result.txt)
"""
import pathlib
import sys

import numpy as np
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE))
import exec_model as M  # noqa: E402
import features as F  # noqa: E402
import forecast as FC  # noqa: E402
import strategy as S  # noqa: E402
from clone import okx_5m  # noqa: E402

T = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731
COST, FUND = 0.0007, 0.0001
ALTS = ["ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT", "ADAUSDT"]
A_, B_ = (0.5, 0.75, 1, 1.25, 1.5, 2), (1, 1.5, 2, 2.5, 3, 4)
TS, SS = (2, 3, 4, 5, 6), (4, 6, 8)
FAMS = {"F0 고정": [("TP4/SL6", ("fix",))],
        "F1 변동성(실현)": [(f"TP {a}σ / SL {b}σ", ("rv", a, b)) for a in A_ for b in B_],
        "F2 변동성(예측)": [(f"TP {a}σ / SL {b}σ", ("fc", a, b)) for a in A_ for b in B_],
        "F3 RSI<20": [(f"RSI<20 → TP{t}/SL{s}", ("rsi", t, s)) for t in TS for s in SS],
        "F4 괴리≤−3%": [(f"괴리≤−3% → TP{t}/SL{s}", ("deep", t, s)) for t in TS for s in SS],
        "F5 거래량급증": [(f"거래량급증 → TP{t}/SL{s}", ("vol", t, s)) for t in TS for s in SS]}


def context(c, d1, train_c, train_d1):
    """신호 봉(급락 & 국면)마다 σ_rv · σ_fc · RSI · 괴리 · 거래량 급증."""
    dev, bull = S.indicators(c, d1)
    sig = ((dev <= -S.D) & bull).values
    f = FC.features(c, d1)
    cand = c.index[sig]
    ctx = pd.DataFrame(index=cand)
    ctx["rv"] = f.rv24h.reindex(cand).values * np.sqrt(288) * 100
    ctx["rsi"], ctx["dev"], ctx["vz"] = f.rsi5m.reindex(cand).values, dev.reindex(cand).values, f.vz.reindex(cand).values
    X, y, _ = FC.dataset(train_c, train_d1, 288)
    fx = f.reindex(cand)
    ctx["fc"] = np.nan
    yrs = cand.year
    folds = [(yrs <= 2021, X.index >= T("2022-01-01"))] + \
            [(yrs == yr, X.index < T(f"{yr}-01-01") - pd.Timedelta("1D")) for yr in range(2022, 2027)]
    for te, tr in folds:
        ok = te & fx.notna().all(axis=1).values
        if ok.sum() == 0 or tr.sum() < 5000:
            continue
        q1 = FC.HGR(loss="quantile", quantile=0.1, **FC.KW).fit(X[tr], y[tr]).predict(fx[ok])
        q9 = FC.HGR(loss="quantile", quantile=0.9, **FC.KW).fit(X[tr], y[tr]).predict(fx[ok])
        ctx.loc[ok, "fc"] = (q9 - q1) / 2.563 * 100
    return sig, ctx


def levels(ctx, rule):
    k = rule[0]
    tp, sl = pd.Series(4.0, index=ctx.index), pd.Series(6.0, index=ctx.index)
    if k in ("rv", "fc"):
        s = ctx[k]
        ok = s.notna()
        tp[ok] = (rule[1] * s[ok]).clip(1, 15); sl[ok] = (rule[2] * s[ok]).clip(1.5, 20)
    elif k != "fix":
        m = {"rsi": ctx.rsi < 20, "deep": ctx.dev <= -3, "vol": ctx.vz >= 1}[k]
        tp[m], sl[m] = rule[1], rule[2]
    return tp, sl


def sim(c, sig, tp, sl, lo, hi):
    """exec_model.trades B2 와 같은 규칙, 거래마다 다른 TP/SL."""
    o, h, l, idx = c.open.values, c.high.values, c.low.values, c.index
    bn = np.asarray((idx - idx[0]) // S.BAR)
    n, i, i1 = len(o), idx.searchsorted(lo), idx.searchsorted(hi - S.BAR)
    cand, pos = np.flatnonzero(sig), {t: k for k, t in enumerate(tp.index)}
    out = []
    while True:
        p = np.searchsorted(cand, i)
        if p >= len(cand):
            break
        k = cand[p]
        if k >= i1 or k + 1 >= n:
            break
        q = pos[idx[k]]
        e = k + 1; entry = o[e]; up = entry * (1 + tp.iat[q] / 100); dn = entry * (1 - sl.iat[q] / 100)
        t0 = e + 1
        jx = int(np.searchsorted(bn, bn[t0] + S.MAXB)) if t0 < n else n
        a = np.flatnonzero(l[t0:jx] <= dn); b = np.flatnonzero(h[t0:jx] >= up)
        ja, jb = (t0 + a[0] if len(a) else n), (t0 + b[0] if len(b) else n)
        if ja < jx and ja <= jb:
            j, why, apx = ja, "손절", min(dn, o[ja])
        elif jb < jx:
            j, why, apx = jb, "익절", up
        else:
            j, why = jx, "만기"
            if j >= n:
                break
            apx = o[j]
        if j + 1 >= n:
            break
        out.append(dict(t0=idx[e], t1=idx[j], why=why, entry=entry, b_px=o[j + 1], b_bars=j + 1 - e,
                        mae=min(l[e:j + 1].min() / entry - 1, min(apx, o[j + 1]) / entry - 1), tp=tp.iat[q], sl=sl.iat[q]))
        i = j + 1
    tr = pd.DataFrame(out)
    r = (tr.b_px / tr.entry - 1 - 2 * COST - FUND * tr.b_bars / 96) if len(tr) else pd.Series(dtype=float)
    return tr, r.reset_index(drop=True)


def summ(tr, r, lo, hi, c):
    if len(tr) == 0:
        return dict(n=0, ev=np.nan, win=np.nan, cagr=0.0, mdd=0.0, rr=0.0, c2=0.0, m2=0.0, tp=np.nan, sl=np.nan)
    yrs = (min(hi, c.index[-1]) - max(lo, c.index[0])).days / 365.25
    p, mdd, _, _ = M.curve(tr, r)
    p2, m2, _, _ = M.curve(tr.assign(mae=tr.mae * 2), r * 2)
    cagr = p[-1] ** (1 / yrs) - 1
    return dict(n=len(tr), ev=r.mean(), win=(r > 0).mean(), cagr=cagr, mdd=mdd, rr=cagr / max(abs(mdd), 0.01),
                c2=max(p2[-1], 1e-9) ** (1 / yrs) - 1, m2=m2, tp=tr.tp.mean(), sl=tr.sl.mean())


def fmt(s):
    return (f"{s['n']:>3}건 승률 {s['win']:.0%} 거래당 {s['ev'] * 100:+.2f}% 연 {s['cagr'] * 100:+.1f}% MDD {s['mdd'] * 100:.0f}% (위험대비 {s['rr']:.2f})"
            f" · 2배 연 {s['c2'] * 100:+.1f}% MDD {s['m2'] * 100:.0f}% · 평균 TP {s['tp']:.1f}/SL {s['sl']:.1f}") if s["n"] else "  0건"


if __name__ == "__main__":
    lines = []
    P = lambda *a: (print(*a, flush=True), lines.append(" ".join(map(str, a))))  # noqa: E731
    P(__doc__.split("\n")[0])
    bs = pd.read_pickle(F.ROOT / "data_cache" / "bitstamp_1d_2016.pkl").close
    bc, bd = FC.history()
    D = {}
    for name, c in (("BitMEX", F.candles().loc["2017-12-01":"2022-01-10"]), ("바이낸스", bc), ("OKX", okx_5m())):
        own = c.close.resample("1D").last()
        d1 = pd.concat([bs[bs.index < own.index[0]], own])
        D[name] = (c, *context(c, d1, bc, bd))
    for sym in ALTS:
        c = pd.read_pickle(F.ROOT / "data_cache" / f"binance_{sym.lower()}_5m_2020.pkl").asfreq("5min").ffill()
        own = c.close.resample("1D").last()
        D[sym] = (c, *context(c, own, c, own))
    # 재구현 검증: 고정 TP4/SL6 == exec_model.trades B2
    c, sig, ctx = D["OKX"]
    own = c.close.resample("1D").last()
    ref = M.trades(c, pd.concat([bs[bs.index < own.index[0]], own]), T("2021-05-01"), T("2026-10-01"), skip=1)
    mine, r = sim(c, sig, *levels(ctx, ("fix",)), T("2021-05-01"), T("2026-10-01"))
    rr_ = np.asarray(M.net(ref, "B", 0.0, "lo", COST, FUND))
    assert len(ref) == len(mine) and np.allclose(rr_, r.values), (len(ref), len(mine))
    P(f"재구현 검증: OKX 고정 TP4/SL6 {len(mine)}건 exec_model B2 와 거래 단위 일치")

    SP = {"IS BitMEX 18-21": ("BitMEX", T("2018-03-05"), T("2022-01-01")), "IS 바이낸스 20-22": ("바이낸스", T("2020-03-01"), T("2023-01-01")),
          "OOS 바이낸스 23-26": ("바이낸스", T("2023-01-01"), T("2026-10-01")), "OOS OKX 23-26": ("OKX", T("2023-01-01"), T("2026-10-01")),
          "참고 OKX 21-26": ("OKX", T("2021-05-01"), T("2026-10-01"))}
    alt_span = {s: (D[s][0].index[0] + pd.Timedelta("201D"), T("2026-10-01")) for s in ALTS}

    def ev(nm, lo, hi, rule):
        c, sig, ctx = D[nm]
        return summ(*sim(c, sig, *levels(ctx, rule), lo, hi), lo, hi, c)

    base = {k: ev(*v, ("fix",)) for k, v in SP.items()}
    base_alt = {s: ev(s, *alt_span[s], ("fix",)) for s in ALTS}
    base_is = min(base["IS BitMEX 18-21"]["rr"], base["IS 바이낸스 20-22"]["rr"])
    P(f"\nF0 고정 TP4/SL6: IS 점수 {base_is:.2f}")
    for k, s in base.items():
        P(f"  {k:16s} {fmt(s)}")
    P("  알트: " + " · ".join(f"{s[:-4]} {base_alt[s]['rr']:.2f}" for s in ALTS) + "  (위험대비)")
    verdict = {}
    for fam, cands in list(FAMS.items())[1:]:
        sc = []
        for lab, rule in cands:
            a, b = ev(*SP["IS BitMEX 18-21"], rule), ev(*SP["IS 바이낸스 20-22"], rule)
            sc.append((min(a["rr"], b["rr"]), lab, rule, a, b))
        sc.sort(key=lambda x: -x[0])
        P(f"\n==== {fam} ==== IS 상위 5 (점수 = 두 IS 위험대비 중 작은 값, F0 {base_is:.2f})")
        for s_, lab, _, a, b in sc[:5]:
            P(f"  {s_:.2f}  {lab:24s} BitMEX 연 {a['cagr'] * 100:+.1f}% MDD {a['mdd'] * 100:.0f}% · 바이낸스20-22 연 {b['cagr'] * 100:+.1f}% MDD {b['mdd'] * 100:.0f}%")
        s_, lab, rule, _, _ = sc[0]
        if s_ < base_is:
            P("  → IS 에서 F0 보다 못함 — 탈락"); verdict[fam] = False; continue
        P(f"  → 고른 규칙: {lab}")
        res = {k: ev(*v, rule) for k, v in SP.items()}
        for k in SP:
            P(f"    {k:16s} {fmt(res[k])}   (F0 연 {base[k]['cagr'] * 100:+.1f}% MDD {base[k]['mdd'] * 100:.0f}%)")
        alt = {s: ev(s, *alt_span[s], rule) for s in ALTS}
        k2 = sum(alt[s]["rr"] > base_alt[s]["rr"] for s in ALTS)
        P("    알트 위험대비 (규칙 / F0): " + " · ".join(f"{s[:-4]} {alt[s]['rr']:.2f}/{base_alt[s]['rr']:.2f}" for s in ALTS))
        c1 = all(res[k]["cagr"] >= base[k]["cagr"] and res[k]["mdd"] >= base[k]["mdd"] - 0.03 for k in ("OOS 바이낸스 23-26", "OOS OKX 23-26"))
        c2 = k2 >= 4
        verdict[fam] = c1 and c2
        P(f"    판정: ① 시간 밖 BTC {'통과' if c1 else '불합격'} · ② 알트 {k2}/6 {'통과' if c2 else '불합격'} → {'모의 운용 후보' if c1 and c2 else '기각'}")
    P("\n요약: " + " · ".join(f"{k} {'후보' if v else '기각'}" for k, v in verdict.items()))
    (HERE / "adaptive_exit_result.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
