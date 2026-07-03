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

function linkify(s) {
  return esc(s).replace(/(https?:\/\/[^\s<]+)/g, u => `<a href="${u}" target="_blank" rel="noopener">${u}</a>`);
}

function fmtTime(ts) {
  if (!ts) return 'never';
  return new Date(ts * 1000).toLocaleString();
}

function fmtMsgTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

function fmtDay(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
}

function isOnline(lastSeen) {
  return lastSeen && (Date.now() / 1000 - lastSeen) < 120;
}

function initials(name) {
  if (!name) return '?';
  const parts = name.trim().split(/[\s,]+/).filter(Boolean);
  if (!parts.length) return '?';
  return (parts[0][0] + (parts[1]?.[0] || '')).toUpperCase();
}

function mediaUrl(att, thumb = false) {
  const base = att.attachment_id != null
    ? `/api/media/attachment/${att.attachment_id}`
    : `/api/media/${encodeURI((att.paths?.length ? att.paths[0] : att.path) || '')}`;
  return thumb ? `${base}?size=thumb` : base;
}

function mediaFallbacks(att, thumb = false) {
  const urls = [];
  const push = (u) => { if (u && !urls.includes(u)) urls.push(u); };
  push(mediaUrl(att, thumb));
  if (!thumb) {
    for (const p of (att.paths || [att.path]).filter(Boolean)) {
      push(`/api/media/${encodeURI(p)}`);
    }
  }
  return urls;
}

// Navigation
$$('.nav').forEach(btn => btn.onclick = () => {
  $$('.nav').forEach(b => b.classList.remove('active'));
  $$('.view').forEach(v => v.classList.remove('active'));
  btn.classList.add('active');
  $(`#view-${btn.dataset.view}`).classList.add('active');
  if (btn.dataset.view === 'browse') loadChats();
  if (btn.dataset.view === 'media') loadMedia(true);
  if (btn.dataset.view === 'schedules') loadSchedules();
  if (btn.dataset.view === 'dashboard') loadDashboard();
});

// ===== Dashboard =====
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

// ===== Messages (iMessage style) =====
let allChats = [];
let currentChat = null;

async function loadChats() {
  $('#chat-list').innerHTML = '<p class="muted center" style="padding:1rem">Loading...</p>';
  try {
    const data = await api('/api/chats');
    allChats = data.chats || [];
    if (!allChats.length) {
      $('#chat-list').innerHTML = '<p class="muted center" style="padding:1rem">No conversations yet.</p>';
      return;
    }
    renderChatList(allChats);
  } catch (err) {
    $('#chat-list').innerHTML = `<p class="muted center" style="padding:1rem">Error: ${esc(err.message)}</p>`;
  }
}

function renderChatList(chats) {
  if (!chats.length) {
    $('#chat-list').innerHTML = '<p class="muted center" style="padding:1rem">No matches.</p>';
    return;
  }
  $('#chat-list').innerHTML = chats.map(c => `
    <div class="chat-item ${currentChat === c.chat_id ? 'active' : ''}" data-id="${c.chat_id}" onclick="openChat(${c.chat_id})">
      <div class="chat-avatar">${esc(initials(c.chat))}</div>
      <div class="chat-item-body">
        <div class="title">${esc(c.chat)}</div>
        <div class="meta">${c.message_count} messages · ${esc((c.last_date || '').slice(0, 10))}</div>
      </div>
    </div>
  `).join('');
}

$('#chat-filter').oninput = (e) => {
  const q = e.target.value.toLowerCase();
  renderChatList(allChats.filter(c => (c.chat || '').toLowerCase().includes(q)));
};

// Group avatar assets sync as attachments but aren't real messages.
function isJunkAttachment(a) {
  return (a.name || '') === 'GroupPhotoImage';
}

