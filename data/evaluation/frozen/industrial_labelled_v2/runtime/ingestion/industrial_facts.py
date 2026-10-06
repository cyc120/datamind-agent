"""One explicit labelled form per page, from native text or accepted OCR blocks.

Only label whitespace and the fullwidth/ASCII separator are normalized. Values
and their original block locators are retained; missing context is never filled.
"""
import hashlib
import math
import re
from datamind.industrial_rules import temperature, timestamp

PARSER_VERSION = 'industrial-labelled-v2'
COMMON = ['文档类型','零件编号','工序编号','指标','版次','审批状态','单位','测量方法']
REQUIREMENT = ['生效时间','失效时间','温度下限','温度上限']
MEASUREMENT = ['批次编号','测量编号','报告编号','测量时点','实测值','报告结论']
LABELS = set(COMMON + REQUIREMENT + MEASUREMENT)
LABEL_PATTERN = '|'.join('\\s*'.join(re.escape(c) for c in label) for label in sorted(LABELS))
EMBEDDED_FIELD = re.compile(r'(?:'+LABEL_PATTERN+r')\s*[：:]')


def labelled_value(text):
    """Split at the first separator only; ISO timestamps contain further colons."""
    parts = re.split(r'[：:]', text, maxsplit=1)
    if len(parts) != 2:
        return None
    label = re.sub(r'\s+', '', parts[0])
    if label not in LABELS:
        return None
    return label, parts[1].strip()


def located(block, size):
    box = block.get('bbox') or []
    try:
        return (bool(block.get('block_id')) and len(box) == 4 and len(size) == 2
                and all(math.isfinite(v) for v in [*box, *size])
                and 0 <= box[0] < box[2] <= size[0] and 0 <= box[1] < box[3] <= size[1])
    except (TypeError, ValueError):
        return False


def extract(blocks, *, document_id, source_sha256, page_sizes):
    pages = {}
    for block in blocks:
        pages.setdefault(block['page_no'], []).append(block)
    facts, errors = [], []
    for page_no, page_blocks in pages.items():
        fields, duplicates, ambiguous = {}, [], []
        for b in page_blocks:
            pair = labelled_value(b['text'])
            if pair is None:
                continue
            label, value = pair
            if EMBEDDED_FIELD.search(value):
                ambiguous.append(label)
            if label in fields:
                duplicates.append(label)
            fields[label] = (value,b)
        kind = fields.get('文档类型',('',None))[0]
        needed = COMMON + (REQUIREMENT if kind=='工艺要求' else MEASUREMENT)
        missing = [k for k in needed if k not in fields or not fields[k][0]]
        unexpected = sorted(set(fields) - set(needed))
        if kind not in {'工艺要求','质检报告'} or duplicates or missing or ambiguous or unexpected:
            errors.append({'page_no':page_no,'reason':'MISSING_DUPLICATE_OR_UNSUPPORTED_FIELDS',
                           'duplicates':duplicates,'missing':missing,'ambiguous':ambiguous,'unexpected':unexpected})
            continue
        if not all(located(fields[k][1], page_sizes.get(page_no, [])) for k in needed):
            errors.append({'page_no':page_no,'reason':'FIELD_LOCATOR_UNAVAILABLE'}); continue
        value = lambda key: fields[key][0]
        identifiers = ['零件编号','版次'] + ([] if kind=='工艺要求' else ['批次编号','测量编号','报告编号'])
        if (value('指标') != 'holding_temperature' or not re.fullmatch(r'[0-9]+',value('工序编号'))
                or not all(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*',value(k)) for k in identifiers)):
            errors.append({'page_no':page_no,'reason':'UNSUPPORTED_METRIC_OR_ID'}); continue
        roles = {'零件编号':'entity','指标':'header','温度下限':'lower','温度上限':'upper','实测值':'value'}
        refs = []
        for label in needed:
            raw,b = fields[label]
            refs.append({'role':roles.get(label,'context'),'field':label,'text':b['text'],'block_id':b['block_id'],
                         'field_value':raw,
                         'page_no':page_no,'bbox':b['bbox'],'page_size':page_sizes[page_no], 'coordinate_system':'pdf_points',
                         'source_sha256':source_sha256,'block_content_hash':hashlib.sha256(b['text'].encode()).hexdigest()})
        fact = {'fact_id':'ifact-'+hashlib.sha256(f'{document_id}:{page_no}:{PARSER_VERSION}'.encode()).hexdigest()[:24],
                'document_id':document_id,'document_version':1,'source_sha256':source_sha256,'parser_version':PARSER_VERSION,
                'extraction_version':PARSER_VERSION,'entity_name':value('零件编号'),'entity_id':value('零件编号'),'entity_type':'part',
                'operation_no':value('工序编号'),'metric':value('指标'),'raw_header':value('指标'),'source_revision':value('版次'),
                'raw_value':value('温度下限')+' .. '+value('温度上限') if kind=='工艺要求' else value('实测值'),
                'raw_unit':value('单位'),'normalized_unit':None,'normalized_value':None,'normalized_range':None,
                'approval_status':{'已批准':'approved','未批准':'unapproved'}.get(value('审批状态'),'unknown'),
                'verification_status':'candidate','evidence_refs':refs,'method':value('测量方法'),
                'ocr_confidence':min((fields[k][1]['ocr_confidence'] for k in needed
                                     if fields[k][1].get('ocr_confidence') is not None),default=None),
                'fact_kind':'requirement' if kind=='工艺要求' else 'measurement',
                'batch_id':None,'valid_from':None,'valid_to':None,'provenance':'synthetic_demo',
                'extraction_method':'pixel_ocr_labelled_form' if any(fields[k][1].get('ocr_confidence') is not None
                    for k in needed) else 'native_labelled_form','conditions':{'method':value('测量方法')}}
        try:
            if kind=='工艺要求':
                fact['raw_value'] = value('温度下限')+' .. '+value('温度上限')
                fact['valid_from'] = timestamp(value('生效时间')).isoformat()
                fact['valid_to'] = None if value('失效时间')=='无' else timestamp(value('失效时间')).isoformat()
                if fact['valid_to'] and timestamp(fact['valid_to']) <= timestamp(fact['valid_from']):
                    raise ValueError('INVALID_VALIDITY_RANGE')
                low,high = temperature(value('温度下限'),value('单位')),temperature(value('温度上限'),value('单位'))
                if low>high: raise ValueError('INVALID_TEMPERATURE_RANGE')
                fact['normalized_range']={'min':format(low,'f'),'max':format(high,'f'),'min_inclusive':True,'max_inclusive':True}
            else:
                fact.update(raw_value=value('实测值'),batch_id=value('批次编号'),measurement_id=value('测量编号'),
                            report_id=value('报告编号'),measured_at=timestamp(value('测量时点')).isoformat(),
                            source_conclusion=value('报告结论'))
                fact['normalized_value']=format(temperature(fact['raw_value'],fact['raw_unit']),'f')
            fact['normalized_unit']='degC'
        except (ValueError,TypeError) as exc:
            fact['verification_status']='needs_review';fact['extraction_error']=str(exc)
        facts.append(fact)
    return facts, {'extraction_version':PARSER_VERSION,'fact_count':len(facts),'errors':errors,
                   'approval_semantics':'Declared in synthetic source; extraction has not been manually verified'}
