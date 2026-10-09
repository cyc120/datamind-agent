'use strict';
/* 上传并入库 / 我的文件 / 详情。
 *
 * 这里只按冻结的上传接口规范调用：
 *   POST /v1/uploads?filename=&provenance=   body=原始字节
 *   GET  /v1/uploads?status=&media_type=&since=&limit=
 *   GET  /v1/uploads/{upload_id}
 *   POST /v1/uploads/{upload_id}/approve
 * 变更请求一律走 app.js 的 apiFetch（带 X-CSRF-Token 与同源凭据），本文件不另开绕过
 * 身份或 CSRF 的通道。
 */
var MULTIFILE = {
  token: 0, docs: [], docToken: 0, detailToken: 0, listToken: 0,
  uploading: false, approving: null,
  results: [], uploads: [], uploadId: null, uploadDetail: null
};

/* 沿用既有纪律：一次最多同时处理 2 份文件。 */
var UPLOAD_CONCURRENCY = 2;
var UPLOAD_MAX_FILES = 20;
/* 轮询终态的节奏与上限：超时只标注"仍在处理中"，不假装已完成。 */
var UPLOAD_POLL_MS = 1500;
var UPLOAD_POLL_LIMIT = 120;
var UPLOAD_TERMINAL = ['succeeded', 'failed', 'partial_failure', 'cancelled'];
var UPLOAD_ACTIVE = ['queued', 'running'];
/* 冻结规范给出的每类上限（字节）。DWG 由服务端明确拒绝，这里只做本地预检。 */
var UPLOAD_LIMITS = [
  ['.dxf', 8 * 1024 * 1024], ['.json', 1 * 1024 * 1024],
  ['.docx', 16 * 1024 * 1024], ['.pptx', 16 * 1024 * 1024],
  ['.xlsx', 16 * 1024 * 1024], ['.pdf', 32 * 1024 * 1024]
];
var UPLOAD_TYPE_LABELS = {
  '.dxf': 'DXF 图纸', '.json': '测量 JSON', '.docx': 'Word 文档',
  '.pptx': '演示文稿', '.xlsx': '表格', '.pdf': 'PDF'
};
var UPLOAD_STATUS_LABELS = {
  queued: '排队中', running: '解析中', succeeded: '已入库（待复核）',
  failed: '失败', partial_failure: '部分失败', cancelled: '已取消'
};
var UPLOAD_ERROR_LABELS = {
  UPLOAD_TOO_LARGE: '文件超过该类型上限',
  UNSUPPORTED_MEDIA_TYPE: '暂不支持该文件类型',
  FILE_TYPE_OR_NAME_INVALID: '文件名或扩展名不合法，或扩展名与实际类型不一致',
  CAD_DWG_CONVERTER_UNAVAILABLE: 'DWG 需要外部转换器，暂不支持',
  UPLOAD_NOT_FOUND: '上传记录不存在或已被清理',
  UPLOAD_NOT_REVIEWABLE: '该文件当前状态不可复核（可能仍在解析、已复核或已失败）',
  // 后端 docs/UPLOAD_PIPELINE.md 冻结的其余上传族错误码（缺失会让用户只看到"未收录"）。
  UPLOAD_EMPTY: '上传内容为空',
  UPLOAD_STAGED_SOURCE_CHANGED: '暂存文件在解析前被改动，已拒绝（请重新上传）',
  UPLOAD_WAVE_ATTEMPTS_EXHAUSTED: '解析多次超时未完成，已停止（请重试或换用更小的文件）',
  UPLOAD_HAS_NO_CONTRACT_BLOCKS: '解析未产出可入库的块（详见缺口说明）',
  PARSE_ADAPTER_UNKNOWN: '没有登记该类型的解析适配器，无法解析',
  PARSE_MEDIA_UNREADABLE: '文件不可读或已损坏',
  PARSE_RESULT_INVALID: '解析结果未通过契约校验，已拒绝入库',
  LOGIN_REQUIRED: '请先登录后再操作',
  SESSION_INVALID: '会话已失效，请重新登录',
  PERMISSION_DENIED: '当前账户没有此操作权限',
  CSRF_REQUIRED: '请求校验失败，请刷新页面后重试',
  ORIGIN_NOT_ALLOWED: '请求来源不被允许，请从本站页面发起操作',
  DATABASE_UNAVAILABLE: '元数据库暂不可用，请稍后重试'
};
/* 诚实：deferred 必须说清"暂不可用于问答检索"。 */
var VECTOR_INDEX_LABELS = {
  deferred: '未建立向量索引（暂不可用于问答检索）',
  done: '向量索引已建立，可用于问答检索'
};
var REVIEW_STATE_LABELS = {
  pending: '待复核', required: '待复核', review_required: '待复核', pending_review: '待复核',
  approved: '已复核', rejected: '已拒绝', not_required: '无需复核'
};
var REVIEW_GATE_NOTE = '未复核前不会出现在可检索文档中。';
/* 冻结的上传接口新增字段：能否参与问答检索只由 searchable 决定，index_state 给出可读原因，
 * index_note 是后端给的机器可读说明。复核通过（review_state=approved）不等于索引就绪，
 * 不得据此推断"可以问答"。 */
