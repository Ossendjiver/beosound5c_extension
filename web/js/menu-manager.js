/**
 * MenuManager — owns the menu item list, rendering, and source script loading.
 *
 * Manages:
 *  - menuItems[] — the ordered list of {title, path} objects
 *  - views{} — route → {title, content, preloadId, ...} definitions
 *  - Source script loading and iframe preloading
 *  - Menu item DOM rendering (static + FLIP-animated)
 *
 * Requires:
 *  - window.Constants, window.arcs, window.LaserPositionMapper
 *  - this.onNavigate(path) callback — wired by UIStore
 *  - this.onMenuLoaded(data) callback — wired by UIStore
 */

class MenuManager {
    constructor() {
        const c = window.Constants || {};
        this.radius = c.arc?.radius || 1000;
        this.angleStep = c.arc?.menuAngleStep || 5;

        // Menu items from centralized constants (static views only — sources/webpages added by router)
        this.menuItems = (c.menuItems || [
            {title: 'PLAYING', path: 'menu/playing'},
            {title: 'SCENES', path: 'menu/scenes'},
            {title: 'SYSTEM', path: 'menu/system'},
            {title: 'SHOWING', path: 'menu/showing'}
        ]).map(item => ({title: item.title, path: item.path}));

        // View definitions keyed by route path
        this.views = {
            'menu/showing': {
                title: 'SHOWING',
                content: `
                    <div id="status-page" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; color: white; text-align: center; background-color: rgba(0,0,0,0.4);">
                        <div id="apple-tv-artwork-container" style="width: 60%; aspect-ratio: 1; margin: 20px; position: relative; display: flex; justify-content: center; align-items: center; overflow: hidden; border-radius: 8px; box-shadow: 0 5px 15px rgba(0,0,0,0.3);">
                            <img id="apple-tv-artwork" src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7" alt="Apple TV Media" style="width: 100%; height: 100%; object-fit: contain; transition: opacity 0.6s ease;">
                        </div>
                        <div id="apple-tv-media-info" style="width: 80%; padding: 10px;">
                            <div id="apple-tv-media-title" style="font-size: 24px; font-weight: bold; margin-bottom: 8px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">—</div>
                            <div id="apple-tv-media-details" style="font-size: 18px; margin-bottom: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">—</div>
                            <div id="apple-tv-state">Unknown</div>
                        </div>
                    </div>`
            },
            'menu/system': {
                title: 'System',
                content: `
                    <div id="system-container" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center;">
                        <iframe id="system-iframe" src="softarc/system.html" style="width: 100%; height: 100%; border: none; border-radius: 8px; box-shadow: 0 5px 15px rgba(0,0,0,0.3);" allowfullscreen></iframe>
                    </div>
                `
            },
            'menu/scenes': {
                title: 'Scenes',
                content: `
                    <div id="scenes-container" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center;">
                    </div>
                `,
                preloadId: 'preload-scenes'
            },
            'menu/playing': {
                title: 'PLAYING',
                content: `
                    <div id="now-playing" class="media-view">
                        <div class="playing-artwork-slot media-view-artwork">
                            <div class="playing-flipper">
                                <div class="playing-face playing-front">
                                    <img class="playing-artwork" src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7" alt="Album Art">
                                </div>
                                <div class="playing-face playing-back" style="display:none">
                                    <img class="playing-artwork-back" src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7" alt="">
                                </div>
                            </div>
                        </div>
                        <div class="playing-info-slot media-view-info">
                            <div id="media-title" class="media-view-title">—</div>
                            <div id="media-artist" class="media-view-artist">—</div>
                            <div id="media-album" class="media-view-album">—</div>
                        </div>
                    </div>`
            },
        };

        this._menuLoaded = false;
        this._menuRetries = 0;
        this._lastSelectedPath = null;

        // Callbacks wired by UIStore
        this.onNavigate = null;      // (path) => void
        this.onMenuLoaded = null;    // (data) => void — called after _fetchMenu succeeds
    }

    // ── Router menu fetch ──

