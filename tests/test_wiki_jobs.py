"""Wiki 整理任务（datamind/wiki_jobs.py）的契约测试：幂等、重试、权限复查、失败隔离。

分层与 `tests/test_wiki_service.py` 相同（系统 python 有 pytest 没有 psycopg2，`.venv` 反之）：
纯逻辑用例必须无依赖可跑，真库用例 `@unittest.skipUnless(DB, …)` 且**全部 ROLLBACK**。

本文件重点钉住四件事（WIKI_API.md §5.9–§5.11 与需求"整理失败不影响原问答"）：

1. **幂等三分支**：同 owner + 同规范载荷摘要 → 复用；同 request_id 不同载荷 → 409；
   同 request_id 不同 owner → 404（存在性隐藏）；
2. **重试**：只有 `failed` 可重试、保留原运行/原载荷/原空间、`attempts` 递增；
3. **两次权限复查**：`claim_build()`（开工前）与 `complete_build()`（返回结果前）；
4. **失败隔离**：整份模块里没有任何一条语句写 `runs` / `claims` / `trace_events`
   /`evidence_*`，失败只落进任务行。
"""
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import HTTPException  # noqa: E402

from datamind import wiki  # noqa: E402
from datamind import wiki_jobs  # noqa: E402

RUN = 'run-894b0785725c45f3'
ENTRY = 'wiki-' + 'a' * 24
REVISION = 'wrev-' + 'b' * 24

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:  # pragma: no cover
    psycopg2 = None
    RealDictCursor = None


def db_available():
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


@contextmanager
def app_transaction():
    """永不提交的应用角色事务（真库用例的唯一入口）。"""
    conn = psycopg2.connect(wiki.wiki_dsn())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur
    finally:
        conn.rollback()
        conn.close()


def request(**overrides):
    payload = {'request_id': 'build-20261014-1', 'run_id': RUN, 'mode': 'template',
               'entry_type_hint': 'conclusion'}
    payload.update(overrides)
    return payload


class WikiBuildPayloadTest(unittest.TestCase):
    """§5.9 的载荷规范与摘要（幂等的判据）。"""

    def test_the_digest_covers_exactly_the_five_frozen_fields(self):
        first = wiki_jobs.validate_build_request(
            request(selection={'claim_ids': ['claim-b', 'claim-a', 'claim-a']}))
        second = wiki_jobs.validate_build_request(
            request(selection={'claim_ids': ['claim-a', 'claim-b']}))
        self.assertEqual(first['payload_sha256'], second['payload_sha256'],
                         '集合语义相同、顺序不同 → 必须同一个摘要，否则幂等会被顺序拆开')
        self.assertRegex(first['payload_sha256'], r'^[a-f0-9]{64}$')
        self.assertEqual(first['selection'], {'claim_ids': ['claim-a', 'claim-b']})
        # request_id / space_id 不是请求内容 → 不进摘要（021:556-557 的键清单）。
        selection = {'claim_ids': ['claim-a', 'claim-b']}
        other_id = wiki_jobs.validate_build_request(
            request(request_id='build-20261014-2', selection=selection))
        self.assertEqual(first['payload_sha256'], other_id['payload_sha256'])
        other_space = wiki_jobs.validate_build_request(
            request(selection=selection, space_id='wsp-' + 'c' * 24))
        self.assertEqual(first['payload_sha256'], other_space['payload_sha256'])
        for change in ({'run_id': 'run-0000000000000000'},
                       {'mode': 'template+polish'},
                       {'entry_type_hint': 'metric'},
                       {'entry_type_hint': None},
                       {'target_entry_id': ENTRY},
                       {'selection': {'claim_ids': ['claim-c']}},
                       {'selection': {'evidence_ids': ['wev-' + '1' * 24]}}):
            changed = wiki_jobs.validate_build_request(request(**change))
            self.assertNotEqual(first['payload_sha256'], changed['payload_sha256'],
                                '改了 %s 就必须换摘要' % change)

    def test_malformed_payloads_are_refused_with_a_stable_code(self):
        for payload in ({},
                        request(run_id=''),
                        request(run_id='   '),
                        request(request_id=None),
                        request(request_id='x' * 65),
                        request(mode='polish'),
                        request(entry_type_hint='entity'),
                        request(target_entry_id='wiki-1'),
                        request(selection={'claims': []}),
                        request(selection={'claim_ids': 'claim-1'}),
                        ['not', 'an', 'object']):
            with self.assertRaises(HTTPException) as caught:
                wiki_jobs.validate_build_request(payload)
            self.assertEqual(caught.exception.status_code, 422, payload)
            self.assertEqual(code_of(caught.exception), 'WIKI_BUILD_REQUEST_INVALID', payload)

    def test_a_missing_mode_defaults_to_template(self):
        parsed = wiki_jobs.validate_build_request({'request_id': 'r', 'run_id': RUN})
        self.assertEqual(parsed['mode'], 'template')
        self.assertEqual(parsed['selection'], {})
        self.assertIsNone(parsed['entry_type_hint'])


