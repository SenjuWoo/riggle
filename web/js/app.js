// Riggle UI: one screen, five steps. All heavy work is a server job; this file
// owns state, panels and the calls that move a character through the pipeline.
import { Viewport } from './viewport.js';
import { PaintBoard } from './paint.js';

const TOKEN = window.RIGGLE.token;
const VERSION = window.RIGGLE.version;
const $ = (sel, root = document) => root.querySelector(sel);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = (n) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 100000 ? 0 : 1)}k` : String(n));

const S = {
  state: null,
  project: null,
  mode: 'import',
  activeJob: null,
  selected: null,
  pending: null,
  previewOn: false,
  paintKey: '',
  paintEpoch: 0,
  lastLog: [],
};

const vp = new Viewport($('#view'));
const paint = new PaintBoard($('#paintOverlay'));

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------
async function api(path, { method = 'GET', body } = {}) {
  const res = await fetch(path, {
    method,
    headers: { 'X-Riggle-Token': TOKEN, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
  return data;
}

const media = (p) => `/api/file?p=${encodeURIComponent(p)}&token=${encodeURIComponent(TOKEN)}&t=${Date.now()}`;

// ---------------------------------------------------------------------------
// small UI helpers
// ---------------------------------------------------------------------------
let toastTimer = 0;
function toast(message, kind = '') {
  const node = $('#toast');
  node.textContent = message;
  node.className = `toast show ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove('show'), 3200);
}

function busy(on, title = 'Working', msg = '') {
  $('#busy').classList.toggle('hidden', !on);
  if (on) {
    $('#busyTitle').textContent = title;
    $('#busyMsg').textContent = msg;
  }
}

function setStatus(msg, pct = 0) {
  $('#statusMsg').textContent = msg;
  $('#progressBar').style.width = `${Math.max(0, Math.min(100, pct))}%`;
  $('#statusPct').textContent = pct ? `${Math.round(pct)}%` : '';
}

function showLog(job) {
  if (job?.log) S.lastLog = job.log;
  $('#logBody').textContent = S.lastLog.join('\n');
  $('#logPanel').classList.remove('hidden');
  $('#logBody').scrollTop = $('#logBody').scrollHeight;
}

// ---------------------------------------------------------------------------
// boot / render
// ---------------------------------------------------------------------------
async function boot() {
  try {
    S.state = await api('/api/state');
    S.project = S.state.project;
    render();
    if (S.project) {
      await loadModel();
      if (S.project.stage === 'exported') setMode('export');
      else if (S.project.uv?.size) setMode('paint');
      else if (S.project.joints?.length) setMode(S.project.has_rig ? 'jiggle' : 'rig');
    }
    const hash = location.hash.replace('#', '');
    if (['import', 'rig', 'jiggle', 'paint', 'export'].includes(hash)) setMode(hash);
  } catch (err) {
    toast(err.message, 'error');
    setStatus(err.message);
  }
  vp.onJointChange = () => {
    if (S.project) S.project.joints = vp.joints;
    renderSelected();
  };
  vp.onSelect = (joint) => {
    S.selected = joint;
    renderSelected();
  };
  vp.onSurface = (hit) => onSurface(hit);
  bindChrome();
  window.__R = { S, vp, paint, render, setMode, loadModel, syncViewport, saveTexture, doExport };
}

function render() {
  const p = S.project;
  $('#projectTitle').innerHTML = p
    ? `<b>${esc(p.name)}</b> · ${esc(p.stage)}`
    : 'no project';
  const dot = $('#blenderDot');
  dot.classList.toggle('ok', !!S.state?.blender?.found);
  dot.title = S.state?.blender?.found
    ? `${S.state.blender.version || 'Blender'} — ${S.state.blender.path}`
    : 'Blender not found — open Settings';

  const can = {
    import: true,
    rig: !!p,
    jiggle: !!p?.has_rig,
    paint: !!p,
    export: !!p?.bbox,
  };
  const order = ['import', 'rig', 'jiggle', 'paint', 'export'];
  const stage = p?.stage || '';
  for (const btn of document.querySelectorAll('#steps button')) {
    const mode = btn.dataset.mode;
    btn.disabled = !can[mode];
    btn.classList.toggle('active', S.mode === mode);
    const idx = order.indexOf(mode);
    const stageIdx = { created: 0, imported: 1, rigged: 2, jiggled: 3, unwrapped: 4, exported: 4 }[stage] ?? -1;
    btn.classList.toggle('done', stageIdx > idx);
  }
  renderPanel();
  renderHud();
}

