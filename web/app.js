'use strict';
var $ = function (id) { return document.getElementById(id); };
var EVENT_NAMES = {
  'run.started': '开始运行', 'query.parsed': '理解问题', 'plan.created': '创建计划',
  'tool.started': '调用 SQL Agent', 'sql.validated': 'SQL 校验通过', 'sql.executed': '只读查询完成',
  'evidence.attached': '证据已关联', 'verification.passed': '结果验证通过',
  'verification.failed': '验证未通过', 'answer.completed': '回答完成',
  'answer.clarification': '需要澄清', 'run.failed': '运行失败'
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
  status('正在运行', 'wait'); $('answer').textContent = '正在规划和执行查询…';
  $('trace').replaceChildren(); $('sql').textContent = '—'; $('result').textContent = '';
  $('meta').textContent = '等待查询'; $('confidence').textContent = '';
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
  if (evt.event_type === 'answer.completed' || evt.event_type === 'run.failed' || evt.event_type === 'answer.clarification') {
    showFinal(out);
  }
}
function showFinal(out) {
  if (!out) { return; }
  var ok = out.status === 'COMPLETED';
  status(ok ? '已完成' : (out.status === 'NEEDS_CLARIFICATION' ? '需要澄清' : '执行失败'), ok ? '' : 'fail');
  $('answer').textContent = out.answer || '未返回答案';
  $('confidence').textContent = out.confidence_level ? '可信级别：' + out.confidence_level : '';
  var configIssues = ['MODEL_KEY_MISSING', 'MODEL_AUTH_FAILED', 'MODEL_BASE_URL_MISSING', 'MODEL_NAME_MISSING'];
  if (out.error_code && configIssues.indexOf(out.error_code) >= 0) {
    view('model'); loadProfiles();
    notice('bad', '问数失败原因在模型接入侧：' + (out.answer || out.error_code));
  }
  if (out.sql_result) { showResult(out.sql_result); }
}
function showResult(data) {
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
    var response = await fetch('/v1/chat/stream', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: question, scope: { database_sources: [$('source').value] } })
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
    events.events.forEach(addEvent);
    showFinal({ status: run.status, answer: run.answer, confidence_level: run.confidence_level });
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
  b.addEventListener('click', function () { $('question').value = b.dataset.q; });
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
