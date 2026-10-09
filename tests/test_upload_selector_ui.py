"""R05 前端回归：「已审批 / 已索引 / 可问答」不再被混成同一状态。

本仓库没有 JavaScript 测试运行器，本轮也没有真实浏览器，所以这里只做两类断言，并明确区分：

1. **结构/契约断言**（`UploadSelectorContractTests`）：只钉住 `web/multifile.js` 读取了新冻结字段
   （`searchable` / `index_state` / `index_note`）、问答文件选择器按 `searchable===true` 过滤、
   三态中文语义存在、复核后 `no_chunks` 不得显示成功。它们不启动真实 DOM 运行时。
2. **打桩行为验证**（`UploadSelectorHarnessTests`）：用 Node + 假 DOM + 打桩 `apiFetch`，
   喂入混合数据（一条 `searchable:true`、一条 `searchable:false, index_state:"no_chunks"`），
   断言可选项只含前者、后者以「暂不可问答」文案出现，并断言复核 `no_chunks` 时的提示文案。
   这不验证真实浏览器 DOM 运行时，也不与真后端联调（后端按冻结规范并行实现）。

反向验证（证明断言不是空转）：`test_the_selector_filter_assertion_is_not_vacuous` 与
`test_the_no_chunks_approval_assertion_is_not_vacuous` 会复制一份改动过的 `multifile.js`
（去掉过滤 / 改成成功文案）再跑同一个打桩 harness，并断言它**必须失败**。若哪天断言被写空，
这两个测试会先变红。

冻结接口（后端子代理按此实现，前端不得自行发明字段）：

* ``GET /v1/uploads`` 与 ``GET /v1/uploads/{upload_id}`` 每项新增
  ``searchable``(bool) / ``index_state``(indexed|no_chunks|not_approved) / ``index_note``(str|None)。
* ``GET /v1/documents`` 每项新增 ``searchable``(bool)。
* ``POST /v1/uploads/{id}/approve`` 的 ``indexed`` 仅在真正可检索时为 true，为 false 时给
  ``index_state`` / ``index_note``。
"""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MULTIFILE_JS = ROOT / 'web' / 'multifile.js'
INDEX_HTML = ROOT / 'web' / 'index.html'

# 本次改动前 index.html 的三处 ?v=；只作为「必须已经提升」的下界，不锁定具体版本号。
PREVIOUS_ASSET_VERSION = (0, 38, 0)

# 三态中文语义必须逐态对应，不得用 review_state=approved 推断可问答。
INDEX_STATE_LABELS = (
    ('indexed', '已建立索引，可用于问答'),
    ('no_chunks', '已解析，尚未建立问答索引（暂不可问答）'),
    ('not_approved', '待复核'),
)
# 后端冻结的 index_note 机器码 → 中文；前端不得自行发明新码，未收录码必须有兜底文案。
INDEX_NOTE_LABELS = (
    ('UPLOAD_NOT_APPROVED', '待复核'),
    ('UPLOAD_HAS_NO_CHUNKS', '已复核，但尚未建立问答索引，暂不可问答'),
    ('UPLOAD_HAS_NO_CONTRACT_BLOCKS', '解析未产出可入库的块'),
    ('UPLOAD_STATUS_NOT_INDEXED', '暂不可问答（该文档状态不是 indexed）'),
)
BLOCKED_NOTE = '已解析，尚未建立问答索引（暂不可问答）'
APPROVE_NO_CHUNKS_NOTICE = '已复核，但尚未建立问答索引，暂不可用于问答'
# 选择器里"可勾选"的唯一判据。
SEARCHABLE_GUARD = 'return !!(doc && doc.searchable === true);'
# 反向验证用的变异锚点：把判据改成"全部放行"（等价于删掉过滤）。
SEARCHABLE_MUTATION = 'return true; /* MUTATION: filter removed */'


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


def table_body(source, name):
    """Return the literal body of `var NAME = {...};`."""
    match = re.search(r'var\s+' + re.escape(name) + r'\s*=\s*\{(.*?)\};', source, re.S)
    return match.group(1) if match else ''


