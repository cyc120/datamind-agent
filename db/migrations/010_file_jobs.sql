-- Durable, bounded registrations of staged DXF/measurement JSON files.
-- Input and actor evidence are retained; session hashes are private execution data.
CREATE TABLE IF NOT EXISTS file_jobs (
 job_id VARCHAR(64) PRIMARY KEY,
 request_id VARCHAR(64) NOT NULL UNIQUE,
 owner_user_id VARCHAR(64) NOT NULL,
 actor JSONB NOT NULL,
 session_hash CHAR(64) NOT NULL,
 request_payload JSONB NOT NULL,
 request_sha256 CHAR(64) NOT NULL,
 max_concurrency INT NOT NULL CHECK(max_concurrency BETWEEN 1 AND 4),
 cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS file_job_items (
 job_id VARCHAR(64) NOT NULL REFERENCES file_jobs(job_id),
 item_id VARCHAR(64) NOT NULL,
 position INT NOT NULL CHECK(position BETWEEN 0 AND 19),
 kind VARCHAR(16) NOT NULL CHECK(kind IN ('drawing','measurement')),
 input_payload JSONB NOT NULL,
 input_sha256 CHAR(64) NOT NULL,
 child_request_id VARCHAR(64) NOT NULL UNIQUE,
 status VARCHAR(16) NOT NULL DEFAULT 'pending'
   CHECK(status IN ('pending','running','succeeded','failed','cancelled')),
 attempts INT NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 3),
 total_attempts INT NOT NULL DEFAULT 0 CHECK(total_attempts>=attempts),
 lease_token VARCHAR(64),
 lease_expires_at TIMESTAMPTZ,
 result JSONB,
 result_sha256 CHAR(64),
 error_code VARCHAR(96),
 started_at TIMESTAMPTZ,
 finished_at TIMESTAMPTZ,
 PRIMARY KEY(job_id,item_id),
 UNIQUE(job_id,position),
 CHECK((status='running')=(lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)),
 CHECK((result IS NULL)=(result_sha256 IS NULL)),
 CHECK(status<>'succeeded' OR result IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS file_job_items_pending_idx ON file_job_items(status,job_id,position);
CREATE INDEX IF NOT EXISTS file_job_items_lease_idx ON file_job_items(lease_expires_at) WHERE status='running';
-- Public reads use authenticated application functions. Grant no read-only role
-- access to these tables because the execution session hash is intentionally private.
