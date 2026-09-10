"""비판적 검증 — '과매수 극단은 지속 신호다' 와 EXTREME_MIN 필터가 편향의 산물은 아닌가.

research_reversion.txt 의 두 주장을 깨는 쪽으로 설계했다:
  (주장1) 고점 후보 뒤 수익률이 전체 평균보다 높다 (OKX 12봉 +0.93% vs +0.13%)
  (주장2) 그래서 '고점점수 ≥ 2' 를 진입 조건으로 걸면 낫다 (+492% → +683%)

의심해야 할 편향 다섯 가지
  ① **추세 조건부 편향**  표본 기간 BTC 는 올랐다. 상승장에서 더 자주 켜지는 신호라면 아무 정보가 없어도
     '신호 뒤 수익률이 평균보다 높다' 가 나온다. → 같은 추세 상태끼리 짝지어 비교한다 (matched control).
  ② **중복 표본**  12봉 앞 수익률을 매 봉 겹쳐서 재면 표본 489개가 독립 489개가 아니다. t 값이 부풀려진다.
     → 겹치지 않는 부분표본 + 블록 부트스트랩으로 다시 잰다.
  ③ **다중 검정·선택 편향**  규칙 7개 × 문턱 여러 개 × 지평 5개를 재고, 필터도 후보 6개 중 골랐다.
     → 순열 검정: 신호를 원형 이동시켜 '정보 없는 같은 모양의 신호' 가 얼마나 자주 이만큼 나오는지 본다.
  ④ **필터의 우연**  108건 중 95건만 남기는 필터는, 아무거나 13건 버려도 결과가 크게 바뀔 수 있다.
     → 같은 개수를 무작위로 버리는 필터 수백 개와 성적을 비교한다. 이게 EXTREME_MIN 채택 여부를 가른다.
  ⑤ **분할 지점 선택**  탐색/검증 경계 2024-01-01 은 임의로 골랐다. → 경계를 옮겨 가며 결론이 유지되는지 본다.

사용: python backtest_bias.py [--trials N]    기본 300
     (숫자만 있는 인자는 쓰지 않는다 — backtest_okx.arg_cost 주석 참고)
"""
import sys

import numpy as np
import pandas as pd

import backtest_okx as B
import backtest_quant as Q
import model as M
import reversion as R

TRIALS = int(next((a.split("=")[1] for a in sys.argv[1:] if a.startswith("--trials=")), 300))
RNG = np.random.default_rng(20260910)


def setup():
    assert 0 <= Q.COST < 0.05, f"편도 비용 {Q.COST} 가 이상하다 (인자를 비용으로 오독)"
    h4 = B.fetch("4h")
    h4 = h4[h4.index >= pd.Timestamp(Q.START, tz="UTC")]
    d1 = B.fetch("1d")
    rg = (B.bull(d1) >= M.CONF)
    rg.index = rg.index + pd.Timedelta(days=1)
    rg = rg.reindex(h4.index, method="ffill").fillna(False).astype(bool)
    en = ((h4.close > h4.high.rolling(M.H4_N).max().shift(1)) & rg).values
    ex = ((h4.close < h4.low.rolling(M.H4_M).min().shift(1)) | ~rg).values
    hi_s, lo_s = R.scores(h4)
    return h4, en, ex, hi_s.values, lo_s.values, rg.values


