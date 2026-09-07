// Quản lý danh sách tài khoản Zalo

var _accountsPollTimer = null;
var _pollEndTime = 0;
var _bulkActionRunning = false;

function setAccountsStatus(msg, type) {
    var el = document.getElementById('accountsStatus');
    if (!el) return;
    el.textContent = msg || '';
    el.className = 'status-bar ' + (type || '');
    el.style.display = msg ? 'block' : 'none';
}

function accountStatusLabel(acc) {
    // Nếu có remoteDebugPort thì đang chạy
    if (acc.remoteDebugPort) {
        return { text: 'Đang chạy', cls: 'running' };
    }
    return { text: 'Chưa chạy', cls: 'wait' };
}

function accountReady(acc) {
    return !!(window.NexusSession && NexusSession.accountReady(acc, false) && acc.userinfoCaptured);
}

function renderAccountsStats(accounts) {
    var total = document.getElementById('accStatTotal');
    var running = document.getElementById('accStatRunning');
    var ready = document.getElementById('accStatReady');
    if (total) total.textContent = accounts.length;
    if (running) running.textContent = accounts.filter(function (a) { return !!a.remoteDebugPort; }).length;
    if (ready) ready.textContent = accounts.filter(accountReady).length;
}

function startAccountsPolling(durationSec) {
    if (_accountsPollTimer) return;

    // Polling trong khoảng thời gian định (mặc định 10 giây)
    var duration = (durationSec || 10) * 1000;
    _pollEndTime = Date.now() + duration;

    _accountsPollTimer = setInterval(function() {
        if (Date.now() > _pollEndTime) {
            stopAccountsPolling();
            return;
        }
        loadAccountsList(true);
    }, 2000); // Poll mỗi 2 giây
}

function stopAccountsPolling() {
    if (_accountsPollTimer) {
        clearInterval(_accountsPollTimer);
        _accountsPollTimer = null;
        _pollEndTime = 0;
    }
}

// ─── Heartbeat: tự đồng bộ trạng thái nền, phát hiện khi người dùng đóng
// cửa sổ Chrome trực tiếp (không qua nút trong app) mà không cần tải lại trang.
var _accountsHeartbeatTimer = null;

function startAccountsHeartbeat() {
    if (_accountsHeartbeatTimer) return;
    _accountsHeartbeatTimer = setInterval(function () {
        if (document.hidden) return;
        loadAccountsList(true);
    }, 5000);
}

function renderAccountCard(acc) {
    var card = document.createElement('div');
    card.className = 'account-card';
    card.dataset.accountId = acc.accountId;

    var avatarWrap = document.createElement('div');
    avatarWrap.className = 'account-card-avatar';
    var avtUrl = (acc.avatarUrl || '').trim();
    if (avtUrl) {
        var img = document.createElement('img');
        img.src = avtUrl.startsWith('//') ? 'https:' + avtUrl : avtUrl;
        img.alt = acc.name || 'Avatar';
        img.onerror = function() {
            this.style.display = 'none';
            avatarWrap.appendChild(defaultAvatarEl(acc.name));
        };
        avatarWrap.appendChild(img);
    } else {
        avatarWrap.appendChild(defaultAvatarEl(acc.name));
    }

    var st = accountStatusLabel(acc);

    var nameCell = document.createElement('div');
    nameCell.className = 'account-card-name-cell';
    nameCell.innerHTML =
        '<div class="account-card-name">' + escHtml(acc.name || 'Tài khoản') + '</div>' +
        '<div class="account-card-id mono">' + escHtml(acc.accountId) + '</div>';

    var statusCell = document.createElement('div');
    statusCell.className = 'account-card-status-cell';
    statusCell.innerHTML = '<span class="account-card-status ' + st.cls + '">' + escHtml(st.text) + '</span>';

    var proxyCell = document.createElement('div');
    proxyCell.className = 'account-card-proxy-cell mono';
    proxyCell.textContent = acc.proxy ? ('Proxy: ' + acc.proxy.split('@').pop()) : 'Chưa cấu hình proxy';

    var actions = document.createElement('div');
    actions.className = 'account-card-actions';

    var btnOpen = document.createElement('button');
    btnOpen.className = 'btn btn-primary btn-sm';

    // Nếu đang chạy, hiển thị "Dừng", ngược lại "Khởi chạy"
    if (acc.remoteDebugPort) {
        btnOpen.textContent = 'Dừng';
        btnOpen.onclick = function() { closeAccount(acc.accountId); };
    } else {
        btnOpen.textContent = 'Khởi chạy';
        btnOpen.onclick = function() { openAccount(acc.accountId); };
    }

    var btnEdit = document.createElement('button');
    btnEdit.className = 'btn btn-ghost btn-sm';
    btnEdit.textContent = 'Sửa';
    btnEdit.onclick = function() { editAccount(acc.accountId, acc.name, acc.proxy || '', !!acc.remoteDebugPort); };

    var btnDelete = document.createElement('button');
    btnDelete.className = 'btn btn-danger btn-sm';
    btnDelete.textContent = 'Xóa';
    btnDelete.onclick = function() { deleteAccount(acc.accountId, acc.name); };

    actions.appendChild(btnOpen);
    actions.appendChild(btnEdit);
    actions.appendChild(btnDelete);

    card.appendChild(avatarWrap);
    card.appendChild(nameCell);
    card.appendChild(statusCell);
    card.appendChild(proxyCell);
    card.appendChild(actions);
    return card;
}

