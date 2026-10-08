# -*- coding: utf-8 -*-
"""
ingest.py — 知识库摄取管线（里程碑 M6/M7 简化版）
流程: 登记文档资产 → 解析(块/表格/页图) → 标题树切片 → BGE 向量化 →
      Qdrant 入库 + PostgreSQL 元数据落库

说明:
- 扫描页只读取原 PDF 像素，通过本地 OCR 识别，不读取原生对照。
- OHR-Bench 样张仅摄取 gt_text；noise_text 保留在 manifest 供鲁棒性评测。

用法: py -3.13 ingestion/ingest.py [--reset]
"""
import argparse
import hashlib
import json
import uuid
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras
from psycopg2.extensions import make_dsn
import pymupdf
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from datamind import task_budget
from ingestion import formula, facts
KB_DIR = ROOT / "data" / "knowledge_base"
OHR_DIR = ROOT / "data" / "ohr_bench_samples"
PAGE_IMG_DIR = ROOT / "data" / "object_store" / "page_images"

PG = dict(host="localhost", port=5432, user="datamind_app",
          password="datamind_app_dev", dbname="datamind_meta")
META_DSN = make_dsn(**PG)
FORMULA_SLOTS = 1
OCR_SLOTS = 2
QDRANT = "http://localhost:6333"
COLLECTION = "datamind_knowledge"
EMBED_MODEL = "BAAI/bge-small-zh-v1.5"
EMBED_DIM = 512
EMBED_CACHE = ROOT / ".cache" / "fastembed"

CHUNK_TARGET = 500   # 字符（中文）
CHUNK_MAX = 800
CHUNK_OVERLAP = 80
PARSER_VERSION = "pdf-ocr-0.4"


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


_PDF_ADAPTER = 'pdf-native'


def _pdf_contract_payload(*, pdf_path, doc_id, blocks, page_sizes, detail, parser_version):
    """Adapter handler: map real PDF blocks into a contract payload.

    `dispatch` validates whatever this returns; it never fills a missing value in.
    """
    from datamind import parse_contract

    mapped = parse_contract.from_pdf_blocks(
        blocks, page_sizes=page_sizes, parser_version=parser_version, document_id=doc_id,
        source_version=detail["source_sha256"])
    return {
        "classification": {"detected_media_type": "application/pdf", "source_name": pdf_path.name},
        "adapter_capabilities": sorted({block["block_type"] for block in blocks if block.get("block_type")}) or ["text"],
        "blocks": mapped, "relations": [],
        "parsed_scope": [{"kind": "page", "ref": page, "status": "parsed"} for page in sorted(page_sizes)],
        "failed_scope": [{"kind": "page", "ref": page["page_no"], "status": "failed",
                          "detail": page.get("error_code")}
                         for page in detail["pages"] if page.get("error_code")],
        "quality_findings": [], "unsupported_features": [], "artifact_refs": [],
        "manifest": {"parser_version": parser_version, "source_sha256": detail["source_sha256"]},
        "parse_status": detail["parse_status"],
    }


def _ensure_pdf_adapter():
    """Register the PDF adapter once; routing then finds it by detected media type."""
    from datamind import parse_contract

    if _PDF_ADAPTER not in parse_contract.registered_adapters():
        parse_contract.register_adapter(
            _PDF_ADAPTER, media_types=["application/pdf"],
            capabilities=["native_text", "pixel_ocr", "tables", "formula"],
            handler=_pdf_contract_payload)


BLOCK_INSERT_SQL = """
    INSERT INTO document_blocks(block_id, document_id, page_no, bbox, block_type,
        text, table_cells, formula_latex, reading_order, heading_level,
        ocr_confidence, parser_version, content_hash)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"""

LOCATOR_INSERT_SQL = """
    INSERT INTO document_block_locators(block_id, document_id, contract_version, locator)
    VALUES (%s,%s,%s,%s)"""


def _block_row(block, doc_id):
    """Row for `document_blocks`; unchanged historical shape (no contract columns)."""
    return (block["block_id"], doc_id, block["page_no"],
            json.dumps(block["bbox"]), block["block_type"], block["text"],
            json.dumps(block["table_cells"], ensure_ascii=False) if block.get("table_cells") else None,
            block.get("formula_latex"), block["reading_order"], block.get("heading_level"),
            block.get("ocr_confidence"), PARSER_VERSION,
            hashlib.sha256(block["text"].encode()).hexdigest())


