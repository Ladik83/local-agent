const AGENT_TOKEN = (document.querySelector('meta[name="agent-token"]') || {}).content || '';
const nativeFetch = window.fetch.bind(window);
window.fetch = function (url, options) {
  const opts = options || {};
  const headers = Object.assign({}, opts.headers || {});
  if (AGENT_TOKEN) headers['X-Agent-Token'] = AGENT_TOKEN;
  opts.headers = headers;
  return nativeFetch(url, opts);
};

const $ = (id) => document.getElementById(id);
let STATE = null;
let BUSY = false;
let HISTORY = [];

function toast(text) {
  const el = $("toast");
  el.textContent = text;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 3200);
}

function gb(value) {
  return (Math.round(value * 10) / 10).toLocaleString("ru-RU") + " ГБ";
}

async function api(path, body) {
  const resp = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return resp.json();
}

async function loadState() {
  STATE = await api("/api/state");
  render();
  return STATE;
}

function setStatus() {
  const dot = $("dot");
  const text = $("statusText");
  dot.className = "dot";
  if (BUSY) {
    dot.className = "dot busy";
    text.textContent = "думает…";
  } else if (STATE && STATE.server.running) {
    dot.className = "dot on";
    text.textContent = "модель загружена";
  } else {
    text.textContent = "не загружена";
  }
}


function esc(t) { return String(t === null || t === undefined ? '' : t).replace(/[&<>"']/g, function (c) { return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&#34;',"'":'&#39;'}[c]; }); }
function badge(text, cls) { return '<span class="badge ' + (cls || '') + '">' + text + '</span>'; }
function tip(text) {
  return '<span class="help" data-tip="' + esc(text) + '">?</span>';
}


function renderHardware() {
  const hw = STATE.hardware, pick = STATE.pick;
  const engine = STATE.engine || {};
  const gpu = (hw.gpu && hw.gpu[0]) ? hw.gpu[0].name : 'без видеочипа';
  const box = document.getElementById('hwCard');
  if (box) {
    let html = '<div class="metric"><span>Процессор</span><b>' + esc(hw.cpu.name) + '</b></div>';
    html += '<div class="metric"><span>Память</span><b>' + gb(hw.ram_total_gb) + '</b></div>';
    html += '<div class="metric"><span>Свободно сейчас</span><b>' + gb(hw.ram_free_gb) + '</b></div>';
    html += '<div class="metric"><span>Видео</span><b>' + esc(gpu) + '</b></div>';
    html += '<div class="metric"><span>Диск моделей</span><b>' + gb(hw.disk_free_gb) + ' свободно</b></div>';
    if (pick.kind === 'local') {
      html += '<div class="sub" style="margin-top:6px">Рекомендуем: <b>' + esc(pick.model.name) + '</b> (' + gb(pick.model.size_gb) + ')</div>';
      html += '<div class="sub">' + esc(pick.why) + '</div>';
    } else {
      html += '<div class="sub" style="margin-top:6px">Своих моделей нет — скачайте на вкладке «Модели».</div>';
    }
    box.innerHTML = html;
  }
  const bar = document.getElementById('chatBar');
  if (bar) {
    let html = '';
    if (engine.running) {
      const name = String(engine.model || '').split(/[\/]/).pop();
      html += '<b>' + esc(name) + '</b><span>' + (engine.used_mb || 0) + ' МБ в памяти</span>';
      html += '<button class="ghost tiny" onclick="unloadModel()">Выгрузить</button>';
    } else if (pick.kind === 'local') {
      html += '<span>Модель не загружена</span>';
      html += '<button class="ghost tiny" onclick="startModel(' + JSON.stringify(pick.model.id).replace(/"/g, '&quot;') + ')">Запустить рекомендованную</button>';
    } else {
      html += '<span>Модель не загружена</span>';
    }
    bar.innerHTML = html;
  }
}

async function unloadModel() {
  toast('Выгружаю модель, освобождаю память…');
  const resp = await fetch('/api/model/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  const data = await resp.json();
  toast(data.ok ? 'Модель выгружена — память свободна' : 'Не получилось выгрузить модель');
  await loadState();
}

function renderLocalModels() {
  const box = document.getElementById('localModels');
  document.getElementById('modelsSub').textContent = 'Папка: ' + STATE.hardware.models_dir + ' — найдено ' + STATE.models.length + ' файлов .gguf';
  box.innerHTML = STATE.models.map(function (m) {
    const best = STATE.pick.kind === 'local' && STATE.pick.model.id === m.id;
    const cls = m.fits ? 'ok' : 'no';
    const isRunning = (STATE.engine || {}).model === m.id;
    const startBtn = isRunning
      ? '<button class="ghost" onclick="unloadModel()">Выгрузить</button>'
      : '<button class="ghost" onclick="startModel(' + JSON.stringify(m.id).replace(/"/g, '&quot;') + ')">Запустить</button>';
    return '<div class="card"><div class="title">' + esc(m.name) + (best ? ' ' + badge('рекомендуем', 'best') : '') + (isRunning ? ' ' + badge('в памяти', 'best') : '') + '</div>' +
      '<div class="meta">' + gb(m.size_gb) + (m.params_b ? ' · ' + m.params_b + 'B' : '') + (m.moe ? ' · MoE ' + m.active_b + 'B активно' : '') + '</div>' +
      '<div class="meta">' + esc(m.file) + '</div>' +
      '<div class="row2">' + badge(m.fits ? 'помещается в память' : 'может не хватить памяти', cls) +
      (m.vision ? badge('видит картинки') : '') + startBtn + '</div></div>';
  }).join('') || '<div class="sub">В папке моделей пусто — скачайте любую из списка ниже.</div>';
}

function renderCatalog() {
  const box = document.getElementById('catalog');
  box.innerHTML = STATE.catalog.map(function (c) {
    const rec = STATE.pick.kind === 'catalog' && STATE.pick.model.id === c.id;
    return '<div class="card"><div class="title">' + esc(c.title) + (rec ? ' ' + badge('для вас', 'best') : '') + '</div>' +
      '<div class="meta">' + gb(c.size_gb) + ' · нужно от ' + c.min_ram_gb + ' ГБ ОЗУ</div>' +
      '<div class="meta">' + esc(c.note) + '</div>' +
      '<div class="row2"><button class="ghost" onclick="downloadModel(\'' + c.id + '\')">Скачать</button></div></div>';
  }).join('');
}

function renderSettings() {
  const s = STATE.settings;
  document.getElementById('allowWrite').checked = !!s.allow_write;
  document.getElementById('allowShell').checked = !!s.allow_shell;
  document.getElementById('allowWeb').checked = !!s.allow_web;
  document.getElementById('modelsDir').value = s.models_dir || '';
  const pubkey = document.getElementById('updatePubkey');
  if (pubkey) pubkey.value = s.update_pubkey || '';
  document.getElementById('extraDirs').value = (s.extra_dirs || []).join('; ');
  document.getElementById('context').value = String(s.context || 8192);
  const approval = document.getElementById('approvalMode');
  if (approval) approval.value = s.approval_mode || 'ask';
  const e = STATE.engine;
  let html = '<div class="row"><span>Движок llama.cpp' +
    tip('llama.cpp — программа, которая запускает модели на вашем компьютере. Если её нет, приложение скачает сборку само.') +
    '</span><span>' +
    (e.installed ? badge('готов', 'ok') : badge('не установлен', 'no')) + '</span></div>';
  html += '<div class="row"><span>Версия</span><span>' + esc(e.tag || (e.exe ? 'внешняя сборка' : '—')) + '</span></div>';
  html += '<div class="row"><span>Последняя доступная</span><span>' + esc(e.latest || '—') + '</span></div>';
  if (e.job && e.job.active) {
    html += '<div class="progress"><i style="width:' + (e.job.pct || 0) + '%"></i></div><div class="sub">' + esc(e.job.text) + '</div>';
  }
  html += '<div class="row"><span>Установить свежую сборку</span><span>' +
    '<button class="ghost" onclick="installEngine(\'vulkan\')">Vulkan (быстрее на видеокарте)</button> ' +
    '<button class="ghost" onclick="installEngine(\'cpu\')">CPU</button></span></div>';
  html += '<div class="sub">' + (e.exe ? 'Файл: ' + esc(e.exe) : 'llama.cpp будет скачан в папку приложения') + '</div>';
  document.getElementById('enginePanel').innerHTML = html;
}

let POLL = null;

function scrollDown() {
  const box = document.getElementById('messages');
  box.scrollTop = box.scrollHeight;
}

function addMsg(role, text) {
  const box = document.getElementById('messages');
  const div = document.createElement('div');
  div.className = 'msg ' + (role === 'user' ? 'user' : 'bot');
  div.innerHTML = md(text || '');
  box.appendChild(div);
  scrollDown();
  return div;
}

function md(text) {
  const safe = esc(text || '');
  const parts = safe.split('```');
  return parts.map(function (part, index) {
    if (index % 2 === 1) {
      return '<pre>' + part.replace(/^[a-zA-Z0-9]*\n/, '') + '</pre>';
    }
    return part
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
      .replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank">$1</a>');
  }).join('');
}

async function cancelGeneration() {
  await postJson('/api/chat/cancel', {});
  toast('Останавливаю…');
  const stop = document.getElementById('stopBtn');
  if (stop) stop.style.display = 'none';
}

const ATTACH = [];

function renderAttach() {
  const bar = document.getElementById('attachBar');
  if (!bar) return;
  bar.innerHTML = '';
  ATTACH.forEach(function (item, index) {
    const chip = document.createElement('span');
    chip.className = 'attachChip';
    chip.textContent = item.name + ' · ' + Math.round(item.chars / 100) / 10 + 'k';
    const drop = document.createElement('button');
    drop.textContent = '×';
    drop.onclick = function () { ATTACH.splice(index, 1); renderAttach(); };
    chip.appendChild(drop);
    bar.appendChild(chip);
  });
}

async function attachFile(file) {
  const hint = document.getElementById('dropHint');
  if (hint) hint.textContent = 'Читаю ' + file.name + '…';
  const buffer = await file.arrayBuffer();
  let binary = '';
  const bytes = new Uint8Array(buffer);
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  const data = btoa(binary);
  const res = await postJson('/api/attach/upload', { name: file.name, data: data });
  if (hint) hint.textContent = 'Перетащите файл сюда: PDF, DOCX, XLSX, CSV, текст';
  if (!res || !res.ok) { toast('Файл не прочитан: ' + ((res && res.error) || 'ошибка')); return; }
  ATTACH.push({ name: res.name, text: res.text || '', chars: res.chars || 0 });
  renderAttach();
  toast('Прикреплено: ' + res.name);
}

function wireDrop() {
  const view = document.getElementById('view-chat');
  if (!view) return;
  ['dragenter', 'dragover'].forEach(function (name) {
    view.addEventListener(name, function (event) { event.preventDefault(); view.classList.add('dragging'); });
  });
  ['dragleave', 'drop'].forEach(function (name) {
    view.addEventListener(name, function (event) { event.preventDefault(); view.classList.remove('dragging'); });
  });
  view.addEventListener('drop', function (event) {
    const files = event.dataTransfer ? event.dataTransfer.files : null;
    if (!files || !files.length) return;
    attachFile(files[0]);
  });
}
async function send() {
  const input = document.getElementById('input');
  const text = input.value.trim();
  if (!text || BUSY) return;
  BUSY = true;
  document.getElementById('send').disabled = true;
  const stopBtn = document.getElementById('stopBtn');
  if (stopBtn) stopBtn.style.display = '';
  input.value = '';
  let fullText = text;
  if (ATTACH.length) {
    const parts = ATTACH.map(function (item) { return 'Файл ' + item.name + ':\n' + item.text; });
    fullText = parts.join('\n\n') + (text ? '\n\n' + text : '');
    ATTACH.length = 0;
    renderAttach();
  }
  addMsg('user', text);
  HISTORY.push({ role: 'user', content: fullText });
  let bubble = addMsg('bot', '');
  bubble.innerHTML = '<span class="typing">думаю…</span>';
  let answer = '';
  let thinking = '';

  function paint() {
    let html = '';
    if (thinking) {
      html += '<details class="think"><summary>размышления</summary>' + esc(thinking) + '</details>';
    }
    html += md(answer);
    bubble.innerHTML = html || '<span class="typing">…</span>';
    scrollDown();
  }

  function chip(line) {
    const box = document.getElementById('messages');
    const div = document.createElement('div');
    div.className = 'tool';
    div.textContent = line;
    box.appendChild(div);
    scrollDown();
  }

  function handle(event) {
    if (event.type === 'plan_start' || event.type === 'summary_start') {
      answer = '';
      thinking = '';
      bubble.innerHTML = '<span class="typing">' + (event.text || '…') + '</span>';
      return;
    }
    if (event.type === 'plan') {
      const names = (event.steps || []).map(function (step, index) {
        return (index + 1) + '. ' + step.role;
      }).join('   ›   ');
      const div = document.createElement('div');
      div.className = 'plan';
      div.textContent = 'План: ' + names;
      document.getElementById('messages').appendChild(div);
      scrollDown();
      return;
    }
    if (event.type === 'role_start') {
      bubble = addMsg('bot', '');
      bubble.classList.add('role');
      const who = document.createElement('span');
      who.className = 'who';
      who.textContent = event.role + ' · ' + event.step + '/' + event.total;
      bubble.appendChild(who);
      answer = '';
      thinking = '';
      return;
    }
    if (event.type === 'role_done') {
      paint();
      return;
    }
    if (event.type === 'approval') { showApproval(event); return; }
    if (event.type === 'delta') { answer += event.text; paint(); }
    else if (event.type === 'think') { thinking += event.text; paint(); }
    else if (event.type === 'tool') {
      chip('⚙ ' + event.name + ' ' + JSON.stringify(event.args || {}).slice(0, 160));
    } else if (event.type === 'tool_result') {
      chip('→ ' + String(event.text || '').slice(0, 260));
    } else if (event.type === 'done') {
      HISTORY = (event.messages || []).filter(function (m) {
        return m.role === 'user' || (m.role === 'assistant' && m.content);
      });
      paint();
      saveChat();
      speakAnswer();
    } else if (event.type === 'error') {
      answer += '\n\nОшибка: ' + event.text;
      paint();
    }
  }

  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages: HISTORY, mode: currentMode() })
    });
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    for (;;) {
      const part = await reader.read();
      if (part.done) break;
      buffer += decoder.decode(part.value, { stream: true });
      let cut = buffer.indexOf('\n\n');
      while (cut >= 0) {
        const block = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);
        const line = block.split('\n').find(function (l) { return l.indexOf('data: ') === 0; });
        if (line) {
          try { handle(JSON.parse(line.slice(6))); } catch (err) { console.error(err); }
        }
        cut = buffer.indexOf('\n\n');
      }
    }
  } catch (err) {
    answer += '\n\nНе удалось получить ответ: ' + err.message;
  }
  BUSY = false;
  document.getElementById('send').disabled = false;
  const stopReset = document.getElementById('stopBtn');
  if (stopReset) stopReset.style.display = 'none';
  paint();
}

