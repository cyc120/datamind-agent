"""Wiki 知识图谱 P3：读 / 查询与导出层（契约 §2 的 #1 / #2 / #6 / #7 / #8）。

本模块**只**实现 `docs/WIKI_API.md` 已经冻结的读侧接口，不发明语义：

| # | 路由（由 `api.py` 接线，本模块不碰路由） | 本模块入口 |
| --- | --- | --- |
| 1 | `GET /v1/wiki/entries` | `entry_catalogue(dsn, identity, …)` |
| 2 | `GET /v1/wiki/spaces/{space_id}/graph` | `space_graph(space_id, dsn, identity, …)` |
| 6 | `GET /v1/wiki/entries/{entry_id}/graph` | `entry_neighborhood(entry_id, dsn, identity, …)` |
| 7 | `GET /v1/wiki/entries/{entry_id}/provenance` | `entry_provenance(entry_id, dsn, …)` |
| 8 | `GET /v1/wiki/entries/{entry_id}/export` | `entry_export(entry_id, dsn, identity, …)` |

#3 / #4 / #5 与 #9–#14 已在 `datamind/wiki.py` / `datamind/wiki_jobs.py` 里实现，本模块**不重复造**，
只复用它们：身份解析（`wiki.resolve_space` / `wiki.space_for_identity`）、权限判定
（`wiki.has_role` / `wiki.may_read_restricted` / `wiki.require_permission`）、状态组装
（`wiki.status_dimensions` / `wiki.aggregate_assertion_status`）、对象投影
（`wiki.entry_object` / `wiki.relation_object` / `wiki.revision_object`）、游标编解码
（`wiki.encode_cursor` / `wiki.decode_cursor`）、错误信封（`wiki.fail`）。

## 1. 唯一的授权投影（本模块最重要的产出）

契约反复要求"目录、详情、图谱、计数、历史、导出与来源展开**共用授权投影**"
（`WIKI_API.md` §5 `meta.authorized_scope`、§5.2 四条、§5.7 约束 4、§5.8"导出不扩大访问范围"）。
本模块把这条做成一件事，而不是每个接口各写一套过滤条件：

```
authorized_scope(identity, space_id)              ← 唯一的授权入口（Python 侧）
        │
        ├── entry_authorization_predicate(scope)   ← SQL 渲染（列表/计数/图/来源全用它）
        ├── entry_is_authorized(scope, row)        ← Python 渲染（同一规则的另一种写法）
        └── relation_endpoint_authorization_predicate(scope)  ← 关系**两个端点**的授权（计数/图用）
```

三个渲染都在 `datamind/wiki_query.py` 的 §3 里定义，规则只有一处：
`(space_id = 授权空间 AND owner_id = 请求者) OR 请求者是 admin`。任何新读接口都必须从这里取 `scope`
并复用上述渲染，**不允许**在别处写 `space_id = …`。

先例与理由：`datamind/visibility.py:1-30` 记录过同一条纪律被写了三遍并已经漂移（事故 R03）。
`wiki.py` 自己的 `_load_entry()`（#3/#4/#5 用）也是同一条规则的第三次书写，所以本模块的词条级读取
（图谱根、来源、导出）**直接调用 `wiki._load_entry()`**，而不是再写第四遍——见 `authorized_entry()`。

## 2. 分页 / 截断 / 游标重置（契约 §4，逐条落在纯函数上）

* 游标不透明（`base64url(规范 JSON)`，沿用 `wiki.encode_cursor`），载荷里带**筛选指纹**；
* **图层或筛选条件变化 → 指纹不匹配 → 服务端忽略游标、当作新集合**（`resolve_cursor()`），
  并在 `warnings` 里给出 `CURSOR_RESET_ON_FILTER_CHANGE`；
* 空间图：每批 50 节点，**累计上限 100 节点 / 300 关系**；上限由服务端按游标里的累计计数**强制**，
  客户端把游标改大也会被 `clamp`（`graph_cursor_state()`），所以"继续加载绕过上限"不成立；
* 达到上限 → `limit_reached` + `next_cursor=null` + 提示键 `WIKI_GRAPH_LIMIT_REACHED`（HTTP 200，
  不是 4xx，契约 §8.2 纪律 2）；
* **关系两端都必须在"当前已授权且已加载"的节点集合内**（`graph_batch()`）：空间图按"候选节点的前缀"
  界定已加载集合（前缀由游标里的计数唯一确定），半条边一律不返回；
* **连接数（degree / relation_counts）只统计当前授权 + 当前图层 + 已加载的连接**
  （`degrees_of()`），隐藏节点不参与计数，因此不能用数字推断隐藏图的存在性。

## 3. 本模块**没有**假设的东西（如实声明）

* **派生关系尚未落库。** `wiki_projection.plan_wiki_derived_relations()` 对 8 个 Wiki 派生关系只产出
  `deferred: true` 的**推导计划**（`wiki_projection.py:2123-2124`），端点是 build 步骤铸造的；
  本模块只读 `wiki_relations` 里**已经存在**的行，不现场推导、不伪造；
* **`wiki_evidence_refs` / `wiki_provenance_links` 目前没有任何写入者**（全仓只有
  `wiki.py:1897` 写 `wiki_relations`）。所以 #2 的 evidence / runs 图层与 #7 的证据段在今天的库上
  会是空的——那是"确实没有"，不是"读取失败"（两者在 #7 里用 `events_unavailable` 与
  `WIKI_LOAD_FAILED` 区分）；
* **运行图层是"运行 + 证据引用"二部图**，边来自 `wiki_provenance_links`。理由：`wiki_relations`
  的端点被迁移 CHECK（`021:447-456`）限制为 `wiki-` / `wassert-` / `wev-` 三种前缀，
  **运行 id 永远不可能成为关系端点**，所以运行图层的边只能来自出处映射表。
  "运行 ↔ 词条"的对应关系在 #7 的 `runs` 段给出（同一份授权投影），不在这里重复造边；
* **中文正文检索策略未定**（契约 §5.1 / §13 第 5 条）：#1 的 `q` 只覆盖**标题 / 别名 / 分类**，
  响应里的 `search` 段如实声明覆盖范围与未覆盖字段，不假装搜了正文。

## 4. 数据库连接

Wiki 的 8 张表已被 `021:605-607` `REVOKE ALL … FROM PUBLIC, readonly_user`（实测
`readonly_user` 读 `wiki_entries` 报 `42501`），**所以 Wiki 的读也必须用应用角色连接**
（`wiki.wiki_dsn()` == `wiki.write_dsn()`），只读角色的 DSN 在这里一次都不用。
#7 还会读 `runs` / `trace_events` / `document_assets` / `data_sources` / `schema_snapshots`
（只读 SELECT，`datamind_app` 对这些表有 SELECT 权限）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re

from . import wiki
#: §6.4 的理由码与"缺失的三列"都**复用投影层**（`wiki_projection.py:148-159`）：
#: 读侧与投影侧对"哪些字段从未落库"必须只有一个答案。
from .wiki_projection import (
    MISSING_EDGE_FIELDS, REASON_EDGE_COLUMNS_ABSENT, missing_edge_columns,
    provenance_incomplete as projection_provenance_incomplete,
)

# ---------------------------------------------------------------------------
# 1. 闭集与上限：契约 §4 / §5.1 / §5.2 / §5.6 / §5.7 / §5.8 的冻结值
# ---------------------------------------------------------------------------

#: §4：目录 20 / 最多 50。
CATALOGUE_LIMIT_DEFAULT = 20
CATALOGUE_LIMIT_MAX = 50
#: §4：空间图每批 50 节点（固定批次，不接受更大值）。
GRAPH_BATCH_DEFAULT = 50
GRAPH_BATCH_MAX = 50
#: §4 / §9.3：单次显示最多 100 节点与 300 条关系。
GRAPH_NODE_LIMIT = 100
GRAPH_RELATION_LIMIT = 300
#: §5.2 的图层闭集。
GRAPH_LAYERS = ('entries', 'evidence', 'runs')
#: §5.6：邻域默认 1 跳、最多 2 跳；每页最多 100 节点。
NEIGHBORHOOD_DEPTH_DEFAULT = 1
NEIGHBORHOOD_DEPTH_MAX = 2
NEIGHBORHOOD_LIMIT_DEFAULT = 50
NEIGHBORHOOD_LIMIT_MAX = 100
#: §5.6 的方向闭集。
NEIGHBORHOOD_DIRECTIONS = ('in', 'out', 'both')
#: §4：证据与事件每页最多 200 条；默认 50。
PROVENANCE_LIMIT_DEFAULT = 50
PROVENANCE_LIMIT_MAX = 200
#: §5.7 的图层闭集。
PROVENANCE_LAYERS = ('evidence', 'runs', 'both')
#: §4 / §5.8：导出单次最多 300 条证据引用。
EXPORT_EVIDENCE_MAX = 300
#: §5.1：`q` ≤ 200 字符；`category` ≤ 64 字符。
QUERY_MAX = 200
CATEGORY_MAX = 64
#: 游标长度上限（沿用 wiki.decode_cursor 的既有边界）。
CURSOR_MAX = 512

#: §5.1 `q` **实际**覆盖的字段（响应里必须如实声明，别让用户以为搜了正文）。
SEARCH_SCOPE_FIELDS = ('title', 'aliases', 'category')
#: §5.1 **未**覆盖的字段：中文正文检索策略未定（契约 §13 第 5 条）。
SEARCH_UNCOVERED_FIELDS = ('body_markdown', 'summary')
SEARCH_BODY_REASON = 'CHINESE_BODY_SEARCH_UNDECIDED'

#: §4 语义 5 / §8.2：达到上限时的提示键（HTTP 200，不是错误码）。
GRAPH_LIMIT_HINT = 'WIKI_GRAPH_LIMIT_REACHED'
#: §5 `meta.authorized_scope`：投影前看到 vs 过滤后返回不一致时的原因码。
AUTHORIZATION_FILTERED = 'AUTHORIZATION_FILTERED'
#: §4 语义 4：筛选/图层变化时游标被重置的原因码（放在 warnings 里，不冒充错误）。
CURSOR_RESET_ON_FILTER_CHANGE = 'CURSOR_RESET_ON_FILTER_CHANGE'
#: §8.2 纪律 1：读取失败**不得**显示成"无数据"。
WIKI_LOAD_FAILED = 'WIKI_LOAD_FAILED'

#: §5.6 的三个截断原因（顺序即优先级）。
TRUNCATED_REASONS = ('NODE_LIMIT', 'RELATION_LIMIT', 'DEPTH_LIMIT')

#: 仓库统一时区：契约 §1"时间一律带时区（ISO 8601，+08:00）"。
ASIA_SHANGHAI = timezone(timedelta(hours=8))

#: §3.2 的 21 个关系类型 → 展示词。`wiki.relation_object()` 刻意不给 `label`
#: （`wiki.py:1104` 注释："label 是投影层的展示词表，本层不造"），而投影层至今没有这张表；
#: 图谱需要它来满足 §3.4 规则 4 的"未支持关系类型"文字标识，所以放在读侧（展示层）。
RELATION_LABELS = {
    'entity_match': '同一实体', 'version_scope': '版本适用范围', 'unit_equivalent': '单位可换算',
    'time_overlap': '时间范围重叠', 'supports': '支撑', 'conflicts': '冲突',
    'derived_from': '派生自', 'retrieved_from': '检索来源', 'maps_to': '词表映射',
    'contradicts': '矛盾', 'verified_by': '被核验', 'repaired_from': '修复自',
    'resolved_by': '被裁决', 'measures': '度量', 'applies_to': '适用于', 'scoped_by': '受限于',
    'threshold_of': '限值', 'about': '关于', 'justified_by': '依据', 'supersedes': '取代',
    'restates': '重述',
}
#: §3.4 规则 3：不认识的类型不改写成最近的已知类型，只标未支持。
UNSUPPORTED_RELATION_LABEL = '未支持关系类型'

#: §6.4 的理由码与"缺失的三列"来自投影层（见文件头的 import），这里只登记读侧用到的语义。

#: §7.5 的读时复查结论 → 三套状态里的 `source_validity` 维度。
SOURCE_VALIDITY_ORDER = ('unavailable', 'stale', 'current')

#: §7.5 `unavailable` 的后果：不返回任何受限正文/摘要/excerpt。
WITHHELD_EXCERPT = ''

#: 目录/图谱里"当前视图版本"的 SQL：发布头优先，其次最新版本（与 `wiki.entry_detail` 同口径）。
#: WHY 不用 `wiki._entry_counts()` 的"只看 head"：那个写法对未发布词条恒返回 0，
#: 会让未发布词条在图谱里永远没有关系；`entry_detail` 已经确定了"head 优先、否则最新"的口径。
_VIEW_REVISION_TEMPLATE = (
    'COALESCE({alias}.head_revision_id, (SELECT rv.revision_id FROM wiki_revisions rv'
    ' WHERE rv.entry_id = {alias}.entry_id'
    ' ORDER BY rv.created_at DESC, rv.revision_id DESC LIMIT 1))')

#: `wiki._ENTRY_COLUMNS` 的带别名版本（单一来源：列清单只写在 wiki.py 一处）。
_ENTRY_COLUMNS_ALIASED = ', '.join(
    'e.' + name.strip() for name in wiki._ENTRY_COLUMNS.split(','))


def view_revision_sql(alias='e') -> str:
    """"当前视图版本"的 SQL 片段（发布头优先、否则最新版本），供目录/图谱/邻域共用。"""
    return _VIEW_REVISION_TEMPLATE.format(alias=alias)


def entry_columns_sql(alias='e') -> str:
    """词条列清单（带别名），与 `wiki._ENTRY_COLUMNS` 同源，避免第二份列清单漂移。"""
    return _ENTRY_COLUMNS_ALIASED.replace('e.', alias + '.') if alias != 'e' \
        else _ENTRY_COLUMNS_ALIASED


# ---------------------------------------------------------------------------
# 2. 纯逻辑：枚举、文本、时间与图形状的基本校验
# ---------------------------------------------------------------------------

def _fail(code, status=409, *, message=None, hint=None):
    wiki.fail(code, status, message=message, hint=hint)


def _invalid(hint):
    """读接口的参数越界：契约 §8 说框架级 422 不走错误码表，服务层的防御性校验
    沿用 `wiki.revision_history()` 的既有做法（`WIKI_BUILD_REQUEST_INVALID` + 422）。"""
    _fail('WIKI_BUILD_REQUEST_INVALID', 422, hint=hint)


def _as_list(value):
    """`type` / `status` 可重复：接受单值、列表，或逗号分隔（与 SQL 的 ANY 同形）。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set, frozenset)):
        items = []
        for item in value:
            items.extend(_as_list(item))
        return items
    if isinstance(value, str):
        return [part.strip() for part in value.split(',') if part.strip()]
    _invalid('expected a string or a list of strings')


def normalize_enum(value, allowed, param, *, multi=False):
    """闭集枚举校验。未知值 → 422（筛选参数不是"原值保留 + supported=false"的对象，见 §1）。"""
    if value is None:
        return () if multi else None
    if not multi:
        if not isinstance(value, str):
            _invalid('%s must be a string' % param)
        wiki._no_nul(value, 'WIKI_BUILD_REQUEST_INVALID', 422)
        text = value.strip()
        if not text:
            return None
        if text not in allowed:
            _invalid('%s must be one of %s' % (param, ', '.join(allowed)))
        return text
    values = []
    for item in _as_list(value):
        wiki._no_nul(item, 'WIKI_BUILD_REQUEST_INVALID', 422)
        text = item.strip()
        if not text:
            continue
        if text not in allowed:
            _invalid('%s must be one of %s' % (param, ', '.join(allowed)))
        if text not in values:
            values.append(text)
    return tuple(sorted(values))


