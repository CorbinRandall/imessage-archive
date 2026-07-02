const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

const DAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function fmtTime(ts) {
  if (!ts) return 'never';
  return new Date(ts * 1000).toLocaleString();
}

function isOnline(lastSeen) {
  return lastSeen && (Date.now() / 1000 - lastSeen) < 120;
}

function formatScheduleDays(days) {
  if (!days?.length) return 'No days';
  return days.map(d => DAY_LABELS[d]).join(', ');
}

function formatScheduleTime(h, m) {
  return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}`;
}

// Navigation
$$('.nav').forEach(btn => btn.onclick = () => {
  $$('.nav').forEach(b => b.classList.remove('active'));
  $$('.view').forEach(v => v.classList.remove('active'));
  btn.classList.add('active');
  $(`#view-${btn.dataset.view}`).classList.add('active');
  if (btn.dataset.view === 'browse') loadChats();
  if (btn.dataset.view === 'schedules') loadSchedules();
  if (btn.dataset.view === 'dashboard') loadDashboard();
});

// Dashboard
async function loadDashboard() {
  const [stats, clients] = await Promise.all([api('/api/stats'), api('/api/clients')]);
  const a = stats.archive || {};
  $('#stat-cards').innerHTML = `
    <div class="card"><div class="label">Messages</div><div class="value">${a.message_count || 0}</div></div>
    <div class="card"><div class="label">Chats</div><div class="value">${a.chat_count || 0}</div></div>
    <div class="card"><div class="label">Contacts</div><div class="value">${a.contact_count || 0}</div></div>
    <div class="card"><div class="label">Media files</div><div class="value">${a.html_media_count || 0}</div></div>
  `;
  $('#sidebar-stats').innerHTML = `${a.message_count || 0} msgs · ${a.contact_count || 0} contacts`;

  $('#clients-list').innerHTML = (clients.clients || []).map(c => `
    <div class="client-card">
      <div class="client-head">
        <div>
          <span class="status-dot ${isOnline(c.last_seen_at) ? 'online' : 'offline'}"></span>
          <strong>${esc(c.name)}</strong>
          <span class="muted"> · ${esc(c.hostname)}</span>
        </div>
        <button class="btn small" onclick="triggerBackup('${c.id}')">Backup now</button>
      </div>
      <div class="muted" style="margin-top:.5rem;font-size:.85rem">
        Last seen: ${fmtTime(c.last_seen_at)} · Last backup: ${fmtTime(c.last_backup_at)}
        ${c.last_status ? ` · ${esc(c.last_status)}` : ''}
        ${c.trigger_pending ? ' · <strong style="color:var(--warn)">Backup queued</strong>' : ''}
      </div>
    </div>
  `).join('') || '<p class="muted">No Mac clients registered yet.</p>';

  $('#runs-list').innerHTML = (clients.runs || []).map(r => `
    <div class="run-row">
      <strong>${esc(r.client_name)}</strong> · ${esc(r.status)} · ${esc(r.phase || '')}
      ${r.schedule_name ? ` · <em>${esc(r.schedule_name)}</em>` : ''}
      <span class="muted"> · ${fmtTime(r.started_at)} · ${esc(r.triggered_by)}</span>
      ${r.message ? `<div class="muted" style="margin-top:.3rem">${esc(r.message)}</div>` : ''}
    </div>
  `).join('') || '<p class="muted">No backups yet.</p>';
}

window.triggerBackup = async (id) => {
  await api(`/api/clients/${id}/backup/trigger`, { method: 'POST' });
  loadDashboard();
};

$('#btn-reindex').onclick = async () => {
  await api('/api/index', { method: 'POST', body: '{"full":true}' });
  alert('Reindex started');
};

// Browse
let allChats = [];
async function loadChats() {
  const data = await api('/api/chats');
  allChats = data.chats || [];
  renderChatList(allChats);
}

function renderChatList(chats) {
  $('#chat-list').innerHTML = chats.map(c => `
    <div class="chat-item" data-id="${c.chat_id}" onclick="openChat(${c.chat_id})">
      <div class="title">${esc(c.chat)}</div>
      <div class="meta">${c.message_count} messages · ${esc((c.last_date || '').slice(0, 10))}</div>
    </div>
  `).join('');
}

