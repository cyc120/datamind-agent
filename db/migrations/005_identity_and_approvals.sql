CREATE TABLE IF NOT EXISTS auth_users (
 user_id VARCHAR(64) PRIMARY KEY,
 username VARCHAR(64) NOT NULL UNIQUE,
 display_name VARCHAR(120) NOT NULL,
 password_hash TEXT NOT NULL,
 roles TEXT[] NOT NULL CHECK (roles <@ ARRAY['viewer','reviewer','approver','admin']::TEXT[]),
 active BOOLEAN NOT NULL DEFAULT true,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS auth_sessions (
 token_hash CHAR(64) PRIMARY KEY,
 user_id VARCHAR(64) NOT NULL REFERENCES auth_users(user_id) ON DELETE CASCADE,
 csrf_token VARCHAR(64) NOT NULL,
 expires_at TIMESTAMPTZ NOT NULL,
 revoked_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS auth_login_limits (
 bucket CHAR(64) PRIMARY KEY,
 failures INT NOT NULL DEFAULT 0,
 window_start TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Legacy reviews remain explicitly unauthenticated; never infer an identity.
ALTER TABLE document_fact_reviews ADD COLUMN IF NOT EXISTS reviewer_user_id VARCHAR(64);
ALTER TABLE document_fact_reviews ADD COLUMN IF NOT EXISTS reviewer_roles TEXT[];
CREATE TABLE IF NOT EXISTS document_fact_approvals (
 approval_id VARCHAR(64) PRIMARY KEY,
 request_id VARCHAR(64) NOT NULL UNIQUE,
 document_id VARCHAR(64) NOT NULL,
 fact_id VARCHAR(64) NOT NULL,
 document_version INT NOT NULL,
 source_sha256 CHAR(64) NOT NULL,
 parser_version VARCHAR(64) NOT NULL,
 payload_sha256 CHAR(64) NOT NULL,
 review_id VARCHAR(64) NOT NULL REFERENCES document_fact_reviews(review_id),
 previous_approval_id VARCHAR(64) REFERENCES document_fact_approvals(approval_id),
 decision VARCHAR(32) NOT NULL CHECK(decision IN ('approved','rejected','revoked')),
 approver_user_id VARCHAR(64) NOT NULL,
 approver_label VARCHAR(120) NOT NULL,
 approver_roles TEXT[] NOT NULL,
 reason TEXT NOT NULL,
 fact_snapshot JSONB NOT NULL,
 request_payload JSONB NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_fact_approval_latest ON document_fact_approvals
 (document_id,fact_id,document_version,source_sha256,parser_version,payload_sha256,created_at DESC);
GRANT SELECT ON document_fact_approvals TO readonly_user;
-- Passwords, sessions and CSRF values are never part of the query role/catalog.
REVOKE ALL ON auth_users,auth_sessions,auth_login_limits FROM PUBLIC,readonly_user;
