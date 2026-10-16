#!/bin/bash
# RLS-C R2 배포: 새 코드(위험 감시 R2) + 6몫(UTC 00·04·08·12·16·20) + 종목당 증거금 비율 (기본 4%, 2026-10-16).
# 몫 구성이 바뀌면 열린 몫이 없을 때만 진행된다 (3) 점검이 막는다).
# 서버에서:  sudo bash rlsc_r2_deploy.sh            # 비율 자동 = 지금 잔고로 BTC 최소 주문을 맞추는 값 + 5% 여유 (4% 미만이면 4%)
#            sudo bash rlsc_r2_deploy.sh 0.035      # 비율 직접 지정 (0 초과 0.05 이하)
#            sudo bash rlsc_r2_deploy.sh --rollback <백업 폴더>
#            sudo env RLSC_TRANCHES=0,8,16 RLSC_COINS=ETH-USDT-SWAP bash rlsc_r2_deploy.sh 0.04   # 소액(≈7 USDT): ETH 만 3몫, 종목당 4%
# 순서: 서비스 정보 확인 → 새 코드 테스트(임시 폴더) → 백업 → 새 코드로 조회만 하는 점검(--preflight, 임시 폴더·DB 사본)
#       → 비율 결정 → HH:15~HH:55 까지 대기 → 정지 → 교체 → systemd drop-in → 시작 → 확인 (실패하면 자동 롤백).
# 주문·청산·계좌 설정 변경은 하지 않는다. 열린 포지션의 손절은 거래소에 남아 있다 (정지 시간 동안 위험 감시는 없음).
set -euo pipefail
TR=${RLSC_TRANCHES:-0,4,8,12,16,20}    # 몫 경계 (UTC 시)
COINS=${RLSC_COINS:-}                  # 비우면 BTC·ETH. 예: ETH-USDT-SWAP
NT=$(awk -F, '{print NF}' <<<"$TR")
FIRST=$(cut -d- -f1 <<<"${COINS:-BTC}")  # 자동 비율을 맞출 종목 (첫 종목)
CENV="RLSC_TRANCHES=$TR${COINS:+ RLSC_COINS=$COINS}"
SVC=rlsc-autotrade
DASH=rlsc-dashboard
HERE=$(cd "$(dirname "$0")" && pwd)
DROP=rlsc-r2.conf
[ "$(id -u)" = 0 ] || { echo "sudo 로 실행하세요"; exit 1; }

prop() { systemctl show "$1" -p "$2" --value; }
SCRIPT=$(prop $SVC ExecStart | grep -o '[^ ]*autotrade\.py' | head -1 || true)
WD=$(prop $SVC WorkingDirectory)
WD=${WD:-$(dirname "${SCRIPT:-/nonexistent/x}")}
RUSER=$(prop $SVC User); RUSER=${RUSER:-root}
PY=$(prop $SVC ExecStart | sed -n 's/.*path=\([^ ;]*\).*/\1/p' | head -1)
ENVS=$(prop $SVC Environment)
[ -f "$WD/autotrade.py" ] || { echo "봇 폴더를 못 찾음 (WorkingDirectory/ExecStart: '$WD')"; exit 1; }
[ -x "$PY" ] || PY=$(command -v python3)
as_user() { sudo -u "$RUSER" env $ENVS "$@"; }

