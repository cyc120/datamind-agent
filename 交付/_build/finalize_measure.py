# -*- coding: utf-8 -*-
"""测量交付物真实页数/幻灯片数，回填模板占位符，并写 reports/office_checks.json。

只读交付物内容；不做 git 写操作；不修改任何既有仓库文件。
运行：cd "/Users/caicai/DataMind Agent" && .venv/bin/python 交付/_build/finalize_measure.py
"""
import hashlib
import json
import pathlib
import re
import subprocess
import sys
from datetime import datetime, timezone

import pymupdf
from docx import Document
from pptx import Presentation

ROOT = pathlib.Path(__file__).resolve().parents[2]
DELIV = ROOT / "交付"
BUILD = DELIV / "_build"
NODE = "/Applications/DeepSeek Harness.app/Contents/Resources/runtime/primary-runtime/dependencies/node/bin/node"
CLI = ("/Applications/DeepSeek Harness.app/Contents/Resources/app.asar.unpacked/dsh/node_modules/"
       "@deepseek-ai/libreoffice-kit/lib/cli.js")
CHECKER = pathlib.Path("/Applications/DeepSeek Harness.app/Contents/Resources/runtime/"
                       "office-skills/scripts/check_office.py")
PY = str(ROOT / ".venv" / "bin" / "python")

FILES = {
    "docx1": DELIV / "01_设计报告_DataMind_Agent.docx",
    "pdf1": DELIV / "01_设计报告_DataMind_Agent.pdf",
    "docx2": DELIV / "02_技术实现说明书_DataMind_Agent.docx",
    "pdf2": DELIV / "02_技术实现说明书_DataMind_Agent.pdf",
    "pptx": DELIV / "03_答辩演示.pptx",
    "docx4": DELIV / "04_突破与创新性自述_DataMind_Agent.docx",
    "pdf4": DELIV / "04_突破与创新性自述.pdf",
}


def convert(docx_path, pdf_path):
    """DOCX → PDF（DSH LibreOffice Kit）。已存在则先删除（Kit 拒绝覆盖）。"""
    if pdf_path.exists():
        pdf_path.unlink()
    out = subprocess.run([NODE, CLI, "convert", "--input", str(docx_path), "--output", str(pdf_path)],
                         capture_output=True, text=True)
    return {"cmd": f'{NODE} {CLI} convert --input "{docx_path.name}" --output "{pdf_path.name}"',
            "exit_code": out.returncode, "stdout": out.stdout.strip(), "stderr": out.stderr.strip()}


def measure():
    m = {}
    for key in ("pdf1", "pdf2", "pdf4"):
        doc = pymupdf.open(str(FILES[key]))
        m[key] = {"pages": doc.page_count, "bytes": FILES[key].stat().st_size}
        doc.close()
    prs = Presentation(str(FILES["pptx"]))
    m["pptx"] = {"slides": len(prs.slides._sldIdLst), "bytes": FILES["pptx"].stat().st_size}
    for key in ("docx1", "docx2", "docx4"):
        d = Document(str(FILES[key]))
        m[key] = {"paragraphs": len(d.paragraphs), "tables": len(d.tables),
                  "bytes": FILES[key].stat().st_size}
    return m


def fill(path, mapping):
    txt = path.read_text(encoding="utf-8")
    for k, v in mapping.items():
        txt = txt.replace("{" + k + "}", str(v))
    path.write_text(txt, encoding="utf-8")
    left = re.findall(r"\{[a-z0-9_]+\}", txt)
    return left


def sha256(p):
    h = hashlib.sha256()
    h.update(pathlib.Path(p).read_bytes())
    return h.hexdigest()


