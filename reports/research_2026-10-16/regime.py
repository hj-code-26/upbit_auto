"""장세 점수 (일봉, 그 봉 종가에 아는 값): ER 효율비, ADX14, CHOP14, 변동성 백분위."""
import sys; sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))
import numpy as np, pandas as pd

def er(c, n=20):
    return (c - c.shift(n)).abs() / c.diff().abs().rolling(n).sum()

def adx(d, n=14):
    up, dn = d.high.diff(), -d.low.diff()
    pdm = up.where((up > dn) & (up > 0), 0.0); ndm = dn.where((dn > up) & (dn > 0), 0.0)
    pc = d.close.shift()
    tr = pd.concat([d.high - d.low, (d.high - pc).abs(), (d.low - pc).abs()], axis=1).max(axis=1)
    w = lambda s: s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()   # noqa: E731
    atr = w(tr); pdi, ndi = 100 * w(pdm) / atr, 100 * w(ndm) / atr
    return w(100 * (pdi - ndi).abs() / (pdi + ndi))

def chop(d, n=14):
    pc = d.close.shift()
    tr = pd.concat([d.high - d.low, (d.high - pc).abs(), (d.low - pc).abs()], axis=1).max(axis=1)
    return 100 * np.log10(tr.rolling(n).sum() / (d.high.rolling(n).max() - d.low.rolling(n).min())) / np.log10(n)

def volpct(d, n=20, look=365):
    v = np.log(d.close).diff().rolling(n).std()
    return v.rolling(look, min_periods=120).rank(pct=True)

def scores(d):
    return pd.DataFrame({"er": er(d.close), "adx": adx(d), "chop": chop(d), "volp": volpct(d)})
