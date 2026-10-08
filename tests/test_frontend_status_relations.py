"""引用关联载荷的前端呈现，以及文档状态语义在这两个视图上的边界。

这里只有结构化/契约断言：本仓库没有 JavaScript 测试运行器，本轮也没有浏览器，所以既不启动
DOM 也不声称验证了浏览器运行时行为。测试只钉住两处会静默漂移的接缝：

1. `rag_engine.citation_from_hit` 真正产出的关系字段名，必须与 `web/app.js` 读取的字段名一致，
   并且"载荷里没有 relations 就不渲染"的分支必须存在——否则前端会臆造关系或占位。
   关系条目里的 `peer_block_id` 是**对端**，locator 条目里的 `block_id` 是**被引块**，两者语义
   相反，这里也一并钉住。
2. 摄取期间文档状态可能显式为 `parsing`，而 `/v1/documents` 只返回 `status='indexed'` 的文档。
   文档列表因此看不到任何非 indexed 文档：不能为其臆造状态列，只能为空列表给出明确中文语义。
   文件任务/摄取进度由 `web/multifile.js` 渲染（本文件之外），它自带中文状态映射。
"""
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from datamind import rag_engine

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / 'web' / 'app.js'

# 前端为一张引用卡片实际读取的关系字段；键名必须与 citation_from_hit 的产出逐个对齐。
RELATION_FIELDS_READ_BY_THE_UI = ('relation_type', 'relation_state', 'recovery_method',
                                  'evidence', 'direction', 'peer_block_id', 'cited_block_id')
# 存储层关系行的完整形状（与 ingestion.contract_store.read_relations 返回的一致）。
STORED_RELATION_FIELDS = ('relation_id', 'document_id', 'relation_type', 'relation_state',
                          'recovery_method', 'evidence', 'direction', 'peer_block_id')


def function_body(source, name):
    """Return the body of `function name(...)` (or its async form) by brace matching."""
    match = re.search(r'(?:async\s+)?function\s+' + re.escape(name) + r'\s*\(', source)
    if not match:
        return ''
    start = source.index('{', match.end() - 1)
    depth = 0
    for position in range(start, len(source)):
        character = source[position]
        if character == '{':
            depth += 1
        elif character == '}':
            depth -= 1
            if depth == 0:
                return source[start:position + 1]
    return source[start:]


def sample_hit(block_ids=('blk-dim-1',)):
    return {'chunk_id': 'chunk-1', 'document_id': 'doc-1', 'title': '图纸说明', 'collection': 'demo_kb',
            'origin': 'curated', 'document_version': 1, 'document_sha256': 'a' * 64,
            'content_hash': 'b' * 64, 'parser_version': 'cad-0.1', 'quality_detail': {},
            'page_start': 1, 'page_end': 1, 'block_ids': list(block_ids), 'source_blocks': [],
            'section_path': [], 'retrieval_score': 0.9, 'retrieval_scores': {}, 'file_type': 'pdf',
            'exact_text': 'DIMENSION 标注 ⌀40 位于 (120,80)'}


def stored_relation(direction='source', peer_block_id='blk-circle-1'):
    """A relation row shaped exactly as storage returns it; `peer_block_id` is the other end."""
    return {'relation_id': 'rel-1', 'document_id': 'doc-1', 'relation_type': 'references',
            'relation_state': 'verified', 'recovery_method': 'native_reference',
            'evidence': 'DIMENSION 标注 ⌀40 位于 (120,80)，引用目标圆 handle=2F',
            'direction': direction, 'peer_block_id': peer_block_id}


class CitationRelationFrontendTests(unittest.TestCase):
    def setUp(self):
        self.source = APP_JS.read_text(encoding='utf-8')

    def test_the_citation_card_renders_a_relations_block(self):
        for function in ('citationRelations', 'citationRelationLabel', 'citationRelationEndpoint',
                         'citationRelationsNote'):
            self.assertTrue(function_body(self.source, function), function + ' must exist')
        # 关系块被接进引用卡片，并且读取的是 relations 条目自身的键名。
        for needle in ('source.relations', 'relation_type', 'relation_state', 'recovery_method',
                       'relation.evidence', 'relation.direction'):
            self.assertIn(needle, self.source, needle)
        card = function_body(self.source, 'showDocumentResult')
        self.assertIn('citationRelationsNote(source, citationRelations(source))', card)
        self.assertIn('card.insertBefore(relationNote, links)', card)

    def test_nothing_is_rendered_when_the_citation_carries_no_relations(self):
        # 没有 relations 时既不返回节点，也不插入占位；空块会被读成"存在关系"。
        note = function_body(self.source, 'citationRelationsNote')
        self.assertRegex(note, r'if\s*\(!\s*entries\.length\s*\)\s*\{\s*return\s+null\s*;')
        collect = function_body(self.source, 'citationRelations')
        self.assertIn('(source.relations || [])', collect)
        self.assertIn('.filter(', collect)
        # 调用点必须容忍 null，而不是无条件插入。
        card = function_body(self.source, 'showDocumentResult')
        self.assertRegex(card, r'if\s*\(relationNote\)\s*\{\s*card\.insertBefore\(')

    def test_the_peer_block_naming_trap_is_respected(self):
        # 关系条目的 peer_block_id 是对端，cited_block_id 才是被引块；两者不可互换。
        endpoint = function_body(self.source, 'citationRelationEndpoint')
        self.assertIn('relation.peer_block_id', endpoint)
        self.assertIn('relation.cited_block_id', endpoint)
        self.assertIn('对端', endpoint)
        # 定位侧只认 locator 条目，绝不把 peer_block_id 当被引块使用。
        self.assertNotIn('peer_block_id', function_body(self.source, 'citationLocators'))
        self.assertNotIn('peer_block_id', function_body(self.source, 'locatorLabel'))
        # 命名陷阱必须写在代码注释里，避免下一个人"顺手统一"。
        self.assertIn('peer_block_id', self.source)
        self.assertIn('命名陷阱', self.source)

    def test_existing_locator_rendering_survives(self):
        # (A) 不得破坏既有定位徽标与"高亮引用区域"。
        for needle in ('citationLocators', 'locatorLabel', 'citationRegionFrame', 'field-page',
                       'source.locators', 'rotation_degrees', '高亮引用区域', '收起高亮'):
            self.assertIn(needle, self.source, needle)
        self.assertIn('citationRegionFrame(source, located)', function_body(self.source, 'showDocumentResult'))


