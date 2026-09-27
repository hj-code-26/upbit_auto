"""거래량 상위 10코인 포트폴리오 백테스트 — "BTC 단일 대신 quant_nasq100 처럼 여러 종목을 굴리면 나은가".

유니버스: POOL 중에서 **그 시점의** 30일 평균 거래대금(USDT) 상위 TOP 개. 오늘의 순위로 과거를 고르면 미래를 보는
것이라 매일 다시 고른다. 단 POOL 자체는 2026-09 에 OKX 에 살아 있는 코인이다 → 상장폐지 코인(LUNA 등)이 빠진
**생존 편향**이 남는다. 절대 수치보다 BTC 단일과의 상대 비교로 읽을 것.
주식·원자재 토큰(XAU·MSTR·SKHYNIX 등)은 뺐다.

전략 (둘 다 슬롯 SLOTS 개, 슬롯당 명목 = 진입 시점 자산/SLOTS × lev, 격리):
  rule  현재 봇 규칙을 코인마다: 일봉 강세 CONF개↑ & 4h 12봉 고가 돌파 → 6봉 저가 이탈/국면 붕괴에 청산.
        유니버스 밖으로 밀려나도 청산 신호까지는 들고 간다 (신규 진입만 막는다).
  mom   quant_nasq100 규칙: 일봉 마감마다 유니버스 안 20일 수익률 상위 MOM_K 개(양수만) 매수 → 20일 만기 청산.
체결·비용·펀딩·강제청산 순서는 engine.run 과 같다 (_selfcheck 가 1코인·1슬롯에서 engine 과 곡선 일치를 확인).
분봉 하한 재확보는 BTC 분봉만 있어서 여기엔 없다 → 기준선도 BTC '기본 규칙' 과 비교한다.

사용: python backtest_multi.py --fetch     POOL 일봉·4h 받기 (data_cache/okxm_*.pkl, 처음엔 10분 안팎)
      python backtest_multi.py             결과
"""
import sys
import time

import ccxt
import numpy as np
import pandas as pd

import backtest_okx as B
import backtest_quant as Q
import engine as E
import model as M

POOL = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "BNB", "LINK", "LTC", "BCH", "DOT", "AVAX", "TRX", "ETC",
        "FIL", "UNI", "ATOM", "NEAR", "SHIB", "EOS", "PEPE", "SUI", "ZEC", "HYPE", "TRUMP", "WLD", "ENA",
        "ARB", "AAVE", "XLM", "TON", "APT", "OP", "TAO", "POL", "CRV", "SAND", "APE"]
TOP, SLOTS, MOM_K, MOM_DAYS = 10, 10, 3, 20
VOL_DAYS = 30                                   # 거래대금 평균 창
MIN_HIST = 60                                   # 상장 직후 코인은 지표(50일선)가 안 서므로 제외


def fetch(coin, bar):
    f = M.CACHE / f"okxm_{coin}_{bar}.pkl"
    if f.exists():
        return pd.read_pickle(f)
    ex = ccxt.okx({"enableRateLimit": True})
    rows, after, stop = [], None, ex.parse8601("2021-01-01T00:00:00Z")
    while True:
        p = {"instId": f"{coin}-USDT-SWAP", "bar": bar, "limit": "100", **({"after": after} if after else {})}
        try:
            d = ex.publicGetMarketHistoryCandles(p)["data"]
        except ccxt.BadSymbol:
            return None
        except ccxt.BaseError as e:
            print(coin, bar, e, file=sys.stderr); time.sleep(2); continue
        if not d:
            break
        rows += d
        after = d[-1][0]
        if int(after) <= stop:
            break
    if not rows:
        return None
    d = pd.DataFrame([r[:6] + [r[7], r[8]] for r in rows],
                     columns=["ts", "open", "high", "low", "close", "contracts", "volume", "confirm"])
    d = d[d.confirm == "1"].drop_duplicates("ts")               # 진행 중 봉 제외
    d.index = pd.to_datetime(d.ts.astype("int64"), unit="ms", utc=True)
    d = d.sort_index()[["open", "high", "low", "close", "volume"]].astype(float)   # volume = 거래대금(USDT)
    d.to_pickle(f)
    return d


def load():
    """→ {코인: (일봉, 4h)} · 4h 공통 인덱스."""
    D = {}
    for c in POOL:
        d1, h4 = fetch(c, "1Dutc"), fetch(c, "4H")
        if d1 is not None and h4 is not None and len(d1) > MIN_HIST:
            D[c] = (d1, h4)
    idx = D["BTC"][1].index
    return D, idx[idx >= pd.Timestamp(Q.START, tz="UTC")]


