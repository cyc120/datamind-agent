'use strict';
var MULTIFILE = {
  token: 0, sources: [], pending: null, job: null, jobData: null,
  docs: [], docToken: 0, detailToken: 0, listToken: 0, timer: null,
  uploading: false, submitting: false, jobBusy: null
};

function multiOption(select, value, label) {
  var option = document.createElement('option');
  option.value = value;
  option.textContent = label;
  select.append(option);
}

function fileBusy() {
  return MULTIFILE.uploading || MULTIFILE.submitting || !!MULTIFILE.jobBusy;
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
  MULTIFILE.sources = [];
  MULTIFILE.pending = null;
  MULTIFILE.job = null;
  MULTIFILE.jobData = null;
  MULTIFILE.uploading = false;
  MULTIFILE.submitting = false;
  MULTIFILE.jobBusy = null;
  clearTimeout(MULTIFILE.timer);
  MULTIFILE.timer = null;
  ['fileRows', 'fileJobs', 'fileJobDetail'].forEach(function(id) {
    $(id).replaceChildren();
  });
  $('uploadFiles').value = '';
  $('fileMessage').textContent = '管理员登录后可上传并登记文件。';
  renderMultifileAuth();
}

function renderMultifileAuth() {
  var disabled = !hasRole('admin') || fileBusy();
  ['uploadFiles', 'uploadProvenance', 'submitFileBatch', 'refreshFiles'].forEach(function(id) {
    $(id).disabled = disabled;
  });
  $('fileRows').querySelectorAll('input').forEach(function(input) {
    input.disabled = disabled;
  });
  $('fileJobs').querySelectorAll('button').forEach(function(button) {
    button.disabled = disabled;
  });
  $('fileJobDetail').querySelectorAll('button[data-file-action]').forEach(function(button) {
    var job = MULTIFILE.jobData;
    var action = button.dataset.fileAction;
    var owner = job && AUTH.user && job.actor && job.actor.user_id === AUTH.user.user_id;
    button.disabled = disabled || !job ||
      (action === 'retry' && (!owner || job.cancel_requested ||
        !['failed', 'partial_failure'].includes(job.status))) ||
      (action === 'cancel' && (job.cancel_requested || ['succeeded', 'cancelled'].includes(job.status)));
  });
}

async function fileResponse(response, missingCode) {
  var data = await response.json();
  if (response.status === 404 && missingCode && data.detail && data.detail.error_code === missingCode) {
    return null;
  }
  if (!response.ok) {
    var code = (data.detail || {}).error_code;
    throw new Error(code ? ('操作未完成：' + code) : '文件操作失败（' + response.status + '）');
  }
  return data;
}

function changeFileInput(source, key, value) {
  if (source[key] !== value) {
    source[key] = value;
    MULTIFILE.pending = null;
  }
}

function renderFileRows() {
  var area = $('fileRows');
  area.replaceChildren();
  MULTIFILE.sources.forEach(function(source, index) {
    var card = document.createElement('div');
    card.className = 'cad-review-card';
    var heading = document.createElement('h3');
    heading.textContent = source.name + ' · ' +
      (source.type === 'drawing' ? 'DXF 图纸' : '尺寸测量 JSON');

    var label = document.createElement('label');
    label.textContent = source.type === 'drawing' ?
      '图纸资产 ID（新建或换版）' : '测量 ID（须与源文件一致）';
    var input = document.createElement('input');
    input.required = true;
    input.maxLength = source.type === 'drawing' ? 64 : 128;
    input.pattern = '[A-Za-z0-9][A-Za-z0-9_.-]*';
    input.id = 'fileIdentity-' + index;
    input.value = source.id || '';
    label.htmlFor = input.id;
    input.addEventListener('input', function() {
      changeFileInput(source, 'id', input.value.trim());
    });
    card.append(heading, label, input);

    if (source.type === 'drawing') {
      var titleLabel = document.createElement('label');
      var title = document.createElement('input');
      titleLabel.textContent = '显示名称';
      titleLabel.htmlFor = 'fileTitle-' + index;
      title.id = titleLabel.htmlFor;
      title.value = source.title || source.name;
      title.maxLength = 200;
      title.required = true;
      source.title = title.value;
      title.addEventListener('input', function() {
        changeFileInput(source, 'title', title.value);
      });
      card.append(titleLabel, title);
    }
    var note = document.createElement('p');
    note.className = 'field-hint';
    note.textContent = '源 SHA-256 ' + source.source_sha256 + ' · ' +
      (source.provenance === 'synthetic_demo' ? '合成材料' : '授权来源声明');
    card.append(note);
    area.append(card);
  });
  renderMultifileAuth();
}

