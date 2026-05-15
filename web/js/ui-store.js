/**
 * UIStore — thin coordinator that wires together MediaManager, MenuManager,
 * and ViewManager.  Owns input handling (laser, wheel, keyboard) and the
 * arc pointer.  Exposes backward-compatible window.uiStore API by delegating
 * to the individual managers.
 *
 * Load order (index.html):
 *   media-manager.js → menu-manager.js → view-manager.js → ui-store.js
 */

class UIStore {
    constructor() {
        // ── Create managers ──
        this.media = new MediaManager();
        this.menu = new MenuManager();
        this.view = new ViewManager();
        this._baseMenuItems = [];
        this._contextMenusByRoute = new Map();
        this._activeContextRoute = '';
        this._contextAffinityRoute = '';
        this._openContextRoutes = new Set();
        this._contextResetTimers = new Map();

        // ── Wire cross-references ──
        this.view.menuManager = this.menu;
        this.view.mediaManager = this.media;

        this.menu.onNavigate = (path) => this.view.navigateToView(path);
        this.menu.onMenuLoaded = (data) => {
            if (data.active_source) {
                this.media.activeSource = data.active_source;
                this.media.activeSourcePlayer = data.active_player || null;
                this.media.setActivePlayingPreset(data.active_source);
            }
            this._updateBaseMenuItems(this.menu.menuItems);
            this._syncContextMenuForRoute(this.view.currentRoute);
        };
        this.menu.onItemHover = (angle) => {
            this.wheelPointerAngle = angle;
            if (window.LaserPositionMapper) {
                this.laserPosition = Math.round(window.LaserPositionMapper.angleToLaserPosition(angle));
            }
            this.handleWheelChange();
        };

        // Keep menu manager informed of current route for removeMenuItem
        const origNav = this.view.navigateToView.bind(this.view);
        this.view.navigateToView = (path) => {
            const previousRoute = this.view.currentRoute;
            const previousVisibleContextRoute = this._resolveVisibleContextRoute(previousRoute);
            origNav(path);
            const nextRoute = this.view.currentRoute;
            this.menu._currentRoute = nextRoute;

            const nextIsTransientContextRoute = this._routeUsesContextAffinity(nextRoute);
            if (nextIsTransientContextRoute && previousVisibleContextRoute) {
                this._contextAffinityRoute = previousVisibleContextRoute;
            } else if (!nextIsTransientContextRoute) {
                this._contextAffinityRoute = '';
            }

            const preservePreviousContext = !!previousVisibleContextRoute
                && this._openContextRoutes.has(previousVisibleContextRoute)
                && (nextIsTransientContextRoute || nextRoute === previousVisibleContextRoute);

            if (previousRoute && previousRoute !== nextRoute && !preservePreviousContext) {
                this._closeContextMenuForRoute(previousVisibleContextRoute || previousRoute, { sync: false });
            }
            if (this._routeUsesContextMenu(nextRoute) && nextRoute !== previousVisibleContextRoute) {
                this._openContextRoutes.delete(nextRoute);
            }
            this._syncContextMenuForRoute(nextRoute);
            const shouldResetContextRoute = nextRoute
                && nextRoute !== previousRoute
                && !(preservePreviousContext && nextRoute === previousVisibleContextRoute);
            if (shouldResetContextRoute) {
                this._scheduleContextRouteReset(nextRoute);
            }
        };

        // ── Input / pointer state ──
        this.wheelPointerAngle = 180;
        this.topWheelPosition = 0;
        this.laserPosition = window.Constants?.laser?.defaultPosition || 93;

        // ── Debug ──
        this.debugEnabled = true;
        this.debugVisible = false;
        this.wsMessages = [];
        this.maxWsMessages = 50;

        // ── Initialize ──
        this._initializeUI();
        this._updateBaseMenuItems(this.menu.menuItems);
        this._setupEventListeners();
        this.view.updateView();

        setTimeout(() => {
            this.view.setMenuVisible(true);
        }, 100);

        // Apple TV refresh starts on-demand when navigating to SHOWING view

        // Fetch menu from router (async, non-blocking)
        this.menu.fetchMenu();
    }

    // ── Backward-compatible property access ──
    // External code (ws-dispatcher, hardware-input, immersive-mode, etc.)
    // accesses these via window.uiStore.X — delegate to the right manager.

