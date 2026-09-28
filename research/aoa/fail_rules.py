"""실패 대응 검증 — 사전등록 (2026-09-28, OOS 결과 보기 전 작성).

근거(IS 탐색, fail.py · fail2.py — BitMEX 2018-03~2021-12 만 봤다):
  롱·숏 가리지 않고 '평소 변동에 비해 큰 2% 움직임 · 24h 안 첫 신호 · 일간 변동성 낮음' = 조용한 장이 깨지는 움직임은
  되돌리지 않고 이어진다(실패율 46~49% vs 31~38%). 이미 출렁이는 장 안의 2% 는 되돌린다.
  척도 없는 판정값 z = |dev| ÷ (rv_1d/√24)   (dev = 5분 종가의 1h EMA 괴리 %, rv_1d = 직전 288봉 5분 로그수익 표준편차×√288×100)
  문턱은 IS 에서 고정: 급락 z* = 2.66 (IS 급락 신호 z 상위 3분위 경계) · 급등 z_s = 1.52 (IS 급등 신호 z 하위 3분위 경계).

가설 (각각 독립 판정, 4개 검정이라 우연 통과 가능성을 감안해 해석한다):
  H1 건너뛰기 : 지금 봇(strategy.py 규칙, 50·200일선 · 롱 2% · TP4/SL6/3일) + 신호 z ≥ 2.66 이면 진입 안 함
  H2 절반     : 지금 봇 + 신호 z ≥ 2.66 이면 0.5배
  H3 급락 숏  : 급락 신호(dev ≤ −2%, 일봉 필터 없음) 중 z ≥ 2.66 → 다음 봉 시가 숏, TP4/SL6/3일 (조용한 장이 깨진 하락을 따라간다)
  H4 급등 숏  : 급등 신호(dev ≥ +2%, 일봉 필터 없음) 중 z < 1.52 & 직전 24h 에 급등 신호 봉 ≥ 1 → 숏 TP4/SL6/3일
                (출렁이는 장 안의 급등만 받아친다). 1단계: IS 거래당 > 0 & 30건 이상이어야 OOS 로 간다. 아니면 그 자체로 기각.
비용 편도 0.07% · 펀딩 0 · 갭이면 시가 체결 · 한 봉에서 익절·손절 둘 다면 손절.

데이터 (fail 탐색에서 안 본 것만 판정에 쓴다):
  BTC OOS = OKX 2022-01~2026-09 (200일선 예열: 비트스탬프 일봉) · 참고 = 바이낸스 BTC 2022-01~2026-09 (같은 시장이라 판정엔 안 씀)
  알트 6종 = 바이낸스 5분봉 ETH·SOL·XRP·BNB·DOGE·ADA 2020-08-15~2026-09 (200일선이 자기 데이터로 계산되는 구간)
  BitMEX IS 는 참고로만 같이 낸다.
평가 (H1·H2): 1배, 자본 × (1 + 크기 × 순수익), MDD 는 보유 중 5분봉 저가 평가손 포함, 수익/위험 rr = 연복리 ÷ |MDD|.
채택 기준 (사전 고정):
  H1·H2: ① OKX OOS 에서 rr > 기준 & MDD 가 기준보다 깊지 않음  ② 알트 6종 중 4종 이상에서 rr > 기준
         (추가 보고: 건너뛴/줄인 거래의 거래당 수익 — 양수면 방어 비용이 수익을 깎는다는 뜻)
  H3·H4: ① OKX 2022-23 · 2024-25 두 구간 모두 거래당 > 0  ② OKX OOS 거래당 > 같은 구간 무작위 시각 숏(같은 건수, 10회 평균)
         ③ 알트 6종 중 4종 이상 거래당 > 0  ④ 승률 > 40% (사용자 규칙)
"""
import pathlib
import sys

import numpy as np
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[1]))
import features as F  # noqa: E402
import strategy as S  # noqa: E402
from clone import okx_5m  # noqa: E402
from sma50 import ALTS  # noqa: E402

COST, ZL, ZS = 0.07, 2.66, 1.52