var INDEX_STATE_LABELS = {
  indexed: '已建立索引，可用于问答',
  no_chunks: '已解析，尚未建立问答索引（暂不可问答）',
  not_approved: '待复核'
};
/* 后端冻结的 index_note 机器码 → 人话。null 表示"可问答，无提示"，未收录码走兜底文案。 */
var INDEX_NOTE_LABELS = {
  UPLOAD_NOT_APPROVED: '待复核',
  UPLOAD_HAS_NO_CHUNKS: '已复核，但尚未建立问答索引，暂不可问答',
  UPLOAD_HAS_NO_CONTRACT_BLOCKS: '解析未产出可入库的块',
  UPLOAD_STATUS_NOT_INDEXED: '暂不可问答（该文档状态不是 indexed）'
};
/* 暂不可问答的文档必须可见并给出原因，不能从选择器里静默消失。 */
var QUESTION_BLOCKED_NOTE = '已解析，尚未建立问答索引（暂不可问答）';
var QUESTION_SEARCHABLE_HEADING = '可用于问答的文件（searchable=true）';
var QUESTION_BLOCKED_HEADING = '暂不可问答的文件（searchable=false）';
var ANALYSIS_KEYS = ['parse_status', 'adapter', 'blocks', 'locators_written'];

function uploadLabel(table, key) {
  return Object.prototype.hasOwnProperty.call(table, key) ? table[key] : null;
}

function uploadExtension(name) {
  var match = /(\.[A-Za-z0-9]+)$/.exec(String(name || ''));
  return match ? match[1].toLowerCase() : '';
}

function uploadLimitFor(extension) {
  for (var index = 0; index < UPLOAD_LIMITS.length; index++) {
    if (UPLOAD_LIMITS[index][0] === extension) { return UPLOAD_LIMITS[index]; }
  }
  return null;
}

function uploadMib(bytes) {
  return String(Math.round(bytes / (1024 * 1024)));
}

function uploadValueText(value) {
  if (value === null || value === undefined || value === '') { return '未知'; }
  if (typeof value === 'object') {
    try { return JSON.stringify(value); } catch (error) { return String(value); }
  }
  return String(value);
}

function uploadCountText(value) {
  if (value === null || value === undefined) { return '未知'; }
  return String(value);
}

function uploadGapText(features) {
  if (features === null || features === undefined) { return '未知'; }
  if (!Array.isArray(features)) { return uploadValueText(features); }
  return features.length ? features.join('、') : '无';
}

function uploadTypeLabel(upload) {
  var extension = uploadExtension(upload && upload.filename);
  var label = uploadLabel(UPLOAD_TYPE_LABELS, extension);
  if (label) { return label; }
  return (upload && upload.media_type) || '未知类型';
}

function uploadNeedsReview(upload) {
  if (!upload || upload.status !== 'succeeded') { return false; }
  return upload.review_state !== 'approved' && upload.review_state !== 'not_required';
}

function uploadStatusLabel(upload) {
  var status = upload && upload.status;
  if (status === 'succeeded' && !uploadNeedsReview(upload)) { return '已入库（已复核）'; }
  return uploadLabel(UPLOAD_STATUS_LABELS, status) || uploadValueText(status);
}

function reviewStateLabel(state) {
  if (!state) { return '未知'; }
  return uploadLabel(REVIEW_STATE_LABELS, state) || state;
}

function uploadErrorCode(data) {
  if (!data) { return null; }
  if (data.detail && data.detail.error_code) { return data.detail.error_code; }
  if (data.error_code) { return data.error_code; }
  if (typeof data.detail === 'string' && /^[A-Z][A-Z0-9_]{2,}$/.test(data.detail)) { return data.detail; }
  return null;
}

function uploadErrorMessage(code, status) {
  var label = uploadLabel(UPLOAD_ERROR_LABELS, code);
  if (label) { return label; }
  if (code) { return '操作未完成：' + code + '（未收录的错误码，请查看后端日志）'; }
  return '操作未完成（HTTP ' + status + '）';
}

function uploadVectorText(value) {
  var label = uploadLabel(VECTOR_INDEX_LABELS, value);
  if (label) { return label; }
  if (value) { return '向量索引状态：' + value; }
  return '向量索引状态未知；解析完成前不能据此判断可否用于问答检索。';
}

/* searchable 是"真的能参与问答检索"的唯一判据；缺失一律按不可问答处理，不默认放行。 */
function uploadSearchable(upload) {
  return !!(upload && upload.searchable === true);
}

function uploadIndexStateLabel(state) {
  if (!state) { return null; }
  return uploadLabel(INDEX_STATE_LABELS, state);
}

function uploadIndexNoteText(note) {
  if (!note) { return null; }  // null → 可问答，不附提示
  var label = uploadLabel(INDEX_NOTE_LABELS, note);
  // 未收录的机器码不得被静默吞掉，也不能编造成"可问答"。
  return label || ('索引说明：' + note);
}

function uploadIndexText(upload) {
  var label = uploadIndexStateLabel(upload && upload.index_state);
  var text = label ? '问答索引：' + label
    : '问答索引状态未知（接口未返回 index_state）；确认可参与问答检索前不要把它当作可问答文件';
  text += ' · searchable=' + (uploadSearchable(upload) ? 'true' : 'false');
  // 本管道 vector_index 仍为 deferred：searchable=true 只说明靠词法检索可用，不得被读成
  // "已建向量索引"。
  if (uploadSearchable(upload) && upload.vector_index === 'deferred') {
    text += ' · 本管道 vector_index=deferred：靠词法检索可用，未建立向量索引';
  }
  var note = uploadIndexNoteText(upload && upload.index_note);
  if (note && note !== label) { text += ' · 说明：' + note; }
  return text;
}

