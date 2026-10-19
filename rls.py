"""RLS-C v1 규칙 — 순수 함수 + 한 계좌 일봉 리플레이. 거래소 호출 없음. 규칙을 바꾸지 않는다.
과거 백테스트(2020-04-01~2026-10-09, 일봉 근사)의 사후 탐색 결과이며 수익을 보장하지 않는다.

  상품: 코인 BTC/ETH-USDT-SWAP (OKX 1Dutc 확정 일봉) · 주식 OKX 주식 무기한 (신호 = 기초 주식 정규장 조정 일봉).
  레짐(일 d 종가): BULL = close_d > SMA50_d & SMA50_d > SMA50_(d−20), BEAR = 거울상, 그 외 NEUTRAL.
  CONF3: d−2, d−1, d 레짐이 모두 같을 때만 확정. 다음 시가(코인 UTC 00:00, 주식 다음 정규장 시가)에 적용.
  방향: 주식 = 확정 BULL ∧ M5 후보 → 롱 (숏 없음). 코인 = 확정 BULL 롱 / BEAR 숏 / NEUTRAL 무포지션.
  M5: 21 거래일마다 u 종가로 mom = C[u−21]/C[u−252]−1, d200 = C[u]/mean(C[u−199..u])−1 를 NDX 전체에서 백분위
      → 평균 = 점수 → OKX 상장(이력 ≥ 260일) 상위 5. 다음 거래일 시가부터 21 거래일 유지, 빠진 종목은 그 시가에 청산.
  사이징: L = min(30, 거래소 최대) (명세 20x 에서 사용자 결정으로 변경), 증거금 = 실현 잔고의 4% (명세 2%, 2026-10-16 사용자 결정), 동시 증거금 합 ≤ 전일 자산 10% (주식 M5 순 → BTC → ETH).
  손절: 거리 = min(1.5×ATR14/신호 봉 종가, 1/L − 티어1 MMR − taker − 1%p). 체결가 기준.
  BE1: 종가가 포지션 방향으로 1×ATR14(진입 시 고정) → 손절 = 진입가 ± 0.15% (다음 봉부터, 후퇴 없음).
       주식은 비율 환산: 기초 종가 − 신호 봉 기초 종가 ≥ ATR (무기한 베이시스 제외).
  DROP1: 롱 close_t − close_(t−1) ≤ −1×ATR14_t, 숏 ≥ +1×ATR14_t → 다음 시가 청산.
  edge: 손절·DROP1 로 정리되면 원하는 방향이 한 번 0(또는 반대)이 됐다가 다시 켜질 때만 재진입.
  C (2026-10-12 사용자 결정, 코인): ① 본전(BE1) 뒤 손절은 edge 를 걸지 않는다 (추세가 맞았던 거래).
       ② 신호 봉 ATR14/종가 > 8% 면 진입하지 않고 대기 (30x 손절 1.88% 가 0.25 ATR 미만 → 진입일 잡음 손절).
       교차 검증(2020-22 · 2023-24 · 2025~, ETH/BTC/BTC+ETH)에서 모든 구간 같거나 개선. 같은 데이터로 고른 규칙이다.
  C3 (2026-10-16 사용자 결정, 코인): 확정 레짐이 NEUTRAL 이면 ADX14 > 30 인 DI 방향이 3일 연속일 때 그 방향을 레짐으로 (rls.adx_dir).
  ATR14 = 완결 일봉 TR 의 Wilder 평활 (alpha = 1/14, 최소 14봉).
"""
import hashlib
import json

import numpy as np
import pandas as pd

SMA, SLOPE, ATR_N = 50, 20, 14
STOCK_MIN = 260                        # 주식 후보 최소 이력 (거래일)
LEVER, MARGIN, MAX_TOTAL = 30, 0.04, 0.10   # 2026-10-11 사용자 결정: 20x → 30x (손절 상한 1.88%). 2026-10-16: 증거금 2% → 4% (6몫)
SL_ATR, LIQ_BUF, BE_ATR, BE_OFF, DROP_ATR = 1.5, 0.01, 1.0, 0.0015, 1.0
TAKER, SLIP, FUNDING_8H = 0.0005, 0.0002, 0.0001
KILL_DD, DAILY_LOSS = 0.35, 0.05      # 운용·백테스트 공통 (예전 KILL_SIM 0.40 은 운용 0.35 와 달라 통일)
STREAK_HOLD, ANOMALY = 20, 0.03        # 몫별 20연패 → 진입 중단 (리플레이엔 없음, 리플레이 몫별 최장 9~15) · 거래 손실 > 자산 3% 경보
SIGNAL_VER = "RLS-C3"                  # SMA50·기울기20·CONF3·ATR14(Wilder)·손절 cap·BE1·DROP1·edge·ADX 보강 — 바꾸면 올린다
RISK_VER = "R2"                        # R1: 킬은 점검이 기록만, 청산은 다음 몫 작업. R2: 위험 감시(기본 5초)가 즉시 계좌 단위 청산,
                                       #     일손실 기준 = UTC 00:00 직후 첫 감시 기록(경계에서만 판정, 리플레이와 같음), 입출금 조정
