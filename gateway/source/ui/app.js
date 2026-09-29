let accessToken = '';
let sessionID = '';
let loginFlow = '';
let channels = [];
let channelFilter = 'all';
let channelSearch = '';
let providerDefaults = {};
let lastSuggestedBaseURL = '';
let availableModels = [];
let selectedModels = [];
let modelSearch = '';

const byId = (id) => document.getElementById(id);
const formValue = (form, name) => form.elements.namedItem(name).value.trim();

function notice(message, error = false) {
  const box = byId('notice');
  box.textContent = message || '';
  box.classList.toggle('error', error);
}

function modelFetchStatus(message, error = false) {
  const status = byId('model-fetch-status');
  status.textContent = message || '';
  status.classList.toggle('error', error);
  status.hidden = !message;
}

function normalizeModelIDs(models) {
  return [...new Set(models.map((model) => model.trim()).filter(Boolean))];
}

function renderModelOptions() {
  const list = byId('model-options');
  list.replaceChildren();
  const search = modelSearch.toLocaleLowerCase();
  const visible = availableModels.filter((model) => model.toLocaleLowerCase().includes(search));
  byId('model-option-count').textContent = availableModels.length
    ? `${visible.length} / ${availableModels.length} 个模型`
    : '请先获取模型列表';
  if (!visible.length) {
    list.append(element('span', availableModels.length ? '没有匹配的模型' : '获取模型后可在这里多选', 'model-options-empty'));
    return;
  }
  for (const model of visible) {
    const option = element('label', undefined, 'model-option');
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = selectedModels.includes(model);
    checkbox.addEventListener('change', () => {
      selectedModels = checkbox.checked
        ? [...selectedModels, model]
        : selectedModels.filter((selected) => selected !== model);
      syncSelectedModels();
    });
    option.append(checkbox, element('span', model));
    list.append(option);
  }
}

function syncSelectedModels() {
  selectedModels = normalizeModelIDs(selectedModels);
  byId('channel-models').value = selectedModels.join(',');
  byId('selected-model-count').textContent = String(selectedModels.length);
  const chips = byId('selected-models');
  chips.replaceChildren();
  if (!selectedModels.length) chips.append(element('span', '尚未选择模型', 'selected-model-empty'));
  for (const model of selectedModels) {
    const chip = element('span', undefined, 'model-chip');
    chip.append(element('span', model));
    chip.append(action('×', () => {
      selectedModels = selectedModels.filter((selected) => selected !== model);
      syncSelectedModels();
    }, 'remove-model'));
    chips.append(chip);
  }
  renderModelOptions();
}

function addManualModels() {
  const input = byId('manual-model-input');
  const models = normalizeModelIDs(input.value.split(','));
  if (!models.length) return;
  availableModels = normalizeModelIDs([...availableModels, ...models]);
  selectedModels = normalizeModelIDs([...selectedModels, ...models]);
  input.value = '';
  syncSelectedModels();
}

async function request(path, options = {}, credential = accessToken) {
  const headers = new Headers(options.headers || {});
  if (credential) headers.set('Authorization', `Bearer ${credential}`);
  if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  if (sessionID) headers.set('X-Auth-Session', sessionID);
  const response = await fetch(path, { ...options, headers, credentials: 'same-origin', cache: 'no-store' });
  let result;
  try { result = await response.json(); } catch { throw new Error(`服务器返回 ${response.status}`); }
  if (!response.ok || result.success === false || result.error) {
    throw new Error(result.message || result.error?.message || `请求失败 (${response.status})`);
  }
  return result;
}

function setAuthenticated(data) {
  accessToken = data.access_token;
  sessionID = data.session?.sid || data.session?.id || '';
  byId('login-panel').hidden = true;
  byId('setup-panel').hidden = true;
  byId('workspace').hidden = false;
  byId('logout').hidden = false;
  loadProviderDefaults();
  loadChannels();
}

async function start() {
  try {
    const setup = await request('/api/setup');
    if (!setup.data.status) {
      byId('setup-panel').hidden = false;
      return;
    }
    const refreshed = await request('/api/user/auth/refresh', { method: 'POST' }, '');
    setAuthenticated(refreshed.data);
  } catch {
    byId('login-panel').hidden = false;
  }
}

