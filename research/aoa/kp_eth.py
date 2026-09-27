"""가설 검정: 업비트 김프 · ETH 교차자산이 aoa 의 롱/숏 방향이나 '어느 급변동을 고르는지'를 설명하는가.
사전등록 (2026-09-26, 데이터 받기 전 작성).

H1 김프: 한국 개인 과열(김프 급등·업비트 거래량 폭증) → 숏, 김프 급락·역프 → 롱.
   kp = 업비트 원화가 / (바이낸스 USDT가 × 전일 환율) − 1 (%)
   kp_dev(하루 중앙값 대비) · kp_chg_15m · kp_chg_1h · kp_lvl(30일 중앙값 대비) · up_vol_z(업비트 1h 거래량 / 하루 평균)
H2 ETH: ETH 가 먼저 움직이면 BTC 를 따라/반대로 친다.
   eth_ret_15m · eth_ret_1h · ethbtc_15m/1h/1d(ETH 수익률 − BTC 현물 수익률) · eth_pos_1h

판정 (사전 고정):
  A. 방향(에피소드 3,063건): 연도 제외 로지스틱, 기준 M1(단기 가격 8개) 대비 새 특징 추가 시
     평균 정확도 +1.0%p 이상 AND 4개 연도 중 3개 이상에서 개선 → '방향 정보 있음'. 아니면 폐기.
     개별 특징: 4개 연도 전부 같은 쪽으로 |AUC−0.5| ≥ 0.05 이면 '일관 신호'로 표시.
  B. 선택(급변동 봉 rv_ratio>1.5 중 그가 진입한 봉 vs 안 한 봉): 기준 특징 대비 새 특징 추가 시 2021 AUC +0.02 이상
     AND 상위 1% 신호 적중률(±30분 내 같은 방향 진입) > 40% (사용자 규칙). 아니면 폐기.
  A·B 중 하나라도 통과하면 OKX/업비트 2022~2026 수익 검증으로 넘어간다. 둘 다 불합격이면 여기서 종료.
"""
import pathlib
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import features as F  # noqa: E402
from compare import auc, load  # noqa: E402
from direction import YEARS, loyo  # noqa: E402
from select_model import fill_pos, labels  # noqa: E402

ROOT = F.ROOT
KP = ["kp_dev", "kp_chg_15m", "kp_chg_1h", "kp_lvl", "up_vol_z"]
ETH = ["eth_ret_15m", "eth_ret_1h", "ethbtc_15m", "ethbtc_1h", "ethbtc_1d", "eth_pos_1h"]
BASE = ["pos_15m", "pos_1h", "pos_4h", "ema_1h", "rsi_5m", "ret_5m", "ret_15m", "ret_1h"]


def build():
    idx = F.candles().index
    rd = lambda n: pd.read_pickle(ROOT / "data_cache" / n).reindex(idx).ffill()  # noqa: E731
    bs, up, eth = rd("binance_spot_5m.pkl"), rd("upbit_btc_5m.pkl"), rd("binance_ethusdt_5m.pkl")
    fx = pd.read_pickle(ROOT / "data_cache" / "fx_usdkrw.pkl")
    fx = fx.shift(1).reindex(idx.normalize()).ffill().bfill()   # 전일 환율 (당일 값은 장 마감 후 공개)
    fx.index = idx
    kp = (up.close / (bs.close * fx) - 1) * 100
    f = pd.DataFrame(index=idx)
    f["kp_dev"] = kp - kp.rolling(288).median()
    f["kp_chg_15m"] = kp - kp.shift(3)
    f["kp_chg_1h"] = kp - kp.shift(12)
    f["kp_lvl"] = kp - kp.rolling(288 * 30).median()
    f["up_vol_z"] = np.log1p(up.volume.rolling(12).sum()) - np.log1p(up.volume.rolling(288).sum() / 24)
    r = lambda s, n: (s / s.shift(n) - 1) * 100  # noqa: E731
    f["eth_ret_15m"], f["eth_ret_1h"] = r(eth.close, 3), r(eth.close, 12)
    f["ethbtc_15m"] = r(eth.close, 3) - r(bs.close, 3)
    f["ethbtc_1h"] = r(eth.close, 12) - r(bs.close, 12)
    f["ethbtc_1d"] = r(eth.close, 288) - r(bs.close, 288)
    hi, lo = eth.high.rolling(12).max(), eth.low.rolling(12).min()
    f["eth_pos_1h"] = ((eth.close - lo) / (hi - lo).replace(0, np.nan)).fillna(0.5)
    return f, kp


