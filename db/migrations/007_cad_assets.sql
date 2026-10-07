-- CAD inspection assets are separate from RAG documents and production facts.
CREATE TABLE IF NOT EXISTS cad_assets (
 document_id VARCHAR(64) PRIMARY KEY,
 current_version INT NOT NULL CHECK (current_version >= 1),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS cad_versions (
 document_id VARCHAR(64) NOT NULL REFERENCES cad_assets(document_id),
 document_version INT NOT NULL CHECK (document_version >= 1),
 source_sha256 CHAR(64) NOT NULL,
 parser_version VARCHAR(64) NOT NULL,
 title VARCHAR(200) NOT NULL,
 request_id VARCHAR(64) NOT NULL UNIQUE,
 request_payload JSONB NOT NULL,
 bundle JSONB NOT NULL,
 bundle_sha256 CHAR(64) NOT NULL,
 registrar_user_id VARCHAR(64) NOT NULL,
 registrar_label VARCHAR(120) NOT NULL,
 registrar_roles TEXT[] NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(document_id,document_version)
);
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='cad_current_version_fk'
                AND conrelid='cad_assets'::regclass) THEN
  ALTER TABLE cad_assets ADD CONSTRAINT cad_current_version_fk
   FOREIGN KEY(document_id,current_version) REFERENCES cad_versions(document_id,document_version)
   DEFERRABLE INITIALLY DEFERRED;
 END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_cad_versions_source ON cad_versions(source_sha256);
GRANT SELECT ON cad_assets,cad_versions TO readonly_user;
