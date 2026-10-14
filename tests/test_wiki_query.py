"""Wiki P3 读/查询与导出层（`datamind/wiki_query.py`）的契约测试。

分层与 `tests/test_wiki_service.py` 同款，理由也一样（本仓库实测）：

* 系统 python（`C:\\Python314`）有 pytest 但**没有 psycopg2**；`.venv` 有 psycopg2 但**没有
  pytest**。所以"授权投影、分页上限、游标重置、两端在已加载集合内、连接数只算已授权、
  搜索覆盖声明、导出必填六项"这些**纯逻辑**必须能在没有 psycopg2 的解释器里跑起来
  （`python -m pytest tests/test_wiki_query.py -q`）；
* 真库用例用 `@unittest.skipUnless(DB, …)` 包住（先例 `tests/test_parse_contract.py:745-776`
  的既有权宜模式），在 `.venv` 下用 `python -m unittest` 跑；
* 真库用例**全部在事务里 ROLLBACK**，8 张 wiki 表在本轮开始与结束都是 0 行
  （`ZZWikiQueryTablesStayEmptyTest` 是这条自检）。
"""
import ast
import json
import re
import sys
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import HTTPException  # noqa: E402

from datamind import wiki, wiki_query  # noqa: E402  纯逻辑不需要 psycopg2

try:  # 数据库用例的依赖；缺了让那些用例 skip，而不是整个文件 collection error
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:  # pragma: no cover
    psycopg2 = None
    RealDictCursor = None

MODULE_SOURCE = (ROOT / 'datamind' / 'wiki_query.py').read_text(encoding='utf-8')


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


def code_of(exc):
    return exc.detail.get('error_code')


def identity(user_id, *roles):
    return {'user_id': user_id, 'roles': list(roles)}


def node(node_id_value, **extra):
    return {'node_kind': 'entry', 'node_id': node_id_value, **extra}


def relation(relation_id_value, src, dst):
    return {'relation_id': relation_id_value, 'src': src, 'dst': dst}


# ---------------------------------------------------------------------------
# 1. 统一授权投影（本次最重要的产出）
# ---------------------------------------------------------------------------

class WikiQueryAuthorizationProjectionTest(unittest.TestCase):
    """契约 §5 `meta.authorized_scope`：全部读取面共用**一个**授权过滤入口。"""

    def test_scope_is_derived_from_the_identity_and_the_requested_space(self):
        scope = wiki_query.authorized_scope(identity('user-a', 'viewer'))
        self.assertEqual(scope['space_id'], wiki.space_for_user('user-a'))
        self.assertEqual(scope['own_space_id'], wiki.space_for_user('user-a'))
        self.assertEqual(scope['user_id'], 'user-a')
        self.assertFalse(scope['is_admin'])

    def test_somebody_elses_space_is_a_404_not_a_403(self):
        """§3.1 脚注 5：请求别人的 `space_id` → 与不存在**完全相同**的 404。"""
        with self.assertRaises(HTTPException) as caught:
            wiki_query.authorized_scope(identity('user-a', 'viewer'),
                                        wiki.space_for_user('user-b'))
        self.assertEqual((caught.exception.status_code, code_of(caught.exception)),
                         (404, 'WIKI_SPACE_NOT_FOUND'))
        with self.assertRaises(HTTPException) as malformed:
            wiki_query.authorized_scope(identity('user-a'), 'not-a-space-id')
        self.assertEqual(code_of(malformed.exception), 'WIKI_SPACE_NOT_FOUND')

    def test_an_admin_may_ask_for_another_space_but_a_viewer_may_not(self):
        other = wiki.space_for_user('user-b')
        self.assertEqual(wiki_query.authorized_scope(identity('admin-1', 'admin'),
                                                     other)['space_id'], other)
        self.assertTrue(wiki_query.authorized_scope(identity('admin-1', 'admin'))['is_admin'])

    def test_an_identity_without_a_user_is_a_401(self):
        with self.assertRaises(HTTPException) as caught:
            wiki_query.authorized_scope({})
        self.assertEqual((caught.exception.status_code, code_of(caught.exception)),
                         (401, 'SESSION_INVALID'))

    def test_the_sql_and_python_renderings_of_the_rule_agree(self):
        """一条规则、两种渲染（SQL 与 Python）必须给出同一结论，否则"能列出"与"能读"会分叉。"""
        scope = wiki_query.authorized_scope(identity('user-a', 'viewer'))
        mine = {'space_id': scope['space_id'], 'owner_id': 'user-a'}
        other_owner = {'space_id': scope['space_id'], 'owner_id': 'user-b'}
        other_space = {'space_id': wiki.space_for_user('user-b'), 'owner_id': 'user-a'}
        self.assertTrue(wiki_query.entry_is_authorized(scope, mine))
        self.assertFalse(wiki_query.entry_is_authorized(scope, other_owner))
        self.assertFalse(wiki_query.entry_is_authorized(scope, other_space))
        admin = wiki_query.authorized_scope(identity('admin-1', 'admin'))
        self.assertTrue(wiki_query.entry_is_authorized(admin, other_space))
        sql = wiki_query.entry_authorization_predicate(scope, alias='e')
        self.assertIn('e.space_id = %(auth_space)s', sql)
        self.assertIn('e.owner_id = %(auth_owner)s', sql)
        self.assertIn('%(auth_admin)s', sql)
        self.assertEqual(wiki_query.authorization_params(scope),
                         {'auth_space': scope['space_id'], 'auth_owner': 'user-a',
                          'auth_admin': False})

    def test_the_ownership_rule_is_written_in_exactly_two_places(self):
        """**核心不变量**：授权规则只有 SQL 与 Python 两种渲染，没有第三个接口各写一套。

        `owner_id` 的**判定**在本模块里只出现两次：`entry_authorization_predicate()`（SQL 条件）
        与 `entry_is_authorized()`（Python 判定）。关系端点的授权、目录计数、图谱候选、来源展开
        都必须复用它们（`relation_endpoint_authorization_predicate()` 只是拼装）。
        """
        self.assertEqual(MODULE_SOURCE.count('owner_id = %({owner})s'), 1,
                         '授权 SQL 条件被写成了第二份——那正是越权的来源')
        self.assertEqual(MODULE_SOURCE.count("row.get('owner_id')"), 1,
                         'Python 侧的授权判定被写成了第二份')
        # 每个读面都真的调用了这套投影（目录/计数/图谱候选/关系/邻域/来源展开）。
        self.assertGreaterEqual(
            len(re.findall(r'entry_authorization_predicate\(scope', MODULE_SOURCE)), 6)
        self.assertGreaterEqual(
            len(re.findall(r'relation_endpoint_authorization_predicate\(', MODULE_SOURCE)), 3)
        self.assertGreaterEqual(len(re.findall(r'entry_is_authorized\(', MODULE_SOURCE)), 2)

    def test_the_relation_endpoint_projection_covers_all_three_endpoint_kinds(self):
        scope = wiki_query.authorized_scope(identity('user-a', 'viewer'))
        sql = wiki_query.relation_endpoint_authorization_predicate(scope, alias='r')
        for kind in ('entry', 'assertion', 'evidence_ref'):
            self.assertIn("'%s'" % kind, sql)
        self.assertIn('%({})s'.format(wiki_query.AUTH_PARAM_SPACE), sql, '证据端点按空间授权')

    def test_reads_never_use_the_readonly_role(self):
        """021:605-607 已把 8 张 wiki 表从 `readonly_user` REVOKE，读也不能用它。

        本模块**自己构造 DSN**这件事本身是被禁止的：DSN 由路由层传进来（应是应用角色），
        所以代码里不该出现任何 `*dsn` 调用（文档里提到它不算——这里看的是 AST 引用）。
        """
        tree = ast.parse(MODULE_SOURCE)
        attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        self.assertNotIn('read_dsn', attributes)
        self.assertFalse([name for name in attributes if name.endswith('dsn')],
                         'DSN 必须由调用方传入，读侧不自己挑角色')
        self.assertIn('connect', attributes)

    def test_the_scope_self_report_and_its_warning(self):
        scope = wiki_query.authorized_scope(identity('user-a'))
        meta = wiki_query.authorized_scope_meta(scope, considered=5, visible=5)
        self.assertEqual(meta['authorized_scope'],
                         {'nodes_considered': 5, 'nodes_visible': 5})
        self.assertRegex(meta['as_of'], r'\+08:00$', '契约 §1：时间一律带 +08:00')
        self.assertEqual(wiki_query.scope_warnings(considered=5, visible=5), [])
        self.assertEqual(wiki_query.scope_warnings(considered=5, visible=2),
                         [wiki_query.AUTHORIZATION_FILTERED])


# ---------------------------------------------------------------------------
# 2. #1 目录与关键词搜索
# ---------------------------------------------------------------------------

