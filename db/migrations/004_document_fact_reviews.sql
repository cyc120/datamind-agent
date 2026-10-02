-- Append-only extraction reviews. Original facts/approval statements stay untouched.
CREATE TABLE IF NOT EXISTS document_fact_reviews (
 review_id VARCHAR(64) PRIMARY KEY,
 request_id VARCHAR(64) NOT NULL UNIQUE,
 document_id VARCHAR(64) NOT NULL,
 fact_id VARCHAR(64) NOT NULL,
 document_version INT NOT NULL,
 source_sha256 CHAR(64) NOT NULL,
 parser_version VARCHAR(64) NOT NULL,
 payload_sha256 CHAR(64) NOT NULL,
 previous_review_id VARCHAR(64) REFERENCES document_fact_reviews(review_id),
 decision VARCHAR(32) NOT NULL CHECK(decision IN ('confirmed','rejected','needs_review')),
 reviewer_label VARCHAR(120) NOT NULL,
 reason TEXT NOT NULL,
 fact_snapshot JSONB NOT NULL,
 request_payload JSONB NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_fact_review_latest ON document_fact_reviews
 (document_id,fact_id,document_version,source_sha256,parser_version,payload_sha256,created_at DESC);
GRANT SELECT ON document_fact_reviews TO readonly_user;
