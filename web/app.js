'use strict';
var $ = function (id) { return document.getElementById(id); };
var EVENT_NAMES = {
  'run.started': '开始运行', 'query.parsed': '理解问题', 'plan.created': '创建计划',
  'tool.started': '调用数据工具', 'sql.validated': 'SQL 校验通过', 'sql.executed': '只读查询完成',
  'retrieval.completed': '文档检索完成', 'answer.abstained': '证据不足，已拒答',
  'evidence.attached': '证据已关联', 'verification.passed': '结果验证通过',
  'verification.failed': '验证未通过', 'answer.completed': '回答完成',
  'answer.clarification': '需要澄清', 'run.failed': '运行失败'
  , 'requirements.loaded': '工艺字段已读取', 'verification.completed': '逐测量核验完成'
};
var PROTOCOL_LABELS = { openai: 'OpenAI 兼容', anthropic: 'Anthropic' };
var PRESETS = [
  { name: 'DeepSeek 官方', protocol: 'openai', base: 'https://api.deepseek.com/v1', model: 'deepseek-chat' },
  { name: 'Anthropic 官方', protocol: 'anthropic', base: 'https://api.anthropic.com', model: 'claude-sonnet-4-5', auth: 'x-api-key' },
  { name: 'Claude 中转', protocol: 'anthropic', base: '', model: 'claude-sonnet-4-5', auth: 'bearer' },
  { name: '阿里云百炼', protocol: 'openai', base: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen-plus' },
  { name: '智谱 AI', protocol: 'openai', base: 'https://open.bigmodel.cn/api/paas/v4', model: 'glm-4-plus' },
  { name: '月之暗面', protocol: 'openai', base: 'https://api.moonshot.cn/v1', model: 'moonshot-v1-8k' },
  { name: '硅基流动', protocol: 'openai', base: 'https://api.siliconflow.cn/v1', model: 'deepseek-ai/DeepSeek-V3' },
  { name: '火山方舟', protocol: 'openai', base: 'https://ark.cn-beijing.volces.com/api/v3', model: '' },
  { name: '本地网关 4000', protocol: 'openai', base: 'http://localhost:4000/v1', model: 'deepseek-flash' },
  { name: '自定义', protocol: 'openai', base: '', model: '' }
];
var STATE = { profiles: [], active: null, editingId: null };

function view(name, fromHash) {
  document.querySelectorAll('.view').forEach(function (section) {
    section.classList.toggle('active', section.dataset.view === name);
  });
  document.querySelectorAll('.nav-item').forEach(function (item) {
    item.classList.toggle('active', item.dataset.view === name);
  });
  localStorage.setItem('datamind_view', name);
  if (!fromHash && location.hash.slice(1) !== name) { location.hash = name; }
  if (name === 'runs') { loadRuns(); }
  if (name === 'model') { loadProfiles(); }
  if (name === 'fields') { loadDocuments(); }
}
window.addEventListener('hashchange', function () {
  var name = location.hash.slice(1);
  if (name) { view(name, true); }
});

/* ---------------- 问数 ---------------- */
function status(text, kind) {
  var el = $('status'); el.textContent = text; el.className = 'status ' + (kind || '');
}
function clearRun() {
  status('正在运行', 'wait'); $('answer').textContent = '正在规划、检索和核验…';
  $('trace').replaceChildren(); $('sql').textContent = '—'; $('result').textContent = '';
  $('meta').textContent = '等待查询'; $('confidence').textContent = '';
  $('citations').replaceChildren(); $('knowledgeMeta').textContent = '';
  $('documentEvidence').classList.add('hidden');
  $('industrialEvidence').classList.add('hidden'); $('industrialRows').replaceChildren();
  $('sqlContract').classList.add('hidden'); $('sqlContract').textContent = '';
  $('sqlEvidence').classList.remove('hidden'); $('sqlResults').classList.remove('hidden');
}
function addEvent(evt) {
  $('runid').textContent = evt.run_id;
  $('replayId').value = evt.run_id;
  localStorage.setItem('datamind_last_run', evt.run_id);
  var li = document.createElement('li'), head = document.createElement('div'), detail = document.createElement('div');
  head.className = 'event'; head.textContent = EVENT_NAMES[evt.event_type] || evt.event_type;
  detail.className = 'detail';
  var out = evt.output_summary || {};
  detail.textContent = [evt.agent_name, evt.error_code || '',
    out.row_count !== undefined ? out.row_count + ' 行' : '',
    out.elapsed_ms !== undefined ? out.elapsed_ms + ' ms' : ''].filter(Boolean).join(' · ');
  li.append(head, detail); $('trace').append(li); $('trace').scrollTop = $('trace').scrollHeight;
  if (evt.event_type === 'answer.completed' || evt.event_type === 'run.failed' || evt.event_type === 'answer.clarification' || evt.event_type === 'answer.abstained') {
    showFinal(out);
  }
}
function showFinal(out) {
  if (!out) { return; }
  var ok = out.status === 'COMPLETED';
  status(ok ? '已完成' : (out.status === 'ABSTAINED' ? '证据不足，已拒答' : out.status === 'NEEDS_CLARIFICATION' ? '需要澄清' : '执行失败'), ok ? '' : 'fail');
  $('answer').textContent = out.answer || '未返回答案';
  $('confidence').textContent = out.confidence_level ? '可信级别：' + out.confidence_level : '';
  var configIssues = ['MODEL_KEY_MISSING', 'MODEL_AUTH_FAILED', 'MODEL_BASE_URL_MISSING', 'MODEL_NAME_MISSING'];
  if (out.error_code && configIssues.indexOf(out.error_code) >= 0) {
    view('model'); loadProfiles();
    notice('bad', '问数失败原因在模型接入侧：' + (out.answer || out.error_code));
  }
  if (out.sql_result) { showResult(out.sql_result); }
  if (out.rag_result) { showDocumentResult(out.rag_result); }
  if (out.industrial_result) { showIndustrialResult(out.industrial_result); }
}
function showDocumentResult(data) {
  $('documentEvidence').classList.remove('hidden');
  $('sqlEvidence').classList.add('hidden'); $('sqlResults').classList.add('hidden');
  var retrieval = data.retrieval || {};
  var notes = [retrieval.snapshot_id || '', retrieval.mode === 'hybrid' ? '文字与向量检索' : '文字检索'];
  if ((retrieval.warnings || []).length) { notes.push('向量检索暂不可用，本次使用文字检索'); }
  if ((data.claims || []).length) { notes.push('原文与数字已核对；语义支持仍需复核'); }
  $('knowledgeMeta').textContent = notes.filter(Boolean).join(' · ');
  var area = $('citations'); area.replaceChildren();
  var sources = (data.citations || []).length ? data.citations : data.retrieved_evidence || [];
  if (!sources.length) { area.textContent = '当前范围内没有可用文档证据。'; return; }
  sources.forEach(function (source) {
    var card = document.createElement('article'), heading = document.createElement('h3');
    card.className = 'citation';
    heading.textContent = (source.number ? '[' + source.number + '] ' : '检索候选 · ') + source.title;
    var meta = document.createElement('div'); meta.className = 'mini';
    meta.textContent = '第 ' + source.page_start + (source.page_end !== source.page_start ? '–' + source.page_end : '') + ' 页 · 文档版本 ' + source.document_version + ' · ' + (source.section_path || []).join(' / ');
    var quote = document.createElement('blockquote'); quote.textContent = source.quote || '该文档已被检索到，尚未用于支持答案。';
    var links = document.createElement('div'); links.className = 'citation-links';
    var fileLink = document.createElement('a'); fileLink.href = source.source_url + (source.page_url ? '#page=' + source.page_start : '');
    fileLink.target = '_blank'; fileLink.rel = 'noopener'; fileLink.textContent = '查看原文件'; links.append(fileLink);
    if (source.page_url) {
      var pageLink = document.createElement('a'); pageLink.href = source.page_url; pageLink.target = '_blank'; pageLink.rel = 'noopener';
      pageLink.textContent = '查看第 ' + source.page_start + ' 页'; links.append(pageLink);
    }
    card.append(heading, meta, quote, links);
    if (source.origin === 'scan' || source.origin === 'ohrbench') {
      var quality = document.createElement('p'); quality.className = 'field-hint';
      var partial = ((source.quality_detail || {}).pages || []).some(function (page) {
        return (page.warnings || []).includes('TABLE_STRUCTURE_NOT_RECOVERED');
      });
      quality.textContent = source.origin === 'scan' ? (source.quality_detail && source.quality_detail.ocr_model ?
        (partial ? '扫描件经本地文字识别；部分表头或单元格未恢复，请核对原页。' : '扫描表格已按单元格识别，尚未人工复核，请核对原页。') :
        '历史记录：扫描件文本来自原生对照版。') : '此样本引用 OHR-Bench 人工转录，页码对应原始文档；原文件链接提供转录数据。';
      card.append(quality);
    }
    if (((source.quality_detail || {}).fact_extraction || {}).fact_count > 0) {
      var fieldButton = document.createElement('button'); fieldButton.className = 'secondary';
      fieldButton.textContent = '查看识别字段';
      fieldButton.addEventListener('click', function () { openDocumentFields(source); }); links.append(fieldButton);
    }
    if ((source.source_blocks || []).some(function (block) { return block.formula_latex; })) {
      var formulaHint = document.createElement('p'); formulaHint.className = 'field-hint';
      formulaHint.textContent = '公式由图片识别，尚未人工复核，请核对原页。'; card.append(formulaHint);
    }
    area.append(card);
  });
}
async function openDocumentFields(source) {
  var dialog = document.createElement('dialog'); dialog.className = 'document-fields';
  var close = document.createElement('button'); close.className = 'secondary'; close.textContent = '关闭';
  close.addEventListener('click', function () { dialog.close(); });
  dialog.addEventListener('close', function () { dialog.remove(); });
  var title = document.createElement('h3'); title.textContent = source.title + ' · 字段复核';
  var hint = document.createElement('p'); hint.className = 'field-hint';
  hint.textContent = source.collection === 'industrial_demo' ? '自建合成原文。字段提取待复核；审批、版次和批次仅表示原文声明。' : '识别结果待复核。批次、有效期及产品编号未确认；空单位表示原文未明确提供。';
  var content = document.createElement('div'); content.textContent = '正在读取…';
  var reviewHint = document.createElement('p'); reviewHint.className = 'field-hint';
  reviewHint.textContent = '这里只复核字段是否忠于原文。姓名为自行填写，当前未接入身份认证；确认字段不会审批文档或裁定来源冲突。存疑、退回的字段将从后续工业规则核验中排除，原文和历史运行保留。';
  var form = document.createElement('div'); form.className = 'review-form';
  var reviewer = document.createElement('input'); reviewer.placeholder = '复核人（自行填写）'; reviewer.maxLength = 120; reviewer.setAttribute('aria-label', '复核人');
  var reason = document.createElement('textarea'); reason.placeholder = '核对原页后填写依据或问题'; reason.maxLength = 1000; reason.setAttribute('aria-label', '复核依据');
  form.append(reviewer, reason);
  dialog.append(close, title, hint, reviewHint, form, content); document.body.append(dialog); dialog.showModal();
  try {
    var response = await fetch('/v1/documents/' + encodeURIComponent(source.document_id) + '/facts');
    if (!response.ok) { throw new Error('字段读取失败（' + response.status + '）'); }
    var data = await response.json();
    if (data.document_version !== source.document_version || data.source_sha256 !== source.document_sha256 || data.parser_version !== source.parser_version) {
      throw new Error('当前文档版本与此运行记录不同，请重新提问。');
    }
    content.replaceChildren();
    if (!data.facts.length) { content.textContent = '当前版本没有可定位的识别字段。'; return; }
    var table = document.createElement('table'), header = document.createElement('tr');
    ['实体 / 批次', '字段', '原文值', '标准化值', '复核记录', '出处 / 操作'].forEach(function (text) {
      var th = document.createElement('th'); th.textContent = text; header.append(th);
    });
    var head = document.createElement('thead'); head.append(header); table.append(head);
    var body = document.createElement('tbody'), preview = document.createElement('div'); preview.className = 'field-preview';
    var previewRow = document.createElement('tr'), previewCell = document.createElement('td'); previewCell.colSpan = 6; previewCell.append(preview); previewRow.append(previewCell);
    data.facts.forEach(function (fact) {
      var row = document.createElement('tr');
      var normalized = fact.normalized_range ? fact.normalized_range.min + '–' + fact.normalized_range.max : fact.normalized_value === null ? '未标准化' : fact.normalized_value;
      var entity = fact.entity_name + (fact.batch_id ? ' / ' + fact.batch_id : '');
      if (fact.extraction_version === 'industrial-native-v1') {
        entity += '\n工序 ' + fact.operation_no + (fact.measurement_id ? ' · ' + fact.measurement_id : '') + '\n版次 ' + fact.source_revision + '\n原文审批声明：' + ({approved:'已批准',unapproved:'未批准'}[fact.approval_status] || fact.approval_status);
      }
      [entity, fact.raw_header === 'holding_temperature' ? '保温温度' : fact.raw_header, fact.raw_value + (fact.extraction_version === 'industrial-native-v1' ? ' ' + (fact.raw_unit || '') : ''), normalized + (fact.normalized_unit === '1' ? '（比例）' : fact.normalized_unit || '')].forEach(function (text) {
        var cell = document.createElement('td'); cell.textContent = text; row.append(cell);
      });
      var status = document.createElement('td'), labels = {confirmed:'原文已核对',needs_review:'存疑',rejected:'已退回'};
      function showReview() {
        var latest = fact.latest_review;
        status.textContent = latest ? labels[latest.decision] + ' · ' + latest.reviewer_label + '\n' + latest.reason + '\n' + latest.created_at : '尚未复核';
      }
      showReview(); row.append(status);
      var cell = document.createElement('td'), button = document.createElement('button'); button.className = 'secondary'; button.textContent = '定位原页';
      button.addEventListener('click', function () { row.after(previewRow); locateDocumentFact(fact, preview); }); cell.append(button);
      var message = document.createElement('p'); message.className = 'field-hint'; message.setAttribute('role','status');
      var url = '/v1/documents/' + encodeURIComponent(source.document_id) + '/facts/' + encodeURIComponent(fact.fact_id) + '/reviews';
      var pending = null, controls = [];
      [['confirmed','确认原文'],['needs_review','标记存疑'],['rejected','退回字段']].forEach(function (item) {
        var action = document.createElement('button'); action.className = 'secondary'; action.textContent = item[1]; controls.push(action);
        action.addEventListener('click', async function () {
          if (!reviewer.value.trim() || !reason.value.trim()) { message.textContent = '请填写复核人和依据，并先核对原页。'; return; }
          var payload = {document_version:data.document_version,source_sha256:data.source_sha256,parser_version:data.parser_version,
            payload_sha256:fact.payload_sha256,previous_review_id:fact.latest_review ? fact.latest_review.review_id : null,
            decision:item[0],reviewer_label:reviewer.value.trim(),reason:reason.value.trim()};
          var signature = JSON.stringify(payload);
          if (!pending || pending.signature !== signature) { pending = {signature:signature,request_id:crypto.randomUUID()}; }
          payload.request_id = pending.request_id; controls.forEach(function (b) { b.disabled = true; }); message.textContent = '正在保存…';
          try {
            var response = await fetch(url, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
            if (!response.ok) { throw new Error(response.status === 409 ? '字段版本或复核记录已变化，请关闭后重新打开。' : '保存失败（' + response.status + '）'); }
            fact.latest_review = (await response.json()).review; pending = null; showReview(); message.textContent = '已保存复核记录。';
          } catch (error) { message.textContent = error.message; }
          finally { controls.forEach(function (b) { b.disabled = false; }); }
        }); cell.append(action);
      });
      var history = document.createElement('button'); history.className = 'secondary'; history.textContent = '复核历史';
      history.addEventListener('click', async function () {
        try {
          var response = await fetch(url); if (!response.ok) { throw new Error('历史读取失败（' + response.status + '）'); }
          var records = (await response.json()).reviews;
          row.after(previewRow); preview.replaceChildren(); var heading = document.createElement('h4'); heading.textContent = fact.entity_name + ' · ' + fact.raw_header + ' · 复核历史';
          var detail = document.createElement('pre'); detail.className = 'review-history';
          detail.textContent = records.length ? records.map(function (r) { return r.created_at + ' · ' + labels[r.decision] + ' · ' + r.reviewer_label + '\n' + r.reason + '\n版本 ' + r.document_version + ' · ' + r.review_id; }).join('\n\n') : '暂无复核记录';
          preview.append(heading,detail);
        } catch (error) { message.textContent = error.message; }
      }); cell.append(history,message); row.append(cell); body.append(row);
    });
    table.append(body); content.append(table);
    var unresolved = ((data.quality || {}).unresolved || []).length;
    if (unresolved) { hint.textContent += ' 另有 ' + unresolved + ' 处字段或表头未能确认。'; }
  } catch (error) { content.textContent = error.message; }
}
function locateDocumentFact(fact, preview) {
  preview.replaceChildren();
  var refs = fact.evidence_refs, first = refs[0], label = document.createElement('p');
  label.textContent = '第 ' + first.page_no + ' 页 · 蓝：实体 · 橙：字段 · 红：值/范围 · 灰：上下文';
  var frame = document.createElement('div'); frame.className = 'field-page';
  var img = document.createElement('img'); img.src = '/v1/documents/' + encodeURIComponent(fact.document_id) + '/pages/' + first.page_no;
  img.alt = '原始文档页及字段定位';
  var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 ' + first.page_size.join(' ')); svg.setAttribute('aria-hidden', 'true');
  refs.forEach(function (ref) {
    var rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect'), box = ref.bbox;
    rect.setAttribute('x', box[0]); rect.setAttribute('y', box[1]); rect.setAttribute('width', box[2] - box[0]); rect.setAttribute('height', box[3] - box[1]);
    rect.setAttribute('fill', 'none'); rect.setAttribute('stroke', {entity: '#2563eb', header: '#d97706', value: '#dc2626', lower: '#dc2626', upper: '#dc2626'}[ref.role] || '#64748b'); rect.setAttribute('stroke-width', '1.5'); svg.append(rect);
  });
  frame.append(img, svg); preview.append(label, frame);
  label.scrollIntoView({block: 'nearest'});
}
function showIndustrialResult(data) {
  $('industrialEvidence').classList.remove('hidden'); $('sqlEvidence').classList.add('hidden'); $('sqlResults').classList.add('hidden');
  $('industrialSummary').textContent = data.scope.part_id + ' · 工序 ' + data.scope.operation_no + ' · ' + data.scope.month + ' · ' + data.summary.batch_count + ' 批次 / ' + data.summary.measurement_count + ' 测量 · 规则 ' + data.rule_version + ' · 固定查询与规则计算';
  var names = {within_range:'范围内',out_of_range:'超限',needs_review:'待复核',insufficient_evidence:'证据不足',consistent:'一致',resolved_equivalent:'单位换算后等价',unresolved:'数值冲突，未消解'};
  var reasons = {MULTIPLE_APPLICABLE_REVISIONS:'工艺有效期重叠',NO_APPLICABLE_REQUIREMENT:'没有适用工艺',UNKNOWN_UNIT:'单位未登记',UNIT_RANGE_OR_METHOD_UNRESOLVED:'规范口径或单位未确认',REPORT_MISSING_OR_AMBIGUOUS:'报告缺失或不唯一',REPORT_KEY_MISMATCH:'报告实体/批次不匹配',METHOD_TIME_OR_APPROVAL_MISMATCH:'测量口径或审批不匹配',NO_REGISTERED_AUTHORITY_RULE:'缺少裁定依据',VALUES_NOT_COMPARABLE:'值或单位不可比较',SELECTED_BY_BATCH_TIME:'按批次时间选适用版本'};
  var table=document.createElement('table'),head=document.createElement('thead'),header=document.createElement('tr');
  ['批次 / 测量','生产原值','适用工艺','工艺核验','报告原值','来源比较','说明 / 出处'].forEach(function (t) { var th=document.createElement('th');th.textContent=t;header.append(th); });head.append(header);table.append(head);
  var body=document.createElement('tbody'), facts={};data.facts.forEach(function(f){facts[f.fact_id]=f;});
  data.rows.forEach(function(row){
    var tr=document.createElement('tr');
    [row.batch_id+' / '+row.measurement_id,row.value+' '+row.unit,row.applicable_revision||'未确定',names[row.compliance],row.report_value===null?'缺失':row.report_value+' '+row.report_unit,names[row.agreement]].forEach(function(t){var td=document.createElement('td');td.textContent=t;tr.append(td);});
    if(row.compliance==='out_of_range'||row.agreement==='unresolved'){tr.className='industrial-conflict';}
    var cell=document.createElement('td'),note=document.createElement('div');note.className='field-hint';note.textContent=row.issues.map(function(i){return reasons[i.reason]||i.reason;}).join('；');cell.append(note);
    var refs=[{fid:row.requirement_fact_id,label:'工艺原页'},{fid:row.report_fact_id,label:'报告原页'}];
    if(!row.requirement_fact_id){row.issues.forEach(function(issue){(issue.fact_ids||[]).forEach(function(fid){if(facts[fid]){refs.push({fid:fid,label:'工艺 '+facts[fid].source_revision});}});});}
    refs.forEach(function(ref){
      var f=facts[ref.fid];if(!f){return;} var link=document.createElement('a');link.href=f.page_url;link.target='_blank';link.rel='noopener';link.textContent=ref.label;cell.append(link,document.createTextNode(' '));
      var button=document.createElement('button');button.className='secondary';button.textContent=ref.label+'定位';button.addEventListener('click',function(){
        var dialog=document.createElement('dialog');dialog.className='document-fields';var close=document.createElement('button');close.textContent='关闭';close.className='secondary';close.addEventListener('click',function(){dialog.close();});dialog.addEventListener('close',function(){dialog.remove();});var preview=document.createElement('div');dialog.append(close,preview);document.body.append(dialog);dialog.showModal();locateDocumentFact(f,preview);
      });cell.append(button);
    });tr.append(cell);body.append(tr);
  });table.append(body);$('industrialRows').replaceChildren(table);
  if (data.facts.length) {
    var first = data.facts[0], review = document.createElement('button'); review.className = 'secondary'; review.textContent = '查看字段 / 人工复核';
    review.addEventListener('click',function () { openDocumentFields({document_id:first.document_id,title:'工业工艺与质检报告',collection:'industrial_demo',document_version:first.document_version,document_sha256:first.source_sha256,parser_version:first.parser_version}); });
    $('industrialRows').prepend(review);
  }
}
function showResult(data) {
  $('sqlContract').textContent = data.semantic_contract || '';
  $('sqlContract').classList.toggle('hidden', !data.semantic_contract);
  $('sql').textContent = data.sql || '—';
  $('meta').textContent = ['快照 ' + (data.source_snapshot_id || '—'), data.row_count + ' 行',
    data.elapsed_ms !== undefined ? data.elapsed_ms + ' ms' : ''].filter(Boolean).join(' · ');
  var area = $('result'); area.replaceChildren();
  var rows = data.rows || [];
  if (!rows.length) { area.textContent = '未查到数据'; return; }
  var table = document.createElement('table'), thead = document.createElement('thead'), hr = document.createElement('tr');
  (data.columns || []).forEach(function (c) { var th = document.createElement('th'); th.textContent = c; hr.append(th); });
  thead.append(hr); table.append(thead);
  var tbody = document.createElement('tbody');
  rows.forEach(function (row) {
    var tr = document.createElement('tr');
    row.forEach(function (v) { var td = document.createElement('td'); td.textContent = v === null ? 'NULL' : String(v); tr.append(td); });
    tbody.append(tr);
  });
  table.append(tbody); area.append(table);
}
async function run() {
  var question = $('question').value.trim();
  if (!question) { return; }
  clearRun(); $('send').disabled = true;
  try {
    var selected = $('source').value;
    var request = { question: question, mode: 'auto' };
    if (selected === 'demo_kb' || selected === 'ohrbench_kb') {
      request.mode = 'rag'; request.scope = { document_collections: [selected] };
    } else if (selected === 'demo_industrial') {
      request.mode = 'industrial'; request.scope = {database_sources:['demo_industrial'], document_collections:['industrial_demo']};
    } else if (selected === 'industrial_sql') {
      request.mode = 'sql'; request.scope = {database_sources:['demo_industrial']};
    } else if (selected !== 'auto') {
      request.mode = 'sql'; request.scope = { database_sources: [selected] };
    }
    var response = await fetch('/v1/chat/stream', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request)
    });
    if (!response.ok) { throw new Error('HTTP ' + response.status); }
    var reader = response.body.getReader(), decoder = new TextDecoder(), buffer = '';
    while (true) {
      var chunk = await reader.read();
      if (chunk.done) { break; }
      buffer += decoder.decode(chunk.value, { stream: true });
      var boundary;
      while ((boundary = buffer.indexOf('\n\n')) >= 0) {
        var block = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2);
        var line = block.split('\n').find(function (s) { return s.indexOf('data: ') === 0; });
        if (line) { try { addEvent(JSON.parse(line.slice(6))); } catch (err) { console.error(err); } }
      }
    }
  } catch (err) {
    status('连接失败', 'fail'); $('answer').textContent = '无法连接 Agent API：' + err.message;
  } finally { $('send').disabled = false; }
}
async function replay() {
  var id = $('replayId').value.trim();
  if (!id) { return; }
  view('chat'); clearRun();
  try {
    var responses = await Promise.all([
      fetch('/v1/runs/' + encodeURIComponent(id)),
      fetch('/v1/runs/' + encodeURIComponent(id) + '/events'),
      fetch('/v1/runs/' + encodeURIComponent(id) + '/evidence')
    ]);
    if (!responses[0].ok) { throw new Error('找不到运行 ID'); }
    var run = await responses[0].json(), events = await responses[1].json(), evidence = await responses[2].json();
    $('question').value = run.question || '';
    events.events.forEach(addEvent);
    var finalEvent = events.events.slice().reverse().find(function (evt) {
      return ['answer.completed', 'answer.abstained', 'run.failed', 'answer.clarification'].indexOf(evt.event_type) >= 0;
    });
    var collections = finalEvent && finalEvent.output_summary.rag_result && finalEvent.output_summary.rag_result.retrieval.collections;
    var sqlResult = finalEvent && finalEvent.output_summary.sql_result;
    $('source').value = finalEvent && finalEvent.output_summary.industrial_result ? 'demo_industrial' : sqlResult && sqlResult.source_id === 'demo_industrial' ? 'industrial_sql' : collections && collections.length === 1 ? collections[0] : 'auto';
    showFinal(finalEvent ? finalEvent.output_summary : { status: run.status, answer: run.answer, confidence_level: run.confidence_level });
    var sqlNode = evidence.nodes.find(function (n) { return n.node_type === 'SQLQuery'; });
    var rowNode = evidence.nodes.find(function (n) { return n.node_type === 'SQLRow'; });
    if (sqlNode && rowNode) {
      var merged = {}; Object.assign(merged, sqlNode.payload, rowNode.payload); showResult(merged);
    }
  } catch (err) { status('读取失败', 'fail'); $('answer').textContent = err.message; }
}
async function diagnostic() {
  clearRun(); $('diagnostic').disabled = true;
  try {
    var response = await fetch('/v1/diagnostics/sql', { method: 'POST' });
    if (!response.ok) { throw new Error('HTTP ' + response.status); }
    var result = await response.json(); $('replayId').value = result.run_id; await replay();
  } catch (err) {
    status('自检失败', 'fail'); $('answer').textContent = '数据库链路自检失败：' + err.message;
  } finally { $('diagnostic').disabled = false; }
}
async function loadRuns() {
  var area = $('runList');
  try {
    var response = await fetch('/v1/runs?limit=20');
    if (!response.ok) { throw new Error('HTTP ' + response.status); }
    var data = await response.json(), runs = data.runs || [];
    area.replaceChildren();
    if (!runs.length) { area.textContent = '暂无运行记录'; return; }
    var table = document.createElement('table'), thead = document.createElement('thead'), hr = document.createElement('tr');
    ['运行 ID', '状态', '问题', '开始时间'].forEach(function (c) {
      var th = document.createElement('th'); th.textContent = c; hr.append(th);
    });
    thead.append(hr); table.append(thead);
    var tbody = document.createElement('tbody');
    runs.forEach(function (item) {
      var tr = document.createElement('tr');
      [item.run_id, item.status, item.question, String(item.started_at || '').slice(0, 19).replace('T', ' ')].forEach(function (v) {
        var td = document.createElement('td'); td.textContent = v === null ? '' : v; tr.append(td);
      });
      tr.style.cursor = 'pointer';
      tr.addEventListener('click', function () { $('replayId').value = item.run_id; replay(); });
      tbody.append(tr);
    });
    table.append(tbody); area.append(table);
  } catch (err) { area.textContent = '运行记录读取失败：' + err.message; }
}

