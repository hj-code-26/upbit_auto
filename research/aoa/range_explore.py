"""횡보장 수익 탐색 — 방향 가정 없이(롱·숏·양쪽) 격자 탐색 → 뒤 구간·알트 확인 (2026-09-28, 실행 전 커밋).

왜: range_rev.py(롱만·50·200일선·조합 1개)는 기각. 사용자 요구 — BTC 장기 상승에 기대지 말고, 횡보장에서 이득을 보는 경우와
    그 비율·비중을 찾아라. 그래서 조합을 넓게 탐색하되, 고르는 구간과 확인하는 구간을 나눠 '과거에 맞춘 값' 을 걸러낸다.

횡보 판정 (range_rev 와 같음): 직전 N봉 박스 H·L, 폭 R=(H−L)/L 이 1% ≤ R < 3%, 거래량 < 3×직전 288봉 중앙값.
격자 216개 = 방향 3 × 진입 2 × 박스 2 × 익절 3 × 손절 3 × 보유 2
  방향   : 롱(박스 하단) · 숏(박스 상단) · 양쪽
  진입   : zone  = 종가가 박스 하단 15%(숏은 상단 15%) 안
           sweep = 봉이 박스 밖으로 꼬리를 냈다가 종가가 박스 안으로 복귀 (가짜 이탈)
           → 둘 다 신호 봉 종가 ∓0.1% 지정가, 12봉 유효, 뚫어야 체결 (range_rev 와 같음)
  박스   : 288봉(24h) · 144봉(12h)
  익절 f : 박스 반대쪽으로 폭의 0.3 · 0.5 · 0.8 지점 (롱 L+f(H−L), 숏 H−f(H−L)) — 지정가
  손절 k : 박스 밖으로 폭의 0.25 · 0.5 · 1.0 (롱 L−k(H−L), 숏 H+k(H−L)) — 테이커
  보유   : 72봉(6h) · 288봉(24h), 넘으면 다음 봉 시가 시장가
비용: 메이커 0.02% · 테이커 0.07% · 펀딩 롱만 0.01%/8h 부담(숏 수취는 안 넣음 — 숏에 유리한 가정 금지). 1배, 한 번에 하나.
체결 봉: 불리한 쪽(손절)을 먼저 본다. 한 봉에 둘 다 → 손절.

구간
  탐색(IS) : BitMEX 2018-03~2021-12 · 바이낸스 BTC 2020-03~2022-12
  확인(OOS): 바이낸스 BTC 2023-01~2026-09 · OKX 2023-01~2026-09 · 알트 6종(바이낸스 현물) 2023-01~2026-09
고르는 규칙 (결과 보기 전 고정)
  IS 점수 = 두 IS 데이터 거래당 순수익의 최솟값 (각 50건 이상인 조합만). 점수 > 0 인 것 중 상위 5개.
  확인 통과 = OKX·바이낸스 OOS 거래당 > 0 & 알트 6종 중 4종 이상 > 0 & OKX OOS 1배 MDD ≥ −25%.
  통과 조합은 '모의 운용 후보' 까지. 실계좌 반영은 사용자 결정.
보고 (판정과 무관, 전부 출력)
  · 격자 전체의 '돈 버는 조합 비율': IS 양수 · OOS(OKX) 양수 · 둘 다 양수, 그리고 IS 순위와 OOS 순위의 상관(스피어만)
    — 상관이 0 근처면 '앞에서 좋았던 조합이 뒤에서도 좋다' 가 성립 안 한다는 뜻.
  · 롱/숏/양쪽, zone/sweep 별 OOS 평균
  · 상위 5개의 시장가(T) 스트레스
  · 펀딩 캐리 (현물 롱 + 무기한 숏, 가격 방향 중립): 바이낸스 BTC 실측 펀딩 2019-09~2026-09 을 횡보/조용/추세 국면별로 나눠
    연율·양수 비율, 그리고 계속 보유 시 연도별 수익(진입·청산 4다리 비용 0.2% 1회).
사용: python research/aoa/range_explore.py   (결과 → research/aoa/range_explore_result.txt)
"""
import itertools
import pathlib
import sys

