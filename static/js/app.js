
/* ===== FIX: lấy account đang chọn cho popup nhắn tin ===== */
function getCurrentSelectedAccountIdForMessage() {
    // FIX: ưu tiên tài khoản của tab đang active, tránh luôn lấy tài khoản đầu tiên ở hidden input khác.
    var activeMap = [
        { tab: 'tab-group', input: 'schedGroupAccountId' },
        { tab: 'tab-phone', input: 'schedPhoneAccountId' },
        { tab: 'tab-personal-groups', input: 'schedPersonalGroupAccountId' }
    ];

    for (var i = 0; i < activeMap.length; i++) {
        var tabEl = document.getElementById(activeMap[i].tab);
        if (tabEl && tabEl.classList.contains('active')) {
            var activeInput = document.getElementById(activeMap[i].input);
            if (activeInput && activeInput.value && activeInput.value.trim()) {
                return activeInput.value.trim();
            }
        }
    }

    var ids = [
        'memberAccountId',
        'schedPersonalGroupAccountId',
        'schedPhoneAccountId',
        'schedGroupAccountId',
        'selectedAccountId',
        'accountId'
    ];

    for (var j = 0; j < ids.length; j++) {
        var el = document.getElementById(ids[j]);
        if (el && el.value && el.value.trim()) {
            return el.value.trim();
        }
    }

    return '';
}
// ─── Shared table renderer ───────────────────────────────────────────────────

function escHtml(s) {
    if (s == null) return '';
    var d = document.createElement('div');
    d.textContent = String(s);
    return d.innerHTML;
}

function setStatus(msg, type) {
    var el = document.getElementById('statusBar');
    if (!el) return;
    el.innerHTML = (type === 'loading' ? '<span class="spinner"></span>' : '') + msg;
    el.className = 'status-bar ' + type;
    el.style.display = 'block';
}

function renderResults(data) {
    document.getElementById('totalCount').textContent = data.length;
    var tbody = document.getElementById('resultsBody');
    tbody.innerHTML = '';
    data.forEach(function(item, idx) {
        var stt = idx + 1;
        var avatar;
        if (item.avatar) {
            avatar = '<img class="av" src="' + escHtml(item.avatar) + '" alt="avatar" style="cursor: pointer;" onerror="this.outerHTML=\'<div class=av-placeholder>?</div>\'" onclick="AvatarPreview.open(this.src); event.stopPropagation();">';
        } else {
            avatar = '<div class="av-placeholder">?</div>';
        }
        var name = escHtml(item.zaloName || '-');
        var uid  = escHtml(item.userId  || '');
        var avt  = (item.avatar || '').replace(/[&'"]/g, function(m) {
            return m === '&' ? '&#x26;' : m === "'" ? "\\'" : '\\"';
        });

        var tr = document.createElement('tr');
        tr.style.cursor = 'pointer';
        tr.onclick = function(e) {
            if (e.target.closest('button')) return;
            openProfileOverlay(uid, name, avt);
        };
        
        // Tạo checkbox td
        var tdCheckbox = document.createElement('td');
        tdCheckbox.style.textAlign = 'center';
        tdCheckbox.style.width = '50px';
        tdCheckbox.style.padding = '8px';
        var checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.className = 'member-checkbox';
        checkbox.style.cursor = 'pointer';
        checkbox.style.width = '18px';
        checkbox.style.height = '18px';
        checkbox.style.accentColor = '#058bff';
        checkbox.onclick = function(e) { e.stopPropagation(); };
        tdCheckbox.appendChild(checkbox);
        tr.appendChild(tdCheckbox);
        
        // Avatar td
        var tdAvatar = document.createElement('td');
        tdAvatar.innerHTML = avatar;
        tr.appendChild(tdAvatar);
        
        // Name td
        var tdName = document.createElement('td');
        tdName.className = 'name-cell member-name';
        tdName.textContent = item.zaloName || '-';
        tr.appendChild(tdName);
        
        // UID td
        var tdUid = document.createElement('td');
        tdUid.className = 'mono';
        tdUid.textContent = uid;
        tr.appendChild(tdUid);
        
        // Status td
        var tdStatus = document.createElement('td');
        tdStatus.style.textAlign = 'center';
        tdStatus.style.color = '#4caf50';
        tdStatus.style.fontWeight = '500';
        tdStatus.innerHTML = '<span style="display:inline-flex;align-items:center;gap:4px;">✓ OK</span>';
        tr.appendChild(tdStatus);
        
        // Action td
        var tdAction = document.createElement('td');
        tdAction.className = 'row-actions';
        tdAction.style.cursor = 'pointer';
        tdAction.onclick = function(e) { e.stopPropagation(); };
        var btn = document.createElement('button');
        btn.className = 'action-btn';
        btn.textContent = 'Xem';
        btn.onclick = function() { openProfileOverlay(uid, item.zaloName || '-', avt); };
        tdAction.appendChild(btn);
        tr.appendChild(tdAction);
        
        tbody.appendChild(tr);
    });
    document.getElementById('resultsSection').style.display = 'block';
    document.getElementById('emptyState').style.display = 'none';
}

