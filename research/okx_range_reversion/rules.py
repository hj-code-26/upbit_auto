"""횡보 박스 복귀 규칙 — 설정값·지표·신호·비용 반영 진입 계획·계약 수량. backtest.py 와 executor.py 가 같이 쓴다.

모든 숫자는 설정값이다. 최적값이라고 주장하지 않는다 (DESIGN.md §4). 운영 코드(루트 strategy.py·autotrade.py)는 import 하지 않는다.

시각 규약: 봉 인덱스 = 봉 시작 시각(UTC). 5분봉 k 는 k+5분에 닫히고, 그 순간 쓸 수 있는 15분봉은 '닫힘 시각 ≤ k+5분' 인 것뿐이다.
박스·ATR 은 신호봉 k 를 빼고 k−1 까지로 계산한다 (shift(1)).
"""
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class P:
    # 횡보 필터 (15분봉, 마감된 봉만)
    adx_n: int = 14
    adx_max: float = 20.0
    er_n: int = 10
    er_max: float = 0.30
    # 박스 (5분봉, 신호봉 제외)
    box_n: int = 24                 # 2시간
    atr_n: int = 14
    stop_atr: float = 0.25          # 손절 = 이탈 극값 ∓ 0.25·ATR
    min_rr: float = 1.0             # 비용 뺀 목표이익 ≥ min_rr × 비용 넣은 손절손실
    # 청산
    tp_wait: int = 30               # 분. 중앙 미도달이면 종료 (A·B·C 공통)
    b_check: int = 15               # 분. B·C 조기 종료 판정 시점
    b_mfe: float = 0.5              # 최대 유리 이동폭 < 0.5 × |진입−손절| 이면 조기 종료
    c_frac: float = 0.5             # C: 중앙 익절 비율
    c_ema: int = 9                  # C: 1분봉 EMA
    c_target_atr: float = 0.25      # C: 최종 목표 = 반대편 경계 안쪽 0.25·ATR
    c_trail_atr: float = 1.0        # C: 추적 손절 = 최고(최저) 종가 ∓ 1·ATR, 단조 강화
    c_max: int = 60                 # C: 최초 체결부터 최대 보유
    c_check: str = "prev"           # 'prev' = 익절 체결 순간 마감돼 있는 직전 1분봉(문언 그대로) · 'close' = 체결 분봉 마감 뒤 판정
    # 비용 (2026-09-30 계정 조회: Lv1 taker 0.05% · maker 0.02%)
    taker: float = 0.0005
    maker: float = 0.0002
    slip: float = 0.0002            # 시장가·스톱 체결 1회당 (가정 — 호가 데이터 없음)
    # 위험
    risk: float = 0.002             # 거래당 예정 손실 = 평가액의 0.2%
    max_notional: float = 1.0       # 명목 ≤ 평가액 × 1
    leverage: int = 3               # 격리. 증거금 = 명목 / 3
    day_loss: float = 0.01          # UTC 하루 손실 1% → 신규 중단 + 정리
    liq_buf_min: float = 0.05       # |mark − 청산가| / mark 가 이보다 작으면 신규 중단
    # OKX 계약 규격 (2026-09-30 public/instruments 조회값. executor 는 실행 시 다시 조회해 덮어쓴다)
    ct_val: float = 0.01            # 1계약 = 0.01 BTC (linear, USDT 정산)
    lot: float = 0.01               # 계약 수량 단위
    min_sz: float = 0.01            # 최소 계약 수
    tick: float = 0.1               # 가격 단위 (USDT)


def stressed(p, k):
    """수수료·슬리피지 k 배."""
    return P(**{**p.__dict__, "taker": p.taker * k, "maker": p.maker * k, "slip": p.slip * k})


def resample(m1, rule):
    return m1.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna(subset=["close"])


