"""꼬리위험 보완 — 상위 거래 제거 민감도 · 로그 기여 집중도 · 청산 여유 · 비용/펀딩 스트레스.

이 파일의 결과는 **집중도·스트레스 진단**이지 유의성 검정이 아니다. 새 k 로 관문을 바꾸지 않는다.

원 구현 (backtest_lev.ex_top): 거래 수익률 ret(=계좌 대비 레버리지 적용 수익률, pos_pct=1) 내림차순 상위 n 개를
곱에서 빼고 나머지를 복리. pos_pct=1 이면 ret 순위 = 계좌 로그기여 log1p(ret) 순위와 같다.
달러 손익 순위(= 진입 시 자본 × ret)는 다르다 → 따로 센다.

두 가지 제거:
  factor  : 수익률 인자만 뺀다 (원 구현). 그 거래가 없었을 때 이후 주문 크기가 달라지는 효과는 복리식 안에서만 반영.
  rerun   : 그 거래의 보유 구간 [i_in, i_out] 동안 진입을 막고 엔진을 다시 돌린다. 그 사이 다른 진입도 막히므로
            '그 거래가 없었다면' 의 근사이지 정확한 반사실이 아니다 (봇은 그 구간 안에 다른 돌파가 오면 샀을 수 있다).

python tail.py → out/topk.csv · out/liq_margin.csv · out/stress.csv
"""
import sys

import common as C
from common import E, np, pd

sys.stdout.reconfigure(encoding="utf-8")


def ledger(res):
    t = res[1].copy()
    eq_before = np.concatenate([[1.0], np.cumprod(1 + t.ret.values)[:-1]])
    t["usd"] = eq_before * t.ret.values
    t["logc"] = np.log1p(t.ret.values)
    return t


def rerun_without(buf, lev, drop):
    h4f, bull, k, h4, fl = C.okx()
    en, ex = C.X.signals(h4f, bull, buf)
    g = np.ones(len(h4), bool)
    for a, b in drop:
        g[max(a - 1, 0):b] = False          # engine 은 gate[i-1] 로 봉 i 진입을 본다
    z = np.zeros(len(h4), bool)
    return E.run(h4, en[k], ex[k], z, z, lambda i, c: lev, allow=("long",), fills=fl, gate=(g, None))


rows, first = [], []
for lev in (1, 2, 3, 5):
    for buf in (0, 2):
        res = C.run_okx(buf, lev)
        t = ledger(res)
        tot = t.logc.sum()
        for key in ("ret", "usd"):
            order = t.sort_values(key, ascending=False)
            for kk in range(11):
                keep = t.drop(order.index[:kk])
                fac = (np.prod(1 + keep.ret.values) - 1) * 100
                rr = rerun_without(buf, lev, list(zip(order.i_in.values[:kk], order.i_out.values[:kk]))) if key == "ret" else None
                rows.append({"lev": lev, "buf": buf, "rank_by": key, "k": kk, "cum_factor%": fac,
                             "cum_rerun%": (rr[0].iloc[-1] - 1) * 100 if rr else np.nan,
                             "trades_rerun": len(rr[1]) if rr else np.nan,
                             "topk_logshare%": order.logc.values[:kk].sum() / tot * 100 if kk else 0.0})
        d = pd.DataFrame([r for r in rows if r["lev"] == lev and r["buf"] == buf and r["rank_by"] == "ret"])
        fz = d[d["cum_factor%"] <= 0].k.min()
        fr = d[d["cum_rerun%"] <= 0].k.min()
        first.append({"lev": lev, "buf": buf, "first_k_factor<=0": "없음(0..10)" if pd.isna(fz) else int(fz),
                      "first_k_rerun<=0": "없음(0..10)" if pd.isna(fr) else int(fr),
                      "top5_logshare%": float(d[d.k == 5]["topk_logshare%"].iloc[0]), "n_trades": len(t)})