async function uploadJson(response) {
  try {
    return await response.json();
  } catch (error) {
    return {};
  }
}

function uploadSleep(milliseconds) {
  return new Promise(function(resolve) { setTimeout(resolve, milliseconds); });
}

function fileBusy() {
  return MULTIFILE.uploading || !!MULTIFILE.approving;
}

function fileActor(token, actor, csrf) {
  return token === MULTIFILE.token && AUTH.user &&
    AUTH.user.user_id === actor && AUTH.csrf === csrf;
}

function clearMultifile() {
  MULTIFILE.token++;
  MULTIFILE.docToken++;
  MULTIFILE.detailToken++;
  MULTIFILE.listToken++;
  MULTIFILE.results = [];
  MULTIFILE.uploads = [];
  MULTIFILE.uploadId = null;
  MULTIFILE.uploadDetail = null;
  MULTIFILE.uploading = false;
  MULTIFILE.approving = null;
  ['fileRows', 'fileList', 'fileDetail'].forEach(function(id) {
    $(id).replaceChildren();
  });
  $('uploadFiles').value = '';
  $('fileMessage').textContent = '管理员登录后可上传并登记文件。';
  $('fileListMessage').textContent = '';
  renderMultifileAuth();
}

function renderMultifileAuth() {
  var disabled = !hasRole('admin') || fileBusy();
  ['uploadFiles', 'uploadProvenance', 'submitFileBatch', 'refreshFiles',
   'uploadStatusFilter', 'uploadTypeFilter'].forEach(function(id) {
    var element = $(id);
    if (element) { element.disabled = disabled; }
  });
  ['fileRows', 'fileList', 'fileDetail'].forEach(function(id) {
    var area = $(id);
    if (!area) { return; }
    area.querySelectorAll('button').forEach(function(button) {
      button.disabled = disabled;
    });
  });
}

/* ---------------- 一个按钮：上传并入库 ---------------- */

function precheckUpload(file) {
  var name = (file && file.name) || '';
  if (!name || name.indexOf('/') !== -1 || name.indexOf('\\') !== -1) {
    return {error_code: 'FILE_TYPE_OR_NAME_INVALID', message: '文件名不合法。'};
  }
  var extension = uploadExtension(name);
  if (extension === '.dwg') {
    return {error_code: 'CAD_DWG_CONVERTER_UNAVAILABLE', message: 'DWG 需要外部转换器，暂不支持。'};
  }
  var limit = uploadLimitFor(extension);
  if (!limit) {
    return {error_code: 'UNSUPPORTED_MEDIA_TYPE', message: '暂不支持该文件类型。'};
  }
  if (!file.size) {
    return {error_code: 'FILE_TYPE_OR_NAME_INVALID', message: '文件为空。'};
  }
  if (file.size > limit[1]) {
    return {error_code: 'UPLOAD_TOO_LARGE',
            message: '超过 ' + extension + ' 的 ' + uploadMib(limit[1]) + ' MiB 上限。'};
  }
  return null;
}

function applyUploadFields(result, data) {
  ['upload_id', 'job_id', 'filename', 'media_type', 'status', 'created_at', 'review_state',
   'review_required', 'block_count', 'locator_count', 'unsupported_features', 'analysis',
   'vector_index', 'contract_url', 'searchable', 'index_state', 'index_note'].forEach(function(key) {
    if (Object.prototype.hasOwnProperty.call(data, key)) { result[key] = data[key]; }
  });
  if (data.error_code) { result.error_code = data.error_code; }
}

function appendText(parent, text, className) {
  var paragraph = document.createElement('p');
  if (className) { paragraph.className = className; }
  paragraph.textContent = text;
  parent.append(paragraph);
  return paragraph;
}

/* contract_url 只接受同源路径，避免把服务端字段变成外站跳转。 */
function safeContractHref(url) {
  if (typeof url !== 'string') { return null; }
  if (url.charAt(0) !== '/' || url.charAt(1) === '/' || url.charAt(1) === '\\') { return null; }
  return url;
}

function uploadContractLink(upload) {
  var href = safeContractHref(upload && upload.contract_url);
  if (!href) { return null; }
  var paragraph = document.createElement('p');
  paragraph.className = 'upload-contract';
  var link = document.createElement('a');
  link.href = href;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  link.textContent = '查看该文档的契约与定位';
  paragraph.append(link);
  var hint = document.createElement('span');
  hint.className = 'field-hint';
  hint.textContent = '（契约与定位由后端契约接口渲染，本页不重复实现。）';
  paragraph.append(hint);
  return paragraph;
}

function uploadApproveButton(upload, onApprove) {
  var paragraph = document.createElement('p');
  paragraph.className = 'field-hint upload-review-gate';
  paragraph.textContent = REVIEW_GATE_NOTE;
  var button = document.createElement('button');
  button.type = 'button';
  button.className = 'secondary';
  button.dataset.uploadApprove = upload.upload_id || upload.name;
  button.textContent = '复核通过';
  button.addEventListener('click', function() { onApprove(upload.upload_id); });
  paragraph.append(button);
  return paragraph;
}