import numpy as np
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[1]))
import exec_model as M  # noqa: E402
import features as F  # noqa: E402
from clone import okx_5m  # noqa: E402

MAKER, TAKER, FUND, WAIT = 0.0002, 0.0007, 0.0001, 12
ALTS = ["ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT", "ADAUSDT"]
GRID = list(itertools.product(("롱", "숏", "양쪽"), ("zone", "sweep"), (288, 144), (0.3, 0.5, 0.8), (0.25, 0.5, 1.0), (72, 288)))


def feats(c, box, volok):
    H = c.high.rolling(box).max().shift(1).values; L = c.low.rolling(box).min().shift(1).values
    R = (H - L) / L
    cl, h, l = c.close.values, c.high.values, c.low.values
    with np.errstate(invalid="ignore", divide="ignore"):
        pos = (cl - L) / (H - L)
        side = (R >= 0.01) & (R < 0.03) & volok
        return dict(H=H, L=L, R=R, side=side,
                    zone_l=side & (pos >= 0) & (pos <= 0.15), zone_s=side & (pos >= 0.85) & (pos <= 1),
                    sweep_l=side & (l < L) & (cl > L), sweep_s=side & (h > H) & (cl < H))


def signals(f, d, entry):
    lo_, sh_ = f[f"{entry}_l"], f[f"{entry}_s"]
    s = np.zeros(len(lo_), np.int8)
    if d in ("숏", "양쪽"): s[sh_] = -1
    if d in ("롱", "양쪽"): s[lo_] = 1                      # 같은 봉에 둘 다면 롱
    return s


def sim(c, f, sg, lo, hi, fr, kk, hold, model="M"):
    o, h, l, cl, idx = c.open.values, c.high.values, c.low.values, c.close.values, c.index
    H, L = f["H"], f["L"]
    n, i, i1 = len(o), idx.searchsorted(lo), idx.searchsorted(hi)
    cand, out = np.flatnonzero(sg), []
    while True:
        p = np.searchsorted(cand, i)
        if p >= len(cand):
            break
        k = cand[p]
        if k >= i1 or k + WAIT + hold + 3 >= n:
            break
        s, W = int(sg[k]), H[k] - L[k]
        tp, sl = (L[k] + fr * W, L[k] - kk * W) if s == 1 else (H[k] - fr * W, H[k] + kk * W)
        if model == "M":
            P = cl[k] * (1 - s * 0.001)
            hit = np.flatnonzero(l[k + 1:k + 1 + WAIT] < P) if s == 1 else np.flatnonzero(h[k + 1:k + 1 + WAIT] > P)
            if not len(hit):
                i = k + WAIT + 1; continue
            e = k + 1 + hit[0]; entry, c_in = P, MAKER
        else:
            e = k + 1; entry, c_in = o[e], TAKER
        adv = (lambda a, b: l[a:b] <= sl) if s == 1 else (lambda a, b: h[a:b] >= sl)
        fav = (lambda a, b: h[a:b] > tp) if s == 1 else (lambda a, b: l[a:b] < tp)
        worst = (lambda x: min(sl, x)) if s == 1 else (lambda x: max(sl, x))
        if adv(e, e + 1)[0]:
            j = xb = e; why, px = "손절", worst(o[e])
        else:
            a = np.flatnonzero(adv(e + 1, e + hold + 1)); b = np.flatnonzero(fav(e + 1, e + hold + 1))
            ja, jb = (a[0] if len(a) else hold), (b[0] if len(b) else hold)
            if ja == hold and jb == hold:
                j = xb = e + hold + 1; why, px = "만기", o[j]
            elif ja <= jb:
                j = xb = e + 1 + ja; why, px = "손절", worst(o[j])
            else:
                j = e + 1 + jb; why = "익절"
                px, xb = (tp, j) if model == "M" else (o[j + 1], j + 1)
        c_out = MAKER if (why == "익절" and model == "M") else TAKER
        r = s * (px / entry - 1) - c_in - c_out - (FUND * (xb - e) / 96 if s == 1 else 0)
        seg = slice(e, xb) if why == "만기" else slice(e, j + 1)
        mae = (l[seg].min() / entry - 1) if s == 1 else -(h[seg].max() / entry - 1)
        out.append((idx[e], idx[xb], why, s, r, min(mae, r)))
        i = xb if why == "만기" else xb + 1
    return pd.DataFrame(out, columns=["t0", "t1", "why", "s", "r", "mae"])


