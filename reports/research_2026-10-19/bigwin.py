"""연구: 크게 이득 본 거래(큰 승리)는 어떤 장에서, 어떤 타이밍에 나왔나. 리플레이 전용, 운용 코드 아님.
현행 규칙(C3 + 동적 크기 + 좁은 봉, 30x) 을 코인마다 따로 3몫(0·8·16) 리플레이 → 거래별 신호 봉 특징 + 결과.
R = 가격 수익 / 손절 거리 (손절 1번 = −1R). 큰 승리 = R ≥ 5. BTC·ETH (규칙을 고른 코인) + SOL·XRP (안 본 코인).
IS 2020-04~2023-06 / OOS 2023-07~ 로 나눠 같은 방향인지 본다. → bigwin.csv (거래별), 표는 stdout"""
import pathlib, sys, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path[:0] = [str(HERE.parents[1]), str(HERE.parent / "research_2026-10-18")]
import numpy as np, pandas as pd
import backtest as bt, rls
from longhold import feats

COINS = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP"]
OFF = (0, 8, 16)
SPLIT = pd.Timestamp("2023-07-01")


def extra(x, btc):
    """추가 특징 (신호 봉 종가에 아는 값)."""
    f = pd.DataFrame(index=x.index)
    atrp = x.atr / x.close
    f["comp"] = atrp / atrp.rolling(60).median()                              # 변동성 압축 (<1 = 평소보다 조용)
    f["box"] = (x.high.rolling(20).max() - x.low.rolling(20).min()) / x.atr   # 직전 20일 폭 (ATR 배수, 작을수록 박스)
    f["pos20"] = (x.close - x.low.rolling(20).min()) / (x.high.rolling(20).max() - x.low.rolling(20).min())
    f["w"] = (x.high - x.low) / x.atr                                         # 신호 봉 폭
    s50 = x.close.rolling(rls.SMA).mean()
    b = np.sign(x.close - s50).where(x.conf == 0, 0)                          # B 상태 (확정 전, SMA50 쪽)
    f["b_up"] = b.rolling(5).apply(lambda v: (v[:-1] > 0).any(), raw=True)
    f["b_dn"] = b.rolling(5).apply(lambda v: (v[:-1] < 0).any(), raw=True)
    f["btc"] = btc.reindex(x.index).ffill() if btc is not None else np.nan    # BTC 확정 레짐 (같은 몫 시각)
    return f


def trades():
    rows = []
    bars = {i: {o: rls.prep(rls.offset_daily(bt.cached_hourly(i), o, 1)) for o in OFF} for i in COINS}
    for i in COINS:
        s0 = "2021-06-01" if i.startswith("SOL") else "2020-04-01"
        for o in OFF:
            x = bars[i][o]
            F = feats(x).join(extra(x, bars["BTC-USDT-SWAP"][o].conf if not i.startswith("BTC") else None))
            _, t, _ = rls.simulate({i: x[["open", "high", "low", "close"]]}, {}, lambda _: set(), set(),
                                   pd.Timestamp(s0) + pd.Timedelta(hours=o), None, sleeves=("coin",), kill=1.0)
            last_exit = None
            for _, r in t.iterrows():
                sig = r.entry_ts - pd.Timedelta(days=1); f = F.loc[sig]; s = r.side
                w = x.loc[r.entry_ts:r.exit_ts]                                     # 보유 중 일봉 (MFE·정점 날)
                fav = (w.high.max() / r.entry - 1) if s > 0 else (1 - w.low.min() / r.entry)
                peak_day = ((w.high.idxmax() if s > 0 else w.low.idxmin()) - r.entry_ts).days
                ret = s * (r.exit / r.entry - 1)
                rows.append({"inst": i[:3], "off": o, "side": s, "entry_ts": r.entry_ts, "exit_ts": r.exit_ts, "why": r.why, "be": r.be,
                             "days": (r.exit_ts - r.entry_ts).days, "ret%": 100 * ret, "R": ret / r.stop_dist, "mfe_R": fav / r.stop_dist,
                             "peak_day": peak_day, "since_exit": (r.entry_ts - last_exit).days if last_exit is not None else np.nan,
                             "adx": f.adx, "di_gap": s * f.di_gap, "atr_pct": f.atr_pct, "sma_dist": s * f.sma_dist, "slope": s * f.slope,
                             "r5": s * f.r5, "r20": s * f.r20, "brk": f.hi20 if s > 0 else f.lo20, "src": f.src, "age": f.age,
                             "day_r": s * f.day_r, "comp": f.comp, "box": f.box, "pos20": f.pos20 if s > 0 else 1 - f.pos20, "w": f.w,
                             "pre_b": f.b_up if s > 0 else f.b_dn, "btc_same": float(f.btc == s) if not np.isnan(f.btc) else np.nan,
                             "dow": r.entry_ts.dayofweek, "is": r.entry_ts < SPLIT})
                last_exit = r.exit_ts
    return pd.DataFrame(rows)


