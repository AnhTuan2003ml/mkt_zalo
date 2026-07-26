
function schedCopyPersonalGroupId(groupId, event) {
    if (event) {
        event.stopPropagation();
        event.preventDefault();
    }

    groupId = String(groupId || '').trim();

    if (!groupId) {
        schedShowNotif('Lỗi', 'Không có Group ID để copy', 'error');
        return;
    }

    function done() {
        schedShowNotif('Đã copy', 'Đã copy Group ID: ' + groupId, 'success');
    }

    function fallbackCopy() {
        var input = document.createElement('textarea');
        input.value = groupId;
        input.style.position = 'fixed';
        input.style.left = '-9999px';
        document.body.appendChild(input);
        input.focus();
        input.select();

        try {
            document.execCommand('copy');
            done();
        } catch (e) {
            console.error('Copy Group ID lỗi:', e);
            schedShowNotif('Lỗi', 'Không copy được Group ID', 'error');
        }

        document.body.removeChild(input);
    }

    if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(groupId).then(done).catch(fallbackCopy);
    } else {
        fallbackCopy();
    }
}



function schedToggleAdvanced(type) {
    var box = null;

    if (type === 'group') {
        box = document.getElementById('groupAdvancedSettings');
    }

    if (type === 'phone') {
        box = document.getElementById('phoneAdvancedSettings');
    }

    if (!box) {
        console.warn('Không tìm thấy advanced settings:', type);
        return;
    }

    if (box.style.display === 'none' || box.style.display === '') {
        box.style.display = 'block';
    } else {
        box.style.display = 'none';
    }
}
// Lập lịch gửi tin nhắn

var _schedMembers = [];
var _schedSelectedMembers = new Set();
var _schedGroupInfo = null;
var _detailRefreshInterval = null;
var _listRefreshInterval = null;
var _currentDetailModal = null;
var _currentDetailScheduleId = null;
var _schedAccounts = [];
var _phoneLookupResults = [];
var _phoneSelectedResults = new Set();
var _personalGroups = [];
var _personalGroupsSelected = new Set();

// ─── LOCALSTORAGE ─────────────────────────────────────────────────────
const STORAGE_PERSONAL_GROUPS = 'zaloTool_personalGroups';
const STORAGE_SCHEDULES_LIST = 'zaloTool_schedulesList';
const STORAGE_AVATAR_OVERLAY_DATA = 'zaloTool_avatarOverlay';
const STORAGE_SCHED_FORM = 'zaloTool_schedFormState';
const STORAGE_PHONE_LOOKUP = 'zaloTool_phoneLookup';

function savePersonalGroupsToStorage(accountId, groups) {
    try {
        var all = JSON.parse(localStorage.getItem(STORAGE_PERSONAL_GROUPS) || '{}');
        all[accountId] = { groups: groups, savedAt: new Date().toISOString() };
        localStorage.setItem(STORAGE_PERSONAL_GROUPS, JSON.stringify(all));
    } catch (e) { console.warn('savePersonalGroups:', e); }
}
function getPersonalGroupsFromStorage(accountId) {
    try {
        var all = JSON.parse(localStorage.getItem(STORAGE_PERSONAL_GROUPS) || '{}');
        return (all[accountId] && all[accountId].groups) || [];
    } catch (e) { return []; }
}
function clearPersonalGroupsFromStorage(accountId) {
    try {
        var all = JSON.parse(localStorage.getItem(STORAGE_PERSONAL_GROUPS) || '{}');
        delete all[accountId];
        localStorage.setItem(STORAGE_PERSONAL_GROUPS, JSON.stringify(all));
    } catch (e) {}
}
function saveSchedulesToStorage(schedules) {
    try { localStorage.setItem(STORAGE_SCHEDULES_LIST, JSON.stringify({ schedules: schedules, savedAt: new Date().toISOString() })); } catch (e) {}
}
function getSchedulesFromStorage() {
    try { var d = JSON.parse(localStorage.getItem(STORAGE_SCHEDULES_LIST) || '{}'); return d.schedules || []; } catch (e) { return []; }
}

// ─── PHONE LOOKUP STORAGE ─────────────────────────────────────────────
function savePhoneLookupToStorage(results) {
    try { localStorage.setItem(STORAGE_PHONE_LOOKUP, JSON.stringify({ results: results, savedAt: new Date().toISOString() })); } catch (e) {}
}
function getPhoneLookupFromStorage() {
    try { var d = JSON.parse(localStorage.getItem(STORAGE_PHONE_LOOKUP) || '{}'); return d.results || []; } catch (e) { return []; }
}

// ─── AVATAR OVERLAY ────────────────────────────────────────────────────
function showAvatarOverlay(avatarUrl, title) {
    title = title || 'Ảnh';
    var overlay = document.getElementById('avatarImageOverlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'avatarImageOverlay';
        overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.8);display:flex;align-items:center;justify-content:center;z-index:9999;opacity:0;transition:opacity 0.2s;cursor:pointer;';
        overlay.innerHTML = '<div style="position:relative;max-width:90%;max-height:90%;display:flex;flex-direction:column;align-items:center;justify-content:center;"><img id="avatarImageDisplay" src="" style="max-width:100%;max-height:80vh;border-radius:8px;object-fit:contain;box-shadow:0 0 20px rgba(0,0,0,0.5);"><p id="avatarImageTitle" style="color:white;margin-top:15px;font-size:14px;text-align:center;white-space:pre-wrap;word-break:break-word;"></p><button onclick="closeAvatarOverlay()" style="position:absolute;top:20px;right:20px;background:rgba(255,255,255,0.2);border:2px solid white;color:white;width:40px;height:40px;border-radius:50%;cursor:pointer;font-size:24px;display:flex;align-items:center;justify-content:center;transition:all 0.2s;" onmouseover="this.style.background=\'rgba(255,255,255,0.3)\'" onmouseout="this.style.background=\'rgba(255,255,255,0.2)\'">✕</button></div>';
        overlay.addEventListener('click', function(e) { if (e.target === overlay) closeAvatarOverlay(); });
        document.body.appendChild(overlay);
    }
    document.getElementById('avatarImageDisplay').src = normalizeAvatarUrlSchedules(avatarUrl) || '';
    document.getElementById('avatarImageTitle').textContent = title;
    overlay.style.display = 'flex';
    setTimeout(function() { overlay.style.opacity = '1'; }, 10);
}
function closeAvatarOverlay() {
    var o = document.getElementById('avatarImageOverlay');
    if (o) { o.style.opacity = '0'; setTimeout(function() { o.style.display = 'none'; }, 200); }
}

// ─── HELPERS ────────────────────────────────────────────────────────────

function hasScheduleValue(v) {
    return v !== undefined && v !== null && v !== '' && v !== '-';
}

function formatScheduleGender(value) {
    if (!hasScheduleValue(value)) return '-';
    var n = Number(value);
    if (Number.isNaN(n)) return String(value);
    if (n === 0) return 'Nam';
    if (n === 1 || n === 2) return 'Nữ';
    return 'Không xác định';
}

function escapeHtmlSchedules(str) {
    return String(str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
}
function normalizeAvatarUrlSchedules(url) {
    if (!url) return '';
    if (url.startsWith('//')) return 'https:' + url;
    return url;
}
function escHtml(text) {
    if (!text) return '';
    var m = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
    return text.replace(/[&<>"']/g, function(c) { return m[c]; });
}



function schedCleanUid(uidText) {
    var uid = String(uidText || '').trim();

    if (uid.toLowerCase().indexOf('id:') === 0) {
        uid = uid.split(':').slice(1).join(':').trim();
    }

    var m = uid.match(/\d{8,}/);
    if (m) return m[0];

    return uid;
}

function schedGetActiveAccountId() {
    // FIX: lấy tài khoản theo tab đang active, không lấy input đầu tiên trên DOM.
    var activeMap = [
        { tab: 'tab-group', input: 'schedGroupAccountId' },
        { tab: 'tab-phone', input: 'schedPhoneAccountId' },
        { tab: 'tab-personal-groups', input: 'schedPersonalGroupAccountId' }
    ];

    for (var i = 0; i < activeMap.length; i++) {
        var tabEl = document.getElementById(activeMap[i].tab);
        if (tabEl && tabEl.classList.contains('active')) {
            var activeInput = document.getElementById(activeMap[i].input);
            return activeInput && activeInput.value ? String(activeInput.value).trim() : '';
        }
    }

    // Fallback cho popup/modal không nằm trong tab schedule.
    var ids = ['schedPersonalGroupAccountId', 'schedPhoneAccountId', 'schedGroupAccountId'];
    for (var j = 0; j < ids.length; j++) {
        var el = document.getElementById(ids[j]);
        if (el && el.value) return String(el.value).trim();
    }
    return '';
}

async function schedFetchFullAvatar(fid, accountId) {
    fid = schedCleanUid(fid);
    accountId = String(accountId || '').trim();

    if (!fid || !accountId) {
        console.warn('[schedules avatar] missing fid/accountId', fid, accountId);
        return '';
    }

    var resp = await fetch('/api/get-avatar', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            accountId: accountId,
            account_id: accountId,
            fid: fid,
            uid: fid,
            userId: fid
        })
    });

    var text = await resp.text();
    var json = null;

    try {
        json = JSON.parse(text);
    } catch (e) {
        console.error('[schedules avatar] API tra ve khong phai JSON:', text.slice(0, 300));
        return '';
    }

    if (!resp.ok || !json.success) {
        console.error('[schedules avatar] API error:', json.error || json);
        return '';
    }

    return normalizeAvatarUrlSchedules(
        json.bk_full_avatar ||
        json.avatar_url ||
        json.full_avatar ||
        ''
    );
}

