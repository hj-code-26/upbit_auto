"""허브가 업스트림 응답 모양 변화에 죽지 않는지 확인. python3 test_hub.py"""
import hub

FULL = {
    "/api/state": {"dry_run": True, "auto": False, "last_run": "2026-10-10T22:40:00+09:00", "next_run": None,
                   "mode": "live", "acct": {"streak": 2}, "okx": {"eq": 41.2, "avail": 30.0, "realized": 40.0, "ts": "2026-10-11T08:07:00"},
                   "daily": [{"equity": 41.0, "dd": -0.031}],
                   "ledger": {"BTC-USDT-SWAP#0": {"side": 1, "sz": 0.01, "entry": 62000.0, "stop": 61000.0},
                              "BTC-USDT-SWAP#1": {"side": 0, "sz": 0.0}},
                   "trades": [{"inst": "ETH-USDT-SWAP", "side": -1, "pnl": -0.4, "pnl_pct": -0.012}], "orders": []},
    "/api/account": {"live": True, "cash": 100.0, "total_value": 1100.0, "return_pct": 3.2, "open_orders": [],
                     "holdings": {"NVDA": {"name": "엔비디아", "quantity": 2.0, "pnl_pct": 5.1}}},
    "/api/orders?limit=50": [{"timestamp": "2026-10-10T22:40:00", "symbol": "NVDA", "side": "BUY"}],
}


def fake(table):
    return lambda url, **_: (table.get(url[url.index("/api"):]), None)


hub.get = fake(FULL)
s, c = hub.stock(), hub.coin()
assert s["mode"] == "모의" and s["account"]["총자산 (USD)"] == 1100.0 and s["tables"][0]["rows"][0]["symbol"] == "NVDA"
assert c["account"]["낙폭 %"] == -3.1 and [r["pos"] for r in c["tables"][0]["rows"]] == ["BTC-USDT-SWAP#0"]
assert abs(c["tables"][1]["rows"][0]["pnl_pct"] + 1.2) < 1e-9

# 업스트림이 죽었거나, 필드가 사라졌거나, 타입이 바뀌어도 예외 없이 빈 값으로 뜬다
for broken in ({}, {"/api/state": [], "/api/account": "x", "/api/orders?limit=50": {"rows": []}},
               {"/api/state": {"daily": [None, 3], "ledger": [1], "okx": None, "trades": "x", "dry_run": False},
                "/api/account": {"holdings": [{"symbol": "AAPL"}], "open_orders": None}}):
    hub.get = fake(broken)
    hub.stock(), hub.coin()
assert hub.stock()["mode"] == "실주문" and hub.stock()["tables"][0]["rows"] == [{"symbol": "AAPL"}]
print("ok")
