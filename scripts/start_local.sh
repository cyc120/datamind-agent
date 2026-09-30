#!/usr/bin/env bash
# Start the local SQL Agent without resetting existing demo data.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
if ! .venv/bin/python -c 'import fastapi,uvicorn,psycopg2,sqlglot' 2>/dev/null; then
  .venv/bin/python -m pip install -r requirements-sql.txt
fi

docker compose -f deploy/compose.yaml up -d postgres
for _ in {1..30}; do
  if docker exec datamind-postgres pg_isready -U postgres -d datamind_meta -q >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
docker exec datamind-postgres pg_isready -U postgres -d datamind_meta -q

if [[ "$(docker exec datamind-postgres psql -U postgres -d postgres -tAc "SELECT count(*) FROM pg_database WHERE datname='chinook'")" == "0" ]]; then
  docker cp db/chinook/Chinook_PostgreSql.sql datamind-postgres:/tmp/chinook.sql
  docker exec datamind-postgres psql -U postgres -q -f /tmp/chinook.sql
  docker exec datamind-postgres psql -U postgres -d chinook -q -c 'GRANT CONNECT ON DATABASE chinook TO readonly_user, datamind_app; GRANT USAGE ON SCHEMA public TO readonly_user, datamind_app; GRANT SELECT ON ALL TABLES IN SCHEMA public TO readonly_user, datamind_app;'
fi

if [[ "$(docker exec datamind-postgres psql -U postgres -d demo_retail -tAc 'SELECT count(*) FROM orders')" == "0" ]]; then
  .venv/bin/python db/seed_demo_retail.py
fi
if [[ "$(docker exec datamind-postgres psql -U postgres -d datamind_meta -tAc 'SELECT count(*) FROM schema_snapshots')" == "0" ]]; then
  .venv/bin/python db/build_catalog.py
fi
if [[ "$(docker exec datamind-postgres psql -U postgres -d datamind_meta -tAc 'SELECT count(*) FROM evaluation_cases')" == "0" ]]; then
  .venv/bin/python db/seed_eval_cases.py
fi

if curl -fsS --max-time 2 http://127.0.0.1:8000/health 2>/dev/null | grep -q '"agent":"sql-only"'; then
  echo 'DataMind Agent 已运行：http://127.0.0.1:8000/'
  exit 0
fi

echo '启动 DataMind Agent：http://127.0.0.1:8000/'
exec .venv/bin/uvicorn datamind.api:app --host 127.0.0.1 --port 8000
