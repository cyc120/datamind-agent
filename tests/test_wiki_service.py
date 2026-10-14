"""Wiki P2 服务层（datamind/wiki.py）的契约测试：纯逻辑优先，数据库用例缺依赖即 skip。

为什么这样分层（本仓库的实测事实，不是偏好）：

* 系统 python（`C:\\Python314`）有 pytest 但**没有 psycopg2**；`.venv` 有 psycopg2 但
  **没有 pytest**。所以"身份归一、CAS 判定、三套状态、状态机"这些**纯逻辑**必须能在
  没有 psycopg2 的解释器里跑起来（`python -m pytest tests/test_wiki_service.py`），
  而真库契约用 `@unittest.skipUnless(DB, …)` 包住（先例 `tests/test_parse_contract.py:745-776`
  的既有权宜模式）；数据库用例在 `.venv` 下用 `python -m unittest` 跑。
* 真库用例**全部在事务里 ROLLBACK**：8 张 wiki 表在本轮开始与结束时都是 0 行
  （`WikiTablesStayEmptyTest` 就是这条自检）。不提交、不 DDL、不碰 `postgres` 持有的表。
"""
import inspect
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import HTTPException  # noqa: E402

from datamind import wiki  # noqa: E402  纯逻辑不需要 psycopg2（见模块头）

REV1 = 'wrev-' + '1' * 24
REV2 = 'wrev-' + '2' * 24
ENTRY = 'wiki-' + 'a' * 24
SPACE = 'wsp-' + 'b' * 24

try:  # 数据库用例的依赖；缺了就让那些用例 skip 而不是让整个文件 collection error
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:  # pragma: no cover
    psycopg2 = None
    RealDictCursor = None


def db_available():
    """真库可用性：应用角色能连上，且 8 张 wiki 表都在（021 已执行）。"""
    if psycopg2 is None:
        return False
    try:
        conn = psycopg2.connect(wiki.wiki_dsn())
        try:
            cur = conn.cursor()
            cur.execute("""SELECT count(*) FROM information_schema.tables
                WHERE table_schema='public' AND table_name LIKE 'wiki\\_%'""")
            return int(cur.fetchone()[0]) == 8
        finally:
            conn.rollback()
            conn.close()
    except Exception:
        return False


DB = db_available()

WIKI1, WIKI2, WIKI3, WIKI4 = ('WIKI1', 'WIKI2', 'WIKI3', 'WIKI4')


class FakePgError(Exception):
    """带 pgcode 的假异常：psycopg2 的错误对象就是这么暴露 SQLSTATE 的。"""

    def __init__(self, pgcode):
        super().__init__('fake %s' % pgcode)
        self.pgcode = pgcode


@contextmanager
def app_transaction():
    """一个**永不提交**的应用角色事务（真库用例的唯一入口）。"""
    conn = psycopg2.connect(wiki.wiki_dsn())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur
    finally:
        conn.rollback()
        conn.close()


def code_of(exc):
    return exc.detail.get('error_code')


class WikiIdentityTest(unittest.TestCase):
    """WIKI_DOMAIN.md §4.1 / §4.3：三类身份里本层负责的两类。"""

    def test_space_is_derived_deterministically_and_never_crosses_users(self):
        first = wiki.space_for_user('user-a')
        self.assertEqual(first, wiki.space_for_user('user-a'), '同一 user 必须得到同一空间')
        self.assertNotEqual(first, wiki.space_for_user('user-b'), '跨空间不归一（§4.1 规则 5）')
        self.assertRegex(first, r'^wsp-[0-9a-f]{24}$')
        self.assertNotIn('user-a', first, '空间 id 不透明，不得回显 user_id')

    def test_space_needs_an_identity(self):
        with self.assertRaises(HTTPException) as caught:
            wiki.space_for_user('  ')
        self.assertEqual(caught.exception.status_code, 401)
        self.assertEqual(code_of(caught.exception), 'SESSION_INVALID')

    def test_business_entity_key_is_entity_type_plus_unit_separator_key(self):
        key = wiki.key_part_for(entry_type='business_entity', entity_type='part',
                               canonical_entity_key='A102')
        self.assertEqual(key, 'part\x1fA102')

    def test_a_business_entity_without_a_confirmed_key_falls_back_to_pending_title(self):
        key = wiki.key_part_for(entry_type='business_entity', title='  设备  A102 ')
        self.assertEqual(key, 'pending:设备 a102', '空白折叠 + lower（§4.1）')
        self.assertEqual(wiki.confirmation_for(entry_type='business_entity', key_part=key),
                         'pending')
        with self.assertRaises(HTTPException) as caught:
            wiki.confirmation_for(entry_type='business_entity', key_part=key,
                                  explicit='confirmed')
        self.assertEqual(code_of(caught.exception), 'WIKI_BUILD_REQUEST_INVALID',
                         '规则 3：pending 键不得被自动升级为确认键')

    def test_metric_norm_conclusion_key_parts_are_the_frozen_compositions(self):
        self.assertEqual(wiki.key_part_for(entry_type='metric', metric_id='holding_temperature'),
                         'holding_temperature')
        self.assertEqual(
            wiki.key_part_for(entry_type='norm', source_document_id='doc-industrial-0ac6c55838c3',
                              source_revision='A102-V2', clause_key='clause-temp'),
            'doc-industrial-0ac6c55838c3\x1fA102-V2\x1fclause-temp',
            '§2.3：source_revision 必须进键，A102-V2 与 A102-V3 是两条规范')
        self.assertEqual(
            wiki.key_part_for(entry_type='conclusion', run_id='run-894b0785725c45f3',
                              claim_id='claim-80ac4ad77526407b'),
            'run-894b0785725c45f3\x1fclaim-80ac4ad77526407b')
        self.assertEqual(wiki.confirmation_for(entry_type='metric',
                                               key_part='holding_temperature'), 'confirmed',
                         '来源内的规范名不是"名字看着像"→ 默认 confirmed')
        self.assertEqual(wiki.confirmation_for(entry_type='conclusion',
                                               key_part='a\x1fb'), 'confirmed')

    def test_unknown_entry_type_and_missing_pieces_are_rejected(self):
        for kwargs in ({'entry_type': 'unknown'},
                       {'entry_type': 'norm', 'source_document_id': 'd', 'source_revision': 'r'},
                       {'entry_type': 'conclusion', 'run_id': 'r'},
                       {'entry_type': 'business_entity'}):
            with self.assertRaises(HTTPException) as caught:
                wiki.key_part_for(**kwargs)
            self.assertEqual(caught.exception.status_code, 422, kwargs)
            self.assertEqual(code_of(caught.exception), 'WIKI_BUILD_REQUEST_INVALID')

    def test_comparison_key_is_order_independent_and_scope_sensitive(self):
        base = {'space_id': SPACE, 'entity_key': {'part_id': 'A102'}, 'predicate':
                'holding_temperature', 'time_scope': {'from': '2026-09-01'},
                'record_scope': {'batch_id': 'B001'}, 'statistic': 'core'}
        first = wiki.comparison_key_of(**base)
        self.assertRegex(first, r'^[a-f0-9]{64}$', '261: 库里存裸 hex64')
        reordered = wiki.comparison_key_of(**{'statistic': 'core', 'record_scope':
                                              {'batch_id': 'B001'},
                                              'time_scope': {'from': '2026-09-01'},
                                              'predicate': 'holding_temperature',
                                              'entity_key': {'part_id': 'A102'},
                                              'space_id': SPACE})
        self.assertEqual(first, reordered, '比较身份必须与字典插入顺序无关')
        self.assertEqual(first, wiki.comparison_key_of(statistic='core',
                                                       record_scope={'batch_id': 'B001'},
                                                       time_scope={'from': '2026-09-01'},
                                                       predicate='holding_temperature',
                                                       entity_key={'part_id': 'A102'},
                                                       space_id=SPACE))
        self.assertNotEqual(first, wiki.comparison_key_of(**{**base, 'record_scope':
                                                             {'batch_id': 'B002'}}),
                            '§4.3 规则 2：具名记录不同 → 连比较组都不同')
        self.assertNotEqual(first, wiki.comparison_key_of(**{**base, 'space_id':
                                                             'wsp-' + 'c' * 24}))
        self.assertEqual(wiki.with_sha256_prefix(first), 'sha256:' + first,
                         '接口层加前缀，指纹列存裸 hex（021:229-232）')

    def test_a_client_supplied_comparison_key_must_match_the_recomputed_one(self):
        computed = wiki.comparison_key_of(space_id=SPACE, entity_key={}, predicate='p')
        self.assertEqual(wiki.normalize_comparison_key('sha256:' + computed, computed=computed),
                         computed)
        self.assertEqual(wiki.normalize_comparison_key(computed, computed=computed), computed)
        for bad in ('sha256:' + '0' * 64, '', None, 'not-a-digest'):
            with self.assertRaises(HTTPException) as caught:
                wiki.normalize_comparison_key(bad, computed=computed)
            self.assertEqual(code_of(caught.exception), 'WIKI_BUILD_REQUEST_INVALID')

    def test_ids_match_the_frozen_prefix_plus_hex24_shapes(self):
        for maker, pattern in ((wiki.entry_id, wiki.ENTRY_ID_RE),
                               (wiki.revision_id, wiki.REVISION_ID_RE),
                               (wiki.assertion_id, wiki.ASSERTION_ID_RE),
                               (wiki.relation_id, wiki.RELATION_ID_RE),
                               (wiki.review_event_id, wiki.REVIEW_EVENT_ID_RE)):
            value = maker()
            self.assertTrue(pattern.fullmatch(value), value)
            self.assertNotEqual(value, maker(), '分配型 id 不得重复')


