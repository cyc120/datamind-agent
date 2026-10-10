"""交付卫生回归：.gitignore 的输出与知识库文件覆盖。

知识库 PDF 原先只被根目录的 ``/*.pdf`` 挡住，``data/knowledge_base/`` 下被别的
工具写入的 PDF 不会被忽略，曾导致 ``field-test-*.pdf`` 被误摄取。现在改为
「整体忽略 + 按 ``manifest.json`` 逐条放行」的白名单式规则。
"""
from pathlib import Path
import json
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
GITIGNORE = ROOT / '.gitignore'
MANIFEST = ROOT / 'data' / 'knowledge_base' / 'manifest.json'

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


class DeliverableHygieneTests(unittest.TestCase):
    def setUp(self):
        self.gitignore = GITIGNORE.read_text(encoding='utf-8')

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


if __name__ == '__main__':
    unittest.main()
