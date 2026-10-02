"""Typed candidates from explicit accepted cell labels, with three source locators.

No header completion, SQL entity matching, date inference or automatic approval.
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import re

FACT_VERSION = "document-facts-v1"
METRICS = {"得分": "inspection_score", "抽检数": "sample_count", "外观瑕疵": "appearance_defects",
           "功能不良": "functional_defects", "不良率": "defect_rate"}
ENTITIES = {"门店": "store", "产品": "product"}


def normalize_value(raw, metric):
    text = raw.strip()
    if metric == "defect_rate":
        if not re.fullmatch(r"\d+(?:\.\d+)?[%％]", text):
            raise ValueError("EXPLICIT_PERCENT_REQUIRED")
        value = Decimal(text[:-1]) / 100
        if not 0 <= value <= 1:
            raise ValueError("RATE_OUT_OF_RANGE")
        return format(value, "f"), "1"
    if not re.fullmatch(r"\d+", text):
        raise ValueError("INTEGER_REQUIRED")
    return str(int(text)), None  # The cell/header does not specify a physical unit.


def extract(blocks, *, document_id, source_sha256, parser_version, document_version=1):
    facts, unresolved, checks = [], [], []
    for block in blocks:
        table = block.get("table_cells") or {}
        grid = table.get("cell_details") or []
        if len(grid) < 2 or table.get("coordinate_system") != "pdf_points":
            continue  # Native tables without cell locators are not typed yet.
        headers = grid[0]
        subjects = [i for i, c in enumerate(headers) if c.get("status") == "recognized" and c.get("text") in ENTITIES]
        if len(subjects) != 1:
            unresolved.append({"block_id": block["block_id"], "reason": "ENTITY_HEADER_UNRESOLVED"})
            continue
        entity_col = subjects[0]
        for col, header in enumerate(headers):
            if header.get("status") != "recognized":
                unresolved.append({"block_id": block["block_id"], "column": col, "reason": "HEADER_UNRESOLVED"})
        for row_no, row in enumerate(grid[1:], 1):
            entity = row[entity_col]
            if entity.get("status") != "recognized" or not entity.get("text"):
                unresolved.append({"block_id": block["block_id"], "row": row_no, "reason": "ENTITY_CELL_UNRESOLVED"})
                continue
            row_facts = {}
            for col, header in enumerate(headers):
                metric = METRICS.get(header.get("text")) if header.get("status") == "recognized" else None
                if not metric:
                    continue
                cell = row[col]
                if cell.get("status") != "recognized" or not cell.get("text"):
                    unresolved.append({"block_id": block["block_id"], "row": row_no, "column": col, "reason": "VALUE_CELL_UNRESOLVED"})
                    continue
                try:
                    value, unit = normalize_value(cell["text"], metric)
                except (ValueError, InvalidOperation) as exc:
                    unresolved.append({"block_id": block["block_id"], "row": row_no, "column": col, "reason": str(exc)})
                    continue
                selected = [("entity", entity, entity_col), ("header", header, col), ("value", cell, col)]
                if not all(c.get("bbox") for _, c, _ in selected) or not table.get("page_size"):
                    unresolved.append({"block_id": block["block_id"], "row": row_no, "column": col, "reason": "CELL_LOCATOR_UNAVAILABLE"})
                    continue
                refs = [{"role": role, "block_id": block["block_id"], "page_no": block["page_no"],
                         "row": 0 if role == "header" else row_no, "column": idx, "text": c["text"],
                         "bbox": c["bbox"], "page_size": table["page_size"], "coordinate_system": "pdf_points",
                         "source_sha256": source_sha256, "block_content_hash": hashlib.sha256(block["text"].encode()).hexdigest()}
                        for role, c, idx in selected]
                identity = f"{document_id}:{document_version}:{parser_version}:{block['block_id']}:{row_no}:{col}"
                fact = {"fact_id": "fact-" + hashlib.sha256(identity.encode()).hexdigest()[:24],
                        "document_id": document_id, "document_version": document_version,
                        "source_sha256": source_sha256, "parser_version": parser_version, "extraction_version": FACT_VERSION,
                        "entity_name": entity["text"], "entity_type": ENTITIES[headers[entity_col]["text"]],
                        "entity_id": None, "metric": metric, "raw_header": header["text"], "raw_value": cell["text"],
                        "raw_unit": "%" if metric == "defect_rate" else None,
                        "normalized_value": value, "normalized_unit": unit, "batch": None, "operation": None,
                        "valid_from": None, "valid_to": None, "approval_status": "unreviewed",
                        "verification_status": "candidate", "evidence_refs": refs,
                        "ocr_confidence": min((c["confidence"] for _, c, _ in selected if c.get("confidence") is not None), default=None)}
                facts.append(fact)
                row_facts[metric] = fact
            required = {"sample_count", "appearance_defects", "functional_defects", "defect_rate"}
            if required <= row_facts.keys():
                n = Decimal(row_facts["sample_count"]["normalized_value"])
                rate = Decimal(row_facts["defect_rate"]["normalized_value"]) * 100
                expected = ((Decimal(row_facts["appearance_defects"]["normalized_value"]) +
                             Decimal(row_facts["functional_defects"]["normalized_value"])) / n * 100
                            ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP) if n else None
                checks.append({"entity_name": entity["text"], "block_id": block["block_id"], "row": row_no,
                               "check": "defect_rate_arithmetic", "status": "passed" if expected == rate else "mismatch",
                               "computed_percent": str(expected) if expected is not None else None,
                               "source_percent": str(rate), "semantic_verification": "not_independently_verified"})
    return facts, {"extraction_version": FACT_VERSION, "fact_count": len(facts), "unresolved": unresolved,
                   "arithmetic_checks": checks, "approval_status": "unreviewed",
                   "scope": "Explicit scan cells only; entity IDs, batch, validity and approval are unknown"}


def payload_hash(payload):
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def persist(cur, document_id, facts):
    """Called inside the same document metadata transaction as blocks and chunks."""
    from psycopg2.extras import Json
    cur.execute("DELETE FROM document_facts WHERE document_id=%s", (document_id,))
    for fact in facts:
        cur.execute("""INSERT INTO document_facts(fact_id,document_id,document_version,source_sha256,parser_version,
                       extraction_version,entity_name,entity_type,metric,raw_value,normalized_value,normalized_unit,
                       approval_status,verification_status,payload,payload_sha256,
                       fact_kind,entity_id,operation_no,batch_id,normalized_range)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    tuple(fact[key] for key in ("fact_id", "document_id", "document_version", "source_sha256",
                        "parser_version", "extraction_version", "entity_name", "entity_type", "metric", "raw_value",
                        "normalized_value", "normalized_unit", "approval_status", "verification_status")) +
                    (Json(fact), payload_hash(fact), fact.get('fact_kind','measurement'), fact.get('entity_id'),
                     fact.get('operation_no'), fact.get('batch_id'), Json(fact['normalized_range']) if fact.get('normalized_range') else None))
