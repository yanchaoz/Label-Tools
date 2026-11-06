'use strict';

const $ = id => document.getElementById(id);
const clone = value => JSON.parse(JSON.stringify(value));
const palette = ['#58e8a2', '#ffbe55', '#55c9ff', '#f18fb8', '#e2df64', '#bdadff'];
const canvas = $('canvas');
const context = canvas.getContext('2d');
const state = {
  dataset: null, record: null, doc: null, image: null, index: 0, mode: 'draw',
  selected: null, vertex: null, path: [], mouse: null, scale: 1, tx: 0, ty: 0,
  width: 1, height: 1, dirty: false, version: 0, saving: null, timer: null,
  undo: [], redo: [], drag: null, space: false, overlay: true, loading: false,
  statusKey: 'loading',
};

function notice(message = '') {
  $('notice').textContent = message;
  $('notice').hidden = !message;
}

function status(key, error = false) {
  state.statusKey = key;
  $('save-state').textContent = t(key);
  $('save-state').classList.toggle('error', error);
}

function showError(error) {
  status('error', true);
  notice(error.message || String(error));
}

async function api(url, payload) {
  const response = await fetch(url, payload === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Annotation-Token': state.dataset.token },
    body: JSON.stringify(payload),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || response.statusText);
  return result;
}

function confirmAction(message) {
  if (document.querySelector('dialog[open]')) return Promise.resolve(false);
  return new Promise(resolve => {
    const dialog = document.createElement('dialog');
    dialog.className = 'confirm-dialog';
    dialog.setAttribute('aria-label', t('confirmTitle'));
    const paragraph = document.createElement('p');
    paragraph.textContent = message;
    const actions = document.createElement('div');
    actions.className = 'dialog-actions';
    const cancel = document.createElement('button');
    cancel.className = 'text-button';
    cancel.textContent = t('cancel');
    const ok = document.createElement('button');
    ok.className = 'text-button primary';
    ok.textContent = t('confirm');
    const finish = result => { dialog.close(); dialog.remove(); resolve(result); };
    cancel.onclick = () => finish(false);
    ok.onclick = () => finish(true);
    dialog.oncancel = event => { event.preventDefault(); finish(false); };
    actions.append(cancel, ok);
    dialog.append(paragraph, actions);
    document.body.append(dialog);
    dialog.showModal();
    cancel.focus();
  });
}

function draftKey(record = state.record) {
  return `label-tools:${state.dataset.workspace_id}:${record.id}`;
}

function stash() {
  if (!state.record || !state.doc) return;
  try {
    if (state.dirty || state.path.length) {
      localStorage.setItem(draftKey(), JSON.stringify({ doc: state.doc, path: state.path, dirty: state.dirty }));
    } else localStorage.removeItem(draftKey());
  } catch (_) { /* Disk autosave remains authoritative when browser storage is unavailable. */ }
}

function snapshot() {
  return clone({ polygons: state.doc.polygons, status: state.doc.status, notes: state.doc.notes });
}

function beginChange() {
  state.undo.push(snapshot());
  if (state.undo.length > 80) state.undo.shift();
  state.redo = [];
}

function changed(reopen = true) {
  if (reopen) state.doc.status = 'draft';
  state.dirty = true;
  state.version += 1;
  status('unsaved');
  stash();
  clearTimeout(state.timer);
  state.timer = setTimeout(() => flush().catch(showError), 700);
  updatePanels();
  draw();
}

async function flush() {
  clearTimeout(state.timer);
  if (state.saving) await state.saving;
  if (!state.dirty || state.drag?.kind === 'vertex') return;
  state.saving = (async () => {
    while (state.dirty && state.drag?.kind !== 'vertex') {
      const version = state.version;
      const saved = await api(`/api/case/${state.record.id}`, clone(state.doc));
      state.doc.revision = saved.revision;
      // Edits made during the request are preserved and saved in the next revision.
      if (version === state.version) {
        state.doc = saved;
        state.dirty = false;
        status(state.path.length ? 'openPath' : 'saved');
        notice();
      }
      state.record.annotation_status = saved.status;
      state.record.instance_count = saved.polygons.length;
      stash();
      updateList();
      updatePanels();
      draw();
    }
  })();
  try { await state.saving; } finally { state.saving = null; }
}