class WikiQueryCatalogueTest(unittest.TestCase):
    """契约 §5.1 / §4：参数边界、转义、冻结的分页默认值，以及**如实的检索覆盖声明**。"""

    def test_limits_are_the_frozen_20_and_50(self):
        self.assertEqual((wiki_query.CATALOGUE_LIMIT_DEFAULT, wiki_query.CATALOGUE_LIMIT_MAX),
                         (20, 50))
        self.assertEqual(wiki.REVISIONS_LIMIT_DEFAULT, 20, '#4 的分页默认值在服务层')
        self.assertEqual(wiki.REVISIONS_LIMIT_MAX, 50)

    def test_the_search_pattern_escapes_percent_underscore_and_backslash(self):
        """§5.1：用户搜 `100%` 时 `%` **不是**通配符（沿用 sessions 的既有语义）。"""
        self.assertEqual(wiki_query.search_pattern('100%'), '%100\\%%')
        self.assertEqual(wiki_query.search_pattern('a_b'), '%a\\_b%')
        self.assertEqual(wiki_query.search_pattern('a\\b'), '%a\\\\b%')
        self.assertIsNone(wiki_query.search_pattern('   '), '空白等于不传')
        self.assertIsNone(wiki_query.search_pattern(None))
        with self.assertRaises(HTTPException) as caught:
            wiki_query.search_pattern('温' * 201)
        self.assertEqual((caught.exception.status_code, code_of(caught.exception)),
                         (422, 'WIKI_BUILD_REQUEST_INVALID'))
        with self.assertRaises(HTTPException):
            wiki_query.search_pattern('bad\x00nul')

    def test_the_sql_escape_clause_is_a_single_backslash(self):
        self.assertEqual(wiki_query.ILIKE_ESCAPE, "ESCAPE '\\'")
        self.assertIn(wiki_query.ILIKE_ESCAPE, wiki_query._QUERY_MATCH_SQL)

    def test_the_search_declaration_admits_the_body_is_not_searched(self):
        """契约 §5.1 / §13 第 5 条：中文正文检索策略未定，响应必须**如实**声明覆盖范围。"""
        search = wiki_query.search_declaration('温度')
        self.assertEqual(search['q'], '温度')
        self.assertEqual(search['scope'], ['title', 'aliases', 'category'])
        self.assertFalse(search['body_searched'])
        self.assertIn('body_markdown', search['uncovered_fields'])
        self.assertEqual(search['reason_code'], 'CHINESE_BODY_SEARCH_UNDECIDED')
        self.assertEqual(wiki_query.search_declaration('  ')['q'], None)

    def test_sql_searches_title_alias_and_category_but_never_the_body(self):
        sql = wiki_query._QUERY_MATCH_SQL
        self.assertIn('e.title ILIKE', sql)
        self.assertIn('unnest(e.aliases)', sql)
        self.assertIn("COALESCE(e.category, '') ILIKE", sql)
        self.assertNotIn('body_markdown', sql)
        self.assertNotIn('summary', sql)

    def test_filters_normalise_so_an_unchanged_filter_keeps_its_fingerprint(self):
        first = wiki_query.normalize_filters(q='温度', entry_type='metric', status='published',
                                             category='工业设备')
        reordered = wiki_query.normalize_filters(category='工业设备', status='published',
                                                 entry_type=['metric'], q='  温度  ')
        self.assertEqual(first, reordered)
        self.assertEqual(wiki_query.filter_fingerprint(first),
                         wiki_query.filter_fingerprint(reordered))
        changed = dict(first, q='压力')
        self.assertNotEqual(wiki_query.filter_fingerprint(first),
                            wiki_query.filter_fingerprint(changed))

    def test_unknown_enums_are_refused_instead_of_silently_matching_nothing(self):
        for kwargs in ({'entry_type': 'not-a-type'}, {'status': 'not-a-status'}):
            with self.assertRaises(HTTPException) as caught:
                wiki_query.normalize_filters(**kwargs)
            self.assertEqual(code_of(caught.exception), 'WIKI_BUILD_REQUEST_INVALID')

    def test_updated_since_must_be_an_iso_timestamp(self):
        moment = wiki_query.normalize_timestamp('2026-10-14T09:00:00+08:00')
        self.assertEqual(wiki_query.iso_value(moment), '2026-10-14T09:00:00+08:00')
        naive = wiki_query.normalize_timestamp('2026-10-14T09:00:00')
        self.assertEqual(wiki_query.iso_value(naive), '2026-10-14T09:00:00+08:00',
                         '朴素时间按仓库统一时区 +08:00 解释')
        with self.assertRaises(HTTPException) as caught:
            wiki_query.normalize_timestamp('yesterday')
        self.assertEqual(code_of(caught.exception), 'WIKI_BUILD_REQUEST_INVALID')


# ---------------------------------------------------------------------------
# 3. 游标：不透明、筛选变化即重置（§4 语义 1/2/4）
# ---------------------------------------------------------------------------

class WikiQueryCursorTest(unittest.TestCase):

    def test_a_listing_cursor_round_trips_and_carries_the_filter_fingerprint(self):
        filters = wiki_query.normalize_filters(q='温度')
        fingerprint = wiki_query.filter_fingerprint(filters)
        token = wiki_query.listing_cursor(fingerprint, sort_key='2026-10-14T09:00:00+08:00',
                                          tie_break='wiki-' + 'a' * 24)
        self.assertNotIn('wiki-', token, '游标是不透明的 base64url，不是明文 id')
        marker, reset = wiki_query.resolve_cursor(token, fingerprint=fingerprint,
                                                  keys=wiki_query.LISTING_CURSOR_KEYS)
        self.assertFalse(reset)
        self.assertEqual(wiki_query.listing_marker(marker),
                         ('2026-10-14T09:00:00+08:00', 'wiki-' + 'a' * 24))

    def test_changing_the_layer_or_any_filter_resets_the_cursor(self):
        """§4 语义 4：图层或筛选变化 → 忽略传入的游标、视为新集合。"""
        entries = wiki_query.filter_fingerprint(
            wiki_query.normalize_filters(layer='entries', q='温度'))
        evidence = wiki_query.filter_fingerprint(
            wiki_query.normalize_filters(layer='evidence'))
        token = wiki_query.listing_cursor(entries, sort_key='x', tie_break='y')
        marker, reset = wiki_query.resolve_cursor(token, fingerprint=entries,
                                                  keys=wiki_query.LISTING_CURSOR_KEYS)
        self.assertEqual((marker is not None, reset), (True, False), '同一筛选继续翻页')
        marker, reset = wiki_query.resolve_cursor(token, fingerprint=evidence,
                                                  keys=wiki_query.LISTING_CURSOR_KEYS)
        self.assertEqual((marker, reset), (None, True), '换了图层必须重置')

    def test_a_malformed_or_foreign_cursor_is_a_422_never_a_500(self):
        for cursor in ('not-base64!!', 'YWJj', 'e30'):
            with self.assertRaises(HTTPException) as caught:
                wiki_query.resolve_cursor(cursor, fingerprint='f',
                                          keys=wiki_query.LISTING_CURSOR_KEYS)
            self.assertEqual((caught.exception.status_code, code_of(caught.exception)),
                             (422, 'WIKI_BUILD_REQUEST_INVALID'), cursor)
        with self.assertRaises(HTTPException) as caught:
            wiki_query.resolve_cursor('a' * 600, fingerprint='f',
                                      keys=wiki_query.LISTING_CURSOR_KEYS)
        self.assertEqual(code_of(caught.exception), 'WIKI_BUILD_REQUEST_INVALID')


# ---------------------------------------------------------------------------
# 4. #2 空间图：100 节点 / 300 关系 / 截断 / 不能绕过上限
# ---------------------------------------------------------------------------

