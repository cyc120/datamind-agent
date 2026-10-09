"""Version-filtered public knowledge retrieval with lexical/dense RRF fusion."""
from __future__ import annotations

from collections import Counter
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import re
import threading
import unicodedata

import psycopg2
from psycopg2.extras import RealDictCursor

from .visibility import published_document_sql


ROOT = Path(__file__).resolve().parents[1]
EMBED_MODEL = "BAAI/bge-small-zh-v1.5"
COLLECTIONS = {"demo_kb", "ohrbench_kb", "industrial_demo"}
RETRIEVER_VERSION = "hybrid-rrf-v3"
_EMBED_LOCK = threading.Lock()
MAX_SELECTED_DOCUMENTS = 8
MAX_SCOPE_CHUNKS = 5000


def document_selection(refs: list[dict]) -> list[dict]:
    """Explicit source identities, never inferred from names in the question."""
    fields = {"document_id", "document_version", "source_sha256", "parser_version"}
    if not isinstance(refs, list) or not 1 <= len(refs) <= MAX_SELECTED_DOCUMENTS:
        raise ValueError("DOCUMENT_SELECTION_INVALID")
    checked = []
    for ref in refs:
        if (not isinstance(ref, dict) or set(ref) != fields
                or not isinstance(ref["document_id"], str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", ref["document_id"])
                or type(ref["document_version"]) is not int or ref["document_version"] < 1
                or not isinstance(ref["source_sha256"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", ref["source_sha256"])
                or not isinstance(ref["parser_version"], str) or not 1 <= len(ref["parser_version"]) <= 64):
            raise ValueError("DOCUMENT_SELECTION_INVALID")
        checked.append(dict(ref))
    if len({ref["document_id"] for ref in checked}) != len(checked):
        raise ValueError("DOCUMENT_SELECTION_INVALID")
    return checked


def meta_dsn() -> str:
    return os.environ.get("DATAMIND_META_DSN") or (
        "host=127.0.0.1 port=5432 dbname=datamind_meta user=readonly_user password=readonly_dev")


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or "")).lower()


def tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text or "").lower()
    for word in ("是多少", "是什么", "有哪些", "请给出", "什么", "多少", "根据", "公司", "的", "了"):
        text = text.replace(word, " ")
    result = re.findall(r"[a-z]+(?:[-_][a-z0-9]+)*|\d+(?:\.\d+)?", text)
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        result.extend(run[i:i + 2] for i in range(len(run) - 1))
    return result


def select_documents(question: str, rows: list[dict]) -> list[dict]:
    """An explicit sample ID or known full title limits the requested sources."""
    sample_ids = set(re.findall(r"\bohr-\d{3}\b", question.lower()))
    if sample_ids:
        rows = [row for row in rows if any(f"[{sample}/" in row["title"].lower() for sample in sample_ids)]
    titles = re.findall(r"《([^》]+)》", question)
    named = [row for row in rows if any(normalize(title) in normalize(row["title"]) for title in titles)]
    return named if named else rows


def lexical_ranking(question: str, rows: list[dict]) -> list[tuple[str, float]]:
    query = set(tokens(question))
    counts = [Counter(tokens(row["title"] + "\n" + " > ".join(row["section_path"]) +
                             "\n" + row["exact_text"])) for row in rows]
    average = sum(sum(c.values()) for c in counts) / max(1, len(counts))
    frequency = Counter(term for counts_for_row in counts for term in counts_for_row)
    ranked = []
    for row, count in zip(rows, counts):
        score = 0.0
        length = sum(count.values())
        for term in query:
            n = count.get(term, 0)
            if n:
                idf = math.log(1 + (len(rows) - frequency[term] + 0.5) / (frequency[term] + 0.5))
                score += idf * n * 2.2 / (n + 1.2 * (0.25 + 0.75 * length / max(1, average)))
        if score:
            ranked.append((row["chunk_id"], score))
    return sorted(ranked, key=lambda hit: (-hit[1], hit[0]))


@lru_cache(maxsize=1)
def embedding_model():
    from fastembed import TextEmbedding
    return TextEmbedding(model_name=EMBED_MODEL, cache_dir=str(ROOT / ".cache/fastembed"), threads=2)


