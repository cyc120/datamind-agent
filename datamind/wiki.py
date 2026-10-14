"""Wiki 知识图谱 P2 服务层：词条身份、不可变版本、追加式复核、CAS 发布。

本模块**只**实现三份已冻结文件里写着的东西，不发明语义：

* `docs/WIKI_API.md` —— §3 权限矩阵、§5.3–5.5 版本读取、§5.12–5.14 三条写路径、§7 三套状态、§8 错误码；
* `docs/WIKI_DOMAIN.md` —— §4 三类身份、§5 三套独立状态（禁令逐条）、§6 版本记账；
* `db/migrations/021_wiki_knowledge.sql`（984 行）—— 8 张表、全部 CHECK、4 个 append-only 触发器。

WHY 这个模块长这样（每条都可在检出里核对，不是偏好）：

1. **8 张 Wiki 表是私有的，所以读写都走应用角色。** `021:605-607` 对 8 张表执行
   `REVOKE ALL ... FROM PUBLIC, readonly_user`；实测 `information_schema.role_table_grants`
   里只有 `datamind_app` 有权限，`readonly_user` 连 SELECT 都没有。因此"写=datamind_app、
   读=readonly_user"的仓库惯例**在 Wiki 表上不成立**：Wiki 的读路径也必须在 `datamind_app`
   上跑（仍然只发 SELECT）。先例：`chat_sessions` 同样被 REVOKE（`020:78`），`api.py:1218`
   就是用 `_write_dsn()` 读它的。`read_dsn()` 保留下来只为与本目录其它模块同形，并让这条
   事实可以被测试钉住，**不要**拿它读 `wiki_*`。

2. **psycopg2 是可选导入的。** 本仓库的两个解释器能力不同（实测）：系统 python 有 pytest
   没有 psycopg2，`.venv` 有 psycopg2 没有 pytest。词条身份归一、CAS 判定、状态机这些
   **纯逻辑**必须能在没有 psycopg2 的解释器里被 import，否则"优先做不需要数据库的纯逻辑
   测试"这条要求根本跑不起来。数据库路径统一过 `connect()`：缺依赖时抛 `RuntimeError`
   并说明用哪个解释器，而不是抛 `AttributeError` 或悄悄降级。

3. **三套状态维度在代码里没有交叉路径。** `status_dimensions()` 只做并置，入参各自校验；
   发布流程的前置条件只看"发布维度"（`confirmation`、复核记录数），**不看**主张核验状态与
   来源有效性；`aggregate_assertion_status()` 只在**同一个维度内**做聚合。因此
   `published + unverified`、`verified + stale`、`conflicted + published` 都是本实现
   自然产出的合法组合（`WIKI_DOMAIN.md` §5.4 的六条禁令在这里是"没有代码能违反"）。

4. **两处契约与迁移不一致的地方，按"迁移 + 契约"里能同时成立的那一种实现，并在报告里如实
   记录**（详见 `docs/` 之外的本轮交付报告）：
   * `WIKI_API.md` §1 说"写操作按 request_id 幂等"，但 8 张表里**只有**
     `wiki_build_jobs.request_id` 一列（`021:555`）；`wiki_revisions` / `wiki_review_events`
     没有 request_id 列，因此版本、复核、发布三条写路径**无法**持久化幂等键。本模块对
     `request_id` 只做格式校验（必填、≤64、非空白），不复用它做去重 —— 版本与复核的
     安全网是"显式基线 CAS + 追加式审计"，发布重复提交会因为预期旧发布头过期而 409。
   * `WIKI_API.md` §5.12 规则 1 的"与当前头不一致"有两种读法（发布头 / 最新版本）。
     本模块取**更严的那种**：已有版本时基线必须是某个 **tip**（没有子版本的版本），
     否则以发布头兜底（见 `baseline_tips()` 的 WHY）。这样并发编辑的第二个提交者一定拿到
     409，而不是悄悄在旧基线上分叉 —— 注意"最新版本"**不能**按 `created_at` 判定：
     同一事务里 `now()` 是常量，那会让 CAS 随机失效（端到端用例实测到这个缺陷后改成了
     按 `base_revision_id` 的子版本关系判 tip）。

5. **入口校验一律换成稳定的 `WIKI_*` 错误码。** 先例 `sessions.py:64-75`：NUL(0x00) 不是
   合法的 Postgres 文本，psycopg2 在客户端就抛 `ValueError`，绕过所有 `fail()` 冒泡成 500。
   用户可控的文本（id / 标题 / 正文 / 游标）都先过 `_no_nul()`。

6. **jsonb 的"未设置"只有 SQL NULL 一种表示**（`021:69-80`）。本模块写 jsonb 列一律走
   `jsonb_or_null()`：`None`/`''` → SQL NULL，dict → 对象，JSON 字面量 `null` 被拒。
   `IS NOT NULL` 在本 schema 的 jsonb 列上是无歧义的；`conflict_records.selected_fact_ref`
   那个坑（175 行全是 JSON null）属于投影层，本模块不读它。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import uuid

from fastapi import HTTPException

try:  # pragma: no cover - 见模块头 WHY 2：纯逻辑测试的解释器可能没有 psycopg2
    import psycopg2
    from psycopg2.extras import Json, RealDictCursor
except ImportError:  # pragma: no cover
    psycopg2 = None
    Json = None
    RealDictCursor = None

# `datamind.auth` 在模块顶层就 `import psycopg2`（auth.py:12），所以这里**不能**在顶层
# `from .auth import lock_identity`：那会让缺 psycopg2 的解释器连纯逻辑都 import 不了
# （实测：系统 python 有 pytest 没有 psycopg2）。只在数据库路径里延迟导入。

# ---------------------------------------------------------------------------
# 1. 闭集与上限：与 021 的 CHECK、WIKI_DOMAIN §2/§3.2/§5、WIKI_API §5.12 逐字对齐
# ---------------------------------------------------------------------------

ENTRY_TYPES = ('business_entity', 'metric', 'norm', 'conclusion')
PUBLICATION_STATES = ('draft', 'in_review', 'published', 'archived')
ASSERTION_STATES = ('unverified', 'verified', 'insufficient', 'conflicted')
#: 读取时复查的第三个维度（WIKI_API.md §5.3 的取值表；迁移里**不存**这一列，见 021:120）。
SOURCE_VALIDITY_STATES = ('current', 'stale', 'unavailable')
GENERATION_METHODS = ('template', 'human', 'model_polish')
VERIFICATION_METHODS = ('human_review', 'rule_engine')
ENTRY_IDENTITY_CONFIRMATIONS = ('confirmed', 'pending')

#: WIKI_API.md §5.13 的 4 个复核动作；`publish` 是第 5 个（021:497-499），只由 publish() 写。
REVIEW_ACTIONS = ('submit_review', 'approve_facts', 'reject_facts', 'request_changes')
PUBLISH_ACTION = 'publish'

RELATION_TYPES = ('entity_match', 'version_scope', 'unit_equivalent', 'time_overlap', 'supports',
                  'conflicts', 'derived_from', 'retrieved_from', 'maps_to', 'contradicts',
                  'verified_by', 'repaired_from', 'resolved_by', 'measures', 'applies_to',
                  'scoped_by', 'threshold_of', 'about', 'justified_by', 'supersedes', 'restates')
#: WIKI_DOMAIN.md §3.2 的 5 个**无向**关系：存储时按端点 (kind, id) 规范化（021:458）。
UNDIRECTED_RELATION_TYPES = ('entity_match', 'unit_equivalent', 'time_overlap', 'conflicts',
                             'contradicts')
#: #1–#13 projected（来自既有边，保留 raw_type）；#14–#21 wiki_derived（Wiki 推导，无 raw_type）。
PROJECTED_RELATION_TYPES = ('entity_match', 'version_scope', 'unit_equivalent', 'time_overlap',
                            'supports', 'conflicts', 'derived_from', 'retrieved_from', 'maps_to',
                            'contradicts', 'verified_by', 'repaired_from', 'resolved_by')
DERIVED_RELATION_TYPES = ('measures', 'applies_to', 'scoped_by', 'threshold_of', 'about',
                          'justified_by', 'supersedes', 'restates')
RELATION_ORIGINS = ('projected', 'wiki_derived')
DERIVATION_RULE_VERSION = 'wiki-derive-v1'

#: WIKI_API.md §7.2 的五项记账键（021:296-300 的 CHECK 要求它们**同时存在**）。
ACCOUNTING_KEYS = ('source_integrity', 'entity_binding', 'rule_verification', 'semantic_support',
                   'source_authority')
#: agent.py:238 与 :629 的固定字面量。Wiki 不得把它改写成"已核验"（WIKI_API.md §7.2）。
SEMANTIC_ENTAILMENT_NOT_VERIFIED = 'not_independently_verified'

#: WIKI_API.md §5.12 规则 5：正文 ≤ 256 KiB、主张 ≤ 200、关系 ≤ 100（超限 422，不截断）。
BODY_MAX_BYTES = 262144
ASSERTIONS_MAX = 200
RELATIONS_MAX = 100
#: 导出上限（§4）是 300 条证据引用；引用清单沿用同一个数，避免"引用比导出能表达的还多"。
CITATIONS_MAX = 300
#: §5.1 / §5.4 的分页：版本历史 20/50。
REVISIONS_LIMIT_DEFAULT = 20
REVISIONS_LIMIT_MAX = 50
#: §5.9 request_id ≤ 64（与 021:581 的 CHECK 一致）。
REQUEST_ID_MAX = 64
#: 021:140 / :731 的 namespace 与 key_part 列宽（VARCHAR(128) / VARCHAR(512)）。
NAMESPACE_MAX = 128
KEY_PART_MAX = 512
DEFAULT_NAMESPACE = 'default'

#: 空间 id 的派生命名空间。没有 wiki_spaces 表（需求 §5 只冻结 8 张），
#: 所以"一用户一空间"只能是**由 user_id 确定性派生**的（见 space_for_user 的 WHY）。
SPACE_NAMESPACE = 'datamind-wiki-space-v1'

ENTRY_ID_RE = re.compile(r'^wiki-[0-9a-f]{24}$')
REVISION_ID_RE = re.compile(r'^wrev-[0-9a-f]{24}$')
ASSERTION_ID_RE = re.compile(r'^wassert-[0-9a-f]{24}$')
EVIDENCE_REF_ID_RE = re.compile(r'^wev-[0-9a-f]{24}$')
RELATION_ID_RE = re.compile(r'^wrel-[0-9a-f]{24}$')
REVIEW_EVENT_ID_RE = re.compile(r'^wevt-[0-9a-f]{24}$')
SPACE_ID_RE = re.compile(r'^wsp-[0-9a-f]{24}$')
HEX64_RE = re.compile(r'^[a-f0-9]{64}$')
#: 关系端点可以是词条 / 主张 / 证据引用三类（021:447-456）。
ENDPOINT_ID_RE = re.compile(r'^(wiki|wassert|wev)-[0-9a-f]{24}$')

#: 4 个 append-only 触发器的 SQLSTATE → 契约错误码。021:112-114 明说服务层应把它们映射成 409
#: 而不是通用服务器错误。WIKI1/WIKI2/WIKI4 = "必须新建行"（版本冲突语义）；
#: WIKI3 = "同一 request_id 不能改载荷"（幂等冲突语义）。
APPEND_ONLY_SQLSTATE = {
    'WIKI1': 'WIKI_VERSION_CONFLICT',
    'WIKI2': 'WIKI_VERSION_CONFLICT',
    'WIKI3': 'WIKI_IDEMPOTENCY_CONFLICT',
    'WIKI4': 'WIKI_VERSION_CONFLICT',
}
#: 业务键唯一约束的 SQLSTATE：并发创建同一业务键时要归一到同一行（见 create_entry）。
UNIQUE_VIOLATION = '23505'


# ---------------------------------------------------------------------------
# 2. 连接与错误封装
# ---------------------------------------------------------------------------

def write_dsn() -> str:
    """应用角色 DSN（默认值硬编码在模块内，照 `settings_store.py:33` / `agent.py:47-49`）。"""
    return os.environ.get('DATAMIND_META_WRITE_DSN') or (
        'host=127.0.0.1 port=5432 dbname=datamind_meta user=datamind_app password=datamind_app_dev')


def read_dsn() -> str:
    """`readonly_user` DSN。**不要**用它读 `wiki_*`：021:605-607 已把 8 张表从该角色 REVOKE。

    留这个函数只为与本目录其它模块同形，并让"只读角色读不到 Wiki 表"这条事实可被测试。
    """
    return os.environ.get('DATAMIND_META_DSN') or (
        'host=127.0.0.1 port=5432 dbname=datamind_meta user=readonly_user password=readonly_dev')


def wiki_dsn() -> str:
    """Wiki 服务实际使用的 DSN：应用角色是唯一对 8 张表有权限的角色（见模块头 WHY 1）。"""
    return write_dsn()


def connect(dsn):
    """psycopg2 连接。缺依赖时给可执行的提示，绝不静默降级（模块头 WHY 2）。"""
    if psycopg2 is None:  # pragma: no cover - 只有缺 psycopg2 的解释器会走到
        raise RuntimeError(
            'datamind.wiki 的数据库路径需要 psycopg2：当前解释器没有装。纯逻辑函数不需要它；'
            '请用 .venv 解释器（psycopg2 在那里）运行数据库用例。')
    return psycopg2.connect(dsn)


def _jsonb(value):
    """psycopg2 的 Json 适配器；只在数据库路径里用（纯逻辑测试不经过这里）。"""
    if Json is None:  # pragma: no cover
        raise RuntimeError('datamind.wiki 需要 psycopg2 才能写 jsonb 列')
    return Json(value)


def fail(code, status=409, *, message=None, hint=None):
    """稳定错误信封：`{"detail": {"error_code", "message", "hint"}}`（WIKI_API.md §8）。"""
    detail = {'error_code': code}
    if message is not None:
        detail['message'] = message
    if hint is not None:
        detail['hint'] = hint
    raise HTTPException(status, detail=detail)


def error_code_of(exc):
    """取出本模块 `fail()` 写进 HTTPException 的 error_code（翻译既有码时用）。"""
    detail = getattr(exc, 'detail', None)
    return detail.get('error_code') if isinstance(detail, dict) else None


def _no_nul(value, code, status=422):
    """拒绝含 NUL(0x00) 的文本，否则 psycopg2 客户端就抛 ValueError → 500（先例 sessions.py:64）。"""
    if isinstance(value, str) and '\x00' in value:
        fail(code, status, hint='text contains NUL (0x00)')


def _body(payload):
    if not isinstance(payload, dict):
        fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='payload must be a JSON object')
    return payload


def _text(value, field, *, code='WIKI_BUILD_REQUEST_INVALID', max_length=None, required=True):
    """strip → 必填 → 长度，并返回 strip 后的形式（与 sessions._clean_title 同形）。"""
    if value is None:
        if required:
            fail(code, 422, hint='%s is required' % field)
        return None
    if not isinstance(value, str):
        fail(code, 422, hint='%s must be a string' % field)
    _no_nul(value, code)
    text = value.strip()
    if not text:
        fail(code, 422, hint='%s must not be blank' % field)
    if max_length is not None and len(text) > max_length:
        fail(code, 422, hint='%s must be at most %d characters' % (field, max_length))
    return text


# ---------------------------------------------------------------------------
# 3. 纯逻辑：id、指纹、身份归一（WIKI_DOMAIN.md §4）
# ---------------------------------------------------------------------------

def new_id(prefix: str) -> str:
    """`<prefix>-` + uuid4 hex[:24]，与既有的 `sess-`/`approval-` 惯例一致（WIKI_API.md §1）。"""
    return prefix + uuid.uuid4().hex[:24]


def entry_id() -> str:
    return new_id('wiki-')


def revision_id() -> str:
    return new_id('wrev-')


def assertion_id() -> str:
    return new_id('wassert-')


def relation_id() -> str:
    return new_id('wrel-')


def review_event_id() -> str:
    return new_id('wevt-')


def canonical_json(value) -> str:
    """规范 JSON：键排序 + 紧凑分隔符 + 不做 ASCII 转义。

    身份指纹必须与字典插入顺序无关（`comparison_key` 的输入来自调用方拼装），
    所以排序键是**语义要求**而不是格式偏好。
    """
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False)


def sha256_json(value) -> str:
    """规范 JSON 的 sha256，裸小写 hex64（指纹列一律不带 `sha256:` 前缀，021:229-232）。"""
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def sha256_text(text: str) -> str:
    """正文摘要：`wiki_revisions.body_sha256` 是 sha256(body_markdown) 的裸 hex（021:778-779）。"""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def with_sha256_prefix(hex64: str) -> str:
    """接口层的表示：WIKI_API.md §5.3 展示 `sha256:…`，库里存裸 hex。"""
    return 'sha256:' + hex64


def strip_sha256_prefix(value):
    """接受 `sha256:<hex64>` 或裸 hex64，返回裸 hex64；其它返回 None。"""
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if text.startswith('sha256:'):
        text = text[len('sha256:'):]
    return text if HEX64_RE.fullmatch(text) else None


def space_for_user(user_id: str) -> str:
    """`wsp-` + hex24：由 user_id **确定性派生**的空间 id。

    WHY 派生而不是分配：需求 §5 只冻结了 8 张表，里面**没有**空间表（`wiki_entries.space_id`
    只是 VARCHAR + 格式 CHECK，021:160）。若空间 id 是分配型，服务端就没有任何地方能回答
    "当前用户的空间是哪个"，每次读都得先建一张表 —— 那是第 9 张表，越界。
    派生满足第一版的全部语义：一用户一空间（§3.1 脚注 5）、跨空间不归一（§4.1 规则 5）、
    空间 id 不透明且不可枚举（不知道 user_id 就推不出来）。
    """
    if not isinstance(user_id, str) or not user_id.strip():
        fail('SESSION_INVALID', 401, hint='identity has no user_id')
    digest = hashlib.sha256(('%s\x1f%s' % (SPACE_NAMESPACE, user_id)).encode('utf-8')).hexdigest()
    return 'wsp-' + digest[:24]


def user_id_of(identity) -> str:
    """请求者 user_id；没有身份 → 401 SESSION_INVALID（中间件本应先拦掉）。"""
    user_id = (identity or {}).get('user_id')
    if not isinstance(user_id, str) or not user_id.strip():
        fail('SESSION_INVALID', 401, hint='identity has no user_id')
    return user_id


def space_for_identity(identity) -> str:
    return space_for_user(user_id_of(identity))


def key_part_for(*, entry_type, title=None, entity_type=None, canonical_entity_key=None,
                 metric_id=None, source_document_id=None, source_revision=None, clause_key=None,
                 run_id=None, claim_id=None) -> str:
    """`business_key` 的第 4 段（WIKI_DOMAIN.md §4.1 的逐类型精确构成）。

    | entry_type | key_part |
    | --- | --- |
    | business_entity | `entity_type + '\\x1f' + canonical_entity_key`；没有则 `'pending:' + lower(title)` |
    | metric | `metric_id`（来源内规范名，不是中文标签） |
    | norm | `source_document_id + '\\x1f' + source_revision + '\\x1f' + clause_key` |
    | conclusion | `run_id + '\\x1f' + claim_id` |

    `\\x1f`（US, Unit Separator）是契约里写死的分隔符：它在标题里几乎不可能出现，且
    排序/比较都不会与普通空格混淆。`pending:` 前缀的键**不得**被标成 confirmed
    （见 `confirmation_for` 与 021:169 的 CHECK）。
    """
    if entry_type == 'business_entity':
        if canonical_entity_key:
            if not entity_type:
                fail('WIKI_BUILD_REQUEST_INVALID', 422,
                     hint='entity_type is required with a canonical entity key')
            key = '%s\x1f%s' % (_text(entity_type, 'entity_type'),
                                _text(canonical_entity_key, 'canonical_entity_key'))
        else:
            if not title:
                fail('WIKI_BUILD_REQUEST_INVALID', 422,
                     hint='title is required to derive a pending entity key')
            key = 'pending:' + ' '.join(title.split()).lower()
    elif entry_type == 'metric':
        key = _text(metric_id, 'metric_id')
    elif entry_type == 'norm':
        key = '\x1f'.join([_text(source_document_id, 'source_document_id'),
                           _text(source_revision, 'source_revision'),
                           _text(clause_key, 'clause_key')])
    elif entry_type == 'conclusion':
        key = '\x1f'.join([_text(run_id, 'run_id'), _text(claim_id, 'claim_id')])
    else:
        fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='unknown entry_type: %r' % (entry_type,))
    if len(key) > KEY_PART_MAX:
        fail('WIKI_BUILD_REQUEST_INVALID', 422,
             hint='key_part must be at most %d characters' % KEY_PART_MAX)
    return key


def confirmation_for(*, entry_type, key_part, explicit=None) -> str:
    """`confirmation` 的归一（WIKI_DOMAIN.md §4.1 规则 3/4 + 021:169 的 CHECK）。

    * `pending:` 键 → 恒 `pending`，**显式传 confirmed 直接 422**（规则 3：待确认的实体键
      不得被自动升级为确认键，升级只能走 `promote_entity_identity()` 并留下复核记录）；
    * `metric`/`norm`/`conclusion` 的键来自来源内的规范名（`metric_id`、文档 + 版本 + 条款、
      run + claim），不是"名字看着像"→ 默认 `confirmed`，显式 `pending` 可下调；
    * `business_entity` 即使给了 canonical 键也默认 `pending`：人工确认必须是一次显式声明
      （规则 3/4），不能由服务端替用户拍板。
    """
    if key_part.startswith('pending:'):
        if explicit == 'confirmed':
            fail('WIKI_BUILD_REQUEST_INVALID', 422,
                 hint='a pending: entity key cannot be stored as confirmed; '
                      'promote the identity through a human confirmation instead')
        return 'pending'
    if explicit is not None:
        if explicit not in ENTRY_IDENTITY_CONFIRMATIONS:
            fail('WIKI_BUILD_REQUEST_INVALID', 422,
                 hint='confirmation must be one of %s' % (ENTRY_IDENTITY_CONFIRMATIONS,))
        return explicit
    return 'pending' if entry_type == 'business_entity' else 'confirmed'


def comparison_key_of(*, space_id, entity_key, predicate, applicability=None, time_scope=None,
                      record_scope=None, statistic=None) -> str:
    """主张比较身份（WIKI_DOMAIN.md §4.3）——**裸 hex64**，接口层再加 `sha256:` 前缀。

    七个键**始终存在**（未设置写 null），这样 `{predicate}` 与
    `{predicate, statistic: null}` 得到同一个 key：身份比较必须与"调用方有没有写这个键"无关。
    只有同一个 `comparison_key` 下的主张才可能被判为冲突（规则 2：指标相同但时间或口径
    不同连比较组都不是同一个）。
    """
    if not isinstance(predicate, str) or not predicate.strip():
        fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='predicate is required')
    return sha256_json({'space_id': space_id,
                        'entity_key': entity_key if isinstance(entity_key, dict) else {},
                        'predicate': predicate.strip(),
                        'applicability': applicability,
                        'time_scope': time_scope,
                        'record_scope': record_scope,
                        'statistic': statistic})


def normalize_comparison_key(value, *, computed):
    """客户端给的 `comparison_key` 必须与服务端重算的一致（可带 `sha256:` 前缀）。

    比较身份决定"哪些主张会被放在一起权衡"，因此绝不能由客户端指定。
    """
    given = strip_sha256_prefix(value)
    if given is None:
        fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='comparison_key must be sha256:<hex64>')
    if given != computed:
        fail('WIKI_BUILD_REQUEST_INVALID', 422,
             hint='comparison_key does not match the canonical comparison identity')
    return computed


def endpoint_kind(value: str):
    """`(kind, id)`；kind ∈ {entry, assertion, evidence_ref}（021:416-421 的三类端点）。"""
    if not isinstance(value, str) or not ENDPOINT_ID_RE.fullmatch(value):
        fail('WIKI_REFERENCE_INVALID', 422,
             hint='endpoint must be wiki-/wassert-/wev- + hex24')
    prefix = value.split('-', 1)[0]
    return {'wiki': 'entry', 'wassert': 'assertion', 'wev': 'evidence_ref'}[prefix], value


def relation_shape(*, src, dst, relation_type, origin=None, raw_type=None, derivation=None,
                   rule_version=None, basis_note=None, basis=None, provenance_incomplete=None):
    """一条关系的入库形状（021:412-485 的 CHECK 逐条满足）。

    * `origin` 必须与词表类匹配：`#1–#13 projected`（保留 `raw_type`）、
      `#14–#21 wiki_derived`（**没有** raw_type，但必须有 `rule_version` 与
      `derivation.rule_version`，021:477-479）；
    * 无向类型按端点 `(kind, id)` 规范化存储，自环被拒（021:457-460）；
    * 未知类型不走这里：§3.4 的"保留 raw_type + supported=false"是**投影层**的事，
      人工新建版本只允许词表内的类型（否则 422 而不是猜一个最近的类型）。
    """
    src_kind, src_id = endpoint_kind(src)
    dst_kind, dst_id = endpoint_kind(dst)
    if relation_type not in RELATION_TYPES:
        fail('WIKI_BUILD_REQUEST_INVALID', 422,
             hint='relation_type must be one of the frozen 21-word vocabulary')
    expected_origin = 'wiki_derived' if relation_type in DERIVED_RELATION_TYPES else 'projected'
    if origin is not None and origin not in RELATION_ORIGINS:
        fail('WIKI_BUILD_REQUEST_INVALID', 422,
             hint='origin must be one of %s' % (RELATION_ORIGINS,))
    if origin is not None and origin != expected_origin:
        fail('WIKI_BUILD_REQUEST_INVALID', 422,
             hint='relation_type %s belongs to origin=%s' % (relation_type, expected_origin))
    is_symmetric = relation_type in UNDIRECTED_RELATION_TYPES
    if src_kind == dst_kind and src_id == dst_id:
        fail('WIKI_REFERENCE_INVALID', 422, hint='a relation cannot be a self-loop')
    if is_symmetric and (src_kind, src_id) > (dst_kind, dst_id):
        src_kind, src_id, dst_kind, dst_id = dst_kind, dst_id, src_kind, src_id
    row = {'relation_id': relation_id(),
           'src_kind': src_kind, 'src_id': src_id, 'dst_kind': dst_kind, 'dst_id': dst_id,
           'relation_type': relation_type, 'is_symmetric': is_symmetric, 'origin': expected_origin,
           'supported': True, 'unsupported_reason': None, 'raw_type': None,
           'basis_note': None if basis_note is None else _text(basis_note, 'basis_note'),
           'basis': jsonb_or_null(basis, field='basis'),
           'derivation': None, 'rule_version': None,
           'provenance_incomplete': _provenance_incomplete(provenance_incomplete)}
    if expected_origin == 'projected':
        # 原值不可丢（§3.4 规则 1）：人工新建的关系没有既有边，raw_type 就是它自己的词。
        row['raw_type'] = _text(raw_type or relation_type, 'raw_type', max_length=32)
    else:
        if raw_type is not None:
            fail('WIKI_BUILD_REQUEST_INVALID', 422,
                 hint='a wiki_derived relation has no raw edge type')
        row['rule_version'] = _text(rule_version or DERIVATION_RULE_VERSION, 'rule_version',
                                    max_length=64)
        payload = derivation if isinstance(derivation, dict) else {}
        row['derivation'] = {'rule_version': row['rule_version'],
                             'inputs': payload.get('inputs') or []}
    return row


def _provenance_incomplete(value):
    """`provenance_incomplete` 必须是**始终带 applies** 的 JSON 对象（021:483-484）。

    `{"applies": false}` = 元数据完整；`{"applies": true, missing_fields:[…], reason_code:…}`
    = 字段存在过但从未落库（WIKI_API.md §6）。绝不 SQL NULL、绝不 JSON null。
    """
    if value is None:
        return {'applies': False}
    if not isinstance(value, dict) or 'applies' not in value:
        fail('WIKI_BUILD_REQUEST_INVALID', 422,
             hint='provenance_incomplete must be an object carrying applies')
    if not isinstance(value['applies'], bool):
        fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='provenance_incomplete.applies must be boolean')
    return value


def jsonb_or_null(value, *, field, required=False):
    """jsonb 列的"未设置"唯一表示：SQL NULL（021:69-80 的 JSON null 陷阱）。

    dict → 原样（数据库层包 `Json`）；`None`/`''` → SQL NULL；
    JSON 字面量 `null`（字符串 `'null'` 或 Python 的 `None` 被显式要求时）一律拒绝。
    读取侧因此可以用无歧义的 `IS NULL` / `IS NOT NULL` —— 这与
    `conflict_records.selected_fact_ref`（175 行全是 JSON null）的读法**相反**。
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='%s is required' % field)
        return None
    if isinstance(value, str):
        text = value.strip()
        if text == 'null':
            fail('WIKI_BUILD_REQUEST_INVALID', 422,
                 hint='%s must be an object or SQL NULL, never the JSON null literal' % field)
        fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='%s must be a JSON object' % field)
    if not isinstance(value, dict):
        fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='%s must be a JSON object' % field)
    return value


