function escapeJsStringMembers(str) {
    if (str === null || str === undefined) return '';
    return String(str).replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '\\"').replace(/\r/g, '\\r').replace(/\n/g, '\\n');
}

// Helper: Escape HTML
function escapeHtmlMembers(str) {
    return String(str || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}


function memberCookieHas(cookieString, cookieName) {
    var target = String(cookieName || '').toLowerCase();
    return String(cookieString || '').split(';').some(function(part) {
        var idx = part.indexOf('=');
        if (idx < 0) return false;
        return part.slice(0, idx).trim().toLowerCase() === target && part.slice(idx + 1).trim().length > 0;
    });
}

function memberAccountSessionReady(acc, requireImei) {
    if (!acc || !acc.accountId || !acc.loginCaptured || !acc.zpwEnk) return false;
    if (!memberCookieHas(acc.cookies, 'zpw_sek')) return false;
    return requireImei ? !!acc.imei : true;
}


// UX: thông báo nổi + trạng thái thân thiện trên trang lấy thành viên
var _memberLastToastKey = '';
var _memberLastToastAt = 0;

function stripMemberHtml(input) {
    var div = document.createElement('div');
    div.innerHTML = String(input || '');
    return (div.textContent || div.innerText || '').trim();
}

function ensureMemberToastStack() {
    var stack = document.getElementById('memberToastStack');
    if (!stack) {
        stack = document.createElement('div');
        stack.id = 'memberToastStack';
        stack.className = 'member-toast-stack';
        stack.setAttribute('aria-live', 'polite');
        stack.setAttribute('aria-atomic', 'true');
        document.body.appendChild(stack);
    }
    return stack;
}

function showMemberToast(message, type) {
    var text = stripMemberHtml(message);
    if (!text) return;

    type = type || 'info';
    var key = type + ':' + text;
    var now = Date.now();
    if (_memberLastToastKey === key && now - _memberLastToastAt < 1200) return;
    _memberLastToastKey = key;
    _memberLastToastAt = now;

    var stack = ensureMemberToastStack();
    var toast = document.createElement('div');
    toast.className = 'member-toast ' + type;

    var icon = type === 'success' ? '✓' : (type === 'error' ? '!' : (type === 'warning' || type === 'warn' ? '⚠' : 'i'));
    toast.innerHTML = '<span class="member-toast-icon">' + icon + '</span><span class="member-toast-text"></span><button type="button" class="member-toast-close" aria-label="Đóng">×</button>';
    toast.querySelector('.member-toast-text').textContent = text;

    function removeToast() {
        toast.classList.add('hiding');
        setTimeout(function() {
            if (toast.parentNode) toast.parentNode.removeChild(toast);
        }, 220);
    }

    toast.querySelector('.member-toast-close').addEventListener('click', removeToast);
    stack.appendChild(toast);
    setTimeout(removeToast, type === 'error' ? 5200 : 3600);
}

function setStatus(msg, type) {
    var el = document.getElementById('statusBar');
    type = type || 'info';
    if (el) {
        el.innerHTML = (type === 'loading' ? '<span class="spinner"></span>' : '') + escapeHtmlMembers(stripMemberHtml(msg));
        el.className = 'status-bar members-inline-status ' + type;
        el.style.display = 'block';
    }
    if (type && type !== 'loading') {
        showMemberToast(msg, type === 'warn' ? 'warning' : type);
    }
}

function updateMemberActionState() {
    var hasData = Array.isArray(_lastFetchedData) && _lastFetchedData.length > 0;
    var createBtn = document.getElementById('createGroupBtn');
    var addBtn = document.getElementById('addFriendBtn');
    var inviteBtn = document.getElementById('inviteGroupBtn');
    var clearBtn = document.getElementById('clearMembersBtn');
    var selectAll = document.getElementById('selectAllMembers');
    var groupInfoBtn = document.getElementById('memberGroupInfoBtn');
    var hasGroupInfo = !!(_savedGroupInfo && (_savedGroupInfo.name || _savedGroupInfo.groupId));

    [createBtn, inviteBtn, addBtn].forEach(function(btn) {
        if (!btn) return;
        btn.disabled = !hasData;
        btn.classList.toggle('is-disabled', !hasData);
        btn.title = hasData ? '' : 'Hãy lấy danh sách thành viên trước';
    });

    if (clearBtn) {
        clearBtn.style.display = hasData ? 'inline-flex' : 'none';
        clearBtn.disabled = !hasData;
    }
    if (selectAll) {
        selectAll.disabled = !hasData;
        selectAll.checked = false;
    }
    if (groupInfoBtn) {
        groupInfoBtn.disabled = !hasGroupInfo;
        groupInfoBtn.classList.toggle('is-disabled', !hasGroupInfo);
        groupInfoBtn.title = hasGroupInfo ? '' : 'Chưa có thông tin nhóm';
    }
}

// Helper: Normalize avatar URL
function normalizeAvatarUrlMembers(url) {
    if (!url) return "";
    if (url.startsWith("//")) return "https:" + url;
    return url;
}


function hasMemberValue(v) {
    return v !== undefined && v !== null && v !== '' && v !== '-';
}

function getMemberGenderValue(member) {
    if (!member) return null;
    if (hasMemberValue(member.gender)) return member.gender;
    if (hasMemberValue(member.sex)) return member.sex;
    if (hasMemberValue(member.genderType)) return member.genderType;
    return null;
}

function formatMemberGender(value) {
    if (!hasMemberValue(value)) return '-';
    if (typeof value === 'string') {
        var normalized = value.trim().toLowerCase();
        if (!normalized || normalized === '-' || normalized === 'unknown' || normalized === 'null' || normalized === 'undefined') return '-';
        if (normalized === 'nam' || normalized === 'male' || normalized === 'm') return 'Nam';
        if (normalized === 'nữ' || normalized === 'nu' || normalized === 'female' || normalized === 'f') return 'Nữ';
    }
    var n = Number(value);
    if (Number.isNaN(n)) return String(value);
    // Theo response profile hiện tại của Zalo Web trong tool này: 0 = Nam, 1 = Nữ.
    // Một số endpoint cũ có thể trả 2 = Nữ, nên vẫn hỗ trợ để tránh hiện sai.
    if (n === 0) return 'Nam';
    if (n === 1 || n === 2) return 'Nữ';
    return 'Không xác định';
}

async function loadMemberAccounts() {
    try {
        const resp = await fetch('/api/accounts');
        const json = await resp.json();
        const accounts = json.accounts || [];

        const container = document.getElementById('memberAccountDropdown');
        if (!container) return;

        const hiddenInput = document.getElementById('memberAccountId');

        function getAccountId(acc) {
            return String((acc && (acc.accountId || acc.id || acc.account_id)) || '').trim();
        }

        function findAccountById(accountId) {
            accountId = String(accountId || '').trim();
            if (!accountId) return null;
            return accounts.find(function(a) { return getAccountId(a) === accountId; }) || null;
        }

        function getStoredMemberAccountId() {
            try { return localStorage.getItem('zalo_members_accountId') || ''; } catch (e) { return ''; }
        }

        function setStoredMemberAccountId(accountId) {
            try { localStorage.setItem('zalo_members_accountId', accountId || ''); } catch (e) {}
        }

        var currentId = String((hiddenInput && hiddenInput.value) || getStoredMemberAccountId() || '').trim();
        var selectedAcc = findAccountById(currentId) || accounts.find(function(acc) { return memberAccountSessionReady(acc, false); }) || accounts[0] || null;
        if (selectedAcc && !memberAccountSessionReady(selectedAcc, false)) {
            selectedAcc = accounts.find(function(acc) { return memberAccountSessionReady(acc, false); }) || selectedAcc;
        }

        function renderSelected(acc) {
            if (!acc) {
                return `
                    <button type="button" class="account-dropdown-button">
                        <span>Chọn tài khoản</span>
                    </button>
                `;
            }

            var id = getAccountId(acc);
            var avatar = normalizeAvatarUrlMembers(acc.avatarUrl || acc.avatar || "");
            var name = acc.name || acc.zaloName || acc.displayName || acc.phone || id || "Không tên";

            return `
                <button type="button" class="account-dropdown-button" title="${escapeHtmlMembers(id)}">
                    ${avatar ? `<img src="${escapeHtmlMembers(avatar)}" class="account-dropdown-avatar" />` : `<span class="account-dropdown-avatar placeholder"></span>`}
                    <span class="account-dropdown-name">${escapeHtmlMembers(name)}</span>
                </button>
            `;
        }

        function renderMenu(sel) {
            return `
                <div class="account-dropdown-menu hidden">
                    ${accounts.map(acc => {
                        var avatar = normalizeAvatarUrlMembers(acc.avatarUrl || acc.avatar || "");
                        var name = acc.name || acc.zaloName || acc.displayName || acc.phone || getAccountId(acc) || "Không tên";
                        var id = getAccountId(acc);
                        var ready = memberAccountSessionReady(acc, false);
                        var missing = [];
                        if (!acc.loginCaptured) missing.push('phiên đăng nhập');
                        if (!acc.zpwEnk) missing.push('zpwEnk');
                        if (!memberCookieHas(acc.cookies, 'zpw_sek')) missing.push('zpw_sek');
                        var active = sel && getAccountId(sel) === id ? ' active' : '';

                        return `
                            <button
                                type="button"
                                class="account-dropdown-item ${!ready ? 'disabled' : ''}${active}"
                                data-account-id="${escapeHtmlMembers(id)}"
                                ${!ready ? 'disabled' : ''}
                            >
                                ${avatar ? `<img src="${escapeHtmlMembers(avatar)}" class="account-dropdown-avatar" />` : `<span class="account-dropdown-avatar placeholder"></span>`}
                                <span>
                                    <b>${escapeHtmlMembers(name)}</b><br>
                                    <small>${!ready ? 'thiếu: ' + missing.join(', ') : id}</small>
                                </span>
                            </button>
                        `;
                    }).join("")}
                </div>
            `;
        }

        function clearMemberScanDataForAccountChange() {
            _lastFetchedData = [];
            _membersCache = {};
            _savedGroupInfo = null;
            var body = document.getElementById('resultsBody');
            if (body) body.innerHTML = '';
            var total = document.getElementById('totalCount');
            if (total) total.textContent = '0';
            var empty = document.getElementById('emptyState');
            if (empty) empty.style.display = 'flex';
            var clearBtn = document.getElementById('clearMembersBtn');
            if (clearBtn) clearBtn.style.display = 'none';
            updateMemberActionState();
        }

        function bindDropdown(sel) {
            container.innerHTML = renderSelected(sel) + renderMenu(sel);
            var button = container.querySelector(".account-dropdown-button");
            var menu = container.querySelector(".account-dropdown-menu");

            if (sel && hiddenInput) {
                hiddenInput.value = getAccountId(sel);
                setStoredMemberAccountId(hiddenInput.value);
            }

            button.addEventListener("click", function(e) {
                e.stopPropagation();
                menu.classList.toggle("hidden");
            });

            container.querySelectorAll(".account-dropdown-item:not([disabled])").forEach(item => {
                item.addEventListener("click", function(e) {
                    e.stopPropagation();
                    var accountId = String(this.dataset.accountId || '').trim();
                    var acc = findAccountById(accountId);
                    menu.classList.add("hidden");

                    if (acc && hiddenInput) {
                        hiddenInput.value = accountId;
                        setStoredMemberAccountId(accountId);
                        clearMemberScanDataForAccountChange();
                        bindDropdown(acc);
                    }
                });
            });
        }

        bindDropdown(selectedAcc);

        if (!container._memberDropdownOutsideClickBound) {
            document.addEventListener("click", function(e) {
                if (!container.contains(e.target)) {
                    var menu = container.querySelector(".account-dropdown-menu");
                    if (menu) menu.classList.add("hidden");
                }
            });
            container._memberDropdownOutsideClickBound = true;
        }

    } catch(e) {
        console.error('Không load được tài khoản:', e);
    }
}

// Members page JS - uses server-side config file with real-time log streaming

var _es = null;
var _logCallback = null;
var _lastFetchedData = []; // lưu kết quả quét để dùng cho tạo nhóm
var _membersCache = {};    // uid -> full member data, dùng cho profile overlay
var _savedGroupInfo = null; // group info lưu để khôi phục khi reload

// ─── localStorage persistence ────────────────────────────────────────────────

function saveMembersToStorage() {
    try {
        localStorage.setItem('zalo_members_data', JSON.stringify(_lastFetchedData));
        localStorage.setItem('zalo_members_cache', JSON.stringify(_membersCache));
        if (_savedGroupInfo) {
            localStorage.setItem('zalo_members_groupInfo', JSON.stringify(_savedGroupInfo));
        }
        var accountId = document.getElementById('memberAccountId') ? document.getElementById('memberAccountId').value : '';
        var groupLink = document.getElementById('groupLinkInput') ? document.getElementById('groupLinkInput').value : '';
        localStorage.setItem('zalo_members_accountId', accountId);
        localStorage.setItem('zalo_members_groupLink', groupLink);
    } catch(e) {
        console.warn('Không lưu được members vào localStorage:', e);
    }
}

function loadMembersFromStorage() {
    try {
        var data = localStorage.getItem('zalo_members_data');
        if (data) {
            _lastFetchedData = JSON.parse(data);
            _membersCache = JSON.parse(localStorage.getItem('zalo_members_cache') || '{}');
            _savedGroupInfo = JSON.parse(localStorage.getItem('zalo_members_groupInfo') || 'null');
            return true;
        }
    } catch(e) {
        console.warn('Không đọc được members từ localStorage:', e);
    }
    return false;
}

function setLogCallback(cb) {
    _logCallback = cb;
}

function connectLogStream() {
    if (_es) {
        _es.close();
    }
    _es = new EventSource('/api/log-stream');
    _es.onmessage = function(e) {
        try {
            var data = JSON.parse(e.data);
            if (data.msg && _logCallback) {
                _logCallback(data.msg, data.type);
            }
        } catch(ex) {}
    };
    _es.onerror = function() {
        // Silently handle errors - will auto-reconnect
    };
}

function disconnectLogStream() {
    if (_es) {
        _es.close();
        _es = null;
    }
}


// ─── Right side Activity Drawer ─────────────────────────────────────────────────
function openMemberLogDrawer() {
    var drawer = document.getElementById('memberLogDrawer');
    var backdrop = document.getElementById('memberLogBackdrop');
    if (drawer) {
        drawer.classList.add('open');
        drawer.setAttribute('aria-hidden', 'false');
    }
    if (backdrop) backdrop.classList.add('open');
    document.body.classList.add('log-drawer-open');
}

function closeMemberLogDrawer() {
    var drawer = document.getElementById('memberLogDrawer');
    var backdrop = document.getElementById('memberLogBackdrop');
    if (drawer) {
        drawer.classList.remove('open');
        drawer.setAttribute('aria-hidden', 'true');
    }
    if (backdrop) backdrop.classList.remove('open');
    document.body.classList.remove('log-drawer-open');
}

function isMemberLogDrawerOpen() {
    var drawer = document.getElementById('memberLogDrawer');
    return !!(drawer && drawer.classList.contains('open'));
}

function toggleMemberLogDrawer() {
    if (isMemberLogDrawerOpen()) closeMemberLogDrawer();
    else openMemberLogDrawer();
}

function setMemberLogMini(text, type) {
    var mini = document.getElementById('memberLogMini');
    if (!mini) return;
    mini.textContent = text || 'xem';
    mini.className = type ? ('log-mini-' + type) : '';
}

function appendMemberLogLine(msg, type) {
    var logOutput = document.getElementById('logOutput');
    if (!logOutput) return;
    var empty = logOutput.querySelector('.log-empty-note');
    if (empty) empty.remove();
    var line = document.createElement('div');
    line.className = 'log-line ' + (type || 'info');
    line.textContent = msg;
    logOutput.appendChild(line);
    logOutput.scrollTop = logOutput.scrollHeight;
    if (type === 'error') setMemberLogMini('lỗi', 'error');
    else if (type === 'warning' || type === 'warn') setMemberLogMini('cảnh báo', 'warning');
    else if (type === 'success') setMemberLogMini('xong', 'success');
    else setMemberLogMini('đang chạy', 'loading');
}




function setMemberOverlayOpen(backdrop, open) {
    if (!backdrop) return;
    backdrop.classList.toggle('visible', !!open);
    backdrop.classList.toggle('open', !!open);
    backdrop.style.display = open ? 'flex' : 'none';
    backdrop.setAttribute('aria-hidden', open ? 'false' : 'true');
    document.body.classList.toggle('nexus-modal-open', !!open);
}

function openMemberFetchOverlay() {
    var backdrop = document.getElementById('memberFetchBackdrop');
    setMemberOverlayOpen(backdrop, true);
    window.setTimeout(function() {
        var input = document.getElementById('groupLinkInput');
        if (input) input.focus();
    }, 40);
}

function closeMemberFetchOverlay() {
    var backdrop = document.getElementById('memberFetchBackdrop');
    setMemberOverlayOpen(backdrop, false);
}

function openMemberGroupInfoOverlay() {
    if (!_savedGroupInfo || (!_savedGroupInfo.name && !_savedGroupInfo.groupId)) {
        setStatus('Chưa có thông tin nhóm để hiển thị.', 'info');
        return;
    }
    setMemberOverlayOpen(document.getElementById('memberGroupInfoBackdrop'), true);
}

function closeMemberGroupInfoOverlay() {
    setMemberOverlayOpen(document.getElementById('memberGroupInfoBackdrop'), false);
}

async function runFetch() {
    var accountId = document.getElementById('memberAccountId') ? document.getElementById('memberAccountId').value : '';
    var groupLink = document.getElementById('groupLinkInput').value.trim();
    if (!accountId) {
        setStatus('Vui lòng chọn tài khoản thực hiện!', 'error');
        return;
    }

    if (!groupLink) {
        setStatus('Vui lòng nhập Link nhóm Zalo!', 'error');
        return;
    }

    var btn = document.getElementById('runBtn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span>Đang xử lý...';

    // Chuẩn bị lịch sử hoạt động nhưng không tự mở drawer.
    // Người dùng bấm thẻ/nút "Lịch sử hoạt động" để mở, bấm lần 2 để đóng.
    var logCard = document.getElementById('logCard');
    var logOutputEl = document.getElementById('logOutput');
    var logProgressEl = document.getElementById('logProgress');
    if (logCard) logCard.style.display = 'block';
    if (logOutputEl) logOutputEl.innerHTML = '';
    if (logProgressEl) logProgressEl.textContent = 'Đang chạy...';
    setMemberLogMini('đang chạy', 'loading');

    // Clear previous results
    _lastFetchedData = [];
    _membersCache = {};
    document.getElementById('resultsBody').innerHTML = '';
    document.getElementById('totalCount').textContent = '0';
    updateMemberActionState();
    
    // Clear search input
    var searchInput = document.getElementById('searchInput');
    if (searchInput) {
        searchInput.value = '';
    }

    setStatus('Đang lấy danh sách thành viên...', 'loading');

    // Setup log streaming
    setLogCallback(function(msg, type) {
        appendMemberLogLine(msg, type);
    });
    connectLogStream();

    try {
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
            setStatus(json.error, 'error');
            var lpErr = document.getElementById('logProgress');
            if (lpErr) lpErr.textContent = 'Lỗi';
            setMemberLogMini('lỗi', 'error');
            document.getElementById('emptyState').style.display = 'flex';
            updateMemberActionState();
            return;
        }

        if (json.success) {
            var doneMessage = 'Hoàn thành! Lấy được ' + json.total + ' thành viên.';
            setStatus(doneMessage, 'success');
            var lp = document.getElementById('logProgress');
            if (lp) lp.textContent = '\u2713 Hoàn thành';
            setMemberLogMini('xong', 'success');

            _lastFetchedData = json.data || [];

            // Display group info if available
            var groupInfo = json.groupInfo || {};
            if (!groupInfo.groupId) groupInfo.groupId = '';
            if (!groupInfo.name) groupInfo.name = '';
            if (!groupInfo.totalMember) groupInfo.totalMember = _lastFetchedData.length;
            displayGroupInfo(groupInfo);

            // Build cache for profile overlay
            _membersCache = {};
            _lastFetchedData.forEach(function(m) {
                var key = m.userId || m.id || '';
                if (key) {
                    _membersCache[key] = m;
                }
            });

            // Render NGAY kết quả /run trả về, không chờ enrich/single-profile.
            // Tránh tình trạng log báo hoàn thành nhưng bảng vẫn "Chưa có dữ liệu".
            renderResults(_lastFetchedData);
            closeMemberFetchOverlay();
            var memberTableScroll = document.querySelector('.members-table-stage');
            if (memberTableScroll) memberTableScroll.scrollTop = 0;

            // ─── Persist to localStorage ─────────────────────────────────
            _savedGroupInfo = groupInfo;
            saveMembersToStorage();
            // Show clear button
            var clearBtn = document.getElementById('clearMembersBtn');
            if (clearBtn) clearBtn.style.display = 'inline-flex';
            updateMemberActionState();

            // Không gọi /api/single-profile tự động sau khi lấy danh sách.
            // Profile chi tiết chỉ được lấy khi người dùng bấm nút Xem từng thành viên.
            var finalMessage = 'Hoàn thành! Lấy được ' + _lastFetchedData.length + ' thành viên. Bấm Xem để tải chi tiết từng người.';
            setStatus(finalMessage, 'success');
            var lpDone = document.getElementById('logProgress');
            if (lpDone) lpDone.textContent = '\u2713 Hoàn thành';
            setMemberLogMini('xong', 'success');
        }
    } catch(err) {
        setStatus('Lỗi kết nối: ' + err.message, 'error');
        var lpCatch = document.getElementById('logProgress');
        if (lpCatch) lpCatch.textContent = 'Lỗi kết nối';
        setMemberLogMini('lỗi', 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg> Lấy thành viên';
        setTimeout(disconnectLogStream, 2000);
    }
}

function displayGroupInfo(info) {
    var card = document.getElementById('groupInfoCard');
    card.style.display = 'block';

    document.getElementById('groupName').textContent = info.name || '-';
    document.getElementById('groupDesc').textContent = info.desc || 'Không có mô tả';
    document.getElementById('groupId').textContent = info.groupId || '-';
    document.getElementById('totalMember').textContent = info.totalMember || 0;

    var avatar = document.getElementById('groupAvatar');
    var fullAvt = info.fullAvt || info.avt || '';
    if (fullAvt) {
        avatar.src = fullAvt;
        avatar.style.display = 'block';
    } else {
        avatar.style.display = 'none';
    }
    _savedGroupInfo = info || null;
    updateMemberActionState();
}

// ─── Enrich Members with Profile Details ────────────────────────────────────

async function enrichMembersWithProfileData(members, accountId) {
    // Fetch profile details for ALL members in batches (100 UIDs per request)
    // Enriches each member object with gender, sdob, isFr fields.
    if (!members || members.length === 0 || !accountId) {
        return;
    }
    
    var totalMembers = members.length;
    
    // Extract all UIDs
    var allUids = members
        .map(function(member) { return member.userId || member.id; })
        .filter(function(uid) { return !!uid; });
    
    if (allUids.length === 0) {
        console.warn('[enrichMembersWithProfileData] No valid UIDs found');
        return;
    }
    
    var processedCount = 0;
    
    try {
        // Process in batches of 200 UIDs to avoid rate limiting
        for (var i = 0; i < allUids.length; i += 200) {
            var batchUids = allUids.slice(i, i + 200);
            
            setStatus('Đang lấy thông tin chi tiết: ' + processedCount + '/' + totalMembers, 'loading');
            
            // Batch request for 100 UIDs
            var response = await fetch('/api/single-profile', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    uids: batchUids,  // ← Batch of 100 UIDs
                    accountId: accountId 
                })
            });
            
            var result = await response.json();
            
            if (result.error) {
                console.warn('[enrichMembersWithProfileData] Batch API error:', result.error);
                // Use defaults for members in this batch if API error
                batchUids.forEach(function(uid) {
                    var member = members.find(function(m) { return (m.userId || m.id) === uid; });
                    if (member) {
                        if (!hasMemberValue(member.gender)) member.gender = null;
                        member.sdob = member.sdob || '-';
                        member.isFr = member.isFr || 0;
                    }
                });
            } else if (result.profiles) {
                // Merge profile details into member objects
                members.forEach(function(member) {
                    var userId = member.userId || member.id;
                    if (batchUids.indexOf(userId) !== -1) {  // Only process if in this batch
                        var profile = result.profiles[userId];
                        
                        if (profile) {
                            // Copy toàn bộ field API trả về vào member để bảng hiển thị đủ data
                            Object.keys(profile).forEach(function(k) {
                                if (profile[k] !== undefined && profile[k] !== null && profile[k] !== '') {
                                    member[k] = profile[k];
                                }
                            });
                            member.gender = hasMemberValue(member.gender) ? Number(member.gender) : null;
                            member.sdob = member.sdob || '-';
                            member.isFr = Number(member.isFr || 0);
                        } else {
                            // Profile not found, use defaults
                            member.gender = hasMemberValue(member.gender) ? Number(member.gender) : null;
                            member.sdob = member.sdob || '-';
                            member.isFr = Number(member.isFr || 0);
                        }
                    }
                });
            }
            
            processedCount += batchUids.length;

            // Cập nhật bảng theo từng batch để người dùng thấy dữ liệu lên dần.
            if (typeof renderResults === 'function') {
                renderResults(members);
            }
            if (typeof saveMembersToStorage === 'function') {
                saveMembersToStorage();
            }
            
            // Small delay between batches to avoid rate limiting
            if (i + 200 < allUids.length) {
                await new Promise(function(resolve) { setTimeout(resolve, 500); });
            }
        }
        
        setStatus('Hoàn thành lấy thông tin chi tiết!', 'success');
    } catch (err) {
        console.error('[enrichMembersWithProfileData] Fetch error:', err);
        // Use defaults if fetch error
        members.forEach(function(member) {
            if (!hasMemberValue(member.gender)) member.gender = null;
            member.sdob = member.sdob || '-';
            member.isFr = member.isFr || 0;
        });
        setStatus('Lỗi khi lấy thông tin chi tiết', 'error');
    }
    
    setStatus('Hoàn thành lấy thông tin chi tiết!', 'success');
}

