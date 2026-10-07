"""JSON entry point for bounded, process-isolated OCR of one PDF page.

The parent passes a document path and page number; this process renders and recognises
the page on its own interpreter so the native OCR session is terminated with its whole
process group when the shared deadline expires. It emits exactly one result line.
"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pymupdf

from ingestion import ocr

MARKER = "DATAMIND_OCR_RESULT:"


def main():
    request = json.loads(sys.stdin.read())
    page_no = request["page_no"]
    if not isinstance(page_no, int) or isinstance(page_no, bool) or page_no < 1:
        raise ValueError("OCR_PAGE_NUMBER_INVALID")
    with pymupdf.open(request["pdf_path"]) as document:
        if page_no > document.page_count:
            raise ValueError("OCR_PAGE_NUMBER_INVALID")
        blocks, detail = ocr.page_blocks(document[page_no - 1], page_no, ocr=ocr.engine())
    sys.stdout.write(MARKER + json.dumps({"blocks": blocks, "detail": detail}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
