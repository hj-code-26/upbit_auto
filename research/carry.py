"""펀딩 캐리 (현물 롱 + 무기한 숏) — 사전등록 설계서 (2026-09-28, 구현·결과 보기 전 작성). 운영 코드는 건드리지 않는다.

배경: research/aoa/range_explore.py — 5분봉 횡보 매매는 216조합 OOS 양수 0개. 방향 중립으로 남은 수익원은 펀딩뿐이었다
      (바이낸스 BTC 실측 2019-09~2026-09 연율 +11.6%, 횡보 국면 +8.0%, 양수 86%). 연도별 편차가 크다: 21 +31% · 22 +4% · 26(9월까지) +2%.

1. 무엇을 하나
   같은 BTC 수량을 현물로 사고(롱) 무기한 선물로 판다(숏). 가격이 오르든 내리든 두 다리의 손익이 상쇄되고,
   숏 쪽이 펀딩(8시간마다, 양수면 롱이 숏에게 지급)을 받는다. 수익 = 펀딩 합 − 진입·청산 비용 − 괴리(베이시스) 변동.

2. 구현 경로 (사용자 결정 — 백테스트는 둘 다 잰다)
   A. USDT 경로 : BTC-USDT 현물 매수 + BTC-USDT-SWAP 숏.  최소 단위 작음(0.01계약 ≈ 0.0001 BTC ≈ 8 USDT).
                  자본 = 현물 금액 + 선물 증거금. 선물 1배면 자본 2배 필요 → **자본 대비 수익은 펀딩의 절반**.
                  선물 2~3배로 줄이면 효율은 오르지만 가격이 +50%/+33% 오르면 숏이 청산된다 → 증거금 보충 규칙 필요(4절).
   B. 코인 증거금 : BTC 를 증거금으로 넣고 BTC-USD-SWAP(인버스) 을 같은 달러 금액만큼 숏.
                  현물 매수가 따로 없어 자본 효율 1배(펀딩 전부가 자본 대비 수익), 1배 숏이면 사실상 청산 없음.
                  대신 1계약 = 100 USD 라 최소 수백 달러가 있어야 하고, 인버스 펀딩은 USDT 무기한과 조금 다르다.
   **급락 매수 봇과 충돌**: 봇은 BTC-USDT-SWAP 에 net 모드(한 방향)로 롱을 잡는다. 같은 계정·같은 상품에 캐리 숏을 넣으면
   두 포지션이 상쇄돼 버린다. → 권장: **OKX 서브계정에 캐리를 분리**(봇 코드·키 그대로). B 경로는 상품이 달라 충돌은 없지만
   봇의 잔고·일손실 차단기(equity 기반)가 캐리 자산까지 세게 되므로 역시 서브계정이 깔끔하다.

3. 백테스트 (사전등록 — 아래 값 말고는 보지 않는다)
   데이터: 바이낸스 BTCUSDT 무기한 펀딩 2019-09~2026-09 (data_cache/binance_funding.pkl, 8h 간격)
           BitMEX XBTUSD 펀딩 2018-01~2022-02 (bitmex_funding.pkl — 다른 거래소 재현용)
           OKX BTC-USDT-SWAP 펀딩 2026-06~09 (okx_funding.pkl, 공개 API 가 3개월만 준다 — 수준 비교용)
           가격: 바이낸스 현물 5분봉 · OKX 무기한 5분봉 2021-05~ (괴리 측정용 — 거래소가 달라 근사)
   규칙 (보유 여부만 정한다. 비보유 시 현금 이자 0 으로 둔다 — 보수적)
     C0 항상 보유 (처음 한 번 진입)
     C1 직전 21회(7일) 펀딩 평균이 연율 +8% 이상이면 진입, 0% 미만이면 청산 (사이 구간은 현 상태 유지)
     C2 직전 9회(3일) 평균, 같은 문턱
     신호는 펀딩 확정 뒤 다음 회차부터 적용(미래 정보 금지).
   비용: 진입·청산 각각 현물 테이커 0.10% + 선물 테이커 0.05% + 미끄러짐 다리당 0.02% → 왕복 0.38%
         (지정가 변형 왕복 0.20% 도 보고). OKX 기본 등급 기준 — 구현 시 계정 수수료 조회로 확인.
   자본 환산: A 경로 선물 1·2·3배 (자본 = 명목 × (1 + 1/배율)) · B 경로 1배 (자본 = 명목).
              A 2·3배는 보유 중 가격 상승으로 청산선에 닿는 횟수를 일봉 고가로 세고, 닿기 전 보충이 필요했던 횟수를 보고.
   고르기: 탐색 구간 2019-09~2022-12 에서 C0·C1·C2 중 자본 대비 연 순수익이 가장 높은 것 하나.
   판정 (확인 구간 2023-01~2026-09, 고른 규칙 · A 경로 선물 1배 · 테이커 비용 — 가장 보수적인 조합으로):
     ① 자본 대비 연 순수익 ≥ 4%   (USDT 예치 대체 수준으로 정한 판단값 — 이보다 낮으면 위험을 질 이유가 없다)
     ② 확인 구간 연도별(23·24·25·26) 순수익 음수인 해 없음
     ③ 고른 규칙이 C0 보다 못하지 않음 — 못하면 C0 로 대신 판정 (단순한 쪽이 기본)
     ④ OKX 3개월 펀딩 평균이 같은 기간 바이낸스의 0.5~2배 안 (바이낸스 수치를 OKX 에 옮겨 써도 되는가)
     넷 다 통과 → '소액 실계좌 후보'. 실계좌·금액·경로는 사용자 결정.
   보고만: 국면별(조용/횡보/추세) 펀딩 · 최장 음수 펀딩 연속 · 최악 30일 캐리 · 연도별 · BitMEX 재현 · 괴리 분포(청산 시 손실 폭)
           · B 경로와 A 2·3배의 자본 대비 수익 · 지정가 비용 변형.
   사전 예측 (틀려도 기록): ② 가 위험하다 — 2026 은 9월까지 +2.1%(명목)라 A 1배 자본 대비 약 +1% 로 ① 도 못 넘을 수 있다.
     C1 은 저펀딩 기간을 피해 비용 대비 낫겠지만 진입·청산 비용 0.38% 가 잦은 전환을 잡아먹을 것.

4. 실행 설계 (판정 통과 후에만 구현. 모의 → 소액 실계좌 순서)
   새 파일 carry_bot.py (autotrade.py 와 분리, 서브계정 키 .env.carry). 1시간마다:
     · 펀딩 이력 조회 → 규칙(C0/C1/C2) 판단 → 진입/청산/유지
     · 진입 순서: 선물 숏 먼저(지정가 → 1분 미체결 시 시장가), 체결 수량만큼 현물 매수. 두 번째 다리 실패 시 첫 다리 되돌림.
       clOrdId 로 주문 확정 후 장부 기록 (autotrade 의 의도→제출→조회→장부 패턴 재사용).
     · 헤지 비율 점검: |현물 BTC − 선물 BTC| / 현물 > 2% 면 차이만 맞춘다.
     · 증거금 점검 (A 경로 2배 이상일 때): 선물 증거금률이 기준 아래면 현물 일부를 팔아 USDT 로 옮긴다 — 헤지 수량도 같이 줄인다.
     · 차단: 펀딩이 −0.05%/8h 이하로 한 번이라도 찍히면 청산 · 두 다리 수량 불일치 해소 실패 시 신규 진입 금지 + 알림.
     · 대시보드에 누적 펀딩 수취·비용·괴리 손익을 따로 표시.
   테스트: 가짜 ccxt 로 '두 번째 다리 실패' · '부분 체결' · '펀딩 음수 전환' · '증거금 부족' 시나리오 (test_audit.py 방식).

5. 위험 (백테스트가 못 잡는 것)
   · 거래소 위험: 두 다리가 모두 OKX 에 있다. 거래소가 멈추거나 파산하면 헤지가 의미 없다 (2022 FTX).
   · 펀딩 음수 구간: 하락장 과열 청산 때 −0.3%/8h 까지 찍혔다(바이낸스 추세 국면 최저). 짧아도 여러 번이면 몇 주치 수익이 사라진다.
   · 괴리: 청산 순간 현물·선물 가격 차이가 벌어져 있으면 그만큼 손실. 급변동일수록 크다.
   · 자동 디레버리징(ADL): 극단 상황에서 거래소가 수익 중인 숏을 강제로 줄일 수 있다 → 헤지가 한쪽만 남는다.

6. 사용자가 정할 것
   · 캐리에 넣을 금액 (B 경로는 수백 달러 이상), 서브계정 생성 여부
   · A(USDT, 소액 가능·자본 효율 절반) vs B(코인 증거금, 효율 1배·최소 금액 큼)
   · 판정 ① 의 문턱 4% 가 적당한지 — 결과 보기 전에만 바꿀 수 있다.
사용: python research/carry.py   (결과 → research/carry_result.txt)   ※ 구현 전 — 이 설계서를 커밋한 뒤 작성한다.
"""
import pathlib
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "aoa"))
from clone import okx_5m  # noqa: E402

