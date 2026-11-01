#!/usr/bin/env bash
# Local development: SQLite + local storage + inline worker. Usage: scripts/dev.sh
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || cp .env.example .env
python apps/web/build.py
cd apps/api && alembic upgrade head && cd ../..
exec uvicorn visentra_api.main:app --host 127.0.0.1 --port "${PORT:-8000}"
