#!/bin/sh
set -eu
echo "== compose ps =="
docker compose ps
echo "== health =="
docker compose exec -T api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3).read().decode())" || true
echo "== recent logs =="
docker compose logs --tail=80 bot worker api