# ---------------------------------------------------------------------------
# 4. 纯逻辑：权限（WIKI_API.md §3，auth.py:17/195/217 的扁平语义）
# ---------------------------------------------------------------------------

def roles_of(identity) -> tuple:
    roles = (identity or {}).get('roles') or []
    if not isinstance(roles, (list, tuple, set, frozenset)):
        return ()
    return tuple(sorted({role for role in roles if isinstance(role, str)}))


def has_role(identity, *wanted) -> bool:
    """扁平角色判定：`admin` 是超管旁路，`reviewer` 与 `approver` **互不继承**。

    判据与 `auth.py:195` / `:217` 逐字同源：`permission not in roles and 'admin' not in roles`
    → 拒绝。这里不做任何"approver 隐含 reviewer"的放宽（WIKI_API.md §3.1 脚注 2 / 需求 §7）。
    """
    roles = roles_of(identity)
    if 'admin' in roles:
        return True
    return any(role in roles for role in wanted)


def is_admin(identity) -> bool:
    return 'admin' in roles_of(identity)


def require_permission(identity, permission):
    """角色门（None = 任意已登录身份）。错误码是 Wiki 的 `WIKI_PERMISSION_DENIED`（§8.2）。"""
    user_id = user_id_of(identity)
    if permission is None:
        return user_id
    if not has_role(identity, permission):
        fail('WIKI_PERMISSION_DENIED', 403,
             hint='permission=%s roles=%s' % (permission, list(roles_of(identity))))
    return user_id