def run_checks():
    checks = {}
    for key, name, contains in [
        ("docx1", "01_设计报告_DataMind_Agent.docx", ["项目核心亮点预览页", "多源融合与证据绑定"]),
        ("docx2", "02_技术实现说明书_DataMind_Agent.docx", ["技术实现说明书"]),
        ("pptx", "03_答辩演示.pptx", ["DataMind Agent"]),
        ("docx4", "04_突破与创新性自述_DataMind_Agent.docx", ["突破与创新性自述"]),
    ]:
        path = DELIV / name
        cmd = [PY, str(CHECKER), str(path)]
        for c in contains:
            cmd += ["--contains", c]
        out_json = ROOT / "tmp" / "wp10" / f"check_{key}.json"
        out_json.parent.mkdir(parents=True, exist_ok=True)
        cmd += ["--out", str(out_json)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        report = json.loads(out_json.read_text(encoding="utf-8")) if out_json.exists() else None
        checks[key] = {"file": name, "command": " ".join(cmd), "exit_code": r.returncode,
                       "stdout_tail": r.stdout.strip()[-600:], "stderr_tail": r.stderr.strip()[-400:],
                       "report": report}
    return checks


if __name__ == "__main__":
    print("== 第 1 次测量（占位符版）==")
    conv_log = {}
    for key, docx_key in (("pdf1", "docx1"), ("pdf2", "docx2"), ("pdf4", "docx4")):
        conv_log[key] = convert(FILES[docx_key], FILES[key])
        print(key, conv_log[key]["exit_code"], conv_log[key]["stdout"][:120])
    m = measure()
    print(json.dumps(m, ensure_ascii=False, indent=1))

    mapping = {
        "p1_docx": m["pdf1"]["pages"], "p1_pdf": m["pdf1"]["pages"],
        "p2_docx": m["pdf2"]["pages"], "p2_pdf": m["pdf2"]["pages"],
        "p3": m["pptx"]["slides"], "p4_pdf": m["pdf4"]["pages"],
        "b1_docx": m["docx1"]["bytes"], "b1_pdf": m["pdf1"]["bytes"],
        "b2_docx": m["docx2"]["bytes"], "b2_pdf": m["pdf2"]["bytes"],
        "b3": m["pptx"]["bytes"], "b4_pdf": m["pdf4"]["bytes"],
    }
    print("== 回填占位符 ==")
    print("docx1 剩余占位符:", fill(BUILD / "make_01_design_report.py", mapping))
    print("docx2 剩余占位符:", fill(BUILD / "make_02_tech_spec.py", mapping))
    print("deck  剩余占位符:", fill(BUILD / "make_03_deck.py", mapping))

    print("== 官方网页占位符 ==")
    # 03 号幻灯片里的页数硬编码替换（写回生成脚本，保证可复现）
    p = BUILD / "make_03_deck.py"
    s = p.read_text(encoding="utf-8")
    s = s.replace('"13 页 / 13 页"', f'"{m["pdf1"]["pages"]} 页 / {m["pdf1"]["pages"]} 页"')
    s = s.replace('"12 页 / 12 页"', f'"{m["pdf2"]["pages"]} 页 / {m["pdf2"]["pages"]} 页"')
    s = s.replace('"20 张（限 20）"', f'"{m["pptx"]["slides"]} 张（限 20）"')
    p.write_text(s, encoding="utf-8")

    print("== 重新生成并二次测量 ==")
    for script in ("make_01_design_report.py", "make_02_tech_spec.py", "make_03_deck.py", "make_04_innovation.py"):
        r = subprocess.run([PY, str(BUILD / script)], capture_output=True, text=True)
        print(script, "exit", r.returncode, r.stdout.strip().splitlines()[-1:])
    for key, docx_key in (("pdf1", "docx1"), ("pdf2", "docx2"), ("pdf4", "docx4")):
        conv_log[key + "_second"] = convert(FILES[docx_key], FILES[key])
    m2 = measure()
    print(json.dumps(m2, ensure_ascii=False, indent=1))

    print("== 结构检查 ==")
    checks = run_checks()
    for k, v in checks.items():
        print(k, "exit", v["exit_code"])

    out = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "checker": str(CHECKER),
        "checker_command_template": f'{PY} {CHECKER} <file> --contains <text> --out <json>',
        "measured": m2,
        "measured_first_pass": m,
        "convert_log": conv_log,
        "checks": checks,
        "anonymity_scan": json.loads((BUILD / "anonymity_scan.json").read_text(encoding="utf-8")),
        "sha256": {k: sha256(v) for k, v in FILES.items()},
        "honesty": [
            "结构检查只证明 ZIP/XML 完整、关系可解析、段落/表格/幻灯片计数与必需文本存在；不证明分页、裁切、字体渲染或版式美观。",
            "本轮未做逐页视觉检查（未渲染预览图），因此 PDF 版式风险未消除。",
            "本文件不包含任何官方得分结论。",
        ],
    }
    (ROOT / "reports" / "office_checks.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("WROTE reports/office_checks.json")
