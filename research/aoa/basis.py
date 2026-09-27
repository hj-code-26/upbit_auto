"""가설: 그는 BitMEX 가 현물보다 과하게 싸질 때(연쇄 청산) 사고 비싸질 때 판다. + 현물 주문흐름(테이커 매수 비중).

특징 (모두 완성된 5분봉 기준, features.at 규약으로 진입 직전 봉):
  basis      = BitMEX 종가 / 바이낸스 현물 종가 − 1 (%), 하루 중앙값을 뺀 값 (USDT/USD 차이·상시 프리미엄 제거)
  basis_chg  = basis 의 15분 변화
  tbr_15m/1h = 현물 테이커 매수량 / 거래량 (0.5 = 균형) − 0.5
  div_1h     = BitMEX 1h 수익률 − 현물 1h 수익률
"""
import pathlib
import sys

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import features as F  # noqa: E402
from compare import auc, load  # noqa: E402
from direction import YEARS, loyo, verdict  # noqa: E402

NEW = ["basis", "basis_chg", "tbr_15m", "tbr_1h", "div_1h"]


def build():
    c = F.candles()
    s = pd.read_pickle(F.ROOT / "data_cache" / "binance_spot_5m.pkl").reindex(c.index).ffill()
    b = (c.close / s.close - 1) * 100
    f = pd.DataFrame(index=c.index)
    f["basis"] = b - b.rolling(288).median()
    f["basis_chg"] = b - b.shift(3)
    f["tbr_15m"] = s.tbv.rolling(3).sum() / s.volume.rolling(3).sum() - 0.5
    f["tbr_1h"] = s.tbv.rolling(12).sum() / s.volume.rolling(12).sum() - 0.5
    f["div_1h"] = (c.close / c.close.shift(12) - s.close / s.close.shift(12)) * 100
    return f


if __name__ == "__main__":
    x = load()
    nf = F.at(build(), x.start)
    x = pd.concat([x, nf], axis=1).dropna(subset=NEW).reset_index(drop=True)
    print("표본", len(x))
    for col in NEW:
        print(f"{col:10s} AUC {auc(x[col], x.long):.3f}", {y: round(auc(g[col], g.long), 3) for y, g in x.groupby("y")},
              "롱 중앙값", round(x.loc[x.long == 1, col].median(), 4), "숏", round(x.loc[x.long == 0, col].median(), 4))
    base = {y: max(g.long.mean(), 1 - g.long.mean()) for y, g in x.groupby("y")}
    short = ["pos_15m", "pos_1h", "pos_4h", "ema_1h", "rsi_5m", "ret_5m", "ret_15m", "ret_1h"]
    mk = lambda: make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000))  # noqa: E731
    verdict("M1 로지스틱(단기 8개) — 기준", loyo(x, mk, short), base)
    verdict("M6 로지스틱(단기 8개 + 괴리·흐름 5개)", loyo(x, mk, short + NEW), base)
    verdict("M7 로지스틱(괴리·흐름 5개만)", loyo(x, mk, NEW), base)