function renderHud() {
  const titles = {
    import: ['Import', 'drop a model to start'],
    rig: ['Rig', S.project?.has_rig ? 'existing skeleton' : 'drag joints to fit'],
    jiggle: ['Jiggle', 'click the model to place a wobble'],
    paint: ['Paint', 'the atlas is the texture'],
    export: ['Export', 'GLB + FBX + texture + manifest'],
  };
  const [title, hint] = titles[S.mode] || ['', ''];
  $('#modeTitle').textContent = title;
  $('#modeHint').textContent = hint;
}

function setMode(mode) {
  if (S.mode === mode) return;
  if (mode !== 'import' && !S.project) return;
  if (mode === 'jiggle' && !S.project?.has_rig) return toast('Bind the skeleton first', 'error');
  if (mode === 'export' && !S.project?.bbox) return toast('Import a model first', 'error');
  S.mode = mode;
  S.pending = null;
  vp.setPlacement(false);
  vp.hideMarker();
  $('#paintOverlay').classList.toggle('hidden', mode !== 'paint');
  render();
  syncViewport();
  if (mode === 'paint') enterPaint();
  if (mode === 'jiggle' && S.previewOn) vp.startJiggle(S.project.jiggle_bones || []);
  if (mode !== 'jiggle') {
    vp.stopJiggle();
    S.previewOn = false;
  }
}

async function loadModel() {
  if (!S.project?.files?.model) {
    vp.clearRig();
    return;
  }
  await vp.load(media('work/model.glb'));
  syncViewport();
}

function syncViewport() {
  if (!S.project) return;
  const editable = S.mode === 'rig' && !S.project.has_rig;
  vp.setJoints(S.project.joints || [], editable);
  vp.mirror = true;
  vp.setPlacement(S.mode === 'jiggle' && !!S.pending);
  if (S.project.uv?.size && S.mode === 'paint') vp.applyPaintTexture(paint.texture);
}

// ---------------------------------------------------------------------------
// jobs
// ---------------------------------------------------------------------------
async function waitJob(id, { reload = true } = {}) {
  busy(true, 'Working', 'starting Blender…');
  let job = null;
  while (true) {
    job = await api(`/api/job?id=${encodeURIComponent(id)}`);
    S.activeJob = job;
    setStatus(job.msg || job.status, job.pct || 0);
    busy(true, job.kind ? `${job.kind}…` : 'Working', job.msg || '');
    S.lastLog = job.log || [];
    if (job.status === 'done' || job.status === 'error') break;
    await sleep(320);
  }
  busy(false);
  setStatus(job.status === 'error' ? job.error : 'Ready', job.status === 'error' ? 100 : 0);
  if (job.status === 'error') {
    toast(job.error || 'Job failed', 'error');
    showLog(job);
    return job;
  }
  if (job.result?.project) S.project = job.result.project;
  render();
  if (reload) await loadModel();
  if (S.mode === 'paint') enterPaint(true);
  return job;
}

async function refreshProject() {
  const st = await api('/api/state');
  S.state = st;
  S.project = st.project;
  render();
}

// ---------------------------------------------------------------------------
// import
// ---------------------------------------------------------------------------
function renderImport(panel) {
  const b = S.state?.blender || {};
  panel.innerHTML = `
    <h2>Start a character</h2>
    <p>Drop a model. Riggle fits a skeleton, lets you drag it into place, then paints and exports the whole thing.</p>
    <div class="drop" id="drop">
      <input type="file" id="file" accept=".blend,.fbx,.glb,.gltf,.obj,.stl,.ply,.usd,.usdz">
      <div class="drop-icon">↥</div>
      <b>Drop a model here</b>
      <span>or click to choose — FBX, GLB, glTF, OBJ, STL, PLY, Blend, USD</span>
    </div>
    <div class="hint"><b>No rig?</b> Riggle fits a humanoid skeleton. <b>Already rigged?</b> Keep it and just add jiggle bones.</div>
    <h3>Recent projects</h3>
    <div class="list" id="recent">${recentHtml()}</div>
    <h3>Setup</h3>
    <label class="field">Blender executable</label>
    <input type="text" id="blenderPath" value="${esc(b.path || '')}" placeholder="C:\\Program Files\\Blender Foundation\\...\\blender.exe">
    <div class="hint">${b.found ? `Found ${esc(b.version || 'Blender')}` : 'Blender was not found. Install Blender 4.5+ or point to blender.exe.'}</div>
    <label class="field">Projects folder</label>
    <input type="text" id="projectsDir" value="${esc(S.state?.projects_dir || '')}">
    <button class="soft" id="saveSettings">Save settings</button>
    <div class="hint">Version ${esc(VERSION)} · everything stays on this machine.</div>
  `;
  $('#drop').onclick = () => $('#file').click();
  $('#file').onchange = (e) => {
    const file = e.target.files?.[0];
    if (file) uploadAndImport(file);
  };
  panel.querySelectorAll('.recent-item').forEach((node) => {
    node.onclick = () => openProject(node.dataset.dir);
  });
  $('#saveSettings').onclick = async () => {
    try {
      S.state = await api('/api/config', {
        method: 'POST',
        body: { blender: $('#blenderPath').value.trim(), projects_dir: $('#projectsDir').value.trim() },
      });
      toast('Settings saved');
      render();
    } catch (err) {
      toast(err.message, 'error');
    }
  };
}

