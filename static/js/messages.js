// Trang Tin nhắn nhóm: lưu tin ghim mới của các nhóm để đọc/xem.
// 3 cột: nhóm có tin mới → danh sách tin của nhóm → nội dung tin được chọn.
let messageState = {
    accounts: [],
    settings: {},
    items: [],
    groups: {},
    selectedAccount: '',
    selectedGroup: '', // '' = tất cả nhóm có tin
    selectedItemId: '',
};

const UI_REFRESH_MS = 30000; // làm mới hiển thị từ db (worker server tự quét theo chu kỳ)

function qs(id) { return document.getElementById(id); }

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>'"]/g, function (c) {
        return {'&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'}[c];
    });
}

function showMessageStatus(text, type = 'info') {
    const el = qs('messageStatus');
    if (!el) return;
    el.style.display = 'block';
    el.className = `status-bar ${type}`;
    el.textContent = text;
    clearTimeout(showMessageStatus._timer);
    showMessageStatus._timer = setTimeout(() => {
        el.style.display = 'none';
    }, 4200);
}

async function apiJson(url, options = {}) {
    const res = await fetch(url, {
        headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
        ...options,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.success === false) {
        throw new Error(data.error || data.message || `HTTP ${res.status}`);
    }
    return data;
}

function letterAvatar(name, size = 40, radius = 12) {
    const letter = escapeHtml((name || 'Z').trim().slice(0, 1).toUpperCase());
    return 'data:image/svg+xml;utf8,' + encodeURIComponent(
        `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}"><rect width="100%" height="100%" rx="${radius}" fill="#dbe7f3"/><text x="50%" y="54%" text-anchor="middle" dominant-baseline="middle" font-size="${Math.round(size * 0.42)}" font-family="Arial" font-weight="bold" fill="#1f6feb">${letter}</text></svg>`
    );
}

function avatarImg(url, name, cls) {
    const fallback = letterAvatar(name);
    const src = url || fallback;
    return `<img class="${cls}" src="${escapeHtml(src)}" alt="" loading="lazy" onerror="this.onerror=null;this.src='${fallback}'">`;
}

// Biến URL trong nội dung tin thành link màu xanh bấm được (text đã escape trước).
function linkifyText(raw) {
    return escapeHtml(raw).replace(
        /(https?:\/\/[^\s<>"']+)/g,
        '<a href="$1" target="_blank" rel="noopener" class="msg-inline-link">$1</a>'
    );
}

// Ảnh Zalo CDN có thời hạn: tin cũ có thể trả 404. Thay bằng nhãn thay vì mất hẳn.
window.msgPhotoError = function (img) {
    const wrap = img.closest('.msg-item-photo-link');
    if (!wrap) return;
    const note = document.createElement('div');
    note.className = 'msg-item-photo-dead';
    note.textContent = '🖼 Ảnh không còn khả dụng (link Zalo đã hết hạn)';
    wrap.replaceWith(note);
};

function formatPinTime(ms) {
    const value = Number(ms || 0);
    if (!value) return '';
    const d = new Date(value);
    const pad = (n) => String(n).padStart(2, '0');
    return `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// ─── Combobox tài khoản (avatar + tên, mặc định tài khoản đầu tiên) ─────────

function renderAccountCombo() {
    const menu = qs('accountComboMenu');
    const label = qs('accountComboLabel');
    const avatarWrap = qs('accountComboAvatar');
    if (!menu || !label || !avatarWrap) return;

    const options = messageState.accounts.map(acc => ({
        id: acc.accountId || '',
        name: acc.name || acc.accountId || 'Tài khoản',
        avatar: acc.avatarUrl || '',
    }));
    if (!options.length) {
        label.textContent = 'Chưa có tài khoản';
        avatarWrap.innerHTML = '<span class="account-combo-all">?</span>';
        menu.innerHTML = '';
        return;
    }
    const selected = options.find(o => o.id === messageState.selectedAccount) || options[0];
    messageState.selectedAccount = selected.id;
    label.textContent = selected.name;
    avatarWrap.innerHTML = avatarImg(selected.avatar, selected.name, 'account-combo-img');

    menu.innerHTML = options.map(o => `
        <button type="button" role="option" class="account-combo-option ${o.id === selected.id ? 'active' : ''}" data-id="${escapeHtml(o.id)}" aria-selected="${o.id === selected.id}">
            ${avatarImg(o.avatar, o.name, 'account-combo-img')}
            <span>${escapeHtml(o.name)}</span>
        </button>
    `).join('');
    menu.querySelectorAll('.account-combo-option').forEach(btn => {
        btn.addEventListener('click', () => {
            messageState.selectedAccount = btn.dataset.id || '';
            messageState.selectedGroup = '';
            messageState.selectedItemId = '';
            closeAccountMenu();
            renderAccountCombo();
            loadUnread();
        });
    });
}

function closeAccountMenu() {
    const menu = qs('accountComboMenu');
    const btn = qs('accountComboBtn');
    if (menu) menu.hidden = true;
    if (btn) btn.setAttribute('aria-expanded', 'false');
}

function toggleAccountMenu() {
    const menu = qs('accountComboMenu');
    const btn = qs('accountComboBtn');
    if (!menu || !btn) return;
    menu.hidden = !menu.hidden;
    btn.setAttribute('aria-expanded', String(!menu.hidden));
}

// ─── Dữ liệu ─────────────────────────────────────────────────────────────────

async function loadAccounts() {
    try {
        const data = await apiJson('/api/accounts');
        messageState.accounts = data.accounts || [];
        if (!messageState.selectedAccount && messageState.accounts.length) {
            messageState.selectedAccount = messageState.accounts[0].accountId || '';
        }
        renderAccountCombo();
    } catch (err) {
        console.warn('Không tải được tài khoản:', err);
    }
}

async function loadSettings() {
    try {
        const data = await apiJson('/api/messages/settings');
        messageState.settings = data.settings || {};
        const auto = !!messageState.settings.auto_check_enabled;
        const interval = Number(messageState.settings.check_interval_minutes || 1);
        const autoSel = qs('autoUpdateSelect');
        if (autoSel) {
            const value = auto ? String(interval) : '0';
            if (![...autoSel.options].some(o => o.value === value)) {
                autoSel.add(new Option(`Mỗi ${interval} phút`, value));
            }
            autoSel.value = value;
        }
        const retSel = qs('retentionSelect');
        if (retSel) {
            const hours = String(Number(messageState.settings.retention_hours ?? 24));
            if (![...retSel.options].some(o => o.value === hours)) {
                retSel.add(new Option(`${hours} giờ`, hours));
            }
            retSel.value = hours;
        }
        updateLastCheckPill();
    } catch (err) {
        showMessageStatus('Không tải được thiết lập: ' + err.message, 'error');
    }
}

function updateLastCheckPill(lastSyncAt) {
    const pill = qs('msgLastCheck');
    if (!pill) return;
    const at = lastSyncAt || messageState.settings.last_check_at || '';
    pill.textContent = at ? `Lần quét: ${at}` : 'Chưa đồng bộ';
}

async function saveSettings() {
    try {
        const autoValue = Number(qs('autoUpdateSelect')?.value || 0);
        const payload = {
            auto_check_enabled: autoValue > 0,
            check_interval_minutes: autoValue > 0 ? autoValue : Number(messageState.settings.check_interval_minutes || 1),
            retention_hours: Number(qs('retentionSelect')?.value ?? 24),
        };
        const data = await apiJson('/api/messages/settings', {
            method: 'PATCH',
            body: JSON.stringify(payload),
        });
        messageState.settings = data.settings || {};
        showMessageStatus('Đã lưu thiết lập.', 'success');
        await loadUnread();
    } catch (err) {
        showMessageStatus('Không lưu được thiết lập: ' + err.message, 'error');
    }
}

async function loadUnread() {
    const params = new URLSearchParams();
    if (messageState.selectedAccount) params.set('account_id', messageState.selectedAccount);
    try {
        const data = await apiJson('/api/messages/unread?' + params.toString());
        messageState.items = data.items || [];
        messageState.groups = data.groups || {};
        if (data.lastSyncAt) updateLastCheckPill(data.lastSyncAt);
        renderAll();
    } catch (err) {
        showMessageStatus('Không tải được tin nhắn: ' + err.message, 'error');
    }
}

async function checkMessages() {
    const btn = qs('btnCheckMessages');
    const oldLabel = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = 'Đang quét...'; }
    try {
        const data = await apiJson('/api/messages/check', {
            method: 'POST',
            body: JSON.stringify({ account_id: messageState.selectedAccount }),
        });
        showMessageStatus(data.message || 'Đã quét tin nhắn các nhóm.', 'success');
        await loadUnread();
    } catch (err) {
        showMessageStatus('Không quét được tin nhắn: ' + err.message, 'error');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = oldLabel; }
    }
}

// ─── Render 3 cột ────────────────────────────────────────────────────────────

function visibleGroups() {
    const counts = {};
    const latest = {};
    messageState.items.forEach(item => {
        counts[item.groupId] = (counts[item.groupId] || 0) + 1;
        latest[item.groupId] = Math.max(latest[item.groupId] || 0, Number(item.createTime) || 0);
    });
    const rows = [];
    Object.entries(messageState.groups).forEach(([gid, meta]) => {
        if (messageState.selectedAccount && meta.accountId !== messageState.selectedAccount) return;
        if (!counts[gid]) return; // nhóm không có tin mới thì không đưa vào danh sách
        rows.push({
            groupId: gid,
            name: meta.name || gid,
            avatar: meta.avatar || '',
            lastError: meta.lastError || '',
            count: counts[gid],
            latest: latest[gid] || 0,
        });
    });
    rows.sort((a, b) => (b.latest - a.latest) || a.name.localeCompare(b.name, 'vi'));
    return rows;
}

function currentItems() {
    const gid = messageState.selectedGroup;
    return messageState.items.filter(item => !gid || item.groupId === gid);
}

function renderAll() {
    const groups = visibleGroups();
    // Nhóm đang chọn đã hết tin → quay về "Tất cả".
    if (messageState.selectedGroup && !groups.some(g => g.groupId === messageState.selectedGroup)) {
        messageState.selectedGroup = '';
    }
    renderGroupList(groups);
    renderMessageList();
    renderDetail();
}

function renderGroupList(groups) {
    const wrap = qs('msgGroupList');
    const countEl = qs('msgGroupCount');
    if (countEl) countEl.textContent = `${groups.length} nhóm có tin mới`;
    if (!wrap) return;
    if (!groups.length) {
        wrap.innerHTML = '<div class="empty-state">Chưa có tin nhắn mới.<br>Bấm "Quét ngay" để kiểm tra.</div>';
        return;
    }
    const allActive = !messageState.selectedGroup;
    const allRow = `
        <button type="button" class="msg-group-item ${allActive ? 'active' : ''}" data-gid="">
            <span class="msg-group-avatar msg-group-avatar-all">☰</span>
            <span class="msg-group-info">
                <span class="msg-group-name">Tất cả tin nhắn</span>
                <span class="msg-group-sub">${groups.length} nhóm có tin mới</span>
            </span>
            <span class="unread-badge">${messageState.items.length}</span>
        </button>`;
    wrap.innerHTML = allRow + groups.map(g => `
        <button type="button" class="msg-group-item ${g.groupId === messageState.selectedGroup ? 'active' : ''}" data-gid="${escapeHtml(g.groupId)}" title="${escapeHtml(g.name)}">
            ${avatarImg(g.avatar, g.name, 'msg-group-avatar')}
            <span class="msg-group-info">
                <span class="msg-group-name">${escapeHtml(g.name)}</span>
                <span class="msg-group-sub ${g.lastError ? 'err' : ''}">${escapeHtml(g.lastError || formatPinTime(g.latest))}</span>
            </span>
            <span class="unread-badge">${g.count}</span>
        </button>
    `).join('');
    wrap.querySelectorAll('.msg-group-item').forEach(item => {
        item.addEventListener('click', () => {
            messageState.selectedGroup = item.dataset.gid || '';
            messageState.selectedItemId = '';
            renderAll();
        });
    });
}

function renderMessageList() {
    const wrap = qs('msgFeedList');
    const title = qs('msgFeedTitle');
    const meta = qs('msgFeedMeta');
    const clearBtn = qs('btnClearGroup');
    const gid = messageState.selectedGroup;
    const groupMeta = gid ? (messageState.groups[gid] || {}) : null;
    const items = currentItems();

    if (title) title.textContent = gid ? (groupMeta.name || gid) : 'Tất cả tin nhắn';
    if (meta) meta.textContent = `${items.length} tin` + (gid && groupMeta.lastSyncAt ? ` • quét lúc ${groupMeta.lastSyncAt}` : '');
    if (clearBtn) {
        clearBtn.hidden = !gid || !items.length;
        clearBtn.textContent = 'Đã đọc hết';
        delete clearBtn.dataset.confirming;
    }
    if (!wrap) return;
    if (!items.length) {
        wrap.innerHTML = '<div class="empty-state">Không có tin nhắn nào. Tin đã xóa sẽ không được lưu lại khi quét.</div>';
        return;
    }
    // Giữ tin đang chọn nếu còn; mặc định chọn tin đầu tiên.
    if (!items.some(x => x.id === messageState.selectedItemId)) {
        messageState.selectedItemId = items[0].id;
    }
    wrap.innerHTML = items.map(item => {
        const snippet = (item.title || '').trim();
        return `
        <button type="button" class="msg-feed-item ${item.id === messageState.selectedItemId ? 'active' : ''}" data-id="${escapeHtml(item.id)}">
            <span class="mf-top">
                <span class="mf-sender">${escapeHtml(item.senderName || 'Không rõ người gửi')}</span>
                <span class="mf-time">${escapeHtml(formatPinTime(item.createTime))}</span>
            </span>
            <span class="mf-snippet">${escapeHtml(snippet) || '<em>(Không có nội dung chữ)</em>'}</span>
            <span class="mf-tags">
                ${!gid ? `<span class="mf-group">${escapeHtml(item.groupName || '')}</span>` : ''}
                ${item.thumb ? '<span class="mf-flag">📷 Ảnh</span>' : ''}
                ${item.href ? '<span class="mf-flag">🔗 Link</span>' : ''}
            </span>
        </button>`;
    }).join('');
    wrap.querySelectorAll('.msg-feed-item').forEach(el => {
        el.addEventListener('click', () => {
            messageState.selectedItemId = el.dataset.id || '';
            wrap.querySelectorAll('.msg-feed-item').forEach(x => x.classList.toggle('active', x === el));
            renderDetail();
        });
    });
}

function renderDetail() {
    const empty = qs('msgDetailEmpty');
    const detail = qs('msgDetail');
    const body = qs('msgDetailBody');
    if (!empty || !detail || !body) return;
    const item = currentItems().find(x => x.id === messageState.selectedItemId);
    if (!item) {
        empty.hidden = false;
        detail.hidden = true;
        return;
    }
    empty.hidden = true;
    detail.hidden = false;
    qs('msgDetailSender').textContent = item.senderName || 'Không rõ người gửi';
    qs('msgDetailMeta').textContent = `${item.groupName || item.groupId} • ${formatPinTime(item.createTime)}`;
    body.innerHTML = `
        ${item.title ? `<div class="msg-detail-text">${linkifyText(item.title)}</div>` : ''}
        ${item.thumb ? `<a class="msg-item-photo-link" href="${escapeHtml(item.thumb)}" target="_blank" rel="noopener"><img class="msg-item-photo" src="${escapeHtml(item.thumb)}" alt="Ảnh đính kèm" loading="lazy" onerror="msgPhotoError(this)"></a>` : ''}
        ${item.href ? `<a class="msg-item-link" href="${escapeHtml(item.href)}" target="_blank" rel="noopener">${escapeHtml(item.href)}</a>` : ''}
        ${!item.title && !item.thumb && !item.href ? '<div class="msg-detail-text muted">(Tin không có nội dung hiển thị — có thể là bình chọn hoặc tệp đính kèm)</div>' : ''}
    `;
    body.scrollTop = 0;
}

async function deleteCurrentItem() {
    const items = currentItems();
    const idx = items.findIndex(x => x.id === messageState.selectedItemId);
    if (idx < 0) return;
    const btn = qs('btnDetailRead');
    if (btn) btn.disabled = true;
    try {
        await apiJson(`/api/messages/unread/${encodeURIComponent(messageState.selectedItemId)}`, { method: 'DELETE' });
        messageState.items = messageState.items.filter(x => x.id !== messageState.selectedItemId);
        const remain = currentItems();
        messageState.selectedItemId = remain.length ? remain[Math.min(idx, remain.length - 1)].id : '';
        renderAll();
    } catch (err) {
        showMessageStatus('Không xóa được tin: ' + err.message, 'error');
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function clearSelectedGroup() {
    const gid = messageState.selectedGroup;
    if (!gid) return;
    const btn = qs('btnClearGroup');
    if (!btn) return;
    // Xác nhận 2 bước ngay trên nút, không dùng dialog chặn trang.
    if (!btn.dataset.confirming) {
        btn.dataset.confirming = '1';
        btn.textContent = 'Bấm lần nữa để xóa hết';
        setTimeout(() => {
            if (btn.dataset.confirming) {
                delete btn.dataset.confirming;
                btn.textContent = 'Đã đọc hết';
            }
        }, 3500);
        return;
    }
    delete btn.dataset.confirming;
    btn.disabled = true;
    try {
        const accountParam = messageState.selectedAccount ? `?account_id=${encodeURIComponent(messageState.selectedAccount)}` : '';
        const data = await apiJson(`/api/messages/unread/group/${encodeURIComponent(gid)}${accountParam}`, { method: 'DELETE' });
        showMessageStatus(`Đã xóa ${data.removed || 0} tin của nhóm khỏi danh sách.`, 'success');
        messageState.selectedItemId = '';
        await loadUnread();
    } catch (err) {
        showMessageStatus('Không xóa được tin của nhóm: ' + err.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Đã đọc hết';
    }
}

// ─── Khởi động ───────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', async () => {
    await loadAccounts();
    await loadSettings();
    await loadUnread();

    qs('btnReloadMessages')?.addEventListener('click', loadUnread);
    qs('btnCheckMessages')?.addEventListener('click', checkMessages);
    qs('btnSaveMessageSettings')?.addEventListener('click', saveSettings);
    qs('btnClearGroup')?.addEventListener('click', clearSelectedGroup);
    qs('btnDetailRead')?.addEventListener('click', deleteCurrentItem);
    qs('accountComboBtn')?.addEventListener('click', toggleAccountMenu);
    document.addEventListener('click', (e) => {
        if (!e.target.closest('.account-combo')) closeAccountMenu();
    });

    // Tự làm mới hiển thị từ db (worker server quét Zalo theo chu kỳ đã lưu).
    setInterval(() => {
        if (!document.hidden) loadUnread();
    }, UI_REFRESH_MS);
});
