"""quant_nasq100(나스닥 100 봇)에서 검증돼 채택된 퀀트 기법을 코인 봇 규칙 위에 얹어 본다.

가져온 것 (그쪽 README 의 '채택한 규칙' / '넣지 않기로 한 것' 기준):
  · 변동성 타겟팅   실현 변동성이 목표를 넘으면 노출을 깎는다. cap = min(1, 목표/실현변동성)
                    저쪽: Sharpe 1.32→1.34, MDD −36.9%→−31.2% (검증 구간). 타겟·창 폭넓게 견고했다
  · 고정 만기 청산   보유 N일이 지나면 신호와 무관하게 판다. 저쪽에서 승률 56.4% 로 모멘텀 청산(42.1%)을 이겼다
  · 하락 국면 전환   지수 60일 수익률 < −3% 면 하락 국면 → 저쪽은 저변동성 종목으로 갈아탄다.
                    단일 종목인 여기서는 '레버리지를 1배로 낮춘다' 로 옮긴다
  · 탐색/검증 분할   두 구간 모두에서 기준선을 이겨야 채택. 한쪽만 이기면 과적합으로 본다
  · 지표             CAGR · 연율 변동성 · Sharpe(= CAGR / 변동성) · MDD. 누적%만 보면 위험이 안 보인다
저쪽이 재보고 떨어뜨린 것(손절선·비중 트림·하락장 매매 중단)은 이 코인 백테스트에서도 이미 같은 결론이
나 있다 (research_okx_short.txt 익절·손절 비교). 그래서 다시 재지 않는다.

기준선 = 현재 봇 규칙: 일봉 강세 CONF개 이상 & 4h 종가가 직전 12봉 고가 돌파 → 직전 6봉 저가 이탈/국면 붕괴에 청산.
체결·비용 가정은 backtest_okx.py 와 같다 (신호봉 종가 → 다음 봉 시가, 편도 0.07%, 펀딩 0.01%/8h, 격리 청산).
분봉 하한 방어(reclaim)는 여기 없다 — 4h 단위 비교라 backtest_entry.py 의 'now' 와 같은 자리다.

사용: python backtest_quant.py [편도비용]
"""
import sys

import numpy as np
import pandas as pd

import backtest_okx as B
import engine as E
import model as M

COST = B.arg_cost(__file__)
START, SPLIT = "2021-03-01", "2024-01-01"     # 탐색 2021-03~2023 / 검증 2024~2026 (둘 다 하락 구간 포함)
BPD = 6                                       # 하루 4h 봉 개수
FUND = 0.0001 * 3 / BPD                       # 4h 봉당 펀딩
YEAR = 365                                    # 코인은 24/7 — 주식의 252 자리
MAX_LEV = 3                                   # autotrade.MAX_LEVERAGE. 타겟팅은 깎기만 하고 올리지 않는다
VOL_WIN = 60                                  # 실현 변동성 창 (일). quant_nasq100 기본값과 같다
BEAR_RET60 = -3                               # 60일 수익률이 이 밑이면 하락 국면 (저쪽 BEAR_RET60_PCT)


def frames():
    """4h 봉 + 봉마다 (진입신호, 청산신호, BTC 실현변동성%, 60일 수익률%). 전부 과거 정보만 쓴다."""
    d1, h4 = B.fetch("1d"), B.fetch("4h")
    rg = M.align_daily(B.bull(d1) >= M.CONF, h4.index).fillna(False).astype(bool)   # 판단 시각 = 4h 봉 종료
    hi = h4.high.rolling(M.H4_N).max().shift(1)
    lo = h4.low.rolling(M.H4_M).min().shift(1)

    r1 = d1.close.pct_change()
    btc_vol = (r1.rolling(VOL_WIN).std() * np.sqrt(YEAR) * 100).shift(1)   # 어제까지의 정보만
    ret60 = ((d1.close / d1.close.shift(60) - 1) * 100).shift(1)
    ext = lambda s: M.align_daily(s, h4.index)          # 변동성·60일 수익률도 같은 시각 기준
    return h4, ((h4.close > hi) & rg).values, ((h4.close < lo) | ~rg).values, ext(btc_vol).values, ext(ret60).values


