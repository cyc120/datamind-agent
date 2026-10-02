"""Author follow-on synthetic forms, then rasterize without reference text layers.

Default author step needs reportlab; --rasterize needs the application's PyMuPDF/OpenCV.
The evaluator passes only the resulting PDF to the frozen runtime parser.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
AUTHOR=ROOT/'data/evaluation/industrial_forms_authoring_v1.json'
SOURCE=ROOT/'output/pdf/industrial_forms_holdout_v1.pdf'
SUITE=ROOT/'data/evaluation/industrial_forms_holdout_v1.json'
CODE=['ingestion/industrial_facts.py','ingestion/ingest.py','ingestion/ocr.py','ingestion/tables.py']


def author():
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    data=json.loads(AUTHOR.read_text());SOURCE.parent.mkdir(parents=True,exist_ok=True)
    pdfmetrics.registerFont(TTFont('FormsChinese',os.environ.get('DATAMIND_PDF_FONT','/System/Library/Fonts/Supplemental/Songti.ttc')))
    c=canvas.Canvas(str(SOURCE),pagesize=(595,842),invariant=1);c.setTitle('DataMind synthetic follow-on industrial field forms')
    cases=[]
    for page,record in enumerate(data['records'],1):
        c.setFillColorRGB(.13,.23,.33);c.setFont('FormsChinese',18);c.drawString(48,790,'工业字段测试 / '+record['template'])
        c.setFont('FormsChinese',10);c.drawString(48,764,'自建合成材料 - 非生产文件 - 审批与结论只是模拟声明')
        size,step,start=(12,39,715) if page==1 else (10,30,706)
        x=52 if page==1 else 94;separator='：' if page==1 else ': '
        fields=[]
        for index,(label,value) in enumerate(record['fields']):
            y=start-index*step;text=label+separator+value;width=pdfmetrics.stringWidth(text,'FormsChinese',size)
            c.setFillColorRGB(.96,.97,.98);c.rect(x-8,y-9,510-x+52,step-4,fill=1,stroke=0)
            c.setFillColorRGB(.1,.17,.25);c.setFont('FormsChinese',size);c.drawString(x,y,text)
            fields.append({'label':label,'value':value,'bbox':[x-2,842-y-size-3,x+width+2,842-y+5]})
        c.setFillColorRGB(.35,.4,.46);c.setFont('FormsChinese',10);c.drawString(48,52,f'industrial-forms-holdout-v1 / native template {page}');c.showPage()
        cases.append({'case_id':f'form-native-{page}','page':page,'carrier':'native','template':record['template'],'fields':fields,
                      'expected_fact':{'entity_id':record['fields'][1][1],'operation_no':record['fields'][2][1],
                                       'measurement_id':record['fields'][7][1],'raw_value':record['fields'][11][1],
                                       'raw_unit':record['fields'][12][1],'normalized_value':'812.4' if page==1 else '800.00'}})
    c.save()
    SUITE.write_text(json.dumps({'suite_version':data['dataset_version'],'provenance':data['provenance'],'source_file':str(SOURCE.relative_to(ROOT)),
        'authoring_sha256':hashlib.sha256(AUTHOR.read_bytes()).hexdigest(),'frozen_parser_code_hashes':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in CODE},
        'cases':cases,'visual_qa':'pending'},ensure_ascii=False,indent=2)+'\n')
    print('native authoring complete; run --rasterize before evaluating')


def rasterize():
    import cv2
    import numpy as np
    import pymupdf
    suite=json.loads(SUITE.read_text());native=pymupdf.open(SOURCE);output=pymupdf.open();output.insert_pdf(native)
    assert len(native)==2
    for index,page in enumerate(native):
        pix=page.get_pixmap(dpi=144,alpha=False);image=cv2.imdecode(np.frombuffer(pix.tobytes('png'),np.uint8),cv2.IMREAD_COLOR)
        angle=0 if index==0 else 1.2;h,w=image.shape[:2];transform=cv2.getRotationMatrix2D((w/2,h/2),angle,1)
        image=cv2.warpAffine(image,transform,(w,h),borderValue=(255,255,255));image=cv2.GaussianBlur(image,(3,3),.3 if index==0 else .5)
        scan=output.new_page(width=595,height=842);scan.insert_image(scan.rect,stream=cv2.imencode('.png',image)[1].tobytes())
        case=json.loads(json.dumps(suite['cases'][index]));case.update(case_id=f'form-scan-{index+1}',page=index+3,carrier='scan',scan_dpi=144,scan_rotation_degrees=angle)
        for field in case['fields']:
            x0,y0,x1,y1=field['bbox'];points=np.array([[[x0*2,y0*2],[x1*2,y0*2],[x1*2,y1*2],[x0*2,y1*2]]],dtype=np.float32)
            points=cv2.transform(points,transform)[0]/2;field['bbox']=[round(float(points[:,0].min()),2),round(float(points[:,1].min()),2),round(float(points[:,0].max()),2),round(float(points[:,1].max()),2)]
        suite['cases'].append(case)
    temp=SOURCE.with_suffix('.tmp.pdf');output.save(temp,garbage=4,deflate=True);output.close();native.close();temp.replace(SOURCE)
    with pymupdf.open(SOURCE) as doc:
        assert len(doc)==4 and all(not doc[p].get_text().strip() for p in (2,3))
    suite.update(source_sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),builder_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),page_count=4)
    SUITE.write_text(json.dumps(suite,ensure_ascii=False,indent=2)+'\n');print(SOURCE,suite['source_sha256'])


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--rasterize',action='store_true');args=p.parse_args()
    rasterize() if args.rasterize else author()
