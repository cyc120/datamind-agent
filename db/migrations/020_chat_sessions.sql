-- Server-side chat sessions: a named grouping of EXISTING `runs` rows.
--
-- This table holds SESSION METADATA ONLY -- there is no transcript table. A session is a title
-- plus soft-delete bookkeeping over rows that already exist: the transcript is DERIVED by
-- filtering `runs` on `conversation_id` and ordering by `started_at` (question = user turn,
-- answer = assistant turn). Keeping it derived means every turn has exactly one copy and there
-- is no second write path that can drift from `runs`.
--
-- `runs.conversation_id` already exists (deploy/init/02_metadata.sql) and was previously never
-- written; the chat endpoints start writing it when a request carries a session id. Its INDEX
-- CANNOT LIVE HERE: `runs` is owned by `postgres`, while db/migrate.py runs every file as
-- `datamind_app`, and `CREATE INDEX ON runs(...)` fails with
-- "ERROR: must be owner of table runs" (verified live). That index is therefore added in
-- deploy/init/02_metadata.sql, which is executed as the postgres owner.
--
-- Invariants the database enforces:
--   * session_id / user_id are non-blank, so a whitespace-only value can never become a primary
--     key or an owner (a blank owner would silently escape the owner-scoped WHERE clauses);
--   * title is non-blank and at most 200 characters, so every session stays listable and
--     renameable and auto-naming cannot persist an unbounded question;
--   * user_id references auth_users(user_id) ON DELETE CASCADE, so removing an account cannot
--     strand chat metadata pointing at a nonexistent owner (auth_users is created by migration
--     005 and is owned by datamind_app, so this FK is permitted -- verified live);
--   * deleted_at IS NULL OR deleted_at >= created_at, so a row can never claim to have been
--     deleted before it existed.
--
-- WHY SOFT DELETE: sessions are user-authored, but their content already lives in `runs`, which
-- is owned by another role and written by every answer path. A hard DELETE here would either
-- orphan that shared history or cascade into it. DELETE therefore only stamps `deleted_at`, and
-- every list/detail/rename path filters `deleted_at IS NULL`; an operator can still purge rows
-- later without the API ever mutating `runs`.
--
-- PRIVACY: chat content is private to its owner, so this table is REVOKEd from PUBLIC and
-- readonly_user (precedent: 005:50 auth_sessions, 011:36 pdf_snapshot_*, 013:17 task_leases).
-- Access is owner-scoped in the application and only through the write identity.
--
-- Every statement below is idempotent (CREATE TABLE/INDEX IF NOT EXISTS; COMMENT and REVOKE are
-- naturally idempotent) because db/migrate.py replays every db/migrations/*.sql on every
-- startup, in one transaction, with no ledger and no checksum, as datamind_app.

CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id  VARCHAR(64) PRIMARY KEY,
    user_id     VARCHAR(64) NOT NULL REFERENCES auth_users(user_id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at  TIMESTAMPTZ,
    CHECK (length(btrim(session_id)) > 0),
    CHECK (length(btrim(user_id)) > 0),
    CHECK (length(btrim(title)) > 0),
    CHECK (length(title) <= 200),
    CHECK (deleted_at IS NULL OR deleted_at >= created_at)
);

-- Listing is always "my live sessions, most recent first": both partial indexes exclude deleted
-- rows from the index entirely, so the WHERE deleted_at IS NULL filter is index-covered.
CREATE INDEX IF NOT EXISTS chat_sessions_owner_updated_idx
    ON chat_sessions(user_id, updated_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS chat_sessions_owner_created_idx
    ON chat_sessions(user_id, created_at DESC) WHERE deleted_at IS NULL;

COMMENT ON TABLE chat_sessions IS
    'Server-side chat session metadata; the transcript is derived from runs WHERE conversation_id = session_id, ordered by started_at';
COMMENT ON COLUMN chat_sessions.session_id IS
    'Opaque session id, "sess-" + uuid4().hex[:24]; primary key and the runs.conversation_id it groups';
COMMENT ON COLUMN chat_sessions.user_id IS
    'Owning auth_users.user_id; every list/detail/rename/delete is scoped to this owner, so a foreign session is indistinguishable from a missing one';
COMMENT ON COLUMN chat_sessions.title IS
    'Human-readable title, non-blank and <= 200 characters; auto-set from the first question while it is still the default';
COMMENT ON COLUMN chat_sessions.created_at IS
    'Row creation time (UTC); the session creation time shown in the UI';
COMMENT ON COLUMN chat_sessions.updated_at IS
    'Last mutation time (UTC); advanced when a run is linked or the title changes, so listing is recency-ordered';
COMMENT ON COLUMN chat_sessions.deleted_at IS
    'Soft-delete stamp (UTC); NULL means live. Every list/detail/rename filters deleted_at IS NULL';

-- Chat content is private: neither the public nor the read-only query role may see it.
REVOKE ALL ON chat_sessions FROM PUBLIC, readonly_user;