class WikiQueryGraphBatchTest(unittest.TestCase):
    """契约 §4 语义 3/5/6/7 与 §5.2 的四条硬要求。"""

    def setUp(self):
        self.nodes = [node('wiki-%024d' % index) for index in range(80)]
        # 前 40 个节点两两相连（按确定顺序），其余节点孤立：孤立节点仍要可见（§9.1）。
        self.relations = [relation('wrel-%024d' % index,
                                   self.nodes[index % 40]['node_id'],
                                   self.nodes[(index + 1) % 40]['node_id'])
                          for index in range(20)]
        self.fingerprint = 'fp-1'

    def test_the_first_batch_is_the_frozen_50_nodes(self):
        batch = wiki_query.graph_batch(candidates=self.nodes,
                                       candidate_relations=self.relations,
                                       fingerprint=self.fingerprint, more_candidates=True)
        self.assertEqual(batch['limits'], {'nodes': 100, 'relations': 300})
        self.assertEqual(batch['loaded'], {'nodes': 50, 'relations': 20})
        self.assertEqual(len(batch['nodes']), 50)
        self.assertEqual(len(batch['relations']), 20)
        self.assertTrue(batch['has_more'])
        self.assertIsNone(batch['limit_reached'])
        self.assertFalse(batch['truncated'])
        self.assertIsNotNone(batch['next_cursor'])
        for item in batch['nodes']:
            self.assertEqual(item['degree_scope'], 'authorized_loaded')
            self.assertIsNone(item['position_hint'])

    def test_the_second_batch_reaches_the_100_node_cap_and_stops(self):
        state = wiki_query.graph_cursor_state(None, fingerprint=self.fingerprint)
        self.assertEqual(state, (0, 0))
        batch = wiki_query.graph_batch(candidates=self.nodes,
                                       candidate_relations=self.relations,
                                       fingerprint=self.fingerprint, loaded_nodes=50,
                                       loaded_relations=20, more_candidates=True)
        self.assertEqual(batch['loaded']['nodes'], 80, '只剩 30 个候选，不该假装有 100')
        self.assertEqual(len(batch['nodes']), 30)
        self.assertFalse(batch['truncated'], '没有第 101 个候选时不算截断')
        self.assertIsNone(batch['limit_reached'])

    def test_the_node_cap_cannot_be_bypassed_by_continuing_to_load(self):
        full = [node('wiki-%024d' % index) for index in range(100)]
        batch = wiki_query.graph_batch(candidates=full, candidate_relations=(),
                                       fingerprint=self.fingerprint, loaded_nodes=50,
                                       more_candidates=True)
        self.assertEqual(batch['loaded']['nodes'], 100)
        self.assertEqual(batch['limit_reached'], {'nodes': 100, 'relations': 300})
        self.assertIsNone(batch['next_cursor'], '达到上限后必须 next_cursor=null')
        self.assertTrue(batch['truncated'])
        self.assertTrue(batch['has_more'], '还有更多，但因为上限不再给')
        self.assertEqual(batch['hint'], wiki_query.GRAPH_LIMIT_HINT)
        # 客户端再拿旧游标来（甚至伪造更大的计数）也拿不到东西。
        again = wiki_query.graph_batch(candidates=full, candidate_relations=(),
                                       fingerprint=self.fingerprint, loaded_nodes=100,
                                       more_candidates=True)
        self.assertEqual(again['nodes'], [])
        self.assertEqual(again['loaded']['nodes'], 100)
        self.assertIsNone(again['next_cursor'])

    def test_the_cursor_counters_are_clamped_so_a_forged_cursor_cannot_widen_the_view(self):
        full = [node('wiki-%024d' % index) for index in range(100)]
        marker = {'f': self.fingerprint, 'n': 5000, 'r': 5000}
        self.assertEqual(wiki_query.graph_cursor_state(marker, fingerprint=self.fingerprint),
                         (100, 300))
        for bad in ({'f': self.fingerprint, 'n': -1, 'r': 0},
                    {'f': self.fingerprint, 'n': 'many', 'r': 0}):
            with self.assertRaises(HTTPException) as caught:
                wiki_query.graph_cursor_state(bad, fingerprint=self.fingerprint)
            self.assertEqual(code_of(caught.exception), 'WIKI_BUILD_REQUEST_INVALID')

    def test_the_relation_cap_is_300_and_also_stops_the_paging(self):
        wide = [node('wiki-%024d' % index) for index in range(20)]
        relations = [relation('wrel-%024d' % index, wide[0]['node_id'],
                              wide[1 + index % 19]['node_id']) for index in range(400)]
        batch = wiki_query.graph_batch(candidates=wide, candidate_relations=relations,
                                       fingerprint=self.fingerprint)
        self.assertEqual(batch['loaded']['relations'], 300)
        self.assertEqual(batch['limit_reached'], {'nodes': 100, 'relations': 300})
        self.assertEqual(batch['truncated_reason'], 'RELATION_LIMIT')
        self.assertIsNone(batch['next_cursor'])

    def test_a_relation_with_only_one_loaded_end_is_never_returned(self):
        """§4 语义 6：半条边要么延后，要么不返回——**不得**只返回一端。"""
        candidates = self.nodes[:3]
        half = relation('wrel-half', candidates[0]['node_id'], 'wiki-' + 'f' * 24)
        inside = relation('wrel-inside', candidates[1]['node_id'], candidates[2]['node_id'])
        self.assertEqual(wiki_query.relations_within_nodes([half, inside],
                                                           [n['node_id'] for n in candidates]),
                         [inside])
        batch = wiki_query.graph_batch(candidates=candidates,
                                       candidate_relations=[half, inside],
                                       fingerprint=self.fingerprint)
        self.assertEqual([item['relation_id'] for item in batch['relations']], ['wrel-inside'])
        self.assertNotIn('wiki-' + 'f' * 24,
                         [item['node_id'] for item in batch['nodes']])

    def test_new_relations_are_only_the_ones_completed_by_this_batch(self):
        full = [node('wiki-%024d' % index) for index in range(100)]
        old = relation('wrel-old', full[1]['node_id'], full[2]['node_id'])
        fresh = relation('wrel-fresh', full[60]['node_id'], full[61]['node_id'])
        batch = wiki_query.graph_batch(candidates=full, candidate_relations=[old, fresh],
                                       fingerprint=self.fingerprint, loaded_nodes=50,
                                       loaded_relations=1, more_candidates=True)
        self.assertEqual([item['relation_id'] for item in batch['relations']], ['wrel-fresh'],
                         '上一批已经给过的边不重复给')

    def test_degrees_count_only_authorized_and_loaded_connections(self):
        """§4 语义 7：隐藏节点**不参与计数**——否则用数字就能推断隐藏图的存在。"""
        visible = [node('wiki-' + 'a' * 24), node('wiki-' + 'b' * 24)]
        hidden = 'wiki-' + 'c' * 24
        all_relations = [
            relation('wrel-1', visible[0]['node_id'], visible[1]['node_id']),
            relation('wrel-2', visible[0]['node_id'], hidden),
            relation('wrel-3', visible[0]['node_id'], hidden),
            relation('wrel-4', visible[1]['node_id'], hidden),
        ]
        loaded = wiki_query.relations_within_nodes(all_relations,
                                                   [n['node_id'] for n in visible])
        self.assertEqual(len(loaded), 1, '两条到隐藏节点的边被丢掉')
        degrees = wiki_query.degrees_of([n['node_id'] for n in visible], loaded)
        self.assertEqual(degrees[visible[0]['node_id']], 1)
        self.assertEqual(degrees[visible[1]['node_id']], 1)
        self.assertNotIn(hidden, degrees, '隐藏节点不得出现在任何计数字典里')
        batch = wiki_query.graph_batch(candidates=visible,
                                       candidate_relations=all_relations,
                                       fingerprint=self.fingerprint)
        self.assertEqual([item['degree'] for item in batch['nodes']], [1, 1],
                         '三个真实连接只报一个：另外两个的邻居不在授权/已加载集合里')

    def test_candidates_must_already_be_capped(self):
        with self.assertRaises(HTTPException) as caught:
            wiki_query.graph_batch(candidates=[node('wiki-%024d' % index) for index in range(101)],
                                   candidate_relations=(), fingerprint='fp')
        self.assertEqual(code_of(caught.exception), 'WIKI_BUILD_REQUEST_INVALID')

    def test_the_response_says_it_is_the_authorized_scope_graph(self):
        """§5.2 第 4 条：界面称之为"当前授权范围图"，**不得**描述为全库全图。"""
        self.assertIn('CURRENT_AUTHORIZED_SCOPE_GRAPH', MODULE_SOURCE)
        self.assertIn('当前授权范围图（不是全库全图）', MODULE_SOURCE)
        self.assertIn('#2 当前授权范围图', MODULE_SOURCE)


# ---------------------------------------------------------------------------
# 5. #6 邻域：截断标识与未到达邻居
# ---------------------------------------------------------------------------

class WikiQueryNeighborhoodTest(unittest.TestCase):

    def test_the_node_cap_truncates_and_counts_only_authorized_neighbors(self):
        root = node('wiki-root')
        neighbors = [node('wiki-%024d' % index) for index in range(10)]
        result = wiki_query.assemble_neighborhood(
            root=root, hop_nodes=[neighbors], relation_rows=[
                relation('wrel-%024d' % index, 'wiki-root', neighbors[index]['node_id'])
                for index in range(10)],
            depth=1, max_depth=2, node_limit=5)
        self.assertEqual(result['loaded']['nodes'], 5, '根 + 4 个邻居')
        self.assertEqual(result['truncated'], True)
        self.assertEqual(result['truncated_reason'], 'NODE_LIMIT')
        self.assertEqual(result['unreached_neighbors'], 6)
        self.assertEqual(result['hint'], wiki_query.GRAPH_LIMIT_HINT)

    def test_the_relation_cap_is_reported_with_its_own_reason(self):
        root = node('wiki-root')
        neighbors = [node('wiki-%024d' % index) for index in range(4)]
        rows = [relation('wrel-%024d' % index, 'wiki-root', neighbors[index % 4]['node_id'])
                for index in range(10)]
        result = wiki_query.assemble_neighborhood(root=root, hop_nodes=[neighbors],
                                                  relation_rows=rows, depth=1, max_depth=2,
                                                  node_limit=10, relation_limit=3)
        self.assertEqual(result['loaded']['relations'], 3)
        self.assertEqual(result['truncated_reason'], 'RELATION_LIMIT')
        self.assertEqual(result['unreached_neighbors'], 0, '节点没被截断就不该报未到达邻居')

    def test_the_depth_limit_only_applies_at_the_maximum_depth(self):
        root = node('wiki-root')
        first = [node('wiki-' + 'a' * 24)]
        twice = wiki_query.assemble_neighborhood(root=root, hop_nodes=[first],
                                                 relation_rows=[], depth=2, max_depth=2,
                                                 boundary_neighbors=7)
        self.assertTrue(twice['truncated'])
        self.assertEqual(twice['truncated_reason'], 'DEPTH_LIMIT')
        self.assertEqual(twice['unreached_neighbors'], 7,
                         '2 跳之外仍然存在的授权邻居要如实计数')
        once = wiki_query.assemble_neighborhood(root=root, hop_nodes=[first],
                                               relation_rows=[], depth=1, max_depth=2,
                                               boundary_neighbors=7)
        self.assertFalse(once['truncated'], 'depth=1 时 2 跳邻居是用户要的范围，不是截断')
        self.assertEqual(once['unreached_neighbors'], 0)

    def test_relations_to_nodes_that_were_not_loaded_are_dropped(self):
        root = node('wiki-root')
        neighbor = node('wiki-n1')
        rows = [relation('wrel-1', 'wiki-root', 'wiki-n1'),
                relation('wrel-2', 'wiki-root', 'wiki-not-loaded')]
        result = wiki_query.assemble_neighborhood(root=root, hop_nodes=[[neighbor]],
                                                  relation_rows=rows, depth=1, max_depth=2)
        self.assertEqual([item['relation_id'] for item in result['relations']], ['wrel-1'])
        self.assertEqual(result['nodes'][0]['degree'], 1)

    def test_the_nested_relation_object_carries_its_display_label(self):
        self.assertEqual(wiki_query.relation_label('supports'), '支撑')
        self.assertEqual(wiki_query.relation_label(None), wiki_query.UNSUPPORTED_RELATION_LABEL)
        self.assertEqual(wiki_query.relation_label('unknown-type', supported=False),
                         wiki_query.UNSUPPORTED_RELATION_LABEL)
        row = {'relation_id': 'wrel-1', 'src_id': 'wiki-a', 'src_kind': 'entry',
               'dst_id': 'wev-b', 'dst_kind': 'evidence_ref', 'relation_type': None,
               'is_symmetric': False, 'origin': 'projected', 'raw_type': 'type_from_the_future',
               'supported': False, 'unsupported_reason': 'TYPE_NOT_IN_WIKI_VOCABULARY',
               'rule_version': None, 'basis_note': None, 'basis': None, 'derivation': None,
               'provenance_incomplete': {'applies': False}, 'created_at': None}
        self.assertEqual(wiki_query.graph_relation_object(row)['label'],
                         wiki_query.UNSUPPORTED_RELATION_LABEL,
                         '§3.4 规则 4：未支持类型要给文字标识，不是猜一个最近的类型')


