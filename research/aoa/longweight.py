"""롱 비중 높이기 — 사전등록 (2026-09-28, 결과 보기 전 작성).

근거: aoa 에피소드(aoa_episodes.parquet) 방향별 — 2019~21 롱 거래당 +0.31/+0.47/+1.28% vs 숏 +0.27/+0.01/+0.16%,
      레버리지×수익 합은 롱 3년 모두 +, 숏 3년 모두 −. 그는 숏에 시간을 두 배 넘게 썼지만(13,064h vs 6,011h) 이익은 롱에서 났다.
두 질문:
  A. 그처럼 양방향을 하되 롱을 무겁게: 지금 봇(롱 1배) + 급등 역추세 숏(dev ≥ +2%, TP4/SL6/3일, 필터 없음) w 배.
     w ∈ {0.25, 0.5}. 롱·숏은 서로 독립으로 동시에 들 수 있다.
     채택: ① OKX 2022-01~2026-09 에서 rr(연복리÷|MDD|) > 롱만  ② 알트 6종(2020-08-15~) 중 4종 이상 rr > 롱만.
  B. 숏 없이 롱만 1.5배 (research_aoa 남은 후보). 채택 기준은 2배 때(dip_lev2.py)와 같게 고정:
     ① BTC 3종(BitMEX 18-21 · 바이낸스 20-26 · OKX 21-26) 모두 MDD ≥ −40%  ② 3종 모두 연복리 > 1배
     ③ 가장 나쁜 데이터의 켈리 배율(거래당 순수익 평균 ÷ 분산) ≥ 1.5
계산: 5분봉마다 평가하는 일정 비중 장부 — 자본 × (1 + Σ 비중 × 그 봉 수익). 진입가 = 다음 봉 시가, 청산가 = 익절/손절가(갭이면 시가).
      비용 편도 0.07% · 롱 펀딩 0.01%/8h · 숏 펀딩 0 (숏에 유리한 가정 안 넣음). MDD 는 5분 종가 기준(봉 안 저가는 안 봄 — 두 변형에 똑같이).
"""
import pathlib
import sys

import numpy as np
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[1]))
import features as F  # noqa: E402
import strategy as S  # noqa: E402
from clone import okx_5m  # noqa: E402
from fail_rules import prep  # noqa: E402
from sma50 import ALTS  # noqa: E402

COST, FUND_BAR = 0.0007, 0.0001 / 96


def stream(p, cl, trig, side, lo, hi):
    """신호 → 5분봉별 수익 배열(1배 기준, 비용·펀딩 포함) + 거래별 순수익 목록."""
    o, h, l, idx = p["o"], p["h"], p["l"], p["idx"]
    R, rets = np.zeros(len(o)), []
    i, i1 = max(idx.searchsorted(lo), 300), idx.searchsorted(hi)
    while i < i1 - 1:
        if not trig[i]:
            i += 1; continue
        k = i + 1; e = o[k]
        up, dn = (e * (1 + S.TP / 100), e * (1 - S.SL / 100)) if side == 1 else (e * (1 - S.TP / 100), e * (1 + S.SL / 100))
        end, px, j = min(k + S.MAXB, len(o) - 1), None, k
        for j in range(k, end):
            if (l[j] <= dn) if side == 1 else (h[j] >= dn):
                px = min(dn, o[j]) if side == 1 else max(dn, o[j]); break
            if (h[j] >= up) if side == 1 else (l[j] <= up):
                px = up; break
        if px is None:
            j, px = end, o[end]
        prev = np.concatenate([[e], cl[k:j]])
        last = cl[k:j + 1].copy(); last[-1] = px
        R[k:j + 1] += side * (last / prev - 1) - (FUND_BAR if side == 1 else 0)
        R[k] -= COST; R[j] -= COST
        rets.append(side * (px / e - 1) - 2 * COST)
        i = j + 1
    return R, np.array(rets)


def perf(R, idx, lo, hi):
    m = (idx >= lo) & (idx < hi)
    eq = np.cumprod(1 + R[m])
    mdd = (eq / np.maximum.accumulate(eq) - 1).min()
    yrs = (min(hi, idx[-1]) - lo).days / 365.25
    cagr = (eq[-1] ** (1 / yrs) - 1) * 100
    return cagr, mdd * 100, cagr / abs(mdd * 100)