class WikiCasPublicationTest(unittest.TestCase):
    """WIKI_API.md §5.12/§5.14 的比较并交换与发布前置。"""

    def test_cas_passes_only_on_the_expected_head(self):
        self.assertTrue(wiki.cas_passes(None, None), '首版发布：预期 null 且实际为空')
        self.assertTrue(wiki.cas_passes(REV1, REV1))
        self.assertFalse(wiki.cas_passes(REV1, REV2))
        self.assertFalse(wiki.cas_passes(None, REV1), '已经有人发布过 → 过时基线')
        self.assertFalse(wiki.cas_passes(REV1, None))
        self.assertTrue(wiki.cas_passes('', None), "'' 与缺失等价")

    def test_a_stale_baseline_is_a_conflict_and_only_a_tip_is_accepted(self):
        self.assertIsNone(wiki.base_revision_problem(base_revision_id=None,
                                                     has_revisions=False),
                          '首版：显式 null 是唯一合法基线')
        self.assertIsNone(wiki.base_revision_problem(base_revision_id=REV1,
                                                     has_revisions=True, tip_ids=[REV1]))
        self.assertEqual(wiki.base_revision_problem(base_revision_id=REV1, has_revisions=True,
                                                    tip_ids=[REV2]),
                         'WIKI_VERSION_CONFLICT', 'v1 已经有子版本 → 它是过时基线')
        self.assertIsNone(wiki.base_revision_problem(base_revision_id=REV1, has_revisions=True,
                                                     tip_ids=[REV1, REV2]),
                          '同一个 tip 上分叉出两个候选版本是允许的（谁发布由 CAS 决定）')
        self.assertEqual(wiki.base_revision_problem(base_revision_id=None, has_revisions=True,
                                                    tip_ids=[REV1]),
                         'WIKI_VERSION_CONFLICT',
                         '已经有版本 → 显式 null 不再是合法基线')
        self.assertEqual(wiki.base_revision_problem(base_revision_id=REV2, has_revisions=True,
                                                    tip_ids=[], head_revision_id=REV1),
                         'WIKI_VERSION_CONFLICT',
                         '退化保护：查不到 tip 时只有发布头可以当基线')

    def test_the_tip_set_does_not_depend_on_created_at(self):
        """同一事务里 `now()` 是常量 —— "最新版本"不能按时间戳判定（端到端用例实测到的缺陷）。"""
        self.assertEqual(wiki.baseline_tips(has_revisions=False), {None})
        self.assertEqual(wiki.baseline_tips(has_revisions=True, tip_ids=[REV1, REV2]),
                         {REV1, REV2})
        source = (ROOT / 'datamind' / 'wiki.py').read_text(encoding='utf-8')
        self.assertIn('NOT EXISTS (SELECT 1 FROM wiki_revisions c', source,
                      'tip 必须由 base_revision_id 的子版本关系判定')

    def test_head_normalisation_rejects_junk_instead_of_silently_nulling_it(self):
        self.assertIsNone(wiki.normalize_head(None))
        self.assertIsNone(wiki.normalize_head('  '))
        self.assertIsNone(wiki.normalize_head('null'))
        self.assertEqual(wiki.normalize_head(REV1), REV1)
        with self.assertRaises(HTTPException) as caught:
            wiki.normalize_head('rev-1')
        self.assertEqual(code_of(caught.exception), 'WIKI_BUILD_REQUEST_INVALID')

    def test_publication_requires_a_confirmed_identity_and_one_review_record(self):
        clean = wiki.publication_blockers(confirmation='confirmed', review_event_count=1,
                                          publication_status='draft')
        self.assertEqual(clean, [])
        codes = [problem[0] for problem in wiki.publication_blockers(
            confirmation='pending', review_event_count=0, publication_status='draft')]
        self.assertEqual(codes, ['WIKI_ENTRY_IDENTITY_PENDING', 'WIKI_REVIEW_REQUIRED'])
        self.assertEqual(wiki.publication_blockers(confirmation='confirmed',
                                                   review_event_count=1,
                                                   publication_status='archived')[0][0],
                         'WIKI_VERSION_CONFLICT')

    def test_unresolved_conflicts_and_unverified_assertions_do_not_block_publication(self):
        """§5.14 规则 5 与 §5.4 禁令 1：发布只看发布维度。"""
        parameters = set(inspect.signature(wiki.publication_blockers).parameters)
        self.assertEqual(parameters, {'confirmation', 'review_event_count',
                                      'publication_status', 'revision_present'},
                         '发布前置条件不得读主张核验状态或来源有效性')
        with self.assertRaises(HTTPException) as caught:
            wiki.raise_publication_blockers(confirmation='pending', review_event_count=1,
                                            publication_status='draft')
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(code_of(caught.exception), 'WIKI_ENTRY_IDENTITY_PENDING')

    def test_cursors_round_trip_and_reject_tampering(self):
        marker = {'created_at': '2026-10-14T09:00:00+08:00', 'revision_id': REV1}
        encoded = wiki.encode_cursor(marker)
        self.assertNotIn('{', encoded, '游标对客户端不透明（§4 分页语义 1）')
        self.assertEqual(wiki.decode_cursor(encoded, keys=('created_at', 'revision_id')), marker)
        for bad in ('not-base64!!', wiki.encode_cursor({'revision_id': REV1}), '', None, 'A' * 600):
            with self.assertRaises(HTTPException) as caught:
                wiki.decode_cursor(bad, keys=('created_at', 'revision_id'))
            self.assertEqual(caught.exception.status_code, 422)


class WikiStatusDimensionsTest(unittest.TestCase):
    """WIKI_DOMAIN.md §5.4 的六条禁令：三套状态不得互相推导。"""

    def test_the_three_dimensions_are_three_separate_fields(self):
        status = wiki.status_dimensions(publication='published', assertion='unverified',
                                        source_validity='stale')
        self.assertEqual(set(status), {'publication', 'assertion', 'source_validity'})
        self.assertEqual(status['publication'], 'published')
        self.assertEqual(status['assertion'], 'unverified',
                         'published 不得被写成 verified（禁令 1）')
        self.assertEqual(status['source_validity'], 'stale',
                         'verified/current 互不推导（禁令 2/3）')
        self.assertNotIn('overall', status)
        self.assertNotIn('combined', status)

    def test_an_unmeasured_source_validity_stays_null_and_is_never_assumed_current(self):
        status = wiki.status_dimensions(publication='draft', assertion='unverified')
        self.assertIsNone(status['source_validity'],
                          '读取时复查还没做 → 必须是"未评估"，不能默认 current')
        with self.assertRaises(HTTPException):
            wiki.status_dimensions(publication='draft', assertion='overall_verified')
        with self.assertRaises(HTTPException):
            wiki.status_dimensions(publication='draft', source_validity='probably-fine')

    def test_assertion_aggregation_stays_inside_its_own_dimension(self):
        self.assertEqual(wiki.aggregate_assertion_status([]), 'unverified',
                         '没有主张不是"已核验"')
        self.assertEqual(wiki.aggregate_assertion_status(['verified', 'verified']), 'verified')
        self.assertEqual(wiki.aggregate_assertion_status(['verified', 'unverified']),
                         'unverified')
        self.assertEqual(wiki.aggregate_assertion_status(['verified', 'insufficient']),
                         'insufficient')
        self.assertEqual(wiki.aggregate_assertion_status(['insufficient', 'conflicted']),
                         'conflicted')
        self.assertEqual(wiki.aggregate_assertion_status(['bogus']), 'unverified')