def normalize_text(value, param, *, max_length):
    """strip → 非空 → 长度；空白等于不传（沿用 `/v1/sessions` 的 `q` 语义，契约 §5.1）。"""
    if value is None:
        return None
    if not isinstance(value, str):
        _invalid('%s must be a string' % param)
    wiki._no_nul(value, 'WIKI_BUILD_REQUEST_INVALID', 422)
    text = value.strip()
    if not text:
        return None
    if len(text) > max_length:
        _invalid('%s must be at most %d characters' % (param, max_length))
    return text


def search_pattern(q):
    """`ILIKE` 模式：`\\`、`%`、`_` 全部转义成字面量，配 `ESCAPE '\\'`（契约 §5.1）。

    与 `sessions._search_pattern`（`sessions.py:128-143`）逐字同款：用户搜 `100%` 时 `%`
    **不是**通配符。返回 `None` 表示"没有搜索词"。
    """
    text = normalize_text(q, 'q', max_length=QUERY_MAX)
    if text is None:
        return None
    escaped = (text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_'))
    return '%' + escaped + '%'


def search_declaration(q):
    """§5.1：**如实**声明本次关键词检索覆盖了哪些字段。

    `scope` 是契约写死的三个字段；`body_searched` 恒为 `false`；`uncovered_fields` 与
    `reason_code` 是补充的诚实声明——中文正文检索策略未定（契约 §13 第 5 条），
    用户不能因为我们没写清楚就以为搜了正文。
    """
    text = normalize_text(q, 'q', max_length=QUERY_MAX)
    return {'q': text, 'scope': list(SEARCH_SCOPE_FIELDS), 'body_searched': False,
            'uncovered_fields': list(SEARCH_UNCOVERED_FIELDS), 'reason_code': SEARCH_BODY_REASON}


def normalize_timestamp(value, param='updated_since'):
    """ISO 8601 → tz-aware datetime。朴素时间按仓库统一时区（+08:00）解释，非法输入 422。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        wiki._no_nul(value, 'WIKI_BUILD_REQUEST_INVALID', 422)
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
        except ValueError:
            _invalid('%s must be an ISO 8601 timestamp' % param)
    else:
        _invalid('%s must be an ISO 8601 timestamp' % param)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ASIA_SHANGHAI)
    return parsed


def iso_value(value):
    """契约 §1：时间一律带时区（ISO 8601，+08:00）。纯函数，导出与自证字段都走它。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=ASIA_SHANGHAI)
        return moment.astimezone(ASIA_SHANGHAI).isoformat()
    return str(value)


def now_iso():
    return datetime.now(ASIA_SHANGHAI).isoformat()


# ---------------------------------------------------------------------------
# 3. 唯一的授权投影（契约 §5 的 meta.authorized_scope / 全部读接口共用）
# ---------------------------------------------------------------------------

#: 授权规则的**参数名**。SQL 片段只使用这三个命名占位符，绝不拼接用户数据。
AUTH_PARAM_SPACE = 'auth_space'
AUTH_PARAM_OWNER = 'auth_owner'
AUTH_PARAM_ADMIN = 'auth_admin'


def authorized_scope(identity, request_space_id=None):
    """**唯一的授权入口**：请求身份 → 授权范围（Python 侧的 AuthorizedScope）。

    返回 `{'space_id', 'user_id', 'is_admin', 'own_space_id', 'roles'}`：

    * `space_id` 是"本次读取被限定在哪个空间"。请求别人的空间 → `404 WIKI_SPACE_NOT_FOUND`
      （与不存在不可区分，契约 §3.1 脚注 5），由 `wiki.resolve_space()` 判定；
    * 规则本体只有一条：`(space_id = 授权空间 AND owner_id = 请求者) OR 请求者是 admin`。
      它在这里定义一次，然后由 `entry_authorization_predicate()`（SQL）、
      `entry_is_authorized()`（Python）、`relation_endpoint_authorization_predicate()`
      （关系两端）三个渲染共用。

    为什么 admin 旁路也在规则里：`auth.py:195` 的超管语义是"admin 通过一切角色门"，
    授权投影必须与认证面一致，否则 `admin` 会看到自己的空间却看不到别人的词条——
    那与 #3/#4/#5 的 `wiki._load_entry()` 行为不一致（那条 SQL 也带 `%s OR (space AND owner)`）。
    """
    user_id = wiki.user_id_of(identity)          # 无身份 → 401 SESSION_INVALID
    own_space = wiki.space_for_identity(identity)
    space_id = wiki.resolve_space(identity, request_space_id)
    return {'space_id': space_id, 'user_id': user_id, 'is_admin': wiki.is_admin(identity),
            'own_space_id': own_space, 'roles': list(wiki.roles_of(identity))}


def authorization_params(scope):
    """授权 SQL 片段的命名参数（与 `entry_authorization_predicate` 一一对应）。"""
    return {AUTH_PARAM_SPACE: scope['space_id'], AUTH_PARAM_OWNER: scope['user_id'],
            AUTH_PARAM_ADMIN: bool(scope['is_admin'])}


def entry_authorization_predicate(scope, *, alias='e'):
    """授权规则的 **SQL 渲染**：`wiki_entries` 别名上的布尔条件。

    **这是本模块唯一一处书写授权条件的地方**（`space_id` / `owner_id` 字面量只在这里出现）。
    所有列表、计数、图谱候选、关系端点、证据展开都用它；新增读接口也必须用它。
    """
    return ('(({a}.space_id = %({space})s AND {a}.owner_id = %({owner})s) OR %({admin})s)'
            .format(a=alias, space=AUTH_PARAM_SPACE, owner=AUTH_PARAM_OWNER,
                    admin=AUTH_PARAM_ADMIN))


def entry_is_authorized(scope, row):
    """授权规则的 **Python 渲染**：与上面的 SQL 条件逐字对应（同一规则，两种写法）。

    两种渲染必须一致，否则"能列出"与"能读取"会分叉。`tests/test_wiki_query.py` 用
    "模块里 `space_id` 授权字面量只出现一次" + "两种渲染对同一批样本给出同一结论"钉住它。
    """
    if scope.get('is_admin'):
        return True
    return (row.get('space_id') == scope['space_id'] and row.get('owner_id') == scope['user_id'])


#: 关系端点 → 承载它的表与 id 列（`021:447-456` 的三个前缀）。
ENDPOINT_TABLES = {'entry': ('wiki_entries', 'entry_id'),
                   'assertion': ('wiki_assertions', 'assertion_id'),
                   'evidence_ref': ('wiki_evidence_refs', 'evidence_ref_id')}


def relation_endpoint_authorization_predicate(scope, *, alias='r'):
    """一条关系的**两个端点**是否都在授权范围内（计数与图谱共用）。

    WHY 需要它：关系是跨对象的边，只校验关系自己的 `entry_id` 不够——一条
    `src = 别人空间的词条` 的边如果被计数，隐藏对象的存在性就通过数字泄露了
    （契约 §4 语义 7 / §8.2 第 198 行）。规则：

    * `entry` 端点 → 该词条满足 `entry_authorization_predicate()`；
    * `assertion` 端点 → 该主张所属词条满足同一条件；
    * `evidence_ref` 端点 → 该证据引用属于授权空间（证据引用只有空间维度，`021:311-312`）。

    三个分支都由同一个 `scope` 生成，因此不会出现"某一个接口忘了校验某一类端点"。
    """
    clauses = []
    for kind in sorted(ENDPOINT_TABLES):
        for side in ('src', 'dst'):
            if kind == 'entry':
                exists = ('EXISTS (SELECT 1 FROM wiki_entries ep_{k} WHERE ep_{k}.entry_id = '
                          '{a}.{s}_id AND {auth})')
                exists = exists.format(
                    k=kind, a=alias, s=side,
                    auth=entry_authorization_predicate(scope, alias='ep_' + kind))
            elif kind == 'assertion':
                exists = ('EXISTS (SELECT 1 FROM wiki_assertions ep_{k}'
                          ' JOIN wiki_entries ep_{k}_entry'
                          ' ON ep_{k}_entry.entry_id = ep_{k}.entry_id'
                          ' WHERE ep_{k}.assertion_id = {a}.{s}_id AND {auth})')
                exists = exists.format(
                    k=kind, a=alias, s=side,
                    auth=entry_authorization_predicate(scope, alias='ep_%s_entry' % kind))
            else:
                exists = ('EXISTS (SELECT 1 FROM wiki_evidence_refs ep_{k}'
                          ' WHERE ep_{k}.evidence_ref_id = {a}.{s}_id'
                          ' AND ep_{k}.space_id = %({space})s)')
                exists = exists.format(k=kind, a=alias, s=side, space=AUTH_PARAM_SPACE)
            clauses.append("({a}.{s}_kind <> '{k}' OR {e})".format(a=alias, s=side, k=kind,
                                                                   e=exists))
    return '(' + ' AND '.join(clauses) + ')'


def authorized_scope_meta(scope, *, considered, visible):
    """§5 的 `meta`：`authorized_scope` 是**授权投影的自证**。

    `nodes_considered` = 投影前看到的对象数，`nodes_visible` = 过滤后返回的数。
    两者不等时调用方必须把 `AUTHORIZATION_FILTERED` 放进 `warnings`（见 `scope_warnings`），
    否则"我看不到"会被误读成"不存在"。
    """
    return {'space_id': scope['space_id'], 'as_of': now_iso(),
            'authorized_scope': {'nodes_considered': int(considered),
                                 'nodes_visible': int(visible)}}


def scope_warnings(*, considered, visible, extra=()):
    """§5：过滤掉了东西就必须给原因码，不能悄悄吞掉。"""
    warnings = list(extra)
    if int(considered) != int(visible):
        warnings.append(AUTHORIZATION_FILTERED)
    return warnings


def envelope(*, data, meta, paging, warnings=()):
    """§5 的统一响应包装：`{data, meta, paging, warnings}`。

    契约 §5.1 / §5.2 的 `200` 示例画的是 `data` 里的内容（`entries`/`search`、`nodes`/`relations`），
    §5 开头的包装图才是响应的最外层；本模块按"示例 = data"实现，`warnings` 是契约
    `meta.authorized_scope` 那一段要求的自证通道。
    """
    return {'data': data, 'meta': meta, 'paging': paging, 'warnings': list(warnings)}


def paging_object(*, limit, has_more, next_cursor, truncated):
    """§4 语义 3：响应**恒带**这四个字段（`truncated` 表示"因上限截断"，不是"没有更多"）。"""
    return {'limit': int(limit), 'has_more': bool(has_more),
            'next_cursor': next_cursor, 'truncated': bool(truncated)}


# ---------------------------------------------------------------------------
# 4. 纯逻辑：筛选归一、筛选指纹与游标（§4 语义 1/2/4）
# ---------------------------------------------------------------------------

#: 目录/来源列表的游标载荷：`f` 筛选指纹、`k` 排序键、`i` 唯一兜底键（§4 语义 2）。
LISTING_CURSOR_KEYS = ('f', 'k', 'i')
#: 空间图的游标载荷：`f` 筛选指纹、`n`/`r` **累计**已加载节点/关系数。
#: 用计数而不是 id 列表：计数唯一确定了"候选列表的前缀"，前缀就是服务端的已加载集合，
#: 所以"关系两端都在已加载集合内"可以在服务端判定，而不必相信客户端报上来的 id 集合。
GRAPH_CURSOR_KEYS = ('f', 'n', 'r')


def normalize_filters(*, layer=None, q=None, entry_type=None, status=None, category=None,
                      updated_since=None, space_id=None, direction=None, depth=None,
                      revision_id=None):
    """把查询参数归一成可比较、可指纹化的字典（只保留非空项）。

    归一是**语义要求**：`?q=温度` 与 `?q=%E6%B8%A9%E5%BA%A6&type=` 必须得到同一个指纹，
    否则"筛选没变"会被判成"变了"从而每次重置游标。
    """
    filters = {}
    if layer is not None:
        filters['layer'] = layer
    pattern = search_pattern(q)
    if pattern is not None:
        filters['q'] = normalize_text(q, 'q', max_length=QUERY_MAX)
    if entry_type:
        filters['entry_type'] = list(normalize_enum(entry_type, wiki.ENTRY_TYPES, 'type',
                                                    multi=True))
    if status:
        filters['status'] = list(normalize_enum(status, wiki.PUBLICATION_STATES, 'status',
                                                multi=True))
    if category:
        filters['category'] = normalize_text(category, 'category', max_length=CATEGORY_MAX)
    moment = normalize_timestamp(updated_since)
    if moment is not None:
        filters['updated_since'] = iso_value(moment)
    if space_id is not None:
        filters['space_id'] = space_id
    if direction is not None:
        filters['direction'] = direction
    if depth is not None:
        filters['depth'] = depth
    if revision_id is not None:
        filters['revision_id'] = revision_id
    return filters


def filter_fingerprint(filters):
    """筛选条件指纹：游标只在指纹一致时才被采用（§4 语义 4）。

    **图层或任何筛选条件变化都会改变指纹**，因此服务端会忽略传入的游标、返回第一批并置
    `truncated=false`；客户端也必须同时重置已加载集合（否则"继续加载绕过上限"会在客户端侧发生）。
    """
    return wiki.sha256_json(dict(filters or {}))


def resolve_cursor(cursor, *, fingerprint, keys):
    """解码游标并判定是否需要重置。

    返回 `(marker, reset)`：

    * `cursor` 为空 → `(None, False)`；
    * 筛选指纹一致 → `(marker, False)`，调用方按 `marker` 继续翻页；
    * 指纹不一致 → `(None, True)`：**服务端视为新集合**（契约 §4 语义 4），
      调用方必须在 `warnings` 里给出 `CURSOR_RESET_ON_FILTER_CHANGE`。

    畸形游标（不可解码、字段集不对、含 NUL）由 `wiki.decode_cursor` 抛 422，绝不 500。
    """
    if not cursor:
        return None, False
    if isinstance(cursor, str) and len(cursor) > CURSOR_MAX:
        _invalid('cursor must be at most %d characters' % CURSOR_MAX)
    marker = wiki.decode_cursor(cursor, keys=keys)
    if marker.get('f') != fingerprint:
        return None, True
    return marker, False


def listing_cursor(fingerprint, *, sort_key, tie_break):
    """目录/来源列表的下一页游标（`k` 排序键 + `i` 唯一兜底键）。"""
    return wiki.encode_cursor({'f': fingerprint, 'k': iso_value(sort_key), 'i': tie_break})


def listing_marker(marker):
    """校验列表游标的排序键/兜底键，返回 `(sort_key, tie_break)`；畸形 → 422。"""
    if marker is None:
        return None
    key, tie = marker.get('k'), marker.get('i')
    if not isinstance(key, str) or not isinstance(tie, str) or not key or not tie:
        _invalid('listing cursor is malformed')
    return key, tie


def graph_cursor_state(marker, *, fingerprint, node_limit=GRAPH_NODE_LIMIT,
                       relation_limit=GRAPH_RELATION_LIMIT):
    """空间图游标的累计计数（`n`/`r`），并**强制**上限。

    两个安全性质：

    * 计数只能用于"继续加载"，超过上限一律 clamp —— 客户端伪造一个更大的 `n` 也拿不到更多节点
      （契约 §4 语义 5："不能通过继续加载绕过上限"）；
    * `n`/`r` 必须是 0..上限的整数，非法 → 422（畸形游标不是合法请求）。
    """
    if marker is None:
        return 0, 0
    values = []
    for name, ceiling in (('n', node_limit), ('r', relation_limit)):
        value = marker.get(name)
        if type(value) is not int or value < 0:
            _invalid('graph cursor is malformed')
        values.append(min(value, ceiling))
    return values[0], values[1]


