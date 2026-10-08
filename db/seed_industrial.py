"""Idempotently load synthetic industrial SQL rows and parse the actual PDF dossier."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import psycopg2
import pymupdf
from db.migrate import migrate
from ingestion import ingest, facts, industrial_facts

DIRECTORY=ROOT/'data/industrial_demo'
DSN='host=127.0.0.1 port=5432 dbname=datamind_meta user=datamind_app password=datamind_app_dev'


def locator_plan(blocks,doc_id,quality):
    """F02: rows to write into `document_block_locators` + the **honest** count.

    选择"真的写入 locator"（而不是删掉计数）：`ingest.parse_pdf` 已经为每个块做过契约
    校验，并把校验过的 locator 原样挂在 `contract_locator` 上；下面重写块 id 只改
    `document_blocks.block_id`，而 pdf_region locator 载荷里**没有** block_id 字段，
    所以同一个已校验对象可以按新 id 落库，两边 id 保持一致。
    以前这条路径只写 `document_blocks` 却沿用了 parse_pdf 的
    `locators_written=296`，于是 quality_detail 声称 296 条而表里 0 行——这里改成只报
    实际要写出的行数，缺定位的块显式记录为 gap，不再谎报。
    """
    rows=[]
    for block in blocks:
        row=ingest._locator_row(block,doc_id)
        if row is not None:rows.append(row)
    contract=quality.get('parse_contract')
    if not isinstance(contract,dict):
        contract={};quality['parse_contract']=contract
    contract['locators_written']=len(rows)
    contract['locators_unavailable']=len(blocks)-len(rows)
    if len(rows)<len(blocks):contract['locator_gap']='locator_unavailable'
    else:contract.pop('locator_gap',None)
    return rows


def write_locators(cur,doc_id,rows):
    """F02: 覆盖式写入该文档的定位行，返回实际执行的行数（供与 quality_detail 对账）。"""
    cur.execute('DELETE FROM document_block_locators WHERE document_id=%s',(doc_id,))
    for row in rows:cur.execute(ingest.LOCATOR_INSERT_SQL,row)
    return len(rows)


def main():
    migrate()
    seed_path=DIRECTORY/'seed_v1.json';manifest=json.loads((DIRECTORY/'manifest.json').read_text())
    seed=json.loads(seed_path.read_text());source=DIRECTORY/manifest['file']
    digest=hashlib.sha256(source.read_bytes()).hexdigest();seed_sha=hashlib.sha256(seed_path.read_bytes()).hexdigest()
    if digest!=manifest['sha256'] or seed_sha!=manifest['seed_sha256']:
        raise ValueError('INDUSTRIAL_FROZEN_SOURCE_CHANGED')
    doc_id='doc-industrial-'+digest[:12]
    blocks,count,quality=ingest.parse_pdf(source,doc_id,include_quality=True)
    for b in blocks: b['block_id']='iblk-'+hashlib.sha256(f'{doc_id}:{b["reading_order"]}'.encode()).hexdigest()[:24]
    with pymupdf.open(source) as pdf:
        sizes={i+1:[float(p.rect.width),float(p.rect.height)] for i,p in enumerate(pdf)}
    extracted,field_quality=industrial_facts.extract(blocks,document_id=doc_id,source_sha256=digest,page_sizes=sizes)
    if field_quality['errors'] or len(extracted)!=manifest['page_count']:
        raise ValueError('INDUSTRIAL_DOCUMENT_FIELDS_INCOMPLETE')
    quality.update(parser_version=industrial_facts.PARSER_VERSION,provenance=seed['provenance'],
                   index_mode='typed_facts_only',fact_extraction=field_quality)
    locators=locator_plan(blocks,doc_id,quality)
    with psycopg2.connect(DSN) as conn,conn.cursor() as cur:
        for p in seed['parts']:
            cur.execute('INSERT INTO demo_industrial.parts VALUES (%s,%s,%s) ON CONFLICT(part_id) DO UPDATE SET name=EXCLUDED.name,material=EXCLUDED.material',tuple(p[k] for k in ('part_id','name','material')))
        for r in seed['revisions']:
            cur.execute('INSERT INTO demo_industrial.process_revisions VALUES (%s,%s,%s,%s,%s) ON CONFLICT(revision_id) DO UPDATE SET approval_status=EXCLUDED.approval_status,valid_from=EXCLUDED.valid_from,valid_to=EXCLUDED.valid_to',tuple(r[k] for k in ('revision_id','part_id','approval_status','valid_from','valid_to')))
            cur.execute("INSERT INTO demo_industrial.operations VALUES (%s,%s,'保温温度核验（合成）') ON CONFLICT DO NOTHING",(r['revision_id'],r['operation_no']))
        for b in seed['batches']:
            cur.execute('INSERT INTO demo_industrial.production_batches VALUES (%s,%s,%s,%s) ON CONFLICT(batch_id) DO UPDATE SET part_id=EXCLUDED.part_id,produced_at=EXCLUDED.produced_at,recorded_revision=EXCLUDED.recorded_revision',tuple(b[k] for k in ('batch_id','part_id','produced_at','recorded_revision')))
        for m in seed['measurements']:
            cur.execute('INSERT INTO demo_industrial.process_measurements VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(measurement_id) DO UPDATE SET value=EXCLUDED.value,unit=EXCLUDED.unit,measured_at=EXCLUDED.measured_at,method=EXCLUDED.method',tuple(m[k] for k in ('measurement_id','batch_id','operation_no','metric','value','unit','measured_at','method')))
        for q in seed['quality_records']:
            cur.execute('INSERT INTO demo_industrial.quality_records VALUES (%s,%s,%s,%s) ON CONFLICT(quality_id) DO UPDATE SET report_id=EXCLUDED.report_id,method=EXCLUDED.method',tuple(q[k] for k in ('quality_id','measurement_id','report_id','method')))
        cur.execute('INSERT INTO demo_industrial.dataset_manifest(dataset_version,seed_sha256,pdf_sha256,provenance) VALUES (%s,%s,%s,%s) ON CONFLICT(dataset_version) DO UPDATE SET seed_sha256=EXCLUDED.seed_sha256,pdf_sha256=EXCLUDED.pdf_sha256', (seed['dataset_version'],seed_sha,digest,seed['provenance']))
        cur.execute("""INSERT INTO document_assets(document_id,title,collection,file_type,origin,sha256,object_uri,page_count,
                       parser_version,status,quality_detail) VALUES (%s,'工业合成演示：工艺与质检原文','industrial_demo','pdf','curated',%s,%s,%s,%s,%s,%s)
                       ON CONFLICT(document_id) DO UPDATE SET status=EXCLUDED.status,parser_version=EXCLUDED.parser_version,quality_detail=EXCLUDED.quality_detail""",
                    (doc_id,digest,str(source),count,industrial_facts.PARSER_VERSION,ingest.SEARCHABLE_STATUS,json.dumps(quality,ensure_ascii=False)))
        cur.execute('DELETE FROM document_blocks WHERE document_id=%s',(doc_id,))
        for b in blocks:
            cur.execute('INSERT INTO document_blocks(block_id,document_id,page_no,bbox,block_type,text,reading_order,parser_version,content_hash) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                        (b['block_id'],doc_id,b['page_no'],json.dumps(b['bbox']),b['block_type'],b['text'],b['reading_order'],industrial_facts.PARSER_VERSION,hashlib.sha256(b['text'].encode()).hexdigest()))
        write_locators(cur,doc_id,locators)
        facts.persist(cur,doc_id,extracted)
    print(f'industrial-demo-v1: 3 parts, {len(seed["batches"])} batches, {len(seed["measurements"])} measurements; {len(extracted)} native PDF facts; {len(locators)} locators; {doc_id}')


if __name__=='__main__':main()
