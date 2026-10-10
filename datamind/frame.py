"""Resolution frame for one conversational turn (WP7: frame the question *before* routing).

Why this module exists
----------------------
``/v1/chat`` accepts a client-supplied ``history`` (see ``docs/MULTI_TURN.md``), but the current
question alone decides routing, clarification and retrieval. A follow-up such as
「那上一年的呢？」 or 「第二个产品呢？」 therefore carries no routable signal at all: the model may
see the transcript later, but the routing/clarification/retrieval steps have already run on the
bare current question (audit §7.1). This module resolves the current turn into a structured frame
first, so those steps can use it.

Four hard rules (each one is enforced by ``tests/test_frame_multi_turn.py``)
--------------------------------------------------------------------------
1. **Explicit, traceable inheritance.** A value taken from an earlier turn records the 1-based
   history index it came from (``provenance``); ``inherited_from_turn`` is ``None`` when nothing
   was inherited. An inheritance whose source turn cannot be located in the transcript is *not*
   performed (fail closed) — nothing is "remembered" outside the request.
2. **The authorization boundary is never inherited.** The frame only *proposes* data sources and
   document collections; the caller still checks them against the scope allowed for this request
   (``scope_check``), and an inherited proposal outside that scope is rejected, not silently
   widened or narrowed. Semantic context and permission are separate questions.
3. **Untrusted history can only contribute registry values.** The history is untrusted prompt
   text. Parsing is a closed-vocabulary maximal-munch over entity/metric/time registries, never a
   free-form extraction; the frame is never rendered into SQL, parameters or the model prompt.
   A hostile turn (``DROP TABLE``, 越权指令) can therefore not change this turn's SQL or params.
4. **Inert without history.** ``update_frame(None, question, None)`` inherits nothing and
   ``frame_to_query`` returns the question unchanged, so the pre-multi-turn path is unchanged.
   The engines only receive a frame when it actually resolved something from history.

Vocabulary scope (honest limits)
--------------------------------
The entity/metric registries mirror the seeded demo vocabulary (``db/seed_demo_retail.py``,
``business_metrics``) plus the industrial part-number shape. Out-of-registry wording resolves to
nothing (the frame stays empty) and the pre-existing clarification path stays in charge. This is a
resolver for a closed demo vocabulary, **not** a general entity linker or an NER model.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

FRAME_VERSION = "frame-v1"

#: The seven contract fields of a resolution frame (order is part of the contract).
FRAME_FIELDS = ("entities", "metrics", "time_range", "sources", "document_collections",
                "overrides", "inherited_from_turn")

#: Document-question vocabulary. ``datamind.agent._is_document_question`` delegates here so the
#: frame and the router can never drift apart. Same words, same ``lower()`` comparison as before.
DOCUMENT_TERMS = ("报告", "文档", "手册", "制度", "白皮书", "pdf", "知识库", "质检",
                  "ohr-bench", "退款条件", "七天无理由", "多久到账", "退款时限", "续航",
                  "会员权益", "有哪些权益", "计算公式", "预算", "巡检", "保修")

#: Region / category / customer-level registries (demo_retail).
REGION_TERMS = ("华东", "华北", "华南", "华中")
CATEGORY_TERMS = ("家电", "数码", "家居", "食品")
LEVEL_TERMS = ("VIP", "钻石", "黄金", "白银")
#: Product names as seeded by ``db/seed_demo_retail.py`` (the closed product registry).
PRODUCT_TERMS = ("智能变频空调 1.5匹", "对开门冰箱 450L", "滚筒洗衣机 10kg", "65英寸4K电视",
                 "扫地机器人 X2", "无线降噪耳机 Pro", "真无线耳机 Air", "智能手表 S6",
                 "平板电脑 T11", "便携蓝牙音箱", "机械键盘 87键", "人体工学椅", "乳胶枕",
                 "空气炸锅 5L", "破壁机", "智能门锁", "混合坚果礼盒", "有机绿茶 250g",
                 "冻干咖啡 40条", "全麦面包 1kg")
#: Industrial part numbers (demo_industrial), e.g. ``A102``.
PART_RE = re.compile(r"[A-Z]\d{3}(?![\dA-Z])")

#: ``(metric_id, surfaces)`` for the short aliases of ``business_metrics``. Long phrase aliases
#: ("卖出多少", "扣除退款后的销售额", …) are deliberately left out: they over-match whole questions
#: and the existing clarification path is the safer owner of ambiguous wording.
METRIC_REGISTRY = (
    ("demo_retail.net_sales", ("净销售额", "净收入")),
    ("demo_retail.refund_amount", ("退款金额", "退款额", "退货金额")),
    ("demo_retail.sales_amount", ("销售额", "销售金额", "成交额", "gmv", "业绩", "营收")),
    ("chinook.revenue", ("销售额", "销售收入", "消费金额")),
    ("demo_retail.units_sold", ("销量", "销售数量", "售出件数")),
    ("chinook.tracks_sold", ("销量", "售出曲目数")),
    ("demo_retail.order_count", ("订单数", "订单量", "单量")),
    ("demo_retail.customer_count", ("客户数", "客户总量", "会员数")),
)
#: Human labels used when rendering an inherited metric into a retrieval query (never into SQL).
METRIC_LABELS = {"demo_retail.net_sales": "净销售额", "demo_retail.refund_amount": "退款金额",
                 "demo_retail.sales_amount": "销售额", "chinook.revenue": "销售收入",
                 "demo_retail.units_sold": "销量", "chinook.tracks_sold": "售出曲目数",
                 "demo_retail.order_count": "订单数", "demo_retail.customer_count": "客户数"}

CHINOOK_TERMS = ("chinook", "歌手", "歌曲", "曲目", "专辑", "音乐")
INDUSTRIAL_TERMS = ("零件", "工序", "批次", "测量")
RETAIL_TERMS = ("订单", "客户", "产品", "商品", "退款", "地区", "大区", "门店", "会员")
OHRBENCH_TERMS = ("ohr-bench", "ohrbench")

#: Turn-level markers that make the current question *referential* ("那…呢", "它", "换成…").
DEICTIC_MARKERS = ("那", "呢", "它", "这个", "那个", "这些", "那些", "同样", "换成", "改成",
                   "改为", "上一年", "前一年", "上一", "第一个", "第二个", "第三个",
                   "第1个", "第2个", "第3个", "不对", "错了", "纠正")
#: Relative-year markers resolved against the year of the inherited frame (not against today).
PREV_YEAR_MARKERS = ("上一年", "前一年")
#: Generic entity-type nouns: "第二个产品呢" names no *new* topic, it refers to the previous one.
GENERIC_NOUNS = ("产品", "商品", "客户", "订单", "地区", "大区", "门店", "会员")
#: Discourse words that carry no slot information by themselves.
_DISCOURSE = ("不是", "而是", "是", "请问", "帮我", "查一下", "看看", "怎么样")
_ORDINALS = {"第一个": 1, "第1个": 1, "第二个": 2, "第2个": 2, "第三个": 3, "第3个": 3}

#: Characters that carry no slot information when a turn is a bare clarification supplement.
_FILLERS = " \t\r\n，。？！,?!、:：;；的了吗呢吧啊"
_ORDINAL_RE = re.compile("|".join(_ORDINALS))

_SURFACE_TO_IDS: dict[str, tuple[str, ...]] = {}
METRIC_SURFACES: dict[str, tuple[str, ...]] = {}
for _metric_id, _surfaces in METRIC_REGISTRY:
    METRIC_SURFACES[_metric_id] = tuple(_surfaces)
    for _surface in _surfaces:
        _SURFACE_TO_IDS.setdefault(_surface, ())
        _SURFACE_TO_IDS[_surface] += (_metric_id,)


@dataclass(frozen=True)
class ResolutionFrame:
    """One turn's resolved question: what it is about, as far as the closed registries can tell.

    ``provenance`` maps a field name to the 1-based history index it was taken from (absent = the
    value comes from the current question). ``inherited_from_turn`` is the latest such index, and
    is ``None`` when no field was inherited — the contract value for "nothing was inherited".
    """

    entities: tuple[str, ...] = ()
    metrics: tuple[str, ...] = ()
    time_range: dict | None = None
    sources: tuple[str, ...] = ()
    document_collections: tuple[str, ...] = ()
    overrides: tuple[dict, ...] = ()
    inherited_from_turn: int | None = None
    provenance: dict = field(default_factory=dict)
    frame_version: str = FRAME_VERSION

    def to_dict(self) -> dict:
        """JSON-ready projection; the seven contract fields first, then provenance."""
        return {"entities": list(self.entities), "metrics": list(self.metrics),
                "time_range": dict(self.time_range) if self.time_range else None,
                "sources": list(self.sources),
                "document_collections": list(self.document_collections),
                "overrides": [dict(item) for item in self.overrides],
                "inherited_from_turn": self.inherited_from_turn,
                "provenance": {key: int(value) for key, value in sorted(self.provenance.items())},
                "frame_version": self.frame_version}

    def inherited_fields(self) -> tuple[str, ...]:
        """Fields whose value came from an earlier turn, in contract order."""
        return tuple(name for name in FRAME_FIELDS if name in self.provenance)

    def value(self, name: str):
        return getattr(self, name)


# ---------------------------------------------------------------------------------------------
# lexing helpers
# ---------------------------------------------------------------------------------------------

def _spans(text: str, terms) -> list[tuple[int, int, str]]:
    """Non-overlapping longest-first (maximal munch) occurrences of ``terms`` in ``text``."""
    lowered = text.lower()
    by_length = sorted({term.lower() for term in terms if term}, key=len, reverse=True)
    spans, index = [], 0
    while index < len(lowered):
        for term in by_length:
            if lowered.startswith(term, index):
                spans.append((index, index + len(term), text[index:index + len(term)]))
                index += len(term)
                break
        else:
            index += 1
    return spans


def _ordered_unique(values):
    seen = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen


def is_document_question(question: str) -> bool:
    """Same predicate ``datamind.agent`` has always used for the document route."""
    return any(word in question.lower() for word in DOCUMENT_TERMS)


# ---------------------------------------------------------------------------------------------
# parsing one question into slots
# ---------------------------------------------------------------------------------------------

def _parse_metrics(text: str, sources) -> tuple[str, ...]:
    resolved = []
    for _, _, surface in _spans(text, list(_SURFACE_TO_IDS)):
        candidates = _SURFACE_TO_IDS[surface.lower()]
        pick = next((item for item in candidates if item.split(".")[0] in sources), candidates[0])
        resolved.append(pick)
    return tuple(_ordered_unique(resolved))


def _parse_time(text: str, *, prev_time=None, today: date | None = None) -> dict | None:
    if any(marker in text for marker in PREV_YEAR_MARKERS):
        base = prev_time.get("from") if prev_time else None
        if base:
            year = int(str(base)[:4]) - 1
            return {"kind": "year", "from": f"{year}-01-01", "to": f"{year + 1}-01-01",
                    "label": f"{year}年", "rule": "上一年"}
        return None
    # Explicit years and 去年/今年/上个月/本月/本季度 share the SQL engine's single implementation.
    from .sql_engine import time_periods
    periods = time_periods(text, today=today) if today is not None else time_periods(text)
    if not periods:
        return None
    period = periods[-1]
    label = f"{period['year']}年" if period.get("year") else period["kind"]
    return {"kind": period["kind"], "from": period["from"], "to": period["to"], "label": label}


def _parse(text: str, *, prev_time=None, today: date | None = None) -> dict:
    """The explicit slots of one question (no inheritance)."""
    entity_spans = _spans(text, REGION_TERMS + CATEGORY_TERMS + LEVEL_TERMS + PRODUCT_TERMS)
    entity_spans += [(span.start(), span.end(), span.group()) for span in PART_RE.finditer(text)]
    entity_spans = sorted(entity_spans)
    negated = _negated_surfaces(text, entity_spans)
    entities = tuple(_ordered_unique(surface for _, _, surface in entity_spans
                                    if surface not in negated))
    chinook = bool(_spans(text, CHINOOK_TERMS))
    industrial = bool(_spans(text, INDUSTRIAL_TERMS))
    sources = (("chinook",) if chinook else ("demo_industrial",) if industrial else ())
    metrics = _parse_metrics(text, sources)
    if not sources:
        # Source words only. A metric alias alone ("销量" belongs to both demo_retail and chinook)
        # must not silently pin the retail source, or a Chinook thread could never be inherited.
        if _spans(text, RETAIL_TERMS) or _spans(text, REGION_TERMS + CATEGORY_TERMS):
            sources = ("demo_retail",)
    document = bool(_spans(text, OHRBENCH_TERMS)) or is_document_question(text)
    collections = (("ohrbench_kb",) if _spans(text, OHRBENCH_TERMS) else
                   ("industrial_demo",) if (industrial and document) else
                   ("demo_kb",) if document else ())
    return {"entities": entities, "metrics": metrics,
            "time_range": _parse_time(text, prev_time=prev_time, today=today),
            "sources": sources, "document_collections": collections}


#: A correction negates the surface right after it ("不是华东，是华南").
NEGATION_RE = re.compile(r"(?:不是|不|非|不要)\s*$")


def _negated_surfaces(text: str, spans) -> set[str]:
    """Entity surfaces the user explicitly rejected in this turn.

    Only a negation *immediately* before the surface counts, so 「不是华东，是华南」 keeps 华南 and
    drops 华东, while 「不对，是华东的订单数」 is a plain affirmation of 华东.
    """
    negated = set()
    for start, _, surface in spans:
        prefix = text[:start].rstrip(" \t\r\n，。？！,?!、:：;；")
        if NEGATION_RE.search(prefix):
            negated.add(surface)
    return negated


def _frame_is_empty(frame: "ResolutionFrame | None") -> bool:
    """True when a turn resolved no slot at all (e.g. it was refused)."""
    return frame is None or not any((frame.entities, frame.metrics, frame.time_range,
                                     frame.sources, frame.document_collections))


def _realign_metrics(metrics, sources, text: str) -> tuple[str, ...]:
    """Re-resolve a metric whose source is not the frame's source.

    「销量」 is registered for both ``demo_retail`` and ``chinook``; in a Chinook thread
    「那销量呢」 must bind ``chinook.tracks_sold``, not the retail metric. Only a surface that is
    actually present in the question is re-bound, and only to another registry candidate.
    """
    if not sources:
        return tuple(metrics)
    realigned = []
    for metric in metrics:
        if metric.split(".")[0] in sources:
            realigned.append(metric)
            continue
        alternatives = [other for surface in METRIC_SURFACES.get(metric, ())
                        for other in _SURFACE_TO_IDS.get(surface, ())
                        if other.split(".")[0] in sources and surface in text.lower()]
        realigned.append(alternatives[0] if alternatives else metric)
    return tuple(_ordered_unique(realigned))


def _is_referential(text: str) -> bool:
    return any(marker in text for marker in DEICTIC_MARKERS)


def _strip_slots(text: str) -> str:
    """Remove every registry *slot* surface, time expression and discourse marker from ``text``.

    Topic words (orders, documents, refund conditions, …) are deliberately **not** removed: they
    are exactly what distinguishes "new topic" from "same topic, new slot".
    """
    remainder = text
    for _, _, surface in _spans(text, list(_SURFACE_TO_IDS) + list(REGION_TERMS) + list(CATEGORY_TERMS)
                                + list(LEVEL_TERMS) + list(PRODUCT_TERMS)):
        remainder = remainder.replace(surface, " ", 1)
    for span in PART_RE.finditer(text):
        remainder = remainder.replace(span.group(), " ", 1)
    remainder = re.sub(r"(20\d{2})\s*年", " ", remainder)
    for marker in sorted(DEICTIC_MARKERS + PREV_YEAR_MARKERS + GENERIC_NOUNS + _DISCOURSE,
                         key=len, reverse=True):
        remainder = remainder.replace(marker, " ")
    return remainder.strip(_FILLERS)


def _is_slot_only(text: str) -> bool:
    """True when the whole question is slot tokens (``2025年``, ``华南``, ``2025年华南``).

    A bare slot is how a clarification gets answered ("请指定时间范围" → "2025年"), so it must be
    allowed to inherit the missing slots. Anything with substantive leftover wording is *not*
    slot-only.
    """
    return not _strip_slots(text)


def _is_anaphora_only(text: str) -> bool:
    """True when the question adds no new topic: only references, fillers and generic nouns.

    「那上一年呢」「第二个产品呢」「不是华东，是华南」 are anaphora-only (full inheritance);
    「那退款的呢」 carries new content (退款) and therefore only inherits context (time/source),
    leaving the metric slot empty so the existing clarification asks instead of guessing.
    """
    return bool(_is_referential(text) or _ORDINAL_RE.search(text)) and not _strip_slots(text)


def _ordinal_entity(turns, question: str):
    """Resolve 「第二个产品呢」 from the latest assistant answer that *lists* products.

    Only the closed product registry is searched, so an assistant turn can contribute at most a
    product name — never SQL, a parameter or an instruction. Returns ``(turn_index, product)``.
    """
    match = _ORDINAL_RE.search(question)
    if not match:
        return None
    ordinal = _ORDINALS[match.group()]
    for index in range(len(turns), 0, -1):
        if turns[index - 1]["role"] != "assistant":
            continue
        products = _ordered_unique(surface for _, _, surface
                                   in _spans(turns[index - 1]["content"], PRODUCT_TERMS))
        if len(products) >= ordinal:
            return index, products[ordinal - 1]
    return None


# ---------------------------------------------------------------------------------------------
# previous-frame reconstruction from the transcript (no server session)
# ---------------------------------------------------------------------------------------------

def _validated_history(history):
    from .sql_engine import history_error, normalize_history
    code = history_error(history)
    if code:
        raise ValueError(code)
    return normalize_history(history)


def _replay(turns, *, today=None):
    """Replay the user turns of a transcript; returns ``(last_frame, frames_by_turn)``.

    Replaying (rather than only looking at the last user turn) is what makes transitive
    inheritance work: a slot-only supplement turn ("2025年") already inherited its metric from the
    turn before it, so the next turn can inherit through it.
    """
    frames, frame = {}, None
    for index, turn in enumerate(turns, 1):
        if turn["role"] != "user":
            continue
        frame = update_frame(frame, turn["content"], turns[:index - 1], today=today)
        frames[index] = frame
    return frame, frames


# ---------------------------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------------------------

def update_frame(prev_frame: ResolutionFrame | None, question: str,
                 history=None, *, today: date | None = None) -> ResolutionFrame:
    """Resolve ``question`` into a :class:`ResolutionFrame`.

    ``history`` is the same client-supplied transcript the chat routes accept; it is validated
    with the existing ``normalize_history`` contract (≤20 turns / ≤2000 chars per entry /
    ≤8000 chars total, reject-only) and raises ``ValueError(code)`` on a violation. ``prev_frame``
    is the caller's view of the previous turn (optional); when it is ``None`` the previous frame is
    replayed from the transcript. An inherited value whose source turn cannot be located in the
    transcript is **not** inherited (fail closed).
    """
    turns = _validated_history(history)
    replayed, frames_by_turn = _replay(turns, today=today) if turns else (None, {})
    previous = prev_frame if prev_frame is not None else replayed

    explicit = _parse(question, today=today)
    inherit_allowed = previous is not None and (_is_referential(question) or _is_slot_only(question))
    # Full inheritance only when the turn adds no new topic; a referential turn that *does* bring
    # new wording ("那退款的呢") inherits context (time/source/collection) and leaves the topic
    # slots to the current question — an unanswered metric then reaches the clarification step.
    inheritable = (("entities", "metrics", "time_range", "sources", "document_collections")
                   if _is_slot_only(question) or _is_anaphora_only(question) else
                   ("time_range", "sources", "document_collections"))

    provenance: dict[str, int] = {}
    values = {"entities": explicit["entities"], "metrics": explicit["metrics"],
              "time_range": explicit["time_range"], "sources": explicit["sources"],
              "document_collections": explicit["document_collections"]}
    overrides: list[dict] = []

    def _locate(name, value):
        """Traceability: the turn that *stated* ``value``, else the latest turn carrying it.

        The last branch keeps following the provenance chain (a turn that inherited the value
        points back at the turn that stated it), so the reported index always names a turn whose
        own text carries the value.
        """
        for index in sorted(frames_by_turn, reverse=True):
            candidate = frames_by_turn[index]
            if candidate.value(name) == value and name not in candidate.provenance:
                return index
        for index in sorted(frames_by_turn, reverse=True):
            candidate = frames_by_turn[index]
            if candidate.value(name) == value:
                return candidate.provenance.get(name, index)
        if previous is not None and previous.provenance.get(name) is not None \
                and previous.value(name) == value:
            return previous.provenance[name]
        return None

    def _locate_direct(name, value):
        """The latest user turn whose own frame carried ``value`` (no provenance chain)."""
        for index in sorted(frames_by_turn, reverse=True):
            if frames_by_turn[index].value(name) == value:
                return index
        return None

    # A relative year ("那上一年呢") is resolved against the inherited frame, not against today.
    # It runs before the generic loop so the derived year wins over the plain inherited year.
    if values["time_range"] is None and any(m in question for m in PREV_YEAR_MARKERS) \
            and previous is not None and previous.time_range:
        derived = _parse_time(question, prev_time=previous.time_range, today=today)
        if previous.time_range.get("rule") == "上一年":
            # Chained "上一年": the base year itself was derived in the previous turn, so the
            # traceable source is that turn (its text carries the marker), not the turn that
            # stated the year the chain started from.
            source = _locate_direct("time_range", previous.time_range)
        else:
            source = _locate("time_range", previous.time_range)
        if derived and source is not None:
            values["time_range"] = derived
            provenance["time_range"] = source
            overrides.append({"field": "time_range", "from": previous.time_range, "to": derived,
                              "rule": "上一年"})

    # An ordinal reference ("第二个产品呢") selects an item from an earlier answer. It runs before
    # the generic loop because it *replaces* the entity choice instead of inheriting one.
    if inherit_allowed and not values["entities"] and _ORDINAL_RE.search(question):
        resolved = _ordinal_entity(turns, question)
        if resolved:
            index, product = resolved
            values["entities"] = (product,)
            provenance["entities"] = index
            overrides.append({"field": "entities", "from": None, "to": [product],
                              "rule": "ordinal_ref", "from_turn": index})

    if inherit_allowed:
        full_tier = _is_slot_only(question) or _is_anaphora_only(question)
        for name in inheritable:
            if values[name]:
                continue
            inherited, index = previous.value(name), None
            if inherited:
                index = _locate(name, inherited)
            if index is None and full_tier:
                # The previous turn left this slot empty (e.g. it was refused): an anaphora-only
                # turn may reach further back, but never past a substantive turn — that would drag
                # an unrelated topic across a real turn of the conversation.
                for turn_index in sorted(frames_by_turn, reverse=True):
                    candidate_frame = frames_by_turn[turn_index]
                    earlier = candidate_frame.value(name)
                    if earlier:
                        candidate = _locate(name, earlier)
                        if candidate is not None:
                            inherited, index = earlier, candidate
                        break
                    if not _frame_is_empty(candidate_frame):
                        break
            if not inherited or index is None:
                # Untraceable provenance: refuse to inherit rather than claim a source turn.
                continue
            values[name] = inherited
            provenance[name] = index

    if values["metrics"]:
        values["metrics"] = _realign_metrics(values["metrics"], values["sources"], question)

    # Explicit corrections ("不是华东，是华南") are recorded as user overrides of the previous frame.
    if previous is not None:
        for name in ("entities", "metrics", "time_range", "sources", "document_collections"):
            current, before = values[name], previous.value(name)
            if not current or not before or current == before or name in provenance:
                continue
            overrides.append({"field": name, "from": before, "to": current})

    inherited_from = max(provenance.values()) if provenance else None
    return ResolutionFrame(entities=tuple(values["entities"]), metrics=tuple(values["metrics"]),
                           time_range=values["time_range"], sources=tuple(values["sources"]),
                           document_collections=tuple(values["document_collections"]),
                           overrides=tuple(overrides), inherited_from_turn=inherited_from,
                           provenance=provenance)


def frame_to_query(question: str, frame: ResolutionFrame) -> dict:
    """Project a frame into the query handed to routing / clarification / retrieval.

    ``text`` is ``question`` **byte for byte** when nothing was inherited. When something was
    inherited it appends the inherited slot surfaces (as human labels), which is only ever used as
    a *retrieval* query; the generation prompt keeps the original question. The frame text is never
    used to build SQL, parameters or any executed string.
    """
    hints = []
    if "time_range" in frame.provenance and frame.time_range:
        hints.append(frame.time_range.get("label") or frame.time_range["from"])
    if "entities" in frame.provenance:
        hints.extend(frame.entities)
    if "metrics" in frame.provenance:
        hints.extend(METRIC_LABELS.get(metric, metric) for metric in frame.metrics)
    hints = list(_ordered_unique(hints))
    text = question if not hints else (
        f"{question}（继承自第{frame.inherited_from_turn}轮：{' '.join(hints)}）")
    return {"question": question, "text": text, "entities": list(frame.entities),
            "metrics": list(frame.metrics), "time_range": dict(frame.time_range) if frame.time_range else None,
            "sources": list(frame.sources), "document_collections": list(frame.document_collections),
            "inherited_from_turn": frame.inherited_from_turn, "frame_version": frame.frame_version}


def scope_check(frame: ResolutionFrame, *, allowed_sources=None, allowed_collections=None) -> str | None:
    """Stable error code when an *inherited* scope proposal is outside this request's scope.

    Only inherited proposals are checked: a scope hint read from the current question is a soft
    semantic hint for which the explicit request scope normally wins, while an inherited proposal
    must pass the same per-request allow-list as any other scope. Nothing is widened or narrowed
    silently.
    """
    if frame.sources and allowed_sources is not None and "sources" in frame.provenance:
        if not set(frame.sources) <= set(allowed_sources):
            return "SOURCE_NOT_ALLOWED"
    if frame.document_collections and allowed_collections is not None \
            and "document_collections" in frame.provenance:
        if not set(frame.document_collections) <= set(allowed_collections):
            return "COLLECTION_NOT_ALLOWED"
    return None


def clarification_resolved_by_frame(clarification: str | None, frame: ResolutionFrame | None) -> bool:
    """True only for the clarification an inherited frame legitimately answers.

    The time-range clarification ("请指定销售额的统计时间范围") is answered by an inherited time
    range. The refund amount/count question and the read-only refusal are **never** suppressed:
    guessing a metric binding is worse than asking again, and a write attempt must stay refused.
    """
    if not clarification or frame is None or frame.time_range is None:
        return False
    from .sql_engine import CLARIFY_TIME_RANGE
    return clarification == CLARIFY_TIME_RANGE


def resolve_turn(question: str, history=None, *, prev_frame: ResolutionFrame | None = None,
                 mode: str = "auto", allowed_sources=None, allowed_collections=None,
                 today: date | None = None) -> dict:
    """The whole pre-routing resolution of one turn: frame + scope gate + route + clarification.

    Pure: no database, no model, no writes. ``datamind.agent.DataMindAgent.run`` calls exactly this
    function for history-bearing requests, so the frozen evaluation measures the shipped decision
    path rather than a copy of it.
    """
    frame = update_frame(prev_frame, question, history, today=today)
    result = {"frame": frame.to_dict(), "query": frame_to_query(question, frame),
              "route": None, "clarification": None, "source": None, "collections": None,
              "scope_from_frame": False, "error_code": None, "frame_version": FRAME_VERSION,
              # In-process handle for the caller (the agent); never serialised.
              "_frame": frame}
    error = scope_check(frame, allowed_sources=allowed_sources, allowed_collections=allowed_collections)
    if error:
        return {**result, "route": "reject", "error_code": error}

    from .agent import route_for
    from .sql_engine import clarify, source_for
    result["route"] = route_for(question, mode, frame=frame)

    clarification = clarify(question)
    result["clarification"] = (None if clarification_resolved_by_frame(clarification, frame)
                               else clarification)

    if result["route"] == "sql":
        inherited_source = ("sources" in frame.provenance and len(frame.sources) == 1)
        requested = frame.sources[0] if inherited_source else (
            allowed_sources[0] if allowed_sources and len(allowed_sources) == 1 else None)
        source = source_for(question, requested)
        if allowed_sources is not None and source not in allowed_sources:
            return {**result, "route": "reject", "error_code": "SOURCE_NOT_ALLOWED"}
        result["source"] = source
        result["scope_from_frame"] = inherited_source
    elif result["route"] == "rag":
        inherited_collections = "document_collections" in frame.provenance
        if inherited_collections and allowed_collections is not None \
                and not set(frame.document_collections) <= set(allowed_collections):
            # An inherited collection outside this request's scope: refuse, never substitute one.
            return {**result, "route": "reject", "error_code": "COLLECTION_NOT_ALLOWED"}
        if inherited_collections:
            # An inherited proposal is used as the retrieval scope (it already passed the check).
            result["collections"] = list(frame.document_collections) or None
            result["scope_from_frame"] = True
        else:
            # A collection read from the current question is a soft hint: it may narrow retrieval
            # inside the allowed scope, but it can never widen it.
            proposal = [name for name in frame.document_collections
                        if allowed_collections is None or name in allowed_collections]
            result["collections"] = proposal or (list(allowed_collections) if allowed_collections
                                                 else None)
    return result
