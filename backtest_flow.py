"""매수·매도 흐름에 반복되는 패턴이 있는가 — "많이 사면 많이 파는 날이 온다" 가설 검정.

OHLCV 에는 매수/매도 구분이 없다. 그래서 **바이낸스 USDT-M 무기한 선물의 원시 klines** 를 쓴다 —
9번 필드가 taker buy base volume(능동 매수 체결량)이다. 2019-09 부터 있다.
  TBR(taker buy ratio) = 능동 매수량 / 전체 거래량.  0.5 면 매수·매도 균형, 0.6 이면 매수 우위.
이건 대리 지표가 아니라 **실제로 시장가로 사고 판 양**이다.

묻는 것 다섯 가지
  ① TBR 이 뭉치는가 흩어지는가 — 매수 많은 봉 뒤에 또 매수인가(관성), 매도인가(반전)
  ② 사용자 가설 직접 검정: 매수 극단 뒤 N봉의 TBR 이 평균보다 낮아지는가
  ③ 주기성 — 요일·UTC 시간대에 반복되는 자리가 있는가
  ④ TBR 이 **가격**을 예측하는가 (흐름 자체가 아니라 수익률)
  ⑤ 그걸 규칙으로 만들면 봇이 나아지는가

사용: python backtest_flow.py
"""
import sys
import time

import ccxt
import numpy as np
import pandas as pd

import backtest_both as BO
import backtest_quant as Q
import model as M

CACHE = M.CACHE / "binance_taker_4h.pkl"


def taker(tf="4h"):
    """바이낸스 선물 원시 klines → open/high/low/close/volume/taker_buy. 캐시."""
    f = M.CACHE / f"binance_taker_{tf}.pkl"
    if f.exists():
        return pd.read_pickle(f)
    ex = ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "future"}})
    ms = {"4h": 14400, "1d": 86400}[tf] * 1000
    rows, since = [], ex.parse8601("2019-09-08T00:00:00Z")
    while since < ex.milliseconds():
        r = ex.fapiPublicGetKlines({"symbol": "BTCUSDT", "interval": tf, "startTime": since, "limit": 1500})
        if not r:
            break
        rows += r
        nxt = int(r[-1][0]) + ms
        if nxt <= since:
            break
        since = nxt
        time.sleep(0.05)
    d = pd.DataFrame([(int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]), float(k[9]))
                      for k in rows], columns=["ts", "open", "high", "low", "close", "volume", "tbuy"])
    d = d.drop_duplicates("ts")
    d.index = pd.to_datetime(d.ts, unit="ms", utc=True)
    d = d.drop(columns="ts").sort_index().iloc[:-1]
    d.to_pickle(f)
    return d


