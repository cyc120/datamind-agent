"""The dependency lock must exist, pin versions, and match what is actually installed.

The repository previously had no lock file at all, so "it works here" could not be
reproduced from the checked-in metadata. These checks keep the lock from silently rotting:
a drift between the lock and the interpreter that runs the suite is a real defect, not a
cosmetic one.
"""
from importlib import metadata
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / 'requirements.lock'
PIN = re.compile(r'^([A-Za-z0-9._-]+)==([^\s;]+)$')
CRITICAL = ('fastapi', 'uvicorn', 'psycopg2-binary', 'sqlglot', 'httpx',
            'qdrant-client', 'pymupdf')


def locked_versions():
    pairs = {}
    for line in LOCK.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = PIN.match(line)
        if match:
            pairs[match.group(1).lower().replace('_', '-')] = match.group(2)
        else:
            pairs[line.lower()] = None  # recorded so a malformed line is reported
    return pairs


class DependencyLockTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(LOCK.is_file(), 'requirements.lock 缺失')
        self.locked = locked_versions()

    def test_every_line_is_a_name_equals_version_pin(self):
        malformed = [name for name, version in self.locked.items() if version is None]
        self.assertEqual(malformed, [], f'锁文件存在未固定的行：{malformed}')

    def test_the_lock_is_not_empty(self):
        self.assertGreater(len(self.locked), 20)

    def test_the_runtime_dependencies_are_pinned(self):
        missing = [name for name in CRITICAL if name not in self.locked]
        self.assertEqual(missing, [], f'锁文件缺少关键运行时依赖：{missing}')

    def test_locked_versions_match_the_installed_environment(self):
        drift = []
        for name, version in self.locked.items():
            try:
                installed = metadata.version(name)
            except metadata.PackageNotFoundError:
                continue  # an optional extra that this interpreter does not have
            if installed != version:
                drift.append(f'{name}: 锁 {version} / 实际 {installed}')
        self.assertEqual(drift, [], '锁文件与当前环境不一致：' + '; '.join(drift))

    def test_the_drift_check_is_not_vacuous(self):
        """Guard the guard: if almost nothing in the lock were installed, the drift test
        above would pass while checking nothing at all."""
        installed = [name for name in self.locked if _has(name)]
        self.assertGreaterEqual(len(installed), 20,
                               f'锁文件里的包仅 {len(installed)} 个装在当前环境，漂移检查等于空转')

def _has(name):
    try:
        metadata.version(name)
    except metadata.PackageNotFoundError:
        return False
    return True


if __name__ == '__main__':
    unittest.main()
