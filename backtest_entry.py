"""진입 타이밍 연구: 4h 돌파 신호가 난 뒤 4시간 동안, 분봉에서 '하한 방어'를 기다렸다 사면 더 나은가.

봇은 지금 신호가 나면 그 자리에서 시장가로 산다. 여기서 묻는 것은 하나다 —
  신호 봉이 닫힌 뒤 다음 4h 봉이 닫히기 전까지(=봇이 신호를 다시 볼 때까지) 분봉을 보며 기다리면
  더 싸게 들어갈 수 있는가, 그리고 '못 사고 놓친 거래' 비용을 빼고도 남는가.
저 두 번째 질문이 핵심이다. 승률 36% · 큰 추세 몇 번이 수익을 만드는 전략에서 진입을 놓치는 건 비싸다.

정책은 모두 같은 창 W = [신호봉 종료, +4h) 안에서만 체결하고, 못 채우면 fallback 을 따른다:
  end  = 창 끝(다음 4h 시가)에 시장가        skip = 그 거래를 포기
비용·청산·펀딩은 backtest_okx.py 와 같은 가정.

**2026-09-21: 체결 엔진을 engine.run 으로 갈아끼웠다.** 그전까지 이 파일은 자체 루프를 갖고 있었고,
audit/AUDIT.md §7 이 "A1~A4 와 같은 결함이 남아 있다 → research_entry_timing.txt 의 숫자는 신뢰도가 낮다" 고
적어 둔 바로 그 루프다. 이 파일의 결론(= 하한 재확보 진입)이 지금 봇 누적의 3.6배(+553% → +2,018%)를
설명하므로, 결함 있는 시뮬레이터 위에서 고른 규칙인지 확인해야 했다. 고친 결함 넷:
  (1) 진입한 4h 봉의 **체결 전** 저가로 강제청산을 판정했다 — 기다렸다 사는 정책일수록 겪지도 않은
      손실을 뒤집어썼다. 즉 옛 루프는 reclaim 계열에 **불리한** 쪽으로 틀려 있었다.
  (2) 청산선을 진입가×(1−1/lev) 로 잡았다. 실제 거래소는 레버리지를 정수로 올려 걸고(ceil) 유지증거금
      (MMR)이 남았을 때 청산한다 → 청산선이 더 가깝다. 즉 옛 루프는 청산을 **과소 계상**했다.
  (3) 청산이 나면 `eq=0` + `break` 로 **그 뒤 모든 거래를 버렸다.** 표본이 정책마다 달라져 비교 자체가 깨진다.
  (4) 격리 마진인데 계좌 전액을 잃는 것으로 셌다 (pos_pct 무시).
  덤으로 MDD 정의도 바뀐다 — 옛 루프는 거래 종료 시점만 이어 붙였고, engine 은 **봉마다 평가손익**까지 본다.
  그래서 아래 MDD 는 옛 표보다 나쁘게 나온다. 그게 정상이고, 그게 수정의 내용이다.

  예상(결과 보기 전에 적는다): (1)은 reclaim 에 유리하게, (2)(3)은 불리하게 작용한다 — 방향이 엇갈리므로
  우위가 줄어들 수도 커질 수도 있다. **뒤집히면 봇 규칙(ENTRY_FLOOR)을 재검토한다.**
사용: python backtest_entry.py [편도비용]        정책 비교
      python backtest_entry.py sweep            reclaim 파라미터·CONF·기간 민감도 (과최적화 점검)
첫 실행은 이벤트 구간 분봉을 받는다 (약 1분).
"""
import sys

import numpy as np
import pandas as pd

import backtest_okx as B
import minute_data as MD
import model as M

MM_P = MD.CACHE / "minute_p.pkl"

COST = B.arg_cost(__file__)
START = "2021-03-01"
FUND = 0.0001 * 3 / 6                     # 4h 봉당 펀딩
PRE_H, WIN_H = 2, 4                       # 지표용 사전 구간 / 진입 창 (다음 4h 봉까지)


