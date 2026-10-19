"""연구: 차트 이미지 학습 (CNN). 4H 봉 32개를 OHLC 막대 그림(64×96, 봉당 3px + MA20 선)으로 그려
다음 6봉(=1일) 의 박스 유지·돌파 방향·고가/저가 움직임을 맞춘다. 리플레이 전용, 운용 코드 아님.
분할(시간순): 학습 ~2023 · 검증 2024 · 시험 2025-01~ (ZEC 는 시험에만). 통과 조건 = 시험 정확도 ≥ 65%.
비교: 다수 클래스 · 숫자 특징 GBM (그림이 숫자보다 나은지). 사용: python reports/research_2026-10-19/chart_cnn.py → chart_cnn.csv"""
import pathlib, sys, warnings; warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent; sys.path.insert(0, str(HERE.parents[1]))
import numpy as np, pandas as pd, torch, torch.nn as nn
import backtest as bt
from sklearn.ensemble import HistGradientBoostingClassifier

W, F, HPX = 32, 6, 64                     # 창 봉 수 · 앞으로 볼 봉 수 · 그림 높이
COINS = ["BTC", "ETH", "SOL", "XRP", "ZEC"]
BOX_K = 4.9                               # 창 폭(최고−최저) ≤ BOX_K × ATR14(4H) 면 박스 (ETH 하위 ~25%)
dev = "mps" if torch.backends.mps.is_available() else "cpu"
torch.manual_seed(0); np.random.seed(0)


def bars4h(c):
    h = bt.cached_hourly(f"{c}-USDT-SWAP")
    b = h.assign(t=pd.to_datetime(h.ts, unit="ms")).set_index("t")[["open", "high", "low", "close"]].astype(float)
    return b.resample("4h").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()


def draw(o, h, l, c, ma):
    """OHLC 막대: 봉마다 3px (왼쪽 시가 틱, 가운데 고저 막대, 오른쪽 종가 틱). 세로는 창 안 최고·최저로 맞춤."""
    lo, hi = min(l.min(), np.nanmin(ma)), max(h.max(), np.nanmax(ma))
    y = lambda v: np.clip(((HPX - 1) * (1 - (v - lo) / (hi - lo + 1e-12))).round().astype(int), 0, HPX - 1)  # noqa: E731
    img = np.zeros((HPX, 3 * W), np.uint8)
    yo, yh, yl, yc, ym = y(o), y(h), y(l), y(c), y(ma)
    for j in range(W):
        img[yh[j]:yl[j] + 1, 3 * j + 1] = 255
        img[yo[j], 3 * j] = img[yc[j], 3 * j + 2] = 255
        img[ym[j], 3 * j:3 * j + 3] = 128
    return img


def samples(c):
    b = bars4h(c); O, H, L, C = (b[k].to_numpy() for k in ("open", "high", "low", "close"))
    pc = np.r_[np.nan, C[:-1]]
    atr = pd.Series(np.nanmax([H - L, abs(H - pc), abs(L - pc)], axis=0)).ewm(alpha=1 / 14, adjust=False).mean().to_numpy()
    ma = pd.Series(C).rolling(20).mean().to_numpy()
    imgs, rows = [], []
    for t in range(W + 20, len(b) - F):     # t = 창 마지막 봉 (그 종가에 안다)
        s = slice(t - W + 1, t + 1); fh, fl = H[t + 1:t + 1 + F].max(), L[t + 1:t + 1 + F].min()
        bh, bl = H[s].max(), L[s].min(); ph, pl = H[t - F + 1:t + 1].max(), L[t - F + 1:t + 1].min()
        box = (bh - bl) <= BOX_K * atr[t]
        # 박스 돌파 방향: 앞 봉들 중 처음으로 박스를 벗어난 쪽 (같은 봉 양쪽 = 제외)
        up = np.flatnonzero(H[t + 1:t + 1 + F] > bh); dn = np.flatnonzero(L[t + 1:t + 1 + F] < bl)
        fu, fd = (up[0] if len(up) else 99), (dn[0] if len(dn) else 99)
        # 전일 고가·저가 중 먼저 닿는 쪽
        uh = np.flatnonzero(H[t + 1:t + 1 + F] > ph); dl = np.flatnonzero(L[t + 1:t + 1 + F] < pl)
        gu, gd = (uh[0] if len(uh) else 99), (dl[0] if len(dl) else 99)
        rows.append(dict(
            coin=c, t=b.index[t], box=box,
            box_hold=float(fh <= bh and fl >= bl) if box else np.nan,          # 박스 유지 (위·아래 다 안 깸)
            box_dir=float(fu < fd) if box and fu != fd else np.nan,             # 박스 위로 먼저 깸
            hh=float(fh > ph), ll=float(fl < pl),                               # 다음 날 고가 > 전일 고가 / 저가 < 전일 저가
            hl_first=float(gu < gd) if gu != gd else np.nan,                    # 전일 고가를 먼저 깸
            expand=float((fh - fl) > (ph - pl)),                                # 다음 날 고저 폭 > 전일 폭
            up=float(C[t + F] > C[t]),                                          # 1일 뒤 종가 상승
            # 숫자 특징 (GBM 비교용, 그림과 같은 정보)
            f_w=(bh - bl) / atr[t], f_p=(ph - pl) / atr[t], f_pos=(C[t] - bl) / (bh - bl + 1e-12),
            f_ma=(C[t] - ma[t]) / atr[t], f_r1=(C[t] / C[t - F] - 1) / (atr[t] / C[t]),
            f_r5=(C[t] / C[t - W + 1] - 1) / (atr[t] / C[t]), f_atr=atr[t] / C[t],
            f_ar=atr[t] / np.nanmean(atr[t - W + 1:t + 1]), f_bar=(H[t] - L[t]) / atr[t],
            d_hi=(ph - C[t]) / atr[t], d_lo=(C[t] - pl) / atr[t], d_bhi=(bh - C[t]) / atr[t], d_blo=(C[t] - bl) / atr[t]))  # 거리 한 개짜리 규칙 비교용 (GBM 특징 아님)
        imgs.append(draw(O[s], H[s], L[s], C[s], ma[s]))
    return np.stack(imgs), pd.DataFrame(rows)