async function schedOpenUserAvatar(fid, title, fallbackUrl, accountId) {
    fid = schedCleanUid(fid);
    title = title || 'Avatar';
    fallbackUrl = normalizeAvatarUrlSchedules(fallbackUrl || '');

    if (!accountId) {
        accountId = schedGetActiveAccountId();
    }

    try {
        console.log('[schedules avatar] click fid=', fid, 'accountId=', accountId);

        var fullUrl = await schedFetchFullAvatar(fid, accountId);

        if (fullUrl) {
            showAvatarOverlay(fullUrl, title);
            return;
        }
    } catch (e) {
        console.error('[schedules avatar] loi lay avatar full:', e);
    }

    if (fallbackUrl) {
        showAvatarOverlay(fallbackUrl, title);
    }
}


// ─── ACCOUNTS ───────────────────────────────────────────────────────────
function schedLoadAccounts() {
    fetch('/api/accounts')
        .then(function(r) { return r.json(); })
        .then(function(json) {
            _schedAccounts = json.accounts || json || [];
            var c1 = document.getElementById('schedGroupAccountDropdown');
            var c2 = document.getElementById('schedPhoneAccountDropdown');
            var c3 = document.getElementById('schedPersonalGroupAccountDropdown');
            if (c1) createAccountDropdownForSched(c1, 'schedGroupAccountId');
            if (c2) createAccountDropdownForSched(c2, 'schedPhoneAccountId');
            if (c3) {
                createAccountDropdownForSched(c3, 'schedPersonalGroupAccountId');
                setTimeout(function() {
                    var pgAid = (document.getElementById('schedPersonalGroupAccountId') || {}).value || '';
                    if (pgAid) schedLoadPersonalGroupsForAccount();
                }, 100);
            }
        });
}

function schedGetAccountIdValue(acc) {
    return String((acc && (acc.accountId || acc.id || acc.account_id)) || '').trim();
}

function schedFindAccountById(accountId) {
    accountId = String(accountId || '').trim();
    if (!accountId) return null;
    return _schedAccounts.find(function(a) { return schedGetAccountIdValue(a) === accountId; }) || null;
}

function schedGetStoredAccountId(hiddenInputId) {
    try { return localStorage.getItem('zaloTool_' + hiddenInputId) || ''; } catch (e) { return ''; }
}

function schedStoreAccountId(hiddenInputId, accountId) {
    try { localStorage.setItem('zaloTool_' + hiddenInputId, accountId || ''); } catch (e) {}
}

function createAccountDropdownForSched(container, hiddenInputId) {
    container.classList.add('account-dropdown');

    var hi = document.getElementById(hiddenInputId);
    var currentId = String((hi && hi.value) || schedGetStoredAccountId(hiddenInputId) || '').trim();
    var sel = schedFindAccountById(currentId) || _schedAccounts[0] || null;

    function rs(acc) {
        if (!acc) return '<button type="button" class="account-dropdown-button"><span>Chọn tài khoản</span></button>';
        var id = schedGetAccountIdValue(acc);
        var av = normalizeAvatarUrlSchedules(acc.avatarUrl || acc.avatar || '');
        var name = acc.name || acc.zaloName || acc.displayName || acc.phone || id || 'Không tên';
        return '<button type="button" class="account-dropdown-button" title="' + escapeHtmlSchedules(id) + '">' +
            (av ? '<img src="' + escapeHtmlSchedules(av) + '" class="account-dropdown-avatar" />' : '<span class="account-dropdown-avatar placeholder"></span>') +
            '<span class="account-dropdown-name">' + escapeHtmlSchedules(name) + '</span></button>';
    }

    function rm() {
        return '<div class="account-dropdown-menu hidden">' + _schedAccounts.map(function(a) {
            var av = normalizeAvatarUrlSchedules(a.avatarUrl || a.avatar || '');
            var id = schedGetAccountIdValue(a);
            var nm = a.name || a.zaloName || a.displayName || a.phone || id || 'Không tên';
            var rd = a.cookies && a.zpwEnk && a.imei;
            var active = (sel && schedGetAccountIdValue(sel) === id) ? ' active' : '';
            return '<button type="button" class="account-dropdown-item' + (rd ? '' : ' disabled') + active + '" data-account-id="' + escapeHtmlSchedules(id) + '"' + (rd ? '' : ' disabled') + '>' +
                (av ? '<img src="' + escapeHtmlSchedules(av) + '" class="account-dropdown-avatar" />' : '<span class="account-dropdown-avatar placeholder"></span>') +
                '<span><b>' + escapeHtmlSchedules(nm) + '</b><br><small>' + escapeHtmlSchedules(id) + '</small></span></button>';
        }).join('') + '</div>';
    }

    function render(acc) {
        sel = acc || null;
        container.innerHTML = rs(sel) + rm();
        var btn = container.querySelector('.account-dropdown-button');
        var menu = container.querySelector('.account-dropdown-menu');

        btn.addEventListener('click', function(e) {
            e.stopPropagation();
            menu.classList.toggle('hidden');
        });

        container.querySelectorAll('.account-dropdown-item:not([disabled])').forEach(function(it) {
            it.addEventListener('click', function(e) {
                e.stopPropagation();
                var aid = String(this.dataset.accountId || '').trim();
                var acc = schedFindAccountById(aid);
                if (!acc) return;

                if (hi) hi.value = aid;
                schedStoreAccountId(hiddenInputId, aid);
                render(acc);

                // FIX: đổi tài khoản là đổi đúng data của tab đó, không giữ data tài khoản cũ.
                if (hiddenInputId === 'schedPersonalGroupAccountId') {
                    _personalGroups = [];
                    _personalGroupsSelected.clear();
                    schedRenderPersonalGroupsTable();
                    schedLoadPersonalGroupsForAccount(aid);
                } else if (hiddenInputId === 'schedGroupAccountId') {
                    _schedMembers = [];
                    _schedSelectedMembers.clear();
                    _schedGroupInfo = null;
                    var sec = document.getElementById('schedMembersSection');
                    if (sec) sec.style.display = 'none';
                } else if (hiddenInputId === 'schedPhoneAccountId') {
                    _phoneLookupResults = [];
                    _phoneSelectedResults.clear();
                    var psec = document.getElementById('phoneResultsSection');
                    if (psec) psec.style.display = 'none';
                }
            });
        });
    }

    if (sel && hi) {
        hi.value = schedGetAccountIdValue(sel);
        schedStoreAccountId(hiddenInputId, hi.value);
    }
    render(sel);

    if (!container._schedDropdownOutsideClickBound) {
        document.addEventListener('click', function(e) {
            if (!container.contains(e.target)) {
                var m = container.querySelector('.account-dropdown-menu');
                if (m) m.classList.add('hidden');
            }
        });
        container._schedDropdownOutsideClickBound = true;
    }
}

// ─── TABS ────────────────────────────────────────────────────────────────
function schedSwitchTab(tabName) {
    // Ẩn tất cả tabs
    ['tab-group', 'tab-phone', 'tab-personal-groups'].forEach(function(id) {
        var t = document.getElementById(id);
        var b = document.querySelector('[data-tab="' + id + '"]');
        if (t) t.classList.remove('active');
        if (b) b.classList.remove('active');
    });
    
    // Ẩn tất cả bảng kết quả
    var schedSection = document.getElementById('schedMembersSection');
    var phoneSection = document.getElementById('phoneResultsSection');
    if (schedSection) schedSection.style.display = 'none';
    if (phoneSection) phoneSection.style.display = 'none';
    
    // Ẩn tất cả headers
    ['personalGroupsHeader', 'personalGroupsLoadingMsg'].forEach(function(id) {
        var el = document.getElementById(id);
        if (el) el.style.display = 'none';
    });
    
    // Kích hoạt tab được chọn
    var t = document.getElementById('tab-' + tabName);
    var b = document.querySelector('[data-tab="tab-' + tabName + '"]');
    if (t) t.classList.add('active');
    if (b) b.classList.add('active');
    
    // Hiển thị bảng phù hợp nếu có dữ liệu
    if (tabName === 'group' && _schedMembers.length > 0 && schedSection) {
        schedSection.style.display = 'block';
    }
    if (tabName === 'phone' && _phoneLookupResults.length > 0 && phoneSection) {
        phoneSection.style.display = 'block';
    }
    
    if (tabName === 'personal-groups') schedLoadPersonalGroupsForAccount();
    schedSaveFormState();
}

