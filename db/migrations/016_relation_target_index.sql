-- Relation lookups filter on `source_block_id = ANY(...) OR target_block_id = ANY(...)`
-- (see contract_store.read_relations), but migration 015 only indexed the source side,
-- so reading a relation from its target end was a sequential scan.
CREATE INDEX IF NOT EXISTS document_block_relations_target_idx
 ON document_block_relations(target_block_id);
