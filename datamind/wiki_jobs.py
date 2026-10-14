"""Wiki 整理任务（`wiki_build_jobs`）：幂等、重试、权限复查、失败隔离。

契约依据：`docs/WIKI_API.md` §5.9（提交）、§5.10（查看）、§5.11（重试）、§1 幂等、§8 错误码；
`docs/WIKI_DOMAIN.md` §5.4（整理失败不得改写既有问答的账）；`021:541-594`（表与 WIKI3 触发器）。

WHY 这个模块长这样：

1. **幂等键是 `(owner_id, request_id)`，载荷摘要是 `payload_sha256`**（021:556-557 / :577）。
   摘要是 `run_id, target_entry_id, entry_type_hint, mode, selection` 五项的规范 JSON 的
   sha256 —— `space_id` 不进摘要（它由 owner 决定，不是请求内容）。同键同摘要 → 复用首次
   结果、**不**创建第二个版本；同键不同摘要 → 409 `WIKI_IDEMPOTENCY_CONFLICT`
   （`wiki_build_jobs_request_immutable()` 触发器从数据库侧保证载荷列改不了）。

2. **`request_id` 的存在性隐藏比数据库的唯一键更严**。数据库的唯一键是
   `(owner_id, request_id)`（021:577），而 `WIKI_API.md` §5.9 的表冻结了
   "同 `request_id` + 不同 owner → 404 `WIKI_BUILD_NOT_FOUND`"。因此提交前按
   `request_id` **全局**查一次：别人的同名 request_id 与"不存在"不可区分。代价如实记录：
   `request_id` 事实上成了全局命名空间，"build-1"这种通用名字会被先到者占用。

3. **`run_id` 故意没有外键**（021:31-35：`ADD FOREIGN KEY` 会锁住 `postgres` 持有的
   `runs` 表，仓库有整批迁移回滚的事故记录）。所以归属校验必须在写事务里自己做：
   `runs.conversation_id → chat_sessions.user_id` 才是归属；工业运行 `conversation_id` 为
   NULL（实测 `run-9ab6d6af60c64f32`）→ **没有会话归属**，按 §11.6 的结论只落触发者自己的
   空间，不构成"别人的运行"。

4. **权限复查两次：提交前与返回前**（需求"长任务提交前与返回前都要复查"）。
   `claim_build()` 在把任务置为 `running` 的同一个事务里复查（撤权后不再开工）；
   `complete_build()` 在写结果、也是**返回结果之前**再复查一次（角色可能在任务运行期间被撤），
   撤权时任务以 `WIKI_PERMISSION_DENIED` 落败，而不是把结果交出去。

5. **整理失败不影响原问答结果**。本模块**只**写 `wiki_build_jobs`（以及它调用的
   `datamind.wiki` 写 `wiki_*` 表）：整份文件里没有一条 `INSERT/UPDATE/DELETE` 指向
   `runs` / `claims` / `trace_events` / `evidence_*`，`tests/test_wiki_jobs.py` 有静态断言
   钉住这一点。失败被记进任务行（`status='failed'` + `error_code` + `error_detail`），
   与 `sessions.touch()` 的"记账 best-effort"惯例同款：记账绝不反噬用户已经拿到的答案。
"""
from __future__ import annotations

import re

from fastapi import HTTPException

from . import wiki

#: 021:559-561 的闭集。
BUILD_MODES = ('template', 'template+polish')
BUILD_STATES = ('queued', 'running', 'succeeded', 'failed')
#: 021:590 的错误码命名规则。不符合的码一律归到 `WIKI_BUILD_FAILED`，否则 INSERT/UPDATE 会被 CHECK 拒。
ERROR_CODE_RE = re.compile(r'^WIKI_[A-Z0-9_]+$')
#: `error_detail` 的落库长度上限（列是 TEXT，这里按"够定位问题、又不塞满日志"截断）。
ERROR_DETAIL_MAX = 500
BUILDS_LIMIT_DEFAULT = 20
BUILDS_LIMIT_MAX = 50


# ---------------------------------------------------------------------------
# 1. 纯逻辑：规范载荷、幂等判定、归属判定（都不需要数据库）
# ---------------------------------------------------------------------------

