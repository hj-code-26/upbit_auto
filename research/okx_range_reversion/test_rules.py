"""rules·backtest 검증 — 합성 분봉만 (네트워크·실데이터 없음). 사용: python research/okx_range_reversion/test_rules.py
미래정보 누수 · 동일 봉 익절/손절 · 지정가 체결 착시 · 청산 변형 A/B/C · 비용 계획 · 계약 수량 · 펀딩 · 일손실."""
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import backtest as B  # noqa: E402
import rules as R  # noqa: E402

p0 = R.P(slip=0.0, taker=0.0, maker=0.0)                                   # 체결 논리만 볼 때 비용 0
T0 = pd.Timestamp("2024-01-01", tz="UTC")


def mk(bars, fund=None):
    """bars = [(o,h,l,c), ...] 1분봉 → sim 입력. fund = {분 인덱스: 률}."""
    m = pd.DataFrame(bars, columns=["open", "high", "low", "close"], index=pd.date_range(T0, periods=len(bars), freq="1min"))
    m["volume"] = 1.0
    f = pd.Series({T0 + pd.Timedelta(minutes=k): r for k, r in (fund or {}).items()}, dtype=float)
    if f.empty:
        f = pd.Series([0.0], index=[T0 - pd.Timedelta("1D")])
    return B.arrays(m, f.sort_index(), p0), m


def flat(n, px=100.0):
    return [(px, px + 0.05, px - 0.05, px)] * n


def test_no_lookahead():
    rng = np.random.default_rng(0)
    n = 60 * 24 * 6
    cl = 100 + np.cumsum(rng.normal(0, 0.05, n))
    m = pd.DataFrame({"open": np.r_[cl[0], cl[:-1]], "close": cl}, index=pd.date_range(T0, periods=n, freq="1min"))
    m["high"] = m[["open", "close"]].max(axis=1) + rng.uniform(0, 0.1, n)
    m["low"] = m[["open", "close"]].min(axis=1) - rng.uniform(0, 0.1, n)
    m["volume"] = 1.0
    p = R.P(adx_max=100, er_max=1.0)                                        # 필터 끄고 신호 많이
    full = R.signals(R.resample(m, "5min"), R.resample(m, "15min"), p)
    assert len(full) > 20
    cut = T0 + pd.Timedelta(days=3, minutes=7)                              # 5분·15분봉 한가운데에서 자른다
    m2 = m.copy()
    m2.loc[m2.index >= cut, ["open", "high", "low", "close"]] *= 1.5        # 미래를 망가뜨린다
    part = R.signals(R.resample(m2, "5min"), R.resample(m2, "15min"), p)
    a, b = full[full.t <= cut], part[part.t <= cut]
    pd.testing.assert_frame_equal(a, b)                                     # 마감 시각이 cut 이전인 신호는 그대로여야 한다
    # 15분봉 필터: 진행 중인 15분봉 값이 새면 cut 직후 신호가 달라진다 — 위 비교가 t<=cut 로 그것까지 잡는다


def test_box_excludes_signal_bar():
    bars = []
    for i in range(60):                                                     # 5분봉 60개: 박스 99~101 안에서 출렁임 (ADX·ER 이 NaN 이 아니게)
        hi, lo, cl = 101 - 0.1 * (i % 4), 99 + 0.1 * (i % 5), 100 + 0.1 * (i % 7) - 0.3
        bars += [(100, hi, lo, cl)] + [(cl, cl, cl, cl)] * 4
    bars += [(100, 100, 98.5, 99.4)] * 5                                    # 신호봉: 저가 98.5 이탈, 종가 99.4 복귀 (중앙 아래)
    m = pd.DataFrame(bars, columns=["open", "high", "low", "close"], index=pd.date_range(T0, periods=len(bars), freq="1min"))
    m["volume"] = 1.0
    s = R.signals(R.resample(m, "5min"), R.resample(m, "15min"), R.P(adx_max=100, er_max=1.0))
    r = s.iloc[-1]
    assert r.side == 1 and r.L == 99 and r.H == 101 and r.mid == 100      # 박스에 신호봉 저가 98.5 가 안 들어간다
    assert r.stop < 98.5 and r.t == m.index[-1] + pd.Timedelta("1min")    # 손절은 이탈 극값 아래, t = 신호봉 마감