    get mediaInfo() { return this.media.mediaInfo; }
    set mediaInfo(v) { this.media.mediaInfo = v; }

    get activeSource() { return this.media.activeSource; }
    set activeSource(v) { this.media.activeSource = v; }

    get activeSourcePlayer() { return this.media.activeSourcePlayer; }
    set activeSourcePlayer(v) { this.media.activeSourcePlayer = v; }

    get activePlayingPreset() { return this.media.activePlayingPreset; }

    get menuItems() { return this.menu.menuItems; }

    get currentRoute() { return this.view.currentRoute; }
    set currentRoute(v) { this.view.currentRoute = v; }

    get menuVisible() { return this.view.menuVisible; }

    get views() { return this.menu.views; }

    get _menuLoaded() { return this.menu._menuLoaded; }

    // ── Delegated methods ──

    handleMediaUpdate(data, reason) { this.media.handleMediaUpdate(data, reason); }
    updateNowPlayingView() { this.media.updateNowPlayingView(); }
    setActivePlayingPreset(sourceId) { this.media.setActivePlayingPreset(sourceId); }

    navigateToView(path) { this.view.navigateToView(path); this.menu._currentRoute = this.view.currentRoute; }
    setMenuVisible(visible) { this.view.setMenuVisible(visible); }

    addMenuItem(item, afterPath, viewDef) {
        if (this._activeContextRoute && this._baseMenuItems.length) {
            this.menu.menuItems = this._cloneMenuItems(this._baseMenuItems);
        }
        this.menu.addMenuItem(item, afterPath, viewDef);
        this._updateBaseMenuItems(this.menu.menuItems);
        this._syncContextMenuForRoute(this.view.currentRoute);
    }
    removeMenuItem(path) {
        this.menu._currentRoute = this.view.currentRoute;
        if (this._activeContextRoute && this._baseMenuItems.length) {
            this.menu.menuItems = this._cloneMenuItems(this._baseMenuItems);
        }
        this.menu.removeMenuItem(path);
        this._updateBaseMenuItems(this.menu.menuItems);
        this._syncContextMenuForRoute(this.view.currentRoute);
    }

    _loadSourceScript(preset) { return this.menu.loadSourceScript(preset); }
    _reloadAllSourceIframes() { this.menu.reloadAllSourceIframes(); }

    _cloneMenuItems(items) {
        return Array.isArray(items)
            ? items.map((item) => Object.assign({}, item))
            : [];
    }

    _normalizeMenuItem(item) {
        const normalized = Object.assign({}, item || {});
        if (normalized.path === 'menu/scenes') {
            normalized.title = 'HOME';
        }
        return normalized;
    }

    _normalizeMenuItems(items) {
        return this._cloneMenuItems(items).map((item) => this._normalizeMenuItem(item));
    }

    _updateBaseMenuItems(items) {
        this._baseMenuItems = this._normalizeMenuItems(items);
    }

    _applyVisibleMenuItems(items) {
        const normalized = this._normalizeMenuItems(items);
        const visiblePaths = new Set(normalized.map((item) => item.path));
        if (!visiblePaths.has(this.menu._lastSelectedPath)) {
            this.menu._lastSelectedPath = null;
        }
        this.menu.menuItems = normalized;
        if (window.LaserPositionMapper?.updateMenuItems) {
            window.LaserPositionMapper.updateMenuItems(this.menu.menuItems);
        }
        this.menu.renderMenuItems();
    }

    _getContextMenuConfig(route) {
        const defaults = { includePlaying: false, includeQueue: false };
        const configByRoute = {
            'menu/mass': { includePlaying: true, includeQueue: true },
            'menu/kodi': { includePlaying: true, includeQueue: true },
            'menu/scenes': { includePlaying: false, includeQueue: false },
        };
        return Object.assign({}, defaults, configByRoute[route] || {});
    }

