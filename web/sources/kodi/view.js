/**
 * Kodi Source Preset
 *
 * Default iframe target assumes the deployed page is available at `softarc/kodi.html`.
 * If your deployed filename differs, change `KODI_IFRAME_SRC` below.
 */
const KODI_IFRAME_SRC = (window.AppConfig && window.AppConfig.kodiIframeSrc) || 'softarc/kodi.html';

const _kodiPlayingPreset = (() => {
    const PAGE_IDS = ['now', 'options', 'transfer'];
    const PAGE_CYCLE_COOLDOWN_MS = 520;
    const YOUTUBE_PREF_KEY = 'bs5c.youtubeVideosEnabled';
    const PLAYING_MENU_ITEMS = [
        { id: 'transfer', name: 'Transfer Queue' },
    ];
    let currentPageIndex = 0;
    let mountedContainer = null;
    let lastPageCycleAt = 0;
    let optionState = { focusIndex: 0 };
    let transferState = { selectedIndex: 0, focusIndex: 0, sending: false, message: '', error: '' };
    let lastMedia = { title: '-', artist: '-', album: '-', artwork: '', state: 'idle' };

    function youtubeVideosEnabled() {
        if (window.MusicVideoPreference) return window.MusicVideoPreference.enabled !== false;
        try {
            return localStorage.getItem(YOUTUBE_PREF_KEY) !== 'false';
        } catch (error) {
            return true;
        }
    }

    function setYoutubeVideosEnabled(enabled) {
        const normalized = enabled !== false;
        if (window.PlaybackTargets?.setMusicVideoEnabled) {
            void window.PlaybackTargets.setMusicVideoEnabled(normalized);
        } else if (window.MusicVideoPreference?.setEnabled) {
            window.MusicVideoPreference.setEnabled(normalized);
        } else {
            try {
                localStorage.setItem(YOUTUBE_PREF_KEY, normalized ? 'true' : 'false');
            } catch (error) {}
            document.dispatchEvent(new CustomEvent('bs5c:music-video-preference', {
                detail: { enabled: normalized },
            }));
        }
        return normalized;
    }

    function toggleYoutubeVideos() {
        const enabled = setYoutubeVideosEnabled(!youtubeVideosEnabled());
        transferState.message = `YouTube videos ${enabled ? 'enabled' : 'disabled'}`;
        transferState.error = '';
        if (mountedContainer) renderOverlay(mountedContainer);
        return true;
    }

    function ensureStyles() {
        if (document.getElementById('kodi-playing-preset-style')) return;
        const style = document.createElement('style');
        style.id = 'kodi-playing-preset-style';
        style.textContent = `
            #now-playing.kodi-playing-active { position: relative; overflow: hidden; }
            #now-playing.kodi-playing-active.immersive-active { overflow: visible; }
            #now-playing.kodi-playing-active .kodi-playing-overlay {
                position: absolute; inset: 0; display: flex; align-items: flex-start; justify-content: flex-end;
                pointer-events: none; z-index: 3;
            }
            #now-playing.kodi-playing-active .kodi-playing-panel {
                width: min(500px, calc(100vw - 500px)); max-height: 72vh; margin: 8vh 54px 0 auto;
                padding: 16px 18px 14px; display: flex; flex-direction: column;
                justify-content: flex-start;
                background: rgba(10, 10, 10, 0.84);
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 24px;
                box-shadow: 0 22px 48px rgba(0, 0, 0, 0.42);
                backdrop-filter: blur(16px);
                opacity: 0; transform: translateY(-8px);
                transition: opacity 160ms ease, transform 160ms ease;
                overflow: hidden; pointer-events: auto;
            }
            #now-playing.kodi-playing-active[data-kodi-page="options"] .kodi-playing-panel,
            #now-playing.kodi-playing-active[data-kodi-page="transfer"] .kodi-playing-panel {
                opacity: 1; transform: translateY(0);
            }
            #now-playing.kodi-playing-active .kodi-playing-kicker {
                font: 600 11px/1.2 Arial, sans-serif; letter-spacing: 1.8px;
                text-transform: uppercase; color: rgba(255, 255, 255, 0.58);
                margin-bottom: 6px;
            }
            #now-playing.kodi-playing-active .kodi-playing-heading {
                font: 300 22px/1.12 Arial, sans-serif; color: #fff; margin-bottom: 8px;
            }
            #now-playing.kodi-playing-active .kodi-playing-copy {
                min-height: 0; margin-bottom: 6px; font: 400 11px/1.35 Arial, sans-serif;
                color: rgba(255, 255, 255, 0.56);
                white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
            }
            #now-playing.kodi-playing-active .kodi-playing-meta {
                margin-bottom: 10px; font: 500 11px/1.25 Arial, sans-serif;
                color: rgba(255, 255, 255, 0.45);
                letter-spacing: 1.2px; text-transform: uppercase;
            }
            #now-playing.kodi-playing-active .kodi-transfer-options {
                display: flex; flex-direction: column; gap: 4px;
                max-height: 52vh; overflow-y: auto; padding-right: 2px; pointer-events: auto;
            }
            #now-playing.kodi-playing-active .kodi-transfer-options::-webkit-scrollbar { display: none; }
            #now-playing.kodi-playing-active .kodi-transfer-option,
            #now-playing.kodi-playing-active .kodi-youtube-toggle {
                width: 100%; min-height: 38px; border: 1px solid transparent;
                border-radius: 12px; background: transparent;
                color: #fff; font: 500 14px/1.2 Arial, sans-serif; text-align: left;
                padding: 8px 10px; touch-action: manipulation;
            }
            #now-playing.kodi-playing-active .kodi-transfer-option.active {
                box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.18);
            }
            #now-playing.kodi-playing-active .kodi-transfer-option.focused,
            #now-playing.kodi-playing-active .kodi-youtube-toggle.focused {
                background: rgba(255, 255, 255, 0.14);
                border-color: rgba(255, 255, 255, 0.28);
                box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.28);
            }
            #now-playing.kodi-playing-active .kodi-youtube-toggle {
                display: grid; grid-template-columns: 1fr 52px; gap: 12px; align-items: center;
            }
            #now-playing.kodi-playing-active .kodi-youtube-switch {
                position: relative; width: 44px; height: 24px; border-radius: 999px;
                background: rgba(255, 255, 255, 0.18); justify-self: end;
            }
            #now-playing.kodi-playing-active .kodi-youtube-switch::after {
                content: ""; position: absolute; left: 3px; top: 3px; width: 18px; height: 18px;
                border-radius: 999px; background: rgba(255, 255, 255, 0.9);
                transition: transform 160ms ease;
            }
            #now-playing.kodi-playing-active .kodi-youtube-toggle.active .kodi-youtube-switch {
                background: rgba(255, 255, 255, 0.36);
            }
            #now-playing.kodi-playing-active .kodi-youtube-toggle.active .kodi-youtube-switch::after {
                transform: translateX(20px);
            }
            #now-playing.kodi-playing-active .kodi-playing-indicators {
                display: none;
            }
            #now-playing.kodi-playing-active .kodi-paused-overlay {
                position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
                gap: 18px; background: rgba(0, 0, 0, 0.34); opacity: 0;
                transition: opacity 180ms ease; pointer-events: none; z-index: 3;
            }
            #now-playing.kodi-playing-active.is-kodi-paused .kodi-paused-overlay { opacity: 1; }
            #now-playing.kodi-playing-active .kodi-paused-bar {
                width: 18px; height: 96px; border-radius: 3px;
                background: rgba(255, 255, 255, 0.88);
                box-shadow: 0 12px 36px rgba(0, 0, 0, 0.38);
            }
        `;
        document.head.appendChild(style);
    }

    function transferTargets() {
        return (window.PlaybackTargets?.targetsFor?.('kodi') || [])
            .map((target) => ({
                id: String(target.id || '').trim(),
                name: String(target.name || target.label || target.id || '').trim(),
            }))
            .filter((target) => target.id)
            .map((target) => ({ ...target, name: target.name || target.id }));
    }

    function youtubeFocusIndex() {
        return transferTargets().length;
    }

    function canShowTransferOverlay() {
        return window.uiStore?.currentRoute === 'menu/playing'
            && window.uiStore?.menuVisible !== false;
    }

    function currentPageId() {
        if (!canShowTransferOverlay()) return 'now';
        return PAGE_IDS[currentPageIndex] || 'now';
    }

    function clampOptionFocus() {
        const maxFocus = Math.max(0, PLAYING_MENU_ITEMS.length - 1);
        optionState.focusIndex = Math.max(0, Math.min(optionState.focusIndex || 0, maxFocus));
    }

    function clampTransferFocus() {
        const targets = transferTargets();
        const maxFocus = Math.max(0, targets.length);
        transferState.selectedIndex = Math.max(0, Math.min(transferState.selectedIndex, Math.max(0, targets.length - 1)));
        transferState.focusIndex = Math.max(0, Math.min(transferState.focusIndex || 0, maxFocus));
        if (transferState.focusIndex < targets.length) {
            transferState.selectedIndex = transferState.focusIndex;
        }
    }

    function currentTransferTarget() {
        const targets = transferTargets();
        clampTransferFocus();
        return targets[transferState.selectedIndex] || targets[0] || null;
    }

    function openOptionsMenu() {
        if (!canShowTransferOverlay()) return false;
        currentPageIndex = 1;
        clampOptionFocus();
        transferState.message = '';
        transferState.error = '';
        if (mountedContainer) renderOverlay(mountedContainer);
        return true;
    }

    function openTransferMenu() {
        if (!canShowTransferOverlay()) return false;
        currentPageIndex = 2;
        clampTransferFocus();
        transferState.focusIndex = Math.min(
            Math.max(0, transferState.selectedIndex || 0),
            youtubeFocusIndex(),
        );
        transferState.message = '';
        transferState.error = '';
        if (mountedContainer) renderOverlay(mountedContainer);
        return true;
    }

    function closePlayingMenu() {
        currentPageIndex = 0;
        if (mountedContainer) renderOverlay(mountedContainer);
        return true;
    }

    function closeTransferMenu() {
        currentPageIndex = 1;
        if (mountedContainer) renderOverlay(mountedContainer);
        return true;
    }

    function stepOptionFocus(delta) {
        if (currentPageId() !== 'options') return;
        const count = PLAYING_MENU_ITEMS.length;
        if (!count) return;
        optionState.focusIndex = Math.max(0, Math.min(count - 1, (optionState.focusIndex || 0) + delta));
        if (mountedContainer) renderOverlay(mountedContainer);
    }

    function stepTransferFocus(delta) {
        if (currentPageId() !== 'transfer' || !canShowTransferOverlay()) return;

        const targets = transferTargets();
        const count = targets.length + 1;
        if (!count) return;
        transferState.focusIndex = Math.max(
            0,
            Math.min(count - 1, (transferState.focusIndex || 0) + delta),
        );
        if (transferState.focusIndex < targets.length) {
            transferState.selectedIndex = transferState.focusIndex;
        }
        transferState.message = '';
        transferState.error = '';
        if (mountedContainer) renderOverlay(mountedContainer);
    }

    function selectTransferTarget(targetId) {
        const targets = transferTargets();
        const index = targets.findIndex((target) => target.id === targetId);
        if (index >= 0) {
            transferState.selectedIndex = index;
            transferState.focusIndex = index;
            transferState.message = `Selected ${targets[index].name}`;
            transferState.error = '';
            if (mountedContainer) renderOverlay(mountedContainer);
        }
    }

    function activateOptionFocus(optionId = '') {
        const normalized = String(optionId || PLAYING_MENU_ITEMS[optionState.focusIndex]?.id || '').trim().toLowerCase();
        if (normalized === 'transfer') {
            return openTransferMenu();
        }
        return false;
    }

    function activateTransferFocus() {
        const targets = transferTargets();
        clampTransferFocus();
        if (transferState.focusIndex < targets.length) {
            void transferQueueToSelected({ closeOnSuccess: true });
            return true;
        }
        const handled = toggleYoutubeVideos();
        if (mountedContainer) renderOverlay(mountedContainer);
        return handled;
    }

    function renderPlayingOptions(targetsEl) {
        clampOptionFocus();
        targetsEl.innerHTML = '';
        PLAYING_MENU_ITEMS.forEach((item, index) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'kodi-transfer-option';
            if (optionState.focusIndex === index) button.classList.add('focused', 'active');
            button.dataset.playingOption = item.id;
            button.textContent = item.name;
            targetsEl.appendChild(button);
        });
    }

    function ensureOverlay(container) {
        let overlay = container.querySelector('.kodi-playing-overlay');
        if (overlay) return overlay;
        overlay = document.createElement('div');
        overlay.className = 'kodi-playing-overlay';
        overlay.innerHTML = `
            <div class="kodi-paused-overlay" hidden>
                <span class="kodi-paused-bar"></span>
                <span class="kodi-paused-bar"></span>
            </div>
            <div class="kodi-playing-panel">
                <div class="kodi-playing-kicker">Video</div>
                <div class="kodi-playing-heading">Target</div>
                <div class="kodi-playing-copy"></div>
                <div class="kodi-playing-meta"></div>
                <div class="kodi-transfer-options"></div>
            </div>
            <div class="kodi-playing-indicators">
                <span class="kodi-playing-indicator" data-page="now"></span>
                <span class="kodi-playing-indicator" data-page="transfer"></span>
            </div>
        `;
        overlay.addEventListener('click', (event) => {
            const playingOption = event.target.closest('[data-playing-option]');
            if (playingOption) {
                activateOptionFocus(playingOption.dataset.playingOption);
                return;
            }
            const option = event.target.closest('[data-kodi-target]');
            if (option) {
                selectTransferTarget(option.dataset.kodiTarget);
                void transferQueueToSelected({ closeOnSuccess: true });
                return;
            }
            if (event.target.closest('[data-youtube-toggle]')) {
                transferState.focusIndex = youtubeFocusIndex();
                toggleYoutubeVideos();
                if (mountedContainer) renderOverlay(mountedContainer);
            }
        });
        container.classList.add('kodi-playing-active');
        container.appendChild(overlay);
        return overlay;
    }

    function updateBaseView(container, data) {
        const titleEl = container.querySelector('.media-view-title');
        const artistEl = container.querySelector('.media-view-artist');
        const albumEl = container.querySelector('.media-view-album');
        if (typeof window.crossfadeText === 'function') {
            window.crossfadeText(titleEl, data?.title || '-');
            window.crossfadeText(artistEl, data?.artist || '-');
            window.crossfadeText(albumEl, data?.album || '-');
        } else {
            if (titleEl) titleEl.textContent = data?.title || '-';
            if (artistEl) artistEl.textContent = data?.artist || '-';
            if (albumEl) albumEl.textContent = data?.album || '-';
        }
        const img = container.querySelector('.playing-artwork');
        if (img && window.ArtworkManager) {
            window.ArtworkManager.displayArtwork(img, data?.artwork || '', 'noArtwork');
        } else if (img && data?.artwork) {
            img.src = data.artwork;
        }
    }

    function renderTransferOptions(targetsEl) {
        const selected = currentTransferTarget();
        const targets = transferTargets();
        clampTransferFocus();
        targetsEl.innerHTML = '';
        if (!targets.length) {
            const empty = document.createElement('div');
            empty.className = 'kodi-playing-copy';
            empty.textContent = 'No Kodi targets configured';
            targetsEl.appendChild(empty);
        }
        targets.forEach((target, index) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'kodi-transfer-option';
            if (target.id === selected?.id) button.classList.add('active');
            if (transferState.focusIndex === index) button.classList.add('focused');
            button.dataset.kodiTarget = target.id;
            button.textContent = target.name;
            targetsEl.appendChild(button);
        });
        const youtubeEnabled = youtubeVideosEnabled();
        const youtube = document.createElement('button');
        youtube.type = 'button';
        youtube.className = 'kodi-youtube-toggle';
        youtube.dataset.youtubeToggle = '1';
        youtube.setAttribute('role', 'switch');
        youtube.setAttribute('aria-checked', youtubeEnabled ? 'true' : 'false');
        if (youtubeEnabled) youtube.classList.add('active');
        if (transferState.focusIndex === youtubeFocusIndex()) youtube.classList.add('focused');
        youtube.innerHTML = `
            <span>YouTube Videos ${youtubeEnabled ? 'On' : 'Off'}</span>
            <span class="kodi-youtube-switch"></span>
        `;
        targetsEl.appendChild(youtube);
        requestAnimationFrame(() => {
            targetsEl.querySelector('.focused')?.scrollIntoView({
                block: 'nearest',
            });
        });
    }

    function renderOverlay(container) {
        if (!container) return;
        const overlay = ensureOverlay(container);
        const pageId = currentPageId();
        const isPaused = String(lastMedia.state || '').trim().toLowerCase() === 'paused';
        container.dataset.kodiPage = pageId;
        container.classList.toggle('is-kodi-paused', isPaused);
        const pausedEl = overlay.querySelector('.kodi-paused-overlay');
        if (pausedEl) pausedEl.hidden = !isPaused;
        overlay.querySelectorAll('.kodi-playing-indicator').forEach((node) => {
            node.classList.toggle('active', node.dataset.page === pageId);
        });

        const targetsEl = overlay.querySelector('.kodi-transfer-options');
        const copyEl = overlay.querySelector('.kodi-playing-copy');
        const metaEl = overlay.querySelector('.kodi-playing-meta');
        if (!targetsEl || !copyEl || !metaEl) return;
        if (pageId === 'now') {
            targetsEl.hidden = true;
            copyEl.textContent = '';
            copyEl.hidden = true;
            metaEl.textContent = lastMedia.state ? `State: ${String(lastMedia.state).toUpperCase()}` : '';
            metaEl.hidden = !metaEl.textContent;
            return;
        }
        targetsEl.hidden = false;
        if (pageId === 'options') {
            copyEl.textContent = '';
            copyEl.hidden = true;
            metaEl.textContent = PLAYING_MENU_ITEMS.length ? 'LEFT Open   RIGHT Back' : '';
            metaEl.hidden = !metaEl.textContent;
            renderPlayingOptions(targetsEl);
            return;
        }
        copyEl.textContent = transferState.error || transferState.message || '';
        copyEl.hidden = !copyEl.textContent;
        metaEl.textContent = transferTargets().length
            ? 'GO Select   RIGHT Back'
            : 'Populate kodi.transfer_targets in config';
        metaEl.hidden = !metaEl.textContent;
        renderTransferOptions(targetsEl);
    }

    async function transferQueueToSelected(options = {}) {
        const closeOnSuccess = options.closeOnSuccess === true;
        if (transferState.sending) return true;
        const target = currentTransferTarget();
        if (!target) return true;
        transferState.sending = true;
        transferState.message = `Transferring queue to ${target.name}`;
        transferState.error = '';
        if (mountedContainer) renderOverlay(mountedContainer);
        try {
            if (window.PlaybackTargets?.setVideoTarget) {
                await window.PlaybackTargets.setVideoTarget(target.id);
            }
            const serviceUrl = (typeof getServiceUrl === 'function')
                ? getServiceUrl('kodiServiceUrl', 8782)
                : 'http://localhost:8782';
            const response = await fetch(`${serviceUrl}/command`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    command: 'transfer_queue',
                    target_player_id: target.id,
                    ...(window.PlaybackTargets?.payloadFor?.('kodi') || {}),
                }),
            });
            let payload = null;
            try {
                payload = await response.json();
            } catch (error) {}
            if (!response.ok || !payload || payload.state === 'error' || payload.status === 'error') {
                transferState.error = `Unable to transfer to ${target.name}`;
                transferState.message = '';
            } else {
                transferState.message = `Queue transferred to ${target.name}`;
                transferState.error = '';
                if (closeOnSuccess) {
                    currentPageIndex = 0;
                }
            }
        } catch (error) {
            transferState.error = `Unable to transfer to ${target.name}`;
            transferState.message = '';
        } finally {
            transferState.sending = false;
            if (mountedContainer) renderOverlay(mountedContainer);
        }
        return true;
    }

    function cyclePage(data) {
        const now = Date.now();
        if (now - lastPageCycleAt < PAGE_CYCLE_COOLDOWN_MS) return true;
        lastPageCycleAt = now;
        const pageId = currentPageId();
        const direction = String(data?.direction || 'clock').toLowerCase();
        const delta = direction === 'counter' ? -1 : 1;
        if (pageId === 'now') {
            return openOptionsMenu();
        }
        if (pageId === 'options') {
            stepOptionFocus(delta);
            return true;
        }
        stepTransferFocus(delta);
        if (mountedContainer) renderOverlay(mountedContainer);
        return true;
    }

    function handleButton(button) {
        const normalized = String(button || '').toLowerCase();
        if (normalized === '__close_transfer_overlay__') {
            return closePlayingMenu();
        }
        const pageId = currentPageId();
        if (pageId === 'now') {
            return false;
        }
        if (pageId === 'options') {
            if (normalized === 'right') return closePlayingMenu();
            if (normalized === 'left' || normalized === 'go') return activateOptionFocus();
            if (normalized === 'up') {
                stepOptionFocus(-1);
                return true;
            }
            if (normalized === 'down') {
                stepOptionFocus(1);
                return true;
            }
            return false;
        }
        if (normalized === 'right') {
            return closeTransferMenu();
        }
        if (normalized === 'up') {
            stepTransferFocus(-1);
            return true;
        }
        if (normalized === 'down') {
            stepTransferFocus(1);
            return true;
        }
        if (normalized === 'go') {
            return activateTransferFocus();
        }
        if (normalized === 'left') return true;
        return false;
    }

    document.addEventListener('bs5c:music-video-preference', () => {
        if (mountedContainer && currentPageId() !== 'now') {
            renderOverlay(mountedContainer);
        }
    });

    document.addEventListener('bs5c:playback-targets', () => {
        if (mountedContainer && currentPageId() !== 'now') {
            const targets = transferTargets();
            transferState.selectedIndex = Math.max(0, Math.min(transferState.selectedIndex, Math.max(0, targets.length - 1)));
            transferState.focusIndex = Math.max(0, Math.min(transferState.focusIndex || 0, Math.max(0, targets.length)));
            renderOverlay(mountedContainer);
        }
    });

    return {
        onMount(container) {
            ensureStyles();
            mountedContainer = container;
            currentPageIndex = 0;
            optionState = { focusIndex: 0 };
            transferState = { selectedIndex: 0, focusIndex: 0, sending: false, message: '', error: '' };
            ensureOverlay(container);
            updateBaseView(container, lastMedia);
            renderOverlay(container);
        },
        onUpdate(container, data) {
            mountedContainer = container;
            lastMedia = { ...lastMedia, ...(data || {}) };
            ensureOverlay(container);
            updateBaseView(container, lastMedia);
            renderOverlay(container);
        },
        onRemove(container) {
            mountedContainer = null;
            currentPageIndex = 0;
            optionState = { focusIndex: 0 };
            transferState = { selectedIndex: 0, focusIndex: 0, sending: false, message: '', error: '' };
            const overlay = container?.querySelector('.kodi-playing-overlay');
            if (overlay) overlay.remove();
            if (container) {
                container.classList.remove('kodi-playing-active');
                container.classList.remove('is-kodi-paused');
                container.removeAttribute('data-kodi-page');
            }
        },
        cyclePage,
        handleButton,
    };
})();