# ---------------------------------------------------------------------------
# 6. #7 provenance：错误摘要三态、脱敏、出处缺失、读取时复查
# ---------------------------------------------------------------------------

class WikiQueryProvenanceTest(unittest.TestCase):

    def test_the_error_summary_has_three_distinguishable_shapes(self):
        """§5.7 约束 2 / WIKI_DOMAIN §7.1：无错误与读取失败**不得**看起来一样。"""
        empty = wiki_query.error_summary_of([])
        self.assertEqual(empty, {'error_code': None, 'none': True})
        self.assertNotEqual(empty, wiki_query.unavailable_error_summary())
        failed = wiki_query.error_summary_of([{
            'error_code': 'INSUFFICIENT_EVIDENCE', 'event_type': 'verification.failed',
            'agent_name': 'verifier', 'started_at': '2026-10-14T08:55:02+08:00',
            'output_summary': {'evidence_sufficient': False}}])
        self.assertEqual(failed['error_code'], 'INSUFFICIENT_EVIDENCE')
        self.assertEqual(failed['event_type'], 'verification.failed')
        self.assertEqual(failed['detail'], {'evidence_sufficient': False})
        self.assertEqual(wiki_query.unavailable_error_summary(),
                         {'error_code': None, 'unavailable': True})

    def test_summaries_are_sanitized_and_the_redactions_stay_visible(self):
        clean, redacted = wiki_query.sanitize_summary({
            'route': 'rag', 'api_key': 'sk-secret', 'db_dsn': 'host=x',
            'path': 'D:\\private\\report.pdf', 'nested': {'token': 'abc'},
            'answer': '/assets/ok.png'})
        self.assertEqual(clean['route'], 'rag')
        self.assertNotIn('api_key', clean)
        self.assertNotIn('db_dsn', clean)
        self.assertIsNone(clean['path'], '内部绝对路径不返回')
        self.assertEqual(clean['nested'], {})
        self.assertEqual(clean['answer'], '/assets/ok.png', '资源 URL 不该被误杀')
        self.assertIn('api_key', redacted)
        self.assertIn('token', redacted)
        self.assertIn('path', redacted, '被丢弃的字段名要可见')
        self.assertNotIn('D:\\private\\report.pdf', redacted, '不回显被丢弃的值')

    def test_an_event_never_claims_visibility_it_does_not_have(self):
        event = wiki_query.event_object({
            'event_id': 'evt-1', 'run_id': 'run-1', 'event_type': 'tool.started',
            'status': 'succeeded', 'agent_name': 'orchestrator', 'tool_name': None,
            'started_at': None, 'ended_at': None, 'duration_ms': 3,
            'input_summary': {'secret_ref': 'x'}, 'output_summary': {'row_count': 3},
            'error_code': None, 'retry_index': 0, 'visibility': 'visible'})
        self.assertEqual(event['output_summary'], {'row_count': 3})
        self.assertEqual(event['input_summary'], {})
        self.assertEqual(event['redacted_fields'], ['secret_ref'])
        self.assertTrue(event['redaction_applied'])

    def test_the_event_type_list_keeps_the_real_chronological_order(self):
        rows = [{'event_type': 'run.started'}, {'event_type': 'query.parsed'},
                {'event_type': 'run.started'}, {'event_type': 'answer.completed'}]
        self.assertEqual(wiki_query.event_types_in_order(rows),
                         ['run.started', 'query.parsed', 'answer.completed'])

    def test_a_run_without_a_conversation_says_so_instead_of_guessing(self):
        """契约 §11.6：工业运行没有会话归属，必须标注，不能有模糊默认值。"""
        run = wiki_query.run_object({'run_id': 'run-1', 'question': 'q', 'status': 'COMPLETED',
                                     'conversation_id': None, 'started_at': None,
                                     'ended_at': None, 'route': 'industrial',
                                     'confidence_level': 'medium'},
                                    error_summary=wiki_query.error_summary_of([]))
        self.assertFalse(run['conversation_attributed'])
        self.assertEqual(run['attribution_note'], 'NO_CONVERSATION_ATTRIBUTION')
        attributed = wiki_query.run_object({'run_id': 'run-2', 'conversation_id': 'sess-1'},
                                           error_summary=wiki_query.error_summary_of([]))
        self.assertTrue(attributed['conversation_attributed'])
        self.assertIsNone(attributed['attribution_note'])

    def test_provenance_incomplete_is_never_guessed_into_a_complete_claim(self):
        """§6.3：缺失要标记、不得推测补全；没有缺失就说没有。"""
        none_missing = wiki_query.provenance_incomplete_summary(markers=[], has_evidence=True)
        self.assertEqual(none_missing, {'applies': False})
        from_marker = wiki_query.provenance_incomplete_summary(
            markers=[{'applies': True, 'missing_fields': ['node.binding'],
                      'reason_code': 'NODE_BINDING_NOT_PERSISTED'}], has_evidence=True)
        self.assertTrue(from_marker['applies'])
        self.assertEqual(from_marker['missing_fields'], ['node.binding'])
        self.assertFalse(from_marker['recoverable'], '从未落库的字段不可恢复')
        # 线上 evidence_edges 真的缺这三列 → 有投影关系时必须标记。
        from_edges = wiki_query.provenance_incomplete_summary(
            markers=[], edge_columns_missing=('evidence', 'binding_type', 'confidence'),
            has_projected_edges=True, has_evidence=True)
        self.assertEqual(from_edges['reason_code'], 'EDGE_COLUMNS_ABSENT_IN_DEPLOYED_SCHEMA')
        self.assertEqual(from_edges['missing_fields'],
                         ['edge.binding_type', 'edge.confidence', 'edge.evidence'])
        no_edges = wiki_query.provenance_incomplete_summary(
            markers=[], edge_columns_missing=('evidence',), has_projected_edges=False,
            has_evidence=False)
        self.assertEqual(no_edges, {'applies': False},
                         '没有投影出来的边时不得假报"边缺列"')

    def test_source_validity_is_rechecked_at_read_time(self):
        """§7.5：读时复查，`stale` 返回旧版本、`unavailable` 不返回任何受限正文。"""
        doc = {'source_kind': 'doc', 'source_ref': 'doc-1', 'document_version': 2,
               'source_sha256': 'x', 'source_snapshot_id': None}
        self.assertEqual(wiki_query.source_validity_verdict(
            doc, document_state={'doc_version': 2}), 'current')
        self.assertEqual(wiki_query.source_validity_verdict(
            doc, document_state={'doc_version': 3}), 'stale', '换版 → stale')
        self.assertEqual(wiki_query.source_validity_verdict(doc, document_state=None),
                         'unavailable', '文档行消失 → unavailable')
        sql = {'source_kind': 'sql', 'source_ref': 'demo_retail', 'source_snapshot_id': None}
        self.assertEqual(wiki_query.source_validity_verdict(sql, source_state=True),
                         'unavailable', '快照缺失 → unavailable')
        sql['source_snapshot_id'] = 'snap-demo_retail-1'
        self.assertEqual(wiki_query.source_validity_verdict(
            sql, source_state=True, snapshot_ids=()), 'unavailable', '快照不在登记表 → unavailable')
        self.assertEqual(wiki_query.source_validity_verdict(
            sql, source_state=True, snapshot_ids=('snap-demo_retail-1',)), 'current')

    def test_an_unavailable_reference_withholds_its_excerpt(self):
        row = {'evidence_ref_id': 'wev-1', 'evidence_fingerprint': 'a' * 64, 'source_kind': 'doc',
               'source_ref': 'doc-1', 'locator': {'page_no': 1}, 'locator_kind': 'pdf_region',
               'record_sha256': 'b' * 64, 'excerpt': '受限原文', 'raw_node_types': ['Chunk'],
               'supported': True, 'provenance_incomplete': {'applies': False}}
        current = wiki_query.evidence_ref_object(row, source_validity='current')
        self.assertEqual(current['excerpt'], '受限原文')
        self.assertFalse(current['excerpt_withheld'])
        self.assertEqual(current['locator']['locator_kind'], 'pdf_region',
                         '§5.7 的 locator 里要带 locator_kind')
        self.assertEqual(current['fingerprint'], 'sha256:' + 'a' * 64)
        gone = wiki_query.evidence_ref_object(row, source_validity='unavailable',
                                              excerpt_allowed=False)
        self.assertEqual(gone['excerpt'], '', '§7.5：unavailable 不返回受限正文')
        self.assertTrue(gone['excerpt_withheld'])
        self.assertTrue(gone['excerpt_available'], '空 excerpt 与"被扣留"是两件事')
        empty = wiki_query.evidence_ref_object(dict(row, excerpt=''), source_validity='current')
        self.assertFalse(empty['excerpt_available'])
        self.assertFalse(empty['excerpt_withheld'], '检索到但没有原文 ≠ 被扣留')

    def test_the_entry_level_validity_aggregates_within_one_dimension_only(self):
        self.assertEqual(wiki_query.aggregate_source_validity(['current', 'stale']), 'stale')
        self.assertEqual(wiki_query.aggregate_source_validity(['current', 'unavailable']),
                         'unavailable')
        self.assertEqual(wiki_query.aggregate_source_validity(['current']), 'current')
        self.assertIsNone(wiki_query.aggregate_source_validity([]),
                          '一条引用都没有 = 未评估，绝不默认 current')

    def test_the_summary_counts_only_what_was_returned(self):
        summary = wiki_query.provenance_summary(
            evidence_refs=[{'evidence_ref_id': 'wev-1'}],
            runs=[{'run_id': 'run-1'}],
            events=[{'error_code': None}, {'error_code': 'INSUFFICIENT_EVIDENCE'}],
            source_validity=['current', 'stale'], event_count=9,
            provenance_incomplete={'applies': False})
        self.assertEqual(summary['evidence_ref_count'], 1)
        self.assertEqual(summary['run_count'], 1)
        self.assertEqual(summary['event_count'], 9, 'event_count 是总数，不是本页条数')
        self.assertEqual(summary['error_count'], 1)
        self.assertEqual(summary['source_validity'], {'current': 1, 'stale': 1, 'unavailable': 0})
        self.assertTrue(summary['provenance_complete'])
        broken = wiki_query.provenance_summary(events=[{'error_code': 'X'}],
                                               events_unavailable=True,
                                               provenance_incomplete={'applies': False})
        self.assertIsNone(broken['error_count'],
                          '读取失败时不给一个看起来像"数过了"的计数')
        self.assertTrue(broken['events_unavailable'])

    def test_the_provenance_sql_only_returns_visible_events(self):
        sql = MODULE_SOURCE
        self.assertIn("COALESCE(visibility, 'visible') = 'visible'", sql)
        self.assertIn('ORDER BY started_at, event_id', sql, '时间线按真实时间顺序')