T = pd.DataFrame(rows)
T.to_csv(C.OUT / "topk.csv", index=False, encoding="utf-8-sig")
FZ = pd.DataFrame(first)
FZ.to_csv(C.OUT / "topk_first_zero.csv", index=False, encoding="utf-8-sig")
print(FZ.to_string(index=False))
print(T[(T.rank_by == "ret") & (T.k.isin([0, 3, 5, 10]))].pivot_table(index=["lev", "buf"], columns="k",
      values=["cum_factor%", "cum_rerun%"]).round(0).to_string())

# ── 실제 경로 재생: 거래별 최대 역행(MAE) 과 배율별 청산선까지의 여유 ──
h4f, bull, k, h4, fl = C.okx()
lo = h4.low.values
liq = []
for buf in (0, 2):
    t = C.run_okx(buf, 1)[1]
    for _, r in t.iterrows():
        a, b = int(r.i_in), int(r.i_out)
        f = fl.get(h4.index[a])
        seg_lo = [f[1]] if isinstance(f, tuple) else [lo[a]]
        seg_lo += list(lo[a + 1:b])                      # 청산 봉 b 는 시가 청산 → 제외
        mae = min(seg_lo) / r.px_in - 1
        liq.append({"buf": buf, "i_in": a, "entry": str(h4.index[a]), "mae%": mae * 100, "bars": int(r.bars)})
L = pd.DataFrame(liq)
out = []
for buf in (0, 2):
    m = L[L.buf == buf]["mae%"]
    for lev in (1, 2, 3, 4, 5):
        th = -(1 / E.exchange_lev(lev) - E.MMR) * 100
        out.append({"buf": buf, "lev": lev, "liq_line%": th, "worst_mae%": m.min(), "margin_to_liq_pp": m.min() - th,
                    "trades_within_10pp": int((m - th < 10).sum()), "n": len(m)})
LM = pd.DataFrame(out)
LM.to_csv(C.OUT / "liq_margin.csv", index=False, encoding="utf-8-sig")
L.to_csv(C.OUT / "mae_by_trade.csv", index=False, encoding="utf-8-sig")
print(LM.to_string(index=False))

# ── 가상 스트레스 (빈도는 실제 발생확률이 아니다) ──
st = []
for lev in (1, 2, 3, 5):
    th = 1 / E.exchange_lev(lev) - E.MMR
    for shock in (0.10, 0.20, 0.25, 0.30, 0.35, 0.50):
        st.append({"kind": "체결 직후 즉시 급락", "lev": lev, "shock%": shock * 100, "liquidated": shock >= th,
                   "account_loss%": 100.0 if shock >= th else lev * shock * 100})
keepC, keepF = C.B.COST, E.FUND
try:
    for cost in (0.0007, 0.001, 0.002):
        for fm in (1, 3):
            C.B.COST, E.FUND = cost, keepF * fm
            for lev in (3,):
                b, c = C.run_okx(0, lev), C.run_okx(2, lev)
                sb, sc = C.sharpe_cv(C.daily(b[0]).values), C.sharpe_cv(C.daily(c[0]).values)
                st.append({"kind": "비용·펀딩 스트레스", "lev": lev, "cost_one_way": cost, "fund_x": fm,
                           "base_sh": sb, "cand_sh": sc, "dSh": sc - sb, "base_trades": len(b[1]), "cand_trades": len(c[1])})
finally:
    C.B.COST, E.FUND = keepC, keepF
for lev in (1, 3, 5):
    for buf in (0, 2):
        r = C.rets(C.run_okx(buf, lev))
        s = m = 0; worst = 1.0; cur = 1.0
        for x in r:
            if x <= 0:
                s += 1; cur *= 1 + x
            else:
                s, cur = 0, 1.0
            m, worst = max(m, s), min(worst, cur)
        st.append({"kind": "연속 손실(실제 경로)", "lev": lev, "buf": buf, "max_streak": m, "worst_streak_loss%": (worst - 1) * 100})
S = pd.DataFrame(st)
S.to_csv(C.OUT / "stress.csv", index=False, encoding="utf-8-sig")
print(S.to_string(index=False))
