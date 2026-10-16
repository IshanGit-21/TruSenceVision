/* ============================================================
   TruSenceVision — app.js
   ============================================================ */

const BASE = 'http://localhost:5001/api';
let backendUp   = false;
let delTarget   = null;
let allStudents = [];

// session state
let sessId    = null;
let sessStart = null;
let sessTimer = null;
let sessPoll  = null;
let lastDetTs = 0;

// camera
let captures  = [];
let camStream = null;
const MAX_CAP = 5;

// attendance
let selSessId = null;

/* ─── PAGE META ──────────────────────────────────────── */
const PAGE_META = {
  dashboard:  { title: 'Dashboard',          sub: 'Overview of your attendance system' },
  register:   { title: 'Register Student',   sub: 'Add a new student and capture their face' },
  session:    { title: 'Live Session',        sub: 'Start a class and monitor attendance in real time' },
  attendance: { title: 'Attendance Records', sub: 'Browse all sessions and student attendance data' },
  students:   { title: 'Students',           sub: 'All registered students and face encoding status' },
};

/* ─── NAVIGATION ─────────────────────────────────────── */
function nav(page) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('on'));
  document.getElementById('page-' + page).classList.add('active');
  document.querySelector(`.nav-item[data-page="${page}"]`).classList.add('on');
  document.getElementById('topTitle').textContent = PAGE_META[page].title;
  document.getElementById('topSub').textContent   = PAGE_META[page].sub;

  if (page === 'dashboard')  loadDashboard();
  if (page === 'students')   loadStudents();
  if (page === 'attendance') loadAttendance();
  if (page === 'session')    loadSessCount();
}

document.querySelectorAll('.nav-item').forEach(el => {
  el.addEventListener('click', () => nav(el.dataset.page));
});

/* ─── CLOCK ──────────────────────────────────────────── */
(function tick() {
  const n = new Date();
  const d = n.toLocaleDateString('en-IN', { weekday:'short', day:'2-digit', month:'short' });
  const t = n.toLocaleTimeString('en-IN', { hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:true });
  setEl('clockDate', d);
  setEl('clockTime', t);
  setTimeout(tick, 1000);
})();

/* ─── HEALTH PING ────────────────────────────────────── */
async function ping() {
  try {
    await fetch(`${BASE}/ping`, { signal: AbortSignal.timeout(2500) });
    backendUp = true;
    document.getElementById('srvDot').className = 'dot on';
    setEl('srvTxt', 'Backend online');
  } catch {
    backendUp = false;
    document.getElementById('srvDot').className = 'dot';
    setEl('srvTxt', 'Backend offline');
  }
}
ping();
setInterval(ping, 5000);

/* ─── HELPERS ────────────────────────────────────────── */
function setEl(id, v) {
  const e = document.getElementById(id);
  if (e) e.textContent = v;
}
function esc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function fmtDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('en-IN', {
    day:'2-digit', month:'short', year:'numeric',
    hour:'2-digit', minute:'2-digit', hour12:true
  });
}
function fmtDur(s) {
  if (!s) return '—';
  const h = Math.floor(s/3600), m = Math.floor((s%3600)/60), ss = s%60;
  return h ? `${h}h ${m}m` : `${m}m ${ss}s`;
}
function pbarCls(p) { return p >= 75 ? 'fg-ok' : p >= 50 ? 'fg-warn' : 'fg-err'; }
function pctCls(p)  { return p >= 75 ? 'pg'   : p >= 50 ? 'pw'      : 'pb'; }

function toast(type, msg) {
  const ok  = type === 'ok';
  const el  = document.getElementById(ok ? 'toastOk'  : 'toastErr');
  const txt = document.getElementById(ok ? 'toastOkMsg' : 'toastErrMsg');
  txt.textContent = msg;
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 3200);
}

