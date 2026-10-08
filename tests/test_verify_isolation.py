# -*- coding: utf-8 -*-
"""
test_verify_isolation.py — 回归测试：审计 F03（端口暴露面）与 F11（验证者污染被测对象）

覆盖:
  F03  deploy/compose.yaml 每个 ports 条目显式绑定 127.0.0.1（解析 YAML，逐条检查；
       并可选地用 `docker compose config` 交叉验证解析后的 host_ip）。
  F11  scripts/verify.py 不再包含硬编码的"2 个数据源 / 26 份文档"期望；
       期望值由 derive_expectations() 动态推导；写入探针受 rollback 保护
       （用假连接断言结束时确实调用了 rollback 且从未 commit）。
  F11  scripts/reset_demo.sh 不再使用 Windows `py` 启动器，改用 venv 解释器。

运行: cd "/Users/caicai/DataMind Agent" && PYTHONPATH=tests .venv/bin/python -m unittest tests.test_verify_isolation
"""
import ast
import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import yaml

REPO = Path(__file__).resolve().parents[1]
COMPOSE = REPO / "deploy" / "compose.yaml"
VERIFY = REPO / "scripts" / "verify.py"
RESET_DEMO = REPO / "scripts" / "reset_demo.sh"

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

LOOPBACK = "127.0.0.1"


def _port_host(entry):
    """从 compose 的 ports 条目中取出发布地址（host）部分，未显式指定时返回 ''。"""
    if isinstance(entry, dict):
        return str(entry.get("host_ip", "") or "")
    text = str(entry)
    # 形如 [host_ip:][published:]target[/protocol]；至少两段才谈得上 host 绑定
    parts = text.split(":")
    if len(parts) < 3:
        return ""
    return parts[0]


class ComposeLoopbackBindingTest(unittest.TestCase):
    """F03: 端口发布必须默认仅回环。"""

    @classmethod
    def setUpClass(cls):
        cls.doc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
        cls.services = cls.doc.get("services", {})
        cls.port_entries = []  # (service, entry)
        for svc, spec in cls.services.items():
            for entry in (spec or {}).get("ports", []) or []:
                cls.port_entries.append((svc, entry))

    def test_compose_declares_ports(self):
        """防止测试空转：确实存在待检查的端口映射。"""
        self.assertTrue(self.port_entries, "compose.yaml 未解析到任何 ports 条目")
        services = {svc for svc, _ in self.port_entries}
        self.assertIn("postgres", services)
        self.assertIn("qdrant", services)

    def test_every_published_port_binds_loopback(self):
        """逐个断言：每个 ports 条目的 host 必须是 127.0.0.1。"""
        offenders = [(svc, entry, _port_host(entry))
                     for svc, entry in self.port_entries
                     if _port_host(entry) != LOOPBACK]
        self.assertEqual(
            offenders, [],
            f"以下端口未显式绑定 {LOOPBACK}（对局域网暴露）：{offenders}",
        )

    def test_published_port_count_matches_expectation(self):
        """postgres 1 个 + qdrant 2 个（REST/gRPC）端口均受绑定约束。"""
        by_svc = {}
        for svc, _ in self.port_entries:
            by_svc[svc] = by_svc.get(svc, 0) + 1
        self.assertEqual(by_svc.get("postgres"), 1)
        self.assertEqual(by_svc.get("qdrant"), 2)

    @unittest.skipUnless(shutil.which("docker"), "docker CLI 不可用")
    def test_docker_compose_config_resolves_to_loopback(self):
        """只读调用 `docker compose config`，确认插值后的 host_ip 仍是 127.0.0.1。"""
        proc = subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE), "config", "--format", "json"],
            cwd=str(REPO), capture_output=True, text=True, timeout=120,
        )
        if proc.returncode != 0:
            self.skipTest(f"docker compose config 不可用: {proc.stderr.strip()[:200]}")
        rendered = json.loads(proc.stdout)
        for svc, spec in rendered.get("services", {}).items():
            for entry in (spec or {}).get("ports", []) or []:
                self.assertEqual(
                    entry.get("host_ip"), LOOPBACK,
                    f"{svc} 解析后 host_ip={entry.get('host_ip')!r}，未绑定 {LOOPBACK}",
                )