# ---------------------------------------------------------------------------
# 7. #8 Markdown 导出
# ---------------------------------------------------------------------------

class WikiQueryExportTest(unittest.TestCase):

    def _markdown(self, **overrides):
        entry = {'entry_id': 'wiki-' + 'a' * 24, 'entry_type': 'metric', 'title': '设备 A102',
                 'demo_data': True,
                 'business_key': {'space_id': 'wsp-' + 'b' * 24, 'namespace': 'industrial-demo',
                                  'entry_type': 'metric', 'key_part': 'holding_temperature',
                                  'confirmation': 'pending'},
                 'status': {'publication': 'published', 'assertion': 'conflicted',
                            'source_validity': 'stale'}}
        revision = {'revision_id': 'wrev-' + 'c' * 24, 'base_revision_id': None,
                    'title': '设备 A102 的温度要求', 'body_markdown': '正文 [1]',
                    'body_sha256': 'd' * 64, 'publication_status': 'published',
                    'citations': [{'marker': '[1]', 'evidence_ref_id': 'wev-' + 'e' * 24}],
                    'author': {'user_id': 'u1', 'display_name': '演示用户'},
                    'generation': {'method': 'human', 'template_version': 'wiki-template-v1'}}
        evidence = [{'evidence_ref_id': 'wev-' + 'e' * 24, 'source_kind': 'doc',
                     'source_ref': 'doc-c43b1575ac57', 'source_validity': 'stale',
                     'excerpt': '续航|降噪开启约 38 小时',
                     'source_version': {'document_version': 1,
                                        'source_sha256': '805851ba',
                                        'source_snapshot_id': None},
                     'locator': {'locator_kind': 'pdf_region', 'page_no': 1,
                                 'block_id': 'blk-a7910ea1fc5c'}}]
        runs = [{'run_id': 'run-894b0785725c45f3', 'question': '续航是多少？',
                 'status': 'COMPLETED', 'event_count': 8,
                 'error_summary': {'error_code': None, 'none': True}}]
        values = {'entry': entry, 'revision': revision, 'evidence_refs': evidence, 'runs': runs,
                  'relations': [{'src': 'wiki-' + 'a' * 24, 'dst': 'wiki-' + 'f' * 24,
                                 'label': '支撑', 'relation_type': 'supports',
                                 'direction': 'directed', 'origin': 'projected',
                                 'raw_type': 'supports', 'supported': True}],
                  'status': entry['status'],
                  'provenance_incomplete': {'applies': True, 'recoverable': False,
                                            'missing_fields': ['edge.evidence'],
                                            'reason_code':
                                                'EDGE_COLUMNS_ABSENT_IN_DEPLOYED_SCHEMA'},
                  'exported_at': '2026-10-14T09:00:00+08:00'}
        values.update(overrides)
        return wiki_query.render_export_markdown(**values)

    def test_the_export_keeps_the_six_required_things(self):
        """§5.8：三套状态、引用、来源版本、运行标识、演示属性、provenance_incomplete。"""
        text = self._markdown()
        # 1. 知识状态：三套分别标注，不合并
        self.assertIn('`publication`', text)
        self.assertIn('`assertion`', text)
        self.assertIn('`source_validity`', text)
        self.assertIn('`conflicted`', text)
        self.assertNotIn('总状态', text)
        # 2. 引用：标记 + 清单 + 定位
        self.assertIn('[1]', text)
        self.assertIn('wev-' + 'e' * 24, text)
        self.assertIn('pdf_region', text)
        self.assertIn('blk-a7910ea1fc5c', text)
        # 3. 来源版本
        self.assertIn('805851ba', text)
        self.assertIn('document_version=1', text)
        # 4. 运行标识逐个
        self.assertIn('run-894b0785725c45f3', text)
        self.assertIn('本次运行无错误事件', text)
        # 5. 演示属性写进正文
        self.assertIn('demo_data: true', text)
        # 6. 出处缺失声明
        self.assertIn('出处信息不完整', text)
        self.assertIn('edge.evidence', text)
        self.assertIn('EDGE_COLUMNS_ABSENT_IN_DEPLOYED_SCHEMA', text)

    def test_the_export_never_merges_the_three_status_dimensions(self):
        text = self._markdown(status={'publication': 'published', 'assertion': 'unverified',
                                      'source_validity': 'unavailable'})
        self.assertIn('`published`', text)
        self.assertIn('`unverified`', text)
        self.assertIn('`unavailable`', text)
        self.assertIn('不表示事实成立', text)

    def test_the_export_declares_completeness_when_nothing_is_missing(self):
        text = self._markdown(provenance_incomplete={'applies': False})
        self.assertIn('applies: false', text)
        self.assertNotIn('出处信息不完整', text)

    def test_the_export_states_a_failed_trace_read_instead_of_showing_no_events(self):
        text = self._markdown(unavailable_note='本次导出的运行轨迹读取失败（boom）；这不是"没有事件"。')
        self.assertIn('读取失败', text)
        run_missing = self._markdown(runs=[{'run_id': 'run-1', 'question': 'q', 'status': 'FAILED',
                                           'event_count': 0,
                                           'error_summary': {'error_code': None,
                                                             'unavailable': True}}])
        self.assertIn('错误摘要不可用', run_missing)

    def test_the_export_headers_are_no_store_and_an_attachment(self):
        headers = wiki_query.export_headers('wiki-' + 'a' * 24)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(headers['Content-Disposition'],
                         'attachment; filename="wiki-%s.md"' % ('a' * 24))

    def test_more_than_300_citations_is_an_error_not_a_silent_truncation(self):
        self.assertEqual(wiki_query.export_ref_guard(300), 300)
        with self.assertRaises(HTTPException) as caught:
            wiki_query.export_ref_guard(301)
        self.assertEqual((caught.exception.status_code, code_of(caught.exception)),
                         (422, 'WIKI_EXPORT_TOO_LARGE'))


# ---------------------------------------------------------------------------
# 8. 边界：不碰 api.py、不重复实现已存在的服务层
# ---------------------------------------------------------------------------

class WikiQueryBoundaryTest(unittest.TestCase):

    def test_the_module_only_binds_the_five_read_routes(self):
        """本模块只做 #1/#2/#6/#7/#8；#3/#4/#5/#9–#14 在 `wiki.py` / `wiki_jobs.py` 里。"""
        self.assertEqual([path for _method, path, _name in wiki_query.ROUTE_BINDINGS],
                         ['/v1/wiki/entries', '/v1/wiki/spaces/{space_id}/graph',
                          '/v1/wiki/entries/{entry_id}/graph',
                          '/v1/wiki/entries/{entry_id}/provenance',
                          '/v1/wiki/entries/{entry_id}/export'])
        for _method, _path, name in wiki_query.ROUTE_BINDINGS:
            self.assertTrue(callable(getattr(wiki_query, name)), name)

    def test_the_module_does_not_reimplement_the_service_layer(self):
        """身份/权限/状态/游标都复用 `wiki.py`，本模块不自己再写一套。

        `wiki.status_dimensions` 不在列表里是因为它由 `wiki.entry_object` 间接调用
        （entry 对象里的 `status` 就是那三个字段），读侧不去重造第四种状态组装。
        """
        for reused in ('resolve_space', 'space_for_identity', 'user_id_of',
                       'is_admin', 'may_read_restricted', 'aggregate_assertion_status',
                       'entry_object', 'relation_object', 'encode_cursor', 'decode_cursor',
                       '_load_entry', '_load_revision', 'fail', '_no_nul', '_text'):
            self.assertIn(reused, MODULE_SOURCE, reused)

    def test_it_writes_nothing(self):
        """读层只发 SELECT：不 INSERT / UPDATE / DELETE（8 张 wiki 表保持 0 行）。"""
        for statement in (r'\bINSERT\s+INTO\b', r'\bUPDATE\s+\w+\s+SET\b', r'\bDELETE\s+FROM\b'):
            self.assertIsNone(re.search(statement, MODULE_SOURCE, re.I), statement)

    def test_the_graph_never_describes_itself_as_the_whole_database(self):
        self.assertIn('CURRENT_AUTHORIZED_SCOPE_GRAPH', MODULE_SOURCE)
        self.assertIn('WIKI_GRAPH_LIMIT_REACHED', MODULE_SOURCE)
        self.assertEqual(wiki_query.GRAPH_LIMIT_HINT, 'WIKI_GRAPH_LIMIT_REACHED')

    def test_the_frozen_limits_are_the_contract_numbers(self):
        self.assertEqual((wiki_query.CATALOGUE_LIMIT_DEFAULT, wiki_query.CATALOGUE_LIMIT_MAX),
                         (20, 50))
        self.assertEqual((wiki_query.GRAPH_BATCH_DEFAULT, wiki_query.GRAPH_BATCH_MAX), (50, 50))
        self.assertEqual((wiki_query.GRAPH_NODE_LIMIT, wiki_query.GRAPH_RELATION_LIMIT),
                         (100, 300))
        self.assertEqual((wiki_query.NEIGHBORHOOD_DEPTH_DEFAULT,
                          wiki_query.NEIGHBORHOOD_DEPTH_MAX), (1, 2))
        self.assertEqual((wiki_query.NEIGHBORHOOD_LIMIT_DEFAULT,
                          wiki_query.NEIGHBORHOOD_LIMIT_MAX), (50, 100))
        self.assertEqual((wiki_query.PROVENANCE_LIMIT_DEFAULT, wiki_query.PROVENANCE_LIMIT_MAX),
                         (50, 200))
        self.assertEqual(wiki_query.EXPORT_EVIDENCE_MAX, 300)

    def test_the_entry_column_list_comes_from_the_service_layer(self):
        for column in wiki._ENTRY_COLUMNS.split(','):
            self.assertIn('e.' + column.strip(), wiki_query.entry_columns_sql('e'))


