"""澄清槽位契约的唯一实现（single source of truth）。

``datamind/api.py`` 在 ``NEEDS_CLARIFICATION`` 响应上做投影，``scripts/eval_clarification.py``
（B3 冻结集评测）只做再导出——两处都从本模块取同一份规则，避免 API、前端、评测各写一套
槽位表 / 正则表。

第 1 轮独立复查 L4 指出：原先把这份实现放在 ``scripts/eval_clarification.py``，导致生产服务
在**请求期** ``from scripts.eval_clarification import ...``，而 ``scripts/`` 甚至没有
``__init__.py``——评测脚本一旦被移动或删除，服务端就只剩空兜底。现在契约下沉到生产包内，
``scripts/`` 只做投影（re-export），调用面不变。
"""
from __future__ import annotations

import re

CONTRACT_VERSION = "clarification-slots-v1"

SLOT_ORDER = ("metric", "time_range", "grain", "document_version", "object_alias")
SLOT_QUESTIONS = {
    "time_range": "请指定统计时间范围（例如 2025 年或 2024 年）。",
    "metric": "请确认要查哪个指标（例如销售额还是订单数）。",
    "grain": "请确认统计粒度（按客户还是按订单）。",
    "object_alias": "请确认要查的对象（哪一款产品）。",
    "document_version": "请确认文档版本（当前生效版本还是历史版本）。",
}
DEFAULT_PRODUCTS = ["无线降噪耳机 Pro", "真无线耳机 Air", "智能手表 S6", "平板电脑 T11"]

TIME_RE = re.compile(r"20\d{2}\s*年|去年|今年|上个月|本月|本季度|全年|季度|至今|历史|近\d+个?月|近\d+年|昨日|上周|today|yesterday")
RANK_TREND_RE = re.compile(r"最高|最多|排名|前十|前\d+|第一|同比|环比|趋势|逐月|汇总|总额|总金额")
METRIC_REFUND_RE = re.compile(r"退款(多少|情况|总共|总额|怎么样|如何)")
METRIC_REFUND_CLEAR_RE = re.compile(r"金额|额度|多少笔|多少单|笔数|单数|元|件数")
METRIC_VAGUE_RE = re.compile(r"(销售|经营|业务|门店)(表现|情况|状况|业绩|成果)")
METRIC_NAMED_RE = re.compile(r"销售额|销售金额|销量|订单数|订单量|单量|净销售额|退款金额|客户数|库存")
GRAIN_VAGUE_RE = re.compile(r"人均|客单价|单均|平均|均值")
GRAIN_CLEAR_RE = re.compile(r"按[客户订单人店产品品类渠道区域月年]|每个[客户订单产品品类人店]|每笔|各[客户订单产品品类地区区域门店]|分[客户订单产品品类]")
VERSION_TRIGGER_RE = re.compile(r"最新|最新版|当前有效|旧版|老版|历史版本|上一个版本|哪个版本|版本")
VERSION_CLEAR_RE = re.compile(r"20\d{2}\s*版|v\d+(\.\d+)*|第\d+版|当前生效版本|已登记版本|上一版本")
DOC_NOUN_RE = re.compile(r"制度|规范|手册|流程|图纸|文档|标准|办法|白皮书|策略|报告|条款|说明书")
ALIAS_TRIGGER_RE = re.compile(r"那款|那台|那个|那批|这款|这台|这个|主推|爆款|热门款|旗舰耳机|旗舰机型|它|该产品|该产品线")
ALIAS_CLEAR_RE = re.compile(r"无线降噪耳机|真无线耳机|智能手表|平板电脑|智能门锁|扫地机器人|空气炸锅|破壁机|机械键盘")

#: 规则式 clarify() 的已知话术 → 槽位。只映射能唯一对应的两类，其余如实返回空，
#: 不猜测（宁可前端只显示原文，也不给一个编出来的槽位）。
RULE_ANSWER_SLOT_PATTERNS = (
    (re.compile(r"销售额的统计时间范围"), "time_range"),
    (re.compile(r"退款金额，还是退款笔数"), "metric"),
)


