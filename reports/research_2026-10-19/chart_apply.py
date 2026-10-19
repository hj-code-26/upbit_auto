"""chart_cnn 결과를 운용 규칙(현행 6몫 · BTC+ETH · 30x · 4% · 동적 크기)에 넣으면 바뀌는가. 리플레이 전용.
차트 연구에서 맞힌 것 → 운용 규칙 후보:
  hh·ll·hl_first (종가에서 가까운 전일 끝이 먼저 깨짐): 신호 봉 종가가 진입 반대쪽 끝에 붙어 있으면 다음 날 그쪽(=손절 쪽)이 먼저 깨지기 쉬움
    → CLV(신호 봉 고저 안 종가 위치, 진입 방향 기준) < c 면 진입 건너뜀
  box_dir (5일 박스 안 가까운 끝이 먼저 깨짐): 5일 고저 안 종가 위치(진입 방향 기준) < c 면 건너뜀
  expand (좁은 날 다음은 넓어짐): 신호 봉 폭 / ATR < w 면 다음 날 크게 움직일 것 → 크기 ×k (k<1 줄임, k>1 키움)
건너뛴 진입은 조건이 풀리면 다음 날 같은 신호로 들어간다. → chart_apply.csv"""
import pathlib, sys, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path.insert(0, str(HERE.parents[1]))
import pandas as pd
import backtest as bt, rls

H = {i: bt.cached_hourly(i) for i in rls.COINS}
P = [("전체", "2020-04-01", None), ("20-22", "2020-04-01", "2022-12-31"), ("23-24", "2023-01-01", "2024-12-31"), ("25~", "2025-01-01", None)]

# 몫마다 일봉 경계가 달라 행 시각이 겹치지 않으므로 모든 몫의 신호 봉 특징을 상품별 한 표에 모은다
F = {}
for i, h in H.items():
    rows = []
    for off in rls.TRANCHES:
        x = rls.prep(rls.offset_daily(h, off, 1))
        rng = (x.high - x.low).replace(0, float("nan"))
        hi5, lo5 = x.high.rolling(5).max(), x.low.rolling(5).min()
        rows.append(pd.DataFrame({"conf": x.conf, "clv": (x.close - x.low) / rng, "pos5": (x.close - lo5) / (hi5 - lo5),
                                  "w": rng / x.atr}, index=x.index))
    F[i] = pd.concat(rows).sort_index()


def side_pos(f, col):
    """진입 방향 기준 위치: 롱 = 그대로, 숏 = 1 − 위치 (1 = 진입 방향 끝, 0 = 손절 쪽 끝)."""
    return f[col] if f.conf > 0 else 1 - f[col]


RULES = {
    "현행": {},
    "CLV<0.2 건너뜀": {"entry_ok": lambda i, d: not side_pos(F[i].loc[d], "clv") < 0.2},
    "CLV<0.33 건너뜀": {"entry_ok": lambda i, d: not side_pos(F[i].loc[d], "clv") < 0.33},
    "CLV<0.5 건너뜀": {"entry_ok": lambda i, d: not side_pos(F[i].loc[d], "clv") < 0.5},
    "5일위치<0.33 건너뜀": {"entry_ok": lambda i, d: not side_pos(F[i].loc[d], "pos5") < 0.33},
    "5일위치<0.5 건너뜀": {"entry_ok": lambda i, d: not side_pos(F[i].loc[d], "pos5") < 0.5},
    "좁은 봉(w<0.7) ×0.5": {"size_k": lambda i, s: 0.5 if F[i].at[s.name, "w"] < 0.7 else 1.0},
    "좁은 봉(w<0.7) ×1.5": {"size_k": lambda i, s: 1.5 if F[i].at[s.name, "w"] < 0.7 else 1.0},
    "좁은 봉(w<1.0) ×0.5": {"size_k": lambda i, s: 0.5 if F[i].at[s.name, "w"] < 1.0 else 1.0},
}


def run(kw, start, end):
    eq, t = rls.simulate_tranches(H, rls.TRANCHES, start, end, **kw)
    y = (eq.index[-1] - eq.index[0]).days / 365.25
    r = eq.pct_change().dropna()
    return dict(cagr=round((eq.iloc[-1] ** (1 / y) - 1) * 100, 1), mdd=round((1 - eq / eq.cummax()).max() * 100, 1),
                sharpe=round(r.mean() / r.std() * 365 ** 0.5, 2), n=len(t))


if __name__ == "__main__":
    out = []
    for name, kw in RULES.items():
        res = {p: run(kw, s, e) for p, s, e in P}
        out.append({"rule": name, **{f"{p}_{k}": v for p, d in res.items() for k, v in d.items()}})
        print(f"{name:20s} " + " | ".join(f"{p} {d['cagr']:5.1f}%/{d['mdd']:4.1f}% S{d['sharpe']:.2f} n{d['n']}" for p, d in res.items()), flush=True)
    pd.DataFrame(out).to_csv(HERE / "chart_apply.csv", index=False)