class WikiBuildIdempotencyTest(unittest.TestCase):
    """§5.9 的幂等表：四条分支各一条断言。"""

    def test_the_four_branches(self):
        common = {'existing_payload_sha256': 'a' * 64, 'incoming_payload_sha256': 'a' * 64,
                  'caller_user_id': 'user-a'}
        self.assertEqual(wiki_jobs.idempotency_decision(existing_owner=None, **common), 'create')
        self.assertEqual(wiki_jobs.idempotency_decision(existing_owner='user-a', **common),
                         'reuse')
        self.assertEqual(wiki_jobs.idempotency_decision(
            existing_owner='user-a',
            existing_payload_sha256='a' * 64, incoming_payload_sha256='b' * 64,
            caller_user_id='user-a'), 'conflict')
        self.assertEqual(wiki_jobs.idempotency_decision(
            existing_owner='user-b', **common), 'hidden', '不同 owner → 与不存在不可区分')
        self.assertEqual(wiki_jobs.idempotency_decision(
            existing_owner='user-b', admin=True, **common), 'reuse',
            'admin 是超管旁路，可以复用别人的任务行')

    def test_the_hidden_branch_is_the_same_404_as_a_missing_task(self):
        """存在性隐藏：别人的 request_id 与不存在的返回同一条 404，不泄露"这个 id 存在"。"""
        hidden = wiki_jobs.idempotency_decision(
            existing_owner='other', existing_payload_sha256='a' * 64,
            incoming_payload_sha256='a' * 64, caller_user_id='me')
        self.assertEqual(hidden, 'hidden')
        source = (ROOT / 'datamind' / 'wiki_jobs.py').read_text(encoding='utf-8')
        self.assertIn("if decision == 'hidden':", source)
        self.assertIn("wiki.fail('WIKI_BUILD_NOT_FOUND', 404)", source,
                      'hidden 分支必须给出与不存在相同的 404')


class WikiRunOwnershipTest(unittest.TestCase):
    """`runs` 没有外键（021:31-35），归属只能由服务层判。"""

    def test_a_missing_run_is_404(self):
        self.assertEqual(wiki_jobs.run_ownership_verdict(run_row=None, owner_user_id=None,
                                                         identity={'user_id': 'u'}),
                         ('WIKI_RUN_NOT_FOUND', 404))

    def test_a_run_of_someone_elses_session_is_403(self):
        self.assertEqual(wiki_jobs.run_ownership_verdict(
            run_row={'conversation_id': 'sess-1'}, owner_user_id='user-b',
            identity={'user_id': 'user-a', 'roles': ['reviewer']}),
            ('WIKI_RUN_FORBIDDEN', 403))

    def test_my_own_run_and_an_unowned_industrial_run_are_reachable(self):
        mine = {'user_id': 'user-a', 'roles': ['reviewer']}
        self.assertIsNone(wiki_jobs.run_ownership_verdict(
            run_row={'conversation_id': 'sess-1'}, owner_user_id='user-a', identity=mine))
        self.assertIsNone(wiki_jobs.run_ownership_verdict(
            run_row={'conversation_id': None}, owner_user_id=None, identity=mine),
            '工业运行没有会话归属（WIKI_API.md §11.6）→ 只落触发者自己的空间')
        self.assertIsNone(wiki_jobs.run_ownership_verdict(
            run_row={'conversation_id': 'sess-1'}, owner_user_id='user-b',
            identity={'user_id': 'user-a', 'roles': ['admin']}))

    def test_a_session_row_that_cannot_be_found_fails_closed(self):
        self.assertEqual(wiki_jobs.run_ownership_verdict(
            run_row={'conversation_id': 'sess-gone'}, owner_user_id=None,
            identity={'user_id': 'user-a', 'roles': ['reviewer']}),
            ('WIKI_RUN_FORBIDDEN', 403))