function filterResults() {
    var searchInput = document.getElementById('searchInput');
    if (!searchInput) return;
    
    var searchText = (searchInput.value || '').trim().toLowerCase();
    
    // If search is empty, show all results
    if (!searchText) {
        renderResults(_lastFetchedData);
        return;
    }
    
    // Filter members by name
    var filtered = _lastFetchedData.filter(function(member) {
        var name = (member.zaloName || member.name || '').toLowerCase();
        return name.includes(searchText);
    });
    
    // If no results, show message in table instead of empty state
    if (filtered.length === 0) {
        var tbody = document.getElementById('resultsBody');
        var totalCount = document.getElementById('totalCount');
        var resultsSection = document.getElementById('resultsSection');
        
        totalCount.textContent = '0';
        resultsSection.style.display = 'block';
        document.getElementById('emptyState').style.display = 'none';
        
        tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;padding:40px;color:var(--text-secondary);">Không tìm thấy thành viên nào</td></tr>';
        return;
    }
    
    renderResults(filtered);
}



function bindMemberRowClick() {
    var tbody = document.getElementById('resultsBody');
    if (!tbody) return;

    if (tbody.dataset.rowClickBound === '1') return;
    tbody.dataset.rowClickBound = '1';

    tbody.addEventListener('click', function(e) {
        // Không mở profile khi bấm checkbox, button, link, input...
        if (e.target.closest('input, button, a, label, select, textarea')) {
            return;
        }

        var row = e.target.closest('tr[data-member-id]');
        if (!row) return;

        var userId = row.getAttribute('data-member-id');
        if (!userId) return;

        showMemberProfile(userId);
    });
}


