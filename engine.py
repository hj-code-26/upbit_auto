"""거래소 레버리지·강제청산선 — autotrade.execute(주문) 와 autotrade.liquidated(모의 장부) 가 같은 식을 쓴다.

옛 4h 백테스트 체결 엔진(run)은 전략 교체(2026-09-27)로 지웠다 — 커밋 d29f29f 에 보존.
2026-09-10 감사의 결론은 그대로다: 거래소는 (a) 레버리지를 정수로 올려 걸고 (b) 유지증거금이 남아 있을 때
청산한다. 둘 다 청산선을 진입가 × (1 ∓ 1/lev) 보다 **가깝게** 만든다.
"""
import math

MMR = 0.005              # OKX BTC-USDT 무기한 tier1 유지증거금률. 증거금이 이만큼 남으면 거래소가 청산한다


def exchange_lev(lev):
    """거래소에 실제로 걸리는 레버리지. autotrade.execute 가 ceil 로 올려 건다 → 청산선이 더 가까워진다."""
    return max(1, math.ceil(lev - 1e-9))


def liq_level(entry, lev, side):
    """강제청산 가격. 정수 레버리지 + 유지증거금 때문에 (1 ∓ 1/lev) 보다 진입가에 가깝다."""
    d = 1.0 / exchange_lev(lev) - MMR
    return entry * (1 - d) if side > 0 else entry * (1 + d)
