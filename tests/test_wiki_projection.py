"""Wiki 知识图谱 P2 投影适配层（``datamind/wiki_projection.py``）的测试。

**本文件全部是纯函数测试，不连接数据库**（`python -m pytest tests/test_wiki_projection.py -q`
可直接跑；系统 python 没有 psycopg2 也不影响，因为这个模块本身不 import 它）。

夹具数据的来源（**真实 payload 形状，不是凭空编的字段名**）：2026-10-14 用
`docker exec datamind-postgres psql -U postgres -d datamind_meta` 只读查出的
`evidence_nodes.payload`，逐条标注在下面的 ``PAYLOADS`` 里，并在
``test_fixture_payloads_carry_only_real_fields`` 中把"只含真实字段"这一条钉住：

============================  ==================================================
节点类型                       来源（只读实测）
============================  ==================================================
``SQLRow``（扁平一行）          ``run-9ab6d6af60c64f32`` 的工业测量行 M006-2
``SQLRow``（columns/rows）      retail 路径，94 行之一
``SQLQuery``                   ``run-f50186b0d3f44017``（demo_retail 聚合）
``Chunk``                      ``payload ? 'number'`` 的 128 行之一（demo_kb）
``Document``（RAG 侧）          186 行带 ``document_sha256`` 的那一类
``Document``（工业侧）          35 行带 ``source_sha256`` 的那一类（互斥，实测无一行两者兼有）
``DocumentFact``（requirement） ``source_revision='A102-V3'``，含真实 ``normalized_range``
``VerificationCheck``          ``run-9ab6d6af60c64f32`` 的 M004-1 行（``version_overlap``）
``ConflictRecord``             ``conflict_type='value_conflict'``（conf-dac12deffa0f4caab917）
``Claim``                      681 行之一
============================  ==================================================

``conflict_records`` 的列/``payload`` 夹具同样来自实测（175 行、四种 ``conflict_type``、
三种 ``resolution_status``）；``selected_fact_ref`` 的 JSON ``null`` 陷阱是实测事实
（175/175 行是 JSON 字面量 ``null``，``IS NOT NULL`` 全部命中）。

跨源路径（``trace_events.output_summary.cross_source_result``）在本库**零运行**
（``SELECT count(*) FROM trace_events WHERE agent_name='cross_source_agent'`` = 0），
所以那部分只能用合成数据测，本文件在相应的测试里明确标注。
"""
import unittest

from datamind import wiki_projection as projection

SPACE = 'wsp-' + 'a' * 24
OTHER_SPACE = 'wsp-' + 'b' * 24
RUN = 'run-9ab6d6af60c64f32'
RAG_RUN = 'run-894b0785725c45f3'

DOC_SHA = '89352d5f5555ca8214599ff93b8742874661232ab6dfb35eb51911c6250a1fc2'
IND_SHA = '0ac6c55838c3ceeee9d9275a4d550e29e84e8e38e4fa03f5e67733e65c0739a2'
SQL_HASH = '92f8efc1ef21314feef08e63903ea953afaf5646ad00d8a24a38a102e14103d7'
SNAP = 'snap-demo_retail-9b6637f76e61'


# ---------------------------------------------------------------- 真实 payload 夹具

PAYLOADS = {
    # 工业测量行：payload 就是扁平一行（NodeDetail.vue:28 与实测 350 行一致）
    'SQLRow': {
        'unit': 'degC', 'value': '870', 'method': 'core', 'metric': 'holding_temperature',
        'part_id': 'A102', 'batch_id': 'B006', 'report_id': 'Q006-2', 'quality_id': 'Q006-2',
        'measured_at': '2026-09-08T11:00:00+08:00', 'operation_no': '30', 'measurement_id': 'M006-2',
    },
    # retail 路径的 SQLRow：94 行是这一种（columns/rows），与上面互不相同
    'SQLRowAggregate': {'rows': [[997]], 'columns': ['count'], 'row_count': 1},
    # SQLQuery：工业侧 35 行带 bound_params，retail 侧 94 行带 bound_params_redacted
    'SQLQuery': {
        'sql': 'SELECT COUNT(*) FROM orders WHERE created_at >= %s AND created_at < %s LIMIT 100',
        'sql_hash': '319add70d694426e79cfebdbc16decebc96c0b2498818f0fbf0d3f527c809c4c',
        'source_id': 'demo_retail',
        'query_frame': {'intent': 'aggregate', 'metrics': [], 'source_id': 'demo_retail',
                        'time_filters': [{'to': '2025-01-01', 'from': '2024-01-01',
                                          'field_id': 'orders.created_at',
                                          'timezone': 'Asia/Shanghai'}]},
        'prompt_version': 'sql-v2', 'source_snapshot_id': SNAP,
        'bound_params_redacted': ['***', '***'],
    },
    # Chunk：正文在 quote（不是 text/excerpt），页码是 page_start/page_end（不是 page_no）
    'Chunk': {
        'quote': '第三条 自签收之日起 7 日内，商品未拆封且不影响二次销售的，支持七天无理由退款。\n'
                 '第四条 质量问题退款不受 7 日限制，凭照片或质检结果受理。',
        'title': '04_售后服务与退款管理制度', 'number': 1, 'origin': 'curated',
        'chunk_id': 'chk-a9e09c78aeae',
        'locators': [{'locator': {'bbox': [62.7, 245.8, 175.2, 260.8], 'unit': 'point',
                                  'page_no': 1, 'document_id': 'doc-ebdef694fdd4',
                                  'locator_kind': 'pdf_region', 'source_version': DOC_SHA},
                      'block_id': 'blk-d8ff2e4b6804', 'contract_version': 'parse-contract-v1'}],
        'page_end': 1, 'page_start': 1,
        'page_url': '/v1/documents/doc-ebdef694fdd4/pages/1?expected_version=1',
        'block_ids': ['blk-d8ff2e4b6804'], 'relations': [], 'collection': 'demo_kb',
        'source_url': '/v1/documents/doc-ebdef694fdd4/file?expected_version=1',
        'document_id': 'doc-ebdef694fdd4',
        'content_hash': '9ed0fbaa508a08c05d88045b2e9bced0b0b7ea896009596668ed4c47aeb8c168',
        'section_path': ['售后服务与退款管理制度', '第二章 退款条件'],
        'source_blocks': [{'bbox': [62.7, 245.8, 175.2, 260.8], 'page_no': 1,
                           'block_id': 'blk-d8ff2e4b6804',
                           'content_hash': 'fb97ab015bb75652e17c358bf3c57cc21a8d70e6cfd8873a3d37417b0000a752',
                           'formula_latex': None, 'ocr_confidence': None,
                           'parser_version': 'pdf-ocr-0.4'}],
        'parser_version': 'pdf-ocr-0.4',
        'quality_detail': {'parse_status': 'parsed', 'document_type': '规章制度',
                           'source_sha256': DOC_SHA,
                           'parse_contract': {'contract_version': 'parse-contract-v1'}},
        'used_in_answer': True, 'document_sha256': DOC_SHA, 'retrieval_score': 0.032522,
        'document_version': 1, 'retrieval_scores': {'dense': 0.68582, 'lexical': 12.32181},
    },
    # Chunk 的"检索到但无原文"形态（WIKI_API.md §11.4：5 个块的 quote 全为空字符串）
    'ChunkEmptyQuote': {
        'quote': '', 'title': '07_退换货政策', 'chunk_id': 'chk-4c358eb26689', 'origin': 'curated',
        'document_id': 'doc-158815ed856f', 'document_sha256': DOC_SHA, 'document_version': 1,
        'page_start': 1, 'page_end': 1, 'block_ids': [], 'collection': 'demo_kb',
        'used_in_answer': False, 'retrieval_score': 0.0101,
    },
    # Document（RAG 侧）：给 origin + document_sha256
    'Document': {
        'title': '04_售后服务与退款管理制度', 'origin': 'curated',
        'source_url': '/v1/documents/doc-ebdef694fdd4/file?expected_version=1',
        'document_id': 'doc-ebdef694fdd4',
        'quality_detail': {'parse_status': 'parsed', 'document_type': '规章制度',
                           'source_sha256': DOC_SHA,
                           'parser_version': 'pdf-ocr-0.4',
                           'parse_contract': {'blocks': 20, 'adapter': 'pdf-native',
                                              'contract_version': 'parse-contract-v1'}},
        'document_sha256': DOC_SHA, 'document_version': 1,
    },
    # Document（工业侧）：给 source_sha256（两者互斥，实测没有一行兼有）
    'DocumentIndustrial': {
        'title': '工业合成演示：工艺与质检原文',
        'source_url': '/v1/documents/doc-industrial-0ac6c55838c3/file',
        'document_id': 'doc-industrial-0ac6c55838c3', 'source_sha256': IND_SHA,
        'document_version': 1,
    },
    # DocumentFact：原文值在 raw_value/raw_unit，实体名在 entity_name，表头在 raw_header
    'DocumentFact': {
        'title': '工业合成演示：工艺与质检原文', 'method': 'core',
        'metric': 'holding_temperature', 'fact_id': 'ifact-08658ba4e2b71b7fc7d65878',
        'batch_id': None, 'page_url': '/v1/documents/doc-industrial-0ac6c55838c3/pages/3',
        'raw_unit': 'degC', 'valid_to': '2026-09-20T00:00:00+08:00', 'entity_id': 'A102',
        'fact_kind': 'requirement', 'raw_value': '835 .. 855', 'collection': 'industrial_demo',
        'conditions': {'method': 'core'}, 'provenance': 'synthetic_demo',
        'raw_header': 'holding_temperature',
        'source_url': '/v1/documents/doc-industrial-0ac6c55838c3/file',
        'valid_from': '2026-09-15T00:00:00+08:00',
        'document_id': 'doc-industrial-0ac6c55838c3', 'entity_name': 'A102',
        'entity_type': 'part', 'operation_no': '30',
        'evidence_refs': [{'bbox': [50.0, 421.7, 126.9, 433.7], 'role': 'lower',
                           'text': '温度下限：835', 'field': '温度下限', 'page_no': 3,
                           'block_id': 'iblk-1a756558c122b21a656d8717',
                           'field_value': '835', 'source_sha256': IND_SHA}],
        'latest_review': None, 'source_sha256': IND_SHA, 'ocr_confidence': None,
        'parser_version': 'industrial-labelled-v2',
        'payload_sha256': '1e0f1a3abd0a3b7b2efbbd1d62e210aa233ec801b3f1f96304786525fc5ca1dd',
        'approval_status': 'approved', 'latest_approval': None, 'normalized_unit': 'degC',
        'source_revision': 'A102-V3', 'document_version': 1,
        'normalized_range': {'max': '855', 'min': '835', 'max_inclusive': True,
                             'min_inclusive': True},
        'normalized_value': None, 'extraction_method': 'native_labelled_form',
        'extraction_version': 'industrial-labelled-v2', 'verification_status': 'candidate',
        'formal_approval_status': 'not_approved',
    },
    # VerificationCheck：整行 row，结论 + 双方原值 + 问题 + 规则
    'VerificationCheck': {
        'unit': 'degC',
        'rules': [{'rule_id': 'same_measurement_decimal_equality', 'conversion': 'identity',
                   'left_value': '850', 'right_value': '850',
                   'rule_version': 'industrial-rules-v1'}],
        'value': '850',
        'issues': [{'kind': 'version_overlap', 'reason': 'MULTIPLE_APPLICABLE_REVISIONS',
                    'status': 'needs_review',
                    'fact_ids': ['ifact-08658ba4e2b71b7fc7d65878',
                                 'ifact-9f8c47a8934c9ce4de917dbd']}],
        'method': 'core', 'metric': 'holding_temperature', 'part_id': 'A102', 'batch_id': 'B004',
        'agreement': 'consistent', 'report_id': 'Q004-1', 'compliance': 'needs_review',
        'quality_id': 'Q004-1', 'measured_at': '2026-09-17T10:00:00+08:00',
        'produced_at': '2026-09-17T10:00:00+08:00', 'report_unit': 'degC', 'operation_no': '30',
        'report_value': '850', 'measurement_id': 'M004-1', 'quality_method': 'core',
        'report_fact_id': 'ifact-b0152605a48d3511e35dff55', 'normalized_unit': 'degC',
        'normalized_value': '850', 'recorded_revision': 'A102-V2', 'applicable_revision': None,
        'requirement_fact_id': None,
    },
    # ConflictRecord：双方原值来自 normalized_values；left/right 是 fact 引用（只有 id）
    'ConflictRecord': {
        'metric': 'holding_temperature', 'reason': 'NO_REGISTERED_AUTHORITY_RULE',
        'rule_id': 'NO_REGISTERED_AUTHORITY_RULE',
        'entity_key': {'part_id': 'A102', 'batch_id': 'B001', 'operation_no': '30',
                       'measurement_id': 'M001-1'},
        'conflict_id': 'conf-dac12deffa0f4caab917', 'rule_version': 'industrial-rules-v1',
        'conflict_type': 'value_conflict',
        'left_fact_refs': [{'kind': 'sql_measurement',
                            'snapshot': 'e8541f8b2d2a1d1b3c34b21627517328c2bfadbb66b6587ca2905e98cd2323b7',
                            'measurement_id': 'M001-1'}],
        'right_fact_refs': [{'fact_id': 'ifact-02dfc4600b7beabde73fe3ea'}],
        'normalized_values': {'production': '870', 'report_raw': '850', 'report_unit': 'degC'},
        'resolution_status': 'unresolved', 'selected_fact_ref': None,
        'comparison_context': {'unit': 'degC', 'method': 'core',
                               'measured_at': '2026-09-05T10:00:00+08:00',
                               'produced_at': '2026-09-05T10:00:00+08:00'},
        'repair_attempt_refs': [],
    },
    'Claim': {'text': '查询结果：997。'},
    'ClaimIndustrial': {
        'text': '测量 M001-1：工艺范围核验 needs_review；来源比较 unresolved。',
    },
}