    async fetchMenu() {
        try {
            const resp = await fetch(`${window.AppConfig?.routerUrl || 'http://localhost:8770'}/router/menu`);
            const data = await resp.json();
            if (!data || !data.items) return;

            // Load source view scripts on demand
            for (const item of data.items) {
                if (item.dynamic && item.preset && !window.SourcePresets?.[item.preset]) {
                    await this.loadSourceScript(item.preset);
                }
            }

            // Rebuild menu items from router response
            const newItems = [];
            for (const item of data.items) {
                const path = `menu/${item.id}`;

                // Webpage items: iframe view (preserved across navigations via rescue logic)
                if (item.type === 'webpage' && item.url) {
                    const containerId = `webpage-container-${item.id}`;
                    this.views[path] = {
                        title: item.title,
                        content: `<div id="${containerId}" class="webpage-container" style="position:absolute;top:0;left:0;width:100%;height:100%;"></div>`,
                        _webpage: { iframeId: `preload-webpage-${item.id}`, containerId, url: item.url }
                    };
                    newItems.push({ title: item.title, path });
                }
                // Dynamic sources: always register view from preset (even if menu item already exists)
                else if (item.dynamic && item.preset) {
                    if (!window.SourcePresets?.[item.preset]) {
                        await this.loadSourceScript(item.preset);
                    }
                    const preset = window.SourcePresets?.[item.preset];
                    if (preset) {
                        newItems.push({ title: item.title, path: preset.item.path });
                        if (preset.view) {
                            this.views[preset.item.path] = preset.view;
                        }
                    } else {
                        newItems.push({ title: item.title, path });
                    }
                } else {
                    const existing = this.menuItems.find(m => m.path === path);
                    newItems.push(existing || { title: item.title, path });
                }
            }
            this.menuItems = newItems;

            // Sync to laser position mapper
            if (window.LaserPositionMapper?.updateMenuItems) {
                window.LaserPositionMapper.updateMenuItems(this.menuItems);
            }

            this._menuLoaded = true;
            this._menuRetries = 0;

            // Notify UIStore so it can restore active source
            if (this.onMenuLoaded) this.onMenuLoaded(data);

            this.renderMenuItems();
            console.log(`[MENU] Loaded ${newItems.length} items from router (active: ${data.active_source || 'none'})`);
        } catch (e) {
            this._menuLoaded = true;
            const attempt = (this._menuRetries = (this._menuRetries || 0) + 1);
            if (attempt <= 15) {
                const delay = Math.min(attempt * 2000, 10000);
                console.log(`[MENU] Router unavailable, retrying in ${delay / 1000}s (attempt ${attempt})`);
                setTimeout(() => this.fetchMenu(), delay);
            } else {
                console.log('[MENU] Router unavailable after 15 attempts, using defaults');
            }
        }
    }

    /**
     * Dynamically load a source's view script (web/sources/{preset}/view.js).
     */
    loadSourceScript(preset) {
        return new Promise(resolve => {
            const existing = document.head.querySelector(`script[data-preset="${preset}"]`);
            if (existing) existing.remove();

            const script = document.createElement('script');
            script.src = `sources/${preset}/view.js`;
            script.dataset.preset = preset;
            script.onload = () => {
                console.log(`[MENU] Loaded source script: ${preset}`);
                const sp = window.SourcePresets?.[preset];
                if (sp?.view?.preloadId && sp.view.iframeSrc) {
                    this.preloadSourceIframe(sp.view.preloadId, sp.view.iframeSrc);
                    if (sp.item?.path) {
                        window.IframeMessenger?.registerIframe(sp.item.path, sp.view.preloadId);
                    }
                }
                resolve();
            };
            script.onerror = () => {
                console.warn(`[MENU] Source script not found: ${preset}`);
                resolve();
            };
            document.head.appendChild(script);
        });
    }

    // ── Iframe preloading ──

    preloadIframes() {
        const iframesToPreload = [
            { id: 'preload-scenes', src: 'softarc/scenes.html' }
        ];

        let preloadContainer = document.getElementById('iframe-preload-container');
        if (!preloadContainer) {
            preloadContainer = document.createElement('div');
            preloadContainer.id = 'iframe-preload-container';
            preloadContainer.style.cssText = 'position:absolute;left:-9999px;top:-9999px;width:1px;height:1px;overflow:hidden;';
            document.body.appendChild(preloadContainer);
        }

        iframesToPreload.forEach(({ id, src }) => {
            if (!document.getElementById(id)) {
                const iframe = document.createElement('iframe');
                iframe.id = id;
                iframe.src = src;
                iframe.style.cssText = 'width:1024px;height:768px;border:none;';
                preloadContainer.appendChild(iframe);
                console.log(`[PRELOAD] Loading ${src}`);
            }
        });
    }

