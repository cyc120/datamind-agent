"""One publish gate: the Python predicate and the SQL condition must agree, everywhere.

Audit [5.4](</Users/caicai/DataMind Agent/reports/project_audit_20261009_561316c.md>) found
"this document is published" written three times (`api.py`, twice inline in `knowledge.py`,
`authority.py`) and already drifting in shape; R03 is the cost of that drift. This module
holds the acceptance for the unified policy in `datamind.visibility`:

* **equivalence matrix** — every `status` × `acl` × `needs_review` × `collection`
  combination is inserted as a real `document_assets` row and then judged twice: by the
  Python predicate and by the SQL condition executed on PostgreSQL. Both must equal the
  intended result computed independently in this test, and each other. 80 combinations;
* **chunk composition** — `knowledge`'s row read must still AND the *chunk* ACL on top of
  the document gate (that clause is not part of the policy and must not be lost);
* **single definition guard** — no other module may *state* the gate, with the three
  out-of-scope exceptions pinned by name, by statement count and by source signature;
* **non-vacuity** — the guard is fed the exact old spellings and must reject them, and every
  dropped or inverted clause is shown to be visible to the matrix.

Rows are synthetic, live only for the test, and are deleted in teardown.
"""
import hashlib
import io
from pathlib import Path
import re
import tokenize
import unittest
import uuid

import psycopg2
from psycopg2.extras import RealDictCursor

from datamind import visibility
from datamind.agent import _write_dsn
from datamind.knowledge import COLLECTIONS, KnowledgeRetriever

DATAMIND = Path(visibility.__file__).resolve().parent

STATUSES = ("parsing", "parsed", "indexed", "failed", "")
ACLS = (["public"], [], ["private"], ["public", "x"])
REVIEW_FLAGS = (True, False)
ALLOWED_COLLECTION = "demo_kb"
OUTSIDE_COLLECTION = "outside_kb"

#: The status literal as the policy module spells it, with the policy's own constant name
#: expanded, so a definition cannot hide behind an indirection. Both quote styles are
#: folded to the same spelling first.
_STATUS_LITERAL = "'" + visibility.PUBLISHED_STATUS + "'"


def intended(status, acl, needs_review, collection) -> bool:
    """The publish decision, written out once here so the matrix is not self-referential."""
    return (status == visibility.PUBLISHED_STATUS
            and visibility.PUBLIC_ACL in acl
            and not needs_review
            and collection in COLLECTIONS)


def _normalized(source: str) -> str:
    """Fold every spelling of the published status to `'indexed'`."""
    for quoted in ("'{PUBLISHED_STATUS}'", '"{PUBLISHED_STATUS}"'):
        source = source.replace(quoted, _STATUS_LITERAL)
    source = source.replace("PUBLISHED_STATUS", _STATUS_LITERAL)
    return source.replace('"indexed"', _STATUS_LITERAL)


def publish_gate_statements(source: str) -> list[tuple[int, str]]:
    """Logical statements (comments dropped) that mention both gate markers.

    The unit is the *logical statement*, not the physical line: a re-stated gate is
    routinely split over continuation lines, which is exactly how the two `knowledge.py`
    copies hid inside a multi-line SQL string. Comments are dropped so that prose
    *describing* the gate cannot put a module on the review list, while a genuine
    re-statement — one expression mentioning the published status and the review flag —
    cannot avoid detection.
    """
    normalized = _normalized(source)
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(normalized).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return []
    statements, current = [], None
    for token in tokens:
        if token.type == tokenize.COMMENT:
            continue
        if token.type in (tokenize.NEWLINE, tokenize.ENDMARKER):
            if current:
                statements.append((current[0], " ".join(current[1])))
                current = None
        elif token.type in (tokenize.NL, tokenize.INDENT, tokenize.DEDENT):
            continue
        else:
            if current is None:
                current = [token.start[0], []]
            current[1].append(token.string)
    if current:
        statements.append((current[0], " ".join(current[1])))
    return [(number, text) for number, text in statements
            if _STATUS_LITERAL in text and "needs_review" in text]


_FULL_GATE = re.compile(
    r"status\s*=\s*" + re.escape(_STATUS_LITERAL) + r".*?"
    r"=ANY\s*\(\s*[\w{}]+\s*\.\s*acl\s*\).*?"
    r"NOT\s*[\w{}]+\s*\.\s*needs_review.*?"
    r"\.\s*collection\s*=\s*ANY\s*\(\s*%s\s*\)")