DC = ROOT / "data_cache"
TAKER_SIDE, MAKER_SIDE = 0.0019, 0.0010      # 한쪽(진입 또는 청산): 현물 0.10 + 선물 0.05 + 미끄러짐 0.02×2 / 지정가 변형
WIN = {"C0": None, "C1": 21, "C2": 9}
ON, OFF = 0.08, 0.0                          # 연율 문턱


def run(f, rule, lo, hi, side_cost):
    """→ 회차별 명목 대비 손익 Series · 전환 횟수 · 보유 비율. 판단은 직전 회차까지 확정된 펀딩만 쓴다."""
    w = WIN[rule]
    known = (f.rolling(w).mean() * 3 * 365).shift(1) if w else None
    ev = f[(f.index >= lo) & (f.index < hi)]
    hold, pnl, sw, held = False, [], 0, 0
    for t, x in ev.items():
        want = True if w is None else (True if known[t] >= ON else False if known[t] < OFF else hold)
        c = 0.0
        if want != hold:
            c -= side_cost; sw += 1; hold = want
        pnl.append(c + (x if hold else 0.0)); held += hold
    if hold and pnl:
        pnl[-1] -= side_cost
    return pd.Series(pnl, index=ev.index, dtype=float), sw, held / max(len(ev), 1)


