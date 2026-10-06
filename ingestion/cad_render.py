"""Offline handle-preserving SVG/HTML preview of the supported parsed profile."""
from html import escape
import json
import math

from ingestion.cad_contract import DrawingSnapshot


def render_svg(bundle):
    drawing = DrawingSnapshot.model_validate(bundle['snapshot'])
    b = drawing.views[0].bbox
    width, height = b.xmax-b.xmin, b.ymax-b.ymin
    stroke = max(width,height)/500
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{b.xmin} {-b.ymax} {width} {height}" role="img" aria-label="CAD entity preview">',
             '<style>.entity{cursor:pointer}.entity:hover,.entity:target,.entity.selected{stroke:#e67e22!important;stroke-width:'+str(stroke*2)+'}.entity:hover text,.entity.selected text{fill:#c05d00}</style>',
             f'<rect x="{b.xmin}" y="{-b.ymax}" width="{width}" height="{height}" fill="#fff"/>']
    for entity in drawing.entities:
        handle = entity.handle
        parts.append(f'<g id="entity-{handle}" class="entity" data-handle="{handle}" stroke="#253e55" stroke-width="{stroke}" fill="none">')
        raw = entity.text if entity.entity_type == 'TEXT' else entity.entity_type
        parts.append('<title>'+escape(f'{handle} · {entity.layer} · {raw}')+'</title>')
        if entity.entity_type == 'LINE':
            parts.append(f'<line x1="{entity.start.x}" y1="{-entity.start.y}" x2="{entity.end.x}" y2="{-entity.end.y}"/>')
        elif entity.entity_type == 'CIRCLE':
            parts.append(f'<circle cx="{entity.center.x}" cy="{-entity.center.y}" r="{entity.radius}"/>')
        else:
            cap = bundle['metadata']['render_entities'][handle]['text_height']
            if isinstance(cap,bool) or not isinstance(cap,(float,int)) or not math.isfinite(cap) or cap <= 0:
                raise ValueError('CAD_RENDER_TEXT_HEIGHT_INVALID')
            text = entity.text.replace('%%c','⌀')
            # Use measured extents, with an explicit approximate-font warning.
            parts.append(f'<text x="{entity.insertion.x}" y="{-entity.insertion.y}" font-family="sans-serif" font-size="{cap*1.38}" textLength="{entity.bbox.xmax-entity.bbox.xmin}" lengthAdjust="spacingAndGlyphs" stroke="none" fill="#253e55">{escape(text)}</text>')
        parts.append('</g>')
    parts.append('</svg>')
    return ''.join(parts)


def render_html(bundle):
    drawing = DrawingSnapshot.model_validate(bundle['snapshot'])
    svg = render_svg(bundle)
    payload = json.dumps(drawing.model_dump(),ensure_ascii=False).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    buttons = ''.join(f'<button data-handle="{e.handle}">{e.handle} · {escape(e.entity_type)} · {escape(e.layer)}</button>' for e in drawing.entities)
    title = escape(drawing.identity.drawing_id.value+' / '+drawing.identity.drawing_revision.value+' / '+drawing.identity.part_id.value)
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CAD 原生实体预览</title><style>body{font:15px system-ui;margin:0;background:#eef2f4;color:#213744}header{padding:20px 24px;background:white}h1{font-size:23px;margin:0 0 8px}p{margin:8px 0;overflow-wrap:anywhere}.layout{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:18px;padding:20px}.canvas{background:white;border:1px solid #ccd6dd;border-radius:8px;padding:15px}.canvas svg{width:100%;max-height:65vh;min-height:330px}aside{background:white;padding:16px;border-radius:8px}button{display:block;padding:8px;width:100%;text-align:left;border:1px solid #d2dce2;background:white;border-radius:5px;margin-bottom:5px;cursor:pointer}button:hover{background:#fef2dd}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;background:#f5f8fa;padding:10px;max-height:340px;overflow:auto}.hint{color:#647985;font-size:13px}@media(max-width:800px){.layout{grid-template-columns:1fr}}</style>
<header><h1>'''+title+'''</h1><p>原生 DXF 实体与来源定位 · 单位 '''+escape(drawing.source.unit)+'''</p><p class="hint">点击实体或右侧句柄查看原文与引用。文字轮廓采用近似字体；此预览用于核对位置，不作为制造尺寸测量工具。</p></header>
<main class="layout"><section class="canvas">'''+svg+'''<p class="hint">SHA-256: '''+drawing.source.source_sha256+'''</p></section><aside><strong>实体句柄</strong><p class="hint">尺寸关联仅采用源文件的显式引用。</p>'''+buttons+'''<pre id="detail">请选择实体。</pre></aside></main>
<script type="application/json" id="drawing-data">'''+payload+'''</script><script>
const drawing=JSON.parse(document.getElementById('drawing-data').textContent);
function select(handle){document.querySelectorAll('.entity').forEach(e=>e.classList.remove('selected'));const entity=drawing.entities.find(e=>e.handle===handle);const features=drawing.features.filter(f=>f.locator.handle===handle||f.target_candidates.includes(handle));let handles=new Set([handle]);features.forEach(f=>{handles.add(f.locator.handle);if(f.selected_target)handles.add(f.selected_target)});handles.forEach(h=>document.getElementById('entity-'+h)?.classList.add('selected'));document.getElementById('detail').textContent=JSON.stringify({source_sha256:drawing.source.source_sha256,entity,features},null,2);}
document.querySelectorAll('[data-handle]').forEach(e=>e.addEventListener('click',()=>select(e.dataset.handle)));
</script></html>'''