    preloadSourceIframe(id, src) {
        if (document.getElementById(id)) return;

        let preloadContainer = document.getElementById('iframe-preload-container');
        if (!preloadContainer) {
            preloadContainer = document.createElement('div');
            preloadContainer.id = 'iframe-preload-container';
            preloadContainer.style.cssText = 'position:absolute;left:-9999px;top:-9999px;width:1px;height:1px;overflow:hidden;';
            document.body.appendChild(preloadContainer);
        }

        const iframe = document.createElement('iframe');
        iframe.id = id;
        iframe.src = src;
        iframe.style.cssText = 'width:1024px;height:768px;border:none;';
        preloadContainer.appendChild(iframe);
        console.log(`[PRELOAD] Loading source iframe: ${src}`);
    }

    reloadAllSourceIframes() {
        for (const sp of Object.values(window.SourcePresets || {})) {
            if (sp.view?.preloadId) {
                const iframe = document.getElementById(sp.view.preloadId);
                if (iframe?.contentWindow) {
                    iframe.contentWindow.postMessage({ type: 'reload-data' }, '*');
                }
            }
        }
    }

    attachPreloadedIframe(preloadId) {
        let containerId = null;
        let iframeSrc = null;

        for (const sp of Object.values(window.SourcePresets || {})) {
            if (sp.view?.preloadId === preloadId) {
                containerId = sp.view.containerId;
                iframeSrc = sp.view.iframeSrc;
                break;
            }
        }

        if (!containerId) {
            const builtins = {
                'preload-scenes': { container: 'scenes-container', src: 'softarc/scenes.html' },
                'preload-security': { container: 'security-container', src: 'softarc/security.html' },
            };
            const b = builtins[preloadId];
            if (b) { containerId = b.container; iframeSrc = b.src; }
        }

        if (!containerId) {
            console.warn(`[PRELOAD] No mapping for ${preloadId}`);
            return;
        }

        const container = document.getElementById(containerId);
        if (!container) {
            console.warn(`[PRELOAD] Container ${containerId} not found`);
            return;
        }

        let iframe = container.querySelector('iframe');
        if (iframe) {
            console.log(`[PRELOAD] Iframe already in ${containerId}`);
            return;
        }

        iframe = document.getElementById(preloadId);
        if (iframe) {
            iframe.style.cssText = 'width: 100%; height: 100%; border: none; border-radius: 8px; box-shadow: 0 5px 15px rgba(0,0,0,0.3);';
            container.appendChild(iframe);
            console.log(`[PRELOAD] Attached ${preloadId} to ${containerId}`);
        } else if (iframeSrc) {
            iframe = document.createElement('iframe');
            iframe.id = preloadId;
            iframe.src = iframeSrc;
            iframe.style.cssText = 'width: 100%; height: 100%; border: none; border-radius: 8px; box-shadow: 0 5px 15px rgba(0,0,0,0.3);';
            container.appendChild(iframe);
            console.log(`[PRELOAD] Created fresh iframe for ${containerId}`);
        }
    }

    // ── Rendering ──

    getStartItemAngle() {
        const visibleCount = this.menuItems.length;
        const totalSpan = this.angleStep * (visibleCount - 1);
        return 180 - totalSpan / 2;
    }

    renderMenuItems() {
        const menuContainer = document.getElementById('menuItems');
        if (!menuContainer) return;
        menuContainer.innerHTML = '';

        const visibleItems = this.menuItems;
        visibleItems.forEach((item, index) => {
            const itemElement = document.createElement('div');
            itemElement.className = 'list-item';
            itemElement.dataset.path = item.path;
            itemElement.textContent = item.title;

            const itemAngle = this.getStartItemAngle() + (visibleItems.length - 1 - index) * this.angleStep;
            const position = arcs.getArcPoint(this.radius, 20, itemAngle);

            Object.assign(itemElement.style, {
                position: 'absolute',
                left: `${position.x - 100}px`,
                top: `${position.y - 25}px`,
                width: '100px',
                height: '50px',
                cursor: 'pointer'
            });

            itemElement.addEventListener('mouseenter', () => {
                if (this.onItemHover) this.onItemHover(itemAngle);
            });

            if (item.path === this._lastSelectedPath) {
                itemElement.classList.add('selectedItem');
            }

            menuContainer.appendChild(itemElement);
        });
    }

    /**
     * Bold the menu item at selectedIndex; click when selectedPath changes.
     */
    applyMenuHighlight(selectedIndex, selectedPath) {
        const menuContainer = document.getElementById('menuItems');
        if (!menuContainer) return;

        const menuElements = menuContainer.querySelectorAll('.list-item');
        menuElements.forEach((el, i) => {
            if (i === selectedIndex) {
                el.classList.add('selectedItem');
            } else {
                el.classList.remove('selectedItem');
            }
        });

        // Click exactly when the bolded item changes — one click per highlight change
        const changed = selectedPath && selectedPath !== this._lastSelectedPath;
        this._lastSelectedPath = selectedPath;
        return changed; // caller sends click command
    }

