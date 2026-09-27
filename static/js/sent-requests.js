(function () {
    'use strict';

    var accounts = [];
    var requests = [];
    var selected = new Set();
    var loading = false;
    var requestSeq = 0;
    var statusTimer = null;

    function $(id) { return document.getElementById(id); }

    function esc(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function normalizeAvatar(url) {
        url = String(url || '').trim();
        if (!url || url === 'null' || url === 'undefined') return '';
        if (url.startsWith('//')) return 'https:' + url;
        return url;
    }

    function accountId(acc) {
        return String((acc && (acc.accountId || acc.id || acc.account_id)) || '').trim();
    }

    function accountName(acc) {
        return (acc && (acc.name || acc.zaloName || acc.displayName || acc.phone)) || accountId(acc) || 'Không tên';
    }

    function selectedAccountId() {
        return String(($('sentReqAccountId') || {}).value || '').trim();
    }

    // ─── Combobox tài khoản (avatar + tên) ─────────────────────────────
    function renderAccountDropdown() {
        var container = $('sentReqAccountDropdown');
        if (!container) return;
        var sel = accounts.find(function (a) { return accountId(a) === selectedAccountId(); }) || null;

        var btnHtml;
        if (!sel) {
            btnHtml = '<button type="button" class="account-dropdown-button"><span>Chọn tài khoản</span></button>';
        } else {
            var av = normalizeAvatar(sel.avatarUrl || sel.avatar || '');
            btnHtml = '<button type="button" class="account-dropdown-button" title="' + esc(accountId(sel)) + '">'
                + (av ? '<img src="' + esc(av) + '" class="account-dropdown-avatar" alt="">' : '<span class="account-dropdown-avatar placeholder"></span>')
                + '<span class="account-dropdown-name">' + esc(accountName(sel)) + '</span></button>';
        }

        var menuHtml = '<div class="account-dropdown-menu hidden">' + accounts.map(function (a) {
            var av = normalizeAvatar(a.avatarUrl || a.avatar || '');
            var id = accountId(a);
            var active = (sel && accountId(sel) === id) ? ' active' : '';
            return '<button type="button" class="account-dropdown-item' + active + '" data-account-id="' + esc(id) + '">'
                + (av ? '<img src="' + esc(av) + '" class="account-dropdown-avatar" alt="">' : '<span class="account-dropdown-avatar placeholder"></span>')
                + '<span><b>' + esc(accountName(a)) + '</b><br><small>' + esc(id) + '</small></span></button>';
        }).join('') + '</div>';

        container.innerHTML = btnHtml + menuHtml;

        var btn = container.querySelector('.account-dropdown-button');
        var menu = container.querySelector('.account-dropdown-menu');
        if (btn && menu) {
            btn.addEventListener('click', function (e) {
                e.stopPropagation();
                menu.classList.toggle('hidden');
            });
        }
        container.querySelectorAll('.account-dropdown-item').forEach(function (item) {
            item.addEventListener('click', function (e) {
                e.stopPropagation();
                var aid = String(this.dataset.accountId || '').trim();
                if (!aid || aid === selectedAccountId()) {
                    if (menu) menu.classList.add('hidden');
                    return;
                }
                if ($('sentReqAccountId')) $('sentReqAccountId').value = aid;
                try { localStorage.setItem('nexus_sentreq_account_id', aid); } catch (err) {}
                renderAccountDropdown();
                loadRequests();
            });
        });
    }

    document.addEventListener('click', function () {
        var menu = document.querySelector('#sentReqAccountDropdown .account-dropdown-menu');
        if (menu) menu.classList.add('hidden');
    });

    async function loadAccounts() {
        try {
            var res = await fetch('/api/accounts');
            var data = await res.json();
            accounts = data.accounts || [];
        } catch (err) {
            accounts = [];
        }
        var saved = '';
        try { saved = localStorage.getItem('nexus_sentreq_account_id') || ''; } catch (err) {}
        var valid = accounts.some(function (a) { return accountId(a) === saved; });
        var first = accounts.length ? accountId(accounts[0]) : '';
        var chosen = valid ? saved : first;
        if ($('sentReqAccountId')) $('sentReqAccountId').value = chosen;
        renderAccountDropdown();
        if (chosen) loadRequests();
        else setEmptyHint('Chưa có tài khoản nào. Hãy thêm tài khoản ở trang Tài khoản trước.');
    }

    function setEmptyHint(text) {
        var empty = $('sentReqEmpty');
        var hint = $('sentReqEmptyHint');
        if (hint) hint.textContent = text;
        if (empty) empty.style.display = 'flex';
    }

    function formatTime(sec) {
        sec = parseInt(sec, 10);
        if (!sec) return '-';
        try {
            var d = new Date(sec * 1000);
            var pad = function (n) { return String(n).padStart(2, '0'); };
            return pad(d.getDate()) + '/' + pad(d.getMonth() + 1) + '/' + d.getFullYear() + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
        } catch (e) {
            return '-';
        }
    }

    async function loadRequests() {
        var aid = selectedAccountId();
        if (!aid) return;
        var seq = ++requestSeq;
        loading = true;
        requests = [];
        selected.clear();
        renderTable();
        if ($('sentReqLoading')) $('sentReqLoading').style.display = 'block';
        if ($('sentReqEmpty')) $('sentReqEmpty').style.display = 'none';
        try {
            var res = await fetch('/api/friend-requests/sent?accountId=' + encodeURIComponent(aid));
            var data = await res.json();
            if (seq !== requestSeq) return;
            if (!data.success) throw new Error(data.error || 'Không lấy được danh sách lời mời đã gửi');
            requests = data.requests || [];
        } catch (err) {
            if (seq !== requestSeq) return;
            requests = [];
            showStatus(err.message || String(err), false);
        } finally {
            if (seq === requestSeq) {
                loading = false;
                if ($('sentReqLoading')) $('sentReqLoading').style.display = 'none';
                renderTable();
            }
        }
    }

    function visibleList() {
        var q = String(($('sentReqSearch') || {}).value || '').toLowerCase().trim();
        if (!q) return requests.slice();
        return requests.filter(function (r) {
            return String(r.zaloName || '').toLowerCase().indexOf(q) >= 0
                || String(r.displayName || '').toLowerCase().indexOf(q) >= 0
                || String(r.userId || '').indexOf(q) >= 0;
        });
    }

    function renderTable() {
        var body = $('sentReqBody');
        if (!body) return;
        var empty = $('sentReqEmpty');
        var list = visibleList();

        if ($('sentReqTotal')) $('sentReqTotal').textContent = requests.length;

        if (!list.length) {
            body.innerHTML = '';
            if (empty && !loading) {
                if (requests.length) setEmptyHint('Không có lời mời nào khớp từ khóa.');
                else setEmptyHint('Chọn tài khoản ở trên rồi bấm "Tải lại".');
                empty.style.display = 'flex';
            }
        } else {
            if (empty) empty.style.display = 'none';
            body.innerHTML = list.map(function (r) {
                var uid = String(r.userId || '').trim();
                var name = r.zaloName || r.displayName || 'Không tên';
                var av = normalizeAvatar(r.avatar);
                var avatarHtml = av
                    ? '<img class="sentreq-avatar" src="' + esc(av) + '" alt="" onerror="this.outerHTML=\'<div class=&quot;sentreq-avatar fallback&quot;>' + esc((name.charAt(0) || '?').toUpperCase()) + '</div>\'">'
                    : '<div class="sentreq-avatar fallback">' + esc((name.charAt(0) || '?').toUpperCase()) + '</div>';
                var msg = String(r.message || '').trim();
                var chk = selected.has(uid) ? 'checked' : '';
                return '<tr data-uid="' + esc(uid) + '">'
                    + '<td><input type="checkbox" data-check="' + esc(uid) + '" ' + chk + '></td>'
                    + '<td>' + avatarHtml + '</td>'
                    + '<td title="' + esc(name) + '"><b>' + esc(name) + '</b></td>'
                    + '<td title="' + esc(msg) + '">' + (msg ? esc(msg) : '<span class="sentreq-muted">-</span>') + '</td>'
                    + '<td class="sentreq-muted">' + esc(formatTime(r.sentAt)) + '</td>'
                    + '<td class="sentreq-muted" style="font-family:monospace">' + esc(uid) + '</td>'
                    + '</tr>';
            }).join('');
        }

        if ($('sentReqShown')) $('sentReqShown').textContent = (requests.length && list.length !== requests.length) ? '· hiển thị ' + list.length : '';
        if ($('sentReqSelectedCount')) $('sentReqSelectedCount').textContent = selected.size;

        var visibleSelected = list.filter(function (r) { return selected.has(String(r.userId || '').trim()); }).length;
        var all = list.length > 0 && visibleSelected === list.length;
        var partial = visibleSelected > 0 && visibleSelected < list.length;
        var head = $('sentReqHeaderCheckbox');
        if (head) { head.checked = all; head.indeterminate = partial; }
        var selAll = $('sentReqSelectAll');
        if (selAll) { selAll.checked = all; selAll.indeterminate = partial; }
        var desel = $('sentReqDeselectAll');
        if (desel) desel.checked = false;

        var undoBtn = $('sentReqUndoBtn');
        if (undoBtn) undoBtn.disabled = selected.size === 0;
    }

    function toggle(uid) {
        uid = String(uid || '').trim();
        if (!uid) return;
        if (selected.has(uid)) selected.delete(uid); else selected.add(uid);
        renderTable();
    }

    function toggleSelectAll(cb) {
        var list = visibleList();
        if (cb && cb.checked === false) {
            list.forEach(function (r) { selected.delete(String(r.userId || '').trim()); });
        } else {
            list.forEach(function (r) { var uid = String(r.userId || '').trim(); if (uid) selected.add(uid); });
        }
        renderTable();
    }

    function toggleDeselectAll(cb) {
        selected.clear();
        renderTable();
        if (cb) cb.checked = false;
    }

    async function undo() {
        var aid = selectedAccountId();
        if (!aid) { showStatus('Chưa chọn tài khoản.', false); return; }
        var ids = [];
        selected.forEach(function (x) { ids.push(x); });
        if (!ids.length) { showStatus('Chưa chọn lời mời nào để thu hồi.', false); return; }

        var confirmed = window.nexusConfirm
            ? await window.nexusConfirm('Thu hồi ' + ids.length + ' lời mời kết bạn đã chọn?', { title: 'Thu hồi lời mời', confirmText: 'Thu hồi', danger: true })
            : window.confirm('Thu hồi ' + ids.length + ' lời mời kết bạn đã chọn?');
        if (!confirmed) return;

        var btn = $('sentReqUndoBtn');
        var prev = btn ? btn.innerHTML : '';
        if (btn) { btn.disabled = true; btn.innerHTML = 'Đang thu hồi...'; }
        try {
            var res = await fetch('/api/friend-requests/undo', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ accountId: aid, userIds: ids })
            });
            var data = await res.json();
            if (!data.success) throw new Error(data.error || 'Thu hồi thất bại');
            var okIds = {};
            (data.results || []).forEach(function (item) { if (item && item.success) okIds[String(item.userId)] = 1; });
            requests = requests.filter(function (r) { return !okIds[String(r.userId || '').trim()]; });
            selected.clear();
            renderTable();
            var failed = data.failed || 0;
            showStatus('Đã thu hồi ' + (data.revoked || 0) + ' lời mời' + (failed ? ', lỗi ' + failed + ' lời mời.' : '.'), failed === 0);
        } catch (err) {
            showStatus('Thu hồi lỗi: ' + (err.message || err), false);
        } finally {
            if (btn) { btn.disabled = false; btn.innerHTML = prev; }
        }
    }

    function showStatus(text, ok) {
        var el = $('sentReqStatus');
        if (!el) return;
        el.textContent = text;
        el.className = ok ? 'ok' : 'err';
        clearTimeout(statusTimer);
        statusTimer = setTimeout(function () { el.className = ''; }, 4500);
    }

    function reload() {
        loadRequests();
    }

    function bindRowEvents() {
        var body = $('sentReqBody');
        if (!body) return;
        body.addEventListener('click', function (e) {
            var cb = e.target.closest('[data-check]');
            if (cb) { e.stopPropagation(); toggle(cb.getAttribute('data-check')); return; }
            var row = e.target.closest('tr[data-uid]');
            if (row && !e.target.closest('input')) toggle(row.getAttribute('data-uid'));
        });
    }

    window.sentReqReload = reload;
    window.sentReqRenderTable = renderTable;
    window.sentReqToggleSelectAll = toggleSelectAll;
    window.sentReqToggleDeselectAll = toggleDeselectAll;
    window.sentReqUndo = undo;

    function boot() {
        bindRowEvents();
        loadAccounts();
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();
