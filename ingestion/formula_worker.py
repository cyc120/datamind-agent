"""Python 3.12 worker. Receives only crop paths, never questions, labels or documents."""
import hashlib
from importlib.metadata import version, distribution
import json
from pathlib import Path
import sys
import types


def main():
    # Load only the pinned formula adapter. RapidDoc's root imports unrelated Office/LLM backends.
    root = distribution("rapid-doc").locate_file("rapid_doc")
    for name, relative in (("rapid_doc", ""), ("rapid_doc.model", "model"), ("rapid_doc.model.formula", "model/formula")):
        module = types.ModuleType(name)
        module.__path__ = [str(root / relative)]
        sys.modules[name] = module
    from rapid_doc.model.formula.rapid_formula_self import RapidFormula, RapidFormulaInput, ModelType
    model_path = Path(__file__).resolve().parents[1] / ".cache/formula_models/pp_formulanet_plus_m.onnx"
    digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
    if digest != "71b6d389cf7b857e45252a4b98cfced1a3ffca7bf24d9497d02d052a41d9493b":
        raise ValueError("FORMULA_MODEL_HASH_MISMATCH")
    model = RapidFormula(RapidFormulaInput(model_type=ModelType.PP_FORMULANET_PLUS_M,
        model_dir_or_path=str(model_path), engine_cfg={"intra_op_num_threads": 2, "inter_op_num_threads": 1}))
    manifest = {"backend": "PP-FormulaNet_plus-M/ONNX", "package_version": version("rapid-doc"),
        "model_source": "Baidu PaddlePaddle; ONNX conversion by RapidAI",
        "upstream": "https://github.com/RapidAI/RapidDoc", "models": [{"name": model_path.name, "sha256": digest}]}
    results = []
    for filename in json.loads(sys.stdin.read())["images"]:
        data = Path(filename).read_bytes()
        output = model([data])[0]
        results.append({"latex": output.rec_formula, "elapsed_ms": round(output.elapse * 1000, 1),
                        "confidence": None, "crop_sha256": hashlib.sha256(data).hexdigest()})
    print("DATAMIND_FORMULA_RESULT:" + json.dumps({"results": results, "manifest": manifest}, ensure_ascii=False))


if __name__ == "__main__":
    main()