function updateList() {
  const filter = $('filter').value;
  const previousScroll = $('case-list').scrollTop;
  $('case-list').replaceChildren();
  state.dataset.records.forEach((record, index) => {
    if (filter !== 'all' && record.annotation_status !== filter) return;
    const button = document.createElement('button');
    button.className = `case-item${index === state.index ? ' active' : ''}`;
    button.setAttribute('aria-label', `${t('images')} ${record.id}`);
    button.setAttribute('aria-current', index === state.index ? 'true' : 'false');
    const image = document.createElement('img');
    image.src = `/thumbnails/${record.id}.jpg`;
    image.alt = '';
    image.loading = 'lazy';
    const copy = document.createElement('span');
    copy.className = 'case-copy';
    const name = document.createElement('strong');
    name.textContent = record.id;
    const small = document.createElement('small');
    small.textContent = t(record.annotation_status);
    copy.append(name, small);
    const dot = document.createElement('span');
    dot.className = `status-dot ${record.annotation_status}`;
    button.append(image, copy, dot);
    button.onclick = () => loadCase(index).catch(showError);
    $('case-list').append(button);
  });
  $('case-list').scrollTop = previousScroll;
  $('progress').textContent = `${state.dataset.records.filter(r => r.annotation_status !== 'draft').length} / ${state.dataset.records.length}`;
}

async function loadCase(index, discard = false) {
  if (state.loading || index < 0 || index >= state.dataset.records.length) return;
  state.loading = true;
  try {
    if (!discard && state.path.length && !await confirmAction(t('leavePath'))) return;
    if (state.saving) { try { await state.saving; } catch (error) { if (!discard) throw error; } }
    if (state.doc && !discard) await flush();
    clearTimeout(state.timer);
    const record = state.dataset.records[index];
    const doc = await api(`/api/case/${record.id}`);
    const image = new Image();
    image.src = `/images/${record.id}.png`;
    await image.decode();
    if (image.naturalWidth !== record.width || image.naturalHeight !== record.height) {
      throw new Error('Preview dimensions differ from the annotation coordinate system');
    }
    if (state.record) {
      state.path = [];
      if (discard) state.dirty = false;
      stash();
    }
    Object.assign(state, { record, doc, image, index, path: [], selected: null, vertex: null,
      undo: [], redo: [], dirty: false, version: 0, drag: null });
    let cached = null;
    try { cached = JSON.parse(localStorage.getItem(draftKey()) || 'null'); } catch (_) { /* Invalid cache is ignored. */ }
    if (cached && !discard && Array.isArray(cached.path) && cached.doc?.id === record.id) {
      const sameRevision = cached.doc.revision === doc.revision;
      if (await confirmAction(t(sameRevision ? 'recovery' : 'conflictRecovery'))) {
        state.doc = cached.doc;
        state.path = cached.path.filter(p => Array.isArray(p) && p.length === 2 && p.every(Number.isFinite));
        state.dirty = Boolean(cached.dirty);
        // A stale recovery is deliberately not rebased; the server rejects its revision.
      }
    }
    $('case-title').textContent = record.id;
    $('page').textContent = `${index + 1} / ${state.dataset.records.length}`;
    $('notes').value = state.doc.notes;
    const resolution = record.resolution_nm;
    $('resolution').textContent = resolution ? `${resolution} nm/px` : 'px';
    $('field-size').textContent = resolution
      ? `${(record.width * resolution / 1000).toFixed(1)} × ${(record.height * resolution / 1000).toFixed(1)} µm`
      : `${record.width} × ${record.height} px`;
    $('target').textContent = state.dataset.target;
    $('target').title = state.dataset.target;
    notice();
    status(state.dirty ? 'unsaved' : state.path.length ? 'openPath' : doc.revision ? 'saved' : 'blank');
    resize(true);
    updateList();
    updatePanels();
  } finally { state.loading = false; }
}

