"""최소 수작업 정답 테스트 — engine.run 과 backtest_entry.simulate 의 체결 규칙 (손으로 계산한 값과 대조).

backtest_quant ↔ backtest_entry 대사 assert 는 **같은 engine.run 을 부르는 두 껍데기의 배선 일치**만 보증한다.
engine 자체의 오류는 양쪽에 똑같이 들어가므로 그 assert 로는 잡히지 않는다. 이 파일은 그 빈칸을 손 계산으로 메운다.

python test_engine.py   (실패하면 AssertionError, 기대 실패는 XFAIL 로 출력)
"""
import sys

import common as C
from common import E, np, pd

sys.stdout.reconfigure(encoding="utf-8")
CST, F = E.cost(), E.FUND
z = lambda n: np.zeros(n, bool)
RES = []


def bars(rows):
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"],
                        index=pd.date_range("2024-01-01", periods=len(rows), freq="4h", tz="UTC")).assign(volume=1.0)


def check(name, cond, detail="", xfail=False):
    RES.append((name, bool(cond), xfail))
    tag = ("XFAIL(확인된 결함)" if not cond else "XPASS?") if xfail else ("ok" if cond else "FAIL")
    print(f"{tag:18s} {name}  {detail}")
    if not xfail:
        assert cond, f"{name}: {detail}"


# 1) 다음 봉 시가 진입 + 손 계산 수익률 (3배, 100→110, 2봉 보유)
d = bars([[100, 100, 100, 100], [100, 101, 99, 100], [105, 106, 104, 105], [110, 111, 109, 110]])
en, ex = z(4), z(4); en[0] = True; ex[2] = True
cv, t, s = E.run(d, en, ex, z(4), z(4), lambda i, c: 3)
want = 3 * (110 / 100 - 1) - 3 * CST * (1 + 110 / 100) - 2 * F * 3
check("다음 봉 시가 진입·시가 청산·비용·펀딩 손계산", abs(t.ret.iloc[0] - want) < 1e-12 and t.px_in.iloc[0] == 100,
      f"ret {t.ret.iloc[0]:.6f} vs {want:.6f}")

# 2) 체결 전 저가 무시 / 체결 후 청산선 접촉은 반영 (5배 청산선 = 100×(1−0.2+0.005)=80.5)
d = bars([[100, 100, 100, 100], [100, 110, 70, 105], [105, 106, 104, 105]])
en = z(3); en[0] = True
_, t, s = E.run(d, en, z(3), z(3), z(3), lambda i, c: 5, fills={d.index[1]: (100.0, 81.0, 110.0)})
check("체결 전 저가(70) 무시", s["liq"] == 0)
_, t, s = E.run(d, en, z(3), z(3), z(3), lambda i, c: 5, fills={d.index[1]: (100.0, 80.4, 110.0)})
check("체결 후 저가 80.4 ≤ 80.5 → 청산", s["liq"] == 1 and t.how.iloc[0] == "청산(진입봉)")

# 3) 격리 증거금 손실: pos_pct=0.4 로 청산 → 자본 0.6. 청산 거래에는 비용·펀딩이 추가로 빠지지 않는다 (−100% 로 끝)
d = bars([[100, 100, 100, 100], [100, 101, 60, 70], [70, 71, 69, 70]])
en = z(3); en[0] = True
cv, t, s = E.run(d, en, z(3), z(3), z(3), lambda i, c: 3, pos_pct=0.4)
check("격리 손실 = 증거금 비중", abs(cv.iloc[-1] - 0.6) < 1e-12, f"자본 {cv.iloc[-1]}")
check("청산 시 비용·펀딩 미반영(증거금 이상 손실 없음 가정)", t.ret.iloc[0] == -1.0,
      "OKX 는 청산 수수료·보험기금 차감이 있으나 격리 증거금을 넘지 않는다 → 문제 없음")

# 4) 표본 마지막 봉: 미청산 거래는 원장에 없고 stats 에만 평가
d = bars([[100, 100, 100, 100], [100, 101, 99, 100], [120, 121, 119, 120]])
en = z(3); en[0] = True
cv, t, s = E.run(d, en, z(3), z(3), z(3), lambda i, c: 1)
check("마지막 봉 미청산 → 원장 제외·평가만", len(t) == 0 and s["open_at_end"] and abs(cv.iloc[-1] - 1.2) < 1e-12)