    _buildContextMenuItems(route) {
        const context = this._contextMenusByRoute.get(route);
        if (!context || !Array.isArray(context.items) || !context.items.length) {
            return null;
        }

        const config = this._getContextMenuConfig(route);
        const items = [];

        if (config.includePlaying) {
            items.push({ title: 'PLAYING', path: 'menu/playing', kind: 'navigate' });
        }

        if (config.includeQueue && this._baseMenuItems.some((item) => item.path === 'menu/queue')) {
            items.push({ title: 'QUEUE', path: 'menu/queue', kind: 'navigate' });
        }

        context.items.forEach((entry) => {
            const id = String(entry.id || '').trim();
            const title = String(entry.title || '').trim();
            if (!id || !title) return;
            items.push({
                title,
                path: `context:${route}:${id}`,
                kind: 'context',
                route,
                contextId: id,
            });
        });

        return items;
    }

    _routeUsesContextMenu(route) {
        return route === 'menu/mass' || route === 'menu/kodi' || route === 'menu/scenes';
    }

    _routeUsesContextAffinity(route) {
        return route === 'menu/playing' || route === 'menu/queue';
    }

    _resolveVisibleContextRoute(route) {
        const normalizedRoute = String(route || '').trim();
        if (this._routeUsesContextMenu(normalizedRoute) && this._openContextRoutes.has(normalizedRoute)) {
            return normalizedRoute;
        }
        if (this._routeUsesContextAffinity(normalizedRoute)
            && this._contextAffinityRoute
            && this._openContextRoutes.has(this._contextAffinityRoute)) {
            return this._contextAffinityRoute;
        }
        return '';
    }

    _syncContextMenuForRoute(route) {
        const visibleContextRoute = this._resolveVisibleContextRoute(route);
        const visibleItems = this._buildContextMenuItems(visibleContextRoute);
        if (visibleContextRoute && visibleItems?.length) {
            this._activeContextRoute = visibleContextRoute;
            this._applyVisibleMenuItems(visibleItems);
            return;
        }

        this._activeContextRoute = '';
        this._applyVisibleMenuItems(this._baseMenuItems.length ? this._baseMenuItems : this.menu.menuItems);
    }

    _openContextMenuForRoute(route, options = {}) {
        const normalizedRoute = String(route || '').trim();
        if (!this._routeUsesContextMenu(normalizedRoute)) return false;

        const visibleItems = this._buildContextMenuItems(normalizedRoute);
        if (!visibleItems?.length) return false;

        this._openContextRoutes.add(normalizedRoute);
        this._syncContextMenuForRoute(normalizedRoute);

        const context = this._contextMenusByRoute.get(normalizedRoute);
        const requestedId = String(
            options.contextId
            || context?.selectedId
            || context?.activeId
            || visibleItems.find((item) => item.kind === 'context')?.contextId
            || ''
        ).trim();

        if (options.selectCurrent !== false && requestedId) {
            const requestedItem = visibleItems.find((item) =>
                item.kind === 'context' && String(item.contextId || '').trim() === requestedId
            );
            if (requestedItem) {
                this._selectContextMenuItem(requestedItem);
                this.sendClickCommand();
            }
        }

        return true;
    }

    _closeContextMenuForRoute(route, options = {}) {
        const normalizedRoute = String(route || '').trim();
        if (!normalizedRoute) return false;

        const wasOpen = this._openContextRoutes.delete(normalizedRoute);
        if (this._contextAffinityRoute === normalizedRoute) {
            this._contextAffinityRoute = '';
        }
        if (this._activeContextRoute === normalizedRoute) {
            this._activeContextRoute = '';
        }

        const currentVisibleContextRoute = this._resolveVisibleContextRoute(this.view.currentRoute);
        if (options.sync !== false
            && (normalizedRoute === this.view.currentRoute || currentVisibleContextRoute === normalizedRoute)) {
            this._syncContextMenuForRoute(this.view.currentRoute);
            if (options.highlightCurrent !== false) {
                const currentRouteIndex = this.menu.menuItems.findIndex((item) => item.path === this.view.currentRoute);
                if (currentRouteIndex >= 0) {
                    this.menu.applyMenuHighlight(currentRouteIndex, this.view.currentRoute);
                }
            }
        }

        return wasOpen;
    }

    exitContextMenu(route) {
        this._closeContextMenuForRoute(route || this.view.currentRoute);
    }

    _routeNeedsContextReset(route) {
        return route === 'menu/mass' || route === 'menu/kodi' || route === 'menu/scenes';
    }

