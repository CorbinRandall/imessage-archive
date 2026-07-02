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
  const [stats, clients] = await Promise.all([
    api('/api/stats'),
    api('/api/clients'),
  ]);
  const a = stats.archive || {};
  $('#stat-cards').innerHTML = `
    <div class="card"><div class="label">Messages</div><div class="value">${a.message_count || 0}</div></div>
    <div class="card"><div class="label">Chats</div><div class="value">${a.chat_count || 0}</div></div>
    <div class="card"><div class="label">Attachments</div><div class="value">${a.attachment_count || 0}</div></div>
    <div class="card"><div class="label">HTML Exports</div><div class="value">${a.html_export_count || 0}</div></div>
  `;
  $('#sidebar-stats').innerHTML = `${a.message_count || 0} messages · ${a.chat_count || 0} chats`;

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
  `).join('') || '<p class="muted">No Mac clients registered yet. Install the client agent on your Mac.</p>';

  $('#runs-list').innerHTML = (clients.runs || []).map(r => `
    <div class="run-row">
      <strong>${esc(r.client_name)}</strong> · ${esc(r.status)} · ${esc(r.phase || '')}
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

window.openChat = async (chatId) => {
  $$('.chat-item').forEach(el => el.classList.toggle('active', +el.dataset.id === chatId));
  const data = await api(`/api/chats/${chatId}/messages?limit=1000`);
  const detail = $('#chat-detail');
  detail.innerHTML = (data.messages || []).map(m => {
    const media = (m.attachments || []).map(a => {
      const url = `/api/media/${encodeURI(a.path)}`;
      if ((a.mime_type || '').startsWith('image/')) return `<img src="${url}" alt="${esc(a.name)}" loading="lazy" />`;
      if ((a.mime_type || '').startsWith('video/')) return `<video src="${url}" controls></video>`;
      if ((a.mime_type || '').startsWith('audio/')) return `<audio src="${url}" controls></audio>`;
      return `<a href="${url}" target="_blank">${esc(a.name)}</a>`;
    }).join('');
    return `<div class="msg ${m.is_from_me ? 'me' : ''}">
      <div class="meta">${esc(m.sender)} · ${esc((m.date || '').replace('T', ' ').slice(0, 19))}</div>
      ${m.text ? `<div class="text">${esc(m.text)}</div>` : ''}
      ${media}
    </div>`;
  }).join('') || '<p class="muted">No messages</p>';
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

// Schedules
async function loadSchedules() {
  const { clients } = await api('/api/clients');
  $('#schedules-list').innerHTML = (clients || []).map(c => scheduleCard(c)).join('')
    || '<p class="muted">No clients registered. Run install-client.sh on your Mac.</p>';
}

function scheduleCard(c) {
  const days = c.days || [];
  return `<div class="schedule-card" data-client="${c.id}">
    <strong>${esc(c.name)}</strong> <span class="muted">${esc(c.hostname)}</span>
    <div style="margin-top:.75rem">
      <label><input type="checkbox" class="sched-enabled" ${c.schedule_enabled ? 'checked' : ''} /> Enable scheduled backups</label>
    </div>
    <div class="days-row">${DAY_LABELS.map((d, i) =>
      `<button type="button" class="day-btn ${days.includes(i) ? 'on' : ''}" data-day="${i}">${d}</button>`
    ).join('')}</div>
    <div class="time-row">
      <label>Time <input type="number" class="sched-hour" min="0" max="23" value="${c.hour ?? 3}" style="width:4rem" /> :
      <input type="number" class="sched-minute" min="0" max="59" value="${c.minute ?? 0}" style="width:4rem" /></label>
      <button class="btn small save-sched">Save</button>
    </div>
  </div>`;
}

$('#schedules-list').addEventListener('click', async (e) => {
  const card = e.target.closest('.schedule-card');
  if (!card) return;
  if (e.target.classList.contains('day-btn')) {
    e.target.classList.toggle('on');
    return;
  }
  if (!e.target.classList.contains('save-sched')) return;
  const days = [...card.querySelectorAll('.day-btn.on')].map(b => +b.dataset.day);
  await api(`/api/clients/${card.dataset.client}/schedule`, {
    method: 'PUT',
    body: JSON.stringify({
      enabled: card.querySelector('.sched-enabled').checked,
      days,
      hour: +card.querySelector('.sched-hour').value,
      minute: +card.querySelector('.sched-minute').value,
    }),
  });
  alert('Schedule saved');
});

loadDashboard();
setInterval(loadDashboard, 15000);
