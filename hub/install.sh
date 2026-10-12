#!/bin/bash
# [root] Trade Hub 설치·갱신. 봇·기존 대시보드·기존 tailscale serve(443, 8443)는 건드리지 않는다.
# 사용(ryan 이 서버에서): sudo bash /home/ryan/quant-deploy/hub/install.sh
# 되돌리기: sudo tailscale serve --https=10000 off && sudo systemctl disable --now trade-hub
set -eu
D=$(dirname "$(readlink -f "$0")")
install -D -m 644 "$D/hub.py" /opt/trade-hub/hub.py
install -m 644 "$D/trade-hub.service" /etc/systemd/system/trade-hub.service
systemctl daemon-reload
systemctl enable trade-hub
systemctl restart trade-hub
tailscale serve --bg --https=10000 http://127.0.0.1:8899
sleep 2
systemctl is-active trade-hub
curl -s -m 40 -o /dev/null -w "stock %{http_code}\n" http://127.0.0.1:8899/api/stock
curl -s -m 15 -o /dev/null -w "coin %{http_code}\n" http://127.0.0.1:8899/api/coin
tailscale serve status
echo "== 완료: https://$(tailscale status --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))'):10000 =="
