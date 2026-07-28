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


    // ─── Workspace navigation — đổi khu vực làm việc, giữ nguyên sidebar ───
    function createWorkspaceRuntime() {
        var nativeDocumentAdd = document.addEventListener.bind(document);
        var nativeDocumentRemove = document.removeEventListener.bind(document);
        var nativeWindowAdd = window.addEventListener.bind(window);
        var nativeWindowRemove = window.removeEventListener.bind(window);
        var nativeSetInterval = window.setInterval.bind(window);
        var nativeClearInterval = window.clearInterval.bind(window);
        var nativeSetTimeout = window.setTimeout.bind(window);
        var nativeClearTimeout = window.clearTimeout.bind(window);

        var currentOwner = '';
        var activeOwners = new Set();
        var loadedScriptOwners = new Set();
        var initCallbacks = new Map();
        var listenerRecords = [];
        var intervalOwners = new Map();
        var timeoutOwners = new Map();
        var navigationController = null;
        var navigating = false;

        function hashText(value) {
            var hash = 2166136261;
            var text = String(value || '');
            for (var i = 0; i < text.length; i += 1) {
                hash ^= text.charCodeAt(i);
                hash = Math.imul(hash, 16777619);
            }
            return (hash >>> 0).toString(36);
        }

        function normalizeScriptOwner(src) {
            if (!src) return '';
            try {
                return 'script:' + new URL(src, window.location.href).pathname;
            } catch (error) {
                return 'script:' + String(src).split('?')[0];
            }
        }

        function ownerForScript(script) {
            if (!script) return currentOwner || '';
            if (script.src) return normalizeScriptOwner(script.src);
            return 'inline:' + hashText(script.textContent || script.innerText || '');
        }

        function resolveOwner() {
            return currentOwner || ownerForScript(document.currentScript);
        }

        function withOwner(owner, callback, thisArg, args) {
            var previous = currentOwner;
            currentOwner = owner || previous;
            try {
                var result = callback.apply(thisArg, args || []);
                if (result && typeof result.then === 'function') {
                    return result.finally(function () { currentOwner = previous; });
                }
                currentOwner = previous;
                return result;
            } catch (error) {
                currentOwner = previous;
                throw error;
            }
        }

        function wrapListener(owner, listener) {
            if (typeof listener === 'function') {
                return function () { return withOwner(owner, listener, this, arguments); };
            }
            if (listener && typeof listener.handleEvent === 'function') {
                return function (event) { return withOwner(owner, listener.handleEvent, listener, [event]); };
            }
            return listener;
        }

        function addTrackedListener(target, nativeAdd, type, listener, options) {
            var owner = resolveOwner();
            if (type === 'DOMContentLoaded' && owner) {
                var callbacks = initCallbacks.get(owner) || [];
                if (callbacks.indexOf(listener) < 0) callbacks.push(listener);
                initCallbacks.set(owner, callbacks);
                return;
            }

            if (!owner) {
                nativeAdd(type, listener, options);
                return;
            }

            var wrapped = wrapListener(owner, listener);
            listenerRecords.push({
                owner: owner,
                target: target,
                type: type,
                original: listener,
                wrapped: wrapped,
                options: options
            });
            nativeAdd(type, wrapped, options);
        }

        function removeTrackedListener(target, nativeRemove, type, listener, options) {
            for (var i = listenerRecords.length - 1; i >= 0; i -= 1) {
                var record = listenerRecords[i];
                if (record.target === target && record.type === type && record.original === listener) {
                    nativeRemove(type, record.wrapped, record.options);
                    listenerRecords.splice(i, 1);
                    return;
                }
            }
            nativeRemove(type, listener, options);
        }

        function installPageResourceTracking() {
            document.addEventListener = function (type, listener, options) {
                addTrackedListener(document, nativeDocumentAdd, type, listener, options);
            };
            document.removeEventListener = function (type, listener, options) {
                removeTrackedListener(document, nativeDocumentRemove, type, listener, options);
            };
            window.addEventListener = function (type, listener, options) {
                addTrackedListener(window, nativeWindowAdd, type, listener, options);
            };
            window.removeEventListener = function (type, listener, options) {
                removeTrackedListener(window, nativeWindowRemove, type, listener, options);
            };

            window.setInterval = function (callback, delay) {
                var owner = resolveOwner();
                var args = Array.prototype.slice.call(arguments, 2);
                var wrapped = owner && typeof callback === 'function'
                    ? function () { return withOwner(owner, callback, window, arguments); }
                    : callback;
                var id = nativeSetInterval.apply(window, [wrapped, delay].concat(args));
                if (owner) intervalOwners.set(id, owner);
                return id;
            };
            window.clearInterval = function (id) {
                intervalOwners.delete(id);
                return nativeClearInterval(id);
            };
            window.setTimeout = function (callback, delay) {
                var owner = resolveOwner();
                var args = Array.prototype.slice.call(arguments, 2);
                var wrapped = owner && typeof callback === 'function'
                    ? function () {
                        timeoutOwners.delete(id);
                        return withOwner(owner, callback, window, arguments);
                    }
                    : callback;
                var id = nativeSetTimeout.apply(window, [wrapped, delay].concat(args));
                if (owner) timeoutOwners.set(id, owner);
                return id;
            };
            window.clearTimeout = function (id) {
                timeoutOwners.delete(id);
                return nativeClearTimeout(id);
            };
        }

        function cleanupOwners(owners) {
            // Một vài trang giữ ID timer trong biến toàn cục. Đặt lại để lần
            // quay lại tab có thể khởi động polling mới sau khi timer cũ đã dừng.
            if (owners.has('script:/static/js/accounts.js')) {
                try { if (typeof window.stopAccountsPolling === 'function') window.stopAccountsPolling(); } catch (error) {}
                try {
                    if (window._accountsHeartbeatTimer) nativeClearInterval(window._accountsHeartbeatTimer);
                    window._accountsHeartbeatTimer = null;
                } catch (error) {}
            }

            owners.forEach(function (owner) {
                for (var i = listenerRecords.length - 1; i >= 0; i -= 1) {
                    var record = listenerRecords[i];
                    if (record.owner !== owner) continue;
                    if (record.target === document) nativeDocumentRemove(record.type, record.wrapped, record.options);
                    else if (record.target === window) nativeWindowRemove(record.type, record.wrapped, record.options);
                    listenerRecords.splice(i, 1);
                }
                intervalOwners.forEach(function (intervalOwner, id) {
                    if (intervalOwner === owner) {
                        nativeClearInterval(id);
                        intervalOwners.delete(id);
                    }
                });
                timeoutOwners.forEach(function (timeoutOwner, id) {
                    if (timeoutOwner === owner) {
                        nativeClearTimeout(id);
                        timeoutOwners.delete(id);
                    }
                });
            });
        }

        function workspaceNodes(doc) {
            var marker = doc.getElementById('nexusWorkspaceStart');
            if (!marker || !marker.parentNode) return [];
            var children = Array.prototype.slice.call(marker.parentNode.children);
            var index = children.indexOf(marker);
            return index < 0 ? [] : children.slice(index + 1);
        }

        function ownerListFromNodes(nodes) {
            var owners = [];
            nodes.forEach(function (node) {
                if (node.tagName !== 'SCRIPT') return;
                var owner = ownerForScript(node);
                if (owner && owners.indexOf(owner) < 0) owners.push(owner);
            });
            return owners;
        }

        async function runInitializers(owners) {
            for (var i = 0; i < owners.length; i += 1) {
                var owner = owners[i];
                var callbacks = initCallbacks.get(owner) || [];
                for (var j = 0; j < callbacks.length; j += 1) {
                    try {
                        await withOwner(owner, callbacks[j], document, [new Event('DOMContentLoaded')]);
                    } catch (error) {
                        console.error('[Nexus workspace] Lỗi khởi tạo trang:', owner, error);
                    }
                }
            }
            labelIconButtons();
            document.documentElement.classList.add('nexus-ui-ready');
        }

        function setWorkspaceLoading(loading) {
            navigating = !!loading;
            document.body.classList.toggle('nexus-workspace-loading', !!loading);
            var loader = document.getElementById('nexusWorkspaceLoader');
            if (loader) {
                loader.classList.toggle('is-visible', !!loading);
                loader.setAttribute('aria-hidden', loading ? 'false' : 'true');
            }
        }

        function syncActiveNavigation(newDocument) {
            var currentLinks = document.querySelectorAll('[data-nexus-workspace-nav]');
            currentLinks.forEach(function (currentLink) {
                var currentPath;
                try { currentPath = new URL(currentLink.href, window.location.href).pathname; }
                catch (error) { currentPath = currentLink.getAttribute('href') || ''; }

                var matched = Array.prototype.find.call(
                    newDocument.querySelectorAll('[data-nexus-workspace-nav]'),
                    function (candidate) {
                        try { return new URL(candidate.href, window.location.href).pathname === currentPath; }
                        catch (error) { return (candidate.getAttribute('href') || '') === currentPath; }
                    }
                );
                var active = !!(matched && (matched.classList.contains('active') || matched.getAttribute('aria-current') === 'page'));
                currentLink.classList.toggle('active', active);
                if (active) currentLink.setAttribute('aria-current', 'page');
                else currentLink.removeAttribute('aria-current');
            });
        }

        function copyBodyState(newDocument) {
            var nextClasses = Array.prototype.slice.call(newDocument.body.classList);
            document.body.className = nextClasses.join(' ');
            document.body.classList.add('nexus-ui-ready');
            if (newDocument.documentElement.dataset.theme) {
                document.documentElement.dataset.theme = newDocument.documentElement.dataset.theme;
            }
        }

        function loadExternalScript(node, owner) {
            return new Promise(function (resolve, reject) {
                if (loadedScriptOwners.has(owner)) {
                    resolve();
                    return;
                }
                var script = document.createElement('script');
                Array.prototype.slice.call(node.attributes || []).forEach(function (attr) {
                    script.setAttribute(attr.name, attr.value);
                });
                script.async = false;
                script.onload = function () {
                    loadedScriptOwners.add(owner);
                    resolve();
                };
                script.onerror = function () { reject(new Error('Không tải được ' + node.src)); };
                document.body.appendChild(script);
            });
        }

        async function executeInlineScript(node, owner) {
            var type = String(node.type || '').toLowerCase();
            if (type && type !== 'text/javascript' && type !== 'application/javascript' && type !== 'module') return;
            var code = node.textContent || '';
            if (!code.trim()) return;
            try {
                await withOwner(owner, function () {
                    // Chạy trong phạm vi riêng để tránh lỗi khai báo lại let/const khi quay lại tab.
                    return (new Function(code)).call(window);
                }, window, []);
            } catch (error) {
                console.error('[Nexus workspace] Lỗi script nội tuyến:', error);
            }
        }

        async function mountWorkspaceNodes(nodes, marker) {
            var owners = ownerListFromNodes(nodes);
            for (var i = 0; i < nodes.length; i += 1) {
                var source = nodes[i];
                if (source.tagName === 'SCRIPT') {
                    var owner = ownerForScript(source);
                    if (source.src) await loadExternalScript(source, owner);
                    else await executeInlineScript(source, owner);
                    continue;
                }
                var imported = document.importNode(source, true);
                marker.parentNode.appendChild(imported);
            }
            return owners;
        }

        async function navigateWorkspace(url, options) {
            options = options || {};
            var targetUrl = new URL(url, window.location.href);
            if (targetUrl.origin !== window.location.origin) {
                window.location.assign(targetUrl.href);
                return;
            }
            if (navigating) return;

            if (navigationController) navigationController.abort();
            navigationController = new AbortController();
            setWorkspaceLoading(true);

            try {
                var response = await fetch(targetUrl.href, {
                    signal: navigationController.signal,
                    headers: {
                        'Accept': 'text/html',
                        'X-Nexus-Workspace': '1'
                    },
                    credentials: 'same-origin'
                });
                if (!response.ok) throw new Error('HTTP ' + response.status);
                var html = await response.text();
                var newDocument = new DOMParser().parseFromString(html, 'text/html');
                var newMarker = newDocument.getElementById('nexusWorkspaceStart');
                var currentMarker = document.getElementById('nexusWorkspaceStart');
                var newMain = newDocument.querySelector('main.page');
                if (!newMarker || !currentMarker || !newMain) throw new Error('Trang không hỗ trợ chuyển vùng làm việc.');

                var newNodes = workspaceNodes(newDocument);
                cleanupOwners(activeOwners);
                activeOwners.clear();

                var currentNodes = workspaceNodes(document);
                currentNodes.forEach(function (node) { node.remove(); });

                copyBodyState(newDocument);
                syncActiveNavigation(newDocument);
                document.title = newDocument.title || document.title;

                var owners = await mountWorkspaceNodes(newNodes, currentMarker);
                activeOwners = new Set(owners);
                await runInitializers(owners);

                if (options.push !== false) window.history.pushState({ nexusWorkspace: true }, '', targetUrl.href);
                window.scrollTo({ top: 0, left: 0, behavior: 'auto' });
                setSidebar(false);
            } catch (error) {
                if (error && error.name === 'AbortError') return;
                console.error('[Nexus workspace] Chuyển tab thất bại, dùng điều hướng đầy đủ:', error);
                window.location.assign(targetUrl.href);
                return;
            } finally {
                setWorkspaceLoading(false);
            }
        }

        function shouldHandleNavigation(event, link) {
            if (!link || !link.hasAttribute('data-nexus-workspace-nav')) return false;
            if (event.defaultPrevented || event.button !== 0) return false;
            if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return false;
            if (link.target && link.target !== '_self') return false;
            if (link.hasAttribute('download')) return false;
            var href = link.getAttribute('href');
            if (!href || href.charAt(0) === '#') return false;
            return true;
        }

        nativeDocumentAdd('click', function (event) {
            var link = event.target.closest && event.target.closest('a[data-nexus-workspace-nav]');
            if (!shouldHandleNavigation(event, link)) return;
            event.preventDefault();
            navigateWorkspace(link.href, { push: true });
        }, true);

        nativeWindowAdd('popstate', function () {
            navigateWorkspace(window.location.href, { push: false });
        });

        nativeDocumentAdd('DOMContentLoaded', function () {
            var nodes = workspaceNodes(document);
            var owners = ownerListFromNodes(nodes);
            owners.forEach(function (owner) {
                activeOwners.add(owner);
                if (owner.indexOf('script:') === 0) loadedScriptOwners.add(owner);
            });
            runInitializers(owners);
        }, { once: true });

        installPageResourceTracking();

        return {
            navigate: navigateWorkspace,
            cleanup: function () { cleanupOwners(activeOwners); },
            isNavigating: function () { return navigating; }
        };
    }

    var workspaceRuntime = createWorkspaceRuntime();

    window.nexusConfirm = nexusConfirm;
    window.NexusInteractions = {
        setSidebar: setSidebar,
        confirm: nexusConfirm,
        navigate: workspaceRuntime.navigate,
        cleanupPage: workspaceRuntime.cleanup
    };
}());