// ─── SAVE SCHEDULE ──────────────────────────────────────────────────────

function schedSaveSchedule() {
    var gt = document.getElementById('tab-group');
    var pt = document.getElementById('tab-phone');
    var pg = document.getElementById('tab-personal-groups');
    var tab = gt && gt.classList.contains('active') ? 'group' : (pt && pt.classList.contains('active') ? 'phone' : 'personal-groups');

    var data = { recipients: [], source: tab, rateLimit: { minDelaySec: 3, maxDelaySec: 5, maxConsecutiveErrors: 5 }, batchConfig: { batchSize: 10, batchDelaySec: 30 } };

    if (tab === 'group') {
        data.accountId = (document.getElementById('schedGroupAccountId') || {}).value || '';
        data.title = (document.getElementById('schedGroupTitle') || {}).value || '';
        data.message = (document.getElementById('schedGroupMessage') || {}).value || '';
        data.runAt = (document.getElementById('schedGroupDateTime') || {}).value || '';
        data.recipients = _schedMembers.filter(function(m) { return _schedSelectedMembers.has(m.userId); }).map(function(m) { return { userId: m.userId, zaloName: m.zaloName, avatar: m.avatar }; });
        data.groupInfo = _schedGroupInfo;
        data.rateLimit.minDelaySec = parseInt((document.getElementById('schedGroupMinDelay') || {}).value) || 3;
        data.rateLimit.maxDelaySec = parseInt((document.getElementById('schedGroupMaxDelay') || {}).value) || 5;
        data.rateLimit.maxConsecutiveErrors = parseInt((document.getElementById('schedGroupMaxErrors') || {}).value) || 5;
        data.batchConfig.batchSize = parseInt((document.getElementById('schedGroupBatchSize') || {}).value) || 10;
        data.batchConfig.batchDelaySec = parseInt((document.getElementById('schedGroupBatchDelay') || {}).value) || 30;
    } else if (tab === 'phone') {
        data.accountId = (document.getElementById('schedPhoneAccountId') || {}).value || '';
        data.title = (document.getElementById('schedPhoneTitle') || {}).value || '';
        data.message = (document.getElementById('schedPhoneMessage') || {}).value || '';
        data.runAt = (document.getElementById('schedPhoneDateTime') || {}).value || '';
        data.recipients = _phoneLookupResults.map(function(r, idx) { 
            if (_phoneSelectedResults.has(idx)) {
                return { userId: r.userId, zaloName: r.zaloName, avatar: r.avatar, phone: r.phone }; 
            }
            return null;
        }).filter(Boolean);
        data.rateLimit.minDelaySec = parseInt((document.getElementById('schedPhoneMinDelay') || {}).value) || 3;
        data.rateLimit.maxDelaySec = parseInt((document.getElementById('schedPhoneMaxDelay') || {}).value) || 5;
        data.rateLimit.maxConsecutiveErrors = parseInt((document.getElementById('schedPhoneMaxErrors') || {}).value) || 5;
        data.batchConfig.batchSize = parseInt((document.getElementById('schedPhoneBatchSize') || {}).value) || 10;
        data.batchConfig.batchDelaySec = parseInt((document.getElementById('schedPhoneBatchDelay') || {}).value) || 30;
    } else {
        data.accountId = (document.getElementById('schedPersonalGroupAccountId') || {}).value || '';
        data.title = (document.getElementById('schedPersonalGroupTitle') || {}).value || '';
        data.message = (document.getElementById('schedPersonalGroupMessage') || {}).value || '';
        data.runAt = (document.getElementById('schedPersonalGroupRunAt') || {}).value || '';
        data.recipients = _personalGroups.filter(function(g) { var gid = String(g.groupId || g.gridId || g.id || g.gid || '').trim(); return _personalGroupsSelected.has(gid); }).map(function(g) { var gid = String(g.groupId || g.gridId || g.id || g.gid || '').trim(); return { groupId: gid, name: g.name || g.grid_name || g.groupName || ('Group ' + gid.slice(0,8)), memberCount: g.memberCount || g.grid_totalMember || g.totalMember || g.total || 0 }; });
        data.rateLimit.minDelaySec = parseInt((document.getElementById('schedPersonalGroupMinDelay') || {}).value) || 3;
        data.rateLimit.maxDelaySec = parseInt((document.getElementById('schedPersonalGroupMaxDelay') || {}).value) || 5;
        data.rateLimit.maxConsecutiveErrors = parseInt((document.getElementById('schedPersonalGroupMaxErrors') || {}).value) || 5;
        data.batchConfig.batchSize = parseInt((document.getElementById('schedPersonalGroupBatchSize') || {}).value) || 10;
        data.batchConfig.batchDelaySec = parseInt((document.getElementById('schedPersonalGroupBatchDelay') || {}).value) || 30;
    }

    if (!data.accountId) { schedShowNotif('Lỗi', 'Chưa chọn tài khoản gửi', 'error'); return; }
    if (!data.title) { schedShowNotif('Lỗi', 'Chưa nhập tiêu đề', 'error'); return; }
    if (!data.message) { schedShowNotif('Lỗi', 'Chưa nhập nội dung', 'error'); return; }
    if (!data.runAt) { schedShowNotif('Lỗi', 'Chưa chọn thời gian', 'error'); return; }
    if (!data.recipients.length) { schedShowNotif('Lỗi', 'Chưa chọn người nhận', 'error'); return; }

    var bid = tab === 'group' ? 'btnSaveScheduleGroup' : (tab === 'phone' ? 'btnSaveSchedulePhone' : 'btnSaveSchedulePersonalGroup');
    var btn = document.getElementById(bid);
    var orig = btn ? btn.innerHTML : '';
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Đang lưu...'; }

    fetch('/api/schedules', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) })
        .then(function(r) { return r.json(); })
        .then(function(j) {
            if (j.error) { schedShowNotif('Lỗi', j.error, 'error'); return; }
            schedShowNotif('Thành công', 'Đã lưu lịch! Xem tiến độ tại trang Lịch gửi.', 'success');
        })
        .catch(function(e) { schedShowNotif('Lỗi', e.message, 'error'); })
        .finally(function() { if (btn) { btn.disabled = false; btn.innerHTML = orig; } });
}

// ─── GROUP MEMBERS ──────────────────────────────────────────────────────
async function schedGetGroupMembers() {
    var accountId = document.getElementById('schedGroupAccountId') ? document.getElementById('schedGroupAccountId').value : '';
    var groupLink = document.getElementById('schedGroupInput') ? document.getElementById('schedGroupInput').value.trim() : '';

    if (!accountId) {
        schedShowNotif('Lỗi', 'Vui lòng chọn tài khoản thực hiện!', 'error');
        return;
    }

    if (!groupLink) {
        schedShowNotif('Lỗi', 'Vui lòng nhập Link nhóm Zalo hoặc Group ID!', 'error');
        return;
    }

    var btn = document.getElementById('btnGetGroupMembers');
    var oldBtnHtml = btn ? btn.innerHTML : '';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span>Đang xử lý...';
    }

    _schedMembers = [];
    _schedGroupInfo = null;
    _schedSelectedMembers.clear();
    schedRenderMembersTable();

    var section = document.getElementById('schedMembersSection');
    if (section) section.style.display = 'block';

    schedShowNotif('Đang tải', 'Đang lấy danh sách thành viên giống tab Lấy thành viên...', 'info');

    try {
        // GIỐNG 100% luồng nút runFetch ở /members:
        // 1) gọi /run bằng form-urlencoded với account_id/accountId/group_link
        var resp = await fetch('/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: new URLSearchParams({
                account_id: accountId,
                accountId: accountId,
                group_link: groupLink
            })
        });

        var json = await resp.json();

        if (json.error) {
            schedShowNotif('Lỗi', json.error, 'error');
            return;
        }

        if (!json.success) {
            schedShowNotif('Lỗi', 'Không lấy được danh sách thành viên', 'error');
            return;
        }

        _schedMembers = json.data || [];
        _schedGroupInfo = json.groupInfo || {};
        _schedSelectedMembers.clear();

        // Backend /run đã lấy profile chi tiết rồi mới trả kết quả.
        // Không gọi /api/single-profile tự động lần nữa để tránh lỗi Zalo [221].
        schedRenderMembersTable();

        if (section) section.style.display = 'block';
        var msg = 'Hoàn thành! Lấy được ' + _schedMembers.length + ' thành viên.';
        if (json.profileDetailTotal && json.profileDetailAttempted < json.profileDetailTotal) {
            msg += ' Profile chi tiết còn thiếu ' + (json.profileDetailTotal - json.profileDetailAttempted) + ' UID do Zalo giới hạn request.';
        }
        schedShowNotif('OK', msg, 'success');
    } catch (err) {
        schedShowNotif('Lỗi kết nối', err.message || String(err), 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = oldBtnHtml || '👥 Lấy thành viên';
        }
    }
}