async function loadDocuments() {
  var area = $('documentList'); area.textContent = '正在读取…';
  try {
    var response = await fetch('/v1/documents'); if (!response.ok) { throw new Error('HTTP ' + response.status); }
    var documents = (await response.json()).documents, table = document.createElement('table'), head = document.createElement('thead'), hr = document.createElement('tr');
    ['文档','知识库','版本','操作'].forEach(function (label) { var th=document.createElement('th'); th.textContent=label; hr.append(th); }); head.append(hr); table.append(head);
    var body = document.createElement('tbody');
    documents.forEach(function (doc) {
      var row = document.createElement('tr'); [doc.title,doc.collection,'v' + doc.doc_version].forEach(function (value) { var td=document.createElement('td');td.textContent=value;row.append(td); });
      var cell=document.createElement('td'), action=document.createElement('button');action.className='secondary';action.textContent='打开字段';
      action.addEventListener('click',function () { openDocumentFields({document_id:doc.document_id,title:doc.title,collection:doc.collection,document_version:doc.doc_version,document_sha256:doc.sha256,parser_version:doc.parser_version}); });
      var original=document.createElement('a');original.textContent='原文件';original.href='/v1/documents/'+encodeURIComponent(doc.document_id)+'/file';original.target='_blank';original.rel='noopener';cell.append(action,document.createTextNode(' '),original);row.append(cell);body.append(row);
    }); table.append(body);area.replaceChildren(table);
  } catch (error) { area.textContent = '文档读取失败：' + error.message; }
}
$('refreshDocuments').addEventListener('click', loadDocuments);