class VerifyScriptIsolationTest(unittest.TestCase):
    """F11: verify.py 期望动态推导 + 写入受 rollback 保护。"""

    @classmethod
    def setUpClass(cls):
        cls.source = VERIFY.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source, filename=str(VERIFY))
        cls.funcs = {n.name: n for n in ast.walk(cls.tree)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    def test_no_hardcoded_eq_two_or_twenty_six(self):
        """不得出现 `== 2` / `== 26` 这类数据源/文档计数比较。"""
        bad = []
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Compare):
                continue
            for op, comparator in zip(node.ops, node.comparators):
                if isinstance(op, (ast.Eq, ast.NotEq)) and isinstance(comparator, ast.Constant) \
                        and comparator.value in (2, 26):
                    bad.append((node.lineno, ast.unparse(node)))
        self.assertEqual(bad, [], f"仍存在硬编码计数比较: {bad}")

    def test_no_legacy_magic_numbers_in_source(self):
        """兜底（AST 结构化，不误伤 `== 2. 结构化库 ==` 这类分节标题字符串）：
        比较运算里不得出现字面量 2/26；期望/计数类变量不得被赋值为 2/26。"""
        bad_cmp, bad_assign = [], []
        expect_name = re.compile(r"(n_|num_|count|expect|doc|source|chunk|eval|schema)", re.I)
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Compare):
                for comparator in [node.left, *node.comparators]:
                    if isinstance(comparator, ast.Constant) and comparator.value in (2, 26):
                        bad_cmp.append((node.lineno, ast.unparse(node)))
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                    and node.value.value in (2, 26):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
                if any(expect_name.search(n) for n in names):
                    bad_assign.append((node.lineno, ast.unparse(node)))
        self.assertEqual(bad_cmp, [], f"仍存在硬编码计数比较: {bad_cmp}")
        self.assertEqual(bad_assign, [], f"仍存在硬编码期望赋值: {bad_assign}")

    def test_dynamic_derivation_function_exists(self):
        """存在动态推导函数，且其查询来自库本身（count + 相关表）。"""
        self.assertIn("derive_expectations", self.funcs,
                      "缺少 derive_expectations()，期望值仍是硬编码")
        fn_src = ast.get_source_segment(self.source, self.funcs["derive_expectations"])
        self.assertIn("count(", fn_src.lower())
        for table in ("data_sources", "schema_snapshots", "document_assets",
                      "chunks", "evaluation_cases"):
            self.assertIn(table, fn_src)
        # 期望值必须在运行时由 SQL 查询得出（存在 execute 调用）
        self.assertIn("execute(", fn_src)

    def test_write_probes_are_wrapped_for_rollback(self):
        """写入路径存在回滚保护：有 rollback 调用、禁用隐式提交、且无 autocommit=True。"""
        rollback_calls = [n for n in ast.walk(self.tree)
                          if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                          and n.func.attr == "rollback"]
        self.assertTrue(rollback_calls, "verify.py 中不存在 rollback() 调用")
        # 不得把 autocommit 打开为 True（否则探针写入会立即提交、无法回滚）
        autocommit_true = [
            n.lineno for n in ast.walk(self.tree)
            if isinstance(n, ast.Assign)
            for t in n.targets
            if isinstance(t, ast.Attribute) and t.attr == "autocommit"
            and isinstance(n.value, ast.Constant) and n.value.value is True
        ]
        self.assertEqual(autocommit_true, [], "存在 autocommit=True，写入无法回滚")
        self.assertIn("verify_readonly_isolation", self.funcs)
        self.assertIn("probe_write_blocked", self.funcs)
        # 越权写入探针语句只应出现在可回滚的探针函数内
        probe_fn = ast.get_source_segment(self.source, self.funcs["probe_write_blocked"])
        self.assertIn("SAVEPOINT", probe_fn)
        self.assertIn("ROLLBACK TO SAVEPOINT", probe_fn)
        for danger in ("DELETE FROM orders", "CREATE TABLE hack_x"):
            for name, fn in self.funcs.items():
                seg = ast.get_source_segment(self.source, fn) or ""
                if danger in seg:
                    self.assertEqual(name, "verify_readonly_isolation",
                                     f"{danger} 出现在 {name}()，未经 rollback 包装")

    def test_isolation_probe_rolls_back_on_exit(self):
        """用假连接打桩：结束时调用 rollback、从不 commit、连接被关闭。"""
        import psycopg2
        import scripts.verify as verify_mod

        class FakeCursor:
            def __init__(self):
                self.executed = []

            def execute(self, sql, args=None):
                self.executed.append(sql)
                stripped = sql.strip().upper()
                if stripped.startswith("DELETE") or stripped.startswith("CREATE"):
                    raise psycopg2.errors.InsufficientPrivilege("permission denied")

        class FakeConn:
            def __init__(self):
                self.autocommit = None
                self.rollbacks = 0
                self.closes = 0
                self.commits = 0

            def cursor(self):
                return FakeCursor()

            def rollback(self):
                self.rollbacks += 1

            def commit(self):
                self.commits += 1

            def close(self):
                self.closes += 1

        fake = FakeConn()

        def fake_connect(**kwargs):
            self.assertEqual(kwargs.get("dbname"), "demo_retail")
            return fake

        # 静音探针自带的 [PASS]/[FAIL] 打印，保持测试输出干净
        with mock.patch.object(verify_mod, "check", lambda *a, **kw: None):
            conn = verify_mod.verify_readonly_isolation(connect=fake_connect)
        self.assertIs(conn, fake)
        self.assertFalse(fake.autocommit, "autocommit 必须关闭，否则探针写入会被提交")
        self.assertGreaterEqual(fake.rollbacks, 1, "结束时未调用 rollback，写入可能残留")
        self.assertEqual(fake.commits, 0, "验证者不得提交任何写入")
        self.assertGreaterEqual(fake.closes, 1, "连接未关闭")


class ResetDemoLauncherTest(unittest.TestCase):
    """F11: reset_demo.sh 使用 venv 解释器而非 Windows `py` 启动器。"""

    @classmethod
    def setUpClass(cls):
        cls.source = RESET_DEMO.read_text(encoding="utf-8")
        cls.lines = cls.source.splitlines()

    def test_no_py_launcher(self):
        bad = [f"{i}: {ln}" for i, ln in enumerate(self.lines, 1)
               if re.search(r"(^|\s)py\s+-3", ln) or re.match(r"\s*py\s+\S", ln)]
        self.assertEqual(bad, [], f"仍在使用 py 启动器: {bad}")

    def test_uses_venv_python(self):
        self.assertGreaterEqual(self.source.count(".venv/bin/python"), 1)
        for i, ln in enumerate(self.lines, 1):
            stripped = ln.strip()
            if stripped.endswith(".py") or ".py " in stripped or ".py --" in stripped:
                if stripped.startswith("#") or stripped.startswith("echo "):
                    continue
                self.assertTrue(
                    ".venv/bin/python" in stripped,
                    f"第 {i} 行未使用 venv 解释器: {ln}",
                )

    def test_shell_syntax_valid(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash 不可用")
        proc = subprocess.run([bash, "-n", str(RESET_DEMO)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