def test_same_bar_tp_sl_is_stop():
    M, _ = mk(flat(2) + [(100, 102, 98, 100)] + flat(40))                  # 한 분봉이 익절(101)·손절(99) 둘 다 닿음
    r = B.sim(M, 1, 1, 100.0, 99.0, 101.0, 102, 98, 1.0, "A", p0)
    assert r["why"] == "손절" and r["pnl"] == -1.0


def test_limit_touch_is_not_fill():
    M, _ = mk(flat(2) + [(100, 101, 99.9, 100.5)] + flat(40, 100.5))       # 고가 = 목표 정확히 → 미체결
    r = B.sim(M, 1, 1, 100.0, 99.0, 101.0, 102, 98, 1.0, "A", p0)
    assert r["why"] == "30분"
    M, _ = mk(flat(2) + [(100, 101.1, 99.9, 100.5)] + flat(40, 100.5))     # 넘으면 체결
    r = B.sim(M, 1, 1, 100.0, 99.0, 101.0, 102, 98, 1.0, "A", p0)
    assert r["why"] == "중앙익절" and abs(r["pnl"] - 1.0) < 1e-12


def test_no_tp_in_entry_minute_and_time30():
    M, _ = mk(flat(1) + [(100, 101.5, 99.9, 100.2)] + flat(60, 100.2))     # 진입 분봉에서만 목표를 넘음
    r = B.sim(M, 1, 1, 100.0, 99.0, 101.0, 102, 98, 1.0, "A", p0)
    assert r["why"] == "30분" and r["x"] == 1 + 30                          # 30번째 분봉 마감 → 다음 시가


def test_gap_stop_fills_at_open():
    M, _ = mk(flat(2) + [(97, 97.5, 96.5, 97)] + flat(40, 97))             # 손절 99 아래로 갭
    r = B.sim(M, 1, 1, 100.0, 99.0, 101.0, 102, 98, 1.0, "A", p0)
    assert r["pnl"] == -3.0


def test_b_early_exit():
    M, _ = mk(flat(50, 100))                                                # 유리 이동 0.05 < 0.5×1
    r = B.sim(M, 1, 0, 100.0, 99.0, 101.0, 102, 98, 1.0, "B", p0)
    assert r["why"] == "조기종료" and r["x"] == 15
    M, _ = mk(flat(3) + [(100, 100.6, 99.9, 100.2)] + flat(50, 100.2))     # 유리 이동 0.6 ≥ 0.5
    r = B.sim(M, 1, 0, 100.0, 99.0, 101.0, 102, 98, 1.0, "B", p0)
    assert r["why"] == "30분"


def test_c_prev_fails_then_closes_rest():
    M, _ = mk(flat(5) + [(100, 101.2, 100, 101.1)] + flat(80, 101.1))
    r = B.sim(M, 1, 0, 100.0, 99.0, 101.0, 103, 97, 1.0, "C", p0)          # 직전 봉 종가 100 < 중앙 → 불충족
    assert [g[4] for g in r["legs"]] == ["중앙익절", "조건불충족"] and abs(r["pnl"] - 1.0) < 1e-12