def graph_cursor(fingerprint, *, loaded_nodes, loaded_relations):
    """空间图的下一页游标：筛选指纹 + 累计已加载计数。"""
    return wiki.encode_cursor({'f': fingerprint, 'n': int(loaded_nodes),
                               'r': int(loaded_relations)})


# ---------------------------------------------------------------------------
# 5. 纯逻辑：首页/尾页的通用装配（limit + 1 探测 has_more，仓库既有惯例）
# ---------------------------------------------------------------------------

def window_and_more(rows, limit):
    """`limit + 1` 探测法：返回 `(窗口, has_more)`（先例 `/v1/runs`、`/v1/sessions`）。"""
    rows = list(rows or ())
    return rows[:limit], len(rows) > limit


def last_cursor_row(window):
    return window[-1] if window else None


# ---------------------------------------------------------------------------
# 6. 纯逻辑：图谱（关系展示、两端在已加载集合内、连接数、批量与截断）
# ---------------------------------------------------------------------------

def node_id(node):
    """图谱节点的 id：`entry_id` / `evidence_ref_id` / `run_id` 三选一（取 `node_id` 字段）。"""
    return node.get('node_id')


def relation_label(relation_type, *, supported=True):
    """关系类型的展示词。未支持类型 → "未支持关系类型"（§3.4 规则 4：细线 + 文字标识）。"""
    if not supported or relation_type is None:
        return UNSUPPORTED_RELATION_LABEL
    return RELATION_LABELS.get(relation_type, UNSUPPORTED_RELATION_LABEL)


def graph_relation_object(row):
    """§5.2 的关系对象 = `wiki.relation_object()` + `label`（展示层唯一新增字段）。"""
    relation = wiki.relation_object(row)
    relation['label'] = relation_label(relation['relation_type'],
                                       supported=relation['supported'])
    return relation


def provenance_link_relation(row):
    """运行图层的边：`wiki_provenance_links` 的一行 → 图谱关系对象。

    WHY 是 `derived_from` 且方向是"证据 → 运行"：契约 §3.2 #7 把 `derived_from` 定义为
    "左端由右端派生（如 `Claim` ← `SQLRow`），依据是运行内的派生记录"。一次运行中的证据出现记录
    正是由那次运行派生的（`wiki_provenance_links` 的语义就是"这条可复用引用来自这次运行"），
    所以 `evidence_ref --derived_from--> run` 是同一词表的直接应用，不是新语义。

    `raw_type` 记为承载它的表名（`origin='projected'` 要求 `raw_type` 非空，`021:475`），
    `basis.provenance_link_id` 指向那一行，前端因此能区分"落库的关系行"与"出处映射边"。
    """
    return {'relation_id': row['provenance_link_id'],
            'src': row['evidence_ref_id'], 'src_kind': 'evidence_ref',
            'dst': row['run_id'], 'dst_kind': 'run',
            'relation_type': 'derived_from',
            'direction': 'directed', 'origin': 'projected',
            'raw_type': 'wiki_provenance_links', 'supported': True,
            'unsupported_reason': None, 'rule_version': None,
            'basis_note': None,
            'basis': {'source_table': 'wiki_provenance_links',
                      'provenance_link_id': row['provenance_link_id'],
                      'run_id': row['run_id'], 'evidence_id': row['evidence_id'],
                      'claim_id': row.get('claim_id'), 'event_id': row.get('event_id'),
                      'revision_id': row.get('revision_id'),
                      'assertion_id': row.get('assertion_id')},
            'derivation': None,
            'provenance_incomplete': row.get('provenance_incomplete') or {'applies': False},
            'created_at': row.get('created_at'),
            'label': RELATION_LABELS['derived_from'],
            'link_kind': 'provenance_link'}


def relations_within_nodes(relations, node_ids):
    """§4 语义 6：**关系两端都必须在当前已授权且已加载的节点集合内**。

    半条边（只有一端在集合里）一律不返回——它既画不出线，又会泄露"另一端存在"。
    """
    loaded = set(node_ids or ())
    return [relation for relation in relations or ()
            if relation.get('src') in loaded and relation.get('dst') in loaded]


def degrees_of(node_ids, relations):
    """每个节点的连接数，**只统计传进来的关系**（= 当前授权 + 当前图层 + 已加载）。

    §4 语义 7：不返回隐藏内容的总度数——那会通过数字泄露存在性。所以本函数只数已加载的边，
    没有第二条数据来源；`relation_counts.scope` 恒为 `authorized_loaded`。
    """
    degrees = {node_id_value: 0 for node_id_value in (node_ids or ())}
    for relation in relations or ():
        for side in ('src', 'dst'):
            value = relation.get(side)
            if value in degrees:
                degrees[value] += 1
    return degrees


def graph_batch(*, candidates, candidate_relations, fingerprint, loaded_nodes=0,
                loaded_relations=0, batch_limit=GRAPH_BATCH_DEFAULT,
                node_limit=GRAPH_NODE_LIMIT, relation_limit=GRAPH_RELATION_LIMIT,
                more_candidates=False, more_relations=False):
    """空间图的一批（#2）：节点增量 + 新完成的关系 + 累计计数 + 截断标记。

    参数语义（数据库层只负责取数，判定全在这里，因此可以脱库测试）：

    * `candidates`：按确定顺序排好的候选节点，**已按 `node_limit` 截断**。已加载集合就是
      "该列表的前 `loaded_nodes` 个"——这个前缀由游标里的计数唯一确定，所以服务端不必相信
      客户端报上来的 id 集合，也能判定"关系两端是否都已加载"；
    * `candidate_relations`：两端都在 `candidates` 里的关系（同样给出 `more_relations`）；
    * `more_candidates` / `more_relations`：候选是否还有第 `limit + 1` 个（`limit + 1` 探测法）。

    返回：

    * `nodes`：**增量**节点（第 `loaded_nodes` 个之后到本批末），每个带
      `degree` / `degree_scope='authorized_loaded'` / `position_hint=None`；
    * `relations`：本批**新完成**的关系（两端都在新的前缀里，且至少一端落在增量里）；
    * `loaded`：累计已加载计数（`loaded.nodes == 上限` 时停止继续加载）；
    * `limit_reached` + `next_cursor=null` + `truncated`：§4 语义 5；只要**达到**上限就给出
      `limit_reached`（提示缩小范围），而 `truncated` 只在"上限确实挡住了东西"时为真。
    """
    candidates = list(candidates or ())
    if len(candidates) > node_limit:
        _invalid('candidates must already be capped at %d nodes' % node_limit)
    loaded_before = max(0, min(int(loaded_nodes or 0), node_limit))
    batch = max(1, min(int(batch_limit or GRAPH_BATCH_DEFAULT), GRAPH_BATCH_MAX))
    prefix_after = candidates[:min(loaded_before + batch, node_limit)]
    ids_after = [node_id(node) for node in prefix_after]
    ids_before = {node_id(node) for node in candidates[:loaded_before]}
    delta = prefix_after[loaded_before:]
    delta_ids = {node_id(node) for node in delta}
    loaded_ids = set(ids_after)

    in_set = relations_within_nodes(candidate_relations, loaded_ids)
    relations_after = in_set[:relation_limit]
    new_relations = [relation for relation in relations_after
                     if relation.get('src') in delta_ids or relation.get('dst') in delta_ids]
    degrees = degrees_of(ids_after, relations_after)

    nodes = []
    for node in delta:
        item = dict(node)
        item['degree'] = degrees.get(node_id(node), 0)
        item['degree_scope'] = 'authorized_loaded'
        item['position_hint'] = None
        nodes.append(item)

    node_cap = len(prefix_after) >= node_limit
    relation_cap = len(relations_after) >= relation_limit
    node_more = bool(more_candidates) or len(candidates) > len(prefix_after)
    relation_more = bool(more_relations) or len(in_set) > len(relations_after)
    limit_reached = {'nodes': node_limit, 'relations': relation_limit} \
        if (node_cap or relation_cap) else None
    truncated = bool((node_cap and node_more) or (relation_cap and relation_more))
    reason = 'NODE_LIMIT' if (node_cap and node_more) else \
        'RELATION_LIMIT' if (relation_cap and relation_more) else None
    has_more = node_more
    next_cursor = None
    if limit_reached is None and has_more:
        next_cursor = graph_cursor(fingerprint, loaded_nodes=len(prefix_after),
                                   loaded_relations=len(relations_after))
    return {'nodes': nodes, 'relations': new_relations,
            'loaded': {'nodes': len(prefix_after), 'relations': len(relations_after)},
            'limits': {'nodes': node_limit, 'relations': relation_limit},
            'limit_reached': limit_reached, 'has_more': has_more, 'next_cursor': next_cursor,
            'truncated': truncated, 'truncated_reason': reason,
            # 供调用方在 warnings 里给出提示键（§4 语义 5 的 WIKI_GRAPH_LIMIT_REACHED）。
            'hint': GRAPH_LIMIT_HINT if limit_reached else None}


def assemble_neighborhood(*, root, hop_nodes, relation_rows, depth, max_depth,
                          node_limit=GRAPH_NODE_LIMIT, relation_limit=GRAPH_RELATION_LIMIT,
                          boundary_neighbors=0):
    """邻域（#6）的装配：BFS 结果 → 节点/关系 + 截断与"未到达邻居数"。

    * 节点按 BFS 顺序装入，达到 `node_limit` 后**停止新增**（不继续加载绕过上限）；
    * 关系两端都必须在装入的节点集合内；关系数上限 `relation_limit`；
    * `unreached_neighbors` **只统计当前授权范围内**、因截断而未返回的邻居
      （非授权对象由数据库层事先过滤掉，根本不进这个函数，否则用数字就能推断隐藏图）；
    * `truncated_reason` 优先级 `NODE_LIMIT` > `RELATION_LIMIT` > `DEPTH_LIMIT`。

    `boundary_neighbors`：当请求深度已经是允许的最大深度（2 跳）时，第 2 跳之外仍然存在的
    授权邻居数。它**只**在这一情形下计入 `unreached_neighbors` 并构成 `DEPTH_LIMIT`；
    请求 `depth=1` 时 2 跳邻居不算"被截断"（那是用户要的范围，不是截断）。
    """
    included, seen = [], set()
    dropped_nodes = 0
    root_node = dict(root)
    if node_id(root_node) is not None:
        included.append(root_node)
        seen.add(node_id(root_node))
    for hop in hop_nodes or ():
        for node in hop or ():
            value = node_id(node)
            if value in seen:
                continue
            if len(included) >= node_limit:
                dropped_nodes += 1
                continue
            included.append(dict(node))
            seen.add(value)
    relations = sorted(relations_within_nodes(relation_rows, seen),
                       key=lambda relation: relation.get('relation_id') or '')
    dropped_relations = max(0, len(relations) - relation_limit)
    relations = relations[:relation_limit]

    depth_truncated = bool(boundary_neighbors) and int(depth) >= int(max_depth)
    if dropped_nodes:
        reason = 'NODE_LIMIT'
    elif dropped_relations:
        reason = 'RELATION_LIMIT'
    elif depth_truncated:
        reason = 'DEPTH_LIMIT'
    else:
        reason = None
    degrees = degrees_of(seen, relations)
    nodes = []
    for node in included:
        item = dict(node)
        item['degree'] = degrees.get(node_id(node), 0)
        item['degree_scope'] = 'authorized_loaded'
        item['position_hint'] = None
        nodes.append(item)
    return {'root': node_id(root_node), 'nodes': nodes, 'relations': relations,
            'loaded': {'nodes': len(nodes), 'relations': len(relations)},
            'limits': {'nodes': node_limit, 'relations': relation_limit},
            'truncated': reason is not None, 'truncated_reason': reason,
            'unreached_neighbors': int(dropped_nodes + (int(boundary_neighbors)
                                                        if depth_truncated else 0)),
            'hint': GRAPH_LIMIT_HINT if reason is not None else None}


# ---------------------------------------------------------------------------
# 7. 纯逻辑：来源复查、事件脱敏与错误摘要（§5.7 / §6 / §7.5）
# ---------------------------------------------------------------------------

def source_validity_verdict(row, *, document_state=None, source_state=None,
                            snapshot_ids=()):
    """§7.5 的**读取时复查**（不缓存）：`current` / `stale` / `unavailable`。

    | 情形 | 结论 |
    | --- | --- |
    | 文档行消失 / 数据源行消失 / 快照 id 不在登记表 / 引用没有快照 | `unavailable` |
    | 文档已换版（`doc_version` != 引用记录的版本） | `stale`（返回**旧版本**的定位，禁止静默跳新版） |
    | 其余 | `current` |

    **权限失败这一支不在本层判定**：完整复查需要文档 ACL 的入口（`visibility.py` 的
    `is_published()` 回答的是"匿名公开可读"，与本用户的访问权不是同一个问题，拿它当权限门
    会把"私有但本人可读"的来源误判成 unavailable）。本层只复查**来源主体是否仍在**，
    这处边界写在报告里，不用一个看起来更严的错判来掩盖它。
    """
    kind = row.get('source_kind')
    if kind == 'doc':
        if not document_state:
            return 'unavailable'
        recorded = row.get('document_version')
        current = document_state.get('doc_version')
        if recorded is not None and current is not None and int(recorded) != int(current):
            return 'stale'
        return 'current'
    if kind == 'sql':
        if not source_state:
            return 'unavailable'
        snapshot = row.get('source_snapshot_id')
        if not snapshot or snapshot not in set(snapshot_ids or ()):
            return 'unavailable'
        return 'current'
    return 'unavailable'


def aggregate_source_validity(verdicts):
    """词条级 `status.source_validity`：**同一维度内**的聚合（不得推出另两套状态）。

    `unavailable` > `stale` > `current`；一条引用都没有时返回 `None`（"未评估"），
    **绝不**默认成 `current` —— 那等于伪造"来源可用"这个结论（`wiki.status_dimensions()` 同款）。
    """
    values = [value for value in (verdicts or ()) if value in SOURCE_VALIDITY_ORDER]
    if not values:
        return None
    for state in SOURCE_VALIDITY_ORDER:
        if state in values:
            return state
    return None


def evidence_ref_object(row, *, source_validity=None, excerpt_allowed=True):
    """§5.7 的 `evidence_refs[]` 元素（`wiki_evidence_refs` 一行 → 响应对象）。

    `unavailable` 时**不返回**任何受限正文/摘要/excerpt（§7.5），只留状态与定位元数据；
    `excerpt` 为空字符串是"检索到但没有可引用原文"，与"被扣留"是两件事，所以多一个
    `excerpt_withheld` 字段把两者分开（诚实声明，不合并语义）。
    """
    excerpt = row.get('excerpt') or ''
    withheld = bool(excerpt) and not excerpt_allowed
    return {'evidence_ref_id': row['evidence_ref_id'],
            'fingerprint': wiki.with_sha256_prefix(row['evidence_fingerprint'])
            if wiki.strip_sha256_prefix(row.get('evidence_fingerprint')) else None,
            'source_kind': row.get('source_kind'),
            'source_ref': row.get('source_ref'),
            'source_version': {'document_version': row.get('document_version'),
                               'source_sha256': row.get('source_sha256'),
                               'source_snapshot_id': row.get('source_snapshot_id'),
                               'source_publication_id': row.get('source_publication_id')},
            # §5.7 的示例把 locator_kind 放在 locator 里；库里它是单独一列，这里合并成契约形状。
            'locator': {**(row.get('locator') or {}), 'locator_kind': row.get('locator_kind')},
            'content_sha256': wiki.with_sha256_prefix(row['record_sha256'])
            if wiki.strip_sha256_prefix(row.get('record_sha256')) else None,
            'excerpt': WITHHELD_EXCERPT if withheld else excerpt,
            'excerpt_available': bool(excerpt),
            'excerpt_withheld': withheld,
            'source_validity': source_validity,
            'raw_node_types': list(row.get('raw_node_types') or []),
            'supported': bool(row.get('supported')),
            'unsupported_reason': row.get('unsupported_reason'),
            'run_scoped': bool(row.get('run_scoped')),
            'run_scoped_run_id': row.get('run_scoped_run_id'),
            'provenance_incomplete': row.get('provenance_incomplete') or {'applies': False},
            'created_at': iso_value(row.get('created_at'))}