class KnowledgeRetriever:
    def __init__(self, dsn: str | None = None, *, mode: str | None = None):
        self.dsn = dsn or meta_dsn()
        self.mode = mode or os.environ.get("DATAMIND_RETRIEVAL_MODE", "hybrid")
        if self.mode not in {"hybrid", "lexical"}:
            raise ValueError("INVALID_RETRIEVAL_MODE")

    def _read_rows(self, cur, collections: list[str], ids=None, *, present: dict | None = None) -> list[dict]:
        # The document gate comes from the one policy layer; `'public'=ANY(c.acl)` below is
        # the *chunk* ACL and is deliberately not part of it.
        published = published_document_sql("d", collections)
        cur.execute(f"""SELECT c.*, d.title, d.collection, d.origin, d.file_type, d.sha256 AS document_sha256,
                           d.doc_version, d.parser_version, d.quality_detail,
                           COALESCE((SELECT jsonb_agg(jsonb_build_object('block_id',b.block_id,
                             'page_no',b.page_no,'bbox',b.bbox,'content_hash',b.content_hash,
                             'ocr_confidence',b.ocr_confidence,'formula_latex',b.formula_latex,'parser_version',b.parser_version)
                             ORDER BY b.reading_order) FROM document_blocks b
                             WHERE b.document_id=c.document_id AND b.block_id=ANY(c.block_ids)), '[]'::jsonb) AS source_blocks
                           FROM chunks c JOIN document_assets d USING(document_id)
                           WHERE c.document_version=d.doc_version
                             AND {published.text}
                             AND 'public'=ANY(c.acl)
                             AND (%s::text[] IS NULL OR c.document_id=ANY(%s::text[]))
                           ORDER BY c.chunk_id LIMIT %s""",
                    (*published.params, ids, ids, MAX_SCOPE_CHUNKS + (1 if ids else 0)))
        rows = [dict(row) for row in cur.fetchall()]
        if ids and len(rows) > MAX_SCOPE_CHUNKS:
            raise ValueError("DOCUMENT_SELECTION_TOO_LARGE")
        if present is not None:
            # Counted *before* the digest filter: "this document has no chunk at all" and
            # "this document has chunks whose text no longer matches its digest" are
            # different facts and must not be collapsed into one refusal.
            for row in rows:
                present[row["document_id"]] = present.get(row["document_id"], 0) + 1
        # A changed text cannot retain its old digest and still become evidence.
        return [row for row in rows if row.get("exact_text") and
                hashlib.sha256(row["exact_text"].encode()).hexdigest() == row["content_hash"]]

    def _rows(self, collections: list[str]) -> list[dict]:
        with psycopg2.connect(self.dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            return self._read_rows(cur, collections)

    def _selected_rows(self, collections: list[str], refs: list[dict]) -> tuple[list[dict], list[dict]]:
        ids = [ref["document_id"] for ref in refs]
        with psycopg2.connect(self.dsn) as conn:
            # The heads and their chunks must be read from the same database snapshot.
            conn.set_session(isolation_level="REPEATABLE READ", readonly=True)
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                published = published_document_sql("document_assets", collections)
                cur.execute(f"""SELECT document_id,doc_version AS document_version,sha256 AS source_sha256,
                    parser_version,title,collection FROM document_assets
                    WHERE document_id=ANY(%s) AND {published.text}""", (ids, *published.params))
                heads = {row["document_id"]: dict(row) for row in cur.fetchall()}
                if set(heads) != set(ids):
                    raise ValueError("DOCUMENT_SELECTION_UNAVAILABLE")
                if any(any(heads[ref["document_id"]][key] != value for key, value in ref.items()) for ref in refs):
                    raise ValueError("DOCUMENT_SELECTION_CHANGED")
                present: dict[str, int] = {}
                rows = self._read_rows(cur, collections, ids, present=present)
        snapshots = []
        for ref in refs:
            entries = [{key: row.get(key) for key in ("chunk_id", "content_hash", "document_version",
                       "parser_version", "page_start", "page_end", "block_ids", "source_blocks", "section_path")}
                       for row in rows if row["document_id"] == ref["document_id"]]
            entries.sort(key=lambda item: item["chunk_id"])
            snapshots.append({**heads[ref["document_id"]], "eligible_chunks": len(entries),
                "chunks_sha256": hashlib.sha256(json.dumps(entries, ensure_ascii=False,
                    sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()})
        # A selected document with **no chunk row at all** cannot contribute evidence. Accepting
        # it silently produced an answer with `eligible_chunks=0` and `all_covered=false` — the
        # document was "approved"/"indexed" but had never been chunked (the upload pipeline does
        # not build a vector index yet). Refusing here is the server-side half of the fix; the
        # Q&A selector filtering on `searchable` is only the client-side half, and a selection can
        # go stale between the list read and this submit.
        #
        # A document that *has* chunks whose text no longer matches its digest is deliberately
        # **not** refused here: that is the tampered/drifted-evidence case, and the existing
        # `eligible_chunks=0` + incomplete-coverage reporting tells the caller what happened
        # without blocking the rest of the selection.
        empty = [ref["document_id"] for ref in refs if not present.get(ref["document_id"])]
        if empty:
            raise ValueError("DOCUMENT_SELECTION_NOT_SEARCHABLE")
        return rows, snapshots

    def revalidate_selection(self, collections: list[str], refs: list[dict], snapshots: list[dict]) -> None:
        """Reject a draft if selected heads/content changed while the model ran."""
        try:
            _, current = self._selected_rows(collections, refs)
        except ValueError as exc:
            raise ValueError("DOCUMENT_SELECTION_CHANGED") from exc
        if current != snapshots:
            raise ValueError("DOCUMENT_SELECTION_CHANGED")

    def _dense(self, question: str, collections: list[str]) -> list[tuple[str, float]]:
        from qdrant_client import QdrantClient, models
        with _EMBED_LOCK:
            vector = list(embedding_model().embed([question]))[0].tolist()
        client = QdrantClient(url=os.environ.get("DATAMIND_QDRANT_URL", "http://127.0.0.1:6333"),
                              timeout=5, trust_env=False)
        try:
            hits = client.query_points("datamind_knowledge", query=vector, limit=30,
                query_filter=models.Filter(must=[
                    models.FieldCondition(key="collection", match=models.MatchAny(any=collections)),
                    models.FieldCondition(key="acl", match=models.MatchValue(value="public")),
                    models.FieldCondition(key="embedding_model", match=models.MatchValue(value=EMBED_MODEL))
                ])).points
        finally:
            client.close()
        return [(hit.payload["chunk_id"], float(hit.score)) for hit in hits
                if hit.payload and hit.payload.get("chunk_id") and hit.score >= 0.35]

    def retrieve(self, question: str, *, collections: list[str] | None = None, limit: int = 5,
                 document_refs: list[dict] | None = None) -> dict:
        collections = sorted(COLLECTIONS if collections is None else set(collections))
        if not collections or not set(collections) <= COLLECTIONS:
            raise ValueError("COLLECTION_NOT_ALLOWED")
        if type(limit) is not int or limit < 1 or (document_refs is not None and limit > 8):
            raise ValueError("RETRIEVAL_LIMIT_INVALID")
        selected = None
        if document_refs is None:
            rows = select_documents(question, self._rows(collections))
        else:
            refs = document_selection(document_refs)
            rows, selected = self._selected_rows(collections, refs)
            # One relevant chunk per selected document fits even for eight files.
            limit = max(limit, len(refs))
        row_map = {row["chunk_id"]: row for row in rows}
        snapshot = hashlib.sha256("\n".join(
            f"{r['chunk_id']}:{r['document_version']}:{r['document_sha256']}:{r['content_hash']}" for r in rows
        ).encode()).hexdigest()[:16]
        all_lexical = lexical_ranking(question, rows)
        lexical = all_lexical[:30]
        if selected:
            # A high scoring long file must not push another file's first hit
            # outside the fusion candidate budget.
            represented = set()
            for key, score in all_lexical:
                doc_id = row_map[key]["document_id"]
                if doc_id not in represented:
                    represented.add(doc_id)
                    if key not in {item[0] for item in lexical}:
                        lexical.append((key, score))
        dense, warnings = [], []
        if self.mode == "hybrid" and rows:
            try:
                dense = self._dense(question, collections)
            except Exception:
                warnings.append("DENSE_RETRIEVAL_UNAVAILABLE")
        dense = [(key, score) for key, score in dense if key in row_map]
        rankings = (lexical, dense)
        scores: dict[str, float] = {}
        evidence_scores: dict[str, dict] = {}
        for method, ranked in zip(("lexical", "dense"), rankings):
            for rank, (key, score) in enumerate(ranked, 1):
                scores[key] = scores.get(key, 0) + 1 / (60 + rank)
                evidence_scores.setdefault(key, {})[method] = round(score, 5)
        ranked_keys = sorted(scores, key=lambda item: (-scores[item], item))
        keys = []
        if selected:
            for ref in refs:
                first = next((key for key in ranked_keys if row_map[key]["document_id"] == ref["document_id"]), None)
                if first:
                    keys.append(first)
        keys.extend(key for key in ranked_keys if key not in keys)
        hits = []
        for key in keys[:limit]:
            hits.append({**row_map[key], "retrieval_score": round(scores[key], 6),
                         "retrieval_scores": evidence_scores[key]})
        result = {"hits": hits, "snapshot_id": "kb-" + snapshot, "retriever_version": RETRIEVER_VERSION,
                "mode": "hybrid" if dense else "lexical", "warnings": warnings,
                "eligible_chunks": len(rows), "collections": collections}
        if selected is not None:
            coverage = [{"document_id": ref["document_id"], "eligible_chunks": ref["eligible_chunks"],
                         "retrieved_chunks": sum(hit["document_id"] == ref["document_id"] for hit in hits)} for ref in selected]
            missing = [item["document_id"] for item in coverage if not item["retrieved_chunks"]]
            result.update(selected_documents=selected, coverage={"documents": coverage,
                "missing_document_ids": missing, "all_covered": not missing,
                "semantics": "Relevant excerpts per selected file; no completeness or semantic entailment claim"})
        return result
