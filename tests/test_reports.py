"""Every checked-in report must be valid JSON.

A corrupt report is invisible to the rest of the suite (nothing else reads them), so it
can be committed unnoticed and mislead a reviewer. This guards that.
"""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / 'reports'
PYTHON_LITERALS = (': True', ': False', ': None')


class ReportIntegrityTests(unittest.TestCase):
    def test_every_report_is_valid_json(self):
        paths = sorted(REPORTS.glob('*.json'))
        self.assertTrue(paths, '没有找到任何报告')
        broken = []
        for path in paths:
            text = path.read_text(encoding='utf-8', errors='replace')
            try:
                payload = json.loads(text)
            except (ValueError, UnicodeDecodeError) as error:
                # Name the likely cause only for a file that really failed to parse, so the
                # same characters inside a string value never raise a false alarm.
                hint = next((literal for literal in PYTHON_LITERALS if literal in text), None)
                broken.append(f'{path.name}: {error}' + (f'（疑似 Python 字面量 {hint}）' if hint else ''))
                continue
            # Historical reports use both object and array shapes; only emptiness is
            # rejected. Existing reports are never rewritten to satisfy a new rule.
            if payload in ({}, []) or payload is None:
                broken.append(f'{path.name}: 顶层不能为空')
        self.assertEqual(broken, [], '报告 JSON 无效：' + '; '.join(broken))


if __name__ == '__main__':
    unittest.main()
