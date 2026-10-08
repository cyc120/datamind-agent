-- Unified SourceLocator per parsed block (design section 5.2/5.4).
-- A separate table rather than new columns on document_blocks: the base table is owned
-- by postgres, while migrations run as the application role, so base-table DDL is not
-- the project's migration pattern. Rows exist only for blocks that were actually
-- contract-validated; blocks parsed before the contract existed keep no row (no
-- fabricated locator).
CREATE TABLE IF NOT EXISTS document_block_locators (
 block_id VARCHAR(64) PRIMARY KEY,
 document_id VARCHAR(64) NOT NULL,
 contract_version VARCHAR(32) NOT NULL,
 locator JSONB NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS document_block_locators_doc_idx
 ON document_block_locators(document_id);
GRANT SELECT ON document_block_locators TO readonly_user;
COMMENT ON TABLE document_block_locators IS
 'Validated SourceLocator per block (locator_kind and coordinates) from datamind/parse_contract.py';
