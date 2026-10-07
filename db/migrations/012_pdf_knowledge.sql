-- Private native PDF vector publication outbox. No legacy knowledge-table grants.
CREATE TABLE IF NOT EXISTS pdf_knowledge_requests (
 publication_id VARCHAR(64) PRIMARY KEY,
 request_id VARCHAR(64) NOT NULL UNIQUE,
 document_id VARCHAR(64) NOT NULL,
 document_version INT NOT NULL CHECK(document_version>=1),
 source_sha256 CHAR(64) NOT NULL,
 parser_version VARCHAR(128) NOT NULL,
 manifest_sha256 CHAR(64) NOT NULL,
 previous_publication_id VARCHAR(64),
 owner_user_id VARCHAR(64) NOT NULL,
 actor JSONB NOT NULL,
 session_hash CHAR(64) NOT NULL,
 request_payload JSONB NOT NULL,
 request_sha256 CHAR(64) NOT NULL,
 target_snapshot JSONB NOT NULL,
 target_snapshot_sha256 CHAR(64) NOT NULL,
 target_registration_sha256 CHAR(64) NOT NULL,
 evidence_sha256 CHAR(64) NOT NULL,
 status VARCHAR(16) NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','failed','published')),
 attempts INT NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 3),
 total_attempts INT NOT NULL DEFAULT 0 CHECK(total_attempts>=attempts),
 lease_token VARCHAR(64),
 lease_expires_at TIMESTAMPTZ,
 vector_plan JSONB,
 vector_plan_sha256 CHAR(64),
 result JSONB,
 result_sha256 CHAR(64),
 error_code VARCHAR(96),
 created_at TIMESTAMPTZ NOT NULL,
 started_at TIMESTAMPTZ,
 finished_at TIMESTAMPTZ,
 UNIQUE(document_id,publication_id),
 FOREIGN KEY(document_id,document_version) REFERENCES pdf_snapshot_versions(document_id,document_version),
 CHECK((status='running' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)
    OR (status<>'running' AND lease_token IS NULL AND lease_expires_at IS NULL)),
 CHECK((vector_plan IS NULL)=(vector_plan_sha256 IS NULL)),
 CHECK((result IS NULL)=(result_sha256 IS NULL)),
 CHECK((status='published')=(result IS NOT NULL)),
 CHECK(status<>'published' OR vector_plan IS NOT NULL)
);
CREATE TABLE IF NOT EXISTS pdf_knowledge_heads (
 document_id VARCHAR(64) PRIMARY KEY,
 current_publication_id VARCHAR(64) NOT NULL,
 FOREIGN KEY(document_id,current_publication_id)
 REFERENCES pdf_knowledge_requests(document_id,publication_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX IF NOT EXISTS pdf_knowledge_requests_status_idx ON pdf_knowledge_requests(status,created_at,publication_id);
CREATE INDEX IF NOT EXISTS pdf_knowledge_requests_lease_idx ON pdf_knowledge_requests(lease_expires_at) WHERE status='running';
REVOKE ALL ON pdf_knowledge_requests,pdf_knowledge_heads FROM PUBLIC,readonly_user;
