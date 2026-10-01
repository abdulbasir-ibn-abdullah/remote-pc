#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q -r requirements.txt

if [ ! -f .env ]; then
  A=$(python3 -c "import secrets;print(secrets.token_urlsafe(32))")
  T=$(python3 -c "import secrets;print(secrets.token_urlsafe(32))")
  printf "ADMIN_KEY=%s\nAGENT_TOKEN=%s\n" "$A" "$T" > .env
  chmod 600 .env
  echo "=== .env yaratildi ==="
  echo "ADMIN_KEY  (faqat sizda qoladi): $A"
  echo "AGENT_TOKEN (PC dagi agent/.env ga qo'yiladi): $T"
  echo "======================"
fi

exec uvicorn main:app --host 127.0.0.1 --port "${PORT:-8765}" \
  --proxy-headers --forwarded-allow-ips=127.0.0.1
