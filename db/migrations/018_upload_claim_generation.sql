-- R04: bind every upload claim to an unforgeable generation.
--
-- Why a new table instead of two columns on `ingest_jobs`
-- -------------------------------------------------------
-- `ingest_jobs` is created by `deploy/init/02_metadata.sql` (run as `postgres`), so it is
-- owned by `postgres`; `db/migrate.py` connects as `datamind_app`, which has DML rights but
-- not ownership. An `ALTER TABLE ingest_jobs ADD COLUMN ...` here would fail with
-- "must be owner of table ingest_jobs" and abort the whole migration run — no existing
-- migration alters one of the init-created tables for exactly that reason. The generation
-- therefore lives in its own table, created (and owned) by the migration role.
--
-- What it holds
-- -------------
-- One row per job. `claimed_at`/`attempt` are bumped by
-- `ingestion.upload_pipeline._claim` on every claim (never reset), so `attempt` is the
-- immutable "how many claims has this content had" counter and `claim_token` is the
-- current generation. A terminal transition, a cancel and a re-arm clear `claim_token`
-- (keeping the attempt count), so an older worker's late result write can never match.
--
-- `ON DELETE CASCADE` matters for cleanup: deleting the `ingest_jobs` row (the existing
-- test/teardown path) removes its claim row too, so no orphan generation rows accumulate.
-- The read-only query role has no business reason to read claim tokens.
CREATE TABLE IF NOT EXISTS ingest_job_claims (
    job_id      VARCHAR(64) PRIMARY KEY REFERENCES ingest_jobs(job_id) ON DELETE CASCADE,
    claim_token VARCHAR(64),
    attempt     BIGINT NOT NULL DEFAULT 0 CHECK(attempt>=0),
    claimed_at  TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
REVOKE ALL ON ingest_job_claims FROM readonly_user;
