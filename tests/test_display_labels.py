"""枚举措辞：后端的中文映射必须与前端 ``NodeDetail.vue`` 说同一句话。

WHY 单独一份测试：词条标题（``wiki_entries.title``）与主张句子（``claims.text``）是同一行数据的
两种呈现，前端 ``stargraph/NodeDetail.vue`` 早就把 ``out_of_range`` 译成「超出区间」、
``unresolved`` 译成「两侧仍不一致（未消解）」。后端如果自己另立一套说法，用户在同屏就会看到
两套中文描述同一件事。本文件逐字比对两端，并在前端改词时立刻失败。

三条被钉住的纪律（与 ``datamind/display_labels.py`` 的模块注释一一对应）：

1. 已登记的取值 → 前端的同一句话（逐字，不是"意思相近"）；
2. 未登记的取值 → **原样返回**，绝不兜底成「未知」；
3. ``unresolved`` 的两种含义不许合并（来源比较 vs 裁决状态）。

不需要数据库，也不需要 psycopg2：``display_labels`` 是纯函数模块，系统 python 可跑。
"""
from pathlib import Path
import re
import unittest

from datamind import display_labels

ROOT = Path(__file__).resolve().parents[1]
NODE_DETAIL = ROOT / 'frontend' / 'src' / 'components' / 'stargraph' / 'NodeDetail.vue'

BLOCK_RE = re.compile(r"const\s+(COMPLIANCE|AGREEMENT|RESOLUTION_STATUS)_TEXT\s*:\s*"
                      r"Record<string,\s*string>\s*=\s*\{(.*?)\}", re.S)
PAIR_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*'([^']*)'")

BACKEND_BLOCKS = {
    'COMPLIANCE': display_labels.COMPLIANCE_ZH,
    'AGREEMENT': display_labels.AGREEMENT_ZH,
    'RESOLUTION_STATUS': display_labels.RESOLUTION_STATUS_ZH,
}


def frontend_blocks():
    """``{COMPLIANCE: {code: 中文}}`` —— 从真实 .vue 源码里读，不复制一份到测试里。"""
    source = NODE_DETAIL.read_text(encoding='utf-8')
    blocks = {}
    for name, body in BLOCK_RE.findall(source):
        blocks[name] = dict(PAIR_RE.findall(body))
    return blocks


class FrontendWordingParityTest(unittest.TestCase):
    """后端说法 ⊆ 前端说法：前端有的每一条后端都必须一字不差地有。"""

    def test_every_node_detail_wording_is_reused_verbatim(self):
        blocks = frontend_blocks()
        self.assertEqual(set(blocks), set(BACKEND_BLOCKS), 'NodeDetail.vue 的三张表缺了一张')
        for name, pairs in blocks.items():
            for code, chinese in pairs.items():
                self.assertIn(code, BACKEND_BLOCKS[name],
                              '%s 的 %s 在前端有中文说法，后端没有' % (name, code))
                self.assertEqual(
                    BACKEND_BLOCKS[name][code], chinese,
                    '%s 的 %s 两端说法不一致：前端「%s」后端「%s」'
                    % (name, code, chinese, BACKEND_BLOCKS[name][code]))

    def test_the_parity_check_is_not_vacuous(self):
        blocks = frontend_blocks()
        total = 0
        for name, pairs in blocks.items():
            self.assertGreaterEqual(len(pairs), 2, '%s 只解析出 %d 条，正则多半失效了'
                                    % (name, len(pairs)))
            total += len(pairs)
        self.assertGreaterEqual(total, 8, '三张表合计应至少 8 条（实际 %d）' % total)
        # 每一条都必须真的译成了中文，否则"逐字比对"比对的是空字符串也会通过。
        for name, pairs in blocks.items():
            for code, chinese in pairs.items():
                self.assertTrue(any('\u4e00' <= ch <= '\u9fff' for ch in chinese),
                                '%s 的 %s 没有中文，比对是空转' % (name, code))