function uploadResultCard(result) {
  var card = document.createElement('div');
  card.className = 'cad-review-card upload-result';
  card.dataset.uploadName = result.name;

  var heading = document.createElement('h3');
  heading.textContent = result.name + ' · ' + uploadTypeLabel(result);
  card.append(heading);

  var failed = result.status === 'failed' || result.status === 'partial_failure';
  var status = document.createElement('p');
  status.className = 'upload-status';
  var pill = document.createElement('span');
  pill.className = 'status' + (failed ? ' fail' :
    (UPLOAD_ACTIVE.indexOf(result.status) !== -1 ? ' wait' : ''));
  pill.textContent = uploadStatusLabel(result);
  status.append(pill);
  if (result.upload_id) {
    status.append(document.createTextNode(' · 上传 ' + result.upload_id));
  }
  card.append(status);

  if (failed) {
    var reason = result.error_code ?
      '错误码 ' + result.error_code + '：' + uploadErrorMessage(result.error_code, result.http_status) :
      (result.error_message || '未提供错误码与说明。');
    if (result.local) { reason += '（本地预检，未提交服务端）'; }
    appendText(card, reason, 'upload-failure');
  } else if (result.error_message) {
    appendText(card, result.error_message, 'upload-failure');
  }

  appendText(card, '块数 ' + uploadCountText(result.block_count) +
    ' · 定位数 ' + uploadCountText(result.locator_count), 'upload-counts');
  appendText(card, '缺口：' + uploadGapText(result.unsupported_features), 'upload-gaps');
  appendText(card, uploadVectorText(result.vector_index), 'field-hint upload-vector');
  // 复核通过 / 已入库都不代表可问答：searchable 才是判据，中文语义逐态给出。
  appendText(card, uploadIndexText(result), 'field-hint upload-index' +
    (uploadSearchable(result) ? '' : ' upload-index-blocked'));
  if (result.poll_exhausted) {
    appendText(card, '轮询已达上限，仍在处理中；请稍后在「我的文件」里刷新查看。', 'field-hint upload-pending');
  }

  if (uploadNeedsReview(result)) {
    card.append(uploadApproveButton(result, approveUpload));
  }
  var link = uploadContractLink(result);
  if (link) { card.append(link); }
  return card;
}

function renderUploadResults() {
  var area = $('fileRows');
  area.replaceChildren();
  MULTIFILE.results.forEach(function(result) {
    area.append(uploadResultCard(result));
  });
  renderMultifileAuth();
}

function uploadSummary() {
  var total = MULTIFILE.results.length;
  var succeeded = MULTIFILE.results.filter(function(result) {
    return result.status === 'succeeded';
  }).length;
  var failed = MULTIFILE.results.filter(function(result) {
    return result.status === 'failed' || result.status === 'partial_failure';
  }).length;
  var pending = total - succeeded - failed;
  var text = '本批 ' + total + ' 份：已入库 ' + succeeded + ' 份，失败 ' + failed + ' 份';
  if (pending > 0) { text += '，仍在处理 ' + pending + ' 份'; }
  return text + '。' + REVIEW_GATE_NOTE;
}

async function uploadOne(index, file, provenance, token, actor, csrf, read) {
  var result = MULTIFILE.results[index];
  if (!result) { return; }
  var problem = precheckUpload(file);
  if (problem) {
    result.status = 'failed';
    result.error_code = problem.error_code;
    result.error_message = problem.message;
    result.local = true;
    if (read === MULTIFILE.detailToken) { renderUploadResults(); }
    return;
  }
  result.status = 'running';
  if (read === MULTIFILE.detailToken) { renderUploadResults(); }
  var response;
  try {
    response = await apiFetch('/v1/uploads?filename=' + encodeURIComponent(file.name) +
      '&provenance=' + encodeURIComponent(provenance), {
      method: 'POST', headers: {'Content-Type': 'application/octet-stream'}, body: file
    });
  } catch (error) {
    if (!fileActor(token, actor, csrf)) { return; }
    result.status = 'failed';
    result.error_message = '上传请求未能发出：' + error.message;
    if (read === MULTIFILE.detailToken) { renderUploadResults(); }
    return;
  }
  if (!fileActor(token, actor, csrf)) { return; }
  var data = await uploadJson(response);
  result.http_status = response.status;
  if (read !== MULTIFILE.detailToken) { return; }
  if (!response.ok) {
    result.status = 'failed';
    result.error_code = uploadErrorCode(data) || ('HTTP_' + response.status);
    result.error_message = null;
    renderUploadResults();
    return;
  }
  applyUploadFields(result, data);
  if (!result.status) { result.status = 'queued'; }
  renderUploadResults();
  await pollUpload(result, token, actor, csrf, read);
}

async function pollUpload(result, token, actor, csrf, read) {
  if (!result.upload_id) { return; }
  var path = '/v1/uploads/' + encodeURIComponent(result.upload_id);
  for (var attempt = 0; attempt < UPLOAD_POLL_LIMIT; attempt++) {
    if (UPLOAD_TERMINAL.indexOf(result.status) !== -1) { return; }
    if (!fileActor(token, actor, csrf) || read !== MULTIFILE.detailToken) { return; }
    await uploadSleep(UPLOAD_POLL_MS);
    if (!fileActor(token, actor, csrf) || read !== MULTIFILE.detailToken) { return; }
    var response;
    try {
      response = await apiFetch(path);
    } catch (error) {
      continue;
    }
    if (!fileActor(token, actor, csrf) || read !== MULTIFILE.detailToken) { return; }
    var data = await uploadJson(response);
    if (!response.ok) {
      result.http_status = response.status;
      result.status = 'failed';
      result.error_code = uploadErrorCode(data) || ('HTTP_' + response.status);
      result.error_message = null;
      if (read === MULTIFILE.detailToken) { renderUploadResults(); }
      return;
    }
    applyUploadFields(result, data);
    if (read === MULTIFILE.detailToken) { renderUploadResults(); }
  }
  result.poll_exhausted = true;
  if (read === MULTIFILE.detailToken) { renderUploadResults(); }
}

