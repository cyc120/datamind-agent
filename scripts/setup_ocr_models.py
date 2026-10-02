"""Download the pinned header recognizer. Source pixels are never replaced."""
from pathlib import Path
import hashlib
import sys
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ingestion.tables import MODEL_PATH, MODEL_SHA


def main():
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    if MODEL_PATH.is_file() and hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest() == MODEL_SHA:
        print("Header model ready:", MODEL_PATH.name)
        return
    url = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv5/rec/" + MODEL_PATH.name
    partial = MODEL_PATH.with_suffix(".part")
    with httpx.stream("GET", url, timeout=120, follow_redirects=True) as response:
        response.raise_for_status()
        with partial.open("wb") as output:
            for data in response.iter_bytes():
                output.write(data)
    if hashlib.sha256(partial.read_bytes()).hexdigest() != MODEL_SHA:
        partial.unlink()
        raise ValueError("TABLE_HEADER_MODEL_HASH_MISMATCH")
    partial.replace(MODEL_PATH)
    print("Header model ready:", MODEL_PATH.name)


if __name__ == "__main__":
    main()