async function schedEnrichMembersWithProfileData(members, accountId) {
    // Copy logic từ members.js/enrichMembersWithProfileData.
    // Gọi get_single_profile thông qua /api/single-profile theo batch, giống nút /members.
    if (!members || members.length === 0 || !accountId) return;

    var totalMembers = members.length;
    var allUids = members
        .map(function(member) { return member.userId || member.id || member.uid; })
        .filter(function(uid) { return !!uid; });

    if (allUids.length === 0) return;

    var processedCount = 0;

    try {
        for (var i = 0; i < allUids.length; i += 200) {
            var batchUids = allUids.slice(i, i + 200);
            schedShowNotif('Đang tải', 'Đang lấy thông tin chi tiết: ' + processedCount + '/' + totalMembers, 'info');

            var response = await fetch('/api/single-profile', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    uids: batchUids,
                    accountId: accountId,
                    account_id: accountId
                })
            });

            var result = await response.json();

            if (result.error) {
                console.warn('[schedEnrichMembersWithProfileData] Batch API error:', result.error);
                batchUids.forEach(function(uid) {
                    var member = members.find(function(m) { return (m.userId || m.id || m.uid) === uid; });
                    if (member) {
                        if (!hasScheduleValue(member.gender)) member.gender = null;
                        member.sdob = member.sdob || '-';
                        member.isFr = member.isFr || 0;
                    }
                });
            } else if (result.profiles) {
                members.forEach(function(member) {
                    var userId = member.userId || member.id || member.uid;
                    if (batchUids.indexOf(userId) !== -1) {
                        var profile = result.profiles[userId];
                        if (profile) {
                            Object.keys(profile).forEach(function(k) {
                                if (profile[k] !== undefined && profile[k] !== null && profile[k] !== '') {
                                    member[k] = profile[k];
                                }
                            });
                            member.userId = member.userId || member.id || userId;
                            member.gender = hasScheduleValue(member.gender) ? Number(member.gender) : null;
                            member.sdob = member.sdob || '-';
                            member.isFr = Number(member.isFr || 0);
                        } else {
                            member.gender = hasScheduleValue(member.gender) ? Number(member.gender) : null;
                            member.sdob = member.sdob || '-';
                            member.isFr = Number(member.isFr || 0);
                        }
                    }
                });
            }

            processedCount += batchUids.length;
            if (i + 200 < allUids.length) {
                await new Promise(function(resolve) { setTimeout(resolve, 500); });
            }
        }
    } catch (err) {
        console.error('[schedEnrichMembersWithProfileData] Fetch error:', err);
        members.forEach(function(member) {
            if (!hasScheduleValue(member.gender)) member.gender = null;
            member.sdob = member.sdob || '-';
            member.isFr = member.isFr || 0;
        });
    }
}

function schedRenderMembersTable() {
    var tbody = document.getElementById('schedMembersBody');
    if (!tbody) return;

    if (!_schedMembers || _schedMembers.length === 0) {
        tbody.innerHTML = '';
        var sc0 = document.getElementById('schedSelectedCount');
        if (sc0) sc0.textContent = '0';
        var tc0 = document.getElementById('schedTotalCount');
        if (tc0) tc0.textContent = '0';
        return;
    }

    var html = '';
    _schedMembers.forEach(function(member, idx) {
        var userId = String(member.userId || member.id || member.uid || '').trim();
        var safeUid = escapeHtmlSchedules(userId);
        var chk = _schedSelectedMembers.has(userId) ? 'checked' : '';

        var name = member.zaloName || member.displayName || member.dName || member.name || 'Không tên';
        var avatar = normalizeAvatarUrlSchedules(member.avatar || member.avt || '');
        var isFr = Number(member.isFr || 0);
        var genderDisplay = formatScheduleGender(member.gender);
        var sdob = member.sdob || member.dob || '-';
        var phoneNumber = member.phoneNumber || member.phone || member.mobile || '-';
        var statusText = member.status || member.accountStatus || '-';

        var friendStatus = isFr === 1 ? 'Đã kết bạn' : 'Chưa';
        var friendColor = isFr === 1 ? 'var(--green)' : 'var(--orange)';
        var friendBgColor = isFr === 1 ? 'rgba(34, 197, 94, 0.1)' : 'rgba(251, 146, 60, 0.1)';

        html += '<tr class="sched-member-row" data-member-id="' + safeUid + '" style="cursor:pointer" onclick="schedToggleMember(this.getAttribute(\'data-member-id\'))">';
        html += '<td style="text-align:center"><input type="checkbox" class="sched-member-checkbox" data-uid="' + safeUid + '" ' + chk + ' onchange="schedToggleMember(this.getAttribute(\'data-uid\')); event.stopPropagation();" onclick="event.stopPropagation();"></td>';

        html += '<td>';
        if (avatar) {
            html += '<img src="' + escapeHtmlSchedules(avatar) + '" class="recipient-avatar avatar-small" style="width:40px;height:40px;border-radius:50%;object-fit:cover;cursor:pointer" onclick="schedOpenMemberAvatar(' + idx + '); event.stopPropagation();" onerror="this.style.display=\'none\'">';
        } else {
            html += '<div class="recipient-avatar" style="width:40px;height:40px;border-radius:50%;background:var(--surface2)"></div>';
        }
        html += '</td>';

        html += '<td>' + escapeHtmlSchedules(name) + '</td>';
        html += '<td><code style="font-size:11px;color:var(--text-secondary)">' + safeUid + '</code></td>';
        html += '<td>' + escapeHtmlSchedules(genderDisplay) + '</td>';
        html += '<td><span style="font-size:12px;color:var(--text-secondary)">' + escapeHtmlSchedules(sdob) + '</span></td>';
        html += '<td><span style="font-size:12px;color:var(--text-secondary)">' + escapeHtmlSchedules(phoneNumber) + '</span></td>';
        html += '<td><span class="member-status-text" title="' + escapeHtmlSchedules(statusText) + '">' + escapeHtmlSchedules(statusText) + '</span></td>';
        html += '<td><span class="status-badge" style="color:' + friendColor + ';background-color:' + friendBgColor + ';font-weight:500">' + escapeHtmlSchedules(friendStatus) + '</span></td>';
        html += '<td><button class="btn btn-sm btn-primary view-btn" onclick="event.stopPropagation(); showMemberDetail(' + idx + ');" style="cursor:pointer">Xem</button></td>';
        html += '</tr>';
    });

    tbody.innerHTML = html;

    var sc = document.getElementById('schedSelectedCount');
    if (sc) sc.textContent = _schedSelectedMembers.size;

    var tc = document.getElementById('schedTotalCount');
    if (tc) tc.textContent = _schedMembers.length;

    var checkedAll = _schedSelectedMembers.size === _schedMembers.length && _schedMembers.length > 0;
    var partial = _schedSelectedMembers.size > 0 && _schedSelectedMembers.size < _schedMembers.length;

    var hcb = document.getElementById('schedHeaderCheckbox');
    if (hcb) {
        hcb.checked = checkedAll;
        hcb.indeterminate = partial;
    }

    var selectAll = document.getElementById('schedSelectAll');
    if (selectAll) {
        selectAll.checked = checkedAll;
        selectAll.indeterminate = partial;
    }
}

function schedToggleMember(uid) {
    if (_schedSelectedMembers.has(uid)) _schedSelectedMembers.delete(uid); else _schedSelectedMembers.add(uid);
    schedRenderMembersTable();
}

function schedToggleSelectAll(cb) {
    if (cb.checked) _schedMembers.forEach(function(m) { _schedSelectedMembers.add(m.userId); }); else _schedSelectedMembers.clear();
    schedRenderMembersTable();
}

function schedFilterMembers() {
    // Hàm filter cho bảng "Từ nhóm" - có thể implement sau nếu cần
}

// ─── PHONE LOOKUP ──────────────────────────────────────────────────────
function schedLookupPhones() {
    var aid = (document.getElementById('schedPhoneAccountId') || {}).value || '';
    var raw = (document.getElementById('phoneInput') || {}).value || '';

    if (!aid) {
        schedShowNotif('L?i', 'Ch?n t?i kho?n', 'error');
        return;
    }

    var phones = raw
        .split(/\n|,|;/)
        .map(function(p) { return p.trim(); })
        .filter(Boolean);

    if (!phones.length) {
        schedShowNotif('L?i', 'Nh?p s? ?i?n tho?i', 'error');
        return;
    }

    schedShowNotif('?ang t?i', '?ang t?o t?c v? tra s? ?i?n tho?i...', 'info');

    fetch('/api/schedules/lookup-phones', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            accountId: aid,
            account_id: aid,
            phones: phones
        })
    })
    .then(function(r) {
        return r.json().then(function(j) {
            j._httpStatus = r.status;
            return j;
        });
    })
    .then(function(j) {
        if (j.error || j.success === false) {
            schedShowNotif('L?i', j.error || 'Kh?ng t?o ???c t?c v? tra s?', 'error');
            return;
        }

        if (j.taskId) {
            schedShowNotif('Đang tải', 'Đang tra cứu... 0%', 'info');
            schedPollPhoneLookupTask(j.taskId);
            return;
        }

        schedApplyPhoneLookupResults(j.results || []);
    })
    .catch(function(err) {
        schedShowNotif('L?i', err.message || String(err), 'error');
    });
}