STOP_TRIGGER = "last"                  # 손절 발동 가격 종류 (리플레이 = 일봉 last 고저)
M5_N, M5_EVERY, MOM_SKIP, MOM_LOOK, D200 = 5, 21, 21, 252, 200
COINS = ("BTC-USDT-SWAP", "ETH-USDT-SWAP")
ALLOWED = COINS + ("ZEC-USDT-SWAP",)    # RLSC_COINS 로 고를 수 있는 상품. ZEC: 2026-10-19 사용자 결정 (OKX 데이터 2025-11~, 검증 9개월뿐)
TRANCHES = (0, 4, 8, 12, 16, 20)       # 시각 분할: 몫별 일봉 경계 (UTC 시). 몫당 증거금 = MARGIN / 몫 수. 2026-10-16 사용자 결정: 3몫 → 6몫
EDGE_WHY = ("sl", "liq", "drop1")      # 이 사유로 정리되면 edge 재진입 제한
BE_EDGE = False                        # C①: 본전 뒤 손절도 edge 를 걸까 (C1 = True)
ATR_MAX = 0.08                         # C②: 신호 봉 ATR14/종가 상한 (코인 진입). None = 없음 (C1)
VOL_TARGET, VOL_K = 0.04, (0.5, 2.0)   # 2026-10-17 사용자 결정 동적 크기 (코인): 진입 증거금 × clip(4% / 신호 봉 ATR14/종가, 0.5, 2).
                                       # 변동성 클 때 작게, 작을 때 크게 (거래소 레버리지는 그대로). None = 끔. 연구: dynsize.py · riskprofile.py
NARROW_W, NARROW_K = None, 0.5         # 연구용 좁은 신호 봉 크기 (고저 폭 < w × ATR14 → × 0.5). 운용 전략에는 없음 (None = 끔) —
                                       # 2026-10-19 사용자 결정: 대시보드 시장 상태 표시만. 연구: reports/research_2026-10-19
ADX_FILL, ADX_CONF = 30, 3             # C3 (2026-10-16 사용자 결정): 확정 레짐이 NEUTRAL 일 때 ADX14 > 30 인 DI 방향이 3일 연속이면 그 방향을
                                       # 레짐으로 쓴다 (코인만). ADX_FILL = None 이면 끈다 (C2). 연구: reports/research_2026-10-16/README.md
SIM_MMR = {"coin": 0.004, "stock": 0.02}   # ponytail: 리플레이용 티어1 MMR 가정. 운용은 public/position-tiers 를 매일 조회


def strategy_config(tranches=TRANCHES, lever=LEVER, insts=COINS, margin=None, weights=None):
    """수익률 비교에 쓰는 전략 설정 (모드·인증 정보 없음). 운용·리플레이가 같은 dict 를 만든다. margin = 종목당 증거금 비율.
    weights = {instId: 종목당 증거금 비율} (코인별 비중, 2026-10-20 사용자 결정) — 있으면 margin_by_inst 로 들어가 해시가 달라진다."""
    n, margin = len(tranches), MARGIN if margin is None else margin
    return {"insts": list(insts), "tranches_utc": list(tranches), "lever": lever,
            "margin_per_symbol": margin, "margin_per_tranche": margin / n, "max_total_margin": MAX_TOTAL,
            **({"margin_by_inst": {i: weights[i] for i in insts}} if weights else {}),
            "signal": {"ver": SIGNAL_VER, "sma": SMA, "slope": SLOPE, "conf": 3, "atr": f"wilder{ATR_N}", "sl_atr": SL_ATR,
                       "liq_buf": LIQ_BUF, "be_atr": BE_ATR, "be_off": BE_OFF, "drop_atr": DROP_ATR,
                       "edge_why": list(EDGE_WHY), "be_edge": BE_EDGE, "atr_max": ATR_MAX,
                       "adx_fill": ADX_FILL, "adx_conf": ADX_CONF, "vol_target": VOL_TARGET, "vol_k": list(VOL_K), "bar": "4H confirmed -> 24h at tranche boundary"},
            "risk": {"ver": RISK_VER, "kill_dd": KILL_DD, "daily_loss": DAILY_LOSS, "daily_ref": "UTC 00:00 record",
                     "streak_hold": STREAK_HOLD, "streak_scope": "tranche", "anomaly": ANOMALY, "stop_trigger": STOP_TRIGGER},
            "cost": {"taker": TAKER, "slip_one_way": SLIP, "funding_8h": FUNDING_8H, "funding_model": "constant, sign by side"}}


