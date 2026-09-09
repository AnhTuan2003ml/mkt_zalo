(function () {
    'use strict';

    var state = {
        accounts: [],
        extraAccountIds: new Set(),
        groups: [],
        targetMode: 'new',
        pollingTask: false,
        jobsTimer: null,
        accountMenuOpen: false,
        accountSearch: '',
        jobs: [],
        jobFilter: 'all',
        jobSearch: '',
        selectedJobId: '',
        detailRequestId: 0,
        detailOverlayOpen: false,
        detailTab: 'overview',
        detailJob: null,
        memberSearch: '',
        memberFilter: 'all',
        sourceGroupInfo: null,
        sourcePreviewToken: 0,
        sourcePreviewController: null,
        sourceDebounceTimer: null,
        targetGroup: null,
        groupLinkPollCount: 0,
        groupLinkPollTimer: null,
        pickerOpen: false,
        pickerSearch: '',
        pickerSort: 'name',
        highlightJobId: '',
        campaignModalId: '',
        selectMode: false,
        selected: {}
    };

    var ICON_DETAIL = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7Z"/><circle cx="12" cy="12" r="3"/></svg>';
    var ICON_VERIFY = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6v5h-5"/><path d="M4 18v-5h5"/><path d="M18.5 9A7 7 0 0 0 6.2 6.2L4 8M5.5 15A7 7 0 0 0 17.8 17.8L20 16"/></svg>';
    var ICON_PAUSE = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>';
    var ICON_RESUME = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="6 4 20 12 6 20 6 4"/></svg>';

    function $(id) { return document.getElementById(id); }

    function storageGet(key) {
        try { return window.localStorage ? (window.localStorage.getItem(key) || '') : ''; }
        catch (error) { return ''; }
    }

    function storageSet(key, value) {
        try { if (window.localStorage) window.localStorage.setItem(key, value); }
        catch (error) { /* Trình duyệt chặn storage: tiếp tục chạy không ghi nhớ. */ }
    }

    function esc(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function safeImageUrl(value) {
        var url = String(value || '').trim();
        if (!url) return '';
        if (url.indexOf('//') === 0) return 'https:' + url;
        if (/^https?:\/\//i.test(url) || /^data:image\//i.test(url)) return url;
        return '';
    }

    function initials(name) {
        var text = String(name || '?').trim();
        if (!text) return '?';
        var words = text.split(/\s+/).filter(Boolean);
        if (words.length === 1) return words[0].slice(0, 1).toUpperCase();
        return (words[0].slice(0, 1) + words[words.length - 1].slice(0, 1)).toUpperCase();
    }

    function shortId(id) {
        var text = String(id == null ? '' : id).trim();
        if (text.length <= 14) return text;
        return text.slice(0, 6) + '…' + text.slice(-6);
    }

    // ─── Toast (dùng lại component .member-toast-stack đã có toàn cục) ────
    function ensureToastStack() {
        var stack = $('groupCopyToastStack');
        if (!stack) {
            stack = document.createElement('div');
            stack.id = 'groupCopyToastStack';
            stack.className = 'member-toast-stack';
            stack.setAttribute('aria-live', 'polite');
            document.body.appendChild(stack);
        }
        return stack;
    }

    function showToast(message, type) {
        var text = String(message || '').trim();
        if (!text) return;
        type = type || 'info';
        var stack = ensureToastStack();
        var toast = document.createElement('div');
        toast.className = 'member-toast ' + type;
        var icon = type === 'success' ? '✓' : (type === 'error' ? '!' : (type === 'warning' || type === 'warn' ? '⚠' : 'i'));
        toast.innerHTML = '<span class="member-toast-icon">' + icon + '</span><span class="member-toast-text"></span><button type="button" class="member-toast-close" aria-label="Đóng">×</button>';
        toast.querySelector('.member-toast-text').textContent = text;
        function removeToast() {
            toast.classList.add('hiding');
            setTimeout(function () { if (toast.parentNode) toast.parentNode.removeChild(toast); }, 220);
        }
        toast.querySelector('.member-toast-close').addEventListener('click', removeToast);
        stack.appendChild(toast);
        setTimeout(removeToast, type === 'error' ? 5200 : 3600);
    }

    // ─── Helpers dữ liệu tài khoản / nhóm ───────────────────────────────────
    function getAccountId(account) {
        return String((account && (account.accountId || account.id || account.account_id)) || '').trim();
    }

    function getAccountName(account) {
        return String((account && (account.name || account.displayName || account.zaloName || account.phoneNumber)) || getAccountId(account) || 'Tài khoản').trim();
    }

    function getAccountAvatar(account) {
        return safeImageUrl(account && (account.avatarUrl || account.avatar || account.profileAvatar));
    }

    function accountReady(account) {
        return !!(window.NexusSession && NexusSession.accountReady(account, true));
    }

    function getGroupId(group) {
        return String((group && (group.groupId || group.gridId || group.id || group.gid)) || '').trim();
    }

    function getGroupLink(group) {
        var link = String((group && (group.groupLink || group.group_link || group.inviteLink || group.link)) || '').trim();
        if (!link) return '';
        if (link.indexOf('//') === 0) return 'https:' + link;
        if (/^https?:\/\//i.test(link)) return link;
        link = link.replace(/^\/+|\/+$/g, '');
        return /^zalo\.me\/g\//i.test(link) ? ('https://' + link) : ('https://zalo.me/g/' + link);
    }

    function getGroupLinkLabel(group) {
        var link = getGroupLink(group);
        if (link) return link;
        var status = String((group && group.linkStatus) || '').toLowerCase();
        return status === 'error' ? 'Chưa lấy được link nhóm' : 'Đang tự tạo link nhóm...';
    }

    function getGroupName(group) {
        var gid = getGroupId(group);
        return String((group && (group.name || group.groupName || group.grid_name || group.title)) || ('Nhóm ' + gid.slice(0, 8))).trim();
    }

    function getGroupMemberCount(group) {
        return Number((group && (group.memberCount || group.totalMember || group.total)) || 0);
    }

    function setStatus(message, type) {
        var el = $('groupCopyStatus');
        if (!el) return;
        el.hidden = !message;
        el.className = 'group-copy-status ' + (type || '');
        el.textContent = message || '';
    }

    function friendlyFetchError(error, fallback) {
        var msg = String((error && error.message) || error || '').trim();
        if (!msg || /failed to fetch/i.test(msg) || /networkerror/i.test(msg) || /load failed/i.test(msg)) {
            return fallback || 'Không thể kết nối máy chủ. Vui lòng kiểm tra lại kết nối mạng và thử lại.';
        }
        return msg;
    }

    function setDefaultStartTime() {
        var input = $('groupCopyStartAt');
        if (!input || input.value) return;
        var date = new Date();
        var local = new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
        input.value = local;
    }

    function formatDateTime(value) {
        if (!value) return '-';
        var date = new Date(value);
        if (isNaN(date.getTime())) return String(value);
        return date.toLocaleString('vi-VN', {
            day: '2-digit', month: '2-digit', year: 'numeric',
            hour: '2-digit', minute: '2-digit'
        });
    }

    function dailyLimit(job) {
        return Number((job && (job.dailyLimit || job.batchSize)) || 0);
    }

    function statusLabel(status) {
        var labels = {
            pending: 'Chờ chạy',
            running: 'Đang chạy',
            done: 'Hoàn thành',
            monitoring: 'Đang theo dõi',
            expired: 'Hết thời gian chiến dịch',
            partial: 'Hoàn thành một phần',
            failed: 'Đã dừng do lỗi',
            cancelled: 'Đã hủy'
        };
        return labels[status] || status || 'Chờ chạy';
    }

    function statusGroup(status) {
        if (status === 'running') return 'running';
        if (status === 'pending' || status === 'monitoring') return 'pending';
        if (status === 'done') return 'done';
        if (status === 'failed' || status === 'partial' || status === 'expired') return 'error';
        return 'other';
    }

    function setAvatarElement(element, account) {
        if (!element) return;
        var name = getAccountName(account);
        var avatar = getAccountAvatar(account);
        element.innerHTML = '';
        var fallback = document.createElement('span');
        fallback.className = 'nexus-lucide nexus-lucide-user';
        fallback.setAttribute('aria-hidden', 'true');
        element.appendChild(fallback);
        if (!avatar) return;
        var image = document.createElement('img');
        image.src = avatar;
        image.alt = name;
        image.loading = 'lazy';
        image.addEventListener('error', function () { image.remove(); });
        element.appendChild(image);
    }

    function miniAvatarHtml(name, avatarUrl) {
        var avatar = safeImageUrl(avatarUrl);
        return '<span class="group-copy-mini-avatar"><span>' + esc(initials(name)) + '</span>' +
            (avatar ? '<img src="' + esc(avatar) + '" alt="" loading="lazy">' : '') + '</span>';
    }

    // ─── Bộ chọn tài khoản ──────────────────────────────────────────────────
    function renderSelectedAccount() {
        var accountId = (($('groupCopyAccount') || {}).value || '').trim();
        var account = state.accounts.find(function (item) { return getAccountId(item) === accountId; });
        var nameEl = $('groupCopyAccountName');
        var stateEl = $('groupCopyAccountState');
        var topEl = $('groupCopyTopAccount');
        if (!account) {
            nameEl.textContent = state.accounts.length ? 'Chọn tài khoản' : 'Chưa có tài khoản';
            stateEl.textContent = state.accounts.length ? 'Nhấn để chọn' : 'Hãy thêm tài khoản trước';
            setAvatarElement($('groupCopyAccountAvatar'), { name: '?' });
            if (topEl) topEl.textContent = '-';
            updateRunButtonState();
            return;
        }
        nameEl.textContent = getAccountName(account);
        stateEl.textContent = accountReady(account) ? 'Sẵn sàng thực hiện' : 'Chưa đủ dữ liệu đăng nhập';
        setAvatarElement($('groupCopyAccountAvatar'), account);
        if (topEl) topEl.textContent = getAccountName(account);
        updateRunButtonState();
    }

    function renderAccountMenu() {
        var menu = $('groupCopyAccountMenu');
        menu.innerHTML = '';

        if (state.accounts.length > 8) {
            var search = document.createElement('input');
            search.type = 'text';
            search.className = 'group-copy-account-search';
            search.placeholder = 'Tìm tài khoản...';
            search.value = state.accountSearch;
            search.addEventListener('click', function (e) { e.stopPropagation(); });
            search.addEventListener('input', function () { state.accountSearch = search.value; renderAccountMenu(); });
            menu.appendChild(search);
            window.setTimeout(function () { search.focus(); }, 0);
        }

        var query = state.accountSearch.trim().toLowerCase();
        var filtered = query
            ? state.accounts.filter(function (a) { return getAccountName(a).toLowerCase().indexOf(query) !== -1; })
            : state.accounts;

        if (!state.accounts.length) {
            var empty = document.createElement('div');
            empty.className = 'group-copy-account-empty';
            empty.textContent = 'Chưa có tài khoản để chọn.';
            menu.appendChild(empty);
            return;
        }
        if (!filtered.length) {
            var emptySearch = document.createElement('div');
            emptySearch.className = 'group-copy-account-empty';
            emptySearch.textContent = 'Không tìm thấy tài khoản phù hợp.';
            menu.appendChild(emptySearch);
            return;
        }

        var selectedId = (($('groupCopyAccount') || {}).value || '').trim();
        filtered.forEach(function (account) {
            var id = getAccountId(account);
            var button = document.createElement('button');
            button.type = 'button';
            button.className = 'group-copy-account-option' + (id === selectedId ? ' active' : '');
            button.setAttribute('role', 'option');
            button.setAttribute('aria-selected', id === selectedId ? 'true' : 'false');

            var avatar = document.createElement('span');
            avatar.className = 'group-copy-account-avatar';
            setAvatarElement(avatar, account);

            var info = document.createElement('span');
            info.className = 'group-copy-account-option-info';
            var strong = document.createElement('strong');
            strong.textContent = getAccountName(account);
            var small = document.createElement('small');
            small.textContent = accountReady(account) ? 'Sẵn sàng' : 'Thiếu zpwEnk, zpw_sek hoặc IMEI';
            info.appendChild(strong);
            info.appendChild(small);

            var mark = document.createElement('span');
            mark.className = 'group-copy-account-option-mark';
            if (id === selectedId) {
                var checkIcon = document.createElement('span');
                checkIcon.className = 'nexus-lucide nexus-lucide-check';
                checkIcon.setAttribute('aria-hidden', 'true');
                mark.appendChild(checkIcon);
            }

            button.appendChild(avatar);
            button.appendChild(info);
            button.appendChild(mark);
            button.addEventListener('click', function () { selectAccount(id); });
            menu.appendChild(button);
        });
    }

    function setAccountMenu(open) {
        state.accountMenuOpen = !!open;
        if (!open) state.accountSearch = '';
        $('groupCopyAccountMenu').hidden = !state.accountMenuOpen;
        $('groupCopyAccountTrigger').setAttribute('aria-expanded', state.accountMenuOpen ? 'true' : 'false');
        $('groupCopyAccountPicker').classList.toggle('open', state.accountMenuOpen);
    }

    async function selectAccount(accountId) {
        var changed = (($('groupCopyAccount') || {}).value || '') !== accountId;
        $('groupCopyAccount').value = accountId || '';
        if (accountId) storageSet('nexus_group_copy_account', accountId);
        renderSelectedAccount();
        renderAccountMenu();
        setAccountMenu(false);
        if (changed) {
            // Đổi tài khoản: bỏ nhóm đích và tiến trình lấy link của tài khoản cũ.
            state.targetGroup = null;
            state.groupLinkPollCount = 0;
            clearTimeout(state.groupLinkPollTimer);
            state.groupLinkPollTimer = null;
            renderTargetPreview();
        }
        // Tài khoản chủ không được đồng thời là tài khoản phụ.
        if (state.extraAccountIds && state.extraAccountIds.has(accountId)) {
            state.extraAccountIds.delete(accountId);
        }
        renderExtraAccounts();
        await loadGroups();
    }

    function escapeText(s) {
        return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    // ─── Tài khoản thực hiện (chọn nhiều qua popup, chia việc không trùng) ───
    // Danh sách cuối = tài khoản chủ đứng đầu + các tài khoản đã tick.
    function execAccountList() {
        var masterId = (($('groupCopyAccount') || {}).value || '').trim();
        var ids = [];
        if (masterId) ids.push(masterId);
        state.extraAccountIds.forEach(function (id) { if (id && ids.indexOf(id) === -1) ids.push(id); });
        return ids;
    }

    function renderExtraAccounts() {
        if (!state.extraAccountIds) state.extraAccountIds = new Set();
        // Loại tài khoản không còn tồn tại.
        state.extraAccountIds.forEach(function (id) {
            if (!state.accounts.some(function (a) { return getAccountId(a) === id; })) state.extraAccountIds.delete(id);
        });
        var label = $('groupCopyExecLabel');
        var ids = execAccountList();
        if (label) label.textContent = ids.length > 1 ? ('Đang dùng ' + ids.length + ' tài khoản thực hiện') : 'Chọn tài khoản thực hiện';
    }

    function renderExecList() {
        var list = $('groupCopyExecList');
        if (!list) return;
        var masterId = (($('groupCopyAccount') || {}).value || '').trim();
        var q = String(($('groupCopyExecSearch') || {}).value || '').toLowerCase().trim();
        var accounts = state.accounts.filter(function (a) {
            if (!q) return true;
            return getAccountName(a).toLowerCase().indexOf(q) !== -1 || getAccountId(a).indexOf(q) !== -1;
        });
        if (!accounts.length) { list.innerHTML = '<div class="exec-picker-empty">Không có tài khoản phù hợp.</div>'; return; }
        list.innerHTML = accounts.map(function (a) {
            var id = getAccountId(a);
            var ready = accountReady(a);
            var isMaster = id === masterId;
            var checked = isMaster || state.extraAccountIds.has(id);
            var av = getAccountAvatar(a);
            return '<label class="exec-picker-item' + (checked ? ' checked' : '') + (ready ? '' : ' disabled') + '">'
                + '<input type="checkbox" ' + (checked ? 'checked' : '') + ((!ready || isMaster) ? ' disabled' : '') + ' data-exec-id="' + escapeText(id) + '">'
                + (av ? '<img src="' + escapeText(av) + '" class="exec-picker-avatar">' : '<span class="exec-picker-avatar placeholder"></span>')
                + '<span class="exec-picker-info"><b>' + escapeText(getAccountName(a)) + (isMaster ? ' (chủ)' : '') + '</b>'
                + '<small>' + escapeText(id) + (ready ? '' : ' · thiếu phiên') + '</small></span></label>';
        }).join('');
        list.querySelectorAll('input[data-exec-id]').forEach(function (cb) {
            cb.addEventListener('change', function () {
                var id = cb.getAttribute('data-exec-id');
                if (cb.checked) state.extraAccountIds.add(id); else state.extraAccountIds.delete(id);
                cb.closest('.exec-picker-item').classList.toggle('checked', cb.checked);
                updateExecCount();
            });
        });
        updateExecCount();
    }
    function updateExecCount() {
        var el = $('groupCopyExecCount');
        if (el) el.textContent = 'Đã chọn ' + execAccountList().length + ' tài khoản';
    }

    window.groupCopyOpenExecPicker = function () {
        var ov = $('groupCopyExecOverlay');
        if (ov) ov.classList.add('open');
        renderExecList();
    };
    window.groupCopyCloseExecPicker = function () {
        var ov = $('groupCopyExecOverlay');
        if (ov) ov.classList.remove('open');
        renderExtraAccounts();
    };
    window.groupCopyRenderExecList = renderExecList;
    window.groupCopyExecToggleAll = function (on) {
        var masterId = (($('groupCopyAccount') || {}).value || '').trim();
        state.accounts.forEach(function (a) {
            var id = getAccountId(a);
            if (id === masterId || !accountReady(a)) return;
            if (on) state.extraAccountIds.add(id); else state.extraAccountIds.delete(id);
        });
        renderExecList();
    };
    document.addEventListener('click', function (e) {
        var ov = $('groupCopyExecOverlay');
        if (ov && e.target === ov) ov.classList.remove('open');
    });

    // ─── Segmented: loại nhóm đích ──────────────────────────────────────────
    function setTargetMode(mode) {
        state.targetMode = mode === 'existing' ? 'existing' : 'new';
        document.querySelectorAll('[data-target-mode]').forEach(function (button) {
            button.classList.toggle('active', button.getAttribute('data-target-mode') === state.targetMode);
        });
        $('groupCopyNewTargetWrap').hidden = state.targetMode !== 'new';
        $('groupCopyExistingTargetWrap').hidden = state.targetMode !== 'existing';
        var startButton = $('groupCopyStartBtn');
        if (startButton) {
            startButton.innerHTML = state.targetMode === 'new'
                ? '<span>▶</span> Tạo nhóm và bắt đầu sao chép'
                : '<span>▶</span> Bắt đầu sao chép vào nhóm';
        }
        updateRunButtonState();
    }

    // ─── Tài khoản & nhóm cá nhân (dữ liệu thật, không hard-code) ─────────
    async function loadAccounts() {
        try {
            var response = await fetch('/api/accounts');
            var data = await response.json();
            if (!response.ok || data.error) throw new Error(data.error || 'Không tải được tài khoản.');
            state.accounts = data.accounts || [];
            var saved = storageGet('nexus_group_copy_account');
            var selected = state.accounts.some(function (item) { return getAccountId(item) === saved; })
                ? saved
                : (state.accounts[0] ? getAccountId(state.accounts[0]) : '');
            $('groupCopyAccount').value = selected;
            renderSelectedAccount();
            renderAccountMenu();
            renderExtraAccounts();
            await loadGroups();
        } catch (error) {
            state.accounts = [];
            $('groupCopyAccount').value = '';
            renderSelectedAccount();
            renderAccountMenu();
            setStatus(friendlyFetchError(error, 'Không thể tải danh sách tài khoản.'), 'error');
        }
    }

    var _groupsFetchToken = 0;
    async function loadGroups(options) {
        options = options || {};
        var accountId = (($('groupCopyAccount') || {}).value || '').trim();
        var token = ++_groupsFetchToken;
        if (!options.keepCurrent) state.groups = [];
        if (!accountId) return;
        try {
            var response = await fetch('/api/groups/personal?accountId=' + encodeURIComponent(accountId));
            var data = await response.json();
            if (token !== _groupsFetchToken) return;
            if (!response.ok || !data.success) throw new Error(data.error || 'Không tải được danh sách nhóm.');
            state.groups = data.groups || [];

            var pendingLinks = state.groups.some(function (g) {
                return !getGroupLink(g) && String(g.linkStatus || '').toLowerCase() !== 'error';
            });
            if (pendingLinks && state.groupLinkPollCount < 16) {
                state.groupLinkPollCount += 1;
                clearTimeout(state.groupLinkPollTimer);
                state.groupLinkPollTimer = setTimeout(function () {
                    var currentAccountId = (($('groupCopyAccount') || {}).value || '').trim();
                    if (currentAccountId === accountId) loadGroups({ keepCurrent: true });
                }, 2500);
            } else {
                state.groupLinkPollCount = 0;
                clearTimeout(state.groupLinkPollTimer);
                state.groupLinkPollTimer = null;
            }
        } catch (error) {
            if (token !== _groupsFetchToken) return;
            if (state.targetMode === 'existing') setStatus(friendlyFetchError(error, 'Không thể tải danh sách nhóm hiện tại.'), 'error');
        }
        if (state.pickerOpen) renderPickerList();
        if (state.targetGroup) {
            var selectedId = getGroupId(state.targetGroup);
            var refreshed = state.groups.find(function (g) { return getGroupId(g) === selectedId; });
            if (refreshed) { state.targetGroup = refreshed; renderTargetPreview(); }
        }
    }

    // ─── Preview nhóm nguồn (link/ID) — gọi /api/groups/info, huỷ request cũ ─
    function resetSourcePreview() {
        state.sourceGroupInfo = null;
        var box = $('groupCopySourcePreview');
        if (box) { box.hidden = true; box.innerHTML = ''; }
        updateRunButtonState();
    }

    function renderSourcePreviewSkeleton() {
        var box = $('groupCopySourcePreview');
        if (!box) return;
        box.hidden = false;
        box.innerHTML = '<div class="gc-gp-skeleton"><span class="gc-gp-skel-avatar"></span><span class="gc-gp-skel-line"></span></div>';
    }

    function renderSourcePreviewError(message) {
        var box = $('groupCopySourcePreview');
        if (!box) return;
        box.hidden = false;
        box.innerHTML = '<div class="gc-gp-error"><span>⚠</span>' +
            '<span class="gc-gp-error-text" title="' + esc(message) + '">Không thể tải thông tin nhóm. Vui lòng kiểm tra lại link nhóm, tài khoản hoặc kết nối.</span>' +
            '<button type="button" class="gc-gp-retry" id="groupCopySourceRetry">Thử lại</button></div>';
        var retry = $('groupCopySourceRetry');
        if (retry) retry.addEventListener('click', function () { scheduleSourcePreview(0); });
    }

    function renderSourcePreview(info) {
        var box = $('groupCopySourcePreview');
        if (!box) return;
        box.hidden = false;
        var count = Number(info.totalMember || 0);
        box.innerHTML = '<div class="gc-gp-row">' +
            '<span class="gc-gp-avatar">' + (info.avt ? '<img src="' + esc(safeImageUrl(info.avt)) + '" alt="">' : '') + '<span>' + esc(initials(info.name)) + '</span></span>' +
            '<span class="gc-gp-copy"><strong title="' + esc(info.name || '-') + '">' + esc(info.name || 'Nhóm ' + shortId(info.groupId)) + '</strong>' +
            '<small>' + (count ? count + ' thành viên · ' : '') + 'ID ' + esc(shortId(info.groupId)) + '</small></span>' +
            '<button type="button" class="gc-gp-reset" id="groupCopySourceChange">Đổi nhóm</button></div>';
        var change = $('groupCopySourceChange');
        if (change) change.addEventListener('click', function () { $('groupCopySource').focus(); $('groupCopySource').select(); });
    }

    function scheduleSourcePreview(delayMs) {
        clearTimeout(state.sourceDebounceTimer);
        if (state.sourcePreviewController) { try { state.sourcePreviewController.abort(); } catch (e) {} }
        var raw = (($('groupCopySource') || {}).value || '').trim();
        state.sourceGroupInfo = null;
        updateRunButtonState();
        if (!raw) { resetSourcePreview(); return; }
        state.sourceDebounceTimer = window.setTimeout(function () { fetchSourcePreview(raw); }, delayMs == null ? 550 : delayMs);
    }

    async function fetchSourcePreview(raw) {
        var accountId = (($('groupCopyAccount') || {}).value || '').trim();
        if (!accountId) { resetSourcePreview(); return; }
        var token = ++state.sourcePreviewToken;
        renderSourcePreviewSkeleton();
        var controller = (typeof AbortController !== 'undefined') ? new AbortController() : null;
        state.sourcePreviewController = controller;
        try {
            var response = await fetch('/api/groups/info', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ accountId: accountId, groupInput: raw }),
                signal: controller ? controller.signal : undefined
            });
            var data = await response.json();
            if (token !== state.sourcePreviewToken) return;
            if (!response.ok || !data.success) throw new Error(data.error || 'Không tải được thông tin nhóm.');
            state.sourceGroupInfo = data.groupInfo || {};
            renderSourcePreview(state.sourceGroupInfo);
        } catch (error) {
            if (token !== state.sourcePreviewToken) return;
            if (error && error.name === 'AbortError') return;
            state.sourceGroupInfo = null;
            renderSourcePreviewError(friendlyFetchError(error, 'Không đọc được nhóm nguồn.'));
        } finally {
            if (token === state.sourcePreviewToken) updateRunButtonState();
        }
    }

    // ─── Nhóm đích hiện tại: preview + modal chọn nhóm ─────────────────────
    function renderTargetPreview() {
        var el = $('groupCopyTargetPreview');
        if (!el) return;
        if (!state.targetGroup) {
            el.className = 'gc-target-preview-empty';
            el.textContent = '+ Chọn nhóm hiện tại';
        } else {
            var g = state.targetGroup;
            el.className = 'gc-target-preview';
            var count = getGroupMemberCount(g);
            var linkLabel = getGroupLinkLabel(g);
            el.innerHTML = '<span class="gc-gp-avatar">' + (g.avatar ? '<img src="' + esc(safeImageUrl(g.avatar)) + '" alt="">' : '') + '<span>' + esc(initials(getGroupName(g))) + '</span></span>' +
                '<span class="gc-gp-copy"><strong>' + esc(getGroupName(g)) + '</strong><small title="' + esc(linkLabel) + '">' + (count ? count + ' thành viên · ' : '') + esc(linkLabel) + '</small></span>';
        }
        $('groupCopyExistingGroup').value = state.targetGroup ? getGroupId(state.targetGroup) : '';
        updateRunButtonState();
    }

    function openGroupPicker() {
        var accountId = (($('groupCopyAccount') || {}).value || '').trim();
        if (!accountId) { showToast('Vui lòng chọn tài khoản thực hiện trước.', 'warning'); return; }
        state.pickerOpen = true;
        state.pickerSearch = '';
        $('groupCopyPickerSearch').value = '';
        $('groupCopyPickerOverlay').classList.add('show');
        var account = state.accounts.find(function (a) { return getAccountId(a) === accountId; });
        $('groupCopyPickerSub').textContent = 'Nhóm đang tham gia của ' + getAccountName(account || {});
        renderPickerList();
        if (!state.groups.length) loadGroups();
    }

    function closeGroupPicker() {
        state.pickerOpen = false;
        $('groupCopyPickerOverlay').classList.remove('show');
    }

    function renderPickerList() {
        var list = $('groupCopyPickerList');
        if (!list) return;
        var accountId = (($('groupCopyAccount') || {}).value || '').trim();
        if (!accountId) { list.innerHTML = '<div class="gc-picker-empty">Vui lòng chọn tài khoản thực hiện.</div>'; return; }

        var query = state.pickerSearch.trim().toLowerCase();
        var groups = state.groups.filter(function (g) {
            if (!query) return true;
            return getGroupName(g).toLowerCase().indexOf(query) !== -1
                || getGroupId(g).toLowerCase().indexOf(query) !== -1
                || getGroupLink(g).toLowerCase().indexOf(query) !== -1;
        });
        groups = groups.slice().sort(function (a, b) {
            if (state.pickerSort === 'members') return getGroupMemberCount(b) - getGroupMemberCount(a);
            return getGroupName(a).localeCompare(getGroupName(b), 'vi');
        });

        if (!groups.length) {
            list.innerHTML = state.groups.length
                ? '<div class="gc-picker-empty">Chưa tìm thấy nhóm phù hợp với tài khoản này.</div>'
                : '<div class="gc-picker-loading"><span class="spinner"></span><p>Đang tải danh sách nhóm...</p></div>';
            return;
        }

        var selectedId = state.targetGroup ? getGroupId(state.targetGroup) : '';
        list.innerHTML = groups.map(function (g) {
            var gid = getGroupId(g);
            var count = getGroupMemberCount(g);
            var selected = gid === selectedId;
            var linkLabel = getGroupLinkLabel(g);
            return '<button type="button" class="gc-picker-row' + (selected ? ' selected' : '') + '" data-pick-group="' + esc(gid) + '">' +
                '<span class="gc-gp-avatar">' + (g.avatar ? '<img src="' + esc(safeImageUrl(g.avatar)) + '" alt="">' : '') + '<span>' + esc(initials(getGroupName(g))) + '</span></span>' +
                '<span class="gc-picker-row-name"><strong>' + esc(getGroupName(g)) + '</strong><small title="' + esc(linkLabel) + '">' + esc(linkLabel) + '</small></span>' +
                '<span class="gc-picker-row-members">' + (count ? count + ' thành viên' : '-') + '</span>' +
                '<span class="gc-picker-row-mark">' + (selected ? '✓' : '') + '</span>' +
            '</button>';
        }).join('');
    }

    function pickGroup(groupId) {
        var group = state.groups.find(function (g) { return getGroupId(g) === groupId; });
        if (!group) return;
        state.targetGroup = group;
        renderTargetPreview();
        closeGroupPicker();
        showToast('Đã chọn nhóm đích: ' + getGroupName(group), 'success');
    }

    async function refreshPickerGroups() {
        var accountId = (($('groupCopyAccount') || {}).value || '').trim();
        if (!accountId) return;
        var btn = $('groupCopyPickerRefresh');
        var orig = btn ? btn.innerHTML : '';
        if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner" style="width:12px;height:12px;"></span> Đang làm mới...'; }
        $('groupCopyPickerList').innerHTML = '<div class="gc-picker-loading"><span class="spinner"></span><p>Đang mở tài khoản và đồng bộ danh sách nhóm...</p></div>';
        try {
            var response = await fetch('/api/groups/refresh-account', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ accountId: accountId })
            });
            var data = await response.json();
            if (!response.ok || !data.success) throw new Error(data.error || 'Không làm mới được danh sách nhóm.');
            showToast('Đang đồng bộ lại danh sách nhóm, vui lòng chờ trong giây lát...', 'info');
            window.setTimeout(loadGroups, 4000);
        } catch (error) {
            $('groupCopyPickerList').innerHTML = '<div class="gc-picker-error">' + esc(friendlyFetchError(error, 'Không thể làm mới danh sách nhóm.')) + '<br><button type="button" class="btn btn-ghost btn-sm" id="groupCopyPickerRetry">Thử lại</button></div>';
            var retry = $('groupCopyPickerRetry');
            if (retry) retry.addEventListener('click', refreshPickerGroups);
        } finally {
            if (btn) { btn.disabled = false; btn.innerHTML = orig; }
        }
    }

    // ─── Xác nhận trường còn thiếu + bật/tắt nút chạy theo thời gian thực ─
    function buildPayload() {
        var accountId = (($('groupCopyAccount') || {}).value || '').trim();
        var sourceInput = (($('groupCopySource') || {}).value || '').trim();
        var startAt = (($('groupCopyStartAt') || {}).value || '').trim();
        var limit = parseInt((($('groupCopyDailyLimit') || {}).value || '20'), 10);
        var campaignDays = parseInt((($('groupCopyCampaignDays') || {}).value || '30'), 10);
        var verifyMinutes = parseInt((($('groupCopyVerifyMinutes') || {}).value || '30'), 10);
        var payload = {
            accountId: accountId,
            sourceInput: sourceInput,
            // Khi preview đã xác minh được nhóm, gửi thêm ID đầy đủ cho backend.
            // Backend vẫn giữ sourceInput gốc để link có thể tự join khi cần.
            sourceGroupId: state.sourceGroupInfo ? getGroupId(state.sourceGroupInfo) : '',
            targetMode: state.targetMode,
            startAt: startAt,
            dailyLimit: limit,
            campaignDurationDays: campaignDays,
            verifyIntervalMinutes: verifyMinutes,
            removeFriendAfterJoin: !!(($('groupCopyRemoveFriend') || {}).checked),
            leaveGroupAfterDone: !!(($('groupCopyLeaveAfterDone') || {}).checked),
            skipLeaders: !!(($('groupCopySkipLeaders') || {}).checked),
            accountIds: [accountId].concat(Array.from(state.extraAccountIds || [])).filter(function (v, i, arr) { return v && arr.indexOf(v) === i; }),
            consentConfirmed: !!(($('groupCopyConsent') || {}).checked)
        };

        if (state.targetMode === 'new') {
            payload.newGroupName = (($('groupCopyNewGroupName') || {}).value || '').trim();
            payload.targetGroupName = payload.newGroupName;
            payload.title = payload.newGroupName ? ('Sao chép vào ' + payload.newGroupName) : 'Sao chép thành viên nhóm';
        } else {
            payload.targetGroupId = state.targetGroup ? getGroupId(state.targetGroup) : '';
            payload.targetGroupLink = state.targetGroup ? getGroupLink(state.targetGroup) : '';
            payload.targetGroupName = state.targetGroup ? getGroupName(state.targetGroup) : '';
            payload.title = payload.targetGroupName ? ('Sao chép vào ' + payload.targetGroupName) : 'Sao chép thành viên nhóm';
        }
        return payload;
    }

    function validatePayload(payload) {
        if (!payload.accountId) return 'Vui lòng chọn tài khoản thực hiện.';
        var account = state.accounts.find(function (item) { return getAccountId(item) === payload.accountId; });
        if (account && !accountReady(account)) return 'Tài khoản chưa sẵn sàng: cần zpwEnk, zpw_sek và IMEI cùng phiên.';
        if (!payload.sourceInput) return 'Vui lòng dán link hoặc ID nhóm nguồn.';
        if (payload.targetMode === 'new' && !payload.newGroupName) return 'Vui lòng nhập tên nhóm mới.';
        if (payload.targetMode === 'existing' && !payload.targetGroupId) return 'Vui lòng chọn một nhóm hiện tại.';
        if (!payload.startAt) return 'Vui lòng chọn thời gian bắt đầu.';
        if (!payload.dailyLimit || payload.dailyLimit < 1 || payload.dailyLimit > 30) return 'Số lời mời kết bạn mỗi ngày phải từ 1 đến 30.';
        if (!payload.campaignDurationDays || payload.campaignDurationDays < 1 || payload.campaignDurationDays > 365) return 'Thời gian chạy chiến dịch phải từ 1 đến 365 ngày.';
        if (!payload.verifyIntervalMinutes || payload.verifyIntervalMinutes < 1 || payload.verifyIntervalMinutes > 1440) return 'Chu kỳ thử add lại phải từ 1 đến 1440 phút.';
        if (!payload.consentConfirmed) return 'Vui lòng xác nhận quyền quản lý nhóm và gửi lời mời.';
        return '';
    }

    function updateRunButtonState() {
        var btn = $('groupCopyStartBtn');
        if (!btn || state.pollingTask) return;
        var error = validatePayload(buildPayload());
        btn.disabled = !!error;
        btn.title = error || '';
    }

    // ─── Tạo tác vụ ─────────────────────────────────────────────────────────
    async function pollTask(taskId) {
        state.pollingTask = true;
        var attempts = 0;
        while (state.pollingTask && attempts < 180) {
            attempts += 1;
            await new Promise(function (resolve) { setTimeout(resolve, 1500); });
            var response = await fetch('/api/tasks/' + encodeURIComponent(taskId));
            var data = await response.json();
            if (!response.ok || data.error) throw new Error(data.error || 'Không đọc được tiến độ tác vụ.');
            var task = data.task || {};
            var latestLog = (task.logs || []).slice(-1)[0];
            var message = latestLog && latestLog.message ? latestLog.message : 'Đang xử lý nhóm nguồn...';
            setStatus(message + ' (' + Number(task.progress || 0) + '%)', 'loading');
            if (task.status === 'completed') return task.result || {};
            if (task.status === 'failed') throw new Error(task.error || 'Lập lịch thất bại.');
            if (task.status === 'cancelled') throw new Error('Tác vụ đã bị hủy.');
        }
        throw new Error('Quá thời gian chờ lập lịch. Vui lòng kiểm tra lại danh sách tác vụ.');
    }

    function setStartButtonLoading(loading) {
        var button = $('groupCopyStartBtn');
        if (!button) return;
        button.disabled = !!loading;
        if (loading) {
            button.innerHTML = '<span class="spinner" style="width:14px;height:14px;border-width:2px;"></span> Đang đọc nhóm...';
        } else {
            updateRunButtonState();
        }
    }

    async function startJob() {
        var payload = buildPayload();
        var error = validatePayload(payload);
        if (error) {
            setStatus(error, 'error');
            showToast(error, 'error');
            return;
        }
        setStartButtonLoading(true);
        setStatus('Đang đọc thành viên nhóm nguồn...', 'loading');
        try {
            var response = await fetch('/api/group-copy/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            var data = await response.json();
            if (!response.ok || !data.success) throw new Error(data.error || 'Không bắt đầu được tác vụ.');
            var result = await pollTask(data.taskId);
            var total = Number(result.totalMembers || ((result.job || {}).totalMembers) || 0);
            var jobCount = Number(result.jobCount || (result.jobs ? result.jobs.length : 1) || 1);
            var accountCount = Number(result.accountCount || 1);
            var verifyMinutes = Number(result.verifyIntervalMinutes || ((result.job || {}).verifyIntervalMinutes) || 30);
            var multiNote = accountCount > 1
                ? (' Chia cho ' + accountCount + ' tài khoản (mỗi tài khoản một khối liên tiếp, không trùng) — tạo ' + jobCount + ' tác vụ.')
                : '';
            var prefix = payload.targetMode === 'new'
                ? 'Đã tạo tác vụ. Nhóm mới sẽ được tạo, kích hoạt link và xử lý ngay ở đợt đầu.'
                : 'Đã tạo tác vụ cho nhóm hiện tại và sẽ lấy link nhóm trước khi xử lý.';
            setStatus(prefix + multiNote + ' Tổng ' + total + ' thành viên; mỗi tài khoản gửi tối đa ' + payload.dailyLimit + ' lời mời kết bạn/ngày và thử add lại mỗi ' + verifyMinutes + ' phút. Chiến dịch dừng khi đủ thành viên hoặc hết ' + payload.campaignDurationDays + ' ngày.' + (payload.removeFriendAfterJoin ? ' Đã bật xóa kết bạn với người do chiến dịch vừa kết bạn.' : ''), 'success');
            showToast('Đã tạo ' + jobCount + ' tác vụ sao chép nhóm — ' + total + ' thành viên.', 'success');
            state.highlightJobId = String((((result.jobs || [])[0] || result.job || {}).jobId) || '');
            $('groupCopyConsent').checked = false;
            resetSourcePreview();
            $('groupCopySource').value = '';
            state.targetGroup = null;
            renderTargetPreview();
            await loadJobs();
            closeCreateOverlay();
        } catch (err) {
            var msg = friendlyFetchError(err, 'Không tạo được tác vụ sao chép nhóm.');
            setStatus(msg, 'error');
            showToast(msg, 'error');
        } finally {
            state.pollingTask = false;
            setStartButtonLoading(false);
        }
    }

    // ─── Danh sách tác vụ ───────────────────────────────────────────────────
    async function loadJobs() {
        var container = $('groupCopyJobs');
        if (!container) return;
        if (!container.children.length) {
            container.innerHTML = '<div class="gc-job-skeleton"></div><div class="gc-job-skeleton"></div><div class="gc-job-skeleton"></div>';
        }
        try {
            var response = await fetch('/api/group-copy/jobs');
            var data = await response.json();
            if (!response.ok || !data.success) throw new Error(data.error || 'Không tải được tác vụ.');
            renderJobs(data.jobs || []);
        } catch (error) {
            renderSummary([]);
            $('groupCopyJobsCount').textContent = 'Không tải được';
            container.innerHTML = '<div class="group-copy-empty">' + esc(friendlyFetchError(error, 'Không thể tải danh sách tác vụ.')) + '<br><button type="button" class="btn btn-ghost btn-sm" style="margin-top:10px;" id="groupCopyJobsRetry">Thử lại</button></div>';
            var retry = $('groupCopyJobsRetry');
            if (retry) retry.addEventListener('click', loadJobs);
        }
    }

    function renderSummary(jobs) {
        var list = Array.isArray(jobs) ? jobs : [];
        var active = 0;
        var joined = 0;
        var total = 0;
        list.forEach(function (job) {
            var status = String((job && job.status) || 'pending');
            if (status === 'pending' || status === 'running' || status === 'monitoring') active += 1;
            joined += Number((job && (job.joinedCount || job.successCount)) || 0);
            total += Number((job && job.totalMembers) || 0);
        });
        var conversion = total > 0 ? Math.round((joined * 1000) / total) / 10 : 0;
        if ($('groupCopyStatTotal')) $('groupCopyStatTotal').textContent = list.length;
        if ($('groupCopyStatActive')) $('groupCopyStatActive').textContent = active;
        if ($('groupCopyStatMembers')) $('groupCopyStatMembers').textContent = joined;
        if ($('groupCopyStatWaiting')) $('groupCopyStatWaiting').textContent = conversion + '%';
    }

    function jobMatchesFilter(job) {
        var group = statusGroup(job.status || 'pending');
        if (state.jobFilter !== 'all' && group !== state.jobFilter) return false;
        var query = state.jobSearch.trim().toLowerCase();
        if (!query) return true;
        var source = job.sourceGroup || {};
        var haystack = [job.title, source.name, job.targetGroupName, job.newGroupName, job.accountName].join(' ').toLowerCase();
        return haystack.indexOf(query) !== -1;
    }

    // Checkbox chọn tác vụ (chỉ hiện khi bật chế độ chọn nhiều). `ids` là danh sách
    // jobId mà thẻ này đại diện (thẻ đơn = 1 job; thẻ chiến dịch = nhiều job).
    function selectCheckboxHtml(ids) {
        if (!state.selectMode) return '';
        var list = (ids || []).filter(Boolean);
        var allChecked = list.length > 0 && list.every(function (id) { return state.selected[id]; });
        return '<label class="gc-select-box" title="Chọn tác vụ" onclick="event.stopPropagation()">' +
            '<input type="checkbox" data-select-ids="' + esc(list.join(',')) + '"' + (allChecked ? ' checked' : '') + '></label>';
    }

    function buildJobCard(job) {
        var source = job.sourceGroup || {};
        var sourceName = source.name || job.sourceInput || '-';
        var targetName = job.targetGroupName || (job.targetMode === 'new' ? job.newGroupName : '') || 'Chưa xác định';
        var total = Number(job.totalMembers || 0);
        var joined = Number(job.joinedCount || job.successCount || 0);
        var directAdded = Number(job.preExistingCount || 0);
        var invited = Number(job.invitedCount || 0);
        var pending = Number(job.pendingCount || job.remainingCount || 0);
        var failed = Number(job.failedCount || 0);
        var percent = Math.max(0, Math.min(100, Number(job.conversionRate || job.progressPercent || 0)));
        var status = job.status || 'pending';
        var jobId = String(job.jobId || '');
        var nextRun = (status === 'pending' || status === 'monitoring') ? formatDateTime(job.nextRunAt) : '-';
        var lastVerified = formatDateTime(job.lastVerifiedAt);
        var problemNote = job.lastError || job.lastVerificationError || '';

        var kebabItems = '';
        if (job.targetGroupId && (status === 'pending' || status === 'monitoring' || status === 'failed')) kebabItems += '<button type="button" data-job-verify="' + esc(jobId) + '">Kiểm tra ngay</button>';
        if (status === 'pending' || status === 'monitoring') kebabItems += '<button type="button" data-job-cancel="' + esc(jobId) + '">Tạm dừng tác vụ</button>';
        if ((status === 'failed' || status === 'cancelled') && Number(job.pendingCount || 0) > 0) kebabItems += '<button type="button" data-job-resume="' + esc(jobId) + '">Tiếp tục</button>';
        if (status !== 'running') kebabItems += '<button type="button" class="is-danger" data-job-delete="' + esc(jobId) + '">Xóa tác vụ</button>';

        var quickActions = '<button type="button" class="gc-btn-sm is-primary" data-job-detail="' + esc(jobId) + '">' + ICON_DETAIL + ' Chi tiết</button>';
        if (job.targetGroupId && (status === 'pending' || status === 'monitoring' || status === 'failed')) {
            quickActions += '<button type="button" class="gc-btn-sm" data-job-verify="' + esc(jobId) + '">' + ICON_VERIFY + ' Kiểm tra ngay</button>';
        }
        if (status === 'pending' || status === 'monitoring') {
            quickActions += '<button type="button" class="gc-btn-sm is-warning" data-job-cancel="' + esc(jobId) + '">' + ICON_PAUSE + ' Tạm dừng</button>';
        } else if ((status === 'failed' || status === 'cancelled') && Number(job.pendingCount || 0) > 0) {
            quickActions += '<button type="button" class="gc-btn-sm is-success" data-job-resume="' + esc(jobId) + '">' + ICON_RESUME + ' Tiếp tục</button>';
        }

        var card = document.createElement('div');
        card.className = 'gc-job-card' + (jobId === state.highlightJobId ? ' gc-job-highlight' : '') + (state.selectMode ? ' gc-selecting' : '') + (state.selected[jobId] ? ' gc-selected' : '');
        card.setAttribute('data-job-card', jobId);
        var jobTitle = String(job.title || ('Sao chép vào ' + targetName));
        var accountNameText = String(job.accountName || job.accountId || 'Tài khoản');
        card.innerHTML =
            '<div class="gc-job-head">' +
                selectCheckboxHtml([jobId]) +
                '<div class="gc-job-heading">' +
                    '<div class="gc-job-title-row"><span class="gc-job-title-icon">' + miniAvatarHtml(targetName, job.targetGroupAvatar) + '</span><div><strong class="gc-job-title" title="' + esc(jobTitle) + '">' + esc(jobTitle) + '</strong><small>Kế hoạch chuyển thành viên</small></div></div>' +
                    '<div class="gc-job-route">' +
                        '<span class="gc-job-route-node">' + miniAvatarHtml(sourceName, source.avt || source.avatar) +
                            '<span class="gc-job-route-copy"><small>Nhóm nguồn</small><strong title="' + esc(sourceName) + '">' + esc(sourceName) + '</strong></span></span>' +
                        '<span class="gc-job-route-arrow">→</span>' +
                        '<span class="gc-job-route-node">' + miniAvatarHtml(targetName, job.targetGroupAvatar) +
                            '<span class="gc-job-route-copy"><small>Nhóm đích</small><strong title="' + esc(targetName) + '">' + esc(targetName) + '</strong></span></span>' +
                    '</div>' +
                '</div>' +
                '<div class="gc-job-owner"><span>Người thực hiện</span><strong>' + esc(accountNameText) + '</strong></div>' +
                '<div class="gc-job-head-meta">' +
                    '<span class="group-copy-job-badge ' + esc(status) + '">' + esc(statusLabel(status)) + '</span>' +
                    (problemNote ? '<span class="gc-warn-chip" title="' + esc(problemNote) + '">⚠</span>' : '') +
                    '<div class="gc-kebab-wrap"><button type="button" class="gc-kebab-btn" data-kebab-toggle="' + esc(jobId) + '" aria-label="Thao tác khác">⋮</button>' +
                        '<div class="gc-kebab-menu" data-kebab-menu="' + esc(jobId) + '" hidden>' + (kebabItems || '<button type="button" disabled>Không có thao tác</button>') + '</div></div>' +
                '</div>' +
            '</div>' +
            '<div class="gc-job-progress-row"><div class="group-copy-progress"><span style="width:' + percent + '%"></span></div><span class="gc-job-progress-pct">' + percent + '%</span></div>' +
            '<div class="gc-job-stats">' +
                '<div class="gc-job-stat"><span>Tổng nguồn</span><strong>' + total + '</strong></div>' +
                '<div class="gc-job-stat is-success"><span>Thêm trực tiếp</span><strong>' + directAdded + '</strong></div>' +
                '<div class="gc-job-stat"><span>Đã mời kết bạn</span><strong>' + invited + '</strong></div>' +
                '<div class="gc-job-stat is-success"><span>Đã vào nhóm</span><strong>' + joined + '</strong></div>' +
                '<div class="gc-job-stat"><span>Đang chờ</span><strong>' + pending + '</strong></div>' +
                '<div class="gc-job-stat' + (failed ? ' is-danger' : '') + '"><span>Lỗi</span><strong>' + failed + '</strong></div>' +
            '</div>' +
            '<div class="gc-job-foot">' +
                '<div class="gc-job-timing"><span>Kiểm tra gần nhất <b>' + esc(lastVerified) + '</b></span><span>Chạy tiếp theo <b>' + esc(nextRun) + '</b></span></div>' +
                '<div class="gc-job-actions">' + quickActions + '</div>' +
            '</div>';
        return card;
    }

    // ─── Gom job cùng 1 chiến dịch (nhiều tài khoản) thành 1 nhóm ───────────────
    function buildJobGroups(jobs) {
        var groups = [], byId = {};
        (jobs || []).forEach(function (job) {
            var cid = String((job && job.campaignId) || '').trim();
            if (!cid) { groups.push({ campaignId: '', jobs: [job] }); return; }
            if (byId[cid]) byId[cid].jobs.push(job);
            else { var g = { campaignId: cid, jobs: [job] }; byId[cid] = g; groups.push(g); }
        });
        return groups;
    }

    function campaignTitleOf(job) {
        var t = String((job && job.title) || '');
        // Bỏ hậu tố "(TK 1/5 - Tên)" để lấy tên chiến dịch chung.
        return t.replace(/\s*\(TK\s*\d+\s*\/\s*\d+[^)]*\)\s*$/, '').trim() ||
            ('Sao chép vào ' + String((job && job.targetGroupName) || 'nhóm đích'));
    }

    function campaignStatus(jobs) {
        var s = jobs.map(function (j) { return String((j && j.status) || 'pending'); });
        if (s.indexOf('running') >= 0) return 'running';
        if (s.some(function (x) { return x === 'pending' || x === 'monitoring'; })) return 'pending';
        if (s.some(function (x) { return x === 'failed'; })) return 'failed';
        if (s.length && s.every(function (x) { return x === 'done'; })) return 'done';
        return s[0] || 'pending';
    }

    // Thẻ TỔNG cho chiến dịch nhiều tài khoản: xem chi tiết mới bung từng thành viên.
    function buildCampaignCard(jobs) {
        var first = jobs[0] || {};
        var source = first.sourceGroup || {};
        var sourceName = source.name || first.sourceInput || '-';
        var targetName = first.targetGroupName || (first.targetMode === 'new' ? first.newGroupName : '') || 'Chưa xác định';
        var title = campaignTitleOf(first);
        var status = campaignStatus(jobs);
        var sum = function (key, alt) {
            return jobs.reduce(function (a, j) { return a + Number((j && (j[key] || (alt ? j[alt] : 0))) || 0); }, 0);
        };
        var total = sum('totalMembers');
        var joined = sum('joinedCount', 'successCount');
        var directAdded = sum('preExistingCount');
        var invited = sum('invitedCount');
        var pending = sum('pendingCount', 'remainingCount');
        var failed = sum('failedCount');
        var percent = total ? Math.max(0, Math.min(100, Math.round(joined * 100 / total))) : 0;
        var ids = jobs.map(function (j) { return String(j.jobId || ''); }).filter(Boolean).join(',');

        var canPause = jobs.some(function (j) { return j.status === 'pending' || j.status === 'monitoring'; });
        var canResume = jobs.some(function (j) { return (j.status === 'failed' || j.status === 'cancelled') && Number(j.pendingCount || 0) > 0; });
        var canDelete = jobs.every(function (j) { return j.status !== 'running'; });

        var actions = '<button type="button" class="gc-btn-sm is-primary" data-campaign-toggle="1">' + ICON_DETAIL + ' Xem chi tiết (' + jobs.length + ' tài khoản)</button>';
        if (canPause) actions += '<button type="button" class="gc-btn-sm is-warning" data-campaign-cancel="' + esc(ids) + '">' + ICON_PAUSE + ' Tạm dừng cả chiến dịch</button>';
        if (canResume) actions += '<button type="button" class="gc-btn-sm is-success" data-campaign-resume="' + esc(ids) + '">' + ICON_RESUME + ' Tiếp tục cả chiến dịch</button>';
        if (canDelete) actions += '<button type="button" class="gc-btn-sm is-danger" data-campaign-delete="' + esc(ids) + '">Xóa cả chiến dịch</button>';

        var campJobIds = jobs.map(function (j) { return String(j.jobId || ''); }).filter(Boolean);
        var card = document.createElement('div');
        var campSelected = campJobIds.length > 0 && campJobIds.every(function (id) { return state.selected[id]; });
        card.className = 'gc-job-card gc-campaign-card' + (state.selectMode ? ' gc-selecting' : '') + (campSelected ? ' gc-selected' : '');
        card.setAttribute('data-campaign-card', first.campaignId || '');
        card.innerHTML =
            '<div class="gc-job-head">' +
                selectCheckboxHtml(campJobIds) +
                '<div class="gc-job-heading">' +
                    '<div class="gc-job-title-row"><span class="gc-job-title-icon">' + miniAvatarHtml(targetName, first.targetGroupAvatar) + '</span><div><strong class="gc-job-title" title="' + esc(title) + '">' + esc(title) + '</strong><small>Chiến dịch • ' + jobs.length + ' tài khoản</small></div></div>' +
                    '<div class="gc-job-route">' +
                        '<span class="gc-job-route-node">' + miniAvatarHtml(sourceName, source.avt || source.avatar) +
                            '<span class="gc-job-route-copy"><small>Nhóm nguồn</small><strong title="' + esc(sourceName) + '">' + esc(sourceName) + '</strong></span></span>' +
                        '<span class="gc-job-route-arrow">→</span>' +
                        '<span class="gc-job-route-node">' + miniAvatarHtml(targetName, first.targetGroupAvatar) +
                            '<span class="gc-job-route-copy"><small>Nhóm đích</small><strong title="' + esc(targetName) + '">' + esc(targetName) + '</strong></span></span>' +
                    '</div>' +
                '</div>' +
                '<div class="gc-job-owner"><span>Tài khoản tham gia</span><strong>' + jobs.length + ' tài khoản</strong></div>' +
                '<div class="gc-job-head-meta">' +
                    '<span class="group-copy-job-badge ' + esc(status) + '">' + esc(statusLabel(status)) + '</span>' +
                '</div>' +
            '</div>' +
            '<div class="gc-job-progress-row"><div class="group-copy-progress"><span style="width:' + percent + '%"></span></div><span class="gc-job-progress-pct">' + percent + '%</span></div>' +
            '<div class="gc-job-stats">' +
                '<div class="gc-job-stat"><span>Tổng nguồn</span><strong>' + total + '</strong></div>' +
                '<div class="gc-job-stat is-success"><span>Thêm trực tiếp</span><strong>' + directAdded + '</strong></div>' +
                '<div class="gc-job-stat"><span>Đã mời kết bạn</span><strong>' + invited + '</strong></div>' +
                '<div class="gc-job-stat is-success"><span>Đã vào nhóm</span><strong>' + joined + '</strong></div>' +
                '<div class="gc-job-stat"><span>Đang chờ</span><strong>' + pending + '</strong></div>' +
                '<div class="gc-job-stat' + (failed ? ' is-danger' : '') + '"><span>Lỗi</span><strong>' + failed + '</strong></div>' +
            '</div>' +
            '<div class="gc-job-foot">' +
                '<div class="gc-job-timing"><span>Gộp tiến độ của ' + jobs.length + ' tài khoản</span></div>' +
                '<div class="gc-job-actions">' + actions + '</div>' +
            '</div>';
        // Chi tiết từng tài khoản hiển thị trong POPUP (không bung inline gây nhảy layout).
        return card;
    }

    // Popup chi tiết chiến dịch: liệt kê từng tài khoản (thẻ job) trong 1 modal.
    function openCampaignModal(campaignId) {
        var cid = String(campaignId || '');
        var jobs = (state.jobs || []).filter(function (j) { return String((j && j.campaignId) || '') === cid; });
        if (!jobs.length) { closeCampaignModal(); return; }
        jobs.sort(function (a, b) { return Number(a.multiAccountIndex || 0) - Number(b.multiAccountIndex || 0); });
        var body = $('groupCopyCampaignBody');
        body.innerHTML = '';
        jobs.forEach(function (j) { body.appendChild(buildJobCard(j)); });
        var first = jobs[0] || {};
        $('groupCopyCampaignTitle').textContent = campaignTitleOf(first);
        $('groupCopyCampaignSub').textContent = jobs.length + ' tài khoản trong chiến dịch';
        $('groupCopyCampaignOverlay').classList.add('show');
        state.campaignModalId = cid;
    }
    function closeCampaignModal() {
        var ov = $('groupCopyCampaignOverlay');
        if (ov) ov.classList.remove('show');
        state.campaignModalId = '';
    }

    async function campaignAction(idsCsv, action) {
        var ids = String(idsCsv || '').split(',').map(function (s) { return s.trim(); }).filter(Boolean);
        for (var i = 0; i < ids.length; i++) {
            try { await jobAction(ids[i], action); } catch (e) { /* tiếp tục các job còn lại */ }
        }
    }

    // ─── Chọn nhiều tác vụ + xóa hàng loạt ─────────────────────────────────
    function allVisibleJobIds() {
        return (state.jobs || []).filter(jobMatchesFilter)
            .map(function (j) { return String(j.jobId || ''); }).filter(Boolean);
    }
    function setSelected(ids, checked) {
        (ids || []).forEach(function (id) {
            if (!id) return;
            if (checked) state.selected[id] = true; else delete state.selected[id];
        });
    }
    // Đếm theo TÁC VỤ (chiến dịch nhiều tài khoản = 1 tác vụ), không đếm job con.
    function selectedTaskCount() {
        var tasks = 0;
        buildJobGroups(state.jobs || []).forEach(function (g) {
            var ids = g.jobs.map(function (j) { return String(j.jobId || ''); }).filter(Boolean);
            if (ids.length && ids.some(function (id) { return state.selected[id]; })) tasks++;
        });
        return tasks;
    }
    function updateBulkBar() {
        var hasAny = Object.keys(state.selected).some(function (id) { return state.selected[id]; });
        var tasks = selectedTaskCount();
        var countEl = $('groupCopyBulkCount');
        if (countEl) countEl.textContent = 'Đã chọn ' + tasks + ' tác vụ';
        var delBtn = $('groupCopyBulkDelete');
        if (delBtn) delBtn.disabled = !hasAny;
        var allBox = $('groupCopyBulkAll');
        if (allBox) {
            var visible = allVisibleJobIds();
            allBox.checked = visible.length > 0 && visible.every(function (id) { return state.selected[id]; });
        }
    }
    function toggleSelectMode(on) {
        state.selectMode = (on === undefined) ? !state.selectMode : !!on;
        if (!state.selectMode) state.selected = {};
        var btn = $('groupCopySelectToggle');
        if (btn) btn.classList.toggle('is-active', state.selectMode);
        var bar = $('groupCopyBulkBar');
        if (bar) bar.hidden = !state.selectMode;
        renderJobs(state.jobs);
    }
    function clearSelection() {
        state.selected = {};
        renderJobs(state.jobs);
    }
    function selectAllVisible(checked) {
        setSelected(allVisibleJobIds(), checked);
        renderJobs(state.jobs);
    }
    async function deleteSelected() {
        var ids = Object.keys(state.selected).filter(function (id) { return state.selected[id]; });
        if (!ids.length) return;
        var runningById = {};
        (state.jobs || []).forEach(function (j) { runningById[String(j.jobId || '')] = (j.status === 'running'); });
        var deletable = ids.filter(function (id) { return !runningById[id]; });
        var skipped = ids.length - deletable.length;
        if (!deletable.length) { showToast('Các tác vụ đang chạy không thể xóa. Hãy tạm dừng trước.', 'error'); return; }
        var taskN = selectedTaskCount();
        var msg = 'Xóa ' + taskN + ' tác vụ đã chọn và toàn bộ lịch sử?' + (skipped ? ' (' + skipped + ' tài khoản đang chạy sẽ được bỏ qua)' : '');
        if (!(await nexusConfirm(msg, { title: 'Xóa tác vụ đã chọn', confirmText: 'Xóa', danger: true }))) return;
        var okCount = 0;
        for (var i = 0; i < deletable.length; i++) {
            try {
                var r = await fetch('/api/group-copy/jobs/' + encodeURIComponent(deletable[i]), { method: 'DELETE' });
                var d = await r.json();
                if (r.ok && d.success) okCount++;
            } catch (e) { /* tiếp tục các tác vụ còn lại */ }
        }
        state.selected = {};
        showToast('Đã xóa ' + okCount + '/' + deletable.length + ' tác vụ.', okCount ? 'success' : 'error');
        await loadJobs();
    }

    function renderJobs(jobs) {
        var container = $('groupCopyJobs');
        state.jobs = Array.isArray(jobs) ? jobs : [];
        renderSummary(state.jobs);
        // Bỏ khỏi vùng chọn những tác vụ đã biến mất sau khi tải lại.
        var existing = {};
        state.jobs.forEach(function (j) { existing[String(j.jobId || '')] = true; });
        Object.keys(state.selected).forEach(function (id) { if (!existing[id]) delete state.selected[id]; });

        var filtered = state.jobs.filter(jobMatchesFilter);
        var nGroupsFiltered = buildJobGroups(filtered).length;
        var nGroupsTotal = buildJobGroups(state.jobs).length;
        $('groupCopyJobsCount').textContent = (state.jobFilter !== 'all' || state.jobSearch)
            ? nGroupsFiltered + '/' + nGroupsTotal + ' tác vụ'
            : nGroupsTotal + ' tác vụ';

        if (!state.jobs.length) {
            state.selectedJobId = '';
            container.innerHTML = '<div class="group-copy-empty">Chưa có tác vụ sao chép nhóm.<br>Bấm "Thêm tác vụ" để tạo kế hoạch đầu tiên.</div>';
            closeDetailOverlay();
            resetDetailPane();
            updateBulkBar();
            return;
        }
        if (!filtered.length) {
            container.innerHTML = '<div class="group-copy-empty">Không có tác vụ phù hợp với bộ lọc hoặc từ khóa tìm kiếm hiện tại.</div>';
        } else {
            container.innerHTML = '';
            buildJobGroups(filtered).forEach(function (g) {
                if (g.jobs.length > 1) container.appendChild(buildCampaignCard(g.jobs));
                else container.appendChild(buildJobCard(g.jobs[0]));
            });
        }

        if (state.highlightJobId) {
            var card = container.querySelector('[data-job-card="' + state.highlightJobId + '"]');
            if (card) {
                card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                window.setTimeout(function () { card.classList.remove('gc-job-highlight'); }, 2600);
            }
            state.highlightJobId = '';
        }

        if (state.selectedJobId && state.detailOverlayOpen) showDetail(state.selectedJobId, true);
        // Popup chi tiết chiến dịch đang mở -> cập nhật lại theo dữ liệu mới (hoặc đóng nếu hết).
        if (state.campaignModalId) {
            var stillThere = (state.jobs || []).some(function (j) { return String((j && j.campaignId) || '') === state.campaignModalId; });
            if (stillThere) openCampaignModal(state.campaignModalId);
            else closeCampaignModal();
        }
        updateBulkBar();
    }

    async function jobAction(jobId, action) {
        var url = '/api/group-copy/jobs/' + encodeURIComponent(jobId);
        var method = 'POST';
        if (action === 'delete') method = 'DELETE';
        else url += '/' + action;
        try {
            var response = await fetch(url, {
                method: method,
                headers: { 'Content-Type': 'application/json' },
                body: method === 'POST' ? JSON.stringify({}) : undefined
            });
            var data = await response.json();
            if (!response.ok || !data.success) throw new Error(data.error || 'Không thực hiện được thao tác.');
            showToast(data.message || 'Đã cập nhật tác vụ.', 'success');
            await loadJobs();
        } catch (error) {
            showToast(friendlyFetchError(error, 'Không thực hiện được thao tác.'), 'error');
        }
    }

    function closeAllKebabMenus() {
        document.querySelectorAll('.gc-kebab-menu').forEach(function (m) { m.hidden = true; });
    }

    // ─── Chi tiết tác vụ: overlay dạng tab (Tổng quan / Tiến độ / Thành viên / Nhật ký) ─
    var ACTION_LABELS = {
        campaign_expired: 'Chiến dịch đã hết thời gian chạy',
        retry_add_verify_and_cleanup: 'Thử add lại, kiểm tra nhóm đích và dọn dẹp',
        verify_target: 'Kiểm tra nhóm đích',
        create_group_get_link_and_copy: 'Tạo nhóm, lấy link và sao chép thành viên',
        get_link_invite_friends_and_send_friend_requests: 'Lấy link nhóm, thêm bạn bè trực tiếp và gửi lời mời kết bạn'
    };

    function memberStatusLabel(status) {
        var labels = { pending: 'Chưa xử lý', invited: 'Đã gửi, chờ tham gia', joined: 'Đã vào nhóm', waiting_friend: 'Chưa xử lý', success: 'Đã vào nhóm', failed: 'Lỗi', skipped: 'Bỏ qua' };
        return labels[status] || status || 'Đang chờ';
    }

    function memberProcessLabel(member) {
        if (member.status === 'joined' && !Number(member.inviteAttempts) && !Number(member.friendRequestAttempts)) return 'Thêm trực tiếp';
        if (member.friendRemoveDelivery === 'removed') return 'Đã vào nhóm · đã xóa kết bạn';
        if (member.friendRequestDelivery === 'friend_request_with_group_link') return 'Gửi lời mời kết bạn kèm link nhóm';
        if (member.inviteDelivery === 'pending_inbox') return 'Gửi link nhóm (chờ chấp nhận)';
        if (member.status === 'invited') return 'Chờ chấp nhận kết bạn';
        if (member.status === 'joined') return 'Đã vào nhóm';
        if (member.status === 'failed') return 'Thêm trực tiếp / gửi lời mời thất bại';
        return 'Chưa xử lý';
    }

    function memberNote(member) {
        if (member.friendRemoveDelivery === 'removed') return 'Đã vào nhóm và đã xóa kết bạn theo thiết lập chiến dịch.';
        if (member.friendRemoveDelivery === 'failed') return 'Đã vào nhóm nhưng lần xóa kết bạn gần nhất chưa thành công.' + (member.friendRemoveMessage ? ' ' + member.friendRemoveMessage : ' Hệ thống sẽ thử lại ở lần kiểm tra sau.');
        if (member.friendRequestDelivery === 'friend_request_with_group_link') return 'Đã gửi lời mời kết bạn; hệ thống sẽ thử add lại theo chu kỳ.' + (member.friendRequestCode >= 0 ? ' Mã Zalo: ' + member.friendRequestCode + '.' : '');
        if (member.inviteDelivery === 'pending_inbox') return (member.inviteResultCode === 262 ? 'Lời mời đã có trong tin nhắn chờ.' : 'Đã gửi lời mời vào tin nhắn chờ.') + (member.inviteResultCode >= 0 ? ' Mã Zalo: ' + member.inviteResultCode + '.' : '');
        var note = member.error || member.friendRequestMessage || member.inviteResultMessage || '';
        if (member.status === 'failed') {
            var code = member.inviteResultCode >= 0 ? member.inviteResultCode : (member.friendRequestCode >= 0 ? member.friendRequestCode : -1);
            if (code >= 0) note = (note ? note + ' ' : '') + '(Mã lỗi Zalo: ' + code + ')';
        }
        return note || '-';
    }

    function detailItem(label, value) {
        return '<div class="group-copy-detail-item"><span>' + esc(label) + '</span><strong>' + (value == null || value === '' ? '-' : value) + '</strong></div>';
    }

    function switchDetailTab(tab) {
        state.detailTab = tab;
        document.querySelectorAll('[data-detail-tab]').forEach(function (btn) {
            btn.classList.toggle('active', btn.getAttribute('data-detail-tab') === tab);
        });
        document.querySelectorAll('.gc-detail-pane').forEach(function (pane) {
            pane.hidden = pane.getAttribute('data-detail-pane') !== tab;
        });
    }

    function renderDetailTabs(job) {
        var body = $('groupCopyDetailBody');
        var source = job.sourceGroup || {};
        var target = job.targetGroupName || job.targetGroupId || '-';
        var joined = Number(job.joinedCount || job.successCount || 0);
        var total = Number(job.totalMembers || 0);
        var conversion = Number(job.conversionRate || 0);
        var members = job.members || [];
        var runs = (job.runs || []).slice().reverse();

        var linkValue = job.groupLink
            ? '<a href="' + esc(job.groupLink) + '" target="_blank" rel="noopener">' + esc(job.groupLink) + '</a><button type="button" class="gc-link-copy-btn" data-copy-link="' + esc(job.groupLink) + '">Sao chép</button>'
            : 'Chưa có';

        var overviewHtml = '<div class="group-copy-detail-grid">' +
            detailItem('Nhóm nguồn', esc(source.name || source.groupId || job.sourceInput || '-')) +
            detailItem('Nhóm đích', esc(target)) +
            detailItem('Tài khoản thực hiện', esc(job.accountName || job.accountId || '-')) +
            detailItem('Trạng thái', esc(statusLabel(job.status))) +
            detailItem('Link tham gia nhóm', linkValue) +
            detailItem('Link hết hạn', job.groupLinkExpirationDate ? esc(formatDateTime(job.groupLinkExpirationDate)) : (job.groupLink ? 'Không giới hạn' : '-')) +
            detailItem('Trạng thái link', job.groupLink ? (job.groupLinkEnabled ? 'Đang bật' : 'Đang tắt') : '-') +
            detailItem('Số lời mời kết bạn/ngày', dailyLimit(job) + ' người') +
            detailItem('Chu kỳ kiểm tra', Number(job.verifyIntervalMinutes || 30) + ' phút/lần') +
            detailItem('Thời gian chiến dịch', Number(job.campaignDurationDays || 30) + ' ngày') +
            detailItem('Kết thúc chiến dịch', esc(formatDateTime(job.campaignEndAt))) +
            detailItem('Xóa bạn sau khi vào nhóm', job.removeFriendAfterJoin ? 'Đang bật' : 'Đang tắt') +
        '</div>';

        var progressHtml = '<div class="group-copy-detail-grid">' +
            detailItem('Tổng thành viên nguồn', total + ' người') +
            detailItem('Đã thêm trực tiếp', Number(job.preExistingCount || 0) + ' người') +
            detailItem('Đã gửi lời mời kết bạn', Number(job.invitedCount || 0) + ' người') +
            detailItem('Đã vào nhóm', joined + '/' + total + ' người') +
            detailItem('Đang chờ tham gia', Number(job.awaitingJoinCount || 0) + ' người') +
            detailItem('Chưa xử lý', Number(job.pendingInviteCount || 0) + ' người') +
            detailItem('Lỗi', Number(job.failedCount || 0) + ' người') +
            detailItem('Bạn chiến dịch đã xóa', Number(job.removedFriendCount || 0) + ' người') +
            detailItem('Tỷ lệ chuyển đổi (vào nhóm/tổng)', conversion + '%') +
            detailItem('Tỷ lệ nhận lời mời kết bạn', Number(job.inviteConversionRate || 0) + '%') +
            detailItem('Thành viên nhóm đích hiện tại', Number(job.targetMemberCount || 0) + ' người') +
            detailItem('Kiểm tra gần nhất', esc(formatDateTime(job.lastVerifiedAt))) +
            detailItem('Lần xử lý kế tiếp', esc(formatDateTime(job.nextRunAt))) +
        '</div>' + (job.lastVerificationError ? '<div class="gc-log-item-error">Lỗi kiểm tra gần nhất: ' + esc(job.lastVerificationError) + '</div>' : '');

        // Gom trạng thái thành viên về 4 nhóm dễ quan sát.
        function memberBucket(m) {
            var s = String(m.status || 'pending');
            if (s === 'joined' || s === 'success') return 'joined';
            if (s === 'invited') return 'invited';
            if (s === 'failed') return 'failed';
            return 'pending'; // pending / waiting_friend / skipped...
        }
        var counts = { all: members.length, joined: 0, invited: 0, pending: 0, failed: 0 };
        members.forEach(function (m) { counts[memberBucket(m)] += 1; });
        var filterDefs = [
            { key: 'all', label: 'Tất cả' },
            { key: 'joined', label: 'Đã vào nhóm' },
            { key: 'invited', label: 'Đã gửi lời mời' },
            { key: 'pending', label: 'Chưa xử lý' },
            { key: 'failed', label: 'Lỗi' },
        ];
        var chipsHtml = '<div class="gc-member-filters">' + filterDefs.map(function (f) {
            return '<button type="button" class="gc-member-chip' + (state.memberFilter === f.key ? ' active' : '') + ' is-' + f.key + '" data-member-filter="' + f.key + '">'
                + esc(f.label) + '<span class="gc-member-chip-count">' + counts[f.key] + '</span></button>';
        }).join('') + '</div>';

        var membersHtml = chipsHtml + '<div class="gc-member-toolbar"><input type="text" id="groupCopyMemberSearch" placeholder="Tìm theo tên hoặc User ID..." value="' + esc(state.memberSearch) + '"></div>';
        if (!members.length) {
            membersHtml += '<div class="group-copy-detail-empty"><span>◎</span><strong>Chưa có dữ liệu thành viên</strong><p>Danh sách sẽ xuất hiện sau khi hệ thống đọc xong nhóm nguồn.</p></div>';
        } else {
            var query = state.memberSearch.trim().toLowerCase();
            var visibleMembers = members.filter(function (m) {
                if (state.memberFilter !== 'all' && memberBucket(m) !== state.memberFilter) return false;
                if (!query) return true;
                return (String(m.zaloName || '').toLowerCase().indexOf(query) !== -1) || (String(m.userId || '').toLowerCase().indexOf(query) !== -1);
            });
            membersHtml += '<div class="group-copy-member-wrap"><table class="group-copy-member-table"><thead><tr>' +
                '<th>Thành viên</th><th>User ID</th><th>Quan hệ</th><th>Cách xử lý</th><th>Trạng thái</th><th>Số lần thử</th><th>Xử lý gần nhất</th><th>Ghi chú / lỗi</th>' +
                '</tr></thead><tbody>' +
                visibleMembers.map(function (member) {
                    var relation = member.isFriend === true ? 'Đã là bạn bè' : (member.isFriend === false ? 'Chưa kết bạn' : 'Chưa xác định');
                    var attempts = Number(member.inviteAttempts || 0) + Number(member.friendRequestAttempts || 0);
                    var lastAt = Math.max(Number(member.joinedAt || 0), Number(member.friendRequestAt || 0), Number(member.invitedAt || 0), Number(member.attemptedAt || 0));
                    return '<tr>' +
                        '<td>' + esc(member.zaloName || member.userId || '-') + '</td>' +
                        '<td style="font-family:monospace;word-break:break-all">' + esc(member.userId || '-') + '</td>' +
                        '<td>' + esc(relation) + '</td>' +
                        '<td>' + esc(memberProcessLabel(member)) + '</td>' +
                        '<td class="group-copy-member-status ' + esc(member.status || 'pending') + '">' + esc(memberStatusLabel(member.status)) + '</td>' +
                        '<td>' + attempts + '</td>' +
                        '<td>' + esc(lastAt ? formatDateTime(lastAt) : '-') + '</td>' +
                        '<td>' + esc(memberNote(member)) + '</td>' +
                    '</tr>';
                }).join('') +
                '</tbody></table></div>';
            if (!visibleMembers.length) membersHtml += '<div class="group-copy-detail-empty" style="margin-top:10px;"><span>◎</span><strong>Không tìm thấy thành viên phù hợp</strong></div>';
        }

        var logHtml;
        if (!runs.length) {
            logHtml = '<div class="group-copy-detail-empty"><span>◎</span><strong>Chưa có nhật ký hoạt động</strong><p>Nhật ký sẽ xuất hiện sau khi tác vụ chạy đợt đầu tiên.</p></div>';
        } else {
            logHtml = '<div class="gc-log-list">' + runs.map(function (run) {
                var label = ACTION_LABELS[run.action] || run.action || 'Đợt xử lý';
                return '<div class="gc-log-item"><div class="gc-log-item-head"><b>Đợt #' + esc(String(run.runNo || '')) + ' — ' + esc(label) + '</b><span>' + esc(formatDateTime(run.startedAt)) + '</span></div>' +
                    '<div class="gc-log-item-meta">' +
                        '<span>Trạng thái: <b>' + esc(statusLabel(run.status) === run.status ? (run.status || '-') : statusLabel(run.status)) + '</b></span>' +
                        '<span>Đã gửi: <b>' + Number(run.sentCount || run.friendRequestCount || 0) + '</b></span>' +
                        '<span>Vào nhóm mới: <b>' + Number(run.newlyJoinedCount || run.joinedCount || 0) + '</b></span>' +
                        '<span>Lỗi: <b>' + Number(run.failedCount || run.friendRequestFailedCount || 0) + '</b></span>' +
                        '<span>Chuyển đổi: <b>' + Number(run.conversionRate || 0) + '%</b></span>' +
                    '</div>' +
                    (run.error ? '<div class="gc-log-item-error">' + esc(run.error) + '</div>' : '') +
                '</div>';
            }).join('') + '</div>';
        }

        body.innerHTML =
            '<div class="gc-detail-pane" data-detail-pane="overview"' + (state.detailTab !== 'overview' ? ' hidden' : '') + '>' + overviewHtml + '</div>' +
            '<div class="gc-detail-pane" data-detail-pane="progress"' + (state.detailTab !== 'progress' ? ' hidden' : '') + '>' + progressHtml + '</div>' +
            '<div class="gc-detail-pane" data-detail-pane="members"' + (state.detailTab !== 'members' ? ' hidden' : '') + '>' + membersHtml + '</div>' +
            '<div class="gc-detail-pane" data-detail-pane="log"' + (state.detailTab !== 'log' ? ' hidden' : '') + '>' + logHtml + '</div>';

        var memberSearchInput = $('groupCopyMemberSearch');
        if (memberSearchInput) {
            memberSearchInput.addEventListener('input', function () {
                state.memberSearch = memberSearchInput.value;
                renderDetailTabs(state.detailJob);
                var el = $('groupCopyMemberSearch');
                if (el) { el.focus(); el.setSelectionRange(el.value.length, el.value.length); }
            });
        }
        body.querySelectorAll('[data-member-filter]').forEach(function (chip) {
            chip.addEventListener('click', function () {
                state.memberFilter = chip.getAttribute('data-member-filter') || 'all';
                renderDetailTabs(state.detailJob);
            });
        });
        body.querySelectorAll('[data-copy-link]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var link = btn.getAttribute('data-copy-link');
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(link).then(function () { showToast('Đã sao chép link nhóm.', 'success'); });
                } else {
                    showToast('Trình duyệt không hỗ trợ sao chép tự động. Vui lòng bôi đen link để copy.', 'warning');
                }
            });
        });
    }

    function setDetailAvatar(job) {
        setAvatarElement($('groupCopyDetailAvatar'), { name: (job && (job.accountName || job.accountId)) || '?', avatarUrl: job && job.accountAvatar });
    }

    async function showDetail(jobId, silent) {
        jobId = String(jobId || '').trim();
        if (!jobId) return;
        state.selectedJobId = jobId;

        var requestId = ++state.detailRequestId;
        var title = $('groupCopyDetailTitle');
        var subtitle = $('groupCopyDetailSubtitle');
        var body = $('groupCopyDetailBody');
        var cachedJob = state.jobs.find(function (item) { return String(item.jobId || '') === jobId; }) || {};
        if (!silent) {
            state.detailTab = 'overview';
            switchDetailTab('overview');
            openDetailOverlay();
            setDetailAvatar(cachedJob);
            if (body) {
                title.textContent = cachedJob.title || 'Đang tải chi tiết...';
                subtitle.textContent = 'Đang đồng bộ tiến độ và danh sách thành viên...';
                body.innerHTML = '<div class="group-copy-detail-empty"><span>◌</span><strong>Đang tải dữ liệu</strong><p>Vui lòng chờ trong giây lát.</p></div>';
            }
        }

        try {
            var response = await fetch('/api/group-copy/jobs/' + encodeURIComponent(jobId));
            var data = await response.json();
            if (!response.ok || !data.success) throw new Error(data.error || 'Không tải được chi tiết.');
            if (requestId !== state.detailRequestId || state.selectedJobId !== jobId) return;

            var job = data.job || {};
            state.detailJob = job;
            var joined = Number(job.joinedCount || job.successCount || 0);
            var total = Number(job.totalMembers || 0);
            var conversion = Number(job.conversionRate || 0);
            title.textContent = job.title || 'Chi tiết tác vụ';
            subtitle.textContent = statusLabel(job.status) + ' · ' + joined + '/' + total + ' đã vào nhóm · ' + conversion + '% chuyển đổi';
            setDetailAvatar(job);
            renderDetailTabs(job);
        } catch (error) {
            if (requestId !== state.detailRequestId) return;
            var msg = friendlyFetchError(error, 'Không thể tải chi tiết tác vụ.');
            if (!silent) showToast(msg, 'error');
            title.textContent = 'Không tải được chi tiết';
            subtitle.textContent = msg;
            body.innerHTML = '<div class="group-copy-detail-empty"><span>!</span><strong>Không thể tải dữ liệu</strong><p>' + esc(msg) + '</p><button type="button" class="btn btn-ghost btn-sm" id="groupCopyDetailRetry">Thử lại</button></div>';
            var retry = $('groupCopyDetailRetry');
            if (retry) retry.addEventListener('click', function () { showDetail(jobId); });
        }
    }

    function resetDetailPane() {
        var title = $('groupCopyDetailTitle');
        var subtitle = $('groupCopyDetailSubtitle');
        var body = $('groupCopyDetailBody');
        if (title) title.textContent = '';
        if (subtitle) subtitle.textContent = '';
        if (body) body.innerHTML = '';
        state.detailJob = null;
        setDetailAvatar(null);
    }

    function openDetailOverlay() {
        var overlay = $('groupCopyDetailOverlay');
        if (!overlay) return;
        state.detailOverlayOpen = true;
        overlay.classList.add('show');
    }

    function closeDetailOverlay() {
        var overlay = $('groupCopyDetailOverlay');
        if (overlay) overlay.classList.remove('show');
        state.detailOverlayOpen = false;
    }

    function closeDetail() {
        state.selectedJobId = '';
        state.detailRequestId += 1;
        closeDetailOverlay();
        resetDetailPane();
    }

    // ─── Overlay tạo tác vụ (form cấu hình chỉ hiện khi bấm "+ Thêm tác vụ") ─
    function openCreateOverlay() {
        var overlay = $('groupCopyCreateOverlay');
        if (!overlay) return;
        overlay.classList.add('show');
        updateRunButtonState();
    }

    function closeCreateOverlay() {
        var overlay = $('groupCopyCreateOverlay');
        if (overlay) overlay.classList.remove('show');
    }

    // ─── Gắn sự kiện ─────────────────────────────────────────────────────────
    function bindEvents() {
        document.querySelectorAll('[data-target-mode]').forEach(function (button) {
            button.addEventListener('click', function () { setTargetMode(button.getAttribute('data-target-mode')); });
        });
        $('groupCopyAccountTrigger').addEventListener('click', function () { setAccountMenu(!state.accountMenuOpen); });
        $('groupCopySource').addEventListener('input', function () { scheduleSourcePreview(); });
        ['groupCopyStartAt', 'groupCopyDailyLimit', 'groupCopyVerifyMinutes', 'groupCopyNewGroupName', 'groupCopyConsent', 'groupCopyCampaignDays'].forEach(function (id) {
            var el = $(id);
            if (el) el.addEventListener('input', updateRunButtonState);
            if (el) el.addEventListener('change', updateRunButtonState);
        });

        $('groupCopyPickGroupBtn').addEventListener('click', openGroupPicker);
        $('groupCopyPickerClose').addEventListener('click', closeGroupPicker);
        $('groupCopyPickerOverlay').addEventListener('click', function (event) { if (event.target === $('groupCopyPickerOverlay')) closeGroupPicker(); });
        $('groupCopyPickerSearch').addEventListener('input', function () { state.pickerSearch = $('groupCopyPickerSearch').value; renderPickerList(); });
        $('groupCopyPickerSort').addEventListener('change', function () { state.pickerSort = $('groupCopyPickerSort').value; renderPickerList(); });
        $('groupCopyPickerRefresh').addEventListener('click', refreshPickerGroups);
        $('groupCopyPickerList').addEventListener('click', function (event) {
            var row = event.target.closest('[data-pick-group]');
            if (row) pickGroup(row.getAttribute('data-pick-group'));
        });

        document.addEventListener('click', function (event) {
            if (!$('groupCopyAccountPicker').contains(event.target)) setAccountMenu(false);
            if (!event.target.closest('.gc-kebab-wrap')) closeAllKebabMenus();
        });
        document.addEventListener('keydown', function (event) {
            if (event.key === 'Escape') {
                setAccountMenu(false);
                closeCampaignModal();
                closeDetail();
                closeGroupPicker();
                closeCreateOverlay();
            }
        });

        $('groupCopyAddBtn').addEventListener('click', openCreateOverlay);
        $('groupCopyCreateClose').addEventListener('click', closeCreateOverlay);
        $('groupCopyCreateOverlay').addEventListener('click', function (event) { if (event.target === $('groupCopyCreateOverlay')) closeCreateOverlay(); });

        if ($('groupCopyCampaignClose')) $('groupCopyCampaignClose').addEventListener('click', closeCampaignModal);
        if ($('groupCopyCampaignOverlay')) $('groupCopyCampaignOverlay').addEventListener('click', function (event) { if (event.target === $('groupCopyCampaignOverlay')) closeCampaignModal(); });

        // Chọn nhiều tác vụ + xóa hàng loạt.
        if ($('groupCopySelectToggle')) $('groupCopySelectToggle').addEventListener('click', function () { toggleSelectMode(); });
        if ($('groupCopyBulkClear')) $('groupCopyBulkClear').addEventListener('click', clearSelection);
        if ($('groupCopyBulkDelete')) $('groupCopyBulkDelete').addEventListener('click', deleteSelected);
        if ($('groupCopyBulkAll')) $('groupCopyBulkAll').addEventListener('change', function () { selectAllVisible(this.checked); });
        if ($('groupCopyJobs')) $('groupCopyJobs').addEventListener('change', function (event) {
            var cb = event.target;
            if (!cb || !cb.getAttribute || cb.getAttribute('data-select-ids') === null) return;
            var ids = String(cb.getAttribute('data-select-ids') || '').split(',').filter(Boolean);
            setSelected(ids, cb.checked);
            var card = cb.closest ? cb.closest('.gc-job-card') : null;
            if (card) card.classList.toggle('gc-selected', cb.checked);
            updateBulkBar();
        });

        $('groupCopyStartBtn').addEventListener('click', startJob);
        $('groupCopyRefreshJobsBtn').addEventListener('click', async function () {
            var button = $('groupCopyRefreshJobsBtn');
            var previous = button.innerHTML;
            button.disabled = true;
            button.innerHTML = '<span class="spinner" style="width:13px;height:13px;border-width:2px;"></span> Đang làm mới';
            setStatus('Đang đồng bộ tài khoản, nhóm và tác vụ...', 'loading');
            try {
                await loadAccounts();
                await loadJobs();
                setStatus('Dữ liệu đã được cập nhật.', 'success');
                showToast('Đã làm mới dữ liệu.', 'success');
            } finally {
                button.disabled = false;
                button.innerHTML = previous;
            }
        });

        document.querySelectorAll('[data-job-filter]').forEach(function (button) {
            button.addEventListener('click', function () {
                state.jobFilter = button.getAttribute('data-job-filter');
                document.querySelectorAll('[data-job-filter]').forEach(function (b) { b.classList.toggle('active', b === button); });
                renderJobs(state.jobs);
            });
        });
        $('groupCopyJobsSearch').addEventListener('input', function () {
            state.jobSearch = $('groupCopyJobsSearch').value;
            renderJobs(state.jobs);
        });

        $('groupCopyJobs').addEventListener('click', async function (event) {
            var kebabToggle = event.target.closest('[data-kebab-toggle]');
            if (kebabToggle) {
                var id = kebabToggle.getAttribute('data-kebab-toggle');
                var menu = document.querySelector('[data-kebab-menu="' + id + '"]');
                var isOpen = menu && !menu.hidden;
                closeAllKebabMenus();
                if (menu) menu.hidden = isOpen;
                return;
            }
            // ─ Thao tác cấp CHIẾN DỊCH (nhiều tài khoản) ─
            var campToggle = event.target.closest('[data-campaign-toggle]');
            if (campToggle) {
                var cardEl = campToggle.closest('.gc-campaign-card');
                var cid = cardEl && cardEl.getAttribute('data-campaign-card');
                openCampaignModal(cid);
                return;
            }
            var campCancel = event.target.closest('[data-campaign-cancel]');
            var campResume = event.target.closest('[data-campaign-resume]');
            var campDelete = event.target.closest('[data-campaign-delete]');
            if (campCancel) { if (await nexusConfirm('Tạm dừng TẤT CẢ tài khoản trong chiến dịch này?', { title: 'Tạm dừng chiến dịch' })) campaignAction(campCancel.getAttribute('data-campaign-cancel'), 'cancel'); return; }
            if (campResume) { campaignAction(campResume.getAttribute('data-campaign-resume'), 'resume'); return; }
            if (campDelete) { if (await nexusConfirm('Xóa CẢ chiến dịch (mọi tài khoản) và toàn bộ lịch sử?', { title: 'Xóa chiến dịch', confirmText: 'Xóa', danger: true })) campaignAction(campDelete.getAttribute('data-campaign-delete'), 'delete'); return; }

            var detail = event.target.closest('[data-job-detail]');
            var verify = event.target.closest('[data-job-verify]');
            var cancel = event.target.closest('[data-job-cancel]');
            var resume = event.target.closest('[data-job-resume]');
            var del = event.target.closest('[data-job-delete]');
            if (detail) showDetail(detail.getAttribute('data-job-detail'));
            else if (verify) jobAction(verify.getAttribute('data-job-verify'), 'verify');
            else if (cancel) { closeAllKebabMenus(); if (await nexusConfirm('Tạm dừng tác vụ đang chờ này? Bạn có thể tiếp tục lại sau.', { title: 'Tạm dừng tác vụ' })) jobAction(cancel.getAttribute('data-job-cancel'), 'cancel'); }
            else if (resume) { closeAllKebabMenus(); jobAction(resume.getAttribute('data-job-resume'), 'resume'); }
            else if (del) { closeAllKebabMenus(); if (await nexusConfirm('Xóa tác vụ và toàn bộ lịch sử tiến độ?', { title: 'Xóa tác vụ', confirmText: 'Xóa', danger: true })) jobAction(del.getAttribute('data-job-delete'), 'delete'); }
        });

        document.querySelectorAll('[data-detail-tab]').forEach(function (button) {
            button.addEventListener('click', function () { switchDetailTab(button.getAttribute('data-detail-tab')); });
        });
        if ($('groupCopyDetailClose')) $('groupCopyDetailClose').addEventListener('click', closeDetail);
        if ($('groupCopyDetailOverlay')) {
            $('groupCopyDetailOverlay').addEventListener('click', function (event) {
                if (event.target === $('groupCopyDetailOverlay')) closeDetail();
            });
        }
    }

    async function applyPlanCapabilities() {
        // Ẩn nút chọn nhiều tài khoản thực hiện nếu gói không cho phép.
        try {
            var plan = await fetch('/api/license/plan').then(function (r) { return r.json(); });
            state.multiAccountExec = !!plan.multiAccountExec;
        } catch (e) {
            state.multiAccountExec = true;
        }
        var field = document.getElementById('groupCopyExecTrigger');
        if (field) {
            var wrap = field.closest('.gc-field');
            if (!state.multiAccountExec) {
                state.extraAccountIds.clear();
                if (wrap) wrap.hidden = true;
            } else if (wrap) {
                wrap.hidden = false;
            }
        }
    }

    async function init() {
        bindEvents();
        setDefaultStartTime();
        setTargetMode('new');
        renderTargetPreview();
        await loadAccounts();
        await applyPlanCapabilities();
        await loadJobs();
        updateRunButtonState();
        state.jobsTimer = window.setInterval(loadJobs, 10000);
    }

    document.addEventListener('DOMContentLoaded', init);
}());
