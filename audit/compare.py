"""3단계 성적 비교 + 거래 원장 (감사 산출물 3).

  1단계 원본      : 수정 전 엔진 (audit/old_engine.py) · 지금 .env 설정
  2단계 엔진수정   : engine.py · **같은 전략**. 여기서 벌어지는 차이는 전부 체결/청산 판정 때문이다
  3단계 전략변경   : engine.py + 변동성 타겟팅(계좌 40%·창 40일) — 미검증 후보

출력: audit/stages.csv (지표) · audit/ledger_<단계>.csv (거래 원장, 진입/청산 시각 포함)
사용: python audit/compare.py
"""
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import backtest_both as BO           # noqa: E402
import backtest_quant as Q           # noqa: E402
import backtest_sizing as S          # noqa: E402
import engine as E                   # noqa: E402
import old_engine as OLD             # noqa: E402

OUT = pathlib.Path(__file__).resolve().parent
YEAR = 365


def ledger(h4, sig, lab, lev_of, fills):
    """거래별 진입/청산 시각·가격까지 남긴다 (engine.run 과 같은 순서로 다시 돈다)."""
    curve, t, stats = E.run(h4, *sig, lev_of, lab=lab, allow=("long",), fills=fills)
    rows, o, lo, c, idx = [], h4.open.values, h4.low.values, h4.close.values, h4.index
    ent = None
    k = 0
    for i in range(1, len(h4)):
        if ent is None and sig[0][i - 1]:
            f = fills.get(idx[i], o[i]) if fills else o[i]
            px = f[0] if isinstance(f, tuple) else f
            if px is not None:
                ent = (idx[i], px)
        elif ent is not None and sig[1][i - 1]:
            rows.append({"entry_time": ent[0], "entry_px": ent[1], "exit_time": idx[i], "exit_px": o[i],
                         "bars": (idx[i] - ent[0]) / pd.Timedelta("4h")})
            ent = None
    led = pd.DataFrame(rows)
    if len(led) and len(t):
        n = min(len(led), len(t))
        led = led.iloc[:n].assign(ret=t.ret.values[:n], how=t.how.values[:n], regime=t.regime.values[:n])
    return curve, t, stats, led


def metrics(curve, t, stats):
    r = t.ret.values if len(t) else np.zeros(1)
    d = curve.resample("D").last().dropna()
    dr = d.pct_change().dropna()
    win, loss = r[r > 0], r[r <= 0]
    yrs = (curve.index[-1] - curve.index[0]).days / YEAR
    mdd_d = (d / d.cummax() - 1).min() * 100
    cagr = ((curve.iloc[-1]) ** (1 / yrs) - 1) * 100 if curve.iloc[-1] > 0 else -100.0
    return {
        "순수익%": round(curve.iloc[-1] * 100 - 100, 1), "CAGR%": round(cagr, 1),
        "MDD_일별%": round(mdd_d, 1), "MDD_4h%": round((curve / curve.cummax() - 1).min() * 100, 1),
        "Calmar": round(cagr / abs(mdd_d), 2) if mdd_d else 0,
        "Sharpe_표준": round(float(dr.mean() / dr.std() * np.sqrt(YEAR)), 2) if dr.std() else 0,
        "Sharpe_탐색": round(S.sharpe_std(curve, Q.START, Q.SPLIT), 2),
        "Sharpe_검증": round(S.sharpe_std(curve, Q.SPLIT, None), 2),
        "거래": len(r), "승률%": round((r > 0).mean() * 100, 1),
        "평균순이익%": round(r.mean() * 100, 2),
        "평균이익%": round(win.mean() * 100, 2) if len(win) else 0,
        "평균손실%": round(loss.mean() * 100, 2) if len(loss) else 0,
        "profit_factor": round(win.sum() / abs(loss.sum()), 2) if len(loss) and loss.sum() else None,
        "최대연속손실": int(max((len(list(g)) for k2, g in __import__("itertools").groupby(r <= 0) if k2), default=0)),
        "상위5제외_누적%": round((np.prod(1 + np.sort(r)[::-1][5:]) - 1) * 100, 1) if len(r) > 5 else None,
        "롱": int((t.side == "롱").sum()) if len(t) else 0, "숏": int((t.side == "숏").sum()) if len(t) else 0,
        "강제청산": stats.get("liq", 0) if isinstance(stats, dict) else int(stats),
        "청산_시가불명": stats.get("liq_ambiguous", 0) if isinstance(stats, dict) else None,
        "미청산_평가%": round(stats.get("mtm_open", 0) * 100, 1) if isinstance(stats, dict) else None,
        "미청산_강제종료%": (round(stats["forced_close_ret"] * 100, 1)
                       if isinstance(stats, dict) and stats.get("forced_close_ret") is not None else None),
        "회전율_회년": round(len(r) / yrs, 1),
    }


def main():
    h4, sig, lab, fills, _, _ = S.setup()
    LEV = S.LMAX
    out = {}

    # 0단계 = 아무것도 고치기 전: 옛 엔진 + 옛 일봉 정렬 (신호·이벤트·분봉 체결까지 전부 옛 기준)
    import model as MM
    MM.ALIGN_LEGACY = True
    try:
        h0, sig0, lab0, fills0, _, _ = S.setup()
        cv, t, liq = OLD.simulate(h0, sig0, lambda i, c: LEV, lab0, ("long",), fills=fills0)
        out["0_원본"] = metrics(cv, t, liq)
    finally:
        MM.ALIGN_LEGACY = False

    cv, t, liq = OLD.simulate(h4, sig, lambda i, c: LEV, lab, ("long",), fills=fills)
    out["1_옛엔진_새정렬"] = metrics(cv, t, liq)

    cv, t, st, led = ledger(h4, sig, lab, lambda i, c: LEV, fills)
    out["2_엔진수정"] = metrics(cv, t, st)
    led.to_csv(OUT / "ledger_2_엔진수정.csv", index=False, encoding="utf-8-sig")

    def vt(i, c):
        v = Q.acct_vol(i, c, 40)
        return S.WARMUP_CAP if v is None else min(LEV, 40 / v)
    cv, t, st, led = ledger(h4, sig, lab, vt, fills)
    out["3_전략변경_VT40"] = metrics(cv, t, st)
    led.to_csv(OUT / "ledger_3_VT40.csv", index=False, encoding="utf-8-sig")

    # 엔진 결함이 실제로 성적을 바꾸는 지점은 레버리지를 올렸을 때다. 5배에서는 걸리는 거래가 없다.
    for L in (7, 10):
        cv, t, liq = OLD.simulate(h4, sig, lambda i, c, L=L: L, lab, ("long",), fills=fills)
        out[f"1_원본_{L}배"] = metrics(cv, t, liq)
        cv, t, st = E.run(h4, *sig, lambda i, c, L=L: L, lab=lab, allow=("long",), fills=fills)
        out[f"2_엔진수정_{L}배"] = metrics(cv, t, st)

    df = pd.DataFrame(out)
    df.to_csv(OUT / "stages.csv", encoding="utf-8-sig")
    print(df.to_string())
    print(f"\n저장: {OUT / 'stages.csv'} · ledger_2_엔진수정.csv · ledger_3_VT40.csv")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
