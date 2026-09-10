from autotrade import liquidated
pos = {"entry": 100.0, "qty": 3.0, "pnl": -100.0}      # 명목 300, 3배 → 증거금 100
assert liquidated({**pos, "lev": 3}) and not liquidated({**pos, "lev": 1})
assert not liquidated({**pos, "lev": 3, "pnl": -99.0})
assert liquidated({**pos, "lev": 1.5, "pnl": -200.0}) and not liquidated({**pos, "lev": 1.5, "pnl": -199.0})
print("ok")