class WikiRevisionGuardTest(unittest.TestCase):
    """§5.12 的版本规则：上限、重新核验、附录式审计、错误码映射。"""

    def test_size_limits_are_refused_not_truncated(self):
        self.assertEqual(wiki.revision_size_guards(body_markdown='x' * 10, assertion_count=200,
                                                   relation_count=100, citation_count=300), [])
        self.assertEqual(wiki.revision_size_guards(body_markdown='x' * 262145),
                         ['body_markdown'])
        self.assertEqual(wiki.revision_size_guards(assertion_count=201), ['assertions'])
        self.assertEqual(wiki.revision_size_guards(relation_count=101), ['relations'])
        self.assertEqual(wiki.revision_size_guards(citation_count=301), ['citations'])
        # 上限是**字节**而不是字符：中文正文按 UTF-8 计（262144/3 = 87381 个汉字）。
        self.assertEqual(wiki.revision_size_guards(body_markdown='中' * 87381), [])
        self.assertEqual(wiki.revision_size_guards(body_markdown='中' * 87382),
                         ['body_markdown'])

    def test_only_byte_identical_assertions_may_carry_the_baseline_verification(self):
        base = [{'comparison_key': 'k1', 'entity_key': {'part_id': 'A102'},
                 'predicate': 'holding_temperature', 'raw_value': '870', 'raw_unit': 'degC',
                 'normalized_value': '870.000', 'normalized_unit': 'degC',
                 'time_scope': None, 'applicability': None, 'record_scope': None,
                 'statistic': 'core', 'evidence_refs': ['wev-' + '1' * 24]}]
        identical = [dict(base[0])]
        deltas = wiki.assertion_deltas(base, identical)
        self.assertEqual((deltas['carried'], deltas['changed'], deltas['new']),
                         ([0], [], []))
        self.assertFalse(deltas['revalidation_required'])
        changed_value = [dict(base[0], raw_value='850')]
        deltas = wiki.assertion_deltas(base, changed_value)
        self.assertEqual(deltas['changed'], [0])
        self.assertTrue(deltas['revalidation_required'],
                        '§5.12 规则 2：人工改了数值 → 必须重新核验')
        changed_unit = [dict(base[0], normalized_unit='K')]
        self.assertEqual(wiki.assertion_deltas(base, changed_unit)['changed'], [0])
        changed_scope = [dict(base[0], record_scope={'batch_id': 'B002'})]
        self.assertEqual(wiki.assertion_deltas(base, changed_scope)['changed'], [0])
        self.assertEqual(wiki.assertion_deltas(base, [])['removed'], ['k1'])
        self.assertTrue(wiki.assertion_deltas(base, [])['revalidation_required'])
        brand_new = [dict(base[0], comparison_key='k2')]
        deltas = wiki.assertion_deltas(base, brand_new)
        self.assertEqual((deltas['new'], deltas['changed']), ([0], []))
        self.assertTrue(deltas['revalidation_required'])
        # 引用变化也算内容变化（021:654 把 evidence_refs 列在不可改写清单里）。
        changed_refs = [dict(base[0], evidence_refs=['wev-' + '2' * 24])]
        self.assertEqual(wiki.assertion_deltas(base, changed_refs)['changed'], [0])

    def test_verified_needs_the_six_items(self):
        basis = {'method': 'human_review', 'coverage': 'A102 / holding_temperature / 2026-09',
                 'rule_version': 'industrial-rules-v1'}
        self.assertEqual(wiki.verification_gaps(basis=basis,
                                                scope={'assertion_ids': ['a'], 'full_revision':
                                                       False}), [])
        self.assertEqual(wiki.verification_gaps(
            basis={'method': 'nope', 'coverage': '', 'rule_version': None},
            scope={'assertion_ids': [], 'full_revision': False}),
            ['scope', 'method', 'coverage', 'rule_version'])

    def test_the_five_accounts_are_recorded_separately_and_semantic_support_is_frozen(self):
        accounting = wiki.verification_accounting(
            source_integrity={'checked': True, 'value': 'sha256 一致'},
            semantic_support={'checked': True, 'full_semantic_entailment': 'entailed'})
        self.assertEqual(set(accounting), set(wiki.ACCOUNTING_KEYS))
        self.assertEqual(accounting['source_integrity'],
                         {'checked': True, 'value': 'sha256 一致'})
        self.assertEqual(accounting['semantic_support'],
                         {'checked': False,
                          'full_semantic_entailment': 'not_independently_verified'},
                         'agent.py:238/:629 的固定字面量，Wiki 不得改写')
        for key in ('entity_binding', 'rule_verification', 'source_authority'):
            self.assertEqual(accounting[key], {'checked': False},
                             '"未记"必须看得见，不能默认成通过')
        with self.assertRaises(HTTPException) as caught:
            wiki.verification_accounting(source_integrity='checked')
        self.assertEqual(code_of(caught.exception), 'WIKI_VERIFICATION_SCOPE_REQUIRED')

    def test_review_matrix_maps_actions_to_assertion_states(self):
        self.assertIsNone(wiki.review_verdict_status('submit_review', 'verified'),
                          '提交复核不改主张状态')
        self.assertEqual(wiki.review_verdict_status('approve_facts', 'verified'), 'verified')
        self.assertEqual(wiki.review_verdict_status('reject_facts', 'conflicted'), 'conflicted')
        self.assertEqual(wiki.review_verdict_status('request_changes', None), 'unverified')
        with self.assertRaises(HTTPException) as caught:
            wiki.review_verdict_status('reject_facts', 'verified')
        self.assertEqual(caught.exception.status_code, 422)
        with self.assertRaises(HTTPException):
            wiki.review_verdict_status('publish', None)

    def test_review_actions_move_the_version_and_only_an_unpublished_entry(self):
        self.assertEqual(wiki.revision_publication_after_review('draft', 'submit_review'),
                         'in_review')
        self.assertEqual(wiki.revision_publication_after_review('in_review', 'request_changes'),
                         'draft')
        self.assertIsNone(wiki.revision_publication_after_review('published', 'request_changes'),
                          '已发布版本不因后续复核退回（历史不可改写）')
        self.assertIsNone(wiki.revision_publication_after_review('draft', 'approve_facts'))
        self.assertEqual(wiki.entry_publication_after_review('draft', None, 'submit_review'),
                         'in_review')
        self.assertIsNone(wiki.entry_publication_after_review('published', REV1, 'submit_review'),
                          '已发布词条的候选版本进入复核，不得把词条拉回 in_review')
        self.assertEqual(wiki.entry_publication_after_review('in_review', None, 'request_changes'),
                         'draft')

    def test_jsonb_unset_is_sql_null_and_a_json_null_literal_is_refused(self):
        self.assertIsNone(wiki.jsonb_or_null(None, field='time_scope'))
        self.assertIsNone(wiki.jsonb_or_null('   ', field='time_scope'))
        self.assertEqual(wiki.jsonb_or_null({'from': 'x'}, field='time_scope'), {'from': 'x'})
        for bad in ('null', 'NULL', [1], 1):
            with self.assertRaises(HTTPException) as caught:
                wiki.jsonb_or_null(bad, field='time_scope')
            self.assertEqual(caught.exception.status_code, 422, bad)
        with self.assertRaises(HTTPException):
            wiki.jsonb_or_null(None, field='entity_key', required=True)

    def test_append_only_sqlstates_map_to_the_contract_error_codes(self):
        self.assertEqual(wiki.append_only_error_code(FakePgError(WIKI1)),
                         'WIKI_VERSION_CONFLICT')
        self.assertEqual(wiki.append_only_error_code(FakePgError(WIKI2)),
                         'WIKI_VERSION_CONFLICT')
        self.assertEqual(wiki.append_only_error_code(FakePgError(WIKI3)),
                         'WIKI_IDEMPOTENCY_CONFLICT')
        self.assertEqual(wiki.append_only_error_code(FakePgError(WIKI4)),
                         'WIKI_VERSION_CONFLICT')
        self.assertIsNone(wiki.append_only_error_code(FakePgError('23505')))
        with self.assertRaises(HTTPException) as caught:
            wiki.raise_append_only(FakePgError(WIKI1))
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(code_of(caught.exception), 'WIKI_VERSION_CONFLICT')
        self.assertIsNone(wiki.raise_append_only(FakePgError('42P01')),
                          '非触发器错误不由本函数处理，调用方必须原样 re-raise')
        source = (ROOT / 'datamind' / 'wiki.py').read_text(encoding='utf-8')
        self.assertIn('raise_append_only(exc)\n        raise', source,
                      '每个捕获点都必须在映射之后 re-raise')

    def test_relation_shape_enforces_the_vocabulary_classes_and_direction(self):
        other = 'wiki-' + 'c' * 24
        row = wiki.relation_shape(src=ENTRY, dst=other, relation_type='supports')
        self.assertEqual(row['origin'], 'projected')
        self.assertEqual(row['raw_type'], 'supports')
        self.assertFalse(row['is_symmetric'])
        self.assertIsNone(row['derivation'])
        derived = wiki.relation_shape(src=ENTRY, dst=other, relation_type='measures')
        self.assertEqual(derived['origin'], 'wiki_derived')
        self.assertIsNone(derived['raw_type'])
        self.assertEqual(derived['rule_version'], wiki.DERIVATION_RULE_VERSION)
        self.assertIn('rule_version', derived['derivation'])
        symmetric = wiki.relation_shape(src=other, dst=ENTRY, relation_type='conflicts')
        self.assertTrue(symmetric['is_symmetric'])
        self.assertEqual((symmetric['src_id'], symmetric['dst_id']), (ENTRY, other),
                         '无向关系按端点 id 规范化存储（021:458）')
        self.assertEqual(row['provenance_incomplete'], {'applies': False})
        for bad in ({'src': ENTRY, 'dst': ENTRY, 'relation_type': 'supports'},
                    {'src': ENTRY, 'dst': other, 'relation_type': 'nearest_guess'},
                    {'src': ENTRY, 'dst': other, 'relation_type': 'supports',
                     'origin': 'wiki_derived'},
                    {'src': ENTRY, 'dst': other, 'relation_type': 'measures',
                     'raw_type': 'measures'},
                    {'src': ENTRY, 'dst': other, 'relation_type': 'measures',
                     'provenance_incomplete': {'missing': []}}):
            with self.assertRaises(HTTPException):
                wiki.relation_shape(**bad)


