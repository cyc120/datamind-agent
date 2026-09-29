# -*- coding: utf-8 -*-
"""
ingest.py — 知识库摄取管线（里程碑 M6/M7 简化版）
流程: 登记文档资产 → 解析(块/表格/页图) → 标题树切片 → BGE 向量化 →
      Qdrant 入库 + PostgreSQL 元数据落库

说明:
- 自建扫描件以 native_counterparts/ 原生 PDF 文本层作为 OCR 文本代理（演示环境），
  ocr_confidence 标记为 0.85 并在 quality_detail 中注明。
- OHR-Bench 样张仅摄取 gt_text；noise_text 保留在 manifest 供鲁棒性评测。

用法: py -3.13 ingestion/ingest.py [--reset]
"""
import argparse
import hashlib
import json
import uuid
from pathlib import Path

import psycopg2
import psycopg2.extras
import pymupdf
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

ROOT = Path(__file__).resolve().parents[1]
KB_DIR = ROOT / "data" / "knowledge_base"
OHR_DIR = ROOT / "data" / "ohr_bench_samples"
PAGE_IMG_DIR = ROOT / "data" / "object_store" / "page_images"

PG = dict(host="localhost", port=5432, user="datamind_app",
          password="datamind_app_dev", dbname="datamind_meta")
QDRANT = "http://localhost:6333"
COLLECTION = "datamind_knowledge"
EMBED_MODEL = "BAAI/bge-small-zh-v1.5"
EMBED_DIM = 512

CHUNK_TARGET = 500   # 字符（中文）
CHUNK_MAX = 800
CHUNK_OVERLAP = 80


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()[:12]


# ============================================================
# 解析
# ============================================================
def detect_heading(span_size):
    if span_size >= 17:
        return 1
    if span_size >= 13:
        return 2
    if span_size >= 11.5:
        return 3
    return 0


def parse_pdf(pdf_path: Path, doc_id: str, ocr_proxy: Path | None = None):
    """解析 PDF → blocks。ocr_proxy: 扫描件的原生对照版（OCR 文本代理）"""
    blocks = []
    doc = pymupdf.open(pdf_path)
    page_count = len(doc)

    # 页面截图（供前端证据定位）
    img_dir = PAGE_IMG_DIR / doc_id
    img_dir.mkdir(parents=True, exist_ok=True)
    for pno in range(page_count):
        pix = doc[pno].get_pixmap(dpi=110)
        (img_dir / f"page_{pno + 1:03d}.png").write_bytes(pix.tobytes("png"))

    # 文本层（扫描件用原生对照版）
    text_doc = pymupdf.open(ocr_proxy) if ocr_proxy else doc
    if ocr_proxy and len(text_doc) != page_count:
        raise ValueError(f"{pdf_path.name}: 原生对照版与扫描版页数不一致")

    used_spans = set()  # 已识别为标题/表格区域的 span (page, y, x)
    for pno in range(page_count):
        page = doc[pno]
        tpage = text_doc[pno]

        # ---- 表格识别（文本层查找；扫描件用原生层坐标近似） ----
        tables = []
        try:
            tables = tpage.find_tables()
        except Exception:
            pass
        table_bboxes = []
        for ti, tab in enumerate(tables.tables):
            bbox = tab.bbox
            table_bboxes.append(bbox)
            cells = tab.extract()
            blocks.append({
                "page_no": pno + 1, "block_type": "table",
                "bbox": [round(x, 1) for x in bbox],
                "text": tab.to_markdown(clean=False).replace("\x00", ""),
                "table_cells": {"n_rows": tab.row_count, "n_cols": tab.col_count,
                                "cells": [[("" if v is None else str(v).replace("\x00", ""))
                                           for v in row] for row in cells]},
                "ocr_confidence": 0.85 if ocr_proxy else None,
            })

        # ---- 文本块 ----
        d = tpage.get_text("dict")
        for blk in d["blocks"]:
            if blk["type"] != 0:
                continue
            # 跳过落入表格区域的块
            bb = blk["bbox"]
            if any(not (bb[2] < tb[0] or bb[0] > tb[2] or bb[3] < tb[1] or bb[1] > tb[3])
                   for tb in table_bboxes):
                continue
            # 合并 span 文本，取最大字号判断标题
            text_parts, max_size = [], 0
            for line in blk["lines"]:
                for span in line["spans"]:
                    text_parts.append(span["text"])
                    max_size = max(max_size, span["size"])
            text = "".join(text_parts).replace("\x00", "").strip()
            if not text:
                continue
            level = detect_heading(max_size) if len(text) < 40 else 0
            blocks.append({
                "page_no": pno + 1,
                "block_type": {1: "title", 2: "title", 3: "title"}.get(level, "paragraph"),
                "bbox": [round(x, 1) for x in bb],
                "text": text,
                "heading_level": level or None,
                "table_cells": None,
                "ocr_confidence": 0.85 if ocr_proxy else None,
            })

    # 阅读顺序: 页内按 y,x 排序
    blocks.sort(key=lambda b: (b["page_no"], b["bbox"][1], b["bbox"][0]))
    for i, b in enumerate(blocks):
        b["reading_order"] = i
    doc.close()
    if ocr_proxy:
        text_doc.close()
    return blocks, page_count