def has_time(question: str) -> bool:
    return bool(TIME_RE.search(question))


def detect_missing_slots(question: str) -> list[str]:
    """六类缺项检测（确定性规则，可解释）。返回按 SLOT_ORDER 排序的槽位列表。"""
    q = re.sub(r"\s+", "", question or "")
    slots: list[str] = []
    # 1) 口径歧义：退款金额 vs 退款笔数
    if METRIC_REFUND_RE.search(q) and not METRIC_REFUND_CLEAR_RE.search(q):
        slots.append("metric")
    # 2) 口径歧义：销售表现/经营情况（销售额 vs 订单数）
    elif METRIC_VAGUE_RE.search(q) and not METRIC_NAMED_RE.search(q):
        slots.append("metric")
    # 3) 时间缺失：排名/趋势/同比类必须有统计窗口
    if RANK_TREND_RE.search(q) and not has_time(q):
        slots.append("time_range")
    # 4) 粒度歧义：平均类未给分组粒度
    if GRAIN_VAGUE_RE.search(q) and not GRAIN_CLEAR_RE.search(q):
        slots.append("grain")
    # 5) 跨版本歧义：相对版本词 + 文档类名词，且未指定具体版本
    if VERSION_TRIGGER_RE.search(q) and DOC_NOUN_RE.search(q) and not VERSION_CLEAR_RE.search(q):
        slots.append("document_version")
    # 6) 对象别名歧义：指代词/非登记别名，且没有真实产品全名
    if ALIAS_TRIGGER_RE.search(q) and not ALIAS_CLEAR_RE.search(q):
        slots.append("object_alias")
    return [slot for slot in SLOT_ORDER if slot in slots]


def slot_candidates(slot: str, question: str, products: list[str] | None = None) -> list[str]:
    """系统为某个缺项提供的候选值（供前端一键补槽，也供评测检查冻结补充是否在候选内）。"""
    q = re.sub(r"\s+", "", question or "")
    if slot == "time_range":
        return ["2025年", "2024年", "近12个月"]
    if slot == "metric":
        return ["退款金额", "退款笔数"] if "退款" in q else ["销售额", "订单数"]
    if slot == "grain":
        return ["按客户", "按订单"]
    if slot == "object_alias":
        return list(products) if products else list(DEFAULT_PRODUCTS)
    if slot == "document_version":
        return ["当前生效版本", "上一版本"]
    return []


def clarification_contract(question: str, products: list[str] | None = None) -> dict:
    """结构化澄清契约：缺项字段 + 候选值 + 每槽追问话术。既有键含义不变。"""
    slots = detect_missing_slots(question)
    return {
        "clarification_contract_version": CONTRACT_VERSION,
        "clarification_missing_slots": slots,
        "clarification_candidates": {slot: slot_candidates(slot, question, products) for slot in slots},
        "clarification_slot_questions": {slot: SLOT_QUESTIONS[slot] for slot in slots},
    }


def rule_slots_for_answer(answer_text: str) -> list[str]:
    """把规则式 clarify() 的话术映射到槽位；无法唯一对应就返回空。"""
    text = answer_text or ""
    return [slot for pattern, slot in RULE_ANSWER_SLOT_PATTERNS if pattern.search(text)]


def clarification_contract_for_answer(question: str, answer_text: str,
                                     products: list[str] | None = None) -> dict:
    """线上澄清响应的结构化投影。

    只声明系统**确实问过**的槽位（由 clarify() 原文映射），不把检测器额外发现的槽位
    算进线上契约——否则会谎报一个系统并未提出的追问。
    """
    slots = rule_slots_for_answer(answer_text)
    return {
        "clarification_contract_version": CONTRACT_VERSION,
        "clarification_missing_slots": slots,
        "clarification_candidates": {slot: slot_candidates(slot, question, products) for slot in slots},
        "clarification_slot_questions": {slot: SLOT_QUESTIONS[slot] for slot in slots},
    }