def test_direction(nf):
    x = load()
    x = pd.concat([x, F.at(nf, x.start)], axis=1).dropna(subset=KP + ETH).reset_index(drop=True)
    print(f"A. 방향 — 표본 {len(x)}", x.groupby("y").size().to_dict())
    for col in KP + ETH:
        a = {y: auc(g[col], g.long) for y, g in x.groupby("y")}
        cons = all(v - 0.5 >= 0.05 for v in a.values()) or all(0.5 - v >= 0.05 for v in a.values())
        print(f"  {col:12s} AUC {auc(x[col], x.long):.3f}", {y: round(v, 3) for y, v in a.items()}, "← 일관 신호" if cons else "")
    mk = lambda: make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000))  # noqa: E731
    base = loyo(x, mk, BASE)
    print("  M1 기준            ", " ".join(f"{base[y]:.3f}" for y in YEARS))
    res = {}
    for name, cols in (("+김프", BASE + KP), ("+ETH", BASE + ETH), ("+김프+ETH", BASE + KP + ETH),
                       ("김프만", KP), ("ETH만", ETH)):
        acc = loyo(x, mk, cols)
        gain = [acc[y] - base[y] for y in YEARS]
        ok = np.mean(gain) >= 0.01 and sum(g > 0 for g in gain) >= 3
        res[name] = ok
        print(f"  {'PASS' if ok else 'DISCARD'} M1{name:10s}" if not name.endswith("만") else f"  (참고) {name:12s}",
              " ".join(f"{acc[y]:.3f}" for y in YEARS), f"| 평균 개선 {np.mean(gain) * 100:+.2f}%p, 개선 연도 {sum(g > 0 for g in gain)}/4")
    return any(v for k, v in res.items() if not k.endswith("만"))


def test_selection(nf):
    f = fill_pos(pd.read_pickle(ROOT / "data_cache" / "aoa_feat_5m.pkl")).join(nf).loc["2018-03-12":"2021-12-31"].dropna()
    L, S = labels(f)
    f = f[f.rv_ratio > 1.5]; L, S = L[f.index], S[f.index]
    ent = ((L + S) > 0).astype(int)
    tr, te = f.index.year < 2021, f.index.year == 2021
    print(f"\nB. 선택 — 급변동 봉 {len(f)}개 중 진입 {int(ent.sum())} (학습 {int(ent[tr].sum())} / 2021 {int(ent[te].sum())})")
    for col in KP + ETH:
        print(f"  {col:12s} 진입 판별 AUC", {y: round(auc(f.loc[f.index.year == y, col], ent[f.index.year == y]), 3) for y in YEARS})
    base_cols = [c for c in pd.read_pickle(ROOT / "data_cache" / "aoa_feat_5m.pkl").columns]
    mk = lambda: HistGradientBoostingClassifier(max_depth=3, max_iter=200, learning_rate=0.05,  # noqa: E731
                                               class_weight="balanced", random_state=0)
    ok_any = False
    near = lambda s: s.rolling(13, center=True, min_periods=1).max()  # noqa: E731
    for name, cols in (("기준", base_cols), ("+김프", base_cols + KP), ("+ETH", base_cols + ETH), ("+김프+ETH", base_cols + KP + ETH)):
        out = {}
        for side, lab in (("롱", L), ("숏", S)):
            m = mk().fit(f.loc[tr, cols], lab[tr])
            p = m.predict_proba(f.loc[te, cols])[:, 1]
            th = np.quantile(m.predict_proba(f.loc[tr, cols])[:, 1], 0.99)
            hit = near(lab[te]).values[p >= th]
            out[side] = (roc_auc_score(lab[te], p), hit.mean() if len(hit) else np.nan, len(hit))
        print(f"  {name:10s} 2021 AUC 롱 {out['롱'][0]:.3f} 숏 {out['숏'][0]:.3f} | 상위1% 적중 롱 {out['롱'][1]:.2f}({out['롱'][2]}) 숏 {out['숏'][1]:.2f}({out['숏'][2]})")
        if name == "기준":
            b = out
        else:
            ok = all(out[s][0] - b[s][0] >= 0.02 for s in out) and all(out[s][1] > 0.40 for s in out)
            ok_any |= ok
            print(f"    → {'PASS' if ok else 'DISCARD'}")
    return ok_any


if __name__ == "__main__":
    nf, kp = build()
    print("김프(%) 연도별 중앙값", kp.groupby(kp.index.year).median().round(2).to_dict(),
          "| 최대", kp.groupby(kp.index.year).max().round(1).to_dict())
    a = test_direction(nf)
    b = test_selection(nf)
    print("\n종합:", "수익 검증으로 진행" if (a or b) else "김프·ETH 둘 다 폐기 — 수익 검증 안 함")