function schedPollPhoneLookupTask(taskId) {
    var maxTries = 300;
    var tries = 0;

    function poll() {
        tries += 1;

        fetch('/api/tasks/' + encodeURIComponent(taskId))
            .then(function(r) { return r.json(); })
            .then(function(j) {
                if (j.error || j.success === false) {
                    schedShowNotif('Lỗi', j.error || 'Không đọc được trạng thái task', 'error');
                    return;
                }

                var task = j.task || {};
                var status = task.status || '';
                var progress = task.progress || 0;

                if (status === 'failed') {
                    schedShowNotif('Lỗi', task.error || 'Tra số điện thoại thất bại', 'error');
                    return;
                }

                if (status === 'completed') {
                    var result = task.result || {};
                    var results = result.results || [];
                    schedApplyPhoneLookupResults(results);

                    var successCount = result.success_count || results.filter(function(r) {
                        return !!r.success;
                    }).length;

                    schedShowNotif(
                        'OK',
                        'Tra cứu xong: ' + successCount + '/' + results.length + ' số có profile',
                        'success'
                    );
                    return;
                }

                schedShowNotif('Đang tải', 'Đang tra cứu... ' + progress + '%', 'info');

                if (tries < maxTries) {
                    setTimeout(poll, 1000);
                } else {
                    schedShowNotif('Lỗi', 'Tra cứu quá lâu, hãy kiểm tra lại task trong log', 'error');
                }
            })
            .catch(function(err) {
                schedShowNotif('L?i', err.message || String(err), 'error');
            });
    }

    poll();
}

function schedApplyPhoneLookupResults(results) {
    _phoneLookupResults = (results || []).map(function(r) {
        if (r.profile) {
            r.userId = r.profile.userId || r.profile.uid || r.profile.id || '';
            r.zaloName = r.profile.zaloName || r.profile.displayName || r.profile.name || '';
            r.displayName = r.profile.displayName || r.zaloName || '';
            r.avatar = r.profile.avatar || '';
            r.gender = r.profile.gender !== undefined ? r.profile.gender : -1;
            r.phoneNumber = r.profile.phoneNumber || r.normalizedPhone || r.phone || '';
            r.sdob = r.profile.sdob || '';
            r.isFr = r.profile.isFr || 0;
            r.status = r.profile.status || '';
        }
        return r;
    });

    _phoneSelectedResults.clear();

    _phoneLookupResults.forEach(function(r, idx) {
        if (r.success && (r.userId || (r.profile && r.profile.userId))) {
            _phoneSelectedResults.add(idx);
        }
    });

    savePhoneLookupToStorage(_phoneLookupResults);
    schedRenderPhoneResultsTable();

    var section = document.getElementById('phoneResultsSection');
    if (section) section.style.display = 'block';
}


// Render phone results as table
function schedRenderPhoneResultsTable() {
    var tbody = document.getElementById('phoneResultsBody');
    if (!tbody) return;

    tbody.innerHTML = _phoneLookupResults.map(function(r, idx) {
        var isSuccess = !!r.success;
        var checked = isSuccess && _phoneSelectedResults.has(idx);

        var av = r.avatar
            ? '<img src="' + escapeHtmlSchedules(r.avatar) + '" class="recipient-avatar" onclick="schedOpenPhoneAvatar(' + idx + '); event.stopPropagation();" style="cursor:pointer;">'
            : '<div class="recipient-avatar" style="background:rgba(88,166,255,0.2); cursor:pointer;" onclick="event.stopPropagation()"></div>';

        var phone = r.phone || r.phoneNumber || '';
        var phoneHtml = phone
            ? escapeHtmlSchedules(phone)
            : '<span style="color:var(--text-secondary);font-size:11px;">-</span>';

        var status = isSuccess
            ? '<span style="color:var(--green)">✓ OK</span>'
            : '<span style="color:var(--red)">✗ Lỗi</span>';

        var name = r.zaloName || r.displayName || r.name || '';
        var nameHtml = name
            ? escapeHtmlSchedules(name)
            : '<span style="color:var(--text-secondary);">-</span>';

        var userId = String(r.userId || r.uid || r.id || '').trim();

        return ''
            + '<tr style="cursor:pointer;opacity:' + (isSuccess ? '1' : '0.6') + '" '
            + (isSuccess ? 'onclick="schedTogglePhoneResult(' + idx + ')"' : '') + '>'
            + '<td style="text-align:center">'
            + '<input type="checkbox" class="phone-result-checkbox" '
            + (checked ? 'checked ' : '')
            + (isSuccess ? '' : 'disabled ')
            + 'onchange="schedTogglePhoneResult(' + idx + '); event.stopPropagation();" '
            + 'onclick="event.stopPropagation();">'
            + '</td>'
            + '<td>' + av + '</td>'
            + '<td style="font-weight:500">' + nameHtml + '</td>'
            + '<td style="font-size:12px;font-family:monospace;color:var(--accent)">' + phoneHtml + '</td>'
            + '<td style="font-size:10px;font-family:monospace;opacity:0.7" title="' + escapeHtmlSchedules(userId) + '">' + escapeHtmlSchedules(userId) + '</td>'
            + '<td style="font-size:11px">' + status + '</td>'
            + '</tr>';
    }).join('');

    var successCount = _phoneLookupResults.filter(function(r) { return !!r.success; }).length;

    var sc = document.getElementById('phoneSelectedCount');
    if (sc) sc.textContent = _phoneSelectedResults.size;

    var tc = document.getElementById('phoneTotalCount');
    if (tc) tc.textContent = successCount;

    var checkedAll = _phoneSelectedResults.size === successCount && successCount > 0;
    var partial = _phoneSelectedResults.size > 0 && _phoneSelectedResults.size < successCount;

    var hcb = document.getElementById('phoneHeaderCheckbox');
    if (hcb) {
        hcb.checked = checkedAll;
        hcb.indeterminate = partial;
    }

    var selectAll = document.getElementById('phoneSelectAll');
    if (selectAll) {
        selectAll.checked = checkedAll;
        selectAll.indeterminate = partial;
    }
}


function schedTogglePhoneResult(idx) {
    if (_phoneLookupResults[idx] && _phoneLookupResults[idx].success) {
        if (_phoneSelectedResults.has(idx)) _phoneSelectedResults.delete(idx); else _phoneSelectedResults.add(idx);
        schedRenderPhoneResultsTable();
    }
}

function schedTogglePhoneSelectAll(cb) {
    if (cb.checked) {
        _phoneLookupResults.forEach(function(r, idx) { if (r.success) _phoneSelectedResults.add(idx); });
    } else {
        _phoneSelectedResults.clear();
    }
    schedRenderPhoneResultsTable();
}

// Render phone lookup results (old view - for backward compatibility, can be removed)
function schedRenderPhoneResults() {
    var container = document.getElementById('phoneLookupStatus');
    if (!container) return;
    if (!_phoneLookupResults || !_phoneLookupResults.length) {
        container.innerHTML = '<p style="color: var(--text-secondary); font-size: 12px;">Chưa có kết quả</p>';
        return;
    }
    var html = '<div style="margin-top: 12px;">';
    _phoneLookupResults.forEach(function(r, idx) {
        var st = r.success ? '<span style="color: var(--green); font-weight: 600;">✓ Thành công</span>' : '<span style="color: var(--red); font-weight: 600;">✗ Thất bại</span>';
        var av = r.avatar ? '<img src="' + escapeHtmlSchedules(r.avatar) + '" style="width: 28px; height: 28px; border-radius: 50%; object-fit: cover;" />' : '<div style="width: 28px; height: 28px; border-radius: 50%; background: rgba(88,166,255,0.2);"></div>';
        var name = r.zaloName ? escapeHtmlSchedules(r.zaloName) : '<span style="color: var(--text-secondary);">-</span>';
        html += '<div style="display: flex; align-items: center; gap: 8px; padding: 10px; background: var(--bg); border-radius: 4px; margin-bottom: 8px; font-size: 12px;">' +
                '<input type="checkbox" data-idx="' + idx + '" ' + (r.success ? '' : 'disabled') + ' />' +
                '<div style="flex-shrink: 0;">' + av + '</div>' +
                '<div style="flex: 1; min-width: 0;">' +
                '<div style="font-weight: 500; color: var(--text);">' + name + '</div>' +
                '<div style="font-size: 11px; color: var(--text-secondary); font-family: monospace;">' + escapeHtmlSchedules(r.phone || r.phoneNumber) + '</div>' +
                '<div style="font-size: 11px; color: var(--text-secondary);">' + (r.userId ? 'ID: ' + escapeHtmlSchedules(r.userId) : '-') + '</div>' +
                '</div>' +
                '<div style="flex-shrink: 0; text-align: right;">' + st + '</div>' +
                '</div>';
    });
    html += '</div>';
    container.innerHTML = html;
}

