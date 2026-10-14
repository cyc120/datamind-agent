"""会话（chat session）= 对既有 `runs` 行的命名分组，而不是一张消息表。

设计要点（WHY）：

* **不建 messages 表**：一次问答的完整记录已经落在 `runs`（question / answer /
  status / confidence_level / started_at）里，再加一张消息表只会产生第二份真相，
  还要在写入路径上做双写与一致性校验。因此 transcript 一律从 `runs` 推导：
  `question` 是用户轮次，`answer` 是助手轮次，按 `started_at ASC, run_id ASC` 排序。
  run 通过 `runs.conversation_id`（该列早已存在，此前从未写入）关联到 session。
* **软删除**：`runs` 的删除会级联清掉 `trace_events` / `claims` 等证据链
  （`ON DELETE CASCADE`），物理删除 session 等于顺手毁掉可审计的 trace。所以 DELETE
  只写 `deleted_at=now()`，所有读取都带 `deleted_at IS NULL`。
* **归属与 404 隐藏**：每一次读/写都在**同一条 SQL** 里同时约束
  `session_id`、`user_id = identity['user_id']` 和 `deleted_at IS NULL`。这样"别人的
  会话"和"不存在的会话"返回完全相同的 `SESSION_NOT_FOUND`，不泄露会话是否存在。
* **权限门**：`/v1/sessions` 不做角色限制，任何已登录用户只管理自己的会话；写事务里
  仍调用 `lock_identity(cur, identity, None)`，作用是让被吊销的登录态无法继续写。
  这里必须传 `None`：角色词表只有 viewer/reviewer/approver/admin 且互不蕴含，
  传 `'viewer'` 会按字面匹配，把 reviewer/approver 一起挡在 403 外，而
  `None` 在 `lock_identity`/`current` 里的语义是"不做角色门"。

返回值形状（api.py 直接转发）：

* `create`   -> 裸的 public session dict（api.py 需包一层 ``{'session': ...}``）
* `listing`  -> ``{'sessions': [...], 'limit': int, 'offset': int, 'has_more': bool}``
* `detail`   -> ``{'session': {...}, 'messages': [...], 'turns': [...]}``
* `results`  -> ``{'session_id': str, 'results': [...], 'limit': int, 'offset': int, 'has_more': bool}``
* `rename`   -> ``{'session': {...}}``
* `remove`   -> ``{'session': {...}}``

`detail` 的 `messages` 每个 run 一条，字段固定为
``run_id/question/answer/status/confidence_level/started_at``；同时额外给出把两侧摊平的
`turns`（``role=user|assistant``，`text`，未完成时 assistant 的 text 为 None），方便前端
直接顺序渲染，两种形状语义完全一致。
"""
import uuid

from fastapi import HTTPException
import psycopg2
from psycopg2.extras import RealDictCursor

from .auth import lock_identity

#: 新建会话的默认标题；仍是该值时，第一轮提问会触发自动命名（见 touch）。
DEFAULT_TITLE = '新对话'
#: 自动命名截断长度（按字符，不补省略号）。
AUTO_TITLE_LIMIT = 40
#: 标题长度上限，与 DDL 的 CHECK (length(title) <= 200) 对齐。
TITLE_MAX = 200
#: 搜索词长度上限。
QUERY_MAX = 200
LIMIT_DEFAULT = 20
LIMIT_MAX = 50
OFFSET_MAX = 10000
#: SQL 的 LIKE ESCAPE 字符；拼进 SQL 时是单反斜杠，见 _search_pattern。
_ESCAPE = '\\'


def fail(code, status=409):
    """Stable error envelope: {"detail": {"error_code": CODE}}."""
    raise HTTPException(status, detail={'error_code': code})