def events(conf=None):
    """봇의 상태기계를 4h 봉 위에서 재현 → [(신호봉 t, 진입봉 i+1, 청산봉, hi, lo)]. hi = 돌파당한 고가."""
    conf = M.CONF if conf is None else conf
    d1, h4 = B.fetch("1d"), B.fetch("4h")
    rg = M.align_daily(B.bull(d1) >= conf, h4.index).fillna(False).astype(bool)     # 판단 시각 = 4h 봉 종료
    hi = h4.high.rolling(M.H4_N).max().shift(1)
    lo = h4.low.rolling(M.H4_M).min().shift(1)
    ent = (h4.close > hi) & rg
    exi = (h4.close < lo) | ~rg
    idx = h4.index
    out, held, i = [], None, 0
    while i < len(idx) - 1:
        if held is None:
            if idx[i] >= pd.Timestamp(START, tz="UTC") and ent.iloc[i]:
                held = i
            i += 1
            continue
        if exi.iloc[i]:
            out.append((idx[held], held + 1, i + 1, float(hi.iloc[held]), float(lo.iloc[held])))
            held = None
        i += 1
    return out, h4                                                  # 아직 보유 중인 마지막 거래는 버린다


def minutes(evs):
    """이벤트마다 [진입시각−PRE_H, 진입시각+WIN_H) 분봉을 받아 {신호봉: (사전, 창)}."""
    rng = [(t + pd.Timedelta(hours=4 - PRE_H), t + pd.Timedelta(hours=4 + WIN_H)) for t, *_ in evs]
    df = MD.fetch(rng)
    out = {}
    for t, *_ in evs:
        e = t + pd.Timedelta(hours=4)
        out[t] = (MD.window(df, e - pd.Timedelta(hours=PRE_H), e), MD.window(df, e, e + pd.Timedelta(hours=WIN_H)))
    return out


# ---------- 하한 방어 후보 ----------
def after(w, px, k):
    """체결 후 극단 → (체결가, 체결 후 저가, 체결 후 고가). engine.run 이 진입봉 안 청산을 이걸로 본다.

    체결 **전** 저가는 이 포지션과 무관하다. 옛 자체 루프가 그걸 안 갈라서 기다리는 정책을 부당하게 죽였다.
    모든 정책에 똑같이 적용한다 — 한 정책만 시각을 주면 그 정책에 유리한 비교가 된다."""
    a = w.iloc[k:]
    if not len(a):
        return (px, px, px)
    return (px, min(px, float(a.low.min())), max(px, float(a.high.max())))


