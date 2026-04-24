/**
 * Kodi Source Preset
 *
 * Default iframe target assumes the deployed page is available at `softarc/kodi.html`.
 * If your deployed filename differs, change `KODI_IFRAME_SRC` below.
 */
const KODI_IFRAME_SRC = (window.AppConfig && window.AppConfig.kodiIframeSrc) || 'softarc/kodi.html';

const _kodiPlayingPreset = (() => {
    const PAGE_IDS = ['now', 'transfer'];
    const PAGE_CYCLE_COOLDOWN_MS = 520;
    const YOUTUBE_PREF_KEY = 'bs5c.youtubeVideosEnabled';
    let currentPageIndex = 0;
    let mountedContainer = null;
    let lastPageCycleAt = 0;
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
                position: absolute; inset: 0; display: flex; justify-content: flex-end;
                pointer-events: none; z-index: 3;
            }
            #now-playing.kodi-playing-active .kodi-playing-panel {
                width: min(44%, 390px); height: 100%; margin-left: auto;
                padding: 48px 42px 42px; display: flex; flex-direction: column;
                justify-content: center;
                background: linear-gradient(90deg, rgba(8, 10, 16, 0) 0%, rgba(8, 10, 16, 0.56) 20%, rgba(8, 10, 16, 0.9) 100%);
                opacity: 0; transform: translateX(18px);
                transition: opacity 180ms ease, transform 180ms ease;
            }
            #now-playing.kodi-playing-active[data-kodi-page="transfer"] .kodi-playing-panel {
                opacity: 1; transform: translateX(0);
            }
            #now-playing.kodi-playing-active .kodi-playing-kicker {
                font: 600 11px/1.2 Arial, sans-serif; letter-spacing: 2.8px;
                text-transform: uppercase; color: rgba(160, 184, 222, 0.8);
                margin-bottom: 14px;
            }
            #now-playing.kodi-playing-active .kodi-playing-heading {
                font: 300 30px/1.08 Arial, sans-serif; color: #fff; margin-bottom: 14px;
            }
            #now-playing.kodi-playing-active .kodi-playing-copy {
                min-height: 20px; font: 400 16px/1.55 Arial, sans-serif;
                color: rgba(223, 232, 247, 0.82);
            }
            #now-playing.kodi-playing-active .kodi-playing-meta {
                margin-top: 18px; font: 500 13px/1.5 Arial, sans-serif;
                color: rgba(160, 184, 222, 0.78);
            }
            #now-playing.kodi-playing-active .kodi-transfer-options {
                margin-top: 18px; display: flex; flex-direction: column; gap: 10px;
                pointer-events: auto;
            }
            #now-playing.kodi-playing-active .kodi-transfer-option,
            #now-playing.kodi-playing-active .kodi-transfer-action,
            #now-playing.kodi-playing-active .kodi-youtube-toggle {
                width: 100%; min-height: 42px; border: 1px solid rgba(148, 199, 255, 0.26);
                border-radius: 6px; background: rgba(255, 255, 255, 0.08);
                color: #fff; font: 500 14px/1.2 Arial, sans-serif; text-align: left;
                padding: 0 14px; touch-action: manipulation;
            }
            #now-playing.kodi-playing-active .kodi-transfer-option.active {
                background: rgba(148, 199, 255, 0.22);
                border-color: rgba(158, 209, 255, 0.74);
            }
            #now-playing.kodi-playing-active .kodi-transfer-option.focused,
            #now-playing.kodi-playing-active .kodi-transfer-action.focused,
            #now-playing.kodi-playing-active .kodi-youtube-toggle.focused {
                border-color: rgba(255, 255, 255, 0.86);
                box-shadow: 0 0 0 2px rgba(158, 209, 255, 0.34);
            }
            #now-playing.kodi-playing-active .kodi-transfer-action {
                text-align: center; background: rgba(158, 209, 255, 0.18);
            }
            #now-playing.kodi-playing-active .kodi-youtube-toggle {
                min-height: 46px; border-color: rgba(255, 255, 255, 0.16);
                display: grid; grid-template-columns: 1fr 52px; gap: 12px; align-items: center;
            }
            #now-playing.kodi-playing-active .kodi-youtube-switch {
                position: relative; width: 44px; height: 24px; border-radius: 999px;
                background: rgba(255, 255, 255, 0.22); justify-self: end;
            }
            #now-playing.kodi-playing-active .kodi-youtube-switch::after {
                content: ""; position: absolute; left: 3px; top: 3px; width: 18px; height: 18px;
                border-radius: 999px; background: rgba(255, 255, 255, 0.9);
                transition: transform 160ms ease;
            }
            #now-playing.kodi-playing-active .kodi-youtube-toggle.active .kodi-youtube-switch {
                background: rgba(158, 209, 255, 0.72);
            }
            #now-playing.kodi-playing-active .kodi-youtube-toggle.active .kodi-youtube-switch::after {
                transform: translateX(20px);
            }
            #now-playing.kodi-playing-active .kodi-playing-indicators {
                position: absolute; left: 50%; bottom: 26px; transform: translateX(-50%);
                display: flex; gap: 10px; z-index: 4; opacity: 0;
                transition: opacity 180ms ease;
            }
            #now-playing.kodi-playing-active[data-kodi-page="transfer"] .kodi-playing-indicators {
                opacity: 1;
            }
            #now-playing.kodi-playing-active .kodi-playing-indicator {
                width: 8px; height: 8px; border-radius: 999px; background: rgba(255, 255, 255, 0.22);
            }
            #now-playing.kodi-playing-active .kodi-playing-indicator.active {
                background: rgba(148, 199, 255, 0.95); transform: scale(1.45);
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

    function stepTransferFocus(delta) {
        if (!canShowTransferOverlay()) {
            currentPageIndex = 0;
            if (mountedContainer) renderOverlay(mountedContainer);
            return;
        }

        const targets = transferTargets();
        const count = targets.length + 1;
        if (!count) {
            currentPageIndex = 1;
            if (mountedContainer) renderOverlay(mountedContainer);
            return;
        }

        if (currentPageIndex !== 1) {
            currentPageIndex = 1;
            transferState.focusIndex = delta < 0 ? count - 1 : 0;
        } else {
            const nextFocus = (transferState.focusIndex || 0) + delta;
            if (nextFocus < 0 || nextFocus >= count) {
                currentPageIndex = 0;
                if (mountedContainer) renderOverlay(mountedContainer);
                return;
            }
            transferState.focusIndex = nextFocus;
        }
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

    function activateTransferFocus() {
        const targets = transferTargets();
        clampTransferFocus();
        if (transferState.focusIndex < targets.length) {
            void transferQueueToSelected({ closeOnSuccess: true });
            return true;
        }
        const handled = toggleYoutubeVideos();
        currentPageIndex = 0;
        if (mountedContainer) renderOverlay(mountedContainer);
        return handled;
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
            const option = event.target.closest('[data-kodi-target]');
            if (option) {
                selectTransferTarget(option.dataset.kodiTarget);
                void transferQueueToSelected({ closeOnSuccess: true });
                return;
            }
            if (event.target.closest('[data-youtube-toggle]')) {
                transferState.focusIndex = youtubeFocusIndex();
                toggleYoutubeVideos();
                currentPageIndex = 0;
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
    }

    function renderOverlay(container) {
        if (!container) return;
        const overlay = ensureOverlay(container);
        const pageId = currentPageIndex === 1 && canShowTransferOverlay() ? 'transfer' : 'now';
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
        targetsEl.hidden = pageId !== 'transfer';
        if (pageId !== 'transfer') {
            copyEl.textContent = '';
            metaEl.textContent = lastMedia.state ? `State: ${String(lastMedia.state).toUpperCase()}` : '';
            return;
        }
        const selected = currentTransferTarget();
        copyEl.textContent = transferState.error || transferState.message || '';
        metaEl.textContent = selected
            ? `Selected: ${selected.name} - YouTube: ${youtubeVideosEnabled() ? 'On' : 'Off'}`
            : 'Populate kodi.transfer_targets in config';
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
        const direction = String(data?.direction || 'clock').toLowerCase();
        const delta = direction === 'counter' ? -1 : 1;
        stepTransferFocus(delta);
        if (mountedContainer) renderOverlay(mountedContainer);
        return true;
    }

    function handleButton(button) {
        const normalized = String(button || '').toLowerCase();
        if (normalized === '__close_transfer_overlay__') {
            currentPageIndex = 0;
            if (mountedContainer) renderOverlay(mountedContainer);
            return true;
        }
        if (currentPageIndex !== 1 || !canShowTransferOverlay()) return false;
        if (normalized === 'left') {
            stepTransferFocus(-1);
            return true;
        }
        if (normalized === 'right') {
            stepTransferFocus(1);
            return true;
        }
        if (normalized === 'go') {
            return activateTransferFocus();
        }
        if (normalized === 'up' || normalized === 'down') {
            return toggleYoutubeVideos();
        }
        return false;
    }

    document.addEventListener('bs5c:music-video-preference', () => {
        if (mountedContainer && currentPageIndex === 1) {
            renderOverlay(mountedContainer);
        }
    });

    document.addEventListener('bs5c:playback-targets', () => {
        if (mountedContainer && currentPageIndex === 1) {
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