async function submitFileBatch(event) {
  if (event && event.preventDefault) { event.preventDefault(); }
  if (!hasRole('admin') || fileBusy()) { return; }
  var files = Array.from($('uploadFiles').files || []);
  if (!files.length) {
    $('fileMessage').textContent = '请先选择要上传并入库的文件。';
    return;
  }
  if (files.length > UPLOAD_MAX_FILES) {
    $('fileMessage').textContent = '每次最多上传 ' + UPLOAD_MAX_FILES + ' 份文件。';
    return;
  }
  var provenance = $('uploadProvenance').value;
  if (!provenance) {
    $('fileMessage').textContent = '请选择文件来源。';
    return;
  }
  var actor = AUTH.user.user_id;
  var csrf = AUTH.csrf;
  var token = ++MULTIFILE.token;
  // 记住本次结果渲染的代数：选择被作废或详情另开时，旧上传不得再覆盖结果卡片。
  var read = ++MULTIFILE.detailToken;
  MULTIFILE.uploading = true;
  MULTIFILE.results = files.map(function(file) {
    return {
      name: file.name, filename: file.name, size: file.size,
      upload_id: null, job_id: null, media_type: null, status: 'queued',
      created_at: null, review_state: null, review_required: true,
      block_count: null, locator_count: null, unsupported_features: null,
      analysis: null, vector_index: null, contract_url: null,
      error_code: null, error_message: null, http_status: null,
      local: false, poll_exhausted: false
    };
  });
  renderUploadResults();
  $('fileMessage').textContent = '正在上传并入库 ' + files.length +
    ' 份文件，最多同时处理 ' + UPLOAD_CONCURRENCY + ' 份…';
  var next = 0;
  async function worker() {
    while (next < files.length) {
      // 被作废的旧登录/旧选择不得继续提交。
      if (!fileActor(token, actor, csrf) || read !== MULTIFILE.detailToken) { return; }
      var index = next++;
      await uploadOne(index, files[index], provenance, token, actor, csrf, read);
    }
  }
  try {
    await Promise.all([worker(), worker()]);
  } finally {
    if (fileActor(token, actor, csrf)) {
      MULTIFILE.uploading = false;
      if (read === MULTIFILE.detailToken) {
        renderUploadResults();
        $('fileMessage').textContent = uploadSummary();
      } else {
        // 结果代数已被别的选择接管：不再重绘卡片，但必须把按钮解冻。
        renderMultifileAuth();
      }
    }
  }
  if (fileActor(token, actor, csrf) && read === MULTIFILE.detailToken) {
    await loadUploads();
  }
}

/* ---------------- 我的文件列表 ---------------- */

function uploadListRow(upload) {
  var row = document.createElement('button');
  row.type = 'button';
  row.className = 'upload-row';
  row.dataset.uploadId = upload.upload_id;

  var name = document.createElement('span');
  name.className = 'upload-row-name';
  name.textContent = uploadValueText(upload.filename || upload.upload_id);
  row.append(name);

  var meta = document.createElement('span');
  meta.className = 'upload-row-meta';
  meta.textContent = uploadTypeLabel(upload) + ' · ' + uploadValueText(upload.created_at);
  row.append(meta);

  var counts = document.createElement('span');
  counts.className = 'upload-row-meta';
  counts.textContent = '块数 ' + uploadCountText(upload.block_count) +
    ' · 定位数 ' + uploadCountText(upload.locator_count);
  row.append(counts);

  var gaps = document.createElement('span');
  gaps.className = 'upload-row-gaps';
  gaps.textContent = '缺口：' + uploadGapText(upload.unsupported_features);
  row.append(gaps);

  var state = document.createElement('span');
  state.className = 'status' + (upload.status === 'failed' || upload.status === 'partial_failure' ? ' fail' :
    (UPLOAD_ACTIVE.indexOf(upload.status) !== -1 ? ' wait' : ''));
  state.textContent = uploadStatusLabel(upload);
  row.append(state);

  row.addEventListener('click', function() { selectUpload(upload.upload_id); });
  return row;
}

function renderUploadList() {
  var area = $('fileList');
  area.replaceChildren();
  var statusFilter = $('uploadStatusFilter') ? $('uploadStatusFilter').value : '';
  var typeFilter = $('uploadTypeFilter') ? $('uploadTypeFilter').value : '';
  var visible = MULTIFILE.uploads.filter(function(upload) {
    if (statusFilter && upload.status !== statusFilter) { return false; }
    if (typeFilter && uploadExtension(upload.filename) !== typeFilter) { return false; }
    return true;
  });
  visible.forEach(function(upload) { area.append(uploadListRow(upload)); });
  if (!MULTIFILE.uploads.length) {
    $('fileListMessage').textContent = '暂无上传记录；用上方「上传并入库」提交第一份文件。';
  } else if (!visible.length) {
    $('fileListMessage').textContent = '本页读取 ' + MULTIFILE.uploads.length +
      ' 份，当前筛选无匹配；状态与类型筛选只作用于本页已读取的记录。';
  } else {
    // 只说"本页读取"：规范未定义 count 是总数还是本页条数，不臆造总量。
    $('fileListMessage').textContent = '本页读取 ' + MULTIFILE.uploads.length + ' 份，显示 ' +
      visible.length + ' 份。点击一行查看详情。' + REVIEW_GATE_NOTE;
  }
  renderMultifileAuth();
}