def _no_nul(value, code, status=422):
    """拒绝含 NUL(0x00) 的文本，否则 psycopg2 会抛 ValueError 而不是 Postgres 错误。

    WHY 必须在入口挡住：`\\x00` 不是合法的 Postgres 文本参数，psycopg2 在**客户端**就抛
    `ValueError: A string literal cannot contain NUL (0x00) characters.`，它绕过本模块所有
    `fail()` 的稳定错误码，冒泡成 500（实测：q / title / 路径里的 session_id 六条路径全部
    500）。用户可控的文本（搜索词、标题、路径参数）因此都要先过这里，换回稳定的
    SESSION_* 契约错误码——一个畸形输入绝不该变成 Internal Server Error。
    """
    if isinstance(value, str) and '\x00' in value:
        fail(code, status)


def _public(row):
    """Projection: only whitelisted columns ever leave this module."""
    return {
        'session_id': row['session_id'],
        'title': row['title'],
        'created_at': row['created_at'],
        'updated_at': row['updated_at'],
    }


def _listed(row):
    """List projection: public columns plus the cheap per-session aggregates."""
    item = _public(row)
    item['run_count'] = int(row['run_count'] or 0)
    item['last_question'] = row['last_question']
    return item


def _user(identity):
    """Owner id; an identity without a user can never match a row (=> 404)."""
    user_id = (identity or {}).get('user_id')
    if not isinstance(user_id, str) or not user_id.strip():
        fail('SESSION_NOT_FOUND', 404)
    return user_id


def _clean_title(value, *, default=None):
    """strip -> required -> length check, then store the stripped form."""
    if value is None:
        if default is not None:
            return default
        fail('SESSION_TITLE_REQUIRED', 422)
    if not isinstance(value, str):
        fail('SESSION_TITLE_REQUIRED', 422)
    _no_nul(value, 'SESSION_TITLE_REQUIRED')
    title = value.strip()
    if not title:
        fail('SESSION_TITLE_REQUIRED', 422)
    if len(title) > TITLE_MAX:
        fail('SESSION_TITLE_TOO_LONG', 422)
    return title


def _create_title(payload):
    """Title on create is optional; an explicit blank still means "invalid"."""
    body = payload if isinstance(payload, dict) else {}
    if body.get('title') is None:
        return DEFAULT_TITLE
    return _clean_title(body['title'])


def _search_pattern(q):
    """Build the ILIKE pattern, escaping \\, % and _ so user text stays literal."""
    if q is None:
        return None
    if not isinstance(q, str):
        fail('SESSION_QUERY_INVALID', 422)
    _no_nul(q, 'SESSION_QUERY_INVALID')
    if len(q) > QUERY_MAX:
        fail('SESSION_QUERY_TOO_LONG', 422)
    text = q.strip()
    if not text:
        return None
    escaped = (text.replace(_ESCAPE, _ESCAPE + _ESCAPE)
                   .replace('%', _ESCAPE + '%')
                   .replace('_', _ESCAPE + '_'))
    return '%' + escaped + '%'


def _bounds(limit, offset):
    """Defensive guard; api.py already enforces the same bounds via Query(...)."""
    if (type(limit) is not int or not 1 <= limit <= LIMIT_MAX
            or type(offset) is not int or not 0 <= offset <= OFFSET_MAX):
        fail('SESSION_PAGINATION_INVALID', 422)


def _session_id():
    """`sess-` + uuid4 hex[:24]; consistent with `run-`/`approval-` precedents."""
    return 'sess-' + uuid.uuid4().hex[:24]


def _auto_title(question):
    """Whitespace-collapsed first question, truncated to AUTO_TITLE_LIMIT chars."""
    if not isinstance(question, str):
        return None
    collapsed = ' '.join(question.split())
    if not collapsed:
        return None
    return collapsed[:AUTO_TITLE_LIMIT]


