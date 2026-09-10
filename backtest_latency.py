"""갱신주기(INTERVAL_MIN)와 AI 검토 위치를 정하기 위한 지연 비용 측정.

두 질문 다 결국 같은 것을 묻는다 — **체결이 몇 분 늦어지면 얼마를 잃는가.**

  ① 갱신주기: 봇은 INTERVAL_MIN 분마다만 깨어난다. 그래서
     · 진입: 분봉이 하한을 되찾은 순간이 아니라 '그 다음 사이클' 에 산다
     · 청산: 4h 봉이 닫힌 순간이 아니라 '그 다음 사이클' 에 판다
  ② AI 검토 위치: 지금은 4h 신호가 나면 곧바로 Claude 를 부르고, 승인되면 대기하다가 하한 재확보 때 산다.
     검토와 실제 체결 사이가 최대 4시간이라 '검토 시점의 판단' 이 낡는다.
     대안은 하한 재확보가 확인된 **직후** 부르는 것 — 대신 호출에 걸리는 시간(수십 초~2분)만큼 체결이 늦는다.
     그 늦어짐의 비용이 여기서 나온다.

분봉 전량 캐시(data_cache/okx_1m.pkl)를 써서 실제 체결가를 분 단위로 다시 잡는다.
사용: python backtest_latency.py [편도비용]
"""
import sys

import numpy as np
import pandas as pd

import backtest_entry as E
import backtest_quant as Q
import minute_data as MD
import model as M

DELAYS = (0, 1, 2, 5, 10, 15, 30, 60)     # 분


def reclaim_at(w, lvl):
    """하한을 되찾은 시각 → (시각, 창 시작부터 지켜졌는가). 못 되찾으면 (None, False).
    backtest_entry.reclaim_at 과 같은 판정이되 '가격' 이 아니라 '시각' 을 돌려준다."""
    lost = (w.low < lvl).values
    if not lost.any():
        return w.index[0], True
    back = w.index[(w.close > lvl) & (w.index > w.index[int(lost.argmax())])]
    return (back[0], False) if len(back) else (None, False)


def price_at(w, m1, at, at_start):
    """그 시각의 체결가. 창 시작 그대로면 시장가 = 창 첫 분봉의 시가 (backtest_entry 와 같은 규약),
    그 밖에는 해당 분봉 종가."""
    if at_start:
        return float(w.open.iloc[0])
    px = m1.close.asof(at)
    return float(px) if np.isfinite(px) else None


def make_fills(evs, mins, m1, delay=0, interval=0):
    """delay 분 뒤 체결(AI 호출 지연) 또는 interval 분 격자의 다음 폴링에 체결(갱신주기).

    폴링 격자는 4h 경계와 무관한 벽시계에 걸려 있으므로, 이벤트마다 위상(phase)을 달리 준다.
    격자가 창 시작에 딱 맞는다고 가정하면 주기가 짧을수록 유리하게 나오는 편향이 생긴다."""
    out = {}
    for n, (t, i, _, hi, _) in enumerate(evs):
        w = mins[t][1]
        if len(w) < 10:
            continue
        at, at_start = reclaim_at(w, hi)
        if at is None:
            out[t] = None
            continue
        start, deadline = w.index[0], w.index[0] + pd.Timedelta(hours=E.WIN_H)
        if interval:
            phase = (n * 7) % interval                      # 이벤트마다 다른 위상 (평균적으로 주기의 절반 지연)
            k = int(np.ceil(((at - start).total_seconds() / 60 - phase) / interval))
            at = start + pd.Timedelta(minutes=phase + max(k, 0) * interval)
            at_start = at == start
        if delay:
            at = at + pd.Timedelta(minutes=delay)
            at_start = False
        if at >= deadline:
            out[t] = None                                   # 늦어져서 창을 넘겼다 → 자리 포기
            continue
        out[t] = price_at(w, m1, at, at_start)
    return out


def to_bar_keys(fills, evs, h4):
    return {h4.index[i]: fills[t] for t, i, _, _, _ in evs if t in fills}


