-- Immutable, narrowly scoped registrations and append-only activation events.
CREATE TABLE IF NOT EXISTS source_authority_rules (
 rule_id VARCHAR(64) PRIMARY KEY,
 request_id VARCHAR(64) NOT NULL UNIQUE,
 creator_user_id VARCHAR(64) NOT NULL,
 creator_label VARCHAR(120) NOT NULL,
 creator_roles TEXT[] NOT NULL,
 payload JSONB NOT NULL,
 payload_sha256 CHAR(64) NOT NULL,
 request_payload JSONB NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS source_authority_events (
 event_id VARCHAR(64) PRIMARY KEY,
 request_id VARCHAR(64) NOT NULL UNIQUE,
 rule_id VARCHAR(64) NOT NULL REFERENCES source_authority_rules(rule_id),
 previous_event_id VARCHAR(64) REFERENCES source_authority_events(event_id),
 decision VARCHAR(24) NOT NULL CHECK(decision IN ('approved','rejected','revoked')),
 actor_user_id VARCHAR(64) NOT NULL,
 actor_label VARCHAR(120) NOT NULL,
 actor_roles TEXT[] NOT NULL,
 reason TEXT NOT NULL,
 request_payload JSONB NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_authority_events_latest ON source_authority_events(rule_id,created_at DESC,event_id DESC);
GRANT SELECT ON source_authority_rules,source_authority_events TO readonly_user;