/* ─── DASHBOARD ──────────────────────────────────────── */
async function loadDashboard() {
  if (!backendUp) return;
  try {
    const d = await fetch(`${BASE}/dashboard`).then(r => r.json());
    if (!d.success) return;
    setEl('s-stu',   d.total_students ?? '—');
    setEl('s-sess',  d.total_sessions ?? '—');
    setEl('s-today', d.today_present  ?? '—');
    const lv  = document.getElementById('s-live');
    const sub = document.getElementById('s-live-sub');
    if (d.session_active) {
      lv.textContent  = 'Active';
      lv.style.color  = 'var(--ok)';
      if (sub) sub.textContent = 'Session in progress';
    } else {
      lv.textContent  = 'Inactive';
      lv.style.color  = 'var(--ink3)';
      if (sub) sub.textContent = 'No active session';
    }
    renderRecent(d.recent_sessions || []);
  } catch {}
}

function renderRecent(arr) {
  const el = document.getElementById('dashRecent');
  if (!arr.length) {
    el.innerHTML = `<div class="empty"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg><p>No sessions yet.<br>Start a class to see records here.</p></div>`;
    return;
  }
  el.innerHTML = arr.map(s => `
    <div class="r-row">
      <div>
        <div class="r-nm">${esc(s.subject)}</div>
        <div class="r-dt">${fmtDate(s.started_at)}</div>
      </div>
      <div class="r-tags">
        <span class="tag t-green">${s.present_count ?? 0} Present</span>
        <span class="tag t-red">${s.absent_count ?? 0} Absent</span>
      </div>
    </div>`).join('');
}

loadDashboard();

/* ─── REGISTER — CAMERA ──────────────────────────────── */
async function openCam() {
  try {
    camStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
    const vid = document.getElementById('camVideo');
    vid.srcObject = camStream;
    vid.style.display = 'block';
    document.getElementById('camPH').style.display = 'none';
    document.getElementById('camCaptureBtn').disabled = false;
    document.getElementById('camOpenBtn').disabled = true;
    document.getElementById('camOpenBtn').textContent = '● Active';
  } catch {
    toast('err', 'Camera access denied — allow camera in browser settings.');
  }
}

function doCapture() {
  if (captures.length >= MAX_CAP) return;
  const vid = document.getElementById('camVideo');
  const cvs = document.getElementById('camCanvas');
  cvs.width  = vid.videoWidth  || 640;
  cvs.height = vid.videoHeight || 480;
  cvs.getContext('2d').drawImage(vid, 0, 0);
  cvs.toBlob(blob => {
    if (!blob) return;
    captures.push(blob);
    const slot = document.getElementById('th' + (captures.length - 1));
    slot.classList.add('done');
    const img = document.createElement('img');
    img.src = URL.createObjectURL(blob);
    slot.appendChild(img);
    setEl('capCount', `${captures.length} / ${MAX_CAP} photos`);
    updateCapNote();
    if (captures.length >= MAX_CAP) {
      document.getElementById('camCaptureBtn').disabled = true;
      document.getElementById('camCaptureBtn').textContent = '✓ Done';
    }
    // flash
    const f = document.createElement('div');
    f.style.cssText = 'position:absolute;inset:0;background:#fff;opacity:.45;z-index:10;pointer-events:none;transition:opacity .18s;';
    document.getElementById('camBox').appendChild(f);
    setTimeout(() => f.style.opacity = '0', 60);
    setTimeout(() => f.remove(), 280);
  }, 'image/jpeg', 0.92);
}

function updateCapNote() {
  const el = document.getElementById('capNotice');
  const n  = captures.length;
  document.getElementById('regBtn').disabled = (n === 0);
  if (n === 0) {
    el.className = 'cap-note';
    el.innerHTML = `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg><div><strong>No face captured yet</strong><span>Use the camera to capture 5 photos first</span></div>`;
  } else if (n < MAX_CAP) {
    el.className = 'cap-note';
    el.innerHTML = `<svg viewBox="0 0 24 24" style="stroke:var(--warn)"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg><div><strong>${n} photo${n > 1?'s':''} captured</strong><span>Capture ${MAX_CAP - n} more for best accuracy</span></div>`;
  } else {
    el.className = 'cap-note ok';
    el.innerHTML = `<svg viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg><div><strong>All 5 photos captured — ready to register</strong><span>Fill in student details and click Register</span></div>`;
  }
}

