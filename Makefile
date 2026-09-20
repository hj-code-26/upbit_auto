# make run   : 포트 정리 → 대시보드+봇 실행  (Windows PowerShell 기준)
SHELL := powershell.exe
.SHELLFLAGS := -NoProfile -ExecutionPolicy Bypass -Command
PORT ?= 8000
PY ?= python

.PHONY: vt vtwf ensemble run bot view botstop multi stop omni on off once test offline audit sizing keys liquidate reset minutes entry sweep quant both downside rev lat bias current sexit flow learn

run: stop omni
	@$(PY) dashboard.py

bot: omni                               # 터미널 봇: 대시보드 없이 INTERVAL_MIN 분마다 판단·주문 (Ctrl+C 로 종료)
	@$(PY) autotrade.py

view: stop                              # 보기 전용 대시보드 — make bot 이 떠 있으면 판단·주문 버튼이 잠긴다
	@$(PY) dashboard.py

botstop:                                # 주문 락(127.0.0.1:8765)을 잡은 프로세스 = 돌고 있는 봇을 끈다
	@Get-NetTCPConnection -LocalPort 8765 -State Bound -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "kill bot: PID $$($$_.OwningProcess)"; Stop-Process -Id $$_.OwningProcess -Force -ErrorAction SilentlyContinue }; exit 0

soxl:                                   # quant_nasq100 SOXL 규칙(밴드 리밸런싱+폭락 게이트) 코인 이식 검증. 결론: REJECT
	@$(PY) backtest_soxl.py

multi:                                  # 거래량 상위 10코인 포트폴리오 (첫 실행은 --fetch 먼저). 결론: DOGE 의존이라 기각
	@$(PY) backtest_multi.py

omni:                                   # OmniRoute(Claude 게이트웨이) 가 안 떠 있으면 백그라운드로 띄운다
	@if (-not (Get-NetTCPConnection -LocalPort 20128 -State Listen -ErrorAction SilentlyContinue)) { omniroute serve --daemon --no-open | Out-Null; Write-Host "omniroute 시작 (http://localhost:20128)" }; exit 0

stop:
	@Get-NetTCPConnection -LocalPort $(PORT) -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "kill port $(PORT): PID $$($$_.OwningProcess)"; Stop-Process -Id $$_.OwningProcess -Force -ErrorAction SilentlyContinue }; exit 0

on:
	@Remove-Item autorun.off -ErrorAction SilentlyContinue; Write-Host "autorun ON"

off:
	@New-Item -ItemType File autorun.off -Force | Out-Null; Write-Host "autorun OFF (cycles skipped, dashboard keeps running)"

once:
	@$(PY) autotrade.py --once

test:
	@$(PY) okx.py; $(PY) test_bot.py; $(PY) test_leverage.py; $(PY) model.py; $(PY) minute_data.py

offline:                                # 거래소 없이 도는 점검만 (엔진·신호·시간축·상태기계)
	@$(PY) engine.py; $(PY) model.py; $(PY) indicators.py; $(PY) test_signal.py; $(PY) test_bot.py

# ---- 2026-09-10 감사 (audit/AUDIT.md) ----
audit:                                  # 3단계 성적 비교 CSV + 거래 원장 (원본 / 엔진수정 / 전략변경)
	@$(PY) audit/manifest.py | Out-Null; $(PY) audit/compare.py

sizing:                                 # 사이징 후보 · 지표 중복 ablation · 스트레스 · bootstrap
	@$(PY) backtest_sizing.py --boot

# ---- 진입 타이밍 연구 (research_entry_timing.txt) ----
entry:                                  # 진입 정책 비교. 첫 실행은 이벤트 구간 분봉을 받는다 (약 1분)
	@$(PY) backtest_entry.py

sweep:                                  # CONF·파라미터·기간 민감도 (과최적화 점검)
	@$(PY) backtest_entry.py sweep

quant:                                  # quant_nasq100 기법 이식 검증 (변동성 타겟팅·만기 청산·하락국면)
	@$(PY) backtest_quant.py --robust

both:                                   # 롱·숏 양방향 + 국면별 승률 (결론: 롱 전용 유지. research_both_sides.txt)
	@$(PY) backtest_both.py

downside:                               # 숏 대안 3종 (짧은 청산선·반등 롱·실제 펀딩). 결론: 셋 다 음수
	@$(PY) backtest_downside.py

rev:                                    # 고점 숏·저점 롱 (평균회귀) 10년 검증 + 나스닥 + 지표 기여도
	@$(PY) backtest_reversion.py --ablation

lat:                                    # 갱신주기·AI 호출 지연의 비용 (분봉 전량 필요)
	@$(PY) backtest_latency.py

bias:                                   # 비판적 검증: 추세편향·중복표본·순열·다중검정 (결론: EXTREME_MIN 기각)
	@$(PY) backtest_bias.py --trials=400

current:                                # 지금 .env 설정 그대로의 수익률·승률·롱숏 비율 (--lev 로 레버리지 스윕)
	@$(PY) backtest_current.py

sexit:                                  # 숏 청산을 다른 축(시간·ATR·익절)으로 재설계 (결론: 28조합 전부 음수)
	@$(PY) backtest_shortexit.py

vt:                                     # 변동성 타겟팅 재측정 (상한 5배). 결론: 사전등록 좌표 REJECT, 창 선택은 vtwf
	@$(PY) backtest_vt.py

ensemble:                               # 다중 룩백 앙상블 (6,3)(12,6)(30,15) 사전등록 검증 (결론: REJECT)
	@$(PY) backtest_ensemble.py

flow:                                   # 매수/매도 흐름의 반복 패턴 (바이낸스 taker 7년. 결론: 관성이지 반전 아님)
	@$(PY) backtest_flow.py

minutes:                                # 1분봉 전량 내려받기 (약 2시간, 끊겨도 이어받음). learn 에 필요
	@$(PY) minute_data.py full

learn:                                  # 분봉 지표 학습 → 워크포워드 평가 (minutes 먼저)
	@$(PY) minute_model.py

keys:
	@$(PY) okx.py keys

liquidate:
	@$(PY) liquidate_upbit.py

reset:                                  # 모의 장부·판단 기록 초기화 (거래소 계좌는 건드리지 않음)
	@$(MAKE) stop; Remove-Item trading.db -ErrorAction SilentlyContinue; Write-Host "trading.db removed"