def fills(pre, w, hi):
    """창 w 안에서 각 정책의 체결 {이름: (가격, 체결후저가, 체결후고가) 또는 None}."""
    if len(w) < 10:
        return {}
    o = float(w.open.iloc[0])
    f = {"now": after(w, o, 0)}
    for x in (0.3, 0.6, 1.0, 1.5):                                  # 지정가 -X% 를 걸어 두고 기다린다
        lim = o * (1 - x / 100)
        hit = (w.low <= lim)
        f[f"dip{x}"] = after(w, lim, int(hit.values.argmax())) if hit.any() else None
    hit = (w.low <= hi)                                             # 돌파당한 고가로 되돌아오면 그 자리에서
    f["retest"] = after(w, hi, int(hit.values.argmax())) if hit.any() and hi < o else None

    lw = (w[["open", "close"]].min(axis=1) - w.low)                 # 아래꼬리 = 매수 흡수
    body = (w.close - w.open).abs().clip(lower=1e-9)
    volm = w.volume.rolling(30).mean().shift(1).fillna(w.volume.mean())
    absorb = (lw >= 2 * body) & (w.close > w.open) & (w.volume >= 2 * volm)
    f["wick"] = after(w, float(w.close[absorb].iloc[0]), int(absorb.values.argmax())) if absorb.any() else None

    vwap = (w.close * w.volume).cumsum() / w.volume.cumsum().replace(0, np.nan)
    below = w.close < vwap
    cross = below.shift(1).fillna(False) & ~below                   # VWAP 아래로 밀렸다가 되찾는 순간
    f["vwap"] = after(w, float(w.close[cross].iloc[0]), int(cross.values.argmax())) if cross.any() else None

    lo20 = w.low.rolling(20).min()                                  # 같은 바닥을 두 번 지키면 (0.1% 이내 재터치 + 종가 방어)
    f["floor2"] = None
    for k in range(25, len(w)):
        base = float(lo20.iloc[k - 1])
        if base > 0 and w.low.iloc[k] <= base * 1.001 and w.close.iloc[k] > base:
            f["floor2"] = after(w, float(w.close.iloc[k]), k)
            break

    # 하한 = 돌파당한 고가 hi. 이 선이 지켜지는지로 진입 여부를 가른다 (기다리는 게 아니라 거르는 규칙 → fallback=skip 으로 읽을 것)
    f["hold_hi"] = after(w, o, 0) if o > hi else None                # 신호 직후 이미 하한 아래면 들어가지 않는다
    k = min(15, len(w) - 1)
    f["hold_hi15"] = after(w, float(w.close.iloc[k]), k) if float(w.low.iloc[:k + 1].min()) > hi else None
    lost = (w.low < hi).values
    if lost.any():                                                   # 하한을 잃었다가 되찾는 순간
        back = [k for k in range(int(lost.argmax()) + 1, len(w)) if w.close.iloc[k] > hi]
        f["reclaim"] = after(w, float(w.close.iloc[back[0]]), back[0]) if back else None
    else:
        f["reclaim"] = after(w, o, 0)
    return f


def reclaim_event(w, hi, tol=0.0, wait=0):
    """하한(=돌파당한 고가) 재확보 진입 → (체결가, 체결 시각) 또는 None (창 안에 못 되찾음 → 그 거래 포기).
    tol: 하한을 tol% 만큼 밑돌아도 '지킨 것' 으로 본다. wait: 되찾은 뒤 wait 분 동안 다시 잃지 않아야 진입.

    시각까지 돌려주는 이유 (2026-09-10 감사): 진입한 4h 봉의 저가는 체결 **전에** 지나갔을 수 있다.
    그 저가로 강제청산을 판정하면 실제로는 겪지 않은 손실을 세게 된다 (engine.run 3단계 참고)."""
    lvl = hi * (1 - tol / 100)
    lost = (w.low < lvl).values
    if not lost.any():
        return float(w.open.iloc[0]), w.index[0]
    for k in range(int(lost.argmax()) + 1, len(w)):
        if w.close.iloc[k] > lvl and (wait == 0 or float(w.low.iloc[k:k + wait + 1].min()) > lvl):
            return float(w.close.iloc[k]), w.index[k]
    return None


def reclaim_at(w, hi, tol=0.0, wait=0):
    """reclaim_event → (가격, 체결후저가, 체결후고가). sweep·learned_block 이 쓴다."""
    r = reclaim_event(w, hi, tol, wait)
    if r is None:
        return None
    px, ts = r
    return after(w, px, int(w.index.get_indexer([ts])[0]))


def learned_fill(w, p, thr):
    """워크포워드 p 가 thr 이상인 첫 분에 진입 (minute_model.py 가 만든 minute_p.pkl)."""
    q = p.reindex(w.index).dropna()
    ok = q[q >= thr]
    if not len(ok):
        return None
    k = int(w.index.get_indexer([ok.index[0]])[0])
    return after(w, float(w.close.iloc[k]), k)