def may_read_restricted(identity, author_id) -> bool:
    """草稿 / 待复核版本的读取门（WIKI_API.md §3.1 脚注 1）。

    作者本人、`reviewer`、`approver`、`admin` 可以读；`viewer` 不能。
    `approver` 可以读草稿是必须的 —— 否则 #14 发布接口不可用；但 `approver` 仍然不能
    **写**草稿（写路径走 `require_permission(…, 'reviewer')`）。
    """
    user_id = (identity or {}).get('user_id')
    if isinstance(user_id, str) and user_id and user_id == author_id:
        return True
    return has_role(identity, 'reviewer', 'approver')


def resolve_space(identity, space_id=None) -> str:
    """请求空间 → 授权空间。别人的空间 = **404**（与不存在不可区分，§3.1 脚注 5）。"""
    own = space_for_identity(identity)
    if space_id is None:
        return own
    if not isinstance(space_id, str) or not SPACE_ID_RE.fullmatch(space_id.strip()):
        fail('WIKI_SPACE_NOT_FOUND', 404)
    space_id = space_id.strip()
    if space_id == own or is_admin(identity):
        return space_id
    fail('WIKI_SPACE_NOT_FOUND', 404)


# ---------------------------------------------------------------------------
# 5. 纯逻辑：CAS、发布前置、三套状态（WIKI_DOMAIN.md §5.4 / WIKI_API.md §7）
# ---------------------------------------------------------------------------

def normalize_head(value):
    """发布头 / 基线的归一：`None`、`''`、`'null'` 都是"没有"；其余必须是 `wrev-` 格式。"""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() == 'null':
            return None
        if REVISION_ID_RE.fullmatch(text):
            return text
    fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='expected a revision id or null')


def cas_passes(expected, actual) -> bool:
    """比较并交换：预期发布头与实际发布头相等才允许前进（§5.14 规则 1）。

    `None == None` 是首版发布的合法形态（`expected_head_revision_id: null`），
    因此"字段被省略"必须由请求模型拒绝，而不是在这里被当成 null（021:504-505 的注释同款）。
    """
    return normalize_head(expected) == normalize_head(actual)


def baseline_tips(*, has_revisions, tip_ids=(), head_revision_id=None) -> set:
    """可以充当新版本基线的版本集合（§5.12 规则 1 的"当前头"）。

    WHY 用"**没有子版本的版本**（DAG 的 tip）"而不是"`created_at` 最大的那一行"：
    `now()` 在同一事务里是常量（PostgreSQL 的 `transaction_timestamp()`），所以同一事务里
    建出的两个版本 `created_at` **完全相同**，再按 `revision_id DESC` 兜底就是按随机 id
    排序 —— "最新版本"变成掷硬币，CAS 会**随机**放过一条过时基线（这是端到端用例实测到的
    缺陷，不是理论担忧）。"没有子版本"是由 `base_revision_id` 唯一确定的，与时间戳无关：

    * 首版（一个版本都还没有）→ 唯一合法基线是**显式 null**；
    * 已经建过版本 → 基线必须是某个 tip：`v1` 一旦有了子版本 `v2`，拿 `v1` 再建一次就是
      过时基线 → 409（这正是"并发编辑的第二个提交者必须重新拉取"）；
    * 允许从同一个 tip 分叉出两个候选版本（两个 tip）—— 谁成为发布头由发布的 CAS 决定。

    `base_revision_id` 的图一定是 DAG（父行必须先存在，外键 + "不能以自己为基线"两条约束），
    所以"有版本 ⇒ 至少一个 tip"，退化分支只是防御。
    """
    tips = {tip for tip in (normalize_head(value) for value in tip_ids or ()) if tip}
    if not has_revisions:
        return {None}
    if not tips and head_revision_id:
        return {normalize_head(head_revision_id)}
    return tips


def base_revision_problem(*, base_revision_id, has_revisions, tip_ids=(), head_revision_id=None):
    """基线不在允许集合里 → `WIKI_VERSION_CONFLICT`；合法 → None。"""
    allowed = baseline_tips(has_revisions=has_revisions, tip_ids=tip_ids,
                            head_revision_id=head_revision_id)
    return None if normalize_head(base_revision_id) in allowed else 'WIKI_VERSION_CONFLICT'


def publication_blockers(*, confirmation=None, review_event_count=0, publication_status=None,
                         revision_present=True):
    """发布的**流程**前置条件（§5.14 规则 3/4，返回 `(code, status, hint)` 列表）。

    刻意只读"发布维度"的输入：实体键确认状态与目标版本的复核记录数。
    **不看**主张核验状态、**不看**来源有效性 —— 允许发布存在 `unresolved` 冲突的说明词条
    （§5.14 规则 5），也允许发布 `assertion='unverified'` 的版本（§5.4 禁令 1：
    `published + unverified` 是合法组合）。
    """
    problems = []
    if not revision_present:
        problems.append(('WIKI_REVISION_NOT_FOUND', 404, 'revision does not belong to this entry'))
    if publication_status == 'archived':
        # 冻结错误码表（§8.2）没有"归档只读"这一条；不发明新码，用 409 版本冲突表达
        # "这次变换不被接受"，并把原因写进 hint（若将来加码，只需改这一处）。
        problems.append(('WIKI_VERSION_CONFLICT', 409, 'archived_entry_is_read_only'))
    if confirmation == 'pending':
        problems.append(('WIKI_ENTRY_IDENTITY_PENDING', 409,
                         'confirm the entity key before publishing'))
    if int(review_event_count or 0) <= 0:
        problems.append(('WIKI_REVIEW_REQUIRED', 409,
                         'the target revision has no review record yet'))
    return problems


def raise_publication_blockers(**kwargs):
    problems = publication_blockers(**kwargs)
    if problems:
        code, status, hint = problems[0]
        fail(code, status, hint=hint)


def status_dimensions(*, publication, assertion=None, source_validity=None) -> dict:
    """三套状态在响应里必须**三个独立字段**（§7.1 / §5.4 的六条禁令）。

    本函数只做并置与取值校验，**没有任何一行代码从一套推出另一套**：
    `published` 不会让 assertion 变 `verified`；`verified` 不会让 source_validity 变
    `current`；`conflicted` 不会让 publication 变回 `draft`。

    `source_validity` 是**读取时复查**的结论（§7.5），本层既不缓存也不臆测：
    未评估就原样返回 `None`（由上层按 §8 的实现纪律显式标注"未评估"），
    **绝不**默认成 `current` —— 那等于伪造"来源可用"这个结论。
    """
    if publication not in PUBLICATION_STATES:
        fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='unknown publication status')
    if assertion is not None and assertion not in ASSERTION_STATES:
        fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='unknown assertion status')
    if source_validity is not None and source_validity not in SOURCE_VALIDITY_STATES:
        fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='unknown source validity status')
    return {'publication': publication, 'assertion': assertion, 'source_validity': source_validity}


def aggregate_assertion_status(statuses):
    """词条对象里的 `status.assertion`：**同一维度内**的聚合，优先级已冻结。

    `conflicted` > `insufficient` > `verified`（仅当该版本每条主张都 verified 且至少一条）
    > `unverified`。没有主张时是 `unverified`（而不是 `verified`：没有主张就没有被核验的事实）。
    这个聚合**只**供 `status.assertion` 使用，绝不用来推导 publication 或 source_validity。
    """
    values = [value for value in (statuses or ()) if value in ASSERTION_STATES]
    if not values:
        return 'unverified'
    if 'conflicted' in values:
        return 'conflicted'
    if 'insufficient' in values:
        return 'insufficient'
    if all(value == 'verified' for value in values):
        return 'verified'
    return 'unverified'


def assertion_content(assertion) -> dict:
    """一条主张的**内容**（不含核验列）：判断相对基线是否变化时只比这些字段。

    与 `wiki_assertions_content_immutable()`（021:638-662）盯住的列**逐一对应** ——
    触发器的列清单就是"什么叫内容变了"的权威定义。
    """
    source = assertion if isinstance(assertion, dict) else {}
    return {'entity_key': source.get('entity_key') or {},
            'predicate': source.get('predicate'),
            'raw_value': source.get('raw_value'),
            'raw_unit': source.get('raw_unit'),
            'normalized_value': source.get('normalized_value'),
            'normalized_unit': source.get('normalized_unit'),
            'time_scope': source.get('time_scope'),
            'applicability': source.get('applicability'),
            'record_scope': source.get('record_scope'),
            'statistic': source.get('statistic'),
            'evidence_refs': sorted(set(source.get('evidence_refs') or ()))}


def assertion_deltas(base_assertions, incoming_assertions):
    """相对基线的变化（§5.12 规则 2：人工调整后**必须重新核验**，不得沿用旧结论）。

    返回 `{'carried': [...], 'changed': [...], 'new': [...], 'removed': [...],
    'revalidation_required': bool}`，元素是 `incoming` 的下标（`carried`/`changed`/`new`）
    或基线主张的 `comparison_key`（`removed`）。

    "内容逐字段相同"是**唯一**允许沿用基线核验结论的情形（`carried`）；
    数值、单位、适用范围、记录口径、引用或范围里任何一项不同 → `changed`/`new`，
    新行一律从 `unverified` 起算。
    """
    incoming = list(incoming_assertions or ())
    base_by_key = {}
    for base in base_assertions or ():
        key = base.get('comparison_key')
        if isinstance(key, str):
            base_by_key[key] = base
    carried, changed, new = [], [], []
    seen = set()
    for index, assertion in enumerate(incoming):
        key = assertion.get('comparison_key')
        seen.add(key)
        base = base_by_key.get(key)
        if base is None:
            new.append(index)
            continue
        if canonical_json(assertion_content(base)) == canonical_json(assertion_content(assertion)):
            carried.append(index)
        else:
            changed.append(index)
    removed = sorted(key for key in base_by_key if key not in seen)
    return {'carried': carried, 'changed': changed, 'new': new, 'removed': removed,
            'revalidation_required': bool(changed or new or removed)}