#: 脱敏：键名命中即丢弃（凭据 / 秘钥 / 内部路径 / 连接串）。
_REDACT_KEY_RE = re.compile(
    r'(secret|token|password|passwd|credential|api[_-]?key|access[_-]?key|authorization'
    r'|cookie|dsn|connection[_-]?string|private[_-]?key|session[_-]?key)', re.I)
#: 值像绝对路径（Windows 盘符 / UNC / POSIX 三段以上）即丢弃：契约 §5.7 约束 3 禁止内部绝对路径。
#: 三段以上的要求是刻意的：`/assets/ok.png` 这类资源 URL 只有一段目录，不该被误杀。
_ABSOLUTE_PATH_RE = re.compile(r'^([A-Za-z]:[\\/]|\\\\|/(?:[A-Za-z0-9._-]+/){2,})')
#: 值因绝对路径被丢弃时留下的记号（由字典分支换成**键名**，避免把敏感值回显给客户端）。
_PATH_TOKEN = '<absolute_path>'
#: 摘要递归与体量边界（防止一条事件把响应撑爆，也防止深数组套娃）。
_SUMMARY_MAX_DEPTH = 4
_SUMMARY_MAX_ITEMS = 64
_SUMMARY_MAX_STRING = 512


def sanitize_summary(payload, *, _depth=0):
    """事件摘要脱敏：返回 `(clean, redacted_keys)`。

    规则：键名命中凭据/路径模式 → 丢弃；字符串值像绝对路径 → 丢弃（记**键名**，不回显那个值）；
    递归深度、元素数与字符串长度都有上限。**丢弃的字段会被列出来**（`redacted_fields`），
    这样"少了一个字段"永远可见，而不是静默消失（契约 §5.7 约束 3 + §8.2 纪律 1 的同一种精神）。
    """
    redacted = []
    if payload is None:
        return None, redacted
    if _depth >= _SUMMARY_MAX_DEPTH:
        return None, redacted
    if isinstance(payload, dict):
        clean = {}
        for index, key in enumerate(payload):
            if index >= _SUMMARY_MAX_ITEMS:
                redacted.append('…')
                break
            name = str(key)
            if _REDACT_KEY_RE.search(name):
                redacted.append(name)
                continue
            value, nested = sanitize_summary(payload[key], _depth=_depth + 1)
            if _PATH_TOKEN in nested:
                redacted.append(name)
                nested = [token for token in nested if token != _PATH_TOKEN]
            redacted.extend(nested)
            clean[name] = value
        return clean, redacted
    if isinstance(payload, (list, tuple)):
        clean = []
        for index, item in enumerate(payload):
            if index >= _SUMMARY_MAX_ITEMS:
                break
            value, nested = sanitize_summary(item, _depth=_depth + 1)
            redacted.extend(nested)
            clean.append(value)
        return clean, redacted
    if isinstance(payload, str):
        if _ABSOLUTE_PATH_RE.match(payload.strip()):
            return None, [_PATH_TOKEN]
        return payload[:_SUMMARY_MAX_STRING], redacted
    return payload, redacted


def event_object(row):
    """§5.7 的 `events[]` 元素：只返回真实事件，`output_summary` 已脱敏。

    `visibility != 'visible'` 的事件由 SQL 侧过滤掉（契约 §5.7 约束 3），本函数不接收它们。
    """
    output, redacted_output = sanitize_summary(row.get('output_summary'))
    source, redacted_input = sanitize_summary(row.get('input_summary'))
    redacted = sorted(set(redacted_output) | set(redacted_input))
    return {'event_id': row['event_id'], 'run_id': row.get('run_id'),
            'event_type': row.get('event_type'), 'status': row.get('status'),
            'agent_name': row.get('agent_name'), 'tool_name': row.get('tool_name'),
            'started_at': iso_value(row.get('started_at')),
            'ended_at': iso_value(row.get('ended_at')),
            'duration_ms': row.get('duration_ms'),
            'input_summary': source, 'output_summary': output,
            'error_code': row.get('error_code'), 'retry_index': row.get('retry_index'),
            'visibility': row.get('visibility', 'visible'),
            'redacted_fields': redacted, 'redaction_applied': bool(redacted)}


def error_summary_of(event_rows):
    """§5.7 约束 2 / `WIKI_DOMAIN` §7.1：错误摘要**必须存在且非空语义**。

    * 有错误 → `{'error_code', 'event_type', 'agent_name', 'at', 'detail'}`；
    * 无错误 → **显式** `{'error_code': None, 'none': True}`（不是 `null`、不是缺字段）；
    * 读取失败 → `{'error_code': None, 'unavailable': True}`（**绝不**显示成"无错误"）。
    """
    for row in event_rows or ():
        if row.get('error_code'):
            detail, _ = sanitize_summary(row.get('output_summary'))
            return {'error_code': row.get('error_code'),
                    'event_type': row.get('event_type'),
                    'agent_name': row.get('agent_name'),
                    'at': iso_value(row.get('started_at')),
                    'detail': detail}
    return {'error_code': None, 'none': True}


def unavailable_error_summary():
    """读取失败时的错误摘要（与"无错误"字节不同，见 `error_summary_of`）。"""
    return {'error_code': None, 'unavailable': True}


def event_types_in_order(rows):
    """事件的 `event_types`：按时间顺序去重（契约 §5.7 的示例是运行内的真实顺序）。"""
    seen, ordered = set(), []
    for row in rows or ():
        value = row.get('event_type')
        if value and value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def run_object(row, *, error_summary, event_count=0, event_types=()):
    """§5.7 的 `runs[]` 元素（运行摘要 + 该运行的错误摘要）。

    `error_summary` 是**必填**：错误摘要只有三种合法形态（有错误 / 显式无错误 / 读取失败不可用），
    调用方必须显式给出它从哪来，不能靠默认值假装"查过了、没有错误"。
    `conversation_id` 为 `NULL` 的运行**没有会话归属**：`attribution_note` 如实说明，
    不猜归属（契约 §11.6 对工业运行的明确要求）。
    """
    conversation = row.get('conversation_id')
    return {'run_id': row['run_id'], 'question': row.get('question'),
            'route': row.get('route'), 'status': row.get('status'),
            'confidence_level': row.get('confidence_level'),
            'started_at': iso_value(row.get('started_at')),
            'ended_at': iso_value(row.get('ended_at')),
            'elapsed_ms': _elapsed_ms(row.get('started_at'), row.get('ended_at')),
            'error_summary': error_summary,
            'event_count': int(event_count or 0),
            'event_types': list(event_types or ()),
            'conversation_id': conversation,
            'conversation_attributed': conversation is not None,
            'attribution_note': None if conversation else 'NO_CONVERSATION_ATTRIBUTION'}


def _elapsed_ms(started, ended):
    if isinstance(started, datetime) and isinstance(ended, datetime):
        return round((ended - started).total_seconds() * 1000.0, 1)
    return None


def provenance_incomplete_summary(*, markers=(), edge_columns_missing=(), has_projected_edges=False,
                                 has_evidence=False, detected_at=None):
    """§5.7 / §6 的 `provenance_incomplete` 汇总（**不得推测补全**）。

    * 有任何一条记录自带 `applies: true` 的标记 → 采用它（`missing_fields` 取并集）；
    * 否则，如果部署库的 `evidence_edges` 缺 `evidence` / `binding_type` / `confidence` 三列
      **且**该版本确实有投影出来的关系 → `EDGE_COLUMNS_ABSENT_IN_DEPLOYED_SCHEMA`
      （这是实测事实：`019:90-96` 声明了这三列，线上没有）；
    * 否则 `{'applies': False}`：**没有缺失就说没有**，不为了"看起来严谨"而假报缺失。
    """
    fields, reasons = [], []
    for marker in markers or ():
        if not isinstance(marker, dict) or not marker.get('applies'):
            continue
        fields.extend(marker.get('missing_fields') or [])
        if marker.get('reason_code'):
            reasons.append(marker['reason_code'])
    if not fields and has_projected_edges and tuple(edge_columns_missing or ()):
        fields = ['edge.%s' % name for name in edge_columns_missing]
        reasons.append(REASON_EDGE_COLUMNS_ABSENT)
    if not fields:
        return projection_provenance_incomplete(applies=False)
    payload = projection_provenance_incomplete(
        applies=True, missing_fields=fields, reason_code=reasons[0] if reasons else None,
        detected_at=iso_value(detected_at) if detected_at is not None else None)
    if not has_evidence:
        payload['note'] = 'NO_EVIDENCE_RECORDED_FOR_THIS_REVISION'
    return payload


def provenance_summary(*, evidence_refs=(), runs=(), events=(), source_validity=None,
                       provenance_incomplete=None, events_unavailable=False,
                       evidence_refs_truncated=False, event_count=None, error_count=None,
                       events_materialized=True):
    """§5.7 的 `summary` 段。计数只统计**本次授权投影后**实际返回的对象。

    `event_count` / `error_count` 都可以由调用方给出**总数**（与当前这一页无关）：
    分页时用本页条数冒充总数就是撒谎。读取失败时 `error_count` 必须是 `None`
    （"没数过"），不是 `0`（"数过了，没有"）。
    """
    validity = {'current': 0, 'stale': 0, 'unavailable': 0}
    for value in source_validity or ():
        if value in validity:
            validity[value] += 1
    incomplete = provenance_incomplete if isinstance(provenance_incomplete, dict) \
        else {'applies': False}
    total_events = int(event_count) if event_count is not None else len(list(events or ()))
    if events_unavailable:
        errors = None
    elif error_count is not None:
        errors = int(error_count)
    else:
        errors = len([event for event in events or () if event.get('error_code')])
    return {'evidence_ref_count': len(list(evidence_refs or ())),
            'run_count': len(list(runs or ())),
            'event_count': total_events,
            'error_count': errors,
            'source_validity': validity,
            'provenance_complete': not bool(incomplete.get('applies')),
            'provenance_incomplete': incomplete,
            'evidence_refs_truncated': bool(evidence_refs_truncated),
            'events_unavailable': bool(events_unavailable),
            # `layer=evidence` 时不翻事件页：这不是"没有事件"，所以单独一个字段说清楚。
            'events_materialized': bool(events_materialized)}


# ---------------------------------------------------------------------------
# 8. 纯逻辑：Markdown 导出（§5.8 必须保留的六项）
# ---------------------------------------------------------------------------

def _citation_index(citations):
    """`citations` JSONB → `{evidence_ref_id: marker}`（标记按版本自己记录的原文，不重排）。"""
    index = {}
    for citation in citations or ():
        if not isinstance(citation, dict):
            continue
        ref = citation.get('evidence_ref_id')
        if ref:
            index.setdefault(ref, citation.get('marker'))
    return index


def _status_table(status, revision_status):
    """三套状态**分别**标注（§5.8 第 1 条 / §5.4 的禁令：不得合并成一个总状态）。"""
    lines = ['| 维度 | 值 | 说明 |', '| --- | --- | --- |',
             '| 发布流程状态 `publication` | `%s` | 编辑与发布流程走到哪一步，**不表示事实成立** |'
             % (status.get('publication') or '（未评估）'),
             '| 主张核验状态 `assertion` | `%s` | 在明确核验范围内证据是否支撑主张 |'
             % (status.get('assertion') or '（未评估）'),
             '| 来源有效性 `source_validity` | `%s` | 读取时复查来源快照/版本，**不缓存** |'
             % (status.get('source_validity') or '（未评估：本版本没有可复查的证据引用）')]
    if revision_status and revision_status != status.get('publication'):
        lines.append('| 本版本自己的流程状态 | `%s` | 该版本记录的流程状态快照（与词条当前状态不同） |'
                     % revision_status)
    return '\n'.join(lines)


def _marker_for(ref_id, index, fallback):
    marker = index.get(ref_id)
    return marker or ('[%d]' % fallback)


def _run_error_line(error_summary):
    """运行错误摘要的一行文字：三种形态**互不混淆**（§7.1 的降级规则）。"""
    summary = error_summary if isinstance(error_summary, dict) else {}
    if summary.get('unavailable'):
        return '；**错误摘要不可用**（读取失败，不得显示成"无错误"）'
    if summary.get('none'):
        return '；本次运行无错误事件'
    if summary.get('error_code'):
        return '；错误摘要：`%s`（%s / %s）' % (summary.get('error_code'),
                                                summary.get('event_type'),
                                                summary.get('agent_name'))
    return '；错误摘要未记录'


def _locator_line(evidence_ref):
    locator = evidence_ref.get('locator') or {}
    kind = locator.get('locator_kind') or evidence_ref.get('locator_kind') or 'unknown'
    parts = []
    for key in ('page_no', 'block_id', 'sql_hash', 'row_count', 'aggregation', 'bbox'):
        if key in locator and locator[key] is not None:
            parts.append('%s=%s' % (key, locator[key]))
    if evidence_ref.get('locator_kind'):
        parts.append('locator_kind=%s' % evidence_ref['locator_kind'])
    return '%s（%s）' % (kind, '，'.join(parts)) if parts else str(kind)