async function loadUploads() {
  renderMultifileAuth();
  if (!hasRole('admin')) {
    $('fileMessage').textContent = '管理员登录后可上传并登记文件。';
    return;
  }
  var token = MULTIFILE.token;
  var actor = AUTH.user.user_id;
  var csrf = AUTH.csrf;
  var read = ++MULTIFILE.listToken;
  try {
    var response = await apiFetch('/v1/uploads?limit=100');
    var data = await uploadJson(response);
    if (!fileActor(token, actor, csrf) || read !== MULTIFILE.listToken) { return; }
    if (!response.ok) {
      throw new Error(uploadErrorMessage(uploadErrorCode(data), response.status));
    }
    var uploads = Array.isArray(data.uploads) ? data.uploads : null;
    if (!uploads) {
      // 空列表与"载荷不对"必须区分：否则网关错误会被读成"你还没有文件"。
      $('fileMessage').textContent = '文件列表返回了预期之外的载荷（缺少 uploads 数组），请查看后端日志。';
      return;
    }
    MULTIFILE.uploads = uploads;
    renderUploadList();
  } catch (error) {
    if (fileActor(token, actor, csrf) && read === MULTIFILE.listToken) {
      $('fileMessage').textContent = error.message;
    }
  }
}

/* app.js 通过 window.loadMultifile 进入本区块。 */
async function loadMultifile() {
  await loadUploads();
}

/* ---------------- 详情 ---------------- */

function uploadDetailRow(list, label, value) {
  var key = document.createElement('dt');
  key.textContent = label;
  var detail = document.createElement('dd');
  detail.textContent = uploadValueText(value);
  list.append(key, detail);
}

function renderUploadAnalysis(card, analysis) {
  if (!analysis || typeof analysis !== 'object') {
    appendText(card, '解析明细（analysis）：接口未返回。', 'field-hint');
    return;
  }
  var heading = document.createElement('h3');
  heading.textContent = '解析明细（analysis）';
  card.append(heading);
  var list = document.createElement('dl');
  list.className = 'upload-analysis';
  ANALYSIS_KEYS.forEach(function(key) {
    if (Object.prototype.hasOwnProperty.call(analysis, key)) {
      uploadDetailRow(list, 'analysis.' + key, analysis[key]);
    }
  });
  Object.keys(analysis).forEach(function(key) {
    if (ANALYSIS_KEYS.indexOf(key) !== -1) { return; }
    uploadDetailRow(list, 'analysis.' + key, analysis[key]);
  });
  card.append(list);
}

function renderUploadDetail() {
  var area = $('fileDetail');
  area.replaceChildren();
  var upload = MULTIFILE.uploadDetail;
  if (!upload) { return; }
  var card = document.createElement('div');
  card.className = 'cad-review-card upload-detail-card';
  var heading = document.createElement('h3');
  heading.textContent = '详情 · ' + (upload.filename || upload.upload_id || '未知文件');
  card.append(heading);

  var list = document.createElement('dl');
  list.className = 'upload-detail';
  uploadDetailRow(list, '文件名', upload.filename);
  uploadDetailRow(list, '上传 ID', upload.upload_id);
  uploadDetailRow(list, '任务 ID', upload.job_id);
  uploadDetailRow(list, '类型', uploadTypeLabel(upload) + '（' + uploadValueText(upload.media_type) + '）');
  uploadDetailRow(list, '状态', uploadStatusLabel(upload));
  uploadDetailRow(list, '复核状态', reviewStateLabel(upload.review_state));
  uploadDetailRow(list, '创建时间', upload.created_at);
  uploadDetailRow(list, '块数', uploadCountText(upload.block_count));
  uploadDetailRow(list, '定位数', uploadCountText(upload.locator_count));
  uploadDetailRow(list, '缺口（unsupported_features）', uploadGapText(upload.unsupported_features));
  uploadDetailRow(list, '向量索引（vector_index）', uploadVectorText(upload.vector_index));
  uploadDetailRow(list, '问答索引（searchable / index_state）', uploadIndexText(upload));
  if (upload.error_code) {
    uploadDetailRow(list, '失败原因',
      upload.error_code + '：' + uploadErrorMessage(upload.error_code, upload.http_status));
  } else if (upload.status === 'failed' || upload.status === 'partial_failure') {
    uploadDetailRow(list, '失败原因', '接口未返回 error_code');
  }
  card.append(list);

  renderUploadAnalysis(card, upload.analysis);

  if (uploadNeedsReview(upload)) {
    card.append(uploadApproveButton(upload, approveUpload));
  } else if (upload.status === 'succeeded') {
    if (uploadSearchable(upload)) {
      appendText(card, '已复核：该文件可进入可检索文档集合（searchable=true）。', 'field-hint');
    } else if (upload.index_state === 'no_chunks') {
      appendText(card, '已复核，但尚未建立问答索引，暂不可用于问答。', 'field-hint upload-index-blocked');
    } else {
      appendText(card, '已复核：该文件可进入可检索文档集合，但问答索引状态未知（接口未返回 ' +
        'index_state），是否可问答未确认。', 'field-hint upload-index-blocked');
    }
  }

  var link = uploadContractLink(upload);
  if (link) { card.append(link); }

  var close = document.createElement('button');
  close.type = 'button';
  close.className = 'secondary';
  close.textContent = '收起详情';
  close.addEventListener('click', function() {
    MULTIFILE.uploadId = null;
    MULTIFILE.uploadDetail = null;
    $('fileDetail').replaceChildren();
  });
  card.append(close);
  area.append(card);
  renderMultifileAuth();
}