byId('setup-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    await request('/api/setup', { method: 'POST', body: JSON.stringify({
      username: formValue(form, 'username'), password: formValue(form, 'password'),
      confirmPassword: formValue(form, 'confirmPassword'), SelfUseModeEnabled: true, DemoSiteEnabled: false,
    }) }, '');
    form.reset();
    byId('setup-panel').hidden = true;
    byId('login-panel').hidden = false;
    notice('初始化完成，请登录。');
  } catch (error) { notice(error.message, true); }
});

byId('login-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    const encryption = await request('/api/user/login/encryption-key', {}, '');
    if (encryption.data?.enabled) throw new Error('当前启用了密码传输加密，请在 New API 原版控制台登录。');
    const result = await request('/api/user/login', { method: 'POST', body: JSON.stringify({
      username: formValue(form, 'username'), password: formValue(form, 'password'),
    }) }, '');
    form.elements.namedItem('password').value = '';
    if (result.data.access_token) { setAuthenticated(result.data); notice('登录成功。'); return; }
    if (result.data.flow_token) {
      loginFlow = result.data.flow_token;
      byId('twofa-form').hidden = false;
      notice('请输入两步验证码。');
      return;
    }
    throw new Error('当前登录验证方式尚未接入此精简页面。');
  } catch (error) { notice(error.message, true); }
});

byId('twofa-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    const result = await request('/api/user/login/2fa', { method: 'POST', body: JSON.stringify({
      flow_token: loginFlow, method: '2fa', code: formValue(form, 'code'),
    }) }, '');
    form.reset();
    loginFlow = '';
    setAuthenticated(result.data);
    notice('登录成功。');
  } catch (error) { notice(error.message, true); }
});

byId('logout').addEventListener('click', async () => {
  try { await request('/api/user/auth/logout', { method: 'POST' }); } catch { /* local logout still clears the page */ }
  accessToken = '';
  sessionID = '';
  byId('workspace').hidden = true;
  byId('logout').hidden = true;
  byId('login-panel').hidden = false;
  byId('test-form').reset();
  byId('test-result').textContent = '';
  notice('已退出登录。');
});

for (const tab of document.querySelectorAll('[data-tab]')) {
  tab.addEventListener('click', () => {
    for (const button of document.querySelectorAll('[data-tab]')) button.classList.toggle('selected', button === tab);
    for (const section of document.querySelectorAll('.tab')) section.hidden = section.id !== tab.dataset.tab;
    if (tab.dataset.tab === 'tokens') loadTokens();
  });
}

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function action(label, onClick, className = 'ghost') {
  const button = element('button', label, className);
  button.type = 'button';
  button.addEventListener('click', onClick);
  return button;
}

const providerNames = {
  1: 'OpenAI 兼容', 17: '通义千问', 18: '讯飞星火', 23: '腾讯混元', 25: 'Kimi',
  16: '智谱 AI', 35: 'MiniMax', 40: '硅基流动', 43: 'DeepSeek', 45: '火山方舟', 46: '百度千帆',
};

function providerInitial(type) {
  const initials = { 1: 'AI', 17: '通', 18: '讯', 23: '腾', 25: 'KM', 16: '智', 35: 'MX', 40: '硅', 43: 'DS', 45: '火', 46: '百' };
  return initials[type] || 'AI';
}

async function loadProviderDefaults() {
  try {
    const result = await request('/api/channel/default_base_urls');
    providerDefaults = result.data || {};
    if (byId('channel-dialog').open && !formValue(byId('channel-form'), 'id')) applyProviderDefault();
  } catch { /* custom base URL remains available when presets cannot load */ }
}