document.addEventListener('bs5c:menu-visibility', (event) => {
    if (event.detail?.visible === false) {
        _kodiPlayingPreset.handleButton('__close_transfer_overlay__');
    }
});

const _kodiController = (() => {
    function currentRoute() {
        return window.uiStore?.currentRoute || '';
    }

    function isPlayingRoute() {
        return currentRoute() === 'menu/playing';
    }

    function sendToIframe(type, data) {
        if (!window.IframeMessenger) {
            console.error('[KODI UI] IframeMessenger is missing!');
            return false;
        }
        console.log(`[KODI UI] Sending ${type} to iframe:`, data);
        return IframeMessenger.sendToRoute('menu/kodi', type, data);
    }

    async function sendTransport(command) {
        const serviceUrl = (typeof getServiceUrl === 'function')
            ? getServiceUrl('kodiServiceUrl', 8782)
            : 'http://localhost:8782';
        try {
            const response = await fetch(`${serviceUrl}/command`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    command,
                    ...(window.PlaybackTargets?.payloadFor?.('kodi') || {}),
                }),
            });
            if (!response.ok) {
                console.warn('[KODI UI] Transport command failed:', command, response.status);
                return false;
            }
            return true;
        } catch (error) {
            console.warn('[KODI UI] Transport command error:', command, error);
            return false;
        }
    }

    return {
        get isActive() { return true; },

        updateMetadata(data) {
            const container = document.getElementById('now-playing');
            if (container && window.uiStore?.activeSource === 'kodi' && window.uiStore.currentRoute === 'menu/playing') {
                _kodiPlayingPreset.onUpdate(container, data || window.uiStore.mediaInfo || {});
            }
        },

        handleNavEvent(data) {
            if (isPlayingRoute()) return _kodiPlayingPreset.cyclePage(data);
            console.log('[KODI UI] Hardware Wheel Turned:', data);
            return sendToIframe('nav', { data });
        },

        handleButton(button) {
            console.log('[KODI UI] Hardware Button Pressed:', button);
            const normalized = String(button || '').toLowerCase();
            if (isPlayingRoute()) {
                if (_kodiPlayingPreset.handleButton(normalized)) {
                    return true;
                }
                if (normalized === 'left') {
                    void sendTransport('transport_previous');
                    return true;
                }
                if (normalized === 'right') {
                    void sendTransport('transport_next');
                    return true;
                }
                if (normalized === 'go') {
                    void sendTransport('transport_toggle');
                    return true;
                }
                if (normalized === 'go_long' || normalized === 'go_hold') {
                    void sendTransport('transport_stop');
                    return true;
                }
                return true;
            }
            if (sendToIframe('button', { button })) return true;
            return false;
        },
    };
})();

window.SourcePresets = window.SourcePresets || {};
window.SourcePresets.kodi = {
    controller: _kodiController,
    playing: _kodiPlayingPreset,
    item: { title: 'KODI', path: 'menu/kodi' },
    after: 'menu/playing',
    view: {
        title: 'KODI',
        content: '<div id="kodi-container" style="width:100%;height:100%;"></div>',
        containerId: 'kodi-container',
        preloadId: 'preload-kodi',
        iframeSrc: KODI_IFRAME_SRC
    },

    onAdd() {},

    onMount() {
        if (window.IframeMessenger) {
            IframeMessenger.registerIframe('menu/kodi', 'preload-kodi');
        }
    },

    onRemove() {
        if (window.IframeMessenger) {
            IframeMessenger.unregisterIframe('menu/kodi');
        }
    },
};
