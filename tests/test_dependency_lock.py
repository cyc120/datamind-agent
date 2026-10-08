"""The dependency lock must exist, pin versions, and match what is actually installed.

The repository previously had no lock file at all, so "it works here" could not be
reproduced from the checked-in metadata. These checks keep the lock from silently rotting:
a drift between the lock and the interpreter that runs the suite is a real defect, not a
cosmetic one.

The lock covers the *runtime* dependencies (core service plus the lazily imported
format/feature packages). Authoring/sampling tools that only regenerate the demo or
industrial material are declared in requirements.txt but deliberately not pinned here:
they are not installed in this interpreter and inventing a version for them would be a
fabricated fact.
"""
from importlib import metadata
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / 'requirements.lock'
REQUIREMENTS = ROOT / 'requirements.txt'
PIN = re.compile(r'^([A-Za-z0-9._-]+)==([^\s;]+)$')
CRITICAL = ('fastapi', 'uvicorn', 'psycopg2-binary', 'sqlglot', 'httpx',
            'qdrant-client', 'pymupdf')
# Imported by service code paths for a specific format/feature (lazily, and skipped
# gracefully when absent). They are still runtime dependencies: the lock must pin them.
CAPABILITY = ('pydantic', 'starlette', 'fastembed', 'numpy', 'pillow', 'opencv-python',
              'onnxruntime', 'rapidocr', 'ezdxf', 'python-docx', 'python-pptx', 'openpyxl')
# Authoring/sampling only. The first tuple is checked to be absent from the lock; the
# second lists the only files allowed to import them (offline material generation).
AUTHORING_ONLY = ('reportlab', 'matplotlib', 'img2pdf', 'pyarrow')
AUTHORING_IMPORTERS = {
    'ingestion/generate_demo_docs.py',
    'ingestion/sample_ohrbench.py',
    'scripts/build_industrial_docs.py',
    'scripts/build_industrial_forms.py',
    'scripts/build_industrial_forms_v2.py',
}


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

    def test_the_capability_dependencies_are_pinned(self):
        """Format/feature packages are lazily imported but still shipped capabilities;
        if one is missing from the lock, a fresh install cannot run that capability."""
        missing = [name for name in CAPABILITY if name not in self.locked]
        self.assertEqual(missing, [], f'锁文件缺少能力相关的运行时依赖：{missing}')

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

    def test_authoring_only_packages_are_declared_but_not_pinned_as_runtime(self):
        declared = REQUIREMENTS.read_text(encoding='utf-8').lower()
        lock_text = LOCK.read_text(encoding='utf-8').lower()
        for name in AUTHORING_ONLY:
            self.assertIn(name, declared, f'{name} 未在 requirements.txt 声明')
            self.assertNotIn(name, self.locked,
                             f'{name} 只是材料生成/取样工具，不应作为运行时依赖固定版本')
            self.assertIn(name, lock_text, f'锁文件未说明 {name} 属于可选/未安装')
        self.assertIn('未安装（可选）', lock_text)

    def test_authoring_only_packages_are_not_imported_by_runtime_modules(self):
        pattern = re.compile(r'^\s*(?:import|from)\s+(' + '|'.join(AUTHORING_ONLY) + r')\b',
                             re.MULTILINE)
        offenders = []
        for package in ('datamind', 'ingestion'):
            for path in sorted((ROOT / package).glob('*.py')):
                relative = path.relative_to(ROOT).as_posix()
                if relative in AUTHORING_IMPORTERS:
                    continue
                text = path.read_text(encoding='utf-8', errors='replace')
                for match in pattern.finditer(text):
                    offenders.append(f'{relative}: {match.group(1)}')
        self.assertEqual(offenders, [], '材料生成/取样包不应被运行时模块导入：'
                         + '; '.join(offenders))


def _has(name):
    try:
        metadata.version(name)
    except metadata.PackageNotFoundError:
        return False
    return True


if __name__ == '__main__':
    unittest.main()