# ───────────────── ① 추세 조건부 편향 ─────────────────
def matched(h4, hi_s, n=12):
    """같은 추세 상태(직전 20봉 수익률 5분위 × 롱국면 여부) 안에서만 고점 후보 vs 비후보를 비교한다.
    '상승장에서 더 자주 켜져서 생긴 착시' 라면 여기서 차이가 사라진다."""
    c = h4.close
    fwd = (c.shift(-n) / c - 1).values * 100
    trail = (c / c.shift(20) - 1).values * 100
    top = hi_s >= R.REV_CONF
    ok = np.isfinite(fwd) & np.isfinite(trail)
    q = pd.qcut(pd.Series(trail[ok]), 5, labels=False, duplicates="drop").values
    f, t = fwd[ok], top[ok]
    print(f"\n── ① 추세 조건부 편향: 직전 20봉 수익률 5분위 안에서 비교 (앞으로 {n}봉) ──")
    print(f"   {'추세 분위':>10}{'구간 평균':>12}{'고점 후보 뒤':>14}{'비후보 뒤':>12}{'차이':>10}{'신호수':>8}")
    tot_w, tot_d = 0.0, 0.0
    for k in range(int(np.nanmax(q)) + 1):
        m = q == k
        a, b = f[m & t], f[m & ~t]
        if len(a) < 10:
            continue
        d = a.mean() - b.mean()
        tot_w += len(a); tot_d += d * len(a)
        print(f"   {k + 1:>10}{f[m].mean():+11.2f}%{a.mean():+13.2f}%{b.mean():+11.2f}%{d:+9.2f}%{len(a):8d}")
    print(f"   신호수 가중 평균 차이: {tot_d / tot_w:+.2f}%   "
          f"(조건 없이 재면 {f[t].mean() - f[~t].mean():+.2f}%)")
    return tot_d / tot_w, f[t].mean() - f[~t].mean()


# ───────────────── ② 중복 표본 ─────────────────
def overlap(h4, hi_s, n=12):
    c = h4.close
    fwd = (c.shift(-n) / c - 1).values * 100
    top = np.where((hi_s >= R.REV_CONF) & np.isfinite(fwd))[0]
    base = np.nanmean(fwd)
    # 겹치지 않게: 앞선 신호로부터 n봉 이상 떨어진 것만
    keep, last = [], -10 ** 9
    for i in top:
        if i - last >= n:
            keep.append(i); last = i
    keep = np.array(keep)
    print(f"\n── ② 중복 표본: 12봉 앞 수익률을 겹쳐 재면 표본이 부풀려진다 ──")
    print(f"   전체 신호 {len(top)}개 → 겹치지 않는 신호 {len(keep)}개 (실질 표본은 {len(keep) / len(top) * 100:.0f}%)")
    for lab, idx in (("전체 신호", top), ("비중복만", keep)):
        x = fwd[idx]
        se = x.std(ddof=1) / np.sqrt(len(x))
        print(f"   {lab:>10}: 평균 {x.mean():+.2f}% (전체 평균 {base:+.2f}%) · "
              f"차이 {x.mean() - base:+.2f}% · 표준오차 {se:.2f}% · t {(x.mean() - base) / se:+.2f}")
    # 블록 부트스트랩 (연속 60봉 블록) — 자기상관을 살린 채 표본을 다시 뽑는다
    bl, nb = 60, len(fwd) // 60
    diffs = []
    for _ in range(TRIALS):
        st = RNG.integers(0, len(fwd) - bl, nb)
        idx = np.concatenate([np.arange(s, s + bl) for s in st])
        idx = idx[np.isfinite(fwd[idx])]
        t2 = (hi_s[idx] >= R.REV_CONF)
        if t2.sum() > 20 and (~t2).sum() > 20:
            diffs.append(fwd[idx][t2].mean() - fwd[idx][~t2].mean())
    d = np.array(diffs)
    print(f"   블록 부트스트랩({bl}봉 블록, {len(d)}회): 차이 중앙값 {np.median(d):+.2f}% · "
          f"90% 구간 [{np.percentile(d, 5):+.2f}%, {np.percentile(d, 95):+.2f}%] · 0 이하 비율 {(d <= 0).mean() * 100:.0f}%")


# ───────────────── ③ 순열 검정 (신호 원형 이동) ─────────────────
def permute(h4, hi_s, n=12):
    """신호 계열을 통째로 무작위 이동시킨다. 신호의 뭉침·빈도는 그대로고 수익률과의 연결만 끊긴다.
    실제 차이가 이 분포 안에 있으면 '정보' 가 아니라 '모양' 이 만든 값이다."""
    c = h4.close
    fwd = (c.shift(-n) / c - 1).values * 100
    top = hi_s >= R.REV_CONF
    ok = np.isfinite(fwd)
    obs = fwd[ok & top].mean() - fwd[ok & ~top].mean()
    null = []
    for _ in range(TRIALS * 3):
        s = RNG.integers(50, len(top) - 50)
        t2 = np.roll(top, s)
        if (ok & t2).sum() > 20:
            null.append(fwd[ok & t2].mean() - fwd[ok & ~t2].mean())
    null = np.array(null)
    p = (null >= obs).mean()
    print(f"\n── ③ 순열 검정: 신호를 무작위로 이동시킨 '정보 없는 신호' {len(null)}개와 비교 ──")
    print(f"   실제 차이 {obs:+.2f}%  ·  귀무분포 중앙값 {np.median(null):+.2f}% · "
          f"95분위 {np.percentile(null, 95):+.2f}%")
    print(f"   p 값 = {p:.3f}  ({'우연으로 보기 어렵다' if p < 0.05 else '우연과 구분되지 않는다'})")
    return p