# 5) 동일 봉 순서 미확정: 청산 신호가 있는데 시가가 이미 청산선 너머 → 보수적으로 청산 + ambig 집계
d = bars([[100, 100, 100, 100], [100, 101, 99, 100], [60, 61, 59, 60]])
en, ex = z(3), z(3); en[0] = True; ex[1] = True
_, t, s = E.run(d, en, ex, z(3), z(3), lambda i, c: 3)
check("시가 갭이 청산선 너머 → 청산(시가불명)·ambig=1", s["liq"] == 1 and s["liq_ambiguous"] == 1)
# 시가 갭 체결 가정: 거래소는 청산선이 아니라 갭 가격에서 청산 → 격리 한도(−100%)에서 멈춘다. 엔진도 −100%. 일치.

# 6) 분봉 결측/미체결 → None 이면 진입을 건너뛴다 (그 봉에서). 다음 봉에 진입 신호가 남아 있으면 **시가로 진입한다**
d = bars([[100, 100, 100, 100], [100, 101, 99, 100], [101, 102, 100, 101], [102, 103, 101, 102]])
en = z(4); en[0] = True; en[1] = True
_, t, s = E.run(d, en, z(4), z(4), z(4), lambda i, c: 1, fills={d.index[1]: None})
check("fills=None → 그 봉은 건너뛰고, 다음 봉 신호가 살아 있으면 시장가 재진입", s["open_at_end"] and len(t) == 0,
      "backtest_entry.events() 상태기계는 같은 자리를 재진입하지 않는다 — 두 경로가 갈릴 수 있는 지점")

# 7) backtest_entry.simulate(fallback='end'): 실제 체결은 o[i+1] 인데 엔진에는 봉 i 진입으로 기록된다
import backtest_entry as BE  # noqa: E402

d = bars([[100, 100, 100, 100], [100, 101, 99, 100], [102, 103, 50, 102], [104, 105, 103, 104], [106, 107, 105, 106]])
ev = [(d.index[0], 1, 4, 99.0, 90.0)]                    # 신호봉 0 → 진입봉 1 → 청산봉 4 (청산 신호는 봉 3)
r = BE.simulate(ev, d, lambda e: None, 1, "end")
true_ret = 1 * (106 / 102 - 1) - CST * (1 + 106 / 102) - 2 * F      # 실제 보유: 봉 2 시가 ~ 봉 4 시가 = 2봉
eng_ret = 1 * (106 / 102 - 1) - CST * (1 + 106 / 102) - 3 * F      # 엔진 기록: 봉 1 ~ 봉 4 = 3봉
got = (1 + r[0] / 100) - 1
check("fallback=end 펀딩 봉수 = 실제 보유 봉수", abs(got - true_ret) < 1e-12,
      f"기록 {got:.6f} · 실제 {true_ret:.6f} · 엔진식 {eng_ret:.6f} (펀딩 1봉 과다)", xfail=True)
r5 = BE.simulate(ev, d, lambda e: None, 5, "end")
check("fallback=end 에서 체결 봉(2)의 저가 50 은 체결 후 → 5배 청산이어야 함", r5[5] == 1, f"청산 {r5[5]}")

# 8) fallback=end 인데 청산봉 j == 진입봉 i+1 이면 같은 가격에 사고팔아 비용만 내는 '유령 거래' 가 생긴다
ev2 = [(d.index[0], 1, 2, 99.0, 90.0)]
r = BE.simulate(ev2, d, lambda e: None, 1, "end")
check("fallback=end · 청산 신호가 체결 전에 선 자리에서는 거래가 없어야 함", r[2] == 0,
      f"거래 {r[2]}건 · 누적 {r[0]:+.4f}% (봇은 대기 중 청산 조건이 켜지면 자리를 취소한다: autotrade.act)", xfail=True)

bad = [n for n, ok, xf in RES if not ok and not xf]
print(f"\n{len(RES)}개 중 실패 {len(bad)} · 확인된 결함(XFAIL) {sum(1 for _, ok, xf in RES if xf and not ok)}")
