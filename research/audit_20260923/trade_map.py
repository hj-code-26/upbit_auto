"""기준선(BUF=0) ↔ 후보(BUF=2) 거래 대응표 — '국면 붕괴 청산 11건이 이득의 전부' 검증.

1:1 매칭을 억지로 하지 않는다. 두 원장의 보유 구간을 겹침으로 묶은 **에피소드** 단위로 대사한다:
  에피소드 = 두 원장 중 어느 쪽이든 보유 구간이 이어져 겹치는 최대 연결 구간.
  각 에피소드의 Δ = Σ log1p(ret_후보) − Σ log1p(ret_기준). pos_pct=1 이면 최종자본 로그비 = Σ 에피소드 Δ (정확 대사).
분류 (기준선 기준):
  동일        진입·청산 봉이 모두 같다
  지연청산    후보가 같은 진입에서 더 늦게 청산 (기준선은 그 사이 청산 후 재진입했을 수 있다)
  기타        그 밖 (대응 모호 — 억지 매칭하지 않는다)
기준선 청산 사유(신호봉 기준): 구조 이탈 / 국면 붕괴(강세<6) / 둘 다.

python trade_map.py → out/trade_map.csv · out/episodes.csv
"""
import sys

import common as C
from common import np, pd

sys.stdout.reconfigure(encoding="utf-8")
LEV = int(next((a.split("=")[1] for a in sys.argv if a.startswith("--lev=")), 3))
h4f, bull, k, h4, fl = C.okx()
b, c = C.run_okx(0, LEV), C.run_okx(2, LEV)
tb, tc = b[1].copy(), c[1].copy()
e0, x0 = C.X.signals(h4f, bull, 0)
bd = C.M.align_daily(bull, h4f.index).ffill().fillna(0).values[k]
brk_dn = (h4f.close < h4f.low.rolling(C.M.H4_M).min().shift(1)).values[k]


def reason(i_out):
    g = i_out - 1
    s, r = bool(brk_dn[g]), bd[g] < C.M.CONF
    return "둘 다" if s and r else "구조 이탈" if s else "국면 붕괴" if r else "기타"


tb["why"] = [reason(int(j)) for j in tb.i_out]
tc["why"] = [reason(int(j)) for j in tc.i_out]
iv = sorted([(int(a), int(z), "b", i) for i, (a, z) in enumerate(zip(tb.i_in, tb.i_out))]
            + [(int(a), int(z), "c", i) for i, (a, z) in enumerate(zip(tc.i_in, tc.i_out))])
eps, cur = [], None
for a, z, s, i in iv:
    if cur and a < cur["end"]:
        cur["end"] = max(cur["end"], z); cur[s].append(i)
    else:
        cur = {"start": a, "end": z, "b": [], "c": []}
        cur[s].append(i); eps.append(cur)
rows = []
for n, e in enumerate(eps):
    lb, lc = np.log1p(tb.ret.values[e["b"]]).sum(), np.log1p(tc.ret.values[e["c"]]).sum()
    B_, C_ = tb.iloc[e["b"]], tc.iloc[e["c"]]
    if len(B_) == 1 and len(C_) == 1 and B_.i_in.iloc[0] == C_.i_in.iloc[0] and B_.i_out.iloc[0] == C_.i_out.iloc[0]:
        kind = "동일"
    elif len(C_) == 1 and len(B_) >= 1 and B_.i_in.iloc[0] == C_.i_in.iloc[0] and C_.i_out.iloc[0] > B_.i_out.iloc[0]:
        kind = "지연청산"
    elif len(B_) == 0 or len(C_) == 0:
        kind = "한쪽만"
    else:
        kind = "기타(모호)"
    rows.append({"ep": n, "start": str(h4.index[e["start"]]), "end": str(h4.index[min(e["end"], len(h4) - 1)]),
                 "kind": kind, "n_base": len(B_), "n_cand": len(C_),
                 "base_first_exit_why": B_.why.iloc[0] if len(B_) else "", "base_exit_whys": "|".join(B_.why),
                 "base_log": lb, "cand_log": lc, "dlog": lc - lb,
                 "base_bars": int(B_.bars.sum()) if len(B_) else 0, "cand_bars": int(C_.bars.sum()) if len(C_) else 0})
EP = pd.DataFrame(rows)
EP.to_csv(C.OUT / f"episodes_lev{LEV}.csv", index=False, encoding="utf-8-sig")
tot = np.log(c[0].iloc[-1] / b[0].iloc[-1])
print(f"{LEV}배 · 기준 {len(tb)}거래 · 후보 {len(tc)}거래 · 에피소드 {len(EP)}")
print(f"최종자본 로그비 {tot:+.4f} = Σ 에피소드 Δ {EP.dlog.sum():+.4f}  (대사 {'일치' if abs(tot-EP.dlog.sum())<1e-9 else '불일치'})")
print(EP.groupby("kind").agg(n=("ep", "size"), dlog=("dlog", "sum")).to_string())
d = EP[EP.kind != "동일"].sort_values("dlog", ascending=False)
print(f"\n비동일 에피소드 {len(d)}개 · Δ 합 {d.dlog.sum():+.4f} · 양(+) {int((d.dlog>0).sum())} / 음(−) {int((d.dlog<0).sum())}")
print(f"상위 3개 에피소드가 총 Δ 에서 차지: {d.dlog.head(3).sum()/tot*100:.0f}%  · 상위 1개: {d.dlog.head(1).sum()/tot*100:.0f}%")
print(f"기준선 청산 사유에 '국면 붕괴'/'둘 다' 가 포함된 비동일 에피소드: "
      f"{int(d.base_exit_whys.str.contains('국면|둘').sum())}개 · Δ {d[d.base_exit_whys.str.contains('국면|둘')].dlog.sum():+.4f}")
print(f"기준선 원장 청산 사유 분포: {tb.why.value_counts().to_dict()} · 후보: {tc.why.value_counts().to_dict()}")
print(d[["ep", "start", "kind", "n_base", "n_cand", "base_exit_whys", "dlog", "base_bars", "cand_bars"]].to_string(index=False))
