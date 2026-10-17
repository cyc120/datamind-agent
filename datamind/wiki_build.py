"""Wiki 整理任务的**生产整理器**（`wiki_build_jobs` 的 worker 主体）。

契约依据：`docs/WIKI_API.md` §5.9（提交，`202` 只返回任务状态）、§5.10（查看任务）、§5.12
（以基线创建版本）、§7.2/§7.3（三套状态只读映射）；`docs/WIKI_DOMAIN.md` §4（三类身份）、§5.4
（整理失败不得改写既有问答的账）；`db/migrations/021_wiki_knowledge.sql`（8 张表与全部 CHECK）。

WHY 这个模块存在
----------------
`datamind/wiki_jobs.py` 的 `execute_build()` 需要一个**注入的整理回调**（`builder`），
本模块就是那个回调的生产实现：整份文件里没有一条语句写 `runs` / `claims` / `trace_events` /
`evidence_*`（`wiki_jobs` 模块头 WHY 5 与 `tests/test_wiki_jobs.py` 的静态断言同款纪律）。

WHY 长这样（逐条可在检出里核对）：

1. **权限复查一步都不省。** 本模块**不**自己写 `wiki_build_jobs` 的状态：领取走
   `wiki_jobs.claim_build()`、交付走 `wiki_jobs.complete_build()`、失败落账走
   `wiki_jobs.fail_build()`。那两步里的 `_lock_actor()` 是契约要求的两次复查
   （撤权后不开工 / 撤权则不交付），在这里重写一遍就等于把复查删掉。

2. **投影一行都不重写。** 条目、证据引用、投影健康度全部来自 `datamind/wiki_projection.py`
   的纯函数（`project_entry` / `project_evidence_ref` / `projection_summary` /
   `plan_wiki_derived_relations`）。本模块只做三件投影层**做不到**的事：读库（投影层不连库）、
   把投影结果落进 `wiki_entries` / `wiki_evidence_refs` / `wiki_provenance_links`、
   用 §5.12 的服务层写版本（`wiki.create_revision`，它带着 append-only 触发器与引用可达性校验）。

3. **连接的 DSN 用应用角色**（`wiki.wiki_dsn()`，默认 `datamind_app`）。`021:605-607` 已把
   8 张 `wiki_*` 表从 `readonly_user` REVOKE，读路径也必须在应用角色上跑（`wiki.py` 模块头 WHY 1）。
   证据表（`evidence_nodes` / `evidence_edges` / `runs`）用同一个角色**只读**——实测该角色对
   这三张表有 SELECT，而 021:24-35 的"postgres 持有的表迁移不得触碰"在这里体现为：
   本模块对它们只发 SELECT。

4. **一次整理只产出一个候选词条。** `wiki_build_jobs` 的交付字段只有一个
   `result_entry_id` + 一个 `result_revision_id`（021:568-570），所以一个运行即使能投影出
   多个词条（例如同时有 Claim 与 DocumentFact），也只能交付一个：取**节点数最多**的候选，
   `entry_type_hint` 命中时优先，并列时按 `entry_type` 字典序固定——确定性优先于"聪明"。
   其余候选在 `plan.skipped_candidate_entry_types` 里如实列出，不静默丢弃。

5. **派生关系仍然是计划，不是行。** `plan_wiki_derived_relations()` 的 8 个 Wiki 派生关系
   端点是 `wiki-`/`wassert-` id，由 build 步骤铸造（`wiki_projection.py:2055-2060`）；
   本版只铸造**词条**端点，没有铸造 `wassert-` 主张端点，所以版本的 `relations` 传空，
   派生计划挂在 `plan.deferred_derivations` 上如实报告，并写进正文的"未落库的部分"一节。

6. **`template+polish` 不假装有模型。** `mode='template+polish'` 目前与 `template` 走同一条
   模板路径：`generation_method` 一律 `'template'`（021:194-198 的闭集里 `model_polish`
   是另一个值），`change_note` 里写明 mode。编一个 `model_id` 的代价是 `wiki_revisions` 里
   出现一条永远无法复现的生成记录（021:215-216），比"没做"更糟。

7. **纯逻辑与数据库分开。** `queued_sql()` / `job_identity()` / `plan_for_job()` /
   `body_markdown()` / `claim_error_verdict()` 都是纯函数，不需要 psycopg2（系统 python 可跑）；
   只有 `load_*` / `persist_draft()` / `build_draft()` 连库（`.venv` 解释器）。这条分层的
   理由是仓库实测的解释器差异（`wiki.py` 模块头 WHY 2），不是审美。
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
import re
import socket
from typing import Callable, Iterable

from . import wiki
from .wiki_projection import (
    ProjectionError,
    allocate_id,
    canonical_json,
    digest64,
    node_payload,
    normalize_node_type,
    plan_wiki_derived_relations,
    project_entry,
    project_evidence_ref,
    projection_summary,
)

#: 模板版本：`generation_method='template'` 时 021:214 要求非空（≤64）。
TEMPLATE_VERSION = 'wiki-build-template-v1'

#: 正文里最多列几条证据（每条一行）。这只是**渲染**上限：条目、证据引用与溯源行
#: 一条都不会少写（`body_markdown` 不是引用的载体，`citations` 才是）。
BODY_EVIDENCE_LINES = 50
#: 正文体量上限（021:209 是 262144 字节）。按字符再收紧一档，留出多字节余量。
BODY_MAX_CHARS = 20000

#: 一个运行的节点读取上限。实时库里 `evidence_nodes` 共 2760 行、单个运行最多几十行，
#: 所以这个上限只在异常数据上生效，并且**如实报告**被截断（不静默丢弃）。
NODES_READ_LIMIT = 2000

#: worker 的退出码（沿用仓库惯例：0 正常、1 内部错误、2 参数错误）。
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2

#: 终态（021:565-566 的闭集）。
TERMINAL_STATES = ('succeeded', 'failed')

#: `error_detail` 的落库长度上限（列是 TEXT；与 `wiki_jobs.ERROR_DETAIL_MAX` 同款纪律）。
DETAIL_MAX = 500

#: 领取阶段的**不可重试**错误码：身份/权限问题不会因为再等一会儿就好了，必须落账成 `failed`，
#: 否则任务永远停在 `queued`。其余异常（数据库抖动、锁等待）保持 `queued`，下一轮再试。
FATAL_CLAIM_CODES = frozenset((
    'WIKI_PERMISSION_DENIED', 'WIKI_BUILD_NOT_FOUND', 'WIKI_ENTRY_NOT_FOUND',
    'WIKI_SPACE_NOT_FOUND', 'WIKI_RUN_FORBIDDEN', 'WIKI_RUN_NOT_FOUND',
    'WIKI_IDEMPOTENCY_CONFLICT', 'WIKI_BUILD_NOT_RETRYABLE', 'WIKI_BUILD_REQUEST_INVALID',
    'WIKI_NO_EVIDENCE', 'SESSION_INVALID',
))

#: 落库前的错误码归一：`error_code` 的 CHECK 是 `^WIKI_[A-Z0-9_]+$`（021:590），
#: 而领取路径会抛出既有的 `SESSION_INVALID`（`auth.py:212`）。`wiki_jobs.sanitize_error_code()`
#: 会把不合规的码一律降级成 `WIKI_BUILD_FAILED` —— 那会丢掉"会话失效"这个真正的原因，
#: 所以在这里显式改名，而不是让它被降级。
ERROR_CODE_ALIASES = {'SESSION_INVALID': 'WIKI_SESSION_INVALID'}


def normalize_error_code(code):
    """把服务层的既有错误码归一成能落进 `error_code` 的 `WIKI_*` 码。"""
    return ERROR_CODE_ALIASES.get(code, code)


# ---------------------------------------------------------------------------
# 1. 纯逻辑：选任务、身份、筛选
# ---------------------------------------------------------------------------

def app_dsn() -> str:
    """整理器使用的 DSN：应用角色（见模块头 WHY 3）。

    与 `wiki.wiki_dsn()` 同一个值；这里给 worker 一个语义化入口，免得它去猜服务层的私有约定。
    """
    return wiki.wiki_dsn()


def queued_sql(*, single=False, owner=None, build_id=None) -> str:
    """`status='queued'` 的选任务 SQL（键集顺序固定：先到先做）。

    `FOR UPDATE SKIP LOCKED` 让两个 worker 进程不会抢同一行 —— 但**领取的权威仍然是
    `wiki_jobs.claim_build()`**（`UPDATE … WHERE status='queued'` 的原子 0 行判定）。
    这里的行锁只是省掉一次白跑，不承担任何正确性。

    `owner` 非空时按 owner 过滤（admin 身份不传它 —— `auth.py:195/217` 的超管旁路与
    `claim_build()` 的 WHERE 子句同款）。子句顺序固定为
    `WHERE … ORDER BY … LIMIT … FOR UPDATE`，所以这里是拼字符串而不是"事后 replace"。
    """
    where = "WHERE status = 'queued'"
    params = []
    if build_id:
        where += ' AND build_id = %s'
        params.append(build_id)
    if owner:
        where += ' AND owner_id = %s'
        params.append(owner)
    sql = ('''SELECT build_id, space_id, owner_id, request_id, run_id, mode, selection,
            entry_type_hint, target_entry_id, status, attempts
        FROM wiki_build_jobs ''' + where + ' ORDER BY created_at, build_id')
    if single:
        sql += ' LIMIT 1'
    return sql + ' FOR UPDATE SKIP LOCKED', params


def job_identity(job) -> dict:
    """owner 身份（`wiki.user_id_of` 的入参形状）。

    worker 只以**任务 owner 的身份**开工：`claim_build()` / `complete_build()` 里的
    `_lock_actor()` 会拿这个身份去复查角色；换成服务身份或别人的身份都等于绕过复查。
    """
    return {'user_id': job['owner_id']}


def selection_filter(job) -> dict:
    """任务载荷里的筛选条件（§5.9 的 `selection`，缺省是空 dict）。

    只认契约里的两个键：`claim_ids`（按 `ref_key`/载荷里的 claim_id 选节点）与
    `evidence_ids`（按 `evidence_id` 选节点）。未知键在提交侧已被
    `wiki_jobs.canonical_selection()` 拒掉，这里再兜一次底。
    """
    selection = job.get('selection') if isinstance(job, dict) else None
    if not isinstance(selection, dict):
        return {}
    resolved = {}
    for key in ('claim_ids', 'evidence_ids'):
        raw = selection.get(key)
        if isinstance(raw, (list, tuple)) and raw:
            resolved[key] = {str(item) for item in raw}
    return resolved


def node_selected(node, selection) -> bool:
    """该节点是否在本次整理的筛选范围内（`selection` 为空 = 全选）。"""
    if not selection:
        return True
    evidence_ids = selection.get('evidence_ids')
    if evidence_ids is not None and str(node.get('evidence_id')) in evidence_ids:
        return True
    claim_ids = selection.get('claim_ids')
    if claim_ids is not None:
        payload = node_payload(node)
        candidates = set()
        if node.get('ref_key') is not None:
            candidates.add(str(node['ref_key']))
        for key in ('claim_id', 'measurement_id', 'fact_id', 'row_id'):
            value = payload.get(key)
            if value is not None:
                candidates.add(str(value))
        if candidates & claim_ids:
            return True
    return False


def claim_id_of(node):
    """节点承载的主张 id（`wiki_provenance_links.claim_id`），没有就 None。

    `Claim` 节点的 `payload.claim_id` 才是契约字段；`ref_key` 只是它的兜底（`wiki_jobs` 与
    `wiki_projection.entry_key_part_for_node()` 用的是同一个兜底）。SQL/文档节点没有主张，
    返回 None —— 溯源行的 `claim_id` 可空，绝不借别的字段顶上。
    """
    payload = node_payload(node)
    value = payload.get('claim_id')
    if value is None or not str(value).strip():
        value = node.get('ref_key')
    text = '' if value is None else str(value).strip()
    return text or None


def select_candidate(entries, *, hint=None) -> tuple:
    """从投影出的候选里选**唯一**交付词条（见模块头 WHY 4）。

    返回 `(candidate, skipped_entry_types)`；`candidate` 为 None 表示这个运行一个词条都投影
    不出来（`project_entry()` 对 `SQLQuery` / `VerificationCheck` / `ConflictRecord` /
    `Document` / `Chunk` 返回 None —— 它们是证据载体与溯源，不是业务身份）。
    """
    grouped = {}
    for entry in entries:
        grouped.setdefault(entry['entry_type'], []).append(entry)
    if not grouped:
        return None, []
    if hint in grouped:
        chosen = hint
    else:
        # 节点数最多者胜；并列时 entry_type 字典序（确定性）。
        chosen = sorted(grouped, key=lambda key: (-len(grouped[key]), key))[0]
    return grouped[chosen][0], tuple(sorted(key for key in grouped if key != chosen))


@dataclass
class DraftPlan:
    """一次整理的**纯**计划：数据全部备好，落库由 `persist_draft()` 做。

    纯数据 + 只读属性，是为了让"选任务 / 挑候选 / 组正文 / 分 marker"这几件事能在没有数据库、
    没有 psycopg2 的解释器里被测试。
    """

    build_id: str
    space_id: str
    run_id: str
    mode: str
    entry: dict = field(default_factory=dict)
    evidence_refs: tuple = ()
    claim_ids: dict = field(default_factory=dict)
    summary: dict = field(default_factory=dict)
    skipped_candidate_entry_types: tuple = ()
    deferred_derivations: dict = field(default_factory=dict)
    node_count: int = 0
    selected_node_count: int = 0
    truncated: bool = False

    @property
    def citations(self) -> list:
        """§5.12 的 `citations`：`[{'marker': '[1]', 'evidence_ref_id': 'wev-…'}]`。

        marker 按**证据引用的稳定顺序**分配（`evidence_refs` 已按节点排序），所以同一个运行
        两次整理得到同一组 marker。
        """
        return [{'marker': '[%d]' % (index + 1), 'evidence_ref_id': ref['evidence_ref_id']}
                for index, ref in enumerate(self.evidence_refs)]

    @property
    def citations_by_evidence_id(self) -> dict:
        """`evidence_id -> marker`，供正文渲染使用。"""
        return {str(ref['evidence_id']): '[%d]' % (index + 1)
                for index, ref in enumerate(self.evidence_refs)}


def body_markdown(plan: DraftPlan) -> str:
    """模板正文（`generation_method='template'`）。

    只写能从计划里读到的真话：运行号、节点数、词条身份、按节点分组的证据。
    证据一行一条（最多 `BODY_EVIDENCE_LINES` 行），每条带 `[n]` marker —— marker 指向 revisions
    的 `citations`，所以正文编号与引用编号是同一套。没有可投影的证据时正文如实写"没有"，
    而不是编一段摘要（`WIKI_API.md` §11.4 的纪律）。
    """
    entry = plan.entry
    markers = plan.citations_by_evidence_id
    business_key = entry.get('business_key') or {}
    lines = ['# %s' % entry.get('title') or '# （无标题）', '']
    lines.append('> 由运行 `%s` 模板生成（`%s`，mode=`%s`）。草稿：核验状态与实体绑定均未人工确认。'
                 % (plan.run_id, TEMPLATE_VERSION, plan.mode))
    lines.append('')
    lines.append('- 词条类型：`%s`' % entry.get('entry_type'))
    lines.append('- 业务键：`%s` / `%s` / `%s`'
                 % (business_key.get('namespace'), business_key.get('entry_type'),
                    _clip(business_key.get('key_part'), 120)))
    lines.append('- 身份确认：`%s`' % entry.get('confirmation'))
    lines.append('- 证据引用：%d 条（运行 %d 个节点，本次整理 %d 个）'
                 % (len(plan.evidence_refs), plan.node_count, plan.selected_node_count))
    if plan.truncated:
        lines.append('- **注意**：本运行的节点数超过读取上限 %d，本次只整理了前 %d 个。'
                     % (NODES_READ_LIMIT, plan.selected_node_count))
    if plan.summary:
        lines.append('- 投影：支持节点 %s / 不支持节点 %s；证据引用 %s（其中 run-scoped %s）'
                     % (plan.summary.get('supported_node_count'),
                        plan.summary.get('unsupported_node_count'),
                        plan.summary.get('evidence_ref_count'),
                        plan.summary.get('run_scoped_evidence_ref_count')))
    lines.append('')
    if entry.get('summary'):
        lines.extend(['## 摘要', '', entry['summary'], ''])
    lines.extend(['## 证据', ''])
    if not plan.evidence_refs:
        lines.append('本次整理没有可投影的证据引用（`evidence_nodes` 里该运行没有可用节点）。')
    for index, ref in enumerate(plan.evidence_refs):
        if index >= BODY_EVIDENCE_LINES:
            break
        excerpt = _clip(ref['excerpt'], 160) if ref['excerpt'] else '（无引文）'
        lines.append('- %s `%s` %s —— %s'
                     % (markers.get(str(ref['evidence_id']), '[-]'), ref['raw_node_type'], excerpt,
                        _clip(ref['source_ref'], 80)))
    if len(plan.evidence_refs) > BODY_EVIDENCE_LINES:
        lines.append('- …另有 %d 条证据引用（见本版本的 `citations`）'
                     % (len(plan.evidence_refs) - BODY_EVIDENCE_LINES))
    lines.extend(['', '## 未落库的部分（如实记录）', ''])
    lines.append('- Wiki 派生关系：%d 条计划，尚未落库（端点在 build 步骤铸造）。'
                 % (plan.deferred_derivations.get('deferred') or 0))
    lines.append('- 主张（`wiki_assertions`）：本版只铸造词条端点与证据引用，未铸造主张端点。')
    lines.append('- 已投影但未交付的候选词条类型：%s'
                 % (', '.join('`%s`' % value for value in plan.skipped_candidate_entry_types)
                    or '无'))
    body = '\n'.join(lines)
    return body if len(body) <= BODY_MAX_CHARS else body[:BODY_MAX_CHARS - 1] + '…'


def plan_for_job(job, nodes, *, edges=()) -> DraftPlan:
    """把（任务行 + 该运行的证据节点）变成一份 `DraftPlan`。**纯函数**：不连库、不写库。

    任何"投影不出来"都**不**在这里吞掉：`ProjectionError` 直接冒泡，由
    `wiki_jobs.execute_build()` 的 `failure_record()` 变成任务行上的错误码与详情
    （例如 `WIKI_ENTITY_KEY_UNRESOLVED` 会原样出现在任务详情里，便于定位是哪个节点）。
    """
    selection = selection_filter(job)
    selected = [node for node in nodes if node_selected(node, selection)]
    refs, projected, claim_ids = [], [], {}
    for node in selected:
        ref = project_evidence_ref(job['space_id'], node, run_id=job['run_id'])
        refs.append(ref)
        claim_id = claim_id_of(node)
        if claim_id is not None:
            claim_ids[str(ref['evidence_id'])] = claim_id
        entry = project_entry(job['space_id'], wiki.DEFAULT_NAMESPACE,
                              normalize_node_type(ref['raw_node_type'])['node_type'],
                              node_payload(node), run_id=job['run_id'],
                              ref_key=node.get('ref_key'))
        if entry is not None:
            projected.append(entry)
    candidate, skipped = select_candidate(projected, hint=job.get('entry_type_hint'))
    if candidate is None:
        # 与 §8.2 的 `WIKI_NO_EVIDENCE` 分开：这里有证据，只是没有节点承载业务身份。
        wiki.fail('WIKI_BUILD_FAILED', 409,
                  hint='run %s projects no entry identity (node types: %s)'
                       % (job['run_id'],
                          ', '.join(sorted({str(node.get('node_type')) for node in selected}))
                          or 'none'))
    derivations = plan_wiki_derived_relations(selected, space_id=job['space_id'],
                                              namespace=wiki.DEFAULT_NAMESPACE,
                                              run_ids=[job['run_id']])
    counts = derivations.get('counts') or {}
    summary = projection_summary(selected, edges, space_id=job['space_id'],
                                 namespace=wiki.DEFAULT_NAMESPACE)
    return DraftPlan(build_id=job['build_id'], space_id=job['space_id'], run_id=job['run_id'],
                     mode=job.get('mode') or 'template', entry=candidate,
                     evidence_refs=tuple(refs), claim_ids=claim_ids, summary=summary,
                     skipped_candidate_entry_types=skipped,
                     deferred_derivations={'counts': counts, 'deferred': sum(counts.values())},
                     node_count=len(nodes), selected_node_count=len(selected))


def result_of(plan: DraftPlan, revision_id: str) -> dict:
    """`wiki_jobs.complete_build()` 的 `result`（§5.10 的交付字段）。

    **不带** `suggested_merges`：`complete_build()` 在缺键时沿用任务行上提交阶段算出的建议
    （`submit_build()` 的 `title_overlap_suggestions()`），本模块没有比它更新的建议，
    传一个空列表反而会把那些建议覆盖掉。
    """
    entry_id = plan.entry['entry_id']
    return {'entry_id': entry_id, 'candidate_entry_id': entry_id, 'revision_id': revision_id}


def failure_detail(exc, *, limit=DETAIL_MAX) -> str:
    """异常 → `error_detail` 文本。"""
    text = ' '.join(('%s: %s' % (type(exc).__name__, exc)).split())
    return text[:limit] if text else type(exc).__name__


def _clip(value, limit):
    text = ' '.join(str('' if value is None else value).split())
    return text if len(text) <= limit else text[:limit - 1] + '…'


# ---------------------------------------------------------------------------
# 2. 纯逻辑：worker 的决策依据
# ---------------------------------------------------------------------------

def claim_error_verdict(exc) -> tuple:
    """领取失败 → `(error_code, fatal)`。

    `wiki_jobs.claim_build()` 抛出的是带稳定 `WIKI_*` 码的 `HTTPException`：
    `WIKI_PERMISSION_DENIED`（撤权，`_lock_actor()` 复查没过）与 `SESSION_INVALID`
    （会话失效，`auth.py:212`）都属于 `FATAL_CLAIM_CODES` —— 不可重试。没有码的异常
    （连接抖动、锁等待）返回 `(None, False)`：保持 `queued`，下一轮再试，
    不把一次网络抖动写成任务的死亡证明。

    返回的码已经过 `normalize_error_code()`：`SESSION_INVALID` 会变成 `WIKI_SESSION_INVALID`，
    因为 `error_code` 的 CHECK 只收 `WIKI_*`（021:590），否则 `sanitize_error_code()` 会把它
    降级成 `WIKI_BUILD_FAILED`，真正的原因就丢了。
    """
    code = wiki.error_code_of(exc)
    if code is None:
        return None, False
    return normalize_error_code(code), code in FATAL_CLAIM_CODES


def is_terminal(job) -> bool:
    """任务是否已到终态（`succeeded` / `failed`）。"""
    return (job or {}).get('status') in TERMINAL_STATES


def safe_slug(value, *, limit=48) -> str:
    """`host:pid` 这类标识的安全化（只用于日志与错误详情）。"""
    text = re.sub(r'[^0-9A-Za-z_.:-]+', '-', str(value or '')).strip('-')
    return text[:limit] or 'unknown'


def worker_name(pid=None) -> str:
    """worker 实例标识：`wiki-build:<host>:<pid>`（进日志，也进中断落账的详情）。"""
    return 'wiki-build:%s:%s' % (safe_slug(socket.gethostname(), limit=32),
                                 pid if pid is not None else os.getpid())


def fingerprint(value) -> str:
    """给日志用的短指纹（不参与任何身份判定）。"""
    return digest64(str(value))[:12]


def jsonable(value):
    """`canonical_json` 的再导出：worker 打印任务行时用同一种序列化。"""
    return canonical_json(value)


def ordered_unique(values: Iterable) -> list:
    """保序去重（调试输出用）。"""
    seen, out = set(), []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


# ---------------------------------------------------------------------------
# 3. 纯逻辑 + 读路径：worker 用谁的身份开工
# ---------------------------------------------------------------------------
#
# WHY 需要这一步：`wiki_jobs.claim_build()` / `complete_build()` 的权限复查最终落到
# `auth.lock_identity()`（`auth.py:202-218`），而它要求身份里带 `_session_hash`
# —— 也就是"一个真实有效的会话"。任务行里只有 `owner_id`（021:554），没有 token，
# 所以 worker **必须**自己解决"以哪个会话的身份开工"，而且只有两个合法来源：
#
#   a. 任务 owner 自己在 `auth_sessions` 里的一个**未撤销、未过期**会话。worker 需要一个
#      真实 token 来证明它拿到的就是那个会话（表里只有 `token_hash`），所以 token 通过
#      `--session-token` 或 `DATAMIND_WIKI_WORKER_SESSION` 提供，且必须与 owner 的这一行匹配。
#   b. 本机无登录演示身份（`auth.demo_identity()`）。它与 API 在回环地址上给每个请求的身份
#      **完全同一个**（`auth.py:59-69`），并且只对 `user-local-admin` 生效。这不是
#      "worker 自己给自己发权限"：它复现的是同一台机器上 API 本来就会做出的授权判断，
#      而且复查仍然在 `claim_build()` 的事务里重新跑一遍。
#
# 两条都不成立 → **不开工**，把任务落成失败（`WIKI_WORKER_IDENTITY_UNAVAILABLE`）。
# 否则任务会永远停在 `queued`，而 §5.10 的读接口会对用户永远显示"排队中"。

DEMO_OWNER_ID = 'user-local-admin'

#: owner 没有可用身份时的错误码（满足 021:590 的 `^WIKI_[A-Z0-9_]+$`）。
ERROR_IDENTITY_UNAVAILABLE = 'WIKI_WORKER_IDENTITY_UNAVAILABLE'
#: worker 被信号打断、任务已领取但未落终态时的错误码。
ERROR_INTERRUPTED = 'WIKI_BUILD_INTERRUPTED'


class IdentityUnavailable(RuntimeError):
    """任务 owner 没有可用于开工的身份（见本节 WHY）。"""

    def __init__(self, code=ERROR_IDENTITY_UNAVAILABLE, detail=''):
        super().__init__('%s: %s' % (code, detail) if detail else code)
        self.code = code
        self.detail = detail


def current_session_token(argv_token=None, environ=None):
    """本次 worker 运行使用的会话 token（命令行优先于环境变量）。"""
    environ = os.environ if environ is None else environ
    token = argv_token or environ.get('DATAMIND_WIKI_WORKER_SESSION')
    token = (token or '').strip()
    return token or None


def token_hash(token) -> str:
    """`auth_sessions.token_hash` 的算法（`auth.py:168` 逐字一致）。"""
    return hashlib.sha256(str(token).encode()).hexdigest()


def demo_identity_ok() -> bool:
    """本机无登录演示身份是否可用（`auth.demo_allowed()` 的 worker 等价判定）。

    与 API 的差异是**收紧**的，两处：

    1. 不读 `DATAMIND_LOCAL_NO_LOGIN`。那个开关的语义是"强制匿名绕过"，对一个无人值守的进程
       是危险的默认；要禁用演示身份用 `--no-demo-identity`，要强制启用用 `--demo-identity`。
    2. 判定顺序固定为"本机地址 → 回环 API 探针"（见 `host_is_loopback()`）。仍然只认本机，
       没有任何请求头参与。
    """
    return host_is_loopback()


#: 回环 API 的探针地址：与 `scripts/start_api_background.py:ready()` 同一个。
LOOPBACK_API_URL = 'http://127.0.0.1:8000/health'
#: 探针超时（秒）。够短，不拖慢启动；失败只是让演示身份不可用，不影响任何写路径。
LOOPBACK_PROBE_TIMEOUT = 0.5


def _api_reachable(url=LOOPBACK_API_URL, timeout=LOOPBACK_PROBE_TIMEOUT) -> bool:
    """回环地址上的 DataMind API 是否可达（本机判定的最后一条证据）。"""
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status == 200
    except (OSError, ValueError, urllib.error.URLError):
        return False


def host_is_loopback(host=None, *, probe_api=True) -> bool:
    """本机地址判定（`socket_host()` 的 worker 版：不信任任何请求头）。

    四步，任一步成立即认定本机：

    1. 字面量：`localhost` / `127.0.0.0/8` / `::1`；
    2. 主机名解析出的地址里有回环地址；
    3. **传入的就是本机主机名，且它能解析出任何一个地址** —— 直接命中；
    4. 回环地址上的 DataMind API 可达（`probe_api=False` 可关掉）。

    第 3 步是 2026-10-17 补的，修一个实测到的故障：本机（Windows + VPN 虚拟网卡）的
    `socket.gethostname()` 只解析到 `fe80::` 段（IPv6 链路本地）和 10/172 段，
    一个回环都没有，于是只走第 4 步 —— 而第 4 步是一次 **0.5 秒超时**的 HTTP 探针。
    只要那一刻 API 正忙（实测：跑前端构建/截图时），探针超时，worker 就判成"不是本机"，
    于是演示身份不可用、任务**永久落成 WIKI_WORKER_IDENTITY_UNAVAILABLE**。

    第 3 步在语义上是安全的：`socket.gethostname()` 返回的是**本机**主机名，
    它解析出的地址按定义就是本机网卡地址；这里没有任何请求头/客户端地址参与，
    也不放松"只认本机"这一条。第 4 步保留作为兜底（主机名解析失败的机器）。
    """
    local_hostname = socket.gethostname().lower()
    if host is not None:
        text = str(host).strip().lower()
        if text in ('localhost', '127.0.0.1', '::1', '::ffff:127.0.0.1') or text.startswith('127.'):
            return True
        try:
            infos = socket.getaddrinfo(text, None)
        except (OSError, UnicodeError):
            infos = []
        if any(info[4][0] in ('127.0.0.1', '::1') for info in infos):
            return True
        # 第 3 步：本机主机名解析出任何地址就认定本机（不依赖网络探针）。
        if text == local_hostname and infos:
            return True
        return bool(probe_api and text in (local_hostname, 'localhost') and _api_reachable())
    if host_is_loopback(local_hostname, probe_api=False):
        return True
    return bool(probe_api and _api_reachable())


def live_session_identity(cur, owner_id, token):
    """owner 的实时会话身份，或 None（token 与 owner 的这一行不匹配时也是 None）。

    只读 `auth_users` / `auth_sessions`（`datamind_app` 对它们有 SELECT）。判据与
    `auth.lock_identity()` 的 SQL 逐项对齐：`u.active`、`s.revoked_at IS NULL`、
    `s.expires_at > now()`、`s.token_hash`。**不**在这里做权限判定：角色判定的唯一权威是
    `claim_build()` / `complete_build()` 事务里的 `_lock_actor()`。
    """
    token = current_session_token(token)
    if not token:
        return None
    cur.execute('''SELECT u.user_id, u.display_name, u.roles, s.csrf_token
        FROM auth_users u JOIN auth_sessions s USING(user_id)
        WHERE u.user_id = %s AND s.token_hash = %s AND u.active
          AND s.revoked_at IS NULL AND s.expires_at > now()
        ORDER BY s.expires_at DESC LIMIT 1''', (owner_id, token_hash(token)))
    row = cur.fetchone()
    if not row:
        return None
    return {'user_id': row['user_id'], 'display_name': row['display_name'],
            'roles': list(row['roles'] or []), 'csrf_token': row['csrf_token'],
            '_session_hash': token_hash(token), '_source': 'session'}


def resolve_identity(cur, job, *, session_token=None, allow_demo=None) -> dict:
    """该任务用哪个身份开工（见本节 WHY）。失败抛 `IdentityUnavailable`。

    `allow_demo` 默认取 `demo_identity_ok()`；测试与"必须显式给 token"的部署可以传 False。
    """
    owner_id = job['owner_id']
    identity = live_session_identity(cur, owner_id, session_token)
    if identity is not None:
        return identity
    if allow_demo is None:
        allow_demo = demo_identity_ok()
    if allow_demo and owner_id == DEMO_OWNER_ID:
        from .auth import demo_identity
        return {**demo_identity(), '_source': 'demo'}
    raise IdentityUnavailable(
        ERROR_IDENTITY_UNAVAILABLE,
        'owner %s has no live session and the loopback demo identity does not apply; '
        'pass --session-token (or DATAMIND_WIKI_WORKER_SESSION) with a session of that owner'
        % owner_id)


def claim_ids_for(plan: DraftPlan) -> dict:
    """`{evidence_id: claim_id}`（`plan.claim_ids` 的只读视图，便于调试输出）。"""
    return dict(plan.claim_ids)


# ---------------------------------------------------------------------------
# 4. 数据库路径：读取（全部只读）
# ---------------------------------------------------------------------------

def load_run(cur, run_id):
    """目标运行的必要列；不存在返回 None。"""
    cur.execute('SELECT run_id, conversation_id, status, question FROM runs WHERE run_id = %s',
                (run_id,))
    return cur.fetchone()


def load_nodes(cur, run_id, *, limit=NODES_READ_LIMIT):
    """该运行的证据节点（`evidence_nodes` 的真实六列，投影层只读这些）。

    排序固定为 `(node_type, evidence_id)`：投影身份只依赖列内容、与顺序无关，但正文与 marker
    的分配依赖顺序，所以这里把它钉成确定的。返回 `(rows, truncated)`。
    """
    cur.execute('''SELECT evidence_id, run_id, node_type, ref_key, payload, created_at
        FROM evidence_nodes WHERE run_id = %s ORDER BY node_type, evidence_id LIMIT %s''',
                (run_id, limit + 1))
    rows = list(cur.fetchall())
    return rows[:limit], len(rows) > limit


def load_edges(cur, run_id, *, limit=NODES_READ_LIMIT):
    """该运行的证据边（只喂给 `projection_summary()` 做类型统计，不铸造关系）。"""
    cur.execute('''SELECT edge_id, run_id, src_evidence_id, dst_evidence_id, edge_type, weight
        FROM evidence_edges WHERE run_id = %s ORDER BY edge_id LIMIT %s''', (run_id, limit))
    return list(cur.fetchall())


def select_queued(dsn, identity, *, limit=None, build_id=None):
    """列出可以尝试领取的任务（`status='queued'`），**不改任何状态**。

    只挑 owner 与身份一致的行，除非身份是 admin（`auth.py:195/217` 的超管旁路，与
    `claim_build()` 的 WHERE 子句同款）。真正的领取仍然由 `claim_build()` 的原子 UPDATE 决定。
    """
    owner = None if wiki.is_admin(identity) else wiki.user_id_of(identity)
    sql, params = queued_sql(owner=owner, build_id=build_id)
    if limit is not None:
        sql = sql.replace(' FOR UPDATE SKIP LOCKED', '')
        sql += ' LIMIT %s FOR UPDATE SKIP LOCKED'
        params.append(int(limit))
    with wiki.connect(dsn) as conn, conn.cursor(cursor_factory=wiki.RealDictCursor) as cur:
        cur.execute(sql, tuple(params))
        return [dict(row) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# 5. 数据库路径：落库（应用角色；只写 wiki_* 表）
# ---------------------------------------------------------------------------

_ENTRY_INSERT = '''INSERT INTO wiki_entries(entry_id, space_id, owner_id, entry_type, namespace,
        key_part, confirmation, title, aliases, category, summary, demo_data)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    ON CONFLICT (space_id, namespace, entry_type, key_part) DO NOTHING
    RETURNING entry_id'''

_EVIDENCE_COLUMNS = ('evidence_ref_id, space_id, evidence_fingerprint, source_kind, source_ref, '
                     'source_sha256, locator_kind, locator, record_sha256, excerpt, raw_node_types, '
                     'supported, unsupported_reason, run_scoped, run_scoped_run_id')

_EVIDENCE_INSERT = ('INSERT INTO wiki_evidence_refs(' + _EVIDENCE_COLUMNS + ''')
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    ON CONFLICT (space_id, evidence_fingerprint) DO NOTHING''')

_PROVENANCE_LINK_INSERT = '''INSERT INTO wiki_provenance_links(provenance_link_id, evidence_ref_id,
        run_id, evidence_id, claim_id, revision_id)
    VALUES (%s,%s,%s,%s,%s,%s)
    ON CONFLICT (evidence_ref_id, run_id) DO NOTHING'''


def evidence_params(plan: DraftPlan, ref) -> tuple:
    """一条投影结果 → `wiki_evidence_refs` 的 15 个参数（顺序与 `_EVIDENCE_COLUMNS` 一致）。

    `source_sha256` 的空串归一成 SQL NULL：021:350 与 `run_scoped = (source_sha256 IS NULL)`
    的 CHECK 都要求"空串不是快照"。
    """
    return (ref['evidence_ref_id'], plan.space_id, ref['evidence_fingerprint'], ref['source_kind'],
            ref['source_ref'], (ref['source_sha256'] or None), ref['locator_kind'],
            wiki._jsonb(ref['locator']), ref['record_sha256'], ref['excerpt'] or '',
            list(ref['raw_node_types']), bool(ref['supported']), ref['unsupported_reason'],
            bool(ref['run_scoped']), ref['run_scoped_run_id'])


def provenance_link_id(evidence_ref_id, run_id) -> str:
    """`wprov-` + 24 hex：**由身份派生**，所以重放同一次整理不会写出第二行。

    `wiki_provenance_links` 的 `UNIQUE (evidence_ref_id, run_id)` 才是权威；这个 id 只是把
    "同一对 (引用, 运行)" 稳定地映射到同一个主键上。
    """
    return allocate_id('provenance_link', seed='%s\x1f%s' % (evidence_ref_id, run_id))


def _ensure_entry(cur, plan: DraftPlan, identity) -> tuple:
    """词条落库/复用。返回 `(entry_id, reused)`。

    直接 INSERT 而不走 `wiki.create_entry()`：后者的职责是**重新推导** `key_part`
    （`entity_identity()`），而这里的身份已经是 `wiki_projection.entry_identity()` 的结果，
    再推导一遍就有了两个真相来源。业务键冲突（同空间同身份）时复用已存在的那一行，
    与 `create_entry()` 的归一语义一致。
    """
    entry = plan.entry
    cur.execute(_ENTRY_INSERT, (entry['entry_id'], plan.space_id, wiki.user_id_of(identity),
                                entry['entry_type'], entry['business_key']['namespace'],
                                entry['key_part'], entry['confirmation'], entry['title'],
                                list(entry['aliases'] or []), entry['category'],
                                entry['summary'] or '', bool(entry.get('demo_data', True))))
    row = cur.fetchone()
    if row is not None:
        return row['entry_id'], False
    cur.execute('''SELECT entry_id FROM wiki_entries
        WHERE space_id = %s AND namespace = %s AND entry_type = %s AND key_part = %s''',
                (plan.space_id, entry['business_key']['namespace'], entry['entry_type'],
                 entry['key_part']))
    row = cur.fetchone()
    if row is None:  # pragma: no cover - 唯一约束失败却查不到行，只能是并发删除
        wiki.fail('WIKI_VERSION_CONFLICT', 409, hint='the entry identity raced with a delete')
    return row['entry_id'], True


def _ensure_evidence(cur, plan: DraftPlan) -> dict:
    """证据引用落库/复用。返回 `{evidence_fingerprint: evidence_ref_id}`。"""
    for ref in plan.evidence_refs:
        cur.execute(_EVIDENCE_INSERT, evidence_params(plan, ref))
    if not plan.evidence_refs:
        return {}
    cur.execute('''SELECT evidence_ref_id, evidence_fingerprint FROM wiki_evidence_refs
        WHERE space_id = %s AND evidence_fingerprint = ANY(%s)''',
                (plan.space_id, [ref['evidence_fingerprint'] for ref in plan.evidence_refs]))
    return {row['evidence_fingerprint']: row['evidence_ref_id'] for row in cur.fetchall()}


def revision_base_for(entry_id, dsn):
    """这次整理要用的 `base_revision_id`：词条的**最新 tip**，首版是 None。

    WHY 不能一律传 None：`wiki.create_revision()` 的首版规则是"没有版本的词条才能
    `base_revision_id=None`"（`wiki.base_revision_problem()`，021:777）。于是"同一个运行再整理一次"
    （换一个 `request_id`，§5.9 的幂等键不同）会在第二次撞上 409 `WIKI_VERSION_CONFLICT`,
    整理明明做完了却落败 —— 实测缺陷。投影的身份是确定性的（`entry_id` 由业务键派生），
    所以第二次整理落在**同一个词条**上，正确语义是追加一个新版本。

    tip 的判据与 `wiki._tip_revision_ids()` 同款：没有子版本的版本。并列时用最新创建的那个
    （`created_at, revision_id` 倒序），与 §5.4 的版本历史同序。
    """
    with wiki.connect(dsn) as conn, conn.cursor(cursor_factory=wiki.RealDictCursor) as cur:
        cur.execute('''SELECT revision_id FROM wiki_revisions r WHERE r.entry_id = %s
            AND NOT EXISTS (SELECT 1 FROM wiki_revisions child
                            WHERE child.entry_id = r.entry_id
                              AND child.base_revision_id = r.revision_id)
            ORDER BY r.created_at DESC, r.revision_id DESC LIMIT 1''', (entry_id,))
        row = cur.fetchone()
    return row['revision_id'] if row else None


def append_plan(plan: DraftPlan, entry_id, dsn, identity, *, citations, request_id=None) -> str:
    """把计划写成该词条的新版本（§5.12）。返回 `revision_id`。

    版本必须走 `wiki.create_revision()`：append-only 触发器（WIKI1）、`base_revision_id` 基线
    校验、引用可达性校验（`_validate_evidence_refs`）都在那里，绕过它就等于绕过 §5.12。
    `citations` 用的是**库里真实存在**的 `evidence_ref_id`（投影派生的 id 与它一致，
    但以查回来的为准 —— 引用可达性是 409/422 之外的硬约束）。
    """
    base_revision_id = revision_base_for(entry_id, dsn)
    revision = wiki.create_revision(
        entry_id,
        {'request_id': request_id or ('rev-' + plan.build_id),
         'base_revision_id': base_revision_id, 'title': plan.entry['title'],
         'body_markdown': body_markdown(plan), 'generation_method': 'template',
         'template_version': TEMPLATE_VERSION, 'citations': list(citations),
         'assertions': [], 'relations': [],
         'change_note': 'build %s from run %s (mode=%s)'
                        % (plan.build_id, plan.run_id, plan.mode)},
        dsn, identity)
    return revision['revision']['revision_id']


def resolved_citations(plan: DraftPlan, evidence_ref_ids) -> list:
    """计划里的 marker → 库里真实存在的 `wev-` id（缺失的引用直接不发出去）。"""
    return [{'marker': citation['marker'],
             'evidence_ref_id': evidence_ref_ids[ref['evidence_fingerprint']]}
            for citation, ref in zip(plan.citations, plan.evidence_refs)
            if ref['evidence_fingerprint'] in evidence_ref_ids]


def persist_draft(plan: DraftPlan, dsn, identity, *, request_id=None) -> dict:
    """把计划落库：词条 → 证据引用 → 版本（服务层）→ 溯源行。返回 `result_of()` 的结果。

    词条身份来自投影层（`wiki_projection.entry_identity()`），是**确定性**的：同一个运行的
    同一个节点永远落在同一个 `entry_id` 上。所以"再整理一次"（同运行、新 `request_id`）是给
    同一个词条**追加一个版本**（`append_plan()` 取当前 tip 作基线），而不是撞 409 或建第二个词条。

    溯源行在版本之后写：它的 `revision_id` 指向刚铸造的版本（021:399 要求
    `revision_id` 与 `assertion_id` 至少一个非空），这是"引用来自哪个运行"的可读记录。
    """
    with wiki.connect(dsn) as conn, conn.cursor(cursor_factory=wiki.RealDictCursor) as cur:
        entry_id, reused_entry = _ensure_entry(cur, plan, identity)
        evidence_ref_ids = _ensure_evidence(cur, plan)

    revision_id = append_plan(plan, entry_id, dsn, identity,
                              citations=resolved_citations(plan, evidence_ref_ids),
                              request_id=request_id)

    with wiki.connect(dsn) as conn, conn.cursor(cursor_factory=wiki.RealDictCursor) as cur:
        for ref in plan.evidence_refs:
            stored_ref_id = evidence_ref_ids.get(ref['evidence_fingerprint'])
            if stored_ref_id is None:  # pragma: no cover - 插入后必须能查到
                continue
            cur.execute(_PROVENANCE_LINK_INSERT,
                        (provenance_link_id(stored_ref_id, plan.run_id), stored_ref_id,
                         plan.run_id, str(ref['evidence_id']),
                         plan.claim_ids.get(str(ref['evidence_id'])), revision_id))
    result = result_of(plan, revision_id)
    result.update({'reused_entry': reused_entry,
                   'skipped_candidate_entry_types': list(plan.skipped_candidate_entry_types)})
    return result


def build_draft(job, dsn, identity, *, request_id=None) -> dict:
    """生产整理回调：读运行 → 投影 → 落库 → 返回 §5.10 的结果字典。

    这是 `wiki_jobs.execute_build(..., builder=…)` 的 `builder`：**它自己不动任务行**，
    状态推进与两次权限复查全在那三步里。
    """
    with wiki.connect(dsn) as conn, conn.cursor(cursor_factory=wiki.RealDictCursor) as cur:
        run_row = load_run(cur, job['run_id'])
        if run_row is None:
            wiki.fail('WIKI_RUN_NOT_FOUND', 404, hint='run %s disappeared' % job['run_id'])
        nodes, truncated = load_nodes(cur, job['run_id'])
        edges = load_edges(cur, job['run_id'])
    if not nodes:
        # `submit_build()` 提交时查过 `evidence_nodes`；行没了就是真的没了（§8.2）。
        wiki.fail('WIKI_NO_EVIDENCE', 409,
                  hint='run %s has no evidence_nodes row left to project' % job['run_id'])
    plan = plan_for_job(job, nodes, edges=edges)
    plan.truncated = truncated
    return persist_draft(plan, dsn, identity, request_id=request_id)


def builder_for(dsn, identity=None, *, request_id=None) -> Callable:
    """`execute_build` 需要的 `builder` 回调（`job_row -> result`）。

    委托给 `builder_for(dsn, identity)` 时**必须传调用方的完整身份**（含 `roles` 与
    `_session_hash`）：`wiki.create_revision()` 内部还会跑一次 `_lock_actor()`，只给
    `job_identity(job)`（只有 `user_id`）会让那次角色门以 `roles=[]` 拒掉，整理明明做完了
    却落 `WIKI_PERMISSION_DENIED`。实测缺陷：worker 第一版就是这么写的。
    """
    def build(job):
        return build_draft(job, dsn, identity or job_identity(job), request_id=request_id)
    return build


__all__ = ['BODY_EVIDENCE_LINES', 'BODY_MAX_CHARS', 'DEMO_OWNER_ID', 'DETAIL_MAX', 'DraftPlan',
           'ERROR_CODE_ALIASES', 'ERROR_IDENTITY_UNAVAILABLE', 'ERROR_INTERRUPTED', 'EXIT_ERROR',
           'EXIT_OK', 'EXIT_USAGE', 'FATAL_CLAIM_CODES', 'IdentityUnavailable',
           'LOOPBACK_API_URL', 'LOOPBACK_PROBE_TIMEOUT', 'NODES_READ_LIMIT', 'TEMPLATE_VERSION',
           'TERMINAL_STATES', 'app_dsn', 'append_plan', 'body_markdown', 'build_draft',
           'builder_for', 'claim_error_verdict', 'claim_id_of', 'claim_ids_for',
           'current_session_token', 'demo_identity_ok', 'evidence_params', 'failure_detail',
           'fingerprint', 'host_is_loopback', 'is_terminal', 'job_identity', 'jsonable',
           'live_session_identity', 'load_edges', 'load_nodes', 'load_run', 'node_selected',
           'normalize_error_code',
           'ordered_unique', 'persist_draft', 'plan_for_job', 'provenance_link_id', 'queued_sql',
           'resolve_identity', 'resolved_citations', 'result_of', 'revision_base_for', 'safe_slug',
           'select_candidate', 'select_queued', 'selection_filter', 'token_hash', 'worker_name']