function recentHtml() {
  const items = S.state?.recent || [];
  if (!items.length) return '<div class="muted" style="font-size:12px;padding:6px 2px">Nothing yet — import your first model.</div>';
  return items.map((r) => `
    <div class="recent-item" data-dir="${esc(r.dir)}">
      <span class="dot ${r.stage === 'exported' ? 'live' : ''}"></span>
      <div class="meta"><b>${esc(r.name)}</b><span>${esc(r.updated?.replace('T', ' ').slice(0, 16) || '')}</span></div>
      <span class="pill ${esc(r.stage)}">${esc(r.stage || 'project')}</span>
    </div>`).join('');
}

async function openProject(dir) {
  try {
    busy(true, 'Opening', dir);
    const { project } = await api('/api/project/open', { method: 'POST', body: { dir } });
    S.project = project;
    S.paintKey = '';
    S.previewOn = false;
    busy(false);
    render();
    await loadModel();
    setMode(project.has_rig ? 'jiggle' : 'rig');
    if (project.uv?.size) setMode('paint');
  } catch (err) {
    busy(false);
    toast(err.message, 'error');
  }
}

function uploadAndImport(file) {
  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/upload');
  xhr.setRequestHeader('X-Riggle-Token', TOKEN);
  xhr.setRequestHeader('X-Riggle-Filename', file.name);
  busy(true, 'Uploading', file.name);
  setStatus(`Uploading ${file.name}`, 0);
  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) setStatus(`Uploading ${file.name}`, (e.loaded / e.total) * 100);
  };
  xhr.onerror = () => {
    busy(false);
    toast('Upload failed', 'error');
  };
  xhr.onload = async () => {
    try {
      if (xhr.status !== 200) throw new Error(JSON.parse(xhr.responseText).error || 'upload failed');
      const up = JSON.parse(xhr.responseText);
      setStatus('Creating project', 100);
      const created = await api('/api/project', {
        method: 'POST',
        body: { source: up.path, name: file.name.replace(/\.[^.]+$/, '') },
      });
      S.project = created.project;
      S.paintKey = '';
      await waitJob(created.job.id);
      S.mode = 'rig';
      render();
      syncViewport();
      toast('Model imported — drag the joints to fit');
    } catch (err) {
      busy(false);
      toast(err.message, 'error');
    }
  };
  xhr.send(file);
}

