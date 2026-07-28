(function () {
    var state = {
        accounts: [],
        schedules: [],
        plans: [],
        messageStats: {}
    };

    function safeArray(value) {
        return Array.isArray(value) ? value : [];
    }

    function number(value) {
        var n = Number(value);
        return Number.isFinite(n) ? n : 0;
    }

    function text(value) {
        if (value === null || value === undefined) return '';
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function parseTime(value) {
        if (!value) return 0;
        var t = new Date(value).getTime();
        return Number.isFinite(t) ? t : 0;
    }

    function formatDate(value) {
        if (!value) return 'Chưa có thời gian';
        var date = new Date(value);
        if (!Number.isFinite(date.getTime())) return 'Chưa có thời gian';
        return date.toLocaleString('vi-VN', {
            day: '2-digit', month: '2-digit', year: 'numeric',
            hour: '2-digit', minute: '2-digit'
        });
    }

    function localDateKey(value) {
        if (!value) return '';
        var date = new Date(value);
        if (!Number.isFinite(date.getTime())) return '';
        var y = date.getFullYear();
        var m = String(date.getMonth() + 1).padStart(2, '0');
        var d = String(date.getDate()).padStart(2, '0');
        return y + '-' + m + '-' + d;
    }

    function normalizeStatus(status) {
        return String(status || '').trim().toLowerCase();
    }

    function completedStatus(status) {
        status = normalizeStatus(status);
        return ['done', 'completed', 'success', 'sent', 'finished'].indexOf(status) !== -1;
    }

    function failedStatus(status) {
        status = normalizeStatus(status);
        return ['error', 'failed', 'failure', 'cancelled', 'stopped'].indexOf(status) !== -1;
    }

    function activeStatus(status) {
        status = normalizeStatus(status);
        return ['running', 'processing', 'pending', 'queued', 'scheduled', 'waiting'].indexOf(status) !== -1;
    }

    function accountReady(account) {
        if (!account || typeof account !== 'object') return false;
        var cookies = String(account.cookies || '');
        return !!(
            account.loginCaptured &&
            account.zpwEnk &&
            (account.hasZpwSek || cookies.indexOf('zpw_sek=') !== -1)
        );
    }

    function getPlanBatches(plan) {
        if (!plan || typeof plan !== 'object') return [];
        return safeArray(plan.batches && plan.batches.length ? plan.batches : plan.items);
    }

    function planStatus(plan) {
        var batches = getPlanBatches(plan);
        if (batches.some(function (batch) { return failedStatus(batch.status); })) return 'error';
        if (batches.some(function (batch) { return activeStatus(batch.status); })) return 'pending';
        if (batches.length && batches.every(function (batch) { return completedStatus(batch.status); })) return 'completed';
        return batches.length ? 'pending' : 'draft';
    }

    function planTime(plan) {
        var batches = getPlanBatches(plan);
        var next = batches
            .map(function (batch) { return parseTime(batch.runAt || batch.scheduledAt || batch.scheduledDate || batch.date); })
            .filter(Boolean)
            .sort(function (a, b) { return b - a; })[0];
        return next || parseTime(plan && (plan.runAt || plan.startDate || plan.createdAt));
    }

    function todayCount() {
        var today = localDateKey(Date.now());
        var scheduleCount = state.schedules.filter(function (item) {
            return localDateKey(item.runAt) === today;
        }).length;
        var planCount = 0;
        state.plans.forEach(function (plan) {
            var batches = getPlanBatches(plan);
            if (batches.length) {
                planCount += batches.filter(function (batch) {
                    return localDateKey(batch.runAt || batch.scheduledAt || batch.scheduledDate || batch.date) === today;
                }).length;
            } else if (localDateKey(plan.startDate || plan.runAt) === today) {
                planCount += 1;
            }
        });
        return scheduleCount + planCount;
    }

    function statusMeta(status) {
        var normalized = normalizeStatus(status);
        if (completedStatus(normalized)) return { label: 'Hoàn thành', cls: 'success' };
        if (failedStatus(normalized)) return { label: normalized === 'cancelled' ? 'Đã hủy' : 'Có lỗi', cls: 'danger' };
        if (normalized === 'running' || normalized === 'processing') return { label: 'Đang chạy', cls: 'running' };
        if (normalized === 'draft') return { label: 'Bản nháp', cls: 'muted' };
        return { label: 'Sắp chạy', cls: 'pending' };
    }

    function setMetric(id, value) {
        var el = document.getElementById(id);
        if (el) el.textContent = String(value);
    }

    function renderMetrics() {
        var ready = state.accounts.filter(accountReady).length;
        var totalAccounts = state.accounts.length;
        var activeSchedules = state.schedules.filter(function (item) { return activeStatus(item.status); }).length;
        var activePlans = state.plans.filter(function (item) { return activeStatus(planStatus(item)); }).length;
        var accountIssues = state.accounts.filter(function (item) { return totalAccounts && !accountReady(item); }).length;
        var failed = state.schedules.filter(function (item) { return failedStatus(item.status); }).length + state.plans.filter(function (item) { return failedStatus(planStatus(item)); }).length;
        var unread = number(state.messageStats.unread);

        setMetric('metricReadyAccounts', ready);
        setMetric('metricActiveCampaigns', activeSchedules + activePlans);
        setMetric('metricTodayRuns', todayCount());
        setMetric('metricAttention', accountIssues + failed + unread);

        var accountsHint = document.getElementById('metricAccountsHint');
        var campaignsHint = document.getElementById('metricCampaignsHint');
        var attentionHint = document.getElementById('metricAttentionHint');
        if (accountsHint) accountsHint.textContent = totalAccounts ? (ready + '/' + totalAccounts + ' tài khoản đã đủ dữ liệu') : 'Chưa có tài khoản';
        if (campaignsHint) campaignsHint.textContent = (state.schedules.length + state.plans.length) + ' lịch và chiến dịch đang lưu';
        if (attentionHint) attentionHint.textContent = unread ? (unread + ' tin nhắn chưa đọc') : 'Lỗi và dữ liệu thiếu';
    }

    function activityItems() {
        var items = [];
        state.schedules.forEach(function (schedule) {
            items.push({
                type: 'schedule',
                title: schedule.title || 'Lịch gửi tin nhắn',
                subtitle: (schedule.accountName || schedule.senderName || 'Chưa rõ tài khoản') + ' · ' + safeArray(schedule.recipients).length + ' người nhận',
                time: parseTime(schedule.runAt || schedule.createdAt),
                status: schedule.status || 'pending'
            });
        });
        state.plans.forEach(function (plan) {
            var recipients = safeArray(plan.recipients || plan.members || plan.targets);
            items.push({
                type: 'plan',
                title: plan.title || plan.name || plan.planTypeLabel || 'Kế hoạch chiến dịch',
                subtitle: (plan.accountName || plan.senderName || 'Chưa rõ tài khoản') + (recipients.length ? (' · ' + recipients.length + ' người nhận') : ''),
                time: planTime(plan),
                status: planStatus(plan)
            });
        });
        return items.sort(function (a, b) { return b.time - a.time; }).slice(0, 8);
    }

    function renderActivity() {
        var root = document.getElementById('dashboardActivity');
        if (!root) return;
        var items = activityItems();
        if (!items.length) {
            root.innerHTML = '<div class="dashboard-empty"><svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 10h18"/></svg><b>Chưa có hoạt động gần đây</b><p>Tạo chiến dịch đầu tiên để theo dõi tiến độ tại đây.</p><a class="btn btn-primary" href="/marketing/group">Tạo chiến dịch</a></div>';
            return;
        }
        root.innerHTML = items.map(function (item) {
            var status = statusMeta(item.status);
            return '<a class="dashboard-activity-item" href="/marketing/schedules">' +
                '<span class="dashboard-activity-dot ' + status.cls + '"></span>' +
                '<span class="dashboard-activity-copy"><b>' + text(item.title) + '</b><small>' + text(item.subtitle) + '</small></span>' +
                '<span class="dashboard-activity-meta"><em class="dashboard-status-pill ' + status.cls + '">' + status.label + '</em><time>' + text(formatDate(item.time)) + '</time></span>' +
                '</a>';
        }).join('');
    }

    function attentionItems() {
        var items = [];
        if (!state.accounts.length) {
            items.push({ level: 'warning', title: 'Chưa có tài khoản Zalo', detail: 'Thêm tài khoản trước khi tạo hoặc chạy chiến dịch.', href: '/accounts', action: 'Thêm tài khoản' });
        } else {
            state.accounts.filter(function (item) { return !accountReady(item); }).slice(0, 3).forEach(function (account) {
                items.push({ level: 'warning', title: (account.name || 'Tài khoản') + ' chưa đủ dữ liệu', detail: 'Thiếu phiên đăng nhập hoặc chưa lấy đủ thông tin cần thiết.', href: '/accounts', action: 'Kiểm tra' });
            });
        }
        var failedCount = state.schedules.filter(function (item) { return failedStatus(item.status); }).length + state.plans.filter(function (item) { return failedStatus(planStatus(item)); }).length;
        if (failedCount) items.push({ level: 'danger', title: failedCount + ' chiến dịch có lỗi', detail: 'Mở lịch chạy để xem nguyên nhân và xử lý.', href: '/marketing/schedules', action: 'Xem lỗi' });
        var unread = number(state.messageStats.unread);
        if (unread) items.push({ level: 'info', title: unread + ' tin nhắn chưa đọc', detail: 'Kiểm tra hội thoại mới trong Hộp thư Beta.', href: '/messages', action: 'Mở hộp thư' });
        return items.slice(0, 5);
    }

    function renderAttention() {
        var root = document.getElementById('dashboardAttention');
        if (!root) return;
        var items = attentionItems();
        if (!items.length) {
            root.innerHTML = '<div class="dashboard-all-good"><span>✓</span><div><b>Mọi thứ đang ổn</b><p>Không có lỗi hoặc dữ liệu thiếu cần xử lý.</p></div></div>';
            return;
        }
        root.innerHTML = items.map(function (item) {
            return '<a class="dashboard-attention-item ' + item.level + '" href="' + item.href + '">' +
                '<span class="dashboard-attention-mark"></span>' +
                '<span class="dashboard-attention-copy"><b>' + text(item.title) + '</b><small>' + text(item.detail) + '</small></span>' +
                '<em>' + text(item.action) + '</em>' +
                '</a>';
        }).join('');
    }

    async function getJson(url) {
        var response = await fetch(url, { headers: { 'Accept': 'application/json' } });
        if (!response.ok) throw new Error('HTTP ' + response.status);
        return response.json();
    }

    async function reload() {
        var button = document.getElementById('dashboardRefreshBtn');
        var status = document.getElementById('dashboardStatus');
        if (button) button.disabled = true;
        if (status) status.textContent = 'Đang cập nhật dữ liệu tổng quan...';

        var results = await Promise.allSettled([
            getJson('/api/accounts'),
            getJson('/api/schedules'),
            getJson('/api/action-plans'),
            getJson('/api/messages/conversations')
        ]);

        if (results[0].status === 'fulfilled') state.accounts = safeArray(results[0].value.accounts);
        if (results[1].status === 'fulfilled') state.schedules = safeArray(results[1].value.schedules);
        if (results[2].status === 'fulfilled') state.plans = safeArray(results[2].value.plans);
        if (results[3].status === 'fulfilled') state.messageStats = results[3].value.stats || {};

        renderMetrics();
        renderActivity();
        renderAttention();

        var failedLoads = results.filter(function (result) { return result.status === 'rejected'; }).length;
        if (status) {
            status.textContent = failedLoads
                ? 'Một số nguồn dữ liệu chưa tải được. Bạn vẫn có thể tiếp tục sử dụng các chức năng khác.'
                : 'Đã cập nhật lúc ' + new Date().toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' });
        }
        if (button) button.disabled = false;
    }

    window.NexusDashboard = { reload: reload };
    document.addEventListener('DOMContentLoaded', reload);
}());
