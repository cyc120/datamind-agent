"""Version-filtered public knowledge retrieval with lexical/dense RRF fusion."""
from __future__ import annotations

from collections import Counter
from functools import lru_cache
import hashlib
import math
import os
from pathlib import Path
import re
import threading
import unicodedata

import psycopg2
from psycopg2.extras import RealDictCursor


ROOT = Path(__file__).resolve().parents[1]
EMBED_MODEL = "BAAI/bge-small-zh-v1.5"
COLLECTIONS = {"demo_kb", "ohrbench_kb", "industrial_demo"}
RETRIEVER_VERSION = "hybrid-rrf-v2"
_EMBED_LOCK = threading.Lock()


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

    def _rows(self, collections: list[str]) -> list[dict]:
        with psycopg2.connect(self.dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""SELECT c.*, d.title, d.collection, d.origin, d.file_type, d.sha256 AS document_sha256,
                           d.doc_version, d.parser_version, d.quality_detail,
                           COALESCE((SELECT jsonb_agg(jsonb_build_object('block_id',b.block_id,
                             'page_no',b.page_no,'bbox',b.bbox,'content_hash',b.content_hash,
                             'ocr_confidence',b.ocr_confidence,'formula_latex',b.formula_latex,'parser_version',b.parser_version)
                             ORDER BY b.reading_order) FROM document_blocks b
                             WHERE b.document_id=c.document_id AND b.block_id=ANY(c.block_ids)), '[]'::jsonb) AS source_blocks
                           FROM chunks c JOIN document_assets d USING(document_id)
                           WHERE d.status='indexed' AND c.document_version=d.doc_version
                             AND 'public'=ANY(c.acl) AND 'public'=ANY(d.acl)
                             AND NOT d.needs_review AND d.collection=ANY(%s)
                           ORDER BY c.chunk_id LIMIT 5000""", (collections,))
            rows = [dict(row) for row in cur.fetchall()]
        # A changed text cannot retain its old digest and still become evidence.
        return [row for row in rows if row.get("exact_text") and
                hashlib.sha256(row["exact_text"].encode()).hexdigest() == row["content_hash"]]

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

    def retrieve(self, question: str, *, collections: list[str] | None = None, limit: int = 5) -> dict:
        collections = sorted(COLLECTIONS if collections is None else set(collections))
        if not collections or not set(collections) <= COLLECTIONS:
            raise ValueError("COLLECTION_NOT_ALLOWED")
        rows = select_documents(question, self._rows(collections))
        row_map = {row["chunk_id"]: row for row in rows}
        snapshot = hashlib.sha256("\n".join(
            f"{r['chunk_id']}:{r['document_version']}:{r['document_sha256']}:{r['content_hash']}" for r in rows
        ).encode()).hexdigest()[:16]
        lexical = lexical_ranking(question, rows)[:30]
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
        hits = []
        for key in sorted(scores, key=lambda item: (-scores[item], item))[:limit]:
            hits.append({**row_map[key], "retrieval_score": round(scores[key], 6),
                         "retrieval_scores": evidence_scores[key]})
        return {"hits": hits, "snapshot_id": "kb-" + snapshot, "retriever_version": RETRIEVER_VERSION,
                "mode": "hybrid" if dense else "lexical", "warnings": warnings,
                "eligible_chunks": len(rows), "collections": collections}
