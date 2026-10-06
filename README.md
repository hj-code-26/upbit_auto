# RLS 자동매매 (OKX USDT 무기한 선물)

레짐 롱/숏 보유 전략(RLS-v0) **10x 변형**을 BTC-USDT-SWAP, ETH-USDT-SWAP에서 종목별로 독립 운용한다.
- 명세: `auto_okx_trading_start/research/RLS_model_prompt_10x.md` (기본 5x 명세 `RLS_model_prompt.md`)
- **검증된 수익 전략이 아니다.** 개발 연구에서 사전 기준 (a)를 충족하지 못했다.
- **10x는 FWD-v2 동결 범위(5x) 밖이며 전진 관찰로 검증되지 않았다.** 격리 청산 거리 ≈ 9.5%가 3×ATR 손절(보통 12~16%)보다 가까워 과거엔 손절보다 강제청산이 먼저 온 경우가 대부분이었다. ETH 10x 과거 MDD 23.9%는 20% 한도를 넘는다.
- 증거금 = 자산 2%, 격리 10x(명목 20%). 손절 `STOP_MODE=A`(3×ATR, 기본) | `B`(min(3×ATR, 청산 거리 − 1.5%p), 검증 안 됨, `rls_<MODE>_B.db`에 분리 기록).
- 강제청산 3회 연속 또는 낙폭 20% 초과 시 정지 → 확인 후 `python autotrade.py --resume <inst>`. 거래 손실 > 자산 3%면 이상 경보.

| 파일 | 역할 |
|---|---|
| `rls.py` | 규칙(순수 함수): 일봉 리샘플, 레짐, ATR14, 손절, 에지 재진입, 리플레이 |
| `okx.py` | OKX v5 클라이언트(실거래/데모)와 dry-run `Paper` |
| `autotrade.py` | 하루 1회 실행: 락 → 일봉 → 레짐 → 조회 → 비교 → 주문 → 기록 |
| `backtest.py` | 과거 리플레이(2020-01~) |
| `test_bot.py` | 검증 체크리스트 2~5 |
| `dashboard.py` | 읽기 전용 화면 + 스케줄러 |

```bash
pip install -r requirements.txt
python test_bot.py
python backtest.py
python autotrade.py --once   # MODE=dry 기본. cron: 5 0 * * * (UTC)
```

순서: dry 2주 이상 → `MODE=demo` 2~4주 → `MODE=live` 소액.
