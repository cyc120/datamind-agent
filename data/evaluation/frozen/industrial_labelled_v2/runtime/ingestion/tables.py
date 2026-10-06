"""Bounded recovery of single-line dark headers above aligned OCR rows.

No labels or reference documents are inputs. Unsupported cells stay missing.
"""
from functools import lru_cache
import hashlib
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / ".cache/ocr_models/ch_PP-OCRv5_rec_server.onnx"
MODEL_SHA = "e09385400eaaaef34ceff54aeb7c4f0f1fe014c27fa8b9905d4709b65746562a"
TABLE_VERSION = "dark-header-grid-v1"


@lru_cache(maxsize=1)
def header_engine():
    if not MODEL_PATH.is_file():
        raise FileNotFoundError("TABLE_HEADER_MODEL_UNAVAILABLE")
    if hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest() != MODEL_SHA:
        raise ValueError("TABLE_HEADER_MODEL_HASH_MISMATCH")
    from rapidocr import RapidOCR, ModelType, OCRVersion
    return RapidOCR(params={"Global.log_level": "error", "Rec.ocr_version": OCRVersion.PPOCRV5,
        "Rec.model_type": ModelType.SERVER, "Rec.model_path": str(MODEL_PATH),
        "EngineConfig.onnxruntime.intra_op_num_threads": 2, "EngineConfig.onnxruntime.inter_op_num_threads": 1})


def header_crop(gray, cell):
    """Find ink relative to row background; exclude border strokes before normalization."""
    x0, y0, x1, y1 = map(int, cell)
    roi = gray[y0 + 5:y1 - 6, x0 + 7:x1 - 7]
    if min(roi.shape, default=0) < 8:
        return None, None
    mask = np.uint8(roi.astype(float) < np.median(roi, axis=1)[:, None] - 6) * 255
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if cv2.contourArea(contour) > 4 and h >= 8 and w / h < 4 and \
                .1 * roi.shape[1] < x + w / 2 < .9 * roi.shape[1] and w < .6 * roi.shape[1]:
            boxes.append((x, y, w, h))
    if not boxes:
        return None, None
    left = max(0, min(b[0] for b in boxes) - 2)
    top = max(0, min(b[1] for b in boxes) - 1)
    right = min(roi.shape[1], max(b[0] + b[2] for b in boxes) + 2)
    bottom = min(roi.shape[0], max(b[1] + b[3] for b in boxes) + 1)
    crop = roi[top:bottom, left:right]
    low, high = float(crop.min()), float(np.percentile(crop, 95))
    if high - low < 8:
        return None, None
    crop = np.clip((crop.astype(float) - low) * 255 / (high - low), 0, 255).astype("uint8")
    return cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR), [x0 + 7 + left, y0 + 5 + top, x0 + 7 + right, y0 + 5 + bottom]


def page_bbox(box, inverse, page_size, dpi):
    x0, y0, x1, y1 = box
    points = np.array([[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]], dtype=np.float32)
    raw = cv2.transform(points, inverse)[0] * (72 / dpi)
    return [round(max(0, float(raw[:, 0].min())), 2), round(max(0, float(raw[:, 1].min())), 2),
            round(min(page_size[0], float(raw[:, 0].max())), 2), round(min(page_size[1], float(raw[:, 1].max())), 2)]