function renderBubbleMedia(attachments) {
  return (attachments || []).filter(a => !isJunkAttachment(a)).map(a => {
    const urls = mediaFallbacks(a);
    const url = urls[0];
    const mime = a.mime_type || '';
    const name = a.name || '';
    const fallback = urls[1] ? `onerror="if(this.src!=='${urls[1]}'){this.src='${urls[1]}';}else{this.closest('.msg-media')?.remove();}"` : `onerror="this.closest('.msg-media')?.remove()"`;
    if (mime.startsWith('image/') || /\.(jpe?g|png|gif|heic|webp)$/i.test(name))
      return `<div class="msg-media" onclick="openLightbox('${url}','image','${esc(name)}')"><img src="${url}" alt="${esc(name)}" loading="lazy" ${fallback} /></div>`;
    if (mime.startsWith('video/') || /\.(mp4|mov|m4v)$/i.test(name))
      return `<div class="msg-media"><video src="${url}" controls preload="metadata" onerror="this.closest('.msg-media')?.remove()"></video></div>`;
    if (mime.startsWith('audio/') || /\.(m4a|caf|mp3|aac|wav)$/i.test(name))
      return `<audio src="${url}" controls preload="none" onerror="this.remove()"></audio>`;
    return `<a class="file-link" href="${url}" target="_blank">&#128206; ${esc(name || 'Attachment')}</a>`;
  }).join('');
}

function renderReactions(reactions) {
  if (!reactions?.length) return '';
  const shown = reactions.slice(0, 6);
  return `<div class="reactions">${shown.map(r =>
    `<span class="reaction-badge" title="${esc(r.sender)}">${esc(r.emoji)}</span>`
  ).join('')}${reactions.length > 6 ? `<span class="reaction-badge">+${reactions.length - 6}</span>` : ''}</div>`;
}

window.openChat = async (chatId) => {
  currentChat = chatId;
  $$('.chat-item').forEach(el => el.classList.toggle('active', +el.dataset.id === chatId));
  const chatInfo = allChats.find(c => c.chat_id === chatId);
  $('#chat-header').innerHTML = chatInfo ? `
    ${esc(chatInfo.chat)}
    <div class="sub">${esc((chatInfo.participants || []).join(', '))}</div>
  ` : '';
  const detail = $('#chat-detail');
  detail.innerHTML = '<p class="muted center">Loading...</p>';

  const data = await api(`/api/chats/${chatId}/messages?limit=2000`);
  const msgs = data.messages || [];
  if (!msgs.length) {
    detail.innerHTML = '<p class="muted center">No messages</p>';
    return;
  }

  const byGuid = {};
  for (const m of msgs) if (m.guid) byGuid[m.guid] = m;

  let html = '';
  let lastDay = '';
  let lastSender = null;
  let lastTs = 0;
  for (const m of msgs) {
    const realAtts = (m.attachments || []).filter(a => !isJunkAttachment(a));
    if (!m.text && !realAtts.length && !(m.reactions || []).length) continue;
    const day = (m.date || '').slice(0, 10);
    const ts = m.date ? Date.parse(m.date) : 0;
    if (day !== lastDay) {
      html += `<div class="day-divider">${esc(fmtDay(m.date))} · ${esc(fmtMsgTime(m.date))}</div>`;
      lastDay = day;
      lastSender = null;
    } else if (ts && lastTs && ts - lastTs > 3600_000) {
      html += `<div class="day-divider">${esc(fmtMsgTime(m.date))}</div>`;
      lastSender = null;
    }
    lastTs = ts;
    const side = m.is_from_me ? 'me' : 'them';
    const senderChanged = m.sender !== lastSender;
    const showSender = m.is_group && !m.is_from_me && senderChanged;
    const isSms = (m.service || '').toLowerCase() === 'sms';
    lastSender = m.sender;

    html += `<div class="msg-row ${side} ${senderChanged ? 'gap' : ''}">`;
    if (showSender) html += `<div class="msg-sender">${esc(m.sender)}</div>`;
    const origin = m.thread_originator_guid && byGuid[m.thread_originator_guid];
    if (origin && origin.text) {
      html += `<div class="reply-quote">${esc(origin.sender)}: ${esc(origin.text.slice(0, 80))}${origin.text.length > 80 ? '…' : ''}</div>`;
    }
    if (m.text) {
      html += `<div class="bubble ${isSms ? 'sms' : ''}" title="${esc(fmtDay(m.date))} ${esc(fmtMsgTime(m.date))}">${linkify(m.text)}${m.edited ? '<span class="edited-tag">(edited)</span>' : ''}</div>`;
    }
    html += renderBubbleMedia(m.attachments);
    html += renderReactions(m.reactions);
    html += `</div>`;
  }
  detail.innerHTML = html;
  detail.scrollTop = detail.scrollHeight;
  // Late-loading media shifts layout; keep pinned to bottom unless the user scrolled up.
  const repin = () => {
    if (detail.scrollHeight - detail.scrollTop - detail.clientHeight < 800) detail.scrollTop = detail.scrollHeight;
  };
  detail.querySelectorAll('img').forEach(el => el.addEventListener('load', repin));
  detail.querySelectorAll('video').forEach(el => el.addEventListener('loadedmetadata', repin));
};