function defaultAvatarEl(name) {
    var el = document.createElement('div');
    el.className = 'account-avatar-default';
    var letter = (name || '?').trim().charAt(0).toUpperCase() || '?';
    el.textContent = letter;
    return el;
}

var _lastAccountsSnapshot = [];

// Giới hạn số tài khoản theo gói: đủ số lượng thì khóa nút "Thêm tài khoản".
var _accountPlan = null;

async function loadAccountPlan() {
    try {
        _accountPlan = await fetch('/api/license/plan').then(function (r) { return r.json(); });
    } catch (e) {
        _accountPlan = null;
    }
    return _accountPlan;
}

function updateAddAccountLimit(count) {
    var btn = document.getElementById('btnAddAccount');
    if (!btn) return;
    var max = _accountPlan ? Number(_accountPlan.maxAccounts || 0) : 0;
    var hintEl = document.getElementById('accountLimitHint');
    // maxAccounts = 0 nghĩa không giới hạn.
    if (max > 0 && count >= max) {
        btn.disabled = true;
        btn.classList.add('is-disabled');
        btn.title = 'Gói hiện tại chỉ cho phép tối đa ' + max + ' tài khoản Zalo. Nâng lên gói 6 tháng trở lên để không giới hạn.';
        if (hintEl) {
            hintEl.textContent = 'Đã đạt giới hạn ' + max + ' tài khoản của gói hiện tại. Nâng gói 6 tháng trở lên để thêm tài khoản.';
            hintEl.style.display = 'block';
        }
    } else {
        btn.disabled = false;
        btn.classList.remove('is-disabled');
        btn.title = '';
        if (hintEl) hintEl.style.display = 'none';
    }
}

async function loadAccountsList(silent) {
    var listEl = document.getElementById('accountsList');
    var emptyEl = document.getElementById('accountsEmpty');
    if (!listEl) return;

    try {
        var resp = await fetch('/api/accounts');
        var json = await resp.json();
        if (json.error) {
            if (!silent) setAccountsStatus(json.error, 'error');
            return;
        }

        var accounts = json.accounts || [];
        _lastAccountsSnapshot = accounts;
        listEl.innerHTML = '';
        renderAccountsStats(accounts);
        updateAddAccountLimit(accounts.length);

        if (accounts.length === 0) {
            if (emptyEl) emptyEl.style.display = 'block';
            stopAccountsPolling();
            return;
        }

        if (emptyEl) emptyEl.style.display = 'none';

        accounts.forEach(function(acc) {
            listEl.appendChild(renderAccountCard(acc));
        });

        // Nếu tất cả tài khoản đều hoàn thành, dừng polling
        var allComplete = accounts.every(function(a) {
            return accountReady(a) && !!a.avatarUrl;
        });
        if (allComplete && _accountsPollTimer) {
            stopAccountsPolling();
        }
    } catch (err) {
        if (!silent) setAccountsStatus('Lỗi tải danh sách: ' + err.message, 'error');
    }
}