async function startModel(path) {
  toast('Загружаю модель, это может занять до минуты');
  try {
    const resp = await fetch('/api/model/load', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: path, background: true })
    });
    const data = await resp.json();
    if (!data.ok) { toast('Не получилось: ' + (data.error || 'ошибка')); return; }
    const result = (await waitJob('model_load', 600000)) || {};
    if (result.ok) { toast('Модель готова'); await loadState(); }
    else { toast('Не получилось: ' + (result.error || 'ошибка')); }
  } catch (err) {
    toast('Ошибка: ' + err.message);
  }
}

async function downloadModel(id) {
  const resp = await fetch('/api/model/download', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: id })
  });
  const data = await resp.json();
  toast(data.ok ? 'Загрузка началась' : (data.error || 'ошибка'));
  poll();
}

async function installEngine(variant) {
  const resp = await fetch('/api/engine/install', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ variant: variant })
  });
  const data = await resp.json();
  toast(data.ok ? 'Скачиваю llama.cpp ' + data.tag : (data.error || 'ошибка'));
  poll();
}

async function discoverModels() {
  await fetch('/api/models/discover', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  toast('Ищу модели на дисках…');
  discoverPoll();
}

let DP = null;
function discoverPoll() {
  if (DP) return;
  DP = setInterval(async function () {
    const job = await (await fetch('/api/job')).json();
    const info = job.discover || {};
    const bar = document.getElementById('discProgress');
    bar.style.display = 'block';
    bar.firstElementChild.style.width = Math.min(95, (info.scanned || 0) / 900) + '%';
    document.getElementById('discText').textContent = info.text || '';
    if (!info.active) {
      clearInterval(DP);
      DP = null;
      bar.style.display = 'none';
      toast(info.error ? ('Ошибка: ' + info.error) : (info.text || 'Готово'));
      await loadState();
    }
  }, 1200);
}

async function saveSettings() {
  const patch = {
    allow_write: document.getElementById('allowWrite').checked,
    allow_shell: document.getElementById('allowShell').checked,
    allow_web: document.getElementById('allowWeb').checked,
    models_dir: document.getElementById('modelsDir').value.trim(),
    extra_dirs: document.getElementById('extraDirs').value.split(';').map(function (t) { return t.trim(); }).filter(Boolean),
    approval_mode: (document.getElementById('approvalMode') || {}).value || 'ask',
    context: parseInt(document.getElementById('context').value, 10) || 8192,
    embed_model: ((document.getElementById('embedModel') || {}).value || '').trim(),
    update_pubkey: ((document.getElementById('updatePubkey') || {}).value || '').trim()
  };
  await fetch('/api/settings', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch)
  });
  toast('Сохранено');
  await loadState();
}

function poll() {
  if (POLL) return;
  POLL = setInterval(async function () {
    const job = await (await fetch('/api/job')).json();
    const busyEngine = job.engine && job.engine.active;
    const busyModel = job.download && job.download.active;
    updateHfProgress(job.download);
    if (busyModel && job.download.text) toast(job.download.text);
    if (busyEngine && job.engine.text) toast(job.engine.text);
    if (!busyEngine && !busyModel) {
      clearInterval(POLL);
      POLL = null;
      if (job.download && job.download.error) toast('Ошибка: ' + job.download.error);
      await loadState();
    }
  }, 1500);
}

const THINKING_LABELS = { none: 'без размышлений', low: 'низкий', medium: 'средний', high: 'высокий', xhigh: 'максимальный' };

function renderRoles() {
  const box = document.getElementById('rolesPanel');
  if (!box) return;
  const rows = (STATE.team || {}).roles || [];
  let html = '<table class="roles"><thead><tr><th>Роль</th><th>Думает</th><th>Что делает</th></tr></thead><tbody>';
  for (const role of rows) {
    let opts = '';
    for (const key of Object.keys(THINKING_LABELS)) {
      opts += '<option value="' + key + '"' + (role.thinking === key ? ' selected' : '') + '>' + THINKING_LABELS[key] + '</option>';
    }
    html += '<tr><td class="roleName">' + esc(role.title) + '</td>' +
      '<td><select data-role-thinking="' + role.id + '">' + opts + '</select></td>' +
      '<td><input type="text" data-role-prompt="' + role.id + '" value="' + esc(role.prompt).replace(/"/g, '&quot;') + '"></td></tr>';
  }
  html += '</tbody></table>';
  box.innerHTML = html;
  applyMode((STATE.team || {}).enabled);
}

async function saveRoles() {
  const rows = ((STATE.team || {}).roles || []).map(function (role) {
    const think = document.querySelector('[data-role-thinking="' + role.id + '"]');
    const prompt = document.querySelector('[data-role-prompt="' + role.id + '"]');
    return { id: role.id, title: role.title, thinking: think ? think.value : role.thinking, tools: role.tools, prompt: prompt ? prompt.value : role.prompt };
  });
  await fetch('/api/roles', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ roles: rows }) });
  toast('Роли сохранены');
  await loadState();
}

function renderProvider() {
  const p = STATE.provider || {};
  const box = document.getElementById('providerPanel');
  if (!box) return;
  const isLocal = (p.kind || 'local') === 'local';
  let html = '<div class="row"><span>Откуда брать модель</span><span>';
  html += '<button class="ghost" onclick="setProviderKind(\'local\')">' + (isLocal ? '● ' : '') + 'Локально</button> ';
  html += '<button class="ghost" onclick="setProviderKind(\'server\')">' + (p.kind === 'server' ? '● ' : '') + 'Свой сервер</button> ';
  html += '<button class="ghost" onclick="setProviderKind(\'api\')">' + (p.kind === 'api' ? '● ' : '') + 'Внешний API</button>';
  html += '</span></div>';
  if (isLocal) {
    html += '<div class="sub">Модель поднимает само приложение — выбирайте .gguf на вкладке «Модели».</div>';
  } else {
    html += '<div class="row"><span>Адрес сервера' +
      tip('Адрес уже запущенного сервера моделей. LM Studio обычно http://localhost:1234, MoE http://localhost:11434. Можно не вводить вручную — нажмите «Найти серверы».') +
      '</span><input type="text" id="provUrl" class="wide" ' +
      'placeholder="http://localhost:1234" value="' + esc(p.base_url || '') + '"></div>';
    if (p.kind === 'api') {
        'placeholder="' + (p.has_key ? 'ключ уже сохранён' : 'вставьте ключ') + '"></div>';
    }
    const model = p.model || '';
    let options = '';
    for (const item of REMOTE) {
      options += '<option value="' + esc(item) + '"' + (item === model ? ' selected' : '') + '>' + esc(item) + '</option>';
    }
    html += '<div class="row"><span>Модель</span><select id="provModel">' + options + '</select></div>';
    html += '<div class="row"><span></span><span>' +
      '<button class="ghost" onclick="discoverServers()">Найти серверы</button> ' +
      '<button class="ghost" onclick="probeProvider()">Проверить</button> ' +
      '<button class="primary" onclick="saveProvider()">Применить</button></span></div>';
  }
  box.innerHTML = html;
}

let REMOTE = [];

async function setProviderKind(kind) {
  await fetch('/api/provider', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind: kind }) });
  REMOTE = [];
  await loadState();
}

async function discoverServers() {
  toast('Ищу запущенные серверы на этом компьютере…');
  const resp = await fetch('/api/providers/discover', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  const data = await resp.json();
  const servers = data.servers || [];
  if (!servers.length) {
    toast('Ничего не нашли на localhost — запустите LM Studio, Ollama или llama-server');
    return;
  }
  const first = servers[0];
  REMOTE = first.models || [];
  await fetch('/api/provider', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kind: 'server', base_url: first.base_url, model: first.models[0] || '' })
  });
  toast('Нашли ' + first.base_url + ', моделей: ' + (first.models || []).length);
  await loadState();
}