def verification_gaps(*, basis, scope) -> list:
    """`verdict=verified` 时六项记账缺一不可（§5.13 规则 3 / §5.2）。

    核验对象 = `scope`；方法 = `basis.method`；覆盖范围 = `basis.coverage`；
    规则版本 = `basis.rule_version`；**操作者与时间由服务端写**（`actor.user_id` 与
    `now()`），不接受客户端传入 —— 所以这里只检查前四项里缺的那几项。
    """
    missing = []
    scope = scope if isinstance(scope, dict) else {}
    if not scope.get('full_revision') and not (scope.get('assertion_ids') or []):
        missing.append('scope')
    if basis.get('method') not in VERIFICATION_METHODS:
        missing.append('method')
    if not isinstance(basis.get('coverage'), str) or not basis['coverage'].strip():
        missing.append('coverage')
    if not isinstance(basis.get('rule_version'), str) or not basis['rule_version'].strip():
        missing.append('rule_version')
    return missing


def verification_accounting(*, source_integrity=None, entity_binding=None,
                            rule_verification=None, semantic_support=None,
                            source_authority=None) -> dict:
    """五项分别记账，**不互相替代**（§7.2 / 需求 §6 第 136 行）。

    五个键**始终存在**（021:296-300 的 CHECK 要求 verified 时它们同时在），未提供的项记
    `{'checked': False}` —— "未记"必须看得见，不能省略、不能默认成通过。
    `semantic_support` **恒为** `checked:false` + `not_independently_verified`：
    那个字面量硬编码在 `agent.py:238` / `:629`，Wiki 不得把它改写成"已核验"，
    因此这里**忽略**调用方传进来的 semantic_support，而不是相信它。
    """
    items = {'source_integrity': source_integrity,
             'entity_binding': entity_binding,
             'rule_verification': rule_verification,
             'source_authority': source_authority}
    accounting = {}
    for key in ACCOUNTING_KEYS:
        if key == 'semantic_support':
            accounting[key] = {'checked': False,
                               'full_semantic_entailment': SEMANTIC_ENTAILMENT_NOT_VERIFIED}
            continue
        value = items.get(key)
        if value is None:
            accounting[key] = {'checked': False}
        elif isinstance(value, dict) and isinstance(value.get('checked'), bool):
            accounting[key] = value
        else:
            fail('WIKI_VERIFICATION_SCOPE_REQUIRED', 422,
                 hint='accounting[%s] must be an object carrying a boolean checked' % key)
    return accounting


def review_verdict_status(action, verdict_assertion):
    """`(action, verdict)` → 写入 `wiki_assertions.assertion_status` 的值；`None` = 不改。

    | action | 主张状态 |
    | --- | --- |
    | `submit_review` | 不改（只是把版本推进到 `in_review`，裁决留给后续动作） |
    | `approve_facts` | 按裁决写入范围内主张 |
    | `reject_facts` | 按裁决写入；裁决**不得**是 `verified`（拒绝一条主张不能同时说它已核验） |
    | `request_changes` | 按裁决写入（退回草稿；默认 `unverified`） |

    非法组合 → 422：冻结错误码表没有"动作与裁决矛盾"这一条，用请求载荷错误码表达。
    """
    if verdict_assertion is not None and verdict_assertion not in ASSERTION_STATES:
        fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='unknown verdict assertion status')
    if action == 'submit_review':
        return None
    if action == 'reject_facts':
        if verdict_assertion == 'verified':
            fail('WIKI_BUILD_REQUEST_INVALID', 422,
                 hint='reject_facts cannot carry a verified verdict')
        return verdict_assertion or 'unverified'
    if action in ('approve_facts', 'request_changes'):
        return verdict_assertion or 'unverified'
    fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='unknown review action')


def revision_publication_after_review(current_status, action):
    """复核动作对**版本自己**流程状态的影响（`draft → in_review → published`）。"""
    if current_status == 'published':
        return None  # 已发布版本不因后续复核退回：历史不可改写（§5.5 / §6.2）
    if action == 'submit_review':
        return 'in_review'
    if action == 'request_changes':
        return 'draft'
    return None


def entry_publication_after_review(current_status, head_revision_id, action):
    """复核动作对**词条**流程状态的影响。

    WHY 只在还没有发布头时才动词条状态：`publication_status='published'` 与
    `head_revision_id IS NOT NULL` 是同一个事实的两半（021:171 的 CHECK）。一个**已发布**
    词条的候选版本进入复核，如果把词条拉回 `in_review`，目录里这个已发布的词条会消失，
    而发布头仍然指着已发布版本 —— 那是自相矛盾的展示。候选版本的流程状态记在它自己
    那一行（`wiki_revisions.publication_status`，021:784-785）。
    """
    if head_revision_id:
        return None
    if action == 'submit_review':
        return 'in_review' if current_status != 'archived' else None
    if action == 'request_changes':
        return 'draft' if current_status != 'archived' else None
    return None


def revision_size_guards(*, body_markdown=None, assertion_count=0, relation_count=0,
                         citation_count=0) -> list:
    """§5.12 规则 5 的上限：正文 ≤ 256 KiB、主张 ≤ 200、关系 ≤ 100、引用 ≤ 300。

    超限 → `WIKI_REVISION_TOO_LARGE`，**不截断后声称完整**。
    """
    too_large = []
    if isinstance(body_markdown, str) and len(body_markdown.encode('utf-8')) > BODY_MAX_BYTES:
        too_large.append('body_markdown')
    if assertion_count > ASSERTIONS_MAX:
        too_large.append('assertions')
    if relation_count > RELATIONS_MAX:
        too_large.append('relations')
    if citation_count > CITATIONS_MAX:
        too_large.append('citations')
    return too_large


def append_only_error_code(exc):
    """`WIKI1..WIKI4` → 契约错误码；不是这四个触发器就返回 None（调用方继续抛原异常）。"""
    return APPEND_ONLY_SQLSTATE.get(getattr(exc, 'pgcode', None))


def raise_append_only(exc):
    """把 append-only 触发器的拒绝翻译成 409（021:112-114 要求的映射）。"""
    code = append_only_error_code(exc)
    if code:
        fail(code, 409, message=str(exc),
             hint='the record is append-only: create a new row instead of rewriting it')


def encode_cursor(value: dict) -> str:
    """不透明游标：`base64url(规范JSON)`，客户端不得解析（§4 分页语义 1）。"""
    raw = base64.urlsafe_b64encode(canonical_json(value).encode('utf-8')).decode('ascii')
    return raw.rstrip('=')


def decode_cursor(cursor, *, keys) -> dict:
    """解码并校验游标；任何畸形输入 → 422，绝不 500。"""
    if not isinstance(cursor, str) or not cursor or len(cursor) > 512:
        fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='cursor must be an opaque string')
    padded = cursor + '=' * (-len(cursor) % 4)
    try:
        value = json.loads(base64.urlsafe_b64decode(padded.encode('ascii')).decode('utf-8'))
    except Exception:
        fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='cursor is not decodable')
    if not isinstance(value, dict) or set(value) != set(keys):
        fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='cursor does not belong to this listing')
    return value


# ---------------------------------------------------------------------------
# 6. 数据库路径：行加载与投影
# ---------------------------------------------------------------------------

_ENTRY_COLUMNS = ('entry_id, space_id, owner_id, entry_type, namespace, key_part, confirmation, '
                  'title, aliases, category, summary, publication_status, head_revision_id, '
                  'demo_data, created_at, updated_at, archived_at')

_REVISION_SELECT = '''
    SELECT r.revision_id, r.entry_id, r.base_revision_id, r.title, r.body_markdown, r.body_sha256,
           r.citations, r.author_id, r.generation_method, r.template_version, r.model_id,
           r.change_note, r.publication_status, r.created_at,
           u.display_name AS author_display_name
      FROM wiki_revisions r
      LEFT JOIN auth_users u ON u.user_id = r.author_id
     WHERE r.entry_id = %s AND r.revision_id = %s'''


def _load_entry(cur, entry_id_value, identity, *, for_update=False, space_id=None):
    """按授权范围加载词条；别人的 / 不存在的 / 空间不对的一律 404（存在性隐藏）。"""
    _no_nul(entry_id_value, 'WIKI_ENTRY_NOT_FOUND', 404)
    if not isinstance(entry_id_value, str) or not ENTRY_ID_RE.fullmatch(entry_id_value.strip()):
        fail('WIKI_ENTRY_NOT_FOUND', 404)
    space = resolve_space(identity, space_id)
    sql = ('SELECT ' + _ENTRY_COLUMNS + ' FROM wiki_entries WHERE entry_id = %s '
           'AND (%s OR (space_id = %s AND owner_id = %s))')
    if for_update:
        sql += ' FOR UPDATE'
    cur.execute(sql, (entry_id_value.strip(), is_admin(identity), space, user_id_of(identity)))
    row = cur.fetchone()
    if not row:
        fail('WIKI_ENTRY_NOT_FOUND', 404)
    return row


def _load_revision(cur, entry_id_value, revision_id_value, *, for_update=False):
    _no_nul(revision_id_value, 'WIKI_REVISION_NOT_FOUND', 404)
    if not isinstance(revision_id_value, str) or not REVISION_ID_RE.fullmatch(
            revision_id_value.strip()):
        fail('WIKI_REVISION_NOT_FOUND', 404)
    sql = _REVISION_SELECT
    if for_update:
        sql += ' FOR UPDATE OF r'
    cur.execute(sql, (entry_id_value, revision_id_value.strip()))
    row = cur.fetchone()
    if not row:
        fail('WIKI_REVISION_NOT_FOUND', 404)
    return row


def _tip_revision_ids(cur, entry_id_value):
    """该词条**没有子版本**的那些版本（DAG 的 tip）—— 新版本的合法基线集合。

    与 `created_at` 无关是有意的：同一事务里 `now()` 恒定（见 `baseline_tips` 的 WHY）。
    """
    cur.execute('''SELECT r.revision_id FROM wiki_revisions r
        WHERE r.entry_id = %s
          AND NOT EXISTS (SELECT 1 FROM wiki_revisions c
                          WHERE c.entry_id = r.entry_id AND c.base_revision_id = r.revision_id)
        ORDER BY r.created_at DESC, r.revision_id DESC''', (entry_id_value,))
    return [row['revision_id'] for row in cur.fetchall()]


def _latest_revision_id(cur, entry_id_value):
    """展示用的"最新版本"：tip 优先，其次 `created_at DESC, revision_id DESC`。"""
    tips = _tip_revision_ids(cur, entry_id_value)
    if tips:
        return tips[0]
    cur.execute('''SELECT revision_id FROM wiki_revisions WHERE entry_id = %s
        ORDER BY created_at DESC, revision_id DESC LIMIT 1''', (entry_id_value,))
    row = cur.fetchone()
    return row['revision_id'] if row else None


def _assertion_rows(cur, revision_id_value):
    cur.execute('''SELECT * FROM wiki_assertions WHERE revision_id = %s
        ORDER BY comparison_key, assertion_id''', (revision_id_value,))
    return cur.fetchall()


def _relation_rows(cur, revision_id_value):
    cur.execute('''SELECT * FROM wiki_relations WHERE revision_id = %s
        ORDER BY relation_id''', (revision_id_value,))
    return cur.fetchall()


def _review_event_rows(cur, revision_id_value):
    """某版本的核验记录 —— 读取历史版本时**必须**同时返回（需求 §5.2 / WIKI_DOMAIN §6.2）。"""
    cur.execute('''SELECT * FROM wiki_review_events WHERE revision_id = %s
        ORDER BY occurred_at, review_event_id''', (revision_id_value,))
    return cur.fetchall()


def _review_event_count(cur, revision_id_value):
    """目标版本已有的**复核**动作数（不含 `publish`，发布不是复核）。"""
    cur.execute('''SELECT count(*) AS counted FROM wiki_review_events
        WHERE revision_id = %s AND action = ANY(%s)''',
                (revision_id_value, list(REVIEW_ACTIONS)))
    return int(cur.fetchone()['counted'])


def entry_object(row, *, assertion_status=None, source_validity=None, revision_count=None,
                 evidence_ref_count=None, relation_count=None):
    """§5.1 的 entry 对象（目录、详情、图节点三处共用的同一投影）。"""
    head = row['head_revision_id']
    return {
        'entry_id': row['entry_id'],
        'entry_type': row['entry_type'],
        'title': row['title'],
        'aliases': list(row['aliases'] or []),
        'category': row['category'],
        'business_key': {'space_id': row['space_id'], 'namespace': row['namespace'],
                         'entry_type': row['entry_type'], 'key_part': row['key_part'],
                         'confirmation': row['confirmation']},
        'confirmation': row['confirmation'],
        'summary': row['summary'],
        # 三个独立字段，永不合并（§7.1）。source_validity 只由读取时复查给出；
        # 本层拿不到时是 None（"未评估"），不是 current。
        'status': status_dimensions(publication=row['publication_status'],
                                    assertion=assertion_status,
                                    source_validity=source_validity),
        'head_revision_id': head,
        'revision_count': revision_count,
        'evidence_ref_count': evidence_ref_count,
        'relation_counts': {'visible': relation_count if relation_count is not None else 0,
                            'truncated': False,
                            'scope': 'authorized_loaded'},
        'updated_at': row['updated_at'],
        # 本库全部是合成演示数据，词条**必须**带这个标记（需求 §1 第 20 行）。
        'demo_data': bool(row['demo_data']),
    }