class UnknownValueTest(unittest.TestCase):
    """纪律 2：未收录的取值原样保留 —— 不猜、不兜底成「未知」。"""

    def test_an_unlisted_value_is_returned_verbatim(self):
        for value in ('brand_new_verdict', 'Out_Of_Range', 'outofrange', 'out_of_range_v2'):
            self.assertEqual(display_labels.compliance_label(value), value)
            self.assertEqual(display_labels.agreement_label(value), value)
            self.assertEqual(display_labels.resolution_label(value), value)

    def test_the_fallback_is_not_a_wildcard_translation(self):
        unknown = display_labels.compliance_label('needs_second_look')
        self.assertEqual(unknown, 'needs_second_look')
        self.assertNotIn('未知', unknown)
        self.assertNotIn('待', unknown)

    def test_none_is_an_empty_string_not_the_word_none(self):
        self.assertEqual(display_labels.compliance_label(None), '')


class TwoMeaningsOfUnresolvedTest(unittest.TestCase):
    """纪律 3：同一个码在两个字段里不是同一句话，合并会改掉其中一个字段的意思。"""

    def test_agreement_and_resolution_keep_their_own_wording(self):
        self.assertEqual(display_labels.agreement_label('unresolved'), '两侧仍不一致（未消解）')
        self.assertEqual(display_labels.resolution_label('unresolved'), '未消解')
        self.assertNotEqual(display_labels.agreement_label('unresolved'),
                            display_labels.resolution_label('unresolved'))


class IndustrialClaimSentenceTest(unittest.TestCase):
    """``claims.text`` 的中文句子（``agent.py::_record_industrial_evidence`` 写入）。"""

    def test_both_enumeration_slots_are_chinese(self):
        text = display_labels.industrial_claim_text(
            {'measurement_id': 'M001-1', 'compliance': 'out_of_range',
             'agreement': 'unresolved'})
        self.assertEqual(text, '测量 M001-1：工艺范围核验 超出区间；来源比较 两侧仍不一致（未消解）。')
        for code in ('out_of_range', 'unresolved'):
            self.assertNotIn(code, text)

    def test_the_sentence_shape_is_not_changed(self):
        """句式与旧实现逐字一致：只有两个枚举槽换成中文（不是重写整句话）。"""
        text = display_labels.industrial_claim_text(
            {'measurement_id': 'M004-1', 'compliance': 'within_range',
             'agreement': 'resolved_equivalent'})
        self.assertTrue(text.startswith('测量 M004-1：工艺范围核验 '))
        self.assertIn('；来源比较 ', text)
        self.assertTrue(text.endswith('。'))

    def test_an_unlisted_verdict_stays_english_in_the_sentence(self):
        text = display_labels.industrial_claim_text(
            {'measurement_id': 'M001-1', 'compliance': 'brand_new_verdict',
             'agreement': 'unresolved'})
        self.assertIn('brand_new_verdict', text)
        self.assertNotIn('未知', text)


class TitleLocalizerTest(unittest.TestCase):
    """已写进库的旧主张文本：只翻译**标题**里的枚举词，其余字符一个不动。"""

    def test_registered_tokens_are_translated_and_ids_are_untouched(self):
        text = display_labels.localize_enum_tokens(
            '测量 M001-1:工艺范围核验 out_of_range;来源比较 unresolved。')
        self.assertEqual(text, '测量 M001-1:工艺范围核验 超出区间;来源比较 两侧仍不一致（未消解）。')
        self.assertIn('M001-1', text)

    def test_unknown_tokens_and_chinese_text_are_left_alone(self):
        text = display_labels.localize_enum_tokens('零件 A102 的 brand_new_verdict 结论。')
        self.assertEqual(text, '零件 A102 的 brand_new_verdict 结论。')

    def test_the_localizer_is_idempotent(self):
        once = display_labels.localize_enum_tokens('compliance=within_range')
        self.assertEqual(display_labels.localize_enum_tokens(once), once)


if __name__ == '__main__':
    unittest.main()