def is_full_publish_gate(text: str) -> bool:
    """Whether one statement is the complete four-clause gate: status, ACL, review, collection.

    Whitespace is dropped before matching because a tokenized statement puts spaces between
    the f-string placeholders (`{ table } .collection=ANY(%s)`); the gate is recognised by
    its four clauses, not by its layout.
    """
    compact = re.sub(r"\s+", "", _normalized(text))
    return bool(_FULL_GATE.search(compact))


#: Modules that still state a **looser** re-check of the publish fields and are pinned by
#: statement signature, so any edit to one of them fails this test and forces a fresh
#: review instead of silently widening the exception. `industrial.py` used to be listed
#: here as a fourth full copy of the gate; it now calls the policy layer instead.
EXCEPTIONS = {
    "fact_reviews.py":
        "a locked re-read after `api._public_document` already applied the full gate; it "
        "re-checks the mutable publish fields and deliberately has no collection clause.",
    "approvals.py":
        "same as fact_reviews.py: a locked re-read behind `api._public_document`, no "
        "collection clause.",
}
EXCEPTION_SIGNATURES = {
    "fact_reviews.py": "current['status']!='indexed'",
    "approvals.py": "current['status']!='indexed'",
}


class PublishPolicyMatrixTest(unittest.TestCase):
    """Equivalence of the Python predicate and the SQL condition over a real table."""

    def setUp(self):
        self.dsn = _write_dsn()
        self.token = uuid.uuid4().hex[:12]
        self.documents = []
        self.rows = {}
        self.addCleanup(self.cleanup)
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            for status in STATUSES:
                for acl in ACLS:
                    for needs_review in REVIEW_FLAGS:
                        for collection in (ALLOWED_COLLECTION, OUTSIDE_COLLECTION):
                            document_id = f"doc-pol-{self.token}-{len(self.rows):03d}"
                            cur.execute("""INSERT INTO document_assets(document_id, title,
                                collection, file_type, sha256, acl, status, needs_review)
                                VALUES (%s, %s, %s, 'pdf', %s, %s, %s, %s)""",
                                        (document_id, f"publish policy {len(self.rows)}",
                                         collection,
                                         hashlib.sha256(document_id.encode()).hexdigest(),
                                         list(acl), status, needs_review))
                            self.documents.append(document_id)
                            self.rows[document_id] = (status, list(acl), needs_review, collection)

    def cleanup(self):
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM chunks WHERE document_id=ANY(%s)", (self.documents,))
            cur.execute("DELETE FROM document_assets WHERE document_id=ANY(%s)",
                        (self.documents,))

    # ------------------------------------------------------------------ the equivalence
    def test_the_predicate_and_the_sql_condition_agree_on_every_combination(self):
        expected_size = len(STATUSES) * len(ACLS) * len(REVIEW_FLAGS) * 2
        self.assertEqual(len(self.rows), expected_size, "the matrix must be complete")
        gate = visibility.published_document_sql("d", [ALLOWED_COLLECTION])
        self.assertEqual(gate.text.count("%s"), 1, "exactly the collection allow-list binds")
        with psycopg2.connect(self.dsn) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                for document_id, facts in self.rows.items():
                    status, acl, needs_review, collection = facts
                    with self.subTest(status=status, acl=tuple(acl),
                                      needs_review=needs_review, collection=collection):
                        cur.execute(f"SELECT * FROM document_assets d "
                                    f"WHERE d.document_id=%s AND {gate.text}",
                                    (document_id, *gate.params))
                        row = cur.fetchone()
                        independent = intended(*facts)
                        predicate = visibility.is_published(row)
                        self.assertIsInstance(predicate, bool)
                        self.assertEqual(predicate, independent,
                                         "Python predicate disagrees with the intended rule")
                        self.assertEqual(row is not None, independent,
                                         "SQL condition disagrees with the intended rule")
                        self.assertEqual(predicate, row is not None,
                                         "the two implementations disagree with each other")

    def test_every_clause_is_the_deciding_one_for_some_combination(self):
        """Each of the four clauses alone flips a row: the matrix is not dominated by one."""
        witnesses = {}
        for facts in self.rows.values():
            status, acl, needs_review, collection = facts
            if intended(*facts):
                continue
            if status == "indexed" and "public" in acl and not needs_review:
                witnesses.setdefault("collection", facts)
            elif status == "indexed" and "public" in acl and collection in COLLECTIONS:
                witnesses.setdefault("needs_review", facts)
            elif status == "indexed" and not needs_review and collection in COLLECTIONS:
                witnesses.setdefault("acl", facts)
            elif "public" in acl and not needs_review and collection in COLLECTIONS:
                witnesses.setdefault("status", facts)
        self.assertEqual(set(witnesses), {"status", "acl", "needs_review", "collection"})

    def test_the_matrix_rejects_a_predicate_with_one_clause_changed(self):
        """Non-vacuity: no changed clause — dropped or inverted — can hide from this matrix."""
        variants = {
            "collection clause dropped": lambda s, a, n, c: (
                s == "indexed" and "public" in a and not n),
            "needs_review clause dropped": lambda s, a, n, c: (
                s == "indexed" and "public" in a and c in COLLECTIONS),
            "acl clause dropped": lambda s, a, n, c: (
                s == "indexed" and not n and c in COLLECTIONS),
            "status clause dropped": lambda s, a, n, c: (
                "public" in a and not n and c in COLLECTIONS),
            "review flag inverted": lambda s, a, n, c: (
                s == "indexed" and "public" in a and n and c in COLLECTIONS),
            "and/or swapped": lambda s, a, n, c: (
                s == "indexed" or ("public" in a and not n and c in COLLECTIONS)),
        }
        matrix = list(self.rows.values())
        for name, variant in variants.items():
            with self.subTest(variant=name):
                blind = [f for f in matrix if variant(*f) != intended(*f)]
                self.assertTrue(blind, f"matrix cannot see the mutant: {name}")

    # ------------------------------------------------- the chunk ACL stays in knowledge
    def test_the_row_read_still_requires_the_chunk_acl_on_top_of_the_document_gate(self):
        chunked, private_chunk, rejected = (f"doc-pol-chk-{self.token}-{i}" for i in range(3))
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            for document_id, needs_review in ((chunked, False), (private_chunk, False),
                                              (rejected, True)):
                cur.execute("""INSERT INTO document_assets(document_id, title, collection,
                    file_type, sha256, acl, status, needs_review)
                    VALUES (%s, 'chunk acl', %s, 'pdf', %s, '{public}', 'indexed', %s)""",
                            (document_id, ALLOWED_COLLECTION,
                             hashlib.sha256(document_id.encode()).hexdigest(), needs_review))
                self.documents.append(document_id)
                cur.execute("""INSERT INTO chunks(chunk_id, document_id, document_version,
                    chunk_index, chunk_type, page_start, page_end, block_ids, text_for_embedding,
                    exact_text, acl, embedding_model, content_hash)
                    VALUES (%s, %s, 1, 0, 'prose', 1, 1, '{}', %s, %s, %s, 'test-embedding', %s)""",
                            (f"chk-pol-{self.token}-{document_id[-1]}", document_id, "正文", "正文",
                             ["private"] if document_id == private_chunk else ["public"],
                             hashlib.sha256("正文".encode()).hexdigest()))
        present = {}
        with psycopg2.connect(self.dsn) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                KnowledgeRetriever(self.dsn)._read_rows(cur, [ALLOWED_COLLECTION],
                                                       present=present)
        self.assertEqual(present.get(chunked), 1,
                         "a published document with a public chunk must still be read")
        self.assertIsNone(present.get(private_chunk),
                          "the chunk-level ACL check ('public'=ANY(c.acl)) was lost")
        self.assertIsNone(present.get(rejected),
                          "the document publish gate was lost in the row read")

    def test_the_head_read_uses_the_same_gate(self):
        published = f"doc-pol-head-{self.token}-yes"
        unpublished = f"doc-pol-head-{self.token}-no"
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            for document_id, needs_review in ((published, False), (unpublished, True)):
                cur.execute("""INSERT INTO document_assets(document_id, title, collection,
                    file_type, sha256, acl, status, needs_review)
                    VALUES (%s, 'head', %s, 'pdf', %s, '{public}', 'indexed', %s)""",
                            (document_id, ALLOWED_COLLECTION,
                             hashlib.sha256(document_id.encode()).hexdigest(), needs_review))
                self.documents.append(document_id)
        refs = [{"document_id": document_id, "document_version": 1,
                 "source_sha256": hashlib.sha256(document_id.encode()).hexdigest(),
                 "parser_version": None} for document_id in (published, unpublished)]
        with self.assertRaises(ValueError) as caught:
            KnowledgeRetriever(self.dsn)._selected_rows([ALLOWED_COLLECTION], refs)
        self.assertEqual(str(caught.exception), "DOCUMENT_SELECTION_UNAVAILABLE",
                         "the unpublished head must be dropped by the shared gate")