    // ── Dynamic add/remove ──

    addMenuItem(item, afterPath, viewDef) {
        if (this.menuItems.some(m => m.path === item.path)) {
            console.log(`[MENU] Item ${item.path} already exists`);
            return;
        }

        const afterIndex = this.menuItems.findIndex(m => m.path === afterPath);
        const insertAt = afterIndex !== -1 ? afterIndex + 1 : this.menuItems.length;
        this.menuItems.splice(insertAt, 0, { title: item.title, path: item.path });

        if (viewDef) {
            this.views[item.path] = viewDef;
        }

        if (window.LaserPositionMapper?.updateMenuItems) {
            window.LaserPositionMapper.updateMenuItems(this.menuItems);
        }

        console.log(`[MENU] Added "${item.title}" after ${afterPath} (now ${this.menuItems.length} items)`);
        this.renderMenuItemsAnimated();
    }

    removeMenuItem(path) {
        const index = this.menuItems.findIndex(m => m.path === path);
        if (index === -1) {
            console.log(`[MENU] Item ${path} not found`);
            return;
        }

        // If currently viewing the removed item, navigate to adjacent
        if (this.onNavigate && this._currentRoute === path) {
            const adjacentPath = this.menuItems[index - 1]?.path || this.menuItems[index + 1]?.path || 'menu/playing';
            this.onNavigate(adjacentPath);
        }

        this.menuItems.splice(index, 1);
        delete this.views[path];

        if (window.LaserPositionMapper?.updateMenuItems) {
            window.LaserPositionMapper.updateMenuItems(this.menuItems);
        }

        console.log(`[MENU] Removed "${path}" (now ${this.menuItems.length} items)`);
        this.renderMenuItemsAnimated();
    }

    /**
     * Re-render menu items with FLIP animation.
     * Existing items slide to their new positions, new items fade in.
     */
    renderMenuItemsAnimated() {
        const menuContainer = document.getElementById('menuItems');
        if (!menuContainer) return;

        // --- FIRST: record old positions keyed by data-path ---
        const oldPositions = {};
        menuContainer.querySelectorAll('.list-item[data-path]').forEach(el => {
            const rect = el.getBoundingClientRect();
            oldPositions[el.dataset.path] = { left: rect.left, top: rect.top };
        });

        // --- Rebuild DOM ---
        menuContainer.innerHTML = '';
        const visibleItems = this.menuItems;
        visibleItems.forEach((item, index) => {
            const itemElement = document.createElement('div');
            itemElement.className = 'list-item';
            itemElement.dataset.path = item.path;
            itemElement.textContent = item.title;

            const itemAngle = this.getStartItemAngle() + (visibleItems.length - 1 - index) * this.angleStep;
            const position = arcs.getArcPoint(this.radius, 20, itemAngle);

            Object.assign(itemElement.style, {
                position: 'absolute',
                left: `${position.x - 100}px`,
                top: `${position.y - 25}px`,
                width: '100px',
                height: '50px',
                cursor: 'pointer'
            });

            itemElement.addEventListener('mouseenter', () => {
                if (this.onItemHover) this.onItemHover(itemAngle);
            });

            if (item.path === this._lastSelectedPath) {
                itemElement.classList.add('selectedItem');
            }

            menuContainer.appendChild(itemElement);
        });

        // --- LAST + INVERT + PLAY ---
        menuContainer.querySelectorAll('.list-item[data-path]').forEach(el => {
            const path = el.dataset.path;
            const newRect = el.getBoundingClientRect();

            if (oldPositions[path]) {
                const dx = oldPositions[path].left - newRect.left;
                const dy = oldPositions[path].top - newRect.top;
                if (Math.abs(dx) > 0.5 || Math.abs(dy) > 0.5) {
                    el.animate([
                        { transform: `translate(${dx}px, ${dy}px)` },
                        { transform: 'translate(0, 0)' }
                    ], { duration: 300, easing: 'ease-out' });
                }
            } else {
                el.animate([
                    { opacity: 0, transform: 'translateX(-20px)' },
                    { opacity: 1, transform: 'translateX(0)' }
                ], { duration: 300, delay: 150, easing: 'ease-out', fill: 'backwards' });
            }
        });
    }
}

window.MenuManager = MenuManager;