function resetCam() {
  captures = [];
  for (let i = 0; i < MAX_CAP; i++) {
    const s = document.getElementById('th' + i);
    s.className = 'thumb'; s.innerHTML = '';
  }
  setEl('capCount', '0 / 5 photos');
  const btn = document.getElementById('camCaptureBtn');
  btn.disabled = !camStream;
  btn.textContent = 'Capture';
  updateCapNote();
}

function stopCam() {
  if (camStream) { camStream.getTracks().forEach(t => t.stop()); camStream = null; }
  const vid = document.getElementById('camVideo');
  if (vid) { vid.srcObject = null; vid.style.display = 'none'; }
  const ph = document.getElementById('camPH');
  if (ph) ph.style.display = 'flex';
  const ob = document.getElementById('camOpenBtn');
  if (ob) { ob.disabled = false; ob.textContent = 'Open Camera'; }
  const cb = document.getElementById('camCaptureBtn');
  if (cb) cb.disabled = true;
}

function clearReg() {
  ['r-name','r-roll','r-class','r-email'].forEach(id => { const e=document.getElementById(id); if(e) e.value=''; });
  document.getElementById('r-sem').value = '6';
  resetCam(); stopCam();
}

document.getElementById('regForm').addEventListener('submit', async e => {
  e.preventDefault();
  const name  = document.getElementById('r-name').value.trim();
  const roll  = document.getElementById('r-roll').value.trim();
  const cls   = document.getElementById('r-class').value.trim();
  const sem   = document.getElementById('r-sem').value;
  const email = document.getElementById('r-email').value.trim();

  if (!name || !roll || !cls) { toast('err', 'Fill in all required fields.'); return; }
  if (captures.length === 0)  { toast('err', 'Capture at least one photo first.'); return; }
  if (!backendUp) { toast('err', 'Backend offline — run: python app.py'); return; }

  const btn = document.getElementById('regBtn');
  btn.disabled = true; btn.textContent = 'Registering…';

  const fd = new FormData();
  fd.append('name', name); fd.append('roll_no', roll);
  fd.append('class_name', cls); fd.append('semester', sem); fd.append('email', email);
  captures.forEach((b, i) => fd.append('images', b, `cap_${i}.jpg`));

  try {
    const res = await fetch(`${BASE}/students/register`, { method:'POST', body:fd }).then(r => r.json());
    if (res.success) { toast('ok', `${name} registered successfully.`); clearReg(); }
    else toast('err', res.message || 'Registration failed.');
  } catch { toast('err', 'Network error — is Flask running?'); }
  finally { btn.disabled = false; btn.textContent = 'Register Student'; }
});

/* ─── SESSION ────────────────────────────────────────── */
async function loadSessCount() {
  try {
    const d = await fetch(`${BASE}/students`).then(r => r.json());
    if (d.success) setEl('sessRegCount', `${d.students.length} student${d.students.length !== 1?'s':''} registered`);
  } catch { setEl('sessRegCount', ''); }
}

async function startSession() {
  const subj = document.getElementById('sessSubj').value.trim();
  if (!subj) { document.getElementById('sessSubj').focus(); return; }
  if (!backendUp) { alert('Backend offline.\n\nRun: python app.py'); return; }

  try {
    const res = await fetch(`${BASE}/sessions/start`, {
      method: 'POST', headers: { 'Content-Type':'application/json' },
      body: JSON.stringify({
        subject:      subj,
        min_interval: parseInt(document.getElementById('sessMin').value) || 2,
        max_interval: parseInt(document.getElementById('sessMax').value) || 5,
        threshold:    parseInt(document.getElementById('sessThresh').value) || 75,
      })
    }).then(r => r.json());

    if (!res.success) { alert(res.message || 'Failed to start session.'); return; }

    sessId    = res.session_id;
    sessStart = Date.now();

    document.getElementById('sess-start').style.display = 'none';
    document.getElementById('sess-live').style.display  = 'block';
    setEl('liveSubj', subj);
    setEl('lv-checks', '0'); setEl('lv-present', '0');
    document.getElementById('liveFeed').src = 'http://localhost:5001/video_feed';

    sessTimer = setInterval(() => {
      const e = Math.floor((Date.now() - sessStart) / 1000);
      const h = Math.floor(e/3600), m = Math.floor((e%3600)/60), s = e%60;
      setEl('liveTimer', [h,m,s].map(v => String(v).padStart(2,'0')).join(':'));
    }, 1000);

    await loadStuLiveList();
    sessPoll = setInterval(pollSess, 4000);
  } catch { alert('Error starting session.'); }
}