def atr(c, n):
    pc = c.close.shift(1)
    tr = pd.concat([c.high - c.low, (c.high - pc).abs(), (c.low - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def adx(c, n):
    up, dn = c.high.diff(), -c.low.diff()
    pdm = up.where((up > dn) & (up > 0), 0.0)
    ndm = dn.where((dn > up) & (dn > 0), 0.0)
    a = atr(c, n)
    pdi = 100 * pdm.ewm(alpha=1 / n, adjust=False).mean() / a
    ndi = 100 * ndm.ewm(alpha=1 / n, adjust=False).mean() / a
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def er(close, n):
    """Kaufman 효율비 = |n봉 순변화| / n봉 절대변화 합. 0 = 제자리 왕복, 1 = 일직선."""
    return (close - close.shift(n)).abs() / close.diff().abs().rolling(n).sum()


def signals(c5, c15, p):
    """5분봉마다 신호 → DataFrame(index=5분봉 시작). side +1 롱 / −1 숏 / 0, t=신호봉 마감 시각, H·L·mid·atr·stop."""
    f15 = pd.DataFrame({"adx": adx(c15, p.adx_n), "er": er(c15.close, p.er_n)})
    f15.index = f15.index + pd.Timedelta("15min")                      # 닫힘 시각으로 옮긴다
    t = c5.index + pd.Timedelta("5min")                                # 5분봉 닫힘 시각
    g = f15.reindex(f15.index.union(t)).ffill().reindex(t)             # 닫힘 시각 ≤ t 인 마지막 15분봉
    flat = ((g.adx < p.adx_max) & (g.er < p.er_max)).values

    H = c5.high.rolling(p.box_n).max().shift(1)
    L = c5.low.rolling(p.box_n).min().shift(1)
    A = atr(c5, p.atr_n).shift(1)
    mid = (H + L) / 2
    lo = (c5.low < L) & (c5.close > L) & (c5.close < mid)              # 하단 이탈 후 복귀
    sh = (c5.high > H) & (c5.close < H) & (c5.close > mid)             # 상단 돌파 후 복귀
    side = np.where(flat & lo & ~sh, 1, np.where(flat & sh & ~lo, -1, 0))
    stop = np.where(side > 0, c5.low - p.stop_atr * A, c5.high + p.stop_atr * A)
    out = pd.DataFrame({"side": side, "t": t, "close": c5.close, "H": H, "L": L, "mid": mid, "atr": A, "stop": stop},
                       index=c5.index)
    return out[(out.side != 0) & out.atr.notna() & out.H.notna()]


def plan(side, ref_px, mid, stop, p, fund=0.0):
    """예상 진입가(기준가 + 불리한 슬리피지)로 비용 넣은 목표이익·손절손실 (BTC 1개당 USDT) 과 진입 여부.
    fund = 보유 중 정산될 예상 펀딩률(부호: 롱이 내면 +). 알 수 없으면 0 이 아니라 직전 정산값을 넣는다."""
    e = ref_px * (1 + side * p.slip)
    stop_fill = stop * (1 - side * p.slip)
    fcost = max(side * fund * e, 0)                                     # 받을 펀딩은 계획에 넣지 않는다 (보수적)
    reward = side * (mid - e) - e * p.taker - mid * p.maker - fcost
    risk = side * (e - stop_fill) + e * p.taker + stop_fill * p.taker + fcost
    ok = risk > 0 and reward > 0 and reward >= p.min_rr * risk
    return dict(e=e, reward=reward, risk=risk, ok=bool(ok))


def contracts(equity, e, risk_per_btc, p):
    """계약 수 (BTC 수량 아님). 위험 0.2% 와 명목 1배 중 작은 쪽을 lot 단위로 내림. 최소 미만이면 0."""
    btc = min(p.risk * equity / risk_per_btc, p.max_notional * equity / e)
    n = math.floor(btc / p.ct_val / p.lot + 1e-9) * p.lot
    return round(n, 8) if n >= p.min_sz - 1e-12 else 0.0
