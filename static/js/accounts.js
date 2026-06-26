// Quản lý danh sách tài khoản Zalo

var _accountsPollTimer = null;
var _pollEndTime = 0;

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

    var info = document.createElement('div');
    info.className = 'account-card-info';
    var st = accountStatusLabel(acc);
    info.innerHTML =
        '<div class="account-card-name">' + escHtml(acc.name || 'Tài khoản') + '</div>' +
        '<div style="display: flex; gap: 8px; align-items: center; margin-top: 6px;">' +
            '<div class="account-card-status ' + st.cls + '" style="flex: 0 0 auto;">' + escHtml(st.text) + '</div>' +
            '<div class="account-card-proxy-inline mono" style="flex: 1; min-width: 0;">Proxy: ' + escHtml(acc.proxy ? acc.proxy.split('@').pop() : 'Chưa cấu hình') + '</div>' +
        '</div>' +
        '<div class="account-card-id mono" style="margin-top: 4px;">' + escHtml(acc.accountId) + '</div>';

    var actions = document.createElement('div');
    actions.className = 'account-card-actions';

    var btnOpen = document.createElement('button');
    btnOpen.className = 'btn btn-primary btn-sm';
    
    // Nếu đang chạy, hiển thị "Đóng", ngược lại "Mở"
    if (acc.remoteDebugPort) {
        btnOpen.textContent = 'Đóng';
        btnOpen.onclick = function() { closeAccount(acc.accountId); };
    } else {
        btnOpen.textContent = 'Mở';
        btnOpen.onclick = function() { openAccount(acc.accountId); };
    }

    var btnProxy = document.createElement('button');
    btnProxy.className = 'btn btn-ghost btn-sm';
    btnProxy.textContent = 'Proxy';
    btnProxy.onclick = function() { setAccountProxy(acc.accountId, acc.proxy || '', !!acc.remoteDebugPort); };

    var btnEdit = document.createElement('button');
    btnEdit.className = 'btn btn-ghost btn-sm';
    btnEdit.textContent = 'Sửa tên';
    btnEdit.onclick = function() { renameAccount(acc.accountId, acc.name); };

    var btnDelete = document.createElement('button');
    btnDelete.className = 'btn btn-danger btn-sm';
    btnDelete.textContent = 'Xóa';
    btnDelete.onclick = function() { deleteAccount(acc.accountId, acc.name); };

    actions.appendChild(btnOpen);
    actions.appendChild(btnProxy);
    actions.appendChild(btnEdit);
    actions.appendChild(btnDelete);

    card.appendChild(avatarWrap);
    card.appendChild(info);
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
        listEl.innerHTML = '';

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
            return a.zpwEnk && a.cookies && a.loginCaptured && a.userinfoCaptured && a.avatarUrl;
        });
        if (allComplete && _accountsPollTimer) {
            stopAccountsPolling();
        }
    } catch (err) {
        if (!silent) setAccountsStatus('Lỗi tải danh sách: ' + err.message, 'error');
    }
}

async function addAccount() {
    var btn = document.getElementById('btnAddAccount');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span> Đang tạo...';
    }
    setAccountsStatus('⏳ Đang tạo tài khoản và mở Chrome...', 'loading');

    try {
        var resp = await fetch('/api/accounts', { method: 'POST' });
        var json = await resp.json();
        if (json.error) {
            setAccountsStatus(json.error, 'error');
            return;
        }
        setAccountsStatus(
            '✅ Đã tạo ' + (json.account && json.account.name) +
            '. Đăng nhập Zalo trên Chrome.',
            'success'
        );
        await loadAccountsList();
        // Poll for 60 seconds to capture all data through reload
        startAccountsPolling(60);
    } catch (err) {
        setAccountsStatus('❌ Lỗi: ' + err.message, 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML =
                '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
                '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg> Thêm tài khoản';
        }
    }
}

async function openAccount(accountId) {
    setAccountsStatus('Đang mở...', 'loading');
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
    
    setAccountsStatus('Đang đóng...', 'loading');
    try {
        var resp = await fetch('/api/accounts/' + encodeURIComponent(accountId) + '/close', {
            method: 'POST'
        });
        var json = await resp.json();
        if (json.error) {
            setAccountsStatus(json.error, 'error');
            return;
        }
        setAccountsStatus('Đã đóng.');
        await loadAccountsList();
    } catch (err) {
        setAccountsStatus('❌ Lỗi: ' + err.message, 'error');
    }
}