// ---------------------------------------------------------------------------
// rig
// ---------------------------------------------------------------------------
function renderRig(panel) {
  const p = S.project;
  if (!p) return renderImport(panel);
  const joints = p.joints || [];
  if (p.has_rig) {
    panel.innerHTML = `
      <h2>Rig</h2>
      <p>This model has a rig. Riggle keeps every bone and its weights.</p>
      <div class="hint"><b>${joints.length} bones</b> ready. Skip to <b>Jiggle</b> and mark what should wobble.</div>
      <button class="primary" id="goJiggle">Add jiggle bones</button>
      <h3>Bones</h3>
      <div class="list" id="jointList">${jointsHtml(joints)}</div>
    `;
    $('#goJiggle').onclick = () => setMode('jiggle');
  } else {
    panel.innerHTML = `
      <h2>Rig</h2>
      <p>Drag any joint in the viewport. Moving a joint carries its children, and the other side mirrors automatically.</p>
      <button class="primary" id="bind">Bind skeleton</button>
      <div class="row" style="margin-top:8px">
        <button class="soft" id="fit">Fit preset</button>
        <button class="soft" id="sym">Symmetrize</button>
      </div>
      <label class="check"><input type="checkbox" id="mirror" checked> Mirror left / right</label>
      <h3>Joints · ${joints.length}</h3>
      <div class="list" id="jointList">${jointsHtml(joints)}</div>
      <div class="hint" id="selectedJoint"></div>
    `;
    $('#bind').onclick = bindSkeleton;
    $('#fit').onclick = async () => {
      try {
        const { joints: fresh } = await api('/api/preset', { method: 'POST' });
        p.joints = fresh;
        S.selected = null;
        render();
        syncViewport();
        toast('Preset refit to the model bounds');
      } catch (err) {
        toast(err.message, 'error');
      }
    };
    $('#sym').onclick = () => {
      vp.mirror = true;
      const left = p.joints.find((j) => j.name.endsWith('.L'));
      if (!left) return toast('No .L bones to mirror', 'error');
      vp._mirror('L');
      for (const j of p.joints) vp.syncSphere(j);
      vp.rebuildLines();
      toast('Right side mirrored from left');
    };
    $('#mirror').onchange = (e) => { vp.mirror = e.target.checked; };
  }
  wireJointList();
  renderSelected();
}

function jointsHtml(joints) {
  return joints.map((j) => `
    <div class="list-item" data-joint="${esc(j.name)}">
      <span class="dot ${j.jiggle ? 'live' : ''}"></span>
      <span class="name">${esc(j.name)}</span>
      <span class="tag">${j.parent ? esc(j.parent) : 'root'}</span>
    </div>`).join('');
}

function wireJointList() {
  document.querySelectorAll('#jointList .list-item').forEach((node) => {
    node.onclick = () => {
      vp.selectJoint(node.dataset.joint);
      S.selected = vp.byName.get(node.dataset.joint);
      renderSelected();
      document.querySelectorAll('#jointList .list-item').forEach((n) => n.classList.remove('selected'));
      node.classList.add('selected');
    };
  });
}

function renderSelected() {
  const node = $('#selectedJoint');
  if (!node) return;
  const j = S.selected;
  node.innerHTML = j
    ? `<div class="mono">${esc(j.name)}</div>
       <div class="mono muted">x ${j.head[0].toFixed(3)} · y ${j.head[1].toFixed(3)} · z ${j.head[2].toFixed(3)}</div>`
    : '<span class="muted">Click a joint to select it.</span>';
}

async function bindSkeleton() {
  try {
    const job = await api('/api/bind', { method: 'POST', body: { joints: S.project.joints } });
    const done = await waitJob(job.job.id);
    if (done.status === 'done') {
      S.mode = 'jiggle';
      render();
      syncViewport();
      toast('Skeleton bound — now add jiggle bones');
    }
  } catch (err) {
    toast(err.message, 'error');
  }
}

// ---------------------------------------------------------------------------
// jiggle
// ---------------------------------------------------------------------------
const JIGGLE_DEFAULTS = { stiffness: 0.55, damping: 0.35, gravity: 0.25, mass: 0.5 };

function renderJiggle(panel) {
  const p = S.project;
  const bones = p.jiggle_bones || [];
  const pending = S.pending;
  panel.innerHTML = `
    <h2>Jiggle</h2>
    <p>Place a bone where flesh, hair or cloth should move. The radius decides which vertices follow it.</p>
    <button class="primary" id="addBone">${pending ? 'Placing… click the model' : '+ Add jiggle bone'}</button>
    ${pending ? pendingHtml(pending, p) : ''}
    <label class="check"><input type="checkbox" id="preview" ${S.previewOn ? 'checked' : ''}> Live spring preview</label>
    <button class="soft" id="shake" ${bones.length ? '' : 'disabled'}>Shake it</button>
    <h3>Jiggle bones · ${bones.length}</h3>
    <div id="jiggleList">${bones.map((b) => jiggleCard(b)).join('') || '<div class="muted" style="font-size:12px;padding:4px 2px">None yet.</div>'}</div>
  `;
  $('#addBone').onclick = startPlacement;
  $('#preview').onchange = (e) => {
    S.previewOn = e.target.checked;
    if (S.previewOn) {
      const n = vp.startJiggle(p.jiggle_bones || []);
      if (!n) toast('No matching bones on the mesh yet', 'error');
      else toast(`Previewing ${n} bone${n === 1 ? '' : 's'} — try Shake`);
    } else {
      vp.stopJiggle();
    }
  };
  $('#shake').onclick = () => vp.shake();
  if (pending) wirePending(p);
  wireJiggleCards();
  if (S.previewOn) vp.startJiggle(p.jiggle_bones || []);
}

