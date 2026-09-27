"""백테스트 체결 엔진 — backtest_both · backtest_quant · backtest_current 가 **같은 코드**를 쓴다.

이 파일이 생긴 이유 (2026-09-10 감사). 예전에는 파일마다 루프를 따로 갖고 있었고, 셋 다 같은 결함이 있었다:
  1. 진입한 봉 안에서의 청산을 아예 검사하지 않았다. 100에 5배로 산 봉이 그 봉 안에서 75까지 빠져도 생존이었다.
  2. 이전 봉에 청산 신호가 있어도 **현재 봉의 저가/고가를 먼저** 보고 파산 판정했다. 시가에 정상 청산됐어야 할
     거래가 그 뒤에 일어난 저가 때문에 전액 손실로 기록됐다.
  3. 청산선을 진입가 × (1 ∓ 1/lev) 로 잡았다. 실제 거래소는 (a) 레버리지를 정수로 올려 걸고
     (autotrade.execute 의 ceil) (b) 유지증거금이 남아 있을 때 청산한다. 둘 다 청산선을 **더 가깝게** 만든다.
  4. 청산되면 무조건 계좌 전액을 0 으로 만들었다. 격리 마진은 그 포지션의 증거금(=명목/레버리지)만 잃는다.
     POSITION_PCT=100% 인 지금 설정에서는 우연히 같은 값이지만, 비중을 낮추면 틀린다.

체결 순서 (봉 i):
  1) 이전 봉(i-1) 청산 신호가 있으면 **시가 o[i] 로 먼저 청산**한다. 그 뒤의 저가/고가는 이미 나간 거래와 무관하다.
     단 시가 자체가 이미 청산선 너머면 OHLC 만으로는 순서를 알 수 없다 → 보수적으로 강제청산으로 세고 따로 집계한다.
  2) 청산 신호가 없으면 봉 i 의 저가/고가로 강제청산을 본다.
  3) 진입은 그 다음이고, **진입한 그 봉 안에서** 체결가 이후의 가격만으로 강제청산을 다시 본다.
     분봉 체결(fills)이면 체결 시각 이후 분봉만 쓴다 — 체결 전 저가는 그 포지션과 무관하기 때문이다.

fills: {진입봉 시각: None | 체결가 | (체결가, 체결 후 저가, 체결 후 고가)}
       None 이면 그 자리를 포기한다. 체결가만 주면 그 봉 전체를 체결 후로 본다 (보수적).
"""
import math

import numpy as np
import pandas as pd

MMR = 0.005              # OKX BTC-USDT 무기한 tier1 유지증거금률. 증거금이 이만큼 남으면 거래소가 청산한다
FUND = 0.0001 * 3 / 6    # 4h 봉당 펀딩 (0.01%/8h 가정 — 실측은 2026-06 이후만 있다: audit/manifest.json)


def cost():
    """편도 비용. backtest_okx.COST 를 늦게 읽는다 (import 순환 회피 + 명령줄 인자 반영)."""
    import backtest_okx as B
    assert 0 <= B.COST < 0.05, f"편도 비용 {B.COST} 가 상식 밖이다 (backtest_okx.arg_cost 주석 참고)"
    return B.COST


def exchange_lev(lev):
    """거래소에 실제로 걸리는 레버리지. autotrade.execute 가 ceil 로 올려 건다 → 청산선이 더 가까워진다."""
    return max(1, math.ceil(lev - 1e-9))


def liq_level(entry, lev, side):
    """강제청산 가격. 정수 레버리지 + 유지증거금 때문에 (1 ∓ 1/lev) 보다 진입가에 가깝다."""
    d = 1.0 / exchange_lev(lev) - MMR
    return entry * (1 - d) if side > 0 else entry * (1 + d)


