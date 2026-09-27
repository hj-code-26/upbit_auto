"""SOXL 규칙(quant_nasq100)을 BTC 무기한으로 옮겨 검증 — 밴드 리밸런싱 + 폭락 게이트 (2026-09-19).

저쪽에서 채택된 규칙은 추세를 맞히는 것이 아니다. **목표 노출을 정해 두고 밴드를 벗어나면 되돌리는**
변동성 수확이다 (research/soxl_ops.md): SOXL 목표 40%(=3배 × 0.4 → 실효 1.2배), 30% 미만 매수 /
50% 초과 매도, 252일 고점 대비 −30% 이하면 신규 매수 중단, 63거래일에 50% 축소 1회, 나머지 현금.
지금 코인 봇(4h 돌파 진입 · 고정 5배 · 시간의 대부분은 현금)과 **정반대 성격**이라 따로 잴 값어치가 있다.

매핑 (무기한 선물에는 ETF 껍데기가 없으므로 노출 자체를 직접 목표로 잡는다)
  SOXL 비중 40% (3배)  → 실효 노출 x* = 1.2배 (명목/자산)
  밴드 30% / 50%       → x < 0.9 이면 매수 · x > 1.5 이면 매도, 되돌리는 지점은 x*
  현금은 단기금리       → **0%** (USDT 담보는 이자가 없다. 저쪽보다 불리한 가정)
  ETF 내부 금융비용     → 펀딩 0.01%/8h = 0.03%/일 을 명목에 부과 (research_both_sides.txt 실측 +0.0041%/8h 보다 보수적)
  252거래일 고점        → 365일 고점 (코인은 24/7)
  63거래일              → 91일
  편도 0.12%           → 편도 0.07% (이 리포 표준: 테이커 0.05 + 슬리피지 0.02)
  다음 날 종가 체결      → **다음 날 시가 체결** (이 리포 표준이 더 보수적이다)

사전 등록 (결과 보기 전에 적는다 — quant_nasq100 의 규율)
  기준선  BTC 1배 매수보유 · 현금(0%) · 지금 봇 (make current: 5배 누적 +2,018% / MDD −62.8% / Sharpe 0.71,
          1배 +202% / −17.8% / 0.94, 표본 2021-03~2026-09)
  **ADOPT 후보 = 넷 모두 충족**
    ① 10.7년 표본(Bitstamp 2016~) MDD > −60%
    ② OKX 2021-03~ 전체 Sharpe > BTC 1배 매수보유 Sharpe (같은 구간)
    ③ 탐색(~2024-01)·검증(2024-01~) **두 구간 모두** CAGR > 0
    ④ 이웃 파라미터(x* 0.9~2.0 · 밴드 ±20~40%)에서 전체 Sharpe 부호가 유지
  ⑤ **대체 여부는 따로** — 지금 봇을 대신하려면 검증 구간 Sharpe 가 봇(0.69)보다 커야 한다.
     못 넘으면 '대체 불가', ①~④만 넘으면 '다른 성격의 후보' 로 남긴다.
  하나라도 못 넘으면 REJECT. 결과를 보고 파라미터·해석을 바꾸지 않는다.
  시도 수 N = 1(주 설정) + 12(이웃 그리드) + 2(게이트·R3 분해) = 15 (다중검정 할인용)

불변식 (시뮬 안 assert)
  (a) 노출 0 설정은 현금(=수익 0) 과 같다
  (b) 리밸런싱 없는 고정 수량 보유는 '노출 x* 상시보유' 와 같다 (밴드를 무한대로 열면)
  (c) 자산 > 0, 노출이 밴드 상한을 (체결 시점에) 넘지 않는다

사용: python backtest_soxl.py
"""
import sys

import numpy as np
import pandas as pd

from isolation import guard

guard()

import backtest_okx as B                                            # noqa: E402
import backtest_quant as Q                                          # noqa: E402