// ─── Message Overlay ─────────────────────────────────────────────────────────

function buildOverlayProfileFields(data) {
    var html = '';
    
    if (data.zaloName || data.displayName || data.name) {
        html += '<div style="display: flex; padding: 8px 0; border-bottom: 1px solid var(--border-color); font-size: 13px;">' +
            '<span style="font-weight: 500; color: var(--text-muted); min-width: 120px; margin-right: 12px;">Tên Zalo:</span>' +
            '<span style="flex: 1; word-break: break-word; color: var(--text-primary);">' + escHtml(data.zaloName || data.displayName || data.name || '-') + '</span>' +
            '</div>';
    }
    
    if (data.username) {
        html += '<div style="display: flex; padding: 8px 0; border-bottom: 1px solid var(--border-color); font-size: 13px;">' +
            '<span style="font-weight: 500; color: var(--text-muted); min-width: 120px; margin-right: 12px;">Username:</span>' +
            '<span style="flex: 1; word-break: break-word; color: var(--text-primary); font-family: monospace;">' + escHtml(data.username) + '</span>' +
            '</div>';
    }
    
    if (data.phoneNumber !== undefined && data.phoneNumber !== null && data.phoneNumber !== '') {
        html += '<div style="display: flex; padding: 8px 0; border-bottom: 1px solid var(--border-color); font-size: 13px;">' +
            '<span style="font-weight: 500; color: var(--text-muted); min-width: 120px; margin-right: 12px;">Số điện thoại:</span>' +
            '<span style="flex: 1; word-break: break-word; color: var(--text-primary); font-family: monospace;">' + escHtml(data.phoneNumber) + '</span>' +
            '</div>';
    } else if (data.phoneNumber === '') {
        html += '<div style="display: flex; padding: 8px 0; border-bottom: 1px solid var(--border-color); font-size: 13px;">' +
            '<span style="font-weight: 500; color: var(--text-muted); min-width: 120px; margin-right: 12px;">Số điện thoại:</span>' +
            '<span style="flex: 1; color: var(--text-muted);">Ẩn / không có</span>' +
            '</div>';
    }
    
    if (data.sdob) {
        html += '<div style="display: flex; padding: 8px 0; border-bottom: 1px solid var(--border-color); font-size: 13px;">' +
            '<span style="font-weight: 500; color: var(--text-muted); min-width: 120px; margin-right: 12px;">Ngày sinh:</span>' +
            '<span style="flex: 1; word-break: break-word; color: var(--text-primary);">' + escHtml(data.sdob) + '</span>' +
            '</div>';
    }
    
    if (data.gender !== undefined && data.gender !== null) {
        var genderText = genderLabel(data.gender);
        html += '<div style="display: flex; padding: 8px 0; border-bottom: 1px solid var(--border-color); font-size: 13px;">' +
            '<span style="font-weight: 500; color: var(--text-muted); min-width: 120px; margin-right: 12px;">Giới tính:</span>' +
            '<span style="flex: 1; color: var(--text-primary);">' + escHtml(genderText) + '</span>' +
            '</div>';
    }
    
    if (data.isFr !== undefined && data.isFr !== null) {
        var friendText = data.isFr === 1 ? 'Bạn bè' : data.isFr === 0 ? 'Không phải bạn bè' : 'Không xác định';
        var badgeStyle = data.isFr === 1 ? 'background: #4caf50;' : 'background: #999;';
        html += '<div style="display: flex; padding: 8px 0; border-bottom: 1px solid var(--border-color); font-size: 13px;">' +
            '<span style="font-weight: 500; color: var(--text-muted); min-width: 120px; margin-right: 12px;">Quan hệ:</span>' +
            '<span style="flex: 1;"><span style="display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 500; color: white; ' + badgeStyle + '">' + escHtml(friendText) + '</span></span>' +
            '</div>';
    }
    
    if (data.status) {
        html += '<div style="display: flex; padding: 8px 0; border-bottom: 1px solid var(--border-color); font-size: 13px;">' +
            '<span style="font-weight: 500; color: var(--text-muted); min-width: 120px; margin-right: 12px;">Trạng thái:</span>' +
            '<span style="flex: 1; word-break: break-word; color: var(--text-primary);">' + escHtml(data.status) + '</span>' +
            '</div>';
    }
    
    if (data.type !== undefined && data.type !== null) {
        var typeLabel = { 0: 'Cá nhân', 1: 'Official Account', 2: 'Bot' }[data.type] || 'Không xác định';
        html += '<div style="display: flex; padding: 8px 0; border-bottom: 1px solid var(--border-color); font-size: 13px;">' +
            '<span style="font-weight: 500; color: var(--text-muted); min-width: 120px; margin-right: 12px;">Loại TK:</span>' +
            '<span style="flex: 1; color: var(--text-primary);">' + escHtml(typeLabel) + '</span>' +
            '</div>';
    }
    
    if (data.accountStatus !== undefined && data.accountStatus !== null) {
        html += '<div style="display: flex; padding: 8px 0; font-size: 13px;">' +
            '<span style="font-weight: 500; color: var(--text-muted); min-width: 120px; margin-right: 12px;">Trạng thái TK:</span>' +
            '<span style="flex: 1; color: var(--text-primary);">' + escHtml(String(data.accountStatus)) + '</span>' +
            '</div>';
    }
    
    return html || '<div style="text-align: center; color: var(--text-muted); font-size: 13px;">Không có thông tin thêm</div>';
}

