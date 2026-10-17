"""하이브리드: 지금 전략(진입·손절·본전·DROP1·C3·변동성 크기) + 정세 1~5 단계별 크기 배수. ETH·BTC 3몫 4%, IS/OOS. → 표"""
import sys, pathlib, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path[:0] = [str(HERE.parents[1]), str(HERE)]
import numpy as np, pandas as pd, rls, backtest as bt, regime5 as R5
from multiprocessing import Pool
OFFS = (0, 8, 16)
VAR = {"지금": None, "5단계만 2배": {5: 2.0}, "5=2·4=1·3=0.5·2=1·1=0.5": {5: 2.0, 3: 0.5, 1: 0.5},
       "5=1.5·3=0.5·1=0.5": {5: 1.5, 3: 0.5, 1: 0.5}, "역(5=0.5)": {5: 0.5, 1: 2.0}}
def run(a):
    inst, name = a
    h = bt.cached_hourly(inst)
    LV = pd.concat([R5.levels(rls.offset_daily(h, o, 1)) for o in OFFS])
    m = VAR[name]
    k = (lambda i, s: m.get(int(LV.get(s.name, 3) if not np.isnan(LV.get(s.name, np.nan)) else 3), 1.0)) if m else None
    out = {"inst": inst[:3], "var": name}
    a0 = "2021-09-01" if inst[:3] == "SOL" else "2020-08-01"
    with bt.lever(30, 0.04):
        for tag, s0, e in (("is", a0, "2023-06-30"), ("oos", "2023-07-01", "2026-10-12")):
            eq, tr = rls.simulate_tranches({inst: h}, OFFS, s0, e, max_total=0.10, size_k=k)
            s = bt.stats(eq); out |= {f"{tag}_cagr": round(s["cagr%"], 1), f"{tag}_mdd": round(s["mdd%"], 1), f"{tag}_sh": round(s["sharpe"], 2)}
    return out
if __name__ == "__main__":
    jobs = [(i, v) for i in ("ETH-USDT-SWAP", "BTC-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP") for v in VAR]
    with Pool(9) as p: df = pd.DataFrame(p.map(run, jobs))
    df.to_csv(HERE / "hybrid5.csv", index=False)
    pd.set_option("display.width", 250); print(df.to_string(index=False))
