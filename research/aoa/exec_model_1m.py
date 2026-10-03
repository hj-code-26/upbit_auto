"""체결 모델 1분 해상도 재측정 — 사전등록 (2026-09-30 실행 안전성 감사, 결과 보기 전 작성). 운영 코드·공식 성적표·exec_model 결과는 건드리지 않는다.
※ 이번 감사는 커밋 금지 지시라 이 설계를 결과 전에 커밋하지 못했다. 결과 파일 첫 줄에 이 파일의 sha256 을 남긴다.

왜: exec_model.py (09-28) 는 5분 OHLC 로 A/B/C 를 비교했고 한계에 '1분봉 이하 필요' 를 적었다. OKX BTC-USDT-SWAP 체결가 1분봉
    (data_cache/okx_1m.pkl, 2021-03 ~ 2026-09-08) 이 있으므로 OKX 한 종에서 그 한계 중 셋을 좁힌다:
    (1) 모호 봉(한 5분봉에 TP·SL)의 선후  (2) 보호주문의 트리거 분  (3) 현행 실봇이 봉 마감 15초 뒤 내는 시장가의 가격 범위.
진입: exec_model.trades 그대로 (strategy.indicators/signal, 신호 다음 5분봉 시가 = 진입가). 5분봉은 1분봉을 모은 clone.okx_5m.
  거래 목록 두 벌: 성적표 기준(skip=0 → A·C5·C1) · 현행 실봇 기준(skip=1, autotrade.entry_bar → B2·B2-lo·B2-hi).
모델
  A      성적표 (exec_model 과 같다): 손절 min(손절가, 시가) · 익절 익절가 · 만기 864봉째 시가 · 한 봉에 둘 다 → 손절.
  C5     exec_model 의 보호주문 모델을 같은 거래에 다시 (5분 판정, 모호 봉 손절 먼저, 손절 × (1−s), 익절 = 익절가).
  C1     보호주문 1분 판정: 진입가·익절가·손절가는 A 와 같다. 1분봉 last 가격으로 첫 터치 분을 찾는다.
         손절 = min(손절가, 그 분 시가) × (1−s) · 익절 = 익절가 (C5 와 같은 규칙 — 해상도 효과만 떼어 본다. 실제 protect.py 는 트리거 시장가라
         익절에도 미끄러짐이 있다 → 비용 시나리오로만 흡수) · 만기 = 봇(B2 와 같은 시각: 진입 봉 건너뛴 864봉째 봉 마감 뒤 다음 5분봉 시가).
         진입 분(첫 1분봉)에는 진입(15초 뒤) 전 가격이 섞여 있다 → 손절은 본다(보수) · 익절은 안 본다(보수).
         한 1분봉에 둘 다 → C1-lo 손절 먼저(판정용) · C1-hi 익절 먼저(범위 보고만).
  B2     현행 실봇 (exec_model 과 같다): 진입 봉 건너뛰고 5분봉 마감 판정 → 다음 5분봉 시가 체결.
  B2-lo/hi  B2 와 판정 같고 체결가만 '판정 봉 마감 뒤 첫 1분봉' 의 저가 / 고가 — 15초(+검토 없음) 뒤 가격은 이 범위 안이라는 것만 안다.
비용 (exec_model 과 같은 격자): 편도 0.07 · 0.12 · 0.20 % × 펀딩(롱 부담) 0.01 · 0.03 %/8h. 손절 추가 미끄러짐 s: 0 · 0.1 · 0.3 %.
측정 (기본 0.07·0.01): 거래 수 · 익절/손절/만기 · 승률 · 거래당 순수익(t) · 1배 연복리 · MDD(보유 중 저가 포함) · 최장 회복 · 연도별
      · 상위 3 제외 거래당 · 같은 진입 짝 차이 (C1−A · C1−C5 · B2−A). 보고: 5분 모호 봉이 1분으로 풀린 수 · 1분 모호 수
      · B2 거래 중 진입 봉의 진입 뒤 1~4분에 손절가를 터치한 수 (현행 실봇이 못 보는 구간).
판정 (사전 고정 — OKX 한 종·거래 수십 건이라 '보강 근거' 일 뿐 단독 채택 근거가 아니다):
  K1 5분 C 결과가 1분에서도 유지되는가 : |C1-lo(s0.1) − C5-lo(s0.1)| 거래당 ≤ 0.10%p → '5분 결과 유지' / 아니면 '5분 C 결과 정정 필요'
  K2 보호주문 비용 스트레스           : C1-lo(s0.3) 이 비용 중 0.12 · 펀딩 0.03 에서 거래당 > 0
  K3 보호주문이 현행 실봇보다 나은가   : 기본 비용에서 C1-lo(s0.1) 거래당 ≥ B2 거래당 이고 MDD 가 B2 보다 5%p 넘게 깊지 않다
                                         → '연결 이득 있음(OKX 한 종)' / 아니면 '이득 입증 안 됨'
  K4 현행 실봇 체결 분 불확실성        : 기본 비용에서 B2-lo 거래당 > 0 → '체결 분 범위의 나쁜 끝에서도 양수'
한계 (추정하지 않는다): T+15초·트리거 순간 가격 자체는 모른다 (1분봉 범위뿐) · 1분봉 안 선후 모름 · 호가 깊이 없음 (s 시나리오)
      · 펀딩 고정률 (OKX 실측은 3개월뿐) · OKX 1종 · 1분 데이터가 2026-09-08 에서 끝난다 · Claude 검토 지연(최대 90초)은 진입가에 반영 안 함.
사용: python research/aoa/exec_model_1m.py   (결과 → research/aoa/exec_model_1m_result.txt)
"""
import hashlib
import pathlib
import sys