// ─── Thêm tài khoản: setup tên + proxy trước, không mở Chrome ngay ─────────

function openNewAccountModal() {
    // Chặn mở form khi đã đủ số tài khoản theo gói.
    var max = _accountPlan ? Number(_accountPlan.maxAccounts || 0) : 0;
    if (max > 0 && (_lastAccountsSnapshot || []).length >= max) {
        setAccountsStatus('Gói hiện tại chỉ cho phép tối đa ' + max + ' tài khoản Zalo. Nâng lên gói 6 tháng trở lên để không giới hạn.', 'error');
        return;
    }
    var backdrop = document.getElementById('newAccountModalBackdrop');
    var nameInput = document.getElementById('newAccountName');
    var proxyInput = document.getElementById('newAccountProxy');
    if (nameInput) nameInput.value = '';
    if (proxyInput) proxyInput.value = '';
    backdrop.classList.add('show');
    setTimeout(function() { if (nameInput) nameInput.focus(); }, 50);
}

function closeNewAccountModal() {
    var backdrop = document.getElementById('newAccountModalBackdrop');
    if (backdrop) backdrop.classList.remove('show');
}

async function submitNewAccount() {
    var name = (document.getElementById('newAccountName').value || '').trim();
    var proxy = (document.getElementById('newAccountProxy').value || '').trim();
    var btn = document.getElementById('btnSaveNewAccount');

    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span> Đang tạo...';
    }
    setAccountsStatus('Đang tạo tài khoản...', 'loading');

    try {
        var resp = await fetch('/api/accounts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name, proxy: proxy, autoLaunch: false })
        });
        var json = await resp.json();
        if (json.error) {
            setAccountsStatus(json.error, 'error');
            return;
        }
        setAccountsStatus(
            '✓ Đã tạo ' + (json.account && json.account.name) + '. Bấm «Khởi chạy» trên thẻ tài khoản khi bạn sẵn sàng đăng nhập.',
            'success'
        );
        closeNewAccountModal();
        await loadAccountsList();
    } catch (err) {
        setAccountsStatus('Lỗi: ' + err.message, 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = 'Tạo tài khoản';
        }
    }
}

// ─── Chạy tất cả / Dừng tất cả ──────────────────────────────────────────────

function _delay(ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); }

async function startAllAccounts() {
    if (_bulkActionRunning) return;
    var targets = (_lastAccountsSnapshot || []).filter(function (a) { return !a.remoteDebugPort; });
    if (!targets.length) {
        setAccountsStatus('Không có tài khoản nào đang chờ khởi chạy.', 'success');
        return;
    }
    _bulkActionRunning = true;
    var btn = document.getElementById('btnStartAll');
    if (btn) btn.disabled = true;
    try {
        for (var i = 0; i < targets.length; i++) {
            setAccountsStatus('Đang khởi chạy ' + (i + 1) + '/' + targets.length + ': ' + (targets[i].name || targets[i].accountId) + '...', 'loading');
            try {
                await fetch('/api/accounts/' + encodeURIComponent(targets[i].accountId) + '/open', { method: 'POST' });
            } catch (err) { /* tiếp tục với tài khoản kế tiếp */ }
            await loadAccountsList(true);
            if (i < targets.length - 1) await _delay(700);
        }
        setAccountsStatus('✓ Đã khởi chạy ' + targets.length + ' tài khoản.', 'success');
        startAccountsPolling(60);
    } finally {
        _bulkActionRunning = false;
        if (btn) btn.disabled = false;
        await loadAccountsList();
    }
}

