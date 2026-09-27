"""사후 감사 분석 — 에피소드 단위 부호 뒤집기 (trade_map 의 비동일 에피소드 8개).

가정: 귀무(EXIT_CONF 무효과)에서 각 에피소드 Δlog 의 부호는 대칭·독립 (크기는 관측값 고정). 2^8 = 256 전수 열거 → 정확.
  (1) 합계 Δ 의 단측 p
  (2) 관문 D·E 의 로그수익 근사판(검증 구간 Δ>0 · 탐색 구간 Δ≥0) 이 귀무에서 동시에 통과할 확률
      = 이 두 관문이 '효과 없음' 을 걸러내는 힘의 근사. Sharpe 가 아니라 로그수익 합이므로 근사다.
python episode_signflip.py  (trade_map.py 를 먼저 돌려 out/episodes_lev3.csv 가 있어야 한다)
"""
import itertools
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
from common import OUT, SPLIT  # noqa: E402

E = pd.read_csv(OUT / "episodes_lev3.csv")
d = E[E.kind != "동일"]
x = d.dlog.values
val = (pd.to_datetime(d.start) >= pd.Timestamp(SPLIT, tz="UTC")).values
obs, obs_v, obs_e = x.sum(), x[val].sum(), x[~val].sum()
S = np.array(list(itertools.product((1, -1), repeat=len(x))))
tot, v, e = (S * x).sum(1), (S * np.where(val, x, 0)).sum(1), (S * np.where(~val, x, 0)).sum(1)
res = {"episodes": len(x), "obs_sum": obs, "obs_valid": obs_v, "obs_explore": obs_e,
       "p_sum_onesided": float((tot >= obs - 1e-12).mean()),
       "P_null(D&E pass, log-approx)": float(((v > 0) & (e >= 0)).mean()),
       "P_null(D&E&sum>0)": float(((v > 0) & (e >= 0) & (tot > 0)).mean()),
       "largest_episode_share%": float(x.max() / obs * 100)}
pd.Series(res).to_csv(OUT / "episode_signflip.csv", encoding="utf-8-sig")
print(pd.Series(res).to_string())