async function uploadFileBatch() {
  if (!hasRole('admin') || fileBusy()) { return; }
  var files = Array.from($('uploadFiles').files || []);
  if (!files.length) { return; }
  if (files.length > 20) {
    $('fileMessage').textContent = '每批最多20份文件。';
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
  MULTIFILE.pending = null;
  MULTIFILE.uploading = true;
  MULTIFILE.sources = [];
  renderFileRows();
  $('fileMessage').textContent = '正在上传文件，最多同时传输2份…';
  var next = 0;
  var failures = [];
  var uploaded = new Array(files.length);

  async function uploadWorker() {
    while (next < files.length) {
      // A rejected old request must not start another upload under a new login.
      if (!fileActor(token, actor, csrf)) { return; }
      var index = next++;
      var file = files[index];
      try {
        if (!/\.(dxf|json)$/i.test(file.name) ||
            file.size > (/\.dxf$/i.test(file.name) ? 2097152 : 1048576)) {
          throw new Error('仅支持≤2 MiB DXF 或≤1 MiB JSON。');
        }
        var response = await apiFetch('/v1/files/uploads?filename=' +
          encodeURIComponent(file.name) + '&provenance=' + encodeURIComponent(provenance), {
          method: 'POST', headers: {'Content-Type': 'application/octet-stream'}, body: file
        });
        var data = await fileResponse(response);
        if (!fileActor(token, actor, csrf)) { return; }
        uploaded[index] = data.source;
      } catch (error) {
        if (!fileActor(token, actor, csrf)) { return; }
        failures.push(file.name + '：' + error.message);
      }
    }
  }

  await Promise.all([uploadWorker(), uploadWorker()]);
  if (!fileActor(token, actor, csrf)) { return; }
  MULTIFILE.sources = uploaded.filter(function(source) { return !!source; });
  MULTIFILE.uploading = false;
  renderFileRows();
  $('fileMessage').textContent = '已上传 ' + MULTIFILE.sources.length +
    ' 份。请逐项填写资产或测量 ID，提交登记任务。' +
    (failures.length ? '失败：' + failures.join('；') : '');
}

function fileInputSnapshot() {
  return MULTIFILE.sources.map(function(source) {
    var id = source.id;
    var maximum = source.type === 'drawing' ? 64 : 128;
    if (!id || id.length > maximum || !/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(id)) {
      throw new Error('请填写每份文件的有效资产或测量 ID。');
    }
    var item = {
      type: source.type, id: id, source_path: source.source_path,
      source_sha256: source.source_sha256, provenance: source.provenance
    };
    if (source.type === 'drawing') {
      item.title = (source.title || '').trim();
      if (!item.title) { throw new Error('请填写图纸显示名称。'); }
    }
    return item;
  });
}

async function submitFileBatch(event) {
  event.preventDefault();
  if (fileBusy()) { return; }
  if (!hasRole('admin') || !MULTIFILE.sources.length) {
    $('fileMessage').textContent = '请先上传文件。';
    return;
  }
  var actor = AUTH.user.user_id;
  var csrf = AUTH.csrf;
  var token = MULTIFILE.token;
  // Invalidate any detail read already in flight and remember this submit's generation, so
  // its response cannot repaint a job detail a newer selection now owns.
  var read = ++MULTIFILE.detailToken;
  clearTimeout(MULTIFILE.timer);
  MULTIFILE.submitting = true;
  renderMultifileAuth();
  try {
    var inputs = fileInputSnapshot();
    var signature = JSON.stringify(inputs);
    var pending = MULTIFILE.pending;
    if (!pending || pending.inputSignature !== signature ||
        pending.actor !== actor || pending.csrf !== csrf) {
      var items = [];
      for (var index = 0; index < inputs.length; index++) {
        if (!fileActor(token, actor, csrf)) { return; }
        var source = inputs[index];
        var drawing = source.type === 'drawing';
        var path = drawing ? '/v1/drawings/' : '/v1/dimensions/measurements/';
        var missingCode = drawing ? 'CAD_VERSION_NOT_FOUND' : 'DIMENSION_RECORD_NOT_FOUND';
        var current = await fileResponse(await apiFetch(path + encodeURIComponent(source.id)), missingCode);
        if (!fileActor(token, actor, csrf)) { return; }
        var payload = {
          request_id: 'item_' + index, source_path: source.source_path,
          source_sha256: source.source_sha256, provenance: source.provenance
        };
        if (drawing) {
          payload.document_id = source.id;
          payload.title = source.title;
          payload.previous_version = current ? current.version.document_version : 0;
          payload.previous_source_sha256 = current ? current.version.source_sha256 : null;
        } else {
          payload.measurement_id = source.id;
          payload.previous_version = current ? current.measurement.payload.source.record_version : 0;
          payload.previous_sha256 = current ? current.measurement.sha256 : null;
        }
        items.push({item_id: 'file_' + index, type: source.type, payload: payload});
      }
      // Freeze the complete POST before sending. An uncertain response retries
      // these same baselines and request ID, never reads a new current version.
      var body = {request_id: crypto.randomUUID(), max_concurrency: 2, items: items};
      pending = {
        actor: actor, csrf: csrf, inputSignature: signature,
        body: JSON.stringify(body), job_id: null
      };
      MULTIFILE.pending = pending;
    }
    if (!fileActor(token, actor, csrf)) { return; }
    var job = await fileResponse(await apiFetch('/v1/files/jobs', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: pending.body
    }));
    if (!fileActor(token, actor, csrf) || MULTIFILE.pending !== pending) { return; }
    // Keep the confirmed request too: another click for unchanged inputs must
    // reopen this job rather than create a second measurement version.
    pending.job_id = job.job_id;
    if (read === MULTIFILE.detailToken) {
      MULTIFILE.job = job.job_id;
      renderFileJob(job);
    }
    $('fileMessage').textContent = '任务已登记；每份文件独立提交，失败项可单独重试。';
    await loadMultifile();
  } catch (error) {
    if (fileActor(token, actor, csrf)) {
      $('fileMessage').textContent = error.message +
        (MULTIFILE.pending ? ' 可原样重试同一登记请求；更改输入会建立新请求。' : '');
    }
  } finally {
    if (fileActor(token, actor, csrf)) {
      MULTIFILE.submitting = false;
      renderMultifileAuth();
      if (MULTIFILE.job && MULTIFILE.jobData) {
        scheduleFileJob(MULTIFILE.job, MULTIFILE.jobData.status);
      }
    }
  }
}

