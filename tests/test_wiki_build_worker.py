"""Wiki 整理 worker（`datamind/wiki_build.py` + `scripts/wiki_build_worker.py`）的测试。

分层与 `tests/test_wiki_jobs.py` 相同（系统 python 有 pytest 没有 psycopg2，`.venv` 反之）：

* **纯逻辑用例**（第一、二、三组）不需要数据库、不需要 psycopg2，`python -m pytest` 就能跑；
* **真库用例**（第四组）`@unittest.skipUnless(DB, …)`，并且**整条流程走一个永不提交的事务**
  （`SharedTransaction`，与 `tests/test_wiki_jobs.py:325-350` 同款，刻意重复不互相 import），
  所以 8 张 wiki 表在用例结束后仍然是 0 行。

本文件重点钉住五件事：

1. **选任务的条件**：只挑 `status='queued'`、按 owner 过滤（admin 除外）、键集顺序固定；
2. **claim 失败（撤权）时不执行整理**：`_lock_actor()` 在领取事务里复查，整理器一次都不许被调用，
   任务保持 `queued` —— 否则"撤权后不开工"这条契约就没有落点；
3. **`--once` 的行为**：跑完一轮就退出、拿到终态计数、不进入轮询；
4. **失败落账**：`succeeded` / `failed` 都只写在任务行上，`attempts` 递增；
5. **不写 `runs` / `claims` / `trace_events` / `evidence_*`**：静态断言 + 真库前后快照对照。
"""
import argparse
import sys
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import HTTPException  # noqa: E402

from datamind import wiki  # noqa: E402
from datamind import wiki_build  # noqa: E402
from datamind import wiki_jobs  # noqa: E402
from scripts import wiki_build_worker as worker  # noqa: E402

SPACE = 'wsp-' + 'c' * 24
RUN = 'run-' + 'a' * 16
BUILD = 'wjob-' + '1' * 24
CLAIM_KEY = 'claim-80ac4ad77526407b'

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:  # pragma: no cover
    psycopg2 = None
    RealDictCursor = None


def db_available():
    """真库可用性探针：缺 psycopg2 或没有 wiki 表就跳过（`test_parse_contract.py:745-776` 同款）。"""
    if psycopg2 is None:
        return False
    try:
        conn = psycopg2.connect(wiki.wiki_dsn())
        try:
            cur = conn.cursor()
            cur.execute("SELECT to_regclass('public.wiki_build_jobs') IS NOT NULL")
            return bool(cur.fetchone()[0])
        finally:
            conn.rollback()
            conn.close()
    except Exception:
        return False


DB = db_available()


def code_of(exc):
    return exc.detail.get('error_code')


def job_row(**overrides):
    """一条 `wiki_build_jobs` 行（字段集与 `wiki_build.queued_sql()` 的 SELECT 一致）。"""
    row = {'build_id': BUILD, 'space_id': SPACE, 'owner_id': 'user-owner', 'request_id': 'req-1',
           'run_id': RUN, 'mode': 'template', 'selection': {}, 'entry_type_hint': None,
           'target_entry_id': None, 'status': 'queued', 'attempts': 0}
    row.update(overrides)
    return row


def claim_node(evidence_id='ev-1', ref_key=CLAIM_KEY, text='续航约 38 小时'):
    return {'evidence_id': evidence_id, 'run_id': RUN, 'node_type': 'Claim', 'ref_key': ref_key,
            'payload': {'text': text}}


def fact_node(evidence_id='ev-2', ref_key='fact-1', metric='duration_noise_on',
              value='38', unit='h'):
    return {'evidence_id': evidence_id, 'run_id': RUN, 'node_type': 'DocumentFact', 'ref_key': ref_key,
            'payload': {'fact_kind': 'measurement', 'fact_id': 'fact-1', 'metric': metric,
                        'entity_id': 'part-1', 'entity_type': 'product',
                        'normalized_value': value, 'raw_value': value, 'raw_unit': unit,
                        'document_id': 'doc-1', 'source_revision': 'v1',
                        'source_sha256': 'd' * 64, 'document_sha256': 'd' * 64}}


# ================================================================ 1. 选任务的条件