async function selectUpload(id) {
  if (!id || !hasRole('admin') || fileBusy()) { return; }
  var token = MULTIFILE.token;
  var actor = AUTH.user.user_id;
  var csrf = AUTH.csrf;
  // 详情读取独立占用 detailToken：旧的详情读取不得覆盖新的选择。
  var read = ++MULTIFILE.detailToken;
  MULTIFILE.uploadId = id;
  try {
    var response = await apiFetch('/v1/uploads/' + encodeURIComponent(id));
    var data = await uploadJson(response);
    if (!fileActor(token, actor, csrf) || read !== MULTIFILE.detailToken || MULTIFILE.uploadId !== id) {
      return;
    }
    if (!response.ok) {
      throw new Error(uploadErrorMessage(uploadErrorCode(data), response.status));
    }
    MULTIFILE.uploadDetail = data;
    renderUploadDetail();
  } catch (error) {
    if (fileActor(token, actor, csrf) && read === MULTIFILE.detailToken && MULTIFILE.uploadId === id) {
      $('fileMessage').textContent = error.message;
    }
  }
}

/* ---------------- 复核门 ---------------- */

async function approveUpload(id) {
  if (!id || !hasRole('admin') || fileBusy()) { return; }
  var token = MULTIFILE.token;
  var actor = AUTH.user.user_id;
  var csrf = AUTH.csrf;
  var read = ++MULTIFILE.detailToken;
  MULTIFILE.approving = id;
  renderMultifileAuth();
  try {
    var response = await apiFetch('/v1/uploads/' + encodeURIComponent(id) + '/approve', {method: 'POST'});
    var data = await uploadJson(response);
    if (!fileActor(token, actor, csrf)) { return; }
    if (!response.ok) {
      throw new Error(uploadErrorMessage(uploadErrorCode(data), response.status));
    }
    // 冻结接口：approve 的 indexed 只有在真正可检索时才为 true；为 false 时给
    // index_state/index_note。复核通过不等于索引就绪，绝不据此宣称可问答。
    var indexed = (data && typeof data.indexed === 'boolean') ? data.indexed : null;
    var markApproved = function(target) {
      target.review_state = 'approved';
      if (indexed === true) {
        target.searchable = true;
        target.index_state = 'indexed';
      } else if (indexed === false) {
        target.searchable = false;
        target.index_state = (data && data.index_state) || 'no_chunks';
        if (data && data.index_note) { target.index_note = data.index_note; }
      }
    };
    MULTIFILE.results.forEach(function(result) {
      if (result.upload_id === id) { markApproved(result); }
    });
    if (MULTIFILE.uploadDetail && MULTIFILE.uploadDetail.upload_id === id) {
      markApproved(MULTIFILE.uploadDetail);
    }
    MULTIFILE.uploads.forEach(function(upload) {
      if (upload.upload_id === id) { markApproved(upload); }
    });
    // 规范未定义 approve 的响应体：若返回的就是详情载荷就顺带采用，否则只保留上面的
    // 乐观标记，随后仍会用真实读取结果覆盖。
    if (data && data.upload_id === id) {
      if (MULTIFILE.uploadDetail && MULTIFILE.uploadDetail.upload_id === id) {
        applyUploadFields(MULTIFILE.uploadDetail, data);
      }
      MULTIFILE.uploads.forEach(function(upload) {
        if (upload.upload_id === id) { applyUploadFields(upload, data); }
      });
      MULTIFILE.results.forEach(function(result) {
        if (result.upload_id === id) { applyUploadFields(result, data); }
      });
    }
    MULTIFILE.approving = null;
    renderMultifileAuth();
    if (read === MULTIFILE.detailToken) {
      renderUploadResults();
      renderUploadList();
      renderUploadDetail();
    }
    if (indexed === false) {
      var blockedLabel = uploadIndexStateLabel((data && data.index_state) || 'no_chunks') ||
        QUESTION_BLOCKED_NOTE;
      $('fileMessage').textContent = '已复核，但尚未建立问答索引，暂不可用于问答（' +
        blockedLabel + '）；该文件不会作为可选问答文件。';
    } else {
      $('fileMessage').textContent = '复核已通过：该文件才会进入可检索文档集合。';
    }
    await loadUploads();
    if (MULTIFILE.uploadId === id) { await selectUpload(id); }
  } catch (error) {
    if (fileActor(token, actor, csrf)) {
      $('fileMessage').textContent = error.message;
    }
  } finally {
    if (MULTIFILE.approving === id) {
      MULTIFILE.approving = null;
      renderMultifileAuth();
    }
  }
}

/* ---------------- 限定问答文件 ---------------- */

/* 冻结接口：GET /v1/documents 的每一项新增 searchable。只有 searchable===true 的文件
 * 才真的能参与问答检索（有切片），必须只把这类文件列为可勾选；其余列出但不可选，
 * 并明确写出"暂不可问答"的原因，不能静默消失、也不能混进可选项。 */
function questionDocumentSearchable(doc) {
  return !!(doc && doc.searchable === true);
}

function questionDocumentLine(doc) {
  var title = (doc && (doc.title || doc.document_id)) || '未知文件';
  var version = (doc && doc.doc_version !== undefined && doc.doc_version !== null) ?
    doc.doc_version : '未知';
  return title + ' · v' + version;
}

