#!/usr/bin/env bash
# ============================================================
# reset_demo.sh — 一键重置全部基础数据库（可重复执行）
# 用法: bash scripts/reset_demo.sh
# ============================================================
set -e
cd "$(dirname "$0")/.."

echo "=== [1/7] 重启容器（清空数据卷） ==="
docker compose -f deploy/compose.yaml down -v
docker compose -f deploy/compose.yaml up -d

echo "=== [2/7] 等待 PostgreSQL 就绪并完成 init 脚本（角色/元数据/演示库） ==="
until docker exec datamind-postgres pg_isready -U postgres -d datamind_meta -q 2>/dev/null; do
  sleep 2
done
until docker exec datamind-postgres psql -U postgres -d demo_retail -c '\dt' >/dev/null 2>&1; do
  sleep 2
done

echo "=== [3/7] 加载 Chinook（SQL 已随仓库附带，来源 lerocha/chinook-database，MIT） ==="
docker cp "db/chinook/Chinook_PostgreSql.sql" datamind-postgres:/tmp/chinook.sql
MSYS_NO_PATHCONV=1 docker exec datamind-postgres psql -U postgres -q -f /tmp/chinook.sql
docker exec datamind-postgres psql -U postgres -d chinook -q -c "
GRANT CONNECT ON DATABASE chinook TO readonly_user, datamind_app;
GRANT USAGE ON SCHEMA public TO readonly_user, datamind_app;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO readonly_user, datamind_app;"

echo "=== [4/7] 生成零售演示数据 ==="
py -3.13 db/seed_demo_retail.py

echo "=== [5/7] 元数据登记（SchemaSnapshot/业务词表/指标/别名） ==="
py -3.13 db/build_catalog.py

echo "=== [6/7] 评测集种子 ==="
py -3.13 db/seed_eval_cases.py

echo "=== [7/7] 知识库摄取（解析→切片→BGE→Qdrant） ==="
py -3.13 db/migrate.py
export HF_ENDPOINT=https://hf-mirror.com
py -3.13 ingestion/ingest.py --reset
py -3.13 db/seed_industrial.py
py -3.13 db/build_industrial_catalog.py

echo "=== 完成。运行 bash scripts/verify.py 做端到端验证 ==="