var FILE_STATUS = {
  queued: '排队中', pending: '排队中', running: '处理中',
  succeeded: '已登记', failed: '失败', partial_failure: '部分失败', cancelled: '已取消'
};

function renderFileJob(job) {
  MULTIFILE.job = job.job_id;
  MULTIFILE.jobData = job;
  $('fileJobs').querySelectorAll('button[data-file-job-id]').forEach(function(button) {
    if (button.dataset.fileJobId === job.job_id) {
      button.textContent = job.job_id + ' · ' + (FILE_STATUS[job.status] || job.status);
    }
  });
  var area = $('fileJobDetail');
  area.replaceChildren();
  var heading = document.createElement('h3');
  heading.textContent = '任务 ' + job.job_id + ' · ' + (FILE_STATUS[job.status] || job.status);
  area.append(heading);
  (job.items || []).forEach(function(item) {
    var paragraph = document.createElement('p');
    paragraph.textContent = item.item_id + ' / ' + (item.target_id || '') + ' · ' +
      (FILE_STATUS[item.status] || item.status) +
      (item.error_code ? ' · ' + item.error_code : '') +
      (item.result ? ' · 结果已保存' : '');
    area.append(paragraph);
  });
  var hint = document.createElement('p');
  hint.className = 'field-hint';
  hint.textContent = '取消会阻止尚未开始的登记；已经提交的文件版本会保留。工作器暂停时，任务继续保留在队列中。';
  area.append(hint);
  [['retry', '重试失败项'], ['cancel', '取消未开始项']].forEach(function(choice) {
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'secondary';
    button.textContent = choice[1];
    button.dataset.fileAction = choice[0];
    button.addEventListener('click', function() {
      mutateFileJob(job.job_id, choice[0]);
    });
    area.append(button);
  });
  renderMultifileAuth();
  scheduleFileJob(job.job_id, job.status);
}

async function mutateFileJob(id, action) {
  if (!hasRole('admin') || fileBusy() || MULTIFILE.job !== id) { return; }
  var token = MULTIFILE.token;
  var actor = AUTH.user.user_id;
  var csrf = AUTH.csrf;
  var read = ++MULTIFILE.detailToken;
  var operation = {id: id, action: action};
  MULTIFILE.jobBusy = operation;
  clearTimeout(MULTIFILE.timer);
  renderMultifileAuth();
  try {
    var updated = await fileResponse(await apiFetch('/v1/files/jobs/' +
      encodeURIComponent(id) + '/' + action, {method: 'POST'}));
    if (fileActor(token, actor, csrf) && read === MULTIFILE.detailToken && MULTIFILE.job === id) {
      renderFileJob(updated);
    }
  } catch (error) {
    if (fileActor(token, actor, csrf) && read === MULTIFILE.detailToken && MULTIFILE.job === id) {
      $('fileMessage').textContent = error.message;
      scheduleFileJob(id, MULTIFILE.jobData && MULTIFILE.jobData.status);
    }
  } finally {
    if (MULTIFILE.jobBusy === operation) {
      MULTIFILE.jobBusy = null;
      renderMultifileAuth();
    }
  }
}