class UploadSelectorContractTests(unittest.TestCase):
    """结构/契约断言：字段、过滤、三态中文语义、复核诚实提示、缓存版本。"""

    def setUp(self):
        self.source = MULTIFILE_JS.read_text(encoding='utf-8')
        self.html = INDEX_HTML.read_text(encoding='utf-8')

    # ---------- 新冻结字段被真正读取 ----------

    def test_the_ui_reads_the_frozen_searchable_and_index_fields(self):
        for field in ('searchable', 'index_state', 'index_note'):
            self.assertIn(field, self.source, field)
        apply_fields = function_body(self.source, 'applyUploadFields')
        self.assertTrue(apply_fields, 'applyUploadFields must exist')
        for field in ('searchable', 'index_state', 'index_note'):
            self.assertIn("'" + field + "'", apply_fields, field)

    def test_searchable_is_the_only_gate_for_question_documents(self):
        guard = function_body(self.source, 'questionDocumentSearchable')
        self.assertTrue(guard, 'questionDocumentSearchable must exist')
        self.assertRegex(guard, r'doc\.searchable\s*===\s*true')
        # 缺失 searchable 一律按不可问答处理，不得默认放行。
        self.assertNotIn('doc.searchable !== false', guard)
        self.assertNotIn('!doc.searchable', guard)

    # ---------- 选择器：只把 searchable===true 列为可选 ----------

    def test_the_selector_renders_only_searchable_documents_as_checkboxes(self):
        body = function_body(self.source, 'renderQuestionDocuments')
        self.assertTrue(body, 'renderQuestionDocuments must exist')
        # 过滤必须经过同一个判据，且可勾选集合来自过滤结果而不是全量 docs。
        self.assertIn('docs.filter(questionDocumentSearchable)', body)
        self.assertIn('searchable.forEach', body)
        self.assertRegex(body, r'searchable\.forEach\(function\s*\(doc\)\s*\{\s*appendQuestionCheckbox')
        # 不可问答的必须另行、可见地列出。
        self.assertIn('appendQuestionBlocked', body)
        self.assertIn('blocked.forEach', body)
        # 把 checkbox 直接挂到全量 docs 的老写法不得残留。
        self.assertNotIn('MULTIFILE.docs.forEach', body)
        self.assertNotIn("createElement('input')", body)

    def test_the_loader_feeds_the_partitioning_renderer_not_a_raw_loop(self):
        body = function_body(self.source, 'loadQuestionDocuments')
        self.assertTrue(body, 'loadQuestionDocuments must exist')
        self.assertIn("apiFetch('/v1/documents')", body)
        self.assertIn('renderQuestionDocuments(MULTIFILE.docs)', body)
        # 渲染层之外不得再自己造 checkbox。
        self.assertNotIn("createElement('input')", body)
        self.assertNotIn('append(', body)
        # 载荷缺失不能被读成"没有可问答文件"。
        self.assertIn('Array.isArray', body)
        self.assertIn('预期之外的载荷', body)

    def test_non_searchable_documents_are_listed_as_not_answerable(self):
        blocked = function_body(self.source, 'appendQuestionBlocked')
        self.assertTrue(blocked, 'appendQuestionBlocked must exist')
        self.assertIn('QUESTION_BLOCKED_NOTE', blocked)
        self.assertIn(BLOCKED_NOTE, self.source)
        render = function_body(self.source, 'renderQuestionDocuments')
        self.assertIn('QUESTION_BLOCKED_HEADING', render)
        self.assertIn('暂不可问答', self.source)

    # ---------- index_state 三态中文语义 ----------

    def test_the_three_index_states_have_exact_chinese_semantics(self):
        table = table_body(self.source, 'INDEX_STATE_LABELS')
        self.assertTrue(table, 'INDEX_STATE_LABELS must exist')
        for state, label in INDEX_STATE_LABELS:
            self.assertRegex(table, re.escape(state) + r"\s*:\s*'" + re.escape(label) + r"'", state)

    def test_every_frozen_index_note_code_has_a_chinese_mapping(self):
        table = table_body(self.source, 'INDEX_NOTE_LABELS')
        self.assertTrue(table, 'INDEX_NOTE_LABELS must exist')
        for code, label in INDEX_NOTE_LABELS:
            self.assertRegex(table, re.escape(code) + r"\s*:\s*'" + re.escape(label) + r"'", code)
            # 机器码不得直接当成用户文案。
            self.assertNotRegex(table, re.escape(code) + r"\s*:\s*'" + re.escape(code) + r"'")
        note = function_body(self.source, 'uploadIndexNoteText')
        self.assertTrue(note, 'uploadIndexNoteText must exist')
        # null（= 可问答）不附提示。
        self.assertRegex(note, r'if\s*\(!\s*note\s*\)\s*\{\s*return\s+null\s*;\s*\}')
        # 未收录码的兜底文案必须存在，不得静默吞掉。
        self.assertIn('索引说明：', note)

    def test_the_index_text_never_claims_answerability_without_searchable(self):
        text = function_body(self.source, 'uploadIndexText')
        self.assertTrue(text, 'uploadIndexText must exist')
        self.assertIn('uploadIndexStateLabel', text)
        self.assertIn('uploadSearchable', text)
        self.assertIn('searchable=', text)
        # 未知状态必须显式说明，不能显示成可问答。
        self.assertIn('index_state', text)
        # vector_index 仍是 deferred：不得被读成"已建向量索引"。
        self.assertIn('vector_index', text)
        self.assertIn('未建立向量索引', text)
        # 上传结果卡片与详情都要显示它。
        self.assertIn('uploadIndexText(result)', function_body(self.source, 'uploadResultCard'))
        self.assertIn('uploadIndexText(upload)', function_body(self.source, 'renderUploadDetail'))

    # ---------- 复核按钮：approved 不等于可问答 ----------

    def test_approving_without_chunks_warns_instead_of_claiming_success(self):
        body = function_body(self.source, 'approveUpload')
        self.assertTrue(body, 'approveUpload must exist')
        self.assertIn('indexed', body)
        self.assertIn('index_state', body)
        self.assertIn(APPROVE_NO_CHUNKS_NOTICE, body)
        # 不许把 no_chunks 复核后写成"复核已通过…可检索"：分支必须存在且带 index_state 判断。
        self.assertRegex(body, r"indexed\s*===\s*false")
        # 详情卡片同样区分可问答与暂不可问答。
        detail = function_body(self.source, 'renderUploadDetail')
        self.assertIn('uploadSearchable(upload)', detail)
        self.assertIn(APPROVE_NO_CHUNKS_NOTICE, detail)

    def test_the_selection_never_sends_a_non_searchable_document(self):
        body = function_body(self.source, 'applyDocumentSelection')
        self.assertTrue(body, 'applyDocumentSelection must exist')
        self.assertIn('questionDocumentSearchable', body)
        self.assertIn('暂不可问答', body)

    def test_changes_still_go_through_the_csrf_aware_fetch(self):
        self.assertIsNone(re.search(r'(?<!api)fetch\(', self.source),
                          'multifile.js 不得绕过 apiFetch 直接 fetch')
        self.assertNotIn('"X-CSRF-Token"', self.source)
        self.assertNotIn("'X-CSRF-Token'", self.source)
        self.assertIn('apiFetch(', function_body(self.source, 'approveUpload'))
        self.assertIn('apiFetch(', function_body(self.source, 'loadQuestionDocuments'))

    # ---------- 缓存版本 ----------

    def test_asset_versions_are_bumped_for_the_edited_assets(self):
        versions = re.findall(r'/assets/[A-Za-z0-9_.-]+\?v=([0-9][0-9A-Za-z.\-]*)', self.html)
        self.assertEqual(len(versions), 3, 'index.html 应有三处 assets ?v=')
        self.assertEqual(len(set(versions)), 1, '三处 ?v= 必须一致：%r' % versions)
        current = tuple(int(part) for part in versions[0].split('.'))
        self.assertGreater(current, PREVIOUS_ASSET_VERSION,
                           'web/* 已改，?v= 必须提升')
        # 静态提示也要说明"只有 searchable=true 可勾选"。
        self.assertIn('searchable=true', self.html)


class NodeSyntaxTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'node 未安装')
    def test_both_scripts_pass_node_check(self):
        node = shutil.which('node')
        for path in (ROOT / 'web' / 'app.js', MULTIFILE_JS):
            completed = subprocess.run([node, '--check', str(path)],
                                       capture_output=True, text=True, timeout=60)
            self.assertEqual(completed.returncode, 0, path.name + ': ' + completed.stderr)


# 打桩行为验证：假 DOM + 打桩 apiFetch，喂混合文档，跑选择器与复核分支。
# 断言写在 JS 里（失败即非零退出），Python 只负责运行与报告。
SELECTOR_HARNESS_JS = r'''
'use strict';
/* Runtime mock harness for web/multifile.js's question-document selector (R05).
 * Fake DOM + fake apiFetch. Proves: only searchable===true documents become selectable,
 * non-searchable ones stay visible as 暂不可问答, and approving a no_chunks upload warns
 * instead of claiming success. This is not a real browser DOM nor a real backend run. */
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const source = fs.readFileSync(process.argv[2], 'utf8');

function makeNode(tag) {
  const node = {
    tagName: tag, id: '', children: [], textContent: '', value: '', disabled: false,
    className: '', dataset: {}, style: {}, listeners: {}, files: [],
    type: '', checked: false,
    append: function () { for (let i = 0; i < arguments.length; i++) node.children.push(arguments[i]); },
    appendChild: function (child) { node.children.push(child); return child; },
    replaceChildren: function () { node.children.length = 0; node.textContent = ''; },
    addEventListener: function (type, handler) { node.listeners[type] = handler; },
    querySelectorAll: function () { return []; },
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    setAttribute: function () {}, removeAttribute: function () {}, focus: function () {},
    insertBefore: function (child) { node.children.push(child); return child; }
  };
  return node;
}
const elements = {};
function element(id) {
  if (!elements[id]) { const node = makeNode('div'); node.id = id; elements[id] = node; }
  return elements[id];
}
/* applyDocumentSelection 只读 checked 集合；测试可替换这个数组来模拟"被硬塞进来"的勾选。 */
let checkedInputs = [];
const document = {
  getElementById: element,
  createElement: makeNode,
  createTextNode: function (text) { return { textContent: text, children: [] }; },
  querySelectorAll: function (selector) {
    return selector === '#questionDocuments input:checked' ? checkedInputs : [];
  }
};
const windowHandlers = {};
const window = { addEventListener: function (type, handler) { windowHandlers[type] = handler; } };
const location = { hash: '' };

function collectText(node, parts) {
  if (!node || typeof node !== 'object') { return parts; }
  if (typeof node.textContent === 'string' && node.textContent) { parts.push(node.textContent); }
  (node.children || []).forEach(function (child) { collectText(child, parts); });
  return parts;
}
function areaText(id) { return collectText(element(id), []).join(' '); }
function collectInputs(node, found) {
  if (!node || typeof node !== 'object') { return found; }
  if (node.tagName === 'input') { found.push(node); }
  (node.children || []).forEach(function (child) { collectInputs(child, found); });
  return found;
}
function collectByClass(node, token, found) {
  if (!node || typeof node !== 'object') { return found; }
  const classes = String(node.className || '').split(/\s+/);
  if (classes.indexOf(token) !== -1) { found.push(node); }
  (node.children || []).forEach(function (child) { collectByClass(child, token, found); });
  return found;
}

const DOCUMENTS = [
  { document_id: 'doc-a', title: '可直接问答手册', collection: 'demo_kb', doc_version: 1,
    sha256: 'a', parser_version: 'p', searchable: true },
  /* R05 复现路径：GET /v1/documents 的 WHERE 未改，历史遗留的 status='indexed' 且 chunks=0
   * 的行仍会返回，只是带 searchable=false。前端必须只认 searchable，不能因为"接口返回了它"
   * 就当作可选。 */
  { document_id: 'doc-b', title: '待索引报告', collection: 'demo_kb', doc_version: 2,
    sha256: 'b', parser_version: 'p', status: 'indexed', block_count: 0,
    searchable: false, index_state: 'no_chunks', index_note: 'UPLOAD_HAS_NO_CHUNKS' },
  /* 人为构造的边界：即使 index_state 说 indexed，只要 searchable 不为 true 就不可选，
   * 证明过滤入口是 searchable 而不是 index_state。 */
  { document_id: 'doc-c', title: '状态可疑的旧文档', collection: 'demo_kb', doc_version: 3,
    sha256: 'c', parser_version: 'p', status: 'indexed', block_count: 0,
    searchable: false, index_state: 'indexed' }
];
const UPLOAD = { upload_id: 'u-b', filename: 'report.pdf', media_type: 'application/pdf',
  status: 'succeeded', created_at: '2024-05-01T00:00:00Z', review_state: 'pending',
  block_count: 0, locator_count: 0, unsupported_features: [], vector_index: 'deferred',
  searchable: false, index_state: 'not_approved' };
const uploadRecord = Object.assign({}, UPLOAD);

const calls = [];
function jsonResponse(payload, status) {
  return { ok: status >= 200 && status < 300, status: status, json: async function () { return payload; } };
}
async function apiFetch(url, options) {
  options = options || {};
  const method = (options.method || 'GET').toUpperCase();
  calls.push({ url: url, method: method });
  if (method === 'GET' && url.indexOf('/v1/documents') === 0) {
    return jsonResponse({ documents: DOCUMENTS }, 200);
  }
  if (method === 'POST' && /^\/v1\/uploads\/[^/]+\/approve$/.test(url)) {
    const id = decodeURIComponent(url.split('/')[3]);
    // 复核通过但没有切片：indexed=false，并给出 index_state/index_note。
    uploadRecord.review_state = 'approved';
    uploadRecord.searchable = false;
    uploadRecord.index_state = 'no_chunks';
    uploadRecord.index_note = 'UPLOAD_HAS_NO_CHUNKS';
    return jsonResponse({ upload_id: id, review_state: 'approved', indexed: false,
      index_state: 'no_chunks', index_note: 'UPLOAD_HAS_NO_CHUNKS', searchable: false }, 200);
  }
  if (method === 'GET' && url.indexOf('/v1/uploads?') === 0) {
    return jsonResponse({ uploads: [Object.assign({}, uploadRecord)], count: 1 }, 200);
  }
  if (method === 'GET' && /^\/v1\/uploads\/[^/]+$/.test(url)) {
    return jsonResponse(Object.assign({}, uploadRecord), 200);
  }
  return jsonResponse({ detail: { error_code: 'UNEXPECTED_CALL' } }, 500);
}

const AUTH = { user: { user_id: 'admin-1', roles: ['admin'] }, csrf: 'csrf-1' };
const sandbox = {
  console: console, setTimeout: setImmediate, clearTimeout: function () {},
  document: document, window: window, location: location,
  AUTH: AUTH,
  hasRole: function (role) { return !!(AUTH.user && AUTH.user.roles.indexOf(role) !== -1); },
  apiFetch: apiFetch, URLSearchParams: URLSearchParams
};
sandbox.$ = function (id) { return element(id); };
vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: 'multifile.js' });

function flush() { return new Promise(function (resolve) { setImmediate(resolve); }); }

(async function main() {
  // 1. 选择器：只有 searchable===true 的文件可勾选
  await sandbox.loadQuestionDocuments();
  await flush();
  const area = element('questionDocuments');
  const inputs = collectInputs(area, []);
  assert.strictEqual(inputs.length, 1,
    'only searchable documents may be selectable, got ' + inputs.length);
  assert.strictEqual(inputs[0].value, 'doc-a',
    'the selectable document must be the searchable one');
  assert.ok(areaText('questionDocuments').indexOf('可用于问答的文件') !== -1,
    'the searchable section heading is missing');
  const blocked = collectByClass(area, 'multifile-document-blocked', []);
  assert.strictEqual(blocked.length, 2,
    'every non-searchable document returned by the API must stay visible as blocked, got ' + blocked.length);
  const blockedText = collectText(blocked[0], []).join(' ');
  assert.ok(blockedText.indexOf('待索引报告') !== -1,
    'the blocked document must be named: ' + blockedText);
  assert.ok(blockedText.indexOf('暂不可问答') !== -1,
    'the blocked document must say 暂不可问答: ' + blockedText);
  assert.ok(areaText('questionDocuments').indexOf('暂不可问答的文件') !== -1,
    'the blocked section heading is missing');
  ['doc-b', 'doc-c'].forEach(function (id) {
    assert.strictEqual(inputs.filter(function (input) { return input.value === id; }).length, 0,
      id + ' (searchable=false) must never be selectable');
  });

  // 1b. 纵深防御：即使勾选被硬塞进不可问答的文档，也不得进入检索范围
  checkedInputs = [{ value: 'doc-b' }];
  assert.throws(function () { sandbox.applyDocumentSelection({}); },
    /暂不可问答/, 'a non-searchable document must be rejected from the scope');
  checkedInputs = [];

  // 2. index_state 三态中文语义
  const notApproved = sandbox.uploadIndexText({ index_state: 'not_approved', searchable: false });
  assert.ok(notApproved.indexOf('待复核') !== -1, 'not_approved -> 待复核: ' + notApproved);
  const noChunks = sandbox.uploadIndexText({ index_state: 'no_chunks', searchable: false });
  assert.ok(noChunks.indexOf('已解析，尚未建立问答索引（暂不可问答）') !== -1,
    'no_chunks -> 已解析，尚未建立问答索引（暂不可问答）: ' + noChunks);
  const indexed = sandbox.uploadIndexText({ index_state: 'indexed', searchable: true });
  assert.ok(indexed.indexOf('已建立索引，可用于问答') !== -1,
    'indexed -> 已建立索引，可用于问答: ' + indexed);
  assert.ok(noChunks.indexOf('searchable=false') !== -1,
    'the index text must state the searchable flag: ' + noChunks);

  // 2b. 后端冻结的 index_note 机器码 → 中文；null = 可问答不附提示；未收录码有兜底
  assert.strictEqual(
    sandbox.uploadIndexText({ index_state: 'indexed', searchable: true, index_note: null })
      .indexOf('说明：'), -1, 'a null index_note must not add a hint');
  assert.ok(sandbox.uploadIndexText({ index_state: 'not_approved', searchable: false,
    index_note: 'UPLOAD_NOT_APPROVED' }).indexOf('待复核') !== -1,
    'UPLOAD_NOT_APPROVED -> 待复核');
  assert.ok(sandbox.uploadIndexText({ index_state: 'no_chunks', searchable: false,
    index_note: 'UPLOAD_HAS_NO_CHUNKS' }).indexOf('已复核，但尚未建立问答索引，暂不可问答') !== -1,
    'UPLOAD_HAS_NO_CHUNKS -> 已复核，但尚未建立问答索引，暂不可问答');
  assert.ok(sandbox.uploadIndexText({ index_state: 'no_chunks', searchable: false,
    index_note: 'UPLOAD_HAS_NO_CONTRACT_BLOCKS' }).indexOf('解析未产出可入库的块') !== -1,
    'UPLOAD_HAS_NO_CONTRACT_BLOCKS -> 解析未产出可入库的块');
  assert.ok(sandbox.uploadIndexText({ index_state: 'no_chunks', searchable: false,
    index_note: 'UPLOAD_STATUS_NOT_INDEXED' }).indexOf('暂不可问答') !== -1,
    'UPLOAD_STATUS_NOT_INDEXED -> 暂不可问答');
  assert.ok(sandbox.uploadIndexNoteText('SOMETHING_ELSE').indexOf('索引说明：SOMETHING_ELSE') !== -1,
    'an unknown index_note code must fall back instead of being swallowed');

  // 2c. searchable=true 但 vector_index=deferred：只能说是词法可用，不得声称已建向量索引
  const lexical = sandbox.uploadIndexText({ index_state: 'indexed', searchable: true,
    vector_index: 'deferred', index_note: null });
  assert.ok(lexical.indexOf('词法') !== -1 && lexical.indexOf('未建立向量索引') !== -1,
    'a searchable-but-deferred document must be described as lexical-only: ' + lexical);
  assert.strictEqual(lexical.indexOf('向量索引已建立'), -1,
    'must not claim a vector index was built: ' + lexical);

  // 3. 复核 no_chunks：必须提示暂不可问答，不得显示成功可问答
  await sandbox.approveUpload('u-b');
  await flush();
  const message = element('fileMessage').textContent;
  assert.ok(message.indexOf('已复核，但尚未建立问答索引，暂不可用于问答') !== -1,
    'approving a no_chunks upload must warn: ' + message);
  assert.ok(message.indexOf('复核已通过') === -1,
    'approving a no_chunks upload must not claim a searchable success: ' + message);
  const record = sandbox.MULTIFILE.uploads[0];
  assert.strictEqual(record.searchable, false, 'the upload must stay searchable=false');
  assert.strictEqual(record.index_state, 'no_chunks', 'the upload must report index_state=no_chunks');

  console.log('SELECTOR HARNESS OK inputs=' + inputs.length + ' blocked=' + blocked.length +
    ' calls=' + calls.length);
})().catch(function (error) {
  console.error('SELECTOR HARNESS FAILED: ' + (error && error.stack || error));
  process.exit(1);
});
'''