def prep(c, long_daily):
    own = c.close.resample("1D").last()
    d1 = own if long_daily is None else pd.concat([long_daily[long_daily.index < own.index[0]], own])
    dev, bull = S.indicators(c, d1)
    rv = np.log(c.close).diff().rolling(288).std() * np.sqrt(288) * 100
    z = (dev.abs() / (rv / np.sqrt(24))).values
    return dict(o=c.open.values, h=c.high.values, l=c.low.values, dev=dev.values, bull=bull.values, z=z, idx=c.index)


def exit_at(p, k, side):
    o, h, l = p["o"], p["h"], p["l"]
    e = o[k]
    up, dn = (e * (1 + S.TP / 100), e * (1 - S.SL / 100)) if side == 1 else (e * (1 - S.TP / 100), e * (1 + S.SL / 100))
    end = min(k + S.MAXB, len(o) - 1)
    for j in range(k, end):
        if (l[j] <= dn) if side == 1 else (h[j] >= dn):
            px = min(dn, o[j]) if side == 1 else max(dn, o[j]); return j, side * (px / e - 1) * 100 - 2 * COST
        if (h[j] >= up) if side == 1 else (l[j] <= up):
            return j, side * (up / e - 1) * 100 - 2 * COST
    return end, side * (o[end] / e - 1) * 100 - 2 * COST


def bot(p, lo, hi, var):
    """var: V0 기준 | H1 건너뛰기 | H2 절반 → 성적 + 방어가 건드린 거래의 순수익 목록."""
    idx, dev, bull, z, l = p["idx"], p["dev"], p["bull"], p["z"], p["l"]
    i, i1 = idx.searchsorted(lo), idx.searchsorted(hi)
    eq, peak, mdd, rets, touched = 1.0, 1.0, 0.0, [], []
    while i < i1 - 1:
        if not (dev[i] <= -S.D and bull[i]):
            i += 1; continue
        hot = z[i] >= ZL
        k = i + 1
        j, net = exit_at(p, k, 1)
        if hot:
            touched.append(net)
        size = 0.0 if (hot and var == "H1") else (0.5 if (hot and var == "H2") else 1.0)
        if size:
            mae = min(l[k:j + 1].min() / p["o"][k] - 1, net / 100)
            mdd = min(mdd, eq * (1 + size * mae) / peak - 1)
            eq *= 1 + size * net / 100; peak = max(peak, eq); mdd = min(mdd, eq / peak - 1)
            rets.append(net)
        i = j + 1
    yrs = (min(hi, idx[-1]) - lo).days / 365.25
    cagr = (eq ** (1 / yrs) - 1) * 100
    return dict(n=len(rets), win=np.mean(np.array(rets) > 0) if rets else np.nan, cagr=cagr, mdd=mdd * 100,
                rr=cagr / abs(mdd * 100) if mdd < 0 else np.nan, touched=touched)


def short_rule(p, lo, hi, H):
    idx, dev, z = p["idx"], p["dev"], p["z"]
    up = dev >= S.D
    sig24 = pd.Series(up.astype(float)).rolling(288).sum().shift(1).values
    trig = (dev <= -S.D) & (z >= ZL) if H == "H3" else up & (z < ZS) & (sig24 >= 1)
    i, i1, out = max(idx.searchsorted(lo), 300), idx.searchsorted(hi), []
    while i < i1 - 1:
        if not trig[i]:
            i += 1; continue
        j, r = exit_at(p, i + 1, -1)
        out.append(r)
        i = j + 1
    return out


def rand_short(p, n, lo, hi, reps=10):
    idx = p["idx"]; a, b = idx.searchsorted(lo), idx.searchsorted(hi) - S.MAXB
    if not n:
        return np.nan
    rng = np.random.default_rng(0)
    return float(np.mean([exit_at(p, i, -1)[1] for _ in range(reps) for i in rng.integers(a, b, n)]))


ev = lambda r: float(np.mean(r)) if len(r) else np.nan  # noqa: E731
wr = lambda r: float(np.mean(np.array(r) > 0)) if len(r) else np.nan  # noqa: E731