class WikiBuildLifecycleTest(unittest.TestCase):
    """重试、两次权限复查、失败记录。"""

    def test_only_a_failed_build_can_be_retried(self):
        self.assertIsNone(wiki_jobs.retry_verdict(status='failed'))
        for status in ('queued', 'running', 'succeeded'):
            self.assertEqual(wiki_jobs.retry_verdict(status=status),
                             ('WIKI_BUILD_NOT_RETRYABLE', 409), status)

    def test_the_final_recheck_refuses_to_deliver_after_a_revocation(self):
        self.assertIsNone(wiki_jobs.completion_verdict(permission_ok=True, owner_ok=True))
        self.assertEqual(wiki_jobs.completion_verdict(permission_ok=False, owner_ok=True),
                         ('WIKI_PERMISSION_DENIED', 403))
        self.assertEqual(wiki_jobs.completion_verdict(permission_ok=True, owner_ok=False),
                         ('WIKI_BUILD_NOT_FOUND', 404))

    def test_a_build_failure_becomes_a_job_row_and_nothing_else(self):
        code, detail = wiki_jobs.failure_record(RuntimeError('boom'))
        self.assertEqual(code, 'WIKI_BUILD_FAILED')
        self.assertIn('RuntimeError', detail)
        wiki_error = HTTPException(409, detail={'error_code': 'WIKI_VERSION_CONFLICT',
                                                'message': 'stale baseline'})
        code, detail = wiki_jobs.failure_record(wiki_error)
        self.assertEqual(code, 'WIKI_VERSION_CONFLICT',
                         '整理时发现基线过期 → 任务详情里要能看到真正的原因')
        self.assertIn('stale baseline', detail)
        self.assertLessEqual(len(wiki_jobs.failure_record(RuntimeError('x' * 5000))[1]),
                             wiki_jobs.ERROR_DETAIL_MAX)
        self.assertTrue(wiki_jobs.failure_record(RuntimeError(''))[1])

    def test_error_codes_obey_the_database_naming_check(self):
        self.assertEqual(wiki_jobs.sanitize_error_code('WIKI_BUILD_FAILED'), 'WIKI_BUILD_FAILED')
        self.assertEqual(wiki_jobs.sanitize_error_code('WIKI_PERMISSION_DENIED'),
                         'WIKI_PERMISSION_DENIED')
        for bad in ('WIKI-version', "WIKI_X'; DROP TABLE wiki_entries; --", None, '', 42,
                    'PERMISSION_DENIED'):
            self.assertEqual(wiki_jobs.sanitize_error_code(bad), 'WIKI_BUILD_FAILED', bad)

    def test_merge_suggestions_never_change_an_identity_by_themselves(self):
        suggestion = wiki_jobs.merge_suggestion(ENTRY, 'title_or_alias_overlap', '设备 A102')
        self.assertEqual(suggestion['entry_id'], ENTRY)
        self.assertTrue(suggestion['requires_human_confirmation'],
                        '§5.10：名称相近只能产生建议')

    def test_the_build_object_carries_the_contract_fields(self):
        row = {'build_id': 'wjob-' + '1' * 24, 'status': 'queued', 'request_id': 'r',
               'payload_sha256': 'c' * 64, 'run_id': RUN, 'space_id': 'wsp-' + 'd' * 24,
               'mode': 'template', 'entry_type_hint': None, 'target_entry_id': None,
               'owner_id': 'user-a', 'attempts': 0, 'result_entry_id': None,
               'result_revision_id': None, 'candidate_entry_id': None, 'suggested_merges': [],
               'error_code': None, 'error_detail': None, 'retry_of_build_id': None,
               'created_at': 'then', 'updated_at': 'now'}
        build = wiki_jobs.build_object(row)
        for key in ('build_id', 'status', 'request_id', 'payload_sha256', 'run_id', 'mode',
                    'entry_type_hint', 'target_entry_id', 'owner', 'attempts',
                    'result_entry_id', 'result_revision_id', 'candidate_entry_id',
                    'suggested_merges', 'error_code', 'error_detail', 'created_at', 'updated_at'):
            self.assertIn(key, build)
        self.assertEqual(build['owner'], {'user_id': 'user-a'})

    def test_the_execute_path_never_swallows_a_failure_into_success(self):
        source = (ROOT / 'datamind' / 'wiki_jobs.py').read_text(encoding='utf-8')
        body = source.split('def execute_build', 1)[1]
        self.assertIn('if builder is None:', body)
        self.assertIn('def _failed(', source)
        self.assertIn('delivered', body, '结果必须带"是否交付"这个显式结论')
        self.assertIn('except HTTPException as exc:', body)
        self.assertIn('except Exception as exc:', body)
        # 触发异常的那三个 except 与"没有整理器"都必须走 _failed（落账），不能 return 成功。
        self.assertEqual(body.count('return _failed('), 4)
        self.assertNotIn("status = 'succeeded'", body)


