#!/bin/bash
# [root] 실거래 검증(forward.py) 설치·갱신: 하루 3번 (UTC 00·08·16:30) 운용 기록을 계좌 단위 리플레이와 비교 → forward.json.
# 매매 봇(rlsc-autotrade)은 멈추지 않는다: 봇이 쓰지 않는 forward.py·backtest.py 와 조회용 dashboard.py 만 바꾸고
# rlsc-dashboard 만 재시작한다. 봇 코드(rls·okx·autotrade)가 이 묶음과 다르면 중단 (R2 배포 먼저).
# 사용(ryan 이 서버에서): sudo bash /home/ryan/quant-deploy/rlsc_r2/forward_install.sh
# 되돌리기: sudo systemctl disable --now rlsc-forward.timer
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
WD=/home/quant/rlsc
[ "$(id -u)" = 0 ] || { echo "sudo 로 실행하세요"; exit 1; }
for f in rls.py okx.py autotrade.py; do
  cmp -s "$HERE/src/$f" "$WD/$f" || { echo "$f 가 운용 코드와 다름 → R2 배포 스크립트로 먼저 맞추세요 (중단)"; exit 1; }
done
MODE=$(systemctl show rlsc-autotrade -p Environment --value | tr ' ' '\n' | sed -n 's/^MODE=//p' | tail -1)
[ -n "$MODE" ] || { echo "rlsc-autotrade 의 MODE 를 못 읽음 (중단)"; exit 1; }
install -o quant -g quant -m 600 "$HERE/src/forward.py" "$HERE/src/backtest.py" "$HERE/src/dashboard.py" "$WD/"
install -d -o quant -g quant "$WD/reports/rlsc_runs"
install -o quant -g quant -m 600 "$HERE/reports/rlsc_runs/forward_bands.json" "$WD/reports/rlsc_runs/"
cat > /etc/systemd/system/rlsc-forward.service <<EOF
# RLS-C 실거래 검증 (운용 vs 계좌 단위 리플레이). 주문 없음, 운용 DB 읽기 전용. 결과 $WD/forward.json
[Unit]
Description=RLS-C forward check (live vs account replay, read-only)
After=network-online.target

[Service]
Type=oneshot
User=quant
Group=quant
WorkingDirectory=$WD
Environment=PYTHONUNBUFFERED=1 MODE=$MODE XDG_CACHE_HOME=$WD/.cache
ExecStart=/home/quant/venv_rlsc/bin/python forward.py
TimeoutStartSec=600
UMask=0077
MemoryMax=1G
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=$WD
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
EOF
cat > /etc/systemd/system/rlsc-forward.timer <<'EOF'
[Unit]
Description=RLS-C forward check 3x/day (after tranche jobs)

[Timer]
OnCalendar=*-*-* 00,08,16:30:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl restart rlsc-dashboard
systemctl enable --now rlsc-forward.timer
systemctl start rlsc-forward.service || { journalctl -u rlsc-forward --no-pager -n 20; exit 1; }
journalctl -u rlsc-forward --no-pager -n 3 -o cat | tail -2
systemctl list-timers rlsc-forward.timer --no-pager | head -2
echo "== 완료 (MODE=$MODE). 대시보드 상단 '실거래 검증' 줄과 Hub 코인 탭 '실거래 검증' 칸을 확인하세요."