// Xác định vai trò trong nhóm: 'owner' (trưởng nhóm) / 'admin' (phó nhóm) / 'member'.
// Ưu tiên groupRole backend đã gắn sẵn; fallback tính từ creatorId/adminIds trong groupInfo
// để dữ liệu cũ lưu trong localStorage vẫn hiển thị đúng.
function getMemberGroupRole(member) {
    if (!member) return 'member';
    if (member.groupRole === 'owner' || member.groupRole === 'admin') return member.groupRole;
    if (member.groupRole === 'member') return 'member';
    var info = _savedGroupInfo || {};
    var uid = String(member.userId || member.id || '');
    if (!uid) return 'member';
    if (info.creatorId && String(info.creatorId) === uid) return 'owner';
    var admins = info.adminIds || [];
    for (var i = 0; i < admins.length; i++) {
        if (String(admins[i]) === uid) return 'admin';
    }
    return 'member';
}

function buildMemberRoleBadge(member) {
    var role = getMemberGroupRole(member);
    if (role === 'owner') {
        return '<span class="member-role-badge" title="Trưởng nhóm (người tạo nhóm)" style="padding:1px 7px;border-radius:999px;font-size:11px;font-weight:600;white-space:nowrap;color:#b45309;background:rgba(245,158,11,0.14);border:1px solid rgba(245,158,11,0.35)">Trưởng nhóm</span>';
    }
    if (role === 'admin') {
        return '<span class="member-role-badge" title="Phó nhóm (quản trị viên)" style="padding:1px 7px;border-radius:999px;font-size:11px;font-weight:600;white-space:nowrap;color:#2563eb;background:rgba(59,130,246,0.12);border:1px solid rgba(59,130,246,0.3)">Phó nhóm</span>';
    }
    return '';
}

// Ô cột "Vai trò": badge cho trưởng/phó nhóm, chữ mờ cho thành viên thường.
function buildMemberRoleCell(member) {
    var badge = buildMemberRoleBadge(member);
    if (badge) return badge;
    return '<span style="font-size:12px;color:var(--text-secondary)">Thành viên</span>';
}

function memberRolePriority(member) {
    var role = getMemberGroupRole(member);
    if (role === 'owner') return 0;
    if (role === 'admin') return 1;
    return 2;
}

// Trưởng nhóm lên đầu, rồi phó nhóm, rồi thành viên (giữ nguyên thứ tự gốc trong cùng vai trò).
function sortMembersByRole(members) {
    return (members || []).slice().sort(function (a, b) {
        return memberRolePriority(a) - memberRolePriority(b);
    });
}

