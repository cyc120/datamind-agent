-- Metadata extension; source approval declarations do not imply manual verification.
ALTER TABLE document_facts DROP CONSTRAINT IF EXISTS document_facts_approval_status_check;
ALTER TABLE document_facts ADD CONSTRAINT document_facts_approval_status_check
 CHECK (approval_status IN ('unreviewed','approved','unapproved','unknown'));
ALTER TABLE document_facts DROP CONSTRAINT IF EXISTS document_facts_verification_status_check;
ALTER TABLE document_facts ADD CONSTRAINT document_facts_verification_status_check
 CHECK (verification_status IN ('candidate','needs_review'));
ALTER TABLE document_facts ALTER COLUMN normalized_value DROP NOT NULL;
ALTER TABLE document_facts ADD COLUMN IF NOT EXISTS fact_kind VARCHAR(32) NOT NULL DEFAULT 'measurement';
ALTER TABLE document_facts ADD COLUMN IF NOT EXISTS entity_id VARCHAR(64);
ALTER TABLE document_facts ADD COLUMN IF NOT EXISTS operation_no VARCHAR(32);
ALTER TABLE document_facts ADD COLUMN IF NOT EXISTS batch_id VARCHAR(64);
ALTER TABLE document_facts ADD COLUMN IF NOT EXISTS normalized_range JSONB;
CREATE INDEX IF NOT EXISTS idx_facts_industrial ON document_facts(entity_id,operation_no,batch_id,metric);

-- Schema owner is bootstrapped by deploy/init/04 or scripts/start_local.sh.
CREATE TABLE IF NOT EXISTS demo_industrial.parts (
 part_id VARCHAR(32) PRIMARY KEY, name TEXT NOT NULL, material TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS demo_industrial.process_revisions (
 revision_id VARCHAR(64) PRIMARY KEY, part_id VARCHAR(32) NOT NULL REFERENCES demo_industrial.parts,
 approval_status VARCHAR(16) NOT NULL, valid_from TIMESTAMPTZ, valid_to TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS demo_industrial.operations (
 revision_id VARCHAR(64) NOT NULL REFERENCES demo_industrial.process_revisions, operation_no VARCHAR(32) NOT NULL,
 name TEXT NOT NULL, PRIMARY KEY(revision_id,operation_no)
);
CREATE TABLE IF NOT EXISTS demo_industrial.production_batches (
 batch_id VARCHAR(32) PRIMARY KEY, part_id VARCHAR(32) NOT NULL REFERENCES demo_industrial.parts,
 produced_at TIMESTAMPTZ NOT NULL, recorded_revision VARCHAR(64) REFERENCES demo_industrial.process_revisions
);
CREATE TABLE IF NOT EXISTS demo_industrial.process_measurements (
 measurement_id VARCHAR(64) PRIMARY KEY, batch_id VARCHAR(32) NOT NULL REFERENCES demo_industrial.production_batches,
 operation_no VARCHAR(32) NOT NULL, metric VARCHAR(64) NOT NULL, value NUMERIC NOT NULL, unit VARCHAR(32) NOT NULL,
 measured_at TIMESTAMPTZ NOT NULL, method VARCHAR(32) NOT NULL
);
CREATE TABLE IF NOT EXISTS demo_industrial.quality_records (
 quality_id VARCHAR(64) PRIMARY KEY, measurement_id VARCHAR(64) NOT NULL UNIQUE REFERENCES demo_industrial.process_measurements,
 report_id VARCHAR(64) NOT NULL UNIQUE, method VARCHAR(32) NOT NULL
);
CREATE TABLE IF NOT EXISTS demo_industrial.dataset_manifest (
 dataset_version VARCHAR(64) PRIMARY KEY, seed_sha256 CHAR(64) NOT NULL, pdf_sha256 CHAR(64) NOT NULL,
 provenance TEXT NOT NULL, loaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
GRANT USAGE ON SCHEMA demo_industrial TO readonly_user;
GRANT SELECT ON ALL TABLES IN SCHEMA demo_industrial TO readonly_user;

CREATE TABLE IF NOT EXISTS conflict_records (
 conflict_id VARCHAR(64) PRIMARY KEY, run_id VARCHAR(64) NOT NULL REFERENCES runs ON DELETE CASCADE,
 entity_key JSONB NOT NULL, metric VARCHAR(64) NOT NULL, conflict_type VARCHAR(64) NOT NULL,
 left_fact_refs JSONB NOT NULL, right_fact_refs JSONB NOT NULL, comparison_context JSONB NOT NULL,
 normalized_values JSONB NOT NULL, rule_id VARCHAR(64) NOT NULL, rule_version VARCHAR(64) NOT NULL,
 resolution_status VARCHAR(32) NOT NULL, selected_fact_ref JSONB, reason TEXT NOT NULL, payload JSONB NOT NULL
);
GRANT SELECT ON conflict_records TO readonly_user;