/* ---------------- 模型接入 ---------------- */
function notice(kind, text) {
  var box = $('modelNotice');
  box.className = 'notice ' + kind; box.textContent = text;
}
function protocolLabel(protocol) { return PROTOCOL_LABELS[protocol] || protocol; }
function renderPresets() {
  var box = $('presets'); box.replaceChildren();
  PRESETS.forEach(function (preset) {
    var button = document.createElement('button');
    button.type = 'button'; button.className = 'preset'; button.textContent = preset.name;
    button.addEventListener('click', function () { applyPreset(preset, button); });
    box.append(button);
  });
}
function applyPreset(preset, button) {
  document.querySelectorAll('.preset').forEach(function (b) { b.classList.remove('active'); });
  if (button) { button.classList.add('active'); }
  $('protocol').value = preset.protocol;
  $('baseUrl').value = preset.base;
  if (preset.model) { $('modelName').value = preset.model; }
  if (preset.auth) { $('authStyle').value = preset.auth; }
  if (!$('profileName').value.trim() && preset.name !== '自定义') { $('profileName').value = preset.name; }
  syncProtocolHint();
  notice('info', '已套用「' + preset.name + '」预设，填入 API 密钥后点「拉取模型列表」验证。');
}
function syncProtocolHint() {
  var anthropic = $('protocol').value === 'anthropic';
  $('baseHint').innerHTML = anthropic
    ? 'Anthropic Messages 协议：填 <code>https://api.anthropic.com</code> 即可，会自动请求 <code>/v1/messages</code>；中转站按其文档填写。'
    : 'OpenAI 兼容地址；填到 <code>/v1</code> 即可，粘贴完整端点也会自动截断。';
  $('authStyle').parentElement.style.opacity = anthropic ? '1' : '.45';
  $('authStyle').disabled = !anthropic;
}
function renderProfileList() {
  var box = $('profileList'); box.replaceChildren();
  if (!STATE.profiles.length) {
    var empty = document.createElement('p');
    empty.className = 'muted';
    empty.textContent = '还没有任何接入配置。右侧选一个预设、填入 API 密钥，点「保存并启用」即可开始问数。';
    box.append(empty); return;
  }
  STATE.profiles.forEach(function (profile) {
    var card = document.createElement('div');
    card.className = 'profile-card' + (profile.active ? ' active' : '');
    var head = document.createElement('div'); head.className = 'profile-head';
    var name = document.createElement('strong'); name.textContent = profile.name;
    var badge = document.createElement('span');
    badge.className = 'pill ' + (profile.active ? 'on' : '');
    badge.textContent = profile.active ? '启用中' : '未启用';
    var proto = document.createElement('span'); proto.className = 'pill';
    proto.textContent = protocolLabel(profile.protocol);
    head.append(name, proto, badge);
    var meta = document.createElement('div'); meta.className = 'profile-meta';
    meta.textContent = [profile.base_url, profile.model || '(未填模型)', profile.has_key ? profile.key_masked : '未配置密钥']
      .filter(Boolean).join('  ·  ');
    var actions = document.createElement('div'); actions.className = 'profile-actions';
    if (!profile.active) {
      actions.append(button('启用', 'ghost', function () { activateProfile(profile.id); }));
    }
    actions.append(button('编辑', 'ghost', function () { editProfile(profile); }));
    actions.append(button('测试', 'ghost', function () { probe('chat', profile.id); }));
    actions.append(button('删除', 'ghost danger-text', function () { removeProfile(profile); }));
    card.append(head, meta, actions);
    box.append(card);
  });
}
function button(label, className, handler) {
  var b = document.createElement('button');
  b.type = 'button'; b.className = className; b.textContent = label;
  b.addEventListener('click', handler);
  return b;
}
function renderActive() {
  var active = STATE.profiles.find(function (p) { return p.active; });
  $('cfgName').textContent = active ? active.name : '未配置';
  $('cfgProtocol').textContent = active ? protocolLabel(active.protocol) : '—';
  $('cfgBase').textContent = active ? active.base_url : '—';
  $('cfgModel').textContent = active ? (active.model || '—') : '—';
  $('cfgKey').textContent = active && active.has_key ? active.key_masked : '未配置';
  $('cfgUpdated').textContent = active && active.updated_at ? String(active.updated_at).slice(0, 19).replace('T', ' ') : '—';
  var ready = !!(active && active.has_key && active.model);
  $('navModelBadge').textContent = ready ? '已配置' : '未配置';
  $('pulseModel').className = 'pulse ' + (ready ? 'ok' : 'warn');
  $('pulseModelText').textContent = ready ? (active.name + ' · ' + active.model) : '未配置';
}
async function loadProfiles() {
  try {
    var response = await fetch('/v1/model/profiles');
    if (!response.ok) { throw new Error('HTTP ' + response.status); }
    var data = await response.json();
    STATE.profiles = data.items || [];
    STATE.active = data.active;
    renderProfileList(); renderActive();
  } catch (err) { notice('bad', '读取接入配置失败：' + err.message); }
}
function resetForm() {
  STATE.editingId = null;
  $('profileName').value = ''; $('baseUrl').value = ''; $('modelName').value = '';
  $('apiKey').value = ''; $('apiKey').placeholder = '留空表示保持当前密钥';
  $('maxTokens').value = ''; $('timeoutS').value = ''; $('extraHeaders').value = ''; $('proxy').value = '';
  $('protocol').value = 'openai'; $('authStyle').value = 'both';
  $('editorTitle').textContent = '新增配置';
  $('modelPicks').replaceChildren();
  document.querySelectorAll('.preset').forEach(function (b) { b.classList.remove('active'); });
  syncProtocolHint();
}
function editProfile(profile) {
  STATE.editingId = profile.id;
  $('profileName').value = profile.name || '';
  $('protocol').value = profile.protocol || 'openai';
  $('baseUrl').value = profile.base_url || '';
  $('modelName').value = profile.model || '';
  $('apiKey').value = '';
  $('apiKey').placeholder = profile.has_key ? ('已保存 ' + profile.key_masked + '，留空表示不修改') : '尚未配置密钥';
  $('authStyle').value = profile.auth_style || 'both';
  $('maxTokens').value = profile.max_tokens || '';
  $('timeoutS').value = profile.timeout_s || '';
  $('proxy').value = profile.proxy || '';
  $('extraHeaders').value = profile.headers && Object.keys(profile.headers).length ? JSON.stringify(profile.headers) : '';
  $('editorTitle').textContent = '编辑配置：' + profile.name;
  syncProtocolHint();
  notice('info', '正在编辑「' + profile.name + '」；保存后立即生效。');
  window.scrollTo({ top: 0, behavior: 'smooth' });
}
function formPayload() {
  var payload = {
    name: $('profileName').value.trim(),
    protocol: $('protocol').value,
    base_url: $('baseUrl').value.trim(),
    model: $('modelName').value.trim(),
    max_tokens: Number($('maxTokens').value || 0),
    timeout_s: Number($('timeoutS').value || 0),
    auth_style: $('authStyle').value,
    proxy: $('proxy').value.trim()
  };
  var key = $('apiKey').value.trim();
  if (key) { payload.api_key = key; }
  var headers = $('extraHeaders').value.trim();
  if (headers) {
    try { payload.headers = JSON.parse(headers); }
    catch (err) { throw new Error('额外请求头不是合法 JSON'); }
  }
  if (STATE.editingId) { payload.profile_id = STATE.editingId; }
  return payload;
}
function probePayload() {
  var payload = formPayload();
  return payload;
}
async function saveProfile(activate) {
  var payload;
  try { payload = formPayload(); }
  catch (err) { notice('bad', err.message); return; }
  if (!payload.name) { notice('bad', '请先填写配置名称。'); return; }
  $('saveProfile').disabled = true; $('saveActivate').disabled = true;
  try {
    var editing = STATE.editingId;
    var response = await fetch(editing ? '/v1/model/profiles/' + encodeURIComponent(editing) : '/v1/model/profiles', {
      method: editing ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    var data = await response.json();
    if (!response.ok) { throw new Error((data.detail && data.detail.error_code) || ('HTTP ' + response.status)); }
    if (activate || !STATE.active) {
      await fetch('/v1/model/profiles/' + encodeURIComponent(data.id) + '/activate', { method: 'POST' });
    }
    await loadProfiles();
    STATE.editingId = data.id;
    $('editorTitle').textContent = '编辑配置：' + data.name;
    $('apiKey').value = '';
    $('apiKey').placeholder = data.has_key ? ('已保存 ' + data.key_masked + '，留空表示不修改') : '尚未配置密钥';
    notice('ok', '配置「' + data.name + '」已保存' + (activate || !STATE.active ? '并启用' : '') + '，密钥 ' + (data.key_masked || '未设置') + '。');
  } catch (err) { notice('bad', '保存失败：' + err.message); }
  finally { $('saveProfile').disabled = false; $('saveActivate').disabled = false; }
}
async function activateProfile(id) {
  try {
    await fetch('/v1/model/profiles/' + encodeURIComponent(id) + '/activate', { method: 'POST' });
    await loadProfiles();
    notice('ok', '已切换启用的接入配置，后续问数立即使用它。');
  } catch (err) { notice('bad', '切换失败：' + err.message); }
}
async function removeProfile(profile) {
  if (!window.confirm('确认删除接入配置「' + profile.name + '」？')) { return; }
  try {
    await fetch('/v1/model/profiles/' + encodeURIComponent(profile.id), { method: 'DELETE' });
    if (STATE.editingId === profile.id) { resetForm(); }
    await loadProfiles();
    notice('info', '配置已删除。');
  } catch (err) { notice('bad', '删除失败：' + err.message); }
}
function renderProbe(result, title) {
  var box = $('probeResult');
  if (!result.ok) {
    box.className = 'notice bad';
    box.textContent = title + '失败：' + (result.message || result.error_code) + '（' + result.error_code + '）';
    return;
  }
  if (result.models) {
    box.className = 'notice ok';
    box.textContent = title + '成功：' + protocolLabel(result.protocol) + ' 共 ' + result.models.length + ' 个模型。';
    showModels(result.models);
  } else {
    box.className = 'notice ok';
    box.textContent = title + '成功：' + protocolLabel(result.protocol) + ' 模型 ' + result.model +
      ' 返回「' + result.reply + '」，用时 ' + result.elapsed_ms + ' ms。';
  }
}
function showModels(models) {
  var box = $('modelPicks'), options = $('modelOptions');
  box.replaceChildren(); options.replaceChildren();
  models.slice(0, 60).forEach(function (name) {
    var option = document.createElement('option'); option.value = name; options.append(option);
    var chip = document.createElement('button');
    chip.type = 'button'; chip.className = 'model-pick'; chip.textContent = name;
    chip.addEventListener('click', function () { $('modelName').value = name; });
    box.append(chip);
  });
}
async function probe(kind, profileId) {
  var payload;
  try { payload = kind === 'chat' ? probePayload() : formPayload(); }
  catch (err) { notice('bad', err.message); return; }
  if (profileId) { payload.profile_id = profileId; delete payload.profile_id_override; }
  var buttonId = kind === 'chat' ? 'testConfig' : 'fetchModels';
  $(buttonId).disabled = true;
  try {
    var response = await fetch(kind === 'chat' ? '/v1/model/config/test' : '/v1/model/config/models', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
    });
    renderProbe(await response.json(), kind === 'chat' ? '测试连接' : '拉取模型列表');
  } catch (err) { notice('bad', '探测失败：' + err.message); }
  finally { $(buttonId).disabled = false; }
}
async function exportProfiles() {
  try {
    var response = await fetch('/v1/model/profiles/export');
    var data = await response.json();
    var blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    var link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = 'datamind-profiles.json';
    document.body.append(link); link.click(); link.remove();
    notice('ok', '已导出 ' + (data.profiles || []).length + ' 套配置（不含密钥，避免明文外泄）。');
  } catch (err) { notice('bad', '导出失败：' + err.message); }
}
async function importProfiles(file) {
  try {
    var text = await file.text();
    var data = JSON.parse(text);
    var profiles = Array.isArray(data) ? data : (data.profiles || []);
    var response = await fetch('/v1/model/profiles/import', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ profiles: profiles, activate_first: false })
    });
    var result = await response.json();
    await loadProfiles();
    notice('ok', '已导入 ' + (result.imported || 0) + ' 套配置；导入的配置不含密钥，需要重新填写 API 密钥。');
  } catch (err) { notice('bad', '导入失败：' + err.message); }
}
function toggleKey() {
  var input = $('apiKey');
  var hidden = input.type === 'password';
  input.type = hidden ? 'text' : 'password';
  $('toggleKey').textContent = hidden ? '隐藏' : '显示';
}
async function refreshPulses() {
  try {
    var response = await fetch('/health');
    var data = await response.json();
    $('pulseDb').className = 'pulse ' + (data.database === 'ready' ? 'ok' : 'bad');
    $('pulseDbText').textContent = data.database === 'ready' ? '正常' : '异常';
  } catch (err) {
    $('pulseDb').className = 'pulse bad'; $('pulseDbText').textContent = '不可用';
  }
}

