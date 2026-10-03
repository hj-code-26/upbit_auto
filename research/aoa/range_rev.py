"""횡보 박스 하단 지정가 롱 — 사전등록 설계서 (2026-09-28, 구현·결과 보기 전 작성). 운영 코드(strategy·autotrade)는 건드리지 않는다.

질문: 하루 폭이 좁은 횡보장에서 박스 하단을 지정가로 받아 박스 중앙에 팔면, 비용을 넘는 기대값이 남는가.
      지금 급락 매수(−2%)는 조용한 날엔 수학적으로 신호가 날 수 없다 — 그 빈 시간을 채울 수 있는지 묻는다.
배경 (research_aoa.txt): 1h 역추세 적중 55% 지만 시장가 왕복 0.14% 를 못 넘었다(7절) · D≤1% 거의 전부 음수(8절)
      · RSI5m 20~40 은 8/8 양수, 20 미만 투매는 최악(14절) · 거래량 폭발은 반복되고 이탈 전조(research_flow)
      · 4h 극단은 반전이 아니라 지속(research_reversion) · 숏은 모든 연구에서 음수 → 롱만.
      새로 거는 변수는 하나 — **체결을 메이커(지정가)로 바꿔 비용을 편도 0.07% → 0.02% 로 낮추는 것.**

규칙 (고정 — 아래 값 말고는 보지 않는다. 5분봉 t 가 닫힌 순간 판정, 박스는 t 를 뺀 직전 288봉)
  박스      : H, L = 직전 288봉(24h) 고가 최대 · 저가 최소, R = (H − L) / L
  횡보      : 1.0% ≤ R < 3.0%          (1% 미만은 익절 폭이 비용 대비 너무 작다)
  국면      : 전날 일봉 종가 > 50일선 & 200일선 (strategy.indicators 의 bull 그대로)
  위치      : 0 ≤ (close_t − L) / (H − L) ≤ 0.15      (L 아래 = 이미 이탈 → 진입 안 함)
  RSI       : 5분 RSI(14, Wilder) 20 ≤ RSI ≤ 40
  거래량    : volume_t < 3 × 직전 288봉 거래량 중앙값
  주문      : 지정가 매수 P = close_t × (1 − 0.10%), 유효 t+1 ~ t+12 (1시간). 저가 < P 인 첫 봉에서 P 로 체결(닿기만 하면 미체결).
              미체결이면 취소, 다음 신호에서 다시 건다. 보유 중이거나 주문이 걸려 있으면 새 신호 무시.
  익절      : 지정가 매도 TP = L + 0.5 × (H − L)  (신호 시점 박스로 고정). 고가 > TP 인 봉에서 TP 로 체결 (메이커).
  손절      : SL = L × (1 − 1.0%). 저가 ≤ SL 인 봉에서 min(SL, 시가) 로 체결 (테이커).
  만기      : 체결 후 288봉(24h) → 다음 봉 시가 시장가 (테이커).
  체결 봉   : 체결된 봉에서 저가 ≤ SL 이면 손절로 본다. 익절은 체결 다음 봉부터. 한 봉에 익절·손절 둘 다 → 손절.
비용: 메이커 편도 0.02% (OKX 스왑 기본 등급) · 테이커 편도 0.07% (레포 표준 = 0.05 + 미끄러짐 0.02) · 펀딩 0.01%/8h · 1배.

체결 모델
  M = 위 규칙 그대로 (진입·익절 메이커, 손절·만기 테이커)                           ← 주 판정
  T = 전부 테이커: 신호 다음 봉 시가 시장가 진입, 익절은 TP 를 넘은 봉의 다음 봉 시가 (편도 0.07%) ← 비용 스트레스
기준선 (결정적, 무작위 표집 아님)
  Z = 횡보·국면 조건만 만족하는 모든 신호 봉에서 같은 주문·청산 (위치·RSI·거래량 조건 제거)  → '박스 하단 고르기' 의 몫

데이터 (tp_sweep.py 와 같은 구간 · 일봉 예열)
  BTC 3종: BitMEX 2018-03~2021-12 · 바이낸스 2020-03~2026-09 · OKX 2021-05~2026-09 (바이낸스·OKX 는 겹쳐서 독립 아님)
  알트 6종: 바이낸스 현물 5분봉 2020-01~2026-09 ETH·SOL·XRP·BNB·DOGE·ADA (첫 200일은 국면 필터가 꺼져 거래 없음)
  파라미터 탐색 없음. 단 RSI 20~40 은 BTC 로 본 14절에서 왔으므로 BTC 는 완전한 표본 밖이 아니다 → ④ 알트를 둔다.

채택 기준 (모델 M, 여섯 개 다 통과해야 '모의 운용 후보')
  ① BTC 3종 모두 거래당 순수익 > 0
  ② BTC 3종 모두 거래당 순수익 > 기준선 Z
  ③ 모델 T 에서도 BTC 3종 중 2종 이상 거래당 > 0     — 메이커 가정 하나에만 기대는 우위인지
  ④ 알트 6종 중 4종 이상 거래당 > 0
  ⑤ BTC 3종 모두 1배 MDD ≥ −25% (보유 중 저가 평가손 포함, exec_model.curve 와 같은 식)
  ⑥ OKX 거래 수 ≥ 30
  통과해도 운영 반영은 사용자 결정. 급락 매수 봇 코드는 안 바꾼다.

보고만 (판정에 안 씀 — 더 좋아 보여도 갈아타지 않는다. 필요하면 별도 사전등록)
  · 건수 · 며칠에 1번 · 연도별 건수 · 승률 · 익절/손절/만기/미체결 취소 수 · 상위 3거래 제외 거래당 · 1배·2배 연복리/MDD · 2026 성적
  · 변형: V1 국면 필터 끔 · V2 RSI 조건 제거 · V3 거래량 조건 제거 · V4 박스 48봉(4h)
  · 급락 매수(strategy.py 현행, B2)와 같이 돌릴 때: 동시 보유 일수 · 거래일 수익 상관 · 자본 절반씩 합산 곡선의 연복리/MDD

사전 예측 (틀려도 기록): ① 은 통과할 수 있으나 ③ 에서 떨어질 것 — 우위가 있다면 대부분 비용 차이에서 나온다.
  또 체결이 '뚫고 내려갈 때만' 일어나므로 역선택으로 손절 비율이 기준선 Z 보다 높을 것.
한계: 5분 OHLC 로는 지정가 대기열 순서를 모른다 — '뚫어야 체결' 은 보수적이지만, 뚫고 바로 튀는 체결이 실제로는 더 적게 잡힐 수 있다.
      봉 안 순서(체결 → 손절 → 익절)를 모르는 부분은 전부 불리하게 잡았다. 알트는 현물 데이터라 펀딩은 같은 고정률.
사용: python research/aoa/range_rev.py   (결과 → research/aoa/range_rev_result.txt)   ※ 구현 전 — 이 설계서를 커밋한 뒤 작성한다.
"""
import pathlib
import sys