def frames(D, idx):
    """코인별 4h 배열: o/l/h/c, 유니버스 소속, rule 진입·청산, mom 진입. 전부 봉 i 종가 시점에 아는 값."""
    vol = pd.DataFrame({c: d1.volume.rolling(VOL_DAYS).mean().where(d1.close.expanding().count() >= MIN_HIST)
                        for c, (d1, _) in D.items()})
    rank = vol.rank(axis=1, ascending=False)
    ret20 = pd.DataFrame({c: d1.close.pct_change(MOM_DAYS) for c, (d1, _) in D.items()})
    in_u = rank <= TOP
    mrank = ret20.where(in_u).rank(axis=1, ascending=False)
    mom = (mrank <= MOM_K) & (ret20 > 0) & in_u
    F = {}
    for c, (d1, h4) in D.items():
        h = h4.reindex(idx)                                        # 상장 전은 NaN
        rg = M.align_daily(B.bull(d1) >= M.CONF, idx).fillna(False).astype(bool)
        u = M.align_daily(in_u[c], idx).fillna(False).astype(bool)
        hi = h.high.rolling(M.H4_N).max().shift(1)
        lo = h.low.rolling(M.H4_M).min().shift(1)
        daily_close = (idx + pd.Timedelta(hours=4)).hour == 0      # 일봉이 닫히는 4h 봉 (20:00 UTC)
        m = M.align_daily(mom[c], idx).fillna(False).astype(bool) & daily_close
        ok = h.close.notna().values
        F[c] = {"o": h.open.values, "l": h.low.values, "h": h.high.values, "c": h.close.ffill().values,
                "u": u.values & ok, "rule_en": ((h.close > hi) & rg & u).values & ok,
                "rule_ex": ((h.close < lo) | ~rg).values, "mom_en": m.values & ok,
                "vrank": M.align_daily(rank[c], idx).values}
    return F


def simulate(F, idx, en_key, ex_key, lev_of, slots=SLOTS, hold_bars=None):
    """다종목 슬롯 포트폴리오. 봉 i 처리 순서는 engine.run 과 같다:
    1) 이전 봉 청산신호/만기 → 시가 청산 (시가가 이미 청산선 너머면 강제청산)  2) 저가 강제청산  3) 신규 진입 + 진입봉 청산.
    → (자본곡선, 거래 DataFrame, stats)"""
    C, n = E.cost(), len(idx)
    eq, pos, rows, liq, ambig = 1.0, {}, [], 0, 0
    curve, npos = np.ones(n), np.zeros(n)
    cap = hold_bars or 10 ** 9
    for i in range(1, n):
        for c in list(pos):
            f, (e, k, lev, N) = F[c], pos[c]
            k += 1
            want = (ex_key and bool(F[c][ex_key][i - 1])) or k >= cap
            lvl = E.liq_level(e, lev, 1)
            if want and not f["o"][i] <= lvl:
                r = N * (f["o"][i] / e - 1) - N * C * (1 + f["o"][i] / e) - k * E.FUND * N
                eq += r; rows.append({"coin": c, "ret": r / (N / lev), "how": "신호"}); del pos[c]
            elif want or f["l"][i] <= lvl:
                ambig += bool(want)
                eq -= N / lev; liq += 1; rows.append({"coin": c, "ret": -1.0, "how": "청산"}); del pos[c]
            else:
                pos[c] = (e, k, lev, N)
        if eq > 0 and len(pos) < slots:
            cand = sorted((c for c in F if c not in pos and F[c][en_key][i - 1] and not np.isnan(F[c]["o"][i])),
                          key=lambda c: F[c]["vrank"][i - 1])      # 동시에 여러 자리면 거래대금 순위 순
            base = eq                                              # 같은 봉에 들어가는 슬롯은 같은 크기
            for c in cand[:slots - len(pos)]:
                f, lev = F[c], lev_of(i, curve)
                e, N = f["o"][i], base / slots * lev
                if f["l"][i] <= E.liq_level(e, lev, 1):
                    eq -= N / lev; liq += 1; rows.append({"coin": c, "ret": -1.0, "how": "청산(진입봉)"})
                else:
                    pos[c] = (e, 0, lev, N)
        curve[i] = eq + sum(N * (F[c]["c"][i] / e - 1) for c, (e, _, _, N) in pos.items())
        npos[i] = len(pos)
        if curve[i] <= 0:
            curve[i:] = 0.0
            break
    t = pd.DataFrame(rows, columns=["coin", "ret", "how"])
    return pd.Series(curve, index=idx), t, {"liq": liq, "liq_ambiguous": ambig, "open_at_end": bool(pos),
                                             "avg_pos": float(npos.mean())}


def _selfcheck(D, idx):
    """1코인·1슬롯이면 engine.run 과 곡선이 같아야 한다 (같은 체결 규칙을 다시 짰으므로)."""
    F = frames({"BTC": D["BTC"]}, idx)
    f = F["BTC"]
    h4 = pd.DataFrame({"open": f["o"], "high": f["h"], "low": f["l"], "close": f["c"]}, index=idx)
    for lev in (1, 5):
        a = simulate(F, idx, "rule_en", "rule_ex", lambda i, c: lev, slots=1)[0]
        z = np.zeros(len(idx), bool)
        b = E.run(h4, f["rule_en"], f["rule_ex"], z, z, lambda i, c: lev, allow=("long",))[0]
        assert np.allclose(a.values, b.values), f"{lev}배: engine 과 곡선 불일치 (최대차 {np.abs(a.values - b.values).max()})"
    print("ok  1코인·1슬롯 = engine.run 곡선 일치")