if __name__ == "__main__":
    T = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731
    bs = pd.read_pickle(F.ROOT / "data_cache" / "bitstamp_1d_2016.pkl").close
    rd = lambda s: pd.read_pickle(F.ROOT / "data_cache" / f"binance_{s.lower()}_5m_2020.pkl").asfreq("5min").ffill()  # noqa: E731
    sets = {"OKX 22-26": (prep(okx_5m(), bs), "2022-01-01", "2026-10-01"),
            "(참고) 바이낸스BTC 22-26": (prep(rd("BTCUSDT"), bs), "2022-01-01", "2026-10-01"),
            "(참고) BitMEX IS 18-21": (prep(F.candles().loc["2018-01-01":"2022-01-10"], bs), "2018-03-05", "2022-01-01")}
    for s in ALTS:
        sets[s[:-4]] = (prep(rd(s), None), "2020-08-15", "2026-10-01")
    alts = [s[:-4] for s in ALTS]
    pd.set_option("display.width", 250)

    print("=== H1 건너뛰기 · H2 절반 (지금 봇 기준) ===")
    R = {(n, v): bot(p, T(lo), T(hi), v) for n, (p, lo, hi) in sets.items() for v in ("V0", "H1", "H2")}
    rows = []
    for n in sets:
        for v in ("V0", "H1", "H2"):
            r = R[(n, v)]
            rows.append(dict(데이터=n, 변형=v, 건수=r["n"], 승률=r["win"], 연복리=r["cagr"], MDD=r["mdd"], rr=r["rr"],
                             z높은거래=len(r["touched"]), 그거래당=ev(r["touched"])))
    print(pd.DataFrame(rows).round(3).to_string(index=False))
    for v in ("H1", "H2"):
        c1 = R[("OKX 22-26", v)]["rr"] > R[("OKX 22-26", "V0")]["rr"] and R[("OKX 22-26", v)]["mdd"] >= R[("OKX 22-26", "V0")]["mdd"]
        k = sum(R[(a, v)]["rr"] > R[(a, "V0")]["rr"] for a in alts)
        print(f"{v}: ① OKX rr·MDD {'통과' if c1 else '불합격'} · ② 알트 rr 개선 {k}/6 {'통과' if k >= 4 else '불합격'} → "
              f"{'채택' if c1 and k >= 4 else '기각'}")

    print("\n=== H3 급락 숏 · H4 급등 숏 ===")
    pI, loI, hiI = sets["(참고) BitMEX IS 18-21"]
    pO = sets["OKX 22-26"][0]
    for H in ("H3", "H4"):
        is_r = short_rule(pI, T(loI), T(hiI), H)
        print(f"{H} IS: {len(is_r)}건 거래당 {ev(is_r):+.3f}% 승률 {wr(is_r):.0%}")
        if H == "H4" and not (len(is_r) >= 30 and ev(is_r) > 0):
            print("  → 1단계(IS) 불합격, OOS 안 봄 → 기각"); continue
        o1, o2 = short_rule(pO, T("2022-01-01"), T("2024-01-01"), H), short_rule(pO, T("2024-01-01"), T("2026-10-01"), H)
        oo = o1 + o2
        rn = rand_short(pO, len(oo), T("2022-01-01"), T("2026-10-01"))
        ae = {a: short_rule(sets[a][0], T(sets[a][1]), T(sets[a][2]), H) for a in alts}
        ref = short_rule(sets["(참고) 바이낸스BTC 22-26"][0], T("2022-01-01"), T("2026-10-01"), H)
        ok = [ev(o1) > 0 and ev(o2) > 0, ev(oo) > rn, sum(ev(v) > 0 for v in ae.values()) >= 4, wr(oo) > 0.4]
        print(f"  OKX 22-23 {len(o1)}건 {ev(o1):+.3f}% · 24-26 {len(o2)}건 {ev(o2):+.3f}% · 합 승률 {wr(oo):.0%} · 무작위 숏 {rn:+.3f}%")
        print("  알트:", " · ".join(f"{a} {len(v)}건 {ev(v):+.2f}% ({wr(v):.0%})" for a, v in ae.items()))
        print(f"  (참고) 바이낸스BTC {len(ref)}건 {ev(ref):+.3f}%")
        print(f"  판정: {'채택' if all(ok) else '기각(' + ','.join('①②③④'[i] for i in range(4) if not ok[i]) + ')'}")