def node(node_type, payload, *, evidence_id='ev-' + '0' * 16, run_id=RUN, ref_key='ref-1'):
    """一条 ``evidence_nodes`` 行（6 列的真实形状）。"""
    return {'evidence_id': evidence_id, 'run_id': run_id, 'node_type': node_type,
            'ref_key': ref_key, 'payload': payload}


def conflict_row(**overrides):
    """一行 ``conflict_records``（列名与实测 ``information_schema`` 一致）。"""
    row = {
        'conflict_id': 'conf-dac12deffa0f4caab917', 'run_id': RUN,
        'entity_key': {'part_id': 'A102', 'batch_id': 'B001', 'operation_no': '30',
                       'measurement_id': 'M001-1'},
        'metric': 'holding_temperature', 'conflict_type': 'value_conflict',
        'left_fact_refs': [{'kind': 'sql_measurement', 'measurement_id': 'M001-1'}],
        'right_fact_refs': [{'fact_id': 'ifact-02dfc4600b7beabde73fe3ea'}],
        'comparison_context': {'unit': 'degC', 'method': 'core'},
        'normalized_values': {'production': '870', 'report_raw': '850', 'report_unit': 'degC'},
        'rule_id': 'NO_REGISTERED_AUTHORITY_RULE', 'rule_version': 'industrial-rules-v1',
        'resolution_status': 'unresolved',
        # 实测：175/175 行是 JSON 字面量 null，不是 SQL NULL
        'selected_fact_ref': None, 'reason': 'NO_REGISTERED_AUTHORITY_RULE',
        'payload': dict(PAYLOADS['ConflictRecord']),
    }
    row.update(overrides)
    return row


def cross_source_record(**overrides):
    """``evidence_graph.detect_conflicts`` 的一条记录（跨源路径；本库零运行，合成数据）。"""
    record = {
        'conflict_id': 'conf-0123456789abcdef0123',
        'entity_id': 'A102', 'entity_label': 'A102',
        'metric': 'holding_temperature', 'version': 'A102-V2',
        'conflict_type': 'value_conflict', 'resolution_status': 'CONFLICT_UNRESOLVED',
        'rule_id': 'NO_REGISTERED_AUTHORITY_RULE', 'authority_rule_id': None,
        'selected_node_id': None,
        'left': {'node_id': 'n-' + '1' * 20, 'node_type': 'measurement', 'source_kind': 'sql',
                 'source_ref': 'demo_industrial', 'value': '870', 'unit': 'degC',
                 'normalized_value': '870.000', 'normalized_unit': 'degC',
                 'source_sha256': IND_SHA, 'version': 'A102-V2',
                 'time_scope': {'from': '2026-09-05', 'to': None},
                 'target': 'degC', 'record_scope': 'M001-1'},
        'right': {'node_id': 'n-' + '2' * 20, 'node_type': 'doc_block', 'source_kind': 'doc',
                  'source_ref': 'doc-industrial-0ac6c55838c3', 'value': '850', 'unit': 'degC',
                  'normalized_value': '850.000', 'normalized_unit': 'degC',
                  'source_sha256': IND_SHA, 'version': 'A102-V2',
                  'time_scope': {'from': '2026-09-05', 'to': None},
                  'target': 'degC', 'record_scope': 'M001-1'},
    }
    record.update(overrides)
    return record


# ================================================================ 夹具自检

class FixtureShapeTest(unittest.TestCase):
    """夹具本身：字段名必须来自真实 payload，不能是编出来的。"""

    def test_fixture_payloads_carry_only_real_fields(self):
        # 实测（2026-10-14，只读）：每个 node_type 的 payload 键全集。夹具里出现的键必须在这个
        # 集合内，否则就是把"猜的字段名"当成了真实形状。
        real_keys = {
            'SQLRow': {'value', 'unit', 'method', 'metric', 'part_id', 'batch_id', 'report_id',
                       'quality_id', 'measured_at', 'operation_no', 'measurement_id',
                       'columns', 'rows', 'row_count'},
            'SQLRowAggregate': {'value', 'unit', 'method', 'metric', 'part_id', 'batch_id',
                                'report_id', 'quality_id', 'measured_at', 'operation_no',
                                'measurement_id', 'columns', 'rows', 'row_count'},
            'SQLQuery': {'sql', 'sql_hash', 'source_id', 'query_frame', 'prompt_version',
                         'source_snapshot_id', 'bound_params', 'bound_params_redacted'},
            'Chunk': {'quote', 'title', 'number', 'origin', 'chunk_id', 'locators', 'page_end',
                      'page_start', 'page_url', 'block_ids', 'relations', 'collection',
                      'source_url', 'document_id', 'content_hash', 'section_path',
                      'source_blocks', 'parser_version', 'quality_detail', 'used_in_answer',
                      'document_sha256', 'retrieval_score', 'document_version',
                      'retrieval_scores'},
            'ChunkEmptyQuote': {'quote', 'title', 'number', 'origin', 'chunk_id', 'locators',
                                'page_end', 'page_start', 'page_url', 'block_ids', 'relations',
                                'collection', 'source_url', 'document_id', 'content_hash',
                                'section_path', 'source_blocks', 'parser_version',
                                'quality_detail', 'used_in_answer', 'document_sha256',
                                'retrieval_score', 'document_version', 'retrieval_scores'},
            'Document': {'title', 'origin', 'source_url', 'document_id', 'quality_detail',
                         'document_sha256', 'document_version', 'source_sha256'},
            'DocumentIndustrial': {'title', 'origin', 'source_url', 'document_id',
                                   'quality_detail', 'document_sha256', 'document_version',
                                   'source_sha256'},
            'DocumentFact': {'fact_id', 'fact_kind', 'metric', 'raw_value', 'raw_unit',
                             'raw_header', 'normalized_value', 'normalized_unit',
                             'normalized_range', 'entity_id', 'entity_name', 'entity_type',
                             'document_id', 'document_version', 'source_sha256',
                             'source_url', 'source_revision', 'valid_from', 'valid_to',
                             'conditions', 'method', 'measured_at', 'measurement_id',
                             'operation_no', 'batch_id', 'report_id', 'parser_version',
                             'extraction_method', 'extraction_version', 'provenance',
                             'approval_status', 'formal_approval_status', 'latest_approval',
                             'latest_review', 'verification_status', 'evidence_refs',
                             'ocr_confidence', 'payload_sha256', 'page_url', 'collection',
                             'title', 'source_conclusion'},
            'VerificationCheck': {'compliance', 'agreement', 'batch_id', 'part_id', 'operation_no',
                                  'measurement_id', 'metric', 'value', 'unit', 'normalized_value',
                                  'normalized_unit', 'report_value', 'report_unit',
                                  'recorded_revision', 'applicable_revision', 'measured_at',
                                  'produced_at', 'method', 'quality_id', 'quality_method',
                                  'report_id', 'report_fact_id', 'requirement_fact_id',
                                  'issues', 'rules'},
            'ConflictRecord': {'conflict_id', 'conflict_type', 'resolution_status', 'entity_key',
                               'metric', 'rule_id', 'rule_version', 'left_fact_refs',
                               'right_fact_refs', 'normalized_values', 'comparison_context',
                               'selected_fact_ref', 'reason', 'repair_attempt_refs'},
            'Claim': {'text'},
            'ClaimIndustrial': {'text'},
        }
        for name, payload in PAYLOADS.items():
            if isinstance(payload, dict):
                self.assertLessEqual(set(payload), real_keys[name],
                                     '%s 有非真实字段: %s' % (name, set(payload) - real_keys[name]))


# ================================================================ 1. 节点类型投影适配

class NodeTypeAdaptationTest(unittest.TestCase):
    """9 类真实节点 + 闭集 4 类 + 未知类型。"""

    ALL_NINE = ('Claim', 'SQLQuery', 'SQLRow', 'Chunk', 'Document', 'DocumentFact',
                'VerificationCheck', 'ConflictRecord', 'AuthorityRule')
    CLOSED_FOUR = ('sql_row', 'doc_block', 'measurement', 'spec_clause')

    def test_nine_real_node_types_are_recognised_and_none_is_dropped(self):
        for raw in self.ALL_NINE:
            verdict = projection.normalize_node_type(raw)
            self.assertTrue(verdict['supported'], raw)
            self.assertIsNotNone(verdict['node_type'], raw)
            self.assertIsNone(verdict['unsupported_reason'], raw)
            self.assertEqual(verdict['raw_type'], raw, '原始类型必须原样保留')

    def test_closed_vocabulary_node_types_are_recognised(self):
        # evidence_graph.py:29 的闭集 4 类：**本库 0 行**（WIKI_DOMAIN.md §3.4 的诚实声明），
        # 所以正确性只能靠这里的合成/词表测试证明，不能声称已被真实数据覆盖。
        for raw in self.CLOSED_FOUR:
            verdict = projection.normalize_node_type(raw)
            self.assertTrue(verdict['supported'], raw)
            self.assertEqual(verdict['node_type'], raw,
                             '闭集词表本身就是 Wiki 认识的形式（cross_source.py:308 真的把它写进 '
                             'evidence_nodes.node_type）')

    def test_migration_019_node_vocabulary_is_the_only_recognition_source(self):
        # 019:62-70 的 node_type CHECK 全集；除闭集 4 类外都应得到明确判定（认识或明确不认识），
        # 不允许"抛异常"或"悄悄变成 None 却仍 supported"。
        declared = ('Question', 'PlanTask', 'TableCell', 'Formula')
        for raw in declared:
            verdict = projection.normalize_node_type(raw)
            self.assertFalse(verdict['supported'], raw)
            self.assertIsNone(verdict['node_type'], raw)
            self.assertEqual(verdict['unsupported_reason'], 'TYPE_NOT_IN_WIKI_VOCABULARY')

    def test_selection_marker_nodes_are_not_forced_into_an_entry(self):
        # wiki_projection 的选型：SQLQuery / VerificationCheck / ConflictRecord / Document /
        # Chunk 都是证据与出处，不产出词条 —— 给它们编一个业务键就是编造身份。
        for raw in ('SQLQuery', 'VerificationCheck', 'ConflictRecord', 'Document', 'Chunk',
                    'AuthorityRule'):
            normalized = projection.normalize_node_type(raw)['node_type']
            self.assertIsNone(
                projection.entry_type_for_node(normalized, PAYLOADS.get(raw, {})), raw)

    def test_every_real_payload_projects_to_an_evidence_ref(self):
        for name in ('SQLRow', 'SQLRowAggregate', 'SQLQuery', 'Chunk', 'ChunkEmptyQuote',
                     'Document', 'DocumentIndustrial', 'DocumentFact', 'VerificationCheck',
                     'ConflictRecord', 'Claim', 'ClaimIndustrial'):
            raw = {'SQLRowAggregate': 'SQLRow', 'ChunkEmptyQuote': 'Chunk',
                   'DocumentIndustrial': 'Document', 'ClaimIndustrial': 'Claim'}.get(name, name)
            ref = projection.project_evidence_ref(SPACE, node(raw, PAYLOADS[name]))
            self.assertTrue(projection.validate_id('evidence_ref', ref['evidence_ref_id']), name)
            self.assertTrue(projection.HEX64.match(ref['evidence_fingerprint']), name)
            self.assertNotIn('sha256:', ref['evidence_fingerprint'], name)
            self.assertIn(ref['locator_kind'], projection.LOCATOR_KINDS, name)

    def test_render_block_uses_the_field_names_node_detail_vue_reads(self):
        # NodeDetail.vue:133-137 Chunk 正文读 quote（不是 text/excerpt），页码读 page_start
        render = projection.render_for_node('Chunk', PAYLOADS['Chunk'])
        self.assertEqual(render['quote'], PAYLOADS['Chunk']['quote'])
        self.assertEqual(render['page_start'], 1)
        self.assertEqual(render['page_end'], 1)
        self.assertNotIn('page_no', render, 'Chunk 的 payload 里没有 page_no，不得凭空造')
        # NodeDetail.vue:154-168 ConflictRecord 读 normalized_values / left_fact_refs
        render = projection.render_for_node('ConflictRecord', PAYLOADS['ConflictRecord'])
        self.assertEqual(render['normalized_values'], PAYLOADS['ConflictRecord']
                         ['normalized_values'])
        self.assertEqual(render['resolution_status'], 'unresolved')
        # NodeDetail.vue:252-268 DocumentFact 读 raw_value / raw_unit / entity_name
        render = projection.render_for_node('DocumentFact', PAYLOADS['DocumentFact'])
        self.assertEqual(render['raw_value'], '835 .. 855')
        self.assertEqual(render['entity_name'], 'A102')
        self.assertNotIn('value', render, 'DocumentFact 的 payload 没有 value 键')
        # 未知类型 -> 前端走 JSON 兜底分支（NodeDetail.vue:352-354）
        render = projection.render_for_node('WeirdThing', {'a': 1})
        self.assertTrue(render['fallback_json'])
        self.assertEqual(render['payload'], {'a': 1})

    def test_unknown_node_type_keeps_raw_value_and_is_marked_unsupported(self):
        ref = projection.project_evidence_ref(
            SPACE, node('WeirdThing', {'a': 1}, evidence_id='ev-' + '9' * 16))
        self.assertFalse(ref['supported'])
        self.assertIsNone(ref['node_type'])
        self.assertEqual(ref['unsupported_reason'], 'TYPE_NOT_IN_WIKI_VOCABULARY')
        self.assertEqual(ref['raw_node_types'], ['WeirdThing'], '原始类型不可丢（3.4 规则 1）')
        self.assertEqual(ref['raw_node_type'], 'WeirdThing')

    def test_blank_raw_node_type_becomes_an_explicit_marker(self):
        # wiki_evidence_refs 有 CHECK (NOT ('' = ANY (raw_node_types)))：空串会让插入失败，
        # 而 unsupported 又要求 raw_node_types 非空。
        self.assertEqual(projection.raw_node_types_for(None), ['<unknown>'])
        self.assertEqual(projection.raw_node_types_for(['', '  ']), ['<unknown>'])
        self.assertEqual(projection.raw_node_types_for('Claim'), ['Claim'])


