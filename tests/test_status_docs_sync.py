"""PROGRESS.md / README.md must not drift from the run they claim to describe.

Audit 5.6: the authoritative entry points disagreed with each other and with the code
(PROGRESS.md said API 0.37.0 while the app was 0.39.0; README.md still called 959/959
"current"). The numbers are now derived from `reports/latest_regression.json` (written by
`scripts/test_isolated.py`) and from the application itself, so this test fails on drift
instead of relying on someone remembering to edit three files.
"""
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "reports/latest_regression.json"
sys.path.insert(0, str(ROOT))


class StatusDocumentsSyncTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(RECORD.is_file(), "run scripts/test_isolated.py first")
        self.summary = json.loads(RECORD.read_text(encoding="utf-8"))
        self.fraction = f"{self.summary['tests']}/{self.summary['tests']}"
        from datamind.api import app
        self.version = app.version

    def _text(self, name):
        return (ROOT / name).read_text(encoding="utf-8")

    def test_the_recorded_run_is_isolated(self):
        """Only invariants that do not depend on the *current* run's outcome.

        Asserting `failures == 0` here was self-referential: the record read during a run is the
        previous run's, so one red record made every later run red as well, permanently. "Do not
        publish a red run" belongs to `status_sync.py`, which also carries the deliberate
        exception for drift-only failures.
        """
        self.assertTrue(self.summary["metadata_isolated"])
        self.assertFalse(self.summary["source_database_modified"],
                         "a run that wrote to the source database must not be published")

    def test_progress_states_the_application_version(self):
        self.assertRegex(self._text("PROGRESS.md"),
                         r"\| 代码 API \| \*\*" + re.escape(self.version) + r"\*\*")

    def test_progress_states_the_recorded_denominator(self):
        self.assertRegex(self._text("PROGRESS.md"),
                         r"\| 完整隔离回归 \| \*\*" + re.escape(self.fraction) + r"\*\*")
        self.assertIn(f"上表 {self.fraction} 是**当前唯一权威值**", self._text("PROGRESS.md"))

    def test_readme_states_the_same_denominator_and_version(self):
        text = self._text("README.md")
        self.assertRegex(text, r"\| 代码 API \| \*\*" + re.escape(self.version) + r"\*\*")
        self.assertRegex(text, r"\| 完整隔离回归 \| \*\*" + re.escape(self.fraction) + r"\*\*")
        self.assertEqual(re.findall(r"\| 完整隔离回归 \| \*\*([0-9]+/[0-9]+)\*\*", text),
                         [self.fraction], "README 应只有一条与机读记录同步的回归分母")
        commit = self.summary.get("commit")
        if commit:
            self.assertIn(f"| 验证基线 | 提交 `{commit}`", text)

    def test_the_sync_script_reports_no_drift(self):
        """The script is the mechanism; if it disagrees, the documents were edited by hand."""
        result = subprocess.run([sys.executable, str(ROOT / "scripts/status_sync.py"), "--check"],
                                cwd=str(ROOT), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_baseline_commit_is_recorded(self):
        commit = self.summary.get("commit")
        if commit:
            self.assertIn(f"| 验证基线 | 提交 `{commit}`", self._text("PROGRESS.md"))


if __name__ == "__main__":
    unittest.main()