async function probeProvider() {
  const url = (document.getElementById('provUrl') || {}).value || '';
  const key = (document.getElementById('provKey') || {}).value || '';
  const resp = await fetch('/api/providers/probe', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ base_url: url, api_key: key })
  });
  const data = await resp.json();
  if (!data.ok) { toast('Не отвечает: ' + (data.error || 'ошибка')); return; }
  REMOTE = data.models || [];
  toast('Моделей найдено: ' + REMOTE.length);
  const kind = (STATE.provider || {}).kind || 'server';
  await fetch('/api/provider', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kind: kind, base_url: url, model: REMOTE[0] || '', api_key: key })
  });
  await loadState();
}

async function saveProvider() {
  const url = (document.getElementById('provUrl') || {}).value || '';
  const key = (document.getElementById('provKey') || {}).value || '';
  const model = (document.getElementById('provModel') || {}).value || '';
  const kind = (STATE.provider || {}).kind || 'server';
  await fetch('/api/provider', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kind: kind, base_url: url, model: model, api_key: key })
  });
  toast('Провайдер сохранён');
  await loadState();
}

function currentMode() {
  const active = document.querySelector('.mode.active');
  return active ? active.dataset.mode : 'agent';
}

function applyMode(enabled) {
  document.querySelectorAll('.mode').forEach(function (button) {
    button.classList.toggle('active', (button.dataset.mode === 'team') === !!enabled);
  });
}

let MCP_INFO = { servers: [], tools: [], catalog: [] };

async function loadMcp() {
  try {
    const resp = await fetch('/api/mcp');
    MCP_INFO = await resp.json();
  } catch (err) {
    MCP_INFO = { servers: [], tools: [], catalog: [] };
  }
}

function renderMcp() {
  const box = document.getElementById('mcpPanel');
  if (!box) return;
  const info = MCP_INFO || {};
  const servers = info.servers || [];
  const tools = info.tools || [];
  let html = '<div class="row"><span>Внешние инструменты (MCP)' +
    tip('MCP — это готовые наборы инструментов от других программ. Подключите сервер «Файлы» — агент сможет работать с файлами, «Память» — запоминать важное. Инструменты появляются в MoE.') +
    '</span><span class="sub">' + tools.length + ' MoE</span></div>';
  for (const server of servers) {
    const line = [server.command].concat(server.args || []).join(' ');
    html += '<div class="row"><span><b>' + esc(server.id) + '</b><br><span class="sub">' + esc(line) + '</span></span><span>' +
      '<button class="ghost" onclick="testMcp(\'' + server.id + '\')">Проверить</button> ' +
      '<button class="ghost" onclick="removeMcp(\'' + server.id + '\')">Удалить</button></span></div>';
  }
  html += '<div class="row"><span>Добавить вручную</span><input type="text" id="mcpCmd" class="wide" placeholder="npx -y MoE/server-memory"></div>';
  html += '<div class="row"><span></span><span><button class="ghost" onclick="addMcpManual()">Добавить</button></span></div>';
  const catalog = info.catalog || [];
  if (catalog.length) {
    html += '<div class="row"><span>Готовые наборы</span><span>' + catalog.map(function (item) {
      return '<button class="ghost" onclick="addMcpCatalog(\'' + item.id + '\')">' + esc(item.title) + '</button>';
    }).join(' ') + '</span></div>';
  }
  box.innerHTML = html;
}

async function postJson(url, payload) {
  const resp = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload || {}) });
  return resp.json();
}

async function addMcpCatalog(id) {
  const item = (MCP_INFO.catalog || []).find(function (entry) { return entry.id === id; });
  if (!item) return;
  const data = await postJson('/api/mcp/add', { id: item.id, command: item.command, args: item.args });
  toast(data.ok ? 'Добавлено: ' + item.title : (data.error || 'ошибка'));
  await loadMcp();
  renderMcp();
}

async function addMcpManual() {
  const raw = ((document.getElementById('mcpCmd') || {}).value || '').trim();
  if (!raw) { toast('Введите команду запуска сервера'); return; }
  const parts = raw.split(/\s+/);
  const id = parts[1] ? parts[1].split('/').pop().replace(/[^a-z0-9_-]/gi, '_') : 'server';
  const data = await postJson('/api/mcp/add', { id: id, command: parts[0], args: parts.slice(1) });
  toast(data.ok ? 'Сервер добавлен: ' + id : (data.error || 'ошибка'));
  await loadMcp();
  renderMcp();
}

async function testMcp(id) {
  toast('Проверяю сервер ' + id + '…');
  const data = await postJson('/api/mcp/test', { id: id });
  toast(data.ok ? ('Инструменты: ' + (data.tools || []).join(', ')) : ('Ошибка: ' + (data.error || 'не ответил')));
  await loadMcp();
  renderMcp();
}

async function removeMcp(id) {
  await postJson('/api/mcp/remove', { id: id });
  toast('Сервер удалён');
  await loadMcp();
  renderMcp();
}

let CURRENT_CHAT = "";
let STATS = null;

function bar(percent, cls) {
  const value = Math.max(0, Math.min(100, Number(percent) || 0));
  return '<div class="bar"><i class="' + (cls || '') + '" style="width:' + value + '%"></i></div>';
}

function renderStats() {
  if (!STATS) return;
  const mem = STATS.memory || {};
  const cpu = STATS.cpu || 0;
  const res = document.getElementById('monRes');
  if (res) {
    res.innerHTML =
      '<div class="metric"><span>Процессор</span><b>' + cpu.toFixed(0) + '%</b></div>' + bar(cpu, cpu > 85 ? 'err' : (cpu > 60 ? 'warn' : '')) +
      '<div class="metric"><span>Память</span><b>' + mem.used + ' из ' + mem.total + ' ГБ</b></div>' + bar(mem.percent, mem.percent > 88 ? 'err' : (mem.percent > 70 ? 'warn' : '')) +
      '<div class="metric"><span>Свободно на диске</span><b>' + STATS.disk_free + ' ГБ</b></div>';
  }
  const engine = STATS.engine || {};
  const model = document.getElementById('monModel');
  if (model) {
    model.innerHTML =
      '<div class="metric"><span>Модель</span><b>' + esc(engine.model || 'не загружена') + '</b></div>' +
      '<div class="metric"><span>Память модели</span><b>' + (engine.rss_mb || 0) + ' МБ</b></div>' +
      '<div class="metric"><span>Порт</span><b>' + (engine.port || '—') + '</b></div>' +
      '<div class="metric"><span>Работает</span><b>' + (engine.uptime ? engine.uptime + ' с' : '—') + '</b></div>';
  }
  const act = STATS.activity || {};
  const tokens = document.getElementById('monTokens');
  if (tokens) {
    tokens.innerHTML =
      '<div class="metric"><span>Токенов на входе</span><b>' + (act.tokens_in || 0) + '</b></div>' +
      '<div class="metric"><span>Токенов на выходе</span><b>' + (act.tokens_out || 0) + '</b></div>' +
      '<div class="metric"><span>Скорость вывода</span><b>' + (act.speed_out || 0) + ' ток/с</b></div>' +
      '<div class="metric"><span>Обработка входа</span><b>' + (act.speed_in || 0) + ' ток/с</b></div>' +
      '<div class="metric"><span>Запросов</span><b>' + (act.calls || 0) + '</b></div>';
  }
  const now = document.getElementById('monNow');
  if (now) {
    let html = '<div class="metric"><span>Состояние</span><b>' + esc(act.phase || 'ожидание') + '</b></div>';
    if (act.role) {
      html += '<div class="metric"><span>Роль</span><b>' + esc(act.role) + (act.step ? ' · ' + esc(act.step) : '') + '</b></div>';
    }
    if (act.tool) {
      html += '<div class="metric"><span>Инструмент</span><span class="roleChip">' + esc(act.tool) + '</span></div>';
    }
    now.innerHTML = html;
  }
}

async function pollStats() {
  try {
    const resp = await fetch('/api/stats');
    STATS = await resp.json();
    renderStats();
    renderStatusBar();
    if (window.Panels && window.Panels.renderAll) {
      try { window.Panels.renderAll(); } catch (error) { console.error('panels', error); }
    }
  } catch (err) {
    return;
  }
}

function toggleSide(which) {
  const side = document.getElementById(which === 'left' ? 'leftSide' : 'rightSide');
  if (side) side.classList.toggle('collapsed');
}

function renderProjects() {
  const box = document.getElementById('projectsList');
  if (!box) return;
  const items = (STATE.settings || {}).projects || [];
  if (!items.length) {
    box.innerHTML = '<div class="sub">Пока нет. Добавьте папку — агент получит к ней доступ.</div>';
    return;
  }
  box.innerHTML = items.map(function (path, index) {
    return '<div class="chatItem"><span class="t" title="' + esc(path) + '">📁 ' + esc(path.split(/[\\/]/).pop() || path) + '</span>' +
      '<span class="x" onclick="removeProject(' + index + ')">×</span></div>';
  }).join('');
}

async function addProject() {
  const input = document.getElementById('projPath');
  const path = (input && input.value || '').trim();
  if (!path) { toast('Введите путь к папке'); return; }
  const items = ((STATE.settings || {}).projects || []).concat([path]);
  await fetch('/api/projects', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ projects: items }) });
  if (input) input.value = '';
  toast('Проект добавлен');
  await loadState();
}

async function removeProject(index) {
  const items = ((STATE.settings || {}).projects || []).filter(function (_, i) { return i !== index; });
  await fetch('/api/projects', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ projects: items }) });
  await loadState();
}

function renderChats() {
  const box = document.getElementById('chatsList');
  if (!box) return;
  const items = STATE.chats || [];
  if (!items.length) {
    box.innerHTML = '<div class="sub">История пуста.</div>';
    return;
  }
  box.innerHTML = items.map(function (chat) {
    const active = chat.id === CURRENT_CHAT ? ' active' : '';
    const title = (chat.title || 'Без названия').slice(0, 42);
    return '<div class="chatItem' + active + '"><span class="t" onclick="openChat(\'' + chat.id + '\')" title="' + esc(chat.title || '') + '">' + esc(title) + '</span>' +
      '<span class="x" onclick="deleteChat(\'' + chat.id + '\')">×</span></div>';
  }).join('');
}

async function saveChat() {
  if (!HISTORY.length) return;
  const resp = await fetch('/api/chats/save', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: CURRENT_CHAT, messages: HISTORY })
  });
  const data = await resp.json();
  if (data && data.id) CURRENT_CHAT = data.id;
  await loadState();
}

async function openChat(id) {
  const data = await (await fetch('/api/chats/get?id=' + encodeURIComponent(id))).json();
  CURRENT_CHAT = id;
  HISTORY = (data.messages || []).filter(function (m) { return m.role === 'user' || m.role === 'assistant'; });
  const box = document.getElementById('messages');
  box.innerHTML = '';
  HISTORY.forEach(function (message) {
    if (!message.content) return;
    addMsg(message.role === 'user' ? 'user' : 'bot', message.content);
  });
  renderChats();
}

function newChat() {
  CURRENT_CHAT = '';
  HISTORY = [];
  document.getElementById('messages').innerHTML = '';
  renderChats();
  toast('Новый чат');
}

