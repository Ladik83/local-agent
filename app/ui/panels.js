/*
  panels.js — нижняя панель мониторинга.
  Наполняет блоки monRes, monModel, modelAdvice, modelTuning, monNow.
  Сам ничего не запускает: точки входа дергает app.js через window.Panels.
  Код ES5: var/function, без стрелок и шаблонных строк.
*/
(function () {
  'use strict';

  var TICK = null;
  var TICK_BASE = 0;
  var TICK_START = 0;

  function byId(id) {
    return document.getElementById(id);
  }

  function isObject(value) {
    return !!value && typeof value === 'object';
  }

  function esc(value) {
    return String(value === null || value === undefined ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function num(value, fallback) {
    var parsed = Number(value);
    if (isFinite(parsed)) return parsed;
    if (typeof value === 'string') {
      parsed = parseFloat(value.replace(',', '.').replace(/[^0-9.\-]/g, ''));
      if (isFinite(parsed)) return parsed;
    }
    return fallback;
  }

  function clamp(value) {
    if (value === null || value === undefined) return null;
    return Math.max(0, Math.min(100, value));
  }

  function text(value, fallback) {
    if (value === null || value === undefined || value === '') return fallback;
    return String(value);
  }

  function errorText(err) {
    if (err && err.message) return String(err.message);
    return String(err === undefined || err === null ? 'не удалось' : err);
  }

  function gbText(value) {
    var amount = num(value, null);
    if (amount === null) return '—';
    if (typeof window.gb === 'function') {
      try { return String(window.gb(amount)); } catch (err) { /* ниже запасной вариант */ }
    }
    return (Math.round(amount * 10) / 10) + ' ГБ';
  }

  function say(message) {
    if (typeof window.toast === 'function') {
      try { window.toast(message); return; } catch (err) { /* молча идем дальше */ }
    }
    if (typeof console !== 'undefined' && console.log) console.log(message);
  }

  function state() {
    try {
      if (typeof STATE !== 'undefined' && STATE) return STATE;
    } catch (err) { /* STATE ещё не готов */ }
    return {};
  }

  function stats() {
    try {
      if (typeof STATS !== 'undefined' && STATS) return STATS;
    } catch (err) { /* ещё не готов */ }
    return {};
  }

  function merge(base, extra) {
    var result = {};
    var key;
    if (isObject(base)) {
      for (key in base) {
        if (Object.prototype.hasOwnProperty.call(base, key)) result[key] = base[key];
      }
    }
    if (isObject(extra)) {
      for (key in extra) {
        if (Object.prototype.hasOwnProperty.call(extra, key)) result[key] = extra[key];
      }
    }
    return result;
  }

  function row(label, value) {
    return '<div class="metric"><span>' + esc(label) + '</span><b>' + esc(value) + '</b></div>';
  }

  function rowHtml(label, valueHtml) {
    return '<div class="metric"><span>' + esc(label) + '</span><b>' + valueHtml + '</b></div>';
  }

  function barHtml(percent, cls) {
    var value = clamp(num(percent, 0));
    return '<div class="meter"><i class="' + esc(cls || '') + '" style="width:' + value + '%"></i></div>';
  }

  function placeholder(box, message) {
    box.innerHTML = '<div class="sub">' + esc(message) + '</div>';
  }

  function send(url, body) {
    var payload = isObject(body) ? body : {};
    if (typeof window.postJson === 'function') {
      try { return Promise.resolve(window.postJson(url, payload)); } catch (err) { return Promise.reject(err); }
    }
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    }).then(function (resp) {
      return resp.json();
    });
  }

  function fetchPanels() {
    return fetch('/api/panels', { headers: { 'Accept': 'application/json' } }).then(function (resp) {
      if (!resp || !resp.ok) throw new Error('код ' + (resp ? resp.status : 0));
      return resp.json();
    });
  }

  function refresh() {
    try {
      if (typeof window.loadState === 'function') {
        var pending = window.loadState();
        if (pending && typeof pending.then === 'function') {
          pending.then(function () { renderAll(); }, function () { renderAll(); });
          return;
        }
      }
    } catch (err) { /* состояние обновится следующим опросом */ }
    renderAll();
  }

  /* ---------- ресурсы ---------- */

  function cpuPercent(data) {
    var raw = data.cpu;
    if (typeof raw === 'number') return clamp(raw);
    if (isObject(raw)) {
      var inner = num(raw.percent, num(raw.load, num(raw.usage, null)));
      if (inner !== null) return clamp(inner);
    }
    var direct = num(data.cpu_percent, num(data.cpu_load, num(data.load, null)));
    if (direct !== null) return clamp(direct);
    var snapshot = stats();
    if (typeof snapshot.cpu === 'number') return clamp(snapshot.cpu);
    return null;
  }

  function memoryInfo(data) {
    var raw = isObject(data.memory) ? data.memory : (isObject(data.ram) ? data.ram : null);
    var used = null;
    var total = null;
    var percent = null;
    var free = null;
    if (raw) {
      used = num(raw.used, num(raw.used_gb, null));
      total = num(raw.total, num(raw.total_gb, null));
      percent = num(raw.percent, null);
      free = num(raw.free, num(raw.free_gb, null));
    }
    if (used === null) used = num(data.ram_used_gb, null);
    if (total === null) total = num(data.ram_total_gb, null);
    if (free === null) free = num(data.ram_free_gb, num(data.mem_free_gb, null));
    if (!raw && percent === null) percent = num(data.mem_percent, null);
    if (percent === null) percent = num(data.ram_percent, null);
    if (used === null && free !== null && total !== null) used = total - free;
    if (percent === null && used !== null && total) percent = used * 100 / total;
    if (percent === null && free !== null && total) percent = (total - free) * 100 / total;
    if (used === null && percent === null) {
      var mem = isObject(stats().memory) ? stats().memory : null;
      if (mem) {
        used = num(mem.used, null);
        total = num(mem.total, null);
        percent = num(mem.percent, null);
        free = num(mem.free, null);
      }
    }
    if (used === null && percent === null) return null;
    return { used: used, total: total, percent: clamp(percent), free: free };
  }

  function diskInfo(data) {
    var raw = isObject(data.disk) ? data.disk : null;
    var free = null;
    var total = null;
    var percent = null;
    if (raw) {
      free = num(raw.free, num(raw.free_gb, null));
      total = num(raw.total, num(raw.total_gb, null));
      percent = num(raw.percent, null);
      if (percent === null && raw.used !== undefined && total) percent = num(raw.used, 0) * 100 / total;
    }
    if (free === null) free = num(data.disk_free, num(data.disk_free_gb, null));
    if (percent === null && free !== null && total) percent = (total - free) * 100 / total;
    if (free === null && percent === null) {
      var snapshot = stats();
      free = num(snapshot.disk_free, null);
    }
    if (free === null && percent === null) return null;
    return { free: free, total: total, percent: clamp(percent) };
  }

  function renderResources(d) {
    var box = byId('monRes');
    if (!box) return;
    try {
      var data = isObject(d) ? d : {};
      var cpu = cpuPercent(data);
      var mem = memoryInfo(data);
      var disk = diskInfo(data);
      if (cpu === null && !mem && !disk) {
        placeholder(box, 'нет данных');
        return;
      }
      var html = '';
      if (cpu !== null) {
        html += row('Процессор', Math.round(cpu) + '%');
        html += barHtml(cpu, cpu >= 90 ? 'err' : (cpu >= 70 ? 'warn' : ''));
      } else {
        html += row('Процессор', 'нет данных');
      }
      if (mem) {
        var memLabel = '';
        if (mem.used !== null && mem.total !== null) memLabel = (Math.round(mem.used * 10) / 10) + ' из ' + gbText(mem.total);
        else if (mem.percent !== null) memLabel = Math.round(mem.percent) + '%';
        else memLabel = gbText(mem.used);
        html += row('Память', memLabel);
        if (mem.percent !== null) {
          html += barHtml(mem.percent, mem.percent >= 88 ? 'err' : (mem.percent >= 70 ? 'warn' : ''));
        }
      } else {
        html += row('Память', 'нет данных');
      }
      if (disk) {
        html += row('Диск, свободно', gbText(disk.free));
        if (disk.percent !== null) {
          var freeShare = clamp(100 - disk.percent);
          html += barHtml(freeShare, freeShare <= 8 ? 'err' : (freeShare <= 20 ? 'warn' : ''));
        }
      } else {
        html += row('Диск свободно', 'нет данных');
      }
      if (window.patchBox) window.patchBox(box, html); else box.innerHTML = html;
    } catch (err) {
      placeholder(box, 'нет данных');
    }
  }

  /* ---------- модель ---------- */

  function modelPath(item) {
    if (!item) return '';
    return text(item.id, text(item.path, text(item.file, '')));
  }

  function modelName(item) {
    if (!item) return '';
    var value = text(item.file, text(item.name, text(item.id, '')));
    return value;
  }

  function modelLoaded(data, engine) {
    if (data) {
      if (data.loaded !== undefined) return !!data.loaded;
      if (data.running !== undefined) return !!data.running;
      if (data.status !== undefined && data.status !== null && data.status !== '') {
        var status = String(data.status).toLowerCase();
        if (status.indexOf('не ') === 0 || status.indexOf('not') >= 0 || status.indexOf('выгруж') >= 0) return false;
        if (status.indexOf('загруж') >= 0 || status.indexOf('loaded') >= 0 || status.indexOf('running') >= 0) return true;
      }
    }
    return !!(engine && engine.running);
  }

  function recommendedPath() {
    var snapshot = state();
    var pick = isObject(snapshot.pick) ? snapshot.pick : {};
    if (isObject(pick.model)) {
      var picked = modelPath(pick.model);
      if (picked) return picked;
    }
    var models = isObject(snapshot.models) ? snapshot.models : [];
    var hardware = isObject(snapshot.hardware) ? snapshot.hardware : {};
    var free = num(hardware.ram_free_gb, 0);
    var limit = free > 0 ? free * 0.6 : 0;
    var best = null;
    for (var i = 0; i < models.length; i++) {
      var size = num(models[i].size_gb, 0);
      if (!size) continue;
      if (limit && size > limit) continue;
      if (!best || size > num(best.size_gb, 0)) best = models[i];
    }
    if (best) return modelPath(best);
    var settings = isObject(snapshot.settings) ? snapshot.settings : {};
    return text(settings.last_model, '');
  }

  function loadModel(path) {
    var target = text(path, '');
    if (!target) {
      say('Сначала выберите модель');
      return;
    }
    say('Загружаю модель…');
    send('/api/model/load', { path: target }).then(function (res) {
      if (res && res.ok === false) {
        say('Не вышло: ' + text(res.error, 'ошибка'));
        return;
      }
      say('Модель загружена');
      refresh();
    }).catch(function (err) {
      say('Не вышло: ' + errorText(err));
    });
  }

  function unloadModel() {
    say('Выгружаю модель…');
    send('/api/model/unload', {}).then(function (res) {
      if (res && res.ok !== false) {
        say('Модель выгружена');
        refresh();
        return null;
      }
      return send('/api/model/stop', {}).then(function (alt) {
        if (alt && alt.ok === false) {
          say('Не вышло: ' + text(alt.error, 'ошибка'));
          return;
        }
        say('Модель выгружена');
        refresh();
      });
    }).catch(function () {
      send('/api/model/stop', {}).then(function () {
        say('Модель выгружена');
        refresh();
      }, function (err) {
        say('Не вышло: ' + errorText(err));
      });
    });
  }

  function renderModel(d) {
      if (!isObject(d) || !d.name) {
        var st = state() || {};
        var eng = st.engine || {};
        var last = String((st.settings || {}).last_model || '');
        var sep = String.fromCharCode(92);
        var shortName = last ? last.split(sep).pop().split('/').pop() : '';
        d = { name: shortName, path: last, loaded: !!eng.running, port: eng.port || 0,
              ctx: (st.settings || {}).context || 8192, size_gb: 0 };
      }
    var box = byId('monModel');
    if (!box) return;
    try {
      var data = isObject(d) ? d : {};
      var snapshot = state();
      var engine = isObject(snapshot.engine) ? snapshot.engine : {};
      var settings = isObject(snapshot.settings) ? snapshot.settings : {};
      var name = text(data.name, text(data.model, text(data.file, text(engine.model, ''))));
      var loaded = modelLoaded(data, engine);
      var sizeGb = num(data.size_gb, num(data.size, null));
      var port = text(data.port, text(engine.port, ''));
      var context = num(data.context, num(data.ctx, num(settings.context, null)));
      var html = '';
      html += row('Размер', name || sizeGb !== null ? gbText(sizeGb) : 'нет данных');
      html += row('Контекст', context !== null && context > 0 ? (context + ' токенов') : '—');
      html += rowHtml('Статус', '<span style="color:' + (loaded ? 'var(--ok)' : 'var(--muted)') + '">' +
        esc(loaded ? 'загружена' : 'не загружена') + '</span>');
      var offload = (engine.offload) || {};
      if (loaded) {
        var where = offload.on_gpu
          ? text(offload.gpu, 'видеокарта') + (offload.layers ? ' · ' + offload.layers : '')
          : 'процессор';
        html += row('Считает', esc(where));
      }
      if (window.patchBox) window.patchBox(box, html); else box.innerHTML = html;
      var loadBtn = box.querySelector('[data-panels-act="load"]');
      if (loadBtn) loadBtn.onclick = function () { loadModel(recommendedPath()); };
      var unloadBtn = box.querySelector('[data-panels-act="unload"]');
      if (unloadBtn) unloadBtn.onclick = function () { unloadModel(); };
    } catch (err) {
      placeholder(box, 'нет данных');
    }
  }

  /* ---------- рекомендация ---------- */

  function adviceLine(title, item, note) {
    if (!item) return '<div class="advice"><b>' + esc(title) + '</b>: нет подходящей</div>';
    var size = num(item.size_gb, null);
    return '<div class="advice"><b>' + esc(title) + '</b> · ' + esc(gbText(size)) +
      '<div class="sub" style="margin:2px 0 4px">' + esc(modelName(item)) + '</div>' +
      '<div class="sub" style="margin:0 0 6px">' + esc(note) + '</div>' +
      '<button class="ghost tiny" data-panels-act="advice" data-path="' + esc(modelPath(item)) + '">Загрузить</button></div>';
  }

  function bindAdvice(box) {
    var buttons = box.querySelectorAll('[data-panels-act="advice"]');
    for (var i = 0; i < buttons.length; i++) {
      (function (button) {
        button.onclick = function () { loadModel(button.getAttribute('data-path')); };
      })(buttons[i]);
    }
  }

  function renderAdvice() {
    var box = byId('modelAdvice');
    if (!box) return;
    try {
      var snapshot = state();
      var models = isObject(snapshot.models) ? snapshot.models : [];
      var hardware = isObject(snapshot.hardware) ? snapshot.hardware : {};
      var free = num(hardware.ram_free_gb, 0);
      if (!models.length) {
        placeholder(box, 'нет данных о моделях');
        return;
      }
      var limit = free > 0 ? free * 0.6 : 0;
      var fast = null;
      var strong = null;
      for (var i = 0; i < models.length; i++) {
        var size = num(models[i].size_gb, 0);
        if (!size) continue;
        if (!limit || size <= limit) {
          if (!strong || size > num(strong.size_gb, 0)) strong = models[i];
        }
        if (size <= 5.5 && (!fast || size > num(fast.size_gb, 0))) fast = models[i];
      }
      if (!strong && !fast) {
        placeholder(box, 'нет модели под свободную память (' + gbText(free) + ')');
        return;
      }
      if (fast && strong && modelPath(fast) === modelPath(strong)) fast = null;
      var noteStrong = limit ? 'до 60% свободной памяти, медленнее, но толковее' : 'самая крупная из найденных';
      var html = adviceLine('Быстро', fast, 'до 5,5 ГБ — отвечает резво') +
        adviceLine('Качественно', strong, noteStrong) +
        '<div class="sub">свободно ' + esc(gbText(free)) + '</div>';
      if (window.patchBox) window.patchBox(box, html); else box.innerHTML = html;
      bindAdvice(box);
    } catch (err) {
      placeholder(box, 'нет данных');
    }
  }

  /* ---------- настройки модели ---------- */

  var TEMP_VALUES = [0.2, 0.5, 0.8, 1.0];
  var CTX_VALUES = [4096, 8192, 16384, 32768];

  function nearest(values, target) {
    var best = values[0];
    var bestDiff = Math.abs(values[0] - target);
    for (var i = 1; i < values.length; i++) {
      var diff = Math.abs(values[i] - target);
      if (diff < bestDiff) {
        best = values[i];
        bestDiff = diff;
      }
    }
    return best;
  }

  function tempLabel(value) {
    if (value === 0.2) return '0.2 · точная';
    if (value === 0.5) return '0.5 · спокойная';
    if (value === 0.8) return '0.8 · живая';
    return '1.0 · смелая';
  }

  function ctxLabel(value) {
    if (value === 4096) return '4 096 токенов';
    if (value === 8192) return '8 192 токенов';
    if (value === 16384) return '16 384 токенов';
    return '32 768 токенов';
  }

  function saveSetting(key, value, label) {
    var patch = {};
    patch[key] = value;
    send('/api/settings', patch).then(function (res) {
      if (res && res.ok === false) {
        say('Не вышло: ' + text(res.error, 'ошибка'));
        return;
      }
      var settings = state().settings;
      if (isObject(settings)) settings[key] = value;
      say(label + ' — сохранено');
    }).catch(function (err) {
      say('Не вышло: ' + errorText(err));
    });
  }

  function renderTuning(d) {
    var box = byId('modelTuning');
    if (!box) return;
    try {
      var override = isObject(d) ? d : {};
      var settings = isObject(state().settings) ? state().settings : {};
      var temp = num(override.temperature, num(settings.temperature, 0.5));
      var context = num(override.context, num(settings.context, 8192));
      var selectedTemp = nearest(TEMP_VALUES, temp);
      var selectedCtx = nearest(CTX_VALUES, context);
      var html = '<div class="tuneRow"><span>Температура</span><select id="panelsTemp">';
      for (var i = 0; i < TEMP_VALUES.length; i++) {
        html += '<option value="' + TEMP_VALUES[i] + '"' +
          (TEMP_VALUES[i] === selectedTemp ? ' selected' : '') + '>' + esc(tempLabel(TEMP_VALUES[i])) + '</option>';
      }
      html += '</select></div>';
      html += '<div class="tuneRow"><span>Контекст</span><select id="panelsCtx">';
      for (var j = 0; j < CTX_VALUES.length; j++) {
        html += '<option value="' + CTX_VALUES[j] + '"' +
          (CTX_VALUES[j] === selectedCtx ? ' selected' : '') + '>' + esc(ctxLabel(CTX_VALUES[j])) + '</option>';
      }
      html += '</select></div>';
      var gpuMode = text(settings.gpu, 'auto');
      var gpuDevices = (state().engine || {}).devices || [];
      html += '<div class="tuneRow"><span title="Видеокарта считает промпт в разы быстрее. Процессор — если видеокарта занята или её нет.">Считать на</span><select id="panelsGpu">' +
        '<option value="auto"' + (gpuMode === 'auto' ? ' selected' : '') + '>видеокарта, если есть</option>' +
        '<option value="gpu"' + (gpuMode === 'gpu' ? ' selected' : '') + '>только видеокарта</option>' +
        '<option value="cpu"' + (gpuMode === 'cpu' ? ' selected' : '') + '>процессор</option></select></div>';
      if (!gpuDevices.length) {
        html += '<div class="advice">Движок без Vulkan — модель пойдёт в память процессора.</div>';
      }
      html += '<div class="tuneRow"><span>Своё значение</span><input type="number" id="panelsCtxManual" min="512" max="262144" step="512" value="' + context + '"></div>';
      html += '<div class="tuneRow"><span></span><button class="ghost tiny" id="panelsCtxApply">Применить</button></div>';
      if (window.patchBox) window.patchBox(box, html); else box.innerHTML = html;
      var tempSelect = byId('panelsTemp');
      var ctxSelect = byId('panelsCtx');
      var manualCtx = byId('panelsCtxManual');
      var gpuSelect = byId('panelsGpu');
      if (gpuSelect) {
        gpuSelect.onchange = function () {
          var value = gpuSelect.value;
          var label = value === 'cpu' ? 'процессор' : (value === 'gpu' ? 'только видеокарта' : 'видеокарта, если есть');
          saveSetting('gpu', value, 'Считать на: ' + label);
        };
      }
      var applyCtx = byId('panelsCtxApply');
      if (applyCtx && manualCtx) {
        applyCtx.onclick = function () {
          var value = Math.round(Number(manualCtx.value) || 0);
          if (!value || value < 512 || value > 262144) {
            toast('Контекст: от 512 до 262144 токенов');
            return;
          }
          manualCtx.value = value;
          saveSetting('context', value, 'Контекст ' + value + ' токенов');
        };
      }
      if (tempSelect) {
        tempSelect.onchange = function () {
          var value = Number(tempSelect.value);
          saveSetting('temperature', value, 'Температура ' + tempLabel(value));
        };
      }
      if (ctxSelect) {
        ctxSelect.onchange = function () {
          var value = Number(ctxSelect.value);
          saveSetting('context', value, 'Контекст ' + ctxLabel(value));
        };
      }
    } catch (err) {
      placeholder(box, 'нет данных');
    }
  }

  /* ---------- что происходит сейчас ---------- */

  function elapsedSeconds(data) {
    var direct = num(data.seconds, num(data.elapsed, num(data.elapsed_seconds, null)));
    if (direct !== null) return Math.max(0, Math.round(direct));
    var since = num(data.since, num(data.started, num(data.started_at, num(data.ts, null))));
    if (since === null || since <= 0) return null;
    if (since > 100000000000) since = since / 1000;
    return Math.max(0, Math.round((Date.now() / 1000) - since));
  }

  function isError(data, phase) {
    if (data.error !== undefined && data.error !== null && data.error !== false && data.error !== '') return true;
    if (data.ok === false) return true;
    var probe = (String(phase) + ' ' + String(data.state || '')).toLowerCase();
    return probe.indexOf('ошиб') >= 0 || probe.indexOf('сбой') >= 0 ||
      probe.indexOf('error') >= 0 || probe.indexOf('failed') >= 0;
  }

  function isBusy(data, phase) {
    if (data.busy === true || data.running === true || data.working === true) return true;
    var probe = String(phase || '').toLowerCase();
    if (!probe) return false;
    if (probe.indexOf('ожидан') >= 0 || probe.indexOf('готов') >= 0 ||
      probe.indexOf('простой') >= 0 || probe.indexOf('idle') >= 0 || probe.indexOf('покой') >= 0) return false;
    return true;
  }

  function stopTick() {
    if (TICK) {
      clearInterval(TICK);
      TICK = null;
    }
  }

  function startTick(baseSeconds) {
    stopTick();
    TICK_BASE = baseSeconds;
    TICK_START = Date.now();
    TICK = setInterval(function () {
      var target = byId('panelsNowSecs');
      if (!target) {
        stopTick();
        return;
      }
      target.textContent = (TICK_BASE + Math.round((Date.now() - TICK_START) / 1000)) + ' с';
    }, 1000);
  }

  function renderNow(d) {
    var box = byId('monNow');
    if (!box) return;
    try {
      var data = isObject(d) ? d : {};
      if (!data.phase && !data.state && isObject(stats().activity)) {
        data = merge(stats().activity, data);
      }
      if (isObject(data.activity)) data = merge(data.activity, data);
      var known = ['phase', 'state', 'tool', 'role', 'step', 'since', 'started',
        'seconds', 'elapsed', 'error', 'busy', 'running'];
      var hasData = false;
      for (var k = 0; k < known.length; k++) {
        var probeValue = data[known[k]];
        if (probeValue !== undefined && probeValue !== null && probeValue !== '') {
          hasData = true;
          break;
        }
      }
      if (!hasData) {
        stopTick();
        placeholder(box, 'нет данных');
        return;
      }
      var phase = text(data.phase, text(data.state, 'ожидание'));
      var tool = text(data.tool, '');
      var role = text(data.role, '');
      var step = text(data.step, '');
      var seconds = elapsedSeconds(data);
      var bad = isError(data, phase);
      var busy = !bad && isBusy(data, phase);
      var color = bad ? 'var(--err)' : (busy ? 'var(--ok)' : 'var(--muted)');
      var html = rowHtml('Сейчас', '<span class="dot" style="background:' + color +
        ';display:inline-block;margin-right:6px"></span>' + esc(phase));
      if (role) html += row('Роль', role);
      if (tool) html += rowHtml('Инструмент', '<span class="roleChip">' + esc(tool) + '</span>');
      if (step) html += row('Шаг', step);
      var duration = (busy || bad) && seconds !== null
        ? '<span id="panelsNowSecs">' + seconds + ' с</span>'
        : '—';
      html += rowHtml('Идёт', duration);
      if (window.patchBox) window.patchBox(box, html); else box.innerHTML = html;
      if (busy && seconds !== null) startTick(seconds);
      else stopTick();
    } catch (err) {
      stopTick();
      placeholder(box, 'нет данных');
    }
  }

  /* ---------- сборка ---------- */

  function safePlaceholders() {
    var ids = ['monRes', 'monModel', 'modelAdvice', 'modelTuning', 'monNow'];
    for (var i = 0; i < ids.length; i++) {
      try {
        var box = byId(ids[i]);
        if (box) placeholder(box, 'нет данных');
      } catch (err) { /* блок мог исчезнуть */ }
    }
  }

  function apply(payload) {
    var data = isObject(payload) ? payload : {};
    var computer = isObject(data.computer) ? data.computer : {};
    var resources = isObject(data.resources) ? merge(computer, data.resources) : computer;
    renderResources(resources);
    renderModel(isObject(data.model) ? data.model : {});
    renderAdvice();
    renderTuning(isObject(data.settings) ? data.settings : null);
    renderNow(isObject(data.now) ? data.now : {});
  }

  function renderAll(payload) {
    if (isObject(payload)) {
      try { apply(payload); } catch (err) { safePlaceholders(); }
      return Promise.resolve(payload);
    }
    return fetchPanels().then(function (data) {
      try { apply(data); } catch (err) { safePlaceholders(); }
      return data;
    }).catch(function (err) {
      try { apply({}); } catch (err2) { safePlaceholders(); }
      if (typeof console !== 'undefined' && console.warn) console.warn('Панели: ' + errorText(err));
      return null;
    });
  }

  window.Panels = {
    renderAll: renderAll,
    renderResources: renderResources,
    renderModel: renderModel,
    renderTuning: renderTuning,
    renderNow: renderNow,
    renderAdvice: renderAdvice
  };
})();
