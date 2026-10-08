"""交付卫生回归：前端残留、静态资源缓存版本与 .gitignore 覆盖。

这三处缺陷都不会让任何功能测试变红，却会直接漏进交付包：

1. 本地 4000 网关（``gateway/``）已从仓库移除，前端若仍把它当作可点选的预设，
   用户会拿到一个指向已删除服务的地址；
2. ``web/app.js`` 一改，``web/index.html`` 的 ``?v=`` 必须同步提升，否则回访
   浏览器继续使用缓存中的旧脚本（本仓库已连续出现两次）；
3. 知识库 PDF 原先只被根目录的 ``/*.pdf`` 挡住，``data/knowledge_base/`` 下被别的
   工具写入的 PDF 不会被忽略，曾导致 ``field-test-*.pdf`` 被误摄取。现在改为
   「整体忽略 + 按 ``manifest.json`` 逐条放行」的白名单式规则。
"""
from pathlib import Path
import json
import re
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
GITIGNORE = ROOT / '.gitignore'
APP_JS = ROOT / 'web' / 'app.js'
INDEX_HTML = ROOT / 'web' / 'index.html'
MANIFEST = ROOT / 'data' / 'knowledge_base' / 'manifest.json'

# 提升前的历史值：只作为「必须已经提升」的下界，不锁定具体版本号。
PREVIOUS_ASSET_VERSION = (0, 37, 0)
KB_PDF_IGNORE_RULE = '/data/knowledge_base/*.pdf'


def git_binary():
    return shutil.which('git')


def git_check_ignore(relpath):
    """规则是否忽略 relpath。

    使用 ``--no-index`` 让 git 只评估 .gitignore 规则本身：默认模式会因文件已被
    跟踪而直接回答「未忽略」，那样即使白名单条目全部丢失也测不出来。
    """
    completed = subprocess.run(
        [git_binary(), 'check-ignore', '--no-index', '--quiet', '--', relpath],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60)
    if completed.returncode not in (0, 1):
        raise AssertionError(completed.stderr or completed.stdout or 'git check-ignore 失败')
    return completed.returncode == 0


def manifest_pdf_names():
    data = json.loads(MANIFEST.read_text(encoding='utf-8'))
    return [document['file'] for document in data['documents']]


class FrontendHygieneTests(unittest.TestCase):
    def setUp(self):
        self.gitignore = GITIGNORE.read_text(encoding='utf-8')

    # ---------- F11：不再内置已删除的本地网关 ----------

    def test_app_js_has_no_preset_for_the_removed_local_gateway(self):
        source = APP_JS.read_text(encoding='utf-8')
        self.assertNotIn('localhost:4000', source)
        self.assertNotIn('127.0.0.1:4000', source)
        # 仍要保留「经自建网关填写其地址」的通用提示，而不是悄悄删掉入口。
        self.assertIn('自建网关', source)

    def test_the_default_base_url_placeholder_matches_the_backend_default(self):
        # 只读地对照 datamind/settings_store.py 的 DEFAULT_BASE_URL。
        settings = (ROOT / 'datamind' / 'settings_store.py').read_text(encoding='utf-8')
        match = re.search(r'DEFAULT_BASE_URL\s*=\s*"([^"]+)"', settings)
        self.assertIsNotNone(match, 'settings_store.py 未定义 DEFAULT_BASE_URL')
        backend_default = match.group(1)
        html = INDEX_HTML.read_text(encoding='utf-8')
        self.assertIn('placeholder="%s"' % backend_default, html)
        # 表单默认值不能被网关预设改写：DeepSeek 官方预设仍是默认地址。
        source = APP_JS.read_text(encoding='utf-8')
        self.assertIn("base: '%s'" % backend_default, source)

    # ---------- 静态资源缓存版本 ----------

    def test_asset_versions_are_consistent_and_advanced(self):
        html = INDEX_HTML.read_text(encoding='utf-8')
        versions = re.findall(r'/assets/[A-Za-z0-9_.-]+\?v=([0-9][0-9A-Za-z.\-]*)', html)
        self.assertEqual(len(versions), 3, 'index.html 应有三处 assets ?v=（style/app/multifile）')
        self.assertEqual(len(set(versions)), 1, '三处 ?v= 必须一致：%r' % versions)
        current = tuple(int(part) for part in versions[0].split('.'))
        self.assertGreater(current, PREVIOUS_ASSET_VERSION,
                           '?v= 未提升，回访浏览器会继续使用旧脚本')

    # ---------- .gitignore 覆盖 ----------

    def test_gitignore_declares_the_runtime_outputs(self):
        for rule in ('/output/', '*.log', '/reports/project_audit_*'):
            self.assertIn(rule, self.gitignore, rule)

    def test_knowledge_base_pdfs_use_a_manifest_driven_whitelist(self):
        self.assertIn(KB_PDF_IGNORE_RULE, self.gitignore)
        names = manifest_pdf_names()
        self.assertEqual(len(names), 12, 'manifest.json 声明的演示 PDF 应为 12 份')
        # 忽略规则必须真的覆盖知识库目录，否则放行条目毫无意义。
        self.assertTrue(git_check_ignore('data/knowledge_base/__probe__.pdf'),
                        '知识库目录未被忽略规则覆盖，白名单放行形同虚设')
        for name in names:
            self.assertFalse(git_check_ignore('data/knowledge_base/%s' % name),
                             'manifest 声明的 %s 被误忽略' % name)

    def test_an_unlisted_knowledge_base_pdf_is_ignored(self):
        self.assertTrue(git_check_ignore('data/knowledge_base/zzz-drop.pdf'),
                        '未被 manifest 声明的 PDF 必须被忽略，否则会再次被误摄取')

    @unittest.skipUnless(git_binary(), 'git 未安装')
    def test_gitignore_does_not_hide_tracked_deliverables(self):
        completed = subprocess.run([git_binary(), 'ls-files', 'output/', 'data/knowledge_base/'],
                                   cwd=str(ROOT), capture_output=True, text=True, timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        tracked = completed.stdout.splitlines()
        self.assertTrue(any(path.startswith('output/pdf/') for path in tracked),
                        'output/ 下的已跟踪交付物不应被忽略规则影响')
        self.assertIn('data/knowledge_base/manifest.json', tracked)

    @unittest.skipUnless(shutil.which('node'), 'node 未安装')
    def test_app_js_is_syntactically_valid(self):
        completed = subprocess.run([shutil.which('node'), '--check', str(APP_JS)],
                                   capture_output=True, text=True, timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == '__main__':
    unittest.main()
