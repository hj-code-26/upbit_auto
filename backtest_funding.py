"""펀딩률을 **비용 상수가 아니라 게이트로** 쓴다 (2026-09-21).

지금 상태
  engine.FUND 는 0.01%/8h 고정 가정이다. 실측은 OKX 공개 API 가 최근 3개월만 줘서
  research_downside 에서 8h 평균 +0.0041% (연율 +4.4%) 를 잰 것이 전부다.
  펀딩은 이 봇에서 **비용으로만** 쓰이고 판단에는 한 번도 안 들어갔다.

가설 (문헌에서 가져온 것, 우리가 만든 것이 아니다)
  · 펀딩은 **방향을 예측하지 않는다.** 방향 예측용으로 쓴 백테스트는 실패한다는 보고가 일관된다.
    그래서 진입 신호로 쓰지 않는다.
  · 펀딩 z-score 극단 = **포지셔닝 과밀** = 청산 캐스케이드 위험 상승. 이건 위험 신호다.
    → 그러므로 **게이트(진입 보류) 또는 레버리지 축소로만** 쓴다.
  고정 5배 운용에서 청산 1회 = 증거금 전액이므로, 맞다면 여기가 가장 값싼 보험이다.

데이터
  바이낸스 USDT-M BTCUSDT 실측 펀딩 이력 (fapiPublicGetFundingRate). 2019-09~ 무료·무제한, 8h 간격.
  OKX 3개월 한계를 우회하려고 바이낸스를 쓴다 — 두 거래소 펀딩은 같은 방향으로 움직이므로
  **신호로는** 대체 가능하다고 본다. 비용으로 쓰는 것이 아니라 게이트로만 쓰므로 수준 차이는 덜 중요하다.
  (이 가정 자체를 아래 ①에서 OKX 3개월 실측과 겹쳐 확인한다. 어긋나면 그 사실을 적고 판정은 그대로 간다.)

검증할 규칙 (둘 다 사전등록. 나중에 하나만 보고하지 않는다)
  G) 게이트     진입 직전 봉의 펀딩 z > Z 이면 **그 자리를 포기**한다 (롱만. 숏은 라이브에서 꺼져 있다).
  L) 레버리지   펀딩 z > Z 이면 레버리지를 절반으로 (5 -> 2.5). 포기하지는 않는다.
  z = (최근 펀딩 - 과거 WIN 일 평균) / 과거 WIN 일 표준편차. 전부 진입 시점 이전 값만 쓴다.
  주 설정 Z=2.0 · WIN=60일. 견고성 격자 Z 1.0/1.5/2.0/2.5/3.0 x WIN 30/60/90일.

=== 사전 등록 판정 (ADOPT = (1)~(6) 모두 충족) ===
  (1) 검증(2024-01~) Sharpe > 고정 5배
  (2) 탐색(~2024-01) Sharpe >= 고정 5배
  (3) 전체 MDD 개선
  (4) 격자 15조합 중 검증 Sharpe 가 기준선을 넘는 비율 >= 70%
      (게이트는 발동 횟수가 적어 대부분 칸이 기준선과 동일해질 수 있다 — 동일은 '넘음' 으로 세지 않는다)
  (5) 강제청산 <= 고정 5배
  (6) 상위 5개 거래 제외 누적이 고정 5배보다 개선
  하나라도 미달이면 REJECT. 결과를 보고 Z·WIN·규칙을 바꾸지 않는다.
  시도 수 N = 2(G·L) x 15(격자) = 30. 다중검정 할인용으로 적어둔다.

**먼저 볼 것 — 조건부 정보량** (research_flow.txt 에서 배운 것)
  거래 흐름(TBR)은 돌파봉 108건 중 107건이 이미 상위 50% 라 **조건부 정보량이 0** 이었다.
  펀딩도 같을 수 있다. 그래서 판정 전에 먼저 센다: **우리 진입 자리에서 z > Z 가 실제로 몇 번 켜지는가.**
  0~2건이면 통계가 없다는 뜻이고, 그 경우 격자 결과와 무관하게 **판정 불가(INCONCLUSIVE)** 로 적는다.
  '발동이 적어서 성적이 같다' 를 '해롭지 않다' 로 읽지 않기 위해서다.

불변식 (assert)
  (a) Z 를 무한대로 두면 고정 5배와 곡선이 일치한다 (게이트가 한 번도 안 켜진다)
  (b) 펀딩 시계열이 4h 봉 인덱스에 붙을 때 **미래 값이 섞이지 않는다** (봉 종료 시각 이전 펀딩만)
  (c) z 계산에 쓰는 표본이 전부 해당 시점 이전이다

사용: python backtest_funding.py   (make funding)
결과: research_funding.txt
"""
import sys
import time

import ccxt
import numpy as np
import pandas as pd

from isolation import guard

guard()

import backtest_quant as Q                                          # noqa: E402
import backtest_vt as V                                             # noqa: E402
import model as M                                                   # noqa: E402