def create(payload, dsn, identity):
    """Create one session for the caller. Returns the bare public session dict."""
    title = _create_title(payload)
    session_id = _session_id()
    user_id = _user(identity)
    with psycopg2.connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        # 先锁身份：被吊销的登录态不能写入，再动 chat_sessions。
        # 传 None = 「任意已登录用户，不做角色门」。角色词表只有 viewer/reviewer/approver/admin
        # 且互不蕴含，拿 'viewer' 当「登录即可」会把 reviewer/approver 一并挡在 403 外。
        lock_identity(cur, identity, None)
        cur.execute('''INSERT INTO chat_sessions(session_id,user_id,title)
            VALUES (%s,%s,%s) RETURNING session_id,title,created_at,updated_at''',
                    (session_id, user_id, title))
        row = cur.fetchone()
    return _public(row)


def listing(dsn, identity, *, q=None, limit=20, offset=0):
    """Owner-scoped page of live sessions, newest activity first.

    `q` matches the session title OR the question/answer text of any run linked to
    that session; blank/whitespace means "no filter". Pagination fetches limit+1
    rows (service idiom) and reports has_more instead of a second COUNT query.

    `q` 的**计划形态**是这里唯一的性能机关，理由见下面 `OFFSET 0` 处的注释。
    """
    _bounds(limit, offset)
    pattern = _search_pattern(q)
    user_id = _user(identity)

    sql = ['''SELECT s.session_id,s.title,s.created_at,s.updated_at,
        (SELECT COUNT(*) FROM runs r WHERE r.conversation_id=s.session_id) AS run_count,
        (SELECT r.question FROM runs r WHERE r.conversation_id=s.session_id
            ORDER BY r.started_at DESC,r.run_id DESC LIMIT 1) AS last_question
        FROM chat_sessions s
        WHERE s.user_id=%s AND s.deleted_at IS NULL''']
    params = [user_id]
    if pattern is not None:
        # 末尾的 `OFFSET 0` **不是**笔误，也不是多余的偏移：它是让搜索不被规划器改成
        # 「hashed SubPlan」的唯一开关。带 OFFSET 的子查询不再算「简单子查询」，Postgres
        # 因此只能在**每个候选会话**上惰性探测一次，`LIMIT 21` 也就真的能提前收工。
        #
        # WHY 必须挡住那个改写（实测，20 801 会话 / 20 800 runs / 33 MB）：
        #   * hashed SubPlan 会先把**整个 runs 表**扫一遍算出哈希表（`EXPLAIN` 里可见
        #     `Seq Scan on runs` + `hashed SubPlan`），代价与 LIMIT 完全脱钩，而且扫的是
        #     **所有人**的 run（不含 owner 条件），只在自己的会话集合里过滤；
        #     实测：命中 224 条 83.6 ms、高频词 56.7 ms、完全无匹配 69.9 ms。
        #   * 保住惰性探测后：命中 224 条 14.3 ms（×5.9）、高频词 0.9 ms（×63，因为
        #     最近 21 个会话就都命中，扫满即停）；只有「完全无匹配」这一种场景需要扫完
        #     自己的全部会话：78.1 ms（比原来慢 11%，但换掉了全表扫）。
        #   * `LIMIT 1` **挡不住**这个改写（实测仍是 hashed SubPlan，85.6 ms），所以不能
        #     用更好读的 `LIMIT 1` 代替。两者语义等价：已实测基线与本写法返回**同一组**
        #     session_id（含 `%`/`_`/`\` 转义用例）。
        # 风险：若未来 Postgres 取消了「带 OFFSET 的子查询不做 hashed subplan」这条规则，
        # 只会静默退回上面那个慢计划——性能回退，不会算错。tests/test_sessions_api.py
        # 里有一条测试盯住这个写法。
        sql.append('''AND (s.title ILIKE %s ESCAPE '\\' OR EXISTS (
            SELECT 1 FROM runs r WHERE r.conversation_id=s.session_id
            AND (r.question ILIKE %s ESCAPE '\\'
                 OR COALESCE(r.answer,'') ILIKE %s ESCAPE '\\') OFFSET 0))''')
        params.extend([pattern, pattern, pattern])
    sql.append('ORDER BY s.updated_at DESC,s.session_id LIMIT %s OFFSET %s')
    params.extend([limit + 1, offset])

    with psycopg2.connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(' '.join(sql), tuple(params))
        rows = cur.fetchall()
    return {'sessions': [_listed(row) for row in rows[:limit]],
            'limit': limit, 'offset': offset, 'has_more': len(rows) > limit}