function renderResults(members) {
    var tbody = document.getElementById('resultsBody');
    var totalCount = document.getElementById('totalCount');
    var resultsSection = document.getElementById('resultsSection');
    var emptyState = document.getElementById('emptyState');

    resultsSection.style.display = 'flex';

    if (!members || members.length === 0) {
        tbody.innerHTML = '';
        totalCount.textContent = '0';
        emptyState.style.display = 'flex';
        updateMemberActionState();
        return;
    }

    totalCount.textContent = members.length;
    emptyState.style.display = 'none';

    members = sortMembersByRole(members);

    var html = '';
    members.forEach(function(member) {
        var userId = member.userId || member.id || '';
        var name = member.zaloName || member.displayName || member.dName || member.name || 'Không tên';
        var avatar = normalizeAvatarUrlMembers(member.avatar || member.avt || '');
        var isFr = Number(member.isFr || 0);
        var genderDisplay = formatMemberGender(getMemberGenderValue(member));
        var sdob = member.sdob || member.dob || '-';
        var phoneNumber = member.phoneNumber || member.phone || member.mobile || '-';
        var statusText = member.status || member.accountStatus || '-';

        var friendStatus = isFr === 1 ? 'Đã kết bạn' : 'Chưa';
        var friendColor = isFr === 1 ? 'var(--green)' : 'var(--orange)';
        var friendBgColor = isFr === 1 ? 'rgba(34, 197, 94, 0.1)' : 'rgba(251, 146, 60, 0.1)';

        html += '<tr data-member-id="' + escapeHtmlMembers(userId) + '" style="cursor:pointer">';

        // 1. checkbox
        html += '<td class="member-select-col" style="text-align:center"><input type="checkbox" class="member-checkbox" data-user-id="' + escapeHtmlMembers(userId) + '" style="cursor:pointer"></td>';

        // 2. avatar
        html += '<td class="member-avatar-col">';
        if (avatar) {
            html += '<img src="' + escapeHtmlMembers(avatar) + '" class="avatar-small" style="width:40px;height:40px;border-radius:50%;object-fit:cover" onerror="this.style.display=\'none\'">';
        } else {
            html += '<div style="width:40px;height:40px;border-radius:50%;background:var(--surface2)"></div>';
        }
        html += '</td>';

        // 3. tên - ẩn ID kỹ thuật khỏi bảng chính, vẫn giữ trong popup chi tiết.
        html += '<td class="member-name-col"><div class="member-name-cell" title="Bấm Xem để mở chi tiết"><span class="member-name-text">' + escapeHtmlMembers(name) + '</span></div></td>';

        // 4. vai trò: Trưởng nhóm (creatorId) / Phó nhóm (adminIds) / Thành viên.
        html += '<td class="member-role-col">' + buildMemberRoleCell(member) + '</td>';

        // 5. giới tính
        html += '<td class="member-gender-col">' + escapeHtmlMembers(genderDisplay) + '</td>';

        // 5. ngày sinh
        html += '<td class="member-dob-col"><span style="font-size:12px;color:var(--text-secondary)">' + escapeHtmlMembers(sdob) + '</span></td>';

        // 6. số điện thoại
        html += '<td class="member-phone-col"><span style="font-size:12px;color:var(--text-secondary)">' + escapeHtmlMembers(phoneNumber) + '</span></td>';

        // 7. trạng thái - chỉ hiển thị rút gọn, bấm vào để xem đầy đủ
        html += '<td class="member-status-col">';
        html += '<button type="button" class="member-status-preview" title="Bấm để xem đầy đủ trạng thái" onclick="event.stopPropagation(); openMemberStatusDetail(\'' + escapeJsStringMembers(userId) + '\')">';
        html += '<span class="member-status-text">' + escapeHtmlMembers(statusText) + '</span>';
        html += '</button>';
        html += '</td>';

        // 8. kết bạn
        html += '<td class="member-friend-col"><span class="status-badge" style="color:' + friendColor + ';background-color:' + friendBgColor + ';font-weight:500">' + escapeHtmlMembers(friendStatus) + '</span></td>';

        // 9. xem
        html += '<td class="member-view-col"><button class="btn btn-sm btn-primary" onclick="event.stopPropagation(); showMemberProfile(\'' + escapeHtmlMembers(userId) + '\')" style="cursor:pointer">Xem</button></td>';

        html += '</tr>';
    });

    tbody.innerHTML = html;
    updateMemberActionState();

    bindMemberRowClick();

    // Add event listeners for checkboxes
    var selectAllCheckbox = document.getElementById('selectAllMembers');
    if (selectAllCheckbox) {
        selectAllCheckbox.removeEventListener('change', toggleSelectAll);
        selectAllCheckbox.addEventListener('change', toggleSelectAll);
    }

    document.querySelectorAll('.member-checkbox').forEach(function(checkbox) {
        checkbox.addEventListener('change', updateSelectAllCheckbox);
    });
}

function toggleSelectAll(e) {
    var isChecked = e.target.checked;
    document.querySelectorAll('.member-checkbox').forEach(function(checkbox) {
        checkbox.checked = isChecked;
    });
}

function updateSelectAllCheckbox() {
    var allCheckboxes = document.querySelectorAll('.member-checkbox');
    var checkedCheckboxes = document.querySelectorAll('.member-checkbox:checked');
    var selectAllCheckbox = document.getElementById('selectAllMembers');
    
    if (selectAllCheckbox) {
        selectAllCheckbox.checked = allCheckboxes.length === checkedCheckboxes.length && allCheckboxes.length > 0;
    }
}

function getSelectedMembers() {
    var selectedCheckboxes = document.querySelectorAll('.member-checkbox:checked');
    var selectedMembers = [];
    
    selectedCheckboxes.forEach(function(checkbox) {
        var userId = checkbox.getAttribute('data-user-id');
        var member = _lastFetchedData.find(function(m) { return m.userId === userId || m.id === userId; });
        if (member) {
            selectedMembers.push(member);
        }
    });
    
    return selectedMembers;
}


// ─── Member Status Detail Modal ───────────────────────────────────────────────
function getMemberDisplayName(member) {
    if (!member) return '-';
    return member.zaloName || member.displayName || member.dName || member.name || 'Không tên';
}

function getMemberStatusText(member) {
    if (!member) return '-';
    var status = member.status || member.accountStatus || member.bio || member.description || '-';
    status = String(status || '-').trim();
    return status || '-';
}

function openMemberStatusDetail(userId) {
    var modal = document.getElementById('memberStatusBackdrop');
    if (!modal) return;

    var member = null;
    if (window._lastFetchedData && Array.isArray(window._lastFetchedData)) {
        member = window._lastFetchedData.find(function(m) {
            return String(m.userId || m.id || '') === String(userId || '');
        });
    }

    var nameEl = document.getElementById('memberStatusName');
    var uidEl = document.getElementById('memberStatusUid');
    var textEl = document.getElementById('memberStatusText');

    if (nameEl) nameEl.textContent = getMemberDisplayName(member);
    if (uidEl) uidEl.textContent = userId || '-';
    if (textEl) textEl.textContent = getMemberStatusText(member);

    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
}

function closeMemberStatusDetail() {
    var modal = document.getElementById('memberStatusBackdrop');
    if (!modal) return;
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
}

// ─── Member Profile Modal ────────────────────────────────────────────────────

async function showMemberProfile(userId) {
    var profileBackdrop = document.getElementById('profileBackdrop');
    if (!profileBackdrop) return;
    
    // Try to find member data from _lastFetchedData first (already enriched with batch call)
    var memberData = null;
    if (window._lastFetchedData && Array.isArray(window._lastFetchedData)) {
        memberData = window._lastFetchedData.find(function(m) {
            return (m.userId || m.id) === userId;
        });
    }
    
    // Nếu có data trong bảng thì hiển thị ngay, nhưng vẫn gọi get_single_profile ở dưới
    // để lấy chi tiết mới nhất cho UID được bấm Xem.
    if (memberData) {
        populateProfileModal(memberData);
        profileBackdrop.classList.add('visible');
        profileBackdrop.style.display = 'flex';
    }
    
    // Luôn fetch API chi tiết khi bấm Xem.
    var accountId = document.getElementById('memberAccountId') ? document.getElementById('memberAccountId').value : '';
    if (!accountId) {
        accountId = localStorage.getItem('zalo_members_accountId') || '';
    }
    
    if (!accountId) {
        setStatus('Vui lòng chọn tài khoản!', 'error');
        return;
    }
    
    // Show loading state
    profileBackdrop.classList.add('visible');
    profileBackdrop.style.display = 'flex';
    
    try {
        var response = await fetch('/api/single-profile', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ uid: userId, accountId: accountId })
        });
        
        var result = await response.json();
        
        if (result.error) {
            console.error('[showMemberProfile] API error:', result.error);
            // Nếu bảng đã có dữ liệu từ backend thì giữ popup đang mở, không đóng modal.
            if (memberData) {
                setStatus('Đã hiển thị dữ liệu hiện có. API chi tiết đang bị giới hạn: ' + result.error, 'warning');
                return;
            }
            setStatus('Lỗi: ' + result.error, 'error');
            profileBackdrop.classList.remove('visible');
            profileBackdrop.style.display = 'none';
            return;
        }
        
        if (!result.profile) {
            console.warn('[showMemberProfile] No profile returned');
            if (memberData) {
                setStatus('Đã hiển thị dữ liệu hiện có. Chưa lấy thêm được profile chi tiết.', 'warning');
                return;
            }
            setStatus('Không lấy được profile', 'error');
            profileBackdrop.classList.remove('visible');
            profileBackdrop.style.display = 'none';
            return;
        }
        
        // Merge profile chi tiết vào cache + bảng để dòng vừa bấm cũng cập nhật giới tính/ngày sinh.
        var detailedProfile = Object.assign({}, memberData || {}, result.profile || {});
        detailedProfile.userId = detailedProfile.userId || userId;
        populateProfileModal(detailedProfile);

        if (window._lastFetchedData && Array.isArray(window._lastFetchedData)) {
            for (var i = 0; i < window._lastFetchedData.length; i++) {
                var rowUid = window._lastFetchedData[i].userId || window._lastFetchedData[i].id;
                if (String(rowUid) === String(userId)) {
                    window._lastFetchedData[i] = Object.assign({}, window._lastFetchedData[i], detailedProfile);
                    break;
                }
            }
            renderResults(window._lastFetchedData);
            saveMembersToStorage();
        }
        if (typeof _membersCache !== 'undefined' && _membersCache) {
            _membersCache[userId] = detailedProfile;
        }
        
    } catch (err) {
        console.error('[showMemberProfile] Fetch error:', err);
        setStatus('Lỗi kết nối: ' + err.message, 'error');
        profileBackdrop.classList.remove('visible');
        profileBackdrop.style.display = 'none';
    }
}