def assertion_object(row):
    """§5.3 的 assertion 对象（比较身份带 `sha256:` 前缀，库里存裸 hex）。"""
    verification = {'status': row['assertion_status'],
                    'checked_at': row['verified_at'],
                    'checked_by': None,
                    'coverage': row['verification_coverage'],
                    'rule_version': row['verification_rule_version'],
                    'accounting': row['verification_accounting']}
    if row['verified_by'] or row['verification_method']:
        verification['checked_by'] = {'user_id': row['verified_by'],
                                      'method': row['verification_method']}
    return {'assertion_id': row['assertion_id'],
            'comparison_key': with_sha256_prefix(row['comparison_key']),
            'entity_key': row['entity_key'],
            'metric': row['predicate'],
            'raw_value': row['raw_value'],
            'raw_unit': row['raw_unit'],
            'normalized_value': row['normalized_value'],
            'normalized_unit': row['normalized_unit'],
            'time_scope': row['time_scope'],
            'applicability': row['applicability'],
            'record_scope': row['record_scope'],
            'statistic': row['statistic'],
            'evidence_refs': list(row['evidence_refs'] or []),
            'verification': verification}


def relation_object(row):
    """§5.2 的关系对象（`label` 是投影层的展示词表，本层不造）。"""
    return {'relation_id': row['relation_id'],
            'src': row['src_id'], 'src_kind': row['src_kind'],
            'dst': row['dst_id'], 'dst_kind': row['dst_kind'],
            'relation_type': row['relation_type'],
            'direction': 'undirected' if row['is_symmetric'] else 'directed',
            'origin': row['origin'],
            'raw_type': row['raw_type'],
            'supported': bool(row['supported']),
            'unsupported_reason': row['unsupported_reason'],
            'rule_version': row['rule_version'],
            'basis_note': row['basis_note'],
            'basis': row['basis'],
            'derivation': row['derivation'],
            'provenance_incomplete': row['provenance_incomplete'],
            'created_at': row['created_at']}


def review_event_object(row):
    return {'review_event_id': row['review_event_id'],
            'action': row['action'],
            'actor': {'user_id': row['actor_user_id']},
            'at': row['occurred_at'],
            'expected_head_revision_id': row['expected_head_revision_id'],
            'based_on_head_revision_id': row['based_on_head_revision_id'],
            'scope': row['scope'],
            'verdict': {'assertion': row['verdict_assertion']},
            'basis': row['basis'],
            'conflicted_assertions': list(row['conflicted_assertions'] or []),
            'note': row['note']}


def revision_object(row, assertions=(), *, include_body=True):
    """§5.3 的 revision 对象。

    历史版本读取用**该版本那一行**的 title/body/citations（021:774-775：词条行上的
    title/summary 只是"当前发布投影"，拿它贴在历史版本上就是用新事实改写历史）。
    """
    citations = row['citations'] if isinstance(row['citations'], list) else []
    return {'revision_id': row['revision_id'],
            'entry_id': row['entry_id'],
            'base_revision_id': row['base_revision_id'],
            'title': row['title'],
            'body_markdown': row['body_markdown'] if include_body else None,
            'body_sha256': row['body_sha256'],
            'citations': citations,
            'assertions': [assertion_object(assertion) for assertion in assertions],
            'author': {'user_id': row['author_id'],
                       'display_name': row.get('author_display_name')},
            'generation': {'method': row['generation_method'],
                           'template_version': row['template_version'],
                           'model_id': row['model_id']},
            'change_note': row['change_note'],
            'publication_status': row['publication_status'],
            'created_at': row['created_at']}


def _entry_counts(cur, row):
    """词条对象的三个计数：版本数、head 版本的证据引用数、head 版本的关系数。

    只统计**当前授权**范围内已加载的连接（§5.1 的 `relation_counts.scope`）：
    这里的 SQL 全部以词条自己的 head 版本为界，隐藏对象不进计数（否则用数字就能推断
    隐藏内容的存在性）。
    """
    cur.execute('SELECT count(*) AS counted FROM wiki_revisions WHERE entry_id = %s',
                (row['entry_id'],))
    revision_count = int(cur.fetchone()['counted'])
    head = row['head_revision_id']
    evidence_ref_count = 0
    relation_count = 0
    if head:
        cur.execute('''SELECT count(DISTINCT citation ->> 'evidence_ref_id') AS counted
            FROM wiki_revisions r, jsonb_array_elements(r.citations) AS citation
            WHERE r.revision_id = %s''', (head,))
        evidence_ref_count = int(cur.fetchone()['counted'])
        cur.execute('SELECT count(*) AS counted FROM wiki_relations WHERE revision_id = %s',
                    (head,))
        relation_count = int(cur.fetchone()['counted'])
    return revision_count, evidence_ref_count, relation_count


def _head_assertion_status(cur, head_revision_id):
    if not head_revision_id:
        return 'unverified'
    cur.execute('SELECT assertion_status FROM wiki_assertions WHERE revision_id = %s',
                (head_revision_id,))
    return aggregate_assertion_status([row['assertion_status'] for row in cur.fetchall()])


def _lock_actor(cur, identity, permission):
    """写事务内的身份复查 + 角色门。

    判据完全交给 `auth.lock_identity`（它锁住 auth_users/auth_sessions，并实现
    `auth.py:195/217` 的扁平角色 + admin 旁路）；本函数只做两件事：
    先用 Wiki 自己的错误码给出稳定结果，再把 `lock_identity` 的既有 `PERMISSION_DENIED`
    翻译成契约要求的 `WIKI_PERMISSION_DENIED`（§8.2），**不改变任何判据**。
    """
    require_permission(identity, permission)
    from .auth import lock_identity  # 延迟导入：auth.py:12 顶层依赖 psycopg2
    try:
        return lock_identity(cur, identity, permission)
    except HTTPException as exc:
        if error_code_of(exc) == 'PERMISSION_DENIED':
            fail('WIKI_PERMISSION_DENIED', 403,
                 hint='permission=%s (rechecked inside the write transaction)' % permission)
        raise


# ---------------------------------------------------------------------------
# 7. 数据库路径：词条创建与身份归一
# ---------------------------------------------------------------------------

def _select_entry_by_business_key(cur, space_id_value, namespace, entry_type, key_part):
    cur.execute('SELECT ' + _ENTRY_COLUMNS + ''' FROM wiki_entries
        WHERE space_id = %s AND namespace = %s AND entry_type = %s AND key_part = %s''',
                (space_id_value, namespace, entry_type, key_part))
    return cur.fetchone()


def entity_identity(entry_type, body, *, title=None, confirmation=None) -> dict:
    """从请求载荷归一出 `(key_part, confirmation)`（纯逻辑，不碰数据库）。

    载荷字段与 §4.1 的构成逐项对应：`entity_type`/`canonical_entity_key`、
    `metric_id`、`source_document_id`/`source_revision`/`clause_key`、`run_id`/`claim_id`。
    """
    key_part = key_part_for(entry_type=entry_type, title=title,
                            entity_type=body.get('entity_type'),
                            canonical_entity_key=body.get('canonical_entity_key'),
                            metric_id=body.get('metric_id'),
                            source_document_id=body.get('source_document_id'),
                            source_revision=body.get('source_revision'),
                            clause_key=body.get('clause_key'),
                            run_id=body.get('run_id'), claim_id=body.get('claim_id'))
    return {'key_part': key_part,
            'confirmation': confirmation_for(entry_type=entry_type, key_part=key_part,
                                             explicit=confirmation)}


def _entry_type(value):
    if value not in ENTRY_TYPES:
        fail('WIKI_BUILD_REQUEST_INVALID', 422,
             hint='entry_type must be one of %s' % (ENTRY_TYPES,))
    return value


def _aliases(value):
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='aliases must be an array')
    aliases = []
    for item in value:
        alias = _text(item, 'aliases[]')
        if alias not in aliases:
            aliases.append(alias)
    return aliases


def _category(value):
    if value is None:
        return None
    return _text(value, 'category', max_length=64)


def _summary(value):
    if value is None:
        return ''
    if not isinstance(value, str):
        fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='summary must be a string')
    _no_nul(value, 'WIKI_BUILD_REQUEST_INVALID')
    return value.strip()


def create_entry(payload, dsn, identity):
    """创建词条，身份按 `WIKI_DOMAIN.md` §4.1 归一。

    返回 `{'entry', 'created', 'identity_source'}`：

    * **归一**：同一 `business_key`（space | namespace | entry_type | key_part）在空间内唯一
      （021:158），若已存在就返回**那一行**（`created=False`），不新建第二个 `entry_id`；
    * 并发创建同一业务键由 `ON CONFLICT (…) DO NOTHING` + 回查处理 —— 唯一约束是权威，
      Python 侧的先查后插只是快路径；
    * `pending:` 键的 `confirmation` 恒为 `pending`，**不阻塞草稿**，但**阻止发布**
      （§4.1 规则 4 / `publication_blockers`）。
    """
    body = _body(payload)
    entry_type = _entry_type(body.get('entry_type'))
    title = _text(body.get('title'), 'title')
    namespace = _text(body.get('namespace') or DEFAULT_NAMESPACE, 'namespace',
                      max_length=NAMESPACE_MAX)
    space_id_value = resolve_space(identity, body.get('space_id'))
    identity_value = entity_identity(entry_type, body, title=title,
                                     confirmation=body.get('confirmation'))
    aliases = _aliases(body.get('aliases'))
    category = _category(body.get('category'))
    summary = _summary(body.get('summary'))
    with connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        _lock_actor(cur, identity, 'reviewer')
        row = _select_entry_by_business_key(cur, space_id_value, namespace, entry_type,
                                            identity_value['key_part'])
        created = False
        if row is None:
            cur.execute('''INSERT INTO wiki_entries(entry_id, space_id, owner_id, entry_type,
                    namespace, key_part, confirmation, title, aliases, category, summary)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (space_id, namespace, entry_type, key_part) DO NOTHING
                RETURNING ''' + _ENTRY_COLUMNS,
                        (entry_id(), space_id_value, user_id_of(identity), entry_type, namespace,
                         identity_value['key_part'], identity_value['confirmation'], title,
                         aliases, category, summary))
            row = cur.fetchone()
            created = row is not None
            if row is None:
                row = _select_entry_by_business_key(cur, space_id_value, namespace, entry_type,
                                                    identity_value['key_part'])
                if row is None:  # pragma: no cover - 唯一约束失败却查不到行，只能是并发删除
                    fail('WIKI_VERSION_CONFLICT', 409, hint='entry identity raced with a delete')
    return {'entry': entry_object(row, assertion_status='unverified'), 'created': created,
            'identity_source': {'key_part': identity_value['key_part'],
                                'confirmation': identity_value['confirmation'],
                                'derived_from': 'canonical_key'
                                if not identity_value['key_part'].startswith('pending:')
                                else 'pending_title'}}


def approved_alias(cur, alias, entity_type=None):
    """已批准别名 → 规范键（`entity_aliases` 只读；Wiki **不写**这张表，§7.3）。

    返回 `{'canonical_id', 'canonical_name', 'entity_type'}` 或 None。
    有效期按 `valid_from`/`valid_to`（`date`，可空）判定。
    """
    cur.execute('''SELECT canonical_id, canonical_name, entity_type FROM entity_aliases
        WHERE approved AND alias = %s
          AND (%s IS NULL OR entity_type = %s)
          AND (valid_from IS NULL OR valid_from <= current_date)
          AND (valid_to IS NULL OR valid_to >= current_date)
        ORDER BY canonical_id LIMIT 1''', (alias, entity_type, entity_type))
    return cur.fetchone()


def promote_entity_identity(entry_id_value, payload, dsn, identity):
    """把 `pending:` 实体键升级为确认键 —— **唯一**允许改 `business_key` 的路径。

    `WIKI_DOMAIN.md` §4.1 规则 3：升级只发生在**人工确认**或**引用已批准别名**时，
    且**必须留下 `wiki_review_events` 记录**。`wiki_review_events` 的外键是
    `(entry_id, revision_id) → wiki_revisions`（021:520-521），所以升级必须落在某一个
    已存在版本上：载荷里的 `revision_id` 就是"这次确认是针对哪个版本的"。
    记录用冻结动作集里的 `approve_facts`（动作词表是闭集，没有 confirm_identity），
    `verdict_assertion` 留空 —— 这是一次身份确认，不是对某条主张的核验裁决。
    """
    body = _body(payload)
    revision_id_value = _text(body.get('revision_id'), 'revision_id')
    method = body.get('method') or 'human_review'
    if method not in ('human_review', 'approved_alias'):
        fail('WIKI_BUILD_REQUEST_INVALID', 422,
             hint='method must be human_review or approved_alias')
    with connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        actor = _lock_actor(cur, identity, 'reviewer')
        entry = _load_entry(cur, entry_id_value, identity, for_update=True)
        revision = _load_revision(cur, entry['entry_id'], revision_id_value)
        canonical = None
        if method == 'approved_alias':
            alias = _text(body.get('alias'), 'alias')
            canonical = approved_alias(cur, alias, body.get('entity_type') or None)
            if canonical is None:
                fail('WIKI_REFERENCE_INVALID', 422,
                     hint='no approved alias registration matches: %s' % alias)
            canonical_key = canonical['canonical_id'] or canonical['canonical_name']
            entity_type = canonical['entity_type']
        else:
            canonical_key = _text(body.get('canonical_entity_key'), 'canonical_entity_key')
            entity_type = _text(body.get('entity_type'), 'entity_type')
        new_key_part = key_part_for(entry_type='business_entity', entity_type=entity_type,
                                    canonical_entity_key=canonical_key)
        cur.execute('''SELECT entry_id FROM wiki_entries
            WHERE space_id = %s AND namespace = %s AND entry_type = 'business_entity'
              AND key_part = %s AND entry_id <> %s''',
                    (entry['space_id'], entry['namespace'], new_key_part, entry['entry_id']))
        if cur.fetchone():
            fail('WIKI_VERSION_CONFLICT', 409,
                 hint='another entry already owns the confirmed business key')
        cur.execute('''UPDATE wiki_entries SET key_part = %s, confirmation = 'confirmed',
                updated_at = now() WHERE entry_id = %s RETURNING ''' + _ENTRY_COLUMNS,
                    (new_key_part, entry['entry_id']))
        updated = cur.fetchone()
        event = _insert_review_event(
            cur, entry_id_value=entry['entry_id'], revision_id_value=revision['revision_id'],
            action='approve_facts', actor_id=actor['user_id'],
            expected_head=entry['head_revision_id'], based_on=entry['head_revision_id'],
            scope={'entry_identity': True, 'previous_key_part': entry['key_part'],
                   'key_part': new_key_part, 'method': method,
                   'alias_registration': canonical['canonical_id'] if canonical else None},
            verdict=None,
            basis={'rule_version': 'wiki-entry-identity-v1', 'method': 'human_review',
                   'coverage': 'business_key of %s' % entry['entry_id'],
                   'evidence_ref_ids': []},
            note=_text(body.get('note'), 'note', required=False))
    return {'entry': entry_object(updated, assertion_status='unverified'),
            'review_event': review_event_object(event),
            'previous_key_part': entry['key_part']}