class WikiJobsSourceBoundaryTest(unittest.TestCase):
    """静态断言：失败隔离、载荷不可改写、两次权限复查。"""

    SOURCE = (ROOT / 'datamind' / 'wiki_jobs.py').read_text(encoding='utf-8')

    def test_no_statement_here_writes_the_answer_side_tables(self):
        for table in ('runs', 'claims', 'trace_events', 'evidence_nodes', 'evidence_edges',
                      'conflict_records'):
            self.assertNotRegex(self.SOURCE,
                                r'(INSERT INTO|UPDATE|DELETE FROM)\s+%s\b' % table,
                                '整理失败不得改写 %s（需求 §4 / P2 验收）' % table)

    def test_a_stored_request_payload_is_never_rewritten(self):
        """WIKI3：`(owner_id, request_id)` 对应的载荷列一个都不能 SET。"""
        for column in ('owner_id', 'space_id', 'request_id', 'payload_sha256', 'run_id', 'mode',
                       'selection', 'entry_type_hint', 'target_entry_id', 'created_at'):
            self.assertNotIn('SET %s' % column, self.SOURCE,
                             '%s 属于不可改写的请求身份（021:673-693）' % column)
            self.assertNotIn('%s = %%s,' % column, self.SOURCE)
        self.assertIn('attempts = attempts + 1', self.SOURCE)
        self.assertIn("status = 'running'", self.SOURCE)
        self.assertIn("status = 'succeeded'", self.SOURCE)
        self.assertIn("status = 'failed'", self.SOURCE)

    def test_permission_is_rechecked_before_starting_and_before_returning(self):
        claim = self.SOURCE.split('def claim_build', 1)[1].split('\ndef ', 1)[0]
        complete = self.SOURCE.split('def complete_build', 1)[1].split('\ndef ', 1)[0]
        self.assertIn("_lock_actor(cur, identity, permission)", claim)
        self.assertIn('completion_verdict(', complete)
        self.assertIn('delivered', complete)
        submit = self.SOURCE.split('def submit_build', 1)[1].split('\ndef ', 1)[0]
        retry = self.SOURCE.split('def retry_build', 1)[1].split('\ndef ', 1)[0]
        self.assertIn("_lock_actor(cur, identity, 'reviewer')", submit)
        self.assertIn("_lock_actor(cur, identity, 'reviewer')", retry)

    def test_every_frozen_build_error_code_is_used(self):
        for error_code in ('WIKI_PERMISSION_DENIED', 'WIKI_RUN_NOT_FOUND', 'WIKI_RUN_FORBIDDEN',
                           'WIKI_BUILD_NOT_FOUND', 'WIKI_IDEMPOTENCY_CONFLICT',
                           'WIKI_BUILD_NOT_RETRYABLE', 'WIKI_BUILD_REQUEST_INVALID',
                           'WIKI_NO_EVIDENCE', 'WIKI_BUILD_FAILED'):
            self.assertIn(error_code, self.SOURCE, error_code)

    def test_the_module_does_not_import_the_projection_layer(self):
        """投影层（datamind/wiki_projection.py）由另一个工作包维护，这里只注入回调。"""
        import_lines = [line for line in self.SOURCE.splitlines()
                        if line.startswith('import ') or line.startswith('from ')]
        for line in import_lines:
            self.assertNotIn('wiki_projection', line, line)
        self.assertIn('def execute_build(build_id_value, dsn, identity, builder=None',
                      self.SOURCE)


