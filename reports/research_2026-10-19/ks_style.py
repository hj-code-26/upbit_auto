"""연구: OKX 리드 트레이더 KS 방식 두 가지를 규칙으로 만들어 리플레이 (regime5.run 재사용). 운용 코드 아님.
A 과열 역추세: 우리 레짐(conf, ADX 보강 포함) 이 BULL 인데 종가가 SMA50 + k×ATR 이상 → 숏 (BEAR 인데 − k×ATR 이하 → 롱).
  종가가 SMA50 ± x×ATR 안으로 돌아오면 상태 해제 = 다음 시가 청산. KS 는 손절이 없지만 30x 구조라 넓은 손절 s×ATR.
B 추세 확정 전 진입: 레짐 NEUTRAL(conf 0) 인데 종가가 SMA50 위 → 롱 (아래 → 숏). 종가가 SMA50 반대편으로 가면 해제.
  레짐이 확정되면(conf ≠ 0) 현행 규칙이 맡으므로 상태 해제.
크기: 위험 기준 r (손절 닿으면 잔고 r 손실, regime5 와 같음). 현행(코인 1거래 손절 ≈ 잔고 2~2.5%) 과 맞춰 r = 2%.
비교: 단독 / 현행(BTC+ETH 6몫 4%, 좁은 봉 포함) 위에 더함(일수익 합 — 자본을 나누지 않고 같이 돌린다고 봄). → ks_style.csv"""
import pathlib, sys, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path.insert(0, str(HERE.parents[1]))
import numpy as np, pandas as pd
import backtest as bt, regime5 as R5, rls

H = {i: bt.cached_hourly(i) for i in rls.COINS}
P = [("전체", "2020-04-01", None), ("20-22", "2020-04-01", "2022-12-31"), ("23-24", "2023-01-01", "2024-12-31"), ("25~", "2025-01-01", None)]
OFF = (0, 8, 16)


def state_a(k, x_exit):
    """과열 역추세 상태: 1 = 롱(하락 과열), −1 = 숏(상승 과열), 0 = 없음. 해제 = SMA50 ± x_exit ATR 안."""
    def f(d):
        x = rls.prep(d); s50 = x.close.rolling(rls.SMA).mean(); z = ((x.close - s50) / x.atr).to_numpy()
        conf, out, st = x.conf.to_numpy(), np.zeros(len(x)), 0
        for i in range(len(x)):
            if np.isnan(z[i]):
                continue
            if st == -1 and z[i] <= x_exit or st == 1 and z[i] >= -x_exit:
                st = 0
            if st == 0 and conf[i] == 1 and z[i] >= k:
                st = -1
            elif st == 0 and conf[i] == -1 and z[i] <= -k:
                st = 1
            out[i] = st
        return pd.Series(out + 3, x.index)          # regime5 표 키: 2 = 숏, 3 = 없음, 4 = 롱
    return f


def state_b(d):
    x = rls.prep(d); s50 = x.close.rolling(rls.SMA).mean()
    side = np.sign(x.close - s50).where(x.conf == 0, 0).fillna(0)
    return side + 3


def table(stop, risk=0.02, trail=None, be=None):
    leg = lambda s: dict(side=s, risk=risk, stop=stop, trail=trail, drop1=False, be=be)  # noqa: E731
    return {2: leg(-1), 3: dict(side=0), 4: leg(1)}


def tranches(levels_fn, tab):
    """두 코인 각각 3몫 → 일수익 평균 (코인별 자본 절반)."""
    rs = []
    for i, h in H.items():
        eq, _ = R5.run_tranches(h, OFF, "2020-03-01", None, levels_fn=levels_fn, table=tab)
        rs.append(eq.pct_change())
    return pd.concat(rs, axis=1).fillna(0).mean(axis=1)


def stats(r, a, b):
    r = r[(r.index >= pd.Timestamp(a)) & (r.index <= pd.Timestamp(b or "2100"))].dropna()
    eq = (1 + r).cumprod(); y = (r.index[-1] - r.index[0]).days / 365.25
    return (eq.iloc[-1] ** (1 / y) - 1) * 100, (1 - eq / eq.cummax()).max() * 100, r.mean() / r.std() * 365 ** 0.5


if __name__ == "__main__":
    base_eq, _ = rls.simulate_tranches(H, rls.TRANCHES, "2020-03-01", None)
    base = base_eq.pct_change(); base.index = base.index.normalize()
    V = {"현행": None}
    for k in (2.5, 3.0, 3.5, 4.0):
        for xe in (0.0, 1.0):
            for s in (2.0, 3.0):
                V[f"A 과열 k{k} 해제{xe} 손절{s}"] = (state_a(k, xe), table(s))
    for s in (1.5, 2.0, 3.0):
        V[f"B 확정 전 손절{s}"] = (state_b, table(s))
        V[f"B 확정 전 손절{s} 본전1"] = (state_b, table(s, be=1.0))
    out = []
    for name, v in V.items():
        if v is None:
            r_alone, r_sum = base, base
        else:
            r_alone = tranches(*v); r_alone.index = r_alone.index.normalize()
            r_alone = r_alone.groupby(level=0).sum()
            r_sum = base.add(r_alone, fill_value=0)
        row = {"rule": name, "corr": round(base.corr(r_alone), 2) if v else 1.0}
        for p, a, b in P:
            for tag, r in (("alone", r_alone), ("plus", r_sum)):
                c, m, s = stats(r, a, b)
                row |= {f"{p}_{tag}_cagr": round(c, 1), f"{p}_{tag}_mdd": round(m, 1), f"{p}_{tag}_sh": round(s, 2)}
        out.append(row)
        print(f"{name:26s} 단독 " + " | ".join(f"{p} {row[f'{p}_alone_cagr']:6.1f}/{row[f'{p}_alone_mdd']:4.1f} S{row[f'{p}_alone_sh']:.2f}" for p, *_ in P)
              + f"   현행+ 전체 {row['전체_plus_cagr']:.1f}/{row['전체_plus_mdd']:.1f} S{row['전체_plus_sh']:.2f} 상관 {row['corr']}", flush=True)
    pd.DataFrame(out).to_csv(HERE / "ks_style.csv", index=False)
