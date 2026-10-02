"""Install the isolated formula adapter and verify its cached ONNX weights."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import httpx

ROOT = Path(__file__).resolve().parents[1]
MODEL_SHA256 = "71b6d389cf7b857e45252a4b98cfced1a3ffca7bf24d9497d02d052a41d9493b"
MODEL_URL = "https://www.modelscope.cn/models/RapidAI/RapidDoc/resolve/v1.0.0/formula/PP-FormulaNet_plus-M/pp_formulanet_plus_m.onnx"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default="python3.12", help="Python 3.10–3.12 executable")
    args = parser.parse_args()
    runtime = json.loads(subprocess.check_output([args.python, "-c", "import sys,json; print(json.dumps(list(sys.version_info[:2])))"]))
    if runtime[0] != 3 or not 10 <= runtime[1] <= 12:
        parser.error("formula adapter requires Python 3.10–3.12")
    env = ROOT / ".venv-formula"
    if not (env / "bin/python").exists():
        subprocess.run([args.python, "-m", "venv", str(env)], check=True)
    python = str(env / "bin/python")
    installed_runtime = json.loads(subprocess.check_output([python, "-c", "import sys,json; print(json.dumps(list(sys.version_info[:2])))"]))
    if installed_runtime[0] != 3 or not 10 <= installed_runtime[1] <= 12:
        parser.error("existing .venv-formula uses an incompatible Python; recreate it with Python 3.10–3.12")
    subprocess.run([python, "-m", "pip", "install", "-r", str(ROOT / "requirements-formula.txt")], check=True)
    # RapidDoc's other document/Office/LLM dependencies are intentionally outside this worker.
    subprocess.run([python, "-m", "pip", "install", "--no-deps", "rapid-doc==0.9.10"], check=True)
    model = ROOT / ".cache/formula_models/pp_formulanet_plus_m.onnx"
    model.parent.mkdir(parents=True, exist_ok=True)
    if not model.exists() or hashlib.sha256(model.read_bytes()).hexdigest() != MODEL_SHA256:
        partial = model.with_suffix(".part")
        with httpx.stream("GET", MODEL_URL, timeout=90, follow_redirects=True) as response:
            response.raise_for_status()
            with partial.open("wb") as output:
                for block in response.iter_bytes():
                    output.write(block)
        if hashlib.sha256(partial.read_bytes()).hexdigest() != MODEL_SHA256:
            partial.unlink()
            raise ValueError("FORMULA_MODEL_HASH_MISMATCH")
        partial.replace(model)
    print("Formula worker ready:", python)


if __name__ == "__main__":
    main()