def _insert_review_event(cur, *, entry_id_value, revision_id_value, action, actor_id,
                         expected_head, based_on, scope, verdict, basis, note=None,
                         conflicted_assertions=()):
    """追加一条复核 / 发布记录。**只 INSERT**：WIKI2 触发器连 UPDATE/DELETE 都拒绝。"""
    cur.execute('''INSERT INTO wiki_review_events(review_event_id, entry_id, revision_id, action,
            actor_user_id, expected_head_revision_id, based_on_head_revision_id, scope,
            verdict_assertion, basis, conflicted_assertions, note)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
                (review_event_id(), entry_id_value, revision_id_value, action, actor_id,
                 normalize_head(expected_head), normalize_head(based_on),
                 _jsonb(jsonb_or_null(scope, field='scope') or {}), verdict,
                 _jsonb(jsonb_or_null(basis, field='basis') or {}),
                 list(conflicted_assertions), note))
    return cur.fetchone()


# ---------------------------------------------------------------------------
# 8. 数据库路径：版本读取（历史版本必须同时带关系与核验记录）
# ---------------------------------------------------------------------------

def revision_detail(entry_id_value, revision_id_value, dsn, identity, *,
                    expected_body_sha256=None, expected_base_revision_id=None,
                    provenance_summary=None):
    """固定版本详情（§5.5）。

    * 返回**该版本自己记录的**状态快照，不返回词条当前的 `status`（§5.5：把当前状态贴在
      历史版本上就是"用新事实改写历史读取"）；
    * `expected_body_sha256` / `expected_base_revision_id` 与仓库既有的固定版本校验同形，
      不一致 → 409 `WIKI_VERSION_CONFLICT`；
    * **同时**返回该版本的关系与核验记录（需求 §5.2 / `WIKI_DOMAIN` §6.2）。
    """
    _no_nul(entry_id_value, 'WIKI_ENTRY_NOT_FOUND', 404)
    with connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        entry = _load_entry(cur, entry_id_value, identity)
        revision = _load_revision(cur, entry['entry_id'], revision_id_value)
        if revision['publication_status'] in ('draft', 'in_review') and not may_read_restricted(
                identity, revision['author_id']):
            # §3.1 脚注 1：无编辑权限者读草稿 → 与不存在**完全相同**的 404。
            fail('WIKI_ENTRY_NOT_FOUND', 404)
        expected_body = strip_sha256_prefix(expected_body_sha256)
        if expected_body_sha256 is not None:
            if expected_body is None:
                fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='expected_body_sha256 must be hex64')
            if expected_body != revision['body_sha256']:
                fail('WIKI_VERSION_CONFLICT', 409,
                     hint='body_sha256 does not match this revision')
        if expected_base_revision_id is not None:
            if normalize_head(expected_base_revision_id) != revision['base_revision_id']:
                fail('WIKI_VERSION_CONFLICT', 409,
                     hint='base_revision_id does not match this revision')
        assertions = _assertion_rows(cur, revision['revision_id'])
        relations = _relation_rows(cur, revision['revision_id'])
        events = _review_event_rows(cur, revision['revision_id'])
        head_assertion_status = _head_assertion_status(cur, entry['head_revision_id'])
        revision_count, evidence_ref_count, relation_count = _entry_counts(cur, entry)
    return {'entry': entry_object(entry, source_validity=None,
                                  assertion_status=head_assertion_status,
                                  revision_count=revision_count,
                                  evidence_ref_count=evidence_ref_count,
                                  relation_count=relation_count),
            'revision': revision_object(revision, assertions),
            'relations': [relation_object(row) for row in relations],
            'review_events': [review_event_object(row) for row in events],
            'provenance_summary': provenance_summary}


def entry_detail(entry_id_value, dsn, identity, *, revision_id_value=None,
                 provenance_summary=None):
    """当前版本 / 指定版本（§5.3）。未指定时优先发布头，其次最新版本。

    草稿 / 待复核版本需要编辑权限（§3.1 脚注 1）；无权限 → 404 `WIKI_ENTRY_NOT_FOUND`
    （与不存在字节一致，不泄露存在性）。
    """
    _no_nul(entry_id_value, 'WIKI_ENTRY_NOT_FOUND', 404)
    with connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        entry = _load_entry(cur, entry_id_value, identity)
        target = None
        if revision_id_value is not None:
            target = _text(revision_id_value, 'revision_id')
        elif entry['head_revision_id']:
            target = entry['head_revision_id']
        else:
            target = _latest_revision_id(cur, entry['entry_id'])
        if target is None:
            revision_count, evidence_ref_count, relation_count = _entry_counts(cur, entry)
            return {'entry': entry_object(entry, assertion_status='unverified',
                                          revision_count=revision_count,
                                          evidence_ref_count=evidence_ref_count,
                                          relation_count=relation_count),
                    'revision': None, 'relations': [], 'review_events': [],
                    'provenance_summary': provenance_summary}
        revision = _load_revision(cur, entry['entry_id'], target)
        if revision['publication_status'] in ('draft', 'in_review') and not may_read_restricted(
                identity, revision['author_id']):
            fail('WIKI_ENTRY_NOT_FOUND', 404)
        assertions = _assertion_rows(cur, revision['revision_id'])
        relations = _relation_rows(cur, revision['revision_id'])
        events = _review_event_rows(cur, revision['revision_id'])
        revision_count, evidence_ref_count, relation_count = _entry_counts(cur, entry)
        head_assertion_status = _head_assertion_status(cur, entry['head_revision_id'])
    return {'entry': entry_object(entry, assertion_status=head_assertion_status,
                                  revision_count=revision_count,
                                  evidence_ref_count=evidence_ref_count,
                                  relation_count=relation_count),
            'revision': revision_object(revision, assertions),
            'relations': [relation_object(row) for row in relations],
            'review_events': [review_event_object(row) for row in events],
            'provenance_summary': provenance_summary}


def revision_history(entry_id_value, dsn, identity, *, limit=REVISIONS_LIMIT_DEFAULT, cursor=None):
    """可见版本历史（§5.4）：每条一个**摘要**（不含正文），带 `body_sha256` 与事件摘要。

    排序 `created_at DESC, revision_id DESC`，游标是不透明的 `(created_at, revision_id)`；
    唯一兜底键是 `revision_id`，否则同一时间戳的窗口会翻出重复行（§4 分页语义 2）。
    """
    if type(limit) is not int or not 1 <= limit <= REVISIONS_LIMIT_MAX:
        fail('WIKI_BUILD_REQUEST_INVALID', 422,
             hint='limit must be between 1 and %d' % REVISIONS_LIMIT_MAX)
    _no_nul(entry_id_value, 'WIKI_ENTRY_NOT_FOUND', 404)
    keys = ('created_at', 'revision_id')
    marker = decode_cursor(cursor, keys=keys) if cursor else None
    with connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        entry = _load_entry(cur, entry_id_value, identity)
        sql = ('''SELECT r.revision_id, r.entry_id, r.base_revision_id, r.title, r.body_sha256,
                r.citations, r.author_id, r.generation_method, r.publication_status, r.created_at,
                u.display_name AS author_display_name,
                (SELECT count(*) FROM wiki_assertions a WHERE a.revision_id = r.revision_id)
                    AS assertion_count
              FROM wiki_revisions r
              LEFT JOIN auth_users u ON u.user_id = r.author_id
             WHERE r.entry_id = %s''')
        params = [entry['entry_id']]
        if marker:
            sql += ' AND (r.created_at, r.revision_id) < (%s::timestamptz, %s)'
            params.extend([marker['created_at'], marker['revision_id']])
        sql += ' ORDER BY r.created_at DESC, r.revision_id DESC LIMIT %s'
        params.append(limit + 1)
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
        window = rows[:limit]
        visible = []
        for row in window:
            if row['publication_status'] in ('draft', 'in_review') and not may_read_restricted(
                    identity, row['author_id']):
                continue  # 无编辑权限者看不到草稿行（§3.1 脚注 1）
            events = _review_event_rows(cur, row['revision_id'])
            citations = row['citations'] if isinstance(row['citations'], list) else []
            visible.append({'revision_id': row['revision_id'],
                            'base_revision_id': row['base_revision_id'],
                            'status': row['publication_status'],
                            'title': row['title'],
                            'author': {'user_id': row['author_id'],
                                       'display_name': row['author_display_name']},
                            'generation_method': row['generation_method'],
                            'citation_count': len(citations),
                            'assertion_count': int(row['assertion_count'] or 0),
                            'body_sha256': row['body_sha256'],
                            'created_at': row['created_at'],
                            'events': [{'event': event['action'], 'at': event['occurred_at'],
                                        'actor': event['actor_user_id']} for event in events]})
    has_more = len(rows) > limit
    # 游标永远指向**本页最后一行数据库行**（不是最后一行可见行）：这样被权限过滤掉的行
    # 不会让下一页原地重来（§4 分页语义 4 的"服务端视为新集合"是图层/筛选变化才重置）。
    next_cursor = None
    if has_more and window:
        last = window[-1]
        next_cursor = encode_cursor({'created_at': last['created_at'].isoformat(),
                                     'revision_id': last['revision_id']})
    return {'entry_id': entry['entry_id'], 'revisions': visible, 'limit': limit,
            'has_more': has_more, 'next_cursor': next_cursor, 'truncated': False}


# ---------------------------------------------------------------------------
# 9. 数据库路径：以基线创建新版本（不可变 + 重新核验）
# ---------------------------------------------------------------------------

def _assertion_payloads(body, *, space_id_value):
    """校验并归一载荷里的 `assertions`（§5.12）：比较身份由服务端重算，引用必须可达。"""
    raw = body.get('assertions') or []
    if not isinstance(raw, list):
        fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='assertions must be an array')
    rows = []
    for item in raw:
        if not isinstance(item, dict):
            fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='each assertion must be an object')
        predicate = _text(item.get('metric') or item.get('predicate'), 'assertion.metric')
        entity_key = jsonb_or_null(item.get('entity_key'), field='entity_key', required=True)
        time_scope = jsonb_or_null(item.get('time_scope'), field='time_scope')
        applicability = jsonb_or_null(item.get('applicability'), field='applicability')
        record_scope = jsonb_or_null(item.get('record_scope'), field='record_scope')
        statistic = _text(item.get('statistic'), 'assertion.statistic', required=False)
        computed = comparison_key_of(space_id=space_id_value, entity_key=entity_key,
                                     predicate=predicate, applicability=applicability,
                                     time_scope=time_scope, record_scope=record_scope,
                                     statistic=statistic)
        if item.get('comparison_key') is not None:
            normalize_comparison_key(item.get('comparison_key'), computed=computed)
        evidence_refs = []
        for reference in item.get('evidence_refs') or []:
            kind, value = endpoint_kind(reference)
            if kind != 'evidence_ref':
                fail('WIKI_REFERENCE_INVALID', 422,
                     hint='assertion.evidence_refs must be wev- ids')
            if value not in evidence_refs:
                evidence_refs.append(value)
        given_id = item.get('assertion_id')
        if given_id is not None:
            given_id = _text(given_id, 'assertion_id')
            if not ASSERTION_ID_RE.fullmatch(given_id):
                fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='assertion_id must be wassert- + hex24')
        row = {'assertion_id': given_id or assertion_id(),
               'comparison_key': computed,
               'entity_key': entity_key,
               'predicate': predicate,
               'raw_value': _text(item.get('raw_value'), 'raw_value', required=False),
               'raw_unit': _text(item.get('raw_unit'), 'raw_unit', required=False),
               'normalized_value': _text(item.get('normalized_value'), 'normalized_value',
                                         required=False),
               'normalized_unit': _text(item.get('normalized_unit'), 'normalized_unit',
                                        required=False),
               'time_scope': time_scope, 'applicability': applicability,
               'record_scope': record_scope, 'statistic': statistic,
               'evidence_refs': evidence_refs}
        if row['raw_unit'] is not None and row['raw_value'] is None:
            fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='a raw unit needs a raw value')
        if row['normalized_unit'] is not None and row['normalized_value'] is None:
            fail('WIKI_BUILD_REQUEST_INVALID', 422,
                 hint='a normalized unit needs a normalized value')
        # `content` 必须用**归一后**的值构造，否则与基线行比较时会出现
        # "值相同但一个是 ' 870 '、一个是 '870'" 这种假变化（或反过来漏判变化）。
        row['content'] = assertion_content(row)
        rows.append(row)
    return rows


def _citation_payloads(body):
    raw = body.get('citations') or []
    if not isinstance(raw, list):
        fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='citations must be an array')
    citations, markers, refs = [], set(), []
    for item in raw:
        if not isinstance(item, dict):
            fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='each citation must be an object')
        marker = _text(item.get('marker'), 'citation.marker', max_length=32)
        reference = _text(item.get('evidence_ref_id'), 'citation.evidence_ref_id')
        kind, value = endpoint_kind(reference)
        if kind != 'evidence_ref':
            fail('WIKI_REFERENCE_INVALID', 422, hint='citation.evidence_ref_id must be a wev- id')
        if marker in markers:
            fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='duplicate citation marker: %s' % marker)
        markers.add(marker)
        citations.append({'marker': marker, 'evidence_ref_id': value})
        refs.append(value)
    return citations, refs


def _validate_evidence_refs(cur, space_id_value, ref_ids):
    """引用的证据引用必须存在**且在本空间内**（§5.12 规则 4：不可达 → 422）。

    "不存在"与"属于别人的空间"返回**同一个** 422：`WIKI_API.md` §8.2 把
    `WIKI_REFERENCE_INVALID` 定义为"引用的证据引用不存在**或已被过滤**"，而 403
    `WIKI_SOURCE_FORBIDDEN` 留给"来源本身不可访问"（来源有效性属读取时复查，见 §7.5）。
    """
    wanted = sorted({ref for ref in ref_ids if ref})
    if not wanted:
        return
    cur.execute('''SELECT evidence_ref_id FROM wiki_evidence_refs
        WHERE space_id = %s AND evidence_ref_id = ANY(%s)''', (space_id_value, wanted))
    found = {row['evidence_ref_id'] for row in cur.fetchall()}
    missing = [ref for ref in wanted if ref not in found]
    if missing:
        fail('WIKI_REFERENCE_INVALID', 422,
             hint='unreachable evidence references: %s' % ', '.join(missing[:5]))


def _relation_payloads(body):
    raw = body.get('relations') or []
    if not isinstance(raw, list):
        fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='relations must be an array')
    rows = []
    for item in raw:
        if not isinstance(item, dict):
            fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='each relation must be an object')
        row = relation_shape(src=item.get('src'), dst=item.get('dst'),
                             relation_type=item.get('relation_type'),
                             origin=item.get('origin'), raw_type=item.get('raw_type'),
                             derivation=item.get('derivation'),
                             rule_version=item.get('rule_version'),
                             basis_note=item.get('basis_note'), basis=item.get('basis'),
                             provenance_incomplete=item.get('provenance_incomplete'))
        rows.append(row)
    seen = set()
    unique = []
    for row in rows:
        identity_key = (row['src_kind'], row['src_id'], row['dst_kind'], row['dst_id'],
                        row['origin'], row['relation_type'] or '', row['raw_type'] or '')
        if identity_key in seen:
            continue
        seen.add(identity_key)
        unique.append(row)
    return unique


def _validate_relation_endpoints(cur, entry, rows):
    """关系两端必须在本词条可达范围内（021:407-411：多态端点不能建外键，服务层检查）。

    不可达 → 404 `WIKI_ENTRY_NOT_FOUND`（与不存在不可区分），避免用关系端点当探针。
    """
    entry_ids, assertion_ids, ref_ids = set(), set(), set()
    for row in rows:
        for kind, value in ((row['src_kind'], row['src_id']), (row['dst_kind'], row['dst_id'])):
            {'entry': entry_ids, 'assertion': assertion_ids,
             'evidence_ref': ref_ids}[kind].add(value)
    if entry_ids:
        cur.execute('''SELECT entry_id FROM wiki_entries
            WHERE space_id = %s AND entry_id = ANY(%s)''', (entry['space_id'], sorted(entry_ids)))
        found = {row['entry_id'] for row in cur.fetchall()}
        if found != entry_ids:
            fail('WIKI_ENTRY_NOT_FOUND', 404)
    if assertion_ids:
        cur.execute('''SELECT assertion_id FROM wiki_assertions
            WHERE entry_id = %s AND assertion_id = ANY(%s)''',
                    (entry['entry_id'], sorted(assertion_ids)))
        if {row['assertion_id'] for row in cur.fetchall()} != assertion_ids:
            fail('WIKI_REFERENCE_INVALID', 422, hint='relation endpoint assertion is unreachable')
    if ref_ids:
        _validate_evidence_refs(cur, entry['space_id'], sorted(ref_ids))


def create_revision(entry_id_value, payload, dsn, identity):
    """以**显式基线**创建新版本（§5.12）。

    必守规则逐条落地：
    1. `base_revision_id` **必须出现在载荷里**（首版传 null），与 tip/发布头不一致 → 409
       `WIKI_VERSION_CONFLICT`（`base_revision_problem()`）；
    2. 相对基线**内容有变**的主张一律从 `unverified` 起算，**不得**沿用基线核验结论；
       只有逐字段相同的主张才带过核验列（`assertion_deltas()` 的 `carried`），
       响应里 `revalidation_required` 如实置位；
    3. 已保存版本只被**新增**：本函数从不 UPDATE 正文/引用/基线（WIKI1 触发器兜底）；
    4. 引用不可达 → 422 `WIKI_REFERENCE_INVALID`；
    5. 正文 ≤ 256 KiB / 主张 ≤ 200 / 关系 ≤ 100 → 超限 422 `WIKI_REVISION_TOO_LARGE`。
    """
    body = _body(payload)
    _request_id_of(body)
    if 'base_revision_id' not in body:
        fail('WIKI_BUILD_REQUEST_INVALID', 422,
             hint='base_revision_id is required; pass null for the first version')
    base = normalize_head(body.get('base_revision_id'))
    title = _text(body.get('title'), 'title')
    body_markdown = body.get('body_markdown')
    if not isinstance(body_markdown, str):
        fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='body_markdown must be a string')
    _no_nul(body_markdown, 'WIKI_BUILD_REQUEST_INVALID')
    generation_method = body.get('generation_method') or 'human'
    if generation_method not in GENERATION_METHODS:
        fail('WIKI_BUILD_REQUEST_INVALID', 422,
             hint='generation_method must be one of %s' % (GENERATION_METHODS,))
    template_version = _text(body.get('template_version'), 'template_version', max_length=64,
                             required=False)
    model_id = _text(body.get('model_id'), 'model_id', max_length=128, required=False)
    if generation_method == 'template' and template_version is None:
        fail('WIKI_BUILD_REQUEST_INVALID', 422,
             hint='template generation requires template_version')  # 021:214
    if model_id is not None and generation_method != 'model_polish':
        fail('WIKI_BUILD_REQUEST_INVALID', 422,
             hint='model_id is only allowed for model_polish')  # 021:215-216
    change_note = _text(body.get('change_note'), 'change_note', required=False)
    citations, citation_refs = _citation_payloads(body)
    relation_payloads = _relation_payloads(body)
    too_large = revision_size_guards(body_markdown=body_markdown,
                                     assertion_count=len(body.get('assertions') or []),
                                     relation_count=len(body.get('relations') or []),
                                     citation_count=len(citations))
    if too_large:
        fail('WIKI_REVISION_TOO_LARGE', 422,
             hint='over the frozen limits: %s' % ', '.join(too_large))

    with connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        actor = _lock_actor(cur, identity, 'reviewer')
        entry = _load_entry(cur, entry_id_value, identity, for_update=True)
        tips = _tip_revision_ids(cur, entry['entry_id'])
        problem = base_revision_problem(base_revision_id=base, has_revisions=bool(tips),
                                        tip_ids=tips,
                                        head_revision_id=entry['head_revision_id'])
        if problem:
            fail(problem, 409,
                 hint='stale baseline: the current tip is %s'
                      % (tips[0] if tips else entry['head_revision_id']))
        if base is not None:
            _load_revision(cur, entry['entry_id'], base)  # 同一词条（复合外键语义），否则 404

        assertion_payloads = _assertion_payloads(body, space_id_value=entry['space_id'])
        all_refs = list(citation_refs)
        for assertion in assertion_payloads:
            all_refs.extend(assertion['evidence_refs'])
        _validate_evidence_refs(cur, entry['space_id'], all_refs)
        _validate_relation_endpoints(cur, entry, relation_payloads)
        given_assertion_ids = [row['assertion_id'] for row in assertion_payloads]
        if given_assertion_ids:
            cur.execute('SELECT assertion_id FROM wiki_assertions WHERE assertion_id = ANY(%s)',
                        (given_assertion_ids,))
            if cur.fetchall():
                fail('WIKI_REFERENCE_INVALID', 422, hint='assertion_id already exists')

        base_assertions = []
        if base is not None:
            cur.execute('''SELECT * FROM wiki_assertions WHERE revision_id = %s''', (base,))
            base_assertions = [dict(row) for row in cur.fetchall()]
        deltas = assertion_deltas(base_assertions, assertion_payloads)
        carried = {index: base_assertions_by_key(base_assertions, assertion_payloads[index])
                   for index in deltas['carried']}

        new_revision_id = revision_id()
        cur.execute('''INSERT INTO wiki_revisions(revision_id, entry_id, base_revision_id, title,
                body_markdown, body_sha256, citations, author_id, generation_method,
                template_version, model_id, change_note)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
                    (new_revision_id, entry['entry_id'], base, title, body_markdown,
                     sha256_text(body_markdown), _jsonb(citations), actor['user_id'],
                     generation_method, template_version, model_id, change_note))
        revision_row = cur.fetchone()
        for index, assertion in enumerate(assertion_payloads):
            source = carried.get(index)
            _insert_assertion(cur, entry_id_value=entry['entry_id'],
                              revision_id_value=new_revision_id, assertion=assertion,
                              carried_from=source)
        for relation in relation_payloads:
            relation['revision_id'] = new_revision_id
            relation['entry_id'] = entry['entry_id']
            _insert_relation(cur, relation)
        assertions = _assertion_rows(cur, new_revision_id)
        relations = _relation_rows(cur, new_revision_id)
        revision_row = dict(revision_row)
        revision_row['author_display_name'] = actor.get('display_name')
        revision_count, evidence_ref_count, relation_count = _entry_counts(cur, entry)
    return {'revision': revision_object(revision_row, assertions),
            'relations': [relation_object(row) for row in relations],
            'entry': entry_object(entry, assertion_status=aggregate_assertion_status(
                [row['assertion_status'] for row in assertions]),
                revision_count=revision_count, evidence_ref_count=evidence_ref_count,
                relation_count=relation_count),
            'revalidation_required': bool(deltas['revalidation_required']),
            'revalidation': {'changed': [assertion_payloads[index]['assertion_id']
                                         for index in deltas['changed']],
                             'new': [assertion_payloads[index]['assertion_id']
                                     for index in deltas['new']],
                             'removed': deltas['removed'],
                             'carried_verification': [assertion_payloads[index]['assertion_id']
                                                      for index in deltas['carried']]}}