class WikiAuthorizationTest(unittest.TestCase):
    """WIKI_API.md §3：扁平角色、admin 旁路、reviewer/approver 互不继承、别人的东西 = 404。"""

    def test_roles_are_flat_and_admin_is_the_only_bypass(self):
        reviewer = {'user_id': 'u', 'roles': ['reviewer']}
        approver = {'user_id': 'u', 'roles': ['approver']}
        viewer = {'user_id': 'u', 'roles': ['viewer']}
        admin = {'user_id': 'u', 'roles': ['admin']}
        self.assertEqual(wiki.require_permission(reviewer, 'reviewer'), 'u')
        self.assertEqual(wiki.require_permission(admin, 'reviewer'), 'u')
        self.assertEqual(wiki.require_permission(admin, 'approver'), 'u')
        self.assertEqual(wiki.require_permission(viewer, None), 'u')
        with self.assertRaises(HTTPException) as caught:
            wiki.require_permission(approver, 'reviewer')
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(code_of(caught.exception), 'WIKI_PERMISSION_DENIED')
        with self.assertRaises(HTTPException) as caught:
            wiki.require_permission(reviewer, 'approver')
        self.assertEqual(code_of(caught.exception), 'WIKI_PERMISSION_DENIED',
                         'approver 不得隐含 reviewer，反之亦然')
        with self.assertRaises(HTTPException) as caught:
            wiki.require_permission({}, 'reviewer')
        self.assertEqual(caught.exception.status_code, 401)

    def test_drafts_are_visible_to_the_author_reviewer_approver_and_admin_only(self):
        author = {'user_id': 'author', 'roles': ['reviewer']}
        self.assertTrue(wiki.may_read_restricted(author, 'author'))
        self.assertTrue(wiki.may_read_restricted({'user_id': 'x', 'roles': ['reviewer']},
                                                 'author'))
        self.assertTrue(wiki.may_read_restricted({'user_id': 'x', 'roles': ['approver']},
                                                 'author'),
                        'approver 必须能读草稿，否则 #14 不可用')
        self.assertTrue(wiki.may_read_restricted({'user_id': 'x', 'roles': ['admin']}, 'author'))
        self.assertFalse(wiki.may_read_restricted({'user_id': 'x', 'roles': ['viewer']},
                                                  'author'))
        self.assertFalse(wiki.may_read_restricted(None, 'author'))

    def test_another_users_space_is_a_404_not_a_403(self):
        identity = {'user_id': 'user-a', 'roles': ['reviewer']}
        own = wiki.resolve_space(identity)
        self.assertEqual(wiki.resolve_space(identity, own), own)
        with self.assertRaises(HTTPException) as caught:
            wiki.resolve_space(identity, wiki.space_for_user('user-b'))
        self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(code_of(caught.exception), 'WIKI_SPACE_NOT_FOUND')
        with self.assertRaises(HTTPException) as caught:
            wiki.resolve_space(identity, 'wsp-not-hex')
        self.assertEqual((caught.exception.status_code, code_of(caught.exception)),
                         (404, 'WIKI_SPACE_NOT_FOUND'),
                         '畸形空间 id 与"别人的空间"返回同一个 404（存在性隐藏）')
        admin = {'user_id': 'user-a', 'roles': ['admin']}
        other = wiki.space_for_user('user-b')
        self.assertEqual(wiki.resolve_space(admin, other), other, 'admin 是超管旁路')

    def test_nul_bytes_never_reach_psycopg2(self):
        with self.assertRaises(HTTPException) as caught:
            wiki._text('a\x00b', 'title')
        self.assertEqual(code_of(caught.exception), 'WIKI_BUILD_REQUEST_INVALID')


class WikiSourceBoundaryTest(unittest.TestCase):
    """静态断言：这个模块**不可能**违反的几条硬边界（读源码文本，不需要数据库）。"""

    SOURCE = (ROOT / 'datamind' / 'wiki.py').read_text(encoding='utf-8')

    def test_the_review_trail_is_append_only_in_this_module(self):
        self.assertNotIn('UPDATE wiki_review_events', self.SOURCE)
        self.assertIn('INSERT INTO wiki_review_events', self.SOURCE)
        self.assertNotIn('DELETE FROM', self.SOURCE)

    def test_a_saved_revision_is_never_overwritten(self):
        for column in ('body_markdown', 'citations', 'base_revision_id', 'body_sha256'):
            self.assertNotIn('SET %s' % column, self.SOURCE,
                             '%s 属于不可改写载荷（WIKI1）' % column)
        self.assertIn("SET publication_status = %s", self.SOURCE,
                      '版本只允许改自己那一行的流程状态')

    def test_publication_is_an_atomic_compare_and_swap_on_the_head(self):
        self.assertIn('head_revision_id IS NOT DISTINCT FROM %s', self.SOURCE)
        self.assertIn('if updated is None', self.SOURCE)
        self.assertIn("fail('WIKI_VERSION_CONFLICT', 409", self.SOURCE)

    def test_no_statement_in_this_module_writes_tables_owned_by_postgres(self):
        for table in ('runs', 'claims', 'trace_events', 'conflict_records',
                      'evidence_nodes', 'evidence_edges'):
            self.assertNotRegex(self.SOURCE,
                                r'(INSERT INTO|UPDATE|DELETE FROM)\s+%s\b' % table,
                                'Wiki 不得写 %s（021:24-35 的边界）' % table)

    def test_read_paths_never_use_the_readonly_role_for_wiki_tables(self):
        self.assertNotIn('connect(read_dsn', self.SOURCE)
        self.assertNotIn('connect(wiki_read_dsn', self.SOURCE)
        self.assertIn('def wiki_dsn() -> str:', self.SOURCE)

    def test_every_frozen_error_code_of_the_write_paths_exists_in_the_module(self):
        for error_code in ('WIKI_PERMISSION_DENIED', 'WIKI_ENTRY_NOT_FOUND',
                           'WIKI_REVISION_NOT_FOUND', 'WIKI_SPACE_NOT_FOUND',
                           'WIKI_VERSION_CONFLICT', 'WIKI_ENTRY_IDENTITY_PENDING',
                           'WIKI_REVIEW_REQUIRED', 'WIKI_REFERENCE_INVALID',
                           'WIKI_REVISION_TOO_LARGE', 'WIKI_VERIFICATION_SCOPE_REQUIRED',
                           'WIKI_BUILD_REQUEST_INVALID'):
            self.assertIn(error_code, self.SOURCE)

    def test_the_document_and_migration_citations_this_module_relies_on_still_hold(self):
        api = (ROOT / 'docs' / 'WIKI_API.md').read_text(encoding='utf-8')
        domain = (ROOT / 'docs' / 'WIKI_DOMAIN.md').read_text(encoding='utf-8')
        migration = (ROOT / 'db' / 'migrations' / '021_wiki_knowledge.sql').read_text(
            encoding='utf-8')
        self.assertIn('WIKI_VERSION_CONFLICT', api)
        self.assertIn('WIKI_IDEMPOTENCY_CONFLICT', api)
        self.assertIn('五项分别记账，不互相替代', domain)
        # 本模块依赖的 4 个 SQLSTATE 必须在迁移里仍然存在。
        for state in (WIKI1, WIKI2, WIKI3, WIKI4):
            self.assertIn("ERRCODE = '%s'" % state, migration)