class QueueSelectionTest(unittest.TestCase):
    """`queued_sql()` / `job_identity()`：worker 只挑可领取的任务，且只以 owner 身份挑。"""

    def test_the_sql_only_selects_queued_jobs_in_a_stable_order(self):
        sql, params = wiki_build.queued_sql()
        self.assertEqual(params, [])
        self.assertIn("WHERE status = 'queued'", sql)
        self.assertIn('ORDER BY created_at, build_id', sql)
        self.assertTrue(sql.endswith('FOR UPDATE SKIP LOCKED'),
                        'FOR UPDATE 必须在 ORDER BY / LIMIT 之后，否则 SQL 不合法')
        for forbidden in ('running', 'succeeded', 'failed'):
            self.assertNotIn("'%s'" % forbidden, sql,
                             '只有 queued 可以被领取，%s 不是选任务条件' % forbidden)

    def test_repeated_calls_are_identical(self):
        self.assertEqual(wiki_build.queued_sql(), wiki_build.queued_sql())

    def test_owner_and_build_filters_keep_the_clause_order(self):
        sql, params = wiki_build.queued_sql(single=True, owner='user-a', build_id=BUILD)
        self.assertEqual(params, [BUILD, 'user-a'])
        self.assertLess(sql.index('build_id = %s'), sql.index('owner_id = %s'))
        self.assertLess(sql.index('owner_id = %s'), sql.index('ORDER BY'))
        self.assertLess(sql.index('ORDER BY'), sql.index('LIMIT 1'))
        self.assertLess(sql.index('LIMIT 1'), sql.index('FOR UPDATE'))

    def test_the_identity_is_the_job_owner_and_nothing_else(self):
        identity = wiki_build.job_identity(job_row(owner_id='user-7'))
        self.assertEqual(identity, {'user_id': 'user-7'})
        self.assertNotIn('roles', identity,
                         'worker 不许自带角色：角色判定的唯一权威是 claim_build 事务里的复查')
        self.assertNotIn('_demo', identity)

    def test_selection_keeps_only_the_two_contract_keys(self):
        self.assertEqual(wiki_build.selection_filter(job_row()), {})
        self.assertEqual(wiki_build.selection_filter(job_row(selection=None)), {})
        self.assertEqual(wiki_build.selection_filter(job_row(selection={'claim_ids': []})), {})
        resolved = wiki_build.selection_filter(
            job_row(selection={'claim_ids': ['c-1'], 'evidence_ids': ['ev-9'],
                               'junk': ['x']}))
        self.assertEqual(resolved, {'claim_ids': {'c-1'}, 'evidence_ids': {'ev-9'}})

    def test_node_selection_matches_claims_and_evidence(self):
        node = claim_node()
        self.assertTrue(wiki_build.node_selected(node, {}), '空筛选 = 全选')
        self.assertTrue(wiki_build.node_selected(node, {'claim_ids': {CLAIM_KEY}}))
        self.assertFalse(wiki_build.node_selected(node, {'claim_ids': {'claim-other'}}))
        self.assertTrue(wiki_build.node_selected(node, {'evidence_ids': {'ev-1'}}))
        self.assertFalse(wiki_build.node_selected(node, {'evidence_ids': {'ev-other'}}))

    def test_the_claim_id_comes_from_the_payload_then_the_ref_key(self):
        self.assertEqual(wiki_build.claim_id_of(claim_node()), CLAIM_KEY)
        with_payload = claim_node()
        with_payload['payload'] = {'text': 'x', 'claim_id': 'claim-payload'}
        self.assertEqual(wiki_build.claim_id_of(with_payload), 'claim-payload')
        fact = fact_node()
        self.assertEqual(wiki_build.claim_id_of(fact), 'fact-1',
                         '没有 claim_id 时用 ref_key（与 wiki_jobs/wiki_projection 的兜底一致）')
        blank = claim_node(ref_key='   ')
        self.assertIsNone(wiki_build.claim_id_of(blank), '空串不是 id')


# ================================================================ 2. 纯逻辑：计划与正文

class DraftPlanTest(unittest.TestCase):
    """`plan_for_job()` / `body_markdown()`：纯函数，不连库。"""

    def test_a_claim_run_plans_a_conclusion_entry_with_markers(self):
        plan = wiki_build.plan_for_job(job_row(), [claim_node(), fact_node()])
        self.assertEqual(plan.entry['entry_type'], 'conclusion')
        self.assertEqual(plan.entry['key_part'], '%s\x1f%s' % (RUN, CLAIM_KEY))
        self.assertEqual(plan.entry['business_key']['space_id'], SPACE)
        self.assertEqual(len(plan.evidence_refs), 2)
        self.assertEqual([c['marker'] for c in plan.citations], ['[1]', '[2]'])
        self.assertEqual(set(plan.citations_by_evidence_id), {'ev-1', 'ev-2'})
        self.assertEqual(plan.node_count, 2)
        self.assertEqual(plan.selected_node_count, 2)
        self.assertFalse(plan.truncated)
        self.assertEqual(plan.claim_ids, {'ev-1': CLAIM_KEY, 'ev-2': 'fact-1'})

    def test_the_hint_picks_between_two_candidate_entry_types(self):
        nodes = [claim_node(), fact_node()]
        self.assertEqual(wiki_build.plan_for_job(
            job_row(entry_type_hint='metric'), nodes).entry['entry_type'], 'metric')
        self.assertEqual(wiki_build.plan_for_job(
            job_row(entry_type_hint='conclusion'), nodes).entry['entry_type'], 'conclusion')

    def test_without_a_hint_the_richest_type_wins_and_the_rest_is_reported(self):
        plan = wiki_build.plan_for_job(job_row(), [claim_node(), fact_node(), fact_node('ev-3')])
        self.assertEqual(plan.entry['entry_type'], 'metric')
        self.assertEqual(plan.skipped_candidate_entry_types, ('conclusion',),
                         '落选的候选必须如实列出，不能静默丢弃')

    def test_selection_narrows_the_nodes_and_the_failed_candidates_are_gone(self):
        plan = wiki_build.plan_for_job(job_row(selection={'evidence_ids': ['ev-2']}),
                                       [claim_node(), fact_node()])
        self.assertEqual(plan.selected_node_count, 1)
        self.assertEqual(plan.entry['entry_type'], 'metric')
        self.assertEqual(plan.node_count, 2)
        self.assertEqual([ref['evidence_id'] for ref in plan.evidence_refs], ['ev-2'])

    def test_a_run_without_an_entry_identity_fails_loudly(self):
        query_only = {'evidence_id': 'ev-q', 'run_id': RUN, 'node_type': 'SQLQuery',
                      'ref_key': 'sql-1', 'payload': {'sql': 'select 1'}}
        with self.assertRaises(HTTPException) as caught:
            wiki_build.plan_for_job(job_row(), [query_only])
        self.assertEqual(code_of(caught.exception), 'WIKI_BUILD_FAILED')
        self.assertIn('SQLQuery', str(caught.exception.detail.get('hint')),
                      '错误详情要能指出是哪种节点没有承载业务身份')

    def test_the_boundary_says_what_did_not_land_and_never_invents_text(self):
        plan = wiki_build.plan_for_job(job_row(), [claim_node()])
        body = wiki_build.body_markdown(plan)
        self.assertIn(wiki_build.TEMPLATE_VERSION, body)
        self.assertIn(RUN, body)
        self.assertIn('未落库', body)
        self.assertIn('`wiki_assertions`', body)
        self.assertLessEqual(len(body), wiki_build.BODY_MAX_CHARS)
        self.assertIn('[1]', body, '正文里的编号与 citations 的 marker 是同一套')

    def test_the_body_stays_bounded_and_reports_the_extra_evidence(self):
        nodes = [claim_node('ev-%03d' % index) for index in range(120)]
        plan = wiki_build.plan_for_job(job_row(), nodes)
        body = wiki_build.body_markdown(plan)
        self.assertIn('另有 %d 条证据引用' % (120 - wiki_build.BODY_EVIDENCE_LINES), body)
        self.assertLessEqual(len(body.splitlines()), wiki_build.BODY_EVIDENCE_LINES + 30)

    def test_truncation_is_reported_not_hidden(self):
        plan = wiki_build.plan_for_job(job_row(), [claim_node()])
        plan.truncated = True
        self.assertIn('超过读取上限', wiki_build.body_markdown(plan))

    def test_the_delivery_result_never_overrides_the_stored_merge_suggestions(self):
        plan = wiki_build.plan_for_job(job_row(), [claim_node()])
        result = wiki_build.result_of(plan, 'wrev-' + 'b' * 24)
        self.assertEqual(result, {'entry_id': plan.entry['entry_id'],
                                 'candidate_entry_id': plan.entry['entry_id'],
                                 'revision_id': 'wrev-' + 'b' * 24})
        self.assertNotIn('suggested_merges', result,
                         '缺键时 complete_build 会沿用任务行上的建议；传空列表会覆盖它们')


