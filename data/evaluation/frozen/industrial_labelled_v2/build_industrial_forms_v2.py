"""Author separate development/acceptance forms; scan pages contain pixels only.

Use bundled Python for authoring and application Python for --rasterize.
Acceptance materials are authored only after development and code freeze.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE = ['ingestion/industrial_facts.py', 'ingestion/ingest.py', 'ingestion/ocr.py',
        'ingestion/tables.py', 'datamind/field_store.py']


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def records(stage):
    # Deliberately disjoint entity/batch/measurement IDs and source values.
    base = 320 if stage == 'development' else 460
    specs = [('质检报告', 'degC', '824.6', '824.6'), ('工艺要求', 'K', '1068.15', '795.00')]
    if stage == 'acceptance':
        specs += [('质检报告', 'K', '1090.35', '817.20'), ('工艺要求', 'degC', '775.5', '775.5')]
    result = []
    for index, (kind, unit, raw, normalized) in enumerate(specs):
        entity = f'P{base+index}'
        common = [('文档类型', kind), ('零件编号', entity), ('工序编号', str(15+index*10)),
                  ('指标', 'holding_temperature'), ('版次', f'{entity}-V{index+2}'),
                  ('审批状态', '已批准' if index % 2 == 0 else '未批准'), ('单位', unit),
                  ('测量方法', 'core' if index % 2 == 0 else 'surface')]
        expected = dict(entity_id=entity, operation_no=str(15+index*10), raw_unit=unit,
                        source_revision=f'{entity}-V{index+2}',
                        approval_status='approved' if index % 2 == 0 else 'unapproved',
                        normalized_unit='degC', fact_kind='measurement' if kind == '质检报告' else 'requirement')
        if kind == '质检报告':
            extra = [('批次编号', f'LOT-{base+index}'), ('测量编号', f'MEAS-{base+index}-7'),
                     ('报告编号', f'REP-{base+index}-7'), ('测量时点', f'2026-11-0{index+1}T10:35:20+08:00'),
                     ('实测值', raw), ('报告结论', '合成声明，须以原文和记录核验')]
            expected.update(batch_id=extra[0][1], measurement_id=extra[1][1], report_id=extra[2][1],
                            measured_at=extra[3][1], raw_value=raw, normalized_value=normalized)
        else:
            high = '1118.15' if unit == 'K' else '825.5'
            extra = [('生效时间', '2026-10-01T00:00:00+08:00'), ('失效时间', '无'),
                     ('温度下限', raw), ('温度上限', high)]
            expected.update(valid_from=extra[0][1], valid_to=None, raw_value=raw+' .. '+high,
                            normalized_range=dict(min=normalized, max='845.00' if unit == 'K' else '825.5',
                                                  min_inclusive=True, max_inclusive=True))
        fields = common + extra
        if stage == 'acceptance':
            fields = fields[0:1] + list(reversed(fields[1:]))  # Field order is not semantic.
        result.append(dict(template=f'{stage}-compact-{index+1}', fields=fields, expected_fact=expected,
                           separator=':' if index % 2 == 0 else '：', spaced_label=index % 2 == 1))
    return result


def author(stage, source, suite_path):
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    if source.exists() or suite_path.exists():
        raise SystemExit('Frozen material exists; choose a new version instead of overwriting')
    source.parent.mkdir(parents=True, exist_ok=True)
    pdfmetrics.registerFont(TTFont('FormsChinese', os.environ.get(
        'DATAMIND_PDF_FONT', '/System/Library/Fonts/Supplemental/Songti.ttc')))
    c = canvas.Canvas(str(source), pagesize=(595, 842), invariant=1)
    c.setTitle('DataMind compact industrial forms / '+stage)
    cases = []
    for index, record in enumerate(records(stage)):
        x, size, step = (62+index*9, 11 if index % 2 == 0 else 10.5, 34)
        c.setFillColorRGB(.1, .2, .3); c.setFont('FormsChinese', 18)
        c.drawString(48, 790, '工业字段 / '+('开发材料' if stage == 'development' else '新样本验收'))
        c.setFont('FormsChinese', 10)
        c.drawString(48, 762, '自建合成材料 - 非生产文件 - 审批仅为原文模拟声明')
        fields = []
        for row, (label, value) in enumerate(record['fields']):
            y = 718-row*step
            displayed = ' '.join(label) if record['spaced_label'] else label
            text = displayed+record['separator']+(' ' if index % 2 else '')+value
            width = pdfmetrics.stringWidth(text, 'FormsChinese', size)
            c.setFillColorRGB(.96, .97, .98); c.rect(48, y-8, 499, 29, fill=1, stroke=0)
            c.setFillColorRGB(.1, .17, .25); c.setFont('FormsChinese', size); c.drawString(x, y, text)
            fields.append(dict(label=label, value=value, bbox=[x-2, 842-y-size-3, x+width+2, 842-y+5]))
        c.setFillColorRGB(.35, .4, .46); c.setFont('FormsChinese', 9)
        c.drawString(48, 48, f'industrial-forms-v2 / {stage} / native {index+1}')
        c.showPage()
        cases.append(dict(case_id=f'{stage}-native-{index+1}', page=index+1, carrier='native',
                          template=record['template'], fields=fields, expected_fact=record['expected_fact']))
    c.save()
    suite = dict(suite_version=f'industrial-forms-{stage}-v2', stage=stage,
        provenance='Synthetic forms authored by the implementer; no independent factory data or blind benchmark.',
        development_participation=stage == 'development',
        source_file=str(source.relative_to(ROOT)), cases=cases, visual_qa='pending',
        frozen_parser_code_hashes={name:digest(ROOT/name) for name in CODE},
        frozen_evaluator_sha256=digest(ROOT/'scripts/eval_industrial_forms.py'),
        builder_sha256=digest(Path(__file__)))
    suite_path.write_text(json.dumps(suite, ensure_ascii=False, indent=2)+'\n')


def rasterize(source, suite_path):
    import cv2
    import numpy as np
    import pymupdf
    suite = json.loads(suite_path.read_text())
    if 'source_sha256' in suite:
        raise SystemExit('Rasterization already frozen')
    native = pymupdf.open(source); output = pymupdf.open(); output.insert_pdf(native)
    count = len(native)
    for index, page in enumerate(native):
        pix = page.get_pixmap(dpi=144, alpha=False)
        image = cv2.imdecode(np.frombuffer(pix.tobytes('png'), np.uint8), cv2.IMREAD_COLOR)
        h, w = image.shape[:2]; angle = (0, 1.0, -.8, .6)[index % 4]
        transform = cv2.getRotationMatrix2D((w/2, h/2), angle, 1)
        image = cv2.warpAffine(image, transform, (w, h), borderValue=(255, 255, 255))
        image = cv2.GaussianBlur(image, (3, 3), .4)
        scan = output.new_page(width=595, height=842)
        scan.insert_image(scan.rect, stream=cv2.imencode('.png', image)[1].tobytes())
        case = json.loads(json.dumps(suite['cases'][index]))
        case.update(case_id=f"{suite['stage']}-scan-{index+1}", page=count+index+1,
                    carrier='scan', scan_dpi=144, scan_rotation_degrees=angle)
        for field in case['fields']:
            x0, y0, x1, y1 = field['bbox']
            points = np.array([[[x0*2,y0*2],[x1*2,y0*2],[x1*2,y1*2],[x0*2,y1*2]]], dtype=np.float32)
            points = cv2.transform(points, transform)[0]/2
            field['bbox'] = [round(float(points[:,0].min()),2), round(float(points[:,1].min()),2),
                             round(float(points[:,0].max()),2), round(float(points[:,1].max()),2)]
        suite['cases'].append(case)
    temporary = source.with_suffix('.tmp.pdf'); output.save(temporary, garbage=4, deflate=True)
    output.close(); native.close(); temporary.replace(source)
    with pymupdf.open(source) as pdf:
        assert len(pdf) == count*2 and all(not pdf[p].get_text().strip() for p in range(count, count*2))
    suite.update(source_sha256=digest(source), page_count=count*2)
    suite_path.write_text(json.dumps(suite, ensure_ascii=False, indent=2)+'\n')
    print(source, suite['source_sha256'])


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--stage', choices=['development', 'acceptance'], required=True)
    ap.add_argument('--rasterize', action='store_true'); args = ap.parse_args()
    source = ROOT/f'output/pdf/industrial_forms_{args.stage}_v2.pdf'
    suite_path = ROOT/f'data/evaluation/industrial_forms_{args.stage}_v2.json'
    rasterize(source, suite_path) if args.rasterize else author(args.stage, source, suite_path)
