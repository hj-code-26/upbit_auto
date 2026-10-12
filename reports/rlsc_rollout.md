# RLS-C v1 (위험 정책 R2) 모의 운용·배포·롤백 절차

> 이 문서는 절차 준비다. 이번 작업에서 서버 접속·서비스 재시작·주문·계좌 설정 변경은 하지 않았다. 아래 명령은 사용자가 확인하고 직접 실행한다.
> 서버 경로·서비스 이름은 패치 문서 기재값 (`~/quant-deploy/rlsc_src`, `rlsc-autotrade`, `rlsc-dashboard`, `operations/64-rlsc-mode.sh`) 이며 확인되지 않았다.

## 0. 먼저 확인할 것 (읽기 전용)

```bash
systemctl show rlsc-autotrade -p Environment -p ExecStart -p Description -p ActiveState
systemctl show rlsc-dashboard -p Environment -p ExecStart -p ActiveState
curl -s http://127.0.0.1:8000/api/state | python3 -c 'import json,sys;s=json.load(sys.stdin);print(s["mode"],s["tranches"],s["acct"])'
```

- **실행 소유권**: `dashboard.py` 는 `SCHEDULE=0` 이 아니면 자기 안에서 스케줄러를 돌린다. `rlsc-dashboard` 에 `SCHEDULE=0` 이 없으면 주문 주체가 둘이다 (락이 동시 실행만 막고, 두 프로세스가 차례로 같은 작업을 할 수 있다). 배포 전에 반드시 확인한다.
- MODE·RLSC_TRANCHES 는 `Environment=` 로 확정한다 (서비스 설명 문자열 아님). `RLSC_TRANCHES=0` 이면 기준 백테스트는 `reports/rlsc_runs/c1-l30` (해시 `34423b221c7e`).

## 1. 단계

| 단계 | 조건 | 할 일 | 끝내는 기준 (기간만으로 넘어가지 않음) |
|---|---|---|---|
| 오프라인 | 완료 | 35개 테스트, `backtest.py --coin` | — |
| dry 2주 | 별도 지시 | `MODE=dry`, `RLSC_RISK_POLL_SEC=60`: 신호·수량·장부·주문 의도 기록 | 매 작업 로그에 설정 해시·위험 상태, 대시보드 상태가 실제와 일치, 예외 없음 |
| demo 4주+ | demo 키·별도 지시 | `MODE=demo`, 아래 2절 체크리스트 | 2절 항목 전부 기록 (미발생 장애는 '미검증'으로 남김) |
| 소액 live 검토 | 사용자 결정 | 시험 손실 예산·설정 버전 동결·demo 결과 | 운영 검증과 수익성 증거를 따로 보고. 자동 전환 없음 |

## 2. demo 체크리스트 (로컬 Paper 로는 확인 불가)

1. 헤지 모드에서 포지션보다 큰 청산 시장가 → 거부인지 줄여 체결인지 (비상 청산·손절 동시 체결 T04 의 실제 동작).
2. 조건부 손절(`ordType=conditional`, `slTriggerPxType=last`)이 포지션이 0 이 되면 자동 취소되는지, 남는다면 다음 점검이 봇 소유 고아로 취소하는지.
3. `GET /api/v5/account/bills?type=1` 이 거래 계정 이체를 돌려주는지, `balChg` 부호.
4. `leverage-info` 방향별 값, 진입 직후 `liqPx` 와 손절 사이 간격 (티어·펀딩 반영), 2번째 몫 진입 후 재검사.
5. 위험 감시 지연: 로그의 `risk.t.detected` → `flatten_req` → `flat_ok` 시각 차이, 5초 감시가 API 호출 제한(잔액 10회/2초)에 걸리지 않는지.
6. 재시작 대사: 포지션 보유 중 서비스 재시작 → 첫 감시에서 미확정 주문·손절이 그대로인지.
7. 일부 몫 청산(레짐·DROP1) 후 남은 몫 손절 수량.

## 3. 배포 (코드 교체) 절차

새 코드는 상태를 자동으로 옮긴다: 첫 실행 때 kv `acct` 의 `peak/rec_eq/rec_day/halted/dl_day` → kv `risk` 의 `hwm/day/flatten` (테스트 `test_state_migration_from_acct`). 몫 장부 키(`BTC-USDT-SWAP#k`)·주문 ID·edge(`blk`)·`seeded`·강제청산 진입 중단(`acct.hold`)·연패는 그대로 쓴다. 새 키: `risk`, `pending`, `applied`, `config`.

1. **시각**: 몫 작업(HH:02)·점검(HH:07) 직후가 아닌 조용한 때 (예: HH:20~HH:50).
2. **보호 확인**: 열린 몫마다 거래소에 손절 조건부 주문이 있는지 대시보드·거래소 화면으로 확인. 손절은 거래소 측 주문이라 봇이 멈춰도 남는다.
3. **백업** (서버, 봇 사용자):
   ```bash
   cd ~/quant-deploy && ts=$(date -u +%Y%m%dT%H%M) && tar czf rlsc_backup_$ts.tgz rlsc_src --exclude='rlsc_src/data_cache'
   sqlite3 rlsc_src/rlsc_live.db ".backup rlsc_src/rlsc_live.$ts.db"     # MODE 에 맞는 DB 이름
   (cd rlsc_src && sha256sum *.py > ../rlsc_src.$ts.sha256 && python3 -m pip freeze > ../rlsc_src.$ts.pip)
   ```
   `.env` 는 복사·출력하지 않는다 (서버에 그대로 둔다).
