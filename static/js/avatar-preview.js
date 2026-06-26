/**
 * Avatar Preview
 * Click #pfAvatar -> call /api/get-avatar -> utils/get_avata.py -> bk_full_avatar
 */

var AvatarPreviewState = {
    isAvatarPreviewOpen: false,
    previewAvatarUrl: ''
};

var AvatarPreview = (function() {
    var _backdrop = null;
    var _img = null;
    var _loading = false;

    function normalizeUrl(url) {
        if (!url) return '';
        url = String(url).trim();
        if (url.indexOf('//') === 0) return 'https:' + url;
        return url;
    }

    function cleanUid(uidText) {
        var uid = String(uidText || '').trim();

        if (uid.toLowerCase().indexOf('id:') === 0) {
            uid = uid.split(':').slice(1).join(':').trim();
        }

        var m = uid.match(/\d{8,}/);
        if (m) return m[0];

        return uid;
    }

    function getCurrentUid() {
        var pfUid = document.getElementById('pfUid');
        if (!pfUid) return '';
        return cleanUid(pfUid.textContent || pfUid.innerText || '');
    }

    function getCurrentAccountId() {
        var hidden = document.getElementById('memberAccountId');
        if (hidden && hidden.value) return hidden.value.trim();

        var saved = localStorage.getItem('zalo_members_accountId');
        if (saved) return saved.trim();

        return '';
    }

    async function fetchFullAvatar(userId, accountId) {
        userId = cleanUid(userId);
        accountId = String(accountId || '').trim();

        if (!userId || !accountId) {
            console.warn('[AvatarPreview] missing userId/accountId', userId, accountId);
            return '';
        }

        var response = await fetch('/api/get-avatar', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                fid: userId,
                uid: userId,
                userId: userId,
                accountId: accountId,
                account_id: accountId
            })
        });

        var text = await response.text();
        var result = null;

        try {
            result = JSON.parse(text);
        } catch (e) {
            console.error('[AvatarPreview] API trả về không phải JSON:', text.slice(0, 300));
            return '';
        }

        if (!response.ok || !result.success) {
            console.error('[AvatarPreview] API error:', result.error || result);
            return '';
        }

        return normalizeUrl(
            result.bk_full_avatar ||
            result.avatar_url ||
            result.full_avatar ||
            ''
        );
    }

    function open(url) {
        if (!_backdrop || !_img) return;

        url = normalizeUrl(url);
        if (!url) return;

        AvatarPreviewState.isAvatarPreviewOpen = true;
        AvatarPreviewState.previewAvatarUrl = url;

        _img.src = url;
        _img.alt = 'Avatar full size';

        _backdrop.hidden = false;
        _backdrop.setAttribute('aria-hidden', 'false');
        _backdrop.classList.add('is-open');
    }

    function close() {
        if (!_backdrop || !_img) return;

        AvatarPreviewState.isAvatarPreviewOpen = false;
        AvatarPreviewState.previewAvatarUrl = '';

        _backdrop.classList.remove('is-open');
        _backdrop.hidden = true;
        _backdrop.setAttribute('aria-hidden', 'true');
        _img.removeAttribute('src');
    }

    async function handlePfAvatarClick(e) {
        e.stopPropagation();

        var pfAvatar = document.getElementById('pfAvatar');
        if (!pfAvatar || !pfAvatar.src || pfAvatar.style.display === 'none') return;

        if (_loading) return;
        _loading = true;

        var oldTitle = pfAvatar.title || '';
        pfAvatar.title = 'Đang lấy ảnh full size...';

        try {
            var uid = getCurrentUid();
            var accountId = getCurrentAccountId();

            console.log('[AvatarPreview] click avatar uid=', uid, 'accountId=', accountId);

            var fullUrl = await fetchFullAvatar(uid, accountId);

            if (fullUrl) {
                // Lưu lại full avatar vào chính thẻ pfAvatar để lần sau dùng URL mới
                pfAvatar.dataset.fullAvatar = fullUrl;
                open(fullUrl);
            } else {
                // Fallback cuối cùng mới dùng ảnh thumbnail cũ
                open(pfAvatar.src);
            }
        } catch (err) {
            console.error('[AvatarPreview] lỗi lấy full avatar:', err);
            open(pfAvatar.src);
        } finally {
            pfAvatar.title = oldTitle || 'Nhấn để xem ảnh full size';
            _loading = false;
        }
    }

    function bindPfAvatar() {
        var pfAvatar = document.getElementById('pfAvatar');
        if (!pfAvatar) return;

        if (pfAvatar.dataset.avatarPreviewBound === '1') return;

        pfAvatar.dataset.avatarPreviewBound = '1';
        pfAvatar.style.cursor = 'zoom-in';
        pfAvatar.title = 'Nhấn để xem ảnh full size';
        pfAvatar.addEventListener('click', handlePfAvatarClick);
    }

    function init() {
        _backdrop = document.getElementById('avatarPreviewBackdrop');
        _img = document.getElementById('avatarPreviewImg');

        if (_backdrop) {
            _backdrop.addEventListener('click', function(e) {
                if (e.target === _backdrop) close();
            });
        }

        if (_img) {
            _img.addEventListener('click', function(e) {
                e.stopPropagation();
                close();
            });
        }

        bindPfAvatar();
    }

    function isOpen() {
        return AvatarPreviewState.isAvatarPreviewOpen;
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    return {
        open: open,
        close: close,
        isOpen: isOpen,
        init: init,
        bindPfAvatar: bindPfAvatar,
        fetchFullAvatar: fetchFullAvatar
    };
})();
