#!/usr/bin/env bash
# Start the local SQL + document Agent without resetting existing demo data.
set -euo pipefail
cd "$(dirname "$0")/.."

rag_enabled=1
background=0
for argument in "$@"; do
case "$argument" in
  --sql-only) rag_enabled=0 ;;
  --background) background=1 ;;
  *) echo '用法：bash scripts/start_local.sh [--sql-only] [--background]' >&2; exit 2 ;;
esac
done
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
if ! .venv/bin/python -c 'import fastapi,uvicorn,psycopg2,sqlglot' 2>/dev/null; then
  .venv/bin/python -m pip install -r requirements-sql.txt
fi
if [[ "$rag_enabled" == 1 ]] && ! .venv/bin/python -c 'import pymupdf,fastembed,qdrant_client,rapidocr; from importlib.metadata import version; assert version("qdrant-client").startswith("1.12."); assert version("rapidocr") == "3.9.2"' 2>/dev/null; then
  .venv/bin/python -m pip install -r requirements-ocr.txt
fi

docker compose -f deploy/compose.yaml up -d postgres
for _ in {1..30}; do
  if docker exec datamind-postgres pg_isready -U postgres -d datamind_meta -q >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
docker exec datamind-postgres pg_isready -U postgres -d datamind_meta -q
docker exec datamind-postgres psql -U postgres -d datamind_meta -q -v ON_ERROR_STOP=1 -c 'CREATE SCHEMA IF NOT EXISTS demo_industrial AUTHORIZATION datamind_app; GRANT USAGE ON SCHEMA demo_industrial TO readonly_user;'
.venv/bin/python db/migrate.py

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

if [[ "$rag_enabled" == 1 ]]; then
  if [[ ! -f .cache/ocr_models/ch_PP-OCRv5_rec_server.onnx ]]; then
    echo '表头模型尚未安装：运行 .venv/bin/python scripts/setup_ocr_models.py；缺失表头将保留为解析缺口。'
  fi
  formula_python="${DATAMIND_FORMULA_PYTHON:-.venv-formula/bin/python}"
  if [[ ! -x "$formula_python" || ! -f .cache/formula_models/pp_formulanet_plus_m.onnx ]]; then
    echo '公式识别尚未安装：运行 .venv/bin/python scripts/setup_formula.py --python /path/to/python3.12；未识别图片将保留解析缺口。'
  fi
  docker compose -f deploy/compose.yaml up -d qdrant
  for _ in {1..30}; do
    if curl -fsS --noproxy '*' --max-time 2 http://127.0.0.1:6333/readyz >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
  curl -fsS --noproxy '*' --max-time 2 http://127.0.0.1:6333/readyz >/dev/null
  # Idempotent: unchanged documents are skipped; a missing vector collection is rebuilt.
  .venv/bin/python ingestion/ingest.py
  .venv/bin/python db/seed_industrial.py
  .venv/bin/python db/build_industrial_catalog.py
fi

# 上传队列（/v1/uploads）不再由 GET 轮询推进（审查 5.1）：任务只由独立 worker 领取。所以
# 启动 API 的同时必须启动 worker，并在停止路径里一并停掉，否则任务会永远停在 queued。
upload_worker_log='.cache/api/upload_worker.log'
upload_worker_pid=''
start_upload_worker() {
  mkdir -p .cache/api
  .venv/bin/python -m ingestion.upload_worker --watch \
    --interval "${DATAMIND_UPLOAD_WORKER_INTERVAL:-2}" >>"$upload_worker_log" 2>&1 &
  upload_worker_pid=$!
  echo "上传 worker 已启动（PID $upload_worker_pid，日志 $upload_worker_log）"
}
stop_upload_worker() {
  if [[ -n "$upload_worker_pid" ]] && kill -0 "$upload_worker_pid" 2>/dev/null; then
    # SIGTERM 不会打断已领取的解析：worker 跑完当前波次后退出。
    kill -TERM "$upload_worker_pid" 2>/dev/null || true
    wait "$upload_worker_pid" 2>/dev/null || true
  fi
}

if curl -fsS --max-time 2 http://127.0.0.1:8000/health 2>/dev/null | grep -Eq '"agent":"(sql-only|sql\+rag)"'; then
  echo 'DataMind Agent 已运行：http://127.0.0.1:8000/（代码更新后请重启原服务进程）'
  if [[ "$rag_enabled" == 1 ]]; then
    echo '若上传任务停在 queued，说明后台处理器未运行；请另开终端执行：'
    echo '  .venv/bin/python -m ingestion.upload_worker --watch'
  fi
  exit 0
fi

echo '启动 DataMind Agent：http://127.0.0.1:8000/'
if [[ "$background" == 1 ]]; then
  background_args=()
  if [[ "$rag_enabled" == 0 ]]; then
    background_args=(--no-upload-worker)
  fi
  # 后台模式由 start_api_background.py 分离启动 API 与 worker（本脚本退出后仍在运行）。
  exec .venv/bin/python scripts/start_api_background.py "${background_args[@]}"
fi

if [[ "$rag_enabled" == 1 ]]; then
  start_upload_worker
fi
trap stop_upload_worker EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

.venv/bin/uvicorn datamind.api:app --host 127.0.0.1 --port 8000 --no-proxy-headers &
api_pid=$!
api_status=0
wait "$api_pid" || api_status=$?
exit "$api_status"