# ================================================================ 2. 三类身份

class EntryIdentityTest(unittest.TestCase):
    """词条身份（WIKI_DOMAIN.md §4.1）。"""

    def test_entry_id_format_matches_the_migration_check(self):
        identity = projection.entry_identity(SPACE, 'ns', 'metric', 'holding_temperature')
        self.assertTrue(projection.validate_id('entry', identity['entry_id']),
                        identity['entry_id'])
        self.assertEqual(len(identity['entry_id']), len('wiki-') + 24)

    def test_business_key_is_the_four_frozen_segments(self):
        identity = projection.entry_identity(SPACE, 'industrial-demo', 'norm',
                                             'doc-1\x1fA102-V2\x1fclause-1')
        self.assertEqual(identity['business_key'], {
            'space_id': SPACE, 'namespace': 'industrial-demo', 'entry_type': 'norm',
            'key_part': 'doc-1\x1fA102-V2\x1fclause-1', 'confirmation': 'pending'})

    def test_business_key_is_stable_while_the_title_is_not_part_of_it(self):
        first = projection.entry_identity(SPACE, 'ns', 'metric', 'holding_temperature')
        second = projection.entry_identity(SPACE, 'ns', 'metric', 'holding_temperature')
        self.assertEqual(first['business_key'], second['business_key'])
        self.assertEqual(first['entry_id'], second['entry_id'],
                         '同一业务键必须得到同一 entry_id（幂等投影）')
        # 跨空间不归一（4.1 规则 5）：space_id 是业务键第一段
        other = projection.entry_identity(OTHER_SPACE, 'ns', 'metric', 'holding_temperature')
        self.assertNotEqual(first['entry_id'], other['entry_id'])
        self.assertNotEqual(first['business_key'], other['business_key'])

    def test_pending_entity_key_can_never_be_confirmed(self):
        part, confirmation = projection.entity_key_part('part', None, confirmation='confirmed',
                                                        title='  设备 A102  ')
        self.assertEqual(part, 'pending:设备 a102')
        self.assertEqual(confirmation, 'pending')
        with self.assertRaises(projection.ProjectionError) as caught:
            projection.entry_identity(SPACE, 'ns', 'business_entity', 'pending:a102',
                                      confirmation='confirmed')
        self.assertEqual(caught.exception.code, 'WIKI_ENTITY_KEY_PENDING_MISMATCH')

    def test_confirmed_entity_key_is_type_plus_us_plus_key(self):
        part, confirmation = projection.entity_key_part('part', 'A102')
        self.assertEqual(part, 'part\x1fA102')
        self.assertEqual(confirmation, 'confirmed')

    def test_metric_key_part_is_the_canonical_name_never_the_title(self):
        self.assertEqual(projection.metric_key_part('demo_retail.sales_amount'),
                         'demo_retail.sales_amount')
        with self.assertRaises(projection.ProjectionError):
            projection.metric_key_part('   ')

    def test_norm_key_part_contains_the_source_revision(self):
        # WIKI_DOMAIN.md §2.3：A102-V2 与 A102-V3 是两条规范，不是一条规范的两个版本号
        v2 = projection.norm_key_part('doc-industrial-0ac6c55838c3', 'A102-V2', 'clause-1')
        v3 = projection.norm_key_part('doc-industrial-0ac6c55838c3', 'A102-V3', 'clause-1')
        self.assertNotEqual(v2, v3)
        self.assertEqual(v2, 'doc-industrial-0ac6c55838c3\x1fA102-V2\x1fclause-1')
        with self.assertRaises(projection.ProjectionError):
            projection.norm_key_part('doc-1', None, 'clause-1')

    def test_conclusion_key_part_never_merges_across_runs(self):
        first = projection.conclusion_key_part('run-1', 'claim-a')
        second = projection.conclusion_key_part('run-2', 'claim-a')
        self.assertNotEqual(first, second)
        self.assertEqual(first, 'run-1\x1fclaim-a')

    def test_requirement_fact_becomes_a_norm_measurement_fact_becomes_a_metric(self):
        norm = projection.project_entry(SPACE, 'industrial-demo', 'document_fact',
                                        PAYLOADS['DocumentFact'], run_id=RUN,
                                        ref_key='ifact-08658ba4e2b71b7fc7d65878')
        self.assertEqual(norm['entry_type'], 'norm')
        self.assertEqual(norm['key_part'], 'doc-industrial-0ac6c55838c3\x1fA102-V3\x1f')
        measurement_fact = dict(PAYLOADS['DocumentFact'], fact_kind='measurement',
                                measurement_id='M004-1')
        metric = projection.project_entry(SPACE, 'industrial-demo', 'document_fact',
                                          measurement_fact, run_id=RUN)
        self.assertEqual(metric['entry_type'], 'metric')
        self.assertEqual(metric['key_part'], 'holding_temperature')

    def test_claim_projects_to_a_conclusion_entry_keyed_by_run_and_claim(self):
        entry = projection.project_entry(SPACE, 'demo-kb', 'claim', PAYLOADS['Claim'],
                                         run_id=RAG_RUN, ref_key='claim-80ac4ad77526407b')
        self.assertEqual(entry['entry_type'], 'conclusion')
        self.assertEqual(entry['key_part'], RAG_RUN + '\x1fclaim-80ac4ad77526407b')
        self.assertEqual(entry['title'], '查询结果:997。')
        self.assertTrue(entry['demo_data'], '本库全部是合成演示数据，必须带标记')

    def test_citation_markers_are_not_part_of_the_title(self):
        entry = projection.project_entry(
            SPACE, 'demo-kb', 'claim', {'text': '无线降噪耳机 Pro 在降噪开启时续航约为 38 小时。 [1]'},
            run_id=RAG_RUN, ref_key='claim-1')
        self.assertNotIn('[1]', entry['title'])
        self.assertIn('38 小时', entry['title'])

    def test_the_metric_key_is_the_sources_canonical_name_not_the_chinese_title(self):
        # WIKI_DOMAIN.md §2.2：business_key 用来源内的规范名（holding_temperature），不是中文标签
        entry = projection.project_entry(SPACE, 'industrial-demo', 'measurement',
                                         PAYLOADS['SQLRow'], run_id=RUN)
        self.assertEqual(entry['entry_type'], 'metric')
        self.assertEqual(entry['key_part'], 'holding_temperature')
        self.assertEqual(entry['title'], 'holding_temperature')

    def test_a_norm_without_a_source_revision_cannot_be_keyed(self):
        # WIKI_DOMAIN.md §2.3：source_revision 必须进键；缺了就拒绝，不得退化成按标题建键
        fact = dict(PAYLOADS['DocumentFact'], source_revision=None)
        with self.assertRaises(projection.ProjectionError) as caught:
            projection.project_entry(SPACE, 'industrial-demo', 'document_fact', fact, run_id=RUN)
        self.assertEqual(caught.exception.code, 'WIKI_NORM_KEY_UNRESOLVED')

    def test_a_conclusion_needs_both_a_run_and_a_claim_id(self):
        with self.assertRaises(projection.ProjectionError) as caught:
            projection.project_entry(SPACE, 'demo-kb', 'claim', {'text': 'x'}, run_id=RUN)
        self.assertEqual(caught.exception.code, 'WIKI_CONCLUSION_KEY_UNRESOLVED')

    def test_unknown_entry_type_is_refused(self):
        with self.assertRaises(projection.ProjectionError) as caught:
            projection.entry_identity(SPACE, 'ns', 'spec_clause', 'k')
        self.assertEqual(caught.exception.code, 'WIKI_ENTRY_TYPE_UNKNOWN')