async function loadStuLiveList() {
  try {
    const d = await fetch(`${BASE}/students`).then(r => r.json());
    if (!d.success) return;
    setEl('lv-total', d.students.length);
    const el = document.getElementById('stuLiveList');
    el.innerHTML = '';
    d.students.forEach(st => {
      const row = document.createElement('div');
      row.className = 'slu-row';
      row.dataset.roll = st.roll_no;
      row.innerHTML = `<span class="slu-nm">${esc(st.name)}</span><span class="slu-pct pb" id="spct_${esc(st.roll_no)}">0%</span>`;
      el.appendChild(row);
    });
  } catch {}
}

async function pollSess() {
  if (!sessId) return;
  try {
    const d = await fetch(`${BASE}/sessions/${sessId}/status`).then(r => r.json());
    if (!d.success) return;
    setEl('lv-checks',  d.checks_done       ?? 0);
    setEl('lv-present', d.currently_present ?? 0);
    if (d.next_check_in != null) {
      const m = Math.floor(d.next_check_in/60), s = d.next_check_in%60;
      setEl('lv-next', m > 0 ? `${m}m ${s}s` : `${s}s`);
    }
    if (d.check_running) {
      const ov = document.getElementById('checkOverlay');
      ov.style.display = 'flex';
      setTimeout(() => ov.style.display = 'none', 2500);
    }
    if (d.latest_detections?.length) addDets(d.latest_detections);
    if (d.student_status) updateStuPcts(d.student_status);
  } catch {}
}

function addDets(arr) {
  const news = arr.filter(d => d.timestamp > lastDetTs);
  if (!news.length) return;
  const log   = document.getElementById('detLog');
  const empty = log.querySelector('.empty');
  if (empty) empty.remove();
  news.forEach(d => {
    lastDetTs = Math.max(lastDetTs, d.timestamp);
    const t  = new Date(d.timestamp * 1000).toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:true});
    const row = document.createElement('div');
    row.className = 'det-row';
    const isP = d.status === 'present';
    row.innerHTML = `<span class="det-nm">${esc(d.name)}</span><span class="det-tm">${t}</span><span class="tag ${isP?'t-green':'t-red'}">${isP?'Present':'Absent'}</span>`;
    log.insertBefore(row, log.firstChild);
  });
  while (log.children.length > 40) log.removeChild(log.lastChild);
}

function updateStuPcts(map) {
  Object.entries(map).forEach(([roll, info]) => {
    const el = document.getElementById(`spct_${roll}`);
    if (!el) return;
    const p = Math.round(info.pct);
    el.textContent = p + '%';
    el.className = 'slu-pct ' + pctCls(p);
  });
}

async function stopSession() {
  if (!sessId) return;
  if (!confirm('Stop the session and finalise attendance?')) return;
  try {
    const res = await fetch(`${BASE}/sessions/${sessId}/stop`, { method:'POST' }).then(r => r.json());
    if (res.success) {
      clearInterval(sessTimer); clearInterval(sessPoll);
      sessTimer = sessPoll = null;
      document.getElementById('sess-live').style.display  = 'none';
      document.getElementById('sess-ended').style.display = 'block';
      setEl('endedSub', `"${res.subject}" complete — attendance saved.`);
      renderResultCards(res.attendance || []);
    }
  } catch { toast('err', 'Failed to stop session.'); }
}