class SharedTransaction:
    """`with wiki.connect(dsn) as conn` 的替身：整条流程共用一个连接、**永不提交**。

    与 `tests/test_wiki_service.py` 里同名类**刻意重复**：两个测试文件各自可独立运行，
    不互相 import。`__enter__` 打 SAVEPOINT、`__exit__` 成功 RELEASE / 失败 ROLLBACK TO，
    这样"某一步 409"不会撤销前面已经成功的步骤；真正的回滚在用例结束时的 `conn.rollback()`。
    """

    SAVEPOINT = 'wiki_jobs_call'

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
class WikiJobsEndToEndRollbackTest(unittest.TestCase):
    """真实 SQL 走一遍：提交（幂等）→ 领取 → 交付 / 失败 → 重试。末尾 ROLLBACK，库里不留行。

    夹具在本事务里建真实 `auth_users` / `auth_sessions` / `chat_sessions` / `runs`
    / `evidence_nodes` 行（应用角色对这些表都有 INSERT，实测），所以归属校验、证据判据、
    幂等唯一键都是**真表**上的行为。
    """

    def setUp(self):
        import hashlib
        import uuid
        self.dsn = wiki.wiki_dsn()
        self.conn = psycopg2.connect(self.dsn)
        real_connect = wiki.connect
        wiki.connect = lambda dsn: SharedTransaction(self.conn)
        self.addCleanup(setattr, wiki, 'connect', real_connect)
        self.addCleanup(self._rollback)

        suffix = uuid.uuid4().hex[:8]
        self.user_id = 'user-wiki-jobs-' + suffix
        self.other_user_id = 'user-wiki-jobs-other-' + suffix
        token = 'wiki-jobs-' + suffix
        other_token = 'wiki-jobs-other-' + suffix
        self.session_hash = hashlib.sha256(token.encode()).hexdigest()
        other_hash = hashlib.sha256(other_token.encode()).hexdigest()
        self.session_id = 'sess-' + uuid.uuid4().hex[:24]
        self.run_id = 'run-' + uuid.uuid4().hex[:16]
        self.orphan_run_id = 'run-' + uuid.uuid4().hex[:16]
        with self.conn.cursor() as cur:
            cur.execute('''INSERT INTO auth_users(user_id, username, display_name, password_hash,
                    roles) VALUES (%s,%s,'E2E','scrypt$00$00',ARRAY['reviewer','approver'])''',
                        (self.user_id, 'wiki-jobs-' + suffix))
            cur.execute('''INSERT INTO auth_sessions(token_hash, user_id, csrf_token, expires_at)
                VALUES (%s,%s,'csrf', now() + interval '1 hour')''',
                        (self.session_hash, self.user_id))
            # 另一个**真实**账号（有 auth_users/auth_sessions 行）：越权用例必须走完
            # lock_identity 的身份复查，否则拿到的是 401 SESSION_INVALID 而不是越权结论。
            cur.execute('''INSERT INTO auth_users(user_id, username, display_name, password_hash,
                    roles) VALUES (%s,%s,'Other','scrypt$00$00',ARRAY['reviewer'])''',
                        (self.other_user_id, 'wiki-jobs-other-' + suffix))
            cur.execute('''INSERT INTO auth_sessions(token_hash, user_id, csrf_token, expires_at)
                VALUES (%s,%s,'csrf', now() + interval '1 hour')''',
                        (other_hash, self.other_user_id))
            cur.execute('''INSERT INTO chat_sessions(session_id, user_id, title)
                VALUES (%s,%s,'E2E 会话')''', (self.session_id, self.user_id))
            cur.execute('''INSERT INTO runs(run_id, conversation_id, question, status, started_at)
                VALUES (%s,%s,'E2E 问题','COMPLETED', now())''',
                        (self.run_id, self.session_id))
            # 无会话归属的运行（工业运行的形态）：只有触发者自己的空间能看到它的产物。
            cur.execute('''INSERT INTO runs(run_id, conversation_id, question, status, started_at)
                VALUES (%s,NULL,'E2E 工业问题','COMPLETED', now())''', (self.orphan_run_id,))
            cur.execute('''INSERT INTO evidence_nodes(evidence_id, run_id, node_type, ref_key,
                    payload) VALUES (%s,%s,'Claim','claim-e2e-1','{}'::jsonb)''',
                        ('ev-' + uuid.uuid4().hex[:16], self.run_id))
        self.reviewer = {'user_id': self.user_id, 'display_name': 'E2E', 'roles': ['reviewer'],
                         '_session_hash': self.session_hash}
        self.viewer = {**self.reviewer, 'roles': ['viewer']}
        self.stranger = {'user_id': self.other_user_id, 'display_name': 'Other',
                         'roles': ['reviewer'], '_session_hash': other_hash}

    def _rollback(self):
        try:
            self.conn.rollback()
        finally:
            self.conn.close()

    def _submit(self, **overrides):
        payload = {'request_id': 'build-e2e-1', 'run_id': self.run_id, 'mode': 'template',
                   'entry_type_hint': 'conclusion'}
        payload.update(overrides)
        return wiki_jobs.submit_build(payload, self.dsn, self.reviewer)

    def _run_row(self):
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT * FROM runs WHERE run_id=%s', (self.run_id,))
            return dict(cur.fetchone())

    # ---------------------------------------------------------------- 提交与幂等

    def test_submitting_twice_reuses_the_first_result_and_a_changed_payload_conflicts(self):
        first = self._submit()
        self.assertFalse(first['reused'])
        self.assertEqual(first['build']['status'], 'queued')
        self.assertEqual(first['build']['owner'], {'user_id': self.user_id})
        self.assertTrue(first['build']['build_id'].startswith('wjob-'))
        self.assertIsInstance(first['build']['suggested_merges'], list)
        again = self._submit()
        self.assertTrue(again['reused'], '同 owner + 同规范载荷 → 复用首次结果')
        self.assertEqual(again['build']['build_id'], first['build']['build_id'])
        with self.assertRaises(HTTPException) as caught:
            self._submit(mode='template+polish')
        self.assertEqual((caught.exception.status_code, code_of(caught.exception)),
                         (409, 'WIKI_IDEMPOTENCY_CONFLICT'))
        with self.assertRaises(HTTPException) as caught:
            self._submit(run_id=self.orphan_run_id)
        self.assertEqual(code_of(caught.exception), 'WIKI_IDEMPOTENCY_CONFLICT',
                         '同一个 request_id 不能被改指到另一条运行')
        with self.conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM wiki_build_jobs WHERE owner_id=%s', (self.user_id,))
            self.assertEqual(cur.fetchone()[0], 1, '冲突的两次都不许落行')

    def test_another_owners_request_id_is_indistinguishable_from_a_missing_task(self):
        self._submit()
        with self.assertRaises(HTTPException) as caught:
            wiki_jobs.submit_build({'request_id': 'build-e2e-1', 'run_id': self.run_id},
                                   self.dsn, self.stranger)
        self.assertEqual((caught.exception.status_code, code_of(caught.exception)),
                         (404, 'WIKI_BUILD_NOT_FOUND'), '存在性隐藏（§5.9）')

    def test_a_run_of_someone_elses_session_is_forbidden_and_an_orphan_run_is_reachable(self):
        with self.assertRaises(HTTPException) as caught:
            wiki_jobs.submit_build({'request_id': 'b-x', 'run_id': self.run_id},
                                   self.dsn, self.stranger)
        self.assertEqual((caught.exception.status_code, code_of(caught.exception)),
                         (403, 'WIKI_RUN_FORBIDDEN'), '会话归属不是请求者')
        with self.assertRaises(HTTPException) as caught:
            wiki_jobs.submit_build({'request_id': 'b-y', 'run_id': self.orphan_run_id},
                                   self.dsn, self.reviewer)
        self.assertEqual(code_of(caught.exception), 'WIKI_NO_EVIDENCE',
                         '无会话归属的运行可达，但它没有可投影的证据 → 409（不是 403）')
        with self.assertRaises(HTTPException) as caught:
            wiki_jobs.submit_build({'request_id': 'b-z', 'run_id': 'run-0000000000000000'},
                                   self.dsn, self.reviewer)
        self.assertEqual((caught.exception.status_code, code_of(caught.exception)),
                         (404, 'WIKI_RUN_NOT_FOUND'))

    def test_read_and_list_are_owner_scoped(self):
        build_id = self._submit()['build']['build_id']
        self.assertEqual(wiki_jobs.read_build(build_id, self.dsn, self.reviewer)['build']
                         ['build_id'], build_id)
        listing = wiki_jobs.list_builds(self.dsn, self.reviewer)
        self.assertEqual([row['build_id'] for row in listing['builds']], [build_id])
        self.assertFalse(listing['has_more'])
        for identity, expected in ((self.stranger, 'WIKI_BUILD_NOT_FOUND'),):
            with self.assertRaises(HTTPException) as caught:
                wiki_jobs.read_build(build_id, self.dsn, identity)
            self.assertEqual((caught.exception.status_code, code_of(caught.exception)),
                             (404, expected), '非 owner 读别人的任务 = 不存在')
        with self.assertRaises(HTTPException) as caught:
            wiki_jobs.read_build('wjob-' + '0' * 24, self.dsn, self.reviewer)
        self.assertEqual(code_of(caught.exception), 'WIKI_BUILD_NOT_FOUND')

    # ---------------------------------------------------------------- 执行、失败隔离、重试

    def _entry_and_revision(self):
        entry = wiki.create_entry({'entry_type': 'conclusion', 'title': 'E2E 结论',
                                   'run_id': self.run_id, 'claim_id': 'claim-e2e-1'},
                                  self.dsn, self.reviewer)['entry']
        revision = wiki.create_revision(
            entry['entry_id'], {'request_id': 'rev-e2e-1', 'base_revision_id': None,
                                'title': 'E2E 结论', 'body_markdown': '正文'}, self.dsn,
            self.reviewer)['revision']
        return entry, revision

    def test_a_failed_build_only_touches_the_job_row(self):
        build_id = self._submit()['build']['build_id']
        before = self._run_row()

        def broken_builder(job):
            raise RuntimeError('整理器炸了')

        outcome = wiki_jobs.execute_build(build_id, self.dsn, self.reviewer, broken_builder)
        self.assertFalse(outcome['delivered'])
        self.assertEqual(outcome['build']['status'], 'failed')
        self.assertEqual(outcome['build']['error_code'], 'WIKI_BUILD_FAILED')
        self.assertIn('RuntimeError', outcome['build']['error_detail'])
        self.assertEqual(outcome['build']['attempts'], 1)
        self.assertEqual(self._run_row(), before,
                         '整理失败不得改写 runs 的任何一列（P2 验收）')

    def test_a_successful_build_records_the_result_revision(self):
        entry, revision = self._entry_and_revision()
        build_id = self._submit(target_entry_id=entry['entry_id'])['build']['build_id']
        outcome = wiki_jobs.execute_build(
            build_id, self.dsn, self.reviewer,
            lambda job: {'entry_id': entry['entry_id'], 'revision_id': revision['revision_id'],
                         'candidate_entry_id': entry['entry_id'],
                         'suggested_merges': [wiki_jobs.merge_suggestion(
                             entry['entry_id'], 'title_or_alias_overlap')]})
        self.assertTrue(outcome['delivered'])
        self.assertEqual(outcome['build']['status'], 'succeeded')
        self.assertEqual(outcome['build']['result_revision_id'], revision['revision_id'])
        self.assertEqual(outcome['build']['result_entry_id'], entry['entry_id'])
        self.assertTrue(outcome['build']['suggested_merges'][0]['requires_human_confirmation'])

    def test_a_builder_without_a_result_revision_cannot_succeed(self):
        build_id = self._submit()['build']['build_id']
        outcome = wiki_jobs.execute_build(build_id, self.dsn, self.reviewer,
                                          lambda job: {'entry_id': None})
        self.assertFalse(outcome['delivered'])
        self.assertEqual(outcome['build']['status'], 'failed')
        self.assertEqual(outcome['build']['error_code'], 'WIKI_BUILD_FAILED')
        self.assertNotEqual(outcome['build']['status'], 'succeeded')

    def test_a_revoked_role_stops_the_result_at_the_final_recheck(self):
        entry, revision = self._entry_and_revision()
        build_id = self._submit()['build']['build_id']
        # 先用编辑权限领取任务（提交前复查通过）……
        claimed = wiki_jobs.claim_build(build_id, self.dsn, self.reviewer)
        self.assertEqual(claimed['status'], 'running')
        # ……再在返回结果之前撤权：结果不交付，任务落败。
        outcome = wiki_jobs.complete_build(
            build_id, {'entry_id': entry['entry_id'], 'revision_id': revision['revision_id']},
            self.dsn, self.viewer)
        self.assertFalse(outcome['delivered'])
        self.assertEqual(outcome['build']['status'], 'failed')
        self.assertEqual(outcome['build']['error_code'], 'WIKI_PERMISSION_DENIED')
        self.assertIsNone(outcome['build']['result_revision_id'])

    def test_a_claim_is_atomic_and_only_a_queued_job_can_be_claimed(self):
        build_id = self._submit()['build']['build_id']
        self.assertIsNotNone(wiki_jobs.claim_build(build_id, self.dsn, self.reviewer))
        self.assertIsNone(wiki_jobs.claim_build(build_id, self.dsn, self.reviewer),
                          "第二次领取必须 0 行（UPDATE … WHERE status='queued'）")
        with self.assertRaises(HTTPException) as caught:
            wiki_jobs.execute_build(build_id, self.dsn, self.viewer)
        self.assertEqual((caught.exception.status_code, code_of(caught.exception)),
                         (403, 'WIKI_PERMISSION_DENIED'), 'viewer 连领取都不行')

    def test_retry_keeps_the_original_request_and_bumps_the_attempts(self):
        build_id = self._submit()['build']['build_id']
        with self.assertRaises(HTTPException) as caught:
            wiki_jobs.retry_build(build_id, {}, self.dsn, self.reviewer)
        self.assertEqual((caught.exception.status_code, code_of(caught.exception)),
                         (409, 'WIKI_BUILD_NOT_RETRYABLE'), 'queued 不能重试')
        wiki_jobs.execute_build(build_id, self.dsn, self.reviewer,
                                lambda job: (_ for _ in ()).throw(RuntimeError('boom')))
        same = wiki_jobs.retry_build(build_id, {}, self.dsn, self.reviewer)
        self.assertTrue(same['retried_in_place'])
        self.assertEqual(same['build']['build_id'], build_id)
        self.assertEqual(same['build']['status'], 'queued')
        self.assertEqual(same['build']['attempts'], 2)
        self.assertIsNone(same['build']['error_code'])
        self.assertEqual(same['build']['payload_sha256'],
                         wiki_jobs.build_payload_sha256(wiki_jobs.validate_build_request(
                             {'request_id': 'build-e2e-1', 'run_id': self.run_id,
                              'mode': 'template', 'entry_type_hint': 'conclusion'})),
                         '重试保留原载荷摘要，不改指最新运行')
        failed_again = wiki_jobs.execute_build(build_id, self.dsn, self.reviewer,
                                               lambda job: (_ for _ in ()).throw(RuntimeError('x')))
        self.assertEqual(failed_again['build']['status'], 'failed')
        new_row = wiki_jobs.retry_build(build_id, {'request_id': 'build-e2e-1-retry'},
                                        self.dsn, self.reviewer)
        self.assertFalse(new_row['retried_in_place'])
        self.assertNotEqual(new_row['build']['build_id'], build_id)
        self.assertEqual(new_row['build']['retry_of_build_id'], build_id)
        self.assertEqual(new_row['build']['run_id'], self.run_id)
        self.assertEqual(new_row['build']['attempts'], 4)
        with self.assertRaises(HTTPException) as caught:
            wiki_jobs.retry_build(build_id, {'request_id': 'build-e2e-1-retry'},
                                  self.dsn, self.stranger)
        self.assertEqual(code_of(caught.exception), 'WIKI_BUILD_NOT_FOUND',
                         '非 owner 非 admin 重试 → 404（与不存在一致）')