def run_selector_harness(source_path):
    """Run the fake-DOM harness against one multifile.js copy."""
    node = shutil.which('node')
    with tempfile.TemporaryDirectory() as workspace:
        harness = Path(workspace) / 'selector_harness.js'
        harness.write_text(SELECTOR_HARNESS_JS, encoding='utf-8')
        return subprocess.run([node, str(harness), str(source_path)],
                              capture_output=True, text=True, timeout=120)


@unittest.skipUnless(shutil.which('node'), 'node 未安装')
class UploadSelectorHarnessTests(unittest.TestCase):
    """假 DOM + 打桩 apiFetch（不是真实浏览器 DOM 运行时，也不是真后端联调）。"""

    def test_only_searchable_documents_are_selectable_and_no_chunks_is_honest(self):
        completed = run_selector_harness(MULTIFILE_JS)
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        self.assertIn('SELECTOR HARNESS OK', completed.stdout)

    def _mutated_source(self, needle, replacement, workspace):
        source = MULTIFILE_JS.read_text(encoding='utf-8')
        self.assertIn(needle, source, '变异锚点缺失，测试本身需要更新：%r' % needle)
        copy = Path(workspace) / 'multifile_mutated.js'
        copy.write_text(source.replace(needle, replacement), encoding='utf-8')
        return copy

    def test_the_selector_filter_assertion_is_not_vacuous(self):
        # 去掉 searchable 过滤（等价于"不过滤"）后，可选项会多出 doc-b，harness 必须失败。
        with tempfile.TemporaryDirectory() as workspace:
            mutated = self._mutated_source(SEARCHABLE_GUARD, SEARCHABLE_MUTATION, workspace)
            completed = run_selector_harness(mutated)
        self.assertNotEqual(completed.returncode, 0,
                            '去掉 searchable 过滤后断言仍然通过，说明选择器断言是空转的')
        self.assertIn('SELECTOR HARNESS FAILED', completed.stderr or completed.stdout)

    def test_the_no_chunks_approval_assertion_is_not_vacuous(self):
        # 把 no_chunks 的诚实提示改成成功文案后，复核断言必须失败。
        with tempfile.TemporaryDirectory() as workspace:
            mutated = self._mutated_source(APPROVE_NO_CHUNKS_NOTICE, '复核已通过', workspace)
            completed = run_selector_harness(mutated)
        self.assertNotEqual(completed.returncode, 0,
                            '把 no_chunks 提示改成成功文案后断言仍然通过，说明复核断言是空转的')
        self.assertIn('SELECTOR HARNESS FAILED', completed.stderr or completed.stdout)


if __name__ == '__main__':
    unittest.main()