    _scheduleContextRouteReset(route) {
        if (!this._routeNeedsContextReset(route) || !window.IframeMessenger) return;
        const existingTimer = this._contextResetTimers.get(route);
        if (existingTimer) {
            clearTimeout(existingTimer);
        }
        const timer = setTimeout(() => {
            this._contextResetTimers.delete(route);
            window.IframeMessenger.sendToRoute(route, 'context-reset', { reason: 'route-enter' });
        }, 220);
        this._contextResetTimers.set(route, timer);
    }

    receiveContextMenuUpdate(payload) {
        const route = String(payload?.route || '').trim();
        if (!route) return;

        const items = Array.isArray(payload?.items)
            ? payload.items.map((entry) => ({
                id: String(entry?.id || '').trim(),
                title: String(entry?.title || '').trim(),
            })).filter((entry) => entry.id && entry.title)
            : [];

        if (!items.length) {
            this._contextMenusByRoute.delete(route);
        } else {
            this._contextMenusByRoute.set(route, {
                route,
                items,
                selectedId: String(payload?.selectedId || '').trim(),
                activeId: String(payload?.activeId || payload?.selectedId || '').trim(),
            });
        }

        const currentVisibleContextRoute = this._resolveVisibleContextRoute(this.view.currentRoute);
        if (route === this.view.currentRoute || route === currentVisibleContextRoute) {
            this._syncContextMenuForRoute(this.view.currentRoute);
        }
    }

    _selectContextMenuItem(item) {
        if (!item || item.kind !== 'context') return;
        if (!window.IframeMessenger) return;
        const targetRoute = item.route || this.view.currentRoute;
        const navigated = this.view.currentRoute !== targetRoute;
        if (this.view.currentRoute !== targetRoute) {
            this.view.navigateToView(targetRoute);
            this.menu._currentRoute = this.view.currentRoute;
        }
        window.IframeMessenger.sendToRoute(targetRoute, 'context-select', {
            id: item.contextId,
        });
        if (navigated) {
            setTimeout(() => {
                window.IframeMessenger?.sendToRoute(targetRoute, 'context-select', {
                    id: item.contextId,
                });
            }, 80);
        }
    }

    _resolveCurrentMenuSelection() {
        if (!this.laserPosition || !window.LaserPositionMapper) {
            return {
                result: { selectedIndex: -1, path: null, isOverlay: false, angle: this.wheelPointerAngle },
                selectedMenuItem: null,
                contextSelection: false,
            };
        }

        const result = window.LaserPositionMapper.resolveMenuSelection(this.laserPosition);
        const selectedMenuItem = result.selectedIndex >= 0
            ? this.menu.menuItems[result.selectedIndex] || null
            : null;
        const contextSelection = selectedMenuItem?.kind === 'context'
            && String(selectedMenuItem.route || '').trim() === this._activeContextRoute;

        return { result, selectedMenuItem, contextSelection };
    }

    tryHandleContextButton(button) {
        const normalized = String(button || '').toLowerCase();
        const currentRoute = this.view.currentRoute;
        if (normalized !== 'left') return false;

        const { selectedMenuItem, contextSelection } = this._resolveCurrentMenuSelection();
        const visibleContextRoute = this._resolveVisibleContextRoute(currentRoute);

        if (!visibleContextRoute) {
            if (!this._routeUsesContextMenu(currentRoute)) return false;
            if (selectedMenuItem?.path !== currentRoute) return false;
            return this._openContextMenuForRoute(currentRoute);
        }

        if (!contextSelection || !selectedMenuItem) return false;

        const context = this._contextMenusByRoute.get(visibleContextRoute);
        const activeId = String(context?.activeId || '').trim();
        const selectedId = String(selectedMenuItem.contextId || '').trim();
        if (!selectedId) return false;

        if (!activeId || activeId !== selectedId) {
            this._selectContextMenuItem(selectedMenuItem);
            this.sendClickCommand();
            return true;
        }

        return false;
    }

    // ── Debug logging ──

    logWebsocketMessage(message) {
        this.wsMessages.unshift({
            time: new Date().toLocaleTimeString(),
            message
        });
        if (this.wsMessages.length > this.maxWsMessages) {
            this.wsMessages.length = this.maxWsMessages;
        }
    }