class PublishPolicySingleDefinitionTest(unittest.TestCase):
    """The gate is stated once; everything else calls it."""

    def _statements(self, name):
        return publish_gate_statements((DATAMIND / name).read_text(encoding="utf-8"))

    def test_the_publish_gate_is_stated_only_in_the_policy_module(self):
        offenders = {}
        for path in sorted(DATAMIND.glob("*.py")):
            if path.name == "visibility.py" or path.name in EXCEPTIONS:
                continue
            statements = publish_gate_statements(path.read_text(encoding="utf-8"))
            if statements:
                offenders[path.name] = statements
        self.assertEqual(offenders, {},
                         "the publish gate was re-stated outside datamind/visibility.py")
        self.assertGreaterEqual(len(self._statements("visibility.py")), 2,
                                "the detector must not be vacuously blind: the policy module "
                                "itself must be detected")

    def test_the_out_of_scope_exceptions_are_exactly_what_was_reviewed(self):
        """Pinned, not waved through: a changed exception fails and must be re-reviewed."""
        self.assertEqual(set(EXCEPTION_SIGNATURES), set(EXCEPTIONS))
        for name, signature in EXCEPTION_SIGNATURES.items():
            with self.subTest(module=name):
                source = (DATAMIND / name).read_text(encoding="utf-8")
                statements = publish_gate_statements(source)
                self.assertEqual(len(statements), 1,
                                 f"{name} changed shape; re-review the exception")
                self.assertIn(signature, source)
        for name in ("fact_reviews.py", "approvals.py"):
            with self.subTest(module=name):
                source = (DATAMIND / name).read_text(encoding="utf-8")
                line = next(line for line in source.splitlines()
                            if EXCEPTION_SIGNATURES[name] in line)
                self.assertNotIn("collection", line,
                                 "this exception must stay a looser locked re-read, "
                                 "not a re-statement of the gate")

    def test_the_complete_gate_is_written_exactly_once_in_the_whole_package(self):
        occurrences = {}
        for path in sorted(DATAMIND.glob("*.py")):
            matches = [(number, text)
                       for number, text in publish_gate_statements(path.read_text(encoding="utf-8"))
                       if is_full_publish_gate(text)]
            if matches:
                occurrences[path.name] = matches
        self.assertEqual(sorted(occurrences), ["visibility.py"],
                         "the four-clause gate must live in the policy module alone")
        self.assertEqual(len(occurrences["visibility.py"]), 1,
                         "and it must be written exactly once")

    def test_the_guard_rejects_the_exact_old_spellings(self):
        """Non-vacuity of the guard: the very spellings that were removed are detected."""
        old_authority = ("def basis_valid(cur,basis):\n"
                         "    if not doc or doc['status']!='indexed' "
                         "or 'public' not in doc['acl'] or doc['needs_review'] "
                         "or doc['collection'] not in COLLECTIONS:return False\n")
        old_knowledge = ("def _read_rows(self, cur, collections, ids=None):\n"
                         "    cur.execute(\"\"\"SELECT c.* FROM chunks c JOIN document_assets d\n"
                         "        USING(document_id)\n"
                         "        WHERE d.status='indexed' AND c.document_version=d.doc_version\n"
                         "          AND 'public'=ANY(c.acl) AND 'public'=ANY(d.acl)\n"
                         "          AND NOT d.needs_review AND d.collection=ANY(%s)\"\"\",\n"
                         "        (collections,))\n")
        self.assertEqual(len(publish_gate_statements(old_authority)), 1)
        self.assertEqual(len(publish_gate_statements(old_knowledge)), 1,
                         "a gate split over continuation lines must still be detected")
        self.assertEqual(len(publish_gate_statements(
            "def f(row):\n"
            "    return row.get(\"status\") == \"indexed\" "
            "and not row.get(\"needs_review\")\n")), 1,
            "double-quoted spellings must be detected too")
        self.assertEqual(publish_gate_statements("return is_published(row)"), [])
        self.assertEqual(publish_gate_statements("if needs_review:\n    pass"), [])
        # Prose describing the gate is documentation, not a second definition.
        self.assertEqual(publish_gate_statements(
            "# `status='indexed'` is the legacy gate; `needs_review` stays TRUE\n"
            "return is_published(row)\n"), [])

    def test_every_caller_goes_through_the_policy_module(self):
        """The unification is wired, not just available."""
        callers = {
            "api.py": ("is_published", "published_document_sql", "public_reviewed_sql"),
            "knowledge.py": ("published_document_sql",),
            "authority.py": ("is_published",),
        }
        for name, symbols in callers.items():
            with self.subTest(module=name):
                source = (DATAMIND / name).read_text(encoding="utf-8")
                self.assertIn("from .visibility import", source)
                for symbol in symbols:
                    self.assertIn(symbol, source)

    def test_the_full_gate_detector_rejects_a_gate_with_a_clause_missing(self):
        complete = ("(d.status='indexed' AND 'public'=ANY(d.acl) "
                    "AND NOT d.needs_review AND d.collection=ANY(%s))")
        self.assertTrue(is_full_publish_gate(complete))
        for dropped in (complete.replace(" AND d.collection=ANY(%s)", ""),
                        complete.replace(" AND NOT d.needs_review", ""),
                        complete.replace(" AND 'public'=ANY(d.acl)", ""),
                        complete.replace("d.status='indexed' AND ", "")):
            with self.subTest(dropped=dropped):
                self.assertFalse(is_full_publish_gate(dropped))


