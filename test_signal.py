"""시간축 점검 — 백테스트 신호와 실봇 신호가 정말 같은가 (2026-09-10 감사 §2B).

거래소 호출 없이 data_cache 의 봉만 쓴다. 세 가지를 본다:

  1) prefix-invariance : 미래 봉을 더 붙여도 과거 봉의 신호가 바뀌면 안 된다. 바뀌면 백테스트가
     미래를 보고 있는 것이다 (rolling.shift 를 잘못 쓰면 조용히 그렇게 된다).
  2) 라이브 리플레이 동등성 : 봇이 매 사이클 하는 일(완성 일봉 + 완성 4h 봉 → model.signal)을
     과거 시점마다 그대로 재현해, backtest_both.frames() 가 만든 배열과 한 칸씩 대조한다.
     여기서 어긋나면 백테스트 성적은 봇의 성적이 아니다.
  3) 일봉 경계 : 4h 봉 20:00 은 00:00 에 닫히고 일봉도 그 순간 닫힌다 → **그날 일봉을 쓸 수 있다.**
     16:00 봉은 아직 못 쓴다. 리플레이가 실제로 그렇게 동작하는지 시각으로 확인한다.
  4) 유한 워밍업 : 봇은 일봉 100개만 받는다 (CANDLES). 전체 이력으로 계산한 지표와 다른지 센다.

사용: python test_signal.py
"""
import sys

import numpy as np
import pandas as pd

import backtest_both as BO
import backtest_okx as B
import model as M

N_SAMPLE = 400          # 리플레이로 대조할 4h 봉 개수 (전 구간에서 고르게 뽑는다)
WARMUP = 100            # autotrade.CANDLES — 봇이 받는 일봉 개수


def replay(d1, h4, i):
    """4h 봉 i 가 닫힌 직후 봇이 보는 것과 같은 입력으로 model.signal 을 부른다.

    봇: X.candles(...).iloc[:-1] → **마지막 완성봉까지**. 4h 봉 i 가 닫힌 시각은 index[i] + 4h 이므로
    그 시점에 완성된 일봉은 close_time ≤ index[i]+4h 인 것, 즉 index 가 index[i]+4h-1d 이하인 일봉이다."""
    t_close = h4.index[i] + pd.Timedelta(hours=4)
    d = d1[d1.index + pd.Timedelta(days=1) <= t_close]
    return M.signal(d, h4.iloc[:i + 1]), d.index[-1]


def main():
    d1, h4 = B.fetch("1d"), B.fetch("4h")
    full = BO.frames()
    idx = full[0].index

    # ── 1) prefix-invariance ──
    for cut in (0.5, 0.8):
        k = int(len(h4) * cut)
        sub = BO.frames.__wrapped__ if hasattr(BO.frames, "__wrapped__") else None
        # frames() 는 캐시를 직접 읽으므로 잘라 넣을 수 없다 → 같은 계산을 여기서 재현해 비교한다
        def build(d1x, h4x):
            rg = (B.bull(d1x) >= M.CONF)
            rg.index = rg.index + pd.Timedelta(days=1)
            rg = rg.reindex(h4x.index, method="ffill").fillna(False).astype(bool)
            hi = h4x.high.rolling(M.H4_N).max().shift(1)
            lo = h4x.low.rolling(M.H4_M).min().shift(1)
            return ((h4x.close > hi) & rg), ((h4x.close < lo) | ~rg)
        e_full, x_full = build(d1, h4)
        cut_t = h4.index[k]
        e_cut, x_cut = build(d1[d1.index < cut_t], h4.iloc[:k])
        n = len(e_cut)
        bad_e = int((e_full.iloc[:n].values != e_cut.values).sum())
        bad_x = int((x_full.iloc[:n].values != x_cut.values).sum())
        assert bad_e == 0 and bad_x == 0, f"prefix-invariance 실패 ({cut:.0%} 지점): 진입 {bad_e}건 · 청산 {bad_x}건 어긋남"
    print(f"ok  1) prefix-invariance — 미래 봉을 붙여도 과거 신호 불변 (50%·80% 절단 지점 모두)")

    # ── 2) 라이브 리플레이 동등성 ──
    start = int(np.searchsorted(idx, pd.Timestamp("2021-03-01", tz="UTC")))
    picks = np.linspace(start + 5, len(idx) - 2, N_SAMPLE).astype(int)
    pos = {t: j for j, t in enumerate(h4.index)}
    bad, checked = [], 0
    for i in picks:
        t = idx[i]
        j = pos[t]
        sig, used_daily = replay(d1, h4, j)
        want_en, want_ex = bool(full[1][i]), bool(full[2][i])
        got_en = sig["zone"] == "long"
        got_ex = sig["exit_long"]
        if (got_en, got_ex) != (want_en, want_ex):
            bad.append((str(t), got_en, want_en, got_ex, want_ex, str(used_daily.date())))
        checked += 1
    assert not bad, f"리플레이 불일치 {len(bad)}/{checked}건 — 처음 5건: {bad[:5]}"
    print(f"ok  2) 라이브 리플레이 동등성 — {checked}개 4h 봉에서 봇 신호 == 백테스트 신호")

    # ── 3) 일봉 경계 ──
    day = pd.Timestamp("2024-06-01", tz="UTC")
    t16, t20, t00 = day + pd.Timedelta(hours=16), day + pd.Timedelta(hours=20), day + pd.Timedelta(days=1)
    _, u16 = replay(d1, h4, pos[t16])       # 20:00 에 닫힌다 → 그날 일봉은 아직 안 닫혔다
    _, u20 = replay(d1, h4, pos[t20])       # 00:00 에 닫힌다 → 일봉도 **같은 순간** 닫힌다
    _, u00 = replay(d1, h4, pos[t00])       # 다음날 04:00 에 닫힌다
    assert u16 == day - pd.Timedelta(days=1), f"16:00 봉은 전날 일봉: {u16}"
    assert u20 == day, f"20:00 봉은 방금 닫힌 그날 일봉을 쓴다 (봇이 실제로 그렇다): {u20}"
    assert u00 == day, f"다음날 00:00 봉도 그날(전날) 일봉: {u00}"
    print(f"ok  3) 일봉 경계 — 16:00봉 {u16.date()} / 20:00봉 {u20.date()} / 다음 00:00봉 {u00.date()}")
    print("    ※ 20:00 봉만 그날 일봉을 쓴다. 수정 전 백테스트는 여기서 하루 묵은 일봉을 봤다 (400개 중 6개 불일치).")

    # ── 4) 유한 워밍업 (봇은 일봉 100개만 받는다) ──
    diff = 0
    for i in picks[::4]:
        j = pos[idx[i]]
        t_close = h4.index[j] + pd.Timedelta(hours=4)
        d = d1[d1.index + pd.Timedelta(days=1) <= t_close]
        if len(d) <= WARMUP:
            continue
        a = M.signal(d, h4.iloc[:j + 1])
        b = M.signal(d.tail(WARMUP), h4.iloc[:j + 1])
        diff += (a["bull"], a["zone"], a["exit_long"]) != (b["bull"], b["zone"], b["exit_long"])
    n4 = len(picks[::4])
    print(f"ok  4) 유한 워밍업 — 일봉 {WARMUP}개만 써도 전체 이력과 판단이 다른 봉 {diff}/{n4}개")
    if diff:
        print(f"    ※ EMA·MACD·RSI 는 재귀식이라 워밍업이 유한하면 값이 완전히 같지는 않다."
              f" {diff / n4 * 100:.1f}% 에서 판단이 갈린다 — CANDLES 를 늘리면 줄어든다.")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