function pendingHtml(pending, p) {
  const h = vp.modelHeight();
  return `
    <div class="pending">
      <div class="row"><b style="flex:1">New jiggle bone</b><span class="mono muted">${pending.point ? 'placed' : 'waiting for click'}</span></div>
      <label class="field">Name</label>
      <input type="text" id="pName" value="${esc(pending.name)}">
      <label class="field">Parent bone</label>
      <select id="pParent">${(p.joints || []).filter((j) => j.deform).map((j) => `<option value="${esc(j.name)}" ${j.name === pending.parent ? 'selected' : ''}>${esc(j.name)}</option>`).join('')}</select>
      <label class="field">Weight radius <span class="mono" id="pRadiusOut">${(pending.radius / h * 100).toFixed(1)}% height</span></label>
      <input type="range" id="pRadius" min="1" max="30" step="0.5" value="${(pending.radius / h * 100).toFixed(1)}">
      <label class="field">Bone length <span class="mono" id="pLengthOut">${(pending.length / h * 100).toFixed(1)}% height</span></label>
      <input type="range" id="pLength" min="2" max="40" step="0.5" value="${(pending.length / h * 100).toFixed(1)}">
      <button class="primary" id="pAdd" style="margin-top:10px" ${pending.point ? '' : 'disabled'}>Add this bone</button>
      <button class="ghost" id="pCancel" style="margin-top:6px;width:100%">Cancel</button>
    </div>`;
}

function wirePending(p) {
  const pending = S.pending;
  const h = vp.modelHeight();
  $('#pName').oninput = (e) => { pending.name = e.target.value.trim(); };
  $('#pParent').onchange = (e) => { pending.parent = e.target.value; };
  $('#pRadius').oninput = (e) => {
    pending.radius = (parseFloat(e.target.value) / 100) * h;
    $('#pRadiusOut').textContent = `${parseFloat(e.target.value).toFixed(1)}% height`;
    if (pending.point) vp.showMarker(pending.point, pending.radius);
  };
  $('#pLength').oninput = (e) => {
    pending.length = (parseFloat(e.target.value) / 100) * h;
    $('#pLengthOut').textContent = `${parseFloat(e.target.value).toFixed(1)}% height`;
  };
  $('#pCancel').onclick = () => {
    S.pending = null;
    vp.setPlacement(false);
    vp.hideMarker();
    render();
  };
  $('#pAdd').onclick = addPendingBone;
}

function jiggleCard(bone) {
  const params = { ...JIGGLE_DEFAULTS, ...(bone.params || {}) };
  const slider = (key, label) => `
    <div class="slider">
      <span>${label}</span>
      <input type="range" data-bone="${esc(bone.name)}" data-param="${key}" min="0" max="1" step="0.01" value="${params[key]}">
      <output>${params[key].toFixed(2)}</output>
    </div>`;
  return `
    <div class="jiggle-card">
      <div class="top"><span class="dot live"></span><span class="name">${esc(bone.name)}</span></div>
      ${slider('stiffness', 'stiff')}
      ${slider('damping', 'damp')}
      ${slider('gravity', 'grav')}
      ${slider('mass', 'mass')}
    </div>`;
}

function wireJiggleCards() {
  document.querySelectorAll('#jiggleList input[type=range]').forEach((input) => {
    input.oninput = () => {
      const out = input.parentElement.querySelector('output');
      out.textContent = parseFloat(input.value).toFixed(2);
      const bone = S.project.jiggle_bones.find((b) => b.name === input.dataset.bone);
      if (bone) {
        bone.params = { ...JIGGLE_DEFAULTS, ...(bone.params || {}), [input.dataset.param]: parseFloat(input.value) };
        vp.updateJiggleParams({ [bone.name]: bone.params });
      }
    };
    input.onchange = debounce(async () => {
      const bone = S.project.jiggle_bones.find((b) => b.name === input.dataset.bone);
      if (!bone) return;
      try {
        const { project } = await api('/api/jiggle/params', {
          method: 'POST',
          body: { name: bone.name, params: bone.params },
        });
        S.project = project;
      } catch (err) {
        toast(err.message, 'error');
      }
    }, 350);
  });
}