function populateProfileModal(profile) {
    // Set avatar
    var pfAvatar = document.getElementById('pfAvatar');
    var pfAvatarPlaceholder = document.getElementById('pfAvatarPlaceholder');
    var avatar = normalizeAvatarUrlMembers(profile.avatar || '');
    
    if (avatar) {
        if (pfAvatar) {
            pfAvatar.src = avatar;
            pfAvatar.style.display = 'block';
        }
        if (pfAvatarPlaceholder) {
            pfAvatarPlaceholder.style.display = 'none';
        }
    } else {
        if (pfAvatar) {
            pfAvatar.style.display = 'none';
        }
        if (pfAvatarPlaceholder) {
            pfAvatarPlaceholder.style.display = 'block';
        }
    }
    
    // Set name and UID
    var pfName = document.getElementById('pfName');
    var pfUid = document.getElementById('pfUid');
    
    if (pfName) {
        pfName.textContent = profile.zaloName || profile.displayName || 'Không tên';
    }
    if (pfUid) {
        pfUid.textContent = 'ID: ' + escapeHtmlMembers(profile.userId || '');
    }
    
    // Set profile fields
    var pfFields = document.getElementById('pfFields');
    if (pfFields) {
        var fieldsHtml = '';
        
        // Xác định trạng thái kết bạn từ isFr
        var isFriendStatus = 'Chưa kết bạn';
        if (profile.isFr === 1 || profile.isFr === true) {
            isFriendStatus = 'Đã kết bạn';
        }
        
        var fields = [
            { label: 'Tên Zalo', value: profile.zaloName || '-' },
            { label: 'Tên hiển thị', value: profile.displayName || '-' },
            { label: 'Username', value: profile.username || '-' },
            { label: 'User ID', value: profile.userId || '-' },
            { label: 'Số điện thoại', value: profile.phoneNumber || '-' },
            { label: 'Trạng thái', value: profile.status || '-' },
            { label: 'Giới tính', value: formatMemberGender(getMemberGenderValue(profile)) },
            { label: 'Ngày sinh', value: profile.sdob || '-' },
            { label: 'Kết bạn', value: isFriendStatus }
        ];
        
        fields.forEach(function(field) {
            fieldsHtml += '<div style="display:flex;align-items:flex-start;border-bottom:1px solid var(--border);padding:8px 0;font-size:13px">';
            fieldsHtml += '<span style="color:var(--text-secondary);min-width:120px;font-weight:500">' + field.label + ':</span>';
            fieldsHtml += '<span style="flex:1;word-break:break-all">' + escapeHtmlMembers(field.value) + '</span>';
            fieldsHtml += '</div>';
        });
        
        pfFields.innerHTML = fieldsHtml;
    }
    
    // Store profile for messaging
    window._currentProfileData = profile;

    // Re-bind click avatar: click pfAvatar -> /api/get-avatar -> bk_full_avatar
    if (window.AvatarPreview && typeof window.AvatarPreview.bindPfAvatar === 'function') {
        window.AvatarPreview.bindPfAvatar();
    }
}

function closeMemberProfile() {
    var profileBackdrop = document.getElementById('profileBackdrop');
    if (profileBackdrop) {
        profileBackdrop.classList.remove('visible');
        profileBackdrop.style.display = 'none';
    }
}

// ─── Create Group Modal ─────────────────────────────────────────────────────

function openCreateGroupModal() {
    if (!_lastFetchedData.length) {
        setStatus('Chưa có danh sách thành viên. Vui lòng quét nhóm trước!', 'error');
        return;
    }

    var count = _lastFetchedData.length;
    document.getElementById('cgMemberCount').textContent = count + ' thành viên';

    // Preview: hiển thị 5 thành viên đầu + số còn lại
    var preview = _lastFetchedData.slice(0, 5).map(function(m) {
        return m.zaloName || m.userId;
    }).join(', ');
    if (count > 5) preview += ' ... (+' + (count - 5) + ' khác)';
    document.getElementById('cgMembersList').textContent = preview;

    // Reset form
    document.getElementById('cgName').value = '';
    document.getElementById('cgDesc').value = '';
    var status = document.getElementById('cgStatus');
    status.textContent = '';
    status.className = 'overlay-status';
    document.getElementById('cgSubmitBtn').disabled = false;
    document.getElementById('cgBtnText').textContent = 'Tạo Nhóm';

    document.getElementById('createGroupBackdrop').classList.add('visible');
    document.getElementById('createGroupBackdrop').style.display = 'flex';
    document.getElementById('cgName').focus();
}

function closeCreateGroupModal() {
    var backdrop = document.getElementById('createGroupBackdrop');
    if (backdrop) {
        backdrop.classList.remove('visible');
        backdrop.style.display = 'none';
    }
}

document.getElementById('cgCloseBtn').addEventListener('click', closeCreateGroupModal);
document.getElementById('createGroupBackdrop').addEventListener('click', function(e) {
    if (e.target === this) closeCreateGroupModal();
});
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') closeCreateGroupModal();
});

function submitCreateGroup() {
    var name   = document.getElementById('cgName').value.trim();
    var desc   = document.getElementById('cgDesc').value.trim();
    var status = document.getElementById('cgStatus');
    var btn    = document.getElementById('cgSubmitBtn');
    var btnTxt = document.getElementById('cgBtnText');

    if (!name) {
        status.textContent = 'Vui lòng nhập tên nhóm!';
        status.className = 'overlay-status error';
        document.getElementById('cgName').focus();
        return;
    }

    var userIds = _lastFetchedData.map(function(m) { return m.userId; });

    btn.disabled = true;
    btnTxt.textContent = 'Đang tạo...';
    status.textContent = '';
    status.className = 'overlay-status';

    fetch('/api/create-group', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name, desc: desc, userIds: userIds })
    })
    .then(function(r) { return r.json(); })
    .then(function(result) {
        if (result.error) {
            status.textContent = result.error;
            status.className = 'overlay-status error';
            btn.disabled = false;
            btnTxt.textContent = 'Tạo Nhóm';
        } else {
            var url = result.groupUrl || '';
            var msg = 'Tạo nhóm thành công! ';
            if (url) msg += '<a href="' + url + '" target="_blank">' + url + '</a>';
            status.innerHTML = msg;
            status.className = 'overlay-status success';
            btn.disabled = false;
            btnTxt.textContent = 'Đã tạo!';
            setTimeout(closeCreateGroupModal, 2500);
        }
    })
    .catch(function(err) {
        status.textContent = 'Lỗi: ' + err.message;
        status.className = 'overlay-status error';
        btn.disabled = false;
        btnTxt.textContent = 'Tạo Nhóm';
    });
}


// ─── Invite To Existing Groups Modal ─────────────────────────────────────────

window._igSelectedMembers = [];
window._igGroups = [];

function getInviteTargetMembers() {
    var selectedMembers = getSelectedMembers();
    if (selectedMembers.length > 0) return selectedMembers;
    return Array.isArray(_lastFetchedData) ? _lastFetchedData.slice() : [];
}

function openInviteGroupModal() {
    if (!_lastFetchedData.length) {
        setStatus('Chưa có danh sách thành viên. Vui lòng quét nhóm trước!', 'error');
        return;
    }

    var accountId = document.getElementById('memberAccountId') ? document.getElementById('memberAccountId').value.trim() : '';
    if (!accountId) {
        setStatus('Vui lòng chọn tài khoản thực hiện trước khi mời vào nhóm.', 'error');
        return;
    }

    var selectedMembers = getInviteTargetMembers();
    if (!selectedMembers.length) {
        setStatus('Danh sách thành viên đang trống.', 'error');
        return;
    }

    window._igSelectedMembers = selectedMembers;

    var selectedCount = getSelectedMembers().length;
    var countText = selectedMembers.length + ' thành viên';
    if (selectedCount === 0) countText += ' · dùng toàn bộ danh sách';
    var countEl = document.getElementById('igMemberCount');
    if (countEl) countEl.textContent = countText;

    var previewNames = selectedMembers.slice(0, 6).map(function(m) {
        return m.zaloName || m.displayName || m.name || m.userId || m.id || 'Không tên';
    }).join(', ');
    if (selectedMembers.length > 6) previewNames += ' ... (+' + (selectedMembers.length - 6) + ' khác)';

    var preview = document.getElementById('igMembersPreview');
    if (preview) {
        preview.innerHTML = '<strong>Sẽ mời:</strong> ' + escapeHtmlMembers(previewNames) +
            (selectedCount === 0 ? '<div class="ig-hint">Bạn chưa tick riêng ai nên hệ thống sẽ dùng toàn bộ danh sách hiện có.</div>' : '');
    }

    var status = document.getElementById('igStatus');
    if (status) {
        status.textContent = '';
        status.className = 'overlay-status';
    }
    var search = document.getElementById('igGroupSearch');
    if (search) search.value = '';
    var startDate = document.getElementById('igStartDate');
    if (startDate && !startDate.value) startDate.value = getIgLocalDateString();
    var consent = document.getElementById('igConsentConfirm');
    if (consent) consent.checked = false;
    updateIgSchedulePreview();
    var btn = document.getElementById('igSubmitBtn');
    if (btn) btn.disabled = true;
    var btnTxt = document.getElementById('igBtnText');
    if (btnTxt) btnTxt.textContent = 'Xác nhận lưu kế hoạch';

    var backdrop = document.getElementById('inviteGroupBackdrop');
    if (backdrop) {
        backdrop.classList.add('visible');
        backdrop.style.display = 'flex';
    }

    loadInviteGroups(accountId);
}

function closeInviteGroupModal() {
    var backdrop = document.getElementById('inviteGroupBackdrop');
    if (backdrop) {
        backdrop.classList.remove('visible');
        backdrop.style.display = 'none';
    }
}

function loadInviteGroups(accountId) {
    var list = document.getElementById('igGroupsList');
    var btn = document.getElementById('igSubmitBtn');
    if (list) list.innerHTML = '<div class="invite-group-empty">Đang tải danh sách nhóm...</div>';
    if (btn) btn.disabled = true;

    fetch('/api/groups/personal?accountId=' + encodeURIComponent(accountId))
        .then(function(r) { return r.json(); })
        .then(function(result) {
            if (!result.success) {
                window._igGroups = [];
                if (list) list.innerHTML = '<div class="invite-group-empty error">' + escapeHtmlMembers(result.error || 'Không tải được danh sách nhóm') + '</div>';
                return;
            }
            window._igGroups = Array.isArray(result.groups) ? result.groups : [];
            renderInviteGroups(window._igGroups);
        })
        .catch(function(err) {
            window._igGroups = [];
            if (list) list.innerHTML = '<div class="invite-group-empty error">Lỗi tải nhóm: ' + escapeHtmlMembers(err.message) + '</div>';
        });
}

