from autotrade import liquidated
# 명목 300 (진입 100 × 3). 청산선 = 1/정수레버리지 − 유지증거금 0.5% (engine.liq_level)
pos = {"side": "long", "entry": 100.0, "qty": 3.0}
assert not liquidated({**pos, "lev": 1, "pnl": -100.0})                                   # 1배는 청산 없음
assert liquidated({**pos, "lev": 3, "pnl": -99.0}) and not liquidated({**pos, "lev": 3, "pnl": -98.0})    # 3배: −98.5
assert liquidated({**pos, "lev": 1.5, "pnl": -149.0}) and not liquidated({**pos, "lev": 1.5, "pnl": -148.0})  # 1.5→2배: −148.5
print("ok")