def reclaim_fills():
    """실제 봇이 쓰는 분봉 하한 방어(research_entry_timing.txt)의 체결을 이벤트별로 구한다.
    → {진입봉 시각: (체결가, 체결 후 저가, 체결 후 고가)} · 창 안에 하한을 못 되찾은 자리는 None (봇도 포기한다).

    체결 후 극단까지 주는 이유: 진입한 그 4h 봉 안에서도 강제청산은 일어난다. 그런데 그 봉의 저가는
    체결 **전에** 지나갔을 수 있다 — 그건 이 포지션과 무관하다. engine.run 이 쓰는 값은 체결 시각 이후뿐이다.
    분봉이 10개도 없는 이벤트는 키를 넣지 않는다 → 시가 시장가로 떨어진다 (지금 표본에선 0건).
    ※ 분봉 자체가 없는 구간은 '미검증' 이다. audit/manifest.json 의 okx_1m 결측 참고."""
    import backtest_entry as E2
    evs, h4e = E2.events()
    mins = E2.minutes(evs)
    out = {}
    for t, i, _, hi, _ in evs:
        w = mins[t][1]
        if len(w) < 10:
            continue
        r = E2.reclaim_event(w, hi)
        if r is None:
            out[h4e.index[i]] = None
            continue
        px, ts = r
        after = w[w.index > ts]
        out[h4e.index[i]] = (px, min(px, float(after.low.min())) if len(after) else px,
                             max(px, float(after.high.max())) if len(after) else px)
    return out


def simulate(h4, entry, exit_, lev_of, hold_days=0, fills=None, side=1, pos_pct=1.0):
    """engine.run 의 얇은 껍데기 — 체결·청산 판정은 engine.py 한 곳에만 있다 (2026-09-10 감사).
    hold_days>0 이면 그만큼 지나면 신호와 무관하게 판다 (고정 만기 청산).
    side=1 롱 · side=-1 숏. → (자본곡선, 거래수익률 배열, stats)"""
    z = np.zeros(len(h4), bool)
    args = (entry, exit_, z, z) if side > 0 else (z, z, entry, exit_)
    curve, t, stats = E.run(h4, *args, lev_of, allow=("long",) if side > 0 else ("short",),
                            fills=fills, hold_bars=hold_days * BPD if hold_days else None, pos_pct=pos_pct)
    return curve, (t.ret.values if len(t) else np.zeros(1)), stats


def acct_vol(i, curve, win=VOL_WIN):
    """진입 직전까지의 계좌 일별 수익률로 잰 연율 실현 변동성(%). 이력이 모자라면 None.
    quant_nasq100 의 exposure_cap() 과 같은 계산 — 자산 자체가 아니라 계좌의 변동성을 본다."""
    daily = curve[max(0, i - win * BPD):i:BPD]
    if len(daily) < win // 2 or (daily <= 0).any():
        return None
    if len(r := np.diff(daily) / daily[:-1]) < 2:
        return None
    v = float(np.std(r, ddof=1) * np.sqrt(YEAR) * 100)
    return v if v > 0 else None            # 창 내내 쉬었으면 변동성 0 → 잴 것이 없다 (타겟팅 비활성)


def metrics(curve, trades, stats, lo=None, hi=None):
    """일별로 다시 샘플링해 CAGR·연율변동성·Sharpe·MDD. 구간을 잘라도 지표 정의는 같다."""
    c = curve[lo:hi]
    d = c.resample("D").last().dropna()
    d = d / d.iloc[0]
    r = d.pct_change().dropna()
    yrs = len(d) / YEAR
    cagr = (d.iloc[-1] ** (1 / yrs) - 1) * 100 if d.iloc[-1] > 0 and yrs > 0 else -100.0
    vol = r.std() * np.sqrt(YEAR) * 100
    return {"누적": d.iloc[-1] * 100 - 100, "cagr": cagr, "vol": vol, "sharpe": cagr / vol if vol else 0,
            "mdd": (d / d.cummax() - 1).min() * 100, "거래": len(trades), "승률": (trades > 0).mean() * 100,
            "최악": trades.min() * 100, "청산": _liq(stats)}