// ─── PERSONAL GROUPS ────────────────────────────────────────────────────
function schedLoadPersonalGroupsForAccount(forceAccountId) {
    var aid = String(forceAccountId || ((document.getElementById('schedPersonalGroupAccountId') || {}).value || '')).trim();
    var input = document.getElementById('schedPersonalGroupAccountId');
    if (input && aid) input.value = aid;

    window._schedPersonalGroupsLoadingAid = aid;

    var body = document.getElementById('schedPersonalGroupsBody');
    var empty = document.getElementById('personalGroupsEmpty');
    var header = document.getElementById('personalGroupsHeader');
    var tableWrap = document.getElementById('schedPersonalGroupsTableWrap');

    if (!aid) {
        _personalGroups = [];
        _personalGroupsSelected.clear();
        if (body) body.innerHTML = '';
        if (header) header.style.display = 'none';
        if (tableWrap) tableWrap.style.display = 'none';
        if (empty) empty.style.display = 'block';
        return;
    }

    _personalGroups = [];
    _personalGroupsSelected.clear();
    schedRenderPersonalGroupsTable();

    var lm = document.getElementById('personalGroupsLoadingMsg');
    if (lm) lm.style.display = 'block';

    fetch('/api/groups/personal?accountId=' + encodeURIComponent(aid))
        .then(function(r) { return r.json(); })
        .then(function(d) {
            // FIX: nếu user đã đổi tài khoản trong lúc request cũ chưa xong thì bỏ response cũ.
            if (window._schedPersonalGroupsLoadingAid !== aid) return;
            if (lm) lm.style.display = 'none';

            if (!d.success || !d.groups || !d.groups.length) {
                var cached = getPersonalGroupsFromStorage(aid);
                if (cached.length) {
                    _personalGroups = cached;
                    _personalGroupsSelected.clear();
                    schedRenderPersonalGroupsTable();
                    schedShowNotif('Info', 'Dữ liệu từ bộ nhớ của tài khoản đang chọn', 'info');
                } else {
                    _personalGroups = [];
                    _personalGroupsSelected.clear();
                    schedRenderPersonalGroupsTable();
                }
                return;
            }

            _personalGroups = d.groups;
            _personalGroupsSelected.clear();
            savePersonalGroupsToStorage(aid, d.groups);
            schedRenderPersonalGroupsTable();
        })
        .catch(function() {
            if (window._schedPersonalGroupsLoadingAid !== aid) return;
            if (lm) lm.style.display = 'none';
            var cached = getPersonalGroupsFromStorage(aid);
            if (cached.length) {
                _personalGroups = cached;
                _personalGroupsSelected.clear();
                schedRenderPersonalGroupsTable();
            } else {
                _personalGroups = [];
                _personalGroupsSelected.clear();
                schedRenderPersonalGroupsTable();
            }
        });
}

function schedRenderPersonalGroupsTable() {
    var body = document.getElementById('schedPersonalGroupsBody');
    var empty = document.getElementById('personalGroupsEmpty');
    var header = document.getElementById('personalGroupsHeader');
    var tableWrap = document.getElementById('schedPersonalGroupsTableWrap');
    var selectAll = document.getElementById('schedPersonalGroupSelectAll');
    var headerCheckbox = document.getElementById('schedPersonalGroupHeaderCheckbox');

    if (!body) return;

    if (!_personalGroups || !_personalGroups.length) {
        body.innerHTML = '';
        if (header) header.style.display = 'none';
        if (tableWrap) tableWrap.style.display = 'none';
        if (empty) empty.style.display = 'block';

        var c0 = document.getElementById('schedPersonalGroupSelectedCount');
        if (c0) c0.textContent = '0';
        return;
    }

    if (empty) empty.style.display = 'none';
    if (header) header.style.display = 'flex';
    if (tableWrap) tableWrap.style.display = 'block';

    body.innerHTML = _personalGroups.map(function(group, idx) {
        var gid = String(group.groupId || group.gridId || group.id || group.gid || '').trim();
        var name = group.name || group.grid_name || group.groupName || ('Group ' + gid.slice(0, 8));
        var avatar = normalizeAvatarUrlSchedules(group.fullAvt || group.avatar || group.grid_fullAvt || group.grid_avatar || '');
        var memberCount = group.memberCount || group.grid_totalMember || group.totalMember || group.total || 0;
        var checked = _personalGroupsSelected.has(gid) ? 'checked' : '';

        var safeGid = escapeHtmlSchedules(gid);
        var safeName = escapeHtmlSchedules(name);
        var safeAvatar = escapeHtmlSchedules(avatar);

        var avatarHtml = avatar
            ? "<img src=\"" + safeAvatar + "\" class=\"recipient-avatar\" onclick=\"showAvatarOverlay('" + safeAvatar + "', '" + safeName + "'); event.stopPropagation();\" style=\"cursor:pointer;\">"
            : "<div class=\"recipient-avatar\" style=\"background:rgba(88,166,255,0.2);display:flex;align-items:center;justify-content:center;\">👥</div>";

        return ""
            + "<tr class=\"sched-personal-group-row\" style=\"cursor:pointer\" onclick=\"schedTogglePersonalGroupSelection('" + safeGid + "')\">"
            + "<td style=\"text-align:center\">"
            + "<input type=\"checkbox\" class=\"sched-personal-group-checkbox\" " + checked + " onchange=\"schedTogglePersonalGroupSelection('" + safeGid + "'); event.stopPropagation();\" onclick=\"event.stopPropagation();\">"
            + "</td>"
            + "<td>" + avatarHtml + "</td>"
            + "<td style=\"font-weight:600\">" + safeName + "</td>"
            + "<td class=\"copy-group-id\" style=\"font-size:11px;font-family:monospace;opacity:.9;cursor:pointer;text-decoration:underline;\" title=\"Click để copy Group ID: " + safeGid + "\" onclick=\"schedCopyPersonalGroupId('" + safeGid + "', event)\">" + safeGid + "</td>"
            + "<td>" + escapeHtmlSchedules(String(memberCount)) + "</td>"
            + "<td style=\"text-align:center\"><button class=\"btn btn-sm btn-primary\" onclick=\"schedShowPersonalGroupDetail('" + safeGid + "'); event.stopPropagation();\">Xem</button></td>"
            + "</tr>";
    }).join('');

    var cnt = document.getElementById('schedPersonalGroupSelectedCount');
    if (cnt) cnt.textContent = _personalGroupsSelected.size;

    var checkedAll = _personalGroupsSelected.size === _personalGroups.length && _personalGroups.length > 0;
    var partial = _personalGroupsSelected.size > 0 && _personalGroupsSelected.size < _personalGroups.length;

    if (selectAll) {
        selectAll.checked = checkedAll;
        selectAll.indeterminate = partial;
    }

    if (headerCheckbox) {
        headerCheckbox.checked = checkedAll;
        headerCheckbox.indeterminate = partial;
        headerCheckbox.onchange = function() {
            schedTogglePersonalGroupSelectAll(headerCheckbox);
        };
    }
}


function schedTogglePersonalGroupSelection(groupId) {
    if (_personalGroupsSelected.has(groupId)) _personalGroupsSelected.delete(groupId); else _personalGroupsSelected.add(groupId);
    schedRenderPersonalGroupsTable();
}
function schedTogglePersonalGroupSelectAll(cb) {
    if (cb.checked) _personalGroups.forEach(function(g) { _personalGroupsSelected.add(String(g.groupId || g.gridId || g.id || g.gid || '').trim()); }); else _personalGroupsSelected.clear();
    schedRenderPersonalGroupsTable();
}
function schedRefreshPersonalGroups() { schedLoadPersonalGroupsForAccount(); }