function startPlacement() {
  if (!S.project.joints?.length) return toast('Rig the model first', 'error');
  const h = vp.modelHeight();
  const count = (S.project.jiggle_bones || []).length + 1;
  S.pending = {
    point: null,
    normal: [0, 1, 0],
    name: `jiggle_${count}`,
    parent: null,
    radius: h * 0.08,
    length: h * 0.12,
  };
  vp.setPlacement(true);
  render();
  toast('Click the model where it should wobble');
}

function onSurface({ point, normal }) {
  if (S.mode !== 'jiggle' || !S.pending) return;
  S.pending.point = point;
  S.pending.normal = normal;
  S.pending.parent = S.pending.parent || nearestBone(point, S.project.joints) || (S.project.joints.find((j) => j.deform) || {}).name;
  vp.showMarker(point, S.pending.radius);
  render();
}

function nearestBone(point, joints) {
  let best = null;
  let bestD = Infinity;
  for (const j of joints) {
    if (j.deform === false) continue;
    const d = distPointSegment(point, j.head, j.tail);
    if (d < bestD) {
      bestD = d;
      best = j.name;
    }
  }
  return best;
}

function distPointSegment(p, a, b) {
  const ab = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
  const denom = ab[0] ** 2 + ab[1] ** 2 + ab[2] ** 2;
  let t = denom < 1e-12 ? 0 : ((p[0] - a[0]) * ab[0] + (p[1] - a[1]) * ab[1] + (p[2] - a[2]) * ab[2]) / denom;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(p[0] - (a[0] + ab[0] * t), p[1] - (a[1] + ab[1] * t), p[2] - (a[2] + ab[2] * t));
}

async function addPendingBone() {
  const pending = S.pending;
  if (!pending?.point) return toast('Click the model first', 'error');
  if (!/^[A-Za-z0-9_.-]{1,63}$/.test(pending.name)) return toast('Use letters, numbers, dot, dash or underscore', 'error');
  const length = pending.length;
  const n = pending.normal;
  const tail = [
    pending.point[0] + n[0] * length,
    pending.point[1] + n[1] * length,
    pending.point[2] + n[2] * length,
  ];
  const bone = {
    name: pending.name,
    parent: pending.parent,
    head: pending.point,
    tail,
    radius: pending.radius,
    params: { ...JIGGLE_DEFAULTS },
  };
  try {
    const job = await api('/api/jiggle', { method: 'POST', body: { bones: [bone] } });
    const done = await waitJob(job.job.id);
    if (done.status === 'done') {
      S.pending = null;
      vp.setPlacement(false);
      vp.hideMarker();
      S.mode = 'jiggle';
      render();
      syncViewport();
      if (S.previewOn) vp.startJiggle(S.project.jiggle_bones || []);
      toast(`${bone.name} added — shake it`);
    }
  } catch (err) {
    toast(err.message, 'error');
  }
}

// ---------------------------------------------------------------------------
// paint
// ---------------------------------------------------------------------------
function renderPaint(panel) {
  const p = S.project;
  if (!p?.uv?.size) {
    panel.innerHTML = `
      <h2>Paint</h2>
      <p>Auto UV unwraps the whole character into one atlas, then you can paint it here and see it on the model.</p>
      <label class="field">Texture size</label>
      <select id="uvSize"><option>512</option><option selected>1024</option><option>2048</option><option>4096</option></select>
      <button class="primary" id="unwrap" style="margin-top:12px">Auto UV unwrap</button>
      <div class="hint">This replaces the source UVs and materials with a fresh atlas. Your original files are never modified.</div>
    `;
    $('#unwrap').onclick = runUnwrap;
    return;
  }
  panel.innerHTML = `
    <h2>Paint</h2>
    <p>Paint the atlas. UV lines show where the mesh lives; the 3D view updates as you go.</p>
    <div class="row">
      <input type="color" id="brushColor" value="#c8ff4d">
      <button class="soft small" id="brushBtn">Brush</button>
      <button class="soft small" id="eraserBtn">Eraser</button>
    </div>
    <label class="field">Size</label>
    <input type="range" id="brushSize" min="2" max="120" value="28">
    <label class="field">Opacity</label>
    <input type="range" id="brushOpacity" min="0.05" max="1" step="0.05" value="1">
    <div class="brush-row">
      <button class="soft" id="undo">Undo</button>
      <button class="soft" id="fill">Fill</button>
    </div>
    <label class="check"><input type="checkbox" id="showUv" checked> Show UV lines</label>
    <button class="primary" id="saveTex" style="margin-top:10px">Save texture</button>
    <button class="ghost" id="rewrap" style="margin-top:8px;width:100%">Re-run Auto UV (${p.uv.size}px)</button>
    <div class="hint"><span class="kbd">Ctrl</span> + <span class="kbd">S</span> saves. Painting is local until you save.</div>
  `;
  const color = $('#brushColor');
  const size = $('#brushSize');
  const opacity = $('#brushOpacity');
  const apply = () => paint.setTool({ color: color.value, size: parseFloat(size.value), opacity: parseFloat(opacity.value) });
  color.oninput = apply;
  size.oninput = apply;
  opacity.oninput = apply;
  $('#brushBtn').onclick = () => {
    paint.setTool({ eraser: false });
    $('#brushBtn').classList.add('primary');
    $('#eraserBtn').classList.remove('primary');
  };
  $('#eraserBtn').onclick = () => {
    paint.setTool({ eraser: true });
    $('#eraserBtn').classList.add('primary');
    $('#brushBtn').classList.remove('primary');
  };
  $('#undo').onclick = () => paint.undo();
  $('#fill').onclick = () => paint.fill(color.value);
  $('#showUv').onchange = (e) => paint.setOverlay(e.target.checked);
  $('#saveTex').onclick = saveTexture;
  $('#rewrap').onclick = runUnwrap;
  apply();
}

