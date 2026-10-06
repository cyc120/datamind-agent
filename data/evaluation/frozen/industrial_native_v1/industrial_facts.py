"""Strict labelled native-PDF form extraction; never accepts fixture or gold text."""
import hashlib
from datamind.industrial_rules import temperature, timestamp

PARSER_VERSION = 'industrial-native-v1'
COMMON = ['文档类型','零件编号','工序编号','指标','版次','审批状态','单位','测量方法']


def extract(blocks, *, document_id, source_sha256, page_sizes):
    pages = {}
    for block in blocks:
        pages.setdefault(block['page_no'], []).append(block)
    facts, errors = [], []
    for page_no, page_blocks in pages.items():
        fields, duplicates = {}, []
        for b in page_blocks:
            if '：' not in b['text']:
                continue
            label,value = b['text'].split('：',1)
            if label in fields:
                duplicates.append(label)
            fields[label] = (value.strip(),b)
        kind = fields.get('文档类型',('',None))[0]
        needed = COMMON + (['生效时间','失效时间','温度下限','温度上限'] if kind=='工艺要求' else
                           ['批次编号','测量编号','报告编号','测量时点','实测值','报告结论'])
        if kind not in {'工艺要求','质检报告'} or duplicates or any(k not in fields for k in needed):
            errors.append({'page_no':page_no,'reason':'MISSING_DUPLICATE_OR_UNSUPPORTED_FIELDS','duplicates':duplicates})
            continue
        value = lambda key: fields[key][0]
        if value('指标') != 'holding_temperature' or not value('零件编号') or not value('工序编号').isdigit():
            errors.append({'page_no':page_no,'reason':'UNSUPPORTED_METRIC_OR_ID'}); continue
        roles = {'零件编号':'entity','指标':'header','温度下限':'lower','温度上限':'upper','实测值':'value'}
        refs = []
        for label in needed:
            raw,b = fields[label]
            refs.append({'role':roles.get(label,'context'),'field':label,'text':b['text'],'block_id':b['block_id'],
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
                'ocr_confidence':None,'fact_kind':'requirement' if kind=='工艺要求' else 'measurement',
                'batch_id':None,'valid_from':None,'valid_to':None,'provenance':'synthetic_demo',
                'extraction_method':'native_labelled_form','conditions':{'method':value('测量方法')}}
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