def yrs(lo, hi, f):
    return (min(hi, f.index[-1]) - max(lo, f.index[0])).days / 365.25


def lev_events(px, hold_idx, L):
    """A 경로 선물 L배: 보유 중 일봉 고가가 기준가 대비 (+0.5/L) 에 닿으면 보충 1회(기준가 갱신),
    (+1/L − 0.4%) 에 닿으면 '보충 안 했으면 청산' 1회(기준가 갱신)."""
    top = liq = 0; ref = None
    for d, (h, c) in px.iterrows():
        if d not in hold_idx:
            ref = None; continue
        ref = c if ref is None else ref
        if h >= ref * (1 + 1 / L - 0.004):
            liq += 1
        if h >= ref * (1 + 0.5 / L):
            top += 1; ref = h
    return top, liq


if __name__ == "__main__":
    T = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731
    lines = []
    P = lambda *a: (print(*a, flush=True), lines.append(" ".join(map(str, a))))  # noqa: E731
    P(__doc__.split("\n")[0])
    fb = pd.read_pickle(DC / "binance_funding.pkl").sort_index()
    fm = pd.read_pickle(DC / "bitmex_funding.pkl").sort_index()
    fo = pd.read_pickle(DC / "okx_funding.pkl").sort_index()
    IS, OOS = (T("2019-09-10"), T("2023-01-01")), (T("2023-01-01"), T("2026-10-01"))
    A1 = lambda x: x / 2  # noqa: E731     A 경로 선물 1배: 자본 = 명목 × 2

    P("\n[1] 탐색 구간 2019-09~2022-12 (바이낸스) — 자본 대비 연 순수익, A 1배 · 테이커")
    best, isr = None, {}
    for r in WIN:
        s, sw, hf = run(fb, r, *IS, TAKER_SIDE)
        isr[r] = A1(s.sum()) / yrs(*IS, fb)
        P(f"  {r}: 연 {isr[r] * 100:+.2f}% (명목 {s.sum() / yrs(*IS, fb) * 100:+.2f}%) · 전환 {sw}회 · 보유 {hf:.0%}")
    best = max(isr, key=isr.get)
    P(f"  → 선택: {best}")

    P("\n[2] 확인 구간 2023-01~2026-09 (바이낸스)")
    oos = {}
    for r in WIN:
        s, sw, hf = run(fb, r, *OOS, TAKER_SIDE)
        sm, _, _ = run(fb, r, *OOS, MAKER_SIDE)
        y = yrs(*OOS, fb)
        oos[r] = (A1(s.sum()) / y, s)
        yr = s.groupby(s.index.year).sum()
        P(f"  {r}: A1배 연 {A1(s.sum()) / y * 100:+.2f}% · A2배 {s.sum() / 1.5 / y * 100:+.2f}% · A3배 {s.sum() / (4 / 3) / y * 100:+.2f}%"
          f" · B {s.sum() / y * 100:+.2f}% · 지정가 A1배 {A1(sm.sum()) / y * 100:+.2f}% · 전환 {sw}회 · 보유 {hf:.0%}"
          f" | 연도별(A1배) " + " ".join(f"{k % 100:02d}:{A1(v) * 100:+.2f}%" for k, v in yr.items()))

    P("\n[3] 판정 (A 경로 · 선물 1배 · 테이커)")
    j = best if oos[best][0] >= oos["C0"][0] else "C0"
    c3 = j == best
    ann, s = oos[j]
    yr = s.groupby(s.index.year).sum().map(A1)
    c1, c2 = ann >= 0.04, bool((yr >= 0).all())
    lo6, hi6 = fo.index[0], fo.index[-1] + pd.Timedelta("1s")
    ob, bb = fo.mean(), fb[(fb.index >= lo6) & (fb.index < hi6)].mean()
    c4 = 0.5 <= ob / bb <= 2
    ok = lambda b: "통과" if b else "불합격"  # noqa: E731
    P(f"  판정 규칙 {j}{'' if c3 else f' (선택 {best} 가 C0 보다 못해 C0 로 대신)'}")
    P(f"  ① 자본 대비 연 {ann * 100:+.2f}% ≥ 4% {ok(c1)} · ② 연도별 " + " ".join(f"{k % 100:02d}:{v * 100:+.2f}%" for k, v in yr.items())
      + f" {ok(c2)} · ③ C0 이상 {ok(c3)}(대체 규칙 적용) · ④ OKX/바이낸스 {ob * 100:.4f}/{bb * 100:.4f}%/8h = {ob / bb:.2f}배 {ok(c4)}")
    P("  →", "소액 실계좌 후보 (실계좌·금액은 사용자 결정)" if (c1 and c2 and c4) else "기각")

    P("\n[4] 보고 (판정과 무관)")
    spot = pd.concat([pd.read_pickle(DC / "binance_spot_5m.pkl").loc[:"2019-12-31 23:55"],
                      pd.read_pickle(DC / "binance_btcusdt_5m_2020.pkl")])
    R = ((spot.high.rolling(288).max() - spot.low.rolling(288).min()) / spot.low.rolling(288).min()).shift(1)
    reg = pd.cut(R.reindex(fb.index, method="ffill"), [0, 0.01, 0.03, np.inf], labels=["조용 <1%", "횡보 1~3%", "추세 ≥3%"], right=False)
    P("  국면별 펀딩(바이낸스, 명목 연율): " + " · ".join(f"{k} {v.mean() * 1095 * 100:+.1f}%(양수 {(v > 0).mean():.0%})" for k, v in fb.groupby(reg, observed=True)))
    neg = (fb < 0).astype(int); runs = neg.groupby((neg != neg.shift()).cumsum()).sum()
    P(f"  최장 음수 펀딩 연속 {runs.max()}회({runs.max() / 3:.1f}일) · 최악 30일 합 {fb.rolling(90).sum().min() * 100:+.2f}%(명목)"
      f" · 최저 한 회 {fb.min() * 100:+.3f}%")
    P("  연도별 명목 펀딩 합(바이낸스): " + " ".join(f"{k % 100:02d}:{v * 100:+.1f}%" for k, v in fb.groupby(fb.index.year).sum().items()))
    for r in WIN:
        s, sw, hf = run(fm, r, fm.index[0], fm.index[-1] + pd.Timedelta("1s"), TAKER_SIDE)
        P(f"  BitMEX 18-22 {r}: A1배 연 {A1(s.sum()) / yrs(fm.index[0], fm.index[-1], fm) * 100:+.2f}% · 전환 {sw}회 · 보유 {hf:.0%}")
    day = spot.resample("1D").agg({"high": "max", "close": "last"}).dropna()
    s0, _, _ = run(fb, "C0", *OOS, TAKER_SIDE)
    hold_days = set(day.index[(day.index >= OOS[0]) & (day.index < OOS[1])])
    for L in (2, 3):
        top, liq = lev_events(day, hold_days, L)
        P(f"  A {L}배 (C0, 23-26): 증거금 보충 {top}회 · 보충 안 했으면 청산 {liq}회")
    okx = okx_5m().close
    sp = spot.close.reindex(okx.index)
    b = (okx / sp - 1).dropna()
    b = b[b.index >= T("2021-05-01")]
    P(f"  괴리(OKX 무기한 / 바이낸스 현물 − 1, 21-26, 거래소 달라 근사): 중앙 {b.median() * 100:+.3f}% · 1% {b.quantile(0.01) * 100:+.3f}%"
      f" · 99% {b.quantile(0.99) * 100:+.3f}% · 최저 {b.min() * 100:+.2f}% · 최고 {b.max() * 100:+.2f}%")
    (ROOT / "research" / "carry_result.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