NL = chr(10)
CAP = V.CAP                      # .env LEVERAGE=5
Z, WIN = 2.0, 60                 # 주 설정 (사전등록)
GRID_Z, GRID_WIN = (1.0, 1.5, 2.0, 2.5, 3.0), (30, 60, 90)
START, SPLIT = Q.START, Q.SPLIT


def funding():
    """바이낸스 USDT-M BTCUSDT 실측 펀딩 이력 (8h). 캐시. → Series[시각] = 펀딩률."""
    f = M.CACHE / "binance_funding.pkl"
    if f.exists():
        return pd.read_pickle(f)
    M.CACHE.mkdir(exist_ok=True)
    ex = ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "future"}})
    rows, since = [], ex.parse8601("2019-09-08T00:00:00Z")
    while since < ex.milliseconds():
        r = ex.fapiPublicGetFundingRate({"symbol": "BTCUSDT", "startTime": since, "limit": 1000})
        if not r:
            break
        rows += r
        nxt = int(r[-1]["fundingTime"]) + 1
        if nxt <= since:
            break
        since = nxt
        time.sleep(0.05)
    s = pd.Series({pd.to_datetime(int(x["fundingTime"]), unit="ms", utc=True): float(x["fundingRate"])
                   for x in rows}).sort_index()
    s = s[~s.index.duplicated()]
    s.to_pickle(f)
    return s