def test_c_close_trail_monotonic_and_target():
    p = R.P(**{**p0.__dict__, "c_check": "close"})
    up = [(101 + i * 0.1, 101.15 + i * 0.1, 100.95 + i * 0.1, 101.1 + i * 0.1) for i in range(8)]
    bars = flat(3) + [(100, 101.2, 100, 101.1)] + up + [(101.9, 102.8, 101.8, 102.7)] + flat(40, 102.7)
    M, _ = mk(bars)
    r = B.sim(M, 1, 0, 100.0, 99.0, 101.0, 103, 97, 1.0, "C", p)           # 최종 목표 = 103 − 0.25 = 102.75
    assert [g[4] for g in r["legs"]] == ["중앙익절", "최종목표"]
    assert abs(r["pnl"] - (0.5 * 1.0 + 0.5 * 2.75)) < 1e-9
    # 추적 손절: 오른 뒤 되밀리면 올라간 손절(최고 종가 − 1ATR)에 나가고, 원래 손절(99)까지 안 간다
    bars = flat(3) + [(100, 101.2, 100, 101.1)] + up + [(101.8, 101.8, 99.5, 99.6)] + flat(40, 99.6)
    M, _ = mk(bars)
    r = B.sim(M, 1, 0, 100.0, 99.0, 101.0, 103, 97, 1.0, "C", p)
    assert r["legs"][-1][4] == "추적손절" and abs(r["legs"][-1][1] - (101.8 - 1.0)) < 1e-9


def test_c_60min_cap():
    p = R.P(**{**p0.__dict__, "c_check": "close"})
    bars = flat(3) + [(100, 101.2, 100, 101.1)] + [(101.1, 101.25, 101.05, 101.2)] * 100
    M, _ = mk(bars)
    r = B.sim(M, 1, 0, 100.0, 99.0, 101.0, 103, 97, 1.0, "C", p)
    assert r["why"] == "60분" and r["x"] == 60


def test_short_mirror():
    M, _ = mk(flat(2) + [(100, 100.1, 98.9, 99.5)] + flat(40, 99.5))
    r = B.sim(M, -1, 1, 100.0, 101.0, 99.0, 102, 98, 1.0, "A", p0)
    assert r["why"] == "중앙익절" and abs(r["pnl"] - 1.0) < 1e-12


def test_costs_and_plan():
    p = R.P()
    a = R.plan(1, 100_000, 100_300, 99_800, p)
    b = R.plan(1, 100_000, 100_300, 99_800, R.stressed(p, 2))
    assert a["e"] > 100_000 and a["reward"] < 300 and a["risk"] > 200       # 슬리피지·수수료가 이익을 깎고 손실을 늘린다
    assert b["reward"] < a["reward"] and b["risk"] > a["risk"]
    assert not R.plan(1, 100_000, 100_100, 99_800, p)["ok"]                 # 목표가 가까우면 비용 뒤 비율 미달 → 진입 안 함
    f = R.plan(1, 100_000, 100_300, 99_800, p, fund=0.001)
    g = R.plan(-1, 100_000, 99_700, 100_200, p, fund=0.001)
    assert f["reward"] < a["reward"] and abs(g["reward"] - R.plan(-1, 100_000, 99_700, 100_200, p)["reward"]) < 1e-9  # 받을 펀딩은 안 넣음
    M, _ = mk(flat(2) + [(100, 101.1, 99.9, 100.5)] + flat(40, 100.5))
    r = B.sim(M, 1, 1, 100.0, 99.0, 101.0, 102, 98, 1.0, "A", R.P(slip=0.0, taker=0.0005, maker=0.0002))
    assert abs(r["pnl"] - (1.0 - 101 * 0.0002 - 100 * 0.0005)) < 1e-12


def test_contracts():
    p = R.P()
    assert R.contracts(10_000, 100_000, 200, p) == 10.0                     # 20U/200 = 0.1 BTC = 10계약
    assert R.contracts(10_000, 100_000, 20, p) == 10.0                      # 위험으로는 1 BTC 지만 명목 1배 상한 0.1 BTC
    assert R.contracts(10_000, 100_000, 199.9, p) == 10.0                   # 0.10005 BTC → lot 내림
    assert R.contracts(100, 100_000, 200, p) == 0.1                         # 0.001 BTC = 0.1계약 (계약 ≠ BTC)
    assert R.contracts(1, 100_000, 10_000, p) == 0.0                        # 0.0000002 BTC → 0.00002 계약 < 최소 0.01


