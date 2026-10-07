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
var AUTH = {user:null,csrf:null};
var AUTHORITY = {candidates:[],documents:[],pending:null};
var CAD = {assets:[],sources:[],pending:null,token:0,reviewPending:{}};
var DIM = {token:0,loadingToken:null,loadingActor:null,reads:{},pending:{},busy:{},sources:[],measurements:[],bindings:[],drawings:[],measurement:null,binding:null,formMeasurement:null,drawing:null,feature:null,measurementBaseline:null,bindingBaseline:null,historyOffset:0,historyNext:null,formRevision:0,measurementRevision:0};
function hasRole(role) { return !!(AUTH.user && (AUTH.user.roles.includes(role) || AUTH.user.roles.includes('admin'))); }
function renderAuth() {
  var labels={viewer:'浏览',reviewer:'复核',approver:'审批',admin:'管理'};
  $('accountStatus').textContent=AUTH.user ? AUTH.user.display_name+' · '+AUTH.user.roles.map(function(r){return labels[r];}).join(' / ') : '未登录 · 只读浏览';
  $('loginForm').classList.toggle('hidden',!!AUTH.user);$('logout').classList.toggle('hidden',!AUTH.user);
  ['saveProfile','saveActivate','testConfig','fetchModels','exportProfiles','importProfiles'].forEach(function(id){$(id).disabled=!hasRole('admin');});
  ['loadAuthorityCandidates','registerAuthority'].forEach(function(id){$(id).disabled=!hasRole('admin');});
  $('cadRegistration').classList.toggle('hidden',!hasRole('admin'));$('registerCad').disabled=!hasRole('admin');
  renderDimensionPermissions();
  if(window.renderMultifileAuth){window.renderMultifileAuth();}
  if(!hasRole('admin')){$('navModelBadge').textContent='管理权限';$('pulseModelText').textContent='管理员可查看';}
}
async function apiFetch(url,options) {
  var requestAuth=AUTH;
  options=options || {};var headers=new Headers(options.headers || {});
  if (AUTH.csrf && !['GET','HEAD'].includes((options.method || 'GET').toUpperCase())) { headers.set('X-CSRF-Token',AUTH.csrf); }
  var response=await fetch(url,Object.assign({},options,{headers:headers,credentials:'same-origin'}));
  if (response.status===401 && url!=='/v1/auth/login' && AUTH===requestAuth) { AUTH={user:null,csrf:null};renderAuth();clearCad();CAD.pending=null;clearDimensions();if(window.clearMultifile){window.clearMultifile();} }
  return response;
}
async function refreshAuth() {
  try { var r=await apiFetch('/v1/auth/me');if(r.ok){var data=await r.json();AUTH={user:data.user,csrf:data.csrf_token};} }
  catch(error){ $('authMessage').textContent='账户状态读取失败。'; }
  renderAuth();
}
async function signIn(event) {
  event.preventDefault();$('authMessage').textContent='正在登录…';
  try {
    var r=await apiFetch('/v1/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:$('loginUsername').value,password:$('loginPassword').value})});
    $('loginPassword').value='';
    if(!r.ok){throw new Error(r.status===429?'登录尝试过多，请稍后再试。':'账号、密码或账户状态不正确。');}
    var data=await r.json();AUTH={user:data.user,csrf:data.csrf_token};renderAuth();$('authMessage').textContent='已登录。';
    clearDimensions();if(hasRole('admin')){loadProfiles();}loadDocuments();loadAuthority();if(location.hash==='#cad'){loadCad();}if(location.hash==='#dimensions'){loadDimensions();}if(window.loadMultifile){window.loadMultifile();}
  }catch(error){$('authMessage').textContent=error.message;}
}
async function signOut() {
  var r=await apiFetch('/v1/auth/logout',{method:'POST'});
  if(r.ok || r.status===401){AUTH={user:null,csrf:null};renderAuth();$('authMessage').textContent='已退出。';
    loadAuthority();AUTHORITY.pending=null;
    clearCad();CAD.pending=null;clearDimensions();if(window.clearMultifile){window.clearMultifile();}
    document.querySelectorAll('dialog.document-fields').forEach(function(d){d.close();});}
}

function view(name, fromHash) {
  if(name!=='dimensions'){DIM.token++;}
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
  if (name === 'authority') { loadAuthority(); }
  if (name === 'cad') { loadCad(); }
  if (name === 'dimensions') { loadDimensions(); }
}
window.addEventListener('hashchange', function () {
  var name = location.hash.slice(1);
  if (name) { view(name, true); }
});


/* ---------------- 图纸资产与固定源版本 ---------------- */
function clearCad() {
  CAD.token++;CAD.assets=[];CAD.sources=[];CAD.reviewPending={};
  $('cadAsset').replaceChildren();$('cadVersion').replaceChildren();$('cadFeatures').replaceChildren();$('cadReviews').replaceChildren();
  $('cadViewer').src='about:blank';$('cadSourceLink').classList.add('hidden');$('cadVersionInfo').textContent='';
  $('cadStagedSource').replaceChildren();$('cadMessage').textContent='登录后可查看图纸与源版本。';
}
async function cadResponse(response) {
  var data=await response.json();
  if(!response.ok){var code=(data.detail||{}).error_code;var labels={CAD_VERSION_CHANGED:'图纸当前版本已变化，请刷新后重新登记。',CAD_STAGED_SOURCE_CHANGED:'本地素材已变化，请刷新并重新选择。',CAD_SOURCE_VERSION_MISMATCH:'固定原文件校验失败，本版本暂停显示。',CAD_SNAPSHOT_HASH_MISMATCH:'提取快照校验失败，本版本暂停显示。',CAD_ASSET_IDENTITY_CHANGED:'新源的图号或零件不同，请建立独立资产。',CAD_SOURCE_ALREADY_CURRENT:'该源已是当前版本，无需重复登记。',CAD_REQUEST_ID_REUSED:'登记请求已用于其他内容，请刷新后重试。',LOGIN_REQUIRED:'请先登录。',SESSION_INVALID:'会话已失效，请重新登录。',PERMISSION_DENIED:'当前账户没有此操作权限。',CAD_REVIEW_VERSION_CHANGED:'图纸版本或特征已变化，请刷新后复核。',CAD_REVIEW_STATE_CHANGED:'复核已有新记录，请刷新后重新操作。',CAD_REVIEW_TARGET_INVALID:'请选择当前特征列出的候选圆。',CAD_REVIEW_REQUEST_ID_REUSED:'复核请求已用于其他内容，请刷新后重试。',CAD_REVIEW_HASH_MISMATCH:'复核记录校验失败，暂停使用。',CAD_REVIEW_CHAIN_INVALID:'复核历史链校验失败，暂停使用。'};throw new Error(labels[code] || ('图纸读取或登记失败：'+(code||response.status)));}
  return data;
}
function cadBaseline() {
  var asset=CAD.assets.find(function(a){return a.document_id===$('cadDocumentId').value;});
  $('cadRegistrationBaseline').textContent=asset?'准备登记当前 v'+asset.document_version+' 的新源版本；源选择和当前版本在保存时再次校验。':'新资产，首次登记为 v1。';
  return asset;
}
async function loadCad() {
  if(!AUTH.user){clearCad();return;}
  var token=++CAD.token,selected=$('cadAsset').value;
  $('cadMessage').textContent='正在读取图纸…';
  try {
    var data=await cadResponse(await apiFetch('/v1/drawings'));
    if(token!==CAD.token||!AUTH.user){return;}
    CAD.assets=data.drawings;$('cadAsset').replaceChildren();
    CAD.assets.forEach(function(a){var o=document.createElement('option');o.value=a.document_id;o.textContent=a.title+' · 当前 v'+a.document_version;$('cadAsset').append(o);});
    if(CAD.assets.some(function(a){return a.document_id===selected;})){$('cadAsset').value=selected;}
    if(hasRole('admin')){
      var available=await cadResponse(await apiFetch('/v1/drawings/sources'));
      if(token!==CAD.token||!AUTH.user){return;}
      CAD.sources=available.sources;$('cadStagedSource').replaceChildren();
      CAD.sources.forEach(function(item,index){var o=document.createElement('option');o.value=index;o.textContent=item.name+' · '+(item.provenance==='synthetic_demo'?'合成材料':'外部素材');$('cadStagedSource').append(o);});
    }
    cadBaseline();
    if(!CAD.assets.length){$('cadMessage').textContent='尚未登记图纸。管理员可从本地素材登记。';$('cadViewer').src='about:blank';$('cadFeatures').replaceChildren();$('cadReviews').replaceChildren();$('cadVersionInfo').textContent='';$('cadSourceLink').classList.add('hidden');$('cadVersion').replaceChildren();return;}
    await loadCadVersions();
  }catch(error){if(token===CAD.token){$('cadMessage').textContent=error.message;$('cadViewer').src='about:blank';$('cadFeatures').replaceChildren();$('cadReviews').replaceChildren();$('cadSourceLink').classList.add('hidden');}}
}
async function loadCadVersions() {
  if(!AUTH.user){clearCad();return;}var id=$('cadAsset').value;if(!id){return;}var token=++CAD.token;
  $('cadViewer').src='about:blank';$('cadFeatures').replaceChildren();$('cadReviews').replaceChildren();$('cadSourceLink').classList.add('hidden');
  try {
    var data=await cadResponse(await apiFetch('/v1/drawings/'+encodeURIComponent(id)+'/versions'));
    if(token!==CAD.token||!AUTH.user){return;}
    $('cadVersion').replaceChildren();data.versions.slice().reverse().forEach(function(v){var o=document.createElement('option');o.value=v.document_version;o.textContent='v'+v.document_version+' · '+v.created_at+' · '+v.registrar_label;$('cadVersion').append(o);});
    $('cadVersion').value=data.current_version;
    var asset=CAD.assets.find(function(a){return a.document_id===id;});
    if(hasRole('admin')&&asset){$('cadDocumentId').value=id;$('cadTitle').value=asset.title;cadBaseline();}
    await showCadVersion();
  }catch(error){if(token===CAD.token){$('cadMessage').textContent=error.message;}}
}
async function showCadVersion() {
  if(!AUTH.user){clearCad();return;}var id=$('cadAsset').value,version=$('cadVersion').value;if(!id||!version){return;}
  var token=++CAD.token,base='/v1/drawings/'+encodeURIComponent(id),query='?version='+encodeURIComponent(version);
  $('cadViewer').src='about:blank';$('cadFeatures').replaceChildren();$('cadReviews').replaceChildren();$('cadSourceLink').classList.add('hidden');$('cadVersionInfo').textContent='';
  try {
    var data=await cadResponse(await apiFetch(base+'/features'+query));
    var entities=await cadResponse(await apiFetch(base+'/entities'+query));data._entities=entities.entities;
    if(token!==CAD.token||!AUTH.user){return;}
    var v=data.version,gate=data.inspection_gate,excluded={};(gate.excluded||[]).forEach(function(e){excluded[e.feature_id]=e.reason;});
    $('cadVersionInfo').textContent='查看 v'+v.document_version+' · '+(v.provenance==='synthetic_demo'?'合成演示材料':'外部素材（登记声明）')+' · 提取 '+v.parser_version+' · SHA-256 '+v.source_sha256;
    $('cadMessage').textContent=gate.status==='ready'?'本版本提取与对象引用一致；仍待业务审批与生产关联。':'本版本含待复核或排除项，原文字与几何均保留。';
    var labels={confirmed:'源文件明确引用',ambiguous:'多个候选对象',unresolved:'对象未确认'},reasons={CAD_ASSOCIATION_UNCERTAIN:'对象关联待复核',CAD_VALUE_OR_UNIT_UNKNOWN:'值或单位未知',CAD_GEOMETRY_ANNOTATION_CONFLICT:'几何与标注矛盾'};
    var table=document.createElement('table'),head=document.createElement('tr');['标注原文','原单位 / 偏差','对象引用','使用检查'].forEach(function(text){var th=document.createElement('th');th.textContent=text;head.append(th);});table.append(head);
    data.features.forEach(function(f){var tr=document.createElement('tr'),values=[f.annotation_raw,(f.unit==='unknown'?'单位未知':f.unit)+' · '+(f.nominal_value===null?'数值未确认':f.nominal_value+' ('+f.tolerance_lower+', '+f.tolerance_upper+')'),labels[f.association_status]+' · 标注 '+f.locator.handle+' / '+(f.selected_target||f.target_candidates.join(', ')||'无目标'),excluded[f.feature_id]?(reasons[excluded[f.feature_id]]||excluded[f.feature_id]):'提取/对象检查通过；未审批'];values.forEach(function(text){var td=document.createElement('td');td.textContent=text;tr.append(td);});table.append(tr);});
    $('cadFeatures').append(table);renderCadReviews(data,id,version,token);$('cadViewer').src=base+'/viewer'+query;$('cadSourceLink').href=base+'/file'+query;$('cadSourceLink').classList.remove('hidden');
  }catch(error){if(token===CAD.token){$('cadMessage').textContent=error.message;}}
}
function renderCadReviews(data,id,version,token) {
  var container=$('cadReviews'),title=document.createElement('h2');title.textContent='尺寸对象关联复核';container.append(title);
  var note=document.createElement('p');note.className='field-hint';note.textContent='复核只确认标注对应哪个候选圆；原文、单位和几何保持不变，不代表图纸审批或生产合格。原图继续显示源文件提取结果。';container.append(note);
  var decisionLabels={confirmed:'已确认关联',rejected:'已退回关联',needs_review:'关联存疑'};
  var excluded={};data.review_gate.excluded.forEach(function(e){excluded[e.feature_id]=e.reason;});
  var reasonLabels={CAD_ASSOCIATION_NOT_REVIEWED:'尚未认证复核',CAD_ASSOCIATION_REJECTED:'关联已退回',CAD_ASSOCIATION_REVIEW_PENDING:'关联仍存疑',CAD_VALUE_OR_UNIT_UNKNOWN:'值或单位未知',CAD_GEOMETRY_ANNOTATION_CONFLICT:'几何与标注矛盾'};
  data.features.forEach(function(f){
    var state=data.association_reviews.find(function(r){return r.feature_id===f.feature_id;}),latest=state.latest_review;
    var card=document.createElement('div');card.className='cad-review-card';
    var heading=document.createElement('h3');heading.textContent='标注 '+f.locator.handle+' · '+f.annotation_raw;card.append(heading);
    var status=document.createElement('p');status.textContent=(latest?decisionLabels[latest.decision]+' · '+latest.reviewer_label+' · '+latest.created_at+(latest.selected_target?' · 人工目标 '+latest.selected_target:''):'本版本尚未复核')+'；'+(excluded[f.feature_id]?(reasonLabels[excluded[f.feature_id]]||excluded[f.feature_id]):'复核与提取一致性检查通过；未审批');card.append(status);
    if(latest){var rationale=document.createElement('p');rationale.textContent='复核理由：'+latest.reason;card.append(rationale);}
    var historyButton=document.createElement('button');historyButton.type='button';historyButton.className='secondary';historyButton.textContent='查看标注 '+f.locator.handle+' 的复核历史（'+state.history_count+'）';card.append(historyButton);
    var history=document.createElement('div');history.className='cad-review-history';card.append(history);
    historyButton.addEventListener('click',async function(){historyButton.disabled=true;try{
      var result=await cadResponse(await apiFetch('/v1/drawings/'+encodeURIComponent(id)+'/features/'+encodeURIComponent(f.feature_id)+'/reviews?version='+encodeURIComponent(version)));
      if(token!==CAD.token||!AUTH.user){return;}history.replaceChildren();
      if(!result.reviews.length){history.textContent='本版本没有复核记录。';}
      result.reviews.forEach(function(r){var p=document.createElement('p');p.textContent='v'+r.document_version+' · '+decisionLabels[r.decision]+(r.selected_target?' / '+r.selected_target:'')+' · '+r.reviewer_label+' · '+r.created_at+' · '+r.reason;history.append(p);});
    }catch(error){if(token===CAD.token){history.textContent=error.message;}}finally{historyButton.disabled=false;}});
    if(hasRole('reviewer')){
      var currentAsset=CAD.assets.find(function(a){return a.document_id===id;});
      if(!currentAsset||currentAsset.document_version!==Number(version)){var old=document.createElement('p');old.className='field-hint';old.textContent='历史版本只读；新增复核请选择当前图纸版本。';card.append(old);}
      else {
        var form=document.createElement('form');form.className='cad-review-form';var prefix='cadReview-'+f.locator.handle;
        var actionLabel=document.createElement('label');actionLabel.htmlFor=prefix+'Decision';actionLabel.textContent='标注 '+f.locator.handle+' 的复核决定';
        var action=document.createElement('select');action.id=actionLabel.htmlFor;
        [['confirmed','确认关联'],['needs_review','保留存疑'],['rejected','退回关联']].forEach(function(o){var option=document.createElement('option');option.value=o[0];option.textContent=o[1];action.append(option);});
        var targetLabel=document.createElement('label');targetLabel.htmlFor=prefix+'Target';targetLabel.textContent='标注 '+f.locator.handle+' 的候选圆';var target=document.createElement('select');target.id=targetLabel.htmlFor;
        var empty=document.createElement('option');empty.value='';empty.textContent='请选择明确对应的圆';target.append(empty);
        f.target_candidates.forEach(function(handle){var e=data._entities&&data._entities.find(function(x){return x.handle===handle;});var option=document.createElement('option');option.value=handle;option.textContent=handle+(e?' · 圆心 ('+e.center.x+', '+e.center.y+') / 半径 '+e.radius:'');target.append(option);});target.required=true;
        var reasonLabel=document.createElement('label');reasonLabel.htmlFor=prefix+'Reason';reasonLabel.textContent='标注 '+f.locator.handle+' 的复核理由';var reason=document.createElement('textarea');reason.id=reasonLabel.htmlFor;reason.required=true;reason.maxLength=1000;
        action.addEventListener('change',function(){target.disabled=action.value!=='confirmed';target.required=action.value==='confirmed';if(target.disabled){target.value='';}});
        var save=document.createElement('button');save.type='submit';save.textContent='保存标注 '+f.locator.handle+' 的关联复核';var message=document.createElement('p');message.setAttribute('role','status');
        form.append(actionLabel,action,targetLabel,target,reasonLabel,reason,save,message);card.append(form);
        form.addEventListener('submit',async function(event){event.preventDefault();if(!hasRole('reviewer')||token!==CAD.token){return;}
          var actor=AUTH.user.user_id,v=data.version,key=id+':'+version+':'+f.feature_id;
          var body={document_version:v.document_version,source_sha256:v.source_sha256,parser_version:v.parser_version,bundle_sha256:v.bundle_sha256,feature_sha256:state.feature_sha256,previous_review_id:latest?latest.review_id:null,decision:action.value,selected_target:action.value==='confirmed'?target.value:null,reason:reason.value.trim()};
          if(!body.reason){message.textContent='请填写复核理由。';return;}
          var signature=JSON.stringify(body),pending=CAD.reviewPending[key];if(!pending||pending.signature!==signature){pending={signature:signature,request_id:crypto.randomUUID()};CAD.reviewPending[key]=pending;}body.request_id=pending.request_id;
          save.disabled=true;message.textContent='正在保存本版本关联复核…';
          try {await cadResponse(await apiFetch('/v1/drawings/'+encodeURIComponent(id)+'/features/'+encodeURIComponent(f.feature_id)+'/reviews',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}));
            if(token!==CAD.token||!AUTH.user||AUTH.user.user_id!==actor){return;}delete CAD.reviewPending[key];await showCadVersion();
          }catch(error){if(token===CAD.token&&AUTH.user&&AUTH.user.user_id===actor){message.textContent=error.message;}}finally{save.disabled=!hasRole('reviewer');}
        });
      }
    }
    container.append(card);
  });
}

