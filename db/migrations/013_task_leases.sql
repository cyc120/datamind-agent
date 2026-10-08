-- Cooperative cross-host leases for bounded task execution.
-- A lease is taken over only after it expires; every takeover increments `fence`, which
-- consumers must compare before writing, so a stale holder cannot overwrite new work.
-- This is coordination, not distributed consensus: it relies on a shared, healthy
-- database and on consumers actually enforcing the fence.
CREATE TABLE IF NOT EXISTS task_leases (
 name VARCHAR(128) PRIMARY KEY,
 token VARCHAR(64) NOT NULL,
 holder VARCHAR(200) NOT NULL,
 fence BIGINT NOT NULL DEFAULT 1 CHECK(fence>=1),
 acquired_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 renewed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS task_leases_expiry_idx ON task_leases(expires_at);
-- Lease rows hold no business data; the read-only query role has no access.
REVOKE ALL ON task_leases FROM readonly_user;