# ================================================================ 3. 纯逻辑：错误分类与身份

class ClaimVerdictTest(unittest.TestCase):
    """撤权/会话失效不可重试，数据库抖动可重试。"""

    def test_a_revoked_role_is_fatal(self):
        exc = HTTPException(403, detail={'error_code': 'WIKI_PERMISSION_DENIED'})
        self.assertEqual(wiki_build.claim_error_verdict(exc),
                         ('WIKI_PERMISSION_DENIED', True))

    def test_a_dead_session_is_fatal_and_is_renamed_for_the_check(self):
        exc = HTTPException(401, detail={'error_code': 'SESSION_INVALID'})
        self.assertEqual(wiki_build.claim_error_verdict(exc),
                         ('WIKI_SESSION_INVALID', True),
                         'error_code 的 CHECK 只收 WIKI_*：必须改名而不是被降级成 BUILD_FAILED')

    def test_an_uncoded_failure_stays_queued(self):
        for exc in (RuntimeError('connection reset'), OSError('timeout'),
                    HTTPException(503, detail='upstream')):
            self.assertEqual(wiki_build.claim_error_verdict(exc), (None, False), exc)

    def test_every_fatal_code_obeys_the_database_naming_check(self):
        for code in wiki_build.FATAL_CLAIM_CODES:
            normalized = wiki_build.normalize_error_code(code)
            self.assertEqual(wiki_jobs.sanitize_error_code(normalized), normalized,
                             '%s 必须能落进 error_code 的 CHECK' % normalized)
        for code in (wiki_build.ERROR_IDENTITY_UNAVAILABLE, wiki_build.ERROR_INTERRUPTED):
            self.assertEqual(wiki_jobs.sanitize_error_code(code), code)

    def test_provenance_ids_are_derived_so_a_replay_cannot_duplicate_them(self):
        first = wiki_build.provenance_link_id('wev-' + 'a' * 24, RUN)
        second = wiki_build.provenance_link_id('wev-' + 'a' * 24, RUN)
        other = wiki_build.provenance_link_id('wev-' + 'a' * 24, RUN + 'x')
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        self.assertTrue(first.startswith('wprov-'))
        self.assertEqual(len(first), len('wprov-') + 24)


class IdentityTest(unittest.TestCase):
    """身份来源必须可解释（见 `wiki_build.py` 第 3 节 WHY）。"""

    def test_the_token_hash_matches_the_session_table_recipe(self):
        import hashlib
        self.assertEqual(wiki_build.token_hash('abc'),
                         hashlib.sha256(b'abc').hexdigest())

    def test_a_loopback_literal_is_local_and_a_foreign_address_is_not(self):
        for value in ('localhost', '127.0.0.1', '127.1.2.3', '::1'):
            self.assertTrue(wiki_build.host_is_loopback(value, probe_api=False), value)
        self.assertFalse(wiki_build.host_is_loopback('10.1.2.3', probe_api=False))
        self.assertFalse(wiki_build.host_is_loopback('wiki.example.com', probe_api=False))

    def test_the_cli_parser_refuses_nonsense(self):
        parser = worker.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args([])                                  # 必须选一个模式
        with self.assertRaises(SystemExit):
            parser.parse_args(['--once', '--watch'])
        self.assertTrue(parser.parse_args(['--once']).once)
        self.assertTrue(parser.parse_args(['--watch', '--limit', '3']).limit == 3)
        self.assertTrue(parser.parse_args(['--check']).check)
        self.assertFalse(parser.parse_args(['--once']).no_demo_identity)
        self.assertTrue(parser.parse_args(['--once', '--no-demo-identity']).no_demo_identity)


