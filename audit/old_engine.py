"""**수정 전** 체결 엔진을 그대로 얼려 둔 것 (git cd5a0a6 의 backtest_both.simulate).

비교용으로만 쓴다. 결함은 engine.py 머리말에 적혀 있다:
진입봉 안 청산 미검사 · 시가 청산보다 봉 저가를 먼저 봄 · 청산선에 정수 레버리지/유지증거금 미반영 ·
격리 손실을 항상 계좌 전액으로 계산.
"""
import numpy as np
import pandas as pd

import backtest_okx as B

FUND = 0.0001 * 3 / 6


def simulate(h4, sig, lev_of, lab, allow=("long", "short"), fills=None, gate=None):
    l_en, l_ex, s_en, s_ex = sig
    COST = B.COST
    o, lo, hi, c, idx = h4.open.values, h4.low.values, h4.high.values, h4.close.values, h4.index
    n = len(h4)
    eq, held, rows, liq = 1.0, None, [], 0
    curve = np.ones(n)
    for i in range(1, n):
        if held:
            e, k, lev, side, reg = held
            k += 1
            blown = (lo[i] <= e * (1 - 1 / lev)) if side > 0 else (hi[i] >= e * (1 + 1 / lev))
            if blown:
                eq, held, liq = 0.0, None, liq + 1
                rows.append({"side": "롱" if side > 0 else "숏", "ret": -1.0, "regime": reg})
            elif (l_ex if side > 0 else s_ex)[i - 1]:
                r = side * lev * (o[i] / e - 1) - 2 * lev * COST - side * k * FUND * lev
                eq *= 1 + r
                rows.append({"side": "롱" if side > 0 else "숏", "ret": r, "regime": reg})
                held = None
            else:
                held = (e, k, lev, side, reg)
        if held is None and eq > 0 and (gate is None or gate[i - 1]):
            side = 1 if ("long" in allow and l_en[i - 1]) else -1 if ("short" in allow and s_en[i - 1]) else 0
            if side:
                px = o[i] if (fills is None or side < 0) else fills.get(idx[i], o[i])
                px = px[0] if isinstance(px, tuple) else px      # 새 fills 형식(체결가, 체결후저가, 고가)도 받는다
                lev = lev_of(i, curve)
                if lev > 0 and px is not None:
                    held = (px, 0, lev, side, lab[i - 1])
        curve[i] = eq * (1 + held[3] * held[2] * (c[i] / held[0] - 1)) if held else eq
        if eq <= 0:
            curve[i:] = 0.0
            break
    t = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["side", "ret", "regime"])
    return pd.Series(curve, index=h4.index), t, liq
