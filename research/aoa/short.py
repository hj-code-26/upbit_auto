"""숏 전용 탐색 — 급락 매수(롱)의 거울상. 사전등록 (2026-09-26, 결과 보기 전 작성).

진입: 5분 종가가 1h EMA(12봉) 대비 +D% 이상 → 다음 봉 시가 숏 (급등을 받아친다). 동시 1포지션, 물타기 없음.
청산: 진입가 대비 익절 TP% / 손절 SL% (갭이면 시가), 시간손절 3일. 비용 편도 0.07%. 펀딩은 0 으로 둔다
      (숏은 보통 펀딩을 받지만 OKX 실측 평균 +0.004%/8h 로 작다 — 유리한 가정을 넣지 않는다).
필터: none / 하락장(전날 일봉 종가 < 전날까지 50일 이평) — 롱의 50일선 필터 거울상.
격자 216개: D ∈ {0.5, 1, 2} × TP ∈ {0.5,1,2,3,4,6} × SL ∈ {0.5,1,2,3,4,6} × 필터 {none, 하락장}.

선정 (BTC IS = BitMEX 2018-03~2021-12 만 보고): 기대값 > 0 · 거래 ≥ 30 · 이웃(TP·SL 한 칸) 과반 IS 기대값 > 0 → 상위 5개.
채택 (후보별 전부 통과해야): ① BTC OOS(OKX) 2022-23 · 2024-25 둘 다 기대값 > 0
  ② OOS 기대값 > 같은 조건 날짜 안 무작위 시각 숏(10회 평균)   ③ 2026-01~09 기대값 > 0 (거래 있으면)
  ④ 처음 보는 알트 6종(바이낸스 2020-01~2026-09)에서 기대값 > 0 인 코인 ≥ 4/6
IS 에서 후보가 하나도 안 나오면 그 자체로 "숏 전략 없음".
"""
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import features as F  # noqa: E402
from clone import okx_5m, prep  # noqa: E402
from sma50 import ALTS  # noqa: E402
from tpsl import COST, DS, MAXB, SLS, TPS, path_exit  # noqa: E402


def bear_mask(c):
    d = c.close.resample("1D").last()
    bear = (d < d.rolling(50).mean()).shift(1)
    return bear.reindex(c.index.normalize()).fillna(False).values.astype(bool)


def run(p, mask, D, tp, sl, lo, hi):
    o, h, l, dev, idx = p["o"], p["h"], p["l"], p["dev"], p["idx"]
    i, i1, out = max(idx.searchsorted(lo), 300), idx.searchsorted(hi), []
    while i < i1 - 1:
        if dev[i] < D or not mask[i]:
            i += 1; continue
        j, r = path_exit(o, h, l, i + 1, -1, tp, sl)
        out.append(r - 2 * COST * 100)
        i = j + 1
    return out


def rand(p, mask, n, tp, sl, lo, hi, reps=10):
    idx = p["idx"]; a, b = idx.searchsorted(lo), idx.searchsorted(hi) - MAXB
    ok = np.where(mask[a:b])[0] + a
    if not n or not len(ok):
        return np.nan
    rng = np.random.default_rng(0)
    return float(np.mean([path_exit(p["o"], p["h"], p["l"], i, -1, tp, sl)[1] - 2 * COST * 100
                          for _ in range(reps) for i in rng.choice(ok, n)]))


ev = lambda r: (np.mean(r) if len(r) else np.nan)  # noqa: E731

