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
            setText('nexusUpdateHint', state.canApply ? 'Có bản mới' : 'Đã mới nhất');

            if (state.canApply) {
                setStatus(data.message || 'Có bản mới. Bấm "Tải và cập nhật" để tải gói cập nhật.', 'success');
            } else {
                setStatus(data.message || 'Bạn đang dùng phiên bản mới nhất.', 'success');
            }
        } catch (err) {
            state.canApply = false;
            setText('nexusUpdateHint', 'Kiểm tra lỗi');
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

    window.NexusUpdater = {
        checkAndOpen: checkAndOpen,
        apply: applyUpdate,
        close: closeModal
    };

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') closeModal();
    });
})();
