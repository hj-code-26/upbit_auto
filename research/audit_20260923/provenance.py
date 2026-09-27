"""provenance.json — SHA · 작업 트리 차이 해시 · 데이터 해시 · 라이브러리 버전 · seed. 읽기 전용."""
import datetime as dt
import hashlib
import json
import pathlib
import platform
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
git = lambda *a: subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True).stdout
sha = lambda b: hashlib.sha256(b).hexdigest()
DATA = ["okx_4h.pkl", "okx_1d.pkl", "okx_1m.pkl", "okx_1m_ranges.json", "bitstamp_4h_2016.pkl", "bitstamp_1d_2016.pkl",
        "binance_taker_1d.pkl"]


def meta(p):
    st = p.stat()
    out = {"sha256": sha(p.read_bytes()), "bytes": st.st_size,
           "mtime": dt.datetime.fromtimestamp(st.st_mtime).astimezone().isoformat()}
    if p.suffix == ".pkl":
        import pandas as pd
        d = pd.read_pickle(p)
        out.update(rows=len(d), first=str(d.index[0]), last=str(d.index[-1]))
    return out


import ccxt, numpy, pandas, scipy  # noqa: E401,E402

prov = {
    "generated_at": dt.datetime.now().astimezone().isoformat(),
    "remote": git("remote", "get-url", "origin").decode().strip(),
    "branch": git("branch", "--show-current").decode().strip(),
    "head": git("rev-parse", "HEAD").decode().strip(),
    "audit_range": "825267f..HEAD",
    "range_commits": git("log", "--format=%h %ad %s", "--date=iso", "825267f..HEAD").decode().splitlines(),
    "worktree_diff": {f: {"diff_sha256": sha(git("diff", "HEAD", "--", f)), "lines": git("diff", "--numstat", "HEAD", "--", f).decode().strip()}
                      for f in ("engine.py", "dashboard.py")},
    "untracked_used": {f: sha((ROOT / f).read_bytes()) for f in ("isolation.py",)},
    "engine_head_vs_worktree": "수치 동일 (repro.json · engine_head_vs_worktree_identical). 차이는 원장 열 i_in/i_out/px_in/px_out 추가뿐",
    "python": platform.python_version(),
    "libs": {"numpy": numpy.__version__, "pandas": pandas.__version__, "ccxt": ccxt.__version__, "scipy": scipy.__version__},
    "data": {f: meta(ROOT / "data_cache" / f) for f in DATA},
    "seeds": {"stats_paired": 20260923, "backtest_lev.boot (원 구현)": 0},
    "bootstrap": {"B": 20000, "blocks": [5, 10, 20, 40, 60], "main_block": 20, "boundary_rerun_B": 200000},
    "external": "OKX GET /api/v5/public/instruments (인증 없음) — out/okx_instrument.json 에 조회 시각 기록",
}
(pathlib.Path(__file__).parent / "provenance.json").write_text(json.dumps(prov, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({k: prov[k] for k in ("head", "branch", "worktree_diff", "untracked_used")}, ensure_ascii=False, indent=1))