def base_assertions_by_key(base_assertions, assertion):
    key = assertion.get('comparison_key')
    for base in base_assertions or ():
        if base.get('comparison_key') == key:
            return base
    return None


def _insert_assertion(cur, *, entry_id_value, revision_id_value, assertion, carried_from=None):
    """插入一条主张。`carried_from` 非空时沿用基线的核验列（内容逐字段相同，见 §5.12 规则 2）。"""
    carried = carried_from or {}
    carried_status = carried.get('assertion_status') or 'unverified'
    cur.execute('''INSERT INTO wiki_assertions(assertion_id, entry_id, revision_id,
            comparison_key, entity_key, predicate, raw_value, raw_unit, normalized_value,
            normalized_unit, time_scope, applicability, record_scope, statistic, evidence_refs,
            assertion_status, verified_at, verified_by, verification_method,
            verification_coverage, verification_rule_version, verification_accounting)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING assertion_id''',
            (assertion['assertion_id'], entry_id_value, revision_id_value,
             assertion['comparison_key'], _jsonb(assertion['entity_key']), assertion['predicate'],
             assertion['raw_value'], assertion['raw_unit'], assertion['normalized_value'],
             assertion['normalized_unit'],
             None if assertion['time_scope'] is None else _jsonb(assertion['time_scope']),
             None if assertion['applicability'] is None else _jsonb(assertion['applicability']),
             None if assertion['record_scope'] is None else _jsonb(assertion['record_scope']),
             assertion['statistic'], list(assertion['evidence_refs']), carried_status,
             carried.get('verified_at'), carried.get('verified_by'),
             carried.get('verification_method'),
             None if carried.get('verification_coverage') is None
             else _jsonb(carried['verification_coverage']),
             carried.get('verification_rule_version'),
             None if carried.get('verification_accounting') is None
             else _jsonb(carried['verification_accounting'])))
    return cur.fetchone()['assertion_id']


def _insert_relation(cur, relation):
    try:
        cur.execute('''INSERT INTO wiki_relations(relation_id, entry_id, revision_id, src_kind,
                src_id, dst_kind, dst_id, relation_type, raw_type, origin, is_symmetric, supported,
                unsupported_reason, rule_version, basis_note, basis, derivation,
                provenance_incomplete)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING relation_id''',
                    (relation['relation_id'], relation['entry_id'], relation['revision_id'],
                     relation['src_kind'], relation['src_id'], relation['dst_kind'],
                     relation['dst_id'], relation['relation_type'], relation['raw_type'],
                     relation['origin'], relation['is_symmetric'], relation['supported'],
                     relation['unsupported_reason'], relation['rule_version'],
                     relation['basis_note'],
                     None if relation['basis'] is None else _jsonb(relation['basis']),
                     None if relation['derivation'] is None else _jsonb(relation['derivation']),
                     _jsonb(relation['provenance_incomplete'])))
    except Exception as exc:  # append-only / 唯一约束等：只翻译触发器那四个
        raise_append_only(exc)
        raise
    return cur.fetchone()['relation_id']


