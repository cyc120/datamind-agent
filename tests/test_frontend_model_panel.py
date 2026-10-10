"""模型接入面板「保存」的可见性与必填校验，钉住两个会静默漂移的接缝。

本轮修复的两个缺陷都表现为**用户看不到任何反馈**，所以这里的断言只针对可结构化的部分：
提示元素与按钮的相对位置、保存路径上的必填校验存在、以及缺失字段会被聚焦定位。
本仓库没有 JavaScript 测试运行器，本文件既不启动 DOM 也不声称验证了浏览器运行时行为。

1. `#modelNotice` 必须出现在 `.actions` 按钮行**之前且紧邻**——它原先在整张编辑表单顶部，
   而「保存」按钮在几十行之下，点保存后提示落在视野之外，看起来就是"没有反应"。
2. `saveProfile` 在发出请求前必须校验 `name` 与 `model`。模型名留空时服务端会静默回落到
   另一个模型，失败只表现为 `SQL_POLICY_BLOCKED`，是本次事故的根因，不能再无声通过。
"""
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / 'web' / 'index.html'
APP_JS = ROOT / 'web' / 'app.js'
STYLE_CSS = ROOT / 'web' / 'style.css'


def function_body(source, name):
    """Return the body of `function name(...)` (or its async form) by brace matching."""
    match = re.search(r'(?:async\s+)?function\s+' + re.escape(name) + r'\s*\(', source)
    if not match:
        return ''
    start = source.index('{', match.end() - 1)
    depth = 0
    for position in range(start, len(source)):
        character = source[position]
        if character == '{':
            depth += 1
        elif character == '}':
            depth -= 1
            if depth == 0:
                return source[start:position + 1]
    return source[start:]


class ModelPanelNoticeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text(encoding='utf-8')
        cls.js = APP_JS.read_text(encoding='utf-8')

    def test_notice_sits_with_the_save_buttons(self):
        save = self.html.index('id="saveProfile"')
        # 别的面板也各有一处 .actions 按钮行，必须锚定包含保存按钮的那一处。
        actions = self.html.rindex('<div class="actions"', 0, save)
        notice = self.html.index('id="modelNotice"')
        self.assertLess(notice, actions, '提示必须出现在保存按钮行之前')
        # 紧邻：提示元素整体结束后到按钮行之间不应再隔着任何内容。
        notice_end = self.html.index('</div>', notice) + len('</div>')
        self.assertEqual(self.html[notice_end:actions].strip(), '',
                         '提示与按钮行之间不应再隔着其它区块')

    def test_the_editor_form_still_owns_the_fields_the_validation_names(self):
        for field in ('profileName', 'modelName'):
            self.assertIn(f'id="{field}"', self.html, f'{field} 必须仍然存在')

    def test_save_validates_name_and_model_before_any_request(self):
        body = function_body(self.js, 'saveProfile')
        self.assertTrue(body, 'saveProfile must exist')
        self.assertIn("requireField(payload.name, 'profileName'", body)
        self.assertIn("requireField(payload.model, 'modelName'", body)
        # 两处校验都必须排在首个 apiFetch 之前，否则请求已经发出。
        first_request = body.index('apiFetch')
        for guard in ("requireField(payload.name", "requireField(payload.model"):
            self.assertLess(body.index(guard), first_request,
                            f'{guard} 必须在发请求之前')

    def test_require_field_focuses_and_scrolls_to_the_missing_input(self):
        body = function_body(self.js, 'requireField')
        self.assertTrue(body, 'requireField must exist')
        self.assertIn('notice(', body)
        self.assertIn('.focus()', body)
        self.assertIn('scrollIntoView', body)
        self.assertIn('field-missing', body)

    def test_the_missing_field_highlight_has_a_style(self):
        self.assertIn('.field-missing', STYLE_CSS.read_text(encoding='utf-8'))

    @unittest.skipUnless(shutil.which('node'), 'node 未安装')
    def test_app_js_passes_node_check(self):
        completed = subprocess.run([shutil.which('node'), '--check', str(APP_JS)],
                                   capture_output=True, text=True, timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == '__main__':
    unittest.main()