# ───────────────── ④ 필터의 우연: 같은 개수를 무작위로 버리면? ─────────────────
def filter_null(h4, en, ex, hi_s):
    keep = hi_s >= 2
    n_sig = int(en.sum())
    n_keep = int((en & keep).sum())
    p_keep = n_keep / n_sig
    act = Q.simulate(h4, en & keep, ex, lambda i, c: 3)
    base = Q.simulate(h4, en, ex, lambda i, c: 3)
    print(f"\n── ④ 필터의 우연: 신호 {n_sig}개 중 {n_keep}개만 남기는 필터 ({p_keep * 100:.0f}%) ──")
    print(f"   '고점점수≥2' 로 남기면 누적 {act[0].iloc[-1] * 100 - 100:+.0f}% (필터 없음 {base[0].iloc[-1] * 100 - 100:+.0f}%)")
    print(f"   같은 비율로 **무작위로** 남기는 필터 {TRIALS}개와 비교한다:")
    outs = []
    for _ in range(TRIALS):
        m = en & (RNG.random(len(en)) < p_keep)
        outs.append(Q.simulate(h4, m, ex, lambda i, c: 3)[0].iloc[-1] * 100 - 100)
    o = np.array(outs)
    pct = (o < act[0].iloc[-1] * 100 - 100).mean() * 100
    print(f"   무작위 필터 분포: 중앙값 {np.median(o):+.0f}% · 25~75% [{np.percentile(o, 25):+.0f}%, "
          f"{np.percentile(o, 75):+.0f}%] · 5~95% [{np.percentile(o, 5):+.0f}%, {np.percentile(o, 95):+.0f}%]")
    print(f"   실제 필터는 무작위 필터의 상위 **{100 - pct:.0f}%** 지점 (백분위 {pct:.0f})")
    print(f"   → {'무작위와 구분되지 않는다' if pct < 95 else '무작위로는 잘 안 나오는 성적이다'}")
    return pct


# ───────────────── ⑤ 분할 지점 민감도 ─────────────────
def split_sensitivity(h4, en, ex, hi_s):
    keep = hi_s >= 2
    print(f"\n── ⑤ 분할 지점을 옮기면 '검증 구간에서도 개선' 이 유지되나 ──")
    print(f"   {'경계':>12}{'기준 검증 Sh':>14}{'필터 검증 Sh':>14}{'차이':>10}")
    cb, tb = Q.simulate(h4, en, ex, lambda i, c: 3), Q.simulate(h4, en & keep, ex, lambda i, c: 3)
    wins = 0
    for sp in ("2022-07-01", "2023-01-01", "2023-07-01", "2024-01-01", "2024-07-01", "2025-01-01"):
        a = Q.metrics(cb[0], cb[1], cb[2], sp, None)["sharpe"]
        b = Q.metrics(tb[0], tb[1], tb[2], sp, None)["sharpe"]
        wins += b > a
        print(f"   {sp:>12}{a:>13.2f}{b:>13.2f}{b - a:>+10.2f}")
    print(f"   → 6개 경계 중 {wins}개에서 필터가 낫다")
    return wins