async function deleteChat(id) {
  await fetch('/api/chats/delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: id }) });
  if (id === CURRENT_CHAT) newChat();
  await loadState();
}

function toggleDrawer() {
  const drawer = document.getElementById('drawer');
  if (!drawer) return;
  drawer.classList.toggle('open');
  const chevron = document.getElementById('sbChevron');
  if (chevron) chevron.textContent = drawer.classList.contains('open') ? '▾' : '▴';
}

function showApproval(event) {
  const box = document.getElementById('approveModal');
  const body = document.getElementById('approveBody');
  if (!box || !body) return;
  const raw = JSON.stringify(event.args || {}, null, 1);
  body.innerHTML = '<h3>Нужно разрешение</h3><p>Агент хочет выполнить <b>' + esc(event.tool) + '</b>:</p>' +
    '<pre>' + esc(raw.slice(0, 800)) + '</pre>' +
    '<div class="row"><span></span><span>' +
    '<button class="ghost" id="denyBtn">Запретить</button> ' +
    '<button class="primary" id="allowBtn">Разрешить</button>' +
    '</span></div>';
  box.classList.add('show');
  const allow = document.getElementById('allowBtn');
  const deny = document.getElementById('denyBtn');
  if (allow) allow.onclick = function () { approveTool(event.id, true); };
  if (deny) deny.onclick = function () { approveTool(event.id, false); };
}
async function approveTool(id, allow) {
  await postJson('/api/approve', { id: id, allow: allow });
  document.getElementById('approveModal').classList.remove('show');
  toast(allow ? 'Разрешено' : 'Запрещено — агент получит отказ');
  const input = document.getElementById('input');
  if (input) input.focus();
}

function renderStatusBar() {
  const state = STATE || {};
  const engine = state.engine || {};
  const stats = STATS || {};
  const act = stats.activity || {};
  const mem = stats.memory || {};
  const set = function (id, html) {
    const el = document.getElementById(id);
    if (el) el.innerHTML = html;
  };
  const name = engine.model ? String(engine.model).split(/[\/]/).pop() : 'не загружена';
  set('sbModel', 'модель: <b>' + esc(name) + '</b>' + (engine.used_mb ? ' · ' + engine.used_mb + ' МБ' : ''));
  set('sbCpu', 'ЦП <b>' + Math.round(stats.cpu || 0) + '%</b>');
  set('sbRam', 'ОЗУ <b>' + (mem.used || 0) + '/' + (mem.total || 0) + ' ГБ</b>');
  set('sbTok', 'токены <b>' + (act.tokens_in || 0) + ' → ' + (act.tokens_out || 0) + '</b>');
  set('sbSpeed', '<b>' + (act.speed_out || 0) + '</b> ток/с');
  set('sbNow', act.tool ? ('инструмент: <b>' + esc(act.tool) + '</b>') : ('<b>' + esc(act.phase || 'ожидание') + '</b>'));
  set('sbPort', 'порт ' + (engine.port || '—'));
}


let VOICE = null;
let REC = null;

async function loadVoice() {
  try {
    VOICE = await (await fetch('/api/voice')).json();
  } catch (err) {
    VOICE = null;
  }
}

function renderVoice() {
  const box = document.getElementById('voicePanel');
  if (!box) return;
  const voice = VOICE || {};
  let html = '<div class="row"><span>Голосовой ввод (whisper.cpp)' +
    tip('Распознаёт вашу речь MoE и вставляет текстом. Нужна одна загрузка ~180 МБ, дальше всё офлайн.') +
    '</span><span>' + (voice.ready ? badge('готов', 'ok') : badge('нужно скачать', 'no')) + '</span></div>';
  if (!voice.ready) {
    html += '<div class="row"><span></span><button class="ghost" onclick="installVoice()">Скачать распознавание речи</button></div>';
  }
  if (voice.job && voice.job.active) {
    html += '<div class="progress"><i style="width:' + (voice.job.pct || 0) + '%"></i></div><div class="sub">' + esc(voice.job.text || '') + '</div>';
  }
  const models = voice.models || [];
  if (models.length) {
    let options = '';
    models.forEach(function (item) {
      options += '<option value="' + item.id + '"' + (item.id === voice.model_name ? ' selected' : '') + '>' +
        esc(item.title) + (item.installed ? '' : ' — скачать') + '</option>';
    });
    html += '<div class="row"><span>Точность распознавания' +
      tip('Чем больше модель, MoE лучше понимает речь и меньше ошибается. «Максимальная точность» — 574 МБ, но работает офлайн и заметно точнее.') +
      '</span><select id="voiceModel">' + options + '</select></div>';
  }
  html += '<label class="row"><span>Озвучивать ответы голосом</span><input type="checkbox" id="voiceTts"' +
    ((STATE.settings || {}).voice_tts ? ' checked' : '') + '></label>';
  html += '<div class="sub">Горячая клавиша: Ctrl+Alt+Space — начать/закончить говорить.</div>';
  box.innerHTML = html;
  const tts = document.getElementById('voiceTts');
  if (tts) tts.addEventListener('change', saveVoiceTts);
  const picker = document.getElementById('voiceModel');
  if (picker) picker.addEventListener('change', saveVoiceModel);
}

async function saveVoiceTts(event) {
  await fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ voice_tts: event.target.checked }) });
  await loadState();
  toast(event.target.checked ? 'Буду озвучивать ответы' : 'Озвучивание выключено');
}

async function installVoice() {
  await fetch('/api/voice/install', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  toast('Скачиваю распознавание речи…');
  const timer = setInterval(async function () {
    await loadVoice();
    renderVoice();
    if (VOICE && VOICE.ready) { clearInterval(timer); toast('Распознавание готово'); }
  }, 4000);
}

function encodeWav(samples, rate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const writeText = function (offset, text) {
    for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i));
  };
  writeText(0, 'RIFF'); view.setUint32(4, 36 + samples.length * 2, true); writeText(8, 'WAVE');
  writeText(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
  view.setUint16(22, 1, true); view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true); writeText(36, 'data');
  view.setUint32(40, samples.length * 2, true);
  let offset = 44;
  for (let i = 0; i < samples.length; i++) {
    const value = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, value < 0 ? value * 0x8000 : value * 0x7fff, true);
    offset += 2;
  }
  return new Blob([view], { type: 'audio/wav' });
}

function blobToBase64(blob) {
  return new Promise(function (resolve) {
    const reader = new FileReader();
    reader.onloadend = function () { resolve(String(reader.result)); };
    reader.readAsDataURL(blob);
  });
}

async function toggleMic() {
  if (REC) { await stopRecording(); return; }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
    let ctx;
    try {
      ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
    } catch (err) {
      ctx = new (window.AudioContext || window.webkitAudioContext)();
    }
    const source = ctx.createMediaStreamSource(stream);
    const node = ctx.createScriptProcessor(4096, 1, 1);
    const chunks = [];
    node.onaudioprocess = function (event) { chunks.push(new Float32Array(event.inputBuffer.getChannelData(0))); };
    source.connect(node);
    node.connect(ctx.destination);
    REC = { stream: stream, ctx: ctx, node: node, chunks: chunks, rate: ctx.sampleRate || 16000, started: Date.now(), timer: null };
    REC.timer = setInterval(function () {
      if (!REC) return;
      const seconds = Math.floor((Date.now() - REC.started) / 1000);
      const label = document.getElementById('micBtn');
      if (label) label.textContent = '⏺ ' + Math.floor(seconds / 60) + ':' + ('0' + (seconds % 60)).slice(-2);
    }, 500);
    document.getElementById('micBtn').classList.add('rec');
    toast('Говорите… нажмите 🎤 или Ctrl+Alt+Space, чтобы закончить');
  } catch (err) {
    let hint = err && err.name === 'NotAllowedError'
      ? 'Разрешите доступ: значок замка в адресной строке → Микрофон → Разрешить.'
      : 'Проверьте, что микрофон подключён и не занят другой программой.';
    toast('Микрофон недоступен: ' + (err && err.message || err) + '. ' + hint);
  }
}

async function stopRecording() {
  const rec = REC;
  REC = null;
  const button = document.getElementById('micBtn');
  if (button) { button.classList.remove('rec'); button.textContent = '🎤'; }
  if (rec && rec.timer) clearInterval(rec.timer);
  try {
    rec.node.disconnect();
    rec.ctx.close();
    rec.stream.getTracks().forEach(function (track) { track.stop(); });
  } catch (err) { /* уже закрыто */ }
  let total = 0;
  rec.chunks.forEach(function (chunk) { total += chunk.length; });
  const pcm = new Float32Array(total);
  let offset = 0;
  rec.chunks.forEach(function (chunk) { pcm.set(chunk, offset); offset += chunk.length; });
  const audio = await blobToBase64(encodeWav(pcm, (rec && rec.rate) || 16000));
  toast('Распознаю…');
  const data = await postJson('/api/voice/transcribe', { audio: audio });
  if (!data.ok) { toast('Не распознал: ' + (data.error || 'ошибка')); return; }
  const text = String(data.text || '').trim();
  if (!text) { toast('Ничего не разобрал — попробуйте ещё раз'); return; }
  handleVoiceText(text);
}

function handleVoiceText(text) {
  const clean = String(text || "").trim();
  if (!clean) { toast('Ничего не разобрал — попробуйте ещё раз'); return; }
  const low = clean.toLowerCase().replace(/[.!?]+$/, "");
  const short = low.split(/\s+/).length <= 4;
  if (short && low.indexOf('новый чат') >= 0) { newChat(); toast('Сделал: новый чат'); return; }
  if (short && low.indexOf('выгрузи') >= 0 && low.indexOf('модел') >= 0) { unloadModel(); return; }
  if (short && low.indexOf('включи') >= 0 && low.indexOf('команд') >= 0) {
    const team = document.querySelector('.mode[data-mode=team]');
    if (team) team.click();
    return;
  }
  if (short && low.indexOf('выключи') >= 0 && low.indexOf('команд') >= 0) {
    const solo = document.querySelector('.mode[data-mode=agent]');
    if (solo) solo.click();
    return;
  }
  const open = low.match(/^открой папку (.+)$/);
  if (open) {
    postJson('/api/open', { path: open[1].trim() });
    toast('Открываю ' + open[1].trim());
    return;
  }
  const input = document.getElementById('input');
  input.value = clean;
  input.classList.add('voiceFill');
  input.focus();
  input.setSelectionRange(clean.length, clean.length);
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 200) + 'px';
  toast('Распознано (' + clean.length + ' символов) — проверьте и нажмите «Отправить»');
  setTimeout(function () { input.classList.remove('voiceFill'); }, 2500);
}

async function speakAnswer() {
  if (!(STATE.settings || {}).voice_tts) return;
  const answers = HISTORY.filter(function (item) { return item.role === 'assistant' && item.content; });
  const last = answers[answers.length - 1];
  if (!last) return;
  postJson('/api/voice/say', { text: String(last.content).slice(0, 600) });
}
function render() {
  const server = STATE.server || {};
  const dot = document.getElementById('dot');
  dot.className = 'dot' + (server.running ? ' on' : '');
  document.getElementById('statusText').textContent = server.running
    ? 'модель загружена'
    : (STATE.engine.installed ? 'модель не загружена' : 'движок не установлен');
  renderHardware();
  renderAdvice();
  renderTuning();
  if (window.Panels && window.Panels.renderAll) {
    try { window.Panels.renderAll(); } catch (error) { console.error('panels', error); }
  }
  renderLocalModels();
  renderCatalog();
  renderHfState();
  renderEmbeddings();
  updateHfProgress(STATE.download);
  renderSettings();
  renderProvider();
  renderRoles();
  loadMcp().then(renderMcp);
  loadVoice().then(renderVoice);
  renderChats();
  renderProjects();
  if (STATE.download && STATE.download.active) poll();
}

