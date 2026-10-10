-- Typed evidence graph for cross-source answering (WP2).
--
-- Two tables carry the graph produced by `datamind/evidence_graph.py`:
--   * evidence_nodes: one row per piece of evidence. node_type is a closed vocabulary
--     (sql_row / doc_block / measurement / spec_clause); a conclusion or a claim is NOT a node.
--   * evidence_edges: one row per typed relation between two nodes. Every edge must carry
--     non-empty evidence text, and a self-reference is refused.
--
-- Fail-closed invariants the database enforces:
--   * node_type and edge_type are whitelisted;
--   * a *supplied* `evidence` text must be non-blank (`evidence IS NULL OR btrim <> ''`), and
--     `evidence` is nullable — see the compatibility note below;
--   * `src_evidence_id <> dst_evidence_id`, so an edge never relates a node to itself;
--   * `run_id` references runs(run_id), so a graph row always belongs to a real run.
--
-- TWO SHAPES SHARE THESE TABLE NAMES (this is why the whitelists below are the *union*):
-- `deploy/init/02_metadata.sql` already creates `evidence_nodes` / `evidence_edges` with the
-- older, wider vocabulary (`SQLQuery`/`SQLRow`/`Chunk`/`Claim`/`DocumentFact`, edges
-- `derived_from`/`retrieved_from`/`maps_to`/`contradicts`/`verified_by`/`repaired_from`/
-- `resolved_by`) and no CHECK constraints. The SQL/RAG/industrial evidence writers
-- (`datamind/agent.py`) still insert that vocabulary into the same table.
-- Therefore:
--   1. section 1 is `CREATE TABLE IF NOT EXISTS` only. On a database where deploy/init ran first
--      (the normal order) it is a pure no-op — it needs no ownership of the existing table and
--      rewrites nothing, so the legacy rows and the legacy vocabulary keep working;
--   2. `evidence` is NULLable with a "non-blank when supplied" CHECK. This is deliberate: *every*
--      writer in this repository omits the column — `datamind/agent.py` (three INSERTs),
--      `datamind/evidence_graph.py:persist` — because the deployed table has no such column
--      (it has `weight`), so the writers can only use the columns the two shapes share. A
--      `NOT NULL` here would make those writers fail on a database where this migration ran
--      before deploy/init (verified: the pre-fix definition raised
--      `null value in column "evidence" ... violates not-null constraint`). The edge
--      justification is still required at the graph level (`datamind/evidence_graph.py` stores
--      it on the in-memory edge and in the node payload);
--   3. on a fresh database the CHECK constraints accept the union of both vocabularies so the
--      legacy writers do not start failing on a vocabulary violation either;
--   4. section 2 adds indexes, grants and comments only when the current role owns the table
--      (fresh-database case). On the deploy/init shape the tables belong to another role and any
--      `CREATE INDEX` / `COMMENT` / `GRANT` on them would abort the whole migration run, so those
--      statements are guarded instead of left unconditional.
--
-- Migration order, stated honestly:
--   * Nothing in this file was applied to `datamind_meta` by this work package. The deployed
--     order is `deploy/init` first, then `db/migrations/*.sql`; on that order the tables below
--     already exist and section 1 is a pure no-op.
--   * The isolated harness *does* apply every file in `db/migrations/` — not directly, but
--     transitively: `scripts/test_isolated.py` runs `db/seed_industrial.py`, which calls
--     `db/migrate.py`; that module globs `db/migrations/*.sql` and prints `applied <name>`. It
--     applies them to a `pg_dump`/`pg_restore` clone of the **already initialized** database,
--     where `CREATE TABLE IF NOT EXISTS` matches the existing deploy/init tables and the body of
--     section 1 is therefore never exercised. The fresh-columns branch of this file is still not
--     covered by the suite.
--   * That branch was instead verified by hand, once, in a disposable database: a minimal
--     `runs(run_id)` stub, then this file, then the legacy INSERT column lists from
--     `datamind/agent.py` and `datamind/evidence_graph.py`. Before the `evidence` change the
--     third step failed with `null value in column "evidence" ... violates not-null constraint`;
--     after it the rows insert, and a *supplied* blank justification is still refused. The full
--     `deploy/init` script was not replayed there, so "fresh-first + complete deploy/init"
--     remains unverified.