function renderInviteGroups(groups) {
    var list = document.getElementById('igGroupsList');
    if (!list) return;

    if (!groups || !groups.length) {
        list.innerHTML = '<div class="invite-group-empty">Tài khoản này chưa có danh sách nhóm. Hãy mở tài khoản để hệ thống đồng bộ nhóm cá nhân trước.</div>';
        updateInviteSubmitState();
        return;
    }

    var html = '';
    groups.forEach(function(g) {
        var gid = String(g.groupId || g.gridId || g.id || '').trim();
        if (!gid) return;
        var name = g.name || g.groupName || ('Nhóm ' + gid.slice(0, 8));
        var avatar = normalizeAvatarUrlMembers(g.avatar || g.fullAvt || g.avt || '');
        var memberCount = g.memberCount || g.totalMember || g.total || 0;
        html += '<label class="invite-group-item" data-group-name="' + escapeHtmlMembers(String(name).toLowerCase()) + '">';
        html += '<input type="checkbox" class="ig-group-checkbox" value="' + escapeHtmlMembers(gid) + '" onchange="updateInviteSubmitState()">';
        if (avatar) {
            html += '<img src="' + escapeHtmlMembers(avatar) + '" class="invite-group-avatar" onerror="this.style.display=\'none\'">';
        } else {
            html += '<span class="invite-group-avatar placeholder"></span>';
        }
        html += '<span class="invite-group-info"><b>' + escapeHtmlMembers(name) + '</b><small>ID: ' + escapeHtmlMembers(gid) + (memberCount ? ' · ' + escapeHtmlMembers(memberCount) + ' thành viên' : '') + '</small></span>';
        html += '</label>';
    });

    list.innerHTML = html || '<div class="invite-group-empty">Không tìm thấy nhóm hợp lệ.</div>';
    updateInviteSubmitState();
}

function filterInviteGroups() {
    var keyword = (document.getElementById('igGroupSearch') ? document.getElementById('igGroupSearch').value : '').trim().toLowerCase();
    document.querySelectorAll('.invite-group-item').forEach(function(item) {
        var name = item.getAttribute('data-group-name') || '';
        item.style.display = !keyword || name.indexOf(keyword) !== -1 ? 'flex' : 'none';
    });
}

function getSelectedInviteGroupIds() {
    return Array.prototype.slice.call(document.querySelectorAll('.ig-group-checkbox:checked'))
        .map(function(cb) { return cb.value; })
        .filter(Boolean);
}

function getSelectedInviteGroupObjects() {
    var selected = new Set(getSelectedInviteGroupIds().map(String));
    return (Array.isArray(window._igGroups) ? window._igGroups : [])
        .map(function(g) {
            var gid = String(g.groupId || g.gridId || g.id || '').trim();
            if (!gid || !selected.has(gid)) return null;
            return {
                groupId: gid,
                name: g.name || g.groupName || gid,
                avatar: g.avatar || g.fullAvt || g.avt || '',
                memberCount: g.memberCount || g.totalMember || g.total || 0
            };
        })
        .filter(Boolean);
}

function getIgLocalDateString() {
    var d = new Date();
    var m = String(d.getMonth() + 1).padStart(2, '0');
    var day = String(d.getDate()).padStart(2, '0');
    return d.getFullYear() + '-' + m + '-' + day;
}

function updateIgSchedulePreview() {
    var preview = document.getElementById('igSchedulePreview');
    if (!preview) return;

    var total = Array.isArray(window._igSelectedMembers) ? window._igSelectedMembers.length : 0;
    var dailyLimit = parseInt((document.getElementById('igDailyLimit') || {}).value || '0', 10);
    var startDate = (document.getElementById('igStartDate') || {}).value || getIgLocalDateString();

    if (!total) {
        preview.textContent = 'Chưa có người để lập lịch.';
        updateInviteSubmitState();
        return;
    }
    if (!dailyLimit || dailyLimit < 1) {
        preview.textContent = 'Số người mỗi ngày phải lớn hơn 0.';
        updateInviteSubmitState();
        return;
    }

    var days = Math.ceil(total / dailyLimit);
    preview.textContent = 'Dự kiến: ' + total + ' người · ' + dailyLimit + ' người/ngày · bắt đầu ' + startDate + ' · hoàn tất trong ' + days + ' ngày.';
    updateInviteSubmitState();
}

function updateInviteSubmitState() {
    var btn = document.getElementById('igSubmitBtn');
    if (!btn) return;
    var hasGroups = getSelectedInviteGroupIds().length > 0;
    var hasMembers = Array.isArray(window._igSelectedMembers) && window._igSelectedMembers.length > 0;
    var dailyLimit = parseInt((document.getElementById('igDailyLimit') || {}).value || '0', 10);
    var consent = document.getElementById('igConsentConfirm') ? document.getElementById('igConsentConfirm').checked : false;
    btn.disabled = !(hasGroups && hasMembers && dailyLimit > 0 && consent);
}

function submitInviteGroups() {
    var accountId = document.getElementById('memberAccountId') ? document.getElementById('memberAccountId').value.trim() : '';
    var groupIds = getSelectedInviteGroupIds();
    var targetGroups = getSelectedInviteGroupObjects();
    var selectedMembers = window._igSelectedMembers || [];
    var dailyLimit = parseInt((document.getElementById('igDailyLimit') || {}).value || '0', 10);
    var startDate = (document.getElementById('igStartDate') || {}).value || getIgLocalDateString();
    var consent = document.getElementById('igConsentConfirm') ? document.getElementById('igConsentConfirm').checked : false;
    var status = document.getElementById('igStatus');
    var btn = document.getElementById('igSubmitBtn');
    var btnTxt = document.getElementById('igBtnText');

    if (!accountId) {
        status.textContent = 'Chưa chọn tài khoản thực hiện.';
        status.className = 'overlay-status error';
        return;
    }
    if (!groupIds.length) {
        status.textContent = 'Vui lòng chọn ít nhất 1 nhóm.';
        status.className = 'overlay-status error';
        return;
    }
    if (!selectedMembers.length) {
        status.textContent = 'Danh sách thành viên cần mời đang trống.';
        status.className = 'overlay-status error';
        return;
    }
    if (!dailyLimit || dailyLimit < 1) {
        status.textContent = 'Số người mỗi ngày phải lớn hơn 0.';
        status.className = 'overlay-status error';
        var dailyInput = document.getElementById('igDailyLimit');
        if (dailyInput) dailyInput.focus();
        return;
    }
    if (!startDate) {
        status.textContent = 'Vui lòng chọn ngày bắt đầu.';
        status.className = 'overlay-status error';
        return;
    }
    if (!consent) {
        status.textContent = 'Bạn cần xác nhận danh sách người nhận hợp lệ/được phép mời trước khi lưu kế hoạch.';
        status.className = 'overlay-status error';
        return;
    }

    var payloadMembers = selectedMembers.map(function(member) {
        return {
            userId: String(member.userId || member.id || '').trim(),
            name: member.zaloName || member.name || member.displayName || '',
            avatar: member.avatar || member.avt || '',
            phone: member.phone || '',
            isFriend: member.isFr || member.isFriend || ''
        };
    }).filter(function(m) { return m.userId; });

    if (!payloadMembers.length) {
        status.textContent = 'Không tìm thấy UID hợp lệ trong danh sách đã chọn.';
        status.className = 'overlay-status error';
        return;
    }

    btn.disabled = true;
    if (btnTxt) btnTxt.textContent = 'Đang lưu...';
    status.textContent = 'Đang tạo kế hoạch chia lịch mời vào nhóm...';
    status.className = 'overlay-status';

    fetch('/api/group-invite-plans', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            accountId: accountId,
            groupIds: groupIds,
            targetGroupIds: groupIds,
            targetGroups: targetGroups,
            dailyLimit: dailyLimit,
            startDate: startDate,
            members: payloadMembers,
            confirmConsent: consent
        })
    })
    .then(function(r) { return r.json().then(function(json) { json._httpOk = r.ok; return json; }); })
    .then(function(result) {
        if (!result.success) {
            status.textContent = result.error || 'Không lưu được kế hoạch mời vào nhóm.';
            status.className = 'overlay-status error';
            btn.disabled = false;
            if (btnTxt) btnTxt.textContent = 'Xác nhận lưu kế hoạch';
            return;
        }

        var plan = result.plan || {};
        var batches = Array.isArray(plan.batches) ? plan.batches : [];
        var html = '<div><strong>' + escapeHtmlMembers(result.message || 'Đã lưu kế hoạch mời vào nhóm.') + '</strong></div>';
        html += '<div class="ig-hint">Trạng thái: chờ xác nhận thủ công từng batch. Không tự động mời hàng loạt.</div>';
        if (batches.length) {
            html += '<div class="ig-result-list">';
            batches.slice(0, 8).forEach(function(b) {
                html += '<div class="ig-result-row success"><span>📅</span><b>Ngày ' + escapeHtmlMembers(b.day) + '</b><small>' + escapeHtmlMembers(b.date) + ' · ' + escapeHtmlMembers(b.count) + ' người</small></div>';
            });
            if (batches.length > 8) {
                html += '<div class="ig-result-row warning"><span>…</span><b>Còn ' + escapeHtmlMembers(batches.length - 8) + ' ngày</b><small>xem trong file dữ liệu kế hoạch</small></div>';
            }
            html += '</div>';
        }
        status.innerHTML = html;
        status.className = 'overlay-status success';
        showMemberToast(result.message || 'Đã lưu kế hoạch mời vào nhóm.', 'success');
        btn.disabled = false;
        if (btnTxt) btnTxt.textContent = 'Đã lưu kế hoạch!';
        setTimeout(closeInviteGroupModal, 2200);
    })
    .catch(function(err) {
        status.textContent = 'Lỗi: ' + err.message;
        status.className = 'overlay-status error';
        btn.disabled = false;
        if (btnTxt) btnTxt.textContent = 'Xác nhận lưu kế hoạch';
    });
}



// ─── Khôi phục dữ liệu members từ localStorage khi reload ──────────────────

function restoreMembersOnLoad() {
    if (!loadMembersFromStorage()) {
        return;
    }

    var savedAccountId = localStorage.getItem('zalo_members_accountId') || '';
    var savedGroupLink = localStorage.getItem('zalo_members_groupLink') || '';

    // Khôi phục input fields
    if (savedAccountId) {
        var hiddenInput = document.getElementById('memberAccountId');
        if (hiddenInput) hiddenInput.value = savedAccountId;
    }
    if (savedGroupLink) {
        var linkInput = document.getElementById('groupLinkInput');
        if (linkInput) linkInput.value = savedGroupLink;
    }

    // Khôi phục group info card
    if (_savedGroupInfo && _savedGroupInfo.name) {
        displayGroupInfo(_savedGroupInfo);
    }

    // Khôi phục bảng kết quả
    if (_lastFetchedData.length > 0) {
        renderResults(_lastFetchedData);
        var clearBtn = document.getElementById('clearMembersBtn');
        if (clearBtn) clearBtn.style.display = 'inline-flex';
        updateMemberActionState();
        setStatus('Đã khôi phục ' + _lastFetchedData.length + ' thành viên (dữ liệu lần quét trước)', 'info');
    }
}

// ─── Xóa dữ liệu members đã lưu ────────────────────────────────────────────


