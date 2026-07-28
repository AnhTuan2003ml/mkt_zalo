(function () {
    'use strict';

    var state = {
        accounts: [],
        accountId: '',
        groups: [],
        selectedGroupId: '',
        selectedGroup: null,
        members: [],
        selectedMembers: new Set(),
        memberFilter: 'all',
        inviteSelectedGroups: new Set(),
        loadingMembers: false,
        membersRequestSeq: 0,
        refreshingGroups: false
    };

    function $(id) { return document.getElementById(id); }

    function esc(v) {
        return String(v == null ? '' : v)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function normalizeAvatar(url) {
        url = String(url || '').trim();
        if (!url) return '';
        if (url.indexOf('//') === 0) return 'https:' + url;
        return url;
    }

    function getAccountId(account) {
        return String((account && (account.accountId || account.id || account.account_id)) || '').trim();
    }

    function getAccountName(account) {
        return String((account && (account.name || account.displayName || account.zaloName || account.phoneNumber)) || getAccountId(account) || 'Tài khoản').trim();
    }

    function getAccountAvatar(account) {
        return normalizeAvatar(account && (account.avatarUrl || account.avatar || account.profileAvatar));
    }

    function accountReady(account) {
        return !!(window.NexusSession && NexusSession.accountReady(account, true));
    }

    function accountStateText(account) {
        if (!account) return state.accounts.length ? 'Nhấn để chọn tài khoản' : 'Hãy thêm tài khoản trước';
        if (account.remoteDebugPort) return 'Đang chạy · Sẵn sàng quản lý nhóm';
        if (accountReady(account)) return 'Sẵn sàng quản lý nhóm';
        return 'Chưa mở hoặc thiếu dữ liệu đăng nhập';
    }

    function initials(name) {
        var parts = String(name || '?').trim().split(/\s+/).filter(Boolean);
        if (!parts.length) return '?';
        if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
        return (parts[0].charAt(0) + parts[parts.length - 1].charAt(0)).toUpperCase();
    }

    function setGroupsAccountAvatar(element, account) {
        if (!element) return;
        var name = getAccountName(account);
        var avatarUrl = getAccountAvatar(account);
        element.innerHTML = '';

        var fallback = document.createElement('span');
        fallback.className = 'nexus-lucide nexus-lucide-user';
        fallback.setAttribute('aria-hidden', 'true');
        element.appendChild(fallback);

        if (!avatarUrl) return;
        var image = document.createElement('img');
        image.src = avatarUrl;
        image.alt = '';
        image.loading = 'lazy';
        image.addEventListener('error', function () { image.remove(); });
        element.appendChild(image);
    }

    function getGroupId(g) {
        return String((g && (g.groupId || g.gridId || g.id || g.gid)) || '').trim();
    }

    function getGroupName(g) {
        var gid = getGroupId(g);
        return String((g && (g.name || g.grid_name || g.groupName || g.title)) || ('Group ' + gid.slice(0, 8))).trim();
    }

    function getMemberId(m) {
        return String((m && (m.userId || m.uid || m.id)) || '').trim();
    }

    function getMemberName(m) {
        return String((m && (m.displayName || m.zaloName || m.dName || m.name || m.globalId)) || 'Không tên').trim();
    }

    function getMemberGenderText(m) {
        var raw = m && (m.gender !== undefined ? m.gender : (m.sex !== undefined ? m.sex : m.genderType));
        if (raw === undefined || raw === null || raw === '' || raw === '-') return '-';

        var text = String(raw).trim().toLowerCase();
        if (!text || text === 'unknown' || text === 'null' || text === 'undefined') return '-';
        if (text === '1' || text === 'nữ' || text === 'nu' || text === 'female' || text === 'f') return 'Nữ';
        if (text === '0' || text === 'nam' || text === 'male' || text === 'm') return 'Nam';

        var value = Number(text);
        if (value === 1) return 'Nữ';
        if (value === 0) return 'Nam';
        return '-';
    }

    function mergeMemberProfile(member, profile) {
        if (!member || !profile) return member || profile || {};
        Object.keys(profile).forEach(function (key) {
            var val = profile[key];
            if (val !== undefined && val !== null && val !== '' && !(Array.isArray(val) && !val.length)) {
                member[key] = val;
            }
        });
        return member;
    }

    async function groupsEnrichMembersWithProfileData(members, accountId, requestSeq) {
        // Không tự lấy profile chi tiết hàng loạt để tránh Zalo giới hạn [221].
        // Chi tiết từng người sẽ được lấy khi người dùng bấm nút Xem.
        return;
    }

    function getMemberDobText(m) {
        return String((m && (m.sdob || m.dob || m.birthDate || m.birthday)) || '-').trim() || '-';
    }

    function getMemberPhoneText(m) {
        return String((m && (m.phoneNumber || m.phone || m.mobile)) || '').trim();
    }

    function isMemberFriend(m) {
        return String((m && (m.isFr || m.friend)) || '') === '1' || (m && m.isFriend === true);
    }

    function showToast(msg, type) {
        var el = $('groupsToast');
        if (!el) return;
        el.textContent = msg || '';
        el.className = 'groups-toast ' + (type || 'info');
        el.style.display = 'block';
        clearTimeout(showToast._timer);
        showToast._timer = setTimeout(function () { el.style.display = 'none'; }, 3200);
    }

    function setStatus(msg, type) {
        var el = $('groupsStatus');
        if (!el) return;
        el.textContent = msg || '';
        el.className = 'groups-inline-status ' + (type || '');
        el.style.display = msg ? 'block' : 'none';
    }

    function sleep(ms) {
        return new Promise(function (resolve) { setTimeout(resolve, ms); });
    }

    function setRefreshButtonLoading(loading, text) {
        var btn = $('groupsRefreshBtn');
        if (!btn) return;
        btn.disabled = !!loading;
        btn.textContent = text || (loading ? 'Đang làm mới...' : 'Làm mới');
    }

    async function fetchPersonalGroups(accountId) {
        var res = await fetch('/api/groups/personal?accountId=' + encodeURIComponent(accountId));
        var data = await res.json();
        if (!data.success) throw new Error(data.error || 'Không lấy được danh sách nhóm');
        return data;
    }

    function renderGroupsAccountSelected() {
        var account = state.accounts.find(function (item) { return getAccountId(item) === state.accountId; });
        var nameEl = $('groupsAccountName');
        var stateEl = $('groupsAccountState');
        var trigger = $('groupsAccountTrigger');
        var hidden = $('groupsAccountSelect');

        if (hidden) hidden.value = state.accountId || '';
        if (trigger) trigger.disabled = !state.accounts.length;
        if (!account) {
            if (nameEl) nameEl.textContent = state.accounts.length ? 'Chọn tài khoản' : 'Chưa có tài khoản';
            if (stateEl) stateEl.textContent = state.accounts.length ? 'Nhấn để chọn tài khoản' : 'Hãy thêm tài khoản trước';
            setGroupsAccountAvatar($('groupsAccountAvatar'), null);
            return;
        }

        if (nameEl) nameEl.textContent = getAccountName(account);
        if (stateEl) stateEl.textContent = accountStateText(account);
        setGroupsAccountAvatar($('groupsAccountAvatar'), account);
    }

    function renderGroupsAccountMenu() {
        var menu = $('groupsAccountMenu');
        if (!menu) return;
        menu.innerHTML = '';

        if (!state.accounts.length) {
            var empty = document.createElement('div');
            empty.className = 'groups-account-empty';
            empty.textContent = 'Chưa có tài khoản để lựa chọn.';
            menu.appendChild(empty);
            return;
        }

        state.accounts.forEach(function (account) {
            var id = getAccountId(account);
            var button = document.createElement('button');
            button.type = 'button';
            button.className = 'groups-account-option' + (id === state.accountId ? ' active' : '');
            button.setAttribute('role', 'option');
            button.setAttribute('aria-selected', id === state.accountId ? 'true' : 'false');

            var avatar = document.createElement('span');
            avatar.className = 'groups-account-avatar';
            setGroupsAccountAvatar(avatar, account);

            var info = document.createElement('span');
            info.className = 'groups-account-option-info';
            var strong = document.createElement('strong');
            strong.textContent = getAccountName(account);
            var small = document.createElement('small');
            small.textContent = accountStateText(account);
            info.appendChild(strong);
            info.appendChild(small);

            var mark = document.createElement('span');
            mark.className = 'groups-account-option-mark';
            if (id === state.accountId) {
                var checkIcon = document.createElement('span');
                checkIcon.className = 'nexus-lucide nexus-lucide-check';
                checkIcon.setAttribute('aria-hidden', 'true');
                mark.appendChild(checkIcon);
            }

            button.appendChild(avatar);
            button.appendChild(info);
            button.appendChild(mark);
            button.addEventListener('click', function () { selectGroupsAccount(id); });
            menu.appendChild(button);
        });
    }

    function setGroupsAccountMenu(open) {
        var picker = $('groupsAccountPicker');
        var menu = $('groupsAccountMenu');
        var trigger = $('groupsAccountTrigger');
        if (!picker || !menu || !trigger) return;
        var shouldOpen = !!open && state.accounts.length > 0;
        menu.hidden = !shouldOpen;
        trigger.setAttribute('aria-expanded', shouldOpen ? 'true' : 'false');
        picker.classList.toggle('open', shouldOpen);
    }

    function selectGroupsAccount(accountId) {
        accountId = String(accountId || '').trim();
        if (accountId === state.accountId) {
            setGroupsAccountMenu(false);
            return;
        }
        state.accountId = accountId;
        localStorage.setItem('zmkt_groups_account_id', state.accountId || '');
        renderGroupsAccountSelected();
        renderGroupsAccountMenu();
        setGroupsAccountMenu(false);

        state.selectedGroupId = '';
        state.selectedGroup = null;
        state.members = [];
        state.selectedMembers.clear();
        groupsLoadGroups();
        renderDetailEmpty();
    }

    async function groupsReloadAccounts() {
        var nameEl = $('groupsAccountName');
        var stateEl = $('groupsAccountState');
        if (nameEl) nameEl.textContent = 'Đang tải tài khoản...';
        if (stateEl) stateEl.textContent = 'Vui lòng chờ';
        setGroupsAccountMenu(false);
        try {
            var res = await fetch('/api/accounts');
            var data = await res.json();
            if (!data.success && data.error) throw new Error(data.error);
            state.accounts = data.accounts || [];
            renderAccounts();
        } catch (err) {
            state.accounts = [];
            state.accountId = '';
            renderGroupsAccountSelected();
            renderGroupsAccountMenu();
            showToast('Lỗi tải tài khoản: ' + err.message, 'error');
        }
    }

    function renderAccounts() {
        var last = localStorage.getItem('zmkt_groups_account_id') || '';
        var currentValid = state.accounts.some(function (a) { return getAccountId(a) === state.accountId; });
        var savedValid = state.accounts.some(function (a) { return getAccountId(a) === last; });
        var first = state.accounts[0] ? getAccountId(state.accounts[0]) : '';
        state.accountId = currentValid ? state.accountId : (savedValid ? last : first);

        renderGroupsAccountSelected();
        renderGroupsAccountMenu();
        if (state.accountId) {
            localStorage.setItem('zmkt_groups_account_id', state.accountId);
            groupsLoadGroups();
        } else {
            groupsRenderList();
            renderDetailEmpty();
            setStatus('Vui lòng thêm tài khoản để xem nhóm.', 'warn');
        }
    }

    function groupsOnAccountChange() {
        var hidden = $('groupsAccountSelect');
        selectGroupsAccount(hidden ? hidden.value : '');
    }

    async function groupsLoadGroups() {
        state.groups = [];
        state.selectedGroupId = '';
        state.selectedGroup = null;
        state.members = [];
        state.selectedMembers.clear();
        groupsRenderList();
        renderDetailEmpty();

        if (!state.accountId) {
            setStatus('Vui lòng chọn tài khoản để xem nhóm.', 'warn');
            return;
        }
        setStatus('Đang tải danh sách nhóm đã lưu...', 'loading');
        try {
            var data = await fetchPersonalGroups(state.accountId);
            state.groups = data.groups || [];
            if (state.groups.length) {
                setStatus('');
            } else {
                setStatus('Chưa có dữ liệu nhóm. Bấm Làm mới để mở tài khoản Zalo và đồng bộ nhóm.', 'warn');
            }
            groupsRenderList();
        } catch (err) {
            setStatus('Lỗi tải nhóm: ' + err.message, 'error');
            groupsRenderList();
        }
    }

    async function groupsPollSyncedGroups(accountId, startedAt) {
        var deadline = Date.now() + 90000;
        var syncedOnce = false;
        var syncedAt = 0;

        while (Date.now() < deadline && state.accountId === accountId) {
            await sleep(syncedOnce ? 3000 : 2000);

            try {
                var data = await fetchPersonalGroups(accountId);
                if (state.accountId !== accountId) return;

                state.groups = data.groups || [];
                groupsRenderList();

                var groupsSyncedAt = Number(data.groupsSyncedAt || 0);
                var fresh = groupsSyncedAt && (!startedAt || groupsSyncedAt >= Number(startedAt || 0));
                var total = state.groups.length;
                var pending = state.groups.filter(function (g) {
                    return String(g.fetchStatus || '').toLowerCase() === 'pending';
                }).length;
                var errored = state.groups.filter(function (g) {
                    return String(g.fetchStatus || '').toLowerCase() === 'error';
                }).length;

                if (fresh) {
                    if (!syncedOnce) {
                        syncedOnce = true;
                        syncedAt = Date.now();
                    }

                    if (total > 0 && pending === 0) {
                        var doneMsg = 'Đã cập nhật ' + total + ' nhóm từ tài khoản đang chọn.';
                        if (errored) doneMsg += ' Có ' + errored + ' nhóm chưa lấy được chi tiết.';
                        setStatus(doneMsg, errored ? 'warn' : 'success');
                        showToast('Đã đồng bộ danh sách nhóm.', 'success');
                        return;
                    }

                    setStatus('Đã bắt được ' + total + ' nhóm. Đang cập nhật chi tiết nhóm' + (pending ? ' (' + pending + ' nhóm đang chờ)' : '') + '...', 'loading');

                    if (syncedOnce && Date.now() - syncedAt > 25000) {
                        setStatus('Đã cập nhật ' + total + ' nhóm. Chi tiết nhóm còn thiếu sẽ tiếp tục được xử lý nền.', 'success');
                        return;
                    }
                } else if (total > 0) {
                    setStatus('Đang chờ dữ liệu nhóm mới từ Zalo Web. Tạm hiển thị ' + total + ' nhóm đã lưu trước đó...', 'loading');
                } else {
                    setStatus('Đã mở Zalo. Đang chờ Zalo Web gọi API danh sách nhóm...', 'loading');
                }
            } catch (err) {
                setStatus('Đang chờ dữ liệu nhóm: ' + err.message, 'loading');
            }
        }

        if (state.groups.length) {
            setStatus('Đã hiển thị ' + state.groups.length + ' nhóm đã lưu, nhưng chưa bắt được dữ liệu nhóm mới từ Zalo Web. Hãy kiểm tra Chrome đã đăng nhập Zalo rồi bấm Làm mới lại.', 'warn');
        } else {
            setStatus('Chưa bắt được danh sách nhóm. Hãy kiểm tra cửa sổ Chrome đã đăng nhập Zalo Web và không bị chặn mạng/proxy.', 'warn');
        }
    }

    async function groupsOpenAccountAndRefreshGroups() {
        if (state.refreshingGroups) return;

        if (!state.accountId) {
            setStatus('Vui lòng chọn tài khoản trước khi làm mới nhóm.', 'warn');
            showToast('Chưa chọn tài khoản.', 'warn');
            return;
        }

        state.refreshingGroups = true;
        state.groups = [];
        state.selectedGroupId = '';
        state.selectedGroup = null;
        state.members = [];
        state.selectedMembers.clear();
        groupsRenderList();
        renderDetailEmpty();
        setRefreshButtonLoading(true, 'Đang mở...');
        setStatus('Đang mở tài khoản Zalo đã chọn...', 'loading');

        try {
            var res = await fetch('/api/groups/refresh-account', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ accountId: state.accountId })
            });
            var data = await res.json();
            if (!data.success) throw new Error(data.error || 'Không mở được tài khoản Zalo');

            setRefreshButtonLoading(true, 'Đang đồng bộ...');
            setStatus('Đã mở tài khoản Zalo. Đang bắt API danh sách nhóm...', 'loading');
            await groupsReloadAccounts();
            state.accountId = data.accountId || state.accountId;
            if ($('groupsAccountSelect')) $('groupsAccountSelect').value = state.accountId;
            await groupsPollSyncedGroups(state.accountId, data.startedAt || 0);
        } catch (err) {
            setStatus('Lỗi làm mới nhóm: ' + err.message, 'error');
            showToast('Lỗi làm mới nhóm: ' + err.message, 'error');
        } finally {
            state.refreshingGroups = false;
            setRefreshButtonLoading(false);
        }
    }

    function groupsRenderList() {
        var list = $('groupsList');
        var empty = $('groupsEmpty');
        var totalText = $('groupsTotalText');
        if (!list) return;
        var q = String(($('groupsSearchInput') || {}).value || '').toLowerCase().trim();
        var groups = (state.groups || []).filter(function (g) {
            return !q || getGroupName(g).toLowerCase().indexOf(q) >= 0 || getGroupId(g).indexOf(q) >= 0;
        });
        if (totalText) totalText.textContent = (state.groups || []).length + ' nhóm';
        if (!groups.length) {
            list.innerHTML = '';
            if (empty) empty.style.display = state.groups.length ? 'none' : 'block';
            return;
        }
        if (empty) empty.style.display = 'none';
        list.innerHTML = groups.map(function (g) {
            var gid = getGroupId(g);
            var name = getGroupName(g);
            var avatar = normalizeAvatar(g.fullAvt || g.avatar || g.grid_fullAvt || g.grid_avatar || '');
            var count = g.memberCount || g.grid_totalMember || g.totalMember || g.total || 0;
            var avt = avatar
                ? '<img src="' + esc(avatar) + '" alt="">'
                : '<div class="groups-list-avatar fallback">👥</div>';
            return '<button type="button" class="groups-list-item ' + (gid === state.selectedGroupId ? 'active' : '') + '" onclick="groupsSelectGroup(\'' + esc(gid) + '\')">'
                + '<div class="groups-list-avatar">' + avt + '</div>'
                + '<div class="groups-list-info"><b>' + esc(name) + '</b><span>' + esc(count) + ' thành viên</span></div>'
                + '<div class="groups-list-arrow">›</div>'
                + '</button>';
        }).join('');
    }

    function groupsSelectGroup(groupId) {
        var g = state.groups.find(function (x) { return getGroupId(x) === groupId; });
        if (!g) return;
        state.selectedGroupId = groupId;
        state.selectedGroup = g;
        state.members = [];
        state.selectedMembers.clear();
        state.memberFilter = 'all';
        if ($('groupsMemberSearch')) $('groupsMemberSearch').value = '';
        groupsRenderList();
        renderGroupDetail();
        groupsRenderMembers();
        groupsLoadMembers();
    }

    function renderDetailEmpty() {
        var none = $('groupsNoSelection');
        var content = $('groupsDetailContent');
        if (none) none.style.display = 'flex';
        if (content) content.style.display = 'none';
    }

    function renderGroupDetail() {
        var none = $('groupsNoSelection');
        var content = $('groupsDetailContent');
        if (none) none.style.display = 'none';
        if (content) content.style.display = 'block';

        var g = state.selectedGroup || {};
        var gid = getGroupId(g);
        var name = getGroupName(g);
        var avatar = normalizeAvatar(g.fullAvt || g.avatar || g.grid_fullAvt || g.grid_avatar || '');
        var count = g.memberCount || g.grid_totalMember || g.totalMember || g.total || 0;

        if ($('groupsDetailName')) $('groupsDetailName').textContent = name;
        if ($('groupsDetailMembers')) $('groupsDetailMembers').textContent = count + ' thành viên';
        if ($('groupsDetailId')) $('groupsDetailId').textContent = 'ID: ' + gid;
        var img = $('groupsDetailAvatar');
        var fallback = $('groupsDetailAvatarFallback');
        if (avatar && img) {
            img.src = avatar;
            img.style.display = 'block';
            if (fallback) fallback.style.display = 'none';
        } else {
            if (img) img.style.display = 'none';
            if (fallback) fallback.style.display = 'flex';
        }
        updateActionButtons();
    }

    async function groupsLoadMembers() {
        if (!state.accountId || !state.selectedGroupId) return;
        var requestSeq = ++state.membersRequestSeq;
        var requestGroupId = state.selectedGroupId;
        state.loadingMembers = true;
        state.members = [];
        state.selectedMembers.clear();
        var loading = $('groupsMembersLoading');
        var loadState = $('groupsAutoLoadState');
        if (loading) loading.style.display = 'block';
        if (loadState) loadState.textContent = 'Đang lấy thành viên bằng cơ chế Lấy thành viên nhóm...';
        groupsRenderMembers();
        updateActionButtons();
        try {
            // Dùng cùng endpoint /run và cùng payload với nút “Lấy thành viên”.
            // Không dùng logic riêng /api/groups/members nữa để tránh sai khác dữ liệu giới tính/ngày sinh/số điện thoại.
            var body = new URLSearchParams({
                account_id: state.accountId,
                accountId: state.accountId,
                group_link: requestGroupId,
                group_id: requestGroupId
            });
            var res = await fetch('/run', {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: body
            });
            var data = await res.json();
            if (requestSeq !== state.membersRequestSeq || requestGroupId !== state.selectedGroupId) return;
            if (data.error || data.success === false) throw new Error(data.error || 'Không lấy được thành viên nhóm');

            state.members = data.data || data.members || [];
            window._groupsLastFetchedData = state.members;

            var groupInfo = data.groupInfo || {};
            if (groupInfo.name && $('groupsDetailName')) $('groupsDetailName').textContent = groupInfo.name;
            if ($('groupsDetailMembers')) $('groupsDetailMembers').textContent = (data.total || state.members.length) + ' thành viên';
            if (loadState) loadState.textContent = 'Đã tải ' + state.members.length + ' thành viên. Bấm Xem để tải chi tiết từng người.';
            showToast('Đã tải ' + state.members.length + ' thành viên.', 'success');
            groupsRenderMembers();
        } catch (err) {
            if (requestSeq !== state.membersRequestSeq) return;
            if (loadState) loadState.textContent = 'Tải thành viên lỗi';
            showToast('Lỗi tải thành viên: ' + err.message, 'error');
        } finally {
            if (requestSeq === state.membersRequestSeq) {
                state.loadingMembers = false;
                if (loading) loading.style.display = 'none';
                groupsRenderMembers();
                updateActionButtons();
            }
        }
    }

    function getFilteredMembers() {
        var q = String(($('groupsMemberSearch') || {}).value || '').toLowerCase().trim();
        return (state.members || []).filter(function (m) {
            var uid = getMemberId(m).toLowerCase();
            var name = getMemberName(m).toLowerCase();
            var phone = getMemberPhoneText(m).toLowerCase();
            return !q || name.indexOf(q) >= 0 || phone.indexOf(q) >= 0 || uid.indexOf(q) >= 0;
        });
    }

    function groupsRenderMembers() {
        var body = $('groupsMembersBody');
        var empty = $('groupsMembersEmpty');
        if (!body) return;
        var members = getFilteredMembers();
        if (!members.length) {
            body.innerHTML = '';
            if (empty) empty.style.display = state.loadingMembers ? 'none' : 'flex';
        } else {
            if (empty) empty.style.display = 'none';
            body.innerHTML = members.map(function (m) {
                var uid = getMemberId(m);
                var name = getMemberName(m);
                var avatar = normalizeAvatar(m.avatar || m.avatarUrl || m.avt || '');
                var phone = getMemberPhoneText(m);
                var friend = isMemberFriend(m);
                var checked = state.selectedMembers.has(uid) ? 'checked' : '';
                var gender = getMemberGenderText(m);
                var dob = getMemberDobText(m);
                var friendText = friend ? 'Đã kết bạn' : 'Chưa';
                var friendClass = friend ? 'success' : 'muted';
                var avatarHtml = avatar
                    ? '<img class="groups-member-avatar" src="' + esc(avatar) + '" alt="" onerror="this.style.display=\'none\'">'
                    : '<div class="groups-member-avatar fallback">' + esc(name.charAt(0).toUpperCase() || '?') + '</div>';
                return '<tr>'
                    + '<td><input type="checkbox" ' + checked + ' onchange="groupsToggleMember(\'' + esc(uid) + '\')"></td>'
                    + '<td>' + avatarHtml + '</td>'
                    + '<td><div class="groups-member-name">' + esc(name) + '</div></td>'
                    + '<td>' + esc(gender) + '</td>'
                    + '<td><span class="muted">' + esc(dob) + '</span></td>'
                    + '<td>' + (phone ? esc(phone) : '<span class="muted">Chưa có</span>') + '</td>'
                    + '<td><span class="pill ' + friendClass + '">' + esc(friendText) + '</span></td>'
                    + '<td><button class="btn btn-sm btn-primary" onclick="event.stopPropagation(); groupsShowMemberProfile(\'' + esc(uid) + '\')">Xem</button></td>'
                    + '</tr>';
            }).join('');
        }
        updateSelectedUI();
    }

    function groupsSetMemberFilter(filter) {
        state.memberFilter = filter || 'all';
        groupsRenderMembers();
    }

    function groupsToggleMember(uid) {
        if (state.selectedMembers.has(uid)) state.selectedMembers.delete(uid);
        else state.selectedMembers.add(uid);
        groupsRenderMembers();
    }

    function groupsToggleSelectAllMembers(cb) {
        var members = getFilteredMembers();
        if (cb && cb.checked) members.forEach(function (m) { var uid = getMemberId(m); if (uid) state.selectedMembers.add(uid); });
        else members.forEach(function (m) { state.selectedMembers.delete(getMemberId(m)); });
        groupsRenderMembers();
    }

    function groupsClearSelection() {
        state.selectedMembers.clear();
        groupsRenderMembers();
    }

    function updateSelectedUI() {
        var bar = $('groupsSelectedBar');
        var count = state.selectedMembers.size;
        if ($('groupsSelectedCount')) $('groupsSelectedCount').textContent = count;
        if (bar) bar.style.display = count ? 'flex' : 'none';
        var all = $('groupsSelectAllMembers');
        var filtered = getFilteredMembers().map(getMemberId).filter(Boolean);
        var selectedFiltered = filtered.filter(function (uid) { return state.selectedMembers.has(uid); }).length;
        if (all) {
            all.checked = filtered.length > 0 && selectedFiltered === filtered.length;
            all.indeterminate = selectedFiltered > 0 && selectedFiltered < filtered.length;
        }
        updateActionButtons();
    }

    function updateActionButtons() {
        var hasMembers = (state.members || []).length > 0;
        var inviteBtn = $('groupsInviteBtn');
        var createBtn = $('groupsCreateBtn');
        if (inviteBtn) inviteBtn.disabled = !hasMembers;
        if (createBtn) createBtn.disabled = !hasMembers;
    }

    function getInviteMemberIds() {
        if (state.selectedMembers.size) return Array.from(state.selectedMembers);
        return (state.members || []).map(getMemberId).filter(Boolean);
    }

    function groupsOpenInviteModal() {
        var memberIds = getInviteMemberIds();
        if (!memberIds.length) {
            showToast('Chưa có thành viên để mời. Hãy chọn một nhóm trước.', 'warn');
            return;
        }
        state.inviteSelectedGroups.clear();
        var current = state.selectedGroupId;
        if (current) state.inviteSelectedGroups.delete(current);
        var bd = $('groupsInviteBackdrop');
        if (bd) bd.style.display = 'flex';
        if ($('groupsInviteSummary')) {
            $('groupsInviteSummary').textContent = (state.selectedMembers.size ? 'Đang chọn ' + memberIds.length + ' thành viên.' : 'Chưa tick riêng, sẽ dùng toàn bộ ' + memberIds.length + ' thành viên của nhóm đang xem.')
        }
        if ($('groupsInviteSearch')) $('groupsInviteSearch').value = '';
        groupsRenderInviteList();
    }

    function groupsCloseInviteModal(event) {
        if (event && event.target && event.currentTarget && event.target !== event.currentTarget) return;
        var bd = $('groupsInviteBackdrop');
        if (bd) bd.style.display = 'none';
        var st = $('groupsInviteStatus');
        if (st) st.textContent = '';
    }

    function groupsRenderInviteList() {
        var list = $('groupsInviteList');
        if (!list) return;
        var q = String(($('groupsInviteSearch') || {}).value || '').toLowerCase().trim();
        var groups = (state.groups || []).filter(function (g) {
            var gid = getGroupId(g);
            if (gid === state.selectedGroupId) return false;
            return !q || getGroupName(g).toLowerCase().indexOf(q) >= 0 || gid.indexOf(q) >= 0;
        });
        if (!groups.length) {
            list.innerHTML = '<div class="groups-empty small"><b>Không có nhóm đích phù hợp</b><p>Danh sách nhóm đích không bao gồm nhóm đang xem.</p></div>';
            return;
        }
        list.innerHTML = groups.map(function (g) {
            var gid = getGroupId(g);
            var name = getGroupName(g);
            var count = g.memberCount || g.grid_totalMember || g.totalMember || g.total || 0;
            var checked = state.inviteSelectedGroups.has(gid) ? 'checked' : '';
            return '<label class="groups-invite-item">'
                + '<input type="checkbox" ' + checked + ' onchange="groupsToggleInviteGroup(\'' + esc(gid) + '\')">'
                + '<span><b>' + esc(name) + '</b><small>' + esc(count) + ' thành viên</small></span>'
                + '</label>';
        }).join('');
    }

    function groupsToggleInviteGroup(gid) {
        if (state.inviteSelectedGroups.has(gid)) state.inviteSelectedGroups.delete(gid);
        else state.inviteSelectedGroups.add(gid);
    }

    async function groupsSubmitInvite() {
        var memberIds = getInviteMemberIds();
        var groupIds = Array.from(state.inviteSelectedGroups);
        var st = $('groupsInviteStatus');
        if (!groupIds.length) {
            if (st) st.textContent = 'Vui lòng chọn ít nhất một nhóm đích.';
            return;
        }
        var btn = $('groupsInviteSubmit');
        if (btn) { btn.disabled = true; btn.textContent = 'Đang mời...'; }
        if (st) st.textContent = 'Đang gửi lời mời vào ' + groupIds.length + ' nhóm...';
        try {
            var res = await fetch('/api/groups/invite', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ accountId: state.accountId, groupIds: groupIds, userIds: memberIds, batchSize: 50 })
            });
            var data = await res.json();
            if (!res.ok && !data.partial) throw new Error(data.error || data.message || 'Mời vào nhóm thất bại');
            showToast(data.message || 'Đã gửi lời mời vào nhóm.', data.success ? 'success' : 'warn');
            groupsCloseInviteModal();
        } catch (err) {
            if (st) st.textContent = err.message;
            showToast('Lỗi mời vào nhóm: ' + err.message, 'error');
        } finally {
            if (btn) { btn.disabled = false; btn.textContent = 'Mời'; }
        }
    }

    function groupsOpenCreateGroupHint() {
        showToast('Tạo nhóm mới đang nằm ở trang Lấy thành viên. Bản vá này tập trung quản lý nhóm và mời vào nhóm có sẵn.', 'info');
    }

    function groupsCopyCurrentGroupId() {
        groupsCopyText(state.selectedGroupId || '');
    }

    function groupsCopyText(text) {
        text = String(text || '');
        if (!text) return;
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(function () { showToast('Đã copy.', 'success'); });
        } else {
            var ta = document.createElement('textarea');
            ta.value = text;
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            document.body.removeChild(ta);
            showToast('Đã copy.', 'success');
        }
    }

    function groupsPopulateMemberProfile(profile) {
        profile = profile || {};
        var uid = String(profile.userId || profile.id || '').trim();
        var name = profile.zaloName || profile.displayName || profile.dName || profile.name || 'Không tên';
        var avatar = normalizeAvatar(profile.avatar || profile.avatarUrl || profile.avt || '');
        var img = $('groupsPfAvatar');
        var ph = $('groupsPfAvatarPlaceholder');
        if (avatar && img) {
            img.src = avatar;
            img.style.display = 'block';
            if (ph) ph.style.display = 'none';
        } else {
            if (img) img.style.display = 'none';
            if (ph) ph.style.display = 'block';
        }
        if ($('groupsPfName')) $('groupsPfName').textContent = name;
        if ($('groupsPfUid')) $('groupsPfUid').textContent = uid ? ('ID: ' + uid) : 'ID: -';
        var fields = [
            ['Tên Zalo', profile.zaloName || '-'],
            ['Tên hiển thị', profile.displayName || profile.dName || '-'],
            ['Username', profile.username || profile.globalId || '-'],
            ['User ID', uid || '-'],
            ['Số điện thoại', profile.phoneNumber || profile.phone || '-'],
            ['Trạng thái', profile.status || '-'],
            ['Giới tính', getMemberGenderText(profile)],
            ['Ngày sinh', getMemberDobText(profile)],
            ['Kết bạn', isMemberFriend(profile) ? 'Đã kết bạn' : 'Chưa kết bạn']
        ];
        var html = fields.map(function (f) {
            return '<div style="display:flex;align-items:flex-start;border-bottom:1px solid var(--border);padding:8px 0;font-size:13px">'
                + '<span style="color:var(--text-secondary);min-width:120px;font-weight:500">' + esc(f[0]) + ':</span>'
                + '<span style="flex:1;word-break:break-all">' + esc(f[1]) + '</span>'
                + '</div>';
        }).join('');
        if ($('groupsPfFields')) $('groupsPfFields').innerHTML = html;
    }

    async function groupsShowMemberProfile(userId) {
        userId = String(userId || '').trim();
        if (!userId) return;
        var backdrop = $('groupsProfileBackdrop');
        var local = (state.members || []).find(function (m) { return getMemberId(m) === userId; });
        if (local) groupsPopulateMemberProfile(local);
        if (backdrop) {
            backdrop.classList.add('visible');
            backdrop.style.display = 'flex';
        }
        if ($('groupsPfStatus')) $('groupsPfStatus').textContent = 'Đang cập nhật thông tin mới nhất...';
        try {
            var res = await fetch('/api/single-profile', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ uid: userId, accountId: state.accountId })
            });
            var data = await res.json();
            if (data.profile) {
                // Gộp dữ liệu chi tiết vừa lấy vào bản ghi hiện có (giữ nguyên trường cũ
                // nếu API mới không trả về), giống cơ chế đồng bộ ở trang Lấy thành viên.
                var detailed = Object.assign({}, local || {}, data.profile || {});
                detailed.userId = detailed.userId || userId;
                groupsPopulateMemberProfile(detailed);
                if ($('groupsPfStatus')) $('groupsPfStatus').textContent = '';

                var idx = (state.members || []).findIndex(function (m) { return getMemberId(m) === userId; });
                if (idx >= 0) {
                    state.members[idx] = Object.assign({}, state.members[idx], detailed);
                    window._groupsLastFetchedData = state.members;
                    groupsRenderMembers();
                }
            } else if (data.error) {
                if ($('groupsPfStatus')) $('groupsPfStatus').textContent = data.error;
            }
        } catch (err) {
            if ($('groupsPfStatus')) $('groupsPfStatus').textContent = 'Không cập nhật được profile: ' + err.message;
        }
    }

    function groupsCloseMemberProfile() {
        var backdrop = $('groupsProfileBackdrop');
        if (backdrop) {
            backdrop.classList.remove('visible');
            backdrop.style.display = 'none';
        }
    }

    // ─── Xem ảnh full size (đồng bộ với trang Lấy thành viên) ─────────────────

    function groupsOpenAvatarPreview(url) {
        var backdrop = $('groupsAvatarPreviewBackdrop');
        var img = $('groupsAvatarPreviewImg');
        if (!backdrop || !img || !url) return;
        img.src = url;
        backdrop.hidden = false;
        backdrop.setAttribute('aria-hidden', 'false');
        backdrop.classList.add('is-open');
    }

    function groupsCloseAvatarPreview() {
        var backdrop = $('groupsAvatarPreviewBackdrop');
        var img = $('groupsAvatarPreviewImg');
        if (!backdrop) return;
        backdrop.classList.remove('is-open');
        backdrop.hidden = true;
        backdrop.setAttribute('aria-hidden', 'true');
        if (img) img.removeAttribute('src');
    }

    async function groupsHandlePfAvatarClick(event) {
        event.stopPropagation();
        var avatar = $('groupsPfAvatar');
        if (!avatar || !avatar.src || avatar.style.display === 'none') return;
        var uidText = ($('groupsPfUid') || {}).textContent || '';
        var uid = uidText.replace(/^ID:\s*/i, '').trim();
        if (!uid || !state.accountId) { groupsOpenAvatarPreview(avatar.src); return; }

        var oldTitle = avatar.title || '';
        avatar.title = 'Đang lấy ảnh full size...';
        try {
            var res = await fetch('/api/get-avatar', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ fid: uid, uid: uid, userId: uid, accountId: state.accountId, account_id: state.accountId })
            });
            var data = await res.json();
            var fullUrl = normalizeAvatar(data.bk_full_avatar || data.avatar_url || data.full_avatar || '');
            groupsOpenAvatarPreview(fullUrl || avatar.src);
        } catch (err) {
            groupsOpenAvatarPreview(avatar.src);
        } finally {
            avatar.title = oldTitle || 'Nhấn để xem ảnh full size';
        }
    }

    function groupsBindAvatarPreview() {
        var backdrop = $('groupsAvatarPreviewBackdrop');
        var img = $('groupsAvatarPreviewImg');
        var avatar = $('groupsPfAvatar');
        if (avatar) {
            avatar.style.cursor = 'zoom-in';
            avatar.addEventListener('click', groupsHandlePfAvatarClick);
        }
        if (backdrop) {
            backdrop.addEventListener('click', function (event) {
                if (event.target === backdrop) groupsCloseAvatarPreview();
            });
        }
        if (img) {
            img.addEventListener('click', function (event) {
                event.stopPropagation();
                groupsCloseAvatarPreview();
            });
        }
    }

    document.addEventListener('DOMContentLoaded', function () {
        var trigger = $('groupsAccountTrigger');
        var picker = $('groupsAccountPicker');
        if (trigger) {
            trigger.addEventListener('click', function () {
                var isOpen = picker && picker.classList.contains('open');
                setGroupsAccountMenu(!isOpen);
            });
        }
        if (picker) picker.addEventListener('click', function (event) { event.stopPropagation(); });
        document.addEventListener('click', function () { setGroupsAccountMenu(false); });
        document.addEventListener('keydown', function (event) {
            if (event.key !== 'Escape') return;
            setGroupsAccountMenu(false);
            groupsCloseAvatarPreview();
        });
        groupsBindAvatarPreview();
        groupsReloadAccounts();
    });

    window.groupsReloadAccounts = groupsReloadAccounts;
    window.groupsOnAccountChange = groupsOnAccountChange;
    window.groupsLoadGroups = groupsLoadGroups;
    window.groupsOpenAccountAndRefreshGroups = groupsOpenAccountAndRefreshGroups;
    window.groupsRenderList = groupsRenderList;
    window.groupsSelectGroup = groupsSelectGroup;
    window.groupsLoadMembers = groupsLoadMembers;
    window.groupsRenderMembers = groupsRenderMembers;
    window.groupsSetMemberFilter = groupsSetMemberFilter;
    window.groupsToggleMember = groupsToggleMember;
    window.groupsToggleSelectAllMembers = groupsToggleSelectAllMembers;
    window.groupsClearSelection = groupsClearSelection;
    window.groupsOpenInviteModal = groupsOpenInviteModal;
    window.groupsCloseInviteModal = groupsCloseInviteModal;
    window.groupsRenderInviteList = groupsRenderInviteList;
    window.groupsToggleInviteGroup = groupsToggleInviteGroup;
    window.groupsSubmitInvite = groupsSubmitInvite;
    window.groupsOpenCreateGroupHint = groupsOpenCreateGroupHint;
    window.groupsCopyCurrentGroupId = groupsCopyCurrentGroupId;
    window.groupsCopyText = groupsCopyText;
    window.groupsShowMemberProfile = groupsShowMemberProfile;
    window.groupsCloseMemberProfile = groupsCloseMemberProfile;
})();
