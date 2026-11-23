#!/bin/sh
set -eu
mkdir -p backups
stamp=$(date -u +%Y%m%dT%H%M%SZ)
user=${POSTGRES_USER:-anon}
db=${POSTGRES_DB:-anonchat}
docker compose exec -T postgres pg_dump -U "$user" "$db" > "backups/anonchat-${stamp}.sql"
echo "saved backups/anonchat-${stamp}.sql"