function openOverlay(uid, name, avatar) {
    avatar = (avatar || '')
        .replace(/&#x26;/g, '&')
        .replace(/\\'/g, "'");

    document.getElementById('overlayAvatar').src = avatar;
    document.getElementById('overlayAvatar').style.display = avatar ? 'block' : 'none';
    document.getElementById('overlayAvatar').onerror = function() { this.style.display = 'none'; };
    document.getElementById('overlayName').textContent = name || '-';
    document.getElementById('overlayUid').textContent  = uid  || '-';
    document.getElementById('overlayMsg').value  = '';
    document.getElementById('overlayStatus').textContent = '';
    document.getElementById('overlaySendBtn').disabled = false;
    document.getElementById('overlaySendBtn').innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg> Gửi tin nhắn';
    document.getElementById('msgOverlayBackdrop').classList.add('visible');
    document.getElementById('msgOverlayBackdrop').style.display = 'flex';
    
    // Fetch and display profile information
    var fieldsContainer = document.getElementById('overlayProfileFields');
    var infoContainer = document.getElementById('overlayProfileInfoContainer');
    
    fieldsContainer.innerHTML = '<div style="text-align: center; color: var(--text-muted); padding: 8px; font-size: 13px;">Đang tải thông tin...</div>';
    infoContainer.style.display = 'block';
    
    fetch('/api/single-profile', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ uid: uid, account_id: getCurrentSelectedAccountIdForMessage(), accountId: getCurrentSelectedAccountIdForMessage() })
    })
    .then(function(r) { return r.json(); })
    .then(function(result) {
        if (result.error || !result.success || !result.profile) {
            fieldsContainer.innerHTML = '<div style="text-align: center; color: var(--text-muted); padding: 8px; font-size: 13px;">Không thể tải thông tin</div>';
            return;
        }
        
        var profile = result.profile;
        fieldsContainer.innerHTML = buildOverlayProfileFields(profile);
    })
    .catch(function(err) {
        fieldsContainer.innerHTML = '<div style="text-align: center; color: var(--text-muted); padding: 8px; font-size: 13px;">Lỗi: ' + escHtml(err.message) + '</div>';
    });
}

function closeOverlay() {
    var backdrop = document.getElementById('msgOverlayBackdrop');
    if (backdrop) {
        backdrop.classList.remove('visible');
        backdrop.style.display = 'none';
    }
}