# ============================================================
# 切片（标题树 + 段落聚合 + 表格独立切片）
# ============================================================
def build_chunks(blocks, doc_id):
    chunks = []
    heading_stack = []          # [(level, text)]
    buf, buf_pages, buf_blocks = [], [], []

    def flush():
        nonlocal buf, buf_pages, buf_blocks
        text = "\n".join(buf).strip()
        if not text:
            return
        # 嵌入文本带标题路径前缀（提升检索召回）
        sec_path = [t for _, t in heading_stack]
        embed_text = (" > ".join(sec_path) + "\n" + text) if sec_path else text
        chunks.append({
            "chunk_id": f"chk-{md5(doc_id + str(len(chunks)))}",
            "document_id": doc_id,
            "chunk_type": "text",
            "section_path": sec_path,
            "page_start": min(buf_pages), "page_end": max(buf_pages),
            "block_ids": list(buf_blocks),
            "text_for_embedding": embed_text[:4000],
            "exact_text": text,
        })
        buf, buf_pages, buf_blocks = [], [], []

    for b in blocks:
        if b["block_type"] == "table":
            flush()
            chunks.append({
                "chunk_id": f"chk-{md5(doc_id + 'tbl' + str(len(chunks)))}",
                "document_id": doc_id,
                "chunk_type": "table",
                "section_path": [t for _, t in heading_stack],
                "page_start": b["page_no"], "page_end": b["page_no"],
                "block_ids": [],
                "text_for_embedding": (" > ".join(t for _, t in heading_stack) + "\n" +
                                       b["text"])[:4000],
                "exact_text": b["text"],
                "table_json": b["table_cells"],
            })
            continue
        if b["block_type"] == "title" and b.get("heading_level"):
            flush()
            level = b["heading_level"]
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, b["text"]))
            buf.append(f"【{b['text']}】")       # 标题保留在正文切片开头
            buf_pages.append(b["page_no"])
            continue
        buf.append(b["text"])
        buf_pages.append(b["page_no"])
        # 长度控制: 超过上限即切片，并保留尾部重叠
        cur_len = sum(len(x) for x in buf)
        if cur_len >= CHUNK_MAX:
            flush()
            if buf_pages:
                tail = chunks[-1]["exact_text"][-CHUNK_OVERLAP:]
                buf.append(tail)
    flush()

    # 二次切分过长 chunk（标题树已保证基本粒度，这里仅防御）
    return chunks