def exit_cost(h4, exit_bars, m1, interval):
    """청산 지연 비용: 4h 봉 종가에 신호 → 백테스트는 다음 봉 시가에 판다.
    실제로는 봉 마감 뒤 최대 interval 분 지나서 판다. 그 차이의 평균(bp)."""
    d = []
    for t in exit_bars:
        base = m1.close.asof(t)                                  # 봉 마감 시각 = 다음 봉 시가와 사실상 같다
        late = m1.close.asof(t + pd.Timedelta(minutes=interval))
        if np.isfinite(base) and np.isfinite(late) and base > 0:
            d.append((late / base - 1) * 10_000)                 # 롱 청산이므로 늦게 팔아 값이 낮으면 손해
    a = np.array(d)
    return a.mean(), np.median(a), len(a)


def main():
    evs, h4 = E.events()
    mins = E.minutes(evs)
    m1 = MD._load()[0]
    m1 = m1[~m1.index.duplicated()].sort_index()
    okx = h4[h4.index >= pd.Timestamp(Q.START, tz="UTC")]
    _, entry, exit_, _, _ = Q.frames()
    msk = h4.index >= pd.Timestamp(Q.START, tz="UTC")
    entry, exit_ = entry[msk], exit_[msk]
    print(f"진입 이벤트 {len(evs)}건 · 1분봉 {len(m1):,}개 ({m1.index[0]:%Y-%m-%d}~{m1.index[-1]:%Y-%m-%d})")
    print(f"평가: 하한 재확보 진입 + 변동성 타겟팅(40%·창40일), 상한 3배. 기준 = 지연 0분\n")

    def vt(i, c):
        v = Q.acct_vol(i, c, 40)
        return Q.MAX_LEV if v is None else min(Q.MAX_LEV, 40 / v)

    print(f"{'':24s}{'──── 탐색 ────':>22} |{'──── 검증 ────':>22} |{'────── 전체 ──────':>28}"
          f"{'거래':>5}{'승률':>6}{'최악':>7}{'청산':>4}")
    print(f"{'':24s}{'CAGR':>7}{'MDD':>7}{'Sh':>6} |{'CAGR':>7}{'MDD':>7}{'Sh':>6} |"
          f"{'누적':>9}{'MDD':>7}{'Sh':>6}")

    print("\n── 기준: 지연 0 (재확보 순간에 바로 체결 — 지금 백테스트의 가정) ──")
    Q.report("지연 0분", *Q.simulate(okx, entry, exit_, vt, fills=to_bar_keys(make_fills(evs, mins, m1), evs, h4)))

    print("\n── ① 갱신주기: 재확보를 '다음 폴링' 에 잡으면 (진입만) ──")
    for iv in (1, 5, 10, 15, 30, 60):
        f = to_bar_keys(make_fills(evs, mins, m1, interval=iv), evs, h4)
        sk = sum(v is None for v in f.values())
        Q.report(f"주기 {iv:2d}분 (포기 {sk}건)", *Q.simulate(okx, entry, exit_, vt, fills=f))

    print("\n── ② AI 검토를 체결 직전으로 옮기면: 호출 지연 D분 만큼 늦게 산다 ──")
    for dly in DELAYS:
        f = to_bar_keys(make_fills(evs, mins, m1, delay=dly), evs, h4)
        sk = sum(v is None for v in f.values())
        Q.report(f"지연 {dly:2d}분 (포기 {sk}건)", *Q.simulate(okx, entry, exit_, vt, fills=f))

    print("\n── ③ 청산 지연 비용 (4h 마감 뒤 N분 늦게 파는 값, bp) ──")
    oi = okx.index
    xb = [oi[i] for i in range(1, len(oi)) if exit_[i - 1]]
    print(f"   청산 신호 {len(xb)}건 · 음수면 늦을수록 손해")
    for iv in (1, 5, 10, 15, 30, 60):
        mu, md, n = exit_cost(h4, xb, m1, iv)
        print(f"   {iv:3d}분 늦게: 평균 {mu:+6.1f}bp · 중앙값 {md:+6.1f}bp  (표본 {n})")

    # 자체 점검: 지연 0 은 지금 백테스트와 같아야 한다
    f0 = to_bar_keys(make_fills(evs, mins, m1), evs, h4)
    ref = Q.reclaim_fills()
    same = [k for k in f0 if k in ref and f0[k] is not None and ref[k] is not None
            and abs(f0[k] - ref[k]) / ref[k] < 1e-6]
    assert len(same) > len(ref) * 0.9, f"지연 0 인데 기존 체결가와 다르다 ({len(same)}/{len(ref)})"
    print(f"\nok  자체 점검: 지연 0분 체결가가 기존 backtest_quant 와 일치 ({len(same)}/{len(ref)}건)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