def run(h4, l_en, l_ex, s_en, s_ex, lev_of, lab=None, allow=("long", "short"),
        fills=None, gate=None, hold_bars=None, pos_pct=1.0):
    """→ (자본곡선, 거래 DataFrame[side, ret, regime, bars, how], stats dict)

    lev_of(i, curve) → 이번 진입의 레버리지 (0 이면 진입하지 않는다).
    gate: None · bool 배열(양방향 공통) · (롱 게이트, 숏 게이트) 튜플.
    pos_pct: 명목 / (자산 × 레버리지). 격리 증거금 비중이자 강제청산 때 잃는 자산 비율.
    stats: {liq, liq_ambiguous, open_at_end, mtm_open, forced_close_ret}
    """
    C = cost()
    o, lo, hi, c, idx = h4.open.values, h4.low.values, h4.high.values, h4.close.values, h4.index
    n = len(h4)
    g_long, g_short = gate if isinstance(gate, tuple) else (gate, gate)
    eq, held, rows, liq, ambig = 1.0, None, [], 0, 0
    curve = np.ones(n)
    cap = hold_bars or 10 ** 9

    def breached(px, e, lev, side):
        lvl = liq_level(e, lev, side)
        return px <= lvl if side > 0 else px >= lvl

    def blow(side, reg, k, how):
        nonlocal eq, liq
        eq *= 1 - pos_pct
        liq += 1
        rows.append({"side": "롱" if side > 0 else "숏", "ret": -1.0, "regime": reg, "bars": k, "how": how})

    for i in range(1, n):
        if held:
            e, k, lev, side, reg = held
            k += 1
            want_exit = bool((l_ex if side > 0 else s_ex)[i - 1]) or k >= cap
            if want_exit and not breached(o[i], e, lev, side):        # 1) 시가 정상 청산이 먼저다
                r = side * lev * (o[i] / e - 1) - lev * C * (1 + o[i] / e) - side * k * FUND * lev
                eq *= 1 + r * pos_pct
                rows.append({"side": "롱" if side > 0 else "숏", "ret": r, "regime": reg, "bars": k, "how": "신호"})
                held = None
            elif want_exit or breached(lo[i] if side > 0 else hi[i], e, lev, side):
                ambig += bool(want_exit)          # 시가부터 청산선 너머 — 순서 불명, 보수적으로 청산 처리
                blow(side, reg, k, "청산(시가불명)" if want_exit else "청산")
                held = None
            else:
                held = (e, k, lev, side, reg)
        if held is None and eq > 0:
            gl = g_long is None or g_long[i - 1]
            gs = g_short is None or g_short[i - 1]
            side = (1 if ("long" in allow and l_en[i - 1] and gl)
                    else -1 if ("short" in allow and s_en[i - 1] and gs) else 0)
            if side:
                f = fills.get(idx[i], o[i]) if (fills is not None and side > 0) else o[i]
                px, lo_a, hi_a = f if isinstance(f, tuple) else (f, lo[i], hi[i])
                lev = lev_of(i, curve)
                reg = None if lab is None else lab[i - 1]
                if lev > 0 and px is not None:
                    if breached(lo_a if side > 0 else hi_a, px, lev, side):   # 3) 진입한 그 봉 안에서 터진다
                        blow(side, reg, 0, "청산(진입봉)")
                    else:
                        held = (px, 0, lev, side, reg)
        curve[i] = eq * (1 + held[3] * held[2] * (c[i] / held[0] - 1) * pos_pct) if held else eq
        if eq <= 0:
            curve[i:] = 0.0
            break
    t = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["side", "ret", "regime", "bars", "how"])
    stats = {"liq": liq, "liq_ambiguous": ambig, "open_at_end": bool(held), "mtm_open": 0.0, "forced_close_ret": None}
    if held:                                    # 마지막 미청산 거래: 평가 손익과 '지금 강제로 닫으면' 성적
        e, k, lev, side, _ = held
        stats["mtm_open"] = side * lev * (c[-1] / e - 1)
        stats["forced_close_ret"] = stats["mtm_open"] - lev * C * (1 + c[-1] / e) - side * k * FUND * lev
    return pd.Series(curve, index=h4.index), t, stats