def _locator_row(block, doc_id):
    """Row for `document_block_locators`, or None when the block has no validated locator.

    A block parsed before the contract existed writes nothing instead of a fabricated
    locator, so `document_block_locators` only ever holds validated rows.
    """
    locator = block.get("contract_locator")
    if not locator:
        return None
    return (block["block_id"], doc_id, block["contract_version"],
            json.dumps(locator, ensure_ascii=False))


def _contract_summary(document, pdf_path, doc_id, blocks, detail):
    """Route the parsed blocks through the registered adapter for this media type.

    Ingestion is never aborted by a contract violation: the stable error code is written
    into the quality detail so the gap stays visible instead of being hidden, and the
    existing block output is left untouched.
    """
    from fastapi import HTTPException

    from datamind import parse_contract

    version = parse_contract.CONTRACT_VERSION
    if not blocks:
        return {"contract_version": version, "validation": "skipped", "reason": "no_blocks"}
    page_sizes = {index + 1: (float(page.rect.width), float(page.rect.height))
                  for index, page in enumerate(document)}
    _ensure_pdf_adapter()
    try:
        detected = parse_contract.detect_media_type(pdf_path)
        result = parse_contract.dispatch(detected, pdf_path=pdf_path, doc_id=doc_id, blocks=blocks,
                                        page_sizes=page_sizes, detail=detail,
                                        parser_version=PARSER_VERSION)
    except HTTPException as error:
        code = error.detail.get("error_code") if isinstance(error.detail, dict) else None
        return {"contract_version": version, "validation": "failed",
                "error_code": code or "PARSE_CONTRACT_FAILED", "blocks": len(blocks)}
    payload = result.payload
    locators = {block["block_id"]: block["locator"] for block in payload["blocks"]}
    for block in blocks:
        # Carry the validated locator with the block so persistence writes the same object
        # that was validated, instead of a re-derived copy.
        block["contract_locator"] = locators.get(block.get("block_id"))
        block["contract_version"] = payload["contract_version"]
    return {"contract_version": payload["contract_version"], "validation": "parsed",
            "adapter": _PDF_ADAPTER, "parse_status": payload["parse_status"],
            "blocks": len(payload["blocks"]),
            "locators_written": sum(1 for block in blocks if block.get("contract_locator")),
            "locator_kinds": sorted({block["locator"]["locator_kind"] for block in payload["blocks"]})}