def canonical_selection(value):
    """`selection` 的归一：`{"claim_ids": [...], "evidence_ids": [...]}`（§5.9）。

    * 缺省 → `{}`（表列默认也是 `{}`，021:560）；
    * 数组去重且**排序**：集合语义相同但顺序不同的两次请求必须得到同一个载荷摘要，
      否则"同 request_id + 同规范载荷"会被顺序差异拆成 409；
    * `null` 与缺省等价（`claim_ids: null` = 不筛选）。
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        wiki.fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='selection must be an object')
    unknown = set(value) - {'claim_ids', 'evidence_ids'}
    if unknown:
        wiki.fail('WIKI_BUILD_REQUEST_INVALID', 422,
                  hint='unknown selection keys: %s' % ', '.join(sorted(unknown)))
    selection = {}
    for key in ('claim_ids', 'evidence_ids'):
        raw = value.get(key)
        if raw is None:
            continue
        if not isinstance(raw, list):
            wiki.fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='selection.%s must be an array' % key)
        items = []
        for item in raw:
            text = wiki._text(item, 'selection.%s[]' % key, max_length=64)
            if text not in items:
                items.append(text)
        selection[key] = sorted(items)
    return selection


def canonical_build_request(request):
    """载荷摘要的**输入**：恰好是 021:556-557 点名的那五项。

    `space_id` / `owner_id` / `request_id` 不进摘要：它们不是"请求内容"。
    """
    return {'run_id': request['run_id'],
            'target_entry_id': request.get('target_entry_id'),
            'entry_type_hint': request.get('entry_type_hint'),
            'mode': request.get('mode') or 'template',
            'selection': canonical_selection(request.get('selection'))}


def build_payload_sha256(request) -> str:
    """规范载荷的 sha256（裸 hex64，021:582 的 CHECK）。"""
    return wiki.sha256_json(canonical_build_request(request))


def validate_build_request(payload):
    """校验并归一 `POST /v1/wiki/builds` 的载荷（§5.9）。

    任何形状问题 → 422 `WIKI_BUILD_REQUEST_INVALID`（`run_id` 空、`mode` 未知…），
    并返回带 `payload_sha256` 的规范请求。**不做**归属校验（那要连库，见 `submit_build`）。
    """
    body = wiki._body(payload)
    request_id = wiki._text(body.get('request_id'), 'request_id', max_length=wiki.REQUEST_ID_MAX)
    run_id = wiki._text(body.get('run_id'), 'run_id', max_length=64)
    mode = body.get('mode') or BUILD_MODES[0]
    if mode not in BUILD_MODES:
        wiki.fail('WIKI_BUILD_REQUEST_INVALID', 422,
                  hint='mode must be one of %s' % (BUILD_MODES,))
    hint = body.get('entry_type_hint')
    if hint is not None and hint not in wiki.ENTRY_TYPES:
        wiki.fail('WIKI_BUILD_REQUEST_INVALID', 422,
                  hint='entry_type_hint must be one of %s' % (wiki.ENTRY_TYPES,))
    target = body.get('target_entry_id')
    if target is not None:
        target = wiki._text(target, 'target_entry_id', max_length=64)
        if not wiki.ENTRY_ID_RE.fullmatch(target):
            wiki.fail('WIKI_BUILD_REQUEST_INVALID', 422,
                      hint='target_entry_id must be wiki- + hex24')
    request = {'request_id': request_id, 'run_id': run_id, 'mode': mode,
               'entry_type_hint': hint, 'target_entry_id': target,
               'selection': canonical_selection(body.get('selection')),
               'space_id': body.get('space_id')}
    request['payload_sha256'] = build_payload_sha256(request)
    return request


def idempotency_decision(*, existing_owner, existing_payload_sha256, incoming_payload_sha256,
                         caller_user_id, admin=False):
    """§5.9 的三条幂等分支。返回 `'create' | 'reuse' | 'conflict' | 'hidden'`。

    | 情形 | 行为 |
    | --- | --- |
    | 没有同 request_id 的行 | `create` |
    | 同 request_id、**不同 owner**、非 admin | `hidden` → 404（与不存在不可区分） |
    | 同 owner、同载荷摘要 | `reuse` → 返回首次结果，不创建重复版本 |
    | 同 owner、不同载荷摘要 | `conflict` → 409 `WIKI_IDEMPOTENCY_CONFLICT` |
    """
    if existing_owner is None:
        return 'create'
    if existing_owner != caller_user_id and not admin:
        return 'hidden'
    if existing_payload_sha256 == incoming_payload_sha256:
        return 'reuse'
    return 'conflict'


def run_ownership_verdict(*, run_row, owner_user_id, identity):
    """目标运行的可达性（`runs` 没有 FK，只能在服务层判）。

    返回 `None`（可达）或 `(code, status)`：

    * 运行不存在 → `('WIKI_RUN_NOT_FOUND', 404)`；
    * 运行挂在**别人**的会话下且请求者不是 admin → `('WIKI_RUN_FORBIDDEN', 403)`；
    * `conversation_id` 为空（工业运行，实测无归属）→ 可达：没有归属就没有"别人"，
      按 `WIKI_API.md` §11.6 只落触发者自己的空间，并要求界面标注"运行无会话归属"。
    """
    if run_row is None:
        return ('WIKI_RUN_NOT_FOUND', 404)
    if wiki.is_admin(identity):
        return None
    if run_row.get('conversation_id') is None:
        return None
    if owner_user_id is None:
        # 会话行查不到（例如会话已被物理清理）：无归属可判 → 按"不是你的"处理，
        # 而不是放行。fail-closed。
        return ('WIKI_RUN_FORBIDDEN', 403)
    if owner_user_id != wiki.user_id_of(identity):
        return ('WIKI_RUN_FORBIDDEN', 403)
    return None


def retry_verdict(*, status):
    """§5.11：只有 `failed` 能重试；其它状态 → 409 `WIKI_BUILD_NOT_RETRYABLE`。"""
    if status == 'failed':
        return None
    return ('WIKI_BUILD_NOT_RETRYABLE', 409)


def sanitize_error_code(code):
    """错误码必须满足 021:590 的 `^WIKI_[A-Z0-9_]+$`，否则归到 `WIKI_BUILD_FAILED`。"""
    if isinstance(code, str) and ERROR_CODE_RE.fullmatch(code.strip()):
        return code.strip()
    return 'WIKI_BUILD_FAILED'


def failure_record(exc):
    """异常 → `(error_code, error_detail)`。

    * `datamind.wiki` / 本模块抛出的 `HTTPException` 带着稳定的 `WIKI_*` 码 → 原样记录
      （例如整理时发现基线过期 → `WIKI_VERSION_CONFLICT`，任务详情里能直接看到原因）；
    * 其它异常 → `WIKI_BUILD_FAILED` + `类型: 消息`（截断到 `ERROR_DETAIL_MAX`）。

    这是"整理失败不影响原问答结果"的落点：失败只变成一行任务状态，**不**回写 `runs`。
    """
    code = wiki.error_code_of(exc)
    detail = None
    if code is not None:
        raw = getattr(exc, 'detail', None)
        detail = wiki.canonical_json(raw) if isinstance(raw, dict) else str(raw)
    else:
        detail = '%s: %s' % (type(exc).__name__, exc)
    detail = ' '.join((detail or '').split())[:ERROR_DETAIL_MAX]
    if not detail:
        detail = code or 'WIKI_BUILD_FAILED'
    return sanitize_error_code(code), detail


def completion_verdict(*, permission_ok, owner_ok):
    """返回结果之前的最终复查结论。

    `permission_ok` = 当前身份仍持有编辑权限（或 admin）；`owner_ok` = 仍是原 owner（或 admin）。
    任一为假 → 任务以 `WIKI_PERMISSION_DENIED` 落败，**不**把结果交出去。
    """
    if not owner_ok:
        return ('WIKI_BUILD_NOT_FOUND', 404)
    if not permission_ok:
        return ('WIKI_PERMISSION_DENIED', 403)
    return None


def merge_suggestion(entry_id_value, reason, detail=None):
    """合并**建议**。`requires_human_confirmation` 恒为 True（§5.10 / §2.1 禁令）。

    名称相近或别名重叠只能产生建议：它**不能**改身份，也**不能**自动归并词条。
    """
    suggestion = {'entry_id': entry_id_value, 'reason': reason,
                  'requires_human_confirmation': True}
    if detail is not None:
        suggestion['detail'] = detail
    return suggestion


def build_object(row):
    """§5.9 / §5.10 的 build 对象。"""
    owner = {'user_id': row['owner_id']}
    return {'build_id': row['build_id'],
            'status': row['status'],
            'request_id': row['request_id'],
            'payload_sha256': row['payload_sha256'],
            'run_id': row['run_id'],
            'space_id': row['space_id'],
            'mode': row['mode'],
            'entry_type_hint': row['entry_type_hint'],
            'target_entry_id': row['target_entry_id'],
            'owner': owner,
            'attempts': int(row['attempts']),
            'result_entry_id': row['result_entry_id'],
            'result_revision_id': row['result_revision_id'],
            'candidate_entry_id': row['candidate_entry_id'],
            'suggested_merges': list(row['suggested_merges'] or []),
            'error_code': row['error_code'],
            'error_detail': row['error_detail'],
            'retry_of_build_id': row['retry_of_build_id'],
            'created_at': row['created_at'],
            'updated_at': row['updated_at']}


# ---------------------------------------------------------------------------
# 2. 数据库路径
# ---------------------------------------------------------------------------

_BUILD_COLUMNS = ('build_id, space_id, owner_id, request_id, payload_sha256, run_id, mode, '
                  'selection, entry_type_hint, target_entry_id, status, attempts, result_entry_id, '
                  'candidate_entry_id, result_revision_id, suggested_merges, error_code, '
                  'error_detail, retry_of_build_id, created_at, updated_at')


def _load_job(cur, build_id_value, identity, *, for_update=False):
    """按 owner（或 admin）加载任务；非 owner 与不存在**完全一样**的 404（§3.1 脚注 3）。"""
    wiki._no_nul(build_id_value, 'WIKI_BUILD_NOT_FOUND', 404)
    if not isinstance(build_id_value, str) or not build_id_value.strip().startswith('wjob-'):
        wiki.fail('WIKI_BUILD_NOT_FOUND', 404)
    sql = ('SELECT ' + _BUILD_COLUMNS + ' FROM wiki_build_jobs WHERE build_id = %s '
           'AND (%s OR owner_id = %s)')
    if for_update:
        sql += ' FOR UPDATE'
    cur.execute(sql, (build_id_value.strip(), wiki.is_admin(identity), wiki.user_id_of(identity)))
    row = cur.fetchone()
    if not row:
        wiki.fail('WIKI_BUILD_NOT_FOUND', 404)
    return row


def _job_by_request_id(cur, request_id_value):
    """按 request_id **全局**查一行（见模块头 WHY 2）。"""
    cur.execute('SELECT ' + _BUILD_COLUMNS + ''' FROM wiki_build_jobs
        WHERE request_id = %s ORDER BY created_at, build_id LIMIT 1''', (request_id_value,))
    return cur.fetchone()


def _load_run(cur, run_id_value):
    cur.execute('SELECT run_id, conversation_id, status FROM runs WHERE run_id = %s',
                (run_id_value,))
    return cur.fetchone()


def _run_owner(cur, run_row):
    if not run_row or not run_row['conversation_id']:
        return None
    cur.execute('SELECT user_id FROM chat_sessions WHERE session_id = %s',
                (run_row['conversation_id'],))
    row = cur.fetchone()
    return row['user_id'] if row else None


def _run_has_evidence(cur, run_id_value):
    """`evidence_nodes` 里有没有该运行的行（§8.2：`WIKI_NO_EVIDENCE` 的判据）。

    只读 `postgres` 持有的表；**绝不**写它（021:24-35 的边界）。
    """
    cur.execute('SELECT 1 FROM evidence_nodes WHERE run_id = %s LIMIT 1', (run_id_value,))
    return cur.fetchone() is not None


def title_overlap_suggestions(cur, *, space_id, title, entry_type, exclude_entry_id=None,
                              limit=5):
    """标题/别名重叠的**合并建议**（同一空间内，最多 `limit` 条）。

    只用 `ILIKE` 前缀模糊（不引入任何扩展，`docs/SESSIONS.md` §6 同款约定），
    结果一律带 `requires_human_confirmation: True`。
    """
    if not title:
        return []
    token = '%' + title.strip().lower() + '%'
    cur.execute('''SELECT entry_id, title FROM wiki_entries
        WHERE space_id = %s AND entry_type = %s AND entry_id <> COALESCE(%s, '')
          AND (lower(title) LIKE %s OR EXISTS (
                SELECT 1 FROM unnest(aliases) AS alias WHERE lower(alias) LIKE %s))
        ORDER BY updated_at DESC, entry_id LIMIT %s''',
                (space_id, entry_type, exclude_entry_id, token, token, limit))
    return [merge_suggestion(row['entry_id'], 'title_or_alias_overlap', row['title'])
            for row in cur.fetchall()]


def submit_build(payload, dsn, identity):
    """`POST /v1/wiki/builds`：从指定运行创建候选草稿（§5.9）。

    顺序刻意如此：**先幂等、再归属、最后证据与入库**。
    幂等判断必须在写任何东西之前完成，否则"同 request_id 不同载荷"会先落一行再报错。
    """
    request = validate_build_request(payload)
    caller = wiki.user_id_of(identity)
    with wiki.connect(dsn) as conn, conn.cursor(cursor_factory=wiki.RealDictCursor) as cur:
        wiki._lock_actor(cur, identity, 'reviewer')
        space_id_value = wiki.resolve_space(identity, request.get('space_id'))
        existing = _job_by_request_id(cur, request['request_id'])
        decision = idempotency_decision(
            existing_owner=existing['owner_id'] if existing else None,
            existing_payload_sha256=existing['payload_sha256'] if existing else None,
            incoming_payload_sha256=request['payload_sha256'],
            caller_user_id=caller, admin=wiki.is_admin(identity))
        if decision == 'hidden':
            wiki.fail('WIKI_BUILD_NOT_FOUND', 404)
        if decision == 'reuse':
            return {'build': build_object(existing), 'reused': True}
        if decision == 'conflict':
            wiki.fail('WIKI_IDEMPOTENCY_CONFLICT', 409,
                      hint='request_id %s already describes another payload'
                           % request['request_id'])
        run_row = _load_run(cur, request['run_id'])
        verdict = run_ownership_verdict(run_row=run_row, owner_user_id=_run_owner(cur, run_row),
                                        identity=identity)
        if verdict:
            wiki.fail(verdict[0], verdict[1])
        target_entry = None
        if request['target_entry_id']:
            target_entry = wiki._load_entry(cur, request['target_entry_id'], identity)
        if not _run_has_evidence(cur, request['run_id']):
            # 目标运行没有可投影的证据：这不是"整理失败"，而是没有可整理的原料（§8.2）。
            wiki.fail('WIKI_NO_EVIDENCE', 409,
                      hint='run %s has no evidence_nodes row to project' % request['run_id'])
        suggestions = title_overlap_suggestions(
            cur, space_id=space_id_value,
            title=(target_entry['title'] if target_entry else None),
            entry_type=(target_entry['entry_type'] if target_entry
                        else (request['entry_type_hint'] or 'conclusion')),
            exclude_entry_id=request['target_entry_id'], limit=5)
        cur.execute('''INSERT INTO wiki_build_jobs(build_id, space_id, owner_id, request_id,
                payload_sha256, run_id, mode, selection, entry_type_hint, target_entry_id,
                suggested_merges)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING ''' + _BUILD_COLUMNS,
                    (wiki.new_id('wjob-'), space_id_value, caller, request['request_id'],
                     request['payload_sha256'], request['run_id'], request['mode'],
                     wiki._jsonb(request['selection']), request['entry_type_hint'],
                     request['target_entry_id'], wiki._jsonb(suggestions)))
        row = cur.fetchone()
    return {'build': build_object(row), 'reused': False}


def read_build(build_id_value, dsn, identity):
    """`GET /v1/wiki/builds/{build_id}`：owner 校验在 SQL 里（§5.10），无角色门。"""
    with wiki.connect(dsn) as conn, conn.cursor(cursor_factory=wiki.RealDictCursor) as cur:
        row = _load_job(cur, build_id_value, identity)
    return {'build': build_object(row)}


def list_builds(dsn, identity, *, limit=BUILDS_LIMIT_DEFAULT, cursor=None):
    """自己的整理任务列表（默认 20 / 上限 50），键集分页，`build_id` 作唯一兜底键。"""
    if type(limit) is not int or not 1 <= limit <= BUILDS_LIMIT_MAX:
        wiki.fail('WIKI_BUILD_REQUEST_INVALID', 422,
                  hint='limit must be between 1 and %d' % BUILDS_LIMIT_MAX)
    keys = ('created_at', 'build_id')
    marker = wiki.decode_cursor(cursor, keys=keys) if cursor else None
    sql = ('SELECT ' + _BUILD_COLUMNS + ' FROM wiki_build_jobs WHERE (%s OR owner_id = %s)')
    params = [wiki.is_admin(identity), wiki.user_id_of(identity)]
    if marker:
        sql += ' AND (created_at, build_id) < (%s::timestamptz, %s)'
        params.extend([marker['created_at'], marker['build_id']])
    sql += ' ORDER BY created_at DESC, build_id DESC LIMIT %s'
    params.append(limit + 1)
    with wiki.connect(dsn) as conn, conn.cursor(cursor_factory=wiki.RealDictCursor) as cur:
        # 读路径不锁身份（`lock_identity` 是写事务的惯例，会给 auth_* 加 FOR SHARE）：
        # 这里只需要一个 user_id，越权由 SQL 里的 owner 过滤承担（§5.10）。
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit and page:
        next_cursor = wiki.encode_cursor({'created_at': page[-1]['created_at'].isoformat(),
                                          'build_id': page[-1]['build_id']})
    return {'builds': [build_object(row) for row in page], 'limit': limit,
            'has_more': len(rows) > limit, 'next_cursor': next_cursor, 'truncated': False}


def retry_build(build_id_value, payload, dsn, identity):
    """`POST /v1/wiki/builds/{build_id}/retry`：原请求失败重试（§5.11）。

    * 角色门 `reviewer`（**当前**权限）＋ 原 owner（非 owner 非 admin → 404）；
    * 只有 `status='failed'` 可重试，否则 409 `WIKI_BUILD_NOT_RETRYABLE`；
    * **保留**原目标运行、原载荷摘要、原空间，绝不改成"最新运行"；
    * `request_id` 省略（或与原值相同）→ 在同一行上重试（`attempts+1`，载荷列一律不动，
      WIKI3 触发器只管载荷列，所以这条路合法且不产生第二行）；
      给了**新** `request_id` → 新行，并记 `retry_of_build_id`。
    """
    body = payload if isinstance(payload, dict) else {}
    new_request_id = wiki._text(body.get('request_id'), 'request_id',
                                max_length=wiki.REQUEST_ID_MAX, required=False)
    with wiki.connect(dsn) as conn, conn.cursor(cursor_factory=wiki.RealDictCursor) as cur:
        actor = wiki._lock_actor(cur, identity, 'reviewer')
        job = _load_job(cur, build_id_value, identity, for_update=True)
        verdict = retry_verdict(status=job['status'])
        if verdict:
            wiki.fail(verdict[0], verdict[1],
                      hint='only a failed build can be retried (status=%s)' % job['status'])
        if new_request_id is None or new_request_id == job['request_id']:
            cur.execute('''UPDATE wiki_build_jobs SET status = 'queued', attempts = attempts + 1,
                    error_code = NULL, error_detail = NULL, updated_at = now()
                WHERE build_id = %s RETURNING ''' + _BUILD_COLUMNS, (job['build_id'],))
            row = cur.fetchone()
            return {'build': build_object(row), 'reused': False, 'retried_in_place': True,
                    'actor': actor['user_id']}
        existing = _job_by_request_id(cur, new_request_id)
        decision = idempotency_decision(
            existing_owner=existing['owner_id'] if existing else None,
            existing_payload_sha256=existing['payload_sha256'] if existing else None,
            incoming_payload_sha256=job['payload_sha256'], caller_user_id=actor['user_id'],
            admin=wiki.is_admin(identity))
        if decision == 'hidden':
            wiki.fail('WIKI_BUILD_NOT_FOUND', 404)
        if decision == 'reuse':
            return {'build': build_object(existing), 'reused': True, 'retried_in_place': False,
                    'actor': actor['user_id']}
        if decision == 'conflict':
            wiki.fail('WIKI_IDEMPOTENCY_CONFLICT', 409,
                      hint='request_id %s already describes another payload' % new_request_id)
        cur.execute('''INSERT INTO wiki_build_jobs(build_id, space_id, owner_id, request_id,
                payload_sha256, run_id, mode, selection, entry_type_hint, target_entry_id,
                suggested_merges, attempts, retry_of_build_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING ''' + _BUILD_COLUMNS,
                    (wiki.new_id('wjob-'), job['space_id'], job['owner_id'], new_request_id,
                     job['payload_sha256'], job['run_id'], job['mode'],
                     wiki._jsonb(job['selection']), job['entry_type_hint'],
                     job['target_entry_id'], wiki._jsonb(list(job['suggested_merges'] or [])),
                     int(job['attempts']) + 1, job['build_id']))
        row = cur.fetchone()
    return {'build': build_object(row), 'reused': False, 'retried_in_place': False,
            'actor': actor['user_id']}


def claim_build(build_id_value, dsn, identity, *, permission='reviewer'):
    """把任务置为 `running` 并 `attempts+1`；**提交前的权限复查**就在这个事务里。

    返回任务行，或 `None`（任务已被别人领走 / 已终态 —— 原子 `UPDATE … WHERE status='queued'`
    返回 0 行）。
    """
    with wiki.connect(dsn) as conn, conn.cursor(cursor_factory=wiki.RealDictCursor) as cur:
        wiki._lock_actor(cur, identity, permission)   # 撤权后不再开工
        sql = ('''UPDATE wiki_build_jobs SET status = 'running', attempts = attempts + 1,
                updated_at = now()
            WHERE build_id = %s AND status = 'queued' ''')
        params = [build_id_value]
        if not wiki.is_admin(identity):
            # admin 是超管旁路（auth.py:195/217），可以领取任何 owner 的任务。
            sql += ' AND owner_id = %s'
            params.append(wiki.user_id_of(identity))
        cur.execute(sql + 'RETURNING ' + _BUILD_COLUMNS, tuple(params))
        row = cur.fetchone()
    return dict(row) if row else None


def complete_build(build_id_value, result, dsn, identity, *, permission='reviewer'):
    """写结果并返回。**返回之前的最终权限复查**：撤权 → 任务落败、结果不交付。

    `result` 形如 `{'entry_id':…, 'revision_id':…, 'candidate_entry_id':…,
    'suggested_merges':[…]}`；`revision_id` 必填（021:587：`succeeded` 必须有结果版本）。
    """
    result = result if isinstance(result, dict) else {}
    revision = result.get('revision_id')
    if not isinstance(revision, str) or not revision.strip():
        wiki.fail('WIKI_BUILD_FAILED', 409, hint='a succeeded build must carry a result revision')
    with wiki.connect(dsn) as conn, conn.cursor(cursor_factory=wiki.RealDictCursor) as cur:
        job = _load_job(cur, build_id_value, identity, for_update=True)
        owner_ok = wiki.is_admin(identity) or job['owner_id'] == wiki.user_id_of(identity)
        permission_ok = wiki.has_role(identity, permission)
        verdict = completion_verdict(permission_ok=permission_ok, owner_ok=owner_ok)
        if verdict and verdict[0] == 'WIKI_BUILD_NOT_FOUND':
            wiki.fail(verdict[0], verdict[1])
        if verdict:
            # 最终复查没过：把失败写进任务行（这一步不能再要求权限，否则任务会永远卡在
            # running）；结果**不**交付。
            row = _mark_failed(cur, job['build_id'], verdict[0],
                               'permission recheck failed before returning the result')
            return {'build': build_object(row), 'delivered': False}
        if job['status'] != 'running':
            wiki.fail('WIKI_BUILD_NOT_RETRYABLE', 409,
                      hint='the build is not running (status=%s)' % job['status'])
        suggestions = result.get('suggested_merges')
        if suggestions is None:
            suggestions = list(job['suggested_merges'] or [])
        cur.execute('''UPDATE wiki_build_jobs SET status = 'succeeded',
                result_entry_id = %s, candidate_entry_id = %s, result_revision_id = %s,
                suggested_merges = %s, error_code = NULL, error_detail = NULL, updated_at = now()
            WHERE build_id = %s RETURNING ''' + _BUILD_COLUMNS,
                    (result.get('entry_id') or result.get('candidate_entry_id'),
                     result.get('candidate_entry_id') or result.get('entry_id'), revision,
                     wiki._jsonb(suggestions), job['build_id']))
        row = cur.fetchone()
    return {'build': build_object(row), 'delivered': True}


def _mark_failed(cur, build_id_value, code, detail):
    cur.execute('''UPDATE wiki_build_jobs SET status = 'failed', error_code = %s,
            error_detail = %s, updated_at = now()
        WHERE build_id = %s RETURNING ''' + _BUILD_COLUMNS,
                (sanitize_error_code(code), detail, build_id_value))
    return cur.fetchone()


def fail_build(build_id_value, code, detail, dsn):
    """把失败写进任务行（best-effort）。

    **不设权限门**：如果撤权就能让任务永远停在 `running`，那才是真问题。记账失败本身
    也绝不冒泡 —— 与 `sessions.touch()` 的 best-effort 同款（`docs/SESSIONS.md` §8）。
    """
    try:
        with wiki.connect(dsn) as conn, conn.cursor(cursor_factory=wiki.RealDictCursor) as cur:
            row = _mark_failed(cur, build_id_value, code, detail)
        return {'build': build_object(row)} if row else None
    except Exception:
        return None


def _failed(build_id_value, code, detail, dsn):
    """失败落账的统一出口：`{'build': …, 'delivered': False}`（记账失败也不抛）。"""
    outcome = fail_build(build_id_value, code, detail, dsn)
    return {'build': outcome['build'] if outcome else None, 'delivered': False}


def execute_build(build_id_value, dsn, identity, builder=None, *, permission='reviewer'):
    """领取 → 整理 → 交付的完整流程，含两次权限复查与失败隔离。

    `builder` 是**注入**的整理回调（`job_row -> {'entry_id','revision_id',…}`）：投影与模板
    组装属于 P1 的 `datamind/wiki_projection.py`，本模块不 import 它，避免两层互相绑死。
    `builder` 为 None 时任务以 `WIKI_BUILD_FAILED` 落败（"整理器不可用"），而不是静默成功。

    返回 `{'build': …, 'delivered': bool}`：整条路径上任何异常都只会改变任务行，
    **不会**触碰 `runs` / `claims` / `trace_events`（模块头 WHY 5）。
    """
    job = claim_build(build_id_value, dsn, identity, permission=permission)
    if job is None:
        return {'build': read_build(build_id_value, dsn, identity)['build'], 'delivered': False}
    if builder is None:
        return _failed(build_id_value, 'WIKI_BUILD_FAILED',
                       'no builder is wired for this deployment', dsn)
    try:
        result = builder(job)
    except HTTPException as exc:
        code, detail = failure_record(exc)
        return _failed(build_id_value, code, detail, dsn)
    except Exception as exc:  # 整理阶段任何异常都不外溢
        code, detail = failure_record(exc)
        return _failed(build_id_value, code, detail, dsn)
    try:
        return complete_build(build_id_value, result, dsn, identity, permission=permission)
    except HTTPException as exc:
        code, detail = failure_record(exc)
        return _failed(build_id_value, code, detail, dsn)