function renderResultCards(att) {
  const el = document.getElementById('resultCards');
  if (!att.length) { el.innerHTML = '<p style="text-align:center;color:var(--ink3);grid-column:span 3">No data.</p>'; return; }
  el.innerHTML = att.map(a => {
    const pct = Math.round(a.detection_pct), isP = a.status === 'present';
    return `<div class="rc"><div class="rc-name">${esc(a.name)}</div><div class="rc-roll">${esc(a.roll_no)}</div><span class="tag ${isP?'t-green':'t-red'}">${isP?'Present':'Absent'}</span><div class="rc-pct">${pct}% detected</div></div>`;
  }).join('');
}

function resetSess() {
  sessId = null; sessStart = null; lastDetTs = 0;
  document.getElementById('sess-ended').style.display = 'none';
  document.getElementById('sess-start').style.display = 'block';
  document.getElementById('detLog').innerHTML = '<div class="empty" style="padding:14px;"><p>Waiting for first check…</p></div>';
  document.getElementById('stuLiveList').innerHTML = '<div class="empty" style="padding:12px;"><p>Loading students…</p></div>';
  loadSessCount();
}

/* ─── ATTENDANCE ─────────────────────────────────────── */
async function loadAttendance() {
  const el = document.getElementById('sessList');
  el.innerHTML = '<div class="empty" style="padding:16px;"><p>Loading…</p></div>';
  if (!backendUp) { el.innerHTML = '<div class="empty"><p>Backend offline</p></div>'; return; }
  try {
    const d = await fetch(`${BASE}/sessions`).then(r => r.json());
    const sessions = d.sessions || [];
    setEl('attSessCount', sessions.length);
    if (!sessions.length) { el.innerHTML = '<div class="empty"><p>No sessions yet</p></div>'; return; }
    el.innerHTML = sessions.map(s => `
      <div class="si ${selSessId === s.id ? 'on' : ''}" onclick="selectSess(${s.id}, this)">
        <div class="si-n">${esc(s.subject)}</div>
        <div class="si-dt">${fmtDate(s.started_at)}</div>
        <div class="si-tags">
          <span class="tag t-green">${s.present_count ?? 0} P</span>
          <span class="tag t-red">${s.absent_count ?? 0} A</span>
        </div>
      </div>`).join('');
  } catch { el.innerHTML = '<div class="empty"><p>Failed to load</p></div>'; }
}

async function selectSess(id, el) {
  selSessId = id;
  document.querySelectorAll('.si').forEach(s => s.classList.remove('on'));
  el.classList.add('on');
  document.getElementById('attSum').style.display    = 'none';
  document.getElementById('attBadges').style.display = 'none';
  document.getElementById('attTable').innerHTML = '<div class="empty"><p>Loading…</p></div>';
  try {
    const d = await fetch(`${BASE}/sessions/${id}/attendance`).then(r => r.json());
    if (!d.success) throw new Error(d.message);
    const s = d.session, att = d.attendance || [];
    setEl('attTitle', s.subject);
    const p = att.filter(a => a.status === 'present').length, ab = att.length - p;
    setEl('attPBadge', `${p} Present`); setEl('attABadge', `${ab} Absent`);
    document.getElementById('attBadges').style.display = 'flex';
    setEl('as-total', att.length); setEl('as-p', p); setEl('as-a', ab);
    setEl('as-chk', s.total_checks ?? '—'); setEl('as-dur', fmtDur(s.duration_secs));
    document.getElementById('attSum').style.display = 'flex';
    if (!att.length) { document.getElementById('attTable').innerHTML = '<div class="empty"><p>No records.</p></div>'; return; }
    document.getElementById('attTable').innerHTML = `
      <div class="tbl-wrap"><table>
        <thead><tr><th>#</th><th>Student</th><th>Roll No</th><th>Status</th><th>Detection %</th><th>Checks</th></tr></thead>
        <tbody>${att.map((a,i) => {
          const pct = Math.round(a.detection_pct), isP = a.status === 'present';
          return `<tr>
            <td style="color:var(--ink3)">${i+1}</td>
            <td><strong style="color:var(--ink)">${esc(a.name)}</strong></td>
            <td class="mono">${esc(a.roll_no)}</td>
            <td><span class="tag ${isP?'t-green':'t-red'}">${isP?'Present':'Absent'}</span></td>
            <td><div class="pbar-wrap"><div class="pbar"><div class="pbar-fill ${pbarCls(pct)}" style="width:${pct}%"></div></div><span class="ptxt">${pct}%</span></div></td>
            <td class="mono" style="color:var(--ink3)">${a.checks_present??0}/${a.total_checks??'—'}</td>
          </tr>`;
        }).join('')}</tbody>
      </table></div>`;
  } catch(e) { document.getElementById('attTable').innerHTML = `<div class="empty"><p>Error: ${esc(e.message)}</p></div>`; }
}