if __name__ == "__main__":
    T = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731
    bs = pd.read_pickle(F.ROOT / "data_cache" / "bitstamp_1d_2016.pkl").close
    rd = lambda s: pd.read_pickle(F.ROOT / "data_cache" / f"binance_{s.lower()}_5m_2020.pkl").asfreq("5min").ffill()  # noqa: E731
    sets = {"BitMEX 18-21": (F.candles().loc["2018-01-01":"2022-01-10"], bs, "2018-03-05", "2022-01-01"),
            "바이낸스 20-26": (rd("BTCUSDT"), bs, "2020-03-01", "2026-10-01"),
            "OKX 21-26": (okx_5m(), bs, "2021-05-01", "2026-10-01"),
            "OKX 22-26": (None, None, "2022-01-01", "2026-10-01")}
    for s in ALTS:
        sets[s[:-4]] = (rd(s), None, "2020-08-15", "2026-10-01")
    alts = [s[:-4] for s in ALTS]
    res, cache = {}, {}
    for name, (c, ld, lo, hi) in sets.items():
        if c is None:
            c, ld = sets["OKX 21-26"][0], bs
        key = id(c)
        if key not in cache:
            p = prep(c, ld)
            cl = c.close.values
            RL, _ = stream(p, cl, (p["dev"] <= -S.D) & p["bull"], 1, T("2018-01-01"), T("2027-01-01"))
            RS, _ = stream(p, cl, p["dev"] >= S.D, -1, T("2018-01-01"), T("2027-01-01"))
            cache[key] = (p["idx"], RL, RS)
        idx, RL, RS = cache[key]
        lo_, hi_ = T(lo), T(hi)
        for v, R in {"롱 1배": RL, "롱1+숏0.25": RL + 0.25 * RS, "롱1+숏0.5": RL + 0.5 * RS, "숏만 1배": RS,
                     "롱 1.5배": 1.5 * RL}.items():
            res[(name, v)] = perf(R, idx, lo_, hi_)
        print("done", name, flush=True)

    # 켈리: 기간 안 거래별 순수익 (롱 1배 규칙)
    def kelly(name):
        c, ld, lo, hi = sets[name]
        p = prep(c, ld)
        _, r = stream(p, c.close.values, (p["dev"] <= -S.D) & p["bull"], 1, T(lo), T(hi))
        return len(r), r.mean() / r.var()

    pd.set_option("display.width", 250)
    V = ["롱 1배", "롱1+숏0.25", "롱1+숏0.5", "숏만 1배", "롱 1.5배"]
    tab = pd.DataFrame({v: {n: f"연{res[(n, v)][0]:+.1f}% MDD{res[(n, v)][1]:.0f}% rr{res[(n, v)][2]:.2f}" for n in sets} for v in V})
    print(tab.to_string())

    print("\n[A] 양방향 · 롱 무겁게")
    for v in ("롱1+숏0.25", "롱1+숏0.5"):
        c1 = res[("OKX 22-26", v)][2] > res[("OKX 22-26", "롱 1배")][2]
        k = sum(res[(a, v)][2] > res[(a, "롱 1배")][2] for a in alts)
        print(f"  {v}: ① OKX 22-26 rr {res[('OKX 22-26', v)][2]:.2f} vs {res[('OKX 22-26', '롱 1배')][2]:.2f} {'통과' if c1 else '불합격'}"
              f" · ② 알트 {k}/6 {'통과' if k >= 4 else '불합격'} → {'채택' if c1 and k >= 4 else '기각'}")
    print("\n[B] 롱 1.5배")
    btc = ["BitMEX 18-21", "바이낸스 20-26", "OKX 21-26"]
    ks = {n: kelly(n) for n in btc}
    c1 = all(res[(n, "롱 1.5배")][1] >= -40 for n in btc)
    c2 = all(res[(n, "롱 1.5배")][0] > res[(n, "롱 1배")][0] for n in btc)
    c3 = min(k for _, k in ks.values()) >= 1.5
    print("  켈리:", " · ".join(f"{n} {m}건 {k:.1f}" for n, (m, k) in ks.items()))
    mdds = ", ".join(f"{res[(n, '롱 1.5배')][1]:.0f}%" for n in btc)
    print(f"  ① MDD≥−40% {'통과' if c1 else '불합격'} ({mdds})"
          f" · ② 연복리>1배 {'통과' if c2 else '불합격'} · ③ 켈리≥1.5 {'통과' if c3 else '불합격'} → {'채택' if c1 and c2 and c3 else '기각'}")
