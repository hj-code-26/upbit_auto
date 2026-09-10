"""데이터·설정 manifest — 백테스트 결과를 재현하는 데 필요한 것만 기록한다.

출력: audit/manifest.json (해시·기간·결측/중복봉·의존성·커밋). 실행: python audit/manifest.py
"""
import hashlib
import json
import pathlib
import subprocess
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE = ROOT / "data_cache"
STEP = {"okx_1d": "1d", "okx_4h": "4h", "okx_1h": "1h", "okx_1m": "1min"}


def bars(name):
    f = CACHE / f"{name}.pkl"
    d = pd.read_pickle(f)
    full = pd.date_range(d.index[0], d.index[-1], freq=STEP[name])
    return {"file": f.name, "sha256": hashlib.sha256(f.read_bytes()).hexdigest()[:16], "rows": len(d),
            "tz": str(d.index.tz), "first": str(d.index[0]), "last": str(d.index[-1]),
            "missing_bars": int(len(full) - len(d.index.unique())), "duplicate_ts": int(d.index.duplicated().sum())}


def main():
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        k, _, v = line.partition("=")
        k = k.strip()
        if k and not k.startswith("#") and "KEY" not in k and "SECRET" not in k and "PASSPHRASE" not in k:
            env[k] = v.split("#")[0].strip()
    fund = pd.read_pickle(CACHE / "okx_funding.pkl")
    m = {
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()),
        "python": sys.version.split()[0],
        "deps": {p: __import__(p).__version__ for p in ("pandas", "numpy", "ccxt")},
        "env_public": env,
        "data": {n: bars(n) for n in STEP},
        "funding_real": {"rows": len(fund), "first": str(fund.index[0]), "last": str(fund.index[-1]),
                         "mean_per_8h_pct": round(float(fund.mean()) * 100, 5),
                         "backtest_assumption_pct": 0.01,
                         "note": "실측 펀딩은 2026-06 이후만 있다. 5.5년 백테스트는 상수 0.01%/8h 가정을 쓴다 (미검증)"},
        "minute_coverage_note": "okx_1m 은 2021-03~ 전 구간이 있으나 backtest_entry 는 이벤트 창만 쓴다",
    }
    (ROOT / "audit" / "manifest.json").write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(m, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
