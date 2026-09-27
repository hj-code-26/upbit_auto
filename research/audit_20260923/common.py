"""감사 공통 로더 — 운영 코드를 **읽기 전용으로 import** 만 한다. 쓰기·주문 경로 없음.

isolation.guard() 는 저장소 루트의 **미추적(untracked)** isolation.py 를 쓴다 (HEAD 에는 없다).
원본이 없는 깨끗한 체크아웃에서는 이 파일도 ImportError 로 멈춘다 — 의도적이다 (no-op 대체 금지).
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = pathlib.Path(__file__).resolve().parent / "out"
sys.path.insert(0, str(ROOT))

from isolation import guard  # noqa: E402

guard()

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import backtest_exitconf as X  # noqa: E402
import backtest_okx as B  # noqa: E402
import backtest_quant as Q  # noqa: E402
import backtest_reversion as RV  # noqa: E402
import engine as E  # noqa: E402
import model as M  # noqa: E402

START, SPLIT, YEAR = Q.START, Q.SPLIT, Q.YEAR
_C = {}


def okx():
    """→ h4f(전체), bull(일봉 강세 개수), k(START 이후 마스크), h4, fills(재확보)."""
    if "okx" not in _C:
        h4f = B.fetch("4h")
        bull = B.bull(B.fetch("1d"))
        k = np.asarray(h4f.index >= pd.Timestamp(START, tz="UTC"))
        _C["okx"] = (h4f, bull, k, h4f[k], Q.reclaim_fills())
    return _C["okx"]


def run_okx(buf, lev=3, fills="reclaim", eng=E, pos_pct=1.0):
    """backtest_exitconf.signals + engine.run 과 같은 배선. buf=0 이 기준선."""
    h4f, bull, k, h4, fl = okx()
    en, ex = X.signals(h4f, bull, buf)
    z = np.zeros(len(h4), bool)
    return eng.run(h4, en[k], ex[k], z, z, lambda i, c: lev, allow=("long",),
                   fills=fl if fills == "reclaim" else fills, pos_pct=pos_pct)


def bitstamp():
    if "bs" not in _C:
        b4, b1 = RV.fetch("bitstamp", "4h"), RV.fetch("bitstamp", "1d")
        _C["bs"] = (b4, B.bull(M.add_indicators(b1.copy())))
    return _C["bs"]


def run_bs(buf, cost=None, lev=1):
    """backtest_exitconf (F) 와 같은 배선. cost 를 주면 B.COST 를 잠시 바꾼다 (engine.cost() 가 늦게 읽는다)."""
    b4, bull_b = bitstamp()
    keep = B.COST
    try:
        if cost is not None:
            B.COST = cost
        en, ex = X.signals(b4, bull_b, buf)
        z = np.zeros(len(b4), bool)
        return E.run(b4, en, ex, z, z, lambda i, c: lev, allow=("long",))
    finally:
        B.COST = keep


def daily(curve, lo=None, hi=None):
    """Q.metrics 와 같은 일별 재표본 → 일별 단순수익률 Series (첫날 제외)."""
    d = curve[lo:hi].resample("D").last().dropna()
    return (d / d.iloc[0]).pct_change().dropna()


def sharpe_cv(r):
    """Q.metrics 의 Sharpe 정의 그대로 (CAGR / 연율변동성). r: (..., n) 배열. yrs=(n+1)/YEAR 로 점추정 일치."""
    r = np.asarray(r)
    n = r.shape[-1]
    g = np.prod(1 + r, axis=-1)
    cagr = np.where(g > 0, np.abs(g) ** (YEAR / (n + 1)) - 1, -1.0)
    vol = r.std(axis=-1, ddof=1) * np.sqrt(YEAR)
    return cagr / vol


def sharpe_std(r):
    r = np.asarray(r)
    return r.mean(axis=-1) / r.std(axis=-1, ddof=1) * np.sqrt(YEAR)


def mlog(r):
    return np.log1p(np.asarray(r)).mean(axis=-1) * YEAR


def rets(res):
    return res[1].ret.values if len(res[1]) else np.zeros(1)