const TEMPLATES = {
  audit: 'Проверь папку и скажи, что улучшить. Сначала посмотри файлы, потом выводы.',
  review: 'Сделай ревью кода в папке: ошибки, риски, что упростить.',
  kb: 'Найди в базе знаний: ',
  spec: 'Собери короткое ТЗ: цель, шаги, результат.'
};

function useTemplate(name) {
  const text = TEMPLATES[name];
  if (!text) return;
  const input = document.getElementById('input');
  input.value = text;
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
}

async function loadModelDirect(path) {
  toast('Загружаю модель…');
  const res = await postJson('/api/model/load', { path: path });
  if (res && res.ok) {
    toast('Модель готова');
    await loadState();
  } else {
    toast('Не вышло: ' + ((res && res.error) || 'ошибка'));
  }
}

async function unloadDirect() {
  await postJson('/api/model/unload', {});
  toast('Модель выгружена');
  await loadState();
}

function modelLabel(item) {
  const name = String(item.file || item.id || '');
  return name.length > 34 ? name.slice(0, 31) + '…' : name;
}

function renderAdvice() {
  const box = document.getElementById('modelAdvice');
  if (!box) return;
  const models = STATE.models || [];
  const hw = STATE.hardware || {};
  const free = Number(hw.ram_free_gb || 0);
  if (!models.length) {
    box.innerHTML = '<div class="sub">Моделей нет. Вкладка Модели, кнопка Искать.</div>';
    return;
  }
  const fits = models.filter(function (m) { return Number(m.size_gb || 0) <= free * 0.6; });
  const small = fits.filter(function (m) { return Number(m.size_gb || 0) <= 5.5; });
  const pick = function (list) {
    return list.length ? list.reduce(function (a, b) { return Number(a.size_gb) > Number(b.size_gb) ? a : b; }) : null;
  };
  const fast = pick(small);
  const strong = pick(fits);

  const line = function (title, item, note) {
    if (!item) return '<div class="advice"><b>' + title + '</b>: нет подходящей</div>';
    return '<div class="advice"><b>' + title + '</b> · ' + gb(item.size_gb) +
      '<div class="sub">' + esc(String(item.file)) + '</div>' +
      '<div class="sub">' + note + '</div>' +
      '<button class="ghost tiny" data-load="' + esc(item.id) + '">Загрузить</button></div>';
  };

  box.innerHTML = line('Быстро', fast, 'до 5.5 ГБ, отвечает резво') +
    line('Качественно', strong, 'до 60% свободной памяти, медленнее') +
    '<div class="sub">свободно ' + free.toFixed(1) + ' ГБ</div>';

  Array.from(box.querySelectorAll('[data-load]')).forEach(function (button) {
    button.onclick = function () { loadModelDirect(button.getAttribute('data-load')); };
  });
}

function renderTuning() {
  const box = document.getElementById('modelTuning');
  if (!box) return;
  const s = STATE.settings || {};
  const temp = Number(s.temperature || 0.7);
  const ctx = Number(s.context || 8192);
  const temps = [[0.2, 'точная'], [0.4, 'спокойная'], [0.7, 'обычная'], [1, 'смелая']];
  const ctxs = [[4096, '4 тыс.'], [8192, '8 тыс.'], [16384, '16 тыс.'], [32768, '32 тыс.']];
  let html = '<div class="tuneRow"><span>Температура</span><select id="tuneTemp">';
  temps.forEach(function (pair) {
    html += '<option value="' + pair[0] + '"' + (Math.abs(pair[0] - temp) < 0.05 ? ' selected' : '') + '>' + pair[1] + '</option>';
  });
  html += '</select></div>';
  html += '<div class="tuneRow"><span>Контекст</span><select id="tuneCtx">';
  ctxs.forEach(function (pair) {
    html += '<option value="' + pair[0] + '"' + (pair[0] === ctx ? ' selected' : '') + '>' + pair[1] + ' токенов</option>';
  });
  html += '</select></div>';
  box.innerHTML = html;
  const t = document.getElementById('tuneTemp');
  const c = document.getElementById('tuneCtx');
  if (t) t.onchange = function () { postJson('/api/settings', { temperature: Number(t.value) }); };
  if (c) c.onchange = function () { postJson('/api/settings', { context: Number(c.value) }); };
}

async function openGear() {
  const box = document.getElementById('gearModal');
  if (!box) return;
  box.classList.add('show');
  try {
    const info = await (await fetch('/api/version')).json();
    window.__dataDir = info.data_dir || '';
    document.getElementById('gearVersion').textContent = 'v' + (info.version || '—');
    document.getElementById('gearPort').textContent = info.port || '—';
    const pending = info.pending;
    if (pending && pending.version) {
      document.getElementById('gearUpdateText').textContent = 'Готово к установке: ' + pending.version;
    }
  } catch (error) {
    document.getElementById('gearVersion').textContent = 'нет связи';
  }
}

async function checkUpdates() {
  const text = document.getElementById('gearUpdateText');
  text.textContent = 'Проверяю…';
  const res = await postJson('/api/update/check', {});
  if (!res || res.ok === false) {
    text.textContent = (res && res.error) || 'не удалось проверить';
    return;
  }
  text.textContent = res.update
    ? ('Есть версия ' + res.latest + ' — нажмите «Проверить» ещё раз для установки')
    : ('У вас последняя версия ' + res.current);
}

function applyLook() {
  const theme = localStorage.getItem('agentTheme') || 'dark';
  const font = localStorage.getItem('agentFont') || '15';
  document.body.classList.toggle('light', theme === 'light');
  document.body.classList.toggle('bigFont', font === '17');
  document.body.classList.toggle('hugeFont', font === '19');
  const themeSelect = document.getElementById('gearTheme');
  const fontSelect = document.getElementById('gearFont');
  if (themeSelect) themeSelect.value = theme;
  if (fontSelect) fontSelect.value = font;
}

function wireResizers() {
  const grid = document.querySelector('.drawerGrid');
  if (!grid) return;
  const cols = Array.from(grid.querySelectorAll('.drawerCol'));
  let saved = [];
  try {
    saved = JSON.parse(localStorage.getItem('agentDrawerWidths') || '[]');
  } catch (error) {
    saved = [];
  }
  cols.forEach(function (col, index) {
    if (saved[index] && Number(saved[index]) > 60) col.style.flex = '0 0 ' + saved[index] + 'px';
  });
  Array.from(grid.querySelectorAll('.colResizer')).forEach(function (bar, index) {
    bar.addEventListener('mousedown', function (event) {
      event.preventDefault();
      const left = cols[index];
      const right = cols[index + 1];
      if (!left || !right) return;
      const startX = event.clientX;
      const leftWidth = left.getBoundingClientRect().width;
      const rightWidth = right.getBoundingClientRect().width;
      const move = function (moveEvent) {
        const delta = moveEvent.clientX - startX;
        left.style.flex = '0 0 ' + Math.max(160, leftWidth + delta) + 'px';
        right.style.flex = '0 0 ' + Math.max(160, rightWidth - delta) + 'px';
      };
      const up = function () {
        document.removeEventListener('mousemove', move);
        document.removeEventListener('mouseup', up);
        const widths = cols.map(function (col) { return Math.round(col.getBoundingClientRect().width); });
        localStorage.setItem('agentDrawerWidths', JSON.stringify(widths));
      };
      document.addEventListener('mousemove', move);
      document.addEventListener('mouseup', up);
    });
  });
}
function wireGear() {
  const gear = document.getElementById('gearBtn');
  if (gear) gear.onclick = openGear;
  const close = document.getElementById('gearClose');
  if (close) close.onclick = function () { document.getElementById('gearModal').classList.remove('show'); };
  const box = document.getElementById('gearModal');
  if (box) box.onclick = function (event) { if (event.target === box) box.classList.remove('show'); };
  const check = document.getElementById('gearCheck');
  if (check) check.onclick = checkUpdates;
  const data = document.getElementById('gearData');
  if (data) data.onclick = function () { postJson('/api/open', { path: window.__dataDir || '' }); };
  const prov = document.getElementById('gearProviders');
  if (prov) prov.onclick = function () {
    box.classList.remove('show');
    const tab = document.querySelector('.tab[data-tab="settings"]');
    if (tab) tab.click();
  };
  const theme = document.getElementById('gearTheme');
  if (theme) theme.onchange = function () { localStorage.setItem('agentTheme', theme.value); applyLook(); };
  const font = document.getElementById('gearFont');
  if (font) font.onchange = function () { localStorage.setItem('agentFont', font.value); applyLook(); };
  const trayBox = document.getElementById('gearTray');
  if (trayBox) {
    trayBox.checked = !!(STATE.settings && STATE.settings.tray);
    trayBox.onchange = async function () {
      await postJson('/api/settings', { tray: trayBox.checked });
      toast('Готово. Изменение применится после перезапуска');
    };
  }
  const plus = document.getElementById('newChatBtn');
  if (plus) plus.onclick = function () { newChat(); };
  applyLook();
}

async function loadAutostart() {
  const box = document.getElementById('autostart');
  if (!box) return;
  try {
    const res = await fetch('/api/autostart');
    const data = await res.json();
    box.checked = !!data.enabled;
  } catch (error) {
    box.checked = false;
  }
}

async function toggleAutostart(enabled) {
  const res = await postJson('/api/autostart', { enabled: enabled });
  if (res && res.ok) toast(enabled ? 'Автозапуск: включено' : 'Автозапуск: выключено');
  else toast('Не вышло: ' + ((res && res.detail) || 'нет ответа'));
}

async function loadTelegram() {
  const state = document.getElementById('tgState');
  if (!state) return;
  const res = await fetch('/api/telegram');
  const data = await res.json();
  state.textContent = data.token_set
    ? ('подключён' + (data.bot ? ': @' + data.bot : '') + (data.chat ? ', чат ' + data.chat : ''))
    : 'бот не подключён';
  const chat = document.getElementById('tgChat');
  if (chat && data.chat) chat.value = data.chat;
}

async function saveTelegram() {
  const token = document.getElementById('tgToken');
  const chat = document.getElementById('tgChat');
  await postJson('/api/telegram', { token: (token.value || '').trim(), chat: (chat.value || '').trim() });
  if (token) token.value = '';
  toast('Сохранено, проверяю бота');
  loadTelegram();
}
async function loadLan() {
  const box = document.getElementById('lanAccess');
  const hint = document.getElementById('lanHint');
  const links = document.getElementById('lanLinks');
  if (!box) return;
  const res = await fetch('/api/lan');
  const data = await res.json();
  box.checked = !!data.enabled;
  if (hint) hint.textContent = data.enabled
    ? 'Включено: телефон в той же сети откроет агента по ссылке ниже.'
    : 'Выключено: сервер слушает только этот компьютер.';
  if (links) links.innerHTML = (data.urls || []).map(function (url) {
    return '<div>' + esc(url) + '</div>';
  }).join('');
  box.onchange = async function () {
    await postJson('/api/settings', { lan_access: box.checked });
    toast('Перезапустите приложение, чтобы применить');
  };
}
async function loadTasks() {
  const res = await fetch('/api/schedule');
  const data = await res.json();
  renderTasks(data.items || []);
}