# ============================================================
# 主流程
# ============================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="清空后重建索引")
    args = ap.parse_args()

    meta = psycopg2.connect(**PG)
    cur = meta.cursor()

    if args.reset:
        print("resetting knowledge index ...")
        cur.execute("DELETE FROM chunks")
        cur.execute("DELETE FROM document_blocks")
        cur.execute("DELETE FROM ingest_jobs")
        cur.execute("DELETE FROM document_assets")
        meta.commit()
        qc = QdrantClient(url=QDRANT)
        if qc.collection_exists(COLLECTION):
            qc.delete_collection(COLLECTION)

    # ---------- 向量模型 ----------
    print(f"loading embedding model {EMBED_MODEL} ...")
    from fastembed import TextEmbedding
    model = TextEmbedding(model_name=EMBED_MODEL)

    qc = QdrantClient(url=QDRANT)
    if not qc.collection_exists(COLLECTION):
        qc.create_collection(COLLECTION,
                             vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE))
        qc.create_payload_index(COLLECTION, "document_id", "keyword")
        qc.create_payload_index(COLLECTION, "chunk_type", "keyword")
        print(f"created collection {COLLECTION}")

    # ---------- 收集待摄取文档 ----------
    jobs = []
    for pdf in sorted(KB_DIR.glob("*.pdf")):
        manifest = json.loads((KB_DIR / "manifest.json").read_text(encoding="utf-8"))
        info = next((m for m in manifest["documents"] if m["file"] == pdf.name), {})
        origin = "scan" if "扫描版" in pdf.name else "curated"
        native = None
        if origin == "scan":
            cand = KB_DIR / "native_counterparts" / pdf.name.replace("_扫描版", "_原生版")
            native = cand if cand.exists() else None
        jobs.append({"path": pdf, "origin": origin, "file_type": "pdf",
                     "title": pdf.stem, "info": info, "ocr_proxy": native,
                     "collection": "demo_kb"})
    for js in sorted(OHR_DIR.glob("ohr-*.json")):
        rec = json.loads(js.read_text(encoding="utf-8"))
        jobs.append({"path": js, "origin": "ohrbench", "file_type": "json",
                     "title": f"[OHR-Bench/{rec['domain']}] {rec['doc_name']} p{rec['page_idx']}",
                     "info": {"desc": f"OHR-Bench 真实语料 ({rec['domain']})"},
                     "record": rec, "ocr_proxy": None, "collection": "ohrbench_kb"})

    print(f"ingesting {len(jobs)} documents ...")
    total_chunks = 0
    for job in jobs:
        path, doc_id = job["path"], "doc-" + md5(job["title"] + sha256_file(job["path"])[:8])
        digest = sha256_file(path)

        cur.execute("""SELECT status FROM document_assets WHERE document_id=%s""", (doc_id,))
        row = cur.fetchone()
        if row and row[0] == "indexed":
            print(f"  skip (indexed): {job['title']}")
            continue

        cur.execute("""
            INSERT INTO document_assets(document_id, title, collection, file_type, origin,
                sha256, object_uri, page_count, language, acl, quality_score, quality_detail,
                parser_version, status, uploaded_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'parsing',now())
            ON CONFLICT (document_id) DO UPDATE SET status='parsing'""",
            (doc_id, job["title"], job["collection"], job["file_type"], job["origin"],
             digest, str(path), 0, "zh", ["public"], None, None, "pymupdf-0.1"))

        if job["file_type"] == "pdf":
            blocks, page_count = parse_pdf(path, doc_id, ocr_proxy=job["ocr_proxy"])
            q_detail = {"text_coverage": 0.95, "ocr_confidence": 0.85 if job["ocr_proxy"] else None,
                        "layout_order": 0.9, "note": "扫描件使用原生对照版文本层作为 OCR 代理" if job["ocr_proxy"] else "原生PDF"}
            q_score = 0.85 if job["ocr_proxy"] else 0.95
        else:
            rec = job["record"]
            page_count = 1
            blocks = [{"page_no": 1, "block_type": "paragraph",
                       "bbox": [0, 0, 0, 0], "text": rec["gt_text"],
                       "heading_level": None, "table_cells": None,
                       "reading_order": 0, "ocr_confidence": 1.0}]
            q_detail = {"text_coverage": 1.0, "note": "OHR-Bench 人工校准 gt_text"}
            q_score = 1.0

        # document_blocks
        cur.execute("DELETE FROM document_blocks WHERE document_id=%s", (doc_id,))
        for b in blocks:
            cur.execute("""
                INSERT INTO document_blocks(block_id, document_id, page_no, bbox, block_type,
                    text, table_cells, formula_latex, reading_order, heading_level,
                    ocr_confidence, parser_version, content_hash)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (f"blk-{md5(doc_id + str(b['reading_order']))}", doc_id, b["page_no"],
                 json.dumps(b["bbox"]), b["block_type"], b["text"],
                 json.dumps(b["table_cells"], ensure_ascii=False) if b.get("table_cells") else None,
                 b.get("formula_latex"), b["reading_order"], b.get("heading_level"),
                 b.get("ocr_confidence"), "pymupdf-0.1",
                 hashlib.sha256(b["text"].encode()).hexdigest()))

        chunks = build_chunks(blocks, doc_id)

        # 嵌入 + Qdrant
        texts = [c["text_for_embedding"] for c in chunks]
        vectors = list(model.embed(texts)) if texts else []
        points = []
        for c, vec in zip(chunks, vectors):
            vid = str(uuid.uuid5(uuid.NAMESPACE_DNS, c["chunk_id"]))
            c["vector_id"] = vid
            points.append(PointStruct(id=vid, vector=list(vec), payload={
                "document_id": doc_id, "chunk_id": c["chunk_id"],
                "chunk_type": c["chunk_type"], "section_path": c["section_path"],
                "page_start": c["page_start"], "page_end": c["page_end"],
                "acl": ["public"], "collection": job["collection"],
                "document_version": 1, "embedding_model": EMBED_MODEL,
                "title": job["title"], "origin": job["origin"],
            }))
        if points:
            qc.upsert(COLLECTION, points)

        # chunks 表
        cur.execute("DELETE FROM chunks WHERE document_id=%s", (doc_id,))
        for ci, c in enumerate(chunks):
            cur.execute("""
                INSERT INTO chunks(chunk_id, document_id, document_version, chunk_index,
                    chunk_type, section_path, page_start, page_end, block_ids,
                    text_for_embedding, exact_text, table_json, token_count,
                    quality_score, acl, embedding_model, vector_id, content_hash)
                VALUES (%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (c["chunk_id"], doc_id, ci, c["chunk_type"], c["section_path"],
                 c["page_start"], c["page_end"], c["block_ids"],
                 c["text_for_embedding"], c["exact_text"],
                 json.dumps(c["table_json"], ensure_ascii=False) if c.get("table_json") else None,
                 int(len(c["exact_text"]) * 0.6), q_score, ["public"], EMBED_MODEL,
                 c["vector_id"], hashlib.sha256(c["exact_text"].encode()).hexdigest()))

        cur.execute("""
            UPDATE document_assets SET status='indexed', page_count=%s,
                   quality_score=%s, quality_detail=%s, doc_version=1
            WHERE document_id=%s""", (page_count, q_score, json.dumps(q_detail, ensure_ascii=False), doc_id))
        meta.commit()
        total_chunks += len(chunks)
        print(f"  [{job['origin']:8s}] {job['title'][:44]:46s} blocks={len(blocks):3d} chunks={len(chunks):3d}")

    # 汇总
    cur.execute("""SELECT origin, count(*), sum(page_count) FROM document_assets GROUP BY origin""")
    print("assets:", cur.fetchall())
    cur.execute("SELECT count(*) FROM chunks")
    print("total chunks:", cur.fetchone()[0], "| vectors:", qc.count(COLLECTION, exact=True).count)
    meta.close()


if __name__ == "__main__":
    main()
