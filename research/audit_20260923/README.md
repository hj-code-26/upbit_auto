# 감사 2026-09-23 — EXIT_CONF 채택 · 레버리지 5→3 권고

대상 범위 `825267f..HEAD` (HEAD `e5e05e3`, 브랜치 `audit/exec-safety`, remote `hj-code-26/upbit_auto`).
결론은 [decision.md](decision.md), 결함 목록은 [findings.md](findings.md), 수치는 [statistics.md](statistics.md) 에 있다.

## 안전 제약 (지킨 것)
- 운영 파일(autotrade/model/okx/upbit/dashboard/engine/indicators/reversion, .env)은 **import 만 했고 수정하지 않았다**.
- 모든 스크립트는 저장소 루트의 `isolation.guard()` 아래에서 돈다. 이 guard 는 비밀값 환경변수를 지우고 trading.db·.env·로그 쓰기를 막는다.
  **isolation.py 는 미추적 파일이다** (HEAD 에 없음). 깨끗한 체크아웃에서는 이 감사도 ImportError 로 멈춘다. no-op 으로 대체하지 않았다.
- 주문·취소·레버리지 설정 API 는 한 번도 부르지 않았다. 외부 호출은 OKX 공개 상품 명세 GET 한 번뿐이다(`sizing.py`, 인증 없음).
- git reset/checkout/stash/clean 은 쓰지 않았다. HEAD 재현 점검은 scratchpad 에 `git archive` 로 풀어서 했다.

## 실행 (저장소 루트에서, 캐시된 데이터만 사용)
```
cd research/audit_20260923
python repro.py            # 보고 수치 재현 + 커밋본/작업트리 engine 수치 동일성   → out/repro.json
python test_engine.py      # 손계산 정답 테스트 11개 (확인된 결함 2개는 XFAIL)
python stats_paired.py     # paired block bootstrap · max-T 보정 · Bitstamp 분할/비용 · 관문 C 블록 민감도 (~3분)
python trade_map.py        # 기준선↔후보 에피소드 대응표 (--lev=1|3|5)
python episode_signflip.py # 에피소드 부호 뒤집기 (trade_map 먼저)
python tail.py             # 상위 k 제거(인자/재실행) · 청산 여유 · 비용/펀딩 스트레스
python entry_audit.py      # fallback='end' 수정판 대사 · 분봉 순서 낙관/비관 경계
python sizing.py           # 주문 크기 순수함수 재현 + OKX 공개 명세 + 이산 계약 근사 백테스트
python provenance.py       # provenance.json
```
관문 C 시드 몬테카를로(`out/bootC_seed_mc.csv`)는 statistics.md §5 에 적은 한 줄짜리 인라인 명령으로 만들었다.

## 의존성·데이터
Python 3.11.2 · numpy 2.3.5 · pandas 3.0.3 · ccxt 4.5.77 · scipy 1.17.1.
데이터는 `data_cache/` 의 okx_4h/1d/1m, okx_1m_ranges.json, bitstamp_4h/1d_2016, binance_taker_1d 이다. 해시·기간은 provenance.json 에 있고 audit/manifest.json 의 해시 접두와 일치한다.

## 실행 상태
| 스크립트 | 실행 | 비고 |
|---|---|---|
| repro.py | 실행함 | 보고 수치 전부 소수 넷째 자리까지 일치 |
| test_engine.py | 실행함 | 9 통과, 확인된 결함 2 (XFAIL) |
| stats_paired.py | 실행함 | B=20,000. 경계 재실행 조건(α/27 근처)에 걸리지 않아 B=200,000 재실행은 없었다 |
| trade_map.py / episode_signflip.py / tail.py / entry_audit.py / sizing.py | 실행함 | |

## 실행하지 않은 것 (검증 불가 또는 범위 밖)
- Hansen SPA(일관 재중심화) — 주 방법은 보수적 max-T(Romano–Wolf 단일단계)다. SPA 는 p 를 더 작게 만들 수 있다. 다만 개별 p 가 이미 0.05 를 넘으므로 판정은 바뀌지 않는다.
- 세션 전체(앙상블·VT·펀딩·시간대, 기준선 5배)의 공통 수익률 행렬 — 기준선과 가설이 달라서 만들지 않았다.
- 분봉 경로에서 자산 변화에 따라 계약 수가 바뀌는 동적 이산 계약 시뮬레이션 — 정적 pos_pct 근사만 했다.
- 과거 시점의 OKX 상품 명세 — 확인한 것은 현재 명세뿐이다.
- 2026-06 이전 실측 펀딩 — 상수 가정(0.01%/8h)을 쓰고 배수 스트레스만 걸었다.
- 로컬에서 커밋 전에 사전 실행했는지 여부 — 커밋 순서만으로는 증명할 수 없다.
