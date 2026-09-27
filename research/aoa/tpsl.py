"""aoa 역추세 진입 + 익절/손절 비율 탐색 — 사전등록 (2026-09-26, 결과 보기 전에 작성).

진입: 5분봉 종가가 1h EMA(12봉) 대비 −D% 이하 → 롱, +D% 이상 → 숏(양방향일 때만). 다음 봉 시가 체결, 동시 1포지션, 물타기 없음.
청산: 진입가 대비 익절 TP% / 손절 SL%, 시간손절 3일(864봉). 한 봉에서 둘 다 닿으면 손절. 비용 편도 0.07%.
격자 216개: D ∈ {0.5, 1, 2} × TP ∈ {0.5, 1, 2, 3, 4, 6} × SL ∈ {0.5, 1, 2, 3, 4, 6} × {양방향, 롱만}.

구간: 탐색 IS = BitMEX 2018-03~2021-12 · 검증 OOS = OKX 2022-01~2025-12 (전반 22-23 / 후반 24-25) · 최종 확인 = OKX 2026-01~09.

선정 (IS 만 보고): 거래당 기대값 > 0, 거래 ≥ 30건, 이웃 조합(TP·SL 한 칸씩 옆, 같은 D·방향) 과반도 IS 기대값 > 0.
  그중 IS 기대값 상위 5개를 '후보'로 확정 → 그 뒤에야 OOS 를 본다.
채택 (후보별): ① OOS 전반·후반 둘 다 기대값 > 0 ② OOS 기대값이 같은 TP/SL·같은 방향 구성의 무작위 진입(20회 평균)보다 높다
  ③ 2026 최종 확인 기대값 > 0. 셋 다 통과한 후보만 '봇 이식 후보'. 없으면 "플러스 조합 없음"으로 기록한다.
참고로 216개 전부의 OOS 결과도 출력한다 (선정에는 안 씀) — '우연히 플러스'가 몇 개나 나오는지 보여 주려고.
"""
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import features as F  # noqa: E402
from clone import okx_5m, prep  # noqa: E402

COST = 0.0007
MAXB = 864
DS, TPS, SLS = (0.5, 1.0, 2.0), (0.5, 1, 2, 3, 4, 6), (0.5, 1, 2, 3, 4, 6)


def path_exit(o, h, l, i, s, tp, sl):
    """i 봉 시가 진입 → (청산 봉, 수익률 %). 비용 전."""
    p = o[i]; up, dn = p * (1 + s * tp / 100), p * (1 - s * sl / 100)
    end = min(i + MAXB, len(o) - 1)
    for j in range(i, end):
        if (l[j] <= dn) if s == 1 else (h[j] >= dn):
            return j, -sl
        if (h[j] >= up) if s == 1 else (l[j] <= up):
            return j, tp
    return end, s * (o[end] / p - 1) * 100


def run(p, D, tp, sl, long_only, lo, hi):
    o, h, l, dev, idx = p["o"], p["h"], p["l"], p["dev"], p["idx"]
    i0, i1 = idx.searchsorted(lo), idx.searchsorted(hi)
    out, i = [], max(i0, 300)
    while i < i1 - 1:
        s = 1 if dev[i] <= -D else (-1 if dev[i] >= D and not long_only else 0)
        if s == 0:
            i += 1; continue
        j, r = path_exit(o, h, l, i + 1, s, tp, sl)
        out.append((i + 1, s, r - 2 * COST * 100))
        i = j + 1
    return out


def rand_ev(p, trades, tp, sl, lo, hi, reps=20, seed=0):
    """같은 거래 수·같은 방향 구성으로 무작위 시각에 진입했을 때의 평균 기대값."""
    if not trades:
        return np.nan
    o, h, l, idx = p["o"], p["h"], p["l"], p["idx"]
    i0, i1 = idx.searchsorted(lo), idx.searchsorted(hi) - MAXB
    rng, sides, evs = np.random.default_rng(seed), np.array([s for _, s, _ in trades]), []
    for _ in range(reps):
        ii = rng.integers(i0, i1, len(sides))
        evs.append(np.mean([path_exit(o, h, l, i, s, tp, sl)[1] - 2 * COST * 100 for i, s in zip(ii, sides)]))
    return float(np.mean(evs))


