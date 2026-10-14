"""Wiki 知识图谱 P1 迁移（db/migrations/021_wiki_knowledge.sql）的静态契约测试。

这些测试**不连接数据库**，与 `tests/test_parse_contract.py:23-40`（解析迁移 SQL 文本）
和 `tests/test_security_round2.py:272-317`（静态断言迁移文本 + db/migrate.py 源码）同一模式。
理由：`db/migrate.py:12-15` 在**每次启动**时、**单事务**、以 **datamind_app** 身份重放
`db/migrations/*.sql`，没有版本账、没有 checksum，因此"这份文件本身是否可重放、是否漏掉一张
表的 REVOKE、是否碰了不属于它的表"才是这里必须钉住的东西。

真机执行证据（以 `datamind_app` 连跑两次、逐表 `readonly_user` 权限、以及 39 条约束/触发器
探针）记录在 P1 交付报告里，不在本文件内重复；本文件只保证"文本即契约"。
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / 'db' / 'migrations'
MIGRATION = MIGRATIONS / '021_wiki_knowledge.sql'
DOMAIN_DOC = ROOT / 'docs' / 'WIKI_DOMAIN.md'
API_DOC = ROOT / 'docs' / 'WIKI_API.md'

# 需求文档 §5 的 8 张表，也是 P1 计划里的 8 张表（多一张即越界）。
TABLES = ['wiki_entries', 'wiki_revisions', 'wiki_assertions', 'wiki_evidence_refs',
          'wiki_provenance_links', 'wiki_relations', 'wiki_review_events', 'wiki_build_jobs']

# docs/WIKI_API.md §12.4：由 postgres 持有、迁移**不得**触碰的 18 张表。
POSTGRES_OWNED = ['app_settings', 'business_metrics', 'chunks', 'claims', 'data_sources',
                  'document_assets', 'document_blocks', 'entity_aliases', 'evaluation_cases',
                  'evidence_edges', 'evidence_nodes', 'ingest_jobs', 'runs', 'schema_columns',
                  'schema_relations', 'schema_snapshots', 'schema_tables', 'trace_events']

# docs/WIKI_DOMAIN.md §2 的四类词条、§3.2 的 21 个关系词、§5 的三套状态取值。
ENTRY_TYPES = ['business_entity', 'metric', 'norm', 'conclusion']
RELATION_TYPES = ['entity_match', 'version_scope', 'unit_equivalent', 'time_overlap', 'supports',
                  'conflicts', 'derived_from', 'retrieved_from', 'maps_to', 'contradicts',
                  'verified_by', 'repaired_from', 'resolved_by', 'measures', 'applies_to',
                  'scoped_by', 'threshold_of', 'about', 'justified_by', 'supersedes', 'restates']
UNDIRECTED_TYPES = ['entity_match', 'unit_equivalent', 'time_overlap', 'conflicts', 'contradicts']
PROJECTED_TYPES = ['entity_match', 'version_scope', 'unit_equivalent', 'time_overlap', 'supports',
                   'conflicts', 'derived_from', 'retrieved_from', 'maps_to', 'contradicts',
                   'verified_by', 'repaired_from', 'resolved_by']
DERIVED_TYPES = ['measures', 'applies_to', 'scoped_by', 'threshold_of', 'about', 'justified_by',
                 'supersedes', 'restates']
PUBLICATION_STATES = ['draft', 'in_review', 'published', 'archived']
ASSERTION_STATES = ['unverified', 'verified', 'insufficient', 'conflicted']

#: 这份迁移里所有 jsonb 列中"未设置"语义由 SQL NULL 表达的那些（JSON `null` 字面量必须被拒）。
NULLABLE_JSONB_COLUMNS = ['time_scope', 'applicability', 'record_scope', 'verification_coverage',
                          'verification_accounting', 'basis', 'derivation']


def read(path):
    return path.read_text(encoding='utf-8')


def strip_comment_lines(text):
    """去掉整行 `--` 注释后的 SQL 代码。

    这份迁移的注释一律独占整行（写在语句上方的说明），所以按行剥离即可；
    `COMMENT ON ... IS '...'` 里的字符串不受影响。这样"散文里提到的词"就无法满足
    一条关于语句的断言 —— 例如页眉里的 `CREATE TABLE/INDEX IF NOT EXISTS` 不能算作
    一个真实的幂等语句。
    """
    return '\n'.join(line for line in text.splitlines() if not line.strip().startswith('--'))


def do_blocks(code):
    return re.findall(r'DO \$\$(.*?)\$\$;', code, re.S)


RAW = read(MIGRATION)
CODE = strip_comment_lines(RAW)


class WikiSchemaMigrationTest(unittest.TestCase):
    """迁移文件本身：编号、幂等、REVOKE 覆盖、越界检查、守卫写法。"""

    def setUp(self):
        self.raw = RAW
        self.code = CODE

    # ---------------------------------------------------------------- 编号与幂等

    def test_the_next_migration_number_is_021_wiki_knowledge(self):
        names = sorted(p.name for p in MIGRATIONS.glob('*.sql'))
        self.assertIn('021_wiki_knowledge.sql', names)
        self.assertEqual(names[-1], '021_wiki_knowledge.sql',
                         '021 必须是最后一个迁移编号')
        self.assertEqual(names[-2], '020_chat_sessions.sql')
        self.assertTrue(MIGRATION.is_file())

    def test_every_create_is_if_not_exists_so_the_file_can_be_replayed(self):
        creates = re.findall(r'CREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX)\s', self.code)
        idempotent = re.findall(
            r'CREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX)\s+IF NOT EXISTS\s', self.code)
        self.assertEqual(len(creates), len(idempotent),
                         '每个 CREATE 都必须是 IF NOT EXISTS（无 ledger，每次启动全量重放）')
        self.assertEqual(len(re.findall(r'CREATE TABLE IF NOT EXISTS (\w+)', self.code)), 8,
                         '必须正好 8 张表')
        # CREATE TRIGGER 没有 IF NOT EXISTS：必须先 DROP TRIGGER IF EXISTS 同名触发器。
        triggers = re.findall(r'CREATE TRIGGER (\w+)', self.code)
        self.assertEqual(len(triggers), 4)
        for name in triggers:
            self.assertIn('DROP TRIGGER IF EXISTS %s ON ' % name, self.code)
        # ALTER TABLE ... ADD CONSTRAINT 同样没有 IF NOT EXISTS：必须先在 pg_constraint 里查。
        alters = re.findall(r'ALTER TABLE\s+\w+\s+ADD CONSTRAINT\s+(\w+)', self.code)
        self.assertEqual(len(alters), 2, '只有发布头与基线两条复合外键需要后置添加')
        for name in alters:
            self.assertIn("conname = '%s'" % name, self.code,
                          'ADD CONSTRAINT 必须先查 pg_constraint，否则第二次重放会报重复对象')

    def test_the_migration_contains_no_data_statements(self):
        """只建结构：不写数据、不删数据、不建扩展、不建序列（ID 全是契约格式的字符串）。"""
        for forbidden in ('INSERT INTO', 'DELETE FROM', 'TRUNCATE', 'DROP TABLE',
                          'CREATE SEQUENCE', 'CREATE EXTENSION', 'DROP CONSTRAINT'):
            self.assertNotIn(forbidden, self.code, '%s 不应出现' % forbidden)

    # ---------------------------------------------------------------- 隐私（最重要）

    def test_every_wiki_table_is_revoked_from_public_and_readonly_user(self):
        revokes = re.findall(r'REVOKE ALL ON ([^;]+?) FROM PUBLIC,\s*readonly_user;',
                             self.code, re.S)
        self.assertTrue(revokes, '必须有一张不落的 REVOKE 语句')
        named = set()
        for block in revokes:
            named.update(re.findall(r'(wiki_\w+)', block))
        missing = sorted(set(TABLES) - named)
        self.assertEqual(missing, [], '这些表没被 REVOKE：漏一张即静默泄露给只读角色')
        self.assertEqual(sorted(named), sorted(TABLES), 'REVOKE 只应覆盖这 8 张表')
        # 私有表：本文件不得出现任何 GRANT（019 给公开证据表 GRANT SELECT，这里不允许）。
        self.assertIsNone(re.search(r'\bGRANT\b', self.code), '私有表不得 GRANT')

    def test_the_revoke_is_deliberately_not_ownership_guarded(self):
        """REVOKE 不放在 tableowner 守卫里，是刻意的 fail-closed 选择。

        守卫会让"表已存在但不是本角色所有"的情况静默跳过 REVOKE，而那种情况恰恰意味着
        CREATE 是 no-op、表不是本文件建的（可能已被默认授权放开）。安全语句宁可整批失败。
        """
        spans = [m.span() for m in re.finditer(r'DO \$\$.*?\$\$;', self.code, re.S)]
        position = self.code.index('REVOKE ALL ON')
        self.assertFalse(any(start < position < end for start, end in spans),
                         'REVOKE 必须在守卫之外')
        self.assertIn('aborting the batch is the fail-closed behaviour', self.raw,
                      '这个偏离必须写进注释，否则下一个人会把它当成漏写守卫')

    # ---------------------------------------------------------------- 越界检查

    def test_no_ddl_on_tables_owned_by_postgres(self):
        """绝不动别人的表：runs / trace_events / evidence_* / claims / conflict_records 等 18 张。

        仓库里有真实事故：`must be owner of table evidence_nodes` 导致整批迁移回滚
        （reports/cards/wp2_cross_source.md:62）。
        """
        for table in POSTGRES_OWNED:
            self.assertIn(table, read(API_DOC), '%s 应仍在 WIKI_API.md §12.4 的清单里' % table)
            patterns = (
                r'ALTER TABLE\s+%s\b' % table,
                r'CREATE\s+(?:UNIQUE\s+)?INDEX[^;]*?\sON\s+%s\b' % table,
                r'COMMENT ON\s+(?:TABLE|COLUMN)\s+%s\b' % table,
                r'GRANT\b[^;]*?\sON\s+%s\b' % table,
                r'REFERENCES\s+%s\b' % table,
                r'(?:CREATE|DROP) TRIGGER[^;]*?\sON\s+%s\b' % table,
                r'INSERT INTO\s+%s\b' % table,
                r'UPDATE\s+%s\b' % table,
                r'DELETE FROM\s+%s\b' % table,
                r'TRUNCATE[^;]*?\b%s\b' % table,
            )
            for pattern in patterns:
                self.assertIsNone(re.search(pattern, self.code, re.I | re.S),
                                  '迁移触碰了 %s：%s' % (table, pattern))

    def test_the_two_run_scoped_columns_are_documented_as_having_no_foreign_key(self):
        """`run_id` 故意不建外键：ADD FOREIGN KEY 会锁住 postgres 持有的 runs 表。"""
        self.assertIn('NO foreign key to runs', self.raw)
        self.assertNotIn('REFERENCES runs', self.code)
        self.assertNotIn('REFERENCES trace_events', self.code)

    # ---------------------------------------------------------------- 守卫写法

    def test_index_comment_trigger_and_constraint_statements_are_ownership_guarded(self):
        """沿用 019:103-129：任何建索引/注释/触发器/加约束都必须包在 DO 块里，
        且块的 `tableowner = current_user` 守卫命中的表名与语句目标表名一致。
        """
        blocks = do_blocks(self.code)
        self.assertEqual(len(blocks), 8, '每张表一个守卫块')
        inside = '\n'.join(blocks)

        for statement in ('CREATE INDEX IF NOT EXISTS', 'CREATE UNIQUE INDEX IF NOT EXISTS',
                          'COMMENT ON TABLE', 'COMMENT ON COLUMN', 'CREATE TRIGGER',
                          'DROP TRIGGER IF EXISTS', 'ALTER TABLE'):
            self.assertEqual(self.code.count(statement), inside.count(statement),
                             '%s 有语句落在 tableowner 守卫之外' % statement)

        target_patterns = (
            r'CREATE (?:UNIQUE )?INDEX IF NOT EXISTS \w+\s+ON (\w+)',
            r'COMMENT ON TABLE (\w+)',
            r'COMMENT ON COLUMN (\w+)\.',
            r'CREATE TRIGGER \w+[^;]*?\sON (\w+)',
            r'DROP TRIGGER IF EXISTS \w+ ON (\w+)',
            r'ALTER TABLE (\w+) ADD CONSTRAINT',
        )
        for block in blocks:
            guard = re.search(r"tablename = '(\w+)'", block)
            self.assertIsNotNone(guard, 'DO 块缺少 tablename 守卫')
            self.assertIn('tableowner = current_user', block)
            self.assertIn("schemaname = 'public'", block)
            for pattern in target_patterns:
                for target in re.findall(pattern, block, re.S):
                    self.assertEqual(target, guard.group(1),
                                     '守卫的是 %s，却对 %s 执行了 DDL'
                                     % (guard.group(1), target))

    # ---------------------------------------------------------------- 契约对齐

    def test_the_eight_table_names_match_the_frozen_contract(self):
        created = re.findall(r'CREATE TABLE IF NOT EXISTS (\w+)', self.code)
        self.assertEqual(sorted(created), sorted(TABLES))
        self.assertTrue(all(name.startswith('wiki_') for name in created),
                        '迁移只应新建 wiki_ 前缀的表')
        # WIKI_DOMAIN.md §6 首句明说"只冻结语义，表结构属 P1"，所以表名以需求 §5 的 8 张为准；
        # 文档确实点名的两个名字必须与迁移逐字一致，且这句话在文档改口时必须让本测试失败。
        domain = read(DOMAIN_DOC)
        self.assertIn('本节只冻结**语义**，表结构属 P1', domain)
        for name in ('wiki_evidence_refs', 'wiki_provenance_links'):
            self.assertIn(name, domain)
            self.assertIn(name, created)

    def test_the_closed_vocabularies_match_the_frozen_contract(self):
        domain = read(DOMAIN_DOC)
        match = re.search(r'relation_type\s+VARCHAR\(24\) CHECK \(relation_type IN \((.*?)\)\)',
                          self.code, re.S)
        self.assertIsNotNone(match, '找不到 relation_type 的闭集 CHECK')
        self.assertEqual(sorted(re.findall(r"'([a-z_]+)'", match.group(1))),
                         sorted(RELATION_TYPES), '关系词表必须是 §3.2 的 21 个词')
        for relation_type in RELATION_TYPES:
            self.assertIn(relation_type, domain)

        for entry_type in ENTRY_TYPES:
            self.assertIn(entry_type, self.code)
            self.assertIn(entry_type, domain)

        for column, expected in (('publication_status', PUBLICATION_STATES),
                                 ('assertion_status', ASSERTION_STATES)):
            found = re.findall(r'%s[^\n]*?CHECK \(%s IN \((.*?)\)\)' % (column, column),
                               self.code, re.S)
            self.assertTrue(found, '找不到 %s 的闭集 CHECK' % column)
            for group in found:
                self.assertEqual(sorted(re.findall(r"'([a-z_]+)'", group)), sorted(expected),
                                 '%s 的取值必须与 §5 冻结的一套状态一致' % column)
                for value in expected:
                    self.assertIn(value, domain)
        # 三套状态在三列里各记一套，没有任何"合并总状态"列。
        self.assertNotIn('overall_status', self.code)
        self.assertNotIn('combined_status', self.code)

    def test_the_relation_vocabulary_classes_and_direction_are_encoded(self):
        """§3.2/#1-#13 projected、#14-#21 wiki_derived、5 个无向类型按端点 id 规范化存储。"""
        symmetric = re.search(r'is_symmetric = \(relation_type IN \((.*?)\)\)', self.code, re.S)
        self.assertIsNotNone(symmetric)
        self.assertEqual(sorted(re.findall(r"'([a-z_]+)'", symmetric.group(1))),
                         sorted(UNDIRECTED_TYPES))
        classes = re.search(r"\(origin = 'projected' AND relation_type IN \((.*?)\)\)\s*"
                            r"OR \(origin = 'wiki_derived' AND relation_type IN \((.*?)\)\)",
                            self.code, re.S)
        self.assertIsNotNone(classes)
        self.assertEqual(sorted(re.findall(r"'([a-z_]+)'", classes.group(1))),
                         sorted(PROJECTED_TYPES))
        self.assertEqual(sorted(re.findall(r"'([a-z_]+)'", classes.group(2))),
                         sorted(DERIVED_TYPES))
        self.assertEqual(sorted(PROJECTED_TYPES + DERIVED_TYPES), sorted(RELATION_TYPES))
        # SYMMETRIC 是 PostgreSQL 保留字（BETWEEN SYMMETRIC），列名必须避开。
        self.assertIsNone(re.search(r'^\s*symmetric\s', self.code, re.M))
        self.assertIn('is_symmetric', self.code)
        # 无向关系必须按端点 id 规范化存储，自环必须被拒。
        self.assertIn('is_symmetric = FALSE OR (src_kind, src_id) <= (dst_kind, dst_id)',
                      self.code)
        self.assertIn('CHECK (NOT (src_kind = dst_kind AND src_id = dst_id))', self.code)

    def test_the_publication_head_and_the_baseline_are_same_entry_composite_foreign_keys(self):
        """发布头 CAS 与显式基线的领域语义由复合外键表达（WIKI_DOMAIN.md 6.3 / 6.1）。"""
        self.assertIn('UNIQUE (entry_id, revision_id)', self.code)
        head = re.search(
            r'ALTER TABLE wiki_entries ADD CONSTRAINT wiki_entries_head_revision_fk\s+'
            r'FOREIGN KEY \(entry_id, head_revision_id\)\s+'
            r'REFERENCES wiki_revisions\(entry_id, revision_id\)', self.code)
        self.assertIsNotNone(head, '发布头必须同词条（复合外键）')
        base = re.search(
            r'ALTER TABLE wiki_revisions ADD CONSTRAINT wiki_revisions_base_revision_fk\s+'
            r'FOREIGN KEY \(entry_id, base_revision_id\)\s+'
            r'REFERENCES wiki_revisions\(entry_id, revision_id\)', self.code)
        self.assertIsNotNone(base, '基线必须同词条（复合外键）')
        self.assertIn('base_revision_id IS NULL OR base_revision_id <> revision_id', self.code)
        self.assertIn("publication_status <> 'published' OR head_revision_id IS NOT NULL",
                      self.code)
        self.assertIn("(publication_status = 'archived') = (archived_at IS NOT NULL)", self.code)

    def test_the_three_identity_rules_have_unique_constraints(self):
        """WIKI_DOMAIN.md §4 的三类身份：词条 / 证据 / 主张比较，去重语义必须是唯一约束。"""
        # 4.1 词条身份：space_id | namespace | entry_type | key_part 唯一
        self.assertIn('UNIQUE (space_id, namespace, entry_type, key_part)', self.code)
        self.assertIn("key_part NOT LIKE 'pending:%' OR confirmation = 'pending'", self.code)
        # 4.2 证据身份：一个指纹一行；快照缺失时降级为运行隔离
        self.assertIn('UNIQUE (space_id, evidence_fingerprint)', self.code)
        self.assertIn('run_scoped = (source_sha256 IS NULL)', self.code)
        self.assertIn('run_scoped = (run_scoped_run_id IS NOT NULL)', self.code)
        # 4.3 主张比较身份：比较键入库（裸 hex，接口层加 sha256: 前缀）
        self.assertIn('comparison_key      CHAR(64) NOT NULL', self.code)
        self.assertRegex(self.code, r"comparison_key ~ '\^\[a-f0-9\]\{64\}\$'")
        self.assertIn('wiki_assertions_comparison_idx', self.code)
        # 一次 (引用, 运行) 一行：两次运行引用同一证据 → 一个引用 + 两条运行映射
        self.assertIn('UNIQUE (evidence_ref_id, run_id)', self.code)
        self.assertIn('CHECK (revision_id IS NOT NULL OR assertion_id IS NOT NULL)', self.code)

    def test_build_request_idempotency_is_expressed_in_the_schema(self):
        """WIKI_API.md 5.9：同 owner+request_id 同载荷复用、不同载荷必须拒绝。"""
        self.assertIn('UNIQUE (owner_id, request_id)', self.code)
        self.assertIn('wiki_build_jobs_request_immutable_trg', self.code)
        self.assertIn('wiki_build_jobs_request_immutable()', self.code)
        for column in ('owner_id', 'space_id', 'request_id', 'payload_sha256', 'run_id', 'mode',
                       'selection', 'entry_type_hint', 'target_entry_id', 'created_at'):
            self.assertIn('NEW.%s IS DISTINCT FROM OLD.%s' % (column, column), self.code,
                          '幂等键对应的规范载荷列 %s 必须不可改写' % column)
        self.assertIn('WIKI_IDEMPOTENCY_CONFLICT', self.raw)
        # 失败任务必须带错误码；成功任务必须带回结果版本。
        self.assertIn("status <> 'failed' OR error_code IS NOT NULL", self.code)
        self.assertIn("status <> 'succeeded' OR result_revision_id IS NOT NULL", self.code)
        self.assertRegex(self.code, r"error_code ~ '\^WIKI_\[A-Z0-9_\]\+\$'")

    def test_verified_assertions_must_carry_the_six_items_and_five_accounts(self):
        """§5.2 / §7.2：verified 不是布尔标记，六项与五项记账缺一不可。"""
        for column in ('verified_at', 'verified_by', 'verification_method',
                       'verification_coverage', 'verification_rule_version',
                       'verification_accounting'):
            self.assertIn('%s IS NOT NULL' % column, self.code)
        for key in ('source_integrity', 'entity_binding', 'rule_verification',
                    'semantic_support', 'source_authority'):
            self.assertIn("verification_accounting ? '%s'" % key, self.code)
        # verdict=verified 时 basis 必须带规则版本/方法/覆盖范围
        self.assertIn("basis ? 'rule_version'", self.code)
        self.assertIn("basis ? 'method'", self.code)
        self.assertIn("basis ? 'coverage'", self.code)

    # ---------------------------------------------------------------- 追加式守卫

    def test_append_only_guards_are_declared_before_the_triggers_that_use_them(self):
        functions = re.findall(r'CREATE OR REPLACE FUNCTION (wiki_\w+)\(\)', self.code)
        self.assertEqual(sorted(functions), sorted([
            'wiki_assertions_content_immutable', 'wiki_build_jobs_request_immutable',
            'wiki_review_events_append_only', 'wiki_revisions_append_only']))
        first_trigger = self.code.index('CREATE TRIGGER')
        for name in functions:
            self.assertLess(self.code.index('CREATE OR REPLACE FUNCTION %s()' % name),
                            first_trigger,
                            'CREATE TRIGGER 会立刻解析函数，函数必须先声明（否则整批回滚）')
            self.assertIn('EXECUTE FUNCTION %s()' % name, self.code)
        for state in ('WIKI1', 'WIKI2', 'WIKI3', 'WIKI4'):
            self.assertIn("ERRCODE = '%s'" % state, self.code)
        # 复核记录是唯一连 DELETE 都拒绝的：追加式审计（WIKI_API.md 5.13 rule 1）。
        self.assertIn('BEFORE UPDATE OR DELETE ON wiki_review_events', self.code)
        # 正文与引用不可原地覆盖，但版本自身的流程状态可以前进。
        self.assertIn("NEW.body_markdown IS DISTINCT FROM OLD.body_markdown", self.code)
        self.assertIn("NEW.citations IS DISTINCT FROM OLD.citations", self.code)
        self.assertNotIn('NEW.publication_status IS DISTINCT FROM OLD.publication_status',
                         self.code)

    # ---------------------------------------------------------------- 两条陷阱写进注释

    def test_the_two_conflict_source_paths_are_documented_in_the_schema(self):
        """陷阱①：工业核验写表，跨源核验**不写表**、只落 trace_events.output_summary。"""
        self.assertIn('conflict_records', self.raw)
        self.assertIn('trace_events.output_summary', self.raw)
        self.assertIn('cross_source_result', self.raw)
        self.assertIn("source_path = industrial_table | cross_source_trace", self.raw)
        self.assertIn('industrial_table', self.raw)
        self.assertIn('cross_source_trace', self.raw)
        # 工业侧三态必须保留，selected 为空时不得推测选了哪一侧。
        self.assertIn('unresolved / needs_review / resolved_equivalent', self.raw)
        self.assertIn('selected_fact_ref', self.raw)

    def test_the_jsonb_json_null_trap_is_documented_and_refused_by_check(self):
        """陷阱②：JSON `null` 字面量在 SQL 层非空；有"未设置"语义的 jsonb 列必须写清判空方式。"""
        self.assertIn('selected_fact_ref', self.raw)
        self.assertIn("jsonb_typeof(selected_fact_ref) <> 'null'", self.raw,
                      '必须写明 conflict_records.selected_fact_ref 的正确判空方式')
        self.assertIn('JSON', self.raw)
        for column in NULLABLE_JSONB_COLUMNS:
            self.assertRegex(self.code,
                             r'jsonb_typeof\(\s*%s\s*\)\s*=\s*.object.' % column,
                             '%s 必须拒绝 JSON null（只允许 SQL NULL 或对象）' % column)
        self.assertRegex(self.code,
                         r"provenance_incomplete JSONB NOT NULL DEFAULT "
                         r"'\{\"applies\": false\}'::jsonb")
        self.assertIn("provenance_incomplete ? 'applies'", self.code)

    # ---------------------------------------------------------------- 机制对齐

    def test_the_replay_mechanism_this_file_assumes_still_holds(self):
        """幂等写法与 REVOKE 的必要性都来自 db/migrate.py 的真实行为，必须仍然成立。"""
        applier = read(ROOT / 'db' / 'migrate.py')
        self.assertIn('(ROOT / "db/migrations").glob("*.sql")', applier)
        self.assertIn('pg_advisory_xact_lock(714202610)', applier)
        self.assertIn('cur.execute(path.read_text(encoding="utf-8"))', applier)
        self.assertIn('with psycopg2.connect(dsn) as conn, conn.cursor() as cur', applier)
        self.assertNotIn('commit()', applier, '整个批次在同一事务内，一条失败全体回滚')
        # 没有版本账：所以 CREATE 必须 IF NOT EXISTS。
        blob = '\n'.join(read(path) for path in sorted(MIGRATIONS.glob('*.sql')))
        self.assertNotIn('schema_migrations', blob)

    def test_the_migration_documents_the_deployed_grants_it_defends_against(self):
        """新表默认不授权只读角色，这条依赖必须写在文件里，否则下一个人会删掉 REVOKE。"""
        self.assertIn('ALTER DEFAULT PRIVILEGES', self.raw)
        self.assertIn('readonly_user', self.raw)
        for precedent in ('005:50', '011:36', '012:50', '013:17', '020:78'):
            self.assertIn(precedent, self.raw, '既有 REVOKE 惯例必须逐条可查')


if __name__ == '__main__':
    unittest.main()