if __name__ == "__main__":
    T = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731
    ci, co = F.candles().loc["2018-02-01":"2021-12-31"], okx_5m().loc["2021-11-01":]
    PI, PO = prep(ci), prep(co)
    MI = {"none": np.ones(len(ci), bool), "하락장": bear_mask(ci)}
    MO = {"none": np.ones(len(co), bool), "하락장": bear_mask(co)}
    rows = []
    for fl in MI:
        for D in DS:
            for tp in TPS:
                for sl in SLS:
                    a = run(PI, MI[fl], D, tp, sl, T("2018-03-05"), T("2022-01-01"))
                    b1 = run(PO, MO[fl], D, tp, sl, T("2022-01-01"), T("2024-01-01"))
                    b2 = run(PO, MO[fl], D, tp, sl, T("2024-01-01"), T("2026-01-01"))
                    rows.append(dict(filter=fl, D=D, tp=tp, sl=sl, IS_n=len(a), IS_win=np.mean(np.array(a) > 0) if a else np.nan,
                                     IS_ev=ev(a), OOS1_n=len(b1), OOS1_ev=ev(b1), OOS2_n=len(b2), OOS2_ev=ev(b2)))
        print("done", fl, flush=True)
    g = pd.DataFrame(rows)

    def nb_ok(r):
        m = g[(g["filter"] == r["filter"]) & (g.D == r.D)]
        dist = (m.tp.map(TPS.index) - TPS.index(r.tp)).abs() + (m.sl.map(SLS.index) - SLS.index(r.sl)).abs()
        return (m[dist == 1].IS_ev > 0).mean() > 0.5

    g["nb"] = g.apply(nb_ok, axis=1)
    pd.set_option("display.width", 250); pd.set_option("display.max_rows", 300)
    print(f"IS 기대값>0: {(g.IS_ev > 0).sum()}/216 · OOS 두 구간 >0: {((g.OOS1_ev > 0) & (g.OOS2_ev > 0)).sum()}/216 "
          f"· 셋 다 >0: {((g.IS_ev > 0) & (g.OOS1_ev > 0) & (g.OOS2_ev > 0)).sum()}/216")
    print("필터·D 별 IS 기대값 평균:", g.groupby(["filter", "D"]).IS_ev.mean().round(3).to_dict())
    print("필터·D 별 OOS 기대값 평균:", g.assign(o=(g.OOS1_ev + g.OOS2_ev) / 2).groupby(["filter", "D"]).o.mean().round(3).to_dict())
    cand = g[(g.IS_ev > 0) & (g.IS_n >= 30) & g.nb].sort_values("IS_ev", ascending=False).head(5)
    print("\n후보 (IS 로만 선정):"); print(cand.round(3).to_string() if len(cand) else "  없음 → 숏 전략 없음")
    alts = {}
    if len(cand):
        for s in ALTS:
            c = pd.read_pickle(F.ROOT / "data_cache" / f"binance_{s.lower()}_5m_2020.pkl").asfreq("5min").ffill()
            alts[s] = (prep(c), {"none": np.ones(len(c), bool), "하락장": bear_mask(c)})
    out = []
    for r in cand.itertuples():
        fl = r.filter
        oo = run(PO, MO[fl], r.D, r.tp, r.sl, T("2022-01-01"), T("2026-01-01"))
        rn = rand(PO, MO[fl], len(oo), r.tp, r.sl, T("2022-01-01"), T("2026-01-01"))
        y26 = run(PO, MO[fl], r.D, r.tp, r.sl, T("2026-01-01"), T("2027-01-01"))
        alt_ev = {s[:-4]: ev(run(p, m[fl], r.D, r.tp, r.sl, T("2020-01-01"), T("2026-10-01"))) for s, (p, m) in alts.items()}
        ok = [r.OOS1_ev > 0 and r.OOS2_ev > 0, ev(oo) > rn, (not y26) or ev(y26) > 0, sum(v > 0 for v in alt_ev.values()) >= 4]
        out.append(dict(filter=fl, D=r.D, tp=r.tp, sl=r.sl, OOS_n=len(oo), OOS_ev=ev(oo), random=rn, y26_n=len(y26), y26_ev=ev(y26),
                        alts_pos=sum(v > 0 for v in alt_ev.values()), alt_ev={k: round(v, 2) for k, v in alt_ev.items()},
                        판정="채택" if all(ok) else "기각(" + ",".join("①②③④"[k] for k in range(4) if not ok[k]) + ")"))
    if out:
        print("\n후보 검증:"); print(pd.DataFrame(out).round(3).to_string())
    g.to_csv(pathlib.Path(__file__).with_name("short_grid.csv"), index=False, encoding="utf-8-sig")
