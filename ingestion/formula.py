"""Bounded isolated formula inference; no formula is filled from known answers."""
import json
import os
import re
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def valid_latex(text):
    if not isinstance(text, str) or not 3 <= len(text) <= 2000 or "=" not in text:
        return False
    depth = 0
    for char in text:
        if char == "{": depth += 1
        if char == "}": depth -= 1
        if depth < 0: return False
    supported = {"frac", "dfrac", "tfrac", "sum", "cdot", "times", "left", "right", "sqrt", "mathrm", "text", "bar", "overline", "cal"}
    commands = set(re.findall(r"\\([A-Za-z]+|\d+)", text))
    remaining = re.sub(r"\\(?:[A-Za-z]+|%)", "", text)
    return depth == 0 and commands <= supported and "\\" not in remaining and not any(c in text for c in "&$")


def recognize(images):
    python = Path(os.environ.get("DATAMIND_FORMULA_PYTHON", ROOT / ".venv-formula/bin/python"))
    if not python.is_file():
        return {"results": [], "error_code": "FORMULA_BACKEND_UNAVAILABLE"}
    if not (ROOT / ".cache/formula_models/pp_formulanet_plus_m.onnx").is_file():
        return {"results": [], "error_code": "FORMULA_MODEL_UNAVAILABLE"}
    try:
        process = subprocess.run([str(python), str(ROOT / "ingestion/formula_worker.py")],
            input=json.dumps({"images": [str(p) for p in images]}), text=True, capture_output=True, timeout=90)
        line = next((line for line in reversed(process.stdout.splitlines()) if line.startswith("DATAMIND_FORMULA_RESULT:")), None)
        if process.returncode or not line:
            return {"results": [], "error_code": "FORMULA_INFERENCE_FAILED"}
        result = json.loads(line.split(":", 1)[1])
        if len(result["results"]) != len(images):
            raise ValueError("count mismatch")
        return result
    except subprocess.TimeoutExpired:
        return {"results": [], "error_code": "FORMULA_TIMEOUT"}
    except (OSError, ValueError, KeyError):
        return {"results": [], "error_code": "FORMULA_INFERENCE_FAILED"}