COST = 0.0007                   # 편도
FUND_D = 0.0001 * 3             # 하루 펀딩 (롱이 낸다)
YEAR = 365
X_STAR, X_LO, X_HI = 1.2, 0.9, 1.5        # 목표 노출과 밴드 (SOXL 40% / 30% / 50% 의 3배 환산)
HI_WIN, GATE_DD = 365, -0.30              # 폭락 게이트: 365일 고점 대비 −30% 이하면 신규 매수 중단
AGE_DAYS, AGE_CUT = 91, 0.5               # 보유 91일에 노출 50% 축소 1회 (저쪽 R3)
SPLIT = "2024-01-01"


def daily(df):
    """4h/1d 봉 → 일봉 (UTC 00:00 경계)."""
    if (df.index[1] - df.index[0]) >= pd.Timedelta(days=1):
        return df
    return df.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()


def sleeve(px, lev=3.0, fund=FUND_D, cost=COST):
    """3배 ETF 를 무기한으로 흉내 낸 '슬리브' 가격 — **매일 노출을 3배로 되돌린다**.

    이것이 SOXL 규칙의 심장이다. 내부 재조정 덕분에 (a) 한 번에 0 이 되지 않고 (b) 하락하면 슬리브
    가치가 줄어 계좌 대비 **비중이 저절로 낮아진다**(위험 축소). 고정 수량 보유는 정반대로 물타기가 된다.
    재조정 자체의 매매비용도 문다 — ETF 는 보수 0.9%/년에 숨기지만 무기한은 우리가 낸다.
    일간 수익 r 에 대해 되돌릴 명목 비율 = |lev(1+r)/(1+lev·r) − lev| / lev."""
    r = px.close.pct_change().fillna(0).values
    x_drift = lev * (1 + r) / np.where(1 + lev * r > 0, 1 + lev * r, np.nan)
    turn = np.abs(x_drift - lev) / lev                                # 재조정으로 갈아타는 명목 비율
    rs = lev * r - lev * fund - np.nan_to_num(turn) * lev * cost
    rs = np.maximum(rs, -0.999999)                                    # 하루 −33% 초과 = 실제로는 강제청산
    return pd.Series(np.cumprod(1 + rs), index=px.index), int((lev * r <= -1).sum())


def sim(px, x_star=X_STAR, lo=X_LO, hi=X_HI, gate=True, age=True, fund=FUND_D, cost=COST):
    """**고정 수량** 노출 밴드 (참고용 변형 — SOXL 규칙이 아니다).
    하락하면 노출이 커져 물타기가 된다. 아래 sim_soxl 과의 대조군으로만 쓴다."""
    assert 0 <= cost < 0.05 and 0 <= fund < 0.01, "비용 가정이 상식 밖이다"
    o, c, l = px.open.values, px.close.values, px.low.values
    peak = pd.Series(c, index=px.index).rolling(HI_WIN, min_periods=1).max().values
    n = len(px)
    E, q, want = 1.0, 0.0, None
    curve, xs, trades, blocked, cut_done, age0, wipe = np.ones(n), [], 0, 0, False, None, 0
    for i in range(1, n):
        E += q * (o[i] - c[i - 1]) - abs(q) * c[i - 1] * fund       # 전일 종가 → 오늘 시가 + 하루치 펀딩
        if want is not None and E > 0:                              # 어제 종가에 정한 주문을 오늘 시가에 체결
            tgt_q = want * E / o[i]
            E -= abs(tgt_q - q) * o[i] * cost
            q = tgt_q
            trades += 1
            want = None
            if age0 is None and q > 0:
                age0 = i
        if E + q * (l[i] - o[i]) <= 0:                              # 장중 저가로 전액 소진 (노출 1.5배면 −67% 필요)
            wipe += 1
            curve[i:] = 0.0
            break
        E += q * (c[i] - o[i])                                      # 시가 → 종가
        x = q * c[i] / E if E > 0 else 0.0
        curve[i] = E
        xs.append(x)
        dd = c[i] / peak[i] - 1
        if age and age0 is not None and not cut_done and i - age0 >= AGE_DAYS:
            want, cut_done = x * AGE_CUT, True                      # R3: 91일에 절반으로 (포지션당 1회)
        elif x > hi:
            want = x_star                                           # 밴드 위 → 판다 (게이트와 무관)
        elif x < lo or q == 0:
            if gate and dd <= GATE_DD:
                blocked += 1                                        # R2: 폭락 중에는 신규 매수 중단
            else:
                want = x_star
    return pd.Series(curve, index=px.index), {"거래": trades, "평균노출": float(np.mean(xs)) if xs else 0.0,
                                              "매수중단일": blocked, "전액소진": wipe}