def summ(tr, days):
    if len(tr) == 0:
        return dict(n=0, ev=np.nan, win=np.nan, cagr=np.nan, mdd=np.nan)
    p, mdd, _, _ = M.curve(tr, tr.r.reset_index(drop=True))
    return dict(n=len(tr), ev=tr.r.mean(), win=(tr.r > 0).mean(), cagr=(p[-1] ** (365.25 / days) - 1) * 100, mdd=mdd)


def fmt(x):
    if x["n"] == 0:
        return "0건"
    return f"{x['n']}건 승률{x['win']:.0%} 거래당{x['ev'] * 100:+.3f}% 연{x['cagr']:+.1f}% MDD{x['mdd'] * 100:.0f}%"


class DS:
    """데이터 하나 — 박스별 특징을 한 번만 계산."""
    def __init__(self, c):
        self.c = c
        volok = (c.volume < 3 * c.volume.rolling(288).median().shift(1)).values
        self.f = {b: feats(c, b, volok) for b in (288, 144)}

    def run(self, g, lo, hi, model="M"):
        d, entry, box, fr, kk, hold = g
        f = self.f[box]
        tr = sim(self.c, f, signals(f, d, entry), lo, hi, fr, kk, hold, model)
        days = (min(hi, self.c.index[-1]) - max(lo, self.c.index[0])).days
        return summ(tr, days), tr


def gname(g):
    return f"{g[0]}·{g[1]}·박스{g[2] // 12}h·익절{g[3]}·손절{g[4]}·보유{g[5] // 12}h"