class PublishPolicyInterfaceTest(unittest.TestCase):
    """The binding convention itself, so a caller cannot bind the allow-list wrongly."""

    def test_the_fragment_is_one_pair_with_exactly_one_placeholder(self):
        where, values = visibility.published_document_sql("d", ["demo_kb", "ohrbench_kb"])
        self.assertEqual(where.count("%s"), 1)
        self.assertEqual(values, (["demo_kb", "ohrbench_kb"],))
        self.assertIn("collection=ANY(%s)", where)
        self.assertTrue(where.startswith("(") and where.endswith(")"))

    def test_the_fragment_text_is_never_caller_data(self):
        for alias in ("d; DROP TABLE document_assets", "d d", "", "d'acl", None, 3):
            with self.subTest(alias=alias):
                with self.assertRaises(ValueError):
                    visibility.published_document_sql(alias, [])

    def test_the_readiness_probe_binds_nothing_and_omits_only_the_allow_list(self):
        text, params = visibility.public_reviewed_sql("document_assets")
        self.assertEqual(params, ())
        self.assertNotIn("%s", text)
        self.assertNotIn("collection", text)
        gate, _ = visibility.published_document_sql("document_assets", [])
        self.assertEqual(text, gate.replace(
            " AND document_assets.collection=ANY(%s)", ""))

    def test_constants_come_from_the_existing_collection_literal(self):
        self.assertEqual(visibility.publishable_collections(), frozenset(COLLECTIONS))
        self.assertIsNot(visibility.publishable_collections(), COLLECTIONS)
        self.assertEqual(visibility.PUBLISHED_STATUS, "indexed")
        self.assertEqual(visibility.PUBLIC_ACL, "public")

    def test_the_predicate_handles_absent_and_edge_rows(self):
        self.assertFalse(visibility.is_published(None))
        self.assertFalse(visibility.is_published({}))
        self.assertFalse(visibility.is_published({"status": "indexed", "acl": None,
                                                  "needs_review": False,
                                                  "collection": "demo_kb"}))
        self.assertFalse(visibility.is_published({"status": "indexed", "acl": ["public"],
                                                  "needs_review": True,
                                                  "collection": "demo_kb"}))
        self.assertFalse(visibility.is_published({"status": "indexed", "acl": ["public"],
                                                  "needs_review": False,
                                                  "collection": "outside_kb"}))
        self.assertTrue(visibility.is_published({"status": "indexed", "acl": ["public"],
                                                 "needs_review": False,
                                                 "collection": "demo_kb"}))


if __name__ == "__main__":
    unittest.main()
