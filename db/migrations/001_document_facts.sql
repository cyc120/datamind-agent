CREATE TABLE IF NOT EXISTS document_facts (
    fact_id VARCHAR(64) PRIMARY KEY,
    document_id VARCHAR(64) NOT NULL REFERENCES document_assets(document_id) ON DELETE CASCADE,
    document_version INT NOT NULL,
    source_sha256 CHAR(64) NOT NULL,
    parser_version VARCHAR(64) NOT NULL,
    extraction_version VARCHAR(64) NOT NULL,
    entity_name TEXT NOT NULL,
    entity_type VARCHAR(32) NOT NULL,
    metric VARCHAR(64) NOT NULL,
    raw_value TEXT NOT NULL,
    normalized_value NUMERIC NOT NULL,
    normalized_unit VARCHAR(32),
    approval_status VARCHAR(32) NOT NULL CHECK (approval_status='unreviewed'),
    verification_status VARCHAR(32) NOT NULL CHECK (verification_status='candidate'),
    payload JSONB NOT NULL,
    payload_sha256 CHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE document_facts ADD COLUMN IF NOT EXISTS payload_sha256 CHAR(64);
CREATE INDEX IF NOT EXISTS idx_document_facts_version ON document_facts(document_id, document_version, parser_version);
GRANT SELECT ON document_facts TO readonly_user;