def margin_of(cfg, inst):
    """설정 dict 에서 상품의 종목당 증거금 비율 (코인별 비중이 있으면 그 값)."""
    return cfg.get("margin_by_inst", {}).get(inst, cfg["margin_per_symbol"])


def config_hash(cfg):
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:12]


def config_diff(a, b, path=""):
    """사람이 읽는 필드별 차이 ["lever: 20 → 30", ...]."""
    out = []
    for k in sorted(set(a) | set(b)):
        x, y = a.get(k), b.get(k)
        if isinstance(x, dict) and isinstance(y, dict):
            out += config_diff(x, y, f"{path}{k}.")
        elif x != y:
            out.append(f"{path}{k}: {x} → {y}")
    return out


def regime(d):
    """{1: BULL, -1: BEAR, 0: NEUTRAL}. SMA50 = 최근 50개 완결 일봉 종가 평균."""
    c = d.close
    s = c.rolling(SMA).mean()
    sh = s.shift(SLOPE)
    return ((c > s) & (s > sh)).astype(int) - ((c < s) & (s < sh)).astype(int)


def confirm(reg):
    """CONF3: d−2, d−1, d 레짐이 모두 같으면 그 레짐, 아니면 0."""
    same = (reg == reg.shift(1)) & (reg == reg.shift(2))
    return reg.where(same, 0).astype(int)


