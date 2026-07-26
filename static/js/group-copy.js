(function () {
    'use strict';

    var state = {
        accounts: [],
        groups: [],
        targetMode: 'new',
        pollingTask: false,
        jobsTimer: null,
        accountMenuOpen: false,
        jobs: [],
        selectedJobId: '',
        detailRequestId: 0
    };

    var ICON_VERIFY = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6v5h-5"/><path d="M4 18v-5h5"/><path d="M18.5 9A7 7 0 0 0 6.2 6.2L4 8M5.5 15A7 7 0 0 0 17.8 17.8L20 16"/></svg>';
    var ICON_CANCEL = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M9.5 9.5l5 5M14.5 9.5l-5 5"/></svg>';
    var ICON_RESUME = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="6 4 20 12 6 20 6 4"/></svg>';
    var ICON_DELETE = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 7h16"/><path d="M9 7V4h6v3"/><path d="M6 7l1 13h10l1-13"/></svg>';

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
        return !!(account && account.cookies && account.zpwEnk && account.imei);
    }

    function getGroupId(group) {
        return String((group && (group.groupId || group.gridId || group.id || group.gid)) || '').trim();
    }

    function getGroupName(group) {
        var gid = getGroupId(group);
        return String((group && (group.name || group.groupName || group.grid_name || group.title)) || ('Nhóm ' + gid.slice(0, 8))).trim();
    }

    function setStatus(message, type) {
        var el = $('groupCopyStatus');
        if (!el) return;
        el.hidden = !message;
        el.className = 'group-copy-status ' + (type || '');
        el.textContent = message || '';
    }

    function setStartButtonLoading(loading) {
        var button = $('groupCopyStartBtn');
        if (!button) return;
        button.disabled = !!loading;
        button.innerHTML = loading
            ? '<span>◌</span> Đang đọc nhóm...'
            : (state.targetMode === 'new' ? '<span>▶</span> Tạo nhóm và gửi lời mời' : '<span>▶</span> Gửi lời mời vào nhóm');
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

    function dailyTime(job) {
        var raw = String((job && job.dailyRunTime) || '').trim();
        if (raw) return raw;
        var date = new Date((job && job.startAt) || '');
        if (isNaN(date.getTime())) return '-';
        return date.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' });
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

    function setAvatarElement(element, account) {
        if (!element) return;
        var name = getAccountName(account);
        var avatar = getAccountAvatar(account);
        element.innerHTML = '';
        element.textContent = initials(name);
        if (!avatar) return;
        var image = document.createElement('img');
        image.src = avatar;
        image.alt = name;
        image.loading = 'lazy';
        image.addEventListener('error', function () { image.remove(); });
        element.appendChild(image);
    }

    function miniAccountAvatar(job) {
        var name = String((job && job.accountName) || 'Tài khoản');
        var avatar = safeImageUrl(job && job.accountAvatar);
        return '<span class="group-copy-mini-avatar"><span>' + esc(initials(name)) + '</span>' +
            (avatar ? '<img src="' + esc(avatar) + '" alt="">' : '') + '</span>';
    }

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
            return;
        }
        nameEl.textContent = getAccountName(account);
        stateEl.textContent = accountReady(account) ? 'Sẵn sàng thực hiện' : 'Chưa đủ dữ liệu đăng nhập';
        setAvatarElement($('groupCopyAccountAvatar'), account);
        if (topEl) topEl.textContent = getAccountName(account);
    }

    function renderAccountMenu() {
        var menu = $('groupCopyAccountMenu');
        menu.innerHTML = '';
        if (!state.accounts.length) {
            var empty = document.createElement('div');
            empty.className = 'group-copy-account-empty';
            empty.textContent = 'Chưa có tài khoản để chọn.';
            menu.appendChild(empty);
            return;
        }

        var selectedId = (($('groupCopyAccount') || {}).value || '').trim();
        state.accounts.forEach(function (account) {
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
            small.textContent = accountReady(account) ? 'Sẵn sàng' : 'Thiếu cookies, zpwEnk hoặc IMEI';
            info.appendChild(strong);
            info.appendChild(small);

            var mark = document.createElement('span');
            mark.className = 'group-copy-account-option-mark';
            mark.textContent = id === selectedId ? '✓' : '';

            button.appendChild(avatar);
            button.appendChild(info);
            button.appendChild(mark);
            button.addEventListener('click', function () { selectAccount(id); });
            menu.appendChild(button);
        });
    }

    function setAccountMenu(open) {
        state.accountMenuOpen = !!open;
        $('groupCopyAccountMenu').hidden = !state.accountMenuOpen;
        $('groupCopyAccountTrigger').setAttribute('aria-expanded', state.accountMenuOpen ? 'true' : 'false');
        $('groupCopyAccountPicker').classList.toggle('open', state.accountMenuOpen);
    }

    async function selectAccount(accountId) {
        $('groupCopyAccount').value = accountId || '';
        if (accountId) storageSet('nexus_group_copy_account', accountId);
        renderSelectedAccount();
        renderAccountMenu();
        setAccountMenu(false);
        await loadGroups();
    }

    function setTargetMode(mode) {
        state.targetMode = mode === 'existing' ? 'existing' : 'new';
        document.querySelectorAll('[data-target-mode]').forEach(function (button) {
            button.classList.toggle('active', button.getAttribute('data-target-mode') === state.targetMode);
        });
        $('groupCopyNewTargetWrap').hidden = state.targetMode !== 'new';
        $('groupCopyExistingTargetWrap').hidden = state.targetMode !== 'existing';
        var startButton = $('groupCopyStartBtn');
        if (startButton && !startButton.disabled) {
            startButton.innerHTML = state.targetMode === 'new'
                ? '<span>▶</span> Tạo nhóm và lập lịch'
                : '<span>▶</span> Lập lịch mời vào nhóm';
        }
        if (state.targetMode === 'existing' && !state.groups.length && (($('groupCopyAccount') || {}).value || '')) {
            loadGroups();
        }
    }

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
            await loadGroups();
        } catch (error) {
            state.accounts = [];
            $('groupCopyAccount').value = '';
            renderSelectedAccount();
            renderAccountMenu();
            setStatus(error.message || String(error), 'error');
        }
    }

    async function loadGroups() {
        var accountId = (($('groupCopyAccount') || {}).value || '').trim();
        var select = $('groupCopyExistingGroup');
        state.groups = [];
        if (!accountId) {
            select.innerHTML = '<option value="">Chọn tài khoản trước</option>';
            return;
        }
        select.innerHTML = '<option value="">Đang tải nhóm hiện tại...</option>';
        try {
            var response = await fetch('/api/groups/personal?accountId=' + encodeURIComponent(accountId));
            var data = await response.json();
            if (!response.ok || !data.success) throw new Error(data.error || 'Không tải được danh sách nhóm.');
            state.groups = data.groups || [];
            renderGroups();
        } catch (error) {
            select.innerHTML = '<option value="">Chưa đồng bộ được nhóm</option>';
            if (state.targetMode === 'existing') setStatus(error.message || String(error), 'error');
        }
    }

    function renderGroups() {
        var select = $('groupCopyExistingGroup');
        if (!state.groups.length) {
            select.innerHTML = '<option value="">Chưa có nhóm hiện tại đã đồng bộ</option>';
            return;
        }
        state.groups.sort(function (a, b) { return getGroupName(a).localeCompare(getGroupName(b), 'vi'); });
        select.innerHTML = '<option value="">Chọn nhóm nhận thành viên</option>' + state.groups.map(function (group) {
            var id = getGroupId(group);
            var count = Number(group.memberCount || group.totalMember || group.total || 0);
            return '<option value="' + esc(id) + '">' + esc(getGroupName(group) + (count ? ' · ' + count + ' thành viên' : '')) + '</option>';
        }).join('');
    }

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
            targetMode: state.targetMode,
            startAt: startAt,
            dailyLimit: limit,
            campaignDurationDays: campaignDays,
            verifyIntervalMinutes: verifyMinutes,
            removeFriendAfterJoin: !!(($('groupCopyRemoveFriend') || {}).checked),
            consentConfirmed: !!(($('groupCopyConsent') || {}).checked)
        };

        if (state.targetMode === 'new') {
            payload.newGroupName = (($('groupCopyNewGroupName') || {}).value || '').trim();
            payload.targetGroupName = payload.newGroupName;
            payload.title = payload.newGroupName ? ('Sao chép vào ' + payload.newGroupName) : 'Sao chép thành viên nhóm';
        } else {
            payload.targetGroupId = (($('groupCopyExistingGroup') || {}).value || '').trim();
            var group = state.groups.find(function (item) { return getGroupId(item) === payload.targetGroupId; });
            payload.targetGroupName = group ? getGroupName(group) : payload.targetGroupId;
            payload.title = payload.targetGroupName ? ('Sao chép vào ' + payload.targetGroupName) : 'Sao chép thành viên nhóm';
        }
        return payload;
    }

    function validatePayload(payload) {
        if (!payload.accountId) return 'Vui lòng chọn tài khoản thực hiện.';
        var account = state.accounts.find(function (item) { return getAccountId(item) === payload.accountId; });
        if (account && !accountReady(account)) return 'Tài khoản chưa đủ cookies, zpwEnk hoặc IMEI.';
        if (!payload.sourceInput) return 'Vui lòng dán link hoặc ID nhóm nguồn.';
        if (payload.targetMode === 'new' && !payload.newGroupName) return 'Vui lòng nhập tên nhóm mới.';
        if (payload.targetMode === 'existing' && !payload.targetGroupId) return 'Vui lòng chọn một nhóm hiện tại.';
        if (!payload.startAt) return 'Vui lòng chọn thời gian bắt đầu.';
        if (!payload.dailyLimit || payload.dailyLimit < 1 || payload.dailyLimit > 100) return 'Số lời mời kết bạn mỗi ngày phải từ 1 đến 100.';
        if (!payload.campaignDurationDays || payload.campaignDurationDays < 1 || payload.campaignDurationDays > 365) return 'Thời gian chạy chiến dịch phải từ 1 đến 365 ngày.';
        if (!payload.verifyIntervalMinutes || payload.verifyIntervalMinutes < 1 || payload.verifyIntervalMinutes > 1440) return 'Chu kỳ thử add lại phải từ 1 đến 1440 phút.';
        if (!payload.consentConfirmed) return 'Vui lòng xác nhận quyền quản lý nhóm và gửi lời mời.';
        return '';
    }

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

    async function startJob() {
        var payload = buildPayload();
        var error = validatePayload(payload);
        if (error) {
            setStatus(error, 'error');
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
            var verifyMinutes = Number(result.verifyIntervalMinutes || ((result.job || {}).verifyIntervalMinutes) || 30);
            var prefix = payload.targetMode === 'new'
                ? 'Đã tạo tác vụ. Nhóm mới sẽ được tạo, kích hoạt link và xử lý ngay ở đợt đầu.'
                : 'Đã tạo tác vụ cho nhóm hiện tại và sẽ lấy link nhóm trước khi xử lý.';
            setStatus(prefix + ' Tổng ' + total + ' thành viên; hệ thống thêm bạn bè trước, sau đó gửi tối đa ' + payload.dailyLimit + ' lời mời kết bạn/ngày và thử add lại mỗi ' + verifyMinutes + ' phút. Không gửi tin nhắn riêng. Chiến dịch dừng khi đủ thành viên hoặc hết ' + payload.campaignDurationDays + ' ngày.' + (payload.removeFriendAfterJoin ? ' Đã bật xóa kết bạn với người do chiến dịch vừa kết bạn.' : ''), 'success');
            await loadJobs();
            $('groupCopyConsent').checked = false;
        } catch (err) {
            setStatus(err.message || String(err), 'error');
        } finally {
            state.pollingTask = false;
            setStartButtonLoading(false);
        }
    }

    async function loadJobs() {
        var container = $('groupCopyJobs');
        if (!container) return;
        if (!container.children.length) {
            container.innerHTML = '<div class="group-copy-empty">Đang tải danh sách tác vụ...</div>';
        }
        try {
            var response = await fetch('/api/group-copy/jobs');
            var data = await response.json();
            if (!response.ok || !data.success) throw new Error(data.error || 'Không tải được tác vụ.');
            renderJobs(data.jobs || []);
        } catch (error) {
            renderSummary([]);
            $('groupCopyJobsCount').textContent = 'Không tải được';
            container.innerHTML = '<div class="group-copy-empty">' + esc(error.message || String(error)) + '</div>';
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

    function renderJobs(jobs) {
        var container = $('groupCopyJobs');
        state.jobs = Array.isArray(jobs) ? jobs : [];
        renderSummary(state.jobs);
        $('groupCopyJobsCount').textContent = state.jobs.length + ' tác vụ';
        if (!state.jobs.length) {
            state.selectedJobId = '';
            container.innerHTML = '<div class="group-copy-empty">Chưa có tác vụ sao chép nhóm.<br>Hãy tạo chiến dịch đầu tiên ở cột bên trái.</div>';
            resetDetailPane();
            return;
        }

        var selectedStillExists = state.jobs.some(function (job) {
            return String(job.jobId || '') === String(state.selectedJobId || '');
        });
        if (!selectedStillExists) state.selectedJobId = String((state.jobs[0] || {}).jobId || '');

        var rows = state.jobs.map(function (job) {
            var source = job.sourceGroup || {};
            var sourceName = source.name || source.groupId || job.sourceInput || '-';
            var target = job.targetGroupName || job.targetGroupId || (job.targetMode === 'new' ? job.newGroupName : 'Chưa xác định');
            var total = Number(job.totalMembers || 0);
            var joined = Number(job.joinedCount || job.successCount || 0);
            var percent = Number(job.conversionRate || job.progressPercent || 0);
            var status = job.status || 'pending';
            var jobId = String(job.jobId || '');
            var selected = jobId === String(state.selectedJobId || '');
            var nextRun = (status === 'pending' || status === 'monitoring') ? formatDateTime(job.nextRunAt) : '-';
            var problemNote = job.lastError || job.lastVerificationError || '';

            var actions = '';
            if (job.targetGroupId && (status === 'pending' || status === 'monitoring' || status === 'failed')) actions += '<button class="gcj-action-btn is-ghost" data-job-verify="' + esc(jobId) + '" title="Kiểm tra ngay" aria-label="Kiểm tra ngay">' + ICON_VERIFY + '</button>';
            if (status === 'pending' || status === 'monitoring') actions += '<button class="gcj-action-btn is-warning" data-job-cancel="' + esc(jobId) + '" title="Hủy tác vụ" aria-label="Hủy tác vụ">' + ICON_CANCEL + '</button>';
            if ((status === 'failed' || status === 'cancelled') && Number(job.pendingCount || 0) > 0) actions += '<button class="gcj-action-btn is-success" data-job-resume="' + esc(jobId) + '" title="Tiếp tục" aria-label="Tiếp tục">' + ICON_RESUME + '</button>';
            if (status !== 'running') actions += '<button class="gcj-action-btn is-danger" data-job-delete="' + esc(jobId) + '" title="Xóa tác vụ" aria-label="Xóa tác vụ">' + ICON_DELETE + '</button>';

            return '<tr class="group-copy-job-row' + (selected ? ' selected' : '') + '" data-job-select="' + esc(jobId) + '" tabindex="0" aria-label="Mở chi tiết ' + esc(job.title || 'tác vụ sao chép nhóm') + '">' +
                '<td class="gcj-account">' + miniAccountAvatar(job) +
                    '<div class="gcj-account-copy"><strong>' + esc(job.title || 'Sao chép thành viên nhóm') + '</strong>' +
                    '<span>' + esc(job.accountName || job.accountId || 'Tài khoản') + '</span></div>' +
                '</td>' +
                '<td class="gcj-route"><span title="' + esc(sourceName) + '">' + esc(sourceName) + '</span><b>→</b><span title="' + esc(target || '-') + '">' + esc(target || '-') + '</span></td>' +
                '<td class="gcj-status">' +
                    '<span class="group-copy-job-badge ' + esc(status) + '">' + esc(statusLabel(status)) + '</span>' +
                    (problemNote ? '<span class="gcj-warn" title="' + esc(problemNote) + '">⚠</span>' : '') +
                '</td>' +
                '<td class="gcj-progress">' +
                    '<div class="group-copy-progress"><span style="width:' + Math.max(0, Math.min(100, percent)) + '%"></span></div>' +
                    '<strong>' + percent + '%</strong>' +
                '</td>' +
                '<td class="gcj-joined">' + joined + '<span>/' + total + '</span></td>' +
                '<td class="gcj-next">' + esc(nextRun) + '</td>' +
                '<td class="gcj-actions">' + (actions || '<span class="gcj-actions-empty">—</span>') + '</td>' +
            '</tr>';
        }).join('');

        container.innerHTML =
            '<table class="group-copy-jobs-table">' +
                '<thead><tr>' +
                    '<th>Tài khoản &amp; chiến dịch</th>' +
                    '<th>Nhóm nguồn → đích</th>' +
                    '<th>Trạng thái</th>' +
                    '<th>Tiến độ</th>' +
                    '<th>Đã vào</th>' +
                    '<th>Lần xử lý kế tiếp</th>' +
                    '<th>Thao tác</th>' +
                '</tr></thead>' +
                '<tbody>' + rows + '</tbody>' +
            '</table>';

        if (state.selectedJobId) showDetail(state.selectedJobId, true);
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
            setStatus(data.message || 'Đã cập nhật tác vụ.', 'success');
            await loadJobs();
        } catch (error) {
            setStatus(error.message || String(error), 'error');
        }
    }

    async function showDetail(jobId, silent) {
        jobId = String(jobId || '').trim();
        if (!jobId) return;
        state.selectedJobId = jobId;
        document.querySelectorAll('[data-job-select]').forEach(function (item) {
            item.classList.toggle('selected', item.getAttribute('data-job-select') === jobId);
        });

        var requestId = ++state.detailRequestId;
        var title = $('groupCopyDetailTitle');
        var subtitle = $('groupCopyDetailSubtitle');
        var body = $('groupCopyDetailBody');
        if (!silent && body) {
            title.textContent = 'Đang tải chi tiết...';
            subtitle.textContent = 'Đang đồng bộ tiến độ và danh sách thành viên.';
            body.innerHTML = '<div class="group-copy-detail-empty"><span>◌</span><strong>Đang tải dữ liệu</strong><p>Vui lòng chờ trong giây lát.</p></div>';
        }

        try {
            var response = await fetch('/api/group-copy/jobs/' + encodeURIComponent(jobId));
            var data = await response.json();
            if (!response.ok || !data.success) throw new Error(data.error || 'Không tải được chi tiết.');
            if (requestId !== state.detailRequestId || state.selectedJobId !== jobId) return;

            var job = data.job || {};
            var source = job.sourceGroup || {};
            var target = job.targetGroupName || job.targetGroupId || '-';
            var joined = Number(job.joinedCount || job.successCount || 0);
            var total = Number(job.totalMembers || 0);
            var conversion = Number(job.conversionRate || 0);
            title.textContent = job.title || 'Chi tiết tác vụ';
            subtitle.textContent = statusLabel(job.status) + ' · ' + joined + '/' + total + ' đã vào nhóm · ' + conversion + '% chuyển đổi';

            var members = job.members || [];
            var html = '<div class="group-copy-detail-grid">' +
                detailItem('Nhóm nguồn', source.name || source.groupId || job.sourceInput || '-') +
                detailItem('Nhóm đích', target) +
                detailItem('Tài khoản', job.accountName || job.accountId || '-') +
                detailItem('Kết bạn mỗi ngày', dailyLimit(job) + ' người') +
                detailItem('Thời gian chiến dịch', Number(job.campaignDurationDays || 30) + ' ngày') +
                detailItem('Kết thúc chiến dịch', formatDateTime(job.campaignEndAt)) +
                detailItem('Xóa bạn sau khi add', job.removeFriendAfterJoin ? 'Đang bật' : 'Đang tắt') +
                detailItem('Link tham gia nhóm', job.groupLink || '-') +
                detailItem('Đã gửi lời mời', Number(job.invitedCount || 0) + ' người') +
                detailItem('Đã gửi kết bạn', Number(job.campaignFriendCount || job.inboxInviteCount || 0) + ' người') +
                detailItem('Bạn chiến dịch đã xóa', Number(job.removedFriendCount || 0) + ' người') +
                detailItem('Đang chờ tham gia', Number(job.awaitingJoinCount || 0) + ' người') +
                detailItem('Đã vào nhóm', joined + '/' + total + ' người') +
                detailItem('Tỷ lệ sao chép', conversion + '%') +
                detailItem('Tỷ lệ nhận lời mời', Number(job.inviteConversionRate || 0) + '%') +
                detailItem('Chưa gửi lời mời', Number(job.pendingInviteCount || 0) + ' người') +
                detailItem('Kiểm tra định kỳ', Number(job.verifyIntervalMinutes || 30) + ' phút/lần') +
                detailItem('Kiểm tra gần nhất', formatDateTime(job.lastVerifiedAt)) +
                detailItem('Thành viên nhóm đích', Number(job.targetMemberCount || 0) + ' người') +
                detailItem('Lần xử lý kế tiếp', formatDateTime(job.nextRunAt)) +
            '</div>';

            if (!members.length) {
                html += '<div class="group-copy-detail-empty"><span>◎</span><strong>Chưa có dữ liệu thành viên</strong><p>Danh sách sẽ xuất hiện sau khi hệ thống đọc xong nhóm nguồn.</p></div>';
            } else {
                html += '<div class="group-copy-member-wrap">' +
                    '<table class="group-copy-member-table"><thead><tr><th>Thành viên</th><th>User ID</th><th>Quan hệ</th><th>Trạng thái</th><th>Ghi chú</th></tr></thead><tbody>' +
                    members.map(function (member) {
                        var relation = member.isFriend === true ? 'Đã là bạn bè' : (member.isFriend === false ? 'Chưa kết bạn' : 'Chưa xác định');
                        var note = member.error || member.friendRequestMessage || member.inviteResultMessage || '-';
                        if (member.friendRemoveDelivery === 'removed') {
                            note = 'Đã vào nhóm và đã xóa kết bạn theo thiết lập chiến dịch.';
                        } else if (member.friendRemoveDelivery === 'failed') {
                            note = 'Đã vào nhóm nhưng lần xóa kết bạn gần nhất chưa thành công.' +
                                (member.friendRemoveMessage ? ' ' + member.friendRemoveMessage : ' Hệ thống sẽ thử lại ở lần kiểm tra sau.');
                        } else if (member.friendRequestDelivery === 'friend_request_with_group_link') {
                            note = 'Đã gửi lời mời kết bạn; hệ thống sẽ thử add lại theo chu kỳ.' +
                                (member.friendRequestCode >= 0 ? ' Mã Zalo: ' + member.friendRequestCode + '.' : '');
                        } else if (member.inviteDelivery === 'pending_inbox') {
                            note = (member.inviteResultCode === 262 ? 'Lời mời đã có trong tin nhắn chờ.' : 'Đã gửi lời mời vào tin nhắn chờ.') +
                                (member.inviteResultCode >= 0 ? ' Mã Zalo: ' + member.inviteResultCode + '.' : '');
                        } else if (member.inviteResultCode >= 0 && member.status === 'failed') {
                            note += ' Mã Zalo: ' + member.inviteResultCode + '.';
                        }
                        return '<tr>' +
                            '<td>' + esc(member.zaloName || member.userId || '-') + '</td>' +
                            '<td style="font-family:monospace;word-break:break-all">' + esc(member.userId || '-') + '</td>' +
                            '<td>' + esc(relation) + '</td>' +
                            '<td class="group-copy-member-status ' + esc(member.status || 'pending') + '">' + esc(memberStatusLabel(member.status)) + '</td>' +
                            '<td>' + esc(note) + '</td>' +
                        '</tr>';
                    }).join('') +
                    '</tbody></table></div>';
            }
            body.innerHTML = html;
        } catch (error) {
            if (requestId !== state.detailRequestId) return;
            if (!silent) setStatus(error.message || String(error), 'error');
            title.textContent = 'Không tải được chi tiết';
            subtitle.textContent = error.message || String(error);
            body.innerHTML = '<div class="group-copy-detail-empty"><span>!</span><strong>Không thể tải dữ liệu</strong><p>' + esc(error.message || String(error)) + '</p></div>';
        }
    }

    function detailItem(label, value) {
        return '<div class="group-copy-detail-item"><span>' + esc(label) + '</span><strong>' + esc(value == null || value === '' ? '-' : value) + '</strong></div>';
    }

    function memberStatusLabel(status) {
        var labels = { pending: 'Chưa gửi lời mời', invited: 'Đã gửi, chờ tham gia', joined: 'Đã vào nhóm', waiting_friend: 'Chưa gửi lời mời', success: 'Đã vào nhóm', failed: 'Lỗi', skipped: 'Bỏ qua' };
        return labels[status] || status || 'Đang chờ';
    }

    function resetDetailPane() {
        var title = $('groupCopyDetailTitle');
        var subtitle = $('groupCopyDetailSubtitle');
        var body = $('groupCopyDetailBody');
        if (title) title.textContent = 'Chưa chọn tác vụ';
        if (subtitle) subtitle.textContent = 'Chọn một chiến dịch ở cột giữa để xem thành viên và tiến độ thực tế.';
        if (body) body.innerHTML = '<div class="group-copy-detail-empty"><span aria-hidden="true">◎</span><strong>Chi tiết sẽ hiển thị tại đây</strong><p>Không còn popup che màn hình. Bạn có thể vừa xem tiến độ, vừa điều chỉnh chiến dịch mới.</p></div>';
    }

    function closeDetail() {
        state.selectedJobId = '';
        state.detailRequestId += 1;
        document.querySelectorAll('[data-job-select]').forEach(function (item) {
            item.classList.remove('selected');
        });
        resetDetailPane();
    }

    function bindEvents() {
        document.querySelectorAll('[data-target-mode]').forEach(function (button) {
            button.addEventListener('click', function () { setTargetMode(button.getAttribute('data-target-mode')); });
        });
        $('groupCopyAccountTrigger').addEventListener('click', function () { setAccountMenu(!state.accountMenuOpen); });
        document.addEventListener('click', function (event) {
            if (!$('groupCopyAccountPicker').contains(event.target)) setAccountMenu(false);
        });
        document.addEventListener('keydown', function (event) {
            if (event.key === 'Escape') {
                setAccountMenu(false);
                closeDetail();
            }
        });
        $('groupCopyStartBtn').addEventListener('click', startJob);
        $('groupCopyReloadBtn').addEventListener('click', async function () {
            setStatus('Đang làm mới tài khoản và nhóm...', 'loading');
            await loadAccounts();
            await loadJobs();
            setStatus('Đã làm mới dữ liệu.', 'success');
        });
        $('groupCopyRefreshJobsBtn').addEventListener('click', loadJobs);
        $('groupCopyJobs').addEventListener('click', async function (event) {
            var verify = event.target.closest('[data-job-verify]');
            var cancel = event.target.closest('[data-job-cancel]');
            var resume = event.target.closest('[data-job-resume]');
            var del = event.target.closest('[data-job-delete]');
            var jobCard = event.target.closest('[data-job-select]');
            if (verify) jobAction(verify.getAttribute('data-job-verify'), 'verify');
            else if (cancel) { if (await nexusConfirm('Hủy tác vụ đang chờ này?', { title: 'Hủy tác vụ' })) jobAction(cancel.getAttribute('data-job-cancel'), 'cancel'); }
            else if (resume) jobAction(resume.getAttribute('data-job-resume'), 'resume');
            else if (del) { if (await nexusConfirm('Xóa tác vụ và lịch sử tiến độ?', { title: 'Xóa tác vụ', confirmText: 'Xóa', danger: true })) jobAction(del.getAttribute('data-job-delete'), 'delete'); }
            else if (jobCard) showDetail(jobCard.getAttribute('data-job-select'));
        });
        $('groupCopyJobs').addEventListener('keydown', function (event) {
            if (event.key !== 'Enter' && event.key !== ' ') return;
            var jobCard = event.target.closest('[data-job-select]');
            if (!jobCard || event.target.closest('button')) return;
            event.preventDefault();
            showDetail(jobCard.getAttribute('data-job-select'));
        });
        if ($('groupCopyDetailClose')) $('groupCopyDetailClose').addEventListener('click', closeDetail);
    }

    async function init() {
        bindEvents();
        setDefaultStartTime();
        setTargetMode('new');
        await loadAccounts();
        await loadJobs();
        state.jobsTimer = window.setInterval(loadJobs, 10000);
    }

    document.addEventListener('DOMContentLoaded', init);
}());
