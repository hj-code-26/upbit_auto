"""체결 모델 비교 — 사전등록 (2026-09-28 실행 안전성 감사, 결과 보기 전 작성). 공식 성적표(dip_lev2_result.txt)·운영 코드는 건드리지 않는다.

질문: 공식 성적표는 '익절·손절가 그대로 체결'(A)을 가정한다. 실제 봇은 5분봉이 닫힌 15초 뒤에야 닿았는지 보고 시장가로 판다(B).
      거래소에 TP/SL 주문을 걸면(C) 지연은 사라지지만 손절 체결 미끄러짐이 생긴다. 모델에 따라 기대값이 얼마나 달라지나.

진입·신호: strategy.indicators / strategy.signal 그대로 (5분 종가가 1h EMA −2% 이하 & 전날 종가 50·200일선 위 → 다음 봉 시가 롱).
          세 모델은 청산 '판정 봉' 이 같다 (A 의 exit_check 규칙) → 진입 시각이 모두 같고, 거래가 1:1 짝지어진다.
  A 현행 성적표: 손절 min(손절가, 시가) · 익절 익절가 · 만기 864봉째 시가. 한 봉에 둘 다 → 손절. (dipbuy/strategy.on_bar 와 거래 단위 일치를 assert)
  B 현행 봇   : 판정 봉 j 가 닫힌 뒤 시장가 → 체결가 = j+1 봉 시가 (만기도 864봉째 봉이 닫힌 뒤 → 865봉째 시가).
  C 보호주문  : 손절 = 손절가 스톱 시장가 → min(손절가, 시가) × (1 − 미끄러짐 s) · 익절 = 익절가 지정가 (닿으면 체결로 본다)
               · 만기는 봇이 하므로 B 와 같다. 한 봉에 둘 다 닿은 '모호 봉' 은 순서를 모른다 →
               C-lo = 손절 먼저(보수) · C-hi = 익절 먼저(낙관) 두 값을 범위로만 낸다. 사실로 단정하지 않는다.
비용 (편도, 진입·청산 각각): 기본 0.07% (레포 표준 = 테이커 0.05 + 미끄러짐 0.02) · 중 0.12% · 고 0.20%.
펀딩 (롱이 낸다): 기본 0.01%/8h · 고 0.03%/8h.  C 손절 추가 미끄러짐 s: 0 · 0.1% · 0.3%.
데이터: dip_lev2.py 와 같은 BTC 3종 · 같은 구간 · 같은 일봉(비트스탬프로 200일선 예열).

측정 (기본 비용): 거래 수 · 익절/손절/만기 · 승률 · 거래당 순수익 · 1배 연복리 · MDD(보유 중 저가 평가손 포함) · 최장 회복 기간
      · 연도별 · 상위 3거래 제외 거래당 · B−A 짝 차이(평균·최악). 스트레스: 모델 × 비용 × 펀딩 거래당 순수익.

판정 (사전 고정):
  J1 B 가 성적표를 대표하는가 : 3종 모두 B 거래당 > 0 이고 B ≥ 0.5 × A  → '봉 마감 시장가 청산 허용 가능' / 아니면 '실계좌 전 보호주문 필요'
  J2 B 의 낙폭                : 3종 모두 B MDD 가 A 보다 5%p 넘게 깊지 않다
  J3 C 스트레스               : 3종 모두 C-lo(s 0.3%) 가 비용 중(0.12%) · 펀딩 고(0.03%/8h) 에서 거래당 > 0 → '보호주문 방식은 비용 스트레스를 견딘다'
  모호 봉 범위(C-lo~C-hi)는 판정에 쓰지 않고 보고만 한다.

한계 (5분 OHLC 로 알 수 없는 것 — 추정하지 않는다):
  · 15초 뒤 가격: B 의 체결가를 j+1 봉 '시가' 로 둔다. 15초 동안의 움직임·호가 스프레드는 비용 시나리오로만 흡수한다.
  · 봉 안 순서: 모호 봉의 TP/SL 선후, 진입 봉에서 진입 전(시가~15초)과 진입 후의 고저 구분은 알 수 없다 → 범위로만 본다.
  · 트리거 체결가: 스톱 시장가의 실제 체결가는 호가 깊이에 달렸다 → s 시나리오. 지정가 익절은 '닿으면 체결' 로 낙관적이다.
  · 펀딩은 고정률 근사다 (실제 8시간 부호·크기 기록 없음). 청산선은 1배라 없음.
  필요 데이터: 1분봉 이하 또는 체결(trade) 데이터 · OKX 펀딩 기록 · 호가 스냅숏. 이것 없이는 위 범위보다 정밀한 숫자를 만들지 않는다.

추가 사전등록 (2026-09-28, 2b (a)안 구현 뒤 · 이 모델들의 결과를 보기 전 작성. 위 J1~J3 과 그 결과는 바꾸지 않는다):
  질문: 봇이 이제 진입 봉을 건너뛰고 첫 온전한 봉부터 익절·손절·만기를 판정한다(autotrade.entry_bar = ceil). 이 편차의 손익 영향은?
  A2 모의 장부 : 판정은 진입 봉 다음 봉부터 · 체결은 A 와 같은 규칙(손절 min(손절가, 시가) · 익절가 · 만기 시가) · 만기 = 다음 봉부터 864봉.
  B2 현행 실봇 : 판정은 A2 와 같고 체결은 B 와 같다 (판정 봉이 닫힌 뒤 다음 봉 시가).
  판정 봉이 달라 청산 시각이 바뀌면 뒤 진입도 바뀔 수 있다 → A/B 와 1:1 짝이 아니다. 같은 진입 시각끼리만 짝 차이를 보고, 결과가 달라진 거래 수를 센다.
  판정 (사전 고정):
    J4 현행 실봇이 성적표를 대표하는가 : 3종 모두 B2 거래당 > 0 이고 B2 ≥ 0.5 × A → '진입 봉 건너뛰기 유지 가능' / 아니면 '(a)안 재검토'
    J5 현행 실봇 낙폭                 : 3종 모두 B2 MDD 가 A 보다 5%p 넘게 깊지 않다
  보고만: A2−A (모의 장부 vs 성적표) · B2−B (건너뛰기만의 효과) · 진입 봉 청산이던 거래의 A2/B2 결과.
  한계: 진입 봉에서 닿은 가격이 진입(시가+15초) 전인지 뒤인지는 여전히 모른다 — A 는 '전부 진입 뒤', A2 는 '판정 안 함' 이라 두 극단이다.
        MAE 는 진입 봉 저가를 포함한다(보유 중이므로 보수적).
사용: python research/aoa/exec_model.py   (결과 → research/aoa/exec_model_result.txt)
"""
import pathlib
import sys