function exportCSV() {
  const url = selSessId ? `${BASE}/attendance/export?session_id=${selSessId}` : `${BASE}/attendance/export`;
  window.open(url, '_blank');
}

/* ─── STUDENTS ───────────────────────────────────────── */
async function loadStudents() {
  const tbody = document.getElementById('stuTbody');
  tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:32px;color:var(--ink3)">Loading…</td></tr>';
  if (!backendUp) { tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:32px;color:var(--ink3)">Backend offline</td></tr>'; return; }
  try {
    const d = await fetch(`${BASE}/students`).then(r => r.json());
    allStudents = d.students || [];
    renderStudents(allStudents);
  } catch { tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:32px;color:var(--ink3)">Failed to load</td></tr>'; }
}

function renderStudents(list) {
  setEl('stuCount', `${list.length} student${list.length!==1?'s':''}`);
  const tbody = document.getElementById('stuTbody');
  if (!list.length) { tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:32px;color:var(--ink3)">No students found.</td></tr>'; return; }
  tbody.innerHTML = list.map(st => {
    const init = st.name.split(' ').map(w=>w[0]).join('').substring(0,2).toUpperCase();
    return `<tr>
      <td><div class="av"><div class="avt">${init}</div><span class="avn">${esc(st.name)}</span></div></td>
      <td class="mono">${esc(st.roll_no)}</td>
      <td>${esc(st.class_name)}</td>
      <td style="color:var(--ink2)">Sem ${esc(String(st.semester??'—'))}</td>
      <td><span class="enc-chip ${st.face_encoded?'enc-ok':'enc-pnd'}">${st.face_encoded?'✓ Encoded':'⏳ Pending'}</span></td>
      <td style="font-size:11.5px;color:var(--ink3)">${fmtDate(st.registered_at)}</td>
      <td><button onclick="openDel('${esc(st.roll_no)}','${esc(st.name)}')" style="background:none;border:none;color:var(--err);font-size:12px;font-weight:600;cursor:pointer;font-family:Sora,sans-serif;">Delete</button></td>
    </tr>`;
  }).join('');
}

function filterStu() {
  const q   = (document.getElementById('stuSearch').value||'').toLowerCase();
  const enc = document.getElementById('stuEncFilter').value;
  renderStudents(allStudents.filter(st => {
    if (q && !st.name.toLowerCase().includes(q) && !st.roll_no.toLowerCase().includes(q)) return false;
    if (enc==='yes' && !st.face_encoded) return false;
    if (enc==='no'  &&  st.face_encoded) return false;
    return true;
  }));
}

function openDel(roll, name) {
  delTarget = roll;
  document.getElementById('delMsg').textContent = `Remove "${name}" (Roll: ${roll}) and all their face data? This cannot be undone.`;
  document.getElementById('delModal').classList.add('open');
}
function closeDel() {
  delTarget = null;
  document.getElementById('delModal').classList.remove('open');
}

document.getElementById('delConfirmBtn').addEventListener('click', async () => {
  if (!delTarget) return;
  closeDel();
  try {
    const res = await fetch(`${BASE}/students/${delTarget}`, { method:'DELETE' }).then(r => r.json());
    if (res.success) { toast('ok', 'Student deleted.'); loadStudents(); }
    else toast('err', res.message || 'Delete failed.');
  } catch { toast('err', 'Network error.'); }
  delTarget = null;
});