def main():
    d = taker("4h")
    d["tbr"] = d.tbuy / d.volume.replace(0, np.nan)
    d["tsell"] = d.volume - d.tbuy
    d["ret"] = d.close.pct_change() * 100
    d = d.dropna()
    print(f"바이낸스 BTCUSDT 무기한 4h {d.index[0]:%Y-%m-%d}~{d.index[-1]:%Y-%m-%d} · {len(d):,}봉 "
          f"({(d.index[-1] - d.index[0]).days / 365:.1f}년)")
    print(f"TBR 평균 {d.tbr.mean():.4f} · 중앙값 {d.tbr.median():.4f} · 표준편차 {d.tbr.std():.4f} "
          f"· 매수 우위 봉 비율 {(d.tbr > 0.5).mean() * 100:.0f}%")

    # ── ① 뭉치는가 흩어지는가 ──
    print("\n═══ ① TBR 의 자기상관 — 매수 많은 봉 뒤엔 매수인가 매도인가 ═══")
    print(f"  {'시차(봉)':>8}{'TBR 상관':>10}{'거래량 상관':>12}{'수익률 상관':>12}")
    for k in (1, 2, 3, 6, 12, 24, 42):
        print(f"  {k:>8}{d.tbr.autocorr(k):>10.3f}{np.log(d.volume).autocorr(k):>12.3f}{d.ret.autocorr(k):>12.3f}")
    print("  양수 = 뭉친다(관성) · 음수 = 반전. 거래량과 비교해서 읽을 것.")

    # ── ② 사용자 가설: 매수 극단 뒤에 매도가 오는가 ──
    print("\n═══ ② '많이 사면 많이 파는 날이 온다' 직접 검정 ═══")
    q = d.tbr.rank(pct=True)
    for lab, m in (("매수 극단 (TBR 상위 10%)", q >= 0.9), ("매수 우위 (상위 25%)", q >= 0.75),
                   ("매도 우위 (하위 25%)", q <= 0.25), ("매도 극단 (하위 10%)", q <= 0.1)):
        nxt = [d.tbr.shift(-k)[m].mean() for k in (1, 2, 3, 6)]
        print(f"  {lab:22s} 그 봉 TBR {d.tbr[m].mean():.4f} → 다음 1·2·3·6봉 "
              + " · ".join(f"{x:.4f}" for x in nxt) + f"   (전체 평균 {d.tbr.mean():.4f})")
    print("  다음 봉 TBR 이 전체 평균보다 **낮으면** 가설대로 매도가 따라온 것이다.")

    # 절대량으로도 본다 (비율이 아니라 '많이')
    print("\n  절대량으로 보면 (거래량 상위 10% 봉 뒤 6봉의 평균 거래량 / 전체 평균):")
    vq = d.volume.rank(pct=True)
    for lab, m in (("거래량 폭발 (상위 10%)", vq >= 0.9), ("거래량 바닥 (하위 10%)", vq <= 0.1)):
        nxt = [d.volume.shift(-k)[m].mean() / d.volume.mean() for k in (1, 3, 6, 12)]
        print(f"  {lab:22s} 다음 1·3·6·12봉 배수 " + " · ".join(f"{x:.2f}x" for x in nxt))

    # ── ③ 주기성 ──
    print("\n═══ ③ 반복되는 자리 — 요일·시간대 ═══")
    d["dow"] = d.index.dayofweek
    d["hr"] = d.index.hour
    print(f"  {'요일':>6}{'TBR':>9}{'평균수익률':>12}{'거래량(평균=1)':>15}{'봉수':>7}")
    for k, nm in enumerate(["월", "화", "수", "목", "금", "토", "일"]):
        x = d[d.dow == k]
        print(f"  {nm:>6}{x.tbr.mean():>9.4f}{x.ret.mean():>+11.3f}%{x.volume.mean() / d.volume.mean():>14.2f}{len(x):>7}")
    print(f"\n  {'UTC시':>6}{'TBR':>9}{'평균수익률':>12}{'거래량(평균=1)':>15}{'봉수':>7}")
    for k in sorted(d.hr.unique()):
        x = d[d.hr == k]
        print(f"  {k:>4}시{x.tbr.mean():>9.4f}{x.ret.mean():>+11.3f}%{x.volume.mean() / d.volume.mean():>14.2f}{len(x):>7}")

    # ── ④ TBR 이 가격을 예측하는가 ──
    print("\n═══ ④ TBR 이 다음 수익률을 예측하는가 (흐름이 아니라 가격) ═══")
    print(f"  {'TBR 5분위':>12}{'그 봉 TBR':>11}{'다음 1봉':>10}{'다음 3봉':>10}{'다음 6봉':>10}{'다음 12봉':>11}{'봉수':>7}")
    d["qt"] = pd.qcut(d.tbr, 5, labels=False)
    for k in range(5):
        x = d[d.qt == k]
        f = [(d.close.shift(-n) / d.close - 1)[d.qt == k].mean() * 100 for n in (1, 3, 6, 12)]
        print(f"  {k + 1:>12}{x.tbr.mean():>11.4f}" + "".join(f"{v:>+9.2f}%" for v in f[:3]) + f"{f[3]:>+10.2f}%{len(x):>7}")
    print(f"  {'전체':>12}{d.tbr.mean():>11.4f}"
          + "".join(f"{(d.close.shift(-n) / d.close - 1).mean() * 100:>+9.2f}%" for n in (1, 3, 6))
          + f"{(d.close.shift(-12) / d.close - 1).mean() * 100:>+10.2f}%{len(d):>7}")

    # ── ⑤ 봇 진입에 얹으면 ──
    print("\n═══ ⑤ 봇 진입에 TBR 필터를 얹으면 (OKX 4h, 3배, 롱 전용) ═══")
    h4, l_en, l_ex, s_en, s_ex, lab = BO.frames()
    tbr = d.tbr.reindex(h4.index, method="ffill")          # 바이낸스 TBR 을 OKX 봉에 붙인다 (같은 4h 경계)
    tq = tbr.rank(pct=True)
    m = h4.index >= pd.Timestamp(Q.START, tz="UTC")
    h4, lab2 = h4[m], lab[m]
    sig = (l_en[m], l_ex[m], s_en[m], s_ex[m])
    print(f"{'':26s}{'탐색':>8}{'검증':>8}{'누적':>10}{'MDD':>8}{'Sh':>6}{'거래':>6}{'승률':>6}")

    def line(nm, gate):
        cv, t, lq = BO.simulate(h4, sig, lambda i, c: 3, lab2, ("long",), gate=None if gate is None else gate[m].values)
        r = t.ret.values if len(t) else np.zeros(1)
        a, b, f = (Q.metrics(cv, r, lq, *s) for s in ((None, Q.SPLIT), (Q.SPLIT, None), (None, None)))
        print(f"{nm:26s}{a['cagr']:>+7.0f}%{b['cagr']:>+7.0f}%{f['누적']:>+9.0f}%{f['mdd']:>7.1f}%"
              f"{f['sharpe']:>6.2f}{f['거래']:>6}{f['승률']:>5.0f}%")

    line("[기준] 필터 없음", None)
    line("TBR 상위 50% 일 때만", tq >= 0.5)
    line("TBR 상위 25% 일 때만", tq >= 0.75)
    line("TBR 하위 50% 일 때만", tq <= 0.5)
    line("TBR 상승 중일 때만", (tbr > tbr.shift(1)))

    # 자체 점검: TBR 은 0~1 이고, 매수+매도 = 전체 거래량이어야 한다
    assert d.tbr.between(0, 1).all(), "TBR 이 0~1 밖이다"
    assert np.allclose((d.tbuy + d.tsell) / d.volume, 1.0), "매수+매도가 전체 거래량과 다르다"
    assert abs(d.tbr.mean() - 0.5) < 0.1, "TBR 평균이 0.5 근처가 아니다 (필드를 잘못 읽었는가)"
    print("\nok  자체 점검 통과 (TBR 정의 · 매수+매도=전체)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