def _selfcheck():
    """감사에서 확정된 결함들의 회귀 테스트. 수정 전 엔진에서는 1·2·3·5·6 이 실패한다."""
    def bars(rows):
        return pd.DataFrame(rows, columns=["open", "high", "low", "close"],
                            index=pd.date_range("2024-01-01", periods=len(rows), freq="4h", tz="UTC")).assign(volume=1.0)

    z = lambda n: np.zeros(n, bool)

    # 1) 100 에 5배로 산 봉이 그 봉 안에서 75 까지 빠지면 살아남으면 안 된다 (청산선 = 100×(1−1/5+0.005) = 80.5)
    d = bars([[100, 100, 100, 100], [100, 101, 75, 90], [90, 91, 89, 90]])
    en = z(3); en[0] = True
    _, t, s = run(d, en, z(3), z(3), z(3), lambda i, c: 5)
    assert s["liq"] == 1 and t.ret.iloc[0] == -1.0, f"진입봉 안 청산을 놓쳤다: {t.to_dict('records')}"

    # 2) 이전 봉 청산 신호 → 시가 100 에 정상 청산. 그 뒤 저가 70 은 이미 나간 거래와 무관하다
    d = bars([[100, 100, 100, 100], [100, 101, 99, 100], [100, 101, 70, 75]])
    en = z(3); en[0] = True
    ex = z(3); ex[1] = True
    _, t, s = run(d, en, ex, z(3), z(3), lambda i, c: 5)
    assert s["liq"] == 0 and len(t) == 1 and t.how.iloc[0] == "신호", f"시가 청산이 나중 저가에 파산 처리됐다: {t.to_dict('records')}"

    # 3) 격리 마진: 비중 50% 면 청산돼도 계좌의 절반만 잃는다
    d = bars([[100, 100, 100, 100], [100, 101, 50, 60], [60, 61, 59, 60]])
    en = z(3); en[0] = True
    cv, t, s = run(d, en, z(3), z(3), z(3), lambda i, c: 5, pos_pct=0.5)
    assert s["liq"] == 1 and abs(cv.iloc[-1] - 0.5) < 1e-12, f"격리 손실이 증거금 비중을 안 따른다: {cv.iloc[-1]}"

    # 4) 정수 올림: 2.1배로 걸면 거래소는 3배로 건다 → 청산선이 −33%(+MMR) 이지 −48% 가 아니다
    assert exchange_lev(2.1) == 3 and abs(liq_level(100, 2.1, 1) - 100 * (1 - 1 / 3 + MMR)) < 1e-9

    # 5) 분봉 체결: 체결 **전** 저가는 쓰지 않는다
    d = bars([[100, 100, 100, 100], [100, 110, 70, 105], [105, 106, 104, 105]])
    en = z(3); en[0] = True
    f = {d.index[1]: (105.0, 104.0, 110.0)}                     # 저가 70 은 체결 전에 지나갔다
    _, t, s = run(d, en, z(3), z(3), z(3), lambda i, c: 5, fills=f)
    assert s["liq"] == 0, "체결 전 저가로 파산 처리했다"

    # 6) 게이트는 방향별로 따로 (backtest_current 의 EXT 필터가 OR 였다)
    d = bars([[100, 100, 100, 100], [100, 101, 99, 100], [100, 101, 99, 100]])
    en = z(3); en[0] = True
    gl, gs = z(3), np.ones(3, bool)                             # 롱 게이트는 닫고 숏 게이트만 연다
    _, t, _ = run(d, en, z(3), z(3), z(3), lambda i, c: 1, gate=(gl, gs))
    assert len(t) == 0, "숏 게이트로 롱 진입이 통과했다"

    # 7) 마지막 미청산 거래는 평가만 하고 거래 원장에 넣지 않는다
    d = bars([[100, 100, 100, 100], [100, 101, 99, 100], [110, 111, 109, 110]])
    en = z(3); en[0] = True
    _, t, s = run(d, en, z(3), z(3), z(3), lambda i, c: 1)
    assert len(t) == 0 and s["open_at_end"] and s["mtm_open"] > 0.09, f"미청산 거래 평가가 없다: {s}"
    print("ok  engine 자체 점검 통과 (진입봉 청산 · 시가 청산 우선 · 격리 손실 · 정수 레버리지 · 분봉 체결 · 방향별 게이트 · 미청산 평가)")


if __name__ == "__main__":
    _selfcheck()
