-- Contract blocks and relations for formats that do not fit the page-shaped block table.
-- Office and DXF blocks locate their origin by part/paragraph/slide/sheet/handle, not by
-- page number, so putting them in `document_blocks` would require inventing a page.
-- Relations reference contract block ids (a plain column, no cross-table FK) so they can
-- describe either store, and only validated rows are ever written.
CREATE TABLE IF NOT EXISTS document_contract_blocks (
 block_id VARCHAR(64) PRIMARY KEY,
 document_id VARCHAR(64) NOT NULL,
 contract_version VARCHAR(32) NOT NULL,
 block_type VARCHAR(32) NOT NULL,
 text TEXT NOT NULL,
 reading_order INT NOT NULL,
 native_attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
 locator JSONB NOT NULL,
 content_hash CHAR(64) NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS document_contract_blocks_doc_idx
 ON document_contract_blocks(document_id, reading_order);
GRANT SELECT ON document_contract_blocks TO readonly_user;

CREATE TABLE IF NOT EXISTS document_block_relations (
 relation_id VARCHAR(64) PRIMARY KEY,
 document_id VARCHAR(64) NOT NULL,
 source_block_id VARCHAR(64) NOT NULL,
 target_block_id VARCHAR(64) NOT NULL,
 relation_type VARCHAR(32) NOT NULL,
 relation_state VARCHAR(16) NOT NULL CHECK(relation_state IN ('candidate','verified','needs_review')),
 recovery_method VARCHAR(64) NOT NULL,
 evidence TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 CHECK(source_block_id <> target_block_id),
 CHECK(length(evidence) > 0)
);
CREATE INDEX IF NOT EXISTS document_block_relations_doc_idx
 ON document_block_relations(document_id);
CREATE INDEX IF NOT EXISTS document_block_relations_source_idx
 ON document_block_relations(source_block_id);
GRANT SELECT ON document_block_relations TO readonly_user;
COMMENT ON TABLE document_block_relations IS
 'Validated BlockRelation rows; evidence is required and a self-reference is refused';