import numpy as np
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[1]))
import exec_model as M  # noqa: E402
import features as F  # noqa: E402
import strategy as S  # noqa: E402
from clone import okx_5m  # noqa: E402

MAKER, TAKER, FUND = 0.0002, 0.0007, 0.0001
HOLD, WAIT = 288, 12
ALTS = ["ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT", "ADAUSDT"]


def feats(c, d1, box=288, volok=None):
    H = c.high.rolling(box).max().shift(1); L = c.low.rolling(box).min().shift(1)
    R = (H - L) / L
    d = c.close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-d).clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    if volok is None:
        volok = (c.volume < 3 * c.volume.rolling(288).median().shift(1)).values
    return dict(H=H.values, L=L.values, R=R.values, side=((R >= 0.01) & (R < 0.03)).values,
                bull=S.indicators(c, d1)[1].values, pos=((c.close - L) / (H - L)).values,
                rsi=(100 - 100 / (1 + up / dn)).values, vol=volok)


def sigs(f, bull=True, rsi=True, vol=True, low=True):
    s = f["side"].copy()
    if low: s &= (f["pos"] >= 0) & (f["pos"] <= 0.15)
    if bull: s &= f["bull"]
    if rsi: s &= (f["rsi"] >= 20) & (f["rsi"] <= 40)
    if vol: s &= f["vol"]
    return s


