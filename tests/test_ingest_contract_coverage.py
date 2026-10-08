# -*- coding: utf-8 -*-
"""契约覆盖回归测试：F01 / F02 / F08 / F12。

全部离线：只驱动纯函数与内存假游标，**不连真实 Qdrant/PostgreSQL/嵌入模型**，也不
调用 `ingest.main()` 或 `db/seed_industrial.main()`（那两个会写库、跑迁移）。

- F01：OHR-Bench JSON 分支必须走契约路径，且只能是"合法 locator"或"显式缺口"，
       绝不允许把 `bbox=[0, 0, 0, 0]` 送进契约（契约判 bbox_inverted）。
- F02：工业种子路径 `quality_detail` 里的定位计数必须等于实际写入
       `document_block_locators` 的行数（历史上声称 296 而实际 0）。
- F08：摄取写入的 `status` 取值集中声明；契约状态机无法表达的值记为已声明缺口。
- F12：`--reset` 的清理语句必须覆盖契约三表。
"""
import ast
import inspect
import json
import unittest
from pathlib import Path

from fastapi import HTTPException

from datamind import parse_contract
from db import seed_industrial
from ingestion import ingest

SOURCE_VERSION = 'a' * 64
OHR_RECORD = {'sample_id': 'ohr-001', 'source': 'OHR-Bench_v2', 'domain': 'academic',
              'doc_name': 'academic/example', 'page_idx': 17, 'license': 'CC-BY-4.0',
              'gt_text': '示例 GT 文本。', 'noise_text': '噪声文本'}

PDF_REGION_LOCATOR = {'locator_kind': 'pdf_region', 'document_id': 'doc-seed',
                      'source_version': SOURCE_VERSION, 'page_no': 1,
                      'bbox': [10.0, 20.0, 100.0, 40.0], 'page_width': 612.0,
                      'page_height': 792.0, 'unit': 'point',
                      'coordinate_system': 'top-left', 'rotation_degrees': 0}


def collect_bboxes(node):
    """Every `bbox` value found anywhere in a payload (locator.bbox included)."""
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == 'bbox':
                found.append(value)
            found.extend(collect_bboxes(value))
    elif isinstance(node, (list, tuple)):
        for item in node:
            found.extend(collect_bboxes(item))
    return found


class FakeCursor:
    """Records `(sql, params)` so cleanup/write sets can be asserted without a database."""

    def __init__(self):
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))


class OhrContractCoverageTests(unittest.TestCase):
    """F01：OHR 形状的块经契约路径后，要么有合法 locator，要么有显式缺口。"""

    def summary(self):
        blocks = [ingest.ohr_block(OHR_RECORD)]
        ingest.ensure_block_ids(blocks, 'doc-ohr')
        summary = ingest._ohr_contract_summary(
            OHR_RECORD, Path('data/ohr_bench_samples/ohr-001.json'), 'doc-ohr', blocks,
            SOURCE_VERSION)
        return blocks, summary

    def test_the_ohr_branch_carries_no_fabricated_zero_bbox(self):
        block = ingest.ohr_block(OHR_RECORD)
        self.assertIsNone(block['bbox'], 'OHR 记录没有几何信息，只能是 None')
        self.assertNotEqual(block.get('bbox'), [0, 0, 0, 0])
        # 落库时确实是 SQL NULL，而不是 json 的 'null'
        self.assertIsNone(ingest._block_row(dict(block, block_id='blk-ohr'), 'doc-ohr')[3])

    def test_the_ohr_branch_goes_through_the_contract_and_declares_the_gap(self):
        blocks, summary = self.summary()
        self.assertEqual(summary['validation'], 'parsed', summary)
        self.assertEqual(summary['adapter'], ingest.OHR_ADAPTER)
        self.assertEqual(summary['parse_status'], 'unsupported')
        self.assertEqual(summary['locators_written'], 0, '不得编造定位')
        self.assertIn(ingest.OHR_LOCATOR_GAP, summary['unsupported_features'])
        # 缺口显式挂在块上：没有 locator 就写不出 locator 行，而不是写一个假的
        self.assertIsNone(blocks[0]['contract_locator'])
        self.assertIsNone(ingest._locator_row(blocks[0], 'doc-ohr'))

    def test_the_contract_payload_is_accepted_and_never_contains_a_zero_bbox(self):
        payload = ingest._ohr_contract_payload(
            record=OHR_RECORD, doc_id='doc-ohr', source_name='ohr-001.json',
            parser_version=ingest.PARSER_VERSION, source_version=SOURCE_VERSION)
        self.assertEqual(payload['blocks'], [], '无 locator 的块不能被表示成 contract block')
        self.assertIn(ingest.OHR_LOCATOR_GAP, payload['unsupported_features'])
        self.assertNotIn([0, 0, 0, 0], collect_bboxes(payload))
        validated = parse_contract.validate_parse_result(payload)
        self.assertEqual(validated.payload['parse_status'], 'unsupported')

    def test_a_zero_bbox_would_be_rejected_by_the_contract(self):
        # 这就是 F01 的证据：[0, 0, 0, 0] 不是"缺失"，契约判它倒置非法。
        with self.assertRaises(HTTPException):
            parse_contract.from_pdf_blocks(
                [{'block_id': 'blk-x', 'page_no': 1, 'block_type': 'paragraph',
                  'text': '正文', 'bbox': [0, 0, 0, 0]}],
                page_sizes={1: (612.0, 792.0)}, parser_version='test-parser',
                document_id='doc-x', source_version=SOURCE_VERSION)

    def test_without_a_page_size_no_region_locator_can_be_honestly_built(self):
        # OHR 样本没有页面尺寸：契约会拒绝无尺寸的 pdf_region，而不是接受编造的尺寸。
        with self.assertRaises(HTTPException):
            parse_contract.from_pdf_blocks(
                [{'block_id': 'blk-x', 'page_no': 1, 'block_type': 'paragraph',
                  'text': '正文', 'bbox': [10.0, 10.0, 100.0, 20.0]}],
                page_sizes={}, parser_version='test-parser',
                document_id='doc-x', source_version=SOURCE_VERSION)