function renderTasks(items) {
  const box = document.getElementById('taskList');
  if (!box) return;
  if (!items.length) { box.innerHTML = '<div class="sub">Задач пока нет.</div>'; return; }
  box.innerHTML = items.map(function (item) {
    return '<div class="row"><span><b>' + esc(item.time) + '</b> ' + esc(item.days || 'daily') +
      '<div class="sub">' + esc((item.task || '').slice(0, 90)) + '</div></span>' +
      '<span><button class="ghost tiny" data-run="' + esc(item.id) + '">Запустить</button> ' +
      '<button class="ghost tiny" data-del="' + esc(item.id) + '">Удалить</button></span></div>';
  }).join('');
  Array.prototype.slice.call(box.querySelectorAll('[data-run]')).forEach(function (button) {
    button.onclick = function () { runTask(button.getAttribute('data-run')); };
  });
  Array.prototype.slice.call(box.querySelectorAll('[data-del]')).forEach(function (button) {
    button.onclick = function () { deleteTask(button.getAttribute('data-del')); };
  });
}

async function saveTask() {
  const item = {
    time: document.getElementById('taskTime').value || '09:00',
    days: document.getElementById('taskDays').value,
    mode: document.getElementById('taskMode').value,
    task: (document.getElementById('taskPrompt').value || '').trim()
  };
  if (!item.task) { toast('Опишите задачу'); return; }
  const res = await postJson('/api/schedule/save', item);
  toast(res && res.ok ? 'Задача сохранена' : 'Не вышло: ' + ((res && res.error) || 'ошибка'));
  document.getElementById('taskPrompt').value = '';
  loadTasks();
}

async function deleteTask(id) {
  await postJson('/api/schedule/delete', { id: id });
  loadTasks();
}

async function runTask(id) {
  await postJson('/api/schedule/run', { id: id });
  toast('Задача запущена, результат будет в чатах');
}
/* база знаний: три колонки */
var kbDocs = [];
var kbStats = { count: 0, chunks: 0, size: 0 };
var kbFolder = '';
var kbOpenDirs = {};
var kbSelected = null;
var kbHits = [];
var kbMode = 'files';
var kbQueryText = '';
var kbFilterText = '';
var kbTextCache = {};
var kbSeq = 0;

function kbFmtSize(bytes) {
  var n = Number(bytes) || 0;
  if (n >= 1048576) return (n / 1048576).toFixed(1) + ' МБ';
  if (n >= 1024) return Math.round(n / 1024) + ' КБ';
  return n + ' Б';
}

function kbBaseName(path) {
  var parts = String(path || '').split(/[\\/]/);
  return parts[parts.length - 1] || '';
}

function kbDirName(path) {
  var parts = String(path || '').split(/[\\/]/);
  parts.pop();
  return parts.join('\\');
}

function kbExt(path) {
  var name = kbBaseName(path);
  var dot = name.lastIndexOf('.');
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : 'файл';
}

function kbFmtDate(value) {
  var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value || ''));
  return m ? (m[3] + '.' + m[2] + '.' + m[1].slice(2)) : '—';
}

function kbSegments(path) {
  return String(path || '').split(/[\\/]+/).filter(function (part) { return part.length > 0; });
}

function kbCommonBase(docs) {
  var first = kbSegments(kbDirName(docs[0].path));
  var keep = first.length;
  for (var i = 1; i < docs.length; i++) {
    var seg = kbSegments(kbDirName(docs[i].path));
    var j = 0;
    while (j < keep && j < seg.length && seg[j].toLowerCase() === first[j].toLowerCase()) j++;
    keep = j;
  }
  return first.slice(0, keep);
}

function kbTree() {
  var docs = kbDocs.slice().sort(function (a, b) { return String(a.path).toLowerCase() < String(b.path).toLowerCase() ? -1 : 1; });
  var base = docs.length ? kbCommonBase(docs) : [];
  var root = { path: base.join('\\'), name: base.length ? base[base.length - 1] : '/', children: {}, count: 0, chunks: 0 };
  docs.forEach(function (doc) {
    var rel = kbSegments(doc.path).slice(base.length);
    var node = root;
    node.count += 1;
    node.chunks += Number(doc.chunks) || 0;
    for (var i = 0; i < rel.length - 1; i++) {
      if (!node.children[rel[i]]) {
        node.children[rel[i]] = { path: node.path ? node.path + '\\' + rel[i] : rel[i], name: rel[i], children: {}, count: 0, chunks: 0 };
      }
      node = node.children[rel[i]];
      node.count += 1;
      node.chunks += Number(doc.chunks) || 0;
    }
  });
  return root;
}

async function loadKB() {
  var box = document.getElementById('kbFolders');
  if (!box) return;
  try {
    var res = await fetch('/api/rag');
    var data = await res.json();
    kbDocs = Array.isArray(data.docs) ? data.docs : (data.items || []);
    kbStats = { count: data.count || kbDocs.length, chunks: data.chunks || 0, size: data.size_mb || 0 };
    if (kbSelected && kbSelected.hitIndex === undefined) {
      var fresh = null;
      for (var i = 0; i < kbDocs.length; i++) { if (kbDocs[i].path === kbSelected.path) { fresh = kbDocs[i]; break; } }
      kbSelected = fresh;
    }
    kbRenderAll();
  } catch (error) {
    box.innerHTML = '<div class="kbEmpty">Не удалось прочитать базу.</div>';
  }
}

function kbRenderAll() {
  var stat = document.getElementById('kbStat');
  if (stat) {
    stat.textContent = 'Файлов: ' + (kbStats.count || kbDocs.length) + ' · фрагментов: ' + (kbStats.chunks || 0) + ' · ' + (kbStats.size || 0) + ' МБ';
  }
  kbRenderFolders();
  kbRenderList();
  kbRenderPreview();
}

function kbRenderFolders() {
  var box = document.getElementById('kbFolders');
  if (!box) return;
  if (!kbDocs.length) {
    box.innerHTML = '<div class="kbEmpty">Пока пусто. Добавьте папку или файл выше.</div>';
    return;
  }
  var rows = [{ path: '', name: 'Все файлы', count: kbStats.count || kbDocs.length, chunks: kbStats.chunks || 0, depth: 0, kids: false, open: false }];
  (function walk(node, depth) {
    Object.keys(node.children).sort(function (a, b) { return a.toLowerCase() < b.toLowerCase() ? -1 : 1; }).forEach(function (key) {
      var child = node.children[key];
      var kids = Object.keys(child.children).length > 0;
      var open = Object.prototype.hasOwnProperty.call(kbOpenDirs, child.path) ? !!kbOpenDirs[child.path] : depth < 1;
      rows.push({ path: child.path, name: child.name, count: child.count, chunks: child.chunks, depth: depth, kids: kids, open: open });
      if (kids && open) walk(child, depth + 1);
    });
  })(kbTree(), 0);
  box.innerHTML = rows.map(function (row) {
    var pad = 6 + row.depth * 12;
    return '<div class="kbNode' + (kbFolder === row.path ? ' sel' : '') + '" data-kbdir="' + esc(row.path) + '" data-kbdepth="' + row.depth + '" data-kbkids="' + (row.kids ? '1' : '0') + '" style="padding-left:' + pad + 'px">' +
      '<span class="kbCar">' + (row.kids ? (row.open ? '▾' : '▸') : '·') + '</span>' +
      '<span class="kbNodeName" title="' + esc(row.path || row.name) + '">' + esc(row.name) + '</span>' +
      '<span class="kbCount" title="файлов / фрагментов">' + row.count + '/' + row.chunks + '</span></div>';
  }).join('');
  Array.prototype.forEach.call(box.querySelectorAll('[data-kbdir]'), function (el) {
    el.onclick = function () {
      var path = el.getAttribute('data-kbdir');
      var depth = Number(el.getAttribute('data-kbdepth')) || 0;
      if (el.getAttribute('data-kbkids') === '1') {
        var wasOpen = Object.prototype.hasOwnProperty.call(kbOpenDirs, path) ? !!kbOpenDirs[path] : depth < 1;
        kbOpenDirs[path] = !wasOpen;
      }
      kbFolder = path;
      kbMode = 'files';
      kbQueryText = '';
      var queryField = document.getElementById('kbQuery');
      if (queryField) queryField.value = '';
      kbRenderFolders();
      kbRenderList();
    };
  });
}

function kbDocsIn(path) {
  if (!path) return kbDocs.slice();
  var prefix = (String(path) + '\\').toLowerCase();
  return kbDocs.filter(function (doc) { return String(doc.path).toLowerCase().indexOf(prefix) === 0; });
}

function kbShortPath(path) {
  var parts = String(path || '').split(/[\\/]/);
  return parts.slice(-2).join('/');
}

function kbMark(text, query) {
  var raw = String(text === null || text === undefined ? '' : text);
  var terms = String(query || '').split(/\s+/).filter(function (term) { return term.length > 1; });
  if (!terms.length) return esc(raw);
  var pattern = new RegExp('(' + terms.map(function (term) { return term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }).join('|') + ')', 'gi');
  return raw.split(pattern).map(function (part, index) {
    if (index % 2 === 1) return '<mark>' + esc(part) + '</mark>';
    return esc(part);
  }).join('');
}

function kbRenderList() {
  var box = document.getElementById('kbList');
  var title = document.getElementById('kbListTitle');
  if (!box) return;
  var filterRow = document.querySelector('.kbFilterRow');
  if (filterRow) filterRow.style.display = kbMode === 'search' ? 'none' : '';
  if (kbMode === 'search') {
    if (title) title.textContent = 'Результаты';
    if (!kbHits.length) { box.innerHTML = '<div class="kbEmpty">Ничего не нашлось.</div>'; return; }
    box.innerHTML = kbHits.map(function (hit, index) {
      var on = kbSelected && kbSelected.hitIndex === index ? ' sel' : '';
      return '<div class="kbHit' + on + '" data-kbhit="' + index + '">' +
        '<div class="kbHitTop"><span class="kbHitPath" title="' + esc(hit.path) + '">' + esc(kbShortPath(hit.path)) + '</span>' +
        '<span class="kbScore">' + (Number(hit.score) || 0).toFixed(2) + '</span></div>' +
        '<div class="kbHitText">' + kbMark(String(hit.text || '').slice(0, 220), kbQueryText) + '</div></div>';
    }).join('');
    Array.prototype.forEach.call(box.querySelectorAll('[data-kbhit]'), function (el) {
      el.onclick = function () { kbSelectHit(Number(el.getAttribute('data-kbhit'))); };
    });
    return;
  }
  if (title) title.textContent = 'Файлы';
  var docs = kbDocsIn(kbFolder);
  var needle = String(kbFilterText || '').trim().toLowerCase();
  if (needle) {
    docs = docs.filter(function (doc) { return kbBaseName(doc.path).toLowerCase().indexOf(needle) >= 0; });
  }
  if (!docs.length) {
    box.innerHTML = '<div class="kbEmpty">' + (needle ? 'Ничего не нашлось.' : 'В этой папке пусто.') + '</div>';
    return;
  }
  box.innerHTML = docs.map(function (doc) {
    var on = kbSelected && kbSelected.hitIndex === undefined && kbSelected.path === doc.path ? ' sel' : '';
    return '<div class="kbFile' + on + '" data-kbpath="' + esc(doc.path) + '">' +
      '<div class="kbFileMain"><span class="kbFileName" title="' + esc(doc.path) + '">' + esc(kbBaseName(doc.path)) + '</span>' +
      '<span class="kbTag">' + esc(kbExt(doc.path)) + '</span></div>' +
      '<div class="kbFileMeta">' + (Number(doc.chunks) || 0) + ' фрагм. · ' + kbFmtSize(doc.size) + ' · ' + kbFmtDate(doc.added) + '</div>' +
      '<button class="kbX" data-kbdel="' + esc(doc.path) + '" title="Убрать из базы">✕</button></div>';
  }).join('');
  Array.prototype.forEach.call(box.querySelectorAll('[data-kbpath]'), function (el) {
    el.onclick = function () { kbSelectDoc(el.getAttribute('data-kbpath')); };
  });
  Array.prototype.forEach.call(box.querySelectorAll('[data-kbdel]'), function (el) {
    el.onclick = function (event) {
      event.stopPropagation();
      kbRemove(el.getAttribute('data-kbdel'));
    };
  });
}