def _liq(stats):
    """stats dict 또는 옛 int 를 청산 횟수로."""
    return stats.get("liq", 0) if isinstance(stats, dict) else int(stats or 0)


def report(name, curve, trades, stats):
    a, b, f = (metrics(curve, trades, stats, *s) for s in ((START, SPLIT), (SPLIT, None), (START, None)))
    mark = ""
    if isinstance(stats, dict):
        mark = ("!" if stats.get("liq_ambiguous") else "") + ("*" if stats.get("open_at_end") else "")
    print(f"{name:24s}"
          f"{a['cagr']:+7.0f}%{a['mdd']:7.1f}%{a['sharpe']:6.2f} |"
          f"{b['cagr']:+7.0f}%{b['mdd']:7.1f}%{b['sharpe']:6.2f} |"
          f"{f['누적']:+8.0f}%{f['mdd']:7.1f}%{f['sharpe']:6.2f}"
          f"{f['거래']:5d}{f['승률']:5.0f}%{f['최악']:7.1f}%{_liq(stats):3d}{mark}")


def main():
    h4, entry, exit_, btc_vol, ret60 = frames()
    m = h4.index >= pd.Timestamp(START, tz="UTC")
    h4, entry, exit_, btc_vol, ret60 = h4[m], entry[m], exit_[m], btc_vol[m], ret60[m]
    print(f"OKX BTC 4h {h4.index[0]:%Y-%m-%d}~{h4.index[-1]:%Y-%m-%d} · CONF={M.CONF} · 편도 {COST * 100:.2f}%")
    print(f"탐색 {START}~{SPLIT} / 검증 {SPLIT}~ · Sharpe = CAGR / 연율변동성 (quant_nasq100 정의)")
    print(f"\n{'':24s}{'──── 탐색 ────':>22} |{'──── 검증 ────':>22} |{'────── 전체 ──────':>28}"
          f"{'거래':>5}{'승률':>6}{'최악':>7}{'청산':>4}")
    print(f"{'':24s}{'CAGR':>7}{'MDD':>7}{'Sh':>6} |{'CAGR':>7}{'MDD':>7}{'Sh':>6} |"
          f"{'누적':>9}{'MDD':>7}{'Sh':>6}")

    base = {}
    print("\n── 기준선 (현재 봇 규칙, 고정 레버리지) ──")
    for lev in (1, 2, 3):
        base[lev] = simulate(h4, entry, exit_, lambda i, c, L=lev: L)
        report(f"고정 {lev}배", *base[lev])

    print("\n── ① 변동성 타겟팅 · 계좌 실현변동성 기준 (저쪽 exposure_cap 과 같은 계산) ──")
    for tg in (40, 60, 80, 120):
        def lev_of(i, c, tg=tg):
            v = acct_vol(i, c)
            return MAX_LEV if v is None else min(MAX_LEV, tg / v)   # 이력 부족하면 3배 (저쪽도 기능 비활성)
        report(f"3배 +VT(계좌) {tg}%", *simulate(h4, entry, exit_, lev_of))

    print("\n── ② 변동성 타겟팅 · BTC 실현변동성 기준 (단일 종목이라 이쪽이 자연스럽다) ──")
    for tg in (40, 60, 80, 120):
        def lev_of(i, c, tg=tg):
            v = btc_vol[i]
            return MAX_LEV if not np.isfinite(v) or v <= 0 else min(MAX_LEV, tg / v)
        report(f"3배 +VT(BTC) {tg}%", *simulate(h4, entry, exit_, lev_of))

    print("\n── ③ 고정 만기 청산 (구조 청산은 그대로 두고 상한만 추가) ──")
    for hd in (3, 5, 10, 20, 40):
        report(f"3배 +만기 {hd}일", *simulate(h4, entry, exit_, lambda i, c: 3, hold_days=hd))

    print("\n── ④ 하락 국면 (60일 수익률 < −3%) ──")
    report("3배 → 하락국면 1배", *simulate(h4, entry, exit_,
           lambda i, c: 1.0 if (np.isfinite(ret60[i]) and ret60[i] < BEAR_RET60) else 3.0))
    report("하락국면 진입금지", *simulate(h4, entry, exit_,
           lambda i, c: 0.0 if (np.isfinite(ret60[i]) and ret60[i] < BEAR_RET60) else 3.0))

    print("\n── ⑤ 조합: VT(BTC) + 하락국면 축소 ──")
    for tg in (60, 80):
        def lev_of(i, c, tg=tg):
            v = btc_vol[i]
            lv = MAX_LEV if not np.isfinite(v) or v <= 0 else min(MAX_LEV, tg / v)
            return min(lv, 1.0) if (np.isfinite(ret60[i]) and ret60[i] < BEAR_RET60) else lv
        report(f"VT(BTC){tg}% + 하락1배", *simulate(h4, entry, exit_, lev_of))

    print("\n── ⑥ 실제 봇 진입(분봉 하한 재확보)에 변동성 타겟팅을 얹으면 ──")
    fills = reclaim_fills()
    print(f"   (진입 이벤트 {len(fills)}건 중 하한 미회복으로 포기 {sum(v is None for v in fills.values())}건)")
    rc = {}
    for lev in (1, 3):
        rc[lev] = simulate(h4, entry, exit_, lambda i, c, L=lev: L, fills=fills)
        report(f"reclaim 고정 {lev}배", *rc[lev])
    for tg, win in ((30, 60), (40, 60), (30, 120), (40, 120)):
        def lev_of(i, c, tg=tg, win=win):
            v = acct_vol(i, c, win)
            return MAX_LEV if v is None else min(MAX_LEV, tg / v)
        report(f"reclaim +VT {tg}%·창{win}", *simulate(h4, entry, exit_, lev_of, fills=fills))

    if "--final" in sys.argv:
        # 실전 설정(하한 재확보 진입)에서 변동성 타겟팅을 확정한다. 앞의 --robust 는 'now' 진입 기준이었다.
        fl = reclaim_fills()
        base3 = simulate(h4, entry, exit_, lambda i, c: 3, fills=fl)
        print("\n\n═══ 실전 경로(하한 재확보) 위에서 변동성 타겟팅 확정 ═══")
        print("\n[기준] reclaim 고정 3배")
        report("reclaim 3배", *base3)

        print("\n── 타겟 × 창 (전 조합) ──")
        for win in (30, 40, 60, 90, 120):
            for tg in (20, 30, 40, 50, 60):
                def lev_of(i, c, tg=tg, win=win):
                    v = acct_vol(i, c, win)
                    return MAX_LEV if v is None else min(MAX_LEV, tg / v)
                report(f"reclaim VT {tg}%·창{win}", *simulate(h4, entry, exit_, lev_of, fills=fl))
            print()

        print("── 워밍업(자산 이력이 창의 절반도 없을 때) 대체값 ──")
        print("   실전에서 이 구간은 계좌를 새로 시작할 때마다 반드시 지나간다. 지금 실계좌가 딱 여기다.")
        for name, fb in (("3배 그대로(현재)", lambda i: MAX_LEV),
                         ("BTC 변동성으로", lambda i: MAX_LEV if not np.isfinite(btc_vol[i]) or btc_vol[i] <= 0
                          else min(MAX_LEV, 40 / btc_vol[i])),
                         ("2배로 낮춤", lambda i: 2.0),
                         ("1배로 낮춤", lambda i: 1.0)):
            def lev_of(i, c, fb=fb):
                v = acct_vol(i, c)
                return fb(i) if v is None else min(MAX_LEV, 40 / v)
            report(f"워밍업 → {name}", *simulate(h4, entry, exit_, lev_of, fills=fl))

        n_wait = sum(1 for i in range(1, len(h4)) if entry[i - 1] and acct_vol(i, base3[0].values) is None)
        print(f"\n   (전체 진입 {len(base3[1])}건 중 워밍업·무거래로 타겟팅이 쉬어 간 진입 {n_wait}건)")

        # 하한 재확보의 마지막 미측정 손잡이: 자리를 몇 시간까지 기다릴 것인가 (봇은 지금 다음 4h 봉 마감까지)
        import backtest_entry as E
        print("\n── 하한 재확보: 진입 창 길이 (기다리는 시간) ──")
        print("   창을 넘기면 자리를 포기한다. 길게 기다릴수록 놓치는 자리는 줄지만 늦게 산다.")
        print("   ※ 6·8시간은 다음 4h 봉으로 넘어가므로 체결 시각을 진입봉에 눌러 재는 근사가 섞인다 (4h 이하는 정확).")
        keep = E.WIN_H
        for h in (2, 3, 4, 6, 8):
            E.WIN_H = h
            f2 = reclaim_fills()
            skipped = sum(v is None for v in f2.values())
            def lev_of(i, c):
                v = acct_vol(i, c, 40)
                return MAX_LEV if v is None else min(MAX_LEV, 40 / v)
            report(f"창 {h}h · 포기 {skipped:2d}건 · 3배", *simulate(h4, entry, exit_, lambda i, c: 3, fills=f2))
            report(f"창 {h}h · 포기 {skipped:2d}건 · +VT", *simulate(h4, entry, exit_, lev_of, fills=f2))
            print()
        E.WIN_H = keep

    print("\n── 참고 ──")
    report("BTC 상시보유 1배", h4.close / h4.close.iloc[0], np.zeros(1), 0)

    if "--robust" in sys.argv:
        # 채택 후보는 이웃값에서도 버텨야 한다 (저쪽이 '타겟 25~35%·창 40~120일 모두 견고' 를 확인한 것과 같은 절차)
        print("\n══ 견고성: VT(계좌) 타겟 × 창 ══")
        for win in (40, 60, 90, 120):
            for tg in (15, 20, 30, 40, 50):
                def lev_of(i, c, tg=tg, win=win):
                    v = acct_vol(i, c, win)
                    return MAX_LEV if v is None else min(MAX_LEV, tg / v)
                report(f"VT(계좌) {tg}% · 창{win}일", *simulate(h4, entry, exit_, lev_of))
            print()

        print("══ 견고성: 고정 만기 청산 (이웃값) ══")
        for hd in (1, 2, 3, 4, 6, 8):
            report(f"3배 +만기 {hd}일", *simulate(h4, entry, exit_, lambda i, c: 3, hold_days=hd))

        print("\n══ 조합: VT(계좌) + 만기 청산 ══")
        for tg in (20, 30, 40):
            for hd in (3, 5):
                def lev_of(i, c, tg=tg):
                    v = acct_vol(i, c)
                    return MAX_LEV if v is None else min(MAX_LEV, tg / v)
                report(f"VT {tg}% + 만기 {hd}일", *simulate(h4, entry, exit_, lev_of, hold_days=hd))

    # 자체 점검: 배선이 맞는지. 극단값에서 기준선과 같아져야 한다
    huge = simulate(h4, entry, exit_, lambda i, c: min(MAX_LEV, 1e9 / max(acct_vol(i, c) or 1, 1e-9)))
    assert abs(huge[0].iloc[-1] - base[3][0].iloc[-1]) < 1e-9, "타겟이 무한대면 3배 고정과 같아야 한다"
    forever = simulate(h4, entry, exit_, lambda i, c: 3, hold_days=10_000)
    assert abs(forever[0].iloc[-1] - base[3][0].iloc[-1]) < 1e-9, "만기가 무한대면 구조 청산만 남아야 한다"
    zero = simulate(h4, entry, exit_, lambda i, c: 0.0)
    assert zero[0].iloc[-1] == 1.0 and len(zero[1]) == 1, "레버리지 0 이면 아무것도 안 산다"
    print("\nok  자체 점검 통과 (타겟 무한대 = 기준선 · 만기 무한대 = 기준선 · 레버리지 0 = 무거래)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
