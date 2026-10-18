"""레버리지 10x~50x 조절 방식 비교 (지금 버전: C3 + 동적 크기, 3몫, 종목당 증거금 4%).
선택은 IS(2020-06~2023-06, ETH·BTC) Sharpe 평균. OOS(2023-07~)·SOL·XRP·2026 은 확인용. → levers.csv"""
import sys, pathlib, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path[:0] = [str(HERE.parents[1]), str(HERE)]
import numpy as np, pandas as pd, rls, backtest as bt, regime as R
from multiprocessing import Pool
OFFS = (0, 8, 16)
INSTS = ("ETH-USDT-SWAP", "BTC-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP")
clipL = lambda v: int(np.clip(round(v), 10, 50)) if np.isfinite(v) else 30  # noqa: E731
def adx_tab(inst):
    h = bt.cached_hourly(inst)
    return pd.concat([R.adx(rls.offset_daily(h, o, 1)) for o in OFFS])
def method(name, inst):
    if name.startswith("fixed"):
        L = int(name[5:]); return lambda i, s: L
    if name == "vol1.2+conf0":
        return lambda i, s: clipL(1.2 / (s.atr / s.close)) if s.get("conf0", s.conf) != 0 else clipL(0.6 / (s.atr / s.close))
    if name.startswith("vol"):                     # 변동성 작을 때 높게: L = c / ATR%
        c = float(name[3:]); return lambda i, s: clipL(c / (s.atr / s.close))
    if name == "invvol":                           # 반대 (확인용)
        return lambda i, s: clipL(30 * (s.atr / s.close) / 0.04)
    A = adx_tab(inst)
    if name == "adx":                              # 추세 강할 때 높게: L = ADX + 10
        return lambda i, s: clipL(A.get(s.name, 20) + 10)
    if name == "invadx":
        return lambda i, s: clipL(60 - A.get(s.name, 20))
    if name == "conf0":                            # SMA 확정 신호 30x, ADX 보강 신호 15x
        return lambda i, s: 30 if s.get("conf0", s.conf) != 0 else 15
NAMES = [f"fixed{L}" for L in (10, 15, 20, 25, 30, 35, 40, 45, 50)] + ["vol0.8", "vol1.0", "vol1.2", "vol1.5", "invvol", "adx", "invadx", "conf0", "vol1.2+conf0"]
def run(a):
    inst, name, vt = a
    h = bt.cached_hourly(inst); f = method(name, inst)
    s0 = "2021-09-01" if inst[:3] == "SOL" else "2020-06-01"
    out = {"inst": inst[:3], "method": name, "vol_size": vt != 0}
    with bt.lever(30, 0.04):
        for tag, a0, b0 in (("is", s0, "2023-06-30"), ("oos", "2023-07-01", "2026-10-16"), ("y26", "2025-06-01", "2026-10-16")):
            eq, tr = rls.simulate_tranches({inst: h}, OFFS, a0, b0, lever_fn=f, vol_target=vt)
            if tag == "y26":
                eq = eq[eq.index >= "2025-12-31"]
                out |= {"y26%": round((eq.iloc[-1] / eq.iloc[0] - 1) * 100, 1), "y26_mdd": round((1 - eq / eq.cummax()).max() * 100, 1)}
                continue
            s = bt.stats(eq, tr)
            out |= {f"{tag}_cagr": round(s["cagr%"], 1), f"{tag}_mdd": round(s["mdd%"], 1), f"{tag}_sh": round(s["sharpe"], 2),
                    f"{tag}_liq": int((tr.why == "liq").sum()) if len(tr) else 0, f"{tag}_L": round(tr.lever.mean(), 1) if len(tr) else 0}
    return out
if __name__ == "__main__":
    jobs = [(i, n, None) for i in INSTS for n in NAMES] + [(i, n, 0) for i in INSTS for n in ("fixed30", "vol1.0", "vol1.2", "adx")]
    with Pool(9) as p: df = pd.DataFrame(p.map(run, jobs, chunksize=2))
    df.to_csv(HERE / "levers.csv", index=False)
    pd.set_option("display.width", 300); pd.set_option("display.max_rows", 200)
    piv = lambda col: df[df.vol_size].pivot(index="method", columns="inst", values=col).loc[NAMES]  # noqa: E731
    sc = df[df.vol_size & df.inst.isin(["ETH", "BTC"])].groupby("method").is_sh.mean().rename("IS점수(ETH·BTC)")
    for col in ("is_sh", "oos_sh", "is_cagr", "oos_cagr", "oos_mdd", "y26%", "y26_mdd", "is_L"):
        print(f"\n--- {col}"); print(piv(col).join(sc) if col == "is_sh" else piv(col))
    print("\n--- 동적 크기 끔 (vol_size False)"); print(df[~df.vol_size][["inst", "method", "is_sh", "oos_sh", "oos_cagr", "oos_mdd", "y26%"]].to_string(index=False))
    print("\n강제청산 건수 합:", int(df.filter(like="_liq").sum().sum()))