function updatePanels() {
  if (!state.doc) return;
  $('instance-count').textContent = state.doc.polygons.length;
  $('instance-list').replaceChildren();
  if (!state.doc.polygons.length) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = t('noInstances');
    $('instance-list').append(empty);
  }
  for (const polygon of state.doc.polygons) {
    const button = document.createElement('button');
    button.className = `instance-item${polygon.id === state.selected ? ' active' : ''}`;
    button.setAttribute('aria-label', `${t('instance')} ${polygon.id}`);
    const swatch = document.createElement('span');
    swatch.className = 'swatch';
    swatch.style.background = palette[(polygon.id - 1) % palette.length];
    const name = document.createElement('span');
    name.textContent = `${t('instance')} ${polygon.id}`;
    const detail = document.createElement('small');
    detail.textContent = polygon.partial ? t('partial') : `${polygon.points.length} ${t('points')}`;
    button.append(swatch, name, detail);
    button.onclick = () => { if (state.path.length) { notice(t('closeFirst')); return; }
      state.selected = polygon.id; state.vertex = null; setMode('edit'); updatePanels(); draw(); };
    $('instance-list').append(button);
  }
  const polygon = selectedPolygon();
  $('partial').disabled = !polygon;
  $('partial').checked = Boolean(polygon?.partial);
  $('delete-instance').disabled = !polygon;
  $('delete-vertex').disabled = !polygon || state.vertex === null || polygon.points.length <= 3;
  $('undo').disabled = !state.undo.length && !state.path.length;
  $('redo').disabled = !state.redo.length || Boolean(state.path.length);
  $('close-path').disabled = state.path.length < 3;
  $('cancel-path').disabled = !state.path.length;
  $('prev').disabled = state.index === 0;
  $('next').disabled = state.index === state.dataset.records.length - 1;
  $('complete').disabled = !state.doc.polygons.length || Boolean(state.path.length);
  $('negative').disabled = Boolean(state.doc.polygons.length || state.path.length);
  $('draft').disabled = state.doc.status === 'draft';
}

function selectedPolygon() { return state.doc?.polygons.find(p => p.id === state.selected); }
function setMode(mode) {
  if (state.path.length && mode !== 'draw') { notice(t('closeFirst')); return; }
  state.mode = mode;
  document.querySelectorAll('[data-mode]').forEach(button => {
    button.classList.toggle('active', button.dataset.mode === mode);
    button.setAttribute('aria-pressed', String(button.dataset.mode === mode));
  });
  canvas.style.cursor = mode === 'pan' ? 'grab' : mode === 'edit' ? 'default' : 'crosshair';
  draw();
}

function resize(fit = false) {
  const rectangle = $('canvas-wrap').getBoundingClientRect();
  const center = [(state.width / 2 - state.tx) / state.scale, (state.height / 2 - state.ty) / state.scale];
  state.width = rectangle.width;
  state.height = rectangle.height;
  canvas.width = Math.round(state.width * devicePixelRatio);
  canvas.height = Math.round(state.height * devicePixelRatio);
  state.tx = state.width / 2 - center[0] * state.scale;
  state.ty = state.height / 2 - center[1] * state.scale;
  if (fit) fitView(); else draw();
}

function fitView() {
  if (!state.image) return;
  state.scale = Math.min((state.width - 30) / state.record.width, (state.height - 30) / state.record.height);
  state.tx = (state.width - state.record.width * state.scale) / 2;
  state.ty = (state.height - state.record.height * state.scale) / 2;
  draw();
}

function zoomAt(factor, x = state.width / 2, y = state.height / 2) {
  if (!state.image) return;
  const previous = state.scale;
  state.scale = Math.max(0.01, Math.min(24, state.scale * factor));
  state.tx = x - (x - state.tx) * state.scale / previous;
  state.ty = y - (y - state.ty) * state.scale / previous;
  draw();
}

function trace(points, close = true) {
  context.beginPath();
  points.forEach((point, index) => index ? context.lineTo(...point) : context.moveTo(...point));
  if (close) context.closePath();
}