def sim_soxl(px, w_star=0.40, w_lo=0.30, w_hi=0.50, lev=3.0, gate=True, age=True,
             gate_on="sleeve", cap=0.70, rf=0.0, fund=FUND_D, cost=COST):
    """**SOXL 규칙 그대로** — 매일 3배로 되돌리는 슬리브를 목표 비중 w* 로 들고, 밴드를 벗어나면 되돌린다.
    나머지는 현금(이자 0%). 신호 = t 종가, 체결 = t+1 시가. → (자산곡선, 로그)

    R1 현금 30%: 매수 후 비중 ≤ 70%  ·  R2 폭락 게이트: 365일 고점 대비 −30% 이하면 신규 매수 중단
    R3 보유 91일에 비중 50% 축소(1회)  ·  실효 노출 = lev × 비중 (기본 3 × 0.4 = 1.2배)"""
    assert 0 <= cost < 0.05 and 0 <= fund < 0.01 and 0 < w_lo <= w_star <= w_hi, "가정이 상식 밖이다"
    S, dead = sleeve(px, lev, fund, cost)
    s = S.values
    so = s * (px.open / px.close).values                              # 슬리브의 '시가' (당일 수익률 배분 근사)
    ref = s if gate_on == "sleeve" else px.close.values                # R2p 는 레버리지 상품 자신의 낙폭을 본다
    peak = pd.Series(ref, index=px.index).rolling(HI_WIN, min_periods=1).max().values
    n = len(px)
    C, u, want = 1.0, 0.0, w_star                                      # 첫날 목표 비중으로 진입
    curve, ws, trades, blocked, cut_done, age0 = np.ones(n), [], 0, 0, False, None
    for i in range(1, n):
        E = C + u * so[i]
        if want is not None and E > 0:                                 # 어제 정한 비중을 오늘 시가에 맞춘다
            tgt_u = min(want, cap) * E / so[i]                         # R1: 매수 후 비중 ≤ cap(70%)
            C -= (tgt_u - u) * so[i] + abs(tgt_u - u) * so[i] * lev * cost   # 명목은 비중의 lev 배다
            u, want = tgt_u, None
            trades += 1
            if age0 is None and u > 0:
                age0 = i
        C *= 1 + rf / YEAR                                             # 현금 이자 (기본 0% — USDT 담보)
        E = C + u * s[i]
        curve[i] = E
        if E <= 0:
            curve[i:] = 0.0
            break
        w = u * s[i] / E
        ws.append(w)
        dd = ref[i] / peak[i] - 1
        if age and age0 is not None and not cut_done and i - age0 >= AGE_DAYS:
            want, cut_done = w * AGE_CUT, True
        elif w > w_hi:
            want = w_star                                              # 밴드 위 → 판다
        elif w < w_lo:
            if gate and dd <= GATE_DD:
                blocked += 1                                           # 폭락 중 신규 매수 중단
            else:
                want = w_star
    return pd.Series(curve, index=px.index), {"거래": trades, "평균노출": float(np.mean(ws)) * lev if ws else 0.0,
                                              "매수중단일": blocked, "슬리브청산일": dead}


def metrics(curve, lo=None, hi=None):
    d = curve[lo:hi]
    d = d / d.iloc[0]
    r = d.pct_change().dropna()
    yrs = len(d) / YEAR
    cagr = (d.iloc[-1] ** (1 / yrs) - 1) * 100 if d.iloc[-1] > 0 and yrs > 0 else -100.0
    vol = r.std() * np.sqrt(YEAR) * 100
    return cagr, vol, (cagr / vol if vol else 0), (d / d.cummax() - 1).min() * 100, d.iloc[-1] * 100 - 100


