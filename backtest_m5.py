"""quant_nasq100 M5 (ALGORITHM.md) 를 업비트 KRW 현물에 옮겨 본다 — 연구 전용, 봇에 안 들어감.

score = mean(pct_rank(close[-1-S]/close[-L]-1), pct_rank(close[-1]/MA200-1)), 상위 5 동일가중, R일마다 재조정.
신호 = t 종가, 체결 = t+1 시가. 유니버스 = 30일 평균 거래대금 상위 N (스테이블 제외, 이력 L봉 이상).
비용 편도 0.15% (backtest_upbit.py 와 같음). 기준 = BTC 매수보유.
생존 편향: data_cache 는 2026-09 에 상장된 코인뿐 → 실제는 표보다 나쁘다.
사용: python backtest_m5.py
"""
import pathlib

import numpy as np
import pandas as pd

CACHE = pathlib.Path(__file__).resolve().parent / "data_cache"
COST = 0.0015
TOP = 5
STABLE = {"USDT", "USDC", "USDS", "USD1", "USDE", "DAI", "TUSD", "PYUSD", "FDUSD", "RLUSD"}


def load():
    o, c, v = {}, {}, {}
    for f in CACHE.glob("KRW-*_5y.pkl"):
        sym = f.stem[4:-3]
        if sym in STABLE:
            continue
        d = pd.read_pickle(f)
        o[sym], c[sym], v[sym] = d["open"], d["close"], d["close"] * d["volume"]
    return pd.DataFrame(o).sort_index(), pd.DataFrame(c).sort_index(), pd.DataFrame(v).sort_index()


def run(O, C, V, L=253, S=21, R=21, N=30, top_n=TOP, worst=False):
    mom = C.shift(S) / C.shift(L - 1) - 1          # close[-1-S]/close[-L] (t 포함 L봉)
    dist = C / C.rolling(200).mean() - 1
    tv = V.rolling(30).mean()
    hist = C.notna().cumsum() >= L
    dates = C.index
    eq, curve, w_old = 1.0, [], pd.Series(dtype=float)
    per, pos, vs_btc = [], [], []
    start = int(hist.sum(axis=1).ge(TOP * 2).values.argmax())
    for i in range(start, len(dates) - 1 - R, R):
        t = dates[i]
        ok = hist.loc[t] & mom.loc[t].notna() & dist.loc[t].notna()
        uni = tv.loc[t][ok].nlargest(N).index
        if len(uni) < top_n:
            continue
        score = (mom.loc[t, uni].rank(pct=True) + dist.loc[t, uni].rank(pct=True)) / 2
        top = (score.nsmallest if worst else score.nlargest)(top_n).index
        w = pd.Series(1 / len(top), index=top)
        turn = w.sub(w_old, fill_value=0).abs().sum()
        w_old = w
        seg_o = O.iloc[i + 1:i + 2 + R][top]       # 진입 시가 ~ 다음 재조정 시가
        rel = (seg_o / seg_o.iloc[0]).ffill()      # 도중 데이터 끊기면 마지막 값 유지
        val = eq * (1 - COST * turn) * (rel * w).sum(axis=1)
        curve.append(val.iloc[:-1])
        r = val.iloc[-1] / eq - 1
        b = O["BTC"].iloc[i + 1 + R] / O["BTC"].iloc[i + 1] - 1
        per.append(r); vs_btc.append(r > b); pos += list(rel.iloc[-1] - 1)
        eq = val.iloc[-1]
    curve = pd.concat(curve)
    yrs = (curve.index[-1] - curve.index[0]).days / 365
    btc = O["BTC"].loc[curve.index[0]:curve.index[-1]]
    mdd = lambda s: (s / s.cummax() - 1).min()
    return dict(
        from_=curve.index[0].date(), n=len(per),
        cagr=eq ** (1 / yrs) - 1, mdd=mdd(curve),
        win=np.mean(np.array(per) > 0), beat=np.mean(vs_btc), pos_win=np.mean(np.array(pos) > 0),
        avg=np.mean(per),
        btc_cagr=(btc.iloc[-1] / btc.iloc[0]) ** (1 / yrs) - 1, btc_mdd=mdd(btc),
    )


def main():
    O, C, V = load()
    print(f"코인 {C.shape[1]}개 · {C.index[0].date()}~{C.index[-1].date()} · 편도 {COST:.2%}")
    print(f"{'변형':<28}{'시작':>11}{'회':>4}{'CAGR':>8}{'MDD':>8}{'회차승률':>8}{'BTC초과':>8}"
          f"{'종목승률':>8}{'회평균':>8} | BTC CAGR/MDD")
    for name, kw in [
        ("봉수 그대로 L253/S21 R21 N30", {}),
        ("달력환산 L365/S30 R30 N30", dict(L=365, S=30, R=30)),
        ("봉수 그대로 R30", dict(R=30)),
        ("유니버스 N20", dict(N=20)),
        ("유니버스 N50", dict(N=50)),
        ("유니버스 N100", dict(N=100)),
        ("[대조] 유니버스 30 전부 동일가중", dict(top_n=30)),
        ("[대조] 점수 하위 5", dict(worst=True)),
    ]:
        m = run(O, C, V, **kw)
        print(f"{name:<28}{str(m['from_']):>11}{m['n']:>4}{m['cagr']:>+8.0%}{m['mdd']:>+8.0%}"
              f"{m['win']:>8.0%}{m['beat']:>8.0%}{m['pos_win']:>8.0%}{m['avg']:>+8.1%}"
              f" | {m['btc_cagr']:+.0%} / {m['btc_mdd']:+.0%}")


if __name__ == "__main__":
    main()