function openClearMembersConfirm() {
    if (!Array.isArray(_lastFetchedData) || _lastFetchedData.length === 0) {
        setStatus('Chưa có danh sách thành viên để xóa.', 'info');
        return;
    }
    var backdrop = document.getElementById('clearMembersConfirmBackdrop');
    if (backdrop) {
        backdrop.classList.add('open');
        backdrop.setAttribute('aria-hidden', 'false');
    }
}

function closeClearMembersConfirm() {
    var backdrop = document.getElementById('clearMembersConfirmBackdrop');
    if (backdrop) {
        backdrop.classList.remove('open');
        backdrop.setAttribute('aria-hidden', 'true');
    }
}

function confirmClearSavedMembers() {
    closeClearMembersConfirm();
    clearSavedMembers();
}

function clearSavedMembers() {
    _lastFetchedData = [];
    _membersCache = {};
    _savedGroupInfo = null;

    localStorage.removeItem('zalo_members_data');
    localStorage.removeItem('zalo_members_cache');
    localStorage.removeItem('zalo_members_groupInfo');
    localStorage.removeItem('zalo_members_accountId');
    localStorage.removeItem('zalo_members_groupLink');

    document.getElementById('resultsBody').innerHTML = '';
    document.getElementById('totalCount').textContent = '0';
    document.getElementById('resultsSection').style.display = 'flex';
    document.getElementById('emptyState').style.display = 'flex';
    document.getElementById('groupInfoCard').style.display = 'none';
    closeMemberGroupInfoOverlay();
    
    // Clear search input
    var searchInput = document.getElementById('searchInput');
    if (searchInput) {
        searchInput.value = '';
    }
    
    var clearBtn = document.getElementById('clearMembersBtn');
    if (clearBtn) clearBtn.style.display = 'none';
    updateMemberActionState();
    setStatus('Đã xóa danh sách thành viên đã lưu.', 'info');
}

// ─── Add Friend Modal ──────────────────────────────────────────────────────

function getAfLocalDateString(offsetDays) {
    var d = new Date();
    d.setDate(d.getDate() + (offsetDays || 0));
    var yyyy = d.getFullYear();
    var mm = String(d.getMonth() + 1).padStart(2, '0');
    var dd = String(d.getDate()).padStart(2, '0');
    return yyyy + '-' + mm + '-' + dd;
}

function addDaysToDateString(dateString, days) {
    var parts = String(dateString || getAfLocalDateString()).split('-').map(Number);
    var d = new Date(parts[0], (parts[1] || 1) - 1, parts[2] || 1);
    d.setDate(d.getDate() + (days || 0));
    var yyyy = d.getFullYear();
    var mm = String(d.getMonth() + 1).padStart(2, '0');
    var dd = String(d.getDate()).padStart(2, '0');
    return dd + '/' + mm + '/' + yyyy;
}

function updateAfSchedulePreview() {
    var selectedMembers = window._afSelectedMembers || [];
    var total = selectedMembers.length;
    var dailyInput = document.getElementById('afDailyLimit');
    var startInput = document.getElementById('afStartDate');
    var preview = document.getElementById('afSchedulePreview');
    if (!preview) return;
    var daily = parseInt(dailyInput && dailyInput.value ? dailyInput.value : '1', 10);
    if (!daily || daily < 1) daily = 1;
    if (dailyInput && Number(dailyInput.value) !== daily) dailyInput.value = daily;
    var startDate = startInput && startInput.value ? startInput.value : getAfLocalDateString();
    var days = total ? Math.ceil(total / daily) : 0;
    var end = days ? addDaysToDateString(startDate, days - 1) : '';
    preview.innerHTML = total
        ? 'Kế hoạch dự kiến: <b>' + escapeHtmlMembers(total) + '</b> người · <b>' + escapeHtmlMembers(daily) + '</b> người/ngày · <b>' + escapeHtmlMembers(days) + '</b> ngày' + (end ? ' · kết thúc khoảng <b>' + escapeHtmlMembers(end) + '</b>' : '')
        : '';
}

async function loadAddFriendAccounts() {
    try {
        const resp = await fetch('/api/accounts');
        const json = await resp.json();
        const accounts = Array.isArray(json.accounts) ? json.accounts : [];
        window._afAccounts = accounts;

        var select = document.getElementById('afAccountSelect');
        var hidden = document.getElementById('afAccountId');
        if (!select || !hidden) return;

        var currentAccountId = '';
        var memberAcc = document.getElementById('memberAccountId');
        if (memberAcc && memberAcc.value) currentAccountId = memberAcc.value.trim();

        var readyAccounts = accounts.filter(function(acc) { return memberAccountSessionReady(acc, false); });
        var html = '';
        if (!readyAccounts.length) {
            html = '<option value="">Chưa có tài khoản đủ zpwEnk và zpw_sek</option>';
        } else {
            readyAccounts.forEach(function(acc) {
                var name = acc.name || acc.accountId || 'Không tên';
                var selected = (currentAccountId && acc.accountId === currentAccountId) ? ' selected' : '';
                html += '<option value="' + escapeHtmlMembers(acc.accountId) + '"' + selected + '>' + escapeHtmlMembers(name) + '</option>';
            });
        }
        select.innerHTML = html;
        hidden.value = select.value || '';
        fillAfDefaultMessage();
        loadAfGroupsForSelectedAccount();
    } catch (err) {
        console.error('Error loading add friend accounts:', err);
    }
}

function onAfAccountChange() {
    var select = document.getElementById('afAccountSelect');
    var hidden = document.getElementById('afAccountId');
    if (hidden && select) hidden.value = select.value || '';
    fillAfDefaultMessage();
    loadAfGroupsForSelectedAccount();
}

function fillAfDefaultMessage() {
    var select = document.getElementById('afAccountSelect');
    var msg = document.getElementById('afMessage');
    if (!select || !msg || msg.value.trim()) return;
    var accountId = select.value || '';
    var acc = (window._afAccounts || []).find(function(a) { return a.accountId === accountId; });
    var accountName = acc ? (acc.name || acc.accountId) : '';
    msg.value = accountName ? ('Xin chào, mình là ' + accountName + '. Kết bạn với mình nhé!') : 'Chào bạn, mình muốn kết bạn với bạn!';
}

function onAfAfterActionChange() {
    var action = document.getElementById('afAfterAction') ? document.getElementById('afAfterAction').value : 'none';
    var existingBox = document.getElementById('afExistingGroupBox');
    var newBox = document.getElementById('afNewGroupBox');
    if (existingBox) existingBox.style.display = action === 'invite_existing_group' ? 'block' : 'none';
    if (newBox) newBox.style.display = action === 'create_new_group' ? 'block' : 'none';
    if (action === 'invite_existing_group') loadAfGroupsForSelectedAccount();
}

function loadAfGroupsForSelectedAccount() {
    var action = document.getElementById('afAfterAction') ? document.getElementById('afAfterAction').value : 'none';
    if (action !== 'invite_existing_group') return;
    var accountId = document.getElementById('afAccountId') ? document.getElementById('afAccountId').value.trim() : '';
    var list = document.getElementById('afGroupsList');
    if (!list) return;
    if (!accountId) {
        list.innerHTML = '<div class="invite-group-empty">Chưa chọn tài khoản.</div>';
        return;
    }
    list.innerHTML = '<div class="invite-group-empty">Đang tải danh sách nhóm...</div>';
    fetch('/api/groups/personal?accountId=' + encodeURIComponent(accountId))
        .then(function(r) { return r.json(); })
        .then(function(result) {
            if (!result.success) {
                window._afGroups = [];
                list.innerHTML = '<div class="invite-group-empty error">' + escapeHtmlMembers(result.error || 'Không tải được danh sách nhóm') + '</div>';
                return;
            }
            window._afGroups = Array.isArray(result.groups) ? result.groups : [];
            renderAfGroups(window._afGroups);
        })
        .catch(function(err) {
            window._afGroups = [];
            list.innerHTML = '<div class="invite-group-empty error">Lỗi tải nhóm: ' + escapeHtmlMembers(err.message) + '</div>';
        });
}

function renderAfGroups(groups) {
    var list = document.getElementById('afGroupsList');
    if (!list) return;
    if (!groups || !groups.length) {
        list.innerHTML = '<div class="invite-group-empty">Tài khoản này chưa có danh sách nhóm. Hãy bấm Làm mới ở mục Nhóm cá nhân trước.</div>';
        return;
    }
    var html = '';
    groups.forEach(function(g) {
        var gid = String(g.groupId || g.gridId || g.id || '').trim();
        if (!gid) return;
        var name = g.name || g.groupName || ('Nhóm ' + gid.slice(0, 8));
        var avatar = normalizeAvatarUrlMembers(g.avatar || g.fullAvt || g.avt || '');
        var memberCount = g.memberCount || g.totalMember || g.total || 0;
        html += '<label class="invite-group-item af-group-item" data-group-name="' + escapeHtmlMembers(String(name).toLowerCase()) + '">';
        html += '<input type="checkbox" class="af-group-checkbox" value="' + escapeHtmlMembers(gid) + '" data-name="' + escapeHtmlMembers(name) + '">';
        if (avatar) html += '<img src="' + escapeHtmlMembers(avatar) + '" class="invite-group-avatar" onerror="this.style.display=\'none\'">';
        else html += '<span class="invite-group-avatar placeholder"></span>';
        html += '<span class="invite-group-info"><b>' + escapeHtmlMembers(name) + '</b><small>ID: ' + escapeHtmlMembers(gid) + (memberCount ? ' · ' + escapeHtmlMembers(memberCount) + ' thành viên' : '') + '</small></span>';
        html += '</label>';
    });
    list.innerHTML = html || '<div class="invite-group-empty">Không tìm thấy nhóm hợp lệ.</div>';
}

function filterAfGroups() {
    var keyword = (document.getElementById('afGroupSearch') ? document.getElementById('afGroupSearch').value : '').trim().toLowerCase();
    document.querySelectorAll('.af-group-item').forEach(function(item) {
        var name = item.getAttribute('data-group-name') || '';
        item.style.display = !keyword || name.indexOf(keyword) !== -1 ? 'flex' : 'none';
    });
}

function getSelectedAfGroupIds() {
    return Array.prototype.slice.call(document.querySelectorAll('.af-group-checkbox:checked'))
        .map(function(cb) { return cb.value; })
        .filter(Boolean);
}

function getSelectedAfGroupObjects() {
    return Array.prototype.slice.call(document.querySelectorAll('.af-group-checkbox:checked')).map(function(cb) {
        return { groupId: cb.value, name: cb.getAttribute('data-name') || cb.value };
    });
}