async function renameAccount(accountId, currentName) {
    var name = prompt('Sửa tên tài khoản:', currentName || '');
    if (name === null) return;
    name = name.trim();
    if (!name) {
        setAccountsStatus('Tên không được để trống.', 'error');
        return;
    }

    try {
        var resp = await fetch('/api/accounts/' + encodeURIComponent(accountId), {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name })
        });
        var json = await resp.json();
        if (json.error) {
            setAccountsStatus(json.error, 'error');
            return;
        }
        setAccountsStatus('Đã đổi tên.', 'success');
        await loadAccountsList();
    } catch (err) {
        setAccountsStatus('Lỗi: ' + err.message, 'error');
    }
}

async function setAccountProxy(accountId, currentProxy, isRunning) {
    // Tạo modal overlay nếu chưa tồn tại
    var backdrop = document.getElementById('proxyModalBackdrop');
    if (!backdrop) {
        backdrop = document.createElement('div');
        backdrop.id = 'proxyModalBackdrop';
        backdrop.className = 'overlay-backdrop';
        backdrop.innerHTML = `
            <div class="overlay-card">
                <div class="overlay-header">
                    <h3>Cấu hình Proxy</h3>
                    <button class="overlay-close" onclick="closeProxyModal()">&times;</button>
                </div>
                <div class="overlay-body">
                    <div style="margin-bottom: 16px;">
                        <label style="display: block; font-size: 12px; font-weight: 700; margin-bottom: 10px; color: var(--text-muted); text-transform: none;">Nhập proxy</label>
                        <textarea id="proxyInput" class="overlay-textarea" placeholder="Định dạng hỗ trợ:&#10;ip:port&#10;ip:port:user:pass&#10;http://user:pass@ip:port&#10;&#10;Để trống để xóa proxy."></textarea>
                    </div>
                    <div style="display: flex; gap: 10px; justify-content: flex-end;">
                        <button class="btn btn-ghost btn-sm" onclick="closeProxyModal()">Hủy</button>
                        <button class="btn btn-primary btn-sm" onclick="saveProxyModal()">Lưu</button>
                    </div>
                </div>
            </div>
        `;
        backdrop.addEventListener('click', function(e) {
            if (e.target === backdrop) closeProxyModal();
        });
        document.body.appendChild(backdrop);
    }

    // Set giá trị input
    var input = document.getElementById('proxyInput');
    input.value = currentProxy || '';

    // Store context cho save button
    window._proxyModalContext = {
        accountId: accountId,
        isRunning: isRunning
    };

    // Show with animation
    backdrop.classList.add('show');
    setTimeout(function() {
        input.focus();
        input.select();
    }, 50);
}

function closeProxyModal() {
    var backdrop = document.getElementById('proxyModalBackdrop');
    if (backdrop) {
        backdrop.classList.remove('show');
    }
}

async function saveProxyModal() {
    var input = document.getElementById('proxyInput');
    var proxy = (input.value || '').trim();
    var ctx = window._proxyModalContext || {};
    var accountId = ctx.accountId;
    var isRunning = ctx.isRunning;

    if (!accountId) return;

    try {
        var resp = await fetch('/api/accounts/' + encodeURIComponent(accountId), {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ proxy: proxy })
        });

        var json = await resp.json();

        if (json.error) {
            setAccountsStatus(json.error, 'error');
            return;
        }

        if (isRunning) {
            setAccountsStatus('✓ Đã lưu proxy. Cần đóng và mở lại profile để proxy có hiệu lực.', 'success');
        } else {
            setAccountsStatus(proxy ? '✓ Đã lưu proxy.' : '✓ Đã xóa proxy.', 'success');
        }

        closeProxyModal();
        await loadAccountsList();
    } catch (err) {
        setAccountsStatus('✗ Lỗi lưu proxy: ' + err.message, 'error');
    }
}

async function deleteAccount(accountId, name) {
    var label = name || accountId;
    if (!confirm('Xóa "' + label + '"?\n\nThư mục profile và dữ liệu đăng nhập sẽ bị xóa vĩnh viễn.')) {
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
    loadAccountsList();
});
