-- Append-only, authenticated association reviews; source snapshots stay intact.
CREATE TABLE IF NOT EXISTS cad_association_reviews (
 review_id VARCHAR(64) PRIMARY KEY,
 request_id VARCHAR(64) NOT NULL UNIQUE,
 document_id VARCHAR(64) NOT NULL,
 document_version INT NOT NULL,
 feature_id VARCHAR(128) NOT NULL,
 source_sha256 CHAR(64) NOT NULL,
 parser_version VARCHAR(64) NOT NULL,
 bundle_sha256 CHAR(64) NOT NULL,
 feature_sha256 CHAR(64) NOT NULL,
 previous_review_id VARCHAR(64) REFERENCES cad_association_reviews(review_id),
 decision VARCHAR(32) NOT NULL CHECK(decision IN ('confirmed','rejected','needs_review')),
 selected_target VARCHAR(32),
 reason TEXT NOT NULL,
 reviewer_user_id VARCHAR(64) NOT NULL,
 reviewer_label VARCHAR(120) NOT NULL,
 reviewer_roles TEXT[] NOT NULL,
 feature_snapshot JSONB NOT NULL,
 request_payload JSONB NOT NULL,
 record_sha256 CHAR(64) NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(document_id,document_version) REFERENCES cad_versions(document_id,document_version),
 CHECK((decision='confirmed')=(selected_target IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_cad_review_version ON cad_association_reviews
 (document_id,document_version,feature_id,created_at,review_id);
GRANT SELECT ON cad_association_reviews TO readonly_user;