class EvidenceIdentityTest(unittest.TestCase):
    """证据身份（WIKI_DOMAIN.md §4.2，含 run_scoped 降级）。"""

    def ref(self, payload, node_type, *, run_id=RUN, evidence_id='ev-' + '0' * 16, space=SPACE,
            ref_key='ref-1'):
        return projection.project_evidence_ref(
            space, node(node_type, payload, evidence_id=evidence_id, run_id=run_id,
                        ref_key=ref_key))

    def test_fingerprint_is_bare_hex64_without_prefix(self):
        ref = self.ref(PAYLOADS['Document'], 'Document')
        self.assertEqual(len(ref['evidence_fingerprint']), 64)
        self.assertTrue(projection.HEX64.match(ref['evidence_fingerprint']))
        self.assertNotIn('sha256:', ref['evidence_fingerprint'],
                         '指纹存裸 hex64，接口层才加 sha256: 前缀')

    def test_same_input_gives_the_same_fingerprint_and_the_same_ref_id(self):
        first = self.ref(PAYLOADS['Document'], 'Document')
        second = self.ref(PAYLOADS['Document'], 'Document')
        self.assertEqual(first['evidence_fingerprint'], second['evidence_fingerprint'])
        self.assertEqual(first['evidence_ref_id'], second['evidence_ref_id'])

    def test_the_same_evidence_in_two_runs_is_one_reference(self):
        # P1 验收"形成一个可复用证据引用和两条运行映射"：指纹不含 run_id
        first = self.ref(PAYLOADS['Document'], 'Document', run_id='run-1',
                         evidence_id='ev-' + '1' * 16)
        second = self.ref(PAYLOADS['Document'], 'Document', run_id='run-2',
                          evidence_id='ev-' + '2' * 16)
        self.assertEqual(first['evidence_ref_id'], second['evidence_ref_id'])
        self.assertEqual(first['evidence_fingerprint'], second['evidence_fingerprint'])
        self.assertNotEqual(first['evidence_id'], second['evidence_id'],
                            '一次运行 = 一条出现记录（4.2 规则 2）')

    def test_a_different_space_gives_a_different_fingerprint(self):
        first = self.ref(PAYLOADS['Document'], 'Document', space=SPACE)
        second = self.ref(PAYLOADS['Document'], 'Document', space=OTHER_SPACE)
        self.assertNotEqual(first['evidence_fingerprint'], second['evidence_fingerprint'])
        self.assertNotEqual(first['evidence_ref_id'], second['evidence_ref_id'])

    def test_a_new_source_version_gives_a_new_fingerprint(self):
        # 4.2 规则 5：document_sha256 变化 -> 新指纹、新证据引用；旧引用保留
        first = self.ref(PAYLOADS['Document'], 'Document')
        changed = dict(PAYLOADS['Document'], document_sha256='b' * 64)
        second = self.ref(changed, 'Document')
        self.assertNotEqual(first['evidence_fingerprint'], second['evidence_fingerprint'])

    def test_document_sha256_and_source_sha256_are_read_by_coalesce_order(self):
        # 实测互斥：13 个文档只有 document_sha256（186 节点），工业文档只有 source_sha256（35）
        rag = self.ref(PAYLOADS['Document'], 'Document')
        self.assertEqual(rag['source_sha256'], DOC_SHA)
        self.assertEqual(rag['source_snapshot_field'], 'document_sha256')
        industrial = self.ref(PAYLOADS['DocumentIndustrial'], 'Document')
        self.assertEqual(industrial['source_sha256'], IND_SHA)
        self.assertEqual(industrial['source_snapshot_field'], 'source_sha256')
        self.assertFalse(industrial['run_scoped'],
                         '只读一个字段会让工业文档的 35 个节点全部退化成运行隔离')

    def test_run_scoped_is_exactly_source_sha256_is_null(self):
        with_snapshot = self.ref(PAYLOADS['Document'], 'Document')
        without = self.ref({'document_id': 'doc-1', 'title': 'x', 'page_no': 1,
                            'bbox': [1, 2, 3, 4], 'block_id': 'blk-1'}, 'Document')
        self.assertFalse(with_snapshot['run_scoped'])
        self.assertIsNone(with_snapshot['run_scoped_run_id'])
        self.assertTrue(without['run_scoped'])
        self.assertEqual(without['run_scoped_run_id'], RUN, '快照缺失时必须钉住 run id')
        self.assertIsNone(without['source_sha256'],
                          '空串要规范成 SQL NULL，否则 CHECK 与语义都会含糊')
        self.assertNotEqual(with_snapshot['evidence_fingerprint'],
                            without['evidence_fingerprint'])

    def test_run_scoped_fingerprint_differs_per_run_and_never_crosses_runs(self):
        payload = {'document_id': 'doc-1', 'title': 'x', 'page_no': 1, 'bbox': [1, 2, 3, 4],
                   'block_id': 'blk-1'}
        first = self.ref(payload, 'Document', run_id='run-1', evidence_id='ev-' + '1' * 16)
        second = self.ref(payload, 'Document', run_id='run-2', evidence_id='ev-' + '2' * 16)
        self.assertNotEqual(first['evidence_fingerprint'], second['evidence_fingerprint'])
        self.assertNotEqual(first['evidence_ref_id'], second['evidence_ref_id'],
                            '不得跨运行复用（4.2 规则 1）')

    def test_identity_refuses_the_two_impossible_combinations(self):
        with self.assertRaises(projection.ProjectionError) as caught:
            projection.evidence_identity(SPACE, 'doc', 'doc-1', DOC_SHA, 'pdf_region',
                                         {'page_no': 1}, 'a' * 64, run_scoped=True)
        self.assertEqual(caught.exception.code, 'WIKI_RUN_SCOPED_WITH_SNAPSHOT')
        with self.assertRaises(projection.ProjectionError) as caught:
            projection.evidence_identity(SPACE, 'doc', 'doc-1', None, 'pdf_region',
                                         {'page_no': 1}, 'a' * 64, run_scoped=False)
        self.assertEqual(caught.exception.code, 'WIKI_SOURCE_SNAPSHOT_ABSENT')
        with self.assertRaises(projection.ProjectionError) as caught:
            projection.evidence_identity(SPACE, 'doc', 'doc-1', None, 'pdf_region',
                                         {'page_no': 1}, 'a' * 64, run_id=None,
                                         run_scoped=True)
        self.assertEqual(caught.exception.code, 'WIKI_RUN_ID_REQUIRED')

    def test_fingerprint_excludes_confidence_and_retrieval_score(self):
        # 4.2 规则 3：排序、检索得分和模型置信度不决定权威
        base = dict(PAYLOADS['Chunk'])
        noisy = dict(base, retrieval_score=0.9999, retrieval_scores={'dense': 1.0, 'lexical': 99.0})
        first = self.ref(base, 'Chunk')
        second = self.ref(noisy, 'Chunk')
        self.assertEqual(first['evidence_fingerprint'], second['evidence_fingerprint'],
                         '检索得分不得进入指纹')

    def test_locator_kind_and_required_keys_follow_the_migration_checks(self):
        ref = self.ref(PAYLOADS['Chunk'], 'Chunk')
        self.assertEqual(ref['locator_kind'], 'pdf_region')
        for key in ('page_no', 'bbox', 'block_id'):
            self.assertIn(key, ref['locator'], 'pdf_region 的三个键由迁移的 CHECK 强制')
        aggregate = self.ref(PAYLOADS['SQLRowAggregate'], 'SQLRow')
        self.assertEqual(aggregate['locator_kind'], 'sql_aggregate')
        self.assertIn('sql_hash', aggregate['locator'],
                      'sql_record / sql_aggregate 必须带 sql_hash')
        record = self.ref(PAYLOADS['SQLRow'], 'SQLRow', ref_key=SQL_HASH)
        self.assertEqual(record['locator_kind'], 'sql_record')
        self.assertIn('sql_hash', record['locator'])
        self.assertEqual(record['locator']['measurement_id'], 'M006-2')

    def test_a_partial_pdf_region_degrades_to_text_span_instead_of_failing_a_check(self):
        # 只有 block_id 没有 page/bbox：pdf_region 会撞 CHECK，text_span 才是真话
        ref = self.ref({'document_id': 'doc-1', 'title': 'x', 'block_id': 'blk-1'}, 'Document')
        self.assertEqual(ref['locator_kind'], 'text_span')
        self.assertEqual(ref['locator']['block_id'], 'blk-1')
        self.assertTrue(ref['locator_resolved'],
                        'text_span 只要求 block_id，所以它是一条可解析的定位')
        self.assertEqual(ref['source_validity_hint'], None)

    def test_a_node_with_no_locator_at_all_says_the_locator_is_unavailable(self):
        # ChunkEmptyQuote 的真实形状：block_ids 为空、page 有、bbox 没有
        # -> pdf_region 与 text_span 都建不起来，只能如实报"定位不可用"，但证据仍然可见
        ref = self.ref(PAYLOADS['ChunkEmptyQuote'], 'Chunk')
        self.assertFalse(ref['locator_resolved'])
        self.assertEqual(ref['source_validity_hint'], 'WIKI_LOCATOR_UNAVAILABLE')
        self.assertIn('ref.locator', ref['provenance_incomplete']['missing_fields'])
        self.assertTrue(projection.validate_id('evidence_ref', ref['evidence_ref_id']),
                        '定位不可用不等于整条引用不可用')
        # 空 block_id 仍然给出一个真话锚点（text_span 的 NOT NULL 列）
        self.assertEqual(ref['locator_kind'], 'text_span')
        self.assertEqual(ref['locator']['block_id'], 'chk-4c358eb26689')

    def test_record_sha256_is_a_bare_hex_digest_and_prefers_the_sources_own(self):
        ref = self.ref(PAYLOADS['Chunk'], 'Chunk')
        self.assertEqual(ref['record_sha256'], PAYLOADS['Chunk']['content_hash'])
        self.assertTrue(projection.HEX64.match(ref['record_sha256']))
        # 没有自带摘要时按持久化内容摘要，仍然是 64 hex
        ref = self.ref({'document_id': 'doc-1', 'block_id': 'blk-1'}, 'Document')
        self.assertTrue(projection.HEX64.match(ref['record_sha256']))

    def test_empty_excerpt_stays_empty_and_is_never_substituted(self):
        # WIKI_API.md §11.4：检索到块但没有可引用原文 -> excerpt 就是空串，不得用邻近正文替代；
        # 而"检索到了"这个事实也必须同时可见（这里由 locator/quote 的其余字段承载）。
        ref = self.ref(PAYLOADS['ChunkEmptyQuote'], 'Chunk')
        self.assertEqual(ref['excerpt'], '')
        self.assertEqual(ref['source_ref'], 'doc-158815ed856f')
        self.assertEqual(ref['locator_kind'], 'text_span')
        self.assertEqual(ref['source_kind'], 'doc')
        self.assertTrue(projection.validate_id('evidence_ref', ref['evidence_ref_id']))

    def test_evidence_ref_id_is_derived_from_the_fingerprint(self):
        ref = self.ref(PAYLOADS['Chunk'], 'Chunk')
        expected = projection.allocate_id(
            'evidence_ref', seed=SPACE + '\x1f' + ref['evidence_fingerprint'])
        self.assertEqual(ref['evidence_ref_id'], expected)


class ComparisonIdentityTest(unittest.TestCase):
    """主张比较身份（WIKI_DOMAIN.md §4.3）。"""

    def test_comparison_key_is_bare_hex64(self):
        key = projection.comparison_key(SPACE, {'part_id': 'A102', 'confirmation': 'pending'},
                                       'holding_temperature')
        self.assertTrue(projection.HEX64.match(key), key)
        self.assertNotIn('sha256:', key)

    def test_same_group_gives_the_same_key(self):
        entity = {'part_id': 'A102', 'batch_id': 'B001', 'confirmation': 'pending'}
        first = projection.comparison_key(SPACE, entity, 'holding_temperature',
                                          record_scope={'measurement_id': 'M001-1'},
                                          statistic='core')
        second = projection.comparison_key(SPACE, entity, 'holding_temperature',
                                           record_scope={'measurement_id': 'M001-1'},
                                           statistic='core')
        self.assertEqual(first, second)

    def test_a_different_time_scope_or_record_is_a_different_group_not_a_conflict(self):
        # 4.3 规则 2：指标相同但时间或口径不同的主张连比较组都不同
        entity = {'part_id': 'A102'}
        base = projection.comparison_key(SPACE, entity, 'holding_temperature',
                                         time_scope={'from': '2026-09-01', 'to': None},
                                         record_scope={'measurement_id': 'M001-1'})
        other_time = projection.comparison_key(SPACE, entity, 'holding_temperature',
                                               time_scope={'from': '2026-10-01', 'to': None},
                                               record_scope={'measurement_id': 'M001-1'})
        other_record = projection.comparison_key(SPACE, entity, 'holding_temperature',
                                                 time_scope={'from': '2026-09-01', 'to': None},
                                                 record_scope={'measurement_id': 'M002-1'})
        self.assertNotEqual(base, other_time)
        self.assertNotEqual(base, other_record)

    def test_a_different_space_is_a_different_group(self):
        entity = {'part_id': 'A102'}
        self.assertNotEqual(projection.comparison_key(SPACE, entity, 'm'),
                            projection.comparison_key(OTHER_SPACE, entity, 'm'))

    def test_empty_optional_groups_normalise_to_null_not_to_an_empty_object(self):
        # wiki_assertions 的 CHECK 要求 jsonb_typeof = 'object' 或 SQL NULL；'{}' 与 NULL 必须等价
        entity = {'part_id': 'A102'}
        self.assertEqual(projection.comparison_key(SPACE, entity, 'm', time_scope={},
                                                   record_scope={}, applicability={},
                                                   statistic='  '),
                         projection.comparison_key(SPACE, entity, 'm'))

    def test_assertion_identity_reads_the_real_industrial_fields(self):
        identity = projection.assertion_identity(SPACE, PAYLOADS['SQLRow'],
                                                 node_type='measurement')
        self.assertEqual(identity['predicate'], 'holding_temperature')
        self.assertEqual(identity['record_scope'],
                         {'measurement_id': 'M006-2', 'batch_id': 'B006', 'part_id': 'A102',
                          'operation_no': '30', 'quality_id': 'Q006-2', 'report_id': 'Q006-2'})
        self.assertEqual(identity['statistic'], 'core')
        self.assertEqual(identity['entity_key']['part_id'], 'A102')
        self.assertEqual(identity['entity_key']['confirmation'], 'pending',
                         '未确认的实体键必须带 pending 标记')
        self.assertEqual(identity['record_scope']['measurement_id'], 'M006-2')

    def test_assertion_identity_refuses_a_node_that_names_no_entity(self):
        with self.assertRaises(projection.ProjectionError) as caught:
            projection.assertion_identity(SPACE, {'metric': 'm'}, node_type='measurement')
        self.assertEqual(caught.exception.code, 'WIKI_ENTITY_KEY_UNRESOLVED')