def render_export_markdown(*, entry, revision, relations=(), evidence_refs=(), runs=(),
                           status, provenance_incomplete=None, exported_at=None,
                           demo_data=None, unavailable_note=None):
    """§5.8 的 Markdown 正文：六项必保留内容逐条落在这里。

    1. 知识状态（三套状态分别标注，**不合并**）；
    2. 引用（`[1]` 标记 + 引用清单，含来源版本与定位）；
    3. 来源版本（`source_sha256` / `document_version` / 快照 id）；
    4. 运行标识（`run_id` 逐个列出）；
    5. 演示属性（`demo_data: true` 时必须写进正文）；
    6. `provenance_incomplete` 的声明（§6.3 规则 3：Markdown 里也不得省略）。

    **不扩大访问范围**：调用方传进来的 `evidence_refs` / `runs` 已经过与页面相同的授权投影
    （见 `entry_export()`），本函数不做也不放宽任何过滤。
    """
    entry = entry or {}
    revision = revision or {}
    status = status or {}
    citations = _citation_index(revision.get('citations'))
    lines = []
    title = revision.get('title') or entry.get('title') or '（无标题）'
    lines.append('# %s' % title)
    lines.append('')
    if demo_data if demo_data is not None else entry.get('demo_data'):
        lines.append('> **演示数据（`demo_data: true`）**：本词条、引用与运行全部为合成演示数据，'
                     '不含真实工厂数据、真实审批或独立准确率结论。')
        lines.append('')
    lines.append('- 词条：`%s`（类型 `%s`）' % (entry.get('entry_id'), entry.get('entry_type')))
    business = entry.get('business_key') or {}
    lines.append('- 业务键：`%s` / `%s` / `%s` / `%s`（实体键确认状态 `%s`）'
                 % (business.get('space_id'), business.get('namespace'),
                    business.get('entry_type'), business.get('key_part'),
                    business.get('confirmation')))
    lines.append('- 版本：`%s`（基线 `%s`；正文 `sha256:%s`）'
                 % (revision.get('revision_id'), revision.get('base_revision_id'),
                    revision.get('body_sha256')))
    author = revision.get('author') or {}
    generation = revision.get('generation') or {}
    lines.append('- 作者：`%s`；生成方式：`%s`%s'
                 % (author.get('user_id'), generation.get('method'),
                    '（模板版本 `%s`）' % generation.get('template_version')
                    if generation.get('template_version') else ''))
    lines.append('- 导出时间：%s' % (iso_value(exported_at) or now_iso()))
    lines.append('')
    lines.append('## 知识状态（三套独立状态，不得合并）')
    lines.append('')
    lines.append(_status_table(status, revision.get('publication_status')))
    lines.append('')
    lines.append('## 正文')
    lines.append('')
    lines.append(revision.get('body_markdown') or '')
    lines.append('')
    lines.append('## 引用')
    lines.append('')
    if not evidence_refs:
        lines.append('本版本没有证据引用。')
    else:
        lines.append('| 标记 | 证据引用 | 来源 | 来源版本 | 定位 | 来源有效性 |')
        lines.append('| --- | --- | --- | --- | --- | --- |')
        for index, evidence_ref in enumerate(evidence_refs, start=1):
            version = evidence_ref.get('source_version') or {}
            lines.append('| %s | `%s` | `%s` / `%s` | document_version=%s, source_sha256=%s, '
                         'snapshot=%s | %s | `%s` |'
                         % (_marker_for(evidence_ref.get('evidence_ref_id'), citations, index),
                            evidence_ref.get('evidence_ref_id'), evidence_ref.get('source_kind'),
                            evidence_ref.get('source_ref'), version.get('document_version'),
                            version.get('source_sha256'), version.get('source_snapshot_id'),
                            _locator_line(evidence_ref),
                            evidence_ref.get('source_validity')))
        lines.append('')
        lines.append('### 引用原文')
        lines.append('')
        for index, evidence_ref in enumerate(evidence_refs, start=1):
            marker = _marker_for(evidence_ref.get('evidence_ref_id'), citations, index)
            if evidence_ref.get('excerpt_withheld'):
                lines.append('%s `%s`：来源不可用，**按契约不返回受限正文**。'
                             % (marker, evidence_ref.get('evidence_ref_id')))
                continue
            excerpt = evidence_ref.get('excerpt') or ''
            lines.append('%s `%s`：%s' % (marker, evidence_ref.get('evidence_ref_id'),
                                          '（检索到该证据，但没有可引用的原文片段）'
                                          if not excerpt else excerpt.replace('\n', ' ')))
        lines.append('')
    lines.append('## 来源版本')
    lines.append('')
    if not evidence_refs:
        lines.append('无。')
    for evidence_ref in evidence_refs:
        version = evidence_ref.get('source_version') or {}
        lines.append('- `%s`：`source_kind=%s`，`source_ref=%s`，`document_version=%s`，'
                     '`source_sha256=%s`，`source_snapshot_id=%s`，`source_publication_id=%s`，'
                     '`record_sha256=%s`'
                     % (evidence_ref.get('evidence_ref_id'), evidence_ref.get('source_kind'),
                        evidence_ref.get('source_ref'), version.get('document_version'),
                        version.get('source_sha256'), version.get('source_snapshot_id'),
                        version.get('source_publication_id'),
                        evidence_ref.get('content_sha256')))
    lines.append('')
    lines.append('## 运行标识')
    lines.append('')
    if not runs:
        lines.append('本版本的证据没有登记任何运行映射。')
    for run in runs:
        lines.append('- `%s`：问题「%s」，状态 `%s`，事件 %s 条%s'
                     % (run.get('run_id'), run.get('question'), run.get('status'),
                        run.get('event_count'), _run_error_line(run.get('error_summary'))))
        if run.get('attribution_note'):
            lines.append('  - 归属：该运行没有会话归属（`%s`），按触发整理的用户空间归属。'
                         % run['attribution_note'])
    lines.append('')
    lines.append('## 出处完整性声明（provenance_incomplete）')
    lines.append('')
    incomplete = provenance_incomplete if isinstance(provenance_incomplete, dict) \
        else {'applies': False}
    if incomplete.get('applies'):
        lines.append('**出处信息不完整**：有一部分从未被持久化，因此不可恢复（不是数据损坏，'
                     '也不可用推断补全）。')
        lines.append('')
        lines.append('- 缺失字段：%s' % '，'.join('`%s`' % field
                                                 for field in incomplete.get('missing_fields')
                                                 or []))
        lines.append('- 理由码：`%s`' % incomplete.get('reason_code'))
        lines.append('- 可恢复：`%s`' % bool(incomplete.get('recoverable')))
        if incomplete.get('detected_at'):
            lines.append('- 判定时间：%s' % incomplete['detected_at'])
    else:
        lines.append('本版本引用的证据与关系未发现缺失的出处字段（`applies: false`）。')
        if incomplete.get('note'):
            lines.append('')
            lines.append('- 备注：`%s`' % incomplete['note'])
    if unavailable_note:
        lines.append('')
        lines.append('> %s' % unavailable_note)
    lines.append('')
    lines.append('## 关系')
    lines.append('')
    if not relations:
        lines.append('本版本没有已保存的关系。')
    for relation in relations:
        lines.append('- `%s` %s `%s`（`%s`，%s，`origin=%s`，`raw_type=%s`，`supported=%s`）'
                     % (relation.get('src'), relation.get('label') or
                        relation_label(relation.get('relation_type'),
                                       supported=relation.get('supported', True)),
                        relation.get('dst'), relation.get('relation_type'),
                        relation.get('direction'), relation.get('origin'),
                        relation.get('raw_type'), relation.get('supported')))
    lines.append('')
    return '\n'.join(lines).rstrip('\n') + '\n'


def export_headers(entry_id_value):
    """§2.3 / §5.8：导出**必须** `no-store` + `Content-Disposition: attachment`。

    导出内容含私有来源文本，不得被代理缓存；文件名用词条 id（`wiki-<id>.md`）。
    """
    return {'Cache-Control': 'no-store',
            'Content-Disposition': 'attachment; filename="%s.md"' % entry_id_value}


def export_filename(entry_id_value):
    return '%s.md' % entry_id_value


def export_ref_guard(citation_count):
    """§4 / §5.8：导出上限 **300** 条证据引用；超出 → `WIKI_EXPORT_TOO_LARGE`。

    超限时**不截断后声称完整**：要么全给，要么明确报错。纯函数，因此这条边界不需要数据库
    就能被测试钉住。
    """
    if int(citation_count or 0) > EXPORT_EVIDENCE_MAX:
        wiki.fail('WIKI_EXPORT_TOO_LARGE', 422,
                  hint='this revision cites %d evidence references; the export limit is %d'
                       % (int(citation_count), EXPORT_EVIDENCE_MAX))
    return int(citation_count or 0)


# ---------------------------------------------------------------------------
# 9. 数据库路径：共用的加载与投影（所有读接口都从这里取行，不各写一套过滤）
# ---------------------------------------------------------------------------

#: `LIKE`/`ILIKE` 的转义字符子句。Python 里写成 `"ESCAPE '\\'"`，SQL 收到的是 `ESCAPE '\'`
#: （单反斜杠）。与 `sessions.py:224-227` 的写法同款：`%`/`_`/`\` 都是字面量。
ILIKE_ESCAPE = "ESCAPE '\\'"

#: §5.1 的 `q` 匹配范围：**标题、别名、分类**（正文不在内，见 `SEARCH_UNCOVERED_FIELDS`）。
_QUERY_MATCH_SQL = (
    "(e.title ILIKE %(pattern)s {esc}"
    " OR EXISTS (SELECT 1 FROM unnest(e.aliases) AS wiki_alias(value)"
    "            WHERE wiki_alias.value ILIKE %(pattern)s {esc})"
    " OR COALESCE(e.category, '') ILIKE %(pattern)s {esc})").format(esc=ILIKE_ESCAPE)


def authorized_entry(cur, entry_id_value, identity, *, space_id=None):
    """词条级读取的**唯一授权入口**：直接复用 #3/#4/#5 已在用的 `wiki._load_entry()`。

    这样"详情能读"与"图谱/来源/导出能读"不可能分叉：它们是同一个函数、同一条 SQL
    （`wiki.py:961-975`：`entry_id = %s AND (%s OR (space_id = %s AND owner_id = %s))`）。
    别人的 / 不存在的 / 空间不对的一律 `404 WIKI_ENTRY_NOT_FOUND`（存在性隐藏）。
    """
    return wiki._load_entry(cur, entry_id_value, identity, space_id=space_id)


def _fetch(cur, sql, params=None):
    cur.execute(sql, params or {})
    return cur.fetchall()


def _scalar(cur, sql, params=None):
    cur.execute(sql, params or {})
    row = cur.fetchone()
    return None if row is None else list(row.values())[0]


def entry_objects(cur, scope, rows):
    """一批词条行 → §5.1 的 entry 对象（目录、图谱节点、详情三处**同一投影**）。

    授权投影在这里出现两次，且两次都来自同一个 `scope`：

    1. SQL 侧（调用方的 WHERE）用 `entry_authorization_predicate()`；
    2. Python 侧在这里用 `entry_is_authorized()` 再兜一道。

    第二道不是重复劳动：它是"投影前看到 vs 过滤后返回"的 `meta.authorized_scope` 自证
    （契约 §5），并且保证任何调用方漏了 SQL 条件时也不会把别人的词条投影出去。
    """
    rows = [dict(row) for row in rows or ()]
    visible = [row for row in rows if entry_is_authorized(scope, row)]
    if not visible:
        return []
    ids = [row['entry_id'] for row in visible]
    view = _view_revisions(cur, scope, ids)
    revisions = [value for value in view.values() if value]
    revision_counts = _revision_counts(cur, ids)
    assertion_status = _assertion_status_by_revision(cur, revisions)
    ref_counts = _citation_ref_counts(cur, revisions)
    relation_counts = _authorized_relation_counts(cur, scope, revisions)
    validity = _view_source_validity(cur, scope, revisions)
    objects = []
    for row in visible:
        revision = view.get(row['entry_id'])
        objects.append(wiki.entry_object(
            row,
            assertion_status=assertion_status.get(revision, 'unverified'),
            source_validity=validity.get(revision),
            revision_count=revision_counts.get(row['entry_id'], 0),
            evidence_ref_count=ref_counts.get(revision, 0),
            relation_count=relation_counts.get(revision, 0)))
    return objects


def _view_revisions(cur, scope, entry_ids):
    """每个词条的"当前视图版本"（发布头优先，否则最新版本，见 `view_revision_sql`）。"""
    if not entry_ids:
        return {}
    sql = ('SELECT e.entry_id, {view} AS view_revision_id FROM wiki_entries e'
           ' WHERE e.entry_id = ANY(%(ids)s) AND {auth}').format(
        view=view_revision_sql('e'), auth=entry_authorization_predicate(scope, alias='e'))
    return {row['entry_id']: row['view_revision_id']
            for row in _fetch(cur, sql, {'ids': list(entry_ids),
                                         **authorization_params(scope)})}


def _revision_counts(cur, entry_ids):
    if not entry_ids:
        return {}
    rows = _fetch(cur, '''SELECT entry_id, count(*) AS counted FROM wiki_revisions
        WHERE entry_id = ANY(%(ids)s) GROUP BY entry_id''', {'ids': list(entry_ids)})
    return {row['entry_id']: int(row['counted']) for row in rows}


def _assertion_status_by_revision(cur, revision_ids):
    """同一维度内的聚合（`wiki.aggregate_assertion_status`），不推出另两套状态。"""
    if not revision_ids:
        return {}
    rows = _fetch(cur, '''SELECT revision_id, assertion_status FROM wiki_assertions
        WHERE revision_id = ANY(%(ids)s)''', {'ids': list(revision_ids)})
    grouped = {}
    for row in rows:
        grouped.setdefault(row['revision_id'], []).append(row['assertion_status'])
    return {revision: wiki.aggregate_assertion_status(values)
            for revision, values in grouped.items()}


def _citation_ref_counts(cur, revision_ids):
    if not revision_ids:
        return {}
    rows = _fetch(cur, '''SELECT r.revision_id,
            count(DISTINCT citation ->> 'evidence_ref_id') AS counted
        FROM wiki_revisions r, jsonb_array_elements(r.citations) AS citation
        WHERE r.revision_id = ANY(%(ids)s) GROUP BY r.revision_id''',
                  {'ids': list(revision_ids)})
    return {row['revision_id']: int(row['counted']) for row in rows}


def _authorized_relation_counts(cur, scope, revision_ids):
    """**只统计当前授权范围内已加载的连接**（§4 语义 7）：关系两个端点都要过授权投影。

    这是"目录里的连接数"与"图谱里的 degree"共用同一条规则的地方——隐藏对象的边不进计数，
    所以数字本身不携带隐藏图的存在性信息。
    """
    if not revision_ids:
        return {}
    sql = ('SELECT r.revision_id, count(*) AS counted FROM wiki_relations r'
           ' WHERE r.revision_id = ANY(%(ids)s) AND {auth} GROUP BY r.revision_id').format(
        auth=relation_endpoint_authorization_predicate(scope, alias='r'))
    rows = _fetch(cur, sql, {'ids': list(revision_ids), **authorization_params(scope)})
    return {row['revision_id']: int(row['counted']) for row in rows}


def _evidence_ref_rows(cur, scope, ref_ids):
    """按授权空间读取证据引用（证据引用的授权维度只有空间，`021:311-312`）。"""
    ref_ids = [value for value in ref_ids or () if value]
    if not ref_ids:
        return []
    rows = _fetch(cur, '''SELECT * FROM wiki_evidence_refs
        WHERE evidence_ref_id = ANY(%(ids)s) AND space_id = %(space)s
        ORDER BY evidence_ref_id''',
                  {'ids': list(ref_ids), 'space': scope['space_id']})
    return [dict(row) for row in rows]


def _source_states(cur, ref_rows):
    """§7.5 的复查素材：文档版本、数据源存在性、快照登记（全部只读 SELECT）。"""
    documents, sources, snapshots = {}, set(), set()
    doc_ids = sorted({row.get('source_ref') for row in ref_rows
                      if row.get('source_kind') == 'doc' and row.get('source_ref')})
    if doc_ids:
        for row in _fetch(cur, '''SELECT document_id, doc_version, status, acl
            FROM document_assets WHERE document_id = ANY(%(ids)s)''', {'ids': doc_ids}):
            documents[row['document_id']] = dict(row)
    sql_ids = sorted({row.get('source_ref') for row in ref_rows
                      if row.get('source_kind') == 'sql' and row.get('source_ref')})
    if sql_ids:
        for row in _fetch(cur, 'SELECT source_id FROM data_sources WHERE source_id = ANY(%(ids)s)',
                          {'ids': sql_ids}):
            sources.add(row['source_id'])
    snapshot_ids = sorted({row.get('source_snapshot_id') for row in ref_rows
                           if row.get('source_snapshot_id')})
    if snapshot_ids:
        for row in _fetch(cur, '''SELECT snapshot_id FROM schema_snapshots
            WHERE snapshot_id = ANY(%(ids)s)''', {'ids': snapshot_ids}):
            snapshots.add(row['snapshot_id'])
    return documents, sources, snapshots