def sim(c, f, sig, lo, hi, model="M"):
    """→ (거래 DataFrame t0·t1·why·r·mae, 미체결 취소 수). 규칙은 머리말 그대로."""
    o, h, l, cl, idx = c.open.values, c.high.values, c.low.values, c.close.values, c.index
    H, L = f["H"], f["L"]
    n, i, i1 = len(o), idx.searchsorted(lo), idx.searchsorted(hi)
    cand, out, cancel = np.flatnonzero(sig), [], 0
    while True:
        p = np.searchsorted(cand, i)
        if p >= len(cand):
            break
        k = cand[p]
        if k >= i1 or k + WAIT + HOLD + 3 >= n:
            break
        tp, sl = L[k] + 0.5 * (H[k] - L[k]), L[k] * 0.99
        if model == "M":
            P = cl[k] * 0.999
            hit = np.flatnonzero(l[k + 1:k + 1 + WAIT] < P)
            if not len(hit):
                cancel += 1; i = k + WAIT + 1; continue
            e = k + 1 + hit[0]; entry, c_in = P, MAKER
        else:
            e = k + 1; entry, c_in = o[e], TAKER
        if l[e] <= sl:                                     # 체결 봉 손절
            j, why, px, xb = e, "손절", min(sl, o[e]), e
        else:
            a = np.flatnonzero(l[e + 1:e + HOLD + 1] <= sl); b = np.flatnonzero(h[e + 1:e + HOLD + 1] > tp)
            ja, jb = (a[0] if len(a) else HOLD), (b[0] if len(b) else HOLD)
            if ja == HOLD and jb == HOLD:
                j = xb = e + HOLD + 1; why, px = "만기", o[j]
            elif ja <= jb:                                  # 한 봉에 둘 다 → 손절
                j = xb = e + 1 + ja; why, px = "손절", min(sl, o[j])
            else:
                j = e + 1 + jb; why = "익절"
                px, xb = (tp, j) if model == "M" else (o[j + 1], j + 1)
        c_out = MAKER if (why == "익절" and model == "M") else TAKER
        r = px / entry - 1 - c_in - c_out - FUND * (xb - e) / 96
        seen = l[e:xb] if why == "만기" else l[e:j + 1]
        out.append(dict(t0=idx[e], t1=idx[xb], why=why, r=r, mae=min(seen.min() / entry - 1, px / entry - 1)))
        i = xb if why == "만기" else xb + 1
    return pd.DataFrame(out, columns=["t0", "t1", "why", "r", "mae"]), cancel


def stats(tr, days, lev=1):
    if tr.empty:
        return dict(n=0, ev=np.nan, win=np.nan, cagr=0.0, mdd=0.0, top=np.nan)
    r = tr.r * lev
    p, mdd, _, _ = M.curve(tr.assign(mae=tr.mae * lev), r.reset_index(drop=True))
    return dict(n=len(tr), ev=r.mean(), win=(r > 0).mean(), cagr=(p[-1] ** (365.25 / days) - 1) * 100, mdd=mdd,
                top=r.sort_values().iloc[:-3].mean() if len(r) > 3 else np.nan)