def detail(session_id, dsn, identity):
    """One owned live session plus its transcript derived from `runs`."""
    _no_nul(session_id, 'SESSION_NOT_FOUND', 404)
    user_id = _user(identity)
    with psycopg2.connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('''SELECT session_id,title,created_at,updated_at FROM chat_sessions
            WHERE session_id=%s AND user_id=%s AND deleted_at IS NULL''',
                    (session_id, user_id))
        row = cur.fetchone()
        if not row:
            fail('SESSION_NOT_FOUND', 404)
        cur.execute('''SELECT run_id,question,answer,status,confidence_level,started_at
            FROM runs WHERE conversation_id=%s ORDER BY started_at ASC,run_id ASC''',
                    (session_id,))
        rows = cur.fetchall()
    return {'session': _public(row), 'messages': [_message(r) for r in rows],
            'turns': _turns(rows)}


def _message(row):
    return {'run_id': row['run_id'], 'question': row['question'], 'answer': row['answer'],
            'status': row['status'], 'confidence_level': row['confidence_level'],
            'started_at': row['started_at']}


def _turns(rows):
    """Flatten each run into a user turn and an assistant turn.

    The assistant turn is emitted even while the run has no answer yet (text None),
    so the frontend can render a pending bubble in stream order.
    """
    turns = []
    for row in rows:
        common = {'run_id': row['run_id'], 'at': row['started_at'], 'status': row['status'],
                  'confidence_level': row['confidence_level']}
        turns.append({**common, 'role': 'user', 'text': row['question']})
        turns.append({**common, 'role': 'assistant', 'text': row['answer']})
    return turns


#: run 的**终态**事件类型：`agent.py::_answer` 按 status 挑一个写进去
#: （COMPLETED→answer.completed、ABSTAINED→answer.abstained、
#: FAILED_WITH_EVIDENCE→run.failed、其余→answer.clarification）。这份 `final` 字典
#: 就是流式接口最后推给前端的那个对象，因此它是历史富内容的**唯一真相**，不需要另存一份。
_TERMINAL_EVENTS = ('answer.completed', 'answer.abstained', 'run.failed', 'answer.clarification')


def _result(row):
    """One run's terminal payload, verbatim (None when the run never reached a terminal event)."""
    return {'run_id': row['run_id'], 'result': row['result']}


def results(session_id, dsn, identity, *, limit=20, offset=0):
    """Recent runs of one owned live session, each with its terminal result payload.

    为什么是**独立接口**而不是塞进 `detail`：单个终态 `output_summary` 实测平均 14 kB、
    最大 140 kB，而真实库里最大的会话有 221 轮 → 全量富内容一次就是 3.9 MB；`detail`
    的响应会从「几百 kB 的正文」变成「几 MB 的正文 + 结果表」，这是不可接受的。
    所以这里按 run 分页（默认 20，上限 50），前端先渲染正文、再按同一套 limit+1/has_more
    口径取最近若干轮的富内容。

    顺序：**与 `detail` 相反**，按 `started_at DESC, run_id DESC`（最近优先）翻页，
    `offset` 从最新的 run 开始算。响应里也是这个倒序——前端按 `run_id` 合并，
    不依赖顺序。`result` 为 null 表示该 run 没走到终态事件（仍在跑、或早于事件落库），
    这种行也**照样返回**，好让「第 offset 页」与 transcript 的时间窗口一一对齐。

    `result` 是终态事件的 `output_summary` **原样**（与流式 `final` 逐字段一致，含
    `sql_result` / `rag_result.citations` / `industrial_result` / `cross_source_result`）。
    刻意不做字段裁剪：裁剪等于在读取路径上再维护一份投影，迟早与写入侧漂移。
    """
    _no_nul(session_id, 'SESSION_NOT_FOUND', 404)
    _bounds(limit, offset)
    user_id = _user(identity)
    with psycopg2.connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        # 归属检查与 detail 同一条口径：别人的会话与不存在的会话不可区分。
        cur.execute('''SELECT session_id FROM chat_sessions
            WHERE session_id=%s AND user_id=%s AND deleted_at IS NULL''', (session_id, user_id))
        if not cur.fetchone():
            fail('SESSION_NOT_FOUND', 404)
        cur.execute('''SELECT r.run_id, e.output_summary AS result
            FROM runs r
            LEFT JOIN LATERAL (
                SELECT t.output_summary FROM trace_events t
                WHERE t.run_id=r.run_id AND t.event_type IN %s
                ORDER BY t.event_id DESC LIMIT 1) e ON true
            WHERE r.conversation_id=%s
            ORDER BY r.started_at DESC,r.run_id DESC LIMIT %s OFFSET %s''',
                    (_TERMINAL_EVENTS, session_id, limit + 1, offset))
        rows = cur.fetchall()
    return {'session_id': session_id, 'results': [_result(row) for row in rows[:limit]],
            'limit': limit, 'offset': offset, 'has_more': len(rows) > limit}