    // ── UI initialization ──

    _initializeUI() {
        const mainArc = document.getElementById('mainArc');
        mainArc.setAttribute('d', arcs.drawArc(arcs.cx, arcs.cy, this.menu.radius, 158, 202));

        this.menu.renderMenuItems();
        this.updatePointer();
        this.menu.preloadIframes();
    }

    // ── Pointer ──

    updatePointer() {
        const pointerDot = document.getElementById('pointerDot');
        const pointerLine = document.getElementById('pointerLine');
        const mainMenu = document.getElementById('mainMenu');

        const point = arcs.getArcPoint(this.menu.radius, 0, this.wheelPointerAngle);
        const transform = `rotate(${this.wheelPointerAngle - 90}deg)`;

        [pointerDot, pointerLine].forEach(element => {
            element.setAttribute('cx', point.x);
            element.setAttribute('cy', point.y);
            element.style.transformOrigin = `${point.x}px ${point.y}px`;
            element.style.transform = transform;
        });

        if (mainMenu) {
            if (this.wheelPointerAngle > 203 || this.wheelPointerAngle < 155) {
                mainMenu.classList.add('slide-out');
            } else {
                mainMenu.classList.remove('slide-out');
            }
        }
    }

    // ── Input handling ──

    handleWheelChange() {
        this.wheelPointerAngle = Math.max(150, Math.min(210, this.wheelPointerAngle));

        if (!this.laserPosition || !window.LaserPositionMapper) {
            console.error('[UI] Laser position system required but not available');
            return;
        }

        const {
            result,
            selectedMenuItem,
            contextSelection,
        } = this._resolveCurrentMenuSelection();

        // Determine effective path — overlays navigate to PLAYING/SHOWING.
        // If SHOWING is not in the menu, both ends land on PLAYING.
        let effectivePath = result.path;
        if (result.isOverlay) {
            const overlayMenu = this._baseMenuItems.length ? this._baseMenuItems : this.menu.menuItems;
            const hasShowing = overlayMenu.some(m => m.path === 'menu/showing');
            effectivePath = (result.angle >= 200 || !hasShowing) ? 'menu/playing' : 'menu/showing';
        } else if (contextSelection) {
            effectivePath = null;
        }

        // Menu visibility
        if (result.isOverlay && this.view.menuVisible) {
            this.view.setMenuVisible(false);
        } else if (!result.isOverlay && !this.view.menuVisible) {
            this.view.setMenuVisible(true);
        }

        // Navigate when the effective path differs
        if (effectivePath && effectivePath !== this.view.currentRoute) {
            this.view.navigateToView(effectivePath);
            this.menu._currentRoute = this.view.currentRoute;
        }

        // Bold + click (only for non-overlay menu items)
        if (this.menu.applyMenuHighlight(result.selectedIndex, result.path)) {
            if (contextSelection && selectedMenuItem) {
                const visibleContextRoute = this._resolveVisibleContextRoute(this.view.currentRoute);
                const context = this._contextMenusByRoute.get(visibleContextRoute);
                const activeId = String(context?.activeId || '').trim();
                const selectedId = String(selectedMenuItem.contextId || '').trim();
                if (selectedId && selectedId !== activeId) {
                    this._selectContextMenuItem(selectedMenuItem);
                }
            }
            this.sendClickCommand();
        }

        this.updatePointer();
        this.topWheelPosition = 0;

        document.dispatchEvent(new CustomEvent('bs5c:wheel-change'));
    }

    setLaserPosition(position) {
        this.laserPosition = position;
    }

    sendClickCommand() {
        const ws = window.hardwareWebSocket;
        if (ws && ws.readyState === WebSocket.OPEN) {
            try {
                ws.send(JSON.stringify({ type: 'command', command: 'click', params: {} }));
            } catch (error) {
                // Silently fail - connection may have closed between check and send
            }
        }
    }

    forwardButtonToActiveIframe(button) {
        if (window.IframeMessenger) {
            window.IframeMessenger.sendButtonEvent(this.view.currentRoute, button);
        }
    }

    forwardKeyboardToActiveIframe(event) {
        if (window.IframeMessenger) {
            window.IframeMessenger.sendKeyboardEvent(this.view.currentRoute, event);
        }
    }