import numpy as np
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[1]))
import features as F  # noqa: E402
import strategy as S  # noqa: E402
from clone import okx_5m  # noqa: E402

COSTS = {"기본0.07": 0.0007, "중0.12": 0.0012, "고0.20": 0.0020}
FUNDS = {"펀딩0.01": 0.0001, "펀딩0.03": 0.0003}
MODELS = [("A", 0.0, "lo"), ("B", 0.0, "lo"), ("C-lo s0", 0.0, "lo"), ("C-lo s0.1", 0.001, "lo"),
          ("C-lo s0.3", 0.003, "lo"), ("C-hi s0", 0.0, "hi")]


def trades(c, d1, lo, hi, skip=0):
    """판정 봉이 같은 거래 목록 → 거래마다 각 모델의 (청산가, 보유봉) · 사유 · MAE.
    skip=1 이면 진입 봉을 건너뛰고 다음 봉부터 판정·만기를 센다 (A2·B2 = 현행 autotrade.entry_bar)."""
    dev, bull = S.indicators(c, d1)
    sig = (dev.values <= -S.D) & bull.values
    o, h, l, idx = c.open.values, c.high.values, c.low.values, c.index
    bn = np.asarray((idx - idx[0]) // S.BAR)               # 봉 번호 = 시각 차이 (결측이 있어도 strategy.exit_check 와 같다)
    i, i1, n, out = idx.searchsorted(lo), idx.searchsorted(hi - S.BAR), len(o), []
    cand = np.flatnonzero(sig)
    while True:
        k = cand[np.searchsorted(cand, i)] if np.searchsorted(cand, i) < len(cand) else n
        if k >= i1 or k + 1 >= n:
            break
        e = k + 1; entry = o[e]; up, dn = S.levels(entry)
        j = t0 = e + skip
        while j < n:
            if bn[j] - bn[t0] >= S.MAXB:
                why = "만기"; break
            if l[j] <= dn:
                why = "손절"; break
            if h[j] >= up:
                why = "익절"; break
            j += 1
        if j + 1 >= n:                                     # 데이터 끝 — B 체결가(j+1 시가)가 없다
            break
        amb = why == "손절" and h[j] >= up and o[j] > dn    # 한 봉에 둘 다 닿았고 갭이 아니다 → 순서 모름
        a_px = o[j] if why == "만기" else min(dn, o[j]) if why == "손절" else up
        a_bars = j - e + (0 if why == "만기" else 1)
        r = dict(t0=idx[e], t1=idx[j], why=why, amb=amb, entry=entry, a_px=a_px, a_bars=a_bars,
                 b_px=o[j + 1], b_bars=j + 1 - e, sl_px=min(dn, o[j]), up=up,
                 mae=min(l[e:j + 1].min() / entry - 1, min(a_px, o[j + 1]) / entry - 1))
        out.append(r)
        i = j + 1
    return pd.DataFrame(out)


def net(tr, model, slip, side, cost, fund):
    if model == "A":
        px, bars = tr.a_px, tr.a_bars
    elif model == "B":
        px, bars = tr.b_px, tr.b_bars
    else:                                                  # C: 손절·익절은 거래소 주문, 만기는 봇(B)
        sl_px = tr.sl_px * (1 - slip)
        is_sl = (tr.why == "손절") & ~(tr.amb & (side == "hi"))
        is_tp = (tr.why == "익절") | (tr.amb & (side == "hi"))
        px = np.where(is_sl, sl_px, np.where(is_tp, tr.up, tr.b_px))
        bars = np.where(tr.why == "만기", tr.b_bars, tr.a_bars)
    return px / tr.entry - 1 - 2 * cost - fund * bars / 96


def curve(tr, r):
    """1배 복리 · MDD 는 보유 중 저가 평가손 포함 (dip_report.curve 와 같은 식) · 최장 회복 기간(일)."""
    eq, peak, mdd, path, peak_t, rec, dd = 1.0, 1.0, 0.0, [], tr.t0.iloc[0], pd.Timedelta(0), False
    for t1, x, mae in zip(tr.t1, r, tr.mae):
        mdd = min(mdd, eq * (1 + min(mae, x)) / peak - 1)
        eq *= 1 + x
        if eq >= peak:                                     # 회복 기간 = 청산 기준 고점 → 다시 고점까지
            rec = max(rec, t1 - peak_t) if dd else rec
            peak, peak_t, dd = eq, t1, False
        else:
            dd = True
        mdd = min(mdd, eq / peak - 1)
        path.append(eq)
    open_dd = tr.t1.iloc[-1] - peak_t if dd else pd.Timedelta(0)   # 끝까지 회복 못 한 구간
    return np.array(path), mdd, max(rec, open_dd).days, dd


def replay_check(c, d1, lo, hi, tr):
    """A 가 strategy.on_bar 재생과 거래 단위로 같은지 (진입 시각 · 청산가)."""
    dev, bull = S.indicators(c, d1)
    st, mine = dict(pending=False, t0=None, entry=None), []
    for t, o, h, l, dv, b in zip(c.index, c.open.values, c.high.values, c.low.values, dev.values, bull.values):
        if t < lo:
            continue
        if t >= hi and st["t0"] is None and not st["pending"]:
            break
        r = S.on_bar(st, t, o, h, l, dv if t < hi - S.BAR else 0, b)
        if r:
            mine.append((r[0], r[2]))
    n = min(len(mine), len(tr))
    assert [m[0] for m in mine[:n]] == list(tr.t0[:n]) and np.allclose([m[1] for m in mine[:n]], tr.a_px[:n]), "A ≠ on_bar"
    return len(mine)


if __name__ == "__main__":
    T = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731
    bs = pd.read_pickle(F.ROOT / "data_cache" / "bitstamp_1d_2016.pkl").close
    sets = [("BitMEX 18-21", F.candles().loc["2018-01-01":"2022-01-10"], "2018-03-05", "2022-01-01"),
            ("바이낸스 20-26", pd.read_pickle(F.ROOT / "data_cache" / "binance_btcusdt_5m_2020.pkl").asfreq("5min").ffill(),
             "2020-03-01", "2026-10-01"),
            ("OKX 21-26", okx_5m(), "2021-05-01", "2026-10-01")]
    lines, ev = [], {}
    P = lambda *a: (print(*a, flush=True), lines.append(" ".join(map(str, a))))  # noqa: E731
    P(__doc__.split("\n")[0])
    for name, c, lo, hi in sets:
        own = c.close.resample("1D").last()
        d1 = pd.concat([bs[bs.index < own.index[0]], own])
        tr = trades(c, d1, T(lo), T(hi))
        n_on = replay_check(c, d1, T(lo), T(hi), tr)
        yrs = (min(T(hi), c.index[-1]) - T(lo)).days / 365.25
        P(f"\n[{name}] {len(tr)}건 (on_bar 재생 {n_on}건 · 앞 {min(n_on, len(tr))}건 거래 단위 일치) · 사유 {tr.why.value_counts().to_dict()}"
          f" · 모호 봉(한 봉에 TP·SL) {int(tr.amb.sum())}건")
        base = {}

        def show(m, tr, r):
            """모델 한 줄 (기본 비용) + ev 기록."""
            p, mdd, rec, unrec = curve(tr, r)
            cagr = (p[-1] ** (1 / yrs) - 1) * 100
            yr = pd.Series(p, index=pd.DatetimeIndex(tr.t1)).groupby(tr.t1.dt.year.values).last()
            ys = " ".join(f"{y % 100:02d}:{(v / pv - 1) * 100:+.0f}" for (y, v), pv in zip(yr.items(), np.r_[1.0, yr.values[:-1]]))
            top = r.sort_values().iloc[:-3].mean() * 100 if len(r) > 3 else np.nan
            P(f"  {m:10s} 거래당 {r.mean() * 100:+.2f}% 승률 {(r > 0).mean():.0%} 연복리 {cagr:+.1f}% MDD {mdd * 100:.0f}%"
              f" 최장회복 {rec}일{'(미회복)' if unrec else ''} · 상위3 제외 {top:+.2f}% | {ys}")
            ev[(name, m, "기본0.07", "펀딩0.01")] = r.mean()
            ev[(name, m, "mdd")] = mdd

        for m, s, side in MODELS:
            r = net(tr, m.split()[0] if m in ("A", "B") else "C", s, side, 0.0007, 0.0001)
            r = pd.Series(np.asarray(r, float), index=tr.index)
            base[m] = r
            show(m, tr, r)
        d = (base["B"] - base["A"]) * 100
        P(f"  B−A 짝 차이: 평균 {d.mean():+.2f}%p · 최악 {d.min():+.2f}%p · 최선 {d.max():+.2f}%p · 사유별 "
          + " ".join(f"{w} {d[tr.why == w].mean():+.2f}" for w in ("익절", "손절", "만기") if (tr.why == w).any()))
        P("  스트레스 거래당 (%):   " + "  ".join(f"{ck}/{fk}" for ck in COSTS for fk in FUNDS))
        for m, s, side in MODELS:
            vals = []
            for ck, cv in COSTS.items():
                for fk, fv in FUNDS.items():
                    x = float(np.mean(net(tr, m if m in ("A", "B") else "C", s, side, cv, fv)))
                    ev[(name, m, ck, fk)] = x
                    vals.append(f"{x * 100:+.2f}")
            P(f"    {m:10s} " + "  ".join(f"{v:>16s}" for v in vals))
        # ── 사후 보충 (결과를 본 뒤 추가 · 사전등록 아님 · 판정에 쓰지 않는다): 평균이 가리는 꼬리 ──
        w = tr[d < -2]
        P(f"  [사후 보충] B 최악 거래 {base['B'].min() * 100:+.2f}% (A {base['A'].min() * 100:+.2f}%) · B−A ≤ −2%p: "
          + ", ".join(f"{t:%Y-%m-%d} {y} {d[i]:+.1f}%p" for i, t, y in zip(w.index, w.t1, w.why)))
        low = c.low.reindex(tr.t1).values                  # 손절 스톱의 최악 체결 한계 = 그 봉 저가 (실제 체결은 손절가~저가 사이)
        worst = np.where(tr.why == "손절", low, np.where(tr.why == "익절", tr.up, tr.b_px)) / tr.entry - 1 \
            - 2 * 0.0007 - 0.0001 * np.where(tr.why == "만기", tr.b_bars, tr.a_bars) / 96
        P(f"  [사후 보충] C 손절을 그 봉 저가로 체결(최악 한계): 거래당 {worst.mean() * 100:+.2f}% · 최악 거래 {worst.min() * 100:+.2f}%")
        # ── 추가 사전등록: 진입 봉 건너뛰기 (A2 모의 장부 · B2 현행 실봇) ──
        t2 = trades(c, d1, T(lo), T(hi), skip=1)
        P(f"  [진입 봉 건너뛰기] {len(t2)}건 · 사유 {t2.why.value_counts().to_dict()}")
        skip = {}
        for m, src in (("A2", "A"), ("B2", "B")):
            skip[m] = pd.Series(np.asarray(net(t2, src, 0.0, "lo", 0.0007, 0.0001), float), index=t2.index)
            show(m, t2, skip[m])
        vals = {m: [f"{float(np.mean(net(t2, src, 0.0, 'lo', cv, fv))) * 100:+.2f}" for cv in COSTS.values() for fv in FUNDS.values()]
                for m, src in (("A2", "A"), ("B2", "B"))}
        for m, v in vals.items():
            P(f"    {m:10s} " + "  ".join(f"{x:>16s}" for x in v))
        k1, k2 = tr.assign(A=base["A"], B=base["B"]).set_index("t0"), t2.assign(A2=skip["A2"], B2=skip["B2"]).set_index("t0")
        both = k1.join(k2[["why", "t1", "A2", "B2"]], rsuffix="2", how="inner")
        moved = both[(both.why != both.why2) | (both.t1 != both.t12)]
        P(f"  같은 진입 {len(both)}건 (A 만 {len(k1) - len(both)} · 건너뛰기만 {len(k2) - len(both)}) · 결과 달라짐 {len(moved)}건"
          f" · 짝 차이 A2−A {(both.A2 - both.A).mean() * 100:+.2f}%p · B2−B {(both.B2 - both.B).mean() * 100:+.2f}%p")
        for t, r in both[both.index == both.t1].iterrows():  # A 에서 진입 봉 안에 청산된 거래
            P(f"    진입 봉 청산 {t:%Y-%m-%d %H:%M} A {r.why} {r.A * 100:+.2f}% → A2 {r.why2} {r.A2 * 100:+.2f}% · B2 {r.B2 * 100:+.2f}%"
              f" ({(r.t12 - t) / pd.Timedelta('1h'):.1f}시간 보유)")
    names = [s[0] for s in sets]
    j1 = all(ev[(n, "B", "기본0.07", "펀딩0.01")] > 0 and ev[(n, "B", "기본0.07", "펀딩0.01")] >= 0.5 * ev[(n, "A", "기본0.07", "펀딩0.01")] for n in names)
    j2 = all(ev[(n, "B", "mdd")] >= ev[(n, "A", "mdd")] - 0.05 for n in names)
    j3 = all(ev[(n, "C-lo s0.3", "중0.12", "펀딩0.03")] > 0 for n in names)
    P(f"\n판정: J1 B 가 성적표 대표 {'통과 → 봉 마감 시장가 청산 허용 가능' if j1 else '불합격 → 실계좌 전 보호주문 필요'}"
      f" · J2 B 낙폭 {'통과' if j2 else '불합격'} · J3 C 스트레스 {'통과' if j3 else '불합격'}")
    b2 = [ev[(n, "B2", "기본0.07", "펀딩0.01")] for n in names]
    j4 = all(x > 0 and x >= 0.5 * ev[(n, "A", "기본0.07", "펀딩0.01")] for n, x in zip(names, b2))
    j5 = all(ev[(n, "B2", "mdd")] >= ev[(n, "A", "mdd")] - 0.05 for n in names)
    P(f"추가 판정: J4 현행 실봇(B2) 이 성적표 대표 {'통과 → 진입 봉 건너뛰기 유지 가능' if j4 else '불합격 → (a)안 재검토'}"
      f" · J5 B2 낙폭 {'통과' if j5 else '불합격'}")
    (HERE / "exec_model_result.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