def line(tag, tr, cancel, days):
    s1, s2 = stats(tr, days), stats(tr, days, 2)
    why = tr.why.value_counts() if len(tr) else {}
    ys = " ".join(f"{y % 100:02d}:{k}" for y, k in tr.t0.dt.year.value_counts().sort_index().items()) if len(tr) else ""
    return (f"  {tag:10s} {s1['n']:>4}건 ({days / max(s1['n'], 1):4.1f}일에 1번) 승률 {s1['win']:.0%} 거래당 {s1['ev'] * 100:+.3f}% "
            f"상위3제외 {s1['top'] * 100:+.3f}% 익/손/만/취소 {why.get('익절', 0)}/{why.get('손절', 0)}/{why.get('만기', 0)}/{cancel} | "
            f"1배 연{s1['cagr']:+.1f}% MDD{s1['mdd'] * 100:.0f}% · 2배 연{s2['cagr']:+.1f}% MDD{s2['mdd'] * 100:.0f}% | {ys}"), s1


def mask(idx, tr):
    m = np.zeros(len(idx), bool)
    for a, b in zip(idx.searchsorted(tr.t0), idx.searchsorted(tr.t1)):
        m[a:b] = True
    return m


def combo(c, d1, lo, hi, tr, days):
    """급락 매수(B2) 와 같이 돌릴 때 — 동시 보유 · 일별 손익 상관 · 자본 절반씩 합산(재조정 없음, 일말 평가)."""
    dp = M.trades(c, d1, lo, hi, skip=1)
    rd = pd.Series(np.asarray(M.net(dp, "B", 0.0, "lo", 0.0007, FUND), float))
    both = (mask(c.index, tr) & mask(c.index, dp)).sum() * 5 / 1440
    days_ix = pd.date_range(lo.normalize(), hi, freq="1D", tz="UTC")
    pa = pd.Series(tr.r.values, index=tr.t1.dt.floor("D")).groupby(level=0).sum().reindex(days_ix, fill_value=0)
    pb = pd.Series(rd.values, index=dp.t1.dt.floor("D")).groupby(level=0).sum().reindex(days_ix, fill_value=0)
    ea = (1 + pd.Series(tr.r.values, index=tr.t1)).cumprod().resample("1D").last().reindex(days_ix).ffill().fillna(1)
    eb = (1 + pd.Series(rd.values, index=dp.t1)).cumprod().resample("1D").last().reindex(days_ix).ffill().fillna(1)
    eq = 0.5 * ea + 0.5 * eb
    f = lambda e: f"연{(e.iloc[-1] ** (365.25 / days) - 1) * 100:+.1f}% MDD{(e / e.cummax() - 1).min() * 100:.0f}%"  # noqa: E731
    return f"  급락매수와 같이: 동시 보유 {both:.1f}일 · 일별 손익 상관 {pa.corr(pb):+.2f} · 횡보만 {f(ea)} · 급락만 {f(eb)} · 반반 {f(eq)}"


def share(c, f, lo, hi):
    """횡보 비중 — 5분봉 기준(직전 24h 박스 폭) + 일봉 기준(그날 고저폭)."""
    sel = (c.index >= lo) & (c.index < hi) & ~np.isnan(f["R"])
    R = f["R"][sel]
    dd = c[(c.index >= lo) & (c.index < hi)].resample("1D").agg({"high": "max", "low": "min"})
    dr = (dd.high - dd.low) / dd.low
    yr = pd.Series(f["side"][sel], index=c.index[sel]).groupby(c.index[sel].year).mean()
    return (f"  횡보 비중: 24h 박스 폭 <1% {np.mean(R < 0.01):.1%} · 1~3% {np.mean((R >= 0.01) & (R < 0.03)):.1%} · ≥3% {np.mean(R >= 0.03):.1%}"
            f" · 1~3% & 50·200일선 위 {np.mean(f['side'][sel] & f['bull'][sel]):.1%} | 하루 고저폭 <2% {np.mean(dr < 0.02):.1%} · <3% {np.mean(dr < 0.03):.1%}"
            f" | 연도별(1~3%) " + " ".join(f"{y % 100:02d}:{v:.0%}" for y, v in yr.items()))