function schedShowPersonalGroupDetail(groupId) {
    var groupInfo = _personalGroups.find(function(g) { return g.groupId === groupId; });
    if (!groupInfo) { console.warn('Không tìm thấy nhóm:', groupId); return; }
    var bd = document.getElementById('schedAccountDetailBackdrop');
    if (bd) { bd.classList.add('visible'); bd.style.display = 'flex'; }
    document.getElementById('schedAcctName').textContent = groupInfo.name || 'Không xác định';
    document.getElementById('schedAcctId').textContent = groupId;
    var av = normalizeAvatarUrlSchedules(groupInfo.fullAvt || groupInfo.avatar || '');
    var ael = document.getElementById('schedAcctAvatar');
    if (av) {
        ael.src = av; ael.style.display = 'block'; ael.style.cursor = 'pointer'; ael.title = 'Click xem ảnh lớn';
        document.getElementById('schedAcctAvatarPlaceholder').style.display = 'none';
        ael.onclick = function() { showAvatarOverlay(av, groupInfo.name || 'Nhóm'); };
    } else { ael.style.display = 'none'; document.getElementById('schedAcctAvatarPlaceholder').style.display = 'flex'; }
    document.getElementById('schedAcctFields').innerHTML = '<div class="pf-field"><span class="pf-label">ID Nhóm</span><span class="pf-value" style="font-family:monospace;font-size:11px">' + escapeHtmlSchedules(groupId) + '</span></div>' +
        '<div class="pf-field"><span class="pf-label">Tổng thành viên</span><span class="pf-value">' + (groupInfo.memberCount || 0) + '</span></div>';
}

// ─── NOTIFICATION ──────────────────────────────────────────────────────
function schedShowNotif(title, message, type) {
    type = type || 'info';
    var card = document.getElementById('notificationOverlay');
    if (!card) return;
    card.className = 'notification-overlay ' + type;
    document.getElementById('notifTitle').textContent = title;
    document.getElementById('notifMessage').textContent = message;
    card.classList.add('show');
    clearTimeout(card._timeout);
    card._timeout = setTimeout(function() { card.classList.remove('show'); }, 3000);
}
function schedHideNotif() {
    var n = document.getElementById('notificationOverlay');
    if (n) n.classList.remove('show');
}

// ─── LOCALSTORAGE FORM STATE ───────────────────────────────────────────
// Mỗi trang (nhóm/SĐT/nhóm cá nhân/danh sách lịch) giờ là 1 trang riêng, chỉ có
// DOM của chính nó — chỉ đọc/ghi field nào thực sự tồn tại trên trang hiện tại,
// giữ nguyên giá trị đã lưu của các trang khác trong cùng 1 object localStorage.
var SCHED_FORM_FIELD_MAP = {
    schedGroupTitle: 'groupTitle',
    schedGroupMessage: 'groupMessage',
    schedGroupDateTime: 'groupRunAt',
    schedGroupInput: 'groupInput',
    schedPhoneTitle: 'phoneTitle',
    schedPhoneMessage: 'phoneMessage',
    schedPhoneDateTime: 'phoneRunAt',
    phoneInput: 'phoneInput',
    schedPersonalGroupTitle: 'pgTitle',
    schedPersonalGroupMessage: 'pgMessage',
    schedPersonalGroupRunAt: 'pgRunAt'
};
function schedSaveFormState() {
    try {
        var st = {};
        try { st = JSON.parse(localStorage.getItem(STORAGE_SCHED_FORM) || '{}') || {}; } catch (e2) { st = {}; }
        st.activeTab = (document.querySelector('.tab-btn.active') || {}).dataset ? document.querySelector('.tab-btn.active').dataset.tab || 'tab-group' : 'tab-group';
        Object.keys(SCHED_FORM_FIELD_MAP).forEach(function (id) {
            var el = document.getElementById(id);
            if (el) st[SCHED_FORM_FIELD_MAP[id]] = el.value || '';
        });
        localStorage.setItem(STORAGE_SCHED_FORM, JSON.stringify(st));
    } catch (e) {}
}
function schedRestoreFormState() {
    try {
        var raw = localStorage.getItem(STORAGE_SCHED_FORM);
        if (!raw) return;
        var st = JSON.parse(raw);
        Object.keys(SCHED_FORM_FIELD_MAP).forEach(function (id) {
            var key = SCHED_FORM_FIELD_MAP[id];
            var el = document.getElementById(id);
            if (el && st[key]) el.value = st[key];
        });
        var tabMap = { 'tab-group': 'group', 'tab-phone': 'phone', 'tab-personal-groups': 'personal-groups' };
        schedSwitchTab(tabMap[st.activeTab] || 'group');
    } catch (e) {}
}

// ─── INIT ───────────────────────────────────────────────────────────────
var _schedSaveTimer = null;
function schedScheduleSave() { clearTimeout(_schedSaveTimer); _schedSaveTimer = setTimeout(schedSaveFormState, 500); }
window.addEventListener('beforeunload', schedSaveFormState);

document.addEventListener('DOMContentLoaded', function() {
    setTimeout(function() {
        var inputs = document.querySelectorAll('#tab-group input, #tab-group textarea, #tab-phone input, #tab-phone textarea, #tab-personal-groups input, #tab-personal-groups textarea');
        inputs.forEach(function(el) { el.addEventListener('input', schedScheduleSave); el.addEventListener('change', schedScheduleSave); });
        schedRestoreFormState();
        // Restore phone lookup results from storage
        _phoneLookupResults = getPhoneLookupFromStorage();
        if (_phoneLookupResults.length) {
            schedRenderPhoneResultsTable();
            var section = document.getElementById('phoneResultsSection');
            if (section) section.style.display = 'block';
        }
    }, 300);
});

window.addEventListener('load', function() {
    schedLoadAccounts();
    var cb = document.getElementById('schedAcctCloseBtn');
    if (cb) {
        cb.addEventListener('click', function(e) {
            e.stopPropagation();
            var bd = document.getElementById('schedAccountDetailBackdrop');
            if (bd) { bd.classList.remove('visible', 'show'); bd.style.display = 'none'; }
        });
    }
});


function schedOpenMemberAvatar(idx) {
    if (idx < 0 || idx >= _schedMembers.length) return;
    var m = _schedMembers[idx] || {};
    var aid = (document.getElementById('schedGroupAccountId') || {}).value || '';
    schedOpenUserAvatar(
        m.userId || m.uid || m.id || '',
        m.zaloName || m.displayName || m.userId || 'Avatar',
        m.avatar || '',
        aid
    );
}

function schedOpenPhoneAvatar(idx) {
    if (idx < 0 || idx >= _phoneLookupResults.length) return;
    var r = _phoneLookupResults[idx] || {};
    var aid = (document.getElementById('schedPhoneAccountId') || {}).value || '';
    schedOpenUserAvatar(
        r.userId || r.uid || r.id || '',
        r.zaloName || r.displayName || r.phone || 'Avatar',
        r.avatar || '',
        aid
    );
}

function schedBuildMemberDetailHtml(m, loadingText) {
    m = m || {};
    var html = '';
    var uid = m.userId || m.uid || m.id || '';

    html += '<div class="detail-item"><div class="detail-label">Tên:</div><div class="detail-value">' +
        escapeHtmlSchedules(m.zaloName || m.displayName || m.globalId || uid || '-') +
        '</div></div>';

    html += '<div class="detail-item"><div class="detail-label">User ID:</div><div class="detail-value" style="font-family:monospace; word-break:break-all;">' +
        escapeHtmlSchedules(uid || '-') +
        '</div></div>';

    if (m.phoneNumber || m.phone) {
        html += '<div class="detail-item"><div class="detail-label">Số ĐT:</div><div class="detail-value" style="font-family:monospace;">' +
            escapeHtmlSchedules(m.phoneNumber || m.phone) +
            '</div></div>';
    }

    if (m.globalId || m.username) {
        html += '<div class="detail-item"><div class="detail-label">Global ID / Username:</div><div class="detail-value" style="font-family:monospace;">' +
            escapeHtmlSchedules(m.globalId || m.username) +
            '</div></div>';
    }

    if (m.sdob) {
        html += '<div class="detail-item"><div class="detail-label">Ngày sinh:</div><div class="detail-value">' +
            escapeHtmlSchedules(m.sdob) +
            '</div></div>';
    }

    if (m.gender !== undefined && m.gender !== null && String(m.gender) !== '') {
        var genderText = String(m.gender);
        if (Number(m.gender) === 0) genderText = 'Không rõ';
        if (Number(m.gender) === 1) genderText = 'Nam';
        if (Number(m.gender) === 2) genderText = 'Nữ';
        html += '<div class="detail-item"><div class="detail-label">Giới tính:</div><div class="detail-value">' +
            escapeHtmlSchedules(genderText) +
            '</div></div>';
    }

    if (m.status) {
        html += '<div class="detail-item"><div class="detail-label">Status:</div><div class="detail-value">' +
            escapeHtmlSchedules(m.status) +
            '</div></div>';
    }

    html += '<div class="detail-item"><div class="detail-label">Trạng thái:</div><div class="detail-value">' +
        (Number(m.accountStatus || 0) === 0 ? '✓ Hoạt động' : '✗ Khóa') +
        '</div></div>';

    if (loadingText) {
        html += '<div class="detail-item"><div class="detail-label">get_single_profile:</div><div class="detail-value">' +
            escapeHtmlSchedules(loadingText) +
            '</div></div>';
    }

    return html;
}