    _setupEventListeners() {
        document.addEventListener('keydown', (event) => {
            if (window.dummyHardwareManager?.isActive) {
                return;
            }
            switch (event.key) {
                case "ArrowUp":
                    this.topWheelPosition = -1;
                    this.handleWheelChange();
                    break;
                case "ArrowDown":
                    this.topWheelPosition = 1;
                    this.handleWheelChange();
                    break;
                case "ArrowLeft":
                    if (this.view.currentRoute === 'menu/playing') {
                        // Webhook handled by dummy hardware system
                    } else {
                        if (!this.tryHandleContextButton('left')) {
                            this.forwardButtonToActiveIframe('left');
                            this.forwardKeyboardToActiveIframe(event);
                        }
                    }
                    break;
                case "ArrowRight":
                    if (this.view.currentRoute === 'menu/playing') {
                        // Webhook handled by dummy hardware system
                    } else {
                        this.forwardButtonToActiveIframe('right');
                        this.forwardKeyboardToActiveIframe(event);
                    }
                    break;
                case "Enter":
                    if (this.view.currentRoute !== 'menu/playing') {
                        this.forwardKeyboardToActiveIframe(event);
                    }
                    break;
            }
        });

        const updatePointerFromClientPoint = (clientX, clientY, target) => {
            if (target?.closest?.([
                'iframe',
                '.webpage-iframe',
                'button',
                'a',
                'input',
                'select',
                'textarea',
                '[role="button"]',
                '[role="switch"]',
                '.mass-playing-transfer',
                '.kodi-transfer-options',
                '.queue-view',
                '.system-page',
            ].join(', '))) return false;
            const mainMenu = document.getElementById('mainMenu');
            if (!mainMenu) return false;

            const rect = mainMenu.getBoundingClientRect();
            const centerX = arcs.cx - rect.left;
            const centerY = arcs.cy - rect.top;

            const dx = clientX - rect.left - centerX;
            const dy = clientY - rect.top - centerY;
            let angle = Math.atan2(dy, dx) * 180 / Math.PI + 90;
            if (angle < 0) angle += 360;

            if ((angle >= 158 && angle <= 202) ||
                (angle >= 0 && angle <= 30) ||
                (angle >= 330 && angle <= 360)) {
                this.wheelPointerAngle = angle;
                if (window.LaserPositionMapper) {
                    this.laserPosition = Math.round(window.LaserPositionMapper.angleToLaserPosition(angle));
                }
                this.handleWheelChange();
                return true;
            }
            return false;
        };

        document.addEventListener('mousemove', (event) => {
            updatePointerFromClientPoint(event.clientX, event.clientY, event.target);
        });

        document.addEventListener('touchstart', (event) => {
            const touch = event.touches && event.touches[0];
            if (!touch) return;
            if (updatePointerFromClientPoint(touch.clientX, touch.clientY, event.target)) {
                event.preventDefault();
            }
        }, { passive: false });

        document.addEventListener('touchmove', (event) => {
            const touch = event.touches && event.touches[0];
            if (!touch) return;
            if (updatePointerFromClientPoint(touch.clientX, touch.clientY, event.target)) {
                event.preventDefault();
            }
        }, { passive: false });

        const menuItemsEl = document.getElementById('menuItems');
        let lastMenuPointerActivateAt = 0;
        const activateMenuItem = (event) => {
            const clickedItem = event.target.closest('.list-item');
            if (!clickedItem) return;

            const children = Array.from(clickedItem.parentElement.children);
            const index = children.indexOf(clickedItem);
            const itemAngle = this.menu.getStartItemAngle() + (children.length - 1 - index) * this.menu.angleStep;
            this.wheelPointerAngle = itemAngle;
            if (window.LaserPositionMapper) {
                this.laserPosition = Math.round(window.LaserPositionMapper.angleToLaserPosition(itemAngle));
            }
            this.handleWheelChange();

            const clickedMenuItem = this.menu.menuItems[index] || null;
            const visibleContextRoute = this._resolveVisibleContextRoute(this.view.currentRoute);
            if (clickedMenuItem?.kind === 'context'
                && String(clickedMenuItem.route || '').trim() === visibleContextRoute) {
                this._selectContextMenuItem(clickedMenuItem);
            }

            this.sendClickCommand();
            return true;
        };
        const activateMenuItemFromPointer = (event) => {
            if (event.pointerType === 'mouse') return;
            if (Date.now() - lastMenuPointerActivateAt < 120) {
                event.preventDefault();
                return;
            }
            if (activateMenuItem(event)) {
                lastMenuPointerActivateAt = Date.now();
                event.preventDefault();
            }
        };
        if (window.PointerEvent) {
            menuItemsEl.addEventListener('pointerup', activateMenuItemFromPointer);
        }
        menuItemsEl.addEventListener('touchend', (event) => {
            if (Date.now() - lastMenuPointerActivateAt < 120) {
                event.preventDefault();
                return;
            }
            if (activateMenuItem(event)) {
                lastMenuPointerActivateAt = Date.now();
                event.preventDefault();
            }
        }, { passive: false });
        menuItemsEl.addEventListener('click', (event) => {
            if (Date.now() - lastMenuPointerActivateAt < 450) {
                event.preventDefault();
                return;
            }
            activateMenuItem(event);
        });
    }