def _ref_validity_map(cur, scope, ref_rows):
    """引用 id → §7.5 的读取时复查结论（不缓存）。"""
    documents, sources, snapshots = _source_states(cur, ref_rows)
    verdicts = {}
    for row in ref_rows:
        verdicts[row['evidence_ref_id']] = source_validity_verdict(
            row, document_state=documents.get(row.get('source_ref')),
            source_state=(row.get('source_ref') in sources) if row.get('source_kind') == 'sql'
            else None,
            snapshot_ids=snapshots)
    return verdicts


def _view_source_validity(cur, scope, revision_ids):
    """每个视图版本的 `status.source_validity`（词条级聚合，见 `aggregate_source_validity`）。"""
    if not revision_ids:
        return {}
    rows = _fetch(cur, '''SELECT r.revision_id, citation ->> 'evidence_ref_id' AS evidence_ref_id
        FROM wiki_revisions r, jsonb_array_elements(r.citations) AS citation
        WHERE r.revision_id = ANY(%(ids)s)''', {'ids': list(revision_ids)})
    by_revision = {}
    for row in rows:
        by_revision.setdefault(row['revision_id'], set()).add(row['evidence_ref_id'])
    ref_rows = _evidence_ref_rows(cur, scope, sorted(
        {ref for refs in by_revision.values() for ref in refs if ref}))
    verdicts = _ref_validity_map(cur, scope, ref_rows)
    return {revision: aggregate_source_validity(
        [verdicts.get(ref) for ref in refs if ref in verdicts])
        for revision, refs in by_revision.items()}


# ---------------------------------------------------------------------------
# 10. 数据库路径：#1 目录与关键词搜索
# ---------------------------------------------------------------------------

def _catalogue_where(scope, filters, *, apply_authorization=True):
    clauses = ['e.space_id = %(space)s']
    params = {'space': scope['space_id'], **authorization_params(scope)}
    if apply_authorization:
        clauses.append(entry_authorization_predicate(scope, alias='e'))
    if filters.get('entry_type'):
        clauses.append('e.entry_type = ANY(%(types)s)')
        params['types'] = list(filters['entry_type'])
    if filters.get('status'):
        clauses.append('e.publication_status = ANY(%(statuses)s)')
        params['statuses'] = list(filters['status'])
    if filters.get('category'):
        clauses.append('e.category = %(category)s')
        params['category'] = filters['category']
    if filters.get('updated_since'):
        clauses.append('e.updated_at >= %(updated_since)s::timestamptz')
        params['updated_since'] = filters['updated_since']
    if filters.get('q'):
        pattern = search_pattern(filters['q'])
        clauses.append(_QUERY_MATCH_SQL)
        params['pattern'] = pattern
    return ' AND '.join(clauses), params


def entry_catalogue(dsn, identity, *, q=None, entry_type=None, status=None, category=None,
                    updated_since=None, space_id=None, limit=CATALOGUE_LIMIT_DEFAULT,
                    cursor=None):
    """#1 `GET /v1/wiki/entries`：授权词条目录与关键词搜索（契约 §5.1）。

    * 目录默认 20、最多 50（§4）；
    * `status` **只**过滤发布流程状态；按核验状态筛选要显式传 `assertion_status`（§7.1）——
      本函数只实现契约冻结的那个参数，不接受把三套状态混起来的筛法；
    * `q` 只覆盖标题 / 别名 / 分类，响应里的 `search` 段如实声明（§5.1）；
    * 请求别人的 `space_id` → `404 WIKI_SPACE_NOT_FOUND`；
    * 游标在筛选变化时被重置（§4 语义 4）。
    """
    scope = authorized_scope(identity, space_id)
    if type(limit) is not int or not 1 <= limit <= CATALOGUE_LIMIT_MAX:
        _invalid('limit must be between 1 and %d' % CATALOGUE_LIMIT_MAX)
    filters = normalize_filters(q=q, entry_type=entry_type, status=status, category=category,
                                updated_since=updated_since, space_id=scope['space_id'])
    fingerprint = filter_fingerprint(filters)
    marker, reset = resolve_cursor(cursor, fingerprint=fingerprint, keys=LISTING_CURSOR_KEYS)
    position = listing_marker(marker)
    warnings = [CURSOR_RESET_ON_FILTER_CHANGE] if reset else []

    where, params = _catalogue_where(scope, filters)
    considered = None
    with wiki.connect(dsn) as conn, conn.cursor(cursor_factory=wiki.RealDictCursor) as cur:
        considered_where, considered_params = _catalogue_where(scope, filters,
                                                               apply_authorization=False)
        considered = int(_scalar(cur, 'SELECT count(*) FROM wiki_entries e WHERE '
                                 + considered_where, considered_params))
        visible = int(_scalar(cur, 'SELECT count(*) FROM wiki_entries e WHERE ' + where, params))
        sql = ('SELECT ' + entry_columns_sql('e') + ' FROM wiki_entries e WHERE ' + where)
        if position:
            sql += (' AND (e.updated_at, e.entry_id) <'
                    ' (%(cursor_updated_at)s::timestamptz, %(cursor_entry_id)s)')
            params = {**params, 'cursor_updated_at': position[0], 'cursor_entry_id': position[1]}
        sql += ' ORDER BY e.updated_at DESC, e.entry_id DESC LIMIT %(limit)s'
        rows = _fetch(cur, sql, {**params, 'limit': limit + 1})
        window, has_more = window_and_more(rows, limit)
        entries = entry_objects(cur, scope, window)

    next_cursor = None
    last = last_cursor_row(window)
    if has_more and last is not None:
        next_cursor = listing_cursor(fingerprint, sort_key=last['updated_at'],
                                     tie_break=last['entry_id'])
    warnings.extend(scope_warnings(considered=considered, visible=visible))
    return envelope(data={'entries': entries, 'search': search_declaration(filters.get('q'))},
                    meta=authorized_scope_meta(scope, considered=considered, visible=visible),
                    paging=paging_object(limit=limit, has_more=has_more,
                                         next_cursor=next_cursor, truncated=False),
                    warnings=warnings)


# ---------------------------------------------------------------------------
# 11. 数据库路径：#2 当前授权范围图
# ---------------------------------------------------------------------------

def _graph_entry_candidates(cur, scope, filters):
    """词条图层的候选节点：授权 + 空间 + 筛选，按确定顺序取"前缀 + 1"探测。"""
    where, params = _catalogue_where(scope, filters)
    sql = ('SELECT ' + entry_columns_sql('e') + ' FROM wiki_entries e WHERE ' + where
           + ' ORDER BY e.updated_at DESC, e.entry_id DESC LIMIT %(cap)s')
    rows = _fetch(cur, sql, {**params, 'cap': GRAPH_NODE_LIMIT + 1})
    more = len(rows) > GRAPH_NODE_LIMIT
    window = [dict(row) for row in rows[:GRAPH_NODE_LIMIT]]
    considered = len(window)
    objects = {obj['entry_id']: obj for obj in entry_objects(cur, scope, window)}
    nodes = [{'node_kind': 'entry', 'node_id': row['entry_id'], 'entry': objects[row['entry_id']]}
             for row in window if row['entry_id'] in objects]
    return nodes, more, considered


def _graph_evidence_candidates(cur, scope):
    """证据图层的候选节点：授权空间内的证据引用（`wiki_evidence_refs.space_id`）。"""
    rows = _fetch(cur, '''SELECT * FROM wiki_evidence_refs WHERE space_id = %(space)s
        ORDER BY created_at DESC, evidence_ref_id DESC LIMIT %(cap)s''',
                  {'space': scope['space_id'], 'cap': GRAPH_NODE_LIMIT + 1})
    more = len(rows) > GRAPH_NODE_LIMIT
    window = [dict(row) for row in rows[:GRAPH_NODE_LIMIT]]
    verdicts = _ref_validity_map(cur, scope, window)
    nodes = [{'node_kind': 'evidence_ref', 'node_id': row['evidence_ref_id'],
              'evidence_ref': evidence_ref_object(
                  row, source_validity=verdicts.get(row['evidence_ref_id']),
                  excerpt_allowed=verdicts.get(row['evidence_ref_id']) != 'unavailable')}
             for row in window]
    return nodes, more, len(window)


#: 运行图层的候选节点：**运行 + 证据引用**两类（`wiki_provenance_links` 的两端）。
#: 用 UNION ALL + 统一排序键，保证"前缀 = 已加载集合"这条判定在混合节点类型下仍成立。
_RUNS_LAYER_CANDIDATES_SQL = '''
    SELECT node_kind, node_id, sort_a, sort_b FROM (
        SELECT 'run' AS node_kind, links.run_id AS node_id,
               max(COALESCE(run_row.started_at, links.created_at)) AS sort_a,
               links.run_id AS sort_b
          FROM (SELECT l.run_id, min(l.created_at) AS created_at
                  FROM wiki_provenance_links l
                  JOIN wiki_evidence_refs er ON er.evidence_ref_id = l.evidence_ref_id
                 WHERE er.space_id = %(space)s GROUP BY l.run_id) links
          LEFT JOIN runs run_row ON run_row.run_id = links.run_id
         GROUP BY links.run_id
        UNION ALL
        SELECT 'evidence_ref', er.evidence_ref_id, er.created_at, er.evidence_ref_id
          FROM wiki_evidence_refs er WHERE er.space_id = %(space)s
    ) candidate
    ORDER BY sort_a DESC NULLS LAST, sort_b DESC
    LIMIT %(cap)s'''


def _run_rows(cur, run_ids):
    """运行摘要行（含 `query.parsed` 的路由，契约 §5.7 的 `route` 字段）。"""
    run_ids = [value for value in run_ids or () if value]
    if not run_ids:
        return {}
    rows = _fetch(cur, '''SELECT r.run_id, r.conversation_id, r.question, r.status,
            r.confidence_level, r.started_at, r.ended_at,
            (SELECT e.output_summary ->> 'route' FROM trace_events e
              WHERE e.run_id = r.run_id AND e.event_type = 'query.parsed'
                AND COALESCE(e.visibility, 'visible') = 'visible'
              ORDER BY e.event_id LIMIT 1) AS route
        FROM runs r WHERE r.run_id = ANY(%(ids)s)''', {'ids': list(run_ids)})
    return {row['run_id']: dict(row) for row in rows}


def _run_error_rows(cur, run_ids):
    """这些运行的**可见错误事件**（只取 error_code 非空的行，用于错误摘要）。"""
    run_ids = [value for value in run_ids or () if value]
    if not run_ids:
        return {}
    rows = _fetch(cur, '''SELECT run_id, event_id, event_type, agent_name, started_at,
            output_summary, error_code FROM trace_events
        WHERE run_id = ANY(%(ids)s) AND COALESCE(visibility, 'visible') = 'visible'
          AND error_code IS NOT NULL
        ORDER BY started_at, event_id''', {'ids': list(run_ids)})
    grouped = {}
    for row in rows:
        grouped.setdefault(row['run_id'], []).append(dict(row))
    return grouped


def _runs_layer_candidates(cur, scope):
    rows = _fetch(cur, _RUNS_LAYER_CANDIDATES_SQL,
                  {'space': scope['space_id'], 'cap': GRAPH_NODE_LIMIT + 1})
    more = len(rows) > GRAPH_NODE_LIMIT
    window = [dict(row) for row in rows[:GRAPH_NODE_LIMIT]]
    run_ids = [row['node_id'] for row in window if row['node_kind'] == 'run']
    ref_ids = [row['node_id'] for row in window if row['node_kind'] == 'evidence_ref']
    runs = _run_rows(cur, run_ids)
    errors = _run_error_rows(cur, run_ids)
    run_index = _run_event_index(cur, run_ids)
    refs = {row['evidence_ref_id']: row for row in _evidence_ref_rows(cur, scope, ref_ids)}
    verdicts = _ref_validity_map(cur, scope, list(refs.values()))
    nodes = []
    for row in window:
        if row['node_kind'] == 'run':
            run = runs.get(row['node_id'])
            missing = run is None
            run = run or {'run_id': row['node_id'], 'question': None, 'status': None,
                          'confidence_level': None, 'started_at': row['sort_a'],
                          'ended_at': None, 'conversation_id': None, 'route': None}
            node = run_object(run, error_summary=error_summary_of(errors.get(row['node_id'])),
                              event_count=run_index.get(row['node_id'], {}).get('count', 0),
                              event_types=run_index.get(row['node_id'], {}).get('types', ()))
            # `wiki_provenance_links.run_id` 刻意没有外键（`021:375-376`）：运行行可能已经不在了。
            # 仍然显示这个运行 id，并让"元数据不可读"可见，不假装它不存在。
            node['run_row_present'] = not missing
            nodes.append({'node_kind': 'run', 'node_id': row['node_id'], 'run': node})
        else:
            ref = refs.get(row['node_id'])
            if ref is None:
                continue
            nodes.append({'node_kind': 'evidence_ref', 'node_id': row['node_id'],
                          'evidence_ref': evidence_ref_object(
                              ref, source_validity=verdicts.get(row['node_id']),
                              excerpt_allowed=verdicts.get(row['node_id']) != 'unavailable')})
    return nodes, more, len(window)


def _graph_relations(cur, scope, *, layer, node_ids):
    """图层的候选关系：**两端都在候选节点里**，且过同一套端点授权投影。"""
    if not node_ids:
        return [], False
    params = {'ids': list(node_ids), 'cap': GRAPH_RELATION_LIMIT + 1,
              **authorization_params(scope)}
    if layer in ('entries', 'evidence'):
        kind = 'entry' if layer == 'entries' else 'evidence_ref'
        sql = ('''SELECT r.* FROM wiki_relations r
            JOIN wiki_entries e ON e.entry_id = r.entry_id
           WHERE {auth} AND r.revision_id = {view}
             AND r.src_kind = %(kind)s AND r.dst_kind = %(kind)s
             AND r.src_id = ANY(%(ids)s) AND r.dst_id = ANY(%(ids)s)
           ORDER BY r.relation_id LIMIT %(cap)s''').format(
            auth=entry_authorization_predicate(scope, alias='e'), view=view_revision_sql('e'))
        params['kind'] = kind
        rows = _fetch(cur, sql, params)
        relations = [graph_relation_object(row) for row in rows]
    else:
        rows = _fetch(cur, '''SELECT l.* FROM wiki_provenance_links l
            JOIN wiki_evidence_refs er ON er.evidence_ref_id = l.evidence_ref_id
           WHERE er.space_id = %(space)s
             AND l.run_id = ANY(%(ids)s) AND l.evidence_ref_id = ANY(%(ids)s)
           ORDER BY l.provenance_link_id LIMIT %(cap)s''',
                      {'space': scope['space_id'], 'ids': list(node_ids),
                       'cap': GRAPH_RELATION_LIMIT + 1})
        relations = [provenance_link_relation(dict(row)) for row in rows]
    more = len(rows) > GRAPH_RELATION_LIMIT
    return relations[:GRAPH_RELATION_LIMIT], more


def _graph_nodes(cur, scope, *, layer, filters):
    if layer == 'entries':
        return _graph_entry_candidates(cur, scope, filters)
    if layer == 'evidence':
        return _graph_evidence_candidates(cur, scope)
    return _runs_layer_candidates(cur, scope)