if __name__ == "__main__":
    T = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731
    lines = []
    P = lambda *a: (print(*a, flush=True), lines.append(" ".join(map(str, a))))  # noqa: E731
    P(__doc__.split("\n")[0])
    bs = pd.read_pickle(F.ROOT / "data_cache" / "bitstamp_1d_2016.pkl").close
    btc = [("BitMEX 18-21", F.candles().loc["2018-01-01":"2022-01-10"], "2018-03-05", "2022-01-01"),
           ("바이낸스 20-26", pd.read_pickle(F.ROOT / "data_cache" / "binance_btcusdt_5m_2020.pkl").asfreq("5min").ffill(),
            "2020-03-01", "2026-10-01"),
           ("OKX 21-26", okx_5m(), "2021-05-01", "2026-10-01")]
    sets = [(nm, c, d0, d9, True) for nm, c, d0, d9 in btc]
    for sym in ALTS:
        sets.append((sym, pd.read_pickle(F.ROOT / "data_cache" / f"binance_{sym.lower()}_5m_2020.pkl").asfreq("5min").ffill(),
                     "2020-01-01", "2026-10-01", False))
    res = {}
    for name, c, d0, d9, is_btc in sets:
        own = c.close.resample("1D").last()
        d1 = pd.concat([bs[bs.index < own.index[0]], own]) if is_btc else own
        lo, hi = max(T(d0), c.index[0]), T(d9)
        days = (min(hi, c.index[-1]) - lo).days
        f = feats(c, d1)
        P(f"\n[{name}] {days / 365.25:.1f}년")
        P(share(c, f, lo, hi))
        runs = [("M 주판정", sigs(f), "M", f), ("T 시장가", sigs(f), "T", f),
                ("Z 기준선", sigs(f, rsi=False, vol=False, low=False), "M", f)]
        if is_btc:
            f4 = feats(c, d1, box=48, volok=f["vol"])
            runs += [("V1 국면끔", sigs(f, bull=False), "M", f), ("V2 RSI없음", sigs(f, rsi=False), "M", f),
                     ("V3 거래량없음", sigs(f, vol=False), "M", f), ("V4 박스4h", sigs(f4), "M", f4)]
        main = None
        for tag, sg, model, ff in runs:
            tr, cancel = sim(c, ff, sg, lo, hi, model)
            txt, s1 = line(tag, tr, cancel, days)
            P(txt)
            res[(name, tag)] = s1
            if tag == "M 주판정":
                main = tr
                t26 = tr[tr.t0.dt.year == 2026] if len(tr) else tr
                P(f"  2026: {len(t26)}건 거래당 {t26.r.mean() * 100 if len(t26) else float('nan'):+.3f}%")
        if is_btc and main is not None and len(main):
            P(combo(c, d1, lo, hi, main, days))
    names = [b[0] for b in btc]
    g = lambda nm, t, k: res[(nm, t)][k]  # noqa: E731
    c1 = all(g(n, "M 주판정", "ev") > 0 for n in names)
    c2 = all(g(n, "M 주판정", "ev") > g(n, "Z 기준선", "ev") for n in names)
    k3 = sum(g(n, "T 시장가", "ev") > 0 for n in names); c3 = k3 >= 2
    k4 = sum(g(s, "M 주판정", "ev") > 0 for s in ALTS); c4 = k4 >= 4
    c5 = all(g(n, "M 주판정", "mdd") >= -0.25 for n in names)
    c6 = g("OKX 21-26", "M 주판정", "n") >= 30
    ok = lambda b: "통과" if b else "불합격"  # noqa: E731
    P(f"\n판정: ① BTC 거래당>0 {ok(c1)} · ② >기준선 Z {ok(c2)} · ③ 시장가 {k3}/3 {ok(c3)} · ④ 알트 {k4}/6 {ok(c4)}"
      f" · ⑤ MDD≥−25% {ok(c5)} · ⑥ OKX {g('OKX 21-26', 'M 주판정', 'n')}건 {ok(c6)}")
    P("  →", "모의 운용 후보 (운영 반영은 사용자 결정)" if all([c1, c2, c3, c4, c5, c6]) else "기각")
    (HERE / "range_rev_result.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