def test_funding_only_when_held():
    M, _ = mk(flat(2) + [(100, 101.1, 99.9, 100.5)] + flat(40, 100.5), fund={2: 0.01, 30: 0.01})
    r = B.sim(M, 1, 1, 100.0, 99.0, 101.0, 102, 98, 1.0, "A", p0)          # 분 2 정산에만 보유 (분 2 중 익절)
    assert abs(r["pnl"] - (1.0 - 0.01 * 100)) < 1e-12
    r = B.sim(M, -1, 1, 100.0, 102.0, 99.0, 102, 98, 1.0, "A", p0)         # 숏은 +률이면 받는다, 30분 청산 → 분 31
    assert r["why"] == "30분" and abs(r["pnl"] - (100 - 100.5 + 2 * 0.01 * 100)) < 1e-9


def test_portfolio_day_loss_blocks():
    bars = flat(1) + [(100, 100.05, 98, 98)] * 3 + flat(200, 98)
    M, m = mk(bars)
    p = R.P(slip=0.0, taker=0.0, maker=0.0, min_rr=0.0, risk=0.006, max_notional=100.0)   # 한 번에 0.6% 씩 잃게
    sig = pd.DataFrame({"t": [m.index[1], m.index[70], m.index[140]], "side": 1, "close": 100.0, "H": 102.0,
                        "L": 98.0, "mid": 101.0, "atr": 1.0, "stop": 99.0})
    for i in (70, 140):                                                     # 뒤 두 신호 자리도 다시 떨어지게
        M["o"], M["l"] = M["o"].copy(), M["l"].copy()
        M["o"][i] = 100.0; M["l"][i] = 98.0
    d, nb = B.portfolio(sig, M, "A", p, eq0=10_000.0)
    assert len(d) == 2 and nb == 1                                          # 두 번째 거래가 일손실 선에서 정리, 세 번째는 막힘
    assert d.why.iloc[1] == "일손실" and d.equity.iloc[-1] >= 10_000 * 0.99 - 1e-6


def test_pipeline_smoke():
    """main() 전체 경로가 합성 랜덤워크(IS/OOS 경계를 걸침)에서 끝까지 도는지만 본다 — 실데이터 결과는 사전등록 커밋 뒤에."""
    import tempfile
    rng = np.random.default_rng(1)
    idx = pd.date_range("2023-12-25", periods=60 * 24 * 14, freq="1min", tz="UTC")
    cl = 40_000 * np.exp(np.cumsum(rng.normal(0, 0.0004, len(idx))))
    m = pd.DataFrame({"open": np.r_[cl[0], cl[:-1]], "close": cl}, index=idx)
    m["high"] = m[["open", "close"]].max(axis=1) * (1 + rng.uniform(0, 3e-4, len(idx)))
    m["low"] = m[["open", "close"]].min(axis=1) * (1 - rng.uniform(0, 3e-4, len(idx)))
    m["volume"] = 1.0
    f = pd.Series(0.0001, index=pd.date_range("2023-12-25", "2024-01-09", freq="8h", tz="UTC"))
    load, fund, here = B.load, B.funding, B.HERE
    try:
        B.load, B.funding, B.HERE = (lambda: m), (lambda: f), pathlib.Path(tempfile.mkdtemp())
        B.main()
        txt = (B.HERE / "backtest_result.txt").read_text(encoding="utf-8")
        assert "[5] 사전등록 판정" in txt and txt.count("→") >= 4
    finally:
        B.load, B.funding, B.HERE = load, fund, here


if __name__ == "__main__":
    fs = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in fs:
        fn()
        print("ok", fn.__name__)
    print(f"{len(fs)}/{len(fs)} 통과")
