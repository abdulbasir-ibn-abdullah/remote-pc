#!/usr/bin/env bash
# VPS da bir marta ishga tushiriladi:  sudo ./install_service.sh
# Backend ni systemd xizmati sifatida o'rnatadi (avto-start, avto-restart).
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "sudo bilan ishga tushiring: sudo ./install_service.sh"; exit 1
fi

DIR="$(cd "$(dirname "$0")" && pwd)"
RUN_USER="${SUDO_USER:-$(stat -c %U "$DIR")}"
if [ "$RUN_USER" = "root" ]; then
  echo "Xizmat root sifatida ishlamasligi kerak. Oddiy foydalanuvchidan sudo bilan ishga tushiring."; exit 1
fi
SERVICE=remote-fix-relay

echo ">> Tayyorlash (venv, bog'liqliklar, .env) foydalanuvchi: $RUN_USER"
sudo -u "$RUN_USER" env SETUP_ONLY=1 bash "$DIR/run.sh"

echo ">> /etc/systemd/system/$SERVICE.service yozilmoqda"
cat > /etc/systemd/system/$SERVICE.service <<UNIT
[Unit]
Description=Remote-Fix Relay (FastAPI)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$DIR
ExecStart=$DIR/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8765 --proxy-headers --forwarded-allow-ips=127.0.0.1
Restart=always
RestartSec=3

# Xavfsizlik cheklovlari
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=$DIR
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictSUIDSGID=true

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now $SERVICE
sleep 2
systemctl --no-pager --lines=5 status $SERVICE || true

echo
echo "Tekshirish : curl -s http://127.0.0.1:8765/health"
echo "Loglar     : journalctl -u $SERVICE -f   |   $DIR/relay.log"
echo "To'xtatish : sudo systemctl stop $SERVICE   (kill switch)"
echo "Qayta yoq. : sudo systemctl restart $SERVICE (barcha tokenlar tozalanadi)"
