(function () {
    'use strict';

    if (window.__NEXUS_THEME_LOADED__) return;
    window.__NEXUS_THEME_LOADED__ = true;

    var STORAGE_KEY = 'nexus-theme';
    // Chỉ còn chế độ tối — bỏ chế độ sáng theo yêu cầu người dùng.
    var THEMES = { dark: true };

    function safeGetTheme() {
        try {
            var saved = localStorage.getItem(STORAGE_KEY);
            return THEMES[saved] ? saved : 'dark';
        } catch (error) {
            return 'dark';
        }
    }

    function safeSaveTheme(theme) {
        try {
            localStorage.setItem(STORAGE_KEY, theme);
        } catch (error) {
            // localStorage may be unavailable in a locked-down webview. Theme still works for this session.
        }
    }

    function getTheme() {
        var current = document.documentElement.getAttribute('data-theme');
        return THEMES[current] ? current : safeGetTheme();
    }

    function themeLabel(theme) {
        return theme === 'dark' ? 'Chế độ tối' : 'Chế độ sáng';
    }

    function updateThemeControls(theme) {
        document.querySelectorAll('[data-theme-toggle]').forEach(function (button) {
            var targetTheme = theme === 'dark' ? 'light' : 'dark';
            button.setAttribute('aria-label', 'Chuyển sang ' + themeLabel(targetTheme).toLowerCase());
            button.setAttribute('title', 'Chuyển sang ' + themeLabel(targetTheme).toLowerCase());
            button.setAttribute('aria-pressed', theme === 'dark' ? 'true' : 'false');

            var label = button.querySelector('[data-theme-label]');
            if (label) label.textContent = themeLabel(theme);

            var description = button.querySelector('[data-theme-description]');
            if (description) description.textContent = theme === 'dark' ? 'Dịu mắt khi làm việc lâu' : 'Rõ ràng trong môi trường sáng';

            var sunIcon = button.querySelector('[data-theme-icon="sun"]');
            var moonIcon = button.querySelector('[data-theme-icon="moon"]');
            if (sunIcon) sunIcon.hidden = theme !== 'light';
            if (moonIcon) moonIcon.hidden = theme !== 'dark';
        });
    }

    function applyTheme(theme, persist) {
        theme = THEMES[theme] ? theme : 'dark';
        document.documentElement.setAttribute('data-theme', theme);
        document.documentElement.style.colorScheme = theme;
        if (document.body) document.body.setAttribute('data-theme', theme);
        if (persist !== false) safeSaveTheme(theme);
        updateThemeControls(theme);
        return theme;
    }

    function toggleTheme() {
        return applyTheme('dark', true);
    }

    function quickPanel() {
        return document.getElementById('nexusQuickActionsBackdrop');
    }

    function quickSearch() {
        return document.getElementById('nexusQuickSearch');
    }

    function filterQuickActions(value) {
        var query = String(value || '').trim().toLocaleLowerCase('vi');
        var visibleCount = 0;
        document.querySelectorAll('[data-quick-action]').forEach(function (item) {
            var haystack = String(item.getAttribute('data-search') || item.textContent || '').toLocaleLowerCase('vi');
            var visible = !query || haystack.indexOf(query) !== -1;
            item.hidden = !visible;
            if (visible) visibleCount += 1;
        });

        var empty = document.getElementById('nexusQuickEmpty');
        if (empty) empty.hidden = visibleCount !== 0;
    }

    function openQuickActions() {
        var backdrop = quickPanel();
        if (!backdrop) return;
        backdrop.classList.add('show');
        backdrop.setAttribute('aria-hidden', 'false');
        document.body.classList.add('nexus-modal-open');
        var input = quickSearch();
        if (input) {
            input.value = '';
            filterQuickActions('');
            window.setTimeout(function () { input.focus(); }, 30);
        }
    }

    function closeQuickActions() {
        var backdrop = quickPanel();
        if (!backdrop) return;
        backdrop.classList.remove('show');
        backdrop.setAttribute('aria-hidden', 'true');
        document.body.classList.remove('nexus-modal-open');
    }

    function bindUi() {
        document.documentElement.classList.add('nexus-theme-ready');
        applyTheme(getTheme(), false);

        document.querySelectorAll('[data-theme-toggle]').forEach(function (button) {
            if (button.dataset.themeBound === '1') return;
            button.dataset.themeBound = '1';
            button.addEventListener('click', toggleTheme);
        });

        var input = quickSearch();
        if (input && input.dataset.quickBound !== '1') {
            input.dataset.quickBound = '1';
            input.addEventListener('input', function () { filterQuickActions(input.value); });
        }

        var backdrop = quickPanel();
        if (backdrop && backdrop.dataset.quickBound !== '1') {
            backdrop.dataset.quickBound = '1';
            backdrop.addEventListener('click', function (event) {
                if (event.target === backdrop) closeQuickActions();
            });
        }

        document.addEventListener('keydown', function (event) {
            var key = String(event.key || '').toLowerCase();
            if ((event.ctrlKey || event.metaKey) && key === 'k') {
                event.preventDefault();
                if (quickPanel() && quickPanel().classList.contains('show')) closeQuickActions();
                else openQuickActions();
                return;
            }
            if (event.key === 'Escape') closeQuickActions();
        });
    }

    applyTheme(safeGetTheme(), false);

    window.NexusUI = {
        applyTheme: applyTheme,
        toggleTheme: toggleTheme,
        getTheme: getTheme,
        openQuickActions: openQuickActions,
        closeQuickActions: closeQuickActions,
        filterQuickActions: filterQuickActions
    };

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bindUi);
    else bindUi();

    window.addEventListener('storage', function (event) {
        if (event.key === STORAGE_KEY && THEMES[event.newValue]) applyTheme(event.newValue, false);
    });
}());