# ================================================================ 3. 关系映射

class RelationMappingTest(unittest.TestCase):
    """关系词表与未知类型（WIKI_DOMAIN.md §3.2-3.4）。"""

    def test_the_projected_half_is_the_union_of_the_two_existing_vocabularies(self):
        self.assertEqual(set(projection.PROJECTED_RELATION_TYPES),
                         set(projection.EDGE_TYPE_VOCABULARY))
        for edge_type in projection.EDGE_TYPE_VOCABULARY:
            verdict = projection.relation_type_for(edge_type)
            self.assertTrue(verdict['supported'], edge_type)
            self.assertEqual(verdict['origin'], 'projected', edge_type)
            self.assertEqual(verdict['raw_type'], edge_type, edge_type)

    def test_all_twenty_one_wiki_relation_words_are_covered(self):
        expected = {
            'entity_match', 'version_scope', 'unit_equivalent', 'time_overlap', 'supports',
            'conflicts', 'derived_from', 'retrieved_from', 'maps_to', 'contradicts',
            'verified_by', 'repaired_from', 'resolved_by', 'measures', 'applies_to', 'scoped_by',
            'threshold_of', 'about', 'justified_by', 'supersedes', 'restates'}
        self.assertEqual(projection.ALL_RELATION_TYPES, expected)
        self.assertEqual(len(projection.ALL_RELATION_TYPES), 21)

    def test_contradicts_projects_as_conflicts_and_keeps_the_raw_word(self):
        # 3.3：同一语义两个词，Wiki 统一投影为 conflicts，raw_type 保留原值
        verdict = projection.relation_type_for('contradicts')
        self.assertEqual(verdict['relation_type'], 'conflicts')
        self.assertEqual(verdict['raw_type'], 'contradicts')
        self.assertTrue(verdict['supported'])
        relation = projection.project_relation('contradicts', 'wev-' + 'b' * 24,
                                               'wev-' + 'a' * 24)
        self.assertEqual(relation['raw_type'], 'contradicts')
        self.assertEqual(relation['relation_type'], 'conflicts')
        self.assertEqual(relation['origin'], 'projected')

    def test_unknown_edge_type_is_never_guessed_into_the_nearest_type(self):
        verdict = projection.relation_type_for('retrieved_from_v2')
        self.assertIsNone(verdict['relation_type'])
        self.assertFalse(verdict['supported'])
        self.assertEqual(verdict['unsupported_reason'], 'TYPE_NOT_IN_WIKI_VOCABULARY')
        self.assertEqual(verdict['raw_type'], 'retrieved_from_v2')
        relation = projection.project_relation('retrieved_from_v2', 'wev-' + 'a' * 24,
                                               'wev-' + 'b' * 24)
        self.assertIsNone(relation['relation_type'])
        self.assertFalse(relation['supported'])
        self.assertEqual(relation['unsupported_reason'], 'TYPE_NOT_IN_WIKI_VOCABULARY')
        self.assertEqual(relation['raw_type'], 'retrieved_from_v2')
        # 未知类型的边照常出现在图谱里（3.4 规则 4），只是不参与派生与"已核验"判定
        self.assertIsNotNone(relation['basis'])

    def test_unsupported_reason_iff_not_supported_iff_no_relation_type(self):
        # 迁移的两条 CHECK：supported = (relation_type IS NOT NULL)，
        # supported = (unsupported_reason IS NULL)
        for raw in projection.EDGE_TYPE_VOCABULARY + ('mystery', ''):
            relation = projection.project_relation(raw, 'wev-' + 'a' * 24, 'wev-' + 'b' * 24)
            self.assertEqual(relation['supported'], relation['relation_type'] is not None, raw)
            self.assertEqual(relation['supported'], relation['unsupported_reason'] is None, raw)

    def test_origin_matches_the_vocabulary_class(self):
        projected = projection.project_relation('derived_from', 'wev-' + 'a' * 24,
                                                'wev-' + 'b' * 24)
        self.assertEqual(projected['origin'], 'projected')
        self.assertIsNotNone(projected['raw_type'], 'projected 必须保留 raw_type')
        derived = projection.project_derived_relation(
            'measures', 'wiki-' + 'a' * 24, 'wiki-' + 'b' * 24,
            inputs=[{'node_type': 'measurement', 'fields': ['entity_id', 'metric']}])
        self.assertEqual(derived['origin'], 'wiki_derived')
        self.assertIsNone(derived['raw_type'], '派生关系不来自任何边，raw_type 必须为 NULL')
        self.assertIsNotNone(derived['rule_version'])
        self.assertEqual(derived['derivation']['rule_version'], derived['rule_version'])
        self.assertIn('inputs', derived['derivation'])

    def test_derived_type_used_as_a_raw_edge_type_is_refused_not_mapped(self):
        verdict = projection.relation_type_for('measures')
        self.assertIsNone(verdict['relation_type'])
        self.assertFalse(verdict['supported'])

    def test_derived_relation_must_carry_its_inputs(self):
        with self.assertRaises(projection.ProjectionError) as caught:
            projection.project_derived_relation('threshold_of', 'wiki-' + 'a' * 24,
                                                'wiki-' + 'b' * 24, inputs=[])
        self.assertEqual(caught.exception.code, 'WIKI_DERIVATION_INPUTS_EMPTY')

    def test_every_projected_relation_is_marked_provenance_incomplete(self):
        # 边的 reasons / confidence / binding_type 确实没落库 -> 凡是依赖它们的字段都必须标记缺失
        relation = projection.project_relation('supports', 'wev-' + 'a' * 24, 'wev-' + 'b' * 24,
                                               edge_id=1, run_id=RUN)
        marker = relation['provenance_incomplete']
        self.assertTrue(marker['applies'])
        self.assertEqual(sorted(marker['missing_fields']),
                         ['edge.binding_type', 'edge.confidence', 'edge.evidence'])
        self.assertEqual(marker['reason_code'], 'EDGE_COLUMNS_ABSENT_IN_DEPLOYED_SCHEMA')
        self.assertFalse(marker['recoverable'], '不得推测补全')
        # 反面对照：派生关系不来自边，就没有这个缺口
        derived = projection.project_derived_relation(
            'about', 'wiki-' + 'a' * 24, 'wiki-' + 'b' * 24, inputs=[{'fields': ['metric']}])
        self.assertEqual(derived['provenance_incomplete'], {'applies': False})

    def test_undirected_types_are_canonicalised_to_one_row(self):
        for symmetric_type in ('entity_match', 'unit_equivalent', 'time_overlap', 'conflicts',
                               'contradicts'):
            forward = projection.project_relation(symmetric_type, 'wev-' + 'a' * 24,
                                                 'wev-' + 'b' * 24)
            backward = projection.project_relation(symmetric_type, 'wev-' + 'b' * 24,
                                                   'wev-' + 'a' * 24)
            self.assertTrue(forward['is_symmetric'], symmetric_type)
            self.assertEqual((forward['src_kind'], forward['src_id'], forward['dst_kind'],
                              forward['dst_id']),
                             (backward['src_kind'], backward['src_id'], backward['dst_kind'],
                              backward['dst_id']),
                             '%s 必须存成同一行（3.2 无向类型）' % symmetric_type)
            self.assertLessEqual((forward['src_kind'], forward['src_id']),
                                 (forward['dst_kind'], forward['dst_id']))

    def test_directed_types_keep_their_exact_orientation(self):
        # WIKI_API.md §11.1：库里是 Claim supports Chunk，supports 有向，方向必须保留
        relation = projection.project_relation('supports', 'wev-' + 'b' * 24, 'wev-' + 'a' * 24)
        self.assertFalse(relation['is_symmetric'])
        self.assertEqual(relation['src_id'], 'wev-' + 'b' * 24)
        self.assertEqual(relation['dst_id'], 'wev-' + 'a' * 24)

    def test_a_relation_needs_ids_the_migration_admits(self):
        # wiki_relations.src_id 的 CHECK 是 ^(wiki|wassert|wev)-[0-9a-f]{24}$
        with self.assertRaises(projection.ProjectionError) as caught:
            projection.project_relation('derived_from', 'ev-adc9f4f247004859',
                                        'wev-' + 'b' * 24)
        self.assertEqual(caught.exception.code, 'WIKI_RELATION_ENDPOINT_FORMAT')

    def test_endpoint_kind_comes_from_the_id_prefix(self):
        self.assertEqual(projection.endpoint_kind('wiki-' + 'a' * 24), 'entry')
        self.assertEqual(projection.endpoint_kind('wassert-' + 'a' * 24), 'assertion')
        self.assertEqual(projection.endpoint_kind('wev-' + 'a' * 24), 'evidence_ref')
        self.assertIsNone(projection.endpoint_kind('ev-' + 'a' * 16))
        self.assertIsNone(projection.endpoint_kind(None))

    def test_a_self_loop_is_refused(self):
        with self.assertRaises(projection.ProjectionError) as caught:
            projection.project_relation('supports', 'wev-' + 'a' * 24, 'wev-' + 'a' * 24)
        self.assertEqual(caught.exception.code, 'WIKI_RELATION_SELF_LOOP')

    def test_a_raw_type_longer_than_the_column_is_clipped_and_the_original_kept(self):
        long_type = 'a_very_long_edge_type_that_exceeds_thirty_two_chars'
        relation = projection.project_relation(long_type, 'wev-' + 'a' * 24, 'wev-' + 'b' * 24)
        self.assertEqual(len(relation['raw_type']), projection.RAW_TYPE_MAX)
        self.assertEqual(relation['basis']['raw_type_clipped_from'], long_type)

    def test_the_real_edge_words_of_this_database_all_map(self):
        # 实测：derived_from 2054 / supports 682 / retrieved_from 375（共 3111 行），全是旧词表
        for raw in ('derived_from', 'supports', 'retrieved_from'):
            relation = projection.project_relation(raw, 'wev-' + 'a' * 24, 'wev-' + 'b' * 24)
            self.assertTrue(relation['supported'], raw)
            self.assertEqual(relation['relation_type'], raw, raw)
            self.assertEqual(relation['raw_type'], raw, raw)

    def test_the_five_new_edge_words_have_no_rows_but_are_still_recognised(self):
        # 迁移宣告但本库 0 行的 5 个新词表：收录但不得声称已验证
        for raw in ('entity_match', 'version_scope', 'unit_equivalent', 'time_overlap',
                    'conflicts'):
            self.assertTrue(projection.relation_type_for(raw)['supported'], raw)


# ================================================================ 4. 冲突双路径归一