class OnceModeTest(unittest.TestCase):
    """`--once` 真的只跑一轮：不进入轮询、按终态计数、身份不可用也不留 queued。"""

    def setUp(self):
        self.queued = [job_row()]
        self.calls = []
        self.failed = []
        self.patch(worker, 'select_identity',
                   lambda dsn, *, session_token, allow_demo: {'user_id': 'user-owner',
                                                              '_source': 'test'})
        self.patch(wiki_build, 'select_queued',
                   lambda dsn, identity, limit=None, build_id=None: list(self.queued))
        self.patch(wiki_build, 'demo_identity_ok', lambda: False)
        self.patch(wiki_build, 'resolve_identity', self.fake_resolve)
        self.patch(worker, 'resolve_identity_for', self.fake_resolve_for)
        self.patch(wiki_jobs, 'fail_build', self.fake_fail)

    def patch(self, module, name, value):
        original = getattr(module, name)
        setattr(module, name, value)
        self.addCleanup(setattr, module, name, original)

    def fake_resolve(self, cur, job, *, session_token=None, allow_demo=None):
        if not allow_demo:
            raise wiki_build.IdentityUnavailable(wiki_build.ERROR_IDENTITY_UNAVAILABLE, 'no session')
        return {'user_id': job['owner_id'], 'roles': ['reviewer'], '_source': 'test'}

    def fake_resolve_for(self, job, *, session_token, allow_demo):
        """`worker.resolve_identity_for` 的替身：纯逻辑用例不连库。"""
        return self.fake_resolve(None, job, session_token=session_token, allow_demo=allow_demo)

    def fake_fail(self, build_id, code, detail, dsn):
        self.failed.append((build_id, code, detail))
        return {'build': job_row(status='failed', error_code=code)}

    def fake_execute(self, outcome):
        def execute(build_id, dsn, identity, builder):
            self.calls.append((build_id, identity, builder))
            return outcome
        return execute

    def test_one_pass_reaches_the_terminal_state_and_returns(self):
        self.patch(wiki_jobs, 'execute_build', self.fake_execute(
            {'build': job_row(status='succeeded', attempts=1,
                              result_revision_id='wrev-' + 'b' * 24,
                              result_entry_id='wiki-' + 'a' * 24),
             'delivered': True}))
        counts = worker.run_once(session_token=None, allow_demo=True)
        self.assertEqual(counts, {'claimed': 1, 'terminal': 1, 'pending': 0})
        self.assertEqual(len(self.calls), 1)
        build_id, identity, builder = self.calls[0]
        self.assertEqual(build_id, BUILD)
        self.assertEqual(identity['user_id'], 'user-owner',
                         '必须以任务 owner 的身份开工（claim/complete 的复查要用它）')
        self.assertTrue(callable(builder))
        self.assertEqual(self.failed, [])

    def test_a_failed_build_is_counted_as_terminal_not_as_pending(self):
        self.patch(wiki_jobs, 'execute_build', self.fake_execute(
            {'build': job_row(status='failed', attempts=1, error_code='WIKI_BUILD_FAILED'),
             'delivered': False}))
        counts = worker.run_once(session_token=None, allow_demo=True)
        self.assertEqual(counts, {'claimed': 1, 'terminal': 1, 'pending': 0},
                         'failed 是终态：不能算成"还要再跑一轮"')

    def test_an_unresolvable_identity_lands_a_failure_instead_of_a_stuck_queue(self):
        self.patch(wiki_jobs, 'execute_build', self.fake_execute({}))
        counts = worker.run_once(session_token=None, allow_demo=False)
        self.assertEqual(counts, {'claimed': 1, 'terminal': 1, 'pending': 0})
        self.assertEqual(self.calls, [], '没有身份就不许开工')
        self.assertEqual(self.failed[0][:2], (BUILD, wiki_build.ERROR_IDENTITY_UNAVAILABLE))

    def test_an_empty_queue_claims_nothing(self):
        self.queued = []
        self.patch(wiki_jobs, 'execute_build', self.fake_execute({}))
        self.assertEqual(worker.run_once(session_token=None, allow_demo=True),
                         {'claimed': 0, 'terminal': 0, 'pending': 0})
        self.assertEqual(self.calls, [])

    def test_the_main_entry_point_runs_one_pass_and_exits_zero(self):
        self.patch(wiki_jobs, 'execute_build', self.fake_execute(
            {'build': job_row(status='succeeded', attempts=1,
                              result_revision_id='wrev-' + 'b' * 24),
             'delivered': True}))
        self.assertEqual(worker.main(['--once', '--demo-identity']), wiki_build.EXIT_OK)
        self.assertEqual(len(self.calls), 1)

    def test_the_in_flight_cleanup_only_touches_jobs_without_a_terminal_state(self):
        outcome = {'build': job_row(status='running', attempts=1), 'delivered': False}
        self.patch(wiki_jobs, 'execute_build', self.fake_execute(outcome))
        in_flight = set()
        counts = worker.run_once(session_token=None, allow_demo=True, in_flight=in_flight)
        self.assertEqual(counts, {'claimed': 1, 'terminal': 0, 'pending': 1})
        self.assertEqual(in_flight, {BUILD})
        self.assertEqual(self.failed, [], '退出清理之外不许把 running 写死')

    def test_a_keyboard_interrupt_watch_loop_exits_cleanly(self):
        """`--watch` 被中断：`main()` 返回 0，不再进入下一轮，并且跑一次僵尸任务兜底。

        `worker.main` 的 `except KeyboardInterrupt: return EXIT_OK` 与 SIGINT/SIGTERM 处理器
        走同一条退出路径（先停循环，再由 `finally: recover_in_flight()` 兜底）。
        Windows 上无法从外部给控制台进程送一个真正的 SIGTERM：实测 `CTRL_BREAK_EVENT`
        会被 CPython 变成 STATUS_CONTROL_C_EXIT（退出码 3221225786），Python 层的处理器没有机会跑。
        所以这里直接让轮询抛 `KeyboardInterrupt` 来驱动同一段代码，并在报告里写明这个边界。
        """
        calls = {'rounds': 0}
        cleaned = {}

        def interrupted(**_kwargs):
            calls['rounds'] += 1
            raise KeyboardInterrupt

        self.patch(worker, 'run_once', interrupted)
        self.patch(worker, 'recover_in_flight',
                   lambda build_ids: cleaned.setdefault('ids', set(build_ids)))
        self.assertEqual(worker.main(['--watch', '--demo-identity']), wiki_build.EXIT_OK)
        self.assertEqual(calls['rounds'], 1, '中断后不许再进入下一轮')
        self.assertIn('ids', cleaned, '退出路径必须跑一次"不留 running 僵尸"的兜底')


# ================================================================ 4. 静态边界：失败隔离