@unittest.skipUnless(DB, 'psycopg2 或 datamind_meta 不可用（数据库用例需要 .venv 解释器）')
class WikiDatabaseContractTest(unittest.TestCase):
    """真库探针：**全部在一个 ROLLBACK 事务里**，验证服务层依赖的数据库契约。"""

    def setUp(self):
        self.entry_id = wiki.entry_id()
        self.revision_id = REV1
        self.assertNotEqual(self.entry_id, self.revision_id)

    def _owner(self, cur):
        cur.execute('SELECT user_id FROM auth_users ORDER BY user_id LIMIT 1')
        row = cur.fetchone()
        if not row:
            self.skipTest('库里没有任何 auth_users 行，无法建词条')
        return row['user_id']

    def _entry(self, cur, *, key_part='k-1', confirmation='confirmed',
               publication_status='draft', head=None, entry_type='metric'):
        owner = self._owner(cur)
        cur.execute('''INSERT INTO wiki_entries(entry_id, space_id, owner_id, entry_type,
                namespace, key_part, confirmation, title, publication_status, head_revision_id)
            VALUES (%s,%s,%s,%s,'default',%s,%s,'t',%s,%s) RETURNING entry_id''',
                    (self.entry_id, wiki.space_for_user(owner), owner, entry_type, key_part,
                     confirmation, publication_status, head))
        return owner

    def _revision(self, cur, entry_id_value, revision_id_value, *, base=None):
        cur.execute('''INSERT INTO wiki_revisions(revision_id, entry_id, base_revision_id, title,
                body_markdown, body_sha256, citations, author_id, generation_method)
            VALUES (%s,%s,%s,'t','body',%s,'[]'::jsonb,%s,'human') RETURNING revision_id''',
                    (revision_id_value, entry_id_value, base, wiki.sha256_text('body'),
                     self._owner(cur)))
        return revision_id_value

    def test_the_wiki1_trigger_refuses_an_in_place_body_overwrite(self):
        with app_transaction() as cur:
            self._entry(cur)
            self._revision(cur, self.entry_id, self.revision_id)
            with self.assertRaises(psycopg2.Error) as caught:
                cur.execute('''UPDATE wiki_revisions SET body_markdown='other'
                    WHERE revision_id=%s''', (self.revision_id,))
            self.assertEqual(wiki.append_only_error_code(caught.exception), 'WIKI_VERSION_CONFLICT',
                             'SQLSTATE 必须是 WIKI1 且能被服务层映射（021:112-114）')

    def test_the_wiki2_trigger_refuses_both_update_and_delete(self):
        for statement in ('UPDATE wiki_review_events SET note=\'x\' WHERE review_event_id=%s',
                          'DELETE FROM wiki_review_events WHERE review_event_id=%s'):
            with app_transaction() as cur:
                owner = self._entry(cur)
                self._revision(cur, self.entry_id, self.revision_id)
                event_id = wiki.review_event_id()
                cur.execute('''INSERT INTO wiki_review_events(review_event_id, entry_id,
                        revision_id, action, actor_user_id, scope, basis)
                    VALUES (%s,%s,%s,'submit_review',%s,'{}'::jsonb,'{}'::jsonb)''',
                            (event_id, self.entry_id, self.revision_id, owner))
                with self.assertRaises(psycopg2.Error) as caught:
                    cur.execute(statement, (event_id,))
                self.assertEqual(wiki.append_only_error_code(caught.exception),
                                 'WIKI_VERSION_CONFLICT', statement)

    def test_the_wiki4_trigger_refuses_a_content_rewrite_but_allows_the_verification_columns(self):
        with app_transaction() as cur:
            self._entry(cur)
            self._revision(cur, self.entry_id, self.revision_id)
            assertion = wiki.assertion_id()
            cur.execute('''INSERT INTO wiki_assertions(assertion_id, entry_id, revision_id,
                    comparison_key, entity_key, predicate, raw_value, raw_unit)
                VALUES (%s,%s,%s,%s,'{"part_id":"A102"}'::jsonb,'holding_temperature','870','degC')''',
                        (assertion, self.entry_id, self.revision_id,
                         wiki.comparison_key_of(space_id=SPACE, entity_key={'part_id': 'A102'},
                                                predicate='holding_temperature')))
            with self.assertRaises(psycopg2.Error) as caught:
                cur.execute('''UPDATE wiki_assertions SET raw_value='850' WHERE assertion_id=%s''',
                            (assertion,))
            self.assertEqual(wiki.append_only_error_code(caught.exception), 'WIKI_VERSION_CONFLICT')

    def test_only_the_verification_columns_of_an_assertion_are_mutable(self):
        with app_transaction() as cur:
            self._entry(cur)
            self._revision(cur, self.entry_id, self.revision_id)
            assertion = wiki.assertion_id()
            cur.execute('''INSERT INTO wiki_assertions(assertion_id, entry_id, revision_id,
                    comparison_key, entity_key, predicate) VALUES (%s,%s,%s,%s,'{}'::jsonb,'p')''',
                        (assertion, self.entry_id, self.revision_id, 'a' * 64))
            cur.execute('''UPDATE wiki_assertions SET assertion_status='insufficient'
                WHERE assertion_id=%s RETURNING assertion_status''', (assertion,))
            self.assertEqual(cur.fetchone()['assertion_status'], 'insufficient')

    def test_a_bare_verified_assertion_is_refused_by_the_database(self):
        with app_transaction() as cur:
            self._entry(cur)
            self._revision(cur, self.entry_id, self.revision_id)
            with self.assertRaises(psycopg2.Error) as caught:
                cur.execute('''INSERT INTO wiki_assertions(assertion_id, entry_id, revision_id,
                        comparison_key, entity_key, predicate, assertion_status)
                    VALUES (%s,%s,%s,%s,'{}'::jsonb,'p','verified')''',
                            (wiki.assertion_id(), self.entry_id, self.revision_id, 'b' * 64))
            self.assertEqual(getattr(caught.exception, 'pgcode', None), '23514',
                             'CHECK：verified 必须有六项与五项记账（021:289-300）')

    def test_a_pending_entity_key_can_never_be_stored_as_confirmed(self):
        with app_transaction() as cur:
            owner = self._owner(cur)
            with self.assertRaises(psycopg2.Error) as caught:
                cur.execute('''INSERT INTO wiki_entries(entry_id, space_id, owner_id, entry_type,
                        namespace, key_part, confirmation, title)
                    VALUES (%s,%s,%s,'business_entity','default','pending:x','confirmed','t')''',
                            (wiki.entry_id(), wiki.space_for_user(owner), owner))
            self.assertEqual(getattr(caught.exception, 'pgcode', None), '23514')

    def test_the_publication_head_cas_is_rowcount_based(self):
        with app_transaction() as cur:
            self._entry(cur)
            second = REV2
            self._revision(cur, self.entry_id, self.revision_id)
            self._revision(cur, self.entry_id, second, base=self.revision_id)
            cur.execute('''UPDATE wiki_entries SET head_revision_id=%s,
                    publication_status='published'
                WHERE entry_id=%s AND head_revision_id IS NOT DISTINCT FROM %s''',
                        (self.revision_id, self.entry_id, None))
            self.assertEqual(cur.rowcount, 1, '首版发布：预期头为空')
            cur.execute('''UPDATE wiki_entries SET head_revision_id=%s
                WHERE entry_id=%s AND head_revision_id IS NOT DISTINCT FROM %s''',
                        (second, self.entry_id, None))
            self.assertEqual(cur.rowcount, 0, '过时基线必须一行都不改（§5.14 规则 1）')
            cur.execute('SELECT head_revision_id FROM wiki_entries WHERE entry_id=%s',
                        (self.entry_id,))
            self.assertEqual(cur.fetchone()['head_revision_id'], self.revision_id)

    def test_the_composite_foreign_key_refuses_a_cross_entry_publication_head(self):
        other_entry = wiki.entry_id()
        with app_transaction() as cur:
            owner = self._entry(cur)
            self._revision(cur, self.entry_id, self.revision_id)
            cur.execute('''INSERT INTO wiki_entries(entry_id, space_id, owner_id, entry_type,
                    namespace, key_part, confirmation, title)
                VALUES (%s,%s,%s,'metric','default','k-2','confirmed','t2')''',
                        (other_entry, wiki.space_for_user(owner), owner))
            with self.assertRaises(psycopg2.Error) as caught:
                cur.execute('UPDATE wiki_entries SET head_revision_id=%s WHERE entry_id=%s',
                            (self.revision_id, other_entry))
            self.assertEqual(getattr(caught.exception, 'pgcode', None), '23503',
                             '发布头必须同词条（复合外键，021:722-724）')

    def test_the_composite_foreign_key_refuses_a_cross_entry_baseline(self):
        other_entry = wiki.entry_id()
        with app_transaction() as cur:
            owner = self._entry(cur)
            self._revision(cur, self.entry_id, self.revision_id)
            cur.execute('''INSERT INTO wiki_entries(entry_id, space_id, owner_id, entry_type,
                    namespace, key_part, confirmation, title)
                VALUES (%s,%s,%s,'metric','default','k-2','confirmed','t2')''',
                        (other_entry, wiki.space_for_user(owner), owner))
            with self.assertRaises(psycopg2.Error) as caught:
                cur.execute('''INSERT INTO wiki_revisions(revision_id, entry_id,
                        base_revision_id, title, body_markdown, body_sha256, citations,
                        author_id, generation_method)
                    VALUES (%s,%s,%s,'t','b',%s,'[]'::jsonb,%s,'human')''',
                            (wiki.revision_id(), other_entry, self.revision_id,
                             wiki.sha256_text('b'), owner))
            self.assertEqual(getattr(caught.exception, 'pgcode', None), '23503',
                             '基线必须同词条（复合外键，021:763-766）')

    def test_an_unknown_entry_publication_head_column_order_is_not_the_guard(self):
        """反向对照：同词条的头是允许的，所以上面两个 23503 不是"所有 UPDATE 都失败"。"""
        with app_transaction() as cur:
            self._entry(cur)
            self._revision(cur, self.entry_id, self.revision_id)
            cur.execute('''UPDATE wiki_entries SET head_revision_id=%s,
                    publication_status='published'
                WHERE entry_id=%s AND head_revision_id IS NULL''',
                        (self.revision_id, self.entry_id))
            self.assertEqual(cur.rowcount, 1)

    def test_readonly_user_cannot_read_any_wiki_table(self):
        """这就是"Wiki 全部读写都走应用角色"的原因（021:605-607 的 REVOKE）。"""
        try:
            conn = psycopg2.connect(wiki.read_dsn())
        except Exception as exc:  # pragma: no cover - 只读角色没配好时不算失败
            self.skipTest('readonly_user 连不上：%s' % exc)
        try:
            cur = conn.cursor()
            with self.assertRaises(psycopg2.Error) as caught:
                cur.execute('SELECT count(*) FROM wiki_entries')
            self.assertEqual(getattr(caught.exception, 'pgcode', None), '42501',
                             'readonly_user 必须拿到 InsufficientPrivilege')
        finally:
            conn.rollback()
            conn.close()