def parse_pdf(pdf_path: Path, doc_id: str, ocr_proxy: Path | None = None, *, include_quality=False,
              ocr_engine=None, formula_engine=None, formula_dsn=None, formula_slots=None,
              formula_deadline=None, ocr_dsn=None, ocr_deadline=None, ocr_slots=None):
    """Parse native text or original pixels. Reference text is never an input."""
    if ocr_proxy is not None:
        raise ValueError("OCR_REFERENCE_INPUT_FORBIDDEN")
    blocks = []
    detail = {"pages": [], "formula_candidates": [], "reference_inputs_used": False,
              "parser_version": PARSER_VERSION, "source_sha256": sha256_file(pdf_path),
              "quality_calibration": "not_calibrated", "note": "原生 PDF 文本/图片与原扫描像素解析"}
    doc = pymupdf.open(pdf_path)
    page_count = len(doc)

    # 页面截图（供前端证据定位）
    img_dir = PAGE_IMG_DIR / doc_id
    img_dir.mkdir(parents=True, exist_ok=True)
    for pno in range(page_count):
        pix = doc[pno].get_pixmap(dpi=110)
        (img_dir / f"page_{pno + 1:03d}.png").write_bytes(pix.tobytes("png"))

    figure_inputs = []
    for pno in range(page_count):
        page = doc[pno]
        tpage = page
        native_chars = len(page.get_text().strip())
        if native_chars < 30:
            try:
                from ingestion import ocr
                if ocr_deadline is not None:
                    # Opt-in: the native OCR session runs in its own process group under
                    # the shared budget instead of the parsing process.
                    page_ocr, page_detail = ocr.page_blocks_bounded(
                        pdf_path, pno + 1, dsn=ocr_dsn, deadline_s=ocr_deadline, slots=ocr_slots)
                else:
                    page_ocr, page_detail = ocr.page_blocks(page, pno + 1, ocr=ocr_engine)
                blocks.extend(page_ocr)
                detail["ocr_model"] = ocr.model_manifest(ocr_engine)
                detail["pages"].append(page_detail)
            except Exception as exc:
                detail["pages"].append({"page_no": pno + 1, "route": "pixel_ocr",
                                        "error_code": getattr(exc, "code", None) or "OCR_FAILED",
                                        "exception_type": type(exc).__name__})
            continue
        detail["pages"].append({"page_no": pno + 1, "route": "native_text", "native_chars": native_chars})

        # ---- 原生文本页表格识别 ----
        tables = None
        try:
            tables = tpage.find_tables()
        except Exception:
            pass
        table_bboxes = []
        for ti, tab in enumerate(tables.tables if tables else []):
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
                "ocr_confidence": None,
            })

        # ---- 文本块 ----
        d = tpage.get_text("dict")
        for blk in d["blocks"]:
            if blk["type"] != 0:
                if blk["type"] == 1:
                    figure = {"page_no": pno + 1, "block_type": "figure", "bbox": list(blk["bbox"]),
                              "text": "", "heading_level": None, "table_cells": None, "ocr_confidence": None}
                    blocks.append(figure)
                    bb = pymupdf.Rect(blk["bbox"])
                    if bb.width > 2 * bb.height and bb.height < page.rect.height * .15 and len(figure_inputs) < 6:
                        crop_dir = ROOT / ".cache/formula" / detail["source_sha256"]
                        crop_dir.mkdir(parents=True, exist_ok=True)
                        crop_path = crop_dir / f"page-{pno+1}-figure-{len(figure_inputs)}.png"
                        crop_path.write_bytes(blk["image"])
                        figure_inputs.append((figure, crop_path))
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
                "ocr_confidence": None,
            })

    if figure_inputs:
        parsed = (formula_engine or (lambda crops: formula.recognize(
            crops, dsn=formula_dsn, slots=formula_slots, deadline_s=formula_deadline)))(
                [path for _, path in figure_inputs])
        detail["formula_model"] = parsed.get("manifest")
        detail["formula_error"] = parsed.get("error_code")
        for (block, crop), candidate in zip(figure_inputs, parsed.get("results", [])):
            accepted = formula.valid_latex(candidate.get("latex"))
            detail["formula_candidates"].append({**candidate, "page_no": block["page_no"], "bbox": block["bbox"],
                "structural_check": "passed" if accepted else "failed", "semantic_verification": "not_independently_verified"})
            if accepted:
                block.update({"block_type": "formula", "formula_latex": candidate["latex"],
                              "text": candidate["latex"], "ocr_confidence": None})
    detail["unparsed_figures"] = sum(b["block_type"] == "figure" for b in blocks)
    detail["parse_status"] = "partial" if detail["unparsed_figures"] or any(
        p.get("error_code") or p.get("warnings") for p in detail["pages"]) else "parsed"
    if not any(b["text"] for b in blocks):
        detail["parse_status"] = "failed"
    # 阅读顺序: 页内按 y,x 排序
    blocks.sort(key=lambda b: (b["page_no"], b["bbox"][1], b["bbox"][0]))
    for i, b in enumerate(blocks):
        b["reading_order"] = i
        # Assigned here so the contract mapping and the database row share one identity.
        b["block_id"] = f"blk-{md5(doc_id + str(i))}"
    detail["parse_contract"] = _contract_summary(doc, pdf_path, doc_id, blocks, detail)
    doc.close()
    return (blocks, page_count, detail) if include_quality else (blocks, page_count)


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

    for bi, b in enumerate(blocks):
        if not b["text"] or b["text"].startswith("DataMind Agent 演示知识库") or b["text"].strip().startswith("第 ") and b["text"].strip().endswith(" 页"):
            continue
        if buf_pages and b["page_no"] != buf_pages[-1]:
            flush()
        if b["block_type"] == "table":
            flush()
            caption = next((candidate for candidate in blocks[bi + 1:bi + 3]
                if candidate["page_no"] == b["page_no"] and candidate["text"].startswith("表 ")), None)
            table_text = ((caption["text"] + "\n") if caption else "") + b["text"]
            chunks.append({
                "chunk_id": f"chk-{md5(doc_id + 'tbl' + str(len(chunks)))}",
                "document_id": doc_id,
                "chunk_type": "table",
                "section_path": [t for _, t in heading_stack],
                "page_start": b["page_no"], "page_end": b["page_no"],
                "block_ids": [b["block_id"]] + ([caption["block_id"]] if caption else []),
                "text_for_embedding": (" > ".join(t for _, t in heading_stack) + "\n" +
                                       table_text)[:4000],
                "exact_text": table_text,
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
            buf_blocks.append(b["block_id"])
            continue
        text = b["text"]
        while text:
            available = CHUNK_MAX - sum(len(x) + 1 for x in buf)
            if available < CHUNK_OVERLAP:
                flush()
                available = CHUNK_MAX
            part = text[:available]
            buf.append(part)
            buf_pages.append(b["page_no"])
            buf_blocks.append(b["block_id"])
            if len(text) <= available:
                break
            text = text[max(1, available - CHUNK_OVERLAP):]
            flush()
    flush()

    # 二次切分过长 chunk（标题树已保证基本粒度，这里仅防御）
    return chunks


# ============================================================
# 主流程
# ============================================================
def main():
    global FORMULA_SLOTS
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="清空后重建索引")
    ap.add_argument("--refresh", action="store_true", help="保留文档资产，重新解析并刷新切片/向量")
    ap.add_argument("--formula-slots", type=int, default=FORMULA_SLOTS, metavar="N",
                    help="跨进程公式会话槽上限（1–8）；无空槽时该文档公式保留为解析缺口")
    ap.add_argument("--formula-deadline", type=float, metavar="SECONDS",
                    help="公式会话挂钟时限（0.5–3600），默认 90")
    ap.add_argument("--ocr-deadline", type=float, metavar="SECONDS",
                    help="扫描页 OCR 在独立进程组内的挂钟时限（0.5–3600）；默认在解析进程内执行")
    ap.add_argument("--ocr-slots", type=int, default=OCR_SLOTS, metavar="N",
                    help="跨进程 OCR 槽上限（1–8）；无空槽时该页保留为解析缺口")
    args = ap.parse_args()
    if not 1 <= args.formula_slots <= 8:
        ap.error("--formula-slots must be between 1 and 8")
    if not 1 <= args.ocr_slots <= 8:
        ap.error("--ocr-slots must be between 1 and 8")
    for name in ("formula_deadline", "ocr_deadline"):
        value = getattr(args, name)
        if value is None:
            continue
        try:
            setattr(args, name, task_budget.validate_deadline(value))
        except ValueError:
            ap.error(f"--{name.replace('_', '-')} must be between 0.5 and 3600 seconds")
    FORMULA_SLOTS = args.formula_slots

    from db.migrate import migrate
    migrate()
    meta = psycopg2.connect(**PG)
    cur = meta.cursor()

    if args.reset:
        print("resetting knowledge index ...")
        cur.execute("DELETE FROM chunks")
        cur.execute("DELETE FROM document_blocks")
        cur.execute("DELETE FROM ingest_jobs")
        cur.execute("DELETE FROM document_assets")
        meta.commit()
        qc = QdrantClient(url=QDRANT, trust_env=False)
        if qc.collection_exists(COLLECTION):
            qc.delete_collection(COLLECTION)

    # ---------- 向量模型 ----------
    print(f"loading embedding model {EMBED_MODEL} ...")
    from fastembed import TextEmbedding
    model = TextEmbedding(model_name=EMBED_MODEL, cache_dir=str(EMBED_CACHE), threads=2)

    qc = QdrantClient(url=QDRANT, trust_env=False)
    rebuild_vectors = not qc.collection_exists(COLLECTION)
    if rebuild_vectors:
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
        jobs.append({"path": pdf, "origin": origin, "file_type": "pdf",
                     "title": pdf.stem, "info": info,
                     "collection": "demo_kb"})
    for js in sorted(OHR_DIR.glob("ohr-*.json")):
        rec = json.loads(js.read_text(encoding="utf-8"))
        jobs.append({"path": js, "origin": "ohrbench", "file_type": "json",
                     "title": f"[{rec['sample_id']}/OHR-Bench/{rec['domain']}] {rec['doc_name']} p{rec['page_idx'] + 1}",
                     "info": {"desc": f"OHR-Bench 真实语料 ({rec['domain']})"},
                     "record": rec, "collection": "ohrbench_kb"})

    print(f"ingesting {len(jobs)} documents ...")
    total_chunks = 0
    for job in jobs:
        path, doc_id = job["path"], "doc-" + md5(job["title"] + sha256_file(job["path"])[:8])
        digest = sha256_file(path)

        cur.execute("""SELECT status,parser_version FROM document_assets WHERE document_id=%s""", (doc_id,))
        row = cur.fetchone()
        if row and row == ("indexed", PARSER_VERSION) and not args.refresh and not rebuild_vectors:
            print(f"  skip (indexed): {job['title']}")
            continue

        cur.execute("""
            INSERT INTO document_assets(document_id, title, collection, file_type, origin,
                sha256, object_uri, page_count, language, acl, quality_score, quality_detail,
                parser_version, status, uploaded_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'parsing',now())
            ON CONFLICT (document_id) DO UPDATE SET status='parsing'""",
            (doc_id, job["title"], job["collection"], job["file_type"], job["origin"],
             digest, str(path), 0, "zh", ["public"], None, None, PARSER_VERSION))

        if job["file_type"] == "pdf":
            blocks, page_count, q_detail = parse_pdf(path, doc_id, include_quality=True,
                                                     formula_dsn=META_DSN, formula_slots=FORMULA_SLOTS,
                                                     formula_deadline=args.formula_deadline,
                                                     ocr_dsn=META_DSN, ocr_deadline=args.ocr_deadline,
                                                     ocr_slots=args.ocr_slots)
            q_detail["document_type"] = job["info"].get("type")
            q_score = None  # Model confidence is not a calibrated document quality score.
        else:
            rec = job["record"]
            page_count = 1
            blocks = [{"page_no": rec['page_idx'] + 1, "block_type": "paragraph",
                       "bbox": [0, 0, 0, 0], "text": rec["gt_text"],
                       "heading_level": None, "table_cells": None,
                       "reading_order": 0, "ocr_confidence": 1.0}]
            q_detail = {"text_coverage": 1.0, "source_page_no": rec['page_idx'] + 1,
                        "note": "OHR-Bench 人工校准 gt_text（原始样本转录，不是本项目 OCR）"}
            q_score = 1.0

        # document_blocks
        cur.execute("DELETE FROM document_blocks WHERE document_id=%s", (doc_id,))
        cur.execute("DELETE FROM document_block_locators WHERE document_id=%s", (doc_id,))
        for b in blocks:
            cur.execute(BLOCK_INSERT_SQL, _block_row(b, doc_id))
        cur.execute("DELETE FROM document_block_locators WHERE document_id=%s", (doc_id,))
        for b in blocks:
            if (locator_row := _locator_row(b, doc_id)) is not None:
                cur.execute(LOCATOR_INSERT_SQL, locator_row)

        extracted, field_detail = facts.extract(blocks, document_id=doc_id, source_sha256=digest,
                                               parser_version=PARSER_VERSION)
        facts.persist(cur, doc_id, extracted)
        q_detail["fact_extraction"] = field_detail
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
        from qdrant_client.models import FieldCondition, Filter, FilterSelector, MatchValue
        qc.delete(COLLECTION, points_selector=FilterSelector(filter=Filter(must=[
            FieldCondition(key="document_id", match=MatchValue(value=doc_id))])), wait=True)
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
            UPDATE document_assets SET status=%s, needs_review=%s, page_count=%s,
                   quality_score=%s, quality_detail=%s, doc_version=1, parser_version=%s
            WHERE document_id=%s""", ('indexed' if chunks else 'failed', not bool(chunks), page_count,
                q_score, json.dumps(q_detail, ensure_ascii=False), PARSER_VERSION, doc_id))
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
