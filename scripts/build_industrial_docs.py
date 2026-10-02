"""Build one reproducible, clearly synthetic native PDF dossier from fixture text.

Authoring dependency: reportlab (use the bundled PDF Python or install it).
The ingestion/runtime does not read this builder's fixture.
"""
import hashlib
import json
import os
from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / 'data/industrial_demo'


def main():
    fixture = json.loads((DIRECTORY / 'seed_v1.json').read_text())
    font = os.environ.get('DATAMIND_PDF_FONT','/System/Library/Fonts/Supplemental/Songti.ttc')
    pdfmetrics.registerFont(TTFont('DemoChinese',font))  # Embed glyphs; no viewer language pack dependency.
    path = DIRECTORY / 'industrial_demo_v1.pdf'
    c = canvas.Canvas(str(path), pagesize=(595,842), invariant=1)
    c.setTitle('DataMind 工业合成演示 - 工艺与质检原文')
    page_no = 0
    def page(title, fields):
        nonlocal page_no
        page_no += 1
        c.setFillColorRGB(.08,.23,.2); c.rect(0,755,595,87,fill=1,stroke=0)
        c.setFillColorRGB(1,1,1); c.setFont('DemoChinese',19); c.drawString(42,797,title)
        c.setFont('DemoChinese',10); c.drawString(42,774,'自建合成演示 | 非工厂数据 | 审批与结论均为模拟原文声明')
        y = 706
        for label,value in fields:
            c.setFillColorRGB(.94,.97,.96); c.rect(40,y-9,515,29,fill=1,stroke=0)
            c.setFillColorRGB(.09,.18,.16); c.setFont('DemoChinese',12)
            c.drawString(50,y,label+'：'+str(value)); y-=37
        c.setFillColorRGB(.35,.42,.4); c.setFont('DemoChinese',10)
        c.drawString(42,92,'核验说明：规范值、实测值和原文结论分别保存，不以任何单一来源默认裁定。')
        c.drawString(42,68,f'industrial-demo-v1 | 原文页 {page_no}'); c.showPage()
    approval={'approved':'已批准','unapproved':'未批准'}
    for r in fixture['revisions']:
        page('工艺要求 / '+r['revision_id'], [('文档类型','工艺要求'),('零件编号',r['part_id']),('工序编号',r['operation_no']),
             ('指标',r['metric']),('版次',r['revision_id']),('审批状态',approval[r['approval_status']]),
             ('生效时间',r['valid_from']),('失效时间',r['valid_to'] or '无'),('温度下限',r['lower']),('温度上限',r['upper']),
             ('单位',r['unit']),('测量方法',r['method'])])
    for r in fixture['reports']:
        page('质检记录 / '+r['report_id'], [('文档类型','质检报告'),('零件编号',r['part_id']),('工序编号',r['operation_no']),
             ('指标',r['metric']),('版次',r['revision']),('审批状态',approval[r['approval_status']]),('批次编号',r['batch_id']),
             ('测量编号',r['measurement_id']),('报告编号',r['report_id']),('测量时点',r['measured_at']),('测量方法',r['method']),
             ('实测值',r['value']),('单位',r['unit']),('报告结论',r['conclusion'])])
    c.save()
    manifest={'dataset_version':fixture['dataset_version'],'provenance':fixture['provenance'],
              'file':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'page_count':page_no,
              'seed_sha256':hashlib.sha256((DIRECTORY/'seed_v1.json').read_bytes()).hexdigest(),
              'builder_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (DIRECTORY/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    print(path,page_no,'pages',manifest['sha256'])


if __name__=='__main__':
    main()
