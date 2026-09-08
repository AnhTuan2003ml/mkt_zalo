(function () {
    var state = {
        checked: false,
        canApply: false
    };

    function $(id) {
        return document.getElementById(id);
    }

    function setText(id, text) {
        var el = $(id);
        if (el) el.textContent = text;
    }

    function setStatus(message, type) {
        var el = $('nexusUpdateStatus');
        if (!el) return;
        el.textContent = message || '';
        el.className = 'nexus-update-status ' + (type || '');
    }

    function setBusy(isBusy, text) {
        var checkBtn = $('nexusUpdateBtn');
        var applyBtn = $('nexusApplyUpdateBtn');
        if (checkBtn) {
            checkBtn.disabled = !!isBusy;
            checkBtn.textContent = text || (isBusy ? 'Đang kiểm tra...' : 'Cập nhật');
        }
        if (applyBtn) applyBtn.disabled = !!isBusy || !state.canApply;
    }

    function openModal() {
        var modal = $('nexusUpdateBackdrop');
        if (!modal) return;
        modal.classList.add('show');
        modal.setAttribute('aria-hidden', 'false');
    }

    function closeModal() {
        var modal = $('nexusUpdateBackdrop');
        if (!modal) return;
        modal.classList.remove('show');
        modal.setAttribute('aria-hidden', 'true');
    }

    async function checkAndOpen() {
        openModal();
        setBusy(true, 'Đang kiểm tra...');
        setStatus('Đang kiểm tra release mới nhất trên GitHub...', 'loading');

        try {
            var res = await fetch('/api/updater/check', { method: 'POST' });
            var data = await res.json();
            if (!data.success) throw new Error(data.error || 'Không kiểm tra được cập nhật.');

            state.checked = true;
            state.canApply = data.state === 'ready';
            setText('nexusUpdateHint', 'Kiểm tra phiên bản');
            setText('nexusUpdateVersion', 'Đang dùng v' + (data.currentVersion || '—') + (state.canApply ? ' · Có v' + (data.latestVersion || 'mới') : ' · Đã mới nhất'));
            var updateDot = $('nexusUpdateDot');
            if (updateDot) updateDot.hidden = !state.canApply;

            if (state.canApply) {
                setStatus(data.message || 'Có bản mới. Bấm "Tải và cập nhật" để tải gói cập nhật.', 'success');
            } else {
                setStatus(data.message || 'Bạn đang dùng phiên bản mới nhất.', 'success');
            }
        } catch (err) {
            state.canApply = false;
            setText('nexusUpdateHint', 'Kiểm tra phiên bản');
            setText('nexusUpdateVersion', 'Không kiểm tra được phiên bản mới');
            setStatus(err.message || 'Lỗi kiểm tra cập nhật.', 'error');
        } finally {
            setBusy(false);
        }
    }

    async function applyUpdate() {
        if (!state.canApply) return;

        setBusy(true, 'Đang cập nhật...');
        setStatus('Đang tải Nexus.zip và chuẩn bị thay Nexus.exe...', 'loading');

        try {
            var res = await fetch('/api/updater/apply', { method: 'POST' });
            var data = await res.json();
            if (!data.success) throw new Error(data.error || 'Cập nhật thất bại.');

            if (data.state === 'none') {
                state.canApply = false;
                setStatus(data.message || 'Bạn đang dùng phiên bản mới nhất.', 'success');
                setText('nexusUpdateHint', 'Đã mới nhất');
                setBusy(false);
                return;
            }

            if (data.state === 'restart') {
                setStatus(data.message || 'Đã tải xong. Ứng dụng sẽ tự khởi động lại để hoàn tất cập nhật.', 'success');
            } else {
                state.canApply = false;
                setText('nexusUpdateHint', 'Đã cập nhật');
                setStatus(data.message || 'Đã cập nhật Nexus.exe.', 'success');
            }
        } catch (err) {
            setStatus(err.message || 'Cập nhật thất bại.', 'error');
            setBusy(false);
        }
    }

    // ─── Tự động kiểm tra + thông báo (không tự tải/áp dụng bản cập nhật) ────
    var AUTO_CHECK_MIN_INTERVAL_MS = 30 * 60 * 1000; // tối đa 1 lần / 30 phút, tránh gọi GitHub API quá nhiều
    var LAST_CHECK_KEY = 'nexus_update_last_autocheck';
    var DISMISSED_KEY = 'nexus_update_dismissed_version';

    function storageGet(key) {
        try { return window.localStorage ? window.localStorage.getItem(key) : null; }
        catch (e) { return null; }
    }
    function storageSet(key, value) {
        try { if (window.localStorage) window.localStorage.setItem(key, value); }
        catch (e) { /* bỏ qua nếu trình duyệt chặn storage */ }
    }

    function showUpdateToast(latestVersion, message) {
        var toast = $('nexusUpdateToast');
        if (!toast) return;
        toast.dataset.latestVersion = latestVersion || '';
        setText('nexusUpdateToastText', message || ('Đã có bản cập nhật mới' + (latestVersion ? ' v' + latestVersion : '') + '.'));
        toast.hidden = false;
        toast.classList.add('show');
    }

    function hideUpdateToast() {
        var toast = $('nexusUpdateToast');
        if (!toast) return;
        toast.classList.remove('show');
        window.setTimeout(function () { toast.hidden = true; }, 200);
    }

    function dismissUpdateToast() {
        var toast = $('nexusUpdateToast');
        var latestVersion = toast ? toast.dataset.latestVersion : '';
        if (latestVersion) storageSet(DISMISSED_KEY, latestVersion);
        hideUpdateToast();
    }

    async function autoCheck(force) {
        if (!force) {
            var last = Number(storageGet(LAST_CHECK_KEY) || 0);
            if (Date.now() - last < AUTO_CHECK_MIN_INTERVAL_MS) return;
        }
        storageSet(LAST_CHECK_KEY, String(Date.now()));

        try {
            var res = await fetch('/api/updater/check', { method: 'GET' });
            var data = await res.json();
            if (!data.success) return;

            var hasUpdate = data.state === 'ready';
            setText('nexusUpdateHint', 'Kiểm tra phiên bản');
            setText('nexusUpdateVersion', 'Đang dùng v' + (data.currentVersion || '—') + (hasUpdate ? ' · Có v' + (data.latestVersion || 'mới') : ' · Đã mới nhất'));
            var autoUpdateDot = $('nexusUpdateDot');
            if (autoUpdateDot) autoUpdateDot.hidden = !hasUpdate;
            state.canApply = hasUpdate;

            if (!hasUpdate) return;
            var latestVersion = data.latestVersion || '';
            var alreadyDismissed = latestVersion && storageGet(DISMISSED_KEY) === latestVersion;
            if (alreadyDismissed) return;

            showUpdateToast(latestVersion, data.message);
        } catch (err) {
            // Kiểm tra tự động thất bại (mất mạng, GitHub lỗi...): im lặng, không làm phiền người dùng.
            console.warn('[updater] auto check lỗi:', err.message || err);
        }
    }

    window.NexusUpdater = {
        checkAndOpen: checkAndOpen,
        apply: applyUpdate,
        close: closeModal,
        autoCheck: autoCheck,
        openFromToast: function () {
            hideUpdateToast();
            checkAndOpen();
        },
        dismissToast: dismissUpdateToast
    };

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') closeModal();
    });

    document.addEventListener('DOMContentLoaded', function () {
        // Kiểm tra tự động ngay khi tải trang (có giới hạn tần suất ở autoCheck).
        autoCheck(false);
    });
})();
