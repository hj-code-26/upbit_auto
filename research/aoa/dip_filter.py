"""급락 매수 + 예측기 반대 필터 — 사전등록 (2026-09-29, 결과 보기 전 작성·커밋). 운영 코드(strategy·autotrade)는 건드리지 않는다.

근거: aoa_forecast.py (사후) — 모델이 강하게 반대할 때 들어간 워뇨띠 거래가 BitMEX·바이낸스 두 쪽 모두 성적이 가장 나빴다(표본 작음).
질문: 급락 매수 신호가 떠도, 예측기가 '4시간 뒤 상승 확률 ≤ 40%' 라고 보면 건너뛰면 나아지는가.

규칙: 현행 급락 매수(strategy.py: 5분 종가 1h EMA −2% & 전날 종가 50·200일선 위 → 롱, TP4/SL6/3일) 에서
      신호 봉 종가 시점의 예측기 P(4시간 뒤 상승) ≤ 0.40 이면 그 신호를 없는 것으로 친다(봇은 계속 대기 → 다음 신호를 볼 수 있다).
예측기: forecast.py 기본 16개 특징 · 같은 모델(HistGradientBoosting, 같은 설정). 과열·ETH 는 BitMEX 2018~19 에 없어서 안 쓴다.
  학습 데이터 = 그 자산의 바이낸스 5분봉(BTC 는 BTCUSDT, 알트는 각 알트) 매시 표본. 시험 연도 Y 의 신호는
    Y ≥ 2022 → Y 이전만 학습(예측 기간 4h 만큼 간격) · Y ≤ 2021 → 2022-01 이후만 학습(그 기간을 안 본 모델)
  특징은 거래 데이터 자체의 5분봉으로 계산(BitMEX 거래는 BitMEX 봉, OKX 거래는 OKX 봉).
체결: exec_model B2 (현행 실봇 — 진입 봉 건너뛰기 · 봉 마감 시장가) · 비용 편도 0.07% · 펀딩 0.01%/8h · 1배. 보조로 A(성적표).
데이터: tp_sweep.py 와 같음 — BTC 3종(BitMEX 18-21 · 바이낸스 20-26 · OKX 21-26) · 알트 6종(바이낸스 현물 20-26).

채택 기준 (넷 다 통과해야 '모의 운용 후보', 반영은 사용자 결정):
  ① BTC 3종 모두 B2 거래당 순수익(필터) > 기준(필터 없음)
  ② BTC 3종 모두 B2 MDD(필터) 가 기준보다 2%p 넘게 깊지 않다
  ③ 알트 6종 중 4종 이상 B2 거래당 개선
  ④ BTC 3종 합산으로, 기준 거래 중 필터가 막은 거래의 평균 < 기준 거래 전체 평균  (나쁜 거래를 골라 막았는가)
보고만: 거래 수·승률·연복리·MDD · 막힌 거래 수와 평균 · 무작위로 같은 수를 뺄 때 대비 백분위(2,000회, 기준 거래 목록 위 근사)
        · 변형(판정 안 씀): 1h ≤0.40 · 24h ≤0.40 · 4h ≤0.45 · 신호 봉에서 P(4h) 분포
한계: 거래 수가 적다(OKX 32건). 필터는 거래를 줄이므로 거래당이 좋아져도 연복리는 줄 수 있다.
사용: python research/aoa/dip_filter.py   (결과 → research/aoa/dip_filter_result.txt)
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
RULES = [("주판정 4h≤0.40", "4시간", 0.40), ("변형 1h≤0.40", "1시간", 0.40), ("변형 24h≤0.40", "24시간", 0.40), ("변형 4h≤0.45", "4시간", 0.45)]
ORIG = S.indicators


def probs(train_c, train_d1, c, d1, cand, n):
    """cand(신호 봉 시각)마다 P(n봉 뒤 상승) — 연도별로 그 기간을 안 본 모델."""
    X, y, _ = FC.dataset(train_c, train_d1, n)
    ft = FC.features(c, d1).reindex(cand)
    p = pd.Series(np.nan, index=cand)
    yrs = cand.year
    folds = [("early", yrs <= 2021, X.index >= T("2022-01-01"))]
    folds += [(yr, yrs == yr, X.index < T(f"{yr}-01-01") - pd.Timedelta(minutes=5 * n)) for yr in range(2022, 2027)]
    for _, te, tr in folds:
        if te.sum() == 0 or tr.sum() < 5000:
            continue
        clf = FC.HGC(**FC.KW).fit(X[tr], (y[tr] > 0).astype(int))
        ok = te & ft.notna().all(axis=1).values
        if ok.sum():
            p[ok] = clf.predict_proba(ft[ok])[:, 1]
    return p


def run(c, d1, lo, hi, skip=None, model="B"):
    if skip is not None:
        S.indicators = lambda c5, dd: (lambda dv, bl: (dv, bl & ~skip.reindex(c5.index, fill_value=False)))(*ORIG(c5, dd))
    try:
        tr = M.trades(c, d1, lo, hi, skip=1 if model == "B" else 0)
    finally:
        S.indicators = ORIG
    r = pd.Series(np.asarray(M.net(tr, model, 0.0, "lo", COST, FUND), float), index=tr.index)
    return tr, r


def summ(tr, r, yrs):
    if len(tr) == 0:
        return dict(n=0, ev=np.nan, win=np.nan, mdd=0.0, cagr=0.0)
    p, mdd, _, _ = M.curve(tr, r)
    return dict(n=len(tr), ev=r.mean(), win=(r > 0).mean(), mdd=mdd, cagr=(p[-1] ** (1 / yrs) - 1) * 100)


def fmt(s):
    return f"{s['n']:>3}건 승률 {s['win']:.0%} 거래당 {s['ev'] * 100:+.2f}% 연 {s['cagr']:+.1f}% MDD {s['mdd'] * 100:.0f}%" if s["n"] else "  0건"


if __name__ == "__main__":
    lines = []
    P = lambda *a: (print(*a, flush=True), lines.append(" ".join(map(str, a))))  # noqa: E731
    P(__doc__.split("\n")[0])
    bs = pd.read_pickle(F.ROOT / "data_cache" / "bitstamp_1d_2016.pkl").close
    bc, bd = FC.history()                                     # BTC 학습용 (바이낸스)
    sets = [("BitMEX 18-21", F.candles().loc["2017-12-01":"2022-01-10"], "2018-03-05", "2022-01-01", True),
            ("바이낸스 20-26", bc, "2020-03-01", "2026-10-01", True), ("OKX 21-26", okx_5m(), "2021-05-01", "2026-10-01", True)]
    for sym in ALTS:
        sets.append((sym, pd.read_pickle(F.ROOT / "data_cache" / f"binance_{sym.lower()}_5m_2020.pkl").asfreq("5min").ffill(),
                     "2020-01-01", "2026-10-01", False))
    res, pooled = {}, {"base": [], "blocked": []}
    rng = np.random.default_rng(0)
    for name, c, d0, d9, is_btc in sets:
        own = c.close.resample("1D").last()
        d1 = pd.concat([bs[bs.index < own.index[0]], own]) if is_btc else own
        lo, hi = max(T(d0), c.index[0]), T(d9)
        yrs = (min(hi, c.index[-1]) - lo).days / 365.25
        tc, td = (bc, bd) if is_btc else (c, own)
        dev, bull = ORIG(c, d1)
        cand = c.index[((dev <= -S.D) & bull).values & (c.index >= lo) & (c.index < hi)]
        pp = {h: probs(tc, td, c, d1, cand, FC.HZ[h]) for h in ("1시간", "4시간", "24시간")}
        trb, rb = run(c, d1, lo, hi)
        base = summ(trb, rb, yrs)
        P(f"\n[{name}] 신호 봉 {len(cand)}개 · P(4h) 중앙 {pp['4시간'].median():.2f} · ≤0.40 비율 {(pp['4시간'] <= 0.40).mean():.0%} · 예측 없음 {pp['4시간'].isna().sum()}")
        P(f"  기준 B2   {fmt(base)}")
        for lab, h, th in RULES:
            skip = (pp[h] <= th).reindex(c.index, fill_value=False)
            tr, r = run(c, d1, lo, hi, skip)
            s = summ(tr, r, yrs)
            blocked = skip.reindex(trb.t0 - S.BAR, fill_value=False).values   # 기준 거래 중 신호 봉이 막힌 것
            rb_bl = rb[blocked]
            extra = ""
            if blocked.sum() and blocked.sum() < len(rb):
                sims = np.array([np.delete(rb.values, rng.choice(len(rb), blocked.sum(), replace=False)).mean() for _ in range(2000)])
                extra = f" · 막힌 기준 거래 {blocked.sum()}건 평균 {rb_bl.mean() * 100:+.2f}% · 무작위 대비 백분위 {(sims < rb[~blocked].mean()).mean():.0%}"
            P(f"  {lab:12s} {fmt(s)}{extra}")
            if lab.startswith("주판정"):
                ta, ra = run(c, d1, lo, hi, skip, "A")
                P(f"  {'(A 성적표)':12s} 기준 {fmt(summ(*run(c, d1, lo, hi, None, 'A'), yrs))} → 필터 {fmt(summ(ta, ra, yrs))}")
                res[name] = (base, s)
                if is_btc:
                    pooled["base"] += list(rb.values); pooled["blocked"] += list(rb_bl.values)
    btc = [s[0] for s in sets if s[4]]
    c1 = all(res[n][1]["ev"] > res[n][0]["ev"] for n in btc)
    c2 = all(res[n][1]["mdd"] >= res[n][0]["mdd"] - 0.02 for n in btc)
    k3 = sum((res[s][1]["n"] > 0) and res[s][1]["ev"] > res[s][0]["ev"] for s in ALTS); c3 = k3 >= 4
    mb, mall = (np.mean(pooled["blocked"]) if pooled["blocked"] else np.nan), np.mean(pooled["base"])
    c4 = bool(pooled["blocked"]) and mb < mall
    ok = lambda b: "통과" if b else "불합격"  # noqa: E731
    P(f"\n판정 (4h ≤ 0.40): ① BTC 거래당 개선 {ok(c1)} · ② MDD {ok(c2)} · ③ 알트 {k3}/6 {ok(c3)}"
      f" · ④ 막힌 거래 {len(pooled['blocked'])}건 평균 {mb * 100:+.2f}% vs 전체 {mall * 100:+.2f}% {ok(c4)}")
    P("  →", "모의 운용 후보 (반영은 사용자 결정)" if all([c1, c2, c3, c4]) else "기각 — 현행 유지")
    (HERE / "dip_filter_result.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
