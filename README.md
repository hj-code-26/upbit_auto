# RLS 20x DROP1 자동매매 (OKX USDT 무기한 선물)

레짐 롱/숏 보유(RLS) + 3일 확인(CONF3) + 본전 이동(BE1) + 24시간 역행 조기 청산(DROP1)을 BTC-USDT-SWAP, ETH-USDT-SWAP에서 종목별 독립 신호로 운용한다.
- 명세: `auto_okx_trading_start/research/RLS_model_prompt_20x_drop1.md`, 연구 엔진 `src/analysis/rls_win.py` (EARLY-v0 DROP1)
- **과거 백테스트의 사후 탐색 결과이며 수익을 보장하지 않는다.** 20x 격리 청산 거리 ≈ 4.55%라 손절(최대 3.55%)을 항상 청산가 안쪽에 둔다.
- 격리 20x, 증거금 = 자산 4% (명목 80%, 종목당), 동시 증거금 합 ≤ 10%. 매시 HH:02 UTC 실행.
- 위험 한도: 당일 시작 대비 −5% → 전량 청산·그날 진입 중단 / 고점 대비 35% → 정지 / 강제청산 1건 → 진입 중단 / 10연패 → 그 종목 진입 중단 (모두 `python autotrade.py --resume`). 7연패·거래 손실 > 자산 4%는 경보.
- 시작 시점에 이미 켜진 레짐에는 진입하지 않고 다음 확정 레짐 에지를 기다린다.

**ATR 주의**: 명세 문구는 Wilder ATR, 명세 수치(CAGR 30.9%, MDD 19.0%)는 연구 엔진의 SMA14 ATR로 나왔다. `rls.ATR_KIND`(기본 `wilder`)로 바꾼다.

| 리플레이 2020-01~2026-10 | CAGR | MDD | Sharpe | 최악 주 | 거래 | 승률 | 청산 |
|---|---|---|---|---|---|---|---|
| 명세 (연구 엔진) | 30.9% | 19.0% | 1.15 | −7.8% | 119 | 38.7% | 0 |
| `backtest.py` ATR=sma | 31.8% | 19.0% | 1.18 | −7.8% | 113 | 38.9% | 0 |
| `backtest.py` Wilder (기본) | 24.4% | 20.9% | 1.00 | −8.6% | 113 | 38.9% | 0 |

| 파일 | 역할 |
|---|---|
| `rls.py` | 규칙(순수 함수): 일봉, 레짐, CONF3, ATR14, 손절 cap, BE1, DROP1, 1H 리플레이, RLS 5x 기준선 |
| `okx.py` | OKX v5 클라이언트(실거래/데모)와 dry-run `Paper` (1H 봉으로 손절·청산·펀딩 재생) |
| `autotrade.py` | 매시 실행: 락 → 확정봉 → 거래소 조회 → 위험 한도 → 레짐 → DROP1 → 본전 → 주문 → 기록 |
| `backtest.py` | 과거 리플레이 (20x/10x/7x, RLS 5x, 매수보유) |
| `test_bot.py` | 검증 체크리스트 2·3·5 |
| `dashboard.py` | 읽기 전용 화면 + 스케줄러 |

```bash
pip install -r requirements.txt
python test_bot.py
python backtest.py
python autotrade.py --once   # MODE=dry 기본. cron: 2 * * * * (UTC)
```

순서: dry 2주 → `MODE=demo` 4주 이상 (청산가가 OKX 공식·유지증거금 티어와 맞는지 확인) → `MODE=live` 소액.