# ---------------------------------------------------------------------------
# 10. 数据库路径：复核（只 INSERT）与发布（CAS）
# ---------------------------------------------------------------------------

def _scope_of(body):
    scope = body.get('scope')
    if scope is None:
        return {'assertion_ids': [], 'full_revision': False}
    if not isinstance(scope, dict):
        fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='scope must be an object')
    full = scope.get('full_revision') or False
    if not isinstance(full, bool):
        fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='scope.full_revision must be boolean')
    ids = []
    for value in scope.get('assertion_ids') or []:
        assertion = _text(value, 'scope.assertion_ids[]')
        if not ASSERTION_ID_RE.fullmatch(assertion):
            fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='assertion_ids must be wassert- + hex24')
        if assertion not in ids:
            ids.append(assertion)
    if full and ids:
        fail('WIKI_BUILD_REQUEST_INVALID', 422,
             hint='scope is either full_revision or assertion_ids, not both')
    if not full and not ids:
        fail('WIKI_BUILD_REQUEST_INVALID', 422,
             hint='scope needs full_revision=true or a non-empty assertion_ids')
    return {'assertion_ids': ids, 'full_revision': bool(full)}


def _basis_of(body):
    basis = body.get('basis')
    if basis is None:
        return {}
    if not isinstance(basis, dict):
        fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='basis must be an object')
    refs = basis.get('evidence_ref_ids') or []
    if not isinstance(refs, list):
        fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='basis.evidence_ref_ids must be an array')
    normalised = dict(basis)
    normalised['evidence_ref_ids'] = [value for value in refs if isinstance(value, str)]
    return normalised


def _request_id_of(body):
    """`request_id` 必填、≤64、非空白。

    注意：**只有** `wiki_build_jobs` 有 request_id 列（021:555），版本 / 复核 / 发布三条
    写路径无处持久化幂等键，所以这里只做格式校验（见模块头 WHY 4）。
    """
    return _text(body.get('request_id'), 'request_id', max_length=REQUEST_ID_MAX)


def _apply_verdict(cur, *, assertion_ids, status, actor, basis, accounting, scope):
    """把复核裁决写进主张的**核验列**（内容列一律不碰，WIKI4 触发器兜底）。

    `verified` → 六项记账齐全 + 五项分别记账（§7.2）；其它状态 → `verified_*` 一律清空：
    留着 `verified_at` 而状态写着 `unverified` 是自相矛盾的展示。旧的核验结论不丢 ——
    它已经写进追加式的 `wiki_review_events`（WIKI2 连 DELETE 都拒绝）。
    """
    if status == 'verified':
        cur.execute('''UPDATE wiki_assertions SET assertion_status = %s, verified_at = now(),
                verified_by = %s, verification_method = %s, verification_coverage = %s,
                verification_rule_version = %s, verification_accounting = %s
            WHERE revision_id = %s AND assertion_id = ANY(%s)''',
                    (status, actor['user_id'], basis.get('method'),
                     _jsonb({'coverage': basis.get('coverage')}), basis.get('rule_version'),
                     _jsonb(accounting), scope['revision_id'], list(assertion_ids)))
    else:
        cur.execute('''UPDATE wiki_assertions SET assertion_status = %s, verified_at = NULL,
                verified_by = NULL, verification_method = NULL, verification_coverage = NULL,
                verification_rule_version = NULL, verification_accounting = NULL
            WHERE revision_id = %s AND assertion_id = ANY(%s)''',
                    (status, scope['revision_id'], list(assertion_ids)))


def _scope_targets(cur, revision_id_value, scope):
    if scope['full_revision']:
        cur.execute('''SELECT assertion_id FROM wiki_assertions WHERE revision_id = %s
            ORDER BY assertion_id''', (revision_id_value,))
        return [row['assertion_id'] for row in cur.fetchall()]
    cur.execute('''SELECT assertion_id FROM wiki_assertions
        WHERE revision_id = %s AND assertion_id = ANY(%s) ORDER BY assertion_id''',
                (revision_id_value, list(scope['assertion_ids'])))
    found = [row['assertion_id'] for row in cur.fetchall()]
    if sorted(found) != sorted(scope['assertion_ids']):
        fail('WIKI_REFERENCE_INVALID', 422,
             hint='scope.assertion_ids must belong to the reviewed revision')
    return found


def submit_review(entry_id_value, payload, dsn, identity):
    """对指定版本追加复核结果（§5.13）。复核记录**只 INSERT**：WIKI2 禁 UPDATE/DELETE。

    * `expected_head_revision_id` 必须带上（字段缺失直接 422），与当前发布头不一致 → 409；
    * `verdict=verified` 时核验六项必须齐全，缺项 → 422 `WIKI_VERIFICATION_SCOPE_REQUIRED`；
      操作者与时间由服务端写（`actor.user_id` / `now()`），**不接受客户端传入**；
    * 复核**不激活任何来源权威规则**（§5.13 规则 4）：本函数不碰
      `source_authority_rules` / `source_authority_events`。
    """
    body = _body(payload)
    _request_id_of(body)
    action = body.get('action')
    if action == PUBLISH_ACTION:
        fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='publishing is POST /publications')
    if action not in REVIEW_ACTIONS:
        fail('WIKI_BUILD_REQUEST_INVALID', 422,
             hint='action must be one of %s' % (REVIEW_ACTIONS,))
    if 'expected_head_revision_id' not in body:
        fail('WIKI_BUILD_REQUEST_INVALID', 422,
             hint='expected_head_revision_id is required; pass null for the first publication')
    expected = normalize_head(body.get('expected_head_revision_id'))
    revision_id_value = _text(body.get('revision_id'), 'revision_id')
    scope = _scope_of(body)
    basis = _basis_of(body)
    verdict = body.get('verdict') or {}
    if not isinstance(verdict, dict):
        fail('WIKI_BUILD_REQUEST_INVALID', 422, hint='verdict must be an object')
    verdict_status = review_verdict_status(action, verdict.get('assertion'))
    if verdict.get('assertion') == 'verified':
        missing = verification_gaps(basis=basis, scope=scope)
        if missing:
            fail('WIKI_VERIFICATION_SCOPE_REQUIRED', 422,
                 hint='verified needs the verification scope: missing %s' % ', '.join(missing))
    note = _text(body.get('note'), 'note', required=False)

    with connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        actor = _lock_actor(cur, identity, 'reviewer')
        entry = _load_entry(cur, entry_id_value, identity, for_update=True)
        revision = _load_revision(cur, entry['entry_id'], revision_id_value)
        if not cas_passes(expected, entry['head_revision_id']):
            fail('WIKI_VERSION_CONFLICT', 409,
                 hint='expected_head_revision_id is stale; current head is %s'
                      % (entry['head_revision_id'],))
        targets = _scope_targets(cur, revision['revision_id'], scope)
        accounting_basis = basis.get('accounting') if isinstance(basis.get('accounting'), dict) else {}
        accounting = verification_accounting(**{key: accounting_basis.get(key)
                                                for key in ACCOUNTING_KEYS
                                                if key in accounting_basis})
        event = _insert_review_event(cur, entry_id_value=entry['entry_id'],
                                     revision_id_value=revision['revision_id'], action=action,
                                     actor_id=actor['user_id'], expected_head=expected,
                                     based_on=entry['head_revision_id'],
                                     scope=scope, verdict=verdict_status, basis=basis, note=note)
        if verdict_status is not None and targets:
            _apply_verdict(cur, assertion_ids=targets, status=verdict_status, actor=actor,
                           basis=basis, accounting=accounting,
                           scope={'revision_id': revision['revision_id']})
        new_revision_status = revision_publication_after_review(revision['publication_status'],
                                                               action)
        if new_revision_status and new_revision_status != revision['publication_status']:
            cur.execute('''UPDATE wiki_revisions SET publication_status = %s
                WHERE revision_id = %s''', (new_revision_status, revision['revision_id']))
        new_entry_status = entry_publication_after_review(entry['publication_status'],
                                                          entry['head_revision_id'], action)
        if new_entry_status and new_entry_status != entry['publication_status']:
            cur.execute('''UPDATE wiki_entries SET publication_status = %s, updated_at = now()
                WHERE entry_id = %s''', (new_entry_status, entry['entry_id']))
        revision = _load_revision(cur, entry['entry_id'], revision['revision_id'])
        assertions = _assertion_rows(cur, revision['revision_id'])
        entry = _load_entry(cur, entry_id_value, identity)
        revision_count, evidence_ref_count, relation_count = _entry_counts(cur, entry)
    return {'review_event': review_event_object(event),
            'revision': revision_object(revision, assertions),
            'entry': entry_object(entry, assertion_status=aggregate_assertion_status(
                [row['assertion_status'] for row in assertions]),
                revision_count=revision_count, evidence_ref_count=evidence_ref_count,
                relation_count=relation_count)}


def publish(entry_id_value, payload, dsn, identity):
    """发布指定版本并检查旧发布头（§5.14，**比较并交换**）。

    * 角色门是 `approver`（扁平：`reviewer` 调这里 403，`admin` 旁路）；
    * 原子 CAS：`UPDATE … WHERE head_revision_id IS NOT DISTINCT FROM <expected>`
      返回 0 行就是失败 —— 不移动发布头、不删任何版本；
    * 前置：`confirmation='pending'` → 409 `WIKI_ENTRY_IDENTITY_PENDING`；
      目标版本没有任何复核记录 → 409 `WIKI_REVIEW_REQUIRED`；
    * **允许**发布带 `unresolved` 冲突的说明词条：冲突主张列进
      `publication.conflicted_assertions` 与发布事件，**不**阻止发布、**不**改主张状态；
    * 发布**不改变**主张核验状态与来源有效性（三套状态独立，§5.4）。
    """
    body = _body(payload)
    _request_id_of(body)
    if 'expected_head_revision_id' not in body:
        fail('WIKI_BUILD_REQUEST_INVALID', 422,
             hint='expected_head_revision_id is required; pass null for the first publication')
    expected = normalize_head(body.get('expected_head_revision_id'))
    revision_id_value = _text(body.get('revision_id'), 'revision_id')
    note = _text(body.get('note'), 'note', required=False)

    with connect(dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        actor = _lock_actor(cur, identity, 'approver')
        entry = _load_entry(cur, entry_id_value, identity, for_update=True)
        revision = _load_revision(cur, entry['entry_id'], revision_id_value)
        review_event_count = _review_event_count(cur, revision['revision_id'])
        raise_publication_blockers(confirmation=entry['confirmation'],
                                   review_event_count=review_event_count,
                                   publication_status=entry['publication_status'],
                                   revision_present=True)
        if not cas_passes(expected, entry['head_revision_id']):
            fail('WIKI_VERSION_CONFLICT', 409,
                 hint='expected_head_revision_id is stale; current head is %s'
                      % (entry['head_revision_id'],))
        cur.execute('''SELECT assertion_id FROM wiki_assertions
            WHERE revision_id = %s AND assertion_status = 'conflicted'
            ORDER BY assertion_id''', (revision['revision_id'],))
        conflicted = [row['assertion_id'] for row in cur.fetchall()]
        previous_head = entry['head_revision_id']
        # 原子比较并交换：只有发布头仍等于预期值时才前进（0 行 = 有人在中间发布过）。
        cur.execute('''UPDATE wiki_entries SET head_revision_id = %s,
                publication_status = 'published', archived_at = NULL, title = %s,
                updated_at = now()
            WHERE entry_id = %s AND head_revision_id IS NOT DISTINCT FROM %s
            RETURNING ''' + _ENTRY_COLUMNS,
                    (revision['revision_id'], revision['title'], entry['entry_id'], expected))
        updated = cur.fetchone()
        if updated is None:
            fail('WIKI_VERSION_CONFLICT', 409,
                 hint='the publication head moved while publishing; re-read and retry')
        cur.execute('''UPDATE wiki_revisions SET publication_status = 'published'
            WHERE revision_id = %s''', (revision['revision_id'],))
        event = _insert_review_event(
            cur, entry_id_value=entry['entry_id'],
            revision_id_value=revision['revision_id'], action=PUBLISH_ACTION,
            actor_id=actor['user_id'], expected_head=expected, based_on=previous_head,
            scope={'full_revision': True, 'assertion_ids': []}, verdict=None,
            basis={'rule_version': 'wiki-publication-v1', 'method': 'human_review',
                   'coverage': 'revision %s' % revision['revision_id'], 'evidence_ref_ids': []},
            note=note, conflicted_assertions=conflicted)
        revision = _load_revision(cur, entry['entry_id'], revision['revision_id'])
        assertions = _assertion_rows(cur, revision['revision_id'])
        entry = _load_entry(cur, entry_id_value, identity)
        revision_count, evidence_ref_count, relation_count = _entry_counts(cur, entry)
    return {'entry': entry_object(entry, assertion_status=aggregate_assertion_status(
                [row['assertion_status'] for row in assertions]),
                revision_count=revision_count, evidence_ref_count=evidence_ref_count,
                relation_count=relation_count),
            'publication': {'revision_id': revision['revision_id'],
                            'previous_head_revision_id': previous_head,
                            'published_at': event['occurred_at'],
                            'published_by': {'user_id': actor['user_id'],
                                             'display_name': actor.get('display_name')},
                            'review_events': [event['review_event_id']],
                            'conflicted_assertions': conflicted},
            'revision': revision_object(revision, assertions)}