$('#chat-filter').oninput = (e) => {
  const q = e.target.value.toLowerCase();
  renderChatList(allChats.filter(c => (c.chat || '').toLowerCase().includes(q)));
};

function renderMedia(attachments) {
  return (attachments || []).map(a => {
    const paths = a.paths?.length ? a.paths : [a.path];
    const url = `/api/media/${encodeURI(paths[0])}`;
    const mime = a.mime_type || '';
    if (mime.startsWith('image/') || /\.(jpe?g|png|gif|heic|webp)$/i.test(a.name || ''))
      return `<img src="${url}" alt="${esc(a.name)}" loading="lazy" onerror="this.style.display='none'" />`;
    if (mime.startsWith('video/') || /\.(mp4|mov)$/i.test(a.name || ''))
      return `<video src="${url}" controls preload="metadata"></video>`;
    if (mime.startsWith('audio/') || /\.(m4a|caf|mp3)$/i.test(a.name || ''))
      return `<audio src="${url}" controls></audio>`;
    return `<a href="${url}" target="_blank">${esc(a.name || 'Download')}</a>`;
  }).join('');
}

window.openChat = async (chatId) => {
  $$('.chat-item').forEach(el => el.classList.toggle('active', +el.dataset.id === chatId));
  const data = await api(`/api/chats/${chatId}/messages?limit=2000`);
  const detail = $('#chat-detail');
  detail.innerHTML = (data.messages || []).map(m => `
    <div class="msg ${m.is_from_me ? 'me' : ''}">
      <div class="meta">${esc(m.sender)} · ${esc((m.date || '').replace('T', ' ').slice(0, 19))}</div>
      ${m.text ? `<div class="text">${esc(m.text)}</div>` : ''}
      ${renderMedia(m.attachments)}
    </div>
  `).join('') || '<p class="muted">No messages</p>';
  detail.scrollTop = detail.scrollHeight;
};

// Search
async function runSearch() {
  const q = $('#search-q').value.trim();
  if (!q) return;
  $('#search-results').innerHTML = '<p class="muted">Searching...</p>';
  const data = await api(`/api/search?q=${encodeURIComponent(q)}&limit=30`);
  $('#search-results').innerHTML = (data.results || []).map(r => `
    <div class="result">
      <div><strong>${esc(r.chat)}</strong> <span class="muted">${(r.score * 100).toFixed(0)}% · ${esc(r.sender)} · ${esc((r.date || '').slice(0, 10))}</span></div>
      <div style="margin-top:.5rem;white-space:pre-wrap">${esc(r.text)}</div>
    </div>
  `).join('') || '<p class="muted">No results</p>';
}

$('#btn-search').onclick = runSearch;
$('#search-q').onkeydown = e => { if (e.key === 'Enter') runSearch(); };

// Schedules CRUD
let allClients = [];
let allSchedules = [];

async function loadSchedules() {
  const [schedData, clientData] = await Promise.all([
    api('/api/schedules'),
    api('/api/clients'),
  ]);
  allSchedules = schedData.schedules || [];
  allClients = clientData.clients || [];
  renderSchedulesTable();
  $('#schedule-editor').classList.add('hidden');
}

function renderSchedulesTable() {
  if (!allSchedules.length) {
    $('#schedules-list').innerHTML = '<p class="muted">No schedules yet. Click <strong>+ New schedule</strong> to create one.</p>';
    return;
  }
  $('#schedules-list').innerHTML = `
    <table class="schedules-table">
      <thead>
        <tr><th>Name</th><th>Mac</th><th>Days</th><th>Time</th><th>Status</th><th>Last run</th><th></th></tr>
      </thead>
      <tbody>
        ${allSchedules.map(s => `
          <tr>
            <td><strong>${esc(s.name)}</strong></td>
            <td>${esc(s.client_name)}</td>
            <td>${esc(formatScheduleDays(s.days))}</td>
            <td>${formatScheduleTime(s.hour, s.minute)}</td>
            <td><span class="sched-badge ${s.enabled ? 'on' : 'off'}">${s.enabled ? 'Enabled' : 'Disabled'}</span></td>
            <td class="muted">${fmtTime(s.last_run_at)}</td>
            <td class="row-actions">
              <button class="btn small secondary" onclick="editSchedule('${s.id}')">Edit</button>
              <button class="btn small secondary" onclick="deleteSchedule('${s.id}')">Delete</button>
            </td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;
}

