"""Bounded isolated formula inference; no formula is filled from known answers."""
import json
import os
from pathlib import Path
import re

from fastapi import HTTPException

from datamind import task_budget

ROOT = Path(__file__).resolve().parents[1]
FORMULA_DEADLINE_SECONDS = 90.0
FORMULA_SLOTS = 1
FORMULA_SLOT_NAME = 'formula-inference'


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


def _run(python, payload, deadline_s):
    # The isolated interpreter runs in its own process group so a stuck ONNX session is
    # terminated with its descendants instead of only the direct child.
    return task_budget.run_bounded_command(
        [str(python), str(ROOT / "ingestion/formula_worker.py")], payload,
        deadline_s=deadline_s or FORMULA_DEADLINE_SECONDS,
        fallback_error_code='FORMULA_INFERENCE_FAILED')


def recognize(images, *, dsn=None, deadline_s=None, slots=None):
    python = Path(os.environ.get("DATAMIND_FORMULA_PYTHON", ROOT / ".venv-formula/bin/python"))
    if not python.is_file():
        return {"results": [], "error_code": "FORMULA_BACKEND_UNAVAILABLE"}
    if not (ROOT / ".cache/formula_models/pp_formulanet_plus_m.onnx").is_file():
        return {"results": [], "error_code": "FORMULA_MODEL_UNAVAILABLE"}
    payload = json.dumps({"images": [str(p) for p in images]})
    try:
        if dsn:
            # Bound simultaneous formula sessions across worker processes; no slot is an
            # immediate, explicit failure rather than an unbounded queue.
            with task_budget.resource_slot(dsn, FORMULA_SLOT_NAME,
                                           slots or task_budget.configured_slots(FORMULA_SLOTS)):
                outcome = _run(python, payload, deadline_s)
        else:
            outcome = _run(python, payload, deadline_s)
    except HTTPException as error:
        code = error.detail.get('error_code') if isinstance(error.detail, dict) else None
        return {"results": [], "error_code": code or "FORMULA_INFERENCE_FAILED"}
    if outcome['status'] == 'timeout':
        return {"results": [], "error_code": "FORMULA_TIMEOUT"}
    if outcome['status'] == 'cancelled':
        return {"results": [], "error_code": "FORMULA_CANCELLED"}
    if outcome['status'] != 'ok':
        return {"results": [], "error_code": "FORMULA_INFERENCE_FAILED"}
    line = next((line for line in reversed(outcome['stdout'].splitlines())
                 if line.startswith("DATAMIND_FORMULA_RESULT:")), None)
    if not line:
        return {"results": [], "error_code": "FORMULA_INFERENCE_FAILED"}
    try:
        result = json.loads(line.split(":", 1)[1])
        if len(result["results"]) != len(images):
            raise ValueError("count mismatch")
        return result
    except (OSError, ValueError, KeyError, TypeError):
        return {"results": [], "error_code": "FORMULA_INFERENCE_FAILED"}