function renderChannels() {
  const list = byId('channel-list');
  list.replaceChildren();
  const enabled = channels.filter((channel) => channel.status === 1).length;
  const models = new Set(channels.flatMap((channel) => (channel.models || '').split(',').map((model) => model.trim()).filter(Boolean)));
  byId('channel-count').textContent = String(channels.length);
  byId('enabled-count').textContent = String(enabled);
  byId('model-count').textContent = String(models.size);

  const search = channelSearch.toLocaleLowerCase();
  const visibleChannels = channels.filter((channel) => {
    if (channelFilter === 'enabled' && channel.status !== 1) return false;
    if (channelFilter === 'disabled' && channel.status === 1) return false;
    const haystack = `${channel.name || ''} ${channel.models || ''} ${providerNames[channel.type] || ''}`.toLocaleLowerCase();
    return !search || haystack.includes(search);
  });

  if (!visibleChannels.length) {
    const empty = element('div', undefined, 'empty-state');
    empty.append(element('strong', channels.length ? '没有匹配的渠道' : '还没有接入渠道'));
    empty.append(element('p', channels.length ? '试试其他搜索词或筛选条件。' : '添加第一个模型服务商，之后可以直接测试连接。'));
    if (!channels.length) empty.append(action('＋ 添加渠道', openAddChannel));
    list.append(empty);
    return;
  }

  for (const channel of visibleChannels) {
    const enabled = channel.status === 1;
    const item = element('article', undefined, `provider-card ${enabled ? 'is-enabled' : 'is-disabled'}`);
    const logo = element('div', providerInitial(channel.type), `provider-icon provider-${channel.type}`);
    const info = element('div', undefined, 'provider-info');
    const title = element('div', undefined, 'provider-title');
    title.append(element('h3', channel.name || `渠道 ${channel.id}`));
    title.append(element('span', enabled ? '已启用' : '已停用', `state-pill ${enabled ? 'on' : 'off'}`));
    info.append(title);
    const meta = element('div', undefined, 'provider-meta');
    meta.append(element('span', providerNames[channel.type] || '自定义渠道'));
    meta.append(element('span', channel.base_url || '供应商默认地址'));
    if (channel.response_time > 0) meta.append(element('span', `${(channel.response_time / 1000).toFixed(2)} 秒`));
    info.append(meta);
    const modelList = (channel.models || '').split(',').map((model) => model.trim()).filter(Boolean);
    const modelLine = element('div', undefined, 'provider-models');
    modelLine.append(element('span', `${modelList.length} 个模型`));
    modelLine.append(element('span', modelList.slice(0, 3).join(' · ') || '尚未配置模型'));
    if (modelList.length > 3) modelLine.append(element('span', `+${modelList.length - 3}`));
    info.append(modelLine);

    const actions = element('div', undefined, 'card-actions');
    actions.append(action('测试连接', () => testChannel(channel), 'ghost test-action'));
    actions.append(action('编辑', () => editChannel(channel), 'ghost'));
    actions.append(action(enabled ? '停用' : '启用', () => changeStatus(channel), 'ghost'));
    actions.append(action('删除', () => deleteChannel(channel), 'ghost danger'));
    item.append(logo, info, actions);
    list.append(item);
  }
}

async function loadChannels() {
  try {
    const result = await request('/api/channel/?p=1&page_size=100');
    channels = result.data?.items || [];
    renderChannels();
  } catch (error) { notice(error.message, true); }
}

function openAddChannel() {
  const form = byId('channel-form');
  form.reset();
  availableModels = [];
  selectedModels = [];
  modelSearch = '';
  byId('model-search').value = '';
  byId('manual-model-input').value = '';
  syncSelectedModels();
  modelFetchStatus('');
  form.elements.namedItem('id').value = '';
  byId('channel-form-title').textContent = '添加渠道';
  byId('key-help').textContent = '密钥仅用于连接上游，不会显示在渠道列表中';
  lastSuggestedBaseURL = '';
  applyProviderDefault();
  byId('channel-dialog').showModal();
  form.elements.namedItem('name').focus();
}

function editChannel(channel) {
  const form = byId('channel-form');
  availableModels = normalizeModelIDs((channel.models || '').split(','));
  selectedModels = [...availableModels];
  modelSearch = '';
  byId('model-search').value = '';
  byId('manual-model-input').value = '';
  syncSelectedModels();
  modelFetchStatus('');
  for (const field of ['id', 'name', 'type']) form.elements.namedItem(field).value = channel[field] ?? '';
  form.elements.namedItem('base_url').value = channel.base_url || '';
  form.elements.namedItem('key').value = '';
  byId('channel-form-title').textContent = `编辑渠道 · ${channel.name}`;
  byId('key-help').textContent = '留空保持现有密钥，填写新 Key 可替换';
  lastSuggestedBaseURL = '';
  byId('channel-dialog').showModal();
  form.elements.namedItem('name').focus();
}