class IndustrialSeedLocatorTests(unittest.TestCase):
    """F02：种子路径必须真的写 locator，并且计数等于实际写出的行数。"""

    def blocks(self):
        return [{'block_id': 'iblk-a', 'contract_version': parse_contract.CONTRACT_VERSION,
                 'contract_locator': dict(PDF_REGION_LOCATOR)},
                {'block_id': 'iblk-b', 'contract_version': parse_contract.CONTRACT_VERSION}]

    def test_the_quality_count_equals_the_rows_actually_written(self):
        blocks = self.blocks()
        # 历史谎报：parse_pdf 声称 296，但种子路径从不写 locator 表。
        quality = {'parse_contract': {'locators_written': 296, 'validation': 'parsed'}}
        rows = seed_industrial.locator_plan(blocks, 'doc-seed', quality)
        cur = FakeCursor()
        written = seed_industrial.write_locators(cur, 'doc-seed', rows)

        inserts = [params for sql, params in cur.executed if sql == ingest.LOCATOR_INSERT_SQL]
        self.assertEqual(written, len(inserts))
        self.assertEqual(quality['parse_contract']['locators_written'], len(inserts),
                         'quality_detail 的计数必须等于实际写入的行数')
        self.assertNotEqual(quality['parse_contract']['locators_written'], 296)
        # 行里的 block_id 必须与 document_blocks 用的是同一次重写后的 id
        self.assertEqual([params[0] for params in inserts], ['iblk-a'])
        self.assertEqual(inserts[0][1], 'doc-seed')
        self.assertEqual(inserts[0][2], parse_contract.CONTRACT_VERSION)

    def test_the_locator_payload_is_the_validated_one_untouched(self):
        rows = seed_industrial.locator_plan(self.blocks(), 'doc-seed', {})
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0][3]), PDF_REGION_LOCATOR)

    def test_a_block_without_a_validated_locator_is_declared_not_counted(self):
        quality = {'parse_contract': {'locators_written': 999}}
        rows = seed_industrial.locator_plan(self.blocks(), 'doc-seed', quality)
        self.assertEqual(len(rows), 1)
        self.assertEqual(quality['parse_contract']['locators_written'], 1)
        self.assertEqual(quality['parse_contract']['locators_unavailable'], 1)
        self.assertEqual(quality['parse_contract']['locator_gap'], 'locator_unavailable')

    def test_writing_deletes_previous_rows_before_inserting(self):
        cur = FakeCursor()
        seed_industrial.write_locators(cur, 'doc-seed', seed_industrial.locator_plan(
            self.blocks(), 'doc-seed', {}))
        delete_sql, delete_params = cur.executed[0]
        self.assertEqual(delete_sql, 'DELETE FROM document_block_locators WHERE document_id=%s')
        self.assertEqual(delete_params, ('doc-seed',))
        self.assertEqual(cur.executed[1][0], ingest.LOCATOR_INSERT_SQL)