function draw() {
  context.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
  context.clearRect(0, 0, state.width, state.height);
  if (!state.image) return;
  const scale = state.scale;
  context.save();
  context.translate(state.tx, state.ty);
  context.scale(scale, scale);
  context.filter = `brightness(${$('brightness').value}%) contrast(${$('contrast').value}%)`;
  context.drawImage(state.image, 0, 0);
  context.filter = 'none';
  if (state.overlay) for (const polygon of state.doc.polygons) {
    const color = palette[(polygon.id - 1) % palette.length];
    trace(polygon.points);
    context.fillStyle = color;
    context.globalAlpha = Number($('opacity').value) / 100;
    context.fill();
    context.globalAlpha = 1;
    context.strokeStyle = color;
    context.lineWidth = (polygon.id === state.selected ? 2.4 : 1.6) / scale;
    context.setLineDash(polygon.partial ? [7 / scale, 4 / scale] : []);
    context.stroke();
    context.setLineDash([]);
    const center = polygon.points.reduce((sum, p) => [sum[0] + p[0] / polygon.points.length, sum[1] + p[1] / polygon.points.length], [0, 0]);
    context.font = `${13 / scale}px system-ui`;
    const text = String(polygon.id);
    context.fillStyle = '#101915cc';
    context.fillRect(center[0] - 3 / scale, center[1] - 14 / scale, context.measureText(text).width + 7 / scale, 19 / scale);
    context.fillStyle = color;
    context.fillText(text, ...center);
    if (polygon.id === state.selected && state.mode === 'edit') {
      polygon.points.forEach((point, index) => {
        context.beginPath();
        context.arc(...point, (index === state.vertex ? 5 : 3.5) / scale, 0, Math.PI * 2);
        context.fillStyle = index === state.vertex ? '#ffde65' : '#fff';
        context.fill();
        context.strokeStyle = '#16251d';
        context.lineWidth = 1 / scale;
        context.stroke();
      });
    }
  }
  if (state.path.length) {
    trace(state.path, false);
    context.strokeStyle = '#ffdc68';
    context.lineWidth = 2 / scale;
    context.stroke();
    if (state.mouse) {
      context.beginPath();
      context.moveTo(...state.path[state.path.length - 1]);
      context.lineTo(...state.mouse);
      context.setLineDash([5 / scale, 4 / scale]);
      context.stroke();
      context.setLineDash([]);
    }
    state.path.forEach((point, index) => {
      context.beginPath();
      context.arc(...point, (index === 0 ? 5 : 3) / scale, 0, Math.PI * 2);
      context.fillStyle = index === 0 ? '#ffdc68' : '#fff';
      context.fill();
    });
  }
  context.restore();
  const unit = state.record.resolution_nm ? state.record.resolution_nm / 1000 : 1;
  const desired = Math.min(140, state.width / 3) / scale * unit;
  const power = 10 ** Math.floor(Math.log10(desired));
  const length = [5, 2, 1].map(v => v * power).find(v => v <= desired) || power / 2;
  const pixels = length / unit * scale;
  context.strokeStyle = context.fillStyle = '#fff';
  context.lineWidth = 3;
  context.beginPath();
  context.moveTo(state.width - 24 - pixels, state.height - 22);
  context.lineTo(state.width - 24, state.height - 22);
  context.stroke();
  context.font = '12px system-ui';
  context.fillText(`${Number(length.toPrecision(3))} ${state.record.resolution_nm ? 'µm' : 'px'}`, state.width - 24 - pixels, state.height - 31);
  $('zoom').textContent = `${Math.round(scale * 100)}%`;
}

function coords(event) {
  const box = canvas.getBoundingClientRect();
  return [(event.clientX - box.left - state.tx) / state.scale, (event.clientY - box.top - state.ty) / state.scale];
}
function bounded(point) { return [Math.max(0, Math.min(state.record.width - 1, point[0])), Math.max(0, Math.min(state.record.height - 1, point[1]))]; }
function inside(point) { return point[0] >= 0 && point[1] >= 0 && point[0] < state.record.width && point[1] < state.record.height; }
function distance(a, b) { return Math.hypot(a[0] - b[0], a[1] - b[1]); }
function inPolygon(point, points) {
  let inside = false;
  for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
    const a = points[i], b = points[j];
    if (((a[1] > point[1]) !== (b[1] > point[1])) && point[0] < (b[0] - a[0]) * (point[1] - a[1]) / (b[1] - a[1]) + a[0]) inside = !inside;
  }
  return inside;
}
function segmentDistance(point, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const fraction = Math.max(0, Math.min(1, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / (dx * dx + dy * dy || 1)));
  return distance(point, [a[0] + fraction * dx, a[1] + fraction * dy]);
}

function closePath() {
  if (state.path.length < 3) return;
  const id = Math.max(0, ...state.doc.polygons.map(p => p.id)) + 1;
  if (id > 65534) { notice(t('maxId')); return; }
  beginChange();
  state.doc.polygons.push({ id, points: clone(state.path), partial: false });
  state.selected = id;
  state.vertex = null;
  state.path = [];
  changed();
}
function cancelPath() { state.path = []; state.mouse = null; stash(); notice(); updatePanels(); draw(); status(state.dirty ? 'unsaved' : state.doc.revision ? 'saved' : 'blank'); }
function undo() {
  if (state.path.length) { state.path.pop(); stash(); updatePanels(); draw(); return; }
  if (!state.undo.length) return;
  state.redo.push(snapshot());
  Object.assign(state.doc, state.undo.pop());
  state.selected = state.vertex = null;
  $('notes').value = state.doc.notes;
  changed(false);
}
function redo() {
  if (!state.redo.length || state.path.length) return;
  state.undo.push(snapshot());
  Object.assign(state.doc, state.redo.pop());
  state.selected = state.vertex = null;
  $('notes').value = state.doc.notes;
  changed(false);
}
function removeInstance() {
  if (state.selected === null) return;
  beginChange();
  state.doc.polygons = state.doc.polygons.filter(p => p.id !== state.selected);
  state.selected = state.vertex = null;
  changed();
}
function removeVertex() {
  const polygon = selectedPolygon();
  if (!polygon || state.vertex === null || polygon.points.length <= 3) return;
  beginChange();
  polygon.points.splice(state.vertex, 1);
  state.vertex = null;
  changed();
}

