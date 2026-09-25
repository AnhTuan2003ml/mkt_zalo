// Trang Quản lý bạn bè: chọn tài khoản (combobox avatar + tên) -> tải danh sách
// bạn bè qua GET /api/friends?accountId=... -> bảng có thống kê số lượng.
(function () {
    'use strict';

    var accounts = [];
    var friends = [];
    var loading = false;
    var requestSeq = 0;

    function $(id) { return document.getElementById(id); }

    function esc(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function normalizeAvatar(url) {
        url = String(url || '').trim();
        if (!url || url === 'null' || url === 'undefined') return '';
        if (url.startsWith('//')) return 'https:' + url;
        return url;
    }

    function accountId(acc) {
        return String((acc && (acc.accountId || acc.id || acc.account_id)) || '').trim();
    }

    function accountName(acc) {
        return (acc && (acc.name || acc.zaloName || acc.displayName || acc.phone)) || accountId(acc) || 'Không tên';
    }

    function selectedAccountId() {
        return String(($('friendsAccountId') || {}).value || '').trim();
    }

    // ─── Combobox tài khoản (avatar + tên) ─────────────────────────────
    function renderAccountDropdown() {
        var container = $('friendsAccountDropdown');
        if (!container) return;
        var sel = accounts.find(function (a) { return accountId(a) === selectedAccountId(); }) || null;

        var btnHtml;
        if (!sel) {
            btnHtml = '<button type="button" class="account-dropdown-button"><span>Chọn tài khoản</span></button>';
        } else {
            var av = normalizeAvatar(sel.avatarUrl || sel.avatar || '');
            btnHtml = '<button type="button" class="account-dropdown-button" title="' + esc(accountId(sel)) + '">'
                + (av ? '<img src="' + esc(av) + '" class="account-dropdown-avatar" alt="">' : '<span class="account-dropdown-avatar placeholder"></span>')
                + '<span class="account-dropdown-name">' + esc(accountName(sel)) + '</span></button>';
        }

        var menuHtml = '<div class="account-dropdown-menu hidden">' + accounts.map(function (a) {
            var av = normalizeAvatar(a.avatarUrl || a.avatar || '');
            var id = accountId(a);
            var active = (sel && accountId(sel) === id) ? ' active' : '';
            return '<button type="button" class="account-dropdown-item' + active + '" data-account-id="' + esc(id) + '">'
                + (av ? '<img src="' + esc(av) + '" class="account-dropdown-avatar" alt="">' : '<span class="account-dropdown-avatar placeholder"></span>')
                + '<span><b>' + esc(accountName(a)) + '</b><br><small>' + esc(id) + '</small></span></button>';
        }).join('') + '</div>';

        container.innerHTML = btnHtml + menuHtml;

        var btn = container.querySelector('.account-dropdown-button');
        var menu = container.querySelector('.account-dropdown-menu');
        if (btn && menu) {
            btn.addEventListener('click', function (e) {
                e.stopPropagation();
                menu.classList.toggle('hidden');
            });
        }
        container.querySelectorAll('.account-dropdown-item').forEach(function (item) {
            item.addEventListener('click', function (e) {
                e.stopPropagation();
                var aid = String(this.dataset.accountId || '').trim();
                if (!aid || aid === selectedAccountId()) {
                    if (menu) menu.classList.add('hidden');
                    return;
                }
                if ($('friendsAccountId')) $('friendsAccountId').value = aid;
                try { localStorage.setItem('nexus_friends_account_id', aid); } catch (err) {}
                renderAccountDropdown();
                loadFriends();
            });
        });
    }

    document.addEventListener('click', function () {
        var menu = document.querySelector('#friendsAccountDropdown .account-dropdown-menu');
        if (menu) menu.classList.add('hidden');
    });

    async function loadAccounts() {
        try {
            var res = await fetch('/api/accounts');
            var data = await res.json();
            accounts = data.accounts || [];
        } catch (err) {
            accounts = [];
        }
        var saved = '';
        try { saved = localStorage.getItem('nexus_friends_account_id') || ''; } catch (err) {}
        var valid = accounts.some(function (a) { return accountId(a) === saved; });
        var first = accounts.length ? accountId(accounts[0]) : '';
        var chosen = valid ? saved : first;
        if ($('friendsAccountId')) $('friendsAccountId').value = chosen;
        renderAccountDropdown();
        if (chosen) loadFriends();
        else setEmptyHint('Chưa có tài khoản nào. Hãy thêm tài khoản ở trang Tài khoản trước.');
    }

    // ─── Tải + hiển thị bạn bè ──────────────────────────────────────────
    function setEmptyHint(text) {
        var empty = $('friendsEmpty');
        var hint = $('friendsEmptyHint');
        if (hint) hint.textContent = text;
        if (empty) empty.style.display = 'flex';
    }

    async function loadFriends() {
        var aid = selectedAccountId();
        if (!aid) return;
        var seq = ++requestSeq;
        loading = true;
        friends = [];
        friendsRenderTable();
        if ($('friendsLoading')) $('friendsLoading').style.display = 'block';
        if ($('friendsEmpty')) $('friendsEmpty').style.display = 'none';
        try {
            var res = await fetch('/api/friends?accountId=' + encodeURIComponent(aid));
            var data = await res.json();
            if (seq !== requestSeq) return;
            if (!data.success) throw new Error(data.error || 'Không lấy được danh sách bạn bè');
            friends = data.friends || [];
        } catch (err) {
            if (seq !== requestSeq) return;
            friends = [];
            setEmptyHint(err.message || String(err));
        } finally {
            if (seq === requestSeq) {
                loading = false;
                if ($('friendsLoading')) $('friendsLoading').style.display = 'none';
                friendsRenderTable();
            }
        }
    }

    function formatGender(value) {
        if (value === 0 || value === '0') return 'Nam';
        if (value === 1 || value === '1') return 'Nữ';
        return '-';
    }

    function friendsRenderTable() {
        var body = $('friendsBody');
        var empty = $('friendsEmpty');
        if (!body) return;

        if (friendsViewMode === 'phone') { friendsRenderPhoneRows(); return; }

        var q = String(($('friendsSearch') || {}).value || '').toLowerCase().trim();
        var list = friends.filter(function (f) {
            if (!q) return true;
            return (f.zaloName || '').toLowerCase().indexOf(q) >= 0
                || (f.displayName || '').toLowerCase().indexOf(q) >= 0
                || (f.username || '').toLowerCase().indexOf(q) >= 0
                || (f.phoneNumber || '').toLowerCase().indexOf(q) >= 0
                || (f.userId || '').indexOf(q) >= 0;
        });

        if ($('friendsTotal')) $('friendsTotal').textContent = friends.length;
        if ($('friendsShown')) {
            $('friendsShown').textContent = q ? '· hiển thị ' + list.length : '';
        }

        if (!list.length) {
            body.innerHTML = '';
            if (empty && !loading) {
                if (friends.length) setEmptyHint('Không có bạn bè nào khớp từ khóa "' + q + '".');
                empty.style.display = 'flex';
            }
            return;
        }
        if (empty) empty.style.display = 'none';

        body.innerHTML = list.map(function (f, idx) {
            var av = normalizeAvatar(f.avatar);
            var name = f.zaloName || f.displayName || 'Không tên';
            var avatarHtml = av
                ? '<img class="friends-avatar" src="' + esc(av) + '" alt="" onerror="this.outerHTML=\'<div class=&quot;friends-avatar fallback&quot;>' + esc(name.charAt(0).toUpperCase() || '?') + '</div>\'">'
                : '<div class="friends-avatar fallback">' + esc(name.charAt(0).toUpperCase() || '?') + '</div>';
            var status = String(f.status || '').trim();
            return '<tr data-uid="' + esc(f.userId) + '" title="Bấm để xem chi tiết">'
                + '<td class="friends-muted">' + (idx + 1) + '</td>'
                + '<td>' + avatarHtml + '</td>'
                + '<td title="' + esc(name) + '"><b>' + esc(name) + '</b></td>'
                + '<td class="friends-status-cell" title="' + esc(status) + '">' + (status ? esc(status) : '<span class="friends-muted">-</span>') + '</td>'
                + '<td>' + esc(formatGender(f.gender)) + '</td>'
                + '<td class="friends-muted">' + esc(f.sdob || '-') + '</td>'
                + '<td class="friends-muted">' + esc(f.phoneNumber || '-') + '</td>'
                + '<td><div class="friends-actions">'
                + '<button type="button" class="friends-chip friends-chip-msg" data-act="msg" data-uid="' + esc(f.userId) + '" title="Gửi tin nhắn">'
                + '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4Z"/></svg>Nhắn tin</button>'
                + '<button type="button" class="friends-chip friends-chip-del" data-act="remove" data-uid="' + esc(f.userId) + '" title="Xóa kết bạn">'
                + '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>Xóa</button>'
                + '</div></td>'
                + '</tr>';
        }).join('');
    }

    // ─── Click hàng / nút thao tác ──────────────────────────────────────
    function findFriend(uid) {
        uid = String(uid || '').trim();
        return friends.find(function (f) { return f.userId === uid; }) || null;
    }

    document.addEventListener('click', function (e) {
        var btn = e.target.closest('#friendsBody [data-act]');
        if (btn) {
            e.stopPropagation();
            var uid = btn.dataset.uid;
            if (btn.dataset.act === 'msg') friendsOpenMsg(uid);
            else if (btn.dataset.act === 'remove') friendsRemoveClick(btn, uid);
            return;
        }
        var row = e.target.closest('#friendsBody tr[data-uid]');
        if (row && !e.target.closest('button, a, input, code')) {
            friendsOpenDetail(row.dataset.uid);
        }
    });

    // ─── Toast trạng thái ────────────────────────────────────────────────
    var statusTimer = null;
    function showStatus(text, ok) {
        var el = $('friendsStatus');
        if (!el) return;
        el.textContent = text;
        el.className = ok ? 'ok' : 'err';
        clearTimeout(statusTimer);
        statusTimer = setTimeout(function () { el.className = ''; }, 4500);
    }

    // ─── Popup chi tiết (giống trang Lấy thành viên: hiện data sẵn rồi enrich) ─
    var detailUid = '';

    function avatarBlock(f) {
        var av = normalizeAvatar(f && f.avatar);
        var name = (f && (f.zaloName || f.displayName)) || '?';
        return av
            ? '<img src="' + esc(av) + '" alt="">'
            : '<div class="friends-avatar fallback">' + esc(name.charAt(0).toUpperCase() || '?') + '</div>';
    }

    function renderDetailRows(p) {
        var rows = [
            ['Tên Zalo', p.zaloName || p.displayName || '-'],
            ['Username', p.username || '-'],
            ['Giới tính', formatGender(p.gender)],
            ['Ngày sinh', p.sdob || '-'],
            ['Số điện thoại', p.phoneNumber || '-'],
            ['Trạng thái', p.status || '-'],
            ['UID', p.userId || detailUid],
        ];
        $('friendsDetailRows').innerHTML = rows.map(function (r) {
            return '<div class="friends-detail-row"><span>' + esc(r[0]) + '</span><b>' + esc(r[1]) + '</b></div>';
        }).join('');
    }

    async function friendsOpenDetail(uid) {
        var f = findFriend(uid);
        if (!f) return;
        detailUid = uid;
        $('friendsDetailAvatar').innerHTML = avatarBlock(f);
        $('friendsDetailName').textContent = f.zaloName || f.displayName || 'Không tên';
        $('friendsDetailUid').textContent = 'UID: ' + uid;
        renderDetailRows(f);
        $('friendsDetailOverlay').classList.add('open');
        // Enrich bằng profile chi tiết (giống nút Xem ở trang Lấy thành viên).
        var loadingEl = $('friendsDetailLoading');
        if (loadingEl) loadingEl.style.display = 'block';
        try {
            var res = await fetch('/api/single-profile', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ uid: uid, accountId: selectedAccountId() })
            });
            var data = await res.json();
            if (detailUid !== uid) return;
            if (data && data.profile) {
                var merged = Object.assign({}, f, data.profile);
                merged.userId = merged.userId || uid;
                renderDetailRows(merged);
                // Cập nhật lại dòng trong bảng (giới tính/ngày sinh/SĐT mới lấy được).
                Object.assign(f, merged);
                friendsRenderTable();
            } else if (data && data.error) {
                showStatus('Profile chi tiết đang bị giới hạn: ' + data.error, false);
            }
        } catch (err) {
            showStatus('Không lấy được profile chi tiết: ' + err.message, false);
        } finally {
            if (detailUid === uid && loadingEl) loadingEl.style.display = 'none';
        }
    }

    function friendsCloseDetail() {
        detailUid = '';
        $('friendsDetailOverlay').classList.remove('open');
    }

    // ─── Nhắn tin ────────────────────────────────────────────────────────
    var msgUid = '';

    function friendsOpenMsg(uid) {
        var f = findFriend(uid);
        if (!f) return;
        msgUid = uid;
        $('friendsMsgAvatar').innerHTML = avatarBlock(f);
        $('friendsMsgName').textContent = f.zaloName || f.displayName || 'Không tên';
        $('friendsMsgText').value = '';
        $('friendsMsgOverlay').classList.add('open');
        setTimeout(function () { $('friendsMsgText').focus(); }, 50);
    }

    function friendsCloseMsg() {
        msgUid = '';
        $('friendsMsgOverlay').classList.remove('open');
    }

    async function friendsSendMessage() {
        var text = String($('friendsMsgText').value || '').trim();
        if (!msgUid) return;
        if (!text) { showStatus('Chưa nhập nội dung tin nhắn.', false); return; }
        var btn = $('friendsMsgSendBtn');
        var old = btn.textContent;
        btn.disabled = true; btn.textContent = 'Đang gửi...';
        try {
            var res = await fetch('/api/send-sms', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ accountId: selectedAccountId(), to_uid: msgUid, message: text })
            });
            var data = await res.json();
            if (!data.success) throw new Error(data.error || 'Gửi thất bại');
            showStatus('Đã gửi tin nhắn.', true);
            friendsCloseMsg();
        } catch (err) {
            showStatus('Gửi tin nhắn lỗi: ' + err.message, false);
        } finally {
            btn.disabled = false; btn.textContent = old;
        }
    }

    // ─── Xóa bạn (bấm 2 lần để xác nhận) ────────────────────────────────
    var DEL_ICON = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>';

    function friendsRemoveClick(btn, uid) {
        if (!btn.classList.contains('confirming')) {
            btn.classList.add('confirming');
            btn.innerHTML = DEL_ICON + 'Chắc chắn?';
            setTimeout(function () {
                btn.classList.remove('confirming');
                btn.innerHTML = DEL_ICON + 'Xóa';
            }, 3000);
            return;
        }
        btn.disabled = true;
        btn.innerHTML = DEL_ICON + 'Đang xóa...';
        fetch('/api/friends/remove', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ accountId: selectedAccountId(), userId: uid })
        }).then(function (res) { return res.json(); }).then(function (data) {
            if (!data.success) throw new Error(data.error || 'Xóa thất bại');
            var f = findFriend(uid);
            friends = friends.filter(function (x) { return x.userId !== uid; });
            friendsRenderTable();
            showStatus('Đã xóa kết bạn với ' + ((f && f.zaloName) || uid) + '.', true);
        }).catch(function (err) {
            btn.disabled = false;
            btn.classList.remove('confirming');
            btn.innerHTML = DEL_ICON + 'Xóa';
            showStatus('Xóa bạn lỗi: ' + err.message, false);
        });
    }

    function friendsReload() {
        friendsViewMode = 'list';
        var wrap = $('friendsPhoneResultsWrap');
        if (wrap) wrap.style.display = 'none';
        loadFriends();
    }

    // ─── Kết bạn theo số điện thoại ─────────────────────────────────────
    var MAX_PHONE_FIND = 10;
    var phoneResults = [];
    var friendsViewMode = 'list';   // 'list' = danh sách bạn bè, 'phone' = kết quả tìm theo SĐT

    function friendsNormalizePhone(p) {
        p = String(p || '').replace(/[^0-9+]/g, '');
        if (p.indexOf('+') === 0) p = p.slice(1);
        if (p.indexOf('0084') === 0) p = '84' + p.slice(4);
        if (p.indexOf('0') === 0) p = '84' + p.slice(1);
        return p;
    }

    function friendsSetPhoneProgress(pct, show) {
        var wrap = $('friendsPhoneProgress');
        if (!wrap) return;
        wrap.style.display = show ? 'flex' : 'none';
        var bar = wrap.querySelector('.ptp-track > span');
        if (bar) bar.style.width = Math.max(0, Math.min(100, pct || 0)) + '%';
        var lbl = $('friendsPhoneProgressPct');
        if (lbl) lbl.textContent = Math.round(pct || 0) + '%';
    }

    function friendsFindByPhone() {
        var aid = selectedAccountId();
        if (!aid) { showStatus('Chưa chọn tài khoản.', false); return; }
        var raw = String(($('friendsPhoneInput') || {}).value || '');
        var seen = {};
        var phones = raw.split(/\n|,|;/).map(function (p) { return String(p || '').trim(); })
            .filter(function (p) {
                if (!p) return false;
                var n = friendsNormalizePhone(p);
                if (!n || seen[n]) return false;
                seen[n] = 1;
                return true;
            });
        if (!phones.length) { showStatus('Chưa nhập số điện thoại.', false); return; }
        if (phones.length > MAX_PHONE_FIND) {
            showStatus('Tối đa ' + MAX_PHONE_FIND + ' số mỗi lần. Hiện có ' + phones.length + ' số.', false);
            return;
        }

        phoneResults = [];
        friendsViewMode = 'phone';
        var wrap = $('friendsPhoneResultsWrap');
        if (wrap) wrap.style.display = 'block';
        friendsRenderTable();

        var btn = $('friendsPhoneFindBtn');
        var orig = btn ? btn.innerHTML : '';
        if (btn) { btn.disabled = true; btn.innerHTML = 'Đang tìm...'; }
        friendsSetPhoneProgress(0, true);

        fetch('/api/schedules/lookup-phones', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ accountId: aid, account_id: aid, phones: phones })
        }).then(function (r) { return r.json(); }).then(function (j) {
            if (j.error || j.success === false) {
                friendsSetPhoneProgress(0, false);
                if (btn) { btn.disabled = false; btn.innerHTML = orig; }
                showStatus(j.error || 'Không tạo được tác vụ tìm.', false);
                return;
            }
            if (j.taskId) { friendsPollPhoneTask(j.taskId, btn, orig); return; }
            friendsSetPhoneProgress(100, true);
            if (btn) { btn.disabled = false; btn.innerHTML = orig; }
            friendsApplyPhoneResults(j.results || []);
            setTimeout(function () { friendsSetPhoneProgress(0, false); }, 1200);
        }).catch(function (err) {
            friendsSetPhoneProgress(0, false);
            if (btn) { btn.disabled = false; btn.innerHTML = orig; }
            showStatus('Lỗi tìm theo số: ' + (err.message || err), false);
        });
    }

    function friendsPollPhoneTask(taskId, btn, orig) {
        var tries = 0;
        function done() { if (btn) { btn.disabled = false; btn.innerHTML = orig; } }
        function poll() {
            tries += 1;
            fetch('/api/tasks/' + encodeURIComponent(taskId)).then(function (r) { return r.json(); }).then(function (j) {
                if (j.error || j.success === false) { friendsSetPhoneProgress(0, false); done(); showStatus(j.error || 'Không đọc được task.', false); return; }
                var task = j.task || {};
                var status = task.status || '';
                var progress = task.progress || 0;
                friendsSetPhoneProgress(progress, true);
                if (status === 'failed') { friendsSetPhoneProgress(0, false); done(); showStatus(task.error || 'Tìm theo số thất bại.', false); return; }
                if (status === 'completed') {
                    var result = task.result || {};
                    friendsSetPhoneProgress(100, true); done();
                    friendsApplyPhoneResults(result.results || []);
                    var ok = result.success_count || (result.results || []).filter(function (r) { return !!r.success; }).length;
                    showStatus('Tìm xong: ' + ok + '/' + (result.results || []).length + ' số có thông tin.', true);
                    setTimeout(function () { friendsSetPhoneProgress(0, false); }, 1500);
                    return;
                }
                if (tries < 600) setTimeout(poll, 1000);
                else { friendsSetPhoneProgress(0, false); done(); showStatus('Tìm quá lâu, thử lại.', false); }
            }).catch(function (err) { friendsSetPhoneProgress(0, false); done(); showStatus('Lỗi: ' + (err.message || err), false); });
        }
        poll();
    }

    function friendsApplyPhoneResults(results) {
        phoneResults = (results || []).map(function (r) {
            var p = r.profile || {};
            return {
                success: !!r.success,
                phone: r.phone || r.normalizedPhone || '',
                userId: String(p.userId || p.uid || r.userId || '').trim(),
                zaloName: p.zaloName || p.displayName || r.zaloName || '',
                avatar: p.avatar || r.avatar || '',
                error: r.error || '',
                added: false
            };
        });
        friendsViewMode = 'phone';
        var wrap = $('friendsPhoneResultsWrap');
        if (wrap) wrap.style.display = 'block';
        friendsRenderTable();
    }

    // Render kết quả tìm theo SĐT vào CHÍNH bảng bạn bè (1 bảng duy nhất).
    function friendsRenderPhoneRows() {
        var body = $('friendsBody');
        var empty = $('friendsEmpty');
        if (!body) return;
        var ok = phoneResults.filter(function (r) { return r.success && r.userId; }).length;
        if ($('friendsPhoneOkCount')) $('friendsPhoneOkCount').textContent = ok;
        if ($('friendsPhoneTotal')) $('friendsPhoneTotal').textContent = phoneResults.length;
        if ($('friendsTotal')) $('friendsTotal').textContent = phoneResults.length;
        if ($('friendsShown')) $('friendsShown').textContent = '· tìm theo SĐT';

        if (!phoneResults.length) {
            body.innerHTML = '';
            if (empty) { setEmptyHint('Chưa có kết quả. Dán số điện thoại và bấm "Tìm".'); empty.style.display = 'flex'; }
            return;
        }
        if (empty) empty.style.display = 'none';

        body.innerHTML = phoneResults.map(function (r, idx) {
            var name = r.zaloName || '-';
            var av = normalizeAvatar(r.avatar);
            var avatarHtml = av
                ? '<img class="friends-avatar" src="' + esc(av) + '" alt="">'
                : '<div class="friends-avatar fallback">' + esc((name.charAt(0) || '?').toUpperCase()) + '</div>';
            var okRow = r.success && r.userId;
            var statusHtml = okRow
                ? '<span style="color:var(--green,#2fae6b)">✓ Tìm được</span>'
                : '<span class="friends-muted" title="' + esc(r.error || '') + '">Không tìm được</span>';
            var action;
            if (!okRow) {
                action = '<span class="friends-muted">-</span>';
            } else if (r.added) {
                action = '<button type="button" class="friends-phone-chip done" disabled>✓ Đã gửi</button>';
            } else {
                action = '<button type="button" class="friends-phone-chip" data-act="addphone" data-idx="' + idx + '">➕ Kết bạn</button>';
            }
            return '<tr style="opacity:' + (okRow ? '1' : '.6') + '">'
                + '<td class="friends-muted">' + (idx + 1) + '</td>'
                + '<td>' + avatarHtml + '</td>'
                + '<td><b>' + esc(name) + '</b></td>'
                + '<td>' + statusHtml + '</td>'
                + '<td class="friends-muted">-</td>'
                + '<td class="friends-muted">-</td>'
                + '<td class="friends-muted" style="font-family:monospace;font-size:12px">' + esc(r.phone || '-') + '</td>'
                + '<td style="text-align:right;padding-right:18px">' + action + '</td>'
                + '</tr>';
        }).join('');
    }

    function friendsShowList() {
        friendsViewMode = 'list';
        var wrap = $('friendsPhoneResultsWrap');
        if (wrap) wrap.style.display = 'none';
        if ($('friendsTotal')) $('friendsTotal').textContent = friends.length;
        if ($('friendsShown')) $('friendsShown').textContent = '';
        friendsRenderTable();
    }

    function friendsAddByPhone(idx, btn) {
        var r = phoneResults[idx];
        if (!r || !r.userId) return;
        var aid = selectedAccountId();
        if (!aid) { showStatus('Chưa chọn tài khoản.', false); return; }
        btn.disabled = true;
        btn.textContent = 'Đang gửi...';
        fetch('/api/send-friend-request', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ accountId: aid, toid: r.userId, toId: r.userId, message: '' })
        }).then(function (res) { return res.json(); }).then(function (data) {
            if (data && (data.success || data.ok)) {
                r.added = true;
                friendsRenderPhoneRows();
                showStatus('Đã gửi kết bạn tới ' + (r.zaloName || r.phone) + '.', true);
            } else {
                throw new Error((data && (data.error || data.message)) || 'Gửi kết bạn thất bại');
            }
        }).catch(function (err) {
            btn.disabled = false;
            btn.textContent = '➕ Kết bạn';
            showStatus('Kết bạn lỗi: ' + (err.message || err), false);
        });
    }

    document.addEventListener('click', function (e) {
        var b = e.target.closest('#friendsBody [data-act="addphone"]');
        if (b) { e.stopPropagation(); friendsAddByPhone(parseInt(b.dataset.idx, 10), b); }
    });

    window.friendsRenderTable = friendsRenderTable;
    window.friendsReload = friendsReload;
    window.friendsCloseDetail = friendsCloseDetail;
    window.friendsCloseMsg = friendsCloseMsg;
    window.friendsSendMessage = friendsSendMessage;
    window.friendsFindByPhone = friendsFindByPhone;
    window.friendsShowList = friendsShowList;

    document.addEventListener('DOMContentLoaded', function () {
        loadAccounts();
        var dBtn = $('friendsDetailMsgBtn');
        if (dBtn) {
            dBtn.addEventListener('click', function () {
                var uid = detailUid;
                friendsCloseDetail();
                if (uid) friendsOpenMsg(uid);
            });
        }
        // Bấm ra ngoài modal thì đóng.
        ['friendsDetailOverlay', 'friendsMsgOverlay'].forEach(function (id) {
            var ov = $(id);
            if (ov) ov.addEventListener('click', function (e) {
                if (e.target === ov) ov.classList.remove('open');
            });
        });
    });
})();
