(function () {
    'use strict';

    if (window.__NEXUS_INTERACTIONS_LOADED__) return;
    window.__NEXUS_INTERACTIONS_LOADED__ = true;

    function sidebar() { return document.querySelector('.app-sidebar'); }
    function toggleButtons() { return document.querySelectorAll('[data-sidebar-toggle]'); }

    function setSidebar(open) {
        document.body.classList.toggle('nexus-sidebar-open', !!open);
        toggleButtons().forEach(function (button) {
            button.setAttribute('aria-expanded', open ? 'true' : 'false');
            button.setAttribute('aria-label', open ? 'Đóng menu' : 'Mở menu');
        });
    }

    function addRipple(event, target) {
        if (!target || target.disabled || target.getAttribute('aria-disabled') === 'true') return;
        var rect = target.getBoundingClientRect();
        var diameter = Math.max(rect.width, rect.height) * 1.35;
        var ripple = document.createElement('span');
        ripple.className = 'nexus-ripple';
        ripple.style.width = diameter + 'px';
        ripple.style.height = diameter + 'px';
        ripple.style.left = (event.clientX - rect.left - diameter / 2) + 'px';
        ripple.style.top = (event.clientY - rect.top - diameter / 2) + 'px';
        target.appendChild(ripple);
        window.setTimeout(function () { ripple.remove(); }, 620);
    }

    function bindRipple(root) {
        root.addEventListener('pointerdown', function (event) {
            if (event.button !== 0) return;
            var target = event.target.closest('.btn, .header-nav a, .sidebar-mini-link, .sidebar-policy-link, .theme-toggle, .nexus-mobile-menu-btn, .nexus-mobile-theme, .dashboard-metric, .dashboard-quick-grid a, .group-copy-mode-switch button');
            if (!target) return;
            target.classList.add('nexus-ripple-host');
            addRipple(event, target);
        });
    }

    function bindSidebar() {
        document.addEventListener('click', function (event) {
            if (event.target.closest('[data-sidebar-toggle]')) {
                setSidebar(!document.body.classList.contains('nexus-sidebar-open'));
                return;
            }
            if (event.target.closest('[data-sidebar-close]')) {
                setSidebar(false);
                return;
            }
            if (window.innerWidth <= 900 && event.target.closest('.header-nav a, .sidebar-mini-link, .sidebar-policy-link')) {
                setSidebar(false);
            }
        });

        document.addEventListener('keydown', function (event) {
            if (event.key === 'Escape') setSidebar(false);
        });

        window.addEventListener('resize', function () {
            if (window.innerWidth > 900) setSidebar(false);
        });
    }

    function labelIconButtons() {
        document.querySelectorAll('button.btn-icon:not([aria-label])').forEach(function (button) {
            var title = button.getAttribute('title');
            if (title) button.setAttribute('aria-label', title);
        });
    }

    function init() {
        bindRipple(document);
        bindSidebar();
        labelIconButtons();
        document.documentElement.classList.add('nexus-ui-ready');
        if (sidebar()) sidebar().setAttribute('tabindex', '-1');
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
    else init();

    // ─── Overlay confirm — thay thế window.confirm() của trình duyệt ─────────
    function escapeHtml(str) {
        var div = document.createElement('div');
        div.textContent = String(str == null ? '' : str);
        return div.innerHTML;
    }

    function nexusConfirm(message, options) {
        options = options || {};
        var title = options.title || 'Xác nhận';
        var confirmText = options.confirmText || 'Đồng ý';
        var cancelText = options.cancelText || 'Hủy';
        var danger = !!options.danger;

        return new Promise(function (resolve) {
            var backdrop = document.getElementById('nexusConfirmBackdrop');
            if (backdrop) backdrop.remove();

            backdrop = document.createElement('div');
            backdrop.id = 'nexusConfirmBackdrop';
            backdrop.className = 'overlay-backdrop';
            backdrop.innerHTML =
                '<div class="overlay-card" style="max-width:420px;">' +
                    '<div class="overlay-header"><h3>' + escapeHtml(title) + '</h3></div>' +
                    '<div class="overlay-body">' +
                        '<p style="margin:0 0 20px;color:var(--text-secondary,#aab4c4);font-size:13px;line-height:1.6;white-space:pre-line;">' + escapeHtml(message) + '</p>' +
                        '<div style="display:flex;gap:10px;justify-content:flex-end;">' +
                            '<button type="button" class="btn btn-ghost btn-sm" data-nexus-confirm-cancel>' + escapeHtml(cancelText) + '</button>' +
                            '<button type="button" class="btn btn-sm ' + (danger ? 'btn-danger' : 'btn-primary') + '" data-nexus-confirm-ok>' + escapeHtml(confirmText) + '</button>' +
                        '</div>' +
                    '</div>' +
                '</div>';
            document.body.appendChild(backdrop);

            function cleanup(result) {
                backdrop.classList.remove('show');
                window.setTimeout(function () { backdrop.remove(); }, 200);
                document.removeEventListener('keydown', onKeydown);
                resolve(result);
            }

            function onKeydown(event) {
                if (event.key === 'Escape') cleanup(false);
            }

            backdrop.addEventListener('click', function (event) {
                if (event.target === backdrop) cleanup(false);
            });
            backdrop.querySelector('[data-nexus-confirm-cancel]').addEventListener('click', function () { cleanup(false); });
            backdrop.querySelector('[data-nexus-confirm-ok]').addEventListener('click', function () { cleanup(true); });
            document.addEventListener('keydown', onKeydown);

            window.requestAnimationFrame(function () { backdrop.classList.add('show'); });
        });
    }

    window.nexusConfirm = nexusConfirm;
    window.NexusInteractions = { setSidebar: setSidebar, confirm: nexusConfirm };
}());
