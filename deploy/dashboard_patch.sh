#!/bin/bash
# [root] 대시보드만 교체 (매매 봇 rlsc-autotrade 는 멈추지 않는다): src/dashboard.py → /home/quant/rlsc, rlsc-dashboard 만 재시작.
# 2026-10-19: 몫마다 '시장' 표시 (좁은 봉 < 0.6 ATR · 진입 방향 SMA50 20일 기울기 > 6%, 매매 규칙 아님). 옛 봇 코드면 공개 4H 봉으로 계산.
# 순서: 새 dashboard + 운용 코드·DB 사본으로 임시 폴더에서 /api/state 시험 → 백업 → 교체 → 재시작 → 127.0.0.1:8000 확인 (실패하면 자동 복구).
# 사용(ryan 이 서버에서): sudo bash ~/rlsc_deploy/dashboard_patch.sh
# 되돌리기:            sudo bash ~/rlsc_deploy/dashboard_patch.sh --rollback <백업 파일>
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
WD=/home/quant/rlsc
DASH=rlsc-dashboard
[ "$(id -u)" = 0 ] || { echo "sudo 로 실행하세요"; exit 1; }
prop() { systemctl show "$1" -p "$2" --value; }
RUSER=$(prop $DASH User); RUSER=${RUSER:-quant}
PY=$(prop $DASH ExecStart | sed -n 's/.*path=\([^ ;]*\).*/\1/p' | head -1)
ENVS=$(prop $DASH Environment)
PORT=$(tr ' ' '\n' <<<"$ENVS" | sed -n 's/^DASHBOARD_PORT=//p' | tail -1); PORT=${PORT:-8000}

restore() {
  install -o "$RUSER" -g "$RUSER" -m 600 "$1" "$WD/dashboard.py"
  systemctl restart $DASH; sleep 3; systemctl is-active $DASH || true
  echo "== 복구: $1 → $WD/dashboard.py"
}
if [ "${1:-}" = "--rollback" ]; then restore "${2:?백업 파일}"; exit 0; fi

echo "== 1) 임시 폴더에서 시험 (운용 코드·DB 사본 + 새 dashboard.py, 조회만)"
T=$(mktemp -d); cp -a "$WD"/*.py "$T"/; cp "$HERE/src/dashboard.py" "$T/"
MODE=$(tr ' ' '\n' <<<"$ENVS" | sed -n 's/^MODE=//p' | tail -1); MODE=${MODE:-dry}
[ -f "$WD/rlsc_$MODE.db" ] && "$PY" -c "import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d)" "$WD/rlsc_$MODE.db" "$T/rlsc_$MODE.db"
[ -d "$WD/reports" ] && cp -a "$WD/reports" "$T/"
chown -R "$RUSER" "$T"
(cd "$T" && sudo -u "$RUSER" env $ENVS SCHEDULE=0 "$PY" -c "
import dashboard as D
s = D.api_state()
for x in s['levels']: print('  ', x['key'], x['state'], '| 시장:', x.get('market') or '-')
") || { echo "시험 실패 → 중단 (대시보드 그대로)"; rm -rf "$T"; exit 1; }
rm -rf "$T"

echo "== 2) 백업 → 교체 → 재시작"
BK="$(dirname "$WD")/dashboard.py.bak-$(date -u +%Y%m%dT%H%M%S)"
cp -a "$WD/dashboard.py" "$BK"
install -o "$RUSER" -g "$RUSER" -m 600 "$HERE/src/dashboard.py" "$WD/dashboard.py"
systemctl restart $DASH

echo "== 3) 확인 (최대 60초)"
ok=""
for _ in $(seq 12); do
  sleep 5
  if curl -s -m 30 "http://127.0.0.1:$PORT/api/state" | "$PY" -c "import json,sys; d=json.load(sys.stdin); assert 'levels' in d" 2>/dev/null; then ok=1; break; fi
done
if [ -z "$ok" ]; then
  echo "!! 대시보드 응답 확인 실패:"; journalctl -u $DASH --no-pager -n 15 -o cat || true
  restore "$BK"; exit 1
fi
echo "== 완료. 매매 봇은 그대로 ($(systemctl is-active rlsc-autotrade)). 백업 $BK"
echo "   되돌리기: sudo bash $0 --rollback $BK"