def ev(tr):
    r = np.array([x for _, _, x in tr])
    return (r.mean() if len(r) else np.nan), len(r), ((r > 0).mean() if len(r) else np.nan)


if __name__ == "__main__":
    T = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731
    PI = prep(F.candles().loc["2018-02-01":"2021-12-31"])
    PO = prep(okx_5m().loc["2021-11-01":])
    rows = []
    for lo_ in (False, True):
        for D in DS:
            for tp in TPS:
                for sl in SLS:
                    a = run(PI, D, tp, sl, lo_, T("2018-03-05"), T("2022-01-01"))
                    b1 = run(PO, D, tp, sl, lo_, T("2022-01-01"), T("2024-01-01"))
                    b2 = run(PO, D, tp, sl, lo_, T("2024-01-01"), T("2026-01-01"))
                    (e, n, w), (e1, n1, _), (e2, n2, _) = ev(a), ev(b1), ev(b2)
                    rows.append(dict(side="롱만" if lo_ else "양방향", D=D, tp=tp, sl=sl, IS_n=n, IS_win=w, IS_ev=e,
                                     OOS1_n=n1, OOS1_ev=e1, OOS2_n=n2, OOS2_ev=e2))
        print("done", "롱만" if lo_ else "양방향", flush=True)
    g = pd.DataFrame(rows)

    def nb_ok(r):
        m = g[(g.side == r.side) & (g.D == r.D)]
        ti, si = TPS.index(r.tp), SLS.index(r.sl)
        nb = m[((m.tp.map(TPS.index) - ti).abs() + (m.sl.map(SLS.index) - si).abs()) == 1]
        return (nb.IS_ev > 0).mean() > 0.5

    g["nb"] = g.apply(nb_ok, axis=1)
    pd.set_option("display.width", 250); pd.set_option("display.max_rows", 300)
    print(f"IS 기대값>0: {(g.IS_ev > 0).sum()}/216 · OOS 전반·후반 둘 다 >0: {((g.OOS1_ev > 0) & (g.OOS2_ev > 0)).sum()}/216 "
          f"· IS·OOS 셋 다 >0: {((g.IS_ev > 0) & (g.OOS1_ev > 0) & (g.OOS2_ev > 0)).sum()}/216")
    cand = g[(g.IS_ev > 0) & (g.IS_n >= 30) & g.nb].sort_values("IS_ev", ascending=False).head(5)
    print("\n후보 (IS 로만 선정):"); print(cand.round(3).to_string())
    out = []
    for r in cand.itertuples():
        lo_ = r.side == "롱만"
        tr = run(PO, r.D, r.tp, r.sl, lo_, T("2022-01-01"), T("2026-01-01"))
        rnd = rand_ev(PO, tr, r.tp, r.sl, T("2022-01-01"), T("2026-01-01"))
        e26, n26, w26 = ev(run(PO, r.D, r.tp, r.sl, lo_, T("2026-01-01"), T("2027-01-01")))
        ok = r.OOS1_ev > 0 and r.OOS2_ev > 0 and ev(tr)[0] > rnd and e26 > 0
        out.append(dict(side=r.side, D=r.D, tp=r.tp, sl=r.sl, OOS_ev=ev(tr)[0], OOS_win=ev(tr)[2], OOS_n=ev(tr)[1],
                        random_ev=rnd, y2026_ev=e26, y2026_n=n26, 채택=ok))
    print("\n후보 검증:"); print(pd.DataFrame(out).round(3).to_string())
    print("\n참고 — 216개 전체:"); print(g.round(3).to_string())
    g.to_csv(pathlib.Path(__file__).with_name("tpsl_grid.csv"), index=False, encoding="utf-8-sig")
