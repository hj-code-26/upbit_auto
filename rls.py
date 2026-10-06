"""RLS(레짐 롱/숏 보유) 규칙 — 순수 함수, 거래소 호출 없음.
명세: auto_okx_trading_start/research/RLS_model_prompt_10x.md (RLS-v0 10x 변형, 5x 명세 RLS_model_prompt.md). 규칙을 바꾸지 않는다.
10x 는 FWD-v2 동결 범위(5x) 밖이며 전진 관찰로 검증되지 않았다. 격리 청산 거리 ≈ 9.5% < 3×ATR(보통 12~16%) 라
과거 백테스트에서는 손절보다 강제청산이 먼저 오는 경우가 대부분이었다.

  일 d 의 UTC 종가로 레짐을 판정하고 d+1 00:00 UTC 부터 적용한다 (d+1 이후 데이터는 쓰지 않는다).
  BULL = close_d > SMA50_d & SMA50_d > SMA50_(d-20) → 롱,  BEAR = 거울상 → 숏,  그 외 NEUTRAL → 무포지션.
  재난 손절 = 진입 체결가 ∓ 3 × ATR14(완결 일봉, Wilder alpha=1/14), 진입 시 고정 (A안, 기본).
  B안(검증 안 됨): 손절 거리 = min(3×ATR, 청산 거리 − 1.5%p) → 손절이 항상 청산보다 먼저.
  손절 체결 후 같은 방향은 레짐이 한 번 꺼졌다 다시 켜질 때(새 에지)만 재진입.
"""
import numpy as np
import pandas as pd

SMA, SLOPE, ATR_N, ATR_K = 50, 20, 14, 3.0
MIN_DAYS = SMA + SLOPE                 # SMA50 + 기울기 20일 = 완결 일봉 70개 이상
MARGIN, LEVER = 0.02, 10               # 증거금 = 자산의 2%, 10x 격리 → 명목 20%
B_BUFFER = 0.015                       # B안: 청산 거리 − 1.5%p
COST = 0.0005 + 0.0002                 # taker 0.05% + 슬리피지 2bps (편도)
FUNDING_8H = 0.0001                    # 백테스트 가정 0.01%/8h (연구와 동일)
MMR = 0.004                            # ponytail: 유지증거금률 단일 근사(OKX 1구간). 시뮬레이션 강제청산가에만 쓴다
DAY_MS = 86_400_000


