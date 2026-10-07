-- Authenticated preparation registrations only; no legacy document/fact/index writes.
CREATE TABLE IF NOT EXISTS pdf_snapshot_assets (
 document_id VARCHAR(64) PRIMARY KEY,
 current_version INT NOT NULL CHECK(current_version >= 1)
);
CREATE TABLE IF NOT EXISTS pdf_snapshot_versions (
 document_id VARCHAR(64) NOT NULL REFERENCES pdf_snapshot_assets(document_id) DEFERRABLE INITIALLY DEFERRED,
 document_version INT NOT NULL CHECK(document_version >= 1),
 source_sha256 CHAR(64) NOT NULL CHECK(source_sha256 ~ '^[a-f0-9]{64}$'),
 parser_version VARCHAR(128) NOT NULL,
 title VARCHAR(200) NOT NULL CHECK(length(btrim(title)) > 0),
 request_id VARCHAR(64) NOT NULL UNIQUE,
 job_id VARCHAR(64) NOT NULL,
 attempt_token VARCHAR(64) NOT NULL,
 manifest_sha256 CHAR(64) NOT NULL,
 snapshot JSONB NOT NULL,
 snapshot_sha256 CHAR(64) NOT NULL,
 request_payload JSONB NOT NULL,
 request_sha256 CHAR(64) NOT NULL,
 registrar_user_id VARCHAR(64) NOT NULL,
 registrar_label VARCHAR(120) NOT NULL,
 registrar_roles TEXT[] NOT NULL,
 created_at TIMESTAMPTZ NOT NULL,
 registration_sha256 CHAR(64) NOT NULL,
 PRIMARY KEY(document_id, document_version),
 UNIQUE(job_id, attempt_token)
);
DO $$ BEGIN
 ALTER TABLE pdf_snapshot_assets ADD CONSTRAINT pdf_snapshot_current_version_fk
 FOREIGN KEY(document_id, current_version) REFERENCES pdf_snapshot_versions(document_id, document_version)
 DEFERRABLE INITIALLY DEFERRED;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
CREATE INDEX IF NOT EXISTS pdf_snapshot_versions_created_idx ON pdf_snapshot_versions(created_at DESC,document_id);
-- Application routes are private administrator routes. There is no SQL readonly
-- grant that would bypass their authentication. Trusted database owners can manage rows.
REVOKE ALL ON pdf_snapshot_assets,pdf_snapshot_versions FROM PUBLIC,readonly_user;
