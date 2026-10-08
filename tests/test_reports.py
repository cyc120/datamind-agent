"""Every checked-in report must be valid JSON.

A corrupt report is invisible to the rest of the suite (nothing else reads them), so it
can be committed unnoticed and mislead a reviewer. This guards that.
"""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / 'reports'


class ReportIntegrityTests(unittest.TestCase):
    def test_every_report_is_valid_json(self):
        paths = sorted(REPORTS.glob('*.json'))
        self.assertTrue(paths, '没有找到任何报告')
        broken = []
        for path in paths:
            try:
                payload = json.loads(path.read_text(encoding='utf-8'))
            except (ValueError, UnicodeDecodeError) as error:
                broken.append(f'{path.name}: {error}')
                continue
            # Historical reports use both object and array shapes; only emptiness is
            # rejected. Existing reports are never rewritten to satisfy a new rule.
            if payload in ({}, []) or payload is None:
                broken.append(f'{path.name}: 顶层不能为空')
        self.assertEqual(broken, [], '报告 JSON 无效：' + '; '.join(broken))

    def test_reports_do_not_use_python_literals(self):
        # `True`/`False`/`None` are valid Python but invalid JSON; catch them explicitly
        # so the failure names the file instead of only a position.
        offenders = []
        for path in sorted(REPORTS.glob('*.json')):
            text = path.read_text(encoding='utf-8')
            for literal in (': True', ': False', ': None'):
                if literal in text:
                    offenders.append(f'{path.name}{literal}')
        self.assertEqual(offenders, [])


if __name__ == '__main__':
    unittest.main()
