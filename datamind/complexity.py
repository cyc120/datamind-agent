"""B8 文档复杂度评估：9 个可解释维度 → 0–100 分 → 确定性处理策略。

设计纪律（对齐审计 §6 对 B8 的要求）：

1. **可解释**：每个维度都给出 ``raw`` 观测量、``score``（0–1）、``weight``、
   ``contribution`` 与一句话规则说明；总分只是加权和的重新标定，不是黑箱。
2. **不伪造**：观测不到的维度标 ``available=False``（例如原生文本 PDF 没有 OCR 置信度），
   总分在可用维度上重新归一化，并报告 ``coverage``（可用权重占比）。缺数据不按 0 分算，
   也不悄悄当作满分。
3. **真的影响处理路径**：``policy_for()`` 的输出（OCR 开关、块预算、检索候选数、人工复核）
   由 ``scripts/eval_complexity.py`` 真实用于解析调用（策略门），不是只写在报告里的建议。
   ``assess_pdf()`` / ``assess_ohr_record()`` 是给摄取入口调用的纯函数，默认不改变任何既有行为。
4. **确定性**：同一观测必然得到同一分数与同一策略（``POLICY_VERSION`` 固定），无随机、无时间依赖。

维度（权重合计 1.00）：

======================  ====  ============================================
维度                    权重  规则要点
======================  ====  ============================================
pages_and_words         0.10  页数与字数（各 50%，40 页 / 4 万字饱和）
image_area_ratio        0.10  图片面积占比（0.6 饱和）
tables_and_continued    0.12  表格数（12 张饱和）60% + 跨页续表 40%
formula_symbol_density  0.12  公式符号密度（30‰ 饱和）70% + 公式候选数 30%
rotation_skew_ratio     0.08  旋转/非轴对齐页比例（0.5 饱和）
ocr_confidence          0.12  1-平均置信度 60% + 低置信行占比 40%（无 OCR 数据则 N/A）
heading_hierarchy_missing 0.10 1-标题层级数/3（0 层=1.0）
encoding_font_mixing    0.10  字体切换率 50% + 字体种类 30% + 私用区/替换字符 20%
scanned_page_ratio      0.16  无原生文本层页比例（0.5 饱和，权重最高）
======================  ====  ============================================
"""
from __future__ import annotations

import re
from pathlib import Path

RULES_VERSION = "complexity-v1"
POLICY_VERSION = "complexity-policy-v1"

IMAGE_AREA_SATURATION = 0.6
PAGE_SATURATION = 40
CHAR_SATURATION = 40000
TABLE_SATURATION = 12
CONTINUED_TABLE_SATURATION = 3
FORMULA_DENSITY_SATURATION = 30.0  # 每 1000 字
FORMULA_CANDIDATE_SATURATION = 6
ROTATION_SATURATION = 0.5
LOW_CONFIDENCE_THRESHOLD = 0.75
LOW_CONFIDENCE_SATURATION = 0.5
HEADING_LEVELS_EXPECTED = 3
FONT_SWITCH_SATURATION = 0.5
DISTINCT_FONT_SATURATION = 8
INVALID_UNICODE_SATURATION = 0.05
SCANNED_SATURATION = 0.5
#: 无原生文本层页比例达到该值即触发 OCR（与档位触发并列的确定性规则）。
SCANNED_OCR_TRIGGER_RATIO = 0.3
NATIVE_TEXT_MIN_CHARS = 30

#: 公式/符号集合：只统计显式符号，不做"看起来像公式"的猜测。
FORMULA_SYMBOLS = set("∑∫√≈≤≥±×÷∞∂∇^_{}\\$πΔθλμσΩ")
FORMULA_PATTERN = re.compile(r"\\begin\{|\\sum|\\frac|\\int|\\alpha|\\beta|\\times|\$\$")

DIMENSIONS = (
    ("pages_and_words", "页数与字数", 0.10),
    ("image_area_ratio", "图片面积占比", 0.10),
    ("tables_and_continued", "表格数与跨页续表", 0.12),
    ("formula_symbol_density", "公式/符号密度", 0.12),
    ("rotation_skew_ratio", "旋转与倾斜页比例", 0.08),
    ("ocr_confidence", "OCR 置信度分布", 0.12),
    ("heading_hierarchy_missing", "标题层级缺失率", 0.10),
    ("encoding_font_mixing", "编码/字体混杂", 0.10),
    ("scanned_page_ratio", "扫描件比例", 0.16),
)
DIMENSION_LABELS = {key: label for key, label, _ in DIMENSIONS}
DIMENSION_WEIGHTS = {key: weight for key, _, weight in DIMENSIONS}
assert abs(sum(DIMENSION_WEIGHTS.values()) - 1.0) < 1e-9, "维度权重必须归一"