if __name__ == "__main__":
    T = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731
    lines = []
    P = lambda *a: (print(*a, flush=True), lines.append(" ".join(map(str, a))))  # noqa: E731
    P(__doc__.split("\n")[0])
    bmx = DS(F.candles().loc["2018-01-01":"2022-01-10"])
    bnb = DS(pd.read_pickle(F.ROOT / "data_cache" / "binance_btcusdt_5m_2020.pkl").asfreq("5min").ffill())
    okx = DS(okx_5m())
    IS = [("BitMEX 18-21", bmx, T("2018-03-05"), T("2022-01-01")), ("바이낸스 20-22", bnb, T("2020-03-01"), T("2023-01-01"))]
    OOS = [("OKX 23-26", okx, T("2023-01-01"), T("2026-10-01")), ("바이낸스 23-26", bnb, T("2023-01-01"), T("2026-10-01"))]

    rows = []
    for g in GRID:
        r = {"g": g}
        for nm, ds, lo, hi in IS + OOS:
            r[nm] = ds.run(g, lo, hi)[0]
        rows.append(r)
    df = pd.DataFrame([{"g": r["g"], **{f"{nm}|{k}": r[nm][k] for nm, *_ in IS + OOS for k in ("n", "ev", "mdd")}} for r in rows])
    ok_n = (df["BitMEX 18-21|n"] >= 50) & (df["바이낸스 20-22|n"] >= 50)
    df["score"] = np.where(ok_n, np.minimum(df["BitMEX 18-21|ev"], df["바이낸스 20-22|ev"]), np.nan)

    P("\n[1] 격자 216개 — 돈 버는 조합 비율 (메이커 체결)")
    isp = (df["BitMEX 18-21|ev"] > 0) & (df["바이낸스 20-22|ev"] > 0)
    for nm, *_ in OOS:
        P(f"  IS 둘 다 양수 {isp.mean():.0%} · {nm} 양수 {(df[f'{nm}|ev'] > 0).mean():.0%} · IS·{nm} 모두 양수 {(isp & (df[f'{nm}|ev'] > 0)).mean():.0%}"
          f" · IS점수↔{nm} 순위상관 {df['score'].corr(df[f'{nm}|ev'], method='spearman'):+.2f}")
    P("  나눠 보기 (OOS OKX 거래당 평균 · 양수 비율):")
    for i_, lab in ((0, "방향"), (1, "진입"), (2, "박스"), (3, "익절"), (4, "손절"), (5, "보유")):
        grp = df.groupby(df.g.map(lambda g: g[i_]))
        P(f"    {lab}: " + " · ".join(f"{k}: {v['OKX 23-26|ev'].mean() * 100:+.3f}% ({(v['OKX 23-26|ev'] > 0).mean():.0%})" for k, v in grp))

    P("\n[2] IS 상위 5개 → 확인")
    top = df[df.score > 0].sort_values("score", ascending=False).head(5)
    if top.empty:
        P("  IS 점수 > 0 인 조합 없음")
    alts = {s: DS(pd.read_pickle(F.ROOT / "data_cache" / f"binance_{s.lower()}_5m_2020.pkl").asfreq("5min").ffill()) for s in ALTS} if len(top) else {}
    for _, row in top.iterrows():
        g = row.g
        P(f"  {gname(g)}  IS점수 {row.score * 100:+.3f}%")
        for nm, ds, lo, hi in IS + OOS:
            P(f"    {nm:12s} M {fmt(ds.run(g, lo, hi)[0])} | T {fmt(ds.run(g, lo, hi, 'T')[0])}")
        av = {s: alts[s].run(g, T("2023-01-01"), T("2026-10-01"))[0] for s in ALTS}
        P("    알트 23-26  " + " · ".join(f"{s[:-4]} {x['ev'] * 100:+.3f}%({x['n']})" if x["n"] else f"{s[:-4]} 0건" for s, x in av.items()))
        o1, o2 = ds_ok = (row["OKX 23-26|ev"], row["바이낸스 23-26|ev"])
        ka = sum((x["n"] > 0) and x["ev"] > 0 for x in av.values())
        passed = o1 > 0 and o2 > 0 and ka >= 4 and row["OKX 23-26|mdd"] >= -0.25
        P(f"    → OOS OKX {o1 * 100:+.3f}% · 바이낸스 {o2 * 100:+.3f}% · 알트 {ka}/6 · OKX MDD {row['OKX 23-26|mdd'] * 100:.0f}% → {'확인 통과' if passed else '불합격'}")

    P("\n[3] 펀딩 캐리 (현물 롱 + 무기한 숏) — 바이낸스 BTC 실측 펀딩, 국면은 직전 24h 박스 폭")
    fu = pd.read_pickle(F.ROOT / "data_cache" / "binance_funding.pkl")
    Rb = pd.Series(bnb.f[288]["R"], index=bnb.c.index).reindex(fu.index, method="ffill")
    reg = pd.cut(Rb, [0, 0.01, 0.03, np.inf], labels=["조용 <1%", "횡보 1~3%", "추세 ≥3%"], right=False)
    for k, v in fu.groupby(reg, observed=True):
        P(f"  {k:9s} {len(v):>5}회 평균 {v.mean() * 100:+.4f}%/8h → 연율 {v.mean() * 3 * 365 * 100:+.1f}% · 양수 {(v > 0).mean():.0%} · 음수 최저 {v.min() * 100:+.3f}%")
    P(f"  전체      연율 {fu.mean() * 3 * 365 * 100:+.1f}% · 양수 {(fu > 0).mean():.0%}")
    yr = fu.groupby(fu.index.year).sum()
    P("  계속 보유 연도별 (펀딩 합, 명목 대비): " + " · ".join(f"{y % 100:02d}:{v * 100:+.1f}%" for y, v in yr.items()) + " · 진입·청산 비용 0.2% 1회")
    P("  ※ 자본은 현물 1 + 선물 증거금이 필요하다(1배 숏이면 자본 2 → 자본 대비 수익은 절반). 거래소 파산·펀딩 음수 전환 위험은 가격 위험과 별개.")
    (HERE / "range_explore_result.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