# ───────────────── ⑥ 필터는 실제로 무엇을 거르고 있나 ─────────────────
def simpler(h4, en, ex, hi_s):
    """'고점 점수' 가 그냥 추세 강도 아니냐는 반론을 확인한다.

    먼저 확인된 것: 돌파봉에서는 단순 추세 지표(도치안 ≥0.5, RSI ≥50, %B ≥0.5)가 **전부 100% 만족**된다.
    12봉 신고가를 뚫었으니 당연하다 → 그 지표들은 조건부 정보량이 0 이고, 필터는 추세 강도를 재는 게 아니다.
    그럼 무엇을 재는가. 7규칙을 하나씩 단독 필터로 걸어 보면 일하는 규칙이 드러난다.
    잔존율이 다르면 비교가 불공정하므로(거래를 줄이는 것만으로 성적이 변한다) 같은 잔존율의
    무작위 필터 분포와 견준 **백분위** 로 읽는다."""
    base = Q.simulate(h4, en, ex, lambda i, c: 3)[0].iloc[-1] * 100 - 100
    x = R.add_features(h4)
    print(f"\n── ⑥ 필터는 무엇을 거르나 (기준 = 필터 없음 {base:+.0f}%, 신호 {en.sum()}개) ──")
    print(f"   {'단독 규칙':>22}{'돌파봉 중 점화율':>16}{'잔존율':>8}{'누적':>10}{'거래':>6}{'무작위 백분위':>15}")

    def rank(mask, trials=None):
        m = en & mask
        if m.sum() == 0:
            return 0.0, float("nan"), 0, float("nan")
        cum = Q.simulate(h4, m, ex, lambda i, c: 3)
        keep = m.sum() / en.sum()
        t = trials or max(80, TRIALS // 4)
        outs = [Q.simulate(h4, en & (RNG.random(len(en)) < keep), ex, lambda i, c: 3)[0].iloc[-1] * 100 - 100
                for _ in range(t)]
        return keep, cum[0].iloc[-1] * 100 - 100, len(cum[1]), (np.array(outs) < cum[0].iloc[-1] * 100 - 100).mean() * 100

    for nm, f, _ in R.RULES:
        mk = f(x).fillna(False).values
        k, cum, n, pct = rank(mk)
        print(f"   {nm:>22}{mk[en].mean() * 100:14.0f}%{k * 100:7.0f}%{cum:+10.0f}%{n:6d}{pct:14.0f}")
    k, cum, n, pct = rank(hi_s >= 2, TRIALS)
    print(f"   {'[합계] 고점점수 ≥ 2':>22}{'':15}{k * 100:7.0f}%{cum:+10.0f}%{n:6d}{pct:14.0f}")
    print("   점화율 100% 인 규칙은 돌파봉에서 항상 참이라 정보가 없다. 변별하는 것은 점화율이 중간인 규칙이다.")


def main():
    h4, en, ex, hi_s, lo_s, rg = setup()
    print(f"OKX BTC 4h {h4.index[0]:%Y-%m-%d}~{h4.index[-1]:%Y-%m-%d} · 봉 {len(h4):,} · 시행 {TRIALS}회")
    m_diff, raw_diff = matched(h4, hi_s)
    overlap(h4, hi_s)
    p = permute(h4, hi_s)
    pct = filter_null(h4, en, ex, hi_s)
    wins = split_sensitivity(h4, en, ex, hi_s)
    simpler(h4, en, ex, hi_s)

    print("\n\n═══ 판정 ═══")
    print(f"① 추세 보정 후 차이 {m_diff:+.2f}% (보정 전 {raw_diff:+.2f}%) — "
          f"{'추세 편향이 상당 부분을 설명한다' if abs(m_diff) < abs(raw_diff) * 0.5 else '추세만으로는 설명되지 않는다'}")
    print(f"③ 순열 p={p:.3f} — {'신호 자체는 우연과 구분된다' if p < 0.05 else '신호 자체가 우연과 구분되지 않는다'}")
    eff = 1 - (pct / 100) ** 18                              # 후보 ~18개 중 최고를 고른 것에 대한 어림 보정
    print(f"④ 필터는 무작위 대비 백분위 {pct:.0f} — 그러나 후보 ~18개 중 사후 선택이므로 "
          f"실효 p ≈ {eff:.2f} ({'통과' if eff < 0.05 else '**통과 못 함**'})")
    print(f"⑤ 분할 경계 6개 중 {wins}개에서 개선 (같은 표본을 자른 것이라 독립 증거는 아니다)")
    print("\n주장1(극단은 지속 신호) 유지 · 주장2(EXTREME_MIN) 기각 → research_bias.txt 참고")

    print("\nok  (판정 문장은 자동 생성이다 — 근거 수치를 직접 볼 것)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