function schedRenderMemberDetailModal(idx, loadingText) {
    if (idx < 0 || idx >= _schedMembers.length) return;
    var m = _schedMembers[idx] || {};
    var uid = m.userId || m.uid || m.id || '';

    document.getElementById('detailTitle').textContent =
        'Chi tiết thông tin - ' + (m.zaloName || m.displayName || m.globalId || uid || 'Người dùng');

    document.getElementById('detailContent').innerHTML = schedBuildMemberDetailHtml(m, loadingText);

    var da = document.getElementById('detailAvatar');
    if (m.avatar) {
        da.src = m.avatar;
        da.style.display = 'block';
        da.style.cursor = 'zoom-in';
        da.title = 'Click để lấy ảnh full size';
        da.onclick = function(e) {
            e.stopPropagation();
            schedOpenMemberAvatar(idx);
        };
    } else {
        da.style.display = 'none';
        da.onclick = null;
    }
}

function schedFetchSingleProfileForMember(idx) {
    if (idx < 0 || idx >= _schedMembers.length) return;

    var m = _schedMembers[idx] || {};
    var uid = String(m.userId || m.uid || m.id || '').trim();
    var aid = String((document.getElementById('schedGroupAccountId') || {}).value || '').trim();

    if (!uid || !aid) return;

    schedRenderMemberDetailModal(idx, 'Đang gọi get_single_profile cho UID này...');

    fetch('/api/single-profile', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            accountId: aid,
            account_id: aid,
            uid: uid
        })
    })
    .then(function(r) { return r.json(); })
    .then(function(j) {
        if (j.error || !j.success || !j.profile) {
            schedRenderMemberDetailModal(idx, j.error || 'Không lấy được profile chi tiết');
            return;
        }

        var p = j.profile || {};
        var merged = Object.assign({}, _schedMembers[idx] || {}, p);

        merged.userId = p.userId || p.uid || p.id || uid;
        merged.zaloName = p.zaloName || p.displayName || merged.zaloName || merged.displayName || '';
        merged.displayName = p.displayName || merged.displayName || '';
        merged.avatar = p.avatar || merged.avatar || '';
        merged.phoneNumber = p.phoneNumber || p.phone || merged.phoneNumber || '';
        merged.globalId = p.globalId || p.username || merged.globalId || '';
        merged.sdob = p.sdob || merged.sdob || '';

        _schedMembers[idx] = merged;

        schedRenderMemberDetailModal(idx, '');
        schedRenderMembersTable();
    })
    .catch(function(err) {
        schedRenderMemberDetailModal(idx, 'Lỗi get_single_profile: ' + (err.message || String(err)));
    });
}

// ─── DETAIL MODAL FUNCTIONS ───────────────────────────────────────
function showMemberDetail(idx) {
    if (idx < 0 || idx >= _schedMembers.length) return;

    // Mở popup bằng data hiện có trước.
    schedRenderMemberDetailModal(idx, 'Đang gọi get_single_profile cho UID này...');
    document.getElementById('detailModal').classList.add('show');

    // Click Xem luôn gọi /api/single-profile cho đúng UID, để logic xem chi tiết giống kỳ vọng.
    schedFetchSingleProfileForMember(idx);
}

function showPhoneDetail(idx) {
    if (idx < 0 || idx >= _phoneLookupResults.length) return;
    var r = _phoneLookupResults[idx];
    var html = '';
    html += '<div class="detail-item"><div class="detail-label">Tên Zalo:</div><div class="detail-value">' + escapeHtmlSchedules(r.zaloName || '-') + '</div></div>';
    if (r.userId) html += '<div class="detail-item"><div class="detail-label">User ID:</div><div class="detail-value" style="font-family:monospace; word-break:break-all;">' + escapeHtmlSchedules(r.userId) + '</div></div>';
    if (r.phone) html += '<div class="detail-item"><div class="detail-label">Số ĐT:</div><div class="detail-value" style="font-family:monospace;">' + escapeHtmlSchedules(r.phone) + '</div></div>';
    if (r.globalId) html += '<div class="detail-item"><div class="detail-label">Global ID:</div><div class="detail-value" style="font-family:monospace;">' + escapeHtmlSchedules(r.globalId) + '</div></div>';
    html += '<div class="detail-item"><div class="detail-label">Trạng thái:</div><div class="detail-value">' + (r.success ? '✓ Thành công' : '✗ Thất bại') + '</div></div>';
    document.getElementById('detailTitle').textContent = 'Chi tiết thông tin - ' + escapeHtmlSchedules(r.zaloName || r.phone || 'Người dùng');
    document.getElementById('detailContent').innerHTML = html;
    if (r.avatar) {
        var da2 = document.getElementById('detailAvatar');
        da2.src = r.avatar;
        da2.style.display = 'block';
        da2.style.cursor = 'zoom-in';
        da2.title = 'Click de lay anh full size';
        da2.onclick = function(e) {
            e.stopPropagation();
            schedOpenUserAvatar(r.userId || r.uid || r.id, r.zaloName || r.displayName || r.phone || 'Avatar', r.avatar, document.getElementById('schedPhoneAccountId') ? document.getElementById('schedPhoneAccountId').value : '');
        };
    } else {
        document.getElementById('detailAvatar').style.display = 'none';
    }
    document.getElementById('detailModal').classList.add('show');
}

function closeDetailModal() {
    document.getElementById('detailModal').classList.remove('show');
}

function enlargeAvatar(src) {
    document.getElementById('avatarImage').src = src;
    document.getElementById('avatarModal').classList.add('show');
}

function closeAvatarModal() {
    document.getElementById('avatarModal').classList.remove('show');
}


// === TODAY DATETIME PATCH START ===
(function() {
    function pad2(n) {
        return String(n).padStart(2, '0');
    }

    function nowDateTimeLocal() {
        var d = new Date();
        return (
            d.getFullYear() + '-' +
            pad2(d.getMonth() + 1) + '-' +
            pad2(d.getDate()) + 'T' +
            pad2(d.getHours()) + ':' +
            pad2(d.getMinutes())
        );
    }

    function setNow(inputId) {
        var el = document.getElementById(inputId);
        if (!el) return;

        el.value = nowDateTimeLocal();
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));

        if (typeof schedShowNotif === 'function') {
            schedShowNotif('Đã chọn hôm nay', 'Đã đặt ngày giờ về thời điểm hiện tại.', 'success');
        }
    }

    function initTodayButtons() {
        var ids = [
            'schedGroupDateTime',
            'schedPhoneDateTime',
            'schedPersonalGroupRunAt'
        ];

        ids.forEach(function(id) {
            var el = document.getElementById(id);
            if (!el) return;

            // Khi m? l?ch, n?u ? tr?ng th? t? set v? th?i gian hi?n t?i
            if (!el.value) {
                el.value = nowDateTimeLocal();
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }

            // Tránh tạo lặp nút
            if (document.getElementById(id + '_todayBtn')) return;

            var parent = el.parentNode;
            if (!parent) return;

            var wrap = document.createElement('div');
            wrap.className = 'sched-datetime-row';

            parent.insertBefore(wrap, el);
            wrap.appendChild(el);

            var btn = document.createElement('button');
            btn.type = 'button';
            btn.id = id + '_todayBtn';
            btn.className = 'btn btn-ghost btn-sm sched-today-btn';
            btn.textContent = 'Hôm nay';
            btn.onclick = function() {
                setNow(id);
            };

            wrap.appendChild(btn);
        });

        if (!document.getElementById('schedDateTimeTodayStyle')) {
            var style = document.createElement('style');
            style.id = 'schedDateTimeTodayStyle';
            style.textContent = `
                .sched-datetime-row {
                    display: flex;
                    gap: 8px;
                    align-items: center;
                    width: 100%;
                }
                .sched-datetime-row input[type="datetime-local"] {
                    flex: 1;
                    min-width: 0;
                }
                .sched-today-btn {
                    white-space: nowrap;
                    height: 36px;
                    padding-left: 12px;
                    padding-right: 12px;
                }
            `;
            document.head.appendChild(style);
        }
    }

    function boot() {
        initTodayButtons();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function() {
            setTimeout(boot, 300);
            setTimeout(boot, 1000);
        });
    } else {
        setTimeout(boot, 300);
        setTimeout(boot, 1000);
    }

    // N?u tab/l?ch render mu?n th? v?n b?t ???c
    document.addEventListener('click', function() {
        setTimeout(boot, 100);
    });
})();
// === TODAY DATETIME PATCH END ===


// ZALO_INITIAL_TAB_PATCH: mở đúng trang tính năng chuyên biệt từ menu trái
(function(){
    function bootInitialTab(){
        if (window.ZALO_INITIAL_TAB && typeof schedSwitchTab === 'function') {
            schedSwitchTab(window.ZALO_INITIAL_TAB);
        }
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function(){ setTimeout(bootInitialTab, 80); });
    } else {
        setTimeout(bootInitialTab, 80);
    }
})();