import numpy as np
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[1]))
import exec_model as M  # noqa: E402
import features as F  # noqa: E402
import strategy as S  # noqa: E402
from clone import okx_5m  # noqa: E402

BASE_C, BASE_F = 0.0007, 0.0001
MIN = pd.Timedelta("1min")


def c1(tr, m1):
    """성적표 거래 목록(skip=0) 마다 1분봉으로 보호주문 청산 → 행마다 (사유, 청산 분 시가, 모호, 보유 5분봉 수, MAE, 청산 시각)."""
    o, h, l, idx = m1.open.values, m1.high.values, m1.low.values, m1.index
    rows = []
    for t0, entry, up in zip(tr.t0, tr.entry, tr.up):
        dn = S.levels(entry)[1]
        p0 = idx.searchsorted(t0)
        tx = t0 + (1 + S.MAXB + 1) * S.BAR                    # 봇 만기 체결 시각 (B2 와 같다)
        px_ = min(idx.searchsorted(tx), len(o) - 1)
        lo_, hi_ = l[p0:px_], h[p0:px_]
        a = np.flatnonzero(lo_ <= dn)
        b = np.flatnonzero(hi_ >= up)
        b = b[b > 0]                                          # 진입 분에는 익절을 안 본다
        ia, ib = (a[0] if len(a) else np.inf), (b[0] if len(b) else np.inf)
        if ia == np.inf and ib == np.inf:
            why, k, amb = "만기", px_, False
        elif ia <= ib:
            why, k, amb = "손절", p0 + int(ia), ia == ib
        else:
            why, k, amb = "익절", p0 + int(ib), False
        held = l[p0:k + 1] if why != "만기" else l[p0:k]
        rows.append(dict(why1=why, o1=o[k], amb1=amb, bars1=(idx[k] - t0) / S.BAR, t1_1=idx[k],
                         mae1=held.min() / entry - 1 if len(held) else 0.0))
    return pd.DataFrame(rows, index=tr.index)


def net_c1(tr, x, s, side, cost, fund):
    dn = tr.entry * (1 - S.SL / 100)
    sl_px = np.minimum(dn, x.o1) * (1 - s)
    is_tp = (x.why1 == "익절") | (x.amb1 & (side == "hi"))
    is_sl = (x.why1 == "손절") & ~(x.amb1 & (side == "hi"))
    px = np.where(is_sl, sl_px, np.where(is_tp, tr.up, x.o1))
    return pd.Series(px / tr.entry - 1 - 2 * cost - fund * x.bars1 / 96, index=tr.index)


def b2_range(tb, m1, cost, fund, end):
    """B2 체결가를 판정 봉 마감 뒤 첫 1분봉의 저가(lo)·고가(hi) 로."""
    t = tb.t1 + S.BAR
    k = m1.index.searchsorted(t)
    ok = k < len(m1)
    k = np.minimum(k, len(m1) - 1)
    lo_px, hi_px = m1.low.values[k], m1.high.values[k]
    f = lambda px: pd.Series(np.where(ok, px, tb.b_px) / tb.entry - 1 - 2 * cost - fund * tb.b_bars / 96, index=tb.index)  # noqa: E731
    return f(lo_px), f(hi_px)