// ===== Media tab =====
let mediaKind = 'all';
let mediaOffset = 0;
let mediaTotal = 0;
const MEDIA_PAGE = 100;

$$('.media-filters .chip').forEach(chip => chip.onclick = () => {
  $$('.media-filters .chip').forEach(c => c.classList.remove('active'));
  chip.classList.add('active');
  mediaKind = chip.dataset.kind;
  loadMedia(true);
});

async function loadMedia(reset = false) {
  if (reset) {
    mediaOffset = 0;
    $('#media-grid').innerHTML = '<p class="muted">Loading media...</p>';
  }
  const data = await api(`/api/media-gallery?kind=${mediaKind}&limit=${MEDIA_PAGE}&offset=${mediaOffset}`);
  mediaTotal = data.total;
  $('#media-count').textContent = `${data.total} items`;

  const cells = (data.items || []).map(item => {
    const thumbUrl = mediaUrl(item, true);
    const fullUrl = mediaUrl(item, false);
    const caption = `${item.chat || ''} · ${(item.date || '').slice(0, 10)}`;
    if (item.kind === 'video')
      return `<div class="media-cell" onclick="openLightbox('${fullUrl}','video','${esc(caption)}')">
        <img src="${thumbUrl}" loading="lazy" alt="" onerror="this.closest('.media-cell')?.remove()" />
        <span class="badge">&#9654; video</span>
        <div class="cell-meta">${esc(caption)}</div>
      </div>`;
    if (item.kind === 'audio')
      return `<div class="media-cell audio-cell">
        <div>&#127911;</div>
        <audio src="${fullUrl}" controls preload="none" style="width:100%"></audio>
        <div>${esc(caption)}</div>
      </div>`;
    return `<div class="media-cell" onclick="openLightbox('${fullUrl}','image','${esc(caption)}')">
      <img src="${thumbUrl}" loading="lazy" alt="" onerror="this.closest('.media-cell')?.remove()" />
      ${item.kind === 'gif' ? '<span class="badge">GIF</span>' : ''}
      <div class="cell-meta">${esc(caption)}</div>
    </div>`;
  }).join('');

  if (reset) $('#media-grid').innerHTML = cells || '<p class="muted">No media found. Run a backup first.</p>';
  else $('#media-grid').insertAdjacentHTML('beforeend', cells);

  mediaOffset += (data.items || []).length;
  $('#btn-media-more').classList.toggle('hidden', mediaOffset >= mediaTotal);
}

$('#btn-media-more').onclick = () => loadMedia(false);

// ===== Lightbox =====
window.openLightbox = (url, type, caption) => {
  const content = $('#lightbox-content');
  content.innerHTML = type === 'video'
    ? `<video src="${url}" controls autoplay></video>`
    : `<img src="${url}" />`;
  $('#lightbox-caption').textContent = caption || '';
  $('#lightbox').classList.remove('hidden');
};
$('#lightbox-close').onclick = () => {
  $('#lightbox').classList.add('hidden');
  $('#lightbox-content').innerHTML = '';
};
$('#lightbox').onclick = (e) => {
  if (e.target.id === 'lightbox') $('#lightbox-close').click();
};
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && !$('#lightbox').classList.contains('hidden')) $('#lightbox-close').click();
});

// ===== Search =====
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

// ===== Schedules CRUD =====
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

function formatScheduleDays(days) {
  if (!days?.length) return 'No days';
  return days.map(d => DAY_LABELS[d]).join(', ');
}

function formatScheduleTime(h, m) {
  return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}`;
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
setInterval(() => {
  if ($('#view-dashboard').classList.contains('active')) loadDashboard();
}, 15000);