4. **멈춤**: `sudo systemctl stop rlsc-autotrade` (대시보드가 스케줄러를 돌리면 그것도). 멈춘 동안 손절은 거래소에 남지만 일손실·킬 감시는 없다 — 멈춤 시간을 최소로.
5. **교체**: 새 `rls.py okx.py autotrade.py dashboard.py backtest.py test_bot.py` 복사 → `python3 test_bot.py` (서버에서 35개 통과 확인, 네트워크 불필요).
6. **조회만 점검**: `MODE=<현재값> python3 autotrade.py --preflight` (주문·설정 변경 없음).
7. **시작**: `sudo systemctl start rlsc-autotrade`. 첫 1분 안에 위험 감시가 대사·손절 점검(`guard`)을 한다. 로그에서 `설정 <해시>`, `위험 RUNNING`, 손절 불일치 경보가 없는지 확인. 새 서비스 시작 직후 일 기준은 옛 00:02 기록을 이어 쓴다 (기록이 없으면 지금 값으로 만들고 '지연' 표시 → 그날 신규 진입 보류, 보호·청산은 정상).
8. **모르는 상태 발견 시**: 봇이 모르는 포지션·조건부 주문·미확정 주문이 보이면 새 코드는 진입을 막고(`foreign`/`mismatch`/`pending`) 자동 취소·청산하지 않는다. 대시보드의 진입 차단 사유를 보고 수동으로 정리한 뒤, 사유가 사라지면 자동 해제된다 (`--resume` 은 킬·강제청산·연패용).

## 3-1. 3몫 전환 + 소액 계좌 증거금 비율 (2026-10-11 사용자 결정)

새 코드 배포(3절)가 먼저다 — 옛 코드는 `RLSC_MARGIN` 을 모르고 2% 를 쓴다.

1. 잔고 확인 (조회만): `MODE=live python3 autotrade.py --preflight` → "지금 잔고로 최소 주문을 맞추는 종목당 증거금 ≈ x%" (BTC 줄).
2. 비율 결정: 그 값 이상, 5% 이하. `reports/rlsc_validation.md` 8절 표의 낙폭을 보고 정한다. 17 USDT 미만이면 BTC 불가 → 2% 로 두고 ETH 만.
3. 서비스 환경 (autotrade 와 dashboard 둘 다 — 대시보드도 몫 수를 환경변수로 읽는다):
   ```bash
   sudo systemctl edit rlsc-autotrade      # [Service] 아래에: Environment=RLSC_TRANCHES=0,8,16 RLSC_MARGIN=0.03
   sudo systemctl edit rlsc-dashboard      # 같은 두 값 (+ SCHEDULE=0 확인)
   sudo systemctl daemon-reload && sudo systemctl restart rlsc-autotrade rlsc-dashboard
   systemctl show rlsc-autotrade -p Environment     # 뒤에 쓴 값이 앞의 RLSC_TRANCHES=0 을 덮는다
   ```
4. 시작 로그의 `설정 <해시>` 가 `reports/rlsc_runs/c3-l30-m<비율>/manifest.json` 의 `strategy_hash` 와 같은지 확인 (대시보드에 그 리플레이가 보이면 일치).
5. 열린 1몫 포지션이 있으면 그 몫(#0)은 원래 수량·손절 그대로 규칙대로 정리되고, 새 진입부터 몫당 비율/3 이 적용된다.

## 4. 롤백

코드만 되돌리면 상태가 되돌아가지 않는다. 옛 코드(R1)는 kv `risk` 를 모르고 `acct.peak/rec_eq/halted` 만 본다 — 새 코드 실행 중에는 이 값들이 갱신되지 않으므로 그대로 되돌리면 오래된 기준으로 일손실이 오발동하거나 킬 상태를 놓친다.

1. 멈춤 (3-4 와 같음). 미확정 주문이 없어야 한다:
   ```bash
   MODE=<현재값> python3 -c "import autotrade as A; print(A.kv_get('pending'))"     # None 또는 {} 이어야 함
   ```
2. 새 상태를 옛 필드로 되쓰기 (DB 백업 후):
   ```bash
   MODE=<현재값> python3 -c "import autotrade as A; r=A.risk_get(); d=r.get('day') or {}; a=A.kv_get('acct',{}); a.update(peak=r['hwm'], rec_eq=d.get('eq'), rec_day=d.get('date'), halted='kill' in r['flatten'], dl_day=r['flatten'].get('daily_loss')); A.kv_set('acct',a); print(a)"
   ```
3. 백업한 옛 코드로 교체 → 시작. 옛 코드는 몫에 속하지 않는 **모든** 조건부 주문을 취소한다 (수동 주문 포함) — 수동 조건부 주문이 있으면 먼저 정리하거나 롤백하지 않는다.
4. 옛 코드에서는 킬 청산이 다음 몫 작업까지 미뤄진다 (R1).

## 5. 운용 중 보는 것

- 대시보드 상단: `위험 상태` (정상 / 진입 금지 기록 / 전량 청산 진행 중 / 전량 정리 완료 / 조회·주문 실패), `신규 진입` 차단 사유, 마지막 조회·감지·청산 요청·flat 확인 시각, 설정 해시·코드(미커밋 여부).
- `HALTED` 는 봇 수량 0 이 확인된 상태다. 수동 포지션이 남아 있을 수 있다 (`mismatch`/`foreign` 사유로 표시).
- 경보: 상태 변화·새 차단 사유만 보낸다 (같은 사유 반복 경보 없음). 테스트는 실제 웹훅을 부르지 않는다.