BANDS = ((25.0, "low"), (50.0, "medium"), (75.0, "high"), (100.01, "very_high"))

#: 确定性策略表：档位 → (OCR 开关, 块预算, 检索候选数, 是否要求人工复核)。
POLICY_TABLE = {
    "low": {"ocr_band_enabled": False, "max_blocks": 400, "retrieval_candidates": 5,
            "require_human_review": False},
    "medium": {"ocr_band_enabled": False, "max_blocks": 600, "retrieval_candidates": 8,
               "require_human_review": False},
    "high": {"ocr_band_enabled": True, "max_blocks": 800, "retrieval_candidates": 12,
             "require_human_review": False},
    "very_high": {"ocr_band_enabled": True, "max_blocks": 1000, "retrieval_candidates": 16,
                  "require_human_review": True},
}


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _dimension(key: str, raw, score: float | None, rules: str, *, available: bool = True) -> dict:
    return {"id": key, "label": DIMENSION_LABELS[key], "weight": DIMENSION_WEIGHTS[key],
            "available": bool(available), "raw": raw,
            "score": None if score is None else round(_clamp(score), 4), "rules": rules}


def _pages_and_words(observation: dict) -> dict:
    pages = int(observation.get("page_count") or 0)
    chars = int(observation.get("char_count") or 0)
    score = 0.5 * min(pages / PAGE_SATURATION, 1.0) + 0.5 * min(chars / CHAR_SATURATION, 1.0)
    return _dimension("pages_and_words", {"page_count": pages, "char_count": chars}, score,
                      "页数与字数各占 50%%，%d 页 / %d 字饱和" % (PAGE_SATURATION, CHAR_SATURATION))


def _image_area_ratio(observation: dict) -> dict:
    ratio = observation.get("image_area_ratio")
    if ratio is None:
        return _dimension("image_area_ratio", None, None,
                          "缺少页面图片面积观测", available=False)
    score = min(float(ratio) / IMAGE_AREA_SATURATION, 1.0)
    return _dimension("image_area_ratio", round(float(ratio), 4), score,
                      "图片面积占比 / %.1f 饱和" % IMAGE_AREA_SATURATION)


def _tables_and_continued(observation: dict) -> dict:
    tables = int(observation.get("table_count") or 0)
    continued = int(observation.get("continued_table_count") or 0)
    score = 0.6 * min(tables / TABLE_SATURATION, 1.0) + \
        0.4 * min(continued / CONTINUED_TABLE_SATURATION, 1.0)
    return _dimension("tables_and_continued", {"table_count": tables,
                                               "continued_table_count": continued}, score,
                      "表格数（%d 饱和）60%% + 跨页续表（%d 饱和）40%%"
                      % (TABLE_SATURATION, CONTINUED_TABLE_SATURATION))


def _formula_symbol_density(observation: dict) -> dict:
    chars = max(int(observation.get("char_count") or 0), 1)
    symbols = int(observation.get("formula_chars") or 0)
    candidates = int(observation.get("formula_candidates") or 0)
    density = symbols / chars * 1000.0
    score = 0.7 * min(density / FORMULA_DENSITY_SATURATION, 1.0) + \
        0.3 * min(candidates / FORMULA_CANDIDATE_SATURATION, 1.0)
    return _dimension("formula_symbol_density", {"symbols": symbols, "per_1000_chars": round(density, 3),
                                                 "formula_candidates": candidates}, score,
                      "公式符号密度（%.0f‰ 饱和）70%% + 公式候选（%d 饱和）30%%"
                      % (FORMULA_DENSITY_SATURATION, FORMULA_CANDIDATE_SATURATION))


def _rotation_skew_ratio(observation: dict) -> dict:
    ratio = observation.get("rotation_skew_ratio")
    if ratio is None:
        return _dimension("rotation_skew_ratio", None, None,
                          "无法测量页面旋转/文本方向（可测页数为 0）", available=False)
    score = min(float(ratio) / ROTATION_SATURATION, 1.0)
    return _dimension("rotation_skew_ratio", round(float(ratio), 4), score,
                      "旋转或非轴对齐页比例 / %.1f 饱和" % ROTATION_SATURATION)