class RelationContractDriftTests(unittest.TestCase):
    """citation_from_hit 的产出与前端读取的键名必须一致，任何一侧改名都要在这里失败。"""

    def setUp(self):
        self.source = APP_JS.read_text(encoding='utf-8')
        self.entry = rag_engine.citation_from_hit(
            sample_hit(), relations={'blk-dim-1': [stored_relation()]})['relations'][0]

    def test_the_ui_reads_only_relation_fields_the_contract_emits(self):
        for field in RELATION_FIELDS_READ_BY_THE_UI:
            self.assertIn(field, self.entry, field)
            self.assertIn('relation.' + field, self.source, 'relation.' + field)

    def test_the_contract_emits_the_whole_stored_relation_row(self):
        for field in STORED_RELATION_FIELDS:
            self.assertIn(field, self.entry, field)
        self.assertEqual(self.entry['cited_block_id'], 'blk-dim-1')

    def test_peer_and_cited_block_are_the_opposite_ends(self):
        incoming = rag_engine.citation_from_hit(
            sample_hit(), relations={'blk-dim-1': [stored_relation(direction='target',
                                                                   peer_block_id='blk-circle-9')]})['relations'][0]
        self.assertEqual(incoming['peer_block_id'], 'blk-circle-9')
        self.assertEqual(incoming['cited_block_id'], 'blk-dim-1')
        self.assertEqual(incoming['direction'], 'target')
        self.assertNotEqual(incoming['peer_block_id'], incoming['cited_block_id'])

    def test_a_citation_without_relations_emits_none(self):
        # 前端据此判断"不渲染"：载荷里必须是空列表而不是缺键或占位对象。
        citation = rag_engine.citation_from_hit(sample_hit())
        self.assertEqual(citation['relations'], [])


class DocumentStatusSemanticsTests(unittest.TestCase):
    """文档列表只看到 indexed 文档；语义必须显式，且不为不可见状态臆造展示。"""

    def setUp(self):
        self.source = APP_JS.read_text(encoding='utf-8')
        self.documents = function_body(self.source, 'loadDocuments')

    def test_the_status_semantics_are_explicit_chinese_labels(self):
        match = re.search(r'var\s+DOCUMENT_STATUS_LABELS\s*=\s*\{(.*?)\};', self.source, re.S)
        self.assertTrue(match, 'DOCUMENT_STATUS_LABELS 必须存在')
        labels = match.group(1)
        for state in ('indexed', 'parsing', 'pending', 'failed'):
            self.assertRegex(labels, re.escape(state) + r"\s*:", state)
        for label in ('可检索', '处理中', '待处理', '处理失败'):
            self.assertIn(label, labels, label)

    def test_the_document_list_states_what_it_can_and_cannot_show(self):
        # API 已过滤 status='indexed'，视图不接收过滤参数，也不渲染原始英文状态。
        self.assertIn("apiFetch('/v1/documents')", self.documents)
        self.assertNotIn('/v1/documents?', self.documents)
        self.assertNotIn('doc.status', self.documents)
        self.assertNotIn('doc.status', self.source)
        # 空列表必须给出中文语义，而不是只留表头（会被读成"没有文档/没有数据"）。
        self.assertIn('DOCUMENT_STATUS_LABELS.indexed', self.documents)
        self.assertIn('DOCUMENT_STATUS_LABELS.parsing', self.documents)
        self.assertIn('documents.length', self.documents)
        self.assertIn('索引', self.documents)

    def test_ingestion_progress_is_not_rendered_here(self):
        # 文件任务/摄取进度属于 web/multifile.js（本文件之外），它自带中文状态映射；
        # app.js 不应重复实现一份会漂移的状态渲染。
        for element in ('fileJobs', 'fileJobDetail', 'fileRows'):
            self.assertNotIn("$('" + element + "')", self.source, element)


class AppScriptSyntaxTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'node 未安装')
    def test_app_js_is_syntactically_valid(self):
        completed = subprocess.run([shutil.which('node'), '--check', str(APP_JS)],
                                   capture_output=True, text=True, timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == '__main__':
    unittest.main()