async function registerCad(event) {
  event.preventDefault();if(!hasRole('admin')){return;}
  var source=CAD.sources[Number($('cadStagedSource').value)],asset=cadBaseline();if(!source){$('cadRegistrationMessage').textContent='请先读取本地素材。';return;}
  var body={document_id:$('cadDocumentId').value,title:$('cadTitle').value.trim(),source_path:source.source_path,source_sha256:source.source_sha256,provenance:source.provenance,previous_version:asset?asset.document_version:0,previous_source_sha256:asset?asset.source_sha256:null};
  var signature=JSON.stringify(body);if(!CAD.pending||CAD.pending.signature!==signature){CAD.pending={signature:signature,request_id:crypto.randomUUID()};}body.request_id=CAD.pending.request_id;
  $('registerCad').disabled=true;$('cadRegistrationMessage').textContent='正在解析并保存固定源版本…';
  try {
    var data=await cadResponse(await apiFetch('/v1/drawings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}));
    if(!AUTH.user){return;}CAD.pending=null;await loadCad();$('cadAsset').value=data.version.document_id;await loadCadVersions();$('cadRegistrationMessage').textContent='已登记 v'+data.version.document_version+'，源文件与提取快照已固定；登记不等同审批。';
  }catch(error){$('cadRegistrationMessage').textContent=error.message;}
  finally{$('registerCad').disabled=!hasRole('admin');}
}
$('refreshCad').addEventListener('click',loadCad);
$('cadAsset').addEventListener('change',loadCadVersions);
$('cadVersion').addEventListener('change',showCadVersion);
$('cadDocumentId').addEventListener('input',cadBaseline);
$('cadForm').addEventListener('submit',registerCad);

/* ---------------- 本地测量、显式绑定与独立使用批准 ---------------- */
var DIMENSION_REASONS = {
  LOGIN_REQUIRED:'请先登录。',SESSION_INVALID:'会话已失效，请重新登录。',PERMISSION_DENIED:'当前账户没有此操作权限。',
  DIMENSION_RECORD_NOT_FOUND:'没有找到这条尺寸记录。',DIMENSION_SOURCE_CHANGED:'素材已变化，请刷新并重新选择。',
  DIMENSION_SOURCE_INVALID:'素材的测量 ID、版本或内容不符合单条测量登记要求。',DIMENSION_MEASUREMENT_STATE_CHANGED:'测量当前版本已变化，请重新读取登记基线。',
  DIMENSION_BINDING_STATE_CHANGED:'绑定当前版本已变化，请刷新并重新读取基线。',DIMENSION_INPUT_VERSION_CHANGED:'图纸或测量版本已变化，请重新选择当前输入。',
  DIMENSION_REVIEW_STATE_CHANGED:'关联复核已有新记录，请刷新图纸与标注。',DIMENSION_APPROVAL_STATE_CHANGED:'使用批准已有新记录，请刷新后重新决定。',
  DIMENSION_CONFIRMED_ASSOCIATION_REQUIRED:'需要当前版本已确认的标注关联。',DIMENSION_INDEPENDENT_APPROVER_REQUIRED:'批准人须独立于绑定登记人和关联复核人。',
  DIMENSION_NO_ACTIVE_APPROVAL_TO_REVOKE:'本版本当前没有可撤销的批准。',DIMENSION_REQUEST_ID_REUSED:'此请求编号已用于其他内容，请刷新后重试。',
  DIMENSION_MEASUREMENT_IDENTITY_CHANGED:'测量来源或零件身份发生变化，请建立独立测量 ID。',DIMENSION_BINDING_IDENTITY_CHANGED:'绑定所用的图纸或测量身份发生变化，请建立独立绑定 ID。',
  DIMENSION_MEASUREMENT_VERSION_MISMATCH:'测量版本已更新，当前绑定暂停使用。',DIMENSION_DRAWING_VERSION_MISMATCH:'图纸版本已更新，当前绑定暂停使用。',
  DIMENSION_CURRENT_HEAD_MISMATCH:'图纸、测量、关联复核或绑定已更新，当前证据不再适用。请重新读取当前版本并建立新绑定。',
  DIMENSION_REVIEW_VERSION_MISMATCH:'关联复核已变化，当前绑定暂停使用。',DIMENSION_BINDING_VERSION_MISMATCH:'绑定已改版，旧版本暂停使用。',
  DIMENSION_USE_APPROVAL_REQUIRED:'等待独立账户批准本版本使用。',DIMENSION_USE_APPROVAL_REJECTED:'本版本使用已退回。',DIMENSION_USE_APPROVAL_REVOKED:'本版本使用批准已撤销。',
  DIMENSION_PRODUCTION_SCOPE_MISMATCH:'测量生产范围与绑定不一致。',DIMENSION_PART_MISMATCH:'测量零件与图纸零件不一致。',DIMENSION_TIMEZONE_REQUIRED:'有效期必须包含时区。',DIMENSION_BINDING_TIME_INVALID:'有效期起点须早于终点。',
  DIMENSION_OUTSIDE_VALIDITY:'测量时刻不在绑定有效期内。',DIMENSION_MEASUREMENT_VALUE_OR_UNIT_UNKNOWN:'测量值或单位未知，暂停比较。',DIMENSION_MEASUREMENT_IN_FUTURE:'测量时刻晚于核验时刻。',
  CAD_VALUE_OR_UNIT_UNKNOWN:'标注数值或单位未知，暂停使用。',CAD_GEOMETRY_ANNOTATION_CONFLICT:'几何与标注存在矛盾，暂停使用。',CAD_ASSOCIATION_UNCERTAIN:'标注对象关联尚未确认。'
};
function dimensionReason(code) { return DIMENSION_REASONS[code] || ('核验暂停：'+(code||'记录读取失败')); }
function dimensionAlive(token,actor) { return token===DIM.token && !!AUTH.user && AUTH.user.user_id===actor; }
function dimensionReadKey(name) { DIM.reads[name]=(DIM.reads[name]||0)+1;return DIM.reads[name]; }
function dimensionReadAlive(name,key,token,actor) { return dimensionAlive(token,actor)&&DIM.reads[name]===key; }
function renderDimensionPermissions() {
  var loading=DIM.loadingToken!==null;
  ['dimensionMeasurement','dimensionBinding','dimensionBindingVersion'].forEach(function(id){$(id).disabled=!AUTH.user||loading;});
  $('refreshDimensions').disabled=!AUTH.user||loading;
  ['dimensionMeasurementRegistration','dimensionBindingRegistration'].forEach(function(id){$(id).classList.toggle('hidden',!hasRole('admin'));});
  [['dimensionMeasurementForm','admin','measurement'],['dimensionBindingForm','admin','binding'],['dimensionApprovalForm','approver','approval']].forEach(function(form){$(form[0]).querySelectorAll('input,select,textarea').forEach(function(control){control.disabled=!hasRole(form[1])||loading||!!DIM.busy[form[2]];});});
  $('dimensionApprovalControls').classList.toggle('hidden',!hasRole('approver')||!DIM.binding);
  $('saveDimensionComparison').classList.toggle('hidden',!hasRole('admin')||!DIM.binding);
  $('dimensionMeasurementBaselineRefresh').disabled=!hasRole('admin')||loading||!!DIM.busy.measurement;
  $('dimensionBindingBaselineRefresh').disabled=!hasRole('admin')||loading||!!DIM.busy.binding;
  $('registerDimensionMeasurement').disabled=!hasRole('admin')||loading||!!DIM.busy.measurement||!DIM.measurementBaseline||DIM.measurementBaseline.id!==$('dimensionMeasurementId').value||!$('dimensionStagedSource').value;
  $('registerDimensionBinding').disabled=!hasRole('admin')||loading||!!DIM.busy.binding||!DIM.bindingBaseline||DIM.bindingBaseline.id!==$('dimensionBindingId').value||!DIM.formMeasurement||!DIM.drawing||!DIM.feature||!$('dimensionTarget').value;
  $('saveDimensionApproval').disabled=!hasRole('approver')||loading||!!DIM.busy.approval||!DIM.binding||DIM.binding.binding.payload.registrar_user_id===AUTH.user.user_id;
  $('saveDimensionComparison').disabled=!hasRole('admin')||loading||!!DIM.busy.comparison||!DIM.binding;
}
function dimensionOptions(select,items,empty,selected) {
  select.replaceChildren();var placeholder=document.createElement('option');placeholder.value='';placeholder.textContent=empty;select.append(placeholder);
  items.forEach(function(item){var option=document.createElement('option');option.value=item.value;option.textContent=item.text;select.append(option);});
  if(items.some(function(item){return String(item.value)===String(selected);})){select.value=selected;}
}
function clearDimensionBindingDetail() {
  DIM.binding=null;DIM.historyOffset=0;DIM.historyNext=null;dimensionReadKey('binding');dimensionReadKey('history');dimensionReadKey('replay');
  ['dimensionBindingInfo','dimensionCurrentResult','dimensionApprovalHistory','dimensionComparisonHistory','dimensionReplay'].forEach(function(id){$(id).replaceChildren();});
  ['dimensionApprovalMessage','dimensionComparisonMessage','dimensionHistoryMessage','dimensionHistoryPage'].forEach(function(id){$(id).textContent='';});
  $('dimensionApprovalForm').reset();$('dimensionBindingDetail').classList.add('hidden');$('dimensionComparisonHistoryCard').classList.add('hidden');
  $('dimensionHistoryPrevious').disabled=true;$('dimensionHistoryNext').disabled=true;renderDimensionPermissions();
}
function clearDimensions() {
  DIM.token++;DIM.loadingToken=null;DIM.loadingActor=null;DIM.reads={};DIM.pending={};DIM.busy={};DIM.sources=[];DIM.measurements=[];DIM.bindings=[];DIM.drawings=[];
  DIM.measurement=null;DIM.formMeasurement=null;DIM.drawing=null;DIM.feature=null;DIM.measurementBaseline=null;DIM.bindingBaseline=null;DIM.formRevision++;DIM.measurementRevision++;
  ['dimensionMeasurementForm','dimensionBindingForm','dimensionApprovalForm'].forEach(function(id){$(id).reset();});
  ['dimensionMeasurement','dimensionBinding','dimensionBindingVersion','dimensionStagedSource','dimensionBindingMeasurement','dimensionDrawing','dimensionFeature','dimensionTarget'].forEach(function(id){$(id).replaceChildren();});
  ['dimensionMeasurementInfo','dimensionBindingMeasurementInfo'].forEach(function(id){$(id).replaceChildren();});
  ['dimensionMeasurementMessage','dimensionBindingMessage','dimensionSourceInfo','dimensionDrawingInfo','dimensionFeatureInfo'].forEach(function(id){$(id).textContent='';});
  $('dimensionMeasurementBaseline').textContent='输入测量 ID 后读取基线。';$('dimensionBindingBaseline').textContent='输入绑定 ID 后读取基线。';
  $('dimensionMessage').textContent='登录后可查看尺寸记录。';clearDimensionBindingDetail();renderDimensionPermissions();
}
async function dimensionResponse(response,allowMissing) {
  var data=await response.json();
  if(allowMissing && response.status===404 && data.detail && data.detail.error_code==='DIMENSION_RECORD_NOT_FOUND'){return null;}
  if(!response.ok){var code=data.detail&&data.detail.error_code;
    if(response.status===422&&!code){throw new Error('请核对必填字段、时间格式和依据长度。');}
    throw new Error(code?dimensionReason(code):('尺寸服务读取失败（'+response.status+'）。'));}
  return data;
}
function dimensionEvidence(container,rows,hashes) {
  container.replaceChildren();var dl=document.createElement('dl');rows.forEach(function(row){var dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=row[0];dd.textContent=row[1]===null?'未知':String(row[1]);dl.append(dt,dd);});container.append(dl);
  if(hashes&&hashes.length){var details=document.createElement('details'),summary=document.createElement('summary');summary.textContent='版本校验凭据';details.append(summary);hashes.forEach(function(row){var p=document.createElement('p');p.textContent=row[0]+' · SHA-256 '+row[1];details.append(p);});container.append(details);}
}
function renderDimensionMeasurement(record,container) {
  var m=record.payload;dimensionEvidence(container,[['测量',m.measurement_id+' · v'+m.source.record_version],['原始测量值',(m.value===null?'未知':m.value)+' '+m.unit],['零件 / 批次',m.part_id+' / '+m.batch_id],['工序 / 工艺版次',m.operation_no+' / '+m.process_revision],['特征 / 方法',m.feature_role+' / '+m.method],['测量时刻',m.measured_at],['来源',m.source.source_id+' · '+(m.source.provenance==='synthetic_demo'?'合成演示素材':'外部素材（登记声明）')],['登记人',record.actor.label+' · '+record.registered_at]],[['测量记录',record.sha256],['测量源文件',m.source.source_sha256]]);
}
async function loadDimensions(preselect) {
  if(!AUTH.user){clearDimensions();return;}
  // Login, direct navigation and the following hashchange may request this view together.
  if(!preselect&&DIM.loadingToken===DIM.token&&DIM.loadingActor===AUTH.user.user_id){return;}
  preselect=preselect||{};
  var actor=AUTH.user.user_id,token=++DIM.token,admin=hasRole('admin');
  DIM.loadingToken=token;DIM.loadingActor=actor;
  var selected={measurement:preselect.measurement||$('dimensionMeasurement').value,binding:preselect.binding||$('dimensionBinding').value,formMeasurement:preselect.bindingMeasurement||$('dimensionBindingMeasurement').value,drawing:$('dimensionDrawing').value,source:$('dimensionStagedSource').value};
  DIM.busy={};DIM.measurement=null;DIM.formMeasurement=null;DIM.drawing=null;DIM.feature=null;DIM.measurementBaseline=null;DIM.bindingBaseline=null;DIM.formRevision++;
  clearDimensionBindingDetail();['dimensionMeasurementInfo','dimensionBindingMeasurementInfo'].forEach(function(id){$(id).replaceChildren();});
  $('dimensionMeasurementBaseline').textContent='请重新读取测量登记基线。';$('dimensionBindingBaseline').textContent='请重新读取绑定登记基线。';
  $('dimensionMessage').textContent='正在读取当前尺寸记录，请稍候…';renderDimensionPermissions();
  try {
    var requests=[apiFetch('/v1/dimensions/measurements'),apiFetch('/v1/dimensions/bindings'),apiFetch('/v1/drawings')];if(admin){requests.push(apiFetch('/v1/dimensions/sources'));}
    var responses=await Promise.all(requests);var data=await Promise.all(responses.map(function(r){return dimensionResponse(r);}));
    if(!dimensionAlive(token,actor)){return;}DIM.measurements=data[0].measurements;DIM.bindings=data[1].bindings;DIM.drawings=data[2].drawings;DIM.sources=admin?data[3].sources:[];
    var measurements=DIM.measurements.map(function(m){return {value:m.measurement_id,text:m.measurement_id+' · 当前 v'+m.current_version};});
    dimensionOptions($('dimensionMeasurement'),measurements,'请选择已登记测量',selected.measurement);
    dimensionOptions($('dimensionBindingMeasurement'),measurements,'请明确选择本次测量',selected.formMeasurement);
    dimensionOptions($('dimensionBinding'),DIM.bindings.map(function(b){return {value:b.binding_id,text:b.binding_id+' · 当前 v'+b.current_version};}),'请选择绑定查看',selected.binding);
    dimensionOptions($('dimensionDrawing'),DIM.drawings.map(function(d){return {value:d.document_id,text:d.title+' · 当前 v'+d.document_version};}),'请明确选择当前图纸',selected.drawing);
    dimensionOptions($('dimensionStagedSource'),DIM.sources.map(function(s,i){return {value:String(i),text:s.name+' · '+(s.provenance==='synthetic_demo'?'合成素材':'外部素材')};}),'请选择测量素材','');
    dimensionOptions($('dimensionBindingVersion'),[],'先选择绑定');dimensionOptions($('dimensionFeature'),[],'先选择当前图纸');dimensionOptions($('dimensionTarget'),[],'先选择已确认标注');
    $('dimensionDrawingInfo').textContent='';$('dimensionFeatureInfo').textContent='';$('dimensionSourceInfo').textContent='';
    $('dimensionMessage').textContent='已读取 '+DIM.measurements.length+' 条测量、'+DIM.bindings.length+' 条绑定。'+((data[0].truncated||data[1].truncated)?'当前列表仅显示前 200 条记录。':'选择记录查看原值、批准与当前结果。');
    renderDimensionPermissions();
    await Promise.all([showDimensionMeasurement(),selectDimensionBinding(),readDimensionFormMeasurement(),readDimensionDrawing()]);
  }catch(error){if(dimensionAlive(token,actor)){$('dimensionMessage').textContent=error.message;}}
  finally{if(dimensionAlive(token,actor)&&DIM.loadingToken===token){DIM.loadingToken=null;DIM.loadingActor=null;renderDimensionPermissions();}}
}
async function showDimensionMeasurement() {
  DIM.measurement=null;$('dimensionMeasurementInfo').replaceChildren();var id=$('dimensionMeasurement').value,key=dimensionReadKey('measurement');if(!AUTH.user||!id){return;}
  var token=DIM.token,actor=AUTH.user.user_id;
  try{var data=await dimensionResponse(await apiFetch('/v1/dimensions/measurements/'+encodeURIComponent(id)));if(!dimensionReadAlive('measurement',key,token,actor)){return;}DIM.measurement=data.measurement;renderDimensionMeasurement(data.measurement,$('dimensionMeasurementInfo'));}
  catch(error){if(dimensionReadAlive('measurement',key,token,actor)){$('dimensionMeasurementInfo').textContent=error.message;}}
}
async function dimensionBaseline(kind) {
  if(!hasRole('admin')||DIM.loadingToken!==null){return;}var measurement=kind==='measurement',input=measurement?'dimensionMeasurementId':'dimensionBindingId',label=measurement?'dimensionMeasurementBaseline':'dimensionBindingBaseline',name=kind+'Baseline';
  var id=$(input).value,key=dimensionReadKey(name),token=DIM.token,actor=AUTH.user.user_id;DIM[name]=null;renderDimensionPermissions();
  if(!id||!$(input).checkValidity()){$(label).textContent='请输入有效的'+(measurement?'测量':'绑定')+' ID。';return;}$(label).textContent='正在读取当前基线…';
  try{var data=await dimensionResponse(await apiFetch('/v1/dimensions/'+(measurement?'measurements/':'bindings/')+encodeURIComponent(id)),true);
    if(!dimensionReadAlive(name,key,token,actor)||$(input).value!==id){return;}var record=data?(measurement?data.measurement:data.binding):null;
    DIM[name]={id:id,version:record?(measurement?record.payload.source.record_version:record.payload.binding_version):0,sha256:record?record.sha256:null};
    $(label).textContent=record?'已读取当前 v'+DIM[name].version+'；本次将登记 v'+(DIM[name].version+1)+'。':'该 ID 尚未登记；本次将登记 v1。';renderDimensionPermissions();
  }catch(error){if(dimensionReadAlive(name,key,token,actor)){$(label).textContent=error.message;}}
}
function showDimensionSource() {
  var source=$('dimensionStagedSource').value===''?null:DIM.sources[Number($('dimensionStagedSource').value)];
  $('dimensionSourceInfo').textContent=source?'源文件 SHA-256 '+source.source_sha256:'';renderDimensionPermissions();
}
async function readDimensionFormMeasurement() {
  DIM.formMeasurement=null;DIM.formRevision++;$('dimensionBindingMeasurementInfo').replaceChildren();renderDimensionPermissions();
  var id=$('dimensionBindingMeasurement').value,key=dimensionReadKey('formMeasurement');if(!AUTH.user||!id){return;}var token=DIM.token,actor=AUTH.user.user_id;
  try{var data=await dimensionResponse(await apiFetch('/v1/dimensions/measurements/'+encodeURIComponent(id)));if(!dimensionReadAlive('formMeasurement',key,token,actor)){return;}DIM.formMeasurement=data.measurement;renderDimensionMeasurement(data.measurement,$('dimensionBindingMeasurementInfo'));renderDimensionPermissions();}
  catch(error){if(dimensionReadAlive('formMeasurement',key,token,actor)){$('dimensionBindingMeasurementInfo').textContent=error.message;}}
}
async function readDimensionDrawing() {
  DIM.drawing=null;DIM.feature=null;DIM.formRevision++;dimensionReadKey('feature');$('dimensionDrawingInfo').textContent='';$('dimensionFeatureInfo').textContent='';
  dimensionOptions($('dimensionFeature'),[],'先选择当前图纸');dimensionOptions($('dimensionTarget'),[],'先选择已确认标注');renderDimensionPermissions();
  var id=$('dimensionDrawing').value,key=dimensionReadKey('drawing');if(!AUTH.user||!id){return;}var token=DIM.token,actor=AUTH.user.user_id;
  $('dimensionDrawingInfo').textContent='正在读取当前图纸与复核…';
  try{var data=await cadResponse(await apiFetch('/v1/drawings/'+encodeURIComponent(id)+'/features'));if(!dimensionReadAlive('drawing',key,token,actor)){return;}
    DIM.drawing=data;var v=data.version;var confirmed=data.association_reviews.filter(function(r){return r.latest_review&&r.latest_review.decision==='confirmed';});
    $('dimensionDrawingInfo').textContent=v.title+' · 图纸 v'+v.document_version+' · 源 SHA-256 '+v.source_sha256;
    dimensionOptions($('dimensionFeature'),confirmed.map(function(r){var f=data.features.find(function(x){return x.feature_id===r.feature_id;});return {value:r.feature_id,text:f.annotation_raw+' · 标注 '+f.locator.handle+' · 已复核圆 '+r.latest_review.selected_target};}),'请明确选择已确认标注');
    if(!confirmed.length){$('dimensionFeatureInfo').textContent='本图纸当前版本没有已确认关联；请先在「图纸与实体」完成认证复核。';}
  }catch(error){if(dimensionReadAlive('drawing',key,token,actor)){$('dimensionDrawingInfo').textContent=error.message;}}
}
function canonicalDimensionJson(value) {
  if(Array.isArray(value)){return '['+value.map(canonicalDimensionJson).join(',')+']';}
  if(value!==null&&typeof value==='object'){return '{'+Object.keys(value).sort().map(function(k){return JSON.stringify(k)+':'+canonicalDimensionJson(value[k]);}).join(',')+'}';}
  return JSON.stringify(value);
}
async function dimensionDigest(value) {
  var bytes=new TextEncoder().encode(canonicalDimensionJson(value));var hash=await crypto.subtle.digest('SHA-256',bytes);return Array.from(new Uint8Array(hash)).map(function(b){return b.toString(16).padStart(2,'0');}).join('');
}
async function selectDimensionFeature() {
  DIM.feature=null;DIM.formRevision++;var id=$('dimensionFeature').value,key=dimensionReadKey('feature'),token=DIM.token,actor=AUTH.user&&AUTH.user.user_id;
  $('dimensionFeatureInfo').textContent='';dimensionOptions($('dimensionTarget'),[],'请明确选择已复核对象');renderDimensionPermissions();if(!actor||!id||!DIM.drawing){return;}
  var drawing=DIM.drawing,feature=drawing.features.find(function(f){return f.feature_id===id;}),state=drawing.association_reviews.find(function(r){return r.feature_id===id;});
  if(!feature||!state||!state.latest_review||state.latest_review.decision!=='confirmed'){return;}
  var review=state.latest_review,projection={};['review_id','document_id','document_version','source_sha256','parser_version','bundle_sha256','feature_id','feature_sha256','decision','selected_target','reviewer_user_id','reviewer_roles','reason'].forEach(function(k){projection[k]=review[k];});
  try{var sha=await dimensionDigest(projection);if(!dimensionReadAlive('feature',key,token,actor)||DIM.drawing!==drawing){return;}
    DIM.feature={feature:feature,state:state,review:review,reviewSha256:sha};dimensionOptions($('dimensionTarget'),[{value:review.selected_target,text:'圆 '+review.selected_target+' · '+review.reviewer_label+' 已确认'}],'请明确选择本次绑定对象');
    $('dimensionFeatureInfo').textContent=feature.annotation_raw+' · '+feature.unit+' · 关联复核 '+review.review_id+' · 复核依据：'+review.reason;
  }catch(error){if(dimensionReadAlive('feature',key,token,actor)){$('dimensionFeatureInfo').textContent='关联校验凭据生成失败，请刷新。';}}
}
function dimensionPending(kind,body) {
  var signature=canonicalDimensionJson(body),actor=AUTH.user.user_id,pending=DIM.pending[kind];
  if(!pending||pending.signature!==signature||pending.actor!==actor){pending={signature:signature,actor:actor,id:'dimension-'+crypto.randomUUID().replaceAll('-','')};DIM.pending[kind]=pending;}
  return Object.assign({},body,{request_id:pending.id});
}
async function registerDimensionMeasurement(event) {
  event.preventDefault();if(!hasRole('admin')||DIM.loadingToken!==null||DIM.busy.measurement){return;}var baseline=DIM.measurementBaseline,source=$('dimensionStagedSource').value===''?null:DIM.sources[Number($('dimensionStagedSource').value)];
  if(!baseline||baseline.id!==$('dimensionMeasurementId').value||!source){$('dimensionMeasurementMessage').textContent='请读取当前登记基线并明确选择素材。';return;}
  var actor=AUTH.user.user_id,token=DIM.token,revision=DIM.measurementRevision;
  var body=dimensionPending('measurement',{measurement_id:baseline.id,source_path:source.source_path,source_sha256:source.source_sha256,provenance:source.provenance,previous_version:baseline.version,previous_sha256:baseline.sha256});
  DIM.busy.measurement=true;renderDimensionPermissions();$('dimensionMeasurementMessage').textContent='正在固定测量源与版本…';
  try{var data=await dimensionResponse(await apiFetch('/v1/dimensions/measurements',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}));
    if(!dimensionAlive(token,actor)||revision!==DIM.measurementRevision){return;}delete DIM.pending.measurement;DIM.busy.measurement=false;$('dimensionMeasurementForm').reset();
    var refreshedToken=DIM.token+1;await loadDimensions({measurement:data.measurement.payload.measurement_id});if(!dimensionAlive(refreshedToken,actor)){return;}
    $('dimensionMeasurementMessage').textContent='已登记 '+data.measurement.payload.measurement_id+' · v'+data.measurement.payload.source.record_version+'，原值与源校验凭据已固定。';
  }catch(error){if(dimensionAlive(token,actor)&&revision===DIM.measurementRevision){$('dimensionMeasurementMessage').textContent=error.message;}}
  finally{if(dimensionAlive(token,actor)){DIM.busy.measurement=false;renderDimensionPermissions();}}
}
function dimensionTimestamp(id) {
  var text=$(id).value.trim();if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(text)||!Number.isFinite(Date.parse(text))){throw new Error('有效期请使用包含时区的时间，例如 2026-09-01T00:00:00+08:00。');}return text;
}
async function registerDimensionBinding(event) {
  event.preventDefault();if(!hasRole('admin')||DIM.loadingToken!==null||DIM.busy.binding){return;}var baseline=DIM.bindingBaseline,m=DIM.formMeasurement,d=DIM.drawing,f=DIM.feature;
  if(!baseline||baseline.id!==$('dimensionBindingId').value||!m||!d||!f||!$('dimensionTarget').value){$('dimensionBindingMessage').textContent='请读取基线，并明确选择测量、当前图纸、已确认标注与对象。';return;}
  var actor=AUTH.user.user_id,token=DIM.token,revision=DIM.formRevision,body;
  try{var from=dimensionTimestamp('dimensionValidFrom'),to=dimensionTimestamp('dimensionValidTo');if(Date.parse(from)>=Date.parse(to)){throw new Error('有效期起点须早于终点。');}
    body=dimensionPending('binding',{binding_id:baseline.id,previous_version:baseline.version,previous_sha256:baseline.sha256,document_id:d.version.document_id,document_version:d.version.document_version,source_sha256:d.version.source_sha256,bundle_sha256:d.version.bundle_sha256,feature_id:f.feature.feature_id,feature_sha256:f.state.feature_sha256,target_handle:$('dimensionTarget').value,measurement_id:m.payload.measurement_id,measurement_record_version:m.payload.source.record_version,measurement_sha256:m.sha256,review_id:f.review.review_id,review_sha256:f.reviewSha256,valid_from:from,valid_to:to,rationale:$('dimensionBindingRationale').value.trim()});
    if(!body.rationale){throw new Error('请填写绑定依据。');}
  }catch(error){$('dimensionBindingMessage').textContent=error.message;return;}
  DIM.busy.binding=true;renderDimensionPermissions();$('dimensionBindingMessage').textContent='正在保存明确绑定与证据版本…';
  try{var data=await dimensionResponse(await apiFetch('/v1/dimensions/bindings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}));
    if(!dimensionAlive(token,actor)||revision!==DIM.formRevision){return;}delete DIM.pending.binding;DIM.busy.binding=false;$('dimensionBindingForm').reset();
    var refreshedToken=DIM.token+1;await loadDimensions({binding:data.binding.payload.binding_id});if(!dimensionAlive(refreshedToken,actor)){return;}$('dimensionBindingMessage').textContent='已登记 '+data.binding.payload.binding_id+' · v'+data.binding.payload.binding_version+'，等待独立使用批准。';
  }catch(error){if(dimensionAlive(token,actor)&&revision===DIM.formRevision){$('dimensionBindingMessage').textContent=error.message;}}
  finally{if(dimensionAlive(token,actor)){DIM.busy.binding=false;renderDimensionPermissions();}}
}
async function selectDimensionBinding() {
  clearDimensionBindingDetail();var id=$('dimensionBinding').value,item=DIM.bindings.find(function(b){return b.binding_id===id;});
  if(!id||!item){dimensionOptions($('dimensionBindingVersion'),[],'先选择绑定');return;}
  var versions=[];for(var n=item.current_version;n>=Math.max(1,item.current_version-199);n--){versions.push({value:String(n),text:'v'+n+(n===item.current_version?' · 当前':' · 历史')});}
  dimensionOptions($('dimensionBindingVersion'),versions,'请选择绑定版本',String(item.current_version));await showDimensionBinding();
}
function renderDimensionResult(container,result,replay) {
  container.replaceChildren();container.dataset.state=result.status;var title=document.createElement('h3');title.textContent=(replay?'当时核验':'当前核验')+' · '+(result.status==='compared'?'已完成单次比较':'已阻断');container.append(title);
  var p=document.createElement('p');p.textContent=result.reason?dimensionReason(result.reason):'使用批准与证据版本检查通过。';container.append(p);
  if(result.comparison){var c=result.comparison;var outcome=document.createElement('p');outcome.textContent=c.status==='within_annotation_range'?'测量值在该标注范围内。':'测量值在该标注范围外。';container.append(outcome);
    var values=document.createElement('p');values.textContent='测量 '+c.measurement_original.value+' '+c.measurement_original.unit+'；标注原文 '+c.annotation_original.raw+' · '+c.annotation_original.unit;container.append(values);
    var normalized=document.createElement('p');normalized.textContent='统一到 mm：测量 '+c.normalized_mm.measurement+'，标注范围 ['+c.normalized_mm.lower+', '+c.normalized_mm.upper+']。';container.append(normalized);}
  var note=document.createElement('p');note.className='field-hint';note.textContent='本地尺寸标注比较；不代表正式图纸审批、制造授权或产品质量结论。';container.append(note);
}
async function showDimensionBinding() {
  clearDimensionBindingDetail();var id=$('dimensionBinding').value,version=$('dimensionBindingVersion').value,key=dimensionReadKey('binding');if(!AUTH.user||!id||!version){return;}var token=DIM.token,actor=AUTH.user.user_id;
  $('dimensionBindingDetail').classList.remove('hidden');$('dimensionBindingInfo').textContent='正在核对绑定证据与当前状态…';
  try{var data=await dimensionResponse(await apiFetch('/v1/dimensions/bindings/'+encodeURIComponent(id)+'?version='+encodeURIComponent(version)));
    if(!dimensionReadAlive('binding',key,token,actor)){return;}DIM.binding=data;var record=data.binding,b=record.payload;
    dimensionEvidence($('dimensionBindingInfo'),[['绑定',b.binding_id+' · v'+b.binding_version],['图纸',b.document_id+' · v'+b.document_version+' / 图号 '+b.drawing_id+' / 图纸版次 '+b.drawing_revision],['标注 / 对象',b.feature_id+' / 圆 '+b.target_handle],['测量',b.measurement_id+' · v'+b.measurement_record_version],['零件 / 批次',b.part_id+' / '+b.batch_id],['工序 / 工艺版次',b.operation_no+' / '+b.process_revision],['特征 / 方法',b.feature_role+' / '+b.method],['有效期','['+b.valid_from+', '+b.valid_to+')'],['登记人',record.actor.label+' · '+record.registered_at],['绑定依据',b.rationale]],[['绑定',record.sha256],['图纸源文件',b.source_sha256],['测量',b.measurement_sha256],['关联复核',b.review_sha256]]);
    renderDimensionResult($('dimensionCurrentResult'),data.current_result,false);renderDimensionApprovals(data.approvals);renderDimensionPermissions();
    if(hasRole('approver')&&b.registrar_user_id===actor){$('saveDimensionApproval').disabled=true;$('dimensionApprovalMessage').textContent='当前账户为绑定登记人；请由独立账户批准。';}
    $('dimensionComparisonHistoryCard').classList.remove('hidden');await loadDimensionHistory(0);
  }catch(error){if(dimensionReadAlive('binding',key,token,actor)){$('dimensionBindingInfo').textContent=error.message;}}
}
function renderDimensionApprovals(events) {
  var container=$('dimensionApprovalHistory');container.replaceChildren();var title=document.createElement('h3');title.textContent='本绑定版本的使用批准历史';container.append(title);
  if(!events.length){var empty=document.createElement('p');empty.textContent='尚无使用批准记录。';container.append(empty);}
  var labels={approved:'批准',rejected:'退回',revoked:'撤销'};events.forEach(function(item){var e=item.payload,p=document.createElement('p');p.textContent=labels[e.decision]+' · '+e.approver_user_id+' · '+e.created_at+' · '+e.reason;container.append(p);});
}
async function saveDimensionApproval(event) {
  event.preventDefault();if(!hasRole('approver')||DIM.loadingToken!==null||!DIM.binding||DIM.busy.approval){return;}var selected=DIM.binding,b=selected.binding.payload,actor=AUTH.user.user_id,token=DIM.token,key=DIM.reads.binding;
  var decision=$('dimensionApprovalDecision').value,reason=$('dimensionApprovalReason').value.trim();if(!decision||!reason){$('dimensionApprovalMessage').textContent='请明确选择决定并填写依据。';return;}
  var last=selected.approvals.length?selected.approvals[selected.approvals.length-1].payload:null;
  if(decision==='revoked'&&(!last||last.decision!=='approved')){$('dimensionApprovalMessage').textContent='本版本当前没有可撤销的批准。';return;}
  var body=dimensionPending('approval',{binding_version:b.binding_version,binding_sha256:selected.binding.sha256,previous_approval_id:last?last.approval_id:null,decision:decision,reason:reason});
  DIM.busy.approval=true;renderDimensionPermissions();$('dimensionApprovalMessage').textContent='正在保存使用批准决定…';
  try{await dimensionResponse(await apiFetch('/v1/dimensions/bindings/'+encodeURIComponent(b.binding_id)+'/approvals',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}));
    if(!dimensionReadAlive('binding',key,token,actor)){return;}delete DIM.pending.approval;DIM.busy.approval=false;$('dimensionApprovalForm').reset();await showDimensionBinding();
    if(dimensionAlive(token,actor)&&DIM.binding&&DIM.binding.binding.payload.binding_id===b.binding_id&&DIM.binding.binding.payload.binding_version===b.binding_version){$('dimensionApprovalMessage').textContent='已保存使用批准决定，当前核验已刷新。';}
  }catch(error){if(dimensionReadAlive('binding',key,token,actor)){$('dimensionApprovalMessage').textContent=error.message;}}
  finally{if(dimensionAlive(token,actor)){DIM.busy.approval=false;renderDimensionPermissions();}}
}
async function saveDimensionComparison() {
  if(!hasRole('admin')||DIM.loadingToken!==null||!DIM.binding||DIM.busy.comparison){return;}var selected=DIM.binding,b=selected.binding.payload,actor=AUTH.user.user_id,token=DIM.token,key=DIM.reads.binding;
  var body=dimensionPending('comparison',{binding_version:b.binding_version,binding_sha256:selected.binding.sha256});DIM.busy.comparison=true;renderDimensionPermissions();$('dimensionComparisonMessage').textContent='正在核对当前版本并保存快照…';
  try{var data=await dimensionResponse(await apiFetch('/v1/dimensions/bindings/'+encodeURIComponent(b.binding_id)+'/comparisons',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}));
    if(!dimensionReadAlive('binding',key,token,actor)){return;}delete DIM.pending.comparison;DIM.busy.comparison=false;renderDimensionResult($('dimensionCurrentResult'),data.payload.result,false);
    $('dimensionComparisonMessage').textContent='已保存本次核验快照 '+data.payload.comparison_id+'。';await loadDimensionHistory(0);
  }catch(error){if(dimensionReadAlive('binding',key,token,actor)){$('dimensionComparisonMessage').textContent=error.message;}}
  finally{if(dimensionAlive(token,actor)){DIM.busy.comparison=false;renderDimensionPermissions();}}
}
async function loadDimensionHistory(offset) {
  if(!AUTH.user||!DIM.binding){return;}var id=DIM.binding.binding.payload.binding_id,token=DIM.token,actor=AUTH.user.user_id,key=dimensionReadKey('history'),bindingKey=DIM.reads.binding;
  $('dimensionHistoryPrevious').disabled=true;$('dimensionHistoryNext').disabled=true;$('dimensionComparisonHistory').replaceChildren();$('dimensionHistoryMessage').textContent='正在读取核验历史…';
  try{var data=await dimensionResponse(await apiFetch('/v1/dimensions/bindings/'+encodeURIComponent(id)+'/comparisons?offset='+offset+'&limit=10'));
    if(!dimensionReadAlive('history',key,token,actor)||bindingKey!==DIM.reads.binding){return;}DIM.historyOffset=offset;DIM.historyNext=data.next_offset;
    var container=$('dimensionComparisonHistory');if(!data.comparisons.length){container.textContent='本绑定尚无已保存核验快照。';}
    data.comparisons.forEach(function(item){var row=document.createElement('div');row.className='dimension-history-item';var p=document.createElement('p');p.textContent='v'+item.binding_version+' · '+item.registered_at+' · '+(item.status==='compared'?(item.comparison.status==='within_annotation_range'?'在该标注范围内':'在该标注范围外'):dimensionReason(item.reason));var button=document.createElement('button');button.type='button';button.className='secondary';button.textContent='回放 '+item.comparison_id;button.addEventListener('click',function(){showDimensionReplay(item.comparison_id,id,bindingKey);});row.append(p,button);container.append(row);});
    $('dimensionHistoryPage').textContent='第 '+(Math.floor(offset/10)+1)+' 页';$('dimensionHistoryPrevious').disabled=offset===0;$('dimensionHistoryNext').disabled=data.next_offset===null;$('dimensionHistoryMessage').textContent='';
  }catch(error){if(dimensionReadAlive('history',key,token,actor)){$('dimensionHistoryMessage').textContent=error.message;}}
}
async function showDimensionReplay(id,bid,bindingKey) {
  if(!AUTH.user||!DIM.binding||DIM.reads.binding!==bindingKey){return;}var token=DIM.token,actor=AUTH.user.user_id,key=dimensionReadKey('replay');$('dimensionReplay').textContent='正在核验历史快照…';
  try{var data=await dimensionResponse(await apiFetch('/v1/dimensions/comparisons/'+encodeURIComponent(id)));
    if(!dimensionReadAlive('replay',key,token,actor)||DIM.reads.binding!==bindingKey||DIM.binding.binding.payload.binding_id!==bid){return;}var p=data.payload,b=p.evaluation.binding.payload,m=p.evaluation.measurement;
    var container=$('dimensionReplay');dimensionEvidence(container,[['历史快照',p.comparison_id+' · '+data.registered_at],['当时核验时刻',p.evaluation.context.evaluated_at],['绑定 / 图纸版本',b.binding_id+' v'+b.binding_version+' / '+b.document_id+' v'+b.document_version],['原始测量',m.value+' '+m.unit+' · '+m.measurement_id+' v'+m.source.record_version],['当时批准记录',p.evaluation.approvals.length+' 条'],['快照保存人',data.actor.label]],[['完整保存记录',data.record_sha256],['当时绑定',p.evaluation.binding.sha256]]);
    var result=document.createElement('div');result.className='dimension-result';renderDimensionResult(result,p.result,true);container.append(result);
  }catch(error){if(dimensionReadAlive('replay',key,token,actor)&&DIM.reads.binding===bindingKey){$('dimensionReplay').textContent=error.message;}}
}
$('refreshDimensions').addEventListener('click',function(){loadDimensions();});
$('dimensionMeasurement').addEventListener('change',showDimensionMeasurement);
$('dimensionBinding').addEventListener('change',selectDimensionBinding);
$('dimensionBindingVersion').addEventListener('change',showDimensionBinding);
$('dimensionMeasurementId').addEventListener('input',function(){DIM.measurementBaseline=null;dimensionReadKey('measurementBaseline');$('dimensionMeasurementBaseline').textContent='请读取当前登记基线。';renderDimensionPermissions();});
$('dimensionBindingId').addEventListener('input',function(){DIM.bindingBaseline=null;dimensionReadKey('bindingBaseline');$('dimensionBindingBaseline').textContent='请读取当前登记基线。';renderDimensionPermissions();});
$('dimensionMeasurementBaselineRefresh').addEventListener('click',function(){dimensionBaseline('measurement');});
$('dimensionBindingBaselineRefresh').addEventListener('click',function(){dimensionBaseline('binding');});
$('dimensionStagedSource').addEventListener('change',showDimensionSource);
$('dimensionBindingMeasurement').addEventListener('change',readDimensionFormMeasurement);
$('dimensionDrawing').addEventListener('change',readDimensionDrawing);
$('dimensionFeature').addEventListener('change',selectDimensionFeature);
$('dimensionTarget').addEventListener('change',renderDimensionPermissions);
$('dimensionMeasurementForm').addEventListener('input',function(){DIM.measurementRevision++;});
$('dimensionBindingForm').addEventListener('input',function(){DIM.formRevision++;});
$('dimensionMeasurementForm').addEventListener('submit',registerDimensionMeasurement);
$('dimensionBindingForm').addEventListener('submit',registerDimensionBinding);
$('dimensionApprovalForm').addEventListener('submit',saveDimensionApproval);
$('saveDimensionComparison').addEventListener('click',saveDimensionComparison);
$('refreshDimensionHistory').addEventListener('click',function(){loadDimensionHistory(0);});
$('dimensionHistoryPrevious').addEventListener('click',function(){loadDimensionHistory(Math.max(0,DIM.historyOffset-10));});
$('dimensionHistoryNext').addEventListener('click',function(){if(DIM.historyNext!==null){loadDimensionHistory(DIM.historyNext);}});

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
  if (retrieval.coverage) {
    var coverage = retrieval.coverage, rows = coverage.documents || [];
    notes.push('所选文件 ' + rows.length + ' 份 · 有相关证据 ' + rows.filter(function(row){return row.retrieved_chunks > 0;}).length + ' 份');
    if (data.answer_coverage) {
      notes.push('答案引用 ' + (data.answer_coverage.cited_document_ids || []).length + ' 份');
      if ((data.answer_coverage.uncited_document_ids || []).length) { notes.push('部分所选文件未被答案引用，请逐份核对'); }
    }
    if ((coverage.missing_document_ids || []).length) { notes.push('部分所选文件缺少相关证据，本次无法完成联合问答'); }
  }
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
  var content = document.createElement('div'); content.className = 'field-content'; content.textContent = '正在读取…';
  var reviewHint = document.createElement('p'); reviewHint.className = 'field-hint';
  reviewHint.textContent = '复核身份由登录账户记录。确认字段只说明提取忠于原文；正式审批需要另一位有审批权限的账户。两种记录都不覆盖原文声明或自动裁定来源冲突。';
  var form = document.createElement('div'); form.className = 'review-form';
  var reviewer = document.createElement('input'); reviewer.value = AUTH.user ? AUTH.user.display_name : '登录后可提交复核/审批'; reviewer.readOnly=true; reviewer.setAttribute('aria-label', '登录身份');
  var reason = document.createElement('textarea'); reason.placeholder = '核对原页后填写依据或问题'; reason.maxLength = 1000; reason.setAttribute('aria-label', '复核依据');
  form.append(reviewer, reason);
  dialog.append(close, title, hint, reviewHint, form, content); document.body.append(dialog); dialog.showModal();
  try {
    var response = await apiFetch('/v1/documents/' + encodeURIComponent(source.document_id) + '/facts');
    if (!response.ok) { throw new Error('字段读取失败（' + response.status + '）'); }
    var data = await response.json();
    if (data.document_version !== source.document_version || data.source_sha256 !== source.document_sha256 || data.parser_version !== source.parser_version) {
      throw new Error('当前文档版本与此运行记录不同，请重新提问。');
    }
    content.replaceChildren();
    if (!data.facts.length) { content.textContent = '当前版本没有可定位的识别字段。'; return; }
    var table = document.createElement('table'), header = document.createElement('tr');
    ['实体 / 批次', '字段', '原文值', '标准化值', '复核 / 正式审批', '出处 / 操作'].forEach(function (text) {
      var th = document.createElement('th'); th.textContent = text; header.append(th);
    });
    var head = document.createElement('thead'); head.append(header); table.append(head);
    var body = document.createElement('tbody'), preview = document.createElement('div'); preview.className = 'field-preview';
    var previewRow = document.createElement('tr'), previewCell = document.createElement('td'); previewCell.colSpan = 6; previewCell.append(preview); previewRow.append(previewCell);
    data.facts.forEach(function (fact) {
      var row = document.createElement('tr');
      var normalized = fact.normalized_range ? fact.normalized_range.min + '–' + fact.normalized_range.max : fact.normalized_value === null ? '未标准化' : fact.normalized_value;
      var entity = fact.entity_name + (fact.batch_id ? ' / ' + fact.batch_id : '');
      var industrialFact = ['industrial-native-v1', 'industrial-labelled-v2'].includes(fact.extraction_version);
      if (industrialFact) {
        entity += '\n工序 ' + fact.operation_no + (fact.measurement_id ? ' · ' + fact.measurement_id : '') + '\n版次 ' + fact.source_revision + '\n原文审批声明：' + ({approved:'已批准',unapproved:'未批准'}[fact.approval_status] || fact.approval_status);
      }
      [entity, fact.raw_header === 'holding_temperature' ? '保温温度' : fact.raw_header, fact.raw_value + (industrialFact ? ' ' + (fact.raw_unit || '') : ''), normalized + (fact.normalized_unit === '1' ? '（比例）' : fact.normalized_unit || '')].forEach(function (text) {
        var cell = document.createElement('td'); cell.textContent = text; row.append(cell);
      });
      var status = document.createElement('td'), labels = {confirmed:'原文已核对',needs_review:'存疑',rejected:'已退回'};
      function showReview() {
        var latest = fact.latest_review;
        status.textContent = latest ? labels[latest.decision] + ' · ' + latest.reviewer_label + (latest.reviewer_user_id?'（账户）':'（历史未认证）') + '\n' + latest.reason + '\n' + latest.created_at : '尚未复核';
        var approvals={approved:'已批准',rejected:'未批准',revoked:'已撤销',stale:'复核已变化，审批失效',not_approved:'尚未审批'};
        status.textContent+='\n正式审批：'+(approvals[fact.formal_approval_status] || '尚未审批');
        if(fact.latest_approval){status.textContent+=' · '+fact.latest_approval.approver_label;}
      }
      showReview(); row.append(status);
      var cell = document.createElement('td'), button = document.createElement('button'); button.className = 'secondary'; button.textContent = '定位原页';
      button.addEventListener('click', function () { row.after(previewRow); locateDocumentFact(fact, preview); }); cell.append(button);
      var message = document.createElement('p'); message.className = 'field-hint'; message.setAttribute('role','status');
      var url = '/v1/documents/' + encodeURIComponent(source.document_id) + '/facts/' + encodeURIComponent(fact.fact_id) + '/reviews';
      var pending = null, controls = [];
      [['confirmed','确认原文'],['needs_review','标记存疑'],['rejected','退回字段']].forEach(function (item) {
        var action = document.createElement('button'); action.className = 'secondary'; action.textContent = item[1]; controls.push(action);
        action.disabled=!hasRole('reviewer');
        action.addEventListener('click', async function () {
          if (!hasRole('reviewer') || !reason.value.trim()) { message.textContent = '请用有复核权限的账户登录，填写依据并核对原页。'; return; }
          var payload = {document_version:data.document_version,source_sha256:data.source_sha256,parser_version:data.parser_version,
            payload_sha256:fact.payload_sha256,previous_review_id:fact.latest_review ? fact.latest_review.review_id : null,
            decision:item[0],reason:reason.value.trim()};
          var signature = JSON.stringify(payload);
          if (!pending || pending.signature !== signature) { pending = {signature:signature,request_id:crypto.randomUUID()}; }
          payload.request_id = pending.request_id; controls.forEach(function (b) { b.disabled = true; }); message.textContent = '正在保存…';
          try {
            var response = await apiFetch(url, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
            if (!response.ok) { throw new Error(response.status === 409 ? '字段版本或复核记录已变化，请关闭后重新打开。' : '保存失败（' + response.status + '）'); }
            var saved=await response.json();
            if(!saved.field_state){throw new Error('字段版本已变化，请重新打开。');}
            Object.assign(fact,saved.field_state);pending=null;showReview();message.textContent='已保存复核记录。';
          } catch (error) { message.textContent = error.message; }
          finally { controls.forEach(function (b) { b.disabled = !hasRole('reviewer'); }); }
        }); cell.append(action);
      });
      var history = document.createElement('button'); history.className = 'secondary'; history.textContent = '复核历史';
      history.addEventListener('click', async function () {
        try {
          var response = await apiFetch(url); if (!response.ok) { throw new Error('历史读取失败（' + response.status + '）'); }
          var records = (await response.json()).reviews;
          row.after(previewRow); preview.replaceChildren(); var heading = document.createElement('h4'); heading.textContent = fact.entity_name + ' · ' + fact.raw_header + ' · 复核历史';
          var detail = document.createElement('pre'); detail.className = 'review-history';
          detail.textContent = records.length ? records.map(function (r) { return r.created_at + ' · ' + labels[r.decision] + ' · ' + r.reviewer_label + '\n' + r.reason + '\n版本 ' + r.document_version + ' · ' + r.review_id; }).join('\n\n') : '暂无复核记录';
          preview.append(heading,detail);
        } catch (error) { message.textContent = error.message; }
      }); cell.append(history,message); row.append(cell); body.append(row);
      addApprovalControls(fact,source,data,cell,reason,showReview,message);
    });
    table.append(body); content.append(table);
    var unresolved = ((data.quality || {}).unresolved || []).length;
    if (unresolved) { hint.textContent += ' 另有 ' + unresolved + ' 处字段或表头未能确认。'; }
  } catch (error) { content.textContent = error.message; }
}
function addApprovalControls(fact,source,data,cell,reason,showReview,message) {
  var url='/v1/documents/'+encodeURIComponent(source.document_id)+'/facts/'+encodeURIComponent(fact.fact_id)+'/approvals';
  var pending=null;
  [['approved','正式批准'],['rejected','不予批准'],['revoked','撤销批准']].forEach(function(item){
    var button=document.createElement('button');button.className='secondary';button.textContent=item[1];button.disabled=!hasRole('approver');
    button.addEventListener('click',async function(){
      if(!hasRole('approver') || !reason.value.trim()){message.textContent='审批需要审批权限和依据。';return;}
      var review=fact.latest_review;
      if(!review || !review.reviewer_user_id || review.decision!=='confirmed'){message.textContent='需要先由登录账户确认提取。';return;}
      if(review.reviewer_user_id===AUTH.user.user_id){message.textContent='复核人不能审批自己的记录，请由另一位审批人处理。';return;}
      var payload={document_version:data.document_version,source_sha256:data.source_sha256,parser_version:data.parser_version,
        payload_sha256:fact.payload_sha256,review_id:review.review_id,previous_approval_id:fact.latest_approval?fact.latest_approval.approval_id:null,
        decision:item[0],reason:reason.value.trim()};
      var signature=JSON.stringify(payload);
      if(!pending || pending.signature!==signature){pending={signature:signature,request_id:crypto.randomUUID()};}
      payload.request_id=pending.request_id;button.disabled=true;
      try {
        var r=await apiFetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
        if(!r.ok){throw new Error(r.status===409?'复核/审批状态或版本已变化，请关闭后重新打开。':'审批未保存（'+r.status+'）。');}
        var saved=await r.json();if(!saved.field_state){throw new Error('字段版本已变化，请重新打开。');}
        Object.assign(fact,saved.field_state);pending=null;showReview();message.textContent='已保存独立审批记录。';
      }catch(error){message.textContent=error.message;}finally{button.disabled=!hasRole('approver');}
    });cell.append(button);
  });
  var history=document.createElement('button');history.className='secondary';history.textContent='审批历史';
  history.addEventListener('click',async function(){
    try{var r=await apiFetch(url);if(!r.ok){throw new Error('审批历史读取失败。');}var records=(await r.json()).approvals;
      message.textContent=records.length?records.map(function(a){return a.created_at+' · '+a.decision+' · '+a.approver_label+'\n'+a.reason+'\n'+a.approval_id;}).join('\n\n'):'暂无审批记录';
    }catch(error){message.textContent=error.message;}
  });cell.append(history);
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
  var names = {within_range:'范围内',out_of_range:'超限',needs_review:'待复核',insufficient_evidence:'证据不足',consistent:'一致',resolved_equivalent:'单位换算后等价',resolved_by_authority:'按登记规则裁决',unresolved:'数值冲突，未消解'};
  var reasons = {MULTIPLE_APPLICABLE_REVISIONS:'工艺有效期重叠',NO_APPLICABLE_REQUIREMENT:'没有适用工艺',UNKNOWN_UNIT:'单位未登记',UNIT_RANGE_OR_METHOD_UNRESOLVED:'规范口径或单位未确认',REPORT_MISSING_OR_AMBIGUOUS:'报告缺失或不唯一',REPORT_KEY_MISMATCH:'报告实体/批次不匹配',METHOD_TIME_OR_APPROVAL_MISMATCH:'测量口径或审批不匹配',NO_REGISTERED_AUTHORITY_RULE:'缺少裁定依据',REGISTERED_AUTHORITY_RULE:'依据已批准的权威规则裁决',AUTHORITY_RULE_STALE:'权威规则依据或版本已变化',AUTHORITY_REGISTRY_INVALID:'权威登记完整性校验失败，保持未消解',AUTHORITY_RULE_OVERLAP:'多条权威规则重叠，需人工处理',VALUES_NOT_COMPARABLE:'值或单位不可比较',SELECTED_BY_BATCH_TIME:'按批次时间选适用版本'};
  var table=document.createElement('table'),head=document.createElement('thead'),header=document.createElement('tr');
  ['批次 / 测量','生产原值','适用工艺','工艺核验','报告原值','来源比较','说明 / 出处'].forEach(function (t) { var th=document.createElement('th');th.textContent=t;header.append(th); });head.append(header);table.append(head);
  var body=document.createElement('tbody'), facts={};data.facts.forEach(function(f){facts[f.fact_id]=f;});
  data.rows.forEach(function(row){
    var tr=document.createElement('tr');
    [row.batch_id+' / '+row.measurement_id,row.value+' '+row.unit,row.applicable_revision||'未确定',names[row.compliance],row.report_value===null?'缺失':row.report_value+' '+row.report_unit,names[row.agreement]].forEach(function(t){var td=document.createElement('td');td.textContent=t;tr.append(td);});
    if(row.compliance==='out_of_range'||row.agreement==='unresolved'){tr.className='industrial-conflict';}
    var cell=document.createElement('td'),note=document.createElement('div');note.className='field-hint';note.textContent=row.issues.map(function(i){return reasons[i.reason]||i.reason;}).join('；');cell.append(note);
    if(row.authority){var authorityNote=document.createElement('p');authorityNote.className='field-hint';authorityNote.textContent='采用 '+(row.selected_fact_ref.kind==='sql_measurement'?'生产记录':'质检报告')+' '+row.selected_value+' '+row.selected_unit+'；'+row.authority.registration.rationale+'；规则 '+row.authority.rule_id+'，批准人 '+row.authority.approver_label;cell.append(authorityNote);var basisLink=document.createElement('a');basisLink.textContent='裁定依据页';basisLink.href='/v1/documents/'+encodeURIComponent(row.authority.registration.basis.document_id)+'/file#page='+row.authority.registration.basis.page_no;basisLink.target='_blank';basisLink.rel='noopener';cell.append(basisLink);}
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
    if(window.applyDocumentSelection){request=window.applyDocumentSelection(request)||request;}
    var response = await apiFetch('/v1/chat/stream', {
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
      apiFetch('/v1/runs/' + encodeURIComponent(id)),
      apiFetch('/v1/runs/' + encodeURIComponent(id) + '/events'),
      apiFetch('/v1/runs/' + encodeURIComponent(id) + '/evidence')
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
    var response = await apiFetch('/v1/diagnostics/sql', { method: 'POST' });
    if (!response.ok) { throw new Error('HTTP ' + response.status); }
    var result = await response.json(); $('replayId').value = result.run_id; await replay();
  } catch (err) {
    status('自检失败', 'fail'); $('answer').textContent = '数据库链路自检失败：' + err.message;
  } finally { $('diagnostic').disabled = false; }
}
async function loadRuns() {
  var area = $('runList');
  try {
    var response = await apiFetch('/v1/runs?limit=20');
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
    var response = await apiFetch('/v1/documents'); if (!response.ok) { throw new Error('HTTP ' + response.status); }
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


/* ---------------- 显式来源权威规则 ---------------- */
async function authorityResponse(response) {
  var data=await response.json();
  if(!response.ok){var code=(data.detail||{}).error_code;var labels={AUTHORITY_CONTEXT_CHANGED:'测量、报告或审批已变化，请重新读取。',APPROVED_FIELD_REQUIRED:'报告字段需要先完成认证复核与独立审批。',AUTHORITY_BASIS_INVALID:'依据文件版本或页码无效。',AUTHORITY_VALIDITY_INVALID:'有效期需含时区、起点早于终点，并覆盖生产时间。',AUTHORITY_CREATOR_CANNOT_APPROVE:'登记人不能审批自己的规则。',AUTHORITY_STATE_CHANGED:'审批状态已变化，请刷新规则。',PERMISSION_DENIED:'当前账户没有操作权限。',LOGIN_REQUIRED:'请先登录。'};throw new Error(labels[code]||('操作失败（'+response.status+'），请刷新后重试。'));}
  return data;
}
function selectedAuthorityCandidate() {return AUTHORITY.candidates[Number($('authorityCandidate').value)];}
function fillAuthorityCandidate() {
  var item=selectedAuthorityCandidate();if(!item){$('authorityCandidateInfo').textContent='暂无可登记的数值冲突。';return;}
  $('authorityCandidateInfo').textContent='生产记录 '+item.production_value+' '+item.production_unit+'；报告 '+item.report_value+' '+item.report_unit+'。'+(item.eligible?'报告字段已独立审批。':'报告字段尚未独立审批，请先到字段复核处理。');
  $('authorityFrom').value=item.valid_from;$('authorityTo').value=item.valid_to;
  $('authorityBasis').value=item.basis_suggestion.document_id;$('authorityPage').value=item.basis_suggestion.page_no;
  $('registerAuthority').disabled=!hasRole('admin') || !item.eligible;
}
async function loadAuthorityCandidates() {
  if(!hasRole('admin')){$('authorityMessage').textContent='管理员可登记规则。';return;}
  try {
    var data=await authorityResponse(await apiFetch('/v1/authority/candidates?question='+encodeURIComponent($('authorityQuestion').value)));
    var docs=await authorityResponse(await apiFetch('/v1/documents'));
    AUTHORITY.candidates=data.candidates;AUTHORITY.documents=docs.documents.filter(function(d){return d.file_type==='pdf';});
    $('authorityCandidate').replaceChildren();AUTHORITY.candidates.forEach(function(item,index){var o=document.createElement('option');o.value=index;o.textContent=item.context.scope.part_id+' / '+item.context.scope.batch_id+' / '+item.context.scope.measurement_id;$('authorityCandidate').append(o);});
    $('authorityBasis').replaceChildren();AUTHORITY.documents.forEach(function(d){var o=document.createElement('option');o.value=d.document_id;o.textContent=d.title+' · v'+d.doc_version;$('authorityBasis').append(o);});
    fillAuthorityCandidate();$('authorityMessage').textContent='已读取当前冲突。';
  }catch(error){$('authorityMessage').textContent=error.message;}
}
async function registerAuthority(event) {
  event.preventDefault();var item=selectedAuthorityCandidate(),doc=AUTHORITY.documents.find(function(d){return d.document_id===$('authorityBasis').value;});
  if(!hasRole('admin') || !item || !doc || !item.eligible || !$('authoritySelected').value){$('authorityMessage').textContent='请选择已审批的冲突测量、采用来源和依据文件。';return;}
  var payload={question:$('authorityQuestion').value,measurement_id:item.context.scope.measurement_id,candidate_sha256:item.candidate_sha256,selected_source:$('authoritySelected').value,valid_from:$('authorityFrom').value,valid_to:$('authorityTo').value,basis:{document_id:doc.document_id,document_version:doc.doc_version,source_sha256:doc.sha256,page_no:Number($('authorityPage').value)},rationale:$('authorityRationale').value.trim()};
  var signature=JSON.stringify(payload);if(!AUTHORITY.pending || AUTHORITY.pending.signature!==signature){AUTHORITY.pending={signature:signature,request_id:crypto.randomUUID()};}payload.request_id=AUTHORITY.pending.request_id;
  $('registerAuthority').disabled=true;
  try{await authorityResponse(await apiFetch('/v1/authority/rules',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}));AUTHORITY.pending=null;$('authorityMessage').textContent='规则已登记，等待另一账户批准启用。';await loadAuthority();}
  catch(error){$('authorityMessage').textContent=error.message;}
  finally{$('registerAuthority').disabled=!hasRole('admin');}
}
async function loadAuthority() {
  var area=$('authorityRules');if(!AUTH.user){area.textContent='登录后可查看规则与历史。';return;}
  area.textContent='正在读取规则…';
  try {
    var data=await authorityResponse(await apiFetch('/v1/authority/rules'));area.replaceChildren();
    if(!data.rules.length){area.textContent='尚未登记规则；来源冲突保持未消解。';}
    data.rules.forEach(function(rule){
      var p=rule.payload,card=document.createElement('article');card.className='authority-rule';var heading=document.createElement('h3');if(!rule.payload_valid){heading.textContent='登记完整性校验失败：'+rule.rule_id;card.append(heading);area.append(card);return;}
      var states={approved:'已批准',rejected:'未批准',revoked:'已撤销'};heading.textContent=p.context.scope.measurement_id+' · 采用'+(p.selected_source==='report'?'质检报告':'生产记录')+' · '+(states[rule.decision]||'待审批');
      var details=document.createElement('p');details.textContent=p.context.scope.part_id+' / '+p.context.scope.batch_id+' · 工序 '+p.context.scope.operation_no+' · 方法 '+p.context.scope.method+' · '+p.valid_from+' 至 '+p.valid_to+'；登记人 '+rule.creator_label;
      var reason=document.createElement('p');reason.textContent='裁定依据：'+p.rationale;var link=document.createElement('a');link.textContent='查看依据第 '+p.basis.page_no+' 页';link.href='/v1/documents/'+encodeURIComponent(p.basis.document_id)+'/file#page='+p.basis.page_no;link.target='_blank';link.rel='noopener';
      var label=document.createElement('label'),input=document.createElement('textarea');input.id='reason-'+rule.rule_id;label.htmlFor=input.id;label.textContent='规则审批依据';input.maxLength=1000;
      var actions=document.createElement('div');actions.className='actions';var message=document.createElement('pre');message.className='review-history';var pending=null;
      var controls=[];[['approved','批准启用'],['rejected','不批准规则'],['revoked','撤销规则']].forEach(function(choice){var b=document.createElement('button');b.textContent=choice[1];b.className='secondary';controls.push(b);b.disabled=!hasRole('approver') || rule.creator_user_id===AUTH.user.user_id;
        b.addEventListener('click',async function(){if(!input.value.trim()){message.textContent='请填写规则审批依据。';return;}var body={decision:choice[0],reason:input.value.trim(),previous_event_id:rule.event_id};var sig=JSON.stringify(body);if(!pending || pending.signature!==sig){pending={signature:sig,request_id:crypto.randomUUID()};}body.request_id=pending.request_id;controls.forEach(function(c){c.disabled=true;});try{await authorityResponse(await apiFetch('/v1/authority/rules/'+encodeURIComponent(rule.rule_id)+'/events',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}));await loadAuthority();$('authorityMessage').textContent='规则审批已保存；后续运行会重新核验适用条件。';}catch(error){message.textContent=error.message;controls.forEach(function(c){c.disabled=!hasRole('approver') || rule.creator_user_id===AUTH.user.user_id;});}});actions.append(b);
      });
      var history=document.createElement('button');history.className='secondary';history.textContent='规则审批历史';history.addEventListener('click',async function(){try{var records=(await authorityResponse(await apiFetch('/v1/authority/rules/'+encodeURIComponent(rule.rule_id)+'/events'))).events;message.textContent=records.length?records.map(function(e){return e.created_at+' · '+states[e.decision]+' · '+e.actor_label+'\n'+e.reason;}).join('\n\n'):'暂无审批记录。';}catch(error){message.textContent=error.message;}});actions.append(history);card.append(heading,details,reason,link,label,input,actions,message);area.append(card);
    });
  }catch(error){area.textContent=error.message;}
}
$('loadAuthorityCandidates').addEventListener('click',loadAuthorityCandidates);
$('authorityCandidate').addEventListener('change',fillAuthorityCandidate);
$('authorityForm').addEventListener('submit',registerAuthority);
$('refreshAuthority').addEventListener('click',loadAuthority);

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
  if(!hasRole('admin')){notice('warn','模型接入需要管理员登录。');return;}
  try {
    var response = await apiFetch('/v1/model/profiles');
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
    var response = await apiFetch(editing ? '/v1/model/profiles/' + encodeURIComponent(editing) : '/v1/model/profiles', {
      method: editing ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    var data = await response.json();
    if (!response.ok) { throw new Error((data.detail && data.detail.error_code) || ('HTTP ' + response.status)); }
    if (activate || !STATE.active) {
      await apiFetch('/v1/model/profiles/' + encodeURIComponent(data.id) + '/activate', { method: 'POST' });
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
    await apiFetch('/v1/model/profiles/' + encodeURIComponent(id) + '/activate', { method: 'POST' });
    await loadProfiles();
    notice('ok', '已切换启用的接入配置，后续问数立即使用它。');
  } catch (err) { notice('bad', '切换失败：' + err.message); }
}
async function removeProfile(profile) {
  if (!window.confirm('确认删除接入配置「' + profile.name + '」？')) { return; }
  try {
    await apiFetch('/v1/model/profiles/' + encodeURIComponent(profile.id), { method: 'DELETE' });
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
    var response = await apiFetch(kind === 'chat' ? '/v1/model/config/test' : '/v1/model/config/models', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
    });
    renderProbe(await response.json(), kind === 'chat' ? '测试连接' : '拉取模型列表');
  } catch (err) { notice('bad', '探测失败：' + err.message); }
  finally { $(buttonId).disabled = false; }
}
async function exportProfiles() {
  try {
    var response = await apiFetch('/v1/model/profiles/export');
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
    var response = await apiFetch('/v1/model/profiles/import', {
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
    var response = await apiFetch('/health');
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
refreshAuth().then(function(){if(hasRole('admin')){loadProfiles();}if(location.hash==='#authority'){loadAuthority();}if(location.hash==='#cad'){loadCad();}if(location.hash==='#dimensions'){loadDimensions();}if(window.loadMultifile){window.loadMultifile();}});
$('loginForm').addEventListener('submit',signIn);
$('logout').addEventListener('click',signOut);
refreshPulses();
setInterval(refreshPulses, 30000);
view(location.hash.slice(1) || localStorage.getItem('datamind_view') || 'chat', true);
var lastRun = localStorage.getItem('datamind_last_run');
if (lastRun) { $('replayId').value = lastRun; }