var overlayCloseBtn = document.getElementById('overlayCloseBtn');
if (overlayCloseBtn) {
    overlayCloseBtn.addEventListener('click', closeOverlay);
}

var msgOverlayBackdrop = document.getElementById('msgOverlayBackdrop');
if (msgOverlayBackdrop) {
    msgOverlayBackdrop.addEventListener('click', function(e) {
        if (e.target === this) closeOverlay();
    });
}
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') closeOverlay();
});

// ─── Profile Detail Overlay ───────────────────────────────────────────────────

var _currentProfileData = null;

function openProfileOverlay(uid, name, avatar) {
    avatar = (avatar || '')
        .replace(/&#x26;/g, '&')
        .replace(/\\'/g, "'");

    if (typeof AvatarPreview !== 'undefined') AvatarPreview.close();
    _currentProfileData = null;

    var memberData = null;
    if (typeof _membersCache !== 'undefined' && _membersCache) {
        memberData = _membersCache[uid];
    }

    setProfileHeader(uid, name, avatar);

    var fieldsEl = document.getElementById('pfFields');
    var statusEl = document.getElementById('pfStatus');
    statusEl.textContent = 'Đang tải profile...';
    statusEl.className = 'overlay-status';

    if (memberData) {
        fieldsEl.innerHTML = buildProfileFields(memberData);
    } else {
        fieldsEl.innerHTML = buildBasicFields({ uid: uid, name: name, avatar: avatar });
    }

    document.getElementById('profileBackdrop').classList.add('visible');
    document.getElementById('profileBackdrop').style.display = 'flex';

    fetchSingleProfile(uid, name, avatar, memberData);
}

function setProfileHeader(uid, name, avatar) {
    var img = document.getElementById('pfAvatar');
    if (avatar) {
        var avatarSrc = avatar.startsWith('//') ? 'https:' + avatar : avatar;
        img.src = avatarSrc;
        img.style.display = 'block';
        img.style.cursor = 'zoom-in';
        img.title = 'Nhấn để xem ảnh full size';
        img.onerror = function() { this.style.display = 'none'; };
        document.getElementById('pfAvatarPlaceholder').style.display = 'none';
    } else {
        img.style.display = 'none';
        document.getElementById('pfAvatarPlaceholder').style.display = 'flex';
    }
    document.getElementById('pfName').textContent = name || '-';
    document.getElementById('pfUid').textContent = uid || '-';
}

function fetchSingleProfile(uid, name, avatar, memberData) {
    var statusEl = document.getElementById('pfStatus');
    var fieldsEl = document.getElementById('pfFields');

    fetch('/api/single-profile', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ uid: uid, account_id: getCurrentSelectedAccountIdForMessage(), accountId: getCurrentSelectedAccountIdForMessage() })
    })
    .then(function(r) { return r.json(); })
    .then(function(result) {
        if (result.error) {
            statusEl.textContent = result.error;
            statusEl.className = 'overlay-status error';
            return;
        }
        if (!result.success || !result.profile) return;

        var profile = result.profile;
        var merged = Object.assign({}, memberData || {}, profile);
        merged.userId = profile.userId || uid;
        merged.zaloName = profile.zaloName || profile.displayName || name;
        merged.avatar = profile.avatar || avatar || (memberData && memberData.avatar) || '';

        _currentProfileData = merged;
        if (typeof _membersCache !== 'undefined' && _membersCache) {
            _membersCache[uid] = merged;
        }

        document.getElementById('pfName').textContent = merged.zaloName || merged.displayName || name || '-';
        if (merged.avatar) {
            var avatarSrc = merged.avatar.startsWith('//') ? 'https:' + merged.avatar : merged.avatar;
            document.getElementById('pfAvatar').src = avatarSrc;
            document.getElementById('pfAvatar').style.display = 'block';
            document.getElementById('pfAvatarPlaceholder').style.display = 'none';
        }

        fieldsEl.innerHTML = buildProfileFields(merged);
        statusEl.textContent = '';
        statusEl.className = 'overlay-status';
    })
    .catch(function(err) {
        statusEl.textContent = 'Lỗi tải profile: ' + err.message;
        statusEl.className = 'overlay-status error';
    });
}

function buildBasicFields(data) {
    var rows = [];
    rows.push(makeField('User ID', '<span class="pf-value mono">' + escHtml(data.uid || '-') + '</span>'));
    rows.push(makeField('Tên hiển thị', '<span class="pf-value">' + escHtml(data.name || '-') + '</span>'));
    return rows.join('');
}