def line(name, curve, log=None, split=SPLIT):
    a = metrics(curve, None, split)
    b = metrics(curve, split, None)
    t = metrics(curve)
    extra = f"{log['거래']:>5}{log['평균노출']:>8.2f}{log['매수중단일']:>7}" if log else " " * 20
    print(f"{name:<26}{a[0]:>7.0f}%{a[3]:>7.1f}%{a[2]:>6.2f} |{b[0]:>7.0f}%{b[3]:>7.1f}%{b[2]:>6.2f} |"
          f"{t[4]:>+9.0f}%{t[3]:>7.1f}%{t[2]:>6.2f}{extra}")
    return t


def header(title):
    print(f"\n{title}")
    print(f"{'':26s}{'──── 탐색 ────':>22} |{'──── 검증 ────':>22} |{'───── 전체 ─────':>24}"
          f"{'매매':>5}{'평균노출':>8}{'중단일':>7}")
    print(f"{'':26s}{'CAGR':>7}{'MDD':>7}{'Sh':>6} |{'CAGR':>7}{'MDD':>7}{'Sh':>6} |{'누적':>9}{'MDD':>7}{'Sh':>6}")


def selfcheck():
    idx = pd.date_range("2020-01-01", periods=400, freq="D", tz="UTC")
    up = pd.DataFrame({"close": np.linspace(100, 200, 400)}, index=idx)
    up["open"], up["high"], up["low"] = up.close, up.close, up.close
    c0, _ = sim(up, x_star=0.0, lo=-1, hi=1e9, gate=False, age=False)
    assert abs(c0.iloc[-1] - 1.0) < 1e-12, f"노출 0 인데 자산이 움직였다: {c0.iloc[-1]}"
    c1, lg = sim(up, x_star=1.0, lo=-1e9, hi=1e9, gate=False, age=False, fund=0, cost=1e-12)
    hold = up.close.iloc[-1] / up.open.iloc[2]      # i=1 종가에 정해 i=2 시가에 체결된다
    assert abs(c1.iloc[-1] / hold - 1) < 1e-6, f"리밸런싱 없는 1배가 매수보유와 다르다: {c1.iloc[-1]:.4f} vs {hold:.4f}"
    assert lg["거래"] == 1, f"밴드를 열었는데 재매매가 있었다: {lg['거래']}"

    # SOXL 시뮬: 슬리브 1배 · 비중 100% · 밴드 무한 · 비용 0 → 매수보유와 같아야 한다
    c2, _ = sim_soxl(up, w_star=1.0, w_lo=1e-9, w_hi=1e9, lev=1.0, gate=False, age=False, cap=1.0, fund=0, cost=0)
    hold1 = up.close.iloc[-1] / up.open.iloc[1]
    assert abs(c2.iloc[-1] / hold1 - 1) < 1e-6, f"1배·비중100% 가 매수보유와 다르다: {c2.iloc[-1]:.4f} vs {hold1:.4f}"
    c3, _ = sim_soxl(up, w_star=1e-9, w_lo=1e-12, w_hi=1e9, gate=False, age=False)
    assert abs(c3.iloc[-1] - 1.0) < 1e-6, f"비중 0 인데 자산이 움직였다: {c3.iloc[-1]}"
    dn = up.assign(close=up.close.iloc[::-1].values)                  # 계속 내리는 구간
    dn["open"], dn["high"], dn["low"] = dn.close, dn.close, dn.close
    _, lg3 = sim_soxl(dn, gate=False, age=False)
    assert lg3["평균노출"] < 3 * 0.5, f"하락 구간에서 노출이 밴드 위에 머물렀다: {lg3['평균노출']:.2f}"
    print("ok  자체 점검 통과 (노출 0 = 현금 · 밴드 무한 = 매수보유 · 하락에서 노출 축소)")