def rename(session_id, payload, dsn, identity):
    """Rename one owned live session. Returns ``{'session': {...}}``."""
    _no_nul(session_id, 'SESSION_NOT_FOUND', 404)
    body = payload if isinstance(payload, dict) else {}
    title = _clean_title(body.get('title'))
    user_id = _user(identity)
    with psycopg2.connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        lock_identity(cur, identity, None)
        cur.execute('''UPDATE chat_sessions SET title=%s,updated_at=now()
            WHERE session_id=%s AND user_id=%s AND deleted_at IS NULL
            RETURNING session_id,title,created_at,updated_at''',
                    (title, session_id, user_id))
        row = cur.fetchone()
        if not row:
            fail('SESSION_NOT_FOUND', 404)
    return {'session': _public(row)}


def remove(session_id, dsn, identity):
    """Soft delete one owned live session. Returns ``{'session': {...}}``."""
    _no_nul(session_id, 'SESSION_NOT_FOUND', 404)
    user_id = _user(identity)
    with psycopg2.connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        lock_identity(cur, identity, None)
        cur.execute('''UPDATE chat_sessions SET deleted_at=now(),updated_at=now()
            WHERE session_id=%s AND user_id=%s AND deleted_at IS NULL
            RETURNING session_id,title,created_at,updated_at''',
                    (session_id, user_id))
        row = cur.fetchone()
        if not row:
            fail('SESSION_NOT_FOUND', 404)
    return {'session': _public(row)}


def touch(session_id, question, dsn):
    """Called by agent.py right after a run joins this session.

    Bumps `updated_at` and, while the title is still the default, names the session
    after the first question (whitespace collapsed, 40 chars max, no ellipsis).

    No identity is needed: the agent already validated and locked the caller inside
    its own write transaction, and this helper runs on the same write DSN. It is
    best-effort by contract — a missing/soft-deleted session or any transient DB
    error must never break the answer the user already paid for, so every failure
    is swallowed here instead of propagating into the run.
    """
    if not isinstance(session_id, str) or not session_id:
        return
    title = _auto_title(question)
    try:
        with psycopg2.connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            if title is None:
                cur.execute('''UPDATE chat_sessions SET updated_at=now()
                    WHERE session_id=%s AND deleted_at IS NULL''', (session_id,))
            else:
                # 仅当标题仍是默认值时改名：用户显式命名过的会话绝不覆盖。
                cur.execute('''UPDATE chat_sessions
                    SET updated_at=now(),
                        title=CASE WHEN title=%s THEN %s ELSE title END
                    WHERE session_id=%s AND deleted_at IS NULL''',
                            (DEFAULT_TITLE, title, session_id))
    except Exception:
        return
