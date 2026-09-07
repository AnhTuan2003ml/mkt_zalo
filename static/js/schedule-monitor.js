// ─── Lịch gửi (trang riêng) ────────────────────────────────────────────
// Chỉ để theo dõi: lịch nào đang chạy, lịch nào sắp chạy tiếp theo, và
// quản lý (sửa/chạy ngay/hủy/xóa) toàn bộ lịch đã tạo từ trang "Chiến dịch".
(function () {
    'use strict';

    var initialCalendarDate = new Date();
    var state = {
        schedules: [],
        plans: [],
        accounts: [],
        all: [],
        calendarYear: initialCalendarDate.getFullYear(),
        calendarMonth: initialCalendarDate.getMonth(),
        selectedDateKey: ''
    };
    var REFRESH_MS = 15000;
    var refreshTimer = null;

    // ─── Helpers ────────────────────────────────────────────────────────
    function escHtml(v) {
        return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
            return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' })[c];
        });
    }

    function fetchJson(url) {
        return fetch(url).then(function (r) { return r.json(); }).catch(function (e) { return { __error: e.message || String(e) }; });
    }

    function parseTime(v) {
        if (v == null || v === '') return 0;
        if (typeof v === 'number') return v < 100000000000 ? v * 1000 : v;
        var n = Number(v);
        if (Number.isFinite(n) && n > 0) return n < 100000000000 ? n * 1000 : n;
        var p = Date.parse(String(v));
        return Number.isNaN(p) ? 0 : p;
    }

    function formatDateTime(v) {
        var ts = parseTime(v);
        if (!ts) return '-';
        return new Date(ts).toLocaleString('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
    }

    function formatDate(v) {
        var ts = parseTime(v);
        if (!ts) return '-';
        return new Date(ts).toLocaleDateString('vi-VN');
    }

    function dateFromKey(key) {
        var parts = String(key || '').split('-').map(Number);
        if (parts.length !== 3 || !parts[0] || !parts[1] || !parts[2]) return null;
        return new Date(parts[0], parts[1] - 1, parts[2], 12, 0, 0, 0);
    }

    function formatDateKey(key, includeWeekday) {
        var d = dateFromKey(key);
        if (!d) return 'Tất cả thời gian';
        return d.toLocaleDateString('vi-VN', includeWeekday ? {
            weekday: 'long', day: '2-digit', month: '2-digit', year: 'numeric'
        } : {
            day: '2-digit', month: '2-digit', year: 'numeric'
        });
    }

    function itemDateTimes(item) {
        var values = [];
        function add(v) {
            var ts = parseTime(v);
            if (ts && values.indexOf(ts) === -1) values.push(ts);
        }
        if (!item) return values;
        if (item._itemType === 'schedule') {
            add(item.runAt);
            return values;
        }
        add(item.startDate);
        add(item._nextTime);
        getPlanBatches(item).forEach(function (batch) {
            add(batch.date || batch.runAt || batch.scheduledAt);
        });
        return values;
    }

    function itemOccursOnDate(item, key) {
        if (!key) return true;
        return itemDateTimes(item).some(function (ts) { return localDayKey(ts) === key; });
    }

    function selectedDateSortTime(item, key) {
        var matches = itemDateTimes(item).filter(function (ts) { return localDayKey(ts) === key; });
        if (!matches.length) return item._sortTime || 0;
        return Math.min.apply(Math, matches);
    }

    function statusLabel(status) {
        var labels = {
            pending: 'Chờ', planned: 'Đã lên kế hoạch', running: 'Đang chạy', done: 'Hoàn thành', completed: 'Hoàn thành',
            partial: 'Một phần', failed: 'Thất bại', cancelled: 'Đã hủy', canceled: 'Đã hủy', skipped: 'Đã bỏ qua',
            pending_manual: 'Chờ xác nhận'
        };
        return labels[status] || status || 'Chờ';
    }

    function badgeClass(status) {
        if (status === 'planned' || status === 'pending_manual') return 'pending';
        if (status === 'completed') return 'done';
        if (status === 'canceled' || status === 'skipped') return 'cancelled';
        return status || 'pending';
    }

    function actionPlanStatus(plan) {
        var p = plan.progress || {};
        return p.displayStatus || plan.status || 'planned';
    }

    // Nhãn loại chiến dịch trên thẻ: khớp với hành vi gửi thật sự, không dùng
    // nhãn "Tin nhắn" chung chung nữa vì gây nhầm giữa 2 cách gửi khác nhau.
    function kindInfo(item) {
        if (item._itemType === 'action_plan') {
            var cls = item.planType === 'group_invite' ? 'group_invite' : 'friend';
            var label = item.planTypeLabel || (item.planType === 'group_invite' ? 'Mời vào nhóm' : 'Gửi kết bạn');
            return { label: label, cls: cls };
        }
        var src = item.source;
        if (src === 'personal-groups') return { label: 'Gửi vào nhóm', cls: 'kind-ingroup' };
        if (src === 'group') return { label: 'Gửi riêng cho thành viên nhóm', cls: 'kind-permember' };
        if (src === 'phone') return { label: 'Gửi theo số điện thoại', cls: 'kind-phone' };
        return { label: 'Tin nhắn', cls: '' };
    }

    function accountName(item) {
        var name = item.accountName || item.senderName || (item.groupInfo && item.groupInfo.accountName) || '';
        if (!name && item.accountId) {
            var acc = (state.accounts || []).find(function (a) {
                return String(a.accountId || a.id || a.account_id || '') === String(item.accountId);
            });
            if (acc) name = acc.name || acc.displayName || acc.zaloName || acc.phone || item.accountId;
        }
        return name || 'N/A';
    }

    function countdownInfo(ts) {
        if (!ts) return { text: '-', cls: '' };
        var diff = ts - Date.now();
        var overdue = diff < 0;
        var abs = Math.abs(diff);
        var mins = Math.floor(abs / 60000);
        var hrs = Math.floor(mins / 60);
        var days = Math.floor(hrs / 24);
        var text;
        if (days > 0) text = days + ' ngày ' + (hrs % 24) + ' giờ';
        else if (hrs > 0) text = hrs + ' giờ ' + (mins % 60) + ' phút';
        else if (mins > 0) text = mins + ' phút';
        else text = '< 1 phút';
        var cls = '';
        if (overdue) { text = 'Quá giờ ' + text; cls = 'overdue'; }
        else { text = 'Còn ' + text; if (abs <= 30 * 60000) cls = 'soon'; }
        return { text: text, cls: cls };
    }

    // ─── Chuẩn hóa dữ liệu ─────────────────────────────────────────────
    function getPlanBatches(plan) { return (plan && plan.batches) || []; }

    function nextBatchTime(plan) {
        var batches = getPlanBatches(plan);
        var pendingTimes = batches
            .filter(function (b) { return ['done', 'failed', 'cancelled', 'skipped'].indexOf(b.status) === -1; })
            .map(function (b) { return parseTime(b.date || b.runAt || b.scheduledAt); })
            .filter(Boolean)
            .sort(function (a, b) { return a - b; });
        if (pendingTimes.length) return pendingTimes[0];
        return parseTime(plan.startDate) || 0;
    }

    function prepareSchedule(sch) {
        sch = sch || {};
        sch._itemType = 'schedule';
        sch._sortTime = parseTime(sch.runAt) || parseTime(sch.createdAt) || 0;
        sch._nextTime = parseTime(sch.runAt);
        return sch;
    }

    function prepareActionPlan(plan) {
        plan = plan || {};
        var t = plan.planType || plan.type || 'friend';
        if (t === 'group' || t === 'invite_group') t = 'group_invite';
        plan._itemType = 'action_plan';
        plan.planType = t;
        plan.planTypeLabel = plan.planTypeLabel || (t === 'group_invite' ? 'Mời vào nhóm' : 'Gửi kết bạn');
        plan._sortTime = parseTime(plan.createdAt) || parseTime(plan.startDate) || 0;
        plan._nextTime = nextBatchTime(plan);
        return plan;
    }

    function isRunning(item) {
        if (item._itemType === 'schedule') return item.status === 'running';
        if (actionPlanStatus(item) === 'running') return true;
        return getPlanBatches(item).some(function (b) { return b.status === 'running'; });
    }

    function isPendingLike(item) {
        if (item._itemType === 'schedule') return item.status === 'pending';
        var st = actionPlanStatus(item);
        return ['planned', 'pending', 'pending_manual'].indexOf(st) !== -1;
    }

    function isDoneToday(item) {
        var todayStart = new Date(); todayStart.setHours(0, 0, 0, 0);
        var todayTs = todayStart.getTime();
        if (item._itemType === 'schedule') {
            if (['done', 'completed', 'partial'].indexOf(item.status) === -1) return false;
            return parseTime(item.updatedAt || item.runAt || item.createdAt) >= todayTs;
        }
        var st = actionPlanStatus(item);
        if (st !== 'completed' && st !== 'done') return false;
        return parseTime(item.updatedAt || item.createdAt) >= todayTs;
    }

    function isError(item) {
        if (item._itemType === 'schedule') return item.status === 'failed';
        return actionPlanStatus(item) === 'failed';
    }

    // ─── Thống kê ────────────────────────────────────────────────────────
    function renderStats() {
        var running = state.all.filter(isRunning);
        var upcoming = state.all.filter(function (item) {
            return !isRunning(item) && isPendingLike(item) && item._nextTime > 0 && item._nextTime <= Date.now() + 24 * 3600000;
        });
        var allUpcoming = state.all.filter(function (item) {
            return !isRunning(item) && isPendingLike(item) && item._nextTime > 0;
        });
        var doneToday = state.all.filter(isDoneToday);
        var errorItems = state.all.filter(isError);

        setText('statRunning', running.length);
        setText('statUpcoming', upcoming.length);
        setText('statDoneToday', doneToday.length);
        setText('statError', errorItems.length);
        setText('runningCountPill', running.length);
        setText('upcomingCountPill', allUpcoming.length);

        var dot = document.getElementById('schedmonLiveDot');
        if (dot) dot.classList.toggle('idle', running.length === 0);
    }


    function setText(id, value) {
        var el = document.getElementById(id);
        if (el) el.textContent = String(value);
    }

    // ─── "Đang chạy" ─────────────────────────────────────────────────────
    function buildRunningCard(item) {
        var kind = kindInfo(item);
        var card = document.createElement('div');
        card.className = 'schedmon-running-card';

        var title = item._itemType === 'schedule' ? (item.title || 'Không có tiêu đề') : (item.name || kind.label);
        var account = accountName(item);
        var runTime = item._itemType === 'schedule' ? formatDateTime(item.runAt) : formatDateTime(item._nextTime || item.startDate);
        var percent = 0;
        var processed = 0;
        var total = 0;
        var success = 0;
        var failed = 0;
        var actionHtml = '';

        if (item._itemType === 'schedule') {
            var recipients = item.recipients || [];
            var results = item.results || [];
            success = results.filter(function (r) { return r.status === 'success'; }).length;
            failed = results.filter(function (r) { return r.status === 'failed'; }).length;
            total = recipients.length;
            processed = success + failed;
            percent = total ? Math.round((processed / total) * 100) : 0;
            actionHtml =
                '<button class="btn btn-ghost btn-sm" onclick="SchedMon.showDetail(\'' + item.scheduleId + '\')">Chi tiết</button>' +
                '<button class="btn btn-warning btn-sm" onclick="SchedMon.cancelSchedule(\'' + item.scheduleId + '\')">Hủy</button>';
        } else {
            var pg = item.progress || {};
            percent = Math.max(0, Math.min(100, Number(pg.percent || 0)));
            total = Number(pg.totalMembers || item.totalMembers || 0);
            processed = Number(pg.processedMembers || 0);
            success = Number(pg.doneMembers || processed || 0);
            failed = Number(pg.failedMembers || 0);
            actionHtml = '<button class="btn btn-ghost btn-sm" onclick=\'SchedMon.showActionPlanDetail(' + jsArg(item.planType) + ',' + jsArg(item.id || '') + ')\'>Chi tiết</button>';
        }

        card.innerHTML =
            '<div class="schedmon-running-main">' +
                '<div class="schedmon-running-kicker"><span class="schedmon-run-dot"></span><span class="schedule-kind-badge ' + kind.cls + '">' + escHtml(kind.label) + '</span></div>' +
                '<div class="schedmon-running-title" title="' + escHtml(title) + '">' + escHtml(title) + '</div>' +
                '<div class="schedmon-running-sub">' + escHtml(account) + '</div>' +
            '</div>' +
            '<div class="schedmon-running-metric"><span>Thời gian chạy</span><strong>' + escHtml(runTime) + '</strong></div>' +
            '<div class="schedmon-running-progress">' +
                '<div class="schedule-progress-line"><div class="schedule-progress-bar" style="width:' + percent + '%"></div></div>' +
                '<div class="schedule-progress-text"><span>Tiến độ ' + percent + '%</span><span>' + processed + '/' + total + '</span></div>' +
            '</div>' +
            '<div class="schedmon-running-metric is-speed"><span>Kết quả</span><strong>' + success + ' thành công · ' + failed + ' lỗi</strong></div>' +
            '<div class="schedmon-running-actions">' + actionHtml + '</div>';
        return card;
    }


    function renderRunning() {
        var root = document.getElementById('runningList');
        if (!root) return;
        var running = state.all.filter(isRunning);
        if (!running.length) {
            root.innerHTML = '<div class="schedmon-empty"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/></svg><b>Không có lịch nào đang chạy</b><p>Lịch sẽ hiện ở đây ngay khi bắt đầu xử lý.</p></div>';
            return;
        }
        root.innerHTML = '';
        running.sort(function (a, b) { return (b._sortTime || 0) - (a._sortTime || 0); }).forEach(function (item) {
            root.appendChild(buildRunningCard(item));
        });
    }

    // ─── "Sắp chạy tiếp theo" ────────────────────────────────────────────
    function renderUpcoming() {
        var root = document.getElementById('upcomingList');
        if (!root) return;
        var upcoming = state.all
            .filter(function (item) { return !isRunning(item) && isPendingLike(item) && item._nextTime > 0; })
            .sort(function (a, b) { return a._nextTime - b._nextTime; })
            .slice(0, 5);

        setText('upcomingCountPill', state.all.filter(function (item) {
            return !isRunning(item) && isPendingLike(item) && item._nextTime > 0;
        }).length);

        if (!upcoming.length) {
            root.innerHTML = '<div class="schedmon-empty"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M4 12h16M14 6l6 6-6 6"/></svg><b>Chưa có lịch nào sắp chạy</b><p>Tạo chiến dịch mới để lịch tiếp theo xuất hiện tại đây.</p></div>';
            return;
        }

        root.innerHTML = upcoming.map(function (item) {
            var kind = kindInfo(item);
            var cd = countdownInfo(item._nextTime);
            var title = item._itemType === 'schedule' ? (item.title || 'Không có tiêu đề') : (item.name || kind.label);
            var id = item._itemType === 'schedule' ? item.scheduleId : item.id;
            var total = item._itemType === 'schedule'
                ? (item.recipients || []).length
                : Number(((item.progress || {}).totalMembers) || item.totalMembers || 0);
            var actions = item._itemType === 'schedule'
                ? '<button class="btn btn-success btn-sm" onclick="SchedMon.runNow(\'' + id + '\')">Chạy ngay</button>' +
                  '<button class="btn btn-ghost btn-sm" onclick="SchedMon.showDetail(\'' + id + '\')">Chi tiết</button>'
                : '<button class="btn btn-ghost btn-sm" onclick=\'SchedMon.showActionPlanDetail(' + jsArg(item.planType) + ',' + jsArg(id || '') + ')\'>Chi tiết</button>';

            return '<div class="schedmon-upcoming-item">' +
                '<span class="schedmon-countdown-chip ' + cd.cls + '">' + escHtml(cd.text) + '</span>' +
                '<div class="schedmon-upcoming-copy">' +
                    '<div class="schedmon-up-title" title="' + escHtml(title) + '">' + escHtml(title) + '</div>' +
                    '<div class="schedmon-up-meta"><span class="schedule-kind-badge ' + kind.cls + '">' + escHtml(kind.label) + '</span><span>' + escHtml(accountName(item)) + '</span></div>' +
                '</div>' +
                '<div class="schedmon-upcoming-detail"><span>Thời gian</span><strong>' + escHtml(formatDateTime(item._nextTime)) + '</strong></div>' +
                '<div class="schedmon-upcoming-detail is-estimate"><span>Quy mô</span><strong>' + total + ' người nhận</strong></div>' +
                '<div class="schedmon-upcoming-actions">' + actions + '</div>' +
            '</div>';
        }).join('');
    }


    // ─── "Tất cả lịch" ───────────────────────────────────────────────────
    function jsArg(v) { return JSON.stringify(String(v == null ? '' : v)); }

    function buildScheduleCard(sch) {
        var kind = kindInfo(sch);
        var st = sch.status || 'pending';
        var total = (sch.recipients || []).length;
        var ok = (sch.results || []).filter(function (r) { return r.status === 'success'; }).length;
        var fail = (sch.results || []).filter(function (r) { return r.status === 'failed'; }).length;
        var pend = Math.max(total - ok - fail, 0);
        var processed = ok + fail;
        var percent = total ? Math.round((processed / total) * 100) : 0;

        var row = document.createElement('div');
        row.className = 'schedule-table-row';
        row.innerHTML =
            '<div class="schedule-table-cell">' +
                '<div class="schedule-table-title" title="' + escHtml(sch.title || 'Không có tiêu đề') + '">' + escHtml(sch.title || 'Không có tiêu đề') + '</div>' +
                '<div class="schedule-table-sub">' + escHtml((sch.message || 'Không có mô tả').slice(0, 90)) + '</div>' +
            '</div>' +
            '<div class="schedule-table-cell schedule-table-source"><span class="schedule-table-source-icon">Z</span><div><div class="schedule-table-value">' + escHtml(kind.label) + '</div><div class="schedule-table-sub">' + escHtml(accountName(sch)) + '</div></div></div>' +
            '<div class="schedule-table-cell"><div class="schedule-table-value">' + escHtml(formatDateTime(sch.runAt)) + '</div></div>' +
            '<div class="schedule-table-cell"><span class="schedule-status-badge ' + badgeClass(st) + '">' + escHtml(statusLabel(st)) + '</span></div>' +
            '<div class="schedule-table-cell schedule-table-result"><strong>' + ok + '/' + total + ' (' + percent + '%)</strong><span>' + pend + ' chờ · ' + fail + ' lỗi</span></div>' +
            '<div class="schedule-table-cell schedule-table-actions">' +
                '<button class="schedule-icon-btn" title="Xem chi tiết" onclick="SchedMon.showDetail(\'' + sch.scheduleId + '\')">' + iconSvg('eye') + '</button>' +
                '<button class="schedule-icon-btn" title="Sửa lịch" onclick="SchedMon.showEditModal(\'' + sch.scheduleId + '\')">' + iconSvg('edit') + '</button>' +
                (st === 'pending' ? '<button class="schedule-icon-btn" title="Chạy ngay" onclick="SchedMon.runNow(\'' + sch.scheduleId + '\')">' + iconSvg('play') + '</button>' : '') +
                (st !== 'running' ? '<button class="schedule-icon-btn is-danger" title="Xóa" onclick="SchedMon.deleteSchedule(\'' + sch.scheduleId + '\')">' + iconSvg('trash') + '</button>' : '<button class="schedule-icon-btn is-danger" title="Hủy" onclick="SchedMon.cancelSchedule(\'' + sch.scheduleId + '\')">' + iconSvg('stop') + '</button>') +
            '</div>';
        return row;
    }


    function buildActionPlanCard(plan) {
        var st = actionPlanStatus(plan);
        var pg = plan.progress || {};
        var kind = kindInfo(plan);
        var total = Number(pg.totalMembers || plan.totalMembers || 0);
        var processed = Number(pg.processedMembers || 0);
        var failed = Number(pg.failedMembers || 0);
        var success = Math.max(processed - failed, 0);
        var pending = Number(pg.pendingMembers || Math.max(total - processed, 0));
        var percent = Math.max(0, Math.min(100, Number(pg.percent || (total ? Math.round(processed * 100 / total) : 0))));
        var title = plan.name || kind.label;

        var row = document.createElement('div');
        row.className = 'schedule-table-row';
        row.innerHTML =
            '<div class="schedule-table-cell">' +
                '<div class="schedule-table-title" title="' + escHtml(title) + '">' + escHtml(title) + '</div>' +
                '<div class="schedule-table-sub">' + escHtml((plan.message || plan.planTypeLabel || 'Kế hoạch tự động').slice(0, 90)) + '</div>' +
            '</div>' +
            '<div class="schedule-table-cell schedule-table-source"><span class="schedule-table-source-icon">N</span><div><div class="schedule-table-value">' + escHtml(kind.label) + '</div><div class="schedule-table-sub">' + escHtml(plan.accountName || plan.accountId || 'N/A') + '</div></div></div>' +
            '<div class="schedule-table-cell"><div class="schedule-table-value">' + escHtml(formatDateTime(plan._nextTime || plan.startDate)) + '</div></div>' +
            '<div class="schedule-table-cell"><span class="schedule-status-badge ' + badgeClass(st) + '">' + escHtml(statusLabel(st)) + '</span></div>' +
            '<div class="schedule-table-cell schedule-table-result"><strong>' + processed + '/' + total + ' (' + percent + '%)</strong><span>' + pending + ' chờ · ' + failed + ' lỗi</span></div>' +
            '<div class="schedule-table-cell schedule-table-actions">' +
                '<button class="schedule-icon-btn" title="Chi tiết và cập nhật" onclick=\'SchedMon.showActionPlanDetail(' + jsArg(plan.planType) + ',' + jsArg(plan.id || '') + ')\'>' + iconSvg('eye') + '</button>' +
            '</div>';
        return row;
    }


    function iconSvg(name) {
        var icons = {
            eye: '<svg viewBox="0 0 24 24"><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z"/><circle cx="12" cy="12" r="2.5"/></svg>',
            edit: '<svg viewBox="0 0 24 24"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L8 18l-4 1 1-4Z"/></svg>',
            play: '<svg viewBox="0 0 24 24"><path d="m8 5 11 7-11 7Z"/></svg>',
            trash: '<svg viewBox="0 0 24 24"><path d="M3 6h18M8 6V4h8v2M6 6l1 15h10l1-15M10 10v7M14 10v7"/></svg>',
            stop: '<svg viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>'
        };
        return icons[name] || '';
    }

    function getItemTotals(item) {
        if (item._itemType === 'schedule') {
            var recipients = item.recipients || [];
            var results = item.results || [];
            var success = results.filter(function (r) { return r.status === 'success'; }).length;
            var failed = results.filter(function (r) { return r.status === 'failed'; }).length;
            return { total: recipients.length, success: success, failed: failed, processed: success + failed };
        }
        var pg = item.progress || {};
        var total = Number(pg.totalMembers || item.totalMembers || 0);
        var processed = Number(pg.processedMembers || 0);
        var failed = Number(pg.failedMembers || 0);
        return { total: total, success: Math.max(processed - failed, 0), failed: failed, processed: processed };
    }

    function renderPerformance() {
        var totals = state.all.reduce(function (acc, item) {
            var t = getItemTotals(item);
            acc.total += t.total;
            acc.success += t.success;
            acc.failed += t.failed;
            acc.processed += t.processed;
            return acc;
        }, { total: 0, success: 0, failed: 0, processed: 0 });
        var pending = Math.max(totals.total - totals.processed, 0);
        var rate = totals.processed ? Math.round(totals.success * 1000 / totals.processed) / 10 : 0;
        setText('schedmonProcessedTotal', totals.processed);
        setText('schedmonProcessedCaption', 'Trên ' + totals.total + ' người nhận');
        setText('schedmonSuccessRate', rate + '%');
        setText('schedmonSuccessTotal', totals.success);
        setText('schedmonFailedTotal', totals.failed);
        setText('schedmonPendingTotal', pending);
        var ring = document.getElementById('schedmonRateRing');
        if (ring) ring.style.setProperty('--rate', Math.max(0, Math.min(100, rate)));
    }

    function renderAlerts() {
        var root = document.getElementById('schedmonAlertList');
        if (!root) return;
        var alerts = [];
        state.all.forEach(function (item) {
            var title = item._itemType === 'schedule' ? (item.title || 'Lịch gửi') : (item.name || item.planTypeLabel || 'Kế hoạch');
            if (isError(item)) {
                alerts.push({ type: 'error', title: title + ' đang có lỗi', detail: 'Mở chi tiết để kiểm tra nguyên nhân và xử lý.' });
                return;
            }
            if (!isRunning(item) && isPendingLike(item) && item._nextTime > 0 && item._nextTime <= Date.now() + 30 * 60000) {
                alerts.push({ type: 'warning', title: title + ' sắp chạy', detail: countdownInfo(item._nextTime).text + ' · Hãy kiểm tra nội dung và tài khoản gửi.' });
            }
        });
        alerts = alerts.slice(0, 4);
        setText('schedmonAlertCount', alerts.length);
        if (!alerts.length) {
            root.innerHTML = '<div class="schedmon-alert-empty">Không có cảnh báo cần xử lý.</div>';
            return;
        }
        root.innerHTML = alerts.map(function (a) {
            return '<div class="schedmon-alert-item ' + (a.type === 'error' ? 'is-error' : '') + '">' +
                '<span class="schedmon-alert-icon">' + (a.type === 'error' ? '!' : 'A') + '</span>' +
                '<div class="schedmon-alert-copy"><strong>' + escHtml(a.title) + '</strong><span>' + escHtml(a.detail) + '</span></div>' +
            '</div>';
        }).join('');
    }

    function localDayKey(ts) {
        var d = new Date(ts);
        return [d.getFullYear(), String(d.getMonth() + 1).padStart(2, '0'), String(d.getDate()).padStart(2, '0')].join('-');
    }

    function updateDateFilterUI() {
        var hasDate = Boolean(state.selectedDateKey);
        var shortLabel = formatDateKey(state.selectedDateKey, false);
        var longLabel = formatDateKey(state.selectedDateKey, true);
        var chip = document.getElementById('schedmonDateFilterClear');
        var chipLabel = document.getElementById('schedmonDateFilterLabel');
        var selectionLabel = document.getElementById('schedmonCalendarSelectionLabel');
        var clearBtn = document.getElementById('schedmonCalendarClearBtn');

        if (chip) chip.hidden = !hasDate;
        if (chipLabel) chipLabel.textContent = shortLabel;
        if (selectionLabel) selectionLabel.textContent = hasDate ? longLabel : 'Tất cả thời gian';
        if (clearBtn) clearBtn.hidden = !hasDate;
    }

    function selectCalendarDate(key, options) {
        options = options || {};
        var selected = dateFromKey(key);
        if (!selected) return;
        state.selectedDateKey = key;
        state.calendarYear = selected.getFullYear();
        state.calendarMonth = selected.getMonth();
        renderCalendar();
        updateDateFilterUI();
        filterAll();

        if (options.notify !== false) {
            showNotif('Đã chọn ngày', 'Đang hiển thị lịch hoạt động ngày ' + formatDateKey(key, false) + '.', 'info');
        }
        if (options.keepOverlay !== true) closeDataOverlay('calendar');
        if (options.scroll !== false) {
            var table = document.querySelector('.schedmon-table-shell');
            if (table && typeof table.focus === 'function') table.focus({ preventScroll: true });
        }
    }

    function clearDateFilter(options) {
        options = options || {};
        if (!state.selectedDateKey && !options.force) return;
        state.selectedDateKey = '';
        renderCalendar();
        updateDateFilterUI();
        filterAll();
        if (options.notify !== false) showNotif('Đã bỏ lọc ngày', 'Đang hiển thị toàn bộ lịch hoạt động.', 'info');
    }

    function changeCalendarMonth(delta) {
        var view = new Date(state.calendarYear, state.calendarMonth + delta, 1, 12, 0, 0, 0);
        state.calendarYear = view.getFullYear();
        state.calendarMonth = view.getMonth();
        renderCalendar();
    }

    function renderCalendar() {
        var root = document.getElementById('schedmonCalendarGrid');
        if (!root) return;
        var now = new Date();
        var year = state.calendarYear;
        var month = state.calendarMonth;
        setText('schedmonCalendarMonth', 'Tháng ' + (month + 1) + ', ' + year);

        var eventDays = {};
        state.all.forEach(function (item) {
            itemDateTimes(item).forEach(function (ts) {
                var key = localDayKey(ts);
                eventDays[key] = (eventDays[key] || 0) + 1;
            });
        });

        var first = new Date(year, month, 1, 12, 0, 0, 0);
        var startOffset = (first.getDay() + 6) % 7;
        var start = new Date(year, month, 1 - startOffset, 12, 0, 0, 0);
        var todayKey = localDayKey(now.getTime());
        var html = '';
        for (var i = 0; i < 42; i += 1) {
            var d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i, 12, 0, 0, 0);
            var key = localDayKey(d.getTime());
            var eventCount = eventDays[key] || 0;
            var classes = ['schedmon-calendar-day'];
            if (d.getMonth() !== month) classes.push('is-muted');
            if (key === todayKey) classes.push('is-today');
            if (key === state.selectedDateKey) classes.push('is-selected');
            if (eventCount) classes.push('has-event');
            var title = d.toLocaleDateString('vi-VN') + (eventCount ? ' · ' + eventCount + ' lịch hoạt động' : ' · Không có lịch');
            html += '<button type="button" class="' + classes.join(' ') + '" data-date-key="' + key + '" aria-label="' + escHtml(title) + '" aria-pressed="' + (key === state.selectedDateKey ? 'true' : 'false') + '" title="' + escHtml(title) + '"><span>' + d.getDate() + '</span></button>';
        }
        root.innerHTML = html;
        root.querySelectorAll('[data-date-key]').forEach(function (button) {
            button.addEventListener('click', function () {
                selectCalendarDate(button.getAttribute('data-date-key'));
            });
        });
        updateDateFilterUI();
    }

    function renderSupportWidgets() {
        renderCalendar();
        renderAlerts();
        renderPerformance();
    }

    function renderAll(items) {
        var root = document.getElementById('allList');
        if (!root) return;
        if (!items.length) {
            var emptyTitle = state.selectedDateKey ? ('Không có lịch ngày ' + formatDateKey(state.selectedDateKey, false)) : 'Chưa có lịch nào';
            var emptyText = state.selectedDateKey ? 'Chọn ngày khác trên lịch tháng hoặc bấm Bỏ lọc để xem toàn bộ.' : 'Tạo chiến dịch đầu tiên để theo dõi tại đây.';
            root.innerHTML = '<div class="schedmon-empty"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg><b>' + escHtml(emptyTitle) + '</b><p>' + escHtml(emptyText) + '</p></div>';
            setText('schedListCount', '0 lịch');
            return;
        }
        root.innerHTML = '';
        items.forEach(function (item) {
            root.appendChild(item._itemType === 'action_plan' ? buildActionPlanCard(item) : buildScheduleCard(item));
        });
        setText('schedListCount', items.length + ' lịch');
    }

    function filterAll() {
        var q = ((document.getElementById('schedListSearch') || {}).value || '').toLowerCase();
        var sf = (document.getElementById('schedListStatusFilter') || {}).value || '';
        var tf = (document.getElementById('schedListTypeFilter') || {}).value || '';
        var dateKey = state.selectedDateKey || '';
        var filtered = state.all.filter(function (s) {
            var isPlan = s._itemType === 'action_plan';
            var type = isPlan ? s.planType : 'schedule';
            var status = isPlan ? actionPlanStatus(s) : (s.status || 'pending');
            var searchText = [s.title, s.name, s.message, s.accountName, s.accountId, s.planTypeLabel].join(' ').toLowerCase();
            if (q && searchText.indexOf(q) === -1) return false;
            if (tf && type !== tf) return false;
            if (sf && status !== sf && !(sf === 'done' && status === 'completed') && !(sf === 'pending' && (status === 'planned' || status === 'pending_manual'))) return false;
            if (dateKey && !itemOccursOnDate(s, dateKey)) return false;
            return true;
        }).sort(function (a, b) {
            if (dateKey) return selectedDateSortTime(a, dateKey) - selectedDateSortTime(b, dateKey);
            return (b._sortTime || 0) - (a._sortTime || 0);
        });
        renderAll(filtered);
        if (q || sf || tf || dateKey) setText('schedListCount', filtered.length + '/' + state.all.length + ' lịch');
        updateDateFilterUI();
    }

    // ─── Chi tiết / sửa / hành động ───────────────────────────────────────
    function findActionPlan(planType, planId) {
        return state.all.find(function (x) { return x._itemType === 'action_plan' && String(x.id || '') === String(planId) && String(x.planType || '') === String(planType); });
    }

    function showActionPlanDetail(planType, planId) {
        var plan = findActionPlan(planType, planId);
        if (plan) { renderActionPlanDetail(plan); return; }
        fetchJson('/api/action-plans?type=' + encodeURIComponent(planType)).then(function (j) {
            var p = (j.plans || []).map(prepareActionPlan).find(function (x) { return String(x.id || '') === String(planId); });
            if (!p) { showNotif('Lỗi', 'Không tìm thấy kế hoạch', 'error'); return; }
            renderActionPlanDetail(p);
        });
    }

    function renderActionPlanDetail(plan) {
        plan = prepareActionPlan(plan);
        var kind = kindInfo(plan);
        var st = actionPlanStatus(plan);
        var pg = plan.progress || {};
        var groups = (plan.targetGroups || []).map(function (g) { return g.name || g.groupName || g.groupId; }).filter(Boolean).join(', ');
        if (!groups && (plan.targetGroupIds || []).length) groups = (plan.targetGroupIds || []).join(', ');
        var html = '';
        html += '<div class="detail-item"><div class="detail-label">Mã kế hoạch:</div><div class="detail-value" style="font-family:monospace;word-break:break-all;">' + escHtml(plan.id || '-') + '</div></div>';
        html += '<div class="detail-item"><div class="detail-label">Loại lịch:</div><div class="detail-value">' + escHtml(kind.label) + '</div></div>';
        html += '<div class="detail-item"><div class="detail-label">Tên kế hoạch:</div><div class="detail-value">' + escHtml(plan.name || '-') + '</div></div>';
        html += '<div class="detail-item"><div class="detail-label">Tài khoản:</div><div class="detail-value">' + escHtml(plan.accountName || plan.accountId || '-') + '</div></div>';
        html += '<div class="detail-item"><div class="detail-label">Ngày bắt đầu:</div><div class="detail-value">' + escHtml(formatDate(plan.startDate)) + '</div></div>';
        html += '<div class="detail-item"><div class="detail-label">Số người/ngày:</div><div class="detail-value">' + escHtml(String(plan.dailyLimit || 1)) + '</div></div>';
        html += '<div class="detail-item"><div class="detail-label">Trạng thái:</div><div class="detail-value">' + escHtml(statusLabel(st)) + '</div></div>';
        if (plan.planType === 'group_invite') html += '<div class="detail-item"><div class="detail-label">Nhóm đích:</div><div class="detail-value">' + escHtml(groups || '-') + '</div></div>';
        if (plan.planType === 'friend' && plan.afterAction && plan.afterAction !== 'none') html += '<div class="detail-item"><div class="detail-label">Sau khi kết bạn:</div><div class="detail-value">' + escHtml(plan.afterAction === 'invite_existing_group' ? ('Mời vào nhóm: ' + (groups || '-')) : ('Tạo nhóm mới: ' + (plan.newGroupName || '-'))) + '</div></div>';
        if (plan.message) html += '<div class="detail-item"><div class="detail-label">Lời mời:</div><div class="detail-value" style="white-space:pre-wrap;">' + escHtml(plan.message) + '</div></div>';

        html += '<hr style="border:0;border-top:1px solid var(--border);margin:12px 0;">';
        html += '<div class="detail-item"><div class="detail-label">Tiến độ:</div><div class="detail-value">' + (pg.processedMembers || 0) + '/' + (pg.totalMembers || plan.totalMembers || 0) + ' người, ' + (pg.percent || 0) + '%</div></div>';
        html += '<div class="detail-item"><div class="detail-label">Batch:</div><div class="detail-value">' + (pg.doneBatches || 0) + '/' + (pg.totalBatches || getPlanBatches(plan).length) + ' xong, ' + (pg.pendingBatches || 0) + ' chờ, ' + (pg.failedBatches || 0) + ' lỗi</div></div>';

        html += '<hr style="border:0;border-top:1px solid var(--border);margin:12px 0;">';
        html += '<div style="font-weight:700;margin-bottom:8px;">Danh sách ngày/batch</div>';
        html += '<div class="action-plan-batches">';
        getPlanBatches(plan).forEach(function (b) {
            var bst = b.status || 'pending_manual';
            var members = b.members || [];
            var memberNames = members.slice(0, 10).map(function (m) { return m.name || m.zaloName || m.userId || m.uid || '-'; });
            var more = members.length > 10 ? ' ... +' + (members.length - 10) + ' người khác' : '';
            html += '<div class="action-plan-batch">';
            html += '<div class="action-plan-batch-head"><div><div class="action-plan-batch-title">Ngày ' + escHtml(String(b.day || '-')) + ' · ' + escHtml(formatDate(b.date)) + '</div><div class="action-plan-batch-meta">' + escHtml(String(b.count || members.length || 0)) + ' người · ' + escHtml(statusLabel(bst)) + '</div></div><span class="schedule-status-badge ' + badgeClass(bst) + '">' + escHtml(statusLabel(bst)) + '</span></div>';
            html += '<div class="action-plan-batch-actions">';
            html += '<button class="btn btn-primary btn-sm" onclick=\'SchedMon.updateActionPlanBatch(' + jsArg(plan.planType) + ',' + jsArg(plan.id || '') + ',' + jsArg(b.day || '') + ',"running")\'>Đang chạy</button>';
            html += '<button class="btn btn-success btn-sm" onclick=\'SchedMon.updateActionPlanBatch(' + jsArg(plan.planType) + ',' + jsArg(plan.id || '') + ',' + jsArg(b.day || '') + ',"done")\'>Xong</button>';
            html += '<button class="btn btn-danger btn-sm" onclick=\'SchedMon.updateActionPlanBatch(' + jsArg(plan.planType) + ',' + jsArg(plan.id || '') + ',' + jsArg(b.day || '') + ',"failed")\'>Lỗi</button>';
            html += '<button class="btn btn-ghost btn-sm" onclick=\'SchedMon.updateActionPlanBatch(' + jsArg(plan.planType) + ',' + jsArg(plan.id || '') + ',' + jsArg(b.day || '') + ',"pending_manual")\'>Chờ</button>';
            html += '</div>';
            html += '<div class="action-plan-mini-list">' + escHtml(memberNames.join(', ') + more) + '</div>';
            html += '</div>';
        });
        html += '</div>';

        setModal('Chi tiết lịch - ' + (plan.planTypeLabel || 'Kế hoạch'), html);
    }

    function updateActionPlanBatch(planType, planId, batchDay, status) {
        var endpoint = planType === 'group_invite' ? '/api/group-invite-plans/' : '/api/friend-request-plans/';
        fetch(endpoint + encodeURIComponent(planId) + '/batches/' + encodeURIComponent(batchDay), {
            method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: status })
        }).then(function (r) { return r.json(); }).then(function (j) {
            if (!j.success) { showNotif('Lỗi', j.error || 'Không cập nhật được batch', 'error'); return; }
            showNotif('OK', 'Đã cập nhật tiến độ batch', 'success');
            var updated = prepareActionPlan(j.plan || {});
            var idx = state.all.findIndex(function (x) { return x._itemType === 'action_plan' && String(x.id || '') === String(planId); });
            if (idx >= 0) state.all[idx] = updated;
            renderActionPlanDetail(updated);
            renderRunning(); renderUpcoming(); renderStats(); filterAll();
        }).catch(function (e) { showNotif('Lỗi', e.message || 'Không cập nhật được batch', 'error'); });
    }

    function showDetail(scheduleId) {
        fetch('/api/schedules/' + encodeURIComponent(scheduleId)).then(function (r) { return r.json(); }).then(function (j) {
            if (j.error || !j.schedule) { showNotif('Lỗi', j.error || 'Không tải được chi tiết lịch', 'error'); return; }
            var sch = j.schedule;
            var kind = kindInfo(prepareSchedule(Object.assign({}, sch)));
            var recipients = sch.recipients || [];
            var results = sch.results || [];
            var ok = results.filter(function (r) { return r.status === 'success'; }).length;
            var fail = results.filter(function (r) { return r.status === 'failed'; }).length;
            var pending = Math.max(recipients.length - ok - fail, 0);

            // Ô thông tin: label trên, giá trị dưới; xếp lưới 2 cột cho gọn.
            function detailCell(label, value, opts) {
                opts = opts || {};
                var style = (opts.mono ? 'font-family:monospace;word-break:break-all;' : '') + (opts.pre ? 'white-space:pre-wrap;' : '');
                return '<div class="detail-cell' + (opts.full ? ' full' : '') + '">'
                    + '<div class="detail-label">' + label + '</div>'
                    + '<div class="detail-value"' + (style ? ' style="' + style + '"' : '') + '>' + value + '</div>'
                    + '</div>';
            }

            var html = '';
            html += '<div class="detail-grid">';
            html += detailCell('Loại chiến dịch', escHtml(kind.label));
            html += detailCell('Trạng thái', escHtml(statusLabel(sch.status)));
            html += detailCell('Tài khoản gửi', escHtml(accountName(sch)));
            html += detailCell('Mã lịch', escHtml(sch.scheduleId || scheduleId), { mono: true });
            html += detailCell('Thời gian gửi', escHtml(formatDateTime(sch.runAt)));
            html += detailCell('Tạo lúc', escHtml(formatDateTime(sch.createdAt)));
            if (sch.groupInfo && (sch.groupInfo.name || sch.groupInfo.groupId)) {
                html += detailCell('Nhóm nguồn', escHtml(sch.groupInfo.name || sch.groupInfo.groupId || '-'));
                html += detailCell('Group ID', escHtml(sch.groupInfo.groupId || '-'), { mono: true });
            }
            html += detailCell('Nội dung', escHtml(sch.message || '-'), { full: true, pre: true });
            html += '</div>';

            // Số liệu: hàng 4 ô stat, số to dễ quét thay cho 4 dòng chữ.
            html += '<div class="detail-stats">';
            html += '<div class="detail-stat"><small>Người nhận</small><strong>' + recipients.length + '</strong></div>';
            html += '<div class="detail-stat ok"><small>Thành công</small><strong>' + ok + '</strong></div>';
            html += '<div class="detail-stat"><small>Chưa gửi</small><strong>' + pending + '</strong></div>';
            html += '<div class="detail-stat err"><small>Lỗi</small><strong>' + fail + '</strong></div>';
            html += '</div>';

            html += '<div style="font-weight:700;margin-bottom:8px;">Danh sách người nhận</div>';
            if (!recipients.length) {
                html += '<div style="color:var(--text-muted);font-size:13px;">Không có người nhận.</div>';
            } else {
                // Chip lọc để quan sát mạch lạc: ai đã gửi, ai lỗi, ai chưa gửi.
                var chipDefs = [
                    { key: 'all', label: 'Tất cả', n: recipients.length },
                    { key: 'success', label: 'Đã gửi', n: ok },
                    { key: 'pending', label: 'Chưa gửi', n: pending },
                    { key: 'failed', label: 'Lỗi', n: fail },
                ];
                html += '<div class="schedmon-recip-filters">' + chipDefs.map(function (c) {
                    return '<button type="button" class="schedmon-recip-chip is-' + c.key + (c.key === 'all' ? ' active' : '') + '" data-recip-filter="' + c.key + '">'
                        + escHtml(c.label) + '<span>' + c.n + '</span></button>';
                }).join('') + '</div>';
                html += '<div style="max-height:300px;overflow:auto;border:1px solid var(--border);border-radius:8px;" id="schedmonRecipWrap">';
                html += '<table class="schedmon-recip-table"><thead><tr>';
                html += '<th>Tên / Nhóm</th><th>ID / Phone</th><th>Trạng thái</th>';
                html += '</tr></thead><tbody>';
                recipients.forEach(function (r) {
                    var rid = r.userId || r.uid || r.id || r.groupId || '';
                    var name = r.zaloName || r.displayName || r.name || r.phone || rid || '-';
                    var phone = r.phone || r.phoneNumber || '';
                    var res = results.find(function (x) { return String(x.userId || x.uid || x.id || x.groupId || '') === String(rid); }) || {};
                    var rst = res.status || 'pending';
                    if (rst !== 'success' && rst !== 'failed') rst = 'pending';
                    var rstText = rst === 'success' ? 'Đã gửi' : (rst === 'failed' ? 'Lỗi' : 'Chưa gửi');
                    html += '<tr data-recip-status="' + rst + '"><td>' + escHtml(name) + '</td>' +
                        '<td style="font-family:monospace;word-break:break-all;">' + escHtml(rid || phone || '-') + '</td>' +
                        '<td><span class="schedmon-recip-badge is-' + rst + '">' + escHtml(rstText) + '</span></td></tr>';
                });
                html += '</tbody></table></div>';
            }

            setModal('Chi tiết lịch - ' + (sch.title || scheduleId), html);
            // Gắn lọc trạng thái người nhận sau khi modal render.
            var modalRoot = document.getElementById('detailModal');
            if (modalRoot) {
                var chips = modalRoot.querySelectorAll('[data-recip-filter]');
                chips.forEach(function (chip) {
                    chip.addEventListener('click', function () {
                        var f = chip.getAttribute('data-recip-filter');
                        chips.forEach(function (c) { c.classList.toggle('active', c === chip); });
                        modalRoot.querySelectorAll('#schedmonRecipWrap tbody tr').forEach(function (tr) {
                            tr.hidden = (f !== 'all' && tr.getAttribute('data-recip-status') !== f);
                        });
                    });
                });
            }
        }).catch(function (e) { showNotif('Lỗi', e.message || 'Không tải được chi tiết lịch', 'error'); });
    }

    function showEditModal(scheduleId) {
        fetch('/api/schedules/' + scheduleId).then(function (r) { return r.json(); }).then(function (j) {
            if (j.error) { showNotif('Lỗi', j.error, 'error'); return; }
            var s = j.schedule;
            var d = new Date(s.runAt);
            var dt = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0') + 'T' + String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
            var m = document.createElement('div');
            m.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:9999;overflow-y:auto;padding:20px 0;';
            m.innerHTML = '<div style="background:var(--surface);border-radius:12px;padding:24px;max-width:500px;width:90%;max-height:90vh;overflow-y:auto;">' +
                '<h3 style="margin:0 0 16px">Sửa lịch</h3>' +
                '<div class="form-group"><label>Tiêu đề</label><input type="text" id="editTitle" value="' + escHtml(s.title || '') + '"></div>' +
                '<div class="form-group"><label>Nội dung</label><textarea id="editMessage" style="min-height:100px">' + escHtml(s.message || '') + '</textarea></div>' +
                '<div class="form-group"><label>Thời gian</label><input type="datetime-local" id="editRunAt" value="' + dt + '"></div>' +
                '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px"><button class="btn btn-ghost" id="btnCancelEdit">Hủy</button><button class="btn btn-primary" id="btnSaveEdit">Lưu</button></div></div>';
            document.body.appendChild(m);
            function close() { if (m.parentNode) m.parentNode.removeChild(m); }
            m.addEventListener('click', function (e) { if (e.target === m) close(); });
            m.querySelector('#btnCancelEdit').addEventListener('click', close);
            m.querySelector('#btnSaveEdit').addEventListener('click', function () {
                var u = { title: m.querySelector('#editTitle').value, message: m.querySelector('#editMessage').value, runAt: m.querySelector('#editRunAt').value };
                fetch('/api/schedules/' + scheduleId, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(u) })
                    .then(function (r) { return r.json(); })
                    .then(function (jj) {
                        if (jj.error) showNotif('Lỗi', jj.error, 'error');
                        else { showNotif('Thành công', 'Đã cập nhật', 'success'); close(); reload(); }
                    });
            });
        });
    }

    function runNow(scheduleId) {
        fetch('/api/schedules/' + scheduleId + '/run-now', { method: 'POST' }).then(function (r) { return r.json(); }).then(function (j) {
            if (j.error) showNotif('Lỗi', j.error, 'error'); else { showNotif('OK', 'Đã chạy', 'success'); reload(); }
        });
    }

    async function cancelSchedule(scheduleId) {
        if (!(await nexusConfirm('Hủy lịch này?', { title: 'Hủy lịch' }))) return;
        fetch('/api/schedules/' + scheduleId + '/cancel', { method: 'POST' }).then(function (r) { return r.json(); }).then(function (j) {
            if (j.error) showNotif('Lỗi', j.error, 'error'); else { showNotif('OK', 'Đã hủy', 'success'); reload(); }
        });
    }

    async function deleteSchedule(scheduleId) {
        if (!(await nexusConfirm('Xóa lịch này?', { title: 'Xóa lịch', confirmText: 'Xóa', danger: true }))) return;
        fetch('/api/schedules/' + scheduleId, { method: 'DELETE' }).then(function (r) { return r.json(); }).then(function (j) {
            if (j.error) showNotif('Lỗi', j.error, 'error'); else { showNotif('OK', 'Đã xóa', 'success'); reload(); }
        });
    }

    // ─── Overlay dữ liệu phụ ─────────────────────────────────────────────
    function getDataOverlay(name) {
        if (!name) return null;
        return document.querySelector('[data-schedmon-overlay="' + String(name) + '"]');
    }

    function closeDataOverlay(name) {
        var overlay = name ? getDataOverlay(name) : document.querySelector('.schedmon-data-overlay.is-open');
        if (!overlay) return;
        overlay.classList.remove('is-open');
        overlay.setAttribute('aria-hidden', 'true');
        document.body.classList.remove('schedmon-overlay-open');

        var opener = overlay._schedmonOpener;
        if (opener && typeof opener.focus === 'function') {
            setTimeout(function () { opener.focus(); }, 20);
        }
    }

    function closeAllDataOverlays() {
        document.querySelectorAll('.schedmon-data-overlay.is-open').forEach(function (overlay) {
            overlay.classList.remove('is-open');
            overlay.setAttribute('aria-hidden', 'true');
        });
        document.body.classList.remove('schedmon-overlay-open');
    }

    function openDataOverlay(name, opener) {
        var overlay = getDataOverlay(name);
        if (!overlay) return;
        closeAllDataOverlays();
        overlay._schedmonOpener = opener || document.activeElement;
        overlay.classList.add('is-open');
        overlay.setAttribute('aria-hidden', 'false');
        document.body.classList.add('schedmon-overlay-open');

        var closeBtn = overlay.querySelector('[data-schedmon-overlay-close]');
        if (closeBtn) setTimeout(function () { closeBtn.focus(); }, 20);
    }

    function bindDataOverlays() {
        document.querySelectorAll('[data-schedmon-overlay-open]').forEach(function (button) {
            button.addEventListener('click', function () {
                openDataOverlay(button.getAttribute('data-schedmon-overlay-open'), button);
            });
        });

        document.querySelectorAll('[data-schedmon-overlay-close]').forEach(function (button) {
            button.addEventListener('click', function () {
                var overlay = button.closest('[data-schedmon-overlay]');
                if (overlay) closeDataOverlay(overlay.getAttribute('data-schedmon-overlay'));
            });
        });

        document.querySelectorAll('.schedmon-data-overlay').forEach(function (overlay) {
            overlay.addEventListener('mousedown', function (event) {
                if (event.target === overlay) closeDataOverlay(overlay.getAttribute('data-schedmon-overlay'));
            });
        });

        document.addEventListener('keydown', function (event) {
            if (event.key === 'Escape') closeAllDataOverlays();
        });
    }

    // ─── Modal / thông báo dùng chung ─────────────────────────────────────
    function setModal(title, html) {
        var titleEl = document.getElementById('detailTitle');
        var contentEl = document.getElementById('detailContent');
        var modalEl = document.getElementById('detailModal');
        if (titleEl) titleEl.textContent = title;
        if (contentEl) contentEl.innerHTML = html;
        if (modalEl) modalEl.classList.add('show');
    }

    function closeDetailModal() {
        var modalEl = document.getElementById('detailModal');
        if (modalEl) modalEl.classList.remove('show');
    }

    function showNotif(title, message, type) {
        type = type || 'info';
        var card = document.getElementById('notificationOverlay');
        if (!card) return;
        card.className = 'notification-overlay ' + type;
        document.getElementById('notifTitle').textContent = title;
        document.getElementById('notifMessage').textContent = message;
        var icon = document.getElementById('notifIcon');
        if (icon) icon.textContent = type === 'success' ? '✓' : (type === 'error' ? '!' : 'i');
        card.classList.add('show');
        clearTimeout(card._timeout);
        card._timeout = setTimeout(function () { card.classList.remove('show'); }, 3000);
    }

    function hideNotif() {
        var n = document.getElementById('notificationOverlay');
        if (n) n.classList.remove('show');
    }

    // ─── Tải dữ liệu & vòng lặp tự làm mới ────────────────────────────────
    function reload() {
        return Promise.all([fetchJson('/api/schedules'), fetchJson('/api/action-plans'), fetchJson('/api/accounts')]).then(function (res) {
            var sj = res[0] || {}, pj = res[1] || {}, aj = res[2] || {};
            state.accounts = aj.accounts || [];
            var normalSchedules = (sj.schedules || []).map(prepareSchedule);
            var actionPlans = (pj.plans || []).map(prepareActionPlan);
            state.all = normalSchedules.concat(actionPlans);

            renderStats();
            renderRunning();
            renderUpcoming();
            filterAll();
            renderSupportWidgets();
        }).catch(function () {
            showNotif('Lỗi', 'Không tải được dữ liệu lịch', 'error');
        });
    }

    function startAutoRefresh() {
        if (refreshTimer) clearInterval(refreshTimer);
        refreshTimer = setInterval(function () {
            if (document.hidden) return;
            reload();
        }, REFRESH_MS);
    }

    document.addEventListener('DOMContentLoaded', function () {
        bindDataOverlays();

        var todayBtn = document.getElementById('schedmonTodayBtn');
        var prevBtn = document.getElementById('schedmonPrevMonthBtn');
        var nextBtn = document.getElementById('schedmonNextMonthBtn');
        var calendarClearBtn = document.getElementById('schedmonCalendarClearBtn');
        var dateFilterClearBtn = document.getElementById('schedmonDateFilterClear');

        if (todayBtn) todayBtn.addEventListener('click', function () {
            var today = new Date();
            selectCalendarDate(localDayKey(today.getTime()), { scroll: false });
        });
        if (prevBtn) prevBtn.addEventListener('click', function () { changeCalendarMonth(-1); });
        if (nextBtn) nextBtn.addEventListener('click', function () { changeCalendarMonth(1); });
        if (calendarClearBtn) calendarClearBtn.addEventListener('click', function () { clearDateFilter(); });
        if (dateFilterClearBtn) dateFilterClearBtn.addEventListener('click', function () { clearDateFilter(); });

        updateDateFilterUI();
        reload();
        startAutoRefresh();
    });

    window.SchedMon = {
        reload: reload,
        filterAll: filterAll,
        selectCalendarDate: selectCalendarDate,
        clearDateFilter: clearDateFilter,
        changeCalendarMonth: changeCalendarMonth,
        showDetail: showDetail,
        showEditModal: showEditModal,
        showActionPlanDetail: showActionPlanDetail,
        updateActionPlanBatch: updateActionPlanBatch,
        runNow: runNow,
        cancelSchedule: cancelSchedule,
        deleteSchedule: deleteSchedule,
        closeDetailModal: closeDetailModal,
        hideNotif: hideNotif,
        openOverlay: openDataOverlay,
        closeOverlay: closeDataOverlay
    };
}());
