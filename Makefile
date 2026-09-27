# make run   : 포트 정리 → 대시보드+봇 실행  (Windows PowerShell 기준)
SHELL := powershell.exe
.SHELLFLAGS := -NoProfile -ExecutionPolicy Bypass -Command
PORT ?= 8000
PY ?= python

.PHONY: run bot view botstop stop omni on off once test offline keys reset replay

run: stop omni
	@$(PY) dashboard.py

bot: omni                               # 터미널 봇: 대시보드 없이 5분봉 마감마다 판단·주문 (Ctrl+C 로 종료)
	@$(PY) autotrade.py

view: stop                              # 보기 전용 대시보드 — make bot 이 떠 있으면 판단·주문 버튼이 잠긴다
	@$(PY) dashboard.py

botstop:                                # 주문 락(127.0.0.1:8765)을 잡은 프로세스 = 돌고 있는 봇을 끈다
	@Get-NetTCPConnection -LocalPort 8765 -State Bound -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "kill bot: PID $$($$_.OwningProcess)"; Stop-Process -Id $$_.OwningProcess -Force -ErrorAction SilentlyContinue }; exit 0

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

test:                                   # 거래소 공개 API 포함 전체 점검
	@$(PY) okx.py; $(PY) test_bot.py; $(PY) test_leverage.py; $(PY) strategy.py

offline:                                # 거래소 없이 도는 점검만 (상태기계·청산선·재생 대조 — data_cache 필요)
	@$(PY) test_bot.py; $(PY) test_leverage.py; $(PY) strategy.py

replay:                                 # 1배/2배 성적표 재측정 (research/aoa/dip_lev2.py, data_cache 필요)
	@$(PY) research/aoa/dip_lev2.py

keys:
	@$(PY) okx.py keys

reset:                                  # 모의 장부·판단 기록 초기화 (거래소 계좌는 건드리지 않음)
	@$(MAKE) stop; Remove-Item trading.db -ErrorAction SilentlyContinue; Write-Host "trading.db removed"