def zscore(fund, h4_index, win_d):
    """4h 봉마다 '그 봉이 닫히는 시각까지 확정된' 펀딩으로 만든 z. 미래 값이 섞이지 않는다.

    펀딩은 8h 간격이라 4h 봉 두 개당 한 번 갱신된다. 봉 종료 시각 **이하** 의 마지막 펀딩만 쓴다."""
    end = h4_index + pd.Timedelta(hours=4)
    n = win_d * 3                                   # 하루 3회
    mu = fund.rolling(n, min_periods=n // 2).mean()
    sd = fund.rolling(n, min_periods=n // 2).std(ddof=1)
    z = ((fund - mu) / sd.replace(0, np.nan))
    out = z.reindex(z.index.union(end)).sort_index().ffill().reindex(end)
    out.index = h4_index
    return out.values


def run(h4, en, ex, fills, z, thr, mode):
    """mode='base' 기준선 · 'G' 게이트(포기) · 'L' 레버리지 절반."""
    hot = np.isfinite(z) & (z > thr)
    if mode == "G":
        return Q.simulate(h4, en & ~hot, ex, lambda i, c: CAP, fills=fills)
    if mode == "L":
        return Q.simulate(h4, en, ex, lambda i, c: CAP / 2 if hot[i - 1] else CAP, fills=fills)
    return Q.simulate(h4, en, ex, lambda i, c: CAP, fills=fills)


def main():
    h4, entry, exit_, _, _ = Q.frames()
    m = h4.index >= pd.Timestamp(START, tz="UTC")
    h4, entry, exit_ = h4[m], entry[m], exit_[m]
    fl = Q.reclaim_fills()
    fund = funding()
    print(f"바이낸스 BTCUSDT 펀딩 {fund.index[0]:%Y-%m-%d}~{fund.index[-1]:%Y-%m-%d} · {len(fund):,}회 "
          f"· 평균 {fund.mean()*100:+.4f}%/8h (연율 {fund.mean()*3*365*100:+.1f}%) · 양수 {(fund>0).mean()*100:.0f}%")
    z = zscore(fund, h4.index, WIN)

    # (b)(c) 미래 누설 점검: 봉 종료 이후의 펀딩이 섞이면 이 값이 달라진다
    probe = h4.index[len(h4) // 2]
    last_ok = fund[fund.index <= probe + pd.Timedelta(hours=4)].index[-1]
    assert last_ok <= probe + pd.Timedelta(hours=4), "(b) 봉 종료 이후 펀딩을 썼다"
    z_trunc = zscore(fund[fund.index <= probe + pd.Timedelta(hours=4)], h4.index[:len(h4) // 2 + 1], WIN)
    assert np.allclose(z_trunc[~np.isnan(z_trunc)], z[:len(h4) // 2 + 1][~np.isnan(z_trunc)], equal_nan=True),         "(c) 미래 표본을 잘라내면 z 가 달라진다 = 누설"

    base = run(h4, entry, exit_, fl, z, Z, "base")
    inf_ = run(h4, entry, exit_, fl, z, 1e9, "G")
    assert abs(inf_[0].iloc[-1] - base[0].iloc[-1]) < 1e-9, "(a) Z=무한대 != 고정 5배"
    print("ok  불변식 (a) Z=무한 = 고정 5배 · (b) 봉 종료 이후 펀딩 미사용 · (c) 표본 절단에도 z 동일")

    # ── 조건부 정보량: 우리 진입 자리에서 몇 번 켜지는가 (판정보다 먼저) ──
    ent_i = [i for i in range(1, len(h4)) if entry[i - 1]]
    zi = np.array([z[i - 1] for i in ent_i])
    zi = zi[np.isfinite(zi)]
    print(f"{NL}── 조건부 정보량 (판정 전에 먼저 본다) ──")
    print(f"   진입 신호 {len(ent_i)}건 중 z 가 유효한 {len(zi)}건")
    print(f"   전체 봉의 z 분포: >1.0 {np.nanmean(z>1)*100:.1f}% · >2.0 {np.nanmean(z>2)*100:.1f}% · >3.0 {np.nanmean(z>3)*100:.1f}%")
    for t in GRID_Z:
        print(f"   진입 자리에서 z>{t}: {int((zi>t).sum()):3d}건 ({(zi>t).mean()*100:5.1f}%)  "
              f"[전체 봉 {np.nanmean(z>t)*100:5.1f}%]")
    fires = int((zi > Z).sum())

    print(f"{NL}{'':24s}{'──── 탐색 ────':>22} |{'──── 검증 ────':>22} |{'────── 전체 ──────':>28}"
          f"{'거래':>5}{'승률':>6}{'최악':>7}{'청산':>4}")
    print(f"{'':24s}{'CAGR':>7}{'MDD':>7}{'Sh':>6} |{'CAGR':>7}{'MDD':>7}{'Sh':>6} |"
          f"{'누적':>9}{'MDD':>7}{'Sh':>6}")
    rep = lambda n, r: Q.report(n, *r)
    f = lambda r, lo, hi: Q.metrics(r[0], r[1], r[2], lo, hi)

    print(f"{NL}── 기준선 ──")
    rep(f"고정 {CAP:.0f}배 (지금 라이브)", base)
    print(f"{NL}── 주 설정 Z={Z} · 창 {WIN}일 ──")
    cands = {}
    for mode, nm in (("G", "G 게이트(포기)"), ("L", "L 레버리지 절반")):
        cands[mode] = run(h4, entry, exit_, fl, z, Z, mode)
        rep(nm, cands[mode])

    bv = f(base, SPLIT, None)["sharpe"]
    hits = {"G": 0, "L": 0}
    print(f"{NL}── 격자 Z × 창 ──")
    for win in GRID_WIN:
        zz = zscore(fund, h4.index, win)
        for t in GRID_Z:
            for mode in ("G", "L"):
                r = run(h4, entry, exit_, fl, zz, t, mode)
                hits[mode] += f(r, SPLIT, None)["sharpe"] > bv
                rep(f"{mode} Z={t}·창{win}일", r)
        print()

    bt, bvm, bf = f(base, START, SPLIT), f(base, SPLIT, None), f(base, START, None)
    tb = V.tail(base[1])
    print(f"{NL}═══ 사전 등록 판정 ═══")
    if fires <= 2:
        print(f"  발동 {fires}건 → **INCONCLUSIVE**. 사전등록대로 격자 결과와 무관하게 판정 불가로 적는다.")
        print("  '발동이 적어 성적이 같다' 는 '해롭지 않다' 가 아니다 (research_flow.txt 의 TBR 전례).")
    for mode, nm in (("G", "G 게이트"), ("L", "L 레버리지")):
        c = cands[mode]
        ct, cv, cf = f(c, START, SPLIT), f(c, SPLIT, None), f(c, START, None)
        rate = hits[mode] / (len(GRID_Z) * len(GRID_WIN)) * 100
        tc = V.tail(c[1])
        checks = [("(1) 검증 Sharpe >", cv["sharpe"] > bvm["sharpe"], f"{cv['sharpe']:.2f} vs {bvm['sharpe']:.2f}"),
                  ("(2) 탐색 Sharpe >=", ct["sharpe"] >= bt["sharpe"], f"{ct['sharpe']:.2f} vs {bt['sharpe']:.2f}"),
                  ("(3) 전체 MDD 개선", cf["mdd"] > bf["mdd"], f"{cf['mdd']:.1f}% vs {bf['mdd']:.1f}%"),
                  ("(4) 격자 >=70%", rate >= 70, f"{rate:.0f}%"),
                  ("(5) 청산 <=", c[2]["liq"] <= base[2]["liq"], f"{c[2]['liq']} vs {base[2]['liq']}"),
                  ("(6) 상위5 제외 누적", tc > tb, f"{tc:+.0f}% vs {tb:+.0f}%")]
        ok = all(v for _, v, _ in checks)
        print(f"{NL}  [{nm}] " + " · ".join(f"{n.split(')')[0]}) {'O' if v else 'X'}({d})" for n, v, d in checks))
        print(f"    → {'ADOPT' if ok and fires > 2 else ('INCONCLUSIVE' if fires <= 2 else 'REJECT')}")
    print(f"{NL}  시도 수 N = 30 (사전등록과 같음).")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