class SharedTransaction:
    """`with wiki.connect(dsn) as conn` 的替身：整条流程共用一个连接、**永不提交**。

    * `__enter__` 打一个 SAVEPOINT；`__exit__` 成功就 RELEASE、失败就 ROLLBACK TO。
      这样"某一步 403"不会连带撤销前面已经成功的步骤（真实的 `with conn` 会整段回滚），
      而单个步骤的失败仍然只影响它自己 —— 端到端用例才能在一次事务里走完整条流程。
    * 真正的回滚发生在用例结束时的那一次 `conn.rollback()`：8 张 wiki 表回到 0 行。
    """

    SAVEPOINT = 'wiki_service_call'

    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        with self._conn.cursor() as cur:
            cur.execute('SAVEPOINT %s' % self.SAVEPOINT)
        return self._conn

    def __exit__(self, exc_type, exc, traceback):
        with self._conn.cursor() as cur:
            if exc_type is None:
                cur.execute('RELEASE SAVEPOINT %s' % self.SAVEPOINT)
            else:
                cur.execute('ROLLBACK TO SAVEPOINT %s' % self.SAVEPOINT)
                cur.execute('RELEASE SAVEPOINT %s' % self.SAVEPOINT)
        return False


@unittest.skipUnless(DB, 'psycopg2 或 datamind_meta 不可用（端到端用例需要 .venv 解释器）')
class WikiEndToEndRollbackTest(unittest.TestCase):
    """真实 SQL 走一遍：建词条 → 建版本 → 复核 → 建新版本（重新核验）→ CAS 发布。

    连接被换成**单事务 + 末尾 ROLLBACK**（见 `SharedTransaction`），所以这条端到端路径验证的是
    真表、真触发器、真 CHECK，而库里仍然一行不留。用例先建一个真实 `auth_users`/`auth_sessions`
    行，`lock_identity` 才能像生产一样复查并锁定身份。
    """

    def setUp(self):
        if psycopg2 is None:  # pragma: no cover - skipUnless 已经挡住
            self.skipTest('psycopg2 不可用')
        import hashlib
        import uuid
        self.dsn = wiki.wiki_dsn()
        self.conn = psycopg2.connect(self.dsn)
        real_connect = wiki.connect
        wiki.connect = lambda dsn: SharedTransaction(self.conn)
        self.addCleanup(setattr, wiki, 'connect', real_connect)
        self.addCleanup(self._rollback)

        suffix = uuid.uuid4().hex[:8]
        self.user_id = 'user-wiki-e2e-' + suffix
        token = 'wiki-e2e-' + suffix
        self.session_hash = hashlib.sha256(token.encode()).hexdigest()
        with self.conn.cursor() as cur:
            cur.execute('''INSERT INTO auth_users(user_id, username, display_name, password_hash,
                    roles) VALUES (%s,%s,'E2E','scrypt$00$00',ARRAY['reviewer','approver'])''',
                        (self.user_id, 'wiki-e2e-' + suffix))
            cur.execute('''INSERT INTO auth_sessions(token_hash, user_id, csrf_token, expires_at)
                VALUES (%s,%s,'csrf', now() + interval '1 hour')''',
                        (self.session_hash, self.user_id))
        self.reviewer = {'user_id': self.user_id, 'display_name': 'E2E', 'roles': ['reviewer'],
                         '_session_hash': self.session_hash}
        self.approver = {**self.reviewer, 'roles': ['reviewer', 'approver']}

        self.entry = wiki.create_entry({'entry_type': 'metric', 'title': 'holding_temperature',
                                        'metric_id': 'holding_temperature',
                                        'namespace': 'industrial-demo',
                                        'summary': '合成演示指标'}, self.dsn,
                                       self.approver)['entry']
        self.other_entry = wiki.create_entry({'entry_type': 'metric', 'title': 'demo_retail.sales_amount',
                                              'metric_id': 'demo_retail.sales_amount',
                                              'namespace': 'industrial-demo'}, self.dsn,
                                             self.approver)['entry']
        self.assertions = [
            {'metric': 'holding_temperature', 'entity_key': {'part_id': 'A102'},
             'raw_value': '870', 'raw_unit': 'degC', 'normalized_value': '870.000',
             'normalized_unit': 'degC', 'record_scope': {'batch_id': 'B001'}, 'statistic': 'core'},
            {'metric': 'holding_temperature', 'entity_key': {'part_id': 'A102'},
             'raw_value': '870', 'raw_unit': 'degC', 'normalized_value': '870.000',
             'normalized_unit': 'degC', 'record_scope': {'batch_id': 'B002'}, 'statistic': 'core'}]
        self.v1 = wiki.create_revision(
            self.entry['entry_id'],
            {'request_id': 'rev-1', 'base_revision_id': None, 'title': '设备 A102 的温度要求',
             'body_markdown': '按 A102-V2 的温度限值。[1]', 'change_note': '首版',
             'assertions': self.assertions,
             'relations': [{'src': self.entry['entry_id'], 'dst': self.other_entry['entry_id'],
                            'relation_type': 'supports', 'basis_note': '同源共现'}]},
            self.dsn, self.approver)['revision']

    def _rollback(self):
        try:
            self.conn.rollback()
        finally:
            self.conn.close()

    def _review(self, revision_id, *, action='approve_facts', verdict='verified', basis=None,
                expected_head=None, scope=None):
        return wiki.submit_review(
            self.entry['entry_id'],
            {'request_id': 'review-%s-%s' % (revision_id[-6:], action),
             'revision_id': revision_id,
             'action': action,
             'expected_head_revision_id': expected_head,
             'scope': scope or {'full_revision': True},
             'verdict': {'assertion': verdict},
             'basis': basis or {'rule_version': 'industrial-rules-v1',
                                'method': 'human_review',
                                'coverage': 'A102 / holding_temperature / 2026-09',
                                'evidence_ref_ids': []}},
            self.dsn, self.reviewer)

    # ---------------------------------------------------------------- 主流程

    def test_the_entry_and_the_first_version_land_with_the_frozen_shapes(self):
        self.assertTrue(self.entry['entry_id'].startswith('wiki-'))
        self.assertEqual(self.entry['confirmation'], 'confirmed')
        self.assertEqual(self.entry['business_key']['key_part'], 'holding_temperature')
        self.assertEqual(self.entry['status'],
                         {'publication': 'draft', 'assertion': 'unverified',
                          'source_validity': None},
                         '三套状态恒为三个字段；未评估的来源有效性不得默认成 current')
        self.assertIsNone(self.v1['base_revision_id'])
        self.assertEqual(len(self.v1['assertions']), 2)
        self.assertEqual([a['verification']['status'] for a in self.v1['assertions']],
                         ['unverified', 'unverified'])
        self.assertEqual(self.v1['assertions'][0]['comparison_key'][:7], 'sha256:')
        relation = wiki.revision_detail(self.entry['entry_id'], self.v1['revision_id'],
                                        self.dsn, self.reviewer)['relations'][0]
        self.assertEqual((relation['relation_type'], relation['origin'], relation['raw_type'],
                          relation['direction'], relation['supported']),
                         ('supports', 'projected', 'supports', 'directed', True))

    def test_a_review_resets_and_then_records_the_verified_accounting(self):
        result = self._review(self.v1['revision_id'])
        self.assertEqual(result['review_event']['action'], 'approve_facts')
        self.assertEqual([a['verification']['status'] for a in result['revision']['assertions']],
                         ['verified', 'verified'])
        accounting = result['revision']['assertions'][0]['verification']['accounting']
        self.assertEqual(set(accounting), set(wiki.ACCOUNTING_KEYS))
        self.assertEqual(accounting['semantic_support'],
                         {'checked': False,
                          'full_semantic_entailment': 'not_independently_verified'})
        self.assertEqual(result['revision']['publication_status'], 'draft',
                         'approve_facts 是裁决，不是提交复核：版本状态停在它原来的位置')
        self.assertEqual(result['entry']['status']['publication'], 'draft',
                         '还没有发布头，也没有 submit_review → 词条仍是 draft')

    def test_a_verified_review_needs_the_scope_and_the_six_items(self):
        with self.assertRaises(HTTPException) as caught:
            self._review(self.v1['revision_id'], basis={'method': 'human_review'})
        self.assertEqual((caught.exception.status_code, code_of(caught.exception)),
                         (422, 'WIKI_VERIFICATION_SCOPE_REQUIRED'))

    def test_a_changed_assertion_is_reset_while_an_identical_one_carries_the_verification(self):
        verified = self._review(self.v1['revision_id'])
        by_scope = {a['record_scope']['batch_id']: a for a in verified['revision']['assertions']}
        self.assertEqual(by_scope['B001']['verification']['status'], 'verified')
        second = wiki.create_revision(
            self.entry['entry_id'],
            {'request_id': 'rev-2', 'base_revision_id': self.v1['revision_id'],
             'title': '设备 A102 的温度要求（按 B004 重算）',
             'body_markdown': 'B004 批次的温度按 850 记录。[1]', 'change_note': '按 B004 批次重算',
             'assertions': [dict(self.assertions[0], raw_value='870'),
                            dict(self.assertions[1], raw_value='850',
                                 normalized_value='850.000')]},
            self.dsn, self.approver)
        self.assertTrue(second['revalidation_required'],
                        '§5.12 规则 2：人工改了数值 → 必须重新核验')
        # 读回来的主张按 comparison_key 排序，所以按记录口径对齐，而不是按下标。
        by_scope = {a['record_scope']['batch_id']: a for a in second['revision']['assertions']}
        self.assertEqual(second['revalidation']['carried_verification'],
                         [by_scope['B001']['assertion_id']])
        self.assertEqual(second['revalidation']['changed'], [by_scope['B002']['assertion_id']])
        self.assertEqual(by_scope['B001']['verification']['status'], 'verified')
        self.assertEqual(by_scope['B002']['verification']['status'], 'unverified',
                         '只有逐字段相同的主张才带过核验；改过的那条从 unverified 起算')
        self.assertEqual(by_scope['B002']['raw_value'], '850')

    def test_a_stale_baseline_is_refused(self):
        second = wiki.create_revision(
            self.entry['entry_id'],
            {'request_id': 'rev-2', 'base_revision_id': self.v1['revision_id'],
             'title': 'second', 'body_markdown': 'b'},
            self.dsn, self.approver)['revision']
        with self.assertRaises(HTTPException) as caught:
            wiki.create_revision(self.entry['entry_id'],
                                 {'request_id': 'rev-3',
                                  'base_revision_id': self.v1['revision_id'],
                                  'title': 'third', 'body_markdown': 'b'},
                                 self.dsn, self.approver)
        self.assertEqual((caught.exception.status_code, code_of(caught.exception)),
                         (409, 'WIKI_VERSION_CONFLICT'),
                         '已经有更新的版本 → 旧基线必须冲突')
        self.assertEqual(second['base_revision_id'], self.v1['revision_id'])

    def test_the_publish_path_is_a_cas_and_keeps_the_three_dimensions_apart(self):
        self._review(self.v1['revision_id'])
        published = wiki.publish(self.entry['entry_id'],
                                 {'request_id': 'pub-1', 'revision_id': self.v1['revision_id'],
                                  'expected_head_revision_id': None, 'note': '发布首版'},
                                 self.dsn, self.approver)
        self.assertEqual(published['publication']['previous_head_revision_id'], None)
        self.assertEqual(published['publication']['conflicted_assertions'], [])
        self.assertEqual(published['entry']['head_revision_id'], self.v1['revision_id'])
        self.assertEqual(published['entry']['status']['publication'], 'published')
        self.assertEqual(published['entry']['status']['assertion'], 'verified',
                         '主张核验状态来自主张自己那一行，发布不改它')
        self.assertIsNone(published['entry']['status']['source_validity'],
                          '发布不得顺手把来源有效性说成 current')
        self.assertEqual(published['entry']['revision_count'], 1)
        self.assertEqual(published['entry']['evidence_ref_count'], 0,
                         '这一版没有引用任何证据引用（citations 为空）')
        with self.assertRaises(HTTPException) as caught:
            wiki.publish(self.entry['entry_id'],
                         {'request_id': 'pub-2', 'revision_id': self.v1['revision_id'],
                          'expected_head_revision_id': None}, self.dsn, self.approver)
        self.assertEqual((caught.exception.status_code, code_of(caught.exception)),
                         (409, 'WIKI_VERSION_CONFLICT'), '过时的预期发布头必须一行都不改')
        reloaded = wiki.entry_detail(self.entry['entry_id'], self.dsn,
                                     self.approver)['entry']
        self.assertEqual(reloaded['head_revision_id'], self.v1['revision_id'],
                         '上面那次失败不得移动发布头')

    def test_a_reviewer_without_the_approver_role_cannot_publish(self):
        self._review(self.v1['revision_id'])
        with self.assertRaises(HTTPException) as caught:
            wiki.publish(self.entry['entry_id'],
                         {'request_id': 'pub-1', 'revision_id': self.v1['revision_id'],
                          'expected_head_revision_id': None}, self.dsn, self.reviewer)
        self.assertEqual((caught.exception.status_code, code_of(caught.exception)),
                         (403, 'WIKI_PERMISSION_DENIED'),
                         'reviewer 与 approver 互不继承（auth.py:195）')

    def test_an_unreviewed_version_cannot_be_published(self):
        with self.assertRaises(HTTPException) as caught:
            wiki.publish(self.entry['entry_id'],
                         {'request_id': 'pub-1', 'revision_id': self.v1['revision_id'],
                          'expected_head_revision_id': None}, self.dsn, self.approver)
        self.assertEqual(code_of(caught.exception), 'WIKI_REVIEW_REQUIRED')

    def test_a_pending_identity_may_be_drafted_but_never_published(self):
        pending = wiki.create_entry({'entry_type': 'business_entity', 'title': '设备 A102',
                                     'namespace': 'industrial-demo'}, self.dsn, self.approver)
        self.assertEqual(pending['entry']['confirmation'], 'pending')
        self.assertEqual(pending['identity_source']['key_part'], 'pending:设备 a102')
        revision = wiki.create_revision(
            pending['entry']['entry_id'],
            {'request_id': 'rev-p1', 'base_revision_id': None, 'title': '设备 A102',
             'body_markdown': '草案'}, self.dsn, self.approver)['revision']
        wiki.submit_review(pending['entry']['entry_id'],
                           {'request_id': 'rv-p1', 'revision_id': revision['revision_id'],
                            'action': 'submit_review', 'expected_head_revision_id': None,
                            'scope': {'full_revision': True}, 'verdict': {}},
                           self.dsn, self.reviewer)
        with self.assertRaises(HTTPException) as caught:
            wiki.publish(pending['entry']['entry_id'],
                         {'request_id': 'pub-p1', 'revision_id': revision['revision_id'],
                          'expected_head_revision_id': None}, self.dsn, self.approver)
        self.assertEqual((caught.exception.status_code, code_of(caught.exception)),
                         (409, 'WIKI_ENTRY_IDENTITY_PENDING'),
                         '§4.1 规则 4：pending 词条可进草稿、不可发布')

    def test_promoting_the_entity_identity_leaves_a_review_event(self):
        pending = wiki.create_entry({'entry_type': 'business_entity', 'title': '设备 A102',
                                     'namespace': 'industrial-demo'}, self.dsn, self.approver)
        entry_id = pending['entry']['entry_id']
        revision = wiki.create_revision(
            entry_id, {'request_id': 'rev-p1', 'base_revision_id': None, 'title': '设备 A102',
                       'body_markdown': '草案'}, self.dsn, self.approver)['revision']
        promoted = wiki.promote_entity_identity(
            entry_id, {'revision_id': revision['revision_id'], 'method': 'human_review',
                       'entity_type': 'part', 'canonical_entity_key': 'A102',
                       'note': '人工确认实体键'}, self.dsn, self.approver)
        self.assertEqual(promoted['entry']['confirmation'], 'confirmed')
        self.assertEqual(promoted['entry']['business_key']['key_part'], 'part\x1fA102')
        self.assertEqual(promoted['review_event']['action'], 'approve_facts',
                         '§4.1 规则 3：升级必须留下 wiki_review_events 记录')
        self.assertEqual(promoted['previous_key_part'], 'pending:设备 a102')

    def test_history_reads_come_back_with_their_relations_and_review_events(self):
        self._review(self.v1['revision_id'])
        detail = wiki.revision_detail(self.entry['entry_id'], self.v1['revision_id'],
                                      self.dsn, self.reviewer)
        self.assertEqual(len(detail['review_events']), 1)
        self.assertEqual(detail['review_events'][0]['review_event_id'][:5], 'wevt-')
        self.assertEqual(detail['relations'][0]['dst'], self.other_entry['entry_id'],
                         '需求 §5.2：读历史版本必须同时返回该版本的关系与核验记录')
        self.assertEqual(detail['revision']['assertions'][0]['verification']['status'],
                         'verified')
        listing = wiki.revision_history(self.entry['entry_id'], self.dsn, self.reviewer)
        self.assertEqual([row['revision_id'] for row in listing['revisions']],
                         [self.v1['revision_id']])
        self.assertEqual(listing['revisions'][0]['assertion_count'], 2)
        self.assertFalse(listing['has_more'])
        self.assertEqual(listing['revisions'][0]['events'][0]['event'], 'approve_facts')

    def test_a_fixed_version_check_mismatch_is_a_conflict(self):
        detail = wiki.revision_detail(self.entry['entry_id'], self.v1['revision_id'],
                                      self.dsn, self.reviewer)
        body_sha256 = detail['revision']['body_sha256']
        matched = wiki.revision_detail(self.entry['entry_id'], self.v1['revision_id'],
                                       self.dsn, self.reviewer,
                                       expected_body_sha256='sha256:' + body_sha256,
                                       expected_base_revision_id=None)
        self.assertEqual(matched['revision']['body_sha256'], body_sha256)
        for kwargs in ({'expected_body_sha256': '0' * 64},
                       {'expected_base_revision_id': REV2}):
            with self.assertRaises(HTTPException) as caught:
                wiki.revision_detail(self.entry['entry_id'], self.v1['revision_id'],
                                     self.dsn, self.reviewer, **kwargs)
            self.assertEqual((caught.exception.status_code, code_of(caught.exception)),
                             (409, 'WIKI_VERSION_CONFLICT'), kwargs)

    def test_another_users_entry_is_invisible(self):
        stranger = {'user_id': 'user-stranger', 'roles': ['reviewer'], '_session_hash': 'x' * 64}
        with self.assertRaises(HTTPException) as caught:
            wiki.entry_detail(self.entry['entry_id'], self.dsn, stranger)
        self.assertEqual((caught.exception.status_code, code_of(caught.exception)),
                         (404, 'WIKI_ENTRY_NOT_FOUND'),
                         '别人的词条必须与不存在完全一致（存在性隐藏）')


@unittest.skipUnless(DB, 'psycopg2 或 datamind_meta 不可用')
class ZZWikiTablesStayEmptyTest(unittest.TestCase):
    """自检：本文件（以及本轮其它用例）**没有**在 8 张 wiki 表里留下数据。

    类名以 ZZ 开头，保证它在同一文件里最后执行；只读，不改任何东西。
    """

    def test_every_wiki_table_still_has_zero_rows(self):
        counts = {}
        with app_transaction() as cur:
            for table in ('wiki_entries', 'wiki_revisions', 'wiki_assertions',
                          'wiki_evidence_refs', 'wiki_provenance_links', 'wiki_relations',
                          'wiki_review_events', 'wiki_build_jobs'):
                cur.execute('SELECT count(*) AS counted FROM %s' % table)
                counts[table] = int(cur.fetchone()['counted'])
        self.assertEqual(counts, {table: 0 for table in counts},
                         'wiki 表必须保持 0 行（用例只在 ROLLBACK 事务里写）')


if __name__ == '__main__':
    unittest.main()
