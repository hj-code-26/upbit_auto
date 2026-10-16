import sys, itertools, warnings; warnings.filterwarnings("ignore")
import pandas as pd, search as S
from multiprocessing import Pool
pd.set_option("display.width", 250); pd.set_option("display.max_rows", 200)
CUR = S.CUR
one = [dict(CUR, **{p: v}) | {"vary": p} for p, vals in S.SPACE.items() for v in vals if v != CUR[p]]
V = {"cur": CUR, "noBE_noDROP": dict(CUR, be=99.0, drop=99.0), "drop.75_trail4": dict(CUR, drop=0.75, trail=4.0)}
six = (0, 4, 8, 12, 16, 20)
sz = [(n, L, m, t) for n in V for L in (25, 30) for m in (0.02, 0.03, 0.04) for t in (3, 6)]
def r1(c): return S.run({k: c[k] for k in S.SPACE}) | {"vary": c["vary"]}
def r2(a):
    n, L, m, t = a
    return {"variant": n, "L": L, "margin%": m * 100, "tr": t} | S.run(V[n], L, m, S.rls.TRANCHES if t == 3 else six)
if __name__ == "__main__":
    with Pool(9) as p:
        A = pd.DataFrame(p.map(r1, one)); B = pd.DataFrame(p.map(r2, sz))
    c = ["is_cagr", "is_mdd", "is_sh", "oos_cagr", "oos_mdd", "oos_sh", "sx_cagr", "sx_mdd", "sx_sh"]
    print("ONE-AT-A-TIME around current (current: IS 1.56 / OOS 1.83 / SX 0.74)")
    print(A[["vary"] + list(S.SPACE) + c].to_string(index=False))
    B.to_csv("stage2.csv", index=False)
    print("SIZING on variants"); print(B[["variant", "L", "margin%", "tr"] + c].to_string(index=False))