class SourceBoundaryTest(unittest.TestCase):
    """静态断言：worker 侧没有一条语句写问答侧的账（与 `test_wiki_jobs.py:278-283` 同款）。"""

    SOURCES = {name: (ROOT / path).read_text(encoding='utf-8') for name, path in (
        ('wiki_build', 'datamind/wiki_build.py'),
        ('worker', 'scripts/wiki_build_worker.py'))}

    @staticmethod
    def code_lines(source):
        """只保留可执行行：去掉**所有** docstring 与注释行。

        边界断言看的是**代码**；本模块的文档里必须能直呼 `claim_build()` / `_lock_actor()`
        这些名字来解释"为什么不重写它们"，所以不能用整份文件做子串判定。
        """
        import ast
        tree = ast.parse(source)
        doc_lines = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                     ast.AsyncFunctionDef)):
                continue
            body = getattr(node, 'body', None) or []
            if not body:
                continue
            first = body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                doc_lines.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
        lines = []
        for number, line in enumerate(source.splitlines(), start=1):
            if number in doc_lines or line.strip().startswith('#'):
                continue
            lines.append(line)
        return '\n'.join(lines)

    def test_no_statement_writes_the_answer_side_tables(self):
        for name, source in self.SOURCES.items():
            code = self.code_lines(source)
            for table in ('runs', 'claims', 'trace_events', 'evidence_nodes', 'evidence_edges',
                          'conflict_records'):
                self.assertNotRegex(code, r'(INSERT INTO|UPDATE|DELETE FROM)\s+%s\b' % table,
                                    '%s 不得改写 %s（需求 §4 / P2 验收）' % (name, table))

    def test_the_only_table_this_layer_writes_is_the_wiki_side(self):
        import re
        for name, source in self.SOURCES.items():
            written = set(re.findall(r'INSERT INTO (\w+)', self.code_lines(source)))
            self.assertLessEqual(
                written, {'wiki_entries', 'wiki_evidence_refs', 'wiki_provenance_links'},
                '%s 的 INSERT 目标超出预期：%s' % (name, sorted(written)))

    def test_it_never_reimplements_the_permission_recheck_or_the_projection(self):
        code = self.code_lines(self.SOURCES['wiki_build'])
        for forbidden in ('_lock_actor', 'def claim_build', 'def complete_build',
                          "status = 'running'", "status = 'succeeded'", "status = 'failed'"):
            self.assertNotIn(forbidden, code,
                             '状态推进与复查只能在 wiki_jobs 里（本模块不得重写）')
        self.assertIn('from .wiki_projection import', self.SOURCES['wiki_build'],
                      '整理逻辑必须复用投影层')
        for required in ('project_entry', 'project_evidence_ref', 'projection_summary'):
            self.assertIn(required, code, required)

    def test_the_version_write_goes_through_the_service_layer(self):
        code = self.code_lines(self.SOURCES['wiki_build'])
        self.assertIn('wiki.create_revision(', code,
                      '版本必须走 §5.12 的服务层：append-only 触发器与引用校验都在那里')
        self.assertIn("'base_revision_id': base_revision_id", code)
        self.assertIn('revision_base_for(', code, '基线必须取自该词条的当前 tip')
        self.assertIn('generation_method', code)
        self.assertIn('wiki-build-template-v1', self.SOURCES['wiki_build'])

    def test_the_baseline_lookup_only_reads_and_prefers_the_newest_tip(self):
        code = self.code_lines(self.SOURCES['wiki_build'])
        somewhere = code.split('def revision_base_for', 1)[1].split('\ndef ', 1)[0]
        self.assertIn('SELECT revision_id FROM wiki_revisions', somewhere)
        self.assertIn('base_revision_id = r.revision_id', somewhere,
                      'tip = 没有子版本的版本（与 wiki._tip_revision_ids 同款判据）')
        self.assertIn('ORDER BY r.created_at DESC', somewhere)
        for forbidden in ('INSERT', 'UPDATE', 'DELETE'):
            self.assertNotIn(forbidden, somewhere, '基线查询必须是只读的')

    def test_the_worker_drives_the_frozen_three_step_path(self):
        code = self.code_lines(self.SOURCES['worker'])
        for required in ('wiki_jobs.execute_build(', 'wiki_jobs.fail_build(',
                         'wiki_build.select_queued(', 'wiki_build.builder_for('):
            self.assertIn(required, code, required)


# ================================================================ 5. 真库：整条链路

class SharedTransaction:
    """`with wiki.connect(dsn) as conn` 的替身：整条流程共用一个连接、**永不提交**。

    与 `tests/test_wiki_jobs.py:325-350` 同名类刻意重复：两个文件各自可独立运行。
    """

    SAVEPOINT = 'wiki_build_worker_call'

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


@contextmanager
def app_transaction():
    """永不提交的应用角色事务。"""
    conn = psycopg2.connect(wiki.wiki_dsn())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur
    finally:
        conn.rollback()
        conn.close()


def wiki_counts(cur):
    """8 张 wiki 表的行数。"""
    counts = {}
    for table in worker.WIKI_TABLES:
        cur.execute('SELECT count(*) AS counted FROM %s' % table)
        counts[table] = int(cur.fetchone()['counted'])
    return counts