def daily(h1):
    """1H 봉(ts ms, open, high, low, close, confirm) → UTC 00:00 경계 일봉.
    24개 1H 가 모두 확정(confirm=1)된 날만 남긴다. index = 그날 00:00 UTC (naive Timestamp)."""
    h = h1[h1.confirm == 1].sort_values("ts")
    g = h.groupby(h.ts // DAY_MS)
    d = pd.DataFrame({"open": g.open.first(), "high": g.high.max(), "low": g.low.min(),
                      "close": g.close.last(), "n": g.size()})
    d = d[d.n == 24].drop(columns="n")
    d.index = pd.to_datetime(d.index * DAY_MS, unit="ms")
    return d


def regime(d):
    """{1: BULL, -1: BEAR, 0: NEUTRAL}. SMA50 은 '최근 50개 완결 일봉' (결측일은 daily() 에서 이미 빠져 있다)."""
    c = d.close
    s = c.rolling(SMA).mean()
    sh = s.shift(SLOPE)
    return ((c > s) & (s > sh)).astype(int) - ((c < s) & (s < sh)).astype(int)


def atr(d):
    """ATR14, True Range 의 Wilder 평활 (alpha = 1/14). 첫 날 TR = high - low."""
    pc = d.close.shift()
    tr = pd.concat([d.high - d.low, (d.high - pc).abs(), (d.low - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / ATR_N, adjust=False, min_periods=ATR_N).mean()


def stop_price(side, entry, atr_v, liq=None):
    """A안: liq=None → 진입가 ∓ 3×ATR.  B안: liq(청산가)를 주면 거리를 min(3×ATR, |진입−청산| − 1.5%p) 로 줄인다."""
    dist = ATR_K * atr_v
    if liq is not None:
        dist = min(dist, abs(entry - liq) - B_BUFFER * entry)
    return entry - side * dist


def beyond_liq(side, stop, liq):
    """sl_beyond_liq: 손절가가 청산가보다 멀다 (= 청산이 먼저 온다)."""
    return bool(liq) and (stop - liq) * side < 0


def funding_pnl(side, rate, notional):
    """펀딩 손익. 양(+) 펀딩이면 롱이 지불(-), 숏이 수취(+)."""
    return -side * rate * notional


def blocked(reg, side, stop_day):
    """손절 뒤 같은 방향 재진입 금지 여부: 손절일 이후의 레짐이 전부 그 방향이면(= 아직 꺼진 적 없음) True.
    손절일 이후 판정된 레짐이 아직 없어도 True (새 에지가 없었으므로)."""
    after = reg[reg.index >= pd.Timestamp(stop_day)]
    return bool((after == side).all())


def simulate(d, allow=(1, -1), funding_8h=FUNDING_8H, margin=MARGIN, lever=LEVER, start=None, stop_mode="A"):
    """일봉 리플레이 → (일별 자산 Series, 시작 1.0), 거래 DataFrame.
    d 레짐 → d+1 시가 체결. 손절 우선: 시가가 이미 손절가를 넘었으면 시가(갭), 장중 닿으면 손절가.
    격리라 3×ATR 손절이 강제청산가(진입가 ∓ (1/lever − MMR))보다 멀면 강제청산이 먼저 난다(10x 에선 대부분).
    stop_mode="B" 는 손절을 청산가 안쪽 1.5%p 로 당긴다 (검증 안 된 변형, A안과 따로 본다).
    손실은 증거금으로 상한. 펀딩은 보유 중인 날 종가 명목 × 하루 3회로 근사.
    allow=(1,) 은 'BULL 롱만(R4)' 대조군. start 를 주면 그 전 일봉은 레짐 워밍업에만 쓰고 start 시가부터 거래한다
    (봇을 그날 켠 것과 같음: 첫날 진행 중인 레짐 방향으로 바로 진입)."""
    r, av = regime(d).to_numpy(), atr(d).to_numpy()
    o, h, l, c = (d[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    cash, side, qty, entry, trig, why, blk, t0 = 1.0, 0, 0.0, 0.0, 0.0, "", 0, 0
    eq, trades = [1.0], []

    def close(i, px, why):
        nonlocal cash, side
        pnl = max(side * qty * (px - entry), -qty * entry / lever) - COST * qty * px
        cash += pnl
        trades.append({"entry_day": d.index[t0], "exit_day": d.index[i], "side": side, "entry": entry, "exit": px,
                       "ret": side * (px - entry) / entry - 2 * COST, "why": why})
        side = 0

    for i in range(1, len(d)):
        want = r[i - 1] if r[i - 1] in allow and (start is None or d.index[i] >= start) else 0
        if blk and r[i - 1] != blk:                                    # 레짐이 한 번 꺼짐/반전 → 재진입 허용
            blk = 0
        if side and (o[i] - trig) * side <= 0:                         # 시가 갭으로 이미 손절가 너머
            blk = side
            close(i, o[i], why)
        if side and want != side:
            close(i, o[i], "regime")
        if not side and want and want != blk and not np.isnan(av[i - 1]):
            side, entry, t0 = want, o[i], i
            qty = cash * margin * lever / o[i]
            liq = entry * (1 - side * (1 / lever - MMR))
            stop = stop_price(side, entry, av[i - 1], liq if stop_mode == "B" else None)
            trig, why = (stop, "stop") if (stop - liq) * side >= 0 else (liq, "liq")
            cash -= COST * qty * o[i]
        if side and ((side == 1 and l[i] <= trig) or (side == -1 and h[i] >= trig)):
            blk = side
            close(i, trig, why)
        if side:
            cash += funding_pnl(side, 3 * funding_8h, qty * c[i])
        eq.append(cash + (side * qty * (c[i] - entry) if side else 0.0))
    eq = pd.Series(eq, index=d.index)
    return (eq if start is None else eq[eq.index >= start]), pd.DataFrame(trades)