class ConflictProjectionTest(unittest.TestCase):
    """两条互不相通的来源路径（WIKI_DOMAIN.md §3.5 / WIKI_API.md §7.4）。"""

    def test_industrial_path_keeps_both_original_values(self):
        conflict = projection.conflict_from_industrial_row(conflict_row())
        self.assertEqual(conflict['source_path'], 'industrial_table')
        self.assertEqual(conflict['left']['value'], '870')   # production
        self.assertEqual(conflict['right']['value'], '850')  # report_raw
        self.assertEqual(conflict['right']['unit'], 'degC')  # report_unit
        self.assertEqual(conflict['left']['unit'], 'degC',
                         '生产侧的单位是比对单位（normalized_values 只给 report_unit）')
        self.assertEqual(conflict['left']['refs'],
                         [{'kind': 'sql_measurement', 'measurement_id': 'M001-1'}])
        self.assertEqual(conflict['right']['refs'],
                         [{'fact_id': 'ifact-02dfc4600b7beabde73fe3ea'}])
        self.assertEqual(conflict['comparison_context']['method'], 'core')
        self.assertEqual(conflict['conflict_id'], 'conf-dac12deffa0f4caab917')
        self.assertTrue(projection.HEX64.match(conflict['comparison_key']))

    def test_the_three_state_resolution_survives_and_is_normalised(self):
        cases = [
            ('unresolved', 'unresolved'),
            ('needs_review', 'needs_review'),
            ('resolved_equivalent', 'resolved_equivalent'),
            ('resolved_by_registered_authority', 'resolved_by_authority'),
            ('CONFLICT_UNRESOLVED', 'unresolved'),
        ]
        for raw, expected in cases:
            row = conflict_row(resolution_status=raw,
                               payload=dict(PAYLOADS['ConflictRecord'], resolution_status=raw))
            conflict = projection.conflict_from_industrial_row(row)
            self.assertEqual(conflict['resolution'], expected, raw)
            self.assertEqual(conflict['raw_resolution_status'], raw)

    def test_a_needs_review_record_is_not_an_undecided_conflict(self):
        # WIKI_DOMAIN.md §1.4：175 行里只有 35 行是 unresolved，另 140 行不得读成"存在未裁决冲突"
        needs_review = conflict_row(
            conflict_id='conf-db4974a290e34c279095', conflict_type='version_overlap',
            resolution_status='needs_review',
            payload=dict(PAYLOADS['ConflictRecord'], conflict_type='version_overlap',
                         resolution_status='needs_review',
                         rule_id='MULTIPLE_APPLICABLE_REVISIONS'))
        equivalent = conflict_row(
            conflict_id='conf-9349df43aeff4ee4911f', conflict_type='unit_equivalence',
            resolution_status='resolved_equivalent',
            payload=dict(PAYLOADS['ConflictRecord'], conflict_type='unit_equivalence',
                         resolution_status='resolved_equivalent',
                         rule_id='DECIMAL_UNIT_CONVERSION_EQUAL'))
        unresolved = conflict_row()
        self.assertFalse(projection.is_unresolved_conflict(
            projection.conflict_from_industrial_row(needs_review)))
        self.assertFalse(projection.is_unresolved_conflict(
            projection.conflict_from_industrial_row(equivalent)))
        self.assertTrue(projection.is_unresolved_conflict(
            projection.conflict_from_industrial_row(unresolved)))
        self.assertFalse(projection.industrial_conflicts_unresolved(needs_review))
        self.assertFalse(projection.industrial_conflicts_unresolved(equivalent))
        self.assertTrue(projection.industrial_conflicts_unresolved(unresolved))

    def test_selected_is_null_when_the_column_is_a_json_null_literal(self):
        # 实测：175/175 行的 selected_fact_ref 是 JSON 字面量 null，WHERE IS NOT NULL 全部命中
        conflict = projection.conflict_from_industrial_row(conflict_row())
        self.assertIsNone(conflict['selected'], 'selected_fact_ref=null 时不得推测选了哪一侧')
        for json_null in (None, 'null', '', [], {}):
            self.assertTrue(projection.jsonb_absent(json_null), repr(json_null))
            conflict = projection.conflict_from_industrial_row(
                conflict_row(selected_fact_ref=json_null))
            self.assertIsNone(conflict['selected'], repr(json_null))

    def test_a_real_selected_ref_is_kept(self):
        selected = {'fact_id': 'ifact-08658ba4e2b71b7fc7d65878'}
        conflict = projection.conflict_from_industrial_row(conflict_row(selected_fact_ref=selected))
        self.assertEqual(conflict['selected'], selected)

    def test_jsonb_null_is_absent_for_every_conflict_jsonb_column(self):
        # 同类字段（normalized_values / left_fact_refs / right_fact_refs）都按此处理
        self.assertTrue(projection.jsonb_absent(None))
        self.assertTrue(projection.jsonb_absent('null'))
        self.assertFalse(projection.jsonb_absent('"null"'))
        self.assertFalse(projection.jsonb_absent('850'))
        self.assertFalse(projection.jsonb_present(None))
        self.assertTrue(projection.jsonb_present({'production': '870'}))
        row = conflict_row(left_fact_refs=None, right_fact_refs='null', normalized_values=None,
                           payload={'conflict_id': 'conf-x'})
        conflict = projection.conflict_from_industrial_row(row)
        self.assertEqual(conflict['left']['refs'], [])
        self.assertEqual(conflict['right']['refs'], [])
        self.assertEqual(conflict['normalized_values'], {})
        self.assertEqual(conflict['left']['value'], None)
        self.assertTrue(conflict['provenance_incomplete']['applies'])

    def test_row_columns_win_over_payload_and_the_payload_fills_the_gaps(self):
        row = conflict_row(rule_version=None, reason=None, metric='holding_temperature',
                           payload=dict(PAYLOADS['ConflictRecord'], metric='other_metric'))
        conflict = projection.conflict_from_industrial_row(row)
        self.assertEqual(conflict['metric'], 'holding_temperature',
                         '被索引的列值优先，不静默用 payload 覆盖')
        self.assertEqual(conflict['rule_version'], 'industrial-rules-v1', '列缺失时由 payload 补')
        self.assertEqual(conflict['reason'], 'NO_REGISTERED_AUTHORITY_RULE')

    def test_cross_source_path_is_normalised_into_the_same_shape(self):
        # 本库 cross_source_agent 事件数 = 0，这一段只能用合成数据测（WIKI_API.md §11.3）
        conflict = projection.conflict_from_cross_source(
            cross_source_record(), run_id='run-cross-1', event_id='evt-1',
            event_type='answer.completed')
        self.assertEqual(conflict['source_path'], 'cross_source_trace')
        self.assertEqual(conflict['source_locator']['event_type'], 'answer.completed')
        self.assertEqual(conflict['source_locator']['output_summary_key'],
                         'cross_source_result.conflicts')
        self.assertEqual(conflict['left']['value'], '870')
        self.assertEqual(conflict['left']['normalized_value'], '870.000')
        self.assertEqual(conflict['left']['source_sha256'], IND_SHA)
        self.assertEqual(conflict['left']['record_scope'], 'M001-1')
        self.assertEqual(conflict['right']['value'], '850')
        self.assertEqual(conflict['resolution'], 'unresolved')
        self.assertIsNone(conflict['selected'])
        self.assertEqual(conflict['provenance_incomplete'], {'applies': False})

    def test_the_two_paths_produce_the_same_keys(self):
        industrial = projection.conflict_from_industrial_row(conflict_row())
        cross = projection.conflict_from_cross_source(cross_source_record())
        self.assertEqual(set(industrial), set(cross),
                         '两条路径必须归一成同一个对象形状（WIKI_API.md §7.4）')
        for key in ('conflict_id', 'source_path', 'source_locator', 'comparison_key', 'entity_key',
                    'metric', 'conflict_type', 'resolution', 'rule_id', 'authority_rule_id',
                    'left', 'right', 'selected', 'provenance_incomplete'):
            self.assertIn(key, industrial, key)
            self.assertIn(key, cross, key)
        for side in ('left', 'right'):
            self.assertEqual(set(industrial[side]), set(cross[side]), side)
            for key in ('node_id', 'value', 'unit', 'normalized_value', 'normalized_unit',
                        'source_sha256', 'version', 'record_scope', 'refs'):
                self.assertIn(key, industrial[side], '%s.%s' % (side, key))

    def test_cross_source_result_split_distinguishes_missing_from_empty(self):
        missing = projection.cross_source_result_conflicts(
            {'status': 'COMPLETED', 'run_id': 'run-1'})
        self.assertFalse(missing['present'])
        self.assertEqual(missing['conflicts'], [])
        empty = projection.cross_source_result_conflicts(
            {'cross_source_result': {'conflicts': [], 'unresolved_conflicts': []}})
        self.assertTrue(empty['present'])
        self.assertEqual(empty['declared_count'], 0)
        one = projection.cross_source_result_conflicts(
            {'cross_source_result': {'conflicts': [cross_source_record()],
                                     'unresolved_conflicts': [cross_source_record()],
                                     'status': 'abstained', 'error_code': 'CONFLICT_UNRESOLVED'}})
        self.assertTrue(one['present'])
        self.assertEqual(len(one['conflicts']), 1)
        self.assertEqual(len(one['unresolved_conflicts']), 1)
        self.assertEqual(one['declared_unresolved_count'], 1)
        self.assertEqual(one['status'], 'abstained')

    def test_unresolved_conflicts_are_recomputed_not_trusted(self):
        # 声明为 unresolved 的列表里若混进 needs_review，必须以归一化后的 resolution 为准
        record = cross_source_record(resolution_status='resolved_by_registered_authority',
                                     selected_node_id='n-' + '1' * 20)
        split = projection.cross_source_result_conflicts(
            {'cross_source_result': {'conflicts': [record],
                                     'unresolved_conflicts': [record]}})
        self.assertEqual(split['declared_unresolved_count'], 1)
        self.assertEqual(split['unresolved_conflicts'], [],
                         'resolved_by_authority 归一为 resolved_by_authority，不是未裁决')

    def test_both_paths_for_one_run_stay_labelled(self):
        split = projection.conflicts_for_run(
            industrial_rows=[conflict_row(),
                             conflict_row(conflict_id='conf-b', resolution_status='needs_review',
                                          conflict_type='measurement_context',
                                          payload=dict(PAYLOADS['ConflictRecord'],
                                                       resolution_status='needs_review',
                                                       conflict_type='measurement_context'))],
            output_summaries=[{'cross_source_result': {'conflicts': [cross_source_record()],
                                                       'unresolved_conflicts': []}}],
            run_id=RUN)
        self.assertEqual(split['counts']['industrial'], 2)
        self.assertEqual(split['counts']['cross_source'], 1)
        self.assertEqual(split['counts']['unresolved'], 2)
        self.assertEqual(split['counts']['needs_review'], 1)
        self.assertTrue(split['has_unresolved_conflict'])
        self.assertEqual({c['source_path'] for c in split['conflicts']},
                         {'industrial_table', 'cross_source_trace'})
        self.assertEqual([c['source_path'] for c in split['unresolved_conflicts']],
                         ['industrial_table', 'cross_source_trace'])

    def test_a_run_without_any_conflict_source_reports_zero_not_an_error(self):
        split = projection.conflicts_for_run(industrial_rows=[], output_summaries=[], run_id=RUN)
        self.assertEqual(split['conflicts'], [])
        self.assertFalse(split['has_unresolved_conflict'])
        self.assertEqual(split['counts'],
                         {'industrial': 0, 'cross_source': 0, 'unresolved': 0, 'needs_review': 0,
                          'resolved_equivalent': 0})

    def test_a_conflict_record_without_an_id_is_refused(self):
        with self.assertRaises(projection.ProjectionError) as caught:
            projection.conflict_from_industrial_row({'payload': {}})
        self.assertEqual(caught.exception.code, 'WIKI_CONFLICT_ID_MISSING')
        with self.assertRaises(projection.ProjectionError):
            projection.conflict_from_cross_source({})


# ================================================================ 5. 核验状态与记账