class Net(nn.Module):                       # Jiang·Kelly·Xiu (2023) 식 작은 CNN
    def __init__(s):
        super().__init__()
        blk = lambda i, o, st=1: [nn.Conv2d(i, o, (5, 3), stride=(st, 1), padding=(2, 1)), nn.BatchNorm2d(o), nn.LeakyReLU(), nn.MaxPool2d((2, 1))]  # noqa: E731
        s.f = nn.Sequential(*blk(1, 16, 2), *blk(16, 32), *blk(32, 64), nn.AdaptiveAvgPool2d((2, 8)), nn.Flatten(), nn.Dropout(0.5), nn.Linear(64 * 16, 1))

    def forward(s, x):
        return s.f(x).squeeze(1)


def fit_cnn(Xtr, ytr, Xva, yva, epochs=8):
    m, best, bstate = Net().to(dev), 9.0, None
    opt = torch.optim.Adam(m.parameters(), 1e-3, weight_decay=1e-4)
    pw = torch.tensor(float((1 - ytr.mean()) / ytr.mean()), device=dev)          # 불균형 보정
    lossf = nn.BCEWithLogitsLoss(pos_weight=pw)
    T = lambda a: torch.tensor(a, dtype=torch.float32)  # noqa: E731
    Xtr_, ytr_ = T(Xtr[:, None] / 255.).to(dev), T(ytr).to(dev)          # 한 번에 장치로
    for _ in range(epochs):
        m.train()
        for i in torch.randperm(len(ytr_), device=dev).split(256):
            opt.zero_grad(); lossf(m(Xtr_[i]), ytr_[i]).backward(); opt.step()
        p = predict(m, Xva); vl = lossf(T(np.log(p / (1 - p))).to(dev), T(yva).to(dev)).item()
        if vl < best:                       # 검증 손실 최저 시점 (조기 종료)
            best, bstate = vl, {k: v.clone() for k, v in m.state_dict().items()}
    m.load_state_dict(bstate); return m


@torch.no_grad()
def predict(m, X):
    m.eval(); out = []
    for i in range(0, len(X), 1024):
        out.append(torch.sigmoid(m(torch.tensor(X[i:i + 1024, None] / 255., dtype=torch.float32, device=dev))).cpu().numpy())
    return np.clip(np.concatenate(out), 1e-6, 1 - 1e-6)


def thr_acc(p, y, thr):
    return ((p >= thr) == y).mean()


if __name__ == "__main__":
    parts = [samples(c) for c in COINS]
    X = np.concatenate([p[0] for p in parts]); D = pd.concat([p[1] for p in parts], ignore_index=True)
    tr = (D.t < "2024-01-01") & (D.coin != "ZEC"); va = (D.t >= "2024-01-01") & (D.t < "2025-01-01") & (D.coin != "ZEC")
    te = D.t >= "2025-01-01"
    fcols = [c for c in D if c.startswith("f_")]
    out = []
    for task in ["box_hold", "box_dir", "hh", "ll", "hl_first", "expand", "up"]:
        ok = D[task].notna().to_numpy()
        a, b_, c_ = (ok & m.to_numpy() for m in (tr, va, te))
        y = D[task].to_numpy()
        cnn = fit_cnn(X[a], y[a], X[b_], y[b_])
        gbm = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, max_leaf_nodes=15, random_state=0).fit(D.loc[a, fcols], y[a])
        res = dict(task=task, n_train=a.sum(), n_test=c_.sum(), test_pos=y[c_].mean())
        maj = float(y[a].mean() >= 0.5)
        res["majority"] = (y[c_] == maj).mean()
        for name, pv, pt in (("cnn", predict(cnn, X[b_]), predict(cnn, X[c_])),
                             ("gbm", gbm.predict_proba(D.loc[b_, fcols])[:, 1], gbm.predict_proba(D.loc[c_, fcols])[:, 1])):
            ths = np.quantile(pv, np.linspace(0.05, 0.95, 37))      # 검증에서 정확도 최고 문턱 → 시험에 그대로
            th = ths[np.argmax([thr_acc(pv, y[b_], t) for t in ths])]
            res[f"{name}_val"] = thr_acc(pv, y[b_], th); res[f"{name}_test"] = thr_acc(pt, y[c_], th)
            res[f"{name}_bal"] = 0.5 * (((pt >= th) & (y[c_] == 1)).sum() / max(1, (y[c_] == 1).sum()) + ((pt < th) & (y[c_] == 0)).sum() / max(1, (y[c_] == 0).sum()))
            for cn in COINS:                  # 코인별 시험 정확도 (ZEC = 학습에 없던 상품)
                k = (D.coin == cn).to_numpy()[c_]
                res[f"{name}_{cn}"] = thr_acc(pt[k], y[c_][k], th) if k.any() else np.nan
        res["pass65"] = res["cnn_test"] >= 0.65
        res["cnn_over_majority"] = res["cnn_test"] - res["majority"]
        out.append(res); print({k: (round(v, 3) if isinstance(v, float) else v) for k, v in res.items()}, flush=True)
    pd.DataFrame(out).round(4).to_csv(HERE / "chart_cnn.csv", index=False)