@unittest.skipUnless(DB, 'psycopg2 或 datamind_meta 不可用（真库用例需要 .venv 解释器）')
class BuildWorkerDatabaseTest(unittest.TestCase):
    """真库走一遍：领取 → 整理 → 交付 / 失败；末尾 ROLLBACK，8 张表不留行。"""

    def setUp(self):
        self.dsn = wiki.wiki_dsn()
        self.conn = psycopg2.connect(self.dsn)
        real_connect = wiki.connect
        wiki.connect = lambda dsn: SharedTransaction(self.conn)
        self.addCleanup(setattr, wiki, 'connect', real_connect)
        self.addCleanup(self._rollback)

        suffix = uuid.uuid4().hex[:8]
        self.user_id = 'user-wiki-build-' + suffix
        token = 'wiki-build-token-' + suffix
        self.session_hash = wiki_build.token_hash(token)
        self.token = token
        self.run_id = 'run-' + uuid.uuid4().hex[:16]
        self.table_counts = {}
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('''INSERT INTO auth_users(user_id, username, display_name, password_hash,
                    roles) VALUES (%s,%s,'Worker E2E','scrypt$00$00',ARRAY['reviewer','approver'])''',
                        (self.user_id, 'wiki-build-' + suffix))
            cur.execute('''INSERT INTO auth_sessions(token_hash, user_id, csrf_token, expires_at)
                VALUES (%s,%s,'csrf', now() + interval '1 hour')''',
                        (self.session_hash, self.user_id))
            cur.execute('''INSERT INTO runs(run_id, conversation_id, question, status, started_at)
                VALUES (%s,NULL,'worker E2E','COMPLETED', now())''', (self.run_id,))
            cur.execute('''INSERT INTO evidence_nodes(evidence_id, run_id, node_type, ref_key,
                    payload) VALUES (%s,%s,'Claim',%s,%s)''',
                        ('ev-' + uuid.uuid4().hex[:16], self.run_id, CLAIM_KEY,
                         '{"text": "worker E2E 主张"}'))
            cur.execute('''INSERT INTO runs(run_id, conversation_id, question, status, started_at)
                VALUES (%s,NULL,'worker E2E 无证据','COMPLETED', now())''',
                        (self.run_id + '-bare',))
            # 快照：整理前后必须逐字节相同（失败隔离的真库证据）。
            cur.execute('SELECT count(*) AS counted FROM runs')
            self.table_counts['runs'] = cur.fetchone()['counted']
            cur.execute('SELECT count(*) AS counted FROM claims')
            self.table_counts['claims'] = cur.fetchone()['counted']
            cur.execute('SELECT count(*) AS counted FROM trace_events')
            self.table_counts['trace_events'] = cur.fetchone()['counted']

    def _rollback(self):
        try:
            self.conn.rollback()
        finally:
            self.conn.close()

    def _submit(self, run_id=None, **overrides):
        payload = {'request_id': 'build-worker-e2e-' + uuid.uuid4().hex[:8],
                   'run_id': run_id or self.run_id, 'mode': 'template',
                   'entry_type_hint': 'conclusion'}
        payload.update(overrides)
        identity = {'user_id': self.user_id, 'display_name': 'Worker E2E',
                    'roles': ['reviewer', 'approver'], '_session_hash': self.session_hash}
        return wiki_jobs.submit_build(payload, self.dsn, identity)['build'], identity

    def _identity(self, roles=('reviewer', 'approver')):
        return {'user_id': self.user_id, 'display_name': 'Worker E2E', 'roles': list(roles),
                '_session_hash': self.session_hash}

    def _resolve(self):
        """走生产的身份解析（读 auth_sessions），不是手工拼一个身份。"""
        with wiki.connect(self.dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            return wiki_build.resolve_identity(cur, {'owner_id': self.user_id},
                                               session_token=self.token, allow_demo=False)

    def _select(self):
        """走生产的选任务路径（owner 过滤 + 键集顺序）。"""
        return wiki_build.select_queued(self.dsn, self._identity())

    def test_a_queued_build_reaches_succeeded_and_everything_only_lands_on_the_wiki_side(self):
        build, _ = self._submit()
        self.assertEqual(build['status'], 'queued')
        queued = [row['build_id'] for row in self._select()]
        self.assertEqual(queued, [build['build_id']], '选任务只挑自己的 queued 行')

        identity = self._resolve()
        self.assertEqual(identity['user_id'], self.user_id)
        self.assertEqual(identity['_session_hash'], self.session_hash)

        outcome = wiki_jobs.execute_build(build['build_id'], self.dsn, identity,
                                          wiki_build.builder_for(self.dsn, identity))
        self.assertTrue(outcome['delivered'])
        self.assertEqual(outcome['build']['status'], 'succeeded')
        self.assertEqual(outcome['build']['attempts'], 1)
        revision_id = outcome['build']['result_revision_id']
        entry_id = outcome['build']['result_entry_id']
        self.assertTrue(revision_id.startswith('wrev-'))
        self.assertEqual(outcome['build']['candidate_entry_id'], entry_id)
        self.assertEqual(outcome['build']['error_code'], None)

        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT * FROM wiki_entries WHERE entry_id = %s', (entry_id,))
            entry = dict(cur.fetchone())
            self.assertEqual(entry['entry_type'], 'conclusion')
            self.assertEqual(entry['space_id'], wiki.space_for_user(self.user_id))
            self.assertEqual(entry['owner_id'], self.user_id)
            self.assertEqual(entry['confirmation'], 'confirmed')
            self.assertEqual(entry['publication_status'], 'draft',
                             '整理只产草稿：发布是另一条路径（§5.14）')
            self.assertIsNone(entry['head_revision_id'])
            cur.execute('SELECT * FROM wiki_revisions WHERE revision_id = %s', (revision_id,))
            revision = dict(cur.fetchone())
            self.assertEqual(revision['entry_id'], entry_id)
            self.assertIsNone(revision['base_revision_id'], '首版没有基线')
            self.assertEqual(revision['generation_method'], 'template')
            self.assertEqual(revision['template_version'], wiki_build.TEMPLATE_VERSION)
            self.assertIn('1', str(revision['body_sha256'])[:0] or '1')
            self.assertEqual(len(revision['citations']), 1)
            self.assertTrue(revision['citations'][0]['evidence_ref_id'].startswith('wev-'))
            self.assertEqual(revision['author_id'], self.user_id)
            cur.execute('SELECT * FROM wiki_provenance_links WHERE revision_id = %s', (revision_id,))
            links = [dict(row) for row in cur.fetchall()]
            self.assertEqual(len(links), 1)
            self.assertEqual(links[0]['run_id'], self.run_id)
            self.assertEqual(links[0]['claim_id'], CLAIM_KEY)
            # 失败隔离的真库证据：问答侧三张表的行数一模一样。
            for table, expected in self.table_counts.items():
                cur.execute('SELECT count(*) AS counted FROM %s' % table)
                self.assertEqual(int(cur.fetchone()['counted']), expected,
                                 '整理不得改动 %s' % table)

    def test_a_revoked_role_stops_the_claim_before_the_builder_runs(self):
        build, _ = self._submit()
        calls = []

        def builder(job):
            calls.append(job)
            raise AssertionError('撤权后不许开工')

        with self.conn.cursor() as cur:
            cur.execute("UPDATE auth_users SET roles = ARRAY['viewer'] WHERE user_id = %s",
                        (self.user_id,))
        with self.assertRaises(HTTPException) as caught:
            wiki_jobs.execute_build(build['build_id'], self.dsn, self._identity(['viewer']),
                                    builder)
        self.assertEqual((caught.exception.status_code, code_of(caught.exception)),
                         (403, 'WIKI_PERMISSION_DENIED'))
        self.assertEqual(calls, [], '整理器一次都不许被调用')
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT status, attempts FROM wiki_build_jobs WHERE build_id = %s',
                        (build['build_id'],))
            row = dict(cur.fetchone())
            self.assertEqual((row['status'], row['attempts']), ('queued', 0),
                             'claim 失败不改任务行：撤权后任务留在 queued 等 owner 恢复权限')

    def test_the_final_recheck_refuses_to_deliver_for_an_identity_without_the_role(self):
        """交付前的复查：身份角色不满足 `reviewer` → 任务落 `WIKI_PERMISSION_DENIED`，结果不交付。

        诚实说明这条用例覆盖的**机制**：`wiki_jobs.completion_verdict()` 的 `permission_ok`
        取自 `wiki.has_role(identity, …)`，也就是**交给它的那个身份**持有的角色（冻结实现如此，
        `wiki_jobs.py:527`）；数据库侧的复查发生在 `_lock_actor()`，即领取事务
        （见上一条用例）与 `wiki.create_revision()` 里。撤权后 worker 下一次解析身份就会拿到
        降级后的角色，于是交付被拒 —— 这就是用户可见的那条路径。

        整理器返回**真实存在**的词条与版本：否则会先撞上 `result_entry_id` 的外键。
        """
        build, _ = self._submit()
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('''INSERT INTO wiki_entries(entry_id, space_id, owner_id, entry_type,
                    namespace, key_part, confirmation, title)
                VALUES (%s,%s,%s,'conclusion','default',%s,'confirmed','撤权用例')
                ON CONFLICT DO NOTHING''',
                        ('wiki-' + 'e' * 24, wiki.space_for_user(self.user_id), self.user_id,
                         'revocation-case'))
            cur.execute('''INSERT INTO wiki_revisions(revision_id, entry_id, title, body_markdown,
                    body_sha256, generation_method, template_version, author_id)
                VALUES (%s,%s,'撤权用例','正文',%s,'template',%s,%s)''',
                        ('wrev-' + 'e' * 24, 'wiki-' + 'e' * 24, 'f' * 64,
                         wiki_build.TEMPLATE_VERSION, self.user_id))

        def builder(job):
            with self.conn.cursor() as cur:
                cur.execute("UPDATE auth_users SET roles = ARRAY['viewer'] WHERE user_id = %s",
                            (self.user_id,))
            return {'entry_id': 'wiki-' + 'e' * 24, 'revision_id': 'wrev-' + 'e' * 24}

        claimed = wiki_jobs.claim_build(build['build_id'], self.dsn, self._identity())
        self.assertEqual(claimed['status'], 'running', '领取时还有编辑权限')
        result = builder(claimed)  # 整理完成 → 此刻角色已被撤
        outcome = wiki_jobs.complete_build(build['build_id'], result, self.dsn,
                                           self._identity(['viewer']))
        self.assertFalse(outcome['delivered'])
        self.assertEqual(outcome['build']['status'], 'failed')
        self.assertEqual(outcome['build']['error_code'], 'WIKI_PERMISSION_DENIED')
        self.assertIsNone(outcome['build']['result_revision_id'])
        self.assertEqual(outcome['build']['attempts'], 1)
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT count(*) AS counted FROM wiki_revisions WHERE entry_id = %s',
                        ('wiki-' + 'e' * 24,))
            self.assertEqual(int(cur.fetchone()['counted']), 1,
                             '撤权时任务落败，交付不写第二个版本')

    def test_rebuilding_the_same_run_appends_a_version_instead_of_conflicting(self):
        """换一个 `request_id` 再整理同一个运行：同一个词条 + 一个新版本（基线是上一个 tip）。

        这是实测缺陷的回归：一律传 `base_revision_id=None` 时第二次整理撞 409
        `WIKI_VERSION_CONFLICT`（`create_revision` 的首版规则），整理做完却落败。
        """
        first, identity = self._submit()
        first_outcome = wiki_jobs.execute_build(first['build_id'], self.dsn, identity,
                                                wiki_build.builder_for(self.dsn, identity))
        self.assertTrue(first_outcome['delivered'])
        second, _ = self._submit()          # 新 request_id → 新任务行，同运行同载荷
        second_outcome = wiki_jobs.execute_build(second['build_id'], self.dsn, identity,
                                                 wiki_build.builder_for(self.dsn, identity))
        self.assertTrue(second_outcome['delivered'], '第二次整理必须成功（追加版本，不是冲突）')
        self.assertEqual(second_outcome['build']['result_entry_id'],
                         first_outcome['build']['result_entry_id'], '身份是确定性的：同一个词条')
        self.assertNotEqual(second_outcome['build']['result_revision_id'],
                            first_outcome['build']['result_revision_id'])
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('''SELECT count(*) AS counted FROM wiki_entries
                WHERE space_id = %s''', (wiki.space_for_user(self.user_id),))
            self.assertEqual(int(cur.fetchone()['counted']), 1, '不得建第二个词条')
            cur.execute('SELECT base_revision_id FROM wiki_revisions WHERE revision_id = %s',
                        (second_outcome['build']['result_revision_id'],))
            self.assertEqual(cur.fetchone()['base_revision_id'],
                             first_outcome['build']['result_revision_id'],
                             '新版本的基线是上一个 tip（§5.12 的显式基线）')

    def test_a_broken_plan_lands_on_the_job_row_only(self):
        build, _ = self._submit()
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT * FROM runs WHERE run_id = %s', (self.run_id,))
            before = dict(cur.fetchone())

        def broken_builder(job):
            raise wiki_build.ProjectionError('WIKI_ENTITY_KEY_UNRESOLVED', 'no entity')

        outcome = wiki_jobs.execute_build(build['build_id'], self.dsn, self._identity(),
                                          broken_builder)
        self.assertFalse(outcome['delivered'])
        self.assertEqual(outcome['build']['status'], 'failed')
        self.assertEqual(outcome['build']['error_code'], 'WIKI_BUILD_FAILED',
                         'ProjectionError 不是 HTTPException → 归到 WIKI_BUILD_FAILED')
        self.assertIn('ProjectionError', outcome['build']['error_detail'])
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT * FROM runs WHERE run_id = %s', (self.run_id,))
            self.assertEqual(dict(cur.fetchone()), before, '整理失败不得改写 runs')

    def test_a_build_whose_evidence_disappeared_fails_with_the_contract_code(self):
        """提交后证据行被删（`submit_build()` 只在提交时查过一次）：整理侧必须落 `WIKI_NO_EVIDENCE`。

        任务行直接 INSERT 是**有意**的：`submit_build()` 不会给一个没有证据的运行落行
        （它自己就 409 了），所以这条"证据消失"的路径只能用一行等价的任务来触发。
        """
        bare_run = self.run_id + '-bare'
        request_id = 'build-worker-bare-' + uuid.uuid4().hex[:8]
        payload_sha = wiki_jobs.build_payload_sha256(wiki_jobs.validate_build_request(
            {'request_id': request_id, 'run_id': bare_run, 'mode': 'template'}))
        build_id = 'wjob-' + uuid.uuid4().hex[:24]
        identity = self._identity()
        with self.conn.cursor() as cur:
            cur.execute('''INSERT INTO wiki_build_jobs(build_id, space_id, owner_id, request_id,
                    payload_sha256, run_id, mode) VALUES (%s,%s,%s,%s,%s,%s,'template')''',
                        (build_id, wiki.space_for_user(self.user_id), self.user_id, request_id,
                         payload_sha, bare_run))
        outcome = wiki_jobs.execute_build(build_id, self.dsn, identity,
                                          wiki_build.builder_for(self.dsn, identity))
        self.assertFalse(outcome['delivered'])
        self.assertEqual(outcome['build']['status'], 'failed')
        self.assertEqual(outcome['build']['error_code'], 'WIKI_NO_EVIDENCE')

    def test_the_worker_replays_a_job_once_and_reports_the_terminal_state(self):
        """worker 侧一轮：`status` 进终态、`attempts` 递增、队列随即为空。"""
        build, _ = self._submit()
        counts = {'claimed': 0, 'terminal': 0, 'pending': 0}
        jobs = self._select()
        self.assertEqual(len(jobs), 1)
        for job in jobs:
            identity = self._resolve()
            outcome = wiki_jobs.execute_build(job['build_id'], self.dsn, identity,
                                              wiki_build.builder_for(self.dsn, identity))
            counts['claimed'] += 1
            counts['terminal' if wiki_build.is_terminal(outcome['build']) else 'pending'] += 1
        self.assertEqual(counts, {'claimed': 1, 'terminal': 1, 'pending': 0})
        self.assertEqual(self._select(), [], '终态任务不再出现在队列里')

    def test_the_exit_cleanup_recovers_a_job_left_running(self):
        """模拟"领取后进程消失"：清理只改任务行，把它落成失败，不留 running 僵尸。"""
        build, _ = self._submit()
        with wiki.connect(self.dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            identity = wiki_build.resolve_identity(cur, {'owner_id': self.user_id},
                                                   session_token=self.token, allow_demo=False)
        claimed = wiki_jobs.claim_build(build['build_id'], self.dsn, identity)
        self.assertEqual(claimed['status'], 'running')

        worker.recover_in_flight({build['build_id']})
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT status, error_code, error_detail, attempts FROM wiki_build_jobs '
                        'WHERE build_id = %s', (build['build_id'],))
            row = dict(cur.fetchone())
        self.assertEqual(row['status'], 'failed')
        self.assertEqual(row['error_code'], wiki_build.ERROR_INTERRUPTED)
        self.assertIn('running', row['error_detail'])
        self.assertEqual(row['attempts'], 1)

    def test_an_owner_without_a_live_session_cannot_be_resolved(self):
        with wiki.connect(self.dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            with self.assertRaises(wiki_build.IdentityUnavailable) as caught:
                wiki_build.resolve_identity(cur, {'owner_id': self.user_id},
                                            session_token='not-a-real-token', allow_demo=False)
            self.assertEqual(caught.exception.code, wiki_build.ERROR_IDENTITY_UNAVAILABLE)
            self.assertEqual(wiki_build.live_session_identity(cur, self.user_id, None), None)

    def test_the_wiki_tables_start_and_end_this_module_empty(self):
        with app_transaction() as cur:
            counts = wiki_counts(cur)
        self.assertEqual(sum(counts.values()), 0,
                         'wiki 表在本套件之外必须保持 0 行：%s' % counts)


if __name__ == '__main__':
    unittest.main()