-- ---------- 1. tables (no-op when deploy/init created them first) ----------
CREATE TABLE IF NOT EXISTS evidence_nodes (
    evidence_id     VARCHAR(64) PRIMARY KEY,
    run_id          VARCHAR(64) NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    node_type       VARCHAR(24) NOT NULL CHECK (node_type IN (
                        'sql_row','doc_block','measurement','spec_clause',
                        'Question','PlanTask','SQLQuery','SQLRow','Document','Chunk','TableCell',
                        'Formula','Claim','VerificationCheck','DocumentFact','ConflictRecord',
                        'AuthorityRule')),
    ref_key         VARCHAR(256) NOT NULL,
    source_sha256   CHAR(64),
    version         VARCHAR(128),
    unit            VARCHAR(24),
    time_scope      JSONB,
    binding         JSONB,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (length(btrim(ref_key)) > 0)
);

CREATE TABLE IF NOT EXISTS evidence_edges (
    edge_id         BIGSERIAL PRIMARY KEY,
    run_id          VARCHAR(64) NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    src_evidence_id VARCHAR(64) NOT NULL REFERENCES evidence_nodes(evidence_id) ON DELETE CASCADE,
    dst_evidence_id VARCHAR(64) NOT NULL REFERENCES evidence_nodes(evidence_id) ON DELETE CASCADE,
    edge_type       VARCHAR(24) NOT NULL CHECK (edge_type IN (
                        'entity_match','version_scope','unit_equivalent','time_overlap','supports',
                        'conflicts','derived_from','retrieved_from','maps_to','contradicts',
                        'verified_by','repaired_from','resolved_by')),
    binding_type    VARCHAR(32),
    confidence      NUMERIC(4,3),
    -- Nullable on purpose: the SQL/RAG/industrial writers insert
    -- ``(run_id, src_evidence_id, dst_evidence_id, edge_type)`` only, because the deployed
    -- (deploy/init) table has no ``evidence`` column at all. A NOT NULL here would break them on
    -- the fresh-first branch. A justification that *is* supplied still cannot be blank.
    evidence        TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (src_evidence_id <> dst_evidence_id),
    CHECK (evidence IS NULL OR length(btrim(evidence)) > 0)
);

-- ---------- 2. indexes / grants / comments, only on a table this role owns ----------
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_tables
               WHERE schemaname = 'public' AND tablename = 'evidence_nodes'
                 AND tableowner = current_user) THEN
        CREATE INDEX IF NOT EXISTS idx_evidence_nodes_run ON evidence_nodes(run_id, node_type);
        CREATE INDEX IF NOT EXISTS idx_evidence_nodes_key ON evidence_nodes(run_id, ref_key);
        EXECUTE 'GRANT SELECT ON evidence_nodes TO readonly_user';
        EXECUTE 'COMMENT ON COLUMN evidence_nodes.node_type IS '
             || quote_literal('Closed vocabulary: sql_row/doc_block/measurement/spec_clause; claims are not nodes');
        EXECUTE 'COMMENT ON COLUMN evidence_nodes.binding IS '
             || quote_literal('Seven-field binding record produced by datamind/binding.py');
    END IF;
    IF EXISTS (SELECT 1 FROM pg_tables
               WHERE schemaname = 'public' AND tablename = 'evidence_edges'
                 AND tableowner = current_user) THEN
        CREATE INDEX IF NOT EXISTS idx_evidence_edges_run ON evidence_edges(run_id, edge_type);
        CREATE INDEX IF NOT EXISTS idx_evidence_edges_src ON evidence_edges(src_evidence_id);
        CREATE INDEX IF NOT EXISTS idx_evidence_edges_dst ON evidence_edges(dst_evidence_id);
        EXECUTE 'GRANT SELECT ON evidence_edges TO readonly_user';
        EXECUTE 'COMMENT ON COLUMN evidence_edges.edge_type IS '
             || quote_literal('Closed vocabulary: entity_match/version_scope/unit_equivalent/time_overlap/supports/conflicts');
        EXECUTE 'COMMENT ON COLUMN evidence_edges.evidence IS '
             || quote_literal('Optional justification text; when supplied it must be non-blank. Nullable so the legacy column list (which omits it) keeps working. A self-reference is refused');
        EXECUTE 'COMMENT ON TABLE evidence_edges IS '
             || quote_literal('Typed relation between two evidence nodes; vocabulary and non-empty evidence are enforced');
    END IF;
END
$$;