function closeChannelDialog() {
  byId('channel-dialog').close();
  byId('channel-form').reset();
  availableModels = [];
  selectedModels = [];
  modelSearch = '';
  byId('model-search').value = '';
  byId('manual-model-input').value = '';
  syncSelectedModels();
  modelFetchStatus('');
  byId('channel-form').elements.namedItem('id').value = '';
  byId('channel-form-title').textContent = '添加渠道';
  lastSuggestedBaseURL = '';
}

function applyProviderDefault() {
  const baseURL = byId('channel-form').elements.namedItem('base_url');
  if (!baseURL.value || baseURL.value === lastSuggestedBaseURL) {
    lastSuggestedBaseURL = providerDefaults[byId('channel-form').elements.namedItem('type').value] || '';
    baseURL.value = lastSuggestedBaseURL;
  }
}

byId('add-channel').addEventListener('click', openAddChannel);
byId('close-channel').addEventListener('click', closeChannelDialog);
byId('cancel-edit').addEventListener('click', closeChannelDialog);
byId('channel-form').elements.namedItem('type').addEventListener('change', applyProviderDefault);
byId('channel-search').addEventListener('input', (event) => {
  channelSearch = event.currentTarget.value.trim();
  renderChannels();
});
byId('model-search').addEventListener('input', (event) => {
  modelSearch = event.currentTarget.value.trim();
  renderModelOptions();
});
byId('add-manual-model').addEventListener('click', addManualModels);
byId('manual-model-input').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    event.preventDefault();
    addManualModels();
  }
});
byId('select-visible-models').addEventListener('click', () => {
  const search = modelSearch.toLocaleLowerCase();
  const visible = availableModels.filter((model) => model.toLocaleLowerCase().includes(search));
  selectedModels = normalizeModelIDs([...selectedModels, ...visible]);
  syncSelectedModels();
});
byId('clear-selected-models').addEventListener('click', () => {
  selectedModels = [];
  syncSelectedModels();
});
for (const filter of document.querySelectorAll('[data-filter]')) {
  filter.addEventListener('click', () => {
    channelFilter = filter.dataset.filter;
    for (const button of document.querySelectorAll('[data-filter]')) button.classList.toggle('selected', button === filter);
    renderChannels();
  });
}

byId('fetch-models').addEventListener('click', async (event) => {
  const form = byId('channel-form');
  const id = formValue(form, 'id');
  const key = formValue(form, 'key');
  const button = event.currentTarget;
  button.disabled = true;
  notice('');
  modelFetchStatus('正在连接上游并获取模型列表…');
  try {
    let result;
    if (id && !key) {
      result = await request(`/api/channel/${id}/fetch_models`);
    } else {
      if (!key) throw new Error('请先填写上游 API Key。');
      result = await request('/api/channel/fetch_models', {
        method: 'POST',
        body: JSON.stringify({
          type: Number(formValue(form, 'type')),
          key,
          base_url: formValue(form, 'base_url') || null,
        }),
      });
    }
    const models = result.data || [];
    if (!models.length) throw new Error('上游没有返回可用模型。');
    availableModels = normalizeModelIDs([...models, ...selectedModels]);
    syncSelectedModels();
    modelFetchStatus(`已从上游获取 ${models.length} 个模型，请确认后保存。`);
  } catch (error) {
    const message = error.message.includes('status code: 404')
      ? `${error.message}。请检查服务商和 API 地址。`
      : error.message;
    modelFetchStatus(message, true);
  } finally {
    button.disabled = false;
  }
});

byId('channel-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const id = formValue(form, 'id');
  if (!selectedModels.length) {
    modelFetchStatus('请至少选择一个模型。', true);
    return;
  }
  const channel = {
    type: Number(formValue(form, 'type')), name: formValue(form, 'name'),
    models: formValue(form, 'models'), group: id ? (channels.find((entry) => entry.id === Number(id))?.group || 'default') : 'default',
    base_url: formValue(form, 'base_url') || null, key: formValue(form, 'key'),
  };
  try {
    if (id) {
      channel.id = Number(id);
      await request('/api/channel/', { method: 'PUT', body: JSON.stringify(channel) });
    } else {
      if (!channel.key) throw new Error('请填写上游 API Key。');
      await request('/api/channel/', { method: 'POST', body: JSON.stringify({ mode: 'single', channel }) });
    }
    closeChannelDialog();
    await loadChannels();
    notice(id ? '渠道已更新。' : '渠道已添加，请点击“测试”验证。');
  } catch (error) { notice(error.message, true); }
});