async function runUnwrap() {
  const size = parseInt($('#uvSize')?.value || S.project?.uv?.size || 1024, 10);
  if (S.project?.uv?.size && !confirm('Re-running Auto UV replaces the current atlas and anything painted on it. Continue?')) return;
  try {
    const job = await api('/api/unwrap', { method: 'POST', body: { size } });
    const done = await waitJob(job.job.id);
    if (done.status === 'done') {
      S.paintEpoch += 1;
      S.paintKey = '';
      S.mode = 'paint';
      render();
      await enterPaint(true);
      toast('UV atlas ready — paint away');
    }
  } catch (err) {
    toast(err.message, 'error');
  }
}

async function enterPaint(force = false) {
  const p = S.project;
  if (!p?.uv?.size) return;
  const key = `${p.dir}|${p.uv.size}|${S.paintEpoch}`;
  if (force || S.paintKey !== key) {
    busy(true, 'Loading texture', 'reading the atlas…');
    await paint.load({
      textureUrl: p.texture ? media('work/texture.png') : '',
      uvUrl: media('work/uv.svg'),
      size: p.uv.size,
    });
    S.paintKey = key;
    busy(false);
  }
  paint.setOverlay(true);
  paint.onChange = (tex) => {
    vp.applyPaintTexture(tex);
    S.paintDirty = true;
  };
  vp.applyPaintTexture(paint.texture);
  S.paintDirty = false;
}

async function saveTexture() {
  if (!S.project?.uv?.size) return;
  if (!S.paintKey) await enterPaint(true);
  try {
    const res = await api('/api/texture', { method: 'POST', body: { data: paint.dataURL() } });
    S.project.texture = 'work/texture.png';
    S.paintDirty = false;
    toast(`Texture saved (${fmt(res.bytes)} bytes)`);
  } catch (err) {
    toast(err.message, 'error');
  }
}

// ---------------------------------------------------------------------------
// export
// ---------------------------------------------------------------------------
function renderExport(panel) {
  const p = S.project;
  if (!p) return renderImport(panel);
  const dir = p.export?.dir || S.state?.last_export_dir || S.state?.default_export_dir || '';
  const name = p.export?.name || p.name || 'character';
  panel.innerHTML = `
    <h2>Export</h2>
    <p>Writes a rigged GLB, FBX, the painted texture and a jiggle manifest into a folder you choose.</p>
    <label class="field">Folder</label>
    <div class="row"><input type="text" id="outDir" value="${esc(dir)}"><button class="soft small" id="browse">Browse</button></div>
    <label class="field">File name</label>
    <input type="text" id="outName" value="${esc(name)}">
    <button class="primary" id="doExport" style="margin-top:14px">Export rigged model</button>
    <div id="exportResult">${exportResultHtml(p)}</div>
    <h3>What you get</h3>
    <div class="hint">
      <b>.glb</b> — skinned mesh + skeleton + texture, jiggle manifest in extras<br>
      <b>.fbx</b> — same rig for Blender, Unity or Unreal<br>
      <b>riggle.json</b> — bone names and spring tuning<br>
      <b>textures/basecolor.png</b> — your paint
    </div>
  `;
  $('#browse').onclick = async () => {
    try {
      const { path } = await api('/api/dialog/folder', { method: 'POST', body: { initial: $('#outDir').value } });
      if (path) $('#outDir').value = path;
    } catch (err) {
      toast(err.message, 'error');
    }
  };
  $('#doExport').onclick = doExport;
  wireReveal();
}

