"""上传并入库 / 我的文件 / 详情的前端契约回归。

本仓库没有 JavaScript 测试运行器，本轮也没有浏览器，所以这里做两件不同的事，并明确区分：

1. **结构断言**（`UploadUiContractTests` 等）：只钉住 `web/multifile.js` 读取的字段名、
   唯一的上传路径、轮询到终态的逻辑、中文状态与错误码映射、`vector_index=deferred` 的
   诚实提示、复核门与缓存版本。它们不启动 DOM，也不声称验证了浏览器运行时行为。
2. **打桩行为验证**（`UploadUiMockHarnessTests`）：用 Node + 假 DOM + 打桩 `apiFetch` 真的
   跑一遍"一个按钮 → POST /v1/uploads → 轮询到终态 → 逐份结果卡片 → 复核通过"。后端按冻结
   规范并行实现，本地后端可能尚未就绪，所以这一层验证的是前端逻辑本身（请求路径、请求头、
   并发上限、中文呈现），不是浏览器 DOM 运行时，也不是与真后端的联调。

冻结接口规范（后端按此实现，前端不得自行发明字段）：

* ``POST /v1/uploads?filename=&provenance=``，body 为原始字节，Content-Type
  ``application/octet-stream``，需 admin + CSRF + 同源；201 返回
  ``upload_id/job_id/filename/media_type/status/review_required``；
  413 ``UPLOAD_TOO_LARGE``、415 ``UNSUPPORTED_MEDIA_TYPE``、422 ``FILE_TYPE_OR_NAME_INVALID``、
  ``CAD_DWG_CONVERTER_UNAVAILABLE``（DWG 明确拒绝）。
* ``GET /v1/uploads?status=&media_type=&since=&limit=`` → ``{"uploads":[...],"count":N}``，每项含
  ``upload_id/filename/media_type/status/created_at/review_state/block_count/locator_count/
  unsupported_features``；R05 起每项还含 ``searchable``(bool) 与 ``index_state``
  (indexed|no_chunks|not_approved) / ``index_note``(str|None)。
* ``GET /v1/uploads/{upload_id}`` → 上述字段 + ``analysis`` + 失败时的 ``error_code`` +
  ``vector_index``(deferred/done) + ``contract_url``。
* ``status`` ∈ queued|running|succeeded|failed|partial_failure|cancelled。
* ``POST /v1/uploads/{upload_id}/approve``（admin+CSRF+同源）；``indexed`` 仅在真正可检索时为
  true，为 false 时给 ``index_state``/``index_note``。
"""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MULTIFILE_JS = ROOT / 'web' / 'multifile.js'
APP_JS = ROOT / 'web' / 'app.js'
INDEX_HTML = ROOT / 'web' / 'index.html'

# 前端读取的字段名必须逐个来自冻结规范。
FROZEN_FIELDS = ('upload_id', 'job_id', 'filename', 'media_type', 'status', 'created_at',
                 'review_state', 'review_required', 'block_count', 'locator_count',
                 'unsupported_features', 'analysis', 'vector_index', 'contract_url', 'error_code',
                 # R05：能否参与问答检索由 searchable 决定，index_state/index_note 给出原因。
                 'searchable', 'index_state', 'index_note')
# 规范冻结的状态取值 → 中文语义。
STATUS_LABELS = (('queued', '排队中'), ('running', '解析中'), ('succeeded', '已入库（待复核）'),
                 ('failed', '失败'), ('partial_failure', '部分失败'), ('cancelled', '已取消'))
# 规范冻结的错误码 → 人话解释。
ERROR_LABELS = (('UPLOAD_TOO_LARGE', '文件超过该类型上限'),
                ('UNSUPPORTED_MEDIA_TYPE', '暂不支持该文件类型'),
                ('CAD_DWG_CONVERTER_UNAVAILABLE', 'DWG 需要外部转换器，暂不支持'))
DEFERRED_NOTICE = '未建立向量索引（暂不可用于问答检索）'
REVIEW_GATE_NOTE = '未复核前不会出现在可检索文档中。'
PREVIOUS_ASSET_VERSION = (0, 38, 0)


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