function scheduleFileJob(id, status) {
  clearTimeout(MULTIFILE.timer);
  MULTIFILE.timer = null;
  if (status && !['queued', 'running'].includes(status)) { return; }
  var token = MULTIFILE.token;
  var actor = AUTH.user && AUTH.user.user_id;
  var csrf = AUTH.csrf;
  MULTIFILE.timer = setTimeout(async function() {
    if (!fileActor(token, actor, csrf) || location.hash !== '#files' || MULTIFILE.job !== id) { return; }
    if (MULTIFILE.jobBusy) { return; }
    var read = ++MULTIFILE.detailToken;
    try {
      var job = await fileResponse(await apiFetch('/v1/files/jobs/' + encodeURIComponent(id)));
      if (fileActor(token, actor, csrf) && read === MULTIFILE.detailToken && MULTIFILE.job === id) {
        renderFileJob(job);
      }
    } catch (error) {
      if (fileActor(token, actor, csrf) && read === MULTIFILE.detailToken && MULTIFILE.job === id) {
        $('fileMessage').textContent = error.message;
      }
    }
  }, 2000);
}

async function selectFileJob(id) {
  if (!hasRole('admin') || fileBusy()) { return; }
  var token = MULTIFILE.token;
  var actor = AUTH.user.user_id;
  var csrf = AUTH.csrf;
  var read = ++MULTIFILE.detailToken;
  MULTIFILE.job = id;
  clearTimeout(MULTIFILE.timer);
  try {
    var detail = await fileResponse(await apiFetch('/v1/files/jobs/' + encodeURIComponent(id)));
    if (fileActor(token, actor, csrf) && read === MULTIFILE.detailToken && MULTIFILE.job === id) {
      renderFileJob(detail);
    }
  } catch (error) {
    if (fileActor(token, actor, csrf) && read === MULTIFILE.detailToken && MULTIFILE.job === id) {
      $('fileMessage').textContent = error.message;
    }
  }
}

async function loadMultifile() {
  renderMultifileAuth();
  if (!hasRole('admin')) { return; }
  var token = MULTIFILE.token;
  var actor = AUTH.user.user_id;
  var csrf = AUTH.csrf;
  var read = ++MULTIFILE.listToken;
  try {
    var data = await fileResponse(await apiFetch('/v1/files/jobs'));
    if (!fileActor(token, actor, csrf) || read !== MULTIFILE.listToken) { return; }
    var jobs = Array.isArray(data) ? data : (data.jobs || data.file_jobs || []);
    $('fileJobs').replaceChildren();
    jobs.forEach(function(job) {
      var button = document.createElement('button');
      button.type = 'button';
      button.className = 'secondary';
      button.dataset.fileJobId = job.job_id;
      button.textContent = job.job_id + ' · ' + (FILE_STATUS[job.status] || job.status);
      button.addEventListener('click', function() { selectFileJob(job.job_id); });
      $('fileJobs').append(button);
    });
    renderMultifileAuth();
  } catch (error) {
    if (fileActor(token, actor, csrf) && read === MULTIFILE.listToken) {
      $('fileMessage').textContent = error.message;
    }
  }
}

async function loadQuestionDocuments() {
  var token = ++MULTIFILE.docToken;
  $('questionDocuments').textContent = '正在读取可用文件…';
  try {
    var data = await fileResponse(await apiFetch('/v1/documents'));
    if (token !== MULTIFILE.docToken) { return; }
    MULTIFILE.docs = data.documents;
    $('questionDocuments').replaceChildren();
    MULTIFILE.docs.forEach(function(doc) {
      var label = document.createElement('label');
      label.className = 'multifile-document';
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
      label.append(check, document.createTextNode(doc.title + ' · v' + doc.doc_version));
      $('questionDocuments').append(label);
    });
    $('questionDocumentsMessage').textContent = '不勾选时沿用问题范围；勾选后只使用所选文件，跨文件同名对象不自动关联。';
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

$('uploadFiles').addEventListener('change', uploadFileBatch);
$('fileBatchForm').addEventListener('submit', submitFileBatch);
$('refreshFiles').addEventListener('click', loadMultifile);
$('refreshQuestionDocuments').addEventListener('click', loadQuestionDocuments);
$('source').addEventListener('change', clearQuestionSelection);
document.querySelectorAll('.chip').forEach(function(button) {
  button.addEventListener('click', clearQuestionSelection);
});
window.addEventListener('hashchange', function() {
  if (location.hash === '#files') { loadMultifile(); }
});
renderMultifileAuth();
loadQuestionDocuments();
if (location.hash === '#files') { loadMultifile(); }