function genderLabel(g) {
    if (g === undefined || g === null || g === '' || g === '-') return '-';
    var n = Number(g);
    if (Number.isNaN(n)) return String(g);
    if (n === 0) return 'Nam';
    if (n === 1 || n === 2) return 'Nữ';
    return 'Không xác định';
}

function friendLabel(isFr) {
    if (isFr === 1) return 'Bạn bè';
    if (isFr === 0) return 'Không phải bạn bè';
    return 'Không xác định';
}

function buildProfileFields(data) {
    var rows = [];
    var shownKeys = {};

    function addRow(label, valueHtml, key) {
        rows.push(makeField(label, valueHtml));
        if (key) shownKeys[key] = true;
    }

    addRow('User ID', '<span class="pf-value mono">' + escHtml(data.userId || data.uid || '-') + '</span>', 'userId');
    addRow('Tên Zalo', '<span class="pf-value">' + escHtml(data.zaloName || data.name || '-') + '</span>', 'zaloName');
    if (data.displayName || data.dName) {
        addRow('Tên hiển thị', '<span class="pf-value">' + escHtml(data.displayName || data.dName || '-') + '</span>', 'displayName');
    }
    if (data.username) {
        addRow('Username', '<span class="pf-value mono">' + escHtml(data.username) + '</span>', 'username');
    }
    if (data.sdob) {
        addRow('Ngày sinh', '<span class="pf-value">' + escHtml(data.sdob) + '</span>', 'sdob');
    }
    if (data.phoneNumber !== undefined && data.phoneNumber !== null && data.phoneNumber !== '') {
        addRow('Số điện thoại', '<span class="pf-value mono">' + escHtml(data.phoneNumber) + '</span>', 'phoneNumber');
    } else if (data.phoneNumber === '') {
        addRow('Số điện thoại', '<span class="pf-value muted">Ẩn / không có</span>', 'phoneNumber');
    }
    if (data.gender !== undefined && data.gender !== null) {
        addRow('Giới tính', '<span class="pf-value">' + escHtml(genderLabel(data.gender)) + '</span>', 'gender');
    }
    if (data.isFr !== undefined && data.isFr !== null) {
        var frClass = data.isFr === 1 ? 'online' : 'offline';
        addRow('Quan hệ', '<span class="pf-value"><span class="pf-badge ' + frClass + '">' + escHtml(friendLabel(data.isFr)) + '</span></span>', 'isFr');
    }
    if (data.status) {
        addRow('Trạng thái', '<span class="pf-value">' + escHtml(data.status) + '</span>', 'status');
    }
    if (data.type !== undefined && data.type !== null) {
        var typeLabel = { 0: 'Cá nhân', 1: 'Official Account', 2: 'Bot' }[data.type] || 'Không xác định';
        addRow('Loại tài khoản', '<span class="pf-value">' + escHtml(typeLabel) + '</span>', 'type');
    }
    if (data.accountStatus !== undefined && data.accountStatus !== null) {
        var statusLabel = { 0: 'Hoạt động', 1: 'Bị khóa', 2: 'Bị xóa' }[data.accountStatus] || 'Không xác định';
        var statusClass = data.accountStatus === 0 ? 'online' : 'offline';
        addRow('Trạng thái TK', '<span class="pf-value"><span class="pf-badge ' + statusClass + '">' + escHtml(statusLabel) + '</span></span>', 'accountStatus');
    }
    if (data.bizPkg) {
        var bizLabel = typeof data.bizPkg.label === 'object' ? (data.bizPkg.label.VI || data.bizPkg.label.EN || 'Business') : (data.bizPkg.label || 'Business');
        addRow('Gói Business', '<span class="pf-value"><span class="pf-badge business">' + escHtml(bizLabel) + '</span></span>', 'bizPkg');
    }

    var skipKeys = ['userId', 'uid', 'zaloName', 'dName', 'displayName', 'avatar', 'globalId', 'type', 'accountStatus', 'bizPkg', 'id', 'name', 'gender', 'isFr', 'sdob', 'phoneNumber', 'username', 'status'];
    for (var key in data) {
        if (skipKeys.indexOf(key) === -1 && !shownKeys[key]) {
            var val = typeof data[key] === 'object' ? JSON.stringify(data[key]) : String(data[key]);
            if (val && val !== 'null' && val !== 'undefined' && val !== '{}') {
                addRow(key, '<span class="pf-value mono">' + escHtml(val) + '</span>');
            }
        }
    }
    return rows.join('');
}

