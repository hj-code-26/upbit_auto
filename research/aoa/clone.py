"""aoa 패턴 복제 전략 백테스트 — 사전등록 (2026-09-26, 결과 보기 전에 작성).

복제 대상 (research/aoa 분석에서 확인된 것):
  · 방향: 1시간 역추세 — 가격이 1h 지수이평(EMA 12봉) 아래로 D% 이상 벗어나면 롱, 위로 D% 이상이면 숏
  · 방아쇠: 변동성 급증 (rv_ratio = 최근 1h 실현변동성 / 하루 평균 1h 변동성) > 1.5  [spike=on 일 때만]
  · 물타기: 첫 진입보다 D% 불리해지면 같은 크기로 1회 추가 (1배 → 2배)          [add=on 일 때만]
  · 청산: 평균단가 대비 익절 TP / 손절 SL, 시간 손절 1일(288봉)
      exit a: TP = D/2, SL = 1.5·D      exit b: TP = D, SL = 2·D
체결: 신호는 5분봉 종가에서 판정 → 다음 봉 시가에 진입. TP/SL 은 이후 봉 고가/저가로 판정, 한 봉에서 둘 다 닿으면 손절로 본다.
비용: 편도 COST (기본 0.07% = 레포 표준, OKX 테이커+슬리피지). 비교용 0% 도 같이 낸다.

조합 24개 = D ∈ {0.5, 1, 2}% × spike ∈ {off, on} × exit ∈ {a, b} × add ∈ {off, on}.  양방향 / 롱만 둘 다 보고.
구간: 개발 IS = BitMEX 2018-03~2021-12 (그가 매매한 시장) · 검증 OOS = OKX 2022-01~2026-09 (처음 보는 데이터).

채택 기준 (사전 고정): 비용 0.07% 에서 ① IS 누적 > 0 ② OOS 전반(2022-01~2024-04)·후반(2024-05~) 둘 다 > 0
  ③ 이 조건을 만족하는 조합이 24개 중 과반(13개 이상) — 최고 조합 하나만 좋은 건 채택 사유가 아니다.
  하나라도 어기면 "봇 이식 불가"로 기록한다.
"""
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import features as F  # noqa: E402

ROOT = F.ROOT
COST = 0.0007
DAY = 288


def okx_5m():
    m = pd.read_pickle(ROOT / "data_cache" / "okx_1m.pkl").astype(float)
    c = m.resample("5min").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    return c.dropna(subset=["close"]).asfreq("5min").ffill()


def prep(c):
    cl = c.close
    dev = (cl / cl.ewm(span=12, adjust=False).mean() - 1) * 100
    lr = np.log(cl).diff()
    rv_ratio = (lr.rolling(12).std() * np.sqrt(12)) / (lr.rolling(288).std() * np.sqrt(288) / np.sqrt(24))
    return dict(o=c.open.values, h=c.high.values, l=c.low.values, dev=dev.values, rvr=rv_ratio.values, idx=c.index)


def run(p, D, spike, exit_, add, long_only, cost=COST):
    """→ 거래 목록 [(진입시각 index, 방향, 순수익률 %)] — 자본 대비 (1배 단위, 추가 시 2배)."""
    o, h, l, dev, rvr = p["o"], p["h"], p["l"], p["dev"], p["rvr"]
    tp_k, sl_k = (0.5, 1.5) if exit_ == "a" else (1.0, 2.0)
    d = D / 100
    out, n, i = [], len(o), 300
    while i < n - 1:
        s = 0
        if not (spike and not rvr[i] > 1.5):
            if dev[i] <= -D:
                s = 1
            elif dev[i] >= D and not long_only:
                s = -1
        if s == 0:
            i += 1
            continue
        e0 = o[i + 1]; units = 1.0; avg = e0; added = False; j = i + 1; pnl = None
        tp = avg * (1 + s * tp_k * d); sl = avg * (1 - s * sl_k * d)
        addpx = e0 * (1 - s * d)
        while j < n:
            hit_add = add and not added and (l[j] <= addpx if s == 1 else h[j] >= addpx)
            if hit_add:                                   # 추가 진입 후 평균단가·목표 재계산
                added = True; avg = (avg + addpx) / 2; units = 2.0
                tp = avg * (1 + s * tp_k * d); sl = avg * (1 - s * sl_k * d)
            hit_sl = l[j] <= sl if s == 1 else h[j] >= sl
            hit_tp = h[j] >= tp if s == 1 else l[j] <= tp
            if hit_sl:
                px = sl
            elif hit_tp and not hit_add:                  # 추가한 봉에서 바로 익절은 인정하지 않는다(순서 모름)
                px = tp
            elif j - i >= DAY:
                px = o[j]
            else:
                j += 1
                continue
            pnl = units * (s * (px / avg - 1) - 2 * cost)
            break
        if pnl is None:
            break
        out.append((i, s, pnl * 100))
        i = j + 1
    return out


def stats(tr, idx, lo, hi):
    r = np.array([p for i, s, p in tr if lo <= idx[i] < hi])
    if len(r) == 0:
        return dict(n=0, ret=0.0, mdd=0.0, win=np.nan)
    eq = np.cumprod(1 + r / 100)
    mdd = (eq / np.maximum.accumulate(eq) - 1).min() * 100
    return dict(n=len(r), ret=(eq[-1] - 1) * 100, mdd=mdd, win=(r > 0).mean())


if __name__ == "__main__":
    T = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731
    P_IS = prep(F.candles().loc["2018-02-01":"2021-12-31"])
    P_OOS = prep(okx_5m().loc["2021-12-01":])
    rows = []
    for long_only in (False, True):
        for D in (0.5, 1.0, 2.0):
            for spike in (False, True):
                for exit_ in ("a", "b"):
                    for add in (False, True):
                        k = dict(D=D, spike=spike, exit_=exit_, add=add, long_only=long_only)
                        a = run(P_IS, **k); b = run(P_OOS, **k)
                        g0 = run(P_IS, **k, cost=0)
                        r = dict(side="롱만" if long_only else "양방향", D=D, spike=spike, exit=exit_, add=add)
                        s_is = stats(a, P_IS["idx"], T("2018-03-05"), T("2022-01-01"))
                        s_g = stats(g0, P_IS["idx"], T("2018-03-05"), T("2022-01-01"))
                        s1 = stats(b, P_OOS["idx"], T("2022-01-01"), T("2024-05-01"))
                        s2 = stats(b, P_OOS["idx"], T("2024-05-01"), T("2027-01-01"))
                        r.update(IS_n=s_is["n"], IS_win=s_is["win"], IS_gross=s_g["ret"], IS=s_is["ret"], IS_mdd=s_is["mdd"],
                                 OOS1=s1["ret"], OOS2=s2["ret"], OOS_win=stats(b, P_OOS["idx"], T("2022-01-01"), T("2027-01-01"))["win"],
                                 OOS_mdd=min(s1["mdd"], s2["mdd"]))
                        r["pass"] = r["IS"] > 0 and r["OOS1"] > 0 and r["OOS2"] > 0
                        rows.append(r)
                        print(r, flush=True)
    df = pd.DataFrame(rows)
    pd.set_option("display.width", 250)
    txt = df.round(3).to_string()
    for side, g in df.groupby("side"):
        verdict = "채택 후보" if g["pass"].sum() >= 13 else "봇 이식 불가"
        txt += f"\n{side}: 통과 {int(g['pass'].sum())}/24 → {verdict}"
    print(txt)
    (ROOT / "research" / "aoa" / "clone_result.txt").write_text(txt, encoding="utf-8")