function kbSelectDoc(path) {
  var found = null;
  for (var i = 0; i < kbDocs.length; i++) { if (kbDocs[i].path === path) { found = kbDocs[i]; break; } }
  kbSelected = found ? { path: found.path, chunks: found.chunks, size: found.size, added: found.added } : null;
  kbRenderList();
  kbRenderPreview();
}

function kbSelectHit(index) {
  var hit = kbHits[index];
  if (!hit) return;
  var meta = null;
  for (var i = 0; i < kbDocs.length; i++) { if (kbDocs[i].path === hit.path) { meta = kbDocs[i]; break; } }
  kbSelected = {
    path: hit.path,
    chunks: meta ? meta.chunks : 0,
    size: meta ? meta.size : 0,
    added: meta ? meta.added : '',
    hit: hit,
    hitIndex: index
  };
  kbRenderList();
  kbRenderPreview();
}

function kbRenderPreview() {
  var box = document.getElementById('kbPreview');
  if (!box) return;
  if (!kbSelected) {
    box.innerHTML = '<div class="kbEmpty">Выберите файл — покажу начало текста.</div>';
    return;
  }
  var sel = kbSelected;
  var meta = [kbFmtSize(sel.size), (Number(sel.chunks) || 0) + ' фрагм.'];
  if (sel.added) meta.push(kbFmtDate(sel.added));
  var html = '<div class="kbPrev">' +
    '<div class="kbPrevName">' + esc(kbBaseName(sel.path)) + '</div>' +
    '<div class="kbPrevPath">' + esc(sel.path) + '</div>' +
    '<div class="kbPrevMeta"><span>' + esc(meta.join(' · ')) + '</span>' +
    '<button class="ghost tiny" id="kbOpenBtn">Открыть в системе</button></div>';
  if (sel.hit) {
    html += '<div class="kbPrevMeta"><span class="kbSrc">Фрагмент · ' + esc(kbShortPath(sel.path)) + '</span>' +
      '<span class="kbScore">' + (Number(sel.hit.score) || 0).toFixed(2) + '</span></div>' +
      '<div class="kbSnippet">' + kbMark(sel.hit.text, kbQueryText) + '</div>';
  }
  html += '<div class="kbPrevMeta" id="kbBadgeRow"></div>' +
    '<pre class="kbText" id="kbTextBox">Загрузка…</pre></div>';
  box.innerHTML = html;
  var button = document.getElementById('kbOpenBtn');
  if (button) button.onclick = function () { kbOpen(sel.path); };
  kbLoadText(sel);
}

async function kbLoadText(sel) {
  var seq = ++kbSeq;
  var text = kbTextCache[sel.path];
  if (text === undefined) {
    try {
      var res = await postJson('/api/attach', { path: sel.path });
      text = res && res.ok ? String(res.text || '') : '';
    } catch (error) {
      text = '';
    }
    if (text) kbTextCache[sel.path] = text;
  }
  if (seq !== kbSeq) return;
  var pre = document.getElementById('kbTextBox');
  if (!pre) return;
  if (!text) {
    pre.textContent = sel.hit ? 'Фрагмент выше. Текст не прочитан.' : 'Текст не прочитан.';
    return;
  }
  var limit = 1500;
  pre.innerHTML = kbMark(text.slice(0, limit), kbQueryText);
  var row = document.getElementById('kbBadgeRow');
  if (row && text.length > limit) {
    row.innerHTML = '<span class="kbBadge">обрезано</span><span>' + text.length + ' симв., показано ' + limit + '</span>';
  }
}

async function kbRemove(path) {
  await postJson('/api/rag/remove', { path: path });
  delete kbTextCache[path];
  if (kbSelected && kbSelected.path === path) kbSelected = null;
  toast('Убрано из базы');
  await loadKB();
}

async function kbOpen(path) {
  try {
    var res = await postJson('/api/open', { path: path });
    if (res && res.ok === false) toast('Не уда|лось от|крыть');
  } catch (error) {
    toast('Не уда|лось от|крыть');
  }
}

async function waitJob(name, timeoutMs) {
  var deadline = Date.now() + (timeoutMs || 600000);
  while (Date.now() < deadline) {
    try {
      var res = await fetch('/api/jobs');
      var data = await res.json();
      var job = (data.jobs || {})[name];
      if (job && job.done) return job.result || {};
    } catch (error) {
      return { ok: false, error: 'Нет ответа' };
    }
    await new Promise(function (resolve) { setTimeout(resolve, 800); });
  }
  return { ok: false, error: 'Не дождался' };
}

async function kbAdd() {
  var input = document.getElementById('kbPath');
  var status = document.getElementById('kbStatus');
  var button = document.getElementById('kbAdd');
  var path = ((input && input.value) || '').trim();
  if (!path) { toast('Пу|ть не у|казан'); return; }
  if (status) status.textContent = 'Ин|дексирую…';
  if (button) button.disabled = true;
  try {
    var res = await postJson('/api/rag/add', { path: path });
    if (res && res.started) {
      if (status) status.textContent = 'Ин|дексирую… э|то мо|жет зан|ять мин|уту';
      res = await waitJob('rag_add');
    }
    if (res && res.ok) {
      if (status) status.textContent = 'Го|тово: фа|йлов ' + (res.files || 0) + ', фрагментов ' + (res.chunks || 0);
      if (input) input.value = '';
      await loadKB();
    } else if (status) {
      status.textContent = (res && res.error) || 'Не пол|училось';
    }
  } catch (error) {
    if (status) status.textContent = 'Ош|ибка: ' + String(error && error.message || error);
  } finally {
    if (button) button.disabled = false;
  }
}

async function kbSearch(clear) {
  var input = document.getElementById('kbQuery');
  var query = clear ? '' : String((input && input.value) || '').trim();
  if (query.length < 2) {
    kbMode = 'files';
    kbHits = [];
    kbQueryText = '';
    kbRenderList();
    return;
  }
  kbQueryText = query;
  var box = document.getElementById('kbList');
  if (box) box.innerHTML = '<div class="kbEmpty">Ищу…</div>';
  try {
    var res = await fetch('/api/rag/search?q=' + encodeURIComponent(query) + '&limit=40');
    var data = await res.json();
    kbHits = Array.isArray(data.items) ? data.items : [];
  } catch (error) {
    kbHits = [];
  }
  kbMode = 'search';
  kbSelected = null;
  try {
    kbRenderList();
    kbRenderPreview();
  } catch (error) {
    var fallback = document.getElementById('kbList');
    if (fallback) fallback.textContent = String(error && error.message || error);
  }
}

async function kbAdd() {
  var input = document.getElementById('kbPath');
  var status = document.getElementById('kbStatus');
  var button = document.getElementById('kbAdd');
  var path = String((input && input.value) || '').trim();
  if (!path) { toast('Путь не указан'); return; }
  if (status) status.textContent = 'Индексирую…';
  if (button) button.disabled = true;
  try {
    var res = await postJson('/api/rag/add', { path: path });
    if (res && res.started) {
      if (status) status.textContent = 'Индексирую… это может занять минуту';
      res = await waitJob('rag_add');
    }
    if (res && res.ok) {
      if (status) status.textContent = 'Готово: файлов ' + (res.files || 0) + ', фрагментов ' + (res.chunks || 0);
      if (input) input.value = '';
      await loadKB();
    } else if (status) {
      status.textContent = (res && res.error) || 'Не получилось';
    }
  } finally {
    if (button) button.disabled = false;
  }
}

document.querySelectorAll('.tab').forEach(function (button) {
  button.addEventListener('click', function () {
    document.querySelectorAll('.tab').forEach(function (b) { b.classList.remove('active'); });
    document.querySelectorAll('.view').forEach(function (v) { v.classList.remove('active'); });
    button.classList.add('active');
    const target = document.getElementById('view-' + button.dataset.tab);
    target.classList.add('active');
    target.scrollTop = 0;
    if (button.dataset.tab === 'kb') loadKB();
    if (button.dataset.tab === 'tasks') loadTasks();
    if (button.dataset.tab === 'settings') { loadLan(); loadTelegram(); }
  });
});

document.getElementById('kbAdd') && document.getElementById('kbAdd').addEventListener('click', kbAdd);
document.getElementById('kbFilter') && document.getElementById('kbFilter').addEventListener('input', function (event) {
  kbFilterText = event.target.value || '';
  if (kbMode === 'files') kbRenderList();
});
document.getElementById('kbQuery') && document.getElementById('kbQuery').addEventListener('keydown', function (event) {
  if (event.key === 'Enter') { event.preventDefault(); kbSearch(); }
  if (event.key === 'Escape') { event.target.value = ''; kbSearch(true); }
});
document.getElementById('autostart') && document.getElementById('autostart').addEventListener('change', function (event) { toggleAutostart(event.target.checked); });
loadAutostart();
document.querySelectorAll('.chip').forEach(function (chip) {
  chip.addEventListener('click', function () { useTemplate(chip.dataset.template); });
});
document.getElementById('micBtn').addEventListener('click', toggleMic);
document.getElementById('stopBtn').addEventListener('click', cancelGeneration);
window.addEventListener('keydown', function (event) {
  if (event.ctrlKey && event.altKey && event.code === 'Space') {
    event.preventDefault();
    toggleMic();
  }
});
document.getElementById('send').addEventListener('click', send);
document.getElementById('discoverBtn').addEventListener('click', discoverModels);
document.getElementById('saveRoles').addEventListener('click', saveRoles);
document.getElementById('helpBtn').addEventListener('click', function () {
  document.getElementById('helpModal').classList.add('show');
});
document.getElementById('helpClose').addEventListener('click', function () {
  document.getElementById('helpModal').classList.remove('show');
});
document.getElementById('helpModal').addEventListener('click', function (event) {
  if (event.target.id === 'helpModal') event.target.classList.remove('show');
});
document.querySelectorAll('.mode').forEach(function (button) {
  button.addEventListener('click', async function (event) {
    if (event.target.classList.contains('help')) return;
    const mode = button.dataset.mode;
    applyMode(mode === 'team');
    await fetch('/api/team', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: mode === 'team' }) });
    toast(mode === 'team' ? 'Режим: команда специалистов' : 'Режим: один помощник');
    await loadState();
  });
});
document.getElementById('saveSettings').addEventListener('click', saveSettings);
const inputBox = document.getElementById('input');
inputBox.addEventListener('keydown', function (event) {
  if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); send(); }
});
inputBox.addEventListener('input', function () {
  inputBox.style.height = 'auto';
  inputBox.style.height = Math.min(inputBox.scrollHeight, 200) + 'px';
});