async function stopAllAccounts() {
    if (_bulkActionRunning) return;
    var targets = (_lastAccountsSnapshot || []).filter(function (a) { return !!a.remoteDebugPort; });
    if (!targets.length) {
        setAccountsStatus('Không có tài khoản nào đang chạy.', 'success');
        return;
    }
    if (!(await nexusConfirm('Dừng tất cả ' + targets.length + ' tài khoản đang chạy?', { title: 'Dừng tất cả' }))) return;
    _bulkActionRunning = true;
    var btn = document.getElementById('btnStopAll');
    if (btn) btn.disabled = true;
    try {
        for (var i = 0; i < targets.length; i++) {
            setAccountsStatus('Đang dừng ' + (i + 1) + '/' + targets.length + ': ' + (targets[i].name || targets[i].accountId) + '...', 'loading');
            try {
                await fetch('/api/accounts/' + encodeURIComponent(targets[i].accountId) + '/close', { method: 'POST' });
            } catch (err) { /* tiếp tục với tài khoản kế tiếp */ }
            await loadAccountsList(true);
            if (i < targets.length - 1) await _delay(400);
        }
        setAccountsStatus('✓ Đã dừng ' + targets.length + ' tài khoản.', 'success');
    } finally {
        _bulkActionRunning = false;
        if (btn) btn.disabled = false;
        await loadAccountsList();
    }
}

async function openAccount(accountId) {
    setAccountsStatus('Đang khởi chạy...', 'loading');
    try {
        var resp = await fetch('/api/accounts/' + encodeURIComponent(accountId) + '/open', {
            method: 'POST'
        });
        var json = await resp.json();
        if (json.error) {
            setAccountsStatus(json.error, 'error');
            return;
        }
        setAccountsStatus('');
        await loadAccountsList();
        startAccountsPolling(60);
    } catch (err) {
        setAccountsStatus('❌ Lỗi: ' + err.message, 'error');
    }
}

async function closeAccount(accountId) {

    setAccountsStatus('Đang dừng...', 'loading');
    try {
        var resp = await fetch('/api/accounts/' + encodeURIComponent(accountId) + '/close', {
            method: 'POST'
        });
        var json = await resp.json();
        if (json.error) {
            setAccountsStatus(json.error, 'error');
            return;
        }
        setAccountsStatus('Đã dừng.');
        await loadAccountsList();
    } catch (err) {
        setAccountsStatus('❌ Lỗi: ' + err.message, 'error');
    }
}



// ─── Sửa tài khoản: gộp Tên + Proxy trong 1 modal duy nhất (không dùng prompt()) ─

function editAccount(accountId, currentName, currentProxy, isRunning) {
    var backdrop = document.getElementById('editAccountModalBackdrop');
    if (!backdrop) {
        backdrop = document.createElement('div');
        backdrop.id = 'editAccountModalBackdrop';
        backdrop.className = 'overlay-backdrop';
        backdrop.innerHTML = `
            <div class="overlay-card">
                <div class="overlay-header">
                    <h3>Sửa tài khoản</h3>
                    <button class="overlay-close" onclick="closeEditAccountModal()">&times;</button>
                </div>
                <div class="overlay-body">
                    <div style="margin-bottom: 16px;">
                        <label style="display: block; font-size: 12px; font-weight: 700; margin-bottom: 10px; color: var(--text-muted); text-transform: none;">Tên tài khoản</label>
                        <input id="editAccountName" type="text" class="overlay-input" placeholder="Ví dụ: Tài khoản 1">
                    </div>
                    <div style="margin-bottom: 6px;">
                        <label style="display: block; font-size: 12px; font-weight: 700; margin-bottom: 10px; color: var(--text-muted); text-transform: none;">Proxy (không bắt buộc)</label>
                        <textarea id="editAccountProxy" class="overlay-textarea" placeholder="Định dạng hỗ trợ:&#10;ip:port&#10;ip:port:user:pass&#10;http://user:pass@ip:port&#10;&#10;Để trống để xóa proxy."></textarea>
                    </div>
                    <p id="editAccountProxyHint" style="margin: 6px 0 16px; color: var(--text-muted); font-size: 11px; line-height: 1.6;"></p>
                    <div style="display: flex; gap: 10px; justify-content: flex-end;">
                        <button class="btn btn-ghost btn-sm" onclick="closeEditAccountModal()">Hủy</button>
                        <button class="btn btn-primary btn-sm" id="btnSaveEditAccount" onclick="submitEditAccount()">Lưu</button>
                    </div>
                </div>
            </div>
        `;
        backdrop.addEventListener('click', function(e) {
            if (e.target === backdrop) closeEditAccountModal();
        });
        document.body.appendChild(backdrop);
    }

    document.getElementById('editAccountName').value = currentName || '';
    document.getElementById('editAccountProxy').value = currentProxy || '';
    document.getElementById('editAccountProxyHint').textContent = isRunning
        ? 'Tài khoản đang chạy: nếu đổi proxy, cần dừng và khởi chạy lại để có hiệu lực.'
        : 'Proxy sẽ được áp dụng ngay từ lần khởi chạy Chrome tiếp theo.';

    window._editAccountContext = { accountId: accountId, isRunning: isRunning };

    backdrop.classList.add('show');
    setTimeout(function() {
        document.getElementById('editAccountName').focus();
        document.getElementById('editAccountName').select();
    }, 50);
}