rollback() {
  local BK=$1
  echo "== 롤백: $BK"
  systemctl stop $SVC $DASH || true
  (cd "$WD" && as_user "$PY" autotrade.py --export-legacy) || echo "!! 상태 되쓰기 실패 — 미확정 주문이 있으면 확정된 뒤 다시"
  cp -a "$BK"/*.py "$WD"/
  rm -f /etc/systemd/system/$SVC.service.d/$DROP /etc/systemd/system/$DASH.service.d/$DROP
  systemctl daemon-reload
  systemctl start $SVC $DASH
  sleep 5; systemctl is-active $SVC $DASH || true
  echo "== 롤백 끝 (옛 코드, 원래 환경)"
}
if [ "${1:-}" = "--rollback" ]; then rollback "${2:?백업 폴더}"; exit 0; fi

MODE=$(tr ' ' '\n' <<<"$ENVS" | sed -n 's/^MODE=//p' | tail -1)
[ -n "$MODE" ] || MODE=$(sed -n 's/^MODE=\([a-z]*\).*/\1/p' "$WD/.env" 2>/dev/null | tail -1)
MODE=${MODE:-dry}
echo "== 서비스: $SVC  사용자 $RUSER  폴더 $WD  python $PY  MODE=$MODE"
echo "   현재 환경: $(tr ' ' '\n' <<<"$ENVS" | grep -E '^(MODE|RLSC_|SCHEDULE)' | tr '\n' ' ')"
echo "   대시보드: $(prop $DASH ActiveState) / $(prop $DASH Environment | tr ' ' '\n' | grep -E '^(SCHEDULE|RLSC_)' | tr '\n' ' ')"
[ "$(systemctl is-active $SVC)" = active ] || { echo "$SVC 가 실행 중이 아님 → 주문 주체를 확인한 뒤 수동으로"; exit 1; }

echo "== 1) 새 코드 테스트 (임시 폴더, 운용 DB·.env 안 씀)"
T=$(mktemp -d); cp "$HERE"/src/*.py "$T"/; chown -R "$RUSER" "$T"
(cd "$T" && sudo -u "$RUSER" "$PY" test_bot.py >"$T/test.log" 2>&1) || { echo "테스트 실패 → 중단 (서비스 그대로)"; grep -E "Error|assert" "$T/test.log" | tail -5; exit 1; }
echo "   $(grep -c '^ok' "$T/test.log") 개 통과"

echo "== 2) 백업"
TS=$(date -u +%Y%m%dT%H%M%S)
BK="$(dirname "$WD")/rlsc_backup_$TS"
mkdir -p "$BK"; cp -a "$WD"/*.py "$BK"/
for db in "$WD"/rlsc_*.db; do [ -f "$db" ] && "$PY" -c "import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d)" "$db" "$BK/$(basename "$db")"; done
for s in $SVC $DASH; do systemctl cat $s > "$BK/$s.unit.txt" 2>/dev/null || true; done
"$PY" -m pip freeze > "$BK/pip.txt" 2>/dev/null || true
echo "   $BK"

echo "== 3) 새 코드로 점검 (조회만: 권한·포지션 모드·잔고·최소 자금·모르는 포지션)"
cp "$BK/rlsc_$MODE.db" "$T/" 2>/dev/null || true
ln -sf "$WD/.env" "$T/.env"; chown -h "$RUSER" "$T/.env"; chown -R "$RUSER" "$T"
M=${1:-auto}
pf() { (cd "$T" && as_user $CENV RLSC_MARGIN="$1" "$PY" autotrade.py --preflight 2>&1 | grep '^\[' ) || true; }
PF=$(pf "$([ "$M" = auto ] && echo 0.05 || echo "$M")")   # 자동이면 상한 5% 로 먼저 본다 (기본 비율로 보면 소액 계좌는 최소 자금에서 막힘)
echo "$PF"
grep -q "점검 통과" <<<"$PF" || { echo "점검 실패 → 중단 (서비스 그대로)"; exit 1; }
if [ "$M" = auto ]; then
  REQ=$(grep "$FIRST 최소 주문" <<<"$PF" | grep -o '≈ [0-9.]*%' | tail -1 | tr -dc '0-9.')
  [ -n "$REQ" ] || { echo "$FIRST 필요 비율을 읽지 못함 → 비율을 직접 지정해 다시: sudo bash $0 0.03"; exit 1; }
  M=$("$PY" -c "import math,sys; r=float(sys.argv[1])/100*1.05; m=max(0.04, math.ceil(r*1000)/1000); print(m if m<=0.05 else 'ETH')" "$REQ")
  if [ "$M" = ETH ]; then echo "   잔고가 작아 5% 안에서는 $FIRST 불가 → 4% 유지 (나머지 종목만 거래)"; M=0.04; fi
fi
"$PY" -c "import sys; m=float(sys.argv[1]); assert 0<m<=0.05" "$M" || { echo "비율 $M 은 0 초과 0.05 이하"; exit 1; }
PF=$(pf "$M"); grep -q "점검 통과" <<<"$PF" || { echo "$PF"; echo "비율 $M 로는 점검 실패 (최소 주문 미달 등) → 중단 (서비스 그대로)"; exit 1; }
HASH=$(cd "$T" && "$PY" -c "import rls,sys; print(rls.config_hash(rls.strategy_config(tuple(int(x) for x in sys.argv[2].split(',')), insts=tuple(sys.argv[3].split(',')) if sys.argv[3] else rls.COINS, margin=float(sys.argv[1]))))" "$M" "$TR" "$COINS")
echo "== 적용 값: $CENV  RLSC_MARGIN=$M (종목당 $(awk "BEGIN{print $M*100}")%, 몫당 $(awk "BEGIN{printf \"%.3f\", $M*100/$NT}")%)  설정 해시 $HASH"
ls "$HERE/reports/rlsc_runs" | grep -q "" && grep -l "\"strategy_hash\": \"$HASH\"" "$HERE"/reports/rlsc_runs/*/manifest.json 2>/dev/null | head -1 | xargs -r dirname | xargs -r basename | sed 's/^/   같은 설정 백테스트: /' || true

echo "== 4) 몫 작업(HH:02)·점검(HH:07)과 겹치지 않게 HH:15~HH:55 대기"
while :; do m=$((10#$(date -u +%M))); [ $m -ge 15 ] && [ $m -le 55 ] && break; sleep 20; done

echo "== 5) 정지 → 교체 → 설정 → 시작"
systemctl stop $SVC $DASH
trap 'echo "!! 오류 → 자동 롤백"; rollback "$BK"' ERR
cp "$HERE"/src/*.py "$WD"/
mkdir -p "$WD/reports"; cp -r "$HERE/reports/rlsc_runs" "$WD/reports/"
chown -R --reference="$BK/autotrade.py" "$WD"/*.py "$WD/reports"
for s in $SVC $DASH; do mkdir -p /etc/systemd/system/$s.service.d; done
printf '[Service]\nEnvironment=%s RLSC_MARGIN=%s\n' "$CENV" "$M" > /etc/systemd/system/$SVC.service.d/$DROP
printf '[Service]\nEnvironment=%s RLSC_MARGIN=%s SCHEDULE=0\n' "$CENV" "$M" > /etc/systemd/system/$DASH.service.d/$DROP
systemctl daemon-reload
systemctl start $SVC $DASH

echo "== 6) 확인 (최대 90초)"
ok=""
for _ in $(seq 18); do
  sleep 5
  [ "$(systemctl is-active $SVC)" = active ] || break
  if tail -n 200 "$WD/autotrade.log" | grep -q "몫 ${NT}개.*설정 $HASH"; then ok=1; break; fi
done
trap - ERR
if [ -z "$ok" ]; then
  echo "!! 시작 확인 실패:"; tail -n 20 "$WD/autotrade.log" | grep -v "OKX_API" || true
  rollback "$BK"; exit 1
fi
tail -n 200 "$WD/autotrade.log" | grep -E "몫 ${NT}개|시작: 점검|ALERT" | tail -5
echo "   환경: $(prop $SVC Environment)"
echo "== 완료. 백업 $BK"
echo "   되돌리기: sudo bash $0 --rollback $BK"
echo "   대시보드 상단에 '위험 상태: 정상'과 같은 설정 백테스트가 보이는지 확인하세요."
