"""Local pixel-only OCR with reading rows, confidence and inverse page coordinates."""
from functools import lru_cache
import hashlib
from importlib.metadata import version
from pathlib import Path
import re

import cv2
import numpy as np

OCR_VERSION = "rapidocr-tables-v2"
OCR_DPI = 240
MIN_CONFIDENCE = 0.75


@lru_cache(maxsize=1)
def engine():
    from rapidocr import RapidOCR
    return RapidOCR(params={"Global.log_level": "error", "Global.text_score": 0.3,
        "EngineConfig.onnxruntime.intra_op_num_threads": 2,
        "EngineConfig.onnxruntime.inter_op_num_threads": 1})


def model_manifest(ocr=None):
    ocr = ocr or engine()
    root = Path(ocr.cfg.Global.model_root_dir)
    return {"backend": "RapidOCR", "package_version": version("rapidocr"),
            "model_source": "PaddleOCR (Baidu); ONNX conversion by RapidAI",
            "upstream": "https://github.com/RapidAI/RapidOCR",
            "models": [{"name": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                       for p in sorted(root.glob("*.onnx"))]}


def deskew(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    lines = cv2.HoughLinesP(cv2.Canny(gray, 50, 150), 1, np.pi / 1800, 100,
                          minLineLength=image.shape[1] * 0.35, maxLineGap=15)
    angles = []
    if lines is not None:
        for x1, y1, x2, y2 in lines.reshape(-1, 4):
            angle = np.degrees(np.arctan2(y2 - y1, x2 - x1))
            if abs(angle) <= 5:
                angles.append(float(angle))
    angle = float(np.median(angles)) if angles else 0.0
    h, w = image.shape[:2]
    transform = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1)
    processed = cv2.warpAffine(image, transform, (w, h), borderValue=(255, 255, 255))
    return processed, transform, angle


def group_rows(lines):
    """Keep spatially adjacent cells together without inferring missing headers/values."""
    groups = []
    for line in sorted(lines, key=lambda x: (np.mean(np.array(x["box"])[:, 1]), np.min(np.array(x["box"])[:, 0]))):
        box = np.array(line["box"])
        cy = float(box[:, 1].mean())
        height = float(np.ptp(box[:, 1]))
        if groups and abs(cy - groups[-1]["cy"]) <= min(height, groups[-1]["height"]) * 0.55:
            groups[-1]["lines"].append(line)
        else:
            groups.append({"cy": cy, "height": height, "lines": [line]})
    return [sorted(g["lines"], key=lambda x: np.min(np.array(x["box"])[:, 0])) for g in groups]


def page_blocks(page, page_no, *, ocr=None, table_recognizer=None):
    pix = page.get_pixmap(dpi=OCR_DPI, alpha=False)
    image = cv2.imdecode(np.frombuffer(pix.tobytes("png"), dtype=np.uint8), cv2.IMREAD_COLOR)
    processed, transform, angle = deskew(image)
    result = (ocr or engine())(processed)
    lines, rejected = [], []
    for box, text, score in zip(result.boxes if result.boxes is not None else [], result.txts or [], result.scores or []):
        line = {"box": np.asarray(box).tolist(), "text": text, "confidence": float(score)}
        (lines if score >= MIN_CONFIDENCE else rejected).append(line)
    inverse = cv2.invertAffineTransform(transform)
    blocks = []
    for row in group_rows(lines):
        points = np.concatenate([np.array(line["box"]) for line in row])
        raw = cv2.transform(points.reshape(1, -1, 2).astype(np.float32), inverse)[0]
        raw = raw * (72 / OCR_DPI)
        bbox = [max(0, float(raw[:, 0].min())), max(0, float(raw[:, 1].min())),
                min(float(page.rect.width), float(raw[:, 0].max())), min(float(page.rect.height), float(raw[:, 1].max()))]
        text = " | ".join(line["text"] for line in row)
        heading = bool(re.match(r"^[一二三四五六七八九十]+[、．.]", text))
        blocks.append({"page_no": page_no, "bbox": [round(x, 2) for x in bbox],
            "block_type": "title" if heading else "paragraph", "heading_level": 2 if heading else None,
            "text": text, "table_cells": None, "ocr_confidence": min(line["confidence"] for line in row)})
    tables, table_findings = [], []
    if ocr is None or table_recognizer is not None:
        from ingestion.tables import recover
        tables, table_findings = recover(processed, lines, inverse=inverse,
            page_size=[float(page.rect.width), float(page.rect.height)], dpi=OCR_DPI,
            page_no=page_no, group_rows=group_rows, recognizer=table_recognizer)
        # Recovered cells replace rows; text outside the table stays in reading order.
        blocks = [b for b in blocks if not any(t["bbox"][0] <= (b["bbox"][0]+b["bbox"][2])/2 <= t["bbox"][2]
            and t["bbox"][1] <= (b["bbox"][1]+b["bbox"][3])/2 <= t["bbox"][3] for t in tables)] + tables
    warnings = ["OCR_CONFIDENCE_UNCALIBRATED"]
    if not tables or any(t["table_cells"]["structure_status"] != "recovered" for t in tables):
        warnings.append("TABLE_STRUCTURE_NOT_RECOVERED")
    detail = {"page_no": page_no, "route": "pixel_ocr", "ocr_version": OCR_VERSION,
              "dpi": OCR_DPI, "skew_degrees": round(angle, 4),
              "raw_to_processed_px": transform.tolist(), "processed_to_raw_px": inverse.tolist(),
              "render_sha256": hashlib.sha256(pix.tobytes("png")).hexdigest(),
              "recognized_lines": len(lines), "low_confidence_candidates": rejected,
              "model_confidence_min": min((line["confidence"] for line in lines), default=None),
              "model_confidence_mean": float(np.mean([line["confidence"] for line in lines])) if lines else None,
              "tables": table_findings, "warnings": warnings}
    return blocks, detail
