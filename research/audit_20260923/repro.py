"""1단계 재현: 보고된 수치가 이 작업 트리에서 다시 나오는가 + 커밋본 engine 과 작업 트리 engine 이 같은 숫자를 내는가.

python research/audit_20260923/repro.py  → out/repro.json
"""
import importlib.util
import json
import sys

import common as C
from common import E, M, Q, X, np, pd

sys.stdout.reconfigure(encoding="utf-8")


def load_head_engine():
    spec = importlib.util.spec_from_file_location("engine_head", C.pathlib.Path(__file__).parent / "engine_head.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def summ(res):
    f = lambda a, b: Q.metrics(res[0], C.rets(res), res[2], a, b)
    t, v, a = f(C.START, C.SPLIT), f(C.SPLIT, None), f(C.START, None)
    return {"explore_sh": round(t["sharpe"], 4), "valid_sh": round(v["sharpe"], 4), "full_sh": round(a["sharpe"], 4),
            "full_cum": round(a["누적"], 2), "mdd": round(a["mdd"], 2), "trades": int(a["거래"]), "liq": int(res[2]["liq"]),
            "final_eq": round(float(res[0].iloc[-1]), 6), "open_at_end": bool(res[2]["open_at_end"])}


out = {}
EH = load_head_engine()
h4f, bull, k, h4, fl = C.okx()
out["okx_range"] = [str(h4.index[0]), str(h4.index[-1]), len(h4)]
out["fills"] = {"n": len(fl), "skipped_none": sum(v is None for v in fl.values())}

# (1) 커밋본 vs 작업 트리 engine
eq = {}
for lev in (1, 2, 3, 5):
    for buf in (0, 2):
        a, b = C.run_okx(buf, lev), C.run_okx(buf, lev, eng=EH)
        eq[f"lev{lev}_buf{buf}"] = bool(np.array_equal(a[0].values, b[0].values)
                                        and np.array_equal(C.rets(a), C.rets(b)) and a[2] == b[2])
out["engine_head_vs_worktree_identical"] = eq

# (2) exitconf 불변식 (a) 는 기준선을 자기 자신과 비교한다 → 실제 기준선(Q.frames) 과 따로 대조
h4q, eq_, xq, _, _ = Q.frames()
mq = h4q.index >= pd.Timestamp(C.START, tz="UTC")
qb = Q.simulate(h4q[mq], eq_[mq], xq[mq], lambda i, c: 3, fills=fl)
b0 = C.run_okx(0, 3)
out["buf0_equals_Qframes_baseline"] = bool(np.array_equal(qb[0].values, b0[0].values))
e0, x0 = X.signals(h4f, bull, 0)
out["buf0_signal_diff_vs_Qframes"] = {"entry": int((e0[k] != eq_[mq]).sum()), "exit": int((x0[k] != xq[mq]).sum())}

# (3) 보고 수치 재현
out["okx"] = {f"lev{lev}_buf{buf}": summ(C.run_okx(buf, lev)) for lev in (1, 2, 3, 5) for buf in (0, 1, 2, 3)}
bs = {}
for buf in (0, 1, 2, 3):
    r = C.run_bs(buf)
    mt = Q.metrics(r[0], C.rets(r), r[2], None, None)
    bs[f"buf{buf}"] = {"sh": round(mt["sharpe"], 4), "cum": round(mt["누적"], 1), "trades": int(mt["거래"])}
b4, _ = C.bitstamp()
out["bitstamp_range"] = [str(b4.index[0]), str(b4.index[-1]), len(b4)]
out["bitstamp"] = bs
out["cost_one_way"] = E.cost()
out["conf"] = M.CONF

(C.OUT / "repro.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, indent=1))