canvas.addEventListener('pointerdown', event => {
  if (!state.image || state.loading) return;
  canvas.focus();
  const point = coords(event);
  if (state.mode === 'pan' || state.space || event.button === 1) {
    state.drag = { kind: 'pan', x: event.clientX, y: event.clientY, tx: state.tx, ty: state.ty };
    canvas.setPointerCapture(event.pointerId);
    return;
  }
  if (event.button !== 0 || !inside(point)) return;
  if (state.mode === 'draw') {
    if (state.path.length >= 3 && distance(point, state.path[0]) * state.scale < 10) { closePath(); return; }
    if (!state.path.length || distance(point, state.path[state.path.length - 1]) * state.scale > 2) {
      state.path.push(bounded(point)); stash(); status('openPath'); updatePanels(); draw();
    }
    return;
  }
  const polygon = selectedPolygon();
  if (polygon) {
    const index = polygon.points.findIndex(vertex => distance(vertex, point) * state.scale < 8);
    if (index >= 0) {
      state.vertex = index;
      beginChange();
      clearTimeout(state.timer);
      // Changing the revision counter here prevents an in-flight save replacing a drag.
      state.version += 1;
      state.drag = { kind: 'vertex', id: polygon.id, vertex: index };
      canvas.setPointerCapture(event.pointerId);
      updatePanels(); draw(); return;
    }
  }
  state.selected = [...state.doc.polygons].reverse().find(p => inPolygon(point, p.points))?.id ?? null;
  state.vertex = null;
  updatePanels(); draw();
});
canvas.addEventListener('pointermove', event => {
  if (!state.image) return;
  state.mouse = coords(event);
  $('position').textContent = `XY ${Math.round(state.mouse[0])}, ${Math.round(state.mouse[1])}`;
  if (state.drag?.kind === 'pan') {
    state.tx = state.drag.tx + event.clientX - state.drag.x;
    state.ty = state.drag.ty + event.clientY - state.drag.y;
  } else if (state.drag?.kind === 'vertex') {
    state.doc.polygons.find(p => p.id === state.drag.id).points[state.drag.vertex] = bounded(state.mouse);
  }
  draw();
});
function finishDrag(event) {
  const edited = state.drag?.kind === 'vertex';
  state.drag = null;
  if (edited) changed();
  if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
}
canvas.addEventListener('pointerup', finishDrag);
canvas.addEventListener('pointercancel', finishDrag);
canvas.addEventListener('pointerleave', () => { state.mouse = null; draw(); });
canvas.addEventListener('dblclick', event => {
  event.preventDefault();
  if (state.mode === 'draw') { closePath(); return; }
  if (state.mode !== 'edit') return;
  const polygon = selectedPolygon();
  if (!polygon) return;
  const point = coords(event);
  let best = -1, closest = 10 / state.scale;
  polygon.points.forEach((a, index) => {
    const dist = segmentDistance(point, a, polygon.points[(index + 1) % polygon.points.length]);
    if (dist < closest) { closest = dist; best = index; }
  });
  if (best >= 0) { beginChange(); polygon.points.splice(best + 1, 0, bounded(point)); state.vertex = best + 1; changed(); }
});
canvas.addEventListener('wheel', event => {
  event.preventDefault();
  const box = canvas.getBoundingClientRect();
  zoomAt(Math.exp(-event.deltaY * 0.001), event.clientX - box.left, event.clientY - box.top);
}, { passive: false });
canvas.addEventListener('contextmenu', event => event.preventDefault());

async function setStatus(value) {
  if (!state.doc || state.loading) return;
  if (state.path.length) { notice(t('closeFirst')); return; }
  if (value === 'negative' && !await confirmAction(t('confirmNegative'))) return;
  if (value === 'complete' && !await confirmAction(t('confirmComplete'))) return;
  beginChange();
  state.doc.status = value;
  changed(false);
  await flush();
}