function scheduleEditorHtml(schedule = null) {
  const days = schedule?.days || [0,1,2,3,4];
  const clientId = schedule?.client_id || (allClients[0]?.id || '');
  return `
    <h3>${schedule ? 'Edit schedule' : 'New schedule'}</h3>
    <div style="margin:.75rem 0">
      <label>Name <input type="text" id="ed-name" value="${esc(schedule?.name || 'Weekday backup')}" style="width:100%;margin-top:.25rem" /></label>
    </div>
    <div style="margin:.75rem 0">
      <label>Mac
        <select id="ed-client" style="width:100%;margin-top:.25rem" ${schedule ? 'disabled' : ''}>
          ${allClients.map(c => `<option value="${c.id}" ${c.id === clientId ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}
        </select>
      </label>
    </div>
    <div style="margin:.75rem 0">
      <label><input type="checkbox" id="ed-enabled" ${schedule?.enabled !== false ? 'checked' : ''} /> Enabled</label>
    </div>
    <div class="days-row" id="ed-days">${DAY_LABELS.map((d, i) =>
      `<button type="button" class="day-btn ${days.includes(i) ? 'on' : ''}" data-day="${i}">${d}</button>`
    ).join('')}</div>
    <div class="time-row">
      <label>Time
        <input type="number" id="ed-hour" min="0" max="23" value="${schedule?.hour ?? 18}" style="width:4rem" /> :
        <input type="number" id="ed-minute" min="0" max="59" value="${schedule?.minute ?? 0}" style="width:4rem" />
      </label>
    </div>
    <div class="row-actions" style="margin-top:1rem">
      <button class="btn" id="ed-save">${schedule ? 'Update' : 'Create'}</button>
      <button class="btn secondary" id="ed-cancel">Cancel</button>
    </div>
  `;
}

$('#btn-new-schedule').onclick = () => {
  if (!allClients.length) { alert('No Mac clients connected yet.'); return; }
  const ed = $('#schedule-editor');
  ed.innerHTML = scheduleEditorHtml();
  ed.classList.remove('hidden');
  bindEditor(null);
};

window.editSchedule = (id) => {
  const sched = allSchedules.find(s => s.id === id);
  if (!sched) return;
  const ed = $('#schedule-editor');
  ed.innerHTML = scheduleEditorHtml(sched);
  ed.classList.remove('hidden');
  bindEditor(sched);
};

function bindEditor(schedule) {
  $('#ed-days').onclick = (e) => {
    if (e.target.classList.contains('day-btn')) e.target.classList.toggle('on');
  };
  $('#ed-cancel').onclick = () => { $('#schedule-editor').classList.add('hidden'); };
  $('#ed-save').onclick = async () => {
    const days = [...$('#ed-days').querySelectorAll('.day-btn.on')].map(b => +b.dataset.day);
    const body = {
      name: $('#ed-name').value.trim() || 'Schedule',
      enabled: $('#ed-enabled').checked,
      days,
      hour: +$('#ed-hour').value,
      minute: +$('#ed-minute').value,
    };
    if (schedule) {
      await api(`/api/schedules/${schedule.id}`, { method: 'PUT', body: JSON.stringify(body) });
    } else {
      body.client_id = $('#ed-client').value;
      await api('/api/schedules', { method: 'POST', body: JSON.stringify(body) });
    }
    await loadSchedules();
    $('#schedule-editor').classList.add('hidden');
  };
}

window.deleteSchedule = async (id) => {
  if (!confirm('Delete this schedule?')) return;
  await api(`/api/schedules/${id}`, { method: 'DELETE' });
  await loadSchedules();
};

loadDashboard();
setInterval(loadDashboard, 15000);