class VerificationTest(unittest.TestCase):
    """核验语义不得简化（WIKI_DOMAIN.md §1.7 / §5.2，WIKI_API.md §7.2-7.3）。"""

    def test_semantic_support_is_always_not_independently_verified(self):
        # agent.py:238/:629 的固定字面量，Wiki 不得改写成"已核验"
        accounting = projection.build_verification_accounting()
        self.assertEqual(accounting['semantic_support'],
                         {'checked': False,
                          'full_semantic_entailment': 'not_independently_verified'})
        for key in ('source_integrity', 'entity_binding', 'rule_verification', 'semantic_support',
                    'source_authority'):
            self.assertIn(key, accounting, '五项分别记账，缺一不可')

    def test_source_authority_is_never_auto_activated(self):
        self.assertEqual(projection.build_verification_accounting()['source_authority'],
                         {'checked': False, 'authority_rule_id': None})
        self.assertTrue(projection.build_verification_accounting(
            authority_rule_id='rule-1')['source_authority']['checked'])

    def test_entity_binding_is_blank_when_the_binding_was_never_persisted(self):
        # 实测：2760 个节点没有一个带 payload.binding
        accounting = projection.build_verification_accounting()
        self.assertEqual(accounting['entity_binding'], {'checked': False},
                         '空白必须如实标为"未记"，不得当作通过')

    def test_warned_never_maps_to_verified(self):
        # WIKI_API.md §11.1 的关键区分样本：claims.status='warned'
        record = projection.verification_for_claim(
            {'status': 'warned',
             'verification': {'excerpt_matches': True, 'numeric_support': True,
                              'semantic_entailment': 'not_independently_verified'}},
            run_status='COMPLETED')
        self.assertEqual(record['status'], 'unverified')
        self.assertEqual(record['full_semantic_entailment'], 'not_independently_verified')
        self.assertFalse(record['accounting']['semantic_support']['checked'])

    def test_the_time_scope_only_contract_is_carried_through_verbatim(self):
        # claims.status='verified' ≠ 事实已证实：time_scope_only 下 semantically_verified=false
        record = projection.verification_for_claim(
            {'status': 'verified',
             'verification': {'verification_contract': 'time_scope_only',
                              'semantically_verified': False, 'validator_findings': []}},
            run_status='COMPLETED')
        self.assertEqual(record['status'], 'verified')
        self.assertEqual(record['verification_contract'], 'time_scope_only')
        self.assertFalse(record['accounting']['rule_verification']['semantically_verified'])
        self.assertEqual(record['accounting']['rule_verification']['verification_contract'],
                         'time_scope_only')

    def test_the_ast_contract_is_marked_as_such_and_semantic_support_still_unchecked(self):
        # WIKI_API.md §11.2 的关键区分样本：retail_metric_ast_contract + semantically_verified=true
        record = projection.verification_for_claim(
            {'status': 'verified',
             'verification': {'verification_contract': 'retail_metric_ast_contract',
                              'semantically_verified': True, 'sql_hash_matches': True,
                              'validator_findings': []}},
            run_status='COMPLETED')
        self.assertEqual(record['status'], 'verified')
        self.assertTrue(record['accounting']['rule_verification']['semantically_verified'])
        self.assertFalse(record['accounting']['semantic_support']['checked'],
                         'semantic_support 恒为 checked:false')

    def test_the_two_samples_are_not_displayed_as_the_same_verified(self):
        warned = projection.verification_for_claim(
            {'status': 'warned', 'verification': {}}, run_status='COMPLETED')
        ast = projection.verification_for_claim(
            {'status': 'verified',
             'verification': {'verification_contract': 'retail_metric_ast_contract',
                              'semantically_verified': True}}, run_status='COMPLETED')
        self.assertNotEqual(warned['status'], ast['status'])
        self.assertNotEqual(warned['accounting']['rule_verification'],
                            ast['accounting']['rule_verification'])

    def test_insufficient_evidence_maps_to_insufficient(self):
        record = projection.verification_for_claim(
            {'status': 'insufficient_evidence', 'verification': {}}, run_status='ABSTAINED')
        self.assertEqual(record['status'], 'insufficient')

    def test_industrial_check_agreement_maps_without_flattening(self):
        consistent = projection.verification_for_check(PAYLOADS['VerificationCheck'])
        self.assertEqual(consistent['status'], 'verified')
        self.assertEqual(consistent['rule_version'], 'industrial-rules-v1')
        self.assertEqual(consistent['coverage']['metric'], 'holding_temperature')
        self.assertEqual(consistent['issues'][0]['kind'], 'version_overlap')
        unresolved = projection.verification_for_check(
            dict(PAYLOADS['VerificationCheck'], agreement='unresolved'))
        self.assertEqual(unresolved['status'], 'conflicted')
        needs_review = projection.verification_for_check(
            dict(PAYLOADS['VerificationCheck'], agreement='needs_review'))
        self.assertEqual(needs_review['status'], 'unverified',
                         'needs_review 不得读成 conflicted')

    def test_a_check_never_claims_semantic_verification(self):
        record = projection.verification_for_check(PAYLOADS['VerificationCheck'])
        self.assertFalse(record['semantically_verified'])
        self.assertEqual(record['full_semantic_entailment'], 'not_independently_verified')
        self.assertFalse(record['binding_persisted'])


# ================================================================ 6. provenance_incomplete

class ProvenanceIncompleteTest(unittest.TestCase):
    """WIKI_API.md §6：必然存在、不得推测补全、缺失要可见。"""

    def test_applies_false_is_an_object_and_never_a_json_null(self):
        # wiki_relations.provenance_incomplete 是 NOT NULL 且必须带 applies
        marker = projection.provenance_incomplete(applies=False)
        self.assertEqual(marker, {'applies': False})
        self.assertIn('applies', marker)
        self.assertIsInstance(marker, dict)

    def test_applies_true_carries_missing_fields_and_is_not_recoverable(self):
        marker = projection.provenance_incomplete(
            applies=True, missing_fields=['edge.confidence', 'edge.evidence'],
            reason_code=projection.REASON_EDGE_COLUMNS_ABSENT)
        self.assertEqual(marker['missing_fields'], ['edge.confidence', 'edge.evidence'])
        self.assertEqual(marker['reason_code'], 'EDGE_COLUMNS_ABSENT_IN_DEPLOYED_SCHEMA')
        self.assertFalse(marker['recoverable'])

    def test_missing_edge_columns_is_checked_against_the_live_shape(self):
        deployed = ['edge_id', 'run_id', 'src_evidence_id', 'dst_evidence_id', 'edge_type',
                    'weight']
        self.assertEqual(sorted(projection.missing_edge_columns(deployed)),
                         ['binding_type', 'confidence', 'evidence'])
        declared = deployed + ['binding_type', 'confidence', 'evidence']
        self.assertEqual(projection.missing_edge_columns(declared), [])

    def test_a_node_without_binding_or_snapshot_is_marked_incomplete(self):
        ref = projection.project_evidence_ref(
            SPACE, node('Chunk', {'quote': 'x', 'title': 't', 'chunk_id': 'c'}))
        marker = ref['provenance_incomplete']
        self.assertTrue(marker['applies'])
        self.assertIn('node.binding', marker['missing_fields'])
        self.assertIn('node.source_sha256', marker['missing_fields'])
        self.assertEqual(marker['reason_code'], 'NODE_BINDING_NOT_PERSISTED')
        self.assertFalse(marker['recoverable'])

    def test_a_node_with_both_binding_and_snapshot_is_complete(self):
        node_row = node('measurement', {
            'source_kind': 'sql', 'source_ref': 'demo_retail', 'measurement_id': 'M1',
            'value': '1', 'unit': 'count', 'sql_hash': SQL_HASH,
            'binding': {'entity_id': 'A102', 'version': 'A102-V2', 'unit': 'degC',
                        'source_ref': 'demo_retail', 'source_sha256': IND_SHA},
            'source_sha256': IND_SHA, 'source_snapshot_id': SNAP})
        ref = projection.project_evidence_ref(SPACE, node_row)
        self.assertFalse(ref['provenance_incomplete']['applies'])
        self.assertTrue(ref['binding_persisted'])
        self.assertEqual(ref['provenance_incomplete'], {'applies': False})

    def test_missing_and_unsupported_stay_two_different_things(self):
        # WIKI_API.md §6.5：字段存在过但没存下来 vs 字段存下来了但 Wiki 不认识
        unsupported = projection.project_evidence_ref(
            SPACE, node('WeirdThing', {'a': 1}, evidence_id='ev-' + '7' * 16))
        self.assertEqual(unsupported['unsupported_reason'], 'TYPE_NOT_IN_WIKI_VOCABULARY')
        self.assertFalse(unsupported['supported'])
        # 「不支持」= 原值在、只是不认识：raw_type 保留，node_type 为空
        self.assertEqual(unsupported['raw_node_type'], 'WeirdThing')
        self.assertIsNone(unsupported['node_type'])
        # 「缺失」= 字段存在过但没落库：binding 与快照都要如实标出来
        self.assertIn('node.binding', unsupported['provenance_incomplete']['missing_fields'])
        self.assertTrue(unsupported['provenance_incomplete']['applies'])
        # 两件事互不替代：未知类型仍是一条可用的证据引用
        self.assertTrue(projection.validate_id('evidence_ref', unsupported['evidence_ref_id']))


# ================================================================ 7. 整 run 投影

class RunProjectionTest(unittest.TestCase):
    """把三类身份、关系与未知类型放在同一次真实形状的投影里跑一遍。"""

    def setUp(self):
        # run-894b0785725c45f3 的真实形状：5 × Chunk、1 × Document、1 × Claim，
        # 边为 5 条 retrieved_from + 1 条 supports（WIKI_API.md §11.1）
        self.nodes = [
            node('Chunk', PAYLOADS['Chunk'], evidence_id='ev-' + '1' * 16, run_id=RAG_RUN,
                 ref_key='chk-f4227338f4e1'),
            node('Chunk', PAYLOADS['ChunkEmptyQuote'], evidence_id='ev-' + '2' * 16,
                 run_id=RAG_RUN, ref_key='chk-4c358eb26689'),
            node('Document', PAYLOADS['Document'], evidence_id='ev-' + '3' * 16, run_id=RAG_RUN,
                 ref_key='doc-ebdef694fdd4'),
            node('Claim', {'text': '无线降噪耳机 Pro 在降噪开启时续航约为 38 小时。 [1]'},
                 evidence_id='ev-' + '4' * 16, run_id=RAG_RUN, ref_key='claim-80ac4ad77526407b'),
            node('WeirdThing', {'a': 1}, evidence_id='ev-' + '5' * 16, run_id=RAG_RUN),
        ]
        self.edges = [
            {'edge_id': 1, 'run_id': RAG_RUN, 'src_evidence_id': 'ev-' + '1' * 16,
             'dst_evidence_id': 'ev-' + '3' * 16, 'edge_type': 'retrieved_from', 'weight': None},
            {'edge_id': 2, 'run_id': RAG_RUN, 'src_evidence_id': 'ev-' + '2' * 16,
             'dst_evidence_id': 'ev-' + '3' * 16, 'edge_type': 'retrieved_from', 'weight': None},
            {'edge_id': 3, 'run_id': RAG_RUN, 'src_evidence_id': 'ev-' + '4' * 16,
             'dst_evidence_id': 'ev-' + '1' * 16, 'edge_type': 'supports', 'weight': None},
            {'edge_id': 4, 'run_id': RAG_RUN, 'src_evidence_id': 'ev-' + '4' * 16,
             'dst_evidence_id': 'ev-' + '9' * 16, 'edge_type': 'mystery_edge', 'weight': None},
        ]

    def test_the_whole_run_projects_and_every_relation_endpoint_resolves(self):
        out = projection.project_run_relations(self.nodes, self.edges, SPACE, run_id=RAG_RUN)
        self.assertEqual(out['counts']['evidence_refs'], 5)
        self.assertEqual(out['counts']['relations'], 3)
        self.assertEqual(out['counts']['dropped_edges'], 1,
                         '端点没被投影的边要带原因丢弃，不能用 schema 认不出的 id 发出去')
        self.assertEqual(out['dropped_edges'][0]['reason_code'],
                         'RELATION_ENDPOINT_NOT_PROJECTED')
        self.assertEqual(out['dropped_edges'][0]['edge_type'], 'mystery_edge')
        for relation in out['relations']:
            self.assertTrue(projection.RELATION_ENDPOINT.match(relation['src_id']))
            self.assertTrue(projection.RELATION_ENDPOINT.match(relation['dst_id']))
            self.assertTrue(relation['provenance_incomplete']['applies'])
            # 原始出现记录仍然可追（4.2 规则 2）
            self.assertTrue(relation['src_evidence_id'].startswith('ev-'))

    def test_the_claim_supports_chunk_direction_is_preserved(self):
        out = projection.project_run_relations(self.nodes, self.edges, SPACE, run_id=RAG_RUN)
        supports = [rel for rel in out['relations'] if rel['relation_type'] == 'supports']
        self.assertEqual(len(supports), 1)
        by_ref = {ref['evidence_id']: ref['evidence_ref_id'] for ref in out['evidence_refs']}
        self.assertEqual(supports[0]['src_id'], by_ref['ev-' + '4' * 16])
        self.assertEqual(supports[0]['dst_id'], by_ref['ev-' + '1' * 16])

    def test_the_two_chunks_of_one_document_get_two_references(self):
        refs = [ref for ref in projection.project_run_relations(
            self.nodes, self.edges, SPACE, run_id=RAG_RUN)['evidence_refs']
            if ref['node_type'] == 'doc_block']
        self.assertEqual(len(refs), 2)
        self.assertNotEqual(refs[0]['evidence_ref_id'], refs[1]['evidence_ref_id'],
                            '不同内容片段 = 不同证据身份（4.2）')

    def test_projection_summary_counts_both_kinds_of_gap(self):
        summary = projection.projection_summary(self.nodes, self.edges, space_id=SPACE,
                                                namespace='demo-kb')
        self.assertFalse(summary['no_evidence'])
        self.assertEqual(summary['node_count'], 5)
        self.assertEqual(summary['edge_count'], 4)
        self.assertEqual(summary['unsupported_node_types'], ['WeirdThing'])
        self.assertEqual(summary['unsupported_edge_types'], ['mystery_edge'])
        self.assertEqual(summary['type_not_in_wiki_vocabulary'], 2)
        self.assertEqual(summary['entry_type_counts'], {'conclusion': 1})
        self.assertEqual(summary['relation_type_counts'], {'retrieved_from': 2, 'supports': 1})

    def test_no_evidence_is_reported_as_a_fact_of_the_data_not_as_a_failure(self):
        # WIKI_API.md §8.2：WIKI_NO_EVIDENCE（读成功但没证据）与 WIKI_LOAD_FAILED（读失败）必须区分
        summary = projection.projection_summary([], [], space_id=SPACE)
        self.assertTrue(summary['no_evidence'])
        self.assertEqual(summary['node_count'], 0)
        self.assertNotIn('error_code', summary)

    def test_projection_never_mutates_its_input(self):
        import copy
        nodes, edges = copy.deepcopy(self.nodes), copy.deepcopy(self.edges)
        projection.project_run_relations(self.nodes, self.edges, SPACE, run_id=RAG_RUN)
        self.assertEqual(self.nodes, nodes)
        self.assertEqual(self.edges, edges)