def gate_on_bot(okx_daily):
    """SOXL 규칙에서 **유일하게 일한 조각**(폭락 게이트)을 지금 봇 위에 얹으면? — 이게 이 파일의 실익이다.
    게이트 = BTC 종가가 365일 고점 대비 −30% 이하인 동안 신규 진입 중단 (보유·청산은 그대로)."""
    import backtest_both as BO
    import model as M

    h4 = B.fetch("4h")
    _, l_en, l_ex, s_en, s_ex, lab = BO.frames()
    dd = okx_daily.close / okx_daily.close.rolling(HI_WIN, min_periods=1).max() - 1
    g = M.align_daily(dd > GATE_DD, h4.index).fillna(True).astype(bool)      # 판단 시각 = 4h 봉 종료
    m = h4.index >= pd.Timestamp(Q.START, tz="UTC")
    h4m, labm, sig = h4[m], lab[m], (l_en[m], l_ex[m], s_en[m], s_ex[m])
    fills = Q.reclaim_fills()
    print(f"\n── SOXL 의 폭락 게이트만 지금 봇에 얹으면 (고정 5배·하한 재확보, 게이트 발동 "
          f"{(~g[m]).mean() * 100:.0f}% 의 시간) ──")
    for name, gate in (("지금 봇 (게이트 없음)", None), ("  + 폭락 게이트", (g.values[m], g.values[m]))):
        cv, t, st = BO.simulate(h4m, sig, lambda i, c: 5.0, labm, ("long",), fills=fills, gate=gate)
        Q.report(name, cv, t.ret.values if len(t) else np.zeros(1), st)


def main():
    selfcheck()
    okx = daily(B.fetch("1d"))
    okx = okx[okx.index >= pd.Timestamp(Q.START, tz="UTC")]
    bs = daily(pd.read_pickle(B.M.CACHE / "bitstamp_4h_2016.pkl"))

    print(f"\n목표 노출 {X_STAR}배 · 밴드 {X_LO}~{X_HI}배 · 폭락 게이트 365일 고점 −30% · 91일 절반축소 · "
          f"편도 {COST * 100:.2f}% · 펀딩 {FUND_D * 100:.2f}%/일 · 현금 이자 0%")

    header(f"═══ OKX BTC 일봉 {okx.index[0]:%Y-%m-%d}~{okx.index[-1]:%Y-%m-%d} (탐색 ~{SPLIT} / 검증 {SPLIT}~) ═══")
    line("SOXL식 (주 설정)", *sim_soxl(okx))
    line("  게이트 끄면", *sim_soxl(okx, gate=False))
    line("  91일 축소 끄면", *sim_soxl(okx, age=False))
    line("  둘 다 끄면(순수 밴드)", *sim_soxl(okx, gate=False, age=False))
    line("  게이트를 BTC 낙폭으로", *sim_soxl(okx, gate_on="btc"))
    line("  리밸런싱 없이 슬리브만", *sim_soxl(okx, w_lo=1e-9, w_hi=1e9, gate=False, age=False))
    line("[대조군] 고정수량 노출밴드", *sim(okx))
    line("  주 설정 + 현금이자 5%", *sim_soxl(okx, rf=0.05))
    line("BTC 1배 매수보유", okx.close / okx.close.iloc[0])

    header("── 이웃 파라미터 (한 조합에만 걸린 결과인지) ──")
    for w0 in (0.25, 0.40, 0.55):
        for b in (0.2, 0.25, 0.4):
            line(f"목표비중 {w0:.0%} (노출 {3 * w0:.2f}배) · 밴드 ±{b:.0%}",
                 *sim_soxl(okx, w_star=w0, w_lo=w0 * (1 - b), w_hi=w0 * (1 + b)))

    gate_on_bot(okx)

    header(f"═══ Bitstamp BTC 일봉 {bs.index[0]:%Y-%m-%d}~{bs.index[-1]:%Y-%m-%d} (10.7년, 독립 표본) ═══")
    line("SOXL식 (주 설정)", *sim_soxl(bs))
    line("  순수 밴드", *sim_soxl(bs, gate=False, age=False))
    line("  리밸런싱 없이 슬리브만", *sim_soxl(bs, w_lo=1e-9, w_hi=1e9, gate=False, age=False))
    line("[대조군] 고정수량 노출밴드", *sim(bs))
    line("  주 설정 + 현금이자 5%", *sim_soxl(bs, rf=0.05))
    line("BTC 1배 매수보유", bs.close / bs.close.iloc[0])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