def recover(image, lines, *, inverse, page_size, dpi, page_no, group_rows, recognizer=None):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    contours, _ = cv2.findContours(np.uint8(gray < 150) * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    bands = sorted([cv2.boundingRect(c) for c in contours if
        cv2.boundingRect(c)[2] > image.shape[1] * .4 and 20 < cv2.boundingRect(c)[3] < image.shape[0] * .08
        and cv2.boundingRect(c)[2] / cv2.boundingRect(c)[3] > 8], key=lambda b: b[1])[:3]
    tables, findings = [], []
    for x, y, w, h in bands:
        candidates = [line for line in lines if y + h < np.mean(np.array(line["box"])[:, 1]) < y + h * 16
            and x < np.mean(np.array(line["box"])[:, 0]) < x + w]
        rows = [row for row in group_rows(candidates) if 2 <= len(row) <= 12]
        contiguous = []
        for row in rows:
            cy = float(np.mean([np.mean(np.array(c["box"])[:, 1]) for c in row]))
            if not contiguous and cy - y - h > h * 1.5:
                break
            if contiguous and cy - contiguous[-1][0] > h * 1.8:
                break
            contiguous.append((cy, row))
        if len(contiguous) < 2:
            continue
        ncols = max(len(row) for _, row in contiguous)
        full = [row for _, row in contiguous if len(row) == ncols]
        centers = np.median([[np.mean(np.array(c["box"])[:, 0]) for c in row] for row in full], axis=0)
        if np.min(np.diff(centers)) < w * .07:
            continue
        cuts = [x, *((centers[:-1] + centers[1:]) / 2), x + w]
        crop_inputs, crops = [], []
        for col in range(ncols):
            crop, crop_box = header_crop(gray, [cuts[col], y, cuts[col + 1], y + h])
            if crop is not None:
                crop_inputs.append((col, crop_box, hashlib.sha256(crop.tobytes()).hexdigest()))
                crops.append(crop)
        recognized = []
        model_error = None
        if crops:
            try:
                from rapidocr.ch_ppocr_rec.typings import TextRecInput
                output = (recognizer or header_engine().text_rec)(TextRecInput(img=crops))
                recognized = list(zip(output.txts, output.scores))
                if len(recognized) != len(crops):
                    raise ValueError("TABLE_HEADER_RESULT_COUNT_MISMATCH")
            except (OSError, ValueError, RuntimeError) as exc:
                model_error = type(exc).__name__ + ":" + str(exc)[:80]
                recognized = []
        headers = [{"text": None, "confidence": None, "status": "missing_header",
            "bbox": page_bbox([cuts[i], y, cuts[i + 1], y + h], inverse, page_size, dpi)} for i in range(ncols)]
        for (col, crop_box, digest), (text, score) in zip(crop_inputs, recognized):
            headers[col].update({"raw_candidate": text, "confidence": float(score), "crop_sha256": digest,
                "text_bbox": page_bbox(crop_box, inverse, page_size, dpi)})
            if text and score >= .75:
                headers[col].update({"text": text, "status": "recognized"})
        grid = []
        for _, row in contiguous:
            cells = [{"text": None, "confidence": None, "status": "unreadable_or_blank", "bbox": None} for _ in range(ncols)]
            for line in row:
                box = np.array(line["box"])
                col = int(np.searchsorted(cuts, box[:, 0].mean(), side="right") - 1)
                if not 0 <= col < ncols:
                    continue
                if cells[col]["text"] is not None or cells[col]["status"] == "ambiguous_cell":
                    cells[col]["status"] = "ambiguous_cell"
                    cells[col]["text"] = None
                    continue
                cells[col] = {"text": line["text"], "confidence": line["confidence"], "status": "recognized",
                    "bbox": page_bbox([box[:, 0].min(), box[:, 1].min(), box[:, 0].max(), box[:, 1].max()], inverse, page_size, dpi)}
            grid.append(cells)
        cy = [value for value, _ in contiguous]
        gap = float(np.median(np.diff(cy)))
        table_box = [x, y, x + w, cy[-1] + gap / 2]
        complete = all(c["status"] == "recognized" for c in headers + [c for row in grid for c in row])
        data = {"n_rows": len(grid) + 1, "n_cols": ncols, "cells": [[c["text"] for c in headers]] +
            [[c["text"] for c in row] for row in grid], "cell_details": [headers] + grid,
            "structure_version": TABLE_VERSION, "structure_status": "recovered" if complete else "partial",
            "page_size": list(page_size), "coordinate_system": "pdf_points"}
        labels = [c["text"] or f"未识别列{i+1}" for i, c in enumerate(headers)]
        markdown = "| " + " | ".join(labels) + " |\n| " + " | ".join(["---"] * ncols) + " |\n"
        markdown += "\n".join("| " + " | ".join(c["text"] or "未识别" for c in row) + " |" for row in grid)
        tables.append({"page_no": page_no, "bbox": page_bbox(table_box, inverse, page_size, dpi), "block_type": "table",
            "text": markdown, "table_cells": data, "ocr_confidence": min((c["confidence"] for row in [headers] + grid
                for c in row if c["confidence"] is not None), default=None)})
        findings.append({"structure_status": data["structure_status"], "headers": headers, "model_error": model_error,
            "header_model": {"name": MODEL_PATH.name, "sha256": MODEL_SHA, "upstream": "https://github.com/RapidAI/RapidOCR"},
            "body_row_count": len(grid), "column_count": ncols, "body_cy_processed_px": cy})
    return tables, findings