def _ocr_confidence(observation: dict) -> dict:
    confidences = observation.get("ocr_confidences")
    if not confidences:
        return _dimension("ocr_confidence", None, None,
                          "无 OCR 逐行置信度（原生文本或未跑 OCR）", available=False)
    values = [float(v) for v in confidences]
    mean = sum(values) / len(values)
    low_share = sum(1 for v in values if v < LOW_CONFIDENCE_THRESHOLD) / len(values)
    score = 0.6 * (1.0 - mean) + 0.4 * min(low_share / LOW_CONFIDENCE_SATURATION, 1.0)
    return _dimension("ocr_confidence", {"lines": len(values), "mean": round(mean, 4),
                                         "low_confidence_share": round(low_share, 4)}, score,
                      "(1-平均置信度) 60%% + 低于 %.2f 的行占比（%.1f 饱和）40%%"
                      % (LOW_CONFIDENCE_THRESHOLD, LOW_CONFIDENCE_SATURATION))


def _heading_hierarchy_missing(observation: dict) -> dict:
    blocks = int(observation.get("text_blocks") or 0)
    if blocks <= 0:
        return _dimension("heading_hierarchy_missing", {"text_blocks": 0}, 1.0,
                          "没有任何文本块 → 标题层级视为完全缺失（=1.0）")
    levels = int(observation.get("heading_level_count") or 0)
    score = 1.0 - min(levels / HEADING_LEVELS_EXPECTED, 1.0)
    return _dimension("heading_hierarchy_missing", {"text_blocks": blocks,
                                                    "heading_level_count": levels}, score,
                      "1 - 标题层级数/%d（0 层记 1.0）" % HEADING_LEVELS_EXPECTED)


def _encoding_font_mixing(observation: dict) -> dict:
    switch = observation.get("font_switch_ratio")
    fonts = observation.get("distinct_font_count")
    invalid = observation.get("invalid_unicode_ratio")
    if switch is None and fonts is None and invalid is None:
        return _dimension("encoding_font_mixing", None, None, "无字体/编码观测", available=False)
    switch = float(switch or 0.0)
    fonts = int(fonts or 0)
    invalid = float(invalid or 0.0)
    score = 0.5 * min(switch / FONT_SWITCH_SATURATION, 1.0) + \
        0.3 * min(max(fonts - 3, 0) / (DISTINCT_FONT_SATURATION - 3), 1.0) + \
        0.2 * min(invalid / INVALID_UNICODE_SATURATION, 1.0)
    return _dimension("encoding_font_mixing", {"font_switch_ratio": round(switch, 4),
                                               "distinct_font_count": fonts,
                                               "invalid_unicode_ratio": round(invalid, 5)}, score,
                      "字体切换率 50%% + 字体种类 30%%（>3 种起算） + 私用区/替换字符 20%%")


def _scanned_page_ratio(observation: dict) -> dict:
    ratio = observation.get("scanned_page_ratio")
    if ratio is None:
        return _dimension("scanned_page_ratio", None, None, "未观测原生文本层", available=False)
    score = min(float(ratio) / SCANNED_SATURATION, 1.0)
    return _dimension("scanned_page_ratio", round(float(ratio), 4), score,
                      "无原生文本层页比例 / %.1f 饱和（权重最高）" % SCANNED_SATURATION)


def band_for(total: float) -> str:
    """分数 → 档位（唯一实现，供 score() 与测试/策略共用）。"""
    return next(label for limit, label in BANDS if total < limit)


def score(observation: dict) -> dict:
    """把观测折算成 0–100 分与逐维贡献；缺数据的维度在可用权重上重新归一化。"""
    dimensions = [_pages_and_words(observation), _image_area_ratio(observation),
                  _tables_and_continued(observation), _formula_symbol_density(observation),
                  _rotation_skew_ratio(observation), _ocr_confidence(observation),
                  _heading_hierarchy_missing(observation), _encoding_font_mixing(observation),
                  _scanned_page_ratio(observation)]
    available_weight = sum(d["weight"] for d in dimensions if d["available"])
    missing = [d["id"] for d in dimensions if not d["available"]]
    total = 0.0
    for dimension in dimensions:
        if not dimension["available"]:
            dimension["contribution"] = None
            dimension["contribution_normalized"] = None
            continue
        raw_contribution = dimension["score"] * dimension["weight"] * 100.0
        dimension["contribution"] = round(raw_contribution, 4)
        dimension["contribution_normalized"] = (
            round(raw_contribution / available_weight, 4) if available_weight else None)
        total += dimension["contribution_normalized"] or 0.0
    total = round(total, 2)
    band = band_for(total)
    return {"rules_version": RULES_VERSION, "complexity_score": total, "band": band,
            "coverage": round(available_weight, 4), "renormalized": bool(missing),
            "available_weight": round(available_weight, 4), "missing_dimensions": missing,
            "dimensions": dimensions,
            "interpretation": _interpretation(dimensions, missing)}