class UploadUiContractTests(unittest.TestCase):
    """结构/契约断言：字段名、唯一上传路径、轮询终态、中文映射、诚实提示。"""

    def setUp(self):
        self.source = MULTIFILE_JS.read_text(encoding='utf-8')
        self.html = INDEX_HTML.read_text(encoding='utf-8')

    # ---------- 字段名与冻结规范一致 ----------

    def test_the_ui_reads_exactly_the_frozen_upload_fields(self):
        for field in FROZEN_FIELDS:
            self.assertIn(field, self.source, field)
        apply_fields = function_body(self.source, 'applyUploadFields')
        self.assertTrue(apply_fields, 'applyUploadFields must exist')
        for field in FROZEN_FIELDS:
            if field == 'error_code':
                # 失败原因只在 data.error_code 存在时落盘，不能把 null 覆盖上去。
                self.assertIn('data.error_code', apply_fields)
                self.assertIn('result.error_code', apply_fields)
                continue
            self.assertIn("'" + field + "'", apply_fields, field)

    def test_the_files_list_reads_the_frozen_list_payload(self):
        body = function_body(self.source, 'loadUploads')
        self.assertTrue(body, 'loadUploads must exist')
        self.assertIn('apiFetch', body)
        self.assertRegex(body, r"'/v1/uploads\?limit=")
        self.assertIn('data.uploads', body)
        self.assertIn('Array.isArray', body)
        # 空列表不能被当成"载荷缺失"的反面：两种情形必须分开说。
        self.assertIn('预期之外的载荷', body)

    def test_the_detail_reads_the_frozen_detail_payload(self):
        body = function_body(self.source, 'selectUpload')
        self.assertTrue(body, 'selectUpload must exist')
        self.assertIn("'/v1/uploads/' + encodeURIComponent(id)", body)
        detail = function_body(self.source, 'renderUploadDetail')
        for needle in ('analysis', 'unsupported_features', 'error_code', 'vector_index'):
            self.assertIn(needle, detail, needle)
        # 契约与定位只提供链接，不在这里重新渲染。
        self.assertIn('uploadContractLink(upload)', detail)

    # ---------- 一个按钮、一条上传路径 ----------

    def test_the_only_upload_path_is_post_v1_uploads(self):
        self.assertIn("'/v1/uploads?filename='", self.source)
        self.assertIn("'&provenance='", self.source)
        self.assertIn("'Content-Type': 'application/octet-stream'", self.source)
        self.assertIn('body: file', self.source)
        # 旧的两步接口（先登记源、再提交批次任务）不得再被调用。
        for retired in ('/v1/files/uploads', '/v1/files/jobs'):
            self.assertNotIn(retired, self.source, retired)
        # 上传只由这一个按钮触发：选择文件不再自动上传。
        self.assertEqual(self.source.count("'&provenance='"), 1, '只允许一处上传调用点')
        self.assertNotIn("$('uploadFiles').addEventListener('change'", self.source)

    def test_the_single_button_lives_in_the_form_submit_handler(self):
        self.assertEqual(self.source.count("addEventListener('submit'"), 1)
        self.assertIn("$('fileBatchForm').addEventListener('submit', submitFileBatch)", self.source)
        self.assertIn('>上传并入库</button>', self.html)
        self.assertNotIn('提交本批登记任务', self.html)
        self.assertEqual(self.html.count('id="uploadFiles"'), 1)
        self.assertEqual(self.html.count('id="submitFileBatch"'), 1)

    def test_the_upload_runs_at_most_two_files_at_a_time(self):
        self.assertRegex(self.source, r'var\s+UPLOAD_CONCURRENCY\s*=\s*2\s*;')
        body = function_body(self.source, 'submitFileBatch')
        self.assertIn('Promise.all([worker(), worker()])', body)

    # ---------- 轮询到终态 ----------

    def test_polling_continues_until_a_frozen_terminal_status(self):
        for status in ('queued', 'running', 'succeeded', 'failed', 'partial_failure', 'cancelled'):
            self.assertIn("'" + status + "'", self.source, status)
        match = re.search(r'var\s+UPLOAD_TERMINAL\s*=\s*\[(.*?)\];', self.source, re.S)
        self.assertTrue(match, 'UPLOAD_TERMINAL 必须存在')
        terminal = match.group(1)
        for status in ('succeeded', 'failed', 'partial_failure', 'cancelled'):
            self.assertIn("'" + status + "'", terminal, status)
        # queued/running 不是终态，不得混进 UPLOAD_TERMINAL。
        for status in ('queued', 'running'):
            self.assertNotIn("'" + status + "'", terminal, status)
        body = function_body(self.source, 'pollUpload')
        self.assertTrue(body, 'pollUpload must exist')
        self.assertIn("'/v1/uploads/' + encodeURIComponent(result.upload_id)", body)
        self.assertRegex(body, r'UPLOAD_TERMINAL\.indexOf\(result\.status\)\s*!==\s*-1')
        self.assertRegex(body, r'await\s+uploadSleep\(UPLOAD_POLL_MS\)')
        self.assertRegex(body, r'UPLOAD_POLL_LIMIT')

    # ---------- 中文状态与错误码映射 ----------

    def test_status_labels_are_chinese_and_cover_every_frozen_status(self):
        table = table_body(self.source, 'UPLOAD_STATUS_LABELS')
        self.assertTrue(table, 'UPLOAD_STATUS_LABELS must exist')
        for status, label in STATUS_LABELS:
            self.assertRegex(table, re.escape(status) + r"\s*:\s*'" + re.escape(label) + r"'")

    def test_error_codes_have_a_human_explanation(self):
        table = table_body(self.source, 'UPLOAD_ERROR_LABELS')
        self.assertTrue(table, 'UPLOAD_ERROR_LABELS must exist')
        for code, label in ERROR_LABELS:
            self.assertRegex(table, re.escape(code) + r"\s*:\s*'" + re.escape(label) + r"'")
        # 未收录的错误码不得被静默吞掉，也不得被编造成"成功"。
        message = function_body(self.source, 'uploadErrorMessage')
        self.assertIn('未收录的错误码', message)
        self.assertNotIn('已入库', message)

    def test_review_state_has_chinese_semantics(self):
        table = table_body(self.source, 'REVIEW_STATE_LABELS')
        self.assertTrue(table, 'REVIEW_STATE_LABELS must exist')
        self.assertIn('approved', table)
        self.assertIn('已复核', table)
        self.assertIn('待复核', table)

    def test_failure_shows_the_code_next_to_its_explanation(self):
        body = function_body(self.source, 'uploadResultCard')
        self.assertIn('错误码 ', body)
        self.assertIn('uploadErrorMessage(result.error_code', body)

    # ---------- 诚实：deferred 向量索引 ----------

    def test_deferred_vector_index_is_stated_as_not_searchable(self):
        table = table_body(self.source, 'VECTOR_INDEX_LABELS')
        self.assertRegex(table, r"deferred\s*:\s*'" + re.escape(DEFERRED_NOTICE) + r"'")
        self.assertIn(DEFERRED_NOTICE, self.source)
        # 不得把 deferred 说成已可检索。
        self.assertNotRegex(table, r"deferred[^\n]*可检索[^\n]*问答")
        result_card = function_body(self.source, 'uploadResultCard')
        self.assertIn('uploadVectorText(result.vector_index)', result_card)
        self.assertIn('uploadVectorText(upload.vector_index)', function_body(self.source, 'renderUploadDetail'))

    # ---------- 我的文件列表与前端过滤 ----------

    def test_the_list_block_offers_status_and_type_filters(self):
        for element in ('fileList', 'fileListMessage', 'uploadStatusFilter', 'uploadTypeFilter'):
            self.assertIn("$('" + element + "')", self.source, element)
            self.assertIn('id="' + element + '"', self.html, element)
        body = function_body(self.source, 'renderUploadList')
        self.assertIn('uploadStatusFilter', body)
        self.assertIn('uploadTypeFilter', body)
        self.assertIn('uploadExtension', body)
        # 列表里必须能看到类型/状态/时间/块数/定位数/缺口。
        row = function_body(self.source, 'uploadListRow')
        for needle in ('uploadTypeLabel(upload)', 'uploadValueText(upload.created_at)',
                       'block_count', 'locator_count', 'uploadGapText(upload.unsupported_features)',
                       'uploadStatusLabel(upload)'):
            self.assertIn(needle, row, needle)

    def test_a_list_row_opens_the_detail(self):
        row = function_body(self.source, 'uploadListRow')
        self.assertIn("addEventListener('click'", row)
        self.assertIn('selectUpload(', row)

    # ---------- 复核门 ----------

    def test_review_gate_posts_to_the_frozen_approve_path(self):
        body = function_body(self.source, 'approveUpload')
        self.assertTrue(body, 'approveUpload must exist')
        self.assertIn("'/v1/uploads/' + encodeURIComponent(id) + '/approve'", body)
        self.assertIn("method: 'POST'", body)
        self.assertIn('apiFetch(', body)
        self.assertIn(REVIEW_GATE_NOTE, self.source)
        self.assertIn(REVIEW_GATE_NOTE, self.html)
        # 复核按钮只在需要复核的 succeeded 项上出现，且走同一个 approve 调用。
        gate = function_body(self.source, 'uploadApproveButton')
        self.assertIn("button.textContent = '复核通过'", gate)
        self.assertIn('onApprove(upload.upload_id)', gate)
        self.assertIn('uploadApproveButton(result, approveUpload)', function_body(self.source, 'uploadResultCard'))
        self.assertIn('uploadApproveButton(upload, approveUpload)', function_body(self.source, 'renderUploadDetail'))
        needs = function_body(self.source, 'uploadNeedsReview')
        self.assertIn("upload.status !== 'succeeded'", needs)
        self.assertIn("'approved'", needs)

    def test_no_bypass_of_the_shared_csrf_aware_fetch(self):
        # 变更请求一律经过 app.js 的 apiFetch（它负责 X-CSRF-Token 与同源凭据）；
        # 本文件不得自己调 fetch，也不得自己拼 CSRF 头。
        self.assertIsNone(re.search(r'(?<!api)fetch\(', self.source),
                          'multifile.js 不得直接调用 fetch')
        self.assertNotIn('"X-CSRF-Token"', self.source)
        self.assertNotIn("'X-CSRF-Token'", self.source)
        self.assertIn("method: 'POST'", self.source)

    # ---------- 契约不重复实现 ----------

    def test_the_contract_view_is_a_link_not_a_reimplementation(self):
        self.assertNotIn('/contract', self.source, '契约渲染必须交给既有契约接口')
        link = function_body(self.source, 'uploadContractLink')
        self.assertTrue(link, 'uploadContractLink must exist')
        self.assertIn('safeContractHref', link)
        self.assertIn("link.target = '_blank'", link)
        self.assertIn("link.rel = 'noopener noreferrer'", link)
        guard = function_body(self.source, 'safeContractHref')
        self.assertIn("url.charAt(0) !== '/'", guard)

    # ---------- 缓存版本 ----------

    def test_asset_versions_are_bumped_for_the_edited_assets(self):
        versions = re.findall(r'/assets/[A-Za-z0-9_.-]+\?v=([0-9][0-9A-Za-z.\-]*)', self.html)
        self.assertEqual(len(versions), 3, 'index.html 应有三处 assets ?v=')
        self.assertEqual(len(set(versions)), 1, '三处 ?v= 必须一致：%r' % versions)
        current = tuple(int(part) for part in versions[0].split('.'))
        self.assertGreater(current, PREVIOUS_ASSET_VERSION,
                           'app.js/multifile.js/style.css 已改，?v= 必须提升')

    # ---------- 既有安全/身份纪律保留 ----------

    def test_the_section_keeps_the_existing_identity_discipline(self):
        clear = function_body(self.source, 'clearMultifile')
        for needle in ('MULTIFILE.token++', 'MULTIFILE.detailToken++', 'MULTIFILE.listToken++'):
            self.assertIn(needle, clear, needle)
        guard = function_body(self.source, 'fileActor')
        self.assertIn('AUTH.csrf === csrf', guard)
        self.assertIn('AUTH.user.user_id === actor', guard)
        # app.js 仍然通过 window.* 调用本区块的入口。
        self.assertIn('window.loadMultifile', APP_JS.read_text(encoding='utf-8'))
        self.assertIn('window.clearMultifile', APP_JS.read_text(encoding='utf-8'))

    def test_app_js_does_not_re_implement_the_upload_views(self):
        app = APP_JS.read_text(encoding='utf-8')
        for element in ('fileRows', 'fileList', 'fileDetail', 'fileJobs', 'fileJobDetail'):
            self.assertNotIn("$('" + element + "')", app, element)


class NodeSyntaxTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'node 未安装')
    def test_both_scripts_pass_node_check(self):
        node = shutil.which('node')
        for path in (APP_JS, MULTIFILE_JS):
            completed = subprocess.run([node, '--check', str(path)],
                                       capture_output=True, text=True, timeout=60)
            self.assertEqual(completed.returncode, 0, path.name + ': ' + completed.stderr)


# 打桩行为验证：假 DOM + 打桩 apiFetch，跑通"一个按钮 → 上传 → 轮询 → 结果卡片 → 复核"。
# 断言写在 JS 里（失败即非零退出），Python 只负责运行与报告，避免两边重复维护。
HARNESS_JS = r'''
'use strict';
/* Runtime mock harness for web/multifile.js: fake DOM + fake apiFetch.
 * Proves the one-button flow POSTs /v1/uploads, polls to a terminal state, renders
 * Chinese status/error semantics, vetoes DWG locally, gates review, and lists details. */
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const source = fs.readFileSync(process.argv[2], 'utf8');

function makeNode(tag) {
  const node = {
    tagName: tag, id: '', children: [], textContent: '', value: '', disabled: false,
    className: '', dataset: {}, style: {}, listeners: {}, files: [],
    htmlFor: '', type: '', target: '', rel: '', href: '', checked: false, required: false,
    append: function () { for (let i = 0; i < arguments.length; i++) node.children.push(arguments[i]); },
    appendChild: function (child) { node.children.push(child); return child; },
    replaceChildren: function () { node.children.length = 0; },
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
const document = {
  getElementById: element,
  createElement: makeNode,
  createTextNode: function (text) { return { textContent: text, children: [] }; },
  querySelectorAll: function () { return []; }
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
function findButtons(node, found) {
  if (!node || typeof node !== 'object') { return found; }
  if (node.listeners && node.listeners.click) { found.push(node); }
  (node.children || []).forEach(function (child) { findButtons(child, found); });
  return found;
}

const calls = [];
let inFlight = 0, maxInFlight = 0, uploadSeq = 0;
const uploads = {};
function jsonResponse(payload, status) {
  return { ok: status >= 200 && status < 300, status: status, json: async function () { return payload; } };
}
function pollScript() {
  return [
    { status: 'running' },
    { status: 'succeeded', block_count: 3, locator_count: 2, unsupported_features: ['table_layout'],
      analysis: { parse_status: 'parsed', adapter: 'pdf_text', blocks: 3, locators_written: 2 },
      vector_index: 'deferred', contract_url: '/v1/documents/doc-1/contract', review_state: 'pending',
      searchable: false, index_state: 'not_approved' }
  ];
}
async function apiFetch(url, options) {
  options = options || {};
  const method = (options.method || 'GET').toUpperCase();
  calls.push({ url: url, method: method, headers: options.headers || {}, body: options.body });
  if (method === 'POST' && url.indexOf('/v1/uploads?') === 0) {
    inFlight++;
    maxInFlight = Math.max(maxInFlight, inFlight);
    await new Promise(function (resolve) { setImmediate(resolve); });
    inFlight--;
    const params = new URLSearchParams(url.slice(url.indexOf('?') + 1));
    const name = params.get('filename');
    uploadSeq += 1;
    const id = 'u-' + uploadSeq;
    uploads[id] = {
      list: { upload_id: id, filename: name, media_type: 'application/pdf', status: 'queued',
              created_at: '2024-05-01T00:00:00Z', review_state: 'pending', block_count: 0,
              locator_count: 0, unsupported_features: [] },
      polls: pollScript()
    };
    return jsonResponse({ upload_id: id, job_id: 'j-' + uploadSeq, filename: name,
      media_type: 'application/pdf', status: 'queued', review_required: true }, 201);
  }
  if (method === 'POST' && /^\/v1\/uploads\/[^/]+\/approve$/.test(url)) {
    const id = decodeURIComponent(url.split('/')[3]);
    if (uploads[id]) {
      uploads[id].list.review_state = 'approved';
      // 该夹具代表"复核后确实有切片可检索"：indexed=true 并同步 searchable/index_state。
      uploads[id].list.searchable = true;
      uploads[id].list.index_state = 'indexed';
    }
    return jsonResponse({ upload_id: id, review_state: 'approved', indexed: true,
      index_state: 'indexed', searchable: true }, 200);
  }
  if (method === 'GET' && url.indexOf('/v1/uploads?') === 0) {
    const list = Object.keys(uploads).map(function (key) { return uploads[key].list; });
    return jsonResponse({ uploads: list, count: list.length }, 200);
  }
  if (method === 'GET' && /^\/v1\/uploads\/[^/]+$/.test(url)) {
    const id = decodeURIComponent(url.split('/')[3]);
    const record = uploads[id];
    if (!record) { return jsonResponse({ detail: { error_code: 'UPLOAD_NOT_FOUND' } }, 404); }
    if (record.polls.length) {
      const next = record.polls.shift();
      Object.keys(next).forEach(function (key) { record.list[key] = next[key]; });
    }
    return jsonResponse(Object.assign({}, record.list), 200);
  }
  if (method === 'GET' && url.indexOf('/v1/documents') === 0) {
    return jsonResponse({ documents: [] }, 200);
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
  const dwg = { name: 'plan.dwg', size: 2048 };
  const pdf = { name: 'drawing.pdf', size: 1024 };
  const json = { name: 'data.json', size: 512 };
  element('uploadProvenance').value = 'synthetic_demo';
  element('uploadFiles').files = [pdf, dwg, json];
  await sandbox.submitFileBatch({ preventDefault: function () {} });
  await flush();

  // 1. one button -> POST /v1/uploads with raw bytes and no second registration step
  const uploadPosts = calls.filter(function (call) {
    return call.method === 'POST' && call.url.indexOf('/v1/uploads?') === 0;
  });
  assert.strictEqual(uploadPosts.length, 2, 'DWG must be vetoed locally; two uploads expected');
  assert.strictEqual(uploadPosts[0].url,
    '/v1/uploads?filename=drawing.pdf&provenance=synthetic_demo');
  assert.strictEqual(uploadPosts[0].headers['Content-Type'], 'application/octet-stream');
  assert.strictEqual(uploadPosts[0].body, pdf, 'raw file bytes must be the body');
  assert.strictEqual(calls.filter(function (call) {
    return call.url.indexOf('/v1/files/jobs') !== -1 || call.url.indexOf('/v1/files/uploads') !== -1;
  }).length, 0, 'the old two-step file APIs must not be used');

  // 2. polling to a terminal state
  const polls = calls.filter(function (call) {
    return call.method === 'GET' && /^\/v1\/uploads\/u-1$/.test(call.url);
  });
  assert.ok(polls.length >= 2, 'poll GET /v1/uploads/{id} until terminal, got ' + polls.length);

  // 3. concurrency discipline stays at most 2
  assert.ok(maxInFlight <= 2, 'at most two concurrent uploads, saw ' + maxInFlight);

  // 4. per-file result card in the same card: status, counts, gaps, honest vector note
  const results = areaText('fileRows');
  assert.ok(results.indexOf('已入库（待复核）') !== -1, 'Chinese terminal status missing');
  assert.ok(results.indexOf('块数 3') !== -1, 'block_count missing');
  assert.ok(results.indexOf('定位数 2') !== -1, 'locator_count missing');
  assert.ok(results.indexOf('缺口：table_layout') !== -1, 'unsupported_features missing');
  assert.ok(results.indexOf('未建立向量索引（暂不可用于问答检索）') !== -1,
    'deferred vector_index must be stated explicitly');
  assert.ok(results.indexOf('待复核') !== -1,
    'index_state=not_approved must map to the Chinese semantics');
  assert.ok(results.indexOf('searchable=false') !== -1,
    'the card must state that the upload is not yet searchable');
  assert.ok(results.indexOf('CAD_DWG_CONVERTER_UNAVAILABLE') !== -1, 'DWG error code missing');
  assert.ok(results.indexOf('DWG 需要外部转换器，暂不支持') !== -1, 'DWG human explanation missing');
  assert.ok(results.indexOf('未复核前不会出现在可检索文档中。') !== -1, 'review gate note missing');

  // 5. my-files list + detail + approve
  assert.ok(areaText('fileList').indexOf('drawing.pdf') !== -1, 'list row missing');
  assert.ok(element('fileListMessage').textContent.indexOf('本页读取 2 份') !== -1,
    'list count message missing: ' + element('fileListMessage').textContent);

  const rows = element('fileList').children;
  assert.strictEqual(rows.length, 2, 'two uploads listed');
  rows[0].listeners.click();
  await flush(); await flush();
  const detail = areaText('fileDetail');
  assert.ok(detail.indexOf('解析明细（analysis）') !== -1, 'analysis block missing');
  assert.ok(detail.indexOf('analysis.adapter') !== -1, 'analysis fields missing');
  assert.ok(detail.indexOf('未建立向量索引（暂不可用于问答检索）') !== -1,
    'detail must repeat the honest vector note');
  assert.ok(detail.indexOf('查看该文档的契约与定位') !== -1, 'contract link missing');

  const buttons = findButtons(element('fileDetail'), []);
  assert.strictEqual(buttons.length, 2, 'approve + collapse buttons expected, got ' + buttons.length);
  buttons[0].listeners.click();
  await flush(); await flush();
  assert.ok(calls.some(function (call) {
    return call.method === 'POST' && call.url === '/v1/uploads/u-1/approve';
  }), 'approve must POST /v1/uploads/{id}/approve');
  assert.ok(areaText('fileDetail').indexOf('该文件可进入可检索文档集合') !== -1,
    'approved state must be shown after the gate opens');
  assert.ok(areaText('fileDetail').indexOf('已建立索引，可用于问答') !== -1,
    'index_state=indexed must be shown after a searchable approval');

  // 6. front-end filtering
  element('uploadStatusFilter').value = 'failed';
  sandbox.renderUploadList();
  assert.strictEqual(element('fileList').children.length, 0, 'status filter must hide non-failed rows');
  assert.ok(element('fileListMessage').textContent.indexOf('无匹配') !== -1,
    'filtered-empty message missing');
  element('uploadStatusFilter').value = '';
  element('uploadTypeFilter').value = '.json';
  sandbox.renderUploadList();
  assert.strictEqual(element('fileList').children.length, 1, 'type filter must keep the json row');

  // 7. list row text carries the required columns
  const rowText = collectText(element('fileList').children[0], []).join(' | ');
  ['块数', '定位数', '缺口', '2024-05-01T00:00:00Z'].forEach(function (needle) {
    assert.ok(rowText.indexOf(needle) !== -1, 'list row missing ' + needle + ': ' + rowText);
  });

  console.log('HARNESS OK calls=' + calls.length + ' maxConcurrent=' + maxInFlight);
})().catch(function (error) {
  console.error('HARNESS FAILED: ' + (error && error.stack || error));
  process.exit(1);
});
'''


@unittest.skipUnless(shutil.which('node'), 'node 未安装')
class UploadUiMockHarnessTests(unittest.TestCase):
    """用假 DOM + 打桩 apiFetch 验证前端逻辑（不是浏览器 DOM 运行时，也不是真后端联调）。"""

    def test_the_one_button_flow_uploads_polls_and_gates_review(self):
        node = shutil.which('node')
        with tempfile.TemporaryDirectory() as workspace:
            harness = Path(workspace) / 'upload_ui_harness.js'
            harness.write_text(HARNESS_JS, encoding='utf-8')
            completed = subprocess.run([node, str(harness), str(MULTIFILE_JS)],
                                       capture_output=True, text=True, timeout=120)
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        self.assertIn('HARNESS OK', completed.stdout)


if __name__ == '__main__':
    unittest.main()
