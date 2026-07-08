let messageState = {
    conversations: [],
    selectedId: null,
    accounts: [],
    settings: {},
};

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

function avatarUrl(conv) {
    if (conv && conv.peer_avatar) return conv.peer_avatar;
    return 'data:image/svg+xml;utf8,' + encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="80" height="80"><rect width="100%" height="100%" rx="18" fill="#1c2128"/><text x="50%" y="54%" text-anchor="middle" font-size="28" font-family="Arial" fill="#58a6ff">${escapeHtml((conv?.peer_name || 'Z').slice(0,1).toUpperCase())}</text></svg>`);
}

function statusLabel(status) {
    return {
        new: 'Tin mới',
        waiting: 'Cần phản hồi',
        replied: 'Đã phản hồi',
        closed: 'Đã đóng',
    }[status] || 'Tin mới';
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

async function loadMessageAccounts() {
    try {
        const data = await apiJson('/api/accounts');
        messageState.accounts = data.accounts || [];
        const sel = qs('msgAccountSelect');
        if (!sel) return;
        const current = sel.value;
        sel.innerHTML = '<option value="">Tất cả tài khoản</option>' + messageState.accounts.map(acc => {
            const accountId = acc.accountId || acc.id || '';
            const label = acc.name || acc.account_name || accountId || 'Tài khoản';
            return `<option value="${escapeHtml(accountId)}">${escapeHtml(label)}</option>`;
        }).join('');
        if (current) sel.value = current;
    } catch (err) {
        console.warn('Không tải được tài khoản:', err);
    }
}

async function loadMessageSettings() {
    try {
        const data = await apiJson('/api/messages/settings');
        messageState.settings = data.settings || {};
        qs('autoCheckEnabled').checked = !!messageState.settings.auto_check_enabled;
        qs('globalAutoReplyEnabled').checked = !!messageState.settings.auto_reply_enabled;
        qs('checkInterval').value = String(messageState.settings.check_interval_minutes || 5);
        qs('defaultReply').value = messageState.settings.default_reply || '';
        qs('msgLastCheck').textContent = messageState.settings.last_check_at
            ? `Lần kiểm tra: ${messageState.settings.last_check_at}`
            : (messageState.settings.last_check_message || 'Chưa đồng bộ');
    } catch (err) {
        showMessageStatus('Không tải được thiết lập tin nhắn: ' + err.message, 'error');
    }
}

async function loadConversations() {
    const params = new URLSearchParams();
    const accountId = qs('msgAccountSelect')?.value || '';
    const status = qs('msgStatusFilter')?.value || 'all';
    const q = qs('msgSearchInput')?.value || '';
    if (accountId) params.set('account_id', accountId);
    if (status) params.set('status', status);
    if (q) params.set('q', q);
    try {
        const data = await apiJson('/api/messages/conversations?' + params.toString());
        messageState.conversations = data.conversations || [];
        renderStats(data.stats || {});
        renderConversationList();
        if (messageState.selectedId) {
            const stillExists = messageState.conversations.some(x => x.id === messageState.selectedId);
            if (!stillExists) clearThread();
        }
    } catch (err) {
        showMessageStatus('Không tải được hội thoại: ' + err.message, 'error');
    }
}

function renderStats(stats) {
    qs('msgStatTotal').textContent = stats.total || 0;
    qs('msgStatUnread').textContent = stats.unread || 0;
    qs('msgStatWaiting').textContent = stats.waiting || 0;
    qs('msgStatReplied').textContent = stats.replied || 0;
}

function renderConversationList() {
    const wrap = qs('conversationList');
    const count = qs('msgListCount');
    if (count) count.textContent = `${messageState.conversations.length} hội thoại`;
    if (!wrap) return;
    if (!messageState.conversations.length) {
        wrap.innerHTML = '<div class="empty-state">Chưa có hội thoại nào được lưu.</div>';
        return;
    }
    wrap.innerHTML = messageState.conversations.map(conv => {
        const unread = Number(conv.unread_count || 0);
        return `<div class="conversation-item ${conv.id === messageState.selectedId ? 'active' : ''}" data-id="${escapeHtml(conv.id)}">
            <img class="conv-avatar" src="${avatarUrl(conv)}" alt="">
            <div class="conv-body">
                <div class="conv-top">
                    <div class="conv-name">${escapeHtml(conv.peer_name || conv.peer_id || 'Không tên')}</div>
                    <div class="conv-time">${escapeHtml(conv.last_message_time || '')}</div>
                </div>
                <div class="conv-message">${escapeHtml(conv.last_message || 'Chưa có nội dung')}</div>
                <div class="conv-footer">
                    <span class="status-chip ${escapeHtml(conv.status || 'new')}">${statusLabel(conv.status)}</span>
                    ${(conv.tags || []).slice(0, 2).map(tag => `<span class="status-chip">#${escapeHtml(tag)}</span>`).join('')}
                    ${unread ? `<span class="unread-badge">${unread}</span>` : ''}
                </div>
            </div>
        </div>`;
    }).join('');
    wrap.querySelectorAll('.conversation-item').forEach(item => {
        item.addEventListener('click', () => selectConversation(item.dataset.id));
    });
}

function clearThread() {
    messageState.selectedId = null;
    qs('threadEmpty').style.display = 'block';
    qs('threadDetail').style.display = 'none';
    renderConversationList();
}

async function selectConversation(id) {
    messageState.selectedId = id;
    renderConversationList();
    try {
        const data = await apiJson(`/api/messages/conversations/${encodeURIComponent(id)}`);
        renderThread(data.conversation);
    } catch (err) {
        showMessageStatus('Không mở được hội thoại: ' + err.message, 'error');
    }
}

function renderThread(conv) {
    if (!conv) return clearThread();
    qs('threadEmpty').style.display = 'none';
    qs('threadDetail').style.display = 'block';
    qs('threadAvatar').src = avatarUrl(conv);
    qs('threadName').textContent = conv.peer_name || conv.peer_id || 'Người nhắn';
    qs('threadMeta').textContent = `UID: ${conv.peer_id || ''} • Tài khoản: ${conv.account_id || ''}`;
    qs('threadStatusSelect').value = conv.status || 'new';

    const thread = qs('messageThread');
    const messages = conv.messages || [];
    if (!messages.length) {
        thread.innerHTML = '<div class="empty-state">Hội thoại chưa có tin nhắn.</div>';
    } else {
        thread.innerHTML = messages.map(msg => `<div class="bubble-row ${msg.direction === 'out' ? 'out' : 'in'}">
            <div class="message-bubble">
                <div class="text">${escapeHtml(msg.text || '')}</div>
                <div class="time">${escapeHtml(msg.created_at || '')}</div>
            </div>
        </div>`).join('');
    }
    thread.scrollTop = thread.scrollHeight;
}

async function updateSelectedConversation(patch) {
    if (!messageState.selectedId) return;
    try {
        const data = await apiJson(`/api/messages/conversations/${encodeURIComponent(messageState.selectedId)}`, {
            method: 'PATCH',
            body: JSON.stringify(patch),
        });
        renderThread(data.conversation);
        await loadConversations();
    } catch (err) {
        showMessageStatus('Không cập nhật được hội thoại: ' + err.message, 'error');
    }
}

async function saveReply() {
    if (!messageState.selectedId) return showMessageStatus('Hãy chọn một hội thoại trước.', 'warning');
    const text = qs('replyText').value.trim();
    if (!text) return showMessageStatus('Nội dung phản hồi đang trống.', 'warning');
    try {
        const data = await apiJson(`/api/messages/conversations/${encodeURIComponent(messageState.selectedId)}/messages`, {
            method: 'POST',
            body: JSON.stringify({ text, direction: 'out' }),
        });
        qs('replyText').value = '';
        renderThread(data.conversation);
        await loadConversations();
        showMessageStatus('Đã lưu phản hồi vào lịch sử hội thoại. Chưa tự gửi Zalo.', 'success');
    } catch (err) {
        showMessageStatus('Không lưu được phản hồi: ' + err.message, 'error');
    }
}

async function saveSettings() {
    try {
        const payload = {
            auto_check_enabled: qs('autoCheckEnabled').checked,
            auto_reply_enabled: qs('globalAutoReplyEnabled').checked,
            check_interval_minutes: Number(qs('checkInterval').value || 5),
            default_reply: qs('defaultReply').value || '',
        };
        const data = await apiJson('/api/messages/settings', {
            method: 'PATCH',
            body: JSON.stringify(payload),
        });
        messageState.settings = data.settings || {};
        showMessageStatus('Đã lưu thiết lập tin nhắn.', 'success');
    } catch (err) {
        showMessageStatus('Không lưu được thiết lập: ' + err.message, 'error');
    }
}

async function checkMessages() {
    const accountId = qs('msgAccountSelect')?.value || '';
    try {
        const data = await apiJson('/api/messages/check', {
            method: 'POST',
            body: JSON.stringify({ account_id: accountId }),
        });
        showMessageStatus(data.message || 'Đã ghi nhận yêu cầu kiểm tra tin nhắn.', 'info');
        await loadMessageSettings();
        await loadConversations();
    } catch (err) {
        showMessageStatus('Không kiểm tra được tin nhắn: ' + err.message, 'error');
    }
}

async function addManualConversation() {
    const firstAccount = messageState.accounts[0] || {};
    const accountId = qs('msgAccountSelect')?.value || firstAccount.accountId || firstAccount.id || 'demo-account';
    const stamp = Date.now().toString().slice(-6);
    const peerId = 'demo-user-' + stamp;
    const peerName = 'Khách mẫu ' + stamp;
    const firstMessage = 'Khách vừa nhắn cần tư vấn';
    try {
        const data = await apiJson('/api/messages/conversations', {
            method: 'POST',
            body: JSON.stringify({ account_id: accountId, peer_id: peerId, peer_name: peerName, first_message: firstMessage }),
        });
        await loadConversations();
        await selectConversation(data.conversation.id);
        showMessageStatus('Đã tạo hội thoại mẫu để test giao diện.', 'success');
    } catch (err) {
        showMessageStatus('Không tạo được hội thoại mẫu: ' + err.message, 'error');
    }
}

function debounce(fn, delay = 300) {
    let timer = null;
    return function (...args) {
        clearTimeout(timer);
        timer = setTimeout(() => fn.apply(this, args), delay);
    };
}

document.addEventListener('DOMContentLoaded', async () => {
    await loadMessageAccounts();
    await loadMessageSettings();
    await loadConversations();

    qs('btnReloadMessages')?.addEventListener('click', loadConversations);
    qs('btnCheckMessages')?.addEventListener('click', checkMessages);
    qs('btnSaveMessageSettings')?.addEventListener('click', saveSettings);
    qs('btnSaveReply')?.addEventListener('click', saveReply);
    qs('btnInsertDefaultReply')?.addEventListener('click', () => {
        qs('replyText').value = qs('defaultReply').value || '';
        qs('replyText').focus();
    });
    qs('btnMarkRead')?.addEventListener('click', () => updateSelectedConversation({ unread_count: 0 }));
    qs('threadStatusSelect')?.addEventListener('change', (e) => updateSelectedConversation({ status: e.target.value }));
    qs('btnAddManualConversation')?.addEventListener('click', addManualConversation);

    qs('msgAccountSelect')?.addEventListener('change', loadConversations);
    qs('msgStatusFilter')?.addEventListener('change', loadConversations);
    qs('msgSearchInput')?.addEventListener('input', debounce(loadConversations, 280));
});