async function testChannel(channel) {
  try {
    notice(`正在测试 ${channel.name}…`);
    const model = channel.test_model || channel.models?.split(',')[0]?.trim() || '';
    const result = await request(`/api/channel/test/${channel.id}?model=${encodeURIComponent(model)}`);
    notice(`${channel.name} 测试通过，耗时 ${result.time ?? 0} 秒。`);
    await loadChannels();
  } catch (error) { notice(`${channel.name}：${error.message}`, true); }
}

async function changeStatus(channel) {
  try {
    await request(`/api/channel/${channel.id}/status`, { method: 'POST', body: JSON.stringify({ status: channel.status === 1 ? 2 : 1 }) });
    await loadChannels();
    notice('状态已更新。');
  } catch (error) { notice(error.message, true); }
}

async function deleteChannel(channel) {
  if (!confirm(`确定删除渠道“${channel.name}”？`)) return;
  try {
    await request(`/api/channel/${channel.id}`, { method: 'DELETE' });
    await loadChannels();
    notice('渠道已删除。');
  } catch (error) { notice(error.message, true); }
}

byId('refresh-channels').addEventListener('click', loadChannels);

async function loadTokens() {
  try {
    const result = await request('/api/token/?p=1&page_size=100');
    const list = byId('token-list');
    list.replaceChildren();
    const tokens = result.data?.items || [];
    if (!tokens.length) list.append(element('p', '还没有应用令牌。'));
    for (const token of tokens) {
      const item = element('article', undefined, 'item');
      const info = element('div');
      info.append(element('h3', token.name || `令牌 ${token.id}`));
      info.append(element('small', token.key || '密钥已隐藏'));
      const actions = element('div', undefined, 'actions');
      actions.append(action('复制密钥', () => copyToken(token.id)));
      actions.append(action('删除', () => deleteToken(token), 'ghost danger'));
      item.append(info, actions);
      list.append(item);
    }
  } catch (error) { notice(error.message, true); }
}

async function copyToken(id) {
  try {
    const result = await request(`/api/token/${id}/key`, { method: 'POST' });
    const key = result.data.key;
    await navigator.clipboard.writeText(key.startsWith('sk-') ? key : `sk-${key}`);
    notice('密钥已复制到剪贴板。');
  } catch (error) { notice(error.message, true); }
}

async function deleteToken(token) {
  if (!confirm(`确定删除令牌“${token.name}”？项目将无法再用它调用模型。`)) return;
  try {
    await request(`/api/token/${token.id}`, { method: 'DELETE' });
    await loadTokens();
    notice('令牌已删除。');
  } catch (error) { notice(error.message, true); }
}

byId('refresh-tokens').addEventListener('click', loadTokens);
byId('token-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    await request('/api/token/', { method: 'POST', body: JSON.stringify({
      name: formValue(form, 'name'), unlimited_quota: true, expired_time: -1, group: 'default',
    }) });
    form.reset();
    await loadTokens();
    notice('令牌已创建。点击“复制密钥”用于项目配置。');
  } catch (error) { notice(error.message, true); }
});

byId('test-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const output = byId('test-result');
  output.textContent = '正在请求模型…';
  try {
    const key = formValue(form, 'api_key');
    const result = await request('/v1/chat/completions', { method: 'POST', body: JSON.stringify({
      model: formValue(form, 'model'), messages: [{ role: 'user', content: formValue(form, 'prompt') }], stream: false,
    }) }, key);
    output.textContent = result.choices?.[0]?.message?.content || JSON.stringify(result, null, 2);
    notice('模型调用成功。');
  } catch (error) { output.textContent = error.message; notice(error.message, true); }
});

byId('list-models').addEventListener('click', async () => {
  const form = byId('test-form');
  const output = byId('test-result');
  try {
    const key = formValue(form, 'api_key');
    if (!key) throw new Error('请先填写应用令牌。');
    const result = await request('/v1/models', {}, key);
    output.textContent = (result.data || []).map((model) => model.id).join('\n') || '此令牌暂无可用模型。';
  } catch (error) { output.textContent = error.message; notice(error.message, true); }
});

start();