def space_graph(space_id, dsn, identity, *, layer='entries', q=None, entry_type=None,
                category=None, status=None, limit=GRAPH_BATCH_DEFAULT, cursor=None):
    """#2 `GET /v1/wiki/spaces/{space_id}/graph`：**当前授权范围图**（契约 §5.2）。

    四条硬要求逐条落点：

    1. 图层或筛选变化 → `resolve_cursor()` 判指纹不一致 → 忽略游标、返回第一批、`truncated=false`；
    2. 关系两端都在已授权且已加载的节点集合内 → `graph_batch()`（前缀 = 已加载集合）；
    3. 达到 100 节点 / 300 关系 → `graph_batch()` 给 `limit_reached` + `next_cursor=null`
       + `WIKI_GRAPH_LIMIT_REACHED` 提示键（HTTP 200）；
    4. 响应里的 `scope_note` 明说这是"当前授权范围图"，不是全库全图。

    请求别人的 `space_id` → `404 WIKI_SPACE_NOT_FOUND`（`authorized_scope` 判定）。
    """
    scope = authorized_scope(identity, space_id)
    layer = normalize_enum(layer or 'entries', GRAPH_LAYERS, 'layer')
    if type(limit) is not int or not 1 <= limit <= GRAPH_BATCH_MAX:
        _invalid('limit must be between 1 and %d' % GRAPH_BATCH_MAX)
    filters = normalize_filters(layer=layer, q=q, entry_type=entry_type, category=category,
                                status=status, space_id=scope['space_id'])
    if layer != 'entries' and any(key in filters for key in ('q', 'entry_type', 'status',
                                                             'category')):
        # 这些筛选只对词条有意义。默默忽略等于让调用方以为筛过了，所以显式拒绝。
        _invalid('q/type/category/status apply to layer=entries only')
    fingerprint = filter_fingerprint(filters)
    marker, reset = resolve_cursor(cursor, fingerprint=fingerprint, keys=GRAPH_CURSOR_KEYS)
    loaded_nodes, loaded_relations = graph_cursor_state(marker, fingerprint=fingerprint)
    warnings = [CURSOR_RESET_ON_FILTER_CHANGE] if reset else []

    with wiki.connect(dsn) as conn, conn.cursor(cursor_factory=wiki.RealDictCursor) as cur:
        candidates, more_candidates, considered = _graph_nodes(cur, scope, layer=layer,
                                                                filters=filters)
        relations, more_relations = _graph_relations(
            cur, scope, layer=layer, node_ids=[node_id(node) for node in candidates])
        batch = graph_batch(candidates=candidates, candidate_relations=relations,
                            fingerprint=fingerprint, loaded_nodes=loaded_nodes,
                            loaded_relations=loaded_relations, batch_limit=limit,
                            more_candidates=more_candidates, more_relations=more_relations)

    if batch['hint']:
        warnings.append(batch['hint'])
    warnings.extend(scope_warnings(considered=considered, visible=len(candidates)))
    data = {'nodes': batch['nodes'], 'relations': batch['relations'], 'layer': layer,
            'loaded': batch['loaded'], 'limits': batch['limits'],
            'limit_reached': batch['limit_reached'],
            'truncated_reason': batch['truncated_reason'],
            # §5.2 第 4 条：界面称之为"当前授权范围图"，不得描述为全库全图。
            'scope_note': 'CURRENT_AUTHORIZED_SCOPE_GRAPH',
            'scope_description': '当前授权范围图（不是全库全图）：只包含本次授权、当前图层、'
                                 '已加载的节点与关系。'}
    return envelope(data=data,
                    meta=authorized_scope_meta(scope, considered=considered,
                                               visible=len(candidates)),
                    paging=paging_object(limit=limit, has_more=batch['has_more'],
                                         next_cursor=batch['next_cursor'],
                                         truncated=batch['truncated']),
                    warnings=warnings)


# ---------------------------------------------------------------------------
# 12. 数据库路径：#6 授权关系邻域
# ---------------------------------------------------------------------------

def _incident_entry_relations(cur, scope, frontier, *, direction, cap):
    """与 `frontier`（词条 id 集合）相连的关系行（两端都必须是授权词条）。

    `direction` 只影响**有向**关系：`in`/`out`/`both`；无向关系（`is_symmetric`）在三种取值下
    都返回（契约 §5.6）。邻居的授权过滤在 `_authorized_entry_ids()` 里做，**非授权对象不会
    进入任何计数**（§5.6 的 `unreached_neighbors` 只统计授权范围内未返回的邻居）。
    """
    if not frontier:
        return []
    want_in, want_out = direction in ('in', 'both'), direction in ('out', 'both')
    sql = ('''SELECT r.* FROM wiki_relations r
        JOIN wiki_entries e ON e.entry_id = r.entry_id
       WHERE {auth} AND r.revision_id = {view}
         AND r.src_kind = 'entry' AND r.dst_kind = 'entry'
         AND ((r.src_id = ANY(%(frontier)s) AND (%(want_out)s OR r.is_symmetric))
           OR (r.dst_id = ANY(%(frontier)s) AND (%(want_in)s OR r.is_symmetric)))
       ORDER BY r.relation_id LIMIT %(cap)s''').format(
        auth=entry_authorization_predicate(scope, alias='e'), view=view_revision_sql('e'))
    rows = _fetch(cur, sql, {'frontier': list(frontier), 'want_in': want_in,
                             'want_out': want_out, 'cap': cap,
                             **authorization_params(scope)})
    return [dict(row) for row in rows]


def _authorized_entry_ids(cur, scope, entry_ids):
    """这些词条里哪些在授权范围内（邻域扩展的**唯一**邻接授权门）。"""
    entry_ids = [value for value in entry_ids or () if value]
    if not entry_ids:
        return set()
    sql = ('SELECT e.entry_id FROM wiki_entries e WHERE e.entry_id = ANY(%(ids)s) AND {auth}')
    rows = _fetch(cur, sql.format(auth=entry_authorization_predicate(scope, alias='e')),
                  {'ids': list(entry_ids), **authorization_params(scope)})
    return {row['entry_id'] for row in rows}


def _entry_node(cur, scope, row):
    objects = entry_objects(cur, scope, [row])
    if not objects:
        return None
    return {'node_kind': 'entry', 'node_id': row['entry_id'], 'entry': objects[0]}


def entry_neighborhood(entry_id_value, dsn, identity, *, depth=NEIGHBORHOOD_DEPTH_DEFAULT,
                       limit=NEIGHBORHOOD_LIMIT_DEFAULT, layer='entries', direction='both'):
    """#6 `GET /v1/wiki/entries/{entry_id}/graph`：授权关系邻域（契约 §5.6）。

    * `depth` 1..2（超出 422）；`limit` ≤ 100，且**受同一 100 节点 / 300 关系上限约束**；
    * 返回 `root` / `truncated` / `truncated_reason` / `unreached_neighbors`；
    * `unreached_neighbors` 只统计**当前授权范围内**因截断未返回的邻居——非授权对象在扩展时
      就被丢掉，从不进入计数，否则用数字就能推断隐藏图的存在性；
    * 根词条走 `authorized_entry()`（与 #3/#4/#5 同一个授权函数）：别人的词条 → 404。

    本接口**不依赖** #2 的图数据（契约 §5.7 约束 4 的同一条窄屏义务），自己按 BFS 取数。
    """
    scope = authorized_scope(identity)
    if type(depth) is not int or not 1 <= depth <= NEIGHBORHOOD_DEPTH_MAX:
        _invalid('depth must be between 1 and %d' % NEIGHBORHOOD_DEPTH_MAX)
    if type(limit) is not int or not 1 <= limit <= NEIGHBORHOOD_LIMIT_MAX:
        _invalid('limit must be between 1 and %d' % NEIGHBORHOOD_LIMIT_MAX)
    layer = normalize_enum(layer or 'entries', ('entries',), 'layer')
    direction = normalize_enum(direction or 'both', NEIGHBORHOOD_DIRECTIONS, 'direction')

    hop_nodes, relation_rows = [], []
    considered = visible = 0
    boundary = 0
    with wiki.connect(dsn) as conn, conn.cursor(cursor_factory=wiki.RealDictCursor) as cur:
        entry = authorized_entry(cur, entry_id_value, identity)
        root = _entry_node(cur, scope, entry)
        visited = {entry['entry_id']}
        frontier = [entry['entry_id']]
        for _hop in range(1, depth + 1):
            # 多取一行给 assemble_neighborhood() 判定"关系被上限截断"（§4 语义 5）。
            budget = GRAPH_RELATION_LIMIT + 1 - len(relation_rows)
            rows = _incident_entry_relations(cur, scope, frontier, direction=direction,
                                            cap=max(budget, 1))
            relation_rows.extend(rows)
            discovered = []
            for row in rows:
                for side in ('src_id', 'dst_id'):
                    value = row[side]
                    if value not in visited and value not in discovered:
                        discovered.append(value)
            considered += len(discovered)
            authorized = _authorized_entry_ids(cur, scope, discovered)
            visible += len(authorized)
            # 只把**授权范围内**的邻居带进图；非授权对象从此不参与任何计数（§5.6）。
            neighbors = [value for value in discovered if value in authorized]
            hop_nodes.append(neighbors)
            visited.update(neighbors)
            frontier = neighbors
            if len(relation_rows) > GRAPH_RELATION_LIMIT or not frontier:
                break
        if depth >= NEIGHBORHOOD_DEPTH_MAX and frontier:
            # 深度边界之外仍然存在的授权邻居：只有请求深度已经是上限（2 跳）时才算"被深度截断"。
            boundary = len([value for value in _boundary_neighbors(
                cur, scope, frontier, direction=direction, seen=visited)
                if value not in visited])
        relation_objects = [graph_relation_object(row) for row in relation_rows]
        node_rows = _entry_rows_by_id(cur, scope, [value for hop in hop_nodes for value in hop])
        node_objects = {obj['entry_id']: obj for obj in entry_objects(cur, scope, node_rows)}
        assembled_nodes = []
        for hop in hop_nodes:
            layer_nodes = []
            for value in hop:
                obj = node_objects.get(value)
                if obj is not None:
                    layer_nodes.append({'node_kind': 'entry', 'node_id': value, 'entry': obj})
            assembled_nodes.append(layer_nodes)
        result = assemble_neighborhood(root=root, hop_nodes=assembled_nodes,
                                       relation_rows=relation_objects, depth=depth,
                                       max_depth=NEIGHBORHOOD_DEPTH_MAX, node_limit=limit,
                                       boundary_neighbors=boundary)

    warnings = scope_warnings(considered=considered, visible=visible)
    if result['hint']:
        warnings.append(result['hint'])
    data = {'root': result['root'], 'nodes': result['nodes'], 'relations': result['relations'],
            'layer': layer, 'depth': depth, 'direction': direction,
            'loaded': result['loaded'], 'limits': result['limits'],
            'truncated': result['truncated'], 'truncated_reason': result['truncated_reason'],
            'unreached_neighbors': result['unreached_neighbors'],
            'scope_note': 'CURRENT_AUTHORIZED_SCOPE_GRAPH',
            'scope_description': '当前授权范围邻域（不是全库全图）：未到达的邻居只统计授权范围内'
                                 '被截断的那些。'}
    # 邻域没有游标（不是分页接口）：`has_more` 恒 false，"尚有后续"由 `truncated` +
    # `unreached_neighbors` 表达（§5.2 第 4 条要求邻域标明"已截断"）。
    return envelope(data=data, meta=authorized_scope_meta(scope, considered=considered,
                                                          visible=visible),
                    paging=paging_object(limit=limit, has_more=False,
                                         next_cursor=None, truncated=result['truncated']),
                    warnings=warnings)


def _entry_rows_by_id(cur, scope, entry_ids):
    entry_ids = [value for value in entry_ids or () if value]
    if not entry_ids:
        return []
    sql = ('SELECT ' + entry_columns_sql('e') + ' FROM wiki_entries e'
           ' WHERE e.entry_id = ANY(%(ids)s) AND ' + entry_authorization_predicate(scope))
    return [dict(row) for row in _fetch(cur, sql, {'ids': list(entry_ids),
                                                   **authorization_params(scope)})]


def _boundary_neighbors(cur, scope, frontier, *, direction, seen):
    """深度边界之外仍存在的授权邻居 id（**只用来如实说明"被深度截断"**，不进节点集合）。"""
    rows = _incident_entry_relations(cur, scope, frontier, direction=direction,
                                     cap=GRAPH_RELATION_LIMIT + 1)
    candidate = []
    for row in rows:
        for side in ('src_id', 'dst_id'):
            value = row[side]
            if value not in seen and value not in candidate:
                candidate.append(value)
    return [value for value in candidate if value in _authorized_entry_ids(cur, scope, candidate)]


# ---------------------------------------------------------------------------
# 13. 数据库路径：#7 证据与执行事件摘要 / #8 Markdown 导出（共用同一份授权投影）
# ---------------------------------------------------------------------------

def _target_revision(cur, entry, identity, revision_id_value=None):
    """读取目标版本：显式 `revision_id` → 发布头 → 最新版本；草稿/待复核需要编辑权限。

    与 #3 `wiki.entry_detail()` 同口径（发布头优先），草稿门沿用
    `wiki.may_read_restricted()`；无权限 → 与不存在**完全相同**的 404（§3.1 脚注 1）。
    """
    target = None
    if revision_id_value is not None:
        target = wiki._text(revision_id_value, 'revision_id')
    elif entry['head_revision_id']:
        target = entry['head_revision_id']
    else:
        target = wiki._latest_revision_id(cur, entry['entry_id'])
    if target is None:
        return None
    revision = wiki._load_revision(cur, entry['entry_id'], target)
    if revision['publication_status'] in ('draft', 'in_review') and not wiki.may_read_restricted(
            identity, revision['author_id']):
        wiki.fail('WIKI_ENTRY_NOT_FOUND', 404)
    return revision


def _revision_relations(cur, scope, revision_id_value):
    """一个版本的已保存关系（过端点授权投影）。派生关系尚未落库，这里不现场推导。"""
    sql = ('''SELECT r.* FROM wiki_relations r
       WHERE r.revision_id = %(revision)s AND {auth}
       ORDER BY r.relation_id''').format(
        auth=relation_endpoint_authorization_predicate(scope, alias='r'))
    rows = _fetch(cur, sql, {'revision': revision_id_value,
                             **authorization_params(scope)})
    return [graph_relation_object(row) for row in rows]


def _linked_ref_ids(cur, revision_id_value):
    """该版本的证据引用 id：`wiki_provenance_links`（版本级或主张级）∪ `citations`。

    两条来源都要取：citation 是版本自己记录的引用，link 是出处映射表登记的出现记录。
    只取一条会让"引用清单"和"来源展开"给出不同的集合，那正是契约要避免的分叉。
    """
    rows = _fetch(cur, '''SELECT l.evidence_ref_id AS ref_id FROM wiki_provenance_links l
        WHERE l.revision_id = %(revision)s
           OR l.assertion_id IN (SELECT a.assertion_id FROM wiki_assertions a
                                  WHERE a.revision_id = %(revision)s)
        UNION
        SELECT citation ->> 'evidence_ref_id' FROM wiki_revisions rv,
             jsonb_array_elements(rv.citations) AS citation
        WHERE rv.revision_id = %(revision)s
        ORDER BY 1''', {'revision': revision_id_value})
    return [row['ref_id'] for row in rows if row['ref_id']]


def _linked_run_ids(cur, revision_id_value):
    rows = _fetch(cur, '''SELECT DISTINCT l.run_id FROM wiki_provenance_links l
        WHERE l.revision_id = %(revision)s
           OR l.assertion_id IN (SELECT a.assertion_id FROM wiki_assertions a
                                  WHERE a.revision_id = %(revision)s)
        ORDER BY 1''', {'revision': revision_id_value})
    return [row['run_id'] for row in rows if row['run_id']]


