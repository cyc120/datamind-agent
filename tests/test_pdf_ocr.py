"""Prevent reference leakage, unsafe formula publication and wrong page locators."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import subprocess

import cv2
import numpy as np
import pymupdf

from ingestion import formula, ingest, ocr
from datamind.rag_engine import verify_claims


class PixelOCRTest(unittest.TestCase):
    def test_reference_input_is_rejected_before_opening_source(self):
        with self.assertRaisesRegex(ValueError, "OCR_REFERENCE_INPUT_FORBIDDEN"):
            ingest.parse_pdf(Path("missing.pdf"), "test", Path("counterpart.pdf"))

    def test_rows_confidence_filter_and_inverse_page_coordinates(self):
        original = np.array([[100., 100.], [250., 100.], [250., 140.], [100., 140.]])
        transform = cv2.getRotationMatrix2D((400, 400), 2.0, 1)
        boxes = [cv2.transform(original[None].astype(np.float32), transform)[0],
                 cv2.transform((original + [300, 0])[None].astype(np.float32), transform)[0],
                 cv2.transform((original + [0, 100])[None].astype(np.float32), transform)[0]]
        recognizer = lambda _: SimpleNamespace(boxes=boxes, txts=["产品A", "45", "错误候选"], scores=[.99, .90, .4])
        with pymupdf.open() as doc:
            page = doc.new_page(width=300, height=300)
            with patch.object(ocr, "deskew", side_effect=lambda image: (image, transform, 2.0)):
                blocks, quality = ocr.page_blocks(page, 1, ocr=recognizer)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["text"], "产品A | 45")
        self.assertEqual(blocks[0]["ocr_confidence"], .9)
        np.testing.assert_allclose(blocks[0]["bbox"], [30, 30, 165, 42], atol=.02)
        self.assertEqual(quality["low_confidence_candidates"][0]["text"], "错误候选")
        self.assertIn("TABLE_STRUCTURE_NOT_RECOVERED", quality["warnings"])

    def test_adjacent_product_rows_do_not_merge(self):
        box = np.array([[0, 0], [80, 0], [80, 20], [0, 20]])
        lines = [{"box": (box + offset).tolist(), "text": text} for offset, text in
                 (([0, 0], "A"), ([100, 0], "45"), ([0, 25], "B"), ([100, 25], "40"))]
        self.assertEqual([[c["text"] for c in row] for row in ocr.group_rows(lines)], [["A", "45"], ["B", "40"]])

    def test_scan_failure_never_falls_back_to_reference_text(self):
        source = Path(__file__).resolve().parents[1] / "data/knowledge_base/07_门店巡检记录_2025年12月_扫描版.pdf"
        with tempfile.TemporaryDirectory() as directory, patch.object(ingest, "PAGE_IMG_DIR", Path(directory)), \
             patch.object(ocr, "page_blocks", side_effect=RuntimeError("offline")):
            blocks, _, quality = ingest.parse_pdf(source, "test", include_quality=True)
        self.assertEqual(blocks, [])
        self.assertEqual(quality["parse_status"], "failed")
        self.assertFalse(quality["reference_inputs_used"])
        self.assertEqual(quality["pages"][0]["error_code"], "OCR_FAILED")


class FormulaBoundaryTest(unittest.TestCase):
    def test_malformed_candidates_cannot_become_evidence(self):
        for latex in ("S={S-R", "S=\\999abc", "S=\\unknown{R}", "S=R\\", "S=R&x", "", None):
            self.assertFalse(formula.valid_latex(latex))
        self.assertTrue(formula.valid_latex(r"g=\frac{S_t-S_{t-1}}{S_{t-1}}\times100\%"))

    def test_failed_formula_keeps_original_figure_and_gap(self):
        source = Path(__file__).resolve().parents[1] / "data/knowledge_base/05_经营指标核算与计算公式说明.pdf"
        failed = lambda crops: {"results": [{"latex": "S={"} for _ in crops]}
        with tempfile.TemporaryDirectory() as directory, patch.object(ingest, "PAGE_IMG_DIR", Path(directory)):
            blocks, _, quality = ingest.parse_pdf(source, "test", include_quality=True, formula_engine=failed)
        self.assertEqual(sum(b["block_type"] == "formula" for b in blocks), 0)
        self.assertEqual(quality["unparsed_figures"], 3)
        self.assertEqual(quality["parse_status"], "partial")
        self.assertTrue(all(not b["text"] for b in blocks if b["block_type"] == "figure"))

    def test_worker_timeout_is_a_parse_gap(self):
        with patch.object(Path, "is_file", return_value=True), \
             patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("worker", 90)):
            self.assertEqual(formula.recognize([Path("crop.png")])["error_code"], "FORMULA_TIMEOUT")

    def test_latex_percent_is_numeric_evidence_without_allowing_changed_number(self):
        quote = r"g=\frac{S_t-S_{t-1}}{S_{t-1}}\times100\%"
        draft = {"claims": [{"text": "同比增长率乘以100%。", "citations": [{"chunk_id": "f", "quote": quote}]}]}
        hits = [{"chunk_id": "f", "title": "公式", "exact_text": quote}]
        self.assertTrue(verify_claims(draft, hits))
        draft["claims"][0]["text"] = "同比增长率乘以101%。"
        with self.assertRaisesRegex(ValueError, "RAG_UNSUPPORTED_NUMBER"):
            verify_claims(draft, hits)