    // ── Test helpers ──

    testAddSource(sourceId) {
        const preset = window.SourcePresets?.[sourceId];
        if (!preset) {
            console.error(`SourcePresets.${sourceId} not loaded`);
            return;
        }
        this.menu.addMenuItem(preset.item, preset.after, preset.view);
        setTimeout(() => {
            const container = document.getElementById('contentArea');
            if (preset.onAdd) preset.onAdd(container);
        }, 50);
    }

    testRemoveSource(sourceId) {
        const preset = window.SourcePresets?.[sourceId];
        if (!preset) {
            console.error(`SourcePresets.${sourceId} not loaded`);
            return;
        }
        if (preset.onRemove) preset.onRemove();
        this.menu._currentRoute = this.view.currentRoute;
        this.menu.removeMenuItem(preset.item.path);
    }
}

// ── Bootstrap ──

document.addEventListener('DOMContentLoaded', () => {
    const uiStore = new UIStore();
    window.uiStore = uiStore;

    window.sendClickCommand = () => {
        if (window.uiStore) {
            window.uiStore.sendClickCommand();
        } else {
            console.error('UIStore not initialized yet');
        }
    };

    // Fade out splash screen after artwork is ready
    const timeouts = window.Constants?.timeouts || {};

    const hideSplash = () => {
        const splash = document.getElementById('splash-overlay');
        if (splash && !splash.classList.contains('fade-out')) {
            splash.classList.add('fade-out');
            setTimeout(() => {
                splash.classList.add('hidden');
            }, timeouts.splashRemoveDelay || 800);
        }
    };

    const waitForArtwork = () => {
        const artworkEl = document.querySelector('#now-playing .playing-artwork');
        if (artworkEl && artworkEl.src && artworkEl.src !== '' && artworkEl.src !== window.location.href) {
            if (artworkEl.complete && artworkEl.naturalHeight > 0) {
                hideSplash();
            } else {
                artworkEl.onload = hideSplash;
                artworkEl.onerror = hideSplash;
            }
        } else {
            setTimeout(waitForArtwork, 100);
        }
    };

    setTimeout(waitForArtwork, 300);
    setTimeout(hideSplash, 3000);

    // Relay messages from child iframes
    window.addEventListener('message', (event) => {
        if (event.data?.type === 'bs5c-context-menu') {
            uiStore.receiveContextMenuUpdate(event.data);
        } else if (event.data?.type === 'bs5c-context-exit') {
            uiStore.exitContextMenu(event.data?.route || '');
        } else if (event.data?.type === 'reload-playlists') {
            uiStore.menu.reloadAllSourceIframes();
        } else if (event.data?.type === 'click') {
            // Only honor clicks from an iframe currently attached to the
            // active view. Preloaded / detached iframes in the offscreen
            // preload container still have live message listeners and may
            // emit clicks from stale input — ignore those to avoid racing
            // through menu items during/after navigation.
            const contentArea = document.getElementById('contentArea');
            if (!contentArea) return;
            const fromActive = Array.from(contentArea.querySelectorAll('iframe'))
                .some(f => f.contentWindow === event.source);
            if (fromActive) uiStore.sendClickCommand();
        }
    });
});