def _interpretation(dimensions: list[dict], missing: list[str]) -> list[str]:
    top = sorted((d for d in dimensions if d["available"]), key=lambda d: -(d["contribution"] or 0))[:3]
    notes = ["主要贡献：%s（%.1f 分）" % (d["label"], d["contribution"] or 0) for d in top]
    if missing:
        notes.append("以下维度缺少观测，已按可用权重重新归一化：%s" % "、".join(
            DIMENSION_LABELS[key] for key in missing))
    return notes


def policy_for(score_result: dict, observation: dict | None = None) -> dict:
    """确定性策略映射：分数档位 → OCR 开关 / 块预算 / 检索候选数 / 人工复核。

    两条规则，都可复算：

    1. 档位决定预算：``max_blocks`` / ``retrieval_candidates`` 只看分数档位；
       ``very_high`` 档要求人工复核。
    2. OCR 开关：档位为 high/very_high **或** 无原生文本层页比例 ≥ 0.3 时打开，
       但前提是确实存在无文本层页面（``scanned_page_ratio > 0``）——一份复杂却完全数字化的
       PDF 不该被强制跑 OCR。两个触发条件都写进 ``reasons``，避免"分数→策略"变成不可查的猜测。
    """
    observation = observation or {}
    band = score_result.get("band") or "low"
    base = POLICY_TABLE.get(band, POLICY_TABLE["low"])
    scanned = observation.get("scanned_page_ratio") or 0.0
    band_trigger = bool(base["ocr_band_enabled"])
    scanned_trigger = scanned >= SCANNED_OCR_TRIGGER_RATIO
    ocr_enabled = (band_trigger or scanned_trigger) and scanned > 0
    reasons = ["复杂度 %.2f 落在 %s 档（%s）" % (score_result.get("complexity_score", 0.0), band,
                                               POLICY_VERSION),
               "档位 OCR 触发=%s（档位阈值 %s），扫描页比例=%.4f（触发阈值 %.2f）→ ocr_enabled=%s"
               % (band_trigger, "high/very_high", scanned, SCANNED_OCR_TRIGGER_RATIO, ocr_enabled)]
    if not ocr_enabled and scanned > 0:
        reasons.append("存在扫描页但比例低于触发阈值：按预算优先，人工复核标记仍可由上游质量门决定")
    return {"policy_version": POLICY_VERSION, "band": band,
            "complexity_score": score_result.get("complexity_score"),
            "ocr_enabled": ocr_enabled, "max_blocks": base["max_blocks"],
            "retrieval_candidates": base["retrieval_candidates"],
            "require_human_review": band == "very_high",
            "ocr_band_trigger": band_trigger, "ocr_scanned_trigger": scanned_trigger,
            "reasons": reasons}


# --------------------------------------------------------------------------- 观测器
def _invalid_unicode_ratio(text: str) -> float:
    if not text:
        return 0.0
    bad = sum(1 for ch in text if ch == "\ufffd" or 0xE000 <= ord(ch) <= 0xF8FF)
    return bad / len(text)