if __name__ == "__main__":
    sha = hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()[:16]
    T = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731
    m1 = pd.read_pickle(F.ROOT / "data_cache" / "okx_1m.pkl").astype(float).sort_index()
    m1 = m1[~m1.index.duplicated()]
    gap = m1.index.to_series().diff() > MIN
    end = gap[gap].index[0] if gap.any() else m1.index[-1]    # 첫 결측 앞까지만 (1분 연속성 보장)
    m1 = m1[m1.index < end]
    c = okx_5m()
    c = c[c.index < end.floor(S.BAR)]
    bs = pd.read_pickle(F.ROOT / "data_cache" / "bitstamp_1d_2016.pkl").close
    own = c.close.resample("1D").last()
    d1 = pd.concat([bs[bs.index < own.index[0]], own])
    lo, hi = T("2021-05-01"), end - 4 * pd.Timedelta("1D")    # 만기(3일)+여유가 데이터 안에 들어가게
    yrs = (hi - lo).days / 365.25
    lines = []
    P = lambda *a: (print(*a, flush=True), lines.append(" ".join(map(str, a))))  # noqa: E731
    P(__doc__.split("\n")[0])
    P(f"설계 파일 sha256 {sha} · OKX 1분봉 {m1.index[0]:%Y-%m-%d} ~ {m1.index[-1]:%Y-%m-%d %H:%M} (첫 결측 앞) · 진입 구간 {lo:%Y-%m-%d} ~ {hi:%Y-%m-%d}")

    tr = M.trades(c, d1, lo, hi)
    n_on = M.replay_check(c, d1, lo, hi, tr)
    tb = M.trades(c, d1, lo, hi, skip=1)
    x = c1(tr, m1)
    P(f"\n성적표 기준 {len(tr)}건 (on_bar 재생 일치 {min(n_on, len(tr))}건) · 사유 A {tr.why.value_counts().to_dict()} · C1 {x.why1.value_counts().to_dict()}")
    P(f"  5분 모호 봉 {int(tr.amb.sum())}건 → 1분으로 푼 결과: " + (", ".join(f"{t:%Y-%m-%d} {w}" for t, w in zip(tr.t1[tr.amb], x.why1[tr.amb])) or "없음")
      + f" · 1분 모호 {int(x.amb1.sum())}건")
    later = (x.t1_1 > tr.t1 + S.BAR).sum()
    P(f"  C1 청산 분이 A 판정 봉보다 늦은 거래 {later}건 (진입 분 익절 제외·만기 규칙 차이) — 고정 진입 비교라 뒤 진입은 A 목록 그대로")
    ent = tb.t0.values
    pre = []
    for t0, entry in zip(tb.t0, tb.entry):                    # 진입 봉(5분) 안 진입 뒤 1~4분의 손절 터치 — 현행 봇이 못 보는 구간
        seg = m1.loc[t0 + MIN: t0 + S.BAR - MIN]
        pre.append(bool((seg.low <= S.levels(entry)[1]).any()))
    P(f"현행 실봇 기준 {len(tb)}건 · 사유 {tb.why.value_counts().to_dict()} · 진입 봉 진입 뒤 1~4분 손절가 터치 {sum(pre)}건")

    res, curves = {}, {}

    def show(name, frame, r, mae=None):
        r = pd.Series(np.asarray(r, float), index=frame.index)
        f2 = frame.assign(mae=frame.mae if mae is None else mae)
        p, mdd, rec, unrec = M.curve(f2, r)
        cagr = (p[-1] ** (1 / yrs) - 1) * 100
        yr = pd.Series(p, index=pd.DatetimeIndex(frame.t1)).groupby(frame.t1.dt.year.values).last()
        ys = " ".join(f"{y % 100:02d}:{(v / pv - 1) * 100:+.0f}" for (y, v), pv in zip(yr.items(), np.r_[1.0, yr.values[:-1]]))
        top = r.sort_values().iloc[:-3].mean() * 100 if len(r) > 3 else np.nan
        tval = r.mean() / (r.std(ddof=1) / np.sqrt(len(r))) if len(r) > 1 else np.nan
        P(f"  {name:12s} {len(r):>3}건 거래당 {r.mean() * 100:+.2f}% (t {tval:+.1f}) 승률 {(r > 0).mean():.0%} 연복리 {cagr:+.1f}% MDD {mdd * 100:.0f}%"
          f" 최장회복 {rec}일{'(미회복)' if unrec else ''} · 상위3 제외 {top:+.2f}% | {ys}")
        res[name], curves[name] = r, mdd

    P("\n[기본 비용 0.07% · 펀딩 0.01%/8h]")
    mae1 = np.minimum(x.mae1, 0)
    show("A", tr, M.net(tr, "A", 0, "lo", BASE_C, BASE_F))
    for s in (0.0, 0.001, 0.003):
        show(f"C5-lo s{s * 100:g}", tr, M.net(tr, "C", s, "lo", BASE_C, BASE_F))
    for s in (0.0, 0.001, 0.003):
        show(f"C1-lo s{s * 100:g}", tr, net_c1(tr, x, s, "lo", BASE_C, BASE_F), mae1)
    show("C1-hi s0", tr, net_c1(tr, x, 0.0, "hi", BASE_C, BASE_F), mae1)
    show("B2", tb, M.net(tb, "B", 0, "lo", BASE_C, BASE_F))
    blo, bhi = b2_range(tb, m1, BASE_C, BASE_F, end)
    show("B2-lo", tb, blo)
    show("B2-hi", tb, bhi)
    d = lambda a, b: (res[a] - res[b]) * 100  # noqa: E731
    P(f"  짝 차이 (같은 진입): C1-lo s0.1 − A 평균 {d('C1-lo s0.1', 'A').mean():+.2f}%p 최악 {d('C1-lo s0.1', 'A').min():+.2f}%p"
      f" · C1-lo s0.1 − C5-lo s0.1 평균 {d('C1-lo s0.1', 'C5-lo s0.1').mean():+.3f}%p (다른 거래 {int((d('C1-lo s0.1', 'C5-lo s0.1').abs() > 1e-9).sum())}건)"
      f" · B2-hi − B2-lo 평균 {d('B2-hi', 'B2-lo').mean():+.2f}%p")

    P("\n[스트레스 — 거래당 %]  " + "  ".join(f"{ck}/{fk}" for ck in M.COSTS for fk in M.FUNDS))
    grid = {}
    for name in ("A", "C5-lo s0.3", "C1-lo s0", "C1-lo s0.1", "C1-lo s0.3", "B2", "B2-lo"):
        vals = []
        for ck, cv in M.COSTS.items():
            for fk, fv in M.FUNDS.items():
                if name == "A":
                    r = M.net(tr, "A", 0, "lo", cv, fv)
                elif name.startswith("C5"):
                    r = M.net(tr, "C", 0.003, "lo", cv, fv)
                elif name.startswith("C1"):
                    r = net_c1(tr, x, float(name.split("s")[-1]) / 100, "lo", cv, fv)
                elif name == "B2":
                    r = M.net(tb, "B", 0, "lo", cv, fv)
                else:
                    r = b2_range(tb, m1, cv, fv, end)[0]
                grid[(name, ck, fk)] = float(np.mean(r))
                vals.append(f"{grid[(name, ck, fk)] * 100:+.2f}")
        P(f"    {name:12s} " + "  ".join(f"{v:>16s}" for v in vals))

    k1 = abs(res["C1-lo s0.1"].mean() - res["C5-lo s0.1"].mean()) <= 0.001
    k2 = grid[("C1-lo s0.3", "중0.12", "펀딩0.03")] > 0
    k3 = res["C1-lo s0.1"].mean() >= res["B2"].mean() and curves["C1-lo s0.1"] >= curves["B2"] - 0.05
    k4 = res["B2-lo"].mean() > 0
    P(f"\n판정: K1 {'5분 결과 유지' if k1 else '5분 C 결과 정정 필요'} · K2 보호주문 비용 스트레스 {'통과' if k2 else '불합격'}"
      f" · K3 {'연결 이득 있음(OKX 한 종)' if k3 else '이득 입증 안 됨'} · K4 {'체결 분 나쁜 끝에서도 양수' if k4 else '불합격 — 체결 분 나쁜 끝에서 음수'}")
    (HERE / "exec_model_1m_result.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