NUM = ["adx", "di_gap", "atr_pct", "sma_dist", "slope", "r5", "r20", "age", "day_r", "comp", "box", "pos20", "w", "since_exit"]
FLAG = ["brk", "pre_b", "btc_same", "src", "side", "dow", "off"]

if __name__ == "__main__":
    T = trades(); T.to_csv(HERE / "bigwin.csv", index=False)
    T["big"] = T.R >= 5
    print(f"거래 {len(T)} · 큰 승리(R≥5) {T.big.sum()} ({T.big.mean():.1%}) · R 합 {T.R.sum():.0f} 중 큰 승리 몫 {T.R[T.big].sum():.0f}"
          f" · 나머지 R 합 {T.R[~T.big].sum():.0f}")
    print("코인별:", T.groupby("inst").agg(n=("R", "size"), big=("big", "sum"), R합=("R", "sum"), 큰승리R=("R", lambda s: s[s >= 5].sum())).round(0).to_dict("index"))
    print("\n[큰 승리 vs 나머지: 중앙값]"); print(T.groupby("big")[NUM + ["days", "peak_day", "mfe_R"]].median().round(2).T.to_string())
    print("\n[특징 5분위: 큰 승리 비율% (IS / OOS) · 평균 R (IS / OOS)] — 두 구간 같은 방향이어야 의미")
    for c in NUM:
        q = pd.qcut(T[c].rank(method="first"), 5, labels=False)
        g = T.groupby([q, "is"])
        big, mr = g.big.mean().unstack() * 100, g.R.mean().unstack()
        rng = T.groupby(q)[c].agg(lambda s: f"{s.min():.2f}~{s.max():.2f}")
        print(f"{c:10s} " + " | ".join(f"Q{k} {rng[k]:>13s} 큰 {big.loc[k, True]:4.1f}/{big.loc[k, False]:4.1f}% R {mr.loc[k, True]:+.2f}/{mr.loc[k, False]:+.2f}" for k in range(5)))
    print("\n[범주: 큰 승리 비율% (IS / OOS) · 평균 R (IS / OOS) · n]")
    for c in FLAG:
        g = T.groupby([c, "is"]); big, mr, n = g.big.mean().unstack() * 100, g.R.mean().unstack(), T.groupby(c).size()
        print(f"{c:9s} " + " | ".join(f"{k}: 큰 {big.loc[k, True]:4.1f}/{big.loc[k, False]:4.1f}% R {mr.loc[k, True]:+.2f}/{mr.loc[k, False]:+.2f} n{n[k]}" for k in big.index))
    B = T[T.big]
    print("\n[큰 승리의 타이밍]")
    print("진입 시 레짐 나이(age, 일) 분포:", B.age.describe(percentiles=[.25, .5, .75]).round(1).to_dict())
    print("보유 일수:", B.days.describe(percentiles=[.25, .5, .75]).round(0).to_dict(), "· 정점까지 일수:", B.peak_day.describe(percentiles=[.25, .5, .75]).round(0).to_dict())
    print("청산 사유:", B.why.value_counts().to_dict(), "· 최대 R 대비 실현:", round((B.R / B.mfe_R).median(), 2))
    print("연·분기별 큰 승리 수:", B.groupby(B.entry_ts.dt.to_period("Q")).size().to_dict())
    print("방향:", B.side.value_counts().to_dict(), "· 코인:", B.inst.value_counts().to_dict())