def atr(d):
    """ATR14: True Range 의 Wilder 평활 (alpha = 1/14, 최소 14봉). 첫 봉 TR = high − low."""
    pc = d.close.shift()
    tr = pd.concat([d.high - d.low, (d.high - pc).abs(), (d.low - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / ATR_N, adjust=False, min_periods=ATR_N).mean()


def adx_dir(d, th=None):
    """ADX14 > th(기본 ADX_FILL) 이면 DI 방향(+DI > −DI → 1, 반대 → −1), 아니면 0. Wilder 평활 (alpha = 1/14)."""
    th = ADX_FILL if th is None else th
    up, dn = d.high.diff(), -d.low.diff()
    pdm, ndm = up.where((up > dn) & (up > 0), 0.0), dn.where((dn > up) & (dn > 0), 0.0)
    pc = d.close.shift()
    tr = pd.concat([d.high - d.low, (d.high - pc).abs(), (d.low - pc).abs()], axis=1).max(axis=1)
    w = lambda s: s.ewm(alpha=1 / ATR_N, adjust=False, min_periods=ATR_N).mean()  # noqa: E731
    pdi, ndi = w(pdm) / w(tr), w(ndm) / w(tr)
    adx = w(100 * (pdi - ndi).abs() / (pdi + ndi))
    return np.sign(pdi - ndi).where(adx > th, 0).fillna(0).astype(int)


def prep(d):
    """일봉 → 행마다 그 봉 종가에 아는 값: reg, conf0(CONF3), conf(conf0, NEUTRAL 이면 ADX 보강), atr, prev(직전 봉 종가)."""
    x = d[["open", "high", "low", "close"]].astype(float).copy()
    x["reg"] = regime(x)
    x["conf0"] = confirm(x.reg)
    a = adx_dir(x) if ADX_FILL else pd.Series(0, x.index)
    same = pd.concat([a == a.shift(j) for j in range(ADX_CONF)], axis=1).all(axis=1)
    x["conf"] = x.conf0.where(x.conf0 != 0, a.where(same, 0)).astype(int)
    x["atr"] = atr(x)
    x["prev"] = x.close.shift()
    return x


def offset_daily(b, offset=0, bar_h=4):
    """bar_h 시간 봉 [ts, open, high, low, close, confirm] → offset 시(UTC) 경계 24시간 봉. 확정 봉만, 봉이 빠진 날은 버린다.
    index = 그 24시간의 시작 시각 (offset=0 이면 OKX 1Dutc 와 같다)."""
    b = b[b.confirm == 1].sort_values("ts").drop_duplicates("ts")
    t = pd.to_datetime(b.ts, unit="ms") - pd.Timedelta(hours=offset)
    g = b.groupby(t.dt.floor("D").to_numpy())
    d = pd.DataFrame({"open": g.open.first(), "high": g.high.max(), "low": g.low.min(), "close": g.close.last(),
                      "n": g.size()})
    d = d[d.n == 24 // bar_h].drop(columns="n").astype(float)
    d.index = pd.DatetimeIndex(d.index) + pd.Timedelta(hours=offset)
    return d


def simulate_tranches(h1, offsets, start, end=None, kill=KILL_DD, **kw):
    """시각 분할 리플레이: 자본을 len(offsets) 등분, 몫마다 offset 경계 일봉으로 코인 리플레이 → 날짜별 일수익 평균.
    h1: {instId: 1H 봉}. → (일별 자산 Series, 몫별 거래 DataFrame [tranche 열]).
    킬은 운용처럼 계좌(몫 평균) 기준: 일별 자산이 고점 대비 kill 이상 빠진 날 이후는 자산 고정·그 뒤 진입 거래 제외
    (그날 기록 시점 값으로 전량 청산했다고 본다). 1몫이면 simulate 의 킬과 같다.
    ponytail: 일손실은 몫마다 따로 본다 (실운용은 계좌 합산 — 몫별 일손실을 끈 비교에서 영향 작음). 리밸런싱 없음 = 몫별 독립 복리."""
    rets, trades = [], []
    for k, off in enumerate(offsets):
        X = {i: offset_daily(h, off, 1) for i, h in h1.items()}
        st = pd.Timestamp(start) + pd.Timedelta(hours=off)
        en = None if end is None else pd.Timestamp(end) + pd.Timedelta(hours=off)
        eq, t, _ = simulate(X, {}, lambda _: set(), set(), st, en, sleeves=("coin",), kill=kill if len(offsets) == 1 else 1.0, **kw)
        rets.append(eq.groupby(eq.index.normalize()).last().pct_change())
        trades.append(t.assign(tranche=k))
    r = pd.concat(rets, axis=1).dropna().mean(axis=1)
    eq = pd.concat([pd.Series([1.0], index=[r.index[0] - pd.Timedelta(days=1)]), (1 + r).cumprod()])
    t = pd.concat(trades, ignore_index=True)
    hit = eq.index[1 - eq / eq.cummax() >= kill]
    if len(offsets) > 1 and len(hit):
        eq = eq.where(eq.index <= hit[0], eq.loc[hit[0]])
        t = t[t.entry_ts < hit[0]]
    return eq, t


def lever_for(max_lever):
    return min(LEVER, float(max_lever))


def liq_dist(lever, mmr):
    return 1 / lever - mmr - TAKER


def stop_dist(atr_v, sig_close, lever, mmr):
    """손절 거리(비율) = min(1.5×ATR/신호 봉 종가, 청산 거리 − 1%p). 항상 청산가보다 진입가 쪽."""
    return min(SL_ATR * atr_v / sig_close, liq_dist(lever, mmr) - LIQ_BUF)


def stop_px(side, entry, dist):
    return entry * (1 - side * dist)


def inside_liq(side, entry, liq):
    """거래소 청산가 기준으로 다시 잡은 '안쪽' 손절가 (청산가에서 진입가 쪽으로 1%p)."""
    return stop_px(side, entry, abs(entry - liq) / entry - LIQ_BUF)


def be_px(side, entry):
    return entry * (1 + side * BE_OFF)


def be_hit(side, close, ref, atr_e):
    """ref = 코인은 진입가, 주식은 신호 봉 기초 종가 (비율 환산)."""
    return side * (close - ref) >= BE_ATR * atr_e


def tighten(side, old, new):
    """손절은 손실 방향으로 되돌리지 않는다 (롱은 올리기만, 숏은 내리기만)."""
    return max(old, new) if side > 0 else min(old, new)


def edge_after(why, be, be_edge=BE_EDGE):
    """이 청산 뒤 같은 방향 재진입을 막을까 (edge). be = 본전(BE1) 손절로 올라가 있었나."""
    return why in EDGE_WHY and (be_edge or why != "sl" or not be)


def vol_k(atr_v, close, target=None):
    """동적 크기 배수: clip(target / (ATR/종가), VOL_K). target 기본 = VOL_TARGET, None 이면 1."""
    target = VOL_TARGET if target is None else target
    return float(np.clip(target / (atr_v / close), *VOL_K)) if target else 1.0


def narrow_k(range_atr, w=None):
    """좁은 신호 봉 배수: (고가 − 저가) / ATR < w(기본 NARROW_W) 이면 NARROW_K, 아니면 1. w 가 None·0 이면 1."""
    w = NARROW_W if w is None else w
    return NARROW_K if w and range_atr < w else 1.0


def vol_ok(atr_v, close, atr_max=ATR_MAX):
    """C②: 신호 봉 변동성이 손절폭에 비해 너무 크지 않을 때만 진입."""
    return not atr_max or atr_v / close <= atr_max


def drop1(side, c, c_prev, atr_v):
    return side * (c - c_prev) <= -DROP_ATR * atr_v


def funding_pnl(side, rate, notional):
    """펀딩 손익. 양(+) 펀딩이면 롱이 지불(−), 숏이 수취(+)."""
    return -side * rate * notional


def okx_sym(ticker):
    """기초 티커 → OKX 무기한 기초명 (BRK-B / BRK.B → BRKB)."""
    return ticker.replace("-", "").replace(".", "").upper()


def m5(closes, members, listed, n=M5_N):
    """closes: u 까지 완결된 조정 종가 (행 = 거래일, 마지막 행 = u, 열 = 티커).
    NDX 구성종목 전체에서 mom·d200 을 각각 백분위 → 평균 = 점수. OKX 상장(listed: okx_sym 집합)·이력 ≥ 260 상위 n."""
    c = closes[sorted(t for t in members if t in closes.columns)]
    mom = (c.iloc[-1 - MOM_SKIP] / c.iloc[-1 - MOM_LOOK] - 1) if len(c) > MOM_LOOK else pd.Series(np.nan, c.columns)
    d200 = c.iloc[-1] / c.iloc[-D200:].mean(skipna=False) - 1
    score = ((mom.rank(pct=True) + d200.rank(pct=True)) / 2)[c.notna().sum() >= STOCK_MIN].dropna()
    return [t for t in score.sort_values(ascending=False, kind="stable").index if okx_sym(t) in listed][:n]


def simulate(coins, stocks, members_at, listed, start, end=None, sleeves=("stock", "coin"), mmr=None,
             funding_8h=FUNDING_8H, slip=SLIP, kill=KILL_DD, daily_loss=DAILY_LOSS, entry_ok=None, tp_atr=None, trail_atr=None,
             fund_adverse=False, be_edge=BE_EDGE, atr_max=ATR_MAX, edge_days=None, edge_atr=None, retry_d1=0, risk=None,
             max_total=MAX_TOTAL, size_k=None, vol_target=None, lever_fn=None, narrow_w=None, preempt=None):
    """한 계좌 일봉 리플레이 → (일별 자산 Series [UTC 00:00 기록], 거래 DataFrame, 일별 기록 DataFrame).
    coins: {instId: UTC 일봉}, stocks: {티커: 정규장 조정 일봉}, members_at(day) → NDX 구성종목 집합.
    하루 D: 00:00 자산 기록·일손실(직전 기록 대비 −5% → 코인 지금, 주식 다음 시가 청산·그 시각 진입 없음)·킬
      → 코인 시가 (갭 손절 → 예약 청산 → 진입) → 주식 시가 (같음, M5 순) → 주식 장중 손절
      → 주식 종가 (M5 갱신 · 레짐/후보 청산 · DROP1 · BE1 판정) → 코인 장중 손절 → 펀딩 → 코인 종가 판정.
    손절: 시가가 손절 너머면 시가(불리), 아니면 손절가. 시가가 청산가 너머면 강제청산(손실 = 증거금).
    entry_ok(inst, 신호 봉 날짜) → False 면 그 진입만 건너뛴다 (연구용 필터, 기본 없음 = 명세 그대로).
    fund_adverse: 펀딩 스트레스 — 롱·숏 모두 |3×funding_8h| 를 지급 (수취 없음). 기본 = 부호대로 지급/수취.
    C 규칙: be_edge (기본 BE_EDGE), atr_max (기본 ATR_MAX, 코인 진입만). be_edge=True, atr_max=None = 예전 C1.
    edge 보완 (연구용, 기본 없음):
    retry_d1=n: 진입한 날 바로 손절되면 edge 를 걸지 않고 같은 방향 재시도를 연속 n 번까지 허용 (진입일 잡음 손절 대응).
    edge_days=n: edge 를 n 일 뒤 해제. edge_atr=k: 종가가 청산가에서 포지션 방향으로 k×ATR 더 가면 해제 (추세 지속 확인).
    tp_atr: 진입가 ± tp_atr×ATR(진입 시) 지정가 익절 (같은 봉에 손절도 닿으면 손절 먼저). trail_atr: 봉 종가마다
    손절을 그동안의 최고가(숏은 최저가) ∓ trail_atr×ATR 로 올린다 (다음 봉부터, 후퇴 없음). 둘 다 연구용, 기본 없음.
    risk=r: 위험 기준 사이징 — 명목 = 실현 잔고 × r / 손절 거리 (손절에 닿으면 잔고 r 손실), 증거금 = 명목 / L. 기본 = 증거금 MARGIN 고정.
    max_total: 동시 증거금 합 상한 (전일 자산 비율).
    vol_target: 동적 크기 목표 (기본 VOL_TARGET, 0 = 끔, 코인만). narrow_w: 연구용 좁은 신호 봉 기준 (기본 NARROW_W = 끔, 코인만).
    lever_fn(inst, 신호 봉 행) → 이 거래의 레버리지 (연구용 동적 레버리지, 기본 없음 = LEVER).
    preempt=(순위 [instId, ...], 본전 뒤 보호): 동시 한도로 진입이 막히면 순위가 더 낮은 코인 포지션 중 가장 낮은 것을 그날 시가에 정리(why "preempt",
    edge 없음)하고 다시 진입. 보호 = True 면 본전 이동한 포지션은 정리하지 않는다. 연구용, 기본 없음.
    size_k(inst, 신호 봉 행) → 배수: 진입 증거금(= 노출)에 곱한다 (연구용 동적 크기, 기본 없음 = 1).
    ponytail: 일봉 근사 — 주식 무기한 장외·주말 가격(기초 현물로 대체), 같은 날 코인 손절과 주식 체결의 선후는 모른다."""
    mmr = {**SIM_MMR, **(mmr or {})}
    X = {i: prep(d) for i, d in coins.items()} if "coin" in sleeves else {}
    S = {t: (x := prep(d)).assign(conf=x.get("conf0", x.conf)) for t, d in stocks.items()} if "stock" in sleeves else {}   # 주식은 ADX 보강 없음
    closes = pd.DataFrame({t: d.close for t, d in stocks.items()}).sort_index()
    sess = closes.index if S else pd.DatetimeIndex([])
    start = pd.Timestamp(start)
    last_day = max([x.index[-1] for x in (*X.values(), *S.values())])
    end = min(pd.Timestamp(end), last_day) if end is not None else last_day
    k0 = sess.searchsorted(start) - 1                 # M5: start 직전 거래일 종가에 첫 갱신, 이후 21 거래일마다
    refresh = set(sess[k0::M5_EVERY]) if S and k0 >= 0 else set()
    cands_next = m5(closes.loc[:sess[k0]], members_at(sess[k0]), listed) if refresh else None

    cash = peak = rec_prev = 1.0
    pos, pend, blk, last = {}, {}, {}, {}
    blk_at, tries = {}, {}                            # edge 보완용: {상품: (걸린 날, 청산가)}, {상품: 연속 재시도 수}
    cands, halted, no_stock = [], False, False
    eqs, trades, days = {}, [], []

    def mark():
        return cash + sum(p["side"] * p["qty"] * (last[i] - p["entry"]) for i, p in pos.items())

    def close(i, px, why, t):
        nonlocal cash
        p = pos.pop(i)
        pend.pop(i, None)
        if why == "liq":
            fill, gross, fee = px, -p["margin"], 0.0
        else:
            fill = px * (1 - p["side"] * slip)
            gross, fee = p["side"] * p["qty"] * (fill - p["entry"]), TAKER * p["qty"] * fill
        cash += gross - fee
        pnl = gross - fee - p["fee"] + p["fund"]
        quick = why == "sl" and t == p["t0"] and tries.get(i, 0) < retry_d1
        tries[i] = tries.get(i, 0) + 1 if quick else 0
        if edge_after(why, p["be_ts"] is not None, be_edge) and not quick:
            blk[i], blk_at[i] = p["side"], (t, fill)
        trades.append({"sleeve": p["sleeve"], "inst": i, "side": p["side"], "entry_ts": p["t0"], "exit_ts": t,
                       "entry": p["entry"], "exit": fill, "margin": p["margin"], "stop_dist": p["dist"], "liq": p["liq"],
                       "be": p["be_ts"] is not None, "be_ts": p["be_ts"], "why": why, "lever": p["L"], "fee": p["fee"] + fee,
                       "funding": p["fund"], "pnl": pnl, "pnl_pct": pnl / p["eq0"]})

    def enter(i, side, sig, o, sleeve, t, cap):
        """sig = 신호 봉(직전 완결 봉) 행. 반환: 동시 한도로 막혔으면 1."""
        nonlocal cash
        L = lever_fn(i, sig) if lever_fn else LEVER
        dist = stop_dist(sig.atr, sig.close, L, mmr[sleeve])
        margin = (cash * risk / dist / L if risk else cash * MARGIN) * (size_k(i, sig) if size_k else 1.0) \
            * (vol_k(sig.atr, sig.close, vol_target) * narrow_k((sig.high - sig.low) / sig.atr, narrow_w) if sleeve == "coin" else 1.0)
        if sum(p["margin"] for p in pos.values()) + margin > cap + 1e-12:
            return 1
        fill, eq0 = o * (1 + side * slip), mark()
        qty, fee = margin * L / fill, TAKER * margin * L
        cash -= fee
        pos[i] = {"side": side, "entry": fill, "qty": qty, "margin": margin, "dist": dist,
                  "stop": stop_px(side, fill, dist), "liq": fill * (1 - side * liq_dist(L, mmr[sleeve])), "L": L,
                  "atr_e": sig.atr, "ref": fill if sleeve == "coin" else sig.close, "be_ts": None, "t0": t,
                  "eq0": eq0, "fee": fee, "fund": 0.0, "sleeve": sleeve, "peak": fill,
                  "tp": fill * (1 + side * tp_atr * sig.atr / fill) if tp_atr else None}
        last[i] = o
        return 0

    def gap(i, o, t):
        p = pos[i]
        if (o - p["stop"]) * p["side"] <= 0:
            close(i, o, "liq" if (o - p["liq"]) * p["side"] <= 0 else "sl", t)

    def intraday(i, b, t):
        p = pos.get(i)
        if p and (b.low <= p["stop"] if p["side"] > 0 else b.high >= p["stop"]):
            close(i, p["stop"], "sl", t)
        elif p and p["tp"] and (b.high >= p["tp"] if p["side"] > 0 else b.low <= p["tp"]):
            close(i, max(b.open, p["tp"]) if p["side"] > 0 else min(b.open, p["tp"]), "tp", t)
        elif p and trail_atr:
            p["peak"] = max(p["peak"], b.high) if p["side"] > 0 else min(p["peak"], b.low)
            p["stop"] = tighten(p["side"], p["stop"], p["peak"] - p["side"] * trail_atr * p["atr_e"])

    def judge(i, side_want, x, t):
        """종가 판정: 레짐/후보 청산 → DROP1 → BE1 (보유 중), edge 해제."""
        p = pos.get(i)
        if p and i not in pend:
            if side_want != p["side"]:
                pend[i] = "candidate" if p["sleeve"] == "stock" and x.conf == 1 else "regime"
            elif not np.isnan(x.atr) and not np.isnan(x.prev) and drop1(p["side"], x.close, x.prev, x.atr):
                pend[i] = "drop1"                            # edge 는 실제 청산 사유로 close() 가 건다 (일손실이 먼저 정리하면 없음 = 운용과 같음)
            elif p["be_ts"] is None and be_hit(p["side"], x.close, p["ref"], p["atr_e"]):
                p["stop"], p["be_ts"] = tighten(p["side"], p["stop"], be_px(p["side"], p["entry"])), t
        if blk.get(i) and (side_want != blk[i] or edge_days and (t - blk_at[i][0]).days >= edge_days
                           or edge_atr and not np.isnan(x.atr) and blk[i] * (x.close - blk_at[i][1]) >= edge_atr * x.atr):
            del blk[i]

    for D in pd.date_range(start, end, freq="D"):
        # 00:00(실운용 00:05) 자산 기록 → 일손실 · 킬
        eq = mark()
        eqs[D] = eq
        peak = max(peak, eq)
        dl, blocked, no_coin = eq <= rec_prev * (1 - daily_loss), 0, False
        if dl:
            no_coin = no_stock = True
            pend.update({i: "daily_loss" for i in pos})
        if not halted and 1 - eq / peak >= kill:
            halted = True
            pend.update({i: "halt" for i in pos})
        rec_prev, cap = eq, max_total * eq

        # 코인 시가 (BTC → ETH)
        for i, x in X.items():
            if D not in x.index:
                continue                                    # 데이터 공백: 신호 갱신 없이 보유·손절만
            o = x.at[D, "open"]
            if i in pos:
                gap(i, o, D)
            if i in pos and i in pend:
                close(i, o, pend[i], D)
            prv = D - pd.Timedelta(days=1)
            if i not in pos and prv in x.index and not halted and not no_coin:
                sig = x.loc[prv]
                if sig.conf and sig.conf != blk.get(i) and not np.isnan(sig.atr) and vol_ok(sig.atr, sig.close, atr_max) \
                        and (entry_ok is None or entry_ok(i, prv)):
                    b = enter(i, int(sig.conf), sig, o, "coin", D, cap)
                    if b and preempt and i in preempt[0]:          # 연구용: 낮은 순위 포지션을 정리하고 다시 진입
                        low = [v for v in pos if v in preempt[0] and preempt[0].index(v) > preempt[0].index(i)
                               and not (preempt[1] and pos[v]["be_ts"] is not None) and D in X[v].index]
                        if low:
                            v = max(low, key=preempt[0].index)
                            close(v, X[v].at[D, "open"], "preempt", D)
                            b = enter(i, int(sig.conf), sig, o, "coin", D, cap)
                    blocked += b

        # 주식 시가 → 장중 손절 → 종가 판정
        if D in sess and S:
            k = sess.get_loc(D)
            if cands_next is not None:
                cands, cands_next = cands_next, None
            for t in list(pos):
                if pos[t]["sleeve"] == "stock" and D in S[t].index:
                    gap(t, S[t].at[D, "open"], D)
                    if t in pos and t in pend:
                        close(t, S[t].at[D, "open"], pend[t], D)
            if not halted and not no_stock and k > 0:
                for t in cands:
                    x = S.get(t)
                    if x is None or t in pos or D not in x.index or sess[k - 1] not in x.index:
                        continue
                    sig = x.loc[sess[k - 1]]
                    if sig.conf == 1 and blk.get(t) != 1 and not np.isnan(sig.atr) and (entry_ok is None or entry_ok(t, sess[k - 1])):
                        blocked += enter(t, 1, sig, x.at[D, "open"], "stock", D, cap)
            no_stock = False
            for t in [t for t in pos if pos[t]["sleeve"] == "stock" and D in S[t].index]:
                intraday(t, S[t].loc[D], D)
            if D in refresh:
                cands_next = m5(closes.loc[:D], members_at(D), listed)
            nxt = cands_next if cands_next is not None else cands
            for t in set(nxt) | {t for t in pos if pos[t]["sleeve"] == "stock"} | set(blk) & set(S):
                if t in S and D in S[t].index:
                    x = S[t].loc[D]
                    if t in pos:
                        last[t] = x.close
                    judge(t, int(x.conf == 1 and t in nxt), x, D)

        # 코인 장중 손절 → 펀딩(하루 3회) → 종가 판정
        for i, x in X.items():
            if D in x.index:
                intraday(i, x.loc[D], D)
                if i in pos:
                    last[i] = x.at[D, "close"]
        for i, p in pos.items():
            f = (-abs(3 * funding_8h) * p["qty"] * last[i] if fund_adverse
                 else funding_pnl(p["side"], 3 * funding_8h, p["qty"] * last[i]))
            cash += f
            p["fund"] += f
        for i, x in X.items():
            if D in x.index:
                judge(i, int(x.at[D, "conf"]), x.loc[D], D)
        days.append({"date": D, "equity": eq, "dd": 1 - eq / peak, "blocked": blocked, "daily_loss": dl,
                     "halted": halted, "n_pos": len(pos), "margin_pct": sum(p["margin"] for p in pos.values()) / eq,
                     "cands": ",".join(cands)})
    return pd.Series(eqs), pd.DataFrame(trades), pd.DataFrame(days).set_index("date")
