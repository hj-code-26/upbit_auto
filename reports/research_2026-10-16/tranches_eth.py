"""ETH 단독(서버 구성) 몫 수 비교: 1·2·3·6몫 × 증거금 4·5%, 30x, 지금 코드. 최소 주문(ETH 0.001 ≈ 2.42 USDT)에 필요한 잔고 포함."""
import sys, pathlib, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path[:0] = [str(HERE.parents[1]), str(HERE)]
import pandas as pd, rls, backtest as bt
from multiprocessing import Pool
SETS = {"1몫 (00)": (0,), "2몫 (00·12)": (0, 12), "3몫 (00·08·16) 지금": (0, 8, 16), "6몫 (4시간마다)": (0, 4, 8, 12, 16, 20)}
def run(a):
    name, m = a; offs = SETS[name]; H = {"ETH-USDT-SWAP": bt.cached_hourly("ETH-USDT-SWAP")}
    out = {"몫": name, "증거금": f"{m:.0%}"}
    with bt.lever(30, m):
        for tag, a0, b0 in (("전체", "2020-06-01", "2026-10-16"), ("20~23", "2020-06-01", "2023-06-30"), ("23~26", "2023-07-01", "2026-10-16")):
            eq, tr = rls.simulate_tranches(H, offs, a0, b0); s = bt.stats(eq, tr)
            out |= {f"{tag} Sharpe": round(s["sharpe"], 2), f"{tag} 연수익": round(s["cagr%"], 1), f"{tag} MDD": round(s["mdd%"], 1)}
            if tag == "전체":
                out |= {"최악달": round(s["worst_mo%"], 1), "거래": len(tr)}
        eq, _ = rls.simulate_tranches(H, offs, "2025-06-01", "2026-10-16"); y = eq[eq.index >= "2025-12-31"]
    out |= {"2026": round((y.iloc[-1] / y.iloc[0] - 1) * 100, 1), "2026 MDD": round((1 - y / y.cummax()).max() * 100, 1)}
    need = 2.42 * 1.001 / (m / len(offs) * 30)
    return out | {"필요잔고(배수1)": round(need, 1), "필요잔고(배수0.5)": round(need * 2, 1)}
if __name__ == "__main__":
    with Pool(8) as p: df = pd.DataFrame(p.map(run, [(n, m) for n in SETS for m in (0.04, 0.05)]))
    df.to_csv(HERE / "tranches_eth.csv", index=False)
    pd.set_option("display.width", 300); pd.set_option("display.max_columns", 30); print(df.to_string(index=False))