class IngestStatusContractTests(unittest.TestCase):
    """F08：status 取值集中声明；契约表达不了的终态记为显式缺口。"""

    def test_written_statuses_are_declared_in_one_place(self):
        self.assertEqual(set(ingest.ASSET_STATUS_WRITES), {'parsing', 'indexed', 'failed'})
        self.assertEqual(set(ingest.ASSET_STATUS_WRITES),
                         {ingest.NON_SEARCHABLE_STATUS, ingest.SEARCHABLE_STATUS,
                          ingest.FAILED_STATUS})
        self.assertEqual(ingest.SEARCHABLE_STATUS, 'indexed')

    def test_every_written_status_is_contract_known_or_a_declared_gap(self):
        written = set(ingest.ASSET_STATUS_WRITES)
        gaps = written - set(parse_contract.INGEST_STATES)
        if 'indexed' in parse_contract.INGEST_STATES:
            self.assertEqual(gaps, set(), '契约已能表达 indexed，缺口声明可以清空')
        else:
            self.assertEqual(gaps, set(ingest.INGEST_STATE_CONTRACT_GAPS))
            self.assertIn('indexed', gaps, '真实终态 indexed 无法被契约状态机表达')

    def test_the_ingest_path_has_no_caller_of_the_contract_state_machine(self):
        # F08：ingest_step() 在本仓库没有调用者；摄取不假装走那套状态机。
        # 用 AST 找真实调用（注释里出现这个名字不算调用）。
        tree = ast.parse(inspect.getsource(ingest))
        called = {node.func.attr if isinstance(node.func, ast.Attribute)
                  else getattr(node.func, 'id', '') for node in ast.walk(tree)
                  if isinstance(node, ast.Call)}
        self.assertNotIn('ingest_step', called)

    def test_status_writes_are_centralized(self):
        source = inspect.getsource(ingest)
        self.assertIn('def mark_asset_status', source)
        self.assertIn('REGISTER_ASSET_SQL', source)
        # 写 document_assets.status 的 UPDATE 语句只在常量里各定义一次
        self.assertEqual(source.count('UPDATE document_assets SET status=%s'), 2)
        # 发布终态与第 1 步都经由同一个 helper
        self.assertEqual(inspect.getsource(ingest.publish_document).count('mark_asset_status('), 2)
        self.assertEqual(inspect.getsource(ingest.main).count('register_asset('), 1)


class ResetContractTablesTests(unittest.TestCase):
    """F12：reset 的清理语句必须覆盖契约三表。"""

    def deleted_tables(self):
        cur = FakeCursor()
        ingest.reset_metadata(cur)
        return [sql.split()[-1] for sql, _ in cur.executed if sql.startswith('DELETE FROM ')]

    def test_reset_clears_the_three_contract_tables(self):
        deleted = self.deleted_tables()
        for table in ('document_contract_blocks', 'document_block_locators',
                      'document_block_relations'):
            self.assertIn(table, deleted, f'{table} 必须被 --reset 清掉')

    def test_reset_still_clears_the_legacy_tables(self):
        deleted = self.deleted_tables()
        for table in ('chunks', 'document_blocks', 'ingest_jobs', 'document_assets'):
            self.assertIn(table, deleted)

    def test_children_are_deleted_before_their_parents(self):
        deleted = self.deleted_tables()
        for child in ('document_contract_blocks', 'document_block_locators',
                      'document_block_relations'):
            self.assertLess(deleted.index(child), deleted.index('document_blocks'))
        self.assertLess(deleted.index('document_blocks'), deleted.index('document_assets'))

    def test_reset_also_clears_fact_rows_and_their_children(self):
        """事实行也是按文档的；只清 assets 会留下指向已删事实的复核与审批。

        这三张表对 `document_facts` 没有外键，因此不会被级联删除——必须显式覆盖。
        审批引用复核，所以 approvals 必须排在 reviews 之前。
        """
        tables = ingest.RESET_DELETE_TABLES
        for table in ('document_fact_approvals', 'document_fact_reviews', 'document_facts'):
            self.assertIn(table, tables)
        self.assertLess(tables.index('document_fact_approvals'),
                        tables.index('document_fact_reviews'))
        self.assertLess(tables.index('document_fact_reviews'), tables.index('document_facts'))

    def test_the_reset_path_uses_the_shared_cleanup(self):
        self.assertIn('reset_metadata(cur)', inspect.getsource(ingest.main))


if __name__ == '__main__':
    unittest.main()