# ---------------------------------------------------------------------------
# 9. 真库用例（缺 psycopg2 / 库时 skip；全部 ROLLBACK）
# ---------------------------------------------------------------------------

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


class SharedTransaction:
    """`with wiki.connect(dsn) as conn` 的替身：整条流程共用一个连接、**永不提交**。

    与 `tests/test_wiki_service.py:785-811` 同款：`__enter__` 打 SAVEPOINT，成功 RELEASE、
    失败 ROLLBACK TO，这样"某一步 404"不会连带撤销前面已经成功的步骤。
    """

    SAVEPOINT = 'wiki_query_call'

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


@unittest.skipUnless(DB, 'psycopg2 或 datamind_meta 不可用（真库用例需要 .venv 解释器）')
class WikiQueryDatabaseTest(unittest.TestCase):
    """真库端到端：两个真实 `auth_users` 行 + 一条属于 B 的词条，A 一律看不见。"""

    def setUp(self):
        if psycopg2 is None:  # pragma: no cover - skipUnless 已经挡住
            self.skipTest('psycopg2 不可用')
        self.dsn = wiki.wiki_dsn()
        self.conn = psycopg2.connect(self.dsn)
        real_connect = wiki.connect
        wiki.connect = lambda dsn: SharedTransaction(self.conn)
        self.addCleanup(setattr, wiki, 'connect', real_connect)
        self.addCleanup(self._rollback)
        suffix = uuid.uuid4().hex[:8]
        self.user_a = 'wiki-q-a-' + suffix
        self.user_b = 'wiki-q-b-' + suffix
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            for user_id in (self.user_a, self.user_b):
                cur.execute('''INSERT INTO auth_users(user_id, username, display_name,
                        password_hash, roles)
                    VALUES (%s,%s,'QueryTest','scrypt$00$00',ARRAY['reviewer','approver'])''',
                            (user_id, 'wiki-q-' + user_id))
        self.identity_a = {'user_id': self.user_a, 'roles': ['reviewer', 'approver']}
        self.identity_b = {'user_id': self.user_b, 'roles': ['reviewer', 'approver']}
        self.entry_b = self._make_entry(self.user_b, 'b-entry', '设备 B', 'published')
        self.entry_a = self._make_entry(self.user_a, 'a-entry', '设备 A', 'published')

    def _rollback(self):
        self.conn.rollback()
        self.conn.close()

    def _make_entry(self, owner, key_part, title, publication_status, *, author=None,
                    entry_type='business_entity', citations=None):
        """建一条真词条 + 一个真版本；发布头用 UPDATE 补（复合外键要求版本先存在，021:722-724）。"""
        from datamind import wiki as service
        entry_id_value = service.entry_id()
        revision_id_value = service.revision_id()
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('''INSERT INTO wiki_entries(entry_id, space_id, owner_id, entry_type,
                    namespace, key_part, confirmation, title, aliases, category, summary)
                VALUES (%s,%s,%s,%s,'default',%s,'confirmed',%s,%s,%s,%s)''',
                        (entry_id_value, service.space_for_user(owner), owner, entry_type,
                         key_part, title, ['设备', 'A102'], '工业设备', '合成演示词条'))
            cur.execute('''INSERT INTO wiki_revisions(revision_id, entry_id, base_revision_id,
                    title, body_markdown, body_sha256, citations, author_id, generation_method,
                    publication_status)
                VALUES (%s,%s,NULL,%s,%s,%s,%s::jsonb,%s,'human',%s)''',
                        (revision_id_value, entry_id_value, title, '正文 body [1]',
                         service.sha256_text('正文 body [1]'),
                         json.dumps(citations or []), author or owner, publication_status))
            cur.execute('''UPDATE wiki_entries SET head_revision_id=%s, publication_status=%s,
                    updated_at=now() WHERE entry_id=%s''',
                        (revision_id_value, publication_status, entry_id_value))
        return entry_id_value

    def _head_revision(self, entry_id_value):
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT head_revision_id FROM wiki_entries WHERE entry_id=%s',
                        (entry_id_value,))
            return cur.fetchone()['head_revision_id']

    def _make_relation(self, owning_entry, revision_id_value, src_id, dst_id,
                       relation_type='supports'):
        """直接插一条真关系行：绕开服务层的端点校验，模拟"库里已有指向别人的边"。"""
        relation_id_value = 'wrel-' + uuid.uuid4().hex[:24]
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('''INSERT INTO wiki_relations(relation_id, entry_id, revision_id,
                    src_kind, src_id, dst_kind, dst_id, relation_type, raw_type, origin,
                    is_symmetric, supported)
                VALUES (%s,%s,%s,'entry',%s,'entry',%s,%s,%s,'projected',FALSE,TRUE)''',
                        (relation_id_value, owning_entry, revision_id_value, src_id, dst_id,
                         relation_type, relation_type))
        return relation_id_value

    def _make_evidence_ref(self, space_id):
        from datamind import wiki as service
        evidence_ref_id = 'wev-' + uuid.uuid4().hex[:24]
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('''INSERT INTO wiki_evidence_refs(evidence_ref_id, space_id,
                    evidence_fingerprint, source_kind, source_ref, source_sha256,
                    document_version, locator_kind, locator, record_sha256, excerpt,
                    raw_node_types, supported, run_scoped)
                VALUES (%s,%s,%s,'doc','doc-c43b1575ac57',%s,1,'pdf_region',
                    %s::jsonb,%s,%s,ARRAY['Chunk'],TRUE,FALSE)''',
                        (evidence_ref_id, space_id, 'f' * 64, 'e' * 64,
                         json.dumps({'locator_kind': 'pdf_region', 'page_no': 1,
                                     'bbox': [62.7, 233.7, 532.6, 366.7],
                                     'block_id': 'blk-a7910ea1fc5c'}),
                         service.sha256_text('续航约 38 小时'), '续航|降噪开启约 38 小时'))
        return evidence_ref_id

    def _link_evidence(self, evidence_ref_id, revision_id_value, run_id):
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('''INSERT INTO wiki_provenance_links(provenance_link_id,
                    evidence_ref_id, run_id, evidence_id, revision_id)
                VALUES (%s,%s,%s,%s,%s)''',
                        ('wprov-' + uuid.uuid4().hex[:24], evidence_ref_id, run_id,
                         'ev-' + uuid.uuid4().hex[:16], revision_id_value))

    def test_the_catalogue_returns_only_the_requesters_entries(self):
        page = wiki_query.entry_catalogue(self.dsn, self.identity_a)
        ids = [entry['entry_id'] for entry in page['data']['entries']]
        self.assertEqual(ids, [self.entry_a], 'B 的词条不得出现在 A 的目录里')
        self.assertNotIn(self.entry_b, ids)
        self.assertEqual(page['data']['search'],
                         {'q': None, 'scope': ['title', 'aliases', 'category'],
                          'body_searched': False,
                          'uncovered_fields': ['body_markdown', 'summary'],
                          'reason_code': 'CHINESE_BODY_SEARCH_UNDECIDED'})
        self.assertFalse(page['paging']['has_more'])
        self.assertIsNone(page['paging']['next_cursor'])

    def test_the_catalogue_search_covers_title_alias_and_category(self):
        for query, expected in (('设备 A', True), ('A102', True), ('工业设备', True),
                                ('正文', False)):
            page = wiki_query.entry_catalogue(self.dsn, self.identity_a, q=query)
            found = [entry['entry_id'] for entry in page['data']['entries']]
            self.assertEqual(self.entry_a in found, expected, query)

    def test_the_percent_sign_is_a_literal_not_a_wildcard(self):
        page = wiki_query.entry_catalogue(self.dsn, self.identity_a, q='100%')
        self.assertEqual(page['data']['entries'], [])

    def test_the_status_filter_only_touches_the_publication_dimension(self):
        page = wiki_query.entry_catalogue(self.dsn, self.identity_a, status='draft')
        self.assertEqual(page['data']['entries'], [], '这一条是 published')

    def test_another_users_space_and_entry_are_404(self):
        with self.assertRaises(HTTPException) as caught:
            wiki_query.space_graph(wiki.space_for_user(self.user_b), self.dsn, self.identity_a)
        self.assertEqual((caught.exception.status_code, code_of(caught.exception)),
                         (404, 'WIKI_SPACE_NOT_FOUND'))
        for call in (lambda: wiki_query.entry_neighborhood(self.entry_b, self.dsn, self.identity_a),
                     lambda: wiki_query.entry_provenance(self.entry_b, self.dsn, self.identity_a),
                     lambda: wiki_query.entry_export(self.entry_b, self.dsn, self.identity_a)):
            with self.assertRaises(HTTPException) as caught:
                call()
            self.assertEqual((caught.exception.status_code, code_of(caught.exception)),
                             (404, 'WIKI_ENTRY_NOT_FOUND'),
                             '别人的词条必须与不存在**完全一致**（存在性隐藏）')

    def test_the_owner_sees_their_own_graph_provenance_and_export(self):
        graph = wiki_query.space_graph(wiki.space_for_user(self.user_a), self.dsn, self.identity_a)
        self.assertEqual(graph['data']['layer'], 'entries')
        self.assertEqual(graph['data']['limits'], {'nodes': 100, 'relations': 300})
        self.assertEqual(graph['data']['loaded'], {'nodes': 1, 'relations': 0})
        self.assertIsNone(graph['data']['limit_reached'])
        self.assertEqual(graph['data']['scope_note'], 'CURRENT_AUTHORIZED_SCOPE_GRAPH')
        node = graph['data']['nodes'][0]
        self.assertEqual(node['node_id'], self.entry_a)
        self.assertEqual(node['degree'], 0)
        self.assertEqual(node['degree_scope'], 'authorized_loaded')

        neighborhood = wiki_query.entry_neighborhood(self.entry_a, self.dsn, self.identity_a)
        self.assertEqual(neighborhood['data']['root'], self.entry_a)
        self.assertFalse(neighborhood['data']['truncated'])
        with self.assertRaises(HTTPException) as caught:
            wiki_query.entry_neighborhood(self.entry_a, self.dsn, self.identity_a, depth=3)
        self.assertEqual(code_of(caught.exception), 'WIKI_BUILD_REQUEST_INVALID')

        provenance = wiki_query.entry_provenance(self.entry_a, self.dsn, self.identity_a)
        self.assertEqual(provenance['data']['summary']['evidence_ref_count'], 0)
        self.assertFalse(provenance['data']['summary']['events_unavailable'],
                         '没有运行映射 ≠ 读取失败')
        self.assertEqual(provenance['data']['runs'], [])

        export = wiki_query.entry_export(self.entry_a, self.dsn, self.identity_a)
        self.assertIn('正文 body', export['markdown'])
        self.assertEqual(export['headers']['Cache-Control'], 'no-store')
        self.assertTrue(export['headers']['Content-Disposition'].startswith('attachment;'))
        self.assertEqual(export['citation_count'], 0)

    def test_a_draft_authored_by_somebody_else_is_a_404_without_edit_rights(self):
        """§3.1 脚注 1：草稿 / 待复核版本需要编辑权限，否则与不存在**完全相同**的 404。

        构造：词条属于 A，但草稿版本的作者是 B。于是空间门与 owner 门都通过，
        只剩"草稿需要编辑权限"这一道门——纯 viewer 被 404，reviewer 能读到。
        """
        service = wiki
        entry_id_value = self._make_entry(self.user_a, 'draft-by-other', '草稿指标', 'draft',
                                          author=self.user_b, entry_type='metric')
        revision_id_value = None
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT head_revision_id FROM wiki_entries WHERE entry_id=%s',
                        (entry_id_value,))
            revision_id_value = cur.fetchone()['head_revision_id']
        viewer = {'user_id': self.user_a, 'roles': ['viewer']}
        with self.assertRaises(HTTPException) as caught:
            wiki_query.entry_provenance(entry_id_value, self.dsn, viewer)
        self.assertEqual((caught.exception.status_code, code_of(caught.exception)),
                         (404, 'WIKI_ENTRY_NOT_FOUND'))
        with self.assertRaises(HTTPException) as caught:
            wiki_query.entry_export(entry_id_value, self.dsn, viewer)
        self.assertEqual(code_of(caught.exception), 'WIKI_ENTRY_NOT_FOUND',
                         '导出与页面走同一个授权投影，不能多看到一点')
        # reviewer 可以读草稿（发布前必须能读）：
        own = wiki_query.entry_provenance(entry_id_value, self.dsn, self.identity_a)
        self.assertEqual(own['data']['revision_id'], revision_id_value)

    def test_a_relation_to_an_unauthorized_entry_is_neither_counted_nor_drawn(self):
        """**关键验收**：连接数只算已授权已加载的连接——隐藏节点不参与计数。

        构造：A 的词条上真的有一条指向 B 私有词条的关系行（模拟库里已有的、指向别人的边）。
        A 的目录里这条词条的 `relation_counts.visible` 必须是 **0**，图里也不得出现这条边和
        B 的节点；同时断言**那一行确实在库里**，证明差别来自授权投影而不是"数据本来就没有"。
        """
        revision = self._head_revision(self.entry_a)
        relation_id_value = self._make_relation(self.entry_a, revision, self.entry_a, self.entry_b)
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT count(*) AS counted FROM wiki_relations WHERE revision_id=%s',
                        (revision,))
            self.assertEqual(int(cur.fetchone()['counted']), 1, '关系行确实在库里')

        page = wiki_query.entry_catalogue(self.dsn, self.identity_a)
        by_id = {entry['entry_id']: entry for entry in page['data']['entries']}
        self.assertEqual(by_id[self.entry_a]['relation_counts']['visible'], 0,
                         '指向未授权词条的边不进计数（否则用数字就能推断隐藏内容）')
        self.assertEqual(by_id[self.entry_a]['relation_counts']['scope'], 'authorized_loaded')

        graph = wiki_query.space_graph(wiki.space_for_user(self.user_a), self.dsn, self.identity_a)
        self.assertEqual([item['relation_id'] for item in graph['data']['relations']], [])
        self.assertNotIn(self.entry_b, [item['node_id'] for item in graph['data']['nodes']])
        self.assertEqual([item['degree'] for item in graph['data']['nodes']], [0])
        self.assertNotEqual(relation_id_value, None)

        # 反向对照：B 的目录里既没有 A 的词条，也看不到这条边（它对 B 而言也是跨空间的）。
        page_b = wiki_query.entry_catalogue(self.dsn, self.identity_b)
        self.assertEqual([entry['entry_id'] for entry in page_b['data']['entries']],
                         [self.entry_b])

    def test_an_authorized_relation_is_counted_drawn_and_exported(self):
        second = self._make_entry(self.user_a, 'a-entry-2', '设备 A2', 'published')
        revision = self._head_revision(self.entry_a)
        self._make_relation(self.entry_a, revision, self.entry_a, second)

        page = wiki_query.entry_catalogue(self.dsn, self.identity_a, limit=50)
        by_id = {entry['entry_id']: entry for entry in page['data']['entries']}
        self.assertEqual(by_id[self.entry_a]['relation_counts']['visible'], 1)
        self.assertEqual(by_id[self.entry_a]['status']['assertion'], 'unverified',
                         '没有主张就是未核验，不是已核验')

        graph = wiki_query.space_graph(wiki.space_for_user(self.user_a), self.dsn, self.identity_a)
        self.assertEqual(len(graph['data']['nodes']), 2)
        self.assertEqual(len(graph['data']['relations']), 1)
        relation = graph['data']['relations'][0]
        self.assertEqual((relation['src'], relation['dst']), (self.entry_a, second))
        self.assertEqual(relation['label'], '支撑')
        self.assertEqual(relation['direction'], 'directed')
        self.assertEqual([item['degree'] for item in graph['data']['nodes']], [1, 1],
                         '两端都在已加载集合内 → 各自算一条连接')

        neighborhood = wiki_query.entry_neighborhood(self.entry_a, self.dsn, self.identity_a)
        self.assertEqual(len(neighborhood['data']['nodes']), 2)
        self.assertEqual(len(neighborhood['data']['relations']), 1)
        self.assertFalse(neighborhood['data']['truncated'])
        two_hops = wiki_query.entry_neighborhood(self.entry_a, self.dsn, self.identity_a, depth=2)
        self.assertEqual(two_hops['data']['truncated'], False,
                         '没有更深邻居时不该假报"被深度截断"')

    def test_cross_batch_half_edges_are_deferred_until_both_ends_are_loaded(self):
        """§4 语义 6：一批里只加载到一端时，那条边**不返回**；两端都加载后才给。"""
        second = self._make_entry(self.user_a, 'a-entry-2', '设备 A2', 'published')
        revision = self._head_revision(self.entry_a)
        self._make_relation(self.entry_a, revision, self.entry_a, second)
        space = wiki.space_for_user(self.user_a)
        first = wiki_query.space_graph(space, self.dsn, self.identity_a, limit=1)
        self.assertEqual(len(first['data']['nodes']), 1)
        self.assertEqual(first['data']['relations'], [],
                         '另一端还没加载 → 半条边不返回')
        self.assertTrue(first['paging']['has_more'])
        second_page = wiki_query.space_graph(space, self.dsn, self.identity_a, limit=1,
                                             cursor=first['paging']['next_cursor'])
        self.assertEqual(len(second_page['data']['nodes']), 1)
        self.assertEqual(len(second_page['data']['relations']), 1, '两端都在了 → 返回这条边')
        self.assertEqual(second_page['data']['loaded'], {'nodes': 2, 'relations': 1})
        # 换了 limit（不是筛选）不算换集合，游标继续有效；换 layer 才重置。
        self.assertNotIn(wiki_query.CURSOR_RESET_ON_FILTER_CHANGE, second_page['warnings'])
        other_layer = wiki_query.space_graph(space, self.dsn, self.identity_a, layer='evidence',
                                             cursor=first['paging']['next_cursor'])
        self.assertIn(wiki_query.CURSOR_RESET_ON_FILTER_CHANGE, other_layer['warnings'])
        self.assertEqual(other_layer['data']['loaded'], {'nodes': 0, 'relations': 0},
                         '重置后是第一批，不是接着上一批')

    def test_the_evidence_and_runs_layers_show_the_provenance_links(self):
        """#2 的 evidence / runs 图层与 #7 的来源展开走**同一份**授权投影。"""
        run_id = 'run-894b0785725c45f3'
        ref = self._make_evidence_ref(wiki.space_for_user(self.user_a))
        revision = self._head_revision(self.entry_a)
        self._link_evidence(ref, revision, run_id)
        space = wiki.space_for_user(self.user_a)

        evidence_layer = wiki_query.space_graph(space, self.dsn, self.identity_a,
                                                layer='evidence')
        self.assertEqual(evidence_layer['data']['layer'], 'evidence')
        self.assertEqual([item['node_kind'] for item in evidence_layer['data']['nodes']],
                         ['evidence_ref'])
        node = evidence_layer['data']['nodes'][0]
        self.assertEqual(node['node_id'], ref)
        self.assertEqual(node['evidence_ref']['source_validity'], 'current',
                         'document_assets 里 doc_version=1 且引用记录的也是 1')

        runs_layer = wiki_query.space_graph(space, self.dsn, self.identity_a, layer='runs')
        kinds = sorted(item['node_kind'] for item in runs_layer['data']['nodes'])
        self.assertEqual(kinds, ['evidence_ref', 'run'])
        self.assertEqual(len(runs_layer['data']['relations']), 1)
        edge = runs_layer['data']['relations'][0]
        self.assertEqual((edge['src'], edge['dst']), (ref, run_id),
                         '边来自 wiki_provenance_links（运行 id 不能是 wiki_relations 的端点）')
        self.assertEqual(edge['relation_id'][:6], 'wprov-')
        run_node = next(item for item in runs_layer['data']['nodes'] if item['node_kind'] == 'run')
        self.assertEqual(run_node['run']['error_summary'], {'error_code': None, 'none': True},
                         '该运行的无错误是**查过了**得出的，不是默认值')
        self.assertEqual(run_node['run']['event_count'], 8)
        self.assertTrue(run_node['run']['conversation_attributed'])
        with self.assertRaises(HTTPException) as caught:
            wiki_query.space_graph(space, self.dsn, self.identity_a, layer='evidence', q='设备')
        self.assertEqual(code_of(caught.exception), 'WIKI_BUILD_REQUEST_INVALID',
                         '词条筛选用在非词条图层上必须显式拒绝，不能默默忽略')

    def test_provenance_and_export_expose_evidence_runs_and_events(self):
        run_id = 'run-894b0785725c45f3'
        ref = self._make_evidence_ref(wiki.space_for_user(self.user_a))
        entry_id_value = self._make_entry(
            self.user_a, 'a-entry-cited', '设备 A 引用', 'published',
            citations=[{'marker': '[1]', 'evidence_ref_id': ref}])
        revision = self._head_revision(entry_id_value)
        self._link_evidence(ref, revision, run_id)

        provenance = wiki_query.entry_provenance(entry_id_value, self.dsn, self.identity_a)
        data = provenance['data']
        self.assertEqual(data['revision_id'], revision)
        summary = data['summary']
        self.assertEqual(summary['evidence_ref_count'], 1)
        self.assertEqual(summary['run_count'], 1)
        self.assertEqual(summary['event_count'], 8)
        self.assertEqual(summary['error_count'], 0,
                         '查过了、可见事件里没有错误 → 0；只有读取失败才是 None')
        self.assertFalse(summary['events_unavailable'])
        self.assertEqual(summary['source_validity'],
                         {'current': 1, 'stale': 0, 'unavailable': 0})
        self.assertEqual(data['evidence_refs'][0]['evidence_ref_id'], ref)
        self.assertEqual(data['evidence_refs'][0]['excerpt'], '续航|降噪开启约 38 小时')
        self.assertFalse(data['evidence_refs'][0]['excerpt_withheld'])
        self.assertEqual(data['runs'][0]['run_id'], run_id)
        self.assertEqual(data['runs'][0]['error_summary'], {'error_code': None, 'none': True})
        self.assertEqual(data['runs'][0]['event_count'], 8)
        self.assertIn('run.started', data['runs'][0]['event_types'])
        self.assertEqual(data['events'][0]['event_type'], 'run.started', '时间线按真实顺序')
        self.assertEqual(len(data['events']), 8)
        self.assertFalse(provenance['paging']['has_more'])
        # 只有证据、不要轨迹时：不给 events/runs，也不撒谎说"没有事件"。
        evidence_only = wiki_query.entry_provenance(entry_id_value, self.dsn, self.identity_a,
                                                   layer='evidence')
        self.assertNotIn('events', evidence_only['data'])
        self.assertNotIn('runs', evidence_only['data'])
        self.assertEqual(evidence_only['data']['summary']['event_count'], 8)

        export = wiki_query.entry_export(entry_id_value, self.dsn, self.identity_a)
        text = export['markdown']
        self.assertIn('[1]', text)
        self.assertIn(ref, text)
        self.assertIn(run_id, text)
        self.assertIn('续航|降噪开启约 38 小时', text)
        self.assertIn('doc-c43b1575ac57', text)
        self.assertIn('本次运行无错误事件', text)
        self.assertIn('`current`', text)
        self.assertEqual(export['citation_count'], 1)
        self.assertGreater(len(text.encode('utf-8')), 0)

        # 固定版本校验：摘要不一致 → 409（不静默导出另一版）。
        with self.assertRaises(HTTPException) as caught:
            wiki_query.entry_export(entry_id_value, self.dsn, self.identity_a,
                                    expected_body_sha256='0' * 64)
        self.assertEqual((caught.exception.status_code, code_of(caught.exception)),
                         (409, 'WIKI_VERSION_CONFLICT'))
        matched = wiki_query.entry_export(entry_id_value, self.dsn, self.identity_a,
                                          expected_body_sha256=export['body_sha256'])
        self.assertEqual(matched['revision_id'], revision)

        # 事件分页：游标不透明、按真实时间顺序翻页、总数不随分页变化。
        page = wiki_query.entry_provenance(entry_id_value, self.dsn, self.identity_a, limit=3)
        self.assertEqual(len(page['data']['events']), 3)
        self.assertTrue(page['paging']['has_more'])
        self.assertEqual(page['data']['summary']['event_count'], 8, '总数与分页无关')
        self.assertNotIn('-', page['paging']['next_cursor'], '游标是 base64url 不透明串')
        following = wiki_query.entry_provenance(entry_id_value, self.dsn, self.identity_a, limit=3,
                                                cursor=page['paging']['next_cursor'])
        self.assertEqual(len(following['data']['events']), 3)
        self.assertNotEqual([event['event_id'] for event in following['data']['events']],
                            [event['event_id'] for event in page['data']['events']])
        self.assertFalse(following['data']['summary']['events_unavailable'])
        self.assertFalse(following['paging']['truncated'],
                         '事件可以继续翻页 → 不是"因上限截断"')

    def test_the_updated_since_window_filters_the_catalogue(self):
        page = wiki_query.entry_catalogue(
            self.dsn, self.identity_a, updated_since='2020-01-01T00:00:00+08:00')
        self.assertEqual([entry['entry_id'] for entry in page['data']['entries']],
                         [self.entry_a])
        empty = wiki_query.entry_catalogue(
            self.dsn, self.identity_a, updated_since='2099-01-01T00:00:00+08:00')
        self.assertEqual(empty['data']['entries'], [])

    def test_the_catalogue_pages_with_an_opaque_cursor(self):
        first = wiki_query.entry_catalogue(self.dsn, self.identity_a, limit=1)
        cursor = first['paging']['next_cursor']
        self.assertIsNone(cursor, '库里 A 只有一条词条')
        # 造第二条再翻页。
        self._make_entry(self.user_a, 'a-entry-2', '设备 A2', 'draft')
        first = wiki_query.entry_catalogue(self.dsn, self.identity_a, limit=1)
        self.assertTrue(first['paging']['has_more'])
        self.assertIsNotNone(first['paging']['next_cursor'])
        second = wiki_query.entry_catalogue(self.dsn, self.identity_a, limit=1,
                                            cursor=first['paging']['next_cursor'])
        self.assertEqual(len(second['data']['entries']), 1)
        self.assertNotEqual(second['data']['entries'][0]['entry_id'],
                            first['data']['entries'][0]['entry_id'])
        # 筛选变了 → 游标被重置，并且**明确告知**客户端。
        changed = wiki_query.entry_catalogue(self.dsn, self.identity_a, limit=1,
                                             cursor=first['paging']['next_cursor'],
                                             q='设备 A')
        self.assertIn(wiki_query.CURSOR_RESET_ON_FILTER_CHANGE, changed['warnings'])
        self.assertFalse(changed['paging']['truncated'])


@unittest.skipUnless(DB, 'psycopg2 或 datamind_meta 不可用')
class ZZWikiQueryTablesStayEmptyTest(unittest.TestCase):
    """自检：本文件的真库用例**没有**在 8 张 wiki 表里留下数据（类名 ZZ 保证最后执行）。"""

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
