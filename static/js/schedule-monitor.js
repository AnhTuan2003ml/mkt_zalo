// ─── Lịch gửi (trang riêng) ────────────────────────────────────────────
// Chỉ để theo dõi: lịch nào đang chạy, lịch nào sắp chạy tiếp theo, và
// quản lý (sửa/chạy ngay/hủy/xóa) toàn bộ lịch đã tạo từ trang "Chiến dịch".
(function () {
    'use strict';

    var state = { schedules: [], plans: [], accounts: [], all: [] };
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
        var doneToday = state.all.filter(isDoneToday);
        var errorItems = state.all.filter(isError);

        setText('statRunning', running.length);
        setText('statUpcoming', upcoming.length);
        setText('statDoneToday', doneToday.length);
        setText('statError', errorItems.length);
        setText('runningCountPill', running.length);

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

        if (item._itemType === 'schedule') {
            var recipients = item.recipients || [];
            var results = item.results || [];
            var ok = results.filter(function (r) { return r.status === 'success'; }).length;
            var fail = results.filter(function (r) { return r.status === 'failed'; }).length;
            var total = recipients.length || 1;
            var percent = Math.round(((ok + fail) / total) * 100);
            card.innerHTML =
                '<span class="schedmon-run-dot"></span><span class="schedule-kind-badge ' + kind.cls + '">' + escHtml(kind.label) + '</span>' +
                '<div class="schedmon-running-title">' + escHtml(item.title || 'Không có tiêu đề') + '</div>' +
                '<div class="schedmon-running-sub">' + escHtml(accountName(item)) + ' · bắt đầu ' + escHtml(formatDateTime(item.runAt)) + '</div>' +
                '<div class="schedule-progress-wrap">' +
                    '<div class="schedule-progress-line"><div class="schedule-progress-bar" style="width:' + percent + '%"></div></div>' +
                    '<div class="schedule-progress-text"><span>' + (ok + fail) + '/' + recipients.length + ' đã xử lý</span><span>' + ok + ' thành công · ' + fail + ' lỗi</span></div>' +
                '</div>' +
                '<div class="schedmon-running-actions">' +
                    '<button class="btn btn-ghost btn-sm" onclick="SchedMon.showDetail(\'' + item.scheduleId + '\')">Chi tiết</button>' +
                    '<button class="btn btn-warning btn-sm" onclick="SchedMon.cancelSchedule(\'' + item.scheduleId + '\')">Hủy</button>' +
                '</div>';
        } else {
            var pg = item.progress || {};
            var percent2 = Math.max(0, Math.min(100, Number(pg.percent || 0)));
            card.innerHTML =
                '<span class="schedmon-run-dot"></span><span class="schedule-kind-badge ' + kind.cls + '">' + escHtml(kind.label) + '</span>' +
                '<div class="schedmon-running-title">' + escHtml(item.name || kind.label) + '</div>' +
                '<div class="schedmon-running-sub">' + escHtml(item.accountName || item.accountId || 'N/A') + ' · ' + (pg.doneBatches || 0) + '/' + (pg.totalBatches || getPlanBatches(item).length) + ' batch xong</div>' +
                '<div class="schedule-progress-wrap">' +
                    '<div class="schedule-progress-line"><div class="schedule-progress-bar" style="width:' + percent2 + '%"></div></div>' +
                    '<div class="schedule-progress-text"><span>Tiến độ: ' + percent2 + '%</span><span>' + (pg.processedMembers || 0) + '/' + (pg.totalMembers || item.totalMembers || 0) + ' người</span></div>' +
                '</div>' +
                '<div class="schedmon-running-actions">' +
                    '<button class="btn btn-ghost btn-sm" onclick=\'SchedMon.showActionPlanDetail(' + jsArg(item.planType) + ',' + jsArg(item.id || '') + ')\'>Chi tiết / cập nhật</button>' +
                '</div>';
        }
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
            .slice(0, 8);

        if (!upcoming.length) {
            root.innerHTML = '<div class="schedmon-empty"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M4 12h16M14 6l6 6-6 6"/></svg><b>Chưa có lịch nào sắp chạy</b><p>Tạo chiến dịch mới để thấy hàng chờ tại đây.</p></div>';
            return;
        }

        root.innerHTML = upcoming.map(function (item) {
            var kind = kindInfo(item);
            var cd = countdownInfo(item._nextTime);
            var title = item._itemType === 'schedule' ? (item.title || 'Không có tiêu đề') : (item.name || kind.label);
            var id = item._itemType === 'schedule' ? item.scheduleId : item.id;
            var actions = item._itemType === 'schedule'
                ? '<button class="btn btn-success btn-sm" onclick="SchedMon.runNow(\'' + id + '\')">Chạy ngay</button>' +
                  '<button class="btn btn-warning btn-sm" onclick="SchedMon.cancelSchedule(\'' + id + '\')">Hủy</button>'
                : '<button class="btn btn-ghost btn-sm" onclick=\'SchedMon.showActionPlanDetail(' + jsArg(item.planType) + ',' + jsArg(id || '') + ')\'>Chi tiết</button>';

            return '<div class="schedmon-upcoming-item">' +
                '<span class="schedmon-countdown-chip ' + cd.cls + '">' + escHtml(cd.text) + '</span>' +
                '<div class="schedmon-upcoming-copy">' +
                    '<div class="schedmon-up-title">' + escHtml(title) + '</div>' +
                    '<div class="schedmon-up-meta"><span class="schedule-kind-badge ' + kind.cls + '">' + escHtml(kind.label) + '</span><span>' + escHtml(formatDateTime(item._nextTime)) + '</span><span>' + escHtml(accountName(item)) + '</span></div>' +
                '</div>' +
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

        var card = document.createElement('div');
        card.className = 'schedule-card';
        card.innerHTML =
            '<div class="schedule-card-header">' +
                '<div class="schedule-card-title-area">' +
                    '<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:6px;"><span class="schedule-kind-badge ' + kind.cls + '">' + escHtml(kind.label) + '</span></div>' +
                    '<h3 class="schedule-title">' + escHtml(sch.title || 'Không có tiêu đề') + '</h3>' +
                    '<div class="schedule-time">🕒 ' + escHtml(formatDateTime(sch.runAt)) + '</div>' +
                '</div>' +
                '<span class="schedule-status-badge ' + badgeClass(st) + '">' + escHtml(statusLabel(st)) + '</span>' +
            '</div>' +
            (sch.message ? '<div class="schedule-message">' + escHtml(sch.message) + '</div>' : '') +
            '<div class="schedule-meta-row">' +
                '<div class="schedule-meta-item"><span class="schedule-meta-label">Người nhận</span><span class="schedule-meta-value">' + ok + '/' + total + ' thành công</span></div>' +
                '<div class="schedule-meta-item"><span class="schedule-meta-label">Chưa gửi / Lỗi</span><span class="schedule-meta-value">' + pend + ' / ' + fail + '</span></div>' +
                '<div class="schedule-meta-item"><span class="schedule-meta-label">Tài khoản</span><span class="schedule-meta-value">' + escHtml(accountName(sch)) + '</span></div>' +
            '</div>' +
            '<div class="schedule-actions-group">' +
                '<button class="btn btn-primary btn-sm" onclick="SchedMon.showEditModal(\'' + sch.scheduleId + '\')">Sửa</button>' +
                '<button class="btn btn-ghost btn-sm" onclick="SchedMon.showDetail(\'' + sch.scheduleId + '\')">Chi tiết</button>' +
                (st === 'pending' ? '<button class="btn btn-success btn-sm" onclick="SchedMon.runNow(\'' + sch.scheduleId + '\')">Chạy ngay</button>' : '') +
                (st === 'pending' || st === 'running' ? '<button class="btn btn-warning btn-sm" onclick="SchedMon.cancelSchedule(\'' + sch.scheduleId + '\')">Hủy</button>' : '') +
                (st !== 'running' ? '<button class="btn btn-danger btn-sm" onclick="SchedMon.deleteSchedule(\'' + sch.scheduleId + '\')">Xóa</button>' : '') +
            '</div>';
        return card;
    }

    function buildActionPlanCard(plan) {
        var st = actionPlanStatus(plan);
        var pg = plan.progress || {};
        var kind = kindInfo(plan);
        var total = Number(pg.totalMembers || plan.totalMembers || 0);
        var pending = Number(pg.pendingMembers || Math.max(total - (pg.doneMembers || 0) - (pg.failedMembers || 0), 0));
        var failed = Number(pg.failedMembers || 0);
        var percent = Math.max(0, Math.min(100, Number(pg.percent || 0)));
        var groups = (plan.targetGroups || []).map(function (g) { return g.name || g.groupName || g.groupId; }).filter(Boolean).join(', ');
        if (!groups && (plan.targetGroupIds || []).length) groups = (plan.targetGroupIds || []).join(', ');
        var after = plan.afterAction === 'invite_existing_group' ? ('Sau kết bạn: mời vào ' + (groups || 'nhóm đã chọn')) : (plan.afterAction === 'create_new_group' ? ('Sau kết bạn: tạo nhóm ' + (plan.newGroupName || 'mới')) : 'Chỉ lưu/gửi theo lịch');
        var metaTarget = plan.planType === 'group_invite' ? (groups || '-') : after;

        var card = document.createElement('div');
        card.className = 'schedule-card';
        card.innerHTML =
            '<div class="schedule-card-header">' +
                '<div class="schedule-card-title-area">' +
                    '<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:6px;">' +
                        '<span class="schedule-kind-badge ' + kind.cls + '">' + escHtml(kind.label) + '</span>' +
                        '<span class="schedule-time">Bắt đầu ' + escHtml(formatDate(plan.startDate)) + '</span>' +
                    '</div>' +
                    '<h3 class="schedule-title">' + escHtml(plan.name || kind.label) + '</h3>' +
                '</div>' +
                '<span class="schedule-status-badge ' + badgeClass(st) + '">' + escHtml(statusLabel(st)) + '</span>' +
            '</div>' +
            (plan.message ? '<div class="schedule-message">' + escHtml(plan.message) + '</div>' : '') +
            '<div class="schedule-progress-wrap">' +
                '<div class="schedule-progress-line"><div class="schedule-progress-bar" style="width:' + percent + '%"></div></div>' +
                '<div class="schedule-progress-text"><span>Tiến độ: ' + percent + '%</span><span>' + (pg.processedMembers || 0) + '/' + total + ' đã xử lý</span></div>' +
            '</div>' +
            '<div class="schedule-meta-row">' +
                '<div class="schedule-meta-item"><span class="schedule-meta-label">Mỗi ngày</span><span class="schedule-meta-value">' + escHtml(String(plan.dailyLimit || 1)) + ' người</span></div>' +
                '<div class="schedule-meta-item"><span class="schedule-meta-label">Batch</span><span class="schedule-meta-value">' + (pg.doneBatches || 0) + '/' + (pg.totalBatches || getPlanBatches(plan).length) + ' xong</span></div>' +
                '<div class="schedule-meta-item"><span class="schedule-meta-label">Chờ / Lỗi</span><span class="schedule-meta-value">' + pending + ' / ' + failed + '</span></div>' +
                '<div class="schedule-meta-item"><span class="schedule-meta-label">Tài khoản</span><span class="schedule-meta-value">' + escHtml(plan.accountName || plan.accountId || 'N/A') + '</span></div>' +
                '<div class="schedule-meta-item"><span class="schedule-meta-label">Đích</span><span class="schedule-meta-value">' + escHtml(metaTarget || '-') + '</span></div>' +
            '</div>' +
            '<div class="schedule-actions-group">' +
                '<button class="btn btn-ghost btn-sm" onclick=\'SchedMon.showActionPlanDetail(' + jsArg(plan.planType) + ',' + jsArg(plan.id || '') + ')\'>Chi tiết / cập nhật tiến độ</button>' +
            '</div>';
        return card;
    }

    function renderAll(items) {
        var root = document.getElementById('allList');
        if (!root) return;
        if (!items.length) {
            root.innerHTML = '<div class="schedmon-empty"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg><b>Chưa có lịch nào</b><p>Tạo chiến dịch đầu tiên để theo dõi tại đây.</p></div>';
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
        var filtered = state.all.filter(function (s) {
            var isPlan = s._itemType === 'action_plan';
            var type = isPlan ? s.planType : 'schedule';
            var status = isPlan ? actionPlanStatus(s) : (s.status || 'pending');
            var searchText = [s.title, s.name, s.message, s.accountName, s.accountId, s.planTypeLabel].join(' ').toLowerCase();
            if (q && searchText.indexOf(q) === -1) return false;
            if (tf && type !== tf) return false;
            if (sf && status !== sf && !(sf === 'done' && status === 'completed') && !(sf === 'pending' && (status === 'planned' || status === 'pending_manual'))) return false;
            return true;
        }).sort(function (a, b) { return (b._sortTime || 0) - (a._sortTime || 0); });
        renderAll(filtered);
        if (q || sf || tf) setText('schedListCount', filtered.length + '/' + state.all.length + ' lịch');
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

            var html = '';
            html += '<div class="detail-item"><div class="detail-label">Mã lịch:</div><div class="detail-value" style="font-family:monospace;word-break:break-all;">' + escHtml(sch.scheduleId || scheduleId) + '</div></div>';
            html += '<div class="detail-item"><div class="detail-label">Tiêu đề:</div><div class="detail-value">' + escHtml(sch.title || '-') + '</div></div>';
            html += '<div class="detail-item"><div class="detail-label">Loại chiến dịch:</div><div class="detail-value">' + escHtml(kind.label) + '</div></div>';
            html += '<div class="detail-item"><div class="detail-label">Tài khoản gửi:</div><div class="detail-value">' + escHtml(accountName(sch)) + '</div></div>';
            html += '<div class="detail-item"><div class="detail-label">Thời gian gửi:</div><div class="detail-value">' + escHtml(formatDateTime(sch.runAt)) + '</div></div>';
            html += '<div class="detail-item"><div class="detail-label">Trạng thái:</div><div class="detail-value">' + escHtml(statusLabel(sch.status)) + '</div></div>';
            html += '<div class="detail-item"><div class="detail-label">Tạo lúc:</div><div class="detail-value">' + escHtml(formatDateTime(sch.createdAt)) + '</div></div>';
            html += '<div class="detail-item"><div class="detail-label">Nội dung:</div><div class="detail-value" style="white-space:pre-wrap;">' + escHtml(sch.message || '-') + '</div></div>';

            if (sch.groupInfo && (sch.groupInfo.name || sch.groupInfo.groupId)) {
                html += '<hr style="border:0;border-top:1px solid var(--border);margin:12px 0;">';
                html += '<div class="detail-item"><div class="detail-label">Nhóm nguồn:</div><div class="detail-value">' + escHtml(sch.groupInfo.name || sch.groupInfo.groupId || '-') + '</div></div>';
                html += '<div class="detail-item"><div class="detail-label">Group ID:</div><div class="detail-value" style="font-family:monospace;word-break:break-all;">' + escHtml(sch.groupInfo.groupId || '-') + '</div></div>';
            }

            html += '<hr style="border:0;border-top:1px solid var(--border);margin:12px 0;">';
            html += '<div class="detail-item"><div class="detail-label">Tổng người nhận:</div><div class="detail-value">' + recipients.length + '</div></div>';
            html += '<div class="detail-item"><div class="detail-label">Thành công:</div><div class="detail-value" style="color:var(--green);">' + ok + '</div></div>';
            html += '<div class="detail-item"><div class="detail-label">Chưa gửi:</div><div class="detail-value">' + pending + '</div></div>';
            html += '<div class="detail-item"><div class="detail-label">Lỗi:</div><div class="detail-value" style="color:var(--red);">' + fail + '</div></div>';

            html += '<hr style="border:0;border-top:1px solid var(--border);margin:12px 0;">';
            html += '<div style="font-weight:700;margin-bottom:8px;">Danh sách người nhận</div>';
            if (!recipients.length) {
                html += '<div style="color:var(--text-muted);font-size:13px;">Không có người nhận.</div>';
            } else {
                html += '<div style="max-height:260px;overflow:auto;border:1px solid var(--border);border-radius:8px;">';
                html += '<table style="width:100%;border-collapse:collapse;font-size:12px;"><thead><tr>';
                html += '<th style="text-align:left;padding:8px;border-bottom:1px solid var(--border);">Tên / Nhóm</th>';
                html += '<th style="text-align:left;padding:8px;border-bottom:1px solid var(--border);">ID / Phone</th>';
                html += '<th style="text-align:left;padding:8px;border-bottom:1px solid var(--border);">Trạng thái</th>';
                html += '</tr></thead><tbody>';
                recipients.forEach(function (r) {
                    var rid = r.userId || r.uid || r.id || r.groupId || '';
                    var name = r.zaloName || r.displayName || r.name || r.phone || rid || '-';
                    var phone = r.phone || r.phoneNumber || '';
                    var res = results.find(function (x) { return String(x.userId || x.uid || x.id || x.groupId || '') === String(rid); }) || {};
                    var rst = res.status || 'pending';
                    var rstText = rst === 'success' ? 'Thành công' : (rst === 'failed' ? 'Lỗi' : 'Chưa gửi');
                    html += '<tr><td style="padding:8px;border-bottom:1px solid var(--border);">' + escHtml(name) + '</td>' +
                        '<td style="padding:8px;border-bottom:1px solid var(--border);font-family:monospace;word-break:break-all;">' + escHtml(rid || phone || '-') + '</td>' +
                        '<td style="padding:8px;border-bottom:1px solid var(--border);">' + escHtml(rstText) + '</td></tr>';
                });
                html += '</tbody></table></div>';
            }

            setModal('Chi tiết lịch - ' + (sch.title || scheduleId), html);
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
        reload();
        startAutoRefresh();
    });

    window.SchedMon = {
        reload: reload,
        filterAll: filterAll,
        showDetail: showDetail,
        showEditModal: showEditModal,
        showActionPlanDetail: showActionPlanDetail,
        updateActionPlanBatch: updateActionPlanBatch,
        runNow: runNow,
        cancelSchedule: cancelSchedule,
        deleteSchedule: deleteSchedule,
        closeDetailModal: closeDetailModal,
        hideNotif: hideNotif
    };
}());
