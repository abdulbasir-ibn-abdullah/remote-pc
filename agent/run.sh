#!/usr/bin/env bash
cd "$(dirname "$0")"
if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  echo ".env yaratildi. RELAY_URL va AGENT_TOKEN ni to'ldiring, so'ng qayta ishga tushiring."
  exit 1
fi
exec python3 agent.py
