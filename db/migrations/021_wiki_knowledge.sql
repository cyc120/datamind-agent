-- Wiki knowledge graph storage (P1): 8 tables carrying entries, immutable revisions, assertions,
-- reusable evidence references, provenance links, relations, append-only review events and
-- idempotent build jobs.
--
-- This file implements the two P0 contracts, which are the authority when the requirement
-- document says something else:
--   * docs/WIKI_DOMAIN.md -- four entry types, the 21-word relation vocabulary, the three
--     identity rules (entry / evidence / assertion comparison), the three independent status
--     dimensions, immutable revisions;
--   * docs/WIKI_API.md   -- id formats (section 1), CAS on the publication head (5.14), the
--     revision and assertion payloads (5.3), idempotent build requests (5.9), error codes (8).
--
-- MIGRATION MECHANISM (hard constraint, verified in db/migrate.py:12-15): every
-- db/migrations/*.sql file is replayed on EVERY startup, inside ONE transaction, as
-- `datamind_app`, under pg_advisory_xact_lock(714202610), with no ledger and no checksum table.
-- Consequences this file is written for:
--   * every statement must be idempotent on its own (CREATE TABLE/INDEX IF NOT EXISTS,
--     DROP TRIGGER IF EXISTS then CREATE TRIGGER, ALTER TABLE ... ADD CONSTRAINT behind an
--     existence test; COMMENT / REVOKE / GRANT are naturally idempotent);
--   * ANY failing statement rolls back the whole batch, not just this file, so statements whose
--     failure is merely an operational inconvenience (indexes, comments, constraints, triggers)
--     are guarded by ownership instead of left unconditional.
--
-- EXPLICITLY NOT DONE, AND WHY:
--   * Nothing here alters, indexes, comments or grants on `runs`, `trace_events`,
--     `evidence_nodes`, `evidence_edges`, `claims` or `conflict_records`. Those are owned by
--     `postgres` (18 tables), db/migrate.py runs as `datamind_app`, and the repository has a real
--     accident on record (reports/cards/wp2_cross_source.md:62 --
--     `InsufficientPrivilege: must be owner of table evidence_nodes` aborted the whole batch).
--     An `ADD FOREIGN KEY` would additionally take a SHARE ROW EXCLUSIVE lock on the referenced
--     table, which is why `wiki_provenance_links.run_id` and `wiki_build_jobs.run_id` are plain
--     VARCHAR columns with NO foreign key to runs: existence is checked by the application
--     inside the write transaction (404 WIKI_RUN_NOT_FOUND). The run-level retention protection
--     of WIKI_DOMAIN.md 5.3 rule 5 is therefore NOT a database constraint -- if it must become
--     one, it needs a separate statement run by the owner of `runs`, not this file.
--   * No sequences. Every primary key is a contract-formatted VARCHAR id (WIKI_API.md 1), so no
--     sequence is created and no sequence privilege can leak.
--   * No ninth table: the version citation list and an assertion evidence list are JSONB / TEXT[]
--     columns because the eight tables named by the requirement document 5 and the P1 plan are
--     the scope. Referential integrity inside those lists cannot be a foreign key; the service
--     checks it in the write transaction and the id FORMAT of every element is enforced here.
--
-- PRIVACY (the rule with the quietest failure mode): these eight tables are private. The
-- deployed database has
--   ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT SELECT ON TABLES TO readonly_user
-- (deploy/init/02_metadata.sql:312-313), which auto-grants SELECT to `readonly_user` on tables
-- created by `postgres` -- omitting a REVOKE does NOT raise, it just leaks. Every table is
-- revoked from PUBLIC and readonly_user below, exactly as 005:50 / 011:36 / 012:50 / 013:17 /
-- 020:78 do. Measured before this migration: readonly_user could SELECT 35 tables in `public`.
--
-- TWO SOURCE PATHS FOR CONFLICTS (frozen in WIKI_DOMAIN.md 3.5, documented here because a
-- projector that reads only one of them silently loses data):
--   1. INDUSTRIAL PATH -- datamind/agent.py:720 writes a row into the `conflict_records` table
--      (175 rows live) plus a matching `ConflictRecord` node in `evidence_nodes`; the payload
--      carries left_fact_refs / right_fact_refs / normalized_values / comparison_context /
--      rule_id / rule_version / resolution_status. The three-state resolution_status
--      (unresolved / needs_review / resolved_equivalent) must survive.
--   2. CROSS-SOURCE PATH -- datamind/cross_source.py:996 and :1013 detect conflicts and NOTHING
--      IS WRITTEN TO ANY TABLE: the result only reaches the terminal trace event, i.e.
--      `trace_events.output_summary.cross_source_result.conflicts` / `.unresolved_conflicts`
--      (datamind/agent.py:195-202). This database has zero cross-source runs today, so that path
--      cannot be covered by live data (docs/WIKI_API.md 11.3) -- but the schema must carry it:
--      a relation with relation_type = 'conflicts' records its source in `basis`
--      (source_path = 'industrial_table' | 'cross_source_trace') and never assumes one.
--   Whichever path produced a conflict, `selected` is null unless a registered rule or a human
--   picked a side (`conflict_records.selected_fact_ref` is null in all 175 rows), and the
--   response must then show both sides -- the schema must not let a projector invent a winner.
--
-- THE JSONB JSON-`null` TRAP (measured, and the reason every JSONB column here is checked):
--   `conflict_records.payload->'selected_fact_ref'` holds the JSON literal `null` in all 175
--   rows, so `WHERE selected_fact_ref IS NOT NULL` matches EVERY row even though "no side was
--   selected" is the meaning. In SQL a JSON `null` is not SQL NULL.
--   Every JSONB column in this file therefore expresses "unsupplied" ONE way -- SQL NULL -- and a
--   CHECK refuses the JSON `null` literal (`jsonb_typeof(col) <> 'null'`), so `col IS NOT NULL`
--   is unambiguous here. When reading any JSONB column of this schema use plain `IS NULL` /
--   `IS NOT NULL`; when reading `conflict_records.selected_fact_ref` you must NOT (use
--   `selected_fact_ref IS NOT NULL AND jsonb_typeof(selected_fact_ref) <> 'null'`).
--   `wiki_relations.provenance_incomplete` goes further: it is NOT NULL and must always be a
--   JSON object carrying `applies`, so "nothing is missing" is `{"applies": false}` -- never
--   SQL NULL and never a JSON null.
--
-- FAIL-CLOSED INVARIANTS THE DATABASE ENFORCES (each one has a contract citation):
--   * closed vocabularies: 4 entry types, 4 publication states, 4 assertion states, 21 relation
--     types, 2 relation origins, 4 locator kinds, 3 build states, 2 build modes;
--   * entry identity (WIKI_DOMAIN.md 4.1): business_key = space_id | namespace | entry_type |
--     key_part is UNIQUE, so two entry_ids can never own one business key; a `pending:` key_part
--     can never be stored as `confirmed`;
--   * evidence identity (4.2): UNIQUE (space_id, evidence_fingerprint) -- one reusable reference
--     per source version and content span; `run_scoped` is TRUE exactly when the source snapshot
--     is absent (`source_sha256` IS NULL), which is the run_id degradation of the fingerprint,
--     and then the pinned run id is mandatory;
--   * assertion comparison identity (4.3): the hashed comparison_key is stored per assertion, so
--     only assertions in one comparison group can ever be weighed against each other;
--   * publication head CAS (6.3 / WIKI_API.md 5.14): composite FK (entry_id, head_revision_id) ->
--     wiki_revisions(entry_id, revision_id), so the head can only point at a revision of the SAME
--     entry -- a cross-entry head is refused by the database, not by review;
--   * explicit baseline (6.1): composite FK (entry_id, base_revision_id) -> wiki_revisions, so a
--     new revision can only be based on a revision of its own entry, and a revision can never be
--     its own base;
--   * a `verified` assertion must carry what WIKI_DOMAIN.md 5.2 requires for `verified` (object,
--     method, coverage, rule version, operator, time) plus the five accounting keys of
--     WIKI_API.md 7.2 -- the database refuses a bare "verified";
--   * relation origin matches its vocabulary class (3.2): #1-#13 must be origin='projected' with
--     the original raw_type kept, #14-#21 must be origin='wiki_derived' with a rule version and
--     a derivation record; an unrecognised type keeps raw_type, gets relation_type NULL and
--     supported=false with unsupported_reason='TYPE_NOT_IN_WIKI_VOCABULARY' (3.4) -- never a
--     guess at the nearest known type;
--   * undirected relation types are stored in canonical endpoint order (3.2) and a self-loop is
--     refused;
--   * append-only: revision content, assertion content and a build request's canonical payload
--     can never be overwritten in place, and review events accept neither UPDATE nor DELETE
--     (WIKI_API.md 5.13 rule 1). Section 3 defines those guards and raises the custom SQLSTATEs
--     WIKI1 (revision), WIKI2 (review event), WIKI3 (build request) and WIKI4 (assertion content)
--     so the service layer can map them to a 409 instead of a generic server error.
--   * what the database does NOT enforce, on purpose: an entry's publication_status is not kept
--     equal to its head revision's status (both are written by the service in one transaction),
--     the 200-assertion / 100-relation / 256-KiB revision limits are row-count limits the service
--     checks before insert (422 WIKI_REVISION_TOO_LARGE), a citation's and an evidence_refs
--     element's existence is checked by the service, and source_validity is never stored at all
--     because WIKI_API.md 7.5 forbids caching it.
--
-- THREE STATUS DIMENSIONS STAY SEPARATE (WIKI_DOMAIN.md 5.4): publication_status,
-- assertion_status and the read-time source_validity are three different columns/values and no
-- statement here derives one from another. "published" is not "verified", and "current" is not
-- "verified".

-- ---------- 1. tables (IF NOT EXISTS: a replay is a pure no-op) ----------

-- Entry identity (WIKI_DOMAIN.md 4.1) plus the current publication head. `title`, `aliases`,
-- `category` and `summary` are the CURRENT published projection used by the catalogue, its
-- keyword search and the entry object of WIKI_API.md 5.1 -- they are not the version history:
-- every revision keeps its own immutable title and body in wiki_revisions and a version read must
-- use the version's values, never these.
CREATE TABLE IF NOT EXISTS wiki_entries (
    entry_id            VARCHAR(64) PRIMARY KEY,
    space_id            VARCHAR(64) NOT NULL,
    owner_id            VARCHAR(64) NOT NULL REFERENCES auth_users(user_id) ON DELETE CASCADE,
    entry_type          VARCHAR(24) NOT NULL CHECK (entry_type IN (
                            'business_entity','metric','norm','conclusion')),
    namespace           VARCHAR(128) NOT NULL,
    key_part            VARCHAR(512) NOT NULL,
    confirmation        VARCHAR(16) NOT NULL CHECK (confirmation IN ('confirmed','pending')),
    title               TEXT NOT NULL,
    aliases             TEXT[] NOT NULL DEFAULT '{}',
    category            VARCHAR(64),
    summary             TEXT NOT NULL DEFAULT '',
    publication_status  VARCHAR(16) NOT NULL DEFAULT 'draft' CHECK (publication_status IN (
                            'draft','in_review','published','archived')),
    head_revision_id    VARCHAR(64),
    demo_data           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at         TIMESTAMPTZ,
    -- business_key = space_id | namespace | entry_type | key_part: unique inside a space, so a
    -- renamed entry keeps its identity and no second entry_id can claim the same business key.
    -- Space is the first segment, so two users never share an identity (no cross-space existence
    -- leak, WIKI_DOMAIN.md 4.1 rule 5).
    UNIQUE (space_id, namespace, entry_type, key_part),
    CHECK (entry_id ~ '^wiki-[0-9a-f]{24}$'),
    CHECK (space_id ~ '^wsp-[0-9a-f]{24}$'),
    CHECK (length(btrim(namespace)) > 0),
    CHECK (length(btrim(key_part)) > 0),
    CHECK (length(btrim(title)) > 0),
    CHECK (category IS NULL OR (length(btrim(category)) > 0 AND length(category) <= 64)),
    CHECK (NOT ('' = ANY (aliases))),
    CHECK (head_revision_id IS NULL OR head_revision_id ~ '^wrev-[0-9a-f]{24}$'),
    -- A `pending:` key part is a provisional entity key: it may be promoted only by a human
    -- confirmation or an approved alias, never by an automatic merge (WIKI_DOMAIN.md 4.1 rule 3).
    CHECK (key_part NOT LIKE 'pending:%' OR confirmation = 'pending'),
    -- 'published' means the head is set; 'archived' and archived_at are two halves of one fact.
    CHECK (publication_status <> 'published' OR head_revision_id IS NOT NULL),
    CHECK ((publication_status = 'archived') = (archived_at IS NOT NULL)),
    CHECK (updated_at >= created_at)
);

-- Immutable revision (WIKI_DOMAIN.md 6.1): a new edit creates a new row carrying its baseline
-- explicitly; the body and the citations of a saved revision are never overwritten in place
-- (enforced by the WIKI1 trigger of section 4). `publication_status` is the one mutable column:
-- it is the version's own lifecycle state (draft -> in_review -> published) and belongs to a
-- different dimension than the assertion status. `base_revision_id` is NULL only for the first
-- version; "implicitly base on the current head" is refused by the contract and gets no default.
CREATE TABLE IF NOT EXISTS wiki_revisions (
    revision_id         VARCHAR(64) PRIMARY KEY,
    entry_id            VARCHAR(64) NOT NULL REFERENCES wiki_entries(entry_id) ON DELETE CASCADE,
    base_revision_id    VARCHAR(64),
    title               TEXT NOT NULL,
    body_markdown       TEXT NOT NULL,
    body_sha256         CHAR(64) NOT NULL,
    -- Citation list of WIKI_API.md 5.3: [{"marker": "[1]", "evidence_ref_id": "wev-..."}]. A JSONB
    -- array because a ninth table is out of scope; the service checks that every evidence_ref_id
    -- exists inside the same space before insert. Part of the immutable payload.
    citations           JSONB NOT NULL DEFAULT '[]'::jsonb,
    author_id           VARCHAR(64) NOT NULL REFERENCES auth_users(user_id) ON DELETE RESTRICT,
    generation_method   VARCHAR(24) NOT NULL CHECK (generation_method IN (
                            'template','human','model_polish')),
    template_version    VARCHAR(64),
    model_id            VARCHAR(128),
    change_note         TEXT,
    publication_status  VARCHAR(16) NOT NULL DEFAULT 'draft' CHECK (publication_status IN (
                            'draft','in_review','published','archived')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Target of the two composite foreign keys below (publication head, explicit baseline).
    UNIQUE (entry_id, revision_id),
    CHECK (revision_id ~ '^wrev-[0-9a-f]{24}$'),
    CHECK (base_revision_id IS NULL OR base_revision_id ~ '^wrev-[0-9a-f]{24}$'),
    CHECK (base_revision_id IS NULL OR base_revision_id <> revision_id),
    CHECK (length(btrim(title)) > 0),
    -- WIKI_API.md 5.12 rule 5: body_markdown <= 256 KiB, refused rather than truncated.
    CHECK (octet_length(body_markdown) <= 262144),
    CHECK (body_sha256 ~ '^[a-f0-9]{64}$'),
    CHECK (jsonb_typeof(citations) = 'array'),
    CHECK (change_note IS NULL OR length(btrim(change_note)) > 0),
    CHECK (template_version IS NULL OR length(btrim(template_version)) > 0),
    CHECK (generation_method <> 'template' OR template_version IS NOT NULL),
    CHECK (model_id IS NULL OR (length(btrim(model_id)) > 0
                                AND generation_method = 'model_polish'))
);

-- Structured assertion inside a revision: the comparison identity (WIKI_DOMAIN.md 4.3) plus the
-- per-assertion verification accounting (5.2 / WIKI_API.md 7.2). The composite foreign key makes
-- entry_id impossible to disagree with the revision's own entry. Values are stored as strings
-- ("870", "870.000") per the repository convention of decimal strings, never floats, and the raw
-- and normalized unit may differ -- two assertions with the same metric but different units are
-- `unit_equivalent`, NOT a value conflict (4.3 rule 3).
CREATE TABLE IF NOT EXISTS wiki_assertions (
    assertion_id        VARCHAR(64) PRIMARY KEY,
    entry_id            VARCHAR(64) NOT NULL,
    revision_id         VARCHAR(64) NOT NULL,
    -- sha256 over the canonical JSON of {space_id, entity_key, predicate, applicability,
    -- time_scope, record_scope, statistic} (4.3). Stored as bare lowercase hex WITHOUT the
    -- 'sha256:' prefix that WIKI_API.md 5.3 shows in responses; the service adds the prefix.
    comparison_key      CHAR(64) NOT NULL,
    -- 4.3 entity_key, e.g. {"part_id": "A102", "batch_id": "B001", "confirmation": "pending"}.
    entity_key          JSONB NOT NULL,
    -- The metric_id (e.g. holding_temperature) or the fact_kind -- WIKI_API.md 5.3 calls this
    -- field `metric`; 4.3 calls it `predicate` because it can also be a non-metric predicate.
    predicate           VARCHAR(256) NOT NULL,
    raw_value           VARCHAR(128),
    raw_unit            VARCHAR(24),
    normalized_value    VARCHAR(128),
    normalized_unit     VARCHAR(24),
    -- All four below are nullable, and SQL NULL is the ONLY "unsupplied" representation: a JSON
    -- `null` literal is refused (see the header note on the jsonb null trap). Read them with
    -- `IS NULL` / `IS NOT NULL`.
    time_scope          JSONB,
    applicability       JSONB,
    record_scope        JSONB,
    statistic           VARCHAR(128),
    -- WIKI_API.md 5.3 assertion.evidence_refs: the wev- ids this assertion rests on. Array
    -- elements cannot be a foreign key; their FORMAT is enforced below and their existence is
    -- checked by the service in the write transaction.
    evidence_refs       TEXT[] NOT NULL DEFAULT '{}',
    -- The assertion status dimension (WIKI_DOMAIN.md 5.2). Mutable ONLY through a review event
    -- (WIKI_API.md 5.13); never derived from runs.status or claims.status (5.4 last row).
    assertion_status    VARCHAR(16) NOT NULL DEFAULT 'unverified' CHECK (assertion_status IN (
                            'unverified','verified','insufficient','conflicted')),
    verified_at         TIMESTAMPTZ,
    verified_by         VARCHAR(64),
    verification_method VARCHAR(24) CHECK (verification_method IN ('human_review','rule_engine')),
    verification_coverage JSONB,
    verification_rule_version VARCHAR(64),
    -- The five-item accounting of WIKI_API.md 7.2 (source_integrity, entity_binding,
    -- rule_verification, semantic_support, source_authority). `semantic_support` is always
    -- {"checked": false, "full_semantic_entailment": "not_independently_verified"} because that
    -- literal is hard-coded in datamind/agent.py:238 and :629 -- the key must be present, its
    -- value is not re-judged here.
    verification_accounting JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (entry_id, revision_id)
        REFERENCES wiki_revisions(entry_id, revision_id) ON DELETE CASCADE,
    CHECK (assertion_id ~ '^wassert-[0-9a-f]{24}$'),
    CHECK (comparison_key ~ '^[a-f0-9]{64}$'),
    CHECK (jsonb_typeof(entity_key) = 'object'),
    CHECK (length(btrim(predicate)) > 0),
    -- A unit without a value is meaningless; a value without a unit is legitimate (counts).
    CHECK (raw_unit IS NULL OR raw_value IS NOT NULL),
    CHECK (normalized_unit IS NULL OR normalized_value IS NOT NULL),
    CHECK (time_scope IS NULL OR jsonb_typeof(time_scope) = 'object'),
    CHECK (applicability IS NULL OR jsonb_typeof(applicability) = 'object'),
    CHECK (record_scope IS NULL OR jsonb_typeof(record_scope) = 'object'),
    CHECK (verification_coverage IS NULL OR jsonb_typeof(verification_coverage) = 'object'),
    CHECK (verification_accounting IS NULL
           OR jsonb_typeof(verification_accounting) = 'object'),
    CHECK (statistic IS NULL OR length(btrim(statistic)) > 0),
    CHECK (cardinality(evidence_refs) = 0 OR array_to_string(evidence_refs, ',')
           ~ '^(wev-[0-9a-f]{24})(,wev-[0-9a-f]{24})*$'),
    -- 'verified' is not a bare flag: WIKI_DOMAIN.md 5.2 requires object, method, coverage, rule
    -- version, operator and time, and WIKI_API.md 7.2 requires the five accounting keys.
    CHECK (assertion_status <> 'verified' OR (
              verified_at IS NOT NULL
              AND verified_by IS NOT NULL
              AND verification_method IS NOT NULL
              AND verification_coverage IS NOT NULL
              AND verification_rule_version IS NOT NULL
              AND verification_accounting IS NOT NULL
              AND verification_accounting ? 'source_integrity'
              AND verification_accounting ? 'entity_binding'
              AND verification_accounting ? 'rule_verification'
              AND verification_accounting ? 'semantic_support'
              AND verification_accounting ? 'source_authority'))
);

-- Reusable evidence identity (WIKI_DOMAIN.md 4.2): one row per source version and content span,
-- however many runs cite it. `source_sha256` is the snapshot / document digest actually used for
-- the fingerprint -- for document evidence it is
-- COALESCE(payload->>'document_sha256', payload->>'source_sha256') because in this database the
-- two fields are mutually exclusive (docs/WIKI_API.md 12.2) -- and it is a free-form snapshot
-- identifier, not necessarily 64 hex (the SQL snapshot ids look like
-- `snap-demo_retail-9b6637f76e61`), so it is NOT constrained to a hex digest.
CREATE TABLE IF NOT EXISTS wiki_evidence_refs (
    evidence_ref_id     VARCHAR(64) PRIMARY KEY,
    space_id            VARCHAR(64) NOT NULL,
    evidence_fingerprint CHAR(64) NOT NULL,
    source_kind         VARCHAR(8) NOT NULL CHECK (source_kind IN ('sql','doc')),
    source_ref          VARCHAR(256) NOT NULL,
    source_sha256       VARCHAR(128),
    source_snapshot_id  VARCHAR(128),
    source_publication_id VARCHAR(128),
    document_version    INT,
    locator_kind        VARCHAR(24) NOT NULL CHECK (locator_kind IN (
                            'sql_record','sql_aggregate','pdf_region','text_span')),
    -- Locator snapshot (4.2 table). The required keys depend on locator_kind and are enforced
    -- below; a historical locator binds to the version it was captured from and must never be
    -- silently redirected to the newest document or the newest SQL data (5.3 rule 3 / 6.6).
    locator             JSONB NOT NULL,
    record_sha256       CHAR(64) NOT NULL,
    -- Empty means empty: when retrieval produced no quotable text the excerpt stays empty and the
    -- fact stays visible (docs/WIKI_API.md 11.4). Never substitute neighbouring text.
    excerpt             TEXT NOT NULL DEFAULT '',
    -- The original node types are never dropped (WIKI_DOMAIN.md 3.4 rule 1); an unrecognised type
    -- keeps its raw value and sets supported=false with a reason (3.4 rules 3 and 5).
    raw_node_types      TEXT[] NOT NULL DEFAULT '{}',
    supported           BOOLEAN NOT NULL DEFAULT TRUE,
    unsupported_reason  VARCHAR(64),
    -- Fingerprint degradation when the source snapshot is missing (4.2 rule 1): the fingerprint
    -- then includes run_id and the reference may be reused by that run ONLY.
    run_scoped          BOOLEAN NOT NULL DEFAULT FALSE,
    run_scoped_run_id   VARCHAR(64),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- One reusable reference per space, source version and content span: this is the evidence
    -- identity de-duplication of the P1 acceptance ("two runs, one reference").
    UNIQUE (space_id, evidence_fingerprint),
    CHECK (evidence_ref_id ~ '^wev-[0-9a-f]{24}$'),
    CHECK (space_id ~ '^wsp-[0-9a-f]{24}$'),
    CHECK (evidence_fingerprint ~ '^[a-f0-9]{64}$'),
    CHECK (record_sha256 ~ '^[a-f0-9]{64}$'),
    CHECK (length(btrim(source_ref)) > 0),
    -- An empty string is not a snapshot: normalize '' to SQL NULL before insert, so that
    -- `source_sha256 IS NULL` and the run_scoped rule below stay unambiguous.
    CHECK (source_sha256 IS NULL OR length(btrim(source_sha256)) > 0),
    CHECK (source_snapshot_id IS NULL OR length(btrim(source_snapshot_id)) > 0),
    CHECK (source_publication_id IS NULL OR length(btrim(source_publication_id)) > 0),
    CHECK (document_version IS NULL OR document_version >= 1),
    CHECK (jsonb_typeof(locator) = 'object'),
    CHECK (locator_kind <> 'pdf_region'
           OR (locator ? 'page_no' AND locator ? 'bbox' AND locator ? 'block_id')),
    CHECK (locator_kind <> 'text_span' OR locator ? 'block_id'),
    CHECK (locator_kind NOT IN ('sql_record','sql_aggregate') OR locator ? 'sql_hash'),
    CHECK (NOT ('' = ANY (raw_node_types))),
    CHECK (supported = (unsupported_reason IS NULL)),
    CHECK (supported OR cardinality(raw_node_types) > 0),
    CHECK (run_scoped = (run_scoped_run_id IS NOT NULL)),
    -- Rule 1 as an invariant, both directions: no snapshot => run isolation with the run id
    -- pinned; a snapshot => the run id must NOT be part of the identity (4.2 rule 3).
    CHECK (run_scoped = (source_sha256 IS NULL))
);

-- Provenance mapping (WIKI_DOMAIN.md 4.2 rule 2 / requirement document 5): the original
-- evidence_id stays the per-run occurrence record, and this table maps occurrences to the single
-- reusable reference, optionally to the revision or the assertion that cites it.
-- WIKI_API.md 1 freezes ONE row per (reference, run) -- and that loses nothing here, because
-- datamind/evidence_graph.py derives node ids from content inside a run, so one run can only ever
-- produce one occurrence per fingerprint. Two runs citing the same evidence give one reference
-- row and two rows here (P1 acceptance).
-- `run_id` deliberately has NO foreign key to runs: see the header note. The same applies to
-- `event_id` (trace_events).
CREATE TABLE IF NOT EXISTS wiki_provenance_links (
    provenance_link_id  VARCHAR(64) PRIMARY KEY,
    evidence_ref_id     VARCHAR(64) NOT NULL
                        REFERENCES wiki_evidence_refs(evidence_ref_id) ON DELETE CASCADE,
    run_id              VARCHAR(64) NOT NULL,
    evidence_id         VARCHAR(64) NOT NULL,
    claim_id            VARCHAR(64),
    event_id            VARCHAR(64),
    revision_id         VARCHAR(64) REFERENCES wiki_revisions(revision_id) ON DELETE CASCADE,
    assertion_id        VARCHAR(64) REFERENCES wiki_assertions(assertion_id) ON DELETE CASCADE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (evidence_ref_id, run_id),
    CHECK (provenance_link_id ~ '^wprov-[0-9a-f]{24}$'),
    CHECK (length(btrim(run_id)) > 0),
    -- Two id schemes exist in this database (WIKI_DOMAIN.md 4.2 rule 2): `ev-` + hex16, and the
    -- content id `n-` + 20 hex. The literal is stored as produced and only checked non-blank, so
    -- a future scheme cannot start failing the ingest path.
    CHECK (length(btrim(evidence_id)) > 0),
    CHECK (claim_id IS NULL OR length(btrim(claim_id)) > 0),
    CHECK (event_id IS NULL OR length(btrim(event_id)) > 0),
    -- The link must point at something: a version, an assertion, or both (an assertion always
    -- belongs to a version, so a revision-only link is the version-level citation).
    CHECK (revision_id IS NOT NULL OR assertion_id IS NOT NULL)
);

-- Relations (WIKI_DOMAIN.md 3.2 / 3.3 / 3.4) bound to the version that owns them. Relation types
-- are the UNION of the new evidence_graph vocabulary and the legacy vocabulary of migration 019
-- -- never a third vocabulary -- and `contradicts` is projected as `conflicts` while raw_type
-- keeps the original word (3.3).
-- `basis` records where a projected relation came from, including which of the two conflict paths
-- produced it (see the header); `provenance_incomplete` records what was never persisted and must
-- not be guessed (WIKI_API.md 6). Endpoints are (kind, id) pairs because a relation may join two
-- entries, two assertions, or two evidence references; the pair is format-checked against the
-- kind, and its EXISTENCE is checked by the service in the write transaction (a polymorphic
-- endpoint cannot be a foreign key).
CREATE TABLE IF NOT EXISTS wiki_relations (
    relation_id         VARCHAR(64) PRIMARY KEY,
    entry_id            VARCHAR(64) NOT NULL,
    revision_id         VARCHAR(64) NOT NULL,
    src_kind            VARCHAR(16) NOT NULL CHECK (src_kind IN (
                            'entry','assertion','evidence_ref')),
    src_id              VARCHAR(64) NOT NULL,
    dst_kind            VARCHAR(16) NOT NULL CHECK (dst_kind IN (
                            'entry','assertion','evidence_ref')),
    dst_id              VARCHAR(64) NOT NULL,
    -- NULL means "Wiki does not recognise this type": supported=false, raw_type kept (3.4 rule 3).
    relation_type       VARCHAR(24) CHECK (relation_type IN (
                            'entity_match','version_scope','unit_equivalent','time_overlap',
                            'supports','conflicts','derived_from','retrieved_from','maps_to',
                            'contradicts','verified_by','repaired_from','resolved_by',
                            'measures','applies_to','scoped_by','threshold_of','about',
                            'justified_by','supersedes','restates')),
    -- The original value, which is never dropped: raw_edge_type for a projection (NULL for the
    -- eight Wiki-derived types, which come from no edge at all, 3.3) or the raw node type.
    raw_type            VARCHAR(32),
    origin              VARCHAR(16) NOT NULL CHECK (origin IN ('projected','wiki_derived')),
    -- TRUE for the undirected types of 3.2; such a row is stored in canonical endpoint id order.
    -- NOT named "symmetric": SYMMETRIC is a reserved word in PostgreSQL (BETWEEN SYMMETRIC).
    is_symmetric        BOOLEAN NOT NULL,
    supported           BOOLEAN NOT NULL DEFAULT TRUE,
    unsupported_reason  VARCHAR(64),
    rule_version        VARCHAR(64),
    basis_note          TEXT,
    basis               JSONB,
    derivation          JSONB,
    provenance_incomplete JSONB NOT NULL DEFAULT '{"applies": false}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (entry_id, revision_id)
        REFERENCES wiki_revisions(entry_id, revision_id) ON DELETE CASCADE,
    CHECK (relation_id ~ '^wrel-[0-9a-f]{24}$'),
    CHECK (src_id ~ '^(wiki|wassert|wev)-[0-9a-f]{24}$'),
    CHECK (dst_id ~ '^(wiki|wassert|wev)-[0-9a-f]{24}$'),
    CHECK (split_part(src_id, '-', 1) = CASE src_kind
                                            WHEN 'entry' THEN 'wiki'
                                            WHEN 'assertion' THEN 'wassert'
                                            ELSE 'wev' END),
    CHECK (split_part(dst_id, '-', 1) = CASE dst_kind
                                            WHEN 'entry' THEN 'wiki'
                                            WHEN 'assertion' THEN 'wassert'
                                            ELSE 'wev' END),
    CHECK (NOT (src_kind = dst_kind AND src_id = dst_id)),
    CHECK (is_symmetric = FALSE OR (src_kind, src_id) <= (dst_kind, dst_id)),
    CHECK (relation_type IS NULL OR is_symmetric = (relation_type IN (
              'entity_match','unit_equivalent','time_overlap','conflicts','contradicts'))),
    CHECK (supported = (relation_type IS NOT NULL)),
    CHECK (supported = (unsupported_reason IS NULL)),
    CHECK (supported OR unsupported_reason = 'TYPE_NOT_IN_WIKI_VOCABULARY'),
    -- The origin must match the vocabulary class of 3.2: #1-#13 are projections that keep their
    -- original edge type, #14-#21 are derived and carry a rule version plus a derivation record.
    CHECK (relation_type IS NULL OR (
              (origin = 'projected' AND relation_type IN (
                  'entity_match','version_scope','unit_equivalent','time_overlap','supports',
                  'conflicts','derived_from','retrieved_from','maps_to','contradicts',
                  'verified_by','repaired_from','resolved_by'))
              OR (origin = 'wiki_derived' AND relation_type IN (
                  'measures','applies_to','scoped_by','threshold_of','about','justified_by',
                  'supersedes','restates')))),
    CHECK (raw_type IS NULL OR length(btrim(raw_type)) > 0),
    CHECK (origin <> 'projected' OR raw_type IS NOT NULL),
    CHECK (origin <> 'wiki_derived' OR raw_type IS NULL),
    CHECK (origin <> 'wiki_derived' OR (rule_version IS NOT NULL
                                        AND derivation IS NOT NULL
                                        AND derivation ? 'rule_version')),
    CHECK (basis_note IS NULL OR length(btrim(basis_note)) > 0),
    CHECK (basis IS NULL OR jsonb_typeof(basis) = 'object'),
    CHECK (derivation IS NULL OR jsonb_typeof(derivation) = 'object'),
    CHECK (jsonb_typeof(provenance_incomplete) = 'object'),
    CHECK (provenance_incomplete ? 'applies')
);

-- Append-only review and publication record (WIKI_API.md 5.13 / 5.14). Both actions live here
-- because the requirement document 5 defines this table as "review AND publication records": a
-- publication is the `publish` action and carries the expected head it CAS-ed against.
-- `actor_user_id` deliberately has NO foreign key to auth_users: this is an audit trail, and
-- deleting an account must not erase who reviewed what (migration 011 stores registrar ids the
-- same way). The actor is re-validated by the service inside the write transaction.
CREATE TABLE IF NOT EXISTS wiki_review_events (
    review_event_id     VARCHAR(64) PRIMARY KEY,
    entry_id            VARCHAR(64) NOT NULL,
    revision_id         VARCHAR(64) NOT NULL,
    action              VARCHAR(24) NOT NULL CHECK (action IN (
                            'submit_review','approve_facts','reject_facts','request_changes',
                            'publish')),
    actor_user_id       VARCHAR(64) NOT NULL,
    -- Server-written only: WIKI_API.md 5.13 rule 3 refuses a client-supplied time.
    occurred_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- The head the caller expected. NULL is meaningful for the first publication, so "the field
    -- was omitted" cannot be distinguished here -- the request model refuses an omitted value.
    expected_head_revision_id VARCHAR(64),
    -- The head actually observed when the event was written; it equals the expected head only
    -- when the CAS passed. WIKI_API.md 5.14 exposes it as publication.previous_head_revision_id.
    based_on_head_revision_id VARCHAR(64),
    -- WIKI_API.md 5.13 scope: {"assertion_ids": ["wassert-..."], "full_revision": false}.
    scope               JSONB NOT NULL DEFAULT '{}'::jsonb,
    verdict_assertion   VARCHAR(16) CHECK (verdict_assertion IN (
                            'unverified','verified','insufficient','conflicted')),
    -- WIKI_API.md 5.13 basis: {"rule_version": ..., "method": ..., "coverage": ...,
    -- "evidence_ref_ids": [...]}.
    basis               JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Snapshot of the conflicting assertions a publication knowingly published (5.14 rule 5): a
    -- publication may carry unresolved conflicts, but it must list them.
    conflicted_assertions TEXT[] NOT NULL DEFAULT '{}',
    note                TEXT,
    FOREIGN KEY (entry_id, revision_id)
        REFERENCES wiki_revisions(entry_id, revision_id) ON DELETE CASCADE,
    CHECK (review_event_id ~ '^wevt-[0-9a-f]{24}$'),
    CHECK (length(btrim(actor_user_id)) > 0),
    CHECK (expected_head_revision_id IS NULL
           OR expected_head_revision_id ~ '^wrev-[0-9a-f]{24}$'),
    CHECK (based_on_head_revision_id IS NULL
           OR based_on_head_revision_id ~ '^wrev-[0-9a-f]{24}$'),
    CHECK (jsonb_typeof(scope) = 'object'),
    CHECK (jsonb_typeof(basis) = 'object'),
    -- WIKI_API.md 5.13 rule 3: verdict=verified needs the scope of the verification (the object
    -- is this event, the operator is actor_user_id and the time is occurred_at).
    CHECK (verdict_assertion <> 'verified' OR (basis ? 'rule_version'
                                               AND basis ? 'method'
                                               AND basis ? 'coverage')),
    CHECK (cardinality(conflicted_assertions) = 0
           OR array_to_string(conflicted_assertions, ',')
              ~ '^(wassert-[0-9a-f]{24})(,wassert-[0-9a-f]{24})*$'),
    CHECK (note IS NULL OR length(btrim(note)) > 0)
);

-- Idempotent build request (WIKI_API.md 5.9 / 5.10 / 5.11). The idempotency key is
-- (owner_id, request_id): the same key with the same canonical payload returns the first result,
-- the same key with a DIFFERENT payload must be refused (409 WIKI_IDEMPOTENCY_CONFLICT) -- which
-- is why the payload columns of a stored request can never be rewritten (WIKI3 trigger below) --
-- and the same request_id under another owner stays indistinguishable from a missing task.
-- `run_id` has no foreign key to runs, and `target_entry_id` / `result_entry_id` /
-- `candidate_entry_id` / `result_revision_id` release their pointer when the target disappears,
-- so an account or entry cleanup cannot be blocked by a job row.
-- suggested_merges stays a suggestion: "close names only ever produce a merge SUGGESTION"
-- (WIKI_DOMAIN.md 2.1) and each entry carries requires_human_confirmation=true.
CREATE TABLE IF NOT EXISTS wiki_build_jobs (
    build_id            VARCHAR(64) PRIMARY KEY,
    space_id            VARCHAR(64) NOT NULL,
    owner_id            VARCHAR(64) NOT NULL REFERENCES auth_users(user_id) ON DELETE CASCADE,
    request_id          VARCHAR(64) NOT NULL,
    -- sha256 over the canonical JSON of the request (run_id, target, hint, mode, selection).
    payload_sha256      CHAR(64) NOT NULL,
    run_id              VARCHAR(64) NOT NULL,
    mode                VARCHAR(24) NOT NULL DEFAULT 'template' CHECK (mode IN (
                            'template','template+polish')),
    selection           JSONB NOT NULL DEFAULT '{}'::jsonb,
    entry_type_hint     VARCHAR(24) CHECK (entry_type_hint IN (
                            'business_entity','metric','norm','conclusion')),
    target_entry_id     VARCHAR(64) REFERENCES wiki_entries(entry_id) ON DELETE SET NULL,
    status              VARCHAR(16) NOT NULL DEFAULT 'queued' CHECK (status IN (
                            'queued','running','succeeded','failed')),
    attempts            INT NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    result_entry_id     VARCHAR(64) REFERENCES wiki_entries(entry_id) ON DELETE SET NULL,
    candidate_entry_id  VARCHAR(64) REFERENCES wiki_entries(entry_id) ON DELETE SET NULL,
    result_revision_id  VARCHAR(64) REFERENCES wiki_revisions(revision_id) ON DELETE SET NULL,
    suggested_merges    JSONB NOT NULL DEFAULT '[]'::jsonb,
    error_code          VARCHAR(64),
    error_detail        TEXT,
    retry_of_build_id   VARCHAR(64) REFERENCES wiki_build_jobs(build_id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (owner_id, request_id),
    CHECK (build_id ~ '^wjob-[0-9a-f]{24}$'),
    CHECK (space_id ~ '^wsp-[0-9a-f]{24}$'),
    CHECK (length(btrim(request_id)) > 0),
    CHECK (length(request_id) <= 64),
    CHECK (payload_sha256 ~ '^[a-f0-9]{64}$'),
    CHECK (length(btrim(run_id)) > 0),
    CHECK (jsonb_typeof(selection) = 'object'),
    CHECK (jsonb_typeof(suggested_merges) = 'array'),
    CHECK (status <> 'failed' OR error_code IS NOT NULL),
    CHECK (status <> 'succeeded' OR result_revision_id IS NOT NULL),
    -- Error codes come from the WIKI_* table of WIKI_API.md 8; the list itself is not frozen
    -- here because it grows, the naming rule is.
    CHECK (error_code IS NULL OR error_code ~ '^WIKI_[A-Z0-9_]+$'),
    CHECK (error_detail IS NULL OR length(btrim(error_detail)) > 0),
    CHECK (retry_of_build_id IS NULL OR retry_of_build_id <> build_id),
    CHECK (updated_at >= created_at)
);

-- ---------- 2. privacy: no Wiki table is readable by PUBLIC or readonly_user ----------
-- Deliberately NOT wrapped in an ownership guard, copying 005:50 / 011:36 / 012:50 / 013:17 /
-- 020:78. These eight tables are created by this very file, so `datamind_app` is their owner and
-- this statement cannot fail on a normal replay. If a table with one of these names already
-- exists and is NOT owned by the current role, then the CREATE above was a no-op, the table was
-- NOT created here, and silently skipping the REVOKE would leave it readable by the read-only
-- query role (possibly through the default privileges of deploy/init/02_metadata.sql:312-313).
-- For a security statement, aborting the batch is the fail-closed behaviour; the ownership guard
-- of section 4 is for statements whose failure is only an operational inconvenience.
REVOKE ALL ON wiki_entries, wiki_revisions, wiki_assertions, wiki_evidence_refs,
              wiki_provenance_links, wiki_relations, wiki_review_events, wiki_build_jobs
    FROM PUBLIC, readonly_user;

-- ---------- 3. append-only guard functions ----------
-- Declared BEFORE the guarded DO blocks of section 4, which create the triggers that call them,
-- because CREATE TRIGGER resolves the function immediately. They touch no table themselves, so a
-- replay simply replaces them in place and ownership guarding is unnecessary here; they run only
-- through triggers, and those are created only when this role owns their table.
-- Custom SQLSTATEs: WIKI1 revision, WIKI2 review event, WIKI3 build request, WIKI4 assertion.
CREATE OR REPLACE FUNCTION wiki_revisions_append_only() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    IF NEW.entry_id IS DISTINCT FROM OLD.entry_id
       OR NEW.base_revision_id IS DISTINCT FROM OLD.base_revision_id
       OR NEW.title IS DISTINCT FROM OLD.title
       OR NEW.body_markdown IS DISTINCT FROM OLD.body_markdown
       OR NEW.body_sha256 IS DISTINCT FROM OLD.body_sha256
       OR NEW.citations IS DISTINCT FROM OLD.citations
       OR NEW.author_id IS DISTINCT FROM OLD.author_id
       OR NEW.generation_method IS DISTINCT FROM OLD.generation_method
       OR NEW.template_version IS DISTINCT FROM OLD.template_version
       OR NEW.model_id IS DISTINCT FROM OLD.model_id
       OR NEW.change_note IS DISTINCT FROM OLD.change_note
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION USING ERRCODE = 'WIKI1',
            MESSAGE = 'wiki_revisions is append-only: revision ' || OLD.revision_id
                      || ' cannot be overwritten in place; create a new revision with an explicit base_revision_id';
    END IF;
    RETURN NEW;
END
$fn$;

CREATE OR REPLACE FUNCTION wiki_assertions_content_immutable() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    IF NEW.revision_id IS DISTINCT FROM OLD.revision_id
       OR NEW.entry_id IS DISTINCT FROM OLD.entry_id
       OR NEW.comparison_key IS DISTINCT FROM OLD.comparison_key
       OR NEW.entity_key IS DISTINCT FROM OLD.entity_key
       OR NEW.predicate IS DISTINCT FROM OLD.predicate
       OR NEW.raw_value IS DISTINCT FROM OLD.raw_value
       OR NEW.raw_unit IS DISTINCT FROM OLD.raw_unit
       OR NEW.normalized_value IS DISTINCT FROM OLD.normalized_value
       OR NEW.normalized_unit IS DISTINCT FROM OLD.normalized_unit
       OR NEW.time_scope IS DISTINCT FROM OLD.time_scope
       OR NEW.applicability IS DISTINCT FROM OLD.applicability
       OR NEW.record_scope IS DISTINCT FROM OLD.record_scope
       OR NEW.statistic IS DISTINCT FROM OLD.statistic
       OR NEW.evidence_refs IS DISTINCT FROM OLD.evidence_refs
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION USING ERRCODE = 'WIKI4',
            MESSAGE = 'wiki_assertions content is immutable: assertion ' || OLD.assertion_id
                      || ' cannot be rewritten; a human change to a value, unit, scope or reference creates a new revision that must be re-verified';
    END IF;
    RETURN NEW;
END
$fn$;

CREATE OR REPLACE FUNCTION wiki_review_events_append_only() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    RAISE EXCEPTION USING ERRCODE = 'WIKI2',
        MESSAGE = 'wiki_review_events is append-only: review event ' || OLD.review_event_id
                  || ' cannot be ' || lower(TG_OP) || 'd; append a new event instead';
END
$fn$;

CREATE OR REPLACE FUNCTION wiki_build_jobs_request_immutable() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    IF NEW.owner_id IS DISTINCT FROM OLD.owner_id
       OR NEW.space_id IS DISTINCT FROM OLD.space_id
       OR NEW.request_id IS DISTINCT FROM OLD.request_id
       OR NEW.payload_sha256 IS DISTINCT FROM OLD.payload_sha256
       OR NEW.run_id IS DISTINCT FROM OLD.run_id
       OR NEW.mode IS DISTINCT FROM OLD.mode
       OR NEW.selection IS DISTINCT FROM OLD.selection
       OR NEW.entry_type_hint IS DISTINCT FROM OLD.entry_type_hint
       OR NEW.target_entry_id IS DISTINCT FROM OLD.target_entry_id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION USING ERRCODE = 'WIKI3',
            MESSAGE = 'wiki_build_jobs request identity is immutable: build ' || OLD.build_id
                      || ' describes the payload of ' || OLD.request_id
                      || '; a different payload needs a new request_id (409 WIKI_IDEMPOTENCY_CONFLICT)';
    END IF;
    RETURN NEW;
END
$fn$;

-- ---------- 4. indexes / constraints / triggers / comments, only on a table this role owns
--              (the guard form of 019:103-129) ----------
-- Nothing is granted to anyone: these tables are private, so there is no GRANT SELECT here, in
-- contrast to the public evidence tables of 019.

-- 4.1 wiki_entries
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_tables
               WHERE schemaname = 'public' AND tablename = 'wiki_entries'
                 AND tableowner = current_user) THEN
        -- Catalogue listing and keyset paging: the sort always carries the unique tiebreaker.
        CREATE INDEX IF NOT EXISTS wiki_entries_space_updated_idx
            ON wiki_entries(space_id, updated_at DESC, entry_id DESC);
        -- WIKI_API.md 5.1 filters the catalogue by publication status only (never by assertion
        -- status), which is exactly this index.
        CREATE INDEX IF NOT EXISTS wiki_entries_space_status_idx
            ON wiki_entries(space_id, publication_status, updated_at DESC, entry_id DESC);
        -- One space per user in the first version, so owner is the entry point for ownership.
        CREATE INDEX IF NOT EXISTS wiki_entries_owner_idx ON wiki_entries(owner_id);
        -- Publication head CAS (WIKI_API.md 5.14 rule 2): the head can only be a revision of the
        -- same entry. Added here rather than inline because wiki_revisions must exist first; the
        -- existence test keeps the replay idempotent (PostgreSQL has no ADD CONSTRAINT IF NOT
        -- EXISTS).
        IF NOT EXISTS (SELECT 1 FROM pg_constraint
                       WHERE conrelid = 'public.wiki_entries'::regclass
                         AND conname = 'wiki_entries_head_revision_fk') THEN
            ALTER TABLE wiki_entries ADD CONSTRAINT wiki_entries_head_revision_fk
                FOREIGN KEY (entry_id, head_revision_id)
                REFERENCES wiki_revisions(entry_id, revision_id);
        END IF;
        COMMENT ON TABLE wiki_entries IS
            'Wiki entry identity (business_key = space_id|namespace|entry_type|key_part, unique) plus the current publication head; title/aliases/category/summary are the current published projection for the catalogue, not the version history. Private: revoked from PUBLIC and readonly_user';
        COMMENT ON COLUMN wiki_entries.space_id IS
            'Space of the entry (wsp- + hex24); the first segment of the business key, so identities never merge across users';
        COMMENT ON COLUMN wiki_entries.key_part IS
            'entry_type-specific key: entity_type + US + canonical key, or pending: + lower(title); metric_id; source_document_id + US + source_revision + US + clause_key; run_id + US + claim_id. A pending: key can never be stored as confirmed';
        COMMENT ON COLUMN wiki_entries.confirmation IS
            'confirmed | pending. pending entries may be drafted but can never be published (409 WIKI_ENTRY_IDENTITY_PENDING); promotion happens only through an approved alias or a human confirmation recorded in wiki_review_events';
        COMMENT ON COLUMN wiki_entries.publication_status IS
            'Process state only (draft | in_review | published | archived). NEVER derived from, and never a substitute for, the assertion status of wiki_assertions or the read-time source validity';
        COMMENT ON COLUMN wiki_entries.head_revision_id IS
            'Current publication head; NULL means nothing was published yet. The composite FK wiki_entries_head_revision_fk makes it impossible to point at a revision of another entry. Publications are compare-and-swap: a stale expected head is 409 WIKI_VERSION_CONFLICT and must not move this column';
        COMMENT ON COLUMN wiki_entries.archived_at IS
            'Archive stamp; set exactly when publication_status = archived. Archiving is the read-only path -- entries are not hard deleted';
        COMMENT ON COLUMN wiki_entries.demo_data IS
            'TRUE for the synthetic demonstration corpus of this database; must be rendered and exported, never silently dropped';
    END IF;
END
$$;

-- 4.2 wiki_revisions
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_tables
               WHERE schemaname = 'public' AND tablename = 'wiki_revisions'
                 AND tableowner = current_user) THEN
        -- Version history (WIKI_API.md 5.4) newest first, with the unique tiebreaker required for
        -- a stable cursor.
        CREATE INDEX IF NOT EXISTS wiki_revisions_entry_created_idx
            ON wiki_revisions(entry_id, created_at DESC, revision_id DESC);
        -- Baseline lineage: which revisions were created on top of one revision.
        CREATE INDEX IF NOT EXISTS wiki_revisions_base_idx
            ON wiki_revisions(base_revision_id) WHERE base_revision_id IS NOT NULL;
        -- Explicit baseline (WIKI_DOMAIN.md 6.1): a revision can only be based on a revision of
        -- the SAME entry, and never on itself.
        IF NOT EXISTS (SELECT 1 FROM pg_constraint
                       WHERE conrelid = 'public.wiki_revisions'::regclass
                         AND conname = 'wiki_revisions_base_revision_fk') THEN
            ALTER TABLE wiki_revisions ADD CONSTRAINT wiki_revisions_base_revision_fk
                FOREIGN KEY (entry_id, base_revision_id)
                REFERENCES wiki_revisions(entry_id, revision_id);
        END IF;
        -- Append-only body, citations and baseline (WIKI_DOMAIN.md 6.1 / WIKI_API.md 5.12 rule 3).
        -- A saved revision may change only publication_status, which is a different dimension.
        DROP TRIGGER IF EXISTS wiki_revisions_append_only_trg ON wiki_revisions;
        CREATE TRIGGER wiki_revisions_append_only_trg
            BEFORE UPDATE ON wiki_revisions
            FOR EACH ROW EXECUTE FUNCTION wiki_revisions_append_only();
        COMMENT ON TABLE wiki_revisions IS
            'Immutable entry version: a new edit inserts a new row with an explicit base_revision_id, and body_markdown / citations of a stored revision can never be overwritten in place (SQLSTATE WIKI1). Read a version with the values of THAT row: the entry row only carries the current projection';
        COMMENT ON COLUMN wiki_revisions.base_revision_id IS
            'Explicit baseline; NULL only for the first version. There is no implicit base on the current head. The composite FK makes a cross-entry baseline impossible and base_revision_id <> revision_id is enforced';
        COMMENT ON COLUMN wiki_revisions.body_sha256 IS
            'sha256 of body_markdown, so a client can tell whether two versions really differ (WIKI_API.md 5.4) and so a fixed-version read can verify expected_body_sha256 (409 WIKI_VERSION_CONFLICT on mismatch)';
        COMMENT ON COLUMN wiki_revisions.citations IS
            'Citation list [{marker, evidence_ref_id}] as a JSONB array (a separate citation table is out of the eight-table P1 scope). Part of the immutable payload; the service checks that every evidence_ref_id exists in the same space before insert. Note: SQL NULL never appears here -- an empty list is [] and a JSON null literal is refused, so IS NOT NULL is unambiguous';
        COMMENT ON COLUMN wiki_revisions.generation_method IS
            'template | human | model_polish. Automatically generated content enters as a draft only (never auto-published), and model_polish is the only mode that may carry model_id';
        COMMENT ON COLUMN wiki_revisions.publication_status IS
            'This version own lifecycle state, the only mutable column of the row. Kept consistent with wiki_entries.publication_status by the service in one transaction; the database does not derive one from the other';
    END IF;
END
$$;

-- 4.3 wiki_assertions
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_tables
               WHERE schemaname = 'public' AND tablename = 'wiki_assertions'
                 AND tableowner = current_user) THEN
        CREATE INDEX IF NOT EXISTS wiki_assertions_revision_idx
            ON wiki_assertions(revision_id);
        -- The comparison group of WIKI_DOMAIN.md 4.3: only assertions sharing a comparison_key
        -- may ever be weighed against each other (same metric with another time or scope is not
        -- a conflict at all).
        CREATE INDEX IF NOT EXISTS wiki_assertions_comparison_idx
            ON wiki_assertions(comparison_key, revision_id);
        -- "which assertions of this entry are conflicted / insufficient" for the entry object.
        CREATE INDEX IF NOT EXISTS wiki_assertions_entry_status_idx
            ON wiki_assertions(entry_id, assertion_status);
        -- Only the verification columns may change; the content is as immutable as the revision
        -- it belongs to.
        DROP TRIGGER IF EXISTS wiki_assertions_content_immutable_trg ON wiki_assertions;
        CREATE TRIGGER wiki_assertions_content_immutable_trg
            BEFORE UPDATE ON wiki_assertions
            FOR EACH ROW EXECUTE FUNCTION wiki_assertions_content_immutable();
        COMMENT ON TABLE wiki_assertions IS
            'Structured assertion of a revision: comparison identity (comparison_key over space/entity/predicate/applicability/time_scope/record_scope/statistic) plus its own verification accounting. Content columns are append-only (SQLSTATE WIKI4); assertion_status, verified_* and verification_* are written only through a review event';
        COMMENT ON COLUMN wiki_assertions.comparison_key IS
            'Bare lowercase sha256 hex WITHOUT the sha256: prefix that the API shows; the service adds the prefix. Computed over the canonical JSON of WIKI_DOMAIN.md 4.3, so two runs asserting the same claim under different run_ids share this key and can be linked with restates';
        COMMENT ON COLUMN wiki_assertions.entity_key IS
            'JSON object, e.g. {"part_id": "A102", "batch_id": "B001", "confirmation": "pending"}; the pending / confirmed marker of the entity identity is carried here as well as on the entry';
        COMMENT ON COLUMN wiki_assertions.raw_unit IS
            'Raw unit as written in the source. raw vs normalized units may differ: two sides with different but convertible units are unit_equivalent / resolved_equivalent and are NOT a value conflict (WIKI_DOMAIN.md 4.3 rule 3)';
        COMMENT ON COLUMN wiki_assertions.time_scope IS
            'Nullable JSON object {from, to, timezone}. SQL NULL is the only unset representation -- a JSON null literal is refused by CHECK, so IS NULL / IS NOT NULL is unambiguous';
        COMMENT ON COLUMN wiki_assertions.applicability IS
            'Nullable JSON object {source_revision, valid_from, valid_to}. SQL NULL is the only unset representation (JSON null literal refused); overlapping validity of two norms is a version_overlap conflict, not a silent pick of the newer one';
        COMMENT ON COLUMN wiki_assertions.record_scope IS
            'Nullable JSON object {measurement_id, batch_id, operation_no, ...}. SQL NULL is the only unset representation (JSON null literal refused). Different named records do not pair, so they never conflict';
        COMMENT ON COLUMN wiki_assertions.statistic IS
            'Statistic / aggregation method (WIKI_DOMAIN.md 4.3 statistic); NULL when the source declares none. Canonicalize between units before hashing';
        COMMENT ON COLUMN wiki_assertions.assertion_status IS
            'unverified | verified | insufficient | conflicted (WIKI_DOMAIN.md 5.2). Independent dimension: never derived from publication_status and never from the read-time source validity. verified additionally requires method, coverage, rule version, operator, time and the five accounting keys';
        COMMENT ON COLUMN wiki_assertions.verification_accounting IS
            'The five separately recorded items of WIKI_API.md 7.2. semantic_support is always {"checked": false, "full_semantic_entailment": "not_independently_verified"} because that literal is hard-coded in datamind/agent.py:238 and :629 -- Wiki must not rewrite it as verified';
    END IF;
END
$$;

-- 4.4 wiki_evidence_refs
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_tables
               WHERE schemaname = 'public' AND tablename = 'wiki_evidence_refs'
                 AND tableowner = current_user) THEN
        -- Reverse index of requirement document 6 line 139: from a source (and its snapshot) to
        -- every reference that used it, which is how a source revision change is located.
        CREATE INDEX IF NOT EXISTS wiki_evidence_refs_source_idx
            ON wiki_evidence_refs(space_id, source_kind, source_ref, source_sha256);
        COMMENT ON TABLE wiki_evidence_refs IS
            'Reusable evidence reference: one row per (space, fingerprint) however many runs cite it -- the fingerprint covers space, source identity, source version, locator and content digest. Stale and unavailable are NOT stored: source validity is re-checked on every read and never cached (WIKI_API.md 7.5)';
        COMMENT ON COLUMN wiki_evidence_refs.source_sha256 IS
            'Snapshot / document digest used in the fingerprint. Free-form snapshot identifier, not always 64 hex (SQL snapshots look like snap-demo_retail-9b6637f76e61). Document evidence must use COALESCE(payload->>document_sha256, payload->>source_sha256) because the two fields are mutually exclusive in this database. Normalize an empty string to SQL NULL: run_scoped is defined as source_sha256 IS NULL';
        COMMENT ON COLUMN wiki_evidence_refs.evidence_fingerprint IS
            'sha256 over the canonical JSON of WIKI_DOMAIN.md 4.2 (space_id, source_kind, source_ref, source_sha256, locator_kind, locator, record_sha256), plus run_id in the run_scoped degradation. Bare hex; the API adds the sha256: prefix';
        COMMENT ON COLUMN wiki_evidence_refs.run_scoped IS
            'TRUE exactly when the source snapshot is missing (source_sha256 IS NULL): the fingerprint then contains run_id, and run_scoped_run_id is mandatory, so the reference may be reused by that run ONLY and never across runs (WIKI_DOMAIN.md 4.2 rule 1)';
        COMMENT ON COLUMN wiki_evidence_refs.locator IS
            'Immutable locator snapshot bound to the captured source version; required keys depend on locator_kind (pdf_region: page_no, bbox, block_id; text_span: block_id; sql_record / sql_aggregate: sql_hash). A read must return THIS locator plus a re-validation hint -- never silently follow the newest document or the newest SQL data';
        COMMENT ON COLUMN wiki_evidence_refs.excerpt IS
            'Captured source text. Empty means empty: when retrieval produced no quotable text the excerpt stays empty and that fact stays visible; neighbouring text or a summary must never be substituted';
        COMMENT ON COLUMN wiki_evidence_refs.raw_node_types IS
            'Original node types, never dropped (WIKI_DOMAIN.md 3.4 rule 1). When Wiki does not recognise them, supported = FALSE with unsupported_reason, and the raw value is still shown';
    END IF;
END
$$;

-- 4.5 wiki_provenance_links
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_tables
               WHERE schemaname = 'public' AND tablename = 'wiki_provenance_links'
                 AND tableowner = current_user) THEN
        -- Run trajectory (WIKI_API.md 5.7): all evidence of one run.
        CREATE INDEX IF NOT EXISTS wiki_provenance_links_run_idx
            ON wiki_provenance_links(run_id, evidence_ref_id);
        CREATE INDEX IF NOT EXISTS wiki_provenance_links_revision_idx
            ON wiki_provenance_links(revision_id) WHERE revision_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS wiki_provenance_links_assertion_idx
            ON wiki_provenance_links(assertion_id) WHERE assertion_id IS NOT NULL;
        COMMENT ON TABLE wiki_provenance_links IS
            'Mapping between a reusable evidence reference and the occurrence records of runs: exactly one row per (evidence_ref_id, run_id), so two runs citing the same evidence produce one reference and two links. run_id and event_id have NO foreign key on purpose -- runs and trace_events are owned by postgres and must not be locked or altered by this migration; existence is checked by the service (404 WIKI_RUN_NOT_FOUND). The error summary of the run is read live from trace_events and never cached here';
        COMMENT ON COLUMN wiki_provenance_links.evidence_id IS
            'The original per-run occurrence id as written by datamind/evidence_graph.py (ev- + hex16, or the content id n- + 20 hex). Stored verbatim, only checked non-blank';
        COMMENT ON COLUMN wiki_provenance_links.event_id IS
            'Optional trace_events.event_id that attached this evidence. No foreign key: trace_events belongs to postgres';
        COMMENT ON COLUMN wiki_provenance_links.revision_id IS
            'Version-level citation. At least one of revision_id / assertion_id must be set, so a link can never point at nothing';
    END IF;
END
$$;

-- 4.6 wiki_relations
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_tables
               WHERE schemaname = 'public' AND tablename = 'wiki_relations'
                 AND tableowner = current_user) THEN
        -- One stored relation per (version, endpoints, type). COALESCE keeps the unknown-type
        -- rows deduplicated too, where relation_type is NULL and raw_type carries the value.
        CREATE UNIQUE INDEX IF NOT EXISTS wiki_relations_identity_idx
            ON wiki_relations(revision_id, src_kind, src_id, dst_kind, dst_id, origin,
                              COALESCE(relation_type, ''), COALESCE(raw_type, ''));
        CREATE INDEX IF NOT EXISTS wiki_relations_revision_type_idx
            ON wiki_relations(revision_id, relation_type);
        -- Both directions of the neighbourhood query of WIKI_API.md 5.6.
        CREATE INDEX IF NOT EXISTS wiki_relations_src_idx ON wiki_relations(src_kind, src_id);
        CREATE INDEX IF NOT EXISTS wiki_relations_dst_idx ON wiki_relations(dst_kind, dst_id);
        COMMENT ON TABLE wiki_relations IS
            'Relation between two knowledge objects of one version. relation_type is the union of the evidence_graph vocabulary and the legacy vocabulary of migration 019 -- never a third vocabulary; contradicts is projected as conflicts with the original word kept in raw_type. CONFLICT SOURCES ARE TWO AND DO NOT MEET (WIKI_DOMAIN.md 3.5): the industrial path writes the conflict_records table, the cross-source path writes NOTHING to any table and only reaches trace_events.output_summary.cross_source_result, so basis records source_path = industrial_table | cross_source_trace and a projector must read both. An unknown type keeps raw_type, gets relation_type NULL, supported = FALSE and unsupported_reason = TYPE_NOT_IN_WIKI_VOCABULARY -- it is still displayed and never guessed into the nearest known type';
        COMMENT ON COLUMN wiki_relations.src_id IS
            'Endpoint id whose prefix must match src_kind: wiki- + hex24 (entry), wassert- + hex24 (assertion), wev- + hex24 (evidence reference). The existence of the endpoint is checked by the service in the write transaction: a polymorphic endpoint cannot be a foreign key';
        COMMENT ON COLUMN wiki_relations.origin IS
            'projected (#1-#13: from an existing edge, raw_type kept, and a missing edge justification is recorded as provenance_incomplete) or wiki_derived (#14-#21: derived by Wiki from actual fields, never from an edge, with rule_version and derivation.rule_version)';
        COMMENT ON COLUMN wiki_relations.is_symmetric IS
            'TRUE for the undirected types of WIKI_DOMAIN.md 3.2 (entity_match, unit_equivalent, time_overlap, conflicts, contradicts); such a row is stored in canonical endpoint id order, which the CHECK enforces. A self-loop is refused';
        COMMENT ON COLUMN wiki_relations.basis IS
            'Nullable JSON object with the basis snapshot of the relation (source table / edge id / run id, or the derivation inputs). SQL NULL is the only unset representation (JSON null literal refused)';
        COMMENT ON COLUMN wiki_relations.derivation IS
            'Mandatory for origin = wiki_derived: {rule_version: wiki-derive-v1, inputs: [...]} so a historical version can reproduce the derivation. Nullable for projections; SQL NULL is the only unset representation (JSON null literal refused)';
        COMMENT ON COLUMN wiki_relations.provenance_incomplete IS
            'NOT NULL JSON object, always carrying applies. {"applies": false} means the projection carried complete metadata; {"applies": true, missing_fields: [...], reason_code: ..., recoverable: false} means fields that existed were never persisted (for example evidence / binding_type / confidence, absent from the deployed evidence_edges) and must NOT be inferred (WIKI_API.md 6). Never SQL NULL and never a JSON null, unlike conflict_records.selected_fact_ref where all 175 rows store a JSON null and IS NOT NULL matches every row';
    END IF;
END
$$;

-- 4.7 wiki_review_events
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_tables
               WHERE schemaname = 'public' AND tablename = 'wiki_review_events'
                 AND tableowner = current_user) THEN
        CREATE INDEX IF NOT EXISTS wiki_review_events_revision_idx
            ON wiki_review_events(revision_id, occurred_at, review_event_id);
        CREATE INDEX IF NOT EXISTS wiki_review_events_entry_idx
            ON wiki_review_events(entry_id, occurred_at DESC, review_event_id DESC);
        -- Append-only audit trail: no UPDATE, no DELETE (WIKI_API.md 5.13 rule 1). Consequence
        -- stated honestly: once a revision has a review event it cannot be hard deleted, so an
        -- entry with review history is archived rather than removed; bulk cleanup of review
        -- history is TRUNCATE (which does not fire row triggers) or a deliberate owner action.
        DROP TRIGGER IF EXISTS wiki_review_events_append_only_trg ON wiki_review_events;
        CREATE TRIGGER wiki_review_events_append_only_trg
            BEFORE UPDATE OR DELETE ON wiki_review_events
            FOR EACH ROW EXECUTE FUNCTION wiki_review_events_append_only();
        COMMENT ON TABLE wiki_review_events IS
            'Append-only review AND publication record: actions submit_review / approve_facts / reject_facts / request_changes (WIKI_API.md 5.13) plus publish (5.14). Rows can be inserted only -- UPDATE and DELETE raise SQLSTATE WIKI2 -- and publication is compare-and-swap on expected_head_revision_id: a stale expectation is 409 WIKI_VERSION_CONFLICT and the head must not move. Publishing an entry whose confirmation is pending must be refused, and unresolved conflicts may be published only with both sides shown and listed in conflicted_assertions';
        COMMENT ON COLUMN wiki_review_events.actor_user_id IS
            'Operator. No foreign key to auth_users on purpose: an audit trail must survive account deletion (same convention as migration 011 registrar ids); the service re-validates and locks the identity inside the write transaction';
        COMMENT ON COLUMN wiki_review_events.expected_head_revision_id IS
            'The publication head the caller expected; the service compares it with the actual head and refuses a stale baseline. NULL is the state before the first publication, so the request model -- not this column -- must refuse an omitted value';
        COMMENT ON COLUMN wiki_review_events.based_on_head_revision_id IS
            'The head actually observed when the event was written (WIKI_API.md 5.14 previous_head_revision_id); equals the expected head only when the CAS passed';
        COMMENT ON COLUMN wiki_review_events.basis IS
            'Verification basis {rule_version, method, coverage, evidence_ref_ids}. A verdict of verified requires rule_version, method and coverage here plus the operator and server time of the row. A review must never auto-activate a source authority rule (WIKI_API.md 5.13 rule 4)';
    END IF;
END
$$;

-- 4.8 wiki_build_jobs
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_tables
               WHERE schemaname = 'public' AND tablename = 'wiki_build_jobs'
                 AND tableowner = current_user) THEN
        CREATE INDEX IF NOT EXISTS wiki_build_jobs_owner_created_idx
            ON wiki_build_jobs(owner_id, created_at DESC, build_id DESC);
        -- Worker poll path: only pending work is indexed.
        CREATE INDEX IF NOT EXISTS wiki_build_jobs_pending_idx
            ON wiki_build_jobs(status, created_at) WHERE status IN ('queued','running');
        CREATE INDEX IF NOT EXISTS wiki_build_jobs_run_idx ON wiki_build_jobs(run_id);
        -- The canonical request of an idempotency key can never change: otherwise the same
        -- (owner_id, request_id) could silently start describing another payload instead of
        -- answering 409 WIKI_IDEMPOTENCY_CONFLICT (WIKI_API.md 5.9).
        DROP TRIGGER IF EXISTS wiki_build_jobs_request_immutable_trg ON wiki_build_jobs;
        CREATE TRIGGER wiki_build_jobs_request_immutable_trg
            BEFORE UPDATE ON wiki_build_jobs
            FOR EACH ROW EXECUTE FUNCTION wiki_build_jobs_request_immutable();
        COMMENT ON TABLE wiki_build_jobs IS
            'Idempotent build request. The key is (owner_id, request_id): the same key with the same payload_sha256 returns the first result without creating a second revision, the same key with a DIFFERENT payload must be refused (409 WIKI_IDEMPOTENCY_CONFLICT), and the same request_id under another owner is indistinguishable from a missing task (404 WIKI_BUILD_NOT_FOUND). A failed build must never rewrite runs / claims / trace_events. run_id has no foreign key to runs on purpose -- that table belongs to postgres (see the header)';
        COMMENT ON COLUMN wiki_build_jobs.payload_sha256 IS
            'sha256 of the canonical request JSON. Together with request_id it is immutable (SQLSTATE WIKI3): a stored request can never be re-pointed at another run, mode or selection';
        COMMENT ON COLUMN wiki_build_jobs.attempts IS
            'Attempt counter; incremented by POST /v1/wiki/builds/{build_id}/retry, which must keep the original run, owner and payload -- never re-target the newest run. Only a failed job is retryable (409 WIKI_BUILD_NOT_RETRYABLE)';
        COMMENT ON COLUMN wiki_build_jobs.suggested_merges IS
            'JSONB array of merge SUGGESTIONS; each entry must carry requires_human_confirmation = true. A close name may only produce a suggestion -- it can never change an identity (WIKI_DOMAIN.md 2.1)';
    END IF;
END
$$;
