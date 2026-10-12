-- ============================================================
-- 02_metadata.sql — 元数据底座（DataMind Agent 技术说明书 §8.1）
-- 统一元数据对象: DataSource / SchemaSnapshot / BusinessMetric /
--   EntityAlias / DocumentAsset / DocumentBlock / Chunk /
--   EvaluationCase / Run / TraceEvent / Evidence / Claim
-- 在 datamind_meta 库执行
-- ============================================================

-- ---------- 0. 结构化数据源目录 ----------
CREATE TABLE IF NOT EXISTS data_sources (
    source_id       VARCHAR(64) PRIMARY KEY,
    name            VARCHAR(128) NOT NULL,
    db_type         VARCHAR(32)  NOT NULL,           -- postgresql / sqlite / ...
    host            VARCHAR(128),
    port            INT,
    database_name   VARCHAR(128),
    secret_ref      VARCHAR(256),                    -- 凭证引用（不存明文）
    schema_allowlist TEXT[] NOT NULL DEFAULT '{}',   -- 允许访问的 schema
    table_allowlist TEXT[] NOT NULL DEFAULT '{}',   -- 允许访问的表（空=全部登记表）
    access_mode     VARCHAR(16) NOT NULL DEFAULT 'readonly',
    environment     VARCHAR(32) NOT NULL DEFAULT 'demo',
    description     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- 1. Schema 快照 ----------
CREATE TABLE IF NOT EXISTS schema_snapshots (
    snapshot_id     VARCHAR(64) PRIMARY KEY,
    source_id       VARCHAR(64) NOT NULL REFERENCES data_sources(source_id),
    catalog_version INT NOT NULL,
    introspected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    table_count     INT,
    column_count    INT,
    notes           TEXT,
    UNIQUE (source_id, catalog_version)
);

CREATE TABLE IF NOT EXISTS schema_tables (
    snapshot_id     VARCHAR(64) NOT NULL REFERENCES schema_snapshots(snapshot_id),
    table_name      VARCHAR(128) NOT NULL,
    schema_name     VARCHAR(64)  NOT NULL DEFAULT 'public',
    table_comment   TEXT,
    row_estimate    BIGINT,
    business_name   VARCHAR(128),                    -- 中文名
    aliases         TEXT[] NOT NULL DEFAULT '{}',    -- 别名/黑话
    PRIMARY KEY (snapshot_id, schema_name, table_name)
);

CREATE TABLE IF NOT EXISTS schema_columns (
    snapshot_id     VARCHAR(64) NOT NULL REFERENCES schema_snapshots(snapshot_id),
    schema_name     VARCHAR(64)  NOT NULL,
    table_name      VARCHAR(128) NOT NULL,
    column_name     VARCHAR(128) NOT NULL,
    ordinal         INT NOT NULL DEFAULT 0,
    data_type       VARCHAR(64) NOT NULL,
    is_nullable     BOOLEAN NOT NULL DEFAULT TRUE,
    is_primary_key  BOOLEAN NOT NULL DEFAULT FALSE,
    column_comment  TEXT,
    business_name   VARCHAR(128),
    aliases         TEXT[] NOT NULL DEFAULT '{}',
    unit            VARCHAR(32),                     -- 单位: CNY / 个 / %
    semantic_role   VARCHAR(32),                     -- metric / dimension / time / id / text
    enum_map        JSONB,                           -- 枚举值映射 {"0":"未支付",...}
    sample_values   JSONB,
    sensitivity     VARCHAR(16) NOT NULL DEFAULT 'public',  -- public/internal/restricted
    freshness       TIMESTAMPTZ,
    PRIMARY KEY (snapshot_id, schema_name, table_name, column_name),
    FOREIGN KEY (snapshot_id, schema_name, table_name)
        REFERENCES schema_tables(snapshot_id, schema_name, table_name) ON DELETE CASCADE
);

-- 主外键 / 业务关系图（Join Graph 边）
CREATE TABLE IF NOT EXISTS schema_relations (
    relation_id     VARCHAR(64) PRIMARY KEY,
    snapshot_id     VARCHAR(64) NOT NULL REFERENCES schema_snapshots(snapshot_id),
    from_table      VARCHAR(128) NOT NULL,
    from_columns    TEXT[] NOT NULL,
    to_table        VARCHAR(128) NOT NULL,
    to_columns      TEXT[] NOT NULL,
    cardinality     VARCHAR(16) NOT NULL DEFAULT 'n:1',  -- 1:1 / 1:n / n:1 / n:m
    relation_kind   VARCHAR(16) NOT NULL DEFAULT 'foreign_key', -- foreign_key/business
    ambiguity_penalty  NUMERIC(4,2) NOT NULL DEFAULT 0,
    fanout_penalty     NUMERIC(4,2) NOT NULL DEFAULT 0,
    privacy_penalty    NUMERIC(4,2) NOT NULL DEFAULT 0,
    description     TEXT,
    approved        BOOLEAN NOT NULL DEFAULT FALSE,      -- 未经审核的 JOIN 不可用
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- 2. 业务指标口径 ----------
CREATE TABLE IF NOT EXISTS business_metrics (
    metric_id       VARCHAR(64) PRIMARY KEY,
    name            VARCHAR(128) NOT NULL,
    aliases         TEXT[] NOT NULL DEFAULT '{}',
    definition      TEXT NOT NULL,                   -- 计算口径（自然语言 + 表达式）
    expression_sql  TEXT,                            -- 参考 SQL 表达式
    unit            VARCHAR(32),
    grain           VARCHAR(64),                     -- 粒度: 日/月/订单
    source_id       VARCHAR(64) REFERENCES data_sources(source_id),
    owner           VARCHAR(64),
    status          VARCHAR(16) NOT NULL DEFAULT 'active',  -- active/deprecated
    valid_from      DATE,
    valid_to        DATE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- 3. 跨源实体别名 ----------
CREATE TABLE IF NOT EXISTS entity_aliases (
    alias_id        VARCHAR(64) PRIMARY KEY,
    canonical_id    VARCHAR(128) NOT NULL,           -- 规范实体 ID
    canonical_name  VARCHAR(128) NOT NULL,
    entity_type     VARCHAR(32) NOT NULL,            -- product/customer/region/...
    alias           VARCHAR(128) NOT NULL,           -- 别名（如“帝都”→北京市）
    source          VARCHAR(64),                     -- 来源: seed/harvest/manual
    match_method    VARCHAR(32) NOT NULL DEFAULT 'exact',
    score           NUMERIC(4,3),
    approved        BOOLEAN NOT NULL DEFAULT TRUE,
    approved_by     VARCHAR(64),
    valid_from      DATE,
    valid_to        DATE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_entity_aliases_alias ON entity_aliases(alias);
CREATE INDEX IF NOT EXISTS idx_entity_aliases_canonical ON entity_aliases(canonical_id);

-- ---------- 4. 文档资产 ----------
CREATE TABLE IF NOT EXISTS document_assets (
    document_id     VARCHAR(64) PRIMARY KEY,
    title           VARCHAR(256) NOT NULL,
    collection      VARCHAR(64) NOT NULL DEFAULT 'default',  -- 知识库集合/租户
    file_type       VARCHAR(16) NOT NULL,            -- pdf/office/image/csv
    origin          VARCHAR(32) NOT NULL DEFAULT 'curated',  -- curated(自建)/ohrbench/scan
    sha256          CHAR(64) NOT NULL,
    object_uri      VARCHAR(512),                    -- MinIO/本地文件引用
    page_count      INT,
    language        VARCHAR(16),
    acl             TEXT[] NOT NULL DEFAULT '{public}',
    quality_score   NUMERIC(4,3),                    -- 质量分 Q
    quality_detail  JSONB,                           -- 各维度诊断
    parser_version  VARCHAR(64),
    doc_version     INT NOT NULL DEFAULT 1,
    status          VARCHAR(16) NOT NULL DEFAULT 'pending',  -- pending/parsed/indexed/failed
    needs_review    BOOLEAN NOT NULL DEFAULT FALSE,
    uploaded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (collection, sha256)
);

-- ---------- 5. 文档结构块 ----------
CREATE TABLE IF NOT EXISTS document_blocks (
    block_id        VARCHAR(64) PRIMARY KEY,
    document_id     VARCHAR(64) NOT NULL REFERENCES document_assets(document_id) ON DELETE CASCADE,
    page_no         INT NOT NULL,
    bbox            JSONB,                           -- [x0,y0,x1,y1]
    block_type      VARCHAR(16) NOT NULL,            -- title/paragraph/table/figure/caption/formula/header/footer
    text            TEXT,
    table_cells     JSONB,                           -- 二维表结构
    formula_latex   TEXT,
    reading_order   INT,
    parent_heading  VARCHAR(64),                     -- 所属标题块 ID
    heading_level   INT,                             -- 标题层级 1..n
    ocr_confidence  NUMERIC(4,3),
    parser_version  VARCHAR(64),
    content_hash    CHAR(64) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_blocks_doc ON document_blocks(document_id, page_no);

-- ---------- 6. 检索切片 ----------
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id        VARCHAR(64) PRIMARY KEY,
    document_id     VARCHAR(64) NOT NULL REFERENCES document_assets(document_id) ON DELETE CASCADE,
    document_version INT NOT NULL DEFAULT 1,
    chunk_index     INT NOT NULL,
    chunk_type      VARCHAR(16) NOT NULL DEFAULT 'text',   -- text/table/formula/figure
    section_path    TEXT[] NOT NULL DEFAULT '{}',    -- 标题路径
    page_start      INT,
    page_end        INT,
    block_ids       TEXT[] NOT NULL DEFAULT '{}',
    text_for_embedding TEXT NOT NULL,
    exact_text      TEXT,
    table_json      JSONB,
    image_ref       VARCHAR(512),
    token_count     INT,
    quality_score   NUMERIC(4,3),
    acl             TEXT[] NOT NULL DEFAULT '{public}',
    embedding_model VARCHAR(64) NOT NULL,            -- 模型版本绑定
    vector_id       VARCHAR(64),                     -- Qdrant point id
    content_hash    CHAR(64) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(document_id);

-- ---------- 7. 评测用例 ----------
CREATE TABLE IF NOT EXISTS evaluation_cases (
    case_id         VARCHAR(64) PRIMARY KEY,
    suite           VARCHAR(32) NOT NULL,            -- nl2sql / rag / cross_source / robustness
    category        VARCHAR(32),                     -- 单表过滤/JOIN/聚合/表格/公式/澄清...
    difficulty      VARCHAR(16) NOT NULL DEFAULT 'easy',
    question        TEXT NOT NULL,
    expected_route  VARCHAR(32),                     -- sql / rag / sql_to_rag / clarify
    gold_sql        TEXT,
    gold_answer_claims JSONB,
    required_evidence JSONB,                         -- 必需证据 ID / 页码 / 片段
    clarification_label VARCHAR(16),                 -- none/needed/ambiguous
    failure_category VARCHAR(32),
    data_version    VARCHAR(64),
    reviewed_by     VARCHAR(64),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_eval_suite ON evaluation_cases(suite, category);

-- ---------- 8. 运行与 Trace ----------
CREATE TABLE IF NOT EXISTS runs (
    run_id          VARCHAR(64) PRIMARY KEY,
    conversation_id VARCHAR(64),
    question        TEXT NOT NULL,
    status          VARCHAR(32) NOT NULL,            -- PLANNED/EXECUTING/VERIFYING/COMPLETED/...
    plan            JSONB,                           -- TaskPlan DAG
    answer          TEXT,
    confidence_level VARCHAR(16),                    -- high/medium/low/insufficient
    versions        JSONB,                           -- manifest: schema/prompt/model/kb 版本
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at        TIMESTAMPTZ
);
-- 应用层按 conversation_id 过滤 runs 来还原会话逐轮问答（question=用户轮、answer=助手轮）。
-- runs 由 postgres 拥有，本文件正以 postgres 身份执行，所以这个索引只能放在这里：
-- db/migrations/*.sql 以 datamind_app 身份执行，对 runs 建索引会报 must be owner of table runs。
CREATE INDEX IF NOT EXISTS runs_conversation_idx ON runs(conversation_id);

CREATE TABLE IF NOT EXISTS trace_events (
    event_id        VARCHAR(64) PRIMARY KEY,
    run_id          VARCHAR(64) NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    parent_event_id VARCHAR(64),
    task_id         VARCHAR(64),
    agent_name      VARCHAR(64),
    event_type      VARCHAR(48) NOT NULL,            -- query.parsed / plan.created / sql.executed / ...
    status          VARCHAR(16),
    started_at      TIMESTAMPTZ,
    ended_at        TIMESTAMPTZ,
    duration_ms     INT,
    input_summary   JSONB,
    output_summary  JSONB,
    tool_name       VARCHAR(64),
    artifact_refs   TEXT[] NOT NULL DEFAULT '{}',
    retry_index     INT NOT NULL DEFAULT 0,
    error_code      VARCHAR(64),
    model_id        VARCHAR(96),
    prompt_version  VARCHAR(64),
    token_usage     JSONB,
    visibility      VARCHAR(16) NOT NULL DEFAULT 'visible'  -- visible/internal/redacted
);
CREATE INDEX IF NOT EXISTS idx_trace_run ON trace_events(run_id, started_at);

-- ---------- 9. 证据与主张（Evidence Graph） ----------
CREATE TABLE IF NOT EXISTS evidence_nodes (
    evidence_id     VARCHAR(64) PRIMARY KEY,
    run_id          VARCHAR(64),
    node_type       VARCHAR(24) NOT NULL,   -- Question/PlanTask/SQLQuery/SQLRow/Document/Chunk/TableCell/Formula/Claim/VerificationCheck
    ref_key         VARCHAR(256),           -- 指向具体对象: sql hash / chunk_id / page...
    payload         JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS evidence_edges (
    edge_id         BIGSERIAL PRIMARY KEY,
    run_id          VARCHAR(64),
    src_evidence_id VARCHAR(64) NOT NULL REFERENCES evidence_nodes(evidence_id) ON DELETE CASCADE,
    dst_evidence_id VARCHAR(64) NOT NULL REFERENCES evidence_nodes(evidence_id) ON DELETE CASCADE,
    edge_type       VARCHAR(24) NOT NULL,   -- derived_from/retrieved_from/maps_to/supports/contradicts/verified_by/repaired_from
    weight          NUMERIC(4,3)
);
CREATE INDEX IF NOT EXISTS idx_edges_run ON evidence_edges(run_id);

CREATE TABLE IF NOT EXISTS claims (
    claim_id        VARCHAR(64) PRIMARY KEY,
    run_id          VARCHAR(64) REFERENCES runs(run_id) ON DELETE CASCADE,
    text            TEXT NOT NULL,
    status          VARCHAR(24) NOT NULL DEFAULT 'unverified', -- verified/warned/failed/insufficient_evidence
    evidence_ids    TEXT[] NOT NULL DEFAULT '{}',
    confidence_level VARCHAR(16),
    verification    JSONB,                  -- 逐条规则 PASS/WARN/FAIL
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- 10. 摄取任务 ----------
CREATE TABLE IF NOT EXISTS ingest_jobs (
    job_id          VARCHAR(64) PRIMARY KEY,
    document_id     VARCHAR(64) REFERENCES document_assets(document_id),
    job_type        VARCHAR(24) NOT NULL,   -- parse/embed/index
    status          VARCHAR(16) NOT NULL DEFAULT 'queued',
    error           TEXT,
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- 11. 应用设置（模型接入配置，仅应用账号可读写） ----------
CREATE TABLE IF NOT EXISTS app_settings (
    setting_key     VARCHAR(64) PRIMARY KEY,
    value           JSONB NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE app_settings IS '页面「模型接入」保存的运行时配置；密钥只存于此表，readonly_user 无权限';

-- 权限授予（元数据库: datamind_app 全权，readonly_user 只读核心表）
GRANT CONNECT ON DATABASE datamind_meta TO datamind_app, readonly_user;
GRANT USAGE, CREATE ON SCHEMA public TO datamind_app;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO readonly_user;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO datamind_app;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO datamind_app;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    GRANT ALL ON TABLES TO datamind_app;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    GRANT ALL ON SEQUENCES TO datamind_app;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    GRANT SELECT ON TABLES TO readonly_user;

-- 模型密钥表对问数只读账号完全关闭（上面的默认授权不适用于本表）
REVOKE ALL ON app_settings FROM readonly_user;