function appendQuestionHint(area, text, className) {
  var hint = document.createElement('p');
  hint.className = className || 'field-hint';
  hint.textContent = text;
  area.append(hint);
  return hint;
}

function appendQuestionCheckbox(area, doc) {
  var label = document.createElement('label');
  label.className = 'multifile-document multifile-document-searchable';
  label.dataset.documentId = doc.document_id;
  var check = document.createElement('input');
  check.type = 'checkbox';
  check.value = doc.document_id;
  check.addEventListener('change', function() {
    var count = document.querySelectorAll('#questionDocuments input:checked').length;
    if (count > 8) {
      check.checked = false;
      $('questionDocumentsMessage').textContent = '最多选择8份文件。';
    } else {
      $('questionDocumentsMessage').textContent = '已选择 ' + count + ' 份；每份保留版本与出处。';
    }
  });
  label.append(check, document.createTextNode(questionDocumentLine(doc)));
  area.append(label);
  return label;
}

function appendQuestionBlocked(area, doc) {
  var row = document.createElement('p');
  row.className = 'multifile-document-blocked';
  row.dataset.documentId = doc.document_id;
  row.textContent = questionDocumentLine(doc) + ' · ' + QUESTION_BLOCKED_NOTE;
  area.append(row);
  return row;
}

function renderQuestionDocuments(docs) {
  var area = $('questionDocuments');
  area.replaceChildren();
  var searchable = docs.filter(questionDocumentSearchable);
  var blocked = docs.filter(function(doc) { return !questionDocumentSearchable(doc); });

  appendQuestionHint(area, QUESTION_SEARCHABLE_HEADING, 'multifile-documents-heading');
  if (searchable.length) {
    searchable.forEach(function(doc) { appendQuestionCheckbox(area, doc); });
  } else {
    appendQuestionHint(area, '当前没有可直接参与问答检索的文件。', 'field-hint');
  }
  if (blocked.length) {
    appendQuestionHint(area, QUESTION_BLOCKED_HEADING,
      'multifile-documents-heading multifile-documents-heading-blocked');
    blocked.forEach(function(doc) { appendQuestionBlocked(area, doc); });
  }
  return {searchable: searchable.length, blocked: blocked.length};
}

async function loadQuestionDocuments() {
  var token = ++MULTIFILE.docToken;
  $('questionDocuments').textContent = '正在读取可用文件…';
  try {
    var data = await uploadJson(await apiFetch('/v1/documents'));
    if (token !== MULTIFILE.docToken) { return; }
    if (!Array.isArray(data.documents)) {
      // 载荷缺失不能被读成"没有可问答的文件"。
      $('questionDocuments').textContent =
        '文件列表返回了预期之外的载荷（缺少 documents 数组），请查看后端日志。';
      return;
    }
    MULTIFILE.docs = data.documents;
    var counts = renderQuestionDocuments(MULTIFILE.docs);
    $('questionDocumentsMessage').textContent = '可问答 ' + counts.searchable + ' 份，暂不可问答 ' +
      counts.blocked + ' 份；只有 searchable=true 的文件可勾选。不勾选时沿用问题范围；勾选后只使用所选文件，' +
      '跨文件同名对象不自动关联。';
  } catch (error) {
    if (token === MULTIFILE.docToken) { $('questionDocuments').textContent = error.message; }
  }
}

function applyDocumentSelection(request) {
  var selected = Array.from(document.querySelectorAll('#questionDocuments input:checked')).map(function(check) {
    return MULTIFILE.docs.find(function(doc) { return doc.document_id === check.value; });
  });
  if (!selected.length) { return; }
  if (selected.length > 8 || selected.some(function(doc) { return !doc; })) {
    throw new Error('请刷新文件列表，选择最多8份文件。');
  }
  // 纵深防御：即使有人绕过渲染把暂不可问答的文件塞进勾选，也不得进入检索范围。
  if (selected.some(function(doc) { return !questionDocumentSearchable(doc); })) {
    throw new Error('所选文件尚未建立问答索引（暂不可问答），请刷新文件列表后重新选择。');
  }
  request.mode = 'rag';
  request.scope = {
    document_collections: Array.from(new Set(selected.map(function(doc) { return doc.collection; }))),
    document_refs: selected.map(function(doc) {
      return {
        document_id: doc.document_id, document_version: doc.doc_version,
        source_sha256: doc.sha256, parser_version: doc.parser_version
      };
    })
  };
}

function clearQuestionSelection() {
  document.querySelectorAll('#questionDocuments input').forEach(function(check) { check.checked = false; });
  $('questionDocumentsMessage').textContent = '已清空文件选择，请按新的问题范围重新勾选。';
}

$('fileBatchForm').addEventListener('submit', submitFileBatch);
$('refreshFiles').addEventListener('click', loadUploads);
$('uploadStatusFilter').addEventListener('change', renderUploadList);
$('uploadTypeFilter').addEventListener('change', renderUploadList);
$('refreshQuestionDocuments').addEventListener('click', loadQuestionDocuments);
$('source').addEventListener('change', clearQuestionSelection);
document.querySelectorAll('.chip').forEach(function(button) {
  button.addEventListener('click', clearQuestionSelection);
});
window.addEventListener('hashchange', function() {
  if (location.hash === '#files') { loadUploads(); }
});
renderMultifileAuth();
loadQuestionDocuments();
if (location.hash === '#files') { loadUploads(); }