function makeField(label, valueHtml) {
    return '<div class="pf-row"><span class="pf-label">' + escHtml(label) + '</span>' + valueHtml + '</div>';
}

function closeProfileOverlay() {
    if (typeof AvatarPreview !== 'undefined') AvatarPreview.close();
    var backdrop = document.getElementById('profileBackdrop');
    if (backdrop) {
        backdrop.classList.remove('visible');
        backdrop.style.display = 'none';
    }
    _currentProfileData = null;
}

function sendMsgFromProfile() {
    var uid = document.getElementById('pfUid').textContent.trim();
    var name = document.getElementById('pfName').textContent.trim();
    var avtImg = document.getElementById('pfAvatar');
    var avatar = (avtImg && avtImg.style.display !== 'none' && avtImg.src) ? avtImg.src : '';
    closeProfileOverlay();
    setTimeout(function() { openOverlay(uid, name, avatar); }, 150);
}

document.getElementById('pfCloseBtn').addEventListener('click', closeProfileOverlay);
document.getElementById('profileBackdrop').addEventListener('click', function(e) {
    if (e.target === this) closeProfileOverlay();
});
document.addEventListener('keydown', function(e) {
    if (e.key !== 'Escape') return;
    if (typeof AvatarPreview !== 'undefined' && AvatarPreview.isOpen()) {
        AvatarPreview.close();
        e.stopPropagation();
        return;
    }
    var profileBackdrop = document.getElementById('profileBackdrop');
    if (profileBackdrop && profileBackdrop.classList.contains('visible')) {
        closeProfileOverlay();
    }
});

// ─── Send SMS ───────────────────────────────────────────────────────────────

document.getElementById('overlaySendBtn').addEventListener('click', function() {
    var uid    = document.getElementById('overlayUid').textContent.trim();
    var name   = document.getElementById('overlayName').textContent.trim();
    var msg    = document.getElementById('overlayMsg').value.trim();
    var status = document.getElementById('overlayStatus');
    var btn    = document.getElementById('overlaySendBtn');

    if (!uid || uid === '-') {
        status.textContent = 'Không xác định được người nhận.';
        status.className = 'overlay-status error';
        return;
    }
    if (!msg) {
        status.textContent = 'Vui lòng nhập nội dung tin nhắn.';
        status.className = 'overlay-status error';
        return;
    }

    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Đang gửi...';
    status.textContent = '';
    status.className = 'overlay-status';

    var accountId = document.getElementById('memberAccountId') ? document.getElementById('memberAccountId').value : '';
    if (!accountId) {
        status.textContent = 'Vui lòng chọn tài khoản để gửi tin nhắn!';
        status.className = 'overlay-status error';
        btn.disabled = false;
        btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg> Gửi tin nhắn';
        return;
    }

    fetch('/api/send-sms', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            accountId: accountId,
            to_uid:    uid,
            message:   msg,
        })
    })
        .then(function(r) { return r && r.json(); })
        .then(function(result) {
            if (!result) return;
            if (result.error) {
                status.textContent = result.error;
                status.className = 'overlay-status error';
            } else {
                status.textContent = 'Đã gửi tin nhắn đến ' + name + '!';
                status.className = 'overlay-status success';
                document.getElementById('overlayMsg').value = '';
                setTimeout(closeOverlay, 1500);
            }
        })
        .catch(function(err) {
            status.textContent = 'Lỗi: ' + err.message;
            status.className = 'overlay-status error';
        })
        .finally(function() {
            btn.disabled = false;
            btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg> Gửi tin nhắn';
        });
});

// ─── Filter results by name ─────────────────────────────────────────────────

function filterResults() {
    var query = document.getElementById('searchInput').value.toLowerCase().trim();
    var rows = document.querySelectorAll('#resultsBody tr');
    var count = 0;
    rows.forEach(function(row) {
        var nameCell = row.querySelector('.member-name');
        var name = nameCell ? nameCell.textContent.toLowerCase() : '';
        var match = !query || name.includes(query);
        row.style.display = match ? '' : 'none';
        if (match) count++;
    });
    document.getElementById('totalCount').textContent = count;
}