def main():
    D, idx = load()
    print(f"POOL {len(D)}개 받음: {' '.join(D)}")
    _selfcheck(D, idx)
    F = frames(D, idx)
    u = pd.DataFrame({c: F[c]["u"] for c in F}, index=idx)
    print(f"\n유니버스 (거래대금 {VOL_DAYS}일 평균 상위 {TOP}) 에 한 번이라도 든 코인 · 기간 중 소속 비율:")
    print("  " + " · ".join(f"{c} {v * 100:.0f}%" for c, v in u.mean().sort_values(ascending=False).items() if v > 0))
    vt = lambda L: (lambda i, c: (lambda v: L if v is None else min(L, 40 / v))(Q.acct_vol(i, c, 40)))

    print(f"\n표본 {idx[0]:%Y-%m-%d}~{idx[-1]:%Y-%m-%d} · 편도 {E.cost() * 100:.2f}% · 탐색 {Q.START}~{Q.SPLIT} / 검증 {Q.SPLIT}~")
    print(f"{'':24s}{'──── 탐색 ────':>22} |{'──── 검증 ────':>22} |{'────── 전체 ──────':>23}"
          f"{'거래':>5}{'승률':>5}{'최악':>7}{'청산':>3}  평균보유")
    print(f"{'':24s}{'CAGR':>8}{'MDD':>7}{'Sh':>6} |{'CAGR':>7}{'MDD':>7}{'Sh':>6} |{'누적':>9}{'MDD':>7}{'Sh':>6}")
    btc_rule = {**F["BTC"]}
    # BTC 단일 기준선은 유니버스 조건 없이 (BTC 는 늘 1·2위라 사실상 같다)
    d1b, h4b = D["BTC"]
    rgb = M.align_daily(B.bull(d1b) >= M.CONF, idx).fillna(False).astype(bool)
    hb = h4b.reindex(idx)
    btc_rule["rule_en"] = ((hb.close > hb.high.rolling(M.H4_N).max().shift(1)) & rgb).values
    for L in (1, 3, 5):
        cv, t, st = simulate({"BTC": btc_rule}, idx, "rule_en", "rule_ex", lambda i, c, L=L: L, slots=1)
        Q.report(f"BTC 단일 규칙 {L}배", cv, t.ret.values if len(t) else np.zeros(1), st); print(f"  {st['avg_pos']:.2f}")
    print()
    for L in (1, 3, 5):
        cv, t, st = simulate(F, idx, "rule_en", "rule_ex", lambda i, c, L=L: L)
        Q.report(f"상위10 규칙 {L}배", cv, t.ret.values if len(t) else np.zeros(1), st); print(f"  {st['avg_pos']:.2f}")
    cv, t, st = simulate(F, idx, "rule_en", "rule_ex", vt(5))
    Q.report("상위10 규칙 VT40·상한5", cv, t.ret.values, st); print(f"  {st['avg_pos']:.2f}")
    rule5 = (cv, t)
    print()
    for L in (1, 3, 5):
        cv, t, st = simulate(F, idx, "mom_en", None, lambda i, c, L=L: L, hold_bars=MOM_DAYS * 6)
        Q.report(f"상위10 모멘텀 {L}배", cv, t.ret.values if len(t) else np.zeros(1), st); print(f"  {st['avg_pos']:.2f}")
    print()
    c = pd.Series(D["BTC"][1].close.reindex(idx).ffill().values, index=idx)
    Q.report("BTC 상시보유 1배", c / c.iloc[0], np.zeros(1), 0); print()

    cv, t = simulate(F, idx, "rule_en", "rule_ex", lambda i, c: 1)[:2]
    print("\n상위10 규칙 1배 — 코인별 거래 (수익률은 그 슬롯 증거금 대비):")
    g = t.groupby("coin").ret.agg(["count", "mean", "sum", lambda x: (x > 0).mean()]).sort_values("sum", ascending=False)
    for c, r in g.iterrows():
        print(f"  {c:6s} {int(r['count']):4d}건  평균 {r['mean'] * 100:+6.2f}%  합 {r['sum'] * 100:+7.0f}%  승률 {r.iloc[3] * 100:3.0f}%")
    top5 = t.ret.nlargest(5).index
    print(f"\n상위 5거래 제외 시 (단리 합) {t.ret.drop(top5).sum() * 100:+.0f}% / 전체 {t.ret.sum() * 100:+.0f}%")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if "--fetch" in sys.argv:
        for c in POOL:
            for bar in ("1Dutc", "4H"):
                d = fetch(c, bar)
                print(c, bar, "없음" if d is None else f"{len(d)}봉 {d.index[0]:%Y-%m-%d}~", flush=True)
    else:
        main()