function openAddFriendModal() {
    var selectedMembers = getSelectedMembers();
    
    if (selectedMembers.length === 0) {
        setStatus('Vui lòng chọn ít nhất 1 người để lập lịch kết bạn!', 'error');
        return;
    }

    var status = document.getElementById('afStatus');
    status.textContent = '';
    status.className = 'overlay-status';
    
    var selectedList = document.getElementById('afSelectedList');
    var listHtml = '';
    selectedMembers.forEach(function(member) {
        var avatar = normalizeAvatarUrlMembers(member.avatar || member.avt || '');
        var name = member.zaloName || member.name || 'Không tên';
        var uid = member.userId || member.id || '';
        listHtml += '<div style="padding:8px;background:var(--bg-hover);border-radius:8px;margin-bottom:6px;display:flex;align-items:center;gap:8px;font-size:13px;">';
        if (avatar) listHtml += '<img src="' + escapeHtmlMembers(avatar) + '" style="width:32px;height:32px;border-radius:50%;object-fit:cover">';
        else listHtml += '<div style="width:32px;height:32px;border-radius:50%;background:var(--text-secondary);opacity:0.3"></div>';
        listHtml += '<span style="min-width:0"><b>' + escapeHtmlMembers(name) + '</b><br><small style="color:var(--text-secondary)">' + escapeHtmlMembers(uid) + '</small></span>';
        listHtml += '</div>';
    });
    selectedList.innerHTML = listHtml || '<div style="color:var(--text-secondary);font-size:13px;text-align:center;">Chưa chọn ai</div>';
    window._afSelectedMembers = selectedMembers;
    window._afGroups = [];
    
    var msg = document.getElementById('afMessage');
    if (msg) msg.value = '';
    var daily = document.getElementById('afDailyLimit');
    if (daily) daily.value = Math.min(Math.max(selectedMembers.length, 1), 20);
    var startDate = document.getElementById('afStartDate');
    if (startDate) startDate.value = getAfLocalDateString();
    var afterAction = document.getElementById('afAfterAction');
    if (afterAction) afterAction.value = 'none';
    var consent = document.getElementById('afConsentConfirm');
    if (consent) consent.checked = false;
    var newGroupName = document.getElementById('afNewGroupName');
    if (newGroupName) newGroupName.value = '';
    var groupSearch = document.getElementById('afGroupSearch');
    if (groupSearch) groupSearch.value = '';
    onAfAfterActionChange();

    document.getElementById('afSubmitBtn').disabled = false;
    document.getElementById('afBtnText').textContent = 'Lưu kế hoạch';

    loadAddFriendAccounts();
    updateAfSchedulePreview();

    document.getElementById('addFriendBackdrop').classList.add('visible');
    document.getElementById('addFriendBackdrop').style.display = 'flex';
    setTimeout(function() {
        var messageBox = document.getElementById('afMessage');
        if (messageBox) messageBox.focus();
    }, 50);
}

function closeAddFriendModal() {
    var backdrop = document.getElementById('addFriendBackdrop');
    if (backdrop) {
        backdrop.classList.remove('visible');
        backdrop.style.display = 'none';
    }
}

document.addEventListener('DOMContentLoaded', function() {
    var fetchBackdrop = document.getElementById('memberFetchBackdrop');
    if (fetchBackdrop) {
        fetchBackdrop.addEventListener('click', function(e) {
            if (e.target === fetchBackdrop) closeMemberFetchOverlay();
        });
    }
    var groupInfoBackdrop = document.getElementById('memberGroupInfoBackdrop');
    if (groupInfoBackdrop) {
        groupInfoBackdrop.addEventListener('click', function(e) {
            if (e.target === groupInfoBackdrop) closeMemberGroupInfoOverlay();
        });
    }

    // Invite Group Modal close button
    var igCloseBtn = document.getElementById('igCloseBtn');
    if (igCloseBtn) {
        igCloseBtn.addEventListener('click', closeInviteGroupModal);
    }

    var igBackdrop = document.getElementById('inviteGroupBackdrop');
    if (igBackdrop) {
        igBackdrop.addEventListener('click', function(e) {
            if (e.target === this) closeInviteGroupModal();
        });
    }

    var igConsent = document.getElementById('igConsentConfirm');
    if (igConsent) {
        igConsent.addEventListener('change', updateInviteSubmitState);
    }

    // Add Friend Modal close button
    var afCloseBtn = document.getElementById('afCloseBtn');
    if (afCloseBtn) {
        afCloseBtn.addEventListener('click', closeAddFriendModal);
    }
    
    var afBackdrop = document.getElementById('addFriendBackdrop');
    if (afBackdrop) {
        afBackdrop.addEventListener('click', function(e) {
            if (e.target === this) closeAddFriendModal();
        });
    }
    
    // Member Profile Modal close button
    var pfCloseBtn = document.getElementById('pfCloseBtn');
    if (pfCloseBtn) {
        pfCloseBtn.addEventListener('click', closeMemberProfile);
    }
    
    var profileBackdrop = document.getElementById('profileBackdrop');
    if (profileBackdrop) {
        profileBackdrop.addEventListener('click', function(e) {
            if (e.target === this) closeMemberProfile();
        });
    }
});

document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        closeInviteGroupModal();
        closeAddFriendModal();
        closeMemberProfile();
        closeMemberLogDrawer();
        closeMemberFetchOverlay();
        closeMemberGroupInfoOverlay();
    }
});

function submitAddFriend() {
    var accountId = document.getElementById('afAccountId').value.trim();
    var message = document.getElementById('afMessage').value.trim();
    var status = document.getElementById('afStatus');
    var btn = document.getElementById('afSubmitBtn');
    var btnTxt = document.getElementById('afBtnText');
    var selectedMembers = window._afSelectedMembers || [];
    var dailyLimit = parseInt(document.getElementById('afDailyLimit').value || '0', 10);
    var startDate = document.getElementById('afStartDate').value || getAfLocalDateString();
    var afterAction = document.getElementById('afAfterAction').value || 'none';
    var targetGroupIds = getSelectedAfGroupIds();
    var targetGroups = getSelectedAfGroupObjects();
    var newGroupName = (document.getElementById('afNewGroupName') ? document.getElementById('afNewGroupName').value.trim() : '');
    var consent = document.getElementById('afConsentConfirm') ? document.getElementById('afConsentConfirm').checked : false;

    if (!accountId) {
        status.textContent = 'Vui lòng chọn tài khoản thực hiện!';
        status.className = 'overlay-status error';
        return;
    }
    if (selectedMembers.length === 0) {
        status.textContent = 'Vui lòng chọn ít nhất 1 người để lập lịch!';
        status.className = 'overlay-status error';
        return;
    }
    if (!dailyLimit || dailyLimit < 1) {
        status.textContent = 'Số người mỗi ngày phải lớn hơn 0.';
        status.className = 'overlay-status error';
        document.getElementById('afDailyLimit').focus();
        return;
    }
    if (!message) {
        status.textContent = 'Vui lòng nhập nội dung lời mời!';
        status.className = 'overlay-status error';
        document.getElementById('afMessage').focus();
        return;
    }
    if (afterAction === 'invite_existing_group' && targetGroupIds.length === 0) {
        status.textContent = 'Vui lòng chọn ít nhất 1 nhóm đích.';
        status.className = 'overlay-status error';
        return;
    }
    if (afterAction === 'create_new_group' && !newGroupName) {
        status.textContent = 'Vui lòng nhập tên nhóm mới.';
        status.className = 'overlay-status error';
        document.getElementById('afNewGroupName').focus();
        return;
    }
    if (!consent) {
        status.textContent = 'Bạn cần xác nhận danh sách người nhận hợp lệ/được phép liên hệ trước khi lưu kế hoạch.';
        status.className = 'overlay-status error';
        return;
    }

    btn.disabled = true;
    btnTxt.textContent = 'Đang lưu...';
    status.textContent = 'Đang tạo kế hoạch chia lịch...';
    status.className = 'overlay-status';

    var payloadMembers = selectedMembers.map(function(member) {
        return {
            userId: String(member.userId || member.id || '').trim(),
            name: member.zaloName || member.name || '',
            avatar: member.avatar || member.avt || '',
            phone: member.phone || '',
            isFriend: member.isFr || member.isFriend || ''
        };
    }).filter(function(m) { return m.userId; });

    fetch('/api/friend-request-plans', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            accountId: accountId,
            message: message,
            dailyLimit: dailyLimit,
            startDate: startDate,
            members: payloadMembers,
            afterAction: afterAction,
            targetGroupIds: targetGroupIds,
            targetGroups: targetGroups,
            newGroupName: newGroupName,
            confirmConsent: consent
        })
    })
    .then(function(r) { return r.json().then(function(json) { json._httpOk = r.ok; return json; }); })
    .then(function(result) {
        if (!result.success) {
            status.textContent = result.error || 'Không lưu được kế hoạch.';
            status.className = 'overlay-status error';
            btn.disabled = false;
            btnTxt.textContent = 'Lưu kế hoạch';
            return;
        }
        var plan = result.plan || {};
        var batches = Array.isArray(plan.batches) ? plan.batches : [];
        var html = '<div><strong>' + escapeHtmlMembers(result.message || 'Đã lưu kế hoạch.') + '</strong></div>';
        html += '<div class="ig-hint">Trạng thái: chờ xác nhận thủ công từng batch. Không tự động gửi hàng loạt.</div>';
        if (batches.length) {
            html += '<div class="ig-result-list">';
            batches.slice(0, 8).forEach(function(b) {
                html += '<div class="ig-result-row success"><span>📅</span><b>Ngày ' + escapeHtmlMembers(b.day) + '</b><small>' + escapeHtmlMembers(b.date) + ' · ' + escapeHtmlMembers(b.count) + ' người</small></div>';
            });
            if (batches.length > 8) {
                html += '<div class="ig-result-row warning"><span>…</span><b>Còn ' + escapeHtmlMembers(batches.length - 8) + ' ngày</b><small>xem trong file dữ liệu kế hoạch</small></div>';
            }
            html += '</div>';
        }
        status.innerHTML = html;
        status.className = 'overlay-status success';
        showMemberToast(result.message || 'Đã lưu kế hoạch kết bạn.', 'success');
        btn.disabled = false;
        btnTxt.textContent = 'Đã lưu';
    })
    .catch(function(err) {
        status.textContent = 'Lỗi: ' + err.message;
        status.className = 'overlay-status error';
        btn.disabled = false;
        btnTxt.textContent = 'Lưu lại';
    });
}

document.addEventListener('DOMContentLoaded', function() {
    renderResults([]);
    updateMemberActionState();
    loadMemberAccounts().then(function() {
        restoreMembersOnLoad();
        updateMemberActionState();
    });

    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            closeClearMembersConfirm();
            closeMemberStatusDetail();
        }
    });
});