# ---------- 평가 ----------
def simulate(evs, h4, px_of, lev, fallback):
    """px_of(ev) → (체결가, 체결후저가, 체결후고가) · 체결가만 · None. fallback: 'end' 창 끝 시장가 · 'skip' 포기.

    체결·청산 판정은 **engine.run 한 곳에만** 있다 (2026-09-21). 이 함수는 이벤트 목록을
    engine 이 쓰는 신호 배열 + fills 사전으로 옮기는 껍데기다.
    → (누적%, MDD%, 거래수, 승률%, 최악%, 청산, 평균개선bp, 체결건수)"""
    assert 0 <= COST < 0.05, f"편도 비용 {COST} 가 상식 밖이다 (arg_cost 주석 참고)"
    import engine as E
    o, idx, n = h4.open, h4.index, len(h4)
    en, ex = np.zeros(n, bool), np.zeros(n, bool)
    fl, gain, filled = {}, [], 0
    for ev in evs:
        t, i, j, hi, _ = ev
        if i >= n:
            continue
        base = float(o.iloc[i])
        p = px_of(ev)
        if p is None:
            if fallback == "skip":
                fl[idx[i]] = None                       # 그 자리를 포기한다 (engine 이 진입을 건너뛴다)
                en[i - 1] = True
                ex[min(j, n) - 1] = True
                continue
            nxt = float(o.iloc[i + 1]) if i + 1 < n else base
            p = (nxt, nxt, nxt)                         # 창 끝 = 다음 4h 시가. 진입봉 안에서는 겪는 것이 없다
        else:
            if not isinstance(p, tuple):
                p = (p, float(h4.low.iloc[i]), float(h4.high.iloc[i]))   # 시각을 모르면 봉 전체 (보수적)
            filled += 1
            gain.append((base / p[0] - 1) * 10_000)
        fl[idx[i]] = p
        en[i - 1] = True
        ex[min(j, n) - 1] = True
    z = np.zeros(n, bool)
    curve, t, stats = E.run(h4, en, ex, z, z, lambda i, c: lev, allow=("long",), fills=fl)
    tr = t.ret.values if len(t) else np.zeros(1)
    return (curve.iloc[-1] * 100 - 100, (curve / curve.cummax() - 1).min() * 100, len(t),
            (tr > 0).mean() * 100, tr.min() * 100, stats["liq"], np.mean(gain) if gain else 0.0, filled)


def report(name, r):
    print(f"{name:22s} 누적 {r[0]:+8.0f}%  MDD {r[1]:6.1f}%  거래 {r[2]:3d} 승률 {r[3]:3.0f}% "
          f"최악 {r[4]:6.1f}% 청산{r[5]}  체결 {r[7]:3d}건 평균 {r[6]:+5.0f}bp")


def learned_block(evs, h4, mins):
    """분봉 학습 모델(minute_model.py)의 p 로 진입 시각을 고르면 나은가.
    p 는 워크포워드 검증 구간에만 있으므로 그 구간의 이벤트로만 비교한다 (모두 out-of-sample)."""
    pser = pd.read_pickle(MM_P)
    ev = [e for e in evs if e[0] >= pser.index[0]]
    if not ev:
        return
    F = {t: {"reclaim": reclaim_at(mins[t][1], hi),
             **{f"learned{thr}": learned_fill(mins[t][1], pser, thr) for thr in (0.5, 0.55, 0.6)}}
         for t, _, _, hi, _ in ev}
    print(f"── 분봉 학습 모델 비교 (검증 구간 {pser.index[0]:%Y-%m-%d}~, 이벤트 {len(ev)}건, 전부 out-of-sample) ──")
    for lev in (1, 3):
        report(f"now x{lev}", simulate(ev, h4, lambda e: float(h4.open.iloc[e[1]]), lev, "end"))
        for n in ("reclaim", "learned0.5", "learned0.55", "learned0.6"):
            report(f"{n} x{lev}", simulate(ev, h4, lambda e, n=n: F[e[0]].get(n), lev, "skip"))
        print()


