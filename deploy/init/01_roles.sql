-- ============================================================
-- 01_roles.sql — 角色与权限（说明书 §2.2 只读与最小权限）
-- 说明: 在 datamind_meta 库中以 postgres 身份执行
--   datamind_app   : 应用读写账号（Catalog/Trace/Chunk 写入）
--   readonly_user  : 问数只读账号，对业务库只有 SELECT
-- ============================================================

-- 应用账号（可登录，读写元数据库）
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'datamind_app') THEN
    CREATE ROLE datamind_app LOGIN PASSWORD 'datamind_app_dev';
  END IF;
END
$$;

-- 问数只读账号（可登录，仅 SELECT）
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'readonly_user') THEN
    CREATE ROLE readonly_user LOGIN PASSWORD 'readonly_dev';
  END IF;
END
$$;

COMMENT ON ROLE datamind_app IS 'DataMind Agent 应用读写账号';
COMMENT ON ROLE readonly_user IS 'NL2SQL 问数只读账号，禁止任何写操作';
