
// Helper: Escape HTML
function escapeHtmlMembers(str) {
    return String(str || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

// Helper: Normalize avatar URL
function normalizeAvatarUrlMembers(url) {
    if (!url) return "";
    if (url.startsWith("//")) return "https:" + url;
    return url;
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
        var selectedAcc = findAccountById(currentId) || accounts[0] || null;

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
                        var ready = acc.cookies && acc.zpwEnk && acc.imei;
                        var missing = [];
                        if (!acc.cookies) missing.push('cookies');
                        if (!acc.zpwEnk) missing.push('zpwEnk');
                        if (!acc.imei) missing.push('imei');
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

    // Show log panel in right column and clear
    document.getElementById('logCard').style.display = 'block';
    document.getElementById('logOutput').innerHTML = '';
    document.getElementById('logProgress').textContent = '';

    // Clear previous results
    document.getElementById('resultsBody').innerHTML = '';
    document.getElementById('totalCount').textContent = '0';
    
    // Clear search input
    var searchInput = document.getElementById('searchInput');
    if (searchInput) {
        searchInput.value = '';
    }

    setStatus('Đang lấy danh sách thành viên...', 'loading');

    // Setup log streaming
    setLogCallback(function(msg, type) {
        var logOutput = document.getElementById('logOutput');
        var line = document.createElement('div');
        line.className = 'log-line ' + (type || 'info');
        line.textContent = msg;
        logOutput.appendChild(line);
        logOutput.scrollTop = logOutput.scrollHeight;
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
            document.getElementById('emptyState').style.display = 'flex';
            return;
        }

        if (json.success) {
            setStatus('Hoàn thành! Lấy được ' + json.total + ' thành viên.', 'success');
            document.getElementById('logProgress').textContent = '\u2713 Hoàn thành';

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

            // ─── Persist to localStorage ─────────────────────────────────
            _savedGroupInfo = groupInfo;
            saveMembersToStorage();
            // Show clear button
            var clearBtn = document.getElementById('clearMembersBtn');
            if (clearBtn) clearBtn.style.display = 'inline-block';

            // Enrich chạy nền sau khi bảng đã hiển thị.
            setStatus('Đã hiển thị ' + _lastFetchedData.length + ' thành viên. Đang bổ sung chi tiết...', 'loading');
            enrichMembersWithProfileData(_lastFetchedData, accountId)
                .then(function() {
                    renderResults(_lastFetchedData);
                    saveMembersToStorage();
                    setStatus('Hoàn thành! Lấy được ' + _lastFetchedData.length + ' thành viên.', 'success');
                })
                .catch(function(err) {
                    console.warn('[runFetch] enrich background error:', err);
                    setStatus('Đã hiển thị danh sách. Lỗi bổ sung chi tiết: ' + (err.message || String(err)), 'error');
                });
        }
    } catch(err) {
        setStatus('Lỗi kết nối: ' + err.message, 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg> Lấy danh sách thành viên';
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
                        member.gender = member.gender || 0;
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
                            member.gender = Number(member.gender || 0);
                            member.sdob = member.sdob || '-';
                            member.isFr = Number(member.isFr || 0);
                        } else {
                            // Profile not found, use defaults
                            member.gender = Number(member.gender || 0);
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
            member.gender = member.gender || 0;
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
        
        tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;padding:40px;color:var(--text-secondary);">Không tìm thấy thành viên nào</td></tr>';
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


function renderResults(members) {
    var tbody = document.getElementById('resultsBody');
    var totalCount = document.getElementById('totalCount');
    var resultsSection = document.getElementById('resultsSection');
    var emptyState = document.getElementById('emptyState');

    if (!members || members.length === 0) {
        tbody.innerHTML = '';
        totalCount.textContent = '0';
        resultsSection.style.display = 'none';
        emptyState.style.display = 'flex';
        return;
    }

    totalCount.textContent = members.length;
    resultsSection.style.display = 'block';
    emptyState.style.display = 'none';

    var html = '';
    members.forEach(function(member) {
        var userId = member.userId || member.id || '';
        var name = member.zaloName || member.displayName || member.dName || member.name || 'Không tên';
        var avatar = normalizeAvatarUrlMembers(member.avatar || member.avt || '');
        var isFr = Number(member.isFr || 0);
        var gender = Number(member.gender || 0);
        var sdob = member.sdob || member.dob || '-';
        var phoneNumber = member.phoneNumber || member.phone || member.mobile || '-';
        var statusText = member.status || member.accountStatus || '-';

        // Zalo thường trả gender: 0 Nam, 1 Nữ
        var genderDisplay = gender === 1 ? 'Nữ' : 'Nam';

        var friendStatus = isFr === 1 ? 'Đã kết bạn' : 'Chưa';
        var friendColor = isFr === 1 ? 'var(--green)' : 'var(--orange)';
        var friendBgColor = isFr === 1 ? 'rgba(34, 197, 94, 0.1)' : 'rgba(251, 146, 60, 0.1)';

        html += '<tr data-member-id="' + escapeHtmlMembers(userId) + '" style="cursor:pointer">';

        // 1. checkbox
        html += '<td style="text-align:center"><input type="checkbox" class="member-checkbox" data-user-id="' + escapeHtmlMembers(userId) + '" style="cursor:pointer"></td>';

        // 2. avatar
        html += '<td>';
        if (avatar) {
            html += '<img src="' + escapeHtmlMembers(avatar) + '" class="avatar-small" style="width:40px;height:40px;border-radius:50%;object-fit:cover" onerror="this.style.display=\'none\'">';
        } else {
            html += '<div style="width:40px;height:40px;border-radius:50%;background:var(--surface2)"></div>';
        }
        html += '</td>';

        // 3. tên
        html += '<td>' + escapeHtmlMembers(name) + '</td>';

        // 4. ID
        html += '<td><code style="font-size:11px;color:var(--text-secondary)">' + escapeHtmlMembers(userId) + '</code></td>';

        // 5. giới tính
        html += '<td>' + escapeHtmlMembers(genderDisplay) + '</td>';

        // 6. ngày sinh
        html += '<td><span style="font-size:12px;color:var(--text-secondary)">' + escapeHtmlMembers(sdob) + '</span></td>';

        // 7. số điện thoại
        html += '<td><span style="font-size:12px;color:var(--text-secondary)">' + escapeHtmlMembers(phoneNumber) + '</span></td>';

        // 8. trạng thái
        html += '<td><span class="member-status-text" title="' + escapeHtmlMembers(statusText) + '">' + escapeHtmlMembers(statusText) + '</span></td>';

        // 9. kết bạn
        html += '<td><span class="status-badge" style="color:' + friendColor + ';background-color:' + friendBgColor + ';font-weight:500">' + escapeHtmlMembers(friendStatus) + '</span></td>';

        // 10. xem
        html += '<td><button class="btn btn-sm btn-primary" onclick="event.stopPropagation(); showMemberProfile(\'' + escapeHtmlMembers(userId) + '\')" style="cursor:pointer">Xem</button></td>';

        html += '</tr>';
    });

    tbody.innerHTML = html;

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
            setStatus('Lỗi: ' + result.error, 'error');
            profileBackdrop.classList.remove('visible');
            profileBackdrop.style.display = 'none';
            return;
        }
        
        if (!result.profile) {
            console.warn('[showMemberProfile] No profile returned');
            setStatus('Không lấy được profile', 'error');
            profileBackdrop.classList.remove('visible');
            profileBackdrop.style.display = 'none';
            return;
        }
        
        // Populate and show modal
        populateProfileModal(result.profile);
        
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
            { label: 'Giới tính', value: profile.gender === 1 ? 'Nữ' : 'Nam' },
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
        if (clearBtn) clearBtn.style.display = 'inline-block';
        setStatus('Đã khôi phục ' + _lastFetchedData.length + ' thành viên (dữ liệu lần quét trước)', 'info');
    }
}

// ─── Xóa dữ liệu members đã lưu ────────────────────────────────────────────

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
    document.getElementById('resultsSection').style.display = 'none';
    document.getElementById('emptyState').style.display = 'flex';
    document.getElementById('groupInfoCard').style.display = 'none';
    
    // Clear search input
    var searchInput = document.getElementById('searchInput');
    if (searchInput) {
        searchInput.value = '';
    }
    
    var clearBtn = document.getElementById('clearMembersBtn');
    if (clearBtn) clearBtn.style.display = 'none';
    setStatus('Đã xóa dữ liệu đã lưu.', 'info');
}

// ─── Add Friend Modal ──────────────────────────────────────────────────────

async function loadAddFriendAccounts() {
    try {
        const resp = await fetch('/api/accounts');
        const json = await resp.json();
        const accounts = json.accounts || [];
        
        const container = document.getElementById('afAccountDropdown');
        if (!container) return;
        
        const hiddenInput = document.getElementById('afAccountId');
        const selectedAcc = accounts[0] || null;
        
        // Render selected account
        function renderSelected(acc) {
            if (!acc) {
                return `
                    <button type="button" class="account-dropdown-button">
                        <span>Chọn tài khoản</span>
                    </button>
                `;
            }
            
            var avatar = normalizeAvatarUrlMembers(acc.avatarUrl || acc.avatar || "");
            var name = acc.name || acc.accountId || "Không tên";
            
            return `
                <button type="button" class="account-dropdown-button">
                    ${avatar ? `<img src="${escapeHtmlMembers(avatar)}" class="account-dropdown-avatar" />` : `<span class="account-dropdown-avatar placeholder"></span>`}
                    <span class="account-dropdown-name">${escapeHtmlMembers(name)}</span>
                </button>
            `;
        }
        
        // Render menu
        function renderMenu() {
            return `
                <div class="account-dropdown-menu hidden">
                    ${accounts.map(acc => {
                        var avatar = normalizeAvatarUrlMembers(acc.avatarUrl || acc.avatar || "");
                        var name = acc.name || acc.accountId || "Không tên";
                        var id = acc.accountId || "";
                        var ready = acc.cookies && acc.zpwEnk;
                        var missing = [];
                        if (!acc.cookies) missing.push('cookies');
                        if (!acc.zpwEnk) missing.push('zpwEnk');
                        
                        return `
                            <button
                                type="button"
                                class="account-dropdown-item ${!ready ? 'disabled' : ''}"
                                data-account-id="${escapeHtmlMembers(id)}"
                                ${!ready ? 'disabled' : ''}
                            >
                                ${avatar ? `<img src="${escapeHtmlMembers(avatar)}" class="account-dropdown-avatar" />` : `<span class="account-dropdown-avatar placeholder"></span>`}
                                <span>
                                    <b>${escapeHtmlMembers(name)}</b><br>
                                    <small>${!ready ? 'thiếu: ' + missing.join(', ') : ''}</small>
                                </span>
                            </button>
                        `;
                    }).join("")}
                </div>
            `;
        }
        
        container.innerHTML = renderSelected(selectedAcc) + renderMenu();
        
        var button = container.querySelector(".account-dropdown-button");
        var menu = container.querySelector(".account-dropdown-menu");
        
        if (selectedAcc && hiddenInput) {
            hiddenInput.value = selectedAcc.accountId;
        }
        
        button.addEventListener("click", function() {
            menu.classList.toggle("hidden");
        });
        
        container.querySelectorAll(".account-dropdown-item:not([disabled])").forEach(item => {
            item.addEventListener("click", function(e) {
                var accountId = this.dataset.accountId;
                var acc = accounts.find(a => a.accountId === accountId);
                if (!acc) return;
                
                if (hiddenInput) hiddenInput.value = accountId;
                button.innerHTML = renderSelected(acc).split('</button>')[0].split('>')[1];
                button.parentElement.innerHTML = renderSelected(acc) + renderMenu();
                
                // Auto-fill message with account name
                var accountName = acc.name || acc.accountId;
                var defaultMsg = "Xin chào, mình là " + escapeHtmlMembers(accountName) + ". Kết bạn với mình nhé!";
                document.getElementById('afMessage').value = defaultMsg;
                
                // Re-attach event listener to new button
                var newButton = container.querySelector(".account-dropdown-button");
                var newMenu = container.querySelector(".account-dropdown-menu");
                newButton.addEventListener("click", function() {
                    newMenu.classList.toggle("hidden");
                });
            });
        });
        
        document.addEventListener("click", function(e) {
            if (!container.contains(e.target) && menu) {
                menu.classList.add("hidden");
            }
        });
    } catch (err) {
        console.error('Error loading add friend accounts:', err);
    }
}

function openAddFriendModal() {
    var selectedMembers = getSelectedMembers();
    
    if (selectedMembers.length === 0) {
        setStatus('Vui lòng chọn ít nhất 1 người để gửi kết bạn!', 'error');
        return;
    }

    var status = document.getElementById('afStatus');
    status.textContent = '';
    status.className = 'overlay-status';
    
    // Display selected members
    var selectedList = document.getElementById('afSelectedList');
    var listHtml = '';
    selectedMembers.forEach(function(member) {
        var avatar = normalizeAvatarUrlMembers(member.avatar || member.avt || '');
        var name = member.zaloName || member.name || 'Không tên';
        listHtml += '<div style="padding:8px;background:var(--bg-hover);border-radius:4px;margin-bottom:6px;display:flex;align-items:center;gap:8px;font-size:13px;">';
        if (avatar) {
            listHtml += '<img src="' + escapeHtmlMembers(avatar) + '" style="width:32px;height:32px;border-radius:50%;object-fit:cover">';
        } else {
            listHtml += '<div style="width:32px;height:32px;border-radius:50%;background:var(--text-secondary);opacity:0.3"></div>';
        }
        listHtml += '<span>' + escapeHtmlMembers(name) + '</span>';
        listHtml += '</div>';
    });
    selectedList.innerHTML = listHtml || '<div style="color:var(--text-secondary);font-size:13px;text-align:center;">Chưa chọn ai</div>';
    
    // Store selected members for submit
    window._afSelectedMembers = selectedMembers;
    
    document.getElementById('afMessage').value = 'Chào bạn, mình muốn kết bạn với bạn!';
    document.getElementById('afSubmitBtn').disabled = false;
    document.getElementById('afBtnText').textContent = 'Gửi Kết Bạn';

    loadAddFriendAccounts();

    document.getElementById('addFriendBackdrop').classList.add('visible');
    document.getElementById('addFriendBackdrop').style.display = 'flex';
    document.getElementById('afMessage').focus();
}

function closeAddFriendModal() {
    var backdrop = document.getElementById('addFriendBackdrop');
    if (backdrop) {
        backdrop.classList.remove('visible');
        backdrop.style.display = 'none';
    }
}

document.addEventListener('DOMContentLoaded', function() {
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
        closeAddFriendModal();
        closeMemberProfile();
    }
});

function submitAddFriend() {
    var accountId = document.getElementById('afAccountId').value.trim();
    var message = document.getElementById('afMessage').value.trim();
    var status = document.getElementById('afStatus');
    var btn = document.getElementById('afSubmitBtn');
    var btnTxt = document.getElementById('afBtnText');
    var selectedMembers = window._afSelectedMembers || [];

    if (!accountId) {
        status.textContent = 'Vui lòng chọn tài khoản gửi!';
        status.className = 'overlay-status error';
        return;
    }

    if (selectedMembers.length === 0) {
        status.textContent = 'Vui lòng chọn ít nhất 1 người để gửi!';
        status.className = 'overlay-status error';
        return;
    }

    if (!message) {
        status.textContent = 'Vui lòng nhập nội dung lời mời!';
        status.className = 'overlay-status error';
        document.getElementById('afMessage').focus();
        return;
    }

    btn.disabled = true;
    btnTxt.textContent = 'Đang gửi...';
    status.textContent = '';
    status.className = 'overlay-status';

    var totalCount = selectedMembers.length;
    var successCount = 0;
    var failCount = 0;
    var results = [];

    // Send friend request to each selected member
    var sendRequests = selectedMembers.map(function(member) {
        var toId = member.userId || member.id;
        return fetch('/api/send-friend-request', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ accountId: accountId, toId: toId, message: message })
        })
        .then(function(r) { return r.json(); })
        .then(function(result) {
            var memberName = member.zaloName || member.name || toId;
            if (result.error) {
                results.push({ name: memberName, status: 'thất bại', error: result.error });
                failCount++;
            } else {
                results.push({ name: memberName, status: 'thành công' });
                successCount++;
            }
        })
        .catch(function(err) {
            var memberName = member.zaloName || member.name || toId;
            results.push({ name: memberName, status: 'thất bại', error: err.message });
            failCount++;
        });
    });

    Promise.all(sendRequests).then(function() {
        var resultHtml = '<div style="margin-bottom:12px;"><strong>Kết quả:</strong> ' + successCount + ' thành công, ' + failCount + ' thất bại</div>';
        resultHtml += '<div style="max-height:200px;overflow-y:auto;border:1px solid var(--border);border-radius:4px;padding:8px;">';
        results.forEach(function(r) {
            var icon = r.status === 'thành công' ? '✓' : '✗';
            var color = r.status === 'thành công' ? 'var(--green)' : 'var(--red)';
            resultHtml += '<div style="padding:6px;border-bottom:1px solid var(--border);font-size:12px;">';
            resultHtml += '<span style="color:' + color + ';font-weight:600;">' + icon + '</span> ';
            resultHtml += '<span>' + escapeHtmlMembers(r.name) + '</span>';
            if (r.error) {
                resultHtml += '<div style="color:var(--text-secondary);font-size:11px;margin-left:20px;">' + escapeHtmlMembers(r.error) + '</div>';
            }
            resultHtml += '</div>';
        });
        resultHtml += '</div>';

        status.innerHTML = resultHtml;
        status.className = 'overlay-status ' + (failCount === 0 ? 'success' : 'warning');
        btn.disabled = false;
        btnTxt.textContent = 'Đã xong!';
        setTimeout(closeAddFriendModal, 3000);
    });
}

document.addEventListener('DOMContentLoaded', function() {
    loadMemberAccounts().then(function() {
        restoreMembersOnLoad();
    });
});