def observe_pdf(path: str | Path, *, ocr_confidences: list[float] | None = None,
                formula_candidates: int | None = None) -> dict:
    """只读观测一份 PDF（不跑 OCR，不改任何数据）。

    ``ocr_confidences`` / ``formula_candidates`` 可由解析阶段回填：解析前先算分时它们是未知的
    （对应维度记 N/A），解析后可以用真实观测重算一次，用于"评分 → 策略 → 实际成本"的对照。
    """
    import pymupdf
    from ingestion.ingest import detect_heading

    path = Path(path)
    observation: dict = {"source": "pdf", "path": str(path)}
    with pymupdf.open(path) as doc:
        page_count = len(doc)
        char_count = 0
        image_area = 0.0
        page_area = 0.0
        table_count = 0
        pages_with_tables = []
        formula_chars = 0
        scanned_pages = 0
        rotated_pages = 0
        measurable_rotation = 0
        heading_levels = set()
        text_blocks = 0
        spans = 0
        font_switches = 0
        fonts = set()
        all_text = []
        for index in range(page_count):
            page = doc[index]
            text = page.get_text()
            native_chars = len(text.strip())
            all_text.append(text)
            char_count += native_chars
            if native_chars < NATIVE_TEXT_MIN_CHARS:
                scanned_pages += 1
            formula_chars += sum(1 for ch in text if ch in FORMULA_SYMBOLS) + \
                len(FORMULA_PATTERN.findall(text)) * 4
            rect = page.rect
            page_area += float(rect.width) * float(rect.height)
            for image in page.get_images(full=True):
                try:
                    for image_rect in page.get_image_rects(image[0]):
                        image_area += float(image_rect.width) * float(image_rect.height)
                except Exception:
                    continue
            try:
                tables = page.find_tables()
                found = len(tables.tables) if tables else 0
            except Exception:
                found = 0
            table_count += found
            if found:
                pages_with_tables.append(index)
            # 旋转/倾斜：优先看文字行方向；没有文字层时退回页面 rotation 元数据。
            page_measurable = False
            dict_text = page.get_text("dict")
            for block in dict_text.get("blocks", []):
                if block.get("type") != 0:
                    continue
                text_blocks += 1
                for line in block.get("lines", []):
                    direction = line.get("dir") or (1.0, 0.0)
                    if abs(abs(direction[0]) - 1.0) > 1e-3 or abs(direction[1]) > 1e-3:
                        rotated_pages += 1
                        page_measurable = True
                        break
                    page_measurable = True
                    for span in line.get("spans", []):
                        spans += 1
                        font = span.get("font") or ""
                        if font:
                            if fonts and font not in fonts:
                                font_switches += 1
                            fonts.add(font)
                        body = span.get("size") or 0
                        if body and len((span.get("text") or "").strip()) < 40:
                            heading_levels.add(detect_heading(body))
            if not page_measurable:
                if int(getattr(page, "rotation", 0) or 0) % 360 != 0:
                    rotated_pages += 1
                    page_measurable = True
            if page_measurable:
                measurable_rotation += 1
        continued = sum(1 for left, right in zip(pages_with_tables, pages_with_tables[1:])
                        if right == left + 1)
        observation.update({
            "page_count": page_count,
            "char_count": char_count,
            "image_area_ratio": (image_area / page_area) if page_area else None,
            "table_count": table_count,
            "continued_table_count": continued,
            "formula_chars": formula_chars,
            "formula_candidates": formula_candidates,
            "rotation_skew_ratio": (rotated_pages / measurable_rotation) if measurable_rotation else None,
            "ocr_confidences": ocr_confidences,
            "text_blocks": text_blocks,
            "heading_level_count": len({level for level in heading_levels if level}),
            "font_switch_ratio": (font_switches / spans) if spans else (0.0 if not fonts else None),
            "distinct_font_count": len(fonts),
            "invalid_unicode_ratio": _invalid_unicode_ratio("".join(all_text)),
            "scanned_page_ratio": (scanned_pages / page_count) if page_count else 0.0,
        })
    return observation


def observe_ohr_record(record: dict) -> dict:
    """观测一条 OHR-Bench 页面记录（只有 gt_text / noise_text，没有几何与置信度）。

    与 ``observe_pdf`` 的差别如实体现在 ``available=False`` 的维度上：没有页面尺寸就没有图片面积，
    没有逐行置信度就没有 OCR 分布，这些维度会被排除并在总分里报告 coverage。
    """
    text = record.get("gt_text") or ""
    page_like_lines = [line for line in text.splitlines() if line.strip()]
    tables = len(re.findall(r"\\begin\{tabular\}|\\begin\{table\}", text))
    formula_chars = sum(1 for ch in text if ch in FORMULA_SYMBOLS) + \
        len(FORMULA_PATTERN.findall(text)) * 4
    heading_like = sum(1 for line in page_like_lines
                       if line.strip() and (line.strip().isupper() or re.match(r"^\d+(\.\d+)*[\s、.]", line.strip())))
    return {
        "source": "ohr_json", "sample_id": record.get("sample_id"),
        "page_count": 1, "char_count": len(text),
        "image_area_ratio": None, "table_count": tables, "continued_table_count": 0,
        "formula_chars": formula_chars, "formula_candidates": None,
        "rotation_skew_ratio": None, "ocr_confidences": None,
        "text_blocks": len(page_like_lines), "heading_level_count": min(heading_like, 3),
        "font_switch_ratio": None, "distinct_font_count": None,
        "invalid_unicode_ratio": _invalid_unicode_ratio(text),
        "scanned_page_ratio": None,
    }


def assess_pdf(path: str | Path, **kwargs) -> dict:
    """摄取入口可调用的纯函数：观测 + 评分 + 策略。不写库、不改块、默认不改变既有解析行为。"""
    observation = observe_pdf(path, **kwargs)
    result = score(observation)
    return {"observation": observation, "complexity": result, "policy": policy_for(result, observation)}


def assess_ohr_record(record: dict) -> dict:
    observation = observe_ohr_record(record)
    result = score(observation)
    return {"observation": observation, "complexity": result, "policy": policy_for(result, observation)}