function exportResultHtml(p) {
  const ex = p.export;
  if (!ex?.files?.length) return '';
  return `
    <div class="hint" style="margin-top:14px">
      <b>Exported ${esc(ex.at?.replace('T', ' '))}</b>
      ${ex.files.map((f) => `<div class="mono" style="font-size:11px;overflow:hidden;text-overflow:ellipsis">${esc(f)}</div>`).join('')}
      <button class="soft small" data-reveal="${esc(ex.dir)}" style="margin-top:8px">Open folder</button>
    </div>`;
}

function wireReveal() {
  document.querySelectorAll('[data-reveal]').forEach((btn) => {
    btn.onclick = () => api('/api/reveal', { method: 'POST', body: { path: btn.dataset.reveal } }).catch((e) => toast(e.message, 'error'));
  });
}

async function doExport() {
  const dir = $('#outDir').value.trim();
  const name = $('#outName').value.trim();
  if (!dir) return toast('Choose an export folder', 'error');
  try {
    if (S.paintDirty) await saveTexture();
    const job = await api('/api/export', { method: 'POST', body: { dir, name } });
    const done = await waitJob(job.job.id, { reload: false });
    if (done.status === 'done') {
      S.mode = 'export';
      render();
      toast('Exported — GLB, FBX and manifest are ready');
    }
  } catch (err) {
    toast(err.message, 'error');
  }
}

// ---------------------------------------------------------------------------
// chrome
// ---------------------------------------------------------------------------
function debounce(fn, ms) {
  let t = 0;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

function renderPanel() {
  const panel = $('#panel');
  if (S.mode === 'import') renderImport(panel);
  else if (S.mode === 'rig') renderRig(panel);
  else if (S.mode === 'jiggle') renderJiggle(panel);
  else if (S.mode === 'paint') renderPaint(panel);
  else if (S.mode === 'export') renderExport(panel);
}

function bindChrome() {
  for (const btn of document.querySelectorAll('#steps button')) {
    btn.onclick = () => setMode(btn.dataset.mode);
  }
  $('#btnSettings').onclick = () => {
    setMode('import');
    setTimeout(() => $('#blenderPath')?.focus(), 50);
  };
  $('#btnFrame').onclick = () => vp.frame();
  $('#btnWire').onclick = (e) => {
    e.target.classList.toggle('on');
    vp.setWireframe(e.target.classList.contains('on'));
  };
  $('#btnGrid').onclick = (e) => {
    e.target.classList.toggle('on');
    vp.setGrid(!e.target.classList.contains('on'));
  };
  $('#btnLog').onclick = () => showLog();
  $('#btnLogClose').onclick = () => $('#logPanel').classList.add('hidden');

  const stage = $('#stage');
  let dragDepth = 0;
  stage.addEventListener('dragenter', (e) => {
    e.preventDefault();
    dragDepth += 1;
    $('#dropzone').classList.remove('hidden');
  });
  stage.addEventListener('dragover', (e) => e.preventDefault());
  stage.addEventListener('dragleave', () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) $('#dropzone').classList.add('hidden');
  });
  stage.addEventListener('drop', (e) => {
    e.preventDefault();
    dragDepth = 0;
    $('#dropzone').classList.add('hidden');
    const file = e.dataTransfer?.files?.[0];
    if (file) uploadAndImport(file);
  });

  document.addEventListener('keydown', (e) => {
    if (e.target.matches('input, select, textarea')) return;
    if (e.ctrlKey && e.key.toLowerCase() === 's' && S.mode === 'paint') {
      e.preventDefault();
      saveTexture();
      return;
    }
    if (e.key === 'f') vp.frame();
    if (e.key === 'w') $('#btnWire').click();
    if (e.key === ' ' && S.mode === 'jiggle' && S.previewOn) {
      e.preventDefault();
      vp.shake();
    }
    if (['1', '2', '3', '4', '5'].includes(e.key)) {
      setMode(['import', 'rig', 'jiggle', 'paint', 'export'][Number(e.key) - 1]);
    }
  });
}

boot();
