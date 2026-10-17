"""后端枚举 → 中文措辞的唯一映射（主张句子与词条标题都从这里取词）。

WHY 需要这个模块
----------------
``claims.text`` 与 ``wiki_entries.title`` 是**给人读的句子**。旧实现把核验结果直接拼成::

    测量 M001-1：工艺范围核验 out_of_range；来源比较 unresolved。

机器码进了句子，读的人得去代码里查它是什么意思。前端早就在做这件事：``stargraph/NodeDetail.vue``
的 ``COMPLIANCE_TEXT`` / ``AGREEMENT_TEXT`` / ``RESOLUTION_STATUS_TEXT`` 把同一批取值翻成中文，
未收录的取值原样露出（``?? String(v)``）。后端组装句子的地方必须用**同一套措辞**，否则同一行
数据在图谱详情与词条标题里会说两种话 —— 那是本仓库最不想要的"同一事实两种说法"。

三条纪律（与 ``NodeDetail.vue:190-220`` 的注释同款）
--------------------------------------------------
1. **只翻译已登记的取值**。未收录的取值**原样返回**：``label()`` 不做前缀/大小写/下划线推断，
   也不兜底成「未知」—— 兜底会把"我们没见过这个值"伪装成"这个值的意思就是未知"。
2. ``unresolved`` 有**两个**含义：``agreement`` 的「两侧仍不一致（未消解）」与 ``resolution_status``
   的「未消解」。所以这里**不合并成一张大表**：调用方按字段选表，合并会让一处说法覆盖另一处。
3. 前端改了措辞，这里要一起改：``tests/test_display_labels.py`` 逐字比对 ``NodeDetail.vue``。

不改历史数据
------------
这些函数只影响**新生成**的句子与标题。已经写进 ``claims`` / ``wiki_entries`` 的行不会被改写：
``db/migrations/`` 是"每次启动全量重放"的，历史数据只能走单独的重建流程，不在本模块职责内。
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------- 三张表，按字段分（纪律 2）

#: ``VerificationCheck.compliance``（``datamind/industrial_rules.py:36-79`` 写的那一列）。
#: 前两条与 ``NodeDetail.vue`` 的 ``COMPLIANCE_TEXT`` 逐字一致；后两条 NodeDetail 没有这两个取值
#: （它会原样露英文），但本库 350 行里有 70 行 ``needs_review``、35 行 ``insufficient_evidence``，
#: 措辞取 ``industrial/VerificationTable.vue:24-29`` ── 两张既有表都写「待复核」「证据不足」，
#: 这里采用 ``NodeDetail.vue`` 决议状态的同族说法「待人工复核」，与同屏的其它状态词一致。
COMPLIANCE_ZH = {
    'within_range': '落在区间内',
    'out_of_range': '超出区间',
    'needs_review': '待人工复核',
    'insufficient_evidence': '证据不足',
}

#: ``VerificationCheck.agreement``（同一行的来源比较结论）。与 ``AGREEMENT_TEXT`` 逐字一致。
AGREEMENT_ZH = {
    'consistent': '两侧一致',
    'resolved_equivalent': '换算后一致',
    'unresolved': '两侧仍不一致（未消解）',
    'needs_review': '待人工复核',
    'insufficient_evidence': '证据不足',
}

#: ``ConflictRecord.resolution_status``。与 ``RESOLUTION_STATUS_TEXT`` 逐字一致。
RESOLUTION_STATUS_ZH = {
    'resolved_by_authority': '已按权威规则裁决',
    'needs_review': '待人工复核',
    'unresolved': '未消解',
}

#: 主张句子（``industrial_claim_text()``）里的枚举词表：这句话只有「工艺范围核验 + 来源比较」
#: 两个槽，所以 ``agreement`` 的 ``unresolved`` 覆盖 ``resolution_status`` 的 —— 在这句话里它
#: 一定是来源比较的结论。``resolved_by_authority`` 只可能来自裁决，单独补进来免得出英文。
CLAIM_TEXT_ZH = {
    **COMPLIANCE_ZH,
    **AGREEMENT_ZH,
    'resolved_by_authority': RESOLUTION_STATUS_ZH['resolved_by_authority'],
}


# ---------------------------------------------------------------- 取词

def label(value, labels: dict) -> str:
    """一个取值的中文措辞；**未收录的原样返回**（纪律 1，不猜不兜底）。"""
    if value is None:
        return ''
    key = str(value)
    return labels.get(key, key)


def compliance_label(value) -> str:
    return label(value, COMPLIANCE_ZH)


def agreement_label(value) -> str:
    return label(value, AGREEMENT_ZH)


def resolution_label(value) -> str:
    return label(value, RESOLUTION_STATUS_ZH)


#: 正文里的"词"：ASCII 字母开头的标识符（``out_of_range`` / ``M001`` / ``A102`` 都算一个词）。
#: 用词级替换而不是 ``str.replace``：``within_range`` 不能被 ``range`` 之类的子串规则碰到。
_ENUM_TOKEN = re.compile(r'[A-Za-z][A-Za-z0-9_]*')


def localize_enum_tokens(text, labels: dict = CLAIM_TEXT_ZH) -> str:
    """把句子里**已登记**的枚举词换成中文，其余字符一个不动。

    用于已经写进库的句子（旧运行的 ``Claim`` 载荷）：句子本身不动，只在投影成词条标题时翻译。
    查不到的词原样留着 —— 这一条是为了不把 ``unresolved_because_x`` 这类新取值误译成别的意思。
    """
    if text is None:
        return ''
    return _ENUM_TOKEN.sub(lambda match: labels.get(match.group(0), match.group(0)), str(text))


# ---------------------------------------------------------------- 句子

def industrial_claim_text(row) -> str:
    """一行工业核验结果写成的中文句子（``agent.py::_record_industrial_evidence`` 的 ``claims.text``）。

    句式与旧实现逐字一样，只把两个枚举槽换成中文::

        测量 M001-1：工艺范围核验 超出区间；来源比较 两侧仍不一致（未消解）。

    未收录的取值原样留在句子里（例如 ``compliance='brand_new_verdict'`` 会原样出现），
    因为把没登记过的结论改写成「未知」比露英文更糟：那等于替规则引擎下了一个它没下的结论。
    """
    row = row if isinstance(row, dict) else {}
    return '测量 %s：工艺范围核验 %s；来源比较 %s。' % (
        row.get('measurement_id'),
        compliance_label(row.get('compliance')),
        agreement_label(row.get('agreement')),
    )