@unittest.skipUnless(DB, 'psycopg2 或 datamind_meta 不可用（数据库用例需要 .venv 解释器）')
class WikiJobsDatabaseContractTest(unittest.TestCase):
    """真库探针：幂等唯一键、WIKI3 触发器、列 CHECK、以及归属查询本身。全部 ROLLBACK。"""

    def setUp(self):
        self.build_id = 'wjob-' + '1' * 24

    def _owner(self, cur):
        cur.execute('SELECT user_id FROM auth_users ORDER BY user_id LIMIT 1')
        row = cur.fetchone()
        if not row:
            self.skipTest('库里没有任何 auth_users 行')
        return row['user_id']

    def _job(self, cur, *, build_id=None, owner=None, request_id='r-1', status='queued',
             error_code=None, result_revision_id=None, payload=None):
        owner = owner or self._owner(cur)
        cur.execute('''INSERT INTO wiki_build_jobs(build_id, space_id, owner_id, request_id,
                payload_sha256, run_id, status, error_code, result_revision_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING build_id''',
                    (build_id or self.build_id, wiki.space_for_user(owner), owner, request_id,
                     payload or ('e' * 64), RUN, status, error_code, result_revision_id))
        return build_id or self.build_id

    def test_the_idempotency_key_is_owner_plus_request_id(self):
        with app_transaction() as cur:
            owner = self._owner(cur)
            self._job(cur, owner=owner, request_id='dup')
            with self.assertRaises(psycopg2.Error) as caught:
                self._job(cur, build_id='wjob-' + '2' * 24, owner=owner, request_id='dup')
            self.assertEqual(getattr(caught.exception, 'pgcode', None), '23505',
                             'UNIQUE (owner_id, request_id) 就是幂等键（021:577）')

    def test_the_wiki3_trigger_refuses_a_payload_rewrite_and_allows_progress(self):
        with app_transaction() as cur:
            self._job(cur)
            with self.assertRaises(psycopg2.Error) as caught:
                cur.execute('''UPDATE wiki_build_jobs SET payload_sha256=%s WHERE build_id=%s''',
                            ('f' * 64, self.build_id))
            self.assertEqual(wiki.append_only_error_code(caught.exception),
                             'WIKI_IDEMPOTENCY_CONFLICT',
                             'SQLSTATE WIKI3 → 409 WIKI_IDEMPOTENCY_CONFLICT')
        with app_transaction() as cur:
            self._job(cur)
            cur.execute('''UPDATE wiki_build_jobs SET status='running', attempts=attempts+1
                WHERE build_id=%s RETURNING status, attempts''', (self.build_id,))
            row = cur.fetchone()
            self.assertEqual((row['status'], row['attempts']), ('running', 1),
                             '流程列与尝试次数可以前进（载荷列不变）')

    def test_the_status_checks_require_an_error_or_a_result(self):
        with app_transaction() as cur:
            with self.assertRaises(psycopg2.Error) as caught:
                self._job(cur, status='failed', error_code=None)
            self.assertEqual(getattr(caught.exception, 'pgcode', None), '23514',
                             "status='failed' 必须有 error_code（021:586）")
        with app_transaction() as cur:
            with self.assertRaises(psycopg2.Error) as caught:
                self._job(cur, status='succeeded', result_revision_id=None)
            self.assertEqual(getattr(caught.exception, 'pgcode', None), '23514',
                             "status='succeeded' 必须有 result_revision_id（021:587）")
        with app_transaction() as cur:
            with self.assertRaises(psycopg2.Error) as caught:
                self._job(cur, status='failed', error_code='NOT_A_WIKI_CODE')
            self.assertEqual(getattr(caught.exception, 'pgcode', None), '23514',
                             '错误码必须匹配 ^WIKI_[A-Z0-9_]+$（021:590）')

    def test_the_ownership_probe_reads_live_runs_without_writing_them(self):
        with app_transaction() as cur:
            cur.execute('SELECT run_id FROM runs ORDER BY run_id LIMIT 1')
            row = cur.fetchone()
            if not row:
                self.skipTest('库里没有 runs 行')
            run_id = row['run_id']
            loaded = wiki_jobs._load_run(cur, run_id)
            self.assertEqual(loaded['run_id'], run_id)
            cur.execute('SELECT count(*) AS counted FROM evidence_nodes WHERE run_id=%s',
                        (run_id,))
            expected = int(cur.fetchone()['counted']) > 0
            self.assertEqual(wiki_jobs._run_has_evidence(cur, run_id), expected)
            cur.execute('''SELECT count(*) AS counted FROM runs
                WHERE run_id=%s AND conversation_id IS NULL''', (run_id,))
            if cur.fetchone()['counted']:
                self.assertIsNone(wiki_jobs.run_ownership_verdict(
                    run_row=loaded, owner_user_id=None,
                    identity={'user_id': 'whoever', 'roles': ['reviewer']}),
                    '无会话归属的运行可达（工业运行）')

    def test_every_wiki_table_is_still_empty(self):
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