def sweep():
    """같은 규칙을 파라미터·CONF·기간을 바꿔 가며 다시 잰다. 한 조합에서만 좋으면 그건 과최적화다."""
    for conf in (6, 7, 8):
        evs, h4 = events(conf)
        mins = minutes(evs)
        evs = [e for e in evs if len(mins[e[0]][1]) >= 10]
        print(f"\n══ CONF={conf} · 이벤트 {len(evs)}건 ══")
        for tol, wait in ((0.0, 0), (0.1, 0), (0.2, 0), (0.0, 5), (0.0, 15), (0.1, 5)):
            F = {t: reclaim_at(mins[t][1], hi, tol, wait) for t, _, _, hi, _ in evs}
            for half, sl in (("전체", evs), ("21-23", [e for e in evs if e[0] < pd.Timestamp("2024-01-01", tz="UTC")]),
                             ("24-26", [e for e in evs if e[0] >= pd.Timestamp("2024-01-01", tz="UTC")])):
                n = simulate(sl, h4, lambda e: float(h4.open.iloc[e[1]]), 3, "end")
                r = simulate(sl, h4, lambda e: F[e[0]], 3, "skip")
                print(f"  tol {tol:.1f}% wait {wait:2d}분 [{half}]  now {n[0]:+7.0f}% / MDD {n[1]:5.1f}%   "
                      f"reclaim {r[0]:+7.0f}% / MDD {r[1]:5.1f}%  ({r[2]}/{n[2]}건)")


if __name__ == "__main__":
    if "sweep" in sys.argv:
        sweep()
        sys.exit()
    evs, h4 = events()
    print(f"진입 이벤트 {len(evs)}건 (CONF={M.CONF}, {START}~)  비용 편도 {COST * 100:.2f}%", file=sys.stderr)
    mins = minutes(evs)
    evs = [e for e in evs if len(mins[e[0]][1]) >= 10]
    print(f"분봉 확보 {len(evs)}건\n")
    names = (["now"] + [f"dip{x}" for x in (0.3, 0.6, 1.0, 1.5)]
             + ["retest", "wick", "vwap", "floor2", "hold_hi", "hold_hi15", "reclaim"])
    F = {t: fills(*mins[t], hi) for t, _, _, hi, _ in evs}

    if MM_P.exists():
        learned_block(evs, h4, mins)

    # ── 대사: 이 파일이 다른 파일과 같은 숫자를 내는가 (engine.run 으로 갈아끼운 뒤 필수) ──
    import backtest_quant as Q
    h4q, e_q, x_q, _, _ = Q.frames()
    m = h4q.index >= pd.Timestamp(START, tz="UTC")
    h4q, e_q, x_q = h4q[m], e_q[m], x_q[m]
    for lev in (1, 3, 5):
        mine = simulate(evs, h4, lambda e: F[e[0]].get("now"), lev, "end")[0]
        theirs = Q.metrics(*Q.simulate(h4q, e_q, x_q, lambda i, c: lev), START, None)["누적"]
        assert abs(mine - theirs) < 0.5, f"now {lev}배 불일치: 이 파일 {mine:+.0f}% vs backtest_quant {theirs:+.0f}%"
        mine = simulate(evs, h4, lambda e: F[e[0]].get("reclaim"), lev, "skip")[0]
        theirs = Q.metrics(*Q.simulate(h4q, e_q, x_q, lambda i, c: lev, fills=Q.reclaim_fills()),
                           START, None)["누적"]
        assert abs(mine - theirs) < 0.5, f"reclaim {lev}배 불일치: 이 파일 {mine:+.0f}% vs backtest_quant {theirs:+.0f}%"
    print("ok  대사 통과 — now·reclaim 누적이 backtest_quant 경로와 1·3·5배에서 모두 일치\n")

    for lev in (1, 3, 5):
        print(f"── 레버리지 {lev}배 ──")
        for fb in ("end", "skip"):
            for n in names:
                if n == "now" and fb == "skip":
                    continue
                report(f"{n} ({fb})", simulate(evs, h4, lambda e, n=n: F[e[0]].get(n), lev, fb))
            print()
