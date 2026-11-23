#!/bin/sh
set -eu
if [ $# -ne 1 ]; then
  echo "usage: ./scripts/restore.sh backups/anonchat-YYYYMMDDTHHMMSSZ.sql" >&2
  exit 1
fi
user=${POSTGRES_USER:-anon}
db=${POSTGRES_DB:-anonchat}
echo "Stop the bot and worker before restoring, or writes during restore will be lost."
docker compose stop bot worker api
docker compose exec -T postgres psql -U "$user" -d "$db" < "$1"
docker compose start bot worker api
echo "restored $1"