def _run_event_index(cur, run_ids):
    """每个运行的**可见**事件数与事件类型（按真实时间顺序去重）。"""
    run_ids = [value for value in run_ids or () if value]
    if not run_ids:
        return {}
    rows = _fetch(cur, '''SELECT run_id, event_id, event_type FROM trace_events
        WHERE run_id = ANY(%(ids)s) AND COALESCE(visibility, 'visible') = 'visible'
        ORDER BY run_id, started_at, event_id''', {'ids': list(run_ids)})
    index = {}
    for row in rows:
        entry = index.setdefault(row['run_id'], {'count': 0, 'types': []})
        entry['count'] += 1
        if row['event_type'] and row['event_type'] not in entry['types']:
            entry['types'].append(row['event_type'])
    return index


def _edge_columns_missing(cur):
    """线上 `evidence_edges` 缺哪三列（§6.4 的第一个理由码要**实测**，不靠常量）。"""
    rows = _fetch(cur, '''SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'evidence_edges' ''')
    return missing_edge_columns([row['column_name'] for row in rows])


def _provenance_bundle(cur, scope, *, revision, layer, limit, marker):
    """#7 与 #8 共用的来源展开（**同一份授权投影**，所以导出不可能比页面看得更多）。

    返回 `evidence_refs` / `runs` / `events` / 计数 / 出处缺失标记；所有对象都已过
    空间授权（证据引用按 `space_id`，运行只经由本版本的出处映射进入）。
    """
    revision_id_value = revision['revision_id']
    linked = _linked_ref_ids(cur, revision_id_value)
    ref_rows = _evidence_ref_rows(cur, scope, linked)           # ← 授权过滤发生在这里
    refs_total = len(ref_rows)
    refs_truncated = refs_total > EXPORT_EVIDENCE_MAX
    ref_rows = ref_rows[:EXPORT_EVIDENCE_MAX]
    verdicts = _ref_validity_map(cur, scope, ref_rows)
    evidence_refs = [evidence_ref_object(
        row, source_validity=verdicts.get(row['evidence_ref_id']),
        excerpt_allowed=verdicts.get(row['evidence_ref_id']) != 'unavailable')
        for row in ref_rows]

    run_ids = _linked_run_ids(cur, revision_id_value)
    run_index = _run_event_index(cur, run_ids)
    run_rows = _run_rows(cur, run_ids)
    error_rows = _run_error_rows(cur, run_ids)
    runs = []
    for run_id_value in run_ids:
        row = run_rows.get(run_id_value)
        if row is None:
            continue
        runs.append(run_object(row,
                               error_summary=error_summary_of(error_rows.get(run_id_value)),
                               event_count=run_index.get(run_id_value, {}).get('count', 0),
                               event_types=run_index.get(run_id_value, {}).get('types', ())))

    events, event_total, unavailable, has_more = [], 0, False, False
    detail = None
    events_wanted = layer in ('runs', 'both') and bool(run_ids)
    if run_ids:
        # 事件**总数**与是否翻事件页无关：摘要里的数字永远是真的（分页不影响它）。
        try:
            event_total = int(_scalar(cur, '''SELECT count(*) FROM trace_events
                WHERE run_id = ANY(%(ids)s) AND COALESCE(visibility, 'visible') = 'visible' ''',
                                      {'ids': list(run_ids)}) or 0)
        except wiki.psycopg2.Error as exc:  # pragma: no cover - 只有真库故障会走到
            cur.connection.rollback()
            unavailable, event_total, detail = True, 0, str(exc)
    if events_wanted and not unavailable:
        params = {'ids': list(run_ids), 'limit': limit + 1}
        sql = ('''SELECT event_id, run_id, event_type, status, agent_name, tool_name,
                started_at, ended_at, duration_ms, input_summary, output_summary,
                error_code, retry_index, visibility
            FROM trace_events
           WHERE run_id = ANY(%(ids)s) AND COALESCE(visibility, 'visible') = 'visible' ''')
        position = listing_marker(marker)
        if position:
            sql += (' AND (started_at, event_id) >'
                    ' (%(cursor_started_at)s::timestamptz, %(cursor_event_id)s)')
            params['cursor_started_at'] = position[0]
            params['cursor_event_id'] = position[1]
        sql += ' ORDER BY started_at, event_id LIMIT %(limit)s'
        try:
            rows = _fetch(cur, sql, params)
            window, has_more = window_and_more(rows, limit)
            events = [event_object(row) for row in window]
        except wiki.psycopg2.Error as exc:  # pragma: no cover - 只有真库故障会走到
            # §8.2 纪律 1：读取失败**不得**显示成"无数据"。回滚后继续（本连接只读），
            # 并把 unavailable 透到 summary/runs 的 error_summary 里。
            cur.connection.rollback()
            events, event_total, unavailable = [], 0, True
            has_more = False
            detail = str(exc)

    relations = _revision_relations(cur, scope, revision_id_value)
    markers = [row.get('provenance_incomplete') for row in
               _fetch(cur, '''SELECT provenance_incomplete FROM wiki_relations
                   WHERE revision_id = %(revision)s''', {'revision': revision_id_value})]
    markers.extend(row.get('provenance_incomplete') for row in ref_rows)
    #: §6.4 的第一个理由码只在"确实有投影出来的关系"时才成立——没有边就没有"边缺列"这回事。
    has_projected = any(relation.get('origin') == 'projected' for relation in relations)
    return {'evidence_refs': evidence_refs, 'refs_truncated': refs_truncated,
            'refs_total': refs_total, 'runs': runs,
            'events': events, 'event_total': event_total, 'has_more': has_more,
            'error_total': sum(len(rows) for rows in error_rows.values()),
            'events_materialized': bool(events_wanted),
            'events_unavailable': unavailable, 'events_detail': detail,
            'relations': relations, 'markers': markers, 'has_projected_edges': has_projected,
            'validity': [verdicts.get(row['evidence_ref_id']) for row in ref_rows],
            'edge_columns_missing': _edge_columns_missing(cur),
            'considered': len(linked), 'visible': len(ref_rows)}


def entry_provenance(entry_id_value, dsn, identity, *, revision_id=None,
                     limit=PROVENANCE_LIMIT_DEFAULT, cursor=None, layer='both'):
    """#7 `GET /v1/wiki/entries/{entry_id}/provenance`：固定版本的证据与执行事件摘要（§5.7）。

    四条硬约束的落点：

    1. `provenance_incomplete` 按 §6 照抄（`provenance_incomplete_summary()`）：缺失就标缺失、
       有就标有、**不推测补全**；
    2. `error_summary` 三态（有错误 / 显式无错误 / 读取失败不可用）；
    3. 脱敏：`visibility != 'visible'` 的事件不返回，摘要过 `sanitize_summary()`；
    4. **不依赖 #2 的图数据**：本接口自己按版本展开，窄屏下可独立调用。

    **一处刻意的降级选择**：轨迹读取失败时本层**不**整单返回 502，而是保留可用的部分
    （词条、引用、运行摘要）并把失败显式标注出来（`summary.events_unavailable`、
    `error_summary.unavailable`、`warnings` 里的 `WIKI_LOAD_FAILED`）。契约 §8.2 纪律 1 的
    硬要求是"不得显示成无数据"——这两条都满足，而"部分可用"比"整单失败"更有用。
    若接线方想要整单 502，只需在 `bundle['events_unavailable']` 为真时抛
    `wiki.fail('WIKI_LOAD_FAILED', 502)` 即可，本层已经把这个判断摆在明面上。
    """
    scope = authorized_scope(identity)
    if type(limit) is not int or not 1 <= limit <= PROVENANCE_LIMIT_MAX:
        _invalid('limit must be between 1 and %d' % PROVENANCE_LIMIT_MAX)
    layer = normalize_enum(layer or 'both', PROVENANCE_LAYERS, 'layer')
    events_paged = layer in ('runs', 'both')

    with wiki.connect(dsn) as conn, conn.cursor(cursor_factory=wiki.RealDictCursor) as cur:
        entry = authorized_entry(cur, entry_id_value, identity)
        revision = _target_revision(cur, entry, identity, revision_id)
        if revision is None:
            # 词条还没有任何版本：没有可展开的来源，这是"确实没有"，不是读取失败。
            data = {'revision_id': None, 'layer': layer,
                    'summary': provenance_summary(
                        provenance_incomplete=projection_provenance_incomplete(applies=False))}
            return envelope(data=data,
                            meta=authorized_scope_meta(scope, considered=0, visible=0),
                            paging=paging_object(limit=limit, has_more=False,
                                                 next_cursor=None, truncated=False),
                            warnings=['WIKI_NO_REVISION_FOR_PROVENANCE'])
        # 游标绑定**实际解析出来的版本**：换了版本（等于换了集合）客户端复用旧游标时，
        # 指纹不匹配 → 重置（§4 语义 4）。
        fingerprint = filter_fingerprint(normalize_filters(revision_id=revision['revision_id'],
                                                           layer=layer))
        marker, reset = resolve_cursor(cursor, fingerprint=fingerprint, keys=LISTING_CURSOR_KEYS)
        if not events_paged:
            marker = None      # 证据段不分页：游标只描述事件页
        bundle = _provenance_bundle(cur, scope, revision=revision, layer=layer, limit=limit,
                                    marker=marker)
        incomplete = provenance_incomplete_summary(
            markers=bundle['markers'],
            edge_columns_missing=bundle['edge_columns_missing'],
            has_projected_edges=bundle['has_projected_edges'],
            has_evidence=bool(bundle['evidence_refs']),
            detected_at=datetime.now(ASIA_SHANGHAI))
        summary = provenance_summary(evidence_refs=bundle['evidence_refs'], runs=bundle['runs'],
                                     events=bundle['events'], source_validity=bundle['validity'],
                                     provenance_incomplete=incomplete,
                                     events_unavailable=bundle['events_unavailable'],
                                     evidence_refs_truncated=bundle['refs_truncated'],
                                     event_count=bundle['event_total'],
                                     error_count=bundle['error_total'],
                                     events_materialized=bundle['events_materialized'])
        relations_present = bool(bundle['relations'])

    warnings = [CURSOR_RESET_ON_FILTER_CHANGE] if reset else []
    if bundle['events_unavailable']:
        warnings.extend([WIKI_LOAD_FAILED, 'WIKI_TRACE_EVENTS_UNAVAILABLE'])
    data = {'revision_id': revision['revision_id'], 'summary': summary, 'layer': layer,
            'revision_has_relations': relations_present}
    if layer in ('evidence', 'both'):
        data['evidence_refs'] = bundle['evidence_refs']
    if events_paged:
        data['runs'] = bundle['runs']
        data['events'] = bundle['events']
    next_cursor = None
    if events_paged and bundle['has_more'] and bundle['events']:
        last = bundle['events'][-1]
        next_cursor = listing_cursor(fingerprint, sort_key=last['started_at'],
                                     tie_break=last['event_id'])
    # `truncated` = "因上限而截断"：事件靠游标可以继续翻页，所以不算截断；
    # 证据引用超过 300 条时无法继续（`WIKI_EXPORT_TOO_LARGE` 的那条上限），才是截断。
    return envelope(data=data,
                    meta=authorized_scope_meta(scope, considered=bundle['considered'],
                                               visible=bundle['visible']),
                    paging=paging_object(limit=limit,
                                         has_more=bool(events_paged and bundle['has_more']),
                                         next_cursor=next_cursor,
                                         truncated=bool(bundle['refs_truncated'])),
                    warnings=warnings)


def entry_export(entry_id_value, dsn, identity, *, revision_id=None, expected_body_sha256=None):
    """#8 `GET /v1/wiki/entries/{entry_id}/export`：带状态与引用的 Markdown（§5.8）。

    * 走**同一个**授权投影：词条经 `authorized_entry()`、证据与运行经 `_provenance_bundle()`
      —— 导出不可能比页面看到更多（§5.8"导出不扩大访问范围"）；
    * `expected_body_sha256` 不一致 → `409 WIKI_VERSION_CONFLICT`（沿用仓库既有的固定版本校验形态）；
    * 超过 300 条证据引用 → `422 WIKI_EXPORT_TOO_LARGE`（**不是**截断后声称完整）；
    * 返回值是给路由接线用的字典：`markdown` / `headers`（含 `no-store` 与
      `Content-Disposition: attachment`）/ `media_type`。
    """
    scope = authorized_scope(identity)
    with wiki.connect(dsn) as conn, conn.cursor(cursor_factory=wiki.RealDictCursor) as cur:
        entry = authorized_entry(cur, entry_id_value, identity)
        revision = _target_revision(cur, entry, identity, revision_id)
        if revision is None:
            wiki.fail('WIKI_REVISION_NOT_FOUND', 404, hint='this entry has no revision yet')
        expected = wiki.strip_sha256_prefix(expected_body_sha256)
        if expected_body_sha256 is not None:
            if expected is None:
                _invalid('expected_body_sha256 must be hex64')
            if expected != revision['body_sha256']:
                wiki.fail('WIKI_VERSION_CONFLICT', 409,
                          hint='body_sha256 does not match this revision')
        bundle = _provenance_bundle(cur, scope, revision=revision, layer='both',
                                    limit=PROVENANCE_LIMIT_MAX, marker=None)
        # 不截断后声称完整：超过上限就换错误码（§4 导出上限 300 条）。
        export_ref_guard(bundle['refs_total'])
        entry_objects_list = entry_objects(cur, scope, [entry])
        entry_object_value = entry_objects_list[0] if entry_objects_list else wiki.entry_object(
            dict(entry))
        incomplete = provenance_incomplete_summary(
            markers=bundle['markers'], edge_columns_missing=bundle['edge_columns_missing'],
            has_projected_edges=bundle['has_projected_edges'],
            has_evidence=bool(bundle['evidence_refs']),
            detected_at=datetime.now(ASIA_SHANGHAI))
        markdown = render_export_markdown(
            entry=entry_object_value,
            revision=wiki.revision_object(revision, wiki._assertion_rows(
                cur, revision['revision_id'])),
            relations=bundle['relations'], evidence_refs=bundle['evidence_refs'],
            runs=bundle['runs'], status=entry_object_value.get('status') or {},
            provenance_incomplete=incomplete, exported_at=datetime.now(ASIA_SHANGHAI),
            demo_data=entry_object_value.get('demo_data'),
            unavailable_note=('本次导出的运行轨迹读取失败，事件与错误摘要不可用（%s）；'
                              '这不是"没有事件"。' % bundle['events_detail'])
            if bundle['events_unavailable'] else None)
    return {'entry_id': entry['entry_id'], 'revision_id': revision['revision_id'],
            'body_sha256': revision['body_sha256'],
            'filename': export_filename(entry['entry_id']),
            'media_type': 'text/markdown; charset=utf-8',
            'headers': export_headers(entry['entry_id']),
            'markdown': markdown,
            'citation_count': len(bundle['evidence_refs']),
            'run_count': len(bundle['runs']),
            'authorized_scope': {'nodes_considered': bundle['considered'],
                                 'nodes_visible': bundle['visible']},
            'provenance_incomplete': incomplete}


# ---------------------------------------------------------------------------
# 14. 路由接线的对照表（给下一个改动 `api.py` 的代理，本模块不碰路由）
# ---------------------------------------------------------------------------

ROUTE_BINDINGS = (
    ('GET', '/v1/wiki/entries', 'entry_catalogue'),
    ('GET', '/v1/wiki/spaces/{space_id}/graph', 'space_graph'),
    ('GET', '/v1/wiki/entries/{entry_id}/graph', 'entry_neighborhood'),
    ('GET', '/v1/wiki/entries/{entry_id}/provenance', 'entry_provenance'),
    ('GET', '/v1/wiki/entries/{entry_id}/export', 'entry_export'),
)