/* ---------------- 绑定 ---------------- */
document.querySelectorAll('.nav-item').forEach(function (item) {
  item.addEventListener('click', function () { view(item.dataset.view); });
});
$('send').addEventListener('click', run);
$('replay').addEventListener('click', replay);
$('diagnostic').addEventListener('click', diagnostic);
$('saveProfile').addEventListener('click', function () { saveProfile(false); });
$('saveActivate').addEventListener('click', function () { saveProfile(true); });
$('newProfile').addEventListener('click', resetForm);
$('resetForm').addEventListener('click', resetForm);
$('testConfig').addEventListener('click', function () { probe('chat'); });
$('fetchModels').addEventListener('click', function () { probe('models'); });
$('toggleKey').addEventListener('click', toggleKey);
$('protocol').addEventListener('change', syncProtocolHint);
$('exportProfiles').addEventListener('click', exportProfiles);
$('importProfiles').addEventListener('click', function () { $('importFile').click(); });
$('importFile').addEventListener('change', function (event) {
  var file = event.target.files && event.target.files[0];
  if (file) { importProfiles(file); }
  event.target.value = '';
});
document.querySelectorAll('.chip').forEach(function (b) {
  b.addEventListener('click', function () { $('question').value = b.dataset.q; $('source').value = b.dataset.source || 'auto'; });
});
$('question').addEventListener('keydown', function (e) {
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { run(); }
});
renderPresets();
syncProtocolHint();
loadProfiles();
refreshPulses();
setInterval(refreshPulses, 30000);
view(location.hash.slice(1) || localStorage.getItem('datamind_view') || 'chat', true);
var lastRun = localStorage.getItem('datamind_last_run');
if (lastRun) { $('replayId').value = lastRun; }