$('partial').onchange = () => { const p = selectedPolygon(); if (!p) return; beginChange(); p.partial = $('partial').checked; changed(); };
$('notes').oninput = () => { if (!state.doc) return; state.doc.notes = $('notes').value; changed(false); };
document.querySelectorAll('[data-mode]').forEach(button => { button.onclick = () => setMode(button.dataset.mode); });
$('undo').onclick = undo;
$('redo').onclick = redo;
$('close-path').onclick = closePath;
$('cancel-path').onclick = cancelPath;
$('fit').onclick = fitView;
$('zoom-in').onclick = () => zoomAt(1.25);
$('zoom-out').onclick = () => zoomAt(0.8);
$('toggle-overlay').onclick = () => { state.overlay = !state.overlay; $('toggle-overlay').setAttribute('aria-pressed', String(state.overlay)); draw(); };
$('save').onclick = () => flush().catch(showError);
$('prev').onclick = () => loadCase(state.index - 1).catch(showError);
$('next').onclick = () => loadCase(state.index + 1).catch(showError);
$('filter').onchange = updateList;
$('delete-instance').onclick = removeInstance;
$('delete-vertex').onclick = removeVertex;
for (const key of ['opacity', 'brightness', 'contrast']) $(key).oninput = draw;
$('reset-display').onclick = () => { $('brightness').value = 100; $('contrast').value = 100; $('opacity').value = 12; draw(); };
for (const value of ['complete', 'negative', 'draft']) $(value).onclick = () => setStatus(value).catch(showError);
$('reload-case').onclick = async () => {
  try { if (state.doc && await confirmAction(t('discard'))) await loadCase(state.index, true); } catch (error) { showError(error); }
};
$('download-draft').onclick = () => {
  if (!state.doc) return;
  const blob = new Blob([JSON.stringify({ ...state.doc, open_path: state.path }, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url; link.download = `${state.record.id}_draft.json`; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  notice(t('backupReady'));
};
$('export').onclick = async () => {
  try {
    if (!state.dataset) return;
    if (state.path.length) { notice(t('closeFirst')); return; }
    $('export').disabled = true;
    await flush();
    const result = await api('/api/export', {});
    const link = document.createElement('a');
    link.href = result.download; link.download = result.file; link.click();
    notice(`${t('exported')} ${result.cases}`);
  } catch (error) { showError(error); } finally { $('export').disabled = false; }
};
$('language').onchange = () => {
  localize($('language').value);
  try { localStorage.setItem('label-tools:language', language); } catch (_) { /* Optional preference. */ }
  status(state.statusKey, state.statusKey === 'error');
  if (state.dataset) updateList();
  updatePanels(); draw();
};

window.addEventListener('keydown', event => {
  if (!state.doc || state.loading || document.querySelector('dialog[open]') || ['INPUT', 'TEXTAREA', 'SELECT'].includes(event.target.tagName)) return;
  const key = event.key.toLowerCase();
  if ((event.ctrlKey || event.metaKey) && ['s', 'z', 'y'].includes(key)) {
    event.preventDefault();
    if (key === 's') flush().catch(showError);
    else if (key === 'z' && !event.shiftKey) undo(); else redo();
    return;
  }
  if (event.code === 'Space') { event.preventDefault(); state.space = true; canvas.style.cursor = 'grab'; }
  if (event.key === 'Enter' && event.target === canvas) { event.preventDefault(); closePath(); }
  if (event.key === 'Escape') cancelPath();
  if (event.key === 'Backspace' && state.path.length) { event.preventDefault(); undo(); }
  if (event.key === 'Delete') state.vertex !== null ? removeVertex() : removeInstance();
  if (key === 'p') setMode('draw');
  if (key === 'v') setMode('edit');
  if (key === 'h') setMode('pan');
  if (key === 'f') fitView();
});
window.addEventListener('keyup', event => { if (event.code === 'Space') { state.space = false; setMode(state.mode); } });
window.addEventListener('blur', () => { state.space = false; });
window.addEventListener('beforeunload', event => {
  stash();
  if (state.dirty || state.saving || state.path.length) { event.preventDefault(); event.returnValue = ''; }
});
new ResizeObserver(() => resize()).observe($('canvas-wrap'));

(async () => {
  try {
    lucide.createIcons();
    state.dataset = await api('/api/dataset');
    let preferred = state.dataset.language;
    try { preferred = localStorage.getItem('label-tools:language') || preferred; } catch (_) { /* Optional preference. */ }
    localize(preferred);
    setMode('draw');
    await loadCase(0);
  } catch (error) { showError(error); }
})();