# ================================================================ 8. 派生关系计划

class DerivedRelationPlanTest(unittest.TestCase):
    """3.2 #14-#21 的推导依据（只给计划 + 明确标记延后，不假装已经落库）。"""

    def industrial_nodes(self):
        return [
            node('DocumentFact', PAYLOADS['DocumentFact'], evidence_id='ev-' + '1' * 16,
                 ref_key='ifact-08658ba4e2b71b7fc7d65878'),
            node('SQLRow', PAYLOADS['SQLRow'], evidence_id='ev-' + '2' * 16, ref_key=SQL_HASH),
            node('VerificationCheck', PAYLOADS['VerificationCheck'], evidence_id='ev-' + '3' * 16,
                 ref_key='M004-1'),
            node('Claim', PAYLOADS['ClaimIndustrial'], evidence_id='ev-' + '4' * 16,
                 ref_key='claim-1'),
        ]

    def test_measures_threshold_of_and_justified_by_are_planned_from_real_fields(self):
        plans = projection.plan_wiki_derived_relations(self.industrial_nodes(), space_id=SPACE,
                                                       namespace='industrial-demo')
        self.assertIn('measures', plans['counts'])
        self.assertIn('threshold_of', plans['counts'])
        self.assertIn('justified_by', plans['counts'])
        for plan in plans['plans']:
            self.assertTrue(plan['deferred'])
            self.assertEqual(plan['deferred_reason'],
                             'ENTRY_AND_ASSERTION_IDS_ASSIGNED_AT_BUILD_TIME')
            self.assertEqual(plan['rule_version'], projection.DERIVATION_RULE_VERSION)
            self.assertEqual(plan['derivation']['rule_version'], plan['rule_version'])
            self.assertTrue(plan['derivation']['inputs'])
            self.assertTrue(plan['basis_note'])

    def test_supersedes_uses_the_later_valid_from_and_never_the_revision_string(self):
        older = dict(PAYLOADS['DocumentFact'], fact_id='ifact-old', source_revision='A102-V2',
                     valid_from='2026-09-01T00:00:00+08:00', valid_to=None)
        newer = dict(PAYLOADS['DocumentFact'], fact_id='ifact-new', source_revision='A102-V3',
                     valid_from='2026-09-15T00:00:00+08:00',
                     valid_to='2026-09-20T00:00:00+08:00')
        plans = projection.plan_wiki_derived_relations([
            node('DocumentFact', older, evidence_id='ev-' + '1' * 16, ref_key='ifact-old'),
            node('DocumentFact', newer, evidence_id='ev-' + '2' * 16, ref_key='ifact-new'),
        ], space_id=SPACE)
        supersedes = [plan for plan in plans['plans'] if plan['relation_type'] == 'supersedes']
        self.assertEqual(len(supersedes), 1)
        self.assertEqual(supersedes[0]['src_key'], ('norm', 'ifact-new'))
        self.assertEqual(supersedes[0]['dst_key'], ('norm', 'ifact-old'))

    def test_a_document_fact_measurement_is_not_a_requirement(self):
        fact = dict(PAYLOADS['DocumentFact'], fact_kind='measurement', measurement_id='M004-1')
        plans = projection.plan_wiki_derived_relations(
            [node('DocumentFact', fact, evidence_id='ev-' + '1' * 16)], space_id=SPACE)
        self.assertNotIn('threshold_of', plans['counts'],
                         'fact_kind=measurement 不是规范，不得推出 threshold_of')

    def test_unsupported_node_types_take_no_part_in_derivation(self):
        # 3.4 规则 4：未知类型的节点不参与 Wiki 派生关系的推导
        plans = projection.plan_wiki_derived_relations([
            node('WeirdMetric', {'metric': 'holding_temperature', 'entity_id': 'A102',
                                 'value': '1'},
                 evidence_id='ev-' + '1' * 16),
        ], space_id=SPACE)
        self.assertEqual(plans['plans'], [])
        self.assertIsNone(plans['restates_skipped_reason'])

    def test_restates_needs_two_runs_and_a_space(self):
        first = dict(PAYLOADS['SQLRow'])
        plans = projection.plan_wiki_derived_relations([
            node('SQLRow', first, evidence_id='ev-' + '1' * 16, run_id='run-1'),
            node('SQLRow', dict(first), evidence_id='ev-' + '2' * 16, run_id='run-2'),
        ], space_id=SPACE)
        self.assertIn('restates', plans['counts'])
        same_run = projection.plan_wiki_derived_relations([
            node('SQLRow', first, evidence_id='ev-' + '1' * 16, run_id='run-1'),
            node('SQLRow', dict(first), evidence_id='ev-' + '2' * 16, run_id='run-1'),
        ], space_id=SPACE)
        self.assertNotIn('restates', same_run['counts'], '同一运行内的两条不是"两次运行"')

    def test_without_a_space_the_restates_derivation_is_reported_as_skipped(self):
        plans = projection.plan_wiki_derived_relations([], space_id=None)
        self.assertEqual(plans['restates_skipped_reason'], 'SPACE_ID_REQUIRED_FOR_COMPARISON_KEY')

    def test_restates_can_be_restricted_to_the_runs_being_projected(self):
        # 全库跑 restates 会把任意两个共有 comparison_key 的测量节点配对（实测 11305 对），
        # 那不是"两次运行得到同一主张"。run_ids 就是把范围钉在真正要投影的运行上。
        first = dict(PAYLOADS['SQLRow'])
        nodes = [
            node('SQLRow', first, evidence_id='ev-' + '1' * 16, run_id='run-1'),
            node('SQLRow', dict(first), evidence_id='ev-' + '2' * 16, run_id='run-2'),
            node('SQLRow', dict(first), evidence_id='ev-' + '3' * 16, run_id='run-3'),
        ]
        wide = projection.plan_wiki_derived_relations(nodes, space_id=SPACE)
        self.assertEqual(wide['counts']['restates'], 3, '三个运行两两各一对')
        self.assertEqual(wide['restates_scope'], 'all_nodes_supplied')
        narrow = projection.plan_wiki_derived_relations(nodes, space_id=SPACE,
                                                        run_ids=['run-1', 'run-2'])
        self.assertEqual(narrow['counts']['restates'], 1)
        self.assertEqual(narrow['restates_scope'], 'run_ids')
        self.assertEqual(narrow['runs_considered'], ['run-1', 'run-2'])

    def test_absent_relations_are_named_not_silently_empty(self):
        plans = projection.plan_wiki_derived_relations([], space_id=SPACE)
        summary = projection.deferred_derivation_summary(plans)
        self.assertEqual(summary['deferred'], 0)
        self.assertEqual(set(summary['absent']), set(projection.DERIVED_RELATION_TYPES))


# ================================================================ 9. 契约常量的自证

class ContractConstantTest(unittest.TestCase):
    """常量必须与 021 迁移和两份契约文档逐字一致。"""

    def test_id_prefixes_match_the_migration_checks(self):
        self.assertEqual(projection.ID_PREFIXES, {
            'entry': 'wiki', 'revision': 'wrev', 'evidence_ref': 'wev',
            'provenance_link': 'wprov', 'relation': 'wrel', 'review_event': 'wevt',
            'build_job': 'wjob', 'space': 'wsp', 'assertion': 'wassert'})

    def test_allocate_id_validates_for_every_kind(self):
        for kind in projection.ID_PREFIXES:
            value = projection.allocate_id(kind)
            self.assertTrue(projection.validate_id(kind, value), value)
            self.assertEqual(len(value), len(projection.ID_PREFIXES[kind]) + 1 + 24)
            self.assertFalse(projection.validate_id(kind, value.upper()), value)
            self.assertFalse(projection.validate_id(kind, value + 'a'), value)
        with self.assertRaises(projection.ProjectionError):
            projection.allocate_id('nope')

    def test_seeded_allocation_is_reproducible(self):
        self.assertEqual(projection.allocate_id('entry', seed='x'),
                         projection.allocate_id('entry', seed='x'))
        self.assertNotEqual(projection.allocate_id('entry', seed='x'),
                            projection.allocate_id('entry', seed='y'))

    def test_entry_types_and_locator_kinds_are_the_closed_sets(self):
        self.assertEqual(set(projection.ENTRY_TYPES),
                         {'business_entity', 'metric', 'norm', 'conclusion'})
        self.assertEqual(set(projection.LOCATOR_KINDS),
                         {'sql_record', 'sql_aggregate', 'pdf_region', 'text_span'})

    def test_reason_codes_are_the_frozen_five(self):
        self.assertEqual({
            projection.REASON_EDGE_COLUMNS_ABSENT, projection.REASON_NODE_BINDING_NOT_PERSISTED,
            projection.REASON_SOURCE_SNAPSHOT_ABSENT, projection.REASON_TRACE_EVENT_PRUNED,
            projection.REASON_REVISION_PREDATES_PROJECTION},
            {'EDGE_COLUMNS_ABSENT_IN_DEPLOYED_SCHEMA', 'NODE_BINDING_NOT_PERSISTED',
             'SOURCE_SNAPSHOT_ABSENT', 'TRACE_EVENT_PRUNED', 'REVISION_PREDATES_PROJECTION'})

    def test_the_two_conflict_path_names_are_the_frozen_ones(self):
        self.assertEqual(projection.CONFLICT_PATH_INDUSTRIAL, 'industrial_table')
        self.assertEqual(projection.CONFLICT_PATH_CROSS_SOURCE, 'cross_source_trace')

    def test_the_connection_dsns_follow_the_repository_convention(self):
        self.assertIn('user=datamind_app', projection.write_dsn())
        self.assertIn('user=readonly_user', projection.read_dsn())
        # 照 settings_store.py:31-33 / sql_engine.py:353-357：默认值硬编码在模块内，可被环境变量覆盖
        self.assertEqual(projection.write_dsn(), projection.DEFAULT_WRITE_DSN)
        self.assertEqual(projection.read_dsn(), projection.DEFAULT_READ_DSN)

    def test_every_jsonb_this_module_emits_avoids_the_json_null_literal(self):
        # 迁移对所有 jsonb 列都拒绝 JSON null 字面量；本模块输出的对象里不得出现裸 'null'
        def scan(value, path=''):
            if isinstance(value, dict):
                for key, item in value.items():
                    scan(item, path + '.' + str(key))
            elif isinstance(value, (list, tuple)):
                for index, item in enumerate(value):
                    scan(item, '%s[%d]' % (path, index))
            elif value == 'null':
                self.fail('JSON null 字面量出现在 %s' % path)

        conflict = projection.conflict_from_industrial_row(conflict_row())
        scan(conflict)
        relation = projection.project_relation('supports', 'wev-' + 'a' * 24, 'wev-' + 'b' * 24)
        scan(relation)
        ref = projection.project_evidence_ref(SPACE, node('Chunk', PAYLOADS['ChunkEmptyQuote']))
        scan(ref)


if __name__ == '__main__':
    unittest.main()