const HF = { q: '', items: [], repo: '', files: [] };

function modelsTab(name) {
  document.querySelectorAll('.subtab').forEach(function (b) {
    b.classList.toggle('active', b.dataset.models === name);
  });
  const local = document.getElementById('modelsLocal');
  const online = document.getElementById('modelsOnline');
  if (local) local.classList.toggle('active', name === 'local');
  if (online) online.classList.toggle('active', name === 'online');
  try { localStorage.setItem('agentModelsTab', name); } catch (error) { return; }
}

function fmtCount(value) {
  const n = Number(value) || 0;
  if (n >= 1000000) return (Math.round(n / 100000) / 10) + ' млн';
  if (n >= 1000) return (Math.round(n / 100) / 10) + ' тыс.';
  return String(n);
}

async function hfSearch(query) {
  const field = document.getElementById('hfQuery');
  const q = String(query || (field ? field.value : '') || '').trim();
  const box = document.getElementById('hfResults');
  const hint = document.getElementById('hfHint');
  if (!q) { toast('Введите название модели'); return; }
  if (field) field.value = q;
  HF.q = q;
  if (box) box.innerHTML = '<div class="sub">Ищу на Hugging Face…</div>';
  try {
    const data = await api('/api/hf/search?q=' + encodeURIComponent(q));
    HF.items = data.items || [];
    if (hint) hint.textContent = data.ok ? ('Найдено моделей: ' + HF.items.length) : ('Не получилось: ' + (data.error || 'ошибка'));
    renderHfResults();
  } catch (err) {
    if (box) box.innerHTML = '';
    if (hint) hint.textContent = 'Нет связи с Hugging Face: ' + err.message;
  }
}

function renderHfResults() {
  const box = document.getElementById('hfResults');
  if (!box) return;
  if (!HF.items.length) { box.innerHTML = '<div class="sub">Ничего не нашлось. Попробуйте другое слово.</div>'; return; }
  box.innerHTML = HF.items.map(function (it) {
    const tags = (it.tags || []).slice(0, 5).join(', ');
    return '<div class="card"><div class="title">' + esc(it.repo) + '</div>' +
      '<div class="meta">' + fmtCount(it.downloads) + ' скачиваний, ' + (it.likes || 0) + ' отметок нравится' + (it.license ? ', лицензия ' + esc(it.license) : '') + '</div>' +
      '<div class="meta">' + esc(tags) + '</div>' +
      '<div class="meta">обновлено ' + esc(it.updated || '') + '</div>' +
      '<div class="row2"><button class="ghost" data-hfrepo="' + esc(it.repo) + '">Показать файлы</button></div></div>';
  }).join('');
}

function hfSize(f) {
  const bytes = Number(f.bytes) || 0;
  if (bytes && bytes < 100 * 1024 * 1024) return Math.max(1, Math.round(bytes / 1048576)) + ' МБ';
  return gb(f.size_gb);
}

async function hfFiles(repo) {
  HF.repo = repo;
  HF.files = [];
  const panel = document.getElementById('hfFilesPanel');
  const list = document.getElementById('hfFiles');
  const title = document.getElementById('hfFilesTitle');
  if (!panel || !list) return;
  panel.style.display = 'block';
  if (title) title.textContent = repo;
  list.innerHTML = '<div class="sub">Смотрю файлы…</div>';
  try {
    const data = await api('/api/hf/files?repo=' + encodeURIComponent(repo));
    if (!data.ok) { list.innerHTML = '<div class="sub">' + esc(data.error || 'ошибка') + '</div>'; return; }
    HF.files = data.files || [];
    if (!HF.files.length) { list.innerHTML = '<div class="sub">В этой модели нет .gguf-файлов.</div>'; return; }
    list.innerHTML = '<table class="roles"><thead><tr><th>Файл</th><th>Размер</th><th>Квант</th><th></th></tr></thead><tbody>' +
      HF.files.map(function (f) {
        return '<tr><td>' + esc(f.file) + '</td><td>' + hfSize(f) + '</td><td>' + esc(f.quant || '') + '</td>' +
          '<td><button class="ghost tiny" data-hfdl="' + esc(f.file) + '">Скачать</button></td></tr>';
      }).join('') + '</tbody></table>';
  } catch (err) {
    list.innerHTML = '<div class="sub">Ошибка: ' + esc(err.message) + '</div>';
  }
}

async function hfDownload(file) {
  if (!HF.repo) { toast('Сначала выберите модель'); return; }
  try {
    const data = await api('/api/hf/download', { repo: HF.repo, file: file });
    if (!data.ok) { toast(data.error || 'ошибка'); return; }
    toast('Качаю ' + file);
    updateHfProgress({ active: true, pct: 0, text: 'Начинаю загрузку' });
    poll();
  } catch (err) {
    toast('Ошибка: ' + err.message);
  }
}

function updateHfProgress(dl) {
  const wrap = document.getElementById('hfProgWrap');
  if (!wrap) return;
  if (!dl) { wrap.style.display = 'none'; return; }
  const text = document.getElementById('hfProgText');
  const bar = document.getElementById('hfProg');
  const started = dl.active || (dl.pct || 0) > 0 || dl.done || dl.error;
  wrap.style.display = started ? 'block' : 'none';
  if (!started) return;
  if (bar && bar.firstElementChild) bar.firstElementChild.style.width = Math.max(2, dl.pct || 0) + '%';
  if (text) text.textContent = dl.error ? ('Ошибка: ' + dl.error) : (dl.text || '');
}

async function saveHfToken() {
  const field = document.getElementById('hfToken');
  const token = field ? field.value.trim() : '';
  if (!token && !((STATE || {}).hf || {}).token_set) { toast('Вставьте токен из настроек Hugging Face'); return; }
  try {
    const data = await api('/api/hf/token', { token: token });
    if (field) field.value = '';
    toast(data.token_set ? 'Токен сохранён' : 'Токен удалён');
    await loadState();
  } catch (err) {
    toast('Ошибка: ' + err.message);
  }
}

function renderHfState() {
  const field = document.getElementById('hfToken');
  if (!field) return;
  const state = (STATE || {}).hf || {};
  field.placeholder = state.token_set ? 'токен сохранён, можно заменить' : 'hf_...';
}

function renderEmbeddings() {
  const info = (STATE || {}).embeddings || {};
  const state = document.getElementById('embState');
  const hint = document.getElementById('embHint');
  const field = document.getElementById('embedModel');
  if (field && document.activeElement !== field) field.value = info.model || '';
  if (state) {
    let text = 'Поиск по смыслу: выключен, ищем по словам';
    if (info.running) text = 'Поиск по смыслу: модель работает';
    else if (info.model) text = 'Поиск по смыслу: файл найден, нажмите «Проверить модель»';
    state.textContent = text;
  }
  if (hint) {
    if (info.dir_ok === false) hint.textContent = 'Папка моделей недоступна: ' + (info.dir || '') + ' Подключите диск или укажите другую папку в Настройках.';
    else hint.textContent = info.model
      ? (info.name + (info.dim ? ' · вектор из ' + info.dim + ' чисел' : ''))
      : 'Модели нет. Скачайте на вкладке «Модели», подойдёт bge-m3 или embeddinggemma.';
  }
}

async function checkEmbeddings() {
  toast('Проверяю модель, первый запуск может занять до минуты');
  try {
    const data = await api('/api/embeddings/check', {});
    toast(data.ok ? ('Работает, вектор из ' + data.dim + ' чисел') : (data.error || 'ошибка'));
    await loadState();
  } catch (err) {
    toast('Ошибка: ' + err.message);
  }
}

async function reindexKB() {
  try {
    const data = await api('/api/rag/reindex', {});
    toast(data.ok ? 'Пересчёт пошёл, это займёт время' : (data.error || 'ошибка'));
    setTimeout(loadKB, 5000);
  } catch (err) {
    toast('Ошибка: ' + err.message);
  }
}

function wireEmbeddings() {
  const check = document.getElementById('embCheck');
  if (check) check.addEventListener('click', checkEmbeddings);
  const again = document.getElementById('kbReindex');
  if (again) again.addEventListener('click', reindexKB);
}

function wireModels() {
  document.querySelectorAll('.subtab').forEach(function (b) {
    b.addEventListener('click', function () { modelsTab(b.dataset.models); });
  });
  const btn = document.getElementById('hfSearchBtn');
  if (btn) btn.addEventListener('click', function () { hfSearch(); });
  const field = document.getElementById('hfQuery');
  if (field) field.addEventListener('keydown', function (event) {
    if (event.key === 'Enter') { event.preventDefault(); hfSearch(); }
  });
  const close = document.getElementById('hfFilesClose');
  if (close) close.addEventListener('click', function () { document.getElementById('hfFilesPanel').style.display = 'none'; });
  const tokenBtn = document.getElementById('hfTokenSave');
  if (tokenBtn) tokenBtn.addEventListener('click', saveHfToken);
  document.addEventListener('click', function (event) {
    const el = event.target;
    if (!el || !el.dataset) return;
    if (el.dataset.hfq) hfSearch(el.dataset.hfq);
    else if (el.dataset.hfrepo) hfFiles(el.dataset.hfrepo);
    else if (el.dataset.hfdl) hfDownload(el.dataset.hfdl);
  });
  let saved = 'local';
  try { saved = localStorage.getItem('agentModelsTab') || 'local'; } catch (error) { saved = 'local'; }
  modelsTab(saved);
}

function bootStep(name, fn) {
  try { fn(); } catch (error) { console.error('boot:' + name, error); }
}
bootStep('resizers', wireResizers);
bootStep('gear', wireGear);
bootStep('autostart', loadAutostart);
bootStep('hf', wireModels);
bootStep('embeddings', wireEmbeddings);
loadState().catch(function (err) { toast('Не удалось получить состояние: ' + err.message); });
pollStats();
setInterval(pollStats, 1500);

// ПОКАЗ ВЕРСИИ в статусной строке
(async function () {
  try {
    const res = await fetch('/api/version');
    const data = await res.json();
    const box = document.getElementById('sbVersion');
    if (box && data.version) box.textContent = 'v' + data.version;
  } catch (error) {
    return;
  }
})();

// remember which sections are open
(function () {
  var KEY = 'agentAccState';
  var saved = {};
  try { saved = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (error) { saved = {}; }
  var boxes = Array.prototype.slice.call(document.querySelectorAll('details.acc'));
  boxes.forEach(function (box, index) {
    var title = box.querySelector('summary');
    var name = title ? title.textContent.trim() : String(index);
    if (Object.prototype.hasOwnProperty.call(saved, name)) box.open = !!saved[name];
    box.addEventListener('toggle', function () {
      saved[name] = box.open;
      try { localStorage.setItem(KEY, JSON.stringify(saved)); } catch (error) { return; }
    });
  });
})();

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/ui/sw.js').catch(function () { return; });
}