function closeEditAccountModal() {
    var backdrop = document.getElementById('editAccountModalBackdrop');
    if (backdrop) backdrop.classList.remove('show');
}

async function submitEditAccount() {
    var ctx = window._editAccountContext || {};
    var accountId = ctx.accountId;
    if (!accountId) return;

    var name = (document.getElementById('editAccountName').value || '').trim();
    var proxy = (document.getElementById('editAccountProxy').value || '').trim();

    if (!name) {
        setAccountsStatus('Tên không được để trống.', 'error');
        return;
    }

    var btn = document.getElementById('btnSaveEditAccount');
    if (btn) { btn.disabled = true; btn.textContent = 'Đang lưu...'; }

    try {
        var resp = await fetch('/api/accounts/' + encodeURIComponent(accountId), {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name, proxy: proxy })
        });
        var json = await resp.json();
        if (json.error) {
            setAccountsStatus(json.error, 'error');
            return;
        }
        closeEditAccountModal();
        setAccountsStatus(
            ctx.isRunning ? '✓ Đã lưu. Cần dừng và khởi chạy lại nếu proxy thay đổi.' : '✓ Đã lưu thay đổi.',
            'success'
        );
        await loadAccountsList();
    } catch (err) {
        setAccountsStatus('Lỗi: ' + err.message, 'error');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Lưu'; }
    }
}

async function deleteAccount(accountId, name) {
    var label = name || accountId;
    var ok = await nexusConfirm('Xóa "' + label + '"?\n\nThư mục profile và dữ liệu đăng nhập sẽ bị xóa vĩnh viễn.', { title: 'Xóa tài khoản', confirmText: 'Xóa', danger: true });
    if (!ok) {
        return;
    }

    setAccountsStatus('Đang xóa...', 'loading');
    try {
        var resp = await fetch('/api/accounts/' + encodeURIComponent(accountId), {
            method: 'DELETE'
        });
        var json = await resp.json();
        if (json.error) {
            setAccountsStatus(json.error, 'error');
            return;
        }
        setAccountsStatus('Đã xóa ' + label + '.', 'success');
        await loadAccountsList();
    } catch (err) {
        setAccountsStatus('Lỗi: ' + err.message, 'error');
    }
}

document.addEventListener('DOMContentLoaded', function() {
    loadAccountPlan().then(function () { loadAccountsList(); });
    startAccountsHeartbeat();
    var newAccountBackdrop = document.getElementById('newAccountModalBackdrop');
    if (newAccountBackdrop) {
        newAccountBackdrop.addEventListener('click', function(e) {
            if (e.target === newAccountBackdrop) closeNewAccountModal();
        });
    }
});
